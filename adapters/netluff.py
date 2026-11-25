"""NetLuff extension: ParlorNet's Five in a Row for NetLuff 1.4 on the Mac.

ParlorNet is not affiliated with NetLuff's author. NetLuff (Henrik Linder, Sweden, 1998; a fat application for
68030 and PowerPC Macs with Open Transport) plays five in a row between two Macs over TCP: one copy clicks "Wait
for connection" and the other types its address and clicks Connect. This extension is the waiting copy. The
player types the parlor's name or address in NetLuff's "Remote IP here" box and clicks Connect. NetLuff has no
port setting and always connects to TCP port 1744, so the parlor publishes this extension's port (6517) as 1744
as well (deploy/install.sh), and declares 1744 to the core (catalog.PUBLISHED). The protocol was read from the
program's PowerPC code and from games between two copies and against a scripted peer.

The wire. Messages are ASCII with a two-letter tag and no separator; a move has no tag:

    ip NN text          the sender's address and program: "10.0.2.15, running NetLuff 1.4.0" (NN: 2 digits)
    sa d                who moves first, sent by the connecting copy: d 1 gives the receiver the move, 0 not
    nm NNN name         the player's name (NNN: 3 digits), sent on connecting and whenever it changes
    ms NNN text         a chat line as shown: "name: text", or "name waves" for /me waves
    ccrr                a move: column and row, 00 to 19, from the top left
    wn ccrr x5          the sender's move just made five or more in a row: the first five points of it; the
                        sender then closes the connection, and the receiver shows the line and "Loser!"
    pn / pr             ping and its answer, sent by the program by itself
    pe                  page: a sound on the other Mac
    qt                  the sender disconnects ("Other player left.")

NetLuff reads a message as its parts come in (Read(2), then the digits, then the text), so each batch here goes
out in one write. A tag it does not know, with more bytes after it, opens an alert ("Unknown request").

The session. On connecting NetLuff sends ip, sa and nm. The extension answers with its own ip ("ParlorNet"),
nm ("ParlorNet" until there is an opponent) and sa 0, which takes the move away from the Mac: its board stays
idle until a game starts. The chat box then greets the player. Lines from the server start with "*** ", as
NetLuff's own notes do.

Sign-in. NetLuff sends the player's name but nothing secret, so the name is never enough: the player types
/signin PASSWORD in the chat box, which signs in the name from NetLuff's name box, or /signin NAME PASSWORD.
The name goes to the core alone, and the password answers its credential question (common.sign_in_lines).
Under the sign-in method none, /signin alone signs in NetLuff's name. A password the server mailed cannot be
used here: its first sign-in is on the web or by telnet, where the player chooses their own. The password
shows in the Mac's own chat box as typed, as every line does there; it goes to the server only.

The lobby. The chat box takes the shared chat commands (adapters/chatcmd.py, docs/chat-commands.md): /help,
/who, /list, /open, /join TABLE or NAME, /invite NAME, /invite bot, /accept NAME, /decline NAME, /close,
/resign, /motd and /quit. Invitations from anywhere are announced in the chat, with a page (pe). Lines that are
not commands go to the table's chat once there is a table, and the opponent's chat comes back as "name: text".

The game. NetLuff's board is 20 by 20, and a line of five or more wins (an overline wins: NetLuff checks for
five in a row through the stone just played and reports the first five). So its tables are fiveinarow/1 with
the freestyle ruleset and size=20, made here. An open table's options are not in its view, so the extension
keeps a mark with each table it makes (TDS netluff), and a Mac sits down only at a table with the mark. The core decides
who moves first (seat 0): when the game starts, the Mac gets sa 1 if it is seat 0, and its window title becomes
"you vs. opponent" (nm). Each move from the Mac is played on the core (MOV); the opponent's moves go to the Mac.
NetLuff checks only its own player's lines, so when the opponent makes five the extension sends their move, a
line in the chat and wn with the first five points of the core's winning line: the Mac draws the line, shows
"Loser!" and hangs up. When the Mac's player makes five, NetLuff sends the move and wn and hangs up; the core
ends the game on the move. Resigning (/resign), the turn clock and a full board end a game in the chat: the Mac
gets sa 0 and the result, and "Disconnect, then Connect" to play again. NetLuff clears its board only when it
connects, so each connection plays one game, as between two copies of the program. A Mac that goes away while
its game runs resigns it, since NetLuff cannot show a game in progress again.

A move the core does not take (it can only happen in a race with the clock, say) cannot be taken back on the
Mac: the player is told, and the game goes on on the web or by telnet.

Silence. NetLuff sends nothing while a player thinks. A proxy on the way may reset a connection that has carried
nothing for about 150 seconds (Docker's userland proxy probes it with TCP keepalive, which a Mac does not
answer), so after IDLE_PING_SECS (45; --idle-ping) with nothing either way the extension sends pn, and NetLuff
answers pr by itself without showing anything (unless its "Display ping requests" box is checked).

Left out: NetLuff's page (pe from the Mac is not relayed), watching from the Mac, resuming a game, a second game
on one connection, other board sizes and the standard ruleset.

    python3 -m adapters.netluff --core 127.0.0.1:6590 --key netluff.key --port 6517 --public-port 1744
"""
import argparse
import asyncio
import itertools
import re
import sys
import time
import unicodedata

