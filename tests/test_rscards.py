"""The RSCARDS extension (adapters/rscards.py, adapters/rscgames.py): ParlorNet's games for GEnie's RSCARDS front
ends, driven by the scripted front end in tests/rscfront.py."""
import os
import socket
import sys
import time
import unittest

import procs
from procs import owned, spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args
from rscfront import Front

sys.path.insert(0, ROOT)
from adapters import rscards as A  # noqa: E402
from adapters import rscwire as R  # noqa: E402
from adapters import rscgames as G  # noqa: E402
from speed import slow  # noqa: E402

WAIT = 25
LOG = []
SHOTS = os.environ.get("RSC_SHOTS")         # a folder: the tests save the screens they reach there as PNG
MACHINE = int(os.environ.get("RSC_MACHINE") or 7)  # the front end the games are played on: 7 Mac color, 8 Mac
                                                   # mono, 4 IBM PC (its 40 click areas, the same packets)


class Wire(unittest.TestCase):
    """The packets, against samples read from the front ends' code and the IBM front end's answers."""

    def test_samples(self):
        self.assertEqual(R.reset(), b'ZZ"T')
        self.assertEqual(R.text(560, 170, "Roll", 7, 0, 25, 64), b'T9(P"J! 70Roll~0P')
        self.assertEqual(R.field(160, 96, 12, "guest", 7, 0), b'I"@!@,X70guest~2=')
        self.assertEqual(R.mouse(True), b"M(!U")
        self.assertEqual(R.entry_line(True), b"D !D")
        self.assertEqual(R.die(440, 72, 1, 5), b"X&X!(!%%E")
        self.assertEqual(R.leave(), b'QQ"B')
        got = R.parse(R.fill(1, 2, 3, 4, 5) + b"\r\n junk " + R.hot(80, 16, 40, 64, "M", 2))
        self.assertEqual(got, [("A", [(1, 2, 3, 4), 5], True), ("H", [(2080, 16, 40, 64), "M"], True)])
        bad = bytearray(R.fill(1, 2, 3, 4, 5))
        bad[3] += 1
        self.assertFalse(R.parse(bytes(bad))[0][2])
        m = R.fill(0, 0, 40, 20, 3, raw=True) + R.disc(20, 10, 15, 7, 1, raw=True)
        self.assertEqual(R.parse_macro(m), [("A", [(0, 0, 40, 20), 3]), ("E", [20, 10, 15, 7, 1])])
        self.assertEqual(R.parse(R.macro(1, m))[0][1][2], m)
        with self.assertRaises(R.WireError):
            R.w(4096)

    def test_versions(self):
        self.assertEqual(A.version_text("05", "02", "0"), "5.02")
        self.assertEqual(A.version_text("05", "00", "0"), "5.0")
        self.assertEqual(A.version_text("04", "01", "0"), "4.01")

    def test_link_lines(self):
        link = A.Link(None, None)
        link.graphics = True
        link.feed(b"RSCARDS$v05.020\rQQQ\r    hello there\r\x06\r    \rEXIT\r")
        got = []
        while not link.events.empty():
            got.append(link.events.get_nowait())
        self.assertEqual(got, [("beacon", 4, "5.02"), ("click", "Q"), ("line", "hello there"), ("line", ""),
                               ("exit",)])

    def test_one_packet_each(self):
        """Every packet is queued on its own, so each acknowledgment counts one (a window that counted an item
        of several packets as one overran the IBM front end's buffer)."""
        link = A.Link(None, None)
        d = A.Display(link)
        sc = A.Scene("x", [R.fill(0, 0, 640, 160, 0)])
        sc.add("a", R.fill(0, 0, 8, 8, 1) + R.disc(4, 4, 2, 2, 0), R.fill(0, 0, 8, 8, 0))
        sc.add("b", [R.text(0, 0, "hi"), R.fill(8, 0, 8, 8, 1) + R.fill(16, 0, 8, 8, 1)])
        d.show(sc)
        self.assertTrue(all(len(R.split(p)) == 1 for p in link.fg), list(link.fg))
        self.assertIn(R.fill(8, 0, 16, 8, 1), link.fg)          # fills side by side go as one
        link.fg.clear()
        d.show(A.Scene("x", [R.fill(0, 0, 640, 160, 0)]))
        self.assertIn(R.fill(0, 0, 8, 8, 0), link.fg)

    def test_telnet_refused(self):
        class W:
            data = b""

            def write(self, d):
                W.data += d
        link = A.Link(None, W())
        link.feed(bytes([255, 251, 24, 255, 253, 1, 255, 251, 24]) + b"FLC 123 9\r" + bytes([255, 250, 24, 1, 255, 240]))
        self.assertEqual(W.data, bytes([255, 254, 24, 255, 252, 1]))        # once each
        events = []
        while not link.events.empty():
            events.append(link.events.get_nowait())
        self.assertEqual([e for e in events if e[0] != "key"], [("flc", 123)])     # terminal mode: keys too
        self.assertEqual("".join(e[1] for e in events if e[0] == "key"), "FLC1239")
        self.assertTrue(link.telnet)


