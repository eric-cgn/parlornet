"""The RSCARDS extension's screens: sign-in, the lobby and a board for each game (adapters/rscards.py).

Every screen is 640 by 200 on the front end's virtual screen, in 25 rows of 8 pixels: rows 0-19 for the
screen itself, rows 20-23 for the message area (events and chat) and row 24 for the entry line, where chat and
the shared chat commands are typed. A game's board takes the left part (x 0-335, y 0-159) and its panel the
right (column 42 on): the game and table, the players, the state of the game, the clock, up to ten buttons in
two columns and a hint.

Colors are the front end's eight. A mono front end (Mac mono, Commodore 128 mono, CGA) draws black for 0 and
white for every other color, so every screen is drawn for both: the session's mono flag picks colors whose
black-or-white versions still tell everything apart (pieces get a rim, highlights a frame).

Screens are plain and cheap: the static parts are drawn once (macros where they save bytes), and a move redraws
only the squares, points or pits that changed. docs/clients/rscards.md has the bytes each screen costs.
"""
import time

from . import botflag, chatcmd
from . import games as registry
from . import rscwire as R
from .common import (client_change_sentence, client_sentence, err_text, fit, group_games, parse_client, parse_clients,
                     parse_games, parse_invites, parse_players, parse_tables)
from .rscards import TEXT_MAX, Scene, wrap_lines

FIELD_AT = (8 * 20, 8 * 12)
PANEL = 42                      # the panel's first column
BTN_COLS = (43, 61)
BTN_ROWS = (9, 11, 13, 15, 17)
BTN_W = 17                      # characters
LIST_W = 38


# ---- drawing helpers --------------------------------------------------------------------------------------

class Pal:
    """The colors a screen draws with, by role, for a color or a mono front end."""

    def __init__(self, mono):
        self.mono = mono
        self.bg = R.BLACK
        self.text = R.WHITE
        self.head = R.WHITE if mono else R.CYAN
        self.title_fg, self.title_bg = (R.BLACK, R.WHITE) if mono else (R.WHITE, R.BLUE)
        self.btn_fg, self.btn_bg = (R.BLACK, R.WHITE) if mono else (R.WHITE, R.BLUE)
        self.hot_fg, self.hot_bg = (R.BLACK, R.WHITE) if mono else (R.BLACK, R.YELLOW)
        self.dim = R.WHITE if mono else R.CYAN
        self.good = R.WHITE if mono else R.YELLOW
        self.msg_bg = R.BLACK if mono else R.BLUE

    def c(self, color, mono):
        return mono if self.mono else color


def txt(col, row, text, fg, bg=R.BLACK, width=0, raw=False):
    """Text in the normal font at a character cell; width (characters) fills the rest with bg."""
    return R.text(col * 8, row * 8, text, fg, bg, R.LEFT, width * 8, raw=raw)


def line_item(scene, key, col, row, text, fg, bg, width):
    """A line of text that may change: drawn over its whole width, erased with the screen's black."""
    text = R.clean_text(text, min(width, TEXT_MAX))
    scene.add(key, txt(col, row, text, fg, bg, width), R.fill(col * 8, row * 8, width * 8, 8, R.BLACK))



