"""IOS adapter tests. The Eclipse checks read the output the way Reversi The
Eclipse 1.2 does:
LF-split lines cut at the first CR, the login phase ending on the first
<ios>, prompt removal, fixed columns and sscanf fields. Clients send LF CR."""
import os
import re
import socket
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from test_adapters import Browser, spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args
import json
from speed import slow

DIRS = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


def scanf(s, fmt):
    """A small sscanf for %Nld, %Ns, %c and %f, each skipping leading spaces."""
    out, i = [], 0
    for spec in fmt.split():
        m = re.match(r"%(\d*)(ld|s|c|f)$", spec)
        width = int(m.group(1)) if m.group(1) else None
        kind = m.group(2)
        while i < len(s) and s[i] == " ":
            i += 1
        if kind == "c":
            out.append(s[i])
            i += 1
            continue
        lim = len(s) if width is None else min(len(s), i + width)
        j = i
        if kind == "s":
            while j < lim and s[j] != " ":
                j += 1
            out.append(s[i:j])
        else:
            num = re.compile(r"[+-]?\d+" if kind == "ld" else r"[+-]?\d*\.?\d*").match(s, i, lim).group()
            j = i + len(num)
            out.append(int(num) if kind == "ld" else float(num))
        i = j
    return out


def parse_pb(line):
    """Section 5.4: sscanf after '<ios> PB ', 64 cells from column 78."""
    assert line.startswith("<ios> PB "), line
    f = scanf(line[9:], "%3ld %8s %8s %4ld %4ld %3ld %3ld %3ld %3ld %c %c %2ld %2ld %f %4ld")
    cells = line[78:142]
    assert len(cells) == 64 and set(cells) <= set("*O-"), line
    return {"game": f[0], "black": f[1], "white": f[2], "tb": f[3], "tw": f[4], "colour": f[9],
            "turn": f[10], "move_no": f[11], "move": f[12], "cells": cells,
            "rows": [cells[k:k + 8] for k in range(0, 64, 8)]}


def parse_game(line):
    """Section 5.3: <ios> [GAME n (black vs. white) (x) created|ended."""
    if not line.startswith("<ios> [GAME "):
        return None
    n = int(re.match(r"\d+", line[12:]).group())
    i = line.index("(")
    black = line[i + 1:line.index(" ", i)][:9]
    j = line.index(" vs. ", i) + 5
    white = line[j:line.index(")", j)][:9]
    second = line.index(")", line.index(")") + 1)
    return {"n": n, "black": black, "white": white, "ended": line[second + 2:].startswith("ended")}


def parse_presence(line):
    """Section 5.2 -> (name, True) for connected, (name, False) for disconnected."""
    if not line.startswith("<ios> [") or line.startswith("<ios> [GAME"):
        return None
    body = line[7:]
    if ")" in body and body[body.index(")") + 1:].startswith(" connected"):
        return body[:body.index("(")], True
    if " " in body and body[body.index(" "):].startswith(" disconnected"):
        return body[:body.index(" ")], False
    return None


def legal(cells, me):
    opp = "O" if me == "*" else "*"
    out = []
    for y in range(8):
        for x in range(8):
            if cells[y * 8 + x] != "-":
                continue
            for dx, dy in DIRS:
                cx, cy, run = x + dx, y + dy, 0
                while 0 <= cx < 8 and 0 <= cy < 8 and cells[cy * 8 + cx] == opp:
                    cx, cy, run = cx + dx, cy + dy, run + 1
                if run and 0 <= cx < 8 and 0 <= cy < 8 and cells[cy * 8 + cx] == me:
                    out.append("ABCDEFGH"[x] + str(y + 1))
                    break
    return out


def board_from(text):
    """Parse the last <ios> BOARD block (the human form) by its columns."""
    lines = [re.sub(r"^([A-Za-z0-9_-]+% )+", "", l) for l in text.split("\n")]     # prompts
    i = max(k for k, l in enumerate(lines) if l.startswith("<ios> BOARD"))
    head, block = lines[i], lines[i + 1:i + 12]
    assert head[25] == "(", head
    rows = []
    for l in block[2:10]:
        l = l.ljust(80)
        rows.append("".join(l[c] for c in range(31, 46, 2)))
    row2 = block[3].ljust(80)
    assert re.match(r"\d\d:\d\d:\d\d", row2[11:19]) and re.match(r"\d\d:\d\d:\d\d", row2[59:67])
    last = block[10].ljust(80)
    return {"rows": rows, "colour": block[1][12], "my_turn": last[0] == "Y", "last": last[57:59].strip()}


