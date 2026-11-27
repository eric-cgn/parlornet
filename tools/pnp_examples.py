#!/usr/bin/env python3
"""The sample sessions of docs/pnp.md, recorded from a real server.

    python3 tools/pnp_examples.py            write every sample block into docs/pnp.md
    python3 tools/pnp_examples.py NAME ...   only those blocks
    python3 tools/pnp_examples.py --list     the scenario names

Each sample block in docs/pnp.md is a fenced block whose first line is ```pnp NAME. For each NAME
this file has a scenario: how to start the server (a fresh parlord with a fixed random seed, an
in-memory database and the options below) and a script that plays the session. The script talks to
the server over the native protocol; every line sent and received is written into the block:

    A> PARLORNET/1                                    connection A's opening, the first thing it sends
    A< PARLORNET/1 parlornet AUTH=none SIGNUP=none      a line from the server to connection A
    A> HELO ann                                       a line from connection A to the server
    # text                                            a note, not part of the session

Values that differ from run to run are written as placeholders: <secs> (seconds left on a turn
clock), <sid> (a session id), <key> (a sign-in key), <mailed> (the temporary password in the latest mail),
<n> (a wait in seconds), <when> (the time of a turn), <date> (a day). tests/test_pnp_doc.py replays every block against a fresh server started the same way and
fails if a server line differs from the block (a placeholder matches any one word).

The server process seeds Python's random generator before it starts and draws the tables' seeds from
it (serve_seeded below), so the seeds, and with them the dice, tiles and random fleets, are the same
on every run.
"""
import asyncio
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "pnp.md")
TERMINATORS = ("OK", "ERR", "ASK", "CNT")     # CNT ends a page of a paged reply (PAG)
OPENING = "PARLORNET/1"         # what a public-port connection sends first
PLACEHOLDER_RE = re.compile(r"<([a-z][a-z0-9]*)>")
FENCE_RE = re.compile(r"^```pnp ([a-z0-9-]+)\s*$")


# ---- the server ---------------------------------------------------------------------------------

def serve_seeded(seed, argv):
    """Run parlord in this process with its table seeds drawn from Python's random generator, seeded. A
    real server draws them from the system's secure generator (parlord.core.table_seed)."""
    import random
    import runpy
    random.seed(seed)
    sys.path.insert(0, ROOT)
    from parlord import core
    core.table_seed = lambda: random.getrandbits(32)
    sys.argv = ["parlord"] + argv
    runpy.run_module("parlord", run_name="__main__", alter_sys=True)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    """A local parlord for one scenario: an in-memory database, the scenario's options and seed.
    Its output is collected, and with the test mail transport the mails it would send are kept in a file
of its own (mails.jsonl), from which the scripts take the temporary passwords; nothing prints them."""

    def __init__(self, scn):
        self.scn = scn
        self.dir = tempfile.TemporaryDirectory(prefix="pnp")
        self.port = free_port()
        args = ["--port", str(self.port), "--frontend-port", "0", "--rate", "1000", "--burst", "1000"]
        args += scn.get("args", [])
        if scn.get("motd"):
            path = os.path.join(self.dir.name, "motd.txt")
            with open(path, "w") as f:
                f.write(scn["motd"])
            args += ["--motd", path]
        if scn.get("mail"):
            path = os.path.join(self.dir.name, "settings.conf")
            self.mail_file = os.path.join(self.dir.name, "mails.jsonl")
            with open(path, "w") as f:                  # keep each mail in a file instead of sending it
                f.write("email.transport=test\nemail.test_file=%s\n" % self.mail_file)
            args += ["--settings-file", path]
        self.keys = None
        if scn.get("ext"):
            sys.path.insert(0, ROOT)
            from parlord import frontends
            self.keys = os.path.join(self.dir.name, "keys")
            os.mkdir(self.keys)
            frontends.write_key(self.keys, "example", "frontend")
            self.ext_port = free_port()
            args = [a for a in args if a not in ("--frontend-port", "0")]
            args += ["--frontend-port", str(self.ext_port), "--keys", self.keys]
        env = dict(os.environ, PYTHONHASHSEED="0")
        env.pop("GRIDWORDS_WORDS", None)             # the word list the server ships
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.path.insert(0, %r); import pnp_examples; "
             "pnp_examples.serve_seeded(%d, sys.argv[1:])" % (os.path.join(ROOT, "tools"), scn.get("seed", 1))]
            + args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        self.banner = self.proc.stdout.readline().decode()
        if not self.banner.startswith("parlord:"):
            self.stop()
            raise RuntimeError("parlord did not start: %r" % self.banner)
        self.out, self.lock = [], threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for raw in self.proc.stdout:
            with self.lock:
                self.out.append(raw.decode("utf-8", "replace").rstrip("\n"))

    def mails(self):
        """The mails sent so far (dicts: to, subject, body), oldest first."""
        import json
        try:
            with open(self.mail_file, encoding="utf-8") as f:
                return [json.loads(l) for l in f if l.strip()]
        except (OSError, AttributeError):
            return []

    def stop(self):
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()
        self.dir.cleanup()


class Mailed:
    """<mailed>: the temporary password in the latest mail (a sign-up's, or a forgotten password's)."""

    def __init__(self, srv):
        self.srv = srv

    async def value(self):
        for _ in range(100):
            mails = self.srv.mails()
            if mails:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("no mail with a password")
        m = re.search(r"Password:\s+(\S+)", mails[-1]["body"] or "")
        if m:
            return m.group(1)
        raise RuntimeError("the latest mail has no password")