from . import chatcmd, declare
from .clients import BY_ID
from .common import (LOGIN_TOTAL_SECS, NEWPASS_TEXT, CoreClient, CoreError, Gate, describe_invite_event, err_text,
                     keepalive, no_wait, parse_invites, peer_addr, sign_in_lines)
from .games import fiveinarow as F

# What the core records with every turn the Mac plays (CLI): the program's name in the clients catalog, with
# the version its ip message claims.
CLIENT_NAME = BY_ID["netluff"].name         # "NetLuff"

PROTOCOL = ("NETLUFF", "NetLuff, for the Mac's NetLuff 1.4", "Five in a Row for NetLuff 1.4 on Macs")
FIVE = "fiveinarow/1"
SIZE = 20
RULES = "freestyle"
NEW_OPTS = "freestyle size=%d" % SIZE       # what follows NEW fiveinarow/1 for a table made here
TABLE_MARK = ("netluff", "20 freestyle")     # kept with each table made here (TDS): NetLuff can play it
NETLUFF_PORT = 1744                         # the port NetLuff always connects to
MAX_SESSIONS = 40
HELLO_SECS = 30.0           # NetLuff's ip, sa and nm after connecting
POLL_SECS = 1.0             # the table's events
INVITE_SECS = 3.0           # IVL, while signed in and not playing
LOBBY_SECS = 1800.0         # signed in, no game and no typing: the connection closes
AFTER_SECS = 600.0          # after a game that ends in the chat, the connection stays this long
IDLE_PING_SECS = 45.0       # nothing either way this long: pn (--idle-ping; 0: never)
HOST_NAME = "ParlorNet"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
COMMANDS = ["list", "resign", "invite", "open", "join", "close", "accept", "decline", "who", "tables", "top",
            "score", "note", "motd", "time", "signin", "quit", "help"]
BEFORE_SIGNIN = ("signin", "help", "motd", "quit")
NOTES = ["NetLuff plays 20 by 20 freestyle tables: five or more in a row wins.", "/invite bot plays the bot."]
AGAIN = "To play again, click Disconnect, then Connect."
ids = itertools.count(1)


def log(sid, text):
    print("netluff %d: %s" % (sid, text), file=sys.stderr, flush=True)


# ---- the wire ---------------------------------------------------------------------------------------------

def mac_text(text):
    return text.encode("mac_roman", "replace")


def ip(text):
    b = mac_text(text)[:99]
    return b"ip%02d" % len(b) + b


def nm(name):
    b = mac_text(name)[:999]
    return b"nm%03d" % len(b) + b


def ms(text):
    b = mac_text(text)[:999]
    return b"ms%03d" % len(b) + b


def sa(mine):
    return b"sa1" if mine else b"sa0"


def move(x, y):
    return b"%02d%02d" % (x, y)


def wn(points):
    """The first five points of a winning line, from one end."""
    return b"wn" + b"".join(move(x, y) for x, y in points[:5])


PN, PR, PE, QT = b"pn", b"pr", b"pe", b"qt"
PLAIN = {b"pn": "pn", b"pr": "pr", b"pe": "pe", b"qt": "qt"}


