"""FIBS adapter tests. Each client class reads the output the way the
period client does:

    MacFIBS   one-line "connect name pw" login, commands end in a bare CR,
              lines split at CR or LF, dispatch by exact word count, no
              "> " prompts tolerated, the set / toggle / rawwho start-up
    FIBS/W    the login script (login: / password: / "\\n>" / Value of),
              CR LF commands, bare move pairs, prefix matching after
              skipping leading ">" and spaces
    xfibs     parses only when the input ends in "> " (also after the
              opponent's moves), CR LF lines, "move bar-22 13-11"
    CLIP      "login client 1008 name pw" and numbered lines

A web player plays through the HTML pages of adapters/web.py."""
import http.client
import re
import socket
import time
import unittest
from urllib.parse import parse_qs, urlsplit

import procs
from test_adapters import WebPlayers, spawn
from test_backgammon import bg
from test_core import adapter_args, free_port, make_keys, stack_args
from speed import slow  # noqa: E402

WAIT = 15


# ---- the board line, read as the clients read it ------------------------------------

def parse_board(line):
    """53 colon-separated fields; MacFIBS's
    normalization: mirror when direction is 1, negate when color is -1, so
    the player's own checkers are positive and move from 24 down to 1."""
    assert line.startswith("board:"), line
    f = line.split(":")
    assert len(f) == 53, (len(f), line)
    n = [int(x) for x in f[3:]]
    b = {"player": f[1], "opp": f[2], "match": n[0], "score": (n[1], n[2]), "raw": n[3:29],
         "turn": n[29], "dice": n[30:32], "odice": n[32:34], "cube": n[34], "may_double": n[35:37],
         "doubled": n[37], "colour": n[38], "direction": n[39], "home": n[40], "bar": n[41],
         "on_home": n[42:44], "on_bar": n[44:46], "can_move": n[46], "rest": n[47:]}
    pts = list(b["raw"][1:25])
    if b["direction"] == 1:
        pts = pts[::-1]
    if b["colour"] == -1:
        pts = [-x for x in pts]
    b["pts"] = pts                  # pts[0] is the player's own point 1
    b["mine"] = b["turn"] * b["colour"] == 1
    return b


def server_point(b, p):
    """Back to the server's numbering (MacFIBS's tables by direction)."""
    if p in ("bar", "off"):
        return p
    return 25 - p if b["direction"] == 1 else p


def plan_move(b):
    """A full legal move for the board's player, in the player's own frame,
    found with the backgammon/1 rules: [(from, to)]."""
    st = bg.new_state(0, True)
    st["pts"] = list(b["pts"])
    st["bar"] = list(b["on_bar"])
    st["off"] = list(b["on_home"])
    st["turn"], st["phase"], st["steps"] = 0, "move", []
    st["dice"] = bg.dice_for(b["dice"])
    out = []
    while True:
        legal = bg.legal_steps(st)
        if not legal:
            return out
        (f, t), d = sorted(legal.items(), key=lambda e: str(e[0]))[0]
        nb, _ = bg.apply_step(bg.board_of(st), 0, f, t)
        st["pts"], st["bar"], st["off"] = nb["pts"], nb["bar"], nb["off"]
        st["dice"].remove(d)
        st["steps"].append(1)
        out.append((f, t))


def check_against_view(test, b, view, seat):
    """A board line agrees with the core's own view of the table."""
    test.assertEqual(b["raw"][1:25], view["pts"])
    test.assertEqual(b["raw"][0], -view["bar"][1])
    test.assertEqual(b["raw"][25], view["bar"][0])
    test.assertEqual(b["colour"], 1 if seat == 0 else -1)
    test.assertEqual(b["direction"], -1 if seat == 0 else 1)
    test.assertEqual(b["on_home"], [view["off"][seat], view["off"][1 - seat]])
    test.assertEqual(b["on_bar"], [view["bar"][seat], view["bar"][1 - seat]])
    test.assertEqual(b["cube"], view["cube"])
    test.assertEqual(sum(b["pts"][i] for i in range(24) if b["pts"][i] > 0) + b["on_home"][0] + b["on_bar"][0], 15)


# ---- clients ----------------------------------------------------------------------------

