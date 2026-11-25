"""A scripted RSCARDS front end for the tests (adapters/rscards.py).

It behaves as the Mac and IBM front ends do (as their code reads and as checked against the IBM front end in
an emulator): in terminal mode it answers ENQ ENQ ENQ 1 with FLC n 9, waits for LAUNCH 1 n
and sends its beacon; in graphics mode it parses every packet, checks its checksum, answers ACK (or NAK for a
bad one, after which it ignores input until the line is quiet), ignores a packet identical to the last one it
acknowledged, and draws: a 640 by 200 screen of color indices, the click areas in the order they were defined
(the first one that holds a point wins), the message area, the entry line and the input field. A click sends
the code three times and CR; a field or the entry line sends four spaces, the text and CR.

render() writes the screen as a PNG, with text drawn as blocks, for looking at a layout."""
import random
import socket
import struct
import sys
import time
import zlib

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters import rscwire as R  # noqa: E402

AREA_MAX = 30       # the Mac front end's room for click areas of each kind (B, H); the IBM's has 40
# click areas of each kind (B, H) each front end holds, by machine code: the Mac's (7, 8) hold 30 of each (a 31st
# takes the place of the first), the IBM PC's (4, 5) 40 (a 41st overwrites its memory and hangs it); others as
# the Mac's until measured
AREA_LIMITS = {4: 40, 5: 40, 7: 30, 8: 30}
RGB = [(0, 0, 0), (255, 255, 255), (220, 0, 0), (0, 200, 0), (0, 0, 230), (240, 240, 0), (230, 0, 230),
       (0, 230, 230)]


