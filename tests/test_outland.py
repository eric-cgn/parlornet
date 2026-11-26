"""Outland adapter tests: the link layer and the control and Backgammon records
as the Outland Gateway 1.3.2 and Backgammon (1995) send and read them.
A scripted Gateway signs in, reads the
service list, opens Backgammon and plays legal moves (worked out with the
backgammon/1 rules, as the real client works them out itself) against a web
player, another Outland player and, mirrored, a player who joins an open
table."""
import http.client
import importlib.util
import json
import os
import re
import select
import socket
import struct
import sys
import tempfile
import time
import unittest
from collections import deque
from urllib.parse import parse_qs, urlsplit

import procs
from test_adapters import WebPlayers, spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import outland as O  # noqa: E402
from adapters.common import parse_reversi_view, parse_tables  # noqa: E402
from parlord.auth import NEWPASS_TEXT  # noqa: E402
from speed import slow  # noqa: E402

_spec = importlib.util.spec_from_file_location("bg_rules", os.path.join(ROOT, "plugins", "backgammon", "plugin.py"))
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)

FAST = ["--poll", "0.15", "--hub", "0.3"]
# the service list: Backgammon and Reversi, both available, no files to compare
Y_BOTH = b"Y\x00\x18\x02" + b"\x00\x01BG@STBBG" + b"\x00\x01FlipCRev"


def s16(b, at):
    return struct.unpack_from(">h", b, at)[0]


# ---- what the client does with boards ---------------------------------------------------

def img_state(img):
    """An image in the client's own frame: column c is point c+1 and team t is seat t."""
    pts = [0] * 24
    for c in range(24):
        n, t = s16(img, 0x32 + 2 * c), s16(img, 0x62 + 2 * c)
        if n:
            pts[c] = n if t == 0 else -n
    return {"pts": pts, "bar": [s16(img, 0x9a), s16(img, 0x9e)], "off": [s16(img, 0x92), s16(img, 0x96)]}


def play_turn(board, team, d1, d2):
    """A full legal turn by the backgammon/1 rules (first legal step each time)."""
    st = {"pts": list(board["pts"]), "bar": list(board["bar"]), "off": list(board["off"]), "turn": team,
          "dice": rules.dice_for([d1, d2]), "steps": []}
    moves = []
    while st["off"][team] < 15:
        legal = rules.legal_steps(st)
        if not legal:
            break
        (frm, to), die = sorted(legal.items(), key=lambda e: str(e[0]))[0]
        nb, _ = rules.apply_step(rules.board_of(st), team, frm, to)
        st["pts"], st["bar"], st["off"] = nb["pts"], nb["bar"], nb["off"]
        st["dice"].remove(die)
        st["steps"].append(1)
        moves.append((frm, to))
    return {"pts": st["pts"], "bar": st["bar"], "off": st["off"]}, moves


def move_image(board, team, moves):
    """What Dump writes: the turn's moves (bar: 24 for team 0, -1 for team 1;
    off: -1 for team 0, 24 for team 1), the columns (team 2 when empty), trays."""
    img = bytearray(162)
    struct.pack_into(">h", img, 0, min(4, len(moves)))
    for i, (f, t) in enumerate(moves[:4]):
        fv = (24 if team == 0 else -1) if f == "bar" else f - 1
        tv = (-1 if team == 0 else 24) if t == "off" else t - 1
        struct.pack_into(">h", img, 2 + 2 * i, fv)
        struct.pack_into(">h", img, 0xa + 2 * i, tv)
        struct.pack_into(">ii", img, 0x12 + 4 * i, 0x00100020 + i, 0)
        struct.pack_into(">i", img, 0x22 + 4 * i, 0x00300040 + i)
    for c in range(24):
        n = board["pts"][c]
        struct.pack_into(">h", img, 0x32 + 2 * c, abs(n))
        struct.pack_into(">h", img, 0x62 + 2 * c, 2 if n == 0 else (0 if n > 0 else 1))
    struct.pack_into(">hhhhhhhh", img, 0x92, board["off"][0], 0, board["off"][1], 1, board["bar"][0], 0,
                     board["bar"][1], 1)
    return bytes(img)


def mirror(state):
    """The core's board seen with the colors swapped (team = seat ^ 1)."""
    return {"pts": [-state["pts"][23 - i] for i in range(24)], "bar": state["bar"][::-1],
            "off": state["off"][::-1]}


# ---- a scripted Outland Gateway -----------------------------------------------------------

def ctl_len(b):
    c = b[0]
    fixed = {ord("P"): 9, ord("T"): 1, ord("V"): 3, ord("R"): 6, ord("S"): 7}
    if c in fixed:
        return fixed[c]
    if c == ord("W"):
        return b[1] if len(b) > 1 else None
    if c == ord("Y"):
        return struct.unpack_from(">H", b, 1)[0] if len(b) >= 3 else None
    if c == ord("b"):
        return struct.unpack_from(">H", b, 2)[0] if len(b) >= 4 else None
    return len(b)


class Gateway:
    """The Gateway's end of the link (sends CRC+1, accepts CRC+0) with
    Backgammon on channel 1."""

    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.parser = O.FrameParser(key_ok=O.KEY_SEND, key_echo=O.KEY_RECV)
        self.seq = 0
        self.expect = 0
        self.streams = {c: bytearray() for c in range(5)}
        self.frames = []
        self.cmds = deque()
        self.recs = deque()
        self.seen = []
        self.chan = 1
        self.key = None
        self.pid = None
        self.closed = False

    def raw(self, data):
        self.s.sendall(data)

    def send(self, chan, payload):
        self.raw(O.encode_frame(self.seq, (self.expect - 1) & 15, O.T_DATA, chan, payload, key=O.KEY_RECV))
        self.seq = (self.seq + 1) & 15

    def send_rec(self, r):
        self.send(self.chan, bytes(r))

    def pump(self, timeout):
        r, _, _ = select.select([self.s], [], [], max(0.0, timeout))
        if not r:
            return False
        data = self.s.recv(65536)
        if not data:
            self.closed = True
            raise EOFError
        got = False
        for f in self.parser.feed(data):
            self.frames.append(f)
            if f.status == "ok" and f.ftype in (O.T_DATA, O.T_RESEND) and f.seq == self.expect:
                self.expect = (self.expect + 1) & 15
                self.streams[f.chan] += f.payload
                got = True
        if got:
            self.raw(O.encode_frame(0, (self.expect - 1) & 15, O.T_ACK, 0, b"", key=O.KEY_RECV))
        b = self.streams[0]
        while b:
            n = ctl_len(b)
            if n is None or n > len(b):
                break
            self.cmds.append(bytes(b[:n]))
            del b[:n]
        b = self.streams[self.chan]
        while True:
            while b and b[0] == 0:
                del b[0]
            if len(b) < O.REC:
                break
            self.recs.append(bytes(b[:O.REC]))
            del b[:O.REC]
        return True

    def ctl(self, letter, timeout=8):
        end = time.time() + timeout
        while True:
            for i, c in enumerate(self.cmds):
                if c[:1] == letter.encode():
                    del self.cmds[i]
                    self.seen.append(c)
                    return c
            if time.time() > end:
                raise AssertionError("no %r command; seen %r" % (letter, self.seen[-6:]))
            self.pump(min(0.2, end - time.time()))

    def rec(self, types, timeout=10):
        if isinstance(types, int):
            types = (types,)
        end = time.time() + timeout
        while True:
            while self.recs:
                r = self.recs.popleft()
                self.seen.append(r)
                if r[0] in types:
                    return r
            if time.time() > end:
                raise AssertionError("no record %r; seen %r" % (types, [x[0] for x in self.seen[-12:]]))
            self.pump(min(0.2, end - time.time()))

    def quiet(self, secs=0.5):
        end = time.time() + secs
        while time.time() < end:
            try:
                self.pump(end - time.time())
            except EOFError:
                break
        out = list(self.recs)
        self.recs.clear()
        return out

    def login(self, name, password):
        p = self.ctl("P")
        self.key = struct.unpack_from(">I", p, 1)[0]
        self.send(0, O.build_login(name, password, self.key))

    def open_backgammon(self):
        w = self.ctl("W")
        y = self.ctl("Y")
        self.send(0, b"\x81\x01" + O.SERVICE_ID)
        r = self.ctl("R")
        assert r == b"R\x01" + O.SERVICE_ID, r
        self.send_rec(O.record(O.C_CHECKIN))
        return w, y

    def lobby(self):
        """LobbyCheckIn's answer: the player list, then our own record."""
        players = {}
        while True:
            r = self.rec((O.S_NEWPLAYER, O.S_YOURNAME))
            name = O.read_pstr(r, O.P_NAME)
            if r[0] == O.S_YOURNAME:
                self.pid = r[O.P_ID]
                return players
            players[name] = (r[O.P_ID], r[O.P_AVAIL])

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def notices(recs):
    return [O.read_pstr(r, 1) for r in recs if r[0] in (O.S_PVTNOTICE, O.S_PUBNOTICE)]


