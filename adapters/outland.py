"""Outland adapter: Backgammon and Reversi for the Outland Gateway (Mac, 1994-95).

The Outland games never touch the network: they talk to the Outland Gateway
on the same Mac, and the Gateway reaches the server over one byte stream (its
TCP tool: host and port are fields in the tool's settings). This adapter is
that server, a client of parlord like the other adapters. What the Gateway,
Backgammon and Outland Reversi expect was recovered from the 1994-95 client
software.

Three layers, as the Gateway has them:

    link     frames: (seq << 4 | ack), (type << 4 | channel), a 16-bit check
             (low half of a table CRC-32, plus a direction key: the Gateway
             sends CRC+1 and accepts CRC+0), a DLE-stuffed payload, DLE ETX.
             Data, ack, nak and resend frames, sequence numbers mod 16, a
             window of 8.
    control  channel 0: P (start, carries the key that XORs the login), the
             login 0x82, W (name), Y (service list: Backgammon, Reversi), R / S
             (service channel open / closed), T (keepalive), V (message by
             number), b (a text window).
    game     channel 1-3: Backgammon's fixed 258-byte records, mapped onto
             backgammon/1 tables in the core; or Reversi's commands, mapped
             onto reversi/1 tables (ReversiService, below).

Sign-in: the Gateway's account name is the ParlorNet name; its password
answers the server's credential question. A password the server mailed
(a new account's, or one asked for with reset) is refused with "Sign in on
the web or by telnet first to choose your password.": the Gateway cannot
be asked for a new one. As with Outland's own service, the login only signs in: a name with
no account is refused (V 1, no such account) with a pointer to the web site
and telnet, where accounts are made, so a mistyped name never becomes an
account. Under sign-in none, where the name is the whole sign-in, a new name
is simply taken. A refusal is a text window, then V 1 or V 2, which make the
Gateway hang up.

The lobby lists the Outland players, everyone at a Backgammon table in the
core and the players the core lists as online, the bot among them (one shared
poll of LST and WHO). A challenge to another Outland player is relayed; when
it is accepted the challenger's connection opens a table with the cube off and
both sit down (seat = Outland color: team 0 is Red and seat 0, team 1 White
and seat 1). A challenge to a web or telnet player who waits at an open
Backgammon table joins that table. A challenge to anyone else (a web or
telnet player, the bot) opens a private table with the cube off, sits the
challenger at seat 0 (Red) and invites the other player to seat 1 (INV); the
challenger's client waits for its answer, which comes when the game starts
(accepted) or when the invitation is declined or expires (declined).
Invitations made to an Outland player anywhere else (IVL, looked at every few
seconds in the lobby) come as that player's challenge; accepting it sits down
at the seat held (JON), in the color picked (the board is mirrored if the
color is not the seat), and declining it declines the invitation (UNV).
"/open" in the lobby chat opens a table that web and telnet players can join;
when someone sits down (and when an Outland player comes back to a running
game) the Outland player gets a challenge from that opponent, and the color
picked there decides how the board is shown. The lobby chat, and Reversi's public talk, take the shared
chat commands (adapters/chatcmd.py, docs/chat-commands.md): /list, /resume, /open, /close, /decline, /who,
/motd and /help.

Every game is a single game (the challenge's match length is sent back as 1).
The server rolls: the dice of a turn go to the client in the go record that
starts it, so the adapter rolls in the core as the Outland player's turn
starts. The Outland client offers the cube before it shows that roll, which
the core cannot do once it has rolled, so a double from an Outland client is
refused with a notice (doubles from web and telnet players are offered to
the Outland player normally: take or drop). Moves are checked by the core;
a move it refuses is taken back and the turn handed to the client again.

    python3 -m adapters.outland --core 127.0.0.1:6501 --port 4000 --host 0.0.0.0
"""
import argparse
import asyncio
import itertools
import random
import re
import struct
import sys
from collections import deque

from . import botflag, chatcmd, declare
from .chatcmd import game_lines, open_lines
from .common import (CoreClient, CoreError, Gate, Where, keepalive, err_text, framed_motd, parse_backgammon_view,
                     parse_invites, parse_players, parse_reversi_view, parse_tables, peer_addr, sign_in_lines,
                     uninvite_of)

# ---- link layer ----------------------------------------------------------------

DLE, ETX = 0x10, 0x03
T_DATA, T_NAK, T_ACK, T_RESEND = 0, 1, 2, 3
KEY_SEND = 0            # the server's frames: CRC+0 (what the Gateway accepts)
KEY_RECV = 1            # the Gateway's frames: CRC+1
WINDOW = 8
MAX_PAYLOAD = 512       # the Gateway asserts a frame carries at most 0x200 bytes
MAX_RAW = 4096          # bytes without a frame end: junk, dropped
ACK_DELAY = 0.2
RESEND_SECS = 4.0
KEEPALIVE_SECS = 30.0

# What the core records with each turn an Outland player makes (CLI): the Gateway, a Mac program.
CLIENT = "Outland Gateway"


def _crc_table():
    table = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table.append(c)
    return table


CRC_TABLE = _crc_table()


def crc16(data):
    """Low 16 bits of the Gateway's CRC-32: start 0xFFFFFFFF, no final inversion."""
    c = 0xFFFFFFFF
    for b in data:
        c = CRC_TABLE[(c ^ b) & 0xFF] ^ (c >> 8)
    return c & 0xFFFF


def stuff(payload):
    return bytes(payload).replace(b"\x10", b"\x10\x10")


def unstuff(body):
    """As the Gateway's parser: an odd DLE is dropped, the second of a pair kept."""
    out = bytearray()
    run = 0
    for b in body:
        if b == DLE:
            run += 1
            if run % 2 == 0:
                out.append(DLE)
        else:
            out.append(b)
            run = 0
    return bytes(out)


def encode_frame(seq, ack, ftype, chan, payload=b"", key=KEY_SEND):
    head = bytes([((seq & 15) << 4) | (ack & 15), ((ftype & 15) << 4) | (chan & 15), 0, 0])
    body = stuff(payload) + b"\x10\x03"
    check = (crc16(head + body) + key) & 0xFFFF
    return head[:2] + struct.pack(">H", check) + body


class Frame:
    def __init__(self, seq, ack, ftype, chan, payload, status):
        self.seq, self.ack, self.ftype, self.chan = seq, ack, ftype, chan
        self.payload, self.status = payload, status

    def __repr__(self):
        return "Frame(seq=%d ack=%d type=%d chan=%d %s %r)" % (self.seq, self.ack, self.ftype, self.chan,
                                                               self.status, self.payload[:16])


class FrameParser:
    """Bytes in, frames out. status is 'ok' (check = CRC + key_ok), 'echo'
    (CRC + key_echo: our own frame looped back) or 'bad'."""

    def __init__(self, key_ok=KEY_RECV, key_echo=KEY_SEND):
        self.key_ok, self.key_echo = key_ok, key_echo
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= 6:
            end = self._end()
            if end is None:
                if len(self.buf) > MAX_RAW:
                    del self.buf[:]
                break
            raw = bytes(self.buf[:end])
            del self.buf[:end]
            out.append(self.parse(raw))
        return out

    def _end(self):
        """Index after the first DLE ETX (with an odd run of DLEs) from byte 4:
        the header is never stuffed and never scanned."""
        run = 0
        for i in range(4, len(self.buf)):
            b = self.buf[i]
            if b == DLE:
                run += 1
            elif b == ETX and run % 2 == 1:
                return i + 1
            else:
                run = 0
        return None

    def parse(self, raw):
        check = struct.unpack_from(">H", raw, 2)[0]
        c = crc16(raw[:2] + b"\0\0" + raw[4:])
        if check == (c + self.key_ok) & 0xFFFF:
            status = "ok"
        elif check == (c + self.key_echo) & 0xFFFF:
            status = "echo"
        else:
            status = "bad"
        return Frame(raw[0] >> 4, raw[0] & 15, raw[1] >> 4, raw[1] & 15, unstuff(raw[4:-2]), status)


class Link:
    """The server end of the Gateway's data link: in-order delivery, acks
    piggybacked on every frame (or an ack frame after ACK_DELAY), a nak for a
    bad frame or a gap, resends after a nak or RESEND_SECS without progress."""

    def __init__(self, write, on_data, clock):
        self.write, self.on_data, self.clock = write, on_data, clock
        self.parser = FrameParser()
        self.next_seq = 0
        self.unacked = []           # (seq, chan, payload), oldest first
        self.queue = deque()        # waiting for room in the window
        self.expect = 0             # next sequence number to receive
        self.ack_due = None
        self.nak_sent = False
        self.sent_at = clock()
        self.last_tx = clock()
        self.counts = {"in": 0, "out": 0, "bad": 0, "resent": 0}

    def ack_nibble(self):
        return (self.expect - 1) & 15

    def _tx(self, data):
        self.write(data)
        self.ack_due = None
        self.last_tx = self.clock()

    def send(self, chan, payload):
        payload = bytes(payload)
        for i in range(0, len(payload), MAX_PAYLOAD):
            self.queue.append((chan, payload[i:i + MAX_PAYLOAD]))
        self._pump()

    def _pump(self):
        while self.queue and len(self.unacked) < WINDOW:
            chan, p = self.queue.popleft()
            seq = self.next_seq
            self.next_seq = (seq + 1) & 15
            if not self.unacked:
                self.sent_at = self.clock()
            self.unacked.append((seq, chan, p))
            self.counts["out"] += 1
            self._tx(encode_frame(seq, self.ack_nibble(), T_DATA, chan, p))

    def _control(self, ftype):
        self._tx(encode_frame(0, self.ack_nibble(), ftype, 0))

    def _acked(self, a):
        seqs = [s for s, _, _ in self.unacked]
        if a in seqs:
            del self.unacked[:seqs.index(a) + 1]
            self.sent_at = self.clock()
            self._pump()

    def _resend(self):
        for seq, chan, p in self.unacked:
            self.counts["resent"] += 1
            self._tx(encode_frame(seq, self.ack_nibble(), T_RESEND, chan, p))
        self.sent_at = self.clock()

    def receive(self, data):
        for f in self.parser.feed(data):
            if f.status == "echo":
                continue
            if f.status == "bad":
                self.counts["bad"] += 1
                self._control(T_NAK)
                continue
            self._acked(f.ack)
            if f.ftype == T_NAK:
                self._resend()
            elif f.ftype in (T_DATA, T_RESEND):
                if f.seq == self.expect:
                    self.expect = (self.expect + 1) & 15
                    self.nak_sent = False
                    self.counts["in"] += 1
                    if self.ack_due is None:
                        self.ack_due = self.clock() + ACK_DELAY
                    self.on_data(f.chan, f.payload)
                elif 1 <= ((self.expect - f.seq) & 15) <= WINDOW:
                    self._control(T_ACK)            # a duplicate: our ack was lost
                elif not self.nak_sent:
                    self.nak_sent = True
                    self._control(T_NAK)            # a gap: send from your ack + 1 again

    def tick(self):
        now = self.clock()
        if self.ack_due is not None and now >= self.ack_due:
            self._control(T_ACK)
        if self.unacked and now - self.sent_at > RESEND_SECS:
            self._resend()


# ---- control channel ----------------------------------------------------------------

SERVICE_ID = b"BG@S"        # Backgammon's Neti 256: how the Gateway finds the application
CREATOR = b"TBBG"           # Backgammon's creator (the Gateway uses it for the icon)
SERVICES = [(SERVICE_ID, CREATOR, 1), (b"Flip", b"CRev", 1)]     # the service list: Backgammon, Reversi
CTL = 0


def xor_key(data, key):
    """The login's XOR: whole big-endian words with the key, then the bytes
    left over with its low byte (its own inverse)."""
    out = bytearray(data)
    n = len(out) // 4
    for i in range(n):
        struct.pack_into(">I", out, 4 * i, struct.unpack_from(">I", out, 4 * i)[0] ^ key)
    for i in range(4 * n, len(out)):
        out[i] ^= key & 0xFF
    return bytes(out)


def parse_login(cmd, key):
    """0x82 len vers:u32 account\\0 password\\0 (XOR-ed from byte 6) ->
    (version, account, password), or None."""
    if len(cmd) < 8 or cmd[0] != 0x82 or cmd[1] > len(cmd) or cmd[1] < 8:
        return None
    vers = struct.unpack_from(">I", cmd, 2)[0]
    parts = xor_key(cmd[6:cmd[1]], key).split(b"\0")
    if len(parts) < 2:
        return None
    return vers, parts[0].decode("mac_roman"), parts[1].decode("mac_roman")


def build_login(account, password, key, vers=0x01328002):
    body = account.encode("mac_roman") + b"\0" + password.encode("mac_roman") + b"\0"
    return bytes([0x82, 6 + len(body)]) + struct.pack(">I", vers) + xor_key(body, key)


def cmd_start(key):
    return b"P" + struct.pack(">II", key, 0)


def cmd_name(name):
    s = name.encode("mac_roman", "replace")[:31] + b"\0"
    return b"W" + bytes([2 + len(s)]) + s


def cmd_services(entries):
    """Y len:u16 count:u8, then per service nfiles:u8 flag:u8 id:u32 creator:u32.
    flag 1: the service may be opened. No files: nothing to compare with the
    local copy, so no update is offered."""
    body = b"".join(bytes([0, flag]) + sid + creator for sid, creator, flag in entries)
    return b"Y" + struct.pack(">HB", 4 + len(body), len(entries)) + body


def cmd_open(chan, sid):
    return b"R" + bytes([chan]) + sid


def cmd_closed(chan, msg, sid):
    return b"S" + bytes([chan, msg]) + sid


def cmd_message(code):
    return b"V" + struct.pack(">H", code)


def cmd_text(text):
    """b 1: a text window (Screens.c); the text ends with a NUL."""
    t = text.replace("\r\n", "\r").replace("\n", "\r").encode("mac_roman", "replace")[:4000] + b"\0"
    return b"b\x01" + struct.pack(">H", 4 + len(t)) + t


# client commands with a fixed length; the others run to the end of their frame
CTL_FIXED = {0x81: 6, 0x83: 2, 0x84: 1, 0x88: 10, 0x89: 1, 0x8b: 5, 0x8e: 2}


def split_commands(payload):
    """One frame of channel 0 from the Gateway -> commands."""
    out = []
    i = 0
    while i < len(payload):
        c = payload[i]
        n = CTL_FIXED.get(c)
        if n is None and c == 0x82 and i + 1 < len(payload):
            n = payload[i + 1]
        if not n or i + n > len(payload):
            n = len(payload) - i
        out.append(bytes(payload[i:i + n]))
        i += n
    return out


# ---- Backgammon records -----------------------------------------------------------

