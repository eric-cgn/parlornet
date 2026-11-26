"""The practice game: a lesson in Tic Tac Toe that teaches telnet's keys (docs/design.md, The practice game).

It is a screen of the telnet frontend, not a game: it has no plugin and no module in adapters/games/, so no
lobby, Games page or table list ever shows it. It runs only on the player's own screen. Every request a
game screen would send to the core is answered here from the lesson's own state, so nothing reaches the
parlor, no table is made and no move is recorded.

The screen is a real game screen (AnsiGame): the title bar, the board, the panel, the sentence line, the
command line, the keys line, the chat screen, help and dialogs all work as they do at a table. The lesson
is a list of steps. Each step puts one instruction on the sentence line, and the next step comes only when
the player does that thing. A move that the step doesn't ask for is refused with the same instruction,
starting with "Not yet." The practice partner's replies are scripted, so every player sees the same game,
and in step 10 the partner leaves the player's line open on purpose.

Session.practice() opens it, from the lobby's list, /practice, the first-sign-in dialog and help.
"""
import time

from parlord.commands import DIALOGS, time_text
from .telnet import (A_BORDER, A_DIM, A_HEAD, A_TEXT, A_YELLOW, COLS, ROWS, AnsiGame, Canvas, dialog_lines, rule,
                     title_bar)

NAME = "Tic Tac Toe practice"
PARTNER = "practice partner"
SAYS_NOTHING = "This is a practice game, so that command does nothing here."
ONLY_HERE = "Practice only. Nothing you do here reaches the parlor."

# The steps: (the instruction, what it teaches, the move or deed that ends it, the partner's reply).
# A deed is a square ("b2" placed with Enter or a click), "/c3" (a typed move), or a word below.
STEPS = [
    ("This practice game shows how every game works. Press Enter to begin.", "You learn the Enter key.",
     "begin", None),
    ("Press the arrows to move to the middle square.", "You learn to move the cursor.", "at b2", None),
    ("Press Enter to place your X.", "Enter plays, and the panel shows the last move.", "b2", "a1"),
    ("Your partner placed an O on a1. Move to c1 and press Enter.", "You learn whose turn it is.", "c1", None),
    ("Press U to take back that X. Lit keys on the bottom line are shortcuts.", "You learn a shortcut key.",
     "undo", None),
    ("Your X is gone. Press Enter on c1 to place it again.", "U took back your last step.", "c1", "a3"),
    ("Press / to open the command line.", "You learn the command line.", "slash", None),
    ("Type time and press Enter. The lit keys went dark because you are typing.", "You learn to run a command.",
     "time", None),
    ("Press / again, type anything, then press Esc to clear it.", "Esc cancels what you are doing.", "cleared",
     None),
    ("You can type a move too. Type /c3 and press Enter.", "You learn to type a move.", "/c3", "b1"),
    ("Press T to open the chat.", "You learn the chat screen.", "chat", None),
    ("Type hello and press Enter to say it.", "A line on the chat screen is said without /say.", "said", None),
    ("Press Esc to go back to the board.", "Esc leaves the chat screen.", "board", None),
    ("You can chat from the board too. Type /say good game and press Enter.", "You learn /say.", "say", None),
    ("Press ? to open help. Space turns the page, and Esc closes it.", "You learn where help is.", "help", None),
    ("Press R to see how a game asks before it ends something. Then press Esc to answer No.",
     "You learn dialogs, and that Esc in one is always safe.", "dialog", None),
    ("Move to c2 and press Enter to finish your line.", "You learn how a game ends.", "c2", None),
    ("You won the practice game. Press Esc to go back to the lobby.", "Esc with nothing to cancel goes back.",
     "lobby", None),
]
SQUARES = [c + r for r in "123" for c in "abc"]         # a1 b1 c1 a2 ... in reading order
LINES = [("a1", "b1", "c1"), ("a2", "b2", "c2"), ("a3", "b3", "c3"), ("a1", "a2", "a3"), ("b1", "b2", "b3"),
         ("c1", "c2", "c3"), ("a1", "b2", "c3"), ("c1", "b2", "a3")]
BX, BY = 6, 3           # the board's top left corner: three cells of 7 columns and 3 rows, with lines between


class Offline:
    """Stands in for the core while the practice game is open, so that a request sent by mistake fails
    loudly instead of reaching the parlor."""

    async def req(self, text):
        raise AssertionError("the practice game sent %r to the core" % text)


