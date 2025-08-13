# MeshMini (with Peer Sync)

Minimal Meshtastic message board for a headless Raspberry Pi:
- Post / read / reply
- Notice board
- `whoami`, `whois`, `nodes`
- Store-&-forward DMs
- Admin / blacklist
- **Redundant BBS** via lightweight on-mesh sync (peer gossip)

## Install (one-liner)
```bash
unzip meshmini_peer_sync_bundle.zip && cd meshmini_peer_sync_bundle
sudo bash install.sh --user <pi-user> --device auto --name "MeshLink BBS" --admins "!deadbeef" --peers "!cafef00d,!baadf00d"
```
Then watch logs:
```bash
sudo journalctl -u meshmini -f --no-pager
```

## On-air Commands (user)
```
?            menu (notice on page 1 if set)
??           help
r            list recent posts
r <id>       read a post
p <text>     post a message
reply <id> <text>   reply to a post
info         show notice
status       node long/short/uptime
whoami       shows your node ID and names
whois <short>   lookup by short name
nodes        list nearby nodes
dm <short> <text>  queue a DM (delivered when seen)
```

## Admin
```
admins add <!nodeid> | admins del <!nodeid> | admins list
bl add <!nodeid>     | bl del <!nodeid>     | bl list
peer add <!nodeid>   | peer del <!nodeid>   | peer list
sync now | sync on | sync off
info set <text>
```

### Peer Sync (how it works)
- Every SYNC_PERIOD (default 300s) each BBS sends an **inventory** of recent post IDs to peers.
- Peers request only the missing IDs (`GET`), and the sender transmits those posts in small `PART` chunks.
- Each post carries a unique `uid`; duplicates are dropped (`applied_uids`).

Environment knobs (edit service or set and `systemctl restart meshmini`):
```
MMB_SYNC=1                # enable/disable sync
MMB_PEERS="!id1,!id2"     # peers
MMB_SYNC_INV=15           # inventory size
MMB_SYNC_PERIOD=300       # seconds
MMB_SYNC_CHUNK=160        # bytes per PART
MMB_MAX_TEXT=140
```

## Service file
Installed to `/etc/systemd/system/meshmini.service`. Runs under `--user` you provide. Ensure that user is in the `dialout` group for serial.
