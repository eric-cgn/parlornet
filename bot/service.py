"""The bot as a service: for each bot name, one connection that looks for
invitations (IVL every few seconds) and one more for each table it plays,
which waits for the other players with WAT. All of them are connections of
the server extension "bot" (the extension channel, TLS 1.3 with the bot's
key): it signs in with a bot name alone, and the core lets that key sign in as
nothing else (parlord/bots.py). A session is made once per name and resumed
(SES) by its other connections.

The bot keeps one open table of every two-player game it plays (docs/bot.md,
Open tables). It makes each one with NEW and the game's defaults, marks it
with the table data "bot open" (TDS), and sits at it as bot while that name
has room, else as bot1, and so on. When a person sits there, the game starts,
the mark comes off, the bot plays the game as at any other table, and a new
open table of that game is made at once. The process that plays the name bot
looks at every table (LST all) every few seconds. It opens what is missing,
stands up from a table of a game the server no longer offers, and keeps only
the older one when there are two of a game.

At other tables the bot sits down only when invited, never invites anyone,
says one greeting when it sits down and "Good game!" at the end, and leaves
when the game is over, when the people at an open table have left, or when its
seat has left a game the others play on. At most `cap` tables per bot name; an
invitation over that is declined."""
import asyncio
import random
import time

from adapters.botflag import bot_seats
from adapters.common import CoreClient, CoreError, parse_games, parse_invites, parse_tables

from . import games, play

NAMES = ("bot", "bot1", "bot2", "bot3")
GREETING = "Hello! I am the bot, a simple player. Good luck!"
FAREWELL = "Good game!"
CLIENT = "ParlorNet bot"     # what the core records with each of its turns (CLI PROG)
HANDSHAKES = 3              # TLS handshakes at once (the core's limit from one address is 8)
OWNER = NAMES[0]             # the process that plays this name keeps the open tables
OPEN_KEY, OPEN_VALUE = "bot", "open"     # the table data that marks an open table of the bot's (TDS)
LOST = (CoreError, OSError, asyncio.TimeoutError, ConnectionError)


class OpenTable:
    """One open table of the bot's: the game, the bot name seated there, and the table once it is made.
    since: when this process learned the table's number (time.monotonic)."""

    def __init__(self, key, rules, name, tid=None):
        self.key, self.rules, self.name, self.tid = key, rules, name, tid
        self.since = time.monotonic()
        self.task = None


