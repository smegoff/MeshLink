sudo tee /opt/meshmini/meshmini.py >/dev/null <<'PY'
#!/usr/bin/env python3
# MeshMini — minimal Meshtastic BBS with peer sync (v0.8.9)
# - Compact one-line '?' menu (auto-shrinks to fit MAX_TEXT)
# - Notice (if set) sent first, as its own page, with Pacific/Auckland timestamp
# - Robust text decoding (bytes/str)
# - PubSub backup for receive path (if pypubsub available)
# - RX watchdog: auto-reconnect if no packets for a while
# - Store-&-forward DMs (by shortName)
# - Simple peer sync (inventory + chunked post replication)
# - SQLite persistence; admin + blacklist; health
# - Nodes command tolerant of string/int keys and empty primary map
# - 'info' command works case/whitespace-insensitively and is rate-limit bypassed

import os, sys, time, sqlite3, threading, random, string
from datetime import datetime
from typing import List, Optional
from collections import deque

try:
    from zoneinfo import ZoneInfo  # stdlib (Py3.9+)
except Exception:
    ZoneInfo = None

# --- Meshtastic imports ---
try:
    import meshtastic
    import meshtastic.serial_interface
    from meshtastic.mesh_interface import MeshInterface
except Exception:
    sys.stderr.write("[meshmini] Missing deps. In venv run: pip install meshtastic pypubsub\n")
    sys.exit(1)

# Optional: pubsub backup
try:
    from pubsub import pub  # type: ignore
except Exception:
    pub = None  # still works without it

# --- Environment / knobs ---
DB_PATH       = os.environ.get("MMB_DB", "/opt/meshmini/board.db")
DEVICE_PATH   = os.environ.get("MMB_DEVICE", "auto")
BBS_NAME      = os.environ.get("MMB_NAME", "MeshLink BBS")
ADMINS_CSV    = os.environ.get("MMB_ADMINS", "")
RATE_SEC      = float(os.environ.get("MMB_RATE", "2"))
CH_FALLBACK   = int(os.environ.get("MMB_CH_FALLBACK", "0"))
REPLY_BCAST   = int(os.environ.get("MMB_REPLY_BCAST", "0"))
DIRECT_FB     = int(os.environ.get("MMB_DIRECT_FALLBACK", "0"))
FB_WAIT       = float(os.environ.get("MMB_FALLBACK_SEC", "5"))
MAX_TEXT      = int(os.environ.get("MMB_MAX_TEXT", "140"))
TX_GAP        = float(os.environ.get("MMB_TX_GAP", "1.0"))
HEALTH_PUBLIC = int(os.environ.get("MMB_HEALTH_PUBLIC", "0"))
DEBUG_LOG     = int(os.environ.get("MMB_DEBUG", "0"))

# Peer sync knobs
SYNC_ON      = int(os.environ.get("MMB_SYNC", "1"))
PEERS_ENV    = [p.strip() for p in os.environ.get("MMB_PEERS", "").split(",") if p.strip()]
SYNC_INV_N   = max(5, int(os.environ.get("MMB_SYNC_INV", "15")))
SYNC_PERIOD  = int(os.environ.get("MMB_SYNC_PERIOD", "300"))
CHUNK_BYTES  = int(os.environ.get("MMB_SYNC_CHUNK", "160"))
SYNC_TAG     = "#SYNC"

# Watchdog knobs
RX_STALE_SEC = int(os.environ.get("MMB_RX_STALE_SEC", "240"))
WATCH_TICK   = int(os.environ.get("MMB_WATCH_TICK", "10"))

def now() -> int: return int(time.time())
def gen_uid(n=10): return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))
def fmt_uptime(seconds:int) -> str:
    h = seconds // 3600; m = (seconds % 3600) // 60
    return f"{h}h{m:02d}m"

def nz_time_str(ts: Optional[int] = None) -> str:
    """Format timestamp in Pacific/Auckland; fall back to UTC if zoneinfo unavailable."""
    ts = now() if ts is None else int(ts)
    try:
        if ZoneInfo:
            dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Pacific/Auckland"))
            return dt.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        pass
    # Fallback: UTC
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")

def dlog(msg: str):
    if DEBUG_LOG:
        print(msg, flush=True)

