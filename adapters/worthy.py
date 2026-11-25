"""Worthy Opponent extension: Reversi, Checkers and Five in a Row for Worthy Opponent 2.0 on DOS, by modem.

ParlorNet is not affiliated with Worthy Opponent's publisher. Worthy Opponent (Future Ideas Today, Inc., Lakewood,
Colorado; version 2.0 of January 1990) is a pack of twelve two-player games for the IBM PC, played between two
copies over Hayes modems: one player dials (F3, the phone book) and the other answers (F4). This extension is the
answering copy. The player dials the parlor from the phone book with an entry such as `ATDT parlor.example.org:6519`,
which a WiFi modem or DOSBox's emulated modem turns into a TCP connection: the program sends an entry that starts
with AT as it stands. After the modem's CONNECT the program speaks its link protocol on the plain byte stream. The
protocol was read from games between two copies in DOSBox through a logging relay.

The wire. Every message is a frame of nine bytes, 00 02 C a b a b S 00: C is a sequence number (1 to 15, then 1
again) times 16 plus a type, a and b two data bytes sent twice, S the sum of C, a, b, a, b modulo 256. The receiver
answers each good frame with one byte 06 (a repeat of the last frame's number is answered and dropped), and the
sender waits for it, sending the frame again after about a second. The types:

    1 Talk          the keys typed in the Talk window (F6), one or two a frame (00 for none); Enter is 0D
    2 game          the keys of a game and its events, one or two a frame: a character (20 Space), 80 plus the
                    scan code for the arrows and the keys around them (C8 up, D0 down, CB left, CD right, C7 Home,
                    C9 PgUp, CF End, D1 PgDn), 81 the end of a turn, 82 its start, 83 and 84 a pause (a dialog
                    open) and its end, 86 a turn started over (Backspace, then Y), 87 the turn's time ran out
    3 text          fields ending in 0D: on connecting each copy sends " Version 2.0 ", " Version 2.0 " and the
                    player's name (the dialing copy first); at a game's start the dialing copy sends "??????",
                    its name, a random seed, both clocks in seconds and its name again, and the other answers with
                    its name
    4 command       ET and XT (the Talk window opened and closed), EG and the game's number (entering a game:
                    the dialing copy picks it, the other accepts with the same), XG (leaving a game)

The keys go to the other copy translated to its board, which may be turned: each copy plays both sides of the
game from the keys and the seed, so nothing else crosses the wire. Who moves first comes from the seed: Turbo
Pascal's generator (RandSeed := seed; RandSeed := RandSeed * 134775813 + 1; the high word mod 100) under 50 gives the
first move to the answering copy, the parlor.

The session. The player's own copy calls, so it picks the games (F8) and sets the clocks (F7). On connecting the
extension answers the handshake with "ParlorNet" as its name and opens the Talk window from its side (ET), so the
F6 line lights up on the PC, and greets the player there. Its lines in the Talk window come as typed text.

Sign-in. Worthy Opponent sends its player's name and nothing secret, so the name is never enough: the player opens
Talk (F6) and types /signin PASSWORD, which signs in the name from Worthy Opponent's Name menu, or
/signin NAME PASSWORD. The name goes to the core alone and the password answers its credential question
(common.sign_in_lines). Under sign-in none, /signin alone signs in the program's name. The password shows in the
PC's own Talk window as typed; it goes to the server only.

The games. CornerStone is reversi/1 (standard, 8 by 8), Checkers is checkers/1 (american; tables made here have
no move limit, as the program has none) and Quinte is fiveinarow/1 (freestyle, 19 by 19: the program's board, and
an overline wins there). The other nine games (Backgammon among them: its dice come from the seed by the program's
own procedure, and the core rolls its own) are not played here. The player picks the game with F8, and the
extension enters it at once, which brings the seed: then the player knows who moves first and uses Talk to find an
opponent: /invite bot, /invite NAME or /open make a table with the player in the seat that moves first or second as
the seed says. /join and /accept take a seat that is already fixed: when it is the wrong one, the extension says
so, and the player leaves the game (F10) and picks it again for a new draw; the seat is then taken when the draw
fits. Moves the player makes before the opponent sits down wait for the game to start.

The extension keeps both boards as the program does: each player's cursor (it stays where it was, and moves to
the player's first piece in reading order when the square it was on is no longer theirs), the stones and pieces,
and whose turn it is. A move from the PC is played on the core when its Space completes it (a game's last move
has no 81 after it); a Checkers turn goes as its steps and END. The opponent's move from the core goes to the PC
as the keys that make it: 82, the cursor's way to the piece or square, Space (the steps of a Checkers move between
two Spaces), then 81 unless the move ends the game. A CornerStone player with no move sends 82 and 81 (the program
asks the player to press Enter); the extension does the same for its side.

The end. The program sees a game end on the board by itself. A resignation, the core's turn clock or a draw (the
Checkers draw offers, /draw) end the game in the Talk window, and the extension leaves the game (XG), as a copy
whose player quits does. Leaving a game from the PC (F10) while it runs resigns it, and so does hanging up. When
the program's own clock runs out it says so (87): the game is resigned on the core.

The modem. Some WiFi modems treat the line as telnet when they dial a host: they double every FF they send and
read FF as a telnet command. The program's frames hold FF as data or a sum, and the PC's very first frame always
ends in FF 00, so a telnet line shows itself at once by its doubled FF. The extension reads FF FF as one FF, takes
telnet commands out of the stream (answering a modem's DO and WILL for binary and suppress-go-ahead with yes,
anything else with no), reads CR NUL as CR on a telnet line until binary is agreed, and doubles its own FF once
the line is telnet. Its Talk text and names are split into frames with no FF and no CR followed by NUL wherever a
split can avoid them, so most of what it sends is the same on either kind of line.

Left out: watching, resuming a game (the program cannot), the other nine games, the program's tournament field
(??????), and the clock settings (the program's own clock runs on the PC; the core's turn clock runs as well).

    python3 -m adapters.worthy --core 127.0.0.1:6590 --key worthy.key --port 6519
"""
import argparse
import asyncio
import itertools
import re
import sys
import time

from . import botflag, chatcmd, declare
from .clients import BY_ID
from .common import (LOGIN_TOTAL_SECS, NEWPASS_TEXT, CoreClient, CoreError, Gate, describe_invite_event, err_text,
                     keepalive, no_wait, parse_invites, parse_tables, peer_addr, sign_in_lines)
from .games import checkers as CK
from .games import fiveinarow as F

CLIENT_NAME = BY_ID["worthy-opponent"].name        # "Worthy Opponent"
MACHINE = "IBM PC"
PROTOCOL = ("WORTHY", "Worthy Opponent, for DOS by modem",
            "Reversi, Checkers, Five in a Row for Worthy Opponent DOS")
HOST_NAME = "ParlorNet"
MAX_SESSIONS = 40
HELLO_SECS = 30.0           # the program's handshake after connecting
POLL_SECS = 1.0             # the table's events
INVITE_SECS = 3.0           # IVL, while signed in and not playing
LOBBY_SECS = 1800.0         # signed in, no game and no typing: the connection closes
RETRY_SECS = 1.5            # a frame with no 06 is sent again after this long
RETRIES = 12                # ... this many times, then the line is taken as gone
TALK_WIDTH = 50             # the Talk window's lines
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
ids = itertools.count(1)

# ---- the wire ---------------------------------------------------------------------------------------------

