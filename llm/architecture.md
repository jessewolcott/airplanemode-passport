# Architecture

## The pieces

```
 phone browser                            AirplaneMode box (Docker)
 ─────────────                            ─────────────────────────
 /passport/  (index.html) ──Caddy──>  passport:8000  (FastAPI, app.py, one uvicorn worker, 64m)
   │ GET  api/cards                        │  cards.describe()
   │ GET  api/rooms        (every 5 s)     │  Rooms ── Room ── Seat[] ── Game (engine.py)
   │ POST api/rooms[/{code}/...]           │  (Rooms, Room, Seat from amrooms; PassRoom adds
   │                                       │   the game)        └─ bots{seat: Bot} + driver task
   │ GET  api/rooms/{code}/state?since=v ──│  long-poll: waits up to PASSPORT_POLL s for a bump
   │                                       │  sweeper task: every 5 s, Rooms.sweep()
```

- **`cards.py`**: plain data. `Card` dataclass, `BASE` (7), `KINGDOM` (24), `PRESETS` (3),
  `pile_size`, `describe()`. No behaviour.
- **`engine.py`**: the rules. Pure (no I/O, no clock); all randomness from `Game.rng`
  (`random.Random(seed)`), so a seed replays a game exactly (`test_seeded_games_repeat`).
- **`bots.py`**: `Bot(level, seed).move(game, seat)` returns a legal intent or `None`.
- **`rooms.py`**: `PassRoom`, on top of **`amrooms`** (the platform's Rooms SDK,
  `../airplanemode/sdk/rooms`, decision 0022). The SDK gives seats, tokens, host, lobby
  helpers, versions and the long-poll event, the per-room driver task and the registry
  (eviction, forgetting rooms). `PassRoom` adds the card set, start, moves, the view, and the
  driver's `plan()`: bot moves, takeover of away people, the pause.
- **`app.py`**: thin HTTP layer over one module-level `rooms = make_rooms()`; turns
  `RoomError` and `HTTPException` into `{"error": "..."}` JSON.
- **`static/index.html`**: three screens (home, lobby, game) plus bottom sheets.

No other AirplaneMode app is called. The only platform call is who's who: `whois()` asks
the admin service (`ADMIN_URL`, default `http://admin:8000`) for `/api/me` before create and
join, passing on only `X-Forwarded-For` and the `am_who` cookie. It labels seats (name,
emoji) and finds a person's seat from another browser; it never authorises anything, and the
game works without it (None on any failure). No `am_admin` check: there is no admin feature.
`app.yaml` says `needs: {llm: none}`.

## Request flow: a solo game

1. `GET /passport/` -> `index()` (`app.py:211`) serves `static/index.html` with
   `Cache-Control: no-cache`.
2. `boot()` loads `api/cards` (all 31 cards + presets). If `localStorage["passport.room"]`
   holds `{code, token}` it asks `state?since=0`; if the server still knows the seat it goes
   straight back to the table, else it clears the saved room and shows home.
3. "Play vs bots" -> `POST api/rooms {name, bots: [level x n], kingdom, start: true}`.
   `create_room` (`app.py:103`) asks `whois`, makes the room, seats the person (host), sets the kingdom,
   adds bots, starts the game, and returns `{code, token, state}`.
4. `PassRoom.start` builds a `Game`, bumps the version and calls `ensure_driver`, which
   starts the SDK's driver task; its `plan()` plays bot seats with a 0.7-1.2 s delay per move.
5. The page loops `GET state?token=&since=<version>`. The server returns at once if the
   version moved, otherwise waits on the room's `asyncio.Event` up to 20 s. Every bot move
   and every person's move bumps the version and wakes all waiting polls.
6. Moves go to `POST act {token, intent}`; the response is the new state, rendered at once.

## How it plugs into the platform

- Platform `compose.yaml` service `passport`: image
  `ghcr.io/jessewolcott/airplanemode-passport:0.1.2` (pinned), `mem_limit
  ${PASSPORT_MEM:-64m}`, `restart: unless-stopped`. No env, no volume.
- `hardware/nanopi-r6s.env`: `PASSPORT_MEM=64m`; `hardware/glinet.env`: `PASSPORT_MEM=48m`
  (decision 0016: 48 MB on GL.iNet for the in-memory apps).
