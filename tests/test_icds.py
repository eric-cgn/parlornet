"""The ICDS extension (adapters/icds.py): Checkers for xcheckers 2.2.3 and telnet programs.

XCheckers below reads the server the way xcheckers' C code does: the stream
is cut into lines at LF with every CR dropped (child.c, packbuf); a line starting "Game" makes it send
"set style 5" and "refresh", one of five refusals starting "icds% " makes it send "refresh", and a line
starting "<5>" is a board read by offsets (read_5.c, readPos5). Its squares are numbered as initBoard and
swapBoard number them (board.c), and a move is a drag from one cell to another, sent as "%d-%d" only when a
piece is picked up and dropped on an empty dark square (events.c). So a numbering or orientation that differs
from the plugin's fails here: the drag would pick up the wrong piece, or none."""
import json
import random
import re
import socket
import sys
import time
import unittest

import procs
from procs import owned, spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import icds as A  # noqa: E402

import importlib.util  # noqa: E402
from speed import slow  # noqa: E402

_spec = importlib.util.spec_from_file_location("checkers_plugin_for_icds", ROOT + "/plugins/checkers/plugin.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

WAIT = 20
LOG = []
NORTH, SOUTH = 0, 1
O, N_, b, w, B, W = range(6)        # board.h's node enum: o light, n dark and empty, then the pieces
CELL = {" ": O, "-": N_, "b": b, "w": w, "B": B, "W": W}
TO32 = {N_: ".", b: "x", B: "X", w: "o", W: "O"}
REFUSALS = ("icds% It is not your move.", "icds% Game clock is paused,", "icds% Illegal move.",
            "icds% Ambigous move.", "icds% It isn't your turn,")
RESULT = re.compile(r"\} (1-0|0-1|1/2-1/2|\*)$")
SIMPLECH = ("   --------------- ", "   ~~~~~~~~~~~~~~~ ", ".  --------------- ", ".  ~~~~~~~~~~~~~~~ ",
            "*  --------------- ", "*  ~~~~~~~~~~~~~~~ ", "-  --------------- ", "-  ~~~~~~~~~~~~~~~ ")


class Grid:
    """xcheckers' board array for English checkers: color and number per screen cell, origin top left."""

    def __init__(self):
        self.direction = NORTH          # resInit
        self.init()

    def init(self):
        """initBoard(ENGLISH): light squares where (x + y) is even; dark ones numbered right to left as the
        loop meets them, from the top: 32 down for NORTH, 1 up for SOUTH."""
        self.colour = [[O] * 8 for _ in range(8)]
        self.number = [[0] * 8 for _ in range(8)]
        num = 0
        for y in range(8):
            for x in range(8):
                if (x + y) % 2 == 0:
                    self.colour[x][y], self.number[x][y] = O, 0
                else:
                    self.colour[x][y] = N_
                    self.number[x][y] = 32 - num if self.direction == NORTH else num + 1
                    num += 1

    def swap(self, orientation):
        """swapBoard: the board turned round, colors and numbers with it."""
        if orientation == self.direction:
            return
        self.direction = orientation
        c, n = [r[:] for r in self.colour], [r[:] for r in self.number]
        for y in range(8):
            for x in range(8):
                self.colour[x][y], self.number[x][y] = c[7 - x][7 - y], n[7 - x][7 - y]

    def read5(self, line):
        """readPos5 for an English board: orientation from column 7, cells from column 43."""
        self.swap(NORTH if line[7] == "B" else SOUTH)
        k = 43
        order = ([(x, y) for y in range(7, -1, -1) for x in range(8)] if self.direction == NORTH else
                 [(x, y) for y in range(8) for x in range(7, -1, -1)])
        for x, y in order:
            if line[k] in CELL:
                self.colour[x][y] = CELL[line[k]]
            k += 1

    def where(self, s):
        return next((x, y) for y in range(8) for x in range(8) if self.number[x][y] == s)

    def cells32(self):
        out = ["?"] * 32
        for y in range(8):
            for x in range(8):
                if self.number[x][y]:
                    out[self.number[x][y] - 1] = TO32[self.colour[x][y]]
        return "".join(out)


class XCheckers:
    """xcheckers -icds: telnet's stream in, the player's typing and drags out."""

    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.buf = b""
        self.lines = []         # every line, as packbuf cut it
        self.pos = 0            # the test's read position in lines
        self.grid = Grid()
        self.board = None       # the last style 5 line
        self.refreshes = 0
        self.styled = 0
        self.part = b""         # the line being received (a prompt waits there)

    def type(self, text):
        """What the player types in the terminal: passed through to telnet (child.c)."""
        self.s.sendall(text.encode() + b"\n")

    def pump(self, timeout=0.3):
        self.s.settimeout(timeout)
        try:
            d = self.s.recv(65536)
        except socket.timeout:
            return True
        if not d:
            return False
        self.buf += d
        while b"\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n", 1)
            self.handle(raw.replace(b"\r", b"").decode("ascii"))
        self.part = self.buf
        return True

    def handle(self, line):
        self.lines.append(line)
        assert not line.startswith(SIMPLECH), "readPosS would take this for a simplech board: %r" % line
        if line.startswith("Game"):
            self.styled += 1
            self.s.sendall(b"set style 5\n")
            self.s.sendall(b"refresh\n")
        elif line.startswith(REFUSALS):
            self.refreshes += 1
            self.s.sendall(b"refresh\n")
        elif line.startswith("<5>"):
            assert len(line) >= 114 and line[6] not in "pimr", line
            # readPos5 writes each clock as "%d : %02d" into clockline's 8 bytes: ten minutes overruns them
            assert all(-599 <= int(line[k:k + 3]) <= 599 for k in (107, 111)), line
            self.board = line
            self.grid.read5(line)

    def until(self, pred, timeout=WAIT, what=""):
        """The next line, from the read position on, that pred takes; the position moves past it."""
        end = time.time() + timeout
        while True:
            for i in range(self.pos, len(self.lines)):
                if pred(self.lines[i]):
                    self.pos = i + 1
                    return self.lines[i]
            if time.time() > end:
                raise AssertionError("waited for %s; last lines %r; log %r" % (what or pred, self.lines[-12:],
                                                                               LOG[-10:]))
            if not self.pump(min(0.3, max(0.05, end - time.time()))):
                raise AssertionError("closed; last lines %r" % self.lines[-12:])

    def text(self, words, timeout=WAIT):
        return self.until(lambda l: words in l, timeout, repr(words))

    def prompt(self, word, timeout=WAIT):
        end = time.time() + timeout
        while not self.part.endswith(word.encode()):
            assert time.time() < end, "no %r prompt: %r" % (word, self.part[-200:])
            assert self.pump(0.2), "closed"

    def quiet(self, secs=0.8):
        end = time.time() + secs
        while time.time() < end:
            if not self.pump(0.1):
                break

    def login(self, name, password):
        self.prompt("login: ")
        self.type(name)
        self.prompt("password: ")
        self.type(password)
        self.text("Starting a ParlorNet Checkers session as %s" % name)

    def fields(self, l=None):
        l = l or self.board
        return {"game": int(l[3:6]), "pov": l[7], "mover": l[8], "black": l[9:26].strip(),
                "white": l[26:43].strip(), "clocks": (int(l[107:110]), int(l[111:114]))}

    def next_board(self, pred=lambda f: True, timeout=WAIT):
        """The next board line from the read position on that pred takes, as fields."""
        return self.fields(self.until(lambda l: l.startswith("<5>") and pred(self.fields(l)), timeout,
                                      "a board"))

    def drag(self, a, c):
        """Button 1 down on square a's cell, up on square c's (events.c): sent only from a piece to an empty
        dark square."""
        x0, y0 = self.grid.where(a)
        x1, y1 = self.grid.where(c)
        g = self.grid
        assert g.colour[x0][y0] > N_, "no piece to pick up on %d" % a
        assert g.colour[x1][y1] == N_ and g.number[x1][y1], "%d is not an empty dark square" % c
        self.s.sendall(b"%d-%d\n" % (g.number[x0][y0], g.number[x1][y1]))

    def close(self):
        try:
            self.s.sendall(b"quit\n")
        except OSError:
            pass
        self.s.close()


