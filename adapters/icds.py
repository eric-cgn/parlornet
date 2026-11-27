"""ICDS extension: Checkers for xcheckers and other clients of the Internet Checkers and Draughts Server.

ICDS (Michael Rohrer; its sixth release came in September 2000) was a telnet-style server derived from
FICS; its source is lost. Its one known client is xcheckers 2.2.3 (X11, Peter Chiocchetti), which runs
`telnet` to the server, passes what
the player types in its terminal through, and reads only a few kinds of line from the server
(from the client's source):

    a line starting "Game"                 a board in ICDS's style 1: it answers "set style 5" and "refresh"
    "<5>..." at column 0                    the style 5 board, one line of fixed fields (below)
    "icds% It is not your move."            and "icds% Illegal move.", "icds% Ambigous move.", "icds% It isn't
                                            your turn," and "icds% Game clock is paused,": it answers "refresh"

and sends "N-M" (from and to, the squares numbered 1 to 32) when a piece is dragged and dropped. So, as on
FICS, a reply comes straight after the "icds% " prompt (that is how those refusals start with it), and
output that is not a reply starts on a new line, so a board line always starts at column 0. No line but a
style 1 board ever starts with "Game", "<5>" or "icds%".

The style 5 line (read_5.c): "<5>" at 0, the game number at 3 (three digits), the variant at 6 ("e": any
letter but p, i, m and r is English checkers), the point of view at 7 (B Black, W White: the viewer's own
side at the bottom), the side to move at 8, Black's name at 9 and White's at 26 (17 characters each), the 64
cells from 43 (Black's back row first, each row from Black's left; " " a light square, "-" an empty dark
one, b w men, B W kings), then Black's and White's clocks in seconds at 107 and 111 (three characters each:
the time left on the turn for the side to move, the whole time per turn for the other, at most 599:
xcheckers keeps a clock in 8 bytes, and ten minutes or more breaks it).

Login, the FIBS way (docs/accounts.md): "login: " takes a name and "password: " its password, which answers
the core's credential question (USER); "guest" signs up (REG) with the server's steps as prompts.

Commands, in ICDS's FICS manner: match NAME [minutes] [black|white] (match bot plays the bot), accept and
decline [NAME] (an invitation, or a draw offer), moves (11-15, 15x22, 15-22-29), draw, resign, refresh,
set style 1|5, games, observe N, unobserve, who, finger, tell NAME text, say, kibitz, shout, motd, help, quit.
Lines starting with / are the shared chat commands (adapters/chatcmd.py). A move given as its two end
squares, as xcheckers sends a jump of several captures, is found among the lines of jumps from the board
and checked with CHK; when two lines of jumps join the same squares the move is refused as ambiguous. A turn
whose steps are complete is ended (END) at once. Tables made here are NEW checkers american.

    python3 -m adapters.icds --core 127.0.0.1:6590 --key /data/frontends/icds.key --port 6518
"""
import argparse
import asyncio
import itertools
import random
import re
import sys

from . import botflag, chatcmd, declare
from .common import (framed_motd, LOGIN_SECS, LOGIN_TOTAL_SECS, CoreClient, CoreError, Gate, keepalive, err_text,
                     loggable, no_wait, parse_invites, parse_players, parse_tables, peer_addr, sign_in_lines,
                     uninvite_of)
from .telnet import TelnetIO

CHECKERS = "checkers/1"
RULES = "american"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
MOVE_RE = re.compile(r"^\d{1,2}([-x]\d{1,2})+\Z")
NUM_RE = re.compile(r"^\d+\Z")
PROMPT = "icds% "
MAX_SESSIONS = 40
MAX_LOGIN_TRIES = 3
POLL_SECS = 1.0             # each session's games
INVITE_POLLS = 3            # its invitations: every third poll
PROMPT_WAIT = 120.0
MAX_GAME_NO = 999
MAX_TURN_MIN = 30 * 24 * 60
MAX_BUFFER = 256 * 1024     # a client that stops reading is dropped
MAX_JUMPS = 12              # the longest line of jumps looked for
STYLE_PREF = "style"
STYLES = ("1", "5")
PIECE5 = {".": "-", "x": "b", "X": "B", "o": "w", "O": "W"}
SIDES = ("Black", "White")
BOT_HOWTO = "To play the bot, type match bot."

BANNER = ["",
          "ParlorNet Checkers, in the manner of ICDS, the Internet Checkers and Draughts Server.",
          "This is a ParlorNet parlor; ICDS itself is no longer running. Games are American",
          "checkers, unrated. Log in with your ParlorNet name, or as guest to sign up.",
          ""]

COMMANDS = {"quit": None, "exit": None, "bye": None, "logout": None, "help": None, "who": None, "games": None,
            "match": None, "accept": None, "decline": None, "draw": None, "resign": None, "refresh": None,
            "board": None, "style": None, "set": None, "observe": None, "unobserve": None, "finger": None,
            "motd": None, "tell": "tell", "say": "say", "kibitz": "kibitz", "whisper": "kibitz", "shout": "shout"}

ids = itertools.count(1)


class Refused(Exception):
    pass


def log(sid, text):
    print("icds[%d] %s" % (sid, text), flush=True)


def verb_of(line):
    """The command a line is taken for (chat commands by their chat word), or None."""
    p = line.split()
    if not p:
        return None
    cmd = p[0].lower()
    if MOVE_RE.match(cmd):
        return "move"
    if cmd.startswith("/"):
        c = chatcmd.parse(cmd)
        return c.name if c and c.name != "unknown" else None
    if cmd not in COMMANDS:
        return None
    return COMMANDS[cmd] or cmd


class Board(str):
    """A board line: the only kind of line allowed to start with "<5>" or "Game"."""


def safe(line):
    """A line that is not a board never starts with what xcheckers takes for one (or for a refusal)."""
    if isinstance(line, Board) or not line.startswith(("Game", "<5>", "icds%")):
        return line
    return " " + line


# ---- the board ----------------------------------------------------------------------------------------

def rc(s):
    """Square 1-32 -> (row, column), row 0 Black's back row at the top as seen from White (types/checkers-1.md)."""
    r = (s - 1) // 4
    return r, 2 * ((s - 1) % 4) + (1 if r % 2 == 0 else 0)


def sq(r, c):
    """(row, column) -> square number, or 0 off the board or on a light square."""
    if 0 <= r < 8 and 0 <= c < 8 and (r + c) % 2 == 1:
        return r * 4 + c // 2 + 1
    return 0


