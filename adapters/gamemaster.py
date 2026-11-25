"""GameMaster extension: ParlorNet's games for GameMaster (Mac, Rhys Hollow, 1991-93).

GameMaster plays its games ("rulebooks") between two Macs. A parlor plays them only through ParlorNet's own
rulebook for GameMaster, whose window signs the player in and carries every GameMaster connection of that
Mac over its one connection to this extension: the tunnel, below. ParlorNet's rulebooks are built and released
in their own repository, eric-cgn/parlornet-gamemaster (rulebooks/parlornet, rulebooks/<game>,
rulebooks/common). Inside the tunnel this
extension is the remote Mac, the one that is called. What GameMaster sends and expects was read
from the rulebooks' Pascal sources and the application's 68k code.

The call. A call sends a fixed 786-byte call record: the size of the game it will upload, the GameMaster
version it needs, and the rulebook's ID string and versions. This extension answers three rulebooks of the
User Kit and ParlorNet's own game rulebooks:

    Dots&Boxes [PNL] 1.0.0     dotsandboxes/1, ruleset majority (13 by 11, first past half the boxes)
    Othello [RHS] 0.2.0        reversi/1, ruleset standard
    BattleShips [RHS] 0.2.0    navalcombat/1, ruleset salvo (six shots a turn)
    Checkers [ParlorNet] 1.0.0 checkers/1, ruleset american
    Mancala [ParlorNet] 1.0.0  mancala/1, ruleset kalah (four seeds a pit)
    Five in a Row [ParlorNet] 1.0.0
                               fiveinarow/1, ruleset freestyle (15 by 15)
    Grid Words [ParlorNet] 1.0.0
                               gridwords/1, ruleset classic, two players; it also plays english
                               tables made on the web or by telnet (Rulebook.accepts)
    Rummy Tile [ParlorNet] 1.0.0
                               rummytile/1, ruleset standard, two players
    Cannon Fire [ParlorNet] 1.0.0
                               cannonfire/1, ruleset classic, two players
    Backgammon [ParlorNet] 1.0.0
                               backgammon/1, ruleset standard, with the doubling cube

For any other rulebook it sends GameMaster's own refusal, ~Dont Send Code, the answer of a Mac whose user
would not learn the rulebook. It never asks for rulebook code and never accepts any. For a supported one
it sends ~Send Game Across Port : and a 10-byte address: 1 (TCP), 0, the address 0.0.0.0 and the port
0x504E0000 plus the call's number, as 32 bits, which the rulebook recognizes when GameMaster opens the game
connection (GMS, below). The caller uploads its game on that connection and plays on it. Messages are Pascal
strings: ~ and a text for GameMaster's own messages, # and a text for the rulebook's.

Who calls. A call belongs to the player signed in on its tunnel, and to nobody else. The call record names
no player; neither the Mac's address nor its name counts.

Which table. The caller's new game always moves first, so the GameMaster player takes seat 0, and the
turn is --turn (10 minutes). After the upload the extension sends ~Just a moment, I'll see while it finds
a table for the opponent the call asked for (GMO's who):

    bot                   a new private table, with the other seat held for the bot (INV)
    NAME                  that player's invitation to this one, if there is one; else a new private table
                          with the other seat held for them; the call waits up to three minutes
    anyone                an invitation to the player at a table of the game, or else an open public table
                          of the game where someone waits; or else a new public table, which anyone can
                          join from the web or telnet for --wait seconds (60), after which the bot is invited
    #TABLE                a free seat at that open table
    @TABLE                the player's own game being played at that table, as it stands (Resuming)

Seat 0 is taken when it is free. In Othello and ParlorNet's own games, when someone already holds seat 0, the
extension sends ~Play it Sam and then ~Request to swap, which the Mac shows as "wishes to swap players"; after
~Swap the GameMaster player takes seat 1 (Othello's black, the second player of the others), and after
~Dont Swap the call ends. Dots&Boxes and BattleShips cannot swap once connected.

When the game starts it sends ~Play it Sam; if nobody comes, ~sorry, not interested. A caller that
uploads a game in progress (a saved Dots&Boxes or Othello game) is turned down the same way, since a
ParlorNet table always starts from the beginning.

Resuming. ParlorNet's own rulebooks from version 1.1.0 take a whole position from the parlor (POS, below),
so a game already being played at a table, started on the web, by telnet or in GameMaster, can be taken up
from GameMaster with the lobby's Resume (GMO c @TABLE). The table must be one of the game the call brings,
being played, with the player in a seat; nothing at it changes (no new table, no seat taken, swapped or
left). The Mac's new game is the window it is shown in: after ~Play it Sam the first
thing it is sent is the position, from the core's view of the player's seat, and only then the moves. The
player may keep the table open on the web or by telnet as well. A move made there for the player's seat
makes the Mac's board stale, and the end of that turn sends the whole position again instead of the moves.
A resumed game is never resigned here: a hang-up or the end of the tunnel leaves it at the table, on its turn
clock, and a move the Mac refuses (the two sides disagree) is answered with the whole position. Othello,
Dots&Boxes and BattleShips take no position, so their games can be taken up only on the web or by telnet.

The games. Othello's moves are a column digit and a row digit (0-7), as Reversi's squares; GameMaster's
white starts, so it is seat 0. Dots&Boxes sends M, 0 for a line across or 1 for a line down, then the
dot row and column as three digits each; both sides take boxes and moves again by the same rule.
BattleShips sends the fleet when its player presses Sail (column, row and h or v for each ship, largest
first), each square marked for the salvo as two digits (a second time to unmark it), Fire for the six,
and Done once its player has seen them land. The extension sends the other player's fleet once both are
ready (the OF lines of the salvo ruleset), and a ParlorNet player's salvo as its six squares, Fire and
Done, after GameMaster's own Done. Requests to swap or restart are declined (~Dont Swap, ~Dont Restart).

ParlorNet's own rulebooks (ParlorGame below; their rulebooks/common/rb.h) share a few messages, and the core
decides every move: the Mac sends M and a whole move, the extension plays it at the table and answers A
and the move, which the Mac shows only then, or R and the core's reason, which the Mac shows instead. The
other player's move reaches the Mac as M, and the Mac answers A. D offers a draw, DY and DN answer an offer,
G resigns, and E with the words of the core's END ends the game on the Mac. Checkers' moves are its squares
joined by - for a step and x for jumps, as the core takes them (11-15, 15x22x31); Mancala's are a pit, 1 to 6,
and a turn that goes on (a last seed in the mover's store) is sent as one M a move; Five in a Row's are a point
(h8); Grid Words' are the core's own (PLAY 8 8 H WORD, EXCH, PASS), and the extension also sends the Mac's rack
(K), the scores (S) and the bag (B), since the parlor deals; Rummy Tile's are each step of a staged turn as the
core's token, and the Mac is sent the whole state (Z, ST, H, TP, P, C, I, W) after each; Cannon Fire's are FIRE
angle power and MOVE L|R n, and the Mac is sent every shell's path and the state, since the core flies the
shells; Backgammon's are roll, double, take, drop, accept, reject, or a whole turn of steps (13-9 9-4, bar-22,
6-off), and the Mac is sent the state (BD ... W) after every change, since the core rolls the dice. A move the
Mac makes before the table has started is played once it has.

The whole position, for a resumed table and whenever the Mac's board is behind: POS seat turn offer (the
seat this Mac plays, the seat to move, a draw offer's seat + 1 or 0), the game's own lines, and POE, after
which the Mac redraws. Checkers sends BD and its 32 squares and L and the quiet turns; Mancala BD and the 14
counts and LM and the last move; Five in a Row BD and the 225 points and LS and the last stone; Grid Words BD
and the 225 squares (. empty, a blank's letter in lower case), K, S, B and LP x y H|V length for the last
play; Rummy Tile, Cannon Fire and Backgammon the state they are sent after every step (Z ... W, BD ... W).

The end. When the game ends the extension leaves the connection open for ten minutes, so the player can
look at the board, then sends ~Beep Beep Beep Beep Beep (GameMaster's hang-up) and closes. A game that
ends by a resignation, a timeout or the operator closes at once. When the Mac hangs up during a game it
started, the extension resigns it for the GameMaster player; a resumed game is left at its table.

    python3 -m adapters.gamemaster --core 127.0.0.1:6590 --key gamemaster.key --port 6508

In the container it listens on 6508, published under the same number.

GameMaster's own calls. GameMaster by itself (Game > Connect) calls port 918 and sends a call record at
once. Those calls are not served: a connection whose first byte is zero, as a call record's is, is read as
a call record and answered ~Dont Send Code, which the Mac shows as "<address> isn't interested.", and the
extension logs one line for it.

The tunnel. The ParlorNet rulebook connects to this port and opens as a client does on the core's public
port (docs/protocol.md, Connecting; parlord/native.py): the line PARLORNET/1, within 20 seconds, with up
to 10 bytes of anything before it. Anything else is answered ERR PROTOCOL wrong protocol (ERR VERSION wrong
version for another version), with the docs site's page for the protocol when the core names a docs site
(PRL's DOC), and closed. The tunnel's lines end with CR LF, whatever ending the opening had; a line from
the Mac may end with CR, LF or both. It then speaks the client protocol's framing: the greeting,
a sign-in relayed to the core (USER or HELO, ASK and AUT, ending OK -), a few lobby requests relayed as they
are (WHO, LST, IVL, UNV, MOTD, GAM; IVL's invitations name a game's ruleset by the rulebook's own, as TBL
does), TBL for the tables the lobby lists (Tunnel.tables), CLI (below), and these lines, which carry the
GameMaster connections of that Mac inside the one connection:

    GMO c who       a call on connection c (1-8): the call record and the rest follow (The call, above);
                    who is the opponent asked for (bot, anyone or a name), #TABLE for a seat at that open
                    table, or @TABLE to take up the player's game being played there (Which table)
    GMS c k         the game connection, on c, that follows call k
    GMD c hex       bytes on connection c, both ways (at most 36 a line)
    GMX c           connection c closed, both ways
    GMK c           (to the Mac) the extension took connection c

When the connection ends, its games end (resigned if they were being played, unless resumed) and the sign-in
with it. A call that cannot go ahead tells the lobby why with an INF line. GameMaster shows only its own words
when a game starts, so the lobby also hears with an INF line what the opponent plays with (the core's CLIENT,
docs/protocol.md), when the game starts and when they play on from another program: "INF Table 12: bob plays
with Netscape Navigator 3.0 on a Macintosh.". The rulebook shows any INF line as the lobby's note.

What the turns were played with. CLI PROG text and CLI MACH text are taken as the core takes them
(docs/protocol.md, Clients and machines), at any time, cleaned the same way and answered OK; CLI alone lists
them. The rulebook from version 0.4 sends both as soon as it is signed in, such as CLI PROG GameMaster 1.0.0 +
ParlorNet 0.4 and CLI MACH Macintosh SE/30. Each game the tunnel then starts says them to the core on the
connection that plays its moves, so its turns are recorded with them. A rulebook that says nothing (before
0.4) has its turns recorded as GameMaster with the version its call record carries (GameMaster 1.0.0), on a
Macintosh, and so does the machine of a rulebook that names only its program.
"""
import argparse
import asyncio
import ipaddress
import itertools
import re
import struct
import sys
import time

from parlord import native

from . import botflag, declare
from .clients import BY_ID
from .common import (BG_HOME_PREF, BG_HOMES, CoreClient, CoreError, Gate, claim_text, client_change_sentence,
                     client_sentence, keepalive, err_text, parse_client, parse_invites, parse_tables,
                     peer_addr)

# ---- the session layer ----------------------------------------------------------------------------

