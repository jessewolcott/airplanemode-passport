# Bots (`bots.py`)

```python
bot = Bot("hard", seed)
intent = bot.move(game, seat)   # a legal intent for game.act(), or None if it isn't this seat's move
```

Bots read the live `Game` object but, by design, only use what their seat could know: their
own hand and owned cards, supply counts, the trash, and other players' VP (public, see the
view). They never look at other hands or deck order. Nothing enforces this; it's a code
convention to keep when editing.

## Levels (`LEVELS = ("easy", "normal", "hard")`)

- **easy**: plays a random action from hand; buys a Visa if it can, else (with 3+ coins)
  sometimes a random affordable action (25 % at exactly 5 coins, 15 % otherwise), else Gold
  Card, else Banknotes, else nothing. Answers half of all decisions at random (never the
  Travel Insurance one).
- **normal**: "big money plus". Plan: the best two `MONEY_PARTNERS` in the kingdom (2 copies
  of the first, 1 of the second). Plays actions in `PLAY_ORDER`. Smart buying (below).
- **hard**: reads the kingdom (`_make_plan`, `bots.py:95-122`):
  - a trasher: first of `TRASHERS` (`upgrade`, `lightpacker`) in the kingdom, bought once in
    its first 3 turns;
  - one terminal: the first of `HARD_TERMINALS` present, with its copy count
    (`redeye` 2, `guidebook` 2, `delayed` 2, `grouptour` 1, `lostluggage` 2, `backpack` 1,
    `upgrade` 1, `overbooked` 2, `dutyfree` 1);
  - one non-terminal helper: first of `HARD_NON_TERMINALS` (`lounge` 2, `concierge` 2,
    `streetfood` 2);
  - an engine instead when the terminal is `backpack` or `delayed` and a village
    (`hostel`, `hitchhike`) exists: 3 drawers + 2 villages, `style="engine"`;
  - Travel Insurance (1, or 2 if Red-Eye is in the kingdom) when the kingdom has an attack;
  - the endgame check (won't end the game while losing; ends it when that wins);
  - only plays Seat Upgrade when some card in hand gains 2+ "worth" from it.

Comments in the code say the tables come from bot-vs-bot runs ("bigger engines lost to these
plans"; "Customs and Currency Exchange tested worse for bots"). The tuning scripts aren't in
the repo; only the two strength tests guard the result.

## `move()` (`bots.py:125-151`)

1. Game over -> `None`.
2. A pending decision: if it's ours, `answer()` it, run the answer through
   `d.validate()`; on any exception use `random_answer` instead. Not ours -> `None`.
3. Not our turn -> `None`. Make the plan on first use.
4. Action phase: `pick_action` -> play it, or `end_phase`.
5. Buy phase: any currency in hand -> `play_money` (all at once). Then if buys are left,
   `pick_buy(coins)` -> buy it, else `end_phase`.

`pick_action` (non-easy) walks `PLAY_ORDER` (villages and cantrips first, then terminals),
skipping Connecting Flight with no other action to double, Customs with nothing worth
trashing, and (hard) a weak Seat Upgrade.

## Buying (`pick_buy` / `_smart_buy`, `bots.py:173-248`)

`endgame_left(g)` = Visas left, or, once any pile is empty, the fewest cards that would empty
enough piles to make three, whichever is smaller. `late` below is that number.

`_smart_buy` (normal and hard), first match wins:
1. Visa if affordable.
2. With 6+ coins: Stamp if `late <= 4`; a wanted cost-6+ card still under its count; Gold Card.
3. Stamp if `late <= 5`; Postcard if `late <= 2`.
4. Hard: the trasher, in turns 1-3, if none owned.
5. Wanted actions, priciest first, each capped at `min(count, 1 + deck_size // pace)`
   (`pace` = `ENGINE_PACE` 6 for engines, 11 for money), so copies spread over the deck.
   Engines order villages vs drawers so there's about one village per drawer.
6. Hard: Travel Insurance up to its count, only with 4 coins or fewer.
7. Miles Card at exactly 5 coins; Banknotes; else nothing.

Hard then runs `_endgame_check` (`bots.py:250-272`): if some affordable buy ends the game
(last Visa, or last card of a pile when two are already empty) and leaves it ahead, buy that.
If the chosen buy would end the game while not ahead, swap to the best VP card that doesn't
end it, or buy nothing.

## Answering (`answer`, `bots.py:330-366`)

| Decision | Answer |
|---|---|
| Travel Insurance yesno | always show it |
| Window Seat yesno | discard if junk (victory/negative); hard also discards a Coins |
| other yesno | no |
| Guidebook choose | `actions + cards` if it holds actions and has 0 actions left, else `cards + coins` |
| gain | `pick_buy` restricted to the options, else the best `worth` |
| Customs / Light Packer | `_trash_picks`: Jet Lag always; Postcards if not late (not easy); hard trashes Coins while keeping 7+ money and not late |
| Currency Exchange | as many Coins as allowed |
| Connecting Flight | first in `PLAY_ORDER` other than Connecting |
| Seat Upgrade | `_upgrade_target`: biggest worth gain |
| Packing Cubes / Concierge | junk cards only |
| attack discards / put-backs | lowest `keep_value` cards (junk -1, money by coin, actions rating/2) |

`worth` and `keep_value` use `RATING` (rough "coins per play" per kingdom card) plus fixed
values for money and VP; Jet Lag is -5.

## Helpers

- `random_answer(d, rng)`: a random legal answer for any decision kind. Used as the bot
  fallback, by `Room._drive`'s fallback, and by the fuzz test.
- `play_bot_game(levels, kingdom, seed)`: plays a bot-only game to the end (20 000 step cap)
  and returns the `Game`. Bot seeds are `seed * 31 + i`. Used by the tests and for tuning.

## Bots in rooms

`Room.bot_for(i)` creates one `Bot` per seat lazily with a fresh random seed, so room games
aren't reproducible from the game seed alone. Taken-over people get a normal bot. Bot seats
keep their level across rematches; `Room.start` resets `bots`, so plans are rebuilt for the
new kingdom.
