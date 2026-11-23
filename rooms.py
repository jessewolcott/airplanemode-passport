"""Passport's rooms: the game on top of amrooms (the platform's Rooms SDK).

amrooms does the seats, tokens, host, leave/rejoin, presence, long-poll and the driver loop.
This file adds the card set choice, starting games, moves and what the driver does.

A person who leaves a started game is played by a "normal" bot at once and stays gone until
they join again with their token (the next game turns them into a bot for good). A person who
sends no request for away_after s (PASSPORT_TAKEOVER) counts as away; while away, a "normal" bot moves for them
whenever the game waits on their seat, until their next request. If every person is away, the
game pauses instead of playing itself out.
"""
import logging
import os
import random
import secrets
import time

from amrooms import Room, RoomError, Rooms, env_float

from bots import LEVELS, Bot, random_answer
from cards import KINGDOM_CHOICES
from engine import Game, IllegalMove, pick_kingdom

log = logging.getLogger("passport")


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


class PassRoom(Room):
    max_seats = 4
    bot_levels = LEVELS
    bot_names = ("Ada", "Basil", "Coco", "Dex", "Esme", "Fitz", "Gigi", "Hugo", "Ines", "Juno")
    away_after = env_float("PASSPORT_TAKEOVER", 60)
    poll_wait = env_float("PASSPORT_POLL", 20)

    def __init__(self, code, seed=None):
        super().__init__(code, seed)
        self.kingdom = "first"
        self.games_played = 0
        self.bots = {}                   # seat index -> Bot (bots and taken-over people)
        self.next_delay = bot_delay()
        self.stuck = None                # version at which no bot move worked (don't spin)

    def playing(self):
        return self.game is not None and not self.game.over

    def changed(self):
        super().changed()
        self.next_delay = bot_delay()

    # ---------------- seats ----------------
    def add_person(self, name, who=None):
        return self.join(name, who=who)

    def bot_controls(self, i):
        return i is not None and (not self.seats[i].human or self.away(i))

    def remove_seat(self, i, people=False):
        super().remove_seat(i, people=people)
        self.game = None          # seats moved: the old result no longer lines up

    def set_kingdom(self, choice):
        if choice not in KINGDOM_CHOICES:
            raise RoomError(400, "Unknown card set.")
        if self.playing():
            raise RoomError(409, "The game has started.")
        self.kingdom = choice
        self.changed()

    # ---------------- games ----------------
    def start(self, seed=None):
        if self.playing():
            raise RoomError(409, "The game has already started.")
        if not 2 <= len(self.seats) <= self.max_seats:
            raise RoomError(400, "Passport needs 2 to 4 players: add a bot or wait for a friend.")
        for s in self.seats:      # people who left for good become bots for the next game
            if s.human and s.left:
                s.make_bot("normal")
        seed = secrets.randbits(32) if seed is None else seed
        kingdom = pick_kingdom(self.kingdom, random.Random(seed))
        self.game = Game([s.name for s in self.seats], kingdom, seed=seed)
        self.bots = {}
        self.games_played += 1
        self.changed()
        self.ensure_driver()

    def act(self, token, intent):
        i = self.need_present(token)
        if self.game is None:
            raise RoomError(409, "The game hasn't started.")
        try:
            self.game.act(i, intent)
        except IllegalMove as e:
            raise RoomError(400, str(e))
        self.changed()

    def bot_for(self, i):
        if i not in self.bots:
            level = self.seats[i].level or "normal"
            self.bots[i] = Bot(level, seed=secrets.randbits(32))
        return self.bots[i]

    # ---------------- the driver ----------------
    def plan(self, now):
        g = self.game
        if g is None or g.over or self.stuck == self.version or not self.anyone_here(now):
            return None
        who = g.waiting_on()
        if who is None:
            return None
        if self.bot_controls(who):
            return (self.changed_at + self.next_delay, lambda: self._bot_move(g, who))
        # a person's turn: wake up when they'd count as away, in case they never come back
        return (self.seats[who].seen + self.away_after + 0.01, lambda: False)

    def _bot_move(self, g, who):
        if self.game is not g or g.over or g.waiting_on() != who or not self.bot_controls(who):
            return False
        try:
            g.act(who, self.bot_for(who).move(g, who))
            return True
        except Exception:                      # a bot bug must not freeze the table
            log.exception("bot move failed in room %s", self.code)
        try:
            if g.pending and g.pending.player == who:
                g.act(who, {"type": "answer", "id": g.pending.id,
                            "value": random_answer(g.pending, random.Random())})
            else:
                g.act(who, {"type": "end_phase"})
            return True
        except Exception:
            log.exception("fallback move failed in room %s", self.code)
            self.stuck = self.version
            return False

    # ---------------- what one person sees ----------------
    def view(self, token=None):
        me = self.seat_of(token)
        now = time.monotonic()
        g = self.game
        state = "lobby" if g is None else ("over" if g.over else "playing")
        running = g is not None and not g.over
        return {
            "code": self.code,
            "version": self.version,
            "state": state,
            "game_no": self.games_played,
            "you": me,
            "host": self.is_host(me),
            "kingdom_choice": self.kingdom,
            "seats": [{"name": s.name, "avatar": s.avatar, "bot": s.level if not s.human else None,
                       "away": self.away(i, now) and running,
                       "left": s.left, "host": s.human and s is self.host, "you": i == me}
                      for i, s in enumerate(self.seats)],
            "game": g.view(me) if g is not None else None,
        }

    def lobby_summary(self):
        return {"code": self.code, "host": self.host.name if self.host else "?",
                "players": len(self.seats), "kingdom": self.kingdom}


def make_rooms():
    return Rooms(PassRoom, max_rooms=200, idle=env_float("PASSPORT_ROOM_TTL", 2 * 3600))