class Reader:
    """NetLuff's byte stream -> messages: ("ip", text), ("sa", digit), ("nm", text), ("ms", text), ("move",
    (x, y)), ("wn", [(x, y)] * 5), ("pn"|"pr"|"pe"|"qt", None), and ("junk", bytes) for anything else, after
    which the rest of what has come is dropped (nothing marks where the next message starts)."""

    def __init__(self):
        self.buf = b""

    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= 2:
            b, tag = self.buf, self.buf[:2]
            need, msg = 0, None
            if tag in PLAIN:
                need, msg = 2, (PLAIN[tag], None)
            elif tag == b"sa":
                need = 3
                if len(b) >= 3:
                    msg = ("sa", int(b[2:3])) if b[2:3].isdigit() else ("junk", b)
            elif tag == b"ip":
                if len(b) >= 4:
                    if not b[2:4].isdigit():
                        msg, need = ("junk", b), len(b)
                    else:
                        need = 4 + int(b[2:4])
                        msg = ("ip", b[4:need].decode("mac_roman"))
                else:
                    need = 4
            elif tag in (b"nm", b"ms"):
                if len(b) >= 5:
                    if not b[2:5].isdigit():
                        msg, need = ("junk", b), len(b)
                    else:
                        need = 5 + int(b[2:5])
                        msg = (tag.decode(), b[5:need].decode("mac_roman"))
                else:
                    need = 5
            elif tag == b"wn":
                need = 22
                if len(b) >= 22:
                    pts = b[2:22]
                    msg = ("wn", [(int(pts[i:i + 2]), int(pts[i + 2:i + 4])) for i in range(0, 20, 4)]) \
                        if pts.isdigit() else ("junk", b)
            elif tag.isdigit():
                need = 4
                if len(b) >= 4:
                    msg = ("move", (int(b[0:2]), int(b[2:4]))) if b[2:4].isdigit() else ("junk", b)
            else:
                msg, need = ("junk", b), len(b)
            if len(b) < need or msg is None:
                break
            if msg[0] == "junk":
                out.append(msg)
                self.buf = b""
                break
            out.append(msg)
            self.buf = b[need:]
        return out


def point(x, y):
    """NetLuff's square (column, row from 0) -> a fiveinarow/1 point: (0, 0) is a1, (19, 19) is t20."""
    return F.sq(x, y)


def core_text(text):
    """Chat for the core: printable ASCII, accents taken off, the rest as '?'."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^ -~]", "?", plain).strip()


def program_claim(ip_text):
    """NetLuff's ip text ("10.0.2.15, running NetLuff 1.4.0") -> the program and version it claims."""
    m = re.search(r"running NetLuff ([0-9][0-9.]{0,10})\s*$", ip_text or "")
    return "%s %s" % (CLIENT_NAME, m.group(1)) if m else CLIENT_NAME


def fits(v):
    """A fiveinarow/1 view's geometry: whether NetLuff can play the table (20 by 20, freestyle)."""
    return v["n"] == SIZE and v["rules"] == RULES