# ---- a scripted Outland Reversi ----------------------------------------------------------------

RV_FIXED = {0x5d: 2, 0x5e: 4, 0x5f: 7, 0x60: 3, 0x61: 3, 0x62: 3, 0x63: 3, 0x64: 1, 0x65: 2, 0x67: 3, 0x69: 1,
            0x6b: 22}
RV_VAR = (0x5a, 0x5b, 0x5c, 0x66, 0x68)
DIRS = [(di, dj) for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj]


class RvClient:
    """Outland Reversi 1.1.1 as its code handles its channel: it asserts
    what the code asserts, keeps the board as [i][j] (i the high nibble of a move), counts the moves to
    know whose disc comes next, applies its own move at once, plays the moves it is sent, and after each
    one passes by itself when it has no move."""

    def __init__(self, gw, chan=2):
        self.g, self.chan = gw, chan
        self.hello = False
        self.me = None
        self.players = {}           # id -> [name, status]
        self.cmds = []              # every command received
        self.talk = []              # (game window?, sender name, text)
        self.invites = deque()      # (inviter id, flags)
        self.messages = []
        self.in_game = False
        self.ended = None
        self.my_move = False
        self.sent = []              # moves sent: (i, j, kind)
        self.watch = True           # connMade shows the watch cursor until a line of room history comes

    # the wire
    def send(self, data):
        self.g.send(self.chan, bytes(data))

    def open(self):
        self.g.send(0, b"\x81" + bytes([self.chan]) + O.RV_SERVICE_ID)
        r = self.g.ctl("R")
        assert r == b"R" + bytes([self.chan]) + O.RV_SERVICE_ID, r
        self.send(b"\x19\x00\x00\x00\x01")                      # sendSHello: the command and a long
        self.wait(lambda: O.RV_SERVER in self.ids())                # YouAre, then the list (Meet)
        self.wait(lambda: not self.watch)                           # room history: the watch cursor goes
        end = time.time() + 0.3
        while time.time() < end:
            self.pump(0.1)
        return self

    def pump(self, timeout=0.2):
        try:
            self.g.pump(timeout)
        except EOFError:
            pass
        b = self.g.streams[self.chan]
        while b:
            c = b[0]
            if c in RV_FIXED:
                n = RV_FIXED[c]
            elif c in RV_VAR:
                v = O.rv_varlen_of(b)
                if v is None:
                    break
                n = v[0]
            else:
                raise AssertionError("a command the client does not know: 0x%02x (handleBadCommand)" % c)
            if len(b) < n:
                break
            cmd = bytes(b[:n])
            del b[:n]
            self.cmds.append(cmd)
            self.handle(cmd)

    def wait(self, cond, timeout=15):
        end = time.time() + timeout
        while not cond():
            if time.time() > end:
                raise AssertionError("waited in vain; last commands %r" % [c[:8] for c in self.cmds[-8:]])
            self.pump(0.2)

    # handleCommand
    def handle(self, c):
        k = c[0]
        assert self.hello or k == 0x69, "the first command must be hello"
        if k == 0x69:
            assert not self.hello
            self.hello = True
        elif k == 0x5d:
            assert c[1] < 24 and c[1] not in self.players
            self.me = c[1]
            self.players[c[1]] = ["You", 1]
        elif k in (0x5a, 0x5c):
            v = O.rv_varlen_of(c)
            pid = c[1 + v[1]]
            at = 2 + v[1] + (1 if k == 0x5c else 0)
            name = c[at:v[0]]
            assert len(name) <= 0x30 and name.endswith(b"\0"), name
            assert pid < 24 and pid != self.me and pid not in self.players, (pid, self.players)
            self.players[pid] = [name[:-1].decode("mac_roman"), 1 if k == 0x5a else c[1 + v[1] + 1]]
        elif k == 0x61:
            if c[1] in self.players:
                if c[2] == 0:
                    del self.players[c[1]]
                else:
                    self.players[c[1]][1] = c[2]
        elif k == 0x5e:
            if c[2] != 24 and c[3] != 24:
                for pid in c[2:4]:
                    self.players[pid][1] = 2
        elif k in (0x5b, 0x66):
            v = O.rv_varlen_of(c)
            pid = c[1 + v[1]]
            assert pid in self.players, "talk from a player not in the list"
            text = c[2 + v[1]:v[0]]
            assert text.endswith(b"\0")
            if k == 0x5b or self.in_game:
                self.talk.append((k == 0x66, self.players[pid][0], text[:-1].decode("mac_roman")))
        elif k == 0x62:
            assert c[1] in self.players
            self.invites.append((c[1], c[2]))
        elif k == 0x63:
            self.messages.append(struct.unpack_from(">H", c, 1)[0])
        elif k == 0x5f:
            assert c[1] in self.players and c[2] in self.players
            self.first, self.opp = c[1] == self.me, c[2] if c[1] == self.me else c[1]
            assert self.me in c[1:3]
            self.board = {(3, 3): 0, (4, 4): 0, (3, 4): 1, (4, 3): 1}       # setStartingPosition
            self.count, self.opp_passed = 0, False
            self.in_game, self.ended, self.my_move = True, None, False
        elif k == 0x60:
            if self.in_game:
                self.do_move(c[1] >> 4, c[1] & 15, c[2])
                self.check_for_valid_move()
        elif k == 0x64:
            self.my_move = True
        elif k == 0x68:
            v = O.rv_varlen_of(c)
            assert 1 <= c[1 + v[1]] <= 7, "handleActionHistory asserts on any other kind"
            assert c[v[0] - 1] == 0 and c[2 + v[1] + 4:v[0]].count(b"\0") >= 2, "two C strings"
            self.watch = False
        elif k == 0x65:
            assert 1 <= c[1] <= 4
            if self.in_game:
                self.ended = c[1]
                self.my_move = False

    # the game, as Game.c has it
    def colour(self):
        return 0 if self.first else 1

    def flips(self, i, j, col):
        if (i, j) in self.board:
            return []
        out = []
        for di, dj in DIRS:
            run, a, b = [], i + di, j + dj
            while self.board.get((a, b)) == 1 - col:
                run.append((a, b))
                a, b = a + di, b + dj
            if run and self.board.get((a, b)) == col:
                out += run
        return out

    def valid(self, col):
        return [(i, j) for i in range(8) for j in range(8) if self.flips(i, j, col)]

    def do_move(self, i, j, kind):
        col = 0 if self.count % 2 == 0 else 1
        mine = col == self.colour()
        if kind == 2:
            if not mine:
                self.opp_passed = True
        else:
            fl = self.flips(i, j, col)
            assert fl, "doMove on a square that flips nothing: %d,%d" % (i, j)
            self.board[(i, j)] = col
            for sq in fl:
                self.board[sq] = col
            if not mine:
                self.opp_passed = False
        self.count += 1

    def check_for_valid_move(self):
        if not self.valid(self.colour()) and not self.opp_passed:
            self.do_move(0, 0, 2)                  # carryOutMove(2): pass, and say so
            self.send(b"\x17\x00\x02")
            self.sent.append((0, 0, 2))
            self.my_move = False

    def play(self, pick=0, greedy=False):
        """A click on a valid square (the pick-th, or the first that flips most): the move at once, then
        sendSMove."""
        assert self.my_move
        moves = self.valid(self.colour())
        i, j = (max(moves, key=lambda m: len(self.flips(*m, self.colour()))) if greedy
                else moves[pick % len(moves)])
        self.do_move(i, j, 1)
        self.send(bytes([0x17, (i << 4) | j, 1]))
        self.sent.append((i, j, 1))
        self.my_move = False

    def rows(self):
        """The board in reversi/1's rows (row 1 first): square i,j is column i, row 8 - j."""
        out = []
        for y in range(8):
            out.append("".join(str(self.board[(x, 7 - y)]) if (x, 7 - y) in self.board else "." for x in range(8)))
        return out

    def ids(self):
        return {n: pid for pid, (n, _) in self.players.items()}


# ---- unit tests ------------------------------------------------------------------------------

