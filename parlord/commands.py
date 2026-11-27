"""The slash commands: one set of commands, with one meaning and one help text each, for every place where a
player types a line that starts with / (docs/chat-commands.md, docs/design.md).

The core runs them for any frontend with the request CMD (docs/protocol.md, Commands), and the chat-style
consoles of the server extensions run them through adapters/chatcmd.py. Both use this module, so there is
one implementation. A command is carried out with the core's own requests (req), the same ones a client
could send itself: through CMD the core sends them on the player's own connection, and a console sends them
on its connection to the core. So a command can do nothing the player could not do directly.

The commands, in the order help lists them:

    /list               your games of this kind (table, opponent, whose move, time left) and open tables
    /resume [table]     a running game back (the first one when no table is given)
    /resign [table]     resign the game played now, or the table named, after asking first (ask)
    /invite NAME        invite a player; /invite bot plays the bot
    /uninvite NAME      withdraw an invitation to the table you are at
    /open               open a table anyone can join
    /join TABLE         sit down at an open table, by its number (from /list); /join NAME sits down at
                        NAME's open table. Neither needs an invitation. When NAME has invited you,
                        /join NAME takes the invitation, as /accept NAME does
    /close              close your open table
    /accept NAME        take NAME's invitation
    /decline NAME       turn NAME's invitation down
    /who [NAME]         the players online; with a name, that player: online or idle, the machine they
                        prefer (their profile) and their tables, as /tables NAME
    /tables NAME        the tables where NAME sits: open ones to join, games being played
    /top                the first five of the leaderboard, and the note that scores are easy to get
    /score [NAME]       your score, or NAME's: points, wins, draws, losses, resignations, rank
    /note [text]        the notes of the game you play now, or a note added to them
    /say TEXT           a line said at the table you are at (CHT)
    /motd               the message of the day
    /time               how long you have been signed in, and the server's time (TIM)
    /signin NAME PASSWORD
                        sign in, where a console signs in through chat
    /quit               sign out (also /bye)
    /help               the commands offered here

A line that starts with / is a command. A few quiet aliases stay for consoles that had them: bye, logout
and exit for quit, and ? for help. Help never shows them.

The interface. Every console builds on it, so it is kept small and stable:

    COMMANDS                        [(name, usage, help)], the whole set in help order
    NOT_HERE                        "Not available here.", the answer to a command a console does not offer
    parse(text)                     -> Command(name, args, text), or None when the line is chat. A slash
                                    word that is no command is Command("unknown", [word], text).
    help_lines(names, notes=(), compact=False, width=0)
                                    the help for the commands named (in help order), then the console's
                                    notes; compact gives one "Commands: ..." line, wrapped to width
    commands_line(names)            "Commands: /list, /resume [table], ..., /help."
    Console(say, req, me, commands, kinds=(), label="", new="standard", notes=(),
            handlers=None, seated=None, width=0, compact=False, current=None)
        await console.handle(text)  -> True when the line was a command (it has been answered), False
                                       when it is chat for the console to pass on
    game_lines(games, label, entering=False), open_lines(tables, me, join=True), clock_text(secs)
                                    the lines /list shows, for consoles that list their own way
    names_with(t, names=None), who_plays(name, say)
                                    a table's players with what each plays with (LST clients)
    time_text(data, final, width=40)
                                    /time's lines from TIM's reply: "Signed in 12:34. Server time 21:05 UTC.",
                                    two lines where one would be wider than width
    note_lines(data, last=0, day=True)
                                    NOTE's lines as a console shows them
    CoreConsole                     the console the core runs for CMD (below)

The hooks a console gives Console:

    say(lines)          shows lines to the player; a plain function or a coroutine function
    req(text)           a coroutine function: one request on the player's core session -> (data, final)
    me                  the player's name, or a function that returns it
    commands            the names offered here (help lists them); any other command gets NOT_HERE
    kinds               the game keys the console plays ("backgammon/1"): /list, /open, /join, /invite
                        and /accept cover these, and /open and /invite make tables of the first
    label               the game's name for /list ("Backgammon"); "" for a console of several games
    new                 what follows the key in NEW for /open and /invite ("standard cube=0")
    notes               extra help lines (a bot that comes after a wait, for example)
    handlers            {name: coroutine function(cmd)}: the console's own way to carry out a command; it
                        replaces the default. A handler for a command not in commands is a quiet extra
                        (for a plain answer such as "Not available here. Use the Invite button.")
    seated(tid, seat, opponent, how)
                        an optional coroutine function, called after a default /open, /join, /invite or
                        /accept has seated the player (how: "open", "join", "invite", "accept"); opponent
                        is None at a table opened for anyone
    width               lines longer than this are wrapped at spaces (0: as they are)
    compact             /help as one "Commands:" line and the notes, for a small window
    current             an optional function: the table of the game the player plays now, or None (/note,
                        /resign, /say, /uninvite)
    list_mark(t, view)  a method a console may override: a few words /list adds after one of my running
                        games (t from LST, view the lines of its VUE), "" for none
    ask(kind, tid, t)   a method a console may override: how it asks before resigning. The default says
                        "Resign table 3? /resign again to confirm.", and a second /resign within
                        RESIGN_SECS, or /resign TABLE yes, confirms it

The defaults, from the core alone: /help, /who (WHO; with a name WHO, PFL, GAM and LST player=NAME),
/tables (GAM, LST player=NAME clients), /top (TOP), /score (PFL), /motd (MOTD), /time (TIM), /list (LST clients, VUE),
/invite (IVL and JON for NAME's own invitation, JON at NAME's open table, else NEW, JON and INV), /open (NEW,
JON), /join (IVL and JON for NAME's invitation, else LST and JON), /close (LST, LVE), /accept (IVL, JON),
/decline (IVL, UNV), /note (NOTE), /say (CHT), /uninvite (UNV). When /join, /accept or /invite seats the player
with someone, the line says what they play with (LST clients, LST table=N clients), /resign (LST, RES: one of my running games of the console's kinds). /resume,
/signin and /quit have no default: a console that offers them gives a handler. After /resign the table's
END comes to the console as any game's end does.
"""
import inspect
import re
import time
from collections import namedtuple

