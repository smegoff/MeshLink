# MeshLink — Minimal Meshtastic BBS (Generic)

A tiny, reliable BBS for a Raspberry Pi connected via USB to a Meshtastic node.

- Read / Post / Reply
- Notice banner (its own page) + quick & full menus
- Direct Message (DM) store & forward
- whoami, whois, lastseen, nodes
- Admins and Blacklist
- Robust reconnects, rate-limit, dedup, paging

Default name: **MeshLink BBS** (configurable via `MMB_NAME`).

## Quick start

```bash
# On your Pi (Debian Bookworm recommended)
sudo bash install.sh --user <pi-user> --device auto --name "MeshLink BBS" --admins "!deadbeef"
```

- Service: `meshmini.service`
- App dir: `/opt/meshmini`
- DB: `/opt/meshmini/board.db`

See **docs/USER_GUIDE.md** and **docs/ADMIN_GUIDE.md**.
