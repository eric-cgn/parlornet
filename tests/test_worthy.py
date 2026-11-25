"""The Worthy Opponent extension (adapters/worthy.py): CornerStone, Checkers and Quinte for Worthy Opponent 2.0
(DOS), dialed by modem.

The scripted PC below behaves as the program does: nine-byte frames,
each answered with 06 and sent again without it; on connecting it sends its version twice and its name; Talk is
typed keys; it picks a game with EG, sends the seed and the clocks, and plays both sides of the game from the
keys, as the program's own board model (the extension's, with the sides swapped: the PC's player is "par" in
its own view and the parlor's keys come in as the other side's). Its moves are its keys: 82, the cursor's way,
Space, 81 unless the move ends the game. It can be a telnet line: it doubles FF and asks for binary."""
import random
import re
import socket
import sys
import threading
import time
import unittest

from test_adapters import spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import worthy as W  # noqa: E402

WAIT = 25
LOG = []
SEED_PC_FIRST, SEED_PARLOR_FIRST = 791416092, 38997274       # captured game starts (first_mover)


def raw_frame(seq, kind, a, b):
    return W.frame(seq, kind, a, b)


class Pc:
    """Worthy Opponent 2.0 on a PC that has dialed the parlor."""

    def __init__(self, port, name, telnet=False):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.name, self.telnet = name, telnet
        self.seq = 1
        self.buf = bytearray()
        self.last_in = None
        self.acks = 0
        self.talk = [""]            # the Talk window's lines from the parlor
        self.cmds = []              # type 4 commands from the parlor
        self.text = bytearray()     # type 3 bytes
        self.board = None           # the game's model, "par" being this PC's player
        self.keys_in = []
        self.closed = False
        self.telnet_in = bytearray()
        if telnet:
            self.s.sendall(b"\xff\xfd\x00\xff\xfb\x00")          # DO BINARY, WILL BINARY
        self.send_text([W.VERSION, W.VERSION, name])

    def close(self):
        self.s.close()

    # ---- the wire ----------------------------------------------------------------------------------
    def out(self, data):
        self.s.sendall(data.replace(b"\xff", b"\xff\xff") if self.telnet else data)

    def pump(self, timeout=0.2):
        self.s.settimeout(timeout)
        try:
            data = self.s.recv(65536)
        except socket.timeout:
            return
        except OSError:
            data = b""
        if not data:
            self.closed = True
            return
        if self.telnet:
            out = bytearray()
            i = 0
            while i < len(data):
                c = data[i]
                if c == 0xFF and i + 1 < len(data) and data[i + 1] == 0xFF:
                    out.append(0xFF)
                    i += 2
                elif c == 0xFF and i + 2 < len(data) and data[i + 1] in (0xFB, 0xFC, 0xFD, 0xFE):
                    self.telnet_in += data[i:i + 3]
                    i += 3
                else:
                    assert c != 0xFF, "a bare FF on a telnet line: %r" % data[i:i + 3]
                    out.append(c)
                    i += 1
            data = bytes(out)
        self.buf += data
        while self.buf:
            if self.buf[0] == W.ACK:
                self.acks += 1
                del self.buf[0]
                continue
            if len(self.buf) < 9:
                break
            assert self.buf[:2] == b"\x00\x02", "not a frame: %r" % bytes(self.buf[:12])
            fr = bytes(self.buf[:9])
            del self.buf[:9]
            assert W.checksum_ok(fr), "a bad frame: %r" % fr
            self.out(bytes([W.ACK]))
            if fr[2] >> 4 == self.last_in:
                continue
            self.last_in = fr[2] >> 4
            self.got(fr[2] & 15, fr[3], fr[4])

    def got(self, kind, a, b):
        if kind == W.TALK:
            for c in (a, b):
                if c == 0x0D:
                    self.talk.append("")
                elif c:
                    self.talk[-1] += chr(c)
        elif kind == W.CMD:
            self.cmds.append(bytes([a, b]))
        elif kind == W.TEXT:
            self.text += bytes([a, b])
        elif kind == W.KEYS:
            for c in (a, b):
                if c:
                    self.keys_in.append(c)
                    self.feed(c)

    def send(self, kind, a, b):
        """One frame, sent until the 06 comes."""
        fr = raw_frame(self.seq, kind, a, b)
        self.seq = W.next_seq(self.seq)
        for _ in range(5):
            n = self.acks
            self.out(fr)
            end = time.time() + 1.2
            while time.time() < end and self.acks == n and not self.closed:
                self.pump(0.1)
            if self.acks > n:
                return
        raise AssertionError("no 06 for %r; log %r" % (fr, LOG[-10:]))

    def send_text(self, fields):
        data = b"".join(f.encode("latin-1") + b"\r" for f in fields)
        data += b"\x00" * (len(data) % 2)
        for i in range(0, len(data), 2):
            self.send(W.TEXT, data[i], data[i + 1])

    def command(self, text):
        b = text.encode("latin-1")
        b += b"\r" * (len(b) % 2)
        for i in range(0, len(b), 2):
            self.send(W.CMD, b[i], b[i + 1])

    def say(self, line, close=True):
        """Typed in Talk (F6), with Enter; then Talk is closed (Esc)."""
        self.command("ET\r")
        for ch in (line + "\r").encode("ascii"):
            self.send(W.TALK, ch, 0)
        if close:
            self.command("XT\r")

    def read(self, until, timeout=WAIT):
        end = time.time() + timeout
        while True:
            got = until()
            if got:
                return got
            if time.time() > end or self.closed:
                raise AssertionError("%s; talk %r; log %r" % ("closed" if self.closed else "timed out",
                                                              self.talk[-8:], LOG[-12:]))
            self.pump(0.2)

    def mark(self):
        """Where the lines still to come start (the line being typed is one of them)."""
        return len(self.talk) - 1

    def said(self, text, since=0):
        """Whole lines of the Talk window (the last one is still being typed)."""
        return [l for l in self.talk[since:-1] if text in l]

    def wait_talk(self, text, since=0):
        return self.read(lambda: self.said(text, since))

    def fields(self, n):
        parts = bytes(self.text).replace(b"\x00", b"").split(b"\r")
        if len(parts) <= n:
            return None
        self.text = bytearray(b"\r".join(parts[n:]))
        return [p.decode("latin-1") for p in parts[:n]]

    # ---- a game ------------------------------------------------------------------------------------
    def pick(self, number, seed):
        """F8: the game, then the start fields once the parlor enters it; -> the opponent's name."""
        self.cmds = []
        self.command("EG%s\r" % chr(number))
        self.read(lambda: any(c[:2] == b"EG" for c in self.cmds))
        self.text = bytearray()
        self.send_text(["??????", self.name, "%11d" % seed, "%6d" % 3600, "%6d" % 3600, self.name])
        name = self.read(lambda: self.fields(1))[0]
        first = W.first_mover(seed)
        self.board = W.MODELS[number]("par" if first == "dos" else "dos")
        self.keys_in = []
        return name

    def feed(self, k):
        """The parlor's key, as the program plays the other side."""
        bd = self.board
        if bd is None or bd.over or k in (W.PAUSE, W.RESUME):
            return
        assert bd.turn == "dos", "the parlor's key %02x out of its turn" % k
        if k == W.TURN_START:
            bd.start_turn("dos")
            return
        assert bd.started["dos"], "a key before 82: %02x" % k
        if k == W.TURN_END:
            bd.end_turn("dos")
        elif k in W.DIRECTION:
            bd.move_cursor("dos", k)
        elif k == W.SPACE:
            if isinstance(bd, W.Checkers):
                assert bd.carry or bd.pieces.get(bd.cursor["dos"], ("",))[0] == "dos"
                bd.space("dos")
            else:
                assert bd.place("dos", *bd.cursor["dos"]), "the parlor's Space on an illegal square"
        else:
            raise AssertionError("an unknown key %02x" % k)

    def my_turn(self):
        bd = self.board
        return bd.over or bd.turn == "par"

    def move(self, choice, first=False):
        """This PC's move: its keys (82 first, Space, 81 unless the game ends) -> the keys sent."""
        bd = self.board
        bd.start_turn("par")
        keys = [W.TURN_START]
        if choice is None:
            keys.append(W.TURN_END)                     # no move: Enter, and the program passes
            bd.end_turn("par")
        else:
            keys += bd.keys_for(choice)
            if not isinstance(bd, W.Checkers):
                bd.place("par", *choice)
            if not bd.over:
                keys.append(W.TURN_END)
                bd.end_turn("par")
        for k in keys:
            self.send(W.KEYS, bd.turn_key(k), 0)       # as the program sends them: turned to the other board
        return keys

    def choose(self, rng):
        bd = self.board
        if isinstance(bd, W.Cornerstone):
            legal = bd.legal("par")
            return rng.choice(legal) if legal else None
        if isinstance(bd, W.Quinte):
            from bot.games import fiveinarow as B
            cells = ["."] * 361
            for (r, c), s in bd.stones.items():
                cells[r * 19 + c] = str(bd.seat(s))
            i = B.choose({"n": 19, "rules": "freestyle", "cells": "".join(cells)}, bd.seat("par"), rng)
            return i // 19, i % 19
        return rng.choice(checkers_moves(bd))

    def play_out(self, rng, limit=200):
        """Moves until the game ends on this PC's board."""
        bd = self.board
        n = 0
        while not bd.over:
            self.read(self.my_turn)
            if bd.over:
                break
            if isinstance(bd, W.Cornerstone) and not bd.legal("par") and not bd.legal("dos"):
                break
            self.move(self.choose(rng))
            n += 1
            assert n < limit
        return n