class Conn:
    """One connection: the public port, or the extension channel (TLS 1.3 with a pre-shared key)."""

    def __init__(self, r, w):
        self.r, self.w = r, w

    @classmethod
    async def open(cls, srv, ext=False):
        if ext:
            from parlord.frontends import client_context, read_key
            ident, _, key = read_key(os.path.join(srv.keys, "example.key"))
            r, w = await asyncio.open_connection("127.0.0.1", srv.ext_port, ssl=client_context(ident, key),
                                                 server_hostname="parlord")
        else:
            r, w = await asyncio.open_connection("127.0.0.1", srv.port)
        return cls(r, w)

    async def opening(self):
        """The public port's start: the client's opening, PARLORNET/1 and its line ending (CR LF here)."""
        self.w.write(OPENING.encode("ascii") + b"\r\n")
        await self.w.drain()

    async def line(self):
        raw = await asyncio.wait_for(self.r.readline(), 10)
        if not raw:
            raise ConnectionError("the server closed the connection")
        return raw.decode("ascii", "replace").rstrip("\r\n")

    async def req(self, text):
        self.w.write((text + "\r\n").encode("ascii"))
        await self.w.drain()
        out = []
        while True:
            l = await self.line()
            out.append(l)
            if l.split(" ", 1)[0] in TERMINATORS:
                return out

    def close(self):
        self.w.close()


# ---- recording a scenario -------------------------------------------------------------------------

CLK_RE = re.compile(r"^CLK (\d+) \d+ (\d+)$")


class Recorder:
    """Plays a script against a server and keeps the transcript. say() sends one request on a
    connection (opening it first: its greeting is the first line) and returns the reply."""

    def __init__(self, srv, scn):
        self.srv, self.scn = srv, scn
        self.conns, self.lines, self.vars = {}, [], {}
        self.codes = Mailed(srv)

    def note(self, text):
        self.lines.append("# " + text)

    def masked(self, line, masks):
        m = CLK_RE.match(line)
        if m:
            line = "CLK %s <secs> %s" % (m.group(1), m.group(2))
        for name, value in self.vars.items():
            if value and value in line:
                line = re.sub(r"(?<![A-Za-z0-9_-])%s(?![A-Za-z0-9_-])" % re.escape(value), "<%s>" % name, line)
        for pat, rep in masks:
            line = re.sub(pat, rep, line)
        return line

    async def conn(self, c):
        """Open connection c: on the public port the client's opening (CR LF here) comes first and the
        greeting answers it; the extension channel greets at once."""
        if c not in self.conns:
            ext = c in self.scn.get("ext", ())
            self.conns[c] = await Conn.open(self.srv, ext=ext)
            if not ext:
                await self.conns[c].opening()
                self.lines.append("%s> %s" % (c, OPENING))
            self.lines.append("%s< %s" % (c, self.masked(await self.conns[c].line(), ())))
        return self.conns[c]

    async def say(self, c, text, note=None, masks=(), bind=None):
        """bind: a placeholder name for the last word of the final reply line (a session id)."""
        conn = await self.conn(c)
        if note:
            self.note(note)
        real = text
        for name in PLACEHOLDER_RE.findall(text):
            value = await self.codes.value() if name == "mailed" else self.vars[name]
            if name == "mailed":
                self.vars["mailed"] = value
            real = real.replace("<%s>" % name, value, 1)
        self.lines.append("%s> %s" % (c, text))
        reply = await conn.req(real)
        if bind:
            self.vars[bind] = reply[-1].split()[-1]
        self.lines += ["%s< %s" % (c, self.masked(l, masks)) for l in reply]
        return reply

    async def spy(self, name):
        """An unrecorded connection signed in as name (sign-in none), to look at a table while planning a
        move. It changes nothing a recorded connection sees."""
        conn = await Conn.open(self.srv)
        await conn.opening()
        await conn.line()
        await conn.req("HELO " + name)
        return conn


async def record(name):
    scn = SCENARIOS[name]
    srv = Server(scn)
    rec = Recorder(srv, scn)
    try:
        await scn["script"](rec)
    finally:
        for c in rec.conns.values():
            c.close()
        srv.stop()
    return rec.lines


# ---- replaying a block (tests/test_pnp_doc.py) ------------------------------------------------------

def pattern(expected):
    """A doc line -> a regex: each placeholder matches one word."""
    parts = PLACEHOLDER_RE.split(expected)
    out = ""
    for i, p in enumerate(parts):
        out += "(?P<%s_%d>\\S+)" % (p, i) if i % 2 else re.escape(p)
    return re.compile(out + r"\Z")


async def replay(name, lines):
    """Replay a block's client lines against a fresh server; returns a list of differences (empty if
    every server line matches)."""
    scn = SCENARIOS[name]
    srv = Server(scn)
    conns, vars_, codes = {}, {}, Mailed(srv)
    problems = []

    def check(c, expected, got):
        for k, (exp, real) in enumerate(zip(expected, got)):
            m = pattern(exp).match(real)
            if not m:
                problems.append("%s: %s< expected %r, got %r" % (name, c, exp, real))
                continue
            for key, value in m.groupdict().items():
                vars_[key.rsplit("_", 1)[0]] = value
        if len(expected) != len(got):
            problems.append("%s: %s expected %d lines, got %d: %r" % (name, c, len(expected), len(got), got))
    body = [l for l in lines if l.strip() and not l.startswith("#")]
    try:
        i = 0
        while i < len(body) and not problems:
            c, d, text = body[i][0], body[i][1], body[i][3:]
            if d == ">" and c not in conns and c not in scn.get("ext", ()):
                if text != OPENING:
                    problems.append("%s: connection %s must start with %s" % (name, c, OPENING))
                    break
                conns[c] = await Conn.open(srv)
                await conns[c].opening()
                j = i + 1
                while j < len(body) and body[j].startswith(c + "<"):
                    j += 1
                expected = [l[3:] for l in body[i + 1:j]]
                check(c, expected, [await conns[c].line() for _ in expected[:1]])
                i = j
                continue
            if d == "<":
                if c in conns:
                    problems.append("%s: unexpected server line %r" % (name, body[i]))
                    break
                conns[c] = await Conn.open(srv, ext=c in scn.get("ext", ()))
                check(c, [text], [await conns[c].line()])
                i += 1
                continue
            j = i + 1
            while j < len(body) and body[j].startswith(c + "<"):
                j += 1
            expected = [l[3:] for l in body[i + 1:j]]
            real = text
            for ph in PLACEHOLDER_RE.findall(text):
                if ph == "mailed":
                    value = await codes.value()
                else:
                    value = vars_.get(ph)
                if value is None:
                    problems.append("%s: no value for <%s>" % (name, ph))
                    break
                real = real.replace("<%s>" % ph, value, 1)
            if c not in conns:
                problems.append("%s: connection %s sends before its greeting" % (name, c))
                break
            check(c, expected, await conns[c].req(real))
            i = j
    finally:
        for conn in conns.values():
            conn.close()
        srv.stop()
    return problems


