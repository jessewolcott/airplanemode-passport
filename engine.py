"""Passport's rules. Pure: no I/O, no clock; all randomness comes from the game's seeded RNG.

A turn: Action phase (1 action), Buy phase (1 buy, play currency), Clean-up (discard
everything, draw 5). Players act by sending intents to `Game.act(player, intent)`:

    {"type": "play", "card": id}        an action (action phase) or a currency (moves to buy)
    {"type": "play_money"}              every currency in hand
    {"type": "buy", "card": id}
    {"type": "end_phase"}               action -> buy, buy -> clean-up and the next turn
    {"type": "answer", "id": n, "value": ...}   answer the pending decision

Pending decisions
-----------------
Card effects are generators. When an effect needs a choice it yields a `Decision` owned by a
specific player (the current player, or a victim of an attack); the engine stores it as
`game.pending` and stops. Nobody else can act until the owner answers. The answer is
validated here, then sent back into the generator, which carries on where it stopped. Effects
compose with `yield from` (an attack asks each victim in turn; Connecting Flight resolves
another card twice), so a whole card resolution is one generator: `game._flow`.

Decision kinds and the answer each expects:
    select  pick between `min` and `max` cards from `options` (a multiset of card ids,
            usually the hand): answer a list of card ids
    gain    pick one supply pile from `options`: answer a card id
    yesno   answer true or false
    choose  pick exactly `min` different option ids from `options` ([{id, label}])
Decisions with only one possible answer are answered automatically and never shown.
"""
import random
from collections import Counter

from cards import CARDS, PRESETS, KINGDOM_IDS, pile_size

HAND_SIZE = 5
ROUND_LIMIT = 60          # safety net: the trip ends after this many rounds, whatever the piles say
LOG_KEEP = 200
BASE_PILES = ["coins", "banknotes", "goldcard", "postcard", "stamp", "visa", "jetlag"]


class IllegalMove(ValueError):
    pass


def name(cid):
    return CARDS[cid].name


def plural(cid, n):
    return name(cid) if n == 1 else f"{n}× {name(cid)}"


def summarize(ids):
    counts = Counter(ids)
    return ", ".join(plural(c, n) for c, n in sorted(counts.items(), key=lambda kv: CARDS[kv[0]].name))


def pick_kingdom(choice, rng):
    if choice in PRESETS:
        return list(PRESETS[choice]["cards"])
    return rng.sample(KINGDOM_IDS, 10)


class Decision:
    def __init__(self, player, kind, card, prompt, options=(), mn=0, mx=0, public="", show=()):
        self.player, self.kind, self.card, self.prompt = player, kind, card, prompt
        self.options = list(options)
        self.min, self.max = mn, mx
        self.public = public or "is choosing"
        self.show = list(show)
        self.id = 0

    _NONE = object()

    def auto(self):
        """The only possible answer, or Decision._NONE when there's a real choice."""
        if self.kind == "select":
            if self.max == 0:
                return []
            if self.min == self.max == len(self.options) or (self.min == self.max and len(set(self.options)) == 1):
                return self.options[:self.min]
        elif self.kind == "gain":
            if not self.options:
                return None
            if len(self.options) == 1:
                return self.options[0]
        elif self.kind == "choose":
            if len(self.options) <= self.min:
                return [o["id"] for o in self.options]
        return Decision._NONE

    def validate(self, value):
        if self.kind == "select":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise IllegalMove("Pick some cards.")
            if not self.min <= len(value) <= self.max:
                raise IllegalMove(f"Pick {self.min}" + (f" to {self.max}" if self.max != self.min else "") + " cards.")
            if Counter(value) - Counter(self.options):
                raise IllegalMove("You can't pick that card.")
            return list(value)
        if self.kind == "gain":
            if value not in self.options:
                raise IllegalMove("You can't gain that card.")
            return value
        if self.kind == "yesno":
            if not isinstance(value, bool):
                raise IllegalMove("Answer yes or no.")
            return value
        if self.kind == "choose":
            ids = [o["id"] for o in self.options]
            if (not isinstance(value, list) or len(value) != self.min or len(set(value)) != len(value)
                    or any(v not in ids for v in value)):
                raise IllegalMove(f"Choose {self.min} different options.")
            return list(value)
        raise IllegalMove("Unknown decision.")

    def to_dict(self, viewer):
        d = {"id": self.id, "player": self.player, "card": self.card, "kind": self.kind,
             "public": self.public}
        if viewer == self.player:
            d.update(prompt=self.prompt, options=self.options, min=self.min, max=self.max, show=self.show)
        return d


