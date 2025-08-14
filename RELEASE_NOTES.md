# Release Notes

## v0.9.1
- Notice timestamps added (Pacific/Auckland) and shown when `info` or `?` is used.
- `nodes` / `whois` made resilient to library differences.
- Safer responses: replies are addressed to the sender (no accidental broadcast).
- Stability: RX watchdog and reconnects; pubsub backup listener.
- Minor log hygiene and help text cleanup.

## v0.9.0
- Compact single-line menu for `?` that auto‑shrinks to fit `MAX_TEXT`.
- Store‑and‑forward DMs by shortName.
- Peer sync (inventory + chunked replication for posts).
- Admin/blacklist + health snapshot.
