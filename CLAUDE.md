# Passport (AirplaneMode app): notes for Claude

A travel-themed deck-building card game for 2-4 players on the hotspot, with bots. FastAPI
backend, one page in `static/index.html`, everything in memory (no volume, no model).

Part of AirplaneMode: the platform repo is `../airplanemode`. Read its `CLAUDE.md` for the
environment notes and `docs/app-contract.md` for the rules.

## Files

- `cards.py`: card data (24 kingdom cards + 7 base), presets, pile sizes. No behaviour.
- `engine.py`: the rules. Pure (no I/O, no clock), seeded RNG. Card effects are generators that
  `yield` a `Decision` (select / gain / yesno / choose) owned by one player, which may be an
  attack's victim rather than the current player. `Game.act(player, intent)` validates
  everything; decisions with one possible answer are answered automatically.
- `bots.py`: easy / normal / hard. `Bot.move(game, seat)` returns a legal intent. Bots only use
  what their seat can know. Any bot answer that fails validation falls back to a random legal one.
- `rooms.py`: rooms, seats, tokens, long-poll versions, the per-room bot task, takeover.
- `app.py`: the HTTP API (list in its docstring).

## Commands

```powershell
docker compose up -d --build         # standalone dev: http://localhost:8772
python tests/test_passport.py        # rules, bots (hundreds of games), rooms, API; CI runs it
```

Local tests: `.venv` with Python 3.10 (`fastapi==0.115.6 uvicorn==0.34.0 httpx==0.28.1`).

## Rules the code depends on

- **Own name, own cards.** It's a deck-builder in the genre, not a clone: don't add another
  game's name, card names, texts or layout. New cards go in `cards.py` (+ an effect in
  `engine.EFFECTS` if `plus` isn't enough) and need a test in `tests/test_passport.py`.
- **Hidden information:** `Game.view(seat)` returns only that seat's hand and only that seat's
  decision options/prompt; the log never names hidden cards (draws, cards put back on the
  deck). Tests check this. VP totals are public (every gain is in the log).
- **Card counts are conserved** (supply + trash + all players' cards); bot and fuzz tests assert it.
- Every effect that asks must be a generator; effects/attack hits that never ask may be plain
  functions (the engine accepts both).
- The bots' plan tables (`HARD_TERMINALS`, `HARD_NON_TERMINALS`, `ENGINE_PACE`, `TRASHERS`) were
  tuned from bot-vs-bot runs. If you change cards or bots, re-check
  `test_hard_beats_easy_most_of_the_time` and `test_hard_holds_its_own_against_three_normals`.
- POSTs need `X-Passport: 1`. Page: relative URLs only (a test checks).
- Bots never run when every person in the room is away (the game pauses).
- **Leaving sticks:** only `POST join` with the token (`Room.rejoin`) clears `left`. `seen()` (state
  polls, acts) never does: the page's own poll is in flight when it posts leave.
- `llm/`: checked-in notes on how it's built (kept out of the image by `.dockerignore`).

## Untested

- Real phones and the real box (the page was played in a phone-sized desktop Chrome window).
- Several people on several phones at once (covered by API tests, not by hand).

## Releasing

Push a `vX.Y.Z` tag. CI tests, then publishes `ghcr.io/jessewolcott/airplanemode-passport:X.Y.Z`
(arm64 + amd64). **Ask Jesse before pushing a release tag.** Released: 0.1.0, 0.1.1 (leaving sticks, lobby list 500 fixed), 0.1.2 (home button) (pinned in the platform's `compose.yaml`).
