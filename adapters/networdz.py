"""Networdz extension: ParlorNet's Grid Words for Networdz 2.0 on Windows.

ParlorNet is not affiliated with Networdz's author. Networdz (Lance Frohman, San Francisco, 1996; Windows 3.1 or
later, Winsock) plays a crossword-tile game between two to four PCs over TCP, peer to peer: one copy is the host
("Local / Dial mode") and connects out to each remote player's copy, which waits in "Answer mode". The host's copy
chooses the rules, deals, checks every word against its own dictionary, starts and ends the game. This extension
answers as the remote copies: the player's Networdz hosts and connects to the parlor once for each Remote player
it lists, and each connection is one seat of a gridwords/1 table at the parlor, where a player on the web, by
telnet, in Play or anywhere else (the bot too) plays that seat. The parlor never hosts and never connects out.
The protocol was read from games captured between copies of the program.

The wire. Binary messages, each a type letter and a fixed length; the second byte is a seat (Networdz's player
number from 0) or 4 for every player:

    X seat 'C' seat        host: you are this seat (on connecting)          4
    D seat name[21]        answerer: my player's name; sent again, it renames the seat   23
    E 4 7 x u16            host: the rules (show racks, hints, tournament, exchange 4 of a kind,
                           trade blanks, timed, minutes)                    16
    F 4 4 x 84             host: the players (type: 0 none, 1 computer, 2 person, 3 remote; name; address)  338
    A 4 bag[250]           host: the bag, a tile number at each place, 0xFA where it is drawn  252
    G seat / H seat ...    host: pick a tile to see who starts / the place picked   2 / 12
    R 4 4 x u16            host: the order of play, the first player first  10
    P seat                 host: draw tiles now                             2
    Q seat rack[8] .. draws[8] ..   the rack before the draw (0xFA empty) and the place drawn for each slot  24
    I seat new 0           host: this seat's turn (new 0: the same seat again after a refused word)  4
    J seat 8 x (row, col, tile, blank's letter) ..   a play: each rack tile, at row and col 0x15 if not played  44
    M seat code ..word     host: its verdict on that play: 0 taken, 7 not in its dictionary  18
    K seat / N seat        a pass / host: discard now (while the bag has tiles)   2 / 2
    O seat rack[8] .. 8 x u16 ..    the discards: 1 for each tile put back     36
    B seat text[81]        a chat line; Networdz shows it as "name: text"   83
    S 4 / Y 4 / Z 4        host: the game is over / stopped / the host has quit   2

The host sends every message to every remote copy, its own and the others', so the first connection of a host
(the lowest seat) is read for the game and the others only carry their seat's answers. A tile's number is its
place in the configuration's tile set: the blanks first, then each letter as many times as the set has it. The
parlor plays the standard set, so the Networdz player loads the ParlorNet configuration (PARLOR.CFG, given on the
client's page), which has the standard board, the 100 tiles and their points and racks of 7; a game whose bag
does not hold those 100 tiles is refused in the chat.

Sign-in. Networdz sends names but nothing secret, so the Networdz player types /signin NAME PASSWORD in its Chat
window (/signin PASSWORD once a game has named the player); the name goes to the core and the password answers
its credential question (common.sign_in_lines). The extension's lines come in that window as a seat's chat.

The table. After sign-in, /open or /invite NAME (/invite bot) makes a gridwords/1 classic table with one seat for
the Networdz player (seat 0) and one for each remote seat it connected, racks=open, marked integrity=false with
the reason "the host's Networdz deals the tiles" (the core's INT): the host's copy deals and decides, so the
parlor cannot keep its secrets or be the authority. Every player's program sees every rack anyway. At such a
table the creator's program has the say (docs/types/gridwords-1.md): every move hands the turn to seat 0, and the
extension gives it on (TURN) as Networdz's I says. Seat 1 and up are the remote seats in Networdz's order; each
connection is renamed after the player who sits there (D), between games only: during one Networdz takes a D
for a move out of turn.

The game. Networdz's New Game waits (its "picking a tile" step) until every seat is taken. Then:

    host's play     J and M from the host -> PLAY x y H|V letters OWN (its words are its dictionary's)
    host's draw     Q from the host -> RACK letters (the tiles its copy drew)
    host's pass     K, then O with no tile -> PASS; O with tiles -> EXCH letters, then RACK
    a turn          I -> TURN seat, when the core's turn is another
    the end         S -> OVER, unless the core's game ended first (a player went out)
    seat's draw     P -> RACK letters seat (the places it picks in the host's bag), then Q
    seat's play     the core's PLAY -> J; M 0 -> the draw; M 7 -> VOID word (the play is taken back, the
                    player is told why, and the table refuses that word from then on)
    seat's exchange the core's EXCH -> K, N -> O with the tiles that left the rack, P -> the draw
    seat's pass     the core's PASS -> K, N -> O with no tile
    chat            B from the host -> table chat, or a command; a seat's chat -> B from its connection

Every seat's tiles come from the host's bag, which Networdz sends (A) and the table follows: the table deals and
draws nothing itself. When Networdz asks a parlor seat to draw, the extension picks places in the host's bag for
the empty slots, as a remote copy does, and tells the table the rack before it answers Networdz. A word the
host's dictionary refuses is taken back (VOID with the word), and the table refuses it from then on, so the bot
finds another.

Left out: Trade Blanks, Exchange 4 of a kind, Timed and Tournament Rules (the chat asks the player to turn them
off), computer players and a second person at the host, a remote seat that is not the parlor, loading saved
games, other configurations and languages, watching from Networdz, resuming a game on a later connection.

    python3 -m adapters.networdz --core 127.0.0.1:6590 --key networdz.key --port 6516
"""
import argparse
import asyncio
import itertools
import random
import re
import sys
import time

from . import botflag, chatcmd, declare
from .clients import BY_ID
from .common import (LOGIN_TOTAL_SECS, NEWPASS_TEXT, CoreClient, CoreError, Gate, err_text, keepalive, no_wait,
                     parse_invites, parse_tables, peer_addr, sign_in_lines)
from .games import gridwords as G