class LinkLayer(unittest.TestCase):
    def test_worked_examples(self):
        """Worked examples of whole frames (keys 0 and 1)."""
        self.assertEqual(O.encode_frame(0, 15, O.T_DATA, 0, b"T").hex(), "0f0095ef541003")
        self.assertEqual(O.encode_frame(0, 15, O.T_DATA, 0, b"P" + bytes.fromhex("1234567800000000")).hex(),
                         "0f00054c50123456780000000010" "03")
        self.assertEqual(O.encode_frame(0, 0, O.T_ACK, 0, b"", key=O.KEY_RECV).hex(), "002032b41003")

    def test_round_trips_and_stuffing(self):
        p = O.FrameParser(key_ok=O.KEY_SEND, key_echo=O.KEY_RECV)
        payloads = [b"", b"\x10", b"\x10\x03", b"\x03\x10\x10\x03\x10", bytes(range(256)), b"\x10" * 300]
        stream = b"".join(O.encode_frame(i, 15 - i, O.T_DATA, i % 4, pl) for i, pl in enumerate(payloads))
        out = []
        for i in range(0, len(stream), 7):            # in pieces, as TCP may deliver it
            out += p.feed(stream[i:i + 7])
        self.assertEqual([f.payload for f in out], payloads)
        self.assertEqual([(f.seq, f.ack, f.chan, f.status) for f in out],
                         [(i, 15 - i, i % 4, "ok") for i in range(len(payloads))])
        # the header is never scanned for DLE ETX: a check of 10 03 does not end the frame
        f = None
        for seq in range(16):
            for chan in range(5):
                raw = O.encode_frame(seq, 0, O.T_DATA, chan, b"\x03abc")
                if raw[3] == 0x10:
                    f = raw
        if f:
            self.assertEqual(O.FrameParser(key_ok=0).feed(f)[0].payload, b"\x03abc")

    def test_keys_echo_and_bad(self):
        mine = O.encode_frame(1, 2, O.T_DATA, 0, b"hello", key=O.KEY_SEND)
        theirs = O.encode_frame(1, 2, O.T_DATA, 0, b"hello", key=O.KEY_RECV)
        server = O.FrameParser()                       # accepts CRC+1, CRC+0 is its own echo
        self.assertEqual([f.status for f in server.feed(theirs + mine)], ["ok", "echo"])
        bad = bytearray(theirs)
        bad[5] ^= 1
        self.assertEqual(server.feed(bytes(bad))[0].status, "bad")
        self.assertEqual(O.crc16(b""), 0xFFFF)

    def test_link_window_acks_and_resend(self):
        t = [0.0]
        wire = []
        got = []
        link = O.Link(wire.append, lambda c, p: got.append((c, p)), lambda: t[0])
        for i in range(10):
            link.send(1, b"rec%d" % i)
        client = O.FrameParser(key_ok=O.KEY_SEND, key_echo=O.KEY_RECV)
        sent = client.feed(b"".join(wire))
        self.assertEqual([f.seq for f in sent], list(range(8)))          # a window of 8
        wire.clear()
        link.receive(O.encode_frame(0, 4, O.T_ACK, 0, b"", key=O.KEY_RECV))   # acks 0-4
        self.assertEqual([f.seq for f in client.feed(b"".join(wire))], [8, 9])
        wire.clear()
        link.receive(O.encode_frame(0, 4, O.T_NAK, 0, b"", key=O.KEY_RECV))   # resend after 4
        again = client.feed(b"".join(wire))
        self.assertEqual([(f.seq, f.ftype) for f in again], [(s, O.T_RESEND) for s in (5, 6, 7, 8, 9)])
        wire.clear()
        t[0] += O.RESEND_SECS + 1
        link.tick()
        self.assertEqual(len(client.feed(b"".join(wire))), 5)            # a timeout resends too
        wire.clear()
        # receiving: in order, a duplicate is acked again, a gap is nak'ed once
        link.receive(O.encode_frame(0, 9, O.T_DATA, 0, b"a", key=O.KEY_RECV))
        link.receive(O.encode_frame(0, 9, O.T_DATA, 0, b"a", key=O.KEY_RECV))
        link.receive(O.encode_frame(3, 9, O.T_DATA, 0, b"c", key=O.KEY_RECV))
        link.receive(O.encode_frame(4, 9, O.T_DATA, 0, b"d", key=O.KEY_RECV))
        self.assertEqual(got, [(0, b"a")])
        self.assertEqual([(f.ftype, f.ack) for f in client.feed(b"".join(wire))], [(O.T_ACK, 0), (O.T_NAK, 0)])
        self.assertFalse(link.unacked)                                   # ack nibble 9 freed them all
        wire.clear()
        t[0] += 1
        link.tick()
        self.assertEqual(wire, [])

    def test_login_xor_and_commands(self):
        for key in (0, 1, 0xDEADBEEF):
            rec = O.build_login("gus", "123456", key)
            self.assertEqual(rec[1], len(rec))
            self.assertEqual(O.parse_login(rec, key), (0x01328002, "gus", "123456"))
        self.assertNotIn(b"123456", O.build_login("gus", "123456", 0x01020304))
        self.assertEqual(O.cmd_services([(O.SERVICE_ID, O.CREATOR, 1)]),
                         b"Y\x00\x0e\x01\x00\x01BG@STBBG")
        self.assertEqual(O.cmd_name("gus"), b"W\x06gus\x00")
        t = O.cmd_text("a\nb")
        self.assertEqual((t[:2], struct.unpack_from(">H", t, 2)[0], t[4:]), (b"b\x01", 8, b"a\rb\x00"))
        self.assertEqual(O.split_commands(b"\x83\x01\x81\x02BG@S" + O.build_login("a", "b", 0)),
                         [b"\x83\x01", b"\x81\x02BG@S", O.build_login("a", "b", 0)])

    def test_board_images(self):
        b = O.Board()
        for flip in (0, 1):
            img = O.board_image(b, flip)
            pts, bar, off = O.image_counts(img, flip)
            self.assertEqual((pts, bar, off), (b.pts, b.bar, b.off))
        # the opening position as the client's constructor sets it (team 0 = seat 0)
        img = O.board_image(b, 0)
        cols = {c: (s16(img, 0x32 + 2 * c), s16(img, 0x62 + 2 * c)) for c in range(24)}
        self.assertEqual({c: v for c, v in cols.items() if v[0]},
                         {0: (2, 1), 5: (5, 0), 7: (3, 0), 11: (5, 1), 12: (5, 0), 16: (3, 1), 18: (5, 1),
                          23: (2, 0)})
        self.assertEqual(cols[1], (0, 2))
        # moves: bar and off by team, mirrored with flip 1
        m = move_image(img_state(img), 0, [("bar", 22), (6, "off"), (13, 9)])
        self.assertEqual(O.image_moves(m, 0, 0), ["bar-22", "6-off", "13-9"])
        self.assertEqual(O.image_moves(m, 0, 1), ["bar-3", "19-off", "12-16"])
        m = move_image(img_state(img), 1, [("bar", 3), (19, "off")])
        self.assertEqual(O.image_moves(m, 1, 0), ["bar-3", "19-off"])
        m2 = bytearray(m)
        struct.pack_into(">h", m2, 2, 255)                 # 255 is the -1 side too
        self.assertEqual(O.image_moves(bytes(m2), 1, 0), ["bar-3", "19-off"])
        struct.pack_into(">h", m2, 0, 7)
        self.assertIsNone(O.image_moves(bytes(m2), 1, 0))
        # the model follows events, hits and their undo
        b.step(0, "24", "23", False)
        b.step(1, "1", "23", True)
        self.assertEqual((b.pts[22], b.bar), (-1, [1, 0]))
        b.undo(1, "1", "23")
        self.assertEqual((b.pts[22], b.bar, b.pts[0]), (1, [0, 0], -2))


# ---- against a live core ------------------------------------------------------------------------

class LobbyList(unittest.TestCase):
    def test_a_running_games_opponent_is_listed_while_offline(self):
        """The challenge that brings back a running game comes from its opponent, and the client names it by
        its player list, so the opponent is listed even when WHO no longer has them online (a real Gateway
        showed such a challenge as coming from the bot)."""
        import types
        hub = O.Hub(1)
        hub.who_ok, hub.online = True, ["eric", "bot"]
        hub.tables = [{"id": "5", "names": ["dan", "eric"], "status": "playing", "filled": 2}]
        game = types.SimpleNamespace(opp="dan")
        hub.sessions["eric"] = types.SimpleNamespace(name="eric", game=game, busy=lambda: True)
        everyone = hub.everyone()
        self.assertEqual(everyone["dan"], ("dan", False))       # listed, not available
        self.assertIn("bot", everyone)
        game.opp = None
        self.assertNotIn("dan", hub.everyone())                  # no running game: offline players stay off


