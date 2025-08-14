# MeshMini

**MeshMini** is a tiny, robust message board + tools service for Meshtastic radios. It focuses on **simplicity and reliability** — not a full retro BBS.

## Highlights

- Post & read messages (+ threaded replies)
- **One-line `?` menu** (auto-shrinks to fit your radio’s text limit)
- **Notice board** (`info`) sent *first* as its own message (if set)
- **Store-and-forward DMs** (by node **short name**)
- `nodes` and `whois <short>` for discovery
- Admin: `admins` and `bl` (blacklist)
- Optional **peer sync** for redundant BBS nodes
- **RX watchdog** (auto-reconnect if link goes quiet)

---

## Quick Start (Raspberry Pi + USB Meshtastic)

```bash
# 1) Install system deps
sudo apt-get update && sudo apt-get install -y python3-venv

# 2) Put files on the Pi and install:
#    (from the repo root that contains meshmini.py and install_meshmini.sh)
sudo bash install_meshmini.sh --user $USER --device auto --name "MeshLink BBS"
# Optional at install:
#   --admins "!cafef00d,!deadbeef"
#   --peers  "!peer1,!peer2"

# 3) Follow logs
sudo journalctl -u meshmini -f
