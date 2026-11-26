"""Networdz extension: ParlorNet's Grid Words for Networdz 2.0 on Windows.

ParlorNet is not affiliated with Networdz's author. Networdz (Lance Frohman, San Francisco, 1996; Windows 3.1 or
later, Winsock) plays a crossword-tile game between two to four PCs over TCP, peer to peer: one copy is the host
("Local / Dial mode") and connects out to each remote player's copy, which waits in "Answer mode". The host's copy
deals, judges the words and runs the game. Here the parlor is always that host. Each connection is one Networdz
player, a guest seated at an ordinary gridwords/1 table beside players on the web, by telnet, in Play, on the
8-bit clients, in GameMaster or the bot, and other Networdz guests. The parlor deals, judges every word with its
own word list and ends the game, as for every other client; this extension speaks the host's side of Networdz's
protocol to the guest's copy. The protocol was read from games captured between copies of the program, and the
host's side checked against the stock program in Answer mode.

A stock copy only answers: it waits for its host to connect. It reaches the parlor through ParlorNet Link
(github.com/eric-cgn/parlornet-networdz-link), a small Windows program of ParlorNet's own that runs beside it on
the player's PC: it connects to the copy in Answer mode and to this extension and passes the bytes along both
ways, so the extension sees a copy that dialed out. The extension only listens; it never connects out. Before it
calls the parlor, the Link tells the copy it is seat 3 (X) and waits for its D, to know the copy is answering;
the extension's X for LOBBY_SEAT then gets D again, as at a game's start. So LOBBY_SEAT must not be 3: the copy
drops what comes with an X for the seat it has already, here the lobby's F.

The wire. Binary messages, each a type letter and a fixed length; the second byte is a seat (Networdz's player
number from 0) or 4 for every player. From the host (the parlor):

    X seat 'C' seat         you are this seat (on connecting, and at a game's start for another seat)   4
    E 4 7 x u16             the rules: show racks, hints, tournament, exchange 4 of a kind, trade blanks,
                            timed, minutes; the parlor sends them all off                       16
    F 4 4 x 84              the players: type (0 none, 1 computer, 2 person, 3 remote), skill, name, address,
                            connected, seat                                                     338
    A 4 bag[250]            the bag: a tile number at each place, 0xFA where it is drawn        252
    R 4 4 x u16             the order of play, the first player first                          10
    P seat                  that seat draws now                                                2
    Q seat rack[8] .. draws[8] ..   another seat's draw: its rack before, the place drawn for each slot   24
    I seat new 0            that seat's turn; new 0: the same seat again after a refused play    4
    J seat 8 x (row, col, tile, blank's letter) ..   another seat's play (row and col 0x15: not played)   44
    M seat code dy dx word[6] tail[7]   the verdict on a play: 0 taken, 7 not in the word list    18
    K seat / N seat         another seat's pass / the guest discards now                        2
    O seat rack[8] .. 8 x u16 ..    another seat's discards: 1 for each tile put back           36
    B seat text[81]         a chat line; Networdz shows it as "name: text"                      83
    S 4 / Y 4 / Z 4         the game is over / stopped / the host has quit                      2

From the guest: D seat name[21] (its player's name, the answer to X), Q (its draw), J (its play), K (a pass),
O (its discards after N), B (chat), Z (it quits).

What the stock program was seen to need (docs/clients/networdz.md): it takes X again with another seat and
answers D for it, but X with the seat it has already makes it drop the rest of what came with it, so a game
whose seat is the guest's already starts with E; it starts a game with E, F, A and R and no starting picks (G, H); with "Draw Own Tiles" off it
draws its tiles at random among the places the last A holds; it shows a Person and a Computer in F, and a seat of
type 0 with a name speaks in the chat under that name without playing. M ends with seven bytes that a stock host
sends there (TAIL); a refused play whose M lacks them stops the answering copy with "Internal Error A3", and so
does a word that runs into them, so M carries at most five letters of the word. For a refused play it shows its
own message for each code and lets its player try again (7 "WORD is not in the dictionary", 14 "Tiles are not
in a line", 15 "Tiles are not connected", 16 "Word must cover center square", 18 "No tiles played", among
others), except codes 1 to 5, its system errors, which stop it.

Tiles. A tile's number is its place in the configuration's tile set: the blanks first, then each letter as many
times as the set has it. The parlor plays the standard set, so the Networdz player loads the ParlorNet
configuration (PARLOR.CFG, on the client's page): the standard board, the 100 tiles and their points, racks of 7.
Each guest's game keeps its own numbering of the table's tiles (Tiles): where each one is, on the board, on a
rack or in the bag. The extension reads the whole table with VUE O, which the core answers with every rack and
the bag only for a table marked integrity=false; the extension marks each table where a Networdz guest sits
(INT, REASON), because Networdz's protocol sends the bag and every rack to the guest's program. Networdz shows
its player only their own rack (E: Show Racks off).

The deal. The table deals and draws. Before the guest draws, A holds only the tiles the table dealt it, so the
copy's random pick takes exactly those; then A holds the whole bag again. Another seat's draw goes as Q with the
places of the tiles the table dealt it.

A game, for a guest at seat g:

    START           X g, E, F, A (every tile), R (the table's order: seat 0 first), then each seat's first draw
                    in that order: P s and Q s for another seat; A (its tiles only), P g, the copy's Q, A (the
                    whole bag) for the guest. Then I for the first turn
    the guest's J   PLAY at the table; M g 0 when it is taken (then its draw, as above, and the next I), or
                    M g 7 with the word the word list lacks, or the code of the program's own message for the
                    table's reason (REASONS; 7 with no word for any other), then I g 0, and the reason in the
                    chat
    the guest's K   N g while the bag holds 7 tiles or more; its O with tiles is EXCH, without PASS; with fewer
                    tiles in the bag K is PASS at once
    another seat    PLAY: J and M s 0, then P s and Q s for its draw; EXCH: K, O with the tiles put back,
                    P, Q, A; PASS: K (and O with none while the bag has tiles); TURN: I
    the end         S after the table's END (Networdz then works out the final scores itself, as the table
                    does); Y after a resignation, a clock run out or the operator's end, with a line saying so
    chat            B both ways; the parlor's own lines come from a seat of type 0 named ParlorNet in F (from
                    seat 4 when all four seats play)

Sign-in. Networdz sends names but nothing secret, so the Networdz player types /signin NAME PASSWORD in its Chat
window (/signin PASSWORD signs in with the name it sent in D); the name goes to the core and the password
answers its credential question (common.sign_in_lines). Then the shared chat commands (chat-commands.md) find,
open and join tables; /open N opens one for N players.

Left out: Trade Blanks, Exchange 4 of a kind, Timed and Tournament Rules (sent off), other configurations and
languages, watching from Networdz, and playing on a game that began before the connection (the program has no
message that sets up a board): a guest who leaves a running game finishes it elsewhere.

    python3 -m adapters.networdz --core 127.0.0.1:6590 --key networdz.key --port 6516
"""
import argparse
import asyncio
import collections
import itertools
import random
import re
import sys
import time

