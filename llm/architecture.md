# Architecture

## The pieces

```
 phone browser                            AirplaneMode box (Docker)
 ─────────────                            ─────────────────────────
 /passport/  (index.html) ──Caddy──>  passport:8000  (FastAPI, app.py, one uvicorn worker, 64m)
   │ GET  api/cards                        │  cards.describe()
   │ GET  api/rooms        (every 5 s)     │  Rooms ── Room ── Seat[] ── Game (engine.py)
   │ POST api/rooms[/{code}/...]           │                    └─ bots{seat: Bot} + asyncio task
   │ GET  api/rooms/{code}/state?since=v ──│  long-poll: waits up to PASSPORT_POLL s for a bump
   │                                       │  sweeper task: every 5 s, Rooms.sweep()
```

- **`cards.py`**: plain data. `Card` dataclass, `BASE` (7), `KINGDOM` (24), `PRESETS` (3),
  `pile_size`, `describe()`. No behaviour.
- **`engine.py`**: the rules. Pure (no I/O, no clock); all randomness from `Game.rng`
  (`random.Random(seed)`), so a seed replays a game exactly (`test_seeded_games_repeat`).
- **`bots.py`**: `Bot(level, seed).move(game, seat)` returns a legal intent or `None`.
- **`rooms.py`**: everything multiplayer: seats, tokens, host, lobby ops, versions and the
  long-poll event, the per-room bot driver task, takeover of away people, forgetting rooms.
- **`app.py`**: thin HTTP layer over one module-level `Rooms()`; turns `RoomError` and
  `HTTPException` into `{"error": "..."}` JSON.
- **`static/index.html`**: three screens (home, lobby, game) plus bottom sheets.

There is no dependency on any other AirplaneMode app or on the platform admin service (no
`am_admin` check: there is no admin feature). `app.yaml` says `needs: {llm: none}`.

## Request flow: a solo game

1. `GET /passport/` -> `index()` (`app.py:210`) serves `static/index.html` with
   `Cache-Control: no-cache`.
2. `boot()` loads `api/cards` (all 31 cards + presets). If `localStorage["passport.room"]`
   holds `{code, token}` it asks `state?since=0`; if the server still knows the seat it goes
   straight back to the table, else it clears the saved room and shows home.
3. "Play vs bots" -> `POST api/rooms {name, bots: [level x n], kingdom, start: true}`.
   `create_room` (`app.py:101`) makes the room, seats the person (host), sets the kingdom,
   adds bots, starts the game, and returns `{code, token, state}`.
4. `Room.start` builds a `Game`, bumps the version and calls `ensure_driver`, which starts
   an asyncio task that plays bot seats with a 0.7-1.2 s delay per move.
5. The page loops `GET state?token=&since=<version>`. The server returns at once if the
   version moved, otherwise waits on the room's `asyncio.Event` up to 20 s. Every bot move
   and every person's move bumps the version and wakes all waiting polls.
6. Moves go to `POST act {token, intent}`; the response is the new state, rendered at once.

## How it plugs into the platform

- Platform `compose.yaml` service `passport`: image
  `ghcr.io/jessewolcott/airplanemode-passport:0.1.0` (pinned), `mem_limit
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
| `PASSPORT_BOT_DELAY` | `0.7-1.2` | `rooms.py:34-43` (`bot_delay`, read on every call) | Seconds before each bot move, `"min-max"` or one number; unparsable -> 1.0. Tests use `0` |
| `PASSPORT_TAKEOVER` | `60` | `rooms.py:30` (import time) | Seconds without a request from a person before they count as away |
| `PASSPORT_ROOM_TTL` | `7200` | `rooms.py:29` (import time) | Seconds since the last version bump before a room is forgotten |
| `PASSPORT_POLL` | `20` | `app.py:40` (import time) | How long a state request waits for a change. Tests use `1` |

`app.yaml`'s `env:` block and the README settings table document all four.

## Constants

| Name | Where | Value | Meaning |
|---|---|---|---|
| `HAND_SIZE` | `engine.py:34` | 5 | Cards drawn at clean-up |
| `ROUND_LIMIT` | `engine.py:35` | 60 | Safety net: game ends once every player has had 60 turns |
| `LOG_KEEP` | `engine.py:36` | 200 | Log entries kept (views send the last 80) |
| `CODE_ALPHABET` | `rooms.py:26` | A-Z minus I, L, O | Room codes, 4 letters |
| `MAX_ROOMS` | `rooms.py:27` | 200 | Rooms in memory |
| `MAX_SEATS` | `rooms.py:28` | 4 | Seats per room |
| `BOT_NAMES` | `rooms.py:31` | Ada ... Juno (10) | First unused name for a new bot |
| `SWEEP_SECONDS` | `app.py:41` | 5 | Sweeper interval |

## Process model

Single uvicorn worker (`CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]`).
All state is in that process; the room's `asyncio.Event` and the driver tasks only work
within one event loop, so never add `--workers`. Game mutations are plain synchronous code
inside `async def` handlers, so they never interleave with each other: there are no locks.
The exceptions are the plain `def` endpoints (`health`, `all_cards`, `index`, `icon`), which
FastAPI runs in a threadpool and which touch no room state. `list_rooms` is `async def`
because it calls `rooms.sweep()`, which can start bot tasks (it used to be a plain `def` and
could 500; see [gotchas-and-extending.md](gotchas-and-extending.md)). `ensure_driver()` also
returns quietly when called off the event loop, leaving the start to the sweeper.

The lifespan (`app.py:46-57`) starts a sweeper task: every 5 s `rooms.sweep()` forgets idle
rooms and calls `ensure_driver()` on running games (that is how takeover of an idle person
starts without anyone polling). FastAPI's docs/OpenAPI endpoints are off (`app.py:60`).

The container runs as an unprivileged `passport` user (uid 10001) and writes nothing to disk.
