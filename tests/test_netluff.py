"""The NetLuff extension (adapters/netluff.py): Five in a Row for NetLuff 1.4 on the Mac.

The scripted client below behaves as the program does: on connecting it
sends ip, sa and nm; its chat lines go out as "name: text"; it takes sa, nm, ms, moves, wn, pn, pe and qt; a
move of its own is allowed only while it has the move, which sa or the opponent's move gives it; it checks
only its own stones for five in a row, and after its own five (move, then wn) or the opponent's wn it hangs
up. It reads the stream as NetLuff does, message by message, so a length that does not match its text fails a
test."""
import re
import socket
import sys
import threading
import time
import unittest

from test_adapters import spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import netluff as A  # noqa: E402
from speed import slow  # noqa: E402

WAIT = 25
LOG = []                # the extension's log, for a failing test's message
DIRS = ((1, 0), (0, 1), (1, 1), (1, -1))


class Mac:
    """NetLuff 1.4, connecting."""

    def __init__(self, port, nick, first=0):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.wire = A.Reader()
        self.nick = nick
        self.got = []           # every message, in order
        self.chats = []         # the chat box's lines
        self.title = None       # the opponent's name, as the window title shows it
        self.mine, self.theirs = set(), set()
        self.my_turn = bool(first)
        self.result = None      # "Victory!" or "Loser!"
        self.line = None        # the winning line a wn drew
        self.closed = False
        self.pages = 0
        self.pings = 0
        self.s.sendall(b"ip3210.0.2.15, running NetLuff 1.4.0" + b"sa%d" % (1 - first) + A.nm(nick))

    def close(self):
        self.s.close()

    def read(self, until, timeout=WAIT):
        end = time.time() + timeout
        while True:
            got = until()
            if got:
                return got
            left = end - time.time()
            if left <= 0 or self.closed:
                raise AssertionError("%s; chats %r; log %r" % ("closed" if self.closed else "timed out",
                                                              self.chats[-8:], LOG[-12:]))
            self.s.settimeout(min(left, 1.0))
            try:
                data = self.s.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                data = b""
            if not data:
                self.closed = True
                continue
            for m in self.wire.feed(data):
                self.handle(m)

    def handle(self, m):
        kind, v = m
        self.got.append(m)
        assert kind != "junk", "NetLuff would show 'Unknown request': %r" % (v,)
        assert self.result is None, "a message after the game: %r" % (m,)
        if kind == "ms":
            self.chats.append(v)
        elif kind == "nm":
            self.title = v
        elif kind == "sa":
            self.my_turn = bool(v)
        elif kind == "move":
            assert v not in self.mine | self.theirs, "%r is taken" % (v,)
            self.theirs.add(v)
            self.my_turn = True
        elif kind == "wn":
            self.line, self.result = v, "Loser!"
            self.s.close()
            self.closed = True
        elif kind == "pn":
            self.pings += 1
            self.s.sendall(b"pr")
        elif kind == "pe":
            self.pages += 1
        elif kind == "qt":
            self.closed = True

    def say(self, text):
        self.s.sendall(A.ms("%s: %s" % (self.nick, text)))

    def emote(self, text):
        self.s.sendall(A.ms("%s %s" % (self.nick, text)))

    def said(self, text, since=0):
        return [c for c in self.chats[since:] if text in c]

    def wait_chat(self, text, since=0):
        return self.read(lambda: self.said(text, since))

    def play(self, x, y):
        """A click on a square: sent only on its own move; its own five goes with wn, then it hangs up."""
        assert self.my_turn, "NetLuff sends no move while it waits"
        assert (x, y) not in self.mine | self.theirs
        self.mine.add((x, y))
        self.my_turn = False
        out = A.move(x, y)
        five = self.five(x, y)
        if five:
            out += A.wn(five)
            self.result = "Victory!"
        self.s.sendall(out)
        if five:
            self.s.close()
            self.closed = True

    def five(self, x, y):
        for dx, dy in DIRS:
            run = [(x, y)]
            for sgn in (1, -1):
                px, py = x + sgn * dx, y + sgn * dy
                while (px, py) in self.mine:
                    run.append((px, py))
                    px, py = px + sgn * dx, py + sgn * dy
            if len(run) >= 5:
                return sorted(run)[:5]
        return None

    def choose(self):
        """A good move by the bot's own rules, on NetLuff's board."""
        from bot.games import fiveinarow as B
        n = A.SIZE
        cells = ["."] * (n * n)
        for (x, y) in self.mine:
            cells[y * n + x] = "0"
        for (x, y) in self.theirs:
            cells[y * n + x] = "1"
        import random
        i = B.choose({"n": n, "rules": "freestyle", "cells": "".join(cells)}, 0, random.Random(1))
        return i % n, i // n