CALL_SIZE = 786                 # the call record
GM_VERSION = 0x0100             # GameMaster 1.0.0, as the call record carries it
MAX_STATE = 64 * 1024           # the largest game upload taken (the four rulebooks send under 3 KB)
PIECE = 8000                    # GameMaster uploads a game in pieces of this size

SEND_GAME = "Send Game Across Port :"
DONT_SEND_CODE = "Dont Send Code"
TOO_OLD = "Go away, I'm old"
JUST_A_MOMENT = "Just a moment, I'll see"
PLAY_IT_SAM = "Play it Sam"
NOT_INTERESTED = "sorry, not interested"
HANG_UP = "Beep Beep Beep Beep Beep"
REQUEST_SWAP = "Request to swap"
SWAP, DONT_SWAP = "Swap", "Dont Swap"
ANSWERS = {"Request to swap": "Dont Swap", "Request to restart": "Dont Restart"}

CALL_SECS = 30.0                # to send the call record after connecting
DOCS_SECS = 300.0               # the docs site's address (PRL's DOC) is asked for again after this long
GAME_SECS = 30.0                # to open the game connection after the answer
STATE_SECS = 60.0               # to upload the game
NAMED_SECS = 180.0              # a named opponent's invitation is waited for this long
BOT_SECS = 60.0                 # the bot's
SWAP_SECS = 60.0                # the Mac's player has this long to answer a request to swap sides
END_LINGER = 600.0              # the connection stays open this long after a game ends
TUNNEL_IDLE = 2 * 3600.0        # a tunnel with no line from the Mac is closed after this long
TUNNEL_PORT = 0x504E0000        # the port of a tunneled call's answer, plus the call's number
TUNNEL_SLOTS = 8                # connections in one tunnel
TBL_MAX = 8                     # tables of each kind in the answer to TBL
INF_WIDTH = 74                  # the text of an INF line, so it fits 80 bytes with "INF " and CR LF
TUNNEL_RELAY = ("WHO", "LST", "IVL", "UNV", "MOTD", "GAM")
# a table of more than two players, which GameMaster cannot play (a call joins two Macs)
BIGGER = ("Table %s is a game for %d players, and GameMaster plays two. Play it on the web, by telnet or in "
          "ParlorNet Play.")
POSITIONS = 0x0110              # ParlorNet's game rulebooks take a whole position (POS) from this version
MAX_CALLS = 40                  # calls and games at once
PER_ADDR = 4                    # from one address
CLIENT_NAME = BY_ID["gamemaster"].name      # "GameMaster": what the turns say they were played with (CLI)
MACHINE = "Macintosh"                       # ... and on, when the rulebook does not name the model

ids = itertools.count(1)


def log(sid, text):
    print("gamemaster[%d] %s" % (sid, text), flush=True)


def pstring(prefix, text):
    """A message: a length byte, the prefix (~ or #) and the text (bytes, at most 254)."""
    body = prefix + text
    return bytes([len(body)]) + body


def control(text):
    """One of GameMaster's own messages."""
    return pstring(b"~", text.encode("latin-1"))


def rulebook(text):
    """A message for the other side's rulebook."""
    return pstring(b"#", text.encode("latin-1"))


def read_str(rec, at, size):
    """A Pascal string of a fixed-size field."""
    n = min(rec[at], size - 1)
    return rec[at + 1:at + 1 + n].decode("latin-1")


def bcd(v):
    """0x0100 -> '1.0.0', 0x0020 -> '0.2.0' (a version as GameMaster keeps it)."""
    return "%d.%d.%d" % ((v >> 8) & 0xFF, (v >> 4) & 0xF, v & 0xF)


GREETING = "Shall we play a game?"     # the call record's first string, as GameMaster 1.0.0 sends it


class CallRecord:
    """The 786-byte record a caller sends first (as captured from GameMaster 1.0.0): the game's size, a
    greeting, the versions, the rulebook's ID string and name, the caller's own address as text, and its
    connection record, which ends with the Mac's name from Sharing Setup at 0x2F0 (its AppleTalk name)."""

    def __init__(self, rec):
        self.size = struct.unpack_from(">I", rec, 0)[0]
        self.key = read_str(rec, 0x004, 64)
        self.version, self.needs, self.rb_version, self.rb_oldest = struct.unpack_from(">4H", rec, 0x044)
        self.rb_id = read_str(rec, 0x04C, 64)
        self.rb_name = read_str(rec, 0x08C, 64)
        self.address = read_str(rec, 0x0CC, 256)
        self.machine = "".join(c for c in read_str(rec, 0x2F0, 34) if c.isprintable())

    @staticmethod
    def build(rb_id, rb_version, rb_oldest, size, needs=GM_VERSION, rb_name="", address="", version=GM_VERSION,
              machine="", key=GREETING):
        """A call record as GameMaster sends it (for tests)."""
        rec = bytearray(CALL_SIZE)
        struct.pack_into(">I", rec, 0, size)
        struct.pack_into(">4H", rec, 0x044, version, needs, rb_version, rb_oldest)
        for at, size_, text in ((0x004, 64, key), (0x04C, 64, rb_id), (0x08C, 64, rb_name),
                                (0x0CC, 256, address), (0x2F0, 34, machine)):
            b = text.encode("latin-1")[:size_ - 1]
            rec[at] = len(b)
            rec[at + 1:at + 1 + len(b)] = b
        return bytes(rec)


def address_bytes(ip, port):
    """The 10-byte address after ~Send Game Across Port :, TCP."""
    return bytes([1, 0]) + ipaddress.IPv4Address(ip).packed + struct.pack(">I", port)


async def read_message(reader):
    """One message from GameMaster -> (control, text): control is True for ~ messages. None at the end."""
    try:
        n = (await reader.readexactly(1))[0]
        body = await reader.readexactly(n) if n else b""
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    if not body:
        return (False, "")
    return (body[:1] == b"~", body[1:].decode("latin-1"))


# ---- the rulebooks ------------------------------------------------------------------------------------

class Rulebook:
    def __init__(self, rb_id, version, key, rules, game, swaps=False, also=()):
        self.rb_id, self.version, self.key, self.rules, self.game = rb_id, version, key, rules, game
        self.swaps = swaps      # the rulebook swaps sides when asked: the caller can take the second seat
        # the rulesets of tables it plays: its own, which its new tables have, and also those of tables made
        # elsewhere that play the same game as far as the rulebook can tell (the parlor keeps the difference)
        self.accepts = (rules,) + tuple(also)

    def takes(self, call):
        """The caller's rulebook can play with this one: GameMaster's rule for a callee that has the same
        version, or a newer one that still plays with this version."""
        return call.rb_oldest <= self.version <= call.rb_version


def sq(x, y):
    return "%s%d" % (chr(97 + x), y + 1)


def xy(name):
    return ord(name[0]) - 97, int(name[1:]) - 1


class Game:
    """One game's translation. from_mac() takes a rulebook message from GameMaster, on_event() an event of
    the table; both use the session's move() and send()."""

    def __init__(self, s):
        self.s = s

    @staticmethod
    def fresh(state):
        """True unless the uploaded game is known to be one in progress."""
        return True

    async def from_mac(self, text):
        pass

    async def on_event(self, kind, args):
        pass

    async def after_end(self, text):
        """A rulebook message after the game ended (BattleShips' Done)."""
        pass

    async def on_start(self):
        """The table has started, before the first message either way."""
        pass


class Othello(Game):
    """Othello [RHS]: a move is the column digit and the row digit (0-7). Passes are not sent: both sides
    work them out, as the core does."""

    @staticmethod
    def fresh(state):
        """The record starts with a 4-byte handle and board[-1..8, -1..8] (by column, then row; 0 none,
        1 black, 2 white). A board other than the opening one is a game in progress; bytes that are no
        board at all leave the question open (True)."""
        board = state[4:104]
        if len(board) < 100 or any(b > 2 for b in board):
            return True
        start = bytearray(100)
        for (x, y), p in (((3, 3), 1), ((3, 4), 2), ((4, 3), 2), ((4, 4), 1)):
            start[(x + 1) * 10 + y + 1] = p
        return board == bytes(start)

    async def from_mac(self, text):
        if len(text) == 2 and text.isdigit() and int(text[0]) < 8 and int(text[1]) < 8:
            await self.s.move(sq(int(text[0]), int(text[1])))
        else:
            self.s.note("Othello sent %r" % text[:20])

    async def on_event(self, kind, args):
        if kind == "PUT" and args and args[0] == str(1 - self.s.seat):
            x, y = xy(args[1])
            await self.s.send("%d%d" % (x, y))


class DotsAndBoxes(Game):
    """Dots&Boxes [PNL]: M, 0 for a line across or 1 for a line down, then the row and the column as three
    digits. Across: dot row r (0-11) from dot column c-1 to c (1-13). Down: dot column c (0-13) from dot
    row r-1 to r (1-11)."""

    @staticmethod
    def fresh(state):
        """The record starts with a 4-byte handle and three box counts, then lines[2][12][14] (booleans)
        from 0x0A. A line drawn is a game in progress; bytes that are no lines at all leave the question
        open (True)."""
        lines = state[0x0A:0x15A]
        return len(lines) < 0x150 or any(b > 1 for b in lines) or not any(lines)

    @staticmethod
    def to_token(text):
        if len(text) != 8 or text[0] != "M" or text[1] not in "01" or not text[2:].isdigit():
            return None
        r, c = int(text[2:5]), int(text[5:8])
        if text[1] == "0" and 0 <= r <= 11 and 1 <= c <= 13:
            return "%s-%s" % (sq(c - 1, r), sq(c, r))
        if text[1] == "1" and 1 <= r <= 11 and 0 <= c <= 13:
            return "%s-%s" % (sq(c, r - 1), sq(c, r))
        return None

    @staticmethod
    def to_mac(token):
        (x1, y1), (x2, y2) = sorted(xy(d) for d in token.split("-"))
        if y1 == y2:
            return "M0%03d%03d" % (y1, max(x1, x2))
        return "M1%03d%03d" % (max(y1, y2), x1)

    async def from_mac(self, text):
        tok = self.to_token(text)
        if tok:
            await self.s.move(tok)
        else:
            self.s.note("Dots&Boxes sent %r" % text[:20])

    async def on_event(self, kind, args):
        if kind == "LINE" and args and args[0] == str(1 - self.s.seat):
            await self.s.send(self.to_mac(args[1]))