class Player:
    def __init__(self, name):
        self.name = name
        self.deck, self.hand, self.discard, self.play = [], [], [], []
        self.turns = 0

    def owned(self):
        return self.deck + self.hand + self.discard + self.play


def card_vp(cid, owned):
    if cid == "scrapbook":
        return len(set(owned)) // 4
    return CARDS[cid].vp


def score(owned):
    return sum(card_vp(c, owned) for c in owned if CARDS[c].vp or c == "scrapbook")


class Game:
    def __init__(self, names, kingdom, seed=None, round_limit=ROUND_LIMIT):
        n = len(names)
        if not 2 <= n <= 4:
            raise ValueError("Passport is for 2 to 4 players.")
        if len(set(kingdom)) != 10 or any(k not in KINGDOM_IDS for k in kingdom):
            raise ValueError("A game uses 10 different kingdom cards.")
        self.rng = random.Random(seed)
        self.kingdom = sorted(kingdom, key=lambda c: (CARDS[c].cost, CARDS[c].name))
        self.supply = {c: pile_size(c, n) for c in BASE_PILES + self.kingdom}
        self.trash = []
        self.players = [Player(nm) for nm in names]
        self.round_limit = round_limit
        self.log, self.log_n = [], 0
        self.pending, self._flow, self.seq = None, None, 0
        self.over, self.result = False, None
        for p in self.players:
            p.deck = ["coins"] * 7 + ["postcard"] * 3
            self.rng.shuffle(p.deck)
            self.draw(p, HAND_SIZE)
        self.total_cards = self.count_cards()
        self.current = self.rng.randrange(n)
        self._start_turn()
        self._settle()

    # ---------------- bookkeeping ----------------
    def count_cards(self):
        return (sum(self.supply.values()) + len(self.trash)
                + sum(len(p.owned()) for p in self.players))

    def say(self, pi, text, kind="move"):
        self.log_n += 1
        self.log.append({"n": self.log_n, "p": pi, "t": text, "k": kind})
        del self.log[:-LOG_KEEP]

    def others(self, pi):
        n = len(self.players)
        return [(pi + k) % n for k in range(1, n)]

    def waiting_on(self):
        if self.over:
            return None
        return self.pending.player if self.pending else self.current

    def empty_piles(self):
        return [c for c, n in self.supply.items() if n == 0]

    # ---------------- card movement ----------------
    def _refill(self, p):
        if not p.deck and p.discard:
            p.deck, p.discard = p.discard, []
            self.rng.shuffle(p.deck)
        return bool(p.deck)

    def draw(self, p, n):
        if isinstance(p, int):
            p = self.players[p]
        got = 0
        for _ in range(n):
            if not self._refill(p):
                break
            p.hand.append(p.deck.pop())
            got += 1
        return got

    def peek(self, p):
        return p.deck[-1] if self._refill(p) else None

    def reveal(self, p):
        return p.deck.pop() if self._refill(p) else None

    def gain(self, pi, cid, to="discard"):
        if self.supply.get(cid, 0) <= 0:
            return False
        self.supply[cid] -= 1
        p = self.players[pi]
        (p.hand if to == "hand" else p.discard).append(cid)
        return True

    def trash_from_hand(self, pi, cids):
        p = self.players[pi]
        for c in cids:
            p.hand.remove(c)
            self.trash.append(c)

    def discard_from_hand(self, pi, cids):
        p = self.players[pi]
        for c in cids:
            p.hand.remove(c)
            p.discard.append(c)

    # ---------------- asking ----------------
    def ask_select(self, pi, card, prompt, options, mn, mx, public=""):
        mx = min(mx, len(options))
        return Decision(pi, "select", card, prompt, options, min(mn, mx), mx, public)

    def ask_gain(self, pi, card, prompt, max_cost, public=""):
        opts = [c for c, n in self.supply.items() if n > 0 and CARDS[c].cost <= max_cost]
        opts.sort(key=lambda c: (-CARDS[c].cost, CARDS[c].name))
        return Decision(pi, "gain", card, prompt, opts, 1, 1, public)

    def ask_yesno(self, pi, card, prompt, public="", show=()):
        return Decision(pi, "yesno", card, prompt, public=public, show=show)

    def ask_choose(self, pi, card, prompt, options, k, public=""):
        return Decision(pi, "choose", card, prompt, options, k, k, public)

    def attack(self, pi, card, hit):
        """Run hit(victim) (a generator) for every other player, in turn order, unless they show
        Travel Insurance. hit may be a plain function when it never asks anything."""
        for v in self.others(pi):
            vp = self.players[v]
            if "insurance" in vp.hand:
                show = yield self.ask_yesno(
                    v, "insurance",
                    f"{self.players[pi].name} played {name(card)}. Show Travel Insurance to be unaffected?",
                    public="may show Travel Insurance")
                if show:
                    self.say(v, f"shows Travel Insurance and is unaffected by {name(card)}")
                    continue
            steps = hit(v)
            if steps is not None:
                yield from steps

    # ---------------- flow ----------------
    def _resolve(self, pi, cid):
        """Generator: the effect of one play of an action card that is already in play."""
        c, p = CARDS[cid], self.players[pi]
        plus = c.plus
        if plus.get("cards"):
            self.draw(p, plus["cards"])
        self.actions += plus.get("actions", 0)
        self.buys += plus.get("buys", 0)
        self.coins += plus.get("coins", 0)
        eff = EFFECTS.get(cid)
        steps = eff(self, pi) if eff else None
        if steps is not None:          # effects that ask something are generators
            yield from steps

    def _run(self, gen):
        self._flow = gen
        self._resume(None)

    def _resume(self, value):
        while True:
            try:
                dec = self._flow.send(value)
            except StopIteration:
                self._flow, self.pending = None, None
                return
            auto = dec.auto()
            if auto is not Decision._NONE:
                value = auto
                continue
            self.seq += 1
            dec.id = self.seq
            self.pending = dec
            return

    def _settle(self):
        """Move the game along when nothing is left to choose in the current phase."""
        while not self.over and not self.pending:
            p = self.players[self.current]
            if self.phase == "action" and (self.actions == 0 or not any(CARDS[c].is_("action") for c in p.hand)):
                self.phase = "buy"
            elif self.phase == "buy" and self.buys == 0:
                self._end_turn()
            else:
                return

    def _start_turn(self):
        p = self.players[self.current]
        p.turns += 1
        self.phase, self.actions, self.buys, self.coins = "action", 1, 1, 0
        self.say(self.current, f"Turn {p.turns}", kind="turn")

    def _end_turn(self):
        p = self.players[self.current]
        p.discard += p.hand + p.play
        p.hand, p.play = [], []
        self.draw(p, HAND_SIZE)
        empties = self.empty_piles()
        if (self.supply["visa"] == 0 or len(empties) >= 3
                or all(q.turns >= self.round_limit for q in self.players)):
            self._finish()
            return
        self.current = (self.current + 1) % len(self.players)
        self._start_turn()

    def _finish(self):
        self.over, self.phase = True, "over"
        rows = []
        for i, p in enumerate(self.players):
            owned = p.owned()
            counts = Counter(c for c in owned if CARDS[c].vp or c == "scrapbook")
            rows.append({"player": i, "name": p.name, "vp": score(owned), "turns": p.turns,
                         "cards": len(owned),
                         "breakdown": [{"card": c, "count": n, "vp": n * card_vp(c, owned)}
                                       for c, n in sorted(counts.items(), key=lambda kv: -CARDS[kv[0]].vp)]})
        best = max(r["vp"] for r in rows)
        fewest = min(r["turns"] for r in rows if r["vp"] == best)
        winners = [r["player"] for r in rows if r["vp"] == best and r["turns"] == fewest]
        reason = ("The Visa pile ran out." if self.supply["visa"] == 0
                  else "Three piles ran out." if len(self.empty_piles()) >= 3
                  else f"{self.round_limit} rounds played.")
        rows.sort(key=lambda r: (-r["vp"], r["turns"]))
        self.result = {"winners": winners, "ranking": rows, "reason": reason}
        self.say(None, reason + " " + (" and ".join(self.players[w].name for w in winners))
                 + (" share the win." if len(winners) > 1 else " wins!"), kind="end")

    # ---------------- intents ----------------
    def act(self, pi, intent):
        if self.over:
            raise IllegalMove("The game is over.")
        if not isinstance(intent, dict):
            raise IllegalMove("Bad move.")
        t = intent.get("type")
        if t == "answer":
            d = self.pending
            if not d or d.player != pi:
                raise IllegalMove("Nothing to answer.")
            if intent.get("id") != d.id:
                raise IllegalMove("That question has moved on.")
            value = d.validate(intent.get("value"))
            self.pending = None
            self._resume(value)
            self._settle()
            return
        if self.pending:
            raise IllegalMove(f"Waiting for {self.players[self.pending.player].name} to choose.")
        if pi != self.current:
            raise IllegalMove("It's not your turn.")
        p = self.players[pi]
        if t == "play":
            cid = intent.get("card")
            if cid not in p.hand:
                raise IllegalMove("That card isn't in your hand.")
            c = CARDS[cid]
            if c.is_("action"):
                if self.phase != "action":
                    raise IllegalMove("Actions are played in the action phase.")
                if self.actions < 1:
                    raise IllegalMove("No actions left.")
                self.actions -= 1
                p.hand.remove(cid)
                p.play.append(cid)
                self.say(pi, f"plays {name(cid)}")
                self._run(self._resolve(pi, cid))
            elif c.is_("currency"):
                self.phase = "buy"
                self._play_money(pi, [cid])
            else:
                raise IllegalMove(f"{c.name} can't be played.")
        elif t == "play_money":
            money = [c for c in p.hand if CARDS[c].is_("currency")]
            if not money:
                raise IllegalMove("No currency in your hand.")
            self.phase = "buy"
            self._play_money(pi, money)
        elif t == "buy":
            cid = intent.get("card")
            if cid not in self.supply:
                raise IllegalMove("That card isn't in the market.")
            if self.supply[cid] <= 0:
                raise IllegalMove(f"No {name(cid)} left.")
            if self.buys < 1:
                raise IllegalMove("No buys left.")
            if CARDS[cid].cost > self.coins:
                raise IllegalMove(f"{name(cid)} costs {CARDS[cid].cost}; you have {self.coins}.")
            self.phase = "buy"
            self.coins -= CARDS[cid].cost
            self.buys -= 1
            self.gain(pi, cid)
            self.say(pi, f"buys {name(cid)}")
        elif t == "end_phase":
            if self.phase == "action":
                self.phase = "buy"
            else:
                self._end_turn()
        else:
            raise IllegalMove("Unknown move.")
        self._settle()

    def _play_money(self, pi, cids):
        p = self.players[pi]
        for cid in cids:
            p.hand.remove(cid)
            p.play.append(cid)
            self.coins += CARDS[cid].coin
            self.buys += CARDS[cid].plus.get("buys", 0)
        self.say(pi, f"plays {summarize(cids)}")

    # ---------------- what each player may see ----------------
    def view(self, viewer=None):
        cur = self.players[self.current]
        d = {
            "kingdom": self.kingdom,
            "supply": dict(self.supply),
            "trash": summarize(self.trash) if self.trash else "",
            "trash_count": len(self.trash),
            "current": self.current,
            "phase": self.phase,
            "actions": self.actions, "buys": self.buys, "coins": self.coins,
            "in_play": list(cur.play),
            "players": [{"name": p.name, "hand": len(p.hand), "deck": len(p.deck),
                         "discard": len(p.discard), "discard_top": p.discard[-1] if p.discard else None,
                         "owned": len(p.owned()), "vp": score(p.owned()), "turns": p.turns}
                        for p in self.players],
            "pending": self.pending.to_dict(viewer) if self.pending else None,
            "log": self.log[-80:],
            "over": self.over,
            "result": self.result,
            "round_limit": self.round_limit,
        }
        if viewer is not None and 0 <= viewer < len(self.players):
            me = self.players[viewer]
            d["hand"] = list(me.hand)
            d["deck_list"] = summarize(me.owned())
        return d


