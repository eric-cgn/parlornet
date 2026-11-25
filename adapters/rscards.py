"""RSCARDS extension: ParlorNet's ten games on GEnie's RSCARDS graphics front ends.

RSCARDS (J. Weaver Jr., Factory Programming, 1984-95) was the graphics front end of GEnie's multiplayer card
and board games. The front end is a remote graphics terminal: the host draws every screen with 27 commands
(rectangles, lines, discs, die faces, text, click areas, input fields, macros) and the front end sends back
clicks and typed lines. GEnie's host program is lost; this extension plays its part with screens of its own.
The packets and their encodings are in adapters/rscwire.py.

The line. The front ends speak over a serial port to a modem, so a player reaches the parlor with a WiFi
modem (ATDT host:6520) or a serial-to-TCP bridge, or in an emulator with its modem or serial bridge. A WiFi
modem in telnet mode sends telnet commands (IAC): the extension refuses every option it is offered and takes
the rest of the stream as it is; it sends no IAC of its own. The 8th bit is ignored, so 7-bit parity works.

Terminal mode first. A new connection gets a one-line welcome in plain text and the auto-shift trigger (ENQ ENQ
ENQ 1, a space then going over the 1). The Mac's front end answers FLC n 9 and gets LAUNCH 1 n, which takes it
into graphics mode; one started by hand (the Mac's Command-R), or one that has no terminal (the IBM PC front end
runs after a terminal program dialed), sends its beacon, RSCARDS, a machine code and its version, every few
seconds. The beacon starts graphics mode here whenever
it comes: the reset packet (Z) stops it.

When no FLC comes within --guide seconds, whoever is there gets a short guide (40 columns): run the RSCARDS
program now, or press T for the text games, and the trigger comes again every TRIGGER_AGAIN_SECS and after each
typed line, for a front end loaded since. T (or RETURN twice) hands
the connection to the text games of the telnet service (adapters/telnet.py), run here on the same line, so a text
terminal need not connect again; a line that does not speak telnet gets none of its telnet commands. The guide
comes once more after --guide-again seconds; a connection that never goes into graphics mode or to the text
games closes after TERMINAL_SECS.

Graphics mode. Packets go out windowed: up to --window bytes (64) are unacknowledged at a time, and a NAK sends
again everything not yet acknowledged (the front end throws away whatever follows a bad packet until 5 seconds
of silence, then sends the NAK); a NAK with nothing waiting means the screen is in doubt, and it is drawn whole
again. Two identical packets are never sent in a row, since the front end would take
the second for a repeat and not draw it. Click areas go out after the drawing, when nothing else waits, so a
board shows first and becomes clickable a moment later.

Screens are drawn from scenes: each screen describes what should be on the front end as a base (drawn once:
the background, the board's static parts, labels) and items (each with the packets that draw it and those
that erase it). Only items that changed are sent. The message area (rows 20-23) carries the table's events and
chat; the entry line (row 24) takes chat and the shared chat commands (adapters/chatcmd.py). Click areas are
given codes as they are needed. The front ends hold few click areas: the Mac front end 30 of each kind (a 31st
takes the place of the first), the IBM front end 40 (a 41st overwrites its memory and hangs it). So no screen
defines more than 30 of each kind, and boards are grids: a B area per column and an H area per row, which the
front end reports together for one click (Session.pair).

Signing in happens in graphics mode, in two input fields: the name, then the password, which is typed into a
field drawn in one color so it does not show. The name alone never signs in where the parlor uses passwords
(docs/accounts.md); a new name is offered a sign-up, as on telnet. The lobby, the games and their screens are in
adapters/rscgames.py.

    python3 -m adapters.rscards --core 127.0.0.1:6590 --key rscards.key --port 6520
"""
import argparse
import asyncio
import itertools
import os
import re
import sys
import time
from collections import deque

from . import declare
from . import rscwire as R
from .common import CoreClient, CoreError, Gate, keepalive, no_wait, peer_addr, sign_in_lines

PROTOCOL = ("RSCARDS", "RSCARDS, for GEnie's graphics front ends",
            "Ten games for GEnie's RSCARDS front ends, by modem")
PORT = 6520
MAX_SESSIONS = 40
WINDOW = 64                # bytes sent and not yet acknowledged, at most: the IBM front end loses input beyond about 120
ACK_SECS = 15.0             # nothing acknowledged this long: send again what waits
HOLD_SECS = 2.0             # nothing acknowledged this long: nothing new goes out (a front end that lost a packet
                            # sends its NAK only after 5 seconds of silence)
