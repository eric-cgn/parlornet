"""Every game over every connection type, through a table's whole life: open a table (two players, and
three where the game takes more), the others join from the lobby list, a stranger is refused a seat at the
running table and watches it instead, every player makes at least two real moves (chosen from the legal-move
list or with CHK, so no seed matters), the waiting player sees the other's move arrive, a player drops the
connection, signs in again and goes back to their seat, the watcher leaves, a player resigns, and every
player sees the end and is back at the menu. Connection types: telnet full screen in each glyph mode (pc,
utf8, ansi), telnet full screen with letter movement keys (IJKL: the menu's O picks them, the letters move
the selection, every typed line starts with :), telnet plain, the web pages and the native protocol; IOS
(Reversi), FIBS and Outland (Backgammon) are in test_matrix_legacy.py.

Each class runs its own servers once; the tables of one class are played at the same time."""
import importlib.util
import os
import re
import socket
import sys
import threading
import time
import traceback
import types
import unittest
from itertools import permutations
from urllib.parse import parse_qs, urlencode, urlsplit

import procs
from test_adapters import Browser, Client, spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import games as G  # noqa: E402
from adapters.games import gridwords as GW  # noqa: E402
from adapters import telnet as T  # noqa: E402
from adapters import web as W  # noqa: E402
from adapters.common import group_games, parse_games, parse_tables  # noqa: E402
from speed import slow  # noqa: E402


