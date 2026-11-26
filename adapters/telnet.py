"""Telnet adapter: one telnet server for every game on the parlord behind it.

A client of the native protocol (adapters/common.py). The screen is 80x24.
Display modes, picked from the terminal type the client reports (telnet
TTYPE), or from a menu:

    pc     ANSI color and IBM PC (CP437) characters (ANSI and BBS terminals)
    utf8   ANSI color and the same characters in Unicode (xterm and friends)
    ansi   ANSI color, ASCII only (VT100 class)
    plain  no escape codes; line by line, moves are typed

The ANSI modes are full screen: the lobby and a board driven by the arrow keys,
Enter and the shortcut letters listed on the keys line. Terminals that report the mouse
(xterm and friends: DECSET 1000 with SGR 1006 coordinates) can click too. Screens are composed as a grid
of cells and only the cells that changed are sent, so a cursor move or a
clock tick costs a few bytes (usable at 2400 baud). No line feed is sent and
the last cell of the screen is never written, so the screen never scrolls.

Every screen has two modes. In game mode every key is a shortcut and nothing
types. The / key opens command mode on row 22, where everything types: Enter
runs the line, Esc clears it, Up and Down recall earlier lines, and the lit
keys go dark. A line runs on the server with CMD (docs/protocol.md, Commands):
the shared commands (/say, /invite, /time ...) and typed moves (/d3) get the
server's words, and a command that asks first opens its dialog with them.
Telnet keeps only its own screens (/help, /leave, /practice ...). T or Tab opens a
table's chat screen, where a line is said to the table. ? opens help, and Esc
cancels what is picked or open, and with nothing to cancel goes back to the
lobby.

Movement keys: terminals that send no arrow keys (or lose them) can move the
cursor with letters instead, a choice of the session (/keys in the lobby, or K at
the login's last prompt): IJKL, ESDF, WASD or HJKL (vi). The arrows work in
every choice. The four letters, either case, move the cursor in the lobby,
the pickers and every board. A shortcut on one of them is given as its
command (/double for D under WASD). The choice is kept with the player (the
core's per-extension data, PRF) and comes back at the next sign-in.

It starts by asking for the terminal type. For a few seconds one line says
that Enter opens the display menu. Without Enter it goes on in the mode the
terminal type suggests, or in plain when the terminal names none. Then comes
ParlorNet's title (adapters/title.py), PARLOR over NET in block letters, as a
page of its own until Enter. Before the login prompt it shows the server's message of the
day. At the prompt a name signs in; "new" makes a new account (the name
wanted, then the server's sign-up steps). A name with no account says so and
points to "new". Passwords are never echoed (TelnetIO.read_secret). The ANSI
screens show how long the player has been signed in at the right end of the
title bar, and the plain menu shows it in its header line.

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

from parlord.auth import NEW_NAME_RULE

from . import declare
from .common import (framed_motd, LOGIN_SECS, LOGIN_TOTAL_SECS, CLAIM_LEN, CoreClient, CoreError, Gate, claim_text,
                     keepalive, NeedAuth,
                     no_wait, describe_invite_event, err_text, group_games, parse_games, parse_invites,
                     parse_reversi_view, parse_players, parse_tables, peer_addr, MOVE_KEYS_PREF, PRACTICE_PREF,
                     PRACTICE_STATES,
                     describe_integrity_event, parse_turns,
                     turn_sentence, turn_time)
from .common import CUT_ENDS, fit, parse_client, parse_clients
from .chatcmd import note_lines
from . import botflag
from . import games
from .webstyle import TAGLINE
from . import title

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
ECHO, SGA, TTYPE, NAWS = 1, 3, 24, 31
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
SQUARE_RE = re.compile(r"^[a-z]\d{1,2}\Z")
TURN_RE = re.compile(r"^\d+[smhd]?\Z", re.I)
PLAYERS_RE = re.compile(r"^(?:players=)?(\d{1,2})p\Z", re.I)    # a seat count typed after new: 3p
MAX_SESSIONS = 60
IDLE_SECS = 1800
COLS, ROWS = 80, 24
ENTER_WAIT = 0.3        # after a Y or N answer, an Enter this soon is one typed out of habit
PRACTICE_LINE = "Type /practice for a short lesson on these keys."
POLL_SECS = 2.0         # game screen: ask the core for new events this often
MENU_SECS = 15.0        # menu: reload the table list this often
DETECT_SECS = 3.0       # the start: how long telnet waits for the terminal type, and for Enter to open the menu
ECHO_WAIT = 0.5         # before a password: how long telnet waits for the client to agree to stop echoing
START_TEXT = "Press Enter to choose a terminal type."      # 38 columns, so it fits a 40-column screen
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
QUIT_WORDS = ("quit", "q", "bye", "exit", "logoff", "logout")
SORRY = "Something went wrong. You are back in the lobby."
PLAIN_PRACTICE = "The practice game teaches the keys of the full screen, so plain mode doesn't have it."
LINE_GONE = (EOFError, ConnectionError, TimeoutError, CoreError, OSError)   # the line or the core went away
# Movement keys: the session's choice -> (its name, the letters for up, down, left and right). The arrows
# work in every choice; "arrows" (no letters) is the default.
MOVE_SETS = {"arrows": ("Arrows only", ""), "ijkl": ("IJKL", "ikjl"), "esdf": ("ESDF", "edsf"),
             "wasd": ("WASD", "wsad"), "hjkl": ("HJKL (vi)", "kjhl")}
MOVE_ORDER = ["arrows", "ijkl", "esdf", "wasd", "hjkl"]
DIRECTIONS = ("UP", "DOWN", "LEFT", "RIGHT")
# The shortcut letters and their one meaning in every game (docs/design.md, One meaning per letter). A game
# uses the ones it needs; any other letter does nothing at a table. Y and N answer dialogs, and only dialogs.
SHORTCUTS = {"b": "split", "c": "check", "d": "double or draw", "e": "end the turn", "g": "random",
             "h": "home board", "m": "new set", "o": "sort", "p": "pass", "r": "resign", "t": "chat",
             "u": "undo", "v": "letter values", "x": "exchange", "z": "reset"}
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
        self.echo_answer = asyncio.Event()  # set when the client answers DO or DONT ECHO
        self.echo_acks = 0          # DONT ECHO answers still to come for a WONT ECHO sent (not new answers)
        self.talks_telnet = False   # the client has sent a telnet command (it answers negotiation)
        self.ansi = False           # the display mode takes escape codes (set by main_session)
        self.since = None           # when the player signed in, for the time connected (set by main_session)
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

    async def detect(self, secs=None):
        """The start: ask for the terminal type and the window size, and say on one line that Enter opens
        the display menu. It waits secs in all, so the player has time to press Enter, and returns True if
        Enter came. Anything else typed meanwhile is dropped."""
        secs = DETECT_SECS if secs is None else secs
        self.raw(bytes([IAC, DO, TTYPE, IAC, DO, NAWS]))
        self.write(START_TEXT + "\n")
        await self.flush()
        end, asked = time.time() + secs, False
        while "ENTER" not in self.keys:
            if self.will_ttype.is_set() and not self.ttype_event.is_set() and not asked:
                self.raw(bytes([IAC, SB, TTYPE, 1, IAC, SE]))
                await self.flush()
                asked = True
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
        chose = "ENTER" in self.keys
        self.keys.clear()
        if asked and not self.ttype_event.is_set():     # a type on its way gets a moment more
            await self._pump_until(self.ttype_event, 1.0)
            self.keys.clear()
        return chose

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
                self.talks_telnet = True
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
                    self.echo_answer.set()
                elif b == ECHO and self.cmd == DONT and self.echo_acks:
                    self.echo_acks -= 1             # the client agrees to a WONT ECHO sent before
                elif b == ECHO and self.cmd == DONT:
                    self.server_echo = False
                    self.echo_answer.set()
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
        elif b == 9:
            self.keys.append("TAB")
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

    async def read_secret(self, maxlen=72):
        """A secret line, such as a password. Telnet never writes what is typed back: it asks the client to
        leave the echo to it (WILL ECHO) and then shows a star for each key. A client that keeps echoing
        on an ANSI screen has the typed text hidden (SGR 8, concealed). The client echoes again afterwards
        (WONT ECHO) unless the screen was in character mode before."""
        asked = not self.server_echo
        if asked:
            self.echo_answer.clear()
            self.raw(bytes([IAC, WILL, ECHO]))
            await self.flush()
            if self.talks_telnet:
                await self._pump_until(self.echo_answer, ECHO_WAIT)
        hide = self.ansi and not self.server_echo
        if hide:
            self.write("\x1b[8m")
            await self.flush()
        try:
            return await self.readline(maxlen, secret=True)
        finally:
            if hide:
                self.write("\x1b[0m")
            if asked and self.server_echo:
                self.raw(bytes([IAC, WONT, ECHO]))
                self.server_echo = False
                self.echo_acks += 1
            await self.flush()


# ---- text helpers (also used by adapters/web.py) -------------------------------

def fmt_secs(s):
    s = max(0, int(s))
    if s >= 86400:
        return "%dd %02dh" % (s // 86400, s % 86400 // 3600)
    if s >= 3600:
        return "%dh %02dm" % (s // 3600, s % 3600 // 60)
    return "%d:%02d" % (s // 60, s % 60)


def connected_text(secs):
    """How long the player has been connected, in whole minutes as the 8-bit clients show it: 0m, 12m,
    then 1h 5m from an hour on."""
    m = max(0, int(secs)) // 60
    return "%dh %dm" % (m // 60, m % 60) if m >= 60 else "%dm" % m


def left_words(secs):
    """Time left on a clock, as the sentences say it: 4:12 under an hour, then 1h 5m, then 2d 3h."""
    s = max(0, int(secs))
    if s >= 86400:
        return "%dd %dh" % (s // 86400, s % 86400 // 3600)
    if s >= 3600:
        return "%dh %dm" % (s // 3600, s % 3600 // 60)
    return "%d:%02d" % (s // 60, s % 60)


def turn_words(secs):
    """The time a turn has, in words: 5 minutes, 1 hour, 3 days."""
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if secs >= size and secs % size == 0:
            n = secs // size
            return "%d %s%s" % (n, unit, "" if n == 1 else "s")
    return left_words(secs)


def and_list(items):
    """ann; ann and bob; ann, bob and cy."""
    items = list(items)
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def or_list(items):
    """13; 13 or 24; 6, 8 or 13."""
    items = list(items)
    return ", ".join(items[:-1]) + " or " + items[-1] if len(items) > 1 else "".join(items)


def sentence(text):
    """A reason from the server, such as "not your turn", as a sentence: "Not your turn." """
    text = (text or "").strip().rstrip(".")
    return (text[:1].upper() + text[1:] + ".") if text else ""


def unknown_command(word):
    """The answer to a command that does not exist, the same in every mode."""
    return "There is no command called /%s. Type /help for help." % word.lstrip("/:")[:20]


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
    return describe_invite_event(text) or describe_integrity_event(text)


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
    """The top line: ParlorNet, the screen's name, the right-hand text, and at the right end the time
    connected (DRAWING_CLOCK) when the screen is drawn for a session. The time changes once a minute, so
    redrawing it costs only the few cells that change."""
    cv.put(0, 0, " " * COLS, A_TITLE)
    cv.put(1, 0, "ParlorNet", A_TITLE_HI)
    on = DRAWING_CLOCK.get()
    room = COLS - 1 - 13 - (len(on) + 2 if on else 0) - (len(right) + 3 if right else 0)
    if len(text) > room:            # long names: cut at a word, so the clock keeps its place
        text = text[:room].rsplit(" ", 1)[0].rstrip(",") if " " in text[:room] else text[:room]
    cv.put(11, 0, g["v"] + " " + text, A_TITLE)
    end = COLS - 1          # the right-hand text ends just before this column
    if on:
        cv.put(COLS - 1 - len(on), 0, on, A_TITLE)
        end -= len(on) + 3
        if right:
            cv.put(end + 1, 0, g["v"], A_TITLE)
    right = right[:max(0, end - 15 - len(text))]
    cv.put(end - len(right), 0, right, A_TITLE)


STATUS_KEYS = {"Enter": "ENTER", "Esc": "ESC", "Backspace": "BS"}
# The movement keys of the screen being composed (a key of MOVE_SETS, "" for arrows only): set by
# AnsiView.screen() around compose(), so every game's status bar shows the keys that apply.
DRAWING_MOVES = contextvars.ContextVar("parlornet_moves", default="")
# The time connected for the title bar (connected_text), set the same way: "" draws none.
DRAWING_CLOCK = contextvars.ContextVar("parlornet_clock", default="")


def status_bar(cv, items, lit=True):
    """The keys line: keys and what they do. 79 columns: the last cell of the screen is never written, so
    nothing can scroll. In game mode (lit) the key names are lit and a click on an item counts as its key.
    In command mode nothing is lit and nothing can be clicked. With movement letters, "Arrows" shows the
    letters. An item whose key is a command (/double) stands for a shortcut letter that moves the cursor:
    a click on it gives the shortcut."""
    letters = MOVE_SETS.get(DRAWING_MOVES.get(), ("", ""))[1]
    cv.put(0, ROWS - 1, " " * (COLS - 1), A_STATUS)
    x = 1
    for item in items:
        key, label = item[0], item[1]
        if letters and key == "Arrows":
            key = DRAWING_MOVES.get().upper()
        width = len(key) + (1 + len(label) if label else 0)
        if x + width > COLS - 2:
            break
        cv.put(x, ROWS - 1, key, A_KEY if lit else A_STATUS)
        if label:
            cv.put(x + len(key) + 1, ROWS - 1, label, A_STATUS)
        k = item[2] if len(item) > 2 else STATUS_KEYS.get(key, key.lower() if len(key) == 1 else None)
        if k and lit:
            cv.hot_area(x, ROWS - 1, width, 1, k)
        x += width + 2


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
        """An attribute's parts: bold, reverse video, and the foreground and background colors as their
        SGR codes (None for the default). Colors are 30-37 and 40-47, the bright 90-97 and 100-107, and
        38;5;n and 48;5;n from the 256-color palette."""
        codes = attr.split(";") if attr else []
        bold = rev = False
        fg = bg = None
        i = 0
        while i < len(codes):
            c = codes[i]
            if c in ("38", "48") and i + 2 < len(codes) and codes[i + 1] == "5":
                if c == "38":
                    fg = ";".join(codes[i:i + 3])
                else:
                    bg = ";".join(codes[i:i + 3])
                i += 3
                continue
            if c == "1":
                bold = True
            elif c == "7":
                rev = True
            elif len(c) == 2 and c[0] in "39":
                fg = c
            elif (len(c) == 2 and c[0] == "4") or (len(c) == 3 and c[:2] == "10"):
                bg = c
            i += 1
        return bold, rev, fg, bg

    def _sgr(self, out, attr):
        """Switch to attr, sending only what changes when nothing has to be
        turned off. Turning bold or reverse video off portably takes a full reset."""
        if attr == self.attr:
            return
        b0, r0, f0, g0 = self._parts(self.attr) if self.attr is not None else (True, True, None, None)
        b1, r1, f1, g1 = self._parts(attr)
        if not attr or (b0 and not b1) or (r0 and not r1) or (f0 and not f1) or (g0 and not g1):
            out.append("\x1b[%sm" % (attr or "0"))
        else:
            codes = [c for c, on in (("1", b1 and not b0), ("7", r1 and not r0), (f1, f1 != f0), (g1, g1 != g0))
                     if on]
            if codes:       # an empty list would be ESC [ m, which resets everything
                out.append("\x1b[%sm" % ";".join(codes))
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
        self.hist = None        # the history line shown on the command line (Up and Down), or None
        self.last_key = time.time()

    def compose(self):
        raise NotImplementedError

    def compose_screen(self):
        return self.compose()

    def cursor_at(self):
        return None

    def cursor_spot(self):
        """Where the terminal's cursor sits when a typing line other than the command line is open."""
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

    def kn(self, letter, command=""):
        """How to give a shortcut, after "Press Enter to ..., or": D, or "type /double" when D moves the cursor."""
        return "type /" + command if command and self.move_of(letter) else letter.upper()

    def kp(self, letter, command=""):
        """The same, where the sentence has no Press of its own: "press E", or "type /end"."""
        return "type /" + command if command and self.move_of(letter) else "press " + letter.upper()

    def keys_help(self):
        """Lines on the movement letters, for help ([] with the arrows only)."""
        m = self.move_set()
        if not m:
            return []
        up, down, left, right = MOVE_SETS[m][1].upper()
        return ["%s moves up, %s left, %s down and %s right, and the arrows work too." % (up, left, down, right)]

    async def help_box(self, title, lines, foot=None):
        """A help box of one page, with the movement letters first when the session has them."""
        extra = self.keys_help()
        await self.pages(title, [(extra + [""] if extra else []) + list(lines)])

    async def hotkey(self, k):
        return None

    async def command(self, line):
        return None

    def commanding(self):
        """True while the command line is open (command mode)."""
        return self.buf.startswith("/")

    async def command_key(self, k):
        """A key in command mode: everything types on the command line. Enter runs it, Esc clears it,
        Backspace erases, and Up and Down recall the lines run before. ? on an empty line opens help."""
        if k == "ESC":
            self.buf, self.hist = "", None
        elif k == "BS":
            self.buf = self.buf[:-1]
        elif k == "ENTER":
            line, self.buf, self.hist = self.buf.strip(), "", None
            if line != "/":
                hist = self.s.history
                if not hist or hist[-1] != line:
                    hist.append(line)
                    del hist[:-50]
                return await self.command(line[1:].strip())
        elif k in ("UP", "DOWN"):
            hist = self.s.history
            if hist:
                i = len(hist) if self.hist is None else self.hist
                i = max(0, i - 1) if k == "UP" else i + 1
                self.hist = i if i < len(hist) else None
                self.buf = hist[i] if i < len(hist) else "/"
        elif k == "REDRAW":
            self.s.painter.invalidate()
        elif k == "?" and self.buf == "/":
            self.buf = ""
            return await self.help()
        elif isinstance(k, str) and len(k) == 1 and len(self.buf) < 60:
            self.buf += k
        return None

    def command_keys(self, cv, back="the game"):
        """The keys line in command mode: nothing lit."""
        status_bar(cv, [("Enter", "run it"), ("Esc", "back to " + back), ("Backspace", "erase")], lit=False)

    def hit(self, k):
        """What a ("CLICK", x, y) event landed on in the last screen sent:
        a key name, a view-specific tuple, or None."""
        cv = self.s.painter.prev if self.s.painter else None
        if cv is None or not isinstance(k, tuple):
            return None
        return cv.hit(k[1], k[2])

    def screen(self):
        token = DRAWING_MOVES.set(self.move_set())      # the status bar shows the session's keys
        since = getattr(self.s, "since", None)
        clock = DRAWING_CLOCK.set(connected_text(time.time() - since) if since else "")
        try:
            cv = self.compose_screen()
        finally:
            DRAWING_MOVES.reset(token)
            DRAWING_CLOCK.reset(clock)
        pos = (min(1 + len(self.buf), COLS - 2), ROWS - 2) if self.buf else (
            self.cursor_spot() or self.cursor_at())
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
        """Row 22: the command line in command mode, or else the latest message."""
        if self.buf:
            cv.put(1, ROWS - 2, self.buf[-(COLS - 3):], A_BRIGHT)
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

    async def swallow_enter(self):
        """After a Y/N answer, drop an Enter typed out of habit."""
        if await self.io.poll(ENTER_WAIT) and self.io.keys[0] == "ENTER":
            self.io.keys.pop(0)

    async def confirm(self, title, lines, verb=None, yes=None):
        """A confirmation: Yes and No, starting on No. Esc is No. True for Yes."""
        return await self.dialog(title, lines, ("Yes", "No"), 1, "Press Y for yes, or N for no.") is True

    async def dialog(self, title, lines, labels, start, foot):
        """A dialog over the screen with two buttons, answered inside itself: Y presses the first button and N
        the second, Left, Right and Tab move between them, and Enter or Space presses the one lit. Esc cancels:
        None. A click presses a button. Returns True for the first button, False for the second."""
        sel = start
        try:
            while True:
                buttons = [((" [%s] " if i == sel else "  %s  ") % label, "y" if i == 0 else "n")
                           for i, label in enumerate(labels)]
                self.overlay = lambda cv: popup(cv, self.g, title, lines, foot=foot, buttons=buttons)
                await self.show()
                k = await self.io.readkey()
                if isinstance(k, tuple):
                    k = self.hit(k) or ""
                if k in ("LEFT", "RIGHT", "TAB"):
                    sel = 1 - sel
                elif k in ("ENTER", " "):
                    answer = sel == 0
                    break
                elif k in ("y", "Y", "n", "N"):
                    answer = k in ("y", "Y")
                    break
                elif k == "ESC":
                    answer = None
                    break
        finally:
            self.overlay = None
        await self.swallow_enter()
        await self.show()
        return answer

    async def choose(self, title, lines, options, sel):
        foot = "Pick one with %s and Enter, or 1 to %d. Esc goes back." % (
            self.move_set().upper() or "the arrows", len(options))
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
                if secret and not getattr(self.io, "server_echo", True):
                    self.io.write("\x1b[8m")       # a client that echoes by itself shows nothing it echoes
                    self.s.painter.attr = None      # and the next screen starts with a full reset
                    await self.io.flush()
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

    async def pages(self, title, pages, page=1):
        """Help in pages, in one box: "Grid Words help, page 1 of 2". Space and Right turn to the next page
        (Space goes round to the first), Left turns back, and Esc or Enter closes it."""
        n, i = len(pages), max(0, min(page, len(pages)) - 1)
        try:
            while True:
                foot = ("Press Space for page %d, or Esc to close." % ((i + 1) % n + 1) if n > 1 else
                        "Press Esc to close.")
                head = "%s, page %d of %d" % (title, i + 1, n) if n > 1 else title
                self.overlay = lambda cv: popup(cv, self.g, head, pages[i][:HELP_ROWS], foot=foot)
                await self.show()
                k = await self.io.readkey()
                if k in ("ESC", "ENTER") or isinstance(k, tuple):
                    break
                if k in (" ", "RIGHT", "PGDN"):
                    i = (i + 1) % n if k == " " else min(n - 1, i + 1)
                elif k in ("LEFT", "PGUP"):
                    i = max(0, i - 1)
        finally:
            self.overlay = None
        await self.show()

    async def message(self, title, lines, foot=None):
        self.overlay = lambda cv: popup(cv, self.g, title, lines, foot=foot or "Press any key (or click).")
        await self.show()
        try:
            await self.io.readkey()
        finally:
            self.overlay = None
        await self.show()