class MiniBBS:
    def __init__(self, device="auto"):
        self.device = device
        self.iface: Optional[MeshInterface] = None
        self.node_id = None
        self.dev_path = None
        self.connected_at = 0
        self.last_rx_at = 0.0
        self.last_seen = {}  # per-sender rate limiter
        self.stop_evt = threading.Event()
        self.sync_enabled = bool(SYNC_ON)
        self.sync_thread = None
        self.watch_thread = None
        self.last_inv_at = 0
        self.seen_pkt_ids = deque(maxlen=256)

        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()

        # bootstrap admin + peers from env
        for a in [a.strip() for a in ADMINS_CSV.split(",") if a.strip()]:
            self._admin_add(a)
        for p in PEERS_ENV:
            self._peer_add(p)

    # ---------- DB ----------
    def _init_db(self):
        cur = self.db.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, author TEXT, body TEXT, reply_to INTEGER)")
        cur.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        cur.execute("CREATE TABLE IF NOT EXISTS admins (id TEXT PRIMARY KEY)")
        cur.execute("CREATE TABLE IF NOT EXISTS blacklist (id TEXT PRIMARY KEY)")
        cur.execute("CREATE TABLE IF NOT EXISTS peers (id TEXT PRIMARY KEY, last_seen INTEGER)")
        cur.execute("CREATE TABLE IF NOT EXISTS seen_uids (uid TEXT PRIMARY KEY, ts INTEGER)")
        cur.execute("CREATE TABLE IF NOT EXISTS applied_uids (uid TEXT PRIMARY KEY, ts INTEGER)")
        cur.execute("CREATE TABLE IF NOT EXISTS rxparts (uid TEXT PRIMARY KEY, total INTEGER, got INTEGER, data TEXT, from_id TEXT, created_ts INTEGER)")
        cur.execute("CREATE TABLE IF NOT EXISTS dm_out (id INTEGER PRIMARY KEY, to_id TEXT, body TEXT, created_ts INTEGER, delivered_ts INTEGER)")
        # Notice timestamp (if missing) will be added on first set
        self.db.commit()

    # ---------- Serial connect ----------
    def _candidate_ports(self) -> List[str]:
        if self.device != "auto": return [self.device]
        cands = []
        for d in ("/dev/ttyACM0","/dev/ttyACM1","/dev/ttyUSB0","/dev/ttyUSB1"):
            if os.path.exists(d): cands.append(d)
        byid = "/dev/serial/by-id"
        if os.path.isdir(byid):
            for n in sorted(os.listdir(byid)):
                p = os.path.join(byid, n)
                if os.path.islink(p): cands.append(p)
        return cands

    def _connect(self):
        print("[meshmini] Connecting…")
        last_err = None
        for cand in self._candidate_ports():
            try:
                self.iface = meshtastic.serial_interface.SerialInterface(devPath=cand)  # opens & connects
                self.node_id = self.iface.myInfo.my_node_num if hasattr(self.iface, "myInfo") else None
                self.dev_path = cand
                self.connected_at = now()
                self.last_rx_at = time.time()
                self.iface.onReceive = self._on_receive  # primary callback
                print(f"[meshmini] Connected on {cand}.")
                self._subscribe_pub()  # backup path
                return
            except Exception as e:
                last_err = e
                time.sleep(1.0)
        raise last_err or RuntimeError("No serial candidates found")

    def _reconnect(self):
        try:
            print("[meshmini] Reconnecting serial…")
            if self.iface:
                try: self.iface.close()
                except: pass
            self.iface = None
            time.sleep(1.0)
            self._connect()
        except Exception as e:
            print(f"[meshmini] reconnect failed: {e}")

    # ---------- PubSub backup ----------
    def _subscribe_pub(self):
        if not pub:
            dlog("[meshmini] PubSub not available; relying on onReceive only")
            return
        for topic in ("meshtastic.receive", "meshtastic.receive.text"):
            try: pub.unsubscribe(self._on_pub_receive, topic)
            except Exception: pass
        try:
            pub.subscribe(self._on_pub_receive, "meshtastic.receive")
            pub.subscribe(self._on_pub_receive, "meshtastic.receive.text")
            dlog("[meshmini] PubSub backup subscribed")
        except Exception as e:
            dlog(f"[meshmini] PubSub subscribe failed: {e}")

    def _on_pub_receive(self, packet=None, interface=None, **kwargs):
        if not packet: return
        pid = packet.get("id") or (packet.get("from"), packet.get("rxTime"))
        if pid in self.seen_pkt_ids:  # dedupe vs onReceive
            return
        self.seen_pkt_ids.append(pid)
        self._on_receive(packet, interface)

    # ---------- Helpers ----------
    def _canon_nodeid(self, maybe_fromId, packet) -> Optional[str]:
        if isinstance(maybe_fromId, str) and maybe_fromId.startswith("!"):
            return maybe_fromId
        src = packet.get("from")
        if isinstance(src, int):
            return f"!{src & 0xffffffff:08x}"
        alt = packet.get("fromId")
        if isinstance(alt, str):
            return alt
        return None

    def _nodeid_to_num(self, nid: str) -> Optional[int]:
        try:
            if nid and nid.startswith("!"):
                return int(nid[1:], 16)
        except Exception:
            pass
        return None

    def _lookup_names(self, nodeid: str) -> tuple[str,str]:
        try:
            num = self._nodeid_to_num(nodeid)
            if num is None: return ("","")
            n = self.iface.nodes.get(num) if getattr(self.iface, "nodes", None) else None
            if not n: return ("","")
            u = n.get("user",{}) if isinstance(n, dict) else {}
            return (u.get("shortName",""), u.get("longName",""))
        except Exception:
            return ("","")

    def _key_to_nodeid(self, key, entry) -> str:
        if isinstance(key, int):
            return f"!{key & 0xffffffff:08x}"
        if isinstance(key, str):
            s = key.strip()
            if s.startswith("!"):
                return s.lower()
            try:
                return f"!{int(s, 16) & 0xffffffff:08x}"
            except Exception:
                pass
        if isinstance(entry, dict):
            for fld in ("num","nodeNum","node_id","nodeId","id"):
                v = entry.get(fld)
                if isinstance(v, int):
                    return f"!{v & 0xffffffff:08x}"
                if isinstance(v, str):
                    sv = v.strip()
                    if sv.startswith("!"):
                        return sv.lower()
                    try:
                        return f"!{int(sv, 16) & 0xffffffff:08x}"
                    except Exception:
                        pass
        return "!?unknown"

    def _iter_nodes(self):
        nd = getattr(self.iface, "nodes", None)
        if isinstance(nd, dict) and nd:
            for k, v in nd.items():
                yield (k, v)
            return
        for attr in ("nodesByNum", "_nodesByNum", "_nodes"):
            alt = getattr(self.iface, attr, None)
            if isinstance(alt, dict) and alt:
                for k, v in alt.items():
                    yield (k, v)
                return
        try:
            info = self.iface.getMyNodeInfo()
            mynum = (info.get("my_node_num") or info.get("num") or info.get("nodeNum"))
            entry = {"user": {"shortName": info.get("shortName","") or (info.get("owner") or ""),
                              "longName":  info.get("longName","")  or (info.get("owner") or "")},
                     "num": mynum}
            if mynum is not None:
                yield (mynum, entry)
        except Exception:
            return

    # ---------- Messaging ----------
    def _send_text(self, dest: Optional[str], text: str):
        if not self.iface: return
        text = text.strip()
        try:
            if dest and isinstance(dest, str) and dest.startswith("!"):
                dlog(f"[send] -> {dest} ch=0: {text}")
                self.iface.sendText(text, destinationId=dest)
            else:
                dlog(f"[send] -> ^all ch=0: {text}")
                self.iface.sendText(text)
            time.sleep(TX_GAP)
        except Exception as e:
            print(f"[meshmini] send error: {e}")

    def _send_paged(self, dest: Optional[str], lines: List[str], title=None):
        head = f"{title}\n" if title else ""
        pages, cur = [], head
        for ln in lines:
            if len(cur) + len(ln) + 1 > MAX_TEXT:
                pages.append(cur.rstrip())
                cur = head + ln + "\n"
            else:
                cur += ln + "\n"
        if cur.strip(): pages.append(cur.rstrip())
        total = len(pages)
        for i, p in enumerate(pages, 1):
            prefix = f"({i}/{total}) " if total > 1 else ""
            self._send_text(dest, prefix + p)

    # ---------- UI ----------
    def _menu_text(self) -> str:
        name = BBS_NAME.strip()
        if len(name) > 28:
            words = name.split()
            if len(words) > 1:
                name = f"{words[0]} {' '.join(w[0] for w in words[1:])}"
            name = name[:28].rstrip()
        header = f"[{name}]"
        parts = ["r list","r <id> read","p <text> post","reply <id> <t>","info","status","whoami","nodes","whois <short>","dm <short> <t>","?? help"]
        def join_line(items): return f"{header} " + " | ".join(items)
        line = join_line(parts)
        if len(line) <= MAX_TEXT: return line
        removable_order = ["dm <short> <t>","whois <short>","nodes","whoami","status","info","reply <id> <t>","p <text> post","r <id> read"]
        keep = parts[:]
        for item in removable_order:
            if item in keep:
                keep.remove(item)
                line = join_line(keep)
                if len(line) <= MAX_TEXT: return line
        tiny = f"{header} r list | p | r <id> | ??"
        if len(tiny) <= MAX_TEXT: return tiny
        base = header if len(header) < MAX_TEXT - 12 else "[BBS]"
        return f"{base} r|p|r#|??"

    def _cmd_menu(self, frm):
        """Show notice (if any) first, then the one-line compact menu."""
        row = self.db.execute("SELECT v FROM kv WHERE k='notice'").fetchone()
        tsr = self.db.execute("SELECT v FROM kv WHERE k='notice_ts'").fetchone()
        if row and (row["v"] or "").strip():
            lines = [(row["v"] or "").strip()]
            title = f"[Notice {nz_time_str(int(tsr['v']))}]" if tsr and tsr["v"] else "[Notice]"
            self._send_paged(frm, lines, title=title)
        self._send_text(frm, self._menu_text())

    def _help_lines(self):
        return [
            f"[{BBS_NAME}] Help:",
            "• r — list recent; r 12 — read #12",
            "• p hello — post new; reply 12 Thanks — reply",
            "• info / info set <text> (admin)",
            "• status — long/short/uptime; whoami",
            "• nodes / whois <short>",
            "• dm <short> <text> — queued DM",
            "• Admin: admins add|del <!id>, bl add|del <!id>",
            "• Peers: peer add|del <!id>, peer list, sync now|on|off",
            "• health [full] — DB/link snapshot",
        ]

    def _cmd_help(self, frm): self._send_paged(frm, self._help_lines())

    def _cmd_status(self, frm):
        ln = ""; sn = ""
        try:
            info = self.iface.getMyNodeInfo()
            ln = info.get("longName",""); sn = info.get("shortName","")
        except Exception: pass
        up = fmt_uptime(now() - self.connected_at)
        self._send_text(frm, f"{ln} / {sn} / up {up}")

    def _cmd_whoami(self, frm, fromId):
        sn, ln = self._lookup_names(fromId)
        self._send_text(frm, f"{fromId} / {sn} / {ln}")

    def _cmd_whois(self, frm, short):
        for key, entry in self._iter_nodes():
            u = entry.get("user","") if isinstance(entry, dict) else {}
            if isinstance(u, dict) and u.get("shortName","").lower() == short.lower():
                nid = self._key_to_nodeid(key, entry)
                ln  = u.get("longName",""); sn = u.get("shortName","")
                self._send_text(frm, f"{nid} / {sn} / {ln}")
                return
        self._send_text(frm, f"no node with short '{short}'")

    def _cmd_nodes(self, frm):
        entries = []
        try:
            for key, entry in self._iter_nodes():
                u = entry.get("user", {}) if isinstance(entry, dict) else {}
                sn = (u.get("shortName") or "?").strip() or "?"
                ln = (u.get("longName") or "").strip()
                nid = self._key_to_nodeid(key, entry)
                entries.append((sn.lower(), f"{sn:>6}  {nid}  {ln}"))
        except Exception:
            entries = []
        entries.sort(key=lambda x: x[0])
        count = len(entries)
        lines = [s for _, s in entries] if count else ["(no nodes)"]
        self._send_paged(frm, lines, title=f"[{BBS_NAME}] Nodes: {count}")

    # ---------- Posts ----------
    def _post_new(self, author, text, reply_to=None, do_sync=True):
        cur = self.db.cursor()
        cur.execute("INSERT INTO posts(ts,author,body,reply_to) VALUES(?,?,?,?)", (now(), author, text, reply_to))
        self.db.commit()
        pid = cur.lastrowid
        if do_sync and self.sync_enabled:
            self._replicate_new_post(pid, text, author, reply_to)
        return pid

    def _cmd_post(self, frm, author, text):
        pid = self._post_new(author, text, None, do_sync=True)
        self._send_text(frm, f"posted #{pid}")

    def _cmd_reply(self, frm, author, pid_str, text):
        try: pid = int(pid_str)
        except: self._send_text(frm, "bad id"); return
        row = self.db.execute("SELECT id FROM posts WHERE id=?", (pid,)).fetchone()
        if not row: self._send_text(frm, f"no such post {pid}"); return
        rid = self._post_new(author, text, pid, do_sync=True)
        self._send_text(frm, f"reply #{rid} -> #{pid}")

    def _cmd_read(self, frm, arg=None):
        cur = self.db.cursor()
        if arg:
            try:
                pid = int(arg)
                row = cur.execute("SELECT id,ts,author,body,reply_to FROM posts WHERE id=?", (pid,)).fetchone()
                if not row: self._send_text(frm, f"no such post {pid}"); return
                ts = datetime.utcfromtimestamp(row["ts"]).strftime("%Y-%m-%d %H:%M")
                lines = [f"#{row['id']} {ts} {row['author']}", row["body"]]
                for rr in cur.execute("SELECT id,ts,author,body FROM posts WHERE reply_to=? ORDER BY id", (pid,)):
                    ts2 = datetime.utcfromtimestamp(rr["ts"]).strftime("%Y-%m-%d %H:%M")
                    lines.append(f" ↳ #{rr['id']} {ts2} {rr['author']}: {rr['body']}")
                self._send_paged(frm, lines, title=None)
            except:
                self._send_text(frm, "bad id")
        else:
            lines = []
            for r in cur.execute("SELECT id,ts,author,body FROM posts ORDER BY id DESC LIMIT 10"):
                ts = datetime.utcfromtimestamp(r["ts"]).strftime("%m-%d %H:%M")
                lines.append(f"#{r['id']:>4} {ts} {r['author']}: {r['body']}")
            self._send_paged(frm, lines or ["(no posts yet)"], title=f"[{BBS_NAME}] Recent:")

    # ---------- Notice ----------
    def _cmd_info(self, frm, args, fromId):
        if args and args[0].lower() == "set":
            if not self._is_admin(fromId):
                self._send_text(frm, "admin only"); return
            body = " ".join(args[1:]).strip()
            self.db.execute("INSERT INTO kv(k,v) VALUES('notice',?) "
                            "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (body,))
            self.db.execute("INSERT INTO kv(k,v) VALUES('notice_ts',?) "
                            "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(now()),))
            self.db.commit()
            self._send_text(frm, f"notice updated @ {nz_time_str(now())}")
        else:
            row = self.db.execute("SELECT v FROM kv WHERE k='notice'").fetchone()
            tsr = self.db.execute("SELECT v FROM kv WHERE k='notice_ts'").fetchone()
            if row and (row["v"] or "").strip():
                when = nz_time_str(int(tsr["v"])) if tsr and tsr["v"] else "unknown"
                self._send_paged(frm, [(row["v"] or "").strip()], title=f"[Notice {when}]")
            else:
                self._send_text(frm, "No notice set.")

    # ---------- DM store-&-forward ----------
    def _cmd_dm(self, frm, short, *text):
        msg = " ".join(text).strip()
        if not msg:
            self._send_text(frm, "dm usage: dm <short> <text>"); return
        target = None
        for key, entry in self._iter_nodes():
            u = entry.get("user",{})
            if isinstance(u, dict) and u.get("shortName","").lower() == short.lower():
                target = self._key_to_nodeid(key, entry); break
        if not target:
            self._send_text(frm, f"no node with short '{short}'"); return
        self.db.execute("INSERT INTO dm_out(to_id,body,created_ts,delivered_ts) VALUES(?,?,?,NULL)", (target, msg, now()))
        self.db.commit()
        self._send_text(frm, f"queued dm to {short} ({target})")

    def _deliver_queued(self, toId):
        cur = self.db.cursor()
        for row in cur.execute("SELECT id,body FROM dm_out WHERE to_id=? AND delivered_ts IS NULL LIMIT 3", (toId,)):
            self._send_text(toId, f"[DM] {row['body']}")
            cur.execute("UPDATE dm_out SET delivered_ts=? WHERE id=?", (now(), row["id"]))
        self.db.commit()

    # ---------- Admin / blacklist ----------
    def _is_admin(self, fromId): return bool(self.db.execute("SELECT 1 FROM admins WHERE id=?", (fromId,)).fetchone())
    def _admin_add(self, nid):
        try:
            self.db.execute("INSERT OR IGNORE INTO admins(id) VALUES(?)", (nid,)); self.db.commit()
        except: pass

    def _peer_add(self, nid):
        try:
            self.db.execute("INSERT OR IGNORE INTO peers(id,last_seen) VALUES(?,?)", (nid, 0)); self.db.commit()
        except: pass

    def _peer_list(self) -> List[str]:
        return [r["id"] for r in self.db.execute("SELECT id FROM peers ORDER BY id")]

    # ---------- Health ----------
    def _cmd_health(self, frm, args, fromId):
        if not HEALTH_PUBLIC and not self._is_admin(fromId):
            self._send_text(frm, "admin only"); return
        dev = self.dev_path or "n/a"
        up = fmt_uptime(now() - self.connected_at)
        try: nodes = sum(1 for _ in self._iter_nodes())
        except Exception: nodes = 0
        cur = self.db.cursor()
        posts = cur.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
        latest = cur.execute("SELECT IFNULL(MAX(id),0) m FROM posts").fetchone()["m"]
        admins = cur.execute("SELECT COUNT(*) c FROM admins").fetchone()["c"]
        peers  = cur.execute("SELECT COUNT(*) c FROM peers").fetchone()["c"]
        bl     = cur.execute("SELECT COUNT(*) c FROM blacklist").fetchone()["c"]
        qdm    = cur.execute("SELECT COUNT(*) c FROM dm_out WHERE delivered_ts IS NULL").fetchone()["c"]
        inv    = self.last_inv_at
        inv_ago = f"{(now()-inv)}s ago" if inv else "n/a"
        sync   = "on" if self.sync_enabled else "off"
        line = f"link=ok dev={dev} up={up} posts={posts} latest={latest} peers={peers} admins={admins} bl={bl} qdm={qdm} nodes={nodes} sync={sync} inv={inv_ago}"
        if len(line) <= MAX_TEXT:
            self._send_text(frm, line); return
        lines = [
            f"link=ok dev={dev} up={up}",
            f"posts={posts} latest={latest} peers={peers}",
            f"admins={admins} bl={bl} qdm={qdm} nodes={nodes}",
            f"sync={sync} last_inv={inv_ago}",
        ]
        self._send_paged(frm, lines, title=f"[{BBS_NAME}] Health:]")

    # ---------- Peer sync ----------
    def _sync_loop(self):
        while not self.stop_evt.wait(SYNC_PERIOD):
            if self.sync_enabled:
                self._broadcast_inventory()

    def _broadcast_inventory(self):
        ids = [str(r["id"]) for r in self.db.execute("SELECT id FROM posts ORDER BY id DESC LIMIT ?", (SYNC_INV_N,))]
        if not ids: return
        payload = f"{SYNC_TAG} INV ids=" + ",".join(ids[::-1])  # ascending
        for peer in self._peer_list():
            self._send_text(peer, payload)
        self.last_inv_at = now()

    def _replicate_new_post(self, pid, body, author, reply_to):
        uid = gen_uid()
        header = f"{SYNC_TAG} POST uid={uid} id={pid} ts={now()} by={author} r={(reply_to or '-')}"
        parts = [body[i:i+CHUNK_BYTES] for i in range(0, len(body), CHUNK_BYTES)] or [""]
        total = len(parts)
        for peer in self._peer_list():
            self._send_text(peer, header + f" n={total}")
            for i, ch in enumerate(parts, 1):
                self._send_text(peer, f"{SYNC_TAG} PART uid={uid} {i}/{total} {ch}")
            self._send_text(peer, f"{SYNC_TAG} END uid={uid}")

    def _handle_sync(self, fromId, text):
        toks = text.split()
        if len(toks) < 2: return
        cmd = toks[1]
        if fromId not in self._peer_list(): return

        if cmd == "INV":
            try:
                kv = dict(x.split("=",1) for x in toks[2:])
                ids = [int(x) for x in kv.get("ids","").split(",") if x]
            except Exception:
                return
            missing = []
            for i in ids:
                if not self.db.execute("SELECT 1 FROM posts WHERE id=?", (i,)).fetchone():
                    missing.append(i)
            for mid in missing[:3]:
                self._send_text(fromId, f"{SYNC_TAG} GET id={mid}")
            return

        if cmd == "GET":
            try:
                mid = int(dict(x.split("=",1) for x in toks[2:]).get("id","0"))
            except Exception:
                return
            row = self.db.execute("SELECT id,ts,author,body,reply_to FROM posts WHERE id=?", (mid,)).fetchone()
            if not row: return
            uid = gen_uid()
            header = f"{SYNC_TAG} POST uid={uid} id={row['id']} ts={row['ts']} by={row['author']} r={(row['reply_to'] or '-')}"
            parts = [row["body"][i:i+CHUNK_BYTES] for i in range(0, len(row["body"]), CHUNK_BYTES)] or [""]
            total = len(parts)
            self._send_text(fromId, header + f" n={total}")
            for i, ch in enumerate(parts, 1):
                self._send_text(fromId, f"{SYNC_TAG} PART uid={uid} {i}/{total} {ch}")
            self._send_text(fromId, f"{SYNC_TAG} END uid={uid}")
            return

        if cmd == "POST":
            try:
                kv = dict(x.split("=",1) for x in toks[2:])
                uid = kv["uid"]; total = int(kv.get("n","1"))
            except Exception:
                return
            self.db.execute("INSERT OR IGNORE INTO seen_uids(uid,ts) VALUES(?,?)", (uid, now()))
            self.db.execute("INSERT OR IGNORE INTO rxparts(uid,total,got,data,from_id,created_ts) VALUES(?,?,?,?,?,?)",
                            (uid, total, 0, "", fromId, now()))
            self.db.commit()
            return

        if cmd == "PART":
            try:
                uid = toks[2].split("=",1)[1]
                idx, tot = toks[3].split("/",1)
                idx = int(idx); tot = int(tot)
                chunk = text.split(toks[3],1)[1].strip()
            except Exception:
                return
            row = self.db.execute("SELECT uid,total,got,data FROM rxparts WHERE uid=?", (uid,)).fetchone()
            if not row: return
            total = tot
            newdata = (row["data"] or "") + chunk
            got = row["got"] + 1
            self.db.execute("UPDATE rxparts SET data=?, got=?, total=? WHERE uid=?", (newdata, got, total, uid))
            self.db.commit()
            return

        if cmd == "END":
            try:
                uid = toks[2].split("=",1)[1]
            except Exception:
                return
            if self.db.execute("SELECT 1 FROM applied_uids WHERE uid=?", (uid,)).fetchone():
                self.db.execute("DELETE FROM rxparts WHERE uid=?", (uid,))
                self.db.commit()
                return
            row = self.db.execute("SELECT data FROM rxparts WHERE uid=?", (uid,)).fetchone()
            if not row: return
            body = row["data"] or ""
            self._post_new(author=f"[peer]{fromId}", text=body, reply_to=None, do_sync=False)
            self.db.execute("INSERT OR IGNORE INTO applied_uids(uid,ts) VALUES(?,?)", (uid, now()))
            self.db.execute("DELETE FROM rxparts WHERE uid=?", (uid,))
            self.db.commit()
            return

    # ---------- Receive ----------
    def _extract_text(self, packet) -> Optional[str]:
        d = packet.get("decoded", {}) or {}
        txt = d.get("text")
        if isinstance(txt, bytes):
            try: return txt.decode("utf-8","ignore")
            except: pass
        if isinstance(txt, str): return txt
        pay = d.get("payload")
        if isinstance(pay, bytes):
            try: return pay.decode("utf-8","ignore")
            except: return None
        if isinstance(pay, str): return pay
        return None

    def _on_receive(self, packet, interface):
        try:
            fromId = self._canon_nodeid(packet.get("fromId"), packet)
            txt = self._extract_text(packet)
            if not fromId or txt is None:
                return
            self.last_rx_at = time.time()

            low_raw = txt.strip()
            norm = " ".join(low_raw.split()).lower()
            dlog(f"[recv] {fromId} ch=0: {low_raw}")

            # deliver queued DMs if this node appeared
            self._deliver_queued(fromId)

            # peer sync first
            if norm.startswith(SYNC_TAG.lower()):
                self._handle_sync(fromId, low_raw); return

            # blacklisted?
            if self.db.execute("SELECT 1 FROM blacklist WHERE id=?", (fromId,)).fetchone():
                dlog(f"[drop] blacklisted {fromId}")
                return

            # --- BYPASS rate-limit: menu/help/info (case/whitespace tolerant) ---
            if norm in ("?", "menu"):
                self._cmd_menu(fromId); return
            if norm in ("??", "help"):
                self._cmd_help(fromId); return
            if norm == "info" or norm.startswith("info "):
                args = low_raw.split()[1:] if len(low_raw.split()) > 1 else []
                self._cmd_info(fromId, args, fromId); return
            # --------------------------------------------------------------------

            # rate limit for everything else
            tprev = self.last_seen.get(fromId, 0.0)
            if time.time() - tprev < RATE_SEC:
                dlog(f"[rate] {fromId} suppressed ({time.time()-tprev:.2f}s < {RATE_SEC}s)")
                return
            self.last_seen[fromId] = time.time()

            tok = low_raw.split()
            if not tok: return
            cmd = tok[0].lower()

            if cmd == "r":
                self._cmd_read(fromId, tok[1] if len(tok) > 1 else None); return
            if cmd in ("p","post"):
                if len(tok) >= 2:
                    self._cmd_post(fromId, fromId, low_raw[len(tok[0]):].strip())
                else:
                    self._send_text(fromId, "usage: p <text>")
                return
            if cmd == "reply" and len(tok) >= 3:
                self._cmd_reply(fromId, fromId, tok[1], low_raw.split(None,2)[2]); return
            if cmd == "info":
                self._cmd_info(fromId, tok[1:], fromId); return
            if cmd == "status": self._cmd_status(fromId); return
            if cmd == "whoami": self._cmd_whoami(fromId, fromId); return
            if cmd == "whois" and len(tok)>=2: self._cmd_whois(fromId, tok[1]); return
            if cmd in ("nodes","node"): self._cmd_nodes(fromId); return
            if cmd == "dm" and len(tok)>=3: self._cmd_dm(fromId, tok[1], *tok[2:]); return
            if cmd == "health": self._cmd_health(fromId, tok[1:], fromId); return

            # Admin / blacklist
            if cmd == "admins" and len(tok)>=2 and self._is_admin(fromId):
                act = tok[1]
                if act == "add" and len(tok)>=3: self._admin_add(tok[2]); self._send_text(fromId, "admin added"); return
                if act == "del" and len(tok)>=3: self.db.execute("DELETE FROM admins WHERE id=?", (tok[2],)); self.db.commit(); self._send_text(fromId, "admin removed"); return
                if act == "list":
                    lines = [r["id"] for r in self.db.execute("SELECT id FROM admins ORDER BY id")]
                    self._send_paged(fromId, lines or ["(none)"], title="[admins]"); return

            if cmd == "bl" and len(tok)>=2 and self._is_admin(fromId):
                act = tok[1]
                if act == "add" and len(tok)>=3: self.db.execute("INSERT OR IGNORE INTO blacklist(id) VALUES(?)",(tok[2],)); self.db.commit(); self._send_text(fromId,"blacklisted"); return
                if act == "del" and len(tok)>=3: self.db.execute("DELETE FROM blacklist WHERE id=?", (tok[2],)); self.db.commit(); self._send_text(fromId,"removed"); return
                if act == "list":
                    lines = [r["id"] for r in self.db.execute("SELECT id FROM blacklist ORDER BY id")]
                    self._send_paged(fromId, lines or ["(none)"], title="[blacklist]"); return

            # Peers & sync (admin)
            if self._is_admin(fromId):
                if cmd == "peer" and len(tok)>=2:
                    act = tok[1]
                    if act == "add" and len(tok)>=3: self._peer_add(tok[2]); self._send_text(fromId,"peer added"); return
                    if act == "del" and len(tok)>=3: self.db.execute("DELETE FROM peers WHERE id=?", (tok[2],)); self.db.commit(); self._send_text(fromId,"peer removed"); return
                    if act == "list": self._send_paged(fromId, self._peer_list() or ["(none)"], title="[peers]"); return
                if cmd == "sync" and len(tok)>=2:
                    act = tok[1]
                    if act == "now": self._broadcast_inventory(); self._send_text(fromId,"sync announced"); return
                    if act == "on": self.sync_enabled = True; self._send_text(fromId, "sync on"); return
                    if act == "off": self.sync_enabled = False; self._send_text(fromId, "sync off"); return

            self._send_text(fromId, "unknown. send ? for menu")
        except Exception as e:
            print(f"[meshmini] onReceive error: {e}", flush=True)

    # ---------- Watchdog ----------
    def _watch_loop(self):
        while not self.stop_evt.wait(WATCH_TICK):
            try:
                stale = (time.time() - self.last_rx_at) > RX_STALE_SEC if self.last_rx_at else False
                if stale:
                    dlog(f"[meshmini] RX stale > {RX_STALE_SEC}s; triggering reconnect")
                    self._reconnect()
            except Exception as e:
                print(f"[meshmini] watchdog error: {e}")

    # ---------- Lifecycle ----------
    def start(self):
        self._connect()
        if self.sync_thread is None:
            self.sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
            self.sync_thread.start()
        if self.watch_thread is None:
            self.watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
            self.watch_thread.start()
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_evt.set()
            if self.iface:
                try: self.iface.close()
                except: pass

def main():
    MiniBBS(DEVICE_PATH).start()

if __name__ == "__main__":
    main()
PY
sudo chown pihole:pihole /opt/meshmini/meshmini.py
sudo chmod +x /opt/meshmini/meshmini.py
sudo systemctl restart meshmini
sudo journalctl -u meshmini -n 40 --no-pager
