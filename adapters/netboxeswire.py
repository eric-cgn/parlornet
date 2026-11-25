"""The messages of NetBoxes 1.1 (Will Helling, 2000), Dots and Boxes for PowerPC Macs, inside NetSprocket's.
No sockets here: bytes in, bytes out, so the game's session
(adapters/nsp_netboxes.py) and the tests share one reading of the format. NetSprocket's own messages are in
adapters/nspwire.py; this module offers them too.

Each NetBoxes message is one NetSprocket message whose what is the kind below, with a short body of its own.
All are sent registered; most go to every player (to = 0), which in NetSprocket means through the host.

    0x101 VERSION_ASK   joiner -> host, right after the approval; 4 spare bytes
    0x100 VERSION       host -> joiner: the joiner's slot, its face, a game in progress (1), joiners may start
                        and abort games (1), the version (16 bits, 110 for 1.1), 2 spare bytes
    0x200 HELLO         a player to all, after VERSION: its name (Str31), slot, face, 2 spare bytes
    0x201 HELLO_BACK    each other player's answer to HELLO, to that player: the same fields
    0x400 CHAT          a chat line to all: the text (Str255), the sender's name (Str31), 4 bytes
    0x401 MAY_START     host -> all: joiners may start games (1) or not (0)
    0x800 START         a new game: dots across, dots down, seconds per turn, the index of the player who
                        moves first, then the options and the mines (405 bytes, zero for a plain game), and
                        3 spare bytes
    0x801 PEG           the player to move picked a dot: x, y (from the top left, from 0), 2 spare bytes
    0x802 LINE          ... and drew a line from it: the direction (0 up, 1 right, 2 left, 3 down)
    0x803 PASS          ... and closed no box: the turn goes to the next player still in the game
    0x804 ABORT         the game is abandoned; no body
    0x805 FACE          a player changed face: the face, the player's slot
    0x806 POINTS        points added to a player's total: a signed 16-bit number, the player's index
    0x807 STEP_OUT      a player left the game being played (not the network game): the slot
    0x808 MINE          a mine came back at a dot: x, y

Players have slots 0-3, given by the host (VERSION); the turn goes round the players of a game in slot order.
A face is one of twelve icons (0-11), each held by one player.
"""
import struct

from .nspwire import *  # noqa: F401,F403  (NetSprocket's own messages, under the same name)
from .nspwire import WireError, pstr, read_pstr

VERSION = 110           # NetBoxes 1.1
PLAYER_TYPE = 0         # the player type NetBoxes joins and hosts with

VERSION_ASK = 0x101
VERSION_REPLY = 0x100
HELLO = 0x200
HELLO_BACK = 0x201
CHAT = 0x400
MAY_START = 0x401
START = 0x800
PEG = 0x801
LINE = 0x802
PASS = 0x803
ABORT = 0x804
FACE = 0x805
POINTS = 0x806
STEP_OUT = 0x807
MINE = 0x808

KINDS = {VERSION_ASK: "version?", VERSION_REPLY: "version", HELLO: "hello", HELLO_BACK: "hello back", CHAT: "chat",
         MAY_START: "may start", START: "start", PEG: "peg", LINE: "line", PASS: "pass", ABORT: "abort",
         FACE: "face", POINTS: "points", STEP_OUT: "step out", MINE: "mine"}

FACES = 12
MAX_SECS = 255          # seconds per turn travel in one byte
BOARD_LEN = 405         # the options (5 bytes) and the mines (20 by 20) in START

# directions from a dot: 0 up, 1 right, 2 left, 3 down
STEPS = {0: (0, -1), 1: (1, 0), 2: (-1, 0), 3: (0, 1)}


def _small(*values, size=4):
    """A body of a few bytes, then zeros (the spare bytes NetBoxes leaves as they were in its memory)."""
    return bytes(values) + bytes(size - len(values))


def version_ask():
    return bytes(4)


def version_reply(slot, face, in_progress=False, may_start=False, version=VERSION):
    return bytes([slot, face, 1 if in_progress else 0, 1 if may_start else 0]) + struct.pack(">H", version) + bytes(2)