class Clients(unittest.TestCase):
    """What the other players play with (the core's CLIENT events and lines)."""

    def screen(self):
        scr = G.Reversi.__new__(G.Reversi)
        scr.me, scr.names, scr.seq, scr.clients, scr.bot_declined, scr.over = 0, ["ann", "bob"], 0, {}, set(), ""
        return scr

    def test_join_and_change(self):
        lines = []
        self.screen()._events(["EVT 1 1 JOIN 1 bob",
                               "EVT 1 2 CLIENT 1 with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus",
                               "EVT 1 3 CLIENT 0 with RSCARDS 5.02 on an IBM PC",       # my own: not told
                               "EVT 1 4 CLIENT 1",                                      # hidden: nothing
                               "EVT 1 5 CLIENT 1 with RSCARDS 5.0 on a Macintosh"], False, lines)
        self.assertEqual(lines, ["bob sat down with GameMaster 1.0.0 + ParlorNet 0.5.",     # cut to fit
                                 "bob now plays with RSCARDS 5.0 on a Macintosh."])
        self.assertTrue(all(len(l) <= A.TEXT_MAX for l in lines))

    def test_fits(self):
        lines = []
        self.screen()._events(["EVT 1 1 JOIN 1 bob", "EVT 1 2 CLIENT 1 with a program whose name runs on and on "
                               "and on, + ParlorNet 0.5 on a Macintosh Plus with more"], False, lines)
        self.assertEqual(len(lines), 1)
        self.assertLessEqual(len(lines[0]), A.TEXT_MAX)
        self.assertTrue(lines[0].startswith("bob sat down with a program"), lines)

    def test_seat_line(self):
        scr = self.screen()
        scr.clients = {1: "with GameMaster 1.0.0 on a Macintosh Plus"}
        self.assertEqual(scr.seat_text(1, "White", 38), "White: bob, GameMaster 1.0.0 on a")
        self.assertEqual(scr.seat_text(0, "Black", 38), "Black: ann (you)")


class Stack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("rscards", "bot")
        del LOG[:]
        cls.procs = owned(cls)
        cls.procs.append(spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password"))
        cls.procs.append(spawn(*adapter_args("adapters.rscards", cls.port, cls.fe, cls.keys, "rscards"),
                               "--poll", "0.3", "--guide", "3", "--guide-again", "6",
                               "--trigger-again", "4", err=LOG))
        cls.procs.append(spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", cls.keys + "/bot.key",
                               "--think", "0,0", "--pace", "0", "--poll", "0.3"))

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)

    def signup(self, name):
        from test_fibs import Stack as FibsStack
        return FibsStack.password_for(self, name)

    def front(self, **kw):
        f = Front(self.port, **kw)
        self.addCleanup(f.close)
        return f

    def shot(self, f, name):
        if SHOTS:
            f.render(os.path.join(SHOTS, name + ".png"))

    def signed_in(self, name, pw, **kw):
        f = self.front(**kw)
        f.until(lambda: f.graphics, what="graphics mode")
        f.wait_field()
        self.assertTrue(f.shows("Your ParlorNet name"))
        f.type_line(name)
        f.wait_field()
        self.assertTrue(f.shows("Password"), f.all_text())
        f.type_line(pw)
        f.until(lambda: f.shows("Start a game"), what="the lobby")
        return f


