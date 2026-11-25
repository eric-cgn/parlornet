"""NetBoxes on the NetSprocket extension: ParlorNet's Dots and Boxes for NetBoxes 1.1 on the Mac.

ParlorNet is not affiliated with Will Helling or Brainfreeze Entertainment. NetBoxes 1.1 (2000) plays Dots and
Boxes for two to four players over Apple's NetSprocket: one copy hosts and the others join, and every message
goes through the host. The NetSprocket extension (adapters/netsprocket.py) is the host. A player chooses Join
Net Game, TCP/IP, and types the parlor's name as the host, the extension's port, their ParlorNet name as the
player name and their password in the password field. The join and the sign-in are the shared host's
(adapters/nsphost.py); NetBoxes is known by its first message after the approval, its version request. The
bytes the program sends and expects are in adapters/netboxeswire.py.

The lobby. After the join the program shows its chat window, with a face for each player. The host's player
is ParlorNet. The chat box takes the shared chat commands (adapters/chatcmd.py, docs/chat-commands.md): /help,
/who, /list, /open, /join TABLE or NAME, /invite NAME, /invite bot, /accept NAME, /decline NAME, /close,
/resign (the game being played), /motd and /quit. /open and /invite take a board size (7x5, boxes across and
down) and /open a number of players (3 or 4). Other lines go to the table's chat once there is a table, and
the other players' chat comes back the same way, under their names. A game starts by itself when every seat is
taken: the parlor sends NetBoxes' own start, and the player cannot start or stop games from the Game menu (the
version answer says joiners may not).

The game. Tables are dotsandboxes/1 with the standard ruleset (docs/types/dotsandboxes-1.md): its moves and its
turns are NetBoxes' own, without mines or random lines. The Mac sits down at standard tables wherever they were
made, for two to four players and of every size the type has (3 to 9 boxes each way): NetBoxes' own Start Game
dialog asks for 6 to 20 dots across and 4 to 20 down, but the program draws and plays the smaller boards it is
sent (4 and 5 dots across, tested). The program keeps its own points (a point a line, more for a box, less when
its own turn clock runs over); the server counts boxes, and its result is the one that stands (the chat gives
it).

Seats and slots. NetBoxes gives turns in the order of its players' slots (0-3), and the host chooses each
joiner's slot. The Mac always has slot 0; at the start the other seats take slots 1 to 3 in the table's order
from the Mac's seat on, so the turns go round as at the table, and the start names the player who moves first.
The host's player (NetSprocket ID 1) takes slot 1; the players in slots 2 and 3 get IDs 3 and 4.

    a line from the Mac         its dot (PEG) and direction (LINE) -> MOV table line; the Mac says itself
                                when its turn passes (PASS)
    a line from another seat    PEG and LINE from that seat's player, then PASS when it closed no box
    a seat leaves a game of 3-4 STEP_OUT for its slot: the Mac skips it, as the server does
    the end                     the chat gives the server's result; a game that ends before the board is
                                full (a resignation, a timeout) is stopped on the Mac (ABORT)

A game the Mac abandons (ABORT or STEP_OUT from the Mac, or the connection lost) is resigned.
NetBoxes plays on in the same network game: after an end, the player starts another from the chat box.

The idle note (adapters/nsphost.py): in a game, whose move it is and the time left; otherwise what to do next.
"""
import asyncio
import re
import time

from . import chatcmd
from . import netboxeswire as W
from .clients import BY_ID
from .common import CoreError, err_text, parse_tables
from .games import dotsandboxes as D
from .nsphost import HOST_NAME, MY_ID, GameSession, log

# What the core records with every turn the Mac plays (CLI): the program's name in the clients catalog.
CLIENT_NAME = BY_ID["netboxes"].name            # "NetBoxes 1.1"
PLAYER_TYPE = W.PLAYER_TYPE                     # NetBoxes joins with player type 0

KEY = "dotsandboxes/1"
RULES = "standard"
SIZE = "5x5"                # the boxes of a table made here, unless the player asks for another
CHAT_WIDTH = 250            # one line a message: the chat window wraps it itself
MAC_SLOT = 0
SLOT_IDS = {1: 1, 2: 3, 3: 4}   # the NetSprocket ID of the player in each other slot (1: the host's player)
COMMANDS = ["list", "resign", "invite", "open", "join", "close", "accept", "decline", "who", "tables", "top",
            "score", "motd", "time", "quit", "help"]
