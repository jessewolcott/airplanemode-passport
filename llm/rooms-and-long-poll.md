# Rooms, seats and long-polling (`rooms.py` on `amrooms`)

Everything here is in memory, in one process.

`rooms.py` is the game on top of **`amrooms`**, the platform's Rooms SDK (platform decision
0022, shared with Gate Change; source and README in
[`../../airplanemode/sdk/rooms/`](../../airplanemode/sdk/rooms/README.md)). The SDK owns
codes, seats, tokens, host, join/leave/rejoin, presence, long-poll, the driver loop and the
room registry. `rooms.py` adds the card set choice, starting games, moves, the per-seat view
and what the driver does. Read the SDK README for its rules; this file says what Passport
uses and changes.

| Line | What |
|---|---|
| `:27` | `bot_delay()` |
| `:39` | `class PassRoom(Room)` and its settings |
| `:62`-`:78` | seats: `add_person`, `bot_controls`, `remove_seat`, `set_kingdom` |
| `:81` | `start()` |
| `:97` | `act(token, intent)` |
| `:114` | `plan(now)`: the driver hook |
| `:126` | `_bot_move()` |
| `:147` | `view(token)` |
| `:168` | `lobby_summary()` |
| `:173` | `make_rooms()`: the registry `app.py` uses |

## What comes from amrooms