from . import chatcmd, declare
from .clients import BY_ID
from .common import (LOGIN_TOTAL_SECS, NEWPASS_TEXT, CoreClient, CoreError, Gate, describe_integrity_event,
                     err_text, keepalive, no_wait, parse_tables, peer_addr, sign_in_lines)
from .games import gridwords as G

CLIENT_NAME = BY_ID["networdz"].name        # "Networdz 2.0"
MACHINE = "Windows"                         # Networdz is a Windows program (3.1 or later)
PROTOCOL = ("NETWORDZ", "Networdz, for Networdz 2.0 on Windows", "Grid Words for Networdz 2.0 on Windows")
GRID = "gridwords/1"
RULES = "classic"
REASON = "a Networdz program receives the bag and all racks"     # INT: why the table is marked integrity=false
VOICE = "ParlorNet"                         # the name the parlor's own chat lines come under
MAX_SESSIONS = 40
POLL_SECS = 0.5             # the table's events
LOBBY_SECS = 1800.0         # connected, no game and nothing typed: the connection closes
DRAW_SECS = 60.0            # the guest's Q after P
CHAT_GAP = 0.3              # between lines in the chat (the instructions box shows the last)
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
LOBBY_SEAT = 1              # the guest's seat on connecting, before a game (X); not 3, ParlorNet Link's
COMMANDS = ["list", "resign", "invite", "open", "join", "close", "accept", "decline", "who", "tables", "top",
            "score", "note", "motd", "time", "signin", "quit", "help"]
BEFORE_SIGNIN = ("signin", "help", "motd", "quit")
NOTES = ["/open 3 or /open 4 opens a table for more players.", "Your game starts in Networdz once everyone sits."]
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
SIZE = 15                   # the board
NAME_LEN, TEXT_LEN = 21, 81
SAY_LEN = TEXT_LEN - 1 - len("ParlorNet: ")   # a line of the parlor's own, with the name before it
NONE, COMPUTER, PERSON, REMOTE = 0, 1, 2, 3
# M's codes, as the stock program shows them: the play is taken; "WORD is not in the dictionary"; "WORD is too
# short"; "Tiles are not in a line"; "Tiles are not connected"; "Word must cover center square"; "No tiles played"
TAKEN, NOT_LISTED, TOO_SHORT, NOT_IN_LINE, NOT_CONNECTED, NOT_CENTER, NO_TILES = 0, 7, 8, 14, 15, 16, 18
REASONS = [("not in the word list: ", NOT_LISTED), ("must join the words", NOT_CONNECTED),
           ("must cover the center", NOT_CENTER), ("at least one tile", NO_TILES), ("two letters or more", TOO_SHORT),
           ("part of the word too", NOT_IN_LINE)]
M_WORD = 5                  # letters of the word M carries
TAIL = bytes.fromhex("e0156d011916e0")      # M's last seven bytes, as a stock host sends them

# The ParlorNet configuration's tile set in Networdz's order: the blanks, then each letter (G.STD_TIL).
TILESET = "_" * G.STD_TIL["_"][1] + "".join(c * G.STD_TIL[c][1] for c in sorted(G.STD_TIL) if c != "_")


def u16(v):
    return bytes([v & 255, v >> 8 & 255])


def u16s(b, n):
    return [b[i] | b[i + 1] << 8 for i in range(0, 2 * n, 2)]


def text_field(text, n):
    return text.encode("latin-1", "replace")[:n - 1].ljust(n, b"\0")


def cstr(b):
    return b.split(b"\0", 1)[0].decode("latin-1", "replace")


def slots(values, fill=EMPTY):
    """A rack's 8 bytes: the rack's 7 slots (fill for a missing one), then 0."""
    vals = list(values[:RACK]) + [fill] * (RACK - len(values))
    return bytes(vals + [0] * (SLOTS - RACK))


def msg_x(seat):
    return b"X" + bytes([seat, ord("C"), seat])


def msg_e():
    """New Game's rules, every one off: Show Racks, Allow Hints, Tournament Rules, Exchange 4 of a kind, Trade
    Blanks, Timed (and its minutes, 2 as the program's default has them)."""
    return b"E" + bytes([ALL]) + b"".join(u16(v) for v in (0, 0, 0, 0, 0, 0, 2))


def msg_f(players):
    """players: [(type, name, seat)] for Networdz's four places, as many as given; a remote one is connected."""
    body = b""
    for i in range(4):
        kind, name, seat = players[i] if i < len(players) else (NONE, "", i)
        addr = b"parlor" if kind == REMOTE else b""
        tail = u16(1) + u16(seat) if kind == REMOTE else b"\0" * 4
        body += u16(kind) + u16(100) + text_field(name, NAME_LEN) + addr.ljust(49, b"\0") + tail + b"\0" * 6
    return b"F" + bytes([ALL]) + body