# ---- commands run on the server (CMD; docs/protocol.md, Commands) ------------------------------------

CMD_MAX = 60            # a command line's characters, its slash included (the request is at most 80 bytes)
DIALOG_WIDTH = 52       # a confirmation's words are wrapped to this in its dialog
# The confirmations the server asks for (DLG kind): the dialog's title. Its words come with the DLG line.
DIALOG_TITLES = {"resign": "Resign", "pass": "Pass", "end": "End turn", "quit": "Sign out"}
# A command whose answer is several lines shows them in a box under this title.
BOX_TITLES = {"who": "Who is online", "tables": "Tables", "top": "Leaderboard", "score": "Score",
              "motd": "Message of the day", "list": "Your games and the open tables", "note": "Notes",
              "time": "Time", "help": "Commands"}


def box_title(line):
    """The box title for the answer to a command line (without its slash)."""
    word = (line.split() or [""])[0].lower()
    if word == "who" and len(line.split()) > 1:
        return line.split()[1]
    return BOX_TITLES.get(word, "ParlorNet")


def dialog_lines(text, width=DIALOG_WIDTH):
    """A dialog's words in lines of at most width, broken between sentences when every sentence fits on a
    line, else wrapped at spaces."""
    parts = re.split(r"(?<=[.?!]) +", text.strip())
    if any(len(p) > width for p in parts):
        return textwrap.wrap(text, width)
    out = []
    for part in parts:
        if out and len(out[-1]) + 1 + len(part) <= width:
            out[-1] += " " + part
        else:
            out.append(part)
    return out


