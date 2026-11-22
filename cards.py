"""Passport's cards: plain data. What a card *does* lives in engine.py (CARD_EFFECTS).

Types: currency, victory, action, attack, reaction, negative. A card can have more than one
(an attack is also an action; Travel Insurance is an action and a reaction).

`plus` is the simple part of an action card, applied first when it is played:
cards / actions / buys / coins. Anything else is a small effect function in engine.py.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Card:
    id: str
    name: str
    cost: int
    types: tuple
    text: str
    plus: dict = field(default_factory=dict)
    coin: int = 0          # currency value
    vp: int = 0            # fixed victory points (Scrapbook is variable: see engine.card_vp)
    kingdom: bool = True

    def is_(self, t):
        return t in self.types


def _c(id, name, cost, types, text, **kw):
    return Card(id, name, cost, tuple(types.split()), text, **kw)


BASE = [
    _c("coins", "Coins", 0, "currency", "+1 coin.", coin=1, kingdom=False),
    _c("banknotes", "Banknotes", 3, "currency", "+2 coins.", coin=2, kingdom=False),
    _c("goldcard", "Gold Card", 6, "currency", "+3 coins.", coin=3, kingdom=False),
    _c("postcard", "Postcard", 2, "victory", "1 VP.", vp=1, kingdom=False),
    _c("stamp", "Passport Stamp", 5, "victory", "3 VP.", vp=3, kingdom=False),
    _c("visa", "Visa", 8, "victory", "6 VP. The game ends when the Visa pile runs out.", vp=6, kingdom=False),
    _c("jetlag", "Jet Lag", 0, "negative", "-1 VP. Handed out by attacks.", vp=-1, kingdom=False),
]

KINGDOM = [
    # cost 2
    _c("windowseat", "Window Seat", 2, "action",
       "+1 card, +1 action. Look at the top card of your deck: you may discard it.",
       plus={"cards": 1, "actions": 1}),
    _c("packingcubes", "Packing Cubes", 2, "action",
       "+1 action. Discard up to 2 cards: +1 coin for each.", plus={"actions": 1}),
    _c("customs", "Customs", 2, "action", "Trash up to 3 cards from your hand."),
    _c("insurance", "Travel Insurance", 2, "action reaction",
       "+1 card, +1 action. When another player plays an attack, you may show this from your hand "
       "to be unaffected by it.", plus={"cards": 1, "actions": 1}),
    # cost 3
    _c("hostel", "Hostel", 3, "action", "+1 card, +2 actions.", plus={"cards": 1, "actions": 2}),
    _c("dutyfree", "Duty Free", 3, "action", "+1 buy, +2 coins.", plus={"buys": 1, "coins": 2}),
    _c("souvenirshop", "Souvenir Shop", 3, "action", "Gain a card costing up to 4."),
    _c("hitchhike", "Hitchhike", 3, "action",
       "+2 actions. Turn over the top card of your deck: an action or currency goes into your "
       "hand, anything else is discarded.", plus={"actions": 2}),
    _c("lightpacker", "Light Packer", 3, "action",
       "+1 card, +1 action. You may trash a card from your hand.", plus={"cards": 1, "actions": 1}),
    # cost 4
    _c("overbooked", "Overbooked", 4, "action attack",
       "+2 coins. Every other player discards down to 4 cards in hand.", plus={"coins": 2}),
    _c("delayed", "Delayed Flight", 4, "action attack",
       "+2 cards. Every other player with 4 or more cards in hand puts one of them on top of "
       "their deck.", plus={"cards": 2}),
    _c("connecting", "Connecting Flight", 4, "action",
       "You may play an action card from your hand twice."),
    _c("exchange", "Currency Exchange", 4, "action",
       "+1 buy. Trash up to 2 Coins from your hand: +2 coins for each.", plus={"buys": 1}),
    _c("upgrade", "Seat Upgrade", 4, "action",
       "Trash a card from your hand. Gain a card costing up to 3 more than it."),
    _c("streetfood", "Street Food", 4, "action", "+1 card, +1 action, +1 coin.",
       plus={"cards": 1, "actions": 1, "coins": 1}),
    _c("backpack", "Backpack", 4, "action", "Draw until you have 6 cards in hand."),
    _c("scrapbook", "Scrapbook", 4, "victory",
       "1 VP for every 4 differently named cards you own (round down)."),
    # cost 5
    _c("guidebook", "Guidebook", 5, "action",
       "Choose two different: +2 cards; +2 actions; +2 coins; +1 buy."),
    _c("concierge", "Concierge", 5, "action",
       "+2 cards, +1 action. You may discard a card for +1 coin.", plus={"cards": 2, "actions": 1}),
    _c("redeye", "Red-Eye", 5, "action attack",
       "+1 card, +1 coin. Every other player gains a Jet Lag.", plus={"cards": 1, "coins": 1}),
    _c("lostluggage", "Lost Luggage", 5, "action attack",
       "+2 coins. Every other player turns over the top 2 cards of their deck, trashes the "
       "priciest currency among them other than Coins, and discards the rest.", plus={"coins": 2}),
    _c("grouptour", "Group Tour", 5, "action",
       "+3 cards, +1 buy. Every other player draws a card.", plus={"cards": 3, "buys": 1}),
    _c("milescard", "Miles Card", 5, "currency", "+2 coins, +1 buy.", coin=2, plus={"buys": 1}),
    # cost 6
    _c("lounge", "First Class Lounge", 6, "action", "+3 cards, +1 action.",
       plus={"cards": 3, "actions": 1}),
]

CARDS = {c.id: c for c in BASE + KINGDOM}
KINGDOM_IDS = [c.id for c in KINGDOM]
TYPE_ORDER = ["action", "currency", "victory", "negative"]

# Named sets for the lobby, plus "random".
PRESETS = {
    "first": {"name": "First trip", "blurb": "Gentle: no attacks, simple cards.",
              "cards": ["windowseat", "packingcubes", "insurance", "hostel", "dutyfree",
                        "souvenirshop", "streetfood", "backpack", "grouptour", "milescard"]},
    "business": {"name": "Business class", "blurb": "Upgrades, double plays and a nasty red-eye.",
                 "cards": ["insurance", "hostel", "connecting", "exchange", "upgrade",
                           "guidebook", "concierge", "redeye", "milescard", "lounge"]},
    "backpacker": {"name": "Backpacker", "blurb": "Travel light: trash, steal and collect.",
                   "cards": ["windowseat", "packingcubes", "customs", "hitchhike", "lightpacker",
                             "overbooked", "delayed", "streetfood", "scrapbook", "lostluggage"]},
}
KINGDOM_CHOICES = list(PRESETS) + ["random"]


def pile_size(card_id, players):
    c = CARDS[card_id]
    if card_id == "coins":
        return 60 - 7 * players
    if card_id == "banknotes":
        return 40
    if card_id == "goldcard":
        return 30
    if card_id == "jetlag":
        return 10 * (players - 1)
    if c.is_("victory"):
        return 8 if players == 2 else 12
    return 10


def describe():
    """All cards and presets, for the page."""
    return {
        "cards": {c.id: {"id": c.id, "name": c.name, "cost": c.cost, "types": list(c.types),
                         "text": c.text, "coin": c.coin, "vp": c.vp, "kingdom": c.kingdom}
                  for c in CARDS.values()},
        "presets": [{"id": k, **v} for k, v in PRESETS.items()]
                   + [{"id": "random", "name": "Random", "blurb": "Any 10 of the 24.", "cards": []}],
    }
