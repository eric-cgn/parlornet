"""The NetSprocket host that every NetSprocket game on the extension's port shares (adapters/netsprocket.py).

A Mac joins with NetSprocket's join request, which carries the player name and the game password in plain
text. Join signs the player in to the core with them: the name goes to the core alone, and the password answers
its first credential question (common.sign_in_lines). A refusal is NetSprocket's join denial, which a program
may show as its own alert; the reason is logged and goes in the denial. A game that shows no denial (NetBoxes, its
refuse()) is told inside the game instead (refuse_inside). A name without an account cannot sign up
from a game, and a password the server mailed cannot be used here: its first sign-in is on the web or by
telnet, where the player chooses their own (the denial's reason says so).

After the approval, Join waits for the program's first message. NetSprocket's join request carries no game ID
on TCP, so the program is known by that message (each game's recognises(frame)), and the core is told which
program it is (CLI PROG: the program's own claim). The game's Session then takes the connection over.

GameSession holds what the games share once the program is known: sending NetSprocket messages, the core
session, the poll of the table's events, the lobby's timeout, the idle note, and going away. Silence: neither
NetSprocket nor these programs send anything while a player thinks or sits in a lobby, and a proxy on the way
may reset a connection that has carried nothing for about 150 seconds (Docker's userland proxy probes it with
TCP keepalive, which a Mac does not answer). So when no message has gone either way for idle_note seconds (45;
the setting netsprocket.idle_note_secs) a chat line goes to the Mac, and again every idle_repeat seconds (75;
netsprocket.idle_repeat_secs) while the silence lasts (idle_note). The Mac's TCP acknowledges it, so data
flows both ways.
"""
import asyncio
import itertools
import re
import sys
import time
from collections import deque

from . import nspwire as N
from .common import NEWPASS_TEXT, CoreClient, CoreError, peer_addr, sign_in_lines

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
JOIN_SECS = 30.0            # the join request after connecting
FIRST_SECS = 30.0           # the program's first message after the approval
POLL_SECS = 1.0             # the table's events
LOBBY_SECS = 1800.0         # a lobby with no game and no typing closes
IDLE_NOTE_SECS = 45.0       # no message either way this long: a chat line (idle_line), as a keepalive
IDLE_REPEAT_SECS = 75.0     # and again after this long while the silence lasts
HOST_NAME = "ParlorNet"
MY_ID = 2                   # the joining player's NetSprocket ID (the host's player is 1)
ids = itertools.count(1)


def log(sid, text):
    print("netsprocket %d: %s" % (sid, text), file=sys.stderr, flush=True)


class Wire:
    """Reading and sending NetSprocket messages on one connection."""

    def init_wire(self, reader, writer):
        self.r, self.w = reader, writer
        self.nsp = N.Reader()
        self.pending = deque()      # messages read and not yet handled
        self.mid = 0
        self.t0 = time.monotonic()
        self.sent = self.heard = time.monotonic()   # our last message (the idle notes aside), the Mac's last bytes
        self.noting = False         # an idle note is being sent: it does not end the silence
        self.send_lock = asyncio.Lock()

    def now(self):
        return int((time.monotonic() - self.t0) * 1000) & 0xFFFFFFFF

    async def send(self, what, frm, to, body):
        async with self.send_lock:
            self.mid += 1
            self.w.write(N.pack(what, frm, to, self.mid, self.now(), body))
            if not self.noting:
                self.sent = time.monotonic()
            await self.w.drain()

    async def next_frame(self):
        """The next message from the Mac, or None when it has gone away."""
        while not self.pending:
            data = await self.r.read(65536)
            if not data:
                return None
            self.heard = time.monotonic()
            self.pending.extend(self.nsp.feed(data))
        return self.pending.popleft()


