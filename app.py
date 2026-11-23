"""Passport: a travel-themed deck-building card game for 2-4 players on the hotspot, with bots.

Everyone starts with a small deck (7 Coins, 3 Postcards), draws 5 cards a turn, plays actions,
buys cards from a shared market of 10 kingdom cards plus money and victory cards, and the
deck grows. Most VP when the Visas (or any three piles) run out wins. Rules: engine.py; cards:
cards.py; bots: bots.py; rooms, seats and long-polling: rooms.py.

API (relative to the app's root; the platform serves it at /passport/):
  GET  health
  GET  api/cards                               every card and the kingdom presets
  GET  api/rooms                               open lobbies on this hotspot
  POST api/rooms {name, bots?: [level], kingdom?, start?: bool}
                                               -> {code, token, state}; start: true starts the
                                               game at once (needs at least one bot)
  POST api/rooms/{code}/join {name, token?}    a new seat, or your old one back (also after
                                               leaving a running game) -> {token, state}
  GET  api/rooms/{code}/state?token=&since=    long-poll: returns when the version moves (or
                                               PASSPORT_POLL s, default 20). No or a wrong token
                                               gets the public view (spectators: no hand, no
                                               private decision data)
  POST api/rooms/{code}/lobby {token, op, ...} host: add_bot {level}, remove {seat},
                                               bot_level {seat, level}, kingdom {kingdom};
                                               anyone: rename {name}
  POST api/rooms/{code}/start {token}          host
  POST api/rooms/{code}/act {token, intent}    see engine.Game.act (409 after you left)
  POST api/rooms/{code}/rematch {token}        anyone seated who hasn't left, once it's over
  POST api/rooms/{code}/leave {token}          lobby: frees the seat; game: a bot plays it
                                               until you join again with your token
POSTs need the header "X-Passport: 1" (blocks cross-site form posts).
"""
import asyncio
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from amrooms import RoomError, whois

import cards
from rooms import make_rooms

log = logging.getLogger("passport")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HERE = Path(__file__).parent
POLL_SECONDS = float(os.getenv("PASSPORT_POLL", 20))
SWEEP_SECONDS = 5

rooms = make_rooms()


@contextlib.asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(rooms.sweep_forever(SWEEP_SECONDS))
    yield
    task.cancel()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.exception_handler(RoomError)
async def room_error(_request, exc: RoomError):
    return JSONResponse({"error": exc.message}, status_code=exc.status)


@app.exception_handler(HTTPException)
async def http_error(_request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


async def body(request: Request):
    if request.headers.get("x-passport") != "1":
        raise HTTPException(403, "Missing X-Passport header.")
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Send JSON.")
    if not isinstance(data, dict):
        raise HTTPException(400, "Send a JSON object.")
    return data


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/cards")
def all_cards():
    return cards.describe()


@app.get("/api/rooms")
async def list_rooms():               # async: sweep() may start bot tasks on the event loop
    return {"rooms": [r.lobby_summary() for r in rooms.open_lobbies(20)]}   # (sweeps first)


@app.post("/api/rooms")
async def create_room(request: Request):
    data = await body(request)
    bots = data.get("bots") or []
    if not isinstance(bots, list) or len(bots) > 3:
        raise HTTPException(400, "Up to 3 bots.")
    who = await whois(request)
    room = rooms.create()
    token = room.add_person(data.get("name"), who=who)
    try:
        if data.get("kingdom"):
            room.set_kingdom(data["kingdom"])
        for level in bots:
            room.add_bot(level)
        if data.get("start"):
            room.start()
    except RoomError:
        rooms.discard(room)
        raise
    return {"code": room.code, "token": token, "state": room.view(token)}


@app.post("/api/rooms/{code}/join")
async def join_room(code: str, request: Request):
    data = await body(request)
    who = await whois(request)
    room = rooms.get(code)
    # your seat back (also after leaving) if the token or who's who says it's you; else a new one
    token = room.join(data.get("name"), data.get("token"), who=who)
    return {"token": token, "state": room.view(token)}


@app.get("/api/rooms/{code}/state")
async def room_state(code: str, token: str = "", since: int = 0):
    room = rooms.get(code)
    room.seen(token)
    room.ensure_driver()
    await room.wait(since, POLL_SECONDS)
    room.seen(token)
    return room.view(token)


@app.post("/api/rooms/{code}/lobby")
async def lobby(code: str, request: Request):
    data = await body(request)
    room = rooms.get(code)
    op = data.get("op")
    if op == "rename":
        room.rename(room.need_seat(data.get("token")), data.get("name"))
    else:
        room.need_host(data.get("token"))
        if op == "add_bot":
            room.add_bot(data.get("level", "normal"))
        elif op == "remove":
            room.remove_seat(int_or_400(data.get("seat")))
        elif op == "bot_level":
            room.set_bot_level(int_or_400(data.get("seat")), data.get("level"))
        elif op == "kingdom":
            room.set_kingdom(data.get("kingdom"))
        else:
            raise HTTPException(400, "Unknown lobby change.")
    return room.view(data.get("token"))


def int_or_400(v):
    if isinstance(v, bool) or not isinstance(v, int):
        raise HTTPException(400, "Which seat?")
    return v


@app.post("/api/rooms/{code}/start")
async def start(code: str, request: Request):
    data = await body(request)
    room = rooms.get(code)
    room.need_host(data.get("token"))
    room.start()
    return room.view(data.get("token"))


@app.post("/api/rooms/{code}/act")
async def act(code: str, request: Request):
    data = await body(request)
    room = rooms.get(code)
    room.seen(data.get("token"))
    room.act(data.get("token"), data.get("intent"))
    return room.view(data.get("token"))


@app.post("/api/rooms/{code}/rematch")
async def rematch(code: str, request: Request):
    data = await body(request)
    room = rooms.get(code)
    room.need_present(data.get("token"))
    if room.game is None or not room.game.over:
        raise HTTPException(409, "Finish this game first.")
    room.start()
    return room.view(data.get("token"))


@app.post("/api/rooms/{code}/leave")
async def leave(code: str, request: Request):
    data = await body(request)
    room = rooms.get(code)
    room.leave(data.get("token"))
    rooms.sweep()                         # an empty lobby goes at once
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/icon.svg")
def icon():
    return FileResponse(HERE / "static" / "icon.svg", media_type="image/svg+xml")