class BattleShips(Game):
    """BattleShips [RHS]: the fleet (column, row, h or v for each of the five ships, largest first), the
    squares of a salvo as they are marked (toggles), Fire, and Done once the firing player has seen the
    salvo land. A Done from GameMaster also says it is ready for the other side's salvo."""

    FLEET = (5, 4, 3, 3, 2)     # carrier, battleship, submarine, destroyer, tug: the salvo ruleset's order

    def __init__(self, s):
        super().__init__(s)
        self.marked = set()     # GameMaster's squares marked for its salvo
        self.incoming = []      # the other side's shots, sent to GameMaster as one salvo
        self.salvo_done = False     # the other side's salvo is complete (the turn came back)
        self.mac_ready = False      # GameMaster sent Done: it waits for the other side's salvo
        self.placed = False         # GameMaster's fleet is placed at the table
        self.fleet_sent = False     # the other fleet is sent to GameMaster

    @staticmethod
    def fleet_tokens(text):
        if len(text) != 15:
            return None
        out = []
        for i, n in enumerate(BattleShips.FLEET):
            x, y, d = text[3 * i], text[3 * i + 1], text[3 * i + 2]
            if not (x.isdigit() and y.isdigit() and d in "hv"):
                return None
            x, y = int(x), int(y)
            if (x if d == "h" else y) + n > 10:
                return None
            out.append("%s-%s" % (sq(x, y), sq(x + n - 1, y) if d == "h" else sq(x, y + n - 1)))
        return out

    @staticmethod
    def fleet_text(lines):
        """OF lines -> GameMaster's fleet string, or None if the fleet is not all there."""
        out = ""
        for l in lines:
            f = l.split()
            if len(f) == 3 and f[0] == "OF":
                a, b = (xy(p) for p in f[2].split("-"))
                (x, y), across = min(a, b), a[1] == b[1] and f[1] != "1"
                out += "%d%d%s" % (x, y, "h" if across else "v")
        return out if len(out) == 15 else None

    async def from_mac(self, text):
        if len(text) == 15 and not self.placed:
            toks = self.fleet_tokens(text)
            if toks is None:
                return self.s.note("BattleShips sent a fleet we cannot read")
            for t in toks + ["ready"]:
                await self.s.move(t)
            self.placed = True
            await self.send_fleet()
        elif len(text) == 2 and text.isdigit():
            self.marked ^= {(int(text[0]), int(text[1]))}
        elif text == "Fire":
            shots, self.marked = sorted(self.marked, key=lambda p: (p[1], p[0])), set()
            for x, y in shots:              # in the order GameMaster reveals them
                if self.s.over:
                    return
                await self.s.move(sq(x, y))
        elif text == "Done":
            self.mac_ready = True
            await self.flush()
        elif text != "Resync":
            self.s.note("BattleShips sent %r" % text[:20])

    async def after_end(self, text):
        if text == "Done":
            self.mac_ready = True
            await self.flush()

    async def send_fleet(self):
        if self.fleet_sent:
            return
        data, final = await self.s.core.req("VUE %s" % self.s.tid)
        fleet = self.fleet_text(data) if final.startswith("OK") else None
        if fleet:
            self.fleet_sent = True
            await self.s.send(fleet)

    async def on_event(self, kind, args):
        other = str(1 - self.s.seat)
        if kind == "READY":
            await self.send_fleet()
        elif kind == "SHOT" and args and args[0] == other:
            self.incoming.append(xy(args[1]))
        elif kind == "TURN" and args and args[0] == str(self.s.seat) and self.incoming:
            self.salvo_done = True
            await self.flush()
        elif kind == "END" and self.incoming:
            self.salvo_done = True
            await self.flush()

    async def flush(self):
        """The other side's salvo, once it is complete and GameMaster waits for it: its squares, Fire and
        Done (the firing side's two messages)."""
        if not (self.salvo_done and self.mac_ready and self.incoming):
            return
        shots, self.incoming, self.salvo_done, self.mac_ready = self.incoming, [], False, False
        for x, y in shots:
            await self.s.send("%d%d" % (x, y))
        await self.s.send("Fire")
        await self.s.send("Done")


class ParlorGame(Game):
    """ParlorNet's own game rulebooks (their rulebooks/common/rb.h): the same few messages for every game, and the core
    as the authority. The Mac sends M and its move; the extension plays it at the table and answers A and the
    move, which the Mac shows only then, or R and the core's reason, and the Mac takes the move back. The
    other player's move goes to the Mac as M; the Mac answers A. D offers a draw, DY and DN answer one, G
    resigns, and E and the core's END words end the game on the Mac.

    The record a rulebook uploads starts with a 1024-byte header: the game's code at 0 (4 bytes), the
    record's layout at 4, the seat to move at 8 and the game over flag at 9. The game's own fields follow."""

    MAGIC = b""
    HEAD = 1024
    DRAWS = False               # the game has draw offers (DRAW, ACCEPT and REJECT events)
    MOVES = ()                  # the events of a move, for one played for this seat elsewhere (the web, telnet)

    def __init__(self, s):
        super().__init__(s)
        self.theirs = []        # the other player's steps of this turn, as tokens
        self.stale = False      # the Mac's board missed a move: the end of the turn sends the whole position

    # ---- the whole position (a resumed table, and whenever the Mac's board is behind) ---------------------
    async def send_position(self, view=None):
        """POS seat turn offer, the game's own lines from the core's view, and POE: the Mac redraws from them
        (rb.h). view: VUE's lines, or None to ask the core now."""
        if view is None:
            view, final = await self.s.req("VUE %s" % self.s.tid)
            if not final.startswith("OK"):
                return
        turn, offer = 0, 0
        for l in view:
            f = l.split()
            if len(f) == 2 and f[0] == "W" and f[1].isdigit():
                turn = int(f[1])
            elif self.DRAWS and len(f) == 2 and f[0] == "D" and f[1].isdigit():
                offer = int(f[1]) + 1
        self.theirs, self.stale = [], self.stale_after(view)
        for l in ["POS %d %d %d" % (self.s.seat, turn, offer)] + self.position(view) + ["POE"]:
            await self.s.send(l)

    def position(self, view):
        """The game's own lines of a whole position, from VUE's lines."""
        return []

    def stale_after(self, view):
        """True when the position shows the other player's turn half made (a staged turn): its end then sends
        the position again rather than the rest of the move."""
        return False

    @classmethod
    def fresh(cls, state):
        if len(state) < cls.HEAD or state[:4] != cls.MAGIC:
            return True                 # not our record: the question stays open
        return state[8] == 0 and state[9] == 0 and cls.fresh_game(state[cls.HEAD:])

    @staticmethod
    def fresh_game(data):
        return True

    # ---- the Mac's messages ------------------------------------------------------------------------
    async def from_mac(self, text):
        verb, _, arg = text.partition(" ")
        if verb == "M" and arg:
            await self.mac_move(arg)
        elif verb == "D" and not arg:
            await self.core_or_refuse("MOV %s draw" % self.s.tid)
        elif verb in ("DY", "DN") and not arg:
            await self.core_or_refuse("MOV %s %s" % (self.s.tid, "accept" if verb == "DY" else "reject"))
        elif verb == "G" and not arg:
            await self.core_or_refuse("RES %s" % self.s.tid)
        elif verb == "R":
            # the Mac refused a move the core played: the two no longer agree (a resumed table is sent the
            # whole position again; any other game cannot go on)
            self.s.note("the Mac refused the move it was sent: %s" % arg[:60])
            raise Desync(arg)
        elif verb not in ("A", "E"):        # A and E: the Mac's answers to the server's own messages
            self.s.note("%s sent %r" % (self.NAME, text[:20]))

    async def after_end(self, text):
        if text.split()[:1] in (["M"], ["D"], ["DY"], ["DN"], ["G"]):
            await self.s.send("R the game is over")

    async def core_or_refuse(self, line):
        """A request for the Mac's player; R and the core's reason to the Mac when it is refused."""
        _, final = await self.s.req(line)
        if not final.startswith("OK"):
            await self.s.send("R %s" % err_text(final)[:200])
            return False
        return True

    async def mac_move(self, move):
        """The Mac's whole move: the core's steps, then END for a staged turn. All or nothing."""
        tokens = self.to_core(move)
        if tokens is None:
            await self.s.send("R that is not a move")
            return
        played = 0              # steps played, to take back if the move is refused
        final = "OK"
        for tok in tokens:
            _, final = await self.s.req("MOV %s %s" % (self.s.tid, tok))
            if not final.startswith("OK"):
                break
            played += self.steps(tok)
        if final.startswith("OK") and final.split()[1:2] == ["more"]:
            final = "ERR ILLEGAL the move is not finished"
        if final.startswith("OK") and final.split()[1:2] == ["done"]:
            _, final = await self.s.req("END %s" % self.s.tid)
        if not final.startswith("OK"):
            for _ in range(played):
                await self.s.req("UND %s" % self.s.tid)
            self.s.note("the core refused %s: %s" % (move[:40], err_text(final)))
            await self.s.send("R %s" % self.refusal(move, err_text(final))[:200])
            return
        await self.answer(move)

    async def answer(self, move):
        """The core took the Mac's whole move: A, which the Mac waits for, then after_move."""
        await self.s.send("A %s" % move)
        await self.after_move()

    async def after_move(self):
        """After a move of either side: what the game tells the Mac besides the moves (Grid Words' rack)."""

    @staticmethod
    def refusal(move, reason):
        """The core's reason for refusing the Mac's move, as the player reads it."""
        return reason

    @classmethod
    def to_core(cls, move):
        """The Mac's move -> the core's tokens, or None."""
        return [move]

    @staticmethod
    def steps(token):
        """The steps of a staged turn a token plays."""
        return 1

    # ---- the table's events ------------------------------------------------------------------------
    async def on_event(self, kind, args):
        other = str(1 - self.s.seat)
        if kind in self.MOVES and args[:1] == [str(self.s.seat)] and not self.s.made_here():
            self.stale = True       # this seat's move, made at the table from somewhere else
        if kind == "TURN" or kind == "END":
            await self.flush()
        if self.DRAWS and kind == "DRAW" and args[:1] == [other]:
            await self.s.send("D")
        elif self.DRAWS and kind == "ACCEPT" and args[:1] == [other]:
            await self.s.send("DY")
        elif self.DRAWS and kind == "REJECT" and args[:1] == [other]:
            await self.s.send("DN")
        elif kind == "END":
            await self.s.send("E %s" % " ".join(args))
        elif args[:1] == [other]:
            self.their_event(kind, args[1:])

    def their_event(self, kind, args):
        """An event of the other player's turn."""

    async def flush(self):
        """The other player's moves, once their turn is over; the whole position instead when the Mac's board
        missed a move (stale)."""
        if self.stale and self.s.positions:
            await self.send_position()
            return
        moves = self.their_moves()
        self.theirs = []
        for move in moves:
            await self.s.send("M %s" % move)
        if moves:
            await self.after_move()

    def their_moves(self):
        return []


class Checkers(ParlorGame):
    """Checkers [ParlorNet]: checkers/1, ruleset american. A move is its squares, joined by - for a step and x for
    jumps (11-15, 15x22x31), which the core takes as one token. The record holds the 32 squares at 1024."""

    NAME = "Checkers"
    MAGIC = b"PNck"
    DRAWS = True
    MOVES = ("STEP", "JUMP")
    MOVE_RE = re.compile(r"^\d{1,2}(-\d{1,2}|(x\d{1,2})+)\Z")

    def position(self, view):
        """BD and the 32 squares (. x X o O, as the record keeps them), L and the quiet turns and their limit."""
        out = []
        for l in view:
            f = l.split()
            if len(f) == 2 and f[0] == "B" and len(f[1]) == 32:
                out.append("BD " + f[1])
            elif len(f) == 3 and f[0] == "L":
                out.append(l)
        return out

    def stale_after(self, view):
        return any(l.startswith("J ") for l in view)

    @staticmethod
    def half_made(view, seat):
        """The view shows seat's own turn half made (steps staged and not ended)."""
        return any(l.startswith("J ") for l in view) and "W %d" % seat in view

    @staticmethod
    def fresh_game(data):
        return data[:32] == b"x" * 12 + b"." * 8 + b"o" * 12

    @classmethod
    def to_core(cls, move):
        return [move] if cls.MOVE_RE.match(move) else None

    @staticmethod
    def steps(token):
        return len(re.findall(r"[-x]", token))

    def their_event(self, kind, args):
        if kind in ("STEP", "JUMP") and len(args) >= 2:
            if not self.theirs:
                self.theirs = [args[0]]
            self.theirs.append(("x" if kind == "JUMP" else "-") + args[1])
        elif kind == "UNDO" and len(self.theirs) > 1:
            self.theirs.pop()
            if len(self.theirs) == 1:
                self.theirs = []

    def their_moves(self):
        return ["".join(self.theirs)] if len(self.theirs) > 1 else []