class Raw:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = b""
        self.seen = []
        self.queue = []

    def recv(self, end):
        self.s.settimeout(max(0.05, min(0.3, end - time.time())))
        try:
            d = self.s.recv(65536)
        except socket.timeout:
            return True
        if not d:
            return False
        self.buf += d
        return True

    def until_raw(self, text, timeout=WAIT):
        """The raw stream up to and including text (scripts match the stream)."""
        end = time.time() + timeout
        t = text.encode()
        while t not in self.buf:
            if time.time() > end:
                raise AssertionError("%r not seen; buffer %r" % (text, self.buf[-500:]))
            if not self.recv(end):
                raise EOFError("closed; buffer %r" % self.buf[-500:])
        i = self.buf.index(t) + len(t)
        out, self.buf = self.buf[:i], self.buf[i:]
        return out.decode("ascii", "replace")

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class MacFIBS(Raw):
    """MacFIBS 2.0.3."""

    def __init__(self, port):
        super().__init__(port)
        self.logged_in = False
        self.board = None
        self.fresh = False      # a board arrived since the last action
        self.prompts = 0
        self.lines = []

    def send(self, cmd):
        self.s.sendall(cmd.encode() + b"\r")         # a bare CR

    def pump(self, timeout=0.3):
        """Read for a while; split lines at CR or LF; act like the client."""
        end = time.time() + timeout
        while True:
            m = re.search(rb"[\r\n]", self.buf)
            if m:
                raw, self.buf = self.buf[:m.start()], self.buf[m.end():]
                line = raw.decode("ascii", "replace")
                if line.strip():
                    self.handle(line)
                continue
            if not self.logged_in and self.buf.startswith(b"login:"):
                self.buf = b""
                self.send("connect %s %s" % (self.user, self.password))
                continue
            if time.time() > end:
                return
            if not self.recv(end):
                raise EOFError

    def handle(self, line):
        self.seen.append(line)
        if line.startswith("> ") or line.lstrip().startswith(">"):
            self.prompts += 1
        s = line.lstrip()
        if s.startswith("board:"):
            self.board = parse_board(s)
            self.fresh = True
            return
        w = s.split()
        if not self.logged_in and len(w) >= 3 and w[0] == "**" and w[1] == "User":
            self.logged_in = True
            self.send("set")
        elif len(w) == 2 and w[0] == "boardstyle:" and w[1] != "3":
            self.send("set boardstyle 3")
        elif len(w) == 2 and w[0] == "timezone:" and not getattr(self, "toggled", False):
            self.toggled = True
            self.send("toggle")
        elif len(w) == 2 and w[0] == "wrap" and not getattr(self, "whoed", False):
            self.whoed = True
            self.send("rawwho")
        self.lines.append(s)

    def login(self, user, password):
        self.user, self.password = user, password
        end = time.time() + WAIT
        while not any(l == "who:end" for l in self.lines):
            if time.time() > end:
                raise AssertionError("start-up did not finish: %r" % self.seen[-20:])
            self.pump(0.2)

    def wait(self, pred, timeout=WAIT):
        end = time.time() + timeout
        while True:
            for i, l in enumerate(self.lines):
                if pred(l):
                    del self.lines[:i + 1]
                    return l
            del self.lines[:]
            if time.time() > end:
                raise AssertionError("not seen; last lines %r" % self.seen[-15:])
            self.pump(0.2)

    @staticmethod
    def classify(line):
        """The word-count dispatch of ParseEnglish for the lines a game needs."""
        w = line.split()
        n = len(w)
        if line.startswith("board:"):
            return "board"
        if n >= 3 and w[1] == "says:":
            return "chat"
        if w and w[0] == "**":
            if n >= 3 and w[1] == "User":
                return "login"
            if line.startswith("** You are now playing a") or " has joined you for a" in line:
                return "match-start"
            if line.startswith("** You terminated the game") or "has left the game." in line:
                return "left"
            return "starstar"
        if n == 2 and w[1] == "doubles.":
            return "double-offer"
        if n == 3 and w[1:] == ["can't", "move."]:
            return "cant-move"
        if n == 3 and w[1] == "logs" and w[2] in ("in.", "out."):
            return "logs-" + w[2][:-1]
        if n == 4 and " accepts the double." in line:
            return "accepts"
        if n == 5 and w[1] in ("roll", "rolls") and w[2][0].isdigit() and w[4][0].isdigit():
            return "roll"
        if n == 6 and "doubles. Type 'accept' or 'reject'." in line:
            return "double-dialog"
        if n == 6 and line.startswith("Starting a new game with "):
            return "new-game"
        if n == 7 and line.startswith(("It's your turn to roll or double.", "It's your turn. Please roll or double")):
            return "roll-or-double"
        if n == 8 and line.startswith("You win the"):
            return "match-won"
        if n == 8 and "wins the" in line and "point match" in line:
            return "match-lost"
        if n == 9 and w[0] == "who:":
            return "rawwho"
        if n == 9 and line.startswith("You win the game and get"):
            return "game-won"
        if n == 9 and w[1] == "wants" and "wants to play a" in line:
            return "invite"
        if n == 9 and w[1] == "has" and "Your running match was loaded" in line:
            return "resumed-by"
        if n >= 10 and line.startswith("You are now playing with") and "Your running match was loaded." in line:
            return "resumed"
        if n >= 10 and "wants to resign. " in line:
            return "resign-dialog"
        if n >= 10 and w[0] == "Type" and w[1] == "'join'":
            return "join-dialog"
        if n >= 10 and "wants to play a" in line:
            return "invite"
        return None


class MacFIBS3(Raw):
    """MacFIBS 3.0.6 (r6) as captured on the wire from the real program in an
    emulator. It answers "login: " with one
    line ending in CR LF: "connect <name> <password>", or, with its hidden "Use
    CLIP" setting, "login MacFIBS_3.0.6 1008 <name> <password>". Both modes then
    wait for a line that starts "** Last login:" and send rawwho, show games,
    show saved, set and toggle, each after the reply to the one before."""

    STARTUP = ("rawwho", "show games", "show saved", "set", "toggle")

    def send(self, cmd):
        self.s.sendall(cmd.encode() + b"\r\n")

    def login(self, user, password, clip=False):
        self.until_raw("login: ")
        self.send(("login MacFIBS_3.0.6 1008 %s %s" if clip else "connect %s %s") % (user, password))
        text = self.until_raw("** Last login:")
        text += self.until_raw("\r\n")
        replies = {}
        ends = {"rawwho": "who:end\r\n", "show games": "\r\n", "show saved": "\r\n", "set": "timezone:",
                "toggle": "wrap"}
        for cmd in self.STARTUP:
            self.send(cmd)
            replies[cmd] = self.until_raw(ends[cmd])
            if cmd in ("set", "toggle"):
                replies[cmd] += self.until_raw("\r\n")
        return text, replies