CLIENT_NAME = BY_ID["networdz"].name        # "Networdz 2.0"
MACHINE = "Windows"                         # Networdz is a Windows program (3.1 or later)
PROTOCOL = ("NETWORDZ", "Networdz, for Networdz 2.0 on Windows", "Grid Words for Networdz 2.0 on Windows")
GRID = "gridwords/1"
RULES = "classic"
REASON = "the host's Networdz deals the tiles"     # INT: why the table keeps no integrity
TABLE_MARK = ("client", "networdz")                 # kept with each table made here (TDS)
HOST_NAME = "ParlorNet"                             # a remote seat's name until a player sits there
MAX_SESSIONS = 40
HELLO_SECS = 30.0           # X after connecting
POLL_SECS = 0.5             # the table's events
INVITE_SECS = 4.0           # IVL, while signed in and not playing
LOBBY_SECS = 1800.0         # connected, no game and nothing typed: the connections close
KEEP_SECS = 45.0            # between games, nothing sent on a connection this long: its name again (D)
CHAT_GAP = 0.3              # between lines in the chat (the instructions box shows the last)
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
COMMANDS = ["list", "resign", "invite", "open", "close", "accept", "decline", "who", "tables", "top", "score",
            "note", "motd", "time", "signin", "quit", "help"]
BEFORE_SIGNIN = ("signin", "help", "motd", "quit")
NOTES = ["Your table has a seat for you and one for each Remote player your Networdz lists here.",
         "/invite bot plays the bot. Choose New Game once everyone is seated."]
ids = itertools.count(1)

# ---- the wire -----------------------------------------------------------------------------------------------

SIZES = {b"X": 4, b"D": 23, b"E": 16, b"F": 338, b"A": 252, b"G": 2, b"H": 12, b"R": 10, b"P": 2, b"Q": 24,
         b"I": 4, b"J": 44, b"M": 18, b"K": 2, b"N": 2, b"O": 36, b"B": 83, b"S": 2, b"Y": 2, b"Z": 2}
ALL = 4                     # the seat byte of a message for every player
EMPTY = 0xFA                # an empty rack slot, or a drawn place in the bag
OFF = 0x15                  # a rack tile's row and column in a play: not on the board
SLOTS = 8                   # rack slots in every message (Networdz's largest rack)
RACK = 7                    # the ParlorNet configuration's rack
BAG = 250                   # places in the bag message
NAME_LEN, TEXT_LEN = 21, 81
SAY_LEN = TEXT_LEN - 1 - len("ParlorNet: ")   # a line of the parlor's own, with the name before it
NONE, COMPUTER, PERSON, REMOTE = 0, 1, 2, 3
REFUSED = {7: "not in the host's dictionary"}

# The ParlorNet configuration's tile set in Networdz's order: the blanks, then each letter (G.STD_TIL).
TILESET = "_" * G.STD_TIL["_"][1] + "".join(c * G.STD_TIL[c][1] for c in sorted(G.STD_TIL) if c != "_")


def text_field(text, n):
    return text.encode("latin-1", "replace")[:n - 1].ljust(n, b"\0")


def cstr(b):
    return b.split(b"\0", 1)[0].decode("latin-1", "replace")


def msg_d(seat, name):
    return b"D" + bytes([seat]) + text_field(name, NAME_LEN)


def msg_b(seat, text):
    return b"B" + bytes([seat]) + text_field(text, TEXT_LEN)


def msg_h(seat, slot, place):
    rack = [EMPTY] * RACK + [0] * (SLOTS - RACK)
    rack[slot] = place
    return b"H" + bytes([seat] + rack) + b"\0\0"


def msg_q(seat, rack, draws):
    return b"Q" + bytes([seat]) + slots(rack) + b"\0\0" + slots(draws, EMPTY) + b"\0" * 4


def msg_j(seat, recs):
    """recs: one (row, col, tile, letter) for each rack slot; letter is a blank's ("T") or ""."""
    body = b""
    for row, col, tile, letter in recs[:SLOTS]:
        body += bytes([row, col, tile, ord(letter) if letter else 0])
    return b"J" + bytes([seat]) + body.ljust(4 * SLOTS, b"\0") + b"\0" * 10


def msg_k(seat):
    return b"K" + bytes([seat])


def msg_o(seat, rack, put_back):
    flags = b"".join(b"\1\0" if i in put_back else b"\0\0" for i in range(SLOTS))
    return b"O" + bytes([seat]) + slots(rack) + b"\0" * 4 + flags + b"\0" * 6


def slots(values, fill=EMPTY):
    """A rack's 8 bytes: the rack's 7 slots (fill for a missing one), then 0."""
    vals = list(values[:RACK]) + [fill] * (RACK - len(values))
    return bytes(vals + [0] * (SLOTS - RACK))


class Reader:
    """Networdz's byte stream -> messages (bytes, each a whole message of its type's size); ("junk", bytes) for
    a type it does not know, after which the rest of what has come is dropped."""

    def __init__(self):
        self.buf = b""

    def feed(self, data):
        self.buf += data
        out = []
        while self.buf:
            n = SIZES.get(self.buf[:1])
            if n is None:
                out.append(("junk", self.buf))
                self.buf = b""
                break
            if len(self.buf) < n:
                break
            out.append((self.buf[:1].decode(), self.buf[:n]))
            self.buf = self.buf[n:]
        return out


def u16s(b, n):
    return [b[i] | b[i + 1] << 8 for i in range(0, 2 * n, 2)]


def parse_rules(m):
    v = u16s(m[2:], 7)
    return {"racks": v[0], "hints": v[1], "tournament": v[2], "four": v[3], "trade": v[4], "timed": v[5],
            "minutes": v[6]}


def parse_players(m):
    """F -> [(type, name, address)] for Networdz's four players."""
    out = []
    for i in range(4):
        e = m[2 + 84 * i:2 + 84 * (i + 1)]
        out.append((e[0] | e[1] << 8, cstr(e[4:25]), cstr(e[25:74])))
    return out


def parse_play(m):
    """J -> [(row, col, tile, letter)] for each rack slot."""
    out = []
    for i in range(SLOTS):
        row, col, tile, letter = m[2 + 4 * i:6 + 4 * i]
        out.append((row, col, tile, chr(letter) if letter else ""))
    return out


def letter(tile):
    """A tile number -> its letter ("_" a blank), or "" for an empty slot or a number past the set."""
    return TILESET[tile] if 0 <= tile < len(TILESET) else ""


def letters_of(rack):
    return "".join(letter(t) for t in rack if t != EMPTY and letter(t))


def minus(a, b):
    """The letters of a that are not in b, counted (multiset difference)."""
    left = list(b)
    out = []
    for c in a:
        if c in left:
            left.remove(c)
        else:
            out.append(c)
    return "".join(out)