class Session:
    def __init__(self, svc, reader, writer, ticket=None):
        self.svc, self.r, self.w, self.ticket = svc, reader, writer, ticket
        self.sid = next(ids)
        self.addr = peer_addr(writer)
        self.wire = Reader()
        self.queue = []             # the Mac's messages read and not yet handled
        self.core = None
        self.core_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.name = None            # signed in as
        self.nick = ""              # NetLuff's name box, as its nm says
        self.program = CLIENT_NAME
        self.console = None
        self.t0 = self.last_typed = time.monotonic()
        self.tid = None
        self.seat = None
        self.opp = None
        self.cursor = 0
        self.started = False
        self.over = False
        self.over_at = 0.0
        self.won_on_mac = False     # the Mac's wn came (it hangs up after it)
        self.hung_up = False        # our wn went out: the Mac hangs up on it, so nothing more is sent
        self.dirty = False          # the Mac's board has a stone the server does not
        self.broken = False         # a move the core did not take: the Mac is out of step
        self.closing = False
        self.announced = {}         # invitations said in the chat: table -> inviter
        self.sent = self.heard = time.monotonic()
        self.pinged = 0.0

    # ---- output -----------------------------------------------------------------------------------------
    async def send(self, *parts, quiet=False):
        """Messages to the Mac, in one write (NetLuff reads a message as its parts arrive)."""
        data = b"".join(parts)
        if not data or self.hung_up:
            return
        async with self.send_lock:
            self.w.write(data)
            if not quiet:
                self.sent = time.monotonic()
            await self.w.drain()

    def lines(self, lines):
        if isinstance(lines, str):
            lines = [lines]
        return [ms("*** " + l) for l in lines if l is not None]

    async def say(self, lines):
        """Lines in the Mac's chat box, from the server."""
        await self.send(*self.lines(lines))

    # ---- the core ---------------------------------------------------------------------------------------
    async def req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    # ---- the session ------------------------------------------------------------------------------------
    async def next_message(self, timeout=None):
        """The Mac's next message, or None when it has gone; asyncio.TimeoutError after timeout seconds."""
        while not self.queue:
            read = self.r.read(4096)
            data = await (read if timeout is None else asyncio.wait_for(read, timeout))
            if not data:
                return None
            self.heard = time.monotonic()
            if self.ticket is not None and self.name is None:
                self.ticket.touch()
            self.queue.extend(self.wire.feed(data))
        return self.queue.pop(0)

    async def run(self):
        hello = []
        try:
            while not any(k == "nm" for k, _ in hello):
                m = await self.next_message(HELLO_SECS if not hello else 3.0)
                if m is None:
                    return
                hello.append(m)
        except asyncio.TimeoutError:
            if not hello:
                return
        if hello[0][0] != "ip":
            log(self.sid, "not NetLuff: %r" % (hello[0],))
            return
        for m in hello:
            await self.on_message(m)
        log(self.sid, "hello: %s, name %r" % (self.program, self.nick))
        try:
            self.core = await CoreClient.connect(*self.svc.core_addr)
            await self.core.source(self.addr)
            await self.core.claim(self.program, "Macintosh")     # the turns say what they were played with
        except (OSError, asyncio.TimeoutError, CoreError) as e:
            self.core = None
            log(self.sid, "the core is not answering: %s" % e)
            await self.send(ip(HOST_NAME), nm(HOST_NAME), sa(False),
                            *self.lines("The game server is not answering. Try again soon."))
            await asyncio.sleep(1)
            return
        await self.send(ip(HOST_NAME), nm(HOST_NAME), sa(False))
        await self.greet()
        poller = asyncio.ensure_future(self.poll())
        try:
            while not self.closing:
                m = await self.next_message()
                if m is None:
                    break
                await self.on_message(m)
        finally:
            poller.cancel()
            await self.hang_up()

    async def greet(self):
        motd = []
        try:
            motd = await self.core.motd()
        except CoreError:
            pass
        lines = ["Welcome to ParlorNet: Five in a Row for NetLuff."] + motd[:6]
        if len(motd) > 6:
            lines.append("(/motd shows the whole message.)")
        lines.append("/help lists the commands.")
        if self.core.auth == "none":
            lines.append("To sign in, type /signin" + ("" if NAME_RE.match(self.nick) else " NAME"))
        elif NAME_RE.match(self.nick):
            lines.append("To sign in as %s, type /signin and your password." % self.nick)
        else:
            lines.append("To sign in, type /signin NAME PASSWORD")
        await self.say(lines)

    async def hang_up(self):
        """The Mac went away: a running game is resigned (NetLuff cannot show it again); an open table is
        left."""
        if self.core is None:
            return
        try:
            if self.name and self.tid and self.started and not self.over:
                v = await self.view()
                if v["turn"] >= 0:
                    await self.req("RES %s" % self.tid)
                    log(self.sid, "resigned table %s: the Mac went away" % self.tid)
            elif self.name and self.tid and not self.started:
                await self.req("LVE %s" % self.tid)
            if self.name:
                await self.core.out()
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.core.close()

    async def on_message(self, m):
        kind, value = m
        if kind == "ip":
            self.program = program_claim(value)
        elif kind == "nm":
            self.nick = value.strip()
        elif kind == "ms":
            await self.chat(value)
        elif kind == "move":
            await self.mac_move(*value)
        elif kind == "wn":
            self.won_on_mac = True
            log(self.sid, "the Mac reports five in a row: %s" % " ".join(point(*p) for p in value))
        elif kind == "pn":
            await self.send(PR, quiet=True)
        elif kind == "qt":
            log(self.sid, "the Mac disconnects")
            self.closing = True
        elif kind == "junk":
            log(self.sid, "not understood: %r" % value[:40])
        # sa (the Mac's own pick of who starts: the core decides), pr and pe need nothing

    # ---- chat and commands ------------------------------------------------------------------------------
    def typed(self, line):
        """A chat line as NetLuff sends it -> (what the player typed, an emote): "name: text" is text, and
        "name text" (/me text) an emote."""
        for nick in [n for n in (self.nick, self.name) if n]:
            if line.startswith(nick + ": "):
                return line[len(nick) + 2:], False
            if line.startswith(nick + " "):
                return line[len(nick) + 1:], True
        head, sep, rest = line.partition(": ")
        if sep and " " not in head:
            return rest, False
        return line, True

    async def chat(self, line):
        self.last_typed = time.monotonic()
        text, emote = self.typed(line)
        text = text.strip()
        if not text:
            return
        cmd = None if emote else chatcmd.parse(text)
        if cmd is not None:
            log(self.sid, "command /%s" % cmd.name)
        try:
            if self.console is None:
                if cmd is None:
                    await self.say("Sign in first: /signin and your password.")
                elif cmd.name == "signin":
                    await self.do_signin(cmd)
                elif cmd.name == "help":
                    await self.say(chatcmd.help_lines(BEFORE_SIGNIN))
                elif cmd.name == "motd":
                    await self.say((await self.core.motd()) or "There is no message of the day.")
                elif cmd.name == "quit":
                    await self.do_quit(cmd)
                else:
                    await self.say("Sign in first: /signin and your password.")
                return
            self.core.user_input()
            if cmd is not None and await self.console.handle(text):
                return
            if self.tid:
                said = core_text(("* " + text) if emote else text)
                _, final = await self.req("CHT %s %s" % (self.tid, said[:56]))
                if not final.startswith("OK"):
                    await self.say("Your chat did not go: %s" % err_text(final))
            else:
                await self.say("Chat reaches your opponent once you have a table. /help for the commands.")
        except (CoreError, OSError, asyncio.TimeoutError) as e:
            log(self.sid, "command failed: %s" % e)
            await self.say("The server is not answering just now.")

    async def do_signin(self, cmd):
        if self.name:
            await self.say("You are signed in as %s." % self.name)
            return
        args = cmd.args
        if self.core.auth == "none":
            name, password = (args[0] if args else self.nick), None
        elif len(args) >= 2:
            name, password = args[0], " ".join(args[1:])
        elif len(args) == 1:
            name, password = self.nick, args[0]
        else:
            await self.say("Type /signin and your password.")
            return
        if not NAME_RE.match(name or ""):
            await self.say("%s is not a ParlorNet name: type /signin NAME PASSWORD" % (name or "Your name")
                           if name else "Type /signin NAME PASSWORD")
            return
        shown = []

        async def keep(lines):
            shown.extend(lines)
        why = await sign_in_lines(self.core, name, password, None, keep, "signin",
                                  "There is no account %s here. Make one on the web site first." % name)
        if why == NEWPASS_TEXT:     # a mailed password: the chat box cannot ask for a new one
            log(self.sid, "sign-in refused: a new password is due (the web or telnet)")
            await self.say(why)
            return
        if shown:
            await self.say(shown)
        if why:
            log(self.sid, "sign-in refused")
            await self.say(no_wait(why))
            return
        if self.ticket is not None and not self.ticket.sign_in():
            await self.say("The server is full. Try again soon.")
            self.closing = True
            return
        self.name = self.core.name or name
        log(self.sid, "signed in as %s" % self.name)
        self.core.user_input()
        self.console = Console(self)
        await self.say(["Signed in as %s." % self.name, "/invite bot plays the bot. /help for more."])
        await self.check_invitations()

    async def do_quit(self, cmd):
        await self.send(*self.lines("Goodbye."), QT)
        self.closing = True

    async def check_invitations(self):
        data, final = await self.req("IVL")
        if not final.startswith("OK"):
            return
        invs = parse_invites(data)
        live = {i["id"] for i in invs}
        for tid in [t for t in self.announced if t not in live]:
            del self.announced[tid]
        page = False
        for i in invs:
            if i["id"] in self.announced:
                continue
            self.announced[i["id"]] = i["from"]
            if i["key"] == FIVE:
                await self.say("%s invites you to Five in a Row: /accept %s" % (i["from"], i["from"]))
                page = True
            else:
                await self.say("%s invites you to another game; NetLuff plays Five in a Row only." % i["from"])
        if page:
            await self.send(PE)

    # ---- the table ----------------------------------------------------------------------------------------
    async def seated(self, tid, seat, opp, how):
        self.tid, self.seat, self.opp, self.cursor = tid, seat, opp, 0
        log(self.sid, "seated at table %s, seat %d (%s)" % (tid, seat, how))

    @property
    def oseat(self):
        return 1 - self.seat

    async def view(self, flags="", tid=None):
        tid = tid or self.tid
        data, final = await self.req("VUE %s %s" % (tid, flags) if flags else "VUE %s" % tid)
        if not final.startswith("OK"):
            raise CoreError(err_text(final))
        return F.parse_view(data)

    async def poll(self):
        last_ivl = 0.0
        while not self.closing:
            await asyncio.sleep(self.svc.poll)
            try:
                now = time.monotonic()
                if self.name is None:
                    if now - self.t0 > LOGIN_TOTAL_SECS:
                        log(self.sid, "signing in took too long")
                        await self.send(*self.lines("Signing in took too long. Connect again to try again."), QT)
                        self.stop()
                elif self.tid:
                    await self.table_events()
                if self.name and not self.tid and not self.over and now - self.last_typed > LOBBY_SECS:
                    await self.send(*self.lines("Nothing happened for a long time; goodbye."), QT)
                    self.stop()
                if self.name and not self.started and not self.closing and now - last_ivl >= INVITE_SECS:
                    last_ivl = now
                    await self.check_invitations()
                if self.over and now - self.over_at > AFTER_SECS and not self.closing:
                    await self.send(QT)
                    self.stop()
                if not self.closing and self.svc.idle_ping and now - max(self.sent, self.heard, self.pinged) \
                        >= self.svc.idle_ping:
                    self.pinged = now
                    await self.send(PN, quiet=True)
            except (CoreError, OSError, asyncio.TimeoutError) as e:
                log(self.sid, "poll: %s" % e)
                self.stop()
            except Exception as e:
                log(self.sid, "poll error: %r" % e)
                import traceback
                traceback.print_exc()
                self.stop()

    def stop(self):
        self.closing = True
        try:
            self.w.close()
        except Exception:
            pass

    async def table_events(self):
        data, final = await self.req("SIN %s %d" % (self.tid, self.cursor))
        if final.startswith("ERR"):
            if not self.started:
                self.tid = None
                await self.say("Your table is gone. /open or /invite to start another.")
            return
        evs = []
        for l in data:
            p = l.split(" ", 4)
            if len(p) < 4 or p[0] != "EVT" or not p[2].isdigit() or int(p[2]) <= self.cursor:
                continue
            evs.append((int(p[2]), p[3], p[4] if len(p) > 4 else ""))
        says = chatcmd.join_says(["%s %s" % (kind, rest) for _, kind, rest in evs])
        i = 0
        while i < len(evs):
            seq, kind, rest = evs[i]
            self.cursor = seq
            a = rest.split()
            change = chatcmd.change_of(kind, a, says, 1 - self.oseat)
            if kind == "JOIN" and len(a) >= 2 and a[0] == str(self.oseat):
                self.opp = a[1]
                if not self.started:
                    await self.say(chatcmd.joined(a[1], says.get(self.oseat, "")))
            elif change and change[0] == self.oseat:     # the opponent plays on with another program
                await self.say(chatcmd.client_change(self.opp or "Your opponent", change[1]))
            elif kind == "LEAVE" and a[:1] == [str(self.oseat)] and not self.started:
                await self.say("%s stood up." % (self.opp or "Your opponent"))
            elif kind in ("INVITE", "UNINVITE") and not self.started:
                text = describe_invite_event(" ".join([kind] + a))
                if text and kind == "UNINVITE":
                    await self.say(text[0].upper() + text[1:] + ".")
            elif kind == "START" and not self.started:
                await self.start()
            elif kind == "CHT" and len(a) >= 2 and a[0] == str(self.oseat):
                said = rest.split(" ", 1)[1][:200]
                who = self.opp or "Opponent"        # "* waves" (another Mac's /me) as NetLuff shows /me
                await self.send(ms("%s %s" % (who, said[2:]) if said.startswith("* ") else "%s: %s" % (who, said)))
            elif kind == "PUT" and len(a) >= 2 and a[0] == str(self.oseat) and self.started:
                group = []
                while i + 1 < len(evs) and evs[i + 1][1] in ("TURN", "END"):
                    i += 1
                    self.cursor = evs[i][0]
                    group.append((evs[i][1], evs[i][2].split()))
                await self.opp_move(a[1], dict(group).get("END"))
            elif kind == "END":
                await self.ended(a)
            i += 1

    async def start(self):
        """The game starts: the window takes the opponent's name, and the Mac gets the move when it is seat
        0."""
        v = await self.view("G")
        if not fits(v):         # a table that changed under us: never seated here (Console.sit checks)
            await self.say("NetLuff cannot play table %s: it is not 20 by 20 freestyle." % self.tid)
            return
        names = await self.names()
        self.opp = names[self.oseat] if len(names) > self.oseat else self.opp or "opponent"
        self.started = True
        first = self.seat == 0          # seat 0 moves first; the opponent's stones come as their PUT events
        log(self.sid, "game on table %s with %s, seat %d" % (self.tid, self.opp, self.seat))
        if v["order"][self.seat]:       # a game with stones of the Mac's own: NetLuff cannot show them
            self.broken = True
            await self.say(["Table %s already has your stones, and NetLuff cannot show them." % self.tid,
                            "Play it on the web or by telnet."])
            return
        await self.send(nm(self.opp[:31]), *self.lines(
            ["Your game with %s on table %s: %s." % (self.opp, self.tid,
                                                    "you move first" if first else "%s moves first" % self.opp),
             "Five or more in a row wins."]), sa(first), PE)

    async def names(self):
        data, final = await self.req("LST table=%s" % self.tid)
        for l in data:
            p = l.split()
            if len(p) >= 7 and p[0] == "TAB" and p[1] == self.tid:
                return p[6].split(",")
        return []

    # ---- moves --------------------------------------------------------------------------------------------
    async def opp_move(self, pt, end):
        """The opponent's stone -> the Mac; with their five, the line in the chat and wn after it (the Mac
        hangs up on wn, so the chat goes first)."""
        p = F.pt(pt)
        if p is None or self.broken:
            return
        parts = [move(*p)]
        if end and end[:1] == ["win"] and end[1:2] == [str(self.oseat)]:
            v = await self.view()
            line = v["five"]
            self.over, self.over_at = True, time.monotonic()
            log(self.sid, "table %s: %s made five" % (self.tid, self.opp))
            parts += self.lines("%s made %s in a row: %s wins." % (
                self.opp, F.COUNT_WORDS.get(len(line), str(len(line))), self.opp))
            parts.append(wn(line))
            await self.send(*parts)
            self.hung_up = True
            return
        elif end:
            await self.send(*parts)
            await self.ended(end)
            return
        await self.send(*parts)

    async def mac_move(self, x, y):
        if not (0 <= x < SIZE and 0 <= y < SIZE):
            log(self.sid, "a move off the board: %d %d" % (x, y))
            return
        if self.broken or self.over:
            log(self.sid, "a move after the game: %s" % point(x, y))
            return
        if not (self.tid and self.started):
            self.dirty = True
            log(self.sid, "a move with no game: %s" % point(x, y))
            await self.say(["That stone is not on the server: there is no game yet.",
                            "Click Disconnect, then Connect, to clear the board."])
            return
        _, final = await self.req("MOV %s %s" % (self.tid, point(x, y)))
        log(self.sid, "from the Mac: %s (%s)" % (point(x, y), final))
        if not final.startswith("OK"):
            self.broken = True
            await self.send(*self.lines(["The server did not take %s: %s." % (point(x, y), err_text(final)),
                                         "NetLuff cannot take a stone back, so finish this game on the web "
                                         "or by telnet."]), sa(False))

    async def ended(self, a):
        """A game that ends in the chat: a resignation, the turn clock or a full board (a five is wn's)."""
        if self.over:
            return
        self.over, self.over_at = True, time.monotonic()
        why = a[0] if a else ""
        seat = int(a[1]) if len(a) > 1 and a[1].isdigit() else -1
        if why == "win":
            text = "You made five in a row: you win." if seat == self.seat else "%s wins." % self.opp
        elif why == "resign":
            text = "You resigned." if seat == self.seat else "%s resigned: you win." % self.opp
        elif why == "timeout":
            text = "Your time ran out: %s wins." % self.opp if seat == self.seat else \
                "%s's time ran out: you win." % self.opp
        elif why == "draw":
            text = "The board is full: a draw."
        else:
            text = "The game is over."
        log(self.sid, "table %s over: %s" % (self.tid, " ".join(a)))
        if not self.won_on_mac:
            await self.send(sa(False), *self.lines([text, AGAIN]))


