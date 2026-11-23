# Gotchas and how to extend

## Two bugs found while writing these notes (both fixed, with regression tests)

1. **`GET api/rooms` could 500 from the threadpool.** `list_rooms` was a plain `def`, so
   FastAPI ran it in a worker thread; its `rooms.sweep()` called `Room.ensure_driver()`, whose
   `asyncio.get_running_loop()` raised whenever a bot needed to move and no driver task was
   alive. Fixed: `list_rooms` is `async def` (the sweep runs on the event loop, and no longer
   mutates `rooms.rooms` from a thread), and `ensure_driver()` returns quietly off the loop.
   Test: `test_lobby_list_with_a_bot_waiting_and_no_driver`.
2. **Leaving mid-game was undone by the page's own long-poll.** `POST leave` set `left` and
   bumped, which woke the page's in-flight state request, whose handler called `seen()`,
   which cleared `left`. Fixed: `seen()` only refreshes the last-request time; only
   `Room.rejoin()` (`POST join` with the token) clears `left`. A seat that has left can't
   `act` or `rematch` (409). So the seat goes to a bot at once, the room can count as empty,
   and the next game makes that seat a bot for good. Test:
   `test_leave_sticks_while_the_page_is_polling` (holds a poll open in a thread).

## Other gotchas (as the code stands)

Engine:
- **Jet Lag counts as a pile for the three-pile ending** (it's in `supply`). With 2 players
  it has only 10 cards, so a Red-Eye-heavy game can end on piles sooner than expected. This is
  the rule (the genre's convention); the README and the page's "How to play" now say so.
- **Souvenir Shop and Seat Upgrade can gain Jet Lag** (any supply pile in cost range), and it
  can be bought (cost 0). Also the rule, also written down in the README and "How to play".
- **Playing a currency in the action phase ends your action phase** (phase -> buy), and so
  does buying with coins from actions (the engine accepts a `buy` in the action phase). The
  page only offers "Buy for N" in the buy phase (`openCard`, like the market's "can buy"
  outline); in the action phase the card sheet says to end your actions first.
- **Decisions with one possible answer never appear** (`Decision.auto`). A yesno is never
  automatic, so Travel Insurance always asks, even against harmless attacks.
- The end check runs only at the end of a turn; Visas running out mid-turn still lets the
  turn finish (by design, matches the README).
- `view()` sends the last 80 log lines; the engine keeps 200.
- Every player's VP total is in the view, including Scrapbook, which depends on how many
  distinct cards they own. The code treats that as public.

Rooms / API:
- **No persistence.** A container restart drops every room; the page then gets 404 and
  goes home ("That game has ended.").
- **`touched` only moves on bumps.** A lobby nobody changes is forgotten after 2 h even
  while polled. The empty-room rule (no person who hasn't left, 60 s idle) is the other way
  rooms go.
- **Watching is open** (by design, documented in the README and the API docstring): `state`
  with no or a wrong token returns the public view of any room whose code you know (no hand,
  no private decision data).
- **Taken-over people always get a normal bot**, whatever bot level the room used.
- **Removing a bot after a game drops the finished game** (`game = None`), which is also the
  only way to let new people join a room that has played.
- `bot_delay()` reads the env on every call; the other env vars are read at import (tests
  patch the module globals `rooms.TAKEOVER_SECONDS`, `rooms.MAX_ROOMS`, `app.POLL_SECONDS`).
- Single process only: the long-poll events and driver tasks live in one event loop.

Page:
- The phase bar has Action and Buy only: the engine has no `cleanup` phase (clean-up is
  part of `_end_turn`), so a "Clean up" step could never light and was removed.
- One bot level for all bots in "Play vs bots"; mixed levels need Host + lobby.
- Two seats in one browser profile share `passport.room`, so test multiplayer with separate
  profiles or private windows.
- The relative-URL test is strict: no `src="/…"`, `href="/…"`, `"/api/` or `` `/api/ ``, and no
  `http://`/`https://` anywhere in the HTML.

## How to extend

### Add a kingdom card
1. Add `_c(id, name, cost, types, text, plus=..., coin=..., vp=...)` to `KINGDOM` in
   `cards.py`. Own name and text, travel theme; check it isn't another game's card.
2. If `plus` isn't enough, write `eff_<id>(g, pi)` in `engine.py` and register it in
   `EFFECTS`. Ask with `yield g.ask_select/ask_gain/ask_yesno/ask_choose(...)`; give a
   `public` text; log with `g.say` without naming hidden cards; move cards only through the
   helpers or lists so counts are conserved. Attacks: `yield from g.attack(pi, id, hit)`.
3. Bots: add it to `RATING`, `PLAY_ORDER` (and `NON_TERMINAL` if it gives +actions), and to
   `answer()` if it asks something the generic attack rule can't handle. Otherwise
   `test_every_bot_answers_every_decision_legally` fails (it forbids the random fallback).
4. Update `test_card_set` (it asserts 24 kingdom ids and 31 cards), README's card table, and
   presets if wanted (each preset must stay 10 distinct kingdom ids).
5. Add a rule test, run the full script, and re-check the two bot strength tests.
6. The page needs nothing: it draws any card from `api/cards`. A new main type would need
   CSS (`t-<type>`), a glyph (`g-<type>`) and `mainType()`.

### Add a decision kind
Add it to `Decision.auto`, `validate`, `to_dict` if it has new fields, `random_answer`,
`Bot.answer`, and `openDecision` + the click handler in the page.

### Add a lobby option or room setting
Room field + setter that raises `RoomError(409)` while a game runs and bumps; a new `op` in
`app.lobby` (host-only unless there's a reason); include it in `Room.view`; render it in
`renderLobby`. Add an API test.

### Add an env var
Read it at the top of the module, document it in `app.yaml` `env:` and the README settings
table, and set it in the platform `compose.yaml` only if the box needs a non-default.

### Before shipping
Tests pass; play it in the platform dev stack at phone size (and two browser profiles for
multiplayer); retake `docs/screenshots/` if the UI changed; mark anything not verified on
hardware as *untested*; ask before pushing a `v*` tag.