class LiveBase(WebPlayers, unittest.TestCase):
    auth = "none"           # the games; sign-in is LivePassword's
    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.port, cls.web = free_port(), free_port(), free_port(), free_port()
        cls.keys = make_keys("outland", "web")
        cls.procs = procs.owned(cls)
        cls.procs.append(spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", cls.auth))
        cls.procs.append(spawn(*adapter_args("adapters.outland", cls.port, cls.fe, cls.keys, "outland"), *FAST))
        cls.procs.append(spawn(*adapter_args("adapters.web", cls.web, cls.fe, cls.keys, "web")))

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)


    def web_table(self, name):
        loc = self.web_get("/new.htm?g=backgammon%%2F1&turn=1h&n=%s" % name)[1]
        return parse_qs(urlsplit(loc).query)["t"][0]

    def native(self, name):
        s = socket.create_connection(("127.0.0.1", self.core), timeout=5)
        s.sendall(b"PARLORNET/1\r\n")       # the opening first, then the greeting
        f = s.makefile("rb")
        f.readline()

        def req(text):
            s.sendall(text.encode() + b"\r\n")
            out = []
            while True:
                l = f.readline().decode().rstrip("\r\n")
                out.append(l)
                if l.startswith(("OK", "ERR", "ASK")):
                    return out
        self.assertEqual(req("HELO %s" % name)[-1], "OK -")        # sign-in none: the name alone
        self.addCleanup(s.close)
        return req

    @staticmethod
    def view(req, tid):
        lines = req("VUE %s M" % tid)
        return O.parse_backgammon_view(lines)

    def web_turn(self, req, tid, name, seat):
        """The web player plays a whole turn through the web adapter's links."""
        for _ in range(12):
            v = self.view(req, tid)
            if v["turn"] != seat:
                return
            if v["phase"] == "roll":
                mv = "roll"
            elif v["phase"] == "take":
                mv = "take"
            elif v["moves"]:
                mv = v["moves"][0]
            else:
                self.assertEqual(self.web_get("/end.htm?t=%s&n=%s" % (tid, name))[0], 302)
                continue
            self.assertEqual(self.web_get("/move.htm?t=%s&mv=%s&n=%s" % (tid, mv, name))[0], 302)
        raise AssertionError("the web player's turn did not end")

    def gateway(self, name, password=""):
        g = Gateway(self.port)
        self.addCleanup(g.close)
        g.login(name, password)
        return g

    def client_turn(self, g, team, board, d1, d2, won_ok=True):
        """Play the dice: RollNotify, then the turn's record with the new board."""
        new, moves = play_turn(board, team, d1, d2)
        g.send_rec(O.record(O.C_ROLLED))
        r = O.record(O.C_WON if new["off"][team] == 15 else O.C_MOVE)
        r[O.O_IMG:O.O_IMG + 162] = move_image(new, team, moves)
        g.send_rec(r)
        return new, moves