ACK = 0x06
TALK, KEYS, TEXT, CMD = 1, 2, 3, 4
TURN_END, TURN_START, PAUSE, RESUME, START_OVER, TIME_UP = 0x81, 0x82, 0x83, 0x84, 0x86, 0x87
SPACE, ENTER, BACKSPACE = 0x20, 0x0D, 0x08
UP, DOWN, LEFT, RIGHT, HOME, PGUP, END, PGDN = 0xC8, 0xD0, 0xCB, 0xCD, 0xC7, 0xC9, 0xCF, 0xD1
DIRECTION = {UP: (-1, 0), DOWN: (1, 0), LEFT: (0, -1), RIGHT: (0, 1),
             HOME: (-1, -1), PGUP: (-1, 1), END: (1, -1), PGDN: (1, 1)}
KEY_FOR = {v: k for k, v in DIRECTION.items()}
TURNED = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT, HOME: PGDN, PGDN: HOME, PGUP: END, END: PGUP}
VERSION = " Version 2.0 "

# The games menu, by the number EG carries.
MENU = {1: "SnakePit", 2: "CornerStone", 3: "Quinte", 4: "ArmyAnts", 5: "Checkers", 6: "Backgammon",
        7: "MissileStrike", 8: "LazerMaze", 9: "CannonBall", 10: "CaveIn", 11: "TreasureQuest", 12: "MiniatureGolf"}
CORNERSTONE, QUINTE, CHECKERS = 2, 3, 5


def frame(seq, kind, a, b):
    c = (seq << 4) | kind
    return bytes([0, 2, c, a, b, a, b, (c + 2 * a + 2 * b) & 0xFF, 0])


def clean_frame(fr):
    """A frame a telnet line carries unchanged: no FF (a telnet command) and no CR followed by NUL (which a
    telnet line may take as a bare CR)."""
    return 0xFF not in fr and b"\r\x00" not in fr


def next_seq(seq):
    return seq % 15 + 1


def split_text(data, seq, single):
    """data -> [(a, b)] pairs whose frames, numbered from seq, are all clean, or None. single: the pairs may also
    carry one byte and a 00 (Talk keys); else a pair is two bytes and an odd tail is padded with 00 (text)."""
    data = bytes(data)

    def go(i, s):
        if i >= len(data):
            return []
        options = []
        if i + 1 < len(data):
            options.append((data[i], data[i + 1], 2))
        if single or i + 1 == len(data):
            options.append((data[i], 0, 1))
        for a, b, n in options:
            if clean_frame(frame(s, TALK if single else TEXT, a, b)):
                rest = go(i + n, next_seq(s))
                if rest is not None:
                    return [(a, b)] + rest
        return None
    return go(0, seq)


def checksum_ok(fr):
    return (sum(fr[2:7]) & 0xFF) == fr[7] and fr[3:5] == fr[5:7] and fr[8] == 0


class Link:
    """The bytes from the PC -> ("ack",), ("frame", kind, a, b) for each new good frame, and the telnet answers
    to send back. Frames are answered with 06 by the caller; a repeated frame comes as ("again",)."""

    BINARY, SGA = 0, 3
    YES = (BINARY, SGA)

    def __init__(self):
        self.buf = bytearray()
        self.state = 0              # telnet: 0 data, 1 after FF, 2 the option of WILL/WONT/DO/DONT, 3 in SB
        self.verb = 0
        self.telnet = False         # the line spoke telnet
        self.binary = False         # ... and agreed to binary from its side (no CR NUL)
        self.last = None            # the last good frame's number
        self.replies = bytearray()

    def untelnet(self, data):
        out = bytearray()
        for c in data:
            if self.state == 0:
                if c == 0xFF:
                    self.state = 1
                else:
                    out.append(c)
            elif self.state == 1:
                if c == 0xFF:               # a doubled FF: only a telnet line sends it
                    out.append(0xFF)
                    self.state, self.telnet = 0, True
                elif 0xFB <= c <= 0xFE:
                    self.verb, self.state, self.telnet = c, 2, True
                elif c == 0xFA:
                    self.state, self.telnet = 3, True
                elif 0xF0 <= c <= 0xF9:
                    self.state, self.telnet = 0, True
                else:                   # no telnet command: a plain FF (a raw line) and this byte
                    out.append(0xFF)
                    self.state = 0
                    out.extend(self.untelnet([c]))
            elif self.state == 2:
                self.answer(self.verb, c)
                self.state = 0
            elif self.state == 3:
                if c == 0xFF:
                    self.state = 4
            elif self.state == 4:
                self.state = 0 if c == 0xF0 else 3
        return bytes(out)

    def answer(self, verb, opt):
        yes = opt in self.YES
        if verb == 0xFB:            # WILL
            if opt == self.BINARY:
                self.binary = True
            self.replies += bytes([0xFF, 0xFD if yes else 0xFE, opt])
        elif verb == 0xFD:          # DO
            self.replies += bytes([0xFF, 0xFB if yes else 0xFC, opt])
        elif verb == 0xFC and opt == self.BINARY:
            self.binary = False

    def feed(self, data):
        data = self.untelnet(data)
        if self.telnet and not self.binary:
            data = data.replace(b"\r\x00", b"\r")   # a telnet line's CR NUL (none in a raw frame's place)
        self.buf += data
        out = []
        while self.buf:
            if self.buf[0] == ACK:
                out.append(("ack",))
                del self.buf[0]
                continue
            if self.buf[0] != 0:
                del self.buf[0]
                continue
            if len(self.buf) < 2:
                break
            if self.buf[1] != 2:
                del self.buf[0]
                continue
            if len(self.buf) < 9:
                break
            fr = bytes(self.buf[:9])
            if not checksum_ok(fr):
                del self.buf[0]
                continue
            del self.buf[:9]
            seq, kind = fr[2] >> 4, fr[2] & 15
            if seq == self.last:
                out.append(("again",))
                continue
            self.last = seq
            out.append(("frame", kind, fr[3], fr[4]))
        return out

    def escape(self, data):
        """What goes to the PC: FF doubled once the line has shown it is telnet."""
        return data.replace(b"\xff", b"\xff\xff") if self.telnet else data

    def take_replies(self):
        r = bytes(self.replies)
        self.replies = bytearray()
        return r


def text_fields(fields):
    return "".join(f + "\r" for f in fields).encode("latin-1", "replace")


def first_mover(seed):
    """Who moves first, from the seed the dialing copy sends: "dos" (the caller) or "par" (the answerer).
    Turbo Pascal's RandSeed := seed, then Random(100) above 50 gives the caller the first move."""
    s = (int(seed) * 134775813 + 1) & 0xFFFFFFFF
    return "dos" if (s >> 16) % 100 > 50 else "par"


def program_claim(version):
    m = re.search(r"Version\s+([0-9][0-9.]{0,6})", version or "")
    return "%s %s" % (CLIENT_NAME, m.group(1)) if m else CLIENT_NAME


def plain(text):
    return re.sub(r"[^ -~]", "?", text)


def wrap(line, width=TALK_WIDTH):
    out, cur = [], ""
    for w in line.split(" "):
        while len(w) > width:
            if cur:
                out.append(cur)
                cur = ""
            out.append(w[:width])
            w = w[width:]
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    out.append(cur)
    return out


# ---- the boards ---------------------------------------------------------------------------------------------
#
# Each model keeps a game as a copy of the program does, in the parlor's own view (the board as the answering
# copy shows it): the keys from the PC come already turned to it, and the parlor's keys are turned to the PC's
# view (turn_key) on the way out. Sides are "dos" and "par".

def other(side):
    return "par" if side == "dos" else "dos"


def wrap_delta(a, b, n):
    d = (b - a) % n
    return d if d <= n // 2 else d - n