class Eclipse:
    """A client that reads like Reversi The Eclipse 1.2 and sends LF CR."""

    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = b""
        self.name = None
        self.seen = []

    def send(self, text):
        self.s.sendall(text.encode() + b"\n\r")

    def _recv(self, end):
        self.s.settimeout(max(0.05, min(0.5, end - time.time())))
        try:
            d = self.s.recv(4096)
        except socket.timeout:
            return True
        if not d:
            return False
        self.buf += d
        return True

    def login(self, name, password="", typed=None):
        """Section 2: match login:, password: and <ios> in the byte stream and
        answer each prompt; returns (logged in, prompts answered, text). typed
        is what the user types in the window when the server prompts with a
        sign-in label (like "New password: ")."""
        self.name = name
        asked = {"login:": 0, "password:": 0}
        text = b""
        end = time.time() + 10
        while True:
            while not self.buf:
                if time.time() > end:
                    raise AssertionError("login phase did not end: %r" % text[-300:])
                if not self._recv(end):
                    return False, asked, text.decode()
            b, self.buf = self.buf[:1], self.buf[1:]
            text += b
            if text.endswith(b"login:"):
                asked["login:"] += 1
                self.send(name)
            elif text.endswith(b"password:"):
                asked["password:"] += 1
                self.send(password)
            elif typed is not None and asked["password:"] and re.search(rb"\r\n[^\n<]+: $", text) \
                    and not text.endswith((b"login: ", b"password: ")) and self.quiet_now():
                asked["typed"] = asked.get("typed", 0) + 1       # a sign-in step's prompt in the window
                if isinstance(typed, list) and not typed:
                    raise AssertionError("one prompt too many: %r" % text[-400:])
                answer = typed.pop(0) if isinstance(typed, list) else typed
                self.send(answer() if callable(answer) else answer)
            elif text.endswith(b"<ios>"):
                break
        self.send("set trust 1")
        self.send("who")
        return True, asked, text.decode()

    def quiet_now(self, secs=0.3):
        """True if nothing more arrives for a moment: the text so far ends in a prompt."""
        if self.buf:
            return False
        self._recv(time.time() + secs)
        return not self.buf

    def raw_line(self, timeout=8):
        """Up to LF, cut at the first CR (section 1)."""
        end = time.time() + timeout
        while b"\n" not in self.buf:
            if time.time() > end:
                raise AssertionError("no line from the server; buffer %r" % self.buf[-300:])
            if not self._recv(end):
                raise EOFError
        raw, self.buf = self.buf.split(b"\n", 1)
        return raw.split(b"\r")[0].decode()

    def line(self, timeout=8):
        """A line after prompt removal (section 4)."""
        l = self.raw_line(timeout)
        while l.startswith(self.name + "%"):
            l = l[len(self.name) + 2:]
        self.seen.append(l)
        return l

    def wait(self, pred, timeout=10):
        end = time.time() + timeout
        while True:
            l = self.line(max(0.1, end - time.time()))
            if pred(l):
                return l

    def who(self):
        """Section 5.1: header, one line thrown away, rows until '... players on'."""
        self.wait(lambda l: l.startswith("login    blitz  stand"))
        self.raw_line()
        rows = {}
        while True:
            r = self.raw_line()
            assert " " in r, "a who row without a space: %r" % r
            if r.split(" ", 1)[1].startswith("players on"):
                return rows, r
            rows[r[0:8].strip()] = r[23:26]

    def pb(self, pred=lambda p: True, timeout=10):
        return parse_pb(self.wait(lambda l: l.startswith("<ios> PB") and pred(parse_pb(l)), timeout))

    def game(self, ended, timeout=10):
        return parse_game(self.wait(lambda l: (parse_game(l) or {}).get("ended") == ended, timeout))

    def presence(self, name, on, timeout=12):
        return self.wait(lambda l: parse_presence(l) == (name, on), timeout)

    def quiet(self, secs=0.6):
        """Everything that arrives within secs, as processed lines."""
        out = []
        end = time.time() + secs
        while time.time() < end:
            try:
                out.append(self.line(max(0.05, end - time.time())))
            except AssertionError:
                break
        return out

    def close(self):
        try:
            self.send("quit")
        except OSError:
            pass
        self.s.close()


