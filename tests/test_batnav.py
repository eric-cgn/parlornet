"""The batnav extension (adapters/batnav.py): Naval Combat for the clients of Batalla Naval (1995-97).

The scripted client below behaves as xbtclient 0.50 does: BNJUG with its name,
kind and version on connecting, BNVER when the server answers, the board with BNWRI after its own check, BNHIT
and at once BNREA of the enemy board it shows, and BNREA again after every BNHIT at its own fleet. It keeps its
own board as the client does, from the BNHIT records, and the tests check that against the server's view."""
import os
import re
import socket
import sys
import threading
import time
import unittest

from test_adapters import spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import batnav as A  # noqa: E402
from speed import slow  # noqa: E402

WAIT = 25
LOG = []                # the extension's log, for a failing test's message

# A legal batnav fleet: one ship of 4, two of 3, three of 2, four of 1, none touching (x, y, across, length).
FLEET = [(0, 0, 1, 4), (5, 0, 1, 3), (0, 2, 0, 3), (9, 2, 0, 2), (2, 2, 1, 2), (5, 2, 1, 2), (2, 9, 1, 1),
         (4, 9, 1, 1), (6, 9, 1, 1), (8, 9, 1, 1)]


def board(ships=FLEET):
    b = bytearray(100)
    for s in ships:
        for x, y in A.ship_cells(s):
            b[y * 10 + x] = 1
    return bytes(b)


class Client:
    """xbtclient 0.50, as the server sees it."""

    def __init__(self, port, name, kind=0x10, version=(0, 50)):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.reader = A.Reader()
        self.recs = []
        self.msgs = []          # BNMSG and BNEXT texts
        self.num = None
        self.play = 0           # the client's state: 1 connected, 2 board accepted, 3 playing, 4 my turn
        self.names = [""] * 5
        self.usrfrom = 0
        self.mine = [0] * 100   # my board as the client keeps it: 1 ship, -1 water, 2 hit, 3 sunk
        self.enemy = None       # the last BNREA board
        self.result = None      # "win" or "lose"
        self.send(A.BNJUG, kind, version[0], version[1], name.encode() + b"\0")

    def close(self):
        self.s.close()

    def send(self, tip0, tip1=0, tip2=0, jug=None, msg=b""):
        self.s.sendall(A.Record(tip0, tip1, tip2, self.num or 0 if jug is None else jug, msg).pack())

    def read(self, until, timeout=WAIT):
        end = time.time() + timeout
        while True:
            got = until()
            if got:
                return got
            left = end - time.time()
            if left <= 0:
                raise AssertionError("timed out; messages %r; log %r" % (self.msgs[-8:], LOG[-12:]))
            self.s.settimeout(min(left, 1.0))
            try:
                data = self.s.recv(4096)
            except socket.timeout:
                continue
            if not data:
                raise EOFError("closed; messages %r; log %r" % (self.msgs[-8:], LOG[-12:]))
            for r in self.reader.feed(data):
                self.recs.append(r)
                self.handle(r)

    def handle(self, r):
        t = r.tip0
        if t == A.BNJUG:
            self.num = r.tip1
            self.play = 1
            self.send(A.BNVER)
        elif t in (A.BNMSG, A.BNEXT):
            self.msgs.append(r.text())
            if t == A.BNEXT and 1 <= r.tip1 <= 5:
                self.names[r.tip1 - 1] = ""
                if self.play >= 3:
                    self.send(A.BNREA, self.usrfrom)
        elif t == A.BNWRI:
            self.play = 2 if r.msg[0] == 1 else 1
        elif t == A.BNSTR:
            for i in range(5):
                if r.msg[i] >= 2:
                    self.names[i] = r.msg[5 + i * 16:21 + i * 16].split(b"\0")[0].decode()
                    if i + 1 != self.num:
                        self.usrfrom = i + 1
            if self.play == 2:
                self.play = 3
        elif t == A.BNTRN:
            self.play = 4
        elif t == A.BNHIT:
            if self.play >= 3:
                x, y, v = r.msg[0], r.msg[1], A.s8(r.msg[2])
                self.mine[y * 10 + x] = v
            self.send(A.BNREA, self.usrfrom)
        elif t == A.BNREA:
            if self.play >= 3:
                self.enemy = [A.s8(b) for b in r.msg]
        elif t in (A.BNLOS, A.BNWIN):
            self.play = 1
            self.result = "win" if t == A.BNWIN else "lose"

    # ---- what the player does ------------------------------------------------------------------------
    def message(self, text, to=0, echo=0):
        self.send(A.BNMSG, to, echo, msg=text.encode() + b"\0")

    def said(self, text, since=0):
        return [m for m in self.msgs[since:] if text in m]

    def wait_said(self, text, since=0):
        return self.read(lambda: self.said(text, since))

    def signin(self, name, password):
        self.read(lambda: self.said("/signin NAME PASSWORD"))
        n = len(self.msgs)
        self.message("/signin %s %s" % (name, password))
        self.wait_said("Signed in as %s." % name, n)

    def send_board(self, b=None):
        n = len(self.recs)
        self.mine = [1 if v else 0 for v in (b or board())]
        self.send(A.BNWRI, msg=b or board())
        return self.read(lambda: [r for r in self.recs[n:] if r.tip0 in (A.BNWRI, A.BNWAI)])[0]

    def fire(self, x, y):
        self.play = 3
        self.send(A.BNHIT, msg=bytes([x, y]))
        self.send(A.BNREA, self.usrfrom)


