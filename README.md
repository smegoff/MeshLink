# MeshMini — Minimal Meshtastic BBS with Peer Sync

**MeshMini** is a tiny, text-only message board for Meshtastic. It focuses on simplicity, reliability, and clear admin tools. No retro BBS fluff—just useful tools for community comms and emergencies.

## Features

- 📋 **Read & Post** threads + replies (`r`, `p`, `reply`)
- 🧭 **Compact menu** on `?` (auto-shrinks to fit Meshtastic message length)
- 📢 **Notice board** (`info`) sent *before* the menu when present  
  - Shows timestamp and optional expiry in your chosen timezone
- 🙋 **Discovery**: `nodes`, `whoami`, `whois <short>`
- ✉️ **Store-and-forward DM**: `dm <short> <text>` (delivers when node is seen)
- 🧑‍💻 **Admin** + **Blacklist** commands
- 🤝 **Peer sync** (redundant BBS nodes): inventory + chunked post replication
- 🩺 **Health**: link + DB snapshot
- 🔌 **RX watchdog**: auto-reconnect on stale link
- 🤖 **Fuzzy help** + polite unknown reply (suppresses telemetry/ACK noise)

> Tested on Raspberry Pi with a Meshtastic radio (USB).

---

## Quick start

```bash
# 1) Clone
git clone https://github.com/<you>/meshmini.git
cd meshmini

# 2) Install (creates /opt/meshmini, venv, systemd service)
#    Set your Linux username and your serial device (auto, /dev/ttyACM0, etc.)
sudo bash install.sh --user <your-username> --device auto --name "MeshLink BBS" \
  --admins "!deadbeef,!cafef00d" --peers "!12345678,!90abcdef"