def logged_out(port, name, timeout=10):
    """Wait until the IOS service no longer has name logged in (a client that quit is gone only once the
    service has seen its connection close). Each look sends the name at login: and hangs up at password:,
    before anything reaches the core."""
    end = time.time() + timeout
    while True:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            text = b""
            while b"login:" not in text:
                d = s.recv(4096)
                if not d:
                    raise AssertionError("closed before login: %r" % text[-200:])
                text += d
            s.sendall(name.encode() + b"\n\r")
            text = b""
            while b"password:" not in text and b"logged in already" not in text:
                d = s.recv(4096)
                if not d:
                    break
                text += d
        finally:
            s.close()
        if b"password:" in text:
            return
        if time.time() > end:
            raise AssertionError("%s still logged in to the IOS service: %r" % (name, text[-200:]))
        time.sleep(0.05)


class IosClient:
    """A terminal user: plain lines, LF ends."""

    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = b""

    def until(self, text, timeout=8):
        end = time.time() + timeout
        while text.encode() not in self.buf and time.time() < end:
            try:
                d = self.s.recv(4096)
            except socket.timeout:
                continue
            if not d:
                break
            self.buf += d
        if text.encode() not in self.buf:
            raise AssertionError("%r not seen; got %r" % (text, self.buf[-600:]))
        i = self.buf.index(text.encode()) + len(text)
        out, self.buf = self.buf[:i].decode(), self.buf[i:]
        return out

    def send(self, line):
        self.s.sendall((line + "\n").encode())

    def login(self, name, password=None):
        self.until("login: ")
        self.send(name)
        if password:
            self.send(password)          # the reference client sends it unprompted
        self.until("password: ")
        return self.until("%s%% " % name)