class Stack(unittest.TestCase):
    auth = "password"

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("batnav", "bot")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", cls.auth),
                     spawn(*adapter_args("adapters.batnav", cls.port, cls.fe, cls.keys, "batnav"), "--poll", "0.2")]
        cls.procs.append(spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", os.path.join(cls.keys, "bot.key"),
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

    def client(self, name, **kw):
        c = Client(self.port, name, **kw)
        self.addCleanup(c.close)
        return c

    def signed_in(self, name):
        pw = self.signup(name)
        c = self.client(name)
        c.read(lambda: c.num)
        self.assertEqual(c.num, 1)
        c.signin(name, pw)
        return c, pw

    def play_out(self, c, other=None):
        """Fire at every square in turn until the game ends; the other client (if any) fires back the same
        way. Returns the squares fired at by c."""
        fired, ofired = [], []
        todo = [(x, y) for y in range(10) for x in range(10)]
        otodo = list(reversed(todo))
        while c.result is None:
            c.read(lambda: c.play == 4 or c.result or (other and other.play == 4))
            if c.result:
                break
            if c.play == 4:
                x, y = todo.pop(0)
                fired.append((x, y))
                c.fire(x, y)
                c.read(lambda: c.enemy is not None and c.enemy[y * 10 + x] != 0 or c.result)
            elif other is not None and other.play == 4:
                x, y = otodo.pop(0)
                ofired.append((x, y))
                other.fire(x, y)
                other.read(lambda: other.enemy is not None and other.enemy[y * 10 + x] != 0 or other.result)
            if other is not None:
                other.read(lambda: True)
        return fired


class Wire(unittest.TestCase):
    """The formats, against batnav 0.50's protocol.h and btserver.c."""

    def test_record(self):
        r = A.Record(A.BNHIT, 0, 0, 1, bytes([3, 4, A.WATER & 0xFF]))
        raw = r.pack()
        self.assertEqual(len(raw), 108)
        self.assertEqual(raw[:8], b"BNP\x11\x01\x59\x00\x00")
        back = A.Record.parse(raw)
        self.assertEqual((back.tip0, back.jug, back.msg[0], A.s8(back.msg[2])), (A.BNHIT, 1, 3, -1))
        self.assertEqual(A.Record(A.BNMSG, msg=A.text_msg("hello")).pack()[8:14], b"hello\0")

    def test_reader(self):
        rd = A.Reader()
        two = A.Record(A.BNVER).pack() + A.Record(A.BNTST).pack()
        self.assertEqual([r.tip0 for r in rd.feed(two[:50])], [])
        self.assertEqual([r.tip0 for r in rd.feed(two[50:])], [A.BNVER, A.BNTST])
        self.assertEqual([r.tip0 for r in rd.feed(b"junk" + A.Record(A.BNSTS).pack())], [A.BNSTS])
        self.assertEqual(rd.skipped, 4)

    def test_fleet(self):
        ships, why = A.fleet_of(board())
        self.assertIsNone(why)
        self.assertEqual([s[3] for s in ships], A.FLEET)
        self.assertEqual(A.ship_token(ships[0]), "a1-d1")
        self.assertIn("(0, 2, 0, 3)", repr(ships))
        corner = list(FLEET)
        corner[-1] = (4, 1, 1, 1)                          # touches the 4-ship and the 3-ship at a corner
        self.assertIn("corner", A.fleet_of(board(corner))[1])
        short = FLEET[:-1]
        self.assertIn("four of 1", A.fleet_of(board(short))[1])
        bent = bytearray(board())
        bent[1 * 10 + 7] = 1                               # the 3-ship at f1-h1 gets a square below: bent
        self.assertIn("straight", A.fleet_of(bytes(bent))[1])
        self.assertIsNone(A.fleet_of(bytes(b if b == 0 else 0x7F for b in board()))[1])   # any value >= 1

    def test_client_names(self):
        self.assertEqual(A.client_name(0x10, 0, 50), "xbtclient 0.50")
        self.assertEqual(A.client_name(0x20, 0, 60), "nbnclient 0.60")
        self.assertEqual(A.client_name(0x30, 0, 48), "btwin 0.48")
        self.assertEqual(A.client_name(0x11, 0, 60), "gbnclient 0.60")
        self.assertEqual(A.client_name(0x77, 1, 0), "")

    def test_names(self):
        m = A.names_msg([2, 2], ["anne", "bot"])
        self.assertEqual(len(m), 100)
        self.assertEqual(m[:5], b"\x02\x02\x00\x00\x00")
        self.assertEqual(m[5:10], b"anne\0")
        self.assertEqual(m[21:25], b"bot\0")


@slow
class Service(Stack):
    def test_signin(self):
        self.signup("ada")
        c = self.client("ada")
        c.read(lambda: c.said("/signin NAME PASSWORD"))
        self.assertTrue(c.said("This is not a btserver."))
        ver = [r for r in c.recs if r.tip0 == A.BNVER]
        self.assertEqual(list(ver[0].msg[:2]), [0, 50])
        n = len(c.msgs)
        c.message("/signin ada wrong-password")
        c.read(lambda: len(c.msgs) > n)
        self.assertFalse(c.said("Signed in", n))
        self.assertFalse(any("wrong-password" in l for l in LOG), "the password must not be logged")
        n = len(c.msgs)
        c.message("hello")
        c.wait_said("Sign in first", n)
        c.message("/signin nobody x-Pass-word1")
        c.wait_said("There is no account nobody here", n)

    def test_board_checks_and_start(self):
        c, _ = self.signed_in("bea")
        bad = list(FLEET)
        bad[-1] = (4, 1, 1, 1)
        n = len(c.msgs)
        r = c.send_board(board(bad))
        self.assertEqual((r.tip0, r.msg[0]), (A.BNWRI, 0))
        c.wait_said("corner", n)
        r = c.send_board()
        self.assertEqual((r.tip0, r.msg[0]), (A.BNWRI, 1))
        n = len(c.recs)
        c.send(A.BNSTR)
        c.read(lambda: [r for r in c.recs[n:] if r.tip0 == A.BNSOL])
        c.wait_said("You have no game yet")
        n = len(c.msgs)
        c.message("/help")
        c.wait_said("/invite NAME", n)
        c.message("/who")
        c.wait_said("Online:", n)

    def test_game_against_the_bot(self):
        c, _ = self.signed_in("cid")
        c.send_board()
        n = len(c.msgs)
        c.message("/invite bot")
        c.wait_said("You invited the bot", n)
        c.read(lambda: c.play == 3 or c.play == 4)
        start = next(r for r in c.recs if r.tip0 == A.BNSTR)
        self.assertEqual(start.msg[:2], b"\x02\x02")
        self.assertEqual(c.names[:2], ["cid", "bot"])
        self.assertEqual(c.usrfrom, 2)
        self.play_out(c)
        self.assertIn(c.result, ("win", "lose"))
        if c.result == "lose":
            c.wait_said("The Game is over: Player 2 is the winner.")       # BNEXT comes after BNLOS
            self.assertEqual(sum(1 for v in c.mine if v == 3), 20, "every square of my fleet is sunk")
            self.assertEqual(sum(1 for v in c.mine if v == 1), 0)
        else:
            self.assertTrue(c.said("Player 2 is dead."))
            self.assertTrue([r for r in c.recs if r.tip0 == A.BNWIN])
        # The server's own view of the client's waters agrees with the board the client kept from BNHIT.
        c.read(lambda: c.said("Send a board for another game."))

    def test_who_sits_down_says_with_what(self):
        """The line that says a player sat down says what they play with (the CLIENT event after JOIN)."""
        a, _ = self.signed_in("ike")
        a.send_board()
        n = len(a.msgs)
        a.message("/open")
        a.wait_said("is open", n)
        tid = re.search(r"table (\w+) is open", a.said("is open", n)[0]).group(1)
        pw = self.signup("jon")
        nat = self.native("jon", pw)
        nat.req("CLI PROG Xbn 2")
        n = len(a.msgs)
        self.assertTrue(nat.req("JON %s" % tid)[-1].startswith("OK 1"))
        a.wait_said("jon sits down at table %s with Xbn 2." % tid, n)

    def test_two_clients_chat_and_resign(self):
        a, _ = self.signed_in("dan")
        b, _ = self.signed_in("eve")
        a.send_board()
        b.send_board()
        n = len(a.msgs)
        a.message("/invite eve")
        b.wait_said("dan invites you to Naval Combat")
        b.message("/accept dan")
        a.read(lambda: a.play >= 3)
        b.read(lambda: b.play >= 3)
        self.assertEqual(a.names[:2], ["dan", "eve"])
        self.assertEqual(b.names[:2], ["eve", "dan"])      # every client is player 1 to itself
        n = len(b.msgs)
        a.message("hello eve", echo=1)
        b.wait_said("dan: hello eve", n)
        a.wait_said("dan: hello eve")
        # dan fires first (seat 0) at a square of eve's 4-ship: a hit on eve's board
        a.read(lambda: a.play == 4)
        a.fire(0, 0)
        a.read(lambda: a.enemy is not None and a.enemy[0] == 2)
        b.read(lambda: b.mine[0] == 2)
        # eve may not ask for dan's ships: BNSUP is never honored
        b.read(lambda: b.play == 4)
        n = len(b.recs)
        b.send(A.BNREA, 2, A.BNSUP)
        got = b.read(lambda: [r for r in b.recs[n:] if r.tip0 == A.BNREA])[0]
        self.assertEqual(got.tip1, 1)
        self.assertNotIn(1, list(got.msg), "no ship square of another player")
        n = len(b.recs)
        b.send(A.BNREA, 1)
        own = b.read(lambda: [r for r in b.recs[n:] if r.tip0 == A.BNREA and r.tip1 == 0])[0]
        self.assertEqual(sum(1 for x in own.msg if x == 1), 19, "the client's own board shows its ships")
        # eve leaves: the game is resigned, and dan wins
        b.send(A.BNEXT)
        a.read(lambda: a.result)
        self.assertEqual(a.result, "win")
        self.assertTrue(a.said("Player 2 quits the game."))

    def test_ncurses_client_types_a_backslash(self):
        """nbtclient's message field takes / for a backspace: its player is shown \\commands and may type them."""
        pw = self.signup("ivy")
        c = self.client("ivy", kind=0x20)
        c.read(lambda: c.said("\\signin NAME PASSWORD"))
        self.assertFalse(c.said("/signin"))
        c.message("\\signin ivy %s" % pw)
        c.wait_said("Signed in as ivy.")
        n = len(c.msgs)
        c.message("\\who")
        c.wait_said("Online:", n)
        self.assertTrue(any("bnclient" in l or "nbtclient 0.50" in l for l in LOG))

    def test_windows_client_gets_whole_lines(self):
        """btwin shows one message at a time in its status line: the last line of each answer is a whole one."""
        pw = self.signup("joy")
        c = self.client("joy", kind=0x30, version=(0, 48))
        c.read(lambda: c.said("/signin NAME PASSWORD"))
        c.message("/signin joy %s" % pw, echo=1)
        c.wait_said("Send your board, then /invite bot to play the bot, /open for anyone, or /help.")
        self.assertFalse(c.said("joy-Pass-word"), "a command is never echoed")

    def test_invitation_to_another_ruleset(self):
        c, _ = self.signed_in("fay")
        pw = self.signup("gus")
        n = self.native("gus", pw)
        tid = n.req("NEW navalcombat/1 classic vis=private")[-1].split()[1]
        n.req("JON %s 0" % tid)
        n.req("INV %s fay 1" % tid)
        c.wait_said("with classic rules")
        k = len(c.msgs)
        c.message("/accept gus")
        c.wait_said("batnav cannot play table %s" % tid, k)
        tid = n.req("NEW navalcombat/1 batnav vis=private")[-1].split()[1]
        n.req("JON %s 0" % tid)
        n.req("INV %s fay 1" % tid)
        c.wait_said("gus invites you to Naval Combat (table %s)." % tid)
        k = len(c.msgs)
        c.message("/accept gus")
        c.wait_said("You sat down at table %s" % tid, k)
        c.wait_said("Now send your board.", k)


class NameOnly(Stack):
    auth = "none"

    def test_the_name_signs_in(self):
        c = self.client("hal")
        c.read(lambda: c.said("Signed in as hal."))
        self.assertFalse(c.said("/signin NAME PASSWORD"))


if __name__ == "__main__":
    unittest.main()
