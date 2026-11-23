# Engine and cards (`engine.py`, `cards.py`)

## Card data (`cards.py`)

`Card` (`cards.py:12-25`, frozen dataclass): `id`, `name`, `cost`, `types` (tuple),
`text`, `plus` (dict: `cards`/`actions`/`buys`/`coins`, applied first when an action is
played), `coin` (currency value), `vp` (fixed VP; Scrapbook is variable), `kingdom` (False
for base cards). `is_(t)` tests a type. Types: `currency`, `victory`, `action`, `attack`,
`reaction`, `negative`.

Base piles (`BASE`, `cards.py:32-40`): `coins` (Coins, 0, +1), `banknotes` (3, +2),
`goldcard` (Gold Card, 6, +3), `postcard` (2, 1 VP), `stamp` (Passport Stamp, 5, 3 VP),
`visa` (8, 6 VP, ends the game when its pile is empty), `jetlag` (Jet Lag, 0, -1 VP,
type `negative`).

Kingdom (`KINGDOM`, `cards.py:42-95`). Card texts are in README.md; this is the id map and
what implements each one:

| id | Name | Cost | Types | `plus` | Effect fn |
|---|---|---|---|---|---|
| `windowseat` | Window Seat | 2 | action | 1 card, 1 action | `eff_windowseat` (yesno, `show=[top]`) |
| `packingcubes` | Packing Cubes | 2 | action | 1 action | `eff_packingcubes` (select 0-2) |
| `customs` | Customs | 2 | action | | `eff_customs` (select 0-3, trash) |
| `insurance` | Travel Insurance | 2 | action reaction | 1 card, 1 action | none; checked in `Game.attack` |
| `hostel` | Hostel | 3 | action | 1 card, 2 actions | |
| `dutyfree` | Duty Free | 3 | action | 1 buy, 2 coins | |
| `souvenirshop` | Souvenir Shop | 3 | action | | `eff_souvenirshop` (gain <= 4) |
| `hitchhike` | Hitchhike | 3 | action | 2 actions | `eff_hitchhike` (never asks) |
| `lightpacker` | Light Packer | 3 | action | 1 card, 1 action | `eff_lightpacker` (select 0-1, trash) |
| `overbooked` | Overbooked | 4 | action attack | 2 coins | `eff_overbooked` |
| `delayed` | Delayed Flight | 4 | action attack | 2 cards | `eff_delayed` |
| `connecting` | Connecting Flight | 4 | action | | `eff_connecting` |
| `exchange` | Currency Exchange | 4 | action | 1 buy | `eff_exchange` (select 0-2 Coins) |
| `upgrade` | Seat Upgrade | 4 | action | | `eff_upgrade` (select 1, then gain <= cost+3) |
| `streetfood` | Street Food | 4 | action | 1 card, 1 action, 1 coin | |
| `backpack` | Backpack | 4 | action | | `eff_backpack` (draw to 6) |
| `scrapbook` | Scrapbook | 4 | victory | | VP via `engine.card_vp` |
| `guidebook` | Guidebook | 5 | action | | `eff_guidebook` (choose 2 of 4) |
| `concierge` | Concierge | 5 | action | 2 cards, 1 action | `eff_concierge` (select 0-1) |
| `redeye` | Red-Eye | 5 | action attack | 1 card, 1 coin | `eff_redeye` |
| `lostluggage` | Lost Luggage | 5 | action attack | 2 coins | `eff_lostluggage` |
| `grouptour` | Group Tour | 5 | action | 3 cards, 1 buy | `eff_grouptour` |
| `milescard` | Miles Card | 5 | currency | 1 buy | none: `coin=2`, buy added in `_play_money` |
| `lounge` | First Class Lounge | 6 | action | 3 cards, 1 action | |

Presets (`PRESETS`, `cards.py:102-112`): `first` "First trip" (no attacks), `business`
"Business class", `backpacker` "Backpacker". `KINGDOM_CHOICES` = the three + `"random"`.
`describe()` (`cards.py:131`) returns `{cards: {id: {...}}, presets: [... + random]}` for the
page; the random preset has `cards: []`.