# ---------------- card effects ----------------
# Each is a generator f(game, player) that runs after the card's simple `plus`.

def eff_windowseat(g, pi):
    p = g.players[pi]
    top = g.peek(p)
    if top is None:
        return
    yes = yield g.ask_yesno(pi, "windowseat", f"The top card of your deck is {name(top)}. Discard it?",
                            public="is looking at the top of their deck", show=[top])
    if yes:
        p.discard.append(p.deck.pop())
        g.say(pi, f"discards {name(top)} from the top of their deck")


def eff_packingcubes(g, pi):
    p = g.players[pi]
    picks = yield g.ask_select(pi, "packingcubes", "Discard up to 2 cards: +1 coin for each.",
                               p.hand, 0, 2, public="is packing")
    if picks:
        g.discard_from_hand(pi, picks)
        g.coins += len(picks)
        g.say(pi, f"discards {summarize(picks)} for +{len(picks)} coin{'s' if len(picks) > 1 else ''}")


def eff_customs(g, pi):
    p = g.players[pi]
    picks = yield g.ask_select(pi, "customs", "Trash up to 3 cards from your hand.", p.hand, 0, 3,
                               public="is going through customs")
    if picks:
        g.trash_from_hand(pi, picks)
        g.say(pi, f"trashes {summarize(picks)}")


