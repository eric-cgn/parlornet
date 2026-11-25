"""The bot as a service: for each bot name, one connection that looks for
invitations (IVL every few seconds) and one more for each table it plays,
which waits for the other players with WAT. All of them are connections of
the server extension "bot" (the extension channel, TLS 1.3 with the bot's
key): it signs in with a bot name alone, and the core lets that key sign in as
nothing else (parlord/bots.py). A session is made once per name and resumed
(SES) by its other connections.

The bot joins only tables it is invited to, never invites anyone, says one
greeting when it sits down and "Good game!" at the end, and leaves when the
game is over, when the people at an open table have left, or when its seat has
left a game the others play on. At most `cap` tables per bot name; an
invitation over that is declined."""
import asyncio
import random
import time

from adapters.common import CoreClient, CoreError, parse_invites, parse_tables

from . import play

NAMES = ("bot", "bot1", "bot2", "bot3")
GREETING = "Hello! I am the bot, a simple player. Good luck!"
FAREWELL = "Good game!"
CLIENT = "ParlorNet bot"     # what the core records with each of its turns (CLI PROG)
HANDSHAKES = 3              # TLS handshakes at once (the core's limit from one address is 8)


class Bot:
    def __init__(self, host, port, key, names=NAMES, cap=20, think=(0.5, 2.0), pace=0.12, poll=2.0, scan=15.0,
                 log=print):
        self.host, self.port, self.key = host, port, key
        self.names = [n.lower() for n in names]
        self.cap, self.think, self.pace, self.poll, self.scan = cap, think, pace, poll, scan
        self.log = log
        self.sids = {}                                  # name -> session id (never logged)
        self.games = {n: {} for n in self.names}        # name -> {table id: task}
        self.refused = set()                            # names the core will not let the bot use
        self.played = 0
        self.handshakes = asyncio.Semaphore(HANDSHAKES)

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
        await asyncio.gather(*(self.lobby(n) for n in self.names))

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
            except (CoreError, OSError, asyncio.TimeoutError, ConnectionError) as e:
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

    async def accept(self, name, c, tid):
        if tid in self.games[name]:
            return
        if len(self.games[name]) >= self.cap:
            await c.req("UNV %s %s" % (tid, name))         # declined: this name is at its limit
            self.log("bot: %s declined table %s (playing %d tables)" % (name, tid, self.cap))
            return
        self.start(name, tid, join=True)

    async def resume_seated(self, name, c):
        """Tables where this name is seated and nothing plays it (after a restart of the bot)."""
        data, final = await c.req("LST mine")
        for t in parse_tables(data):
            if name in [n.lower() for n in t["names"]] and t["id"] not in self.games[name]:
                if len(self.games[name]) < self.cap:
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
                data, _ = await c.req("LST table=%s" % tid)
                t = next((t for t in parse_tables(data) if t["id"] == tid), None)
                seat = next((i for i, n in enumerate(t["seated"]) if n.lower() == name), None) if t else None
                if seat is None:
                    return
            table = NetTable(c, tid, seat, self.pace, random.Random())
            table.log = self.log
            if join:
                await table.chat(GREETING)
            why = await play.run(table, self.think, log=self.log)
            if why == "over":
                await table.chat(FAREWELL)
                self.played += 1
        except CoreError as e:
            self.log("bot: %s at table %s: %s" % (name, tid, e))
        except (OSError, asyncio.TimeoutError, ConnectionError) as e:
            self.log("bot: %s at table %s lost its connection (%s)" % (name, tid, type(e).__name__))
        finally:
            if c is not None:
                c.close()
            self.games[name].pop(tid, None)


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