def bag_ok(bag):
    """A new game's bag holds the ParlorNet configuration's tiles, each once."""
    return sorted(bag[:len(TILESET)]) == list(range(len(TILESET))) and not any(bag[len(TILESET):])


def play_move(placed, tiles):
    """A host's play: placed [(x, y, letter, blank)] (1-based squares) and the board's tiles {(x, y): c} -> the
    PLAY tokens with the whole main word, or None when the tiles are not in one line."""
    xs, ys = {p[0] for p in placed}, {p[1] for p in placed}
    new = {(x, y): (c, b) for x, y, c, b in placed}
    if len(ys) == 1 and len(xs) >= 1:
        horiz = True
        if len(placed) == 1:        # one tile: the way it makes a word
            x, y = placed[0][:2]
            horiz = (x - 1, y) in tiles or (x + 1, y) in tiles or not ((x, y - 1) in tiles or (x, y + 1) in tiles)
    elif len(xs) == 1:
        horiz = False
    else:
        return None
    dx, dy = (1, 0) if horiz else (0, 1)
    x, y = min(placed, key=lambda p: (p[0], p[1]))[:2]
    while (x - dx, y - dy) in tiles:
        x, y = x - dx, y - dy
    word, cx, cy = "", x, y
    while (cx, cy) in tiles or (cx, cy) in new:
        if (cx, cy) in new:
            c, blank = new[(cx, cy)]
            word += ("_" + c.upper()) if blank else c.upper()
        else:
            word += tiles[(cx, cy)].upper()
        cx, cy = cx + dx, cy + dy
    if any(p not in [(x + dx * i, y + dy * i) for i in range(len(word.replace("_", "")))] for p in new):
        return None
    return ["PLAY", str(x), str(y), "H" if horiz else "V", word]


def parse_racks(lines):
    """RK lines of an open-rack view -> {seat: letters}."""
    out = {}
    for l in lines:
        f = l.split()
        if len(f) >= 2 and f[0] == "RK" and f[1].isdigit():
            out[int(f[1])] = "" if len(f) < 3 or f[2] == "-" else f[2]
    return out


def log(sid, text):
    print("networdz %s: %s" % (sid, text), file=sys.stderr, flush=True)


# ---- a connection: one remote seat of a Networdz host --------------------------------------------------------

class Link:
    def __init__(self, svc, reader, writer, ticket):
        self.svc, self.r, self.w, self.ticket = svc, reader, writer, ticket
        self.addr = peer_addr(writer)
        self.wire = Reader()
        self.seat = None            # Networdz's player number for this connection (X)
        self.name = HOST_NAME
        self.shown = True           # the host's screen shows self.name
        self.host = None
        self.sent = time.monotonic()
        self.closed = False

    async def send(self, *msgs):
        data = b"".join(msgs)
        if not data or self.closed:
            return
        if self.svc.trace:
            log("trace", "to seat %s: %s" % (self.seat, data.hex()))
        self.w.write(data)
        self.sent = time.monotonic()
        try:
            await self.w.drain()
        except (ConnectionError, OSError):
            self.closed = True

    async def rename(self, name):
        """The seat's name on the host's screen (D). Only between games: during one (a New Game waiting for
        players too) Networdz takes a D for a move out of turn, so the name goes when the game is over."""
        self.name = name
        self.shown = False
        if self.host is None or self.host.nw is None:
            await self.show_name()

    async def show_name(self):
        self.shown = True
        await self.send(msg_d(self.seat, self.name))

    def close(self):
        self.closed = True
        try:
            self.w.close()
        except Exception:
            pass


# ---- a Networdz host: its connections, its player's core session and the game --------------------------------