class FibsW(Raw):
    """FIBS/W 1.30: the WSKLOGIN/INITFIBS scripts."""

    def send(self, cmd):
        self.s.sendall(cmd.encode() + b"\r\n")

    def login(self, user, password):
        self.until_raw("login: ")
        self.send(user)
        text = self.branch(["password: ", "login: ", "\n>"])
        assert text.endswith("password: "), text
        self.send(password)
        text = self.branch(["login: ", "authenticated.", "\n>"])
        assert not text.endswith("login: "), text
        if text.endswith("authenticated."):
            text += self.until_raw("\n>")
        self.send("set boardstyle 3")
        text += self.until_raw("Value of", 50)
        text += self.until_raw("\n>")
        return text

    def branch(self, pats, timeout=WAIT):
        end = time.time() + timeout
        while True:
            hits = [(self.buf.find(p.encode()), p) for p in pats if p.encode() in self.buf]
            if hits:
                i, p = min(hits)
                out, self.buf = self.buf[:i + len(p)], self.buf[i + len(p):]
                return out.decode()
            if time.time() > end or not self.recv(end):
                raise AssertionError("none of %r; buffer %r" % (pats, self.buf[-300:]))

    def lines(self, timeout=0.5):
        """Lines cut at LF, CR dropped, leading '>' and spaces skipped."""
        end = time.time() + timeout
        while time.time() < end:
            self.recv(end)
        out = []
        while b"\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n", 1)
            l = raw.replace(b"\r", b"").decode().lstrip("> ")
            out.append(l)
        self.seen += out
        return out

    def wait(self, prefix, timeout=WAIT):
        end = time.time() + timeout
        while True:
            while self.queue:
                l = self.queue.pop(0)
                if l.startswith(prefix):
                    return l
            if time.time() > end:
                raise AssertionError("%r not seen; last %r" % (prefix, self.seen[-15:]))
            self.queue += self.lines(0.2)


class XFibs(Raw):
    """xfibs 0.8: input is parsed only once it ends in
    '> '; lines split at CR LF; a leading '> ' is removed."""

    def send(self, cmd):
        self.s.sendall(cmd.encode() + b"\r\n")

    def login(self, user, password):
        end = time.time() + WAIT
        sent = set()
        while True:
            last = self.buf.split(b"\r\n")[-1]
            if last.startswith(b"login:") and "login" not in sent:
                sent.add("login")
                self.send(user)
            elif last.startswith(b"passwo") and "password" not in sent:
                sent.add("password")
                self.send(password)
            elif self.buf.endswith(b"> ") and "password" in sent:
                self.queue += self.burst()          # the login burst is parsed like any other
                return
            if time.time() > end or not self.recv(end):
                raise AssertionError("xfibs login: %r" % self.buf[-300:])

    def burst(self, timeout=WAIT):
        """The next burst: everything up to a trailing '> ', as parsed lines."""
        end = time.time() + timeout
        while not self.buf.endswith(b"> "):
            if time.time() > end:
                raise AssertionError("no burst ending in '> ': %r" % self.buf[-300:])
            if not self.recv(end):
                raise EOFError
        text, self.buf = self.buf.decode(), b""
        out = []
        for l in text.split("\r\n"):
            while l.startswith("> "):
                l = l[2:]
            if l:
                out.append(l)
        self.seen += out
        return out

    def wait(self, pred, timeout=WAIT):
        end = time.time() + timeout
        while True:
            while self.queue:
                l = self.queue.pop(0)
                if pred(l):
                    return l
            self.queue += self.burst(max(0.5, end - time.time()))


class Clip(Raw):
    def send(self, cmd):
        self.s.sendall(cmd.encode() + b"\r\n")

    def lines(self, timeout=0.5):
        end = time.time() + timeout
        while time.time() < end:
            self.recv(end)
        out = []
        while b"\r\n" in self.buf:
            raw, self.buf = self.buf.split(b"\r\n", 1)
            out.append(raw.decode())
        self.seen += out
        return out

    def wait(self, pred, timeout=WAIT):
        end = time.time() + timeout
        while True:
            while self.queue:
                l = self.queue.pop(0)
                if pred(l):
                    return l
            if time.time() > end:
                raise AssertionError("not seen; last %r" % self.seen[-15:])
            self.queue += self.lines(0.2)


class Native:
    """A second, native connection for a player: the core's own view."""

    def __init__(self, port, name, password):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.s.sendall(b"PARLORNET/1\r\n")       # the opening first, then the greeting
        self.f = self.s.makefile("rb")
        self.f.readline()
        r = self.req("HELO %s" % name)[-1]                  # the name, then the password when asked
        if r.startswith("ASK password"):
            r = self.req("AUT password %s" % password)[-1]
        assert r.startswith("OK"), r

    def req(self, text):
        self.s.sendall(text.encode() + b"\r\n")
        out = []
        while True:
            l = self.f.readline().decode().strip()
            out.append(l)
            if l.startswith(("OK", "ERR", "ASK")):
                return out

    def view(self, tid):
        from adapters.common import parse_backgammon_view
        return parse_backgammon_view(self.req("VUE %s" % tid))

    def close(self):
        self.s.close()


# ---- the tests ------------------------------------------------------------------------

class Stack(WebPlayers, unittest.TestCase):
    auth = "password"

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.fibs, cls.web = free_port(), free_port(), free_port(), free_port()
        cls.keys = make_keys("fibs", "web")
        cls.procs = procs.owned(cls)
        cls.procs.append(spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", cls.auth))
        cls.procs.append(spawn(*adapter_args("adapters.fibs", cls.fibs, cls.fe, cls.keys, "fibs"), "--poll", "0.25",
                               "--prompt-wait", "2"))
        cls.procs.append(spawn(*adapter_args("adapters.web", cls.web, cls.fe, cls.keys, "web")))

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)

    def client(self, cls):
        c = cls(self.fibs)
        self.addCleanup(c.close)
        return c

    def password_for(self, name):
        """A new name over the native protocol, signed up with a password (as the
        web would make it); returns the password."""
        n = Native.__new__(Native)
        n.s = socket.create_connection(("127.0.0.1", self.core), timeout=5)
        n.s.sendall(b"PARLORNET/1\r\n")       # the opening first, then the greeting
        n.f = n.s.makefile("rb")
        n.f.readline()
        pw = name + "-Pass-word"
        assert n.req("HELO %s" % name)[-1].startswith("ASK password Choose")
        assert n.req("AUT password %s" % pw)[-1].startswith("ASK confirm")
        assert n.req("AUT confirm %s" % pw)[-1] == "OK -"
        n.close()
        return pw

    def web_act(self, tid, name, avoid=()):
        """Everything the web player can do now: follow the first move link
        (a roll, a step, take, accept) or end the turn. Returns the actions."""
        done = []
        for _ in range(8):
            body = self.web_get("/table.htm?t=%s&n=%s&r=0" % (tid, name))[2]
            links = [h.replace("&amp;", "&") for h in re.findall(r'href="(/move\.htm\?[^"]+)"', body)]
            links = [h for h in links if not any("mv=%s&" % a in h + "&" for a in avoid)]
            if links:
                st = self.web_get(links[0])[0]
                self.assertEqual(st, 302)
                done.append(parse_qs(urlsplit(links[0]).query)["mv"][0])
                continue
            e = re.search(r'href="(/end\.htm\?[^"]+)"', body)
            if e:
                self.assertEqual(self.web_get(e.group(1).replace("&amp;", "&"))[0], 302)
                done.append("end")
                continue
            break
        return done