class Board:
    """What the three games share: cursors, the turn, the first player."""
    N = 8
    KEY = ""

    def __init__(self, first):
        self.first = first                  # the side that moves first: seat 0 at the core
        self.turn = first                   # whose turn the program counts
        self.started = {"dos": False, "par": False}     # 82 seen for the current turn
        self.over = False
        self.cursor = {}

    def seat(self, side):
        return 0 if side == self.first else 1

    def side_of_seat(self, seat):
        return self.first if seat == 0 else other(self.first)

    def move_cursor(self, side, key):
        dr, dc = DIRECTION[key]
        r, c = self.cursor[side]
        self.cursor[side] = ((r + dr) % self.N, (c + dc) % self.N)

    def turn_key(self, key):
        return key

    def path_keys(self, frm, to):
        """Keys in the parlor's view that take a cursor from frm to to, one square a key (diagonals first)."""
        dr, dc = wrap_delta(frm[0], to[0], self.N), wrap_delta(frm[1], to[1], self.N)
        keys = []
        while dr or dc:
            sr, sc = (dr > 0) - (dr < 0), (dc > 0) - (dc < 0)
            keys.append(KEY_FOR[(sr, sc)])
            dr, dc = dr - sr, dc - sc
        return keys


class Cornerstone(Board):
    """CornerStone: Reversi on 8 by 8. The first player's stones start on (3,3) and (4,4), rows and columns from
    the top left, the same on both screens; the core's reversi/1 has them mirrored left to right."""
    KEY = "reversi/1"
    DIRS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    def __init__(self, first):
        super().__init__(first)
        second = other(first)
        self.stones = {(3, 3): first, (4, 4): first, (3, 4): second, (4, 3): second}
        self.cursor = {first: (3, 3), second: (3, 4)}

    def flips(self, side, r, c):
        if (r, c) in self.stones:
            return []
        out = []
        for dr, dc in self.DIRS:
            rr, cc, run = r + dr, c + dc, []
            while 0 <= rr < 8 and 0 <= cc < 8 and self.stones.get((rr, cc)) == other(side):
                run.append((rr, cc))
                rr, cc = rr + dr, cc + dc
            if run and 0 <= rr < 8 and 0 <= cc < 8 and self.stones.get((rr, cc)) == side:
                out += run
        return out

    def legal(self, side):
        return [(r, c) for r in range(8) for c in range(8) if self.flips(side, r, c)]

    def start_turn(self, side):
        """82: the cursor comes back to the player's first stone if its square is not theirs."""
        self.started[side] = True
        if self.stones.get(self.cursor[side]) != side:
            own = sorted(p for p, s in self.stones.items() if s == side)
            if own:
                self.cursor[side] = own[0]

    def place(self, side, r, c):
        fl = self.flips(side, r, c)
        if not fl:
            return False
        for p in fl + [(r, c)]:
            self.stones[p] = side
        if not self.legal("dos") and not self.legal("par"):
            self.over = True
        return True

    def end_turn(self, side):
        self.started[side] = False
        self.turn = other(side)

    @staticmethod
    def square(r, c):
        """(row, column) on the program's board -> the core's square name."""
        return "%s%d" % (chr(ord("a") + 7 - c), r + 1)

    @staticmethod
    def from_square(tok):
        tok = tok.lower()
        if len(tok) < 2 or not "a" <= tok[0] <= "h" or not tok[1:].isdigit():
            return None
        x, y = ord(tok[0]) - 97, int(tok[1:]) - 1
        return (y, 7 - x) if 0 <= y < 8 else None

    def keys_for(self, square):
        """The parlor's move as keys in its own view: from its cursor to the square, then Space; the cursor
        stays there."""
        cur = self.cursor["par"]
        keys = self.path_keys(cur, square) + [SPACE]
        self.cursor["par"] = square
        return keys


class Quinte(Board):
    """Quinte: five in a row on 19 by 19 points, the same on both screens; both cursors start in the center."""
    KEY = "fiveinarow/1"
    N = 19

    def __init__(self, first):
        super().__init__(first)
        self.stones = {}
        self.cursor = {"dos": (9, 9), "par": (9, 9)}

    def start_turn(self, side):
        self.started[side] = True
        if self.stones.get(self.cursor[side]) != side:
            own = sorted(p for p, s in self.stones.items() if s == side)
            if own:
                self.cursor[side] = own[0]

    def five(self, side, r, c):
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            n = 1
            for sgn in (1, -1):
                rr, cc = r + sgn * dr, c + sgn * dc
                while 0 <= rr < 19 and 0 <= cc < 19 and self.stones.get((rr, cc)) == side:
                    n += 1
                    rr, cc = rr + sgn * dr, cc + sgn * dc
            if n >= 5:
                return True
        return False

    def place(self, side, r, c):
        if (r, c) in self.stones:
            return False
        self.stones[(r, c)] = side
        if self.five(side, r, c) or len(self.stones) == 19 * 19:
            self.over = True
        return True

    def end_turn(self, side):
        self.started[side] = False
        self.turn = other(side)

    @staticmethod
    def square(r, c):
        return F.sq(c, r)

    @staticmethod
    def from_square(tok):
        p = F.pt(tok)
        return (p[1], p[0]) if p and 0 <= p[0] < 19 and 0 <= p[1] < 19 else None

    def keys_for(self, square):
        cur = self.cursor["par"]
        keys = self.path_keys(cur, square) + [SPACE]
        self.cursor["par"] = square
        return keys