GUIDE_SECS = 12.0           # no FLC this long after the trigger: the guide for whoever is there (a Mac answers in 5-8)
GUIDE_AGAIN_SECS = 120.0    # the guide once more after this long
TRIGGER_AGAIN_SECS = 15.0   # the trigger again this often while no front end has answered: one loaded after a
                            # terminal program dialed is in terminal mode only later
LAUNCH_SECS = 20.0          # LAUNCH sent and no beacon this long: the trigger again
TERMINAL_SECS = 600.0       # a connection that never goes into graphics mode is closed after this long
SIGNIN_SECS = 600.0         # to sign in after graphics mode starts
IDLE_SECS = 3 * 3600.0      # nothing from the player this long: the connection closes
TEXT_MAX = 62                # characters in one T packet (64 bytes with the two color digits)
NOOP = R.play(63)           # macro 63 is never defined: a packet that does nothing

IAC, SE, SB, WILL, WONT, DO, DONT = 255, 240, 250, 251, 252, 253, 254
ENQ, ACK, NAK = 0x05, 0x06, 0x15
TRIGGER = b"\x05\x05\x051"
# the trigger as sent: its 1 shows on a plain terminal, so a space goes over it (no backspace, which not every
# terminal takes)
TRIGGER_LINE = b"\r" + TRIGGER + b"\r \r"

# the machine codes of the beacon and the RSCARDS General Instructions: name, a mono screen
MACHINES = {1: ("terminal", True), 2: ("Atari ST", False), 3: ("Atari ST", True), 4: ("IBM PC", False),
            5: ("IBM PC", True), 6: ("Amiga", False), 7: ("Macintosh", False), 8: ("Macintosh", True),
            9: ("Apple IIgs", False), 10: ("Commodore 128", False), 11: ("Commodore 128", True)}
BEACON_RE = re.compile(r"RSCARDS(.)v(\d\d)\.(\d\d)(\d)")
FLC_RE = re.compile(r"FLC (\d{1,5}) (\d{1,3})\Z")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
# click-area codes: the printable bytes but + (and ~, which ends a string). A click sends its code three times,
# and a Hayes modem takes +++ after a second of quiet for its escape: it goes to command mode, answers OK (which
# the front end takes for a bad packet) and swallows everything the front end sends after it, so the line is dead
CODES = [chr(c) for c in range(0x21, 0x7E) if chr(c) != "+"]
AREA_MAX = 30               # click areas of each kind (B, H) the Mac front end holds; the IBM's holds 40
# click areas of each kind the front ends hold where it is fewer than AREA_MAX: the Commodore 128's 29 B areas (a
# 30th runs into its table of H areas, and a clear then corrupts its memory)
AREA_LIMITS = {10: {"B": 29, "H": 30}, 11: {"B": 29, "H": 30}}

WELCOME = "ParlorNet: GEnie's RSCARDS games."
GUIDE = ("This is ParlorNet's RSCARDS service, for",      # 40 columns at most: a Commodore's screen
         "GEnie's RSCARDS graphics programs.",
         "",
         "With RSCARDS: run it now.",
         " IBM PC: go to DOS and type RSCARDS 1",
         "  (or the number of your COM port).",
         " Mac: File > RSCARDS (Command-R).",
         "",
         "Without it: press T for the text games",
         "here, or telnet to port 6502 next time.")
ids = itertools.count(1)


def log(sid, text):
    print("rscards %d: %s" % (sid, text), file=sys.stderr, flush=True)


def trace(text):
    """--debug: a line of the packet trace, with the time (seconds, to the millisecond)."""
    print("rscards %.3f %s" % (time.monotonic(), text), file=sys.stderr, flush=True)


def version_text(major, minor, patch):
    """The beacon's 05.02 0 -> '5.02', 05.00 0 -> '5.0', 04.01 0 -> '4.01'."""
    m = str(int(major))
    v = minor.rstrip("0") if patch == "0" else minor + patch
    return "%s.%s" % (m, v or "0")


# ---- the line: telnet, ACK and NAK, packets out ------------------------------------------------------------