REC = 258
O_IMG, O_CUBE, O_DOUBLINGS, O_SCORE1, O_SCORE0 = 2, 164, 165, 166, 168
O_DIE1, O_DIE2, O_POINTS, O_MOVER = 170, 171, 172, 174
P_NAME, P_ID, P_AVAIL, P_ICON, P_STATS = 2, 0x23, 0x24, 0x25, 0x26

# client -> server
C_START, C_MOVE, C_WON, C_DOUBLE, C_TAKE, C_DROP = 0x01, 0x0a, 0x0c, 0x14, 0x15, 0x16
C_DRAW, C_RESIGN, C_ANSWER, C_ROLLED, C_AWAY, C_BREAK = 0x1a, 0x1c, 0x1d, 0x2b, 0x32, 0x34
C_CHAT, C_LOBBYCHAT, C_CHECKIN, C_CHALLENGE, C_RESPONSE, C_RELOBBY, C_WATCH = 0x37, 0x3a, 0x64, 0x6a, 0x6b, 0x6e, 0x78
# server -> client
S_GO, S_ROLL, S_TURN, S_DOUBLE, S_DRAWRET, S_RESIGN, S_RESIGNRET = 0x28, 0x2a, 0x7c, 0x14, 0x1b, 0x1c, 0x1d
S_LEFT, S_CHAT, S_LOBBYCHAT, S_NEWPLAYER, S_PLAYERLEFT, S_CHALLENGE = 0x33, 0x37, 0x3a, 0x65, 0x66, 0x6a
S_RESPONSE, S_YOURNAME, S_RELOBBY, S_LEFTLOBBY, S_OKWATCH, S_PUBNOTICE, S_PVTNOTICE = \
    0x6b, 0x6d, 0x6e, 0x6f, 0x79, 0x82, 0x83

NAMES = {0x01: "start", 0x0a: "move", 0x0c: "won", 0x14: "double", 0x15: "take", 0x16: "drop", 0x1a: "draw",
         0x1c: "resign", 0x1d: "answer", 0x2b: "rolled", 0x32: "away", 0x34: "break", 0x37: "chat",
         0x3a: "lobbychat", 0x64: "checkin", 0x6a: "challenge", 0x6b: "response", 0x6e: "relobby",
         0x78: "watch"}


def record(rtype, b1=0):
    r = bytearray(REC)
    r[0], r[1] = rtype, b1 & 0xFF
    return r


def pstr(text, limit=253):
    b = text.encode("mac_roman", "replace")[:limit]
    return bytes([len(b)]) + b


def read_pstr(r, at):
    n = min(r[at], len(r) - at - 1)
    return bytes(r[at + 1:at + 1 + n]).decode("mac_roman", "replace")


def s16(b, at):
    return struct.unpack_from(">h", b, at)[0]


def player_record(rtype, pid, name, available):
    r = record(rtype, pid)
    r[P_NAME:P_NAME + 33] = pstr(name, 32).ljust(33, b"\0")
    r[P_ID] = pid
    r[P_AVAIL] = 1 if available else 0
    r[P_ICON] = 0
    return r


NOTICE_LEN = 200            # a notice record's text


def notice(rtype, text):
    r = record(rtype)
    r[1:1 + 201] = pstr(text, NOTICE_LEN).ljust(201, b"\0")
    return r


# ---- boards ----------------------------------------------------------------------------

class Board:
    """Points 1-24 signed as in backgammon/1 (seat 0 positive), bar and off per seat."""
    START = {24: 2, 13: 5, 8: 3, 6: 5}

    def __init__(self):
        self.pts = [0] * 24
        for p, n in self.START.items():
            self.pts[p - 1] += n
            self.pts[(25 - p) - 1] -= n
        self.bar, self.off = [0, 0], [0, 0]
        self.hits = []          # hit flags of the steps since the last turn change (for UNDO)

    @classmethod
    def from_view(cls, v):
        b = cls()
        b.pts, b.bar, b.off = list(v["pts"]), list(v["bar"]), list(v["off"])
        return b

    def step(self, seat, frm, to, hit):
        s = 1 if seat == 0 else -1
        if frm == "bar":
            self.bar[seat] -= 1
        else:
            self.pts[int(frm) - 1] -= s
        if to == "off":
            self.off[seat] += 1
        else:
            if hit:
                self.pts[int(to) - 1] = 0
                self.bar[1 - seat] += 1
            self.pts[int(to) - 1] += s
        self.hits.append(hit)

    def undo(self, seat, frm, to):
        s = 1 if seat == 0 else -1
        hit = self.hits.pop() if self.hits else False
        if to == "off":
            self.off[seat] -= 1
        else:
            self.pts[int(to) - 1] -= s
            if hit:
                self.bar[1 - seat] -= 1
                self.pts[int(to) - 1] = -s
        if frm == "bar":
            self.bar[seat] += 1
        else:
            self.pts[int(frm) - 1] += s


def column_point(col, flip):
    """Outland column 0-23 -> backgammon/1 point."""
    return col + 1 if flip == 0 else 24 - col


def board_image(board, flip, moves=None):
    """The 162-byte image: move list (from the relayed record, or none),
    24 columns of count/team (team 2 for an empty column), then the trays:
    team 0 off, team 1 off, team 0 bar, team 1 bar. team = seat ^ flip."""
    img = bytearray(162)
    if moves:
        img[0:0x32] = moves[:0x32]
    for c in range(24):
        n = board.pts[column_point(c, flip) - 1]
        count, team = (0, 2) if n == 0 else (abs(n), (0 if n > 0 else 1) ^ flip)
        struct.pack_into(">h", img, 0x32 + 2 * c, count)
        struct.pack_into(">h", img, 0x62 + 2 * c, team)
    struct.pack_into(">hh", img, 0x92, board.off[0 ^ flip], 0)
    struct.pack_into(">hh", img, 0x96, board.off[1 ^ flip], 1)
    struct.pack_into(">hh", img, 0x9a, board.bar[0 ^ flip], 0)
    struct.pack_into(">hh", img, 0x9e, board.bar[1 ^ flip], 1)
    return img


def image_counts(img, flip):
    """An image -> (pts, bar, off) in backgammon/1 terms (for checks)."""
    pts = [0] * 24
    for c in range(24):
        n, team = s16(img, 0x32 + 2 * c), s16(img, 0x62 + 2 * c)
        if n:
            seat = (team & 1) ^ flip
            pts[column_point(c, flip) - 1] = n if seat == 0 else -n
    off, bar = [0, 0], [0, 0]
    off[0 ^ flip], off[1 ^ flip] = s16(img, 0x92), s16(img, 0x96)
    bar[0 ^ flip], bar[1 ^ flip] = s16(img, 0x9a), s16(img, 0x9e)
    return pts, bar, off


def image_moves(img, team, flip):
    """The moves of the turn just played -> backgammon/1 steps ('13-9',
    'bar-22', '6-off'), or None if the list makes no sense. Team 0 enters from
    24 and bears off to -1, team 1 enters from -1 and bears off to 24; 255 is
    -1 too."""
    n = s16(img, 0)
    if not 0 <= n <= 4:
        return None
    out = []
    for i in range(n):
        f, t = s16(img, 2 + 2 * i), s16(img, 0xa + 2 * i)
        f, t = -1 if f == 255 else f, -1 if t == 255 else t
        if (team == 0 and f == 24) or (team == 1 and f == -1):
            frm = "bar"
        elif 0 <= f <= 23:
            frm = str(column_point(f, flip))
        else:
            return None
        if (team == 0 and t == -1) or (team == 1 and t == 24):
            to = "off"
        elif 0 <= t <= 23:
            to = str(column_point(t, flip))
        else:
            return None
        out.append("%s-%s" % (frm, to))
    return out


# ---- the adapter ------------------------------------------------------------------------

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,11}\Z")
# Said in each lobby as a player comes in, after the message of the day: how to start a game with the bot.
# After accepting a challenge for a game whose seats were already set (a game brought back, an open table,
# an invitation with a seat held): the color picked only decides how the board is drawn.
FIXED_SIDE = ("Your side was set when this game started. The board is now drawn with you as %s. Other clients will "
              "draw you as %s.")
WEB_BG_COLOURS = ("white", "black")     # seat 0, seat 1 on the web and telnet boards (adapters/games/backgammon.py)
WEB_RV_COLOURS = ("black", "white")     # seat 0, seat 1 there (adapters/games/reversi.py)
BG_BOT_HOWTO = "To play the bot, pick bot in the player list and click Invite."
RV_BOT_HOWTO = "To play the bot, pick bot in the player list and choose Invite from the Game menu."
BACKGAMMON = "backgammon/1"
MAX_SESSIONS = 40
POLL_SECS = 1.0
INVITE_POLLS = 3        # invitations made elsewhere: looked at every third poll
HUB_SECS = 3.0
REFUSE_LINGER = 3.0
LOGIN_SECS = 60.0         # a connection that has not logged in by then is closed
WHERE = Where()          # where else to play, from the server's name and its ports (common.Where)

ids = itertools.count(1)


def log(sid, text):
    print("outland[%d] %s" % (sid, text), flush=True)


def no_wait(text):
    return re.sub(r"^wait=\d+ ", "", text)


def chat_text(text):
    """Outland chat (Mac Roman, up to 253 characters) -> printable ASCII for CHT."""
    return "".join(c if " " <= c <= "~" else "?" for c in text).strip()


class Hub:
    """One game's Outland lobby: the Outland players in it, everyone at a table
    of that game in the core, player ids (one byte each, as the Backgammon
    client keeps them; Reversi's clients number their players themselves), and
    pending challenges. Its members (a Backgammon Session, a ReversiService)
    show the list in their own records: lobby_enter, lobby_player, lobby_gone."""

    def __init__(self, poll_secs, key=None):
        self.key = key or BACKGAMMON
        self.sessions = {}      # lower name -> member in the lobby
        self.ids = {}           # lower name -> id
        self.names = {}         # id -> display name
        self.tables = []        # backgammon tables from the last LST
        self.present = {}       # lower name -> (name, available) as announced
        self.offers = set()     # (challenger lower, target lower)
        self.online = []        # the last WHO: players connected anywhere, and one bot (botflag.listed)
        self.bots = set()       # lower names of the bots WHO listed
        self.who_ok = False     # the core answers WHO: the lobby lists the players online, nobody else
        self.task = None
        self.turn = 0
        self.poll_secs = poll_secs

    def id_of(self, name):
        k = name.lower()
        if k not in self.ids:
            used = set(self.ids.values())
            pid = next((i for i in range(1, 251) if i not in used), 0)     # 0: no room in the client's list
            self.ids[k] = pid
        self.names[self.ids[k]] = name
        return self.ids[k]

    def release(self, name):
        pid = self.ids.pop(name.lower(), None)
        if pid is not None:
            self.names.pop(pid, None)

    def everyone(self):
        """lower name -> (name, available): Outland players in the lobby (busy
        while they play) and the players at Backgammon tables (busy at a
        running game)."""
        out = {}
        for n in self.online:
            out[n.lower()] = (n, True)
        for t in self.tables:
            for n in t["names"]:
                if self.who_ok and n.lower() not in out:
                    continue                # not online (WHO): not listed
                busy = t["status"] == "playing" or not out.get(n.lower(), (n, True))[1]
                out[n.lower()] = (n, not busy or n.lower() in self.bots)    # the bot plays many tables at once
        for k, s in self.sessions.items():
            if not self.who_ok or k in out:
                out[k] = (s.name, not s.busy())
        # The opponent of a game a player here has running is always listed, online or not: the client
        # names a challenge or a game by its player list, so an opponent missing from it shows as someone
        # else (the challenge that brings a running game back would seem to come from another player).
        for s in self.sessions.values():
            g = getattr(s, "game", None)
            opp = getattr(g, "opp", None) if g else None
            if opp and opp.lower() not in out:
                out[opp.lower()] = (opp, False)
        return out

    def broadcast(self, rec, skip=None):
        for s in list(self.sessions.values()):
            if s is not skip:
                s.send_rec(rec)

    def members(self, skip=None):
        return [s for s in list(self.sessions.values()) if s is not skip]

    def sync(self, skip=None):
        now = self.everyone()
        for k, (name, avail) in now.items():
            pid = self.id_of(name)
            old = self.present.get(k)
            if not pid:
                continue
            if old is None or old[1] != avail:
                for s in self.members(skip):
                    s.lobby_player(name, avail, old is None)
        for k, (name, _) in self.present.items():
            if k not in now:
                for s in self.members(skip):
                    s.lobby_gone(name)
                self.release(name)
        self.present = now

    def open_table_of(self, name):
        k = name.lower()
        return next((t for t in self.tables if t["status"] == "open" and t["filled"] == 1
                     and [n.lower() for n in t["names"]] == [k] and not botflag.bot_waits(t)), None)

    def table(self, tid):
        return next((t for t in self.tables if t["id"] == tid), None)

    def keep_tables(self, data):
        self.tables = [t for t in parse_tables(data) if t["key"] == self.key and t["status"] in ("open", "playing")]

    async def poll(self, via):
        data, final = await via.core_req("LST all")
        if final.startswith("OK"):
            self.keep_tables(data)
            await self.who(via)
            self.sync()
            for s in list(self.sessions.values()):
                s.hub_polled()

    async def who(self, via):
        """The core's players online (WHO): who lists show only them. A core without WHO: None, and the
        lists show the players at tables and those connected here, as before."""
        data, final = await via.core_req("WHO")
        if final.startswith("OK"):
            players = parse_players(data)
            self.online = botflag.listed(players)
            self.bots = {n.lower() for n, bot in players if bot}
            self.who_ok = True

    async def run(self):
        while self.sessions:
            await asyncio.sleep(self.poll_secs)
            ss = list(self.sessions.values())
            if not ss:
                break
            via = ss[self.turn % len(ss)]
            self.turn += 1
            try:
                await self.poll(via)
            except (CoreError, OSError, asyncio.TimeoutError):
                via.closing = True
            except Exception as e:      # the shared poll must keep running
                print("outland hub error: %r" % e, file=sys.stderr, flush=True)
        self.task = None

    async def enter(self, s):
        """A player comes into the lobby: they get the list (lobby_enter), then
        the others hear of them."""
        if not self.sessions:
            try:
                data, final = await s.core_req("LST all")
                if final.startswith("OK"):
                    self.keep_tables(data)
            except (CoreError, OSError, asyncio.TimeoutError):
                pass
        try:
            await self.who(s)               # the players online, the newcomer among them
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        self.sessions[s.name.lower()] = s
        s.lobby_enter(self.everyone())
        self.sync(skip=s)
        self.present[s.name.lower()] = (s.name, not s.busy())
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self.run())

    def leave(self, s):
        k = s.name.lower()
        if self.sessions.get(k) is s:
            del self.sessions[k]
        for o in [o for o in self.offers if k in o]:
            self.offers.discard(o)
            other = self.sessions.get(o[0] if o[1] == k else o[1])
            if other and o[1] == k:       # their challenge to me goes unanswered
                other.offer_unanswered(k)
        self.sync()


