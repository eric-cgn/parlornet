"""Chat commands: one set of commands for every chat-style console (docs/chat-commands.md).

A chat-style console is a place where a player types lines to the server: a lobby chat (Outland's
Backgammon lobby, Outland Reversi's public talk), a game's chat box. Every such console takes the same commands, with one meaning and one help text
each. This module parses them, keeps their help, and carries out the ones that need nothing but the core.
A console says which commands it offers and supplies its own way of doing the rest.

The commands, in the order help lists them:

    /list               your games of this kind (table, opponent, whose move, time left) and open tables
    /resume [table]     a running game back (the first one when no table is given)
    /resign [table]     resign the game played now, or the table named: a second /resign within
                        RESIGN_SECS confirms it, or /resign TABLE yes at once
    /invite NAME        invite a player; /invite bot plays the bot
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
    /motd               the message of the day
    /time               how long you have been connected, and the server's time (TIM)
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
    join_says(lines), client_of(args), change_of(kind, args, says, mine), parse_clients(lines),
    names_with(t, names=None),
    who_plays(name, say), joined(name, say, verb, width), client_change(name, say, width)
                                    what each player plays with (the CLIENT event that follows JOIN, and
                                    LST clients; docs/protocol.md, Clients and machines), for the lines
                                    that tell a console's player someone sat down
    time_text(data, final, width=40)
                                    /time's lines from TIM's reply: "Connected 12:34. Server time 21:05 UTC.",
                                    two lines where one would be wider than width

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
                        /resign)
    list_mark(t, view)  a method a console may override: a few words /list adds after one of my running
                        games (t from LST, view the lines of its VUE), "" for none

The defaults, from the core alone: /help, /who (WHO; with a name WHO, PFL, GAM and LST player=NAME),
/tables (GAM, LST player=NAME clients), /top (TOP), /score (PFL), /motd (MOTD), /time (TIM), /list (LST clients, VUE),
/invite (IVL and JON for NAME's own invitation, JON at NAME's open table, else NEW, JON and INV), /open (NEW,
JON), /join (IVL and JON for NAME's invitation, else LST and JON), /close (LST, LVE), /accept (IVL, JON),
/decline (IVL, UNV), /note (NOTE). When /join, /accept or /invite seats the player
with someone, the line says what they play with (LST clients, LST table=N clients), /resign (LST, RES: one of my running games of the console's kinds). /resume,
/signin and /quit have no default: a console that offers them gives a handler. After /resign the table's
END comes to the console as any game's end does.
"""
import inspect
import re
import time
from collections import namedtuple

