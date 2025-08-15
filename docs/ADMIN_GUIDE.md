# MeshMini – Admin Guide

*A minimal, reliable Meshtastic message board with peer sync and store-and-forward DMs.*

> **Scope**: This guide is for admins running the `meshmini` service on a headless Linux box (e.g., Raspberry Pi) connected to a Meshtastic node over USB.

---

## 1) What you get

- **Simple messaging**: post/read, threaded replies, one-line menu (`?`), robust paging.
- **Notice board**: `info` notice shown before the menu; optional **expiry**; timestamps shown in **Pacific/Auckland**.
- **Discovery**: `nodes`, `whois <short>`, `whoami`.
- **Store & forward DMs**: `dm <short> <text>` queues a direct message and delivers when the node is seen.
- **Peer sync (optional)**: lightweight inventory + chunked replication to other BBS peers.
- **Admin tools**: admins, blacklist, peers, health, rate-limit & unknown-message filtering.
- **Resilience**: RX watchdog auto-reconnects if serial falls silent.

---

## 2) Daily admin quick-start

### Set (or update) admins
- **At install** (preferred): put node IDs in env var `MMB_ADMINS` (comma-sep, e.g. `!deadbeef,!cafef00d`) and restart the service.
- **At runtime** (append safely, no overwrite):
  - Add: `admins add !1234abcd`
  - Remove: `admins del !1234abcd`
  - List: `admins list`

### Set the notice (with optional expiry)
- **Show current notice**: `info`
- **Set notice (no expiry)**:  
  `info set Emergency water is available at the school hall`
- **Set notice (with expiry in hours)**, e.g., 24 hours:  
  `info set 24 Emergency water is available at the school hall`
- **What users see** when they send `?`:  
  1) The notice on its own page with **Set** and **Expires** (Pacific/Auckland), then  
  2) The compact one-line menu.

> If expiry is used, the notice automatically hides after the deadline.

### Blacklist a noisy or abusive node
- Add to blacklist: `bl add !89abcdef`
- Remove: `bl del !89abcdef`
- Show list: `bl list`

### Check system health
- `health` → quick DB/link snapshot (visible to admins; set `MMB_HEALTH_PUBLIC=1` to expose to all)
- Includes: device, uptime, posts, peers, admins, blacklist size, queued DMs, node count, sync status, last inventory.

---

## 3) User-facing commands (what your users can type)

- `?`  → show notice (if any) then compact menu  
- `??` → full help
- `r` → list recent posts  
- `r <id>` → read a post with replies  
- `p <text>` or `post <text>` → new post  
- `reply <id> <text>` → reply to a post  
- `info` → show notice  
- `status` → node long/short name + uptime  
- `whoami` → show your node ID / names  
- `nodes` → list known nodes  
- `whois <short>` → find a node by ShortName  
- `dm <short> <text>` → queue a DM; delivered when the node is seen

---

## 4) Admin-only commands

- **Admins**: `admins add|del <!id>`, `admins list`
- **Blacklist**: `bl add|del <!id>`, `bl list`
- **Peers/sync**:
  - `peer add <!id>` / `peer del <!id>` / `peer list`
  - `sync on` / `sync off` (toggle background sync)
  - `sync now` (push inventory immediately)
- **Health**: `health` (or `health full` in some builds)

---

## 5) Peer sync (optional redundancy)

- Add peer node IDs (other BBS nodes) with `peer add <!id>` or via `MMB_PEERS` in env.
- Sync periodically broadcasts an **inventory** of recent post IDs.
- Peers request any missing IDs; posts replicate in small **chunks** to fit LoRa text frames.
- Toggle with `sync on/off`; force broadcast with `sync now`.

---

## 6) Store & forward DMs

- `dm <short> <text>` stores a message **to that node ID**.
- When the target node is seen on the mesh, the DM is delivered and marked complete.
- Admin can view totals via `health` (`qdm=`).

---

## 7) Service management (systemd)