class Join(Wire):
    """One connection until its program is known: the join request, the sign-in, the approval and the first
    message. games: the game modules, each with recognises(frame), CLIENT_NAME and Session."""

    def __init__(self, svc, reader, writer, ticket=None, games=()):
        self.svc, self.ticket, self.games = svc, ticket, games
        self.init_wire(reader, writer)
        self.sid = next(ids)
        self.addr = peer_addr(writer)
        self.core = None
        self.name = None
        self.ptype = N.PLAYER_TYPE

    async def sign_in(self, name, password):
        """None when signed in, else the reason (logged, not shown)."""
        if not NAME_RE.match(name or ""):
            return "not a name: %r" % (name or "")[:20]
        try:
            self.core = await CoreClient.connect(*self.svc.core_addr)
            await self.core.source(self.addr)
        except (OSError, asyncio.TimeoutError, CoreError) as e:
            self.core = None
            return "the core is not answering (%s)" % e
        return await sign_in_lines(self.core, name, password, None, None, "signin")

    async def run(self):
        try:
            first = await asyncio.wait_for(self.next_frame(), JOIN_SECS)
        except asyncio.TimeoutError:
            return
        if first is None:
            return
        if first.what != N.JOIN_REQUEST:
            log(self.sid, "first message is not a join request: %r" % first)
            return
        name, password, self.ptype = N.parse_join(first.body)
        log(self.sid, "join %r, player type %d (password not logged)" % (name, self.ptype))
        why = await self.sign_in(name, password)
        if why is None and self.ticket is not None and not self.ticket.sign_in():
            why = "the server is full"
        if why is not None:
            log(self.sid, "refused: %s" % why)
            text = why if why == NEWPASS_TEXT else "Check your ParlorNet name and password: %s." % why.rstrip(".")
            if not await self.refuse_inside(name, text):
                await self.send(N.JOIN_DENIED, N.SYSTEM_ID, 0, N.join_denied(text))
                await asyncio.sleep(2)
            await self.sign_out()
            return
        self.name = self.core.name or name
        game, f = await self.approve(self.name)
        if game is None:
            log(self.sid, "no game known by its first message: %r" % f)
            await self.sign_out()
            return
        if self.ptype != game.PLAYER_TYPE:
            log(self.sid, "%s, though it joined with player type %d" % (game.CLIENT_NAME, self.ptype))
        log(self.sid, "the program says it is %s" % game.CLIENT_NAME)
        try:
            await self.core.claim(game.CLIENT_NAME, "Macintosh")    # the turns say what they were played with
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        await game.Session(self).run(f)

    async def approve(self, name):
        """Approve the join, as the program's own host would (both players with the joiner's own player type),
        and wait for the program's first message -> (the game it is, or None; that message)."""
        await self.send(N.JOIN_APPROVED, N.SYSTEM_ID, MY_ID,
                        N.join_approved([(N.HOST_ID, HOST_NAME), (MY_ID, name)], self.now(), self.ptype))
        await self.send(N.PLAYER_JOINED, N.SYSTEM_ID, 0, N.player_joined(2, MY_ID, name, self.ptype))
        try:
            f = await asyncio.wait_for(self.next_frame(), FIRST_SECS)
        except asyncio.TimeoutError:
            f = None
        return next((g for g in self.games if f is not None and g.recognises(f)), None), f

    async def refuse_inside(self, name, text):
        """A program that never shows NetSprocket's join denial (NetBoxes) is told
        why inside the game instead: the join is approved, the program shows what the game's refuse(join, first,
        text) says, and the connection closes. Only for a program whose player type is such a game's, and only
        when its first message confirms it. True when it was told."""
        if not any(getattr(g, "refuse", None) and g.PLAYER_TYPE == self.ptype for g in self.games):
            return False
        game, f = await self.approve(name or "?")
        if game is None or not getattr(game, "refuse", None):
            return False
        await game.refuse(self, f, text)
        return True

    async def sign_out(self):
        if self.core is None:
            return
        try:
            await self.core.out()
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.core.close()