def parse_version_reply(body):
    """-> (slot, face, in progress, joiners may start, version)."""
    if len(body) < 6:
        raise WireError("short version")
    return body[0], body[1], bool(body[2]), bool(body[3]), struct.unpack_from(">H", body, 4)[0]


NAME_SHOWN = 10         # the game window shows this many characters of every name, whatever its length


def hello(name, slot, face):
    """HELLO and HELLO_BACK: the player's name, slot and face. A name shorter than NAME_SHOWN is made that long
    with zero bytes, which draw as nothing: the game window would otherwise show what an earlier, longer name
    left behind."""
    b = name.encode("mac_roman", "replace")[:31]
    b += bytes(max(NAME_SHOWN - len(b), 0))
    return bytes([len(b)]) + b + bytes(31 - len(b)) + bytes([slot, face]) + bytes(2)


def parse_hello(body):
    """-> (name, slot, face)."""
    if len(body) < 34:
        raise WireError("short hello")
    return read_pstr(body[:32]).rstrip("\0"), body[32], body[33]


def chat(text, name):
    """A chat line: the text and the sender's name, as the Mac shows them ("name: text")."""
    return pstr(text, 256) + pstr(name) + bytes(4)


def parse_chat(body):
    """-> (text, name)."""
    if len(body) < 288:
        raise WireError("short chat")
    return read_pstr(body[:256]), read_pstr(body[256:288])


def may_start(yes):
    return _small(1 if yes else 0)


def start(cols, rows, secs, first):
    """A plain game (no mines, no random lines): cols and rows count dots, first is the index of the player
    to move first among the game's players in slot order."""
    return bytes([cols, rows, min(max(secs, 0), MAX_SECS), first]) + bytes(BOARD_LEN) + bytes(3)


def parse_start(body):
    """-> (cols, rows, seconds per turn, first, mines on)."""
    if len(body) < 4 + BOARD_LEN:
        raise WireError("short start")
    return body[0], body[1], body[2], body[3], bool(body[4])


def peg(x, y):
    return _small(x, y)


def parse_peg(body):
    if len(body) < 2:
        raise WireError("short peg")
    return body[0], body[1]


def line(direction):
    return _small(direction)


def parse_line(body):
    if len(body) < 1 or body[0] not in STEPS:
        raise WireError("bad line")
    return body[0]


def pass_turn():
    return _small(0)


def abort():
    return b""


def face(face_, slot):
    return _small(face_, slot)


def points(delta, index):
    return struct.pack(">hB", delta, index) + bytes(1)


def parse_points(body):
    """-> (points, player index)."""
    if len(body) < 3:
        raise WireError("short points")
    delta, index = struct.unpack_from(">hB", body)
    return delta, index


def step_out(slot):
    return _small(slot)


# ---- lines and dots -------------------------------------------------------------------------------------------
# NetBoxes names a dot by x and y from the top left, from 0; a line is a dot and a direction. ParlorNet's
# dotsandboxes/1 names a dot by a column letter and a row number from 1 (a1), and a line by its two dots, the
# left or upper one first (a1-b1, a1-a2): docs/types/dotsandboxes-1.md.

def dot_name(x, y):
    return "%s%d" % (chr(ord("a") + x), y + 1)


def dot_xy(name):
    return ord(name[0].lower()) - ord("a"), int(name[1:]) - 1


def line_token(x, y, direction):
    """A dot and a direction -> the line in ParlorNet's form."""
    dx, dy = STEPS[direction]
    a, b = sorted([(x, y), (x + dx, y + dy)], key=lambda d: (d[1], d[0]))
    return "%s-%s" % (dot_name(*a), dot_name(*b))


def line_move(token):
    """ParlorNet's line (a1-b1) -> (x, y, direction) from its left or upper dot."""
    d1, d2 = token.split("-")
    (x1, y1), (x2, y2) = sorted([dot_xy(d1), dot_xy(d2)], key=lambda d: (d[1], d[0]))
    if y1 == y2 and x2 == x1 + 1:
        return x1, y1, 1
    if x1 == x2 and y2 == y1 + 1:
        return x1, y1, 3
    raise WireError("not a line: %r" % token)