class Checkers(Board):
    """Checkers on the dark squares ((row + column) odd). Each player sees their own men at the bottom, so the PC's
    board is the parlor's turned half way round, and so are the keys. The arrows move the cursor two squares
    (along the dark squares of a row or column), the diagonal keys one; Space picks up a piece that can move
    (only one that can jump when a jump is possible) and puts it down; a diagonal key moves a picked-up piece a
    step or a jump. Both cursors start on the fourth square of the player's third row, (5,4) as each sees it.
    The core's checkers/1 board is the second player's view (the first player, Black, at the top)."""
    KEY = "checkers/1"

    def __init__(self, first):
        super().__init__(first)
        self.pieces = {}                    # (r, c) in the parlor's view -> (side, king)
        for r in range(8):
            for c in range(8):
                if (r + c) % 2 == 1:
                    if r < 3:
                        self.pieces[(r, c)] = ("dos", False)
                    elif r > 4:
                        self.pieces[(r, c)] = ("par", False)
        self.cursor = {"par": (5, 4), "dos": (2, 3)}
        self.carry = None                   # (side, square it was picked from)
        self.path = []                      # [(from, to, captured or None)] this turn
        self.crowned = False
        self.saved = None                   # the turn's start, for 86

    def turn_key(self, key):
        return TURNED.get(key, key)

    def forward(self, side):
        return 1 if side == "dos" else -1   # the PC's men come down the parlor's board

    def dirs(self, sq):
        side, king = self.pieces[sq]
        if king:
            return [(-1, -1), (-1, 1), (1, -1), (1, 1)]
        f = self.forward(side)
        return [(f, -1), (f, 1)]

    def jumps(self, sq):
        side = self.pieces[sq][0]
        out = []
        for dr, dc in self.dirs(sq):
            m, t = (sq[0] + dr, sq[1] + dc), (sq[0] + 2 * dr, sq[1] + 2 * dc)
            if 0 <= t[0] < 8 and 0 <= t[1] < 8 and self.pieces.get(m, ("",))[0] == other(side) and t not in self.pieces:
                out.append((t, m))
        return out

    def steps(self, sq):
        out = []
        for dr, dc in self.dirs(sq):
            t = (sq[0] + dr, sq[1] + dc)
            if 0 <= t[0] < 8 and 0 <= t[1] < 8 and t not in self.pieces:
                out.append(t)
        return out

    def must_jump(self, side):
        return any(self.jumps(sq) for sq, (s, _) in list(self.pieces.items()) if s == side)

    def can_move(self, side):
        return any(self.jumps(sq) or self.steps(sq) for sq, (s, _) in list(self.pieces.items()) if s == side)

    def reading_first(self, side):
        own = sorted(sq for sq, (s, _) in self.pieces.items() if s == side)
        if not own:
            return None
        return own[0] if side == "par" else own[-1]       # the PC reads its own turned board

    def start_turn(self, side):
        self.started[side] = True
        if self.pieces.get(self.cursor[side], ("",))[0] != side:
            first = self.reading_first(side)
            if first:
                self.cursor[side] = first
        self.saved = (dict(self.pieces), dict(self.cursor))
        self.carry, self.path, self.crowned = None, [], False

    def start_over(self, side):
        if self.saved:
            self.pieces, cursors = dict(self.saved[0]), self.saved[1]
            self.cursor[side] = cursors[side]
        self.carry, self.path, self.crowned = None, [], False

    def move_cursor(self, side, key):
        """The PC's arrows and diagonals: free when nothing is picked up; with a piece picked up a diagonal key
        moves it (a step, or a jump when one is possible) and the arrows do nothing."""
        dr, dc = DIRECTION[key]
        if self.carry is None:
            n = 1 if dr and dc else 2
            r, c = self.cursor[side]
            self.cursor[side] = ((r + n * dr) % 8, (c + n * dc) % 8)
            return
        if not (dr and dc) or self.crowned:
            return
        sq = self.cursor[side]
        jumped = any(p[2] for p in self.path)
        if self.path and not jumped:
            return                      # a step ends the move
        for t, m in self.jumps(sq):
            if (t[0] - sq[0], t[1] - sq[1]) == (2 * dr, 2 * dc):
                self.do_step(side, sq, t, m)
                return
        if not self.path and not self.must_jump(side):
            t = (sq[0] + dr, sq[1] + dc)
            if t in self.steps(sq):
                self.do_step(side, sq, t, None)

    def do_step(self, side, frm, to, captured):
        piece = self.pieces.pop(frm)
        if captured:
            del self.pieces[captured]
        far = 7 if side == "dos" else 0
        if not piece[1] and to[0] == far:
            piece = (side, True)
            self.crowned = True
        self.pieces[to] = piece
        self.cursor[side] = to
        self.path.append((frm, to, captured))

    def space(self, side):
        """Space: pick up, put back, or put down at the end of a move -> True when the move is complete."""
        sq = self.cursor[side]
        if self.carry is None:
            if self.pieces.get(sq, ("",))[0] != side:
                return False
            if self.must_jump(side) and not self.jumps(sq):
                return False
            if not self.jumps(sq) and not self.steps(sq):
                return False
            self.carry = (side, sq)
            return False
        if not self.path:
            self.carry = None
            return False
        last = self.path[-1]
        if last[2] and not self.crowned and self.jumps(sq):
            return False                # a jump that can go on must go on
        self.carry = None
        if not self.can_move(other(side)):
            self.over = True
        return True

    def end_turn(self, side):
        self.started[side] = False
        self.turn = other(side)
        self.carry, self.path, self.crowned = None, [], False

    def core_sq(self, rc):
        """A square in the parlor's view -> the core's number (the core's board is the second player's view)."""
        r, c = rc
        if self.first == "par":
            r, c = 7 - r, 7 - c
        return CK.sq(r, c)

    def view_sq(self, n):
        r, c = CK.rc(n)
        if self.first == "par":
            r, c = 7 - r, 7 - c
        return r, c

    def tokens(self):
        return ["%d%s%d" % (self.core_sq(f), "x" if cap else "-", self.core_sq(t)) for f, t, cap in self.path]

    def keys_for(self, steps):
        """The parlor's move, [(from, to)] in its view, as keys: the cursor's way to the piece (two squares an
        arrow, one a diagonal), Space, a diagonal key a step or jump, Space."""
        frm = steps[0][0]
        keys = self.cursor_keys(self.cursor["par"], frm) + [SPACE]
        self.carry = ("par", frm)
        self.cursor["par"] = frm
        for a, b in steps:
            dr, dc = (b[0] - a[0]) // abs(b[0] - a[0]), (b[1] - a[1]) // abs(b[1] - a[1])
            keys.append(KEY_FOR[(dr, dc)])
            captured = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2) if abs(b[0] - a[0]) == 2 else None
            self.do_step("par", a, b, captured)
        keys.append(SPACE)
        self.carry = None
        if not self.can_move("dos"):
            self.over = True
        return keys

    def cursor_keys(self, frm, to):
        dr, dc = wrap_delta(frm[0], to[0], 8), wrap_delta(frm[1], to[1], 8)
        keys = []
        while dr and dc:
            sr, sc = (dr > 0) - (dr < 0), (dc > 0) - (dc < 0)
            keys.append(KEY_FOR[(sr, sc)])
            dr, dc = dr - sr, dc - sc
        while dr:
            s = (dr > 0) - (dr < 0)
            keys.append(KEY_FOR[(s, 0)])
            dr -= 2 * s
        while dc:
            s = (dc > 0) - (dc < 0)
            keys.append(KEY_FOR[(0, s)])
            dc -= 2 * s
        return keys


MODELS = {CORNERSTONE: Cornerstone, QUINTE: Quinte, CHECKERS: Checkers}
GAME_TEXT = {CORNERSTONE: "CornerStone is Reversi here",
             QUINTE: "Quinte is Five in a Row here, 19 by 19: five or more wins",
             CHECKERS: "Checkers is American checkers here"}
NEW_OPTS = {CORNERSTONE: "standard", QUINTE: "freestyle size=19", CHECKERS: "american limit=0"}
TABLE_MARK = {CORNERSTONE: ("worthy", "cornerstone"), QUINTE: ("worthy", "quinte"), CHECKERS: ("worthy", "checkers")}
KEYS_OF = {CORNERSTONE: "reversi/1", QUINTE: "fiveinarow/1", CHECKERS: "checkers/1"}
LABEL = {CORNERSTONE: "Reversi", QUINTE: "Five in a Row", CHECKERS: "Checkers"}


def log(sid, text):
    print("worthy %d: %s" % (sid, text), file=sys.stderr, flush=True)


class Game:
    """One game entered on the PC: the menu number, then the seed (who moves first), then a table."""

    def __init__(self, number):
        self.number = number
        self.board = None
        self.seed = None
        self.tid = None
        self.seat = None
        self.opp = None
        self.started = False        # the core's START
        self.over = False
        self.waiting = []           # the PC's moves made before the core's game started: [(kind, value)]
        self.theirs = []            # the opponent's move as the core's events, until it ends
        self.offer = None           # a draw offer waiting (the seat that made it)
        self.left = False           # the parlor's copy has left the game (XG sent)



