"""Rooms: who sits where, lobbies, bots that play their seats, long-poll versions.

Everything is in memory. A room has a 4-letter code, up to 4 seats (people or bots) and at most
one game at a time. People hold a secret seat token (kept in the browser), so a refresh or a
locked phone gets the same seat back. The state each person gets is drawn for their seat only
(engine.Game.view), so nobody sees another hand.

Bots play on the server as one asyncio task per room (`Room.ensure_driver`), with a short delay
so people can follow. A person who leaves a started game, or stops polling for TAKEOVER_SECONDS
while the game waits on them, is played by a "normal" bot until they come back. If every
person is away, the game pauses instead of playing itself out.
"""
import asyncio
import logging
import os
import random
import secrets
import time

from bots import LEVELS, Bot
from cards import KINGDOM_CHOICES
from engine import Game, IllegalMove, pick_kingdom

log = logging.getLogger("passport")

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ"     # no I, L, O: easy to read out loud
MAX_ROOMS = 200
MAX_SEATS = 4
FORGET_AFTER = float(os.getenv("PASSPORT_ROOM_TTL", 2 * 3600))
TAKEOVER_SECONDS = float(os.getenv("PASSPORT_TAKEOVER", 60))
BOT_NAMES = ["Ada", "Basil", "Coco", "Dex", "Esme", "Fitz", "Gigi", "Hugo", "Ines", "Juno"]


def bot_delay():
    """Seconds a bot waits before each move: PASSPORT_BOT_DELAY, "0.7-1.2" or "0" (tests)."""
    raw = os.getenv("PASSPORT_BOT_DELAY", "0.7-1.2")
    try:
        lo, _, hi = raw.partition("-")
        lo = float(lo)
        hi = float(hi) if hi else lo
        return random.uniform(lo, max(lo, hi))
    except ValueError:
        return 1.0


def clean_name(name, fallback="Traveller"):
    name = " ".join(str(name or "").split())[:20]
    return "".join(ch for ch in name if ch.isprintable()) or fallback


class RoomError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class Seat:
    def __init__(self, name, token=None, level=None):
        self.name, self.token, self.level = name, token, level
        self.seen = time.monotonic()
        self.left = False

    @property
    def human(self):
        return self.token is not None