class Stack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("netluff", "bot")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password"),
                     spawn(*adapter_args("adapters.netluff", cls.port, cls.fe, cls.keys, "netluff"),
                           "--poll", "0.2", "--idle-ping", "2", "--after-secs", "3")]
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

    def mac(self, nick, first=0):
        m = Mac(self.port, nick, first)
        self.addCleanup(m.close)
        return m

    def signed_in(self, name, pw, first=0):
        m = self.mac(name, first)
        m.wait_chat("To sign in as %s, type /signin and your password." % name)
        n = len(m.chats)
        m.say("/signin %s" % pw)
        m.wait_chat("Signed in as %s." % name, n)
        return m


class Wire(unittest.TestCase):
    """The formats, against a game captured between two copies of NetLuff 1.4."""

    def test_captured_messages(self):
        r = A.Reader()
        got = r.feed(b"ip3210.0.2.15, running NetLuff 1.4.0sa0")
        self.assertEqual(got, [("ip", "10.0.2.15, running NetLuff 1.4.0"), ("sa", 0)])
        self.assertEqual(r.feed(b"nm003B"), [])
        self.assertEqual(r.feed(b"ob"), [("nm", "Bob")])
        self.assertEqual(r.feed(b"ms018Alice: hello there09"), [("ms", "Alice: hello there")])
        self.assertEqual(r.feed(b"09"), [("move", (9, 9))])
        self.assertEqual(r.feed(b"1309wn09091009110912091309"),
                         [("move", (13, 9)), ("wn", [(9, 9), (10, 9), (11, 9), (12, 9), (13, 9)])])
        self.assertEqual(r.feed(b"pnprpeqt"), [("pn", None), ("pr", None), ("pe", None), ("qt", None)])
        self.assertEqual(r.feed(b"zz junk"), [("junk", b"zz junk")])
        self.assertEqual(r.buf, b"")
        self.assertEqual(A.program_claim("10.0.2.15, running NetLuff 1.4.0"), "NetLuff 1.4.0")
        self.assertEqual(A.program_claim("someone"), "NetLuff")

    def test_builders(self):
        self.assertEqual(A.ip("ParlorNet"), b"ip09ParlorNet")
        self.assertEqual(A.nm("Alice"), b"nm005Alice")
        self.assertEqual(A.ms("Alice: hello there"), b"ms018Alice: hello there")
        self.assertEqual(A.ms("Räksmörgås"), b"ms010R\x8aksm\x9arg\x8cs")      # Mac Roman, bytes counted
        self.assertEqual(A.move(1, 2), b"0102")
        self.assertEqual(A.wn([(3, 5), (4, 5), (5, 5), (6, 5), (7, 5), (8, 5)]), b"wn03050405050506050705")
        self.assertEqual((A.point(0, 0), A.point(19, 19), A.point(1, 2)), ("a1", "t20", "b3"))

    def test_typed(self):
        s = A.Session.__new__(A.Session)
        s.nick, s.name = "Bob", None
        self.assertEqual(s.typed("Bob: /signin x y"), ("/signin x y", False))
        self.assertEqual(s.typed("Bob waves"), ("waves", True))
        self.assertEqual(s.typed("Robert: hi"), ("hi", False))      # a name changed on the way