def checkers_moves(bd):
    """Every whole move of the PC's side ("par"): [(from, to), ...] steps, in its own view."""
    def chains(sq, path):
        res = []
        for t, m in bd.jumps(sq):
            saved = dict(bd.pieces)
            piece = bd.pieces.pop(sq)
            del bd.pieces[m]
            crowned = not piece[1] and t[0] == 0
            bd.pieces[t] = ("par", True) if crowned else piece
            more = [] if crowned else chains(t, path + [(sq, t)])
            res += more or [path + [(sq, t)]]
            bd.pieces = saved
        return res
    own = [sq for sq, (s, _) in bd.pieces.items() if s == "par"]
    if bd.must_jump("par"):
        return [c for sq in own for c in chains(sq, [])]
    return [[(sq, t)] for sq in own for t in bd.steps(sq)]


class Wire(unittest.TestCase):
    """The formats, against frames captured between two copies of Worthy Opponent 2.0."""

    def test_captured_frames(self):
        self.assertEqual(W.frame(1, W.TEXT, 0x20, 0x56), bytes.fromhex("00021320562056ff00"))
        self.assertEqual(W.frame(6, W.CMD, ord("E"), ord("T")), bytes.fromhex("000264455445549600"))
        self.assertEqual(W.frame(10, W.KEYS, 0x82, 0), bytes.fromhex("0002a282008200a600"))
        self.assertEqual(W.frame(13, W.TALK, ord("t"), 0), bytes.fromhex("0002d174007400b900"))
        self.assertTrue(W.checksum_ok(bytes.fromhex("000233333833380900")))
        self.assertFalse(W.checksum_ok(bytes.fromhex("000233333833380a00")))
        self.assertEqual([W.next_seq(s) for s in (1, 14, 15)], [2, 15, 1])

    def test_link(self):
        ln = W.Link()
        hello = bytes.fromhex("00021320562056ff00")
        got = ln.feed(hello[:5])
        self.assertEqual(got, [])
        got = ln.feed(hello[5:] + hello + b"\x06" + bytes.fromhex("00022365726572d100"))
        self.assertEqual(got, [("frame", 3, 0x20, 0x56), ("again",), ("ack",), ("frame", 3, 0x65, 0x72)])
        self.assertFalse(ln.telnet)
        self.assertEqual(ln.feed(b"junk" + bytes.fromhex("00023373697369eb00")), [("frame", 3, 0x73, 0x69)])
        self.assertEqual(ln.escape(b"\xff"), b"\xff")

    def test_telnet_line(self):
        """A telnet line: its doubled FF is one FF, and it shows the line is telnet; negotiation is answered,
        and the extension's own FF goes doubled."""
        ln = W.Link()
        got = ln.feed(b"\xff\xfd\x00\xff\xfd\x01\xff\xfb\x00" + bytes.fromhex("00021320562056ffff00"))
        self.assertEqual(got, [("frame", 3, 0x20, 0x56)])
        self.assertTrue(ln.telnet and ln.binary)
        self.assertEqual(ln.take_replies(), b"\xff\xfb\x00\xff\xfc\x01\xff\xfd\x00")
        self.assertEqual(ln.escape(b"\x00\xff\x00"), b"\x00\xff\xff\x00")
        ln = W.Link()
        ln.feed(bytes.fromhex("00021320562056ffff00"))
        self.assertTrue(ln.telnet and not ln.binary)        # CR NUL is CR on such a line
        self.assertEqual(ln.feed(bytes.fromhex("0002210d00000d00003b00")), [("frame", 1, 0x0D, 0)])

    def test_clean_frames(self):
        """Talk text and names avoid FF and CR NUL in every frame, whatever the numbering."""
        text = b"Welcome to ParlorNet.\rTo sign in, type /signin\r"
        for seq in range(1, 16):
            pairs = W.split_text(text, seq, single=True)
            s, back = seq, b""
            for a, b in pairs:
                self.assertTrue(W.clean_frame(W.frame(s, W.TALK, a, b)))
                back += bytes([a]) + (bytes([b]) if b else b"")
                s = W.next_seq(s)
            self.assertEqual(back, text)

    def test_first_mover(self):
        """The seed's first Random(100): above 50 gives the caller (the PC) the first move. Captured starts
        (993725981 gives exactly 50: the parlor moved first)."""
        caller = [101059879, 588451113, 841878825, 100666666, 355536170, 875826474, 791416092, 942935341,
                  743443758, 553848111, 319425843, 341249338]
        answerer = [272108836, 620956970, 38997274, 727518512, 339676462, 139857199, 906693920, 168562181,
                    455282182, 537529863, 993725981]
        self.assertEqual({W.first_mover(s) for s in caller}, {"dos"})
        self.assertEqual({W.first_mover(s) for s in answerer}, {"par"})

    def test_program(self):
        self.assertEqual(W.program_claim(" Version 2.0 "), "Worthy Opponent 2.0")
        self.assertEqual(W.program_claim("??"), "Worthy Opponent")