```bash
# Start / stop / restart
sudo systemctl start meshmini
sudo systemctl stop meshmini
sudo systemctl restart meshmini

# Follow logs
sudo journalctl -u meshmini -f

# View the unit file
systemctl cat meshmini

# Enable at boot
sudo systemctl enable meshmini
8) Configuration (environment file)

Edit /etc/meshmini/meshmini.env (recommended), then:

sudo systemctl restart meshmini

Common settings:

Variable	Default	Purpose
MMB_DB	/opt/meshmini/board.db	SQLite DB path
MMB_DEVICE	auto	Serial device path or auto
MMB_NAME	MeshLink BBS	BBS display name (shown in menu)
MMB_ADMINS	(empty)	Comma-sep node IDs (e.g. !deadbeef,!cafef00d)
MMB_RATE	2	Per-sender rate limit (seconds)
MMB_MAX_TEXT	140	Max chars per outbound message
MMB_HEALTH_PUBLIC	0	1 = allow anyone to run health
MMB_DEBUG	0	1 = verbose debug logs
MMB_UNKNOWN_REPLY	1	Reply to unknown text only; ignores acks/telemetry
MMB_SYNC	1	Enable peer sync
MMB_PEERS	(empty)	Comma-sep peer BBS node IDs
MMB_SYNC_INV	15	Inventory size for broadcast
MMB_SYNC_PERIOD	300	Seconds between inventory broadcasts
MMB_SYNC_CHUNK	160	Bytes per chunk for post replication
MMB_RX_STALE_SEC	240	Reconnect if no RX for N seconds
MMB_WATCH_TICK	10	Watchdog poll interval (seconds)

Unknown replies & noise: With MMB_UNKNOWN_REPLY=1, the service replies only to unknown text messages, ignoring system frames (acks, telemetry), reducing chatter.

9) Maintenance

DB backup:

sudo systemctl stop meshmini
sudo cp -a /opt/meshmini/board.db /opt/meshmini/board.db.bak.$(date +%F)
sudo systemctl start meshmini

Move DB (update MMB_DB and restart):

sudo mkdir -p /var/lib/meshmini
sudo mv /opt/meshmini/board.db /var/lib/meshmini/board.db
echo 'MMB_DB=/var/lib/meshmini/board.db' | sudo tee -a /etc/meshmini/meshmini.env
sudo systemctl restart meshmini

10) Troubleshooting

“Could not exclusively lock port /dev/ttyACM0”
Another process is using the serial port. Stop other Meshtastic tools, then:

sudo lsof /dev/ttyACM0

Ensure only meshmini holds the port.

“Timed out waiting for connection completion”
Cable/port problem or device not ready. Try another USB cable/port; power cycle the radio; check MMB_DEVICE.

No response to ?
Check logs: sudo journalctl -u meshmini -f
Verify service is running and your node is on the same channel. Rate limit might be suppressing repeats — wait a couple seconds.

Menu split across many pages
MMB_MAX_TEXT is usually 140 on Meshtastic; the menu auto-shrinks, but if you’ve lowered the max text size in firmware/channels, consider raising it here or accept the compact fallback.

Too much “unknown. send ? for menu” noise
Ensure MMB_UNKNOWN_REPLY=1 (default) so the service only replies to unknown text, not acks/telemetry. You can also set it to 0 to silence unknown replies entirely.

Nodes shows 0
Your radio may not have a current node list; wait for traffic, or reboot nodes so they announce.

11) Security tips

Keep admin list tight (MMB_ADMINS + admins add/del).

Use blacklist to silence abusers/noisy devices.

If exposing logs, beware they can include message content.

Consider moving board.db to a backed-up location.

12) License

MeshMini is typically released under the MIT License. Include LICENSE in your repo for clarity.

13) Quick reference (cheat sheet)

? → notice + menu  ?? → full help

Posts: r, r <id>, p <text>, reply <id> <text>

Info: info, info set <text> or info set <hours> <text>

People: whoami, nodes, whois <short>, dm <short> <text>

Admin: admins add|del <!id>, admins list, bl add|del <!id>, bl list

Peers: peer add|del <!id>, peer list, sync on|off|now

Health: health
MD
