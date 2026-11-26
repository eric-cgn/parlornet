"""The telnet extension's title page (adapters/title.py): each display mode's screen, its bytes, and the session."""
import asyncio
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters import telnet, title  # noqa: E402
from adapters.telnet import TelnetIO  # noqa: E402

BUDGET = 1400       # bytes, in any mode: about 6 seconds at 2400 baud
ENCODING = {"plain": "ascii", "ansi": "ascii", "pc": "cp437", "utf8": "utf-8"}


def expected():
    """The screen the grid gives: '#' for a block, as 24 lines of 80."""
    rows = [""]
    for word in ("PARLOR", "NET"):
        for r in range(9):
            line = ".".join(title.GRID[c][r] for c in word).replace("#", "##").replace(".", "  ")
            rows.append(" " * ((80 - len(line)) // 2) + line)
        rows.append("")
    return [r.ljust(80) for r in rows]


class Screen:
    """Just enough of an ANSI terminal for the page: CR, LF, clear, home, cursor forward and colors. Each
    cell is (character, foreground, background); a foreground of 30-37 with bold is reported as 90-97."""

    def __init__(self, text):
        self.cells = [[(" ", None, None)] * 80 for _ in range(24)]
        self.x = self.y = 0
        fg = bg = None
        bold = False
        for tok in re.split(r"(\x1b\[[0-9;]*[A-Za-z]|\r|\n)", text):
            if tok == "\r":
                self.x = 0
            elif tok == "\n":
                self.y += 1
                assert self.y < 24, "the page scrolled"
            elif tok.startswith("\x1b["):
                args, f = tok[2:-1], tok[-1]
                if f == "J":
                    self.cells = [[(" ", None, None)] * 80 for _ in range(24)]
                elif f == "H":
                    self.x = self.y = 0
                elif f == "C":
                    self.x += int(args or 1)
                elif f == "m":
                    for p in [int(a) if a else 0 for a in args.split(";")]:
                        if p == 0:
                            fg = bg = None
                            bold = False
                        elif p == 1:
                            bold = True
                        elif 30 <= p <= 37:
                            fg = p
                        elif 40 <= p <= 47 or 100 <= p <= 107:
                            bg = p
                else:
                    raise AssertionError("unexpected escape %r" % tok)
            else:
                for ch in tok:
                    assert self.x < 80, "a line longer than 80"
                    self.cells[self.y][self.x] = (ch, fg + 60 if bold and fg else fg, bg)
                    self.x += 1

    def text(self):
        return ["".join(c[0] for c in row) for row in self.cells]


class Page(unittest.TestCase):
    def test_each_mode_draws_the_grid(self):
        want = expected()
        for mode in ("plain", "ansi", "pc", "utf8"):
            with self.subTest(mode=mode):
                data = title.page(mode).encode(ENCODING[mode])
                self.assertLessEqual(len(data), BUDGET)
                self.assertNotIn(b"\xff", data)                  # nothing telnet would have to double
                text = data.decode(ENCODING[mode])
                if mode == "plain":
                    self.assertNotIn("\x1b", text)
                    lines = text.split("\r\n")
                    self.assertEqual(len(lines), 24)
                    self.assertEqual([l.ljust(80) for l in lines[:21]], want)
                    self.assertTrue(all(len(l) <= 80 for l in lines))
                    continue
                scr = Screen(text)
                self.assertEqual(scr.y, 23)                       # ends on the last line, which has no line end
                for y in range(21):
                    for x in range(80):
                        ch, fg, bg = scr.cells[y][x]
                        if want[y][x] == "#":
                            band = (title.WARM if y < 11 else title.COOL)[(y - 1) % 10]
                            if mode == "utf8":                   # a space in the row's color as its background
                                self.assertEqual((ch, bg), (" ", band + 10), (x, y))
                            else:
                                self.assertEqual((ch, fg), ("█" if mode == "pc" else "#", band), (x, y))
                        else:
                            self.assertEqual((ch, bg), (" ", None), (x, y))
                self.assertEqual(scr.text()[21].strip(), title.CAPTION)
                self.assertEqual(scr.text()[23].strip(), "Press Enter")
                self.assertTrue(text.endswith("\x1b[0m"))

    def test_the_plain_page(self):
        lines = title.page("plain").split("\r\n")
        self.assertEqual(lines[1].strip(), "########      ######    ########    ####          ######    ########")
        self.assertEqual(lines[21].strip(), "Turn-based game server for any computer, old or new")
        self.assertEqual(lines[23], " " * 34 + "Press Enter")

    def test_the_grid_is_the_8bit_clients(self):
        """The letters are art/splash.txt's in parlornet-8bit, when that checkout is at hand (PARLORNET_8BIT,
        or beside this one)."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for root in (os.environ.get("PARLORNET_8BIT"), os.path.join(os.path.dirname(here), "parlornet-8bit")):
            path = root and os.path.join(root, "art", "splash.txt")
            if path and os.path.exists(path):
                with open(path) as f:
                    self.assertEqual(title.parse(f.read()), title.GRID)
                return
        self.skipTest("no parlornet-8bit checkout")


class FakeWriter:
    def __init__(self):
        self.data = b""

    def write(self, b):
        self.data += b

    async def drain(self):
        pass


class FakeReader:
    """What the player typed, then the end of the line."""

    def __init__(self, data):
        self.data = data

    async def read(self, n):
        data, self.data = self.data, b""
        return data


def session_io(typed=b"\r\n", size=None):
    io = TelnetIO(FakeReader(typed), FakeWriter())
    io.size = size
    return io


class Session(unittest.TestCase):
    def test_title_until_enter(self):
        for mode in ("plain", "ansi", "pc", "utf8"):
            with self.subTest(mode=mode):
                io = session_io()
                self.assertTrue(asyncio.run(telnet.title_page(io, mode)))
                page = title.page(mode).replace("\r\n", "\n").replace("\n", "\r\n").encode(ENCODING[mode])
                self.assertTrue(io.w.data.startswith(page))
                self.assertEqual(io.w.data[len(page):], telnet.CLEAR.encode() if mode != "plain" else b"\r\n")
                self.assertEqual(io.charset, "ascii")             # the session's own until the display is chosen
                self.assertEqual(io.keys, [])

    def test_a_small_window_has_none(self):
        for size in ((40, 24), (80, 20)):
            io = session_io(size=size)
            self.assertFalse(asyncio.run(telnet.title_page(io, "pc")))
            self.assertEqual(io.w.data, b"")

    def test_the_session_opens_with_it(self):
        """The title page, then (after Enter) the message of the day and the login prompt; with no terminal
        type the page is plain."""
        io = session_io(b"\r\n")
        seen = {}

        async def motd(args):
            seen["title before motd"] = b"Press Enter" in io.w.data
            return ["Welcome."]

        async def sign_in(io_, args, ticket=None):
            seen["out"] = io.w.data
            return None

        async def detect(secs=None):            # the start: no terminal type, and no Enter for the menu
            return False
        io.detect = detect
        saved = telnet.MOTD.get, telnet.sign_in
        telnet.MOTD.get, telnet.sign_in = motd, sign_in
        try:
            asyncio.run(telnet.main_session(io, None))
        finally:
            telnet.MOTD.get, telnet.sign_in = saved
        self.assertTrue(seen["title before motd"])
        out = seen["out"].decode("ascii")
        self.assertTrue(out.startswith(title.page("plain")))
        self.assertIn("Welcome.", out[len(title.page("plain")):])
        self.assertNotIn("ParlorNet: ", out)                  # the title page says what this is

    def test_rscards_text_games_skip_it(self):
        io = session_io(b"")
        out = {}

        async def motd(args):
            return []

        async def sign_in(io_, args, ticket=None):
            out["data"] = io.w.data
            return None

        async def detect(secs=None):            # the start: no terminal type, and no Enter for the menu
            return False
        io.detect = detect
        saved = telnet.MOTD.get, telnet.sign_in
        telnet.MOTD.get, telnet.sign_in = motd, sign_in
        try:
            asyncio.run(telnet.main_session(io, None, title_first=False))
        finally:
            telnet.MOTD.get, telnet.sign_in = saved
        self.assertNotIn(b"Press Enter", out["data"])
        self.assertIn(b"ParlorNet: ", out["data"])            # no title page and no message: the line instead


if __name__ == "__main__":
    unittest.main()
