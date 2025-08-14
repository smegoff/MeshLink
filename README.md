# MeshMini — minimal Meshtastic BBS (with peer sync)

**MeshMini** is a tiny headless message board for [Meshtastic](https://meshtastic.org/) nodes. It focuses on **simplicity** and **reliability**, while still offering niceties like store‑and‑forward DMs, node discovery, admin controls, and optional peer synchronization for redundancy.

- Single‑line compact `?` menu (auto-shrinks to fit radio limits)
- Notice board (admins can `info set ...`), notice timestamp shown in **Pacific/Auckland**
- Post/read replies; store‑and‑forward DMs by short name
- Nodes / whois discovery
- Optional peer sync (inventory + chunked replication)
- RX watchdog (auto‑reconnect), pubsub backup
- SQLite persistence; admin + blacklist; health

## Quick start

```bash
# On the Pi connected to a Meshtastic node
git clone <your repo> meshmini
cd meshmini
sudo bash install.sh --user <pi-username> --device auto --name "MeshLink BBS" --admins "!deadbeef"
sudo journalctl -u meshmini -f --no-pager
```

The installer puts files here:

- App: `/opt/meshmini/meshmini.py` (+ venv at `/opt/meshmini/venv`)
- DB:  `/opt/meshmini/board.db` (SQLite)
- Env: `/etc/meshmini/meshmini.env`
- Service: `/etc/systemd/system/meshmini.service`

Edit config in `/etc/meshmini/meshmini.env`, then:

```bash
sudo systemctl restart meshmini
```

## Commands (from any node on the channel)

- `?` — notice (if set) then compact one‑line menu
- `??` — full help
- `r` — list recent posts
- `r <id>` — read a post and its replies
- `p <text>` — create a new post
- `reply <id> <text>` — reply to post
- `info` — show notice (with NZ timestamp)
- `info set <text>` — set notice (admins only)
- `status` — device long/short name and uptime
- `whoami` — your node ID/short/long name
- `nodes` — list known nodes
- `whois <short>` — show node id + names
- `dm <short> <text>` — queue a direct message; will send when the node is seen again
- `health` — quick DB + link summary (admins by default; public if `MMB_HEALTH_PUBLIC=1`)

### Admin / blacklist / peers

- `admins add <!id>` / `admins del <!id>` / `admins list`
- `bl add <!id>` / `bl del <!id>` / `bl list`
- `peer add <!id>` / `peer del <!id>` / `peer list`
- `sync now` — announce inventory to peers
- `sync on|off` — toggle replication

> **Node IDs** are `!xxxxxxxx` hex strings. Short names must match the node’s current shortName case‑insensitively.

## Environment variables

Edit `/etc/meshmini/meshmini.env`:

```
MMB_DB=/opt/meshmini/board.db
MMB_DEVICE=auto                # or /dev/ttyACM0, etc.
MMB_NAME=MeshLink BBS
MMB_ADMINS=!deadbeef,!cafef00d
MMB_MAX_TEXT=140
MMB_RATE=2
MMB_HEALTH_PUBLIC=1            # allow non-admins to use 'health'
MMB_DEBUG=0
MMB_SYNC=1
MMB_PEERS=!11111111,!22222222
MMB_SYNC_PERIOD=300
MMB_SYNC_INV=15
MMB_SYNC_CHUNK=160
MMB_RX_STALE_SEC=240
MMB_WATCH_TICK=10
```

## Updating

```bash
cd /opt/meshmini
sudo systemctl stop meshmini
sudo cp /path/to/new/meshmini.py /opt/meshmini/meshmini.py
sudo chown <user>:<user> /opt/meshmini/meshmini.py
sudo chmod +x /opt/meshmini/meshmini.py
sudo systemctl start meshmini
```

## License

MIT (see `LICENSE`).