@slow
class Fibs(Stack):
    def test_board_line_layout(self):
        """The start of a 3 point match, O rolled 6-2: the CLIP specification's example."""
        from adapters.fibs import Model, board_line
        m = Model()
        m.new_game()
        m.match, m.turn, m.phase, m.rolled, m.need = 3, 0, "move", (6, 2), 2
        line = board_line(m, True, 0, ["me", "someplayer"])
        self.assertEqual(line, "board:You:someplayer:3:0:0:0:-2:0:0:0:0:5:0:3:0:0:0:-5:5:0:0:0:-3:0:-5:0:0:0:0:"
                               "2:0:1:6:2:0:0:1:1:1:0:1:-1:0:25:0:0:0:0:2:0:0:0")
        x = parse_board(board_line(m, True, 1, ["someplayer", "me"]))
        self.assertEqual((x["colour"], x["direction"], x["turn"], x["dice"], x["odice"]), (-1, 1, 1, [0, 0], [6, 2]))
        self.assertEqual(x["pts"][:6], [-2, 0, 0, 0, 0, 5])     # X's own view: 5 on its 6 point
        self.assertFalse(x["mine"])
        from adapters.fibs import parse_move
        self.assertEqual(parse_move(["13-9", "8-7"], 0), [(13, 9), (8, 7)])
        self.assertEqual(parse_move("13 7 8 7".split(), 0), [(13, 7), (8, 7)])
        self.assertEqual(parse_move("bar - 5 bar -5 6 off".split(), 1), [("bar", 5), ("bar", 5), (6, "off")])
        self.assertEqual(parse_move("b 22 25 20 6 o".split(), 0)[:2], [("bar", 22), ("bar", 20)])
        self.assertIsNone(parse_move(["off-3"], 0))

    def test_macfibs_login_startup_who_toggles(self):
        pw = self.password_for("mona")
        mac = self.client(MacFIBS)
        mac.login("mona", pw)
        self.assertTrue(mac.logged_in)
        self.assertEqual(mac.prompts, 0)                        # no "> " for connect logins
        rows = [l for l in mac.seen if l.startswith("who:")]
        self.assertIn("who:end", rows)
        me = [l.split() for l in rows if l.split()[3:4] == ["mona"]]
        self.assertEqual(len(me[0]), 9)
        self.assertEqual(MacFIBS.classify(" ".join(me[0])), "rawwho")
        self.assertIn(me[0][2][0], "RP-")
        toggles = [l.split() for l in mac.seen if len(l.split()) == 2 and l.split()[1] in ("YES", "NO")]
        self.assertEqual([t[0] for t in toggles][-1], "wrap")
        self.assertIn(["telnet", "NO"], toggles)
        self.assertIn(["moreboards", "YES"], toggles)
        mac.send("toggle ready")
        self.assertEqual(mac.wait(lambda l: l.startswith("** You")), "** You're now refusing to play with someone.")
        mac.send("toggle double")
        self.assertEqual(mac.wait(lambda l: l.startswith("**")), "** You won't be asked if you want to double.")
        mac.send("toggle double")
        mac.wait(lambda l: l == "** You will be asked if you want to double.")
        mac.send("frobnicate")
        mac.wait(lambda l: l == "** Unknown command: 'frobnicate'")
        # another FIBS user logs in and out: "<name> logs in." (3 words)
        x = self.client(XFibs)
        x.login("xena", self.password_for("xena"))
        self.assertEqual(MacFIBS.classify(mac.wait(lambda l: "xena" in l)), "logs-in")
        x.send("bye")
        self.assertEqual(MacFIBS.classify(mac.wait(lambda l: "xena" in l)), "logs-out")
        self.assertEqual(mac.prompts, 0)

    def test_macfibs3_both_login_lines(self):
        """MacFIBS 3 starts up only after "** Last login:". Its CLIP login gets the same
        English lines as its connect login, because the program reads no CLIP numbered
        lines. Both replay the exact lines MacFIBS 3 sent in the emulator."""
        for clip, name in ((False, "mfnon"), (True, "mfclip")):
            pw = self.password_for(name)
            m = self.client(MacFIBS3)
            text, replies = m.login(name, pw, clip)
            lines = [l for l in text.split("\r\n") if l]
            self.assertEqual(lines[-2:], ["** User %s authenticated." % name,
                                          "** Last login: not shown on this server."], clip)
            self.assertFalse(re.search(r"(?m)^1 %s \d+" % name, text), clip)       # no CLIP welcome
            rows = [l.split() for l in replies["rawwho"].split("\r\n") if l.startswith("who: ")]
            self.assertIn(name, [r[3] for r in rows])
            self.assertTrue(all(len(r) == 9 for r in rows), rows)
            self.assertTrue(replies["show games"].startswith("List of games:"))
            self.assertIn("boardstyle: 3", replies["set"])
            self.assertIn("telnet          NO", replies["toggle"])
            self.assertNotIn("> ", "".join(replies.values()) + text)               # no prompts either way
            m.send("tell %s hello" % name)
            m.until_raw("** You tell %s: hello\r\n" % name)

    def test_macfibs3_quit_on_a_quiet_connection(self):
        """MacFIBS 3's File > Quit sends nothing; it closes its side of the connection.
        Under MacTCP it first waits for its pending receive to get data, so a connection
        on which the server stays quiet (a match on the player's own move) hung it. The
        server sends a connect session an empty line after a few quiet seconds, and closes
        at once when the client closes its side. A telnet session gets no such lines."""
        from adapters.fibs import KEEPALIVE_SECS
        m = self.client(MacFIBS3)
        m.login("mfquit", self.password_for("mfquit"))
        w = self.client(FibsW)
        w.login("wquiet", self.password_for("wquiet"))
        end = time.time() + 1                               # "wquiet logs in." and the prompt
        while time.time() < end:
            m.recv(end)
            w.recv(end)
        m.buf = w.buf = b""
        start = time.time()
        got = m.until_raw("\r\n", timeout=KEEPALIVE_SECS + 3)
        self.assertEqual(got, "\r\n")                       # an empty line, nothing else
        self.assertGreater(time.time() - start, 1.0)        # only after quiet seconds
        end = time.time() + 1
        while time.time() < end:
            w.recv(end)
        self.assertNotIn(b"\r\n\r\n", w.buf)
        self.assertEqual(w.buf.strip(b"\r\n> "), b"")       # no keepalive for telnet sessions
        m.s.shutdown(socket.SHUT_WR)                        # Quit: the client closes its side
        end = time.time() + 3
        m.s.settimeout(0.3)
        closed = False
        while time.time() < end and not closed:
            try:
                closed = m.s.recv(4096) == b""
            except socket.timeout:
                pass
        self.assertTrue(closed, "the server did not close after the client's half-close")

    def test_presence_ignores_private_tables(self):
        """The table list is polled through each session in turn, and a private table
        is listed only to its own players. Its players must not log in and out from
        poll to poll (seen with MacFIBS 3 and the bot)."""
        from adapters.fibs import Hub
        hub = Hub()
        hub.tables = [{"key": "backgammon/1", "status": "playing", "names": ["ann", "bot"], "vis": "private"},
                      {"key": "backgammon/1", "status": "open", "names": ["cid"], "vis": "public"}]
        self.assertEqual(hub.everyone(), {"cid": "cid"})

    def test_fibsw_prompts_set_and_guest(self):
        pw = self.password_for("wally")
        w = self.client(FibsW)
        text = w.login("wally", pw)
        self.assertIn("** User wally authenticated.", text)
        w.send("set")
        self.assertEqual(w.wait("boardstyle:"), "boardstyle: 3")
        w.send("who")
        w.wait("No  S  username")
        w.send("toggle greedy")
        self.assertEqual(w.wait("**"), "** Will use automatic greedy bearoffs.")
        w.send("set timezone Europe/Berlin")
        self.assertIn("Value of 'timezone' set to Europe/Berlin.", w.wait("Value of"))
        # a wrong password: the reason, then login: again
        bad = self.client(FibsW)
        bad.until_raw("login: ")
        bad.send("wally")
        bad.until_raw("password: ")
        bad.send("nope")
        self.assertIn("Wrong password", bad.until_raw("login: "))
        # a name with no account: said so, with FIBS's way to register; no sign-up starts
        unk = self.client(FibsW)
        unk.until_raw("login: ")
        unk.send("zed")
        unk.until_raw("password: ")
        unk.send("zed-Pass-word")
        self.assertIn("There is no account named zed here. New here? Log in as guest.", unk.until_raw("login: "))
        # guest registration ends in "You are registered." and a prompt
        g = self.client(FibsW)
        g.until_raw("login: ")
        g.send("guest")
        g.until_raw("\n>")
        self.password_for("taken1")
        g.send("name taken1")                               # a name in use: taken, never a sign-in here
        self.assertIn("The name taken1 is taken; choose another", g.until_raw("\n>"))
        g.send("name guest")                                # a reserved word
        self.assertIn("Guest is a reserved word here", g.until_raw("\n>"))
        g.send("name gus_w")
        g.until_raw("password")
        g.send("Gus-secret")
        g.until_raw("again")
        g.send("Gus-secret")
        g.until_raw("\nYou are registered.")
        g.until_raw("\n>")
        g.send("set boardstyle 3")
        g.until_raw("Value of")
        again = self.client(FibsW)
        again.login("gus_w", "Gus-secret")                   # the password chosen at registration

    def test_identifies_as_parlornet_not_fibs(self):
        """The banner, the guest text, help, about and whois say ParlorNet, not FIBS, and point to
        fibs.com; no FIBS rating is shown; the adapter has no way to reach any server but the core."""
        import adapters.fibs as F
        x = self.client(XFibs)
        banner = x.until_raw("login: ")
        self.assertIn("This is ParlorNet's FIBS-protocol service for vintage Backgammon clients;", banner)
        self.assertIn("it is not FIBS", banner)
        self.assertIn("telnet to\r\nfibs.com 4321.", banner)
        self.assertTrue(banner.endswith("\r\nlogin: "))
        self.assertTrue(all(len(l) <= 79 for l in banner.split("\r\n")))
        self.assertNotIn("Welcome to FIBS", banner)
        x.buf = banner.encode() + x.buf                          # let the client's own login read it
        x.login("idris", self.password_for("idris"))
        text = "\r\n".join(x.seen)
        self.assertIn("This is ParlorNet, not FIBS.", text)
        for cmd, want in (("about", "ParlorNet is not FIBS and is not affiliated with it. For FIBS, telnet to "
                                    "fibs.com 4321."),
                          ("help", "This is ParlorNet, not FIBS; type 'about' to read more."),
                          ("whois idris", "  No rating: games here are unrated.")):
            x.send(cmd)
            self.assertEqual(x.wait(lambda l: l == want), want)
        x.send("rawwho")
        row = x.wait(lambda l: l.startswith("who:") and " idris " in l).split()
        self.assertEqual(len(row), 9)
        self.assertEqual((row[4], row[8]), (F.UNRATED, F.HERE))
        self.assertNotIn("1500", "\r\n".join(x.seen))
        g = self.client(FibsW)
        g.until_raw("login: ")
        g.send("guest")
        self.assertIn("this is not a FIBS account", g.until_raw("\n>"))
        g.send("bye")
        # nothing in the adapter opens a connection of its own, other than to the core it is given
        with open(F.__file__, encoding="utf-8") as f:
            src = f.read()
        for bad in ("open_connection", "create_connection", "socket", "urllib", "http.client"):
            self.assertNotIn(bad, src)

    def test_macfibs_and_web_full_game(self):
        """MacFIBS invites a web player to a 1 point match; they play it out.
        The MacFIBS board lines match the core's view; moves go both ways."""
        pw = self.password_for("macy")
        self.web_login("webb")
        mac = self.client(MacFIBS)
        mac.login("macy", pw)
        mac.send("invite webb 1")
        mac.wait(lambda l: l.startswith("** You invited webb to a 1 point match."))
        lobby = self.web_get("/lobby.htm?n=webb")[2]
        tid = re.search(r"/join\.htm\?[^\"]*\bt=(\d+)", lobby).group(1)
        self.assertEqual(self.web_get("/join.htm?t=%s&n=webb" % tid)[0], 302)
        self.assertEqual(MacFIBS.classify(mac.wait(lambda l: "has joined you" in l)), "match-start")
        self.assertEqual(MacFIBS.classify(mac.wait(lambda l: l.startswith("Starting"))), "new-game")
        mac.wait(lambda l: l.startswith("You rolled "))
        native = Native(self.core, "macy", pw)
        self.addCleanup(native.close)
        seat = 0
        checked = moves = web_moves = 0
        end = time.time() + 240
        result = None
        while result is None:
            self.assertLess(time.time(), end, mac.seen[-20:])
            web_moves += len(self.web_act(tid, "webb", avoid=("double",)))
            mac.pump(0.15)
            for l in mac.lines:
                k = MacFIBS.classify(l)
                if k in ("game-won", "match-won", "match-lost") or "wins the game" in l:
                    result = l
                if k == "roll-or-double":
                    mac.send("roll")
            del mac.lines[:]
            b = mac.board
            if b and mac.fresh and b["mine"] and b["dice"] != [0, 0] and b["can_move"] and result is None:
                mac.fresh = False
                if checked < 3:
                    v = native.view(tid)
                    if v["seq"] and not v["phase"] == "over":
                        check_against_view(self, b, v, seat)
                        checked += 1
                plan = plan_move(b)
                self.assertEqual(len(plan), b["can_move"] if b["on_home"][0] + sum(1 for _, t in plan if t == "off")
                         < 15 else len(plan), (b, plan))
                mac.send("move " + " ".join("%s %s" % (server_point(b, f), server_point(b, t)) for f, t in plan))
                moves += 1
        self.assertGreater(moves, 5)
        self.assertGreater(web_moves, 5)
        self.assertGreater(checked, 0)
        mac.pump(1.0)
        self.assertEqual(mac.board["turn"], 0)                  # the last board: game over
        self.assertEqual(mac.prompts, 0)

    def test_xfibs_and_fibsw_double_resign_and_match(self):
        """Two FIBS clients, a 3 point match: double and take, resign offers
        rejected and accepted, the next game after 'join', the match end.
        xfibs only reads bursts that end in '> '."""
        x, w = self.client(XFibs), self.client(FibsW)
        x.login("xavier", self.password_for("xavier"))
        w.login("wilma", self.password_for("wilma"))
        x.send("set boardstyle 3")
        x.wait(lambda l: l.startswith("Value of"))
        x.send("invite wilma 3")
        self.assertEqual(w.wait("xavier wants"), "xavier wants to play a 3 point match with you.")
        w.send("join xavier")
        x.wait(lambda l: l.startswith("** Player wilma has joined you for a 3 point match."))
        w.wait("** You are now playing a 3 point match with xavier")
        opening = x.wait(lambda l: l.startswith(("It's your turn to move.", "wilma makes the first move.")))
        boards = {}

        def x_board():
            return parse_board(x.wait(lambda l: l.startswith("board:")))

        def w_board():
            return parse_board(w.wait("board:"))
        # the opener moves: whoever it is, with each client's own syntax
        if opening.startswith("It's your turn"):
            b = x_board()
            x.send("move " + " ".join("%s-%s" % (server_point(b, f), server_point(b, t)) for f, t in plan_move(b)))
            first, second = x, w
        else:
            b = w_board()
            w.send(" ".join("%s %s" % (server_point(b, f), server_point(b, t)) for f, t in plan_move(b)))
            first, second = w, x
        # the other side: its turn with the cube: double; the opener takes
        if second is x:
            x.wait(lambda l: l.startswith("It's your turn. Please roll or double"))
            x.send("double")
            x.wait(lambda l: l.startswith("You double. Please wait for wilma"))
            w.wait("xavier doubles.")
            w.send("accept")
            w.wait("You accept the double. The cube shows 2.")
            self.assertEqual(x.wait(lambda l: "accepts the double" in l),
                             "wilma accepts the double. The cube shows 2.")
            boards["x"] = x_board()
            self.assertEqual((boards["x"]["cube"], boards["x"]["may_double"]), (2, [0, 1]))
            loser, winner, lname, wname = x, w, "xavier", "wilma"
        else:
            w.wait("It's your turn. Please roll or double")
            w.send("double")
            w.wait("You double. Please wait for xavier")
            x.wait(lambda l: l == "wilma doubles. Type 'accept' or 'reject'.")
            x.send("accept")
            x.wait(lambda l: l == "You accept the double. The cube shows 2.")
            w.wait("xavier accepts the double. The cube shows 2.")
            loser, winner, lname, wname = w, x, "wilma", "xavier"

        def wait_for(c, text):
            return c.wait(text) if c is w else c.wait(lambda l: l.startswith(text))
        # the doubler resigns a gammon: rejected; then a single game: accepted (2 points with the cube)
        loser.send("resign g")
        wait_for(winner, "%s wants to resign. You will win 4 points. Type 'accept' or 'reject'." % lname)
        winner.send("reject")
        wait_for(winner, "You reject. The game continues.")
        wait_for(loser, "%s rejects. The game continues." % wname)
        loser.send("resign n")
        wait_for(winner, "%s wants to resign. You will win 2 points." % lname)
        winner.send("accept")
        wait_for(winner, "You accept and win 2 points.")
        wait_for(winner, "You win the game and get 2 points. Congratulations!")
        wait_for(loser, "%s wins the game and gets 2 points. Sorry." % wname)
        score = wait_for(winner, "score in 3 point match:")
        self.assertEqual(score, "score in 3 point match: %s-2 %s-0" % (wname, lname))
        for c in (w, x):
            wait_for(c, "Type 'join' if you want to play the next game, type 'leave' if you don't.")
            c.send("join")
        # game 2 is the Crawford game: the leader's opponent can't double; the trailer resigns it
        for c in (w, x):
            wait_for(c, "** This is the Crawford game")
            wait_for(c, "Starting a new game with")
        b = (w_board if winner is w else x_board)()
        self.assertEqual(b["score"], (2, 0))
        self.assertEqual(b["may_double"], [0, 0])
        loser.send("resign n")
        wait_for(winner, "%s wants to resign. You will win 1 point." % lname)
        winner.send("accept")
        wait_for(winner, "You win the 3 point match 3-0 .")
        self.assertEqual(wait_for(loser, "%s wins the 3 point match" % wname),
                         "%s wins the 3 point match 3-0 ." % wname)
        # every burst ended with a prompt for xfibs: nothing is left unparsed
        x.send("who")
        self.assertTrue(any(l.startswith("No  S  username") for l in x.burst()))

    def test_clip_login_numbered_and_chat(self):
        pw = self.password_for("clio")
        c = self.client(Clip)
        c.until_raw("login: ")
        c.send("login TestClient_v1 1008 clio %s" % pw)
        first = c.wait(lambda l: l.startswith("1 "))
        self.assertTrue(re.match(r"^1 clio \d+ \S+$", first), first)
        own = c.wait(lambda l: l.startswith("2 "))
        self.assertEqual(len(own.split()), 22)
        c.wait(lambda l: l == "3")
        c.wait(lambda l: l == "4")
        me = c.wait(lambda l: l.startswith("5 clio "))
        self.assertEqual(len(me.split()), 13)
        c.wait(lambda l: l == "6")
        self.assertNotIn(b"> ", c.buf)
        mac = self.client(MacFIBS)
        mac.login("mack", self.password_for("mack"))
        c.wait(lambda l: l == "7 mack mack logs in.")
        mac.send("tell clio hello there")
        self.assertEqual(c.wait(lambda l: l.startswith("12 ")), "12 mack hello there")
        mac.wait(lambda l: l == "** You tell clio: hello there")
        c.send("tell mack hi back")
        c.wait(lambda l: l == "16 mack hi back")
        self.assertEqual(MacFIBS.classify(mac.wait(lambda l: "hi back" in l)), "chat")
        c.send("shout anyone here?")
        c.wait(lambda l: l == "17 anyone here?")
        mac.wait(lambda l: l == "clio shouts: anyone here?")
        c.send("rawwho")
        rows = []
        while True:
            l = c.wait(lambda l: l[:2] in ("5 ", "6"))
            if l == "6":
                break
            rows.append(l.split()[1])
        self.assertIn("mack", rows)
        mac.send("bye")
        c.wait(lambda l: l == "8 mack mack logs out.")


    def test_join_says_the_client(self):
        """When a match starts, a line of its own after FIBS's join line says what the opponent plays with,
        where their program said; a later change of program is told too (docs/protocol.md, CLIENT)."""
        x = self.client(XFibs)
        x.login("xoan", self.password_for("xoan"))
        pw = self.password_for("cleo")
        c = self.client(Clip)
        c.until_raw("login: ")
        c.send("login TestClient_v1 1008 cleo %s" % pw)
        c.wait(lambda l: l == "6")
        x.send("invite cleo 1")
        c.wait(lambda l: l.startswith("xoan wants"))
        c.send("join xoan")
        x.wait(lambda l: l.startswith("** Player cleo has joined you for a 1 point match."))
        self.assertEqual(x.wait(lambda l: l.startswith("** cleo ")), "** cleo joined with TestClient v1.")
        c.wait(lambda l: l.startswith("** You are now playing a 1 point match with xoan"))
        other = Native(self.core, "cleo", pw)
        self.addCleanup(other.close)
        tid = next(t for t in other.req("LST mine") if t.startswith("TAB ")).split()[1]
        other.req("CLI PROG Other Program 2.0")
        other.req("CHT %s hello" % tid)
        self.assertEqual(x.wait(lambda l: l.startswith("** cleo ")), "** cleo now plays with Other Program 2.0.")
        self.assertFalse(any(l.startswith("** xoan") for l in c.seen), "xoan's program said nothing")