def blocks(text):
    """{name: [lines]} of every ```pnp block in a document."""
    out, cur, name = {}, None, None
    for line in text.split("\n"):
        if cur is None:
            m = FENCE_RE.match(line)
            if m:
                name, cur = m.group(1), []
        elif line.startswith("```"):
            out[name] = cur
            cur = None
        else:
            cur.append(line)
    return out


def write_blocks(text, recorded):
    """The document with the named blocks' contents replaced."""
    out, skip = [], False
    for line in text.split("\n"):
        if skip:
            if line.startswith("```"):
                out.append(line)
                skip = False
            continue
        out.append(line)
        m = FENCE_RE.match(line)
        if m and m.group(1) in recorded:
            out += recorded[m.group(1)]
            skip = True
    return "\n".join(out)


# ---- the scenarios ----------------------------------------------------------------------------------

SCENARIOS = {}


def scenario(name, **kw):
    def deco(fn):
        SCENARIOS[name] = dict(kw, script=fn)
        return fn
    return deco


NONE = ["--auth", "none"]
PASSWORD = "swordfish 1997"         # made up for the examples
NEW_PASSWORD = "two swordfish 1998"
LOCK_MASK = [(r"wait=\d+ (.*) in \d+ seconds", r"wait=<n> \1 in <n> seconds")]


def game_plugin(game):
    """A game plugin's module (plugins/<game>/plugin.py), loaded under its own name."""
    import importlib.util
    name = "pnp_plugin_" + game
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "plugins", game, "plugin.py"))
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


async def moves(spy, tid):
    """The legal moves (M lines) a spy connection sees."""
    return [l[2:] for l in await spy.req("VUE %s M" % tid) if l.startswith("M ")]


async def two_players(r, game, opts="", a="ann", b="bob", note=None):
    """Sign in a and b, make a table, sit both down: the game starts."""
    await r.say("A", "HELO " + a)
    await r.say("B", "HELO " + b)
    await r.say("A", ("NEW %s %s" % (game, opts)).strip(), note=note)
    await r.say("A", "JON 1")
    await r.say("B", "JON 1", note="The second player sits down: the game starts.")


# ---- part 1: the server protocol ----

@scenario("greeting", args=NONE, motd="Welcome to this parlor.\nFinished tables are kept for a day.")
async def _greeting(r):
    await r.conn("A")
    await r.say("A", "MOTD", note="Allowed before signing in: MOTD and GAM.")
    await r.say("A", "GAM")
    await r.say("A", "LST", note="Anything else needs a sign-in first.")
    await r.say("A", "HELO ann", note="Sign-in none: the name alone.")
    await r.say("A", "HELO ann")
    await r.say("A", "LST")


@scenario("signup-password", args=["--auth", "password"])
async def _signup_password(r):
    await r.say("A", "HELO bo", note="A free name under sign-up password: choose a password.")
    await r.say("A", "AUT password short", note="Too short: the question comes again, with the reason.")
    await r.say("A", "AUT password " + PASSWORD)
    await r.say("A", "AUT confirm swordfish 1996", note="The two do not match: start the choice again.")
    await r.say("A", "AUT password " + PASSWORD)
    await r.say("A", "AUT confirm " + PASSWORD)
    await r.say("A", "PWD", note="Change the password: the current one, a new one, the new one again.")
    await r.say("A", "AUT current " + PASSWORD)
    await r.say("A", "AUT password " + NEW_PASSWORD)
    await r.say("A", "AUT confirm " + NEW_PASSWORD)
    await r.say("B", "HELO bo", note="Signing in on another connection.")
    await r.say("B", "AUT password " + NEW_PASSWORD)
    await r.say("C", "HELO bo", note="A wrong password locks the name for a minute.")
    await r.say("C", "AUT password " + PASSWORD)
    await r.say("C", "HELO bo", note="While it is locked (no address on file), nothing is checked.", masks=LOCK_MASK)
    await r.say("A", "OUT", note="Sign out; the connection can sign in again.")
    await r.say("D", "CAN text,info", note="A client that cannot hide what is typed says so first.")
    await r.say("D", "HELO cy")


@scenario("signin-signup", args=["--auth", "password"])
async def _signin_signup(r):
    await r.say("A", "USER bo", note="Sign in only: a name with no account is refused; nothing is made.")
    await r.say("A", "REG bo", note="Sign up only: the sign-up method's steps for a new name.")
    await r.say("A", "AUT password " + PASSWORD)
    await r.say("A", "AUT confirm " + PASSWORD)
    await r.say("B", "REG bo", note="A taken name is refused; REG never signs in.")
    await r.say("B", "USER bo", note="Signing in.")
    await r.say("B", "AUT password " + PASSWORD)
    await r.say("C", "REG guest", note="Words some frontends take at their login prompt are reserved.")