def response(pid, accepted, team=0):
    """0x6b to a challenger: [2] who answers, [4..5] match length, [6] accepted,
    [7] the challenger's own team (the client takes the color it is given)."""
    r = record(S_RESPONSE)
    r[2] = pid
    struct.pack_into(">H", r, 4, 1)
    r[6], r[7] = (1 if accepted else 0), team
    return r


class Game:
    """One Outland player's game: a backgammon/1 table in the core."""

    def __init__(self, tid, seat, opp, opp_id, state, team=None):
        self.tid, self.seat, self.opp, self.opp_id = tid, seat, opp, opp_id
        self.team = seat if team is None else team
        self.flip = self.team ^ seat
        self.state = state          # waiting, challenged (my invitation out), invite, playing, over
        self.resume = False
        self.cursor = 0
        self.early = set()          # event numbers handled ahead of the cursor
        self.says = {}              # seat -> what its player plays with, from a CLIENT right after its JOIN
        self.board = Board()
        self.opening = None         # (first seat, seat 0's die, seat 1's die)
        self.opening_sent = False
        self.client_turn = False    # the client holds the turn: wait for its move
        self.my_turn = False        # TURN me seen; the dice not yet
        self.my_dice = (1, 2)
        self.opp_moves = []
        self.cube = 1
        self.doubled = False        # the opponent's double waits for an answer
        self.resign_asked = False   # we asked the client to accept a resignation
        self.resigned_here = False
        self.incomplete = 0