def whole_turns(cells, seat):
    """Every whole turn for seat on cells (by the plugin's rules): [path of squares]."""
    st = {"cells": cells, "turn": seat, "phase": "move", "steps": [], "quiet": 0, "limit": 0, "offer": None,
          "no_offer": -1, "over": False, "result": None}
    out = []

    def rec(s, path):
        lg = P.legal(s)
        if s["steps"] and not lg:
            out.append(path)
            return
        for (a, c), m in lg.items():
            s2 = json.loads(json.dumps(s))
            P.play_step(s2, seat, a, c, [])
            rec(s2, (path or [a]) + [c])
    rec(st, [])
    return out


class Unit(unittest.TestCase):
    def test_style5_read_as_xcheckers_reads_it(self):
        """Every square lands where xcheckers puts its number, from either side."""
        rnd = random.Random(5)
        for _ in range(30):
            cells = "".join(rnd.choice(".xXoO") for _ in range(32))
            for pov in "BW":
                line = A.style5(7, pov, "B", "amy", "bot", cells, (540, 30))
                g = Grid()
                g.read5(line)
                self.assertEqual(g.cells32(), cells)
                self.assertEqual(line[3:6], "007")
                self.assertEqual((line[9:26].strip(), line[26:43].strip()), ("amy", "bot"))
                self.assertEqual((int(line[107:110]), int(line[111:114])), (540, 30))
        g = Grid()
        g.read5(A.style5(1, "B", "B", "a", "b", P.new_state()["cells"], (0, 0)))
        self.assertEqual([g.number[x][7] for x in (0, 2, 4, 6)], [4, 3, 2, 1])     # Black at the bottom
        self.assertTrue(all(g.colour[x][y] == b for y in (5, 6, 7) for x in range(8) if g.number[x][y]))
        g.read5(A.style5(1, "W", "B", "a", "b", P.new_state()["cells"], (0, 0)))
        self.assertEqual([g.number[x][7] for x in (0, 2, 4, 6)], [29, 30, 31, 32])  # White at the bottom
        self.assertTrue(all(g.colour[x][y] == w for y in (5, 6, 7) for x in range(8) if g.number[x][y]))

    def test_clocks_and_numbers_fit(self):
        line = A.style5(1234, "B", "W", "a" * 12, "b" * 12, "." * 32, (86400, -500))
        self.assertEqual(len(line), 114)
        # under ten minutes: xcheckers writes "%d : %02d" into 8 bytes (status.h), and "10 : 00" overruns them
        self.assertEqual((int(line[107:110]), int(line[111:114])), (599, 0))

    def test_safe_lines(self):
        self.assertEqual(A.safe("Game1 tells you: hi"), " Game1 tells you: hi")
        self.assertEqual(A.safe(A.Board("Game 3 (a vs. b)")), "Game 3 (a vs. b)")

    def test_jump_lines(self):
        """A Black man on 2, White men on 6, 7, 14 and 15: two lines of jumps join 2 and 18."""
        cells = ["."] * 32
        cells[1] = "x"
        for s in (6, 7, 14, 15):
            cells[s - 1] = "o"
        cells = "".join(cells)
        self.assertEqual(sorted(A.jump_lines(cells, 2, 18, 0)), [[2, 9, 18], [2, 11, 18]])
        # by shape alone a man may seem to jump back (2x11x18x9): CHK, that is the plugin, refuses that line
        self.assertEqual(A.jump_lines(cells, 2, 9, 0), [[2, 9], [2, 11, 18, 9]])
        self.assertEqual(A.jump_lines(cells, 6, 18, 0), [])         # not Black's piece

    def test_ambiguous_and_single(self):
        import asyncio
        cells = ["."] * 32
        cells[1] = "x"
        for s in (6, 7, 14, 15):
            cells[s - 1] = "o"
        v = {"cells": "".join(cells), "moves": ["2x9", "2x11"]}
        s = A.Session.__new__(A.Session)

        async def req(text):
            return [], "OK ok"
        s.core_req = req
        tr = A.Track("1", 0)
        steps, refusal = asyncio.run(s.resolve(tr, "2-18", v))
        self.assertTrue(steps is None and refusal.startswith("Ambigous move."))
        self.assertEqual(asyncio.run(s.resolve(tr, "2-9", v)), ("2x9", None))
        v["cells"] = v["cells"][:14] + "." + v["cells"][15:]           # no White man on 15: one line
        self.assertEqual(asyncio.run(s.resolve(tr, "2-18", v)), ("2x9x18", None))