class Bot:
    def __init__(self, host, port, key, names=NAMES, cap=20, think=(0.5, 2.0), pace=0.12, poll=2.0, scan=15.0,
                 log=print, keep_open=True):
        self.host, self.port, self.key = host, port, key
        self.names = [n.lower() for n in names]
        self.cap, self.think, self.pace, self.poll, self.scan = cap, think, pace, poll, scan
        self.log = log
        self.keep_open = keep_open and OWNER in self.names    # only the process that plays bot keeps them
        self.sids = {}                                  # name -> session id (never logged)
        self.games = {n: {} for n in self.names}        # name -> {table id: task}
        self.refused = set()                            # names the core will not let the bot use
        self.played = 0
        self.handshakes = asyncio.Semaphore(HANDSHAKES)
        self.open = {}                                  # game type/ver -> OpenTable, while it waits for a player
        self.marks = {}                                 # table id -> True if it carries the open mark
        self.wake = asyncio.Event()                     # a player sat at an open table, or one went
        self.said = set()                               # problems with open tables already logged

    # ---- connections ------------------------------------------------------------------------
    async def connect(self, name):
        """A connection signed in as name: the session resumed if there is one, else a new one. A few
        handshakes at a time: the core drops the oldest of more than 8 from one address."""
        async with self.handshakes:
            c = await CoreClient.connect(self.host, self.port, self.key)
        try:
            await c.claim(CLIENT)               # its turns say they were played by the bot (CLI)
            sid = self.sids.get(name)
            if sid:
                try:
                    await c.resume(sid)
                    return c
                except CoreError:
                    self.sids.pop(name, None)
            self.sids[name] = await c.helo(name)
            return c
        except BaseException:
            c.close()
            raise

    # ---- the lobby ----------------------------------------------------------------------------
    async def run(self):
        self.log("bot: playing as %s through %s:%d" % (", ".join(self.names), self.host, self.port))
        keeper = [self.keeper()] if self.keep_open else []
        await asyncio.gather(*(self.lobby(n) for n in self.names), *keeper)

    async def lobby(self, name):
        delay = 1.0
        while True:
            try:
                c = await self.connect(name)
            except CoreError as e:
                if name not in self.refused:
                    self.log("bot: cannot sign in as %s: %s" % (name, e))
                    self.refused.add(name)
                await asyncio.sleep(60)
                continue
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2)
                continue
            delay = 1.0
            if name in self.refused:
                self.refused.discard(name)
                self.log("bot: signed in as %s" % name)
            try:
                await self.watch(name, c)
            except LOST as e:
                self.log("bot: %s lost its connection (%s); again" % (name, type(e).__name__))
            finally:
                c.close()
            await asyncio.sleep(1)

    async def watch(self, name, c):
        scanned = 0.0
        while True:
            data, final = await c.req("IVL")
            if not final.startswith("OK"):
                raise CoreError(final)
            for inv in parse_invites(data):
                await self.accept(name, c, inv["id"])
            if time.monotonic() - scanned >= self.scan:
                scanned = time.monotonic()
                await self.resume_seated(name, c)
            await asyncio.sleep(self.poll)

    def playing(self, name):
        """The tables this name plays, its open tables left out (they wait for a player)."""
        waiting = {o.tid for o in self.open.values()}
        return sum(1 for tid in self.games[name] if tid not in waiting)

    async def accept(self, name, c, tid):
        if tid in self.games[name]:
            return
        if self.playing(name) >= self.cap:
            await c.req("UNV %s %s" % (tid, name))         # declined: this name is at its limit
            self.log("bot: %s declined table %s (playing %d tables)" % (name, tid, self.cap))
            return
        self.start(name, tid, join=True)

    async def resume_seated(self, name, c):
        """Tables where this name is seated and nothing plays it (after a restart of the bot). An open table
        of the bot's that waits for a player is the keeper's."""
        data, final = await c.req("LST mine")
        flags = bot_seats(data)
        for t in parse_tables(data):
            if name in [n.lower() for n in t["names"]] and t["id"] not in self.games[name]:
                if self.keep_open and t["status"] == "open" and len(flags.get(t["id"], ())) == t["filled"] \
                        and await self.marked(c, t):
                    continue
                if self.playing(name) < self.cap:
                    self.start(name, t["id"], join=False)

    def start(self, name, tid, join):
        self.games[name][tid] = asyncio.create_task(self.table(name, tid, join))

    # ---- one table ----------------------------------------------------------------------------
    async def table(self, name, tid, join):
        c = None
        try:
            c = await self.connect(name)
            seat = None
            if join:
                _, final = await c.req("JON %s" % tid)
                if final.startswith("OK") and len(final.split()) > 1:
                    seat = int(final.split()[1])
                elif not final.startswith("ERR STATE"):
                    self.log("bot: %s could not sit at table %s: %s" % (name, tid, final))
                    return
            if seat is None:
                seat, t = await self.seat_at(c, tid, name)
                if seat is None:
                    return
                if await self.marked(c, t):     # an open table of the bot's that a person sat at while it was away
                    await self.unmark(c, tid)
            await self.play(c, name, tid, seat, greet=join)
        except CoreError as e:
            self.log("bot: %s at table %s: %s" % (name, tid, e))
        except (OSError, asyncio.TimeoutError, ConnectionError) as e:
            self.log("bot: %s at table %s lost its connection (%s)" % (name, tid, type(e).__name__))
        finally:
            if c is not None:
                c.close()
            self.games[name].pop(tid, None)

    async def seat_at(self, c, tid, name):
        """(name's seat at the table or None, the table's LST entry or None)."""
        data, _ = await c.req("LST table=%s" % tid)
        t = next((t for t in parse_tables(data) if t["id"] == tid), None)
        return (next((i for i, n in enumerate(t["seated"]) if n.lower() == name), None) if t else None), t

    async def play(self, c, name, tid, seat, greet):
        table = NetTable(c, tid, seat, self.pace, random.Random())
        table.log = self.log
        if greet:
            await table.chat(GREETING)
        why = await play.run(table, self.think, log=self.log)
        if why == "over":
            await table.chat(FAREWELL)
            self.played += 1

    # ---- the open tables (docs/bot.md, Open tables) -------------------------------------------
    async def marked(self, c, t):
        """Whether a table carries the bot's open mark (TDL), remembered once known. A table nobody sits at
        yet may be one that is being made, so a missing mark there is asked again next time."""
        tid = t["id"]
        if tid not in self.marks:
            data, final = await c.req("TDL %s" % tid)
            if not final.startswith("OK"):
                return False
            mark = ("TDL %s %s" % (OPEN_KEY, OPEN_VALUE)) in data
            if mark or t["filled"]:
                self.marks.setdefault(tid, mark)
            return mark
        return self.marks[tid]

    async def unmark(self, c, tid):
        await c.req("TDD %s %s" % (tid, OPEN_KEY))
        self.marks[tid] = False

    def say_once(self, what, text):
        if what not in self.said:
            self.said.add(what)
            self.log(text)

    def seat_name(self):
        """The bot name to seat at a new open table: the first one the core accepts that has room."""
        return next((n for n in self.names if n not in self.refused and self.playing(n) < self.cap), None)

    async def keeper(self):
        """One connection that keeps the open tables: a look every `poll` seconds, or at once when a player
        sits at one of them or one goes. Only the process that plays the name bot runs it, so two bot
        processes never both open tables."""
        delay = 1.0
        while True:
            c = None
            try:
                if OWNER in self.refused:
                    raise CoreError("the name %s is not the bot's here" % OWNER)
                c = await self.connect(OWNER)
                delay = 1.0
                while True:
                    self.wake.clear()
                    await self.keep(c)
                    try:
                        await asyncio.wait_for(self.wake.wait(), self.poll)
                    except asyncio.TimeoutError:
                        pass
            except CoreError:
                await asyncio.sleep(max(self.poll, 5.0))
            except (OSError, asyncio.TimeoutError, ConnectionError):
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2)
            finally:
                if c is not None:
                    c.close()

    async def keep(self, c):
        """One look: every two-player game the server offers and the bot plays gets one open table, the
        bot's open tables of other games are left, and of two open tables of one game the older one stays.
        A game for more than two players gets none."""
        began = time.monotonic()
        data, final = await c.req("GAM")
        if not final.startswith("OK"):
            raise CoreError(final)
        offered = {}
        for g in parse_games(data):                     # one line per ruleset, the default first
            if games.MODULES.get(g["key"]) and g["min"] == g["max"] == 2 and g["key"] not in offered:
                offered[g["key"]] = g["rules"]
        data, final = await c.req("LST all")
        if not final.startswith("OK"):
            raise CoreError(final)
        flags = bot_seats(data)
        listed, standing = set(), {}
        for t in parse_tables(data):
            listed.add(t["id"])
            if t["status"] == "open" and t["vis"] == "public" and len(flags.get(t["id"], ())) == t["filled"] \
                    and await self.marked(c, t):
                standing.setdefault(t["key"], []).append(t)
        for key, o in list(self.open.items()):
            if o.task is None or o.task.done():
                self.open.pop(key, None)
            elif o.tid is not None and o.since < began and o.tid not in listed:
                o.task.cancel()                         # gone while it waited: an idle limit or the operator
                self.open.pop(key, None)
        for key in sorted(set(offered) | set(standing)):
            tables = sorted(standing.get(key, []), key=lambda t: int(t["id"]))
            keep = tables[0] if tables and key in offered else None
            for t in tables:
                if t is not keep:
                    await self.retire(t)
            if key not in offered:
                continue
            o = self.open.get(key)
            if keep is None:
                if o is None:
                    name = self.seat_name()
                    if name is None:
                        self.say_once("room", "bot: no bot name has room for an open table")
                    else:
                        self.begin(OpenTable(key, offered[key], name))
                continue
            if o is not None:
                continue                                # its task waits there, or is still making its table
            seated = [n.lower() for n in keep["names"]]
            name = next((n for n in seated if n in self.names), None)
            if name is None and not seated:
                name = self.seat_name()                 # nobody sits there yet: made just before a restart
            if name is not None and keep["id"] not in self.games.get(name, {}):
                self.begin(OpenTable(key, keep["rules"], name, keep["id"]))
            # seated by a bot name this process does not play: another bot process keeps it

    def begin(self, o):
        self.open[o.key] = o
        o.task = asyncio.create_task(self.stand(o))
        if o.tid is not None:
            self.games[o.name][o.tid] = o.task

    async def retire(self, t):
        """Stand up from an open table of the bot's that should not be there (its game was turned off, or it
        is the second of its game). A table seated by a bot name this process does not play is left alone."""
        seated = [n.lower() for n in t["names"]]
        name = next((n for n in seated if n in self.names), None)
        if name is None and seated:
            return
        name = name or next((n for n in self.names if n not in self.refused), None)
        if name is None:
            return
        c = None
        try:
            c = await self.connect(name)
            if not seated:
                await c.req("JON %s" % t["id"])         # nobody sits there: sitting and standing up ends it
            await c.req("LVE %s" % t["id"])
        except LOST:
            pass                                        # the next look tries again
        finally:
            if c is not None:
                c.close()

    async def stand(self, o):
        """Make an open table (or take one up again after a restart) and wait there for a player. When a
        person sits down the mark comes off, the keeper opens the next one, and this task plays the game."""
        c = None
        try:
            c = await self.connect(o.name)
            if o.tid is None:
                _, final = await c.req("NEW %s %s" % (o.key, o.rules))
                if not final.startswith("OK"):
                    self.say_once(("new", o.key, final), "bot: could not open a table of %s: %s" % (o.key, final))
                    return
                o.tid, o.since = final.split()[1], time.monotonic()
                self.games[o.name][o.tid] = o.task
                _, final = await c.req("TDS %s %s %s" % (o.tid, OPEN_KEY, OPEN_VALUE))
                self.marks[o.tid] = final.startswith("OK")
                if not self.marks[o.tid]:
                    self.say_once(("tds", final), "bot: could not mark table %s: %s" % (o.tid, final))
                    await c.req("JON %s" % o.tid)       # sitting and standing up ends the table
                    await c.req("LVE %s" % o.tid)
                    return
            seat, _ = await self.seat_at(c, o.tid, o.name)
            if seat is None:
                _, final = await c.req("JON %s" % o.tid)
                if not final.startswith("OK"):
                    return
                seat = int(final.split()[1])
            if not await self.wait_for_player(c, o):
                return
            await self.unmark(c, o.tid)
            if self.open.get(o.key) is o:
                del self.open[o.key]
            self.wake.set()                             # the next open table of this game, at once
            await self.play(c, o.name, o.tid, seat, greet=True)
        except CoreError as e:
            self.log("bot: %s at open table %s: %s" % (o.name, o.tid, e))
        except (OSError, asyncio.TimeoutError, ConnectionError):
            pass                                        # the keeper takes it up again when the core is back
        finally:
            if c is not None:
                c.close()
            if self.open.get(o.key) is o:
                del self.open[o.key]
                self.wake.set()
            if o.tid is not None and self.games[o.name].get(o.tid) is o.task:
                del self.games[o.name][o.tid]

    async def wait_for_player(self, c, o):
        """True once a person sits at the open table (the game may have started), False when it is gone."""
        seq = 0
        while True:
            data, final = await c.req("LST table=%s" % o.tid)
            t = next((t for t in parse_tables(data) if t["id"] == o.tid), None)
            if t is None or t["status"] not in ("open", "playing"):
                return False
            if t["status"] == "playing" or len(bot_seats(data).get(o.tid, ())) < t["filled"]:
                return True
            if o.name not in [n.lower() for n in t["names"]]:
                return False
            data, final = await c.req("WAT %s %d 30" % (o.tid, seq))
            if not final.startswith("OK"):
                return False
            f = final.split()
            seq = int(f[1]) if len(f) > 1 and f[1].isdigit() else seq


class NetTable(play.NetTable):
    """play.NetTable, signed out of the core when the session ends (the lobby signs in again). A move the
    server refuses is logged: the strategies play only what the rules allow, so it is a mistake."""
    log = print

    async def req(self, text):
        data, final = await super().req(text)
        if final.startswith(("ERR SIGNEDOUT", "ERR NOHELO")):
            raise CoreError(final, final.split()[1])
        if text.startswith(("MOV ", "END ")) and final.startswith("ERR"):
            self.log("bot: table %s refused %s: %s" % (self.tid, text, final))
        return data, final