class Session:
    """One Gateway connection: the link, the control channel and the sign-in, Backgammon on one service
    channel (this class) and Reversi on another (self.rv, a ReversiService)."""

    def __init__(self, hub, reader, writer, core_addr, loop, ticket=None, rv_hub=None):
        self.hub, self.r, self.w, self.core_addr = hub, reader, writer, core_addr
        self.rv_hub = rv_hub or Hub(hub.poll_secs, REVERSI)
        self.rv = None
        self.ticket = ticket
        self.sid = next(ids)
        self.loop = loop
        self.link = Link(self.write, self.on_data, loop.time)
        self.core = None
        self.core_lock = asyncio.Lock()
        self.lock = asyncio.Lock()
        self.inbox = asyncio.Queue()
        self.key = random.getrandbits(32) | 1
        self.name = None
        self.pid = 0
        self.signed = False
        self.bg_chan = None
        self.bg_buf = bytearray()
        self.in_lobby = False
        self.game = None
        self.invited = {}           # lower name -> table id: invitations to me shown as that player's challenge
        self.told = set()           # table ids of invitations to other games, told once
        self.released = {}          # table id -> (seat, opponent, turn last seen): running games left in the core
        self.closing = False
        self.refused = False
        self._chat = None

    # ---- output ------------------------------------------------------------------------
    def write(self, data):
        if not self.w.is_closing():
            self.w.write(data)

    def ctl(self, cmd):
        self.link.send(CTL, cmd)

    def send_rec(self, rec):
        if self.bg_chan is not None:
            self.link.send(self.bg_chan, bytes(rec))

    def pub(self, text):
        self.send_rec(notice(S_PUBNOTICE, text))

    def pvt(self, text):
        self.send_rec(notice(S_PVTNOTICE, text))

    async def core_req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    async def locked(self, fn, *args):
        async with self.lock:
            if not self.closing:
                try:
                    await fn(*args)
                except (CoreError, OSError, asyncio.TimeoutError):
                    self.closing = True
                except Exception as e:
                    print("outland[%d] error: %r" % (self.sid, e), file=sys.stderr, flush=True)

    def busy(self):
        return bool(self.game and self.game.state in ("invite", "playing", "challenged"))

    # ---- the Backgammon lobby list, as the Hub reports it --------------------------------
    def lobby_enter(self, everyone):
        """LobbyCheckIn: the player list (with themselves) first, then their own
        record (so the list does not announce each player as arriving)."""
        self.pid = self.hub.id_of(self.name)
        for k, (name, avail) in everyone.items():
            self.send_rec(player_record(S_NEWPLAYER, self.hub.id_of(name), name, avail))
        self.send_rec(player_record(S_YOURNAME, self.pid, self.name, True))

    def lobby_player(self, name, avail, new):
        pid = self.hub.id_of(name)
        self.send_rec(player_record(S_NEWPLAYER if new else S_RELOBBY if avail else S_LEFTLOBBY, pid, name, avail))

    def lobby_gone(self, name):
        self.send_rec(record(S_PLAYERLEFT, self.hub.ids.get(name.lower(), 0)))

    def offer_unanswered(self, k):
        self.send_rec(response(self.hub.ids.get(k, 0), False))

    def hub_polled(self):
        if self.game and self.game.state == "waiting":
            asyncio.ensure_future(self.locked(self.check_waiting))
        elif self.game and self.game.state == "challenged":
            asyncio.ensure_future(self.locked(self.check_challenge))

    # ---- input ---------------------------------------------------------------------------
    def on_data(self, chan, payload):
        if chan == CTL:
            for cmd in split_commands(payload):
                self.inbox.put_nowait(("ctl", cmd))
        elif chan == self.bg_chan:
            self.bg_buf += payload
            while True:
                while self.bg_buf and self.bg_buf[0] == 0:      # as the client does: skip zeros
                    del self.bg_buf[0]
                if len(self.bg_buf) < REC:
                    break
                rec = bytes(self.bg_buf[:REC])
                del self.bg_buf[:REC]
                self.inbox.put_nowait(("rec", rec))
        elif self.rv is not None and chan == self.rv.chan:
            for cmd in self.rv.feed(payload):
                self.inbox.put_nowait(("rv", cmd))
        else:
            log(self.sid, "data on channel %d ignored" % chan)

    async def worker(self):
        while not self.closing:
            kind, data = await self.inbox.get()
            await self.locked({"ctl": self.control, "rec": self.game_record, "rv": self.rv_command}[kind], data)

    async def rv_command(self, cmd):
        if self.rv is not None:
            await self.rv.command(cmd)

    # ---- control channel ---------------------------------------------------------------
    async def control(self, cmd):
        c = cmd[0]
        if c == 0x82:
            await self.login(cmd)
        elif not self.signed:
            log(self.sid, "command 0x%02x before the login ignored" % c)
        elif c == 0x81 and len(cmd) >= 6:
            chan, sid = cmd[1], cmd[2:6]
            log(self.sid, "open service %r on channel %d" % (sid, chan))
            if sid == SERVICE_ID and 1 <= chan <= 3:
                if self.bg_chan is not None:
                    self.close_service()
                if self.rv is not None and self.rv.chan == chan:
                    self.close_reversi()
                self.bg_chan, self.bg_buf = chan, bytearray()
                self.ctl(cmd_open(chan, sid))
            elif sid == RV_SERVICE_ID and 1 <= chan <= 3:
                if self.rv is not None:
                    self.close_reversi()
                if chan == self.bg_chan:
                    self.close_service()
                self.rv = ReversiService(self, self.rv_hub, chan)
                self.ctl(cmd_open(chan, sid))
            else:
                self.ctl(cmd_closed(chan, 3, sid))
        elif c == 0x83 and len(cmd) >= 2:
            log(self.sid, "service on channel %d closed by the client" % cmd[1])
            if cmd[1] == self.bg_chan:
                self.close_service()
            elif self.rv is not None and cmd[1] == self.rv.chan:
                self.close_reversi()
        elif c == 0x84:
            # Gateway 1.3.2 sends this right after "8c 03 03", once it has the service list, and stays
            # connected (seen in an emulator): it is not a sign-off. The session ends when the stream does.
            log(self.sid, "command 0x84 (after the service list) ignored")
        else:
            log(self.sid, "command 0x%02x (%d bytes) ignored" % (c, len(cmd)))

    def close_service(self):
        if self.in_lobby:
            self.hub.leave(self)
        self.in_lobby = False
        self.bg_chan = None
        self.bg_buf = bytearray()

    def close_reversi(self):
        self.rv.channel_closed()
        self.rv = None

    async def login(self, cmd):
        if self.signed or self.refused:
            return
        parsed = parse_login(cmd, self.key)
        if parsed is None:
            await self.refuse(1, "The login record could not be read.")
            return
        vers, name, password = parsed
        log(self.sid, "login %r (Gateway version %08x; password not logged)" % (name, vers))
        if not NAME_RE.match(name):
            await self.refuse(1, "Names here are 1 to 12 letters, digits, - or _, starting with a letter or "
                                 "digit. Use such a name as the Outland account name.")
            return
        if name.lower() in self.hub.sessions or name.lower() in self.rv_hub.sessions or any(
                s.name and s.name.lower() == name.lower() and s.signed for s in SESSIONS if s is not self):
            await self.refuse(1, "%s is signed in from another Outland Gateway already." % name)
            return
        refusal, code = await self.sign_in(name, password)
        if refusal is None and self.ticket is not None and not self.ticket.sign_in():
            refusal, code = "ParlorNet's Outland service is full; try again soon.", 1
        if refusal:
            await self.refuse(code, refusal)
            return
        self.name, self.signed = name, True
        self.ctl(cmd_name(name))
        self.ctl(cmd_services(SERVICES))
        log(self.sid, "signed in as %s" % name)

    async def sign_in(self, name, password):
        """(refusal, V code). The Gateway sends its password in the login packet
        and cannot be asked anything after it, so this frontend declares only
        secret steps (common.sign_in_lines without prompt): the password
        answers the server's credential question; a mailed temporary password
        ends with the core's NEWPASS refusal, a pointer to the web and
        telnet, where the new password is chosen. It signs in only (USER): a name with no account
        is V 1 with a pointer to where accounts are made (under sign-in none
        the name is simply taken). If the core asks nothing, the password is
        dropped. The connection to the core is opened only now, with a login
        packet from the Gateway."""
        if self.core is None:
            try:
                self.core = await CoreClient.connect(*self.core_addr)
                await self.core.source(peer_addr(self.w))
                await self.core.claim(CLIENT, "Macintosh")      # what the turns say they were played with (CLI)
                await WHERE.update(self.core)
            except (OSError, asyncio.TimeoutError, CoreError):
                self.core = None
                return "The game server isn't answering; try again soon.", 1
        unknown = "There is no account named %s here." % name
        refusal = await sign_in_lines(self.core, name, password, mode="signin", unknown=unknown)
        if refusal is None:
            return None, 0
        if refusal == unknown:          # V 1: no such account, as Outland's own service said
            return "%s Make one %s, then sign in here with it." % (unknown, WHERE.new_text), 1
        return ("%s (Sign up or sign in %s.)" % (refusal, WHERE.text) if "web" not in refusal else refusal,
                2 if password else 1)

    async def refuse(self, code, text):
        """A text window with the reason, then V code: codes 1, 2 and 4 make
        the Gateway hang up after a second; we close soon after."""
        log(self.sid, "refused: %s" % text)
        self.refused = True
        self.ctl(cmd_text(text))
        self.ctl(cmd_message(code))
        asyncio.get_running_loop().call_later(REFUSE_LINGER, self.hang_up)

    def hang_up(self):
        self.closing = True
        try:
            self.w.close()
        except Exception:
            pass

    # ---- Backgammon records ------------------------------------------------------------
    async def game_record(self, r):
        t = r[0]
        if self.core is not None:
            self.core.user_input()      # what the player does in Backgammon is activity
        log(self.sid, "< %s (0x%02x)" % (NAMES.get(t, "?"), t))
        if t == C_CHECKIN:
            if not self.in_lobby:
                self.in_lobby = True
                await self.hub.enter(self)
                for line in await self.s_motd():
                    self.pub(line)
                self.pub(BG_BOT_HOWTO)
                await self.rejoin()
            return
        if not self.in_lobby:
            return
        handler = {C_CHALLENGE: self.on_challenge, C_RESPONSE: self.on_response, C_MOVE: self.on_move,
                   C_WON: self.on_move, C_DOUBLE: self.on_double, C_TAKE: self.on_answer_double,
                   C_DROP: self.on_answer_double, C_RESIGN: self.on_resign, C_ANSWER: self.on_answer,
                   C_DRAW: self.on_draw, C_AWAY: self.on_away, C_RELOBBY: self.on_relobby,
                   C_CHAT: self.on_chat, C_LOBBYCHAT: self.on_lobby_chat, C_WATCH: self.on_watch}.get(t)
        if handler:
            await handler(r)

    async def s_motd(self):
        """The message of the day between its rules, for a lobby's first lines (none when there is none)."""
        try:
            return framed_motd(await self.core.motd())
        except (CoreError, OSError, asyncio.TimeoutError):
            return []

    async def running(self):
        """My Backgammon games running in the core: [(table, seat, opponent, whose turn)], oldest first; and
        my open table, if any."""
        me = self.name.lower()
        data, final = await self.core_req("LST")
        mine = sorted((t for t in parse_tables(data) if t["key"] == BACKGAMMON and t["status"] in ("open", "playing")
                       and me in [n.lower() for n in t["seated"]]), key=lambda t: int(t["id"]))
        games, open_t = [], None
        for t in mine:
            seat = [n.lower() for n in t["seated"]].index(me)
            if t["status"] == "open":
                open_t = open_t or (t["id"], seat)
                continue
            data, final = await self.core_req("VUE %s M" % t["id"])
            v = parse_backgammon_view(data) if final.startswith("OK") else {"turn": -1, "clock": None}
            games.append((t["id"], seat, t["seated"][1 - seat], v["turn"], v["clock"] and v["clock"][1]))
        return games, open_t

    async def rejoin(self):
        """Into the lobby with games running in the core: one where it is my move comes back with its
        opponent's challenge; the others wait in the core, said in one line (/resume brings one back, and
        the opponent's move offers it). Else an open table of mine is mine again."""
        games, open_t = await self.running()
        mine = [x for x in games if x[3] == x[1]]
        for tid, seat, opp, turn, _ in games:
            self.released[tid] = (seat, opp, turn)
        for line in game_lines(games, "Backgammon", entering=True):
            self.pub(line)
        waiting = [x for x in games if x[3] != x[1]]
        if waiting:
            self.pub(waiting_line(waiting))
        self.pub(COMMANDS_LINE)
        if mine:
            await self.bring_back(*mine[0][:3])
        elif open_t:
            self.game = Game(open_t[0], open_t[1], None, 0, "waiting")
            self.pub("Your table %s is open; web and telnet players can join it." % open_t[0])

    async def bring_back(self, tid, seat, opp, why=None):
        """A running game comes back as its opponent's challenge; accepting it shows the board."""
        self.released.pop(tid, None)
        self.game = Game(tid, seat, opp, self.hub.id_of(opp), "invite")
        self.game.resume = True
        self.hub.sync()
        self.pub(why or "Your game with %s at table %s goes on: accept the challenge to play it." % (opp, tid))
        self.send_rec(challenge_record(self.game.opp_id))

    def release(self, g, turn=-1):
        """Leave a running game to the core (it goes on there) and say how to bring it back."""
        self.game = None
        self.released[g.tid] = (g.seat, g.opp, turn)
        self.pub("Your game with %s at table %s goes on. Type /resume in the lobby chat to bring it back."
                 % (g.opp, g.tid))
        self.hub.sync()

    async def resume(self, arg):
        """/resume [table]: a running game back with its challenge, the first one if no table is given."""
        if self.busy():
            self.pub("Finish your game first.")
            return
        if self.game and self.game.state == "waiting":
            self.pub("Your table %s is open. Type /close to close it first." % self.game.tid)
            return
        games, _ = await self.running()
        pick = [x for x in games if not arg or x[0] == arg]
        if not pick:
            self.pub("You have no Backgammon game running at table %s." % arg if arg
                     else "You have no Backgammon game running.")
            return
        await self.bring_back(*pick[0][:3])

    async def check_released(self):
        """A game left in the core whose opponent has moved: offered with its challenge, while I am free."""
        if self.game or self.invited or not self.released:
            return
        for tid, (seat, opp, last) in list(self.released.items()):
            data, final = await self.core_req("VUE %s M" % tid)
            v = parse_backgammon_view(data) if final.startswith("OK") else None
            if v is None or v["turn"] < 0:
                del self.released[tid]
                continue
            self.released[tid] = (seat, opp, v["turn"])
            if v["turn"] == seat and last != seat:
                await self.bring_back(tid, seat, opp, "%s has moved in your game at table %s: accept the "
                                                      "challenge to play it." % (opp, tid))
                return

    async def on_challenge(self, r):
        pid = r[2]
        target = self.hub.names.get(pid)
        if self.busy():
            self.send_rec(response(pid, False))
            self.pub("Finish your game first.")
            return
        if not target or target.lower() == self.name.lower():
            self.send_rec(response(pid, False))
            return
        if struct.unpack_from(">H", r, 4)[0] != 1:
            self.pub("Games here are single games, so the match is 1 game.")
        other = self.hub.sessions.get(target.lower())
        if other:
            if other.busy():
                self.send_rec(response(pid, False))
                self.pub("%s is playing." % target)
                return
            self.hub.offers.add((self.name.lower(), target.lower()))
            other.send_rec(challenge_record(self.pid))
            return
        tab = self.hub.open_table_of(target)
        if tab is None:
            await self.invite(target, pid)
            return
        await self.leave_open()
        _, final = await self.core_req("JON %s" % tab["id"])
        if not final.startswith("OK"):
            self.send_rec(response(pid, False))
            self.pub("Can't join %s's table: %s" % (target, err_text(final)))
            return
        seat = int(final.split()[1])
        self.game = Game(tab["id"], seat, target, pid, "playing")
        self.send_rec(response(pid, True, seat))
        self.hub.sync()

    async def invite(self, target, pid):
        """A challenge to a player who is not on Outland here and not waiting at an open table (a web or
        telnet player, the bot): a private table with the cube off, me at seat 0 (Red), the other seat held
        for them (INV). The client waits for the answer: check_challenge sends it."""
        await self.leave_open()
        _, final = await self.core_req("NEW %s standard cube=0 vis=private" % BACKGAMMON)
        if final.startswith("OK"):
            tid = final.split()[1]
            _, final = await self.core_req("JON %s 0" % tid)
            if final.startswith("OK"):
                _, final = await self.core_req("INV %s %s 1" % (tid, target))
                if not final.startswith("OK"):
                    await self.core_req("LVE %s" % tid)
        if not final.startswith("OK"):
            self.send_rec(response(pid, False))
            self.pub("There is no player called %s here." % target if final.startswith("ERR NOPLAYER")
                     else "Can't challenge %s: %s" % (target, err_text(final)))
            return
        self.game = Game(tid, 0, target, pid, "challenged")
        if final == "OK offline":       # not online now (WHO): the invitation waits for them
            self.pub("%s is not online now. Your challenge will wait at table %s for an hour." % (target, tid))
        elif target.lower() in self.hub.bots:
            self.pub("%s is the server's bot. It will accept in a few seconds." % target)
        else:
            self.pub("Your challenge to %s is waiting at table %s. %s can answer it on the web or by telnet."
                     % (target, tid, target))
        self.hub.sync()

    async def check_challenge(self):
        """My challenge to a player elsewhere (invite): the game started (accepted: the client is told, and
        the game is played from its first event), or the invitation ended (declined)."""
        g = self.game
        if not g or g.state != "challenged":
            return
        data, final = await self.core_req("SIN %s 0" % g.tid)
        if final.startswith("ERR"):
            self.game = None
            self.send_rec(response(g.opp_id, False))
            self.hub.sync()
            return
        for l in data:
            p = l.split(" ", 3)
            if len(p) < 4 or p[0] != "EVT":
                continue
            if p[3] == "START":
                g.state = "playing"
                self.send_rec(response(g.opp_id, True, g.team))
                self.hub.sync()
                return
            u = uninvite_of(p[3])
            if u and u[0].lower() == (g.opp or "").lower():
                await self.core_req("LVE %s" % g.tid)
                self.game = None
                self.send_rec(response(g.opp_id, False))
                self.pub({"declined": "%s declined your challenge.",
                          "expired": "Your challenge to %s expired."}.get(u[1], "Your challenge to %s ended.")
                         % u[0])
                self.hub.sync()
                return

    async def check_invites(self):
        """Invitations to me made anywhere else (IVL): a Backgammon one comes as the inviter's challenge
        (the client's dialog), one at a time and only while I am free; another game's, a notice once."""
        data, final = await self.core_req("IVL")
        if not final.startswith("OK"):
            return
        invs = parse_invites(data)
        live = {i["id"] for i in invs}
        for k in [k for k, tid in self.invited.items() if tid not in live]:
            del self.invited[k]
        self.told &= live
        for i in invs:
            k = i["from"].lower()
            if i["key"] != BACKGAMMON:
                if i["key"] == REVERSI and self.rv is not None and self.rv.in_lobby:
                    continue                # the Reversi window shows it as an invitation
                if i["id"] not in self.told:
                    self.told.add(i["id"])
                    self.pub("%s invites you to %s at table %s. You can play it %s."
                             % (i["from"], i["key"].split("/")[0].capitalize(), i["id"], WHERE.text))
                continue
            if k in self.invited or k in self.hub.sessions or self.busy() or self.invited:
                continue
            self.invited[k] = i["id"]
            pid = self.hub.id_of(i["from"])
            if k not in self.hub.present:       # not in the client's list yet: the dialog needs the name
                self.send_rec(player_record(S_NEWPLAYER, pid, i["from"], True))
            self.send_rec(challenge_record(pid))

    async def on_response(self, r):
        pid, accepted, team = r[2], r[6] != 0, r[7] & 1
        g = self.game
        if g and g.state == "invite" and pid == g.opp_id:
            await self.invite_answer(accepted, team)
            return
        challenger = self.hub.names.get(pid)
        tid = self.invited.pop((challenger or "").lower(), None)
        if tid is not None:
            await self.invitation_answer(tid, challenger, pid, accepted, team)
            return
        key = ((challenger or "").lower(), self.name.lower())
        if key not in self.hub.offers:
            log(self.sid, "a response to no challenge")
            return
        self.hub.offers.discard(key)
        ch = self.hub.sessions.get(key[0])
        if ch is None or ch.busy():
            if accepted:
                self.pvt("%s is not here any more." % challenger)
                self.send_rec(record(S_LEFT))
            return
        if not accepted:
            ch.send_rec(response(self.pid, False))
            return
        await self.leave_open()
        asyncio.ensure_future(ch.locked(ch.start_challenge, self, team))

    async def invitation_answer(self, tid, challenger, pid, accepted, team):
        """The answer to an invitation shown as a challenge: accepted, sit down at the seat held for me
        (JON), the board in the color picked; declined, UNV."""
        if not accepted:
            await self.core_req("UNV %s %s" % (tid, self.name))
            return
        await self.leave_open()
        _, final = await self.core_req("JON %s" % tid)
        if not final.startswith("OK"):
            self.pvt("Can't sit down at table %s: %s" % (tid, err_text(final)))
            self.send_rec(record(S_LEFT))
            return
        self.game = Game(tid, int(final.split()[1]), challenger, pid, "playing", team)
        self.pvt(FIXED_SIDE % ("White" if team else "Red", WEB_BG_COLOURS[self.game.seat]))
        self.hub.sync()

    async def start_challenge(self, target, team):
        """The challenger's side: open the table (cube off), sit at the other
        color's seat, tell the client, then the target sits down."""
        await self.leave_open()
        _, final = await self.core_req("NEW %s standard cube=0" % BACKGAMMON)
        seat = 1 - team
        if final.startswith("OK"):
            tid = final.split()[1]
            _, final = await self.core_req("JON %s %d" % (tid, seat))
        if not final.startswith("OK"):
            self.send_rec(response(target.pid, False))
            self.pub("Can't open a table: %s" % err_text(final))
            asyncio.ensure_future(target.locked(target.cancelled, self.name))
            return
        self.game = Game(tid, seat, target.name, target.pid, "playing")
        self.send_rec(response(target.pid, True, seat))
        asyncio.ensure_future(target.locked(target.join_challenge, tid, team, self))

    async def join_challenge(self, tid, seat, ch):
        _, final = await self.core_req("JON %s %d" % (tid, seat))
        if not final.startswith("OK"):
            self.pvt("Can't sit down at table %s: %s" % (tid, err_text(final)))
            self.send_rec(record(S_LEFT))
            asyncio.ensure_future(ch.locked(ch.abandon, tid))
            return
        self.game = Game(tid, seat, ch.name, ch.pid, "playing")
        self.hub.sync()

    async def cancelled(self, who):
        self.pvt("%s could not open a table." % who)
        self.send_rec(record(S_LEFT))

    async def abandon(self, tid):
        if self.game and self.game.tid == tid:
            await self.core_req("LVE %s" % tid)
            self.game = None
            self.pvt("The game could not start.")
            self.send_rec(record(S_LEFT))
            self.hub.sync()

    async def invite_answer(self, accepted, team):
        g = self.game
        if not accepted:
            if g.resume:
                self.released[g.tid] = (g.seat, g.opp, g.seat)      # not offered again until /resume
                self.pub("Your game with %s at table %s goes on, and its turn clock runs. Type /resume in the "
                         "lobby chat to bring it back." % (g.opp, g.tid))
            else:
                await self.core_req("RES %s" % g.tid)
                self.pub("You declined, so the game at table %s was resigned." % g.tid)
            self.game = None
            self.hub.sync()
            return
        g.team, g.flip = team, team ^ g.seat
        g.state = "playing"
        self.pvt(FIXED_SIDE % ("White" if team else "Red", WEB_BG_COLOURS[g.seat]))
        await self.catch_up(g)
        self.hub.sync()

    async def leave_open(self):
        g = self.game
        if g and g.state == "waiting":
            await self.core_req("LVE %s" % g.tid)
            self.game = None

    async def check_waiting(self):
        """Someone sat down at my open table: their challenge."""
        g = self.game
        if not g or g.state != "waiting":
            return
        t = self.hub.table(g.tid)
        if t is None:
            data, final = await self.core_req("LST table=%s" % g.tid)
            t = next((x for x in parse_tables(data) if x["id"] == g.tid), None)
            if t is None:
                self.game = None
                self.pub("Your table %s was closed." % g.tid)
                return
        if t["status"] != "playing":
            return
        opp = t["seated"][1 - g.seat]
        g.opp, g.opp_id, g.state = opp, self.hub.id_of(opp), "invite"
        self.hub.sync()
        data, final = await self.core_req("VUE %s C" % g.tid)       # what they play with
        say = chatcmd.parse_clients(data).get(1 - g.seat, "") if final.startswith("OK") else ""
        self.pub(chatcmd.joined(opp, say, "sat down at your table %s" % g.tid, NOTICE_LEN))
        self.send_rec(challenge_record(g.opp_id))

    # ---- the game -----------------------------------------------------------------------
    async def view(self, g):
        data, final = await self.core_req("VUE %s M" % g.tid)
        return parse_backgammon_view(data) if final.startswith("OK") else None

    async def catch_up(self, g):
        """After the client accepted a game that started without it: read the
        events so far, then bring the client to the present."""
        data, final = await self.core_req("SIN %s 0" % g.tid)
        steps = 0
        for l in data:
            p = l.split()
            if len(p) < 4 or p[0] != "EVT":
                continue
            g.cursor = max(g.cursor, int(p[2]))
            if p[3] == "ROLL" and g.opening is None:
                g.opening = (int(p[4]), int(p[5]), int(p[6]))
            elif p[3] == "STEP":
                steps += 1
            elif p[3] == "END":
                g.state = "over"
        if g.state == "over":
            self.pvt("That game is over.")
            self.send_rec(record(S_LEFT))
            return
        v = await self.view(g)
        if v is None:
            return
        g.board = Board.from_view(v)
        g.cube = v["cube"]
        if steps == 0 and g.opening:
            self.send_opening(g)
            return
        g.opening_sent = True
        self.send_rec(roll_record(g.my_dice, g.opp_id))          # ends the client's opening state
        self.send_rec(turn_record(1 - g.team))
        if v["turn"] == g.seat and v["phase"] == "take":
            g.doubled = True
            self.send_rec(record(S_DOUBLE))
        elif v["turn"] == g.seat:
            while v["phase"] == "move" and (await self.core_req("UND %s" % g.tid))[1].startswith("OK"):
                pass
            g.my_turn = True
            await self.poll_game()
        else:
            self.pvt("It is %s's turn; the board will be brought up to date on yours." % g.opp)

    def send_opening(self, g):
        first, d0, d1 = g.opening
        by_seat = (d0, d1)
        r = record(S_ROLL)
        r[O_DIE1], r[O_DIE2] = by_seat[1 ^ g.flip], by_seat[0 ^ g.flip]     # team 1's die, team 0's die
        r[O_MOVER] = g.opp_id
        self.send_rec(r)
        g.opening_sent = True
        if first == g.seat:
            g.my_dice = (d0, d1)
            g.client_turn = True

    async def poller(self, secs):
        n = 0
        while not self.closing:
            await asyncio.sleep(secs)
            n += 1
            g = self.game
            if g and g.state == "playing" and self.bg_chan is not None:
                await self.locked(self.poll_game)
            if self.in_lobby and n % INVITE_POLLS == 0:
                await self.locked(self.check_invites)
                await self.locked(self.check_released)
            rv = self.rv
            if rv is not None and rv.game and rv.game.state == "playing":
                await self.locked(rv.poll_game)
            if rv is not None and rv.in_lobby and n % INVITE_POLLS == 0:
                await self.locked(rv.check_invites)
                await self.locked(rv.check_released)

    async def poll_game(self):
        g = self.game
        for _ in range(4):          # an automatic roll is followed by its events at once
            if not g or g.state != "playing" or self.game is not g:
                return
            data, final = await self.core_req("SIN %s %d" % (g.tid, g.cursor))
            if final.startswith("ERR"):
                self.game = None
                return
            g.says = chatcmd.join_says(data)    # a CLIENT right after its JOIN is no change of program
            for l in data:
                p = l.split()
                if len(p) < 4 or p[0] != "EVT" or p[1] != g.tid:
                    continue
                seq, kind, args = int(p[2]), p[3], p[4:]
                if seq <= g.cursor:
                    continue
                if seq in g.early:
                    if not g.client_turn:
                        g.cursor = seq
                    continue
                if g.client_turn:
                    if kind in ("CHT", "END"):
                        g.early.add(seq)
                        await self.event(g, kind, args)
                    continue
                await self.event(g, kind, args)
                g.cursor = seq
            if g.state == "playing" and g.my_turn and not g.client_turn and not g.doubled:
                v = await self.view(g)
                if v and v["turn"] == g.seat and v["phase"] == "roll":
                    await self.core_req("MOV %s roll" % g.tid)
                    continue
                if v and v["turn"] == g.seat and v["phase"] == "move":
                    g.my_dice = tuple((v["dice"] + v["dice"])[:2]) if v["dice"] else g.my_dice
                    g.my_turn = False
                    await self.send_go(g)
            return

    async def event(self, g, kind, a):
        me, opp = g.seat, 1 - g.seat
        change = chatcmd.change_of(kind, a, g.says, me)
        if change and change[0] == opp and g.opp:       # the opponent plays on with another program
            self.pvt(chatcmd.client_change(g.opp, change[1], NOTICE_LEN))
        elif kind == "START":
            g.board = Board()
        elif kind == "TURN":
            s = int(a[0])
            g.board.hits = []
            if s == opp:
                g.opp_moves = []
            elif not g.doubled:
                g.my_turn = True
        elif kind == "ROLL":
            s, d = int(a[0]), (int(a[1]), int(a[2]))
            if g.opening is None:
                g.opening = (s, d[0], d[1])
                self.send_opening(g)
                g.my_turn = False
            elif s == me:
                g.my_dice, g.my_turn = d, False
                await self.send_go(g)
            else:
                g.opp_moves = []
                self.send_rec(roll_record(d, g.opp_id))
        elif kind == "STEP" and len(a) >= 3:
            s = int(a[0])
            g.board.step(s, a[1], a[2], len(a) > 3 and a[3] == "HIT")
            if s == opp:
                g.opp_moves.append("%s-%s" % (a[1], a[2]))
        elif kind == "UNDO" and len(a) >= 3:
            s = int(a[0])
            g.board.undo(s, a[1], a[2])
            if s == opp and g.opp_moves:
                g.opp_moves.pop()
        elif kind == "PASS":
            if int(a[0]) == opp:
                self.pvt("%s can't move." % g.opp)
        elif kind == "DBL":
            if int(a[0]) == opp:
                g.doubled, g.my_turn = True, False
                self.send_rec(record(S_DOUBLE))
        elif kind == "TAKE":
            g.cube, g.doubled = int(a[1]), False
        elif kind == "DROP":
            g.doubled = False
        elif kind == "CHT" and len(a) >= 2:
            # the client shows its own lines itself (0x53ea): only the opponent's are sent
            who = g.opp_id if a[0] == str(opp) else 0
            if who:
                r = record(S_CHAT, who)
                r[2:2 + 57] = pstr(" ".join(a[1:]), 56).ljust(57, b"\0")
                self.send_rec(r)
        elif kind == "END":
            await self.ended(g, a)

    async def send_go(self, g, points=0):
        """Your turn (or, with points, the game is over): the board as the
        core has it, the dice held for the client's roll, the mover's id."""
        v = await self.view(g)
        if v and (v["turn"] == g.seat or v["turn"] < 0):
            g.board = Board.from_view(v)
        r = record(S_GO)
        r[O_IMG:O_IMG + 162] = board_image(g.board, g.flip, self.hub_moves(g))
        r[O_CUBE] = min(g.cube, 64)
        r[O_DIE1], r[O_DIE2] = g.my_dice
        struct.pack_into(">H", r, O_POINTS, points)
        r[O_MOVER] = g.opp_id
        self.send_rec(r)
        if not points:
            g.client_turn = True

    def hub_moves(self, g):
        """The opponent's own record of the turn (screen points for the
        animation) when the opponent is an Outland player at this table whose
        steps the core took as they were."""
        other = self.hub.sessions.get((g.opp or "").lower())
        og = other.game if other else None
        if og and og.tid == g.tid and og.flip == g.flip and other.last_moves \
                and other.last_moves[0] == g.opp_moves:
            return other.last_moves[1]
        return None

    async def ended(self, g, a):
        g.state, g.client_turn, g.doubled = "over", False, False
        kind = a[0] if a else "?"
        if kind == "win" and len(a) >= 3:
            w, pts = int(a[1]), int(a[2])
            if w != g.seat:
                await self.send_go(g, points=max(1, pts))
                self.pvt("%s wins %d point%s." % (g.opp, pts, "" if pts == 1 else "s"))
            else:
                self.pvt("You win %d point%s." % (pts, "" if pts == 1 else "s"))
        elif kind == "resign" and len(a) >= 2:
            if int(a[1]) == g.seat:
                if not g.resigned_here:
                    await self.send_go(g, points=g.cube)
                    self.pvt("You resigned.")
            else:
                g.resign_asked = True
                r = record(S_RESIGN)
                struct.pack_into(">H", r, 2, 1)
                self.send_rec(r)
        elif kind == "timeout" and len(a) >= 2:
            if int(a[1]) == g.seat:
                self.pvt("You ran out of time.")
                await self.send_go(g, points=g.cube)
            else:
                self.pvt("%s ran out of time: you win." % g.opp)
                self.send_rec(record(S_LEFT))
        else:
            self.pvt("The game is over (%s)." % " ".join(a))
            self.send_rec(record(S_LEFT))
        self.hub.sync()

    async def resync(self, g, why):
        """Hand the turn back: kTurn to the opponent's color, then a go with the
        board and the dice as the core has them."""
        if why:
            self.pvt(why)
        self.send_rec(turn_record(1 - g.team))
        await self.send_go(g)

    async def on_move(self, r):
        g = self.game
        if not g or g.state != "playing" or not g.client_turn:
            log(self.sid, "a move outside the client's turn ignored")
            return
        img = r[O_IMG:O_IMG + 162]
        steps = image_moves(img, g.team, g.flip)
        v = await self.view(g)
        if v is not None and v["turn"] != g.seat:
            # the core passed this turn (no legal move) or the game moved on
            if steps:
                self.pvt("You had no legal move, so the turn passed.")
            g.client_turn, g.incomplete = False, 0
            self.last_moves = None
            await self.poll_game()
            return
        if steps is None:
            await self.resync(g, "That move could not be read; play it again.")
            return
        applied, last, ended = 0, "", False
        for st in steps:
            data, final = await self.core_req("MOV %s %s" % (g.tid, st))
            if not final.startswith("OK"):
                for _ in range(applied):
                    await self.core_req("UND %s" % g.tid)
                await self.resync(g, "%s is not legal here (%s)." % (st, err_text(final)))
                return
            applied += 1
            last = final
            if any(" END " in l for l in data):
                ended = True
                break
        if ended:
            g.client_turn = False
            self.last_moves = (steps, bytes(img[0:0x32]))
            return
        v = await self.view(g)
        mine = v is not None and v["turn"] == g.seat and v["phase"] == "move"
        if mine and (last.split()[1:] == ["more"] or (applied == 0 and v["moves"])):
            g.incomplete += 1
            if g.incomplete >= 3:
                await self.finish_turn(g, v)
                return
            for _ in range(applied):
                await self.core_req("UND %s" % g.tid)
            await self.resync(g, "Your turn is not complete: %s can still be played." % " or ".join(v["moves"][:4]))
            return
        if mine:
            _, final = await self.core_req("END %s" % g.tid)
            if not final.startswith("OK"):
                for _ in range(applied):
                    await self.core_req("UND %s" % g.tid)
                await self.resync(g, "The turn could not end (%s)." % err_text(final))
                return
        g.client_turn, g.incomplete = False, 0
        self.last_moves = (steps, bytes(img[0:0x32]))
        await self.poll_game()

    async def finish_turn(self, g, v):
        """The client keeps sending a turn the core finds incomplete: play the
        rest from the core's list of legal steps and say so."""
        played = []
        for _ in range(4):
            if not v or not v["moves"]:
                break
            m = v["moves"][0]
            _, final = await self.core_req("MOV %s %s" % (g.tid, m))
            if not final.startswith("OK"):
                break
            played.append(m)
            v = await self.view(g)
        await self.core_req("END %s" % g.tid)
        self.pvt("The server finished your turn with %s." % (" ".join(played) or "no move"))
        g.client_turn, g.incomplete = False, 0
        self.last_moves = None
        await self.poll_game()

    async def on_double(self, r):
        g = self.game
        if not g or g.state != "playing" or not g.client_turn:
            return
        why = ("Outland's cube is offered before the roll, and here the dice are rolled when your turn starts, "
               "so you can't double from Outland. Roll and play on.")
        v = await self.view(g)
        if v is not None and v["turn"] != g.seat:
            self.pvt(why)               # the turn has passed already (no legal move): nothing to hand back
            return
        await self.resync(g, why)

    async def on_answer_double(self, r):
        g = self.game
        if not g or g.state != "playing" or not g.doubled:
            return
        word = "take" if r[0] == C_TAKE else "drop"
        _, final = await self.core_req("MOV %s %s" % (g.tid, word))
        if not final.startswith("OK"):
            self.pvt("Can't %s: %s" % (word, err_text(final)))
        g.doubled = False
        await self.poll_game()

    async def on_resign(self, r):
        g = self.game
        if not g or g.state != "playing":
            return
        level = max(1, min(3, struct.unpack_from(">H", r, 2)[0]))
        _, final = await self.core_req("RES %s" % g.tid)
        if not final.startswith("OK"):
            ans = record(S_RESIGNRET)
            self.send_rec(ans)
            self.pvt("Can't resign: %s" % err_text(final))
            return
        g.resigned_here = True
        ans = record(S_RESIGNRET)
        struct.pack_into(">H", ans, 2, 1)
        self.send_rec(ans)
        g.state, g.client_turn = "over", False
        await self.send_go(g, points=g.cube * level)
        self.hub.sync()

    async def on_answer(self, r):
        g = self.game
        if g and g.resign_asked:
            g.resign_asked = False
            if struct.unpack_from(">H", r, 2)[0] == 0:
                self.pvt("The game is over: %s resigned." % g.opp)
                self.send_rec(record(S_LEFT))

    async def on_draw(self, r):
        self.pvt("Draws are not available here: play on, or resign.")
        self.send_rec(record(S_DRAWRET, 0))

    async def on_away(self, r):
        g = self.game
        if g and g.state == "playing":
            await self.core_req("RES %s" % g.tid)
            g.resigned_here = True
        if g and g.state != "waiting":
            self.game = None
        self.hub.sync()

    async def on_relobby(self, r):
        """Back in the lobby. A finished game is done with; one still running in the core goes on there."""
        g = self.game
        if g and g.state == "over":
            self.game = None
        elif g and g.state == "playing":
            v = await self.view(g)
            if v is not None and v["turn"] < 0:
                self.game = None
            else:
                self.release(g, v["turn"] if v else -1)
                return
        self.hub.sync()

    async def on_chat(self, r):
        g = self.game
        text = chat_text(read_pstr(r, 2))
        if not text:
            return
        if not g or g.state not in ("playing", "over"):
            self.pvt("You are not at a table.")
            return
        for i in range(0, min(len(text), 280), 56):
            await self.core_req("CHT %s %s" % (g.tid, text[i:i + 56]))
        if g.state == "playing":
            await self.poll_game()

    async def on_lobby_chat(self, r):
        text = read_pstr(r, 2)
        if await self.chat.handle(text):
            return
        out = record(S_LOBBYCHAT, self.pid)
        out[2:2 + 254] = pstr(text, 253).ljust(254, b"\0")
        self.hub.broadcast(out, skip=self)          # the sender's client shows its own line

    @property
    def chat(self):
        """The lobby chat's commands (adapters/chatcmd.py)."""
        if self._chat is None:
            self._chat = lobby_console(lambda lines: [self.pub(l) for l in lines], self.core_req, self.name,
                                       "Backgammon", {"list": self.list_cmd, "resume": self.resume_cmd,
                                                      "open": self.open_cmd, "close": self.close_cmd},
                                       BG_INVITE_HERE, width=200)
        return self._chat

    async def list_cmd(self, cmd):
        games, _ = await self.running()
        for line in game_lines(games, "Backgammon") + open_lines(self.hub.tables, self.name, join=False):
            self.pub(line)

    async def resume_cmd(self, cmd):
        await self.resume(cmd.args[0] if cmd.args else "")

    async def open_cmd(self, cmd):
        await self.open_table()

    async def close_cmd(self, cmd):
        if self.game and self.game.state == "waiting":
            await self.leave_open()
            self.pub("Your table is closed.")
            self.hub.sync()
        else:
            self.pub("You have no open table.")

    async def open_table(self):
        if self.game and self.game.state in ("waiting", "invite", "playing", "challenged"):
            self.pub("You have a table already (%s)." % self.game.tid)
            return
        _, final = await self.core_req("NEW %s standard cube=0" % BACKGAMMON)
        if final.startswith("OK"):
            tid = final.split()[1]
            _, final = await self.core_req("JON %s 0" % tid)
        if not final.startswith("OK"):
            self.pub("Can't open a table: %s" % err_text(final))
            return
        self.game = Game(tid, 0, None, 0, "waiting")
        self.pub("Your table %s is open: web and telnet players can join it there. When someone sits down you "
                 "get their challenge. /close closes it." % tid)
        self.hub.sync()

    async def on_watch(self, r):
        self.pub("Watching is not available here yet.")
        self.send_rec(record(S_OKWATCH, 0))

    # ---- the connection --------------------------------------------------------------
    async def run(self, poll_secs):
        self.last_moves = None
        self.ctl(cmd_start(self.key))
        tasks = [asyncio.ensure_future(self.worker()), asyncio.ensure_future(self.poller(poll_secs))]
        read = asyncio.ensure_future(self.r.read(4096))
        started = self.loop.time()
        try:
            while not self.closing:
                done, _ = await asyncio.wait({read}, timeout=0.1)
                if read in done:
                    data = read.result()
                    if not data:
                        break
                    if self.ticket is not None and not self.signed:
                        self.ticket.touch()
                    self.link.receive(data)
                    read = asyncio.ensure_future(self.r.read(4096))
                self.link.tick()
                now = self.loop.time()
                if self.signed and now - self.link.last_tx > KEEPALIVE_SECS:
                    self.ctl(b"T")
                if not self.signed and not self.refused and now - started > LOGIN_SECS:
                    log(self.sid, "no login")
                    break
                if self.w.transport.get_write_buffer_size() > 256 * 1024:
                    break
        finally:
            self.closing = True
            for t in tasks + [read]:
                t.cancel()
            if self.in_lobby:
                self.hub.leave(self)
            rv = self.rv
            if rv is not None:
                rv.channel_closed()
            if self.core is not None:
                for g in (self.game, rv.game if rv else None):
                    if g and g.state == "challenged":     # nobody to play: the table goes
                        try:
                            await asyncio.wait_for(self.core_req("LVE %s" % g.tid), 5)
                        except (CoreError, OSError, asyncio.TimeoutError):
                            pass
                await self.core.finish()        # the session ends with the connection