Pile sizes (`pile_size`, `cards.py:116-128`): Coins `60 - 7*players` (what's left after the
starting decks), Banknotes 40, Gold Card 30, Jet Lag `10 * (players - 1)`, every victory card
(including Scrapbook) 8 with 2 players or 12 with 3-4, every other kingdom card 10.

## The game object (`engine.py:144-462`)

`Game(names, kingdom, seed=None, round_limit=60)` needs 2-4 names and 10 distinct kingdom
ids (else `ValueError`). The kingdom is sorted by (cost, name). `supply` holds the seven
base piles plus the ten kingdom piles. Each player gets 7 Coins + 3 Postcards, shuffled, and
draws 5. `total_cards` records the starting count for conservation checks. The first player
is `rng.randrange(n)`.

`Player` holds `deck` (top = end of list), `hand`, `discard`, `play`, `turns`. `owned()` is
all four lists together.

Turn state on the game: `current`, `phase` (`"action"`, `"buy"`, `"over"`), `actions`,
`buys`, `coins`, `pending` (a `Decision` or `None`), `_flow` (the running effect generator),
`seq` (decision id counter), `over`, `result`.

### Intents (`Game.act`, `engine.py:355-425`)

| Intent | Rules |
|---|---|
| `{"type": "answer", "id": n, "value": v}` | Only the pending decision's owner, with the current `id` ("That question has moved on."). Validated, then sent into the generator |
| `{"type": "play", "card": id}` | Must be in hand. Action: action phase and `actions >= 1`; moved to `play`, logged, effect run. Currency: switches to buy phase and plays just that card. Anything else: "can't be played" |
| `{"type": "play_money"}` | Plays every currency in hand; switches to buy phase |
| `{"type": "buy", "card": id}` | In supply, pile > 0, `buys >= 1`, cost <= coins. Sets phase to buy (so buying straight from the action phase is allowed if you have coins) |
| `{"type": "end_phase"}` | Action -> buy; buy -> `_end_turn()` |

While a decision is pending nobody else can act ("Waiting for X to choose."). Out of turn:
"It's not your turn." Every message is a human sentence; the API returns it as `error`.

After each intent, `_settle()` (`engine.py:303-312`) moves the game along: the action phase
is skipped when `actions == 0` or there's no action card in hand, and the buy phase ends the
turn by itself when `buys == 0` (so the last buy ends the turn). There is no "cleanup" phase
value: clean-up happens inside `_end_turn`.

`_end_turn` (`engine.py:320-331`): discard hand + play, draw 5, then check the end:
**Visa pile empty, or 3+ empty piles (any supply pile counts, Jet Lag included), or every
player has `turns >= round_limit`**. The check only runs at the end of a turn.

`_finish` (`engine.py:333-352`): per player `vp` (`score(owned)`), `turns`, `cards`, and a
`breakdown` of VP cards. Winners: highest VP, then fewest turns among those; any remaining
tie is shared. `result = {winners, ranking (sorted), reason}`; a final log line of kind `end`.

### Decisions (`engine.py:63-121`)

Effects are generators. When one needs a choice it yields a `Decision(player, kind, card,
prompt, options, min, max, public, show)`. The owner may be an attack's victim, not the
current player. `_resume` (`engine.py:287-301`) drives the generator: if `dec.auto()` has the
only possible answer it is sent straight back and never shown; otherwise the decision gets
the next `seq` id and becomes `game.pending`.

| Kind | Options | Answer | Auto-answered when |
|---|---|---|---|
| `select` | multiset of card ids (usually the hand) | list of `min..max` ids, multiset-subset of options | `max == 0`, or `min == max == len(options)`, or `min == max` and all options identical |
| `gain` | supply piles with cards left and cost <= limit, priciest first | one card id | no options (answer `None`) or exactly one |
| `yesno` | none (`show` may list cards) | `true`/`false` (strict bool) | never |
| `choose` | `[{id, label}]` | exactly `min` distinct ids | `len(options) <= min` |

