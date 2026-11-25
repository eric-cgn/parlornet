"""Awale extension: ParlorNet's Mancala for Awale 2.x (Didier and Olivier Guillion, Myriad) on the Mac.

ParlorNet is not affiliated with Myriad. From version 2.2.2 (December 1997) Awale plays "live" over IRC: the
program logs on to an IRC server, joins a keyed channel, introduces itself there and exchanges its moves as
PRIVMSG lines with the other copies in the channel (the protocol was read from a captured session and the
program's PowerPC code). This extension is a small IRC server with exactly what
Awale uses. A player puts one line, ParlorNet;name;host;6515, in the program's Internet:serveurs.cfg and
connects with Internet > Network setup > Connect now.

The channel is the extension's own, one per connection: the other members are players of this parlor, shown as
Awale players. Each has an IRC nick of the program's own form (awpl and four hex digits) and introduces itself
with Awale's introduction line; the Mac pings each one and gets its answer at once. The members are:

    ParlorNet           the server itself: it talks in the chat box (as private lines), and refuses a challenge
    bot                 the bot, always there
    the players online  from the core's WHO, refreshed every few seconds; one who leaves quits the channel

Sign-in. Awale sends no password (no PASS) and its name field is free text. On a parlor that signs in by name
alone (sign-in none) the name of the program's Network setup ("Your name") signs in at once. Otherwise
ParlorNet asks in the chat box for !signin NAME PASSWORD, typed into Write to group: the name goes to the core
alone, and the password answers its credential question (common.sign_in_lines). A password the server mailed
cannot be used here; its first sign-in is on the web or by telnet.

Commands. Awale refuses a chat line that starts with / ("Illegal character."), so the shared chat commands
(adapters/chatcmd.py, docs/chat-commands.md) start with ! here: !help, !who, !list, !invite NAME (!invite bot),
!open, !join TABLE or NAME, !close, !accept NAME, !decline NAME, !resign, !note, !motd, !signin and !quit. The
server's answers come from ParlorNet with / turned into !. Lines that are not commands go to the opponent's
table chat during a game.

Challenges and invitations. Challenging a member (select, Challenge) is /invite: it takes that player's
invitation, sits down at their open Oware table, or opens a private table (NEW mancala/1 oware) with the Mac
in seat 0 and invites them; the member accepts (code 3) when the game starts, or refuses (code 4) when the
invitation is declined or runs out. An invitation to the player's own name from anywhere, to an Oware table,
arrives as a challenge from the inviter; Accept sits down (JON), Decline declines (UNV). So does the start of a
game the player joined with a command (!open, !join, !accept): the opponent challenges and Accept starts it.
Awale plays the Oware family only, so an invitation to a table with store rules (kalah) is only named in the
chat.

Who moves first. Awale decides by numbers: each copy sends "I choose : (n)" (code 9) and the bigger number
moves first, a tie making both choose again. The core's seat 0 moves first, so the member answers the Mac's
number with one just below it when the Mac sits in seat 0 and one just above it otherwise (a tie when there
is no such number, 0 or 32767, so that the Mac chooses again).

The game. A hole of Awale is a pit of mancala/1 (hole h, from the mover's left, is pit h + 1); both sow
counterclockwise and capture 2s and 3s on the opponent's row the same way (Awale's own "Awale" rule: twelve or
more seeds skip the starting hole, a move must feed an opponent who has no seeds when it can, a capture that
would leave the opponent nothing captures nothing). The core decides; the Mac's moves are played on it and the
opponent's moves go to the Mac as code 6 lines with the board after the move. The rules end differently:

    ParlorNet's oware                   Awale
    more than 24 seeds wins at once     plays on to the end
    an endless cycle ends the game      no such end
    a player who cannot feed ends it    that player still moves; the game ends when the next cannot

So when the core ends a game that the Mac would play on, the member ends it as a deadlock (code 10, reason 3),
and the Mac shows the seeds captured so far, which at more than 24 are the final scores. When the opponent
cannot feed, the member makes the move Awale expects (its first non-empty hole, which by then reaches no
further than its own row), and the Mac ends the game itself with the same totals as the core; when the Mac
cannot feed, its player's last move ends the game there and is not sent to the core. A resignation on the
core, or the opponent's time running out, is the member abandoning (code 10, reason 1); the Mac's own time
running out is reason 2. Cancel game on the Mac (code 10, reason 1) resigns the table.

Leaving. A Mac that goes away leaves its table running: the game can be finished on the web or by telnet,
since Awale has no way back into a game. The server pings the Mac every PING_SECS (IRC PING), which Awale
answers, so a connection that waits for a move always carries something both ways.

    python3 -m adapters.awale --core 127.0.0.1:6590 --key awale.key --port 6515
"""
import argparse
import asyncio
import re
import sys
import time
import zlib

from . import botflag, chatcmd, declare
from .common import (LOGIN_TOTAL_SECS, NEWPASS_TEXT, CoreClient, CoreError, Gate, err_text, keepalive, no_wait,
                     parse_invites, parse_players, parse_tables, peer_addr, sign_in_lines, uninvite_of)
from .clients import BY_ID

CLIENT_NAME = BY_ID["awale"].name           # "Awale"
PROTOCOL = ("AWALE", "IRC, for Awale", "Mancala (Oware rules) for Awale 2.x on Macs")
MANCALA = "mancala/1"
RULES = "oware"                             # what follows NEW mancala/1 for a table made here
SERVER = "parlornet"                        # the IRC server's name in its replies
CHANNEL = "#AWCH0000"                       # the channel Awale joins (its #AWCH and four digits)
TOPIC = "Awale_players'_private_room"
RULE_NAME = "Awale"                         # the rule every member plays
RULE_HASH = "EB878272263F9CA0112349A7084D2543"  # Awale 2.2.2's code for its Awale rule (echoed when the Mac's own differs)
NO_GAME = "ffff00000000000000000000000000000ffff"   # the board field of an introduction with no game
POINTS = 100                                # Awale's starting rating; ParlorNet keeps none
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
MAX_SESSIONS = 40
REGISTER_SECS = 60.0        # NICK and USER after connecting
POLL_SECS = 1.0             # the table's events
LOBBY_POLL_SECS = 5.0       # WHO and IVL in the lobby
PING_SECS = 60.0            # IRC PING to the Mac
LOBBY_SECS = 3600.0         # a lobby with no typing and no game closes
MAX_CHOICE = 32767          # Awale's choice numbers are 0 to 32767
COMMANDS = ["list", "resign", "invite", "open", "join", "close", "accept", "decline", "who", "tables", "top",
            "score", "note", "motd", "time", "signin", "quit", "help"]