@slow
class Service(Stack):
    def test_sign_in(self):
        pw = self.signup("ann")
        self.signup("abe")
        f = self.front()
        f.until(lambda: f.graphics, what="graphics mode")
        self.assertIn(b"ParlorNet", f.terminal)
        f.wait_field()
        f.type_line("abe")
        f.wait_field()
        f.type_line("wrong")
        f.wait_field()
        f.until(lambda: f.shows("Your ParlorNet name"), what="the name again")
        f.type_line("ann")
        f.wait_field()
        f.type_line(pw)
        f.until(lambda: f.shows("Signed in as ann"), what="the lobby")
        self.assertEqual(f.bad, 0)
        self.shot(f, "lobby")

    def test_lobby_fits_the_front_end(self):
        pw = self.signup("lou")
        f = self.signed_in("lou", pw)
        f.until(lambda: f.shows("Online:"), what="the lobby's lists")
        self.quiet(f)
        self.assertLessEqual(len(f.areas), 30)
        for game in A_GAMES:
            self.assertTrue(f.shows(G.GAME_NAMES[game]), game)
        # typed commands work on the entry line
        n = len(f.msgs)
        f.type_line("/who")
        f.until(lambda: any("Online" in m for m in f.msgs[n:]), what="/who")
        f.type_line("/help")
        f.until(lambda: any("/invite" in m for m in f.msgs[n:]), what="/help")
        self.assertTrue(all(len(m) <= 62 for m in f.msgs), f.msgs)

    def test_watch(self):
        """A public game between others is listed under Other tables with Watch: a click shows its board to a
        watcher (the core counts the watcher), and Lobby leaves it."""
        pw = self.signup("wpl")
        n = self.native("wpl", pw)
        final = n.req("NEW checkers/1 american turn=1h")[-1]
        tid = final.split()[1]
        got = n.req("JON %s" % tid)
        self.assertTrue(got[-1].startswith("OK"), (final, got))
        self.assertTrue(n.req("INV %s bot" % tid)[-1].startswith("OK"))
        end = time.time() + WAIT
        while time.time() < end and "playing" not in " ".join(l for l in n.req("LST") if l.split()[1:2] == [tid]):
            time.sleep(0.2)
        f = self.signed_in("wat", self.signup("wat"))
        f.until(lambda: any((f.text_at(41, r) or "").startswith(tid + " ") for r in G.OPEN_ROWS)
                and f.shows("Watch") and f.area_at(68 * 8 + 4, G.OPEN_ROWS[0] * 8 + 2), what="the game to watch")
        row = next(r for r in G.OPEN_ROWS if (f.text_at(41, r) or "").startswith(tid + " "))
        self.quiet(f)
        k = len(f.msgs)
        f.click(68 * 8 + 4, row * 8 + 2)
        f.until(lambda: f.shows("Checkers, table %s" % tid), what="the watched table")
        f.until(lambda: any("watching" in m for m in f.msgs[k:]), what="the watching line")
        self.quiet(f)
        self.assertLessEqual(len(f.areas), 30)
        self.assertNotIn("+", A.CODES)          # a click would send +++, a modem's escape
        self.press(f, "Lobby")
        f.until(lambda: f.shows("Start a game"), what="the lobby again")
        self.assertEqual(f.bad, 0)

    # ---- terminal mode: the front ends' ways in, and text terminals -------------------------------------------
    def raw_term(self):
        """A plain terminal on the port: no telnet, no RSCARDS program."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.settimeout(0.2)
        self.addCleanup(s.close)
        return s

    def read_until(self, s, text, got, secs=12):
        end = time.time() + secs
        while text.encode() not in got[0]:
            if time.time() > end:
                raise AssertionError("%r not seen in %r" % (text, got[0][-400:]))
            try:
                d = s.recv(4096)
            except socket.timeout:
                continue
            if not d:
                raise AssertionError("closed before %r: %r" % (text, got[0][-400:]))
            got[0] += d
        return got[0]

    def test_ibm_beacon_after_the_guide(self):
        """The IBM front end: a terminal program dials, the guide comes, and RSCARDS started later sends its
        beacon with no FLC: graphics mode and the sign-in field."""
        f = self.front(machine=4, version="05.020", flc=False)
        f.until(lambda: b"press T" in f.terminal, what="the guide")
        self.assertTrue(all(len(l) <= 40 for l in f.terminal.decode("latin-1").split("\r\n")), f.terminal)
        f.enter_graphics()
        f.wait_field()
        self.assertTrue(f.shows("Your ParlorNet name"))

    def test_mac_by_hand(self):
        """The Mac's Command-R: the beacon with no FLC, before any guide."""
        f = self.front(flc=False)
        f.until(lambda: b"\x05\x05\x051" in f.terminal, what="the trigger")
        f.enter_graphics()
        f.wait_field()
        self.assertNotIn(b"press T", f.terminal)

    def test_text_terminal_plays_text_games(self):
        """A plain terminal gets the guide, presses T and plays Reversi with the text games' plain screens on the
        same connection; no telnet command reaches it."""
        s, got = self.raw_term(), [b""]
        self.read_until(s, "press T", got)
        s.sendall(b"T")
        self.read_until(s, "Login", got)
        for send, wait in ((b"new\r\n", "Name you want"), (b"tty\r\n", "Choose a password"),
                           (b"tty-Pass-word\r\n", "Type it again"), (b"tty-Pass-word\r\n", "Press Enter"),
                           (b"\r\n", "Your tables"),
                           (b"open reversi 5m\r\n", "Waiting for a second player"),
                           (b"/invite bot\r\n", "Your turn. Type a square such as"), (b"/d3\r\n", "tty plays d3")):
            s.sendall(send)
            self.read_until(s, wait, got, 20)
        self.assertNotIn(b"\xff", got[0])

    def test_text_terminal_that_sits(self):
        """A terminal that does nothing: the guide, once more after a while, and the line stays open."""
        s, got = self.raw_term(), [b""]
        self.read_until(s, "press T", got)
        n = got[0].count(b"press T")
        self.read_until(s, "telnet to port 6502 next time.", got)
        end = time.time() + 15
        while got[0].count(b"press T") < n + 1 and time.time() < end:
            try:
                d = s.recv(4096)
            except socket.timeout:
                continue
            self.assertTrue(d, "closed")
            got[0] += d
        self.assertEqual(got[0].count(b"press T"), 2)
        self.assertGreaterEqual(got[0].count(b"\x05\x05\x051\r \r"), 2)       # the trigger again, its 1 blanked
        s.sendall(b"\r\n")            # one RETURN is not two
        time.sleep(1)
        s.sendall(b"hello\r\n")
        time.sleep(1)
        try:
            d = s.recv(4096)
            self.assertNotIn(b"Login", d)
        except socket.timeout:
            pass

    def test_waiting_limit(self):
        """Open a third time at one game: the core's ERR LIMIT text in the message area, and no table left over."""
        pw = self.signup("lim")
        f = self.signed_in("lim", pw)
        n = self.native("lim", pw)
        row = 3 + A_GAMES.index("checkers/1")
        for i in range(3):
            f.until(lambda: f.shows("Start a game") and f.area_at(27 * 8 + 4, row * 8 + 2),
                    what="the lobby's Open")
            self.quiet(f)
            k = len(f.msgs)
            f.click(27 * 8 + 4, row * 8 + 2)
            if i < 2:
                f.until(lambda: f.shows("Checkers, table"), what="the open table")
                self.quiet(f)
                self.press(f, "Lobby")
        f.until(lambda: "waiting at 2" in " ".join(f.msgs[k:]), what="the refusal")
        self.assertIn("Can't", " ".join(f.msgs[k:]))
        tables = [l for l in n.req("LST mine") if l.startswith("TAB ")]
        self.assertEqual(len(tables), 2, tables)
        self.assertEqual(f.bad, 0)

    def test_mono(self):
        """A mono front end (Mac mono): colors 0 and white only, and the board still tells the sides apart."""
        f, n, tid, seat = self.start_game("mo", "checkers/1", machine=8)
        self.quiet(f)
        colors = set(f.px)
        self.assertLessEqual(colors, {0, 1})
        from adapters.games.checkers import rc
        # a black man (seat 0) is a white rim round a black center; a white man is white at its center
        for sq_, center in ((1, 0), (32, 1)):
            r, c = rc(sq_)
            if seat == 0:
                r, c = 7 - r, 7 - c
            x, y = 8 + c * 40 + 20, r * 20 + 10
            self.assertEqual(f.pixel(x, y), center, (sq_, x, y))
        self.shot(f, "checkers-mono")

    # ---- the games against the bot ------------------------------------------------------------------------
    def test_checkers(self):
        self.play_game("checkers/1", self.mv_checkers, 400, whole=True)

    def test_backgammon(self):
        self.play_game("backgammon/1", self.mv_backgammon, 6)

    def test_reversi(self):
        self.play_game("reversi/1", self.mv_reversi, 6)

    def test_mancala(self):
        self.play_game("mancala/1", self.mv_mancala, 6)

    def test_fiveinarow(self):
        self.play_game("fiveinarow/1", self.mv_squares, 6)

    def test_dotsandboxes(self):
        self.play_game("dotsandboxes/1", self.mv_dots, 6)

    def test_navalcombat(self):
        self.play_game("navalcombat/1", self.mv_naval, 6)

    def test_gridwords(self):
        self.play_game("gridwords/1", self.mv_gridwords, 3)

    def test_rummytile(self):
        self.play_game("rummytile/1", self.mv_rummy, 3)

    def test_cannonfire(self):
        self.play_game("cannonfire/1", self.mv_cannon, 3)

    # ---- the harness ----------------------------------------------------------------------------------------
    def native(self, name, pw):
        from test_fibs import Native
        n = Native(self.core, name, pw)
        n.name = name
        self.addCleanup(n.close)
        return n

    def quiet(self, f, secs=0.6):
        """Read until the extension has sent nothing for secs."""
        end = time.time() + WAIT
        while time.time() < end:
            if not f.pump(secs):
                return
        raise AssertionError("the extension never went quiet")

    def press(self, f, label):
        """Click the button labeled label (once it is drawn and its area is there)."""
        def find():
            for (x, y), (text, fg, bg) in list(f.texts.items()):
                if text.strip() in (label, "Sure? " + label) and f.area_at(x + 4, y + 3):
                    return x, y
            return None
        x, y = f.until(find, what="the button %r" % label)
        f.click(x + 4, y + 3)
        return True

    def start_game(self, name, game, **kw):
        pw = self.signup(name)
        f = self.signed_in(name, pw, **kw)
        n = self.native(name, pw)
        row = 3 + A_GAMES.index(game)
        f.until(lambda: f.area_at(20 * 8 + 2, row * 8 + 2) and f.area_at(20 * 8 + 2, row * 8 + 2, True),
                what="the lobby's areas")
        self.quiet(f)
        f.entered = f.bytes
        f.click(20 * 8 + 2, row * 8 + 2)
        title = "%s, table" % G.GAME_NAMES[game]
        f.until(lambda: f.shows(title), what="the %s screen" % game)
        import re
        tid = re.search(r"table (\d+)", f.all_text()).group(1)
        end = time.time() + WAIT
        while time.time() < end and "playing" not in " ".join(n.req("LST")):
            time.sleep(0.2)
        seat = next(l.split()[6].split(",").index(name) for l in n.req("LST") if l.split()[1:2] == [tid])
        return f, n, tid, seat

    def core_view(self, n, tid):
        return n.req("VUE %s M" % tid)

    def turn(self, view):
        for l in view:
            f = l.split()
            if f[:1] == ["W"] and len(f) == 2:
                return -1 if f[1] in ("-", "-1") else int(f[1])
        return None

    def play_game(self, game, mover, most, whole=False, **kw):
        """Sign in, play the bot at game by clicks (mover): after two of my turns leave for the lobby and come
        back (the whole screen again, measured), then play on, up to most turns or (whole) to the end. Records
        the bytes: the whole screen, and the median and largest exchange (my turn and the bot's reply)."""
        name = game.split("/")[0][:6] + "p"
        kw.setdefault("machine", MACHINE)
        if kw["machine"] == 4:
            kw.setdefault("version", "05.020")
        f, n, tid, seat = self.start_game(name, game, **kw)
        self.quiet(f)
        entry = f.bytes - f.entered
        self.shot(f, game.split("/")[0] + "-start")
        moves, per, full = 0, [], None
        for _ in range(most * 4 if whole else most):
            view = self.wait_my_turn(f, n, tid, seat)
            if view is None:
                break
            before = f.bytes
            if mover(f, n, tid, seat, view) is False:
                break
            moves += 1
            self.wait_next(n, tid, seat, view)
            self.quiet(f)
            per.append(f.bytes - before)
            if moves == 2 and full is None:
                self.shot(f, game.split("/")[0] + "-play")
                full = self.lobby_and_back(f, game, tid)
            if not whole and moves >= most:
                break
        self.assertGreater(moves, 0)
        self.assertEqual(f.bad, 0)
        over = self.turn(self.core_view(n, tid)) == -1 or "playing" not in " ".join(
            l for l in n.req("LST") if l.split()[1:2] == [tid])
        if whole:
            self.assertTrue(over, "the game did not end")
            self.shot(f, game.split("/")[0] + "-end")
        if full is None and not over:
            full = self.lobby_and_back(f, game, tid)
        self.assertLessEqual(len(f.areas), 30)
        MEASURED[game] = (full or 0, sorted(per)[len(per) // 2] if per else 0, max(per) if per else 0, moves, entry,
                          f.peak["B"], f.peak["H"])
        return f

    def lobby_and_back(self, f, game, tid):
        """Lobby, then back to the table: -> the bytes of the table's whole screen."""
        self.press(f, "Lobby")
        f.until(lambda: f.shows("Start a game"), what="the lobby")
        self.quiet(f)
        before = f.bytes
        f.until(lambda: f.area_at(41 * 8 + 4, 3 * 8 + 2) and f.area_at(41 * 8 + 4, 13 * 8 + 2, True),
                what="my game in the lobby")
        row = next(r for r in (3, 4, 5) if (f.text_at(41, r) or "").startswith(tid + " "))
        f.click(41 * 8 + 4, row * 8 + 2)
        f.until(lambda: f.shows("%s, table %s" % (G.GAME_NAMES[game], tid)), what="the table again")
        self.quiet(f)
        return f.bytes - before

    def wait_my_turn(self, f, n, tid, seat):
        """The core's view when it is my turn and the screen says so; None when the game is over."""
        end = time.time() + 60
        while time.time() < end:
            view = self.core_view(n, tid)
            t = self.turn(view)
            if t == -1 or any(l.startswith("ERR") for l in view):
                return None
            if t == seat and any(l.startswith("Q place") for l in view) or (t == seat and (
                    any(l.startswith("M ") for l in view) or not any(l.startswith("M ") for l in view))):
                f.pump(0.3)
                if f.shows("Your move") or f.shows("Place your") or f.shows("fleet is placed"):
                    return view
            f.pump(0.3)
        raise AssertionError("my turn never came: %s" % f.all_text())

    def wait_next(self, n, tid, seat, view):
        """Until the core's view has moved on from view (my move made)."""
        seq = next((l.split()[2] for l in view if l.startswith("V ")), None)
        end = time.time() + 30
        while time.time() < end:
            v = self.core_view(n, tid)
            if next((l.split()[2] for l in v if l.startswith("V ")), None) != seq:
                return
            time.sleep(0.2)
        raise AssertionError("my move was not made")

    @staticmethod
    def moves(view):
        return [l.split(" ", 1)[1] for l in view if l.startswith("M ")]

    # ---- one turn of each game, by clicks ---------------------------------------------------------------------
    def mv_checkers(self, f, n, tid, seat, view):
        from adapters.games.checkers import rc
        steps = [m for m in self.moves(view) if "-" in m or "x" in m]
        if not steps:
            self.press(f, "Accept draw")
            return
        sq_list = [int(x) for x in steps[0].replace("x", "-").split("-")]

        def center(s_):
            r, c = rc(s_)
            if seat == 0:
                r, c = 7 - r, 7 - c
            return 8 + c * 40 + 20, r * 20 + 10
        f.until(lambda: f.area_at(*center(sq_list[0])) and f.area_at(*center(sq_list[0]), hots=True),
                what="the squares' areas")
        for s_ in sq_list:
            f.click(*center(s_))
            self.quiet(f, 0.3)

    def mv_backgammon(self, f, n, tid, seat, view):
        scr = G.Backgammon.__new__(G.Backgammon)
        scr.me = seat
        if any(l == "Q roll" for l in view):
            self.press(f, "Roll")
            return
        if any(l == "Q take" for l in view):
            self.press(f, "Take")
            return
        for _ in range(4):
            steps = [m for m in self.moves(view) if "-" in m]
            if not steps:
                break
            a, b = steps[0].split("-")
            for where in (a, b):
                if where == "bar":
                    x, y = G.BG_X0 + 6 * G.BG_PW + 5, 120
                elif where == "off":
                    x, y = G.BG_TRAY_X + 5, 120
                else:
                    px, top = scr.pos(int(where))
                    x, y = px + 11, (G.BG_TOP + 20) if top else (G.BG_BOT - 20)
                f.click(x, y)
                self.quiet(f, 0.3)
            view = self.core_view(n, tid)
        if any(l == "Q move" for l in view) and self.turn(view) == seat:
            self.press(f, "Done")

    def mv_reversi(self, f, n, tid, seat, view):
        m = self.moves(view)[0]
        c, r = ord(m[0]) - 97, int(m[1:]) - 1
        f.until(lambda: f.area_at(8 + c * 40 + 20, r * 20 + 10), what="the board's areas")
        f.click(8 + c * 40 + 20, r * 20 + 10)

    def mv_mancala(self, f, n, tid, seat, view):
        scr = G.Mancala.__new__(G.Mancala)
        scr.me = seat
        p = int(self.moves(view)[0])
        x, y = scr.pit_rect(seat, p)
        f.until(lambda: f.area_at(x + 10, y + 10), what="the pits' areas")
        f.click(x + 10, y + 10)

    def mv_squares(self, f, n, tid, seat, view):
        from bot.games import fiveinarow as B
        import random
        size = int(next((l.split()[1] for l in view if l.startswith("BRD")), "15"))
        cells = ["."] * (size * size)
        for l in view:
            p = l.split()
            if p[:1] == ["P"] and len(p) > 1:
                for t in p[2:]:
                    cx, cy = ord(t[0]) - 97, int(t[1:]) - 1
                    cells[cy * size + cx] = p[1]
        rules = next((l.split()[1] for l in view if l.startswith("RUL")), "freestyle")
        i = B.choose({"n": size, "rules": rules, "cells": "".join(cells)}, seat, random.Random(1))
        c, r = i % size, i // size
        cw, ch = min(20, 300 // size), min(10, 150 // size)
        x, y = G.FiveInARow.X0 + c * cw + cw // 2, G.FiveInARow.Y0 + r * ch + ch // 2
        f.until(lambda: f.area_at(x, y) and f.area_at(x, y, True), what="the board's areas")
        f.click(x, y)

    def mv_dots(self, f, n, tid, seat, view):
        line = self.moves(view)[0]
        a, b = line.split("-")
        (c0, r0), (c1, r1) = [(ord(t[0]) - 97, int(t[1:]) - 1) for t in (a, b)]
        scr = G.DotsAndBoxes.__new__(G.DotsAndBoxes)
        scr.v = {"w": 5, "h": 5}
        for l in view:
            if l.startswith("BRD"):
                scr.v = {"w": int(l.split()[1]), "h": int(l.split()[2])}
        px, py = scr.pitch()
        x = G.DotsAndBoxes.X0 + (c0 + c1) * px // 2
        y = G.DotsAndBoxes.Y0 + (r0 + r1) * py // 2
        f.until(lambda: f.area_at(x, y) and f.area_at(x, y, True), what="the board's areas")
        f.click(x, y)

    def mv_naval(self, f, n, tid, seat, view):
        if any(l == "Q place" for l in view):
            self.press(f, "Place at random")
            self.press(f, "Ready")
            return
        m = self.moves(view)[0]
        c, r = ord(m[0]) - 97, int(m[1:]) - 1
        x, y = G.NavalCombat.GRIDS["tgt"] + c * 16 + 8, G.NavalCombat.Y0 + r * 10 + 5
        f.until(lambda: f.area_at(x, y) and f.area_at(x, y, True), what="their waters' areas")
        f.click(x, y)

    def mv_gridwords(self, f, n, tid, seat, view):
        from bot.games import gridwords as B
        from bot.play import View
        v = View(view)
        best = B.plays(B.state(v, seat), seat, 2.0, B.ruleset(v), best=1)
        if not best:
            f.type_line("pass")
            return
        _, x, y, d, text = best[0]
        X, Y = G.GridWords.X0 + (x - 1) * 20 + 10, G.GridWords.Y0 + (y - 1) * 10 + 5
        f.until(lambda: f.area_at(X, Y) and f.area_at(X, Y, True), what="the board's areas")
        f.click(X, Y)
        self.quiet(f, 0.3)
        if d == "V":
            f.click(X, Y)
            self.quiet(f, 0.3)
        f.type_line(text)

    def mv_rummy(self, f, n, tid, seat, view):
        self.press(f, "Done")           # nothing played: a tile is drawn

    def mv_cannon(self, f, n, tid, seat, view):
        self.press(f, "Angle +5")
        self.quiet(f, 0.3)
        self.press(f, "Fire")


MEASURED = {}


def tearDownModule():
    if MEASURED and os.environ.get("RSC_BYTES"):
        with open(os.environ["RSC_BYTES"], "w") as out:
            for game, (full, med, top, moves, entry, b, h) in sorted(MEASURED.items()):
                out.write("%s first %d again %d exchange-median %d exchange-max %d moves %d areas B %d H %d\n"
                          % (game, entry, full, med, top, moves, b, h))


A_GAMES = ("reversi/1", "backgammon/1", "checkers/1", "mancala/1", "fiveinarow/1", "dotsandboxes/1",
           "navalcombat/1", "gridwords/1", "rummytile/1", "cannonfire/1")


if __name__ == "__main__":
    unittest.main()