class GameSession(Wire):
    """A signed-in Mac playing one game, from its first message on. A game's Session sets CHAT_WIDTH and
    gives on_frame(frame), table_events(), idle_line() and say(lines), and builds self.console (a
    chatcmd.Console) in setup(). AFTER_SECS: how long the connection stays open after a game's end, or None
    when the program plays on in the same session."""

    CHAT_WIDTH = 60
    AFTER_SECS = None

    def __init__(self, join):
        for k in ("svc", "ticket", "sid", "addr", "core", "name", "r", "w", "nsp", "pending", "mid", "t0", "sent",
                  "heard", "noting", "send_lock"):
            setattr(self, k, getattr(join, k))
        self.core_lock = asyncio.Lock()
        self.ready = False          # the program has had its lobby (the idle note waits for it)
        self.tid = None             # my table
        self.seat = None
        self.cursor = 0             # the last event of my table handled
        self.started = False        # the game is under way on the Mac
        self.over = False
        self.over_at = 0.0
        self.last_typed = time.monotonic()
        self.closing = False
        self.noted = None           # the last idle note (idle_due)
        self.handling = False       # a message from the Mac is being answered
        self.console = None

    def setup(self):
        """Build self.console and anything else the session needs before the first message."""

    # ---- the core ---------------------------------------------------------------------------------------
    async def req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    # ---- the session ------------------------------------------------------------------------------------
    async def run(self, first):
        self.setup()
        poller = asyncio.ensure_future(self.poll())
        try:
            f = first
            while f is not None:
                self.handling = True
                try:
                    await self.on_frame(f)
                finally:
                    self.handling = False
                if self.closing:
                    break
                f = await self.next_frame()
        finally:
            poller.cancel()
            await self.hang_up()

    async def hang_up(self):
        """The Mac went away: a game being played is resigned (the programs cannot come back to it)."""
        if self.core is None:
            return
        try:
            if self.started and not self.over and self.tid:
                await self.req("RES %s" % self.tid)
                log(self.sid, "resigned table %s: the Mac went away" % self.tid)
            elif self.tid and not self.started:
                await self.req("LVE %s" % self.tid)
            await self.core.out()
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.core.close()

    def close(self):
        self.closing = True
        self.w.close()

    async def typed(self, text):
        """A line the player typed in the chat box: a command, or chat for the table."""
        self.last_typed = time.monotonic()
        self.core.user_input()
        text = text.strip()
        if not text:
            return
        try:
            if await self.console.handle(text):
                return
            if self.tid:
                await self.req("CHT %s %s" % (self.tid, re.sub(r"[^ -~]", "?", text)[:56]))
            else:
                await self.say("Chat goes to your opponent once you have a table. /help for the commands.")
        except (CoreError, OSError, asyncio.TimeoutError) as e:
            log(self.sid, "command failed: %s" % e)
            await self.say("The server is not answering just now.")

    async def poll(self):
        while not self.closing:
            await asyncio.sleep(self.svc.poll)
            try:
                if self.tid:
                    await self.table_events()
                elif not self.started and time.monotonic() - self.last_typed > LOBBY_SECS:
                    await self.say("Nothing happened for a long time; goodbye.")
                    self.close()
                if self.AFTER_SECS is not None and self.over and time.monotonic() - self.over_at > self.AFTER_SECS:
                    self.close()
                if not self.closing and self.idle_due():
                    await self.idle_note()
            except (CoreError, OSError, asyncio.TimeoutError) as e:
                log(self.sid, "poll: %s" % e)
                self.close()
            except Exception as e:
                log(self.sid, "poll error: %r" % e)
                print("netsprocket poll error: %r" % e, file=sys.stderr, flush=True)
                import traceback
                traceback.print_exc()
                self.close()

    async def events(self):
        """The table's new events since the cursor -> [(seq, kind, rest)], or None when the table is gone."""
        data, final = await self.req("SIN %s %d" % (self.tid, self.cursor))
        if final.startswith("ERR"):
            return None
        evs = []
        for l in data:
            p = l.split(" ", 4)
            if len(p) < 4 or p[0] != "EVT" or not p[2].isdigit() or int(p[2]) <= self.cursor:
                continue
            evs.append((int(p[2]), p[3], p[4] if len(p) > 4 else ""))
        return evs

    async def names(self):
        """The names at my table, seat by seat ('-' for an empty seat)."""
        data, final = await self.req("LST table=%s" % self.tid)
        for l in data:
            p = l.split()
            if len(p) >= 7 and p[0] == "TAB" and p[1] == self.tid:
                return p[6].split(",")
        return []

    # ---- the idle note -------------------------------------------------------------------------------------
    def idle_due(self):
        """No message either way for idle_note seconds while nothing from the Mac is being answered (the Mac's
        bytes count from the first: a message may be on its way); then again every idle_repeat seconds while
        the silence lasts."""
        if not self.svc.idle_note or not self.ready or self.handling or self.closing:
            return False
        now, last = time.monotonic(), max(self.sent, self.heard)
        if now - last < self.svc.idle_note:
            return False
        return self.noted is None or self.noted < last or now - self.noted >= self.svc.idle_repeat

    async def idle_note(self):
        """Fill a silence with a chat line (idle_line), so that data goes both ways."""
        line = await self.idle_line()
        if not line or not self.idle_due():         # the state may have moved while the core answered
            return
        first = self.noted is None or self.noted < max(self.sent, self.heard)
        self.noting = True
        try:
            await self.say(line)
        finally:
            self.noting = False
        self.noted = time.monotonic()
        if first:
            log(self.sid, "idle chat %r (again every %g s while silent)" % (line, self.svc.idle_repeat))