def msg_a(bag):
    return b"A" + bytes([ALL]) + bytes(bag[:BAG]).ljust(BAG, b"\0")


def msg_r(order):
    return b"R" + bytes([ALL]) + b"".join(u16(s) for s in (list(order) + [0] * 4)[:4])


def msg_p(seat):
    return b"P" + bytes([seat])


def msg_q(seat, rack, draws):
    return b"Q" + bytes([seat]) + slots(rack) + b"\0\0" + slots(draws, EMPTY) + b"\0" * 4


def msg_i(seat, new=True):
    return b"I" + bytes([seat, 1 if new else 0, 0])


def msg_j(seat, recs):
    """recs: one (row, col, tile, letter) for each rack slot; letter is a blank's ("T") or ""."""
    body = b""
    for row, col, tile, letter in recs[:SLOTS]:
        body += bytes([row, col, tile, ord(letter) if letter else 0])
    return b"J" + bytes([seat]) + body.ljust(4 * SLOTS, b"\0") + b"\0" * 10


def msg_m(seat, code, across, word=""):
    """The verdict on a play: code, the way its word runs (across: one step down a column is 0), the word (at
    most M_WORD letters, so that it stops before TAIL), and TAIL."""
    w = word.upper().encode("ascii", "replace")[:M_WORD]
    return b"M" + bytes([seat, code, 0 if across else 1, 1 if across else 0]) + w.ljust(M_WORD + 1, b"\0") + TAIL


def msg_k(seat):
    return b"K" + bytes([seat])


def msg_n(seat):
    return b"N" + bytes([seat])


def msg_o(seat, rack, put_back):
    flags = b"".join(b"\1\0" if i in put_back else b"\0\0" for i in range(SLOTS))
    return b"O" + bytes([seat]) + slots(rack) + b"\0" * 4 + flags + b"\0" * 6


def msg_b(seat, text):
    return b"B" + bytes([seat]) + text_field(text, TEXT_LEN)


def msg_end(kind):
    """S (the game is over), Y (stopped) or Z (the host quits)."""
    return kind.encode() + bytes([ALL])


class Reader:
    """Networdz's byte stream -> messages (kind, bytes); ("junk", bytes) for a type it does not know, after
    which the rest of what has come is dropped."""

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


def parse_play(m):
    """J -> [(row, col, tile, letter)] for each rack slot."""
    out = []
    for i in range(SLOTS):
        row, col, tile, letter = m[2 + 4 * i:6 + 4 * i]
        out.append((row, col, tile, chr(letter) if letter else ""))
    return out


def parse_draw(m):
    """Q -> (the rack before the draw, the place drawn for each slot), RACK slots each."""
    return list(m[2:2 + RACK]), list(m[12:12 + RACK])


def parse_discards(m):
    """O -> the slots put back."""
    flags = u16s(m[14:], RACK)
    return [i for i in range(RACK) if flags[i]]


def letter(tile):
    """A tile number -> its letter ("_" a blank), or "" for an empty slot or a number past the set."""
    return TILESET[tile] if 0 <= tile < len(TILESET) else ""


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


def common(a, b):
    """The letters a and b share, counted."""
    return minus(a, minus(a, b))


def play_move(placed, tiles):
    """A play: placed [(x, y, letter, blank)] (1-based squares) and the board's tiles {(x, y): c} -> the PLAY
    tokens with the whole main word, or None when the tiles are not in one line."""
    xs, ys = {p[0] for p in placed}, {p[1] for p in placed}
    new = {(x, y): (c, b) for x, y, c, b in placed}
    if len(new) != len(placed):
        return None
    if len(ys) == 1:
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


def parse_open_view(lines):
    """VUE O -> {"racks": {seat: letters}, "bag": letters, "turn", "tiles", ...} (G.parse_view and the RK and
    BO lines)."""
    v = G.parse_view(lines)
    racks, bag = {}, ""
    for l in lines:
        f = l.split()
        if len(f) >= 2 and f[0] == "RK" and f[1].isdigit():
            racks[int(f[1])] = "" if len(f) < 3 or f[2] == "-" else f[2]
        elif len(f) == 2 and f[0] == "BO" and f[1] != "-":
            bag += f[1]
    v["racks"], v["bagtiles"] = racks, bag
    return v


def refusal(reason, word=""):
    """The core's reason for refusing a play (word: its main word) -> (M's code, the word for M). Networdz shows
    its own message for each code; any reason it has none for goes as 7 with no word, and the chat says why."""
    m = re.match(r"not in the word list: ([A-Z]+)", reason or "")
    if m:
        return NOT_LISTED, m.group(1)
    code = next((c for text, c in REASONS if text in (reason or "")), NOT_LISTED)
    return code, word if code == TOO_SHORT else ""


def start_racks(now, backlog, me):
    """The racks a game started with, from the racks now ({seat: letters}) and the table's events not yet told
    Networdz (backlog: (kind, args, tiles of the X event after a PLAY, says), in order): a seat that has played
    since had the tiles it placed instead of those it drew. Which of its tiles came in the draw the table does
    not say, and Networdz does not show another seat's rack, so the last ones on the rack are taken. A seat
    plays once at most before the guest's first turn (me), which waits for Networdz."""
    racks = dict(now)
    items = [it for it in backlog if not callable(it)]
    for i, (kind, a, tiles, _) in enumerate(items):
        seat = int(a[0]) if a and a[0].isdigit() else -1
        if kind == "PLAY" and tiles and seat in racks and seat != me:
            placed = "".join("_" if c.islower() else c for c in tiles[2::3])
            k = next((int(it[1][1]) for it in items[i + 1:] if it[0] == "DRAW" and it[1][:1] == [str(seat)]
                      and len(it[1]) > 1 and it[1][1].isdigit()), 0)
            rack = racks[seat]
            racks[seat] = rack[:len(rack) - k] + placed
    return racks


