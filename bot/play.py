"""Playing one table: the view as the bot reads it, the table it plays on
(the server's, through the protocol; or a plugin in this process, for tests
and for trying a strategy), and the loop that plays it to the end.

A game's strategy (bot/games/) sees a View and a Table and plays one whole
turn: the steps of a staged turn and their END as well. The loop asks for the
view again after every turn, so a strategy keeps nothing between turns."""
import asyncio
import random
import time
import traceback

from . import games


class View:
    """VUE lines: tags -> field lists, the move list, the status."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.tags = {}
        self.moves = []
        self.status = ""
        self.seq = 0
        self.type = ""
        for line in self.lines:
            f = line.split()
            if not f:
                continue
            t = f[0]
            if t == "V" and len(f) >= 3:
                self.type, self.seq = f[1], int(f[2]) if f[2].isdigit() else 0
            elif t == "M":
                self.moves.append(" ".join(f[1:]))
            elif t == "S":
                self.status = line[2:]
            else:
                self.tags.setdefault(t, []).append(f[1:])

    def one(self, tag, default=None):
        rows = self.tags.get(tag)
        return rows[0] if rows else default

    def all(self, tag):
        return self.tags.get(tag, [])

    @property
    def turn(self):
        """The seat whose turn it is (W), -1 once over, None before the game starts."""
        w = self.one("W")
        if not w:
            return None
        return int(w[0]) if w[0].lstrip("-").isdigit() else -1

    @property
    def over(self):
        return self.status.startswith("Game over")

    @property
    def waiting(self):
        return self.status.startswith("Waiting for players")

    def key(self):
        """What the position is, without what changes by itself (the clock, the status words)."""
        return tuple(l for l in self.lines if not l.startswith(("CLK ", "S ", "V ")))


def won(end, seat):
    """1 for a win of seat in an END result, 0.5 for a draw, 0 for anything else."""
    f = (end or "").split()
    if f[:1] == ["win"] and len(f) > 1 and f[1].isdigit():
        return 1.0 if int(f[1]) == seat else 0.0
    return 0.5 if f[:1] == ["draw"] else 0.0


# ---- the tables a strategy plays on ---------------------------------------------------------------

class NetTable:
    """A table on a server, through one connection signed in as the player (a CoreClient, or anything with
    req(text) -> (data lines, final line)). pace: the least time between two requests, to stay within the
    core's rate limit (10 a second)."""

    def __init__(self, conn, tid, seat, pace=0.12, rng=None, wait_secs=30):
        self.conn, self.tid, self.seat, self.pace = conn, tid, seat, pace
        self.rng = rng or random.Random()
        self.wait_secs = wait_secs
        self.last = 0.0
        self.requests = 0

    async def req(self, text):
        if self.pace:
            gap = self.last + self.pace - time.monotonic()
            if gap > 0:
                await asyncio.sleep(gap)
            self.last = time.monotonic()
        self.requests += 1
        return await self.conn.req(text)

    async def view(self):
        data, final = await self.req("VUE %s GTM" % self.tid)
        return View(data) if final.startswith("OK") else None

    async def move(self, text):
        return (await self.req("MOV %s %s" % (self.tid, text)))[1]

    async def check(self, text):
        return (await self.req("CHK %s %s" % (self.tid, text)))[1]

    async def end(self):
        return (await self.req("END %s" % self.tid))[1]

    async def undo(self):
        return (await self.req("UND %s" % self.tid))[1]

    async def wait(self, seq, secs=None):
        """Until something happens at the table (WAT), at most secs."""
        data, final = await self.req("WAT %s %d %d" % (self.tid, seq, secs or self.wait_secs))
        return [l.split(" ", 3)[3] for l in data if l.startswith("EVT ") and len(l.split(" ", 3)) == 4]

    async def chat(self, text):
        return (await self.req("CHT %s %s" % (self.tid, text)))[1]

    async def leave(self):
        return (await self.req("LVE %s" % self.tid))[1]

    async def bots_only(self):
        """True when everyone seated at this (open) table is a bot: the people have left."""
        data, final = await self.req("LST table=%s" % self.tid)
        for line in data:
            f = line.split()
            if len(f) >= 7 and f[0] == "TAB" and f[1] == self.tid:
                names = [n for n in f[6].split(",")]
                bots = next((x[4:].split(",") for x in f[7:] if x.startswith("bot=")), [])
                return all(n == "-" or str(i) in bots for i, n in enumerate(names))
        return False


