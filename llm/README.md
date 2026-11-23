# Passport: how it's built (LLM / engineer reference)

Checked-in notes, written from the code (release 0.1.2, 2026-11-22).
They're kept out of the image by `.dockerignore` (it drops both `llm` and `*.md`). If the code
and these notes disagree, the code wins; fix the notes.

## Overview

Passport is an AirplaneMode app: a travel-themed deck-building card game for 2 to 4 players,
each on their own phone on the travel router's hotspot, with server-side bots (easy, normal,
hard) filling empty seats or a whole table for solo play. No internet, no accounts, no model,
no volume: rooms and games live in the memory of one uvicorn process and are lost on restart.

Everyone starts with 7 Coins + 3 Postcards, holds 5 cards a turn, plays actions, buys from a
market of 10 kingdom cards (one of three presets or 10 random of 24) plus money and victory
piles, and the game ends when the Visas or any three piles run out. The server enforces every
rule (`engine.py`, pure and seeded) and sends each player only their own hand. Multiplayer is
one shape shared with Gate Change (decision 0016): a room with a 4-letter code, a secret seat
token kept in the browser, and long-polling (`state?since=<version>`, about 20 s) instead of
WebSockets, so it works through Caddy with no config. A person who leaves is played by a
"normal" bot until they join again with their token; one who goes quiet for a minute counts as
away, and a normal bot moves for them whenever the game waits on their seat; if every person is away the game
pauses. The whole UI is one file, `static/index.html` (~840 lines of HTML/CSS/vanilla JS).

Nothing has been tested on real phones or the real box yet. The page was played in a
phone-sized desktop Chrome window; several people at once is covered only by the API tests.

## Files in this folder

| File | What it covers |
|---|---|
| [architecture.md](architecture.md) | The pieces, request flow, platform wiring, config, process model |
| [engine-and-cards.md](engine-and-cards.md) | `cards.py` data and `engine.py` rules: turn flow, decisions, every card effect, ending, views |
| [rooms-and-long-poll.md](rooms-and-long-poll.md) | `rooms.py`: rooms, seats, tokens, versions, the bot driver, takeover, sweeping |
| [bots.md](bots.md) | `bots.py`: levels, plans, buying, answering, the tuning tables |
| [api.md](api.md) | `app.py`: every endpoint, request/response shapes, errors |
| [frontend-page.md](frontend-page.md) | `static/index.html`: screens, polling, rendering, sheets, local storage |
| [testing-and-release.md](testing-and-release.md) | Tests, dev setups, image, CI, releasing, what's untested |
| [gotchas-and-extending.md](gotchas-and-extending.md) | Non-obvious behaviour, two bugs found while writing these notes (now fixed), how to add things |

## Source map

| Path | Lines | Role |
|---|---|---|
| `cards.py` | 139 | Card data (7 base + 24 kingdom), presets, pile sizes, `describe()` for the page |
| `engine.py` | 665 | The rules: `Game`, `Decision`, card effects (`EFFECTS`) |
| `bots.py` | 379 | `Bot` (easy/normal/hard), `random_answer`, `play_bot_game` |
| `rooms.py` | 347 | `Room`, `Rooms`, `Seat`, `RoomError`, the per-room bot task |
| `app.py` | 217 | FastAPI app, the HTTP API (listed in its docstring), the sweeper task |
| `static/index.html` | 803 | The whole frontend |
| `static/icon.svg`, `docs/icon.svg` | | App icon (same file twice) |
| `tests/test_passport.py` | 723 | Rules, bots, rooms and API tests (plain script, no pytest) |
| `Dockerfile`, `requirements.txt`, `.dockerignore` | | Image (python:3.12-slim, fastapi + uvicorn only) |
| `compose.yaml` | | Standalone dev on http://localhost:8772 |
| `app.yaml` | | AirplaneMode app manifest |
| `.github/workflows/image.yml` | | CI: tests, then multi-arch image to GHCR |
| `.vscode/tasks.json` | | Standalone up/down, run tests |
| `docs/screenshots/` | | `home.png`, `table.png` (README) |
| `CLAUDE.md`, `README.md` | | Rules for Claude; user-facing description and card list |
| `../airplanemode/docs/decisions/0016-games-and-couch.md` | | The design decision (shared with Gate Change, Wayfarer, The Couch) |

## The rules that must not break (from `CLAUDE.md`)

1. **Own name, own cards.** A deck-builder in the genre, not a clone: no other game's name,
   card names, texts or layout. New cards go in `cards.py` (+ an effect in `engine.EFFECTS`
   if `plus` isn't enough) and need a test. `test_card_set` bans the obvious names.
2. **Hidden information.** `Game.view(seat)` returns only that seat's hand and only that
   seat's decision options/prompt; the log never names hidden cards (draws, cards put back on
   the deck). VP totals are public. `test_log_and_public_view_never_leak_hidden_cards` checks
   every step of 40 games.
3. **Card counts are conserved** (supply + trash + all players' cards); bot and fuzz tests
   assert it.
4. Every effect that asks must be a generator; effects or attack hits that never ask may be
   plain functions.
5. The bots' plan tables (`HARD_TERMINALS`, `HARD_NON_TERMINALS`, `ENGINE_PACE`, `TRASHERS`)
   were tuned from bot-vs-bot runs. After changing cards or bots, re-check
   `test_hard_beats_easy_most_of_the_time` and `test_hard_holds_its_own_against_three_normals`.
6. POSTs need `X-Passport: 1`. The page uses relative URLs only (a test checks).
7. Bots never run when every person in the room is away (the game pauses).
8. Releasing = pushing a `vX.Y.Z` tag, which publishes the image: **ask Jesse first.**