def log(sid, text):
    print("networdz %s: %s" % (sid, text), file=sys.stderr, flush=True)


# ---- a game's tiles as Networdz numbers them --------------------------------------------------------------

class Desync(Exception):
    """Networdz and the table no longer agree about a tile."""


class Tiles:
    """One guest's game: each of the table's 100 tiles has a number (its place in TILESET), and is in the bag
    (a place 0..99 of the bag message), on a rack (a slot of a seat's 7) or on the board."""

    def __init__(self, racks, bag, rng=None):
        """racks: {seat: letters} and bag: letters, as the table has them before Networdz's first draws. Every
        tile starts in the bag, at a shuffled place; the first draws give each seat its rack."""
        self.rng = rng or random.Random()
        numbers = collections.defaultdict(list)
        for n, c in enumerate(TILESET):
            numbers[c].append(n)
        if sorted("".join(racks.values()) + bag) != sorted(TILESET):
            raise Desync("the table's tiles are not the ParlorNet set")
        places = list(range(len(TILESET)))
        self.rng.shuffle(places)
        self.bag = [EMPTY] * BAG
        for n, p in zip(range(len(TILESET)), places):
            self.bag[p] = n
        self.racks = {s: [EMPTY] * RACK for s in racks}
        self.board = {}             # (x, y) -> number
        self.blanks = {}            # (x, y) -> the letter a blank on the board stands for, in lower case
        self.out = []               # numbers put back by an exchange, until its draw is over

    def bag_letters(self):
        return "".join(letter(n) for n in self.bag if n != EMPTY)

    def rack_letters(self, seat):
        return "".join(letter(n) for n in self.racks[seat] if n != EMPTY)

    def places_for(self, letters):
        """Places in the bag holding these letters (one each), chosen at random among those that do."""
        out = []
        for c in letters:
            choice = [p for p, n in enumerate(self.bag) if n != EMPTY and letter(n) == c and p not in out]
            if not choice:
                raise Desync("the bag has no %s" % ("blank" if c == "_" else c))
            out.append(self.rng.choice(choice))
        return out

    def short_bag(self, places):
        """A: only these places hold their tiles."""
        return [self.bag[p] if p in places else EMPTY for p in range(len(TILESET))]

    def full_bag(self):
        return self.bag[:len(TILESET)]

    def draw(self, seat, places):
        """These places' tiles go to the seat's empty slots, in order -> (the rack before, the place for each
        slot), as Q has them."""
        rack = self.racks[seat]
        before = list(rack)
        draws = [EMPTY] * RACK
        empty = [i for i in range(RACK) if rack[i] == EMPTY]
        if len(places) > len(empty):
            raise Desync("seat %d has no room for %d tiles" % (seat, len(places)))
        for slot, p in zip(empty, places):
            if self.bag[p] == EMPTY:
                raise Desync("place %d is empty" % p)
            rack[slot], self.bag[p] = self.bag[p], EMPTY
            draws[slot] = p
        return before, draws

    def took(self, seat, before, draws):
        """The guest's own draw (its Q): check it against the places it was given, and take it."""
        if list(before[:RACK]) != self.racks[seat]:
            raise Desync("the rack before the draw differs")
        places = [p for p in draws if p != EMPTY]
        empty = [i for i in range(RACK) if self.racks[seat][i] == EMPTY]
        for slot, p in enumerate(draws[:RACK]):
            if p == EMPTY:
                continue
            if slot not in empty or p >= len(self.bag) or self.bag[p] == EMPTY:
                raise Desync("the draw names place %d for slot %d" % (p, slot))
            self.racks[seat][slot], self.bag[p] = self.bag[p], EMPTY
        return places

    def play(self, seat, placed):
        """Another seat's play: placed [(x, y, c)] as the X event has them (lower case: a blank) -> J's records,
        one for each rack slot."""
        rack = self.racks[seat]
        recs = {}
        for x, y, c in placed:
            want = "_" if c.islower() else c
            slot = next((i for i in range(RACK) if i not in recs and rack[i] != EMPTY and letter(rack[i]) == want),
                        None)
            if slot is None:
                raise Desync("seat %d's rack has no %s" % (seat, want))
            recs[slot] = (y - 1, x - 1, rack[slot], c.upper() if c.islower() else "")
        out = []
        for i in range(RACK):
            out.append(recs.get(i, (OFF, OFF, rack[i], "")))
            if i in recs:
                xy = (recs[i][1] + 1, recs[i][0] + 1)
                self.board[xy] = rack[i]
                if recs[i][3]:
                    self.blanks[xy] = recs[i][3].lower()
                rack[i] = EMPTY
        return out

    def guest_play(self, seat, recs):
        """The guest's J -> placed [(x, y, letter, blank)] (1-based squares), checked against its rack and the
        board; the tiles stay on the rack until the table takes the play (played)."""
        placed = []
        for slot, (row, col, tile, bl) in enumerate(recs[:RACK]):
            if row == OFF and col == OFF:
                continue
            if tile != self.racks[seat][slot] or not (0 <= row < SIZE and 0 <= col < SIZE):
                raise Desync("the play does not match the rack or the board")
            c = letter(tile)
            if c == "_" and not ("A" <= bl.upper() <= "Z"):
                raise Desync("a blank without its letter")
            placed.append((col + 1, row + 1, bl.upper() if c == "_" else c, c == "_", slot))
        return placed

    def played(self, seat, placed):
        for x, y, c, blank, slot in placed:
            self.board[(x, y)] = self.racks[seat][slot]
            if blank:
                self.blanks[(x, y)] = c.lower()
            self.racks[seat][slot] = EMPTY

    def put_back(self, seat, letters=None, slots_=None):
        """An exchange: these letters (or these slots) leave the rack until the draw is over -> the slots."""
        rack = self.racks[seat]
        if slots_ is None:
            slots_ = []
            for c in letters:
                slot = next((i for i in range(RACK) if i not in slots_ and rack[i] != EMPTY and letter(rack[i]) == c),
                            None)
                if slot is None:
                    raise Desync("seat %d's rack has no %s to put back" % (seat, c))
                slots_.append(slot)
        for i in slots_:
            self.out.append(rack[i])
            rack[i] = EMPTY
        return slots_

    def back_in_bag(self):
        """The tiles an exchange put back go to empty places of the bag."""
        empty = [p for p in range(len(TILESET)) if self.bag[p] == EMPTY]
        self.rng.shuffle(empty)
        for n, p in zip(self.out, empty):
            self.bag[p] = n
        self.out = []

    def tiles(self):
        """The board as {(x, y): letter}, a blank's letter in lower case, as the table's view has it."""
        return {xy: self.blanks.get(xy) or letter(n) for xy, n in self.board.items()}


