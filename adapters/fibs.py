"""FIBS adapter: ParlorNet's Backgammon for vintage clients of the FIBS protocol.

ParlorNet is not FIBS (the First Internet Backgammon Server, still running at
fibs.com port 4321) and is not affiliated with it. This service speaks FIBS's
published client protocol only so that the old FIBS clients can play
ParlorNet's tables; it identifies itself as ParlorNet in its banner, help and
about texts, and never connects to fibs.com, relays to it or imports its
accounts, ratings or texts.

Speaks the FIBS text protocol the way period clients parse it on one side
(MacFIBS 2.0.3 and 3, FIBS/W 1.30, xfibs 0.8 and xibc 0.31 in detail) and
the native protocol on the other; it is a client of parlord like the other adapters.

Three login styles at the login: prompt pick the session's mode:

    connect name password       MacFIBS (TinyMUD style): no "> " prompts
    name, then password         FIBS/W, xfibs, xibc, terminals: a "> " prompt
                                after every reply and every burst of news
    login client 1008 name pw   CLIP: numbered login, who and chat lines

Sign-in and sign-up are the server's steps (common.sign_in_lines): the name
alone goes to the server, the password answers its credential question and
is dropped if none comes, and every other step is a prompt in the client's
window (a sign-in with a mailed password goes on to a new password there). As on FIBS, a login only signs
in: a name with no account is refused with a pointer to guest (under sign-in
none, where a name is the whole sign-in, it is simply taken). "guest" then
"name x" signs up, the FIBS way.

Seat 0 of a backgammon/1 table is FIBS's player O (moving 24 down to 1) and
seat 1 is X (moving 1 up to 24), so points keep their numbers both ways.
Game messages use FIBS's wording, word for word where a client matches it
(the protocol's message formats, the board line, and the fixed phrases and
word counts MacFIBS, FIBS/W and xfibs look for). Everything else, the banner,
the guest dialog, help, about and the who/whois details, is ParlorNet's own.
Board lines (boardstyle 3, the default) carry all 53 fields; boardstyle 1
and 2 draw a small text board. One shared task polls the core's table list
and the core's list of players online (WHO) for presence ("who", "<name> logs
in."): players at tables, players connected elsewhere and the bot. invite
makes a private table and invites the other player through the core (INV),
anyone with an account, the bot included; join sits down at it. Invitations
made anywhere else (the web, telnet, the native protocol) come as FIBS's
"<name> wants to play a 1 point match with you." and "Type 'join <name>' to
accept."; decline <name> (ParlorNet's own word: FIBS has none) declines one.
The inviter hears of a declined or expired invitation.

The connection to the core is opened only once a login line has come, and
connections that have not signed in are counted and capped (common.Gate: per
adapter and per client address) with a short time to log in. Commands are
logged as typed, except chat (only the verb) and lines that are not commands.

    python3 -m adapters.fibs --core 127.0.0.1:6501 --port 6505 --host 0.0.0.0
"""
import argparse
import asyncio
import itertools
import re
import sys
import time

from . import botflag, declare
from .common import (framed_motd, LOGIN_SECS, LOGIN_TOTAL_SECS, CoreClient, claim_text, CoreError, Gate,
                     client_change_sentence, client_sentence, keepalive, err_text, parse_client, with_program,
                     loggable, parse_backgammon_view, parse_invites, parse_players, parse_tables, peer_addr,
                     sign_in_lines, uninvite_of)
from .telnet import TelnetIO

BG = "backgammon/1"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
MAX_SESSIONS = 60
POLL_SECS = 1.0         # each session's tables
HUB_SECS = 3.0          # the shared table-list poll (presence)
INVITE_POLLS = 3        # invitations made elsewhere: every third poll
# An empty line goes to "connect" sessions (MacFIBS) after this many quiet seconds. MacFIBS 3's Quit
# takes effect only once its pending receive returns, and under MacTCP that receive has no timeout, so on
# a quiet connection Quit could wait for the server's next line. MacFIBS 2 and 3 both drop empty lines.
KEEPALIVE_SECS = 4.0
MAX_BUFFER = 256 * 1024
MAX_LOGIN_TRIES = 3
PROMPT_WAIT = 120.0     # a sign-in answer typed in the client's window
MAX_LINE = 400
MAX_MATCH = 99
WHERE = "on the web (http://parlornet.org) or by telnet (port 6502)"
# The login banner: ParlorNet's own words. It says what this service is and where FIBS itself is. (FIBS's
# ASCII-art banner is not reproduced: no client needs it to log in.)
BANNER = ("",
          "This is ParlorNet's FIBS-protocol service for vintage Backgammon clients;",
          "it is not FIBS. For FIBS, the First Internet Backgammon Server, telnet to",
          "fibs.com 4321.",
          "",
          "Log in with your ParlorNet name and password. New here? Log in as guest.",
          "")
ABOUT = ["This is ParlorNet's FIBS-protocol service for vintage Backgammon clients (MacFIBS,",
         "FIBS/W, xfibs, xibc and terminals). Games are hosted by ParlorNet and are unrated.",
         "ParlorNet is not FIBS and is not affiliated with it. For FIBS, telnet to fibs.com 4321."]
# The rating and experience fields of who, rawwho and the CLIP own-info line: clients need a number there
# (MacFIBS reads the rating as a word, CLIP clients as a number), but games here are unrated, so every
# player shows this placeholder, not a FIBS-style rating.
UNRATED = "0.00"
# FIBS sent this line right after "** User <name> authenticated.", and ParlorNet sends it too. MacFIBS 3
# sends its start-up commands only once it sees a line that starts with "** Last login:". This server
# does not show the time of the last login.
BOT_HOWTO = "To play the bot, type invite bot 1 for a one-point match, or invite bot 5 for five points."
SAVED_HOWTO = ["Type show saved to list your unfinished games.",
               "Type join and a player's name or a table number to go back to one."]
LAST_LOGIN = "** Last login: not shown on this server."
# Clients that log in with a CLIP login line but cannot read CLIP's numbered lines. MacFIBS 3 has a
# hidden "Use CLIP" setting that sends "login MacFIBS_3.0.6 1008 name password", but nothing in the
# program handles the numbered lines that follow. These sessions get the same English lines as a
# "connect" login.
ENGLISH_CLIP_LOGINS = ("MacFIBS_3",)


def client_name(mode, clip=""):
    """The client program, as the core records it with each turn (CLI PROG): only what the client says, the
    name on a CLIP login line ("login MacFIBS_3.0.6 1008 ..." is MacFIBS 3.0.6). A connect login names no
    program: MacFIBS logs in that way, but so may any client, since connect is the TinyMUD-style login the
    CLIP specification gives to all. A login by name and password is used by FIBS/W, xfibs, xibc and
    every telnet program alike. Both report nothing."""
    if mode in ("clip", "client") and clip:
        return claim_text(clip.replace("_", " "))
    return ""
def client_line(name, say, verb):
    """What a player plays with, as a line of its own after FIBS's join lines, whose words clients match and
    which stay as they were: ["** bob joined with GameMaster 1.0.0 on a Macintosh Plus."], or with verb None
    a change mid-match ("** bob now plays with ..."). It starts with ** as FIBS's own notices do, so no
    CLIP client reads a name that starts with a digit as a numbered line. [] when nothing is known."""
    if not say:
        return []
    text = client_change_sentence(name, say, 76) if verb is None else client_sentence(name, say, verb, 76)
    return ["** " + text]


# The "from" (host) field of who and rawwho: players connected here with a FIBS-protocol client, and
# everyone else (the web, telnet, other protocols).
HERE, ELSEWHERE = "client", "web"
START = {24: 2, 13: 5, 8: 3, 6: 5}
LEVELS = {"n": "single", "normal": "single", "s": "single", "single": "single", "g": "gammon",
          "gammon": "gammon", "b": "backgammon", "backgammon": "backgammon"}

# toggles in FIBS's order (the client reads them until "wrap"), and FIBS's
# replies, which MacFIBS's Settings menu matches word for word: kept only
# because the client needs them
TOGGLES = ["allowpip", "autoboard", "autodouble", "automove", "bell", "crawford", "double", "greedy",
           "moreboards", "notify", "ratings", "ready", "report", "silent", "telnet", "wrap"]
TOGGLE_TEXT = {
    "allowpip": ("** You allow the use the server's 'pip' command.",
                 "** You don't allow the use of the server's 'pip' command."),
    "autoboard": ("** The board will be refreshed after every move.",
                  "** The board won't be refreshed after every move."),
    "autodouble": ("** You agree that doublets during opening double the cube.",
                   "** You don't agree that doublets during opening double the cube."),
    "automove": ("** Forced moves will be done automatically.", "** Forced moves won't be done automatically."),
    "bell": ("** Your terminal will ring the bell if someone talks to you or invites you",
             "** Your terminal won't ring the bell if someone talks to you or invites you"),
    "crawford": ("** You insist on playing with the Crawford rule.",
                 "** You would like to play without using the Crawford rule."),
    "double": ("** You will be asked if you want to double.", "** You won't be asked if you want to double."),
    "greedy": ("** Will use automatic greedy bearoffs.", "** Won't use automatic greedy bearoffs."),
    "moreboards": ("** Will send rawboards after rolling.", "** Won't send rawboards after rolling."),
    "notify": ("** You'll be notified when new users log in.", "** You won't be notified when new users log in."),
    "ratings": ("** You'll see how the rating changes are calculated.",
                "** You won't see how the rating changes are calculated."),
    "ready": ("** You're now ready to invite or join someone.", "** You're now refusing to play with someone."),
    "report": ("** You will be informed about starting and ending matches.",
               "** You won't be informed about starting and ending matches."),
    "silent": ("** You won't hear what other players shout.", "** You will hear what other players shout."),
    "telnet": ("** You use telnet and don't need extra 'newlines'.",
               "** You use a client program and will receive extra 'newlines'."),
    "wrap": ("** The server will wrap long lines.", "** Your terminal knows how to wrap long lines."),
}
DEFAULTS = {"allowpip": True, "autoboard": True, "autodouble": False, "automove": False, "bell": False,
            "crawford": True, "double": True, "greedy": False, "moreboards": True, "notify": True,
            "ratings": False, "ready": True, "report": False, "silent": False, "telnet": True, "wrap": False}