@scenario("signup-email-password", args=["--auth", "password", "--signup", "email"], mail=True)
async def _signup_email_password(r):
    await r.say("A", "HELO una", note="A free name under sign-up email: confirm, give an address.")
    await r.say("A", "AUT confirm y")
    await r.say("A", "AUT email una@example.org")
    await r.say("A", "AUT password <mailed>", note="The temporary password from the mail. It confirms the account, "
                "and a password of the player's own comes next.")
    await r.say("A", "AUT password <mailed>", note="Not the temporary one again.")
    await r.say("A", "AUT password " + PASSWORD)
    await r.say("A", "AUT confirm " + PASSWORD)
    await r.say("B", "USER una", note="Signing in another time.")
    await r.say("B", "AUT password reset", note="A forgotten password: a temporary one goes to the address on file.")
    await r.say("B", "AUT password <mailed>")
    await r.say("B", "AUT password " + NEW_PASSWORD)
    await r.say("B", "AUT confirm " + NEW_PASSWORD)


DATE_MASK = [(r"\b\d{4}-\d{2}-\d{2}\b", "<date>")]


@scenario("stay-signed-in", args=["--auth", "password"])
async def _stay_signed_in(r):
    await r.say("A", "REG bo", note="bo makes an account on his Apple IIe, with Stay signed in on.")
    await r.say("A", "AUT password " + PASSWORD)
    await r.say("A", "AUT confirm " + PASSWORD)
    await r.say("A", "CLI MACH Apple IIe", note="The program names the machine; the key is labeled with it.")
    await r.say("A", "KEY NEW", bind="key", note="Right after the password, the key for this computer. The "
                "program keeps it, never the password.")
    await r.say("A", "KEY LST", masks=DATE_MASK, note="The computers where bo stays signed in.")
    await r.say("B", "USER bo", note="Another day: the key answers the password question, and nothing is asked.")
    await r.say("B", "AUT key <key>")
    await r.say("B", "OUT", note="Signing out forgets the key, on the server and in the program.")
    await r.say("C", "USER bo", note="A key the server no longer has is refused, and counts as a wrong answer.")
    await r.say("C", "AUT key <key>")
    await r.say("C", "USER bo", note="The program forgets it and asks for the password.")
    await r.say("C", "AUT password " + PASSWORD)
    await r.say("C", "KEY NEW Apple IIe", bind="key", note="Stay signed in is still on, so this sign-in gets "
                "a new key. A label may come with the request.")
    await r.say("C", "KEY LST", masks=DATE_MASK)
    await r.say("C", "KEY DEL all", note="Forget every key: no computer stays signed in as bo.")
    await r.say("C", "KEY LST")


@scenario("sessions", args=NONE, ext=("X", "Y", "Z"))
async def _sessions(r):
    r.note("X, Y and Z are server extension connections (the extension channel, TLS 1.3 with a key);")
    r.note("the server greets them at once. A is a connection to the public port, which starts with PARLORNET/1.")
    await r.say("X", "SRC 192.0.2.7", note="A server extension names the client it serves.")
    await r.say("X", "HELO ann", bind="sid", note="The session id comes back on the OK line.")
    await r.say("Y", "SES <sid>", note="Another connection of the same server extension resumes it.")
    await r.say("Y", "LST")
    await r.say("A", "SES <sid>", note="The public port has no sessions to resume.")
    await r.say("Y", "OUT", note="Ending the session signs out every connection that holds it.")
    await r.say("X", "LST")
    await r.say("Z", "SES <sid>")


@scenario("tables", args=NONE)
async def _tables(r):
    await r.say("A", "HELO amy")
    await r.say("B", "HELO bo")
    await r.say("C", "HELO cy")
    await r.say("D", "HELO dan", note="dan signs in and out again: he has an account but is not online.")
    await r.say("D", "OUT")
    await r.say("A", "WHO", note="Who is online: the seconds since each one's last activity.",
                masks=[(r"idle=\d+", "idle=<secs>")])
    await r.say("A", "NEW reversi standard size=6 turn=1h vis=private",
                note="A private table: a game option (size), the time per turn, the visibility.")
    await r.say("A", "JON 1", note="Making a table does not seat its creator.")
    await r.say("A", "INV 1 bo 1", note="Invite bo and hold seat 1 for him.")
    await r.say("A", "INV 1 cy", note="Invite cy, with no seat held.")
    await r.say("A", "INV 1 dan", note="An invitation to a player who is not online waits for them.")
    await r.say("B", "IVL")
    await r.say("B", "LST")
    await r.say("C", "JON 1", note="The only free seat is held for bo.")
    await r.say("C", "SEE 1", note="An invited player may watch.")
    await r.say("C", "UNV 1 cy", note="cy declines the invitation.")
    await r.say("C", "LST", note="The private table is gone from cy's list.")
    await r.say("B", "JON 1", note="bo sits in the seat held for him; the game starts.")
    await r.say("A", "SIN 1 0", note="Every event of the table from the start.")
    await r.say("B", "CHT 1 good luck")
    await r.say("A", "SIN 1")
    await r.say("A", "LVE 1", note="Leaving a running game does not resign it: amy keeps her seat.")
    await r.say("A", "LST")


