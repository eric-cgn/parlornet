"""Telnet's start, its secrets and its clock: the terminal type is detected for a few seconds while one line
says Enter opens the display menu; passwords are never echoed in any display mode, whatever the client
does about ECHO; and the screens show the time connected, redrawn once a minute."""
import asyncio
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters import telnet as T  # noqa: E402
from adapters.common import NeedAuth  # noqa: E402
from adapters.telnet import DO, DONT, ECHO, IAC, SB, SE, TTYPE, WILL, WONT  # noqa: E402

SECRET = "Sec-ret-99"
TTYPE_IS = bytes([IAC, SB, TTYPE, 0]) + b"VT100" + bytes([IAC, SE])


class Writer:
    def __init__(self):
        self.data = b""

    def write(self, b):
        self.data += b

    async def drain(self):
        pass


class TimedReader:
    """What a client sends, each piece at its time in seconds from the first read; after the last piece it
    sends nothing more (a reader waiting then waits until it gives up)."""

    def __init__(self, pieces, end=False):
        self.pieces, self.t0, self.end = list(pieces), None, end

    async def read(self, n):
        loop = asyncio.get_running_loop()
        if self.t0 is None:
            self.t0 = loop.time()
        if not self.pieces:
            if self.end:
                return b""
            await asyncio.sleep(3600)
        at, data = self.pieces[0]
        await asyncio.sleep(max(0.0, self.t0 + at - loop.time()))
        self.pieces.pop(0)
        return data


def make_io(pieces, end=False):
    return T.TelnetIO(TimedReader(pieces, end), Writer())