`ask_select` clamps `max` to the number of options and `min` to `max`.
`to_dict(viewer)` (`engine.py:116-121`) gives everyone `id, player, card, kind, public`
("is packing", "may show Travel Insurance"...), and only the owner `prompt, options, min,
max, show`.

`attack(pi, card, hit)` (`engine.py:251-266`): for each other player in turn order, if they
hold Travel Insurance they get a yesno ("Show Travel Insurance to be unaffected?"); yes logs
it and skips them. Otherwise `hit(victim)` runs; it may be a generator or a plain function.

`_resolve(pi, cid)` applies `plus` then runs `EFFECTS[cid]` (generator or plain). Connecting
Flight composes with `yield from g._resolve(...)` twice, so a whole card is one generator.

### Effect details worth knowing

- **Window Seat**: peeks the top card (reshuffling the discard in if needed); only the owner
  sees it via `show`. Discarding it logs its name (it's now public on the discard).
- **Packing Cubes / Concierge**: discard for +1 coin each; logged by name.
- **Customs / Light Packer**: trash from hand; logged.
- **Souvenir Shop / Seat Upgrade gains**: `ask_gain` offers *every* supply pile in range,
  base piles included, so Jet Lag (cost 0) is a legal gain. Bots value it at -5.
- **Hitchhike**: reveals the top card; action or currency goes to hand, else discard; logged.
- **Overbooked**: victims with more than 4 cards select exactly `len(hand) - 4`; logged.
- **Delayed Flight**: victims with 4+ cards put one on their deck; the log deliberately says
  only "puts a card on top of their deck" (a test checks the card isn't named).
- **Connecting Flight**: select 0-1 from the action cards in hand (it is already in play);
  the chosen card goes to `play` once and resolves twice (plus and effect each time).
- **Currency Exchange**: options are only the Coins in hand; +2 coins per trashed Coin.
- **Seat Upgrade**: no-op with an empty hand; otherwise trash exactly one, gain up to cost+3.
- **Backpack**: draws one at a time until 6 in hand or the deck and discard are empty.
- **Guidebook**: `GUIDE_OPTIONS` cards/actions/coins/buy, choose 2 different.
- **Red-Eye**: each unprotected victim gains a Jet Lag if any are left (silently none
  otherwise).
- **Lost Luggage**: reveals up to 2; trashes the priciest currency other than Coins
  (Banknotes, Gold Card, Miles Card), discards the rest; revealed cards are logged.
- **Group Tour**: every other player draws 1; not logged (draws are hidden).

### Scoring (`engine.py:134-141`)

`card_vp`: Scrapbook = `len(set(owned)) // 4` (distinct card names you own, itself included),
others `CARDS[c].vp`. `score(owned)` sums VP cards (Jet Lag -1). The game view shows every
player's live `vp`; the code treats this as public since every gain is in the log.

### Log

`say(player, text, kind)` appends `{n, p, t, k}`; `k` is `move`, `turn` ("Turn 3", one per
turn start) or `end`; `p` is `None` for the end line. Only the last 200 are kept.

### What each player sees (`Game.view`, `engine.py:437-462`)

Public: `kingdom`, `supply`, `trash` (summary text) and `trash_count`, `current`, `phase`,
`actions`, `buys`, `coins`, `in_play` (the current player's played cards), `players[]`
(`name, hand` (count), `deck, discard` (counts), `discard_top, owned, vp, turns`),
`pending` (filtered as above), `log` (last 80), `over`, `result`, `round_limit`.
Only for a seated viewer: `hand` (list) and `deck_list` (summary of all their owned cards).
`view(None)` has neither.

## Invariants the tests hold the engine to

- `count_cards() == total_cards` after every move in fuzzing and at the end of 300 bot games.
- Bot-only games always finish (within 20 000 steps).
- Random legal (and some illegal) moves never break the engine (`IllegalMove` only).