BEFORE_SIGNIN = ("signin", "help", "motd", "quit")
NOTES = ["Commands start with !, since Awale refuses /. Challenge a player in the list, or !invite bot."]
WELCOME = "Welcome, %s. Challenge a player in the list, or the bot. !help for the commands."
ids = iter(range(1, 1 << 30))

# The message codes (the three digits after the colon of each PRIVMSG)
INTRO, CHALLENGE, ACCEPT, REFUSE, PLAYING, MOVE, SAY, FREE, CHOOSE, ENDED, PING, PONG = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13)
# Code 10: why a game ended, and its result, both from the sender's side
ABANDONED, HE_ABANDONED, DEADLOCK, NORMAL = 1, 2, 3, 4
WON, LOST, DRAWN = 0, 1, -1
REASON_TEXT = {ABANDONED: "I abandonned.", HE_ABANDONED: "He abandonned.", DEADLOCK: "The game was in deadlock.",
               NORMAL: "It terminated normally."}
RESULT_TEXT = {WON: "I won.", LOST: "He won.", DRAWN: "Drawn game."}


def log(sid, text):
    print("awale %d: %s" % (sid, text), file=sys.stderr, flush=True)


# ---- the wire --------------------------------------------------------------------------------------------

def parse_irc(line):
    """An IRC line -> (prefix nick or None, COMMAND, [args]); the last argument may contain spaces."""
    prefix = None
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
        prefix = prefix.split("!")[0]
    if " :" in line:
        head, _, trail = line.partition(" :")
        args = head.split() + [trail]
    else:
        args = line.split()
    if not args:
        return prefix, "", []
    return prefix, args[0].upper(), args[1:]


def parse_message(text):
    """A PRIVMSG text of Awale's ('006 I play against ...') -> (code, text after it) or None."""
    m = re.match(r"^(\d{3}) ?(.*)$", text, re.S)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def fields(text):
    """The parenthesized fields of a message, in order: Awale reads its messages only by them."""
    return re.findall(r"\(([^()]*)\)", text)


def clean(text, most=200):
    """Text that goes inside a field: printable, no parentheses (Awale turns its own into brackets)."""
    text = text.replace("(", "[").replace(")", "]")
    return re.sub(r"[^ -~]", "?", text)[:most]


def board(moves, i_started, mine, theirs, caught, lost, hole):
    """The board field of a move (code 6), as Awale writes it: the move count (%04x), 0 when the sender moved
    first and 1 when the other did (%01x), the sender's six holes from its left then the other's (%02x each),
    the seeds the sender and the other have captured (%02x each) and the hole played (%04x, ffff for none)."""
    return "%04x%01x%s%02x%02x%04x" % (moves & 0xFFFF, 0 if i_started else 1,
                                       "".join("%02x" % (c & 0xFF) for c in list(mine) + list(theirs)),
                                       caught & 0xFF, lost & 0xFF, hole & 0xFFFF)


def read_board(text):
    """board()'s field -> dict, or None when it is not one."""
    if not re.fullmatch(r"[0-9a-fA-F]{37}", text or ""):
        return None
    cells = [int(text[5 + 2 * i:7 + 2 * i], 16) for i in range(12)]
    hole = int(text[33:37], 16)
    return {"moves": int(text[0:4], 16), "started": text[4] == "0", "mine": cells[:6], "theirs": cells[6:],
            "caught": int(text[29:31], 16), "lost": int(text[31:33], 16), "hole": hole - 0x10000 if hole > 0x7FFF
            else hole}


def intro(name, email, country, points, rule, rule_hash, serial, when, address, sysop, version, state, text):
    """The introduction line (code 1), field by field."""
    return ("I'm (%s) (%s) from (%s),I have (%d) pts, I play (%s)(%s)(%s)(%s)(%s)(%s)(%s)(%s)(%s)"
            % (clean(name, 31), clean(email, 63), clean(country, 31), points, clean(rule, 31), rule_hash, serial,
               when, address, sysop, version, state, clean(text, 120)))


def nick_for(name, taken):
    """A member's nick: awpl and four hex digits from the name, not one in taken (lower case)."""
    n = zlib.crc32(name.lower().encode()) & 0xFFFF
    while True:
        nick = "awpl%04x" % n
        if nick not in taken:
            return nick
        n = (n + 1) & 0xFFFF


def answer_choice(theirs, mac_first):
    """The member's number for the Mac's (code 9): just below it when the Mac is to move first, just above it
    otherwise, equal (Awale then chooses again) when there is no such number."""
    if mac_first:
        return theirs - 1 if theirs > 0 else theirs
    return theirs + 1 if theirs < MAX_CHOICE else theirs


def bang(lines):
    """The shared console's lines with its commands shown as Awale players type them (! for /)."""
    names = "|".join(sorted(set(chatcmd.NAMES) | set(chatcmd.ALIASES) - {"?"}, key=len, reverse=True))
    rx = re.compile(r"(?<![\w/])/(%s)\b" % names)
    return [rx.sub(r"!\1", l) for l in lines]


# ---- mancala/1 views and events (docs/types/mancala-1.md) -----------------------------------------------

def rows(b, seat):
    """A 14-count board -> (seat's six pits from its left, the other's six, seat's store, the other's)."""
    o = 1 - seat
    return b[seat * 7:seat * 7 + 6], b[o * 7:o * 7 + 6], b[seat * 7 + 6], b[o * 7 + 6]