class Mancala(ParlorGame):
    """Mancala [ParlorNet]: mancala/1, ruleset kalah, four seeds a pit. A move is the pit's number, 1 to 6, from
    the mover's left, as the core takes it. A last seed in the mover's store gives another move, so one turn
    can be several moves; each goes to the Mac as its own M. The record holds the 14 counts at 1024 (seat 0's
    pits and store, then seat 1's)."""

    NAME = "Mancala"
    MAGIC = b"PNmc"
    MOVES = ("SOW",)

    def position(self, view):
        """BD and the 14 counts (seat 0's pits and store, then seat 1's), LM and the last move (seat, pit)."""
        out = []
        for l in view:
            f = l.split()
            if len(f) == 15 and f[0] == "B":
                out.append("BD " + " ".join(f[1:]))
            elif len(f) == 3 and f[0] == "LM":
                out.append(l)
        return out

    @staticmethod
    def fresh_game(data):
        return data[:14] == bytes([4] * 6 + [0] + [4] * 6 + [0])

    @classmethod
    def to_core(cls, move):
        return [move] if len(move) == 1 and move in "123456" else None

    def their_event(self, kind, args):
        if kind == "SOW" and args:
            self.theirs.append(args[0])

    def their_moves(self):
        return list(self.theirs)


class FiveInARow(ParlorGame):
    """Five in a Row [ParlorNet]: fiveinarow/1, ruleset freestyle, 15 by 15. A move is a point, a column letter
    and a row number (h8), as the core takes it. The record holds the 225 points at 1024, row by row, 0 for an
    empty one."""

    NAME = "Five in a Row"
    MAGIC = b"PNfr"
    MOVES = ("PUT",)
    POINT_RE = re.compile(r"^[a-o](1[0-5]|[1-9])\Z")

    def position(self, view):
        """BD and the 225 points row by row from the top (0 empty, 1 Black, 2 White), LS and the last stone."""
        cells = ["0"] * 225
        out = []
        for l in view:
            f = l.split()
            if len(f) >= 2 and f[0] == "P" and f[1] in ("0", "1"):
                for p in f[2:]:
                    if self.POINT_RE.match(p):
                        x, y = xy(p)
                        cells[y * 15 + x] = str(int(f[1]) + 1)
            elif len(f) == 3 and f[0] == "L" and self.POINT_RE.match(f[2]):
                out.append("LS " + f[2])
        return ["BD " + "".join(cells)] + out

    @staticmethod
    def fresh_game(data):
        return not any(data[:225])

    @classmethod
    def to_core(cls, move):
        return [move] if cls.POINT_RE.match(move) else None

    def their_event(self, kind, args):
        if kind == "PUT" and args:
            self.theirs.append(args[0])

    def their_moves(self):
        return list(self.theirs)


class GridWords(ParlorGame):
    """Grid Words [ParlorNet]: gridwords/1, ruleset classic, two players. The parlor deals and keeps the bag, so
    the rulebook plays only on a parlor. Moves are the core's own tokens: PLAY x y H|V letters, EXCH letters,
    PASS. Besides the shared messages the Mac is sent K and its rack (as the view's R line), S and the two
    scores, and B and the tiles in the bag, when the game starts and after every move. The other player's
    play goes to the Mac as PLAY with the word as it stands on the board (a blank's letter in lower case),
    an exchange as EXCH and the count, a pass as PASS. The record holds the 225 squares at 1024."""

    NAME = "Grid Words"
    MAGIC = b"PNgw"
    MOVES = ("PLAY", "EXCH", "PASS")

    def position(self, view):
        """BD and the 225 squares row by row (. empty, a letter, a blank's letter in lower case), K and this
        player's rack, S and the two scores, B and the bag, and LP x y H|V length for the last play."""
        cells, rack, scores, bag, last = ["."] * 225, "-", {}, 0, []
        for l in view:
            f = l.split()
            if f[:1] == ["X"]:
                for i in range(1, len(f) - 2, 3):
                    x, y, c = f[i], f[i + 1], f[i + 2]
                    if x.isdigit() and y.isdigit() and 1 <= int(x) <= 15 and 1 <= int(y) <= 15 and c.isalpha():
                        cells[(int(y) - 1) * 15 + int(x) - 1] = c[0]
            elif len(f) == 2 and f[0] == "R":
                rack = f[1]
            elif len(f) == 3 and f[0] == "T" and f[1].isdigit():
                scores[int(f[1])] = f[2]
            elif len(f) == 2 and f[0] == "BAG" and f[1].isdigit():
                bag = int(f[1])
            elif len(f) == 7 and f[0] == "LAST":
                last = ["LP %s %s %s %d" % (f[2], f[3], f[4], len(f[5]))]
        return ["BD " + "".join(cells), "K %s" % rack, "S %s %s" % (scores.get(0, "0"), scores.get(1, "0")),
                "B %d" % bag] + last
    PLAY_RE = re.compile(r"^PLAY (1[0-5]|[1-9]) (1[0-5]|[1-9]) [HV] (_?[A-Z]){1,15}\Z")
    EXCH_RE = re.compile(r"^EXCH [A-Z_]{1,7}\Z")

    @staticmethod
    def fresh_game(data):
        return not any(data[:225])

    @classmethod
    def to_core(cls, move):
        return [move] if move == "PASS" or cls.PLAY_RE.match(move) or cls.EXCH_RE.match(move) else None

    @staticmethod
    def refusal(move, reason):
        """In the player's terms, as the web and telnet say it: 'Can't play HEX across from g4: ...'."""
        from .games import gridwords
        return gridwords.refusal(move, reason)

    async def on_start(self):
        await self.after_move()

    async def after_move(self):
        data, final = await self.s.req("VUE %s" % self.s.tid)
        if not final.startswith("OK"):
            return
        rack, scores, bag = "-", {}, 0
        for l in data:
            f = l.split()
            if len(f) == 2 and f[0] == "R":
                rack = f[1]
            elif len(f) == 3 and f[0] == "T" and f[1].isdigit():
                scores[int(f[1])] = f[2]
            elif len(f) == 2 and f[0] == "BAG" and f[1].isdigit():
                bag = int(f[1])
        await self.s.send("K %s" % rack)
        await self.s.send("S %s %s" % (scores.get(0, "0"), scores.get(1, "0")))
        await self.s.send("B %d" % bag)

    def their_event(self, kind, args):
        if kind == "PLAY" and len(args) >= 4:
            self.theirs.append("PLAY %s %s %s %s" % tuple(args[:4]))
        elif kind == "EXCH" and args:
            self.theirs.append("EXCH %s" % args[0])
        elif kind == "PASS":
            self.theirs.append("PASS")

    def their_moves(self):
        return list(self.theirs)


class RummyTile(ParlorGame):
    """Rummy Tile [ParlorNet]: rummytile/1, ruleset standard, two players. The parlor deals and keeps the pool,
    so the rulebook plays only on a parlor. A turn is staged: the Mac sends each step as the core's own token
    (R7B7K7-n, R7-3, 3.2-5, 3.2-r, 3-5, 3/4, reset), and UND and END, each played at once; A or R answers.
    After each step of the Mac's, and whenever the turn passes, the Mac is sent the whole state from the view:
    Z, then ST and each set, H and the rack, TP, P, C, I and W, which ends it. The record holds the number of
    sets on the table at 1024 (0 for a new game)."""

    NAME = "Rummy Tile"
    MAGIC = b"PNrt"
    T = r"(?:[RBKY](?:1[0-3]|[1-9])|J)"
    STEP_RE = re.compile(r"^(?:%s{1,13}-(?:\d{1,2}|n)|\d{1,2}\.(?:\d{1,2}|%s)-(?:\d{1,2}|n|r)|\d{1,2}-\d{1,2}|"
                         r"\d{1,2}/\d{1,2}|reset)\Z" % (T, T), re.I)

    @staticmethod
    def fresh_game(data):
        return data[:2] == b"\x00\x00"

    @classmethod
    def to_core(cls, move):
        return [move] if move in ("UND", "END") or cls.STEP_RE.match(move) else None

    async def on_start(self):
        await self.after_move()

    async def mac_move(self, move):
        if self.to_core(move) is None:
            await self.s.send("R that is not a step")
            return
        line = "%s %s" % (move, self.s.tid) if move in ("UND", "END") else "MOV %s %s" % (self.s.tid, move)
        _, final = await self.s.req(line)
        if not final.startswith("OK"):
            await self.s.send("R %s" % err_text(final)[:200])
            return
        await self.s.send("A %s" % move)
        if move != "END":               # after END the turn passes, and the state follows (flush)
            await self.after_move()

    async def flush(self):
        self.theirs = []
        await self.after_move()

    @staticmethod
    def state(data):
        got = {l.split()[0]: l for l in data if l.split() and l.split()[0] in ("TP", "P", "C", "I", "W")}
        return ["Z"] + [l for l in data if l.startswith(("ST ", "H "))] + \
            [got[k] for k in ("TP", "P", "C", "I", "W") if k in got]

    def position(self, view):
        """The state it is sent after every step (Z ... W)."""
        self.shown = self.state(view)
        return self.shown

    async def after_move(self):
        data, final = await self.s.req("VUE %s" % self.s.tid)
        if not final.startswith("OK"):
            return
        lines = self.state(data)
        if lines == getattr(self, "shown", None):
            return                      # the Mac has this state already
        self.shown = lines
        for l in lines:
            await self.s.send(l)


class CannonFire(ParlorGame):
    """Cannon Fire [ParlorNet]: cannonfire/1, ruleset classic, two players. The physics run in the core, so the
    rulebook plays only on a parlor: the Mac sends FIRE angle power and MOVE L|R n, the core's own tokens, and
    draws what it is sent: F seat and the shell's path (TRJ x y ...) for every shot, and the state from the
    view (Z, K, AIM for the view's A, L, N, H, B, T, then W, which ends it) at the start, after a move and when
    the turn passes.
    The record holds the arena's width at 1024 (0 until the parlor sends it)."""

    NAME = "Cannon Fire"
    MAGIC = b"PNcf"
    MOVE_RE = re.compile(r"^(FIRE (180|1[0-7]\d|\d{1,2}) (100|\d{1,2})|MOVE [LR] [1-9]\d{0,2})\Z")
    STATE = ("Z", "K", "A", "L", "N", "H", "B", "T")

    @staticmethod
    def fresh_game(data):
        return data[:2] == b"\x00\x00"

    @classmethod
    def to_core(cls, move):
        return [move] if cls.MOVE_RE.match(move) else None

    async def on_start(self):
        await self.after_move()

    answered = None

    async def answer(self, move):
        # A and the state (Z ... W) wait for the TURN, MOVE or END event that follows the Mac's own move (the
        # session handles the Mac's messages and the table's events one at a time, so that event comes after
        # this). W redraws the arena, so it must come after the shot's F and TRJ, or the Mac draws the new burst
        # with the last shell's path; and A, which ends the Mac's wait, comes after W, or the Mac shows its
        # own turn again until W arrives.
        self.answered = move

    async def on_event(self, kind, args):
        if kind == "FIRE" and args:
            await self.s.send("F %s" % args[0])
        elif kind == "TRJ":
            await self.s.send("TRJ %s" % " ".join(args))
        elif kind in ("TURN", "MOVE", "END"):
            await self.after_move()
            if self.answered:
                await self.s.send("A %s" % self.answered)
                self.answered = None
        if kind == "END":
            await self.s.send("E %s" % " ".join(args))

    def state(self, data):
        # A (the aim) goes as AIM: A is the shared answer to a move
        return [("AIM" + l[1:] if l.startswith("A ") else l) for l in data
                if l.split()[:1] and l.split()[0] in self.STATE] + [l for l in data if l.startswith("W ")]

    def position(self, view):
        """The terrain, the tanks, the aims, the moves left, the wind, the last burst, the budget, the points and
        the turn, as after every shot (Z ... W)."""
        self.shown = self.state(view)
        return self.shown

    async def after_move(self):
        data, final = await self.s.req("VUE %s" % self.s.tid)
        if not final.startswith("OK"):
            return
        lines = self.state(data)
        if lines == getattr(self, "shown", None):
            return
        self.shown = lines
        for l in lines:
            await self.s.send(l)