# The lobby commands (both lobbies' chat; adapters/chatcmd.py, docs/chat-commands.md), as /help lists them
# and the lobby says on entering. Invitations come and go through the client's own windows, so /invite,
# /join and /accept answer with a pointer to them (inviting a player who waits at an open table sits you
# down there).
COMMANDS = ["list", "resume", "open", "close", "decline", "who", "tables", "top", "score", "motd", "time", "help"]
COMMANDS_LINE = chatcmd.commands_line(COMMANDS)
HELP_LINES = chatcmd.help_lines(COMMANDS)
BG_INVITE_HERE = "Not available here. Pick the player in the player list and click Invite."
RV_INVITE_HERE = "Not available here. Pick the player in the player list and choose Invite from the Game menu."
ACCEPT_HERE = "Not available here. Answer the invitation in its window."


def lobby_console(say, req, me, label, handlers, invite_here, width=0):
    """The lobby chat's commands: the shared set, with this lobby's own /list, /resume, /open and /close."""
    async def invite(cmd):
        await console.say(invite_here)

    async def accept(cmd):
        await console.say(ACCEPT_HERE)

    console = chatcmd.Console(say, req, me, COMMANDS, label=label, width=width,
                              handlers=dict(handlers, invite=invite, join=invite, accept=accept))
    return console


