#!/usr/bin/env python3
# MeshMini — minimal Meshtastic BBS with peer sync (v0.8 - health)
# Focus: simplicity, reliability, and small RF footprint.

import os, sys, time, json, sqlite3, threading, random, string
from datetime import datetime
from typing import List, Optional

# --- Meshtastic imports ---
try:
    import meshtastic
    import meshtastic.serial_interface
    from meshtastic.mesh_interface import MeshInterface
except Exception:
    sys.stderr.write("[meshmini] Missing deps. In venv: pip install meshtastic pypubsub\n")
    sys.exit(1)

DB_PATH      = os.environ.get("MMB_DB", "/opt/meshmini/board.db")
DEVICE_PATH  = os.environ.get("MMB_DEVICE", "auto")
BBS_NAME     = os.environ.get("MMB_NAME", "MeshLink BBS")
ADMINS_CSV   = os.environ.get("MMB_ADMINS", "")
RATE_SEC     = float(os.environ.get("MMB_RATE", "2"))             # rate limit per sender
CH_FALLBACK  = int(os.environ.get("MMB_CH_FALLBACK", "0"))
REPLY_BCAST  = int(os.environ.get("MMB_REPLY_BCAST", "0"))
DIRECT_FB    = int(os.environ.get("MMB_DIRECT_FALLBACK", "0"))
FB_WAIT      = float(os.environ.get("MMB_FALLBACK_SEC", "5"))
MAX_TEXT     = int(os.environ.get("MMB_MAX_TEXT", "140"))
TX_GAP       = float(os.environ.get("MMB_TX_GAP", "1.0"))
HEALTH_PUBLIC= int(os.environ.get("MMB_HEALTH_PUBLIC", "0"))      # 1 => health visible to all

# --- Peer sync knobs ---
SYNC_ON      = int(os.environ.get("MMB_SYNC", "1"))               # enable/disable sync
PEERS_ENV    = [p.strip() for p in os.environ.get("MMB_PEERS", "").split(",") if p.strip()]
SYNC_INV_N   = max(5, int(os.environ.get("MMB_SYNC_INV", "15")))  # advertised IDs per inventory
SYNC_PERIOD  = int(os.environ.get("MMB_SYNC_PERIOD", "300"))      # seconds between gossip
CHUNK_BYTES  = int(os.environ.get("MMB_SYNC_CHUNK", "160"))       # payload per PART
SYNC_TAG     = "#SYNC"                                            # control messages start with this

def now() -> int: return int(time.time())

def gen_uid(n=10):
    return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))