# ---- one Networdz guest -----------------------------------------------------------------------------------

class Guest:
    def __init__(self, svc, reader, writer, ticket):
        self.svc, self.r, self.w, self.ticket = svc, reader, writer, ticket
        self.addr = peer_addr(writer)
        self.sid = next(ids)
        self.wire = Reader()
        self.queue = asyncio.Queue()
        self.core = None
        self.core_lock = asyncio.Lock()
        self.nw_name = ""           # the name its D said
        self.name = None            # signed in as
        self.console = None
        self.closed = False
        self.t0 = self.last_typed = time.monotonic()
        self.polling = False        # a look at the table's events is queued
        self.nseat = LOBBY_SEAT     # the seat X said last
        self.voice = 0              # the seat the parlor's own lines come from
        self.tid = None             # my table, once seated
        self.seat = None            # my seat there
        self.cursor = 0
        self.started = False        # the table's game has begun
        self.over = False           # ... and ended
        self.game = None            # Tiles, while a game is relayed
        self.names = []
        self.backlog = collections.deque()      # what is still to be told Networdz, in order
        self.waiting = None         # ("Q", places, then) or ("O",): Networdz's answer the backlog waits for
        self.wait_until = 0.0
        self.pending = None         # the guest's play or exchange the table is deciding
        self.my_turn = False

    # ---- output --------------------------------------------------------------------------------------------
    async def send(self, *msgs):
        data = b"".join(msgs)
        if not data or self.closed:
            return
        if self.svc.trace:
            log(self.sid, "trace: to Networdz %s" % data.hex())
        self.w.write(data)
        try:
            await self.w.drain()
        except (ConnectionError, OSError):
            self.closed = True

    async def say(self, lines):
        if isinstance(lines, str):
            lines = [lines]
        for i, line in enumerate(chatcmd.wrap([l for l in lines if l], TEXT_LEN - 1)):
            if i:
                await asyncio.sleep(self.svc.chat_gap)
            await self.send(msg_b(self.voice, line[:TEXT_LEN - 1]))

    async def req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    # ---- the session ---------------------------------------------------------------------------------------
    async def run(self):
        try:
            self.core = await CoreClient.connect(*self.svc.core_addr)
            await self.core.source(self.addr)
            await self.core.claim(CLIENT_NAME, MACHINE)
        except (OSError, asyncio.TimeoutError, CoreError) as e:
            log(self.sid, "the core is not answering: %s" % e)
            self.core = None
        await self.lobby()
        if self.core is None:
            await self.say("The game server is not answering. Try again soon.")
            self.close()
            return
        asyncio.ensure_future(self.greet())
        reader = asyncio.ensure_future(self.read())
        ticker = asyncio.ensure_future(self.tick())
        try:
            while not self.closed:
                item = await self.queue.get()
                if item is None:
                    continue
                kind, m = item
                try:
                    if kind == "core":
                        self.polling = False
                        await self.table_events()
                    else:
                        await self.on_message(kind, m)
                    await self.pump()
                except Desync as e:
                    await self.out_of_step(str(e))
                except (CoreError, OSError, asyncio.TimeoutError) as e:
                    log(self.sid, "%s: %s" % (kind, e))
                except Exception as e:
                    log(self.sid, "%s failed: %r" % (kind, e))
                    import traceback
                    traceback.print_exc()
        finally:
            reader.cancel()
            ticker.cancel()
            await self.hang_up()

    async def lobby(self):
        """Before a game: the guest is seat 1, and seat 0 is the parlor, named in the players' list (again once
        D names the guest)."""
        self.nseat, self.voice = LOBBY_SEAT, 0
        await self.send(msg_x(LOBBY_SEAT), self.lobby_players())

    def lobby_players(self):
        return msg_f([(PERSON, VOICE, 0), (REMOTE, self.nw_name, LOBBY_SEAT)])

    async def greet(self):
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

    async def read(self):
        try:
            while not self.closed:
                data = await self.r.read(4096)
                if not data:
                    break
                for kind, m in self.wire.feed(data):
                    if kind == "junk":
                        log(self.sid, "not understood: %r" % m[:16])
                        continue
                    if self.svc.trace:
                        log(self.sid, "trace: from Networdz %s" % m.hex())
                    await self.queue.put((kind, m))
        except (ConnectionError, OSError):
            pass
        log(self.sid, "disconnected")
        self.closed = True
        await self.queue.put(None)

    async def hang_up(self):
        self.close()
        if self.ticket is not None:
            self.ticket.release()
        if self.core is None:
            return
        try:
            if self.name and self.tid and not self.started:
                await self.req("LVE %s" % self.tid)      # stand up from a table whose game has not begun
            if self.name:
                await self.core.out()
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.core.close()

    def close(self):
        self.closed = True
        try:
            self.w.close()
        except Exception:
            pass
        self.queue.put_nowait(None)

    async def tick(self):
        while not self.closed:
            await asyncio.sleep(self.svc.poll)
            try:
                now = time.monotonic()
                if self.name is None and now - self.t0 > LOGIN_TOTAL_SECS:
                    await self.say("Signing in took too long. Connect again to try again.")
                    self.close()
                elif not self.started and now - self.last_typed > LOBBY_SECS:
                    await self.say("Nothing happened for a long time; goodbye.")
                    await self.send(msg_end("Z"))
                    self.close()
                elif self.waiting and now > self.wait_until:
                    await self.queue.put(("late", None))
                elif self.tid and not self.polling:
                    self.polling = True
                    await self.queue.put(("core", None))
            except (CoreError, OSError, asyncio.TimeoutError) as e:
                log(self.sid, "tick: %s" % e)

    # ---- messages from Networdz ----------------------------------------------------------------------------
    async def on_message(self, kind, m):
        if kind == "late":
            if self.waiting:
                raise Desync("Networdz did not answer in time")
            return
        if self.ticket is not None and self.name is None:
            self.ticket.touch()
        handler = getattr(self, "nw_" + kind, None)
        if handler is not None:
            await handler(m)

    async def nw_D(self, m):
        self.nw_name = cstr(m[2:])
        if self.game is None and self.nseat == LOBBY_SEAT:
            await self.send(self.lobby_players())

    async def nw_B(self, m):
        text = cstr(m[2:]).strip()
        if text:
            await self.chat(text)

    async def nw_Z(self, m):
        log(self.sid, "Networdz quit")
        self.close()

    async def nw_Q(self, m):
        if not self.waiting or self.waiting[0] != "Q":
            return
        _, given, then = self.waiting
        before, draws = parse_draw(m)
        took = self.game.took(self.nseat, before, draws)
        if sorted(took) != sorted(given):
            raise Desync("Networdz drew other tiles than the table dealt")
        self.waiting = None
        await self.send(msg_a(self.game.full_bag()))
        if then:
            await then()

    async def nw_J(self, m):
        if not self.my_turn or self.pending or self.game is None:
            return
        placed = self.game.guest_play(self.nseat, parse_play(m))
        tokens = play_move([p[:4] for p in placed], self.game.tiles()) if placed else None
        if tokens is None:
            await self.refuse_play(*((NOT_IN_LINE, "", "Your tiles are not in one line.") if placed else
                                     (NO_TILES, "", "You played no tile.")))
            return
        self.pending = ("play", placed, tokens)
        self.my_turn = False
        _, final = await self.req("MOV %s %s" % (self.tid, " ".join(tokens)))
        if final.startswith("OK"):
            word = tokens[4].replace("_", "")
            await self.send(msg_m(self.nseat, TAKEN, tokens[3] == "H", word))
            self.game.played(self.nseat, placed)
            return
        self.pending = None
        self.my_turn = True
        why = err_text(final)
        await self.refuse_play(*refusal(why, tokens[4].replace("_", "")), why=why[:1].upper() + why[1:] + ".")

    async def refuse_play(self, code, word, why):
        """M with the code, I to the same seat again, and the reason in the chat."""
        await self.send(msg_m(self.nseat, code, True, word), msg_i(self.nseat, new=False))
        await self.say(why)

    async def nw_K(self, m):
        if not self.my_turn or self.pending or self.game is None:
            return
        self.my_turn = False
        if len(self.game.bag_letters()) >= RACK:     # an exchange is possible: Networdz asks for the discards
            self.waiting = ("O",)
            self.wait_until = time.monotonic() + DRAW_SECS
            await self.send(msg_n(self.nseat))
            return
        await self.guest_move("PASS")

    async def nw_O(self, m):
        if not self.waiting or self.waiting[0] != "O":
            return
        self.waiting = None
        put = parse_discards(m)
        rack = self.game.racks[self.nseat]
        give = "".join(letter(rack[i]) for i in put if rack[i] != EMPTY)
        if not give:
            await self.guest_move("PASS")
            return
        self.pending = ("exch", put)
        await self.guest_move("EXCH %s" % give)

    async def guest_move(self, move):
        _, final = await self.req("MOV %s %s" % (self.tid, move))
        if not final.startswith("OK"):
            self.pending = None
            raise Desync("the table refused %s: %s" % (move.split()[0].lower(), err_text(final)))
        if move.startswith("EXCH"):
            self.game.put_back(self.nseat, slots_=self.pending[1])

    # ---- the table's side ------------------------------------------------------------------------------
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
        if evs and evs[-1][1] == "PLAY":    # its tiles come in the X event after it: wait for that
            evs.pop()
        says = chatcmd.join_says(["%s %s" % (kind, " ".join(a)) for _, kind, a in evs])
        for i, (seq, kind, a) in enumerate(evs):
            self.cursor = seq
            nxt = evs[i + 1] if i + 1 < len(evs) else None
            self.backlog.append((kind, a, nxt[2] if nxt and nxt[1] == "X" else None, says))

    async def pump(self):
        """Tell Networdz what the table did, in order, while it is not drawing or discarding."""
        while self.backlog and not self.waiting and not self.closed:
            item = self.backlog.popleft()
            if callable(item):
                await item()
            else:
                await self.core_event(*item)

    def nw_of(self, seat):
        return seat           # the table's seat n is Networdz's seat n

    async def core_event(self, kind, a, tiles=None, says=None):
        seat = int(a[0]) if a and a[0].isdigit() else -1
        change = chatcmd.change_of(kind, a, says or {}, self.seat if self.seat is not None else -1)
        if kind == "JOIN" and len(a) >= 2:
            if seat != self.seat:
                await self.say(chatcmd.joined(a[1], (says or {}).get(seat, ""), "sits down at table %s" % self.tid,
                                              SAY_LEN))
        elif change:
            t = await self.table()
            name = t["seated"][change[0]] if t and change[0] < len(t["seated"]) else "seat %d" % (change[0] + 1)
            await self.say(chatcmd.client_change(name, change[1], SAY_LEN))
        elif kind == "LEAVE" and seat >= 0 and seat != self.seat and not self.started:
            await self.say("Seat %d stands up from table %s." % (seat + 1, self.tid))
        elif kind == "CHT" and len(a) >= 2:
            if seat != self.seat:
                text = " ".join(a[1:])
                await (self.send(msg_b(self.nw_of(seat), text)) if self.game and seat >= 0 else
                       self.say("%s: %s" % (self.name_of(seat), text) if seat >= 0 else text))
        elif kind == "INT":
            await self.say(describe_integrity_event(" ".join([kind] + a)))
        elif kind == "UNINVITE" and len(a) >= 2 and not self.started:
            why = a[2] if len(a) > 2 else ""
            await self.say("%s %s." % (a[1], {"declined": "declined your invitation", "expired":
                                               "did not come in time"}.get(why, "is no longer invited")))
        elif kind == "START":
            await self.start()
        elif kind == "END":
            await self.end(a)
        elif self.game is None:
            return
        elif kind == "PLAY" and tiles and seat != self.seat:
            await self.other_played(seat, a, tiles)
        elif kind == "PLAY" and seat == self.seat:
            self.pending = None
        elif kind == "DRAW" and len(a) >= 2:
            await self.drew(seat)
        elif kind == "EXCH" and len(a) >= 2:
            await self.exchanged(seat)
        elif kind == "PASS" and seat != self.seat:
            await self.send(msg_k(seat), *([msg_o(seat, self.game.racks[seat], [])] if self.game.bag_letters() else []))
        elif kind == "TURN" and seat >= 0:
            self.my_turn = seat == self.seat
            await self.send(msg_i(self.nw_of(seat)))

    def name_of(self, seat):
        return self.names[seat] if 0 <= seat < len(self.names) and self.names[seat] != "-" else "seat %d" % (seat + 1)

    async def view(self):
        data, final = await self.req("VUE %s O" % self.tid)
        if not final.startswith("OK"):
            raise CoreError(err_text(final))
        v = parse_open_view(data)
        if self.seat is not None and self.seat not in v["racks"]:
            raise Desync("the table shows no racks (is it marked integrity=false?)")
        return v

    async def table(self):
        data, final = await self.req("LST table=%s clients" % self.tid)
        return next((t for t in parse_tables(data) if t["id"] == self.tid), None) if final.startswith("OK") else None

    async def start(self):
        """The table's game begins: Networdz's New Game, the players, the bag, the order and the first draws."""
        self.started, self.over = True, False
        log(self.sid, "table %s starts" % self.tid)
        t = await self.table()
        self.names = t["seated"] if t else []
        v = await self.view()
        n = len(v["racks"])
        bots = set()
        data, final = await self.req("VUE %s" % self.tid)
        for l in data:
            if l.startswith("BOT "):
                bots |= {int(x) for x in l.split()[1:] if x.isdigit()}
        players = []
        for s in range(n):
            kind = REMOTE if s == self.seat else COMPUTER if s in bots else PERSON
            if s != self.seat and await self.plays_networdz(t, s):
                kind = REMOTE
            players.append((kind, self.name_of(s), s))
        self.voice = ALL
        if n < 4:
            players.append((NONE, VOICE, n))
            self.voice = n
        await self.table_events()           # every move the view shows is in the backlog now
        racks = start_racks(v["racks"], self.backlog, self.seat)
        self.game = Tiles(racks, minus(TILESET, "".join(racks.values())), self.svc.rng)
        seat = [msg_x(self.seat)] if self.seat != self.nseat else []    # X with its own seat again: Networdz
        self.nseat = self.seat                                          # drops what comes with it
        await self.send(*seat, msg_e(), msg_f(players), msg_a(self.game.full_bag()), msg_r(range(n)))
        self.backlog.extendleft(reversed([lambda s=s: self.deal(s, racks[s]) for s in range(n)]))

    async def plays_networdz(self, t, seat):
        """Another seat's player plays with Networdz too (LST clients): Remote in F."""
        return "Networdz" in ((t or {}).get("clients") or {}).get(seat, "")

    async def deal(self, seat, letters, then=None):
        """A draw: Q for another seat; for the guest A with only its tiles, P, and its own Q (nw_Q)."""
        if not letters:
            if then:
                await then()
            return
        places = self.game.places_for(letters)
        if seat != self.seat:
            before, draws = self.game.draw(seat, places)
            await self.send(msg_p(seat), msg_q(seat, before, draws))
            if then:
                await then()
            return
        self.waiting = ("Q", places, then)
        self.wait_until = time.monotonic() + DRAW_SECS
        await self.send(msg_a(self.game.short_bag(places)), msg_p(self.nseat))

    async def drew(self, seat):
        """DRAW after a play: the tiles the seat's rack gained at the table."""
        v = await self.view()
        new = minus(v["racks"].get(seat, ""), self.game.rack_letters(seat))
        await self.deal(seat, new)

    async def other_played(self, seat, a, tiles):
        placed = [(int(tiles[i]), int(tiles[i + 1]), tiles[i + 2]) for i in range(0, len(tiles) - 2, 3)]
        recs = self.game.play(seat, placed)
        across = a[3] == "H" if len(a) > 3 else True
        await self.send(msg_j(seat, recs), msg_m(seat, TAKEN, across, a[4] if len(a) > 4 else ""))

    async def exchanged(self, seat):
        """EXCH: the tiles put back and the ones drawn, from the racks before and after."""
        v = await self.view()
        after = v["racks"].get(seat, "")
        if seat == self.seat:           # its discards came in O (guest_move took them off the rack)
            self.pending = None
            new = minus(after, self.game.rack_letters(seat))
            await self.deal(seat, new, then=self.exchange_done)
            return
        before = self.game.rack_letters(seat)
        kept = common(before, after)
        rack = list(self.game.racks[seat])
        put = self.game.put_back(seat, minus(before, kept))
        await self.send(msg_k(seat), msg_o(seat, rack, put))
        await self.deal(seat, minus(after, kept), then=self.exchange_done)

    async def exchange_done(self):
        self.game.back_in_bag()
        await self.send(msg_a(self.game.full_bag()))

    async def end(self, a):
        self.over = True
        log(self.sid, "table %s over: %s" % (self.tid, " ".join(a)))
        if self.game is not None:
            if a[:1] in (["win"], ["draw"]):
                await self.send(msg_end("S"))
                await self.say("Game over at table %s: %s." % (self.tid, self.result(a)))
            else:
                await self.send(msg_end("Y"))
                await self.say("Table %s is over: %s." % (self.tid, self.result(a)))
        self.game, self.my_turn, self.pending = None, False, None
        self.tid, self.seat, self.started = None, None, False

    def result(self, a):
        if a[:1] == ["win"] and len(a) >= 2 and a[1].isdigit():
            return "%s wins, %s" % (self.name_of(int(a[1])), a[2] if len(a) > 2 else "")
        if a[:1] == ["draw"]:
            return "a draw, %s" % (a[1] if len(a) > 1 else "")
        if a[:1] in (["resign"], ["timeout"]) and len(a) > 1 and a[1].isdigit():
            return "%s %s" % (self.name_of(int(a[1])), "resigned" if a[0] == "resign" else "ran out of time")
        return "the game was ended"

    async def out_of_step(self, why):
        log(self.sid, "out of step: %s" % why)
        if self.game is None:
            return
        self.game, self.waiting, self.my_turn, self.pending = None, None, False, None
        self.backlog.clear()
        await self.send(msg_end("Y"))
        await self.say(["Networdz and table %s no longer agree." % self.tid,
                        "Finish the game on the web or by telnet."])

    # ---- chat and commands ---------------------------------------------------------------------------------
    async def chat(self, text):
        self.last_typed = time.monotonic()
        cmd = chatcmd.parse(text)
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
                await self.say("Chat reaches the other players once you sit at a table.")
        except (CoreError, OSError, asyncio.TimeoutError) as e:
            log(self.sid, "command failed: %s" % e)
            await self.say("The server is not answering just now.")

    async def do_signin(self, cmd):
        if self.name:
            await self.say("You are signed in as %s." % self.name)
            return
        args = cmd.args
        if self.core.auth == "none":
            name, password = (args[0] if args else self.nw_name), None
        elif len(args) >= 2:
            name, password = args[0], " ".join(args[1:])
        elif len(args) == 1 and self.nw_name:
            name, password = self.nw_name, args[0]
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
            self.close()
            return
        self.name = self.core.name or name
        log(self.sid, "signed in as %s" % self.name)
        self.core.user_input()
        self.console = Console(self)
        await self.say(["Signed in as %s." % self.name, "/invite NAME or /invite bot, /open or /list."])

    async def do_quit(self, cmd):
        await self.say("Goodbye.")
        await self.send(msg_end("Z"))
        self.close()

    async def seated(self, tid, seat, opp, how):
        """The console seated me: this is the table Networdz plays, marked integrity=false (its program receives
        the bag and every rack)."""
        if self.tid and self.tid != tid and self.started and not self.over:
            await self.say("Table %s goes on at the parlor." % self.tid)
        self.tid, self.seat, self.cursor, self.started, self.over = tid, seat, 0, False, False
        self.game, self.waiting, self.pending, self.my_turn = None, None, None, False
        self.backlog.clear()
        _, final = await self.req("INT %s false %s" % (tid, REASON))
        if not final.startswith("OK"):
            log(self.sid, "INT %s: %s" % (tid, final))
        log(self.sid, "seated at table %s, seat %d (%s)" % (tid, seat, how))