@slow
class Ios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.ios, cls.web = free_port(), free_port(), free_port(), free_port()
        cls.keys = make_keys("ios", "web")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "none"),     # the games; sign-in below
                     spawn(*adapter_args("adapters.ios", cls.ios, cls.fe, cls.keys, "ios"), "--prompt-wait", "2"),
                     spawn(*adapter_args("adapters.web", cls.web, cls.fe, cls.keys, "web"))]

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
            p.stdout.close()
            p.stderr.close()

    def web_table(self, name):
        """A web player who opens a Reversi table and waits; returns (browser, table id)."""
        b = Browser(self.web)
        b.login(name, name + "-Pass-word")
        loc = b.act("/new.htm?g=reversi%2F1")[1]
        return b, parse_qs(urlsplit(loc).query)["t"][0]

    def eclipse(self, name, password=""):
        c = Eclipse(self.ios)
        ok, asked, _ = c.login(name, password)
        self.assertTrue(ok)
        self.assertEqual(asked, {"login:": 1, "password:": 1})
        self.addCleanup(c.close)
        return c

    def test_eclipse_login_who_and_presence(self):
        self.web_table("steve")
        c = Eclipse(self.ios)
        ok, asked, text = c.login("ecl")
        self.addCleanup(c.close)
        self.assertTrue(ok)
        self.assertEqual(asked, {"login:": 1, "password:": 1})
        self.assertTrue(text.endswith("<ios>"))
        self.assertEqual(c.raw_line(), " [ecl(-) connected")    # the rest of the first <ios> line
        rows, end = c.who()                                      # sent by the client after <ios>
        self.assertEqual(rows.get("steve"), "---")               # a web player waiting at a table
        self.assertEqual(rows.get("ecl"), "---")
        self.assertEqual(end, "%d players online" % len(rows))
        # players appear and leave (online: signed in and active, docs/accounts.md): a web player who
        # signs in and makes a table, then signs out; and another IOS login
        b, tid = self.web_table("wendy")
        c.presence("wendy", True)
        b.act("/out.htm")
        c.presence("wendy", False)
        d = self.eclipse("ecl2")
        c.presence("ecl2", True)
        d.close()
        c.presence("ecl2", False)
        # finger: plain lines; the placeholder row is ignored
        c.send("fi --------")
        c.send("fi steve")
        l = c.wait(lambda l: l.strip() != "")
        self.assertTrue(l.startswith("steve "), l)
        self.assertFalse(any(x.startswith("login:") for x in c.quiet(0.3)))

    def test_eclipse_match_board_observe_tell(self):
        amy, bo, cat = self.eclipse("amy"), self.eclipse("bo"), self.eclipse("cat")
        for c in (amy, bo, cat):
            c.who()
        amy.send("match  20 0 120 bo 20 0 120 b")    # Eclipse's word order; b: amy plays black
        m = bo.wait(lambda l: l.startswith("<ios> MATCH"))
        self.assertNotIn(m[m.index("MATCH") + 7], "sr")      # unrated
        f = scanf(m[m.index(")") + 1:], "%ld %ld %ld %s %s %ld %ld %ld %s")
        self.assertEqual((f[0], f[3], f[4], f[5], f[8]), (20, "FROM", "amy", 20, "black"))
        second = bo.raw_line()                               # read at once, no prompt removal
        self.assertRegex(second[15:], r"^-?\d+ \.\. -?\d+ -?\d+ -?\d+ \.\. -?\d+")
        bo.send("accept amy")
        games = [c.game(False) for c in (amy, bo, cat)]
        self.assertTrue(all(g == games[0] for g in games))
        g = games[0]
        self.assertEqual((g["black"], g["white"]), ("amy", "bo"))
        n = g["n"]
        self.assertLessEqual(n, 999)
        pa, pb = amy.pb(), bo.pb()
        for p in (pa, pb):
            self.assertEqual((p["game"], p["black"], p["white"], p["colour"], p["move"]), (n, "amy", "bo", "*", -1))
            self.assertEqual(p["rows"][3:5], ["---O*---", "---*O---"])
        self.assertEqual((pa["turn"], pb["turn"]), ("Y", "N"))
        cat.send("who")
        rows, _ = cat.who()
        self.assertEqual((rows["amy"], rows["bo"]), ("%3d" % n, "%3d" % n))
        cat.send("ob %d" % n)
        self.assertEqual(cat.pb()["turn"], "N")
        cat.send("board %d" % n)
        self.assertEqual(cat.pb()["game"], n)
        amy.send("PM A1 +0.00     0.50")
        self.assertIn("Illegal", amy.wait(lambda l: "Illegal" in l))
        amy.send("PM D3 +0.00     1.50")
        mine = amy.pb(lambda p: p["move"] == 34)
        self.assertEqual((mine["turn"], mine["colour"], mine["rows"][2]), ("N", "O", "---*----"))
        theirs = bo.pb(lambda p: p["move"] == 34)
        self.assertEqual((theirs["turn"], theirs["rows"]), ("Y", mine["rows"]))
        self.assertEqual(cat.pb(lambda p: p["move"] == 34)["turn"], "N")
        bo.send("tell amy good luck")
        self.assertEqual(amy.wait(lambda l: "good luck" in l), "bo: good luck")
        cat.send("ob %d" % n)
        cat.wait(lambda l: "stop observing" in l)
        amy.send("resign")
        for c in (amy, bo, cat):
            self.assertEqual(c.game(True)["n"], n)
        amy.send("resign")                                   # a closed window: a quiet reply
        amy.send("fi amy")
        self.assertTrue(amy.wait(lambda l: l.strip() != "" and not l.startswith("Game ")).startswith("amy "))

    def test_play_to_end_terminal_match_and_text_board(self):
        pia, quin = self.eclipse("pia"), self.eclipse("quin")
        pia.who()
        quin.who()
        pia.send("set board 1")
        pia.send("match quin 5 0 0 white")                  # the terminal form
        m = quin.wait(lambda l: l.startswith("<ios> MATCH"))
        f = scanf(m[m.index(")") + 1:], "%ld %ld %ld %s %s %ld %ld %ld %s")
        self.assertEqual((f[0], f[3], f[4], f[8]), (5, "FROM", "pia", "white"))
        quin.send("accept pia")
        players = {"*": quin, "O": pia}
        cur = quin.pb()
        self.assertEqual((cur["black"], cur["white"], cur["turn"]), ("quin", "pia", "Y"))
        head = pia.wait(lambda l: l.startswith("<ios> BOARD"))     # set board 1: the text board too
        self.assertEqual(board_from("\n".join([head] + [pia.line() for _ in range(11)]))["rows"][3], "---O*---")
        for _ in range(70):
            colour = cur["colour"]
            moves = legal(cur["cells"], colour)
            self.assertTrue(moves, cur)
            mover = players[colour]
            mover.send("PM %s +0.00     1.50" % moves[0])
            cur = mover.pb(lambda p, k=cur["move_no"] + 1: p["move_no"] == k)
            if not legal(cur["cells"], "*") and not legal(cur["cells"], "O"):
                break
            self.assertEqual(cur["turn"], "Y" if cur["colour"] == colour else "N")
        for c in (pia, quin):
            self.assertEqual(c.game(True)["black"], "quin")
        pia.send("board")
        pia.wait(lambda l: "not playing" in l)

    def test_ios_joins_a_web_table_and_resigns(self):
        self.web_table("webby")
        c = IosClient(self.ios)
        c.login("ike")
        c.send("who")
        self.assertIn("webby", c.until("players online"))
        c.send("match webby")
        c.until("(webby vs. ike) (unrated) created")
        c.send("board")
        text = c.until("<ios> BOARD")
        bd = board_from(text + c.until("ike% "))
        self.assertEqual(bd["rows"][3], "---O*---")
        self.assertFalse(bd["my_turn"])
        c.send("resign")
        c.until("(webby vs. ike) (unrated) ended")

    def test_join_says_the_client(self):
        """When the game starts, a line after [GAME n ...] created says what the opponent plays with
        (docs/protocol.md, CLIENT)."""
        n = socket.create_connection(("127.0.0.1", self.core), timeout=5)
        self.addCleanup(n.close)
        f = n.makefile("rb")
        n.sendall(b"PARLORNET/1\r\n")
        f.readline()

        def req(line):
            n.sendall(line.encode() + b"\r\n")
            while True:
                r = f.readline().decode().strip()
                if r.startswith(("OK", "ERR")):
                    return r
        for line in ("CLI PROG Netscape Navigator 3.0", "CLI MACH Power Macintosh", "HELO nettie"):
            req(line)
        req("JON %s" % req("NEW reversi standard").split()[1])
        c = IosClient(self.ios)
        c.login("ivo")
        c.send("match nettie")
        c.until("(nettie vs. ivo) (unrated) created")
        text = c.until("<ios> PB")          # after the announcement, before the board
        self.assertIn("nettie plays with Netscape Navigator 3.0 on a Power Macintosh.\r\n", text)
        c.send("resign")
        c.until("(nettie vs. ivo) (unrated) ended")

    def test_parse_match_forms(self):
        from adapters.ios import parse_match, pb_line, sq_code
        self.assertEqual(parse_match("20 0 120 steve 20 0 120".split()), ("steve", 20, None))
        self.assertEqual(parse_match("bronstein 20 0 120 steve 15 0 120 w".split()), ("steve", 15, "white"))
        self.assertEqual(parse_match("20 0 120 steve 20 0 120 b".split()), ("steve", 20, "black"))
        self.assertEqual(parse_match(["bo"]), ("bo", None, None))
        self.assertEqual(parse_match("bo 30 0 0 black".split()), ("bo", 30, "black"))
        self.assertEqual(sq_code("d3"), 34)
        line = pb_line(12, "eric", "steve", ["." * 8] * 3 + ["...10..."] * 1 + ["...01..."] + ["." * 8] * 3,
                       0, True, 0, -1)
        self.assertEqual(line[:78], "<ios> PB  12 eric     steve       0    0   0   0   0   0 * Y  0 -1"
                                    "   0.00    0")
        self.assertEqual(len(line), 142)


