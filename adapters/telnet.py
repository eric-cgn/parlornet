"""Telnet adapter: one telnet server for every game on the parlord behind it.

A client of the native protocol (adapters/common.py). The screen is 80x24.
Display modes, picked from the terminal type the client reports (telnet
TTYPE) and confirmed in a menu:

    pc     ANSI color and IBM PC (CP437) characters (the default)
    utf8   ANSI color and the same characters in Unicode (xterm and friends)
    ansi   ANSI color, ASCII only (VT100 class)
    plain  no escape codes; line by line, moves are typed

The ANSI modes are full screen: a menu and a board driven by the arrow keys,
Enter and single keys listed in a status bar. Terminals that report the mouse
(xterm and friends: DECSET 1000 with SGR 1006 coordinates) can click too. Screens are composed as a grid
of cells and only the cells that changed are sent, so a cursor move or a
clock tick costs a few bytes (usable at 2400 baud). No line feed is sent and
the last cell of the screen is never written, so the screen never scrolls.
Typed commands (join 1, say hi, d3 ...) still work: a hotkey letter followed
at once by more typing counts as typing.

Movement keys: terminals that send no arrow keys (or lose them) can move the
cursor with letters instead, a choice of the session (O in the menu, or K at
the login's last prompt): IJKL, ESDF, WASD or HJKL (vi). The arrows work in
every choice. On an empty line the four letters, either case, move the cursor
in the menu, the pickers and every board, and never act as hotkeys. `:` opens
the command line: there every letter types, and `:` then a hotkey letter does
what that key does (:d for D in WASD). The choice is kept with the player
(the core's per-extension data, PRF) and comes back at the next sign-in.

Before the login prompt it shows the server's message of the day. At the
prompt a name signs in; "new" makes a new account (the name wanted, then the
server's sign-up steps). A name with no account says so and points to "new".

    python3 -m adapters.telnet --core 127.0.0.1:6501 --port 6502 --host 0.0.0.0
"""
import argparse
import asyncio
import contextvars
import re
import sys
import textwrap
import time
import traceback

from . import declare
from .common import (framed_motd, LOGIN_SECS, LOGIN_TOTAL_SECS, CLAIM_LEN, CoreClient, CoreError, Gate, claim_text,
                     keepalive, NeedAuth,
                     no_wait, describe_invite_event, err_text, group_games, parse_games, parse_invites,
                     parse_reversi_view, parse_players, parse_tables, peer_addr, MOVE_KEYS_PREF, pending_invites,
                     describe_racks_event, racks_pending, parse_profile, parse_turns, turn_sentence, turn_time)
from .common import SCORES_NOTE, parse_top, score_text
from .common import CUT_ENDS, fit, parse_client, parse_clients
from .chatcmd import note_lines, time_text
from . import botflag
from . import games
from .webstyle import TAGLINE

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
ECHO, SGA, TTYPE, NAWS = 1, 3, 24, 31
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
SQUARE_RE = re.compile(r"^[a-z]\d{1,2}\Z")
TURN_RE = re.compile(r"^\d+[smhd]?\Z", re.I)
PLAYERS_RE = re.compile(r"^(?:players=)?(\d{1,2})p\Z", re.I)    # a seat count typed after new: 3p
MAX_SESSIONS = 60
IDLE_SECS = 1800
COLS, ROWS = 80, 24
HOTKEY_WAIT = 0.3       # a hotkey letter followed this quickly by more typing is typing
POLL_SECS = 2.0         # game screen: ask the core for new events this often
MENU_SECS = 15.0        # menu: reload the table list this often
CLEAR = "\x1b[0m\x1b[2J\x1b[H"
MODES = {"pc": "IBM PC characters, ANSI color", "utf8": "Unicode characters, ANSI color",
         "ansi": "ASCII characters, ANSI color", "plain": "plain text, no color, typed moves"}
ORDER = ["pc", "utf8", "ansi", "plain"]
CHARSET = {"pc": "cp437", "utf8": "utf-8", "ansi": "ascii", "plain": "ascii"}
SAMPLE = {"pc": "\x1b[30;42m▐█▌\x1b[1;37;42m▐▓▌\x1b[0m"
                " ╔═╤═╗ ░▒▓",
          "ansi": "\x1b[30;42m X \x1b[1;37;42m O \x1b[0m +-+-+", "plain": "X O +-+-+"}
TURNS = [("5m", "5 minutes"), ("15m", "15 minutes"), ("1h", "1 hour"), ("1d", "1 day"),
         ("3d", "3 days"), ("5d", "5 days")]
TURN_DEFAULT = 3
MOUSE_ON = "\x1b[?1000h\x1b[?1006h"     # report clicks, with SGR (extended) coordinates
MOUSE_OFF = "\x1b[?1000l\x1b[?1006l"
MENU_WORDS = ("menu", "m", "leave", "back", "quit", "q", "exit")
BOT_HINT = "Type bot for a bot"      # in the waiting box: the typed command bot invites one (docs/bot.md)
QUIT_WORDS = ("quit", "q", "bye", "exit", "logoff", "logout")
SORRY = "Something went wrong; you are back at the menu."
LINE_GONE = (EOFError, ConnectionError, TimeoutError, CoreError, OSError)   # the line or the core went away
# Movement keys: the session's choice -> (its name, the letters for up, down, left and right). The arrows
# work in every choice; "arrows" (no letters) is the default.
MOVE_SETS = {"arrows": ("Arrows only", ""), "ijkl": ("IJKL", "ikjl"), "esdf": ("ESDF", "edsf"),
             "wasd": ("WASD", "wsad"), "hjkl": ("HJKL (vi)", "kjhl")}
MOVE_ORDER = ["arrows", "ijkl", "esdf", "wasd", "hjkl"]
DIRECTIONS = ("UP", "DOWN", "LEFT", "RIGHT")
HELP_ROWS = 17          # the most lines a help box holds (80x24, with its frame, foot and shadow)
# Terminal types known to send ANSI arrow keys (ESC [ A ...). For no type, an unknown one or a VT52-class
# one the login offers letter keys for moving.
ARROW_TTYPES = ("ansi", "pcansi", "syncterm", "cterm", "scoansi", "netrunner", "xterm", "screen", "tmux",
                "rxvt", "linux", "alacritty", "kitty", "konsole", "putty", "iterm", "gnome", "wezterm", "foot",
                "vt1", "vt2", "vt3", "vt4", "vt5", "dec-vt1", "dec-vt2", "dec-vt3", "dec-vt4", "dec-vt5")


def move_label(m):
    """A movement-key choice as a menu line: 'IJKL       I up, J left, K down, L right'."""
    name, letters = MOVE_SETS[m]
    if not letters:
        return name
    up, down, left, right = letters.upper()
    return "%-10s %s up, %s left, %s down, %s right" % (name, up, left, down, right)


def arrows_for_ttype(tt):
    """Whether a terminal of this type is known to send ANSI arrow keys."""
    t = (tt or "").lower()
    return t.startswith(ARROW_TTYPES) and not t.startswith(("vt52", "dec-vt52"))


def log_error(where):
    """An unexpected exception: the full traceback to stderr (code and the exception, nothing typed)."""
    print("telnet %s error: %s" % (where, traceback.format_exc().rstrip()), file=sys.stderr, flush=True)


def mode_for_ttype(tt):
    t = (tt or "").lower()
    if not t:
        return None
    if t.startswith(("ansi", "pcansi", "syncterm", "cterm", "pc", "scoansi", "netrunner")):
        return "pc"
    if t.startswith(("xterm", "screen", "tmux", "rxvt", "linux", "alacritty", "kitty", "konsole",
                     "putty", "iterm", "gnome", "wezterm", "foot")):
        return "utf8"
    if t.startswith(("vt", "dec-vt", "ibm", "wyse", "adm")):
        return "ansi"
    if t in ("dumb", "unknown", "network", "tty33", "glasstty"):
        return "plain"
    return "ansi"


def mouse_for_ttype(tt):
    """Terminals that answer DECSET 1000 with mouse reports (the Linux console does not)."""
    t = (tt or "").lower()
    return t.startswith(("xterm", "screen", "tmux", "rxvt", "putty", "iterm", "alacritty", "kitty", "konsole",
                         "gnome", "wezterm", "foot"))


class TelnetIO:
    """Telnet I/O: option negotiation (terminal type, echo), key events
    (characters, ENTER, BS, arrows, HOME/END/PGUP/PGDN, ESC, REDRAW for
    Ctrl-L, ("CLICK", x, y) for a left mouse click at 0-based screen cell
    x, y) and line editing."""

    charset = "ascii"

    def __init__(self, reader, writer):
        self.r, self.w = reader, writer
        self.state = 0
        self.cmd = 0
        self.sb = bytearray()
        self.keys = []
        self.cr = False
        self.esc = 0
        self.csi = ""
        self.ss3 = False
        self.mouse_bytes = []
        self.mouse = False          # mouse reports switched on (set by main_session)
        self.ttype = None
        self.ttype_event = asyncio.Event()
        self.will_ttype = asyncio.Event()
        self.server_echo = False
        self.size = None            # (cols, rows) when the client reports it (NAWS); else 80x24 is assumed
        self.on_input = None        # called for each key the player sends (CoreClient.user_input: activity)

    # ---- output ------------------------------------------------------------
    def write(self, text):
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        data = text.encode(self.charset, "replace").replace(b"\xff", b"\xff\xff")
        self.w.write(data)

    def raw(self, data):
        self.w.write(data)

    async def flush(self):
        await self.w.drain()

    # ---- negotiation -------------------------------------------------------
    async def negotiate(self, wait=1.5):
        """Ask for the terminal type (and the window size); returns the type or None."""
        self.raw(bytes([IAC, DO, TTYPE, IAC, DO, NAWS]))
        await self.flush()
        await self._pump_until(self.will_ttype, wait)
        if self.will_ttype.is_set() and not self.ttype_event.is_set():
            self.raw(bytes([IAC, SB, TTYPE, 1, IAC, SE]))
            await self.flush()
            await self._pump_until(self.ttype_event, wait)
        return self.ttype

    async def _pump_until(self, event, wait):
        end = time.time() + wait
        while not event.is_set() and time.time() < end:
            try:
                data = await asyncio.wait_for(self.r.read(512), max(0.05, end - time.time()))
            except asyncio.TimeoutError:
                break
            if not data:
                raise EOFError
            self._feed(data)

    def char_mode(self):
        """Ask the client to send each key at once and let the server echo."""
        self.raw(bytes([IAC, WILL, ECHO, IAC, WILL, SGA, IAC, DO, SGA]))

    # ---- input -------------------------------------------------------------
    def _feed(self, data):
        for b in data:
            if self.state == 0:
                if b == IAC:
                    self.state = 1
                else:
                    self._byte(b)
            elif self.state == 1:
                if b in (DO, DONT, WILL, WONT):
                    self.cmd = b
                    self.state = 2
                elif b == SB:
                    self.sb = bytearray()
                    self.state = 3
                elif b == IAC:
                    self._byte(255)
                    self.state = 0
                else:
                    self.state = 0
            elif self.state == 2:
                if b == TTYPE and self.cmd == WILL:
                    self.will_ttype.set()
                elif b == TTYPE and self.cmd == WONT:
                    self.will_ttype.set()
                    self.ttype_event.set()
                elif b == ECHO and self.cmd == DO:
                    self.server_echo = True
                elif b == ECHO and self.cmd == DONT:
                    self.server_echo = False
                self.state = 0
            elif self.state == 3:
                if b == IAC:
                    self.state = 4
                else:
                    self.sb.append(b)
            elif self.state == 4:
                if b == SE:
                    if self.sb[:2] == bytes([TTYPE, 0]):
                        self.ttype = self.sb[2:].decode("ascii", "ignore").strip()[:40]
                        self.ttype_event.set()
                    elif self.sb[:1] == bytes([NAWS]) and len(self.sb) >= 5:
                        cols, rows = self.sb[1] * 256 + self.sb[2], self.sb[3] * 256 + self.sb[4]
                        if cols and rows and (cols, rows) != self.size:
                            self.size = (cols, rows)
                            self.keys.append("REDRAW")      # a resized window needs a full repaint
                    self.state = 0
                else:
                    self.sb.append(b)
                    self.state = 3

    def _click(self, btn, x, y):
        """A mouse report (1-based cell): a press of the left button, no
        motion and no wheel, becomes ("CLICK", x, y) with 0-based x, y."""
        if btn & 0x63 == 0 and x >= 1 and y >= 1:
            self.keys.append(("CLICK", x - 1, y - 1))

    def _byte(self, b):
        if self.esc == 1:
            if b in (ord("["), ord("O")):
                self.esc = 2
                self.csi = ""
                self.ss3 = b == ord("O")
                return
            self.keys.append("ESC")
            self.esc = 0
        elif self.esc == 3:                 # legacy mouse report: ESC [ M b x y, each byte + 32
            self.mouse_bytes.append(b)
            if len(self.mouse_bytes) == 3:
                self.esc = 0
                btn, x, y = (v - 32 for v in self.mouse_bytes)
                self._click(btn, x, y)
            return
        elif self.esc == 2:
            if 0x30 <= b <= 0x3F and len(self.csi) < 16:
                self.csi += chr(b)
                return
            self.esc = 0
            if b == ord("M") and not self.csi and not self.ss3:
                self.esc = 3
                self.mouse_bytes = []
                return
            if b in (ord("M"), ord("m")) and self.csi.startswith("<") and not self.ss3:
                f = self.csi[1:].split(";")      # SGR report: ESC [ < b ; x ; y M (press) or m (release)
                if b == ord("M") and len(f) == 3 and all(n.isdigit() for n in f):
                    self._click(int(f[0]), int(f[1]), int(f[2]))
                return
            if b == ord("~"):
                key = {"1": "HOME", "7": "HOME", "4": "END", "8": "END", "5": "PGUP",
                       "6": "PGDN"}.get(self.csi)
            else:
                key = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT", "H": "HOME",
                       "F": "END"}.get(chr(b))
            if key:
                self.keys.append(key)
            return
        if b == 27:
            self.esc = 1
        elif b in (13, 10):
            if b == 10 and self.cr:
                self.cr = False
                return
            self.cr = b == 13
            self.keys.append("ENTER")
            return
        elif b == 0:
            pass
        elif b in (8, 127):
            self.keys.append("BS")
        elif b == 12:
            self.keys.append("REDRAW")
        elif 32 <= b < 127:
            self.keys.append(chr(b))
        self.cr = False

    async def readkey(self, timeout=None):
        """Next key, or None on timeout."""
        end = None if timeout is None else time.time() + timeout
        while not self.keys:
            if self.esc == 1:
                try:
                    data = await asyncio.wait_for(self.r.read(512), 0.15)
                except asyncio.TimeoutError:
                    self.esc = 0
                    self.keys.append("ESC")
                    break
            else:
                left = IDLE_SECS if end is None else end - time.time()
                if left <= 0:
                    return None
                try:
                    data = await asyncio.wait_for(self.r.read(512), left)
                except asyncio.TimeoutError:
                    if end is None:
                        raise
                    return None
            if not data:
                raise EOFError
            self._feed(data)
        if self.on_input is not None:
            self.on_input()
        return self.keys.pop(0)

    async def poll(self, wait):
        """Take in whatever arrives within wait seconds; True if a key is queued."""
        end = time.time() + wait
        while not self.keys:
            left = end - time.time()
            if left <= 0:
                break
            try:
                data = await asyncio.wait_for(self.r.read(512), left)
            except asyncio.TimeoutError:
                break
            if not data:
                raise EOFError
            self._feed(data)
        return bool(self.keys)

    async def readline(self, maxlen=72, secret=False):
        buf = []
        while True:
            k = await self.readkey()
            if k == "ENTER":
                if self.server_echo:
                    self.write("\n")
                    await self.flush()
                return "".join(buf).strip()
            if k == "BS":
                if buf:
                    buf.pop()
                    if self.server_echo:
                        self.write("\b \b")
                        await self.flush()
            elif len(k) == 1 and len(buf) < maxlen:
                buf.append(k)
                if self.server_echo:
                    self.write("*" if secret else k)
                    await self.flush()


