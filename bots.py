"""Bot players. A bot looks at the game exactly as the engine holds it, but only uses what its
own player could know (its hand, public piles, VP that the log makes public). It never peeks
at other hands or deck order.

    bot = Bot("hard", seed)
    intent = bot.move(game, my_index)     # a legal intent for game.act(), or None if not our move

Levels
  easy    buys the best money it can afford, a Visa at 8, now and then a random action;
          plays actions in random order and answers questions at random-ish.
  normal  "big money plus": a ranked list of good actions (one or two of them), money, and VP
          timed by how close the game is to ending.
  hard    reads the kingdom: picks a money plan around one terminal (an attack when there's a
          good one) plus a non-terminal helper, or a small engine (villages + a drawer) when
          the kingdom has both; buys a trasher early to clear starting cards and Jet Lag, buys
          Travel Insurance against attacks, and won't take the last
          Visa (or empty the third pile) while losing; ends the game when that wins.
          The plan tables come from bot-vs-bot runs (bigger engines lost to these plans).
Every bot answers every decision legally: anything it gets wrong falls back to a random legal
answer (`random_answer`), which the tests also use as a fuzzing player.
"""
import random
from collections import Counter

from cards import CARDS
from engine import score

LEVELS = ("easy", "normal", "hard")

# How much each kingdom card is worth to a bot, roughly in "coins per play".
RATING = {
    "lounge": 8, "redeye": 8, "grouptour": 7, "guidebook": 6, "concierge": 6, "backpack": 6,
    "streetfood": 5, "lostluggage": 5, "overbooked": 5, "milescard": 5, "hostel": 4, "delayed": 4,
    "exchange": 4, "upgrade": 4, "dutyfree": 4, "lightpacker": 4, "connecting": 3,
    "souvenirshop": 3, "hitchhike": 3, "insurance": 3, "customs": 3, "windowseat": 2,
    "packingcubes": 2, "scrapbook": 2,
}
# Best partners for a money deck, best first. (Miles Card is just money for the bots.)
MONEY_PARTNERS = ["redeye", "lounge", "grouptour", "backpack", "lostluggage", "overbooked",
                  "guidebook", "concierge", "delayed", "dutyfree", "streetfood"]
# Hard's money plans: the terminal to build around (with copies), best first, from bot-vs-bot
# runs; and the best non-terminal helper to add.
HARD_TERMINALS = {"redeye": 2, "guidebook": 2, "delayed": 2, "grouptour": 1, "lostluggage": 2,
                  "backpack": 1, "upgrade": 1, "overbooked": 2, "dutyfree": 1}
HARD_NON_TERMINALS = {"lounge": 2, "concierge": 2, "streetfood": 2}
ENGINE_PACE = 6           # engine: one more wanted action per this many cards owned
TRASHERS = ["upgrade", "lightpacker"]     # (Customs and Currency Exchange tested worse for bots)
VILLAGES = ["hostel", "hitchhike"]
DRAWERS = ["lounge", "backpack", "grouptour", "concierge", "delayed"]
# The order to play actions in: things that give actions first, then the best terminal.
PLAY_ORDER = ["hostel", "hitchhike", "lounge", "concierge", "streetfood", "windowseat", "insurance",
              "lightpacker", "packingcubes", "connecting", "guidebook", "redeye", "grouptour",
              "backpack", "lostluggage", "overbooked", "delayed", "exchange", "dutyfree", "upgrade",
              "customs", "souvenirshop"]
NON_TERMINAL = {"hostel", "hitchhike", "lounge", "concierge", "streetfood", "windowseat",
                "insurance", "lightpacker", "packingcubes"}


def random_answer(d, rng):
    if d.kind == "select":
        return rng.sample(d.options, rng.randint(d.min, d.max))
    if d.kind == "gain":
        return rng.choice(d.options)
    if d.kind == "yesno":
        return rng.random() < 0.5
    if d.kind == "choose":
        return [o["id"] for o in rng.sample(d.options, d.min)]
    raise ValueError(d.kind)