class Boards(unittest.TestCase):
    """The models, against keys captured between two copies."""

    def test_cornerstone_cursor_and_pass(self):
        """The captured pass game: the second player's cursor goes to its first stone when its own was
        flipped; after eight moves the first player has no move."""
        bd = W.Cornerstone("dos")
        caps = [("dos", [W.PGUP]), ("par", [W.PGUP, W.PGUP]), ("dos", [W.RIGHT, W.RIGHT]), ("par", [W.PGUP, W.PGUP]),
                ("dos", [W.PGDN, W.PGDN, W.RIGHT, W.RIGHT]), ("par", [W.PGDN]),
                ("dos", [W.END, W.END, W.END, W.DOWN]), ("par", [W.HOME, W.HOME])]
        for side, keys in caps:
            bd.start_turn(side)
            for k in keys:
                bd.move_cursor(side, k)
            self.assertTrue(bd.place(side, *bd.cursor[side]), (side, bd.cursor[side]))
            bd.end_turn(side)
        self.assertEqual(bd.legal("dos"), [])
        self.assertTrue(bd.legal("par"))
        self.assertFalse(bd.over)
        self.assertEqual(W.Cornerstone.square(3, 3), "e4")
        self.assertEqual(W.Cornerstone.from_square("e4"), (3, 3))

    def test_cornerstone_wipe_out(self):
        bd = W.Cornerstone("par")
        for side, sq in zip(["par", "dos"] * 5, [(2, 4), (2, 3), (1, 2), (1, 5), (1, 4), (2, 5), (4, 2), (1, 3),
                                                  (1, 6)]):
            self.assertTrue(bd.place(side, *sq))
        self.assertTrue(bd.over)

    def test_checkers_keys(self):
        """Captured: the first player's PgUp arrives as End; a jump is one diagonal key; Space on a man that
        cannot jump, when a jump is due, picks nothing up; the cursor goes to the first man in reading order
        when its square was emptied."""
        bd = W.Checkers("dos")              # the PC (first) at the top of the parlor's board
        self.assertEqual(bd.cursor["dos"], (2, 3))
        bd.start_turn("dos")
        bd.space("dos")
        bd.move_cursor("dos", W.END)        # the PC's PgUp
        self.assertTrue(bd.space("dos"))
        self.assertEqual(bd.tokens(), ["10-14"])
        bd.end_turn("dos")
        bd.start_turn("par")
        keys = bd.keys_for([((5, 4), (4, 3))])
        self.assertEqual([bd.turn_key(k) for k in keys], [W.SPACE, W.PGDN, W.SPACE])
        bd.end_turn("par")
        bd.start_turn("dos")
        self.assertTrue(bd.must_jump("dos"))
        bd.space("dos")                     # (3,2) jumps (4,3)
        bd.move_cursor("dos", W.PGDN)
        self.assertTrue(bd.space("dos"))
        self.assertEqual(bd.tokens(), ["14x23"])
        bd.end_turn("dos")
        bd.start_turn("par")
        self.assertEqual(bd.cursor["par"], (5, 0))

    def test_quinte_overline(self):
        bd = W.Quinte("par")
        for c in (3, 4, 5, 7, 8):
            bd.place("par", 7, c)
        self.assertFalse(bd.over)
        bd.place("par", 7, 6)
        self.assertTrue(bd.over)
        self.assertEqual((W.Quinte.square(9, 9), W.Quinte.from_square("j10")), ("j10", (9, 9)))


