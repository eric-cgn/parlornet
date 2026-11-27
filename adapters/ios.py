"""IOS adapter: Reversi for clients of the Internet Othello Server (1993-1999).

Speaks the IOS text protocol the way its clients parse it on one side
(Reversi The Eclipse 1.2 and OLIOS in detail) and the native protocol on the other;
it is a client of parlord like the other adapters. IOS has no tables: players
challenge each other by name.

    match X   takes up X's invitation if X invited me; else joins X's open
              Reversi table if X waits alone at one (web, telnet or IOS);
              otherwise opens a table, sits down and invites X through the
              core (INV, the other seat held for X). X may be anyone with an
              account: an IOS player, a web or telnet player, or the bot.
              Eclipse's form (match [bronstein] t inc def X t inc def [b|w])
              and the terminal form (match X [t inc def] [white|black]) both
              work; the challenger's time in minutes becomes the time per turn.
    accept X  takes up X's invitation (JON); decline X declines it (UNV)
    PM D3 ... plays a move; passes are automatic here, so "ps" is ignored
    ob N      watches game N (again: stops); board N sends game N's board

Invitations from anyone (IOS, web, telnet, the native protocol) reach an IOS
player as an <ios> MATCH offer with FROM, which Eclipse shows as its Accept
dialog: each session looks at its invitations (IVL) every few seconds, and at
once when another IOS player challenges it. An invitation to another game
is one line of text. A declined or expired challenge is a line for the
challenger (the core's UNINVITE event and its reason).

Boards go out as one-line <ios> PB records (what Eclipse reads); "set board 1"
adds the human <ios> BOARD block for terminal users. One shared task polls the
core's table list (LST) and its list of players online (WHO) every few
seconds, so players at web and telnet tables, players connected elsewhere and
the bot appear in "who" and as <ios> [name(-) connected lines, and every game is
announced with <ios> [GAME n ...] created / ended. Game numbers are the table
ids while those are below 1000, otherwise the smallest free number.

Sign-in, sign-up and reset go through the core's steps (common.sign_in_lines),
in the client's window during the login phase, before the player joins the
lobby (Eclipse forwards what is typed in its window until the first <ios>
line): info lines are shown, each question is a prompt with the server's own
label (`Sign up as tia? (y/n): `, `Email address: `, `New password: `),
and the IOS password answers only the first question flagged as asking for
the credential (if none comes, the password is dropped and never sent). So
a new player can sign up from Eclipse: name, y, email address, then the
password from the mail; and a sign-in with a mailed password goes on to
`New password: ` and `Type it again: ` in the same window. A refused login
closes the connection. The session ends with the connection.

The connection to the core is opened only once the client has sent its login
line, and connections that have not signed in are counted and capped
(common.Gate: per adapter and per client address) with a short time to log
in, so idle connects cannot use up the core's extension channel.

Commands are logged as typed, except chat (tell, say: only the verb) and
lines that are not commands; the password and anything typed at a sign-in
prompt never are.

    python3 -m adapters.ios --core 127.0.0.1:6590 --key /data/frontends/ios.key --port 6503
"""
import argparse
import asyncio
import itertools
import random
import re
import sys

from . import botflag, declare
from .common import (framed_motd, LOGIN_SECS, LOGIN_TOTAL_SECS, CoreClient, CoreError, Gate, keepalive, err_text,
                     client_change_sentence, client_sentence, loggable, parse_client, parse_invites, parse_players, parse_reversi_view, parse_tables, peer_addr,
                     sign_in_lines, uninvite_of)
from .telnet import TelnetIO

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,7}\Z")
SQ_RE = re.compile(r"^[A-Ha-h][1-8]\Z")
NUM_RE = re.compile(r"^-?\d+\Z")
WIDTH = 79              # a text line, such as what a player plays with, fits a terminal's 80 columns
BOT_HOWTO = "To play the bot, type match bot, or pick bot in your program's list of players."

COMMANDS = {"quit": None, "exit": None, "bye": None, "logout": None, "help": None, "who": None, "match": None,
            "accept": None, "decline": None, "pm": None, "ps": None, "pa": None, "pass": None, "board": None,
            "refresh": None, "look": None, "ob": None, "observe": None, "fi": None, "finger": None,
            "resign": None, "tell": "tell", "t": "tell", "say": "say", "whisper": "say", "kibitz": "say",
            "set": None}


def verb_of(line):
    """The command a line is taken for (chat commands by their chat word), or None."""
    p = line.split()
    if not p:
        return None
    cmd = p[0].lower()
    if SQ_RE.match(cmd):
        return "pm"
    if cmd not in COMMANDS:
        return None
    return COMMANDS[cmd] or cmd
REVERSI = "reversi/1"
MAX_SESSIONS = 40
POLL_SECS = 1.0         # each session's games
INVITE_POLLS = 3        # each session's invitations: every third poll
HUB_SECS = 3.0          # the shared table-list poll (presence and games)
PASSWORD_WAIT = 3.0
PROMPT_WAIT = 120.0     # for a sign-in value typed in the client's window
MAX_GAME_NO = 999
MAX_TURN_MIN = 30 * 24 * 60
MAX_BUFFER = 256 * 1024     # a client that stops reading is dropped
WHO_HEADER = "login    blitz  stand.  G  M  idle  on for from host"
WHO_RULE = "-" * 52
CELL = {"0": "*", "1": "O"}

ids = itertools.count(1)


class Refused(Exception):
    pass


def no_wait(text):
    """A core error for players: without the machine-readable wait=N."""
    return re.sub(r"^wait=\d+ ", "", text)


def log(sid, text):
    print("ios[%d] %s" % (sid, text), flush=True)


def place(width, items):
    """Build a fixed-column line: items are (column, text)."""
    line = [" "] * width
    for col, text in items:
        for i, ch in enumerate(text):
            if col + i < width:
                line[col + i] = ch
    return "".join(line).rstrip()