class Front:
    def __init__(self, port, machine=7, version="05.000", mono=False, telnet=False, timeout=20, flc=True):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.machine, self.version = machine, version
        self.mono = mono
        self.telnet = telnet
        self.flc = flc               # answer the auto-shift trigger (the Mac, C128); False: a front end started by
        self.timeout = timeout       # hand later (the IBM's, the Mac's Command-R): enter_graphics() sends the beacon
        self.buf = b""
        self.terminal = b""          # what terminal mode showed
        self.graphics = False
        self.px = bytearray(640 * 200)
        self.areas = []              # [(rect, code)]: B areas
        self.hots = []               # H areas (highlighted under the pointer); a click in both sends both
        self.area_limit = AREA_LIMITS.get(machine, AREA_MAX)
        self.peak = {"B": 0, "H": 0}  # the most click areas of each kind defined at once
        self.texts = {}              # (x, y) -> (text, fg, bg)
        self.macros = {}
        self.field = None            # (x, y, maxlen, text)
        self.entry = False
        self.mouse = False
        self.scroll = None
        self.msgs = []               # every message-area line, in order
        self.last = None             # the last packet acknowledged
        self.packets = 0
        self.bytes = 0               # graphics bytes received
        self.bad = 0
        self.log = []                # (cmd, args) of every packet drawn
        self.closed = False
        if telnet:
            self.s.sendall(bytes([255, 251, 24, 255, 253, 1]))      # IAC WILL TTYPE, IAC DO ECHO

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass

    # ---- reading ----------------------------------------------------------------------------------------
    def pump(self, secs=0.2):
        """Read for up to secs; returns True when something came."""
        self.s.settimeout(secs)
        try:
            data = self.s.recv(65536)
        except socket.timeout:
            return False
        except OSError:
            data = b""
        if not data:
            self.closed = True
            return False
        self.feed(data)
        return True

    def until(self, test, secs=None, what="it"):
        end = time.time() + (secs or self.timeout)
        while time.time() < end:
            got = test()
            if got:
                return got
            if self.closed:
                break
            self.pump(0.2)
        got = test()
        if got:
            return got
        raise AssertionError("timed out waiting for %s; messages %r; texts %r" % (
            what, self.msgs[-6:], sorted(t[0] for t in self.texts.values())[:40]))

    def feed(self, data):
        if not self.graphics:
            while data:
                if data[0] == 255:          # a telnet command: the front end's modem would pass it on as junk
                    self.telnet_seen = data[:3]
                    data = data[3:]
                    continue
                self.terminal += data[:1]
                data = data[1:]
                if self.terminal.endswith(b"\x05\x05\x051") and self.flc:
                    self.n = random.randint(0, 32767)
                    self.s.sendall(b"FLC %d 9\r" % self.n)
                elif getattr(self, "n", None) is not None and self.terminal.endswith(b"LAUNCH 1 %d\r\n" % self.n):
                    self.enter_graphics()
                    break
            if self.graphics and data:
                self.feed(data)
            return
        self.buf += data
        self.bytes += len(data)
        self.drain()

    def enter_graphics(self):
        self.graphics = True
        self.beacon()

    def beacon(self):
        self.s.sendall(b"RSCARDS" + bytes([0x20 + self.machine]) + b"v" + self.version.encode() + b"\r")

    def drain(self):
        while True:
            data = bytes(x for x in self.buf if x not in (13, 10))
            i = 0
            while i < len(data) and chr(data[i]) not in R.ARGS:
                i += 1
            data = data[i:]
            if not data:
                self.buf = b""
                return
            try:
                got = R.parse(data[:self.packet_len(data)])
            except (R.WireError, IndexError):
                self.buf = data
                return
            n = self.packet_len(data)
            if n is None:
                self.buf = data
                return
            self.buf = data[n:]
            cmd, args, ok = got[0]
            raw = data[:n]
            if not ok:
                self.bad += 1
                self.s.sendall(b"\x15\r")
                continue
            self.s.sendall(b"\x06\r")
            if raw == self.last:
                continue
            self.last = raw
            self.packets += 1
            self.do(cmd, args)

    @staticmethod
    def packet_len(data):
        cmd = chr(data[0])
        i = 1
        for kind in R.ARGS[cmd]:
            if kind == "s":
                end = data.find(b"~", i)
                if end < 0:
                    return None
                i = end + 1
            elif kind == "S":
                if i < 3:
                    return None
                n = R.decode("w", data[i - 2:i])
                i += n
            else:
                i += R.SIZES[kind]
        i += 2
        return i if i <= len(data) else None

    # ---- drawing ----------------------------------------------------------------------------------------
    def color(self, c):
        c = c & 7
        return (1 if c else 0) if self.mono else c

    def rect(self, x, y, w, h, c):
        if (x, y, w, h) == (0, 0, 0, 0):
            x, y, w, h = 0, 0, 640, 200
        c = self.color(c)
        for yy in range(max(0, y), min(200, y + h)):
            self.px[yy * 640 + max(0, x):yy * 640 + min(640, x + w)] = bytes([c]) * max(0, min(640, x + w) - max(0, x))
        for k in [k for k in self.texts if x <= k[0] < x + w and y <= k[1] < y + h]:
            del self.texts[k]

    def do(self, cmd, args, ox=0, oy=0):
        self.log.append((cmd, args))
        if cmd == "A":
            (x, y, w, h), c = args
            if (x, y, w, h) == (0, 0, 0, 0):
                self.rect(0, 0, 640, 200, c)
            else:
                self.rect(x + ox, y + oy, w, h, c)
        elif cmd == "O":
            (x, y, w, h), outer, inner = args
            self.rect(x + ox, y + oy, w, h, outer)
            self.rect(x + ox + 8, y + oy + 4, w - 16, h - 8, inner)
        elif cmd == "E":
            x, y, rx, ry, c = args
            x, y = x + ox, y + oy
            for yy in range(y - ry, y + ry + 1):
                for xx in range(x - rx, x + rx + 1):
                    if 0 <= xx < 640 and 0 <= yy < 200 and ((xx - x) / max(rx, 1)) ** 2 + ((yy - y) / max(ry, 1)) ** 2 <= 1:
                        self.px[yy * 640 + xx] = self.color(c)
        elif cmd == "W":
            x1, y1, x2, y2, c = args
            n = max(abs(x2 - x1), abs(y2 - y1), 1)
            for k in range(n + 1):
                xx, yy = x1 + ox + (x2 - x1) * k // n, y1 + oy + (y2 - y1) * k // n
                if 0 <= xx < 640 and 0 <= yy < 200:
                    self.px[yy * 640 + xx] = self.color(c)
        elif cmd == "X":
            x, y, c, v = args
            self.rect(x + ox, y + oy, 48, 24, c)
            self.texts[(x + ox, y + oy)] = ("die%d" % v, 0, c)
        elif cmd == "T":
            font, x, y, w, s = args
            fg, bg, text = int(s[0]), int(s[1]), s[2:]
            if y == R.MSG_APPEND:
                self.msgs.append(text)
                return
            if y == R.MSG_LINE:
                if self.msgs:
                    self.msgs[-1] = text
                return
            cw = 6 if font in (25, 27, 29) else 8
            width = max(w, len(text) * cw)
            for k in [k for k in self.texts if x + ox <= k[0] < x + ox + width and k[1] == y + oy]:
                del self.texts[k]
            self.rect(x + ox, y + oy, width, 8, bg)
            for i, ch in enumerate(text):
                if ch != " ":
                    for yy in range(y + oy + 1, y + oy + 7):
                        for xx in range(x + ox + i * cw + 1, x + ox + i * cw + cw - 1):
                            if 0 <= xx < 640 and 0 <= yy < 200:
                                self.px[yy * 640 + xx] = self.color(fg)
            self.texts[(x + ox, y + oy)] = (text, fg, bg)
        elif cmd == "B":
            rect, code = args
            if rect == (0, 0, 0, 0):
                self.areas = []
            else:
                self.check_code(code)
                if len(self.areas) >= self.area_limit:
                    raise AssertionError("click area %d of a front end that holds %d" % (len(self.areas) + 1,
                                                                                          self.area_limit))
                self.areas.append((rect, code))
                self.peak["B"] = max(self.peak["B"], len(self.areas))
        elif cmd == "H":
            (x, y, w, h), code = args
            if (x % 1000, y, w, h) == (0, 0, 0, 0):
                self.hots = []
            else:
                self.check_code(code)
                if len(self.hots) >= self.area_limit:
                    raise AssertionError("H area %d of a front end that holds %d" % (len(self.hots) + 1,
                                                                                      self.area_limit))
                self.hots.append(((x % 1000, y, w, h), code))
                self.peak["H"] = max(self.peak["H"], len(self.hots))
        elif cmd == "I":
            x, y, maxlen, filt, s = args
            self.field = (x, y, maxlen, s[2:])
        elif cmd == "D":
            self.entry = args[0] == 0
        elif cmd == "M":
            self.mouse = args[0] == 8
        elif cmd == "L":
            self.scroll = tuple(args)
        elif cmd == "Z":
            self.rect(0, 0, 640, 200, 0)
            self.texts, self.areas, self.hots, self.mouse = {}, [], [], False
        elif cmd == "U":
            slot, n, data = args
            self.macros[slot] = data
        elif cmd in ("P", "Y"):
            slot = args[0]
            dx, dy = (args[1], args[2]) if cmd == "Y" else (0, 0)
            for c2, a2 in R.parse_macro(self.macros.get(slot, b"")):
                self.do(c2, a2, dx, dy)
        elif cmd == "Q":
            self.graphics = False

    @staticmethod
    def check_code(code):
        """A click sends its code three times: +++ is a Hayes modem's escape, which kills the line."""
        if code == "+":
            raise AssertionError("click code +: a click would send +++, a modem's escape")

    # ---- what is shown ----------------------------------------------------------------------------------
    def text_at(self, col, row):
        t = self.texts.get((col * 8, row * 8))
        return t[0] if t else None

    def all_text(self):
        return " | ".join(t[0] for _, t in sorted(self.texts.items(), key=lambda kv: (kv[0][1], kv[0][0])))

    def shows(self, words):
        return words in self.all_text()

    def pixel(self, x, y):
        return self.px[y * 640 + x]

    def area_at(self, x, y, hots=False):
        for (ax, ay, aw, ah), code in (self.hots if hots else self.areas):
            if ax <= x < ax + aw and ay <= y < ay + ah:
                return code
        return None

    # ---- input ------------------------------------------------------------------------------------------
    def click(self, x, y):
        """A click: the code of the first B area there and of the first H area there, as the IBM front end
        sends them."""
        code, hot = self.area_at(x, y), self.area_at(x, y, True)
        if code is None and hot is None:
            raise AssertionError("no click area at %d,%d" % (x, y))
        if self.field is not None:          # a click sends an open field
            self.field = None
            self.s.sendall(b"    \r")
            return
        out = b""
        for c in (code, hot):
            if c is not None:
                out += c.encode("latin-1") * 3 + b"\r"
        self.s.sendall(out)

    def type_line(self, text):
        self.field = None
        self.s.sendall(b"    " + text.encode("ascii") + b"\r")

    def wait_field(self, secs=None):
        return self.until(lambda: self.field is not None, secs, "an input field")

    def render(self, path, scale=2):
        rows = []
        for y in range(200):
            row = bytearray()
            for x in range(640):
                row += bytes(RGB[self.px[y * 640 + x]])
            for _ in range(scale):
                rows.append(b"\x00" + bytes(row))
        raw = b"".join(rows)

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 640, 200 * scale, 8, 2, 0, 0, 0)) + \
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
        with open(path, "wb") as f:
            f.write(png)