@scenario("sample", args=NONE)
async def _sample(r):
    await r.conn("A")
    await r.say("A", "HELO ann", note="ann signs in (this server's sign-in is none).")
    await r.say("A", "GAM", note="What this server hosts.")
    await r.say("A", "NEW reversi standard turn=1h", note="A Reversi table with an hour per turn.")
    await r.say("A", "JON 1")
    await r.say("B", "HELO bob", note="bob, on another connection, finds the table and sits down.")
    await r.say("B", "LST")
    await r.say("B", "JON 1")
    await r.say("A", "VUE 1 GTM", note="ann's view: geometry, status text and her legal moves.")
    await r.say("A", "CHK 1 c4", note="Check a move without playing it.")
    await r.say("A", "MOV 1 c4", note="Play it: the reply carries the new events.")
    await r.say("A", "MOV 1 f5", note="Not ann's turn now.")
    await r.say("B", "SIN 1", note="bob asks what happened since he last looked.")
    await r.say("B", "MOV 1 c3")
    await r.say("A", "SIN 1")
    await r.say("A", "CHT 1 well played")
    await r.say("B", "RES 1", note="bob resigns: the game ends.")
    await r.say("A", "SIN 1")
    await r.say("A", "BYE")


@scenario("paging", args=NONE)
async def _paging(r):
    await r.say("A", "HELO ann")
    await r.say("A", "PAG 512", note="Pages of at most 512 bytes, each line counted with its CR LF.")
    await r.say("A", "GAM", note="The list of games is longer than a page: whole lines, then CNT.")
    await r.say("A", "CNT", note="CNT brings the next page. The last page ends with the reply's own final line.")
    await r.say("A", "GAM")
    await r.say("A", "LST", note="Any other request drops the rest of the list and is answered as usual.")
    await r.say("A", "CNT", note="Nothing waits for CNT now.")
    await r.say("A", "PAG 300", note="A page holds at least 512 bytes.")
    await r.say("A", "PAG 0", note="PAG 0 turns paging off.")


@scenario("errors", args=NONE)
async def _errors(r):
    await r.say("A", "FROB")
    await r.say("A", "VUE 1")
    await r.say("A", "HELO ann")
    await r.say("A", "VUE 9")
    await r.say("A", "NEW chess standard")
    await r.say("A", "NEW reversi fancy")
    await r.say("A", "NEW reversi standard size=7")
    await r.say("A", "NEW reversi standard turn=10s")
    await r.say("A", "NEW reversi standard")
    await r.say("A", "VUE 1")
    await r.say("A", "MOV 1 d3")
    await r.say("A", "JON 1")
    await r.say("A", "RES 1")
    await r.say("A", "UND 1")
    await r.say("A", "CHT 1")
    await r.say("A", "JON 1")
    await r.say("A", "INV 1 nobody")
    await r.say("A", "PWD")


@scenario("commands", args=NONE)
async def _commands(r):
    await r.say("A", "HELO ann")
    await r.say("B", "HELO bob")
    await r.say("A", "CMD /open", note="In the lobby, the line goes as typed. Here /open needs a game.")
    await r.say("A", "CMD /open reversi")
    await r.say("A", "CMD 1 /invite bob", note="At ann's open table, the command runs there.")
    await r.say("B", "CMD /accept ann", note="An invitation is answered in its dialog, which sends JON.")
    await r.say("B", "JON 1")
    await r.say("A", "CMD 1 /d3", note="A typed move that is played has nothing to say.")
    await r.say("A", "SIN 1", note="What it did comes as events, as after any request.")
    await r.say("A", "CMD 1 /f5", note="A refusal is a sentence like any other.")
    await r.say("B", "CMD 1 /a1")
    await r.say("B", "CMD 1 /say well played")
    await r.say("B", "CMD 1 /resign", note="Resigning asks first, with DLG and the dialog's words.")
    await r.say("B", "CMD 1 YES /resign", note="The dialog's Yes sends the confirmed form.")
    await r.say("A", "CMD /who")
    await r.say("A", "CMD who", note="The line starts with its slash.")
    await r.say("A", "CMD 9 /who")


@scenario("prefs", args=NONE)
async def _prefs(r):
    await r.say("A", "HELO ann")
    await r.say("A", "PRF SET board dark", note="Keep a value for ann: a key, then the rest of the line.")
    await r.say("A", "PRF SET hello Good game, all")
    await r.say("A", "PRF GET board")
    await r.say("A", "PRF GET sound", note="A key that is not set: OK alone.")
    await r.say("A", "PRF LST")
    await r.say("A", "PRF DEL hello")
    await r.say("A", "PRF SET a.b x", note="Keys are letters, digits, - and _.")
    await r.say("A", "PRF SET note " + "x" * 57, note="Values are at most 56 characters.")
    await r.say("B", "HELO bob", note="Each player has their own.")
    await r.say("B", "PRF LST")