class Session:
    def __init__(self, svc, reader, writer, ticket=None):
        self.svc, self.r, self.w, self.ticket = svc, reader, writer, ticket
        self.sid = next(ids)
        self.addr = peer_addr(writer)
        self.link = Link()
        self.seq = 1                # the next frame's number
        self.out = asyncio.Queue()
        self.acked = asyncio.Event()
        self.core = None
        self.core_lock = asyncio.Lock()
        self.name = None
        self.nick = ""
        self.program = CLIENT_NAME
        self.console = None
        self.text = bytearray()     # type 3 bytes not yet read as fields
        self.cmd = bytearray()      # type 4 bytes not yet read as a command
        self.need = 3               # fields the next type 3 message has
        self.talk = ""              # the Talk line being typed
        self.pending = None         # a game picked before sign-in (its number)
        self.game = None            # the game entered on the PC
        self.join_later = None      # (tid, seat, opp, how): a seat that waits for a draw that fits
        self.closing = False
        self.t0 = self.last_typed = time.monotonic()
        self.announced = {}

    # ---- output -----------------------------------------------------------------------------------------
    def send_frame(self, kind, a, b):
        self.out.put_nowait(frame(self.seq, kind, a, b))
        self.seq = next_seq(self.seq)

    def send_keys(self, keys, turn=None):
        """Game keys (already in the PC's view), one a frame."""
        for k in keys:
            self.send_frame(KEYS, k, 0)

    def send_cmd(self, text):
        b = text.encode("latin-1")
        b += b"\r" * (len(b) % 2)
        for i in range(0, len(b), 2):
            self.send_frame(CMD, b[i], b[i + 1])

    def send_text(self, fields):
        """Type 3 fields; a name that would make an unclean frame gets a space added."""
        for extra in ("", " ", "  ", "   "):
            data = text_fields(fields[:-1] + [fields[-1] + extra])
            pairs = split_text(data, self.seq, single=False)
            if pairs is not None:
                for a, b in pairs:
                    self.send_frame(TEXT, a, b)
                return
        data = text_fields(fields) + b"\x00" * (len(text_fields(fields)) % 2)
        for i in range(0, len(data), 2):            # an FF sum: doubled on a telnet line (Link.escape)
            self.send_frame(TEXT, data[i], data[i + 1])

    def send_talk(self, text):
        data = "\r".join(plain(l) for l in text.split("\r")).encode("ascii")
        pairs = split_text(data, self.seq, single=True)
        if pairs is None:
            data = data.replace(b"\r", b" \r")
            pairs = split_text(data, self.seq, single=True) or []
        for a, b in pairs:
            self.send_frame(TALK, a, b)

    async def say(self, lines):
        """Lines in the PC's Talk window."""
        if isinstance(lines, str):
            lines = [lines]
        out = []
        for l in lines:
            if l is not None:
                out += wrap(l)
        if out:
            self.send_talk("".join(l + "\r" for l in out))

    async def sender(self):
        while True:
            fr = await self.out.get()
            for _ in range(RETRIES):
                self.acked.clear()
                self.w.write(self.link.escape(fr))
                await self.w.drain()
                try:
                    await asyncio.wait_for(self.acked.wait(), RETRY_SECS)
                    break
                except asyncio.TimeoutError:
                    continue
            else:
                log(self.sid, "no answer from the PC: hanging up")
                self.stop()
                return

    # ---- the core ---------------------------------------------------------------------------------------
    async def req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    # ---- the session ------------------------------------------------------------------------------------
    async def read_frames(self, timeout=None):
        read = self.r.read(4096)
        data = await (read if timeout is None else asyncio.wait_for(read, timeout))
        if not data:
            return None
        if self.ticket is not None and self.name is None:
            self.ticket.touch()
        events = self.link.feed(data)
        replies = self.link.take_replies()
        acks = bytes([ACK]) * sum(1 for e in events if e[0] in ("frame", "again"))
        if replies or acks:
            self.w.write(replies + acks)
        for e in events:
            if e[0] == "ack":
                self.acked.set()
        return [e for e in events if e[0] == "frame"]

    async def run(self):
        sender = asyncio.ensure_future(self.sender())
        try:
            end = time.monotonic() + HELLO_SECS
            while self.need == 3 and not self.nick and not self.closing:
                left = end - time.monotonic()
                if left <= 0:
                    log(self.sid, "no handshake")
                    return
                try:
                    got = await self.read_frames(left)
                except asyncio.TimeoutError:
                    log(self.sid, "no handshake")
                    return
                if got is None:
                    return
                for _, kind, a, b in got:
                    if kind == TEXT:
                        self.text += bytes([a, b])
                        fields = self.fields(3)
                        if fields:
                            self.program = program_claim(fields[0])
                            self.nick = fields[2].strip() or "?"
            if self.closing or not self.nick:
                return
            log(self.sid, "hello: %s, name %r" % (self.program, self.nick))
            self.send_text([VERSION, VERSION, HOST_NAME])
            try:
                self.core = await CoreClient.connect(*self.svc.core_addr)
                await self.core.source(self.addr)
                await self.core.claim(self.program, MACHINE)
            except (OSError, asyncio.TimeoutError, CoreError) as e:
                self.core = None
                log(self.sid, "the core is not answering: %s" % e)
                self.send_cmd("ET\r")
                await self.say("The game server is not answering. Try again soon.")
                await asyncio.sleep(5)
                return
            self.send_cmd("ET\r")
            await self.greet()
            poller = asyncio.ensure_future(self.poll())
            try:
                while not self.closing:
                    got = await self.read_frames()
                    if got is None:
                        break
                    for _, kind, a, b in got:
                        await self.on_frame(kind, a, b)
            finally:
                poller.cancel()
                await self.hang_up()
        finally:
            sender.cancel()

    def fields(self, n):
        """n complete type 3 fields, taken from what has come, or None."""
        data = bytes(self.text).replace(b"\x00", b"")
        parts = data.split(b"\r")
        if len(parts) <= n:
            return None
        self.text = bytearray(b"\r".join(parts[n:]))
        return [p.decode("latin-1") for p in parts[:n]]

    async def greet(self):
        motd = []
        try:
            motd = await self.core.motd()
        except CoreError:
            pass
        lines = ["Welcome to ParlorNet. CornerStone, Checkers and Quinte are played here."] + motd[:4]
        if self.core.auth == "none":
            lines.append("To sign in, type /signin" + ("" if NAME_RE.match(self.nick) else " NAME"))
        elif NAME_RE.match(self.nick):
            lines.append("To sign in as %s, type /signin and your password." % self.nick)
        else:
            lines.append("To sign in, type /signin NAME PASSWORD")
        lines.append("/help lists the commands.")
        await self.say(lines)

    async def hang_up(self):
        if self.core is None:
            return
        try:
            g = self.game
            if self.name and g and g.tid and g.started and not g.over:
                await self.req("RES %s" % g.tid)
                log(self.sid, "resigned table %s: the PC went away" % g.tid)
            elif self.name and g and g.tid and not g.started:
                await self.req("LVE %s" % g.tid)
            if self.name:
                await self.core.out()
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.core.close()

    def stop(self):
        self.closing = True
        try:
            self.w.close()
        except Exception:
            pass

    # ---- frames from the PC -----------------------------------------------------------------------------
    async def on_frame(self, kind, a, b):
        try:
            if kind == TALK:
                for c in (a, b):
                    if c:
                        await self.talk_key(c)
            elif kind == CMD:
                self.cmd += bytes([a, b])
                await self.command()
            elif kind == TEXT:
                self.text += bytes([a, b])
                await self.text_fields()
            elif kind == KEYS:
                for c in (a, b):
                    if c:
                        await self.game_key(c)
        except (CoreError, OSError, asyncio.TimeoutError) as e:
            log(self.sid, "frame: %s" % e)
            await self.say("The server is not answering just now.")

    async def command(self):
        data = bytes(self.cmd)
        if b"\r" not in data:
            return
        line, _, rest = data.partition(b"\r")
        self.cmd = bytearray(rest.lstrip(b"\r\x00"))
        verb, arg = line[:2], line[2:]
        if verb == b"EG" and arg:
            await self.enter(arg[0])
        elif verb == b"XG":
            await self.leave()
        # ET and XT: the player opened or closed Talk

    async def text_fields(self):
        g = self.game
        if g and g.board is None:
            f = self.fields(6)
            if f:
                await self.game_info(f)

    # ---- Talk -------------------------------------------------------------------------------------------
    async def talk_key(self, c):
        if c == ENTER:
            line, self.talk = self.talk.strip(), ""
            if line:
                await self.chat(line)
        elif c == BACKSPACE:
            self.talk = self.talk[:-1]
        elif 32 <= c < 127 and len(self.talk) < 200:
            self.talk += chr(c)

    async def chat(self, text):
        self.last_typed = time.monotonic()
        cmd = chatcmd.parse(text)
        if cmd is not None:
            log(self.sid, "command /%s" % cmd.name)
        if self.console is None:
            if cmd is None:
                await self.say("Sign in first: /signin and your password.")
            elif cmd.name == "signin":
                await self.do_signin(cmd)
            elif cmd.name == "help":
                await self.say(chatcmd.help_lines(("signin", "help", "motd", "quit")))
            elif cmd.name == "motd":
                await self.say((await self.core.motd()) or "There is no message of the day.")
            elif cmd.name == "quit":
                await self.do_quit(cmd)
            else:
                await self.say("Sign in first: /signin and your password.")
            return
        self.core.user_input()
        if cmd is not None and await self.console.handle(text):
            return
        g = self.game
        if g and g.tid:
            _, final = await self.req("CHT %s %s" % (g.tid, plain(text)[:56]))
            if not final.startswith("OK"):
                await self.say("Your chat did not go: %s" % err_text(final))
        else:
            await self.say("Chat reaches your opponent once you have a table. /help for the commands.")

    async def do_signin(self, cmd):
        if self.name:
            await self.say("You are signed in as %s." % self.name)
            return
        args = cmd.args
        if self.core.auth == "none":
            name, password = (args[0] if args else self.nick), None
        elif len(args) >= 2:
            name, password = args[0], " ".join(args[1:])
        elif len(args) == 1:
            name, password = self.nick, args[0]
        else:
            await self.say("Type /signin and your password.")
            return
        if not NAME_RE.match(name or ""):
            await self.say("%s is not a ParlorNet name: type /signin NAME PASSWORD" % name if name
                           else "Type /signin NAME PASSWORD")
            return
        shown = []

        async def keep(lines):
            shown.extend(lines)
        why = await sign_in_lines(self.core, name, password, None, keep, "signin",
                                  "There is no account %s here. Make one on the web site first." % name)
        if why == NEWPASS_TEXT:
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
            self.stop()
            return
        self.name = self.core.name or name
        log(self.sid, "signed in as %s" % self.name)
        self.core.user_input()
        self.console = Console(self)
        await self.say(["Signed in as %s." % self.name,
                        "Pick CornerStone, Checkers or Quinte with F8, then /invite bot plays the bot."])
        await self.check_invitations()
        if self.pending:
            number, self.pending = self.pending, None
            await self.enter(number)

    async def do_quit(self, cmd):
        await self.say("Goodbye.")
        await asyncio.sleep(2)
        self.stop()

    # ---- entering and leaving a game --------------------------------------------------------------------
    async def enter(self, number):
        """EG from the PC: the player picked a game. Enter it at once (the seed comes back) when it is played
        here and the player is signed in."""
        name = MENU.get(number, "that game")
        if number not in MODELS:
            await self.say("%s is not played here. Press F10 and pick CornerStone, Checkers or Quinte." % name)
            return
        if self.console is None:
            self.pending = number
            await self.say("Sign in first (/signin and your password); then %s starts." % name)
            return
        if self.game and self.game.tid and self.game.started and not self.game.over:
            await self.say("Your game at table %s is still on: /resign it first." % self.game.tid)
            return
        self.game = Game(number)
        self.need = 6
        self.text = bytearray()
        self.send_cmd("EG%s\r" % chr(number))
        log(self.sid, "entering %s" % name)

    async def game_info(self, f):
        """The dialing copy's start of a game: the seed says who moves first."""
        g = self.game
        try:
            g.seed = int(f[2].strip())
        except ValueError:
            log(self.sid, "a seed that is no number: %r" % f[2])
            return
        g.board = MODELS[g.number](first_mover(g.seed))
        g.seat = g.board.seat("dos")
        opp = self.join_later[2] if self.join_later else None
        self.send_text([(opp or HOST_NAME)[:16]])
        mover = "you move first" if g.board.first == "dos" else "your opponent moves first"
        log(self.sid, "game %s, seed %d: %s" % (MENU[g.number], g.seed, mover))
        lines = ["%s; %s." % (GAME_TEXT[g.number], mover)]
        if self.join_later:
            tid, seat, opp, how = self.join_later
            if seat == g.seat:
                self.join_later = None
                got = await self.console.sit(tid, seat, opp, how)
                if got is not None:
                    lines.append(await self.console.sat_with(tid, opp))
                await self.say(lines)
                return
            lines.append("At table %s you would move %s: press F10 and pick the game again." % (
                tid, "first" if seat == 0 else "second"))
        else:
            lines.append("/invite bot plays the bot; /invite NAME, /open, /join, /accept.")
        await self.say(lines)

    async def leave(self):
        """XG from the PC: the player left the game. A game still on is resigned."""
        g = self.game
        if not g:
            return
        if g.tid and g.started and not g.over:
            _, final = await self.req("RES %s" % g.tid)
            log(self.sid, "left the game: resigned table %s (%s)" % (g.tid, final))
            g.over = True
            await self.say("You resigned table %s." % g.tid)
        elif g.tid and not g.started:
            await self.req("LVE %s" % g.tid)
            await self.say("You left table %s." % g.tid)
        if g.board is not None and not g.left:
            self.send_cmd("XG\r")
        self.game = None
        self.need = 3
        self.text = bytearray()

    async def end_on_pc(self, lines):
        """A game that ends off the board: the reason in Talk, then the parlor's copy leaves the game."""
        g = self.game
        await self.say(lines)
        if g and g.board is not None:
            g.left = True
            self.send_cmd("XG\r")

    # ---- the PC's keys ------------------------------------------------------------------------------------
    async def game_key(self, k):
        g = self.game
        if not g or g.board is None or g.board.over:
            return
        bd = g.board
        if k == TIME_UP:
            await self.say("Your time ran out on Worthy Opponent's clock.")
            if g.tid and g.started and not g.over:
                await self.req("RES %s" % g.tid)
                g.over = True
            return
        if k in (PAUSE, RESUME):
            return
        if bd.turn != "dos":
            log(self.sid, "a key out of turn: %02x" % k)
            return
        if k == TURN_START:
            bd.start_turn("dos")
            return
        if not bd.started["dos"]:
            return
        if k == TURN_END:
            bd.end_turn("dos")
            await self.parlor_turn()
            return
        if k == START_OVER and isinstance(bd, Checkers):
            bd.start_over("dos")
            return
        if k in DIRECTION:
            bd.move_cursor("dos", k)
            return
        if k == SPACE:
            if isinstance(bd, Checkers):
                if bd.space("dos"):
                    await self.play(("steps", bd.tokens()))
                return
            r, c = bd.cursor["dos"]
            if bd.place("dos", r, c):
                await self.play(("square", bd.square(r, c)))

    async def play(self, move):
        g = self.game
        if not (g.tid and g.started):
            g.waiting.append(move)
            if len(g.waiting) == 1:
                await self.say("Your move waits until your opponent sits down." if not g.tid
                               else "Your move waits for the game to start.")
            return
        await self.send_move(move)

    async def send_move(self, move):
        g = self.game
        kind, value = move
        if kind == "square":
            _, final = await self.req("MOV %s %s" % (g.tid, value))
        else:
            _, final = await self.req("MOV %s %s" % (g.tid, " ".join(value)))
            if final.startswith("OK") and "done" in final:
                _, final = await self.req("END %s" % g.tid)
        log(self.sid, "from the PC: %s (%s)" % (value, final))
        if not final.startswith("OK"):
            g.over = True
            await self.end_on_pc(["The server did not take your move: %s." % err_text(final),
                                  "This game goes on at table %s on the web or by telnet." % g.tid])

    async def parlor_turn(self):
        """The parlor's side of the program's turn: play the opponent's move when the core has it, or pass."""
        g = self.game
        bd = g.board if g else None
        if not bd or bd.over or bd.turn != "par":
            return
        if isinstance(bd, Cornerstone) and not bd.legal("par"):
            self.send_keys([TURN_START, TURN_END])
            bd.started["par"] = False
            bd.turn = "dos"
            return
        if g.theirs:
            await self.their_move()

    async def their_move(self):
        """The opponent's move (from the core's events in g.theirs) to the PC, as keys."""
        g = self.game
        bd = g.board
        if bd.turn != "par" or bd.over:
            return
        if isinstance(bd, Checkers):
            moves, g.theirs = g.theirs, []
        else:
            moves, g.theirs = g.theirs[:1], g.theirs[1:]
        bd.start_turn("par")
        keys = [TURN_START]
        if isinstance(bd, Checkers):
            steps = [(bd.view_sq(a), bd.view_sq(b)) for a, b in moves]
            keys += bd.keys_for(steps)
        else:
            sq = bd.from_square(moves[0])
            if sq is None:
                return
            keys += bd.keys_for(sq)
            bd.place("par", *sq)
        if not bd.over:
            keys.append(TURN_END)
            bd.end_turn("par")
        else:
            bd.started["par"] = False
        self.send_keys([bd.turn_key(k) for k in keys])

    # ---- the core's events --------------------------------------------------------------------------------
    async def poll(self):
        last_ivl = 0.0
        cursor = {}
        while not self.closing:
            await asyncio.sleep(self.svc.poll)
            try:
                now = time.monotonic()
                if self.name is None:
                    if now - self.t0 > LOGIN_TOTAL_SECS:
                        log(self.sid, "signing in took too long")
                        await self.say("Signing in took too long. Call again to try again.")
                        await asyncio.sleep(2)
                        self.stop()
                    continue
                g = self.game
                if g and g.tid:
                    await self.table_events(g, cursor)
                if not (g and g.started and not g.over) and now - self.last_typed > LOBBY_SECS:
                    await self.say("Nothing happened for a long time; goodbye.")
                    await asyncio.sleep(2)
                    self.stop()
                if not (g and g.started) and now - last_ivl >= INVITE_SECS:
                    last_ivl = now
                    await self.check_invitations()
            except (CoreError, OSError, asyncio.TimeoutError) as e:
                log(self.sid, "poll: %s" % e)
                self.stop()
            except Exception as e:
                log(self.sid, "poll error: %r" % e)
                import traceback
                traceback.print_exc()
                self.stop()

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
            game = next((n for n, k in KEYS_OF.items() if k == i["key"]), None)
            if game is None:
                await self.say("%s invites you to a game Worthy Opponent does not play here." % i["from"])
            else:
                await self.say("%s invites you to %s (%s): pick it with F8, then /accept %s" % (
                    i["from"], LABEL[game], MENU[game], i["from"]))

    async def table_events(self, g, cursor):
        tid = g.tid
        data, final = await self.req("SIN %s %d" % (tid, cursor.get(tid, 0)))
        if final.startswith("ERR"):
            return
        says = chatcmd.join_says(data)      # what the opponent who sat down plays with
        for l in data:
            p = l.split(" ", 4)
            if len(p) < 4 or p[0] != "EVT" or not p[2].isdigit() or int(p[2]) <= cursor.get(tid, 0):
                continue
            cursor[tid] = int(p[2])
            if self.game is not g:
                return
            await self.event(g, p[3], p[4] if len(p) > 4 else "", says)

    async def event(self, g, kind, rest, says=None):
        a = rest.split()
        oseat = 1 - g.seat
        change = chatcmd.change_of(kind, a, says or {}, g.seat)
        if kind == "JOIN" and len(a) >= 2 and a[0] == str(oseat):
            g.opp = a[1]
            if not g.started:
                await self.say(chatcmd.joined(a[1], (says or {}).get(oseat, "")))
        elif change and change[0] == oseat:     # the opponent plays on with another program
            await self.say(chatcmd.client_change(g.opp or "Your opponent", change[1]))
        elif kind == "LEAVE" and a[:1] == [str(oseat)] and not g.started:
            await self.say("%s stood up." % (g.opp or "Your opponent"))
        elif kind in ("INVITE", "UNINVITE") and not g.started:
            text = describe_invite_event(" ".join([kind] + a))
            if text and kind == "UNINVITE":
                await self.say(text[0].upper() + text[1:] + ".")
        elif kind == "START" and not g.started:
            g.started = True
            names = await self.names(g.tid)
            g.opp = names[oseat] if len(names) > oseat else g.opp or "your opponent"
            await self.say("Your game with %s on table %s starts: %s." % (
                g.opp, g.tid, "you move first" if g.board.first == "dos" else "%s moves first" % g.opp))
            log(self.sid, "game on table %s with %s, seat %d" % (g.tid, g.opp, g.seat))
            waiting, g.waiting = g.waiting, []
            for m in waiting:
                await self.send_move(m)
        elif kind == "CHT" and len(a) >= 2 and a[0] == str(oseat):
            said = rest.split(" ", 1)[1][:200]
            await self.say("%s: %s" % (g.opp or "Opponent", said))
        elif kind == "PUT" and len(a) >= 2 and a[0] == str(oseat):
            g.theirs.append(a[1])
            await self.parlor_turn()
        elif kind in ("STEP", "JUMP") and len(a) >= 3 and a[0] == str(oseat):
            g.theirs.append((int(a[1]), int(a[2])))
        elif kind == "UNDO" and len(a) >= 3 and a[0] == str(oseat) and g.theirs:
            g.theirs.pop()
        elif kind == "TURN" and isinstance(g.board, Checkers) and a[:1] == [str(g.seat)] and g.theirs:
            await self.parlor_turn()
        elif kind == "DRAW" and a[:1] == [str(oseat)]:
            g.offer = oseat
            await self.say("%s offers a draw: /draw accepts it, /nodraw declines it." % (g.opp or "Your opponent"))
        elif kind == "REJECT" and a[:1] == [str(oseat)]:
            await self.say("%s declines the draw." % (g.opp or "Your opponent"))
        elif kind == "END":
            if isinstance(g.board, Checkers) and g.theirs:
                await self.parlor_turn()
            await self.ended(g, a)

    async def names(self, tid):
        data, final = await self.req("LST table=%s" % tid)
        for t in parse_tables(data) if final.startswith("OK") else []:
            if t["id"] == tid:
                return t["seated"]
        return []

    async def ended(self, g, a):
        if g.over and not g.board.over:
            return
        g.over = True
        why = a[0] if a else ""
        seat = int(a[1]) if len(a) > 1 and a[1].isdigit() else -1
        log(self.sid, "table %s over: %s" % (g.tid, " ".join(a)))
        opp = g.opp or "your opponent"
        if why == "win" and g.board.over:
            await self.say("You win." if seat == g.seat else "%s wins." % opp)
            return
        if why == "draw" and g.board.over:
            await self.say("A draw.")
            return
        if why == "resign":
            text = "You resigned." if seat == g.seat else "%s resigned: you win." % opp
        elif why == "timeout":
            text = "Your time at the server ran out: %s wins." % opp if seat == g.seat else \
                "%s's time ran out: you win." % opp
        elif why == "draw":
            text = "The game is a draw (%s)." % (" ".join(a[1:]) or "agreed")
        elif why == "win":
            text = "You win." if seat == g.seat else "%s wins." % opp
        else:
            text = "The game is over."
        await self.end_on_pc([text, "Press F10, then F8 for another game."])