def eff_souvenirshop(g, pi):
    cid = yield g.ask_gain(pi, "souvenirshop", "Gain a card costing up to 4.", 4, public="is shopping")
    if cid and g.gain(pi, cid):
        g.say(pi, f"gains {name(cid)}")


def eff_hitchhike(g, pi):
    p = g.players[pi]
    top = g.reveal(p)
    if top is None:
        return
    if CARDS[top].is_("action") or CARDS[top].is_("currency"):
        p.hand.append(top)
        g.say(pi, f"turns over {name(top)} and keeps it")
    else:
        p.discard.append(top)
        g.say(pi, f"turns over {name(top)} and discards it")


def eff_lightpacker(g, pi):
    p = g.players[pi]
    picks = yield g.ask_select(pi, "lightpacker", "You may trash a card from your hand.", p.hand, 0, 1,
                               public="is packing light")
    if picks:
        g.trash_from_hand(pi, picks)
        g.say(pi, f"trashes {summarize(picks)}")


def eff_overbooked(g, pi):
    def hit(v):
        vp = g.players[v]
        extra = len(vp.hand) - 4
        if extra <= 0:
            return
        picks = yield g.ask_select(v, "overbooked", f"Overbooked! Discard {extra} card{'s' if extra > 1 else ''}.",
                                   vp.hand, extra, extra, public="is discarding down to 4")
        g.discard_from_hand(v, picks)
        g.say(v, f"discards {summarize(picks)}")
    yield from g.attack(pi, "overbooked", hit)