class Host:
    def __init__(self, svc, addr):
        self.svc, self.addr = svc, addr
        self.sid = next(ids)
        self.links = {}             # Networdz seat -> Link (ours)
        self.queue = asyncio.Queue()
        self.core = None
        self.core_lock = asyncio.Lock()
        self.name = None            # signed in as
        self.console = None
        self.ticket = None
        self.t0 = self.last_typed = time.monotonic()
        self.closing = False
        self.polling = False        # a look at the table's events is queued
        self.tid = None             # the table, once made
        self.cursor = 0
        self.started = False        # the table's game has begun at the core
        self.over = False           # ... and ended there
        self.announced = {}
        self.reset_game()

    def reset_game(self):
        """Networdz's game, as the extension keeps it."""
        self.nw = None              # None between Networdz games, else a dict (new_game)
        self.pending_bag = None     # an A that came before the E of a New Game
        self.held = []              # (kind, seat) G and P to answer once the core's game runs
        self.host_play = None       # the host's J, until its M
        self.host_pass = False      # the host's K, until its O or the next I
        self.outgoing = {}          # Networdz seat -> ("pass", put back, drawn) for a parlor seat's K
        self.sent_play = {}         # Networdz seat -> its rack before the J sent for it, until the host's M
        self.detached = False       # Networdz and the table are out of step: no more relaying

    # ---- seats ---------------------------------------------------------------------------------------------
    @property
    def ours(self):
        return sorted(self.links)

    def primary(self):
        return self.links[self.ours[0]] if self.links else None

    def host_seat(self):
        if self.nw and self.nw.get("host") is not None:
            return self.nw["host"]
        return next(i for i in range(4) if i not in self.links)

    def core_seat(self, nseat):
        """Networdz seat -> the table's seat: 0 the host's player, then the remote seats in order."""
        if nseat == self.host_seat():
            return 0
        return self.ours.index(nseat) + 1 if nseat in self.links else None

    def nw_seat(self, cseat):
        if cseat == 0:
            return self.host_seat()
        return self.ours[cseat - 1] if 0 < cseat <= len(self.ours) else None

    # ---- output --------------------------------------------------------------------------------------------
    def voice(self):
        """Where the parlor's own lines come from: a seat of ours still named ParlorNet, else a seat with no
        player (Networdz shows ": text"), else the first connection."""
        for s in self.ours:
            if self.links[s].name == HOST_NAME:
                return self.links[s], s, ""
        used = set(self.ours) | {self.host_seat()}
        if self.nw and self.nw.get("players"):
            used |= {i for i, p in enumerate(self.nw["players"]) if p[0] != NONE}
        free = [i for i in range(4) if i not in used]
        if free and self.links:
            return self.primary(), free[0], ""
        return self.primary(), self.ours[0] if self.links else 0, HOST_NAME + ": "

    async def say(self, lines):
        if isinstance(lines, str):
            lines = [lines]
        link, seat, prefix = self.voice()
        if link is None:
            return
        for i, line in enumerate(l for l in lines if l):
            if i:
                await asyncio.sleep(self.svc.chat_gap)
            await link.send(msg_b(seat, (prefix + line)[:TEXT_LEN - 1]))

    async def req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    # ---- the connections ------------------------------------------------------------------------------------
    async def add(self, link):
        self.links[link.seat] = link
        link.host = self
        name = await self.seat_name(link.seat)
        link.name = name or HOST_NAME
        await link.send(msg_d(link.seat, link.name))
        log(self.sid, "seat %d connected" % link.seat)

    async def seat_name(self, nseat):
        """The name of the player at the table seat for a Networdz seat, or None."""
        if not self.tid or self.core is None:
            return None
        cs = self.core_seat(nseat)
        t = await self.table()
        if t and cs is not None and cs < len(t["seated"]) and t["seated"][cs] != "-":
            return t["seated"][cs]
        return None

    async def greet(self):
        await asyncio.sleep(0.5)            # the other connections of the same New Game first
        lines = ["Welcome to ParlorNet: Grid Words for Networdz."]
        try:
            motd = await self.core.motd()
        except (CoreError, OSError, asyncio.TimeoutError):
            motd = []
        lines += motd[:4]
        if len(motd) > 4:
            lines.append("(/motd shows the whole message.)")
        lines += ["/help lists the commands.", "To sign in, type /signin NAME PASSWORD"]
        await self.say(lines)

    async def gone(self, link):
        self.links.pop(link.seat, None)
        if not self.links:
            self.closing = True
            await self.queue.put(None)

    # ---- the session ---------------------------------------------------------------------------------------
    async def run(self):
        try:
            self.core = await CoreClient.connect(*self.svc.core_addr)
            await self.core.source(self.addr)
            await self.core.claim(CLIENT_NAME, MACHINE)
        except (OSError, asyncio.TimeoutError, CoreError) as e:
            log(self.sid, "the core is not answering: %s" % e)
            self.core = None
        if self.core is not None:
            asyncio.ensure_future(self.greet())
        ticker = asyncio.ensure_future(self.tick())
        try:
            while not self.closing:
                item = await self.queue.get()
                if item is None:
                    continue
                link, kind, m = item
                try:
                    await self.on_message(link, kind, m)
                except (CoreError, OSError, asyncio.TimeoutError) as e:
                    log(self.sid, "message %s: %s" % (kind, e))
                except Exception as e:
                    log(self.sid, "message %s failed: %r" % (kind, e))
                    import traceback
                    traceback.print_exc()
        finally:
            ticker.cancel()
            await self.hang_up()

    async def hang_up(self):
        for link in list(self.links.values()):
            link.close()
        if self.ticket is not None:
            self.ticket.release()
        if self.core is None:
            return
        try:
            if self.name and self.tid and not self.started:
                await self.req("LVE %s" % self.tid)
            if self.name:
                await self.core.out()
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.core.close()

    async def tick(self):
        last_ivl = 0.0
        while not self.closing:
            await asyncio.sleep(self.svc.poll)
            try:
                now = time.monotonic()
                if self.core is None:
                    await self.say("The game server is not answering. Try again soon.")
                    self.stop()
                    return
                if self.name is None and now - self.t0 > LOGIN_TOTAL_SECS:
                    await self.say("Signing in took too long. Connect again to try again.")
                    self.stop()
                elif self.tid and not self.polling:
                    self.polling = True
                    await self.queue.put((None, "core", None))
                if self.name and not self.started and now - last_ivl >= INVITE_SECS:
                    last_ivl = now
                    await self.check_invitations()
                if not self.started and self.nw is None and now - self.last_typed > LOBBY_SECS:
                    await self.say("Nothing happened for a long time; goodbye.")
                    self.stop()
                for link in list(self.links.values()):     # between games only: during one Networdz takes
                    if self.nw is None and now - link.sent >= self.svc.keep:     # a D for a move out of turn
                        await link.show_name()
            except (CoreError, OSError, asyncio.TimeoutError) as e:
                log(self.sid, "tick: %s" % e)
            except Exception as e:
                log(self.sid, "tick error: %r" % e)

    def stop(self):
        self.closing = True
        for link in list(self.links.values()):
            link.close()
        self.queue.put_nowait(None)

    # ---- messages from Networdz ----------------------------------------------------------------------------
    async def on_message(self, link, kind, m):
        if self.svc.trace and kind != "core":
            log(self.sid, "trace: seat %d sent %s" % (link.seat, m.hex()))
        if kind == "core":
            self.polling = False
            if self.tid:
                await self.table_events()
            return
        if self.ticket is not None and self.name is None:
            self.ticket.touch()
        if link is not self.primary():
            return                  # the host sends everything to every connection: the first one is read
        seat = m[1]
        if kind in "DHQJKOB" and seat in self.links:
            return                  # our own, sent on to the other connections
        handler = getattr(self, "nw_" + kind, None)
        if handler is not None:
            await handler(seat, m)

    async def nw_B(self, seat, m):
        text = cstr(m[2:]).strip()
        if seat != self.host_seat() or not text:
            return
        await self.chat(text)

    async def nw_E(self, seat, m):
        """A New Game starts: its rules."""
        bag = self.pending_bag
        self.reset_game()
        self.nw = {"rules": parse_rules(m), "bag": bag, "racks": {}, "turn": None, "host": None,
                   "players": None, "order": [], "ok": True}
        r = self.nw["rules"]
        off = [n for k, n in (("trade", "Trade Blanks"), ("four", "Exchange 4 of a kind"), ("timed", "Timed"),
                              ("tournament", "Tournament Rules")) if r[k]]
        log(self.sid, "New Game: %s" % r)
        if off:
            await self.refuse(["ParlorNet plays without %s." % " or ".join(off),
                               "Stop Game, turn them off in Options, Rules, then New Game."])
        await self.check_bag()

    async def nw_F(self, seat, m):
        if not self.nw:
            return
        players = parse_players(m)
        self.nw["players"] = players
        persons = [i for i, p in enumerate(players) if p[0] == PERSON]
        remotes = [i for i, p in enumerate(players) if p[0] == REMOTE]
        if any(p[0] == COMPUTER for p in players):
            await self.refuse(["Computer players cannot play at the parlor.", "Set them to none, then New Game."])
        elif len(persons) != 1:
            await self.refuse(["Exactly one Person plays at your PC here.", "Make the others Remote players."])
        elif sorted(remotes) != self.ours:
            await self.refuse(["Every Remote player must be the parlor's address.",
                               "Connect again with Options, Communicate."])
        else:
            self.nw["host"] = persons[0]
            log(self.sid, "players: %s" % ", ".join("%d %s" % (i, p[1]) for i, p in enumerate(players) if p[0]))
            if self.name is None:
                await self.say(["Sign in to play: /signin NAME PASSWORD", "Your game waits until then."])
            elif not self.tid or self.over:
                await self.say(["Make your table first: /invite NAME (or bot), or /open.",
                                "Your game waits until everyone is seated."])
            elif not self.started:
                await self.waiting_line()
            elif len(self.ours) + 1 != len((await self.table() or {}).get("seated", [])):
                await self.refuse(["Table %s has another number of seats." % self.tid,
                                   "Stop Game; /resign it, then make another."])

    async def refuse(self, lines):
        if self.nw:
            self.nw["ok"] = False
        await self.say(lines)

    async def nw_A(self, seat, m):
        """The bag: at a New Game (before or after its E), after the starting picks and after an exchange."""
        bag = list(m[2:2 + BAG])
        if self.nw is None:
            self.pending_bag = bag
            return
        self.nw["bag"] = bag
        await self.check_bag()

    async def check_bag(self):
        """A new game's bag must hold the ParlorNet configuration's tiles, once (PARLOR.CFG)."""
        if self.nw.get("checked") or self.nw.get("bag") is None:
            return
        self.nw["checked"] = True
        if not bag_ok(self.nw["bag"]) and self.nw["ok"]:
            await self.refuse(["Your Networdz plays another tile set.",
                               "Load PARLOR.CFG (Options, Configuration), then New Game."])

    async def nw_G(self, seat, m):
        if seat in self.links:
            self.held.append(("G", seat))
            await self.answer_held()

    async def nw_R(self, seat, m):
        if self.nw:
            self.nw["order"] = [s for s in u16s(m[2:], 4)]

    async def nw_P(self, seat, m):
        if seat in self.links:
            self.held.append(("P", seat))
            await self.answer_held()

    async def nw_Q(self, seat, m):
        """The host's draw: its rack, which the table takes (RACK)."""
        if not self.nw or self.nw.get("bag") is None:
            return
        rack, draws = list(m[2:2 + RACK]), list(m[12:12 + RACK])      # the rack before the draw, the places
        for i, place in enumerate(draws):
            if place != EMPTY and place < BAG:
                rack[i] = self.nw["bag"][place]
                self.nw["bag"][place] = EMPTY
        self.nw["racks"][seat] = rack
        if seat == self.host_seat() and self.playing():
            await self.move("RACK %s" % (letters_of(rack) or "-"), quiet=True)

    async def nw_I(self, seat, m):
        if not self.nw:
            return
        self.nw["turn"] = seat
        if self.host_pass:          # the host passed with an empty bag: no discards came
            self.host_pass = False
            await self.move("PASS")
        cs = self.core_seat(seat)
        if cs is not None and self.playing():
            v = await self.view()
            if v["turn"] >= 0 and v["turn"] != cs:
                await self.move("TURN %d" % cs, quiet=True)

    async def nw_J(self, seat, m):
        if seat == self.host_seat():
            self.host_play = parse_play(m)

    async def nw_M(self, seat, m):
        code, word = m[2], cstr(m[5:])
        if seat == self.host_seat():
            play, self.host_play = self.host_play, None
            if code == 0 and play and self.playing():
                await self.host_played(play)
            return
        if seat not in self.links or not self.playing():
            return
        cs = self.core_seat(seat)
        before = self.sent_play.pop(seat, None)
        if before is None:              # no play of ours waits for it
            log(self.sid, "seat %d: the host's M %d for no play" % (seat, code))
            return
        if code == 0:
            return
        if before is not None:          # refused: the tiles stay on the seat's rack in Networdz
            self.nw["racks"][seat] = before
        why = REFUSED.get(code, "refused by the host (code %d)" % code)
        _, final = await self.req("MOV %s VOID %s" % (self.tid, word.upper()) if word.isalpha() else
                                  "MOV %s VOID" % self.tid)
        log(self.sid, "seat %d's %s refused (%d): %s" % (cs, word, code, final))
        await self.req("CHT %s %s" % (self.tid, ("%s is %s." % (word.upper() or "That word", why))[:56]))

    async def nw_K(self, seat, m):
        if seat == self.host_seat():
            self.host_pass = True

    async def nw_N(self, seat, m):
        """Discard now: a parlor seat's pass or exchange, kept since its K."""
        if seat not in self.links:
            return
        kind, put_back, _ = self.outgoing.pop(seat, ("pass", [], ""))
        rack = list(self.nw["racks"].get(seat, []))
        await self.links[seat].send(msg_o(seat, rack, put_back))
        for i in put_back:              # the tiles go back to the bag after the draw (Networdz's A says where)
            rack[i] = EMPTY
        self.nw["racks"][seat] = rack

    async def nw_O(self, seat, m):
        """The host's discards after its K: none is a pass, else an exchange."""
        if seat != self.host_seat() or not self.playing():
            return
        self.host_pass = False
        rack = list(m[2:2 + RACK])
        flags = u16s(m[14:], RACK)
        give = "".join(letter(rack[i]) for i in range(RACK) if flags[i] and rack[i] != EMPTY)
        await self.move("EXCH %s" % give if give else "PASS")

    async def show_names(self):
        """Between games: the names that changed during one."""
        for link in list(self.links.values()):
            if not link.shown:
                await link.show_name()

    async def nw_S(self, seat, m):
        """Networdz's game is over (by its own rules: every player passed with the bag empty, or one went out)."""
        if self.playing():
            v = await self.view()
            if v["turn"] >= 0:
                await self.move("OVER", quiet=True)
        self.nw = None
        await self.show_names()

    async def nw_Y(self, seat, m):
        if self.playing():
            await self.say(["Table %s goes on at the parlor:" % self.tid,
                            "finish it on the web or by telnet, or /resign it."])
        self.detached = True
        self.nw = None
        await self.show_names()

    async def nw_Z(self, seat, m):
        log(self.sid, "the host quit")
        self.stop()

    # ---- the table's side ------------------------------------------------------------------------------
    def playing(self):
        return bool(self.tid and self.started and not self.over and not self.detached and self.nw
                    and self.nw.get("ok"))

    async def move(self, text, quiet=False):
        _, final = await self.req("MOV %s %s" % (self.tid, text))
        if not final.startswith("OK"):
            log(self.sid, "the table refused %s: %s" % (text, final))
            await self.out_of_step("The table refused %s: %s." % (text.split()[0].lower(), err_text(final)))
        elif not quiet:
            log(self.sid, "host: %s" % text)
        return final.startswith("OK")

    async def out_of_step(self, why):
        if self.detached:
            return
        self.detached = True
        await self.say([why, "Finish table %s on the web or by telnet." % self.tid])

    async def view(self):
        data, final = await self.req("VUE %s" % self.tid)
        if not final.startswith("OK"):
            raise CoreError(err_text(final))
        v = G.parse_view(data)
        v["racks"] = parse_racks(data)
        return v

    async def host_played(self, play):
        """The host's play, which its own dictionary took: on the table as it stands, with OWN."""
        v = await self.view()
        placed = [(col + 1, row + 1, letter(tile) if letter(tile) != "_" else bl, letter(tile) == "_")
                  for row, col, tile, bl in play[:RACK] if row != OFF and tile != EMPTY and letter(tile)]
        tokens = play_move(placed, v["tiles"]) if placed else None
        if tokens is None:
            await self.out_of_step("Your play could not be read.")
            return
        await self.move(" ".join(tokens + ["OWN"]))

    async def answer_held(self):
        """G and P for parlor seats, once the table's game runs (a new game waits until everyone is seated)."""
        if not self.nw or not self.nw.get("ok") or not self.started or self.over or self.detached:
            return
        while self.held:
            kind, seat = self.held.pop(0)
            if kind == "G":
                bag = self.nw.get("bag") or []
                places = [i for i, t in enumerate(bag[:len(TILESET)]) if t != EMPTY]
                place = random.choice(places) if places else 0
                await self.links[seat].send(msg_h(seat, min(2 * seat, RACK - 1), place))     # its slot: 2 x seat
            else:
                await self.draw(seat)

    async def draw(self, seat):
        """Networdz asks a parlor seat to draw: as a remote copy does, a place in the host's bag for each empty
        slot while it has tiles. The table takes the rack first (RACK letters seat: the host's program deals),
        so a player there has it before Networdz gives the next turn."""
        cs = self.core_seat(seat)
        rack = list(self.nw["racks"].get(seat, [EMPTY] * RACK))
        rack += [EMPTY] * (RACK - len(rack))
        bag = self.nw["bag"]
        draws = [EMPTY] * RACK
        kept = list(rack)               # Q carries the rack as it was before the draw, and the places drawn
        for slot in range(RACK):
            places = [i for i, t in enumerate(bag[:len(TILESET)]) if t != EMPTY]
            if rack[slot] != EMPTY or not places:
                continue
            place = random.choice(places)
            draws[slot], rack[slot] = place, bag[place]
            bag[place] = EMPTY
        self.nw["racks"][seat] = rack
        if await self.move("RACK %s %d" % (letters_of(rack) or "-", cs), quiet=True):
            await self.links[seat].send(msg_q(seat, kept, draws))

    async def table(self):
        data, final = await self.req("LST table=%s" % self.tid)
        return next((t for t in parse_tables(data) if t["id"] == self.tid), None) if final.startswith("OK") else None

    async def table_events(self):
        data, final = await self.req("SIN %s %d" % (self.tid, self.cursor))
        if final.startswith("ERR"):
            if not self.started:
                self.tid = None
                await self.say("Your table is gone. /open or /invite to make another.")
            return
        evs = []
        for l in data:
            p = l.split(" ", 4)
            if len(p) < 4 or p[0] != "EVT" or not p[2].isdigit() or int(p[2]) <= self.cursor:
                continue
            evs.append((int(p[2]), p[3], (p[4] if len(p) > 4 else "").split()))
        says = chatcmd.join_says(["%s %s" % (kind, " ".join(a)) for _, kind, a in evs])
        for i, (seq, kind, a) in enumerate(evs):
            self.cursor = seq
            nxt = evs[i + 1] if i + 1 < len(evs) else None
            await self.core_event(kind, a, nxt[2] if nxt and nxt[1] == "X" else None, says)

    async def core_event(self, kind, a, tiles=None, says=None):
        seat = int(a[0]) if a and a[0].isdigit() else -1
        nseat = self.nw_seat(seat) if seat >= 0 else None
        change = chatcmd.change_of(kind, a, says or {}, 0)
        if kind == "JOIN" and len(a) >= 2:
            if nseat in self.links:
                await self.links[nseat].rename(a[1])
            if seat != 0:
                say = (says or {}).get(seat, "")
                await self.say(chatcmd.joined(a[1], say, "sits down at table %s" % self.tid, SAY_LEN)
                               if self.nw is None else
                               chatcmd.joined(a[1], say, "sits down (named after this game)", SAY_LEN))
        elif change:                        # a player plays on with another program
            t = await self.table()
            name = t["seated"][change[0]] if t and change[0] < len(t["seated"]) else "seat %d" % (change[0] + 1)
            await self.say(chatcmd.client_change(name, change[1], SAY_LEN))
        elif kind == "LEAVE" and nseat in self.links and not self.started:
            await self.links[nseat].rename(HOST_NAME)
        elif kind == "START":
            self.started = True
            log(self.sid, "table %s starts" % self.tid)
            if self.nw:
                await self.answer_held()
            else:
                await self.say(["Everyone is seated at table %s." % self.tid, "Choose New Game to play."])
        elif kind == "UNINVITE" and len(a) >= 2 and not self.started:
            why = a[2] if len(a) > 2 else ""
            await self.say("%s %s." % (a[1], {"declined": "declined your invitation", "expired":
                                               "did not come in time"}.get(why, "is no longer invited")))
        elif kind == "CHT" and len(a) >= 2 and seat != 0:
            text = " ".join(a[1:])
            if nseat in self.links:
                await self.links[nseat].send(msg_b(nseat, text))
            else:
                await self.say(text)
        elif kind == "END":
            self.over = True
            log(self.sid, "table %s over: %s" % (self.tid, " ".join(a)))
            if self.nw and a[:1] in (["resign"], ["timeout"]):
                who = self.links.get(nseat).name if nseat in self.links else "You"
                await self.say(["%s %s: the game is over at the parlor." % (
                    who, "resigned" if a[0] == "resign" else "ran out of time"), "Choose Stop Game."])
        elif not self.playing() or seat <= 0 or nseat not in self.links:
            return
        elif kind == "PLAY" and tiles:
            await self.seat_played(nseat, tiles)
        elif kind in ("EXCH", "PASS"):
            await self.seat_passed(nseat, kind)

    async def seat_played(self, nseat, f):
        """A parlor seat's play, from the tiles of the X event after its PLAY: J for Networdz."""
        placed = [(int(f[i]), int(f[i + 1]), f[i + 2]) for i in range(0, len(f) - 2, 3)]
        rack = list(self.nw["racks"].get(nseat, []))
        rack += [EMPTY] * (RACK - len(rack))
        recs, used = [], {}
        for x, y, c in placed:
            want = "_" if c.islower() else c
            slot = next((i for i in range(RACK) if i not in used and rack[i] != EMPTY and letter(rack[i]) == want),
                        None)
            if slot is None:
                await self.out_of_step("Seat %s's rack in Networdz lacks %s." % (self.core_seat(nseat), want))
                return
            used[slot] = (y - 1, x - 1, rack[slot], c.upper() if c.islower() else "")
        self.sent_play[nseat] = list(rack)          # the rack as it was, should the host refuse the play
        for i in range(RACK):
            recs.append(used.get(i, (OFF, OFF, rack[i], "")))
            if i in used:
                rack[i] = EMPTY
        self.nw["racks"][nseat] = rack
        await self.links[nseat].send(msg_j(nseat, recs))

    async def seat_passed(self, nseat, kind):
        """A parlor seat's exchange or pass: K now, its discards when Networdz asks (N), its draw at P."""
        put_back = []
        if kind == "EXCH":
            v = await self.view()
            rack = list(self.nw["racks"].get(nseat, []))
            now = v["racks"].get(self.core_seat(nseat), "")
            gone = minus(letters_of(rack), now)
            for c in gone:
                slot = next(i for i in range(len(rack)) if i not in put_back and letter(rack[i]) == c)
                put_back.append(slot)
        self.outgoing[nseat] = ("pass", put_back, "")
        await self.links[nseat].send(msg_k(nseat))

    # ---- chat and commands ---------------------------------------------------------------------------------
    async def chat(self, text):
        self.last_typed = time.monotonic()
        cmd = chatcmd.parse(text)
        if self.core is None:
            return
        try:
            if self.console is None:
                if cmd is not None and cmd.name == "signin":
                    await self.do_signin(cmd)
                elif cmd is not None and cmd.name == "help":
                    await self.say(chatcmd.help_lines(BEFORE_SIGNIN))
                elif cmd is not None and cmd.name == "motd":
                    await self.say((await self.core.motd()) or "There is no message of the day.")
                elif cmd is not None and cmd.name == "quit":
                    await self.do_quit(cmd)
                else:
                    await self.say("Sign in first: /signin NAME PASSWORD")
                return
            self.core.user_input()
            if cmd is not None and await self.console.handle(text):
                return
            if self.tid:
                _, final = await self.req("CHT %s %s" % (self.tid, re.sub(r"[^ -~]", "?", text)[:56]))
                if not final.startswith("OK"):
                    await self.say("Your chat did not go: %s" % err_text(final))
            else:
                await self.say("Chat reaches the other players once you have a table.")
        except (CoreError, OSError, asyncio.TimeoutError) as e:
            log(self.sid, "command failed: %s" % e)
            await self.say("The server is not answering just now.")

    async def do_signin(self, cmd):
        if self.name:
            await self.say("You are signed in as %s." % self.name)
            return
        args = cmd.args
        named = None
        if self.nw and self.nw.get("host") is not None and self.nw.get("players"):
            named = self.nw["players"][self.nw["host"]][1]
        if self.core.auth == "none":
            name, password = (args[0] if args else named), None
        elif len(args) >= 2:
            name, password = args[0], " ".join(args[1:])
        elif len(args) == 1 and named:
            name, password = named, args[0]
        else:
            await self.say("Type /signin NAME PASSWORD")
            return
        if not NAME_RE.match(name or ""):
            await self.say("Type /signin NAME PASSWORD")
            return
        shown = []

        async def keep(lines):
            shown.extend(lines)
        why = await sign_in_lines(self.core, name, password, None, keep, "signin",
                                  "There is no account %s here. Make one on the web site first." % name)
        if why == NEWPASS_TEXT:
            await self.say(["Sign in on the web or by telnet first", "and choose your own password there."])
            return
        if shown:
            await self.say(shown)
        if why:
            log(self.sid, "sign-in refused")
            await self.say(no_wait(why))
            return
        if self.ticket is not None and not self.ticket.sign_in():
            await self.say("The server is full. Try again soon.")
            self.stop()
            return
        self.name = self.core.name or name
        log(self.sid, "signed in as %s" % self.name)
        self.core.user_input()
        self.console = Console(self)
        n = len(self.links)
        await self.say(["Signed in as %s." % self.name,
                        "Your Networdz has %d remote seat%s here." % (n, "" if n == 1 else "s"),
                        "/invite NAME or /invite bot for each, or /open."])
        await self.check_invitations()

    async def do_quit(self, cmd):
        await self.say("Goodbye.")
        self.stop()

    async def check_invitations(self):
        data, final = await self.req("IVL")
        if not final.startswith("OK"):
            return
        invs = parse_invites(data)
        for i in invs:
            if i["id"] in self.announced:
                continue
            self.announced[i["id"]] = i["from"]
            await self.say(["%s invites you to table %s." % (i["from"], i["id"]),
                            "Networdz plays the tables it hosts: /invite %s here." % i["from"]])

    async def waiting_line(self):
        t = await self.table()
        if not t:
            return
        free = t["seats"] - t["filled"]
        await self.say("Table %s waits for %d more player%s." % (self.tid, free, "" if free == 1 else "s"))

    async def make_table(self, vis):
        """The table for this host's game: a seat for its player and one for each remote seat here."""
        if self.tid and not self.over:
            return self.tid
        n = 1 + len(self.links)
        if not 2 <= n <= 4:
            await self.say("Connect one to three Remote players to the parlor first.")
            return None
        _, final = await self.req("NEW %s %s players=%d racks=open turn=%s vis=%s"
                                  % (GRID, RULES, n, self.svc.turn, vis))
        if not final.startswith("OK"):
            await self.say("Can't open a table: %s" % err_text(final))
            return None
        tid = final.split()[1]
        _, final = await self.req("INT %s false %s" % (tid, REASON))
        if not final.startswith("OK"):
            await self.say("Can't open a table: %s" % err_text(final))
            return None
        await self.req("TDS %s %s %s" % ((tid,) + TABLE_MARK))
        _, final = await self.req("JON %s 0" % tid)
        if not final.startswith("OK"):
            await self.req("LVE %s" % tid)
            await self.say("Can't open a table: %s" % err_text(final))
            return None
        self.tid, self.cursor, self.started, self.over = tid, 0, False, False
        self.detached = False
        for link in self.links.values():
            if link.name != HOST_NAME:
                await link.rename(HOST_NAME)
        log(self.sid, "table %s for %d players (%s)" % (tid, n, vis))
        return tid