# command words in priority order: an abbreviation means the first that starts with it
COMMANDS = ["board", "move", "roll", "double", "accept", "reject", "resign", "join", "invite", "decline", "leave",
            "look",
            "kibitz", "say", "set", "show", "shout", "tell", "toggle", "who", "whisper", "whois", "watch",
            "unwatch", "rawwho", "pip", "away", "back", "help", "motd", "about", "version", "bye", "quit",
            "logout", "exit", "adios", "ciao", "tschoe", "gag", "blind", "message", "ratings", "redouble",
            "beaver", "panic", "address", "average", "dicetest", "stat", "waitfor", "time"]
QUIT_WORDS = ("bye", "quit", "logout", "exit", "adios", "ciao", "tschoe")
UNSUPPORTED = ("message", "ratings", "redouble", "beaver", "panic", "address", "average", "dicetest", "stat",
               "waitfor")

ids = itertools.count(1)


class Refused(Exception):
    pass


def log(sid, text):
    print("fibs[%d] %s" % (sid, text), flush=True)


def no_wait(text):
    return re.sub(r"^wait=\d+ ", "", text)


def plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def expand(word):
    """An abbreviated command word -> the command, or None."""
    w = word.lower()
    if w in COMMANDS:
        return w
    return next((c for c in COMMANDS if c.startswith(w)), None) if w else None


def match_words(length):
    return "an unlimited match" if not length else "a %d point match" % length


# ---- the game as FIBS shows it -----------------------------------------------------

class Model:
    """A table's position as the board line needs it: kept from events, and
    replaced by the core's view whenever the view is known to be current."""

    def __init__(self):
        self.pts = [0] * 24
        self.bar, self.off = [0, 0], [0, 0]
        self.turn, self.phase, self.over = -1, "", False
        self.cube, self.owner = 1, -1
        self.rolled, self.need = None, 0
        self.ca, self.use_cube = [True, True], True
        self.match, self.score, self.game, self.crawford = 0, [0, 0], 1, False
        self.offer = None       # (seat, points, kind, phase before)
        self.keep = False       # the next TURN keeps the phase (a double or a resign offer)

    def copy(self):
        m = Model()
        m.__dict__.update({k: (list(v) if isinstance(v, list) else v) for k, v in self.__dict__.items()})
        return m

    def new_game(self):
        self.pts = [0] * 24
        for p, n in START.items():
            self.pts[p - 1] += n
            self.pts[25 - p - 1] -= n
        self.bar, self.off = [0, 0], [0, 0]
        self.cube, self.owner, self.rolled, self.need, self.offer = 1, -1, None, 0, None
        self.ca = [self.use_cube and not self.crawford] * 2
        self.over = False

    def from_view(self, v):
        self.pts, self.bar, self.off = list(v["pts"]), list(v["bar"]), list(v["off"])
        self.turn, self.phase = v["turn"], v["phase"]
        self.over = v["phase"] == "over"
        self.cube, self.owner = v["cube"], v["cube_owner"]
        self.rolled, self.need = v["rolled"], v["need"]
        if v["cube_ok"] is not None:
            self.ca = list(v["cube_ok"])
        self.match, self.score, self.game, self.crawford = v["match"], list(v["score"]), v["game"], v["crawford"]
        if v["offer"]:
            before = self.offer[3] if self.offer else "move"
            self.offer = tuple(v["offer"]) + (before,)
        else:
            self.offer = None

    def step(self, seat, frm, to, hit):
        s = 1 if seat == 0 else -1
        if frm == "bar":
            self.bar[seat] -= 1
        else:
            self.pts[int(frm) - 1] -= s
        if to == "off":
            self.off[seat] += 1
        else:
            if hit:
                self.pts[int(to) - 1] = 0
                self.bar[1 - seat] += 1
            self.pts[int(to) - 1] += s

    def pips(self, seat):
        if seat == 0:
            return sum(n * p for p, n in enumerate(self.pts, 1) if n > 0) + 25 * self.bar[0]
        return sum(-n * (25 - p) for p, n in enumerate(self.pts, 1) if n < 0) + 25 * self.bar[1]


def board_line(m, me, persp, names):
    """The boardstyle 3 line, 53 fields, for the player at seat persp. me is
    True when that player is the viewer ("You"). Seat 0 is O (color 1,
    direction -1, home 0, bar 25), seat 1 is X (color -1, direction 1, home
    25, bar 0); positions are the absolute points, positive for O."""
    p, o = persp, 1 - persp
    pos = [-m.bar[1]] + list(m.pts) + [m.bar[0]]
    moving = not m.over and m.turn in (0, 1) and m.rolled and (m.phase == "move" or (
        m.phase == "resign" and m.offer and m.offer[3] == "move"))
    dice = [0, 0, 0, 0]
    if moving:
        k = 0 if m.turn == p else 2
        dice[k:k + 2] = list(m.rolled)
    turn = 0 if m.over or m.turn not in (0, 1) else (1 if m.turn == 0 else -1)
    fields = (["board", "You" if me else names[p], names[o], m.match or 9999,
               m.score[p] if m.match else 0, m.score[o] if m.match else 0] + pos +
              [turn] + dice + [m.cube, int(bool(m.ca[p])), int(bool(m.ca[o])),
                               int(m.phase == "take" and m.turn == p and not m.over),
               1 if p == 0 else -1, -1 if p == 0 else 1, 0 if p == 0 else 25, 25 if p == 0 else 0,
               m.off[p], m.off[o], m.bar[p], m.bar[o],
               m.need if moving and m.turn == p else 0, 0, 0, 0])
    return ":".join(str(f) for f in fields)


def text_board(m, me, persp, names):
    """boardstyle 1 and 2: a small board for people at a terminal (O is seat 0)."""
    p = persp
    top = list(range(13, 25)) if p == 0 else list(range(12, 0, -1))
    bottom = list(range(12, 0, -1)) if p == 0 else list(range(13, 25))

    def row(points, level, up):
        cells = []
        for q in points:
            n = m.pts[q - 1]
            c = "O" if n > 0 else "X"
            k = abs(n)
            if level == 4 and k > 5:
                cells.append("%2d " % min(k, 99))
            else:
                cells.append(" %s " % c if k > level else " . ")
        return " " + "".join(cells[:6]) + "|" + "".join(cells[6:])

    def labels(points):
        return " " + "".join("%2d " % q for q in points[:6]) + "|" + "".join("%2d " % q for q in points[6:])
    out = [labels(top)] + [row(top, lv, False) for lv in range(5)]
    out.append("   BAR  O %d  X %d     OFF  O %d  X %d     cube %d" % (m.bar[0], m.bar[1], m.off[0], m.off[1],
                                                                  m.cube))
    out += [row(bottom, lv, True) for lv in range(4, -1, -1)] + [labels(bottom)]
    who = "You" if me else names[p]
    mover = "" if m.turn not in (0, 1) else ("O" if m.turn == 0 else "X")
    dice = " rolled %d-%d" % tuple(m.rolled) if m.rolled and m.phase == "move" else ""
    out.append("   %s are %s (O moves 24 to 1, X moves 1 to 24). %s%s" % (
        who, "O" if p == 0 else "X", "%s to move%s." % (mover, dice) if mover and not m.over else "Game over.", ""))
    if m.match:
        out.append("   %d point match: %s %d, %s %d" % (m.match, names[0], m.score[0], names[1], m.score[1]))
    return out


def parse_move(words, seat):
    """'13-9 8-7', '13 9 8 7', 'bar-22', 'b 22', '6 off', 'bar - 5' -> [(from, to)]
    in our terms ('bar', point or 'off'), or None. 0 and 25 mean the bar or
    home of the mover."""
    toks = " ".join(words).lower().replace("-", " ").split()
    pts = []
    for t in toks:
        if t in ("bar", "b", "ba"):
            pts.append("bar")
        elif t in ("off", "o", "of", "home"):
            pts.append("off")
        elif t.isdigit() and 0 <= int(t) <= 25:
            n = int(t)
            if n in (0, 25):
                pts.append("bar" if (n == 25) == (seat == 0) else "off")
            else:
                pts.append(n)
        else:
            return None
    if not pts or len(pts) % 2:
        return None
    pairs = list(zip(pts[0::2], pts[1::2]))
    if any(f == "off" or t == "bar" for f, t in pairs):
        return None
    return pairs


def is_move_line(words):
    w = words[0].lower()
    return bool(re.match(r"^(\d|bar)", w)) or (w in ("b", "o") and len(words) > 1 and
                                              re.match(r"^[\d-]", words[1]))


# ---- shared state ----------------------------------------------------------------