def waiting_line(games):
    """The lobby line for running games that wait for the opponent's move: [(table, seat, opponent, turn)]."""
    if len(games) == 1:
        tid, _, opp = games[0][:3]
        return ("Your game with %s at table %s waits for %s's move. Type /resume in the lobby chat to bring it "
                "back." % (opp, tid, opp))
    parts = ["with %s at table %s" % (x[2], x[0]) for x in games]
    return ("Your games %s and %s wait for your opponents' moves. Type /resume and a table number in the lobby "
            "chat to bring one back." % (", ".join(parts[:-1]), parts[-1]))


def challenge_record(pid):
    """kChallenge: [1] and [2] the challenger's id, [4..5] the match length."""
    r = record(S_CHALLENGE, pid)
    r[2] = pid
    struct.pack_into(">H", r, 4, 1)
    return r


def roll_record(dice, mover):
    r = record(S_ROLL)
    r[O_DIE1], r[O_DIE2] = dice
    r[O_MOVER] = mover
    return r


def turn_record(team):
    """kTurn: the client sets the turn to [1] (0 or 1)."""
    return record(S_TURN, team)


# ---- Reversi -----------------------------------------------------------------------------
#
# Outland Reversi 1.1.1 (Delta Tao, 1994) on its own service channel: a stream of
# commands, one command byte each, some with a length field in the Gateway's 1-or-2
# byte encoding. The client numbers
# the players in its list 0-23 (24 means nobody) and keeps no names but the ones sent
# to it; it calls itself "You". It checks moves itself, applies its own move at once
# and plays the moves it is sent. Both clients count the moves (a pass is a move) to
# know whose disc comes next, so every pass of the opponent is sent as a move.

REVERSI = "reversi/1"
RV_SERVICE_ID = b"Flip"     # Reversi's Neti 256
RV_CREATOR = b"CRev"        # Reversi's creator
RV_PLAYERS = 24             # the client's player table; id 24 is nobody
RV_NOBODY = 24
RV_SERVER = "(ParlorNet)"   # the server's notices come from this player: not a name anyone can have
RV_NAME_MAX = 47            # the client keeps a name of at most 48 bytes with its NUL

# client -> server
RV_C_TALK, RV_C_INVITE, RV_C_ANSWER, RV_C_MOVE, RV_C_BACK, RV_C_HELLO = 0x14, 0x15, 0x16, 0x17, 0x18, 0x19
RV_C_GAMETALK, RV_C_COMPUTER, RV_C_FAMERANGE, RV_C_FAMESELF, RV_C_END = 0x1a, 0x1b, 0x1c, 0x1d, 0x1e
RV_C_LEN = {0x15: 3, 0x16: 3, 0x17: 3, 0x18: 1, 0x19: 5, 0x1b: 3, 0x1c: 8, 0x1d: 4, 0x1e: 2}   # 0x14, 0x1a: varlen
RV_NAMES = {0x14: "talk", 0x15: "invite", 0x16: "answer", 0x17: "move", 0x18: "back to the waiting room",
            0x19: "hello", 0x1a: "game talk", 0x1b: "computer game over", 0x1c: "hall of fame query",
            0x1d: "hall of fame query", 0x1e: "game over"}
# server -> client
RV_S_JOIN, RV_S_TALK, RV_S_MEET, RV_S_YOUARE, RV_S_GAMEINFO, RV_S_START = 0x5a, 0x5b, 0x5c, 0x5d, 0x5e, 0x5f
RV_S_MOVE, RV_S_STATUS, RV_S_INVITE, RV_S_MESSAGE, RV_S_YOURMOVE, RV_S_END = 0x60, 0x61, 0x62, 0x63, 0x64, 0x65
RV_S_GAMETALK, RV_S_HISTORY, RV_S_HELLO = 0x66, 0x68, 0x69
# player status in the client's list
RV_GONE, RV_WAITING, RV_PLAYING = 0, 1, 2
# The client draws status 1 with an hourglass (its icon 1024), 2 with a board (1025), 3 with icon 1027, and any
# other status with no icon (playerUpdate, CODE 1 0x5674). Players read the hourglass as busy, so the bot,
# which is always free, gets a status of its own that the client draws with no icon.
RV_BOT_FREE = 4
# move kinds (the third byte of a move): a pass is 2
RV_PLACE, RV_PASS = 1, 2
# 0x65's result, as the receiver sees it
RV_DRAW, RV_WON, RV_LOST, RV_LEFT = 1, 2, 3, 4
# 0x63's messages (the client's STR# 129): 1, 2 and 9-11 also close its "Inviting your party!" window
RV_M_DECLINED, RV_M_NOT_FREE, RV_M_CANCELLED = 1, 9, 13


def rv_varlen(cmd, payload):
    """A command with a length field: the field holds the whole command's length, one byte up to 0x7e,
    else two (0x80 | v >> 7, v & 0x7f), as the client's setInclusiveVarLength writes it."""
    n = len(payload) + 2
    if n <= 0x7e:
        return bytes([cmd, n]) + payload
    n += 1
    return bytes([cmd, 0x80 | (n >> 7) & 0x7f, n & 0x7f]) + payload


def rv_varlen_of(buf, at=1):
    """(length of the whole command, size of the field), or None if the field is not all here."""
    if len(buf) <= at:
        return None
    if buf[at] & 0x80:
        if len(buf) <= at + 1:
            return None
        return ((buf[at] & 0x7f) << 7) | buf[at + 1], 2
    return buf[at], 1


def rv_cstr(text, limit):
    return text.replace("\0", "").encode("mac_roman", "replace")[:limit] + b"\0"


def rv_join(pid, name):
    return rv_varlen(RV_S_JOIN, bytes([pid]) + rv_cstr(name, RV_NAME_MAX))


def rv_meet(pid, status, name):
    return rv_varlen(RV_S_MEET, bytes([pid, status]) + rv_cstr(name, RV_NAME_MAX))


def rv_talk(pid, text, game=False):
    return rv_varlen(RV_S_GAMETALK if game else RV_S_TALK, bytes([pid]) + rv_cstr(text, 240))


def rv_message(code):
    return bytes([RV_S_MESSAGE]) + struct.pack(">H", code)


def rv_start(first, second, index=0, flags=0):
    """0x5f: the player who moves first (and plays the first color), the other, the game's index (0-11),
    flags (bit 0 kept by the client, use unknown) and a word the client does not read."""
    return bytes([RV_S_START, first, second, index, flags, 0, 0])


def rv_square(packed):
    """A move's square byte -> a reversi/1 square. The client's board is [i][j] with i in the high nibble;
    with i as the column (a-h) and 8 - j as the row its starting position is reversi/1's (seat 0 on d5 and
    e4), so its first player is seat 0. Its window shows that board turned left to right."""
    i, j = packed >> 4, packed & 15
    if i > 7 or j > 7:
        return None
    return "%s%d" % ("abcdefgh"[i], 8 - j)


def rv_packed(sq):
    x, row = ord(sq[0]) - ord("a"), int(sq[1:])
    return (x << 4) | (8 - row)


class RvBoard:
    """An 8x8 board kept from the core's PUT events, for one question: does a seat have a move?"""

    def __init__(self):
        self.cells = {(3, 3): 1, (4, 4): 1, (4, 3): 0, (3, 4): 0}     # (x, y), y = row - 1: as reversi/1 starts

    @staticmethod
    def xy(sq):
        return ord(sq[0]) - ord("a"), int(sq[1:]) - 1

    def put(self, seat, squares):
        for sq in squares:
            self.cells[self.xy(sq)] = seat

    def can_move(self, seat):
        for x in range(8):
            for y in range(8):
                if (x, y) in self.cells:
                    continue
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        cx, cy, run = x + dx, y + dy, 0
                        while (dx or dy) and self.cells.get((cx, cy)) == 1 - seat:
                            cx, cy, run = cx + dx, cy + dy, run + 1
                        if run and self.cells.get((cx, cy)) == seat:
                            return True
        return False


class RvGame:
    """One Outland player's Reversi game: a reversi/1 table in the core."""

    def __init__(self, tid, seat, opp, state, resume=False):
        self.tid, self.seat, self.opp, self.state = tid, seat, opp, state
        # waiting (my open table), challenged (my invitation out), playing, over, elsewhere (played on
        # the web or by telnet: this client cannot show it)
        self.resume = resume        # the game ran before the client saw it: its own moves are sent too
        self.cursor = 0
        self.names = [None, None]
        self.started = False        # GameInfo and GameStart sent
        self.turn = -1
        self.told_turn = False
        self.board = RvBoard()
        self.resigned_here = False