class Console(chatcmd.Console):
    """The shared chat commands, on the three kinds of table the program plays. /invite and /open make a table
    of the game entered on the PC, with the PC in the seat its draw gave; /join and /accept take a seat only
    when it is that one."""

    def __init__(self, s):
        self.s = s
        super().__init__(s.say, s.req, lambda: s.name,
                         ["list", "resign", "invite", "open", "join", "close", "accept", "decline", "who", "tables",
                          "top", "score", "note", "motd", "time", "signin", "quit", "help"],
                         kinds=list(KEYS_OF.values()), label="", new="standard",
                         notes=["Pick the game on the PC first (F8): CornerStone, Checkers or Quinte.",
                                "/draw offers or accepts a draw in Checkers, /nodraw declines."],
                         handlers={"quit": s.do_quit, "signin": s.do_signin, "draw": self.do_draw,
                                   "nodraw": self.do_nodraw},
                         seated=self.on_seated, width=TALK_WIDTH,
                         current=lambda: s.game.tid if s.game and s.game.started and not s.game.over else None)

    def ready(self):
        g = self.s.game
        return g is not None and g.board is not None

    async def handle(self, text):
        cmd = chatcmd.parse(text)
        if cmd is not None and cmd.name in ("open", "invite", "join", "accept"):
            g = self.s.game
            if not self.ready():
                await self.say("Pick the game on the PC first: F8, then CornerStone, Checkers or Quinte.")
                return True
            if g.tid:
                await self.say("You have a table already (%s)." % g.tid)
                return True
            self.kinds = [KEYS_OF[g.number]]
            try:
                return await super().handle(text)
            finally:
                self.kinds = list(KEYS_OF.values())
        return await super().handle(text)

    async def on_seated(self, tid, seat, opp, how):
        g = self.s.game
        g.tid, g.opp = tid, opp
        log(self.s.sid, "seated at table %s, seat %d (%s)" % (tid, seat, how))

    async def free_seat(self, tid):
        data, final = await self.req("LST table=%s" % tid)
        for t in parse_tables(data) if final.startswith("OK") else []:
            if t["id"] == tid:
                return next((i for i, n in enumerate(t["seated"]) if n == "-"), None)
        return None

    async def sit(self, tid, seat, opp, how):
        """Only in the seat the PC's draw gave: else the seat waits for a new draw (join_later)."""
        g = self.s.game
        want = g.seat
        if seat is None:
            seat = await self.free_seat(tid)
        if seat is not None and seat != want:
            self.s.join_later = (tid, seat, opp, how)
            await self.say("Worthy Opponent gave %s the first move, but at table %s you move %s. Press F10, "
                           "then pick the game again with F8: the seat is taken when the draw fits." % (
                               "you" if want == 0 else "your opponent", tid, "first" if seat == 0 else "second"))
            return None
        return await super().sit(tid, seat, opp, how)

    async def new_table(self, vis):
        g = self.s.game
        _, final = await self.req("NEW %s %s turn=%s%s" % (KEYS_OF[g.number], NEW_OPTS[g.number], self.s.svc.turn,
                                                           " vis=private" if vis == "private" else ""))
        if not final.startswith("OK"):
            await self.say("Can't open a table: %s" % err_text(final))
            return None
        tid = final.split()[1]
        await self.req("TDS %s %s %s" % ((tid,) + TABLE_MARK[g.number]))
        _, final = await self.req("JON %s %d" % (tid, g.seat))
        if not final.startswith("OK"):
            await self.req("LVE %s" % tid)
            await self.say("Can't open a table: %s" % err_text(final))
            return None
        return tid

    async def do_open(self, cmd):
        tid = await self.new_table("public")
        if tid is None:
            return
        await self.on_seated(tid, self.s.game.seat, None, "open")
        await self.say("Your table %s is open: anyone can join it. /close closes it." % tid)

    async def do_invite(self, cmd):
        """As the shared /invite, with the PC's seat from its draw and the guest in the other."""
        name = cmd.args[0]
        if name.lower() == self.me.lower():
            await self.say("You cannot invite yourself.")
            return
        theirs = next((i for i in await self.invitations()
                       if i["from"].lower() == name.lower() and i["key"] in self.kinds), None)
        if theirs is not None:
            await self.do_accept(chatcmd.Command("accept", [name], cmd.text))
            return
        waiting = self.waiting_table(await self.tables(), name=name)
        if waiting is not None:
            if await self.sit(waiting["id"], None, waiting["names"][0], "invite") is not None:
                host = waiting["names"][0]
                say = waiting.get("clients", {}).get(waiting["seat_of"].get(host, -1), "")
                await self.say("You sat down at %s's table %s.%s" % (host, waiting["id"],
                                                                     " %s plays %s." % (host, say) if say else ""))
            return
        tid = await self.new_table("private")
        if tid is None:
            return
        _, final = await self.req("INV %s %s %d" % (tid, name, 1 - self.s.game.seat))
        if not final.startswith("OK"):
            await self.req("LVE %s" % tid)
            await self.say("There is no player called %s here." % name if final.startswith("ERR NOPLAYER")
                           else "Can't invite %s: %s" % (name, err_text(final)))
            return
        await self.on_seated(tid, self.s.game.seat, name, "invite")
        if final == "OK offline":
            await self.say("%s is not online now. Your invitation waits at table %s." % (name, tid))
        elif botflag.is_bot_name(name):
            await self.say("You invited the bot to table %s. It will accept in a few seconds." % tid)
        else:
            await self.say("You invited %s to table %s." % (name, tid))

    async def do_draw(self, cmd):
        g = self.s.game
        if not (g and g.started and not g.over and g.number == CHECKERS):
            await self.say("A draw is offered in a Checkers game.")
            return
        word = "accept" if g.offer is not None else "draw"
        _, final = await self.req("MOV %s %s" % (g.tid, word))
        g.offer = None
        await self.say(("You offered a draw." if word == "draw" else "You accepted the draw.")
                       if final.startswith("OK") else "Can't: %s" % err_text(final))

    async def do_nodraw(self, cmd):
        g = self.s.game
        if not (g and g.offer is not None):
            await self.say("There is no draw offer.")
            return
        _, final = await self.req("MOV %s reject" % g.tid)
        g.offer = None
        await self.say("You declined the draw." if final.startswith("OK") else "Can't: %s" % err_text(final))


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
            print("worthy session error: %r" % e, file=sys.stderr, flush=True)
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
    print("worthy adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    global LOGIN_TOTAL_SECS
    ap = argparse.ArgumentParser(prog="adapters.worthy")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6519)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    ap.add_argument("--turn", default="1h", help="time per turn at the tables this extension makes")
    declare.add_args(ap)
    ap.add_argument("--poll", type=float, default=POLL_SECS, help=argparse.SUPPRESS)              # tests
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