WHEN = [(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", "<when>")]     # a turn's time (TRN, FEED)


@scenario("clients", args=NONE)
async def _clients(r):
    await r.conn("A")
    await r.say("A", "CLI PROG ParlorNet for the Apple IIe 1.1",
                note="Before signing in (or after): the program and its version, then the machine.")
    await r.say("A", "CLI MACH Apple IIe (enhanced)")
    await r.say("A", "CLI")
    await r.say("A", "HELO ann")
    await r.say("B", "HELO bob", note="bob's program says nothing about itself.")
    await r.say("A", "NEW reversi standard")
    await r.say("A", "JON 1")
    await r.say("B", "SEE 1", note="bob watches: the table was told what ann plays with, right after her JOIN.")
    await r.say("B", "SIN 1 0")
    await r.say("B", "VUE 1 C", note="The view with C: what each seated player plays with.")
    await r.say("B", "LST clients", note="The same lines after each table's TAB line, for a lobby list.")
    await r.say("B", "JON 1", note="bob's connection named nothing: no CLIENT follows his JOIN.")
    await r.say("A", "MOV 1 d3")
    await r.say("B", "MOV 1 c3")
    await r.say("B", "TRN 1", note="The table's turns: who, when, the turn in words, and with what.", masks=WHEN)
    await r.say("A", "TRN 1 7", note="Only the turns after event 7.", masks=WHEN)
    await r.say("B", "FEED 5", note="The latest turns at public tables, newest first.", masks=WHEN)
    await r.say("A", "PFL SET PREF Macintosh SE/30", note="ann names the machine she likes best.")
    await r.say("B", "PFL ann", note="Anyone signed in reads her profile.")
    await r.say("B", "PFL nobody")


@scenario("scores", args=NONE)
async def _scores(r):
    await r.say("A", "HELO ann")
    await r.say("B", "HELO bob")
    await r.say("A", "NEW reversi standard")
    await r.say("A", "JON 1")
    await r.say("B", "JON 1")
    await r.say("B", "RES 1", note="bob resigns: ann scores a win (4 points), bob a resignation (0).")
    await r.say("A", "NEW checkers american")
    await r.say("A", "JON 2")
    await r.say("B", "JON 2")
    await r.say("A", "MOV 2 draw", note="A draw by agreement: 2 points each.")
    await r.say("B", "MOV 2 accept")
    await r.say("B", "TOP", note="The leaderboard: rank, name, points, wins, draws, losses, resigns.")
    await r.say("B", "TOP 1 game=reversi", note="One game's points only.")
    await r.say("A", "PFL bob", note="A profile carries the score and the rank.")
    await r.conn("C")
    await r.say("C", "TOP 1", note="No sign-in is needed to read the leaderboard.")


# ---- part 2: the games ----

@scenario("reversi", args=NONE, seed=11)
async def _reversi(r):
    await two_players(r, "reversi", "standard size=4", note="A 4 by 4 board, for a short game.")
    await r.say("A", "VUE 1 GTM")
    spies = {"A": await r.spy("ann"), "B": await r.spy("bob")}
    while True:
        for c in ("A", "B"):
            m = await moves(spies[c], 1)
            if m:
                reply = await r.say(c, "MOV 1 " + m[0])
                break
        else:
            break
        if any(" END " in l for l in reply):
            break
    await r.say("B", "SIN 1")
    await r.say("A", "VUE 1 T")


@scenario("backgammon", args=NONE, seed=3)
async def _backgammon(r):
    await two_players(r, "backgammon", "standard cube=0", note="No doubling cube: each turn starts with a roll.")
    spies = {"A": await r.spy("ann"), "B": await r.spy("bob")}
    first = None
    for c in ("A", "B"):
        if await moves(spies[c], 1):
            first = c
    other = "B" if first == "A" else "A"
    await r.say(first, "SIN 1 0", note="The opening roll: one die each; the higher die starts and plays both.")
    await r.say(first, "VUE 1 GTM")
    undone = False
    for c in (first, other):
        while True:
            m = await moves(spies[c], 1)
            if not m:
                break
            reply = await r.say(c, "MOV 1 " + m[-1])
            if not undone:
                undone = True
                await r.say(c, "UND 1", note="Take the step back, and play it again.")
                reply = await r.say(c, "MOV 1 " + m[-1])
            if reply[-1] == "OK done":
                break
        await r.say(c, "END 1")
    await r.say(first, "VUE 1 T")
    await r.say(first, "MOV 1 resign-single", note="Offer to resign a single game.")
    await r.say(other, "MOV 1 accept")


GW_RACK_RE = re.compile(r"^R (\S+)$")


def gridwords_words():
    gw = game_plugin("gridwords")
    return gw, gw.load_words()


def formable(word, rack):
    rack = list(rack)
    for ch in word:
        if ch in rack:
            rack.remove(ch)
        else:
            return False
    return True


@scenario("gridwords", args=NONE, seed=5)
async def _gridwords(r):
    gw, words = gridwords_words()
    await two_players(r, "gridwords", "english")
    view = await r.say("A", "VUE 1 GT", note="The static geometry (flag G) comes first; a client keeps it.")
    rack = next(GW_RACK_RE.match(l).group(1) for l in view if GW_RACK_RE.match(l))
    cands = sorted((w for w in words if 4 <= len(w) <= 5 and formable(w, rack)), key=lambda w: (-len(w), w))
    first = cands[0]
    await r.say("A", "CHK 1 PLAY 8 8 H %s" % first[::-1], note="Check a play: these letters are not a word.")
    await r.say("A", "CHK 1 PLAY 8 8 H %s" % first, note="This one is: the points and the words.")
    await r.say("A", "MOV 1 PLAY 8 8 H %s" % first)
    spy = await r.spy("bob")
    rack_b = next(GW_RACK_RE.match(l).group(1) for l in await spy.req("VUE 1") if GW_RACK_RE.match(l))
    play = None
    for i, letter in enumerate(first):
        x = 8 + i
        for w in sorted(words, key=lambda w: (abs(len(w) - 4), w)):
            if not 3 <= len(w) <= 5 or letter not in w:
                continue
            k = w.index(letter)
            if 8 - k < 1 or 8 - k + len(w) - 1 > 15:
                continue
            if not formable(w[:k] + w[k + 1:], rack_b):
                continue
            mv = "PLAY %d %d V %s" % (x, 8 - k, w)
            if (await spy.req("CHK 1 " + mv))[-1].startswith("OK"):
                play = mv
                break
        if play:
            break
    await r.say("B", "VUE 1")
    await r.say("B", "CHK 1 " + play)
    await r.say("B", "MOV 1 " + play)
    view = await r.say("A", "VUE 1")
    rack = next(GW_RACK_RE.match(l).group(1) for l in view if GW_RACK_RE.match(l))
    await r.say("A", "MOV 1 EXCH " + rack[:2], note="Two passes or exchanges by each player end the game.")
    await r.say("B", "MOV 1 PASS")
    await r.say("A", "MOV 1 PASS")
    await r.say("B", "MOV 1 PASS")
    await r.say("A", "VUE 1 T")


@scenario("cannonfire", args=NONE, seed=1)
async def _cannonfire(r):
    cf = game_plugin("cannonfire")
    await two_players(r, "cannonfire", "classic seed=2026",
                      note="seed=2026: the landscape and wind of the type spec's recorded game.")
    await r.say("A", "VUE 1 GT")
    st = cf.new_state(2, 2026)
    conns = ("A", "B")

    def best_shot(st, seat):
        """The shot that leaves the other tank the fewest hit points (then the least harm to itself)."""
        best = None
        for a in range(5, 176):
            for p in range(20, 101):
                st2, ev, end = cf.shoot(st, seat, a, p)
                key = (st2["tanks"][1 - seat][1], -st2["tanks"][seat][1], abs(a - 90), p)
                if best is None or key < best[0]:
                    best = (key, a, p)
        return best[1], best[2]

    await r.say("A", "CHK 1 MOVE L 7", note="CHK previews a move: where the tank would end up, and the path it "
                "would use of the budget, in tenths of a column (7.2 columns in a 72-column arena).")
    await r.say("A", "MOV 1 MOVE L 7", note="Up the slope: 4 columns for 5.2 columns of path. No TURN: A still "
                "has the turn and fires next.")
    st = cf.drive(st, 0, -1, 7)[0]
    await r.say("A", "MOV 1 MOVE R 2", note="One move a turn.")
    a, p = best_shot(st, 0)
    await r.say("A", "CHK 1 FIRE %d %d" % (a, p), note="CHK checks the shot's numbers only.")
    await r.say("A", "MOV 1 FIRE %d %d" % (a, p), note="A direct hit takes 20, the most a shot does (less with "
                "distance, none from 10 away). The crater drops bob's tank 6; it falls with the ground, unharmed.")
    st = cf.shoot(st, 0, a, p)[0]
    await r.say("B", "MOV 1 MOVE R 7", note="The other tank sees the events it missed first. Its move climbs out "
                "of the crater: 5 columns for 6.2 columns of path.")
    st = cf.drive(st, 1, 1, 7)[0]
    a, p = best_shot(st, 1)
    await r.say("B", "MOV 1 FIRE %d %d" % (a, p))
    st = cf.shoot(st, 1, a, p)[0]
    a, p = best_shot(st, 0)
    await r.say("A", "MOV 1 FIRE %d %d" % (a, p), note="A fires without moving; it has 3 moves left.")
    await r.say("B", "VUE 1 T", note="Five direct hits destroy a tank, so a game runs to ten shots or more.")


@scenario("dotsandboxes", args=NONE)
async def _dotsandboxes(r):
    await r.say("A", "HELO ann")
    await r.say("B", "HELO bob")
    await r.say("C", "HELO cy")
    await r.say("A", "NEW dotsandboxes standard players=3 size=3")
    for c in "ABC":
        await r.say(c, "JON 1")
    await r.say("A", "MOV 1 a1-b1")
    await r.say("B", "MOV 1 a1-a2")
    await r.say("C", "MOV 1 b2b1", note="Either dot first, the dash left out: the event names the line.")
    await r.say("A", "CHK 1 a2-b2", note="How many boxes would this line close?")
    await r.say("A", "MOV 1 a2-b2", note="It closes box a1: ann moves again.")
    await r.say("A", "MOV 1 c3-d3")
    await r.say("B", "VUE 1 GT")
    await r.say("C", "RES 1", note="With three playing, cy leaves and the others play on.")
    await r.say("B", "MOV 1 a4-b4")
    await r.say("A", "MOV 1 c1-c2")
    await r.say("B", "RES 1", note="With two left, a resignation ends the game.")
    await r.say("C", "SIN 1")


@scenario("fiveinarow", args=NONE)
async def _fiveinarow(r):
    await two_players(r, "fiveinarow", "freestyle size=9")
    for a, b in (("e5", "a9"), ("e4", "b9"), ("e3", "c9"), ("e2", "d9")):
        await r.say("A", "MOV 1 " + a)
        await r.say("B", "MOV 1 " + b)
    await r.say("A", "VUE 1 GT")
    await r.say("A", "CHK 1 f5")
    await r.say("A", "CHK 1 e6", note="CHK says whether a move wins.")
    await r.say("A", "MOV 1 e6")
    await r.say("B", "VUE 1 T")


@scenario("mancala", args=NONE)
async def _mancala(r):
    await two_players(r, "mancala", "kalah")
    await r.say("A", "VUE 1 GTM")
    await r.say("A", "MOV 1 3", note="The last seed lands in ann's store: she moves again.")
    spies = {"A": await r.spy("ann"), "B": await r.spy("bob")}
    for _ in range(20):
        m = await moves(spies["A"], 1)
        c = "A" if m else "B"
        m = m or await moves(spies["B"], 1)
        caps = [p for p in m if (await spies[c].req("CHK 1 " + p))[-1] != "OK captures 0"]
        if caps:
            await r.say(c, "CHK 1 " + caps[0], note="CHK tells how many seeds a move would capture.")
            await r.say(c, "MOV 1 " + caps[0])
            break
        await r.say(c, "MOV 1 " + m[-1])
    await r.say("B", "VUE 1 T")
    await r.say("B", "RES 1")


@scenario("checkers", args=NONE)
async def _checkers(r):
    await two_players(r, "checkers", "american")
    await r.say("A", "VUE 1 GTM")
    await r.say("A", "MOV 1 11-15", note="One step; the turn is complete, so the reply is OK done.")
    await r.say("A", "UND 1")
    await r.say("A", "MOV 1 11-15")
    await r.say("A", "END 1")
    await r.say("B", "MOV 1 22-18")
    await r.say("B", "END 1")
    await r.say("A", "VUE 1 TM", note="A capture is compulsory.")
    await r.say("A", "MOV 1 15x22")
    await r.say("A", "END 1")
    await r.say("B", "MOV 1 26x17")
    await r.say("B", "END 1")
    await r.say("A", "MOV 1 draw", note="Offer a draw.")
    await r.say("B", "MOV 1 accept")


@scenario("navalcombat", args=NONE, seed=4)
async def _navalcombat(r):
    await two_players(r, "navalcombat", "classic")
    await r.say("A", "MOV 1 a1-a4", note="Ships are placed largest first; this one has five squares.")
    await r.say("A", "MOV 1 a1-e1")
    await r.say("A", "MOV 1 random", note="The server places the rest.")
    await r.say("A", "VUE 1 GT")
    await r.say("A", "MOV 1 ready")
    await r.say("B", "MOV 1 random")
    await r.say("B", "MOV 1 ready")
    spy_a, spy_b = await r.spy("ann"), await r.spy("bob")
    view_b = await spy_b.req("VUE 1")
    two = next(l.split()[2] for l in view_b if l.startswith("Y 2 "))
    nc = game_plugin("navalcombat")
    targets = [nc.sq_name(*c) for c in nc.ship_cells(nc.parse_place(two))]
    mine = {nc.sq_name(*c) for l in await spy_a.req("VUE 1") if l.startswith("Y ")
            for c in nc.ship_cells(nc.parse_place(l.split()[2]))}
    misses = [s for s in ("j10", "j9", "i10") if s not in mine]
    await r.say("A", "CHK 1 " + targets[0], note="CHK never tells hit or miss.")
    await r.say("A", "MOV 1 " + targets[0])
    await r.say("B", "MOV 1 " + misses[0])
    await r.say("A", "MOV 1 " + targets[1])
    await r.say("B", "VUE 1 T", note="bob's view: his own fleet, and of ann's only the shots.")
    await r.say("B", "RES 1")


RUMMY_SEED = 2


def rummy_meld(rack):
    """Two or more sets from a rack, worth 30 or more: (tokens, points), or None."""
    rt = game_plugin("rummytile")
    import itertools
    tiles = [t for t in rack if t != "J"]
    sets = []
    for c in "RBKY":
        nums = sorted({int(t[1:]) for t in tiles if t[0] == c})
        for i in range(len(nums)):
            for j in range(i + 2, len(nums)):
                run = nums[i:j + 1]
                if run[-1] - run[0] == len(run) - 1:
                    sets.append([c + str(n) for n in run])
    for n in range(1, 14):
        cols = sorted({t[0] for t in tiles if int(t[1:]) == n}, key="RBKY".index)
        for k in (3, 4):
            for combo in itertools.combinations(cols, k):
                sets.append([c + str(n) for c in combo])
    best = None
    for k in (1, 2):
        for combo in itertools.combinations(sets, k):
            used = [t for s in combo for t in s]
            if any(used.count(t) > rack.count(t) for t in used):
                continue
            pts = sum(rt.value(s) for s in combo)
            if pts >= 30 and (best is None or (k, -pts) < (len(best[0]), -best[1])):
                best = (["".join(s) + "-n" for s in combo], pts)
    return best


@scenario("rummytile", args=NONE, seed=RUMMY_SEED)
async def _rummytile(r):
    await two_players(r, "rummytile", "standard")
    view = await r.say("A", "VUE 1 GT")
    rack = [t for l in view if l.startswith("H ") for t in l.split()[1:]]
    toks, _ = rummy_meld(rack)
    for t in toks:
        await r.say("A", "MOV 1 " + t)
    await r.say("A", "CHK 1 !end", note="What END would do now.")
    await r.say("A", "END 1")
    view = await r.say("B", "VUE 1")
    tile = next(t for l in view if l.startswith("H ") for t in l.split()[1:])
    await r.say("B", "MOV 1 %s-n" % tile, note="One tile is not a set; before a first meld the table's sets "
                "cannot be touched.")
    await r.say("B", "CHK 1 !end")
    await r.say("B", "UND 1")
    await r.say("B", "END 1", note="Nothing played: END draws a tile.")
    await r.say("A", "RES 1")


def main(argv):
    if "--list" in argv:
        print("\n".join(SCENARIOS))
        return
    if argv and argv[0] == "--print":
        for n in argv[1:] or SCENARIOS:
            print("```pnp %s\n%s\n```" % (n, "\n".join(asyncio.run(record(n)))))
        return
    names = argv or list(SCENARIOS)
    with open(DOC) as f:
        text = f.read()
    present = blocks(text)
    recorded = {}
    for n in names:
        if n not in SCENARIOS:
            sys.exit("no scenario %s" % n)
        if n not in present:
            print("docs/pnp.md has no block for %s" % n, file=sys.stderr)
            continue
        recorded[n] = asyncio.run(record(n))
        long = [l for l in recorded[n] if not l.startswith("#") and len(l) - 3 > 78]
        if long:
            sys.exit("%s: a line over 78 characters: %r" % (n, long[0]))
        print("%s: %d lines" % (n, len(recorded[n])))
    with open(DOC, "w") as f:
        f.write(write_blocks(text, recorded))


if __name__ == "__main__":
    main(sys.argv[1:])