@slow
class Live(LiveBase):

    def test_outland_against_a_web_player(self):
        self.web_login("wes")
        tid = self.web_table("wes")
        req = self.native("wes")
        g = self.gateway("gus", "anything")             # sign-in none: the password is dropped
        w, y = g.open_backgammon()
        self.assertEqual(w, b"W\x06gus\x00")
        self.assertEqual(y, Y_BOTH)
        players = g.lobby()
        self.assertEqual(players["wes"][1], 1)          # waiting at an open table
        self.assertIn("gus", players)
        self.assertEqual(players["gus"][0], g.pid)
        # challenge wes (a 3-game match: the answer says it is 1 game)
        ch = O.record(O.C_CHALLENGE)
        ch[2] = players["wes"][0]
        struct.pack_into(">H", ch, 4, 3)
        g.send_rec(ch)
        resp = g.rec(O.S_RESPONSE)
        self.assertEqual((resp[2], resp[6], struct.unpack_from(">H", resp, 4)[0]), (players["wes"][0], 1, 1))
        team = resp[7]
        self.assertEqual(team, 1)                       # wes sat first: gus has seat 1, White
        g.send_rec(O.record(O.C_START, team))
        opening = g.rec(O.S_ROLL)
        self.assertEqual(opening[O.O_MOVER], players["wes"][0])
        d1, d2 = opening[O.O_DIE1], opening[O.O_DIE2]
        self.assertNotEqual(d1, d2)
        v = self.view(req, tid)
        self.assertEqual(v["turn"], 1 if d1 > d2 else 0)   # [170] is team 1's die: higher starts
        board = img_state(O.board_image(O.Board(), 0))
        mine = d1 > d2
        turns = 0
        doubled_once = False
        while turns < 6:
            if mine:
                new, moves = self.client_turn(g, team, board, d1, d2)
                deadline = time.time() + 10
                while self.view(req, tid)["turn"] == team and time.time() < deadline:
                    time.sleep(0.1)
                v = self.view(req, tid)
                self.assertEqual((v["pts"], list(v["bar"]), list(v["off"])),
                                 (new["pts"], new["bar"], new["off"]), moves)
                board = new
            else:
                self.web_turn(req, tid, "wes", 0)
                go = g.rec(O.S_GO)
                self.assertEqual(go[O.O_MOVER], players["wes"][0])
                v = self.view(req, tid)
                img = go[O.O_IMG:O.O_IMG + 162]
                self.assertEqual(img_state(img), {"pts": v["pts"], "bar": list(v["bar"]), "off": list(v["off"])})
                d1, d2 = go[O.O_DIE1], go[O.O_DIE2]
                deadline = time.time() + 10                     # the roll can reach the view just after the GO
                while not v["dice"] and time.time() < deadline:
                    time.sleep(0.05)
                    v = self.view(req, tid)
                self.assertEqual(sorted(set(v["dice"])), sorted({d1, d2}))   # the core rolled for gus
                board = img_state(img)
                if not doubled_once and play_turn(board, team, d1, d2)[1]:
                    # Outland's cube comes before the roll: refused, and the turn handed back
                    doubled_once = True
                    g.send_rec(O.record(O.C_DOUBLE))
                    t = g.rec(O.S_TURN)
                    self.assertEqual(t[1], 0)
                    go = g.rec(O.S_GO)
                    self.assertEqual((go[O.O_DIE1], go[O.O_DIE2]), (d1, d2))
                    self.assertTrue(any("can't double" in n for n in notices(g.seen)))
            mine = not mine
            turns += 1
        # an illegal move is taken back and the turn handed back
        while True:
            if not mine:
                self.web_turn(req, tid, "wes", 0)
                go = g.rec(O.S_GO)
                board, d1, d2 = img_state(go[O.O_IMG:O.O_IMG + 162]), go[O.O_DIE1], go[O.O_DIE2]
            if play_turn(board, team, d1, d2)[1]:
                break
            board = self.client_turn(g, team, board, d1, d2)[0]      # no move this time: pass and go on
            mine = False
        bad = O.record(O.C_MOVE)
        bad[O.O_IMG:O.O_IMG + 162] = move_image(board, team, [(24, 1)])
        g.send_rec(bad)
        self.assertEqual(g.rec(O.S_TURN)[1], 0)
        go = g.rec(O.S_GO)
        self.assertEqual(img_state(go[O.O_IMG:O.O_IMG + 162]), board)
        self.assertTrue(any("not legal here" in n for n in notices(g.seen)))
        # chat both ways
        msg = O.record(O.C_CHAT)
        msg[2:12] = O.pstr("hello wes")
        g.send_rec(msg)
        deadline = time.time() + 5
        while not any(l.endswith("CHT 1 hello wes") for l in req("SIN %s 0" % tid)) and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(any(l.endswith("CHT 1 hello wes") for l in req("SIN %s 0" % tid)))
        req("CHT %s hi gus" % tid)
        back = g.rec(O.S_CHAT)                         # not our own line: the client prints that itself
        self.assertEqual((back[1], O.read_pstr(back, 2)), (players["wes"][0], "hi gus"))
        # resign: accepted at once, and a go with the points ends the client's game
        res = O.record(O.C_RESIGN)
        struct.pack_into(">H", res, 2, 2)
        g.send_rec(res)
        ans = g.rec(O.S_RESIGNRET)
        self.assertEqual(struct.unpack_from(">H", ans, 2)[0], 1)
        end = g.rec(O.S_GO)
        self.assertEqual((struct.unpack_from(">H", end, O.O_POINTS)[0], end[O.O_MOVER]), (2, players["wes"][0]))
        self.assertTrue(any(l.endswith("END resign 1") for l in req("SIN %s 0" % tid)))
        g.send_rec(O.record(O.C_AWAY))
        g.send_rec(O.record(O.C_RELOBBY))
        g.quiet(0.5)

    def test_two_outland_players(self):
        ann, bob = self.gateway("ann"), self.gateway("bob")
        for c in (ann, bob):
            c.open_backgammon()
        pa = ann.lobby()
        pb = bob.lobby()
        self.assertIn("ann", pb)
        while "bob" not in pa:                           # bob came in after ann's list
            r = ann.rec(O.S_NEWPLAYER)
            pa[O.read_pstr(r, O.P_NAME)] = (r[O.P_ID], r[O.P_AVAIL])
        # lobby chat reaches both
        lc = O.record(O.C_LOBBYCHAT)
        lc[2:9] = O.pstr("hi all")
        ann.send_rec(lc)
        r = bob.rec(O.S_LOBBYCHAT)
        self.assertEqual((r[1], O.read_pstr(r, 2)), (ann.pid, "hi all"))
        self.assertFalse(any(x[0] == O.S_LOBBYCHAT for x in ann.quiet(0.3)))   # ann's client shows it itself
        # bob challenges ann; ann accepts as White (item 1: team 1)
        ch = O.record(O.C_CHALLENGE)
        ch[2] = ann.pid
        struct.pack_into(">H", ch, 4, 1)
        bob.send_rec(ch)
        inv = ann.rec(O.S_CHALLENGE)
        self.assertEqual((inv[1], inv[2], struct.unpack_from(">H", inv, 4)[0]), (bob.pid, bob.pid, 1))
        resp = bytearray(inv)
        resp[0], resp[6], resp[7] = O.C_RESPONSE, 1, 1
        ann.send_rec(resp)
        ann.send_rec(O.record(O.C_START, 1))
        got = bob.rec(O.S_RESPONSE)
        self.assertEqual((got[2], got[6], got[7]), (ann.pid, 1, 0))     # bob is Red, team 0
        bob.send_rec(O.record(O.C_START, 0))
        oa, ob = ann.rec(O.S_ROLL), bob.rec(O.S_ROLL)
        self.assertEqual((oa[O.O_DIE1], oa[O.O_DIE2]), (ob[O.O_DIE1], ob[O.O_DIE2]))
        self.assertEqual((oa[O.O_MOVER], ob[O.O_MOVER]), (bob.pid, ann.pid))
        d1, d2 = oa[O.O_DIE1], oa[O.O_DIE2]
        teams = {ann: 1, bob: 0}
        mover = ann if d1 > d2 else bob
        board = img_state(O.board_image(O.Board(), 0))
        for turn in range(5):
            other = bob if mover is ann else ann
            new, moves = self.client_turn(mover, teams[mover], board, d1, d2)
            shown = other.rec(O.S_ROLL) if turn else oa  # the other side saw the roll, now the go
            go = other.rec(O.S_GO)
            img = go[O.O_IMG:O.O_IMG + 162]
            self.assertEqual(img_state(img), new)
            self.assertEqual(go[O.O_MOVER], mover.pid)
            if moves and len(moves) <= 4:                # the mover's own move list, relayed
                sent = move_image(new, teams[mover], moves)
                self.assertEqual(bytes(img[:0x32]), sent[:0x32])
            self.assertIsNotNone(shown)
            board, d1, d2, mover = new, go[O.O_DIE1], go[O.O_DIE2], other
        # bob puts the board away: a resignation, offered to ann
        loser = bob
        winner = ann
        loser.send_rec(O.record(O.C_AWAY))
        req = winner.rec(O.S_RESIGN)
        self.assertEqual(struct.unpack_from(">H", req, 2)[0], 1)
        ok = O.record(O.C_ANSWER)
        struct.pack_into(">H", ok, 2, 1)
        winner.send_rec(ok)
        loser.send_rec(O.record(O.C_RELOBBY))
        self.assertFalse(any("over" in n for n in notices(winner.quiet(0.5))))

    def test_open_table_mirrored(self):
        """/open, a web player sits down, the Outland player accepts the other
        color: the board is mirrored, and moves still land on the right points."""
        g = self.gateway("ola")
        g.open_backgammon()
        g.lobby()
        self.assertEqual(notices([g.rec(O.S_PUBNOTICE)]), [O.BG_BOT_HOWTO])   # no message of the day here
        self.assertEqual(notices([g.rec(O.S_PUBNOTICE)]), [O.COMMANDS_LINE])  # no games in progress either
        lc = O.record(O.C_LOBBYCHAT)
        lc[2:8] = O.pstr("/open")
        g.send_rec(lc)
        text = notices([g.rec(O.S_PUBNOTICE)])[0]
        tid = re.search(r"table (\d+) is open", text).group(1)
        self.web_login("wyn")
        self.assertEqual(self.web_get("/join.htm?t=%s&n=wyn" % tid)[0], 302)
        req = self.native("wyn")
        inv = g.rec(O.S_CHALLENGE)
        resp = bytearray(inv)
        resp[0], resp[6], resp[7] = O.C_RESPONSE, 1, 1         # White: team 1, but ola has seat 0
        g.send_rec(resp)
        opening = g.rec(O.S_ROLL)
        self.assertIn(O.FIXED_SIDE % ("White", "white"), notices(g.seen))   # seat 0: only the drawing follows
        d1, d2 = opening[O.O_DIE1], opening[O.O_DIE2]
        v = self.view(req, tid)
        self.assertEqual(v["turn"], 0 if d1 > d2 else 1)         # team 1 is seat 0 here
        board = mirror({"pts": v["pts"], "bar": list(v["bar"]), "off": list(v["off"])})
        self.assertEqual(board, img_state(O.board_image(O.Board(), 0)))
        mine = d1 > d2
        for _ in range(3):
            if mine:
                new, _ = self.client_turn(g, 1, board, d1, d2)
                deadline = time.time() + 10
                while self.view(req, tid)["turn"] == 0 and time.time() < deadline:
                    time.sleep(0.1)
                v = self.view(req, tid)
                self.assertEqual(mirror({"pts": v["pts"], "bar": list(v["bar"]), "off": list(v["off"])}), new)
                board = new
            else:
                self.web_turn(req, tid, "wyn", 1)
                go = g.rec(O.S_GO)
                v = self.view(req, tid)
                board = img_state(go[O.O_IMG:O.O_IMG + 162])
                self.assertEqual(board, mirror({"pts": v["pts"], "bar": list(v["bar"]), "off": list(v["off"])}))
                d1, d2 = go[O.O_DIE1], go[O.O_DIE2]
            mine = not mine
        req("RES %s" % tid)
        r = g.rec(O.S_RESIGN)                          # wyn resigned: ola is asked to accept
        no = O.record(O.C_ANSWER)
        g.send_rec(no)                                  # declined: the game is over anyway
        self.assertEqual(g.rec(O.S_LEFT)[0], O.S_LEFT)
        self.assertIsNotNone(r)

    def test_who_sits_down_says_with_what(self):
        """/open, a native player sits down: the notice says what they play with (VUE C)."""
        g = self.gateway("pia")
        g.open_backgammon()
        g.lobby()
        g.rec(O.S_PUBNOTICE)                    # how to play the bot
        g.rec(O.S_PUBNOTICE)                    # the commands
        lc = O.record(O.C_LOBBYCHAT)
        lc[2:8] = O.pstr("/open")
        g.send_rec(lc)
        tid = re.search(r"table (\d+) is open", notices([g.rec(O.S_PUBNOTICE)])[0]).group(1)
        req = self.native("quin")
        req("CLI PROG Xgammon 1")
        req("JON %s" % tid)
        self.assertEqual(notices([g.rec(O.S_PUBNOTICE)]), ["quin sat down at your table %s with Xgammon 1." % tid])
        req("LVE %s" % tid)

    def test_refusals(self):
        g = self.gateway("bad name!", "x")
        self.assertIn(b"Names here are", g.ctl("b"))
        self.assertEqual(g.ctl("V"), b"V" + struct.pack(">H", 1))
        # unknown services are closed; nothing is open before the login
        g = self.gateway("uma")
        g.ctl("Y")
        g.send(0, b"\x81\x02XXXX")
        self.assertEqual(g.ctl("S"), b"S\x02\x03XXXX")
        # a second Gateway with the same name
        h = self.gateway("uma")
        self.assertIn(b"another Outland Gateway", h.ctl("b"))
        # a bad frame is nak'ed
        bad = bytearray(O.encode_frame(5, 15, O.T_DATA, 0, b"\x84", key=O.KEY_RECV))
        bad[4] ^= 0x40
        g.raw(bytes(bad))
        deadline = time.time() + 5
        while not any(f.ftype == O.T_NAK for f in g.frames) and time.time() < deadline:
            g.pump(0.2)
        self.assertTrue(any(f.ftype == O.T_NAK for f in g.frames))


@slow
class LivePassword(LiveBase):
    """Sign-in and sign-up password: the Gateway's password answers the
    credential question. The login only signs in, as on Outland: a name with
    no account is V 1 with a pointer to where accounts are made."""
    auth = "password"

    def test_sign_up_sign_in_and_refusals(self):
        self.web_login("olaf")                                  # the account is made on the web
        g = self.gateway("olaf", "olaf-Pass-word")          # and signs in from the Gateway
        w, y = g.open_backgammon()
        self.assertEqual(w, b"W\x07olaf\x00")
        g.close()
        time.sleep(0.3)
        self.web_login("rita")
        for name, password, code, reason in (("rita", "", 1, b"rita needs its password."),
                                             ("rita", "wrong-password", 2, b"Wrong password"),
                                             ("shorty", "shorty-Pass-word", 1,
                                              b"There is no account named shorty here. Make one on the web")):
            g = self.gateway(name, password)
            text = g.ctl("b")
            self.assertIn(reason, text)
            self.assertEqual(g.ctl("V"), b"V" + struct.pack(">H", code))
            self.assertFalse(any(c[:1] in (b"W", b"Y") for c in list(g.seen) + list(g.cmds)))
            with self.assertRaises((EOFError, AssertionError)):
                g.ctl("Z", timeout=O.REFUSE_LINGER + 2)