@slow
class Service(Stack):
    def test_hello_and_sign_in(self):
        m = self.mac("ann")
        m.wait_chat("type /signin and your password")
        self.assertEqual([k for k, _ in m.got[:3]], ["ip", "nm", "sa"])
        self.assertEqual(m.got[0], ("ip", "ParlorNet"))
        self.assertEqual((m.title, m.my_turn), ("ParlorNet", False))     # the Mac's own pick is taken back
        self.assertTrue(all(c.startswith("*** ") for c in m.chats), m.chats)
        n = len(m.chats)
        m.say("hello")
        m.wait_chat("Sign in first", n)
        m.say("/signin wrong-password")             # the name in NetLuff's box: no account
        m.wait_chat("There is no account ann here", n)
        pw = self.signup("ann")
        self.signup("abe")
        n = len(m.chats)
        m.say("/signin abe not-the-password")
        m.read(lambda: len(m.chats) > n)
        self.assertNotIn("Signed in", " ".join(m.chats[n:]))
        n = len(m.chats)
        m.say("/signin %s" % pw)
        m.wait_chat("Signed in as ann.", n)
        n = len(m.chats)
        m.say("/help")
        m.wait_chat("/invite NAME", n)
        m.wait_chat("20 by 20 freestyle", n)
        n = len(m.chats)
        m.say("hello there")
        m.wait_chat("once you have a table", n)
        n = len(m.chats)
        m.say("/quit")
        m.read(lambda: m.closed)
        self.assertEqual(m.got[-1], ("qt", None))

    def test_a_game_against_the_bot(self):
        """The Mac invites the bot, so it is seat 0 and moves first; it plays the bot's own rules until one side
        makes five. Either way the Mac sees the end as it would from another copy: its own wn, or the bot's."""
        pw = self.signup("bea")
        m = self.signed_in("bea", pw)
        m.say("/invite bot")
        m.read(lambda: m.title == "bot")
        m.read(lambda: m.my_turn)
        self.assertTrue(m.said("you move first"))
        self.assertGreaterEqual(m.pages, 1)
        plies = 0
        while m.result is None:
            m.read(lambda: m.my_turn or m.result)
            if m.result:
                break
            plies += 1
            self.assertLess(plies, 200)
            m.play(*m.choose())
        nat = self.native("bea", pw)
        tid = re.search(r"on table (\w+):", m.said("Your game with bot")[0]).group(1)
        v = nat.req("VUE %s" % tid)
        self.assertIn("W -1", v)
        from adapters.games import fiveinarow as F
        view = F.parse_view(v)
        self.assertEqual(set(view["order"][0]), m.mine)
        self.assertEqual(set(view["order"][1]), m.theirs)
        if m.result == "Loser!":
            self.assertTrue(set(m.line) <= m.theirs and len(m.line) == 5)
            self.assertTrue(m.said("bot made"))
            self.assertEqual(view["stones"][view["five"][0]], 1)
        else:
            self.assertEqual(view["stones"][view["five"][0]], 0)
        turns = nat.req("TRN %s" % tid)
        self.assertTrue(any("NetLuff 1.4.0" in l for l in turns), turns)

    def test_the_opponent_sits_down_with_their_program(self):
        """The line that says the opponent sat down says what they play with (the CLIENT event after JOIN),
        and a later move from another program is told too."""
        pw_m, pw_n = self.signup("eli"), self.signup("fen")
        m = self.signed_in("eli", pw_m)
        n = len(m.chats)
        m.say("/open")
        m.wait_chat("is open: anyone can join it", n)
        tid = re.search(r"table (\w+) is open", m.said("is open", n)[0]).group(1)
        nat = self.native("fen", pw_n)
        nat.req("CLI PROG GameMaster 1.0.0")
        nat.req("CLI MACH Macintosh Plus")
        n = len(m.chats)
        self.assertTrue(nat.req("JON %s" % tid)[-1].startswith("OK 1"))
        m.wait_chat("fen sits down with GameMaster 1.0.0 on a Macintosh Plus.", n)
        other = self.native("fen", pw_n)
        other.req("CLI PROG RSCARDS 5.02")
        n = len(m.chats)
        other.req("CHT %s hi" % tid)
        m.wait_chat("fen now plays with RSCARDS 5.02.", n)

    def test_against_a_player_elsewhere(self):
        """A native player: a table made elsewhere is refused, even 20 by 20; the Mac opens one, the native player
        joins it and the Mac moves first; chat both ways; the opponent's five reaches the Mac as their move, a
        line and wn."""
        pw_m, pw_n = self.signup("cy"), self.signup("dee")
        nat = self.native("dee", pw_n)
        tid = nat.req("NEW fiveinarow freestyle size=20")[-1].split()[1]
        nat.req("JON %s 0" % tid)
        m = self.signed_in("cy", pw_m)
        n = len(m.chats)
        m.say("/join %s" % tid)
        m.wait_chat("table %s was made elsewhere." % tid, n)
        m.wait_chat("/invite dee to play them here.", n)
        nat.req("LVE %s" % tid)
        n = len(m.chats)
        m.say("/open")
        m.wait_chat("is open: anyone can join it", n)
        tid = re.search(r"table (\w+) is open", m.said("is open", n)[0]).group(1)
        self.assertTrue(nat.req("JON %s" % tid)[-1].startswith("OK 1"))
        m.read(lambda: m.title == "dee")
        self.assertTrue(m.said("you move first", n))
        m.read(lambda: m.my_turn)
        m.say("good luck")
        n = len(m.chats)
        end = time.time() + WAIT
        while not any("CHT 0 good luck" in l for l in nat.req("SIN %s 0" % tid)):
            self.assertLess(time.time(), end)
            time.sleep(0.2)
        nat.req("CHT %s and to you" % tid)
        m.wait_chat("dee: and to you", n)
        for k in range(5):
            m.read(lambda: m.my_turn)
            m.play(10 + 2 * k, 10 + 2 * (k % 2))
            end = time.time() + WAIT
            while not nat.req("MOV %s %s" % (tid, A.point(2 + k, 3)))[-1].startswith("OK"):
                self.assertLess(time.time(), end)
                time.sleep(0.2)
        m.read(lambda: m.result)
        self.assertEqual(m.result, "Loser!")
        self.assertEqual(m.line, [(2, 3), (3, 3), (4, 3), (5, 3), (6, 3)])
        self.assertTrue(m.said("dee made five in a row"))
        self.assertEqual(m.got[-2][0], "ms")        # the line goes before wn: NetLuff hangs up on wn

    def test_resign_and_going_away(self):
        """The opponent resigns: the result in the chat, and the Mac waits. A Mac that goes away during a game
        resigns it."""
        pw_m, pw_n = self.signup("eve"), self.signup("fay")
        nat = self.native("fay", pw_n)
        m = self.signed_in("eve", pw_m)
        n = len(m.chats)
        m.say("/invite fay")
        m.wait_chat("You invited fay", n)
        tid = next(l.split()[1] for l in nat.req("IVL") if l.startswith("IVT"))
        nat.req("JON %s" % tid)
        m.read(lambda: m.my_turn)
        m.play(9, 9)
        end = time.time() + WAIT
        while "W 1" not in nat.req("VUE %s" % tid):
            self.assertLess(time.time(), end)
            time.sleep(0.2)
        nat.req("RES %s" % tid)
        m.wait_chat("fay resigned: you win.")
        m.wait_chat(A.AGAIN)
        self.assertFalse(m.my_turn)
        n = len(m.chats)
        m.say("/invite bot")
        m.wait_chat("This connection has played its game", n)
        m.read(lambda: m.closed, timeout=10)        # --after-secs: then qt
        self.assertEqual(m.got[-1], ("qt", None))
        # a second game, and the Mac goes away in it
        m = self.signed_in("eve", pw_m, first=1)
        m.say("/invite fay")
        m.wait_chat("You invited fay")
        tid = next(l.split()[1] for l in nat.req("IVL") if l.startswith("IVT"))
        nat.req("JON %s" % tid)
        m.read(lambda: m.title == "fay")
        m.close()
        end = time.time() + WAIT
        while not any(l.startswith("EVT") and "END resign 0" in l for l in nat.req("SIN %s 0" % tid)):
            self.assertLess(time.time(), end)
            time.sleep(0.2)

    def test_invitations_and_the_idle_ping(self):
        pw_m, pw_n = self.signup("gil"), self.signup("hal")
        nat = self.native("hal", pw_n)
        m = self.signed_in("gil", pw_m)
        tid = nat.req("NEW fiveinarow freestyle size=20 vis=private")[-1].split()[1]
        nat.req("JON %s 0" % tid)
        pages = m.pages
        nat.req("INV %s gil 1" % tid)
        m.wait_chat("hal invites you to Five in a Row: /accept hal")
        m.read(lambda: m.pages > pages)
        m.read(lambda: m.pings >= 1, timeout=10)       # --idle-ping 2: NetLuff answers pr by itself
        n = len(m.chats)
        m.say("/accept hal")                            # made elsewhere: the Mac invites back
        m.wait_chat("/invite hal to play them here.", n)
        nat.req("LVE %s" % tid)
        m.say("/invite hal")
        m.wait_chat("You invited hal", n)
        tid = next(l.split()[1] for l in nat.req("IVL") if l.startswith("IVT"))
        nat.req("JON %s" % tid)
        m.read(lambda: m.title == "hal")
        m.emote("waves")
        end = time.time() + WAIT
        while not any("* waves" in l for l in nat.req("SIN %s 0" % tid)):
            self.assertLess(time.time(), end)
            time.sleep(0.2)
        nat.req("CHT %s * nods" % tid)                  # another Mac's /me, shown as NetLuff shows /me
        m.wait_chat("hal nods")


if __name__ == "__main__":
    unittest.main()
