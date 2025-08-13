# MeshMini — Admin Guide

### First steps
- Add yourself as an admin via install `--admins "!yourid"` or on-air:
  - `admins add !yourid`

### Admin commands
```
admins add <!id> | admins del <!id> | admins list
bl add <!id>     | bl del <!id>     | bl list
peer add <!id>   | peer del <!id>   | peer list
sync now | sync on | sync off
info set <text>
```

### Redundant BBS (peer sync)
1. Add peer node IDs: `peer add !cafef00d`
2. Trigger a sync: `sync now` (or wait SYNC_PERIOD).
3. New posts replicate automatically; missing posts are pulled on demand.

**Tune via environment** (edit `/etc/systemd/system/meshmini.service` and `sudo systemctl daemon-reload && sudo systemctl restart meshmini`):
```
MMB_SYNC=1
MMB_PEERS="!id1,!id2"
MMB_SYNC_INV=15
MMB_SYNC_PERIOD=300
MMB_SYNC_CHUNK=160
```

### Logs
```
sudo journalctl -u meshmini -f
```