from . import bots
from .replies import (bot_waits, err_text, parse_games, parse_invites, parse_players, parse_profile, parse_tables, parse_tim,
                      parse_top, pending_invites)

# The message of the day as the text frontends show it: between a top and a bottom rule, 39 columns wide
# like the message itself, so it is clear where it ends and the server's own instructions begin.
MOTD_TOP = "========= Message of the Day =========="
MOTD_BOTTOM = "==== End of the Message of the Day ===="


def framed_motd(lines):
    """The message of the day between its two rules, or nothing when there is none."""
    return [MOTD_TOP] + list(lines) + [MOTD_BOTTOM] if lines else []


# What every leaderboard view says, in short (docs/protocol.md, Scores).
SCORES_NOTE = ("Any score is easy to get on a public server. The leaderboard is meant for conferences, "
               "festivals and LAN parties.")


def score_text(s):
    """A score in words: "12 points: 3 wins, 1 draw, 2 losses, 1 resigned"."""
    def n(k, one, many):
        return "%d %s" % (s[k], one if s[k] == 1 else many)
    return "%s: %s, %s, %s, %d resigned" % (n("points", "point", "points"), n("wins", "win", "wins"),
                                            n("draws", "draw", "draws"), n("losses", "loss", "losses"),
                                            s["resigns"])


NOT_HERE = "Not available here."
NO_SCORES = "This server shows no scores."
TOP_ROWS = 5                # /top: the first five
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")

# (name, usage, help): one meaning and one help text each, short enough for a narrow chat box
COMMANDS = [
    ("list", "/list", "your games and open tables"),
    ("resume", "/resume [table]", "bring a game back"),
    ("resign", "/resign", "resign a game (asks first)"),
    ("invite", "/invite NAME", "invite a player (bot too)"),
    ("uninvite", "/uninvite NAME", "withdraw an invitation"),
    ("open", "/open", "open a table anyone can join"),
    ("join", "/join TABLE or NAME", "take an open seat"),
    ("close", "/close", "close your open table"),
    ("accept", "/accept NAME", "take NAME's invitation"),
    ("decline", "/decline NAME", "refuse NAME's invitation"),
    ("who", "/who [NAME]", "players online, or one"),
    ("tables", "/tables NAME", "NAME's tables"),
    ("top", "/top", "the leaderboard"),
    ("score", "/score [NAME]", "your score, or NAME's"),
    ("note", "/note [text]", "read or add a game note"),
    ("say", "/say TEXT", "say a line at the table"),
    ("motd", "/motd", "the message of the day"),
    ("time", "/time", "time signed in, server time"),
    ("signin", "/signin NAME PASSWORD", "sign in"),
    ("quit", "/quit", "sign out (also /bye)"),
    ("help", "/help", "these commands"),
]
NAMES = [c[0] for c in COMMANDS]
USAGE = {c[0]: c[1] for c in COMMANDS}
ALIASES = {"bye": "quit", "logout": "quit", "exit": "quit", "?": "help"}
# The commands that need a name, or for /join a table number
NEEDS_NAME = ("invite", "uninvite", "join", "accept", "decline", "signin", "tables")

RESIGN_SECS = 30            # a second /resign within this long confirms the first
IDLE_SECS = 600             # /who NAME says idle after this long without activity (WHO's idle=)
TABLE_RE = re.compile(r"^[A-Za-z0-9]{1,12}\Z")

Command = namedtuple("Command", "name args text")


def parse(text):
    """A typed line -> Command, or None when it is chat."""
    line = (text or "").strip()
    if not line.startswith("/"):
        return None
    words = line[1:].split()
    if not words:
        return Command("unknown", [line], text)
    verb, args = ALIASES.get(words[0].lower(), words[0].lower()), words[1:]
    if verb in NAMES:
        return Command(verb, args, text)
    return Command("unknown", [words[0]], text)


def help_lines(names, notes=(), compact=False, width=0):
    """The help for the commands named, in help order, then the notes."""
    if compact:
        return wrap([commands_line(names)], width) + wrap(list(notes), width)
    return ["%s: %s" % (usage, what) for name, usage, what in COMMANDS if name in names] + list(notes)


def commands_line(names):
    return "Commands: %s." % ", ".join(usage for name, usage, _ in COMMANDS if name in names)


def sentence(text):
    """A reason from the core or a game ("that point is taken; try another") as sentences: "That point is
    taken. Try another." Each part after a colon or a semicolon becomes a sentence of its own."""
    parts = [p.strip() for p in re.split(r"[:;] ", (text or "").strip()) if p.strip()]
    return " ".join(p[:1].upper() + p[1:] + ("" if p.endswith((".", "?", "!")) else ".") for p in parts)


def wrap(lines, width):
    """Lines longer than width split at spaces (width 0: as they are)."""
    if not width:
        return list(lines)
    out = []
    for line in lines:
        while len(line) > width:
            cut = line.rfind(" ", 0, width + 1)
            cut = cut if cut > 0 else width
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        out.append(line)
    return out


def clock_text(secs):
    """Time left on a turn clock, short: 2d 3h, 5h 10m, 12m, 40s."""
    if secs is None:
        return ""
    d, h, m = secs // 86400, secs % 86400 // 3600, secs % 3600 // 60
    return "%dd %dh" % (d, h) if d else "%dh %dm" % (h, m) if h else "%dm" % m if m else "%ds" % secs