class LiveMotd(LiveBase):
    """A server with a message of the day: each Outland lobby shows it between its rules as a player comes
    in, then how to play the bot."""
    MOTD = "Welcome to the test parlor.\n\n  Games tonight at eight."

    @classmethod
    def setUpClass(cls):
        cls.motd = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        cls.motd.write(cls.MOTD)
        cls.motd.close()
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("outland")
        cls.procs = procs.owned(cls)
        cls.procs.append(spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "none", "--motd", cls.motd.name))
        cls.procs.append(spawn(*adapter_args("adapters.outland", cls.port, cls.fe, cls.keys, "outland"), *FAST))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        os.unlink(cls.motd.name)

    def expected(self):
        from adapters.common import framed_motd
        return framed_motd(self.MOTD.split("\n"))

    def test_backgammon_lobby(self):
        g = self.gateway("mo")
        g.open_backgammon()
        g.lobby()
        got = [O.read_pstr(g.rec(O.S_PUBNOTICE), 1) for _ in range(len(self.expected()) + 1)]
        self.assertEqual(got, self.expected() + [O.BG_BOT_HOWTO])
        self.assertTrue(got[0].startswith("=") and got[-2].startswith("="))

    def test_reversi_waiting_room(self):
        g = self.gateway("rmo")
        g.ctl("Y")
        c = RvClient(g).open()
        n = len(self.expected()) + 1
        c.wait(lambda: len([t for t in c.talk if t[1] == O.RV_SERVER]) >= n)
        got = [t[2] for t in c.talk if t[1] == O.RV_SERVER][:n]
        self.assertEqual(got, [l or " " for l in self.expected()] + [O.RV_BOT_HOWTO])