def eff_delayed(g, pi):
    def hit(v):
        vp = g.players[v]
        if len(vp.hand) < 4:
            return
        picks = yield g.ask_select(v, "delayed", "Delayed! Put a card from your hand on top of your deck.",
                                   vp.hand, 1, 1, public="is putting a card back on their deck")
        vp.hand.remove(picks[0])
        vp.deck.append(picks[0])
        g.say(v, "puts a card on top of their deck")
    yield from g.attack(pi, "delayed", hit)


def eff_connecting(g, pi):
    p = g.players[pi]
    acts = [c for c in p.hand if CARDS[c].is_("action")]
    picks = yield g.ask_select(pi, "connecting", "Choose an action card to play twice.", acts, 0, 1,
                               public="is choosing a connection")
    if not picks:
        return
    cid = picks[0]
    p.hand.remove(cid)
    p.play.append(cid)
    g.say(pi, f"plays {name(cid)} twice")
    yield from g._resolve(pi, cid)
    yield from g._resolve(pi, cid)


def eff_exchange(g, pi):
    p = g.players[pi]
    coins = [c for c in p.hand if c == "coins"]
    picks = yield g.ask_select(pi, "exchange", "Trash up to 2 Coins: +2 coins for each.", coins, 0, 2,
                               public="is changing money")
    if picks:
        g.trash_from_hand(pi, picks)
        g.coins += 2 * len(picks)
        g.say(pi, f"trashes {summarize(picks)} for +{2 * len(picks)} coins")


