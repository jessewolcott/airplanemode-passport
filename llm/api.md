# HTTP API (`app.py`)

All paths are relative to the app root; on the box Caddy serves them under `/passport/`
(prefix stripped). The list is also in `app.py`'s docstring.

## Conventions

- **POSTs** go through `body()` (`app.py:73-82`): header `X-Passport: 1` or
  `403 "Missing X-Passport header."` (blocks cross-site form posts); JSON body or
  `400 "Send JSON."`; must be an object or `400 "Send a JSON object."`
- **Errors** are always `{"error": "Human sentence."}` with a status: `RoomError` and
  `HTTPException` both go through handlers (`app.py:63-70`). `IllegalMove` from the engine
  becomes `RoomError(400, message)` in `Room.act`. The page shows `error` in a toast.
- **GETs** need no header. There's no auth beyond the seat token, and no rate limiting.
- Responses that carry a room are `Room.view(token)`: see
  [rooms-and-long-poll.md](rooms-and-long-poll.md).

## Endpoints

| Method + path | Body / query | Returns | Errors |
|---|---|---|---|
| `GET /` | | `static/index.html`, `Cache-Control: no-cache` | |
| `GET /icon.svg` | | the icon, `image/svg+xml` | |
| `GET /health` | | `{"ok": true}` | |
| `GET /api/cards` | | `cards.describe()`: `{cards: {id: {id,name,cost,types,text,coin,vp,kingdom}}, presets: [{id,name,blurb,cards}]}` (31 cards, 4 presets incl. random) | |
| `GET /api/rooms` | | `{rooms: [{code, host, players, kingdom}]}` open lobbies (sweeps first) | none (it used to 500 in a rare case; fixed, see gotchas) |
| `POST /api/rooms` | `{name, bots?: [level], kingdom?, start?}` | `{code, token, state}` | 400 "Up to 3 bots." / bad level / unknown set / "needs 2 to 4 players" (when `start` with no bots); 503 room cap |
| `POST /api/rooms/{code}/join` | `{name, token?}` | `{token, state}` | 404 no room; 409 started or full |
| `GET /api/rooms/{code}/state` | `?token=&since=` | the view, after waiting | 404 no room |
| `POST /api/rooms/{code}/lobby` | `{token, op, ...}` | the view | 403 not seated / not host; 400 "Unknown lobby change." etc. |
| `POST /api/rooms/{code}/start` | `{token}` | the view | 403 not host; 409 already started; 400 seat count |
| `POST /api/rooms/{code}/act` | `{token, intent}` | the view | 403 not seated; 409 left the game / no game; 400 illegal move |
| `POST /api/rooms/{code}/rematch` | `{token}` | the view | 403 not seated; 409 "Finish this game first." |
| `POST /api/rooms/{code}/leave` | `{token}` | `{"ok": true}` | 403 not seated |

### `POST /api/rooms` (`app.py:101-119`)

Validates `bots` is a list of at most 3, creates the room, seats the caller (host), then
applies `kingdom` (if truthy), adds each bot, and starts if `start` is truthy. If any of
those raises a `RoomError`, the half-made room is removed before re-raising. The `start`
flag (documented in the docstring's API list) is what the page's "Play vs bots" uses.

### `POST /api/rooms/{code}/join` (`app.py:122-130`)

If `token` matches a seat in the room, the caller gets that seat back via `Room.rejoin`
(works mid-game; it is the only thing that clears `left`, and it makes the person host if the
room has none). Otherwise a new seat is added (lobby only). The name in the body is ignored
on rejoin.

### `GET /api/rooms/{code}/state` (`app.py:133-140`)

`seen(token)` -> `ensure_driver()` -> `await room.wait(since, POLL_SECONDS)` -> `seen(token)`
again -> view. `seen` only refreshes the seat's last-request time; it never undoes a leave
(the page's own poll is in flight when it posts leave). Defaults `token=""`, `since=0` (returns immediately). An unknown token is
fine: you get the public view with `you: null` (the page treats that as "you're no longer
in that room").

### `POST /api/rooms/{code}/lobby` (`app.py:143-162`)

| `op` | Who | Fields |
|---|---|---|
| `rename` | anyone seated | `name` |
| `add_bot` | host | `level` (default `"normal"`) |
| `remove` | host | `seat` (int; bools rejected by `int_or_400`: "Which seat?") |
| `bot_level` | host | `seat`, `level` |
| `kingdom` | host | `kingdom` (`first`, `business`, `backpacker`, `random`) |

### `POST /api/rooms/{code}/act` (`app.py:180-186`)

`intent` is passed to `Game.act` unchanged; see the intents table in
[engine-and-cards.md](engine-and-cards.md). Answer intents must carry the decision's `id`
from `game.pending.id`.

### `POST /api/rooms/{code}/rematch` (`app.py:189-197`)

Any seated person who hasn't left (not just the host; 409 after leaving), only when the
current game is over. Same seats,
`game_no + 1`, new seed and (for `random`) a new kingdom. People who had left become normal
bots.

### `POST /api/rooms/{code}/leave` (`app.py:200-207`)

`Room.leave`, then deletes the room if nobody is left and no game exists. Mid-game the seat
is marked `left` and a bot plays it at once; `act` and `rematch` with that token get 409
until the person joins again with it. A room with a
game whose people all left is removed by the sweeper 60 s later.

## Long-poll client contract

Send `since` = the last `version` you rendered. The server answers at once when the version
differs, else after up to `PASSPORT_POLL` seconds with the same version (render it anyway; it's
harmless). Ignore responses with an older version than you have (POST responses and poll
responses can cross). Keep polling while the page is open: that is also what keeps your seat
from counting as away.
