"""batnav extension: ParlorNet's Naval Combat for the clients of Batalla Naval (batnav, 1995-97).

ParlorNet is not affiliated with the authors of batnav. Batalla Naval, by Ricardo Quesada with a Windows 3.x
client by Horacio Pena, is a client-server Battleship for up to five players: one server, btserver, holds every
fleet and judges every shot, and its clients are xbtclient (X11, XView), nbtclient (ncurses) and btwin (Windows
3.x). This extension plays btserver's part. The wire format was read from the 0.50 sources and checked against
the built clients; in brief:

    a record           108 bytes, both ways: "BNP", the version 0x11, the player number, a message code
                       (tip0), two values (tip1, tip2) and 100 bytes of text or of a board. Every field is a
                       char, so there is no byte order; the clients read one record per read().
    a board            100 bytes, square i at column i % 10 and row i / 10: 0 unknown or open water, 1 a ship
                       (only in the owner's own board), -1 a miss (water), 2 a hit, 3 a square of a sunk ship.

    from the client                          this extension
    BNJUG name, kind, version                BNJUG: player number 1 and the parlor's name; then BNVER
    BNVER                                    BNVER 0.50, then the greeting and how to sign in (BNMSG lines)
    BNMSG text, to, echo                     /commands (adapters/chatcmd.py) or chat at the table
    BNWRI board                              checked by batnav's rules: BNWRI 1 or 0
    BNSTR (the Start button)                 BNSOL while the game cannot start yet, with a line saying why
    BNHIT x y                                the shot, played at the table (MOV); refused: BNTRN again
    BNREA player [view]                      BNREA: that player's board, ships hidden but the client's own
    BNSTS                                    BNSTS: the table's players and their state
    BNEXT                                    the client leaves: a running game is resigned

    to the client, from the table's events
    the game's start (all fleets ready)      BNSTR with the players' states and names, then BNTRN or BNWHO
    TURN                                     BNTRN for the client's turn, else BNWHO with the shooter
    SHOT at the client's fleet               BNHIT x y result: -1 water, 2 hit, 3 for each square of a sunk ship
    END                                      BNLOS, BNWIN and BNEXT, in btserver's order and words
    CHT                                      BNMSG "name: text"

Player numbers. btserver numbers its players 1 to 5 in the order they connect. Here every client is player 1 in
its own session, and the other seats of its table follow in seat order (2, 3, ...): a number means the same
player to that client for the whole game, and the client's own board is always board 1.

Sign-in. batnav has no accounts: BNJUG carries only a name of up to 16 characters, and nothing else. On a parlor
that signs in by name alone that name is the sign-in (HELO). Elsewhere the name is only the client's claim, and
the player signs in in the message window: /signin NAME PASSWORD. The name goes to the core alone and the
password answers its credential question (common.sign_in_lines); the line is never echoed or logged. A password
the server mailed cannot be used here: its first sign-in is on the web or by telnet, where the player chooses
their own. A name without an account cannot sign up here.

Tables. The message window takes the shared chat commands (adapters/chatcmd.py, docs/chat-commands.md): /invite
NAME, /invite bot, /open, /join TABLE or NAME, /accept NAME, /decline NAME, /list, /close, /resign, /who, /motd,
/help and /quit. Tables made here are NEW navalcombat/1 batnav: batnav's fleet (four ships of one square, three of
two, two of three and one of four, never touching). A batnav client sits down at any batnav-rules Naval Combat
table, wherever it was made, and at no other. Invitations to the player arrive as a line in the message window.

The game. The player fills the board and presses Send Board: the fleet is checked here as btserver checked it,
and once the player has a table whose game has begun, it is placed there ship by ship and made ready. Shooting
starts when every fleet at the table is ready; the client gets BNSTR then. The Start button is not needed (it
gets BNSOL and a line about what the game waits for). The core judges every shot; the client is told of shots
at its own fleet (BNHIT) and reads the other boards (BNREA), with ships hidden until they are sunk, whatever the
client asks for (btserver showed the ships to a client that asked with BNSUP). A client that leaves (BNEXT) or
goes away during a game has it resigned, since a batnav client cannot come back to a game. After the end the
player sends a board again for the next game.

    python3 -m adapters.batnav --core 127.0.0.1:6590 --key batnav.key --port 6514
"""
import argparse
import asyncio
import itertools
import re
import sys
import time

from . import botflag, chatcmd, declare
from .common import (NEWPASS_TEXT, CoreClient, CoreError, Gate, err_text, keepalive, no_wait, parse_invites,
                     parse_tables, peer_addr, sign_in_lines)

PROTOCOL = ("BATNAV", "Batalla Naval (batnav)", "Naval Combat for the batnav clients of 1995-98")
NC = "navalcombat/1"
RULES = "batnav"

# ---- the wire ----------------------------------------------------------------------------------------------
REC = 108                   # every record, both ways
MSG = 100                   # its text or board
HEAD = b"BNP"
VERSION = 0x11
NAME_LEN = 16               # a name in BNJUG, BNSTR and BNSTS
SLOTS = 5                   # btserver's MAXPLAYER: the player slots of BNSTR and BNSTS

BNMSG, BNEXT, BNJUG, BNALL, BNWAI = 0x41, 0x43, 0x44, 0x46, 0x47
BNREA, BNWRI, BNHIT, BNCON, BNSOL, BNSTR, BNTRN, BNLOS, BNWIN = 0x57, 0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F
BNVER, BNTST, BNWHO, BNSTS = 0x60, 0x61, 0x62, 0x63
BNSUP = 0x39                # BNREA's "supervisor" view: never honored here
CODE_NAMES = {BNMSG: "BNMSG", BNEXT: "BNEXT", BNJUG: "BNJUG", BNALL: "BNALL", BNWAI: "BNWAI", BNREA: "BNREA",
              BNWRI: "BNWRI", BNHIT: "BNHIT", BNCON: "BNCON", BNSOL: "BNSOL", BNSTR: "BNSTR", BNTRN: "BNTRN",
              BNLOS: "BNLOS", BNWIN: "BNWIN", BNVER: "BNVER", BNTST: "BNTST", BNWHO: "BNWHO", BNSTS: "BNSTS"}