def eff_upgrade(g, pi):
    p = g.players[pi]
    if not p.hand:
        return
    picks = yield g.ask_select(pi, "upgrade", "Trash a card from your hand to upgrade it.", p.hand, 1, 1,
                               public="is choosing what to upgrade")
    cid = picks[0]
    g.trash_from_hand(pi, [cid])
    g.say(pi, f"trashes {name(cid)}")
    limit = CARDS[cid].cost + 3
    got = yield g.ask_gain(pi, "upgrade", f"Gain a card costing up to {limit}.", limit, public="is upgrading")
    if got and g.gain(pi, got):
        g.say(pi, f"gains {name(got)}")


def eff_backpack(g, pi):
    p = g.players[pi]
    while len(p.hand) < 6 and g.draw(p, 1):
        pass


GUIDE_OPTIONS = [{"id": "cards", "label": "+2 cards"}, {"id": "actions", "label": "+2 actions"},
                 {"id": "coins", "label": "+2 coins"}, {"id": "buy", "label": "+1 buy"}]


def eff_guidebook(g, pi):
    picks = yield g.ask_choose(pi, "guidebook", "Choose two.", GUIDE_OPTIONS, 2, public="is reading the guidebook")
    for k in picks:
        if k == "cards":
            g.draw(pi, 2)
        elif k == "actions":
            g.actions += 2
        elif k == "coins":
            g.coins += 2
        elif k == "buy":
            g.buys += 1
    labels = {o["id"]: o["label"] for o in GUIDE_OPTIONS}
    g.say(pi, "chooses " + " and ".join(labels[k] for k in picks))


def eff_concierge(g, pi):
    p = g.players[pi]
    picks = yield g.ask_select(pi, "concierge", "You may discard a card for +1 coin.", p.hand, 0, 1,
                               public="is talking to the concierge")
    if picks:
        g.discard_from_hand(pi, picks)
        g.coins += 1
        g.say(pi, f"discards {name(picks[0])} for +1 coin")


def eff_redeye(g, pi):
    def hit(v):
        if g.gain(v, "jetlag"):
            g.say(v, "gains a Jet Lag")
    yield from g.attack(pi, "redeye", hit)


def eff_lostluggage(g, pi):
    def hit(v):
        vp = g.players[v]
        shown = [c for c in (g.reveal(vp), g.reveal(vp)) if c]
        if not shown:
            return
        money = [c for c in shown if CARDS[c].is_("currency") and c != "coins"]
        lost = max(money, key=lambda c: CARDS[c].cost) if money else None
        if lost:
            shown.remove(lost)
            g.trash.append(lost)
        vp.discard += shown
        g.say(v, f"turns over {summarize(shown + ([lost] if lost else []))}"
                 + (f" and loses {name(lost)}" if lost else ""))
    yield from g.attack(pi, "lostluggage", hit)


def eff_grouptour(g, pi):
    for v in g.others(pi):
        g.draw(v, 1)


EFFECTS = {
    "windowseat": eff_windowseat, "packingcubes": eff_packingcubes, "customs": eff_customs,
    "souvenirshop": eff_souvenirshop, "hitchhike": eff_hitchhike, "lightpacker": eff_lightpacker,
    "overbooked": eff_overbooked, "delayed": eff_delayed, "connecting": eff_connecting,
    "exchange": eff_exchange, "upgrade": eff_upgrade, "backpack": eff_backpack,
    "guidebook": eff_guidebook, "concierge": eff_concierge, "redeye": eff_redeye,
    "lostluggage": eff_lostluggage, "grouptour": eff_grouptour,
}
