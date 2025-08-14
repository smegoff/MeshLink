# MeshMini — a tiny Meshtastic message board

**MeshMini** is a minimal, reliable message board (not a full BBS) for Meshtastic networks.

- ✅ Post / read / reply
- ✅ One-line **notice** shown before the menu
- ✅ Compact `?` menu in a single message; `??` for detailed help
- ✅ Store-and-forward **DM** to a node by **short name**
- ✅ Admin & blacklist controls
- ✅ Optional **peer sync** between multiple BBS nodes (inventory + chunked post replication)
- ✅ SQLite persistence
- ✅ **Watchdog** auto-reconnect + optional PubSub fallback for receive path

MIT-licensed and designed to be deployed on a headless Raspberry Pi connected to a Meshtastic node via USB.

---

## Quick start (Raspberry Pi)

```bash
# 1) Install system deps
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip sqlite3

# 2) App layout
sudo mkdir -p /opt/meshmini
sudo chown -R $USER:$USER /opt/meshmini
python3 -m venv /opt/meshmini/venv
/opt/meshmini/venv/bin/pip install --upgrade pip
/opt/meshmini/venv/bin/pip install meshtastic pypubsub

# 3) Drop the app file
nano /opt/meshmini/meshmini.py
# (paste the file from this repo)

# 4) Systemd service (replace <user> with your username)
sudo tee /etc/systemd/system/meshmini.service >/dev/null <<'UNIT'
[Unit]
Description=MeshMini - minimal Meshtastic BBS
After=network.target

[Service]
Type=simple
User=<user>
Group=<user>
WorkingDirectory=/opt/meshmini

# Runtime config
Environment=PYTHONUNBUFFERED=1
Environment=MMB_DB=/opt/meshmini/board.db
Environment=MMB_DEVICE=auto
Environment=MMB_NAME=MeshLink BBS
# Optional: initial admins (comma-separated Meshtastic IDs like !ba654c8c)
# Environment=MMB_ADMINS=!deadbeef,!cafef00d

# Behavior knobs
Environment=MMB_MAX_TEXT=140
Environment=MMB_RATE=2
Environment=MMB_HEALTH_PUBLIC=1
Environment=MMB_DEBUG=0

# Peer sync (optional)
Environment=MMB_SYNC=1
# Environment=MMB_PEERS=!11223344,!a1b2c3d4
Environment=MMB_SYNC_PERIOD=300
Environment=MMB_SYNC_INV=15
Environment=MMB_SYNC_CHUNK=160

# Watchdog
Environment=MMB_RX_STALE_SEC=240
Environment=MMB_WATCH_TICK=10

ExecStart=/opt/meshmini/venv/bin/python /opt/meshmini/meshmini.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now meshmini
sudo journalctl -u meshmini -f --no-pager