@slow
class FibsNoAuth(Stack):
    """--auth none: a name alone signs in, the password is ignored."""
    auth = "none"

    def test_name_alone_and_web_join(self):
        mac = self.client(MacFIBS)
        mac.login("nora", "whatever")
        self.assertFalse(any("token" in l.lower() for l in mac.seen))
        x = self.client(XFibs)
        x.login("ned", "")
        self.assertTrue(any(l == "** User ned authenticated." for l in x.queue))
        self.assertFalse(any("token" in l.lower() for l in x.queue))
        # a web player waits at a public table; the xfibs user joins it by name (seat 1: X)
        self.web_login("wren")
        _, loc, _ = self.web_get("/new.htm?g=backgammon%2F1&n=wren")
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        x.send("join wren")
        x.wait(lambda l: l.startswith("** You are now playing an unlimited match with wren"))
        x.wait(lambda l: l.startswith("board:"))
        x.send("board")
        b = parse_board(x.wait(lambda l: l.startswith("board:")))
        self.assertEqual((b["player"], b["opp"], b["match"]), ("You", "wren", 9999))
        self.assertEqual(sum(v for v in b["pts"] if v > 0), 15)
        native = Native(self.core, "ned", "x")
        self.addCleanup(native.close)
        wren = Native(self.core, "wren", "x")
        self.addCleanup(wren.close)
        # three turns each way; xfibs moves "move bar-22 13-11" in the server's numbering
        moved, fresh = 0, True          # the board just asked for may already be our turn
        end = time.time() + 90
        while moved < 3:
            self.assertLess(time.time(), end, x.seen[-20:])
            self.web_act(tid, "wren", avoid=("double",))
            try:
                lines = x.burst(0.4)
            except AssertionError:
                lines = []
            for l in lines:
                if l.startswith("It's your turn. Please roll or double"):
                    x.send("roll")
                if l.startswith("board:"):
                    b, fresh = parse_board(l), True
            if b and fresh and b["mine"] and b["dice"] != [0, 0] and b["can_move"]:
                fresh = False
                check_against_view(self, b, native.view(tid), 1)
                plan = plan_move(b)
                x.send("move " + " ".join("%s-%s" % (server_point(b, f), server_point(b, t)) for f, t in plan))
                moved += 1
        # chat both ways through the table
        x.send("say good game")
        x.wait(lambda l: l == "** You say wren: good game")
        self.assertTrue(any(l.endswith("CHT 1 good game") for l in wren.req("SIN %s 0" % tid)))
        wren.req("CHT %s thanks ned" % tid)
        x.wait(lambda l: l == "wren says: thanks ned")
        # a MacFIBS user watches ned (the table is public)
        mac.send("watch ned")
        self.assertTrue(mac.wait(lambda l: l.startswith("You're now watching")) == "You're now watching ned.")
        mac.send("board")
        mac.pump(0.8)
        self.assertEqual((mac.board["player"], mac.board["opp"], mac.board["colour"]), ("ned", "wren", -1))
        mac.send("unwatch")
        mac.wait(lambda l: l == "You stop watching ned.")
        # ned drops the line and comes back: the running match is loaded again
        x.close()
        time.sleep(0.5)
        y = self.client(XFibs)
        y.login("ned", "")
        y.wait(lambda l: l == "Type show saved to list your unfinished games.")   # how to find unfinished games
        self.assertEqual(MacFIBS.classify(y.wait(lambda l: l.startswith("You are now playing with"))), "resumed")
        b = parse_board(y.wait(lambda l: l.startswith("board:")))
        check_against_view(self, b, native.view(tid), 1)
        y.send("show saved")                                            # the game, with its table number
        y.wait(lambda l: l.strip() == "ned - wren (table %s)" % tid or l.strip() == "wren - ned (table %s)" % tid)
        y.send("join %s" % tid)                                         # a table number is understood
        y.wait(lambda l: l == "** You are already playing.")
        # ned offers to resign; the web page shows Accept and Reject, and wren accepts
        y.send("resign n")
        y.wait(lambda l: l == "You want to resign. wren will win 1 point.")
        body = self.web_get("/table.htm?t=%s&n=wren&r=0" % tid)[2]
        self.assertIn("mv=reject", body)
        self.assertIn("accept", self.web_act(tid, "wren"))
        y.wait(lambda l: l == "wren accepts and wins 1 point.")
        y.wait(lambda l: l == "wren wins the game and gets 1 point. Sorry.")
        self.assertEqual(parse_board(y.wait(lambda l: l.startswith("board:")))["turn"], 0)



