"""Telnet window size (NAWS) handling and mouse reports."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters.telnet import IAC, NAWS, SB, SE, WILL, TelnetIO


class FakeWriter:
    def __init__(self):
        self.data = b""

    def write(self, b):
        self.data += b

    async def drain(self):
        pass


class Naws(unittest.TestCase):
    def test_size_reports_and_redraw(self):
        io = TelnetIO(None, FakeWriter())
        self.assertIsNone(io.size)                       # nothing reported: 80x24 is assumed
        io._feed(bytes([IAC, WILL, NAWS, IAC, SB, NAWS, 0, 100, 0, 30, IAC, SE]))
        self.assertEqual(io.size, (100, 30))
        self.assertEqual(io.keys, ["REDRAW"])
        io.keys.clear()
        io._feed(bytes([IAC, SB, NAWS, 0, 100, 0, 30, IAC, SE]))   # same size: nothing to do
        self.assertEqual(io.keys, [])
        io._feed(bytes([IAC, SB, NAWS, 0, 255, 255, 0, 24, IAC, SE]))  # 255 is sent doubled
        self.assertEqual(io.size, (255, 24))
        io._feed(bytes([IAC, SB, NAWS, 0, 0, 0, 0, IAC, SE]))      # zero means unknown: keep the last
        self.assertEqual(io.size, (255, 24))
        io._feed(b"d3\r\n")
        self.assertEqual(io.keys[-3:], ["d", "3", "ENTER"])

    def test_too_small_notice(self):
        from adapters.telnet import AnsiView, Painter

        class S:
            pass
        s = S()
        s.io = TelnetIO(None, FakeWriter())
        s.painter = Painter()
        s.g = None
        v = AnsiView(s)
        s.io.size = (60, 20)
        asyncio.run(v.show())
        self.assertIn(b"needs 80x24", s.io.w.data)
        self.assertIsNone(s.painter.prev)


class Mouse(unittest.TestCase):
    def feed(self, data):
        io = TelnetIO(None, FakeWriter())
        io._feed(data)
        return io.keys

    def test_sgr_reports(self):
        self.assertEqual(self.feed(b"\x1b[<0;19;9M\x1b[<0;19;9m"), [("CLICK", 18, 8)])   # press; release ignored
        self.assertEqual(self.feed(b"\x1b[<0;120;40M"), [("CLICK", 119, 39)])         # beyond 95 columns
        self.assertEqual(self.feed(b"\x1b[<16;3;4M"), [("CLICK", 2, 3)])               # with Ctrl held
        for other in (b"\x1b[<2;5;5M", b"\x1b[<1;5;5M", b"\x1b[<64;5;5M", b"\x1b[<32;5;5M", b"\x1b[<0;5M"):
            self.assertEqual(self.feed(other), [], other)       # right, middle, wheel, motion, malformed
        self.assertEqual(self.feed(b"\x1b[<0;2;2Mx\x1b[A\r"), [("CLICK", 1, 1), "x", "UP", "ENTER"])

    def test_legacy_reports(self):
        self.assertEqual(self.feed(b"\x1b[M" + bytes([32, 32 + 19, 32 + 9])), [("CLICK", 18, 8)])
        self.assertEqual(self.feed(b"\x1b[M" + bytes([35, 32 + 19, 32 + 9])), [])      # release
        self.assertEqual(self.feed(b"\x1b[M" + bytes([32 + 64, 40, 40])), [])          # wheel
        io = TelnetIO(None, FakeWriter())                   # a report split across reads
        io._feed(b"\x1b[M ")
        io._feed(bytes([33, 33]) + b"d")
        self.assertEqual(io.keys, [("CLICK", 0, 0), "d"])
        self.assertEqual(self.feed(b"\x1bOM"), [])                                     # keypad Enter (SS3), not a mouse
        self.assertEqual(self.feed(b"\x1b[M" + bytes([32, 255, 255, 33])), [("CLICK", 222, 0)])  # IAC doubled

    def test_mouse_terminals(self):
        from adapters.telnet import mouse_for_ttype
        for t in ("xterm-256color", "XTERM", "screen.xterm", "tmux-256color", "rxvt-unicode", "putty", "iTerm2",
                  "alacritty", "xterm-kitty", "konsole", "gnome-terminal", "wezterm", "foot"):
            self.assertTrue(mouse_for_ttype(t), t)
        for t in ("linux", "vt100", "ansi", "dumb", "", None):
            self.assertFalse(mouse_for_ttype(t), t)


if __name__ == "__main__":
    unittest.main()