class Stack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("worthy", "bot")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password"),
                     spawn(*adapter_args("adapters.worthy", cls.port, cls.fe, cls.keys, "worthy"), "--poll", "0.2")]
        cls.procs.append(spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", cls.keys + "/bot.key",
                               "--think", "0,0", "--pace", "0", "--poll", "0.3"))
        del LOG[:]
        for i, p in enumerate(cls.procs):
            for f in (p.stdout, p.stderr):
                keep = LOG if (i == 1 and f is p.stderr) else None
                threading.Thread(target=lambda f=f, keep=keep: [keep.append(l.decode(errors="replace").rstrip())
                                                                if keep is not None else None
                                                                for l in iter(f.readline, b"")],
                                 daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()

    def signup(self, name):
        from test_fibs import Stack as FibsStack
        return FibsStack.password_for(self, name)

    def native(self, name, password):
        from test_fibs import Native
        n = Native(self.core, name, password)
        self.addCleanup(n.close)
        return n

    def pc(self, name, telnet=False):
        p = Pc(self.port, name, telnet)
        self.addCleanup(p.close)
        return p

    def signed_in(self, name, pw, telnet=False):
        p = self.pc(name, telnet)
        p.wait_talk("To sign in as %s, type /signin and your password." % name)
        n = p.mark()
        p.say("/signin %s" % pw)
        p.wait_talk("Signed in as %s." % name, n)
        return p


class Service(Stack):
    def test_hello_and_sign_in(self):
        p = self.pc("ann")
        p.read(lambda: p.fields(3))
        p.wait_talk("type /signin and your password")
        self.assertIn(b"ET", p.cmds[0])                 # the F6 line lights up
        n = p.mark()
        p.say("hello")
        p.wait_talk("Sign in first", n)
        p.command("EG\x06\r")                           # Backgammon, before sign-in: not played here
        p.wait_talk("Backgammon is not played here", n)
        pw = self.signup("ann")
        self.signup("abe")
        n = p.mark()
        p.say("/signin abe not-the-password")
        p.read(lambda: len(p.talk) > n + 2)
        self.assertFalse(p.said("Signed in", n))
        p.command("EG\x02\r")                           # CornerStone: waits for the sign-in
        p.wait_talk("CornerStone starts.", n)
        n = p.mark()
        p.say("/signin %s" % pw)
        p.wait_talk("Signed in as ann.", n)
        p.read(lambda: any(c[:2] == b"EG" for c in p.cmds))
        n = p.mark()
        p.say("/help")
        p.wait_talk("/invite NAME", n)
        p.say("/quit", close=False)
        p.read(lambda: p.closed)

    def game(self, name, number, seed, telnet=False):
        pw = self.signup(name)
        p = self.signed_in(name, pw, telnet)
        n = p.mark()
        self.assertEqual(p.pick(number, seed), "ParlorNet")
        p.wait_talk("you move first" if W.first_mover(seed) == "dos" else "your opponent", n)
        p.say("/invite bot")
        p.wait_talk("Your game with bot on table", n)
        tid = re.search(r"on table (\w+) starts", p.said("Your game with bot")[0]).group(1)
        return p, pw, tid

    def test_cornerstone_against_the_bot(self):
        """The PC moves first (the seed); a whole game; the core's board ends as the PC's."""
        p, pw, tid = self.game("bea", W.CORNERSTONE, SEED_PC_FIRST)
        p.play_out(random.Random(3))
        p.read(lambda: p.said("wins") or p.said("You win") or p.said("A draw"))
        nat = self.native("bea", pw)
        v = nat.req("VUE %s" % tid)
        self.assertIn("W -1", v)
        rows = [l.split(" ", 2)[2] for l in v if l.startswith("R ")]
        bd = p.board
        for (r, c), s in bd.stones.items():
            self.assertEqual(rows[r][7 - c], str(bd.seat(s)), (r, c))
        self.assertEqual(sum(row.count(".") for row in rows), 64 - len(bd.stones))
        self.assertTrue(any("Worthy Opponent 2.0" in l for l in nat.req("TRN %s" % tid)))

    def test_checkers_against_the_bot(self):
        """The parlor's side moves first (the seed): the bot's moves come as turned keys; a whole game."""
        p, pw, tid = self.game("cid", W.CHECKERS, SEED_PARLOR_FIRST)
        p.play_out(random.Random(5))
        p.read(lambda: p.said("wins") or p.said("You win") or p.said("draw"))
        nat = self.native("cid", pw)
        v = nat.req("VUE %s" % tid)
        from adapters.games import checkers as CK
        cells = CK.parse_view(v)["cells"]
        bd = p.board
        for n in range(1, 33):
            r, c = CK.rc(n)
            mine = bd.pieces.get((r, c))       # the core's board is the second player's view: the PC's own
            want = "." if mine is None else ("o" if mine[0] == "par" else "x")
            self.assertEqual(cells[n - 1].lower(), want, n)

    def test_quinte_on_a_telnet_line(self):
        """A WiFi modem in telnet mode: doubled FF both ways, binary agreed; a whole Quinte game."""
        p, pw, tid = self.game("dot", W.QUINTE, SEED_PC_FIRST, telnet=True)
        self.assertIn(b"\xff\xfb\x00", p.telnet_in)
        p.play_out(random.Random(9))
        p.read(lambda: p.said("wins") or p.said("You win"))
        nat = self.native("dot", pw)
        from adapters.games import fiveinarow as F
        view = F.parse_view(nat.req("VUE %s" % tid))
        self.assertEqual(len(view["stones"]), len(p.board.stones))
        for (x, y), seat in view["stones"].items():
            self.assertEqual(p.board.seat(p.board.stones[(y, x)]), seat)

    def test_leaving_resigns(self):
        p, pw, tid = self.game("eve", W.CORNERSTONE, SEED_PC_FIRST)
        p.move(p.choose(random.Random(1)))
        p.command("XG\r")
        p.read(lambda: p.said("You resigned table %s." % tid))
        p.read(lambda: b"XG" in b"".join(p.cmds))
        nat = self.native("eve", pw)
        self.assertIn("W -1", nat.req("VUE %s" % tid))

    def test_a_seat_that_does_not_fit(self):
        """An invitation whose seat moves second, while the draw gave the PC the first move: the seat waits for
        a new draw, and is taken when one fits."""
        pw_p, pw_n = self.signup("fay"), self.signup("gus")
        nat = self.native("gus", pw_n)
        tid = nat.req("NEW reversi standard")[-1].split()[1]
        nat.req("JON %s 0" % tid)
        nat.req("INV %s fay 1" % tid)
        p = self.signed_in("fay", pw_p)
        p.wait_talk("gus invites you to Reversi (CornerStone)")
        p.pick(W.CORNERSTONE, SEED_PC_FIRST)
        n = p.mark()
        p.say("/accept gus")
        p.wait_talk("Press F10", n)
        p.command("XG\r")
        n = p.mark()
        p.pick(W.CORNERSTONE, SEED_PARLOR_FIRST)
        p.wait_talk("You sat down at table %s with gus." % tid, n)
        p.wait_talk("Your game with gus on table %s starts" % tid, n)

    def test_who_sits_down_says_with_what(self):
        """Sitting down with an inviter, and an opponent sitting down at the PC's open table: the Talk window
        says what the other plays with (LST clients, the CLIENT event after JOIN)."""
        pw_p, pw_n = self.signup("hal"), self.signup("ivy")
        nat = self.native("ivy", pw_n)
        nat.req("CLI PROG Xboard 4")
        tid = nat.req("NEW reversi standard")[-1].split()[1]
        nat.req("JON %s 0" % tid)
        nat.req("INV %s hal 1" % tid)
        p = self.signed_in("hal", pw_p)
        p.wait_talk("ivy invites you to Reversi (CornerStone)")
        n = p.mark()
        p.pick(W.CORNERSTONE, SEED_PARLOR_FIRST)
        p.say("/accept ivy")
        p.read(lambda: "You sat down at table %s with ivy, who plays with Xboard 4." % tid
               in " ".join(p.talk[n:]))
        p.say("/resign %s yes" % tid)
        p.wait_talk("resigned", n)
        p.command("XG\r")
        n = p.mark()
        p.pick(W.CORNERSTONE, SEED_PC_FIRST)
        p.say("/open")
        p.wait_talk("is open", n)
        tid = re.search(r"table (\w+) is open", p.said("is open", n)[0]).group(1)
        n = p.mark()
        nat.req("JON %s" % tid)
        p.read(lambda: "ivy sits down with Xboard 4." in " ".join(p.talk[n:]))


if __name__ == "__main__":
    unittest.main()