class Room:
    def __init__(self, code):
        self.code = code
        self.seats = []
        self.host = None                 # token of the host
        self.kingdom = "first"
        self.game = None
        self.games_played = 0
        self.bots = {}                   # seat index -> Bot (bots and taken-over people)
        self.version = 1
        self._changed = asyncio.Event()
        self.touched = time.monotonic()
        self.task = None

    # ---------------- versions / long-poll ----------------
    def bump(self):
        self.version += 1
        self.touched = time.monotonic()
        ev, self._changed = self._changed, asyncio.Event()
        ev.set()

    async def wait(self, since, timeout):
        if self.version != since:
            return
        ev = self._changed
        try:
            await asyncio.wait_for(ev.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    # ---------------- seats ----------------
    def seat_of(self, token):
        if not token:
            return None
        for i, s in enumerate(self.seats):
            if s.token and secrets.compare_digest(s.token, str(token)):
                return i
        return None

    def need_seat(self, token):
        i = self.seat_of(token)
        if i is None:
            raise RoomError(403, "You're not in this room.")
        return i

    def need_host(self, token):
        i = self.need_seat(token)
        if self.seats[i].token != self.host:
            raise RoomError(403, "Only the host can do that.")
        return i

    def seen(self, token):
        i = self.seat_of(token)
        if i is not None:
            s = self.seats[i]
            s.seen = time.monotonic()
            if s.left:
                s.left = False
                self.bump()
        return i

    def away(self, i, now=None):
        s = self.seats[i]
        if not s.human:
            return False
        return s.left or (now or time.monotonic()) - s.seen > TAKEOVER_SECONDS

    def bot_controls(self, i):
        return i is not None and (not self.seats[i].human or self.away(i))

    def anyone_here(self):
        return any(s.human and not self.away(i) for i, s in enumerate(self.seats))

    def humans(self):
        return [s for s in self.seats if s.human]

    def add_person(self, name):
        if self.game is not None:
            raise RoomError(409, "That game has already started.")
        if len(self.seats) >= MAX_SEATS:
            raise RoomError(409, "That room is full (4 players).")
        token = secrets.token_urlsafe(18)
        self.seats.append(Seat(clean_name(name, f"Traveller {len(self.seats) + 1}"), token))
        if self.host is None:
            self.host = token
        self.bump()
        return token

    def add_bot(self, level):
        if level not in LEVELS:
            raise RoomError(400, "Bots are easy, normal or hard.")
        if self.game is not None and not self.game.over:
            raise RoomError(409, "The game has started.")
        if len(self.seats) >= MAX_SEATS:
            raise RoomError(409, "The room is full (4 players).")
        used = {s.name for s in self.seats}
        name = next((n for n in BOT_NAMES if n not in used), f"Bot {len(self.seats) + 1}")
        self.seats.append(Seat(name, level=level))
        self.bump()

    def remove_seat(self, i):
        if self.game is not None and not self.game.over:
            raise RoomError(409, "The game has started.")
        if not 0 <= i < len(self.seats):
            raise RoomError(404, "No such seat.")
        if self.seats[i].human:
            raise RoomError(400, "Only bots can be removed.")
        del self.seats[i]
        self.game = None          # seats moved: the old result no longer lines up
        self.bump()

    def set_bot_level(self, i, level):
        if level not in LEVELS or not 0 <= i < len(self.seats) or self.seats[i].human:
            raise RoomError(400, "Pick a bot and a level.")
        if self.game is not None and not self.game.over:
            raise RoomError(409, "The game has started.")
        self.seats[i].level = level
        self.bump()

    def set_kingdom(self, choice):
        if choice not in KINGDOM_CHOICES:
            raise RoomError(400, "Unknown card set.")
        if self.game is not None and not self.game.over:
            raise RoomError(409, "The game has started.")
        self.kingdom = choice
        self.bump()

    def rename(self, i, name):
        self.seats[i].name = clean_name(name, self.seats[i].name)
        self.bump()

    def leave(self, token):
        i = self.need_seat(token)
        if self.game is None:
            del self.seats[i]
        else:
            self.seats[i].left = True
        if self.host == token:
            nxt = next((s.token for s in self.seats if s.human and not s.left), None)
            self.host = nxt
        self.bump()
        self.ensure_driver()

    def empty(self):
        return not any(s.human and not s.left for s in self.seats)

    # ---------------- games ----------------
    def start(self, seed=None):
        if self.game is not None and not self.game.over:
            raise RoomError(409, "The game has already started.")
        if not 2 <= len(self.seats) <= MAX_SEATS:
            raise RoomError(400, "Passport needs 2 to 4 players: add a bot or wait for a friend.")
        # people who left for good become bots for the next game
        for s in self.seats:
            if s.human and s.left:
                s.token, s.level, s.left = None, "normal", False
        seed = secrets.randbits(32) if seed is None else seed
        kingdom = pick_kingdom(self.kingdom, random.Random(seed))
        self.game = Game([s.name for s in self.seats], kingdom, seed=seed)
        self.bots = {}
        self.games_played += 1
        self.bump()
        self.ensure_driver()

    def act(self, token, intent):
        i = self.need_seat(token)
        if self.game is None:
            raise RoomError(409, "The game hasn't started.")
        try:
            self.game.act(i, intent)
        except IllegalMove as e:
            raise RoomError(400, str(e))
        self.bump()
        self.ensure_driver()

    def bot_for(self, i):
        if i not in self.bots:
            level = self.seats[i].level or "normal"
            self.bots[i] = Bot(level, seed=secrets.randbits(32))
        return self.bots[i]

    def ensure_driver(self):
        g = self.game
        if g is None or g.over or not self.bot_controls(g.waiting_on()) or not self.anyone_here():
            return
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self._drive())

    async def _drive(self):
        while True:
            g = self.game
            if g is None or g.over:
                return
            who = g.waiting_on()
            if not self.bot_controls(who) or not self.anyone_here():
                return
            await asyncio.sleep(bot_delay())
            if self.game is not g or g.over or g.waiting_on() != who or not self.bot_controls(who):
                continue
            try:
                g.act(who, self.bot_for(who).move(g, who))
            except Exception:                      # a bot bug must not freeze the table
                log.exception("bot move failed in room %s", self.code)
                try:
                    if g.pending and g.pending.player == who:
                        from bots import random_answer
                        g.act(who, {"type": "answer", "id": g.pending.id,
                                    "value": random_answer(g.pending, random.Random())})
                    else:
                        g.act(who, {"type": "end_phase"})
                except Exception:
                    log.exception("fallback move failed in room %s", self.code)
                    return
            self.bump()

    # ---------------- what one person sees ----------------
    def view(self, token=None):
        me = self.seat_of(token)
        now = time.monotonic()
        g = self.game
        state = "lobby" if g is None else ("over" if g.over else "playing")
        return {
            "code": self.code,
            "version": self.version,
            "state": state,
            "game_no": self.games_played,
            "you": me,
            "host": me is not None and self.seats[me].token == self.host,
            "kingdom_choice": self.kingdom,
            "seats": [{"name": s.name, "bot": s.level if not s.human else None,
                       "away": self.away(i, now) and g is not None and not g.over,
                       "left": s.left, "host": s.human and s.token == self.host, "you": i == me}
                      for i, s in enumerate(self.seats)],
            "game": g.view(me) if g is not None else None,
        }


class Rooms:
    def __init__(self):
        self.rooms = {}

    def new_code(self):
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
            if code not in self.rooms:
                return code

    def create(self):
        self.sweep()
        if len(self.rooms) >= MAX_ROOMS:
            oldest = min(self.rooms.values(), key=lambda r: r.touched)
            if time.monotonic() - oldest.touched < 10 * 60:
                raise RoomError(503, "Too many games on this hotspot right now.")
            self.rooms.pop(oldest.code)
        room = Room(self.new_code())
        self.rooms[room.code] = room
        return room

    def get(self, code):
        room = self.rooms.get(str(code or "").upper())
        if room is None:
            raise RoomError(404, "No game with that code.")
        return room

    def open_lobbies(self):
        out = [r for r in self.rooms.values() if r.game is None and r.humans() and len(r.seats) < MAX_SEATS]
        out.sort(key=lambda r: -r.touched)
        return [{"code": r.code, "host": next((s.name for s in r.seats if s.token == r.host), "?"),
                 "players": len(r.seats), "kingdom": r.kingdom} for r in out[:20]]

    def sweep(self, now=None):
        """Forget idle rooms and empty ones; restart bots where people have gone idle."""
        now = now or time.monotonic()
        for code, r in list(self.rooms.items()):
            if now - r.touched > FORGET_AFTER or (r.empty() and now - r.touched > 60):
                if r.task and not r.task.done():
                    r.task.cancel()
                del self.rooms[code]
            elif r.game is not None and not r.game.over:
                r.ensure_driver()
