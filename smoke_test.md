## A one-page checklist to verify a fresh build does **everything** we expect (no telemetry leaks, all commands work, paging, names, game module, etc.).



:::info
Run with a live Meshtastic node connected and at least one other node in range for DM/store-and-forward tests.

:::


## 0) Prep

- [ ] Service up


`sudo systemctl restart meshmini && sudo journalctl -u meshmini -n 50 --no-pager`

- [ ] Confirm: `Connected on /dev/tty...` and no startup tracebacks.
- [ ] **Env sanity** (whatever you use in prod):

`MMB_NAME`, `MMB_MAX_TEXT=140`, `MMB_RATE=2`, `MMB_HEALTH_PUBLIC=1`,
`MMB_UNKNOWN_REPLY=1`, `MMB_RX_STALE_SEC=240`, `MMB_WATCH_TICK=10`,
`MMB_SYNC=1`, `MMB_SYNC_PERIOD=300`, `MMB_SYNC_INV=15`, `MMB_SYNC_CHUNK=160`.

- [ ] **DB present:** `/opt/meshmini/board.db` exists and is writable.

## 1) Telemetry / ACK suppression (no “unknown” spam)

- [ ] Goal: **Only** human-like text gets “I didn’t recognise that…” replies. ACKs, telemetry, positions, routing, etc. should be ignored.
- [ ] On a neighboring node, toggle GPS/telemetry beaconing or send a ping (ACK/NAK only).
  **Expect:** MeshMini stays silent (no reply).
- [ ] On the same neighbor, send random binary/emoji/not-text noise.
  **Expect:** Silent if it doesn’t decode to plain text.
- [ ] Send a short plain text like `qwerty`.
  **If** `MMB_UNKNOWN_REPLY=1` → **Expect:** `I didn't recognise that. Send '?' for menu.`
  **If** `MMB_UNKNOWN_REPLY=0` → **Expect:** Silent.


:::tip
Tip (optional): set `MMB_DEBUG=1` and `journalctl -fu meshmini` to see “filtered non-text/telemetry” drops.

:::


## 2) Menu & Help (fuzzy triggers; single-line menu)

- [ ] From a node, send: `?`, `??`, `help`, `menu`, `m` (case-insensitive).
  **Expect:**

* Any **notice** (if set) arrives as **its own** message (with created + expiry time in Pacific/Auckland).
* Then a **one-line compact menu** (auto-shrinks to stay ≤ `MMB_MAX_TEXT`).


## 3) Posting & Reading (names resolved)

- [ ] Send: `p hello world` (or `Post HELLO WORLD`)
  **Expect:** `posted #<id>`.


- [ ] Send: r list (or R, r, ls, latest, recent, L) 

  Expect: a paged “Recent” list, each line like:

  42 mm-dd hh:mm From: <ShortName> / <LongName> / !xxxxxxxx: <message>


- [ ] Send: `r 42` (replace with a real id)
  **Expect:** post header + body + any replies with sender names resolved.


- [ ] Send: `reply 42 Thanks`
  **Expect:** `reply #<rid> -> #42`. Reading `r 42` shows the reply.


## 4) Name resolution (Short/Long/NodeId)

- [ ] As an admin, set a notice with expiry:
  `info set Maintenance tonight 7–9pm | exp=3h`
  **Expect:** `notice updated`.


- [ ] Send: `?`
  **Expect first line:**
  `Notice (set 2025-08-18 13:05 NZT; expires 2025-08-18 16:05 NZT): Maintenance tonight 7–9pm`
  Then the compact one-line menu.


- [ ] After expiry time passes, send `info`.
  **Expect:** `No notice set.`


## 5) Notice board with Pacific/Auckland times

- [ ] As an admin, set a notice with expiry:
  `info set Maintenance tonight 7–9pm | exp=3h`
  **Expect:** `notice updated`.


- [ ] Send: `?`
  **Expect first line:**
  `Notice (set 2025-08-18 13:05 NZT; expires 2025-08-18 16:05 NZT): Maintenance tonight 7–9pm`
  Then the compact one-line menu.