class FibsNeverVolunteers(unittest.TestCase):
    """The FIBS password goes to the core only as the answer to a credential
    question (asserted on the lines the core receives)."""

    def test_none_drops_it_password_answers_it(self):
        from test_ios import RecordingCore
        from parlord import auth
        for signin, name, want in (("none", "ned", []), ("password", "pia", ["AUT password pia-Pass-word"])):
            core = RecordingCore(signin, None, ("fibs",))
            self.addCleanup(core.stop)          # also when the extension does not start (stop twice is fine)
            if signin == "password":
                p = core.srv.accounts.player(name, create=True)
                p.cred = auth.hash_password("pia-Pass-word")
                core.srv.accounts.save_player(p)
            port, proc = core.adapter("adapters.fibs", "fibs")
            try:
                mac = MacFIBS(port)
                mac.login(name, "pia-Pass-word")
                mac.close()
                # a login signs in only (USER); under sign-in none the name is the whole sign-in (HELO)
                first = "%s %s" % ("HELO" if signin == "none" else "USER", name)
                deadline = time.time() + 10
                while not (first in core.lines and all(w in core.lines for w in want)) and time.time() < deadline:
                    time.sleep(0.05)
                self.assertIn(first, core.lines)
                self.assertEqual([l for l in core.lines if "pia-Pass-word" in l], want, signin)
            finally:
                procs.stop(proc)
                core.stop()


if __name__ == "__main__":
    unittest.main()