NOTES = ["/open and /invite NAME take a size, such as 7x5 (boxes across and down; 5x5 if none), and /open "
         "also 3 or 4 players.", "A game starts when every seat is taken."]
WELCOME = "Welcome, %s. Type /invite bot to play the bot, /open for anyone, or /help."
REFUSE_SECS = 5.0           # a refused sign-in's reason stays this long before the connection closes
SIZE_RE = re.compile(r"^([0-9]{1,2})x([0-9]{1,2})\Z", re.I)


def recognises(f):
    """NetBoxes 1.1's first message after the approval is its version request; any message of NetBoxes'
    own kinds is NetBoxes."""
    return f.what in W.KINDS


async def refuse(join, first, text):
    """A sign-in the server refused. NetBoxes never shows NetSprocket's join denial: it opens its chat window
    at once and says only "The game host has left!" when the connection closes. So the join is approved, the
    reason goes in the chat window, and the connection closes a few seconds later (adapters/nsphost.py)."""
    if first.what == W.VERSION_ASK:
        await join.send(W.VERSION_REPLY, W.HOST_ID, MY_ID, W.version_reply(MAC_SLOT, 0))
    for line in (text, "Choose Join Net Game to try again."):
        await join.send(W.CHAT, W.HOST_ID, 0, W.chat(line, HOST_NAME))
    await asyncio.sleep(REFUSE_SECS)