class Hub:
    """Everyone connected here and what the core's table list shows."""

    def __init__(self):
        self.sessions = {}      # lower name -> Session
        self.tables = []
        self.online = []        # the last WHO: players connected anywhere, and one bot (botflag.listed)
        self.bots = set()       # lower names of the bots WHO listed
        self.who_ok = False     # the core answers WHO: who lists the players online, nobody else
        self.lengths = {}       # table id -> match length, for the invitations made here (0: unlimited)
        self.present = {}
        self.task = None
        self.turn = 0

    def everyone(self):
        """lower name -> name: the players online (the core's WHO: active in the last presence.idle
        seconds, on any frontend, and the bot). With a core that has no WHO: the players at tables and
        those connected here."""
        if self.who_ok:
            out = {n.lower(): n for n in self.online}
            for k, s in self.sessions.items():
                if k in out:
                    out[k] = s.name
            return out
        out = {}
        for t in self.tables:
            # The list is polled through one session after another, and a private table is listed only to
            # its own players. Counting its players would make them log in and out from poll to poll.
            if t.get("vis") == "private":
                continue
            for n in t["names"]:
                out[n.lower()] = n
        for k, s in self.sessions.items():
            out[k] = s.name
        return out

    def sync_presence(self, skip=None, silent=False):
        now = self.everyone()
        new = [n for k, n in now.items() if k not in self.present]
        gone = [n for k, n in self.present.items() if k not in now]
        self.present = now
        if silent:
            return
        for s in list(self.sessions.values()):
            if s is not skip:
                s.presence(new, gone)

    async def poll(self, via, silent=False):
        data, final = await via.core_req("LST all")
        if final.startswith("OK"):
            self.tables = parse_tables(data)
            await self.who(via)
            while len(self.lengths) > 200:     # private tables are not in every LST: keep the newest
                del self.lengths[next(iter(self.lengths))]
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
            via = ss[self.turn % len(ss)]
            self.turn += 1
            try:
                await self.poll(via)
            except (CoreError, OSError, asyncio.TimeoutError):
                via.closing = True
            except Exception as e:      # the shared poll must keep running
                print("fibs hub error: %r" % e, file=sys.stderr, flush=True)
        self.task = None

    async def enter(self, s):
        try:
            if not self.sessions:
                self.present = {}
                await self.poll(s, silent=True)
            else:
                await self.who(s)           # the newcomer is online now: the others hear of it at once
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        old = self.sessions.get(s.name.lower())
        if old and old is not s:
            old.post(["** Warning: You logged in again somewhere else. Goodbye."], prompt=False)
            old.closing = True
            old.io.w.close()
        self.sessions[s.name.lower()] = s
        self.sync_presence(skip=s)
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self.run())

    def leave(self, s):
        k = s.name.lower()
        if self.sessions.get(k) is s:
            del self.sessions[k]
        self.sync_presence()

    def table_with(self, *names, status=("playing",)):
        want = [n.lower() for n in names]
        for t in self.tables:
            seated = [n.lower() for n in t["names"]]
            if t["key"] == BG and t["status"] in status and all(n in seated for n in want):
                return t
        return None


class Track:
    """A table this session plays (seat 0/1) or watches (seat -1, from persp)."""

    def __init__(self, tid, seat, persp=None):
        self.tid, self.seat = tid, seat
        self.persp = seat if seat >= 0 else (persp or 0)
        self.cursor = 0
        self.names = ["?", "?"]
        self.m = Model()
        self.pending = []       # steps of the turn being played: (seat, from, to, hit)
        self.started = False
        self.created = False    # I opened the table (I invited)
        self.length = 0
        self.opening = None
        self.hold = False       # between games of a match: waits for "join"
        self.autoroll = False
        self.own_cht = 0
        self.clients = {}       # seat -> what its player plays with, in words (CLIENT events)


# ---- one connection ----------------------------------------------------------------