class Console(chatcmd.Console):
    """The lobby's chat commands (the shared set), on fiveinarow/1 tables NetLuff can play."""

    def __init__(self, s):
        self.s = s
        super().__init__(s.say, s.req, lambda: s.name, COMMANDS, kinds=[FIVE], label="Five in a Row",
                         new=NEW_OPTS, notes=NOTES, handlers={"quit": s.do_quit, "signin": s.do_signin},
                         seated=s.seated, current=lambda: s.tid if s.started and not s.over else None)

    async def handle(self, text):
        cmd = chatcmd.parse(text)
        if cmd is not None and cmd.name in ("open", "invite", "join", "accept"):
            if self.s.over or self.s.dirty or self.s.broken:
                await self.say("This connection has played its game. " + AGAIN)
                return True
            if self.s.tid:
                await self.say("You have a table already (%s)." % self.s.tid if not self.s.started
                               else "You are playing a game now.")
                return True
        return await super().handle(text)

    async def sit(self, tid, seat, opp, how):
        """Only at a table made from NetLuff: an open table's options are not in its view, so this extension marks
        its own (TDS, TABLE_MARK) and reads the mark here (TDL)."""
        data, final = await self.req("TDL %s" % tid)
        if not final.startswith("OK") or "TDL %s %s" % TABLE_MARK not in data:
            await self.say(["NetLuff plays the 20 by 20 freestyle tables made from NetLuff, and table %s was made "
                            "elsewhere." % tid, "/invite %s to play them here." % (opp.split()[0] if opp else "NAME")])
            return None
        return await super().sit(tid, seat, opp, how)

    async def new_table(self, vis):
        """As the default, with this extension's time per turn, 20 by 20 and freestyle."""
        _, final = await self.req("NEW %s %s turn=%s%s" % (FIVE, NEW_OPTS, self.s.svc.turn,
                                                            " vis=private" if vis == "private" else ""))
        if not final.startswith("OK"):
            await self.say("Can't open a table: %s" % err_text(final))
            return None
        tid = final.split()[1]
        await self.req("TDS %s %s %s" % ((tid,) + TABLE_MARK))
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
        self.turn = args.turn
        self.idle_ping = args.idle_ping


async def serve(args):
    svc = Service(args)
    gate = Gate(MAX_SESSIONS)
    gate.notice = b""

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
            print("netluff session error: %r" % e, file=sys.stderr, flush=True)
        finally:
            ticket.release()
            log(s.sid, "disconnect")
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("netluff adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    global LOGIN_TOTAL_SECS, AFTER_SECS
    ap = argparse.ArgumentParser(prog="adapters.netluff")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6517)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    ap.add_argument("--turn", default="30m", help="time per turn at the tables this extension makes")
    ap.add_argument("--idle-ping", type=float, default=IDLE_PING_SECS,
                    help="seconds with nothing either way before a ping, which NetLuff answers (0: never)")
    declare.add_args(ap)
    ap.add_argument("--poll", type=float, default=POLL_SECS, help=argparse.SUPPRESS)              # tests
    ap.add_argument("--login-secs", type=float, default=LOGIN_TOTAL_SECS, help=argparse.SUPPRESS)  # tests
    ap.add_argument("--after-secs", type=float, default=AFTER_SECS, help=argparse.SUPPRESS)        # tests
    a = ap.parse_args(argv)
    LOGIN_TOTAL_SECS, AFTER_SECS = a.login_secs, a.after_secs
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