def clock_words(secs):
    secs = max(0, int(secs))
    if secs >= 86400:
        return "%dd %dh" % (secs // 86400, secs % 86400 // 3600)
    if secs >= 3600:
        return "%dh %02dm" % (secs // 3600, secs % 3600 // 60)
    if secs >= 120:
        return "%d min" % (secs // 60)
    return "%d s" % (secs // 10 * 10)


def describe(key, text, names):
    from .telnet import describe_event as common
    mod = registry.get(key)
    d = None
    if mod is not None:
        try:
            d = mod.describe_event(text, names)
        except (ValueError, IndexError):
            d = None
    if d is None:
        try:
            d = common(text, names)
        except (ValueError, IndexError):
            d = None
    return d


# ---- sign-in ----------------------------------------------------------------------------------------------

def signin_scene(s, label, note=()):
    pal = Pal(s.mono)
    sc = Scene(("signin",), [R.fill(0, 0, 640, 160, R.BLACK), R.fill(0, 0, 640, 8, pal.title_bg),
                             txt(1, 0, "ParlorNet", pal.title_fg, pal.title_bg),
                             txt(4, 3, "Board games: Reversi, Backgammon, Checkers, Mancala,", pal.text),
                             txt(4, 4, "Five in a Row, Dots and Boxes, Naval Combat, Grid Words,", pal.text),
                             txt(4, 5, "Rummy Tile Classic and Cannon Fire.", pal.text),
                             txt(4, 7, "Sign in with your ParlorNet name; a new name signs up.", pal.dim),
                             txt(4, 8, "Type in the field and press RETURN.", pal.dim)],
               scroll=(20, 4, pal.msg_bg), entry=False)
    line_item(sc, "label", 4, 10, label, pal.good, R.BLACK, 70)
    for i, l in enumerate(wrap_lines(note, TEXT_MAX)[:5]):
        line_item(sc, ("note", i), 4, 14 + i, l, pal.text, R.BLACK, 72)
    return sc


# ---- the lobby --------------------------------------------------------------------------------------------

LOBBY_GAMES = ("reversi/1", "backgammon/1", "checkers/1", "mancala/1", "fiveinarow/1", "dotsandboxes/1",
               "navalcombat/1", "gridwords/1", "rummytile/1", "cannonfire/1")
GAME_NAMES = {"reversi/1": "Reversi", "backgammon/1": "Backgammon", "checkers/1": "Checkers",
              "mancala/1": "Mancala", "fiveinarow/1": "Five in a Row", "dotsandboxes/1": "Dots and Boxes",
              "navalcombat/1": "Naval Combat", "gridwords/1": "Grid Words", "rummytile/1": "Rummy Tile",
              "cannonfire/1": "Cannon Fire"}
MINE_ROWS, INV_ROWS, OPEN_ROWS = (3, 4, 5), (8, 9), (12, 13, 14)
LEFT_COLS = ((8, 144, "pick"), (160, 40, "bot"), (216, 48, "open"))           # x, width, what: the games' grid
RIGHT_COLS = ((328, 208, "list"), (544, 40, "accept"), (592, 32, "decline"))  # the lists' grid; on the other
                                                                               # tables' rows the second is Watch
LOBBY_HELP = ["Click Bot to play the bot, Open to wait for a player, or a game of yours to go back to it.",
              "Type /invite NAME to invite a player to the game whose name you clicked; /help lists commands."]


class Lobby:
    def __init__(self, s):
        self.s = s
        self.pal = Pal(s.mono)
        self.poll_every = max(3.0, s.svc.poll * 2)
        self.games = []             # [(key, rules, min players)], what the parlor plays of LOBBY_GAMES
        self.mine, self.invs, self.open, self.online = [], [], [], []
        self.pick = "checkers/1"
        self.who_at = 0.0
        self.console = None

    async def enter(self):
        data, final = await self.s.req("GAM")
        got = {g["key"]: g for g in group_games(parse_games(data))} if final.startswith("OK") else {}
        self.games = [(k, got[k]["rules"], got[k]["min"] or 2) for k in LOBBY_GAMES if k in got]
        if self.pick not in got and self.games:
            self.pick = self.games[0][0]
        self.console = chatcmd.Console(self.s.say, self.s.req, lambda: self.s.name, self.commands(),
                                       kinds=self.kinds(), label="", new=self.rules_of(self.pick),
                                       notes=["Click a game's name first: /invite and /open make tables of it."],
                                       handlers={"resume": self.do_resume, "quit": self.do_quit},
                                       seated=self.seated, width=80)
        await self.poll()

    async def leave(self):
        pass

    def commands(self):
        return ["list", "resume", "invite", "open", "join", "close", "accept", "decline", "who", "tables", "top",
                "score", "motd", "quit", "help"]

    def kinds(self):
        return [self.pick] + [k for k, _, _ in self.games if k != self.pick]

    def rules_of(self, key):
        g = next((g for g in self.games if g[0] == key), None)
        return "%s turn=%s" % (g[1] if g else "standard", self.s.svc.turn)

    async def poll(self):
        me = (self.s.name or "").lower()
        tables = []
        for words in ("mine", "open", "playing"):   # mine, then the open ones and the games being played (capped)
            data, final = await self.s.req("LST %s clients" % words)
            got = {t["id"] for t in tables}
            tables += [t for t in parse_tables(data) if t["id"] not in got] if final.startswith("OK") else []
        known = {k for k, _, _ in self.games}
        mine, opens = [], []
        for t in sorted(tables, key=lambda t: int(t["id"]) if t["id"].isdigit() else 0):
            if t["key"] not in known:
                continue
            low = [n.lower() for n in t["seated"]]
            if me in low:
                mine.append(t)
            elif t["vis"] != "private" and (t["status"] == "playing" or t["filled"] < t["seats"]):
                opens.append(t)
        rows = []
        for t in mine[:len(MINE_ROWS)]:
            state = "waiting"
            if t["status"] == "playing":
                d, f = await self.s.req("VUE %s" % t["id"])
                turn = next((int(l.split()[1]) for l in d if l.startswith("CLK ") and l.split()[1].isdigit()), -1)
                seat = [n.lower() for n in t["seated"]].index(me)
                state = "your move" if turn == seat else "their move"
            others = [n for n in t["names"] if n.lower() != me]
            rows.append((t["id"], "%-3s %-13s %-10s %s" % (t["id"], GAME_NAMES.get(t["key"], t["key"])[:13],
                                                          (others[0] if others else "-")[:10], state)))
        self.mine = rows
        join = [t for t in opens if t["status"] != "playing"]            # the tables to join first, then those
        watch = [t for t in opens if t["status"] == "playing"][::-1]     # to watch (newest first), at least one
                                                                         # when there is one
        shown = join[:len(OPEN_ROWS) - min(1, len(watch))]
        shown += watch[:len(OPEN_ROWS) - len(shown)]
        self.open = [(t["id"], "%-3s %-13s %s" % (t["id"], GAME_NAMES[t["key"]][:13], self.who(t)), t["status"])
                     for t in shown]
        data, final = await self.s.req("IVL")
        invs = [i for i in parse_invites(data) if i["key"] in known] if final.startswith("OK") else []
        self.invs = [(i, "%s: %s" % (i["from"][:12], GAME_NAMES[i["key"]])) for i in invs[:len(INV_ROWS)]]
        if time.monotonic() - self.who_at > 20:
            self.who_at = time.monotonic()
            data, final = await self.s.req("WHO")
            self.online = botflag.listed(parse_players(data)) if final.startswith("OK") else []

    @staticmethod
    def who(t):
        """Who is at another table, in 8 characters: the players; at a table waiting for one, the player and what
        they play with, as far as it fits."""
        if t["status"] == "playing":
            return " v ".join(t["names"])[:8]
        text = ", ".join(t["names"])
        say = next(iter(t.get("clients", {}).values()), "")
        say = say[5:] if say.startswith("with ") else say
        if say and len(text) < 6:
            text += " " + fit(say, 7 - len(text))
        return text[:8]

    def scene(self):
        pal = self.pal
        base = [R.fill(0, 0, 640, 160, R.BLACK), R.fill(0, 0, 640, 8, pal.title_bg),
                txt(1, 0, "ParlorNet", pal.title_fg, pal.title_bg),
                txt(1, 2, "Start a game", pal.head), txt(41, 2, "Your games", pal.head),
                txt(41, 7, "Invitations", pal.head), txt(41, 11, "Other tables", pal.head),
                txt(1, 15, "Click Bot to play the bot, or Open to wait for a player.", pal.dim),
                txt(1, 16, "Click a game's name to choose it for /invite NAME and /open.", pal.dim),
                txt(1, 17, "Type on the bottom line: /help lists the commands.", pal.dim),
                txt(1, 18, "Click another table to join it, or Watch to watch its game.", pal.dim)]
        for i, (key, _, _) in enumerate(self.games):
            base += [txt(2, 3 + i, GAME_NAMES[key], pal.text),
                     txt(20, 3 + i, " Bot ", pal.btn_fg, pal.btn_bg), txt(27, 3 + i, " Open ", pal.btn_fg, pal.btn_bg)]
        sc = Scene(("lobby", tuple(k for k, _, _ in self.games)), base, scroll=(20, 4, pal.msg_bg), entry=True)
        line_item(sc, "me", 50, 0, ("Signed in as %s" % self.s.name).rjust(29), pal.title_fg, pal.title_bg, 29)
        for i, (key, _, _) in enumerate(self.games):
            # the chosen game: a marker left of the names, outside the row's click area, whose frame (inverted
            # under the pointer) would leave a line on anything drawn inside it while the pointer is there
            if key == self.pick:
                sc.add(("g", i), txt(0, 3 + i, ">", pal.hot_fg, pal.hot_bg), R.fill(0, (3 + i) * 8, 8, 8, R.BLACK))
            sc.area(8, (3 + i) * 8, 256, 8, ("row", "L", i), "H")
        for i, (x, w, what) in enumerate(LEFT_COLS):
            sc.area(x, 3 * 8, w, max(1, len(self.games)) * 8, ("col", "L", i))
        rows = MINE_ROWS + INV_ROWS + OPEN_ROWS
        for i, (x, w, what) in enumerate(RIGHT_COLS):
            sc.area(x, rows[0] * 8, w, (rows[-1] - rows[0] + 1) * 8, ("col", "R", i))
        for row in rows:
            sc.area(328, row * 8, 296, 8, ("row", "R", row), "H")
        for i, row in enumerate(MINE_ROWS):
            text = self.mine[i][1] if i < len(self.mine) else ("(none)" if i == 0 else "")
            line_item(sc, ("m", i), 41, row, text, pal.text, R.BLACK, LIST_W)
        for i, row in enumerate(INV_ROWS):
            text = self.invs[i][1] if i < len(self.invs) else ("(none)" if i == 0 else "")
            line_item(sc, ("i", i), 41, row, text, pal.text, R.BLACK, 26)
            if i < len(self.invs):
                sc.add(("iyes", i), [txt(68, row, " Yes ", pal.btn_fg, pal.btn_bg),
                                     txt(74, row, " No ", pal.btn_fg, pal.btn_bg)], R.fill(68 * 8, row * 8, 80, 8, R.BLACK))
        for i, row in enumerate(OPEN_ROWS):
            text = self.open[i][1] if i < len(self.open) else ("(none)" if i == 0 else "")
            line_item(sc, ("o", i), 41, row, text, pal.text, R.BLACK, 26)
            if i < len(self.open):
                sc.add(("owatch", i), txt(68, row, " Watch ", pal.btn_fg, pal.btn_bg),
                       R.fill(68 * 8, row * 8, 80, 8, R.BLACK))
        line_item(sc, "online", 1, 19, ("Online: " + ", ".join(self.online))[:78], pal.text, R.BLACK, 78)
        return sc

    def grid_meaning(self, m):
        """A click on the games' grid or the lists' grid -> (what, key or index), or None."""
        kind, grid = m[0], m[1]
        col = m[2] if kind in ("cell", "col") else None
        row = m[3] if kind == "cell" else m[2] if kind == "row" else None
        if row is None:
            return None
        if grid == "L":
            if row >= len(self.games):
                return None
            what = LEFT_COLS[col][2] if col is not None else "pick"
            return what, self.games[row][0]
        what = RIGHT_COLS[col][2] if col is not None else "list"
        if row in MINE_ROWS:
            return ("mine", MINE_ROWS.index(row)) if what == "list" else None
        if row in INV_ROWS:
            return ("decline" if what == "decline" else "accept", INV_ROWS.index(row))
        if row in OPEN_ROWS:
            return ("join", OPEN_ROWS.index(row)) if what == "list" else ("watch", OPEN_ROWS.index(row))
        return None

    async def click(self, m):
        if m[0] in ("cell", "col", "row"):
            m = self.grid_meaning(m)
            if m is None:
                return
        kind = m[0]
        if kind == "pick":
            self.pick = m[1]
            self.console.kinds, self.console.new = self.kinds(), self.rules_of(self.pick)
            self.s.say("%s chosen: /invite NAME or /open make a table of it." % GAME_NAMES[m[1]])
        elif kind in ("bot", "open"):
            await self.new_table(m[1], kind == "bot")
        elif kind == "mine":
            if m[1] < len(self.mine):
                await self.enter_table(self.mine[m[1]][0])
        elif kind == "join":
            if m[1] < len(self.open):
                if self.open[m[1]][2] == "playing":
                    await self.enter_table(self.open[m[1]][0])       # a game being played: watch it
                else:
                    await self.sit(self.open[m[1]][0], "join")
        elif kind == "watch":
            if m[1] < len(self.open):
                await self.enter_table(self.open[m[1]][0])
        elif kind == "accept":
            if m[1] < len(self.invs):
                await self.sit(self.invs[m[1]][0]["id"], "accept")
        elif kind == "decline":
            if m[1] < len(self.invs):
                i = self.invs[m[1]][0]
                await self.s.req("UNV %s %s" % (i["id"], self.s.name))
                self.s.say("You declined %s's invitation." % i["from"])
                await self.poll()

    async def typed(self, text):
        if await self.console.handle(text):
            return
        self.s.say("Chat goes to the players at a table. Commands start with /; /help lists them.")

    async def new_table(self, key, bot):
        g = next((g for g in self.games if g[0] == key), None)
        if g is None:
            return
        _, final = await self.s.req("NEW %s %s turn=%s%s" % (key, g[1], self.s.svc.turn,
                                                              " vis=private" if bot else ""))
        if not final.startswith("OK"):
            self.s.say("Can't start a table: %s" % err_text(final))
            return
        tid = final.split()[1]
        _, final = await self.s.req("JON %s 0" % tid)
        if not final.startswith("OK"):
            await self.s.req("LVE %s" % tid)
            self.s.say("Can't sit down: %s" % err_text(final))
            return
        if bot:
            _, final = await self.s.req("INV %s %s" % (tid, botflag.next_name([self.s.name]) or "bot"))
            self.s.say("You invited the bot to table %s; it sits down in a few seconds." % tid
                       if final.startswith("OK") else "Can't invite the bot: %s" % err_text(final))
        else:
            self.s.say("Your %s table %s is open; anyone can join it." % (GAME_NAMES[key], tid))
        await self.enter_table(tid)

    async def sit(self, tid, how):
        _, final = await self.s.req("JON %s" % tid)
        if not final.startswith("OK"):
            self.s.say("Can't sit down at table %s: %s" % (tid, err_text(final)))
            await self.poll()
            return
        await self.enter_table(tid)

    async def seated(self, tid, seat, opp, how):
        await self.enter_table(tid)

    async def enter_table(self, tid):
        data, final = await self.s.req("LST table=%s clients" % tid)
        t = next((t for t in parse_tables(data) if t["id"] == tid), None) if final.startswith("OK") else None
        if t is None:
            self.s.say("Table %s is gone." % tid)
            return
        cls = SCREENS.get(t["key"])
        if cls is None:
            self.s.say("%s cannot be played here yet." % GAME_NAMES.get(t["key"], t["key"]))
            return
        await self.s.go(cls(self.s, t))

    async def do_resume(self, cmd):
        tid = (cmd.args[0].lstrip("#") if cmd.args else "") or (self.mine[0][0] if self.mine else "")
        if not tid:
            self.s.say("You have no game to go back to.")
            return
        await self.enter_table(tid)

    async def do_quit(self, cmd):
        self.s.say("Goodbye.")
        self.s.closing = True


# ---- a game's screen ---------------------------------------------------------------------------------------

class GameScreen:
    """One table: the core's view and events, the panel, the buttons and the chat. A game sets KEY, SIDES and
    the board: board_base(pal) (static packets), board(scene) (items and areas), board_click(meaning) and the
    buttons it offers (buttons(): [(name, label)])."""

    KEY = ""
    SIDES = ("Seat 1", "Seat 2")
    VUE_FLAGS = "M"
    SLOTS = 10                  # button slots (two columns, rows 9 to 17); with a board's columns at most 30 areas
    HINT = ""
    PANEL = PANEL               # the panel's first column, its button columns, rows and width (characters)
    BTN_COLS, BTN_ROWS, BTN_W = BTN_COLS, BTN_ROWS, BTN_W

    def __init__(self, s, t):
        self.s, self.t = s, t
        self.tid = t["id"]
        self.pal = Pal(s.mono)
        self.mono = s.mono
        self.poll_every = s.svc.poll
        self.names = list(t["seated"])
        self.me = self.seat_of(t)
        self.v = None
        self.seq = 0
        self.over = ""
        self.started = t["status"] == "playing"
        self.confirm = None         # (button name, until): a button that waits for its second click
        self.bot_declined = set()   # bot names that turned this table down (each plays a limited number)
        self.bot_retry = False
        self.status_note = ""
        self.console = chatcmd.Console(self.s.say, self.s.req, lambda: self.s.name,
                                       ["list", "resign", "invite", "who", "tables", "top", "score", "note", "motd",
                                        "help", "quit"],
                                       kinds=[self.KEY], label=GAME_NAMES.get(self.KEY, ""),
                                       handlers={"quit": self.do_quit, "invite": self.do_invite},
                                       width=80, current=lambda: self.tid)
        self.clock_at = time.monotonic()
        self.deadline = None        # (seat, monotonic time) when the clock of the seat to move runs out
        self.clients = {}           # seat -> what its player plays with ("with GameMaster 1.0.0 on a Macintosh")

    def seat_of(self, t):
        low = [n.lower() for n in t["seated"]]
        me = (self.s.name or "").lower()
        return low.index(me) if me in low else -1

    # ---- the core ---------------------------------------------------------------------------------------
    async def enter(self):
        if self.me < 0:             # a watcher: the core counts the table's watchers
            _, final = await self.s.req("SEE %s" % self.tid)
            self.watching = final.startswith("OK")
            if not self.watching:
                self.s.say("Can't watch table %s: %s" % (self.tid, err_text(final)))
        await self.refresh(full=True)
        data, final = await self.s.req("SIN %s %d" % (self.tid, max(0, self.seq - 4)))
        self.events(data, quiet=False)
        if final.startswith("OK") and len(final.split()) > 1:
            self.seq = max(self.seq, int(final.split()[1]))
        if self.me < 0:
            self.s.say("You are watching table %s." % self.tid)

    async def leave(self):
        if getattr(self, "watching", False):
            self.watching = False
            await self.s.req("LVE %s" % self.tid)

    async def refresh(self, full=False):
        data, final = await self.s.req("LST table=%s clients" % self.tid)
        t = next((t for t in parse_tables(data) if t["id"] == self.tid), None) if final.startswith("OK") else None
        if t is not None:
            self.t, self.names = t, list(t["seated"])
            self.me = self.seat_of(t)
            self.started = t["status"] == "playing"
        data, final = await self.s.req("VUE %s %s" % (self.tid, self.VUE_FLAGS + "C" + ("G" if full else "")))
        if final.startswith("OK"):
            self.clients = parse_clients(data)      # what each seat's player plays with, kept apart from the view
            data = [l for l in data if not l.startswith("CLIENT ")]
            mod = registry.get(self.KEY)
            self.v = mod.parse_view(data)
            self.raw = data
            self.got_view(data, full)
            self.clock_at = time.monotonic()
            if full and self.seq == 0:
                self.seq = self.v.get("seq", 0)
        elif t is None and not self.over:
            self.over = "This table is gone."

    def got_view(self, lines, full):
        """A game's hook: the view's lines, parsed by the game module (self.v)."""

    def events(self, data, quiet=False):
        """The table's events: each one's words to on_event, its words to the message area. Consecutive steps of
        one player ("bot moves 13 to 10", "bot moves 10 to 8") share one line."""
        lines = []
        n = self._events(data, quiet, lines)
        me = self.name(self.me) + " " if self.me >= 0 else None
        out = []
        for d in lines:
            if me and d.startswith(me) and set(d.split()[1:3]) & {"moves", "jumps", "plays", "fires", "sows",
                                                                  "draws", "puts", "drives"}:
                continue
            if "falls with the ground" in d:        # the board shows it
                continue
            w = d.split(" ", 2)
            if out and len(w) == 3 and w[1] in ("moves", "jumps") and out[-1][0] == w[:2] and \
                    len(out[-1][1]) + len(w[2]) < 58:
                out[-1][1] += ", " + w[2]
            else:
                out.append([w[:2], d])
        if out:
            self.s.say([d for _, d in out])
        return n

    def _events(self, data, quiet, lines):
        n = 0
        joined = {}                 # seat -> the index in lines of its JOIN, for the CLIENT right after it
        for l in data:
            p = l.split(" ", 3)
            if len(p) < 4 or p[0] != "EVT" or not p[2].isdigit():
                continue
            seq = int(p[2])
            if seq <= self.seq and quiet:
                continue
            self.seq = max(self.seq, seq)
            n += 1
            f = p[3].split()
            if f[0] == "CLIENT":            # what a seat's player plays with: joins its JOIN's line, or a line
                c = parse_client(p[3])      # of its own when it changes mid-game; nothing for a hidden one
                if c is not None and c[0] != self.me:
                    seat, say = c
                    self.clients[seat] = say
                    if joined.get(seat) is not None and joined[seat] == len(lines) - 1:
                        lines[-1] = client_sentence(self.name(seat), say, "sat down", TEXT_MAX)
                        joined[seat] = None
                    else:
                        lines.append(client_change_sentence(self.name(seat), say, TEXT_MAX))
                continue
            if f[0] == "JOIN" and len(f) >= 3 and f[1].isdigit():
                i = int(f[1])
                self.names += ["-"] * (i + 1 - len(self.names))
                self.names[i] = f[2]
            if (f[0] == "UNINVITE" and len(f) >= 4 and f[3] == "declined" and botflag.is_bot_name(f[2])
                    and quiet and self.me >= 0):
                self.bot_declined.add(f[2].lower())     # that bot name plays all the tables it may: ask the next
                self.bot_retry = True
            d = describe(self.KEY, p[3], self.names)
            self.on_event(f)
            if f[0] == "END":
                self.over = d or "Game over."
            if d:
                lines.append(d)
                if f[0] == "JOIN" and f[1].isdigit():
                    joined[int(f[1])] = len(lines) - 1
        return n

    def on_event(self, f):
        """A game's hook: one event's words."""

    async def poll(self):
        data, final = await self.s.req("SIN %s %d" % (self.tid, self.seq))
        if not final.startswith("OK"):
            return
        if self.events(data, quiet=True):
            await self.refresh()
        elif self.v is not None and not self.v.get("rows"):
            await self.refresh()        # waiting for players: the start comes with the next view
        if self.bot_retry and not self.playing() and not self.over:
            self.bot_retry = False
            name = botflag.next_name([n for n in self.names if n != "-"] + sorted(self.bot_declined))
            if name is not None:
                await self.s.req("INV %s %s" % (self.tid, name))

    async def move(self, token, end=False):
        """MOV; the reply's events go to the message area. -> the final line."""
        data, final = await self.s.req("MOV %s %s" % (self.tid, token))
        self.events(data, quiet=True)
        if not final.startswith("OK"):
            self.s.say(self.refusal(token, err_text(final)))
        elif final.startswith("OK done") and end:
            data, final2 = await self.s.req("END %s" % self.tid)
            self.events(data, quiet=True)
            if not final2.startswith("OK"):
                self.s.say("Can't end the turn: %s" % err_text(final2))
        await self.refresh()
        return final

    async def staged(self, verb):
        data, final = await self.s.req("%s %s" % (verb, self.tid))
        self.events(data, quiet=True)
        if not final.startswith("OK"):
            self.s.say("Can't: %s" % err_text(final))
        await self.refresh()
        return final

    def refusal(self, token, reason):
        return "Can't play %s: %s" % (token, reason)

    # ---- state ------------------------------------------------------------------------------------------
    def playing(self):
        return bool(self.v and self.v.get("rows")) and not self.over

    def my_turn(self):
        return self.me >= 0 and self.playing() and self.v.get("turn") == self.me

    def name(self, seat):
        n = self.names[seat] if 0 <= seat < len(self.names) else "-"
        return n if n != "-" else "(empty)"

    def status(self):
        """The two status lines."""
        if self.over:
            parts = wrap_lines([self.over], 80 - self.PANEL)
            return parts[0], " ".join(parts[1:])
        if not self.playing():
            return ("Waiting for players." if self.me >= 0 else "Not started yet."), \
                ("Click Bot to play the bot." if self.me >= 0 else "")
        turn = self.v.get("turn", -1)
        if turn == self.me:
            return "Your move.", self.status_note
        return "%s's move." % self.name(turn) if 0 <= turn < len(self.names) else "", self.status_note

    def clock_line(self):
        c = self.v and self.v.get("clock")
        if not c or self.over or not self.playing():
            return ""
        seat, left, limit = c
        now = time.monotonic()
        # one deadline per turn: the core's count moves on between views, and a line that went back and forth
        # by a minute would cost a packet at every poll
        at = self.clock_at + left           # when it runs out, by the latest view
        if self.deadline is None or self.deadline[0] != seat or abs(self.deadline[1] - at) > 5:
            self.deadline = (seat, at)
        left = max(0, self.deadline[1] - now)
        who = "You have" if seat == self.me else "%s has" % self.name(seat)[:12]
        return "%s %s" % (who, clock_words(left))

    def buttons(self):
        """[(name, label)] for the slots, in order; a game adds its own before these."""
        out = []
        if not self.over and self.me >= 0:
            if not self.playing():
                out.append(("bot", "Invite the bot"))
            else:
                out.append(("resign", "Resign"))
        out.append(("lobby", "Lobby"))
        return out

    # ---- drawing ----------------------------------------------------------------------------------------
    def scene(self):
        pal = self.pal
        P, W = self.PANEL, 80 - self.PANEL
        title = "%s, table %s" % (GAME_NAMES.get(self.KEY, self.KEY), self.tid)
        base = [R.fill(0, 0, 640, 160, R.BLACK), R.fill(P * 8, 0, 640 - P * 8, 8, pal.title_bg),
                txt(P + 1, 0, title[:W - 1], pal.title_fg, pal.title_bg)] + self.board_base()
        sc = Scene(("game", self.tid, self.base_key()), base, scroll=(20, 4, pal.msg_bg), entry=True)
        for macro_slot, data in self.macros().items():
            sc.macro(macro_slot, data)
        for i in range(min(len(self.names), 4)):      # whose move it is shows in the status line, not here
            side = self.SIDES[i] if i < len(self.SIDES) else "Seat %d" % (i + 1)
            line_item(sc, ("seat", i), P, 2 + i, self.seat_text(i, side, W), pal.text, R.BLACK, W)
        a, b = self.status()
        line_item(sc, "st1", P, 6, a, pal.good, R.BLACK, W)
        line_item(sc, "st2", P, 7, b or self.clock_line(), pal.text, R.BLACK, W)
        self.current = self.place_buttons(self.buttons())
        now = time.monotonic()
        for slot, b in enumerate(self.current):
            if b is None:
                continue
            name, label = b
            hot = self.confirm is not None and self.confirm[0] == name and now < self.confirm[1]
            self.button(sc, slot, "Sure? " + label if hot else label, hot)
        for slot in range(self.SLOTS):
            col, row = self.slot_at(slot)
            sc.area(col * 8, row * 8, self.BTN_W * 8, 8, ("slot", slot))
        line_item(sc, "hint", P, 19, self.hint(), pal.dim, R.BLACK, W)
        self.board(sc)
        return sc

    def base_key(self):
        return ()

    COMMON = ("bot", "resign", "lobby")

    def place_buttons(self, buttons):
        """The buttons in their slots: the game's own from the first slot, the common ones (the bot or Resign,
        then Lobby) always in the last two, so a move never moves them; None for an empty slot."""
        out = [None] * self.SLOTS
        own = [b for b in buttons if b[0] not in self.COMMON][:self.SLOTS - 2]
        for i, b in enumerate(own):
            out[i] = b
        common = [b for b in buttons if b[0] in self.COMMON]
        for b in common:
            out[self.SLOTS - 1 if b[0] == "lobby" else self.SLOTS - 2] = b
        return out

    def seat_note(self, seat):
        """A few words after a seat's name (a score, hit points)."""
        return ""

    def seat_text(self, i, side, width, note=True):
        """A seat's line: the side, the player and a note; for another player, what they play with as far as it
        fits ("Black: ann, GameMaster 1.0.0 on a Mac")."""
        text = "%s: %s%s%s" % (side, self.name(i), " (you)" if i == self.me else "", self.seat_note(i) if note else "")
        say = self.clients.get(i) if i != self.me else None
        if say:
            room = width - len(text) - 2
            say = fit(say[5:] if say.startswith("with ") else say, room) if room >= 8 else ""
            if say:
                text += ", " + say
        return text

    def slot_at(self, slot):
        n = len(self.BTN_COLS)
        return self.BTN_COLS[slot % n], self.BTN_ROWS[slot // n]

    def button(self, sc, slot, label, hot=False):
        col, row = self.slot_at(slot)
        pal = self.pal
        fg, bg = (pal.hot_fg, pal.hot_bg) if hot else (pal.btn_fg, pal.btn_bg)
        sc.add(("btn", slot), txt(col, row, " " + label[:self.BTN_W - 1], fg, bg, self.BTN_W),
               R.fill(col * 8, row * 8, self.BTN_W * 8, 8, R.BLACK))

    def macros(self):
        return {}

    def board_base(self):
        return []

    def board(self, sc):
        pass

    def hint(self):
        return self.HINT if self.me >= 0 and not self.over else ""

    def grid(self, sc, tag, x0, y0, cw, ch, cols, rows):
        """Click areas for a grid: a B area per column and an H area per row, so one click names the square. A row's
        area reaches a pixel into the next row and past the right edge, so the frame the front end inverts under the
        pointer lies on the grid's lines, which a move never draws again: a square drawn inside its lines while the
        row is framed leaves no stray line when the pointer leaves (the first area defined wins on the shared
        line)."""
        for i in range(cols):
            sc.area(x0 + i * cw, y0, cw, rows * ch, ("col", tag, i))
        for j in range(rows):
            sc.area(x0, y0 + j * ch, cols * cw + 1, ch + 1, ("row", tag, j), "H")

    # ---- input ------------------------------------------------------------------------------------------
    async def click(self, m):
        if m[0] in ("col", "row"):
            m = self.lone(m)
            if m is None:
                return
        if m[0] == "slot":
            if m[1] < len(self.current) and self.current[m[1]] is not None:
                await self.press(self.current[m[1]][0])
            return
        if self.over:
            self.s.say("The game is over. Click Lobby for another.")
            return
        if self.me < 0:
            return
        if not self.playing():
            self.s.say("The game has not started yet.")
            return
        await self.board_click(m)

    async def press(self, name):
        if name in ("resign",):
            now = time.monotonic()
            if not (self.confirm and self.confirm[0] == name and now < self.confirm[1]):
                self.confirm = (name, now + 10)
                self.s.say("Click Resign again within 10 seconds to resign the game.")
                return
            self.confirm = None
            _, final = await self.s.req("RES %s" % self.tid)
            if not final.startswith("OK"):
                self.s.say("Can't resign: %s" % err_text(final))
            await self.poll()
            return
        if name == "lobby":
            await self.s.go(Lobby(self.s))
            return
        if name == "bot":
            name = botflag.next_name([n for n in self.names if n != "-"]) or "bot"
            _, final = await self.s.req("INV %s %s" % (self.tid, name))
            self.s.say("You invited the bot; it sits down in a few seconds." if final.startswith("OK")
                       else "Can't invite the bot: %s" % err_text(final))
            return
        await self.game_button(name)

    async def game_button(self, name):
        pass

    def lone(self, m):
        """A click that gave a grid's column or row only (between the areas): None to ignore it."""
        return None

    async def board_click(self, m):
        pass

    def typed_move(self, text):
        """A typed line -> a move token to play, or None (then it is chat)."""
        return None

    async def typed(self, text):
        if await self.console.handle(text):
            return
        if self.me >= 0 and self.playing() and not self.over:
            token = self.typed_move(text)
            if token:
                await self.move(token, end=True)
                return
        _, final = await self.s.req("CHT %s %s" % (self.tid, text[:56]))
        if not final.startswith("OK"):
            self.s.say("Can't: %s" % err_text(final))
        else:
            await self.poll()

    async def do_quit(self, cmd):
        self.s.say("Goodbye.")
        self.s.closing = True

    async def do_invite(self, cmd):
        name = cmd.args[0]
        _, final = await self.s.req("INV %s %s" % (self.tid, name))
        self.s.say(("You invited %s." % name) if final.startswith("OK") else "Can't invite %s: %s"
                   % (name, err_text(final)))


SCREENS = {}


def screen(cls):
    SCREENS[cls.KEY] = cls
    return cls


# ---- Checkers ----------------------------------------------------------------------------------------------

CK_MACRO = 13                   # macro slots 13-16: Black's man, White's man, Black's king, White's king


@screen
class Checkers(GameScreen):
    KEY = "checkers/1"
    SIDES = ("Black", "White")
    SLOTS = 8
    HINT = "Click a piece, then where it goes."
    SQ_W, SQ_H, BX, BY = 40, 20, 8, 0

    def __init__(self, s, t):
        super().__init__(s, t)
        self.sel = None
        p = self.pal
        self.light = p.c(R.YELLOW, R.WHITE)
        self.dark = p.c(R.GREEN, R.BLACK)

    def flip(self):
        return self.me == 0

    def xy(self, n):
        from .games.checkers import rc
        r, c = rc(n, 8)
        if self.flip():
            r, c = 7 - r, 7 - c
        return self.BX + c * self.SQ_W, self.BY + r * self.SQ_H

    def macros(self):
        """The dark squares of a row (two macros), and the four pieces (a man and a king of each side), each
        drawn on its square from the square's corner."""
        rows = {}
        for parity in (0, 1):
            data = b"".join(R.fill(self.BX + c * self.SQ_W, 0, self.SQ_W, self.SQ_H, self.dark, raw=True)
                            for c in range(8) if (c + parity) % 2 == 1)
            rows[1 + parity] = data
        for i, ch in enumerate("xoXO"):
            rows[CK_MACRO + i] = self.piece_data(ch)
        return rows

    def piece_data(self, ch):
        """A man: a shadow, the rim, the body and a ring cut in it; a king: two men stacked, a yellow crown
        (white on a mono screen) in the top one's ring. Black's men are black with a white rim, White's white
        with a black rim."""
        black = ch in "xX"
        rim, body = (R.WHITE, R.BLACK) if black else (R.BLACK, R.WHITE)
        cx = self.SQ_W // 2

        def man(cy, ry):
            return [(cx, cy, 15, ry, rim), (cx, cy, 13, ry - 1, body), (cx, cy, 9, ry - 3, rim), (cx, cy, 8, ry - 4, body)]
        shadow = [(cx + 1, 12, 15, 7, R.BLACK)]
        if ch in "xo":
            discs = shadow + man(10, 7)
        else:
            crown = self.pal.c(R.YELLOW, R.WHITE if not black else R.WHITE)
            discs = shadow + [(cx, 12, 15, 6, rim), (cx, 12, 13, 5, body)] + man(8, 6)[:3] + [(cx, 8, 8, 2, crown)]
        return b"".join(R.disc(x, y, rx, ry, c, raw=True) for x, y, rx, ry, c in discs)

    def board_base(self):
        out = [R.fill(self.BX, self.BY, 8 * self.SQ_W, 8 * self.SQ_H, self.light)]
        out += [R.run(1 + r % 2, 0, self.BY + r * self.SQ_H) for r in range(8)]
        return out

    def piece(self, ch, x, y):
        """A man or king at the square whose corner is x, y (its macro)."""
        return [R.run(CK_MACRO + "xoXO".index(ch), x, y)]

    def targets(self, frm):
        from .games.checkers import steps_of
        return [(b, tok) for a, b, tok in steps_of(self.v["moves"]) if a == frm]

    def froms(self):
        from .games.checkers import steps_of
        return {a for a, _, _ in steps_of(self.v["moves"])}

    def board(self, sc):
        v = self.v
        cells = v["cells"] if v else ""
        sel = None
        if self.my_turn() and v["phase"] == "move":
            sel = v["path"][-1] if v["path"] else self.sel
            if sel not in self.froms():
                sel = None
        tg = {b for b, _ in self.targets(sel)} if sel else set()
        self.grid(sc, "ck", self.BX, self.BY, self.SQ_W, self.SQ_H, 8, 8)
        for n in range(1, 33):
            x, y = self.xy(n)
            ch = cells[n - 1] if len(cells) >= n else "."
            if ch == "." and n != sel and n not in tg:
                continue
            # a square is drawn a pixel inside its top and left edges, where the frame of the row under the pointer
            # lies (GameScreen.grid)
            if n == sel or n in tg:
                ground = [R.panel(x + 1, y + 1, self.SQ_W - 1, self.SQ_H - 1,
                                  self.pal.c(R.MAGENTA if n == sel else R.CYAN, R.WHITE), self.dark)]
            else:
                ground = [R.fill(x + 1, y + 1, self.SQ_W - 1, self.SQ_H - 1, self.dark)]
            sc.add(("sq", n), ground + (self.piece(ch, x, y) if ch != "." else []),
                   R.fill(x + 1, y + 1, self.SQ_W - 1, self.SQ_H - 1, self.dark))

    def status(self):
        a, b = super().status()
        if self.v and self.v.get("rows") and not b:
            m0, k0, m1, k1 = self.v["counts"]
            b = "Black %d%s, White %d%s" % (m0 + k0, " (%d K)" % k0 if k0 else "", m1 + k1,
                                            " (%d K)" % k1 if k1 else "")
        if self.v and self.v.get("offer") is not None and not self.over:
            b = "%s offers a draw." % ("You" if self.v["offer"] == self.me else self.name(self.v["offer"]))
        return a, b

    def buttons(self):
        """The same buttons all game long (a button that does not apply says why when clicked): a move then
        costs no button bytes."""
        out = []
        if self.me >= 0 and self.playing():
            out += [("draw", "Offer a draw"), ("undo", "Undo jump"), ("accept", "Accept draw"),
                    ("reject", "Reject draw")]
        return out + super().buttons()

    async def game_button(self, name):
        if name in ("accept", "reject") and not (self.v["phase"] == "draw" and self.my_turn()):
            self.s.say("No draw is offered to you.")
        elif name in ("accept", "reject", "draw"):
            await self.move(name)
        elif name == "undo":
            if not (self.v["path"] and self.my_turn()):
                self.s.say("Undo takes back a jump of a line of jumps.")
                return
            self.sel = None
            await self.staged("UND")

    async def board_click(self, m):
        from .games.checkers import sq
        if m[0] != "cell":
            return
        c, r = m[2], m[3]
        if self.flip():
            r, c = 7 - r, 7 - c
        n = sq(r, c, 8)
        if not n:
            return
        if not self.my_turn() or self.v["phase"] != "move":
            self.s.say("It is not your move." if not self.my_turn() else "Answer the draw offer first.")
            return
        sel = self.v["path"][-1] if self.v["path"] else self.sel
        hit = dict(self.targets(sel)) if sel else {}
        if n in hit:
            final = await self.move(hit[n], end=True)
            self.sel = n if final.startswith("OK more") else None
        elif n in self.froms():
            self.sel = n
        else:
            self.sel = None
            if self.froms() and len(self.froms()) == len({a for a in self.froms()}):
                self.s.say("That piece cannot move. %s" % ("You must jump." if any("x" in t for t in self.v["moves"])
                                                           else ""))

    def typed_move(self, text):
        import re
        t = text.strip().lower()
        return t if re.match(r"^\d{1,2}([-x]\d{1,2})+$", t) else None


# ---- Backgammon ---------------------------------------------------------------------------------------------

BG_PW, BG_BAR = 22, 20               # a point's width, the bar's
BG_X0 = 4                             # the left edge of the left half
BG_TOP, BG_BOT, BG_PH = 4, 156, 64    # the top edge, the bottom edge, a point's length
BG_DISC = 9                           # stacked checkers' spacing
BG_TRAY_X = BG_X0 + 12 * BG_PW + BG_BAR + 4    # the tray for borne-off checkers
BG_MACRO = 3                          # macro slots 3-6: the points (down and up, in the two colors); 11, 12: the checkers
BG_BANDS = 12
BG_RING = 8                           # macro slot 8: the mark where a picked checker would land
BG_FELT = 56                          # the felt a point's redraw covers (its checkers and its cut triangle)
BG_HOT_Y = 84                         # where the lower half's click areas start, below the dice


def point_cut(ht, half, bands=BG_BANDS):
    """The bands of a point, from the tip: [(distance from the tip, its end, half width)], without those at the
    tip narrower than three pixels: a point ends blunt rather than in a one-pixel needle."""
    out = []
    for i in range(bands):
        a, b_ = ht * i // bands, ht * (i + 1) // bands
        hw = int(half * a / (ht - 1))
        if hw >= 1:
            out.append((a, b_, hw))
    return out


def point_data(x0, y0, wd, ht, apex_down, color):
    """A point (a triangle) as macro data: a stack of fills about 5 pixels high, each as wide as the triangle at
    its narrow end and a pixel over the next (no gap where a front end scales the screen). The front ends have no
    polygon; this draws in 100 bytes, one macro, and fast (ten commands)."""
    cx = x0 + (wd - 1) // 2
    out = b""
    for k, (a, b_, hw) in enumerate(point_cut(ht, (wd - 1) / 2.0)):
        y = (y0 + ht - b_) if apex_down else (y0 + a)
        out += R.fill(cx - hw, y - (1 if k and not apex_down else 0), 2 * hw + 1, b_ - a + (1 if k else 0), color,
                      raw=True)
    return out


def point_outline(x0, y0, wd, ht, apex_down, color):
    """The same point's outline (macro data), for every other point on a mono screen: the base, the sides and the
    blunt tip."""
    cx = x0 + (wd - 1) // 2
    a, _, hw = point_cut(ht, (wd - 1) / 2.0)[0]
    by, ty = (y0, y0 + ht - 1 - a) if apex_down else (y0 + ht - 1, y0 + a)
    return b"".join(R.line(*xy, color, raw=True) for xy in ((x0, by, x0 + wd - 1, by), (x0, by, cx - hw, ty),
                                                            (x0 + wd - 1, by, cx + hw, ty), (cx - hw, ty, cx + hw, ty)))


@screen
class Backgammon(GameScreen):
    KEY = "backgammon/1"
    SIDES = ("White", "Black")
    HINT = "Click a checker, then where it goes."

    def __init__(self, s, t):
        super().__init__(s, t)
        self.sel = None
        p = self.pal
        self.felt = p.c(R.GREEN, R.BLACK)
        self.pt_cols = (p.c(R.RED, R.WHITE), p.c(R.YELLOW, R.WHITE))
        self.frame = p.c(R.BLUE, R.WHITE)
        self.tray_x = BG_TRAY_X

    def bottom_seat(self):
        return 1 if self.me == 1 else 0

    def pos(self, p):
        """Absolute point 1-24 -> (x of its left edge, top: bool). The viewer's home board is at the bottom
        right: for seat 0 points 1-6 there, for seat 1 points 24-19."""
        i = p if self.bottom_seat() == 0 else 25 - p
        if i <= 12:                         # bottom row, right to left
            k = 12 - i                      # 0 at the left
            top = False
        else:                               # top row, left to right
            k = i - 13
            top = True
        x = BG_X0 + k * BG_PW + (BG_BAR if k >= 6 else 0)
        return x, top

    def macros(self):
        """The points (pointing down for the top row, up for the bottom one, in the two point colors; on a mono
        screen every other point is an outline), then the two checkers: a rim, the body and a ring."""
        out = {}
        for t, top in enumerate((True, False)):
            for ci, color in enumerate(self.pt_cols):
                draw = point_outline if self.mono and ci == 1 else point_data
                out[BG_MACRO + t * 2 + ci] = draw(1, 0, 21, BG_PH, top, color)
        out[BG_RING] = b"".join(R.disc(6, 3, rx, ry, c, raw=True) for rx, ry, c in
                                ((6, 3, R.BLACK), (4, 2, self.pal.c(R.CYAN, R.WHITE)), (2, 1, R.BLACK)))
        body = self.pal.c(R.RED, R.BLACK)
        ring = R.BLACK if not self.mono else R.WHITE
        out[BG_MACRO + 8] = b"".join(R.disc(10, 5, rx, ry, c, raw=True) for rx, ry, c in
                                     ((9, 4, R.BLACK), (8, 3, R.WHITE), (5, 2, R.BLACK), (4, 1, R.WHITE)))
        out[BG_MACRO + 9] = b"".join(R.disc(10, 5, rx, ry, c, raw=True) for rx, ry, c in
                                     ((9, 4, R.WHITE), (8, 3, body), (5, 2, ring), (4, 1, body)))
        return out

    def tri(self, p):
        x, top = self.pos(p)
        return [R.run(BG_MACRO + (0 if top else 2) + p % 2, x, BG_TOP if top else BG_BOT - BG_PH)]

    def board_base(self):
        out = [R.fill(0, 0, 336, 160, self.frame),
               R.fill(BG_X0, BG_TOP, 6 * BG_PW, BG_BOT - BG_TOP, self.felt),
               R.fill(BG_X0 + 6 * BG_PW + BG_BAR, BG_TOP, 6 * BG_PW, BG_BOT - BG_TOP, self.felt),
               R.fill(self.tray_x, BG_TOP, 28, BG_BOT - BG_TOP, R.BLACK)]
        for p in range(1, 25):
            out += self.tri(p)
        return out

    def base_key(self):
        return (self.bottom_seat(),)

    def checker(self, seat, cx, cy):
        """Seat 0 (White): white with a black rim and ring; seat 1 (Black): red (black on a mono screen) with a
        white rim. One macro each."""
        return [R.run(BG_MACRO + 8 + seat, cx - 10, cy - 5)]

    def slot_xy(self, p, k):
        x, top = self.pos(p)
        cy = (BG_TOP + 5 + k * BG_DISC) if top else (BG_BOT - 5 - k * BG_DISC)
        return x + BG_PW // 2, cy

    def bare(self, p):
        """A point without checkers: its felt and its triangle. The felt goes back a pixel inside the point's
        column, where the frame of a click area under the pointer may lie (BG_HOT_Y, board)."""
        x, top = self.pos(p)
        y0 = BG_TOP if top else BG_BOT - BG_FELT
        return [R.fill(x + 1, y0, BG_PW - 1, BG_FELT, self.felt)] + self.tri(p)

    def board(self, sc):
        v = self.v
        if not v:
            return
        pts = v["pts"]
        sel, targets = self.selection()
        # once a checker is picked, a ring where it would land on each point it can reach (and in the tray when it
        # can bear off). A dot on each stack that can move before the pick was tried: it cost each such stack's
        # redraw twice a step, a turn's bytes doubled.
        drawn = {}
        for p in range(1, 25):
            n = pts[p - 1]
            seat = 0 if n > 0 else 1
            n = abs(n)
            x, top = self.pos(p)
            if n:               # a point is drawn whole when its checkers change: felt, triangle, checkers
                draw = self.bare(p)
                shown = n if n <= 6 else 5          # a tall stack: five checkers and "+N" for the rest
                for k in range(shown):
                    cx, cy = self.slot_xy(p, k)
                    draw += self.checker(seat, cx, cy)
                if n > shown:
                    cx, cy = self.slot_xy(p, shown)
                    draw += self.more(seat, cx, cy, n - shown)
                sc.add(("pt", p), draw, self.bare(p))
                drawn[p] = draw
        for t in targets:
            if t == "off":
                sc.add(("tg", "off"), self.target(self.tray_x + 14, 104), R.fill(self.tray_x + 6, 100, 17, 9, R.BLACK))
            elif isinstance(t, int):
                n = abs(pts[t - 1]) if (pts[t - 1] > 0) == (self.me == 0) else 0       # a blot hit is lifted
                cx, cy = self.slot_xy(t, min(n, 5))
                sc.add(("tg", t), self.target(cx, cy), self.under(t, cx - 6, cy - 3, 13, 7) or drawn.get(t)
                       or self.bare(t))
        if isinstance(sel, int):        # the picked point: a mark on the frame beside it
            x, top = self.pos(sel)
            my = BG_TOP - 3 if top else BG_BOT + 1
            mark = self.pal.c(R.YELLOW, R.WHITE)
            sc.add(("mk", sel), R.fill(x + 2, my, BG_PW - 4, 2, mark), R.fill(x + 2, my, BG_PW - 4, 2, self.frame))
        # the bar: the viewer's checkers in the lower half, the other side's in the upper
        bx = BG_X0 + 6 * BG_PW
        for seat in (0, 1):
            n = v["bar"][seat]
            low = seat == self.bottom_seat()
            shown = n if n <= 4 else 3              # three checkers and "+N" for the rest
            for k in range(min(n, 4)):
                cy = (BG_HOT_Y + 6 + k * BG_DISC) if low else (76 - 4 - k * BG_DISC)
                clear = R.fill(bx + 1, cy - 4, BG_BAR - 1, 9, self.frame)
                draw = self.checker(seat, bx + BG_BAR // 2, cy) if k < shown else \
                    [clear] + self.more(seat, bx + BG_BAR // 2, cy, n - shown)
                sc.add(("bar", seat, k), draw, clear)
            if sel == "bar" and low:
                sc.add(("mk", "bar"), R.fill(bx + 2, BG_BOT + 1, BG_BAR - 4, 2, self.pal.c(R.YELLOW, R.WHITE)),
                       R.fill(bx + 2, BG_BOT + 1, BG_BAR - 4, 2, self.frame))
        # the tray: borne-off counts; the viewer's at the bottom
        for seat in (0, 1):
            low = seat == self.bottom_seat()
            y = 140 if low else 12
            n = v["off"][seat]
            if n:
                count = (self.checker(seat, self.tray_x + 10, y + 4)
                         + [R.text(self.tray_x + 20, y, "%d" % n, R.WHITE, R.BLACK)]) if n < 10 else \
                    self.more(seat, self.tray_x + 14, y + 4, n)     # from ten on the count alone, in the side's colors
                sc.add(("off", seat), [R.fill(self.tray_x + 1, y, 27, 9, R.BLACK)] + count,
                       R.fill(self.tray_x + 1, y, 27, 9, R.BLACK))
        # click areas: a B area for each column's upper half and an H area for its lower half, so a click names
        # the point by itself. The H area's frame (inverted under the pointer) lies on pixels a move never draws
        # again: the column's left edge and the next one's, the line below the dice and the board's frame.
        for k, (x, w) in enumerate(self.columns()):
            sc.area(x, BG_TOP, w, BG_HOT_Y - BG_TOP, ("bgpt", k, 0))
            sc.area(x, BG_HOT_Y, w + 1, BG_BOT - BG_HOT_Y + 1, ("bgpt", k, 1), "H")
        # the dice still to play, in the right half's middle; the cube on its owner's side
        dice = v["dice"] if self.playing() else []
        if dice:
            x0 = BG_X0 + 6 * BG_PW + BG_BAR + 4
            color = R.WHITE if v["turn"] == 0 else self.pal.c(R.RED, R.WHITE)
            dy = BG_HOT_Y - 24
            draw = [R.fill(x0, dy, 128, 24, self.felt)]
            if len(dice) > 2:           # doubles: one die and how many times it is still to play
                draw += [R.die(x0 + 8, dy, color, dice[0]),
                         R.text(x0 + 64, dy + 8, "x%d" % len(dice), R.WHITE, self.felt)]
            else:
                draw += [R.die(x0 + 8 + i * 56, dy, color, d) for i, d in enumerate(dice)]
            sc.add("dice", draw, R.fill(x0, dy, 128, 24, self.felt))
        # the doubling cube: in the tray's middle showing 64 until it is taken, then on its owner's side with its
        # value; drawn with a shadow and a lit top edge
        cube = v["cube"]
        owner = v["cube_owner"]
        if v.get("cube_ok") is not None or cube > 1:
            x = self.tray_x + 4
            y = 62 if owner < 0 else (118 if owner == self.bottom_seat() else 26)
            face = self.pal.c(R.YELLOW, R.WHITE)
            sc.add(("cube", x, y, cube, owner), [R.fill(x + 2, y + 2, 20, 16, self.pal.c(R.BLUE, R.WHITE) if not
                                                        self.mono else R.BLACK),
                                                 R.fill(x, y, 20, 16, face), R.fill(x, y, 20, 2, R.WHITE),
                                                 R.text(x, y + 5, "64" if owner < 0 else str(cube), R.BLACK, face,
                                                        R.CENTER, 20)],
                   R.fill(x, y, 22, 18, R.BLACK))

    def more(self, seat, cx, cy, n):
        """In the place of a stack's next checker, how many more it holds: "+2", or "10" from ten on, in the
        side's colors."""
        label = "+%d" % n if n < 10 else "%d" % n
        fg, bg = (R.WHITE, self.pal.c(R.RED, R.BLACK)) if seat else (R.BLACK, R.WHITE)
        return [R.text(cx - 4 * len(label), cy - 4, label, fg, bg)]

    def target(self, cx, cy):
        """The mark where a picked checker would land: a ring, light on dark and dark on light (a macro)."""
        return [R.run(BG_RING, cx - 6, cy - 3)]

    def under(self, p, x, y, w, h):
        """Packets that put back what lies under a rectangle on point p where no checker is: the felt and the
        triangle's bands; None for a mono screen's outlined point (its lines are drawn again with the point)."""
        if self.mono and p % 2:
            return None
        px, top = self.pos(p)
        y0 = BG_TOP if top else BG_BOT - BG_PH
        out = [R.fill(x, y, w, h, self.felt)]
        cx = px + 1 + 10
        for k, (a, b_, hw) in enumerate(point_cut(BG_PH, 10.0)):
            by0, by1 = ((y0 + BG_PH - b_, y0 + BG_PH - a + (1 if k else 0)) if top
                        else (y0 + a - (1 if k else 0), y0 + b_))
            ix0, iy0 = max(x, cx - hw), max(y, by0)
            ix1, iy1 = min(x + w, cx + hw + 1), min(y + h, by1)
            if ix0 < ix1 and iy0 < iy1:
                out.append(R.fill(ix0, iy0, ix1 - ix0, iy1 - iy0, self.pt_cols[p % 2]))
        return out

    def columns(self):
        """The board's click columns, left to right: six points, the bar, six points, the tray."""
        out = [(BG_X0 + k * BG_PW, BG_PW) for k in range(6)] + [(BG_X0 + 6 * BG_PW, BG_BAR)]
        out += [(BG_X0 + 6 * BG_PW + BG_BAR + k * BG_PW, BG_PW) for k in range(6)] + [(self.tray_x, 28)]
        return out

    def where(self, col, half):
        """A click's column and half (0 top) -> a point 1-24, "bar" or "off"."""
        if col == 6:
            return "bar"
        if col == 13:
            return "off"
        k = col if col < 6 else col - 1
        i = 13 + k if half == 0 else 12 - k         # the viewer's numbering (seat 0's)
        return i if self.bottom_seat() == 0 else 25 - i

    def selection(self):
        """(the picked point or "bar", the targets of it: points and "off")."""
        if not self.my_turn() or self.v["phase"] != "move":
            return None, set()
        steps = self.steps()
        sel = self.sel
        if sel not in {a for a, _ in steps}:
            sel = None
        if sel is None and self.v["bar"][self.me] and any(a == "bar" for a, _ in steps):
            sel = "bar"
        return sel, {b for a, b in steps if a == sel}

    def steps(self):
        out = []
        for m in self.v["moves"]:
            p = m.split("-")
            if len(p) == 2:
                a = "bar" if p[0] == "bar" else int(p[0]) if p[0].isdigit() else None
                b = "off" if p[1] == "off" else int(p[1]) if p[1].isdigit() else None
                if a is not None and b is not None:
                    out.append((a, b))
        return out

    BTN_ROWS = (10, 12, 14, 16, 18)
    BUTTON_SLOTS = {"roll": 0, "double": 1, "end": 2, "undo": 3, "take": 4, "drop": 5, "accept": 6, "reject": 7}

    def pips(self, seat):
        """The pips seat has still to move (seat 0 moves from 24 down to 1, seat 1 the other way; the bar is 25)."""
        v = self.v
        if seat == 0:
            n = sum(p * c for p, c in enumerate(v["pts"], 1) if c > 0)
        else:
            n = sum((25 - p) * -c for p, c in enumerate(v["pts"], 1) if c < 0)
        return n + 25 * v["bar"][seat]


    def status(self):
        """Three lines for the status box: whose turn, what the dice say, what to do now."""
        a, b = super().status()
        v = self.v
        do = ""
        if v and v.get("rows") and not self.over:
            phase = v["phase"]
            mine = v["turn"] == self.me
            other = self.name(v["turn"])
            if phase == "roll":
                can = bool(v["cube_ok"] and v["cube_ok"][self.me])
                b = ("Your roll." if mine else "%s to roll." % other)
                do = ("Click Roll%s." % (", or Double to offer %d" % (v["cube"] * 2) if can else "")) if mine else ""
            elif phase == "move":
                dice = v.get("rolled") or ()
                left = len(v["dice"])
                b = "Rolled %s, %d to play." % ("-".join(map(str, dice)), left) \
                    if v["moves"] else "Rolled %s, all played." % "-".join(map(str, dice))
                if mine:
                    if not v["moves"]:
                        do = "Click Done (or Undo)."
                    else:
                        do = "Pick a checker, then a ringed point."
                else:
                    b = "%s rolled %s." % (other, "-".join(map(str, dice)))
            elif phase == "take":
                b = "Double to %d offered." % (v["cube"] * 2)
                do = "Take it, or Drop and lose %d." % v["cube"] if mine else "Waiting for %s to answer." % other
            elif phase == "resign" and v["offer"]:
                b = "%s offers to resign (%s)." % (self.name(v["offer"][0]), v["offer"][2])
                do = "Accept or Reject." if mine else ""
            if v["match"]:
                b = (b + "  Score %d-%d of %d" % (v["score"][0], v["score"][1], v["match"]))[:36]
        self.instruction = do
        return a, b

    def scene(self):
        """The common screen, with the status in a box of four lines (whose turn, the dice, what to do now, the
        clock) and the buttons below it."""
        sc = super().scene()
        pal, P = self.pal, self.PANEL
        box = pal.c(R.CYAN, R.WHITE)
        sc.base += [R.fill(P * 8 + 2, 37, 300, 2, box), R.fill(P * 8 + 2, 73, 300, 2, box),
                    R.fill(P * 8 + 2, 37, 2, 38, box), R.fill(P * 8 + 300, 37, 2, 38, box)]
        for i in range(min(len(self.names), 2)):     # the names, short of the pips' column
            line_item(sc, ("seat", i), P, 2 + i, self.seat_text(i, self.SIDES[i], 31, note=False), pal.text, R.BLACK,
                      31)
        if self.v and self.v.get("rows"):           # the pips beside each name: three characters a step
            for seat in (0, 1):
                sc.add(("pips", seat, self.pips(seat)), txt(74, 2 + seat, "%3d" % self.pips(seat), pal.dim),
                       b"")
            sc.add("pips", txt(74, 1, "pips", pal.dim), R.fill(74 * 8, 8, 32, 8, R.BLACK))
        a, b = self.status()
        line_item(sc, "st1", P + 1, 5, a, pal.good, R.BLACK, 36)
        line_item(sc, "st2", P + 1, 6, b, pal.text, R.BLACK, 36)
        line_item(sc, "do", P + 1, 7, self.instruction, pal.dim, R.BLACK, 36)
        line_item(sc, "hint", P + 1, 8, self.clock_line(), pal.text, R.BLACK, 36)
        return sc

    def hint(self):
        return ""

    def buttons(self):
        """The buttons that apply now, each always in its own place: Roll and Double before the roll, Done and Undo
        after it, Take and Drop when a double is offered, Accept and Reject an offer to resign."""
        out = []
        v = self.v
        if self.me >= 0 and self.playing() and v["turn"] == self.me:
            phase = v["phase"]
            if phase == "roll":
                out.append(("roll", "Roll"))
                if v["cube_ok"] and v["cube_ok"][self.me]:
                    out.append(("double", "Double"))
            elif phase == "move":
                if not v["moves"]:
                    out.append(("end", "Done"))
                if len(v["dice"]) < (v.get("need") or len(v["dice"])):
                    out.append(("undo", "Undo"))
            elif phase == "take":
                out += [("take", "Take"), ("drop", "Drop")]
            elif phase == "resign":
                out += [("accept", "Accept"), ("reject", "Reject")]
        return out + super().buttons()

    def place_buttons(self, buttons):
        out = [None] * self.SLOTS
        for b in buttons:
            if b[0] in self.BUTTON_SLOTS:
                out[self.BUTTON_SLOTS[b[0]]] = b
            else:
                out[self.SLOTS - 1 if b[0] == "lobby" else self.SLOTS - 2] = b
        return out

    async def game_button(self, name):
        self.sel = None
        if not self.my_turn():
            self.s.say("It is not your turn.")
            return
        phase = self.v["phase"]
        if name == "end":
            if phase != "move":
                self.s.say("Done ends a turn once its steps are played.")
            elif self.v["moves"]:
                self.s.say("Play your steps first; the dice show what is left.")
            else:
                await self.staged("END")
        elif name == "undo":
            if phase != "move":
                self.s.say("Undo takes back a step of this turn.")
            else:
                await self.staged("UND")
        elif name in ("roll", "double") and phase != "roll":
            self.s.say("You have rolled already." if phase == "move" else "Not now.")
        elif name in ("take", "drop") and phase != "take":
            self.s.say("No double waits for your answer.")
        else:
            await self.move(name)

    async def board_click(self, m):
        if not self.my_turn():
            self.s.say("It is not your move.")
            return
        if self.v["phase"] == "roll":
            await self.move("roll")
            return
        if self.v["phase"] != "move":
            return
        if m[0] != "bgpt":
            return
        where = self.where(m[1], m[2])
        steps = self.steps()
        sel, targets = self.selection()
        if sel is not None and where in targets:
            token = "%s-%s" % (sel, where)
            final = await self.move(token)
            if final.startswith("OK"):
                still = {a for a, _ in self.steps()}
                self.sel = where if where in still else None
            return
        if any(a == where for a, _ in steps):
            self.sel = where
        else:
            self.sel = None
            if steps:
                self.s.say("No checker of yours can move from there." if where != "off" else "Pick a checker first.")

    def typed_move(self, text):
        import re
        t = text.strip().lower()
        if t in ("roll", "double", "take", "drop"):
            return t
        return t if re.match(r"^(bar|\d{1,2})-(off|\d{1,2})$", t) else None


# ---- the pieces of the round-piece games --------------------------------------------------------------------

def stone(seat, cx, cy, rx, ry, pal, colors=None):
    """A round piece readable in mono: seat 0 a dark disc with a white rim, seat 1 a white disc with a black rim."""
    if seat == 0:
        return [R.disc(cx, cy, rx, ry, R.WHITE), R.disc(cx, cy, rx - 2, max(1, ry - 2), R.BLACK)]
    return [R.disc(cx, cy, rx, ry, R.BLACK), R.disc(cx, cy, rx - 2, max(1, ry - 2), R.WHITE)]


def stone_data(seat, cx, cy, rx, ry, shine=True, shadow=True):
    """A round piece as macro data, readable in mono: a shadow below and to the right, then seat 0 dark with a
    white rim and a shine, seat 1 white with a black rim."""
    d = [(cx + 1, cy + 1, rx, ry, R.BLACK)] if shadow else []
    if seat == 0:
        d += [(cx, cy, rx, ry, R.WHITE), (cx, cy, rx - 2, max(1, ry - 2), R.BLACK)]
        if shine and rx >= 6:
            d.append((cx - rx // 3, cy - max(1, ry // 3), max(1, rx // 4), 1, R.WHITE))
    else:
        d += [(cx, cy, rx, ry, R.BLACK), (cx, cy, rx - 2, max(1, ry - 2), R.WHITE)]
    return b"".join(R.disc(x, y, a, b_, c, raw=True) for x, y, a, b_, c in d)


def sq_name(c, r):
    return "%s%d" % (chr(97 + c), r + 1)


def lines_macro(x0, y0, x1, y1, dx, dy, n, color):
    """n lines from (x0, y0)-(x1, y1), each moved by dx, dy from the last, as macro data."""
    if x0 == x1:        # a line down is a fill 2 pixels wide: the Mac front end, 640 pixels to 630, loses 1-pixel ones
        return b"".join(R.fill(x0 + k * dx, y0 + k * dy, 2, y1 - y0, color, raw=True) for k in range(n))
    return b"".join(R.line(x0 + k * dx, y0 + k * dy, x1 + k * dx, y1 + k * dy, color, raw=True) for k in range(n))


# ---- Reversi ------------------------------------------------------------------------------------------------

RV_MACRO = 17                   # macro slots 17, 18: the discs


@screen
class Reversi(GameScreen):
    KEY = "reversi/1"
    SIDES = ("Black", "White")
    HINT = "Click a square to place a disc."
    CW, CH, BX, BY = 40, 20, 8, 0

    def __init__(self, s, t):
        super().__init__(s, t)
        self.board_col = self.pal.c(R.GREEN, R.BLACK)
        self.line_col = self.pal.c(R.BLACK, R.WHITE)

    def n(self):
        return (self.v or {}).get("n", 8) or 8

    def base_key(self):
        return (self.n(),)

    def macros(self):
        """The two discs, drawn in a square from its corner."""
        n = self.n()
        cw, ch = 320 // n, 160 // n
        return {RV_MACRO + seat: stone_data(seat, cw // 2, ch // 2, cw // 2 - 5, ch // 2 - 3) for seat in (0, 1)}

    def board_base(self):
        n = self.n()
        cw, ch = 320 // n, 160 // n
        out = [R.fill(self.BX, self.BY, n * cw, n * ch, self.board_col)]
        out += [R.line(self.BX, self.BY + k * ch, self.BX + n * cw - 1, self.BY + k * ch, self.line_col)
                for k in range(1, n)]
        out += [R.fill(self.BX + k * cw, self.BY, 2, n * ch, self.line_col)
                for k in range(1, n)]
        return out

    def board(self, sc):
        n = self.n()
        cw, ch = 320 // n, 160 // n
        self.grid(sc, "rv", self.BX, self.BY, cw, ch, n, n)
        for r, row in enumerate((self.v or {}).get("rows") or []):
            for c, cell in enumerate(row):
                if cell in "01":
                    x, y = self.BX + c * cw, self.BY + r * ch
                    sc.add(("d", c, r), R.run(RV_MACRO + int(cell), x, y),
                           R.fill(x + 2, y + 2, cw - 3, ch - 3, self.board_col))

    def status(self):
        a, b = super().status()
        if self.v and self.v.get("rows") and not b:
            b = "Black %d, White %d" % self.v["counts"]
        return a, b

    async def board_click(self, m):
        if m[0] != "cell":
            return
        if not self.my_turn():
            self.s.say("It is not your move.")
            return
        word = sq_name(m[2], m[3])
        if word not in self.v["moves"]:
            self.s.say("%s is not a legal move." % word)
            return
        await self.move(word)

    def typed_move(self, text):
        t = text.strip().lower()
        return t if self.v and t in self.v["moves"] else None


# ---- Mancala ------------------------------------------------------------------------------------------------

MN_MACRO, MN_SEEDS = 52, 10     # macro slots 52-62: a pit with 0 to 10 seeds
MN_AT = [(17, 12), (23, 12), (20, 7), (20, 17), (11, 12), (29, 12), (14, 7), (26, 7), (14, 17), (26, 17)]


@screen
class Mancala(GameScreen):
    KEY = "mancala/1"
    SIDES = ("South", "North")
    HINT = "Click one of your pits to sow it."
    PX, PW, STORE_W = 48, 40, 36

    def __init__(self, s, t):
        super().__init__(s, t)
        self.wood = self.pal.c(R.YELLOW, R.WHITE)

    def bottom(self):
        return self.me if self.me in (0, 1) else 0

    def pit_rect(self, seat, p):
        """Seat's pit p (1-6): the bottom row runs left to right, the top row right to left."""
        if seat == self.bottom():
            return self.PX + (p - 1) * self.PW, 96
        return self.PX + (6 - p) * self.PW, 40

    def store_rect(self, seat):
        return (self.PX + 6 * self.PW + 4, 40) if seat == self.bottom() else (self.PX - self.STORE_W - 4, 40)

    def base_key(self):
        return (self.bottom(),)

    def macros(self):
        """A pit with 0 to 10 seeds: the hollow (a black oval) and the seeds in it, from the pit's corner."""
        cols = [self.pal.c(c, R.WHITE) for c in (R.WHITE, R.CYAN, R.RED, R.YELLOW, R.GREEN, R.MAGENTA)]
        out = {}
        for k in range(MN_SEEDS + 1):
            out[MN_MACRO + k] = R.disc(20, 12, 16, 11, R.BLACK, raw=True) + b"".join(
                R.disc(x, y, 3, 2, cols[i % len(cols)], raw=True) for i, (x, y) in enumerate(MN_AT[:k]))
        return out

    def board_base(self):
        rim = self.pal.c(R.RED, R.BLACK)
        out = [R.fill(4, 28, 328, 104, self.wood), R.fill(4, 28, 328, 2, rim), R.fill(4, 130, 328, 2, rim),
               R.fill(4, 28, 2, 104, rim), R.fill(330, 28, 2, 104, rim)]
        for seat in (0, 1):
            x, y = self.store_rect(seat)
            out.append(R.disc(x + 18, y + 40, 17, 38, R.BLACK))
        return out

    def board(self, sc):
        from .games.mancala import pit, store
        v = self.v
        if not v or not v.get("rows"):
            return
        last = v.get("last")
        for seat in (0, 1):
            for p in range(1, 7):
                x, y = self.pit_rect(seat, p)
                n = pit(v, seat, p)
                mine = seat == self.me
                if mine:
                    sc.area(x + 3, y, self.PW - 6, 24, ("pit", p))
                hot = last is not None and last == (seat, p)
                fg, bg = (R.WHITE, R.BLACK) if hot and self.mono else (self.pal.c(R.RED if hot else R.BLACK, R.BLACK),
                                                                       self.wood)
                ny = y - 9 if y < 64 else y + 25            # the count: above a top pit, below a bottom one
                sc.add(("p", seat, p), [R.run(MN_MACRO + min(n, MN_SEEDS), x, y),
                                        R.text(x + 12, ny, "%2d" % n, fg, bg, R.LEFT, 16)])
            x, y = self.store_rect(seat)
            sc.add(("s", seat), R.text(x + 10, y + 36, "%2d" % store(v, seat), R.WHITE, R.BLACK, R.LEFT, 16))
        sc.add("labels", [R.text(4, 136, "Your store is on the right." if self.me >= 0 else "", self.pal.dim,
                                 R.BLACK, R.LEFT, 216)], R.fill(4, 136, 216, 8, R.BLACK))

    def seat_note(self, seat):
        from .games.mancala import store
        return " (%d)" % store(self.v, seat) if self.v and self.v.get("rows") else ""

    async def board_click(self, m):
        if m[0] != "pit":
            return
        if not self.my_turn():
            self.s.say("It is not your move.")
            return
        tok = str(m[1])
        if tok not in self.v["moves"]:
            self.s.say("Pit %d is empty or cannot be played." % m[1])
            return
        await self.move(tok)

    def typed_move(self, text):
        t = text.strip()
        return t if self.v and t in self.v["moves"] else None


# ---- Five in a Row ------------------------------------------------------------------------------------------

FR_MACRO = 19                   # macro slots 19, 20: the grid's lines; 21, 22: the stones


@screen
class FiveInARow(GameScreen):
    KEY = "fiveinarow/1"
    SIDES = ("Black", "White")
    HINT = "Click a square to place a stone."
    X0, Y0 = 22, 2

    def __init__(self, s, t):
        super().__init__(s, t)
        self.board_col = self.pal.c(R.YELLOW, R.BLACK)
        self.line_col = self.pal.c(R.BLACK, R.WHITE)

    def n(self):
        return (self.v or {}).get("n", 15) or 15

    def cell(self):
        n = self.n()
        return min(20, 300 // n), min(10, 150 // n)

    def base_key(self):
        return (self.n(),)

    def macros(self):
        n = self.n()
        cw, ch = self.cell()
        k = (n + 1 + 3) // 4
        return {FR_MACRO: lines_macro(self.X0, self.Y0, self.X0 + n * cw, self.Y0, 0, ch, k, self.line_col),
                FR_MACRO + 1: lines_macro(self.X0, self.Y0, self.X0, self.Y0 + n * ch, cw, 0, k, self.line_col),
                FR_MACRO + 2: stone_data(0, cw // 2, ch // 2, cw // 2 - 2, ch // 2 - 1, shadow=False),
                FR_MACRO + 3: stone_data(1, cw // 2, ch // 2, cw // 2 - 2, ch // 2 - 1, shadow=False)}

    def board_base(self):
        n = self.n()
        cw, ch = self.cell()
        k = (n + 1 + 3) // 4
        out = [R.fill(self.X0, self.Y0, n * cw + 1, n * ch + 1, self.board_col)]
        for i in range(0, n + 1, k):
            out.append(R.run(FR_MACRO, 0, i * ch) if i + k <= n + 1 else b"")
            out.append(R.run(FR_MACRO + 1, i * cw, 0) if i + k <= n + 1 else b"")
        for i in range((n + 1) // k * k, n + 1):
            out.append(R.line(self.X0, self.Y0 + i * ch, self.X0 + n * cw, self.Y0 + i * ch, self.line_col))
            out.append(R.line(self.X0 + i * cw, self.Y0, self.X0 + i * cw, self.Y0 + n * ch, self.line_col))
        out.append(R.text(self.X0, self.Y0 + n * ch + 2, "a" + " " * ((n - 2) * cw // 8 + 1) + chr(96 + n),
                          self.pal.dim, R.BLACK))
        out.append(R.text(2, self.Y0 + 1, "1", self.pal.dim, R.BLACK))
        out.append(R.text(2, self.Y0 + (n - 1) * ch + 1, "%d" % n, self.pal.dim, R.BLACK))
        return out

    def board(self, sc):
        n = self.n()
        cw, ch = self.cell()
        self.grid(sc, "fr", self.X0, self.Y0, cw, ch, n, n)
        for (c, r), seat in (self.v or {}).get("stones", {}).items():
            x, y = self.X0 + c * cw, self.Y0 + r * ch
            sc.add(("st", c, r), R.run(FR_MACRO + 2 + seat, x, y), R.fill(x + 1, y + 1, cw - 1, ch - 1, self.board_col))

    async def board_click(self, m):
        if m[0] != "cell":
            return
        if not self.my_turn():
            self.s.say("It is not your move.")
            return
        word = sq_name(m[2], m[3])
        if self.v["moves"] and word not in self.v["moves"]:
            self.s.say("%s is taken." % word)
            return
        await self.move(word)

    def typed_move(self, text):
        import re
        t = text.strip().lower()
        return t if re.match(r"^[a-t]\d{1,2}$", t) else None


# ---- Dots and Boxes -----------------------------------------------------------------------------------------

DB_MACRO = 29                   # macro slot 29: a row of dots


@screen
class DotsAndBoxes(GameScreen):
    KEY = "dotsandboxes/1"
    SIDES = ("A", "B", "C", "D")
    HINT = "Click between two dots to draw a line."
    X0, Y0 = 16, 4
    SEAT_COLS = (R.RED, R.CYAN, R.YELLOW, R.MAGENTA)

    def size(self):
        v = self.v or {}
        return v.get("w", 5) or 5, v.get("h", 5) or 5

    def pitch(self):
        w, h = self.size()
        return min(60, 312 // w), min(30, 148 // h)

    def base_key(self):
        return self.size()

    def macros(self):
        """A row of dots (when it fits a macro)."""
        w, h = self.size()
        px, py = self.pitch()
        data = b"".join(R.disc(self.X0 + i * px, self.Y0, 3, 2, R.WHITE, raw=True) for i in range(w + 1))
        return {DB_MACRO: data} if len(data) <= 126 else {}

    def board_base(self):
        w, h = self.size()
        px, py = self.pitch()
        out = []
        for j in range(h + 1):
            if self.macros():
                out.append(R.run(DB_MACRO, 0, j * py))
            else:
                out += [R.disc(self.X0 + i * px, self.Y0 + j * py, 3, 2, R.WHITE) for i in range(w + 1)]
        return out

    def board(self, sc):
        v = self.v
        if not v or not v.get("rows"):
            return
        w, h = self.size()
        px, py = self.pitch()
        # the half grid: columns are dot, box, dot, ...; rows likewise
        xs = []
        for i in range(2 * w + 1):
            x = self.X0 + (i // 2) * px + (-3 if i % 2 == 0 else 3)
            xs.append((x, 6 if i % 2 == 0 else px - 6))
        ys = []
        for j in range(2 * h + 1):
            y = self.Y0 + (j // 2) * py + (-3 if j % 2 == 0 else 3)
            ys.append((y, 6 if j % 2 == 0 else py - 6))
        for i, (x, ww) in enumerate(xs):
            sc.area(x, self.Y0 - 3, ww, h * py + 6, ("col", "db", i))
        for j, (y, hh) in enumerate(ys):
            sc.area(self.X0 - 3, y, w * px + 6, hh, ("row", "db", j), "H")
        line_col = self.pal.c(R.WHITE, R.WHITE)
        for j, row in enumerate(v["hl"]):
            for i, c in enumerate(row):
                if c == "-":
                    sc.add(("h", i, j), R.fill(self.X0 + i * px + 2, self.Y0 + j * py, px - 3, 2, line_col))
        for j, row in enumerate(v["vl"]):
            for i, c in enumerate(row):
                if c == "|":
                    sc.add(("v", i, j), R.fill(self.X0 + i * px, self.Y0 + j * py + 2, 2, py - 3, line_col))
        for j, row in enumerate(v["bx"]):
            for i, c in enumerate(row):
                if c in "0123":
                    seat = int(c)
                    color = self.pal.c(self.SEAT_COLS[seat], R.WHITE)
                    bx, by = self.X0 + i * px + 4, self.Y0 + j * py + 4
                    sc.add(("b", i, j), [R.fill(bx, by, px - 7, py - 7, color),
                                         R.text(self.X0 + i * px + px // 2 - 4, self.Y0 + j * py + py // 2 - 3,
                                                "ABCD"[seat], R.BLACK, color)])

    def seat_note(self, seat):
        c = (self.v or {}).get("counts") or []
        return " (%d)" % c[seat] if seat < len(c) and self.v.get("rows") else ""

    def line_of(self, i, j):
        """A half-grid square -> the line there ("a1-b1"), or None for a dot or a box."""
        if i % 2 == 1 and j % 2 == 0:
            c, r = i // 2, j // 2
            return "%s-%s" % (sq_name(c, r), sq_name(c + 1, r))
        if i % 2 == 0 and j % 2 == 1:
            c, r = i // 2, j // 2
            return "%s-%s" % (sq_name(c, r), sq_name(c, r + 1))
        return None

    async def board_click(self, m):
        if m[0] != "cell":
            return
        if not self.my_turn():
            self.s.say("It is not your move.")
            return
        line = self.line_of(m[2], m[3])
        if line is None:
            self.s.say("Click between two dots.")
            return
        if self.v["moves"] and line not in self.v["moves"]:
            self.s.say("That line is drawn already.")
            return
        await self.move(line)

    def typed_move(self, text):
        import re
        t = text.strip().lower()
        return t if re.match(r"^[a-n]\d{1,2}-?[a-n]\d{1,2}$", t) else None


# ---- Naval Combat -------------------------------------------------------------------------------------------

NV_MACRO = 23                   # macro slots 23, 24: the grids' lines; 25-27: a hit, a sunk square, a miss


@screen
class NavalCombat(GameScreen):
    KEY = "navalcombat/1"
    SIDES = ("Fleet 1", "Fleet 2")
    CW, CH, Y0 = 16, 10, 22
    GRIDS = {"own": 8, "tgt": 176}

    def __init__(self, s, t):
        super().__init__(s, t)
        self.water = self.pal.c(R.BLUE, R.BLACK)
        self.frm = None             # the first end of a ship being placed

    def macros(self):
        """The grids' lines; a hit (fire), a sunk ship's square (a cross on red) and a miss (a splash), drawn in a
        square from its inner corner."""
        n = 10
        color = self.pal.c(R.CYAN, R.WHITE)
        w, h = self.CW - 1, self.CH - 1
        fire, core = self.pal.c(R.RED, R.WHITE), self.pal.c(R.YELLOW, R.BLACK)
        sunk = self.pal.c(R.RED, R.WHITE)
        return {NV_MACRO: lines_macro(0, self.Y0, 0, self.Y0 + n * self.CH, self.CW, 0, n + 1, color),
                NV_MACRO + 1: lines_macro(0, self.Y0, n * self.CW, self.Y0, 0, self.CH, n + 1, color),
                NV_MACRO + 2: R.disc(7, 4, 6, 4, fire, raw=True) + R.disc(7, 4, 3, 2, core, raw=True),
                NV_MACRO + 3: (R.fill(0, 0, w, h, sunk, raw=True) + R.line(2, 1, w - 3, h - 2, R.BLACK, raw=True)
                               + R.line(2, h - 2, w - 3, 1, R.BLACK, raw=True)),
                NV_MACRO + 4: R.disc(7, 4, 3, 2, R.WHITE, raw=True) + R.disc(7, 4, 1, 1, self.water, raw=True)}

    def board_base(self):
        out = []
        for name, x in self.GRIDS.items():
            out += [R.fill(x, self.Y0, 160, 100, self.water), R.run(NV_MACRO, x, 0), R.run(NV_MACRO + 1, x, 0),
                    R.text(x, 2, "Your fleet" if name == "own" else "Their waters", self.pal.head, R.BLACK),
                    R.text(x, self.Y0 - 9, " a b c d e f g h i j"[1:], self.pal.dim, R.BLACK)]
        out += [R.text(0, self.Y0 + 104, "Rows 1-10 from the top.", self.pal.dim, R.BLACK)]
        return out

    def mark(self, x, y, ch, fg, bg):
        return R.text(x + 4, y + 1, ch, fg, bg, R.LEFT, 8)

    def board(self, sc):
        v = self.v
        if not v or not v.get("rows"):
            return
        me = self.me if self.me in (0, 1) else 0
        other = 1 - me
        placing = v["phase"] == "place" and self.me >= 0
        hit_bg, ship_bg = self.pal.c(R.RED, R.WHITE), self.pal.c(R.CYAN, R.WHITE)
        for grid, seat in (("own", me), ("tgt", other)):
            x0 = self.GRIDS[grid]
            for r, row in enumerate(v["boards"][seat]):
                for c, ch in enumerate(row):
                    if ch == ".":
                        if grid == "own" and placing and self.frm == (c, r):
                            pass
                        else:
                            continue
                    x, y = x0 + c * self.CW + 1, self.Y0 + r * self.CH + 1
                    if ch == "s":
                        draw = R.fill(x, y, self.CW - 1, self.CH - 1, ship_bg)
                    elif ch == "o":
                        draw = R.run(NV_MACRO + 4, x, y)
                    elif ch == "x":
                        draw = [R.fill(x, y, self.CW - 1, self.CH - 1, ship_bg if grid == "own" else self.water),
                                R.run(NV_MACRO + 2, x, y)]
                    elif ch == "#":
                        draw = R.run(NV_MACRO + 3, x, y)
                    else:
                        draw = R.fill(x, y, self.CW - 1, self.CH - 1, self.pal.c(R.MAGENTA, R.WHITE))
                    sc.add((grid, c, r, ch), draw, R.fill(x, y, self.CW - 1, self.CH - 1, self.water))
        if placing:
            self.grid(sc, "own", self.GRIDS["own"], self.Y0, self.CW, self.CH, 10, 10)
        self.grid(sc, "tgt", self.GRIDS["tgt"], self.Y0, self.CW, self.CH, 10, 10)

    def status(self):
        a, b = super().status()
        v = self.v
        if v and v.get("rows") and not self.over:
            if v["phase"] == "place" and self.me >= 0:
                if v["ready"][self.me]:
                    a, b = "Your fleet is ready.", "Waiting for the other fleet."
                elif v["next"]:
                    a, b = "Place your ship of %d." % v["next"], ("Click its other end." if self.frm else
                                                                 "Click one end, then the other.")
                else:
                    a, b = "Your fleet is placed.", "Click Ready to start."
            elif v["phase"] == "shoot" and not b:
                b = "Afloat: yours %d, theirs %d" % (v["afloat"][self.me if self.me >= 0 else 0],
                                                    v["afloat"][1 - (self.me if self.me >= 0 else 0)])
        return a, b

    def hint(self):
        v = self.v
        if v and v.get("rows") and v["phase"] == "shoot" and self.my_turn():
            return "Click a square of their waters to fire."
        return ""

    def buttons(self):
        out = []
        v = self.v
        if self.me >= 0 and self.playing() and v["phase"] == "place" and not v["ready"][self.me]:
            out += [("random", "Place at random")]
            if v["mine"]:
                out += [("undo", "Undo ship"), ("clear", "Clear")]
            if not v["next"]:
                out.append(("ready", "Ready"))
        return out + super().buttons()

    async def game_button(self, name):
        self.frm = None
        await self.move(name)

    async def board_click(self, m):
        if m[0] != "cell":
            return
        v = self.v
        if m[1] == "own":
            if v["phase"] != "place" or v["ready"][self.me] or not v["next"]:
                return
            c, r = m[2], m[3]
            if v["next"] == 1:
                await self.move("%s-%s" % (sq_name(c, r), sq_name(c, r)))
                return
            if self.frm is None:
                self.frm = (c, r)
                return
            a, self.frm = self.frm, None
            if a == (c, r):
                return
            await self.move("%s-%s" % (sq_name(*a), sq_name(c, r)))
            return
        if v["phase"] != "shoot":
            self.s.say("Place your fleet first." if v["phase"] == "place" else "The game is over.")
            return
        if not self.my_turn():
            self.s.say("It is not your turn to fire.")
            return
        word = sq_name(m[2], m[3])
        if v["moves"] and word not in v["moves"]:
            self.s.say("You fired at %s already." % word)
            return
        await self.move(word)

    def typed_move(self, text):
        import re
        t = text.strip().lower()
        if t in ("random", "ready", "undo", "clear"):
            return t
        return t if re.match(r"^[a-j]\d{1,2}(-[a-j]\d{1,2})?$", t) else None


# ---- Grid Words ---------------------------------------------------------------------------------------------
# The colors of adapters/palette.py's default, on the front end's eight: the board dark (black), triple
# premiums filled (word: yellow, the default's warm word color; letter: magenta, its purple), double premiums
# as their label in the same color on the board, tiles white with black letters. Every premium carries its
# name (3W, 2W, 3L, 2L), so mono front ends show the same board.

GW_LABELS = {"TW": "3W", "DW": "2W", "TL": "3L", "DL": "2L"}
GW_FILL = {"TW": (R.BLACK, R.RED), "DW": (R.BLACK, R.MAGENTA), "TL": (R.WHITE, R.BLUE), "DL": (R.BLACK, R.CYAN)}
GW_TILE = 28                    # macro slot 28: a tile's body


@screen
class GridWords(GameScreen):
    KEY = "gridwords/1"
    SIDES = ("Player 1", "Player 2", "Player 3", "Player 4")
    VUE_FLAGS = ""
    X0, Y0, CW, CH = 4, 2, 20, 10

    def __init__(self, s, t):
        super().__init__(s, t)
        self.start = None           # (x, y, "H" or "V"): the first square of the word to play, 1-based
        self.word_col = self.pal.c(R.YELLOW, R.WHITE)
        self.letter_col = self.pal.c(R.MAGENTA, R.WHITE)
        self.grid_col = self.pal.c(R.BLUE, R.WHITE)
        self.tile_col = self.pal.c(R.YELLOW, R.WHITE)

    def size(self):
        v = self.v or {}
        return v.get("w", 15) or 15, v.get("h", 15) or 15

    def base_key(self):
        v = self.v or {}
        return self.size() + (tuple(sorted(v.get("prm", {}).items())),)

    def premium(self, kind, x, y, raw=False):
        """A premium square at board square x, y (0-based), filled in the board game's colors with its name;
        on a mono screen triples filled, doubles as a label."""
        if self.mono:
            fg, bg = (R.BLACK, R.WHITE) if kind in ("TW", "TL") else (R.WHITE, R.BLACK)
        else:
            fg, bg = GW_FILL[kind]
        return R.text(self.X0 + x * self.CW + 1, self.Y0 + y * self.CH + 1, " " + GW_LABELS[kind], fg, bg, R.LEFT,
                      self.CW - 1, raw=raw)

    def rows_by_pattern(self):
        w, h = self.size()
        prm = (self.v or {}).get("prm", {})
        rows = {}
        for (x, y), kind in prm.items():
            rows.setdefault(y - 1, []).append((x - 1, kind))
        patterns = {}
        for y, cells in rows.items():
            patterns.setdefault(tuple(sorted(cells)), []).append(y)
        return patterns

    def macros(self):
        w, h = self.size()
        out = {}
        slot = 30
        for cells in sorted(self.rows_by_pattern()):
            data = b"".join(self.premium(kind, x, 0, raw=True) for x, kind in cells)
            if len(data) <= 127 and slot < 50:
                out[slot] = data
                slot += 1
        k = (w + 1 + 1) // 2
        out[GW_TILE] = (R.fill(0, 0, self.CW - 1, self.CH - 1, self.tile_col, raw=True)
                        + R.fill(0, self.CH - 2, self.CW - 1, 1, R.BLACK, raw=True)
                        + R.fill(self.CW - 2, 0, 1, self.CH - 1, R.BLACK, raw=True))
        out[50] = lines_macro(self.X0, self.Y0, self.X0 + w * self.CW, self.Y0, 0, self.CH, (h + 2) // 2, self.grid_col)
        out[51] = lines_macro(self.X0, self.Y0, self.X0, self.Y0 + h * self.CH, self.CW, 0, 8, self.grid_col)
        return out

    def premium_base(self):
        w, h = self.size()
        out = [R.run(50, 0, 0), R.run(50, 0, (h + 2) // 2 * self.CH), R.run(51, 0, 0), R.run(51, 8 * self.CW, 0)]
        macros = self.macros()
        slots = {v: k for k, v in macros.items()}
        for cells, ys in sorted(self.rows_by_pattern().items()):
            data = b"".join(self.premium(kind, x, 0, raw=True) for x, kind in cells)
            for y in ys:
                if data in slots:
                    out.append(R.run(slots[data], 0, y * self.CH))
                else:
                    out += [self.premium(kind, x, y) for x, kind in cells]
        return out

    def board(self, sc):
        v = self.v
        if not v or not v.get("rows"):
            return
        w, h = self.size()
        self.grid(sc, "gw", self.X0, self.Y0, self.CW, self.CH, w, h)
        for (x, y), ch in v["tiles"].items():        # a tile: its body (a macro, with a shadow) and the letter
            tx, ty = self.X0 + (x - 1) * self.CW + 1, self.Y0 + (y - 1) * self.CH + 1
            sc.add(("t", x, y), [R.run(GW_TILE, tx, ty), R.text(tx + 5, ty, ch, R.BLACK, self.tile_col)])
        if self.start and self.my_turn():
            x, y, d = self.start
            sc.add("start", R.text(self.X0 + (x - 1) * self.CW + 1, self.Y0 + (y - 1) * self.CH + 1,
                                   " >" if d == "H" else " v", self.pal.hot_fg, self.pal.hot_bg, R.LEFT, self.CW - 1),
                   self.square_again(x, y))
        rack = v["rack"]
        if rack is not None and self.me >= 0:
            sc.add("rack", R.text((self.PANEL + 6) * 8, 8 * 8, " ".join(rack.replace("_", "?")) or "(empty)",
                                  R.BLACK, R.WHITE, R.LEFT, 8 * 15),
                   R.fill((self.PANEL + 6) * 8, 8 * 8, 15 * 8, 8, R.BLACK))

    def square_again(self, x, y):
        """What an empty square shows: its premium, or the board."""
        kind = (self.v or {}).get("prm", {}).get((x, y))
        if kind:
            return self.premium(kind, x - 1, y - 1)
        return R.fill(self.X0 + (x - 1) * self.CW + 1, self.Y0 + (y - 1) * self.CH + 1, self.CW - 1, self.CH - 1,
                      R.BLACK)

    def seat_note(self, seat):
        sc = (self.v or {}).get("scores") or []
        return " %d" % sc[seat] if seat < len(sc) and self.v.get("rows") else ""

    def board_base(self):
        return self.premium_base() + [R.text(self.PANEL * 8, 8 * 8, "Rack:", self.pal.text, R.BLACK)]

    def status(self):
        a, b = super().status()
        if self.v and self.v.get("rows") and not self.over and not b:
            b = "Bag: %d tiles" % self.v["bag"]
        return a, b

    def hint(self):
        return "Square (twice: down), type the word." if self.me >= 0 and not self.over else ""

    def buttons(self):
        out = []
        if self.me >= 0 and self.playing():
            out += [("pass", "Pass")]
        return out + super().buttons()

    async def game_button(self, name):
        if name == "pass" and not self.my_turn():
            self.s.say("It is not your move.")
        elif name == "pass":
            now = time.monotonic()
            if not (self.confirm and self.confirm[0] == name and now < self.confirm[1]):
                self.confirm = (name, now + 10)
                self.s.say("Click Pass again to pass your turn.")
                return
            self.confirm = None
            await self.move("PASS")

    async def board_click(self, m):
        if m[0] != "cell":
            return
        if not self.my_turn():
            self.s.say("It is not your move.")
            return
        x, y = m[2] + 1, m[3] + 1
        if self.start and self.start[:2] == (x, y):
            self.start = (x, y, "V" if self.start[2] == "H" else "H")
        else:
            self.start = (x, y, "H")


    def typed_move(self, text):
        import re
        t = text.strip()
        m = re.match(r"^(?:exch|exchange)\s+([A-Za-z_?]{1,7})$", t, re.I)
        if m:
            return "EXCH " + m.group(1).upper().replace("?", "_")
        if t.lower() == "pass":
            return "PASS"
        m = re.match(r"^([a-o])(\d{1,2})\s+(a|d|across|down)\s+([A-Za-z_]{2,15})$", t, re.I)
        if m:
            x, y = ord(m.group(1).lower()) - 96, int(m.group(2))
            return "PLAY %d %d %s %s" % (x, y, "H" if m.group(3).lower().startswith("a") else "V",
                                         m.group(4).upper())
        if self.start and re.match(r"^[A-Za-z_]{2,15}$", t):
            x, y, d = self.start
            return "PLAY %d %d %s %s" % (x, y, d, t.upper())
        return None

    async def move(self, token, end=False):
        final = await super().move(token, end)
        if final.startswith("OK"):
            self.start = None
        return final


# ---- Rummy Tile Classic -------------------------------------------------------------------------------------
# Tiles are drawn flat in the tile color with their name (R7, B12, K1, Y13; the letter carries the family on
# a mono screen) in the family color, as adapters/rtpalette.py's default does: white tiles, red, blue and black
# numbers. The front end has no dark gold, and yellow does not read on white, so the yellow family is drawn
# yellow on a black tile. The joker is a star.

RT_INK = {"R": R.RED, "B": R.BLUE, "K": R.BLACK, "Y": R.YELLOW}
RT_SETS, RT_RACK = 12, 3        # rows of sets on the screen, rows of the rack
RT_X, RT_TW = 24, 32            # where the tiles start, one tile's width (a tile and a gap)
RT_PITCH = 9                    # a row of tiles every 9 pixels: the frame of the row under the pointer lies in the
                                # one-pixel gaps between rows, which a move never draws


def rt_y(row):
    """The top of a row of sets (0 .. RT_SETS - 1) or of the rack (RT_SETS ..)."""
    return 8 + row * RT_PITCH + (4 if row >= RT_SETS else 0)


def rt_clear(y):
    """A row erased: the set's number and its tiles, not the pixel column where the row's frame lies."""
    return [R.fill(0, y, 16, 8, R.BLACK), R.fill(RT_X, y, 456 - RT_X, 8, R.BLACK)]


def rt_tiles(text):
    import re
    return re.findall(r"[RBKY]\d{1,2}|J", text.upper())


@screen
class RummyTile(GameScreen):
    KEY = "rummytile/1"
    SIDES = ("Seat 1", "Seat 2", "Seat 3", "Seat 4")
    VUE_FLAGS = ""
    PANEL = 57
    BTN_COLS, BTN_ROWS, BTN_W = (58,), (10, 11, 12, 13, 14, 15, 16, 17, 18), 21
    SLOTS = 9

    def __init__(self, s, t):
        super().__init__(s, t)
        self.sel = []               # rack positions picked
        self.tsel = None            # (set, position) of a table tile picked, 1-based
        self.top = 0                # the first set shown

    def got_view(self, lines, full):
        v = self.v
        v["sets"], v["hand"], v["tp"] = [], [], None
        for l in lines:
            f = l.split()
            if f[:1] == ["ST"] and len(f) >= 3:
                v["sets"].append((int(f[1]), f[2], f[3:]))
            elif f[:1] == ["H"]:
                v["hand"] += f[1:]
            elif f[:1] == ["TP"] and len(f) >= 4:
                v["tp"] = (f[1], f[2], f[3])
            elif f[:1] == ["W"] and len(f) == 2:
                v["turn"] = -1 if f[1] == "-" else int(f[1])
        v["rows"] = v["rows"] if "rows" in v else bool(v["sets"] or v["hand"])
        self.sel = [i for i in self.sel if i < len(v["hand"])]

    def look(self, t, picked=False):
        """A tile's (ink, body, text): the name in its family's ink on a white body (yellow on black), the joker a
        star; a picked tile in reverse."""
        if t == "J":
            fg, bg, text = R.BLACK, R.WHITE, " *"
        else:
            fg, bg, text = RT_INK[t[0]], (R.BLACK if t[0] == "Y" else R.WHITE), t
        if self.mono:
            fg, bg = R.BLACK, R.WHITE
        if picked:
            fg, bg = (R.WHITE, R.BLACK) if self.mono else (R.WHITE, R.MAGENTA)
        return fg, bg, text

    def tiles(self, y, looks):
        """A row of tiles from RT_X, one tile a slot of RT_TW pixels: each run of tiles that look alike is one
        text packet (the tiles' names, four characters apart)."""
        out, i = [], 0
        while i < len(looks):
            j = i
            while j + 1 < len(looks) and looks[j + 1][:2] == looks[i][:2]:
                j += 1
            fg, bg = looks[i][:2]
            text = "".join(t[2].ljust(4) for t in looks[i:j + 1])[:-1]
            out.append(R.text(RT_X + i * RT_TW, y, text, fg, bg, R.LEFT, (j - i) * RT_TW + 24))
            i = j + 1
        return out

    def board(self, sc):
        v = self.v
        if not v or not v.get("rows"):
            return
        sets = v.get("sets", [])
        shown = sets[self.top:self.top + RT_SETS]
        for row in range(RT_SETS):
            y = rt_y(row)
            if row < len(shown):
                k, kind, tiles = shown[row]
                ok = kind[0] in "rg"
                label = R.text(0, y, "%2d" % k, self.pal.text if ok else self.pal.hot_fg,
                               R.BLACK if ok else self.pal.hot_bg, R.LEFT, 16)
                draw = [label] + self.tiles(y, [self.look(t, self.tsel == (k, i + 1)) for i, t in enumerate(tiles[:13])])
                sc.add(("set", row, k, kind, tuple(tiles), self.tsel if self.tsel and self.tsel[0] == k else None),
                       draw, rt_clear(y))
        hand = v.get("hand", [])
        for row in range(RT_RACK):
            y = rt_y(RT_SETS + row)
            part = hand[row * 13:(row + 1) * 13]
            if part:
                draw = self.tiles(y, [self.look(t, row * 13 + i in self.sel) for i, t in enumerate(part)])
                sc.add(("rack", row, tuple(part), tuple(i for i in self.sel if row * 13 <= i < row * 13 + 13)),
                       draw, rt_clear(y))
        sc.add("rackl", R.text(0, rt_y(RT_SETS), "Rk", self.pal.head, R.BLACK))
        end = rt_y(RT_SETS + RT_RACK - 1) + 9
        for i in range(13):
            sc.area(RT_X + i * RT_TW, 8, RT_TW, end - 8, ("col", "rt", i))
        for row in range(RT_SETS + RT_RACK):
            sc.area(20, rt_y(row) - 1, 437, 10, ("row", "rt", row), "H")

    def seat_note(self, seat):
        v = self.v or {}
        return ""

    def status(self):
        a, b = super().status()
        v = self.v
        if v and v.get("rows") and not self.over:
            tp = v.get("tp")
            if self.my_turn() and tp:
                b = {"ok": "Done ends the turn.", "none": "Done draws a tile.", "sets": "A set is not valid.",
                     "meld": "First meld: 30 points (%s)." % tp[1], "joker": "The joker rule fails."}.get(tp[2], "")
            elif not b:
                b = ""
        return a, b

    def hint(self):
        if not self.my_turn():
            return ""
        if self.sel:
            return "Click a set, or New set."
        if self.tsel:
            return "A set, New set or the rack."
        return "Pick tiles, then a set."

    def buttons(self):
        out = []
        v = self.v
        if self.me >= 0 and self.playing():
            out += [("new", "New set"), ("end", "Done"), ("undo", "Undo"), ("reset", "Start over")]
        if v and len(v.get("sets", [])) > RT_SETS:
            out.append(("more", "More sets"))
        return out + super().buttons()

    def picked_tiles(self):
        hand = self.v["hand"]
        return "".join(hand[i] for i in sorted(self.sel) if i < len(hand))

    async def step(self, token):
        self.sel, self.tsel = [], None
        await self.move(token)

    async def game_button(self, name):
        if name == "more":
            n = len(self.v.get("sets", []))
            self.top = self.top + RT_SETS if self.top + RT_SETS < n else 0
            return
        if not self.my_turn():
            self.s.say("It is not your turn.")
            return
        if name == "new":
            if self.sel:
                await self.step("%s-n" % self.picked_tiles())
            elif self.tsel:
                await self.step("%d.%d-n" % self.tsel)
            else:
                self.s.say("Pick tiles from your rack or a tile on the table first.")
        elif name == "end":
            self.sel, self.tsel = [], None
            await self.staged("END")
        elif name == "undo":
            self.sel, self.tsel = [], None
            await self.staged("UND")
        elif name == "reset":
            await self.step("reset")

    def lone(self, m):
        if m[0] == "row":
            return ("cell", "rt", None, m[2])
        return None

    async def board_click(self, m):
        if m[0] != "cell" or not self.my_turn():
            if not self.my_turn():
                self.s.say("It is not your turn.")
            return
        col, row = m[2], m[3]
        sets = self.v.get("sets", [])
        if row >= RT_SETS:                      # the rack
            i = (row - RT_SETS) * 13 + (col if col is not None else 99)
            if self.tsel:
                await self.step("%d.%d-r" % self.tsel)
                return
            if i < len(self.v["hand"]):
                if i in self.sel:
                    self.sel.remove(i)
                else:
                    self.sel.append(i)
            return
        k = self.top + row
        if k >= len(sets):
            return
        number, _, tiles = sets[k]
        if self.sel:
            await self.step("%s-%d" % (self.picked_tiles(), number))
        elif self.tsel:
            if self.tsel[0] == number and col is not None and self.tsel[1] == col + 1:
                self.tsel = None
            else:
                await self.step("%d.%d-%d" % (self.tsel + (number,)))
        elif col is not None and col < len(tiles):
            self.tsel = (number, col + 1)

    def typed_move(self, text):
        import re
        t = text.strip()
        if re.match(r"^([RBKYrbkyJj0-9.]+-(\d+|[nNrR])|\d+/\d+|\d+-\d+|reset)$", t):
            return t
        return None

    async def typed(self, text):
        t = self.typed_move(text) if self.me >= 0 and self.playing() else None
        if t:
            self.sel, self.tsel = [], None
            await self.move(t)
            return
        await super().typed(text)


# ---- Cannon Fire --------------------------------------------------------------------------------------------

CF_GROUND, CF_TOP = 152, 24     # the arena's bottom and the top of the sky (y)


@screen
class CannonFire(GameScreen):
    KEY = "cannonfire/1"
    SIDES = ("Tank 1", "Tank 2", "Tank 3", "Tank 4")
    VUE_FLAGS = ""
    TANK_COLS = (R.WHITE, R.RED, R.CYAN, R.YELLOW, R.MAGENTA, R.GREEN, R.WHITE, R.RED)

    def __init__(self, s, t):
        super().__init__(s, t)
        self.ground = self.pal.c(R.GREEN, R.WHITE)
        self.sky = self.pal.c(R.BLUE, R.BLACK)
        self.aim = None

    def geometry(self):
        w = (self.v or {}).get("w") or len((self.v or {}).get("rows") or []) or 72
        band = 3 if w > 40 else 1               # columns drawn together: 72 columns as 24 bands of 12 pixels
        cw = max(1, 320 // w)
        return w, band, cw

    def base_key(self):
        return self.geometry()

    def board_base(self):
        out = [R.fill(0, CF_TOP, 336, CF_GROUND - CF_TOP + 1, self.sky)]
        if not self.mono:
            out.append(R.disc(300, CF_TOP + 14, 9, 5, R.YELLOW))      # the sun
        return out

    def height_y(self, h):
        return CF_GROUND - h * 2

    def board(self, sc):
        v = self.v
        if not v or not v.get("rows"):
            return
        w, band, cw = self.geometry()
        x0 = (336 - w * cw) // 2
        rows = v["rows"]
        for i in range(0, w, band):
            hs = rows[i:i + band]
            h = sum(hs) // len(hs) if hs else 0
            x = x0 + i * cw
            top = self.height_y(h)
            if h > 0:
                sc.add(("band", i, h), R.fill(x, top, cw * band, CF_GROUND - top + 1, self.ground),
                       R.fill(x, CF_TOP, cw * band, CF_GROUND - CF_TOP + 1, self.sky))
        for seat, tank in enumerate(v["tanks"]):
            if not tank:
                continue
            x, hp = tank
            h = rows[x] if 0 <= x < len(rows) else 0
            cx, cy = x0 + x * cw + cw // 2, self.height_y(h) - 4
            color = self.pal.c(self.TANK_COLS[seat % 8], R.WHITE)
            if hp <= 0:
                color = self.pal.c(R.MAGENTA, R.WHITE)
            aims = v.get("aim") or []
            # the gun as last fired: aiming with the buttons costs no redraw
            ang = aims[seat][0] if seat < len(aims) and aims[seat] else 90
            import math
            bx, by = cx + round(7 * math.cos(math.radians(ang))), cy - 2 - round(6 * math.sin(math.radians(ang)))
            draw = [R.fill(cx - 6, cy + 2, 13, 2, R.BLACK if not self.mono else R.WHITE),     # the tracks
                    R.fill(cx - 5, cy - 1, 11, 3, color),                                       # the hull
                    R.disc(cx, cy - 2, 3, 2, color),                                            # the turret
                    R.line(cx, cy - 2, bx, by, color),                                          # the gun, as aimed
                    R.text(cx - 4, cy - 17, str(seat + 1), R.BLACK, color)]
            sc.add(("tank", seat, x, h, hp > 0, ang), draw,
                   [R.fill(cx - 8, cy - 17, 17, 21, self.sky)] + self.bands_under(cx - 8, 17, rows, x0, cw, band))

    def bands_under(self, x, wd, rows, x0, cw, band, below=None):
        """The ground of the bands under x .. x+wd, drawn again after an erase."""
        out = []
        w = len(rows)
        for i in range(0, w, band):
            bx = x0 + i * cw
            if bx + cw * band <= x or bx >= x + wd:
                continue
            hs = rows[i:i + band]
            h = sum(hs) // len(hs) if hs else 0
            if h > 0:
                top = self.height_y(h)
                out.append(R.fill(bx, top, cw * band, CF_GROUND - top + 1, self.ground))
        return out

    def seat_note(self, seat):
        v = self.v or {}
        tanks = v.get("tanks") or []
        if seat < len(tanks) and tanks[seat]:
            return " hp %d" % tanks[seat][1]
        return ""

    def my_aim(self):
        if self.aim is None:
            aims = (self.v or {}).get("aim") or []
            self.aim = list(aims[self.me]) if 0 <= self.me < len(aims) and aims[self.me] else [45, 60]
        return self.aim

    def status(self):
        a, b = super().status()
        v = self.v
        if v and v.get("rows") and not self.over and self.me >= 0:
            ang, pw = self.my_aim()
            b = "Aim %d deg, power %d. Wind %+d" % (ang, pw, v["wind"])
        return a, b

    def hint(self):
        return "Type 45 60: fire at 45 deg, power 60." if self.me >= 0 and not self.over else ""

    def buttons(self):
        out = []
        if self.me >= 0 and self.playing():
            out += [("a-", "Angle -5"), ("a+", "Angle +5"), ("p-", "Power -5"), ("p+", "Power +5"),
                    ("ml", "Move left"), ("mr", "Move right"), ("fire", "Fire")]
        return out + super().buttons()

    async def game_button(self, name):
        if not self.my_turn():
            self.s.say("It is not your turn.")
            return
        aim = self.my_aim()
        if name in ("a-", "a+"):
            aim[0] = max(0, min(180, aim[0] + (5 if name == "a+" else -5)))
        elif name in ("p-", "p+"):
            aim[1] = max(0, min(100, aim[1] + (5 if name == "p+" else -5)))
        elif name == "fire":
            await self.move("FIRE %d %d" % tuple(aim))
        elif name in ("ml", "mr"):
            await self.move("MOVE %s 999" % ("L" if name == "ml" else "R"))

    def typed_move(self, text):
        import re
        t = text.strip().lower()
        m = re.match(r"^(?:fire\s+)?(\d{1,3})\s+(\d{1,3})$", t)
        if m:
            self.aim = [int(m.group(1)), int(m.group(2))]
            return "FIRE %d %d" % tuple(self.aim)
        m = re.match(r"^(?:move\s+)?([lr])\s*(\d{0,3})$", t)
        if m:
            return "MOVE %s %s" % (m.group(1).upper(), m.group(2) or "999")
        return None