class IosPassword(unittest.TestCase):
    """Sign-in and sign-up password (the container's default): the IOS password
    answers the server's credential question; the rest are prompts in the
    client's window."""

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.ios, cls.web = free_port(), free_port(), free_port(), free_port()
        cls.keys = make_keys("ios", "web")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys)),
                     spawn(*adapter_args("adapters.ios", cls.ios, cls.fe, cls.keys, "ios"), "--prompt-wait", "2"),
                     spawn(*adapter_args("adapters.web", cls.web, cls.fe, cls.keys, "web"))]

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
            p.stdout.close()
            p.stderr.close()

    def test_sign_up_in_the_window_then_sign_in(self):
        """A new name: the IOS password is the password chosen; "Type it again"
        is a prompt in the window; then the lobby. Mac line endings (LF CR)."""
        c = Eclipse(self.ios)
        ok, asked, text = c.login("mac1", "mac1-Pass-word", typed=["mac1-Pass-word"])
        self.assertTrue(ok, text)
        self.assertEqual(asked, {"login:": 1, "password:": 1, "typed": 1})
        self.assertIn("mac1 is new here. To sign up, choose a password.", text)
        self.assertIn("\r\nType it again: ", text)
        self.assertNotIn("Choose a password: ", text)          # answered by the password field
        self.assertNotIn("token", text.lower())
        self.assertEqual(c.raw_line(), " [mac1(-) connected")
        c.close()
        logged_out(self.ios, "mac1")
        d = Eclipse(self.ios)                                  # back: the password field, nothing asked
        ok, asked, text = d.login("mac1", "mac1-Pass-word")
        self.assertTrue(ok, text)
        self.assertEqual(asked, {"login:": 1, "password:": 1})
        d.close()
        logged_out(self.ios, "mac1")
        e = Eclipse(self.ios)                                  # a wrong password: the reason, then closed
        ok, asked, text = e.login("mac1", "wrongpw")
        self.assertFalse(ok)
        self.assertIn("Wrong password", text)
        self.assertNotIn("wait=", text)

    def test_password_typed_at_the_prompt_and_refusals(self):
        Browser(self.web).login("tyra", "tyra-Pass-word")
        c = Eclipse(self.ios)                                  # no password in the login: the server's label
        ok, asked, text = c.login("tyra", "", typed="tyra-Pass-word")
        self.assertTrue(ok, text)
        self.assertEqual(asked, {"login:": 1, "password:": 1, "typed": 1})
        self.assertIn("\r\nPassword: ", text)
        c.close()
        logged_out(self.ios, "tyra")                           # else: "tyra is logged in already"
        for name, password, reason in (("waytoolongname", "", "Names are"),
                                       ("tyra", "", "No answer came to: Password.")):    # prompted, nothing typed
            c = Eclipse(self.ios)
            ok, asked, text = c.login(name, password)
            self.assertFalse(ok)
            self.assertEqual(asked["login:"], 1)
            self.assertIn(reason, text)
            c.s.close()