class Backgammon(ParlorGame):
    """Backgammon [ParlorNet]: backgammon/1, ruleset standard, with the doubling cube. The core rolls the dice, so
    the rulebook plays only on a parlor. The Mac stages a turn by the same rules and sends it whole: M and its
    steps (13-9 9-4, bar-22, 6-off), each played at the table as a MOV, then END; if the core refuses one, the
    steps already played are taken back (UND) and R gives the reason. M roll, double, take, drop, accept and
    reject are the core's own words, and M end ends a turn whose steps were all played elsewhere.

    After every change the Mac is sent the whole state from the view: BD and the 24 points (seat 0's checkers
    counted up, seat 1's down), the two bars and the two trays; CU the cube (value and owner, - in the middle);
    DI the dice left to play; DR the dice rolled; N the steps the roll needs; CA who may double; RS a
    resignation offered; LM the last turn played (seat, dice, and its steps or pass); Q the phase; and W, which
    ends it. The A that answers the Mac's move comes after the state it leads to, so the Mac never shows the
    old turn again. H left or H right says which side the player keeps the home board on (PRF bghome, this
    extension's own, as the telnet extension keeps its own); the Mac sends H when the player changes it. The
    record holds the 24 points at 1024, the starting position for a new game."""

    NAME = "Backgammon"
    MAGIC = b"PNbg"
    WORDS = ("roll", "double", "take", "drop", "accept", "reject", "end")
    STEP_RE = re.compile(r"^(bar|1\d|2[0-4]|[1-9])-(1\d|2[0-4]|[1-9]|off)\Z")
    START = bytes(v & 0xFF for v in (-2, 0, 0, 0, 0, 5, 0, 3, 0, 0, 0, -5, 5, 0, 0, 0, -3, 0, -5, 0, 0, 0, 0, 2))
    STATE = ("TURN", "ROLL", "DBL", "TAKE", "DROP", "OFFER", "ACCEPT", "REJECT", "PASS", "END")

    def __init__(self, s):
        super().__init__(s)
        self.answered = None    # the Mac's move, answered A once the state it leads to has gone
        self.turn = None        # this turn so far: [seat, die, die, steps]
        self.last = None        # the last turn played, as its LM line

    @classmethod
    def fresh_game(cls, data):
        return data[:28] == cls.START + bytes(4)

    @classmethod
    def to_core(cls, move):
        if move in cls.WORDS:
            return [move]
        steps = move.split()
        return steps if 1 <= len(steps) <= 4 and all(cls.STEP_RE.match(t) for t in steps) else None

    async def from_mac(self, text):
        if text in ("H left", "H right"):
            await self.s.req("PRF SET %s %s" % (BG_HOME_PREF, text[2:]))
            return
        await super().from_mac(text)

    async def after_end(self, text):
        if text in ("H left", "H right"):
            await self.s.req("PRF SET %s %s" % (BG_HOME_PREF, text[2:]))
            return
        await super().after_end(text)

    async def home(self):
        """H and the side of the home board the player keeps (right unless kept as left)."""
        data, final = await self.s.req("PRF GET %s" % BG_HOME_PREF)
        got = [l.split()[2].lower() for l in data if len(l.split()) == 3 and l.split()[:2] == ["PRF", BG_HOME_PREF]]
        await self.s.send("H %s" % (got[0] if got and got[0] in BG_HOMES else BG_HOMES[0]))

    async def on_start(self):
        await self.home()
        await self.after_move()

    async def send_position(self, view=None):
        await self.home()
        await super().send_position(view)

    async def mac_move(self, move):
        tokens = self.to_core(move)
        if tokens == [move] and move in self.WORDS:
            line = "END %s" % self.s.tid if move == "end" else "MOV %s %s" % (self.s.tid, move)
            _, final = await self.s.req(line)
            if not final.startswith("OK"):
                await self.s.send("R %s" % err_text(final)[:200])
                return
            await self.answer(move)
            return
        await super().mac_move(move)

    async def answer(self, move):
        self.answered = move

    def track(self, kind, args):
        """The turn being played, from its events, and the last one played (LM)."""
        if kind == "ROLL" and len(args) >= 3:
            self.turn = args[:3] + [[]]
        elif kind == "STEP" and self.turn and len(args) >= 3 and args[0] == self.turn[0]:
            self.turn[3].append("%s-%s%s" % (args[1], args[2], "*" if "HIT" in args[3:] else ""))
        elif kind == "UNDO" and self.turn and self.turn[3]:
            self.turn[3].pop()
        elif kind == "PASS" and self.turn:
            self.last, self.turn = "LM %s %s %s pass" % tuple(self.turn[:3]), None
        elif kind in ("TURN", "END") and self.turn and self.turn[3]:
            self.last, self.turn = "LM %s %s %s %s" % tuple(self.turn[:3] + [" ".join(self.turn[3])]), None
        elif kind in ("OPEN", "GAME"):
            self.last = self.turn = None

    async def on_event(self, kind, args):
        self.track(kind, args)
        if kind in self.STATE:
            await self.after_move()
            if self.answered:
                await self.s.send("A %s" % self.answered)
                self.answered = None
        if kind == "END":
            await self.s.send("E %s" % " ".join(args))

    def state(self, view):
        got = {}
        for l in view:
            f = l.split()
            if f and f[0] in ("P", "B", "O", "D", "C", "DR", "N", "CA", "RS", "Q", "W"):
                got.setdefault(f[0], []).append(f[1:])
        pts = {p[0]: p[1:] for p in got.get("P", []) if p and p[0] in ("1", "13")}
        if len(pts.get("1", [])) != 12 or len(pts.get("13", [])) != 12:
            return []
        out = ["BD " + " ".join(pts["1"] + pts["13"] + (got.get("B") or [["0", "0"]])[0][:2] +
                                (got.get("O") or [["0", "0"]])[0][:2]),
               "CU " + " ".join((got.get("C") or [["1", "-"]])[0][:2]),
               " ".join(["DI"] + (got.get("D") or [[]])[0]),
               " ".join(["DR"] + (got.get("DR") or [[]])[0][:2]),
               "N " + (got.get("N") or [["0"]])[0][0],
               "CA " + " ".join((got.get("CA") or [["0", "0"]])[0][:2])]
        out += ["RS " + " ".join(r) for r in got.get("RS", [])[:1]]
        out += [self.last] if self.last else []
        out += ["Q " + (got.get("Q") or [["move"]])[0][0], "W " + (got.get("W") or [["-"]])[0][0]]
        return out

    def position(self, view):
        """The state it is sent after every change (BD ... W)."""
        self.shown = self.state(view)
        return self.shown

    async def after_move(self):
        data, final = await self.s.req("VUE %s" % self.s.tid)
        if not final.startswith("OK"):
            return
        lines = self.state(data)
        if not lines or lines == getattr(self, "shown", None):
            return
        self.shown = lines
        for l in lines:
            await self.s.send(l)


RULEBOOKS = {rb.rb_id: rb for rb in (
    Rulebook("Dots&Boxes [PNL]", 0x0100, "dotsandboxes/1", "majority", DotsAndBoxes),
    Rulebook("Othello [RHS]", 0x0020, "reversi/1", "standard", Othello, swaps=True),
    Rulebook("BattleShips [RHS]", 0x0020, "navalcombat/1", "salvo", BattleShips),
    Rulebook("Checkers [ParlorNet]", 0x0100, "checkers/1", "american", Checkers, swaps=True),
    Rulebook("Mancala [ParlorNet]", 0x0100, "mancala/1", "kalah", Mancala, swaps=True),
    Rulebook("Five in a Row [ParlorNet]", 0x0100, "fiveinarow/1", "freestyle", FiveInARow, swaps=True),
    # english, the web's and telnet's first ruleset, differs from classic only in the scoreless turns that end
    # a game (two rounds rather than six turns), which the parlor counts and ends with END; the rulebook keeps
    # no count of its own (plugins/gridwords, docs/types/gridwords-1.md)
    Rulebook("Grid Words [ParlorNet]", 0x0100, "gridwords/1", "classic", GridWords, swaps=True, also=("english",)),
    Rulebook("Rummy Tile [ParlorNet]", 0x0100, "rummytile/1", "standard", RummyTile, swaps=True),
    Rulebook("Cannon Fire [ParlorNet]", 0x0100, "cannonfire/1", "classic", CannonFire, swaps=True),
    Rulebook("Backgammon [ParlorNet]", 0x0100, "backgammon/1", "standard", Backgammon, swaps=True),
)}


# ---- the service --------------------------------------------------------------------------------------

class Player:
    """The player signed in on a tunnel."""

    def __init__(self, name, sid):
        self.name, self.sid = name, sid


class Pending:
    """A call answered with its tunneled game port, waiting for the caller's game connection (GMS)."""

    def __init__(self, sid, call, rb, addr, player, tunnel, choice):
        self.sid, self.call, self.rb, self.addr = sid, call, rb, addr
        self.player = player        # the tunnel's sign-in
        self.tunnel = tunnel        # the tunnel it came by
        self.choice = choice        # the opponent or table the call asked for (GMO's who)
        self.arrived = asyncio.Event()