def parse_view(lines):
    v = {"rules": "", "b": None, "turn": -1, "clock": None}
    for l in lines:
        f = l.split()
        if len(f) >= 3 and f[0] == "RU":
            v["rules"] = f[1]
        elif len(f) == 15 and f[0] == "B":
            v["b"] = [int(x) for x in f[1:]]
        elif len(f) == 2 and f[0] == "W":
            v["turn"] = int(f[1])
        elif len(f) == 4 and f[0] == "CLK":
            v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
    return v


class Member:
    """A player of the parlor as an Awale player in the channel."""

    def __init__(self, name, nick, text, bot=False, server=False):
        self.name, self.nick, self.text, self.bot, self.server = name, nick, text, bot, server
        self.status = FREE

    def prefix(self):
        return ":%s!%s@%s" % (self.nick, self.nick, SERVER)


class Session:
    def __init__(self, svc, reader, writer, ticket=None):
        self.svc, self.r, self.w, self.ticket = svc, reader, writer, ticket
        self.sid = next(ids)
        self.addr = peer_addr(writer)
        self.core = None
        self.core_lock = asyncio.Lock()
        self.nick = None            # the Mac's IRC nick (awpl....)
        self.user = None
        self.joined = False
        self.mac_name = None        # the name in the Mac's introduction (a claim, never a sign-in by itself
                                    # except on a parlor that signs in by name alone)
        self.mac_rule, self.mac_hash = RULE_NAME, RULE_HASH
        self.mac_serial, self.mac_version = "NULL", "546"
        self.name = None            # the signed-in name (the core's)
        self.members = {}           # nick (lower) -> Member
        self.server_member = None
        self.console = None
        self.since = time.monotonic()
        self.last_typed = time.monotonic()
        self.closing = False
        # the game
        self.tid = None
        self.seat = None
        self.opp = None             # the opponent's Member
        self.opp_says = ""          # what the opponent plays with (the CLIENT event after their JOIN)
        self.says = {}              # seat -> what its player plays with, from the latest LST clients
        self.state = "lobby"        # lobby, challenging (the Mac challenged opp), offered (opp challenged the Mac),
                                    # starting (accepted; choosing who starts), playing, over
        self.offers = {}            # invitation table id -> Member: challenges made to the Mac from invitations
        self.declined = set()       # invitation tables already offered (not offered again)
        self.cursor = 0
        self.started = False        # the core's game has started
        self.on = False             # the Mac has its game (its code 6 with hole -1 came)
        self.queue = []             # the opponent's moves (and the end) waiting for the Mac's game
        self.sows = 0               # moves made so far at the table
        self.last_sow = None        # (seat, board after it) of the latest move
        self.stuck = False          # the core swept the rows because the player to move cannot feed
        self.welcomed = False

    # ---- output -------------------------------------------------------------------------------------------
    async def send(self, line):
        if self.svc.trace:
            log(self.sid, "> " + line)
        self.w.write(line.encode("latin-1", "replace") + b"\r\n")
        await self.w.drain()

    async def numeric(self, code, text):
        await self.send(":%s %s %s %s" % (SERVER, code, self.nick or "*", text))

    async def msg(self, member, code, text, to=None):
        """A message from a member: to the channel, or to the Mac's nick."""
        await self.send("%s PRIVMSG %s :%03d %s" % (member.prefix(), to or CHANNEL, code, text))

    async def say(self, lines):
        """Lines in the Mac's chat box, from ParlorNet: private lines, which Awale shows as <ParlorNet> : text."""
        if isinstance(lines, str):
            lines = [lines]
        if self.server_member is None:
            return
        for line in bang(lines):
            await self.msg(self.server_member, SAY, "I say to %s (%s) : (%s)" % (
                clean(self.mac_name or "you", 31), self.nick, clean(line, 250)), to=self.nick)

    async def say_from(self, member, text):
        await self.msg(member, SAY, "I say to %s (%s) : (%s)" % (clean(self.mac_name or "you", 31), self.nick,
                                                                  clean(text, 250)), to=self.nick)

    # ---- members --------------------------------------------------------------------------------------------
    def taken(self):
        return set(self.members) | {(self.nick or "").lower()}

    async def add(self, name, text, bot=False, server=False):
        m = Member(name, nick_for(name, self.taken()), text, bot, server)
        self.members[m.nick.lower()] = m
        await self.send("%s JOIN :%s" % (m.prefix(), CHANNEL))
        await self.introduce(m)
        return m

    async def introduce(self, m):
        if m.status == PLAYING and self.opp is m:
            await self.msg(m, PLAYING, "I'm now playing with %s (%s)" % (clean(self.mac_name or "", 31), self.nick))
        else:
            await self.msg(m, FREE, "I'm not playing for the moment.")
        await self.msg(m, INTRO, intro(m.name, "-", "ParlorNet" if not m.server else "server", POINTS,
                                       self.mac_rule if self.mac_rule == RULE_NAME else RULE_NAME,
                                       self.mac_hash if self.mac_rule == RULE_NAME else RULE_HASH,
                                       self.mac_serial, time.strftime("%H:%M:%S", time.gmtime()), "0.0.0.0", "0",
                                       self.mac_version, NO_GAME, m.text))

    async def drop(self, m):
        self.members.pop(m.nick.lower(), None)
        await self.send("%s QUIT :Gone" % m.prefix())

    def by_name(self, name):
        return next((m for m in self.members.values() if m.name.lower() == (name or "").lower()), None)

    async def member(self, name):
        """The member for a player, added to the channel when missing."""
        m = self.by_name(name)
        if m is None:
            m = await self.add(name, "The server's bot." if botflag.is_bot_name(name) else "A ParlorNet player.",
                               bot=botflag.is_bot_name(name))
        return m

    async def refresh(self):
        """The channel's members from WHO: newcomers join, players gone quit (not the opponent)."""
        data, final = await self.req("WHO")
        if not final.startswith("OK"):
            return
        online = botflag.listed(parse_players(data))
        want = {n.lower() for n in online if n.lower() != (self.name or "").lower()}
        for m in list(self.members.values()):
            if not m.server and m.name.lower() not in want and m is not self.opp and m not in self.offers.values():
                await self.drop(m)
        for n in online:
            if n.lower() != (self.name or "").lower() and self.by_name(n) is None:
                await self.member(n)

    # ---- the core ---------------------------------------------------------------------------------------
    async def req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    async def connect_core(self):
        if self.core is not None:
            return True
        try:
            self.core = await CoreClient.connect(*self.svc.core_addr)
            await self.core.source(self.addr)
            await self.core.claim(CLIENT_NAME)         # the turns say what they were played with
            return True
        except (OSError, asyncio.TimeoutError, CoreError) as e:
            self.core = None
            log(self.sid, "the core is not answering: %s" % e)
            await self.say("The server is not answering just now. Connect again later.")
            return False

    async def sign_in(self, name, password):
        """None when signed in, else the reason to show."""
        shown = []

        async def keep(lines):
            shown.extend(lines)
        why = await sign_in_lines(self.core, name, password, None, keep, "signin",
                                  "There is no account %s here. Make one on the web site first." % name)
        if why == NEWPASS_TEXT:
            return why
        if shown:
            await self.say(shown)
        if why:
            return no_wait(why)
        if self.ticket is not None and not self.ticket.sign_in():
            return "The server is full. Try again soon."
        self.name = self.core.name or name
        log(self.sid, "signed in as %s" % self.name)
        self.core.user_input()
        self.console = Console(self)
        await self.say(WELCOME % self.name)
        await self.refresh()
        await self.check_invitations()
        return None

    async def do_signin(self, cmd):
        if self.name:
            await self.say("You are signed in as %s." % self.name)
            return
        if len(cmd.args) < 2 and self.core.auth != "none":
            await self.say("Type !signin NAME PASSWORD")
            return
        why = await self.sign_in(cmd.args[0], " ".join(cmd.args[1:]) or None)
        if why:
            log(self.sid, "sign-in refused")
            await self.say(why)
            if why == "The server is full. Try again soon.":
                self.closing = True
                self.w.close()

    async def do_quit(self, cmd):
        await self.say("Goodbye.")
        self.closing = True
        self.w.close()

    # ---- the session ------------------------------------------------------------------------------------
    async def lines(self):
        buf = b""
        while True:
            data = await self.r.read(4096)
            if not data:
                return
            if self.ticket is not None:
                self.ticket.touch()
            buf += data
            if len(buf) > 8192:
                return
            while True:
                cut = [i for i in (buf.find(b"\r"), buf.find(b"\n")) if i >= 0]
                if not cut:
                    break
                i = min(cut)
                line, buf = buf[:i], buf[i + 1:]
                if line.strip():
                    yield line.decode("mac_roman", "replace")

    async def run(self):
        it = self.lines()
        poller = None
        try:
            while True:
                try:
                    line = await asyncio.wait_for(it.__anext__(), REGISTER_SECS if not self.joined else None)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    return
                await self.on_line(line)
                if self.joined and poller is None:
                    poller = asyncio.ensure_future(self.poll())
                if self.closing:
                    return
        finally:
            if poller is not None:
                poller.cancel()
            await self.hang_up()

    async def hang_up(self):
        """The Mac went away: its table stays as it is (Awale cannot come back to a game)."""
        if self.core is None:
            return
        try:
            if self.tid and not self.started:
                await self.req("LVE %s" % self.tid)
            if self.name:
                await self.core.out()
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.core.close()

    async def on_line(self, line):
        if self.svc.trace:
            log(self.sid, "< " + re.sub(r"(!signin\s+\S+\s+)\S.*?\)", r"\1...)", line, flags=re.I))
        prefix, cmd, args = parse_irc(line)
        if cmd == "NICK" and args:
            if self.nick is None:
                self.nick = args[0][:16]
                await self.registered()
            else:
                await self.send(":%s NICK :%s" % (self.nick, args[0][:16]))
                self.nick = args[0][:16]
        elif cmd == "USER" and args:
            self.user = args[0]
            if self.nick:
                await self.registered()
        elif cmd == "PASS":
            pass                    # Awale sends none; a password comes with !signin
        elif cmd == "PING":
            await self.send(":%s PONG %s :%s" % (SERVER, SERVER, args[-1] if args else SERVER))
        elif cmd == "PONG":
            pass
        elif cmd == "LIST":
            await self.numeric("321", "Channel :Users  Name")
            await self.numeric("322", "%s %d :%s" % (CHANNEL, len(self.members) + 1, TOPIC))
            await self.numeric("323", ":End of /LIST")
        elif cmd == "JOIN" and self.nick and self.user:
            await self.join()
        elif cmd in ("MODE", "TOPIC") and args:
            if args[0].lower() == CHANNEL.lower():
                await self.send(":%s!%s@%s %s %s" % (self.nick, self.user, SERVER, cmd, " ".join(
                    args[:-1] + [":" + args[-1]] if cmd == "TOPIC" else args)))
        elif cmd == "PRIVMSG" and len(args) >= 2:
            await self.privmsg(args[0], args[1])
        elif cmd == "QUIT":
            self.closing = True
        elif cmd in ("WHO", "WHOIS", "NAMES", "USERHOST", "ISON", "PART", "NOTICE"):
            pass
        elif cmd:
            await self.numeric("421", "%s :Unknown command" % cmd)

    async def registered(self):
        if not (self.nick and self.user) or self.welcomed:
            return
        self.welcomed = True
        await self.numeric("001", ":Welcome to ParlorNet's IRC service for Awale, %s" % self.nick)
        await self.numeric("002", ":Your host is %s" % SERVER)
        await self.numeric("003", ":This is ParlorNet, not an IRC network")
        await self.numeric("004", "%s parlornet-awale i k" % SERVER)
        await self.numeric("375", ":- %s -" % SERVER)
        await self.numeric("372", ":- Mancala for Awale")
        await self.numeric("376", ":End of /MOTD command.")

    async def join(self):
        if self.joined:
            return
        self.joined = True
        names = " ".join([self.nick] + [m.nick for m in self.members.values()])
        await self.send(":%s!%s@%s JOIN :%s" % (self.nick, self.user, SERVER, CHANNEL))
        await self.numeric("332", "%s :%s" % (CHANNEL, TOPIC))
        await self.numeric("353", "= %s :@%s" % (CHANNEL, names))
        await self.numeric("366", "%s :End of /NAMES list." % CHANNEL)

    async def privmsg(self, target, text):
        m = parse_message(text)
        if target.lower() == (self.nick or "").lower():
            # the Mac pings itself to time the link: back it comes
            await self.send(":%s!%s@%s PRIVMSG %s :%s" % (self.nick, self.user, SERVER, self.nick, text))
            return
        if m is None:
            return
        code, body = m
        f = fields(body)
        to = self.members.get(target.lower())
        if code == PING and to is not None:
            await self.msg(to, PONG, "Pong", to=self.nick)
        elif code == INTRO:
            await self.mac_intro(f)
        elif code == SAY:
            await self.typed(f)
        elif code == CHALLENGE and len(f) >= 1:
            await self.mac_challenge(self.members.get(f[0].lower()))
        elif code == ACCEPT and len(f) >= 1:
            await self.mac_accept(self.members.get(f[0].lower()))
        elif code == REFUSE and len(f) >= 1:
            await self.mac_refuse(self.members.get(f[0].lower()))
        elif code == CHOOSE and f:
            await self.mac_choice(f[0])
        elif code == MOVE and len(f) >= 2:
            await self.mac_move(self.members.get(f[0].lower()), f[1])
        elif code == ENDED and len(f) >= 2:
            await self.mac_ended(self.members.get(f[0].lower()), f[1])
        elif code == FREE:
            await self.mac_free()

    async def mac_intro(self, f):
        """The Mac's introduction: its name (a claim), its rule; then sign-in or the welcome."""
        if len(f) < 1:
            return
        first = self.mac_name is None
        self.mac_name = f[0][:31]
        if len(f) >= 6:
            self.mac_rule, self.mac_hash = f[4], f[5]
        if len(f) >= 11:
            self.mac_serial, self.mac_version = f[6], f[10]
        if not first:
            return
        if not await self.connect_core():
            self.closing = True
            return
        self.server_member = await self.add("ParlorNet", "The server: it answers !help typed below.", server=True)
        if self.mac_rule != RULE_NAME:
            await self.say("ParlorNet plays Awale's Awale rule: choose Rules > Awale.")
        motd = await self.motd()
        if motd:
            await self.say(chatcmd.framed_motd(motd))
        if self.core.auth == "none" and NAME_RE.match(self.mac_name):
            why = await self.sign_in(self.mac_name, None)
            if why is None:
                return
            await self.say(why)
        await self.say(["Sign in: type !signin NAME PASSWORD in Write to group, then Send."])

    async def motd(self):
        try:
            data, final = await self.core.req("MOTD")
        except (CoreError, OSError, asyncio.TimeoutError):
            return []
        return [l[5:] for l in data if l.startswith("MOTD")][:8] if final.startswith("OK") else []

    async def typed(self, f):
        """A chat line: field 1 is the channel (Write to group) or a member (the game's own box), field 2 the
        text. A command, or the opponent's table chat."""
        if len(f) < 2:
            return
        text = f[1].strip()
        if not text:
            return
        self.last_typed = time.monotonic()
        if self.core is not None and self.name:
            self.core.user_input()
        cmdline = "/" + text[1:] if text.startswith("!") else None
        try:
            if cmdline is not None:
                cmd = chatcmd.parse(cmdline)
                if self.name is None:
                    if cmd is not None and cmd.name in BEFORE_SIGNIN:
                        await {"signin": self.do_signin, "quit": self.do_quit}.get(
                            cmd.name, self.before_signin)(cmd)
                    else:
                        await self.say("Sign in first: !signin NAME PASSWORD")
                    return
                if await self.console.handle(cmdline):
                    return
            if self.name is None:
                await self.say("Sign in first: !signin NAME PASSWORD")
            elif self.tid and self.started:
                await self.req("CHT %s %s" % (self.tid, re.sub(r"[^ -~]", "?", text)[:56]))
            else:
                await self.say("Chat goes to your opponent during a game. !help lists the commands.")
        except (CoreError, OSError, asyncio.TimeoutError) as e:
            log(self.sid, "command failed: %s" % e)
            await self.say("The server is not answering just now.")

    async def before_signin(self, cmd):
        if cmd.name == "help":
            await self.say(chatcmd.help_lines(BEFORE_SIGNIN))
        elif cmd.name == "motd":
            await self.say(chatcmd.framed_motd(await self.motd()) or ["There is no message of the day."])

    # ---- challenges ---------------------------------------------------------------------------------------
    async def mac_challenge(self, m):
        """The Mac challenges a member: /invite, on an Oware table."""
        if m is None:
            return
        if self.name is None or m.server or self.state != "lobby" or self.tid:
            await self.msg(m, REFUSE, "I refuse the challenge from %s (%s)" % (clean(self.mac_name or "", 31),
                                                                                self.nick))
            await self.say("Sign in first: !signin NAME PASSWORD" if self.name is None else
                           "Challenge a player in the list, or the bot." if m.server else
                           "You have a game or a table already.")
            return
        self.opp = m
        self.state = "challenging"
        await self.console.handle("/invite %s" % m.name)
        if self.tid is None:                # nothing came of it (said why)
            await self.refuse_mac(m)

    async def refuse_mac(self, m):
        await self.msg(m, REFUSE, "I refuse the challenge from %s (%s)" % (clean(self.mac_name or "", 31), self.nick))
        if self.opp is m:
            self.opp = None
        self.state = "lobby"

    async def mac_accept(self, m):
        """The Mac accepts a member's challenge: an invitation (JON), or the game it joined starting."""
        if m is None or self.state not in ("lobby", "offered"):
            return
        if self.tid and self.opp is m and self.state == "offered":
            self.state = "starting"
            m.status = PLAYING
            await self.msg(m, PLAYING, "I'm now playing with %s (%s)" % (clean(self.mac_name or "", 31), self.nick))
            return
        tid = next((t for t, mm in self.offers.items() if mm is m), None)
        if tid is None:
            return
        del self.offers[tid]
        _, final = await self.req("JON %s" % tid)
        if not final.startswith("OK"):
            await self.say("Can't sit down at table %s: %s" % (tid, err_text(final)))
            await self.msg(m, FREE, "I'm not playing for the moment.")
            return
        got = int(final.split()[1]) if len(final.split()) > 1 and final.split()[1].isdigit() else 1
        await self.seated(tid, got, m.name, "accept")
        self.state = "starting"
        m.status = PLAYING
        await self.msg(m, PLAYING, "I'm now playing with %s (%s)" % (clean(self.mac_name or "", 31), self.nick))

    async def mac_refuse(self, m):
        """The Mac refuses a challenge: an invitation is declined; a game already begun is left as it is."""
        if m is None:
            return
        tid = next((t for t, mm in self.offers.items() if mm is m), None)
        if tid is not None:
            del self.offers[tid]
            self.declined.add(tid)
            await self.req("UNV %s %s" % (tid, self.name))
            await self.say("You declined %s's invitation." % m.name)
        elif self.state == "offered" and self.opp is m:
            await self.say("Your game with %s at table %s is running: accept their challenge to play it, or "
                           "!resign." % (m.name, self.tid))
        elif self.state == "challenging" and self.opp is m and self.tid and not self.started:
            await self.req("LVE %s" % self.tid)
            await self.say("You withdrew the challenge; table %s is closed." % self.tid)
            self.reset()

    async def seated(self, tid, seat, opp, how):
        """The shared console (or an accept) has seated the Mac."""
        self.tid, self.seat, self.cursor = tid, seat, 0
        self.started, self.on, self.queue, self.sows, self.last_sow, self.stuck = False, False, [], 0, None, False
        if opp and self.opp is None and how != "open":
            self.opp = await self.member(opp.split()[0])
        log(self.sid, "seated at table %s, seat %d (%s)" % (tid, seat, how))

    def reset(self):
        if self.opp is not None:
            self.opp.status = FREE
        self.tid, self.seat, self.opp, self.state = None, None, None, "lobby"
        self.opp_says = ""          # what the opponent plays with (the CLIENT event after their JOIN)
        self.started, self.on, self.queue, self.last_sow, self.stuck = False, False, [], None, False

    async def check_invitations(self):
        """Invitations to the player: an Oware table's arrives as a challenge from the inviter."""
        if self.name is None or self.state != "lobby" or self.tid:
            return
        data, final = await self.req("IVL")
        if not final.startswith("OK"):
            return
        invs = [i for i in parse_invites(data) if i["key"] == MANCALA]
        live = {i["id"] for i in invs}
        for tid, m in list(self.offers.items()):
            if tid not in live:             # withdrawn or expired
                del self.offers[tid]
                m.status = FREE
                await self.msg(m, FREE, "I'm not playing for the moment.")
                await self.say("%s's invitation has ended." % m.name)
        for i in invs:
            if i["id"] in self.offers or i["id"] in self.declined:
                continue
            if i["rules"] != RULES:
                self.declined.add(i["id"])
                await self.say("%s invited you to Mancala with %s rules at table %s; Awale plays Oware rules "
                               "only, so play that one on the web or by telnet." % (i["from"], i["rules"], i["id"]))
                continue
            if self.offers:
                continue                    # one challenge at a time
            m = await self.member(i["from"])
            self.offers[i["id"]] = m
            await self.msg(m, CHALLENGE, "I want to challenge %s (%s)" % (clean(self.mac_name or "", 31), self.nick))

    # ---- the table ----------------------------------------------------------------------------------------
    async def poll(self):
        last_lobby, last_ping = 0.0, time.monotonic()
        while not self.closing:
            await asyncio.sleep(self.svc.poll)
            try:
                now = time.monotonic()
                if now - last_ping >= self.svc.ping:
                    last_ping = now
                    await self.send("PING :%s" % SERVER)
                if self.core is None:
                    continue
                if self.name is None:
                    if now - self.since > LOGIN_TOTAL_SECS:
                        await self.say("Signing in took too long. Connect again to try again.")
                        self.closing = True
                        self.w.close()
                    continue
                if self.tid:
                    await self.table_events()
                if now - last_lobby >= self.svc.lobby_poll:
                    last_lobby = now
                    await self.refresh()
                    await self.check_invitations()
                if not self.tid and now - self.last_typed > LOBBY_SECS:
                    await self.say("Nothing happened for a long time; goodbye.")
                    self.closing = True
                    self.w.close()
            except (CoreError, OSError, asyncio.TimeoutError, ConnectionError) as e:
                log(self.sid, "poll: %s" % e)
                self.closing = True
                self.w.close()
            except Exception as e:
                log(self.sid, "poll error: %r" % e)
                import traceback
                traceback.print_exc()
                self.closing = True
                self.w.close()

    async def table_events(self):
        data, final = await self.req("SIN %s %d" % (self.tid, self.cursor))
        if final.startswith("ERR"):
            if not self.started:
                await self.say("Your table is gone.")
                await self.end_challenge()
            return
        says = chatcmd.join_says(data)      # what the opponent who sat down plays with
        for l in data:
            p = l.split(" ", 4)
            if len(p) < 4 or p[0] != "EVT" or not p[2].isdigit() or int(p[2]) <= self.cursor:
                continue
            self.cursor = int(p[2])
            kind, rest = p[3], p[4] if len(p) > 4 else ""
            a = rest.split()
            if not self.tid:
                break
            change = chatcmd.change_of(kind, a, says, self.seat if self.seat is not None else -1)
            if kind == "JOIN" and len(a) >= 2 and a[0] != str(self.seat) and self.opp is None:
                self.opp = await self.member(a[1])
                self.opp_says = says.get(int(a[0]), "") if a[0].isdigit() else ""
            elif change and self.opp is not None and self.started:     # plays on with another program
                self.opp_says = change[1]
                await self.say(chatcmd.client_change(self.opp.name, change[1]))
            elif kind == "UNINVITE" and not self.started:
                who, why = uninvite_of("UNINVITE " + rest) or ("", "")
                await self.say("%s %s." % (who, {"declined": "declined your challenge", "expired":
                                                   "did not answer in time"}.get(why, "is no longer invited")))
                await self.end_challenge()
            elif kind == "START" and not self.started:
                await self.game_starts()
            elif kind == "CHT" and len(a) >= 2 and a[0] != str(self.seat) and self.opp is not None:
                await self.say_from(self.opp, rest.split(" ", 1)[1])
            elif kind == "SOW" and len(a) == 17 and a[0].isdigit() and a[1].isdigit():
                self.sows += 1
                self.last_sow = (int(a[0]), [int(x) for x in a[3:]])
                self.stuck = False
                if a[0] != str(self.seat):
                    await self.forward(("move", int(a[0]), int(a[1]), self.sows, self.last_sow[1]))
            elif kind == "SWEEP" and len(a) >= 3 and a[2] == "nofeed":
                self.stuck = True
            elif kind == "END":
                await self.forward(("end", a))

    async def end_challenge(self):
        m = self.opp
        if self.tid and not self.started:
            try:
                await self.req("LVE %s" % self.tid)
            except CoreError:
                pass
        if m is not None and self.state == "challenging":
            await self.refuse_mac(m)
        self.reset()

    async def game_starts(self):
        """Both seats are taken. A member the Mac challenged accepts; otherwise the member challenges the Mac
        and Accept (or its earlier one) begins the game."""
        self.started = True
        if self.opp is None:
            names = await self.names()
            if self.seat is not None and len(names) == 2:
                self.opp = await self.member(names[1 - self.seat])
                self.opp_says = self.says.get(1 - self.seat, "")
        if self.opp is None:
            return
        log(self.sid, "game on table %s with %s, seat %d" % (self.tid, self.opp.name, self.seat))
        if self.state == "challenging":
            self.state = "starting"
            self.opp.status = PLAYING
            await self.msg(self.opp, ACCEPT, "I accept the challenge from %s (%s)" % (
                clean(self.mac_name or "", 31), self.nick))
            if self.opp_says:               # Awale says the member accepted; the parlor adds with what
                await self.say("%s plays %s." % (self.opp.name, self.opp_says))
        elif self.state == "lobby":
            self.state = "offered"
            await self.msg(self.opp, CHALLENGE, "I want to challenge %s (%s)" % (
                clean(self.mac_name or "", 31), self.nick))
            await self.say(chatcmd.joined(self.opp.name, self.opp_says, "sat down at table %s" % self.tid)
                           + " Accept their challenge to play.")

    async def names(self):
        """The names at my table by seat; self.says, what each plays with (LST clients)."""
        data, final = await self.req("LST table=%s clients" % self.tid)
        for t in parse_tables(data):
            if t["id"] == self.tid:
                self.says = t.get("clients", {})
                return t["seated"]
        self.says = {}
        return []

    async def mac_choice(self, text):
        """The Mac's number (code 9): the member answers so that the core's seat 0 moves first."""
        if self.state != "starting" or self.opp is None or not text.lstrip("-").isdigit():
            return
        mine = answer_choice(int(text), self.seat == 0)
        await self.msg(self.opp, CHOOSE, "I choose : (%d)" % mine, to=self.nick)

    async def mac_move(self, m, hole_text):
        """The Mac's move (code 6, field 1 the opponent, field 2 the hole); hole -1 begins its game."""
        if m is None or m is not self.opp or not self.tid or not hole_text.lstrip("-").isdigit():
            return
        hole = int(hole_text)
        if hole < 0:
            if self.state == "starting":
                self.state, self.on = "playing", True
                await self.say("Your game with %s at table %s: %s first." % (
                    m.name, self.tid, "you move" if self.seat == 0 else m.name + " moves"))
                queue, self.queue = self.queue, []
                for item in queue:
                    await self.deliver(item)
            return
        if self.state == "over":
            log(self.sid, "the Mac's move after the end (hole %d): not sent" % hole)
            return
        if self.state != "playing" or not 0 <= hole <= 5:
            return
        _, final = await self.req("MOV %s %d" % (self.tid, hole + 1))
        if not final.startswith("OK"):
            log(self.sid, "move refused: %s" % final)
            await self.say("The server refused that move (%s). Finish the game on the web or by telnet: "
                           "table %s." % (err_text(final), self.tid))
            self.state = "over"

    async def mac_ended(self, m, reason_text):
        """The Mac ended its game (code 10): Cancel game resigns the table; after the core's end it only
        closes the game here."""
        if m is None or m is not self.opp:
            return
        reason = int(reason_text) if reason_text.lstrip("-").isdigit() else 0
        if self.state in ("starting", "playing"):
            if reason == ABANDONED:
                _, final = await self.req("RES %s" % self.tid)
                log(self.sid, "the Mac abandoned table %s: %s" % (self.tid, final))
                await self.say("You resigned table %s." % self.tid if final.startswith("OK")
                               else "Can't resign table %s: %s" % (self.tid, err_text(final)))
            else:
                log(self.sid, "the Mac ended table %s (reason %d) before the server" % (self.tid, reason))
                await self.say("Your game goes on at the server: table %s. Finish it on the web or by telnet, "
                               "or !resign %s." % (self.tid, self.tid))
        await self.game_over()

    async def mac_free(self):
        """The Mac says it plays nobody (code 8): after a game, the member is free again."""
        if self.state == "over":
            await self.game_over()
        elif self.state in ("starting", "playing"):
            log(self.sid, "the Mac left its game at table %s" % self.tid)
            await self.say("Your game goes on at the server: table %s." % self.tid)
            await self.game_over()

    async def game_over(self):
        m = self.opp
        log(self.sid, "table %s over here" % self.tid)
        self.reset()
        if m is not None:
            await self.msg(m, FREE, "I'm not playing for the moment.")

    async def forward(self, item):
        """An opponent's move or the end: to the Mac once its game is on, else kept until then."""
        if self.on:
            await self.deliver(item)
        else:
            self.queue.append(item)

    async def deliver(self, item):
        if item[0] == "move":
            _, seat, pit, n, b = item
            await self.opp_move(seat, pit - 1, n, b)
        elif item[0] == "end":
            await self.ended_on_core(item[1])

    async def opp_move(self, seat, hole, n, b):
        mine, theirs, caught, lost = rows(b, seat)
        await self.msg(self.opp, MOVE, "I play against %s (%s) MY hole #(%d):(%s)" % (
            clean(self.mac_name or "", 31), self.nick, hole,
            board(n, self.seat != 0, mine, theirs, caught, lost, hole)))

    async def ended_on_core(self, a):
        """The core's END: tell the Mac (module docstring), and the result in the chat box."""
        if self.state == "over":
            return
        self.state = "over"
        data, final = await self.req("VUE %s" % self.tid)
        b = parse_view(data)["b"] or [0] * 14
        me, opp = b[self.seat * 7 + 6], b[(1 - self.seat) * 7 + 6]
        why = a[0] if a else ""
        if why in ("resign", "timeout") and len(a) > 1 and a[1].isdigit():
            loser = int(a[1])
            if loser != self.seat:
                await self.ended_msg(ABANDONED, LOST)
            elif why == "timeout":
                await self.ended_msg(HE_ABANDONED, WON)
            await self.say("%s %s at table %s: %s won." % (
                "You" if loser == self.seat else self.opp.name, "resigned" if why == "resign" else "ran out of time",
                self.tid, self.opp.name if loser == self.seat else "you"))
            return
        elif self.stuck and self.last_sow is not None:
            # The player to move cannot feed the other. Awale has that player move once more (a sowing that
            # cannot reach the other side) and then ends the game by itself, with the same totals.
            mover, before = self.last_sow
            if mover == self.seat:          # the opponent is stuck: its last move, on the board before the sweep
                o = 1 - self.seat
                hole = next((i for i, c in enumerate(before[o * 7:o * 7 + 6]) if c), None)
                if hole is not None:
                    await self.last_move(before, o, hole)
            text = "Neither side can feed the other."
        else:
            await self.ended_msg(DEADLOCK, WON if opp > me else LOST if me > opp else DRAWN)
            text = "The server ended the game; Awale shows it as a deadlock."
        result = "you won" if me > opp else "%s won" % self.opp.name if opp > me else "a draw"
        await self.say("%s Final score %d to %d at table %s: %s." % (text, me, opp, self.tid, result))

    async def last_move(self, b, seat, hole):
        """The stuck opponent's last sowing, which stays on its own row, so that the Mac ends the game."""
        cells = list(b[seat * 7:seat * 7 + 6])
        n = cells[hole]
        cells[hole] = 0
        for i in range(hole + 1, hole + 1 + n):
            cells[i % 6] += 1
        after = list(b)
        after[seat * 7:seat * 7 + 6] = cells
        await self.opp_move(seat, hole, self.sows + 1, after)

    async def ended_msg(self, reason, result):
        await self.msg(self.opp, ENDED, "The game with %s (%s) ended because : (%d) %s Result : (%d) %s" % (
            clean(self.mac_name or "", 31), self.nick, reason, REASON_TEXT[reason], result, RESULT_TEXT[result]))