WATER, UNKNOWN, SHIP, HIT, SUNK = -1, 0, 1, 2, 3
# a player's state in BNSTR and BNSTS, as btserver kept it
ST_NONE, ST_CONNECTED, ST_BOARD, ST_PLAYING, ST_TURN, ST_LOST = 0, 1, 2, 3, 4, 5
XCLI, GCLI, NCLI, WCLI = 0x10, 0x11, 0x20, 0x30        # BNJUG's tip1: the client's kind
CLIENTS = {XCLI: ("xbtclient", "xbnclient"), GCLI: ("gbnclient", "gbnclient"), NCLI: ("nbtclient", "nbnclient"),
           WCLI: ("btwin", "wbnclient")}              # the program's name before 0.60, and from 0.60 on
SERVER_VERSION = (0, 50)    # BNVER's answer: btserver 0.50, whose protocol this is
FLEET = [4, 3, 3, 2, 2, 2, 1, 1, 1, 1]
N = 10


def s8(b):
    """A byte as the clients' signed char."""
    return b - 256 if b > 127 else b


class Record:
    """One BNP record."""
    __slots__ = ("jug", "tip0", "tip1", "tip2", "msg", "ver")

    def __init__(self, tip0, tip1=0, tip2=0, jug=0, msg=b"", ver=VERSION):
        self.tip0, self.tip1, self.tip2, self.jug, self.ver = tip0, tip1, tip2, jug, ver
        self.msg = bytes(msg)

    def pack(self):
        body = (self.msg + bytes(MSG))[:MSG]
        return HEAD + bytes([self.ver & 0xFF, self.jug & 0xFF, self.tip0 & 0xFF, self.tip1 & 0xFF,
                             self.tip2 & 0xFF]) + body

    @classmethod
    def parse(cls, data):
        return cls(data[5], s8(data[6]), s8(data[7]), s8(data[4]), data[8:REC], data[3])

    def text(self):
        """The message as text: up to its first NUL, printable ASCII only."""
        raw = self.msg.split(b"\0", 1)[0]
        return re.sub(r"[^ -~]", "?", raw.decode("latin-1"))

    def __repr__(self):
        return "<%s %d %d jug %d>" % (CODE_NAMES.get(self.tip0, "0x%02X" % self.tip0), self.tip1, self.tip2,
                                      self.jug)


class Reader:
    """Bytes from a client -> whole records. A record that does not start with BNP is skipped up to the next
    BNP (btserver dropped such a record; a client never sends one)."""

    def __init__(self):
        self.buf = b""
        self.skipped = 0

    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= REC:
            if not self.buf.startswith(HEAD):
                at = self.buf.find(HEAD, 1)
                cut = at if at > 0 else len(self.buf) - 2
                self.skipped += cut
                self.buf = self.buf[cut:]
                continue
            out.append(Record.parse(self.buf[:REC]))
            self.buf = self.buf[REC:]
        return out


def client_name(kind, major, minor):
    """BNJUG's kind and version -> "xbtclient 0.50", or "" for a kind batnav never had."""
    names = CLIENTS.get(kind)
    if names is None:
        return ""
    return "%s %d.%02d" % (names[(major, minor) >= (0, 60)], major, minor)


def text_msg(text):
    """A line of text for BNMSG and BNEXT: at most 99 characters and a NUL."""
    return re.sub(r"[^ -~]", "?", text)[:MSG - 1].encode("ascii") + b"\0"


def board_bytes(cells):
    """[100 ints] -> the record's board."""
    return bytes(c & 0xFF for c in cells)


def names_msg(states, names):
    """BNSTR's and BNSTS's message: the five players' states in bytes 0-4, their names from byte 5, 16 each."""
    out = bytearray(MSG)
    for i in range(SLOTS):
        out[i] = states[i] if i < len(states) else ST_NONE
        name = (names[i] if i < len(names) else "")[:NAME_LEN - 1].encode("ascii", "replace")
        out[5 + i * NAME_LEN:5 + i * NAME_LEN + len(name)] = name
    return bytes(out)


# ---- the fleet ---------------------------------------------------------------------------------------------
def sq(x, y):
    return "%s%d" % (chr(97 + x), y + 1)


def xy(name):
    return ord(name[0]) - 97, int(name[1:]) - 1