class Service:
    def __init__(self, args):
        self.args = args
        self.gate = Gate(MAX_CALLS, per_addr=PER_ADDR)
        self.gate.notice = None                 # GameMaster speaks messages, not text
        self.server_name = "ParlorNet"
        self.docs = ("", 0.0)                   # the docs site's address from PRL, and when it was asked

    async def core(self, sid=None, addr=""):
        """A connection to the core: resumed (SES) when sid is given, else new with the client's address."""
        c = await CoreClient.connect(self.args.core_host, self.args.core_port)
        f = c.greeting.split()
        if len(f) > 1:
            self.server_name = f[1]
        try:
            if sid:
                await c.resume(sid)
            else:
                await c.source(addr)
        except BaseException:
            c.close()
            raise
        return c

    # ---- the port: the ParlorNet rulebook's tunnel ------------------------------------------------------
    async def on_call(self, reader, writer):
        addr = peer_addr(writer)
        ticket = self.gate.admit(addr, writer)
        if ticket is None:
            writer.close()
            return
        keepalive(writer)
        sid = next(ids)
        try:
            lines = native.Lines(reader)
            loop = asyncio.get_running_loop()
            start = loop.time()
            try:
                head = await asyncio.wait_for(reader.readexactly(1), self.args.opening_secs)
                if head == b"\0":
                    await self.refuse(sid, addr, reader, writer, head)
                    return
                lines.buf += head
                eol = await lines.start(self.args.opening_secs - (loop.time() - start), native.ENDING_SECS)
            except asyncio.TimeoutError:
                await self.bad_opening(writer, *native.WRONG)
                return
            except native.BadOpening as e:
                await self.bad_opening(writer, e.code, e.text)
                return
            if eol is not None:
                await Tunnel(self, sid, addr, reader, writer, ticket, bytes(lines.buf)).run()
        except (ConnectionError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            print("gamemaster call error: %r" % e, file=sys.stderr, flush=True)
        finally:
            ticket.release()
            try:
                writer.close()
            except Exception:
                pass

    async def docs_url(self):
        """The docs site's address as the core's PRL names it (DOC; "" when the operator cleared it or the core
        cannot be reached), asked for at most every DOCS_SECS."""
        url, when = self.docs
        if time.time() - when > DOCS_SECS:
            url = ""
            try:
                c = await asyncio.wait_for(CoreClient.connect(self.args.core_host, self.args.core_port), 10)
                try:
                    data, final = await asyncio.wait_for(c.req("PRL"), 10)
                finally:
                    c.close()
                if final.startswith("OK"):
                    url = next((l.split(" ", 1)[1] for l in data if l.startswith("DOC ")), "")
            except (CoreError, OSError, asyncio.TimeoutError):
                pass
            self.docs = (url, time.time())
        return url

    async def bad_opening(self, writer, code, text):
        """A tunnel that did not open with PARLORNET/1: the ERR line, as the core's public port sends it."""
        writer.write(native.opening_error(code, text, await self.docs_url()))
        await writer.drain()

    @staticmethod
    async def refuse(sid, addr, reader, writer, head):
        """A call from GameMaster by itself, outside the ParlorNet rulebook: GameMaster's own refusal, which the
        Mac shows as "<address> isn't interested.", and one line in the log."""
        call = CallRecord(head + await asyncio.wait_for(reader.readexactly(CALL_SIZE - len(head)), CALL_SECS))
        log(sid, "a GameMaster call from %s without the ParlorNet rulebook (%s): refused" % (
            addr, call.rb_id[:40] or "?"))
        writer.write(control(DONT_SEND_CODE))
        await writer.drain()

    async def call(self, sid, addr, reader, writer, tunnel, n):
        """A call on the tunnel's connection n."""
        rec = await asyncio.wait_for(reader.readexactly(CALL_SIZE), CALL_SECS)
        call = CallRecord(rec)
        log(sid, "call from %s: %s %s (oldest %s), GameMaster %s, %d bytes of game" % (
            addr, call.rb_id[:40] or "?", bcd(call.rb_version), bcd(call.rb_oldest), bcd(call.version), call.size))
        if call.needs > GM_VERSION:
            log(sid, "needs GameMaster %s: refused" % bcd(call.needs))
            writer.write(control(TOO_OLD))
            await writer.drain()
            return
        rb = RULEBOOKS.get(call.rb_id)
        why = None
        if rb is None:
            why = "a rulebook this parlor does not play"
        elif not rb.takes(call):
            why = "a version this parlor does not play"
        elif call.size > MAX_STATE:
            why = "a game too large"
        elif tunnel.player is None:
            why = "nobody signed in on this tunnel"
        if why:
            log(sid, "%s: refused" % why)
            writer.write(control(DONT_SEND_CODE))
            await writer.drain()
            return
        conn = tunnel.conns.get(n)
        p = Pending(sid, call, rb, addr, tunnel.player, tunnel, getattr(conn, "choice", None))
        tunnel.pending[n] = p
        writer.write(control(SEND_GAME) + address_bytes("0.0.0.0", TUNNEL_PORT + n))
        await writer.drain()
        try:
            await asyncio.wait_for(p.arrived.wait(), GAME_SECS)
        except asyncio.TimeoutError:
            log(sid, "no game connection came")
        finally:
            if tunnel.pending.get(n) is p:
                del tunnel.pending[n]

    async def game(self, p, reader, writer):
        """The game connection that follows call p, tunneled."""
        s = Session(self, p, reader, writer)
        try:
            await s.run()
        except (ConnectionError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        except CoreError as e:
            log(p.sid, "the core: %s" % e)
        except Exception as e:
            print("gamemaster session error: %r" % e, file=sys.stderr, flush=True)
        finally:
            await s.finish()


class Desync(Exception):
    """The core refused what GameMaster did: the two sides no longer agree."""


class Session:
    """One game connection."""

    def __init__(self, service, pending, reader, writer):
        self.svc, self.p = service, pending
        self.sid, self.addr, self.rb = pending.sid, pending.addr, pending.rb
        self.r, self.w = reader, writer
        self.core = self.watch = None
        self.tid, self.seat, self.over, self.started = None, 0, False, False
        self.playing = False        # ~Play it Sam was sent
        self.closed = False
        self.queue = asyncio.Queue()
        self.early = []             # rulebook messages that came before the game started
        # the Mac's rulebook takes a whole position (POS): ParlorNet's own, from 1.1.0
        self.positions = issubclass(self.rb.game, ParlorGame) and pending.call.rb_version >= POSITIONS
        self.resumed = False        # a table already being played, taken up again: it is never resigned here
        self.view = None            # a resumed table's view, the position the Mac is sent first
        self.own = set()            # the numbers of the events this session's own requests made
        self.evt_n = 0              # the number of the event being handled
        self.clients = {}           # seat -> what its player plays with, in words (CLIENT; tell_opponent)

    def made_here(self):
        """The event being handled came from this session's own request (else from the table: the other
        player, or this player somewhere else)."""
        return self.evt_n in self.own

    def tell(self, text):
        """A line for the ParlorNet rulebook's lobby: INF and the text."""
        self.p.tunnel.send_line("INF " + text)

    def note(self, text):
        log(self.sid, text)

    async def put(self, data):
        self.w.write(data)
        await self.w.drain()

    async def ctl(self, text):
        await self.put(control(text))

    async def send(self, text):
        await self.put(rulebook(text))

    async def run(self):
        state = await asyncio.wait_for(self.r.readexactly(self.p.call.size), STATE_SECS) \
            if self.p.call.size else b""
        await self.ctl(JUST_A_MOMENT)
        if not self.rb.game.fresh(state):
            self.note("a game in progress was uploaded: turned down")
            await self.ctl(NOT_INTERESTED)
            return
        player = self.p.player
        try:
            self.core = await self.svc.core(player.sid)
            self.watch = await self.svc.core(player.sid)
        except CoreError as e:
            self.note("the tunnel's sign-in from %s has ended (%s)" % (self.addr, e))
            await self.ctl(NOT_INTERESTED)
            return
        await self.core.claim(*self.p.tunnel.claim(self.p.call))    # what the turns say they were played with
        self.name = player.name
        self.game = self.rb.game(self)
        start = await self.find_table()
        if start is None:
            if not self.playing:
                await self.ctl(NOT_INTERESTED)
            return                      # after ~Play it Sam (a swap declined), the hang-up says it
        if not self.playing:
            await self.play_it_sam()
        await self.tell_opponent()
        await self.play(start)

    async def tell_opponent(self):
        """The game has started: the lobby hears what the opponent plays with, as an INF line ("Table 12: bob
        plays with Netscape Navigator 3.0 on a Macintosh."), since GameMaster says only its own words when a
        game starts (~Play it Sam). Nothing when the opponent's program said nothing, or from a core older
        than LST's clients word."""
        try:
            data, final = await self.req("LST table=%s clients" % self.tid)
        except CoreError:
            return
        t = next((t for t in parse_tables(data) if t["id"] == self.tid), None) if final.startswith("OK") else None
        other = 1 - self.seat
        self.clients = dict(t["clients"]) if t else {}
        if t and other < len(t["seated"]) and t["seated"][other] != "-" and t["clients"].get(other):
            self.tell(self.client_text(t["seated"][other], t["clients"][other], "plays"))

    def client_text(self, name, say, verb):
        """'Table 12: bob plays with ...' (verb None: 'now plays', a change mid-game), within an INF line."""
        head = "Table %s: " % self.tid
        width = INF_WIDTH - len(head)
        return head + (client_change_sentence(name, say, width) if verb is None else
                       client_sentence(name, say, verb, width))

    async def play_it_sam(self):
        await self.ctl(PLAY_IT_SAM)
        self.playing = self.started = True

    # ---- finding a table ------------------------------------------------------------------------------
    async def req(self, text):
        data, final = await self.core.req(text)
        self.own.update(n for n, _, _ in events(data))
        return data, final

    async def find_table(self):
        """Seat the GameMaster player at a table of the rulebook's game; -> the event number to play from once
        the game has started, or None. Seat 0 when it can (the caller's new game moves first); seat 1 at a
        table where someone already holds seat 0, when the rulebook swaps sides and the Mac's player agrees."""
        key, rules, accepts = self.rb.key, self.rb.rules, self.rb.accepts
        choice = self.p.choice or "anyone"
        if choice.startswith("#"):
            return await self.join_table(choice[1:], key, accepts)
        if choice.startswith("@"):
            return await self.resume_table(choice[1:], key, accepts)
        if choice != "bot":
            for tid, seat, why in await self.existing(key, accepts, None if choice == "anyone" else choice):
                got = await self.sit(tid, seat, why)
                if got is not None or self.playing or self.closed:
                    return got
        if choice == "anyone":
            tid = await self.new_table(key, rules, "public")
            if tid is None:
                return None
            self.note("table %s open for anyone" % tid)
            got = await self.wait_start(tid, self.svc.args.wait, None, give_up=False)
            if got is not None or self.closed:
                return got
            choice = "bot"
        else:
            tid = await self.new_table(key, rules, "private")
            if tid is None:
                return None
        invitee = choice
        if choice == "bot":
            data, _ = await self.req("LST table=%s" % tid)
            t = next((t for t in parse_tables(data) if t["id"] == tid), None)
            invitee = botflag.next_name(t["names"] if t else [self.name])
        _, final = await self.req("INV %s %s 1" % (tid, invitee))
        if not final.startswith("OK"):
            self.note("cannot invite %s: %s" % (invitee, err_text(final)))
            await self.req("LVE %s" % tid)
            return None
        self.note("table %s: %s invited" % (tid, invitee))
        return await self.wait_start(tid, BOT_SECS if choice == "bot" else NAMED_SECS, invitee)

    async def join_table(self, tid, key, accepts):
        """GMO's #TABLE: a free seat there, seat 0 first, seat 1 for a rulebook that swaps sides. accepts: the
        rulesets the rulebook plays (Rulebook.accepts)."""
        data, _ = await self.req("LST table=%s" % tid)
        t = next((t for t in parse_tables(data) if t["id"] == tid), None)
        if t is None or t["key"] != key or t["rules"] not in accepts or t["status"] != "open":
            self.note("table %s is not open for this game" % tid)
            self.tell("Table %s is no longer waiting for a player." % tid)
            return None
        for seat in (0, 1) if self.rb.swaps else (0,):
            if t["seated"][seat] == "-":
                return await self.sit(tid, seat, "table %s" % tid)
        self.note("table %s has no seat this rulebook can take" % tid)
        self.tell("Table %s has no seat this game can take from GameMaster." % tid)
        return None

    async def resume_table(self, tid, key, accepts):
        """A table of the game being played, where the GameMaster player has a seat: -> the event number its
        view was taken at (the Mac is sent that position first), or None. Nothing at the table changes: no new
        table, no seat taken or left."""
        game = self.rb.rb_id.split(" [")[0]
        data, _ = await self.req("LST table=%s" % tid)
        t = next((t for t in parse_tables(data) if t["id"] == tid), None)
        low = [n.lower() for n in t["seated"]] if t else []
        why = None
        if t is None or t["key"] != key or t["rules"] not in accepts:
            why = "Table %s is not a game of %s here." % (tid, game)
        elif self.name.lower() not in low:
            why = "Table %s is not one of your games." % tid
        elif t["status"] != "playing":
            why = "The game at table %s is not being played now." % tid
        elif t["seats"] != 2:
            why = BIGGER % (tid, t["seats"])
        elif self.rb.key not in RESUMES:
            why = "%s cannot be taken up half way in GameMaster. Play table %s on the web or by telnet." % (game, tid)
        elif not self.positions:
            why = "This %s rulebook is older than 1.1.0 and cannot take up a game; install the latest ParlorNet " \
                  "Rulebooks." % game
        view, final = (await self.req("VUE %s" % tid)) if why is None else ([], "")
        if why is None and not (final.startswith("OK") and view and view[0].startswith("V ")):
            why = "The parlor cannot show table %s just now." % tid
        seat = low.index(self.name.lower()) if why is None else -1
        if why is None and self.rb.game is Checkers and Checkers.half_made(view, seat):
            why = "You are half way through a move at table %s: finish it on the web or by telnet." % tid
        if why:
            self.note("cannot resume table %s: %s" % (tid, why))
            self.tell(why)
            return None
        self.tid, self.seat, self.resumed, self.view = tid, seat, True, view
        self.note("resumed table %s as seat %d" % (tid, seat))
        return int(view[0].split()[2])

    async def existing(self, key, accepts, inviter):
        """Tables of the game the player could take: invitations to them (from inviter only, when one is
        named), then, for anyone, open public tables where someone waits. -> [(table, seat, why)], those
        that leave seat 0 free first; seat 1 ones only for a rulebook that swaps sides."""
        data, _ = await self.req("LST")
        tables = {t["id"]: t for t in parse_tables(data)}
        data, _ = await self.req("IVL")
        out = []
        for inv in parse_invites(data):
            t = tables.get(inv["id"])
            if (t is None or inv["key"] != key or inv["rules"] not in accepts or t["status"] != "open"
                    or (inviter and inv["from"].lower() != inviter.lower())):
                continue
            if t["seats"] != 2:
                self.tell(BIGGER % (inv["id"], t["seats"]))
                continue
            why = "%s's invitation to table %s" % (inv["from"], inv["id"])
            if inv["seat"] in (0, -1) and t["seated"][0] == "-":
                out.append((inv["id"], 0, why))
            elif inv["seat"] in (1, -1) and t["seated"][1] == "-":
                out.append((inv["id"], 1, why))
        if inviter is None:
            for t in tables.values():
                if (t["key"] != key or t["rules"] not in accepts or t["status"] != "open" or t["vis"] != "public"
                        or t["seats"] != 2 or not t["names"] or self.name.lower() in [n.lower() for n in t["names"]]):
                    continue
                why = "%s's table %s" % (t["names"][0], t["id"])
                for seat in (0, 1):
                    if t["seated"][seat] == "-":
                        out.append((t["id"], seat, why))
                        break
        return sorted((c for c in out if c[1] == 0 or self.rb.swaps), key=lambda c: c[1])

    async def sit(self, tid, seat, why):
        """Take seat 0, or seat 1 once the Mac's player agrees to swap sides; -> as find_table."""
        if seat == 1:
            if not await self.ask_swap():
                return None
            self.seat = 1
        _, final = await self.req("JON %s %d" % (tid, seat))
        if not final.startswith("OK"):
            self.note("cannot sit at %s: %s" % (why, err_text(final)))
            return None
        self.note("took %s%s" % (why, " as the second player" if seat else ""))
        self.tid = tid
        return await self.wait_start(tid, NAMED_SECS, None)

    async def ask_swap(self):
        """~Play it Sam, then ~Request to swap: True when the Mac answers ~Swap (its rulebook has swapped
        sides). A refusal, silence or a hang-up is False, and the session then ends."""
        await self.play_it_sam()
        await self.ctl(REQUEST_SWAP)
        end = time.monotonic() + SWAP_SECS
        while time.monotonic() < end:
            try:
                got = await asyncio.wait_for(read_message(self.r), max(0.1, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
            if got is None or got == (True, HANG_UP):
                self.closed = True
                return False
            if got == (True, SWAP):
                return True
            if got == (True, DONT_SWAP):
                self.note("the Mac's player would not swap sides")
                return False
            if got[0] and got[1] in ANSWERS:
                await self.ctl(ANSWERS[got[1]])
            elif not got[0]:
                self.note("a move came before the answer to the swap")
                return False
        self.note("no answer to the swap")
        return False

    async def new_table(self, key, rules, vis):
        _, final = await self.req("NEW %s %s turn=%s vis=%s" % (key, rules, self.svc.args.turn, vis))
        if not final.startswith("OK"):
            self.note("cannot open a table: %s" % err_text(final))
            return None
        tid = final.split()[1]
        _, final = await self.req("JON %s 0" % tid)
        if not final.startswith("OK"):
            self.note("cannot sit at table %s: %s" % (tid, err_text(final)))
            return None
        self.tid = tid
        return tid

    async def wait_start(self, tid, secs, invitee, give_up=True):
        """Until the game starts (-> the event number of START), the invitation ends, the Mac hangs up or
        secs pass (-> None; with give_up the GameMaster player stands up)."""
        end = time.monotonic() + secs
        seq = 0
        hangup = asyncio.ensure_future(read_message(self.r))
        try:
            while time.monotonic() < end:
                wat = asyncio.ensure_future(self.watch.req("WAT %s %d %d" % (
                    tid, seq, max(1, min(20, int(end - time.monotonic()) + 1)))))
                done, _ = await asyncio.wait({wat, hangup}, return_when=asyncio.FIRST_COMPLETED)
                if hangup in done:
                    got = hangup.result()
                    if got is None or (got[0] and got[1] == HANG_UP):
                        self.note("the Mac hung up while waiting")
                        await wat
                        await self.req("LVE %s" % tid)
                        self.closed = True
                        return None
                    if got[0] and got[1] in ANSWERS:
                        await self.ctl(ANSWERS[got[1]])
                    elif not got[0]:
                        self.early.append(got)      # a move made before the table started: played after
                    hangup = asyncio.ensure_future(read_message(self.r))
                    if not wat.done():
                        await wat
                data, final = wat.result()
                if not final.startswith("OK"):
                    raise CoreError(final)
                for n, kind, args in events(data):
                    if kind == "START":
                        return n
                    if kind == "UNINVITE" and invitee and len(args) > 1 and args[1].lower() == invitee.lower():
                        self.note("%s's invitation ended (%s)" % (invitee, " ".join(args[2:]) or "?"))
                        end = 0
                seq = int(final.split()[1]) if len(final.split()) > 1 else seq
        finally:
            if not hangup.done():
                hangup.cancel()
        if give_up:
            await self.req("LVE %s" % tid)
        return None

    # ---- playing ----------------------------------------------------------------------------------------
    async def move(self, token):
        """Play a move for the GameMaster player. Desync if the core refuses it (the session then ends)."""
        data, final = await self.req("MOV %s %s" % (self.tid, token))
        if not final.startswith("OK"):
            self.note("the core refused %s: %s" % (token, err_text(final)))
            raise Desync(token)
        if any(kind == "END" for _, kind, _ in events(data)):
            self.over = True                # the rest of a salvo is not played

    async def mac_reader(self):
        while True:
            got = await read_message(self.r)
            await self.queue.put(("mac", got))
            if got is None:
                return

    async def watcher(self, seq):
        try:
            while True:
                data, final = await self.watch.req("WAT %s %d 20" % (self.tid, seq))
                if not final.startswith("OK"):
                    raise CoreError(final)
                for n, kind, args in events(data):
                    if n > seq:
                        await self.queue.put(("evt", (kind, args, n)))
                seq = int(final.split()[1])
                if any(kind == "END" for _, kind, _ in events(data)):
                    return
        except (CoreError, OSError, asyncio.TimeoutError, ConnectionError) as e:
            await self.queue.put(("lost", str(e)))

    async def play(self, seq):
        if self.resumed:
            await self.game.send_position(self.view)
        else:
            await self.game.on_start()
        for m in self.early:
            self.queue.put_nowait(("mac", m))
        self.early = []
        tasks = [asyncio.ensure_future(self.mac_reader()), asyncio.ensure_future(self.watcher(seq))]
        linger = None
        try:
            while True:
                timeout = None if linger is None else max(0.0, linger - time.monotonic())
                try:
                    what, data = await asyncio.wait_for(self.queue.get(), timeout)
                except asyncio.TimeoutError:
                    break                       # the time after the end is up
                if what == "lost":
                    self.note("lost the core (%s)" % data)
                    break
                if what == "evt":
                    kind, args, self.evt_n = data
                    if kind == "CLIENT":    # the opponent plays on from another program: the lobby hears
                        c = parse_client(" ".join(["CLIENT"] + args))
                        known = self.clients.get(c[0]) if c else None
                        if c:
                            self.clients[c[0]] = c[1]
                        if c and c[0] != self.seat:
                            data, _ = await self.req("LST table=%s" % self.tid)
                            t = next((t for t in parse_tables(data) if t["id"] == self.tid), None)
                            if t and c[0] < len(t["seated"]) and t["seated"][c[0]] != "-":
                                self.tell(self.client_text(t["seated"][c[0]], c[1], None if known else "plays"))
                        continue
                    await self.game.on_event(kind, args)
                    if kind == "END":
                        self.over = True
                        self.note("table %s ended: %s" % (self.tid, " ".join(args)))
                        if args and args[0] not in ("win", "draw"):
                            break               # resigned, out of time, closed: GameMaster would wait on
                        linger = time.monotonic() + self.svc.args.linger
                    continue
                if data is None or (data[0] and data[1] == HANG_UP):
                    self.closed = True
                    if self.resumed and not self.over:
                        self.note("the Mac hung up during the game: the table %s plays on" % self.tid)
                    elif not self.over:
                        self.note("the Mac hung up during the game: resigned")
                        await self.req("RES %s" % self.tid)
                    break
                is_ctl, text = data
                if is_ctl:
                    if text in ANSWERS:
                        await self.ctl(ANSWERS[text])
                elif self.over:
                    await self.game.after_end(text)
                else:
                    try:
                        await self.game.from_mac(text)
                    except Desync:
                        if self.resumed and self.positions:
                            await self.game.send_position()
                            continue
                        await self.req("RES %s" % self.tid)
                        break
        finally:
            for t in tasks:
                t.cancel()

    async def finish(self):
        if self.started and not self.closed:
            try:
                await self.ctl(HANG_UP)
            except (ConnectionError, OSError):
                pass
        for c in (self.core, self.watch):
            if c is not None:
                c.close()               # the session stays: it is the tunnel's sign-in
        try:
            self.w.close()
        except Exception:
            pass


def events(lines):
    """EVT lines -> [(number, kind, [args])]."""
    out = []
    for l in lines:
        f = l.split()
        if len(f) >= 4 and f[0] == "EVT" and f[2].isdigit():
            out.append((int(f[2]), f[3], f[4:]))
    return out


# ---- the games, as the lobby and the calls know them ----------------------------------------------

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
GAME_OF = {(rb.key, r): rb.rb_id.split(" [")[0] for rb in RULEBOOKS.values() for r in rb.accepts}
# a table's ruleset as the ParlorNet rulebook's lobby knows the game: the rulebook's own (TBL, IVL)
LOBBY_RULES = {(rb.key, r): rb.rules for rb in RULEBOOKS.values() for r in rb.accepts}
RESUMES = {rb.key for rb in RULEBOOKS.values() if issubclass(rb.game, ParlorGame)}     # take a position


def opponent(word):
    """An opponent as GMO names one -> "anyone", "bot", a name, or None."""
    low = (word or "").lower()
    if low in ("anyone", "any", "open"):
        return "anyone"
    if low == "bot" or botflag.is_bot_name(word):
        return "bot"
    return word if NAME_RE.match(word or "") else None


# ---- the tunnel ------------------------------------------------------------------------------------

class TunnelConn:
    """One GameMaster connection inside a tunnel: the reader and writer that Service.call and Session use."""

    def __init__(self, tunnel, n):
        self.t, self.n = tunnel, n
        self.reader = asyncio.StreamReader(limit=MAX_STATE + CALL_SIZE)
        self.closed = False
        self.task = None
        self.choice = None          # a call's opponent or table (GMO's who)

    def write(self, data):
        if not self.closed:
            self.t.send_data(self.n, data)

    async def drain(self):
        await self.t.drain()

    def close(self):
        """Closed on this side: GMX to the Mac."""
        if not self.closed:
            self.closed = True
            self.t.send_line("GMX %d" % self.n)
            self.t.forget(self)

    def ended(self):
        """Closed by the Mac, or the tunnel ended."""
        self.closed = True
        self.reader.feed_eof()

    def get_extra_info(self, key, default=None):
        return self.t.w.get_extra_info(key, default)


class Tunnel:
    """The ParlorNet rulebook's connection: a sign-in, a little lobby, and the Mac's GameMaster connections
    (module docstring, The tunnel)."""

    def __init__(self, svc, sid, addr, reader, writer, ticket, rest):
        self.svc, self.sid, self.addr = svc, sid, addr
        self.r, self.w, self.ticket = reader, writer, ticket
        self.rest = bytearray(rest)     # what came after the opening's line ending
        self.core = None
        self.player = None
        self.name = None
        self.conns = {}             # number -> TunnelConn
        self.pending = {}           # call number -> Pending, until its game connection comes
        self.prog = self.mach = ""  # what the rulebook said it is and the Mac it runs on (CLI), cleaned

    def note(self, text):
        log(self.sid, text)

    def claim(self, call):
        """What the turns of a game on this tunnel say they were played with (CLI on the game's connection to
        the core): the program and the Mac the rulebook named, else GameMaster with the version its call record
        carries, on a Macintosh."""
        return self.prog or "%s %s" % (CLIENT_NAME, bcd(call.version)), self.mach or MACHINE

    def client(self, f, line):
        """CLI as the core takes it (docs/protocol.md, Clients and machines), kept for the games the tunnel
        starts from then on: CLI PROG text, CLI MACH text (nothing after the word forgets it), and CLI alone,
        which lists what was said."""
        if len(f) == 1:
            for field, text in (("PROG", self.prog), ("MACH", self.mach)):
                if text:
                    self.send_line("CLI %s %s" % (field, text))
            self.send_line("OK")
            return
        field = f[1].upper()
        if field not in ("PROG", "MACH"):
            self.send_line("ERR SYNTAX CLI, CLI PROG text or CLI MACH text")
            return
        parts = line.strip().split(None, 2)
        text = claim_text(parts[2] if len(parts) > 2 else "")
        if field == "PROG":
            self.prog = text
        else:
            self.mach = text
        self.send_line("OK")

    def send_line(self, text):
        if not self.w.is_closing():
            self.w.write(text.encode("ascii", "replace") + b"\r\n")

    async def drain(self):
        await self.w.drain()

    def send_data(self, n, data):
        for i in range(0, len(data), 36):
            self.send_line("GMD %d %s" % (n, data[i:i + 36].hex()))

    def forget(self, conn):
        if self.conns.get(conn.n) is conn:
            del self.conns[conn.n]

    async def read_line(self):
        """A line from the Mac (CR, LF or both end it), at most 82 bytes; None at the end."""
        buf = b""
        while True:
            if self.rest:
                ch = bytes(self.rest[:1])
                del self.rest[:1]
            else:
                ch = await asyncio.wait_for(self.r.read(1), TUNNEL_IDLE)
            if not ch:
                return None
            if ch in b"\r\n":
                if buf:
                    return buf.decode("ascii", "replace")
                continue
            buf += ch
            if len(buf) > 82:
                self.send_line("ERR TOOLONG line too long")
                return None

    async def run(self):
        try:
            self.core = await self.svc.core(addr=self.addr)
        except (CoreError, OSError, asyncio.TimeoutError) as e:
            self.note("cannot reach the core: %s" % e)
            self.send_line("ERR BUSY the parlor cannot be reached just now")
            await self.drain()
            return
        self.note("tunnel from %s" % self.addr)
        try:
            self.send_line(self.core.greeting)
            await self.drain()
            while True:
                line = await self.read_line()
                if line is None:
                    return
                f = line.split()
                if not f:
                    continue
                verb = f[0].upper()
                if verb == "BYE":
                    self.send_line("OK")
                    return
                if verb.startswith("GM") and len(verb) == 3:
                    await self.gm(verb, f)
                elif verb == "CLI":
                    self.client(f, line)
                elif self.player is None:
                    await self.sign_in(verb, f, line)
                elif verb in TUNNEL_RELAY:
                    await self.relay(line)
                elif verb == "TBL":
                    await self.tables()
                elif verb == "OUT":
                    await self.relay(line)
                    self.player = None
                    return
                else:
                    self.send_line("ERR UNKNOWN %s" % verb[:12])
                await self.drain()
        finally:
            await self.finish()

    async def relay(self, line):
        data, final = await self.core.req(line)
        seats = {}
        if line.split()[:1] == ["IVL"] and any(l.startswith("IVT ") for l in data):
            tabs, _ = await self.core.req("LST")
            seats = {t["id"]: t["seats"] for t in parse_tables(tabs)}
        for l in data:
            f = l.split()
            if f[:1] == ["IVT"] and len(f) > 1 and seats.get(f[1], 2) != 2:
                continue        # GameMaster plays two: an invitation to a bigger table is for the web, telnet or Play
            if f[:1] == ["IVT"] and len(f) > 3 and (f[2], f[3]) in LOBBY_RULES:
                # an invitation to a game a rulebook plays, by the ruleset the lobby knows it by (LOBBY_RULES)
                l = " ".join(f[:3] + [LOBBY_RULES[(f[2], f[3])]] + f[4:])
            self.send_line(l)
        self.send_line(final)

    async def tables(self):
        """TBL: the tables the lobby shows, of the games played here, at most TBL_MAX of each kind:
            YG table game rules seat turn opponent    the player's own games being played (turn: the seat to
                                                      move, -1 when the core does not say)
            WG table game rules name                  an open public table where name waits for a player
            PG table game rules name name             a game other players play, the newest first
        then OK. rules is the rulebook's own ruleset, which the lobby knows the game by, also for a table of
        another ruleset the rulebook plays (Rulebook.accepts: an english Grid Words table is listed as classic)."""
        data, final = await self.core.req("LST")
        if not final.startswith("OK"):
            self.send_line(final)
            return
        me = self.player.name.lower()
        mine, waiting, playing = [], [], []
        for t in sorted(parse_tables(data), key=lambda t: int(t["id"]) if t["id"].isdigit() else 0):
            if (t["key"], t["rules"]) not in GAME_OF or t["seats"] != 2:
                continue
            low = [n.lower() for n in t["seated"]]
            game = "%s %s %s" % (t["id"], t["key"], LOBBY_RULES[(t["key"], t["rules"])])
            if me in low and t["status"] == "playing" and len(mine) < TBL_MAX:
                seat = low.index(me)
                view, ok = await self.core.req("VUE %s" % t["id"])
                turn = next((int(l.split()[1]) for l in view if l.startswith("CLK ") and l.split()[1].isdigit()), -1) \
                    if ok.startswith("OK") else -1
                mine.append("YG %s %d %d %s" % (game, seat, turn, t["seated"][1 - seat]))
            elif (me not in low and t["status"] == "open" and t["vis"] == "public" and t["names"]
                    and t["filled"] < t["seats"] and len(waiting) < TBL_MAX):
                waiting.append("WG %s %s" % (game, t["names"][0]))
            elif me not in low and t["status"] == "playing":
                playing.append("PG %s %s %s" % (game, t["seated"][0], t["seated"][1]))
        for l in mine + waiting + playing[::-1][:TBL_MAX]:      # the newest games of others
            self.send_line(l)
        self.send_line("OK")

    async def sign_in(self, verb, f, line):
        """USER, HELO, AUT, CAN and MOTD relayed to the core until it signs the player in."""
        if verb not in ("USER", "HELO", "AUT", "CAN", "MOTD"):
            self.send_line("ERR NOHELO sign in first")
            return
        if verb in ("USER", "HELO") and len(f) > 1:
            self.name = f[1]
        try:
            data, final = await self.core.req(line)
        except CoreError as e:
            self.send_line("ERR SYNTAX %s" % e)
            return
        for l in data:
            self.send_line(l)
        if final.startswith("OK") and verb in ("USER", "HELO", "AUT") and len(final.split()) > 1 and self.name:
            if not self.ticket.sign_in():
                self.send_line("ERR BUSY the parlor is full")
                return
            self.player = Player(self.name, final.split()[1])
            self.note("%s signed in by the tunnel" % self.name)
            self.send_line("OK -")
        else:
            self.send_line(final)

    async def gm(self, verb, f):
        n = int(f[1]) if len(f) > 1 and f[1].isdigit() else 0
        if not 1 <= n <= TUNNEL_SLOTS:
            self.send_line("ERR SYNTAX connection 1 to %d" % TUNNEL_SLOTS)
            return
        if verb == "GMD":
            conn = self.conns.get(n)
            try:
                data = bytes.fromhex(f[2]) if len(f) == 3 else b""
            except ValueError:
                data = b""
            if conn is None or conn.closed or not data:
                return
            if len(conn.reader._buffer) + len(data) > MAX_STATE:
                conn.close()
                conn.ended()
                return
            conn.reader.feed_data(data)
        elif verb == "GMX":
            conn = self.conns.pop(n, None)
            if conn is not None:
                conn.ended()
        elif verb in ("GMO", "GMS"):
            if n in self.conns or self.player is None:
                self.send_line("GMX %d" % n)
                return
            conn = TunnelConn(self, n)
            if verb == "GMO":
                who = f[2] if len(f) > 2 else "anyone"
                if re.match(r"^[#@]\d{1,9}\Z", who):
                    conn.choice = who           # #TABLE joins that open table, @TABLE resumes that game
                else:
                    conn.choice = opponent(who) or "anyone"
                self.conns[n] = conn
                self.send_line("GMK %d" % n)
                conn.task = asyncio.ensure_future(self.call(conn))
                return
            k = int(f[2]) if len(f) > 2 and f[2].isdigit() else 0
            p = self.pending.pop(k, None)
            if p is None:
                self.send_line("GMX %d" % n)
                return
            p.arrived.set()
            self.conns[n] = conn
            self.send_line("GMK %d" % n)
            conn.task = asyncio.ensure_future(self.game(p, conn))
        else:
            self.send_line("ERR UNKNOWN %s" % verb)

    async def call(self, conn):
        try:
            await self.svc.call(self.sid, self.addr, conn.reader, conn, self, conn.n)
        except (ConnectionError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            print("gamemaster tunnel call error: %r" % e, file=sys.stderr, flush=True)
        finally:
            conn.close()

    async def game(self, p, conn):
        try:
            await self.svc.game(p, conn.reader, conn)
        finally:
            conn.close()

    async def finish(self):
        """The tunnel ended: its connections end (a game being played is resigned), then its sign-in."""
        tasks = [c.task for c in self.conns.values() if c.task]
        for c in list(self.conns.values()):
            c.ended()
        if tasks:
            await asyncio.wait(tasks, timeout=10)
        if self.core is not None:
            if self.player is not None:
                self.note("%s's tunnel ended" % self.player.name)
            await self.core.finish()


# ---- the program -----------------------------------------------------------------------------------

# The protocol this extension serves, declared to the core (adapters/declare.py): key, name, description.
PROTOCOL = ("GAMEMASTER", "GameMaster", "Nine games for GameMaster on 68k Macs")


async def serve(args):
    svc = Service(args)
    server = await asyncio.start_server(svc.on_call, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("gamemaster adapter on %s:%d (the ParlorNet rulebook's tunnel) -> core %s:%d" % (
        args.host, args.port, args.core_host, args.core_port), flush=True)
    async with server:
        await server.serve_forever()


def turn_value(text):
    if not re.match(r"^\d{1,4}[smhd]?\Z", text):
        raise argparse.ArgumentTypeError("a time per turn such as 10m, 1h or 1d")
    return text


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.gamemaster")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6508, help="where the ParlorNet rulebook connects")
    ap.add_argument("--turn", type=turn_value, default="10m", help="the time per turn of the tables it opens")
    ap.add_argument("--wait", type=float, default=60.0,
                    help="seconds a new open table waits for anyone before the bot is invited")
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    declare.add_args(ap)
    ap.add_argument("--linger", type=float, default=END_LINGER, help=argparse.SUPPRESS)
    ap.add_argument("--opening-secs", type=float, default=native.OPENING_SECS, help=argparse.SUPPRESS)
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