class Link:
    """The serial line as a TCP stream: reads the front end's bytes into events and sends packets, windowed,
    with retransmission. Events: ("beacon", code, version), ("flc", n), ("exit",), ("click", ch),
    ("line", text) for a typed line or an input field (in graphics mode), ("text", line) in terminal mode,
    ("gone",)."""

    def __init__(self, reader, writer, window=WINDOW):
        self.r, self.w = reader, writer
        self.window = window
        self.events = asyncio.Queue()
        self.fg, self.bg = deque(), deque()     # packets to send; bg only when fg is empty
        self.inflight = deque()
        self.last = None                        # the last packet written
        self.graphics = False
        self.telnet = False                     # an IAC came: the other end speaks telnet
        self.tstate, self.tcmd = 0, 0
        self.answered = set()
        self.buf = bytearray()
        self.wake = asyncio.Event()
        self.drained = asyncio.Event()
        self.drained.set()
        self.progress = time.monotonic()
        self.heard = time.monotonic()
        self.closed = False
        self.sent_bytes = self.sent_packets = self.naks = self.resent = 0
        self.tasks = []
        self.debug = False
        self.on_desync = None       # called when a NAK comes with nothing waiting for its acknowledgment
        self.back = deque()         # events put back (Session.pair)

    def start(self):
        self.tasks = [asyncio.ensure_future(self._reader()), asyncio.ensure_future(self._sender()),
                      asyncio.ensure_future(self._timer())]

    def stop(self):
        for t in self.tasks:
            t.cancel()
        self.closed = True

    # ---- in -----------------------------------------------------------------------------------------------
    async def _reader(self):
        try:
            while True:
                data = await self.r.read(1024)
                if not data:
                    break
                self.heard = time.monotonic()
                if self.debug and self.graphics:
                    trace("in: %r" % data)
                self.feed(data)
        except (ConnectionError, OSError):
            pass
        self.closed = True
        self.wake.set()
        await self.events.put(("gone",))

    def feed(self, data):
        for x in data:
            if self.tstate:
                self._telnet(x)
            elif x == IAC:
                self.telnet = True
                self.tstate = 1
            else:
                self._byte(x & 0x7F)

    def _telnet(self, x):
        """Telnet commands from a WiFi modem in telnet mode: every option is refused, once."""
        if self.tstate == 1:
            if x in (WILL, WONT, DO, DONT):
                self.tcmd, self.tstate = x, 2
            elif x == SB:
                self.tstate = 3
            else:
                self.tstate = 0             # IAC IAC (a data 255) and the two-byte commands: dropped
        elif self.tstate == 2:
            self.tstate = 0
            if self.tcmd in (WILL, DO) and (self.tcmd, x) not in self.answered:
                self.answered.add((self.tcmd, x))
                self.w.write(bytes([IAC, DONT if self.tcmd == WILL else WONT, x]))
                if self.debug:
                    trace("telnet answer: %d %d" % (self.tcmd, x))
        elif self.tstate == 3:
            if x == IAC:
                self.tstate = 4
        elif self.tstate == 4:
            self.tstate = 0 if x == SE else 3

    def _byte(self, x):
        if x == NAK and self.debug:
            trace("in: NAK (%d in flight)" % len(self.inflight))
        if x == ACK or x == NAK:
            if self.graphics:
                self._ack(x == ACK)
            return
        if x == 13:
            line = self.buf.decode("ascii", "replace")
            self.buf = bytearray()
            self._line(line)
        elif x in (10, 0):
            return
        elif len(self.buf) < 200:
            if not self.graphics and 0x20 < x < 0x7F:
                self.events.put_nowait(("key", chr(x), not self.buf))     # terminal mode: each key, and whether
            self.buf.append(x)                                            # it starts a line

    def _line(self, line):
        m = BEACON_RE.search(line)
        if m:
            self.events.put_nowait(("beacon", ord(m.group(1)) - 0x20, version_text(*m.group(2, 3, 4))))
            return
        if not self.graphics:
            m = FLC_RE.search(line)
            if m:
                self.events.put_nowait(("flc", int(m.group(1))))
            else:
                self.events.put_nowait(("text", line.strip()))
            return
        if line.strip() == "EXIT":
            self.events.put_nowait(("exit",))
        elif line.startswith("    ") or (line.startswith(" ") and not line.strip()):
            self.events.put_nowait(("line", line[4:]))
        elif len(line) == 3 and line[0] == line[1] == line[2]:
            self.events.put_nowait(("click", line[0]))
        elif line.strip():
            self.events.put_nowait(("line", line.strip()))

    # ---- out ----------------------------------------------------------------------------------------------
    def write_text(self, text):
        """Terminal mode: plain text, lines ending in CR LF."""
        self.w.write(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii", "replace"))

    def send(self, *packets, background=False):
        """Queue packets: each must be one whole packet (its acknowledgment is counted)."""
        q = self.bg if background else self.fg
        for p in packets:
            if p:
                q.extend(R.split(p))
        self.drained.clear()
        self.wake.set()

    def drop_background(self):
        self.bg.clear()

    def _ack(self, ok):
        if not self.inflight:
            if not ok and self.graphics:        # a packet we took for acknowledged was not: draw it all again
                self.naks += 1
                self.fg.appendleft(NOOP)
                if self.on_desync is not None:
                    self.on_desync()
                self.wake.set()
            return
        self.progress = time.monotonic()
        if ok:
            self.inflight.popleft()
        else:
            self.naks += 1
            self._again()
        self.wake.set()

    def _again(self):
        self.resent += len(self.inflight)
        self.fg.extendleft(reversed(self.inflight))
        self.inflight.clear()
        self.last = None

    async def _sender(self):
        try:
            while not self.closed:
                await self.wake.wait()
                self.wake.clear()
                wrote = False
                while (self.fg or self.bg) and not self.closed:
                    q = self.fg or self.bg
                    p = q[0]
                    if self.inflight and (sum(map(len, self.inflight)) + len(p) > self.window
                                          or time.monotonic() - self.progress > HOLD_SECS):
                        break           # the window is full, or the front end is quiet: let it reach its NAK
                    q.popleft()
                    if not self.inflight:
                        self.progress = time.monotonic()
                    if p == self.last:      # the front end would take it for a repeat: something between
                        self._write(NOOP)
                    self._write(p)
                    wrote = True
                if wrote:
                    await self.w.drain()
                if not (self.fg or self.bg or self.inflight):
                    self.drained.set()
        except (ConnectionError, OSError):
            self.closed = True
            self.drained.set()

    def _write(self, p):
        if self.debug:
            trace("out: %r (%d in flight)" % (p, sum(map(len, self.inflight))))
        self.w.write(p)
        self.inflight.append(p)
        self.last = p
        self.sent_bytes += len(p)
        self.sent_packets += 1

    async def _timer(self):
        while not self.closed:
            await asyncio.sleep(1.0)
            if self.inflight and time.monotonic() - self.progress > ACK_SECS:
                self.progress = time.monotonic()
                self._again()
                self.wake.set()

    async def flush(self, timeout=None):
        """Wait until everything sent has been acknowledged."""
        if timeout is None:
            await self.drained.wait()
        else:
            await asyncio.wait_for(self.drained.wait(), timeout)

    def put_back(self, ev):
        """An event read too early: the next next_event returns it."""
        self.back.append(ev)

    async def next_event(self, timeout=None):
        if self.back:
            return self.back.popleft()
        try:
            return await asyncio.wait_for(self.events.get(), timeout)
        except asyncio.TimeoutError:
            return None


# ---- scenes and the display -----------------------------------------------------------------------------

class Scene:
    """What one screen should show: a base, drawn whole when it changes (base_key), items drawn when they
    change and erased when they go, click areas (rect, meaning) in order of importance, and macros the base or
    the items play."""

    def __init__(self, base_key, base=(), scroll=None, entry=True):
        self.base_key = base_key
        self.base = [p for p in base if p]
        self.items = {}
        self.areas = []
        self.macros = {}
        self.scroll = scroll        # (first row, rows, color) of the message area, or None
        self.entry = entry

    def add(self, key, draw, erase=b""):
        """An item: the packets that draw it and those that erase it (a packet, packets run together, or a list
        of either); kept one packet each, so the line counts each one's acknowledgment."""
        def packets(x):
            x = b"".join(x) if isinstance(x, (list, tuple)) else x
            return tuple(R.split(x)) if x else ()
        self.items[key] = (packets(draw), packets(erase))

    def area(self, x, y, w, h, meaning, kind="B"):
        """A click area. kind B is an ordinary one; H is one of the second kind, which the front end highlights
        (a one-pixel frame) while the pointer is in it. A click in a B area and an H area at once sends both
        codes, so a grid of B columns over H rows tells the square from one click."""
        self.areas.append(((x, y, w, h), meaning, kind))

    def macro(self, slot, data):
        self.macros[slot] = data


class Display:
    """What the front end shows now, and the packets that take it to a new scene."""

    def __init__(self, link, mono=False):
        self.link, self.mono = link, mono
        self.base_key = None
        self.items = {}
        self.macros = {}
        self.codes = {}             # code -> meaning, for the areas defined since the last clear
        self.defined = {}           # meaning -> code
        self.count = {"B": 0, "H": 0}
        self.limits = {"B": AREA_MAX, "H": AREA_MAX}    # click areas of each kind this front end holds
        self.log = deque(maxlen=4)  # the message area's lines, drawn again with a new base
        self.scroll = None
        self.entry = None
        self.mouse = False
        self.msg_colors = (R.WHITE, R.BLACK if mono else R.BLUE)

    def invalidate(self):
        """The front end lost something: the next scene is drawn whole."""
        self.base_key = None
        self.mouse = False
        self.entry = None

    def reset(self):
        """After Z: nothing is shown, no area is defined; the macros stay (they survive Z)."""
        self.base_key, self.items, self.codes, self.defined = None, {}, {}, {}
        self.count = {"B": 0, "H": 0}
        self.scroll = self.entry = None
        self.mouse = False

    def show(self, scene):
        out, later = [], []
        for slot, data in scene.macros.items():
            if self.macros.get(slot) != data:
                out.append(R.macro(slot, data))
                self.macros[slot] = data
        new_base = scene.base_key != self.base_key
        if new_base:
            self.link.drop_background()
            out += [R.area(0, 0, 0, 0, "!"), R.hot(0, 0, 0, 0, "!")]       # the old screen's click areas go
            self.codes, self.defined, self.count = {}, {}, {"B": 0, "H": 0}
            out += scene.base
            self.base_key, self.items = scene.base_key, {}
            if scene.scroll != self.scroll:         # the message area keeps its lines from screen to screen
                self.scroll = scene.scroll
                if scene.scroll:
                    first, rows, color = scene.scroll
                    out.append(R.scroll_area(first, rows, color))
                    out += [self.say_packet(l) for l in self.log]
        for k in [k for k in self.items if k not in scene.items]:
            out.extend(self.items[k][1])
        for k, v in scene.items.items():
            if self.items.get(k) != v:
                out.extend(v[0])
        self.items = dict(scene.items)
        if scene.entry != self.entry:
            out.append(R.entry_line(scene.entry))
            self.entry = scene.entry
        if not self.mouse:
            out.append(R.mouse(True))
            self.mouse = True
        later = self.area_packets(scene.areas)
        out = coalesce(out)
        self.link.send(*out)
        if later is not None:
            self.link.send(*later, background=True)
        return out, later

    def area_packets(self, areas):
        """The B and H packets for areas not yet defined; after a clear, for all of them, when the codes or the
        front end's room for areas (self.limits) run out. Areas already defined stay defined even when
        the scene no longer has them: a click there comes back with its old meaning, which the screen ignores
        or explains."""
        seen, uniq = set(), []
        for a in areas:
            if a[1] not in seen:
                seen.add(a[1])
                uniq.append(a)
        areas = [a for a in uniq if a[2] == "B"][:self.limits["B"]] + [a for a in uniq if a[2] == "H"][:self.limits["H"]]
        want = [a for a in areas if a[1] not in self.defined]
        if self.link.debug and want:
            print("rscards areas: %d B, %d H wanted of %d" % (sum(a[2] == "B" for a in want), sum(a[2] == "H" for a in want),
                                                            len(areas)), file=sys.stderr, flush=True)
        if not want:
            return None
        out = []
        need = {k: sum(1 for a in want if a[2] == k) for k in "BH"}
        if any(self.count[k] + need[k] > self.limits[k] for k in "BH") or len(self.codes) + len(want) > len(CODES):
            self.link.drop_background()
            out += [R.area(0, 0, 0, 0, "!"), R.hot(0, 0, 0, 0, "!")]
            self.codes, self.defined, self.count = {}, {}, {"B": 0, "H": 0}
            want = areas
        free = [c for c in CODES if c not in self.codes]
        for (rect, meaning, kind), code in zip(want, free):
            self.codes[code] = meaning
            self.defined[meaning] = code
            self.count[kind] += 1
            out.append(R.area(*rect, code) if kind == "B" else R.hot(*rect, code, frame=1))
        out.append(R.mouse(True))
        return out

    def meaning(self, code):
        return self.codes.get(code)

    def say_packet(self, text):
        fg, bg = self.msg_colors
        return R.text(0, R.MSG_APPEND, text[:TEXT_MAX], fg, bg)

    def say(self, lines):
        if isinstance(lines, str):
            lines = [lines]
        out = []
        for l in wrap_lines(lines, TEXT_MAX):
            self.log.append(l)
            out.append(self.say_packet(l))
        self.link.send(*out)


def coalesce(packets):
    """Packets with each run of fills that join side by side or one above the other (same color, same rows or
    the same columns) made into one fill: a crater's columns or a row of erased squares cost one packet."""
    out, last = [], None
    for p in packets:
        if p[:1] == b"A" and len(p) == 12:
            (x, y, w, h), c = R.decode("r", p[1:9]), p[9] - 0x20
            if last is not None and (x, y, w, h) != (0, 0, 0, 0):
                lx, ly, lw, lh, lc = last
                if lc == c and ly == y and lh == h and lx + lw == x:
                    last = (lx, ly, lw + w, lh, c)
                    out[-1] = R.fill(*last)
                    continue
                if lc == c and lx == x and lw == w and ly + lh == y:
                    last = (lx, ly, lw, lh + h, c)
                    out[-1] = R.fill(*last)
                    continue
            last = (x, y, w, h, c) if (x, y, w, h) != (0, 0, 0, 0) else None
        else:
            last = None
        out.append(p)
    return out


def wrap_lines(lines, width):
    out = []
    for l in lines:
        l = R.clean_text(l, 400)
        while len(l) > width:
            cut = l.rfind(" ", 0, width + 1)
            cut = cut if cut > 0 else width
            out.append(l[:cut])
            l = l[cut:].lstrip()
        out.append(l)
    return out


# ---- the session --------------------------------------------------------------------------------------------

class Session:
    def __init__(self, svc, reader, writer, ticket=None):
        self.svc, self.ticket = svc, ticket
        self.sid = next(ids)
        self.addr = peer_addr(writer)
        self.link = Link(reader, writer, svc.window)
        self.link.debug = svc.debug
        self.w = writer
        self.display = None
        self.core = None
        self.name = None
        self.machine, self.mono, self.version = "", False, ""
        self.screen = None
        self.closing = False
        self.t0 = time.monotonic()

    def claim_text(self):
        return ("RSCARDS %s" % self.version).strip(), self.machine

    async def run(self):
        self.link.start()
        try:
            if await self.terminal():
                await self.graphics()
        finally:
            await self.finish()

    async def finish(self):
        if self.screen is not None:
            try:
                await self.screen.leave()
            except Exception:
                pass
        if self.core is not None:
            await self.core.finish()
        if self.ticket is not None:
            self.ticket.release()
        self.link.stop()
        try:
            self.w.close()
        except Exception:
            pass

    # ---- terminal mode ---------------------------------------------------------------------------------------
    async def terminal(self):
        """Terminal mode (the module's docstring) -> True to go on in graphics mode."""
        self.link.graphics = False
        self.link.write_text("\n%s\n" % WELCOME)
        self.link.w.write(TRIGGER_LINE)
        triggered = sent = time.monotonic()
        launched = guided = None
        guides = blanks = 0
        while not self.link.closed:
            now = time.monotonic()
            if now - self.t0 > TERMINAL_SECS:
                self.link.write_text("\nNo RSCARDS program answered; goodbye.\n")
                return False
            if launched is not None and now - launched > LAUNCH_SECS:
                launched, triggered = None, now          # launched, and no beacon came: try once more
                self.link.w.write(TRIGGER_LINE)
                sent = now
            if launched is None and guides and now - sent > self.svc.trigger_again:
                self.link.w.write(TRIGGER_LINE)          # a front end loaded since may be listening now
                sent = now
            if launched is None and (guides == 0 and now - triggered > self.svc.guide
                                     or guides == 1 and now - guided > self.svc.guide_again):
                self.link.write_text("\n" + "\n".join(GUIDE) + "\n")
                guides, guided = guides + 1, now
            ev = await self.link.next_event(1.0)
            if ev is None:
                continue
            kind = ev[0]
            if kind == "gone":
                return False
            if kind == "flc":
                self.link.w.write(b"LAUNCH 1 %d\r\n" % ev[1])
                launched = now
            elif kind == "beacon":
                self.start_graphics(ev[1], ev[2])
                return True
            elif kind == "key" and guides and ev[1] in "Tt" and ev[2]:
                await self.to_text()
                return False
            elif kind == "text":
                blanks = blanks + 1 if not ev[1] else 0
                if ev[1].lower() == "t" or (guides and blanks >= 2):
                    await self.to_text()
                    return False
                if not guides and ev[1]:            # someone typing: the guide at once
                    triggered = now - self.svc.guide
                elif ev[1] and launched is None:    # and the trigger, in case a front end is listening now
                    self.link.w.write(TRIGGER_LINE)
                    sent = now

    async def to_text(self):
        """The text games: the telnet service's session (adapters/telnet.py), run on this connection."""
        from . import telnet as T
        log(self.sid, "to the text games (%s)" % ("telnet" if self.link.telnet else "plain"))
        self.link.stop()
        await asyncio.gather(*self.link.tasks, return_exceptions=True)     # the line's reader lets go first
        io = T.TelnetIO(self.link.r, self.w if self.link.telnet else PlainWriter(self.w))
        io.write("\nParlorNet's text games.\n")
        try:
            await T.main_session(io, self.svc.args, self.ticket, title_first=False)   # a 40-column line: no title
        except (EOFError, ConnectionError, asyncio.TimeoutError, CoreError, OSError):
            pass

    def start_graphics(self, code, version):
        self.machine, self.mono = MACHINES.get(code, ("", False))
        self.version = version
        self.link.graphics = True
        self.display = Display(self.link, self.mono)
        if code in AREA_LIMITS:
            self.display.limits = dict(AREA_LIMITS[code])
        self.link.send(R.reset())
        self.display.reset()
        self.link.on_desync = self.display.invalidate
        log(self.sid, "graphics: machine %d (%s%s), RSCARDS %s" % (code, self.machine or "?",
                                                                 ", mono" if self.mono else "", version))

    # ---- graphics mode --------------------------------------------------------------------------------------
    async def graphics(self):
        from . import rscgames as G
        self.G = G
        if not await self.sign_in():
            return
        await self.go(G.Lobby(self))
        last_poll = 0.0
        while not self.closing and not self.link.closed:
            self.display.show(self.screen.scene())
            now = time.monotonic()
            wait = max(0.05, last_poll + self.screen.poll_every - now)
            ev = await self.link.next_event(wait)
            if ev is not None and self.svc.debug:
                log(self.sid, "event %r" % (ev,))
            if ev is not None:
                if ev[0] == "gone":
                    break
                if ev[0] in ("click", "line"):
                    self.core.user_input()
                    self.last_input = time.monotonic()
                await self.event(ev)
            if self.closing:
                break
            if time.monotonic() - last_poll >= self.screen.poll_every:
                last_poll = time.monotonic()
                await self.screen.poll()
            if time.monotonic() - self.last_input > IDLE_SECS:
                self.display.say("Nothing happened for a long time; goodbye.")
                break
        if not self.link.closed:
            self.link.send(R.leave())
            try:
                await self.link.flush(10)
            except asyncio.TimeoutError:
                pass

    async def event(self, ev):
        kind = ev[0]
        if kind == "click":
            meaning = self.display.meaning(ev[1])
            if self.svc.debug:
                log(self.sid, "click %r: %r" % (ev[1], meaning))
            if meaning is not None and meaning[0] in ("col", "row"):
                meaning = await self.pair(meaning)
            if meaning is not None:
                await self.screen.click(meaning)
        elif kind == "line":
            text = ev[1].strip()
            if text:
                await self.screen.typed(text)
        elif kind == "exit":            # File > Terminal on the front end: back to terminal mode
            log(self.sid, "the front end left graphics mode")
            self.closing = True

    async def pair(self, first):
        """A click on a grid sends its column's code and its row's (a B and an H area): the two together are the
        square ("cell", grid, column, row). A lone one is the column or the row alone."""
        nxt = await self.link.next_event(0.6)
        if nxt is not None and nxt[0] == "click":
            m = self.display.meaning(nxt[1])
            if m is not None and m[0] in ("col", "row") and m[0] != first[0] and m[1] == first[1]:
                col, row = (first, m) if first[0] == "col" else (m, first)
                return ("cell", col[1], col[2], row[2])
        if nxt is not None:
            self.link.put_back(nxt)
        return first

    async def go(self, screen):
        if self.screen is not None:
            await self.screen.leave()
        self.screen = screen
        await screen.enter()

    def say(self, lines):
        self.display.say(lines)

    async def req(self, text):
        return await self.core.req(text)

    # ---- signing in -------------------------------------------------------------------------------------------
    async def ask(self, label, secret=False, note=()):
        """An input field on the sign-in screen -> the text typed ("" if none)."""
        self.display.show(self.G.signin_scene(self, label, note))
        fg, bg = (R.WHITE, R.WHITE) if self.mono and secret else (R.BLUE, R.BLUE) if secret else \
            ((R.BLACK, R.WHITE) if self.mono else (R.YELLOW, R.BLUE))
        x, y = self.G.FIELD_AT
        self.link.send(R.fill(x, y, 41 * 8, 8, R.BLACK), R.field(x, y, 40, "", fg, bg))
        end = time.monotonic() + SIGNIN_SECS
        while time.monotonic() < end:
            ev = await self.link.next_event(end - time.monotonic())
            if ev is None or ev[0] == "gone":
                raise EOFError
            if ev[0] == "line":
                if self.ticket is not None:
                    self.ticket.touch()
                return ev[1].strip()
            if ev[0] == "exit":
                raise EOFError
        raise EOFError

    async def sign_in(self):
        """The sign-in fields -> True when signed in."""
        self.last_input = time.monotonic()
        note = ()
        try:
            for _ in range(3):
                name = await self.ask("Your ParlorNet name:", note=note)
                if not NAME_RE.match(name):
                    note = ("Names are 1 to 12 letters, digits, - and _.",) if name else ()
                    continue
                if self.core is None:
                    try:
                        self.core = await CoreClient.connect(*self.svc.core_addr)
                        await self.core.source(self.addr)
                        await self.core.claim(*self.claim_text())
                    except (OSError, asyncio.TimeoutError, CoreError) as e:
                        log(self.sid, "the core is not answering: %s" % e)
                        self.core = None
                        await self.ask("The game server is not answering. Try again soon.", note=())
                        return False
                shown = []

                async def show(lines):
                    shown.extend(lines)

                async def prompt(label, secret):
                    lines = list(shown)
                    del shown[:]
                    return await self.ask(label + ("?" if label.endswith(("account", "name")) else ":"),
                                          secret, lines)
                why = await sign_in_lines(self.core, name, None, prompt, show, "any")
                if why is None:
                    if self.ticket is not None and not self.ticket.sign_in():
                        await self.ask("The server is full. Try again soon.")
                        return False
                    self.name = self.core.name or name
                    log(self.sid, "signed in as %s" % self.name)
                    return True
                log(self.sid, "sign-in refused")
                note = tuple(shown) + (no_wait(why),)
            return False
        except EOFError:
            return False


class PlainWriter:
    """A writer for a line that does not speak telnet (a serial terminal behind a modem): the telnet commands the
    text games send are left out (IAC IAC, a data byte 255, goes through as one byte)."""

    def __init__(self, w):
        self.w, self.state = w, 0

    def write(self, data):
        out = bytearray()
        for x in data:
            if self.state == 0:
                if x == IAC:
                    self.state = 1
                else:
                    out.append(x)
            elif self.state == 1:
                if x == IAC:
                    out.append(x)
                    self.state = 0
                else:
                    self.state = {WILL: 2, WONT: 2, DO: 2, DONT: 2, SB: 3}.get(x, 0)
            elif self.state == 2:
                self.state = 0
            elif self.state == 3:
                if x == IAC:
                    self.state = 4
            else:
                self.state = 0 if x == SE else 3
        self.w.write(bytes(out))

    def __getattr__(self, name):
        return getattr(self.w, name)


class Service:
    def __init__(self, args):
        self.args = args
        self.core_addr = (args.core_host, args.core_port)
        self.window = args.window
        self.guide, self.guide_again, self.trigger_again = args.guide, args.guide_again, args.trigger_again
        self.turn = args.turn
        self.poll = args.poll
        self.debug = args.debug


async def serve(args):
    svc = Service(args)
    gate = Gate(MAX_SESSIONS)
    gate.notice = b"\r\nThe server is full; try again soon.\r\n"

    async def handle(reader, writer):
        ticket = gate.admit(peer_addr(writer), writer)
        if ticket is None:
            writer.close()
            return
        keepalive(writer)
        s = Session(svc, reader, writer, ticket)
        log(s.sid, "connect")
        try:
            await s.run()
        except (EOFError, ConnectionError, asyncio.TimeoutError, CoreError, OSError) as e:
            log(s.sid, "ended: %s" % (e or type(e).__name__))
        except Exception as e:
            log(s.sid, "error: %r" % e)
            import traceback
            traceback.print_exc()
        log(s.sid, "closed (%d bytes in %d packets, %d NAKs)" % (s.link.sent_bytes, s.link.sent_packets,
                                                                  s.link.naks))

    server = await asyncio.start_server(handle, args.host, args.port, limit=4096)
    declare.start(args, PROTOCOL)
    print("rscards adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.rscards")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    ap.add_argument("--turn", default="1h", help="time per turn at the tables this extension makes")
    ap.add_argument("--window", type=int, default=int(os.environ.get("PARLOR_RSCARDS_WINDOW") or WINDOW),
                    help="bytes sent and not yet acknowledged, at most (1: one packet at a time)")
    declare.add_args(ap)
    ap.add_argument("--guide", type=float, default=GUIDE_SECS, help="seconds after the trigger with no FLC: the "
                    "guide for a text terminal")
    ap.add_argument("--guide-again", type=float, default=GUIDE_AGAIN_SECS, help=argparse.SUPPRESS)  # tests
    ap.add_argument("--trigger-again", type=float, default=TRIGGER_AGAIN_SECS, help=argparse.SUPPRESS)  # tests
    ap.add_argument("--poll", type=float, default=1.5, help=argparse.SUPPRESS)                 # tests
    ap.add_argument("--debug", action="store_true", default=bool(os.environ.get("PARLOR_RSCARDS_DEBUG")),
                    help="log the clicks")
    a = ap.parse_args(argv)
    if a.key:
        CoreClient.key_file = a.key
    a.core_host, _, p = a.core.rpartition(":")
    a.core_port = int(p)
    return a


if __name__ == "__main__":
    try:
        asyncio.run(serve(parse_args()))
    except KeyboardInterrupt:
        pass
