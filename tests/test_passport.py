"""Passport tests: rules, decisions, bots, rooms and the HTTP API.  Run: python tests/test_passport.py"""
import os
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

os.environ.update(PASSPORT_BOT_DELAY="0", PASSPORT_POLL="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import app as passport  # noqa: E402
import rooms as rooms_mod  # noqa: E402
from bots import LEVELS, Bot, play_bot_game, random_answer  # noqa: E402
from cards import CARDS, KINGDOM_IDS, PRESETS, describe  # noqa: E402
from engine import Game, IllegalMove, pick_kingdom, score  # noqa: E402

import logging  # noqa: E402
logging.getLogger("httpx").setLevel(logging.WARNING)

H = {"X-Passport": "1"}
ALL_SETS = list(PRESETS) + ["random"]


# ---------------- helpers ----------------
def new_game(n=2, kingdom="business", seed=1):
    return Game([f"P{i}" for i in range(n)], pick_kingdom(kingdom, random.Random(seed)), seed=seed)


def table(hand, kingdom=None, n=2, deck=None, others=None, coins=0):
    """A game where it's player 0's action phase with exactly `hand` (extra cards come from nowhere:
    fine for rule tests; conservation is checked by the full games)."""
    k = kingdom or ["hostel", "customs", "overbooked", "insurance", "redeye", "connecting",
                    "upgrade", "guidebook", "backpack", "lostluggage"]
    g = Game([f"P{i}" for i in range(n)], k, seed=7)
    g.current = 0
    g.pending, g._flow = None, None
    g.phase, g.actions, g.buys, g.coins = "action", 1, 1, coins
    p = g.players[0]
    p.hand = list(hand)
    p.play, p.discard = [], []
    if deck is not None:
        p.deck = list(deck)
    for i, h in (others or {}).items():
        g.players[i].hand = list(h)
    return g


def must_fail(fn, *a, match=""):
    try:
        fn(*a)
    except IllegalMove as e:
        assert match.lower() in str(e).lower(), (str(e), match)
        return
    raise AssertionError("expected IllegalMove")


def ans(g, who, value):
    g.act(who, {"type": "answer", "id": g.pending.id, "value": value})


# ---------------- cards ----------------
def test_card_set():
    assert len(KINGDOM_IDS) == 24 and len(set(KINGDOM_IDS)) == 24
    for k, p in PRESETS.items():
        assert len(set(p["cards"])) == 10 and all(c in KINGDOM_IDS for c in p["cards"]), k
    costs = Counter(CARDS[c].cost for c in KINGDOM_IDS)
    assert sum(n for c, n in costs.items() if 2 <= c <= 5) >= 20 and costs[6] >= 1
    banned = {"village", "smithy", "market", "militia", "moat", "witch", "chapel", "laboratory", "festival",
              "cellar", "remodel", "mine", "gardens", "throne room", "council room", "moneylender",
              "dominion", "province", "duchy", "estate", "copper", "silver", "gold", "curse"}
    for c in CARDS.values():
        assert c.name.lower() not in banned, c.name
    d = describe()
    assert len(d["cards"]) == 31 and [p["id"] for p in d["presets"]][-1] == "random"


def test_setup_and_pile_sizes():
    for n in (2, 3, 4):
        g = new_game(n)
        for p in g.players:
            assert sorted(p.owned()) == sorted(["coins"] * 7 + ["postcard"] * 3)
            assert len(p.hand) == 5 or p is g.players[g.current]
        assert g.supply["visa"] == (8 if n == 2 else 12) and g.supply["stamp"] == g.supply["visa"]
        assert g.supply["postcard"] == g.supply["visa"]
        assert g.supply["jetlag"] == 10 * (n - 1)
        assert g.supply["coins"] == 60 - 7 * n
        assert len(g.kingdom) == 10
    try:
        Game(["a"], PRESETS["first"]["cards"])
        raise AssertionError("1 player allowed")
    except ValueError:
        pass
    try:
        Game(["a", "b"], PRESETS["first"]["cards"][:9])
        raise AssertionError("9 kingdom cards allowed")
    except ValueError:
        pass


def test_seeded_games_repeat():
    a = play_bot_game(["hard", "normal", "easy"], pick_kingdom("random", random.Random(5)), 5)
    b = play_bot_game(["hard", "normal", "easy"], pick_kingdom("random", random.Random(5)), 5)
    assert a.log == b.log and a.result == b.result


# ---------------- turn flow ----------------
def test_turn_flow_and_illegal_moves():
    g = table(["coins", "coins", "coins", "postcard", "postcard"])
    other = 1
    must_fail(g.act, other, {"type": "end_phase"}, match="not your turn")
    must_fail(g.act, 0, {"type": "play", "card": "goldcard"}, match="isn't in your hand")
    must_fail(g.act, 0, {"type": "play", "card": "postcard"}, match="can't be played")
    must_fail(g.act, 0, {"type": "buy", "card": "nonsense"}, match="market")
    must_fail(g.act, 0, {"type": "dance"}, match="unknown")
    must_fail(g.act, 0, "buy", match="bad move")
    g.act(0, {"type": "play", "card": "coins"})              # currency moves us to buy
    assert g.phase == "buy" and g.coins == 1
    g.act(0, {"type": "play_money"})
    assert g.coins == 3 and g.players[0].hand == ["postcard", "postcard"]
    must_fail(g.act, 0, {"type": "play_money"}, match="no currency")
    must_fail(g.act, 0, {"type": "buy", "card": "stamp"}, match="costs 5")
    must_fail(g.act, 0, {"type": "play", "card": "hostel"}, match="isn't in your hand")
    before = g.supply["banknotes"]
    g.act(0, {"type": "buy", "card": "banknotes"})            # last buy ends the turn
    assert g.supply["banknotes"] == before - 1
    assert g.current == 1 and g.phase in ("action", "buy")
    p0 = g.players[0]
    assert "banknotes" in p0.discard and len(p0.hand) == 5 and not p0.play
    assert g.log[-1]["k"] == "turn" and g.log[-1]["p"] == 1


def test_action_phase_skips_when_nothing_to_play():
    g = table(["coins", "coins", "coins", "coins", "postcard"])
    g._settle()
    assert g.phase == "buy"
    g = table(["hostel", "coins"])
    g._settle()
    assert g.phase == "action"
    g.act(0, {"type": "end_phase"})
    assert g.phase == "buy"
    g.act(0, {"type": "end_phase"})
    assert g.current == 1


def test_actions_are_limited():
    g = table(["overbooked", "redeye", "coins"], others={1: ["coins"] * 3})
    g.act(0, {"type": "play", "card": "overbooked"})
    assert g.actions == 0 and g.coins == 2 and g.phase == "buy"        # nothing left: skipped to buy
    must_fail(g.act, 0, {"type": "play", "card": "redeye"}, match="action phase")


def test_buying_extra_cards_with_buys():
    g = table(["guidebook", "coins", "coins"], deck=["goldcard", "goldcard"])
    g.act(0, {"type": "play", "card": "guidebook"})
    must_fail(ans, g, 0, ["buy", "buy"], match="2 different")
    must_fail(ans, g, 0, ["buy"], match="2 different")
    must_fail(ans, g, 0, ["buy", "fly"], match="2 different")
    ans(g, 0, ["buy", "coins"])
    assert g.buys == 2 and g.coins == 2
    g.act(0, {"type": "play_money"})
    g.act(0, {"type": "buy", "card": "postcard"})
    assert g.current == 0 and g.buys == 1 and g.coins == 2
    g.act(0, {"type": "buy", "card": "postcard"})
    assert g.current == 1


# ---------------- card effects and decisions ----------------
def test_simple_plus_cards():
    g = table(["hostel", "backpack"], deck=["coins"] * 10)
    g.act(0, {"type": "play", "card": "hostel"})
    assert g.actions == 2 and len(g.players[0].hand) == 2            # -hostel +1 card
    g.act(0, {"type": "play", "card": "backpack"})
    assert len(g.players[0].hand) == 6 and g.actions == 1


def test_customs_trashes_up_to_three():
    g = table(["customs", "postcard", "postcard", "coins", "jetlag"])
    g.act(0, {"type": "play", "card": "customs"})
    d = g.pending
    assert d.kind == "select" and d.player == 0 and d.max == 3 and d.min == 0
    must_fail(ans, g, 0, ["postcard"] * 3, match="can't pick")
    must_fail(ans, g, 0, ["postcard", "postcard", "coins", "jetlag"], match="pick 0 to 3")
    must_fail(ans, g, 0, "postcard", match="pick some")
    must_fail(g.act, 0, {"type": "answer", "id": d.id + 1, "value": []}, match="moved on")
    must_fail(g.act, 0, {"type": "end_phase"}, match="waiting")
    ans(g, 0, ["postcard", "jetlag"])
    assert sorted(g.trash) == ["jetlag", "postcard"] and sorted(g.players[0].hand) == ["coins", "postcard"]


def test_attack_decision_belongs_to_the_victim():
    g = table(["overbooked"], n=3, others={1: ["coins", "coins", "postcard", "postcard", "coins"],
                                           2: ["coins", "coins", "coins"]})
    g.act(0, {"type": "play", "card": "overbooked"})
    d = g.pending
    assert d.player == 1 and d.min == d.max == 1      # player 2 has 3 cards: not asked
    must_fail(g.act, 0, {"type": "answer", "id": d.id, "value": ["coins"]}, match="nothing to answer")
    must_fail(g.act, 2, {"type": "answer", "id": d.id, "value": ["coins"]}, match="nothing to answer")
    must_fail(g.act, 0, {"type": "end_phase"}, match="waiting for p1")
    # other players see that P1 is choosing, but not P1's options
    seen = g.view(0)["pending"]
    assert seen["player"] == 1 and "options" not in seen and "prompt" not in seen
    assert g.view(1)["pending"]["options"] == g.players[1].hand
    ans(g, 1, ["postcard"])
    assert len(g.players[1].hand) == 4 and "postcard" in g.players[1].discard
    assert len(g.players[2].hand) == 3 and g.pending is None


def test_travel_insurance_blocks():
    g = table(["redeye"], n=3, deck=["coins"] * 5,
              others={1: ["insurance", "coins"], 2: ["insurance", "coins"]})
    g.act(0, {"type": "play", "card": "redeye"})
    assert g.pending.kind == "yesno" and g.pending.player == 1
    must_fail(ans, g, 1, "yes", match="yes or no")
    ans(g, 1, True)
    assert g.pending.player == 2
    ans(g, 2, False)
    assert "jetlag" not in g.players[1].owned() and "jetlag" in g.players[2].discard
    assert g.supply["jetlag"] == 19 and g.coins == 1


def test_jet_lag_runs_out_quietly():
    g = table(["redeye"], deck=["coins"] * 5)
    g.supply["jetlag"] = 0
    g.act(0, {"type": "play", "card": "redeye"})
    assert "jetlag" not in g.players[1].owned()


def test_connecting_flight_plays_twice():
    g = table(["connecting", "hostel", "redeye"], deck=["coins"] * 10)
    g.act(0, {"type": "play", "card": "connecting"})
    d = g.pending
    assert sorted(d.options) == ["hostel", "redeye"] and d.max == 1
    ans(g, 0, ["hostel"])
    assert g.actions == 4 and len(g.players[0].hand) == 3       # redeye + 2 drawn
    assert g.players[0].play == ["connecting", "hostel"]


def test_connecting_flight_into_an_attack_with_choices():
    g = table(["connecting", "overbooked"], others={1: ["coins"] * 4 + ["postcard", "visa"]})
    g.act(0, {"type": "play", "card": "connecting"})
    ans(g, 0, ["overbooked"])
    assert g.pending.player == 1 and g.pending.min == 2
    ans(g, 1, ["postcard", "visa"])
    assert g.pending is None and g.coins == 4 and len(g.players[1].hand) == 4   # 2nd time: already 4


def test_upgrade_trashes_and_gains():
    g = table(["upgrade", "coins", "goldcard"])
    g.act(0, {"type": "play", "card": "upgrade"})
    assert g.pending.min == g.pending.max == 1
    ans(g, 0, ["goldcard"])
    d = g.pending
    assert d.kind == "gain" and "visa" in d.options and all(CARDS[c].cost <= 9 for c in d.options)
    must_fail(ans, g, 0, "lounge", match="can't gain")          # not in this kingdom
    ans(g, 0, "visa")
    assert "visa" in g.players[0].discard and "goldcard" in g.trash and g.supply["visa"] == 7


def test_upgrade_with_one_card_is_automatic():
    g = table(["upgrade", "jetlag"])
    g.act(0, {"type": "play", "card": "upgrade"})
    assert "jetlag" in g.trash and g.pending.kind == "gain"
    assert max(CARDS[c].cost for c in g.pending.options) == 3


def test_lost_luggage_and_delayed_flight():
    k = ["delayed", "lostluggage", "hostel", "customs", "overbooked", "insurance", "redeye",
         "connecting", "upgrade", "guidebook"]
    g = table(["lostluggage"], kingdom=k)
    g.players[1].deck = ["postcard", "goldcard", "banknotes"]          # top is the end
    g.act(0, {"type": "play", "card": "lostluggage"})
    assert g.trash == ["goldcard"] and "banknotes" in g.players[1].discard
    assert g.players[1].deck == ["postcard"]

    g = table(["delayed"], kingdom=k, deck=["coins"] * 4,
              others={1: ["visa", "coins", "coins", "coins"]})
    g.act(0, {"type": "play", "card": "delayed"})
    assert g.pending.player == 1
    ans(g, 1, ["visa"])
    assert g.players[1].deck[-1] == "visa" and len(g.players[1].hand) == 3
    assert "visa" not in g.log[-1]["t"]                          # hidden: which card went back


def test_window_seat_hitchhike_cubes_concierge_exchange():
    k = ["windowseat", "hitchhike", "packingcubes", "concierge", "exchange", "hostel", "customs",
         "overbooked", "insurance", "redeye"]
    g = table(["windowseat"], kingdom=k, deck=["visa", "coins"])
    g.act(0, {"type": "play", "card": "windowseat"})
    assert g.pending.kind == "yesno" and g.pending.show == ["visa"]
    assert "show" not in g.view(1)["pending"]
    ans(g, 0, True)
    assert g.players[0].discard == ["visa"]

    g = table(["hitchhike"], kingdom=k, deck=["postcard", "hostel"])
    g.act(0, {"type": "play", "card": "hitchhike"})
    assert g.players[0].hand == ["hostel"] and g.actions == 2

    g = table(["packingcubes", "postcard", "jetlag", "coins"], kingdom=k)
    g.act(0, {"type": "play", "card": "packingcubes"})
    ans(g, 0, ["postcard", "jetlag"])
    assert g.coins == 2 and g.actions == 1

    g = table(["concierge", "postcard"], kingdom=k, deck=["coins", "coins"])
    g.act(0, {"type": "play", "card": "concierge"})
    ans(g, 0, ["postcard"])
    assert g.coins == 1 and len(g.players[0].hand) == 2

    g = table(["exchange", "coins", "coins", "coins", "banknotes"], kingdom=k)
    g.act(0, {"type": "play", "card": "exchange"})
    assert g.pending.options == ["coins"] * 3
    ans(g, 0, ["coins", "coins"])
    assert g.coins == 4 and g.buys == 2 and g.trash == ["coins", "coins"]


def test_souvenir_shop_group_tour_miles_card():
    k = ["souvenirshop", "grouptour", "milescard", "lounge", "hostel", "customs", "overbooked",
         "insurance", "redeye", "dutyfree"]
    g = table(["souvenirshop"], kingdom=k)
    g.act(0, {"type": "play", "card": "souvenirshop"})
    assert all(CARDS[c].cost <= 4 for c in g.pending.options) and "lounge" not in g.pending.options
    ans(g, 0, "hostel")
    assert g.players[0].discard == ["hostel"]

    g = table(["grouptour"], kingdom=k, deck=["coins"] * 3, others={1: ["coins"]})
    g.players[1].deck = ["coins"]
    g.act(0, {"type": "play", "card": "grouptour"})
    assert len(g.players[0].hand) == 3 and g.buys == 2 and len(g.players[1].hand) == 2

    g = table(["milescard", "coins"], kingdom=k)
    g.act(0, {"type": "play_money"})
    assert g.coins == 3 and g.buys == 2


def test_light_packer_and_reshuffle():
    k = ["lightpacker", "hostel", "customs", "overbooked", "insurance", "redeye", "connecting",
         "upgrade", "guidebook", "backpack"]
    g = table(["lightpacker", "postcard"], kingdom=k, deck=[])
    g.players[0].discard = ["coins", "goldcard"]
    g.act(0, {"type": "play", "card": "lightpacker"})       # empty deck: discard is shuffled in
    assert len(g.players[0].hand) == 2 and len(g.players[0].deck) + len(g.players[0].discard) == 1
    ans(g, 0, ["postcard"])
    assert g.trash == ["postcard"]


def test_scrapbook_scores_by_variety():
    owned = ["coins", "postcard", "banknotes", "scrapbook", "hostel", "visa", "stamp", "redeye"]
    assert score(owned) == 1 + 2 + 6 + 3          # 8 names -> 2 VP each scrapbook
    assert score(["scrapbook", "jetlag", "jetlag"]) == -2


# ---------------- ending ----------------
def test_game_ends_on_visas_and_three_piles():
    g = table(["coins"] * 5)
    g.supply["visa"] = 0
    g.act(0, {"type": "play_money"})
    g.act(0, {"type": "end_phase"})
    assert g.over and g.result["reason"].startswith("The Visa") and g.waiting_on() is None
    must_fail(g.act, 1, {"type": "end_phase"}, match="over")

    g = table(["coins"] * 5)
    for c in ("hostel", "customs"):
        g.supply[c] = 0
    g.supply["redeye"] = 1
    g.coins = 5
    g.act(0, {"type": "buy", "card": "redeye"})
    assert g.over and g.result["reason"].startswith("Three")


def test_ties_go_to_fewer_turns_then_share():
    g = table(["coins"] * 5)
    g.players[0].turns, g.players[1].turns = 5, 4
    for p in g.players:
        p.deck, p.hand, p.discard, p.play = ["visa"], [], [], []
    g._finish()
    assert g.result["winners"] == [1]
    g.over = False
    g.players[0].turns = 4
    g._finish()
    assert g.result["winners"] == [0, 1] and "share" in g.log[-1]["t"]


def test_round_limit_ends_stalled_games():
    g = Game(["a", "b"], PRESETS["first"]["cards"], seed=3, round_limit=3)
    for _ in range(50):
        if g.over:
            break
        g.act(g.waiting_on(), {"type": "end_phase"})
    assert g.over and "rounds" in g.result["reason"] and all(p.turns == 3 for p in g.players)


# ---------------- hidden information ----------------
def test_views_hide_other_hands():
    g = new_game(3)
    for viewer in range(3):
        v = g.view(viewer)
        assert v["hand"] == g.players[viewer].hand
        assert all(set(p) == {"name", "hand", "deck", "discard", "discard_top", "owned", "vp", "turns"}
                   for p in v["players"])
        assert all(isinstance(p["hand"], int) for p in v["players"])
    assert "hand" not in g.view(None)


# ---------------- bots ----------------
def test_many_bot_games_end_and_conserve_cards():
    games = 0
    for kc in ALL_SETS:
        for n in (2, 3, 4):
            for s in range(25):
                seed = ALL_SETS.index(kc) * 1000 + n * 100 + s
                rng = random.Random(seed)
                levels = [rng.choice(LEVELS) for _ in range(n)]
                g = play_bot_game(levels, pick_kingdom(kc, rng), seed)
                assert g.over and g.result and g.count_cards() == g.total_cards, (kc, n, s)
                assert len(g.result["ranking"]) == n
                games += 1
    assert games == 300


def test_random_players_never_break_the_engine():
    """Fuzz: players that make random legal (and some illegal) moves and random answers."""
    for s in range(60):
        rng = random.Random(s)
        n = 2 + s % 3
        g = Game([f"R{i}" for i in range(n)], pick_kingdom("random", rng), seed=s, round_limit=25)
        steps = 0
        while not g.over:
            steps += 1
            assert steps < 20000
            who = g.waiting_on()
            if g.pending:
                if rng.random() < 0.05:
                    must_fail(g.act, who, {"type": "answer", "id": g.pending.id, "value": ["visa"] * 9})
                g.act(who, {"type": "answer", "id": g.pending.id, "value": random_answer(g.pending, rng)})
                continue
            p = g.players[who]
            moves = [{"type": "end_phase"}] * 2
            moves += [{"type": "play", "card": c} for c in p.hand
                      if (CARDS[c].is_("action") and g.phase == "action" and g.actions)
                      or CARDS[c].is_("currency")]
            moves += [{"type": "buy", "card": c} for c, k in g.supply.items()
                      if k and CARDS[c].cost <= g.coins and g.buys]
            if rng.random() < 0.05:
                must_fail(g.act, (who + 1) % n, {"type": "end_phase"})
            g.act(who, rng.choice(moves))
            assert g.count_cards() == g.total_cards
        assert g.count_cards() == g.total_cards


def test_every_bot_answers_every_decision_legally():
    """Each level, in every kingdom card's company, answers without the random fallback."""
    import bots as bots_mod
    calls = Counter()
    real = bots_mod.random_answer

    def spy(d, rng):
        calls[d.card] += 1
        return real(d, rng)
    bots_mod.random_answer = spy
    try:
        for level in LEVELS:
            for i, card in enumerate(KINGDOM_IDS):
                rng = random.Random(i)
                k = [card] + rng.sample([c for c in KINGDOM_IDS if c != card], 9)
                g = Game(["a", "b", "c"], k, seed=i)
                b = [Bot(level, seed=i), Bot(level, seed=i + 1), Bot(level, seed=i + 2)]
                b[0].rng.random = lambda: 0.99          # easy: never answer at random on purpose
                b[1].rng.random = b[2].rng.random = b[0].rng.random
                while not g.over:
                    w = g.waiting_on()
                    g.act(w, b[w].move(g, w))
    finally:
        bots_mod.random_answer = real
    assert not calls, calls


def test_hard_beats_easy_most_of_the_time():
    for kc in ALL_SETS:
        wins = 0
        for s in range(40):
            lv = ["hard", "easy"] if s % 2 else ["easy", "hard"]
            g = play_bot_game(lv, pick_kingdom(kc, random.Random(s)), 1000 + s)
            wins += sum(1 / len(g.result["winners"]) for w in g.result["winners"] if lv[w] == "hard")
        assert wins >= 0.75 * 40, (kc, wins)


def test_hard_holds_its_own_against_three_normals():
    wins = 0
    for s in range(60):
        lv = ["hard", "normal", "normal", "normal"]
        random.Random(s).shuffle(lv)
        g = play_bot_game(lv, pick_kingdom(ALL_SETS[s % 4], random.Random(s)), 2000 + s)
        wins += sum(1 / len(g.result["winners"]) for w in g.result["winners"] if lv[w] == "hard")
    assert wins >= 0.33 * 60, wins          # a fair share would be 25%


# ---------------- rooms and the API ----------------
def wait_until(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def post(path, data, code=200):
    r = c.post(path, json=data, headers=H)
    assert r.status_code == code, (path, r.status_code, r.text)
    return r.json()


def test_page_and_health():
    html = c.get("/").text
    assert "api/rooms" in html and '"/api/' not in html and "`/api/" not in html
    assert not re.search(r'(src|href)="(https?:|//|/[^/])', html), re.findall(r'(?:src|href)="[^"]*"', html)
    assert not re.search(r"https?://", html)
    assert c.get("/health").json() == {"ok": True}
    assert c.get("/icon.svg").headers["content-type"].startswith("image/svg")
    assert len(c.get("/api/cards").json()["cards"]) == 31


def test_posts_need_the_header():
    r = c.post("/api/rooms", json={"name": "x"})
    assert r.status_code == 403
    assert c.post("/api/rooms", content=b"nope", headers=H).status_code == 400


def test_lobby_join_bots_and_start():
    a = post("/api/rooms", {"name": "Alice <b>"})
    code, ta = a["code"], a["token"]
    assert re.fullmatch(r"[A-HJKMNP-Z]{4}", code) and a["state"]["host"] and a["state"]["state"] == "lobby"
    assert any(r["code"] == code for r in c.get("/api/rooms").json()["rooms"])
    b = post(f"/api/rooms/{code.lower()}/join", {"name": "Bob"})
    tb = b["token"]
    assert b["state"]["you"] == 1 and not b["state"]["host"]
    again = post(f"/api/rooms/{code}/join", {"name": "Bob", "token": tb})       # same seat back
    assert again["token"] == tb and again["state"]["you"] == 1 and len(again["state"]["seats"]) == 2
    post(f"/api/rooms/{code}/lobby", {"token": tb, "op": "add_bot", "level": "hard"}, 403)
    post(f"/api/rooms/{code}/start", {"token": tb}, 403)
    post(f"/api/rooms/{code}/lobby", {"token": ta, "op": "add_bot", "level": "superhard"}, 400)
    st = post(f"/api/rooms/{code}/lobby", {"token": ta, "op": "add_bot", "level": "hard"})
    assert st["seats"][2]["bot"] == "hard"
    st = post(f"/api/rooms/{code}/lobby", {"token": ta, "op": "bot_level", "seat": 2, "level": "easy"})
    assert st["seats"][2]["bot"] == "easy"
    post(f"/api/rooms/{code}/lobby", {"token": ta, "op": "remove", "seat": 1}, 400)      # people stay
    post(f"/api/rooms/{code}/lobby", {"token": ta, "op": "kingdom", "kingdom": "moon"}, 400)
    st = post(f"/api/rooms/{code}/lobby", {"token": ta, "op": "kingdom", "kingdom": "backpacker"})
    assert st["kingdom_choice"] == "backpacker"
    st = post(f"/api/rooms/{code}/lobby", {"token": tb, "op": "rename", "name": "  Bobby   B "})
    assert st["seats"][1]["name"] == "Bobby B"
    st = post(f"/api/rooms/{code}/start", {"token": ta})
    assert st["state"] == "playing" and sorted(st["game"]["kingdom"]) == sorted(PRESETS["backpacker"]["cards"])
    assert not any(r["code"] == code for r in c.get("/api/rooms").json()["rooms"])
    post(f"/api/rooms/{code}/join", {"name": "Late"}, 409)
    post(f"/api/rooms/{code}/start", {"token": ta}, 409)
    # each person sees only their own hand
    room = passport.rooms.get(code)
    va = c.get(f"/api/rooms/{code}/state", params={"token": ta}).json()
    vb = c.get(f"/api/rooms/{code}/state", params={"token": tb}).json()
    assert va["game"]["hand"] == room.game.players[0].hand and vb["game"]["hand"] == room.game.players[1].hand
    assert "hand" not in c.get(f"/api/rooms/{code}/state").json()["game"]
    # moves out of turn are refused
    g = room.game
    wait_until(lambda: g.waiting_on() in (0, 1))
    w = g.waiting_on()
    other = tb if w == 0 else ta
    if not g.pending:
        r = c.post(f"/api/rooms/{code}/act", json={"token": other, "intent": {"type": "end_phase"}}, headers=H)
        assert r.status_code == 400 and "turn" in r.json()["error"]
    r = c.post(f"/api/rooms/{code}/act", json={"token": "nope", "intent": {"type": "end_phase"}}, headers=H)
    assert r.status_code == 403
    finish_with_bots(code, {0: ta, 1: tb})
    st = c.get(f"/api/rooms/{code}/state", params={"token": ta}).json()
    assert st["state"] == "over" and st["game"]["result"]["ranking"]
    # rematch: same room, same players, a new game
    st = post(f"/api/rooms/{code}/rematch", {"token": tb})
    assert st["state"] == "playing" and st["game_no"] == 2 and [s["name"] for s in st["seats"]][:2] == ["Alice <b>", "Bobby B"]
    post(f"/api/rooms/{code}/rematch", {"token": tb}, 409)


def finish_with_bots(code, tokens, limit=5000):
    """Play the people's seats through the API with a bot's choices; the server bots play theirs."""
    room = passport.rooms.get(code)
    helpers = {i: Bot("normal", seed=i) for i in tokens}
    for _ in range(limit):
        g = room.game
        if g.over:
            return
        w = g.waiting_on()
        if w in tokens and not room.bot_controls(w):
            post(f"/api/rooms/{code}/act", {"token": tokens[w], "intent": helpers[w].move(g, w)})
        else:
            v = room.version
            c.get(f"/api/rooms/{code}/state", params={"token": tokens[min(tokens)], "since": v})
    raise AssertionError("game did not finish")


def test_solo_game_vs_bots_long_poll():
    r = post("/api/rooms", {"name": "Solo", "bots": ["easy", "normal", "hard"], "kingdom": "random", "start": True})
    code, tok = r["code"], r["token"]
    assert r["state"]["state"] == "playing" and len(r["state"]["seats"]) == 4
    room = passport.rooms.get(code)
    # bots play until it's my move
    assert wait_until(lambda: room.game.waiting_on() == 0 or room.game.over)
    v = room.version
    t0 = time.time()
    st = c.get(f"/api/rooms/{code}/state", params={"token": tok, "since": v}).json()
    assert st["version"] == v and 0.8 < time.time() - t0 < 3        # nothing moved: waited ~1 s
    st = c.get(f"/api/rooms/{code}/state", params={"token": tok, "since": v - 1}).json()
    assert st["version"] == v                                         # behind: returns at once
    finish_with_bots(code, {0: tok})
    assert room.game.over and room.game.count_cards() == room.game.total_cards
    post("/api/rooms", {"name": "x", "bots": ["easy"] * 4}, 400)
    post("/api/rooms", {"name": "x", "start": True}, 400)


def test_leaving_hands_the_seat_to_a_bot_and_coming_back_reclaims_it():
    a = post("/api/rooms", {"name": "Ann"})
    code, ta = a["code"], a["token"]
    tb = post(f"/api/rooms/{code}/join", {"name": "Ben"})["token"]
    post(f"/api/rooms/{code}/start", {"token": ta})
    room = passport.rooms.get(code)
    post(f"/api/rooms/{code}/leave", {"token": tb})
    st = c.get(f"/api/rooms/{code}/state", params={"token": ta}).json()
    assert st["seats"][1]["left"] and st["seats"][1]["away"]
    # Ben's turns are now played by a bot
    for _ in range(300):
        g = room.game
        if g.over or g.players[1].turns >= 2:
            break
        if g.waiting_on() == 0:
            post(f"/api/rooms/{code}/act", {"token": ta, "intent": Bot("normal", 1).move(g, 0)})
        else:
            c.get(f"/api/rooms/{code}/state", params={"token": ta, "since": room.version})
    assert room.game.players[1].turns >= 2 or room.game.over
    # Ben comes back with his token: same seat, no longer away
    st = post(f"/api/rooms/{code}/join", {"name": "Ben", "token": tb})
    assert st["state"]["you"] == 1 and not st["state"]["seats"][1]["away"]
    # the host leaving passes the host role on
    post(f"/api/rooms/{code}/leave", {"token": ta})
    assert room.host == tb


def test_idle_players_are_covered_then_reclaim():
    a = post("/api/rooms", {"name": "Idle", "bots": ["normal"]})
    code, ta = a["code"], a["token"]
    tb = post(f"/api/rooms/{code}/join", {"name": "Watcher"})["token"]
    post(f"/api/rooms/{code}/start", {"token": ta})
    room = passport.rooms.get(code)
    old, old_poll = rooms_mod.TAKEOVER_SECONDS, passport.POLL_SECONDS
    rooms_mod.TAKEOVER_SECONDS, passport.POLL_SECONDS = 0.3, 0.1
    try:
        time.sleep(0.4)
        # seats: Idle, a bot, Watcher. Watcher keeps polling; Idle doesn't, so a bot covers Idle.
        for _ in range(400):
            if room.game.players[0].turns >= 3 or room.game.over:
                break
            room.seats[2].seen = time.monotonic()
            if room.game.waiting_on() == 2 and not room.bot_controls(2):
                post(f"/api/rooms/{code}/act", {"token": tb, "intent": Bot("normal", 2).move(room.game, 2)})
            else:
                c.get(f"/api/rooms/{code}/state", params={"token": tb, "since": room.version})
        assert room.game.players[0].turns >= 3 or room.game.over
        assert room.view(tb)["seats"][0]["away"] or room.game.over
        c.get(f"/api/rooms/{code}/state", params={"token": ta, "since": 0})
        assert not room.away(0)
    finally:
        rooms_mod.TAKEOVER_SECONDS, passport.POLL_SECONDS = old, old_poll


def test_everyone_away_pauses_the_game():
    a = post("/api/rooms", {"name": "Gone", "bots": ["normal"], "start": True})
    code, ta = a["code"], a["token"]
    room = passport.rooms.get(code)
    post(f"/api/rooms/{code}/leave", {"token": ta})
    time.sleep(0.3)
    v = room.version
    time.sleep(0.3)
    assert room.version == v and not room.game.over


def test_unknown_rooms_and_room_cap():
    assert c.get("/api/rooms/ZZZZ/state").status_code == 404
    post("/api/rooms/ZZZZ/join", {"name": "x"}, 404)
    old = rooms_mod.MAX_ROOMS
    rooms_mod.MAX_ROOMS = len(passport.rooms.rooms)
    try:
        post("/api/rooms", {"name": "x"}, 503)
    finally:
        rooms_mod.MAX_ROOMS = old


def test_lobby_leave_and_forget():
    a = post("/api/rooms", {"name": "Brief"})
    code = a["code"]
    post(f"/api/rooms/{code}/leave", {"token": a["token"]})
    assert code not in passport.rooms.rooms
    a = post("/api/rooms", {"name": "Old"})
    passport.rooms.get(a["code"]).touched -= rooms_mod.FORGET_AFTER + 1
    passport.rooms.sweep()
    assert a["code"] not in passport.rooms.rooms


if __name__ == "__main__":
    failed = 0
    with TestClient(passport.app) as c:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                t = time.time()
                try:
                    fn()
                    print(f"ok   {name} ({time.time() - t:.1f}s)")
                except Exception:
                    failed += 1
                    import traceback
                    traceback.print_exc()
                    print("FAIL", name)
    print(f"{failed} failed" if failed else "all passed")
    sys.exit(1 if failed else 0)