def fmt_uptime(seconds:int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h{m:02d}m"

class MiniBBS:
    def __init__(self, device="auto"):
        self.device = device
        self.iface: Optional[MeshInterface] = None
        self.node_id = None
        self.dev_path = None
        self.connected_at = 0
        self.last_seen = {}  # rate limit per sender
        self.stop_evt = threading.Event()
        self.sync_enabled = bool(SYNC_ON)
        self.sync_thread = None
        self.last_inv_at = 0

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
        # DM outbox
        cur.execute("CREATE TABLE IF NOT EXISTS dm_out (id INTEGER PRIMARY KEY, to_id TEXT, body TEXT, created_ts INTEGER, delivered_ts INTEGER)")
        self.db.commit()

    # ---------- Serial connect ----------
    def _candidate_ports(self) -> List[str]:
        if self.device != "auto":
            return [self.device]
        # common ACM/USB and by-id paths
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
                # register callback
                self.iface.onReceive = self._on_receive
                print(f"[meshmini] Connected on {cand}.")
                return
            except Exception as e:
                last_err = e
                time.sleep(1.0)
        raise last_err or RuntimeError("No serial candidates found")

    # ---------- Messaging helpers ----------
    def _send_text(self, dest: Optional[str], text: str):
        """dest: '!nodeid' for direct, or None for broadcast on primary channel."""
        if not self.iface: return
        text = text.strip()
        try:
            if dest and dest.startswith("!"):
                self.iface.sendText(text, destinationId=dest)
            else:
                self.iface.sendText(text)
            time.sleep(TX_GAP)
        except Exception as e:
            print(f"[meshmini] send error: {e}")

    def _send_paged(self, dest: Optional[str], lines: List[str], title=None):
        """Split into pages to stay within size limits."""
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
    def _menu_lines(self):
        return [
            f"[{BBS_NAME}] Menu:",
            "r                Read messages",
            "r <id>           Read message <id>",
            "p <text>         Post message",
            "reply <id> <t>   Reply to a message",
            "info             Show notice (if set)",
            "status           Node name/uptime",
            "whoami           Your ID and names",
            "whois <short>    Lookup node by short name",
            "nodes            List nearby nodes",
            "dm <short> <t>   Store-&-forward DM",
            "health [full]    Quick system health",
            "??               Help (detailed)",
        ]

    def _help_lines(self):
        return [
            f"[{BBS_NAME}] Help:",
            "• r — list recent posts; r 12 — read post 12",
            "• p hello world — post a new message",
            "• reply 12 Thanks — reply to post 12",
            "• info / info set <text> (admins) — notice board",
            "• status — node long/short name and uptime",
            "• whoami / whois <short> / nodes",
            "• dm <short> <text> — queue a DM (delivered when seen)",
            "• Admin: admins add|del <!id>, bl add|del <!id>",
            "• Peers: peer add|del <!id>, peer list, sync now|on|off",
            "• health [full] — quick DB + link check",
        ]

    # ---------- Commands ----------
    def _cmd_menu(self, frm):
        # optional notice as page 1
        row = self.db.execute("SELECT v FROM kv WHERE k='notice'").fetchone()
        if row and row["v"]:
            self._send_paged(frm, [row["v"]], title=None)  # notice only page 1
        self._send_paged(frm, self._menu_lines())

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
        ln = ""; sn = ""
        try:
            n = self.iface.getNode(fromId)
            if n: ln = n.get("longName",""); sn = n.get("shortName","")
        except Exception: pass
        self._send_text(frm, f"{fromId} / {sn} / {ln}")

    def _cmd_whois(self, frm, short):
        for n in self.iface.nodes.values():
            u = n.get("user","")
            if isinstance(u, dict) and u.get("shortName","").lower() == short.lower():
                nid = f"!{n['num'] & 0xffffffff:08x}"
                ln  = u.get("longName",""); sn = u.get("shortName","")
                self._send_text(frm, f"{nid} / {sn} / {ln}")
                return
        self._send_text(frm, f"no node with short '{short}'")

    def _cmd_nodes(self, frm):
        lines = []
        for num, n in self.iface.nodes.items():
            u = n.get("user",{})
            nid = f"!{num & 0xffffffff:08x}"
            lines.append(f"{u.get('shortName','?'):>6}  {nid}")
        lines.sort()
        self._send_paged(frm, lines, title=f"[{BBS_NAME}] Nodes:")

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
            if not lines: lines = ["(no posts yet)"]
            self._send_paged(frm, lines, title=f"[{BBS_NAME}] Recent:")

    def _cmd_info(self, frm, args, fromId):
        if args and args[0] == "set":
            if not self._is_admin(fromId):
                self._send_text(frm, "admin only"); return
            body = " ".join(args[1:]).strip()
            self.db.execute("INSERT INTO kv(k,v) VALUES('notice',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (body,))
            self.db.commit()
            self._send_text(frm, "notice updated")
        else:
            row = self.db.execute("SELECT v FROM kv WHERE k='notice'").fetchone()
            self._send_text(frm, row["v"] if row and row["v"] else "No notice set.")

    def _cmd_dm(self, frm, short, *text):
        msg = " ".join(text).strip()
        if not msg:
            self._send_text(frm, "dm usage: dm <short> <text>"); return
        # resolve short
        target = None
        for num, n in self.iface.nodes.items():
            u = n.get("user",{})
            if u.get("shortName","").lower() == short.lower():
                target = f"!{num & 0xffffffff:08x}"; break
        if not target:
            self._send_text(frm, f"no node with short '{short}'"); return
        # queue
        self.db.execute("INSERT INTO dm_out(to_id,body,created_ts,delivered_ts) VALUES(?,?,?,NULL)", (target, msg, now()))
        self.db.commit()
        self._send_text(frm, f"queued dm to {short} ({target})")

    def _deliver_queued(self, toId):
        cur = self.db.cursor()
        for row in cur.execute("SELECT id,body FROM dm_out WHERE to_id=? AND delivered_ts IS NULL LIMIT 3", (toId,)):
            self._send_text(toId, f"[DM] {row['body']}")
            cur.execute("UPDATE dm_out SET delivered_ts=? WHERE id=?", (now(), row["id"]))
        self.db.commit()

    def _is_admin(self, fromId):
        return bool(self.db.execute("SELECT 1 FROM admins WHERE id=?", (fromId,)).fetchone())

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

        # radio basics
        dev = self.dev_path or "n/a"
        up = fmt_uptime(now() - self.connected_at)
        nodes = 0
        try: nodes = len(self.iface.nodes)
        except Exception: pass

        # db stats
        cur = self.db.cursor()
        posts = cur.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
        latest = cur.execute("SELECT IFNULL(MAX(id),0) m FROM posts").fetchone()["m"]
        admins = cur.execute("SELECT COUNT(*) c FROM admins").fetchone()["c"]
        peers  = cur.execute("SELECT COUNT(*) c FROM peers").fetchone()["c"]
        bl     = cur.execute("SELECT COUNT(*) c FROM blacklist").fetchone()["c"]
        qdm    = cur.execute("SELECT COUNT(*) c FROM dm_out WHERE delivered_ts IS NULL").fetchone()["c"]
        rxin   = cur.execute("SELECT COUNT(*) c FROM rxparts WHERE got < total").fetchone()["c"]

        inv = self.last_inv_at
        inv_ago = f"{(now()-inv)}s ago" if inv else "n/a"
        sync = "on" if self.sync_enabled else "off"

        line = f"link=ok dev={dev} up={up} posts={posts} latest={latest} peers={peers} admins={admins} bl={bl} qdm={qdm} nodes={nodes} sync={sync} inv={inv_ago}"
        if len(line) <= MAX_TEXT:
            self._send_text(frm, line)
            return

        # fall back to multi-line (full or if too long)
        lines = [
            f"link=ok dev={dev} up={up}",
            f"posts={posts} latest={latest} peers={peers}",
            f"admins={admins} bl={bl} qdm={qdm} nodes={nodes}",
            f"sync={sync} last_inv={inv_ago}",
        ]
        self._send_paged(frm, lines, title=f"[{BBS_NAME}] Health:")

    # ---------- Peer sync protocol ----------
    def _sync_loop(self):
        while not self.stop_evt.wait(SYNC_PERIOD):
            if self.sync_enabled:
                self._broadcast_inventory()

    def _broadcast_inventory(self):
        ids = [str(r["id"]) for r in self.db.execute(
            "SELECT id FROM posts ORDER BY id DESC LIMIT ?", (SYNC_INV_N,))]
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
    def _on_receive(self, packet, interface):  # meshtastic callback
        d = packet.get("decoded", {})
        txt = d.get("text") or d.get("payload")
        if not isinstance(txt, str):  # sometimes bytes
            try: txt = txt.decode("utf-8","ignore")
            except: return
        fromId = packet.get("fromId")
        if not fromId: return

        # Deliver queued DMs if we see this node
        self._deliver_queued(fromId)

        # Rate limit
        t = self.last_seen.get(fromId, 0)
        if time.time() - t < RATE_SEC:
            return
        self.last_seen[fromId] = time.time()

        low = txt.strip()
        if low.startswith(SYNC_TAG):
            self._handle_sync(fromId, low)
            return

        # ignore blacklisted
        if self.db.execute("SELECT 1 FROM blacklist WHERE id=?", (fromId,)).fetchone():
            return

        # Menu triggers
        if low == "?" or low.lower() == "menu":
            self._cmd_menu(fromId); return
        if low == "??" or low.lower() == "help":
            self._cmd_help(fromId); return

        # Commands parsing
        tok = low.split()
        if not tok: return
        cmd = tok[0].lower()

        # Read
        if cmd == "r":
            self._cmd_read(fromId, tok[1] if len(tok) > 1 else None); return

        # Post
        if cmd == "p" or cmd == "post":
            if len(tok) >= 2:
                self._cmd_post(fromId, fromId, low[len(tok[0]):].strip())
            else:
                self._send_text(fromId, "usage: p <text>")
            return

        # Reply
        if cmd == "reply" and len(tok) >= 3:
            self._cmd_reply(fromId, fromId, tok[1], low.split(None,2)[2]); return

        # Info
        if cmd == "info":
            self._cmd_info(fromId, tok[1:], fromId); return

        # Status / whoami / whois / nodes / dm / health
        if cmd == "status": self._cmd_status(fromId); return
        if cmd == "whoami": self._cmd_whoami(fromId, fromId); return
        if cmd == "whois" and len(tok)>=2: self._cmd_whois(fromId, tok[1]); return
        if cmd == "nodes": self._cmd_nodes(fromId); return
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

        # Unknown
        self._send_text(fromId, "unknown. send ? for menu")

    # ---------- Lifecycle ----------
    def start(self):
        self._connect()
        # Start sync timer
        if self.sync_thread is None:
            self.sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
            self.sync_thread.start()
        # Keep process alive
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
    dev = DEVICE_PATH
    MiniBBS(dev).start()

if __name__ == "__main__":
    main()