class ReversiService:
    """Outland Reversi on one service channel of a Gateway's Session: the Reversi lobby (a Hub of its
    own), invitations and games. Its handlers run in the Session's worker, under the Session's lock."""

    def __init__(self, session, hub, chan):
        self.s, self.hub, self.chan = session, hub, chan
        self.buf = bytearray()
        self.hello = False
        self.in_lobby = False
        self.ids = {}               # lower name -> id in this client's list
        self.names = {}             # id -> name
        self.status = {}            # id -> status last sent
        self.pinned = set()         # lower names kept in the list while a game needs them
        self.pid = None
        self.server_id = None
        self.game = None
        self.asked = None           # an invitation the client is answering: (lower name, kind, table or flags)
        self.outgoing = None        # my invitation to an Outland player: (lower name, flags)
        self.request_open = False   # the client's "Inviting your party!" window is up, waiting for an answer
        self.invited = {}           # lower name -> table id: invitations shown from IVL
        self.told = set()
        self.released = {}          # table id -> (seat, opponent, turn last seen): running games left in the core
        self._chat = None

    # ---- what the Hub and the Session use ------------------------------------------------
    @property
    def name(self):
        return self.s.name

    @property
    def closing(self):
        return self.s.closing

    @closing.setter
    def closing(self, value):
        self.s.closing = value

    def core_req(self, text):
        return self.s.core_req(text)

    def locked(self, fn, *args):
        return self.s.locked(fn, *args)

    def busy(self):
        """In a game (finished games too, until the board is put away), or with an invitation out."""
        return bool(self.game and self.game.state != "waiting") or self.outgoing is not None

    def send(self, data):
        if data[:1] == bytes([RV_S_START]) or (data[:1] == bytes([RV_S_MESSAGE]) and data[2] in (1, 2, 9, 10, 11)):
            self.request_open = False   # GameStart and these messages close the client's invitation window
        if self.chan is not None:
            self.s.link.send(self.chan, bytes(data))

    def log(self, text):
        log(self.s.sid, "reversi: " + text)

    # ---- the client's list ------------------------------------------------------------------
    def add_player(self, name, status, meet=False):
        """A name into the client's list (Meet when it enters, Join later): its id, or None when the list
        is full."""
        k = name.lower()
        if k in self.ids:
            return self.ids[k]
        used = set(self.names)
        pid = next((i for i in range(RV_PLAYERS) if i not in used), None)
        if pid is None:
            return None
        self.ids[k], self.names[pid], self.status[pid] = pid, name, status
        if meet or status != RV_WAITING:
            self.send(rv_meet(pid, status, name))
        else:
            self.send(rv_join(pid, name))
        return pid

    def drop_player(self, name):
        k = name.lower()
        pid = self.ids.get(k)
        if pid is None or pid in (self.pid, self.server_id):
            return
        if k in self.pinned:
            return
        del self.ids[k]
        self.names.pop(pid, None)
        self.status.pop(pid, None)
        self.send(bytes([RV_S_STATUS, pid, RV_GONE]))

    def set_status(self, name, status):
        pid = self.ids.get(name.lower())
        if pid is not None and pid not in (self.pid, self.server_id) and self.status.get(pid) != status:
            self.status[pid] = status
            self.send(bytes([RV_S_STATUS, pid, status]))

    def need_player(self, name):
        """An id for a player the client must know (an opponent, an inviter): a full list makes room."""
        pid = self.add_player(name, RV_WAITING)
        if pid is None:
            spare = next((n for k, n in sorted(self.ids.items()) if k not in self.pinned
                          and self.ids[k] not in (self.pid, self.server_id)), None)
            if spare:
                self.drop_player(spare)
                pid = self.add_player(name, RV_WAITING)
        return pid

    def lobby_enter(self, everyone):
        """After the hello: who the client is (it calls itself "You"), then everyone already here, the
        server's notices among them."""
        self.ids, self.names, self.status = {}, {}, {}
        self.pid = 0
        self.ids[self.name.lower()], self.names[0], self.status[0] = 0, self.name, RV_WAITING
        self.send(bytes([RV_S_YOUARE, 0]))
        self.server_id = self.add_player(RV_SERVER, RV_PLAYING, meet=True)
        for k, (name, avail) in everyone.items():
            if k != self.name.lower():
                self.add_player(name, self.status_for(name, avail), meet=True)
        # The client shows the watch cursor from the moment it connects until a line of room history (0x68)
        # arrives (connMade sets the flag at A5-0x3e42, only handleActionHistory clears it). Kind 4 prints
        # its text as it is, so an empty one changes nothing on the screen.
        self.send(rv_varlen(RV_S_HISTORY, bytes([4]) + b"\0\0\0\0" + b"\0" + b"\0"))

    def status_for(self, name, avail):
        if name.lower() in self.hub.bots:
            return RV_BOT_FREE
        return RV_WAITING if avail else RV_PLAYING

    def lobby_player(self, name, avail, new):
        if name.lower() == self.name.lower():
            return
        if name.lower() not in self.ids:
            self.add_player(name, self.status_for(name, avail))
        self.set_status(name, self.status_for(name, avail))

    def lobby_gone(self, name):
        self.drop_player(name)

    def offer_unanswered(self, k):
        if self.outgoing and self.outgoing[0] == k:
            self.outgoing = None
            self.send(rv_message(RV_M_DECLINED))

    def hub_polled(self):
        g = self.game
        if g and g.state == "waiting":
            asyncio.ensure_future(self.locked(self.check_waiting))
        elif g and g.state == "challenged":
            asyncio.ensure_future(self.locked(self.check_challenge))

    def line(self, text):
        """One line from the server's player in the waiting room, as it is (the message of the day keeps its
        layout)."""
        if self.server_id is not None:
            self.send(rv_talk(self.server_id, text[:200] or " "))

    def notice(self, text):
        """A line from the server's player: into the game's talk window during a game, else the lobby's."""
        if self.server_id is None:
            return
        game = bool(self.game and self.game.started and self.game.state in ("playing", "over"))
        text = " ".join(text.split())
        while text:
            cut = text[:200].rfind(" ") if len(text) > 200 else len(text)
            cut = cut if cut > 0 else 200
            self.send(rv_talk(self.server_id, text[:cut], game))
            text = text[cut:].strip()

    # ---- input ------------------------------------------------------------------------------
    def feed(self, payload):
        """The channel's bytes -> whole commands (into the Session's inbox)."""
        self.buf += payload
        out = []
        while self.buf:
            c = self.buf[0]
            if c in (RV_C_TALK, RV_C_GAMETALK):
                v = rv_varlen_of(self.buf)
                if v is None:
                    break
                n = v[0]
                if n < 1 + v[1]:
                    self.log("a bad length; %d bytes dropped" % len(self.buf))
                    del self.buf[:]
                    break
            elif c in RV_C_LEN:
                n = RV_C_LEN[c]
            else:
                self.log("command 0x%02x unknown; %d bytes dropped" % (c, len(self.buf)))
                del self.buf[:]
                break
            if len(self.buf) < n:
                break
            out.append(bytes(self.buf[:n]))
            del self.buf[:n]
        return out

    async def command(self, cmd):
        c = cmd[0]
        if self.s.core is not None and c not in (RV_C_FAMERANGE, RV_C_FAMESELF):
            self.s.core.user_input()
        self.log("< %s (0x%02x)" % (RV_NAMES.get(c, "?"), c))
        if c == RV_C_HELLO:
            if not self.hello:
                self.hello = True
                self.send(bytes([RV_S_HELLO]))
                self.in_lobby = True
                await self.hub.enter(self)
                for line in await self.s.s_motd():
                    self.line(line)
                self.line(RV_BOT_HOWTO)
                await self.rejoin()
            return
        if not self.in_lobby:
            return
        handler = {RV_C_TALK: self.on_talk, RV_C_GAMETALK: self.on_game_talk, RV_C_INVITE: self.on_invite,
                   RV_C_ANSWER: self.on_answer, RV_C_MOVE: self.on_move, RV_C_BACK: self.on_back}.get(c)
        if handler:
            await handler(cmd)

    @staticmethod
    def text_of(cmd):
        v = rv_varlen_of(cmd)
        return cmd[1 + v[1]:].split(b"\0")[0].decode("mac_roman", "replace") if v else ""

    async def on_talk(self, cmd):
        text = self.text_of(cmd)
        if await self.chat.handle(text):
            return
        for m in self.hub.members():           # the client prints its own line when it comes back
            pid = m.ids.get(self.name.lower())
            if pid is not None:
                m.send(rv_talk(pid, text))

    @property
    def chat(self):
        """Public talk's commands (adapters/chatcmd.py)."""
        if self._chat is None:
            self._chat = lobby_console(lambda lines: [self.line(l) for l in lines], self.core_req, self.name,
                                       "Reversi", {"list": self.list_cmd, "resume": self.resume_cmd,
                                                   "open": self.open_cmd, "close": self.close_cmd},
                                       RV_INVITE_HERE, width=200)
        return self._chat

    async def list_cmd(self, cmd):
        games, _ = await self.running()
        for line in game_lines(games, "Reversi") + open_lines(self.hub.tables, self.name, join=False):
            self.line(line)

    async def resume_cmd(self, cmd):
        await self.resume(cmd.args[0] if cmd.args else "")

    async def open_cmd(self, cmd):
        await self.open_table()

    async def close_cmd(self, cmd):
        g = self.game
        if g and g.state == "waiting":
            await self.leave_open()
            self.notice("Your table is closed.")
            self.hub.sync()
        else:
            self.notice("You have no open table.")

    async def on_game_talk(self, cmd):
        g = self.game
        text = chat_text(self.text_of(cmd))
        if not text:
            return
        if not g or g.state not in ("playing", "over") or not g.started:
            self.notice("You are not at a table.")
            return
        for i in range(0, min(len(text), 280), 56):      # the line comes back as the table's CHT
            await self.core_req("CHT %s %s" % (g.tid, text[i:i + 56]))
        if g.state == "playing":
            await self.poll_game()

    # ---- invitations ---------------------------------------------------------------------------
    async def on_invite(self, cmd):
        pid, first = cmd[1], cmd[2] & 1          # flags bit 0: the inviter moves first (seat 0)
        self.request_open = True                 # the client opened its window as it sent this
        target = self.names.get(pid)
        if not target or pid in (self.pid, self.server_id):
            self.send(rv_message(RV_M_NOT_FREE))
            if pid == self.server_id:
                self.notice("%s is how the server talks to you. Invite a player, or the bot." % RV_SERVER)
            return
        if self.game and self.game.state != "waiting":
            self.send(rv_message(RV_M_NOT_FREE))
            return
        other = self.hub.sessions.get(target.lower())
        if other:
            if other.busy() or other.asked:
                self.send(rv_message(RV_M_NOT_FREE if other.busy() else 2))
                return
            self.hub.offers.add((self.name.lower(), target.lower()))
            self.outgoing = (target.lower(), first)
            other.offered(self.name, first)
            self.hub.sync()
            return
        tab = self.hub.open_table_of(target)
        if tab is None:
            await self.invite(target, first)
            return
        await self.leave_open()
        _, final = await self.core_req("JON %s" % tab["id"])
        if not final.startswith("OK"):
            self.send(rv_message(RV_M_DECLINED))
            self.notice("Can't join %s's table: %s" % (target, err_text(final)))
            return
        self.game = RvGame(tab["id"], int(final.split()[1]), target, "playing")
        self.hub.sync()
        await self.poll_game()

    def offered(self, inviter, first):
        """Another Outland player's invitation: the client's dialog (bit 0: "You would move last")."""
        pid = self.need_player(inviter)
        self.asked = (inviter.lower(), "outland", first)
        self.send(bytes([RV_S_INVITE, pid, first]))

    async def invite(self, target, first):
        """An invitation to a player who is not on Outland Reversi here and not waiting at an open table (a
        web or telnet player, the bot): a private table, me in the seat I chose, the other held for them
        (INV). The client keeps its "Inviting your party!" window up until the game starts (GameStart closes
        it) or the invitation ends (message 1)."""
        await self.leave_open()
        seat = 0 if first else 1
        _, final = await self.core_req("NEW %s standard vis=private" % REVERSI)
        if final.startswith("OK"):
            tid = final.split()[1]
            _, final = await self.core_req("JON %s %d" % (tid, seat))
            if final.startswith("OK"):
                _, final = await self.core_req("INV %s %s %d" % (tid, target, 1 - seat))
                if not final.startswith("OK"):
                    await self.core_req("LVE %s" % tid)
        if not final.startswith("OK"):
            self.send(rv_message(RV_M_DECLINED))
            self.notice("There is no player called %s here." % target if final.startswith("ERR NOPLAYER")
                        else "Can't invite %s: %s" % (target, err_text(final)))
            return
        self.game = RvGame(tid, seat, target, "challenged")
        if final == "OK offline":
            self.notice("%s is not online now. Your invitation will wait at table %s for an hour." % (target, tid))
        elif target.lower() in self.hub.bots:
            self.notice("%s is the server's bot. It will accept in a few seconds." % target)
        else:
            self.notice("Your invitation to %s is waiting at table %s. %s can answer it on the web or by telnet."
                        % (target, tid, target))
        self.hub.sync()

    async def check_challenge(self):
        """My invitation to a player elsewhere: the game started (it is played from its first event), or
        the invitation ended."""
        g = self.game
        if not g or g.state != "challenged":
            return
        data, final = await self.core_req("SIN %s 0" % g.tid)
        if final.startswith("ERR"):
            self.game = None
            self.send(rv_message(RV_M_DECLINED))
            self.hub.sync()
            return
        for l in data:
            p = l.split(" ", 3)
            if len(p) < 4 or p[0] != "EVT":
                continue
            if p[3] == "START":
                g.state = "playing"
                self.hub.sync()
                await self.poll_game()
                return
            u = uninvite_of(p[3])
            if u and u[0].lower() == (g.opp or "").lower():
                await self.core_req("LVE %s" % g.tid)
                self.game = None
                self.notice({"declined": "%s declined your invitation.",
                             "expired": "Your invitation to %s expired."}.get(u[1], "Your invitation to %s ended.")
                            % u[0])
                self.send(rv_message(RV_M_DECLINED))
                self.hub.sync()
                return

    async def check_invites(self):
        """Invitations to me made anywhere else (IVL): a Reversi one comes as the inviter's invitation (the
        client's dialog), one at a time and only while I am free; another game's, a notice once."""
        data, final = await self.core_req("IVL")
        if not final.startswith("OK"):
            return
        invs = parse_invites(data)
        live = {i["id"] for i in invs}
        for k in [k for k, tid in self.invited.items() if tid not in live]:
            del self.invited[k]
        self.told &= live
        for i in invs:
            k = i["from"].lower()
            if i["key"] != REVERSI:
                if i["key"] == BACKGAMMON and self.s.in_lobby:
                    continue                # the Backgammon window shows it as a challenge
                if i["id"] not in self.told:
                    self.told.add(i["id"])
                    self.notice("%s invites you to %s at table %s. You can play it %s."
                                % (i["from"], i["key"].split("/")[0].capitalize(), i["id"], WHERE.text))
                continue
            if k in self.invited or k in self.hub.sessions or self.busy() or self.asked or self.invited:
                continue
            self.invited[k] = i["id"]
            pid = self.need_player(i["from"])
            self.asked = (k, "table", i["id"])
            self.send(bytes([RV_S_INVITE, pid, 0 if i["seat"] == 0 else 1]))

    async def on_answer(self, cmd):
        """0x16: [1] a player, [2] 1 yes / 0 no. The answer to an invitation shown, or (with 0, from the
        "Inviting your party!" window) my own invitation called off."""
        pid, yes = cmd[1], cmd[2] != 0
        who = (self.names.get(pid) or "").lower()
        if self.asked and self.asked[0] == who:
            asked, self.asked = self.asked, None
            if asked[1] == "table":
                self.invited.pop(who, None)
                await self.table_answer(asked[2], yes)
            else:
                await self.offer_answer(who, yes, asked[2])
            return
        if not yes and self.outgoing and self.outgoing[0] == who:
            self.outgoing, self.request_open = None, False
            self.hub.offers.discard((self.name.lower(), who))
            self.hub.sync()
            return
        g = self.game
        if not yes and g and g.state == "challenged" and (g.opp or "").lower() == who:
            await self.core_req("LVE %s" % g.tid)          # the invitation goes with the table
            self.game, self.request_open = None, False
            self.hub.sync()
            return
        self.log("an answer to nothing")
        if yes:
            self.send(rv_message(RV_M_CANCELLED))

    async def table_answer(self, tid, yes):
        if not yes:
            await self.core_req("UNV %s %s" % (tid, self.name))
            return
        await self.leave_open()
        _, final = await self.core_req("JON %s" % tid)
        if not final.startswith("OK"):
            self.notice("Can't sit down at table %s: %s" % (tid, err_text(final)))
            return
        t = next((x for x in parse_tables((await self.core_req("LST table=%s" % tid))[0]) if x["id"] == tid), None)
        seat = int(final.split()[1])
        opp = t["seated"][1 - seat] if t and len(t["seated"]) == 2 else None
        self.game = RvGame(tid, seat, opp if opp != "-" else None, "playing")
        self.hub.sync()
        await self.poll_game()

    async def offer_answer(self, inviter, yes, first):
        key = (inviter, self.name.lower())
        ch = self.hub.sessions.get(inviter)
        if key not in self.hub.offers or ch is None or (ch.game and ch.game.state != "waiting"):
            self.hub.offers.discard(key)
            if yes:
                self.send(rv_message(RV_M_CANCELLED))
            return
        self.hub.offers.discard(key)
        ch.outgoing = None
        if not yes:
            ch.send(rv_message(RV_M_DECLINED))
            self.hub.sync()
            return
        await self.leave_open()
        asyncio.ensure_future(ch.locked(ch.start_offer, self, first))

    async def start_offer(self, target, first):
        """The inviter's side of a game between two Outland players: the table, my seat (0 if I move
        first), then the other sits down."""
        await self.leave_open()
        seat = 0 if first else 1
        _, final = await self.core_req("NEW %s standard" % REVERSI)
        if final.startswith("OK"):
            tid = final.split()[1]
            _, final = await self.core_req("JON %s %d" % (tid, seat))
        if not final.startswith("OK"):
            self.send(rv_message(RV_M_DECLINED))
            self.notice("Can't open a table: %s" % err_text(final))
            return
        self.game = RvGame(tid, seat, target.name, "playing")
        asyncio.ensure_future(target.locked(target.join_offer, tid, 1 - seat, self))

    async def join_offer(self, tid, seat, ch):
        _, final = await self.core_req("JON %s %d" % (tid, seat))
        if not final.startswith("OK"):
            self.notice("Can't sit down at table %s: %s" % (tid, err_text(final)))
            asyncio.ensure_future(ch.locked(ch.abandon, tid))
            return
        self.game = RvGame(tid, seat, ch.name, "playing")
        self.hub.sync()
        await self.poll_game()

    async def abandon(self, tid):
        if self.game and self.game.tid == tid:
            await self.core_req("LVE %s" % tid)
            self.game = None
            self.send(rv_message(RV_M_DECLINED))
            self.hub.sync()

    # ---- tables -----------------------------------------------------------------------------------
    async def leave_open(self):
        g = self.game
        if g and g.state == "waiting":
            await self.core_req("LVE %s" % g.tid)
            self.game = None

    async def open_table(self):
        if self.game:
            self.notice("You have a table already (%s)." % self.game.tid)
            return
        _, final = await self.core_req("NEW %s standard" % REVERSI)
        if final.startswith("OK"):
            tid = final.split()[1]
            _, final = await self.core_req("JON %s 0" % tid)
        if not final.startswith("OK"):
            self.notice("Can't open a table: %s" % err_text(final))
            return
        self.game = RvGame(tid, 0, None, "waiting")
        self.notice("Your table %s is open: web and telnet players can join it there, and the game starts when "
                    "someone sits down. You move first. /close closes it." % tid)
        self.hub.sync()

    async def check_waiting(self):
        """Someone sat down at my open table: the game starts."""
        g = self.game
        if not g or g.state != "waiting":
            return
        t = self.hub.table(g.tid)
        if t is None:
            data, final = await self.core_req("LST table=%s" % g.tid)
            t = next((x for x in parse_tables(data) if x["id"] == g.tid), None)
            if t is None:
                self.game = None
                self.notice("Your table %s was closed." % g.tid)
                return
        if t["status"] != "playing":
            return
        g.opp, g.state = t["seated"][1 - g.seat], "playing"
        self.hub.sync()
        await self.poll_game()

    async def running(self):
        """My Reversi games running in the core: [(table, seat, opponent, whose turn)], oldest first; and my
        open table, if any."""
        me = self.name.lower()
        data, final = await self.core_req("LST")
        mine = sorted((t for t in parse_tables(data) if t["key"] == REVERSI and t["status"] in ("open", "playing")
                       and me in [n.lower() for n in t["seated"]]), key=lambda t: int(t["id"]))
        games, open_t = [], None
        for t in mine:
            seat = [n.lower() for n in t["seated"]].index(me)
            if t["status"] == "open":
                open_t = open_t or (t["id"], seat)
                continue
            data, final = await self.core_req("VUE %s" % t["id"])
            v = parse_reversi_view(data) if final.startswith("OK") else {"turn": -1, "clock": None}
            games.append((t["id"], seat, t["seated"][1 - seat], v["turn"], v["clock"] and v["clock"][1]))
        return games, open_t

    async def rejoin(self):
        """Into the waiting room with Reversi games running in the core: one where it is my move is shown
        again from its first move; the others wait in the core, said in one line (/resume brings one back,
        and the opponent's move shows it). Else an open table of mine is mine again. A board the client opens
        can only be left by giving the game up, so a game waiting for the opponent is not opened unasked."""
        games, open_t = await self.running()
        mine = [x for x in games if x[3] == x[1]]
        for tid, seat, opp, turn, _ in games:
            self.released[tid] = (seat, opp, turn)
        for line in game_lines(games, "Reversi", entering=True):
            self.line(line)
        waiting = [x for x in games if x[3] != x[1]]
        if waiting:
            self.notice(waiting_line(waiting).replace("lobby chat", "public talk"))
        self.line(COMMANDS_LINE)
        if mine:
            await self.bring_back(*mine[0][:3])
        elif open_t:
            self.game = RvGame(open_t[0], open_t[1], None, "waiting")
            self.notice("Your table %s is open; web and telnet players can join it." % open_t[0])

    async def bring_back(self, tid, seat, opp, why=None):
        self.released.pop(tid, None)
        if why:
            self.notice(why)
        self.game = RvGame(tid, seat, opp, "playing", resume=True)
        self.hub.sync()
        await self.poll_game()

    async def resume(self, arg):
        """/resume [table]: a running game shown again, the first one if no table is given."""
        if self.busy():
            self.notice("Finish your game first.")
            return
        if self.game and self.game.state == "waiting":
            self.notice("Your table %s is open. Type /close to close it first." % self.game.tid)
            return
        games, _ = await self.running()
        pick = [x for x in games if not arg or x[0] == arg]
        if not pick:
            self.notice("You have no Reversi game running at table %s." % arg if arg
                        else "You have no Reversi game running.")
            return
        if pick[0][3] != pick[0][1]:
            self.notice("It is %s's move. Putting the board away gives the game up." % pick[0][2])
        await self.bring_back(*pick[0][:3])

    async def check_released(self):
        """A game left in the core whose opponent has moved: shown again, while I am free in the room."""
        if self.game or self.asked or self.outgoing or not self.released:
            return
        for tid, (seat, opp, last) in list(self.released.items()):
            data, final = await self.core_req("VUE %s" % tid)
            v = parse_reversi_view(data) if final.startswith("OK") else None
            if v is None or v["turn"] < 0:
                del self.released[tid]
                continue
            self.released[tid] = (seat, opp, v["turn"])
            if v["turn"] == seat and last != seat:
                await self.bring_back(tid, seat, opp, "%s has moved in your game at table %s." % (opp, tid))
                return

    # ---- the game -----------------------------------------------------------------------------------
    async def poll_game(self):
        g = self.game
        if not g or g.state != "playing" or self.chan is None:
            return
        data, final = await self.core_req("SIN %s %d" % (g.tid, g.cursor))
        if final.startswith("ERR"):
            self.game = None
            return
        for l in data:
            p = l.split()
            if len(p) < 4 or p[0] != "EVT" or p[1] != g.tid:
                continue
            seq = int(p[2])
            if seq <= g.cursor:
                continue
            g.cursor = seq
            await self.event(g, p[3], p[4:])
            if self.game is not g or g.state != "playing":
                break
        if self.game is g and g.state == "playing" and g.started:
            if g.turn == g.seat and not g.told_turn:
                g.told_turn = True
                self.send(bytes([RV_S_YOURMOVE]))
            if g.resume and g.turn >= 0:
                g.resume = False

    async def event(self, g, kind, a):
        me, opp = g.seat, 1 - g.seat
        if kind == "CLIENT":            # what a player plays with: the opponent's, mid-game, is told
            c = chatcmd.client_of(a)
            if c and c[0] == opp and g.started and not g.resume:
                self.notice(chatcmd.client_change(g.opp or "Your opponent", c[1]))
            return
        if kind == "JOIN" and len(a) >= 2 and a[0].isdigit() and int(a[0]) < 2:
            g.names[int(a[0])] = a[1]
            if int(a[0]) == opp:
                g.opp = a[1]
        elif kind == "START":
            await self.begin(g)
        elif kind == "TURN" and a:
            g.turn = int(a[0])
            if g.turn != me:
                g.told_turn = False
        elif not g.started:
            return
        elif kind == "PUT" and len(a) >= 2:
            s = int(a[0])
            g.board.put(s, [a[1]] + [x for x in a[2:] if x != "FLP"])
            if s == opp:
                self.send(bytes([RV_S_MOVE, rv_packed(a[1]), RV_PLACE]))
            elif g.resume:
                # its own move, shown again: the client then looks for a move of its own and passes if it
                # has none, which would put its count of moves out of step
                if not g.board.can_move(me):
                    await self.elsewhere(g, "Your game at table %s goes on, but it can't be shown here from its "
                                            "start. Play it %s." % (g.tid, WHERE.text))
                    return
                self.send(bytes([RV_S_MOVE, rv_packed(a[1]), RV_PLACE]))
        elif kind == "PASS" and a:
            if int(a[0]) == opp:
                self.send(bytes([RV_S_MOVE, 0, RV_PASS]))     # the client passes for itself; the opponent's
        elif kind == "CHT" and len(a) >= 2:
            who = g.names[int(a[0])] if a[0].isdigit() and int(a[0]) < 2 else None
            pid = self.ids.get(who.lower()) if who else None
            text = " ".join(a[1:])
            if pid is None:
                self.send(rv_talk(self.server_id, "%s: %s" % (who or "watcher", text), True))
            else:
                self.send(rv_talk(pid, text, True))
        elif kind == "END":
            await self.ended(g, a)

    async def begin(self, g):
        """START: GameInfo (the two players' status: playing), then GameStart with the player who moves
        first; the client opens its board."""
        v = await self.core_req("VUE %s GC" % g.tid)
        brd = next((l.split() for l in v[0] if l.startswith("BRD ")), None)
        if brd and brd[1:3] != ["8", "8"]:
            await self.elsewhere(g, "Table %s's board is %sx%s; Outland Reversi plays on 8x8 only. Play it %s."
                                    % (g.tid, brd[1], brd[2], WHERE.text))
            return
        names = list(g.names)
        if None in names:
            data = (await self.core_req("LST table=%s" % g.tid))[0]
            t = next((x for x in parse_tables(data) if x["id"] == g.tid), None)
            if t:
                names = [n if n != "-" else None for n in t["seated"][:2]]
        names[g.seat] = self.name
        g.names = names
        g.opp = names[1 - g.seat] or g.opp or "?"
        self.pinned.add(g.opp.lower())
        ids = [self.pid if i == g.seat else self.need_player(g.opp) for i in range(2)]
        if None in ids:
            await self.elsewhere(g, "Your game at table %s can't be shown here. Play it %s." % (g.tid, WHERE.text))
            return
        self.send(bytes([RV_S_GAMEINFO, 0, ids[0], ids[1]]))
        self.send(rv_start(ids[0], ids[1]))
        g.started = True
        if not g.resume:
            self.notice("Table %s: you move %s. This board is the web's board mirrored left to right, and other "
                        "clients draw your discs %s." % (g.tid, "first" if g.seat == 0 else "second",
                                                         WEB_RV_COLOURS[g.seat]))
            say = chatcmd.parse_clients(v[0]).get(1 - g.seat, "")
            if say:                     # the client shows the opponent by name; the parlor adds with what
                self.notice("%s plays %s." % (g.opp, say))

    async def elsewhere(self, g, text):
        g.state = "elsewhere"
        self.notice(text)
        if self.request_open:           # the "Inviting your party!" window waits for an answer: close it
            self.send(rv_message(RV_M_DECLINED))
        self.hub.sync()

    async def ended(self, g, a):
        g.state = "over"
        kind = a[0] if a else "?"
        if kind == "draw":
            result = RV_DRAW
        elif kind == "win" and len(a) >= 2:
            result = RV_WON if int(a[1]) == g.seat else RV_LOST
        elif kind in ("resign", "timeout") and len(a) >= 2:
            mine = int(a[1]) == g.seat
            result = RV_LOST if mine else RV_LEFT if kind == "resign" else RV_WON
            if kind == "timeout":
                self.notice("You ran out of time." if mine else "%s ran out of time." % g.opp)
            elif not mine:
                self.notice("%s resigned." % g.opp)
        else:
            result = RV_LEFT
            self.notice("The game is over (%s)." % " ".join(a))
        if g.started and not g.resigned_here:
            self.send(bytes([RV_S_END, result]))
            self.send(bytes([RV_S_GAMEINFO, 0, RV_NOBODY, RV_NOBODY]))
        self.hub.sync()

    async def on_move(self, cmd):
        g = self.game
        sq, kind = rv_square(cmd[1]), cmd[2]
        if not g or g.state != "playing" or not g.started:
            self.log("a move outside a game ignored")
            return
        if kind == RV_PASS:
            self.log("the client passes (the core passes for it)")
            return
        if sq is None:
            self.log("a square off the board ignored")
            return
        data, final = await self.core_req("MOV %s %s" % (g.tid, sq))
        if not final.startswith("OK"):
            await self.elsewhere(g, "%s is not legal here (%s), so this board no longer matches the game. "
                                    "Your game at table %s goes on; play it %s." % (sq, err_text(final), g.tid,
                                                                                    WHERE.text))
            return
        g.told_turn = False
        await self.poll_game()

    async def on_back(self, cmd):
        """0x18: the board put away. During a game that is leaving it: a resignation."""
        g = self.game
        if g and g.state == "playing" and g.started:
            await self.core_req("RES %s" % g.tid)
            g.resigned_here = True
        if g and g.state != "waiting":
            self.game = None
            for k in list(self.pinned):         # the opponent: as the lobby has them now
                self.pinned.discard(k)
                pid = self.ids.get(k)
                if pid is None:
                    continue
                if k in self.hub.present:
                    name, avail = self.hub.present[k]
                    self.status.pop(pid, None)
                    self.set_status(name, self.status_for(name, avail))
                else:
                    self.drop_player(self.names[pid])
        self.hub.sync()

    def channel_closed(self):
        if self.in_lobby:
            self.hub.leave(self)
        self.in_lobby = False
        self.chan = None