class RecordingCore:
    """A real core (parlord.core.Server) behind the frontend channel, in a
    thread, that keeps every line a frontend sends it."""

    def __init__(self, signin, signup=None, names=("ios",)):
        import asyncio
        import threading
        from parlord import frontends
        from parlord.core import Server
        self.lines = []
        lines = self.lines

        class Recording(Server):
            async def dispatch(self, sess, line):
                lines.append(line)
                return await super().dispatch(sess, line)
        self.srv = Recording(os.path.join(ROOT, "plugins"), "rec", ":memory:", signin, None, signup)
        self.srv.settings["email.transport"] = "log"
        self.keys = make_keys(*names)
        self.srv.keyring = frontends.Keyring(self.keys)
        self.port = free_port()
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def start():
            await self.srv.load_plugins()
            self.server = await asyncio.start_server(self.srv.handle_frontend, "127.0.0.1", self.port)
            ready.set()
        threading.Thread(target=lambda: (self.loop.run_until_complete(start()), self.loop.run_forever()),
                         daemon=True).start()
        ready.wait(10)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        for p in self.srv.plugins.values():
            p.stop()

    def adapter(self, module, name):
        port = free_port()
        return port, spawn(module, "--port", str(port), "--core", "127.0.0.1:%d" % self.port,
                           "--key", os.path.join(self.keys, name + ".key"), "--prompt-wait", "2")