def fleet_of(board):
    """A client's board (100 bytes) -> its ships [(x, y, across, length)], largest first, or (None, why) when
    batnav's rules refuse it: ten straight ships (one of 4 squares, two of 3, three of 2, four of 1) that do not
    touch, not even at a corner. Any value of 1 or more is a ship square, as btserver read it."""
    cells = {(i % N, i // N) for i in range(N * N) if s8(board[i]) >= 1}
    seen, ships = set(), []
    for c in sorted(cells, key=lambda p: (p[1], p[0])):
        if c in seen:
            continue
        group, todo = set(), [c]
        while todo:
            p = todo.pop()
            if p in group:
                continue
            group.add(p)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                q = (p[0] + dx, p[1] + dy)
                if q in cells:
                    todo.append(q)
        seen |= group
        xs, ys = {p[0] for p in group}, {p[1] for p in group}
        if len(xs) > 1 and len(ys) > 1:
            return None, "a ship must be straight"
        length = len(group)
        if length > 4:
            return None, "no ship is longer than 4 squares"
        x0, y0 = min(xs), min(ys)
        ships.append((x0, y0, 1 if len(xs) > 1 or length == 1 else 0, length))
    for (x, y) in cells:                     # ships may not touch at a corner either
        for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            q = (x + dx, y + dy)
            if q in cells and (x + dx, y) not in cells and (x, y + dy) not in cells:
                return None, "ships may not touch, not even at a corner"
    lengths = sorted((s[3] for s in ships), reverse=True)
    if lengths != FLEET:
        have = {n: lengths.count(n) for n in (4, 3, 2, 1)}
        return None, ("the fleet is one ship of 4 squares, two of 3, three of 2 and four of 1 (this board has "
                      "%d, %d, %d and %d)" % (have[4], have[3], have[2], have[1]))
    ships.sort(key=lambda s: -s[3])
    return ships, None


def ship_cells(ship):
    x, y, across, length = ship
    return [(x + i, y) if across else (x, y + i) for i in range(length)]


def ship_token(ship):
    cells = ship_cells(ship)
    return "%s-%s" % (sq(*cells[0]), sq(*cells[-1]))


def token_cells(token):
    """'a1-a3' -> [(0, 0), (0, 1), (0, 2)]."""
    (x0, y0), (x1, y1) = (xy(p) for p in token.split("-"))
    if y0 == y1:
        return [(x, y0) for x in range(min(x0, x1), max(x0, x1) + 1)]
    return [(x0, y) for y in range(min(y0, y1), max(y0, y1) + 1)]


# ---- the table's view (docs/types/navalcombat-1.md) ---------------------------------------------------------
def parse_view(lines):
    """VUE lines -> {"phase", "turn", "ready" [per seat], "rows" {seat: {y: cells}}, "mine" [tokens], "clock",
    "afloat" [ships afloat per seat]}."""
    v = {"phase": "", "turn": -1, "ready": [], "rows": {}, "mine": [], "clock": None, "afloat": []}
    for l in lines:
        f = l.split()
        if not f:
            continue
        if f[0] == "Q" and len(f) == 2:
            v["phase"] = f[1]
        elif f[0] == "W" and len(f) == 2:
            v["turn"] = -1 if f[1] == "-" else int(f[1])
        elif f[0] == "RD":
            v["ready"] = [x == "1" for x in f[1:]]
        elif f[0] == "B" and len(f) == 4 and f[1].isdigit() and f[2].isdigit():
            v["rows"].setdefault(int(f[1]), {})[int(f[2]) - 1] = f[3]
        elif f[0] == "Y" and len(f) == 3:
            v["mine"].append(f[2])
        elif f[0] == "L":
            v["afloat"] = [int(x) for x in f[1:] if x.isdigit()]
        elif f[0] == "CLK" and len(f) == 4 and f[1].isdigit():
            v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
    return v


CELL = {".": UNKNOWN, "o": WATER, "x": HIT, "#": SUNK, "s": SHIP}


def board_of(v, seat, own):
    """One seat's waters from a view, as a batnav board: ships only on the client's own board (the view shows
    them only to their owner anyway)."""
    out = [UNKNOWN] * (N * N)
    for y, row in v["rows"].get(seat, {}).items():
        for x, c in enumerate(row[:N]):
            val = CELL.get(c, UNKNOWN)
            out[y * N + x] = UNKNOWN if val == SHIP and not own else val
    return out


# ---- the session --------------------------------------------------------------------------------------------
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
MAX_SESSIONS = 40
HELLO_SECS = 30.0           # BNJUG after connecting
GREET_SECS = 2.0            # the greeting waits this long for the client's BNVER (it would hide a status line)
LOGIN_SECS = 600.0          # to sign in
POLL_SECS = 1.0             # the table's events
INVITE_SECS = 3.0           # the player's invitations
LOBBY_SECS = 1800.0         # no game and no typing: goodbye
CHAT_WIDTH = 48             # the X client's message pane shows 49 columns
WIN_WIDTH = 80              # btwin shows one message at a time, in a status line about this wide
COMMANDS = ["list", "resign", "invite", "open", "join", "close", "accept", "decline", "who", "tables", "top",
            "score", "note", "motd", "time", "quit", "help"]
BEFORE_SIGNIN = ("signin", "help", "motd", "quit")
NOTES = ["Send your board, then /invite bot or /invite NAME; the game starts when every fleet is in."]
SIGNIN_LINE = "Sign in: send the message /signin NAME PASSWORD"
SLASH_RE = re.compile(r"(?<![\w:/])/(?=[a-z])")     # a command's slash in a line of text, not one in an address
ids = itertools.count(1)


def log(sid, text):
    print("batnav %d: %s" % (sid, text), file=sys.stderr, flush=True)


class Session:
    def __init__(self, svc, reader, writer, ticket=None):
        self.svc, self.r, self.w, self.ticket = svc, reader, writer, ticket
        self.sid = next(ids)
        self.addr = peer_addr(writer)
        self.wire = Reader()
        self.core = None
        self.core_lock = asyncio.Lock()
        self.name = None            # signed in as
        self.claimed = ""           # the name in BNJUG: the client's claim
        self.client = ""            # the program and version BNJUG names (a claim)
        self.kind = 0
        self.console = None
        self.greeted = False
        self.hello_at = 0.0
        self.closing = False
        self.last_typed = time.monotonic()
        self.announced = {}         # invitations told about: table -> from
        self.fleet = None           # the ships of the board the client sent and this side accepted
        self.reset_table()

    def reset_table(self):
        self.tid = None
        self.seat = None
        self.n = 2
        self.names = []
        self.cursor = 0
        self.placed = False         # the fleet is placed and ready at the table
        self.started = False        # shooting has started and the client has had BNSTR
        self.over = False
        self.lost = False           # the client's fleet is sunk (BNLOS sent)
        self.my_turn = False
        self.ready = set()          # the seats whose fleets are ready (READY events)

    # ---- output ------------------------------------------------------------------------------------------
    async def send(self, tip0, tip1=0, tip2=0, jug=None, msg=b""):
        rec = Record(tip0, tip1, tip2, 1 if jug is None else jug, msg)
        self.w.write(rec.pack())
        await self.w.drain()

    async def say(self, lines):
        """Lines in the client's message window (BNMSG). nbtclient's message field takes / as a backspace, so
        its player is shown commands with a backslash, which works on every client."""
        if isinstance(lines, str):
            lines = [lines]
        for line in chatcmd.wrap(lines, self.width):
            if self.kind == NCLI:
                line = SLASH_RE.sub("\\\\", line)
            await self.send(BNMSG, 0, 0, 0, text_msg(line))

    @property
    def width(self):
        """Lines are wrapped to this: btwin keeps only the last line, so it gets whole sentences."""
        return WIN_WIDTH if self.kind == WCLI else CHAT_WIDTH

    # ---- numbers ----------------------------------------------------------------------------------------
    def num(self, seat):
        """A seat's player number as this client sees it: its own is 1."""
        return (seat - self.seat) % self.n + 1

    def seat_of(self, num):
        return (num - 1 + self.seat) % self.n if 1 <= num <= self.n else None

    def who(self, seat):
        return self.names[seat] if 0 <= seat < len(self.names) and self.names[seat] != "-" else "player %d" % (
            self.num(seat))

    # ---- the core ---------------------------------------------------------------------------------------
    async def req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    async def view(self):
        data, final = await self.req("VUE %s" % self.tid)
        if not final.startswith("OK"):
            raise CoreError(err_text(final))
        return parse_view(data)

    async def table(self):
        data, final = await self.req("LST table=%s" % self.tid)
        return next((t for t in parse_tables(data) if t["id"] == self.tid), None) if final.startswith("OK") else None

    # ---- the session ------------------------------------------------------------------------------------
    async def records(self):
        while True:
            data = await self.r.read(4096)
            if not data:
                return
            for rec in self.wire.feed(data):
                yield rec

    async def run(self):
        it = self.records()
        try:
            first = await asyncio.wait_for(it.__anext__(), HELLO_SECS)
        except (asyncio.TimeoutError, StopAsyncIteration):
            return
        if first.tip0 != BNJUG:
            log(self.sid, "the first record is not BNJUG: %r" % first)
            return
        await self.hello(first)
        if self.closing:
            return
        poller = asyncio.ensure_future(self.poll())
        try:
            async for rec in it:
                try:
                    await self.on_record(rec)
                except (CoreError, asyncio.TimeoutError) as e:
                    log(self.sid, "%r: %s" % (rec, e))
                    await self.say("The server is not answering just now.")
                if self.closing:
                    break
        finally:
            poller.cancel()
            await self.hang_up()

    async def hello(self, rec):
        """BNJUG: the client's name (a claim), kind and version. The answer is player number 1 and the parlor's
        name; the client asks BNVER next, and the greeting follows that answer."""
        self.claimed = rec.text()[:NAME_LEN]
        self.kind = rec.tip1 & 0xFF
        self.client = client_name(self.kind, rec.tip2, rec.jug & 0xFF)
        log(self.sid, "BNJUG %r, %s" % (self.claimed, self.client or "an unknown client 0x%02X" % self.kind))
        try:
            self.core = await CoreClient.connect(*self.svc.core_addr)
            await self.core.source(self.addr)
            if self.client:        # what the core records with every turn played here: the client's own claim
                await self.core.claim("batnav " + self.client, "Windows" if self.kind == WCLI else "")
        except (OSError, asyncio.TimeoutError, CoreError) as e:
            log(self.sid, "no game server: %s" % e)
            self.core = None
            await self.send(BNALL, SLOTS + 1, 0, SLOTS + 1)        # btserver's "full": try later
            self.closing = True
            return
        parlor = (self.core.greeting.split() + ["", "ParlorNet"])[1] or "ParlorNet"
        await self.send(BNJUG, 1, 0, 1, text_msg(parlor))
        self.hello_at = time.monotonic()

    async def greet(self):
        if self.greeted:
            return
        self.greeted = True
        lines = ["Welcome to ParlorNet. This is not a btserver."]
        try:
            motd = await self.core.motd()
        except CoreError:
            motd = []
        if motd:
            lines += chatcmd.wrap(motd[:12], self.width)
        if self.core.auth == "none" and NAME_RE.match(self.claimed or ""):
            await self.say(lines)
            await self.sign_in(self.claimed, None)
            return
        lines += ["/help lists the commands.", SIGNIN_LINE]
        await self.say(lines)

    async def hang_up(self):
        """The client went away or left: a game being played is resigned (a batnav client cannot come back to a
        game), an open table is left, and the session ends."""
        if self.core is None:
            return
        try:
            if self.tid and self.name:
                if self.started and not self.over and not self.lost:
                    await self.req("RES %s" % self.tid)
                    log(self.sid, "resigned table %s: the client left" % self.tid)
                elif not self.started:
                    t = await self.table()
                    if t is not None and t["status"] == "open":
                        await self.req("LVE %s" % self.tid)
            await self.core.finish()
        except (CoreError, OSError, asyncio.TimeoutError):
            self.core.close()

    # ---- what the client sends --------------------------------------------------------------------------
    async def on_record(self, rec):
        t = rec.tip0
        if t in (BNMSG, BNWRI, BNSTR, BNHIT):
            self.last_typed = time.monotonic()
            if self.name:
                self.core.user_input()  # the player's own doing: the next request reports it (ACT)
        if t == BNVER:
            await self.send(BNVER, 0, 0, 0, bytes(SERVER_VERSION))
            await self.greet()
        elif t == BNMSG:
            await self.typed(rec)
        elif t == BNWRI:
            await self.board(rec)
        elif t == BNSTR:
            await self.start_pressed()
        elif t == BNHIT:
            await self.shot(rec)
        elif t == BNREA:
            await self.read_board(rec)
        elif t == BNSTS:
            await self.status()
        elif t == BNTST:
            await self.send(BNTST, 0, 0, 0)
        elif t == BNEXT:
            log(self.sid, "BNEXT: the client leaves")
            self.closing = True
        elif t == BNJUG:
            log(self.sid, "a second BNJUG ignored")
        else:
            log(self.sid, "unknown record %r" % rec)

    async def typed(self, rec):
        """A message from the client's message window: a /command, or chat at the table."""
        self.last_typed = time.monotonic()
        text = rec.text().strip()
        if not text:
            return
        if text.startswith("\\"):              # a command typed with a backslash (nbtclient cannot type /)
            text = "/" + text[1:]
        cmd = chatcmd.parse(text)
        if cmd is not None:
            log(self.sid, "command /%s" % cmd.name)
        if self.core.sid:
            self.core.user_input()
        if self.console is None:
            if cmd is None:
                await self.say(["Sign in first.", SIGNIN_LINE])
            elif cmd.name == "signin":
                if len(cmd.args) < 2 and self.core.auth != "none":
                    await self.say("Type /signin NAME PASSWORD")
                elif not NAME_RE.match(cmd.args[0] if cmd.args else ""):
                    await self.say("Type /signin NAME PASSWORD")
                else:
                    await self.sign_in(cmd.args[0], " ".join(cmd.args[1:]) or None)
            elif cmd.name == "help":
                await self.say(chatcmd.help_lines(BEFORE_SIGNIN, compact=True, width=self.width))
            elif cmd.name == "motd":
                await self.say(chatcmd.framed_motd(await self.core.motd()) or "There is no message of the day.")
            elif cmd.name == "quit":
                await self.say("Goodbye.")
                self.closing = True
            else:
                await self.say(["Sign in first.", SIGNIN_LINE])
            return
        if cmd is not None and cmd.name == "signin":
            await self.say("You are signed in as %s." % self.name)
            return
        if await self.console.handle(text):
            return
        if self.tid:
            _, final = await self.req("CHT %s %s" % (self.tid, text[:56]))
            if not final.startswith("OK"):
                await self.say("Your message did not go: %s" % err_text(final))
            elif rec.tip2 == 1:                 # the client's Local Echo
                await self.say("%s: %s" % (self.name, text[:56]))
        else:
            await self.say("Messages reach the other players once you have a table. /help lists the commands.")

    async def sign_in(self, name, password):
        shown = []

        async def keep(lines):
            shown.extend(lines)
        why = await sign_in_lines(self.core, name, password, None, keep, "signin",
                                  "There is no account %s here. Make one on the web site or by telnet first." % name)
        if why == NEWPASS_TEXT:
            log(self.sid, "sign-in refused: a new password is due (the web or telnet)")
            await self.say(why)
            return
        if shown:
            await self.say(shown)
        if why:
            log(self.sid, "sign-in refused")
            await self.say(no_wait(why))
            return
        if self.ticket is not None and not self.ticket.sign_in():
            await self.say("The server is full. Try again soon.")
            self.closing = True
            return
        self.name = self.core.name
        log(self.sid, "signed in as %s" % self.name)
        self.core.user_input()
        self.console = Console(self)
        await self.say(["Signed in as %s." % self.name,
                        "Send your board, then /invite bot to play the bot, /open for anyone, or /help."])
        await self.check_invitations()

    async def board(self, rec):
        """Send Board: btserver's check of the fleet. Once accepted, the fleet goes to the table when its game
        has begun."""
        if self.started and not self.over and not self.lost:
            await self.send(BNWAI, 0, 0, 0)           # btserver: people are playing
            return
        ships, why = fleet_of(rec.msg)
        if ships is None:
            self.fleet = None
            await self.send(BNWRI, 0, 0, 0, b"\0")
            await self.say("Your board was not accepted: %s." % why)
            return
        if self.over or self.lost:
            self.reset_table()
        self.fleet = ships
        await self.send(BNWRI, 0, 0, 0, b"\1")
        if not self.name:
            await self.say(SIGNIN_LINE)
        elif self.tid:
            await self.place()
        else:
            await self.say("Your board is in. /invite bot to play the bot, /invite NAME, /open or /help.")

    async def start_pressed(self):
        """The Start button. A game here starts by itself once every fleet at the table is ready, so this only
        tells the player what the game waits for (BNSOL is btserver's "you are the only one")."""
        await self.send(BNSOL, 0, 0, 0)
        if not self.name:
            await self.say(["Sign in first.", SIGNIN_LINE])
        elif not self.tid:
            await self.say("You have no game yet: /invite bot, /invite NAME or /open.")
        else:
            await self.say(await self.waiting_line())

    async def waiting_line(self):
        t = await self.table()
        if t is None:
            return "Your table is gone."
        if t["status"] == "open":
            return "Table %s waits for %s." % (self.tid, "a player" if t["filled"] < t["seats"] else "the start")
        v = await self.view()
        waiting = [self.who(s) for s, r in enumerate(v["ready"]) if not r and s != self.seat]
        if waiting:
            return "The game starts when %s %s." % (" and ".join(waiting), "has placed their fleet"
                                                     if len(waiting) == 1 else "have placed their fleets")
        return "The game is starting."

    async def shot(self, rec):
        x, y = rec.msg[0], rec.msg[1]
        if not (self.started and not self.over and not self.lost):
            return
        if not (0 <= x < N and 0 <= y < N):
            await self.say("That square is off the board.")
            await self.send(BNTRN, 0, 0, 1)
            return
        _, final = await self.req("MOV %s %s" % (self.tid, sq(x, y)))
        if final.startswith("OK"):
            self.my_turn = False
            return
        log(self.sid, "shot %s refused: %s" % (sq(x, y), final))
        await self.say("%s: %s." % (sq(x, y).upper(), err_text(final)))
        if self.my_turn:
            await self.send(BNTRN, 0, 0, 1)          # the turn is still the client's

    async def read_board(self, rec):
        """BNREA: a player's board, ships hidden (the BNSUP view is never given); the client's own with them."""
        if self.tid is None or not self.started:
            return
        seat = self.seat_of(rec.tip1)
        if seat is None:
            return
        v = await self.view()
        cells = board_of(v, seat, seat == self.seat)
        if self.lost_seat(v, seat):
            cells = [WATER if c == UNKNOWN else c for c in cells]       # btserver showed a sunk fleet's sea
        await self.send(BNREA, rec.tip1 - 1, 0, 0, board_bytes(cells))

    @staticmethod
    def lost_seat(v, seat):
        """Every ship of the seat's fleet is sunk."""
        return v["phase"] in ("shoot", "over") and seat < len(v["afloat"]) and v["afloat"][seat] == 0

    async def status(self):
        states, names = [ST_NONE] * SLOTS, [""] * SLOTS
        names[0] = (self.name or self.claimed)[:NAME_LEN - 1]
        states[0] = ST_BOARD if self.fleet else ST_CONNECTED
        if self.tid is not None and self.names:
            v = await self.view() if self.started or self.placed else None
            for seat in range(self.n):
                i = self.num(seat) - 1
                if i >= SLOTS:
                    continue
                names[i] = self.names[seat] if self.names[seat] != "-" else ""
                if not names[i]:
                    continue
                if v is None:
                    states[i] = ST_CONNECTED if seat != self.seat else states[0]
                elif self.over:
                    states[i] = ST_CONNECTED
                elif v["phase"] == "shoot":
                    states[i] = ST_TURN if v["turn"] == seat else ST_PLAYING
                else:
                    states[i] = ST_BOARD if (seat < len(v["ready"]) and v["ready"][seat]) else ST_CONNECTED
        await self.send(BNSTS, 0, 0, SLOTS + 1, names_msg(states, names))

    # ---- the table --------------------------------------------------------------------------------------
    async def seated(self, tid, seat, opp, how):
        self.reset_table()
        self.tid, self.seat = tid, seat
        t = await self.table()
        if t is not None:
            self.n, self.names = t["seats"], t["seated"]
        log(self.sid, "seated at table %s, seat %d of %d (%s)" % (tid, seat, self.n, how))
        if not self.fleet:
            await self.say("Now send your board.")

    async def place(self):
        """The accepted fleet onto the table, once its game has begun: ship by ship, largest first, then ready."""
        if self.placed or not self.fleet or not self.tid:
            return
        v = await self.view()
        if v["phase"] != "place":
            return
        if self.seat < len(v["ready"]) and v["ready"][self.seat]:
            self.placed = True
            return
        if v["mine"]:
            await self.req("MOV %s clear" % self.tid)
        for ship in self.fleet:
            _, final = await self.req("MOV %s %s" % (self.tid, ship_token(ship)))
            if not final.startswith("OK"):
                log(self.sid, "placing %s refused: %s" % (ship_token(ship), final))
                await self.req("MOV %s clear" % self.tid)
                await self.say("The table did not take your fleet: %s. Send your board again." % err_text(final))
                self.fleet = None
                return
        _, final = await self.req("MOV %s ready" % self.tid)
        if not final.startswith("OK"):
            await self.say("The table did not take your fleet: %s." % err_text(final))
            return
        self.placed = True
        log(self.sid, "fleet placed at table %s" % self.tid)

    async def poll(self):
        last_inv = 0.0
        while not self.closing:
            await asyncio.sleep(self.svc.poll)
            try:
                if self.tid:
                    await self.table_events()
                if self.name and time.monotonic() - last_inv >= INVITE_SECS:
                    last_inv = time.monotonic()
                    await self.check_invitations()
                idle = time.monotonic() - self.last_typed
                if not self.name and self.hello_at and not self.greeted and (
                        time.monotonic() - self.hello_at > GREET_SECS):
                    await self.greet()
                if not self.name and idle > LOGIN_SECS:
                    await self.say("Signing in took too long. Connect again to try again.")
                    self.close()
                elif not (self.tid and not self.over) and idle > LOBBY_SECS:
                    await self.say("Nothing happened for a long time; goodbye.")
                    self.close()
            except (CoreError, OSError, asyncio.TimeoutError) as e:
                log(self.sid, "poll: %s" % e)
                self.close()
            except Exception as e:
                log(self.sid, "poll error: %r" % e)
                import traceback
                traceback.print_exc()
                self.close()

    def close(self):
        self.closing = True
        try:
            self.w.close()
        except Exception:
            pass

    async def check_invitations(self):
        data, final = await self.req("IVL")
        if not final.startswith("OK"):
            return
        invs = parse_invites(data)
        live = {i["id"] for i in invs}
        for tid in [t for t in self.announced if t not in live]:
            del self.announced[tid]
        for i in invs:
            if i["id"] in self.announced:
                continue
            self.announced[i["id"]] = i["from"]
            if i["key"] == NC and i["rules"] == RULES:
                await self.say(["%s invites you to Naval Combat (table %s)." % (i["from"], i["id"]),
                                "/accept %s or /decline %s" % (i["from"], i["from"])])
            elif i["key"] == NC:
                await self.say(["%s invites you to Naval Combat (table %s)" % (i["from"], i["id"]),
                                "with %s rules, which batnav cannot play." % i["rules"],
                                "Play it on the web or by telnet, or /decline %s." % i["from"]])
            else:
                await self.say(["%s invites you to another game." % i["from"],
                                "Play it on the web or by telnet, or /decline %s." % i["from"]])

    async def table_events(self):
        data, final = await self.req("SIN %s %d" % (self.tid, self.cursor))
        if final.startswith("ERR"):
            if not self.started:
                await self.say("Your table %s is gone. /invite or /open to start another." % self.tid)
                self.reset_table()
            return
        says = chatcmd.join_says(data)      # what each player who sat down plays with
        for l in data:
            p = l.split(" ", 4)
            if len(p) < 4 or p[0] != "EVT" or not p[2].isdigit() or int(p[2]) <= self.cursor:
                continue
            self.cursor = int(p[2])
            await self.event(p[3], p[4] if len(p) > 4 else "", says)
            if self.tid is None:
                break

    async def event(self, kind, rest, says=None):
        a = rest.split()
        change = chatcmd.change_of(kind, a, says or {}, self.seat)
        if kind == "JOIN" and len(a) >= 2 and a[0].isdigit():
            seat = int(a[0])
            if seat < len(self.names):
                self.names[seat] = a[1]
            if seat != self.seat and not self.started:
                await self.say(chatcmd.joined(a[1], (says or {}).get(seat, ""), "sits down at table %s" % self.tid))
        elif change:                        # a player plays on with another program
            await self.say(chatcmd.client_change(self.who(change[0]), change[1]))
        elif kind == "LEAVE" and a[:1] and a[0].isdigit() and not self.started:
            seat = int(a[0])
            if seat != self.seat:
                await self.say("%s stood up." % self.who(seat))
                if seat < len(self.names):
                    self.names[seat] = "-"
        elif kind == "START":
            t = await self.table()
            if t is not None:
                self.n, self.names = t["seats"], t["seated"]
            others = [self.who(s) for s in range(self.n) if s != self.seat]
            await self.say("Your game with %s on table %s begins." % (" and ".join(others), self.tid))
            if self.fleet:
                await self.place()
            else:
                await self.say("Send your board to play.")
        elif kind == "READY" and a and a[0].isdigit():
            # Shooting starts when every seat is ready; the TURN events before that are the placing clock's.
            self.ready.add(int(a[0]))
            if not self.started and len(self.ready) >= self.n:
                await self.begin()
            elif not self.started and int(a[0]) != self.seat and not self.placed:
                await self.say("%s has placed their fleet.%s" % (
                    self.who(int(a[0])), "" if self.fleet else " Send your board."))
        elif kind == "TURN" and a and a[0].isdigit():
            if self.started and not self.over:
                seat = int(a[0])
                self.my_turn = seat == self.seat
                if self.lost:
                    return
                if self.my_turn:
                    await self.send(BNTRN, 0, 0, 1)
                else:
                    await self.send(BNWHO, self.num(seat), 0, 0)
        elif kind == "SHOT" and len(a) >= 3 and a[0].isdigit():
            await self.shot_event(int(a[0]), a[1], a[2:])
        elif kind == "END":
            await self.ended(a)
        elif kind == "CHT" and len(a) >= 2 and a[0].isdigit() and int(a[0]) != self.seat:
            await self.say("%s: %s" % (self.who(int(a[0])), rest.split(" ", 1)[1]))
        elif kind == "UNINVITE" and len(a) >= 3:
            why = {"declined": "declined your invitation", "expired": "was invited, and the invitation expired",
                   "withdrawn": "is no longer invited"}.get(a[3] if len(a) > 3 else "", "")
            if why:
                await self.say("%s %s." % (a[2], why))

    async def begin(self):
        """Every fleet is ready: btserver's BNSTR, with the players' states and names in the client's numbers."""
        if self.started:
            return
        t = await self.table()
        if t is not None:
            self.n, self.names = t["seats"], t["seated"]
        states, names = [ST_NONE] * SLOTS, [""] * SLOTS
        for seat in range(self.n):
            i = self.num(seat) - 1
            if i < SLOTS:
                states[i], names[i] = ST_BOARD, self.names[seat]
        self.started = True
        await self.send(BNSTR, 0, 0, 1, names_msg(states, names))
        log(self.sid, "shooting starts at table %s" % self.tid)

    async def shot_event(self, seat, square, result):
        """A shot at the table. Shots at the client's own fleet are told with BNHIT, square by square for a sunk
        ship, as btserver did; the client reads the other boards itself."""
        if seat == self.seat or not self.started or self.lost:
            return
        x, y = xy(square)
        if result[0] == "miss":
            await self.send(BNHIT, 0, 0, 1, bytes([x, y, WATER & 0xFF]))
        elif result[0] == "hit":
            await self.send(BNHIT, 0, 0, 1, bytes([x, y, HIT]))
        elif result[0] == "sunk" and len(result) >= 3:
            for cx, cy in token_cells(result[2]):
                await self.send(BNHIT, 0, 0, 1, bytes([cx, cy, SUNK]))

    async def ended(self, a):
        """END: btserver's last words. The loser gets BNLOS, then "The Game is over: Player n is the winner."
        (BNEXT, value 2); the winner gets "Player n is dead." (BNEXT, value 1), or "Player n quits the game."
        (value 0) after a resignation, then BNWIN."""
        if self.over:
            return
        self.over = True
        how = a[0] if a else ""
        seat = int(a[1]) if len(a) > 1 and a[1].isdigit() else -1
        log(self.sid, "table %s over: %s" % (self.tid, " ".join(a)))
        if not self.started:
            await self.say("The game at table %s ended before it began (%s)." % (self.tid, " ".join(a)))
            self.reset_table()
            self.fleet = None
            return
        if how == "win" and 0 <= seat < self.n:
            winner = seat
            if winner == self.seat:
                for s in range(self.n):
                    if s != self.seat:
                        await self.send(BNEXT, self.num(s), 1, 0, text_msg("Player %d is dead." % self.num(s)))
                await self.send(BNWIN, 0, 0, 1)
            else:
                await self.lose(winner, None)
        elif how in ("resign", "timeout") and 0 <= seat < self.n:
            words = "quits the game" if how == "resign" else "ran out of time"
            if seat == self.seat:
                await self.lose(next((s for s in range(self.n) if s != self.seat), self.seat),
                                "You %s." % ("resigned" if how == "resign" else "ran out of time"))
            else:
                await self.send(BNEXT, self.num(seat), 0, 0, text_msg("Player %d %s." % (self.num(seat), words)))
                await self.say("%s %s." % (self.who(seat), "resigned" if how == "resign" else "ran out of time"))
                await self.send(BNWIN, 0, 0, 1)
        else:
            await self.say("The game at table %s was ended by the server." % self.tid)
            await self.send(BNEXT, 1, 2, 0, text_msg("The game was ended by the server."))
        self.fleet = None
        self.tid = None
        await self.say("Send a board for another game.")

    async def lose(self, winner, why):
        if why:
            await self.say(why)
        if not self.lost:
            self.lost = True
            await self.send(BNLOS, 0, 0, 1)
        await self.send(BNEXT, self.num(winner), 2, 0,
                        text_msg("The Game is over: Player %d is the winner." % self.num(winner)))


class Console(chatcmd.Console):
    """The message window's chat commands (the shared set), on batnav-rules Naval Combat tables."""

    def __init__(self, s):
        self.s = s
        super().__init__(s.say, s.req, lambda: s.name, COMMANDS, kinds=[NC], label="Naval Combat", new=RULES,
                         notes=NOTES, handlers={"quit": self.quit}, seated=s.seated, width=s.width,
                         current=lambda: s.tid if s.started and not s.over else None)

    async def handle(self, text):
        cmd = chatcmd.parse(text)
        if cmd is not None and cmd.name in ("open", "invite", "join", "accept") and self.s.tid and not self.s.over:
            await self.say("You have a table already (%s)." % self.s.tid if not self.s.started
                           else "You are playing a game now.")
            return True
        return await super().handle(text)

    async def tables(self):
        return [t for t in await super().tables() if t["rules"] == RULES]

    async def invitations(self):
        """The player's invitations, those to batnav-rules tables first (an /accept takes the first one from
        a player)."""
        invs = await super().invitations()
        return sorted(invs, key=lambda i: not (i["key"] == NC and i["rules"] == RULES))

    async def sit(self, tid, seat, opp, how):
        data, final = await self.req("LST table=%s" % tid)
        t = next((t for t in parse_tables(data) if t["id"] == tid), None) if final.startswith("OK") else None
        if t is not None and (t["key"] != NC or t["rules"] != RULES):
            await self.say(["batnav cannot play table %s: it has %s rules." % (tid, t["rules"]),
                            "/invite %s to a batnav table instead." % (opp.split()[0] if opp else "NAME")])
            return None
        return await super().sit(tid, seat, opp, how)

    async def new_table(self, vis):
        """As the default, with this extension's time per turn and the batnav ruleset."""
        _, final = await self.req("NEW %s %s turn=%s%s" % (NC, RULES, self.s.svc.turn,
                                                            " vis=private" if vis == "private" else ""))
        if not final.startswith("OK"):
            await self.say("Can't open a table: %s" % err_text(final))
            return None
        tid = final.split()[1]
        _, final = await self.req("JON %s 0" % tid)
        if not final.startswith("OK"):
            await self.req("LVE %s" % tid)
            await self.say("Can't open a table: %s" % err_text(final))
            return None
        return tid

    async def quit(self, cmd):
        await self.say("Goodbye.")
        self.s.closing = True
        self.s.w.close()


class Service:
    def __init__(self, args):
        self.args = args
        self.core_addr = (args.core_host, args.core_port)
        self.poll = args.poll
        self.turn = args.turn


async def serve(args):
    svc = Service(args)
    gate = Gate(MAX_SESSIONS)
    gate.notice = b""

    async def handle(reader, writer):
        ticket = gate.admit(peer_addr(writer), writer)
        if ticket is None:
            writer.close()
            return
        keepalive(writer)
        s = Session(svc, reader, writer, ticket)
        log(s.sid, "connect")
        try:
            await s.run()
        except (EOFError, ConnectionError, asyncio.TimeoutError, CoreError, OSError) as e:
            log(s.sid, "ended: %s" % (e or type(e).__name__))
        except Exception as e:
            print("batnav session error: %r" % e, file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc()
        finally:
            ticket.release()
            log(s.sid, "disconnect")
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("batnav adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.batnav")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6514)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    ap.add_argument("--turn", default="30m", help="time per turn at the tables this extension makes")
    declare.add_args(ap)
    ap.add_argument("--poll", type=float, default=POLL_SECS, help=argparse.SUPPRESS)    # tests
    a = ap.parse_args(argv)
    if a.key:
        CoreClient.key_file = a.key
    a.core_host, _, p = a.core.rpartition(":")
    a.core_port = int(p)
    return a


if __name__ == "__main__":
    try:
        asyncio.run(serve(parse_args()))
    except KeyboardInterrupt:
        pass