class Start(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(T, "DETECT_SECS", 0.4)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_line_and_the_wait_with_a_silent_terminal(self):
        io = make_io([])
        t0 = time.time()
        self.assertFalse(asyncio.run(io.detect()))
        self.assertGreaterEqual(time.time() - t0, 0.35, "it waits the few seconds")
        self.assertIsNone(io.ttype)
        self.assertTrue(io.w.data.startswith(bytes([IAC, DO, TTYPE, IAC, DO, 31])))
        self.assertIn(b"Press Enter to choose a terminal type.\r\n", io.w.data)
        self.assertLessEqual(len(T.START_TEXT), 40, "fits a 40-column screen")

    def test_the_type_is_asked_for_and_the_wait_still_runs(self):
        io = make_io([(0.05, bytes([IAC, WILL, TTYPE])), (0.1, TTYPE_IS)])
        t0 = time.time()
        self.assertFalse(asyncio.run(io.detect()))
        self.assertGreaterEqual(time.time() - t0, 0.35, "time to press Enter, even with the type known")
        self.assertEqual(io.ttype, "VT100")
        self.assertIn(bytes([IAC, SB, TTYPE, 1, IAC, SE]), io.w.data)

    def test_enter_opens_the_menu_at_once(self):
        io = make_io([(0.05, b"x\r\n")])
        t0 = time.time()
        self.assertTrue(asyncio.run(io.detect()))
        self.assertLess(time.time() - t0, 0.3)
        self.assertEqual(io.keys, [], "the keys typed while waiting are used up")

    def session(self, pieces, title_first=True):
        """main_session up to the sign-in (stood in for): what was written, and the display mode used."""
        io = make_io(pieces)
        seen = {}

        async def motd(args):
            return []

        async def sign_in(io_, args, ticket=None, core=None):
            seen["out"], seen["ansi"] = io.w.data, io.ansi
            return None
        with mock.patch.object(T.MOTD, "get", motd), mock.patch.object(T, "sign_in", sign_in):
            asyncio.run(T.main_session(io, None, title_first=title_first))
        return seen["out"], seen["ansi"]

    def test_no_key_goes_on_with_the_detected_mode(self):
        out, ansi = self.session([(0.05, bytes([IAC, WILL, TTYPE])), (0.1, TTYPE_IS), (0.6, b"\r\n")])
        self.assertNotIn(b"Choice [", out, "no menu")
        self.assertTrue(ansi)
        self.assertIn(T.title.page("ansi").encode("ascii").split(b"\r\n")[0], out)  # the title in ansi mode
        self.assertIn(b"Your terminal says it is VT100.", out)

    def test_no_answer_at_all_is_plain(self):
        out, ansi = self.session([(0.6, b"\r\n")])
        self.assertNotIn(b"Choice [", out)
        self.assertFalse(ansi)
        self.assertNotIn(b"\x1b", out, "plain mode sends no escape codes")
        self.assertIn(T.title.page("plain").encode("ascii"), out)

    def test_enter_opens_the_display_menu(self):
        out, ansi = self.session([(0.05, bytes([IAC, WILL, TTYPE])), (0.1, TTYPE_IS), (0.15, b"\r\n"),
                                  (0.3, b"4\r\n"), (0.4, b"\r\n")])
        self.assertIn(b"Choose how ParlorNet draws your screen.\r\nPick the first line whose sample looks right.",
                      out)
        self.assertIn(b"Choice [3]: ", out)                 # VT100: ansi is the default
        self.assertFalse(ansi, "4 chose plain")
        self.assertIn(T.title.page("plain").encode("ascii"), out)

    def test_enter_then_enter_keeps_the_default(self):
        out, ansi = self.session([(0.05, b"\r\n"), (0.2, b"\r\n"), (0.3, b"\r\n")])
        self.assertIn(b"Choice [4]: ", out)                 # no type named: plain is the default
        self.assertFalse(ansi)


class Secrets(unittest.TestCase):
    """No secret is ever written back, in any mode, whether the client agrees to stop echoing, refuses or
    says nothing at all."""

    def read(self, client, ansi, typed=SECRET):
        answer = {"agrees": bytes([IAC, DO, ECHO]), "refuses": bytes([IAC, DONT, ECHO]), "silent": b""}[client]
        pieces = ([(0.0, bytes([IAC, WILL, TTYPE]))] if client != "silent" else []) + \
                 ([(0.05, answer)] if answer else []) + [(0.1, typed.encode() + b"\r\n")]
        io = make_io(pieces)
        if client != "silent":
            io._feed(bytes([IAC, WILL, TTYPE]))     # it speaks telnet
            pieces.pop(0)
        io.ansi = ansi
        got = asyncio.run(io.read_secret())
        return io, got

    def test_every_client_and_mode(self):
        for client in ("agrees", "refuses", "silent"):
            for ansi in (True, False):
                with self.subTest(client=client, ansi=ansi):
                    io, got = self.read(client, ansi)
                    self.assertEqual(got, SECRET)
                    self.assertNotIn(SECRET.encode(), io.w.data)
                    self.assertTrue(io.w.data.startswith(bytes([IAC, WILL, ECHO])), "asked before reading")
                    if client == "agrees":
                        self.assertIn(b"*" * len(SECRET), io.w.data)                # a star for each key
                        self.assertTrue(io.w.data.endswith(bytes([IAC, WONT, ECHO])), "the client echoes again")
                        self.assertFalse(io.server_echo)
                    else:
                        self.assertNotIn(b"*", io.w.data)
                    # a client that may echo by itself: the ANSI modes hide what it shows
                    self.assertEqual(b"\x1b[8m" in io.w.data, ansi and client != "agrees")
                    if not ansi:
                        self.assertNotIn(b"\x1b", io.w.data)

    def test_a_second_secret_after_the_first(self):
        """The client's answer to the WONT ECHO after the first secret is not taken for an answer to the
        second WILL ECHO (that hid the stars of "Type it again")."""
        io = make_io([(0.0, bytes([IAC, DO, ECHO]) + b"first-one\r\n"),
                      (0.1, bytes([IAC, DONT, ECHO])), (0.15, bytes([IAC, DO, ECHO]) + b"second-1\r\n")])
        io._feed(bytes([IAC, WILL, TTYPE]))
        io.ansi = True

        async def both():
            return await io.read_secret(), await io.read_secret()
        self.assertEqual(asyncio.run(both()), ("first-one", "second-1"))
        self.assertNotIn(b"\x1b[8m", io.w.data)
        self.assertEqual(io.w.data.count(b"*"), len("first-one") + len("second-1"))

    def test_character_mode_keeps_its_echo(self):
        """In the full screen the server already echoes: no new WILL ECHO, and no WONT ECHO after."""
        io = make_io([(0.0, SECRET.encode() + b"\r\n")])
        io.server_echo, io.ansi = True, True
        self.assertEqual(asyncio.run(io.read_secret()), SECRET)
        self.assertNotIn(bytes([IAC, WILL, ECHO]), io.w.data)
        self.assertNotIn(bytes([IAC, WONT, ECHO]), io.w.data)
        self.assertNotIn(SECRET.encode(), io.w.data)
        self.assertTrue(io.server_echo)

    def test_every_secret_ask_goes_through_read_secret(self):
        """The sign-in password, a new password and its confirmation, the current password at a change and
        a temporary password: every ASK marked secret is read without echo, the others with it."""
        asks = [NeedAuth("password", "Password", True), NeedAuth("current", "Current password", True),
                NeedAuth("temp", "Password from the email", True, cred=True),
                NeedAuth("new", "New password", True, new=True), NeedAuth("confirm", "Type it again", True),
                NeedAuth("email", "Email address", False)]

        class Core:
            def __init__(self):
                self.left, self.got = list(asks[1:]), []

            async def aut(self, field, value):
                self.got.append((field, value))
                if self.left:
                    raise self.left.pop(0)
                return True
        for ansi in (True, False):
            typed = b"".join(b"%s\r\n" % ("addr@example.org" if not a.secret else SECRET + a.field).encode()
                             for a in asks)
            io = make_io([(0.0, bytes([IAC, DO, ECHO])), (0.05, typed)])
            io._feed(bytes([IAC, WILL, TTYPE]))
            io.ansi = ansi
            core = Core()
            self.assertTrue(asyncio.run(T.answer_asks(io, core, asks[0])))
            self.assertEqual([f for f, _ in core.got], [a.field for a in asks])
            for a in asks:
                if a.secret:
                    self.assertNotIn((SECRET + a.field).encode(), io.w.data)
            self.assertEqual(io.w.data.count(bytes([IAC, WILL, ECHO])), 5, "not for the email address")

    def test_the_full_screen_password_box(self):
        """The ANSI menu's password change: stars in the box, and with a client that echoes by itself the
        typed key is hidden and the next screen starts with a full reset."""
        from test_adapters import FakeIO

        class Core:
            auth = "password"

            async def pwd(self):
                raise NeedAuth("current", "Current password", True)

            async def aut(self, field, value):
                return True
        for mode in ("pc", "utf8", "ansi"):
            io = FakeIO(mode, list(SECRET) + ["ENTER"])
            sent = []
            write = io.write
            io.write = lambda text, write=write: (sent.append(text), write(text))
            s = T.Session(io, Core(), mode, "ann")
            menu = T.AnsiMenu(s)
            asyncio.run(menu.password())
            out = "".join(sent)
            self.assertNotIn(SECRET, out)
            self.assertTrue(any("*" * len(SECRET) in "\n".join(scr) for scr in io.screens), "a star a key")
            self.assertIn("\x1b[8m", out, "io.server_echo is False: the client may echo by itself")
            self.assertEqual(menu.msg, "Your password is changed. You are signed out everywhere else.")


class Clock(unittest.TestCase):
    def test_connected_text(self):
        for secs, text in ((0, "0m"), (59, "0m"), (60, "1m"), (12 * 60 + 30, "12m"), (3599, "59m"),
                           (3600, "1h 0m"), (3900, "1h 5m"), (26 * 3600 + 60, "26h 1m")):
            self.assertEqual(T.connected_text(secs), text)

    def menu(self, mode, since):
        from test_adapters import FakeIO

        class Core:
            auth = "none"

            async def req(self, text):
                return ([], "OK")
        s = T.Session(FakeIO(mode), Core(), mode, "ann")
        s.since = since
        return s, T.AnsiMenu(s)

    def test_in_the_title_bar_of_every_ansi_mode(self):
        for mode in ("pc", "utf8", "ansi"):
            s, menu = self.menu(mode, time.time() - 65 * 60)
            cv, _ = menu.screen()
            top = "".join(cv.ch[0])
            self.assertTrue(top.rstrip().endswith("ann %s 1h 5m onl unl rem" % s.g["v"]), top)
            self.assertIn("Lobby", top)

    def test_redrawn_only_when_the_minute_changes(self):
        s, menu = self.menu("pc", time.time() - 61)
        cv, pos = menu.screen()
        s.painter.frame(cv, pos, True)
        cv, pos = menu.screen()
        self.assertEqual(s.painter.frame(cv, pos), "", "the same minute: nothing to send")
        s.since -= 60                                       # a minute later
        cv, pos = menu.screen()
        data = s.painter.frame(cv, pos)
        self.assertIn("2", data)
        self.assertLess(len(data), 20, data)                # the changed digit and a cursor move
        self.assertIn("2m", "".join(cv.ch[0]))

    def test_a_game_screen_has_it_too(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from game_checks import ScreenCore
        from test_adapters import FakeIO
        from adapters.games import reversi
        io = FakeIO("pc")
        s = T.Session(io, ScreenCore(reversi.KEY, []), "pc", "ann")
        s.since = time.time() - 12 * 60
        g = reversi.AnsiScreen(s, "3", reversi.KEY, 0)
        g.names, g.v = ["ann", "bob"], None
        cv, _ = g.screen()
        self.assertTrue("".join(cv.ch[0]).rstrip().endswith("12m onl unl rem"))

    def test_the_plain_menu_header(self):
        from test_adapters import FakeIO

        class Core:
            auth = "none"

            async def req(self, text):
                return ([], "OK")
        io = FakeIO("plain")
        s = T.Session(io, Core(), "plain", "ann")
        s.since = time.time() - 12 * 60
        asyncio.run(T.PlainMenu(s).relist())
        header = next(l for l in io.vt.lines() if l.startswith("== ParlorNet lobby"))
        self.assertTrue(header.endswith("= ann | 12m onl unl rem"), header)
        self.assertLessEqual(len(header), 79)

    def test_the_time_left_from_tim(self):
        """A connection with a time limit: TIM's left= gives the minutes left, asked at most once a minute."""
        from test_adapters import FakeIO
        asked = []

        class Core:
            auth = "none"

            async def req(self, text):
                asked.append(text)
                if text == "TIM":
                    return (["TIM 720 2026-11-24T21:05:33Z left=3800 call=3800 month=unl"], "OK")
                return ([], "OK")
        io = FakeIO("plain")
        s = T.Session(io, Core(), "plain", "ann")
        s.since = time.time() - 12 * 60
        asyncio.run(T.PlainMenu(s).relist())
        header = next(l for l in io.vt.lines() if l.startswith("== ParlorNet lobby"))
        self.assertTrue(header.endswith("= ann | 12m onl 63m rem"), header)
        asyncio.run(s.load())
        self.assertEqual(asked.count("TIM"), 1)             # once a minute, not with every list
        s.left_at -= 120                                     # asked two minutes ago: 61 minutes are left
        self.assertEqual(T.online_text(s), "12m onl 61m rem")


if __name__ == "__main__":
    unittest.main()