def endgame_left(g):
    """'Visas left' or the fewest cards that would empty a third pile, whichever is closer."""
    left = g.supply["visa"]
    empties = len(g.empty_piles())
    if empties:
        lows = sorted(n for n in g.supply.values() if n > 0)
        left = min(left, sum(lows[:3 - empties]))
    return left


def is_junk(cid):
    c = CARDS[cid]
    return c.is_("negative") or (c.is_("victory") and not c.is_("action"))


class Bot:
    def __init__(self, level="normal", seed=None):
        if level not in LEVELS:
            raise ValueError(level)
        self.level = level
        self.rng = random.Random(seed)
        self.plan = None

    # ---------------- planning ----------------
    def _make_plan(self, g):
        k = set(g.kingdom)
        plan = {"style": "money", "want": {}, "trasher": None, "insurance": 0}
        partners = [c for c in MONEY_PARTNERS if c in k]
        if self.level == "normal":
            if partners:
                plan["want"][partners[0]] = 2
            if len(partners) > 1:
                plan["want"][partners[1]] = 1
        elif self.level == "hard":
            villages = [c for c in VILLAGES if c in k]
            trashers = [c for c in TRASHERS if c in k]
            plan["trasher"] = trashers[0] if trashers else None
            # one kind of terminal (two kinds collide in hand) plus the best non-terminal helper
            term = next((c for c in HARD_TERMINALS if c in k), None)
            side = next((c for c in HARD_NON_TERMINALS if c in k), None)
            if term:
                plan["want"][term] = HARD_TERMINALS[term]
            if side:
                plan["want"][side] = HARD_NON_TERMINALS[side]
            if villages and term in ("backpack", "delayed"):
                # a small engine: more copies of the drawer, with villages so they don't collide
                plan["style"] = "engine"
                plan["want"][term] = 3
                plan["want"][villages[0]] = 2
            if "insurance" in k and any(CARDS[c].is_("attack") for c in k):
                plan["insurance"] = 2 if "redeye" in k else 1
        self.plan = plan

    # ---------------- the move ----------------
    def move(self, g, me):
        if g.over:
            return None
        if g.pending:
            if g.pending.player != me:
                return None
            d = g.pending
            try:
                value = d.validate(self.answer(g, me, d))
            except Exception:          # never stall a game on a bot bug
                value = random_answer(d, self.rng)
            return {"type": "answer", "id": d.id, "value": value}
        if g.current != me:
            return None
        if self.plan is None:
            self._make_plan(g)
        p = g.players[me]
        if g.phase == "action":
            cid = self.pick_action(g, me)
            return {"type": "play", "card": cid} if cid else {"type": "end_phase"}
        if any(CARDS[c].is_("currency") for c in p.hand):
            return {"type": "play_money"}
        if g.buys > 0:
            cid = self.pick_buy(g, me, g.coins)
            if cid and g.supply.get(cid, 0) > 0 and CARDS[cid].cost <= g.coins:
                return {"type": "buy", "card": cid}
        return {"type": "end_phase"}

    # ---------------- actions ----------------
    def pick_action(self, g, me):
        hand = g.players[me].hand
        acts = [c for c in hand if CARDS[c].is_("action")]
        if not acts or g.actions < 1:
            return None
        if self.level == "easy":
            return self.rng.choice(acts)
        options = [c for c in PLAY_ORDER if c in acts]
        for c in options:
            if c == "connecting" and not any(CARDS[a].is_("action") and a != "connecting" for a in acts):
                continue
            if c == "customs" and not self._trash_picks(g, me, hand, 3):
                continue
            if c == "upgrade" and self.level == "hard" and not self._upgrade_target(g, me, hand, strict=True):
                continue
            return c     # PLAY_ORDER puts cards that give actions before the terminals
        return None

    # ---------------- buying ----------------
    def pick_buy(self, g, me, coins, options=None):
        """What to get with `coins`; options limits the choice (for gains). None = nothing."""
        avail = [c for c, n in g.supply.items() if n > 0 and CARDS[c].cost <= coins
                 and (options is None or c in options)]
        if not avail:
            return None
        if self.plan is None:
            self._make_plan(g)
        late = endgame_left(g)
        owned = Counter(g.players[me].owned())
        p = g.players[me]
        if self.level == "easy":
            choice = self._easy_buy(avail, coins)
        else:
            choice = self._smart_buy(g, me, avail, coins, late, owned, p)
        if choice is None or choice not in avail:
            return None
        if self.level == "hard":
            choice = self._endgame_check(g, me, choice, avail)
        return choice

    def _easy_buy(self, avail, coins):
        actions = [c for c in avail if CARDS[c].is_("action")]
        if "visa" in avail:
            return "visa"
        if actions and coins >= 3 and self.rng.random() < (0.25 if coins == 5 else 0.15):
            return self.rng.choice(actions)
        for c in ("goldcard", "banknotes"):
            if c in avail:
                return c
        return None

    def _smart_buy(self, g, me, avail, coins, late, owned, p):
        hard = self.level == "hard"
        plan = self.plan

        def ok(c):
            return c in avail

        # Endgame: take VP.
        if ok("visa"):
            return "visa"
        if coins >= 6:
            if late <= 4 and ok("stamp"):
                return "stamp"
            for c, n in plan["want"].items():
                if CARDS[c].cost >= 6 and owned[c] < n and ok(c):
                    return c
            if ok("goldcard"):
                return "goldcard"
        if late <= 5 and ok("stamp"):
            return "stamp"
        if late <= 2 and ok("postcard"):
            return "postcard"
        # Early: one trasher (hard)
        if hard and plan["trasher"] and p.turns <= 3 and owned[plan["trasher"]] == 0 and ok(plan["trasher"]):
            return plan["trasher"]
        # Wanted actions, priciest first; spread copies over the deck.
        size = sum(owned.values())
        wants = sorted(plan["want"].items(), key=lambda kv: -CARDS[kv[0]].cost)
        if plan["style"] == "engine":
            # a terminal drawer for every village: villages only once the drawers need them
            vill = sum(owned[c] for c in VILLAGES)
            draw = sum(owned[c] for c in DRAWERS)
            wants.sort(key=lambda kv: (kv[0] in VILLAGES) != (draw > vill + 1))
        for c, n in wants:
            limit = min(n, 1 + size // (ENGINE_PACE if plan["style"] == "engine" else 11))
            if owned[c] < limit and ok(c):
                return c
        if hard and plan["insurance"] and owned["insurance"] < plan["insurance"] and ok("insurance") and coins <= 4:
            return "insurance"
        if ok("milescard") and coins == 5:
            return "milescard"
        if ok("banknotes"):
            return "banknotes"
        return None

    def _endgame_check(self, g, me, choice, avail):
        """Hard: don't end the game while losing; end it when that wins."""
        empties = len(g.empty_piles())

        def ends(c):
            return g.supply[c] == 1 and (c == "visa" or empties == 2)

        mine = score(g.players[me].owned())
        best_other = max(score(q.owned()) for i, q in enumerate(g.players) if i != me)

        def wins_with(c):
            return mine + max(CARDS[c].vp, 0) > best_other

        winners = [c for c in avail if ends(c) and wins_with(c) and c != "jetlag"]
        if winners:
            return max(winners, key=lambda c: (CARDS[c].vp, CARDS[c].cost))
        if ends(choice) and not wins_with(choice):
            safe = [c for c in avail if not ends(c) and c != "jetlag"]
            vp = [c for c in safe if CARDS[c].vp > 0]
            if vp:
                return max(vp, key=lambda c: CARDS[c].vp)
            return None
        return choice

    # ---------------- answering ----------------
    def worth(self, g, cid):
        late = endgame_left(g) <= 5
        c = CARDS[cid]
        if cid == "jetlag":
            return -5
        if c.is_("currency") and cid in ("coins", "banknotes", "goldcard"):
            return {"coins": 0.5, "banknotes": 3, "goldcard": 6}[cid]
        if c.is_("victory"):
            if cid == "visa":
                return 9
            return {"postcard": 1.5 if late else -0.5, "stamp": 4.5 if late else 1.5,
                    "scrapbook": 3 if late else 1}.get(cid, 1)
        return RATING.get(cid, 2)

    def keep_value(self, g, cid):
        """How much a card in hand is worth keeping right now (for discards)."""
        c = CARDS[cid]
        if is_junk(cid):
            return -1
        if c.is_("currency"):
            return c.coin
        return RATING.get(cid, 2) / 2

    def _trash_picks(self, g, me, cands, mx):
        owned = Counter(g.players[me].owned())
        money = sum(CARDS[c].coin * n for c, n in owned.items())
        late = endgame_left(g) <= 5
        picks = []
        for c in sorted(cands, key=lambda c: self.worth(g, c)):
            if len(picks) >= mx:
                break
            if c == "jetlag":
                picks.append(c)
            elif c == "postcard" and not late and self.level != "easy":
                picks.append(c)
            elif c == "coins" and self.level == "hard" and not late and money - 1 >= 7:
                picks.append(c)
                money -= 1
        return picks

    def _upgrade_target(self, g, me, hand, strict=False):
        best, best_delta = None, -99
        for c in set(hand):
            limit = CARDS[c].cost + 3
            gains = [x for x, n in g.supply.items() if n > 0 and CARDS[x].cost <= limit]
            if not gains:
                continue
            top = max(self.worth(g, x) for x in gains)
            delta = top - self.worth(g, c)
            if delta > best_delta:
                best, best_delta = c, delta
        if strict and best_delta < 2:
            return None
        return best

    def answer(self, g, me, d):
        if self.level == "easy" and d.card not in ("insurance",) and self.rng.random() < 0.5:
            return random_answer(d, self.rng)
        hand = g.players[me].hand
        if d.kind == "yesno":
            if d.card == "insurance":
                return True
            if d.card == "windowseat":
                return bool(d.show) and (is_junk(d.show[0]) or (self.level == "hard" and d.show[0] == "coins"))
            return False
        if d.kind == "choose":   # Guidebook
            more_actions = any(CARDS[c].is_("action") for c in hand) and g.actions == 0
            return ["actions", "cards"] if more_actions else ["cards", "coins"]
        if d.kind == "gain":
            want = self.pick_buy(g, me, max(CARDS[c].cost for c in d.options), options=d.options)
            if want in d.options:
                return want
            return max(d.options, key=lambda c: (self.worth(g, c), CARDS[c].cost))
        if d.kind == "select":
            opts = list(d.options)
            if d.card in ("customs", "lightpacker"):
                return self._trash_picks(g, me, opts, d.max)
            if d.card == "exchange":
                return opts[:d.max]
            if d.card == "connecting":
                ranked = [c for c in PLAY_ORDER if c in opts and c != "connecting"] or opts
                return ranked[:1]
            if d.card == "upgrade":
                t = self._upgrade_target(g, me, opts)
                return [t] if t in opts else opts[:1]
            if d.card in ("packingcubes", "concierge"):
                junk = [c for c in opts if is_junk(c)]
                return junk[:d.max]
            # attacks: lose the least valuable cards
            ranked = sorted(opts, key=lambda c: self.keep_value(g, c))
            return ranked[:d.min]
        return random_answer(d, self.rng)


def play_bot_game(levels, kingdom, seed, max_steps=20000):
    """Bot-only game to the end. Returns the game. Used by tests and for tuning."""
    from engine import Game
    g = Game([f"{lv.title()} {i + 1}" for i, lv in enumerate(levels)], kingdom, seed=seed)
    bots = [Bot(lv, seed * 31 + i) for i, lv in enumerate(levels)]
    for _ in range(max_steps):
        who = g.waiting_on()
        if who is None:
            return g
        g.act(who, bots[who].move(g, who))
    raise RuntimeError("bot game did not finish")