class Console(chatcmd.Console):
    """The shared chat commands; /open N opens a table for N players."""

    def __init__(self, g):
        self.g = g
        super().__init__(g.say, g.req, lambda: g.name, COMMANDS, kinds=[GRID], label="Grid Words",
                         new="%s turn=%s" % (RULES, g.svc.turn), notes=NOTES,
                         handlers={"quit": g.do_quit, "signin": g.do_signin, "open": self.do_open},
                         seated=g.seated, current=lambda: g.tid if g.started and not g.over else None)

    async def do_open(self, cmd):
        n = cmd.args[0] if cmd.args else "2"
        if not (n.isdigit() and 2 <= int(n) <= 4):
            await self.say("Type /open, or /open 3 or /open 4 for more players.")
            return
        keep = self.new
        self.new = "%s players=%s" % (keep, n)
        try:
            tid = await self.new_table("public")
        finally:
            self.new = keep
        if tid is None:
            return
        await self.g.seated(tid, 0, None, "open")
        await self.say("Your table %s is open for %s players. /close closes it." % (tid, n))


# ---- the listener -----------------------------------------------------------------------------------------

class Service:
    def __init__(self, args):
        self.args = args
        self.core_addr = (args.core_host, args.core_port)
        self.poll, self.turn, self.chat_gap = args.poll, args.turn, args.chat_gap
        self.trace = args.trace
        self.rng = random.Random()
        self.gate = Gate(MAX_SESSIONS)
        self.gate.notice = b""

    async def handle(self, reader, writer):
        keepalive(writer)
        addr = peer_addr(writer)
        ticket = self.gate.admit(addr, writer)
        if ticket is None:
            writer.close()
            return
        g = Guest(self, reader, writer, ticket)
        log(g.sid, "connected from %s" % addr)
        try:
            await g.run()
        except Exception as e:
            print("networdz session error: %r" % e, file=sys.stderr, flush=True)


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