SESSIONS = set()


# The protocol this extension serves, declared to the core (adapters/declare.py): key, name, description.
PROTOCOL = ("OUTLAND", "Outland", "Backgammon and Reversi for the Outland Gateway on Macs")


async def serve(args):
    hub, rv_hub = Hub(args.hub), Hub(args.hub, REVERSI)
    loop = asyncio.get_running_loop()

    gate = Gate(MAX_SESSIONS)
    gate.notice = None          # the Gateway speaks frames, not text

    async def handle(reader, writer):
        ticket = gate.admit(peer_addr(writer), writer)
        if ticket is None:
            writer.close()
            return
        keepalive(writer)
        s = Session(hub, reader, writer, (args.core_host, args.core_port), loop, ticket, rv_hub)
        SESSIONS.add(s)
        log(s.sid, "connect from %s" % (writer.get_extra_info("peername") or ("?",))[0])
        try:
            await s.run(args.poll)
        except (ConnectionError, asyncio.TimeoutError, CoreError, OSError):
            pass
        except Exception as e:
            print("outland session error: %r" % e, file=sys.stderr, flush=True)
        finally:
            ticket.release()
            SESSIONS.discard(s)
            log(s.sid, "disconnect (frames in %(in)d out %(out)d bad %(bad)d resent %(resent)d)" % s.link.counts)
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("outland adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.outland")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4000)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    declare.add_args(ap)
    ap.add_argument("--poll", type=float, default=POLL_SECS, help=argparse.SUPPRESS)
    ap.add_argument("--hub", type=float, default=HUB_SECS, help=argparse.SUPPRESS)
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