- [ ] After expiry time passes, send `info`.
  **Expect:** `No notice set.`

  *(If you use a different expiry syntax, ad*apt the test to your current config.)

  

# 6) Status

- [ ] Send: `status`
  **Expect:** `<LongName> / <ShortName> / up <HhMm>`.


## 7) Admin rights & lists

- [ ] Send: `admin?` from both an admin node and a non-admin node.
  **Expect:** `yes` for admin, `no` for others.


- [ ] Admin list: `admins list`
  **Expect:**

```none
[admins]
!deadbeef  ABCD  Alice’s Base
!cafef00d  EFGH  Van Node
```

- [ ] Add/remove (use test ids):
  `admins add !12345678` → `admin added`
  `admins del !12345678` → `admin removed`

## 8) Blacklist

- [ ] As admin: `bl add !someid` → `blacklisted`.
- [ ] From that blacklisted node, send any command.
  **Expect:** MeshMini stays silent.
- [ ] `bl del !someid` → `removed`. Confirm it can interact again.
- [ ] `bl list` shows the table.


## 9) Direct Message (store & forward)

- [ ] From Node A: `dm BOB hello there` (where BOB is a **short name** of Node B).
  **Expect:** `queued dm to BOB (!id)`.


- [ ] When Node B next appears/talks, it receives `[DM] hello there`.
  **Edge case:** If `whois BOB` fails, DM should say `no node with short 'BO`


## 10) Peer sync (redundant BBS nodes)

*(If running a second BBS node)*

- [ ] On primary: `peer add !peerid`
- [ ] `sync now` → **Expect:** `sync announced` and peer receives `#SYNC INV ...`
- [ ] On peer: missing posts are requested via `#SYNC GET`, and chunks via `PART/END`.
  Reading `r list` on peer shows replicated posts.


## 11) Health

- [ ] Admin sends: `health`
  **Expect:** single line (or paged summary if > `MMB_MAX_TEXT`) with:
  `link=ok dev=/dev/... up=... posts=... latest=... peers=... admins=... bl=... qdm=... nodes=... sync=on/off inv=...`


## 12) Game module (MeshQuest)

*(If installed/enabled in your build*

- [ ] `game start` (or the exposed trigger in your build, e.g., `quest start`)
  **Expect:** friendly intro: “🧭 Started … Type ‘look’…”
- [ ] `look`, `move north`, `inventory`
  **Expect:** sensible text responses.
- [ ] `game stop` ends the session cleanly.
- [ ] Multiple nodes can run sessions concurrently without cross-talk.


## 13) Case-insensitivity, rate-limit, paging

- [ ] Commands in random case: `R`, `RePlY`, `INFO`, `WhoIs` → all work.
- [ ] Send 3 commands within 2s from same node.
- [ ] **Expect:** only the first one is processed (rate-limited others).
- [ ] Long outputs (`nodes`, `help`, long `r list`) arrive as `(1/N)`, `(2/N)`… pages and are readable.


## 14) Watchdog / reconnect

- [ ] Unplug/replug the serial device (or simulate silence).
  **Expect in logs:** “RX stale… reconnecting”, then “Connected on /dev/tty…”.
  **Functionally:** after reconnect, `?` and `r list` still work.


## 15) No regressions: “Unknown” reply wording & scope

- [ ] Send obvious garbage text from a node with `MMB_UNKNOWN_REPLY=1`.
  **Expect:** `I didn't recognise that. Send '?' for menu.`
- [ ] Confirm the message is **direct back to the sender**, not broadcast to `^all`.
- [ ] Confirm no such replies for **ACK/NAK**, **telemetry**, **position**, **non-text** packets.


### If something fails

* Bump debug: `MMB_DEBUG=1`, restart, `journalctl -fu meshmini`.
* Check DB schema: `sqlite3 /opt/meshmini/board.db ".schema posts"`
  Should include `id, ts, author, body, reply_to`.
* Confirm `MMB_MAX_TEXT` and menu line length; reduce name if needed.
* Verify node maps via `nodes` — if empty, try `whoami` and `status` to ensure connection.