def hms(secs):
    secs = max(0, min(int(secs), 99 * 3600 + 3599))
    return "%02d:%02d:%02d" % (secs // 3600, secs // 60 % 60, secs % 60)


def sq_code(sq):
    """'d3' -> 34: row * 10 + column (A = 1), the PB move field."""
    return int(sq[1:]) * 10 + ord(sq[0].lower()) - ord("a") + 1


def code_sq(code):
    return "%s%d" % ("ABCDEFGH"[code % 10 - 1], code // 10) if code > 0 else ""


def pb_line(no, black, white, rows, to_move, mine, move_no, last, used=(0, 0)):
    """<ios> PB: fixed fields read with sscanf, then the 64 cells from column 78
    (row 1 first, A to H; * black, O white, - empty). used = seconds used by
    black and white; Eclipse counts on from them for the side to move."""
    head = ("<ios> PB %3d %-8s %-8s %4d %4d %3d %3d %3d %3d %c %c %2d %2d %6.2f %4d"
            % (no, black[:8], white[:8], min(9999, used[0]), min(9999, used[1]), 0, 0, 0, 0,
               "*" if to_move == 0 else "O", "Y" if mine else "N", min(99, move_no), last, 0.0, 0))
    cells = "".join(CELL.get(c, "-") for row in rows[:8] for c in row[:8].ljust(8, "."))
    return head[:78].ljust(78) + cells.ljust(64, "-")


def board_block(no, black, white, rows, counts, to_move, my_turn, last, last_no, clocks):
    """<ios> BOARD block for people at a terminal: a header and 11 fixed-column
    lines. Columns: side to move at 12 (line 2); discs at 31, 33 .. 45 (rows);
    clocks on the second row at 11-18 and 59-66 with default/increment at
    20/24 and 68/72; last line: Y/N at 0, move number at 52, square at 57,
    value at 70."""
    out = ["<ios> BOARD of GAME%4d, (%s vs. %s)" % (no, black, white)]
    out.append(place(80, [(31, "A B C D E F G H")]))
    mover = "*" if to_move == 0 else "O"
    out.append(place(80, [(0, "<ios>"), (6, "Move:"), (12, mover),
                          (15, "%s to move" % (black if to_move == 0 else white))]))
    for y, row in enumerate(rows[:8]):
        items = [(28, str(y + 1))] + [(31 + 2 * x, CELL.get(c, "-")) for x, c in enumerate(row[:8])]
        if y == 1:
            items += [(11, clocks[0]), (20, "0"), (24, "0"), (59, clocks[1]), (68, "0"), (72, "0")]
        if y == 3:
            items += [(50, "* %-8s %2d" % (black, counts[0]))]
        if y == 4:
            items += [(50, "O %-8s %2d" % (white, counts[1]))]
        out.append(place(80, items))
    items = [(0, "Y" if my_turn else "N")]
    if last_no:
        items += [(52, "%2d" % last_no), (57, code_sq(last) or "P"), (70, "+0.00")]
    out.append(place(80, items))
    return out


def parse_match(args):
    """match arguments -> (opponent, minutes or None, 'black'/'white'/None),
    the color being the challenger's.
    Eclipse: [bronstein] oppTime oppInc oppDef opp myTime myInc myDef [b|w]
    terminal: opp [time inc def] [white|black]"""
    words = [a for a in args if a.lower() != "bronstein"]
    i = next((k for k, a in enumerate(words) if not NUM_RE.match(a)), None)
    if i is None:
        return None, None, None
    opp, before, rest = words[i], [int(a) for a in words[:i]], words[i + 1:]
    after = []
    while rest and NUM_RE.match(rest[0]):
        after.append(int(rest.pop(0)))
    colour = None
    if rest:
        colour = {"b": "black", "black": "black", "w": "white", "white": "white"}.get(rest[0].lower())
    times = after or before
    minutes = min(times[0], MAX_TURN_MIN) if times and times[0] > 0 else None
    return opp, minutes, colour


def connected(name):
    return "<ios> [%s(-) connected" % name[:8]


def disconnected(name):
    return "<ios> [%s disconnected" % name[:8]


class Hub:
    """Everyone connected through this adapter, what the core's table list
    and player list show, and the game numbers."""

    def __init__(self):
        self.sessions = {}      # lower name -> Session
        self.tables = []        # the last LST, parsed
        self.online = []        # the last WHO: players connected anywhere, and one bot (botflag.listed)
        self.bots = set()       # lower names of the bots WHO listed
        self.who_ok = False     # the core answers WHO: the who list is the players online, nobody else
        self.present = {}       # lower name -> name, as announced
        self.games = {}         # table id -> [black, white]: running games announced
        self.numbers = {}       # table id -> game number, running games
        self.old = {}           # table id -> game number, ended games (until the number is reused)
        self.ended = set()      # table ids announced as ended
        self.task = None
        self.turn = 0

    # ---- game numbers ---------------------------------------------------------
    def number(self, tid):
        if tid in self.numbers:
            return self.numbers[tid]
        if tid in self.old:
            return self.old[tid]
        used = set(self.numbers.values())
        n = int(tid) if tid.isdigit() and 1 <= int(tid) <= MAX_GAME_NO and int(tid) not in used else \
            next((k for k in range(1, MAX_GAME_NO + 1) if k not in used), MAX_GAME_NO)
        for k in [k for k, v in self.old.items() if v == n]:
            del self.old[k]
        self.numbers[tid] = n
        return n

    def tid_of(self, n):
        for d in (self.numbers, self.old):
            for tid, k in d.items():
                if k == n:
                    return tid
        return None

    # ---- who is here ----------------------------------------------------------
    def everyone(self):
        """lower name -> name: the players online (the core's WHO: active in the last presence.idle seconds,
        on any frontend, and the bot), IOS players as typed at login. With a core that has no WHO: IOS
        sessions and everyone at a table."""
        if self.who_ok:
            out = {n.lower(): n[:8] for n in self.online}
            for k, s in self.sessions.items():
                if k in out:
                    out[k] = s.name
            return out
        out = {}
        for t in self.tables:
            for n in t["names"]:
                out[n.lower()] = n[:8]
        for k, s in self.sessions.items():
            out[k] = s.name
        return out

    def full_name(self, name):
        """A name as typed or picked from the who list (cut to 8) -> the player's whole name, as far as
        this adapter knows it; else the name itself."""
        k = name.lower()
        names = list(self.online) + [n for t in self.tables for n in t["names"]]
        return next((n for n in names if n.lower() == k), None) or \
            next((n for n in names if len(n) > 8 and n[:8].lower() == k), name)

    def post_all(self, lines, skip=None):
        for s in list(self.sessions.values()):
            if s is not skip:
                s.post(lines)

    def sync_presence(self, skip=None, silent=False):
        now = self.everyone()
        new = [n for k, n in now.items() if k not in self.present]
        gone = [n for k, n in self.present.items() if k not in now]
        self.present = now
        if not silent and (new or gone):
            self.post_all([connected(n) for n in new] + [disconnected(n) for n in gone], skip)

    # ---- games ----------------------------------------------------------------
    def game_line(self, tid, word):
        b, w = self.games.get(tid) or ["?", "?"]
        return "<ios> [GAME %d (%s vs. %s) (unrated) %s" % (self.number(tid), b[:8], w[:8], word)

    def game_created(self, tid, names, also=None):
        """Announce a running game to every IOS session, once. Synchronous, so a
        caller's PB line always follows the announcement."""
        if tid in self.games or tid in self.ended:
            if also and tid in self.games:
                also.post([self.game_line(tid, "created")], prompt=False)
            return
        self.games[tid] = [self.own_case(n) for n in names[:2]]
        self.post_all([self.game_line(tid, "created")])

    def game_ended(self, tid):
        if tid not in self.games:
            return
        line = self.game_line(tid, "ended")
        del self.games[tid]
        self.ended.add(tid)
        if tid in self.numbers:
            self.old[tid] = self.numbers.pop(tid)
        self.post_all([line])

    def own_case(self, name):
        """An IOS player's name exactly as typed at login (Eclipse compares case)."""
        s = self.sessions.get(name.lower())
        return s.name if s else name

    # ---- the shared poll ----------------------------------------------------
    async def poll(self, via, silent=False):
        """Read the core's table list through one session's core connection and
        announce what changed: players, games created, games ended."""
        before = set(self.games)
        data, final = await via.core_req("LST all")
        if not final.startswith("OK"):
            return
        self.tables = parse_tables(data)
        await self.who(via)
        running = {t["id"]: t["seated"][:2] for t in self.tables
                   if t["key"] == REVERSI and t["status"] == "playing"}
        for tid, names in running.items():
            if tid in self.games or tid in self.ended:
                continue
            if silent:
                self.games[tid] = [self.own_case(n) for n in names]
            else:
                self.game_created(tid, names)
        for tid in [t for t in before if t in self.games and t not in running]:
            if silent:
                del self.games[tid]
                self.ended.add(tid)
            else:
                self.game_ended(tid)
        for tid in [t for t in self.numbers if t not in self.games]:
            self.old[tid] = self.numbers.pop(tid)
        self.sync_presence(silent=silent)

    async def who(self, via):
        """The core's players online (WHO): who lists show only them. A core without WHO: None, and the
        lists show the players at tables and those connected here, as before."""
        data, final = await via.core_req("WHO")
        if final.startswith("OK"):
            players = parse_players(data)
            self.online = botflag.listed(players)
            self.bots = {n.lower() for n, bot in players if bot}
            self.who_ok = True

    async def run(self):
        while self.sessions:
            await asyncio.sleep(HUB_SECS)
            ss = list(self.sessions.values())
            if not ss:
                break
            via = ss[self.turn % len(ss)]   # round robin: keeps every core connection alive
            self.turn += 1
            try:
                await self.poll(via)
            except (CoreError, OSError, asyncio.TimeoutError):
                via.closing = True
            except Exception as e:      # the shared poll must keep running
                print("ios hub error: %r" % e, file=sys.stderr, flush=True)
        self.task = None

    async def enter(self, s):
        """A login succeeded: its first line starts with <ios> (that ends
        Eclipse's login phase), then everyone else hears of it."""
        s.post([connected(s.name)], prompt=False)
        try:
            if not self.sessions:
                self.present = {}
                await self.poll(s, silent=True)
            else:
                await self.who(s)           # the newcomer is online now: the others hear of it at once
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        self.sessions[s.name.lower()] = s
        self.sync_presence(skip=s)
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self.run())

    def leave(self, s):
        k = s.name.lower()
        if self.sessions.get(k) is s:
            del self.sessions[k]
        self.sync_presence()


class Track:
    """A game this session plays (seat 0/1) or watches (seat -1)."""

    def __init__(self, tid, seat):
        self.tid, self.seat = tid, seat
        self.cursor = 0
        self.names = ["?", "?"]
        self.last = -1          # last move as row * 10 + column; -1 none or a pass
        self.started = False
        self.over = False
        self.clients = {}       # seat -> what its player plays with, in words (CLIENT events)


class Session:
    def __init__(self, hub, io, core_addr, ticket=None):
        self.hub, self.io, self.core_addr = hub, io, core_addr
        self.ticket = ticket
        self.addr = peer_addr(io.w)
        self.sid = next(ids)
        self.name = None
        self.core = None
        self.core_lock = asyncio.Lock()
        self.drain_lock = asyncio.Lock()
        self.ev_lock = asyncio.Lock()
        self.table = None       # the table I sit at (open or running)
        self.tracks = {}        # table id -> Track: my table and the games I watch
        self.human = False      # set board 1: the human BOARD block as well
        self.shown = {}         # table id -> who invited me: the invitations shown (MATCH offers)
        self.inv_lock = asyncio.Lock()
        self.closing = False

    # ---- output -------------------------------------------------------------
    def post(self, lines, prompt=True):
        """Write lines at once (one write: never split by other output).
        CR LF ends; the prompt goes last and never inside a reply."""
        w = self.io.w
        if w.is_closing():
            return
        text = "".join(l + "\r\n" for l in lines)
        if prompt and self.name:
            text += "%s%% " % self.name
        if text:
            w.write(text.encode("ascii", "replace"))
        if w.transport.get_write_buffer_size() > MAX_BUFFER:
            self.closing = True
            w.close()

    async def send(self, lines, prompt=True):
        self.post(lines, prompt)
        async with self.drain_lock:
            await self.io.w.drain()

    async def core_req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    # ---- login --------------------------------------------------------------
    async def login(self):
        self.io.w.write(b"\r\nParlorNet IOS service: Reversi for IOS clients.\r\n"
                        b"Use a name of up to 8 letters and digits, and your ParlorNet password.\r\n"
                        b"A new name signs up here.\r\n\r\nlogin: ")
        await self.io.w.drain()
        name = ""
        while not name:     # Mac clients end lines with LF CR: skip the empty lines
            try:
                name = (await asyncio.wait_for(self.io.readline(), LOGIN_SECS)).strip()
                self.touch()
            except asyncio.TimeoutError:
                raise Refused("no login")
        log(self.sid, "login %r" % name)
        if not NAME_RE.match(name):
            await self.refuse("Names are 1 to 8 letters, digits, - or _, starting with a letter or digit.")
        if name.lower() in self.hub.sessions:
            await self.refuse("%s is logged in already." % name)
        self.io.w.write(b"password: ")
        await self.io.w.drain()
        password = await self.read_password()
        notes = []
        try:                # only now, with a login line from the client, a connection to the core
            self.core = await CoreClient.connect(*self.core_addr)
            await self.core.source(self.addr)
        except (OSError, asyncio.TimeoutError, CoreError):
            await self.refuse("The game server isn't answering. Try again soon.")
        async with self.core_lock:
            refusal = await sign_in_lines(self.core, name, password, self.prompt, self.show)
        if refusal:
            await self.refuse(refusal)
        if name.lower() in self.hub.sessions:
            await self.refuse("%s is logged in already." % name)
        if self.ticket is not None and not self.ticket.sign_in():
            await self.refuse("Server is full, try again soon.")
        self.name = name
        self.io.on_input = self.core.user_input     # every command typed from now on is activity
        await self.hub.enter(self)
        self.post(notes + ["Welcome, %s. Type help for the commands; games here are unrated." % name,
                           "Passes are made for you. Type set board 1 to get boards as text.",
                           BOT_HOWTO], prompt=False)
        await self.rejoin()
        await self.check_invites()
        await self.send([])

    def touch(self):
        if self.ticket is not None:
            self.ticket.touch()

    async def read_password(self):
        """The password line, or "" after two empty lines (LF CR from a Mac
        client with no password) or a short wait."""
        loop = asyncio.get_running_loop()
        end = loop.time() + PASSWORD_WAIT
        empty = 0
        while True:
            left = end - loop.time()
            if left <= 0:
                break
            try:
                line = (await asyncio.wait_for(self.io.readline(), left)).strip()
            except asyncio.TimeoutError:
                break
            if line:
                log(self.sid, "password line received (not logged)")
                return line
            empty += 1
            if empty >= 2:
                break
        log(self.sid, "no password")
        return ""

    async def show(self, lines):
        """Sign-in info lines from the core, in the client's window."""
        self.io.w.write("".join("\r\n" + l for l in lines).encode("ascii", "replace"))
        await self.io.w.drain()

    async def prompt(self, label, secret=False):
        """Ask for a sign-in value with the server's own label, like password:
        (no newline), and wait for the player to type it in the client's
        window. Empty lines (LF CR from Mac clients) are skipped. Not logged.
        Eclipse answers every "password:" it sees with the password it keeps, so a
        label that ends in "password" (New password, Choose a password) is written without
        that colon: the player types the answer."""
        text = "%s: " % label
        if "password:" in text:
            text = "%s (8 or more characters): " % label
        self.io.w.write(("\r\n" + text).encode("ascii", "replace"))
        await self.io.w.drain()
        loop = asyncio.get_running_loop()
        end = loop.time() + PROMPT_WAIT
        while loop.time() < end:
            try:
                line = (await asyncio.wait_for(self.io.readline(), end - loop.time())).strip()
            except asyncio.TimeoutError:
                break
            if line:
                self.touch()
                log(self.sid, "sign-in answer received (not logged)")
                return line
        return ""

    async def refuse(self, text):
        """A refused login ends the connection: asking login: again would make
        Eclipse send the same name forever."""
        log(self.sid, "refused: %s" % text)
        self.io.w.write(("\r\n%s\r\n" % text).encode("ascii", "replace"))
        await self.io.w.drain()
        raise Refused(text)

    # ---- commands -----------------------------------------------------------
    async def command(self, line):
        p = line.split()
        if not p:
            return          # empty lines (the CR of LF CR from Mac clients) are ignored
        cmd = p[0].lower()
        if cmd in ("quit", "exit", "bye", "logout"):
            self.closing = True
            return
        if cmd == "help":
            await self.send(["Commands are who, match <name> [minutes] [black|white], accept <name>,",
                             "decline <name>, PM <square> (or just d3), board [game], ob <game>,",
                             "fi <name>, tell <name> <text>, say <text>, resign, set board 1 (boards",
                             "as text), quit. Squares are A1-H8, row 1 at the top. Minutes are the",
                             "time per turn."])
        elif cmd == "who":
            await self.who()
        elif cmd == "match":
            await self.match(p[1:])
        elif cmd == "accept":
            await self.accept(p[1:])
        elif cmd == "decline":
            await self.decline(p[1:])
        elif cmd == "pm" or SQ_RE.match(cmd) or cmd in ("ps", "pa", "pass"):
            await self.move(p[1] if cmd == "pm" and len(p) > 1 else cmd)
        elif cmd in ("board", "refresh", "look"):
            if len(p) > 1:
                await self.board_of(p[1])
            else:
                await self.text_board()
        elif cmd in ("ob", "observe"):
            await self.observe(p[1:])
        elif cmd in ("fi", "finger"):
            await self.finger(p[1:])
        elif cmd == "resign":
            await self.resign()
        elif cmd in ("tell", "t") and len(p) >= 3:
            await self.tell(p[1], " ".join(p[2:]))
        elif cmd in ("say", "whisper", "kibitz") and len(p) >= 2:
            await self.say(" ".join(p[1:]))
        elif cmd == "set":
            if len(p) >= 3 and p[1].lower() == "board":
                self.human = p[2].lower() not in ("0", "off", "no", "false")
                await self.send(["Boards as text are %s." % ("on" if self.human else "off")])
            else:
                await self.send([])     # accepted and ignored (set trust 1 and the like)
        else:
            await self.send(["Unknown command. Type help."])     # never repeat it: it may be a password

    async def rejoin(self):
        """Pick up a Reversi game this player is already seated in."""
        data, _ = await self.core_req("LST")
        me = self.name.lower()
        mine = [t for t in parse_tables(data) if t["key"] == REVERSI and t["status"] in ("playing", "open")
                and me in [n.lower() for n in t["names"]]]
        if not mine:
            return
        t = sorted(mine, key=lambda t: (t["status"] != "playing", int(t["id"])))[0]
        seat = [n.lower() for n in t["seated"]].index(me)
        tr = Track(t["id"], seat)
        tr.names = [self.own(n) for n in t["seated"][:2]]
        await self.catch_up(tr)
        self.tracks[tr.tid] = tr
        self.table = tr.tid
        if t["status"] == "playing":
            tr.started = True
            self.hub.game_created(tr.tid, tr.names, also=self)
            self.post(["Welcome back: your game %d continues." % self.hub.number(tr.tid)], prompt=False)
            await self.send_pb(tr, prompt=False)

    async def who(self):
        hub = self.hub
        await hub.poll(self)        # a fresh table list
        playing, waiting = {}, set()
        for t in hub.tables:
            if t["key"] != REVERSI:
                continue
            for n in t["names"]:
                if t["status"] == "playing":
                    playing[n.lower()] = hub.number(t["id"])
                elif t["status"] == "open":
                    waiting.add(n.lower())
        people = hub.everyone()
        # Eclipse: header, one line skipped, rows (name at 0, game number at
        # 23-25, a space in every row) until "<count> players online".
        lines = [WHO_HEADER, WHO_RULE]
        for k, n in sorted(people.items(), key=lambda e: e[1].lower()):
            game = "%3d" % playing[k] if k in playing else "---"
            flag = "o" if k in waiting and k not in playing else "-"
            lines.append("%-8s [   0] (   0) %s %s" % (n[:8], game, flag))
        lines.append("%d players online" % len(people))
        await self.send(lines)

    async def free_seat(self):
        """Before a new game: leave my open table; False if I am playing one."""
        if not self.table:
            return True
        tr = self.tracks.get(self.table)
        if tr:
            await self.poll_track(tr)
        if self.table and tr and tr.started:
            await self.send(["You are playing game %d. Finish or resign it first." % self.hub.number(tr.tid)])
            return False
        if self.table:
            await self.leave_open()
        return True

    async def leave_open(self):
        tid = self.table
        await self.core_req("LVE %s" % tid)         # the invitations I made there end with it
        self.untrack(tid)

    async def invitation_from(self, name):
        """My invitation from name to a Reversi table (IVL), or None."""
        data, final = await self.core_req("IVL")
        if not final.startswith("OK"):
            return None
        k = name.lower()
        return next((i for i in parse_invites(data) if i["key"] == REVERSI and
                     (i["from"].lower() == k or (len(i["from"]) > 8 and i["from"][:8].lower() == k))), None)

    async def match(self, args):
        """A challenge: X's invitation taken up, X's open table joined, or a new table with X invited."""
        opp, minutes, colour = parse_match(args)
        if not opp:
            await self.send(["Usage: match <name> [minutes] [black|white]"])
            return
        if opp.lower() == self.name.lower():
            await self.send(["You can't play yourself."])
            return
        inv = await self.invitation_from(opp)
        if inv:                             # X challenged me first: this takes it up
            if await self.free_seat():
                self.shown.pop(inv["id"], None)
                await self.join(inv["id"], colour)
            return
        if not await self.free_seat():
            return
        data, _ = await self.core_req("LST open game=%s" % REVERSI)
        tables = parse_tables(data)
        open_tab = next((t for t in tables if t["key"] == REVERSI and t["status"] == "open" and t["names"]
                         and t["vis"] == "public" and all(n[:8].lower() == opp.lower() for n in t["names"])
                         and not botflag.bot_waits(t)), None)      # a match of the bot makes its own table
        if open_tab:
            await self.join(open_tab["id"], colour)
            return
        opp = self.hub.full_name(opp)
        _, final = await self.core_req("NEW %s standard%s" % (REVERSI, " turn=%dm" % minutes if minutes else ""))
        if not final.startswith("OK"):
            await self.send(["Can't start a game: %s" % err_text(final)])
            return
        tid = final.split()[1]
        seat = {"black": 0, "white": 1}.get(colour, random.randint(0, 1))
        _, final = await self.core_req("JON %s %d" % (tid, seat))
        if not final.startswith("OK"):
            await self.send(["Can't sit down: %s" % err_text(final)])
            return
        self.track(tid, seat)
        _, final = await self.core_req("INV %s %s %d" % (tid, opp, 1 - seat))      # the other seat held for X
        if not final.startswith("OK"):
            await self.leave_open()
            await self.send(["There is no player called %s here." % opp if final.startswith("ERR NOPLAYER")
                             else "Can't challenge %s: %s" % (opp, err_text(final))])
            return
        offline = final.split()[1:] == ["offline"]      # not online now (WHO): the invitation waits for them
        target = self.hub.sessions.get(opp.lower())
        if target:                          # an IOS player: the offer now, not at their next look
            asyncio.ensure_future(target.check_invites())
        if not offline and (opp.lower() in self.hub.bots or botflag.is_bot_name(opp)):
            await self.send(["Your challenge to %s is pending. The bot will sit down in a few seconds." % opp])
        elif not offline:
            await self.send(["Your challenge to %s is pending (table %s)." % (opp, tid)])
        else:
            await self.send(["Your challenge to %s is pending at table %s." % (opp, tid),
                             "%s is not online now. The invitation will wait for an hour." % opp])

    async def check_invites(self):
        """My invitations (IVL): each new one shown once, a Reversi one as an <ios> MATCH offer (Eclipse's
        Accept dialog), another game's as a line of text; one that went away unanswered, a line."""
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
                out += await self.offer_lines(i)
            if out:
                await self.send(out)

    async def offer_lines(self, i):
        """An invitation -> the lines that show it: for Reversi the <ios> MATCH pair (Eclipse reads the
        numbers after the first ")", shows its Accept dialog for FROM, and reads the next line at once, the
        rating change from column 15; the times are the time per turn in minutes, the color the
        challenger's), then words for people at a terminal."""
        if i["key"] != REVERSI:
            game = i["key"].split("/")[0].capitalize()
            return ["%s invites you to %s at table %s. You can play it on the web or by telnet." %
                    (i["from"], game, i["id"])]
        t = next((t for t in self.hub.tables if t["id"] == i["id"]), None)
        if t is None:
            data, _ = await self.core_req("LST table=%s" % i["id"])
            t = next((t for t in parse_tables(data) if t["id"] == i["id"]), None)
        turn = (t["turn"] if t and t["turn"] else 86400) // 60
        seated = [n.lower() for n in t["seated"]] if t else []
        if i["from"].lower() in seated:
            theirs = "black" if seated.index(i["from"].lower()) == 0 else "white"
        else:
            theirs = "white" if i["seat"] == 0 else "black"
        frm = i["from"]
        return ["<ios> MATCH (u) %d 0 0 FROM %s %d 0 0 %s" % (turn, frm, turn, theirs),
                " " * 15 + "0 .. 0 0 0 .. 0",
                "%s challenges you to unrated Reversi, %d min a turn, %s playing %s."
                % (frm, turn, frm, theirs),
                "Type accept %s or decline %s." % (frm, frm)]

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

    async def join(self, tid, colour=None):
        seat = {"black": " 0", "white": " 1"}.get(colour, "")
        _, final = await self.core_req("JON %s%s" % (tid, seat))
        if not final.startswith("OK") and seat:
            _, final = await self.core_req("JON %s" % tid)
        if not final.startswith("OK"):
            await self.send(["Can't join: %s" % err_text(final)])
            return
        await self.poll_track(self.track(tid, int(final.split()[1])))

    async def accept(self, args):
        """accept X: X's invitation taken up (JON: the seat held for me)."""
        if not args:
            await self.send(["Usage: accept <name>"])
            return
        inv = await self.invitation_from(args[0])
        if inv is None:
            await self.send(["There is no offer from %s." % args[0]])
            return
        if await self.free_seat():
            self.shown.pop(inv["id"], None)
            await self.join(inv["id"])

    async def decline(self, args):
        """decline X: X's invitation declined (UNV); X hears of it from the core (UNINVITE ... declined)."""
        if not args:
            await self.send(["Usage: decline <name>"])
            return
        inv = await self.invitation_from(args[0])
        if inv is None:
            await self.send(["There is no offer from %s." % args[0]])
            return
        self.shown.pop(inv["id"], None)
        await self.core_req("UNV %s %s" % (inv["id"], self.name))
        await self.send(["You decline the match with %s." % inv["from"]])

    async def move(self, token):
        tr = self.tracks.get(self.table) if self.table else None
        if not tr:
            await self.send(["You are not playing a game."])
            return
        if token.lower() in ("ps", "pa", "pass"):
            await self.send([])     # passes are made by the server
            return
        if not SQ_RE.match(token):
            await self.send(["Illegal move: %s" % token])
            return
        _, final = await self.core_req("MOV %s %s" % (tr.tid, token.lower()))
        if not final.startswith("OK"):
            await self.send(["Illegal move: %s (%s)" % (token.upper(), err_text(final))])
            return
        await self.poll_track(tr)

    async def resign(self):
        tr = self.tracks.get(self.table) if self.table else None
        if not tr:
            await self.send([])     # Eclipse sends resign when a finished game's window closes
            return
        _, final = await self.core_req("RES %s" % tr.tid)
        await self.poll_track(tr)
        if not final.startswith("OK") and self.table == tr.tid:
            if tr.started:
                await self.send(["Can't resign game %d: %s" % (self.hub.number(tr.tid), err_text(final))])
            else:
                await self.leave_open()
                await self.send(["You leave your open table %s." % tr.tid])

    async def tell(self, who, text):
        target = self.hub.sessions.get(who.lower())
        tr = self.tracks.get(self.table) if self.table else None
        if target:
            # "<name>: <text>" goes to Eclipse's Talk window for that name
            target.post(["%s: %s" % (self.name[:8], text)])
            await self.send(["(told %s)" % who])
        elif tr and who.lower() in (n[:8].lower() for n in tr.names):
            await self.say(text)
        else:
            await self.send(["%s is not on IOS here and not at your table." % who])

    async def say(self, text):
        if not self.table:
            await self.send(["You are not playing a game."])
            return
        await self.core_req("CHT %s %s" % (self.table, text[:56]))
        await self.send(["(told your table)"])

    def game_no(self, word):
        n = word.lstrip("#")
        return self.hub.tid_of(int(n)) if n.isdigit() else None

    async def observe(self, args):
        """ob <n>: watch game n (its PB lines come with N); again: stop."""
        if not args:
            await self.send(["Usage: ob <game>"])
            return
        tid = self.game_no(args[0])
        if tid is None:
            await self.send(["There is no game %s." % args[0]])
            return
        no = self.hub.number(tid)
        tr = self.tracks.get(tid)
        if tr and tr.seat >= 0:
            await self.send_pb(tr)
            return
        if tr:
            self.untrack(tid)
            await self.core_req("LVE %s" % tid)
            await self.send(["You stop observing game %d." % no])
            return
        if tid not in self.hub.numbers:
            await self.send(["Game %d is over." % no])
            return
        tr = await self.watch(tid)
        if tr is None:
            return
        self.tracks[tid] = tr
        self.post(["You are observing game %d." % no], prompt=False)
        await self.send_pb(tr)

    async def watch(self, tid):
        _, final = await self.core_req("SEE %s" % tid)
        if not final.startswith("OK"):
            await self.send(["Can't observe game %d: %s" % (self.hub.number(tid), err_text(final))])
            return None
        tr = Track(tid, -1)
        tr.names = list(self.hub.games.get(tid) or tr.names)
        await self.catch_up(tr)
        return tr

    async def board_of(self, word):
        """board <n>: one PB line for game n."""
        tid = self.game_no(word)
        if tid is None:
            await self.send(["There is no game %s." % word])
            return
        tr = self.tracks.get(tid)
        if tr:
            await self.send_pb(tr)
            return
        tr = await self.watch(tid)
        if tr:
            await self.send_pb(tr)
            await self.core_req("LVE %s" % tid)

    async def text_board(self):
        tr = self.tracks.get(self.table) if self.table else None
        if not tr:
            await self.send(["You are not playing a game."])
            return
        v = await self.view(tr)
        if not v or not v["rows"]:
            await self.send(["The game has not started yet."])
            return
        await self.send(self.human_block(tr, v))

    async def finger(self, args):
        if not args:
            await self.send(["Usage: fi <name>"])
            return
        name = args[0]
        if set(name) == {"-"}:
            return          # Eclipse's placeholder row
        k = name.lower()
        hub = self.hub
        disp = next((n for key, n in hub.everyone().items() if key == k or n.lower() == k), None)
        if disp is None:
            await self.send(["%s is not online here." % name])
            return
        if k in hub.bots:
            await self.send(["%s is the server's bot. Type match %s to play against it." % (disp, disp)])
            return
        lines = ["%s is online %s." % (disp, "here on IOS" if k in hub.sessions else "on the web or telnet")]
        for t in hub.tables:
            if t["key"] != REVERSI or k not in [n[:8].lower() for n in t["names"]]:
                continue
            if t["status"] == "playing":
                lines.append("%s plays game %d (%s vs. %s)." % (disp, hub.number(t["id"]), t["seated"][0][:8],
                                                               t["seated"][1][:8]))
            elif t["status"] == "open":
                lines.append("%s waits for an opponent at table %s. Type match %s to play." % (disp, t["id"], disp))
        await self.send(lines)

    # ---- game events --------------------------------------------------------
    async def poll_track(self, tr):
        async with self.ev_lock:
            if self.tracks.get(tr.tid) is not tr:
                return
            data, final = await self.core_req("SIN %s %d" % (tr.tid, tr.cursor))
            if final.startswith("ERR"):
                self.untrack(tr.tid)
                return
            await self.handle_events(tr, data)

    async def catch_up(self, tr):
        """Read a table's past events without output: names, last move, cursor."""
        data, final = await self.core_req("SIN %s 0" % tr.tid)
        for l in data:
            p = l.split()
            if len(p) < 4 or p[0] != "EVT" or p[1] != tr.tid:
                continue
            tr.cursor = max(tr.cursor, int(p[2]))
            if p[3] == "JOIN" and len(p) >= 6 and p[4].isdigit() and int(p[4]) < 2:
                tr.names[int(p[4])] = self.own(p[5])
            elif p[3] == "START":
                tr.started = True
            elif p[3] == "CLIENT":
                c = parse_client(l.split(" ", 3)[3])
                if c:
                    tr.clients[c[0]] = c[1]
            elif p[3] == "PUT" and len(p) >= 6:
                tr.last = sq_code(p[5])
            elif p[3] == "PASS":
                tr.last = -1
            elif p[3] == "END":
                tr.over = True
        if final.startswith("OK") and len(final.split()) > 1:
            tr.cursor = max(tr.cursor, int(final.split()[1]))

    async def handle_events(self, tr, lines):
        board = created = False
        ended = None
        msgs = []
        for l in lines:
            p = l.split()
            if len(p) < 4 or p[0] != "EVT" or p[1] != tr.tid or int(p[2]) <= tr.cursor:
                continue
            tr.cursor = int(p[2])
            kind = p[3]
            if kind == "JOIN" and len(p) >= 6:
                seat = int(p[4])
                if seat < 2:
                    tr.names[seat] = self.own(p[5])
            elif kind == "START":
                tr.started = created = True
            elif kind == "CLIENT":
                c = parse_client(l.split(" ", 3)[3])
                if c and c[0] < 2:
                    known = tr.clients.get(c[0])
                    tr.clients[c[0]] = c[1]
                    if tr.started and c[0] != tr.seat:       # playing on from another program
                        msgs.append(client_change_sentence(tr.names[c[0]], c[1], WIDTH) if known else
                                    client_sentence(tr.names[c[0]], c[1], "plays", WIDTH))
            elif kind == "PUT" and len(p) >= 6:
                tr.last = sq_code(p[5])
                board = True
            elif kind == "PASS":
                tr.last = -1
                msgs.append("%s has no move and passes." % tr.names[int(p[4])])
                board = True
            elif kind == "CHT" and len(p) >= 6:
                who = tr.names[int(p[4])] if p[4].isdigit() and int(p[4]) < 2 else "watcher"
                if tr.seat < 0 or p[4] != str(tr.seat):
                    msgs.append("%s: %s" % (who[:8], " ".join(p[5:])))     # Eclipse's Talk window
            elif kind == "UNINVITE" and tr.seat >= 0 and not tr.started:
                who, why = uninvite_of(" ".join(p[3:]))
                if why == "declined":
                    msgs.append("%s declines the match. Your table %s stays open for anyone." % (who, tr.tid))
                elif why == "expired":
                    msgs.append("Your challenge to %s has expired. Your table %s stays open for anyone."
                                % (who, tr.tid))
            elif kind == "END":
                ended = p[4:]
                tr.over = True
        if created:
            await self.refresh_names(tr)
            self.hub.game_created(tr.tid, tr.names)     # before the first PB
            opp = 1 - tr.seat if tr.seat in (0, 1) else None
            if opp is not None and tr.clients.get(opp):     # a line of its own after [GAME n ...] created
                msgs.append(client_sentence(tr.names[opp], tr.clients[opp], "plays", WIDTH))
        if msgs:
            self.post(msgs, prompt=False)
        if (board or created) and tr.started:
            await self.send_pb(tr, prompt=False)
        if ended is not None:
            await self.finish(tr, ended)
        if created or msgs or board or ended is not None:
            await self.send([])

    def own(self, name):
        """Names as Eclipse expects them: mine exactly as typed, others cut to 8."""
        return self.name if name.lower() == self.name.lower() else self.hub.own_case(name)[:8]

    async def refresh_names(self, tr):
        data, _ = await self.core_req("LST table=%s" % tr.tid)
        tab = next((t for t in parse_tables(data) if t["id"] == tr.tid), None)
        if tab:
            tr.names = [self.own(n) for n in tab["seated"][:2]]

    async def view(self, tr):
        data, final = await self.core_req("VUE %s T" % tr.tid)
        return parse_reversi_view(data) if final.startswith("OK") else None

    def board_facts(self, tr, v):
        turn = v["turn"] if v["turn"] in (0, 1) else 0
        mine = tr.seat >= 0 and v["turn"] == tr.seat and not tr.over
        used, left = [0, 0], [None, None]
        if v["clock"] and v["clock"][0] in (0, 1):
            seat, secs_left, limit = v["clock"]
            used[seat] = max(0, limit - secs_left)      # this turn so far; the window counts on
            left[seat] = secs_left
        return turn, mine, used, left

    def pb(self, tr, v):
        turn, mine, used, _ = self.board_facts(tr, v)
        return pb_line(self.hub.number(tr.tid), tr.names[0], tr.names[1], v["rows"], turn, mine,
                       sum(v["counts"]) - 4, tr.last, used)

    def human_block(self, tr, v):
        turn, mine, _, left = self.board_facts(tr, v)
        clocks = [hms(s) if s is not None else "00:00:00" for s in left]
        return board_block(self.hub.number(tr.tid), tr.names[0], tr.names[1], v["rows"], v["counts"], turn,
                           mine, tr.last, sum(v["counts"]) - 4, clocks)

    async def send_pb(self, tr, prompt=True):
        v = await self.view(tr)
        if not v or not v["rows"]:
            await self.send(["The game has not started yet."], prompt=prompt)
            return
        lines = [self.pb(tr, v)]
        if self.human:
            lines += self.human_block(tr, v)
        await self.send(lines, prompt=prompt)

    async def finish(self, tr, result):
        v = await self.view(tr)
        counts = v["counts"] if v else (0, 0)
        b, w = tr.names
        if result and result[0] == "win":
            text = "%s wins %d-%d." % (tr.names[int(result[1])], counts[0], counts[1])
        elif result and result[0] == "draw":
            text = "Draw %d-%d." % counts
        elif result and result[0] == "resign":
            text = "%s resigns." % tr.names[int(result[1])]
        elif result and result[0] == "timeout" and len(result) > 1:
            text = "%s ran out of time." % tr.names[int(result[1])]
        else:
            text = "Game over (%s)." % " ".join(result)
        no = self.hub.number(tr.tid)
        self.hub.game_ended(tr.tid)
        self.post(["Game %d is over. %s %d, %s %d. %s" % (no, b, counts[0], w, counts[1], text)], prompt=False)
        self.untrack(tr.tid)

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
                await self.command(line)
        finally:
            for t in (inp, task):
                if t:
                    t.cancel()
            if self.name:
                self.hub.leave(self)
            if self.core is not None:
                await self.core.finish()        # the session ends with the connection


# The protocol this extension serves, declared to the core (adapters/declare.py): key, name, description.
PROTOCOL = ("IOS", "Internet Othello Server (IOS)", "Reversi for clients of the Internet Othello Server")


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
            print("ios session error: %r" % e, file=sys.stderr, flush=True)
        finally:
            ticket.release()
            log(s.sid, "disconnect")
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("ios adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.ios")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6503)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    declare.add_args(ap)
    ap.add_argument("--prompt-wait", type=float, default=PROMPT_WAIT, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.key:
        CoreClient.key_file = a.key
    globals()["PROMPT_WAIT"] = a.prompt_wait
    a.core_host, _, p = a.core.rpartition(":")
    a.core_port = int(p)
    return a


if __name__ == "__main__":
    try:
        asyncio.run(serve(parse_args()))
    except KeyboardInterrupt:
        pass
