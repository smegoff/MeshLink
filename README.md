# MeshMini (v0.8 — Health)

Adds a tiny `health` command on top of v0.7 (peer sync).

### New command
```
health           # one-line: link/dev/uptime/posts/peers/admins/etc
health full      # verbose multi-line if needed
```
By default `health` is **admin-only**. To allow anyone:
```
MMB_HEALTH_PUBLIC=1
```

### Upgrade
```
sudo cp meshmini.py /opt/meshmini/meshmini.py
sudo systemctl restart meshmini
sudo journalctl -u meshmini -f --no-pager
```