class Session:
    def __init__(self, hub, io, core_addr, ticket=None):
        self.hub, self.io, self.core_addr = hub, io, core_addr
        self.ticket = ticket
        self.addr = peer_addr(io.w)
        self.sid = next(ids)
        self.name = None
        self.mode = "telnet"        # telnet | client (connect, MacFIBS) | clip
        self.client = "-"
        self.prog = ""              # what this session told the core it is (CLI PROG), for "has joined you"
        self.core = None
        self.core_lock = asyncio.Lock()
        self.ev_lock = asyncio.Lock()
        self.toggles = dict(DEFAULTS)
        self.sets = {"boardstyle": "3", "linelength": "0", "pagelength": "0", "redoubles": "none",
                     "sortwho": "login", "timezone": "UTC"}
        self.table = None           # the table I play
        self.watching = None        # the table I watch
        self.tracks = {}
        self.away = None
        self.gagged, self.blinded = set(), set()
        self.login_at = int(time.time())
        self.announced = {}         # invitations already shown: table id -> who invited
        self.offline = False        # the player my last invitation went to was not online
        self.closing = False
        self.last_out = time.monotonic()     # when a line was last sent (the keepalive's clock)

    # ---- output ---------------------------------------------------------------
    def prompting(self):
        return bool(self.name) and self.mode != "clip" and self.toggles["telnet"]

    def post(self, lines, prompt=True):
        """Lines at once, CR LF ends; telnet-mode sessions get the "> " prompt
        after the burst (xfibs parses nothing until the input ends in "> ")."""
        w = self.io.w
        if w.is_closing():
            return
        text = "".join(l + "\r\n" for l in lines)
        if prompt and self.prompting():
            text += "You're away. Please type 'back' > " if self.away is not None else "> "
        if text:
            w.write(text.encode("ascii", "replace"))
            self.last_out = time.monotonic()
        if w.transport.get_write_buffer_size() > MAX_BUFFER:
            self.closing = True
            w.close()

    async def send(self, lines, prompt=True):
        self.post(lines, prompt)
        await self.io.w.drain()

    async def core_req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    def presence(self, new, gone):
        if not self.name or not self.toggles["notify"]:
            return
        me = self.name.lower()
        lines = []
        for n in new:
            if n.lower() != me:
                lines.append("7 %s %s logs in." % (n, n) if self.mode == "clip" else "%s logs in." % n)
        for n in gone:
            if n.lower() != me:
                lines.append("8 %s %s logs out." % (n, n) if self.mode == "clip" else "%s logs out." % n)
        if lines:
            self.post(lines)

    # ---- login ----------------------------------------------------------------
    async def readline(self):
        return await self.io.readline(maxlen=MAX_LINE)

    async def full(self):
        self.io.w.write(b"\r\n** Server is full, try again soon.\r\n")
        await self.io.w.drain()
        raise Refused("full")

    async def login_line(self):
        """A line during the login phase: it must come within LOGIN_SECS."""
        try:
            line = await asyncio.wait_for(self.readline(), LOGIN_SECS)
        except asyncio.TimeoutError:
            raise Refused("no login")
        if self.ticket is not None:
            self.ticket.touch()
        return line

    async def login(self):
        w = self.io.w
        # "login: " with no line end: MacFIBS, xfibs and xibc wait for a partial line starting "login:"
        w.write(("\r\n".join(BANNER) + "\r\nlogin: ").encode("ascii"))
        await w.drain()
        tries = 0
        while True:
            line = ""
            while not line:
                line = await self.login_line()
            words = line.split()
            first = words[0].lower()
            name = password = None
            if first == "connect" and len(words) >= 2:
                self.mode = "client"
                name, password = words[1], " ".join(words[2:])
                log(self.sid, "connect %r (password not logged)" % name)
            elif first == "login" and len(words) >= 4 and words[2].isdigit():
                self.mode, self.client = "clip", words[1][:20]
                if words[1].startswith(ENGLISH_CLIP_LOGINS):
                    self.mode = "client"
                name, password = words[3], " ".join(words[4:])
                log(self.sid, "clip login %r client %r version %s (password not logged)" % (name, words[1],
                                                                                             words[2]))
            elif first == "guest" and len(words) == 1:
                log(self.sid, "guest")
                name = await self.register()
                if name:
                    break
            elif len(words) == 1:
                self.mode = "telnet"
                name = words[0]
                log(self.sid, "login %r" % name)
                w.write(b"\r\npassword: ")
                await w.drain()
                password = await self.login_line()
                log(self.sid, "password line received (not logged)")
            if name and password is not None:
                refusal, _ = await self.sign_in(name, password)
                if refusal is None and self.ticket is not None and not self.ticket.sign_in():
                    await self.full()
                if refusal is None:
                    self.name = name
                    self.toggles["telnet"] = self.mode == "telnet"
                    if self.core is not None:       # the turns say what they were played with (CLI)
                        self.prog = client_name(self.mode, words[1] if first == "login" else "")
                        await self.core.claim(self.prog)
                    await self.welcome([])
                    return
            else:
                refusal = "Log in with your name, or as guest."
            tries += 1
            log(self.sid, "refused: %s" % refusal)
            w.write(("\r\n** %s\r\n" % refusal).encode("ascii", "replace"))
            if tries >= MAX_LOGIN_TRIES:
                await w.drain()
                raise Refused(refusal)
            w.write(b"\r\nlogin: ")
            await w.drain()
        # a guest who registered
        await self.welcome([])

    async def sign_in(self, name, password, mode="signin"):
        """(refusal, None): refusal is None when signed in. Sign-in, sign-up
        and reset are the core's steps (common.sign_in_lines), shown as lines
        and prompts in the client's window before the player joins the lobby;
        the password answers the first question (dropped if none comes).
        mode "signin" (a login): a name with no account is refused with a
        pointer to guest, FIBS's way to register, and never starts a sign-up
        (except under sign-in none, where the name is the whole sign-in);
        "signup": the guest dialog's name x."""
        if not NAME_RE.match(name):
            return "Names are 1 to 12 letters, digits, - or _.", None
        if self.core is None:       # only now, with a login line from the client, a connection to the core
            try:
                self.core = await CoreClient.connect(*self.core_addr)
                await self.core.source(self.addr)
            except (OSError, asyncio.TimeoutError, CoreError):
                self.core = None
                return "The game server isn't answering. Try again soon.", None
        unknown = "There is no account named %s here. New here? Log in as guest." % name
        return await sign_in_lines(self.core, name, password, self.prompt, self.show, mode, unknown), None

    async def show(self, lines):
        self.io.w.write("".join("\r\n" + l for l in lines).encode("ascii", "replace"))
        await self.io.w.drain()

    async def prompt(self, label, secret=False):
        """A sign-in question with the server's own label; what is typed back
        (not logged), or "" if nothing comes within PROMPT_WAIT."""
        self.io.w.write(("\r\n%s: " % label).encode("ascii", "replace"))
        await self.io.w.drain()
        try:
            line = (await asyncio.wait_for(self.readline(), PROMPT_WAIT)).strip()
        except asyncio.TimeoutError:
            return ""
        log(self.sid, "sign-in answer received (not logged)")
        if self.ticket is not None:
            self.ticket.touch()
        return line

    async def register(self):
        """The guest dialog: 'name x', then the server's sign-up steps as
        prompts. Returns the new name or None."""
        w = self.io.w
        self.mode = "telnet"
        self.name = None
        # ParlorNet's own wording; FIBS/W needs only the "> " prompt (no "password: ") here and, at the
        # end, "You are registered." at the start of a line
        w.write(b"\r\nYou are signing up for a ParlorNet name (this is not a FIBS account).\r\n"
                b"Type 'name' and the name you want, for example: name alice\r\n"
                b"(it starts with a letter and has up to 12 letters, digits, - and _, with - and _ only\r\n"
                b"between letters or digits). Type 'bye' to leave without signing up.\r\n> ")
        await w.drain()
        while True:
            line = await self.login_line()
            words = line.split()
            if not words:
                w.write(b"> ")
                continue
            cmd = words[0].lower()
            if cmd in QUIT_WORDS:
                raise Refused("guest left")
            if cmd != "name" or len(words) != 2:
                w.write(b"** Type 'name' and the name you want, or 'bye'.\r\n> ")
                continue
            name = words[1]
            log(self.sid, "register %r" % name)
            if not NAME_RE.match(name):
                w.write(b"** Names are 1 to 12 letters, digits, - or _.\r\n> ")
                continue
            refusal, _ = await self.sign_in(name, None, "signup")      # the core drives the sign-up
            if refusal is not None:
                w.write(("\r\n** %s\r\n> " % refusal).encode("ascii", "replace"))
                continue
            if self.ticket is not None and not self.ticket.sign_in():
                await self.full()
            # "You are registered." is the phrase FIBS/W's guest script waits for
            w.write(b"\r\nYou are registered.\r\n")
            await w.drain()
            self.name = name
            return name

    async def welcome(self, notes):
        motd = []
        try:
            motd = await self.core.motd()
        except CoreError:
            pass
        self.io.on_input = self.core.user_input     # every command typed from now on is activity
        await self.hub.enter(self)
        if self.mode == "clip":
            o = self.own_info()
            lines = ["1 %s %d parlornet" % (self.name, self.login_at), o, "3"] + framed_motd(motd) + ["4"]
            lines += self.who_rows(clip=True) + ["6"] + notes
            self.post(lines, prompt=False)
        else:
            # MacFIBS 2 starts its set, toggle and rawwho on "** User <name> authenticated.". MacFIBS 3
            # waits for the "** Last login:" line instead.
            self.post(["", "** User %s authenticated." % self.name, LAST_LOGIN] + framed_motd(motd) + notes +
                      ["This is ParlorNet, not FIBS. Games here are unrated. Type help for the commands.",
                       BOT_HOWTO] + SAVED_HOWTO, prompt=False)
        await self.rejoin()
        await self.check_invitations()
        await self.send([])

    def own_info(self):
        t = self.toggles
        b = lambda k: "1" if t[k] else "0"      # noqa: E731
        return "2 %s %s %s %s %s %s %s %s %s 0 %s %s 0 %s %s %s %s 0 %s %s UTC" % (
            self.name, b("allowpip"), b("autoboard"), b("autodouble"), b("automove"),
            "1" if self.away is not None else "0", b("bell"), b("crawford"), b("double"), b("greedy"),
            b("moreboards"), b("notify"), UNRATED, b("ratings"), b("ready"), b("report"), b("silent"))

    # ---- commands ---------------------------------------------------------------
    async def command(self, line):
        words = line.split()
        if not words:
            if self.prompting():
                await self.send([])
            return
        if is_move_line(words):
            await self.move(words)
            return
        word = words[0]
        cmd = expand(word)
        args = words[1:]
        if cmd is None:
            await self.send(["** Unknown command: '%s'" % word[:20]])
            return
        if cmd in QUIT_WORDS:
            await self.send(["", "Goodbye. Thanks for playing on ParlorNet."], prompt=False)
            self.closing = True
            return
        handler = getattr(self, "cmd_" + cmd, None)
        if handler:
            await handler(args, line)
        elif cmd in UNSUPPORTED:
            await self.send(["** '%s' isn't available on this server." % cmd])
        else:
            await self.send(["** Unknown command: '%s'" % word[:20]])

    async def cmd_help(self, args, line):
        await self.send([
            "Commands: who, rawwho, invite <name> <points>|unlimited, join [<name>], decline <name>,",
            "move <from-to> ...",
            "(or just 13-7 8-7), roll, double, accept, reject, resign n|g|b, leave, board, pip,",
            "watch <name>, unwatch, look <name>, tell <name> <text>, say, kibitz, whisper, shout,",
            "toggle [<name>], set [<name> <value>], show games, away, back, bye.",
            "Points are numbered for O (24 to 1); X counts 1 up to 24. Games are unrated.",
            "Your opponent may be on the web or a terminal: everyone plays the same tables.",
            "This is ParlorNet, not FIBS; type 'about' to read more."])

    async def cmd_about(self, args, line):
        await self.send(ABOUT)

    cmd_version = cmd_about

    async def cmd_motd(self, args, line):
        try:
            motd = await self.core.motd()
        except CoreError:
            motd = []
        await self.send(framed_motd(motd) or ["There is no message of the day."])

    async def cmd_time(self, args, line):
        await self.send([time.strftime("%A, %B %d %H:%M:%S UTC", time.gmtime())])

    # settings
    async def cmd_toggle(self, args, line):
        if not args:
            await self.send(["The current settings are:"] +
                            ["%-15s %s" % (k, "YES" if self.toggles[k] else "NO") for k in TOGGLES])
            return
        k = next((t for t in TOGGLES if t.startswith(args[0].lower())), None)
        if args[0].lower() == "moves":
            await self.send(["** Moves are listed after every game."])
            return
        if k is None:
            await self.send(["** Don't know how to toggle %s" % args[0][:20]])
            return
        self.toggles[k] = not self.toggles[k]
        await self.send([TOGGLE_TEXT[k][0 if self.toggles[k] else 1]])

    async def cmd_set(self, args, line):
        if not args:
            s = self.sets
            await self.send(["Settings of variables:", "boardstyle: %s" % s["boardstyle"],
                             "linelength: %s" % s["linelength"], "pagelength: %s" % s["pagelength"],
                             "redoubles:  %s" % s["redoubles"], "sortwho:    %s" % s["sortwho"],
                             "timezone:   %s" % s["timezone"]])
            return
        k = next((x for x in self.sets if x.startswith(args[0].lower())), None)
        if k is None:
            await self.send(["** Invalid argument. Type 'help set'."])
            return
        if len(args) < 2:
            await self.send(["Value of '%s' is %s" % (k, self.sets[k])])
            return
        v = args[1][:24]
        if k == "boardstyle" and v not in ("1", "2", "3"):
            await self.send(["** Valid arguments are the numbers 1 to 3."])
            return
        if k in ("linelength", "pagelength") and not v.isdigit():
            await self.send(["** Valid arguments are numbers."])
            return
        self.sets[k] = v
        await self.send(["Value of '%s' set to %s." % (k, v)])

    async def cmd_away(self, args, line):
        self.away = " ".join(args) or "away"
        await self.send(["You're away. Please type 'back'"])

    async def cmd_back(self, args, line):
        was = self.away
        self.away = None
        await self.send(["Welcome back." if was is not None else "** You're not away."])

    async def cmd_gag(self, args, line):
        if args:
            k = args[0].lower()
            if k in self.gagged:
                self.gagged.discard(k)
                await self.send(["** You ungag %s." % args[0]])
            else:
                self.gagged.add(k)
                await self.send(["** You gag %s." % args[0]])
        else:
            await self.send(["** gag whom?"])

    async def cmd_blind(self, args, line):
        if args:
            k = args[0].lower()
            if k in self.blinded:
                self.blinded.discard(k)
                await self.send(["** You unblind %s." % args[0]])
            else:
                self.blinded.add(k)
                await self.send(["** You blind %s." % args[0]])
        else:
            await self.send(["** blind whom?"])

    # who
    def status_of(self, k):
        hub = self.hub
        playing = any(t["key"] == BG and t["status"] == "playing" and k in [n.lower() for n in t["names"]]
                      for t in hub.tables)
        waiting = any(t["key"] == BG and t["status"] == "open" and k in [n.lower() for n in t["names"]]
                      for t in hub.tables)
        s = hub.sessions.get(k)
        if k in hub.bots:
            return "R-"         # the bot plays many tables at once: always ready to be invited
        if playing or (s and s.table and s.tracks.get(s.table) and s.tracks[s.table].started):
            first = "P"
        elif waiting or (s and s.toggles["ready"]):
            first = "R"
        else:
            first = "-"
        return first + ("W" if s and s.watching else "-")

    def opponent_of(self, k):
        t = self.hub.table_with(k)
        if t:
            return next((n for n in t["names"] if n.lower() != k), "-")
        return "-"

    def who_rows(self, clip=False, only=None, ready=False):
        people = self.hub.everyone()
        rows = []
        for k, n in sorted(people.items(), key=lambda e: e[1].lower()):
            if only and k != only.lower():
                continue
            st = self.status_of(k)
            if ready and st[0] != "R":
                continue
            s = self.hub.sessions.get(k)
            where = HERE if s else ELSEWHERE
            if clip:
                watching = "-"
                if s and s.watching and s.tracks.get(s.watching):
                    tr = s.tracks[s.watching]
                    watching = tr.names[tr.persp]
                rows.append("5 %s %s %s %d %d %s 0 0 %d %s %s -" % (
                    n, self.opponent_of(k), watching, st[0] == "R", bool(s and s.away is not None), UNRATED,
                    s.login_at if s else int(time.time()), where, s.client if s else "web"))
            else:
                rows.append((n, st, where))
        return rows

    async def cmd_rawwho(self, args, line):
        await self.hub.poll(self)
        only = args[0] if args else None
        if self.mode == "clip":
            await self.send(self.who_rows(clip=True, only=only) + ["6"])
            return
        # 9 words: who: <opponent> <status> <name> <rating> <experience> <idle> <login> <where>
        lines = ["who: %s %s %s %s 0 0 0 %s" % (self.opponent_of(n.lower()), st, n, UNRATED, where)
                 for n, st, where in self.who_rows(only=only)]
        await self.send(lines + ["who:end"])

    async def cmd_who(self, args, line):
        await self.hub.poll(self)
        ready = bool(args) and args[0].lower() == "ready"
        only = args[0] if args and not ready else None
        if self.mode == "clip":
            await self.send(self.who_rows(clip=True, only=only, ready=ready) + ["6"])
            return
        lines = ["No  S  username        rating   exp login  idle from"]
        for i, (n, st, where) in enumerate(self.who_rows(only=only, ready=ready), 1):
            lines.append("%-3d %-2s %-14s %7s %5d %5s %5s %s" % (i, st, n, UNRATED, 0, "-", "-", where))
        await self.send(lines)

    async def cmd_whois(self, args, line):
        if not args:
            await self.send(["** whois whom?"])
            return
        await self.hub.poll(self)
        k = args[0].lower()
        n = self.hub.everyone().get(k)
        if not n:
            await self.send(["No information found on user %s." % args[0]])
            return
        s = self.hub.sessions.get(k)
        if k in self.hub.bots:
            await self.send(["Information about %s:" % n, "  This is the server's bot.",
                             "  Type 'invite %s 1' to play against it." % n])
            return
        lines = ["Information about %s:" % n,
                 "  Logged in here with a FIBS-protocol client." if s else "  Online on the web or telnet.",
                 "  No rating: games here are unrated."]
        opp = self.opponent_of(k)
        if opp != "-":
            lines.append("  %s is playing with %s." % (n, opp))
        await self.send(lines)

    async def cmd_show(self, args, line):
        what = args[0].lower() if args else ""
        await self.hub.poll(self)
        if "games".startswith(what) and what:
            rows = ["%-12s - %-12s (table %s)" % (t["names"][0], t["names"][1], t["id"]) for t in self.hub.tables
                    if t["key"] == BG and t["status"] == "playing" and len(t["names"]) == 2]
            await self.send(["List of games:"] + (rows or ["No games are being played."]))
        elif "saved".startswith(what) and what:
            mine = [t for t in self.hub.tables if t["key"] == BG and t["status"] == "playing"
                    and self.name.lower() in [n.lower() for n in t["names"]]]
            await self.send(["  %s (table %s)" % (" - ".join(t["names"]), t["id"]) for t in mine]
                            or ["no saved games."])
        elif "watchers".startswith(what) and what:
            rows = ["%s is watching %s." % (s.name, s.tracks[s.watching].names[s.tracks[s.watching].persp])
                    for s in self.hub.sessions.values() if s.watching and s.watching in s.tracks]
            await self.send(rows or ["Nobody is watching."])
        else:
            await self.send(["** Type 'show games', 'show saved' or 'show watchers'."])

    # chat
    def hear(self, kind, who, text):
        """Chat from someone: FIBS's text, or CLIP 12-15."""
        if who.lower() in self.gagged:
            return
        if self.mode == "clip":
            self.post(["%d %s %s" % ({"says": 12, "shouts": 13, "whispers": 14, "kibitzes": 15}[kind], who,
                                     text)])
        else:
            self.post(["%s %s: %s" % (who, kind, text)])

    async def echo(self, kind, target, text):
        if self.mode == "clip":
            await self.send([{"tell": "16 %s %s" % (target, text), "say": "16 %s %s" % (target, text),
                              "shout": "17 %s" % text, "whisper": "18 %s" % text,
                              "kibitz": "19 %s" % text}[kind]])
        else:
            await self.send(["** You %s%s: %s" % (kind, " " + target if kind in ("tell", "say") and target else "",
                                                   text)])

    async def cmd_tell(self, args, line):
        if len(args) < 2:
            await self.send(["** tell whom what?"])
            return
        who, text = args[0], " ".join(args[1:])
        target = self.hub.sessions.get(who.lower())
        tr = self.tracks.get(self.table) if self.table else None
        if target:
            target.hear("says", self.name, text)
            await self.echo("tell", target.name, text)
        elif tr and who.lower() in [n.lower() for n in tr.names]:
            await self.core_req("CHT %s %s" % (tr.tid, text[:56]))
            await self.echo("tell", who, text)
        else:
            await self.send(["** %s is not logged in on this service and not at your table." % who])

    async def cmd_say(self, args, line):
        tr = self.tracks.get(self.table) if self.table else None
        if not tr or not tr.started:
            await self.send(["** You're not playing, so you can't say something."])
            return
        text = " ".join(args)
        if not text:
            await self.send(["** say what?"])
            return
        await self.core_req("CHT %s %s" % (tr.tid, text[:56]))
        await self.echo("say", tr.names[1 - tr.seat], text)

    async def cmd_kibitz(self, args, line):
        tid = self.table or self.watching
        tr = self.tracks.get(tid) if tid else None
        text = " ".join(args)
        if not tr or not text:
            await self.send(["** You're not playing or watching." if not tr else "** kibitz what?"])
            return
        if tr.seat < 0:
            tr.own_cht += 1
        await self.core_req("CHT %s %s" % (tr.tid, text[:56]))
        await self.echo("kibitz", None, text)

    async def cmd_whisper(self, args, line):
        """To the other watchers of my table who are connected to this service."""
        tid = self.table or self.watching
        text = " ".join(args)
        if not tid or not text:
            await self.send(["** You're not playing or watching." if not tid else "** whisper what?"])
            return
        for s in list(self.hub.sessions.values()):
            if s is not self and s.watching == tid:
                s.hear("whispers", self.name, text)
        await self.echo("whisper", None, text)

    async def cmd_shout(self, args, line):
        text = " ".join(args)
        if not text:
            await self.send(["** shout what?"])
            return
        for s in list(self.hub.sessions.values()):
            if s is not self and not s.toggles["silent"]:
                s.hear("shouts", self.name, text)
        await self.echo("shout", None, text)

    # ---- tables: invite and join --------------------------------------------------
    # A FIBS invitation is a private table (NEW ... vis=private) where I sit
    # as O and the other player is invited to the X seat (INV); join is JON.
    async def open_invited_table(self, length, opp):
        """Returns (table id, None) or (None, error text)."""
        opts = " match=%d" % length if length else ""
        _, final = await self.core_req("NEW %s standard%s vis=private" % (BG, opts))
        if not final.startswith("OK"):
            return None, err_text(final)
        tid = final.split()[1]
        self.hub.lengths[tid] = length          # before INV: the invited player's look may come at once
        _, final = await self.core_req("JON %s 0" % tid)
        if final.startswith("OK"):
            _, final = await self.core_req("INV %s %s 1" % (tid, opp))
        if not final.startswith("OK"):
            await self.core_req("LVE %s" % tid)
            return None, err_text(final)
        self.offline = final == "OK offline"    # not online now (WHO): the invitation waits for them
        return tid, None

    async def invitation_from(self, name):
        """The open backgammon table name invited me to, or None."""
        data, final = await self.core_req("IVL")
        if not final.startswith("OK"):
            return None
        for inv in parse_invites(data):
            if inv["key"] == BG and inv["from"].lower() == name.lower():
                return inv["id"]
        return None

    async def cmd_invite(self, args, line):
        if not args:
            await self.send(["** invite whom?"])
            return
        opp = args[0]
        if opp.lower() == self.name.lower():
            await self.send(["** You can't invite yourself."])
            return
        tr = self.tracks.get(self.table) if self.table else None
        if tr and tr.started:
            await self.send(["** You can't invite someone while you are playing."])
            return
        await self.hub.poll(self)
        running = self.hub.table_with(self.name, opp)
        if running and (len(args) == 1 or not tr):
            await self.resume(running)
            return
        if len(args) == 1:
            length = 1
        elif args[1].lower().startswith("unl"):
            length = 0
        elif args[1].isdigit() and 1 <= int(args[1]) <= MAX_MATCH:
            length = int(args[1])
        else:
            await self.send(["** The match length is 1 to %d points, or 'unlimited'." % MAX_MATCH])
            return
        target = self.hub.sessions.get(opp.lower())
        await self.drop_open_table()
        tid, error = await self.open_invited_table(length, opp)
        if tid is None:
            await self.send(["** There is no one called %s." % opp] if "no player" in error else
                            ["** Can't start a match: %s" % error])
            return
        tr = self.track(tid, 0)
        tr.created, tr.length = True, length
        name = target.name if target else self.hub.everyone().get(opp.lower(), opp)
        tr.names = [self.name, name]
        if target:
            if tid not in target.announced:         # (its own look at its invitations may have come first)
                target.announced[tid] = self.name
                bell = "\a" if target.toggles["bell"] and target.mode != "clip" else ""
                target.post(["%s%s wants to play %s with you." % (bell, self.name, match_words(length)),
                             "Type 'join %s' to accept." % self.name])
            await self.send(["** You invited %s to %s." % (name, match_words(length))])
        elif not self.offline and (opp.lower() in self.hub.bots or botflag.is_bot_name(opp)):
            await self.send(["** You invited %s to %s." % (name, match_words(length)),
                             "%s is the server's bot. It will join you in a few seconds." % name])
        elif not self.offline:
            await self.send(["** You invited %s to %s." % (name, match_words(length)),
                             "%s is on the web or telnet and will see the invitation there." % name])
        else:
            await self.send(["** You invited %s to %s." % (name, match_words(length)),
                             "%s is not online now. The invitation will wait for an hour." % name])

    async def check_invitations(self):
        """Invitations made elsewhere (the web, telnet, the native protocol) are announced once, in FIBS's
        words: a Backgammon table not opened here is a single game, a 1 point match. An invitation to
        another game is a line of text. One that goes away is forgotten (it can be announced again)."""
        data, final = await self.core_req("IVL")
        if not final.startswith("OK"):
            return
        invs = parse_invites(data)
        live = {i["id"] for i in invs}
        for tid in [t for t in self.announced if t not in live]:
            del self.announced[tid]
        out = []
        for i in invs:
            if i["id"] in self.announced:
                continue
            self.announced[i["id"]] = i["from"]
            if i["key"] == BG:
                bell = "\a" if self.toggles["bell"] and self.mode != "clip" else ""
                out += ["%s%s wants to play %s with you." % (bell, i["from"],
                                                             match_words(self.hub.lengths.get(i["id"], 1))),
                        "Type 'join %s' to accept." % i["from"]]
            else:
                out.append("** %s invites you to %s at table %s. You can play it %s." % (
                    i["from"], i["key"].split("/")[0].capitalize(), i["id"], WHERE))
        if out:
            self.post(out)

    async def cmd_decline(self, args, line):
        """decline <name>: an invitation declined (UNV). ParlorNet's own command; the inviter hears of it."""
        if not args:
            await self.send(["** decline whom?"])
            return
        data, final = await self.core_req("IVL")
        inv = next((i for i in parse_invites(data) if i["from"].lower() == args[0].lower()), None) \
            if final.startswith("OK") else None
        if inv is None:
            await self.send(["** %s didn't invite you." % args[0]])
            return
        await self.core_req("UNV %s %s" % (inv["id"], self.name))
        self.announced.pop(inv["id"], None)
        await self.send(["** You decline the invitation from %s." % inv["from"]])

    async def cmd_join(self, args, line):
        if not args:
            held = next((t for t in self.tracks.values() if t.hold), None)
            if held:
                held.hold = False
                await self.poll_track(held)
                await self.send([])
            else:
                await self.send(["** Error: No one to join."])
            return
        name = args[0]
        if name.lower() == self.name.lower():
            await self.send(["** You can't join yourself."])
            return
        tr = self.tracks.get(self.table) if self.table else None
        if tr and tr.started:
            await self.send(["** You are already playing."])
            return
        await self.hub.poll(self)
        if name.isdigit():
            # ParlorNet's addition: join a table by its number, as show saved and show games list them
            t = next((t for t in self.hub.tables if t["key"] == BG and t["id"] == name), None)
            mine = t and self.name.lower() in [n.lower() for n in t["names"]]
            if t and mine and t["status"] == "playing":
                await self.resume(t)
                return
            if t and not mine and t["status"] == "open" and len(t["names"]) == 1:
                name = t["names"][0]            # an open table: join the player waiting there
            else:
                await self.send(["** There is no game of yours at table %s. Type show saved to list them."
                                 % name])
                return
        running = self.hub.table_with(self.name, name)
        if running:
            await self.resume(running)
            return
        tid = await self.invitation_from(name)
        if tid is None:
            # a player waiting alone at an open public table
            t = next((t for t in self.hub.tables if t["key"] == BG and t["status"] == "open"
                      and [n.lower() for n in t["names"]] == [name.lower()]), None)
            tid = t["id"] if t else None
        if tid is None:
            await self.send(["** %s didn't invite you." % name])
            return
        await self.drop_open_table()
        _, final = await self.core_req("JON %s" % tid)
        if not final.startswith("OK"):
            await self.send(["** Can't join %s: %s" % (name, err_text(final))])
            return
        await self.poll_track(self.track(tid, int(final.split()[1])))     # from its first event

    async def drop_open_table(self):
        """A new invitation or join cancels my open table."""
        tr = self.tracks.get(self.table) if self.table else None
        if tr and not tr.started:
            await self.core_req("LVE %s" % tr.tid)
            self.untrack(tr.tid)

    async def resume(self, t):
        """Back at a running table: "Your running match was loaded."""
        old = self.tracks.get(t["id"])
        if old and old.seat >= 0 and not old.hold:
            await self.send(["** You are already playing with %s." % next(
                (n for n in t["names"] if n.lower() != self.name.lower()), "?")])
            return
        await self.drop_open_table()
        seat = [n.lower() for n in t["seated"]].index(self.name.lower())
        tr = self.track(t["id"], seat)
        tr.names = list(t["seated"][:2])
        tr.started = True
        await self.catch_up(tr)
        opp = tr.names[1 - seat]
        lines = ["You are now playing with %s. Your running match was loaded." % opp]
        lines += client_line(opp, tr.clients.get(1 - seat), "plays")
        lines += self.board_lines(tr, tr.m)
        lines += self.turn_prompt(tr)
        other = self.hub.sessions.get(opp.lower())
        if other and other.table == tr.tid:
            other.post(["%s has joined you. Your running match was loaded." % self.name] +
                       client_line(self.name, with_program(self.prog), "joined"))
        await self.send(lines)

    async def rejoin(self):
        """At login: pick up a running match (or my open table)."""
        data, _ = await self.core_req("LST")
        me = self.name.lower()
        mine = [t for t in parse_tables(data) if t["key"] == BG and t["status"] in ("playing", "open")
                and me in [n.lower() for n in t["names"]]]
        running = [t for t in mine if t["status"] == "playing"]
        if running:
            await self.resume(sorted(running, key=lambda t: int(t["id"]))[0])
        elif mine:
            t = sorted(mine, key=lambda t: int(t["id"]))[-1]
            tr = self.track(t["id"], [n.lower() for n in t["seated"]].index(me))
            tr.created = True
            await self.catch_up(tr)

    def track(self, tid, seat, persp=None):
        tr = Track(tid, seat, persp)
        self.tracks[tid] = tr
        if seat >= 0:
            self.table = tid
        else:
            self.watching = tid
        return tr

    def untrack(self, tid):
        self.tracks.pop(tid, None)
        if self.table == tid:
            self.table = None
        if self.watching == tid:
            self.watching = None

    async def cmd_leave(self, args, line):
        tr = self.tracks.get(self.table) if self.table else None
        if not tr:
            await self.send(["** You're not playing."])
            return
        if not tr.started:
            await self.drop_open_table()
            await self.send(["** You withdraw your invitation."])
            return
        opp = tr.names[1 - tr.seat]
        self.untrack(tr.tid)
        other = self.hub.sessions.get(opp.lower())
        if other:
            other.post(["** Player %s has left the game. The game was saved." % self.name])
        await self.send(["** You terminated the game. The game was saved.",
                         "Your clock keeps running here; type 'join %s' to go on." % opp])

    # ---- playing --------------------------------------------------------------------
    def my_track(self):
        tr = self.tracks.get(self.table) if self.table else None
        return tr if tr and tr.started and not tr.hold else None

    async def mov(self, tr, token):
        _, final = await self.core_req("MOV %s %s" % (tr.tid, token))
        return final

    async def cmd_roll(self, args, line):
        tr = self.my_track()
        if not tr:
            await self.send(["** You're not playing."])
            return
        await self.poll_track(tr)
        m = tr.m
        if m.turn != tr.seat:
            await self.send(["** It's not your turn to roll the dice."])
        elif m.phase == "move":
            await self.send(["** You did already roll the dice."])
        elif m.phase != "roll":
            await self.send(["** You can't roll now."])
        else:
            final = await self.mov(tr, "roll")
            if not final.startswith("OK"):
                await self.send(["** %s" % err_text(final)])
            await self.poll_track(tr)

    async def cmd_double(self, args, line):
        tr = self.my_track()
        if not tr:
            await self.send(["** You're not playing."])
            return
        await self.poll_track(tr)
        m = tr.m
        if m.turn != tr.seat:
            await self.send(["** It's not your turn."])
        elif m.phase == "move":
            await self.send(["** You can only double before you roll the dice."])
        elif m.crawford:
            await self.send(["** You can't double in the Crawford game."])
        elif m.owner >= 0 and m.owner != tr.seat:
            await self.send(["** It's not your cube."])
        elif m.phase != "roll":
            await self.send(["** You can't double now."])
        else:
            final = await self.mov(tr, "double")
            if not final.startswith("OK"):
                await self.send(["** %s" % err_text(final)])
            await self.poll_track(tr)

    async def answer(self, yes):
        tr = self.my_track()
        if not tr:
            await self.send(["** You're not playing."])
            return
        await self.poll_track(tr)
        m = tr.m
        if m.turn == tr.seat and m.phase == "take":
            token = "take" if yes else "drop"
        elif m.turn == tr.seat and m.phase == "resign":
            token = "accept" if yes else "reject"
        else:
            await self.send(["** You can't %s now. Nobody doubled or resigned." % ("accept" if yes else "reject")])
            return
        final = await self.mov(tr, token)
        if not final.startswith("OK"):
            await self.send(["** %s" % err_text(final)])
        await self.poll_track(tr)

    async def cmd_accept(self, args, line):
        await self.answer(True)

    async def cmd_reject(self, args, line):
        await self.answer(False)

    async def cmd_resign(self, args, line):
        tr = self.my_track()
        if not tr:
            await self.send(["** You're not playing."])
            return
        level = LEVELS.get(args[0].lower()) if args else None
        if not level:
            await self.send(["** Type 'resign n' (normal), 'resign g' (gammon) or 'resign b' (backgammon)."])
            return
        final = await self.mov(tr, "resign-" + level)
        if not final.startswith("OK"):
            await self.send(["** %s" % err_text(final)])
        await self.poll_track(tr)

    async def cmd_move(self, args, line):
        if not args:
            await self.send(["** move what?"])
            return
        await self.move(args)

    async def move(self, words):
        tr = self.my_track()
        if not tr:
            await self.send(["** You're not playing."])
            return
        await self.poll_track(tr)
        m = tr.m
        if m.turn != tr.seat:
            await self.send(["** It's not your turn to move."])
            return
        if m.phase == "roll":
            await self.send(["** You have to roll the dice before moving."])
            return
        if m.phase != "move":
            await self.send(["** You can't move now."])
            return
        pairs = parse_move(words, tr.seat)
        if pairs is None:
            await self.send(["** Moves are like 'move 13-9 8-7', 'bar-22' or '6-off'."])
            return
        need = m.need
        made, final, error = 0, "OK more", None
        for f, t in pairs:
            if final == "OK done":
                error = "** Please don't give more than %s." % plural(need, "move")
                break
            if final == "OK":
                break           # the game ended
            n, final = await self.step(tr, f, t)
            if n is None:
                error = "** You can't move %s-%s." % (f, t)
                break
            made += n
        if error is None and final == "OK more":
            error = "** You must give %s." % plural(need, "move")
        if error:
            for _ in range(made):
                await self.core_req("UND %s" % tr.tid)
            await self.send([error])
            return
        if final == "OK done":
            _, final = await self.core_req("END %s" % tr.tid)
            if not final.startswith("OK"):
                await self.send(["** %s" % err_text(final)])
        await self.poll_track(tr)

    async def step(self, tr, f, t, depth=0):
        """One checker from f to t: one step, or several steps of the dice
        through free points (8-2 with 3-3). Returns (steps made, last reply)
        or (None, reply) with nothing left made."""
        final = await self.mov(tr, "%s-%s" % (f, t))
        if final.startswith("OK"):
            return 1, final
        if depth >= 3:
            return None, final
        data, _ = await self.core_req("VUE %s M" % tr.tid)
        moves = [l.split()[1] for l in data if l.startswith("M ") and "-" in l]
        down = tr.seat == 0
        start = (25 if down else 0) if f == "bar" else f
        end = (0 if down else 25) if t == "off" else t
        for mv in moves:
            a, b = mv.split("-")
            if a != str(f) or b == "off":
                continue
            mid = int(b)
            if not ((end < mid < start) if down else (start < mid < end)):
                continue
            first = await self.mov(tr, mv)
            if not first.startswith("OK"):
                continue
            if first == "OK":
                return 1, first
            n, last = await self.step(tr, mid, t, depth + 1)
            if n is not None:
                return n + 1, last
            await self.core_req("UND %s" % tr.tid)
        return None, final

    async def cmd_board(self, args, line):
        tid = self.table if self.table and self.tracks[self.table].started else self.watching
        tr = self.tracks.get(tid) if tid else None
        if not tr:
            await self.send(["** You're not playing."])
            return
        await self.poll_track(tr)
        await self.fresh(tr)
        await self.send(self.board_lines(tr, tr.m))

    async def cmd_pip(self, args, line):
        tid = self.table or self.watching
        tr = self.tracks.get(tid) if tid else None
        if not tr or not tr.started:
            await self.send(["** You're not playing."])
            return
        p = tr.persp
        who = "Your" if tr.seat >= 0 else "%s's" % tr.names[p]
        await self.send(["%s pip count is %d. %s's pip count is %d." % (who, tr.m.pips(p), tr.names[1 - p],
                                                                       tr.m.pips(1 - p))])

    async def cmd_watch(self, args, line):
        if not args:
            await self.send(["** watch whom?"])
            return
        await self.hub.poll(self)
        t = self.hub.table_with(args[0])
        if not t:
            await self.send(["** %s is not playing." % args[0]])
            return
        if self.tracks.get(t["id"]) and self.tracks[t["id"]].seat >= 0:
            await self.send(["** You're playing that game."])
            return
        await self.stop_watching()
        _, final = await self.core_req("SEE %s" % t["id"])
        if not final.startswith("OK"):
            await self.send(["** Can't watch %s: %s" % (args[0], err_text(final))])
            return
        persp = [n.lower() for n in t["seated"]].index(args[0].lower())
        tr = self.track(t["id"], -1, persp)
        tr.names = list(t["seated"][:2])
        tr.started = True
        await self.catch_up(tr)
        await self.send(["You're now watching %s." % tr.names[persp]])

    async def stop_watching(self):
        tr = self.tracks.get(self.watching) if self.watching else None
        if tr:
            self.untrack(tr.tid)
            if tr.tid != self.table:
                await self.core_req("LVE %s" % tr.tid)
            return tr
        return None

    async def cmd_unwatch(self, args, line):
        tr = await self.stop_watching()
        await self.send(["You stop watching %s." % tr.names[tr.persp]] if tr else ["** You're not watching."])

    async def cmd_look(self, args, line):
        if not args:
            await self.send(["** look at whom?"])
            return
        await self.hub.poll(self)
        t = self.hub.table_with(args[0])
        if not t:
            await self.send(["** %s is not playing." % args[0]])
            return
        mine = self.tracks.get(t["id"])
        if mine:
            await self.fresh(mine)
            await self.send(self.board_lines(mine, mine.m, force=True))
            return
        await self.core_req("SEE %s" % t["id"])
        persp = [n.lower() for n in t["seated"]].index(args[0].lower())
        tr = Track(t["id"], -1, persp)
        tr.names = list(t["seated"][:2])
        await self.catch_up(tr)
        await self.core_req("LVE %s" % t["id"])
        await self.send(self.board_lines(tr, tr.m))

    # ---- game events --------------------------------------------------------------
    async def view(self, tr):
        data, final = await self.core_req("VUE %s" % tr.tid)
        return parse_backgammon_view(data) if final.startswith("OK") else None

    async def fresh(self, tr):
        """Take the core's view when it shows exactly what the events said."""
        if tr.pending:
            return
        v = await self.view(tr)
        if v and v["seq"] == tr.cursor:
            tr.m.from_view(v)

    async def catch_up(self, tr):
        """Start following a table: names from its events, the position from
        the view, events from now on."""
        data, final = await self.core_req("SIN %s 0" % tr.tid)
        for l in data:
            p = l.split()
            if len(p) >= 6 and p[0] == "EVT" and p[3] == "JOIN" and p[4] in ("0", "1"):
                tr.names[int(p[4])] = p[5]
            if len(p) >= 4 and p[0] == "EVT" and p[3] == "START":
                tr.started = True
            c = parse_client(l.split(" ", 3)[3]) if len(p) >= 5 and p[0] == "EVT" and p[3] == "CLIENT" else None
            if c:
                tr.clients[c[0]] = c[1]
        v = await self.view(tr)
        if v:
            tr.cursor = v["seq"]
            if tr.started:
                tr.m.from_view(v)
                tr.m.use_cube = tr.m.cube > 1 or any(tr.m.ca) or tr.m.crawford
                tr.length = tr.m.match

    async def poll_track(self, tr):
        while True:
            async with self.ev_lock:
                if self.tracks.get(tr.tid) is not tr or tr.hold:
                    return
                data, final = await self.core_req("SIN %s %d" % (tr.tid, tr.cursor))
                if final.startswith("ERR"):
                    self.untrack(tr.tid)
                    return
                await self.handle_events(tr, data)
                roll = tr.autoroll
                tr.autoroll = False
            if not roll:
                return
            await self.mov(tr, "roll")

    def board_lines(self, tr, m):
        me = tr.seat >= 0
        if self.mode != "clip" and self.sets["boardstyle"] in ("1", "2"):
            return text_board(m, me, tr.persp, tr.names)
        return [board_line(m, me, tr.persp, tr.names)]

    def turn_prompt(self, tr):
        """What the player is asked at the start of their turn (or after a resume)."""
        m = tr.m
        if tr.seat < 0 or m.over or m.turn != tr.seat:
            return []
        if m.phase == "roll":
            if self.toggles["double"]:
                return ["It's your turn. Please roll or double"]
            tr.autoroll = True
            return []
        if m.phase == "move" and m.need:
            return ["Please move %s." % plural(m.need, "piece")]
        if m.phase == "take":
            return ["%s doubles. Type 'accept' or 'reject'." % tr.names[1 - tr.seat]]
        if m.phase == "resign" and m.offer:
            return ["%s wants to resign. You will win %s. Type 'accept' or 'reject'."
                    % (tr.names[m.offer[0]], plural(m.offer[1], "point"))]
        return []

    async def handle_events(self, tr, lines):
        evs = []
        for l in lines:
            p = l.split()
            if len(p) < 4 or p[0] != "EVT" or p[1] != tr.tid or int(p[2]) <= tr.cursor:
                continue
            evs.append((int(p[2]), p[3], p[4:], l.split(" ", 4)[4] if len(p) > 4 else ""))
        out = []            # text lines and ("board", Model) items
        m = tr.m
        boards = self.toggles["autoboard"] or self.toggles["moreboards"]
        ended = None
        for i, (seq, kind, a, rest) in enumerate(evs):
            nxt = evs[i + 1][1:3] if i + 1 < len(evs) else (None, [])
            tr.cursor = seq
            if kind != "TURN":
                m.keep = False
            if kind == "JOIN" and len(a) >= 2 and a[0] in ("0", "1"):
                tr.names[int(a[0])] = a[1]
            elif kind == "CLIENT":
                c = parse_client("CLIENT " + rest)
                if c and c[0] in (0, 1):
                    known = tr.clients.get(c[0])
                    tr.clients[c[0]] = c[1]
                    if tr.started and c[0] != tr.seat:      # playing on from another program
                        out += client_line(tr.names[c[0]], c[1], None if known else "plays")
            elif kind == "START":
                await self.on_start(tr, out)
            elif kind == "CHT" and len(a) >= 2:
                self.on_chat(tr, a[0], rest.split(" ", 1)[1] if " " in rest else "")
            elif kind == "GAME":
                m.game, m.crawford = int(a[0]), "crawford" in a[1:]
                m.new_game()
                if m.crawford:
                    out.append("** This is the Crawford game: no doubling.")
            elif kind == "OPEN":
                d = (int(a[0]), int(a[1]))
                p = tr.persp
                tr.opening = "open"
                if tr.seat >= 0:
                    out += ["Starting a new game with %s." % tr.names[1 - p],
                            "You rolled %d, %s rolled %d" % (d[p], tr.names[1 - p], d[1 - p])]
                else:
                    out += ["%s rolled %d, %s rolled %d" % (tr.names[p], d[p], tr.names[1 - p], d[1 - p])]
            elif kind == "ROLL":
                s, d = int(a[0]), (int(a[1]), int(a[2]))
                m.turn, m.rolled, m.phase = s, d, "move"
                if tr.opening == "open":
                    tr.opening = "roll"
                    out.append("It's your turn to move." if s == tr.seat else "%s makes the first move." % tr.names[s])
                elif s == tr.seat:
                    out.append("You roll %d and %d." % d)
                else:
                    out.append("%s rolls %d and %d." % (tr.names[s], d[0], d[1]))
                if boards:
                    out.append(("board", m.copy()))
            elif kind == "STEPS":
                m.need = int(a[1])
                if tr.opening == "roll":
                    tr.opening = None
                elif int(a[0]) == tr.seat:
                    out.append("Please move %s." % plural(m.need, "piece"))
            elif kind == "PASS":
                s = int(a[0])
                m.need = 0
                out.append("You can't move." if s == tr.seat else "%s can't move." % tr.names[s])
            elif kind == "STEP":
                tr.pending.append((int(a[0]), a[1], a[2], len(a) > 3 and a[3] == "HIT"))
            elif kind == "UNDO":
                s = int(a[0])
                for k in range(len(tr.pending) - 1, -1, -1):
                    if tr.pending[k][0] == s:
                        del tr.pending[k]
                        break
            elif kind == "TURN":
                self.flush(tr, out)
                s = int(a[0])
                m.turn = s
                if not m.keep:
                    m.phase, m.rolled, m.need = "roll", None, 0
                m.keep = False
                if s == tr.seat and m.phase == "roll" and not (nxt[0] == "ROLL" and nxt[1][:1] == [str(s)]):
                    out += self.turn_prompt(tr)
            elif kind == "DBL":
                s, v = int(a[0]), int(a[1])
                m.phase, m.keep = "take", True
                if s == tr.seat:
                    out.append("You double. Please wait for %s to accept or reject." % tr.names[1 - s])
                elif tr.seat >= 0:
                    out.append("%s doubles. Type 'accept' or 'reject'." % tr.names[s])
                else:
                    out.append("%s doubles." % tr.names[s])
                if boards:
                    out.append(("board", m.copy()))
            elif kind == "TAKE":
                s, v = int(a[0]), int(a[1])
                m.cube, m.owner = v, s
                m.ca = [m.use_cube and v < 64 and k == s for k in (0, 1)]
                out.append("%s the double. The cube shows %d." % ("You accept" if s == tr.seat else
                                                                   "%s accepts" % tr.names[s], v))
                if boards:
                    out.append(("board", m.copy()))
            elif kind == "DROP":
                s = int(a[0])
                pts = plural(m.cube, "point")
                if s == tr.seat:
                    out.append("You give up. %s wins %s." % (tr.names[1 - s], pts))
                else:
                    out.append("%s gives up. %s %s." % (tr.names[s], "You win" if tr.seat >= 0 else
                                                        "%s wins" % tr.names[1 - s], pts))
            elif kind == "OFFER":
                s, pts, k = int(a[0]), int(a[1]), a[2]
                before = m.phase if m.phase in ("roll", "move") else "move"
                m.offer, m.phase, m.keep = (s, pts, k, before), "resign", True
                if s == tr.seat:
                    out.append("You want to resign. %s will win %s." % (tr.names[1 - s], plural(pts, "point")))
                elif tr.seat >= 0:
                    out.append("%s wants to resign. You will win %s. Type 'accept' or 'reject'."
                               % (tr.names[s], plural(pts, "point")))
                else:
                    out.append("%s wants to resign. %s will win %s." % (tr.names[s], tr.names[1 - s],
                                                                       plural(pts, "point")))
            elif kind == "ACCEPT":
                s = int(a[0])
                pts = plural(m.offer[1], "point") if m.offer else "the points"
                out.append("You accept and win %s." % pts if s == tr.seat else
                           "%s accepts and wins %s." % (tr.names[s], pts))
                m.offer = None
            elif kind == "REJECT":
                s = int(a[0])
                if m.offer:
                    m.phase = m.offer[3]
                m.offer, m.keep = None, True
                out.append("You reject. The game continues." if s == tr.seat else
                           "%s rejects. The game continues." % tr.names[s])
                if s != tr.seat and tr.seat >= 0 and nxt[0] != "TURN":
                    out += self.turn_prompt(tr)
            elif kind == "WON":
                self.flush(tr, out)
                s, pts = int(a[0]), int(a[1])
                m.over = True
                out += self.win_lines(tr, s, pts)
            elif kind == "SCORE":
                m.score = [int(a[0]), int(a[1])]
                p = tr.persp
                out.append("score in %d point match: %s-%d %s-%d" % (
                    m.match or tr.length, self.name if tr.seat >= 0 else tr.names[p], m.score[p],
                    tr.names[1 - p], m.score[1 - p]))
                if nxt[0] == "GAME" and tr.seat >= 0:
                    out.append("Type 'join' if you want to play the next game, type 'leave' if you don't.")
                    tr.hold = True
                    break
            elif kind == "END":
                self.flush(tr, out)
                ended = a
                out += self.end_lines(tr, a)
                m.over = True
                m.turn = -1
                out.append(("board", m.copy()))
                break
            elif kind == "UNINVITE" and tr.created and not tr.started and tr.seat >= 0:
                who, why = uninvite_of(" ".join([kind] + a))
                if why == "declined":
                    out.append("** %s declines your invitation." % who)
                elif why == "expired":
                    out.append("** Your invitation to %s has expired." % who)
            elif kind in ("TURN", "LEAVE", "INVITE", "UNINVITE"):
                pass
        if ended is None and not tr.hold and out:
            await self.fresh(tr)
            for k in range(len(out) - 1, -1, -1):
                if isinstance(out[k], tuple):
                    out[k] = ("board", tr.m.copy())
                    break
        lines = []
        for item in out:
            if isinstance(item, tuple):
                lines += self.board_lines(tr, item[1])
            else:
                lines.append(item)
        if ended is not None:
            self.finished(tr)
        if lines:
            self.post(lines)

    def flush(self, tr, out):
        """A turn is over: its steps count now (they were tentative before)."""
        if not tr.pending:
            return
        steps = tr.pending
        tr.pending = []
        for s, f, t, hit in steps:
            tr.m.step(s, f, t, hit)
        s = steps[0][0]
        if s != tr.seat:
            out.append("%s moves %s ." % (tr.names[s], " ".join("%s-%s" % (f, t) for _, f, t, _ in steps)))
        if self.toggles["autoboard"]:
            out.append(("board", tr.m.copy()))

    async def on_start(self, tr, out):
        tr.started = True
        v = await self.view(tr)
        m = tr.m
        if v:
            m.match = v["match"]
            m.use_cube = v["cube"] > 1 or bool(v["cube_ok"] and any(v["cube_ok"])) or v["crawford"]
        tr.length = m.match
        m.score, m.game, m.crawford = [0, 0], 1, False
        m.new_game()
        if tr.seat < 0:
            return
        opp = tr.names[1 - tr.seat]
        if tr.created:
            out.append("** Player %s has joined you for %s." % (opp, match_words(tr.length)))
        else:
            out.append("** You are now playing %s with %s" % (match_words(tr.length), opp))
        out += client_line(opp, tr.clients.get(1 - tr.seat), "joined" if tr.created else "plays")

    def on_chat(self, tr, seat, text):
        if seat == "-":
            if tr.seat < 0 and tr.own_cht:
                tr.own_cht -= 1
                return
            self.hear("kibitzes", "watcher", text)
        elif seat.isdigit() and int(seat) != tr.seat:
            self.hear("says" if tr.seat >= 0 else "kibitzes", tr.names[int(seat)], text)

    def win_lines(self, tr, s, pts):
        if s == tr.seat:
            return ["You win the game and get %s. Congratulations!" % plural(pts, "point")]
        return ["%s wins the game and gets %s.%s" % (tr.names[s], plural(pts, "point"),
                                                       " Sorry." if tr.seat >= 0 else "")]

    def end_lines(self, tr, a):
        m = tr.m
        out = []
        if a and a[0] == "win" and len(a) >= 3:
            s = int(a[1])
            if not m.match:
                return self.win_lines(tr, s, int(a[2]))
        elif a and a[0] in ("resign", "timeout") and len(a) >= 2:
            loser = int(a[1])
            s = 1 - loser
            what = "resigned" if a[0] == "resign" else "ran out of time"
            out.append("** %s %s." % ("You" if loser == tr.seat else tr.names[loser], what))
            if not m.match:
                return out + self.win_lines(tr, s, m.cube)
        else:
            return out + ["** The game is over."]
        hi, lo = m.score[s], m.score[1 - s]
        if s == tr.seat:
            out.append("You win the %d point match %d-%d ." % (m.match, hi, lo))
        else:
            out.append("%s wins the %d point match %d-%d ." % (tr.names[s], m.match, hi, lo))
        return out

    def finished(self, tr):
        self.untrack(tr.tid)

    async def poller(self):
        n = 0
        while not self.closing:
            await asyncio.sleep(POLL_SECS)
            n += 1
            if self.mode == "client" and time.monotonic() - self.last_out >= KEEPALIVE_SECS:
                self.post([""], prompt=False)
            try:
                for tr in list(self.tracks.values()):
                    await self.poll_track(tr)
                if n % INVITE_POLLS == 0:
                    await self.check_invitations()
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
            inp = asyncio.ensure_future(self.readline())
            while not self.closing:
                done, _ = await asyncio.wait({inp, task}, return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    break
                line = inp.result()
                words = line.split()
                log(self.sid, "< %s" % loggable(line, "move" if words and is_move_line(words) else
                                                 expand(words[0]) if words else None))
                inp = asyncio.ensure_future(self.readline())
                await self.command(line)
        finally:
            for t in (inp, task):
                if t:
                    t.cancel()
            if self.name and self.hub.sessions.get(self.name.lower()) is self:
                self.hub.leave(self)
            if self.core is not None:
                await self.core.finish()        # the session ends with the connection


# The protocol this extension serves, declared to the core (adapters/declare.py): key, name, description.
PROTOCOL = ("FIBS", "First Internet Backgammon Server (FIBS)", "Backgammon for vintage FIBS clients")


async def serve(args):
    global POLL_SECS
    POLL_SECS = args.poll
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
            print("fibs session error: %r" % e, file=sys.stderr, flush=True)
        finally:
            ticket.release()
            log(s.sid, "disconnect")
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("fibs adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.fibs")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6505)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    declare.add_args(ap)
    ap.add_argument("--prompt-wait", type=float, default=PROMPT_WAIT, help=argparse.SUPPRESS)
    ap.add_argument("--poll", type=float, default=POLL_SECS, help=argparse.SUPPRESS)    # tests
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