from . import botflag
from .common import err_text, framed_motd, parse_games, parse_invites, parse_players, parse_profile, parse_tables
from .common import SCORES_NOTE, client_change_sentence, client_sentence, parse_client, parse_clients, parse_top
from .common import score_text

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
    ("motd", "/motd", "the message of the day"),
    ("time", "/time", "time connected, server time"),
    ("signin", "/signin NAME PASSWORD", "sign in"),
    ("quit", "/quit", "sign out (also /bye)"),
    ("help", "/help", "these commands"),
]
NAMES = [c[0] for c in COMMANDS]
USAGE = {c[0]: c[1] for c in COMMANDS}
ALIASES = {"bye": "quit", "logout": "quit", "exit": "quit", "?": "help"}
NEEDS_NAME = ("invite", "join", "accept", "decline", "signin", "tables")      # a name, or for /join a table number

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
    """/time's lines from TIM's reply (data lines, final line): "Connected 12:34. Server time 21:05 UTC." The
    time connected is m:ss, or h:mm:ss from an hour on; the server's time is UTC, hours and minutes. Where
    the line would be wider than width (from an hour on, on a 40-column screen) it is two lines."""
    if final.startswith("ERR UNKNOWN"):
        return ["This server does not tell the time."]
    if not final.startswith("OK"):
        return [err_text(final)]
    f = next((l.split() for l in data if l.startswith("TIM ")), [])
    if len(f) < 3 or not f[1].isdigit() or not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z", f[2]):
        return ["The server's time is not known."]
    secs = int(f[1])
    h, m, s = secs // 3600, secs % 3600 // 60, secs % 60
    lines = ["Connected %s." % ("%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)),
             "Server time %s UTC." % f[2][11:16]]
    one = " ".join(lines)
    return [one] if len(one) <= width else lines


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


# ---- what each player plays with (the CLIENT event and LST clients; docs/protocol.md, Clients and machines) --

def client_of(args):
    """The words after an event's kind -> (seat, what its player plays with) for a CLIENT event, or None (a
    bare CLIENT seat, at a private table the reader may not see, says nothing)."""
    return parse_client("CLIENT " + " ".join(args)) if args else None


def join_says(lines):
    """EVT lines (or their payloads, "JOIN 1 ann") in order -> {seat: text} for each CLIENT that comes right
    after its seat's JOIN: what the one who sat down plays with, for the line that tells of the JOIN. The
    core sends the two together, so they are in one reply."""
    out, last = {}, None
    for l in lines:
        p = l.split(" ", 3) if l.startswith("EVT ") else ["EVT", "", ""] + [l]
        if len(p) < 4:
            continue
        payload = p[3]
        f = payload.split()
        if f and f[0] == "CLIENT" and last is not None and f[1:2] == [last]:
            c = parse_client(payload)
            if c:
                out[c[0]] = c[1]
        last = f[1] if len(f) > 2 and f[0] == "JOIN" else None
    return out


def change_of(kind, args, says, mine=-1):
    """An event (kind and the words after it) -> (seat, text) when it is a CLIENT that tells of a seat's
    program changing: not one that follows its seat's JOIN in the same reply (says, from join_says), not the
    console's own seat (mine), and not a bare one. None otherwise."""
    c = client_of(args) if kind == "CLIENT" else None
    if c is None or c[0] == mine or says.get(c[0]) == c[1]:
        return None
    return c


def joined(name, say, verb="sits down", width=0):
    """'ann sits down with GameMaster 1.0.0 on a Macintosh Plus.' ('ann sits down.' when say is ""), cut to
    width (adapters/common.py client_sentence)."""
    return client_sentence(name, say, verb, width)


def client_change(name, say, width=0):
    """'ann now plays with RSCARDS 5.02 on an IBM PC.', cut to width."""
    return client_change_sentence(name, say, width)


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
        names = botflag.listed(parse_players(data)) if final.startswith("OK") else []
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
        """/resign [table] [yes]: the game the player plays now (current), or the table named, once confirmed by
        a second /resign within RESIGN_SECS or by yes (RES). Only a running game of mine of this console's
        kinds."""
        args = [a.lstrip("#") for a in cmd.args[:2]]
        sure = bool(args) and args[-1].lower() == "yes"
        if sure:
            args = args[:-1]
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
            await self.say("Resign table %s? /resign again to confirm." % tid)
            return
        self.resign_ask = None
        _, final = await self.req("RES %s" % tid)
        await self.say("You resigned table %s." % tid if final.startswith("OK")
                       else "Can't resign table %s: %s" % (tid, err_text(final)))

    def waiting_table(self, tables, name=None, tid=None):
        """An open public table with a free seat, where NAME waits or numbered tid, not one of mine."""
        me = self.me.lower()
        for t in tables:
            low = [n.lower() for n in t["names"]]
            if (t["status"] != "open" or t.get("vis") == "private" or t["filled"] >= t["seats"] or me in low
                    or not low):
                continue
            if (tid is not None and t["id"] == tid) or (name is not None and name.lower() in low):
                return t
        return None

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
        elif botflag.is_bot_name(name):
            await self.say("You invited the bot to table %s. It will accept in a few seconds." % tid)
        else:
            await self.say("You invited %s to table %s." % (name, tid))