@slow
class IosNeverVolunteers(unittest.TestCase):
    """The IOS password goes to the core only as the answer to a credential
    question: never on HELO, never under none, never for an email sign-up's
    other steps. Asserted on the lines the core receives."""

    def run_ios(self, signin, signup, name, password, typed=None, prepare=None):
        core = RecordingCore(signin, signup)
        if prepare:
            prepare(core.srv)
        port, proc = core.adapter("adapters.ios", "ios")
        try:
            c = Eclipse(port)
            ok, asked, text = c.login(name, password, typed=typed(core) if callable(typed) else typed)
            if ok:
                c.raw_line()
            c.close()
            time.sleep(0.3)
            return ok, asked, text, list(core.lines)
        finally:
            proc.kill()
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()
            core.stop()

    def test_none_drops_the_password(self):
        ok, asked, text, lines = self.run_ios("none", None, "nan", "secret-pw-1")
        self.assertTrue(ok, text)
        self.assertIn("HELO nan", lines)
        self.assertFalse(any("secret-pw-1" in l for l in lines), lines)

    def test_password_answers_the_credential_question_only(self):
        from parlord import auth

        def prepare(srv):
            p = srv.accounts.player("pia", create=True)
            p.cred = auth.hash_password("pia-Pass-word")
            srv.accounts.save_player(p)
        ok, asked, text, lines = self.run_ios("password", None, "pia", "pia-Pass-word", prepare=prepare)
        self.assertTrue(ok, text)
        self.assertIn("HELO pia", lines)
        self.assertIn("AUT password pia-Pass-word", lines)
        self.assertEqual([l for l in lines if "pia-Pass-word" in l], ["AUT password pia-Pass-word"])

    def test_two_questions_field_then_prompt(self):
        """Password sign-up asks two things: the field answers the first, the
        second is a prompt with the server's label."""
        ok, asked, text, lines = self.run_ios("password", None, "pat", "pat-Pass-word", typed=["typed-again", ""])
        self.assertFalse(ok)                                   # typed differently: the two do not match
        self.assertIn("AUT password pat-Pass-word", lines)
        self.assertIn("AUT confirm typed-again", lines)
        self.assertIn("Type it again: ", text)

    def test_email_sign_up_from_the_window(self):
        """Sign-up email with sign-in password, in the client's window: y, the address, the password from
        the mail, then a password of the player's own, typed twice. Nothing asks for the account's
        credential, so the IOS password field is never sent."""
        def typed(core):
            def mailed():
                return re.search(r"Password:\s+(\S+)", core.srv.auth.mailer.last["body"]).group(1)
            return ["y", "eve@example.org", mailed, "Eve-own-pass1", "Eve-own-pass1"]
        ok, asked, text, lines = self.run_ios("password", "email", "eve", "eve-Pass-word", typed=typed)
        self.assertTrue(ok, "not signed in")
        self.assertEqual(asked["typed"], 5)
        for want in ("eve is not signed up on this server.", "Sign up as eve? (y/n): ", "Email address: ",
                     "We emailed a password for eve to eve@example.org.", "Password from the email: ",
                     "That was a temporary password. Choose a password of your own now.",
                     "New password (8 or more characters): ", "Type it again: "):
            self.assertIn(want, text)
        self.assertEqual([l.split()[1] for l in lines if l.startswith("AUT")],
                         ["confirm", "email", "password", "password", "confirm"])
        self.assertFalse(any("eve-Pass-word" in l for l in lines), "the field was sent")


