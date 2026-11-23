# The page (`static/index.html`)

One file: CSS (`:10-218`), an inline SVG sprite of glyphs (`:222-232`), three screens
(`#home`, `#lobby`, `#game`), one bottom-sheet container and a toast, and one `<script>`
(`:340-801`) of vanilla JS. No build step, no external resources (a test forbids `http(s)://`
anywhere in the HTML and absolute `src`/`href`).

## State

- `CARDS`, `PRESETS`: from `api/cards` at boot.
- `S` (`:352`): `code`, `token`, `st` (last rendered state), `version`, `pollId` (poll loop
  generation), `sel` (selected hand card), `soloSet`, `picked` (decision picks), `sheet`
  (which sheet is open: `"help"`, `"card"`, `"log"`, `"menu"`, `"over"`, `"dec:<id>"`),
  `overShown`.
- `localStorage` via `store` (prefix `passport.`, every access in try/catch):
  `passport.room` = `{code, token}` (your seat), `passport.name` = your name.

## Boot (`:786-799`)

Fill the name field; load `api/cards` (on failure: toast "Can't reach Passport. Retrying…"
and retry in 3 s); a URL hash `#ABCD` pre-fills the join code; if a room is saved, ask
`state?since=0` and enter it if `you` is set; forget it only on `you: null` or a 404 (any
other error keeps it, and home shows "Back to your game"); otherwise show home.
`visibilitychange` restarts polling when the page becomes visible again (a woken phone), and
so does `pageshow` from the back/forward cache.

Coming back from the AirplaneMode menu is a page load, so boot re-enters the saved room: a
state request with the token marks the person present again and the bots stop covering
their seat (they only did after `TAKEOVER_SECONDS`). Going home isn't Leave (`left` stays
false), so no confirm.

## AirplaneMode home button

`a.am-home` (`href="../"`, app contract item 10) on every screen: fixed at the top right of
`#home` (`.am-home.float`; `.brand` has 52 px right padding so the title clears it, and a
smaller title under 420 px), and the last item of the lobby and game top bars. Gold house on
the panel colour, round like `.iconbtn`, gold focus ring. `z-index: 25` puts it above the
sheet layer (20), so it stays visible and tappable over every sheet, including the decision
and results sheets; sheets are capped at `100dvh - 64px` so they start below it.
`test_page_has_airplanemode_home_on_every_screen` checks each screen has one.

## Home

- Name input (max 20, saved on change).
- "Back to your game" panel when a room is saved -> `join(code, token)`.
- "Play vs bots": segmented 1-3 bots and a level (one level for all bots), card set tiles,
  -> `POST api/rooms {name, bots, kingdom, start: true}`.
- "Play with people here": "Host a game" (`POST api/rooms {name}`), a 4-letter code field +
  Join, and "Games on this hotspot" (`api/rooms`, refreshed every 5 s while home is shown;
  errors ignored). Codes are checked `^[A-Z]{4}$` client-side.
- `join()` reuses the saved token if the code matches the saved room, so rejoining by code
  gets your old seat back.

## Polling (`:430-445`)

`startPolling()` bumps `S.pollId` and loops while it's still current:
`GET api/rooms/{code}/state?token=...&since=S.version`. A response with `you == null` ->
forget the room, toast "You're no longer in that room.", go home. A 404 -> "That game has
ended.", home. Other errors -> wait 2 s and retry. `stopPolling()` just bumps `pollId`; the
in-flight request isn't aborted, its result is dropped.

`render(st)` ignores states older than `S.version`, then draws the lobby or the game.

## Lobby (`renderLobby`, `:455-474`)

Big room code; seats with a coloured initial, "you"/"host" badges; for bots the host gets a
level `<select>` and a remove button, others see "<level> bot". Add-bot buttons and the
start button show only for the host; set tiles are disabled for non-hosts; the set's ten
card names (or "picked at random") are listed. Start is disabled under 2 seats.

## Game (`renderGame`, `:477-547`)

Top to bottom:
- **Top bar**: room code, log, help, menu buttons, then the AirplaneMode home button.
- **Players strip**: name, VP, hand count, deck/discard/owned counts, tags (you, "<level>
  bot", "away · bot playing"); the current player is outlined.
- **Phase bar**: Action / Buy steps and the actions/buys/coins counters. (There was a
  "Clean up" step; the engine has no `cleanup` phase, clean-up happens inside `_end_turn`,
  so it was removed.)
- **Status line**: whose turn, "Waiting for X: <public text>…", "Your choice: <card>", or
  "The trip is over." with a Results button.
- **In play** (current player's played cards) and the trash count.
- **Market**: the 10 kingdom piles, then the 7 base piles, each with its count; empty piles
  greyed; affordable ones outlined when you can buy. Tapping opens a card sheet: on your turn
  in the buy phase a "Buy for N" button (or why not); in the action phase only a note to end
  your actions first (the engine would accept a buy there, but the page keeps to the phases).
- **Recent moves**: last 8 log lines.
- **Dock**: a hint line, your hand (sorted: actions, currency, victory, negative; then by
  cost), "Play all money" and "End actions"/"End turn".

Hand interaction: first tap selects and shows the card text; a second tap plays it if
playable. Moves go through `send(intent)` (`:675-681`), which drops clicks while a request is
in flight, renders the returned state, and on error toasts the message and re-renders.

## Sheets

- **Decision** (`openDecision`, `:598-618`): opens automatically when `pending.player` is you,
  keyed by decision id so it isn't rebuilt on every poll. `select` shows the options as
  cards with a Confirm button labelled with the count ("None" when 0 is allowed); a
  1-of-1 select answers on the first tap. `gain` answers on tap. `yesno` shows `show` cards
  and two buttons ("Show it"/"No" for Travel Insurance). `choose` toggles labelled buttons.
  Decision and results sheets can't be dismissed by tapping outside or Escape.
- **Results** (`openOver`): opened once per `code + game_no`; ranking with VP breakdown
  chips, reason, and Table / Leave / Rematch.
- **Log**: the full log the server sent (last 80), plus "Your cards" (`deck_list`) and the
  trash.
- **Menu**: "Leave the game" (warns a bot will play your seat).
- **Help**: the rules summary; **Card**: a big card with text.

## Card rendering (`cardHTML`, `:376-386`)

One function draws every card: class `t-<main type>` (negative > attack > reaction > victory
> currency > action) picks the colour and glyph, `c-<id>` allows per-card styling (Gold Card,
Miles Card, Visa), victory/negative cards get a stamp with their VP ("?" for Scrapbook).
Variants: `mini`, `hand`, `big`, `compact`. All text goes through `esc()`.

## Layout notes

Sized for phones first (44 px tap targets, safe-area insets, `100dvh`, sticky dock),
wider market and hand cards from 760 px. `prefers-reduced-motion` turns animations off.
Not tried on real phones yet.