- Platform `compose.dev.yaml` builds `../airplanemode-passport` as `airplanemode-passport:dev`.
- Platform `Caddyfile`: `redir /passport /passport/` and
  `handle_path /passport/* { reverse_proxy passport:8000 }`. `handle_path` strips the prefix,
  so the app's routes are rooted at `/` and the page must use relative URLs (`api/rooms`).
  Long-polls of ~20 s pass through Caddy's defaults with no extra config.
- Platform `portal/apps.json`: id `passport`, path `/passport/`, category Games,
  `uses_llm: false`, icon `/icons/passport.svg`. Platform CI checks image tag, Caddy route and
  apps.json entry exist together.
- `app.yaml` here: id `passport`, port 8000, path `/passport/`, memory 64m, `needs: {llm:
  none}`, documented env vars.

## Configuration (environment)

| Var | Default | Read at | Meaning |
|---|---|---|---|
| `PASSPORT_BOT_DELAY` | `0.7-1.2` | `rooms.py:27-36` (`bot_delay`, read on every call) | Seconds before each bot move, `"min-max"` or one number; unparsable -> 1.0. Tests use `0` |
| `PASSPORT_TAKEOVER` | `60` | `rooms.py:43` (`PassRoom.away_after`, import time) | Seconds without a request from a person before they count as away |
| `PASSPORT_ROOM_TTL` | `7200` | `rooms.py:174` (`make_rooms`, import time) | Seconds since the room was last changed or polled by a seated person before it's forgotten |
| `PASSPORT_POLL` | `20` | `app.py:49` and `rooms.py:44` (import time) | How long a state request waits for a change. Tests use `1` |
| `ADMIN_URL` | `http://admin:8000` | `amrooms.whois` (every call) | Where who's who asks `/api/me`; empty turns it off (tests do) |

`app.yaml`'s `env:` block documents all five; the README settings table the four `PASSPORT_*` vars.

## Constants

| Name | Where | Value | Meaning |
|---|---|---|---|
| `HAND_SIZE` | `engine.py:34` | 5 | Cards drawn at clean-up |
| `ROUND_LIMIT` | `engine.py:35` | 60 | Safety net: game ends once every player has had 60 turns |
| `LOG_KEEP` | `engine.py:36` | 200 | Log entries kept (views send the last 80) |
| `CODE_ALPHABET` | amrooms | A-Z minus I, L, O | Room codes, 4 letters |
| `max_rooms` | `rooms.py:174` (`make_rooms`) | 200 | Rooms in memory |
| `PassRoom.max_seats` | `rooms.py:40` | 4 | Seats per room |
| `PassRoom.bot_names` | `rooms.py:42` | Ada ... Juno (10) | A random unused one for a new bot |
| `empty_grace` | amrooms `Rooms` | 60 s | A game everyone left is kept this long after its last change |
| `SWEEP_SECONDS` | `app.py:50` | 5 | Sweeper interval |

## Process model

Single uvicorn worker (`CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]`).
All state is in that process; the room's `asyncio.Event` and the driver tasks only work
within one event loop, so never add `--workers`. Game mutations are plain synchronous code
inside `async def` handlers, so they never interleave with each other: there are no locks.
The exceptions are the plain `def` endpoints (`health`, `all_cards`, `index`, `icon`), which
FastAPI runs in a threadpool and which touch no room state. `list_rooms` is `async def`
because `open_lobbies()` sweeps, which can start driver tasks (it used to be a plain `def`
and could 500; see [gotchas-and-extending.md](gotchas-and-extending.md)). The SDK's
`ensure_driver()` also returns quietly when called off the event loop, leaving the start to
the sweeper. The driver runs `plan()`'s actions under `room.lock`; the HTTP handlers don't
take it (they have no awaits between reading and changing a room).

The lifespan (`app.py:55-59`) starts `rooms.sweep_forever(SWEEP_SECONDS)`: every 5 s it
forgets idle and empty rooms and calls `ensure_driver()` on the rest. A driver keeps
re-planning at least every 2 s, which is how takeover of an idle person starts without
anyone polling. FastAPI's docs/OpenAPI endpoints are off (`app.py:62`).

The container runs as an unprivileged `passport` user (uid 10001) and writes nothing to disk.