class Practice(AnsiGame):
    """The practice game's screen."""

    GAME = NAME
    HOTKEYS = "urt?"
    HELP_MOVING = ["The arrows move the cursor over the board. Press Enter to place",
                   "an X there. A click on a square does the same."]
    HELP_KEYS = ["U takes back your last X."]
    HELP_COMMANDS = ["Press / to type one. /b2 places an X on b2, and /time and /help",
                     "work here. Commands that need the parlor do nothing here."]
    HELP_REFERENCE = ["The board",
                      "The letters name the columns from the left, and the numbers name",
                      "the rows from the top. Three in a row, across, down or corner to",
                      "corner, wins.",
                      "",
                      "The practice game",
                      "Nothing you do here reaches the parlor. Your partner's moves are",
                      "the same for every player."]
    EVERY_TABLE = "At a real table, /say, /invite, /note, /time and /leave work too."

    def __init__(self, sess):
        super().__init__(sess, "practice", "practice", 0)
        self.core = Offline()
        self.names = [sess.name, PARTNER]
        self.board = {}                 # square -> "X" or "O"
        self.cur = (0, 0)               # column, row
        self.step = 0
        self.not_yet = False
        self.mine = []                  # the squares of your Xs, in the order placed
        self.started_at = time.time()
        self.v = {"rows": True, "turn": 0, "moves": [], "status": ""}
        self.log = ["The practice game started. You play X."]

    # ---- the lesson -----------------------------------------------------------
    def deed(self, what):
        """The player did something: the next step comes when it is what this step asks for. True if so."""
        if self.step >= len(STEPS) or STEPS[self.step][2] != what:
            return False
        reply = STEPS[self.step][3]
        self.step += 1
        self.not_yet = False
        if reply:
            self.board[reply] = "O"
            self.last = "Your partner placed an O on %s." % reply
            self.log.append(self.last)
        return True

    def refuse(self):
        """A move the step doesn't ask for: nothing happens, and the instruction comes again."""
        self.not_yet = True

    def hint(self):
        text = STEPS[min(self.step, len(STEPS) - 1)][0]
        if self.step == 1 and self.move_set():
            text = "Press the arrows or %s to move to the middle square." % self.move_set().upper()
        return ("Not yet. " if self.not_yet else "") + text, A_YELLOW

    def winner(self):
        return next((line for line in LINES if all(self.board.get(s) == "X" for s in line)), None)

    # ---- the state a real screen reads from the view -----------------------------
    def playing(self):
        return not self.winner() and not self.over

    def my_turn(self):
        return self.playing()

    def sq(self):
        return SQUARES[self.cur[1] * 3 + self.cur[0]]

    def turn_sentence(self):
        return "Your turn." if self.playing() else "Game over. You won." if self.winner() else "Game over."

    def clock_sentence(self):
        return "This game has no clock.", False

    def title_text(self):
        return NAME

    # ---- what a real screen asks the core, answered here --------------------------
    async def open_view(self):
        return True

    async def refresh(self):
        return True

    async def pull(self, since=None):
        return []

    async def notes(self):
        return []

    async def chat(self, text):
        self.log.append("<%s> %s" % (self.s.name, text))
        return None

    def time_sentence(self):
        """/time as the server says it, from the clock here: the time signed in and the time in UTC."""
        tim = "TIM %d %s" % (time.time() - self.s.since, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return " ".join(time_text([tim], "OK", width=COLS - 2))

    async def place(self, square, typed=False):
        """An X on square, when the step asks for it there."""
        if self.board.get(square):
            self.say("A mark is on %s already." % square)
            return self.refuse()
        want = STEPS[self.step][2] if self.step < len(STEPS) else ""
        if want != ("/" + square if typed else square):
            return self.refuse()
        self.board[square] = "X"
        self.mine.append(square)
        self.last = "You placed an X on %s." % square
        self.log.append(self.last)
        self.deed(want)
        if self.winner():
            self.log.append("You won the practice game.")
        return None

    # ---- keys --------------------------------------------------------------
    async def on_key(self, k):
        self.not_yet = False
        was_buf, was_chat = self.buf, self.chatting
        result = await super().on_key(k)
        if self.step == 6 and self.buf == "/":
            self.deed("slash")
        if was_buf not in ("", "/") and k == "ESC" and not self.buf:
            self.deed("cleared")
        if not was_chat and self.chatting:
            self.deed("chat")
        if was_chat and not self.chatting and k == "ESC":
            self.deed("board")
        if self.step == 1 and self.sq() == "b2":
            self.deed("at b2")
        return result               # Esc with nothing to cancel leaves the lesson at any step, as at a table

    def on_arrow(self, k):
        dx, dy = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}[k]
        self.cur = ((self.cur[0] + dx) % 3, (self.cur[1] + dy) % 3)

    async def on_enter(self):
        if self.step == 0:
            self.deed("begin")
            return None
        if not self.playing():
            return self.refuse()
        return await self.place(self.sq())

    async def on_click(self, value):
        if value[0] == "sq":
            self.cur = (value[1], value[2])
            return await self.on_enter()
        return None

    async def hotkey(self, k):
        if k == "u":
            if self.step != 4 or not self.mine:
                return self.refuse()
            square = self.mine.pop()
            del self.board[square]
            self.last = "You took back your X on %s." % square
            self.log.append(self.last)
            self.deed("undo")
            return None
        if k == "r":
            return await self.shared("resign")
        return await super().hotkey(k)

    async def help(self, page=1):
        await super().help(page)
        self.deed("help")

    def move_word(self, cmd, words=()):
        return cmd if cmd in SQUARES and len(words) == 1 else None

    async def run_command(self, line):
        if line.split()[:1] == ["practice"]:
            return self.tell(SAYS_NOTHING)
        return await super().run_command(line)

    async def shared(self, line):
        """What a table sends to the server is answered here: a move, /time, /say and /resign as in a game, and
        any other command says that it does nothing in practice."""
        p = line.split()
        cmd = p[0].lower()
        if self.move_word(cmd, p):
            return await self.place(cmd, typed=True)
        if cmd == "time":
            self.tell(self.time_sentence())
            self.deed("time")
        elif cmd == "say" and len(p) > 1:
            await self.chat(line.split(None, 1)[1])
            self.deed("say")
        elif cmd == "resign":
            words = DIALOGS["resign"] % "Your partner wins."
            if await self.ask_yes("Resign", dialog_lines(words), "resign"):
                self.over = "You resigned the practice game"
                self.step = len(STEPS) - 1
            elif self.step == 15:
                self.deed("dialog")
        else:
            self.tell(SAYS_NOTHING)
        return None

    async def chat_key(self, k):
        said = k == "ENTER" and self.chat_buf.strip() and not self.chat_buf.startswith("/")
        result = await super().chat_key(k)
        if said and self.step == 11:
            self.log.append("<%s> Hello! This is the chat. Press Esc to go back to the board." % PARTNER)
            self.deed("said")
        return result

    def chat_head(self):
        return "Chat at the practice table. Your partner here is part of the lesson."

    # ---- drawing -----------------------------------------------------------
    def compose(self):
        cv = Canvas()
        title_bar(cv, self.g, NAME)
        self.draw_board(cv)
        self.draw_panel(cv)
        cv.put(1, 20, ONLY_HERE, A_DIM)
        text, attr = self.hint()            # the lesson's instruction stays, in command mode too
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        if self.buf:
            self.input_line(cv)
        elif self.msg:
            cv.put(1, ROWS - 2, self.msg[:COLS - 2], self.msg_attr)
        undo = [("u", "undo", "undo")] if self.step == 4 else []
        self.keys_line(cv, self.keys("move", "place", undo) if self.playing() else self.keys())
        return cv

    def draw_board(self, cv):
        g = self.g
        showcur = self.playing() and self.lit()
        for c in range(3):
            cv.put(BX + 4 + 8 * c, BY - 1, "abc"[c], A_HEAD if showcur and c == self.cur[0] else A_BORDER)
        for r in range(4):
            y = BY + 4 * r
            if r in (0, 3):
                left, mid, right = (g["tl"], g["h"], g["tr"]) if r == 0 else (g["bl"], g["h"], g["br"])
            else:
                left, mid, right = g["lj"], g["x"], g["rj"]
            cv.put(BX, y, left + (g["h"] * 7 + mid) * 2 + g["h"] * 7 + right, A_BORDER)
        for r in range(3):
            cv.put(BX - 3, BY + 2 + 4 * r, str(r + 1), A_HEAD if showcur and r == self.cur[1] else A_BORDER)
            for y in range(BY + 1 + 4 * r, BY + 4 + 4 * r):
                cv.put(BX, y, (g["v"] + " " * 7) * 3 + g["v"], A_BORDER)
            for c in range(3):
                x0, y0 = BX + 1 + 8 * c, BY + 1 + 4 * r
                attr = "0;30;46" if showcur and (c, r) == self.cur else A_TEXT
                for dy in range(3):
                    cv.put(x0, y0 + dy, " " * 7, attr)
                mark = self.board.get("abc"[c] + str(r + 1), "")
                cv.put(x0 + 3, y0 + 1, mark or " ", "0;1;30;46" if attr != A_TEXT else "0;1;37")
                cv.hot_area(x0, y0, 7, 3, ("sq", c, r))

    def draw_panel(self, cv):
        px = 40

        def lesson(cv, y):
            rule(cv, self.g, px, y, COLS - 1 - px, A_BORDER, "Lesson", tattr=A_HEAD)
            n = min(self.step, len(STEPS) - 1)
            cv.put(px + 2, y + 1, "Step %d of %d." % (n + 1, len(STEPS)), A_TEXT)
            cv.put(px + 2, y + 2, STEPS[n][1][:COLS - 5 - px], A_DIM)
            return y + 3
        self.draw_panel_parts(cv, px, 1, COLS - 1 - px, [(0, ("X", A_TEXT), ""), (1, ("O", A_TEXT), "")],
                              extra=lesson)

    def cursor_at(self):
        if self.playing():
            return BX + 4 + 8 * self.cur[0], BY + 2 + 4 * self.cur[1]
        return None


async def run_practice(sess):
    """Open the practice game on this session's screen; back to the lobby when the player leaves it."""
    screen = Practice(sess)
    sess.painter.invalidate()
    await screen.run()
    sess.painter.invalidate()