- **`Seat`**: `kind` (`human`/`bot`), `name`, `avatar` (who's who emoji or None), `person`
  (who's who id or None), `level` (bot level, None for a person), `token`
  (`secrets.token_urlsafe(18)` for people, None for bots), `seen` (monotonic time of the last
  request), `left`, `auto` (unused by Passport). `human` = `kind == "human"`.
  `make_bot("normal")` turns a person's seat into a bot.
- **`RoomError(status, message)`**: `app.py` turns it into `{"error": message}` with that
  status.
- **Codes**: 4 letters without I, L, O, unique among live rooms. `Rooms.get` upper-cases, so
  codes are case-insensitive; unknown or closed -> `RoomError(404)` "No game with that code
  (it may have ended)."
- **Tokens**: compared with `secrets.compare_digest`. Whoever holds one is that seat. The page
  keeps it in `localStorage["passport.room"]`.
- **Names**: `clean_name` collapses whitespace, cuts to 20 characters, drops non-printables;
  empty becomes the who's who name, else "Traveller" (the old name on rename). Names are made
  unique per room, case-insensitive ("Traveller 2", "Sam 2"), also on rename. HTML is not
  stripped (a test keeps "Alice <b>"); the page escapes everything.
- **`join(name, token, who)`**: your seat back if the token is yours or `who["id"]` matches a
  seat's `person` (another browser on the same phone, no token needed). Either way it clears
  `left`, refreshes `seen`, takes the who's who id and emoji, renames if a name (or a who's
  who name) is given, makes you host if the room has none, and calls `changed()`. Works
  mid-game and after leaving. Otherwise a new seat, lobby only (`game is None`):
  `RoomError(409)` "That game has already started." / "That table is full (4 seats)." The
  first person becomes host.
- **`host`** is a `Seat` (not a token). `is_host(i)`, `need_host(token)` (403 "Only the host
  can do that."), `need_seat(token)` (403 "You're not at this table."), `need_present(token)`
  (409 "You left this game. Join it again to play.": used by `act` and `/rematch`).
- **`leave(token)`**: in the lobby the seat is deleted; once a game exists the seat is only
  marked `left`. If the host leaves, the host role passes to the first remaining person who
  hasn't left (or None).
- **`seen(token)`**: refreshes the seat's `seen`, and the room's `touched` only for a seated
  person who hasn't left. It never clears
  `left`, because the page's own long-poll is still in flight when it posts leave. Only `join`
  (with the token, or by who's who) takes a left seat back.
- **Presence**: `away(i)`: a person who `left`, or whose last request is older than
  `away_after`. Bots are never away. `anyone_here()`: at least one person who isn't away.
  `empty()`: no person who hasn't left.
- **Lobby helpers**: `add_bot(level)` (409 "The game has started." while `playing()`; 400
  "Unknown difficulty."; 409 "The table is full (4 seats)."; a random unused name from
  `PassRoom.bot_names`), `set_bot_level(i, level)` (400 "No such seat." / "Only bots have a
  difficulty."; 409 while playing), `rename(i, name)`.
- **`changed()` / `wait(since, timeout)`**: `version += 1`, `touched = changed_at = now`,
  swap in a fresh `asyncio.Event` and set the old one (wakes every waiter at once). `wait`
  returns at once if `version != since`, else waits up to `timeout` (`app.POLL_SECONDS`,
  `PASSPORT_POLL`, 20 s).
- **The driver** (`ensure_driver`): one asyncio task per room with a game, started by
  `start()`, every state poll and every sweep; it returns quietly off the event loop (the next
  sweep starts it). The loop asks `plan(now)` for `(when, action)`, runs `action()` under
  `room.lock` once due and then `changed()` (unless the action returns False). It re-plans on
  every change and at least every 2 s, and keeps running (napping) while a finished game is
  shown. Errors are logged and it sleeps 1 s.

## `PassRoom`

Class attributes: `max_seats = 4`, `bot_levels = bots.LEVELS`, `bot_names` (Ada ... Juno, 10),
`away_after` (`PASSPORT_TAKEOVER`, 60 s), `poll_wait` (`PASSPORT_POLL`, 20 s; the state
endpoint passes its own `POLL_SECONDS`). Read at import; tests set
`rooms.PassRoom.away_after`.

Extra fields: `kingdom` (preset id or `"random"`, default `"first"`), `games_played`, `bots`
(`{seat index: Bot}`, built lazily), `next_delay` (`bot_delay()`, re-rolled on every
`changed()`), `stuck` (see the driver). Seats are ordered; index = player index in the game.

- `playing()`: a game that isn't over.
- `add_person(name, who)`: `join(name, who=who)` (create uses it).
- `bot_controls(i)`: the seat is a bot, or a person who is away.
- `remove_seat(i)`: the SDK's with `people=False` (bots only: 400 "Only bots can be removed.";
  400 "No such seat."; 409 for the host or while playing), then **`game = None`** (back to the
  lobby state: the old result no longer lines up).
- `set_kingdom(choice)`: must be in `KINGDOM_CHOICES` (400), not while playing (409).
- `start(seed=None)`: 409 while playing; 400 unless 2-4 seats. People who had left become
  bots for good (`make_bot("normal")`); a 32-bit seed is drawn; the kingdom comes from
  `pick_kingdom(self.kingdom, Random(seed))`; `bots` is reset; `games_played += 1`;
  `changed()`; `ensure_driver()`.
- `act(token, intent)`: `need_present`, 409 "The game hasn't started." without a game,
  `Game.act` (an `IllegalMove` becomes `RoomError(400)`), `changed()`.
- `bot_for(i)`: `Bot(seat.level or "normal")`, so a taken-over person always gets a normal bot.

After a game is over, the room keeps `game` (state `"over"`) so the results stay visible.
New people can't join then (`join` needs `game is None` for a new seat) unless the host
removes a bot, which drops the finished game and returns the room to the lobby.

## Lobby rules

| Operation | Who | Allowed when | Notes |
|---|---|---|---|
| `add_person` / `join` (create/join) | anyone | new seat: `game is None`, < 4 seats | first person becomes host; rejoin any time |
| `add_bot(level)` | host | no game running (over is fine), < 4 seats | random unused `bot_names` entry |
| `remove_seat(i)` | host | no game running | bots only; **also sets `game = None`** |
| `set_bot_level(i, level)` | host | no game running | bots only |
| `set_kingdom(choice)` | host | no game running | must be in `KINGDOM_CHOICES` |
| `rename(i, name)` | any seated person | any time | made unique |
| `start(seed=None)` | host (`/start`) or anyone seated (`/rematch`, only after game over) | no game running, 2-4 seats | see above |

## Away, takeover and pausing

The page long-polls continuously, so an open page refreshes `seen` at least every ~20 s; a
locked phone or closed tab stops it. The view's `seats[].away` is only true while a game is
running. An idle (not left) person is no longer away after any request with their token, and
the driver stops playing for them at its next plan. A person who left must `join` again.

## The driver hook (`plan`)

`plan(now)` returns None (nothing to do) when there's no game, it's over, `stuck ==
version`, or **nobody is here** (`not anyone_here(now)`: this is the pause, bots never play
out a game alone). Otherwise, for the seat the game waits on (`game.waiting_on()`: pending
decision owner, else current player):

- bot-controlled: `(changed_at + next_delay, _bot_move)`, i.e. `bot_delay()` (0.7-1.2 s)
  after the last change;
- a person: `(seen + away_after + 0.01, lambda: False)`, a wake-up for the moment they'd
  count as away, in case they never come back (False: nothing changed, no version bump).

`_bot_move(g, who)` re-checks that the same game still waits on the same bot-controlled seat
(else returns False), then `g.act(who, bot.move(g, who))`. If that raises, it logs and tries
a fallback: a `random_answer` to the pending decision, or `end_phase`. If the fallback fails
too, it sets `stuck = version` and returns False, so the driver doesn't spin on the same
position; any later change (a person's move, a rejoin) clears the guard. Bot moves call
`g.act` directly (not `Room.act`), so they don't need a token.

## Room limits and forgetting (`make_rooms()` -> `amrooms.Rooms`)

`Rooms(PassRoom, max_rooms=200, idle=PASSPORT_ROOM_TTL (7200))`. Tests change
`passport.rooms.max_rooms` / `.idle` on the instance.

- `create()` sweeps first. At 200 rooms it closes and drops the least recently touched room
  that is closed, a lobby or a finished game; it only refuses (`503 "Every table on this
  hotspot (200) has a game going. Try again when one finishes."`) when every room is a game
  in progress. `app.create_room` drops a half-made room with `rooms.discard(room)` if a
  `RoomError` happens while setting it up.
- `sweep()` forgets closed rooms, rooms untouched for `idle` (2 h), empty lobbies at once,
  and empty games (no person who hasn't left) untouched for 60 s (`empty_grace`, so the last
  person can still come back; bots don't move while nobody is here, so this is 60 s after the
  last person left or the last change). It starts drivers for the rest. It runs every 5 s
  (`sweep_forever` in the lifespan), on `create`, on the lobby list, and after every `/leave`.
- `touched` moves on every change and on `seen()` from a seated person who hasn't left; a
  spectator's poll or a leaver's last poll keeps nothing alive.
- `open_lobbies(20)`: rooms with no game, not closed, fewer than 4 seats and at least one
  person; newest first; at most 20. `app.list_rooms` maps each with `lobby_summary()`:
  `{code, host (name or "?"), players, kingdom}`.

## What a person gets (`PassRoom.view`, `:147`)

```
{code, version, state: "lobby"|"playing"|"over", game_no, you (seat index or null),
 host (am I host), kingdom_choice,
 seats: [{name, avatar, bot (level or null), away, left, host, you}],
 game: Game.view(you) or null}
```

`avatar` is the who's who emoji or null. A state request without a valid token still gets
the public view (`you: null`, no hand): anyone who knows a code can watch a table's public
state.