def read_mail(path, to, timeout=10):
    """The body of the newest mail to an address, from the file parlord's test transport writes
    (email.test_file). Never printed."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(path, encoding="utf-8") as f:
                mails = [json.loads(l) for l in f if l.strip()]
        except OSError:
            mails = []
        mine = [m for m in mails if m["to"] == to]
        if mine:
            return mine[-1]["body"]
        time.sleep(0.1)
    raise AssertionError("no mail to %s" % to)


def mailed_password(path, to):
    return re.search(r"Password:\s+(\S+)", read_mail(path, to)).group(1)


@slow
class IosEmail(unittest.TestCase):
    """--auth password --signup email, the live server's methods: a sign-up mails a password; the first
    sign-in with it from an IOS client asks for a password of the player's own in the client's window."""

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.ios = free_port(), free_port(), free_port()
        cls.keys = make_keys("ios")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mails = os.path.join(cls.tmp.name, "mails")
        settings = os.path.join(cls.tmp.name, "settings")
        with open(settings, "w") as f:
            f.write("email.transport=test\nemail.test_file=%s\n" % cls.mails)
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password", "--signup", "email",
                           "--settings-file", settings),
                     spawn(*adapter_args("adapters.ios", cls.ios, cls.fe, cls.keys, "ios"), "--prompt-wait", "2")]

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
            p.stdout.close()
            p.stderr.close()
        cls.tmp.cleanup()

    def signup(self, name, addr):
        """Sign up over the native protocol as web or telnet would; returns the mailed password."""
        s = socket.create_connection(("127.0.0.1", self.core), timeout=5)
        s.sendall(b"PARLORNET/1\r\n")       # the opening first, then the greeting
        f = s.makefile("rb")
        self.assertIn("AUTH=password SIGNUP=email", f.readline().decode())

        def req(text):
            s.sendall(text.encode() + b"\r\n")
            while True:
                l = f.readline().decode().strip()
                if l.startswith(("OK", "ERR", "ASK")):
                    return l
        self.assertEqual(req("REG %s" % name), "ASK email Email_address")
        self.assertEqual(req("AUT email %s" % addr), "ASK password Password_from_the_email secret")
        s.close()                # the account waits for its first sign-in
        return mailed_password(self.mails, addr)

    def test_first_sign_in_chooses_a_password_in_the_window(self):
        pw = self.signup("tia", "tia@example.org")
        c = Eclipse(self.ios)                  # the mailed password in the password field, then two prompts
        ok, asked, text = c.login("tia", pw, typed=["Tia-own-pass1", "Tia-own-pass1"])
        self.assertTrue(ok, "not signed in")
        self.assertEqual(asked, {"login:": 1, "password:": 1, "typed": 2})
        # (not "New password:": Eclipse would answer that with the password it keeps)
        for want in ("That was a temporary password. Choose a password of your own now.",
                     "New password (8 or more characters): ", "Type it again: "):
            self.assertIn(want, text)
        self.assertEqual(c.raw_line(), " [tia(-) connected")
        c.close()
        logged_out(self.ios, "tia")
        d = Eclipse(self.ios)                  # from then on, the password of their own
        ok, asked, text = d.login("tia", "Tia-own-pass1")
        self.assertTrue(ok, "not signed in")
        self.assertEqual(asked, {"login:": 1, "password:": 1})
        d.close()
        logged_out(self.ios, "tia")
        e = Eclipse(self.ios)                  # sign up from the client's window: y, address, then sign in
        ok, asked, text = e.login("sue", "", typed=["y", "sue@example.org",
                                                    lambda: mailed_password(self.mails, "sue@example.org"),
                                                    "Sue-own-pass1", "Sue-own-pass1"])
        self.assertTrue(ok, "not signed in")
        self.assertEqual(asked["typed"], 5)
        for want in ("sue is not signed up on this server.", "Sign up as sue? (y/n): ", "Email address: ",
                     "We emailed a password for sue to sue@example.org.", "Password from the email: "):
            self.assertIn(want, text)
        self.assertLess(text.index("Sign up as sue?"), text.index("Email address"))
        self.assertEqual(e.raw_line(), " [sue(-) connected")
        e.close()
        logged_out(self.ios, "sue")
        for name, password, reason in (
                ("newbie", "x", "newbie is not signed up on this server. Sign up as newbie? (y/n): "),
                ("tia", "", "No answer came to: Password."),
                ("tia", "Not-her-pass1", "Wrong password")):
            e = Eclipse(self.ios)
            ok, asked, text = e.login(name, password)
            self.assertFalse(ok, text)
            self.assertEqual(asked["login:"], 1)
            self.assertIn(reason, text.replace("\r\n", " "))
            self.assertNotIn("wait=", text)
            e.s.close()


class IosNone(unittest.TestCase):
    """--auth none: a name alone signs in and no token is shown."""

    def test_name_alone(self):
        core, fe, ios = free_port(), free_port(), free_port()
        keys = make_keys("ios")
        procs = [spawn(*stack_args(core, fe, keys), "--auth", "none"),
                 spawn(*adapter_args("adapters.ios", ios, fe, keys, "ios"))]
        try:
            c = Eclipse(ios)
            ok, asked, text = c.login("nan")
            self.assertTrue(ok, text)
            self.assertEqual(c.raw_line(), " [nan(-) connected")
            self.assertFalse(any("token" in l for l in c.quiet(0.5)))
            c.close()
        finally:
            for p in procs:
                p.kill()
                p.wait()
                p.stdout.close()
                p.stderr.close()


if __name__ == "__main__":
    unittest.main()
