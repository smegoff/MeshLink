# Admin Guide

This covers admin features accessible from the mesh and how to manage the service on the Pi.

## On-air admin commands

### Admins
- Show admins: `admins`
- Add admin: `admins add !deadbeef`
- Remove admin: `admins remove !deadbeef`
- Clear all admins: `admins clear` (reverts to everyone permitted for admin ops)

Bootstrap mode: if there’s no admin list yet, everyone is considered admin until you add one.

### Blacklist
- Show blacklist: `blacklist`
- Add: `blacklist add !cafef00d`
- Remove: `blacklist remove !cafef00d`
- Clear: `blacklist clear`

### BBS Name & Notice
- Get/set name: `name` / `name set MeshLink BBS`
- Info banner: `info` / `info set <text>` (prints on its own page before menus)

### Store & Forward (DM queue)
- List queued: `sf list` (admin view)
- Flush for a node: `sf flush <short|!id>`
- Purge one queued message: `sf purge <Q#>`

## Service control (Pi)

```bash
# Logs
sudo journalctl -u meshmini -f

# Restart / status
sudo systemctl restart meshmini
systemctl status meshmini --no-pager
```

## Tuning

Edit `/etc/systemd/system/meshmini.service` Environment lines and then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart meshmini
```

Key knobs:
- `MMB_RATE=8` per-sender rate limit (seconds)
- `MMB_MAX_TEXT=110` reply chunk size
- `MMB_DIRECT_FALLBACK=1` broadcast if no direct ACK
- `MMB_SF_NOTIFY=1` notify sender when queued DM delivers
- `MMB_TTL_HOURS=72` queued message expiry

## Backups

The SQLite database is at `/opt/meshmini/board.db`. Safe to copy while service is running thanks to WAL.