class LocalGame:
    """A game run by its plugin in this process, the way the core runs it (tests, and trying a strategy
    against another without a server). table(seat) is a seat's Table."""

    def __init__(self, game, rules, players, seed, options=None):
        from .rules import plugin
        self.m = plugin(game)
        info = self.m.op_info({})
        self.key = "%s/%d" % (info["type"], info["ver"])
        self.rules = rules
        r = self.m.op_new({"rules": rules, "players": players, "options": options or {}, "seed": seed})
        if "error" in r:
            raise ValueError(r["error"])
        self.state, self.events, self.result = r["state"], list(r.get("events", [])), r.get("end")
        self.players = players

    def table(self, seat, rng=None):
        return LocalTable(self, seat, rng)

    def apply(self, seat, toks, check):
        r = self.m.op_apply({"rules": self.rules, "state": self.state, "seat": seat, "move": toks, "check": check})
        if "error" in r:
            return "ERR ILLEGAL %s" % r["error"]
        if check:
            return ("OK %s" % r.get("info", "")).rstrip()
        self.state = r["state"]
        self.events += r.get("events", [])
        if "end" in r:
            self.result = r["end"]
        return ("OK %s" % (r.get("staged") or "")).rstrip()


class LocalTable:
    def __init__(self, game, seat, rng=None):
        self.g, self.seat, self.tid = game, seat, "local"
        self.rng = rng or random.Random(seat)
        self.requests = 0

    async def view(self):
        g = self.g
        r = g.m.op_view({"rules": g.rules, "state": g.state, "seat": self.seat})
        lines = ["V %s %d" % (g.key, len(g.events))] + r.get("geometry", []) + r.get("wire", [])
        status = r.get("status", "")
        if g.result and "Game over" not in status:
            status = "Game over: " + g.result
        lines.append("S " + status)
        if not g.result:
            mv = g.m.op_moves({"rules": g.rules, "state": g.state, "seat": self.seat})
            lines += ["M " + (" ".join(x) if isinstance(x, list) else x) for x in mv.get("moves", [])]
        return View(lines)

    async def move(self, text):
        self.requests += 1
        if self.g.result:
            return "ERR NOGAME the game is not running"
        return self.g.apply(self.seat, text.split(), False)

    async def check(self, text):
        self.requests += 1
        return self.g.apply(self.seat, text.split(), True)

    async def end(self):
        return await self.move("!end")

    async def undo(self):
        return await self.move("!undo")

    async def wait(self, seq, secs=None):
        return []

    async def chat(self, text):
        return "OK"

    async def leave(self):
        return "OK"

    async def bots_only(self):
        return False


# ---- the loop -------------------------------------------------------------------------------------

async def take_turn(module, table, v, style):
    """One turn with the strategy, or the random one (style "random")."""
    if style == "random":
        await module.random_turn(table, v)
    else:
        await module.smart(table, v)


async def run(table, think=(0.5, 2.0), style="smart", log=print):
    """Play the table to its end. Returns why it stopped: "over" (the game ended), "gone" (the table went
    away), "left" (the people left an open table, so the bot stood up), "dropped" (this seat left a game
    the others play on). think: the pause before each turn, (least, most) seconds."""
    last, stuck, mine = None, 0, False
    module = None
    while True:
        v = await table.view()
        if v is None:
            return "gone"
        if v.over:
            return "over"
        if v.waiting or v.turn is None:
            if await table.bots_only():
                await table.leave()
                return "left"
            await table.wait(v.seq)
            continue
        module = module or games.get(v.type)
        if module is None:
            log("bot: no strategy for %s; leaving table %s" % (v.type, table.tid))
            return "gone"
        if module.dropped(v, table.seat):
            return "dropped"
        if not module.my_turn(v, table.seat):
            mine = False
            await table.wait(v.seq)
            continue
        key = v.key()
        stuck = stuck + 1 if key == last else 0
        last = key
        if stuck >= 6:                  # nothing it tries is taken: let the clock decide, without spinning
            await table.wait(v.seq, 10)
            stuck = 3
            continue
        if not mine and think and think[1] > 0:
            await asyncio.sleep(table.rng.uniform(*think))
        mine = True
        try:
            await take_turn(module, table, v, "random" if stuck >= 2 else style)
        except (OSError, asyncio.TimeoutError, ConnectionError):
            raise
        except Exception:
            log("bot: %s at table %s failed:\n%s" % (v.type, table.tid, traceback.format_exc(limit=4)))
