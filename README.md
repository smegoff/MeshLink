# MeshMini

**MeshMini** is a tiny, robust message board + tools service for [Meshtastic](https://meshtastic.org/) radios. It focuses on **simplicity, reliability**, and **admin basics** — not a full retro BBS.

- Post & read messages (with replies)
- One-line `?` menu (auto-shrinks to fit your radio’s text budget)
- Notice board (`info`) shown before the menu if set
- Store-&-forward DMs (by node **short name**)
- Clean `nodes` listing; `whois <short>` lookup
- Admin controls (admins, blacklist)
- Peer sync between multiple BBS nodes (optional)
- RX watchdog: auto-reconnect if radio goes quiet

## Quick Start (Raspberry Pi + USB Meshtastic)

```bash
# 1) Install system deps (Debian/RPi OS)
sudo apt-get update
sudo apt-get install -y python3 python3-venv

# 2) App dir & venv
sudo mkdir -p /opt/meshmini
sudo chown $USER:$USER /opt/meshmini
python3 -m venv /opt/meshmini/venv
/opt/meshmini/venv/bin/pip install --upgrade pip
/opt/meshmini/venv/bin/pip install meshtastic pypubsub

# 3) Put meshmini.py in place (see repo file)
#    and make sure it's executable:
chmod +x /opt/meshmini/meshmini.py

# 4) Create a systemd unit
sudo tee /etc/systemd/system/meshmini.service >/dev/null <<'UNIT'
[Unit]
Description=MeshMini - minimal Meshtastic BBS
After=network.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=/opt/meshmini

# Core config
Environment=PYTHONUNBUFFERED=1
Environment=MMB_DB=/opt/meshmini/board.db
Environment=MMB_DEVICE=auto
Environment=MMB_NAME=MeshLink BBS
# Optional: comma-separated admin IDs, e.g. !cafef00d,!deadbeef
# Environment=MMB_ADMINS=!cafef00d

# Behavior knobs (tweak as desired)
Environment=MMB_RATE=2
Environment=MMB_MAX_TEXT=160
Environment=MMB_HEALTH_PUBLIC=0
Environment=MMB_SYNC=1
# Environment=MMB_PEERS=!peer1,!peer2

ExecStart=/opt/meshmini/venv/bin/python /opt/meshmini/meshmini.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

# 5) Enable + start
sudo systemctl daemon-reload
sudo systemctl enable --now meshmini
sudo journalctl -u meshmini -f