def mailed_password(path, to, timeout=10):
    """The password in the newest mail to an address, from the file parlord's test transport writes
    (email.test_file). Never printed."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(path, encoding="utf-8") as f:
                mine = [json.loads(l) for l in f if l.strip() and json.loads(l)["to"] == to]
        except OSError:
            mine = []
        if mine:
            return re.search(r"Password:\s+(\S+)", mine[-1]["body"]).group(1)
        time.sleep(0.1)
    raise AssertionError("no mail to %s" % to)


class LiveEmail(unittest.TestCase):
    """--auth password --signup email: the Outland password is the ParlorNet password; a mailed password
    cannot be used from the Gateway, which cannot ask for a new one, and the refusal says where to go; a new
    name is sent to the web site or telnet to sign up."""

    def test_mailed_password_refused_then_own_password_and_signup_pointer(self):
        core, port = free_port(), free_port()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mails = os.path.join(tmp.name, "mails")
        settings = os.path.join(tmp.name, "settings")
        with open(settings, "w") as f:
            f.write("email.transport=test\nemail.test_file=%s\n" % mails)
        fe, keys = free_port(), make_keys("outland")
        running = procs.owned(self)
        running.append(spawn(*stack_args(core, fe, keys), "--auth", "password", "--signup", "email",
                             "--settings-file", settings))
        running.append(spawn(*adapter_args("adapters.outland", port, fe, keys, "outland"), *FAST))
        s = socket.create_connection(("127.0.0.1", core), timeout=5)
        s.sendall(b"PARLORNET/1\r\n")       # the opening first, then the greeting
        f = s.makefile("rb")
        f.readline()

        def req(text):
            s.sendall(text.encode() + b"\r\n")
            while True:
                l = f.readline().decode().strip()
                if l.startswith(("OK", "ERR", "ASK")):
                    return l
        self.assertEqual(req("REG tia"), "ASK email Email_address")
        self.assertEqual(req("AUT email tia@example.org"), "ASK password Password_from_the_email secret")
        s.close()
        pw = mailed_password(mails, "tia@example.org")
        g = Gateway(port)                  # the pending account, from the Gateway: where to go instead
        g.login("tia", pw)
        self.assertEqual(g.ctl("b")[4:], NEWPASS_TEXT.encode() + b"\0")     # the text window, all of it
        self.assertEqual(g.ctl("V"), b"V" + struct.pack(">H", 2))
        g.close()
        s = socket.create_connection(("127.0.0.1", core), timeout=5)    # as on the web or by telnet
        s.sendall(b"PARLORNET/1\r\n")
        f = s.makefile("rb")
        f.readline()
        self.assertEqual(req("USER tia"), "ASK password Password secret cred")
        self.assertEqual(req("AUT password %s" % pw), "ASK password New_password secret new")
        self.assertEqual(req("AUT password Tia-own-pass1"), "ASK confirm Type_it_again secret new")
        self.assertEqual(req("AUT confirm Tia-own-pass1"), "OK -")
        s.close()
        g = Gateway(port)
        g.login("tia", "Tia-own-pass1")
        self.assertEqual(g.ctl("Y"), Y_BOTH)
        g.pump(0.3)
        self.assertFalse(any(c[:1] == b"b" for c in list(g.seen) + list(g.cmds)))  # no token to show
        g.close()
        time.sleep(0.3)
        for name, password, code, reason in (("newbie", "123456", 1, b"There is no account named newbie here."
                                                                     b" Make one on the web"),
                                             ("tia", "Not-her-pass1", 2, b"Wrong password")):
            h = Gateway(port)
            h.login(name, password)
            self.assertIn(reason, h.ctl("b"))
            self.assertEqual(h.ctl("V"), b"V" + struct.pack(">H", code))
            h.close()


# ---- Outland Reversi ------------------------------------------------------------------------------

class ReversiCodec(unittest.TestCase):
    def test_lengths_squares_and_commands(self):
        # the length field: the whole command, one byte up to 0x7e, two beyond, as the client writes it
        self.assertEqual(O.rv_varlen(0x5b, b"\x03hi\x00"), b"\x5b\x06\x03hi\x00")
        self.assertEqual(len(O.rv_varlen(0x5b, b"x" * 124)), 0x7e)
        self.assertEqual(O.rv_varlen(0x5b, b"x" * 125)[:3], b"\x5b\x81\x00")
        for n in (0, 1, 124, 125, 126, 300, 1000):
            cmd = O.rv_varlen(0x5b, b"x" * n)
            self.assertEqual(O.rv_varlen_of(cmd)[0], len(cmd))
        # the client's starting position ([3][3] and [4][4] its first color) is reversi/1's: seat 0 on d5, e4
        self.assertEqual([O.rv_square(b) for b in (0x33, 0x44, 0x34, 0x43)], ["d5", "e4", "d4", "e5"])
        for sq in ("%s%d" % (c, r) for c in "abcdefgh" for r in range(1, 9)):
            self.assertEqual(O.rv_square(O.rv_packed(sq)), sq)
        self.assertIsNone(O.rv_square(0x08))
        # the client's commands, in pieces and run together
        svc = O.ReversiService(type("S", (), {"sid": 0})(), None, 2)
        stream = (b"\x19\x00\x00\x00\x01" + O.rv_varlen(0x14, b"hello\0") + b"\x15\x03\x01\x17\x23\x01\x18"
                  + O.rv_varlen(0x1a, b"y" * 200 + b"\0") + b"\x1e\x02")
        got = []
        for i in range(0, len(stream), 5):
            got += svc.feed(stream[i:i + 5])
        self.assertEqual([c[0] for c in got], [0x19, 0x14, 0x15, 0x17, 0x18, 0x1a, 0x1e])
        self.assertEqual(svc.text_of(got[1]), "hello")
        self.assertEqual(len(got[5]), 204)
        self.assertEqual(svc.feed(b"\x77\x01\x02"), [])                     # unknown: dropped
        # the server's board model answers "has this seat a move?"
        b = O.RvBoard()
        self.assertTrue(b.can_move(0) and b.can_move(1))
        b.put(0, ["d3", "d4"])
        self.assertTrue(b.can_move(1))


@slow
class LiveReversi(WebPlayers, unittest.TestCase):
    """Outland Reversi against a web player, the bot, another Outland player and an invitation from the
    web: a core (sign-in none), the Outland and web extensions and the bot."""

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.port, cls.web = free_port(), free_port(), free_port(), free_port()
        cls.keys = make_keys("outland", "web", "bot")
        cls.procs = procs.owned(cls)
        cls.procs.append(spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "none"))
        cls.procs.append(spawn(*adapter_args("adapters.outland", cls.port, cls.fe, cls.keys, "outland"), *FAST))
        cls.procs.append(spawn(*adapter_args("adapters.web", cls.web, cls.fe, cls.keys, "web")))
        cls.procs.append(spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", os.path.join(cls.keys, "bot.key"),
                               "--think", "0,0", "--pace", "0", "--poll", "0.3"))
        end = time.time() + 20
        while "bot" not in cls.who():
            assert time.time() < end, "the bot did not sign in"
            time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)

    @classmethod
    def who(cls):
        s = socket.create_connection(("127.0.0.1", cls.core), timeout=5)
        s.sendall(b"PARLORNET/1\r\n")
        f = s.makefile("rb")
        f.readline()
        s.sendall(b"HELO whocheck\r\nWHO\r\n")
        lines = []
        while not (len(lines) > 1 and lines[-1].startswith("OK")):
            lines.append(f.readline().decode().strip())
        s.close()
        return [l.split()[1] for l in lines if l.startswith("PLR ")]

    native = LiveBase.native

    def view(self, req, tid):
        return parse_reversi_view(req("VUE %s M" % tid))

    def reversi(self, name, gw=None):
        g = gw or Gateway(self.port)
        self.addCleanup(g.close)
        if gw is None:
            g.login(name, "")
            self.assertEqual(g.ctl("Y"), Y_BOTH)
        return RvClient(g).open()

    def play_out(self, c, tid, req, web=None, web_seat=None, stop_after=None, greedy=False):
        """Until the game ends: the client moves when told, the web player (if any) moves from the view.
        Whenever the client is told to move, its board is the core's and the turn is its own."""
        mine = 0
        end = time.time() + 120
        while c.ended is None and time.time() < end:
            c.pump(0.1)
            if c.my_move:
                v = self.view(req, tid)
                self.assertEqual((v["turn"], v["rows"]), (0 if c.first else 1, c.rows()))
                if stop_after and mine >= stop_after:
                    return
                c.play(mine, greedy)
                mine += 1
            elif web:
                v = self.view(req, tid)
                if v["turn"] == web_seat and v["moves"]:
                    self.assertEqual(self.web_get("/move.htm?t=%s&mv=%s&n=%s" % (tid, v["moves"][-1], web))[0], 302)
        self.assertIsNotNone(c.ended, "the game did not end")
        v = self.view(req, tid)
        self.assertEqual(v["rows"], c.rows())
        n0, n1 = v["counts"]
        mine_n, theirs = (n0, n1) if c.first else (n1, n0)
        self.assertEqual(c.ended, 1 if n0 == n1 else 2 if mine_n > theirs else 3)
        return v

    def test_reversi_against_a_web_player(self):
        self.web_login("wes")
        loc = self.web_get("/new.htm?g=reversi%2F1&turn=1h&n=wes")[1]
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        req = self.native("wes")
        c = self.reversi("rex")
        ids = c.ids()
        self.assertEqual(c.players[c.me][0], "You")
        self.assertEqual(c.players[ids["wes"]][1], 1)               # waiting at an open table: in the room
        self.assertEqual(c.players[ids["bot"]][1], O.RV_BOT_FREE)   # always free: drawn with no hourglass
        self.assertIn(O.RV_SERVER, ids)
        c.wait(lambda: (False, O.RV_SERVER, O.RV_BOT_HOWTO) in c.talk)  # how to play the bot, as rex comes in
        # lobby talk: the client prints its own line when it comes back
        c.send(O.rv_varlen(0x14, b"hello room\0"))
        c.wait(lambda: ("You" in [t[1] for t in c.talk]))
        self.assertIn((False, "You", "hello room"), c.talk)
        # an invitation to wes, who waits alone at an open table: rex joins it and moves second
        c.send(bytes([0x15, ids["wes"], 1]))
        c.wait(lambda: c.in_game)
        start = [x for x in c.cmds if x[0] == 0x5f][0]
        info = [x for x in c.cmds if x[0] == 0x5e][0]
        self.assertEqual((start[1], start[2]), (ids["wes"], c.me))   # wes sat first: seat 0 moves first
        self.assertEqual(info[1:], bytes([0, ids["wes"], c.me]))
        self.assertLess(c.cmds.index(info), c.cmds.index(start))      # GameInfo before GameStart
        self.assertFalse(c.first)
        # game talk both ways; the client's own line comes back with its own id
        c.send(O.rv_varlen(0x1a, b"good luck\0"))
        c.wait(lambda: (True, "You", "good luck") in c.talk)
        req("CHT %s thanks rex" % tid)
        c.wait(lambda: (True, "wes", "thanks rex") in c.talk)
        # the client takes the most discs and wes the last square listed: wes runs out of moves (a pass,
        # sent as wes's move) and the game ends before the board is full
        self.play_out(c, tid, req, web="wes", web_seat=0, stop_after=4, greedy=True)
        # the Gateway goes away and comes back: the game is shown again from its first move
        c.g.close()
        time.sleep(0.5)
        c = self.reversi("rex")
        c.wait(lambda: c.in_game and c.count > 0)
        c.wait(lambda: c.my_move or self.view(req, tid)["turn"] == 0)
        if self.view(req, tid)["turn"] == 0:
            self.assertEqual(self.view(req, tid)["rows"], c.rows())
        v = self.play_out(c, tid, req, web="wes", web_seat=0, greedy=True)
        self.assertTrue(any(x[0] == 0x60 and x[2] == O.RV_PASS for x in c.cmds))
        self.assertLess(sum(v["counts"]), 64)
        # the game slot is free again: GameInfo with nobody comes right behind GameEnd, in a frame of its own,
        # so it may still be on its way when play_out stops reading at GameEnd
        c.wait(lambda: any(x[0] == 0x5e and x[2] == 24 for x in c.cmds))
        end_at = max(i for i, x in enumerate(c.cmds) if x[0] == 0x65)
        self.assertEqual(c.cmds[end_at + 1], bytes([0x5e, 0, 24, 24]))
        c.send(b"\x18")                                                     # the board put away
        wes = c.ids()["wes"]
        c.wait(lambda: c.players[wes][1] == 1)                             # wes is back in the room

    def test_reversi_says_with_what_the_opponent_plays(self):
        """Joining a native player's table: the parlor's line after the start says what they play with (VUE C),
        and a line from them on another program is told in the game's talk."""
        req = self.native("vin")
        req("CLI PROG Xreversi 3")
        tid = req("NEW reversi standard")[-1].split()[1]
        req("JON %s 0" % tid)
        c = self.reversi("ron")
        c.wait(lambda: "vin" in c.ids())
        c.send(bytes([0x15, c.ids()["vin"], 1]))
        c.wait(lambda: c.in_game)
        c.wait(lambda: any(t[2] == "vin plays with Xreversi 3." for t in c.talk))
        other = self.native("vin")
        other("CLI PROG Telnet 1")
        other("CHT %s hello" % tid)
        c.wait(lambda: any(t[2] == "vin now plays with Telnet 1." for t in c.talk))
        c.wait(lambda: (True, "vin", "hello") in c.talk)
        req("RES %s" % tid)

    def test_reversi_against_the_bot(self):
        c = self.reversi("rob")
        ids = c.ids()
        c.send(bytes([0x15, ids["bot"], 1]))                        # rob moves first
        c.wait(lambda: c.in_game, timeout=20)
        self.assertTrue(c.first)
        self.assertTrue(any("bot" in t[2] for t in c.talk))         # the server says the bot will accept
        req = self.native("rob")
        tid = next(t["id"] for t in parse_tables(req("LST")) if "rob" in t["names"] and t["key"] == O.REVERSI)
        c.wait(lambda: any(t[0] and t[1] == "bot" for t in c.talk))   # its hello, in the game's talk window
        v = self.play_out(c, tid, req)
        self.assertEqual(v["turn"], -1)

    def test_two_outland_players_and_leaving(self):
        ann, bob = self.reversi("ann"), self.reversi("bob")
        ann.wait(lambda: "bob" in ann.ids())
        # ann invites bob, bob moves first (ann's choice: move last)
        ann.send(bytes([0x15, ann.ids()["bob"], 0]))
        bob.wait(lambda: bob.invites)
        pid, flags = bob.invites.popleft()
        self.assertEqual((bob.players[pid][0], flags), ("ann", 0))    # "You would move first"
        bob.send(bytes([0x16, pid, 1]))
        ann.wait(lambda: ann.in_game)
        bob.wait(lambda: bob.in_game)
        self.assertTrue(bob.first and not ann.first)
        req = self.native("ann")
        tid = next(t["id"] for t in parse_tables(req("LST")) if "ann" in t["names"] and t["key"] == O.REVERSI)
        for _ in range(3):
            for p in (bob, ann):
                p.wait(lambda: p.my_move)
                p.play()
                other = ann if p is bob else bob
                other.wait(lambda: other.count == p.count)
                self.assertEqual(other.board, p.board)
        self.assertEqual(self.view(req, tid)["rows"], ann.rows())
        # bob puts the board away: he leaves the game, and ann's client says her opponent left
        bob.send(b"\x18")
        ann.wait(lambda: ann.ended is not None)
        self.assertEqual(ann.ended, 4)
        ann.send(b"\x18")
        # a declined invitation: message 1 closes the inviter's window
        bob.wait(lambda: bob.players.get(bob.ids().get("ann"), [0, 0])[1] == 1)
        bob.send(bytes([0x15, bob.ids()["ann"], 1]))
        ann.wait(lambda: ann.invites)
        pid, flags = ann.invites.popleft()
        self.assertEqual(flags, 1)                                  # "You would move last"
        ann.send(bytes([0x16, pid, 0]))
        bob.wait(lambda: 1 in bob.messages)

    def test_reversi_player_invited_from_the_web(self):
        c = self.reversi("ivy")
        self.web_login("wyn")
        loc = self.web_get("/new.htm?g=reversi%2F1&turn=1h&vis=private&n=wyn")[1]
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        st, _, body = self.web_get("/invite.htm?t=%s&who=ivy&n=wyn" % tid)
        self.assertEqual(st, 302, body[-300:])
        c.wait(lambda: c.invites)
        pid, flags = c.invites.popleft()
        self.assertEqual(c.players[pid][0], "wyn")
        c.send(bytes([0x16, pid, 1]))
        c.wait(lambda: c.in_game)
        self.web_get("/resign.htm?t=%s&sure=1&n=wyn" % tid)
        c.wait(lambda: c.ended is not None)
        self.assertEqual(c.ended, 4)                                # "Your opponent leaves the game."
        c.send(b"\x18")
        # declined: the web table's log says so
        loc = self.web_get("/new.htm?g=reversi%2F1&turn=1h&vis=private&n=wyn")[1]
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        self.web_get("/invite.htm?t=%s&who=ivy&n=wyn" % tid)
        c.wait(lambda: c.invites)
        pid, _ = c.invites.popleft()
        c.send(bytes([0x16, pid, 0]))
        end = time.time() + 10
        while "ivy declined the invitation" not in self.web_get("/table.htm?t=%s&n=wyn" % tid)[2]:
            self.assertLess(time.time(), end)
            time.sleep(0.3)

    # ---- games left running in the core: the lobby says so, /resume brings them back -----------------------
    def native_turn(self, req, tid, seat, game=O.BACKGAMMON):
        """Play a seat's whole turn through the native protocol (the first legal move each time)."""
        for _ in range(12):
            v = (LiveBase.view(req, tid) if game == O.BACKGAMMON else self.view(req, tid))
            if v["turn"] != seat:
                return
            if game == O.REVERSI:
                self.assertEqual(req("MOV %s %s" % (tid, v["moves"][0]))[-1][:2], "OK")
                continue
            mv = "roll" if v["phase"] == "roll" else "take" if v["phase"] == "take" else \
                v["moves"][0] if v["moves"] else None
            req("MOV %s %s" % (tid, mv) if mv else "END %s" % tid)
        raise AssertionError("the turn did not end")

    def running_game(self, me, opp, game, my_turn):
        """A game of `game` between opp (seat 0) and me (seat 1), both through the native protocol, brought to
        a turn of mine or of the opponent's."""
        ro, rm = self.native(opp), self.native(me)
        tid = ro("NEW %s standard%s" % (game, " cube=0" if game == O.BACKGAMMON else ""))[-1].split()[1]
        ro("JON %s 0" % tid)
        rm("JON %s 1" % tid)
        view = LiveBase.view if game == O.BACKGAMMON else self.view
        for _ in range(3):
            turn = view(ro, tid)["turn"]
            if turn == (1 if my_turn else 0):
                return tid, ro, rm
            self.native_turn(rm if turn == 1 else ro, tid, turn, game)
        raise AssertionError("no turn of the kind wanted")

    def bg_lobby(self, name):
        g = Gateway(self.port)
        self.addCleanup(g.close)
        g.login(name, "")
        g.open_backgammon()
        return g, g.lobby()

    def lobby_chat(self, g, text):
        lc = O.record(O.C_LOBBYCHAT)
        lc[2:3 + len(text)] = O.pstr(text)
        g.send_rec(lc)

    def test_backgammon_game_on_the_opponents_move_waits(self):
        """A running game on the opponent's move: the lobby says so, with no challenge, and the player is
        free to challenge the bot."""
        tid, ro, rm = self.running_game("rho", "dan", O.BACKGAMMON, my_turn=False)
        g, players = self.bg_lobby("rho")
        early = g.quiet(1.5)
        said = notices(early)
        self.assertIn("Your game with dan at table %s waits for dan's move. Type /resume in the lobby chat to "
                      "bring it back." % tid, said)
        self.assertTrue(any(l.startswith("Table %s with dan: dan's move" % tid) for l in said), said)
        self.assertIn(O.COMMANDS_LINE, said)
        self.assertFalse([r for r in early + g.seen if r[0] == O.S_CHALLENGE])
        ch = O.record(O.C_CHALLENGE)
        ch[2] = players["bot"][0]
        struct.pack_into(">H", ch, 4, 1)
        g.send_rec(ch)
        got = g.rec(O.S_RESPONSE, timeout=15)
        self.assertEqual((got[2], got[6]), (players["bot"][0], 1))          # accepted: no "Finish your game"
        self.assertFalse(any("Finish your game" in n for n in notices(g.seen)))

    def test_backgammon_resume_release_and_offer(self):
        """/resume brings the game back; going back to the lobby on the opponent's move releases it; the
        opponent's move offers it again; /list, /help and the other chat commands answer in the lobby."""
        tid, ro, rm = self.running_game("rae", "dov", O.BACKGAMMON, my_turn=False)
        g, players = self.bg_lobby("rae")
        g.quiet(1)
        self.lobby_chat(g, "/list")
        said = notices(g.quiet(1))
        self.assertEqual(said[0], "Your Backgammon games in progress:")
        self.assertTrue(said[1].startswith("Table %s with dov: dov's move, " % tid), said)
        self.lobby_chat(g, "/help")
        self.assertEqual(notices(g.quiet(1)), O.HELP_LINES)
        # the shared chat commands (adapters/chatcmd.py): /who from the core, /invite pointing at the
        # client's own window, an unknown slash word answered, and a plain word left as chat
        self.lobby_chat(g, "/who")
        said = notices(g.quiet(1))
        self.assertTrue(said and said[0].startswith("Online: ") and "rae" in said[0], said)
        self.lobby_chat(g, "/invite dov")
        self.assertEqual(notices(g.quiet(1)), [O.BG_INVITE_HERE])
        self.lobby_chat(g, "/dance")
        self.assertEqual(notices(g.quiet(1)), ["No such command: /dance. Type /help for the commands."])
        self.lobby_chat(g, "who")
        self.assertEqual(notices(g.quiet(1)), [])
        self.lobby_chat(g, "/resume")
        inv = g.rec(O.S_CHALLENGE)
        self.assertEqual(inv[2], players.get("dov", (None,))[0] or inv[2])
        resp = bytearray(inv)
        resp[0], resp[6], resp[7] = O.C_RESPONSE, 1, 0                       # Red, but rae has seat 1
        g.send_rec(resp)
        self.assertIn(O.FIXED_SIDE % ("Red", "black"), notices(g.quiet(1.5)))
        # back to the lobby on dov's move: the game is released, and rae is free
        g.send_rec(O.record(O.C_RELOBBY))
        self.assertIn("Your game with dov at table %s goes on. Type /resume in the lobby chat to bring it back."
                      % tid, notices(g.quiet(1)))
        # dov moves: the game is offered again with its challenge
        self.native_turn(ro, tid, 0)
        inv = g.rec(O.S_CHALLENGE, timeout=15)
        self.assertTrue(any("dov has moved in your game at table %s" % tid in n for n in notices(g.seen)), g.seen)
        self.assertEqual(LiveBase.view(rm, tid)["turn"], 1)

    def test_reversi_game_on_the_opponents_move_waits(self):
        tid, ro, rm = self.running_game("rio", "dee", O.REVERSI, my_turn=False)
        c = self.reversi("rio")
        c.wait(lambda: any("waits for dee's move" in t[2] for t in c.talk))
        c.pump(1)
        self.assertFalse(c.in_game)                                 # no board opened unasked
        self.assertIn((False, O.RV_SERVER, O.COMMANDS_LINE), c.talk)
        c.send(O.rv_varlen(0x14, b"/list\0"))
        c.wait(lambda: any(t[2].startswith("Table %s with dee: dee's move" % tid) for t in c.talk))
        c.send(O.rv_varlen(0x14, b"/accept dee\0"))
        c.wait(lambda: any(t[2] == O.ACCEPT_HERE for t in c.talk))
        c.send(O.rv_varlen(0x14, b"/resume\0"))
        c.wait(lambda: c.in_game)
        c.pump(0.5)
        self.assertEqual(self.view(rm, tid)["rows"], c.rows())
        self.assertTrue(any("Putting the board away gives the game up" in t[2] for t in c.talk))
        # dee moves: the client is told to move
        self.native_turn(ro, tid, 0, O.REVERSI)
        c.wait(lambda: c.my_move)
        self.assertEqual(self.view(rm, tid)["rows"], c.rows())


if __name__ == "__main__":
    unittest.main()
