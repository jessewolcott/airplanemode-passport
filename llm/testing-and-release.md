# Testing and release

## The test script (`tests/test_passport.py`)

A plain script, no pytest: `python tests/test_passport.py` runs every `test_*` function in
file order inside one `TestClient(passport.app)` (so the lifespan sweeper runs), prints
`ok`/`FAIL` with timings, and exits 1 on any failure. It sets `PASSPORT_BOT_DELAY=0` and
`PASSPORT_POLL=1` before importing the app. API tests share one app and its `rooms`, so
order matters a little (e.g. the room-cap test sets `MAX_ROOMS` to the current count).
The whole run took about 4 s locally on 2026-11-22 (all passed).

Helpers: `new_game(n, kingdom, seed)`; `table(hand, ...)` forges an action phase for player 0
with an exact hand (cards from nowhere: fine for rule tests, not for conservation);
`must_fail(fn, ..., match=)` expects `IllegalMove`; `ans(g, who, value)` answers the pending
decision; `post(path, data, code)`; `finish_with_bots(code, tokens)` plays people's seats
through the API with normal-bot choices.

| Group | Tests |
|---|---|
| Cards | `test_card_set` (24 kingdom ids, presets valid, cost spread, banned names from the other game, 31 cards in `describe`) |
| Setup | `test_setup_and_pile_sizes`, `test_seeded_games_repeat` |
| Turn flow | `test_turn_flow_and_illegal_moves`, `test_action_phase_skips_when_nothing_to_play`, `test_actions_are_limited`, `test_buying_extra_cards_with_buys` |
| Effects | `test_simple_plus_cards`, `test_customs_trashes_up_to_three`, `test_attack_decision_belongs_to_the_victim`, `test_travel_insurance_blocks`, `test_jet_lag_runs_out_quietly`, two Connecting Flight tests, two Seat Upgrade tests, `test_lost_luggage_and_delayed_flight`, `test_window_seat_hitchhike_cubes_concierge_exchange`, `test_souvenir_shop_group_tour_miles_card`, `test_light_packer_and_reshuffle`, `test_scrapbook_scores_by_variety` |
| Ending | `test_game_ends_on_visas_and_three_piles`, `test_ties_go_to_fewer_turns_then_share`, `test_round_limit_ends_stalled_games` |
| Hidden info | `test_views_hide_other_hands`, `test_log_and_public_view_never_leak_hidden_cards` (every step of 40 games, bots plus random moves: each seat's view minus its own hand and decision equals the public view, and every log line names only cards that became public that step or are in play; Hitchhike's kept card and a shown Travel Insurance are the allowed reveals), plus checks inside the attack, Delayed Flight and Window Seat tests |
| Bots | `test_many_bot_games_end_and_conserve_cards` (300 games: every set x 2-4 players x 25 seeds), `test_random_players_never_break_the_engine` (60 fuzz games), `test_every_bot_answers_every_decision_legally` (spies on `random_answer`: no fallback allowed), `test_hard_beats_easy_most_of_the_time` (>= 75 % per set), `test_hard_holds_its_own_against_three_normals` (>= 33 % of 60, fair share 25 %) |
| API | `test_page_and_health` (relative URLs, no `http(s)://`, icon type, 31 cards), `test_posts_need_the_header`, `test_lobby_join_bots_and_start`, `test_solo_game_vs_bots_long_poll` (poll waits ~1 s when nothing moves, returns at once when behind), `test_leaving_hands_the_seat_to_a_bot_and_coming_back_reclaims_it`, `test_leave_sticks_while_the_page_is_polling` (holds a poll open in a thread, like the page), `test_lobby_list_with_a_bot_waiting_and_no_driver`, `test_idle_players_are_covered_then_reclaim` (patches `TAKEOVER_SECONDS`/`POLL_SECONDS`), `test_everyone_away_pauses_the_game`, `test_unknown_rooms_and_room_cap`, `test_lobby_leave_and_forget` |

Not covered: the page's JavaScript (no browser tests), concurrent requests (the test client
is mostly used sequentially; only the leave test holds a poll open in a thread), real phones.

## Running locally

- Python 3.10 venv: `py -3.10 -m venv .venv`, then
  `.venv\Scripts\pip install -r requirements.txt httpx==0.28.1` (httpx is for TestClient).
- `requirements.txt`: `fastapi==0.115.6`, `uvicorn==0.34.0`. Nothing else at runtime.
- Standalone: `docker compose up -d --build` -> http://localhost:8772 (`compose.yaml`:
  project `passport-dev`, port `${PORT:-8772}:8000`, `mem_limit: 64m`). To play several
  seats by hand, use separate browser profiles or private windows (the seat lives in
  `localStorage`).
- In the platform dev stack (from `../airplanemode`):
  `docker compose -f compose.yaml -f compose.dev.yaml up -d --build` -> http://localhost:8080/passport/.
- VS Code tasks: "Standalone: up", "Standalone: down", "Tests: Passport" (default test task).

## Image

`Dockerfile`: `python:3.12-slim`, install requirements, `COPY . .`, add user `passport`
(uid 10001), run as it, `EXPOSE 8000`, `uvicorn app:app --host 0.0.0.0 --port 8000`.
`.dockerignore` excludes `.git .github .vscode .venv tests __pycache__ *.md compose.yaml docs
llm`, so the image holds the `.py` files, `static/`, `app.yaml`, `requirements.txt`,
`Dockerfile`. `.gitattributes` forces LF for `*.sh`, `*.yaml`, `*.yml` and `Dockerfile`.

## CI (`.github/workflows/image.yml`)

On push to `main`, on `v*` tags, and on pull requests: checkout, Python 3.12, install
requirements + httpx, run the test script, then QEMU + buildx and build
`linux/arm64,linux/amd64`. Pushes (not PRs) log in to GHCR and push
`ghcr.io/<owner>/airplanemode-passport` tagged `main` (branch) or `X.Y.Z` (semver from the
tag).

## Releasing

1. Tests pass; play it in the platform dev stack at phone size; retake
   `docs/screenshots/` if the UI changed.
2. **Ask Jesse**, then push `vX.Y.Z`. CI publishes `ghcr.io/jessewolcott/airplanemode-passport:X.Y.Z`.
3. In `../airplanemode`: bump the pinned tag in `compose.yaml` (never `latest`) and the
   status notes. Released so far: 0.1.0.

## Untested (as of 0.1.0)

- Real phones and the real box (NanoPi R6S / GL.iNet); played only in a phone-sized desktop
  Chrome window, against bots.
- Several people on several phones at once (API tests only).
- Memory use under load on the 48 MB GL.iNet limit.