class Console(chatcmd.Console):
    """The chat box's commands (the shared set, typed with !), on Oware tables."""

    def __init__(self, s):
        self.s = s
        super().__init__(s.say, s.req, lambda: s.name, COMMANDS, kinds=[MANCALA], label="Mancala", new=RULES,
                         notes=NOTES, handlers={"signin": s.do_signin, "quit": s.do_quit}, seated=s.seated,
                         width=0, compact=True, current=lambda: s.tid if s.started else None)

    async def handle(self, text):
        cmd = chatcmd.parse(text)
        if cmd is not None and cmd.name in ("open", "invite", "join", "accept") and self.s.tid:
            await self.say("You have a table already (%s)." % self.s.tid if not self.s.started
                           else "You are playing a game now.")
            return True
        return await super().handle(text)

    async def sit(self, tid, seat, opp, how):
        """Only an Oware table: Awale plays no other."""
        data, final = await self.req("VUE %s" % tid)
        rules = parse_view(data)["rules"] if final.startswith("OK") else ""
        if rules and rules != RULES:
            await self.say("Table %s plays %s rules; Awale plays Oware rules only." % (tid, rules))
            return None
        return await super().sit(tid, seat, opp, how)

    def waiting_table(self, tables, name=None, tid=None):
        t = super().waiting_table(tables, name, tid)
        return t if t is None or t.get("rules") == RULES else None

    async def new_table(self, vis):
        """As the default, with this extension's time per turn and the Oware rules."""
        _, final = await self.req("NEW %s %s turn=%s%s" % (MANCALA, RULES, self.s.svc.turn,
                                                            " vis=private" if vis == "private" else ""))
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


class Service:
    def __init__(self, args):
        self.args = args
        self.core_addr = (args.core_host, args.core_port)
        self.poll = args.poll
        self.lobby_poll = args.lobby_poll
        self.ping = args.ping
        self.turn = args.turn
        self.trace = args.trace


async def serve(args):
    svc = Service(args)
    gate = Gate(MAX_SESSIONS)
    gate.notice = b"ERROR :Server is full, try again soon.\r\n"

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
            print("awale session error: %r" % e, file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc()
        finally:
            ticket.release()
            log(s.sid, "disconnect")
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("awale adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.awale")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6515)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    ap.add_argument("--turn", default="1d", help="time per turn at the tables this extension makes")
    declare.add_args(ap)
    ap.add_argument("--poll", type=float, default=POLL_SECS, help=argparse.SUPPRESS)              # tests
    ap.add_argument("--lobby-poll", type=float, default=LOBBY_POLL_SECS, help=argparse.SUPPRESS)  # tests
    ap.add_argument("--ping", type=float, default=PING_SECS, help=argparse.SUPPRESS)              # tests
    ap.add_argument("--trace", action="store_true",
                    help="log every IRC line (a password typed with !signin is left out)")
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