def answer_lines(lines, width=COLS - 2):
    """The lines a command answered, without the empty ones at the end. One paragraph that fits in width is
    joined into one line, since the server wraps its lines narrower than the screen."""
    lines = list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    one = " ".join(l.strip() for l in lines)
    if len(lines) > 1 and all(l.strip() for l in lines) and len(one) <= width:
        return [one]
    return lines


# ---- menus ------------------------------------------------------------------------

LOBBY_HELP = [         # the lobby's one page of help, on the full screen
    "Moving",
    "The arrows pick a table, and Enter opens it. Enter on your own game",
    "goes back to it, on an open table sits down, on another game watches",
    "it, and on an invitation lets you accept or decline it.",
    "",
    "Keys for the lobby",
    "N opens a new table. ? opens this help, and / opens the command line.",
    "",
    "Commands",
    "/open alone asks the questions N asks. /open GAME opens a table of that",
    "game, and /invite bot GAME or /invite NAME GAME opens one to play the",
    "bot or a player. /join TABLE or /join NAME sits down, /resume and",
    "/watch take a table, and /close closes your open tables. /who, /tables,",
    "/top, /score, /motd, /time and /activity tell you things. /password,",
    "/machine and /keys change your settings.",
    "",
    "Signing out",
    "/quit signs you out, after asking first. Your games go on without you.",
]

PLAIN_LOBBY_HELP = [   # plain mode's: in the lobby every line is a command, and the slash may be left off
    "Commands",
    "/open asks which game, the rules, the players and the time per turn,",
    "and opens a table anyone can join. /open GAME opens one of that game.",
    "/invite bot GAME opens a table to play the bot, and /invite NAME GAME",
    "opens one and invites that player.",
    "/join TABLE or /join NAME sits down at an open table, and /join NAME",
    "takes NAME's invitation.",
    "/resume goes back to your game where it is your turn, and /resume",
    "TABLE goes back to that table. /watch TABLE watches a game.",
    "/close closes your open tables where nobody has joined you.",
    "/who lists who is online, and /who NAME tells about one player.",
    "/tables NAME lists a player's tables. /top shows the leaderboard,",
    "and /score NAME shows a player's score.",
    "/motd shows the message of the day, and /time the time signed in.",
    "/activity shows the latest turns, and /machine sets your machine.",
    "/password changes your password, and /list shows the tables again.",
    "/quit signs you out, after asking first. Your games go on without you.",
    "In the lobby the slash may be left off.",
]


class MenuBase:
    """Typed menu commands, shared by the plain and the full-screen menu."""

    async def command(self, line):
        """A lobby command, with or without its slash (docs/design.md, The lobby). The shared commands run on
        the server (shared); the lobby's own screens and settings are here."""
        if line.startswith("/"):
            line = line[1:]
        p = line.split()
        if not p:
            return await self.relist()
        cmd, args = p[0].lower(), p[1:]
        rest = line.split(None, 1)[1].strip() if len(p) > 1 else ""
        if cmd == "help":
            return await self.help()
        if cmd == "list":                       # the lobby is the list: shown again
            return await self.relist()
        if cmd == "password" and self.s.has_password:
            return await self.password()
        if cmd == "keys" and self.s.ansi:
            return await self.options()
        if cmd == "practice":
            if not self.s.ansi:
                return await self.notice(PLAIN_PRACTICE)
            await self.s.practice()
            return await self.relist()
        if cmd == "open" and not args:          # the questions N asks
            return await self.new([], whom="anyone")
        if cmd in ("resume", "watch"):
            return await self.table_command(cmd, args)
        if cmd == "join" and len(args) == 1:      # a table of yours already: back to it, as /resume does
            mine = [t for t in (await self.s.load())[0] if t["id"] == args[0] and self.s.name in t["names"]]
            if mine:
                return await self.enter(mine[0]["id"], watch=False)
        if cmd == "machine":
            return await self.machine(rest or None)
        if cmd == "activity":
            return await self.show_text("Activity", await self.s.activity_lines())
        return await self.shared(line)

    async def shared(self, line):
        """A shared command, run on the server (CMD). One that seats you at a new table takes you there."""
        cmd = line.split()[0].lower()
        seats = cmd in ("open", "invite", "join")
        before = {t["id"] for t in (await self.s.load())[0] if self.s.name in t["names"]} if seats else set()
        lines = answer_lines(await self.s.command(self, line))
        if self.s.quitting:
            return "quit"
        if seats:
            tabs, _ = await self.s.load()
            new = [t for t in tabs if self.s.name in t["names"] and t["id"] not in before]
            if new:
                return await self.enter(new[0]["id"], watch=False)
        if cmd in ("open", "invite", "join", "close", "uninvite"):
            await self.relist()
        if len(lines) == 1:
            return await self.notice(lines[0])
        if lines:
            return await self.show_text(box_title(line), lines)
        return None

    async def ask_yes(self, title, lines, verb, yes=None):
        """A confirmation the server asked for: Yes or No."""
        raise NotImplementedError

    async def table_command(self, cmd, args):
        """/resume and /watch: the lobby's own way back to a table's screen."""
        arg = args[0] if args else ""
        tabs, clocks = await self.s.load()
        me = self.s.name
        if cmd == "resume":
            mine = [t for t in tabs if me in t["names"] and t["status"] == "playing"]
            if arg:
                mine = [t for t in mine if t["id"] == arg]
            mine.sort(key=lambda t: not (clocks.get(t["id"]) and clocks[t["id"]][0] == t["seat_of"].get(me)))
            if not mine:
                return await self.notice("You have no game at table %s." % arg if arg else
                                         "You have no game to go back to.")
            return await self.enter(mine[0]["id"], watch=False)
        if not arg:
            return await self.notice("Type /watch and a table number.")
        return await self.enter(arg, watch=True)

    async def machine(self, text):
        """machine [text]: set your preferred machine (PFL SET PREF); asked when not given, and "-" clears it."""
        if text is None:
            text = await self.ask_machine()
            if not text:
                return await self.notice("Your preferred machine is not changed.")
        text = "" if text == "-" else claim_text(text)
        _, final = await self.s.core.req(("PFL SET PREF %s" % text).rstrip())
        if not final.startswith("OK"):
            return await self.notice("That didn't work. %s" % sentence(err_text(final)))
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

    async def new(self, args, whom=None):
        """Open a table: the game, its rules where it has several, the players where it takes more than two,
        the time per turn, and whom to play (the bot, anyone who joins, or a player named). What is not given
        is asked. whom: "bot", "anyone", a player's name, or None to ask."""
        games = [g for g in self.s.games if g["key"] in RENDERERS]
        turn, game, rules, players, rest = None, None, None, None, []
        for a in args:
            g = next((g for g in games if g["key"].split("/")[0] == a.lower()), None)
            if g is not None and game is None:
                game = g
            elif TURN_RE.match(a):
                turn = a
            elif PLAYERS_RE.match(a):
                players = int(PLAYERS_RE.match(a).group(1))
            else:
                rest.append(a)
        if game is None and len(games) > 1:
            if rest:
                return await self.notice("There is no game called %s here. The games are %s." % (
                    rest[0][:12], and_list(g["key"].split("/")[0] for g in games)))
            game = await self.ask_game(games)
            if game is None:
                return None
        game = game or (games[0] if games else None)
        if game is None:
            return await self.notice("No game here has a text screen yet.")
        rulesets = game.get("rulesets") or [game["rules"]]
        for a in rest:
            if a.lower() in rulesets and rules is None:
                rules = a.lower()
            else:
                return await self.notice("%s has no rules called %s. Its rules are %s." % (
                    game["name"], a[:12], and_list(rulesets)))
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
        if whom is None:
            whom = await self.ask_whom(game)
            if whom is None:
                return None
        tid, err = await self.s.new_table(game, turn, rules, players)
        if err:
            return await self.notice(err)
        if whom != "anyone":            # sit down, then invite the bot to each other seat, or the player named
            await self.s.core.req("JON %s" % tid)
            names = botflag.NAMES[:max(0, (players or lo) - 1)] if whom.lower() == "bot" else [whom]
            for name in names:
                _, final = await self.s.core.req("INV %s %s" % (tid, name))
                if final.startswith("ERR NOPLAYER"):
                    await self.notice("There is no player called %s, so table %s is open to anyone." % (name, tid))
        return await self.enter(tid, watch=False)

    async def ask_whom(self, game):
        raise NotImplementedError

    async def enter(self, tid, watch, sit=False):
        err = await self.s.enter(tid, watch, sit)
        if self.s.quitting:                 # /quit at the table, said yes to
            return "quit"
        if self.s.practice_next:            # /practice at the table
            await self.s.practice()
        if err:
            return await self.notice(err)
        return await self.back(tid)

    async def decline(self, tid):
        """/decline NAME: my invitation to that table declined (UNV); whoever invited me hears of it."""
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
        self.asked = set()          # the invitations asked about once (their tables)

    def say(self, text=""):
        self.io.write(text + "\n")

    async def ask(self, text):
        self.io.write(text)
        await self.io.flush()
        return await self.io.readline()

    async def run(self):
        await self.relist()
        while True:
            if await self.answer_invites() == "quit":
                return
            line = await self.ask("> ")
            if await self.command(line) == "quit":
                return

    async def answer_invites(self):
        """Each new invitation is a dialog once, as on the full screen: Y accepts it, N declines it, and Enter
        leaves it for later, when /join NAME takes it."""
        for inv in [i for i in self.s.invites if i["id"] not in self.asked]:
            self.asked.add(inv["id"])
            yn = (await self.ask("%s invites you to play %s at table %s. Type Y to accept, N to decline, or "
                                 "press Enter to leave it for later. " % (inv["from"], self.s.game_name(inv["key"]),
                                                                          inv["id"]))).strip().lower()
            if yn.startswith("y"):
                return await self.enter(inv["id"], watch=False, sit=True)
            if yn.startswith("n"):
                return await self.decline(inv["id"])
        return None

    async def relist(self):
        tabs, clocks = await self.s.load()
        me = self.s.name
        mine = [t for t in tabs if me in t["names"]]
        others = [t for t in tabs if me not in t["names"]]
        self.say("")
        tail = "%s | %s" % (me, connected_text(time.time() - self.s.since))      # the time connected
        self.say(("== ParlorNet lobby " + "=" * 60)[:78 - len(tail)] + " " + tail)
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
            act = "/join %s" % t["id"] if t["status"] == "open" else "/watch %s" % t["id"]
            self.say("  %4s  %-8s  %-27s  %-12s  %s" % (t["id"], self.s.game_name(t["key"]), who[:27],
                                                        turn_text(t["turn"]) + " a turn", act)[:79])
            if t["status"] == "open":
                self.client_lines(t, t["names"])
        if not others:
            self.say("  (none)")
        if self.s.invites:
            self.say("Invitations for you:")
            for i in self.s.invites:
                self.say("  %4s  %-8s  from %-22s  /join %s accepts it" % (
                    i["id"], self.s.game_name(i["key"]), i["from"], i["from"])[:79])
        self.say(await self.s.who_text())
        self.say("Type /open and a game to open a table, or /help for the commands.")
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
        await self.notice("\n".join(" " + l for l in PLAIN_LOBBY_HELP if self.s.has_password or "/password" not in l))

    async def back(self, tid):
        return await self.relist()

    async def ask_yes(self, title, lines, verb, yes=None):
        yn = await self.ask(" ".join(lines) + " Type Y for yes, or N for no. ")
        return yn.strip().lower().startswith("y")

    async def password(self):
        """The server's password-change steps, as prompts (secrets not echoed)."""
        try:
            await self.s.core.pwd()
            return await self.notice("Nothing to change.")
        except NeedAuth as ask:
            done = await answer_asks(self.io, self.s.core, ask)
        except CoreError as e:
            return await self.notice("That didn't work. %s" % sentence(no_wait(str(e))))
        await self.notice("Your password is changed. You are signed out everywhere else." if done
                          else "Your password is not changed.")

    async def ask_turn(self, game):
        t = await self.ask("How long is each turn? Type 5m, 15m, 1h, 1d, 3d or 5d [1d]: ")
        return t or "1d"

    async def ask_whom(self, game):
        a = (await self.ask("Whom do you want to play? Type bot, a player's name, or Enter for anyone: ")).strip()
        return "anyone" if not a or a.lower() == "anyone" else a if NAME_RE.match(a) else None

    async def ask_rules(self, game, rulesets):
        for n, r in enumerate(rulesets):
            self.say("  %d  %-10s %s" % (n + 1, r, games.rules_name(game["key"], r)))
        a = (await self.ask("Which rules? Type 1 to %d [1]: " % len(rulesets))).strip().lower()
        if not a:
            return rulesets[0]
        if a.isdigit() and 1 <= int(a) <= len(rulesets):
            return rulesets[int(a) - 1]
        r = next((r for r in rulesets if r.startswith(a)), None)
        if r is None:
            await self.notice("%s has no rules called %s." % (game["name"], a[:12]))
        return r

    async def ask_players(self, game, lo, hi):
        a = (await self.ask("How many players? Type %d to %d [%d]: " % (lo, hi, lo))).strip()
        if not a:
            return lo
        if a.isdigit() and lo <= int(a) <= hi:
            return int(a)
        await self.notice("%s takes %d to %d players." % (game["name"], lo, hi))
        return None

    async def ask_game(self, games):
        words = [g["key"].split("/")[0] for g in games]
        a = (await self.ask("Which game? Type %s [%s]: " % (or_list(words), words[0]))).lower()
        if not a:
            return games[0]
        game = next((g for g, w in zip(games, words) if w.startswith(a)), None)
        if game is None:
            await self.notice("There is no game called %s here." % a[:12])
        return game