class Session(GameSession):
    CHAT_WIDTH = CHAT_WIDTH

    def __init__(self, join):
        super().__init__(join)
        self.face = 0               # the Mac's face, as it said last
        self.peg = None             # the dot the Mac picked for its line
        self.n = 2                  # players at my table
        self.slots = {}             # seat -> slot on the Mac, for the game being played
        self.shown = {1: (1, HOST_NAME)}     # slot -> (NetSprocket ID, name): the other players the Mac knows
        self.window = False         # the Mac may have a game window up (one that ended stays until aborted)
        self.seat_names = []

    def setup(self):
        self.console = Console(self)

    # ---- output -----------------------------------------------------------------------------------------
    async def msg(self, what, body, frm=W.HOST_ID, to=0):
        await self.send(what, frm, to, body)

    async def say(self, lines, name=HOST_NAME):
        """Lines in the chat window, under the name given (the parlor's own: ParlorNet)."""
        if isinstance(lines, str):
            lines = [lines]
        for line in chatcmd.wrap(lines, CHAT_WIDTH):
            await self.msg(W.CHAT, W.chat(line, name))

    def player_id(self, seat):
        return SLOT_IDS.get(self.slots.get(seat), W.HOST_ID)

    def name_of(self, seat):
        n = self.seat_names[seat] if 0 <= seat < len(self.seat_names) else "-"
        return n if n != "-" else "seat %d" % (seat + 1)

    # ---- from the Mac -----------------------------------------------------------------------------------
    async def on_frame(self, f):
        try:
            await self.on_message(f)
        except W.WireError as e:
            log(self.sid, "bad %s message: %s" % (W.KINDS.get(f.what, "%x" % f.what), e))

    async def on_message(self, f):
        k = f.what
        if k == W.VERSION_ASK:
            # the Mac's slot and face, no game in progress, and the Game menu's Start and Abort are not its own
            await self.msg(W.VERSION_REPLY, W.version_reply(MAC_SLOT, self.face), to=MY_ID)
        elif k == W.HELLO:
            name, slot, face = W.parse_hello(f.body)
            self.face = face
            log(self.sid, "hello from %r, slot %d, face %d" % (name, slot, face))
            await self.msg(W.HELLO_BACK, W.hello(HOST_NAME, 1, self.free_faces()[0]), to=MY_ID)
            if not self.ready:
                self.ready = True
                await self.say(WELCOME % self.name)
        elif k == W.CHAT:
            text, _ = W.parse_chat(f.body)
            await self.typed(text)
        elif k == W.PEG:
            self.peg = W.parse_peg(f.body)
        elif k == W.LINE:
            await self.mac_line(W.parse_line(f.body))
        elif k == W.FACE:
            face, slot = f.body[0], f.body[1]
            if slot == MAC_SLOT:
                self.face = face
        elif k in (W.ABORT, W.STEP_OUT):
            self.window = self.window and k == W.STEP_OUT
            await self.mac_left()
        elif k == W.START:
            # The Game menu's Start is off for the Mac; a start from it anyway is taken back.
            log(self.sid, "a start from the Mac (%r), taken back" % (W.parse_start(f.body),))
            await self.msg(W.ABORT, W.abort())
            await self.say("Games start here when every seat is taken: /invite bot, /open, /help.")
        elif k not in (W.PASS, W.POINTS, W.MINE, W.HELLO_BACK):
            log(self.sid, "%s from the Mac, not used" % W.KINDS.get(k, "%08x" % k))

    def free_faces(self):
        """The faces no player at the Mac has, the Mac's own aside."""
        return [i for i in range(W.FACES) if i != self.face]

    # ---- the idle note -------------------------------------------------------------------------------------
    async def idle_line(self):
        """The idle note: in a game, whose move it is and their time left; else what comes next."""
        if self.tid and self.started and not self.over:
            try:
                v = await self.view()
            except CoreError:
                return None
            if v["turn"] >= 0 and v["clock"]:
                seat, left, _ = v["clock"]
                who = "Your move" if seat == self.seat else "%s to move" % self.name_of(seat)
                line = "%s: %s left." % (who, chatcmd.clock_text(left))
                return line if len(line) <= CHAT_WIDTH else "Their move: %s left." % chatcmd.clock_text(left)
            return None
        if self.tid:
            return "Table %s waits for its players." % self.tid
        return "Type /invite bot to play the bot, or /help."

    # ---- the table ----------------------------------------------------------------------------------------
    async def seated(self, tid, seat, opp, how):
        self.tid, self.seat, self.cursor = tid, seat, 0
        self.started = self.over = False
        log(self.sid, "seated at table %s, seat %d (%s)" % (tid, seat, how))

    async def view(self, flags=""):
        data, final = await self.req("VUE %s %s" % (self.tid, flags) if flags else "VUE %s" % self.tid)
        if not final.startswith("OK"):
            raise CoreError(err_text(final))
        return D.parse_view(data)

    async def table_events(self):
        evs = await self.events()
        if evs is None:
            if not self.started:
                self.tid = None
                await self.say("Your table is gone. /open or /invite to start another.")
            return
        says = chatcmd.join_says(["%s %s" % (kind, rest) for _, kind, rest in evs])
        for seq, kind, rest in evs:
            self.cursor = seq
            a = rest.split()
            mine = a[:1] == [str(self.seat)]
            change = chatcmd.change_of(kind, a, says, self.seat)
            if kind == "JOIN" and len(a) >= 2 and not mine and not self.started:
                await self.say(chatcmd.joined(a[1], says.get(int(a[0]), "") if a[0].isdigit() else ""))
            elif change and self.seat_names:      # a player plays on with another program
                await self.say(chatcmd.client_change(self.name_of(change[0]), change[1]))
            elif kind == "LEAVE" and a and not mine and not self.started:
                await self.say("A player stood up.")
            elif kind == "START" and not self.started:
                await self.start()
            elif kind == "CHT" and len(a) >= 2 and a[0].isdigit() and not mine:
                await self.say(rest.split(" ", 1)[1][:200], name=self.name_of(int(a[0]))[:31])
            elif kind == "LINE" and len(a) >= 2 and a[0].isdigit() and self.started and not self.over:
                if not mine:
                    await self.other_line(int(a[0]), a[1], "BOX" in a[2:])
            elif kind == "GONE" and a and a[0].isdigit() and self.started and not self.over:
                await self.gone(int(a[0]), a[1] if len(a) > 1 else "")
            elif kind == "END":
                await self.ended(a)
                return

    async def start(self):
        """Every seat taken: show the Mac the other players in their slots, and start NetBoxes' game."""
        v = await self.view("G")
        data, final = await self.req("LST table=%s" % self.tid)
        t = next((t for t in parse_tables(data) if t["id"] == self.tid), None) if final.startswith("OK") else None
        self.seat_names = t["seated"] if t else await self.names()
        self.n = len(self.seat_names)
        self.slots = {s: (s - self.seat) % self.n for s in range(self.n)}
        # The other seats in their slots, under their names, each with a face the Mac does not have. Players in
        # slots 2 and 3 are new NetSprocket players to the Mac; those of an earlier, larger game leave.
        faces = iter(self.free_faces())
        now = {}
        for s in sorted(self.slots, key=self.slots.get)[1:]:
            slot, name = self.slots[s], self.name_of(s)[:31]
            pid = SLOT_IDS[slot]
            if slot not in self.shown:
                await self.msg(W.PLAYER_JOINED, W.player_joined(len(self.shown) + 2, pid, name, W.PLAYER_TYPE),
                               frm=W.SYSTEM_ID)
            await self.msg(W.HELLO_BACK, W.hello(name, slot, next(faces)), frm=pid, to=MY_ID)
            self.shown[slot] = now[slot] = (pid, name)
        for slot, (pid, name) in sorted(self.shown.items()):
            if slot not in now:
                del self.shown[slot]
                await self.msg(W.PLAYER_LEFT, W.player_left(len(self.shown) + 1, pid, name), frm=W.SYSTEM_ID)
        first = (max(v["turn"], 0) - self.seat) % self.n
        secs = t["turn"] if t and t["turn"] else W.MAX_SECS
        others = [self.name_of(s) for s in range(self.n) if s != self.seat]
        await self.say("* Your game with %s on table %s, %d by %d boxes." % (
            " and ".join(others), self.tid, v["w"], v["h"]))
        # A copy of NetBoxes that is sent a start never has the first turn itself (the starter has it), and its
        # own turn comes only with a PASS. So when the Mac moves first, the game starts with the player before
        # it, who passes at once; the server's table has no such turn.
        opening = first == MAC_SLOT
        if self.window:             # a start into a window still up from an earlier game leaves it blank
            await self.msg(W.ABORT, W.abort())
        await self.msg(W.START, W.start(v["w"] + 1, v["h"] + 1, min(secs, W.MAX_SECS),
                                        self.n - 1 if opening else first))
        if opening:
            await self.msg(W.PASS, W.pass_turn(), frm=SLOT_IDS[self.n - 1])
        self.started, self.over, self.peg, self.window = True, False, None, True
        log(self.sid, "game on table %s, seat %d of %d, %dx%d, first %d" % (self.tid, self.seat, self.n, v["w"],
                                                                           v["h"], first))

    # ---- moves --------------------------------------------------------------------------------------------
    async def other_line(self, seat, token, boxed):
        """Another seat's line -> its dot and direction on the Mac, from that seat's player; then the turn
        passes on the Mac unless the line closed a box."""
        try:
            x, y, d = W.line_move(token)
        except (W.WireError, ValueError):
            log(self.sid, "not a line: %r" % token)
            return
        pid = self.player_id(seat)
        await self.msg(W.PEG, W.peg(x, y), frm=pid)
        await self.msg(W.LINE, W.line(d), frm=pid)
        if not boxed:
            await self.msg(W.PASS, W.pass_turn(), frm=pid)

    async def mac_line(self, direction):
        if not (self.tid and self.started and not self.over) or self.peg is None:
            log(self.sid, "a line from the Mac with no game or no dot (%r)" % (self.peg,))
            return
        x, y = self.peg
        self.peg = None
        token = W.line_token(x, y, direction)
        _, final = await self.req("MOV %s %s" % (self.tid, token))
        if final.startswith("OK"):
            log(self.sid, "from the Mac: %s" % token)
            return
        log(self.sid, "the server refused %s: %s" % (token, final))
        await self.say("* The server refused %s: %s. The game on your Mac no longer matches the server's, "
                       "so it ends here." % (token, err_text(final)))
        await self.msg(W.ABORT, W.abort())
        self.window = False
        await self.req("RES %s" % self.tid)

    async def gone(self, seat, why):
        """A seat left a game of three or four: the Mac skips it from now on."""
        slot = self.slots.get(seat)
        if slot is None:
            return
        await self.msg(W.STEP_OUT, W.step_out(slot), frm=W.HOST_ID)
        what = {"resign": "resigned", "timeout": "ran out of time"}.get(why, "left")
        if seat == self.seat:
            await self.say("* You %s and are out of this game; the others play on." % what)
        else:
            await self.say("* %s %s and is out of the game." % (self.name_of(seat), what))

    async def mac_left(self):
        """The Mac stopped its game (Abort Game, or closing the game window): resigned on the server."""
        if not (self.tid and self.started and not self.over):
            return
        tid = self.tid
        _, final = await self.req("RES %s" % tid)
        log(self.sid, "the Mac left its game: RES %s -> %s" % (tid, final))
        if final.startswith("OK"):
            await self.say("You left the game at table %s: it counts as resigned." % tid)
        v = None
        try:
            v = await self.view()
        except CoreError:
            pass
        if v is not None and v["turn"] >= 0:            # three or more: the others play on without the Mac
            self.tid, self.started = None, False
            self.last_typed = time.monotonic()

    async def ended(self, a):
        if self.over:
            return
        self.over, self.over_at = True, time.monotonic()
        try:
            v = await self.view("T")
        except CoreError:
            v = None
        if v is not None and self.window and D.boxes_left(v):
            await self.msg(W.ABORT, W.abort())          # the board is not full: NetBoxes would play on
            self.window = False
        counts = v["counts"] if v else []
        scores = ", ".join("%s %d" % (self.name_of(i), c) for i, c in enumerate(counts))
        kind = a[0] if a else ""
        who = self.name_of(int(a[1])) if len(a) > 1 and a[1].isdigit() else "A player"
        what = {"win": "%s wins" % who, "draw": "a draw", "resign": "%s resigned" % who,
                "timeout": "%s ran out of time" % who}.get(kind, "the game ended")
        await self.say("* Table %s is over: %s. Boxes: %s." % (self.tid, what, scores))
        await self.say("Type /invite bot or /open for another game.")
        log(self.sid, "table %s over: %s" % (self.tid, " ".join(a)))
        self.tid, self.started = None, False
        self.last_typed = time.monotonic()