class Console(chatcmd.Console):
    """The shared chat commands, on the one table this host's game is played at."""

    def __init__(self, h):
        self.h = h
        super().__init__(h.say, h.req, lambda: h.name, COMMANDS, kinds=[GRID], label="Grid Words",
                         new="%s racks=open" % RULES, notes=NOTES,
                         handlers={"quit": h.do_quit, "signin": h.do_signin, "invite": self.do_invite,
                                   "open": self.do_open, "accept": self.do_accept, "close": self.do_close},
                         current=lambda: h.tid if h.started and not h.over else None)

    async def do_open(self, cmd):
        h = self.h
        if h.tid and not h.over:
            await self.say("Your table is %s." % h.tid)
            return
        if await h.make_table("public"):
            await self.say(["Table %s is open: anyone can sit down." % h.tid, "/close closes it."])

    async def do_invite(self, cmd):
        h = self.h
        name = cmd.args[0]
        if name.lower() == (h.name or "").lower():
            await self.say("You cannot invite yourself.")
            return
        if not (h.tid and not h.over) and not await h.make_table("private"):
            return
        t = await h.table()
        if t and t["status"] != "open":
            await self.say("Table %s has begun already." % h.tid)
            return
        if botflag.is_bot_name(name):
            data, _ = await self.req("SIN %s 0" % h.tid)
            invited = [l.split()[5] for l in data if l.startswith("EVT") and l.split()[3] == "INVITE"
                       and len(l.split()) > 5]
            name = botflag.next_name((t["names"] if t else []) + invited) or name
        _, final = await self.req("INV %s %s" % (h.tid, name))
        if not final.startswith("OK"):
            await self.say("There is no player called %s here." % name if final.startswith("ERR NOPLAYER")
                           else "Can't invite %s: %s" % (name, err_text(final)))
            return
        if final == "OK offline":
            await self.say("%s is not online now. Your invitation waits." % name)
        elif botflag.is_bot_name(name):
            await self.say("You invited the bot to table %s." % h.tid)
        else:
            await self.say("You invited %s to table %s." % (name, h.tid))

    async def do_accept(self, cmd):
        await self.say(["Networdz plays only the tables it hosts.", "/invite %s here instead." % cmd.args[0]])

    async def do_close(self, cmd):
        h = self.h
        if not h.tid or h.started:
            await self.say("You have no open table.")
            return
        await self.req("LVE %s" % h.tid)
        await self.say("Table %s is closed." % h.tid)
        h.tid = None
        for link in h.links.values():
            if link.name != HOST_NAME:
                await link.rename(HOST_NAME)