class AnsiMenu(MenuBase, AnsiView):
    """The full-screen lobby: your games (your turn first) and your invitations, then the other tables. The
    arrows pick and Enter opens. N opens a new table, ? is help and / the command line. A letter means what it
    means at a table, or nothing (docs/design.md, The lobby)."""

    MINE_TOP, MINE_ROWS = 2, 5
    OTHER_TOP, OTHER_ROWS = 10, 8
    HOTKEYS = "n?"

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
        return it[1]["id"] if it[0] == "table" else "inv:" + it[1]["id"] if it[0] == "invite" else it[0]

    def my_move(self, it):
        if it[0] != "table" or it[1]["id"] not in self.clocks:
            return False
        return self.clocks[it[1]["id"]][0] == it[1]["seat_of"].get(self.s.name, -9)

    async def load(self):
        tabs, self.clocks = await self.s.load()
        cur = self.item_id(self.items[self.sel]) if self.items else None
        me = self.s.name
        mine = [t for t in tabs if me in t["names"]]
        self.mine = [("table", t) for t in mine if self.my_move(("table", t))]
        self.mine += [("table", t) for t in mine if not self.my_move(("table", t))]
        self.mine += [("invite", i) for i in self.s.invites]
        fresh = [i for i in self.s.invites if i["id"] not in self.seen_invites]
        self.seen_invites = {i["id"] for i in self.s.invites}
        if fresh and not self.first:
            i = fresh[0]
            self.say("%s invites you to play %s at table %s. Press Enter on it to answer." % (
                i["from"], self.s.game_name(i["key"]), i["id"]), A_YELLOW)
        self.others = [("table", t) for t in tabs if me not in t["names"]] + [("practice", None)]
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
        if it[0] == "practice":
            return "%5s  %s" % ("", "The practice game teaches the keys."), 0, A_GREEN, A_GREEN
        if it[0] == "invite":
            i = it[1]
            left = "%5s  %-8s  %-16s  %-10s  " % (i["id"], self.s.game_name(i["key"]), ("from " + i["from"])[:16],
                                                 "")
            return (left + "an invitation")[:74], len(left), A_YELLOW, A_TEXT
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
            st, attr = ("open", A_GREEN) if t["status"] == "open" else ("playing", A_DIM)
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
            return "You have no games yet. Press N to open a table."
        kind, x = self.items[self.sel]
        if kind == "practice":
            return "Press Enter to play the practice game. It is not played on the parlor."
        if kind == "invite":
            return "%s invites you to play %s at table %s. Press Enter to answer." % (
                x["from"], self.s.game_name(x["key"]), x["id"])
        opp = [n for n in x["names"] if n != self.s.name]
        seats = [(x["seat_of"][n], n) for n in x["names"]]       # with what each plays (LST clients)
        cl, w = x.get("clients") or {}, COLS - 2
        if self.s.name in x["names"]:
            if x["status"] == "open":
                return "Table %s is waiting for an opponent. Press Enter to go back to it." % x["id"]
            if not opp:
                return "Table %s is your game. Press Enter to go back to it." % x["id"]
            return with_clients(["Press Enter to go back to your game with %%s at table %s." % x["id"],
                                 "Your game with %%s is at table %s." % x["id"]],
                                [(s, n) for s, n in seats if n != self.s.name], cl, " and ", w)
        if x["status"] == "open":
            if not seats:
                return "Table %s is open. Press Enter to sit down." % x["id"]
            return with_clients(["%%s waits at table %s. Press Enter to sit down." % x["id"],
                                 "%%s waits at table %s." % x["id"]], seats, cl, ", ", w)
        return with_clients(["%%s play at table %s. Press Enter to watch." % x["id"],
                             "%%s play at table %s." % x["id"]], seats, cl, " and ", w)

    def compose(self):
        cv = Canvas()
        title_bar(cv, self.g, "Lobby", self.s.name)
        moves = [it[1]["id"] for it in self.mine if self.my_move(it)]
        if moves:
            cv.put(1, 1, ("It's your turn at table %s." % and_list(moves[:8]))[:COLS - 2], A_YELLOW)
        elif not [it for it in self.mine if it[0] == "table"]:
            cv.put(1, 1, "You have no games yet. Press N to open a table.", A_TEXT)
        else:
            cv.put(1, 1, "Welcome, %s. Pick a table with %s and press Enter." % (
                self.s.name, self.move_set().upper() or "the arrows"), A_TEXT)
        self.panel(cv, 0, self.MINE_TOP, self.MINE_ROWS, "Your tables",
                   "%5s  %-8s  %-16s  %-10s  %s" % ("Table", "Game", "Opponent", "Per turn", "Status"),
                   self.mine, 0, "You have no games yet. Press N to open a table.")
        self.panel(cv, 1, self.OTHER_TOP, self.OTHER_ROWS, "Other tables",
                   "%5s  %-8s  %-24s  %-10s  %s" % ("Table", "Game", "Players", "Per turn", ""),
                   self.others, len(self.mine), "Nobody else has a table yet. Press N to open one.")
        cv.put(1, ROWS - 3, self.describe()[:COLS - 2], A_TEXT)
        self.input_line(cv)
        if self.buf:
            self.command_keys(cv, "the lobby")
        else:
            status_bar(cv, [("Arrows", "pick"), ("Enter", "open"), ("N", "new table"), ("?", "help"),
                            ("/", "type", "/")])
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
        if self.buf:
            return await self.command_key(k)
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
                return await self.hotkey(v)                 # a keys line item, clicked: never a movement
            k = v
        k = self.move_of(k) or k                            # a movement letter is an arrow
        if k in ("UP", "DOWN", "PGUP", "PGDN", "HOME", "END"):
            if n:
                step = {"UP": -1, "DOWN": 1, "PGUP": -5, "PGDN": 5, "HOME": -n, "END": n}[k]
                self.sel = max(0, min(n - 1, self.sel + step))
            return None
        if k == "REDRAW":
            self.s.painter.invalidate()
            return None
        if k in ("ENTER", " "):
            return await self.open_selected()
        if k == "/":
            self.buf = "/"
            return None
        if isinstance(k, str) and len(k) == 1:
            if k.lower() in self.HOTKEYS:
                return await self.hotkey(k.lower())
            return self.say("%s does nothing in the lobby. Press ? to see the keys." % k.upper(), A_DIM)
        return None

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
            return self.say("You move the cursor with the arrows only now.", A_GREEN)
        return self.say("You move the cursor with %s now, and it is kept for next time." % self.move_set().upper(),
                        A_GREEN)

    async def hotkey(self, k):
        if k == "?":
            return await self.help()
        if k == "n":
            return await self.new([])
        return None

    async def open_selected(self):
        if not self.items:
            return None
        kind, x = self.items[self.sel]
        if kind == "practice":
            await self.s.practice()
            return await self.load()
        if kind == "invite":            # the invitation dialog: Accept sits down, Decline turns it down
            answer = await self.dialog("Invitation", [
                "%s invites you to play %s at table %s." % (x["from"], self.s.game_name(x["key"]), x["id"]),
                "If you accept, you sit down there."], ("Accept", "Decline"), 0,
                "Press Y to accept, or N to decline. Esc puts this away.")
            if answer is None:
                return None
            return await (self.enter(x["id"], watch=False, sit=True) if answer else self.decline(x["id"]))
        if self.s.name in x["names"]:
            return await self.enter(x["id"], watch=False)
        return await self.enter(x["id"], watch=x["status"] != "open")

    # ---- MenuBase hooks ------------------------------------------------------
    async def relist(self):
        await self.load()

    async def notice(self, text):
        self.say(text, A_RED if text.startswith(("That didn't work", "There is no", "No ", "You have no")) else A_YELLOW)

    async def back(self, tid):
        """Back from a table: keep it selected."""
        await self.load()
        ids = [self.item_id(it) for it in self.items]
        if tid in ids:
            self.sel = ids.index(tid)
        await self.show()

    async def help(self):
        await self.help_box("Lobby help", [l for l in LOBBY_HELP if self.s.has_password or "/password" not in l])

    async def ask_yes(self, title, lines, verb, yes=None):
        return await self.confirm(title, lines, verb, yes)

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
            return await self.notice("That didn't work. %s" % sentence(no_wait(str(e))))
        while True:
            value = await self.ask_text(ask.label, ask.info or [ask.label], maxlen=64, secret=ask.secret)
            if not value:
                return self.say("Your password is not changed.", A_DIM)
            try:
                await self.s.core.aut(ask.field, value)
                return self.say("Your password is changed. You are signed out everywhere else.", A_GREEN)
            except NeedAuth as again:
                ask = again
            except CoreError as e:
                return await self.notice("That didn't work. %s" % sentence(no_wait(str(e))))

    async def ask_turn(self, game):
        i = await self.choose("New %s table" % game["name"],
                              ["How long is each turn? A player whose", "clock runs out loses the game."],
                              [label + ("  (default)" if n == TURN_DEFAULT else "")
                               for n, (_, label) in enumerate(TURNS)], TURN_DEFAULT)
        return None if i is None else TURNS[i][0]

    async def ask_game(self, games):
        i = await self.choose("New table", ["Which game do you want to play?"], [g["name"] for g in games], 0)
        return None if i is None else games[i]

    async def ask_whom(self, game):
        i = await self.choose("New %s table" % game["name"], ["Whom do you want to play?"],
                              ["%s against the bot" % game["name"], "%s with anyone who joins" % game["name"],
                               "%s with a player you name" % game["name"]], 0)
        if i is None:
            return None
        if i < 2:
            return ("bot", "anyone")[i]
        name = await self.ask_text("A player", ["The name of the player to invite."])
        return name or None

    async def ask_rules(self, game, rulesets):
        i = await self.choose("New %s table" % game["name"], ["Which rules do you want?"],
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
        self.last = ""          # the last move, as a sentence (last_move, from the events)
        self.turn_secs = 0
        self.clock_at = time.time()

    def name(self, seat):
        n = self.names[seat] if 0 <= seat < len(self.names) else "-"
        return n if n != "-" else "seat %d" % (seat + 1)

    GAME = ""               # the game's name, for the title bar and the header

    def who(self, name):
        """A player's name as a sentence says it: the bot is "the bot"."""
        return "the bot" if botflag.is_bot_name(name) else name

    def seated_names(self):
        return [n for n in self.names if n and n != "-"]

    def title_text(self):
        """The title bar's words: "Grid Words, table 12, with ann", or for a watcher "Grid Words, table 12,
        ann and bob"."""
        title = "%s, table %s" % (self.GAME, self.tid)
        if self.me >= 0:
            others = [self.who(n) for i, n in enumerate(self.names) if i != self.me and n and n != "-"]
            return title + (", with " + and_list(others) if others else "")
        seated = [self.who(n) for n in self.seated_names()]
        return title + (", " + and_list(seated) if seated else "")

    def over_sentence(self):
        """The end of the game as a sentence: "Game over. ann wins 33-31." """
        status = (self.v or {}).get("status") or ""
        text = (self.over or (status if status.startswith("Game over") else "") or "Game over").strip().rstrip(".")
        if text.startswith("Game over: "):
            text = text[len("Game over: "):]
        elif text == "Game over":
            return "Game over."
        first = text.split()[0] if text.split() else ""
        if first not in self.names:         # a name stays as its player wrote it
            text = text[:1].upper() + text[1:]
        return "Game over. " + text + "."

    def waiting_sentence(self):
        """Before the game starts: who it waits for, and for a seated player how to bring in the bot."""
        seated = len(self.seated_names())
        free = max(1, len(self.names) - seated) if self.names else 1
        who = "a second player" if seated <= 1 or len(self.names) <= 2 else \
            "one more player" if free == 1 else "%d more players" % free
        if self.me >= 0:
            return "Waiting for %s. Type /invite bot to play the bot." % who
        return "Waiting for %s." % who

    def not_yours(self, seat=None):
        """What a key that plays says when it isn't your turn."""
        if self.me < 0:
            return "You are watching this table."
        if not self.playing():
            return self.over_sentence() if self.over else "The game has not started yet."
        return "It is %s's turn." % self.who(self.name(self.v["turn"] if seat is None else seat))

    def offer(self):
        """The offer waiting for this player's answer, or None: (key, title, lines, start on accept, the move
        that accepts, the move that declines, the sentence while it is put away). The key tells one offer
        from the next."""
        return None

    def refused(self, what, final):
        """A move the server refused, in the player's own terms: "You can't play d3. Not your turn." """
        return "You can't play %s. %s" % (what, sentence(err_text(final)))

    def describe(self, text):
        return describe_event(text, self.names)

    def move_word(self, cmd, words=()):
        """A typed command -> the move it stands for (sent with MOV: one token or several, space-separated),
        or None if it is not one. cmd is the first word in lower case, words every word as typed."""
        return None

    # The commands every table takes, and what the sentence line says while one is typed.
    TABLE_COMMANDS = {
        "say": "/say sends a line to the table.",
        "resign": "/resign gives up the game, after asking first.",
        "invite": "/invite brings a player to this table, and /invite bot brings in the bot.",
        "uninvite": "/uninvite takes back an invitation you sent.",
        "note": "/note shows the table's notes, and /note with words adds one.",
        "time": "/time shows the time signed in and the server's time.",
        "who": "/who lists who is online, and /who with a name shows one player.",
        "list": "/list shows your games and the open tables.",
        "leave": "/leave goes back to the lobby. The game goes on without you.",
        "quit": "/quit signs you out, after asking first. Your games go on.",
        "help": "/help opens help.",
        "practice": "/practice starts the practice game of Tic Tac Toe, which teaches the keys.",
        "redraw": "/redraw loads the table again and redraws the screen.",
    }
    COMMANDS = {}           # a game's own commands (/roll, /pass ...): name -> what the sentence line says
    # Help, page 1: how to play here (Moving, Keys for the game, Commands, Every game); page 2: the reference
    HELP_MOVING = []
    HELP_KEYS = []
    HELP_COMMANDS = []
    HELP_REFERENCE = []
    EVERY_TABLE = "Every table also takes /say, /invite, /note, /time and /leave."

    def reference(self):
        """Help's second page: the facts to look up."""
        return list(self.HELP_REFERENCE)
    MOVE_EXAMPLE = ""       # a typed move, for the sentence line: "/d3"

    def command_sentence(self, line):
        """What the sentence line says while line (with its slash) is typed on the command line."""
        word = line[1:].split()[0].lower() if line[1:].split() else ""
        text = self.COMMANDS.get(word) or self.TABLE_COMMANDS.get(word)
        if text:
            return text
        if word and self.me >= 0 and self.move_word(word, line[1:].split()):
            return "Press Enter to play %s." % line[1:].strip()
        if self.MOVE_EXAMPLE:
            return "Type a command, or a move such as %s, and press Enter." % self.MOVE_EXAMPLE
        return "Type a command and press Enter. /help lists them."

    # how a screen shows things; the full screen and plain mode each give their own
    def tell(self, text, attr=None):
        raise NotImplementedError

    async def show_lines(self, title, lines):
        raise NotImplementedError

    async def ask_yes(self, title, lines, verb, yes=None):
        raise NotImplementedError

    async def help(self, page=1):
        raise NotImplementedError

    async def reload(self):
        await self.pull()
        await self.refresh()

    async def game_command(self, cmd, args):
        """A game's own command that is more than a move (/colors, /sort, /home): True when done."""
        return False

    async def run_command(self, line):
        """A command line, without its slash -> "menu" to leave the table, else None. /leave, /practice, /help,
        /redraw and a game's own commands for its screen (/sort, /home) are the table screen's own. Every
        other line, a typed move included, runs on the server (shared)."""
        p = line.split()
        if not p:
            return None
        cmd, args = p[0].lower(), p[1:]
        if cmd == "leave":
            return "menu"
        if cmd == "practice":
            if not self.s.ansi:
                return self.tell(PLAIN_PRACTICE)
            self.s.practice_next = True
            return "menu"
        if cmd == "help":
            await self.help(2 if args[:1] == ["2"] else 1)
        elif cmd == "redraw":
            await self.reload()
            self.redrawn()
        elif cmd in self.COMMANDS and await self.game_command(cmd, args):
            pass
        else:
            return await self.shared(line)
        return None

    def typed_reset(self):
        """Before a line runs on the server: a pick in progress is put down, since the board may change."""

    async def shared(self, line):
        """A command line or a typed move, run on the server (CMD) with this table's words, then the events
        it caused and its answer -> "menu" when it signed you out."""
        self.typed_reset()
        lines = answer_lines(await self.s.command(self, line, self.tid))
        if self.s.quitting:
            return "menu"
        await self.reload()
        await self.answered(line, lines)
        return None

    async def answered(self, line, lines):
        """The answer to a command line: one line on the sentence line, more in a box."""
        if len(lines) == 1:
            self.tell(lines[0])
        elif lines:
            await self.show_lines(box_title(line), lines)

    def redrawn(self):
        """After /redraw: the full screen repaints."""

    async def said(self):
        """After a line said to the table."""
        await self.pull()

    async def play_word(self, word):
        """Play move_word's answer -> an error text or None."""
        return await self.move(word)

    def on_event(self, text):
        """Called for every new event (the payload after the seq)."""

    def last_move(self, text):
        """A game's event -> the last-move sentence it makes (docs/design.md, The last move), or None when the
        event says nothing about the last move."""
        return None

    def view_last(self):
        """The last move as a sentence from the view alone, for a game whose view carries it, when no event
        has told it yet; "" otherwise."""
        return ""

    def last_said(self):
        return self.last or self.view_last()

    def actor(self, seat):
        """Who did something, at the start of a last-move sentence: "You", or the player's name."""
        seat = int(seat)
        return "You" if seat == self.me else self.who(self.name(seat))

    def turn_sentence(self):
        """The panel's turn line, in the shared words: "Your turn.", "Waiting for ann.", "Game over." """
        if not (self.v and self.v.get("rows")):
            return "Waiting for a second player."
        if not self.playing():
            return "Game over."
        if self.my_turn():
            return "Your turn."
        turn = self.v.get("turn", -1)
        if turn is None or turn < 0:
            return "Game over."
        return "Waiting for %s." % self.who(self.name(turn))

    def clock_sentence(self):
        """The panel's clock line -> (text, urgent): "You have 4:12 left.", "ann has 2:03 left.", or for a
        table whose clock isn't running "Each turn has 5 minutes." """
        c = self.clock()
        if c:
            seat, left, limit = c
            if seat == self.me:
                return "You have %s left." % left_words(left), left < 60
            return "%s has %s left." % (self.who(self.name(seat)), left_words(left)), False
        if self.turn_secs and not self.over:
            return "Each turn has %s." % turn_words(self.turn_secs), False
        return "", False

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
                said = self.last_move(p[3])
                if said is not None:
                    self.last = said
                d = self.describe(p[3])
                if d:
                    self.log.append(d)
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

    def playing(self):
        return bool(self.v and self.v["rows"]) and not self.over

    def my_turn(self):
        return self.me >= 0 and self.playing() and self.v["turn"] == self.me and bool(self.v["moves"])

    async def tick(self):
        """Called each time the screen's loop wakes, with or without a key: a game's timed steps (the
        Backgammon countdowns). Nothing by default."""
        return None

    def tick_in(self):
        """Seconds until tick() has something to do, or None. The loop wakes no later than that."""
        return None

    async def key_seen(self, k):
        """Called with each key before the screen handles it. True means the key is used up (as Esc is when
        it stops a Backgammon countdown)."""
        return False

    def clock(self):
        c = self.v and self.v.get("clock")
        if not c or self.over:
            return None
        seat, left, limit = c
        return seat, max(0, left - (time.time() - self.clock_at)), limit

    async def move(self, sq):
        _, final = await self.core.req("MOV %s %s" % (self.tid, sq))
        if not final.startswith("OK"):
            return self.refused(sq, final)
        await self.pull()
        await self.refresh()
        return None

    async def staged(self, verb, what):
        """UND or END for games with staged turns."""
        _, final = await self.core.req("%s %s" % (verb, self.tid))
        if not final.startswith("OK"):
            return "You can't %s now. %s" % (what, sentence(err_text(final)))
        await self.pull()
        await self.refresh()
        return None

    async def chat(self, text):
        _, final = await self.core.req("CHT %s %s" % (self.tid, text))
        return None if final.startswith("OK") else "Your line can't be sent. %s" % sentence(err_text(final))

class AnsiGame(GameBase, AnsiView):
    """A game's full screen: the key loop, typed commands, talk, resign and
    help, shared by every game. A game's AnsiScreen (adapters/games/) draws
    the screen in compose() and fills in the hooks: on_arrow, on_click,
    on_enter, on_escape, hotkey, move_word, typed_reset and snap."""

    idle = False
    HOTKEYS = "rt?"         # the game's shortcut keys (letters lower case); any other key does nothing
    LOG_TOP, LOG_ROWS = 9, 11
    EVERY_GAME = ["R resigns, after asking first. T or Tab opens the chat.",
                  "Esc goes back to the lobby, and the game goes on without you."]

    def __init__(self, sess, tid, key, me):
        GameBase.__init__(self, sess, tid, key, me)
        AnsiView.__init__(self, sess)
        self.was_my_turn = False
        self.chatting = False   # the chat screen is open
        self.chat_buf = ""      # what is typed on it
        self.offer_away = None  # the offer put away with Esc (its key), until Enter brings it back

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

    def input_line(self, cv):
        """Row 22: the command line. A game screen shows its message on the sentence line above, in place
        of the hint, so it is not repeated here."""
        if self.buf:
            cv.put(1, ROWS - 2, self.buf[-(COLS - 3):], A_BRIGHT)

    def lit(self):
        """True in game mode: the cursor and the lit keys show. In command mode they go dark."""
        return not self.buf

    def sentence(self, hint):
        """The sentence line's text and attribute: the command being typed, a message, an offer put away,
        or the hint."""
        if self.buf:
            return self.command_sentence(self.buf), A_TEXT
        if self.msg:
            return self.msg, self.msg_attr
        return self.offer_hint() or hint()

    def keys_line(self, cv, keys):
        """The keys line: the game's keys in game mode, the command line's keys in command mode."""
        if self.buf:
            return self.command_keys(cv)
        status_bar(cv, keys)

    def keys(self, arrows=None, enter=None, actions=(), cancel=False):
        """The keys line's slots in the shared order (docs/design.md): the arrows and Enter, then the game's
        actions as (letter, label, command) in order, then Esc cancel while something is picked, then Chat,
        Help and Resign, the command line and Esc lobby. A shortcut on a movement letter shows as its
        command. When the slots don't fit, Resign goes first, then Chat, then the game's last actions, and
        "? help" becomes "? more"."""
        head = ([("Arrows", arrows)] if arrows else []) + ([("Enter", enter)] if enter else [])
        acts = []
        for letter, label, command in actions:
            if self.move_of(letter):
                acts.append(("/" + command.split()[0], "", letter))
            else:
                acts.append((letter.upper(), label, letter))
        seated = self.me >= 0 and self.playing()
        tail = [("Esc", "cancel")] if cancel else []
        table = [("T", "chat"), ("?", "help")] + ([("R", "resign")] if seated else [])
        end = [("/", "type", "/")] + ([] if cancel else [("Esc", "lobby")])

        def width(items):
            return sum(len(i[0]) + (1 + len(i[1]) if i[1] else 0) + 2 for i in items) - 2
        more = False
        while width(head + acts + tail + table + end) > COLS - 3:
            if ("R", "resign") in table:
                table.remove(("R", "resign"))
            elif ("T", "chat") in table:
                table.remove(("T", "chat"))
            elif acts:
                acts.pop()
            else:
                break
            more = True
        if more:
            table = [t if t[0] != "?" else ("?", "more") for t in table]
        return head + acts + tail + table + end

    async def shared(self, line):
        result = await GameBase.shared(self, line)
        self.snap()
        return result

    def tell(self, text, attr=None):
        self.say(text, A_RED if attr == "red" else attr or A_YELLOW)

    async def show_lines(self, title, lines):
        await self.message(title, list(lines)[:HELP_ROWS] or ["There is nothing to show."])

    async def ask_yes(self, title, lines, verb, yes=None):
        return await self.confirm(title, lines, verb, yes)

    def redrawn(self):
        self.snap()
        self.s.painter.invalidate()

    # ---- the sentence line's shared sentences ----------------------------------
    def waiting_hint(self):
        return self.waiting_sentence(), A_DIM

    def over_hint(self, text=None):
        if text is not None:
            self.over = self.over or text
        return self.over_sentence() + " Press Esc to go back to the lobby.", A_YELLOW

    def watch_hint(self):
        names = and_list([self.who(n) for n in self.seated_names()]) or "this table"
        return "You are watching %s. Press T to chat, or Esc to leave." % names, A_DIM

    def their_hint(self, seat):
        return "Waiting for %s. Press T to chat, or ? for help." % self.who(self.name(seat)), A_DIM

    # ---- drawing helpers -----------------------------------------------------
    def draw_log(self, cv, x, width):
        """The log, newest last, in LOG_ROWS rows from LOG_TOP; chat bright, the rest dim."""
        lines = []
        for entry in self.log:
            attr = A_TEXT if entry.startswith("<") else A_DIM
            lines += [(l, attr) for l in wrap(entry, width)]
        for i, (l, attr) in enumerate(lines[-self.LOG_ROWS:]):
            cv.put(x, self.LOG_TOP + i, l, attr)

    def draw_panel_parts(self, cv, px, top, pw, players, facts=(), extra=None, bottom=19):
        """The panel, in the shared order (docs/design.md): the players, then the turn, the clock, the last
        move and the game's facts, then the chat and log down to the bottom row. players: [(seat, mark, score)],
        where mark is (text, attribute) or None and score is the game's own unit, as "33 discs". facts: lines
        of text, or (text, attribute). extra(cv, y) draws more under the facts (Grid Words' rack) and returns
        the next row."""
        g, inner = self.g, pw - 4
        box(cv, g, px, top, pw, bottom - top + 1, A_BORDER, "Players", tattr=A_HEAD)
        y = top + 1
        started = bool(self.v and self.v.get("rows"))
        for seat, mark, score in players:
            tomove = started and self.playing() and self.v.get("turn") == seat
            empty = seat >= len(self.names) or self.names[seat] in ("", "-")
            cv.put(px + 2, y, g["ptr"] if tomove else " ", A_YELLOW)
            x = px + 4
            if mark:
                cv.put(x, y, mark[0], mark[1])
                x += len(mark[0]) + 1
            label = ("(empty seat)" if empty else self.names[seat]) + (" (you)" if seat == self.me else "")
            room = px + pw - 2 - x - (len(score) + 1 if score and started else 0)
            cv.put(x, y, label[:room], A_BRIGHT if tomove else A_DIM if empty else A_TEXT)
            if score and started:
                cv.put(px + pw - 2 - len(score), y, score, A_BRIGHT if tomove else A_TEXT)
            y += 1
        rule(cv, g, px, y, pw, A_BORDER)
        y += 1
        clock, urgent = self.clock_sentence()
        lines = [(self.turn_sentence(), A_YELLOW), (clock, A_RED if urgent else A_TEXT)]
        if started and self.last_said():
            lines.append((self.last_said(), A_TEXT))
        lines += [f if isinstance(f, tuple) else (f, A_DIM) for f in facts]
        for text, attr in lines:
            for l in textwrap.wrap(text, inner):
                if y < bottom - 2:
                    cv.put(px + 2, y, l, attr)
                    y += 1
        if extra:
            y = extra(cv, y)
        rule(cv, g, px, y, pw, A_BORDER, "Chat and log", tattr=A_HEAD)
        self.LOG_TOP, self.LOG_ROWS = y + 1, max(0, bottom - y - 1)
        self.draw_log(cv, px + 2, inner)

    def draw_waiting(self, cv, x0, y0, w=27):
        """The box over the board before the game starts."""
        box(cv, self.g, x0, y0, w, 5, A_POP, double=True, fill=A_POP)
        cv.put(x0 + 2, y0 + 1, ("Table %s is open." % self.tid).center(w - 4), A_POP)
        cv.put(x0 + 2, y0 + 3, "Nobody has joined yet.".center(w - 4), A_POPDIM)

    # ---- running ---------------------------------------------------------
    async def run(self):
        if not await self.open_view():
            return "That table can't be shown."
        self.snap()
        await self.show()
        await self.offer_check()
        polled = time.time()
        while True:
            k = await self.key(min(1.0, self.tick_in() or 1.0))
            if k is None:
                if time.time() - polled >= POLL_SECS:
                    polled = time.time()
                    if await self.pull():
                        await self.refresh()
                        self.snap()
                await self.tick()
                await self.show()
                await self.offer_check()
                continue
            self.msg = ""
            if not await self.key_seen(k) and await self.on_key(k) == "menu":
                return None
            await self.tick()
            await self.show()

    async def on_key(self, k):
        o = self.offer()
        if o and not self.buf and not self.chatting:
            if self.offer_away != o[0]:
                self.io.keys.insert(0, k)           # the dialog is up: the key that came answers it
                return await self.answer_offer(o)
            if k in ("ENTER", " "):                 # put away: Enter brings it back
                return await self.answer_offer(o)
        result = await self.game_key(k)
        if result != "menu":
            await self.offer_check()
        return result

    async def game_key(self, k):
        if self.chatting:
            return await self.chat_key(k)
        if self.buf:
            return await self.command_key(k)
        if isinstance(k, tuple):
            v = self.hit(k)
            if isinstance(v, tuple):
                return await self.on_click(v)
            if not isinstance(v, str):
                return None
            if len(v) == 1 and v in self.HOTKEYS:
                return await self.hotkey(v)         # a keys line item, clicked: never a movement
            k = v
        else:
            k = self.move_of(k) or k                # a movement letter is an arrow
        if k in ("UP", "DOWN", "LEFT", "RIGHT"):
            return self.on_arrow(k)
        if k == "ESC":
            return self.on_escape()
        if k == "REDRAW":
            self.s.painter.invalidate()
            return None
        if k in ("ENTER", " "):
            return await self.on_enter()
        if k == "/":
            self.buf = "/"
            return None
        if k == "TAB":
            return self.open_chat()
        if isinstance(k, str) and len(k) == 1:
            if k.lower() in self.HOTKEYS:
                return await self.hotkey(k.lower())
            return self.say(self.does_nothing(k), A_DIM)
        return None

    def does_nothing(self, k):
        return "%s does nothing in %s. Press ? to see the keys." % (k.upper(), self.GAME)

    # ---- offers: a dialog for a double, a draw offer or an offer to resign (docs/design.md, Dialogs) -----
    async def offer_check(self):
        """Open the offer's dialog when an offer waits and the player has not put it away."""
        o = self.offer()
        if not o:
            self.offer_away = None
            return None
        if self.offer_away == o[0] or self.overlay or self.buf or self.chatting:
            return None
        return await self.answer_offer(o)

    async def answer_offer(self, o):
        key, title, lines, start, yes, no, _ = o
        answer = await self.dialog(title, lines, ("Accept", "Decline"), 0 if start else 1,
                                   "Press Y to accept, or N to decline. Esc puts this away.")
        if answer is None:
            self.offer_away = key           # put away: Enter brings it back
            return None
        self.offer_away = None
        return await self.answer_with(yes if answer else no)

    async def answer_with(self, word):
        """Send the move that answers an offer."""
        err = await self.play_word(word)
        if err:
            self.say(err, A_RED)
        self.snap()

    def offer_hint(self):
        """The sentence line while an offer is put away, or None."""
        o = self.offer()
        return (o[6], A_YELLOW) if o and self.offer_away == o[0] else None

    # ---- the chat screen -----------------------------------------------------
    def open_chat(self):
        self.chatting, self.chat_buf, self.msg = True, "", ""

    def chat_head(self):
        """The chat screen's first row: the table, and whose turn it is on the board."""
        head = "Chat at table %s." % self.tid
        if not (self.v and self.v.get("rows")):
            return head + " The game has not started."
        if not self.playing():
            return head + " The game is over."
        if self.my_turn():
            return head + " It is your turn on the board."
        return head + " %s is playing." % self.who(self.name(self.v["turn"]))

    async def chat_key(self, k):
        """A key on the chat screen: everything types, except ? on an empty line, which opens help. Enter says
        the line (a line with a slash is a command), and Esc goes back to the board."""
        if k == "ESC":
            self.chatting, self.chat_buf = False, ""
        elif k == "BS":
            self.chat_buf = self.chat_buf[:-1]
        elif k == "REDRAW":
            self.s.painter.invalidate()
        elif k == "?" and not self.chat_buf:      # on an empty line, ? is help, as everywhere
            return await self.help()
        elif k == "ENTER":
            line, self.chat_buf = self.chat_buf.strip(), ""
            if line.startswith("/"):
                return await self.command(line[1:].strip())
            if line:
                err = await self.chat(line)
                if err:
                    self.say(err, A_RED)
                await self.pull()
        elif isinstance(k, str) and len(k) == 1 and len(self.chat_buf) < 70:
            self.chat_buf += k
        return None

    def compose_screen(self):
        return self.compose_chat() if self.chatting else self.compose()

    def compose_chat(self):
        cv = Canvas()
        g = self.g
        title_bar(cv, g, self.title_text())
        cv.put(1, 1, self.chat_head()[:COLS - 2], A_YELLOW)
        box(cv, g, 0, 2, COLS, 18, A_BORDER, "Chat and log", tattr=A_HEAD)
        lines = []
        for entry in self.log:
            attr = A_TEXT if entry.startswith("<") else A_DIM
            lines += [(l, attr) for l in wrap(entry, COLS - 4)]
        for i, (l, attr) in enumerate(lines[-16:]):
            cv.put(2, 3 + i, l, attr)
        text, attr = (self.msg, self.msg_attr) if self.msg else (
            "Type a line and press Enter to say it. Esc goes back to the board.", A_TEXT)
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        cv.put(1, ROWS - 2, ("> " + self.chat_buf)[-(COLS - 3):], A_BRIGHT)
        status_bar(cv, [("Enter", "send"), ("/", "command"), ("Esc", "board")], lit=False)
        return cv

    def cursor_spot(self):
        return (min(3 + len(self.chat_buf), COLS - 2), ROWS - 2) if self.chatting else None

    async def hotkey(self, k):
        """R resigns, T opens the chat screen, ? helps; a game adds its own and calls this."""
        if k == "r":
            return await self.shared("resign")
        if k == "t":
            return self.open_chat()
        if k == "?":
            return await self.help()
        return None

    async def command(self, line):
        """A command line, without its slash."""
        return await self.run_command(line)

    def help_pages(self):
        """Help's two pages, as lists of lines."""
        one = ["Moving"] + self.HELP_MOVING + ["", "Keys for %s" % self.GAME] + (
            self.HELP_KEYS or ["%s has no keys of its own." % self.GAME]) + ["", "Commands"] + self.HELP_COMMANDS \
            + ["", "Every game"] + self.EVERY_GAME
        extra = self.keys_help()
        two = (extra + [""] if extra else []) + self.reference() + ["", self.EVERY_TABLE, PRACTICE_LINE]
        return one, two

    async def help(self, page=1):
        await self.pages("%s help" % self.GAME, list(self.help_pages()), page)


class PlainGame(GameBase):
    """A game on a plain terminal: the board printed line by line (lines()),
    a prompt (prompt()), typed moves (move_word, play_word) and the shared
    commands: say, resign, redraw, menu, help."""

    BOARD_KINDS = ("JOIN", "LEAVE", "START", "END")   # events that redraw the board (others are printed)

    def lines(self):
        raise NotImplementedError

    def prompt(self):
        """The prompt: the sentence line made short, ending with ">", naming a command only when it applies."""
        v = self.v
        if not (v and v.get("rows")):
            return "Waiting for a second player. > "
        if not self.playing():
            return "Game over. > "
        if self.me < 0:
            return "You are watching. > "
        if self.my_turn():
            return self.turn_prompt() + " > "
        return "%s > " % self.turn_sentence()

    def turn_prompt(self):
        """The prompt on your turn, without its ">"."""
        return "Your turn."

    # ---- the plain screen's parts, in the panel's order ----------------------------------------
    def plain_header(self):
        """The first lines: "Grid Words, table 12, with ann. You are eric." """
        return textwrap.wrap("%s. %s" % (self.title_text(), "You are watching." if self.me < 0 else
                                         "You are %s." % self.s.name), 78)

    def plain_waiting(self):
        return ["", self.waiting_sentence(), "Type /leave to go back to the lobby. The table stays open."]

    def side_text(self, players, facts=(), width=32):
        """The text beside the board, in the panel's order (docs/design.md): the players, then the turn, the
        clock, the last move and the game's facts. players: [(seat, mark, score)]."""
        out = []
        started = bool(self.v and self.v.get("rows"))
        for seat, mark, score in players:
            tomove = started and self.playing() and self.v.get("turn") == seat
            empty = seat >= len(self.names) or self.names[seat] in ("", "-")
            label = ("(empty seat)" if empty else self.names[seat]) + (" (you)" if seat == self.me else "")
            head = "%s %s%s" % (">" if tomove else " ", mark + " " if mark else "", label)
            out.append(head[:width - len(score) - 1].ljust(width - len(score)) + score)
        out.append("")
        clock, _ = self.clock_sentence()
        first = self.over_sentence() if started and not self.playing() else self.turn_sentence()
        for text in [first, clock, self.last_said() if started else ""] + list(facts):
            out += textwrap.wrap(text, width) if text else []
        return out

    @staticmethod
    def beside(board, side, col):
        """The board's lines with the side text beside them from column col, the rest below."""
        out = []
        for i in range(max(len(board), len(side))):
            left = board[i] if i < len(board) else ""
            right = side[i] if i < len(side) else ""
            out.append((left.ljust(col) + right if right else left).rstrip()[:80])
        return out

    def plain_log(self, n=4):
        return [" " + l[:78] for l in self.log[-n:]]

    async def draw(self):
        self.io.write("\n".join(self.lines()) + "\n" + self.full_prompt())
        await self.io.flush()
        self.drawn = True

    async def notice(self, text):
        self.io.write(text + "\n" + self.full_prompt())
        await self.io.flush()

    def full_prompt(self):
        """The prompt, or while an offer waits, its question: the dialog of plain mode."""
        o = self.offer()
        if o:
            return "\n".join(o[2]) + "\nType Y to accept, or N to decline. > "
        return self.prompt()

    async def run(self):
        if not await self.open_view():
            return "That table can't be shown."
        await self.draw()
        buf = ""
        while True:
            k = await self.io.readkey(timeout=min(POLL_SECS, self.tick_in() or POLL_SECS))
            if k is None:
                new = await self.pull()
                if any(kind in self.BOARD_KINDS for kind, _ in new):
                    await self.refresh()
                    await self.draw()
                elif any(d for _, d in new):
                    await self.notice("\n".join(" " + self.as_sentence(d) for _, d in new if d))
                await self.tick()
                continue
            if await self.key_seen(k):
                continue
            if k == "ENTER":
                line, buf = buf.strip(), ""
                if self.io.server_echo:
                    self.io.write("\n")
                if await self.line_in(line) == "menu":
                    return None
                await self.tick()
            elif k == "BS":
                if buf:
                    buf = buf[:-1]
                    if self.io.server_echo:
                        self.io.write("\b \b")
                        await self.io.flush()
            elif k == "ESC":
                if not buf:
                    return None
                if self.io.server_echo:             # Esc with something typed clears it
                    self.io.write("\b \b" * len(buf))
                    await self.io.flush()
                buf = ""
            elif isinstance(k, str) and len(k) == 1 and len(buf) < 60:
                buf += k
                if self.io.server_echo:
                    self.io.write(k)
                    await self.io.flush()

    async def line_in(self, line):
        """A line typed at the prompt. A line with a slash is a command or a move. An empty line draws the
        board again. Any other line is said to the table, except one that is a move, which is not sent:
        the answer says how to play it or say it."""
        self.drawn = False
        o = self.offer()
        if o and line.lower() in ("y", "n", "yes", "no"):
            yes = line.lower().startswith("y")
            err = await self.play_word(o[4] if yes else o[5])
            return await (self.notice(err) if err else self.draw())
        if not line:
            await self.reload()
            return await self.draw()
        if line.startswith("/"):
            if await self.run_command(line[1:].strip()) == "menu":
                return "menu"
        else:
            p = line.split()
            if self.me >= 0 and p[0].lower() not in self.COMMANDS and self.move_word(p[0].lower(), p):
                self.tell("To play %s, type /%s. To say it, type /say %s." % (line, line, line))
            else:
                err = await self.chat(line)
                if err:
                    self.tell(err)
                await self.said()
        if not self.drawn:
            self.io.write(self.full_prompt())
            await self.io.flush()
        return None

    async def command(self, line):
        """A command line, without its slash (also for the tests)."""
        return await self.run_command(line)

    def tell(self, text, attr=None):
        self.io.write(text + "\n")

    async def show_lines(self, title, lines):
        self.io.write(title + "\n" + "".join("  %s\n" % l for l in lines))

    async def ask_yes(self, title, lines, verb, yes=None):
        self.io.write(" ".join(lines) + " Type Y for yes, or N for no. ")
        await self.io.flush()
        return (await self.io.readline()).strip().lower().startswith("y")

    PLAIN_EVERY = ["Every game",
                   "Type /resign to resign, after asking first. A line without a slash is",
                   "said to the table. Type /leave to go back to the lobby, and the game",
                   "goes on without you."]

    async def help(self, page=1):
        if page == 2:
            lines = self.reference() + ["", self.EVERY_TABLE]
        else:
            lines = ["Commands"] + self.HELP_COMMANDS + [""] + self.PLAIN_EVERY + ["", "Type /help 2 for the reference."]
        self.io.write("".join(" %s\n" % l if l else "\n" for l in lines))

    async def said(self):
        new = await self.pull()
        for _, d in new:
            if d:
                self.io.write(" " + self.as_sentence(d) + "\n")

    @staticmethod
    def as_sentence(text):
        """A log line printed between prompts: chat as it was said, an event as a sentence."""
        return text if text.startswith("<") or text.endswith((".", "!", "?")) else text + "."

    async def shared(self, line):
        """As on the full screen, and the board is printed again when the line changed it."""
        before = self.v
        self.typed_reset()
        lines = answer_lines(await self.s.command(self, line, self.tid))
        if self.s.quitting:
            return "menu"
        new = await self.pull()
        await self.refresh()
        if self.v != before:
            await self.draw()
        else:
            for _, d in new:
                if d:
                    self.io.write(" " + self.as_sentence(d) + "\n")
        await self.answered(line, lines)
        return None

# The text screens of every game, from its module in adapters/games/: game type -> (full-screen class, plain
# class). Worked out from the registry when used, so a new game module needs no edit here.
RENDERERS = games.Lookup(lambda m: (m.AnsiScreen, m.PlainScreen))

# Names that moved to adapters/games/ and are still found here (tests and older code use them).
_MOVED = {"AnsiReversi": "reversi", "PlainReversi": "reversi", "AnsiBackgammon": "backgammon",
          "PlainBackgammon": "backgammon", "BgBase": "backgammon", "bg_glyphs": "backgammon"}


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
        self.since = getattr(io, "since", None) or time.time()     # signed in: the time connected counts from here
        self.history = []           # the command lines run, for Up and Down in command mode
        self.quitting = False       # /quit at a table, said yes to: the session ends
        self.practice_next = False  # /practice at a table: the lobby opens the practice game

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
        if not names:
            return "Nobody is online now."
        text = "%s %s online now." % (and_list(names), "is" if len(names) == 1 else "are")
        return text if len(text) <= COLS - 2 else "%d players are online now, %s and more." % (
            len(names), ", ".join(names[:5]))

    def game_name(self, key):
        g = next((g for g in self.games if g["key"] == key), None)
        return g["name"] if g else key.split("/")[0].capitalize()

    # ---- profiles and the activity (PFL, FEED; docs/protocol.md) ---------------------------------------------
    async def activity_lines(self, width=52, most=5):
        """The latest turns at public tables (FEED), each a sentence wrapped to width."""
        if not self.games:
            await self.load()
        data, final = await self.core.req("FEED %d" % most)
        if not final.startswith("OK"):
            return ["That didn't work. %s" % sentence(err_text(final))]
        out = []
        for t in parse_turns(data):
            out += textwrap.wrap("%s: %s" % (turn_time(t["at"]), turn_sentence(t, self.game_name(t["key"]))),
                                 width, subsequent_indent="  ")
        return out or ["No turns at public tables yet."]

    async def cmd(self, line, tid=None, sure=False):
        """One command line on the server: CMD [table] [YES] /line (docs/protocol.md, Commands) -> (the dialog
        it asks for as (kind, table), or None, and the lines it answered)."""
        if len(line) + 1 > CMD_MAX:
            return None, ["A command line takes up to %d characters." % CMD_MAX]
        head = " ".join(w for w in (tid or "", "YES" if sure else "") if w)
        data, final = await self.core.req("CMD %s/%s" % (head + " " if head else "", line))
        if not final.startswith("OK"):
            return None, ["Not so fast. Wait a moment and type it again." if final.startswith("ERR RATE")
                          else "That didn't work. %s" % sentence(err_text(final))]
        dialog, lines = None, []
        for l in data:
            f = l.split()
            if f[:1] == ["DLG"] and len(f) >= 2:
                dialog = (f[1], f[2] if len(f) > 2 else None)
            elif f[:1] == ["INF"]:
                lines.append(l[4:])
        return dialog, lines

    async def command(self, view, line, tid=None):
        """A command line on the server, asking first where the server asks: the dialog on view (its ask_yes),
        with the server's words, and its Yes sends the confirmed form -> the lines to show. A confirmed /quit
        signs you out, and the session ends (quitting)."""
        dialog, lines = await self.cmd(line, tid)
        if dialog is None:
            return lines
        kind, at = dialog
        words = dialog_lines(" ".join(l for l in lines if l.strip()))
        if not await view.ask_yes(DIALOG_TITLES.get(kind, kind.capitalize()), words, kind):
            return []
        _, lines = await self.cmd(kind, at, sure=True)
        if kind == "quit":
            self.quitting = True
        return lines

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
            return None, "That didn't work. %s" % sentence(err_text(final))
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
                    return "Table %s is already playing. Type /watch %s to watch it." % (tid, tid)
                watch = True
            _, final = await self.core.req("%s %s" % ("SEE" if watch else "JON", tid))
            if not final.startswith("OK"):
                return "That didn't work. %s" % sentence(err_text(final))
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

    async def practice(self):
        """Open the practice game (adapters/telnet_practice.py) on the full screen; back when the player leaves."""
        from .telnet_practice import run_practice
        self.practice_next = False
        await run_practice(self)

    async def offer_practice(self):
        """Once, at a sign-in on the full screen: ask whether the player wants the practice game, and keep the
        answer with the player (PRF) so that the question never comes back."""
        load = getattr(self.core, "load_pref", None)
        if not load or await load(PRACTICE_PREF, PRACTICE_STATES, "new") != "new":
            return
        menu = AnsiMenu(self)
        yes = await menu.dialog("Practice game", ["Would you like a two-minute practice game to learn",
                                                  "the keys? It is not played on the parlor."],
                                ("Yes", "No"), 0, "Press Y for yes, or N for no.")
        await self.core.save_pref(PRACTICE_PREF, "asked")
        if yes:
            await self.practice()

    async def run(self):
        mouse = self.ansi and getattr(self.io, "mouse", False)
        if mouse:
            self.io.write(MOUSE_ON)
        try:
            if self.ansi:
                await self.offer_practice()
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
        value = await (io.read_secret() if ask.secret else io.readline())
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
                io.write("A new account. %s\n" % NEW_NAME_RULE)
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
    io.write("\nLetters can move the cursor as the arrows do, and the arrows work too.\n")
    for i, m in enumerate(MOVE_ORDER, 1):
        io.write("  %d) %s\n" % (i, move_label(m)))
    io.write("They are kept for your next visit, and /keys in the lobby changes them.\nChoice [1]: ")
    await io.flush()
    choice = (await io.readline()).strip()
    numbers = [str(i) for i in range(1, len(MOVE_ORDER) + 1)]
    return MOVE_ORDER[numbers.index(choice)] if choice in numbers else "arrows"


async def title_page(io, mode, ticket=None):
    """ParlorNet's title (adapters/title.py) as a page of its own, in the display mode chosen at the start,
    until Enter. False if the window is smaller than 80x24, which has no title page; None if no Enter came."""
    if io.size and (io.size[0] < COLS or io.size[1] < ROWS):
        return False
    io.charset = CHARSET[mode]
    io.write(title.page(mode))
    io.charset = TelnetIO.charset
    await io.flush()
    try:
        await asyncio.wait_for(io.readline(), LOGIN_SECS)
    except asyncio.TimeoutError:
        io.write("\nNo key came; bye.\n")
        await io.flush()
        return None
    if ticket is not None:
        ticket.touch()
    io.write(CLEAR if mode != "plain" else "\n")
    return True


async def pick_display(io, guess):
    """The display menu, opened by Enter at the start: a sample line in each mode, the detected one first
    in line as the default (plain when the terminal named no type). Returns the mode chosen."""
    default = guess or "plain"
    io.write("\nChoose how ParlorNet draws your screen.\nPick the first line whose sample looks right.\n")
    if io.ttype:
        io.write("Your terminal says it is %s.\n" % io.ttype)
    for i, m in enumerate(ORDER, 1):
        io.write("  %d) %-36s" % (i, MODES[m]))
        if guess != "plain":
            sample = SAMPLE.get(m, SAMPLE["pc"])
            io.raw(sample.encode(CHARSET[m] if m != "plain" else "ascii"))
        io.write("%s\n" % ("   <- default" if m == default else ""))
    io.write("Choice [%d]: " % (ORDER.index(default) + 1))
    await io.flush()
    choice = (await asyncio.wait_for(io.readline(), LOGIN_SECS)).strip()
    return ORDER[int(choice) - 1] if choice in ("1", "2", "3", "4") else default


async def main_session(io, args, ticket=None, title_first=True):
    """A telnet session: the start (the terminal type, with Enter for the display menu), the title page
    (unless title_first is False, as for the RSCARDS service's text games), the message of the day, the
    sign-in, then the menu and the tables. With no Enter at the start the display is the one the terminal
    type suggests, or plain when it names none."""
    chose = await io.detect()
    ttype = io.ttype
    guess = mode_for_ttype(ttype)
    try:
        async with asyncio.timeout(LOGIN_TOTAL_SECS):
            mode = await pick_display(io, guess) if chose else guess or "plain"
            io.ansi = mode != "plain"
            titled = await title_page(io, mode, ticket) if title_first else False
            if titled is None:
                return
            if not titled:
                io.write("\n")
            motd = await MOTD.get(args)
            # the message of the day has its own title; with no title page and no message, a line says what this is
            for l in framed_motd(motd) or ([] if titled else ["ParlorNet: %s" % TAGLINE]):
                io.write("%s\n" % l)
            io.write("\n")
            if ttype and not chose:         # the display menu said it already
                io.write("Your terminal says it is %s.\n" % ttype)
            signed = await sign_in(io, args, ticket)
    except TimeoutError:
        io.write("\nSigning in took too long; bye.\n")
        await io.flush()
        return
    if signed is None:
        return
    core, name = signed
    io.since = time.time()
    try:
        io.charset = CHARSET[mode]
        if mode != "plain":
            io.char_mode()
            io.mouse = mouse_for_ttype(ttype)
        offer = mode != "plain" and not arrows_for_ttype(ttype)
        io.write("\nHello, %s.\n" % name)
        if offer:
            io.write("Your terminal may not send arrow keys. Letters can move the cursor instead.\n")
        moves = await load_moves(core) if mode != "plain" else "arrows"
        if moves != "arrows":
            io.write("You move the cursor with %s, as you chose before. /keys in the lobby changes that.\n"
                     % moves.upper())
        io.write("Press Enter for the lobby%s." % (", or K and Enter to choose movement keys" if offer else ""))
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