# ---- text helpers (also used by adapters/web.py) -------------------------------

def fmt_secs(s):
    s = max(0, int(s))
    if s >= 86400:
        return "%dd %02dh" % (s // 86400, s % 86400 // 3600)
    if s >= 3600:
        return "%dh %02dm" % (s // 3600, s % 3600 // 60)
    return "%d:%02d" % (s // 60, s % 60)


def clock_text(v, names):
    if not v or v.get("clock") is None:
        return ""
    seat, left, limit = v["clock"]
    who = names[seat] if 0 <= seat < len(names) else "seat %d" % seat
    return "%s has %s left to move (limit %s a turn)" % (who, fmt_secs(left), fmt_secs(limit))


def turn_text(secs):
    """Time per turn in words: 5 min, 1 hour, 3 days."""
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "min")):
        if secs >= size and secs % size == 0:
            n = secs // size
            return "%d %s%s" % (n, unit, "s" if n != 1 and unit != "min" else "")
    return fmt_secs(secs)


def describe_event(text, names):
    """An EVT payload (after the seq) -> a short human line, or None. The events
    every game shares; each game's own are described by its module in
    adapters/games/ (describe_event there), which is asked first."""
    p = text.split()
    k = p[0]

    def nm(i):
        i = int(i)
        return names[i] if 0 <= i < len(names) else "seat %d" % i
    if k == "PASS":
        return "%s has no move and passes" % nm(p[1])
    if k == "JOIN":
        return "%s sat down" % p[2]
    if k == "LEAVE":
        return "%s stood up" % nm(p[1])
    if k == "START":
        return "Game started"
    if k == "GONE" and len(p) == 3:
        return "%s %s and leaves the game; the others play on" % (
            nm(p[1]), "ran out of time" if p[2] == "timeout" else "resigned")
    if k == "CHT":
        who = nm(p[1]) if p[1] != "-" else "watcher"
        return "<%s> %s" % (who, " ".join(p[2:]))
    if k == "END" and len(p) > 1:
        if p[1] == "resign":
            return "%s resigned" % nm(p[2])
        if p[1] == "timeout":
            return "%s ran out of time" % nm(p[2]) if len(p) > 2 else "Game ended: time ran out"
        if p[1] == "win":
            return "Game over: %s wins %s" % (nm(p[2]), p[3])
        if p[1] == "draw":
            return "Game over: a draw, %s" % p[2]
        if p[1] == "abandoned":
            return "The operator ended this game"
    return describe_invite_event(text) or describe_racks_event(text, names)


def say_cut(say, width):
    """What a player plays with ("with GameMaster 1.0.0 on a Macintosh Plus") cut at a word to width, without
    a dangling "on a" or "+"; "" when not even "with X" fits."""
    words = fit(say or "", max(0, width)).split()
    while words and (words[-1].lower() in CUT_ENDS + ("for", "of", "in", "at", "by", "to")
                     or " ".join(words).count("(") > " ".join(words).count(")")):
        words.pop()
    return " ".join(words) if len(words) >= 2 else ""


def with_clients(templates, names, clients, sep, width):
    """A line naming the players at a table with what each plays with, as much of it as fits width: each
    template has one %s for the names, each "ann (with GameMaster 1.0.0 on a Macintosh Plus)", joined with
    sep; a template (or a list of them, the shorter ones later) is tried with each player's words whole,
    then cut shorter until a line fits. names: [(seat, name)]; clients: {seat: text} (LST clients, VUE C).
    When nothing of them fits, the first template with the names alone."""
    templates = [templates] if isinstance(templates, str) else templates
    for most in range(60, 15, -2):
        parts = []
        for seat, name in names:
            say = say_cut(clients.get(seat, ""), most)
            parts.append("%s (%s)" % (name, say) if say else name)
        for template in templates:
            line = template % sep.join(parts)
            if len(line) <= width:
                return line
    return templates[0] % sep.join(n for _, n in names)


def wrap(text, width):
    """Split text into lines of at most width; continuation lines are indented."""
    out, line = [], ""
    for word in text.split():
        while len(word) > width - 2:
            if line:
                out.append(line)
                line = "  "
            out.append((line + word)[:width])
            word = word[width - len(line):]
            line = "  "
        if not line.strip():
            line = line + word
        elif len(line) + 1 + len(word) <= width:
            line += " " + word
        else:
            out.append(line)
            line = "  " + word
    if line.strip():
        out.append(line)
    return out


# ---- character sets, colors and the cell screen -------------------------------

def glyph_set(mode):
    """Line drawing and discs for a display mode; pc and utf8 draw the same
    shapes (every one of them is in CP437), ansi and plain use ASCII."""
    if mode in ("pc", "utf8"):
        return {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─",
                "v": "│", "lj": "├", "rj": "┤",
                "TL": "╔", "TR": "╗", "BL": "╚", "BR": "╝", "H": "═",
                "V": "║", "Tt": "╤", "Tb": "╧", "Tl": "╟", "Tr": "╢",
                "x": "┼", "dark": "▐█▌", "light": "▐▓▌",
                "hint": "·", "ptr": "»", "more": "▓"}
    return {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|", "lj": "+", "rj": "+",
            "TL": "+", "TR": "+", "BL": "+", "BR": "+", "H": "-", "V": "|", "Tt": "+", "Tb": "+",
            "Tl": "+", "Tr": "+", "x": "+", "dark": " X ", "light": " O ", "hint": ".", "ptr": ">",
            "more": "#"}


# SGR attributes; every one starts from 0 so it can be sent on its own.
A_TEXT = ""
A_BRIGHT = "0;1;37"
A_DIM = "0;36"
A_HEAD = "0;1;36"
A_BORDER = "0;36"
A_YELLOW = "0;1;33"
A_GREEN = "0;1;32"
A_RED = "0;1;31"
A_SEL = "0;30;46"
A_TITLE = "0;1;37;44"
A_TITLE_HI = "0;1;33;44"
A_STATUS = "0;30;47"
A_KEY = "0;31;47"
A_POP = "0;1;37;44"
A_POPT = "0;1;33;44"
A_POPDIM = "0;37;44"
A_POPSEL = "0;30;47"
A_FIELD = "0;30;46"
A_SHADOW = "0;1;30;40"
A_GRID = "0;30;42"
A_AXIS = "0;32"
A_AXIS_HI = "0;1;33"


class Canvas:
    """An 80x24 grid of (character, attribute) cells. Writing outside it
    sets `clipped`, which the tests treat as a layout bug."""

    def __init__(self):
        self.ch = [[" "] * COLS for _ in range(ROWS)]
        self.at = [[A_TEXT] * COLS for _ in range(ROWS)]
        self.clipped = False
        self.hot = []           # click targets: (x0, y0, x1, y1, value); later ones lie on top

    def hot_area(self, x, y, w, h, value):
        self.hot.append((x, y, x + w, y + h, value))

    def hit(self, x, y):
        for x0, y0, x1, y1, value in reversed(self.hot):
            if x0 <= x < x1 and y0 <= y < y1:
                return value
        return None

    def put(self, x, y, text, attr=A_TEXT):
        if not 0 <= y < ROWS:
            self.clipped = True
            return
        row_c, row_a = self.ch[y], self.at[y]
        for i, c in enumerate(text):
            if 0 <= x + i < COLS:
                row_c[x + i] = c
                row_a[x + i] = attr
            else:
                self.clipped = True

    def lines(self):
        return ["".join(r).rstrip() for r in self.ch]


def box(cv, g, x, y, w, h, attr, title="", double=False, tattr=None, fill=None):
    k = ("TL", "TR", "BL", "BR", "H", "V") if double else ("tl", "tr", "bl", "br", "h", "v")
    tl, tr, bl, br, hz, vt = (g[c] for c in k)
    cv.put(x, y, tl + hz * (w - 2) + tr, attr)
    for r in range(y + 1, y + h - 1):
        cv.put(x, r, vt, attr)
        if fill is not None:
            cv.put(x + 1, r, " " * (w - 2), fill)
        cv.put(x + w - 1, r, vt, attr)
    cv.put(x, y + h - 1, bl + hz * (w - 2) + br, attr)
    if title:
        cv.put(x + 2, y, " %s " % title, tattr or attr)


def rule(cv, g, x, y, w, attr, title="", tattr=None):
    cv.put(x, y, g["lj"] + g["h"] * (w - 2) + g["rj"], attr)
    if title:
        cv.put(x + 2, y, " %s " % title, tattr or attr)


def title_bar(cv, g, text, right=""):
    cv.put(0, 0, " " * COLS, A_TITLE)
    cv.put(1, 0, "ParlorNet", A_TITLE_HI)
    cv.put(11, 0, g["v"] + " " + text, A_TITLE)
    right = right[:COLS - 16 - len(text)]
    cv.put(COLS - 1 - len(right), 0, right, A_TITLE)


STATUS_KEYS = {"Enter": "ENTER", "Esc": "ESC", "Backspace": "BS"}
# The movement keys of the screen being composed (a key of MOVE_SETS, "" for arrows only): set by
# AnsiView.screen() around compose(), so every game's status bar shows the keys that apply.
DRAWING_MOVES = contextvars.ContextVar("parlornet_moves", default="")


def status_bar(cv, items):
    """The bottom line: keys and what they do. 79 columns: the last cell of
    the screen is never written, so nothing can scroll. A click on an item
    counts as its key. With movement letters, "Arrows" shows the letters and
    a hotkey that is one of them shows as :x (typed : then the letter)."""
    letters = MOVE_SETS.get(DRAWING_MOVES.get(), ("", ""))[1]
    cv.put(0, ROWS - 1, " " * (COLS - 1), A_STATUS)
    x = 1
    for key, label in items:
        if letters and key == "Arrows":
            key = DRAWING_MOVES.get().upper()
        elif letters and len(key) == 1 and key.lower() in letters:
            key = ":" + key.lower()
        if x + len(key) + 1 + len(label) > COLS - 2:
            break
        cv.put(x, ROWS - 1, key, A_KEY)
        cv.put(x + len(key) + 1, ROWS - 1, label, A_STATUS)
        k = STATUS_KEYS.get(key, key.lower() if len(key) == 1 else key[1] if len(key) == 2 and key[0] == ":"
                            else None)
        if k:
            cv.hot_area(x, ROWS - 1, len(key) + 1 + len(label), 1, k)
        x += len(key) + len(label) + 3


def popup(cv, g, title, lines, options=None, sel=0, field=None, foot=None, buttons=None):
    """A dialog box in the middle of the screen, with a shadow. Options and
    buttons ((label, key) pairs) can be clicked; the rest of the screen
    cannot while it is up. Returns where the terminal's cursor should sit."""
    btext = "   ".join(label for label, _ in buttons or [])
    widths = [len(title) + 6] + [len(l) for l in lines] + [len(o) + 6 for o in options or []]
    widths += [len(foot or ""), 20 if field is not None else 0, len(btext)]
    w = min(max(widths) + 4, COLS - 6)
    h = len(lines) + 2 + (len(options) + 1 if options else 0) + (2 if field is not None else 0)
    h += (2 if foot else 0) + (2 if buttons else 0)
    x, y = (COLS - w) // 2, max(2, (ROWS - 1 - h) // 2)
    for r in range(y + 1, min(y + h + 1, ROWS - 1)):
        for c in (x + w, x + w + 1):
            if c < COLS:
                cv.at[r][c] = A_SHADOW
    if y + h < ROWS - 1:
        for c in range(x + 2, min(x + w + 2, COLS)):
            cv.at[y + h][c] = A_SHADOW
    box(cv, g, x, y, w, h, A_POP, title, double=True, tattr=A_POPT, fill=A_POP)
    cv.hot_area(0, 0, COLS, ROWS, None)         # modal: clicks elsewhere do nothing
    r, pos = y + 1, None
    for l in lines:
        cv.put(x + 2, r, l[:w - 4], A_POP)
        r += 1
    if options:
        r += 1
        for i, o in enumerate(options):
            cv.put(x + 3, r, (" %d  %s" % (i + 1, o)).ljust(w - 6), A_POPSEL if i == sel else A_POP)
            cv.hot_area(x + 3, r, w - 6, 1, ("opt", i))
            if i == sel:
                pos = (x + 3, r)
            r += 1
    if field is not None:
        r += 1
        fw = w - 6
        shown = field[-(fw - 1):]
        cv.put(x + 3, r, shown.ljust(fw), A_FIELD)
        pos = (x + 3 + len(shown), r)
        r += 1
    if foot:
        cv.put(x + 2, r + 1, foot[:w - 4], A_POPDIM)
        r += 2
    if buttons:
        bx = x + max(2, (w - len(btext)) // 2)
        for label, key in buttons:
            cv.put(bx, r + 1, label, A_POPSEL)
            cv.hot_area(bx, r + 1, len(label), 1, key)
            bx += len(label) + 3
    return pos


class Painter:
    """Turns a Canvas into terminal output: only the cells that differ from
    what is on the screen, or a clear and full paint when that is cheaper."""

    def __init__(self):
        self.prev = None
        self.attr = None
        self.pos = None

    def invalidate(self):
        self.prev = None

    @staticmethod
    def _parts(attr):
        codes = attr.split(";") if attr else []
        bold = "1" in codes
        fg = next((c for c in codes if len(c) == 2 and c[0] == "3"), None)
        bg = next((c for c in codes if len(c) == 2 and c[0] == "4"), None)
        return bold, fg, bg

    def _sgr(self, out, attr):
        """Switch to attr, sending only what changes when nothing has to be
        turned off (turning bold off portably takes a full reset)."""
        if attr == self.attr:
            return
        b0, f0, g0 = self._parts(self.attr) if self.attr is not None else (True, None, None)
        b1, f1, g1 = self._parts(attr)
        if not attr or (b0 and not b1) or (f0 and not f1) or (g0 and not g1):
            out.append("\x1b[%sm" % (attr or "0"))
        else:
            out.append("\x1b[%sm" % ";".join(c for c, on in (("1", b1 and not b0), (f1, f1 != f0),
                                                              (g1, g1 != g0)) if on))
        self.attr = attr

    def _goto(self, out, x, y):
        if self.pos == (x, y):
            return
        if self.pos and self.pos[1] == y and x > self.pos[0]:
            out.append("\x1b[%dC" % (x - self.pos[0]) if x - self.pos[0] > 1 else "\x1b[C")
        elif self.pos and self.pos[1] == y and x == 0:
            out.append("\r")
        else:
            out.append("\x1b[%d;%dH" % (y + 1, x + 1))
        self.pos = (x, y)

    def frame(self, cv, cursor=None, full=False):
        out = []
        prev = self.prev
        if prev is not None and not full:
            changed = sum(1 for y in range(ROWS) for x in range(COLS)
                          if cv.ch[y][x] != prev.ch[y][x] or cv.at[y][x] != prev.at[y][x])
            filled = sum(1 for y in range(ROWS) for x in range(COLS)
                         if cv.ch[y][x] != " " or cv.at[y][x] != A_TEXT)
            full = changed > 200 and filled < changed
        if prev is None or full:
            out.append(CLEAR)
            self.attr, self.pos = A_TEXT, (0, 0)
            prev = Canvas()
        for y in range(ROWS):
            cc, ca, pc, pa = cv.ch[y], cv.at[y], prev.ch[y], prev.at[y]
            last = COLS - 1 if y == ROWS - 1 else COLS
            x = 0
            while x < last:
                if cc[x] == pc[x] and ca[x] == pa[x]:
                    x += 1
                    continue
                self._goto(out, x, y)
                while x < last:
                    self._sgr(out, ca[x])
                    out.append(cc[x])
                    x += 1
                    # carry on through a short run of unchanged cells when
                    # rewriting them is cheaper than moving the cursor
                    k = x
                    while k < last and cc[k] == pc[k] and ca[k] == pa[k] and ca[k] == self.attr and k - x < 4:
                        k += 1
                    if k >= last or k - x >= 4 or (cc[k] == pc[k] and ca[k] == pa[k]):
                        break
                self.pos = (x, y) if x < COLS else None
        self.prev = cv
        if cursor:
            self._goto(out, min(cursor[0], COLS - 1), min(cursor[1], ROWS - 1))
        return "".join(out)


# ---- full-screen views (ANSI modes) ---------------------------------------------

class AnsiView:
    """A full-screen view: compose() draws the whole screen into a Canvas,
    show() sends the difference. Dialogs are drawn over it."""

    idle = True
    HOTKEYS = ""            # single keys taken as commands on an empty input line

    def __init__(self, sess):
        self.s, self.io, self.g = sess, sess.io, sess.g
        self.msg, self.msg_attr = "", A_YELLOW
        self.buf = ""
        self.overlay = None
        self.last_key = time.time()

    def compose(self):
        raise NotImplementedError

    def cursor_at(self):
        return None

    def say(self, text, attr=A_YELLOW):
        self.msg, self.msg_attr = text, attr

    # ---- movement keys -------------------------------------------------------
    def move_set(self):
        """The session's movement letters: a key of MOVE_SETS, or "" for the arrows only."""
        m = getattr(self.s, "moves", "")
        return m if MOVE_SETS.get(m, ("", ""))[1] else ""

    def move_of(self, k):
        """A movement letter (either case) -> UP, DOWN, LEFT or RIGHT; any other key -> None."""
        m = self.move_set()
        if not m or not isinstance(k, str) or len(k) != 1:
            return None
        i = MOVE_SETS[m][1].find(k.lower())
        return DIRECTIONS[i] if i >= 0 else None

    def move_key(self, direction):
        """The letter (upper case) that moves the cursor that way, or None with the arrows only."""
        m = self.move_set()
        return MOVE_SETS[m][1][DIRECTIONS.index(direction)].upper() if m else None

    def kn(self, letter):
        """How to press a hotkey, for hints: D, or :d when D is a movement letter."""
        return ":" + letter.lower() if self.move_of(letter) else letter.upper()

    def typed_text(self):
        """What is on the input line, without the : that opened it."""
        return self.buf[1:] if self.buf.startswith(":") else self.buf

    def keys_help(self):
        """Lines on the movement letters for the help box ([] with the arrows only)."""
        m = self.move_set()
        if not m:
            return []
        up, down, left, right = MOVE_SETS[m][1].upper()
        name = m.upper()
        out = ["%s  %s up, %s left, %s down, %s right; arrows too." % (name, up, left, down, right),
               "Text that starts with %s, %s, %s or %s: type : first." % tuple(name)]
        taken = [c for c in name if c.lower() in self.HOTKEYS]
        if taken:
            names = ", ".join(taken[:-1]) + " and " + taken[-1] if len(taken) > 1 else taken[0]
            out.append("The %s key%s: type %s." % (names, "s" if len(taken) > 1 else "",
                                                   " or ".join(":" + c.lower() for c in taken)))
        return out

    async def help_box(self, title, lines):
        """A help box, with the movement letters first when the session has them (on a second page when
        they do not fit)."""
        extra = self.keys_help()
        if extra and len(lines) + 1 + len(extra) <= HELP_ROWS:
            lines = extra + [""] + list(lines)
        elif extra:
            await self.message(title, lines)
            title, lines = "Movement keys", extra
        await self.message(title, lines)

    async def char_key(self, k):
        """A printable key: on an empty line, or right after :, a hotkey letter acts (unless more typing
        follows at once); anything else is typed. A leading : opens the line for any text."""
        if self.buf in ("", ":") and k.lower() in self.HOTKEYS and not await self.typing_follows():
            self.buf = ""
            return await self.hotkey(k.lower())
        if len(self.buf) < 60:
            self.buf += k
        return None

    async def run_line(self, line):
        """Enter on a typed line: the command. After a leading :, a hotkey letter alone does what the key
        does."""
        if line.startswith(":"):
            line = line[1:].strip()
            if len(line) == 1 and line.lower() in self.HOTKEYS:
                return await self.hotkey(line.lower())
        return await self.command(line)

    async def hotkey(self, k):
        return None

    async def command(self, line):
        return None

    def hit(self, k):
        """What a ("CLICK", x, y) event landed on in the last screen sent:
        a key name, a view-specific tuple, or None."""
        cv = self.s.painter.prev if self.s.painter else None
        if cv is None or not isinstance(k, tuple):
            return None
        return cv.hit(k[1], k[2])

    def screen(self):
        token = DRAWING_MOVES.set(self.move_set())      # the status bar shows the session's keys
        try:
            cv = self.compose()
        finally:
            DRAWING_MOVES.reset(token)
        pos = (min(3 + len(self.buf), COLS - 2), ROWS - 2) if self.buf else self.cursor_at()
        if self.overlay:
            pos = self.overlay(cv) or pos
        return cv, pos

    def too_small(self):
        size = getattr(self.io, "size", None)
        return bool(size) and (size[0] < COLS or size[1] < ROWS)

    async def show(self, full=False):
        if self.too_small():
            cols, rows = self.io.size
            self.io.write(CLEAR + "Your window is %dx%d.\r\nParlorNet needs 80x24:\r\nplease make it larger."
                          % (cols, rows))
            await self.io.flush()
            self.s.painter.invalidate()     # repaint everything once it is big enough
            return
        cv, pos = self.screen()
        data = self.s.painter.frame(cv, pos or (COLS - 2, ROWS - 1), full)
        if data:
            self.io.write(data)
            await self.io.flush()

    def input_line(self, cv):
        """Line 23: what is being typed, or else the latest message."""
        if self.buf:
            cv.put(1, ROWS - 2, "> " + self.buf[-(COLS - 6):], A_BRIGHT)
        elif self.msg:
            cv.put(1, ROWS - 2, self.msg[:COLS - 2], self.msg_attr)

    async def key(self, timeout=1.0):
        k = await self.io.readkey(timeout)
        if k is None:
            if self.idle and time.time() - self.last_key > IDLE_SECS:
                raise asyncio.TimeoutError
        else:
            self.last_key = time.time()
        return k

    async def typing_follows(self):
        """A hotkey letter on an empty command line is the start of a typed
        command when more typing follows at once (a typed or pasted word)."""
        if not self.io.keys:
            await self.io.poll(HOTKEY_WAIT)
        return bool(self.io.keys) and (len(self.io.keys[0]) == 1 or self.io.keys[0] in ("ENTER", "BS"))

    async def swallow_enter(self):
        """After a Y/N answer, drop an Enter typed out of habit."""
        if await self.io.poll(HOTKEY_WAIT) and self.io.keys[0] == "ENTER":
            self.io.keys.pop(0)

    async def confirm(self, title, lines):
        self.overlay = lambda cv: popup(cv, self.g, title, lines,
                                        buttons=[(" Y  yes ", "y"), (" N or Esc  no ", "n")])
        await self.show()
        try:
            while True:
                k = await self.io.readkey()
                if isinstance(k, tuple):
                    k = self.hit(k) or ""
                if k in ("y", "Y", "n", "N", "ESC"):
                    break
        finally:
            self.overlay = None
        await self.swallow_enter()
        await self.show()
        return k in ("y", "Y")

    async def choose(self, title, lines, options, sel):
        foot = "%s and Enter, or 1-%d. Esc cancels." % (self.move_set().upper() or "Arrows", len(options))
        try:
            while True:
                self.overlay = lambda cv: popup(cv, self.g, title, lines, options, sel, foot=foot)
                await self.show()
                k = await self.io.readkey()
                if isinstance(k, tuple):
                    v = self.hit(k)
                    if isinstance(v, tuple) and v[0] == "opt":
                        sel = v[1]
                        break
                    continue
                k = self.move_of(k) or k
                if k in ("UP", "LEFT"):
                    sel = (sel - 1) % len(options)
                elif k in ("DOWN", "RIGHT"):
                    sel = (sel + 1) % len(options)
                elif k in ("ENTER", " "):
                    break
                elif len(k) == 1 and k.isdigit() and 1 <= int(k) <= len(options):
                    sel = int(k) - 1
                    break
                elif k == "ESC":
                    sel = None
                    break
        finally:
            self.overlay = None
        await self.show()
        return sel

    async def ask_text(self, title, lines, maxlen=12, secret=False, free=False):
        """A box asking for a line of text: a name (letters, digits, - and _), or with secret or free any
        printable characters. None when Esc cancels it."""
        text = ""
        try:
            while True:
                self.overlay = lambda cv: popup(cv, self.g, title, lines, field="*" * len(text) if secret else text,
                                                buttons=[(" Enter  OK ", "ENTER"), (" Esc  cancel ", "ESC")])
                await self.show()
                k = await self.io.readkey()
                if isinstance(k, tuple):
                    k = self.hit(k)
                    if not isinstance(k, str):
                        continue
                if k == "ENTER":
                    break
                if k == "ESC":
                    text = None
                    break
                if k == "BS":
                    text = text[:-1]
                elif len(k) == 1 and len(text) < maxlen and (k.isalnum() or k in "-_" or
                                                             ((secret or free) and " " <= k <= "~")):
                    text += k
        finally:
            self.overlay = None
        await self.show()
        return text

    async def message(self, title, lines):
        self.overlay = lambda cv: popup(cv, self.g, title, lines, foot="Press any key (or click).")
        await self.show()
        try:
            await self.io.readkey()
        finally:
            self.overlay = None
        await self.show()


# ---- menus ------------------------------------------------------------------------

MENU_HELP = [
    "Arrow keys pick a table, Enter opens it: your own",
    "game, a free seat (you sit down) or a game to watch.",
    "",
    "  N  new table       W  watch the selected table",
    "  P  your password   R  refresh the list",
    "  Q  log off         ?  this help",
    "  O  options: letter keys that move the cursor",
    "  X  decline the selected invitation (Enter joins)",
    "A mouse click selects a table; a second click opens it.",
    "",
    "Typed commands work too: new [game] [rules] [3p]",
    "[5m|1h|1d...] [bot], join <n>, watch <n>, decline <n>,",
    "who, profile [name], machine, activity, scores, time,",
    "list, password, help, quit. Alone? new reversi bot",
    "",
    "Leaving a game or logging off never resigns it, but",
    "your turn timer keeps running while it is your move.",
]

PLAIN_HELP = """ Commands:
   new [game] [rules] [3p] [time] [bot]
                     start a table: a game (reversi, mancala ...), its rules if it has
                     several, the players if it takes more than two (3p), and the time
                     per turn like 5m, 1h, 1d (what is left out is asked); with bot, the
                     bot is invited to the other seats (at a table: type bot)
   join <n>          sit at table n, or go back to your game there; at a table you
                     are invited to, this accepts the invitation
   decline <n>       decline your invitation to table n
   watch <n>         watch table n
   list              show the tables and your invitations again
   who               the players online now
   profile [name]    a player's profile, or your own: their preferred machine, the games
                     they played and what they played with at public tables
   machine [text]    set the machine you like to play on, shown on your profile
                     (machine - clears it)
   activity          the latest turns at public tables, and what they were played with
   scores [game]     the leaderboard: the players with the most points, in every game
                     or in one (scores reversi)
   time              how long you have been connected, and the server's time
   invite <name>     at your table, invite a player
   uninvite <name>   at your table, withdraw that invitation
   password          change your password
   help              this list
   quit              log off (your games stay; your turn timers keep running)"""


class MenuBase:
    """Typed menu commands, shared by the plain and the full-screen menu."""

    async def command(self, line):
        p = line.split()
        if not p:
            return await self.relist()
        cmd = p[0].lower()
        if cmd in QUIT_WORDS:
            return "quit" if await self.confirm_quit() else None
        if cmd in ("help", "?", "h"):
            return await self.help()
        if cmd in ("list", "tables", "games", "l", "refresh", "r"):
            return await self.relist()
        if cmd == "password" and self.s.has_password:
            return await self.password()
        if cmd in ("options", "keys", "o") and self.s.ansi:
            return await self.options()
        if cmd in ("new", "n"):
            return await self.new(p[1:])
        if cmd in ("join", "j", "watch", "w", "accept"):
            if len(p) < 2:
                return await self.notice("Which table? For example: %s 1" % cmd)
            return await self.enter(p[1], watch=cmd in ("watch", "w"), sit=cmd in ("join", "j", "accept"))
        if cmd == "decline":
            if len(p) < 2:
                return await self.notice("Which table? For example: decline 1")
            return await self.decline(p[1])
        if cmd in ("invites", "invitations"):
            return await self.relist()
        if cmd in ("who", "online"):
            return await self.notice(await self.s.who_text())
        if cmd in ("profile", "finger", "whois"):
            return await self.profile(p[1] if len(p) > 1 else "")
        if cmd == "machine":
            return await self.machine(line.split(None, 1)[1].strip() if len(p) > 1 else None)
        if cmd == "activity":
            return await self.show_text("Activity", await self.s.activity_lines())
        if cmd in ("scores", "score", "leaderboard", "top"):
            return await self.show_text("Leaderboard", await self.s.score_lines(p[1] if len(p) > 1 else ""))
        if cmd == "time":
            return await self.notice(" ".join(time_text(*await self.s.core.req("TIM"), width=COLS)))
        if p[0].isdigit():
            return await self.enter(p[0], watch=False)
        return await self.notice("Unknown command: %s. Type help for the list." % line[:20])

    async def profile(self, name):
        """profile [name]: a player's profile (PFL), or your own."""
        if name and not NAME_RE.match(name):
            return await self.notice("Names are letters, digits, - and _, up to 12.")
        title, lines = await self.s.profile_lines(name)
        if title is None:
            return await self.notice(lines[0])
        return await self.show_text(title, lines)

    async def machine(self, text):
        """machine [text]: set your preferred machine (PFL SET PREF); asked when not given, and "-" clears it."""
        if text is None:
            text = await self.ask_machine()
            if not text:
                return await self.notice("Your preferred machine is not changed.")
        text = "" if text == "-" else claim_text(text)
        _, final = await self.s.core.req(("PFL SET PREF %s" % text).rstrip())
        if not final.startswith("OK"):
            return await self.notice("Can't: " + err_text(final))
        return await self.notice("Your preferred machine is now %s." % text if text else
                                 "Your preferred machine is cleared.")

    async def ask_machine(self):
        return (await self.ask("The machine you like to play on (- clears it, Enter keeps it): ")).strip()

    async def show_text(self, title, lines):
        """Lines of text under a title: one after another here, in a box on the full screen."""
        self.say(title + ":")
        for l in lines:
            self.say("  " + l)
        await self.io.flush()

    async def new(self, args):
        """new [game] [rules] [3p] [time]: what is not given is asked for, the rules only for a game with
        several rulesets and the players only for a game that takes more than two."""
        games = [g for g in self.s.games if g["key"] in RENDERERS]
        turn, game, rules, players, rest = None, None, None, None, []
        vs_bot = False
        for a in args:
            g = next((g for g in games if g["key"].split("/")[0] == a.lower()), None)
            if g is not None and game is None:
                game = g
            elif a.lower() in ("bot", "bots"):      # new reversi bot: the bot in every other seat
                vs_bot = True
            elif TURN_RE.match(a):
                turn = a
            elif PLAYERS_RE.match(a):
                players = int(PLAYERS_RE.match(a).group(1))
            else:
                rest.append(a)
        if game is None and len(games) > 1:
            if rest:
                return await self.notice("This server has no text screen for %s. Games here: %s"
                                         % (rest[0][:12], ", ".join(g["name"] for g in games)))
            game = await self.ask_game(games)
            if game is None:
                return None
        game = game or (games[0] if games else None)
        if game is None:
            return await self.notice("No games here have a text screen yet.")
        rulesets = game.get("rulesets") or [game["rules"]]
        for a in rest:
            if a.lower() in rulesets and rules is None:
                rules = a.lower()
            else:
                return await self.notice("%s has no rules called %s. Its rules: %s"
                                         % (game["name"], a[:12], ", ".join(rulesets)))
        if rules is None:
            rules = rulesets[0]
            if len(rulesets) > 1:
                rules = await self.ask_rules(game, rulesets)
                if rules is None:
                    return None
        lo, hi = game.get("min") or 2, game.get("max") or 2
        if players is not None and not lo <= players <= hi:
            return await self.notice("%s takes %d to %d players." % (game["name"], lo, hi))
        if players is None and hi > lo:
            players = await self.ask_players(game, lo, hi)
            if players is None:
                return None
        if turn is None:
            turn = await self.ask_turn(game)
            if turn is None:
                return None
        tid, err = await self.s.new_table(game, turn, rules, players)
        if err:
            return await self.notice(err)
        if vs_bot:                  # sit down, then a bot invited to each other seat (docs/bot.md)
            await self.s.core.req("JON %s" % tid)
            for name in botflag.NAMES[:max(0, (players or lo) - 1)]:
                await self.s.core.req("INV %s %s" % (tid, name))
        return await self.enter(tid, watch=False)

    async def enter(self, tid, watch, sit=False):
        err = await self.s.enter(tid, watch, sit)
        if err:
            return await self.notice(err)
        return await self.back(tid)

    async def decline(self, tid):
        """decline n: my invitation to table n declined (UNV); whoever invited me hears of it."""
        inv = next((i for i in self.s.invites if i["id"] == tid), None)
        _, final = await self.s.core.req("UNV %s %s" % (tid, self.s.name))
        if not final.startswith("OK"):
            return await self.notice("You have no invitation to table %s." % tid[:6])
        await self.relist()
        if inv:
            return await self.notice("You declined %s's invitation to table %s." % (inv["from"], tid))
        return await self.notice("You declined the invitation to table %s." % tid)


class PlainMenu(MenuBase):
    """Line by line, for terminals without escape codes."""

    def __init__(self, sess):
        self.s, self.io = sess, sess.io

    def say(self, text=""):
        self.io.write(text + "\n")

    async def ask(self, text):
        self.io.write(text)
        await self.io.flush()
        return await self.io.readline()

    async def run(self):
        await self.relist()
        while True:
            line = await self.ask("> ")
            if await self.command(line) == "quit":
                return

    async def relist(self):
        tabs, clocks = await self.s.load()
        me = self.s.name
        mine = [t for t in tabs if me in t["names"]]
        others = [t for t in tabs if me not in t["names"]]
        self.say("")
        self.say(("== ParlorNet main menu " + "=" * 56)[:78 - len(me)] + " " + me)
        self.say("Your tables:")
        for t in mine:
            opp = [n for n in t["names"] if n != me]
            st = self.s.table_state(t, clocks)[0]
            self.say("  %4s  %-8s  %-16s  %-12s  %s" % (t["id"], self.s.game_name(t["key"]),
                                                        "vs " + opp[0] if opp else "-",
                                                        turn_text(t["turn"]) + " a turn", st)[:79])
            self.client_lines(t, opp)
        if not mine:
            self.say("  (none)")
        self.say("Other tables:")
        for t in others[:12]:
            who = " vs ".join(t["names"]) if t["status"] == "playing" else ", ".join(t["names"]) or "-"
            act = "join %s" % t["id"] if t["status"] == "open" else "watch %s" % t["id"]
            self.say("  %4s  %-8s  %-27s  %-12s  %s" % (t["id"], self.s.game_name(t["key"]), who[:27],
                                                        turn_text(t["turn"]) + " a turn", act)[:79])
            if t["status"] == "open":
                self.client_lines(t, t["names"])
        if not others:
            self.say("  (none)")
        if self.s.invites:
            self.say("Invitations for you:")
            for i in self.s.invites:
                self.say("  %4s  %-8s  from %-22s  join %s or decline %s" % (
                    i["id"], self.s.game_name(i["key"]), i["from"], i["id"], i["id"])[:79])
        self.say(await self.s.who_text())
        self.say("Commands: new [game] [time], join <n>, watch <n>, %swho, list, %shelp, quit"
                 % ("decline <n>, " if self.s.invites else "", "password, " if self.s.has_password else ""))
        await self.io.flush()

    def client_lines(self, t, names):
        """Under a table's line, what each of these players plays with, where the server said (LST clients)."""
        for n in names:
            say = t["clients"].get(t["seat_of"].get(n, -1), "")
            if say:
                self.say(("        %s plays %s" % (n, say))[:79])

    async def notice(self, text):
        self.say(text)
        await self.io.flush()

    async def help(self):
        await self.notice(PLAIN_HELP if self.s.has_password else
                          "\n".join(l for l in PLAIN_HELP.split("\n") if "password" not in l))

    async def back(self, tid):
        return await self.relist()

    async def confirm_quit(self):
        yn = await self.ask("Log off? Your games stay open and your turn timers keep running. (y/N) ")
        return yn.lower().startswith("y")

    async def password(self):
        """The server's password-change steps, as prompts (secrets not echoed)."""
        try:
            await self.s.core.pwd()
            return await self.notice("Nothing to change.")
        except NeedAuth as ask:
            done = await answer_asks(self.io, self.s.core, ask)
        except CoreError as e:
            return await self.notice("Can't: " + no_wait(str(e)))
        await self.notice("Your password is changed; you are signed out everywhere else." if done
                          else "Your password is not changed.")

    async def ask_turn(self, game):
        t = await self.ask("Time per turn [1d] (5m, 15m, 1h, 1d, 3d, 5d ...): ")
        return t or "1d"

    async def ask_rules(self, game, rulesets):
        for n, r in enumerate(rulesets):
            self.say("  %d  %-10s %s" % (n + 1, r, games.rules_name(game["key"], r)))
        a = (await self.ask("Rules (1-%d) [1]: " % len(rulesets))).strip().lower()
        if not a:
            return rulesets[0]
        if a.isdigit() and 1 <= int(a) <= len(rulesets):
            return rulesets[int(a) - 1]
        r = next((r for r in rulesets if r.startswith(a)), None)
        if r is None:
            await self.notice("%s has no rules called %s." % (game["name"], a[:12]))
        return r

    async def ask_players(self, game, lo, hi):
        a = (await self.ask("Players (%d-%d) [%d]: " % (lo, hi, lo))).strip()
        if not a:
            return lo
        if a.isdigit() and lo <= int(a) <= hi:
            return int(a)
        await self.notice("%s takes %d to %d players." % (game["name"], lo, hi))
        return None

    async def ask_game(self, games):
        words = [g["key"].split("/")[0] for g in games]
        a = (await self.ask("Game (%s) [%s]: " % (", ".join(words), words[0]))).lower()
        if not a:
            return games[0]
        game = next((g for g, w in zip(games, words) if w.startswith(a)), None)
        if game is None:
            await self.notice("No such game here: %s" % a[:12])
        return game


class AnsiMenu(MenuBase, AnsiView):
    """The full-screen main menu: your tables, other tables, a new-table
    entry; arrows and Enter, hotkeys in the status bar."""

    MINE_TOP, MINE_ROWS = 2, 5
    OTHER_TOP, OTHER_ROWS = 10, 8
    HOTKEYS = "nwproqx?"

    def __init__(self, sess):
        AnsiView.__init__(self, sess)
        self.mine, self.others, self.clocks = [], [], {}
        self.sel = 0
        self.top = [0, 0]
        self.loaded = 0.0
        self.first = True
        self.seen_invites = set()

    # ---- data ------------------------------------------------------------
    @property
    def items(self):
        return self.mine + self.others

    @staticmethod
    def item_id(it):
        return it[1]["id"] if it[0] == "table" else "inv:" + it[1]["id"] if it[0] == "invite" else \
            "new:" + it[1]["key"]

    def my_move(self, it):
        if it[0] != "table" or it[1]["id"] not in self.clocks:
            return False
        return self.clocks[it[1]["id"]][0] == it[1]["seat_of"].get(self.s.name, -9)

    async def load(self):
        tabs, self.clocks = await self.s.load()
        cur = self.item_id(self.items[self.sel]) if self.items else None
        me = self.s.name
        self.mine = [("invite", i) for i in self.s.invites]
        self.mine += [("table", t) for t in tabs if me in t["names"]]
        self.mine += [("new", g) for g in self.s.games if g["key"] in RENDERERS]
        fresh = [i for i in self.s.invites if i["id"] not in self.seen_invites]
        self.seen_invites = {i["id"] for i in self.s.invites}
        if fresh and not self.first:
            i = fresh[0]
            self.say("%s invites you to %s at table %s. Enter joins, %s declines." % (
                i["from"], self.s.game_name(i["key"]), i["id"], self.kn("X")), A_YELLOW)
        self.others = [("table", t) for t in tabs if me not in t["names"]]
        ids = [self.item_id(it) for it in self.items]
        if self.first:
            self.first = False
            self.sel = next((i for i, it in enumerate(self.items) if self.my_move(it)), 0)
        elif cur in ids:
            self.sel = ids.index(cur)
        self.sel = max(0, min(self.sel, len(self.items) - 1))
        self.loaded = time.time()

    # ---- drawing ---------------------------------------------------------
    def row_text(self, it):
        """-> (text, status column, status attribute, plain attribute)."""
        if it[0] == "new":
            return "%5s  Start a new %s table" % ("+", it[1]["name"]), 0, A_GREEN, A_GREEN
        if it[0] == "invite":
            i = it[1]
            left = "%5s  %-8s  %-16s  %-10s  " % (i["id"], self.s.game_name(i["key"]), ("from " + i["from"])[:16],
                                                 "")
            return (left + "invited: Enter joins")[:74], len(left), A_YELLOW, A_TEXT
        t = it[1]
        game = self.s.game_name(t["key"])
        if self.s.name in t["names"]:
            opp = [n for n in t["names"] if n != self.s.name]
            left = "%5s  %-8s  %-16s  %-10s  " % (t["id"], game, ("vs " + opp[0]) if opp else "-",
                                                 turn_text(t["turn"]))
            st, attr = self.s.table_state(t, self.clocks)
        else:
            who = " vs ".join(t["names"]) if t["status"] == "playing" else ", ".join(t["names"])
            left = "%5s  %-8s  %-24s  %-10s  " % (t["id"], game, (who or "-")[:24], turn_text(t["turn"]))
            st, attr = ("open: Enter joins", A_GREEN) if t["status"] == "open" else (
                "playing: %s watches" % self.kn("W"), A_DIM)
        return (left + st)[:74], len(left), attr, A_TEXT

    def panel(self, cv, which, top, rows, title, header, items, base, empty):
        g = self.g
        box(cv, g, 1, top, COLS - 2, rows + 3, A_BORDER, title, tattr=A_HEAD)
        cv.put(3, top + 1, header, A_HEAD)
        off = self.top[which]
        if base <= self.sel < base + len(items):
            local = self.sel - base
            off = min(off, local)
            off = max(off, local - rows + 1)
        off = max(0, min(off, len(items) - rows))
        self.top[which] = off
        if not items:
            cv.put(4, top + 2, empty, A_DIM)
        for i, it in enumerate(items[off:off + rows]):
            y = top + 2 + i
            cv.hot_area(2, y, COLS - 4, 1, ("item", base + off + i))
            text, split, sattr, attr = self.row_text(it)
            if base + off + i == self.sel:
                cv.put(2, y, " " * (COLS - 4), A_SEL)
                cv.put(3, y, text, A_SEL)
            else:
                cv.put(3, y, text[:split] if split else text, attr)
                if split:
                    cv.put(3 + split, y, text[split:], sattr)
        if off > 0:
            cv.put(COLS - 16, top, " %s more %s " % (g["more"], g["more"]), A_BORDER)
        if off + rows < len(items):
            cv.put(COLS - 18, top + rows + 2, " %s %d more %s " % (g["more"], len(items) - off - rows,
                                                               g["more"]), A_BORDER)

    def describe(self):
        if not self.items:
            return "No games here have a text screen yet."
        kind, x = self.items[self.sel]
        if kind == "invite":
            return "%s invites you to %s at table %s. Press Enter to join or %s to decline." % (
                x["from"], self.s.game_name(x["key"]), x["id"], self.kn("X"))
        if kind == "new":
            ask = (["rules"] if len(x.get("rulesets") or ()) > 1 else []) + \
                (["players"] if (x.get("max") or 2) > (x.get("min") or 2) else []) + ["time per turn"]
            what = ", ".join(ask[:-1]) + " and " + ask[-1] if len(ask) > 1 else "the " + ask[0]
            for text in ("Enter starts a new %s table; you choose %s." % (x["name"], what),
                         "Enter: a new %s table; you choose %s." % (x["name"], what)):
                if len(text) <= COLS - 2:
                    return text
            return "Enter starts a new %s table." % x["name"]
        opp = [n for n in x["names"] if n != self.s.name]
        seats = [(x["seat_of"][n], n) for n in x["names"]]       # with what each plays (LST clients)
        cl, w = x.get("clients") or {}, COLS - 2
        if self.s.name in x["names"]:
            if x["status"] == "open":
                return "Table %s is waiting for an opponent. Enter shows it." % x["id"]
            if not opp:
                return "Table %s: Enter goes back to your game against ?." % x["id"]
            return with_clients(["Table %s: Enter goes back to your game against %%s." % x["id"],
                                 "Table %s: Enter goes back to your game with %%s." % x["id"]],
                                [(s, n) for s, n in seats if n != self.s.name], cl, ", ", w)
        if x["status"] == "open":
            if not seats:
                return "Table %s: nobody waits for an opponent. Enter sits down, %s watches." % (x["id"], self.kn("W"))
            return with_clients(["Table %s: %%s waits for an opponent. Enter sits down, %s watches." % (
                x["id"], self.kn("W")), "Table %s: %%s waits. Enter sits down, %s watches." % (x["id"], self.kn("W"))],
                seats, cl, ", ", w)
        return with_clients(["Table %s: %%s. Enter or %s watches the game." % (x["id"], self.kn("W")),
                             "Table %s: %%s. Enter or %s watches." % (x["id"], self.kn("W"))], seats, cl, " vs ", w)

    def compose(self):
        cv = Canvas()
        title_bar(cv, self.g, "Main menu", self.s.name)
        moves = [it[1]["id"] for it in self.mine if self.my_move(it)]
        if moves:
            cv.put(1, 1, "It is your move at table %s." % ", ".join(moves[:8]), A_YELLOW)
        elif self.move_set():
            cv.put(1, 1, "Welcome, %s. Choose a table with %s or the arrows, then Enter."
                   % (self.s.name, self.move_set().upper()), A_TEXT)
        else:
            cv.put(1, 1, "Welcome, %s. Choose a table with the arrow keys and press Enter." % self.s.name,
                   A_TEXT)
        self.panel(cv, 0, self.MINE_TOP, self.MINE_ROWS, "Your tables",
                   "%5s  %-8s  %-16s  %-10s  %s" % ("Table", "Game", "Opponent", "Per turn", "Status"),
                   self.mine, 0, "No games here have a text screen yet.")
        self.panel(cv, 1, self.OTHER_TOP, self.OTHER_ROWS, "Other tables",
                   "%5s  %-8s  %-24s  %-10s  %s" % ("Table", "Game", "Players", "Per turn", ""),
                   self.others, len(self.mine), "No other tables yet. Start one, and anyone can join it.")
        cv.put(1, ROWS - 3, self.describe()[:COLS - 2], A_TEXT)
        self.input_line(cv)
        if self.buf:
            status_bar(cv, [("Enter", "run command"), ("Esc", "cancel"), ("Backspace", "erase")])
        else:
            status_bar(cv, [("Arrows", "pick"), ("Enter", "open"), ("N", "new"), ("W", "watch")]
                       + ([("P", "password")] if self.s.has_password else [])
                       + [("O", "keys"), ("?", "help"), ("Q", "quit")])
        return cv

    def cursor_at(self):
        if not self.items:
            return None
        if self.sel < len(self.mine):
            return 2, self.MINE_TOP + 2 + self.sel - self.top[0]
        return 2, self.OTHER_TOP + 2 + self.sel - len(self.mine) - self.top[1]

    # ---- running -----------------------------------------------------------
    async def run(self):
        await self.load()
        await self.show(full=True)
        while True:
            k = await self.key(1.0)
            if k is None:
                if time.time() - self.loaded > MENU_SECS:
                    await self.load()
                await self.show()
                continue
            self.msg = ""
            if await self.on_key(k) == "quit":
                return
            await self.show()

    async def on_key(self, k):
        n = len(self.items)
        if isinstance(k, tuple):
            v = self.hit(k)
            if isinstance(v, tuple) and v[0] == "item" and v[1] < n:
                if self.sel == v[1]:
                    return await self.open_selected()       # a second click opens
                self.sel = v[1]
                return None
            if not isinstance(v, str):
                return None
            if len(v) == 1 and v in self.HOTKEYS:
                return await self.hotkey(v)                 # a status bar key, clicked: never a movement
            k = v
        elif not self.buf:
            k = self.move_of(k) or k                        # a movement letter is an arrow
        if k in ("UP", "DOWN", "PGUP", "PGDN", "HOME", "END"):
            if n:
                step = {"UP": -1, "DOWN": 1, "PGUP": -5, "PGDN": 5, "HOME": -n, "END": n}[k]
                self.sel = max(0, min(n - 1, self.sel + step))
            return None
        if k == "REDRAW":
            self.s.painter.invalidate()
            return None
        if k == "ESC":
            self.buf = ""
            return None
        if k == "BS":
            self.buf = self.buf[:-1]
            return None
        if k == "ENTER" or (k == " " and not self.buf):
            if self.buf:
                line, self.buf = self.buf.strip(), ""
                return await self.run_line(line)
            return await self.open_selected()
        if len(k) != 1:
            return None
        return await self.char_key(k)

    async def options(self):
        """The movement keys: letters that move the cursor as the arrows do, kept with the player for the
        next sign-in (keep_moves)."""
        cur = MOVE_ORDER.index(self.s.moves) if getattr(self.s, "moves", "") in MOVE_ORDER else 0
        i = await self.choose("Movement keys", ["Letters that move the cursor as the arrow",
                                                "keys do; the arrows always work too.",
                                                "The choice is kept for your next visit."],
                              [move_label(m) for m in MOVE_ORDER], cur)
        if i is None:
            return None
        self.s.moves = MOVE_ORDER[i]
        await keep_moves(self.s.core, self.s.moves)
        if not self.move_set():
            return self.say("Movement keys: the arrows only.", A_GREEN)
        return self.say("Movement keys: %s, kept for next time. Press : first to type text."
                        % self.move_set().upper(), A_GREEN)

    async def hotkey(self, k):
        if k == "o":
            return await self.options()
        if k == "q":
            return "quit" if await self.confirm_quit() else None
        if k == "?":
            return await self.help()
        if k == "r":
            await self.relist()
            return self.say("Refreshed.", A_DIM)
        if k == "p" and self.s.has_password:
            return await self.password()
        if k == "n":
            it = self.items[self.sel] if self.items else None
            return await self.new([it[1]["key"].split("/")[0]] if it and it[0] == "new" else [])
        if k == "x":
            it = self.items[self.sel] if self.items else None
            if not it or it[0] != "invite":
                return self.say("Pick an invitation to decline first.")
            return await self.decline(it[1]["id"])
        if k == "w":
            it = self.items[self.sel] if self.items else None
            if not it or it[0] != "table":
                return self.say("Pick a table to watch first.")
            return await self.enter(it[1]["id"], watch=self.s.name not in it[1]["names"])
        return None

    async def open_selected(self):
        if not self.items:
            return None
        kind, x = self.items[self.sel]
        if kind == "new":
            return await self.new([x["key"].split("/")[0]])
        if kind == "invite":
            return await self.enter(x["id"], watch=False, sit=True)
        if self.s.name in x["names"]:
            return await self.enter(x["id"], watch=False)
        return await self.enter(x["id"], watch=x["status"] != "open")

    # ---- MenuBase hooks ------------------------------------------------------
    async def relist(self):
        await self.load()

    async def notice(self, text):
        self.say(text, A_RED if text.startswith(("Can't", "Unknown", "No ")) else A_YELLOW)

    async def back(self, tid):
        """Back from a table: keep it selected."""
        await self.load()
        ids = [self.item_id(it) for it in self.items]
        if tid in ids:
            self.sel = ids.index(tid)
        await self.show()

    async def help(self):
        await self.help_box("Help", MENU_HELP if self.s.has_password else
                            [l.replace("  P  your password   R", "  R").replace("password, ", "")
                             for l in MENU_HELP])

    async def confirm_quit(self):
        return await self.confirm("Log off", ["Log off? Your games stay open and your turn",
                                              "timers keep running."])

    async def show_text(self, title, lines):
        await self.message(title, lines[:HELP_ROWS] or ["Nothing yet."])

    async def ask_machine(self):
        return ((await self.ask_text("Preferred machine", ["The machine you like to play on, shown on",
                                                            "your profile. - clears it."], maxlen=40,
                                     free=True)) or "").strip()

    async def password(self):
        """The server's password-change steps, each in a box (typed as stars)."""
        try:
            await self.s.core.pwd()
            return await self.notice("Nothing to change.")
        except NeedAuth as first:
            ask = first
        except CoreError as e:
            return await self.notice("Can't: " + no_wait(str(e)))
        while True:
            value = await self.ask_text(ask.label, ask.info or [ask.label], maxlen=64, secret=ask.secret)
            if not value:
                return self.say("Your password is not changed.", A_DIM)
            try:
                await self.s.core.aut(ask.field, value)
                return self.say("Your password is changed; you are signed out everywhere else.", A_GREEN)
            except NeedAuth as again:
                ask = again
            except CoreError as e:
                return await self.notice("Can't: " + no_wait(str(e)))

    async def ask_turn(self, game):
        i = await self.choose("New %s table" % game["name"],
                              ["Time per turn. A player whose clock", "runs out loses the game."],
                              [label + ("  (default)" if n == TURN_DEFAULT else "")
                               for n, (_, label) in enumerate(TURNS)], TURN_DEFAULT)
        return None if i is None else TURNS[i][0]

    async def ask_game(self, games):
        i = await self.choose("New table", ["Which game?"], [g["name"] for g in games], 0)
        return None if i is None else games[i]

    async def ask_rules(self, game, rulesets):
        i = await self.choose("New %s table" % game["name"], ["Which rules?"],
                              [games.rules_name(game["key"], r) for r in rulesets], 0)
        return None if i is None else rulesets[i]

    async def ask_players(self, game, lo, hi):
        i = await self.choose("New %s table" % game["name"],
                              ["How many players? The game starts", "when every seat is taken."],
                              ["%d players" % n for n in range(lo, hi + 1)], 0)
        return None if i is None else lo + i


# ---- game screens -------------------------------------------------------------------

class GameBase:
    """One table: the core requests and the event log, shared by the
    full-screen and the plain screens. A game's screens (adapters/games/)
    set parse to the module's parse_view, and describe to its event texts
    first; move_word and play_word make typed moves work."""

    @staticmethod
    def parse(lines):
        raise NotImplementedError("a game's screen sets parse to its parse_view")

    def __init__(self, sess, tid, key, me):
        self.s, self.io, self.core = sess, sess.io, sess.core
        self.tid, self.gkey, self.me = tid, key, me
        self.names, self.log = [], []
        self.clients = {}       # seat -> what its player plays with (VUE C)
        self.told = set()       # seats whose CLIENT event the log has shown
        self.seq = 0
        self.v = None
        self.over = ""
        self.turn_secs = 0
        self.clock_at = time.time()

    def name(self, seat):
        n = self.names[seat] if 0 <= seat < len(self.names) else "-"
        return n if n != "-" else "seat %d" % (seat + 1)

    def describe(self, text):
        return describe_event(text, self.names)

    def move_word(self, cmd, words=()):
        """A typed command -> the move it stands for (sent with MOV: one token or several, space-separated),
        or None if it is not one. cmd is the first word in lower case, words every word as typed."""
        return None

    async def play_word(self, word):
        """Play move_word's answer -> an error text or None."""
        return await self.move(word)

    def on_event(self, text):
        """Called for every new event (the payload after the seq)."""

    async def refresh(self):
        data, _ = await self.core.req("LST table=%s" % self.tid)
        tab = next((t for t in parse_tables(data) if t["id"] == self.tid), None)
        if tab:
            self.names, self.turn_secs = tab["seated"], tab["turn"]
            if self.me >= 0 and self.s.name in tab["seat_of"]:
                self.me = tab["seat_of"][self.s.name]
        elif not self.over:
            self.over = "Game over"
        data, final = await self.core.req("VUE %s GMTC" % self.tid)
        if not final.startswith("OK"):
            return False
        self.clients = parse_clients(data)
        self.v = self.parse([l for l in data if not l.startswith("CLIENT ")])
        self.clock_at = time.time()
        return True

    def client_text(self, text, out, backlog):
        """A CLIENT event: what a seat's player plays with. Right after that seat's JOIN it joins the JOIN's line
        ("ann sat down with GameMaster 1.0.0 on a Macintosh Plus"), which takes the JOIN's place in out;
        later it is a line of its own ("ann now plays with ...": another program, mid-game). A CLIENT with
        nothing to say (a private table's, for a watcher who may not see it) shows nothing."""
        c = parse_client(text)
        if c is None:
            return
        seat, say = c
        self.clients[seat] = say
        self.told.add(seat)
        name = self.name(seat)
        if out and out[-1][0] == "JOIN" and out[-1][1] and self.log and self.log[-1] == out[-1][1] \
                and out[-1][2] == seat:
            d = "%s sat down %s" % (name, say)
            self.log[-1] = d
            out[-1] = ("JOIN", d, None)         # told: a second CLIENT would be a change
            return
        self.log.append("%s %s %s" % (name, "plays" if backlog else "now plays", say))
        out.append(("CLIENT", self.log[-1], seat))

    async def pull(self, since=None):
        """New events -> [(kind, description or None)]; adds to the log."""
        data, final = await self.core.req("SIN %s %d" % (self.tid, self.seq if since is None else since))
        out = []
        for l in data:
            p = l.split(" ", 3)
            if len(p) == 4 and p[0] == "EVT":
                kind = p[3].split()[0]
                f = p[3].split()
                if kind == "CLIENT":
                    self.on_event(p[3])
                    self.client_text(p[3], out, since is not None)
                    continue
                if kind == "JOIN" and len(f) >= 3 and f[1].isdigit() and int(f[1]) < 16:
                    # a seat filled since the last refresh: the events after it (the opening roll) name them
                    self.names += ["-"] * (int(f[1]) + 1 - len(self.names))
                    self.names[int(f[1])] = f[2]
                self.on_event(p[3])
                d = self.describe(p[3])
                if d:
                    self.log.append(d)
                if kind == "RACKS" and len(f) >= 3 and f[1] == "ask" and f[2] != str(self.me) and self.me >= 0:
                    self.log.append("Type racks open to agree, or racks no to refuse.")
                if kind == "END":
                    self.over = d or "Game over"
                out.append((kind, d, int(f[1]) if kind == "JOIN" and f[1:2] and f[1].isdigit() else None))
        if final.startswith("OK") and len(final.split()) > 1:
            self.seq = int(final.split()[1])
        del self.log[:-40]
        return [(k, d) for k, d, _ in out]

    BACKLOG = 12        # events shown in the log on arrival

    async def open_view(self):
        """The table's view and its latest events, when the screen opens (a game's screen may use any
        attribute name of its own except the methods of these classes: Grid Words keeps self.start)."""
        if not await self.refresh():
            return False
        self.seq = self.v["seq"]
        await self.pull(max(0, self.seq - self.BACKLOG))
        # what each other player plays with, when the events shown did not say it (VUE C)
        self.log += ["%s plays %s" % (self.name(i), say) for i, say in sorted(self.clients.items())
                     if i != self.me and i not in self.told and 0 <= i < len(self.names) and self.names[i] != "-"]
        self.log += ["Note: " + n for n in (await self.notes())[-self.NOTES:]]
        return True

    NOTES = 3           # the latest notes, shown in the log when the screen opens

    async def notes(self):
        """The table's notes as lines to show (NOTE)."""
        data, final = await self.core.req("NOTE %s" % self.tid)
        return note_lines(data) if final.startswith("OK") else []

    async def typed_note(self, text):
        """The typed commands notes (or note alone) and note TEXT -> lines to show."""
        if not text:
            return await self.notes() or ["This table has no notes yet."]
        _, final = await self.core.req("NOTE %s %s" % (self.tid, text))
        return ["Noted."] if final.startswith("OK") else ["Can't: " + err_text(final)]

    async def typed_racks(self, word):
        """racks open: ask to open the racks of this game, or agree to another player's request; racks no:
        refuse it (OPR) -> what to say."""
        if self.me < 0:
            return "Only a player seated here can answer that."
        if word not in ("open", "no"):
            return "Type racks open to open the racks, or racks no to refuse."
        _, final = await self.core.req("OPR %s%s" % (self.tid, " no" if word == "no" else ""))
        await self.pull()
        await self.refresh()
        return {"OK open": "The racks are open.", "OK asked": "Asked: the racks open once the others agree.",
                "OK": "You said no to opening the racks."}.get(final, "Can't: " + err_text(final))

    def playing(self):
        return bool(self.v and self.v["rows"]) and not self.over

    def my_turn(self):
        return self.me >= 0 and self.playing() and self.v["turn"] == self.me and bool(self.v["moves"])

    def clock(self):
        c = self.v and self.v.get("clock")
        if not c or self.over:
            return None
        seat, left, limit = c
        return seat, max(0, left - (time.time() - self.clock_at)), limit

    async def move(self, sq):
        _, final = await self.core.req("MOV %s %s" % (self.tid, sq))
        if not final.startswith("OK"):
            return "Can't play %s: %s" % (sq, err_text(final))
        await self.pull()
        await self.refresh()
        return None

    async def staged(self, verb, what):
        """UND or END for games with staged turns."""
        _, final = await self.core.req("%s %s" % (verb, self.tid))
        if not final.startswith("OK"):
            return "Can't %s: %s" % (what, err_text(final))
        await self.pull()
        await self.refresh()
        return None

    async def chat(self, text):
        _, final = await self.core.req("CHT %s %s" % (self.tid, text))
        return None if final.startswith("OK") else "Can't: " + err_text(final)

    async def invite_bot(self):
        """The typed command bot: invite the next bot name to this open table (docs/bot.md) -> what to say."""
        data, _ = await self.core.req("LST table=%s" % self.tid)
        tab = next((t for t in parse_tables(data) if t["id"] == self.tid), None)
        if tab is None or tab["status"] != "open" or self.me < 0:
            return "The bot can be invited only to an open table where you sit."
        evts, _ = await self.core.req("SIN %s 0" % self.tid)
        pending = pending_invites([l.split(" ", 3)[3] for l in evts if l.startswith("EVT ") and l.count(" ") >= 3])
        if tab["seats"] - tab["filled"] <= len(pending):
            return "Every free seat is taken or held for someone invited."
        name = botflag.next_name(tab["names"] + pending)
        if name is None:
            return "No bot name is free at this table."
        _, final = await self.core.req("INV %s %s" % (self.tid, name))
        await self.pull()
        return ("Invited %s: it sits down in a few seconds." % name if final.startswith("OK")
                else "Can't: " + err_text(final))

    async def invite_player(self, name, withdraw=False):
        """The typed commands invite <name> and uninvite <name> at a table where I sit (INV, UNV) -> what to
        say. Whoever is invited sees it in their own frontend (a match offer on IOS, a FIBS invitation)."""
        if self.me < 0:
            return "Only a player seated here can invite."
        if not NAME_RE.match(name):
            return "Names are letters, digits, - and _, up to 12."
        _, final = await self.core.req("%s %s %s" % ("UNV" if withdraw else "INV", self.tid, name))
        await self.pull()
        if final.startswith("OK"):
            return ("You withdrew %s's invitation." % name if withdraw else
                    "You invited %s, who is not online now. It will wait an hour." % name if final == "OK offline" else
                    "You invited %s. It will sit down in a few seconds." % name if botflag.is_bot_name(name) else
                    "You invited %s." % name)
        if final.startswith("ERR NOPLAYER"):
            return "There is no player called %s." % name
        if final.startswith("ERR NOINVITE"):
            return "%s is not invited here." % name
        return "Can't: " + err_text(final)

    async def resign(self):
        _, final = await self.core.req("RES %s" % self.tid)
        err = None if final.startswith("OK") else "Can't: " + err_text(final)
        await self.pull()
        await self.refresh()
        return err


class AnsiGame(GameBase, AnsiView):
    """A game's full screen: the key loop, typed commands, talk, resign and
    help, shared by every game. A game's AnsiScreen (adapters/games/) draws
    the screen in compose() and fills in the hooks: on_arrow, on_click,
    on_enter, on_escape, hotkey, move_word and typed_move, snap."""

    idle = False
    HOTKEYS = "rt?"         # single keys taken as commands on an empty input line
    LOG_TOP, LOG_ROWS = 9, 11
    TITLE = "Game"          # the help box's title
    HELP = []               # the help box's lines

    def __init__(self, sess, tid, key, me):
        GameBase.__init__(self, sess, tid, key, me)
        AnsiView.__init__(self, sess)
        self.was_my_turn = False

    # ---- hooks for the game ------------------------------------------------
    def snap(self):
        """After the view changes: put the cursor somewhere useful."""

    def on_arrow(self, k):
        """UP, DOWN, LEFT or RIGHT."""

    async def on_click(self, value):
        """A click on a hot area the game drew (its value, a tuple)."""

    async def on_enter(self):
        """Enter or Space with nothing typed."""

    def on_escape(self):
        """Esc with nothing typed: "menu" leaves the table."""
        return "menu"

    def typing(self):
        """True while every letter types even on an empty line, so movement letters do not move (Grid
        Words, once a word's start is set)."""
        return False

    def input_line(self, cv):
        """Line 23: what is being typed. A game screen shows its message on the line above, in place of the
        hint, so it is not repeated here."""
        if self.buf:
            cv.put(1, ROWS - 2, "> " + self.buf[-(COLS - 6):], A_BRIGHT)

    async def typed_move(self, word):
        """A typed move (move_word's answer): play it and say what went wrong."""
        err = await self.play_word(word)
        if err:
            self.say(err, A_RED)
        self.snap()

    # ---- drawing helpers -----------------------------------------------------
    def draw_log(self, cv, x, width):
        """The log, newest last, in LOG_ROWS rows from LOG_TOP; chat bright, the rest dim."""
        lines = []
        for entry in self.log:
            attr = A_TEXT if entry.startswith("<") else A_DIM
            lines += [(l, attr) for l in wrap(entry, width)]
        for i, (l, attr) in enumerate(lines[-self.LOG_ROWS:]):
            cv.put(x, self.LOG_TOP + i, l, attr)

    def draw_waiting(self, cv, x0, y0, w=27):
        """The "Waiting for an opponent" box over the board."""
        box(cv, self.g, x0, y0, w, 5, A_POP, double=True, fill=A_POP)
        cv.put(x0 + 2, y0 + 1, "Waiting for an opponent".center(w - 4), A_POP)
        if self.me >= 0 and w - 4 >= len(BOT_HINT):
            cv.put(x0 + 2, y0 + 2, BOT_HINT.center(w - 4), A_POPDIM)
        cv.put(x0 + 2, y0 + 3, ("Table %s is open" % self.tid).center(w - 4), A_POPDIM)

    # ---- running ---------------------------------------------------------
    async def run(self):
        if not await self.open_view():
            return "Can't show that table."
        self.snap()
        await self.show()
        polled = time.time()
        while True:
            k = await self.key(1.0)
            if k is None:
                if time.time() - polled >= POLL_SECS:
                    polled = time.time()
                    if await self.pull():
                        await self.refresh()
                        self.snap()
                await self.show()
                continue
            self.msg = ""
            if await self.on_key(k) == "menu":
                return None
            await self.show()

    async def on_key(self, k):
        if isinstance(k, tuple):
            v = self.hit(k)
            if isinstance(v, tuple):
                return await self.on_click(v)
            if not isinstance(v, str):
                return None
            if len(v) == 1 and v in self.HOTKEYS:
                return await self.hotkey(v)         # a status bar key, clicked: never a movement
            k = v
        elif not self.buf and not self.typing():
            k = self.move_of(k) or k                # a movement letter is an arrow
        if k in ("UP", "DOWN", "LEFT", "RIGHT"):
            return self.on_arrow(k)
        if k == "ESC":
            if self.buf:
                self.buf = ""
                return None
            return self.on_escape()
        if k == "REDRAW":
            self.s.painter.invalidate()
            return None
        if k == "BS":
            self.buf = self.buf[:-1]
            return None
        if k == "ENTER" or (k == " " and not self.buf):
            if self.buf:
                line, self.buf = self.buf.strip(), ""
                return await self.run_line(line)
            return await self.on_enter()
        if len(k) != 1:
            return None
        return await self.char_key(k)

    async def hotkey(self, k):
        """R resigns, T starts a line to the table, ? helps; a game adds its own and calls this."""
        if k == "r":
            return await self.ask_resign()
        if k == "t":
            self.buf = "say "
            return None
        if k == "?":
            return await self.help()
        return None

    async def command(self, line):
        p = line.split()
        if not p:
            return None
        cmd = p[0].lower()
        if cmd in MENU_WORDS:
            return "menu"
        if cmd in ("redraw", "r", "refresh"):
            await self.pull()
            await self.refresh()
            self.snap()
            self.s.painter.invalidate()
        elif cmd in ("help", "?"):
            await self.help()
        elif cmd in ("say", "chat") and len(p) > 1:
            err = await self.chat(" ".join(p[1:]))
            if err:
                self.say(err, A_RED)
            await self.pull()
        elif cmd == "resign":
            await self.ask_resign()
        elif cmd == "bot" and len(p) == 1:
            self.say(await self.invite_bot())
        elif cmd in ("invite", "uninvite") and len(p) == 2:
            self.say(await self.invite_player(p[1], withdraw=cmd == "uninvite"))
        elif cmd in ("notes", "note"):
            lines = await self.typed_note(line.split(None, 1)[1].strip() if cmd == "note" and len(p) > 1 else "")
            if len(lines) == 1:
                self.say(lines[0])
            else:
                await self.message("Notes", lines)
        elif cmd == "racks" and len(p) <= 2:
            self.say(await self.typed_racks(p[1].lower() if len(p) > 1 else ""))
        elif self.me >= 0 and self.move_word(cmd, p):
            await self.typed_move(self.move_word(cmd, p))
        else:
            self.say("Unknown command: %s. Press ? for help." % line[:20], A_RED)
        return None

    async def ask_resign(self):
        if self.me < 0 or not self.playing():
            return self.say("There is no game of yours running here to resign.")
        if await self.confirm("Resign", ["Resign this game? It ends now and counts",
                                         "as a loss. (Leaving never resigns.)"]):
            err = await self.resign()
            if err:
                self.say(err, A_RED)
        return None

    async def help(self):
        await self.help_box(self.TITLE, self.HELP)


class PlainGame(GameBase):
    """A game on a plain terminal: the board printed line by line (lines()),
    a prompt (prompt()), typed moves (move_word, play_word) and the shared
    commands: say, resign, redraw, menu, help."""

    BOARD_KINDS = ("JOIN", "LEAVE", "START", "END")   # events that redraw the board (others are printed)
    HELP_TEXT = ""                                    # what help prints

    def lines(self):
        raise NotImplementedError

    def prompt(self):
        return "> "

    async def draw(self):
        self.io.write("\n".join(self.lines()) + "\n" + self.prompt())
        await self.io.flush()

    async def notice(self, text):
        self.io.write(text + "\n" + self.prompt())
        await self.io.flush()

    async def run(self):
        if not await self.open_view():
            return "Can't show that table."
        await self.draw()
        buf = ""
        while True:
            k = await self.io.readkey(timeout=POLL_SECS)
            if k is None:
                new = await self.pull()
                if any(kind in self.BOARD_KINDS for kind, _ in new):
                    await self.refresh()
                    await self.draw()
                elif any(d for _, d in new):
                    await self.notice("\n".join(" " + d for _, d in new if d))
                continue
            if k == "ENTER":
                line, buf = buf.strip(), ""
                if self.io.server_echo:
                    self.io.write("\n")
                if await self.command(line) == "menu":
                    return None
            elif k == "BS":
                if buf:
                    buf = buf[:-1]
                    if self.io.server_echo:
                        self.io.write("\b \b")
                        await self.io.flush()
            elif k == "ESC":
                return None
            elif len(k) == 1 and len(buf) < 60:
                buf += k
                if self.io.server_echo:
                    self.io.write(k)
                    await self.io.flush()

    async def command(self, line):
        p = line.split()
        if not p or p[0].lower() in ("redraw", "r", "refresh"):
            await self.pull()
            await self.refresh()
            return await self.draw()
        cmd = p[0].lower()
        if cmd in MENU_WORDS:
            return "menu"
        if cmd in ("help", "?"):
            return await self.notice(self.HELP_TEXT)
        if cmd in ("say", "chat") and len(p) > 1:
            err = await self.chat(" ".join(p[1:]))
            new = await self.pull()
            return await self.notice(err or "\n".join(" " + d for _, d in new if d))
        if cmd == "resign" and self.me >= 0:
            self.io.write("Resign this game? (y/N) ")
            await self.io.flush()
            if (await self.io.readline()).lower().startswith("y"):
                err = await self.resign()
                if err:
                    self.log.append(err)
            return await self.draw()
        if cmd == "bot" and len(p) == 1:
            return await self.notice(await self.invite_bot())
        if cmd in ("invite", "uninvite") and len(p) == 2:
            return await self.notice(await self.invite_player(p[1], withdraw=cmd == "uninvite"))
        if cmd in ("notes", "note"):
            lines = await self.typed_note(line.split(None, 1)[1].strip() if cmd == "note" and len(p) > 1 else "")
            return await self.notice("\n".join(" " + l for l in lines))
        if cmd == "racks" and len(p) <= 2:
            return await self.notice(await self.typed_racks(p[1].lower() if len(p) > 1 else ""))
        word = self.move_word(cmd, p)
        if self.me >= 0 and word:
            err = await self.play_word(word)
            if err:
                return await self.notice(err)
            return await self.draw()
        return await self.notice("Unknown: %s. Type help." % line[:20])


# The text screens of every game, from its module in adapters/games/: game type -> (full-screen class, plain
# class). Worked out from the registry when used, so a new game module needs no edit here.
RENDERERS = games.Lookup(lambda m: (m.AnsiScreen, m.PlainScreen))

# Names that moved to adapters/games/ and are still found here (tests and older code use them).
_MOVED = {"AnsiReversi": "reversi", "PlainReversi": "reversi", "AnsiBackgammon": "backgammon",
          "PlainBackgammon": "backgammon", "BgBase": "backgammon", "bg_glyphs": "backgammon",
          "GAME_HELP": "reversi", "BG_HELP": "backgammon"}


def __getattr__(name):
    from importlib import import_module
    if name in _MOVED:
        return getattr(import_module(".games." + _MOVED[name], __package__), name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


# ---- session --------------------------------------------------------------------

class Session:
    def __init__(self, io, core, mode, name, moves="arrows"):
        self.io, self.core, self.mode = io, core, mode
        self.name = name
        self.moves = moves if moves in MOVE_SETS else "arrows"    # the movement keys (kept: keep_moves)
        self.has_password = core.auth == "password"   # only sign-in password has a credential to change here
        self.ansi = mode != "plain"
        self.g = glyph_set(mode)
        self.painter = Painter() if self.ansi else None
        self.games = []
        self.invites = []           # my invitations (IVL), loaded with the tables

    async def load(self):
        """-> (tables, {table id: (seat to move, secs left, turn secs, when)})
        for the tables where this player plays a running game."""
        if not self.games:
            data, _ = await self.core.req("GAM")
            order = list(RENDERERS)         # the menu lists games in a fixed order, Reversi first
            self.games = sorted(group_games(parse_games(data)),     # one entry per game, with its rulesets
                                key=lambda g: order.index(g["key"]) if g["key"] in order else len(order))
        data, final = await self.core.req("LST clients")      # with what each seated player plays
        if not final.startswith("OK"):                          # a core from before LST clients
            data, _ = await self.core.req("LST")
        tabs = parse_tables(data)
        idata, final = await self.core.req("IVL")
        self.invites = parse_invites(idata) if final.startswith("OK") else []
        clocks = {}
        for t in tabs:
            if self.name in t["names"] and t["status"] == "playing":
                vd, final = await self.core.req("VUE %s" % t["id"])
                for l in vd if final.startswith("OK") else []:
                    c = l.split()
                    if c[0] == "CLK" and len(c) >= 4:
                        clocks[t["id"]] = (int(c[1]), int(c[2]), int(c[3]), time.time())
        return tabs, clocks

    async def who_text(self):
        """The players online now (the core's WHO: active in the last presence.idle seconds on any
        frontend, and the bot), as one line."""
        data, final = await self.core.req("WHO")
        if not final.startswith("OK"):
            return "This server does not list who is online."
        players = parse_players(data)
        bots = {n.lower() for n, bot in players if bot}
        names = [botflag.label(n, bots) for n in botflag.listed(players)]
        return ("Online now: " + ", ".join(names))[:COLS - 2] if names else "Nobody is online."

    def game_name(self, key):
        g = next((g for g in self.games if g["key"] == key), None)
        return g["name"] if g else key.split("/")[0].capitalize()

    # ---- profiles and the activity (PFL, FEED; docs/protocol.md) ---------------------------------------------
    async def profile_lines(self, name, width=52):
        """(title, lines) of a player's profile, or your own without a name: the preferred machine, the games
        played and the programs and machines used at public tables, and the latest turns there, each wrapped
        to width; (None, [reason]) when there is no such player."""
        if not self.games:
            await self.load()
        data, final = await self.core.req("PFL %s" % name if name else "PFL")
        if not final.startswith("OK"):
            return None, ["There is no player named %s here." % name if "NOPLAYER" in final else
                          "Can't: " + err_text(final)]
        prof = parse_profile(data)
        out = ["Preferred machine: %s" % (prof["pref"] or "none given")]
        if prof["score"]:
            sc = prof["score"]
            out += textwrap.wrap("Score: %s%s" % (score_text(sc), ", number %d" % sc["rank"] if sc["rank"] else ""),
                                 width, subsequent_indent="  ")
        for label, items in (("Games played", [(self.game_name(k), n) for k, n in prof["games"]]),
                             ("Played with", prof["progs"]), ("On", prof["machs"])):
            if items:
                out += textwrap.wrap("%s: %s" % (label, ", ".join("%s (%d)" % kv for kv in items)), width,
                                     subsequent_indent="  ")
        fdata, final = await self.core.req("FEED 3 %s" % prof["name"])
        turns = parse_turns(fdata) if final.startswith("OK") else []
        if turns:
            out.append("Latest turns:")
        for t in turns:
            out += textwrap.wrap(turn_sentence(t, self.game_name(t["key"])), width, initial_indent="  ",
                                 subsequent_indent="    ")
        return "Profile of %s" % prof["name"], out

    async def score_lines(self, game="", width=52, most=10):
        """The leaderboard (TOP), by every game or one (its type or its name, such as reversi or "grid"): a
        table of the first most players, then the note that scores are easy to get (SCORES_NOTE)."""
        if not self.games:
            await self.load()
        key = ""
        if game:
            low = game.lower()
            key = next((g["key"] for g in self.games if low in (g["key"], g["key"].split("/")[0])
                        or g["name"].lower().replace(" ", "").startswith(low)), "")
            if not key:
                return ["There is no game %s here." % game[:20]]
        data, final = await self.core.req("TOP %d%s" % (most, " game=" + key if key else ""))
        if final.startswith("ERR NOSCORES") or final.startswith("ERR UNKNOWN"):
            return ["This server shows no leaderboard."]
        if not final.startswith("OK"):
            return ["Can't: " + err_text(final)]
        rows = parse_top(data)
        out = ["In %s:" % self.game_name(key)] if key else []
        if rows:
            out.append("%4s %-12s %6s %4s %4s %4s %4s" % ("Rank", "Player", "Points", "Won", "Draw", "Lost", "Res"))
            out += ["%4d %-12s %6d %4d %4d %4d %4d" % (r["rank"], r["name"], r["points"], r["wins"], r["draws"],
                                                      r["losses"], r["resigns"]) for r in rows]
        else:
            out.append("No scores yet: finish a game to get one.")
        return out + textwrap.wrap(SCORES_NOTE, width)

    async def activity_lines(self, width=52, most=5):
        """The latest turns at public tables (FEED), each a sentence wrapped to width."""
        if not self.games:
            await self.load()
        data, final = await self.core.req("FEED %d" % most)
        if not final.startswith("OK"):
            return ["Can't: " + err_text(final)]
        out = []
        for t in parse_turns(data):
            out += textwrap.wrap("%s: %s" % (turn_time(t["at"]), turn_sentence(t, self.game_name(t["key"]))),
                                 width, subsequent_indent="  ")
        return out or ["No turns at public tables yet."]

    def table_state(self, t, clocks):
        """Status of one of this player's tables -> (text, attribute)."""
        if t["status"] == "open":
            return "waiting for an opponent", A_DIM
        c = clocks.get(t["id"])
        if not c:
            return t["status"], A_TEXT
        left = fmt_secs(c[1] - (time.time() - c[3]))
        if c[0] == t["seat_of"].get(self.name):
            return "your move, %s left" % left, A_YELLOW
        return "their move, %s left" % left, A_TEXT

    async def new_table(self, game, turn, rules=None, players=None):
        _, final = await self.core.req("NEW %s %s turn=%s%s" % (game["key"], rules or game["rules"], turn,
                                                              " players=%d" % players if players else ""))
        if not final.startswith("OK"):
            return None, "Can't: " + err_text(final)
        return final.split()[1], None

    async def enter(self, tid, watch=False, sit=False):
        """Open a table's screen (sitting down or watching as asked); returns
        an error text, or None after the player comes back from it. A table
        number alone opens a running game to watch, as the menu's Enter does;
        sit (join n) asks for a seat, and a running game says how to watch it."""
        if not tid.isdigit():
            return "Table numbers are digits."
        data, _ = await self.core.req("LST table=%s" % tid)
        tab = next((t for t in parse_tables(data) if t["id"] == tid), None)
        if tab is None:
            return "No such table."
        if tab["key"] not in RENDERERS:
            return "No text screen for this game yet."
        if self.name in tab["names"]:
            me = tab["seat_of"][self.name]
        else:
            if not watch and tab["status"] != "open":
                if sit:
                    return "Table %s is already playing; type watch %s to watch it." % (tid, tid)
                watch = True
            _, final = await self.core.req("%s %s" % ("SEE" if watch else "JON", tid))
            if not final.startswith("OK"):
                return "Can't: " + err_text(final)
            me = -1 if watch else int(final.split()[1])
        try:
            screen = RENDERERS[tab["key"]][0 if self.ansi else 1](self, tid, tab["key"], me)
            err = await screen.run()
        except LINE_GONE:
            raise
        except Exception:
            log_error("table screen")
            err = SORRY
            if self.painter:
                self.painter.invalidate()       # the menu repaints the whole screen
        if me < 0:
            await self.core.req("LVE %s" % tid)
        return err

    async def run(self):
        mouse = self.ansi and getattr(self.io, "mouse", False)
        if mouse:
            self.io.write(MOUSE_ON)
        try:
            for tries in range(4):
                menu = AnsiMenu(self) if self.ansi else PlainMenu(self)
                if tries:
                    if self.ansi:
                        self.painter.invalidate()
                        menu.say(SORRY, A_RED)
                    else:
                        self.io.write("\n" + SORRY + "\n")
                try:
                    return await menu.run()
                except LINE_GONE:
                    raise
                except Exception:
                    if tries == 3:
                        raise
                    log_error("menu")
        finally:
            if mouse:
                self.io.write(MOUSE_OFF)        # also on a dropped line or an idle timeout


async def answer_asks(io, core, ask, login=False):
    """Answer the server's sign-in questions (ASK lines) until it says OK; returns
    True, or None if the player gives up (an empty answer) or is refused. The
    labels are the auth layer's; secret answers are not echoed. Info lines
    (sign-up, reset) are shown before the question. login: at the login, where
    giving up on the new password after a temporary one is explained."""
    while True:
        for line in ask.info:
            io.write("%s\n" % line)
        io.write("%s (or press Enter to stop): " % ask.label)
        await io.flush()
        value = await io.readline(secret=ask.secret)
        if not value:
            if login and ask.new and not ask.cred:
                io.write("Your password is not chosen yet. Sign in again with the password from the email "
                         "to choose it.\n")
            return None
        try:
            return await core.aut(ask.field, value)
        except NeedAuth as again:
            ask = again
        except CoreError as e:
            io.write("%s.\n" % re.sub(r"^wait=\d+ ", "", str(e)).capitalize().rstrip("."))
            return None


class MotdCache:
    """The message of the day, fetched at most once a minute over a short
    connection of its own, so a client that has not logged in costs the core
    nothing (it shows before the name prompt)."""

    def __init__(self):
        self.lines, self.at, self.lock = [], 0.0, asyncio.Lock()

    async def get(self, args):
        async with self.lock:
            if time.time() - self.at > 60:
                self.at = time.time()
                try:
                    core = await CoreClient.connect(args.core_host, args.core_port)
                    try:
                        self.lines = await core.motd()
                    finally:
                        core.close()
                except (OSError, asyncio.TimeoutError, CoreError):
                    pass
        return self.lines


MOTD = MotdCache()


def terminal_name(ttype):
    """The client program the core records with each turn (CLI PROG): telnet and the terminal type the
    player's program named (TTYPE), such as "telnet (VT100)", or "telnet" when it named none."""
    t = claim_text(ttype or "", CLAIM_LEN - len("telnet ()"))
    return "telnet (%s)" % t if t else "telnet"


LOGIN_PROMPT = 'Login (your name, or "new" for a new account): '
NEW_WORD = "new"


async def sign_in(io, args, ticket=None):
    """The login phase: a name signs in (USER), or "new" starts a sign-up (REG) that asks for the name
    wanted and then whatever the server's sign-up method needs. A name with no account says so and points
    to "new"; it never starts a sign-up. The connection to the core is opened once a valid name (or new)
    has been typed. Returns (core, name), or None if the player left, the core is not answering, or the
    adapter is full."""
    core = None

    async def line(prompt):
        io.write(prompt)
        await io.flush()
        try:
            text = (await asyncio.wait_for(io.readline(), LOGIN_SECS)).strip()
        except asyncio.TimeoutError:
            io.write("\nNo name came; bye.\n")
            await io.flush()
            return None
        if ticket is not None:
            ticket.touch()
        return text

    try:
        while True:
            name = await line(LOGIN_PROMPT)
            if name is None:
                return None
            new = name.lower() == NEW_WORD
            if new:
                io.write("A new account. Names are letters, digits, - and _, up to 12.\n")
                name = await line("Name you want (or press Enter to stop): ")
                if name is None:
                    return None
                if not name:
                    continue
            if not NAME_RE.match(name):
                io.write("Use letters, digits, - and _ only, up to 12.\n")
                continue
            if core is None:
                try:
                    core = await CoreClient.connect(args.core_host, args.core_port)
                    await core.source(peer_addr(io.w))
                    await core.claim(terminal_name(io.ttype))      # what the turns say they were played with
                except (OSError, asyncio.TimeoutError, CoreError):
                    core = None
                    io.write("The game server isn't answering. Try again soon.\n")
                    await io.flush()
                    return None
            try:
                await (core.reg(name) if new else core.user(name))
                break
            except NeedAuth as ask:
                if await answer_asks(io, core, ask, login=True):    # whatever the server's sign-in or sign-up wants
                    break
            except CoreError as e:
                if e.code == "NOPLAYER":
                    io.write('There is no account named %s here. To make one, type new at the Login prompt.\n'
                             % name)
                elif new:
                    io.write("Can't sign up: %s.\n" % re.sub(r"^wait=\d+ ", "", str(e)).rstrip("."))
                else:
                    io.write("Can't sign in: %s\n" % re.sub(r"^wait=\d+ ", "", str(e)))
        if ticket is not None and not ticket.sign_in():
            io.write("Server is full, try again soon.\n")
            await io.flush()
            await core.finish()
            return None
        return core, name
    except BaseException:
        if core is not None:
            await core.finish()
        raise


# ---- the movement keys, kept with the player (PRF: adapters/common.py, docs/protocol.md) ------------------

async def load_moves(core):
    """The player's kept movement keys (a key of MOVE_SETS); "arrows" if none are kept, or for a stand-in
    core without the per-extension data."""
    load = getattr(core, "load_pref", None)
    return await load(MOVE_KEYS_PREF, MOVE_ORDER, "arrows") if load else "arrows"


async def keep_moves(core, moves):
    """Keep the movement keys for the next sign-in; False if the core refused (they still apply now)."""
    save = getattr(core, "save_pref", None)
    return bool(save) and await save(MOVE_KEYS_PREF, moves)


async def ask_moves(io):
    """The movement keys, asked line by line at login -> a key of MOVE_SETS (Enter: the arrows only)."""
    io.write("\nMovement keys: letters that move the cursor as the arrows do (arrows work too).\n")
    for i, m in enumerate(MOVE_ORDER, 1):
        io.write("  %d) %s\n" % (i, move_label(m)))
    io.write("They are kept for your next visit; O in the menu changes them.\nChoice [1]: ")
    await io.flush()
    choice = (await io.readline()).strip()
    numbers = [str(i) for i in range(1, len(MOVE_ORDER) + 1)]
    return MOVE_ORDER[numbers.index(choice)] if choice in numbers else "arrows"


async def main_session(io, args, ticket=None):
    ttype = await io.negotiate()
    guess = mode_for_ttype(ttype)
    io.write("\n")
    motd = await MOTD.get(args)
    for l in framed_motd(motd) or ["ParlorNet: %s" % TAGLINE]:   # the message of the day has its own title
        io.write("%s\n" % l)
    io.write("\n")
    if ttype:
        io.write("Your terminal says it is %s.\n" % ttype)
    try:
        async with asyncio.timeout(LOGIN_TOTAL_SECS):
            signed = await sign_in(io, args, ticket)
    except TimeoutError:
        io.write("\nSigning in took too long; bye.\n")
        await io.flush()
        return
    if signed is None:
        return
    core, name = signed
    try:
        default = guess or "pc"
        io.write("\nDisplay (pick the first line whose sample looks right):\n")
        for i, m in enumerate(ORDER, 1):
            io.write("  %d) %-36s" % (i, MODES[m]))
            if guess != "plain":
                sample = SAMPLE.get(m, SAMPLE["pc"])
                io.raw(sample.encode(CHARSET[m] if m != "plain" else "ascii"))
            io.write("%s\n" % ("   <- default" if m == default else ""))
        io.write("Choice [%d]: " % (ORDER.index(default) + 1))
        await io.flush()
        choice = (await io.readline()).strip()
        mode = ORDER[int(choice) - 1] if choice in ("1", "2", "3", "4") else default
        io.charset = CHARSET[mode]
        if mode != "plain":
            io.char_mode()
            io.mouse = mouse_for_ttype(ttype)
        offer = mode != "plain" and not arrows_for_ttype(ttype)
        io.write("\nHello, %s.\n" % name)
        io.write("To play the bot, start a table and type bot while you wait for an opponent.\n")
        if offer:
            io.write("Your terminal may not send arrow keys; letters can move the cursor instead.\n")
        moves = await load_moves(core) if mode != "plain" else "arrows"
        if moves != "arrows":
            io.write("Movement keys: %s, as you chose before (O in the menu changes them).\n" % moves.upper())
        io.write("Press Enter for the menu%s." % (", or K and Enter to choose movement keys" if offer else ""))
        await io.flush()
        if (await io.readline()).strip().lower() in ("k", "keys") and mode != "plain":
            moves = await ask_moves(io)
            await keep_moves(core, moves)
        io.on_input = core.user_input           # every key from now on is activity (CoreClient.user_input)
        await Session(io, core, mode, name, moves).run()
        io.write((CLEAR if mode != "plain" else "\n") + "Bye. Your games stay open; come back any time.\n")
        await io.flush()
    finally:
        await core.finish()     # the session lives only as long as the telnet connection


# The protocol this extension serves, declared to the core (adapters/declare.py): key, name, description.
PROTOCOL = ("TELNET", "Telnet with ANSI escape codes", "Full-screen menus and boards for terminal programs")


async def serve(args):
    gate = Gate(MAX_SESSIONS)

    async def handle(reader, writer):
        ticket = gate.admit(peer_addr(writer), writer)
        if ticket is None:
            writer.write(b"Server is full, try again soon.\r\n")
            writer.close()
            return
        keepalive(writer)
        io = TelnetIO(reader, writer)
        try:
            await main_session(io, args, ticket)
        except (EOFError, ConnectionError, asyncio.TimeoutError, CoreError, OSError):
            pass
        except Exception:
            log_error("session")
            try:
                writer.write(b"\r\nSomething went wrong; please connect again.\r\n")
                await asyncio.wait_for(writer.drain(), 2)
            except Exception:
                pass
        finally:
            ticket.release()
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("telnet adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.telnet")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6502)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    declare.add_args(ap)
    a = ap.parse_args(argv)
    if a.key:
        CoreClient.key_file = a.key
    a.core_host, _, p = a.core.rpartition(":")
    a.core_port = int(p)
    return a


if __name__ == "__main__":
    from adapters import telnet as _telnet     # run the importable module: the one the game modules import
    try:
        asyncio.run(_telnet.serve(_telnet.parse_args()))
    except KeyboardInterrupt:
        pass