def _plugin(name):
    spec = importlib.util.spec_from_file_location("mx_" + name, os.path.join(ROOT, "plugins", name, "plugin.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cf = _plugin("cannonfire")
WAIT = 10
TABLES_AT_ONCE = 8


# ---- the core's own view, for choosing moves and checking what happened ----------------------------

class Conn:
    """A native-protocol connection (sign-in none: the name alone)."""

    def __init__(self, port, name):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.s.sendall(b"PARLORNET/1\r\n")       # the opening first, then the greeting
        self.f = self.s.makefile("rb")
        self.f.readline()
        r = self.req("HELO %s" % name)
        assert r[-1].startswith("OK"), r

    def req(self, text):
        self.s.sendall(text.encode() + b"\r\n")
        out = []
        while True:
            l = self.f.readline().decode("latin-1").rstrip("\r\n")
            if not l:
                raise EOFError("connection closed after %r" % text)
            out.append(l)
            if l.startswith(("OK", "ERR", "ASK")):
                return out

    def close(self):
        for x in (self.f, self.s):
            try:
                x.close()
            except OSError:
                pass


def table(conn, tid):
    return next((t for t in parse_tables(conn.req("LST")) if t["id"] == tid), None)


def until(pred, what, timeout=WAIT, step=0.03):
    end = time.time() + timeout
    while True:
        r = pred()
        if r:
            return r
        if time.time() > end:
            raise AssertionError("timed out waiting for %s" % what)
        time.sleep(step)


# ---- move choice ---------------------------------------------------------------------------------

_WORDS = {}


def word_list():
    if not _WORDS:
        with open(os.path.join(ROOT, "plugins", "gridwords", "words.txt")) as f:
            for w in f:
                w = w.strip().upper()
                if 2 <= len(w) <= 5 and w.isalpha():
                    _WORDS.setdefault(len(w), set()).add(w)
    return _WORDS


def gridwords_play(p, tid, v):
    """A word the server takes (CHK), from the rack across the center or through a tile on the board."""
    words = word_list()
    rack = [c for c in (v["rack"] or "") if c.isalpha()]
    cands = []
    if not v["tiles"]:
        cx, cy = v["ctr"]
        for n in (2, 3, 4):
            for perm in permutations(rack, n):
                w = "".join(perm)
                if w in words.get(n, ()):
                    cands.append("PLAY %d %d H %s" % (cx, cy, w))
    else:
        for (x, y), c in sorted(v["tiles"].items()):
            for n in (2, 3):
                for perm in permutations(rack, n - 1):
                    for i in range(n):
                        w = "".join(perm[:i]) + c.upper() + "".join(perm[i:])
                        if w in words.get(n, ()):
                            cands += ["PLAY %d %d V %s" % (x, y - i, w), "PLAY %d %d H %s" % (x - i, y, w)]
    seen = set()
    for mv in cands:
        if mv in seen:
            continue
        seen.add(mv)
        f = mv.split()
        if not (1 <= int(f[1]) <= 15 and 1 <= int(f[2]) <= 15):
            continue
        if p.oracle.req("CHK %s %s" % (tid, mv))[-1].startswith("OK"):
            return mv
        if len(seen) > 60:
            break
    return "EXCH %s" % rack[0] if v["bag"] >= 7 and rack else "PASS"


SPIRAL = sorted(((x, y) for x in range(1, 16) for y in range(1, 16)), key=lambda q: (abs(q[0] - 8) + abs(q[1] - 8), q))


def next_action(key, p, tid, lines, v):
    """-> (token, ends the turn) for the player to move, "END" ending a staged turn; None: nothing to do."""
    game = key.split("/")[0]
    raw = {l.split()[0]: l.split()[1:] for l in lines if l.split()}
    if game in ("reversi", "mancala", "dotsandboxes"):
        return v["moves"][0], True
    if game in ("backgammon", "checkers"):
        if game == "backgammon" and v["phase"] == "roll":
            return "roll", False
        return (v["moves"][0], False) if v["moves"] else ("END", True)
    if game == "fiveinarow":
        for x, y in SPIRAL:
            pt = "%s%d" % (chr(96 + x), y)
            if p.oracle.req("CHK %s %s" % (tid, pt))[-1].startswith("OK"):
                return pt, True
        raise AssertionError("no free point")
    if game == "navalcombat":
        if raw.get("Q", [""])[0] == "place":
            if "N" in raw:
                return "random", False
            if raw.get("RD", ["0", "0"])[p.seat] == "0":
                return "ready", False
            return None
        return v["moves"][0], True
    if game == "cannonfire":
        if v["left"][p.seat] == 4 and not v["moved"]:     # each tank's first turn: a move first (the turn stays)
            for d in (-1, 1):
                tok = p.module.full_move(v, d)
                if p.oracle.req("CHK %s %s" % (tid, tok))[-1].startswith("OK"):
                    return tok, False
        st = {"w": 0, "h": [], "tanks": [], "wind": 0}
        for l in lines:
            f = l.split()
            if f[0] == "ARN":
                st["w"] = int(f[1])
            elif f[0] == "Z":
                st["h"] += [ord(c) - 0x30 for c in f[2]]
            elif f[0] == "K":
                st["tanks"].append([int(f[2]), int(f[3])])
            elif f[0] == "N":
                st["wind"] = int(f[1])
        best = None                 # a shot that leaves the arena or bursts well away from every tank
        for pw in range(100, 0, -5):
            for ang in (5, 175, 20, 160, 35, 145, 50, 130, 65, 115):
                _, impact, _ = cf.fly(st, p.seat, ang, pw)
                gap = 99 if impact is None else min(abs(impact[0] - x) for x, _ in st["tanks"])
                if best is None or gap > best[0]:
                    best = (gap, ang, pw)
                if gap >= 9:
                    return "FIRE %d %d" % (ang, pw), True
        return "FIRE %d %d" % best[1:], True
    if game == "gridwords":
        return gridwords_play(p, tid, v), True
    if game == "rummytile":
        if int(raw.get("TP", ["0"])[0]) == 0:
            rack = [t for l in lines if l.startswith("H ") for t in l.split()[1:]]
            for t in rack[:6]:
                if p.oracle.req("CHK %s %s-n" % (tid, t))[-1].startswith("OK"):
                    return "%s-n" % t, False
        return "END", True
    raise AssertionError("no move choice for %s" % key)


def gridwords_typed(tok, tiles):
    """A Grid Words play (the whole word, PLAY x y H|V WORD) -> (start square, direction, letters) as a player
    types it: from the first empty square, only the letters from the rack (the tiles on the board join)."""
    f = tok.split()
    x, y, letters = GW.typed_from(tiles, int(f[1]), int(f[2]), f[3], f[4])
    return "%s%d" % (chr(96 + x), y), f[3], letters


def typed(key, tok, tiles=None):
    """A core token -> what a telnet player types for it (tiles: Grid Words' board, {(x, y): letter})."""
    game = key.split("/")[0]
    if tok == "END":
        return "end"
    if game == "gridwords":
        f = tok.split()
        if f[0] == "PLAY":
            sq, d, letters = gridwords_typed(tok, tiles or {})
            return "%s %s %s" % (sq, "across" if d == "H" else "down", letters)
        return "pass" if f[0] == "PASS" else "exchange %s" % f[1]
    if game == "cannonfire":
        f = tok.split()
        if f[0] == "MOVE":
            return "drive %s %s" % ("left" if f[1] == "L" else "right", f[2])
        return tok.lower()
    return tok


def describe_for(fe_kind, module, text, names):
    """What a frontend shows for an event (None: nothing)."""
    try:
        if fe_kind == "web":
            return W.describe(text, names, module.KEY)
        return module.AnsiScreen.describe(types.SimpleNamespace(names=names, me=-1), text)
    except Exception:
        return None


def mark_of(desc):
    """The start of an event's text that sits on one screen row: whole words, at most 14 characters."""
    out = ""
    for w in desc.split():
        if out and len(out) + 1 + len(w) > 14:
            break
        out = (out + " " + w).strip()
    return out


def still(lines):
    """A view without the lines that change by themselves (the clock)."""
    return [l for l in lines if not l.startswith(("CLK ", "S "))]


class Player:
    def __init__(self, name, oracle, module):
        self.name, self.oracle, self.module = name, oracle, module
        self.seat = -1

    def view(self, tid):
        lines = self.oracle.req("VUE %s GMT" % tid)[:-1]
        return lines, self.module.parse_view(lines)


# ---- telnet ----------------------------------------------------------------------------------------

UP, DOWN, HOME, ESC = "\x1b[A", "\x1b[B", "\x1b[H", "\x1b"
CHOICE = {"pc": "1", "utf8": "2", "ansi": "3", "plain": "4"}
ENC = {"pc": "cp437", "utf8": "utf-8", "ansi": "latin-1", "plain": "latin-1"}


class TelnetFE:
    kind = "telnet"
    login_lock = threading.Lock()        # the adapter holds at most four sign-ins from one address

    def __init__(self, tc, mode, moves=None):
        self.tc, self.mode, self.full = tc, mode, mode != "plain"
        self.moves = moves                  # letter movement keys (a key of T.MOVE_SETS), or the arrows
        self.down = T.MOVE_SETS[moves][1][1] if moves else DOWN
        self.cmd = "/"                      # every typed command starts with the slash

    # -- the connection
    def connect(self, p):
        with self.login_lock:
            c = Client(self.tc.tel_port, enc=ENC[self.mode], ttype="VT100", display=CHOICE[self.mode])
            c.s.settimeout(0.05)
            self.raw_until(c, "Login")                  # the name has an account (the oracle's HELO made it)
            c.send(p.name + "\r\n")
            self.raw_until(c, "Press Enter")
            c.send("\r\n")
        if self.full:                                   # the first sign-in offers the tutorial once
            end = time.time() + WAIT
            while "Your tables" not in c.vt.text() and "short tutorial on how" not in c.vt.text():
                if c.closed or time.time() > end:
                    raise AssertionError("no lobby:\n%s" % self.dump(c))
                c.pump()
            if "short tutorial on how" in c.vt.text():
                c.send("n")
        self.see(c, "Your tables")
        if self.moves:                                  # /keys: the movement keys (kept for the player)
            c.send("/keys\r")
            self.see(c, "Letters that move the cursor")
            c.send(str(T.MOVE_ORDER.index(self.moves) + 1))
            self.see(c, "You move the cursor with %s now" % self.moves.upper())
        p.c = c

    def raw_until(self, c, text, timeout=WAIT):
        end = time.time() + timeout
        while True:
            i = c.raw.find(text.encode("latin-1"), c.mark)
            if i >= 0:
                c.mark = i + len(text)
                return
            if c.closed:
                raise AssertionError("telnet connection closed waiting for %r:\n%s" % (text, self.dump(c)))
            if time.time() > end:
                raise AssertionError("%r not seen:\n%s" % (text, self.dump(c)))
            c.pump()

    def dump(self, c):
        return c.vt.text() if self.full else c.raw[-1500:].decode("latin-1")

    def see(self, c, text, timeout=WAIT):
        """Wait for text: on the screen (full screen), or in what arrived since the last match (plain)."""
        if not self.full:
            return self.raw_until(c, text, timeout)
        end = time.time() + timeout
        while text not in c.vt.text():
            if c.closed:
                raise AssertionError("telnet connection closed waiting for %r:\n%s" % (text, self.dump(c)))
            if time.time() > end:
                raise AssertionError("%r not on screen:\n%s" % (text, self.dump(c)))
            c.pump()

    def settle(self, c, quiet=0.1):
        n = -1
        end = time.time() + 3
        while n != len(c.raw) and time.time() < end:
            n = len(c.raw)
            t = time.time() + quiet
            while time.time() < t:
                c.pump()

    def hint(self, c):
        return c.vt.lines()[21]

    def select(self, c, *needles):
        """The full-screen lobby: reload, then the arrow keys down the rows until the sentence line says it."""
        c.send("/list\r")
        self.settle(c)
        c.send(HOME)
        self.settle(c)
        for _ in range(40):
            if all(n in self.hint(c) for n in needles):
                return
            c.send(self.down)
            self.settle(c, 0.04)
        raise AssertionError("no menu row %r:\n%s" % (needles, self.dump(c)))

    def mark(self, c):
        c.mark = len(c.raw)

    def at_table(self, p, tid):
        """p is on the table's screen, still connected."""
        c = p.c
        if not self.full:
            end = time.time() + WAIT
            while not re.search(("table %s[.,]" % tid).encode(), c.raw[-4000:]):
                if c.closed or time.time() > end:
                    raise AssertionError("not at table %s%s:\n%s" % (tid, " (connection closed)" if c.closed
                                                                      else "", self.dump(c)))
                c.pump()
            return
        end = time.time() + WAIT
        while "table %s" % tid not in c.vt.lines()[0]:
            if c.closed or time.time() > end:
                raise AssertionError("not at table %s%s:\n%s" % (tid, " (connection closed)" if c.closed else "",
                                                                  self.dump(c)))
            c.pump()

    # -- a table's life
    def open_table(self, p, g, n, typed_form):
        c = p.c
        lo, hi, rulesets = g["min"], g["max"], g["rulesets"]
        word = g["key"].split("/")[0]
        if typed_form and n == lo:              # the shared /open GAME, run on the server: its first rules
            if not self.full:
                self.mark(c)
            c.send(self.cmd + "open %s\r%s" % (word, "" if self.full else "\n"))
        elif self.full:
            c.send("n")                                 # N: the game, the rules, the players, the time, whom
            self.see(c, "Which game do you want to play?")
            c.send(self.down * list(k for k in T.RENDERERS).index(g["key"]))
            c.send("\r")
            if len(rulesets) > 1:
                self.see(c, "Which rules do you want?")
                c.send("\r")
            if hi > lo:
                self.see(c, "How many players?")
                c.send(self.down * (n - lo))
                c.send("\r")
            self.see(c, "How long is each turn?")
            c.send("\r")
            self.see(c, "Whom do you want to play?")
            c.send(self.down + "\r")                    # anyone who joins
        else:                                   # /open alone asks the questions
            self.mark(c)
            c.send("open\r\n")
            self.raw_until(c, "Which game?")
            c.send(word + "\r\n")
            if len(rulesets) > 1:
                self.raw_until(c, "Which rules?")
                c.send("\r\n")
            if hi > lo:
                self.raw_until(c, "How many players?")
                c.send("%d\r\n" % n)
            self.raw_until(c, "How long is each turn?")
            c.send("\r\n")

    def join(self, p, tid):
        c = p.c
        if self.full:
            self.select(c, "at table %s." % tid, "waits")
            c.send("\r")
        else:
            self.mark(c)
            c.send("join %s\r\n" % tid)

    def offers(self, p, tid, seated):
        """What the menu offers for a running table: resume for a seated player, watch for anyone else."""
        c = p.c
        if self.full:
            self.select(c, "table %s." % tid)
            want = "your game" if seated else "play at table %s." % tid
            self.tc.assertIn(want.lower(), self.hint(c).lower())
        else:
            self.settle(c)                      # what the menu printed before is in
            self.mark(c)
            start = c.mark
            c.send("list\r\n")
            self.raw_until(c, "/help for the commands.")
            out = c.raw[start:c.mark].decode("latin-1")
            rows = [l for l in out.split("\n") if re.match(r"\s+%s\s" % tid, l)]
            if not rows and not seated:         # the plain list shows the first 12 other tables only
                self.tc.assertEqual(len(re.findall(r"\n\s+\d+\s.*/(join|watch) \d+", out.split("Other tables:")[1])),
                                    12, out)
                return
            row = rows[-1]
            if seated:
                self.tc.assertRegex(row, r"(your|their) move")
            else:
                self.tc.assertIn("/watch %s" % tid, row)

    def refused_join(self, p, tid):
        """A stranger asks for a seat at the running table: told why, still connected, at the menu."""
        c = p.c
        self.mark(c)
        c.send(self.cmd + "join %s\r\n" % tid)
        self.see(c, "Table %s has no open seat for you." % tid)     # the server's words
        self.tc.assertFalse(c.closed)

    def watch(self, p, tid):
        c = p.c
        if self.full:
            self.select(c, "play at table %s." % tid)
            c.send("\r")
            self.see(c, "You are watching")             # the sentence line says so on every game's screen
        else:
            self.mark(c)
            c.send("watch %s\r\n" % tid)
            self.raw_until(c, "You are watching")

    def unwatch(self, p):
        self.back_to_menu(p)

    def back_to_menu(self, p):
        c = p.c
        self.mark(c)
        if self.full:
            c.send(ESC)
            self.see(c, "Lobby")
            self.see(c, "Your tables")
        else:
            c.send("/leave\r\n")
            self.raw_until(c, "Your tables")

    def act(self, p, key, tid, tok):
        c = p.c
        self.mark(c)
        c.send(self.cmd + "redraw\r")       # the screen catches up first, as a player's would
        tiles = p.view(tid)[1]["tiles"] if key.startswith("gridwords/") else None
        c.send(self.cmd + typed(key, tok, tiles) + "\r")
        if tok == "PASS":                   # passing asks first
            c.send("y" if self.full else "y\r")
        elif tok == "END" and key.startswith("rummytile/"):     # ending a turn that won't count asks first
            self.settle(c)
            if "End your turn?" in (c.vt.text() if self.full else c.raw[c.mark:].decode("latin-1")):
                c.send("y" if self.full else "y\r")

    def snapshot(self, p):
        self.settle(p.c, 0.02)
        return p.c.vt.text() if self.full else len(p.c.raw)

    def arrives(self, p, text, snap):
        """text shows up on p's screen without p typing anything."""
        c = p.c
        end = time.time() + WAIT
        while True:
            if self.full:
                if text in c.vt.text():
                    return
            elif text.encode("latin-1") in c.raw[snap:]:
                return
            if c.closed or time.time() > end:
                raise AssertionError("%r did not arrive:\n%s" % (text, self.dump(c)))
            c.pump()

    def drop(self, p):
        p.c.close()

    def resume(self, p, tid):
        self.connect(p)
        c = p.c
        self.offers(p, tid, seated=True)
        if self.full:
            c.send("\r")
        else:
            self.mark(c)
            c.send("join %s\r\n" % tid)
        self.at_table(p, tid)

    def resign(self, p):
        c = p.c
        self.mark(c)
        c.send(self.cmd + "resign\r")
        if self.full:
            self.see(c, "Resign this game?")
            c.send("y")
        else:
            self.raw_until(c, "Resign this game?")
            c.send("y\r\n")

    def after_end(self, p, end_text):
        """The end on the board, then back at the menu, still connected."""
        self.arrives(p, end_text, 0)
        self.back_to_menu(p)
        self.tc.assertFalse(p.c.closed)
        if self.full:                       # plain mode scrolls by design; the title page's clear does not change that
            self.tc.assertEqual(p.c.vt.errors, [])

    def close(self, p):
        try:
            p.c.close()
        except Exception:
            pass


# ---- web ---------------------------------------------------------------------------------------------

def links(body):
    return [h.replace("&amp;", "&") for h in re.findall(r'href="([^"]+)"', body)]


def find_link(body, path, **want):
    for h in links(body):
        u = urlsplit(h)
        if u.path != path:
            continue
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if all(q.get(k) == v for k, v in want.items()):
            return h
    return None


def lobby_row(body, tid):
    body = re.sub(r"</?font[^>]*>", "", body)        # the site's faces and sizes, in every cell
    return next((r for r in re.findall(r"<tr[^>]*><td[^>]*>(\d+)</td>(.*?)</tr>", body) if r[0] == tid),
                (tid, ""))[1]


class WebFE:
    kind = "web"

    def __init__(self, tc):
        self.tc = tc

    def connect(self, p):
        b = Browser(self.tc.web_port)
        st, _, _, body = b.login(p.name)
        self.tc.assertEqual(st, 200, body[-500:])
        self.tc.assertIn("ParlorNet lobby", body)
        p.b = b

    def ok(self, p, st, body, what):
        self.tc.assertIn(st, (200, 302), "%s: %s" % (what, body[-800:]))
        self.tc.assertNotIn("<title>ParlorNet: not done</title>", body, "%s: %s" % (what, body[-800:]))   # error_page

    def go(self, p, href, what):
        st, loc, _, body = p.b.go(href)
        while st == 302:
            st, loc, _, body = p.b.go(loc)
        self.ok(p, st, body, what)
        return body

    def lobby(self, p):
        return self.go(p, "/lobby.htm", "lobby")

    def open_table(self, p, g, n, typed_form):
        body = self.lobby(p)
        form = next(f for f in body.split("<li><form")[1:] if 'name="g" value="%s"' % g["key"] in f)
        q = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form))
        if len(g["rulesets"]) > 1:
            q["ru"] = re.search(r'<select name="ru"><option value="([^"]+)" selected', form).group(1)
        else:
            self.tc.assertNotIn('name="ru"', form)
        if g["max"] > g["min"]:
            self.tc.assertIn('<option value="%d">%d' % (n, n) if n != g["min"] else
                             '<option value="%d" selected>%d' % (n, n), form)
            q["np"] = str(n)
        else:
            self.tc.assertNotIn('name="np"', form)
        q["turn"], q["vis"] = "1d", "public"
        self.go(p, "/new.htm?" + urlencode(q), "new table")

    def join(self, p, tid):
        body = self.lobby(p)
        h = find_link(body, "/join.htm", t=tid)
        self.tc.assertIsNotNone(h, "no Join link for table %s: %s" % (tid, lobby_row(body, tid)))
        self.go(p, h, "join")

    def offers(self, p, tid, seated):
        row = lobby_row(self.lobby(p), tid)
        self.tc.assertNotIn("Join", row)
        self.tc.assertIn("Your game: go" if seated else "Watch", row)

    def refused_join(self, p, tid):
        st, _, _, body = p.b.get("/join.htm?" + urlencode({"t": tid, "x": self.x(p)}))
        self.tc.assertEqual(st, 200)
        self.tc.assertIn("not open", body)                                  # says why
        self.tc.assertIsNotNone(find_link(body, "/lobby.htm"), body)        # and the way back

    def x(self, p):
        return parse_qs(urlsplit(p.b.link(r"[?&]x=")).query)["x"][0]

    def watch(self, p, tid):
        body = self.lobby(p)
        h = find_link(body, "/table.htm", t=tid, w="1")
        self.tc.assertIsNotNone(h, lobby_row(body, tid))
        body = self.go(p, h, "watch")
        self.tc.assertIn("Stop watching", body)

    def unwatch(self, p):
        h = find_link(p.b.body, "/leave.htm")
        body = self.go(p, h, "stop watching")
        self.tc.assertIn("ParlorNet lobby", body)

    def at_table(self, p, tid):
        self.tc.assertIn("table %s</title>" % tid, p.b.body)

    def page(self, p, tid):
        return self.go(p, "/table.htm?t=%s" % tid, "table page")

    @staticmethod
    def areas(body):
        """The board image's map: [(center x, center y, href)]."""
        out = []
        for shape, coords, href in re.findall(r'<area shape="(\w+)" coords="([-\d,]+)" href="([^"]+)"', body):
            c = [int(n) for n in coords.split(",")]
            if shape == "rect":
                x, y = (c[0] + c[2]) // 2, (c[1] + c[3]) // 2
            elif shape == "circle":
                x, y = c[0], c[1]
            else:
                x, y = sum(c[0::2]) // len(c[0::2]), sum(c[1::2]) // len(c[1::2])
            out.append((x, y, href.replace("&amp;", "&")))
        return out

    def area(self, body, path, **want):
        for x, y, h in self.areas(body):
            u = urlsplit(h)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == path and all(q.get(k) == v for k, v in want.items()):
                return x, y, h
        return None

    def click(self, p, body, hit, what):
        """Every other click by ISMAP (/click.htm?x,y, as a browser without client-side maps sends it),
        the others by the area's own link."""
        p.clicks = getattr(p, "clicks", 0) + 1
        self.tc.board_clicks += 1
        if p.clicks % 2:
            return self.go(p, hit[2], what + " (area)")
        ismap = find_link(body, "/click.htm")
        self.tc.assertIsNotNone(ismap, "a board map without its ISMAP link")
        return self.go(p, ismap + "?%d,%d" % hit[:2], what + " (ISMAP)")

    def act(self, p, key, tid, tok):
        body = self.page(p, tid)
        game, x = key.split("/")[0], self.x(p)
        if tok == "END":
            h = find_link(body, "/end.htm", t=tid)
            self.tc.assertIsNotNone(h, "no End turn link")
            body = self.go(p, h, "end the turn")
        elif game == "cannonfire" and tok.startswith("MOVE "):         # a click on the ground within reach
            _, v = p.view(tid)
            m = p.module
            x0 = v["tanks"][p.seat][0]
            ends = [c for c in m.reach(v, p.seat) if (c - x0) * (1 if tok.split()[1] == "R" else -1) > 0]
            self.tc.assertTrue(ends, "no ground within reach for %s" % tok)
            col = max(ends, key=lambda c: abs(c - x0))
            k, _, h = m.geometry(v)
            pt = (col * k + k // 2, h - 1 - v["rows"][col] * k + 2)
            self.tc.assertEqual(m.board_click(v, p.seat, "", *pt)[0], "/move.htm")
            self.tc.assertIsNotNone(find_link(body, "/move.htm", mv=tok), "no Move link for %s" % tok)
            self.tc.board_clicks += 1
            ismap = find_link(body, "/click.htm")
            self.tc.assertIsNotNone(ismap, "the landscape without its ISMAP link")
            body = self.go(p, ismap + "?%d,%d" % pt, "drive to column %d (ISMAP)" % col)
        elif self.area(body, "/move.htm", mv=tok):                      # a square, a point, a pit, a line
            body = self.click(p, body, self.area(body, "/move.htm", mv=tok), "move %s" % tok)
        elif re.match(r"^(bar|\d+)[-x]", tok) and self.area(body, "/table.htm", sel=re.split("[-x]", tok)[0]):
            frm = re.split("[-x]", tok)[0]                              # pick the piece up, then put it down
            body = self.click(p, body, self.area(body, "/table.htm", sel=frm), "pick %s" % frm)
            to = self.area(body, "/move.htm", mv=tok)
            self.tc.assertIsNotNone(to, "no target %s on the board after picking %s" % (tok, frm))
            body = self.click(p, body, to, "move %s" % tok)
        elif find_link(body, "/move.htm", mv=tok):                      # roll, random, ready, pass ...
            body = self.go(p, find_link(body, "/move.htm", mv=tok), "move %s" % tok)
        elif game == "gridwords" and tok.startswith("PLAY "):          # the play form: the rack's letters
            sq, d, letters = gridwords_typed(tok, p.view(tid)[1]["tiles"])
            q = {"t": tid, "sq": sq, "dir": d, "letters": letters, "x": x}
            body = self.go(p, "/move.htm?" + urlencode(q), "play %s" % tok)
        elif game == "cannonfire":                                      # the shot form
            f = tok.split()
            body = self.go(p, "/move.htm?" + urlencode({"t": tid, "angle": f[1], "power": f[2], "x": x}),
                           "fire %s" % tok)
        else:                                                           # what the page's forms send
            body = self.go(p, "/move.htm?" + urlencode({"t": tid, "mv": tok, "x": x}), "move %s" % tok)
        self.tc.assertIn("Refresh", body)

    def snapshot(self, p):
        return None

    def arrives(self, p, text, snap):
        body = self.page(p, p.tid)                  # a refresh of the page
        self.tc.assertIn(W.esc(text), body)

    def drop(self, p):
        p.b = None

    def resume(self, p, tid):
        self.connect(p)
        body = self.lobby(p)
        self.offers(p, tid, seated=True)
        h = find_link(body, "/table.htm", t=tid)
        body = self.go(p, h, "back to the game")
        self.tc.assertIn("Resign", body)

    def resign(self, p):
        body = self.page(p, p.tid)
        body = self.go(p, find_link(body, "/resign.htm", t=p.tid), "resign")
        self.go(p, find_link(body, "/resign.htm", t=p.tid, sure="1"), "resign: yes")

    def after_end(self, p, end_text):
        body = self.page(p, p.tid)
        self.tc.assertIn(W.esc(end_text), body)
        h = find_link(body, "/lobby.htm")
        self.tc.assertIsNotNone(h)
        self.tc.assertIsNone(find_link(body, "/resign.htm"))
        self.tc.assertIn("ParlorNet lobby", self.go(p, h, "lobby after the end"))

    def close(self, p):
        pass


# ---- native ------------------------------------------------------------------------------------------

class NativeFE:
    kind = "native"

    def __init__(self, tc):
        self.tc = tc

    def connect(self, p):
        p.n = Conn(self.tc.core_port, p.name)

    def req(self, p, text, ok=True):
        r = p.n.req(text)
        if ok:
            self.tc.assertTrue(r[-1].startswith("OK"), "%s: %s" % (text, r))
        return r

    def open_table(self, p, g, n, typed_form):
        r = self.req(p, "NEW %s %s turn=1d%s" % (g["key"], g["rules"], " players=%d" % n if n > 2 else ""))
        self.req(p, "JON %s" % r[-1].split()[1])

    def join(self, p, tid):
        t = table(p.n, tid)
        self.tc.assertEqual(t["status"], "open")
        self.req(p, "JON %s" % tid)

    def offers(self, p, tid, seated):
        t = table(p.n, tid)
        self.tc.assertEqual((t["status"], p.name in t["names"]), ("playing", seated))

    def refused_join(self, p, tid):
        r = self.req(p, "JON %s" % tid, ok=False)
        self.tc.assertRegex(r[-1], r"^ERR [A-Z]+ \w", r)
        self.req(p, "LST")

    def watch(self, p, tid):
        self.offers(p, tid, seated=False)
        self.req(p, "SEE %s" % tid)
        self.req(p, "VUE %s" % tid)

    def unwatch(self, p):
        self.req(p, "LVE %s" % p.tid)

    def at_table(self, p, tid):
        self.req(p, "VUE %s" % tid)

    def act(self, p, key, tid, tok):
        self.req(p, "END %s" % tid if tok == "END" else "MOV %s %s" % (tid, tok))

    def snapshot(self, p):
        return None

    def arrives(self, p, text, snap):
        pass                                    # the event list is the native view; checked by the driver

    def drop(self, p):
        p.n.close()

    def resume(self, p, tid):
        self.connect(p)
        self.offers(p, tid, seated=True)
        self.req(p, "VUE %s" % tid)

    def resign(self, p):
        self.req(p, "RES %s" % p.tid)

    def after_end(self, p, end_text):
        r = self.req(p, "VUE %s T" % p.tid)
        self.tc.assertTrue(any(l.startswith("S Game over") for l in r), r)

    def close(self, p):
        p.n.close()


# ---- the driver -----------------------------------------------------------------------------------------

class Matrix:
    """A table's life for every game over one connection type; the test classes below pick the type."""
    FE = None
    PREFIX = "x"

    @classmethod
    def setUpClass(cls):
        cls.core_port, cls.fe_port, cls.web_port, cls.tel_port = free_port(), free_port(), free_port(), free_port()
        cls.keys = make_keys("web", "telnet")
        core = ["tests.matrix_core"] + stack_args(cls.core_port, cls.fe_port, cls.keys)[1:]
        cls.procs = procs.owned(cls)
        cls.procs.append(spawn(*core, "--auth", "none"))
        if cls.FE[0] == "web":
            cls.procs.append(spawn(*adapter_args("adapters.web", cls.web_port, cls.fe_port, cls.keys, "web")))
        if cls.FE[0] == "telnet":
            cls.procs.append(spawn(*adapter_args("adapters.telnet", cls.tel_port, cls.fe_port, cls.keys,
                                                 "telnet")))

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)

    def frontend(self):
        kind = self.FE[0]
        if kind == "telnet":
            return TelnetFE(self, *self.FE[1:])
        return WebFE(self) if kind == "web" else NativeFE(self)

    def test_every_game(self):
        self.board_clicks = 0
        c = Conn(self.core_port, self.PREFIX + "admin")
        gam = c.req("GAM")
        c.close()
        drops = {l.split()[1] for l in gam if l.startswith("GAM ") and "drop" in (l.split() + [""])[6]}
        offered = {g["key"]: g for g in group_games(parse_games(gam))}
        jobs = []
        for gi, m in enumerate(G.all_games()):
            g = offered[m.KEY]
            for n in sorted({2, min(3, g["max"])}):
                jobs.append((gi, m, g, n, m.KEY in drops))
        self.assertEqual(len({j[1].KEY for j in jobs}), 10)
        fails, sem = [], threading.Semaphore(TABLES_AT_ONCE)

        def run(job):
            with sem:
                try:
                    self.one_table(*job)
                except Exception:
                    fails.append("%s, %d players: %s" % (job[1].NAME, job[3], traceback.format_exc()))
        threads = [threading.Thread(target=run, args=(j,)) for j in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if self.FE[0] == "web" and not fails:
            self.assertGreater(self.board_clicks, 40)       # moves made on the board images
        if fails:
            logs = []
            for proc in self.procs:         # what the servers said (they are not needed any more)
                procs.stop(proc)
                logs.append("\n".join(proc.tail)[-4000:])
            self.fail("%d of %d tables failed over %s:\n\n%s\n\nThe servers' errors:\n%s"
                      % (len(fails), len(jobs), "/".join(self.FE), "\n\n".join(fails), "\n".join(logs)))

    def one_table(self, gi, module, g, n, drop):
        fe = self.frontend()
        tag = "%s%d%d" % (self.PREFIX, gi, n)
        players = [Player(tag + r, Conn(self.core_port, tag + r), module) for r in "abc"[:n]]
        watcher = Player(tag + "w", Conn(self.core_port, tag + "w"), module)
        everyone = players + [watcher]
        try:
            for p in everyone:
                fe.connect(p)
            self.life(fe, module, g, n, drop, players, watcher)
        finally:
            for p in everyone:
                fe.close(p)
                p.oracle.close()

    def life(self, fe, module, g, n, drop, players, watcher):
        key = module.KEY
        a = players[0]
        # open the table; the others sit down from the lobby list; the game starts
        fe.open_table(a, g, n, typed_form=(n + len(a.name)) % 2 == 1)
        t = until(lambda: next((t for t in parse_tables(a.oracle.req("LST"))
                                if a.name in t["names"] and t["key"] == key), None), "the new table")
        tid = t["id"]
        self.assertEqual((t["seats"], t["status"]), (n, "open"))
        fe.at_table(a, tid)
        for p in players[1:]:
            fe.join(p, tid)
            until(lambda p=p: p.name in (table(a.oracle, tid) or {"names": ()})["names"], "%s seated" % p.name)
            fe.at_table(p, tid)
        t = until(lambda: (table(a.oracle, tid) or {}).get("status") == "playing" and table(a.oracle, tid),
                  "the game to start")
        for p in players:
            p.seat, p.tid = t["seat_of"][p.name], tid
        watcher.tid = tid
        names = t["seated"]
        # a stranger: refused a seat (told why), offered watching, watches
        fe.refused_join(watcher, tid)
        fe.offers(watcher, tid, seated=False)
        fe.watch(watcher, tid)
        fe.at_table(watcher, tid)
        # play: two real moves each at least; a move seen arriving; a player drops out and comes back
        turns = {p.seat: 0 for p in players}
        arrived = resumed = False
        end = time.time() + 60
        while min(turns.values()) < 2:
            self.assertLess(time.time(), end, "the game did not move on: %r" % turns)
            mover = None
            for p in players:
                lines, v = p.view(tid)
                self.assertNotIn("Game over", v["status"], "the game ended early: %r" % turns)
                if v["turn"] == p.seat:
                    a_ = next_action(key, p, tid, lines, v)
                    if a_:
                        mover = (p, a_, v)
                        break
            if mover is None:
                time.sleep(0.05)
                continue
            p, (tok, ends), v = mover
            others = [q for q in players if q is not p]
            snaps = {q.name: fe.snapshot(q) for q in others} if not arrived else {}
            fe.act(p, key, tid, tok)
            try:
                lines2, v2 = until(lambda: (lambda r: r if r[1]["seq"] != v["seq"] or still(r[0]) != still(lines)
                                            else None)(p.view(tid)),
                                   "%s's %s to be played" % (p.name, tok))
            except AssertionError:
                raise AssertionError("%s typed %r for %s; the screen:\n%s" % (
                    p.name, tok, typed(key, tok, v.get("tiles")), fe.dump(p.c) if hasattr(fe, "dump") else
                    getattr(getattr(p, "b", None), "body", "")[-1500:]))
            if ends or v2["turn"] != p.seat:
                turns[p.seat] += 1
            if not arrived:
                evts = [l.split(" ", 3)[3] for l in p.oracle.req("SIN %s %d" % (tid, v["seq"]))
                        if l.startswith("EVT ")]
                descs = [describe_for(fe.kind, module, e, names) for e in evts]
                descs = [d for d in descs if d]
                if descs:
                    text = mark_of(descs[0]) if fe.kind == "telnet" else descs[0]
                    q = others[0]
                    if fe.kind != "telnet" or text not in (snaps[q.name] if fe.full else ""):
                        fe.arrives(q, text, snaps[q.name])
                        arrived = True
            if not resumed and ends and min(turns.values()) >= 1:
                q = players[-1]
                fe.drop(q)
                time.sleep(0.05)
                fe.resume(q, tid)
                resumed = True
        self.assertTrue(arrived, "no move was seen arriving")
        self.assertTrue(resumed, "nobody came back")
        # the watcher leaves
        fe.unwatch(watcher)
        # the end: a resignation (two in a game where a seat can drop out of three)
        resigners = players[:2] if drop and n > 2 else players[:1]
        for p in resigners:
            fe.resign(p)
            until(lambda p=p: any(l.split()[3:5] in (["END", "resign"], ["GONE", str(p.seat)])
                                  for l in players[-1].oracle.req("SIN %s 0" % tid) if l.startswith("EVT ")),
                  "%s's resignation" % p.name)
        evts = [l.split(" ", 3)[3] for l in players[-1].oracle.req("SIN %s 0" % tid) if l.startswith("EVT ")]
        self.assertTrue(evts[-1].startswith("END resign"), evts[-3:])
        end_text = describe_for(fe.kind, module, evts[-1], names)
        self.assertTrue(end_text)
        for p in players:
            fe.after_end(p, mark_of(end_text) if fe.kind == "telnet" else end_text)


@slow
class TelnetPc(Matrix, unittest.TestCase):
    FE, PREFIX = ("telnet", "pc"), "tp"


@slow
class TelnetUtf8(Matrix, unittest.TestCase):
    FE, PREFIX = ("telnet", "utf8"), "tu"


@slow
class TelnetAnsi(Matrix, unittest.TestCase):
    FE, PREFIX = ("telnet", "ansi"), "ta"


@slow
class TelnetIjkl(Matrix, unittest.TestCase):
    FE, PREFIX = ("telnet", "pc", "ijkl"), "tk"


@slow
class TelnetPlain(Matrix, unittest.TestCase):
    FE, PREFIX = ("telnet", "plain"), "tl"


@slow
class Web(Matrix, unittest.TestCase):
    FE, PREFIX = ("web",), "wb"


@slow
class Native(Matrix, unittest.TestCase):
    FE, PREFIX = ("native",), "nt"


class NoShadowing(unittest.TestCase):
    """A game screen's own attributes never hide a method of the screen classes (Grid Words once kept the
    start square of a typed word in self.start, which hid GameBase.start and dropped the connection)."""

    def test_no_attribute_hides_a_method(self):
        from game_checks import ScreenCore, session
        from test_adapters import FakeIO
        import inspect
        for m in G.all_games():
            src = inspect.getsource(m)
            assigned = set(re.findall(r"self\.(\w+)\s*(?:=|\+=|-=)(?!=)", src))
            for cls, mode in ((m.AnsiScreen, "pc"), (m.PlainScreen, "plain")):
                s = session(FakeIO(mode), ScreenCore(m.KEY, []), mode)
                g = cls(s, "3", m.KEY, 0)
                for k in assigned | set(vars(g)):
                    self.assertFalse(callable(getattr(cls, k, None)), "%s.%s hides a method" % (cls.__name__, k))


class TelnetKeepsTheLine(unittest.TestCase):
    """A failure inside a table screen is logged with its traceback and the player is back at the menu;
    a seat asked for at a running table says how to watch it, and its number alone watches it."""

    def test_screen_failure_back_to_the_menu(self):
        import asyncio
        import contextlib
        import io
        from unittest import mock
        from game_checks import LONG, ScreenCore, session
        from test_adapters import FakeIO
        from adapters.games import reversi
        for mode, cls in (("pc", reversi.AnsiScreen), ("plain", reversi.PlainScreen)):
            s = session(FakeIO(mode), ScreenCore(reversi.KEY, ["V reversi/1 1", "W 0"], names=(LONG, "bob")), mode)

            async def boom(self):
                raise ZeroDivisionError("a bug")
            err = io.StringIO()
            with mock.patch.object(cls, "run", boom), contextlib.redirect_stderr(err):
                self.assertEqual(asyncio.run(s.enter("3")), T.SORRY)
            self.assertIn("Traceback", err.getvalue())
            self.assertIn("ZeroDivisionError: a bug", err.getvalue())

    def test_running_table_join_and_number(self):
        import asyncio
        from game_checks import ScreenCore, session
        from test_adapters import FakeIO
        from adapters.games import reversi
        core = ScreenCore(reversi.KEY, ["V reversi/1 1", "W 0"], names=("ann", "bob"))
        s = session(FakeIO("plain"), core, "plain", name="zed")
        self.assertEqual(asyncio.run(s.enter("3", sit=True)), "Table 3 is already playing. Type /watch 3 to watch it.")
        self.assertNotIn("JON 3", core.reqs)
        with self.assertRaises(EOFError):             # the number alone opens it to watch (FakeIO runs out)
            asyncio.run(s.enter("3"))
        self.assertIn("SEE 3", core.reqs)
        self.assertNotIn("JON 3", core.reqs)
