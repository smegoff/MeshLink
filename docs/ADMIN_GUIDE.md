# Admin Guide

## Make yourself admin
Provide your node id (`!xxxxxxxx`) to the installer via `--admins`. Or later:
```
admins add !deadbeef
```
List/remove:
```
admins list
admins del !deadbeef
```

## Setting the notice
```
info set Road closed at Bridge St. Use detour via Main Rd.
```
- The timestamp in **Pacific/Auckland** is saved automatically.
- Users see the notice when they send `?`, and when they send `info`.

## Blacklist
```
bl add <!id>
bl list
bl del <!id>
```

## Peers & sync (optional redundancy)
Add peer BBS node IDs:
```
peer add !11111111
peer list
peer del !11111111
```
Trigger an inventory push:
```
sync now
```
Toggle:
```
sync on
sync off
```

## Health check
```
health
```
Shows device link, counts, last inventory push, etc.