def cells64(cells):
    """The view's 32 squares -> style 5's 64 cells: Black's back row first, each row from Black's left (the
    right as White sees it), " " a light square, "-" an empty dark one, b w B W the pieces."""
    out = []
    for r in range(8):
        for cc in range(8):
            s = sq(r, 7 - cc)
            out.append(PIECE5.get(cells[s - 1], "-") if s else " ")
    return "".join(out)


def clamp(secs):
    """A clock for the style 5 line: 0 to 599 seconds. xcheckers writes a clock as "%d : %02d" into 8 bytes
    (status.h, clockline), so ten minutes or more overruns it and breaks the client's drawing; its own
    countdown stops at 9:59 for the same reason (status.c, checkClock)."""
    return max(0, min(599, int(secs)))


def style5(no, pov, mover, black, white, cells, clocks):
    """The style 5 line: fixed fields read by offset (module docstring)."""
    return Board("<5>%03d%s%s%s%-17s%-17s%s%3d %3d" % (no % 1000, "e", pov, mover, black[:17], white[:17],
                                                        cells64(cells), clamp(clocks[0]), clamp(clocks[1])))


def piece1(ch, s):
    return {"x": " b ", "X": " B ", "o": " w ", "O": " W "}.get(ch, "%2d " % s)


def style1(no, names, cells, seat, status):
    """Style 1, for people: "Game n (black vs. white)" (xcheckers' cue to ask for style 5), then the board
    from the viewer's side, an empty dark square showing its number."""
    rows = range(7, -1, -1) if seat != 1 else range(8)
    cols = range(8) if seat == 1 else range(7, -1, -1)
    out = [Board("Game %d (%s vs. %s)" % (no, names[0], names[1])), "    +------------------------+"]
    for r in rows:
        out.append("    |" + "".join(piece1(cells[sq(r, c) - 1], sq(r, c)) if sq(r, c) else "   "
                                     for c in cols) + "|")
    out.append("    +------------------------+")
    out.append("    b B: %s (Black)   w W: %s (White)" % (names[0], names[1]))
    if status:
        out.append("    " + status)
    return out


def parse_view(lines):
    v = {"cells": None, "turn": None, "phase": "", "path": [], "offer": None, "clock": None, "moves": [],
         "status": ""}
    for l in lines:
        k, _, rest = l.partition(" ")
        f = rest.split()
        if k == "B" and len(rest) == 32:
            v["cells"] = rest
        elif k == "W":
            v["turn"] = int(f[0]) if f and f[0].isdigit() else None
        elif k == "Q":
            v["phase"] = rest
        elif k == "J":
            v["path"] = [int(x) for x in f if x.isdigit()]
        elif k == "D" and f and f[0].isdigit():
            v["offer"] = int(f[0])
        elif k == "CLK" and len(f) >= 3 and all(x.lstrip("-").isdigit() for x in f[:3]):
            v["clock"] = (int(f[0]), int(f[1]), int(f[2]))
        elif k == "M":
            v["moves"].append(rest)
        elif k == "S":
            v["status"] = rest
    return v


def jump_lines(cells, a, b, seat):
    """Every line of jumps from a to b on the board as it is, by its shape only: over an opposing piece not yet
    taken onto an empty square, at most MAX_JUMPS long. The plugin decides which of them is legal (CHK)."""
    mine, theirs = ("xX", "oO") if seat == 0 else ("oO", "xX")
    if not (1 <= a <= 32 and 1 <= b <= 32) or cells[a - 1] not in mine:
        return []
    out = []

    def walk(s, taken, path):
        if len(path) > MAX_JUMPS:
            return
        r, c = rc(s)
        for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            m, t = sq(r + dr, c + dc), sq(r + 2 * dr, c + 2 * dc)
            if not m or not t or m in taken or cells[m - 1] not in theirs:
                continue
            if cells[t - 1] != "." and t != a:
                continue
            if t == b:
                out.append(path + [t])
            walk(t, taken | {m}, path + [t])

    walk(a, frozenset(), [a])
    return out


def result_words(result, names):
    """An END payload -> (words, score) in FICS's manner: ("amy resigns", "0-1")."""
    p = result
    if p and p[0] == "win" and len(p) >= 2 and p[1] in "01":
        w = int(p[1])
        why = "has no pieces left" if len(p) > 2 and p[2] == "captured" else "cannot move"
        return "%s wins: %s %s" % (names[w], names[1 - w], why), ("1-0" if w == 0 else "0-1")
    if p and p[0] == "draw":
        return ("Game drawn by agreement" if p[1:] == ["agreed"] else
                "Game drawn: no capture and no man moved for the move limit"), "1/2-1/2"
    if p and p[0] in ("resign", "timeout") and len(p) > 1 and p[1] in "01":
        s = int(p[1])
        return ("%s resigns" % names[s] if p[0] == "resign" else "%s ran out of time" % names[s],
                "0-1" if s == 0 else "1-0")
    return "Game ended (%s)" % " ".join(p), "*"


class Hub:
    """Everyone connected through this extension, and the game numbers."""

    def __init__(self):
        self.sessions = {}      # lower name -> Session
        self.numbers = {}       # table id -> game number

    def number(self, tid):
        if tid in self.numbers:
            return self.numbers[tid]
        used = set(self.numbers.values())
        n = int(tid) if tid.isdigit() and 1 <= int(tid) <= MAX_GAME_NO and int(tid) not in used else \
            next((k for k in range(1, MAX_GAME_NO + 1) if k not in used), MAX_GAME_NO)
        self.numbers[tid] = n
        return n

    def tid_of(self, n):
        return next((tid for tid, k in self.numbers.items() if k == n), None)

    def enter(self, s):
        old = self.sessions.get(s.name.lower())
        if old is not None and old is not s:
            old.post(["You have logged in from somewhere else; this connection ends."])
            old.closing = True
            try:
                old.io.w.close()
            except Exception:
                pass
        self.sessions[s.name.lower()] = s

    def leave(self, s):
        if self.sessions.get(s.name.lower()) is s:
            del self.sessions[s.name.lower()]


class Track:
    """A game this session plays (seat 0 or 1) or watches (seat -1)."""

    def __init__(self, tid, seat):
        self.tid, self.seat = tid, seat
        self.cursor = 0
        self.names = ["?", "?"]
        self.started = False
        self.over = False
        self.path = []          # the steps of the turn being played, for the line that tells it
        self.jumped = False
        self.says = {}          # seat -> what its player plays with (CLIENT events), when known


