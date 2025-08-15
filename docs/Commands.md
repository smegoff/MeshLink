Commands (user)

?, menu, help — show menu / help

r — list recent posts

r <id> — read a post + its replies

p <text> — post a new message

reply <id> <text> — reply to a post

nodes — show known nodes

whoami — show your ID + names

whois <short> — look up by short name

dm <short> <text> — store-and-forward direct message

info — show the notice (timestamp + optional expiry)

status — BBS node long/short/uptime

health — link + DB snapshot (public if enabled; otherwise admin-only)

Commands (admin)

admins add|del <!id>; admins list

bl add|del <!id>; bl list (blacklist)

info set <text> — set/update notice

optional expiry via env: MMB_INFO_TTL_MIN or MMB_INFO_UNTIL

peer add|del <!id>; peer list

sync now|on|off

health full

Node IDs are Meshtastic IDs like !ba654c8c (hex, with leading !).
