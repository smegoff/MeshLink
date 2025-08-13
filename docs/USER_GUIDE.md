# User Guide

Welcome to **MeshLink BBS** — a lightweight Meshtastic BBS.

## Basics
- Send `?` for the quick menu (notice page first if set).
- Send `??` for the full help.
- Post: `p <text>`
- Read list: `r`
- Read one: `r <id>`
- Reply: `reply <id> <text>`

## Direct Messages (Store & Forward)
- Send: `msg <short|!id> <text>`
- If the node is online, you’ll see `Delivered…`.
- If offline, you’ll get `Queued (Q#…)` — delivery will be retried when the node is heard.
- Check your pending messages with `outbox`.

## Who’s who
- `whoami` — your node ID + long/short names (if known)
- `whois <short|!id>` — lookup for a node
- `lastseen <short|!id>` — how long since last heard
- `nodes` — what the BBS currently knows

## Notice
- If the operator has set a notice, it is shown before the menu.
- You can always read it again with `info`.