class Session:
    def __init__(self, hub, io, core_addr, ticket=None):
        self.hub, self.io, self.core_addr = hub, io, core_addr
        self.ticket = ticket
        self.addr = peer_addr(io.w)
        self.sid = next(ids)
        self.name = None
        self.core = None
        self.core_lock = asyncio.Lock()
        self.ev_lock = asyncio.Lock()
        self.inv_lock = asyncio.Lock()
        self.table = None       # the table I sit at (open or running)
        self.tracks = {}        # table id -> Track
        self.shown = {}         # table id -> who invited me: invitations shown
        self.style = "1"
        self.closing = False
        self.wrote = False
        self.console = None

    # ---- output -----------------------------------------------------------------------------------------
    def post(self, lines, glue=False):
        """Write lines, then the prompt, in one write. glue: a reply, written straight after the prompt (as
        FICS does: "icds% Illegal move."); else the lines start on a new line."""
        w = self.io.w
        if w.is_closing():
            return
        text = ("" if glue else "\r\n") + "".join(safe(l) + "\r\n" for l in lines) if lines else ""
        text += PROMPT if self.name else ""
        if text:
            w.write(text.encode("ascii", "replace"))
            self.wrote = True
        if w.transport.get_write_buffer_size() > MAX_BUFFER:
            self.closing = True
            w.close()

    def reply(self, lines):
        self.post([lines] if isinstance(lines, str) else lines, glue=True)

    def notify(self, lines):
        self.post([lines] if isinstance(lines, str) else lines)

    async def core_req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    # ---- login ------------------------------------------------------------------------------------------
    async def login_line(self):
        try:
            line = await asyncio.wait_for(self.io.readline(), LOGIN_SECS)
        except asyncio.TimeoutError:
            raise Refused("no login")
        if self.ticket is not None:
            self.ticket.touch()
        return line.strip()

    async def login(self):
        w = self.io.w
        w.write(("\r\n".join(BANNER) + "\r\nlogin: ").encode("ascii"))
        await w.drain()
        tries = 0
        while True:
            line = ""
            while not line:
                line = await self.login_line()
            words = line.split()
            name = refusal = None
            if words[0].lower() == "guest" and len(words) == 1:
                log(self.sid, "guest")
                name, refusal = await self.register()
            elif len(words) == 1:
                name = words[0]
                log(self.sid, "login %r" % name)
                w.write(b"\r\npassword: ")
                await w.drain()
                password = await self.login_line()
                log(self.sid, "password line received (not logged)")
                refusal = await self.sign_in(name, password, "signin")
            else:
                refusal = "Log in with your name, or as guest."
            if refusal is None and self.ticket is not None and not self.ticket.sign_in():
                refusal = "Server is full, try again soon."
                tries = MAX_LOGIN_TRIES
            if refusal is None:
                break
            tries += 1
            log(self.sid, "refused: %s" % refusal)
            w.write(("\r\n%s\r\n" % refusal).encode("ascii", "replace"))
            if tries >= MAX_LOGIN_TRIES:
                await w.drain()
                raise Refused(refusal)
            w.write(b"\r\nlogin: ")
            await w.drain()
        self.name = name
        self.io.on_input = self.core.user_input
        self.style = await self.core.load_pref(STYLE_PREF, STYLES, "1")
        self.hub.enter(self)
        self.console = chatcmd.Console(
            self.reply, self.core_req, lambda: self.name,
            ["list", "resign", "invite", "open", "join", "close", "accept", "decline", "who", "top", "score",
             "note", "motd", "time", "quit", "help"],
            kinds=[CHECKERS], label="Checkers", new=RULES, handlers={"quit": self.slash_quit},
            seated=self.seated, current=lambda: self.table)
        motd = await self.core.motd()
        lines = ["", "**** Starting a ParlorNet Checkers session as %s ****" % name, ""] + framed_motd(motd) + [
            "", "Type help for the commands. Games here are American checkers and unrated.", BOT_HOWTO,
            "Boards come as text (style 1); xcheckers asks for style 5 by itself." if self.style == "1" else
            "Boards come in style 5, for xcheckers; set style 1 shows them as text."]
        self.post(lines, glue=True)
        await self.rejoin()
        await self.check_invites()

    async def sign_in(self, name, password, mode):
        if not NAME_RE.match(name):
            return "Names are 1 to 12 letters, digits, - or _."
        if self.core is None:       # only now, with a login line from the client, a connection to the core
            try:
                self.core = await CoreClient.connect(*self.core_addr)
                await self.core.source(self.addr)
            except (OSError, asyncio.TimeoutError, CoreError):
                self.core = None
                return "The game server isn't answering. Try again soon."
        unknown = "There is no account named %s here. New here? Log in as guest." % name
        return await sign_in_lines(self.core, name, password, self.prompt, self.show, mode, unknown)

    async def register(self):
        """guest: the name wanted, then the server's sign-up steps as prompts. -> (name, refusal)"""
        self.io.w.write(b"\r\nYou are signing up for a ParlorNet name."
                        b"\r\nName you want (letters, digits, - and _, up to 12): ")
        await self.io.w.drain()
        name = await self.login_line()
        log(self.sid, "sign-up name %r" % name)
        return name, await self.sign_in(name, "", "signup")

    async def show(self, lines):
        self.io.w.write("".join("\r\n" + l for l in lines).encode("ascii", "replace"))
        await self.io.w.drain()

    async def prompt(self, label, secret=False):
        """A sign-in question with the server's own label; what is typed back (not logged)."""
        self.io.w.write(("\r\n%s: " % label).encode("ascii", "replace"))
        await self.io.w.drain()
        try:
            line = (await asyncio.wait_for(self.io.readline(), PROMPT_WAIT)).strip()
        except asyncio.TimeoutError:
            return ""
        log(self.sid, "sign-in answer received (not logged)")
        if self.ticket is not None:
            self.ticket.touch()
        return line

    # ---- commands ---------------------------------------------------------------------------------------
    async def command(self, line):
        p = line.split()
        if not p:
            return
        cmd = p[0].lower()
        if cmd.startswith("/"):
            if not await self.console.handle(line):
                self.reply("Type /help for the commands.")
            return
        if MOVE_RE.match(cmd):
            await self.move(cmd)
        elif cmd in ("quit", "exit", "bye", "logout"):
            self.reply("Thank you for playing. Goodbye.")
            self.closing = True
        elif cmd == "help":
            self.reply(HELP)
        elif cmd == "who":
            await self.who()
        elif cmd == "games":
            await self.games()
        elif cmd == "match":
            await self.match(p[1:])
        elif cmd == "accept":
            await self.accept(p[1:])
        elif cmd == "decline":
            await self.decline(p[1:])
        elif cmd == "draw":
            await self.draw()
        elif cmd == "resign":
            await self.resign()
        elif cmd in ("refresh", "board"):
            await self.refresh(p[1:])
        elif cmd == "style" or (cmd == "set" and len(p) >= 2 and p[1].lower() == "style"):
            await self.set_style(p[1:] if cmd == "style" else p[2:])
        elif cmd == "set":
            self.reply("%s is not a setting here; set style 1 or set style 5 is." % (p[1] if len(p) > 1 else "That"))
        elif cmd == "observe":
            await self.observe(p[1:])
        elif cmd == "unobserve":
            await self.unobserve(p[1:])
        elif cmd == "finger":
            await self.finger(p[1:])
        elif cmd == "motd":
            self.reply(framed_motd(await self.core.motd()) or ["There is no message of the day."])
        elif cmd == "tell" and len(p) >= 3:
            await self.tell(p[1], line.split(None, 2)[2])
        elif cmd == "say" and len(p) >= 2:
            await self.say(line.split(None, 1)[1])
        elif cmd in ("kibitz", "whisper") and len(p) >= 2:
            await self.say(line.split(None, 1)[1])
        elif cmd == "shout" and len(p) >= 2:
            text = line.split(None, 1)[1]
            for s in list(self.hub.sessions.values()):
                if s is not self:
                    s.notify("%s shouts: %s" % (self.name, text))
            self.reply("(shouted to %d)" % (len(self.hub.sessions) - 1))
        elif cmd in ("tell", "say", "kibitz", "whisper", "shout"):
            self.reply("Usage: %s %stext" % (cmd, "NAME " if cmd == "tell" else ""))
        else:
            self.reply("Command not found. Type help for the commands.")     # never repeated: it may be a password

    async def slash_quit(self, cmd):
        self.reply("Thank you for playing. Goodbye.")
        self.closing = True

    async def seated(self, tid, seat, opp, how):
        """A / command seated me (chatcmd): follow that table."""
        await self.leave_open_other(tid)
        self.track(tid, seat)

    async def leave_open_other(self, keep):
        """Before following a new table: stop following my open table if it is another one."""
        if self.table and self.table != keep:
            tr = self.tracks.get(self.table)
            if tr and not tr.started:
                await self.core_req("LVE %s" % self.table)
                self.untrack(self.table)

    # ---- who, games, finger -----------------------------------------------------------------------------
    async def tables(self):
        data, final = await self.core_req("LST all")      # who and games need every table, not the capped list
        return parse_tables(data) if final.startswith("OK") else []

    async def online(self):
        data, final = await self.core_req("WHO")
        players = parse_players(data) if final.startswith("OK") else []
        return botflag.listed(players), {n.lower() for n, bot in players if bot}

    async def who(self):
        names, bots = await self.online()
        playing, waiting = {}, set()
        for t in await self.tables():
            if t["key"] != CHECKERS:
                continue
            for n in t["names"]:
                if t["status"] == "playing":
                    playing[n.lower()] = self.hub.number(t["id"])
                elif t["status"] == "open":
                    waiting.add(n.lower())
        lines = ["Players online:"]
        for n in sorted(names, key=str.lower):
            k = n.lower()
            what = ("the bot: type match %s to play it" % n if k in bots else
                    "playing game %d" % playing[k] if k in playing else
                    "waiting at an open table" if k in waiting else "")
            lines.append(" %-12s %s%s" % (n, "(icds) " if k in self.hub.sessions else "", what))
        lines.append("%d players displayed." % len(names))
        self.reply(lines)

    async def games(self):
        out = []
        for t in sorted(await self.tables(), key=lambda t: int(t["id"]) if t["id"].isdigit() else 0):
            if t["key"] != CHECKERS or t["status"] != "playing" or len(t["seated"]) < 2:
                continue
            out.append(" %3d %s vs. %s" % (self.hub.number(t["id"]), t["seated"][0], t["seated"][1]))
        self.reply(["Checkers games in progress (observe N to watch one):"] + out + ["%d games displayed." % len(out)]
                   if out else "There are no Checkers games in progress.")

    async def finger(self, args):
        if not args:
            self.reply("Usage: finger NAME")
            return
        k = args[0].lower()
        names, bots = await self.online()
        disp = next((n for n in names if n.lower() == k), None)
        if disp is None:
            self.reply("%s is not online." % args[0])
            return
        if k in bots:
            self.reply("%s is the server's bot. Type match %s to play it." % (disp, disp))
            return
        lines = ["%s is online %s." % (disp, "here (ICDS)" if k in self.hub.sessions else "elsewhere on this parlor")]
        for t in await self.tables():
            if t["key"] != CHECKERS or k not in [n.lower() for n in t["names"]]:
                continue
            if t["status"] == "playing":
                lines.append("%s plays game %d (%s vs. %s)." % (disp, self.hub.number(t["id"]), t["seated"][0],
                                                               t["seated"][1]))
            elif t["status"] == "open" and t["vis"] == "public":
                lines.append("%s waits at open table %s: /join %s sits down there." % (disp, t["id"], t["id"]))
        self.reply(lines)

    # ---- tables -----------------------------------------------------------------------------------------
    def track(self, tid, seat):
        tr = Track(tid, seat)
        self.tracks[tid] = tr
        if seat >= 0:
            self.table = tid
        return tr

    def untrack(self, tid):
        self.tracks.pop(tid, None)
        if self.table == tid:
            self.table = None

    def mine(self):
        return self.tracks.get(self.table) if self.table else None

    async def free_seat(self):
        """Before a new game: leave my open table; False if I play a running one."""
        tr = self.mine()
        if tr:
            await self.poll_track(tr)
        tr = self.mine()
        if tr and tr.started:
            self.reply("You are playing game %d. Finish or resign it first." % self.hub.number(tr.tid))
            return False
        if tr:
            await self.core_req("LVE %s" % tr.tid)
            self.untrack(tr.tid)
        return True

    async def invitation_from(self, name=None):
        """My invitation to a Checkers table from name (any one when name is None), or None."""
        data, final = await self.core_req("IVL")
        if not final.startswith("OK"):
            return None
        invs = [i for i in parse_invites(data) if i["key"] == CHECKERS]
        if name is None:
            return invs[0] if len(invs) == 1 else None
        return next((i for i in invs if i["from"].lower() == name.lower()), None)

    async def match(self, args):
        """match NAME [minutes] [black|white]: NAME's challenge taken up, NAME's open table joined, or a new
        table with NAME invited to the other seat."""
        opp = next((a for a in args if not NUM_RE.match(a) and a.lower() not in ("black", "white", "b", "w")),
                   None)
        nums = [int(a) for a in args if NUM_RE.match(a)]
        color = next(({"b": 0, "black": 0, "w": 1, "white": 1}[a.lower()] for a in args
                       if a.lower() in ("black", "white", "b", "w")), None)
        if not opp or not NAME_RE.match(opp):
            self.reply("Usage: match NAME [minutes a turn] [black|white]")
            return
        if opp.lower() == self.name.lower():
            self.reply("You can't match yourself.")
            return
        inv = await self.invitation_from(opp)
        if inv:
            if await self.free_seat():
                await self.join(inv["id"], inv["from"])
            return
        if not await self.free_seat():
            return
        waiting = next((t for t in await self.tables() if t["key"] == CHECKERS and t["status"] == "open"
                        and t["vis"] == "public" and t["filled"] < t["seats"] and t["names"]
                        and all(n.lower() == opp.lower() for n in t["names"]) and not botflag.bot_waits(t)), None)
        if waiting:
            await self.join(waiting["id"], waiting["names"][0])
            return
        minutes = min(nums[0], MAX_TURN_MIN) if nums and nums[0] > 0 else None
        # a public table, as every game on FICS and ICDS could be observed; the other seat is held for opp
        _, final = await self.core_req("NEW %s %s%s" % (CHECKERS, RULES, " turn=%dm" % minutes if minutes else ""))
        if not final.startswith("OK"):
            self.reply("Can't start a game: %s" % err_text(final))
            return
        tid = final.split()[1]
        seat = color if color is not None else random.randint(0, 1)
        _, final = await self.core_req("JON %s %d" % (tid, seat))
        if not final.startswith("OK"):
            await self.core_req("LVE %s" % tid)
            self.reply("Can't sit down: %s" % err_text(final))
            return
        self.track(tid, seat)
        _, final = await self.core_req("INV %s %s %d" % (tid, opp, 1 - seat))
        if not final.startswith("OK"):
            await self.core_req("LVE %s" % tid)
            self.untrack(tid)
            self.reply("There is no player called %s here." % opp if final.startswith("ERR NOPLAYER")
                       else "Can't challenge %s: %s" % (opp, err_text(final)))
            return
        target = self.hub.sessions.get(opp.lower())
        if target:
            asyncio.ensure_future(target.check_invites())
        side = SIDES[seat]
        if final.split()[1:] == ["offline"]:
            self.reply(["Issuing: %s (%s) %s, unrated checkers." % (self.name, side, opp),
                        "%s is not online now. The challenge waits for an hour." % opp])
        elif botflag.is_bot_name(opp):
            self.reply("Issuing: %s (%s) %s, unrated checkers. The bot sits down in a few seconds." %
                       (self.name, side, opp))
        else:
            self.reply("Issuing: %s (%s) %s, unrated checkers. Your challenge is pending." % (self.name, side, opp))

    async def join(self, tid, opp):
        _, final = await self.core_req("JON %s" % tid)
        if not final.startswith("OK"):
            self.reply("Can't sit down: %s" % err_text(final))
            return
        self.shown.pop(tid, None)
        self.track(tid, int(final.split()[1]))
        self.reply("You accept the match with %s." % opp)
        await self.poll_track(self.mine())

    async def accept(self, args):
        """accept [NAME]: a draw offer waiting for my answer, else NAME's (or the only) challenge."""
        if not args:
            tr = self.mine()
            v = await self.view(tr) if tr and tr.started else None
            if v and v["phase"] == "draw" and v["turn"] == tr.seat:
                await self.answer_draw("accept")
                return
        inv = await self.invitation_from(args[0] if args else None)
        if inv is None:
            self.reply("There is no offer to accept." if not args else "There is no challenge from %s." % args[0])
            return
        if await self.free_seat():
            await self.join(inv["id"], inv["from"])

    async def decline(self, args):
        if not args:
            tr = self.mine()
            v = await self.view(tr) if tr and tr.started else None
            if v and v["phase"] == "draw" and v["turn"] == tr.seat:
                await self.answer_draw("reject")
                return
        inv = await self.invitation_from(args[0] if args else None)
        if inv is None:
            self.reply("There is no offer to decline." if not args else "There is no challenge from %s." % args[0])
            return
        self.shown.pop(inv["id"], None)
        await self.core_req("UNV %s %s" % (inv["id"], self.name))
        self.reply("You decline the match offer from %s." % inv["from"])

    async def check_invites(self):
        """My invitations (IVL), each new one shown once; one that went away unanswered, a line."""
        if self.closing or not self.name or self.core is None:
            return
        async with self.inv_lock:
            try:
                data, final = await self.core_req("IVL")
            except (CoreError, OSError, asyncio.TimeoutError):
                self.closing = True
                return
            if not final.startswith("OK"):
                return
            invs = {i["id"]: i for i in parse_invites(data)}
            out = []
            for tid in [t for t in self.shown if t not in invs]:
                frm = self.shown.pop(tid)
                if tid != self.table:
                    out.append("The match offer from %s is no longer open." % frm)
            for tid, i in invs.items():
                if tid in self.shown:
                    continue
                self.shown[tid] = i["from"]
                if i["key"] != CHECKERS:
                    game = i["key"].split("/")[0].capitalize()
                    out.append("%s invites you to %s at table %s. You can play it on the web or by telnet." %
                               (i["from"], game, i["id"]))
                    continue
                theirs = SIDES[1 - i["seat"]] if i["seat"] in (0, 1) else "either side"
                out += ["Challenge: %s (%s) %s, unrated checkers (%s), table %s." %
                        (i["from"], theirs, self.name, i["rules"], i["id"]),
                        'You can "accept %s" or "decline %s".' % (i["from"], i["from"])]
            if out:
                self.notify(out)

    # ---- the game ---------------------------------------------------------------------------------------
    async def view(self, tr, flags="TM"):
        data, final = await self.core_req("VUE %s %s" % (tr.tid, flags))
        return parse_view(data) if final.startswith("OK") else None

    async def move(self, token):
        tr = self.mine()
        if tr is None or not tr.started:
            if tr is not None:
                await self.poll_track(tr)
                tr = self.mine()
            if tr is None or not tr.started:
                self.reply("You are not playing a game.")
                return
        v = await self.view(tr)
        if v is None or v["cells"] is None:
            self.reply("You are not playing a game.")
            return
        if v["turn"] != tr.seat:
            self.reply("It is not your move.")
            return
        if v["phase"] == "draw":
            self.reply("Illegal move. Answer the draw offer first: accept or decline.")
            return
        steps, refusal = await self.resolve(tr, token, v)
        if refusal:
            self.reply(refusal)
            return
        _, final = await self.core_req("MOV %s %s" % (tr.tid, steps))
        if not final.startswith("OK"):
            reason = err_text(final)
            self.reply("Illegal move. %s%s." % (reason[:1].upper(), reason[1:].rstrip(".")))
            return
        if final.split()[1:] == ["done"]:
            _, final = await self.core_req("END %s" % tr.tid)
        elif final.split()[1:] == ["more"]:
            await self.poll_track(tr, quiet=True)
            await self.send_board(tr, "Keep jumping: the jump must go on.")
            return
        await self.poll_track(tr)

    async def resolve(self, tr, token, v):
        """A typed or dragged move -> (the step tokens to play, None) or (None, the refusal to show). Two
        squares that are a legal step are that step; else the lines of jumps that join them, checked with CHK:
        one is played, two or more are ambiguous."""
        sqs = [int(x) for x in re.split(r"[-x]", token)]
        if len(sqs) > 2 or "x" in token:
            return token, None
        a, b = sqs
        legal = {}
        for m in v["moves"]:
            p = re.split(r"[-x]", m)
            if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
                legal[(int(p[0]), int(p[1]))] = m
        if (a, b) in legal:
            return legal[(a, b)], None
        good = []
        for line in jump_lines(v["cells"], a, b, tr.seat):
            chain = "x".join(str(s) for s in line)
            _, final = await self.core_req("CHK %s %s" % (tr.tid, chain))
            if final.startswith("OK"):
                good.append(chain)
        if len(good) == 1:
            return good[0], None
        if len(good) > 1:
            # "Ambigous" as xcheckers matches it (read_5.c), so that it asks for the board again
            return None, "Ambigous move. Two lines of jumps go from %d to %d: drag one jump at a time." % (a, b)
        return token, None      # the plugin says why it is not legal

    async def answer_draw(self, word):
        tr = self.mine()
        _, final = await self.core_req("MOV %s %s" % (tr.tid, word))
        if not final.startswith("OK"):
            self.reply("You can't do that now: %s" % err_text(final))
            return
        self.reply("You %s the draw offer." % ("accept" if word == "accept" else "decline"))
        await self.poll_track(tr)

    async def draw(self):
        """draw: accept the other player's offer, or offer one."""
        tr = self.mine()
        if tr is None or not tr.started:
            self.reply("You are not playing a game.")
            return
        v = await self.view(tr)
        if v and v["phase"] == "draw" and v["turn"] == tr.seat:
            await self.answer_draw("accept")
            return
        _, final = await self.core_req("MOV %s draw" % tr.tid)
        if not final.startswith("OK"):
            self.reply("You can't offer a draw: %s" % err_text(final))
            return
        self.reply("You offer a draw.")
        await self.poll_track(tr, quiet=True)

    async def resign(self):
        tr = self.mine()
        if tr is None:
            self.reply("You are not playing a game.")
            return
        await self.poll_track(tr)
        tr = self.mine()
        if tr is None:
            self.reply("You are not playing a game.")
            return
        if not tr.started:
            await self.core_req("LVE %s" % tr.tid)
            self.untrack(tr.tid)
            self.reply("You withdraw your match offer.")
            return
        _, final = await self.core_req("RES %s" % tr.tid)
        if not final.startswith("OK"):
            self.reply("You can't resign: %s" % err_text(final))
            return
        await self.poll_track(tr)

    async def refresh(self, args):
        if args:
            tid = self.game_tid(args[0])
            tr = self.tracks.get(tid) if tid else None
            if tid and tr is None:
                tr = await self.watch(tid)
                if tr is None:
                    return
                await self.send_board(tr)
                await self.core_req("LVE %s" % tid)
                return
        else:
            tr = self.mine() or next((t for t in self.tracks.values() if t.seat < 0), None)
        if tr is None:
            self.reply("You are neither playing nor observing a game.")
            return
        if not tr.started:
            await self.poll_track(tr, quiet=True)
            if not tr.started:
                self.reply("Your game has not started yet.")
                return
        await self.send_board(tr)

    async def set_style(self, args):
        if not args or args[0] not in STYLES:
            self.reply("Style %s is not offered; styles 1 (text) and 5 (xcheckers) are." % (args[0] if args else "?"))
            return
        self.style = args[0]
        await self.core.save_pref(STYLE_PREF, self.style)
        self.reply("Style %s set." % self.style)

    def game_tid(self, word):
        n = word.lstrip("#")
        return self.hub.tid_of(int(n)) or n if n.isdigit() else None

    async def watch(self, tid):
        _, final = await self.core_req("SEE %s" % tid)
        if not final.startswith("OK"):
            self.reply("You can't observe game %s: %s" % (tid, err_text(final)))
            return None
        tr = Track(tid, -1)
        t = next((t for t in await self.tables() if t["id"] == tid), None)
        if t is None or t["key"] != CHECKERS or t["status"] != "playing":
            await self.core_req("LVE %s" % tid)
            self.reply("There is no Checkers game %s." % tid)
            return None
        tr.names = t["seated"][:2]
        tr.started = True
        await self.catch_up(tr)
        return tr

    async def observe(self, args):
        if not args:
            self.reply("Usage: observe N (games lists them)")
            return
        tid = self.game_tid(args[0])
        if tid is None:
            self.reply("There is no game %s." % args[0])
            return
        if tid in self.tracks:
            await self.send_board(self.tracks[tid])
            return
        tr = await self.watch(tid)
        if tr is None:
            return
        self.tracks[tid] = tr
        self.reply("You are now observing game %d." % self.hub.number(tid))
        await self.send_board(tr)

    async def unobserve(self, args):
        watched = [t for t in self.tracks.values() if t.seat < 0]
        if args:
            tid = self.game_tid(args[0])
            watched = [t for t in watched if t.tid == tid]
        if not watched:
            self.reply("You are not observing any games.")
            return
        for tr in watched:
            self.untrack(tr.tid)
            await self.core_req("LVE %s" % tr.tid)
        self.reply("Removing game %s from observation list." % ", ".join(str(self.hub.number(t.tid))
                                                                        for t in watched))

    async def tell(self, who, text):
        target = self.hub.sessions.get(who.lower())
        tr = self.mine()
        if target:
            target.notify("%s tells you: %s" % (self.name, text))
            self.reply("(told %s)" % target.name)
        elif tr and tr.started and who.lower() in [n.lower() for n in tr.names]:
            await self.say(text)
        else:
            self.reply("%s is not logged in here and not at your table." % who)

    async def say(self, text):
        tr = self.mine() or next((t for t in self.tracks.values() if t.seat < 0), None)
        if tr is None:
            self.reply("You are not playing or observing a game.")
            return
        await self.core_req("CHT %s %s" % (tr.tid, text[:56]))
        self.reply("(told game %d)" % self.hub.number(tr.tid))

    # ---- events -----------------------------------------------------------------------------------------
    async def rejoin(self):
        """At login: a Checkers game this player is seated in is followed again (a running one first)."""
        me = self.name.lower()
        mine = [t for t in await self.tables() if t["key"] == CHECKERS and t["status"] in ("playing", "open")
                and me in [n.lower() for n in t["names"]]]
        if not mine:
            return
        t = sorted(mine, key=lambda t: (t["status"] != "playing", int(t["id"]) if t["id"].isdigit() else 0))[0]
        tr = self.track(t["id"], [n.lower() for n in t["seated"]].index(me))
        tr.names = t["seated"][:2]
        await self.catch_up(tr)
        if t["status"] == "playing":
            tr.started = True
            self.notify("Your game %d (%s vs. %s) continues." % (self.hub.number(tr.tid), tr.names[0], tr.names[1]))
            await self.send_board(tr)

    async def catch_up(self, tr):
        """A table's past events read without output: names, cursor, whether it started."""
        data, final = await self.core_req("SIN %s 0" % tr.tid)
        for l in data:
            p = l.split()
            if len(p) < 4 or p[0] != "EVT" or p[1] != tr.tid:
                continue
            tr.cursor = max(tr.cursor, int(p[2]))
            c = chatcmd.client_of(p[4:]) if p[3] == "CLIENT" else None
            if c and c[0] < 2:
                tr.says[c[0]] = c[1]
            if p[3] == "JOIN" and len(p) >= 6 and p[4].isdigit() and int(p[4]) < 2:
                tr.names[int(p[4])] = p[5]
            elif p[3] == "START":
                tr.started = True
            elif p[3] == "END":
                tr.over = True
        if final.startswith("OK") and len(final.split()) > 1 and final.split()[1].isdigit():
            tr.cursor = max(tr.cursor, int(final.split()[1]))

    async def poll_track(self, tr, quiet=False):
        async with self.ev_lock:
            if tr is None or self.tracks.get(tr.tid) is not tr:
                return
            data, final = await self.core_req("SIN %s %d" % (tr.tid, tr.cursor))
            if final.startswith("ERR"):
                self.untrack(tr.tid)
                return
            await self.handle_events(tr, data, quiet)

    async def handle_events(self, tr, lines, quiet=False):
        """The table's new events -> lines for the player: the opponent's turn in words, then the board when
        the turn passes or the game starts, and the end in FICS's manner. quiet: only the bookkeeping."""
        board = created = False
        ended = None
        msgs = []
        says = chatcmd.join_says(lines)     # what each player who sat down plays with
        for l in lines:
            p = l.split()
            if len(p) < 4 or p[0] != "EVT" or p[1] != tr.tid or not p[2].isdigit() or int(p[2]) <= tr.cursor:
                continue
            tr.cursor = int(p[2])
            kind, rest = p[3], p[4:]
            seat = int(rest[0]) if rest and rest[0].isdigit() else -1
            change = chatcmd.change_of(kind, rest, says, tr.seat)
            if kind == "CLIENT" and 0 <= seat < 2 and chatcmd.client_of(rest):
                tr.says[seat] = chatcmd.client_of(rest)[1]
            if kind == "JOIN" and len(rest) >= 2 and 0 <= seat < 2:
                tr.names[seat] = rest[1]
            elif change and change[0] < 2 and tr.started:      # plays on with another program
                msgs.append(chatcmd.client_change(tr.names[change[0]], change[1]))
            elif kind == "START":
                tr.started = created = True
            elif kind in ("STEP", "JUMP") and len(rest) >= 3:
                tr.path = (tr.path or [rest[1]]) + [rest[2]]
                tr.jumped = tr.jumped or kind == "JUMP"
            elif kind == "UNDO" and tr.path:
                tr.path.pop()
                if len(tr.path) == 1:
                    tr.path, tr.jumped = [], False
            elif kind == "TURN":
                if tr.path and tr.started and seat != -1 and (tr.seat < 0 or 1 - seat != tr.seat):
                    msgs.append("%s moves %s." % (tr.names[1 - seat], ("x" if tr.jumped else "-").join(tr.path)))
                tr.path, tr.jumped = [], False
                board = True
            elif kind == "DRAW" and seat >= 0:
                if seat != tr.seat:
                    msgs.append("%s offers you a draw. Type accept or decline (or draw to accept)." % tr.names[seat]
                                if tr.seat >= 0 else "%s offers a draw." % tr.names[seat])
            elif kind == "REJECT" and seat >= 0 and seat != tr.seat:
                msgs.append("%s declines the draw offer." % tr.names[seat])
            elif kind == "CHT" and len(rest) >= 2:
                if tr.seat < 0 or seat != tr.seat:
                    who = tr.names[seat] if 0 <= seat < 2 else "an observer"
                    msgs.append("%s says: %s" % (who, " ".join(rest[1:])))
            elif kind == "UNINVITE" and tr.seat >= 0 and not tr.started:
                who, why = uninvite_of(" ".join(p[3:]))
                if why in ("declined", "expired"):
                    msgs.append("%s declines the match offer." % who if why == "declined" else
                                "Your challenge to %s has expired." % who)
                    await self.core_req("LVE %s" % tr.tid)
                    self.untrack(tr.tid)
            elif kind == "END":
                ended = rest
                tr.over = True
        if quiet:
            return
        if created:
            no = self.hub.number(tr.tid)
            msgs.insert(0, "{Game %d (%s vs. %s) Creating unrated checkers match.}" % (no, tr.names[0], tr.names[1]))
            if tr.seat >= 0:
                msgs.insert(1, "You play %s; %s moves first." % (SIDES[tr.seat], tr.names[0]))
            # what the others play with, after the match's lines (the program prints its own start)
            at = 2 if tr.seat >= 0 else 1
            for i in (0, 1):
                if i != tr.seat and tr.says.get(i):
                    msgs.insert(at, "%s plays %s." % (tr.names[i], tr.says[i]))
                    at += 1
        if ended is not None:
            words, score = result_words(ended, tr.names)
            msgs.append("{Game %d (%s vs. %s) %s} %s" % (self.hub.number(tr.tid), tr.names[0], tr.names[1],
                                                          words, score))
        if msgs or ((board or created or ended is not None) and tr.started):
            lines = msgs + ((await self.board_lines(tr)) if (board or created or ended is not None) and tr.started
                            else [])
            self.notify(lines)
        if ended is not None:
            self.hub.numbers.pop(tr.tid, None)
            self.untrack(tr.tid)

    async def board_lines(self, tr, status=None):
        v = await self.view(tr, "T")
        if v is None or v["cells"] is None:
            return []
        no = self.hub.number(tr.tid)
        turn = v["turn"] if v["turn"] in (0, 1) else None
        if self.style == "5":
            seat = tr.seat if tr.seat in (0, 1) else 0
            clocks = [0, 0]
            if v["clock"] and v["clock"][0] in (0, 1):
                mover, left, limit = v["clock"]
                clocks = [limit, limit]
                clocks[mover] = left
            else:
                t = next((t for t in await self.tables() if t["id"] == tr.tid), None)
                clocks = [t["turn"], t["turn"]] if t and t["turn"] else [599, 599]
            mover = "BW"[turn] if turn is not None else "BW"[(tr.seat + 1) % 2 if tr.seat >= 0 else 0]
            return [style5(no, "BW"[seat], mover, tr.names[0], tr.names[1], v["cells"], clocks)]
        text = status or v["status"]
        if v["clock"] and v["clock"][0] in (0, 1) and turn is not None:
            text = "%s (%s) to move, %s left." % (tr.names[turn], SIDES[turn], chatcmd.clock_text(v["clock"][1]))
            if status:
                text = status + " " + text
        return style1(no, tr.names, v["cells"], tr.seat, text)

    async def send_board(self, tr, status=None):
        lines = await self.board_lines(tr, status)
        if self.style == "5" and status:
            lines = lines + [status]
        self.notify(lines or ["The game has not started yet."])

    async def poller(self):
        n = 0
        while not self.closing:
            await asyncio.sleep(POLL_SECS)
            n += 1
            try:
                for tr in list(self.tracks.values()):
                    await self.poll_track(tr)
                if n % INVITE_POLLS == 0:
                    await self.check_invites()
            except (CoreError, OSError, asyncio.TimeoutError):
                self.closing = True
                return

    async def run(self):
        task = inp = None
        try:
            try:
                async with asyncio.timeout(LOGIN_TOTAL_SECS):
                    await self.login()
            except (Refused, TimeoutError):
                return
            task = asyncio.ensure_future(self.poller())
            inp = asyncio.ensure_future(self.io.readline())
            while not self.closing:
                done, _ = await asyncio.wait({inp, task}, return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    break
                line = inp.result()
                log(self.sid, "< %s" % loggable(line, verb_of(line)))
                inp = asyncio.ensure_future(self.io.readline())
                self.wrote = False
                try:
                    await self.command(line)
                except CoreError as e:
                    self.reply("The game server says: %s" % no_wait(str(e)))
                if not self.wrote and not self.closing:
                    self.reply([])
            await self.io.w.drain()
        finally:
            for t in (inp, task):
                if t:
                    t.cancel()
            if self.name:
                self.hub.leave(self)
            if self.core is not None:
                await self.core.finish()        # the session ends with the connection


HELP = [
    "Commands (ICDS's, in FICS's manner):",
    "  match NAME [minutes] [black|white]   challenge NAME; match bot plays the bot",
    "  accept [NAME], decline [NAME]        answer a challenge or a draw offer",
    "  11-15, 15x22, 15-22-29               a move: the squares are numbered 1 to 32",
    "  draw, resign                         offer or accept a draw; resign the game",
    "  refresh, set style 1|5               the board again; text (1) or xcheckers' line (5)",
    "  games, observe N, unobserve          games in progress; watch one",
    "  who, finger NAME                     the players online",
    "  tell NAME text, say text, shout text talk",
    "  motd, help, quit",
    "The shared /commands work too: /list, /open, /join, /invite, /note, /help.",
    "Square 1 is in Black's back row; Black moves first, towards 29-32.",
]

# The protocol this extension serves, declared to the core (adapters/declare.py): key, name, description.
PROTOCOL = ("ICDS", "Internet Checkers and Draughts Server (ICDS)", "Checkers for xcheckers and other ICDS clients")


async def serve(args):
    hub = Hub()
    gate = Gate(MAX_SESSIONS)

    async def handle(reader, writer):
        ticket = gate.admit(peer_addr(writer), writer)
        if ticket is None:
            writer.write(b"Server is full, try again soon.\r\n")
            writer.close()
            return
        keepalive(writer)
        io = TelnetIO(reader, writer)
        s = Session(hub, io, (args.core_host, args.core_port), ticket)
        log(s.sid, "connect")
        try:
            await s.run()
        except (EOFError, ConnectionError, asyncio.TimeoutError, CoreError, OSError):
            pass
        except Exception as e:
            print("icds session error: %r" % e, file=sys.stderr, flush=True)
        finally:
            ticket.release()
            log(s.sid, "disconnect")
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("icds adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.icds")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6518)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    ap.add_argument("--poll", type=float, default=POLL_SECS, help=argparse.SUPPRESS)
    declare.add_args(ap)
    a = ap.parse_args(argv)
    if a.key:
        CoreClient.key_file = a.key
    globals()["POLL_SECS"] = a.poll
    a.core_host, _, p = a.core.rpartition(":")
    a.core_port = int(p)
    return a


if __name__ == "__main__":
    try:
        asyncio.run(serve(parse_args()))
    except KeyboardInterrupt:
        pass
