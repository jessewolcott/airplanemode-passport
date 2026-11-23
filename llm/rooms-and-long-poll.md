# Rooms, seats and long-polling (`rooms.py`)

Everything here is in memory, in one process. Shape shared with Gate Change (decision 0016).

## Objects

- **`Seat`** (`rooms.py:59-67`): `name`, `token` (`None` for bots), `level` (bot level, or
  `None` for a person), `seen` (monotonic time of the person's last request), `left`.
  `human` = has a token.
- **`Room`** (`rooms.py:70-323`): `code`, `seats` (list, index = player index in the game),
  `host` (the host's token), `kingdom` (preset id or `"random"`, default `"first"`), `game`,
  `games_played`, `bots` (`{seat index: Bot}`, built lazily), `version`, `_changed`
  (`asyncio.Event`), `touched`, `task` (the bot driver).
- **`Rooms`** (`rooms.py:326-368`): `rooms` dict by code; `create`, `get`, `open_lobbies`,
  `sweep`.
- **`RoomError(status, message)`**: `app.py` turns it into `{"error": message}` with that
  status.

## Codes, tokens, names

- Code: 4 letters from `CODE_ALPHABET` (no I, L, O), unique among live rooms. `Rooms.get`
  upper-cases, so codes are case-insensitive in URLs.
- Token: `secrets.token_urlsafe(18)` per person, compared with `secrets.compare_digest`.
  It's the only credential: whoever holds it is that seat. The page keeps it in
  `localStorage["passport.room"]`.
- Names: `clean_name` collapses whitespace, cuts to 20 characters, drops non-printables;
  empty becomes "Traveller N" for a new person or the old name on rename. HTML is not
  stripped (a test keeps "Alice <b>"); the page escapes everything.

## Versions and long-polling

- `bump()` (`rooms.py:85-89`): `version += 1`, `touched = now`, swap in a fresh
  `asyncio.Event` and set the old one, which wakes every waiter at once.
- `wait(since, timeout)` (`rooms.py:91-98`): returns at once if `version != since`, else waits
  on the current event up to `timeout` (`PASSPORT_POLL`, 20 s).
- Every state change bumps: seat changes, lobby ops, start, each move (person or bot), a
  person coming back. `version` starts at 1, so `since=0` always returns immediately.
- Polls themselves never bump and never update `touched`.

## Lobby rules

| Operation | Who | Allowed when | Notes |
|---|---|---|---|
| `add_person` (create/join) | anyone | `game is None`, < 4 seats | first person becomes host |
| `add_bot(level)` | host | no game running (over is fine), < 4 seats | first unused `BOT_NAMES` entry |
| `remove_seat(i)` | host | no game running | bots only; **also sets `game = None`** (back to lobby state) |
| `set_bot_level(i, level)` | host | no game running | bots only |
| `set_kingdom(choice)` | host | no game running | must be in `KINGDOM_CHOICES` |
| `rename(i, name)` | any seated person | any time | |
| `start(seed=None)` | host (`/start`) or anyone seated (`/rematch`, only after game over) | no game running, 2-4 seats | see below |

`start()` (`rooms.py:232-247`): people who had left (`left=True`) become bots for good
(`token=None, level="normal"`); a 32-bit seed is drawn; the kingdom comes from
`pick_kingdom(self.kingdom, Random(seed))`; `bots` is reset; `games_played += 1`; bump;
`ensure_driver()`.

After a game is over, the room keeps `game` (state `"over"`) so the results stay visible.
New people can't join then (`add_person` needs `game is None`) unless the host removes a bot,
which drops the finished game and returns the room to the lobby.

## Leaving and the host

`leave(token)` (`rooms.py:216-226`): in the lobby the seat is deleted; once a game exists the
seat is only marked `left`. If the host leaves, the host role passes to the first remaining
person who hasn't left (or `None`). The `/leave` endpoint then deletes the room if nobody
is left and there's no game.

`seen(token)` is called on state polls and acts: it only refreshes `seen`. It never clears
`left`, because the page's own long-poll is still in flight when it posts leave and used to
take the seat straight back. Only `rejoin(token)` (from `POST join` with the token) clears
`left`, bumps, and makes the person host if the room has none. `need_present(token)` (used
by `act` and `/rematch`) refuses a seat that has left with 409.

## Away, takeover and pausing

- `away(i)`: a person who `left`, or whose last request is older than `TAKEOVER_SECONDS`
  (60). Bots are never away. The page long-polls continuously, so an open page refreshes
  `seen` at least every ~20 s; a locked phone or closed tab stops it.
- `bot_controls(i)`: seat is a bot, or a person who is away.
- `anyone_here()`: at least one person who isn't away.
- The view's `seats[].away` is only true while a game is running.

A taken-over person is played by `bot_for(i)`, which builds `Bot(seat.level or "normal")`:
always a normal bot. An idle (not left) person is no longer away after any request with
their token, and the driver stops playing for them at its next check. A person who left
must `join` again with their token.

## The bot driver (`ensure_driver` / `_drive`, `rooms.py:266-302`)

- `ensure_driver()` starts `_drive()` as an asyncio task when: there's a running game, the
  seat it waits on (`game.waiting_on()`: pending decision owner, else current player) is
  bot-controlled, someone is here, and no task is alive. It's called after start, each move,
  leave, every state poll, and every sweep (every 5 s).
- `_drive()` loops: stop if the game is over, the waited-on seat isn't bot-controlled, or
  nobody is here (**this is the pause**: bots never play out a game alone). Otherwise sleep
  `bot_delay()`, re-check that the same game still waits on the same seat, then
  `g.act(who, bot.move(g, who))` and bump.
- If a bot move raises, it logs and tries a fallback: a `random_answer` to the pending
  decision, or `end_phase`. If that fails too the task ends (the next `ensure_driver` call
  will start a new one).
- Bot moves call `g.act` directly (not `Room.act`), so they don't need a token.

## Room limits and forgetting

- `Rooms.create()` sweeps first; at 200 rooms it evicts the least recently touched one only
  if it was untouched for 10+ minutes, else `503 "Too many games on this hotspot right now."`
- `sweep()` (`rooms.py:359-368`) deletes a room when `now - touched > FORGET_AFTER` (2 h) or
  when it's `empty()` (no person who hasn't left) and untouched for 60 s, cancelling its
  task. For running games it calls `ensure_driver()`.
- `touched` moves only on `bump()`, so a lobby nobody changes is forgotten after 2 h even
  if people keep polling it.
- `open_lobbies()`: rooms with no game, at least one person, fewer than 4 seats; newest
  first; at most 20; each `{code, host (name), players, kingdom}`.

## What a person gets (`Room.view`, `rooms.py:305-323`)

```
{code, version, state: "lobby"|"playing"|"over", game_no, you (seat index or null),
 host (am I host), kingdom_choice,
 seats: [{name, bot (level or null), away, left, host, you}],
 game: Game.view(you) or null}
```

A state request without a valid token still gets the public view (`you: null`, no hand):
anyone who knows a code can watch a table's public state.