@slow
class Stack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("icds", "bot")
        del LOG[:]
        cls.procs = owned(cls)
        cls.procs.append(spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password"))
        cls.procs.append(spawn(*adapter_args("adapters.icds", cls.port, cls.fe, cls.keys, "icds"), "--poll", "0.2",
                               out=LOG, err=LOG))
        cls.procs.append(spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", cls.keys + "/bot.key",
                               "--think", "0,0", "--pace", "0", "--poll", "0.3"))

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)

    def signup(self, name):
        from test_fibs import Stack as FibsStack
        return FibsStack.password_for(self, name)

    def native(self, name, password):
        from test_fibs import Native
        n = Native(self.core, name, password)
        self.addCleanup(n.close)
        return n

    def client(self, name=None, password=None):
        c = XCheckers(self.port)
        self.addCleanup(c.close)
        if name:
            c.login(name, password)
        return c

    def test_guest_signup_then_login(self):
        c = self.client()
        c.prompt("login: ")
        c.type("guest")
        c.prompt("(letters, digits, - and _, up to 12): ")
        c.type("gina")
        c.prompt(": ")
        c.type("Gina-Pass-1")           # choose a password
        c.prompt(": ")
        c.type("Gina-Pass-1")           # and again
        c.text("Starting a ParlorNet Checkers session as gina")
        c.close()
        d = self.client("gina", "Gina-Pass-1")
        d.close()
        e = self.client()
        e.prompt("login: ")
        e.type("nobody")
        e.prompt("password: ")
        e.type("x")
        e.text("There is no account named nobody here. New here? Log in as guest.")
        e.prompt("login: ")
        e.type("gina")
        e.prompt("password: ")
        e.type("wrong-Pass-1")
        e.text("Wrong password")
        e.prompt("login: ")
        e.type("two words")
        e.text("Log in with your name, or as guest.")
        e.quiet(1)
        self.assertFalse(e.pump(0.5))           # three refused logins close the line

    def test_game_against_the_bot(self):
        """A whole game as xcheckers plays it: style 1 first, which xcheckers turns into style 5 itself; drags
        only; jumps of several captures dragged in one go or a jump at a time."""
        pw = self.signup("xavier")
        c = self.client("xavier", pw)
        c.type("match bot black")
        c.text("Creating unrated checkers match")
        c.until(lambda l: l.startswith("Game"), what="a style 1 board")
        f = c.next_board(lambda f: f["mover"] == "B")
        self.assertEqual(c.styled, 1)
        self.assertEqual((f["pov"], f["black"], f["white"]), ("B", "xavier", "bot"))
        rnd = random.Random(11)
        whole = hops = played = 0
        for _ in range(150):
            if any(l.startswith("{Game") and RESULT.search(l) for l in c.lines):
                break
            f = c.fields()
            if f["mover"] != "B":
                c.pump(0.3)
                continue
            turns = whole_turns(c.grid.cells32(), 0)
            self.assertTrue(turns, "no move for Black on %s" % c.grid.cells32())
            path = rnd.choice(turns)
            played += 1
            before = c.pos = len(c.lines)
            if len(path) > 2 and rnd.random() < 0.5:
                hops += 1
                for a, z in zip(path, path[1:]):
                    c.drag(a, z)
                    if z != path[-1]:
                        c.until(lambda l: "Keep jumping" in l, what="keep jumping")
            else:
                whole += len(path) > 2
                c.drag(path[0], path[-1])
            c.until(lambda l: (l.startswith("{Game") and RESULT.search(l)) or (l.startswith("<5>") and l[8] == "W"),
                    what="the board after %r" % path)
            self.assertFalse(any(l.startswith(REFUSALS) for l in c.lines[before:]), c.lines[before:])
        c.pos = 0
        end = c.until(lambda l: l.startswith("{Game") and RESULT.search(l), what="the end")
        self.assertRegex(end, r"^\{Game \d+ \(xavier vs\. bot\) .*\} (1-0|0-1|1/2-1/2)$")
        self.assertEqual(c.refreshes, 0)
        self.assertGreater(played, 8)

    def test_refusals_bring_a_refresh(self):
        pw = self.signup("rita")
        c = self.client("rita", pw)
        c.type("match bot white")
        c.next_board(lambda f: f["mover"] == "W")       # the bot (Black) has moved
        self.assertEqual(c.fields()["pov"], "W")
        c.type("1-5")                                   # not a move for White: it is illegal
        c.until(lambda l: l.startswith("icds% Illegal move."), what="the refusal")
        c.next_board()
        self.assertEqual(c.refreshes, 1)
        c.type("draw")
        c.text("Game drawn by agreement")               # the bot takes a draw when it is not ahead
        c.type("24-20")
        c.until(lambda l: l.startswith("icds% You are not playing a game."), what="no game")

    def test_not_your_move_and_a_double_jump(self):
        """Against a player on the native protocol: a move out of turn, then a double jump dragged end to end
        (7 to 23 over 10 and 18)."""
        pw, pw2 = self.signup("bea"), self.signup("wes")
        c = self.client("bea", pw)
        n = self.native("wes", pw2)
        c.type("match wes black")
        c.text("Issuing: bea (Black) wes")
        invs = n.req("IVL")
        tid = next(l.split()[1] for l in invs if l.startswith("IVT"))
        self.assertTrue(n.req("JON %s" % tid)[-1].startswith("OK"))
        c.next_board(lambda f: f["mover"] == "B")
        for mine, theirs in (("9-13", "21-17"), ("5-9", "23-18"), ("10-14", "17x10")):
            a, z = mine.split("-")
            c.drag(int(a), int(z))
            c.next_board(lambda f: f["mover"] == "W")
            if mine == "9-13":
                c.type("13-17")
                c.until(lambda l: l.startswith("icds% It is not your move."), what="not your move")
                c.next_board()
            self.assertTrue(n.req("MOV %s %s" % (tid, theirs))[-1].startswith("OK done"))
            self.assertTrue(n.req("END %s" % tid)[-1] == "OK")
            c.next_board(lambda f: f["mover"] == "B")
        c.drag(7, 23)
        c.next_board(lambda f: f["mover"] == "W")
        self.assertEqual(c.grid.cells32()[22], "x")
        self.assertEqual(c.grid.cells32()[9], ".")
        self.assertEqual(c.grid.cells32()[17], ".")
        c.type("say good one")
        c.text("(told game")
        view = n.req("SIN %s 0" % tid)
        self.assertTrue(any("CHT 0 good one" in l for l in view), view)

    def test_challenge_from_elsewhere_and_chat(self):
        pw, pw2, pw3 = self.signup("cora"), self.signup("dina"), self.signup("ezra")
        c = self.client("cora", pw)
        d = self.client("dina", pw2)
        d.type("tell cora hello there")
        c.text("dina tells you: hello there")
        n = self.native("ezra", pw3)
        tid = n.req("NEW checkers american")[-1].split()[1]
        n.req("JON %s 0" % tid)
        n.req("INV %s cora 1" % tid)
        c.text("Challenge: ezra (Black) cora, unrated checkers (american), table %s." % tid)
        c.type("accept ezra")
        c.text("Creating unrated checkers match")
        f = c.next_board()
        self.assertEqual((f["pov"], f["black"], f["white"]), ("W", "ezra", "cora"))
        d.type("games")
        d.text("ezra vs. cora")
        d.type("who")
        d.text("players displayed.")
        d.type("/list")
        d.text("You have no Checkers games in progress.")
        d.type("observe %s" % tid)
        d.text("You are now observing game")
        d.until(lambda l: l.startswith("Game"), what="the observer's style 1 board")
        d.next_board()
        self.assertEqual(d.fields()["pov"], "B")
        n.req("MOV %s 11-15" % tid)
        n.req("END %s" % tid)
        c.text("ezra moves 11-15.")
        d.text("ezra moves 11-15.")
        c.type("resign")
        c.text("cora resigns} 1-0")

    def test_the_match_says_with_what_the_opponent_plays(self):
        """The match's start says what the opponent plays with (the CLIENT event after their JOIN), and a move
        from another program is told too."""
        pw, pw2 = self.signup("flo"), self.signup("gil")
        c = self.client("flo", pw)
        n = self.native("gil", pw2)
        n.req("CLI PROG Xcheckers 2.1")
        n.req("CLI MACH SPARCstation 2")
        tid = n.req("NEW checkers american")[-1].split()[1]
        n.req("JON %s 0" % tid)
        n.req("INV %s flo 1" % tid)
        c.text("Challenge: gil (Black) flo")
        c.type("accept gil")
        c.text("Creating unrated checkers match")
        c.text("gil plays with Xcheckers 2.1 on a SPARCstation 2.")
        c.next_board()
        m = self.native("gil", pw2)
        m.req("CLI PROG Telnet 1")
        m.req("MOV %s 11-15" % tid)
        m.req("END %s" % tid)
        c.text("gil now plays with Telnet 1.")
        c.text("gil moves 11-15.")


if __name__ == "__main__":
    unittest.main()
