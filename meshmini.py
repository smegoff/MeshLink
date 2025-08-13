#!/usr/bin/env python3
# MeshMini - Minimal Meshtastic BBS (generic)
from __future__ import annotations
import os, sys, sqlite3, signal, threading, re, time, json, glob
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, List, Dict

DB_PATH = os.environ.get("MMB_DB", "/opt/meshmini/board.db")
DEVICE_PATH = os.environ.get("MMB_DEVICE", "auto")
ADMINS_ENV = {s.strip() for s in os.environ.get("MMB_ADMINS", "").split(",") if s.strip()}

RATE_LIMIT_SEC = int(os.environ.get("MMB_RATE", "8"))
REPLY_BROADCAST = os.environ.get("MMB_REPLY_BCAST", "0") == "1"
CH_FALLBACK = int(os.environ.get("MMB_CH_FALLBACK", "0"))
DIRECT_FALLBACK = os.environ.get("MMB_DIRECT_FALLBACK", "0") == "1"
FALLBACK_SEC = float(os.environ.get("MMB_FALLBACK_SEC", "5"))

MAX_TEXT = int(os.environ.get("MMB_MAX_TEXT", "110"))
TX_GAP = float(os.environ.get("MMB_TX_GAP", "1.5"))
NAME_DEFAULT = os.environ.get("MMB_NAME", "MeshLink BBS")
VERBOSE = os.environ.get("MMB_VERBOSE", "0") == "1"
STARTUP_DELAY = float(os.environ.get("MMB_STARTUP_DELAY", "2.0"))
STARTED_AT = datetime.utcnow()
NOTICE_IN_MENU_MAX = int(os.environ.get("MMB_NOTICE_IN_MENU_MAX", "180"))

DELIVER_WAIT = float(os.environ.get("MMB_DELIVER_WAIT", "8"))
SF_NOTIFY = os.environ.get("MMB_SF_NOTIFY", "0") == "1"
SF_LIMIT_BATCH = int(os.environ.get("MMB_SF_LIMIT_BATCH", "3"))
SF_TTL_HOURS = int(os.environ.get("MMB_SF_TTL_HOURS", "72"))

try:
    import meshtastic
    import meshtastic.serial_interface
    from meshtastic.protobuf import portnums_pb2
    from pubsub import pub
    try:
        pub.setTopicUnspecifiedFatal(False)
    except Exception:
        pass
except Exception:
    sys.stderr.write("[meshmini] Missing deps. In venv: pip install meshtastic pypubsub\n")
    raise

SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY AUTOINCREMENT, from_id TEXT, text TEXT NOT NULL, ts TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS replies(id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL, from_id TEXT, text TEXT NOT NULL, ts TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sf_queue(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  to_id TEXT NOT NULL,
  from_id TEXT,
  text TEXT NOT NULL,
  ch_idx INTEGER DEFAULT 0,
  created_ts INTEGER NOT NULL,
  delivered_ts INTEGER,
  attempts INTEGER DEFAULT 0,
  last_attempt INTEGER,
  status TEXT DEFAULT 'queued',
  ttl_sec INTEGER DEFAULT 259200
);
CREATE INDEX IF NOT EXISTS x_sf_toid ON sf_queue(to_id, status);
'''

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.commit(); con.close()

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    try:
        yield con; con.commit()
    finally:
        con.close()

def chunk_text(msg: str, limit: int) -> List[str]:
    if len(msg) <= limit: return [msg]
    words = msg.split(" ")
    chunks, cur = [], ""
    for w in words:
        if len(cur) + len(w) + (0 if not cur else 1) <= limit:
            cur = w if not cur else f"{cur} {w}"
        else:
            chunks.append(cur); cur = w
    if cur: chunks.append(cur)
    total = len(chunks)
    if total > 1:
        chunks = [f"({i+1}/{total}) {c}" for i,c in enumerate(chunks)]
    return chunks

def list_serial_candidates() -> List[str]:
    byid = sorted(glob.glob("/dev/serial/by-id/*"))
    acm = sorted(glob.glob("/dev/ttyACM*"))
    usb = sorted(glob.glob("/dev/ttyUSB*"))
    return byid + acm + usb

def serialish_error(e: Exception) -> bool:
    s = str(e).lower()
    return any(x in s for x in ["serial","usb","resource temporarily unavailable","disconnected","timed out","no such file","input/output error","could not exclusively lock"])

def _norm_id(s: Optional[str]) -> Optional[str]:
    if not s: return None
    x = s.strip().lower()
    x = x if x.startswith("!") else ("!" + x)
    return x if re.fullmatch(r"![0-9a-f]{8}", x) else None

def _fmt_ago(seconds: Optional[float]) -> str:
    if seconds is None: return "unknown"
    seconds = int(seconds)
    if seconds < 60: return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60: return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24: return f"{h}h{m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d{h:02d}h"

class MiniBBS:
    def __init__(self, device: Optional[str]):
        self.device = (device or "auto").strip()
        self.iface = None
        self.stop = threading.Event()
        self.last_reply: Dict[str, datetime] = {}
        self._recon_lock = threading.Lock()
        self.connected_at = 0.0
        self.seen_ids: deque[int] = deque(maxlen=256)
        self.fp_set: set[str] = set()
        self.fp_queue: deque[tuple[str,float]] = deque()
        self.my_id: Optional[str] = None

    def _load_json_set(self, key: str) -> Optional[set[str]]:
        try:
            with db() as con:
                row = con.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
                if not row: return None
                arr = json.loads(row["v"])
                return {_norm_id(x) for x in arr if _norm_id(x)}
        except Exception:
            return None

    def _save_json_set(self, key: str, ids: set[str]):
        with db() as con:
            con.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, json.dumps(sorted(ids))))

    def _current_admins(self) -> set[str]:
        ids = self._load_json_set("admins_json")
        if ids is not None: return ids
        env_ids = {_norm_id(x) for x in ADMINS_ENV if _norm_id(x)}
        if env_ids:
            self._save_json_set("admins_json", env_ids)
            return env_ids
        return set()

    def _is_admin(self, node_id: Optional[str]) -> bool:
        cur = self._current_admins()
        if not cur: return True
        return _norm_id(node_id) in cur

    def _admin_list(self) -> List[str]:
        return sorted(self._current_admins())

    def _current_blacklist(self) -> set[str]:
        return self._load_json_set("blacklist_json") or set()

    def _is_blacklisted(self, node_id: Optional[str]) -> bool:
        return _norm_id(node_id) in self._current_blacklist()

    def _name(self) -> str:
        try:
            with db() as con:
                row = con.execute("SELECT v FROM kv WHERE k='name'").fetchone()
                return (row["v"] if row and row["v"] else NAME_DEFAULT)[:40]
        except Exception:
            return NAME_DEFAULT

    def _notice_data(self):
        try:
            with db() as con:
                row = con.execute("SELECT v FROM kv WHERE k='notice'").fetchone()
                txt = (row["v"] if row else "").strip()
                row2 = con.execute("SELECT v FROM kv WHERE k='notice_ts'").fetchone()
                ts = int(row2["v"]) if row2 and str(row2["v"]).isdigit() else None
                return (txt or None), ts
        except Exception:
            return (None, None)

    def _fmt_ts(self, ts: Optional[int]) -> Optional[str]:
        if not ts: return None
        lt = time.localtime(ts); now = time.localtime()
        if (lt.tm_year, lt.tm_mon, lt.tm_mday) == (now.tm_year, now.tm_mon, now.tm_mday):
            return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
        return f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d} {lt.tm_hour:02d}:{lt.tm_min:02d}"

    def _notice_line_for_menu(self) -> Optional[str]:
        text, ts = self._notice_data()
        if not text: return None
        cut = text[:NOTICE_IN_MENU_MAX] + ("…" if len(text) > NOTICE_IN_MENU_MAX else "")
        when = self._fmt_ts(ts)
        head = f"Notice (updated {when}):" if when else "Notice:"
        return f"{head} {cut}"

    def _iter_nodes(self):
        try:
            for nid, node in (getattr(self.iface, "nodesById", {}) or {}).items():
                yield nid, node
        except Exception:
            return

    def _get_user_names(self, node):
        user = getattr(node, "user", None)
        if user is None and isinstance(node, dict): user = node.get("user")
        def get(u, *keys):
            for k in keys:
                if isinstance(u, dict) and u.get(k): return u[k]
                v = getattr(u, k, None)
                if v: return v
            return None
        ln = sn = None
        if user:
            ln = get(user, "long_name", "longName")
            sn = get(user, "short_name", "shortName")
        return (ln or "-"), (sn or "-")

    def _get_last_heard_epoch(self, node):
        for key in ("lastHeard","last_heard","last_seen","lastSeen","heard"):
            v = None
            if isinstance(node, dict): v = node.get(key)
            else: v = getattr(node, key, None)
            if v:
                try: return float(v)
                except Exception: pass
        if isinstance(node, dict):
            for subk in ("device","position","stats"):
                sub = node.get(subk)
                if isinstance(sub, dict):
                    for key in ("lastHeard","last_heard","last_seen","lastSeen"):
                        v = sub.get(key)
                        if v:
                            try: return float(v)
                            except Exception: pass
        return None

    def _resolve_by_short(self, short: str) -> List[tuple[str,str,str]]:
        want = (short or "").strip().lower()
        if not want: return []
        candidates, seen = [], set()

        nbid = getattr(self.iface, "nodesById", {}) or {}
        for nid, node in nbid.items():
            ln, sn = self._get_user_names(node)
            if nid and nid not in seen:
                candidates.append((nid, ln, sn)); seen.add(nid)

        nbnum = getattr(self.iface, "nodesByNum", {}) or {}
        items = []
        try: items = list(nbnum.items())
        except Exception: items = []
        for num, node in items:
            try: nid = f"!{int(num):08x}"
            except Exception: continue
            if nid in seen: continue
            ln, sn = self._get_user_names(node)
            candidates.append((nid, ln, sn)); seen.add(nid)

        exact = [t for t in candidates if t[2] and t[2].lower() == want]
        if exact: return exact
        starts = [t for t in candidates if t[2] and t[2].lower().startswith(want)]
        if len(starts) == 1: return starts
        contains = [t for t in candidates if (t[2] and want in t[2].lower()) or (t[1] and want in t[1].lower())]
        return contains

    def _lookup_by_id(self, nid: str):
        return (getattr(self.iface, "nodesById", {}) or {}).get(nid)

    def _send_direct_and_wait(self, dest_id: str, text: str, ch_idx: int, timeout: float) -> bool:
        acked = threading.Event()
        def _ack(pkt):
            if VERBOSE:
                rr = (pkt or {}).get("decoded", {}).get("routing", {})
                print(f"[send] DM ACK/NAK: {rr}")
            acked.set()
        try:
            self.iface.sendData(
                text.encode("utf-8"),
                destinationId=dest_id,
                portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                wantAck=True,
                wantResponse=False,
                onResponse=_ack,
                onResponseAckPermitted=True,
                channelIndex=ch_idx,
            )
        except Exception as e:
            print(f"[meshmini] dm send error: {e}")
            if serialish_error(e): self._reconnect("dm-send-error")
            return False
        return acked.wait(timeout)

    def _queue_dm(self, to_id: str, from_id: Optional[str], body: str, ch_idx: int) -> int:
        ttl = max(60, SF_TTL_HOURS * 3600)
        now_ts = int(time.time())
        with db() as con:
            con.execute("INSERT INTO sf_queue(to_id,from_id,text,ch_idx,created_ts,ttl_sec) VALUES(?,?,?,?,?,?)",
                        (to_id, from_id, body, int(ch_idx), now_ts, ttl))
            return con.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _deliver_queue_for(self, dest_id: Optional[str]):
        if not dest_id: return
        now = int(time.time())
        with db() as con:
            con.execute("UPDATE sf_queue SET status='expired' WHERE status='queued' AND (? - created_ts) > ttl_sec", (now,))
            rows = con.execute("SELECT id,to_id,from_id,text,ch_idx FROM sf_queue WHERE status='queued' AND to_id=? ORDER BY id ASC LIMIT ?",
                               (dest_id, SF_LIMIT_BATCH)).fetchall()
        for r in rows:
            qid, to_id, from_id, body, ch_idx = r["id"], r["to_id"], r["from_id"] or "?", r["text"], int(r["ch_idx"])
            ln, sn = self._names_for(from_id)
            payload = f"[DM via BBS] from {sn}/{from_id}: {body}"
            ok = self._send_direct_and_wait(to_id, payload, ch_idx, DELIVER_WAIT)
            with db() as con:
                if ok:
                    con.execute("UPDATE sf_queue SET status='delivered', delivered_ts=?, attempts=attempts+1, last_attempt=? WHERE id=?",
                                (int(time.time()), int(time.time()), qid))
                else:
                    con.execute("UPDATE sf_queue SET attempts=attempts+1, last_attempt=? WHERE id=?",
                                (int(time.time()), qid))
            if ok and SF_NOTIFY and from_id and from_id != "?":
                try:
                    self._send(f"Queued DM delivered to {to_id} (Q#{qid}).", from_id, CH_FALLBACK, want_ack=False, on_ack=None)
                except Exception:
                    pass

    def _menu_quick_text(self) -> str:
        n = self._name()
        return (f"[{n}] Menu\n"
                " r | r <id> | p <text> | reply <id> <t>\n"
                " msg <sn|!id> <t> (store-and-forward)\n"
                " whois <q> | lastseen <q>\n"
                " info | status | whoami | ??")

    def _menu_full_text(self) -> str:
        n = self._name()
        return (
            f"[{n}] Help\n"
            " r               Read recent posts\n"
            " r <id>          Read post number <id>\n"
            " p <text>        Post a new message\n"
            " reply <id> <t>  Reply to a post\n"
            " msg <sn|!id> <t>  DM (S&F if offline)\n"
            " outbox          Your queued DMs\n"
            " whois <q>       Look up node (ID, names, last-seen)\n"
            " lastseen <q>    How long since last heard\n"
            " nodes           What this BBS currently knows\n"
            " info            Show the current notice\n"
            " status          Station long/short & uptime\n"
            " whoami          Your node ID + names\n"
            " admins / blacklist / name …\n"
            " ?               Quick menu   |   ?? Detailed help"
        )

    def start(self):
        signal.signal(signal.SIGINT, lambda *a: self.stop.set())
        signal.signal(signal.SIGTERM, lambda *a: self.stop.set())
        init_db()
        pub.subscribe(self._on_receive, "meshtastic.receive")
        pub.subscribe(self._on_receive, "meshtastic.receive.text")
        pub.subscribe(self._on_node_updated, "meshtastic.node.updated")
        self._connect()
        try:
            while not self.stop.is_set():
                self.stop.wait(0.5)
        finally:
            try:
                if self.iface: self.iface.close()
            except Exception:
                pass

    def _connect(self):
        print("[meshmini] Connecting…")
        last_err = None
        while not self.stop.is_set():
            wants = []
            want = (self.device or "auto").strip().lower()
            if want and want != "auto": wants.append(self.device)
            wants.extend(list_serial_candidates())
            for cand in wants:
                try:
                    self.iface = meshtastic.serial_interface.SerialInterface(devPath=cand)
                    self.device = cand
                    self.connected_at = time.time()
                    print(f"[meshmini] Connected on {cand}.")
                    return
                except Exception as e:
                    last_err = e; continue
            time.sleep(2)
        if last_err: raise last_err
        raise RuntimeError("Stopped before connection could be made")

    def _reconnect(self, reason="unknown"):
        if self.stop.is_set(): return
        if not self._recon_lock.acquire(blocking=False): return
        try:
            print(f"[meshmini] reconnect requested ({reason})")
            try:
                if self.iface: self.iface.close()
            except Exception: pass
            time.sleep(1.5)
            self._connect()
        except Exception as e:
            print(f"[meshmini] reconnect failed: {e}; retrying soon")
            t = threading.Timer(5.0, lambda: self._reconnect("retry"))
            t.daemon = True; t.start()
        finally:
            self._recon_lock.release()

    def _on_node_updated(self, node=None, interface=None, **kw):
        try:
            num = None
            if node is not None:
                num = getattr(node, "num", None)
                if num is None and isinstance(node, dict):
                    num = node.get("num") or node.get("numBytes") or node.get("id")
            if num is None:
                mi = getattr(self.iface, "myInfo", None)
                nid = getattr(mi, "my_node_num", None) if mi else None
                if nid is not None: self.my_id = f"!{nid:08x}"
                return
            nid = f"!{int(num):08x}"
            self._deliver_queue_for(nid)
        except Exception:
            pass

    def _owner_names(self):
        ln = sn = None
        try:
            if hasattr(self.iface, "getLongName"): ln = self.iface.getLongName()
            if hasattr(self.iface, "getShortName"): sn = self.iface.getShortName()
        except Exception:
            pass
        try:
            mi = getattr(self.iface, "myInfo", None)
            mynum = getattr(mi, "my_node_num", None) if mi else None
            node = getattr(self.iface, "nodesByNum", {}).get(mynum) if mynum is not None else None
            user = getattr(node, "user", None) if node is not None else None
            if user is None and isinstance(node, dict): user = node.get("user")
            if user:
                ln = ln or (user.get("long_name") if isinstance(user, dict) else getattr(user, "longName", None))
                sn = sn or (user.get("short_name") if isinstance(user, dict) else getattr(user, "shortName", None))
        except Exception:
            pass
        return (ln or "-"), (sn or "-")

    def _names_for(self, node_id: Optional[str]):
        ln = sn = None; nid = (node_id or "").strip()
        try:
            node = getattr(self.iface, "nodesById", {}).get(nid)
            user = getattr(node, "user", None) if node is not None else None
            if user is None and isinstance(node, dict): user = node.get("user")
            if user:
                ln = (user.get("long_name") if isinstance(user, dict) else getattr(user, "longName", None))
                sn = (user.get("short_name") if isinstance(user, dict) else getattr(user, "shortName", None))
        except Exception:
            pass
        if not (ln or sn):
            try:
                if nid.startswith("!"):
                    num = int(nid[1:], 16)
                    node = getattr(self.iface, "nodesByNum", {}).get(num)
                    user = getattr(node, "user", None) if node is not None else None
                    if user is None and isinstance(node, dict): user = node.get("user")
                    if user:
                        ln = (user.get("long_name") if isinstance(user, dict) else getattr(user, "longName", None))
                        sn = (user.get("short_name") if isinstance(user, dict) else getattr(user, "shortName", None))
            except Exception:
                pass
        return (ln or "-"), (sn or "-")

    def _is_text_port(self, pnum) -> bool:
        try:
            return (pnum == portnums_pb2.PortNum.TEXT_MESSAGE_APP or pnum == "TEXT_MESSAGE_APP" or pnum == 1)
        except Exception:
            return pnum in ("TEXT_MESSAGE_APP", 1)

    def _dedup_fp(self, from_id: Optional[str], text: str, ttl: float = 10.0) -> bool:
        now = time.time()
        fp = f"{from_id or '?'}|{text}"
        while self.fp_queue and (now - self.fp_queue[0][1]) > ttl:
            old_fp, _ = self.fp_queue.popleft(); self.fp_set.discard(old_fp)
        if fp in self.fp_set: return True
        self.fp_set.add(fp); self.fp_queue.append((fp, now)); return False

    def _on_receive(self, packet=None, interface=None, **kw):
        try:
            if (time.time() - self.connected_at) < STARTUP_DELAY:
                return
            decoded = (packet or {}).get("decoded", {}) or {}
            pnum = decoded.get("portnum")
            if not self._is_text_port(pnum): return
            text = (decoded.get("text") or "").strip()
            if not text:
                payload = decoded.get("payload")
                if isinstance(payload, (bytes, bytearray)):
                    try: text = payload.decode("utf-8", "ignore").strip()
                    except Exception: text = ""
            if not text: return

            from_id = (packet or {}).get("fromId") or (f"!{(packet or {}).get('from'):08x}" if (packet or {}).get('from') is not None else None)
            if self._is_blacklisted(from_id): return

            pkt_id = (packet or {}).get("id")
            if pkt_id is not None:
                if pkt_id in self.seen_ids: return
                self.seen_ids.append(pkt_id)

            ch_idx = (packet or {}).get("channel") or decoded.get("channel") or CH_FALLBACK
            try: ch_idx = int(ch_idx)
            except Exception: ch_idx = CH_FALLBACK

            if self._dedup_fp(from_id, text): return

            now = datetime.utcnow()
            last = self.last_reply.get(from_id or "")
            if last and (now - last).total_seconds() < RATE_LIMIT_SEC:
                self._deliver_queue_for(from_id)
                return

            resp = self._handle(text, from_id, ch_idx)
            self._deliver_queue_for(from_id)

            if isinstance(resp, list):
                for r in resp:
                    if r: self._reply_seq(from_id, r, ch_idx); time.sleep(TX_GAP)
            elif resp:
                self._reply_seq(from_id, resp, ch_idx)
            self.last_reply[from_id or ""] = now
        except Exception as e:
            print(f"[meshmini] receive error: {e}")
            if serialish_error(e): self._reconnect("recv-error")

    def _reply_seq(self, to_id: Optional[str], text: str, ch_idx: int):
        parts = chunk_text(text, MAX_TEXT)
        if REPLY_BROADCAST:
            for part in parts:
                self._send(part, meshtastic.BROADCAST_ADDR, ch_idx, want_ack=False); time.sleep(TX_GAP)
            return
        for part in parts:
            acked = threading.Event()
            def _ack(pkt):
                if VERBOSE:
                    rr = (pkt or {}).get("decoded", {}).get("routing", {})
                    print(f"[send] ACK/NAK: {rr}")
                acked.set()
            self._send(part, to_id, ch_idx, want_ack=True, on_ack=_ack)
            got = acked.wait(FALLBACK_SEC)
            if not got and DIRECT_FALLBACK:
                print(f"[meshmini] no ACK in {FALLBACK_SEC}s; broadcasting fallback")
                self._send(part, meshtastic.BROADCAST_ADDR, ch_idx, want_ack=False)
            time.sleep(TX_GAP)

    def _send(self, text: str, dest_id: Optional[str], ch_idx: int, want_ack: bool, on_ack=None):
        if not dest_id: return
        try:
            self.iface.sendData(
                text.encode("utf-8"),
                destinationId=dest_id,
                portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                wantAck=want_ack,
                wantResponse=False,
                onResponse=on_ack,
                onResponseAckPermitted=True,
                channelIndex=ch_idx,
            )
        except Exception as e:
            print(f"[meshmini] send error: {e}")
            if serialish_error(e): self._reconnect("send-error")
            if "payload too big" in str(e).lower() and len(text) > 20:
                for s in chunk_text(text, max(20, MAX_TEXT - 20)):
                    try:
                        self.iface.sendData(
                            s.encode("utf-8"),
                            destinationId=dest_id,
                            portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                            wantAck=want_ack,
                            wantResponse=False,
                            onResponse=on_ack,
                            onResponseAckPermitted=True,
                            channelIndex=ch_idx,
                        )
                    except Exception as e2:
                        print(f"[meshmini] send error (retry): {e2}")
                        if serialish_error(e2): self._reconnect("send-error-retry")

    def _handle(self, text: str, from_id: Optional[str], ch_idx: int):
        t = text.strip(); low = t.lower()

        if t == "??":
            notice_line = self._notice_line_for_menu()
            return [notice_line, self._menu_full_text()] if notice_line else self._menu_full_text()

        if low in ("?","h","help","menu"):
            notice_line = self._notice_line_for_menu()
            return [notice_line, self._menu_quick_text()] if notice_line else self._menu_quick_text()

        if low in ("whoami","id","myid"):
            nid = from_id or "-"
            ln, sn = self._names_for(nid)
            return f"{nid} ({ln} / {sn})"

        if low == "admins":
            ids = self._admin_list()
            if ids:
                lines = []
                for a in ids:
                    ln, sn = self._names_for(a)
                    lines.append(f"{a} ({ln} / {sn})")
                return "Admins:\n" + "\n".join(lines)
            return "Admins: (none configured) — everyone permitted."

        if low in ("isadmin","is admin","am i admin","am i admin?","admin?"):
            cur = self._current_admins()
            if not cur: return "No admin list configured — everyone permitted."
            return "Admin? " + ("yes" if self._is_admin(from_id) else "no")

        m = re.match(r"^(?:admin|admins)\s+add\s+(!?[0-9a-fA-F]{8})$", t)
        if m:
            target = _norm_id(m.group(1))
            cur = self._current_admins()
            if cur and not self._is_admin(from_id): return "Not authorized to add admins."
            if target in cur: return f"{target} is already an admin."
            cur.add(target); self._save_json_set("admins_json", cur)
            listing = "\n".join(f"{a} ({self._names_for(a)[0]} / {self._names_for(a)[1]})" for a in sorted(cur))
            return "Added.\nAdmins:\n" + listing if listing else "Added."

        m = re.match(r"^(?:admin|admins)\s+remove\s+(!?[0-9a-fA-F]{8})$", t)
        if m:
            target = _norm_id(m.group(1))
            cur = self._current_admins()
            if cur and not self._is_admin(from_id): return "Not authorized to remove admins."
            if target not in cur: return f"{target} is not an admin."
            cur.remove(target); self._save_json_set("admins_json", cur)
            if cur:
                listing = "\n".join(f"{a} ({self._names_for(a)[0]} / {self._names_for(a)[1]})" for a in sorted(cur))
                return "Removed.\nAdmins:\n" + listing
            else:
                return "Removed. Admin list now empty — everyone permitted."

        if low in ("admins clear","admin clear"):
            cur = self._current_admins()
            if cur and not self._is_admin(from_id): return "Not authorized to clear admins."
            self._save_json_set("admins_json", set())
            return "Admins cleared — everyone permitted."

        if low == "blacklist":
            if not self._is_admin(from_id): return "Not authorized."
            bl = sorted(self._current_blacklist())
            if not bl: return "Blacklist: (empty)."
            lines = []
            for a in bl:
                ln, sn = self._names_for(a)
                lines.append(f"{a} ({ln} / {sn})")
            return "Blacklist:\n" + "\n".join(lines)

        m = re.match(r"^blacklist\s+add\s+(!?[0-9a-fA-F]{8})$", t)
        if m:
            if not self._is_admin(from_id): return "Not authorized."
            target = _norm_id(m.group(1))
            bl = self._current_blacklist()
            if target in bl: return f"{target} already blacklisted."
            bl.add(target); self._save_json_set("blacklist_json", bl)
            return f"Blacklisted {target}."

        m = re.match(r"^blacklist\s+remove\s+(!?[0-9a-fA-F]{8})$", t)
        if m:
            if not self._is_admin(from_id): return "Not authorized."
            target = _norm_id(m.group(1))
            bl = self._current_blacklist()
            if target not in bl: return f"{target} not in blacklist."
            bl.remove(target); self._save_json_set("blacklist_json", bl)
            return f"Removed {target}."

        if low == "blacklist clear":
            if not self._is_admin(from_id): return "Not authorized."
            self._save_json_set("blacklist_json", set()); return "Blacklist cleared."

        if low in ("status","s"):
            ln, sn = self._owner_names()
            up = datetime.utcnow() - STARTED_AT
            up_h = int(up.total_seconds() // 3600); up_m = int((up.total_seconds() % 3600)//60)
            return f"{ln} / {sn} / up {up_h}h{up_m:02d}m"

        if low == "name":
            return self._name()
        if low.startswith("name set "):
            if not self._is_admin(from_id): return "Not authorized to set name."
            newname = t[len("name set "):].strip()[:40]
            if not newname: return "Usage: name set <text>"
            with db() as con:
                con.execute("INSERT INTO kv(k,v) VALUES('name',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (newname,))
            return f"Name set to: {newname}"

        if low.startswith("p ") or low.startswith("post "):
            body = t.split(" ",1)[1].strip()
            if not body: return "Usage: p <message>"
            with db() as con:
                con.execute("INSERT INTO posts(from_id,text) VALUES(?,?)", (from_id, body))
                pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            return f"Posted as #{pid}."

        if low in ("r","read"):
            with db() as con:
                rows = con.execute("SELECT id,text FROM posts ORDER BY id DESC LIMIT 10").fetchall()
            if not rows: return "No posts yet. Use 'p <text>' to post."
            return "Recent:\n" + "\n".join(f"#{r['id']}: {r['text'][:60]}" for r in rows) + "\nRead one: r <id>"

        m = re.match(r"^(?:r|read)\s+(\d+)$", low)
        if m:
            pid = int(m.group(1))
            with db() as con:
                p = con.execute("SELECT id,text,COALESCE(from_id,'?') as from_id FROM posts WHERE id=?", (pid,)).fetchone()
                rs = con.execute("SELECT text,COALESCE(from_id,'?') as from_id FROM replies WHERE post_id=? ORDER BY id ASC LIMIT 5", (pid,)).fetchall()
            if not p: return f"No post #{pid}."
            out = [f"#{p['id']} by {p['from_id']}\n{p['text']}"]
            if rs:
                out.append("Replies:"); out += [f"- {r['from_id']}: {r['text'][:80]}" for r in rs]
            out.append(f"Reply: reply {pid} <text>")
            return "\n".join(out)

        if low.startswith("reply "):
            m2 = re.match(r"^reply\s+(\d+)\s+(.+)$", t, re.IGNORECASE)
            if not m2: return "Usage: reply <id> <text>"
            pid = int(m2.group(1)); body = m2.group(2).strip()
            with db() as con:
                ok = con.execute("SELECT 1 FROM posts WHERE id=?", (pid,)).fetchone()
                if not ok: return f"No post #{pid}."
                con.execute("INSERT INTO replies(post_id,from_id,text) VALUES(?,?,?)", (pid, from_id, body))
            return f"Replied on #{pid}."

        if low == "info":
            with db() as con:
                row = con.execute("SELECT v FROM kv WHERE k='notice'").fetchone()
                txt = (row['v'] if row else '').strip()
                row2 = con.execute("SELECT v FROM kv WHERE k='notice_ts'").fetchone()
                ts = int(row2['v']) if row2 and str(row2['v']).strip().isdigit() else None
            if not txt: return "No notice set."
            when = self._fmt_ts(ts); head = f"Notice (updated {when}):" if when else "Notice:"
            return f"{head}\n{txt[:450]}"

        if low.startswith("info set "):
            if not self._is_admin(from_id): return "Not authorized to set notice."
            body = t[len("info set "):].strip()
            now_ts = int(time.time())
            with db() as con:
                con.execute("INSERT INTO kv(k,v) VALUES('notice',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (body,))
                con.execute("INSERT INTO kv(k,v) VALUES('notice_ts',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(now_ts),))
            return "Notice updated."

        m = re.match(r"^(msg|dm|to)\s+(\S+)\s+(.+)$", t, re.IGNORECASE)
        if m:
            target, body = m.group(2).strip(), m.group(3).strip()
            if not body: return "Usage: msg <short|!id> <text>"
            if target.startswith("!"):
                dest_id = _norm_id(target)
                if not dest_id: return f"Bad node ID '{target}'."
                if self._is_blacklisted(dest_id): return f"{dest_id} is blacklisted."
                ok = self._send_direct_and_wait(dest_id, f"[DM via BBS] from {self._names_for(from_id)[1]}/{from_id}: {body}", ch_idx, DELIVER_WAIT)
                if ok: return f"Delivered to {dest_id}."
                qid = self._queue_dm(dest_id, from_id, body, ch_idx); return f"Queued for {dest_id} (Q#{qid})."
            matches = self._resolve_by_short(target)
            if not matches: return f"No node with short '{target}'. Try 'nodes' or use !nodeId."
            if len(matches) > 1:
                options = ", ".join([f"{sn}({nid})" for nid, ln, sn in matches if sn])
                return f"Ambiguous '{target}': {options}"
            dest_id, ln, sn = matches[0]
            if self._is_blacklisted(dest_id): return f"{sn} is blacklisted."
            ok = self._send_direct_and_wait(dest_id, f"[DM via BBS] from {self._names_for(from_id)[1]}/{from_id}: {body}", ch_idx, DELIVER_WAIT)
            if ok: return f"Delivered to {sn} ({dest_id})."
            qid = self._queue_dm(dest_id, from_id, body, ch_idx); return f"Queued for {sn} (Q#{qid})."

        m = re.match(r"^whois\s+(\S+)$", t, re.IGNORECASE)
        if m:
            q = m.group(1).strip(); nid = None; ln = "-"; sn = "-"
            if q.startswith("!"):
                nid = _norm_id(q); node = self._lookup_by_id(nid) if nid else None
                if node: ln, sn = self._get_user_names(node)
            else:
                matches = self._resolve_by_short(q)
                if not matches: return f"No node found for '{q}'."
                if len(matches) > 1: return "Ambiguous: " + ", ".join([f"{sn}({nid})" for nid, ln, sn in matches])
                nid, ln, sn = matches[0]
            node = self._lookup_by_id(nid) if nid else None
            last = None
            if node:
                epoch = self._get_last_heard_epoch(node)
                if epoch: last = time.time() - epoch
            return f"{sn} ({nid}) — {ln}\nlast seen: {_fmt_ago(last)}"

        m = re.match(r"^lastseen\s+(\S+)$", t, re.IGNORECASE)
        if m:
            q = m.group(1).strip(); nid = None; label = q
            if q.startswith("!"):
                nid = _norm_id(q); label = q
            else:
                matches = self._resolve_by_short(q)
                if not matches: return f"No node found for '{q}'."
                if len(matches) > 1: return "Ambiguous: " + ", ".join([f"{sn}({nid})" for nid, ln, sn in matches])
                nid, ln, sn = matches[0]; label = sn
            node = self._lookup_by_id(nid) if nid else None
            epoch = self._get_last_heard_epoch(node) if node else None
            if not epoch: return f"{label}: last-seen unknown"
            return f"{label}: {_fmt_ago(time.time() - epoch)}"

        if low == "nodes":
            rows = []; now = time.time()
            for nid, node in self._iter_nodes():
                ln, sn = self._get_user_names(node)
                epoch = self._get_last_heard_epoch(node)
                ago = _fmt_ago((now - epoch) if epoch else None)
                rows.append(f"{sn or '????'} {nid}  last:{ago}")
            if not rows: return "No nodes known yet. Have them talk once or use !nodeId."
            return "Known nodes:\n" + "\n".join(sorted(rows))

        if low == "outbox":
            with db() as con:
                rows = con.execute("SELECT id,to_id,substr(text,1,40) AS t FROM sf_queue WHERE status='queued' AND from_id=? ORDER BY id ASC LIMIT 10", (from_id,)).fetchall()
            if not rows: return "Outbox: empty."
            lines = [f"Q#{r['id']} -> {r['to_id']}: {r['t']}" for r in rows]
            return "Outbox:\n" + "\n".join(lines)

        if low == "sf list":
            if not self._is_admin(from_id): return "Not authorized."
            with db() as con:
                rows = con.execute("SELECT id,to_id,from_id,substr(text,1,40) AS t,created_ts FROM sf_queue WHERE status='queued' ORDER BY id ASC LIMIT 10").fetchall()
            if not rows: return "SF queue: empty."
            def lab(ts): return time.strftime("%m-%d %H:%M", time.localtime(ts))
            lines = [f"Q#{r['id']} -> {r['to_id']} from {r['from_id']} [{lab(r['created_ts'])}]: {r['t']}" for r in rows]
            return "SF queue:\n" + "\n".join(lines)

        m = re.match(r"^sf\s+flush\s+(\S+)$", t, re.IGNORECASE)
        if m:
            if not self._is_admin(from_id): return "Not authorized."
            q = m.group(1).strip()
            if q.startswith("!"):
                dest_id = _norm_id(q)
            else:
                matches = self._resolve_by_short(q)
                if not matches: return f"No node '{q}'."
                if len(matches) > 1: return "Ambiguous: " + ", ".join([f"{sn}({nid})" for nid, ln, sn in matches])
                dest_id, _, _ = matches[0]
            self._deliver_queue_for(dest_id); return f"Flush attempted for {q}."

        m = re.match(r"^sf\s+purge\s+(\d+)$", t, re.IGNORECASE)
        if m:
            if not self._is_admin(from_id): return "Not authorized."
            qid = int(m.group(1))
            with db() as con:
                con.execute("DELETE FROM sf_queue WHERE id=?", (qid,))
            return f"Purged Q#{qid}."

        return "Unknown. Send '?' for menu; '??' for help."

def main():
    init_db()
    MiniBBS(DEVICE_PATH).start()

if __name__ == "__main__":
    main()