class Console(chatcmd.Console):
    """The lobby's chat commands (the shared set), on standard Dots and Boxes tables NetBoxes can show."""

    def __init__(self, s):
        self.s = s
        self.size, self.players = SIZE, 2
        super().__init__(s.say, s.req, lambda: s.name, COMMANDS, kinds=[KEY], label="Dots and Boxes", new=RULES,
                         notes=NOTES, handlers={"quit": self.quit}, seated=s.seated, width=CHAT_WIDTH,
                         current=lambda: s.tid if s.started and not s.over else None)

    async def handle(self, text):
        cmd = chatcmd.parse(text)
        if cmd is not None and cmd.name in ("open", "invite", "join", "accept") and self.s.tid:
            await self.say("You have a table already (%s)." % self.s.tid if not self.s.started
                           else "You are playing a game now.")
            return True
        if cmd is not None and cmd.name in ("open", "invite"):
            size, players, rest = SIZE, 2, []
            for arg in cmd.args:
                if SIZE_RE.match(arg):
                    size = arg.lower()
                elif arg in ("2", "3", "4") and cmd.name == "open":
                    players = int(arg)
                else:
                    rest.append(arg)
            self.size, self.players = size, players     # the server checks them (NEW)
            text = " ".join(["/" + cmd.name] + rest)
        return await super().handle(text)

    async def sit(self, tid, seat, opp, how):
        """Only standard tables: the majority ruleset ends before the board is full, which NetBoxes cannot."""
        data, final = await self.req("LST table=%s" % tid)
        t = next((t for t in parse_tables(data) if t["id"] == tid), None) if final.startswith("OK") else None
        if t is not None and t["rules"] != RULES:
            await self.say(["NetBoxes cannot play table %s: its rules are %s." % (tid, t["rules"]),
                            "/invite %s to play them here." % (opp.split()[0] if opp else "NAME")])
            return None
        return await super().sit(tid, seat, opp, how)

    async def new_table(self, vis):
        """NEW with the size and players asked for, this extension's time per turn and the standard rules."""
        size, players = self.size, self.players
        self.size, self.players = SIZE, 2
        _, final = await self.req("NEW %s %s size=%s players=%d turn=%s%s" % (
            KEY, RULES, size, players, self.s.svc.turn, " vis=private" if vis == "private" else ""))
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

    async def quit(self, cmd):
        await self.say("Goodbye.")
        self.s.close()
