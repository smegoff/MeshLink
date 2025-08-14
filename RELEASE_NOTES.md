
---

## 3) `RELEASE_NOTES.md`

```markdown
# MeshMini Release Notes

## v0.8.6
**Fixed**
- `nodes` command now handles both integer and string keys from the Meshtastic library.
- If `.nodes` is empty, falls back to alternate internal maps and finally to the local node so the list is never blank.
- `whois` and `dm <short>` updated to use the same robust node iteration logic.

**Improved**
- One-line `?` menu with progressive shrinking to fit `MMB_MAX_TEXT`.
- Notice (if set via `info set`) is sent first, then the menu.
- Text decoding made more tolerant across firmware/library versions.
- RX watchdog: reconnect if no packets for `MMB_RX_STALE_SEC` seconds.
- Clean, paged output for lists.

**Features**
- Store-&-forward DMs by short name.
- Simple peer sync for redundant BBS nodes (inventory + chunked replication).
- Admin & blacklist management.
- Health snapshot (`health`) with counts and status.

## v0.8.5
- Initial public cut with compact menu, notice page, DM queue, peer sync, admin/blacklist, watchdog.