def time_text(data, final, width=40):
    """/time's lines from TIM's reply (data lines, final line): "Signed in 12:34. Server time 21:05 UTC." The
    time signed in is m:ss, or h:mm:ss from an hour on, and the server's time is UTC, hours and minutes. Where
    the line would be wider than width (from an hour on, on a 40-column screen) it is two lines. TIM counts
    from signing in and answers 0 before it, so with 0 seconds the line says the server's time alone. When the
    connection has time limits, a sentence says each: "This call has 63 minutes left." from TIM's call=, "You
    have 40 minutes left this month." from month=, and "The parlor's month has 10 minutes left." when the
    pool's time is the shortest (left= below both). Each is wrapped to width."""
    if final.startswith("ERR UNKNOWN"):
        return ["This server does not tell the time."]
    if not final.startswith("OK"):
        return [err_text(final)]
    tim = parse_tim(data)
    if tim is None:
        return ["The server's time is not known."]
    secs, now, left, call, month = tim
    h, m, s = secs // 3600, secs % 3600 // 60, secs % 60
    server = "Server time %s UTC." % now[11:16]
    lines = ["Signed in %s." % ("%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)), server] if secs else [server]
    one = " ".join(lines)
    out = [one] if len(one) <= width else lines
    mins = lambda n: "less than a minute" if n < 60 else "1 minute" if n < 120 else "%d minutes" % (n // 60)  # noqa: E731
    said = []
    if call is not None:
        said.append("This call has %s left." % mins(call))
    if month is not None:
        said.append("You have %s left this month." % mins(month))
    if left is not None and all(left < n for n in (call, month) if n is not None):
        said.append("The parlor's month has %s left." % mins(left))
    return out + wrap(said, width)


def game_lines(games, label, entering=False):
    """/list's lines for my running games: [(table, seat, opponent, whose turn (seat), seconds left)], and
    optionally the name of the player to move as a sixth item (a table of more than two) and a few words
    about the table as a seventh (list_mark). label: the game's name ("Backgammon"), or "" for several
    games."""
    kind = "%s " % label if label else ""
    if not games:
        return [] if entering else ["You have no %sgames in progress." % kind]
    out = ["Your %sgames in progress:" % kind]
    for g in games:
        tid, seat, opp, turn, left = g[:5]
        mover = g[5] if len(g) > 5 and g[5] else opp
        mark = g[6] if len(g) > 6 else ""
        who = "your move" if turn == seat else "%s's move" % mover if turn >= 0 else "over"
        out.append("Table %s with %s: %s%s%s." % (tid, opp, who, ", %s left" % clock_text(left) if left else "",
                                                  ", " + mark if mark else ""))
    return out


def who_plays(name, say):
    """'ann, who plays with GameMaster 1.0.0 on a Macintosh Plus', or the name alone when nothing is known."""
    return "%s, who plays %s" % (name, say) if say else name


def names_with(t, names=None):
    """A table's names (parse_tables) with what each plays with, where LST clients said it: 'bob (with
    Netscape Navigator 3.0 on a Macintosh) and cy'."""
    clients = t.get("clients") or {}
    out = []
    for n in names if names is not None else t["names"]:
        say = clients.get(t["seat_of"].get(n, -1), "")
        out.append("%s (%s)" % (n, say) if say else n)
    return " and ".join(out)


def open_lines(tables, me, join=True):
    """/list's lines for the public tables where someone waits for a player (not at my own). join: the
    console has /join, and each line says how to sit down there; else it says to invite the player."""
    out = []
    for t in tables:
        if (t["status"] != "open" or not t["names"] or t.get("vis") == "private" or t["filled"] >= t["seats"]
                or me.lower() in [n.lower() for n in t["names"]]):
            continue
        who = names_with(t)
        out.append("Table %s: %s %s for a player. %s"
                   % (t["id"], who, "waits" if len(t["names"]) == 1 else "wait",
                      "Type /join %s to sit down." % t["id"] if join else "Invite %s to play there." % t["names"][0]))
    return (["Open tables:"] + out) if out else ["No open tables are waiting for a player."]


def note_lines(data, last=0, day=True):
    """NOTE lines (the reply to NOTE table) -> lines to show, the last few only when last is given: the
    core's own as they are, a player's as "name: text"; each starts with its day ("Nov 22") unless day is
    False (a narrow chat box)."""
    out = []
    for l in data:
        p = l.split(" ", 3)
        if len(p) == 4 and p[0] == "NOTE":
            try:
                when = time.strftime("%b %d", time.strptime(p[1], "%Y-%m-%d")).replace(" 0", " ")
            except ValueError:
                when = p[1]
            text = p[3] if p[2] == "-" else "%s: %s" % (p[2], p[3])
            out.append("%s %s" % (when, text) if day else text)
    return out[-last:] if last else out


class Console:
    """One chat-style console's commands (module docstring: the hooks)."""

    def __init__(self, say, req, me, commands, kinds=(), label="", new="standard", notes=(),
                 handlers=None, seated=None, width=0, compact=False, current=None):
        self.say_hook, self.req, self.me_hook = say, req, me
        self.current = current
        self.commands = list(commands)
        self.kinds, self.label, self.new = list(kinds), label, new
        self.notes, self.width, self.compact = list(notes), width, compact
        self.handlers = dict(handlers or {})
        self.seated = seated
        self.resign_ask = None      # (table, when): the /resign waiting for its confirmation

    @property
    def me(self):
        return self.me_hook() if callable(self.me_hook) else self.me_hook

    async def say(self, lines):
        if isinstance(lines, str):
            lines = [lines]
        got = self.say_hook(wrap(lines, self.width))
        if inspect.isawaitable(got):
            await got

    async def handle(self, text):
        """A typed line: True when it was a command (answered), False when it is chat."""
        cmd = parse(text)
        if cmd is None:
            return False
        if cmd.name == "unknown":
            await self.say("No such command: %s. Type /help for the commands." % ("/" + cmd.args[0].lstrip("/")))
            return True
        fn = self.handlers.get(cmd.name)
        if fn is None and cmd.name in self.commands:
            fn = getattr(self, "do_" + cmd.name, None)
        if fn is None:
            await self.say(NOT_HERE)
            return True
        if cmd.name in NEEDS_NAME and (not cmd.args or not NAME_RE.match(cmd.args[0])):
            await self.say("Type %s." % USAGE[cmd.name].replace("TABLE or NAME", "and a table or a name"))
            return True
        await fn(cmd)
        return True

    # ---- the defaults ------------------------------------------------------------------------------
    async def do_help(self, cmd):
        await self.say(help_lines(self.commands, self.notes, self.compact, self.width))

    async def do_who(self, cmd):
        if cmd.args:
            await self.who_one(cmd.args[0])
            return
        data, final = await self.req("WHO")
        names = bots.listed(parse_players(data)) if final.startswith("OK") else []
        await self.say("Online: " + (", ".join(names) or "nobody") + ".")

    async def who_one(self, name):
        """/who NAME: one line on the player (online, idle or not online; the machine they prefer), then their
        tables as /tables gives them."""
        if not NAME_RE.match(name):
            await self.say("Type /who or /who NAME.")
            return
        data, final = await self.req("PFL %s" % name)
        if not final.startswith("OK"):
            await self.say("No player called %s." % name if final.startswith("ERR NOPLAYER") else err_text(final))
            return
        prof = parse_profile(data)
        name = prof["name"] or name
        data, final = await self.req("WHO")
        idle = next((p[2] for p in (l.split() for l in data) if len(p) >= 3 and p[0] == "PLR"
                     and p[1].lower() == name.lower()), None) if final.startswith("OK") else None
        secs = int(idle[5:]) if idle and idle[5:].isdigit() else 0
        state = ("not online" if idle is None else "online" if secs < IDLE_SECS
                 else "idle %s" % clock_text(secs - secs % 60))
        await self.say(["%s: %s%s." % (name, state, ", plays on %s" % prof["pref"] if prof["pref"] else "")]
                       + await self.player_tables(name))

    async def do_tables(self, cmd):
        await self.say(await self.player_tables(cmd.args[0]))

    async def player_tables(self, name):
        """The lines for the tables where name sits (LST player=name): open ones first, then games."""
        data, final = await self.req("LST player=%s clients" % name)
        if not final.startswith("OK"):
            return [err_text(final)]
        more = next((int(f[1]) for f in (l.split() for l in data) if len(f) == 2 and f[0] == "MORE"
                     and f[1].isdigit()), 0)
        tables = sorted(parse_tables(data), key=lambda t: (t["status"] != "open", int(t["id"])))
        if not tables:
            return ["%s sits at no table." % name]
        gdata, gfinal = await self.req("GAM")
        games = {g["key"]: g["name"] for g in parse_games(gdata)} if gfinal.startswith("OK") else {}
        join = "join" in self.commands or "join" in self.handlers
        out = []
        for t in tables:
            what = games.get(t["key"], t["key"].split("/")[0])
            who = names_with(t)
            one = len(t["names"]) == 1
            if t["status"] == "open":
                free = (join and t["key"] in self.kinds and t["filled"] < t["seats"]
                        and self.me.lower() not in [n.lower() for n in t["names"]])
                out.append("Table %s, %s: %s %s.%s" % (t["id"], what, who, "waits" if one else "wait",
                                                        " /join %s" % t["id"] if free else ""))
            else:
                out.append("Table %s, %s: %s %s playing." % (t["id"], what, who, "is" if one else "are"))
        return out + (["And %d more." % more] if more else [])

    async def do_top(self, cmd):
        """/top: the first TOP_ROWS of the leaderboard on one line (TOP), then the note that scores are easy
        to get (SCORES_NOTE)."""
        data, final = await self.req("TOP %d" % TOP_ROWS)
        if not final.startswith("OK"):
            await self.say(NO_SCORES if final.startswith(("ERR NOSCORES", "ERR UNKNOWN")) else err_text(final))
            return
        rows = parse_top(data)
        await self.say(["Top: %s." % ", ".join("%d %s %d" % (r["rank"], r["name"], r["points"]) for r in rows)
                        if rows else "No scores yet.", SCORES_NOTE])

    async def do_score(self, cmd):
        """/score [NAME]: a player's score (PFL SCORE), your own without a name."""
        name = cmd.args[0] if cmd.args else self.me
        if not NAME_RE.match(name or ""):
            await self.say("Type /score or /score NAME.")
            return
        data, final = await self.req("PFL %s" % name)
        if not final.startswith("OK"):
            await self.say("No player called %s." % name if final.startswith("ERR NOPLAYER") else err_text(final))
            return
        prof = parse_profile(data)
        sc = prof["score"]
        if sc is None:
            await self.say(NO_SCORES)
            return
        await self.say("%s: %s%s." % (prof["name"] or name, score_text(sc),
                                      ", number %d" % sc["rank"] if sc["rank"] else ""))

    async def do_motd(self, cmd):
        data, final = await self.req("MOTD")
        lines = [l[5:] for l in data if l.startswith("MOTD")] if final.startswith("OK") else []
        await self.say(framed_motd(lines) or ["There is no message of the day."])

    async def do_time(self, cmd):
        """/time: TIM, as time_text says it."""
        await self.say(time_text(*await self.req("TIM")))

    async def tables(self):
        data, final = await self.req("LST game=%s clients" % self.kinds[0] if len(self.kinds) == 1 else "LST clients")
        return [t for t in parse_tables(data) if t["key"] in self.kinds] if final.startswith("OK") else []

    async def do_list(self, cmd):
        me = self.me.lower()
        tables = await self.tables()
        games, mine_open = [], []
        for t in sorted(tables, key=lambda t: int(t["id"]) if t["id"].isdigit() else 0):
            low = [n.lower() for n in t["seated"]]
            if me not in low or t["status"] not in ("open", "playing"):
                continue
            seat = low.index(me)
            if t["status"] == "open":
                mine_open.append("Your table %s is open." % t["id"])
                continue
            data, final = await self.req("VUE %s" % t["id"])
            turn, left = -1, None
            for l in data if final.startswith("OK") else []:
                f = l.split()
                if len(f) >= 3 and f[0] == "CLK" and f[1].isdigit():
                    turn, left = int(f[1]), int(f[2])
            others = [n for i, n in enumerate(t["seated"]) if i != seat and n != "-"]
            mover = t["seated"][turn] if 0 <= turn < len(t["seated"]) else ""
            games.append((t["id"], seat, " and ".join(others) or "nobody", turn, left, mover,
                          self.list_mark(t, data if final.startswith("OK") else [])))
        await self.say(game_lines(games, self.label) + mine_open
                       + open_lines(tables, self.me, "join" in self.commands or "join" in self.handlers))

    def list_mark(self, t, view):
        """A few words /list adds after one of my running games ("" for none); a console may override it."""
        return ""

    async def do_note(self, cmd):
        """/note: the notes of the game the player plays now; /note text adds one (NOTE)."""
        tid = self.current() if self.current else None
        if not tid:
            await self.say("Notes belong to a game: start or /resume one first.")
            return
        text = cmd.text.strip().split(None, 1)
        if len(text) > 1:
            _, final = await self.req("NOTE %s %s" % (tid, text[1].strip()))
            await self.say("Noted at table %s." % tid if final.startswith("OK")
                           else "Can't add that note: %s" % err_text(final))
            return
        data, final = await self.req("NOTE %s" % tid)
        lines = note_lines(data) if final.startswith("OK") else []
        await self.say(lines or "Table %s has no notes." % tid)

    async def do_resign(self, cmd):
        """/resign [table] [yes]: the game the player plays now (current), or the table named, once confirmed
        (ask, sure) (RES). Only a running game of mine of this console's kinds."""
        args = [a.lstrip("#") for a in cmd.args[:2]]
        yes = bool(args) and args[-1].lower() == "yes"
        if yes:
            args = args[:-1]
        sure = self.sure(yes)
        now = time.monotonic()
        asked = self.resign_ask if self.resign_ask and now - self.resign_ask[1] <= RESIGN_SECS else None
        tid = args[0] if args else asked[0] if asked else self.current() if self.current else None
        if not tid or not TABLE_RE.match(tid):
            await self.say("Type /resign TABLE (its number in /list).")
            return
        me = self.me.lower()
        t = next((t for t in await self.tables() if t["id"] == tid), None)
        if t is None or t["status"] != "playing" or me not in [n.lower() for n in t["seated"]]:
            self.resign_ask = None
            await self.say("Table %s is not a running game of yours." % tid)
            return
        if not sure and not (asked and asked[0] == tid):
            self.resign_ask = (tid, now)
            await self.ask("resign", tid, t)
            return
        self.resign_ask = None
        _, final = await self.req("RES %s" % tid)
        await self.say("You resigned table %s." % tid if final.startswith("OK")
                       else "Can't resign table %s: %s" % (tid, err_text(final)))

    def sure(self, yes):
        """Is the action confirmed already? In chat, /resign TABLE yes confirms at once."""
        return yes

    async def ask(self, kind, tid, t):
        """Ask before resigning (kind "resign") at table tid (t from LST). In chat, a second /resign within
        RESIGN_SECS confirms it (do_resign)."""
        await self.say("Resign table %s? /resign again to confirm." % tid)

    async def do_say(self, cmd):
        """/say TEXT: a line said at the table the player is at (CHT). It comes back as the table's CHT event,
        so nothing more is said here."""
        tid = self.current() if self.current else None
        text = cmd.text.strip().split(None, 1)
        if not tid:
            await self.say("You can say a line only at a table.")
            return
        if len(text) < 2:
            await self.say("Type /say and what you want to say.")
            return
        _, final = await self.req("CHT %s %s" % (tid, text[1].strip()))
        if not final.startswith("OK"):
            await self.say("Your line was not said at table %s. %s" % (tid, sentence(err_text(final))))

    async def do_uninvite(self, cmd):
        """/uninvite NAME: withdraw NAME's invitation to the table the player is at (UNV)."""
        tid = self.current() if self.current else None
        name = cmd.args[0]
        if not tid:
            await self.say("You can withdraw an invitation only at your table.")
            return
        _, final = await self.req("UNV %s %s" % (tid, name))
        if final.startswith("OK"):
            await self.say("You withdrew %s's invitation." % name)
        elif final.startswith("ERR NOINVITE"):
            await self.say("%s is not invited to table %s." % (name, tid))
        elif final.startswith("ERR NOTSEATED"):
            await self.say("Only a player seated at table %s can withdraw an invitation." % tid)
        else:
            await self.say("The invitation was not withdrawn. %s" % sentence(err_text(final)))

    def waiting_table(self, tables, name=None, tid=None):
        """An open public table with a free seat, where NAME waits or numbered tid, not one of mine. By name it
        never finds one of the bot's open tables (docs/bot.md, Open tables): /invite bot makes a table of the
        player's own, with the settings they chose."""
        me = self.me.lower()
        for t in tables:
            low = [n.lower() for n in t["names"]]
            if (t["status"] != "open" or t.get("vis") == "private" or t["filled"] >= t["seats"] or me in low
                    or not low):
                continue
            if (tid is not None and t["id"] == tid) or (name is not None and name.lower() in low
                                                        and not bot_waits(t)):
                return t
        return None

    async def bot_table(self, tables, name):
        """/join bot: the bot's open table of this console's game, or None after saying which there are."""
        me = self.me.lower()
        found = [t for t in tables if bot_waits(t) and t["filled"] < t["seats"] and name.lower() in
                 [n.lower() for n in t["names"]] and me not in [n.lower() for n in t["names"]]]
        if len(found) > 1:
            gdata, gfinal = await self.req("GAM")
            games = {g["key"]: g["name"] for g in parse_games(gdata)} if gfinal.startswith("OK") else {}
            await self.say("%s waits at a table of each of these games: %s. Type /join and the number." % (
                name, ", ".join("%s %s" % (games.get(t["key"], t["key"].split("/")[0]), t["id"])
                                for t in sorted(found, key=lambda t: int(t["id"])))))
            return None
        if not found:
            await self.say("%s is not waiting at an open table. /list shows the open tables." % name)
            return None
        return found[0]

    async def says_at(self, tid):
        """{name: what they play with} at a table (LST table=N clients); {} when nothing is known."""
        data, final = await self.req("LST table=%s clients" % tid)
        t = next((x for x in parse_tables(data) if x["id"] == tid), None) if final.startswith("OK") else None
        if t is None:
            return {}
        return {n: t["clients"][i] for n, i in t["seat_of"].items() if i in t["clients"]}

    async def sat_with(self, tid, opp, t=None):
        """'You sat down at table 5 with bob, who plays with ...' after a default /join or /accept; t: the
        table as LST clients gave it, when the console has it, else it is asked for."""
        if t is not None:
            says = {n: t["clients"][i] for n, i in t["seat_of"].items() if i in t.get("clients", {})}
        else:
            says = await self.says_at(tid)
        who = " and ".join(who_plays(n, says.get(n, "")) for n in opp.split(" and "))
        return "You sat down at table %s with %s." % (tid, who)

    async def invitations(self):
        data, final = await self.req("IVL")
        return parse_invites(data) if final.startswith("OK") else []

    async def sit(self, tid, seat, opp, how):
        """JON (at seat, or the seat held for me) -> my seat, or None after saying why not."""
        _, final = await self.req("JON %s%s" % (tid, "" if seat is None else " %d" % seat))
        if not final.startswith("OK"):
            await self.say("Can't sit down at table %s: %s" % (tid, err_text(final)))
            return None
        got = int(final.split()[1]) if len(final.split()) > 1 and final.split()[1].isdigit() else seat or 0
        if self.seated is not None:
            await self.seated(tid, got, opp, how)
        return got

    async def new_table(self, vis):
        """NEW of the first kind, then seat 0 -> the table, or None after saying why not."""
        if not self.kinds:
            await self.say(NOT_HERE)
            return None
        _, final = await self.req("NEW %s %s%s" % (self.kinds[0], self.new, " vis=private" if vis == "private"
                                                    else ""))
        if not final.startswith("OK"):
            await self.say("Can't open a table: %s" % err_text(final))
            return None
        tid = final.split()[1]
        _, final = await self.req("JON %s 0" % tid)
        if not final.startswith("OK"):
            await self.req("LVE %s" % tid)
            await self.say("Can't open a table: %s" % err_text(final))
            return None
        return tid

    async def do_open(self, cmd):
        tid = await self.new_table("public")
        if tid is None:
            return
        if self.seated is not None:
            await self.seated(tid, 0, None, "open")
        await self.say("Your table %s is open: anyone can join it. /close closes it." % tid)

    async def do_close(self, cmd):
        me = self.me.lower()
        mine = [t for t in await self.tables()
                if t["status"] == "open" and [n.lower() for n in t["names"]] == [me]]
        if not mine:
            await self.say("You have no open table.")
            return
        for t in mine:
            await self.req("LVE %s" % t["id"])
        await self.say("Your table %s is closed." % ", ".join(t["id"] for t in mine))

    async def do_accept(self, cmd):
        name = cmd.args[0]
        inv = next((i for i in await self.invitations()
                    if i["from"].lower() == name.lower() and i["key"] in self.kinds), None)
        if inv is None:
            await self.say("You have no invitation from %s here." % name)
            return
        if await self.sit(inv["id"], None, inv["from"], "accept") is not None:
            await self.say(await self.sat_with(inv["id"], inv["from"]))

    async def do_join(self, cmd):
        """/join TABLE or /join NAME: NAME's invitation if there is one (as /accept), else a free seat at an
        open table, found by its number or by the player waiting there."""
        arg = cmd.args[0]
        invs = await self.invitations()
        inv = next((i for i in invs if i["key"] in self.kinds
                    and (i["from"].lower() == arg.lower() or i["id"] == arg)), None)
        if inv is not None:                 # invited there: the seat held for me
            if await self.sit(inv["id"], None, inv["from"], "accept") is not None:
                await self.say(await self.sat_with(inv["id"], inv["from"]))
            return
        tables = await self.tables()
        t = (self.waiting_table(tables, tid=arg) if arg.isdigit() else None) or self.waiting_table(tables, name=arg)
        if t is None and not arg.isdigit() and any(bot_waits(x) for x in tables):
            t = await self.bot_table(tables, arg)
            if t is None:
                return
        if t is None:
            await self.say("Table %s has no open seat for you. /list shows the open tables." % arg if arg.isdigit()
                           else "%s is not waiting at an open table. /list shows the open tables." % arg)
            return
        opp = " and ".join(t["names"])
        if await self.sit(t["id"], None, opp, "join") is not None:
            await self.say(await self.sat_with(t["id"], opp, t))

    async def do_decline(self, cmd):
        name = cmd.args[0]
        invs = [i for i in await self.invitations() if i["from"].lower() == name.lower()]
        if not invs:
            await self.say("You have no invitation from %s." % name)
            return
        for i in invs:
            await self.req("UNV %s %s" % (i["id"], self.me))
        await self.say("You declined %s's invitation." % invs[0]["from"])

    async def do_invite(self, cmd):
        name = cmd.args[0]
        if name.lower() == self.me.lower():
            await self.say("You cannot invite yourself.")
            return
        theirs = next((i for i in await self.invitations()
                       if i["from"].lower() == name.lower() and i["key"] in self.kinds), None)
        if theirs is not None:              # NAME invited me already: that is the game
            await self.do_accept(Command("accept", [name], cmd.text))
            return
        waiting = self.waiting_table(await self.tables(), name=name)
        if waiting is not None:             # NAME waits at an open table: join it
            if await self.sit(waiting["id"], None, waiting["names"][0], "invite") is not None:
                host = waiting["names"][0]
                say = waiting.get("clients", {}).get(waiting["seat_of"].get(host, -1), "")
                await self.say("You sat down at %s's table %s.%s" % (host, waiting["id"],
                                                                     " %s plays %s." % (host, say) if say else ""))
            return
        tid = await self.new_table("private")
        if tid is None:
            return
        _, final = await self.req("INV %s %s 1" % (tid, name))
        if not final.startswith("OK"):
            await self.req("LVE %s" % tid)
            await self.say("There is no player called %s here." % name if final.startswith("ERR NOPLAYER")
                           else "Can't invite %s: %s" % (name, err_text(final)))
            return
        if self.seated is not None:
            await self.seated(tid, 0, name, "invite")
        if final == "OK offline":
            await self.say("%s is not online now. Your invitation waits at table %s." % (name, tid))
        elif bots.is_name(name):
            await self.say("You invited the bot to table %s. It will accept in a few seconds." % tid)
        else:
            await self.say("You invited %s to table %s." % (name, tid))


# ---- the commands the core runs for any frontend (CMD; docs/protocol.md, Commands) -----------------------

# The commands CMD offers, in help order. /resume and /signin belong to a frontend's own screens, and /accept
# and /decline would answer an invitation, which a player answers in its dialog (docs/design.md).
CORE_COMMANDS = ("list", "resign", "invite", "uninvite", "open", "join", "close", "who", "tables", "top", "score",
                 "note", "say", "motd", "time", "quit", "help")
CONFIRM = "YES"     # CMD [table] YES /kind: the confirmed form, which a dialog's Yes button sends

# The confirmations (docs/design.md, Dialogs): the words of each dialog. A command that asks first answers
# DLG kind [table] with these words, and the dialog's Yes sends CMD [table] YES /kind.
DIALOGS = {
    "resign": "Resign this game? %s Leaving the table never resigns.",
    "pass": "Pass your turn? You play nothing this turn.",
    "end": "End your turn? The table doesn't count yet, so your tiles go back and you take a tile.",
    "quit": "Sign out? Your games go on without you.",
}
IN_DIALOG = "An invitation or an offer is answered in its own dialog, not from the command line."
ANSWERS = ("accept", "decline")     # the shared commands that would answer an invitation
STEPS = {"undo": "UND", "end": "END"}

# The table a line was typed at: its id, game key and ruleset, status (open, playing, done), the player's seat
# there (-1 for a watcher), and whether the player has left its game (GONE).
At = namedtuple("At", "id key rules status seat gone")


class CoreConsole(Console):
    """The commands the core runs for CMD [table] [YES] /line, for any frontend. req sends the core's requests
    on the player's own connection. games: [(key, name, rules)], every game hosted with its first ruleset,
    for /list, /open and /invite. at: the table the line was typed at (At), or None. sure: the line came in
    the confirmed form. typed(words): the game's own reading of a typed move (the plugin's typed request,
    docs/plugins.md). After handle(), lines holds what to show, and dialog is (kind, table or None) when the
    command asks first, with lines holding the dialog's words."""

    def __init__(self, req, me, games, at=None, sure=False, typed=None, width=0):
        self.lines, self.dialog = [], None
        super().__init__(self.lines.extend, req, me, CORE_COMMANDS, kinds=[g[0] for g in games], new="",
                         width=width, current=lambda: at.id if at else None,
                         notes=["At your table, a move typed after the slash plays it, such as /d3 in Reversi."])
        self.games, self.at, self.confirmed, self.typed = list(games), at, sure, typed
        self.chosen = None          # (key, rules) of the game an /open or /invite named
        self.usage = ""             # what to type when a table is needed and no game is named

    async def handle(self, text):
        cmd = parse(text)
        if cmd is not None and cmd.name in ANSWERS:
            await self.say(IN_DIALOG)
            return True
        if cmd is not None and cmd.name == "unknown" and self.at is not None and self.at.seat >= 0:
            if await self.play_typed(text):
                return True
        return await super().handle(text)

    def open_dialog(self, kind, tid, words):
        self.dialog = (kind, tid)
        self.lines[:] = wrap([words], self.width)

    def sure(self, yes):
        """Only the confirmed form confirms. A yes typed on the command line answers nothing."""
        return self.confirmed

    async def ask(self, kind, tid, t):
        others = [n for n in t["seated"] if n != "-" and n.lower() != self.me.lower()]
        who = "%s wins." % others[0] if len(t["seated"]) == 2 and others else "The others play on."
        self.open_dialog(kind, tid, DIALOGS[kind] % who)

    async def do_note(self, cmd):
        if self.at is None:
            await self.say("Notes belong to a game, so type /note at its table.")
            return
        await super().do_note(cmd)

    async def do_quit(self, cmd):
        """/quit: the sign-out dialog first. Confirmed, it ends the player's session here (OUT)."""
        if not self.confirmed:
            self.open_dialog("quit", None, DIALOGS["quit"])
            return
        _, final = await self.req("OUT")
        await self.say("You are signed out. Your games go on without you." if final.startswith("OK")
                       else sentence(err_text(final)))

    # ---- a typed move at the player's table ---------------------------------------------------------
    async def play_typed(self, text):
        """A typed move: the game reads the words (typed), and the move is played as the player's own MOV, UND or
        END. False when the game does not know the words, so they are no command."""
        at, words = self.at, text.strip()[1:].split()
        if not words:
            return False
        if at.status == "open":
            await self.say("The game at table %s has not started yet." % at.id)
            return True
        if at.status != "playing":
            await self.say("The game at table %s is over." % at.id)
            return True
        if at.gone:
            await self.say("You have left the game at table %s." % at.id)
            return True
        r = await self.typed(words) if self.typed else {}
        if not r:
            return False
        if r.get("error"):
            await self.say(sentence(r["error"]))
            return True
        if r.get("say"):
            await self.say(r["say"])
            return True
        if r.get("ask") in DIALOGS and not self.confirmed:
            self.open_dialog(r["ask"], at.id, DIALOGS[r["ask"]])
            return True
        if r.get("step") in STEPS:
            line = "%s %s" % (STEPS[r["step"]], at.id)
        elif r.get("move"):
            line = "MOV %s %s" % (at.id, " ".join(r["move"]))
        else:
            return False
        _, final = await self.req(line)
        if not final.startswith("OK"):
            await self.say("You can't %s. %s" % (r.get("what") or "play " + " ".join(words), sentence(err_text(final))))
        return True

    # ---- opening, inviting and closing, at a table or in the lobby --------------------------------
    def game_named(self, word):
        w = word.lower().replace("_", "")
        for key, name, rules in self.games:
            if w in (key.lower(), key.split("/")[0].lower(), name.lower().replace(" ", "")):
                return key, rules
        return None

    def game_list(self):
        names = [key.split("/")[0] for key, _, _ in self.games]
        return names[0] if len(names) == 1 else "%s and %s" % (", ".join(names[:-1]), names[-1])

    async def choose(self, word):
        """The game a word names, for the table an /open or /invite makes -> True, or False after saying which
        games there are."""
        game = self.game_named(word)
        if game is None:
            await self.say("There is no game called %s here. The games are %s." % (word, self.game_list()))
            return False
        self.chosen = game
        self.kinds = [game[0]]
        return True

    async def new_table(self, vis):
        """A table of the game named, else of the game at the table the line was typed at."""
        game = self.chosen or ((self.at.key, self.at.rules) if self.at else None)
        if game is None:
            await self.say("%s The games are %s." % (self.usage, self.game_list()))
            return None
        self.kinds = [game[0]] + [k for k in self.kinds if k != game[0]]
        self.new = game[1]
        return await super().new_table(vis)

    async def do_open(self, cmd):
        """/open [game]: a public table of that game, or of the game at this table."""
        self.usage = "Type /open and a game, such as /open %s." % self.games[0][0].split("/")[0]
        if cmd.args and not await self.choose(cmd.args[0]):
            return
        await super().do_open(cmd)

    async def do_invite(self, cmd):
        """/invite NAME [game]: at the open table where the player waits, NAME is invited there. Anywhere else
        it does what it does in every console, with a table of the game named or of this table's game."""
        name = cmd.args[0]
        if self.at is not None and self.at.status == "open" and self.at.seat >= 0:
            await self.invite_here(name)
            return
        self.usage = "To invite %s, name a game too, such as /invite %s %s." % (name, name,
                                                                                self.games[0][0].split("/")[0])
        if len(cmd.args) > 1 and not await self.choose(cmd.args[1]):
            return
        await super().do_invite(cmd)

    async def invite_here(self, name):
        """INV at the table the player waits at. /invite bot invites the first bot name not seated or invited
        there, since each bot name holds one seat (docs/bot.md)."""
        tid = self.at.id
        if name.lower() == self.me.lower():
            await self.say("You cannot invite yourself.")
            return
        bot = bots.is_name(name)
        if bot:
            data, _ = await self.req("LST table=%s" % tid)
            t = next((x for x in parse_tables(data) if x["id"] == tid), None)
            evts, _ = await self.req("SIN %s 0" % tid)
            pending = pending_invites([l.split(" ", 3)[3] for l in evts if l.startswith("EVT ") and l.count(" ") >= 3])
            if t is not None and t["seats"] - t["filled"] <= len(pending):
                await self.say("Every free seat at table %s is held for someone invited." % tid)
                return
            if name.lower() == bots.NAMES[0]:
                taken = {n.lower() for n in (t["names"] if t else []) + pending}
                name = next((n for n in bots.NAMES if n not in taken), None)
                if name is None:
                    await self.say("Every bot is seated or invited at table %s already." % tid)
                    return
        _, final = await self.req("INV %s %s" % (tid, name))
        if final == "OK":
            await self.say("You invited the bot to table %s. It will sit down in a few seconds." % tid if bot
                           else "You invited %s to table %s." % (name, tid))
        elif final == "OK offline":
            await self.say("%s is not online now. Your invitation waits at table %s."
                           % ("The bot" if bot else name, tid))
        elif final.startswith("ERR NOPLAYER"):
            await self.say("The bot does not play on this parlor." if bot
                           else "There is no player called %s here." % name)
        elif final.startswith("ERR STATE"):
            await self.say("%s is seated at table %s already." % (name, tid))
        else:
            await self.say("%s was not invited. %s" % (name, sentence(err_text(final))))

    async def do_close(self, cmd):
        """/close at a table closes that table, while the player waits there alone. In the lobby it closes every
        open table where the player waits alone, as in every console."""
        if self.at is None:
            await super().do_close(cmd)
            return
        data, _ = await self.req("LST table=%s" % self.at.id)
        t = next((x for x in parse_tables(data) if x["id"] == self.at.id), None)
        if t is None or t["status"] != "open" or [n.lower() for n in t["names"]] != [self.me.lower()]:
            await self.say("Table %s can be closed only while you wait there alone." % self.at.id)
            return
        await self.req("LVE %s" % self.at.id)
        await self.say("Your table %s is closed." % self.at.id)