# ---- the listener -----------------------------------------------------------------------------------------

class Service:
    def __init__(self, args):
        self.args = args
        self.core_addr = (args.core_host, args.core_port)
        self.poll, self.turn, self.keep, self.chat_gap = args.poll, args.turn, args.keep, args.chat_gap
        self.trace = args.trace
        self.hosts = {}             # address -> Host
        self.gate = Gate(MAX_SESSIONS)
        self.gate.notice = b""

    async def handle(self, reader, writer):
        keepalive(writer)
        link = Link(self, reader, writer, None)
        msgs, data, end = [], b"", time.monotonic() + HELLO_SECS
        try:
            while not msgs:         # X may come in pieces
                more = await asyncio.wait_for(reader.read(64), max(0.1, end - time.monotonic()))
                if not more:
                    break
                data += more
                msgs = link.wire.feed(more)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        if not msgs or msgs[0][0] != "X" or msgs[0][1][1] >= 4:
            log("-", "not Networdz: %r" % data[:16])
            writer.close()
            return
        link.seat = msgs[0][1][1]
        host = self.hosts.get(link.addr)
        if host is None or host.closing or link.seat in host.links:
            ticket = self.gate.admit(link.addr, writer)     # a Networdz host signing in counts once
            if ticket is None:
                writer.close()
                return
            host = Host(self, link.addr)
            host.ticket = ticket
            self.hosts[link.addr] = host
            asyncio.ensure_future(self.run_host(host))
        await host.add(link)
        for kind, m in msgs[1:]:
            await host.queue.put((link, kind, m))
        try:
            while not link.closed:
                data = await reader.read(4096)
                if not data:
                    break
                for kind, m in link.wire.feed(data):
                    if kind == "junk":
                        log(host.sid, "not understood: %r" % m[:16])
                        continue
                    await host.queue.put((link, kind, m))
        except (ConnectionError, OSError):
            pass
        finally:
            link.close()
            log(host.sid, "seat %d disconnected" % link.seat)
            await host.gone(link)

    async def run_host(self, host):
        try:
            await host.run()
        except Exception as e:
            print("networdz session error: %r" % e, file=sys.stderr, flush=True)
        finally:
            if self.hosts.get(host.addr) is host:
                del self.hosts[host.addr]


async def serve(args):
    svc = Service(args)
    server = await asyncio.start_server(svc.handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("networdz adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    global LOGIN_TOTAL_SECS
    ap = argparse.ArgumentParser(prog="adapters.networdz")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6516)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    ap.add_argument("--turn", default="30m", help="time per turn at the tables this extension makes")
    ap.add_argument("--keep", type=float, default=KEEP_SECS,
                    help="seconds with nothing sent on a connection before its name goes again")
    ap.add_argument("--trace", action="store_true", help="log every message to and from Networdz, in hex")
    declare.add_args(ap)
    ap.add_argument("--poll", type=float, default=POLL_SECS, help=argparse.SUPPRESS)              # tests
    ap.add_argument("--chat-gap", type=float, default=CHAT_GAP, help=argparse.SUPPRESS)          # tests
    ap.add_argument("--login-secs", type=float, default=LOGIN_TOTAL_SECS, help=argparse.SUPPRESS)  # tests
    a = ap.parse_args(argv)
    LOGIN_TOTAL_SECS = a.login_secs
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
