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
