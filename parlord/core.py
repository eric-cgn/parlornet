"""parlord: the ParlorNet table host.

Implements docs/protocol.md (client protocol) and docs/plugins.md (game
plugins). It knows no game rules: tables run game plugins, which are
subprocesses speaking JSON lines. Replies are any number of data lines ending
with exactly one OK or ERR line.
"""
import asyncio
import json
import os
import re
import secrets
import sys
import time

from . import DOCS_URL, PROJECT_URL, bots, frontends, native
from .accounts import STEP_KINDS, Accounts, Player, source_key  # noqa: F401  (Player: for code that imports it from here)
from .store import Store

VERSION = 1
MAXLINE = 80
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
WORD_RE = re.compile(r"^[A-Za-z0-9_-]{1,22}\Z")      # the second word of HELO, and a session id
SRC_RE = re.compile(r"^[0-9A-Fa-f:.]{2,45}\Z")       # an IPv4 or IPv6 address (SRC)
PREF_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+\Z")   # and at most Limits.pref_key_len
NATIVE_NS = ":native"     # the public port's namespace for PRF: never an extension's key name (no colon there)
PREF_SHARED = {"play": "web"}  # extensions whose PRF data is kept under another's name (pref_ns)
SIGNIN_VERBS = ("HELO", "USER", "REG", "AUT", "SES", "CAN", "SRC", "BYE", "MOTD", "GAM",  # before signing in
                "PRO", "PRD", "PRL", "NFY", "CLI", "TOP", "TIM")
# What a client says it is (CLI; docs/protocol.md, Clients and machines): the program and the machine. The
# words of CLI and of the TRX lines of a turn (TRN, FEED), and the words of the profile (PFL).
CLAIM_FIELDS = ("PROG", "MACH")
# Notices for players who are not at the table (NFY; docs/protocol.md, Notices): the kinds, and the PRF key in
# an extension's namespace where a player lists the kinds they want from it, with the extension's token.
NOTICE_KINDS = ("turn", "end", "join")
NOTICE_PREF = "notify"
NOTICE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,8}\Z")
# A protocol's key (PRO): an upper-case letter, then upper-case letters, digits, / and -, at most 12 (HTTP,
# TELNET, PARLORNET/1, IOS, FIBS, OUTLAND). docs/protocol.md, Protocols; docs/design/protocol-declarations.md.
PROTO_KEY_RE = re.compile(r"^[A-Z][A-Z0-9/-]{0,11}\Z")
PROTO_NAME_LEN = 48       # PRO key port name: the name, printable ASCII
PROTO_DESC_LEN = 56       # PRD key text: the one-line description
PROTO_PER_CONN = 4        # protocols one connection may declare
NATIVE_PROTOCOL = ("PARLORNET/%d" % VERSION, "ParlorNet protocol (PARLORNET/%d)" % VERSION,
                   "ParlorNet's own protocol, for programs written for it")
# Why an invitation ended, the last word of its UNINVITE event (docs/protocol.md, Invitations).
UNINVITE_WHY = ("declined", "withdrawn", "expired", "started", "left")
# The requests that are the player's own activity (docs/accounts.md, Who is online): what a player does.
# Looking (LST, IVL, WHO, VUE, SIN, WAT, SEE, GAM, MOTD, TIM, PRF GET and LST) is what frontends also do on their
# own, every few seconds, so it never counts; a frontend reports the commands a player types that need no
# other request with ACT. Signing in counts as well.
ACTIVE_VERBS = frozenset(("ACT", "NEW", "JON", "LVE", "INV", "UNV", "RES", "MOV", "CHK", "UND", "END", "CHT", "OPR",
                          "PWD"))


class Limits:
    max_conns = 120          # public port
    # The extension channel has its own caps, so a flood on the public port cannot shut it out, and
    # each server extension has its own share, so a flood through one (its public port) cannot shut
    # out the others. Every shipped adapter caps its own connections well under the share (each
    # opens a core connection only for a client that has sent a login line, and counts those).
    max_frontend_conns = 1200     # all server extensions together (a file-descriptor bound)
    max_conns_per_frontend = 250  # one server extension (one key name)
    handshakes_per_source = 8     # connections still in the TLS handshake from one address (no name known yet)
    max_handshakes = 64           # ... in all; over either, the oldest of the busiest address is dropped
    # The defaults of the settings in SETTING_LIMITS (Server.limit). A table in memory is at most about 100 KB
    # (its last event_keep events, the game's state, notes and data), so 400 tables, finished ones kept for a
    # day among them, stay near 40 MB.
    online_max = 200         # players signed in at once, bots not counted (ERR BUSY at sign-in)
    tables_max = 400         # tables on the server, finished ones included (ERR FULL)
    active_max = 20          # games a player is playing at once (ERR LIMIT on sitting down)
    waiting_per_type = 2     # open tables of one game type a player waits at (ERR LIMIT on sitting down)
    open_hours = 24          # an open table with nothing happening at it goes after this long
    turn_max_days = 5        # the longest time per turn a table may set (turn=)
    list_max = 20            # LST lists at most this many tables besides the asker's own (then MORE n)
    idle_secs = 900          # silent connection
    done_keep_secs = 24 * 3600    # finished tables stay listed this long
    turn_default = 24 * 3600      # per-turn time limit unless the creator sets one
    turn_min = 60
    turn_max = 5 * 24 * 3600      # the admin's default time per turn at most (the tables': turn_max_days)
    sweep_secs = 10
    rate_per_sec = 10.0
    rate_burst = 30
    event_keep = 500
    invites_per_free_seat = 2    # open invitations per table: twice its free seats
    invite_secs = 3600       # an invitation not taken up or renewed in this time expires
    presence_idle = 1800     # a player with no activity this long is not online (setting presence.idle)
    plugin_idle_secs = 600   # a plugin process stops after this long without a call (setting plugins.idle)
    # The public port's start (docs/protocol.md, Connecting): the client's PARLORNET/1 first, whose line
    # ending sets the connection's.
    first_line_secs = native.OPENING_SECS     # from connecting to the client's opening; then ERR PROTOCOL
    ending_secs = native.ENDING_SECS          # after the first CR (LF), for the LF (CR) of a two-byte ending
    # What each server extension (and the public port) may keep for a player (PRF; docs/protocol.md).
    # Sized so the longest request, PRF SET key value, fits the 80-byte line.
    pref_keys = 16           # keys per extension per player
    pref_key_len = 12        # a key: 1 to 12 of A-Z a-z 0-9 _ -
    pref_value_len = 56      # a value: 1 to 56 printable ASCII characters
    pref_bytes = 1024        # keys and values together, per extension per player
    # A table's notes (NOTE) and its data (TDL, TDS, TDD; docs/protocol.md). A NOTE line, "NOTE date name
    # text", fits the 80-byte line; the core's own notes ("-" for the name) may be up to note_core_len.
    notes = 20               # notes per table: a player's is refused past it, the core's drops the oldest
    note_len = 48            # a player's note: 1 to 48 printable ASCII characters
    note_core_len = MAXLINE - 2 - len("NOTE 2026-11-22 - ")     # 60
    tdata_keys = 16          # keys a table's data holds, besides the core's own (TDATA_CORE)
    tdata_key_len = 8        # a key: 1 to 8 of a-z 0-9 _ -, so "TDS table key value" fits the line
    tdata_value_len = 56     # a value: 1 to 56 printable ASCII characters
    racks_why_len = 20       # what open racks are for (OPR table text), as the notes name it
    integrity_why_len = note_core_len - len("Integrity: ")     # 49: why a table is marked integrity=false
    notices_keep = 1000      # notices kept for NFY (in memory only)
    notice_wait = 30         # the longest NFY waits for one
    # Clients and machines (CLI), the turns played (TRN, FEED) and profiles (PFL); docs/protocol.md.
    claim_len = 40           # CLI PROG text, CLI MACH text, PFL SET PREF text: printable ASCII, cleaned and cut
    say_len = 60             # a turn in words (the plugin's say), so "TRX MOVE text" fits the line
    turns_list = 50          # TRN table lists at most the latest this many turns
    feed_default = 10        # FEED without a number
    feed_max = 30            # FEED n: at most this many
    turns_keep = 5000        # turns kept in the database (the counts of the profiles are kept apart)
    profile_top = 8          # PFL lists at most this many game types, programs and machines each
    # What each seated player plays with, said when they sit down (the CLIENT event, VUE C, LST clients).
    client_say_len = 60      # "with PROG on a MACH"; in an event, as much as "EVT table seq CLIENT seat " leaves
    # Scores and the leaderboard (TOP, PFL SCORE; docs/protocol.md, Scores).
    top_default = 10         # TOP without a number
    top_max = 50             # TOP n: at most this many


# The settings that are a whole number from 1 (docs/accounts.md, Admin panel): setting -> (the attribute of
# Limits that is its default, the highest value the admin menus take). Unset, empty, 0 or not a number: the default.
SETTING_LIMITS = {
    "players.online_max": ("online_max", 5000),
    "games.tables_max": ("tables_max", 5000),
    "games.active_max": ("active_max", 999),
    "games.waiting_per_type": ("waiting_per_type", 99),
    "games.open_hours": ("open_hours", 720),
    "games.turn_max_days": ("turn_max_days", 30),
    "lists.max": ("list_max", 200),
}

# The points a seated player gets when a game ends (docs/protocol.md, Scores): setting -> its default. Each is
# a whole number from 0 to SCORE_MOST; unset, empty or not a number: the default. A timeout scores as a
# resignation. scores.show (on or off, default on) says whether the leaderboard and the scores are shown.
SCORE_SETTINGS = {"scores.win": 4, "scores.draw": 2, "scores.loss": 1, "scores.resign": 0}
SCORE_MOST = 99
SCORE_REASONS = ("win", "draw", "loss", "resign", "timeout")


TDATA_KEY_RE = re.compile(r"^[a-z0-9_-]+\Z")
TDATA_CORE = ("racks",)     # the keys of a table's data that the core keeps itself (TDS and TDD refuse them)
# A table marked integrity=false (INT; docs/protocol.md, Tables marked integrity=false): the server has given
# out something it normally keeps, such as every rack. The core keeps "false why" under this key of the table's
# data. It is longer than a key TDS takes, so no server extension can set or forget it there.
INTEGRITY_KEY = "integrity"
OPEN_RACKS_WHY = "each player's program receives the bag and racks"     # racks=open, or opened later (OPR)


def printable(text):
    return all(" " <= c <= "~" for c in text)


def clean_claim(text, most=None):
    """What a client says about itself (CLI), or a player's preferred machine (PFL): printable ASCII only,
    runs of spaces made one, trimmed, and cut to most characters (Limits.claim_len). "" when nothing is left."""
    text = " ".join("".join(c if " " <= c <= "~" else " " for c in text or "").split())
    return text[:most or Limits.claim_len].rstrip()


# A program and a machine in words, as the frontends say them with a turn (adapters/common.py with_program and
# on_machine, which keep the same lists): programs whose names read with "the" before them, and machine names
# that are systems rather than machines and take no article.
THE_PROGRAMS = ("Outland Gateway", "ParlorNet bot")
SYSTEMS = ("macos", "mac os", "windows", "linux", "android", "chromeos", "ios", "ipados", "unix", "sunos", "os/2",
           "beos", "freebsd", "openbsd", "netbsd")


def client_say(prog, mach, most=None):
    """What a player plays with, in words without their name, for the CLIENT event: "with GameMaster 1.0.0 +
    ParlorNet 0.5 on a Macintosh Plus", "with Netscape Navigator 3.0", "on an Apple IIe (enhanced)"; "" when the
    connection named neither. At most most characters (Limits.client_say_len): the program is kept whole and
    the machine cut at a word, or left out when not even its first word fits."""
    most = most or Limits.client_say_len
    p = "with %s%s" % ("the " if prog.startswith(THE_PROGRAMS) else "", prog) if prog else ""
    m = ""
    if mach:
        m = "on " + mach if mach.lower().startswith(SYSTEMS) else \
            "on %s %s" % ("an" if mach[:1].lower() in "aeiou" else "a", mach)
    text = " ".join(x for x in (p, m) if x)
    if len(text) <= most:
        return text
    if len(p) > most:
        return p[:most].rstrip()
    words, keep = m.split(), ""
    for i, w in enumerate(words):
        nxt = (keep + " " + w).strip()
        if len(" ".join(x for x in (p, nxt) if x)) > most:
            break
        keep = nxt
    if p and len(keep.split()) < (3 if keep.split()[1:2] in (["a"], ["an"]) else 2):
        return p
    return " ".join(x for x in (p, keep) if x) or p


def utc_stamp(at):
    """A time as TRN and FEED send it: 2026-11-23T14:05:09Z."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def parse_duration(text):
    """'90', '90s', '5m', '2h', '1d' -> seconds, or None."""
    m = re.match(r"^(\d{1,6})([smhd]?)\Z", text.lower())
    if not m:
        return None
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


MOTD_WIDTH = MAXLINE - 2 - len("MOTD ")       # 73: "MOTD text" fits the 80-byte line with its CR LF
MOTD_LINES = 20                               # a 24-line terminal shows the whole message with the login prompt
MOTD_MARKER = "{contribute}"                  # replaced by site.contribute_url; its line is left out once cleared


def wrap(lines, width=76):
    """Lines of text cut at spaces to at most width characters (for INF and
    MOTD); a word longer than a line is cut where the line ends. A line that
    fits is kept as it is, spaces and all (an ASCII layout in the MOTD)."""
    out = []
    for para in lines:
        if len(para.rstrip()) <= width:
            out.append(para.rstrip())
            continue
        cur = ""
        for w in para.split():
            while len(w) > width:
                if cur:
                    out.append(cur)
                    cur = ""
                out.append(w[:width])
                w = w[width:]
            if cur and len(cur) + 1 + len(w) > width:
                out.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        out.append(cur[:width])
    return out


def ok(*parts):
    """The OK line, with any info after one space; just "OK" when there is none."""
    return " ".join(["OK"] + [str(p) for p in parts if str(p) != ""])


def err(code, text=""):
    return ("ERR %s %s" % (code, text)).rstrip()[:MAXLINE - 2]


class PluginError(Exception):
    pass


HOST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pluginhost.py")


class PluginProcess:
    """A plugin process: a game's own, or the plugin host (parlord/pluginhost.py)
    serving every Python plugin. Started on its first call, stopped by the
    sweep once idle (Server.stop_idle_plugins). Calls are stateless, so it can
    serve every table and be restarted at will: a call that fails or takes
    longer than its timeout kills it, and the call is tried once more in a new
    one."""

    def __init__(self, argv):
        self.argv = argv
        self.proc = None
        self.lock = asyncio.Lock()
        self.used = time.monotonic()
        self.spawns = 0

    @property
    def running(self):
        return self.proc is not None and self.proc.returncode is None

    async def _spawn(self):
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        self.spawns += 1

    async def call(self, req, timeout=5.0):
        async with self.lock:
            try:
                for attempt in (0, 1):
                    try:
                        if not self.running:
                            await self._spawn()
                        self.proc.stdin.write((json.dumps(req, separators=(",", ":")) + "\n").encode())
                        await self.proc.stdin.drain()
                        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout)
                        if not line:
                            raise PluginError("plugin exited")
                        return json.loads(line)
                    except (PluginError, asyncio.TimeoutError, OSError, ValueError):    # OSError: also a failed start
                        if self.running:
                            self.proc.kill()
                        self.proc = None
                        if attempt:
                            raise PluginError("plugin failed")
            finally:
                self.used = time.monotonic()

    def idle_for(self):
        return time.monotonic() - self.used

    async def close(self):
        """Stop it (it starts again on the next call)."""
        async with self.lock:
            proc, self.proc = self.proc, None
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()

    def stop(self):
        if self.running:
            self.proc.kill()


class Plugin:
    """One game type: its info, read once at start-up (listing the games needs
    no process), and the process that answers its calls: the plugin host
    shared by the Python plugins (game: its name there), or a process of its
    own."""

    def __init__(self, path, process=None, game=None):
        self.path = path
        self.game = game
        self.process = process or PluginProcess([sys.executable, path] if path.endswith(".py") else [path])
        self.info = None

    @property
    def proc(self):
        return self.process.proc

    async def call(self, req, timeout=5.0):
        if self.game is not None:
            req = dict(req, game=self.game)
        return await self.process.call(req, timeout)

    def stop(self):
        self.process.stop()


SEED_BITS = 63    # a table's seed: too wide to find by trying every value, and fits SQLite's INTEGER


def table_seed():
    """A new table's seed, from the operating system's secure generator. Every die, deal and shuffle of
    the table comes from it (docs/plugins.md), and it never leaves the core, so a player cannot learn it
    by trying every seed against the dice or tiles they have seen (docs/design/trust.md)."""
    return secrets.randbits(SEED_BITS)


def valid_info(info):
    return (isinstance(info, dict) and "error" not in info and isinstance(info.get("type"), str)
            and isinstance(info.get("ver"), int) and isinstance(info.get("rules"), list)
            and isinstance(info.get("min"), int) and isinstance(info.get("max"), int))


class Table:
    def __init__(self, tid, plugin, rules, options, seats, seed):
        self.id = tid
        self.plugin = plugin
        self.type = plugin.info["type"]
        self.ver = plugin.info["ver"]
        self.rules = rules
        self.options = options
        self.seed = seed
        self.seats = [None] * seats
        self.watchers = set()
        self.status = "open"     # open | playing | done
        self.state = None
        self.seq = 0
        self.events = []
        self.cond = asyncio.Condition()
        self.created = self.touched = time.time()
        self.turn_secs = Limits.turn_default
        self.turn_seat = -1
        self.deadline = 0.0
        self.vis = "public"      # public | private (seats for invited players only)
        self.invites = {}        # player key -> {"name", "from", "seat" (-1: any), "at"}
        self.owner = ""          # the player who created it (may always sit at their private table)
        self.gone = set()        # seats that left a running game while the others play on (GONE events)
        self.notes = []          # [(n, at, who, text)], oldest first; who "" for the core (NOTE)
        self.data = {}           # a little data kept with the table: key -> value (TDL, TDS, TDD)
        self.steps = (-1, [])    # (seat, events) of a staged turn not yet ended, for its turn record (TRN)
        self.clients = {}        # seat -> (program, machine) its player said they play with (CLI; CLIENT events)

    def seat_of(self, name):
        return self.seats.index(name) if name in self.seats else -1

    @property
    def no_integrity(self):
        """Why this table is marked integrity=false (INT), or None for a table that is not."""
        v = self.data.get(INTEGRITY_KEY, "")
        return v.split(" ", 1)[1] if v.startswith("false ") else None

    def held(self, but=None):
        """Seats reserved for invited players (other than `but`, a player key)."""
        return {i["seat"] for k, i in self.invites.items() if i["seat"] >= 0 and k != but}

    def visible_to(self, name):
        """Public, or private and this player created it, is seated or is invited."""
        return (self.vis == "public" or self.seat_of(name) >= 0 or name.lower() in self.invites
                or name.lower() == self.owner.lower())

    def add_event(self, text):
        self.seq += 1
        self.events.append((self.seq, text))
        del self.events[:-Limits.event_keep]
        self.touched = time.time()

    def lines_since(self, n, hide=False):
        """The events after n as EVT lines. hide: the reader may not see what the players play with (a private
        table's, for someone neither seated there nor its creator), so each CLIENT event is only its seat."""
        return ["EVT %s %d %s" % (self.id, s, " ".join(t.split()[:2]) if hide and t.startswith("CLIENT ") else t)
                for s, t in self.events if s > n]


class Session:
    """One connection. Who it is signed in as is the accounts component's
    business (conn.signin); the core reads it with accounts.current()."""

    def __init__(self, writer, frontend=None, fingerprint=None, source=None, eol=b"\r\n"):
        self.writer = writer
        self.eol = eol                    # the line ending it sends: the client's own on the public port
        self.frontend = frontend          # the frontend's key name on the frontend channel
        self.fingerprint = fingerprint    # of the key it used (a rotated key closes the connection)
        self.source = source              # the client's address (public port; SRC on the frontend channel)
        self.player = None
        self.signin = None
        self.pending = None
        self.ses_fails = 0
        self.mails = 0
        self.can = STEP_KINDS             # the sign-in step kinds this client can show (CAN)
        self.prog = ""                    # what the client says it is (CLI PROG), cleaned; "" if it did not say
        self.mach = ""                    # ... and the machine it runs on (CLI MACH)
        self.cursor = {}
        self.tokens = Limits.rate_burst
        self.stamp = time.time()
        self.opened = time.monotonic()    # when the connection opened (TIM)
        self.closing = False

    def allow(self):
        now = time.time()
        self.tokens = min(Limits.rate_burst, self.tokens + (now - self.stamp) * Limits.rate_per_sec)
        self.stamp = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class Server:
    def __init__(self, plugin_dir, name="parlornet", db=":memory:", auth="password", auth_label=None, signup=None):
        self.default_name = name
        self.plugin_dir = plugin_dir
        self.plugins = {}
        self.tables = {}
        self.sessions = set()
        self.store = Store(db)
        self.next_id = int(self.store.get("next_id", "1"))
        self.conns = 0
        self.frontend_conns = set()
        self.frontend_count = {}          # server extension name -> its connections
        self.handshakes = frontends.Handshakes(Limits.handshakes_per_source, Limits.max_handshakes)
        self.admin_conns = 0
        self.started = time.time()
        self.settings = self.store.settings()
        self.name = self.settings.get("server.name") or name
        self.accounts = Accounts(self.store, self.settings, self.name, auth, auth_label, signup)
        self.accounts.places = self.signin_places      # where sign-in mail says to sign in
        self.players = self.accounts.players
        self.default_motd = ""
        self.keyring = None
        self.public_port = None           # the public port (PARLORNET/1 in PRL), once serve() listens on it
        self.protocols = {}               # extension connection -> {key: [port, name, description]} (PRO, PRD)
        self.notices = []                 # [(seq, kind, table, name, who, type/ver, client)] for NFY, oldest first
        self.notice_seq = 0
        self.notice_cond = asyncio.Condition()

    # ---- settings and sign-in -----------------------------------------------
    # Every setting the admin panel changes (parlord/admin.py) takes effect at
    # once; the command line only gives the defaults.
    @property
    def auth(self):
        return self.accounts.method
    def use_auth(self):
        self.accounts.use_method()

    def set_setting(self, key, value):
        if value is None:
            self.settings.pop(key, None)
        else:
            self.settings[key] = value
        self.store.set_setting(key, value)
        if key.startswith("auth."):
            self.use_auth()
        if key == "server.name":
            self.name = value or self.default_name
            self.accounts.server_name = self.name

    def motd(self):
        """The message of the day as the operator wrote it (the admin menus edit this)."""
        return self.settings.get("server.motd", self.default_motd)

    def contribute_url(self):
        """site.contribute_url (the admin menus or the settings file), else PARLOR_CONTRIBUTE_URL, else the
        project's home (PROJECT_URL). Set to nothing ("") in any of those, it is cleared: "" here."""
        if "site.contribute_url" in self.settings:
            return (self.settings["site.contribute_url"] or "").strip()
        return os.environ.get("PARLOR_CONTRIBUTE_URL", PROJECT_URL).strip()

    def signin_places(self):
        """Where a player can sign in and choose a password, for sign-in mail: the web and telnet as the
        server extensions declared them (PRO), at the server's name, e.g. ["on the web at
        http://parlornet.org:6500/", "by telnet to parlornet.org port 6502"]; [] when neither is up."""
        out = []
        for key, port, _, _ in self.protocol_list():
            if key == "HTTP":
                out.append("on the web at http://%s%s/" % (self.name, "" if port == 80 else ":%d" % port))
            elif key == "TELNET":
                out.append("by telnet to %s port %d" % (self.name, port))
        return out

    def docs_url(self):
        """site.docs_url, the documentation site, found as contribute_url is: the setting, else PARLOR_DOCS_URL,
        else DOCS_URL; "" when cleared, or when it is not a usable address (no spaces, printable ASCII, a
        scheme, and short enough for the DOC line). It always ends with /."""
        if "site.docs_url" in self.settings:
            url = (self.settings["site.docs_url"] or "").strip()
        else:
            url = os.environ.get("PARLOR_DOCS_URL", DOCS_URL).strip()
        if not url or "://" not in url or not all(" " < c <= "~" for c in url):
            return ""
        url = url if url.endswith("/") else url + "/"
        return url if len("DOC " + url) <= MAXLINE - 2 else ""

    def motd_lines(self):
        """The message of the day as every frontend shows it: {contribute} replaced by the contribute URL,
        or, when the operator cleared it, every line holding the marker left out; then cut to lines of at most
        MOTD_WIDTH characters, at most MOTD_LINES of them, without trailing blank lines."""
        url = self.contribute_url()
        if any(c in url for c in " \t\r\n") or not all(" " < c <= "~" for c in url):
            url = ""
        lines = [l.replace(MOTD_MARKER, url) for l in self.motd().split("\n") if url or MOTD_MARKER not in l]
        out = wrap(lines, MOTD_WIDTH)
        while out and not out[-1]:
            out.pop()
        return out[:MOTD_LINES]

    def turn_default(self):
        """server.turn_default, at most games.turn_max_days."""
        try:
            secs = int(self.settings.get("server.turn_default", Limits.turn_default))
        except ValueError:
            secs = Limits.turn_default
        return min(secs, self.limit("games.turn_max_days") * 86400)

    def offered(self):
        """{type/ver:rules} of the games this server offers: every installed
        ruleset except those the admin turned off (setting games.off)."""
        off = set(x for x in self.settings.get("games.off", "").split(",") if x)
        return set("%s:%s" % (k, r) for k, p in self.plugins.items() for r in p.info["rules"]
                   if k not in off and "%s:%s" % (k, r) not in off)

    def new_signups_held(self):
        return self.accounts.new_signups_held()

    def kick(self, name):
        """Close every connection signed in as name at once (its sessions have
        already been ended by the accounts component)."""
        for sess in list(self.sessions):
            if sess.player is not None and sess.player.name.lower() == name.lower():
                sess.closing = True
                try:
                    sess.writer.close()
                except Exception:
                    pass

    # ---- account actions for the admin: all through the accounts component ----------
    def suspend_player(self, name, on=True):
        if not self.accounts.suspend(name, on):
            return False
        if on:
            self.kick(name)
        return True

    async def reset_player(self, name):
        """See Accounts.reset; the player's connections are closed."""
        r = await self.accounts.reset(name)
        if r[0]:
            self.kick(name)
        return r

    async def delete_player(self, p):
        """Remove an account: its sessions end, it is signed out, and nothing
        of it passes to whoever takes the name next: its open invitations are
        withdrawn, its running games end (END abandoned), its seats at open
        tables are given up, it no longer owns any table, and private tables
        it played at that are over are removed."""
        self.accounts.delete(p.name)
        self.kick(p.name)
        key = p.name.lower()
        for t in list(self.tables.values()):
            seated = t.seat_of(p.name) >= 0 or any(s and s.lower() == key for s in t.seats)
            if key not in t.invites and not seated and t.owner.lower() != key and p.name not in t.watchers:
                continue
            async with t.cond:
                if key in t.invites:
                    self.drop_invite(t, key, "withdrawn")
                t.watchers.discard(p.name)
                if t.owner.lower() == key:
                    t.owner = ""
                seats = [i for i, s in enumerate(t.seats) if s and s.lower() == key]
                self.forget_clients(t, seats)
                if seats and t.status == "playing":
                    self.event(t, "END abandoned")
                if seats and t.status == "open":
                    for i in seats:
                        t.seats[i] = None
                        t.clients.pop(i, None)
                        self.event(t, "LEAVE %d" % i)
                    self.drop_invites_from(t, p.name)
                gone = (t.status == "open" and not any(t.seats)) or (seats and t.status == "done" and t.vis == "private")
                if gone:
                    del self.tables[t.id]
                    self.store.delete_table(t.id)
                else:
                    self.save(t)
                t.cond.notify_all()

    async def end_table(self, tid):
        """The operator ends a table: a running game ends (END abandoned) and
        stays listed as finished; an open or finished table is removed."""
        t = self.tables.get(tid)
        if t is None:
            return False
        async with t.cond:
            if t.status == "playing":
                self.event(t, "END abandoned")
                self.save(t)
            else:
                del self.tables[tid]
                self.store.delete_table(tid)
            t.cond.notify_all()
        return True

    def player(self, name):
        return self.accounts.player(name)

    async def load_plugins(self):
        """Find the plugins and read each one's info, once (docs/plugins.md).
        A plugin.py is served by the plugin host, shared by all of them, unless
        its info asks for a process of its own ("process": "own") or the host
        cannot serve it; an executable named plugin (any language) always runs
        in its own process. The processes are stopped again here: each starts
        on its first real call."""
        found = []
        for d in sorted(os.listdir(self.plugin_dir)):
            py, exe = os.path.join(self.plugin_dir, d, "plugin.py"), os.path.join(self.plugin_dir, d, "plugin")
            if os.path.isfile(py):
                found.append((d, py))
            elif os.path.isfile(exe) and os.access(exe, os.X_OK):
                found.append((d, exe))
        host = PluginProcess([sys.executable, HOST] + [path for _, path in found if path.endswith(".py")])
        for name, path in found:
            p = info = None
            if path.endswith(".py"):
                try:
                    info = await host.call({"op": "info", "game": name})
                except PluginError:
                    info = None
                if valid_info(info) and info.get("process") != "own":
                    p = Plugin(path, host, name)
            if p is None:
                p = Plugin(path)
                try:
                    info = await p.call({"op": "info"})
                except PluginError:
                    info = None
                await p.process.close()
            if not valid_info(info):
                print("parlord: plugin %s gave no usable info; not offered" % name, file=sys.stderr, flush=True)
                continue
            p.info = info
            self.plugins["%s/%d" % (info["type"], info["ver"])] = p
        await host.close()

    def plugin_idle_secs(self):
        try:
            return float(self.settings.get("plugins.idle", Limits.plugin_idle_secs))
        except ValueError:
            return Limits.plugin_idle_secs

    async def stop_idle_plugins(self):
        """Stop each plugin process that has had no call for plugins.idle
        seconds and serves no game being played; the next call starts it again."""
        limit = self.plugin_idle_secs()
        playing = {id(t.plugin.process) for t in self.tables.values() if t.status == "playing"}
        for proc in {id(p.process): p.process for p in self.plugins.values()}.values():
            if proc.running and id(proc) not in playing and not proc.lock.locked() and proc.idle_for() > limit:
                await proc.close()

    def load_state(self):
        """Players, sessions and tables from the database (after load_plugins)."""
        self.accounts.load()
        for d in self.store.load_tables():
            plugin = self.plugins.get(d["gtype"])
            if plugin is None:
                continue
            t = Table(d["id"], plugin, d["rules"], d["options"], len(d["seats"]), d["seed"])
            t.seats, t.status, t.state, t.seq = d["seats"], d["status"], d["state"], d["seq"]
            t.turn_secs, t.turn_seat, t.deadline = d["turn_secs"], d["turn_seat"], d["deadline"]
            t.created, t.touched = d["created"], d["touched"]
            t.vis, t.owner = d["vis"] or "public", d["owner"] or ""
            t.gone = set(d.get("gone") or [])
            t.invites = self.store.load_invites(t.id)
            t.events = self.store.load_events(t.id, Limits.event_keep)
            t.notes = self.store.load_notes(t.id)
            t.data = self.store.load_tdata(t.id)
            t.clients = {int(k): tuple(v) for k, v in (d.get("clients") or {}).items()
                         if str(k).isdigit() and int(k) < len(t.seats) and t.seats[int(k)]}
            self.tables[t.id] = t

    # ---- persistence helpers -------------------------------------------
    def save_player(self, p):
        self.accounts.save_player(p)

    def save(self, t):
        self.store.save_table(t)

    def drop_invite(self, t, key, why):
        """End an invitation, with an UNINVITE event that says why (UNINVITE_WHY)."""
        inv = t.invites.pop(key)
        self.store.delete_invite(t.id, key)
        self.event(t, "UNINVITE %s %s %s" % (inv["seat"] if inv["seat"] >= 0 else "-", inv["name"], why))

    def drop_invites_from(self, t, name):
        """A player stood up from an open table: the invitations they made there end ("left")."""
        for k in [k for k, i in t.invites.items() if i["from"].lower() == name.lower()]:
            self.drop_invite(t, k, "left")

    def expire_invites(self, t, now):
        """Invitations older than Limits.invite_secs end ("expired"); True if any did."""
        old = [k for k, i in t.invites.items() if now - i["at"] > Limits.invite_secs]
        for k in old:
            self.drop_invite(t, k, "expired")
        return bool(old)

    def add_note(self, t, who, text):
        """A note on a table: who is the player who wrote it, or "" for the core. At Limits.notes the core's
        own note takes the place of the oldest (a player's note is refused before it gets here)."""
        text = "".join(c for c in text if " " <= c <= "~").strip()[:Limits.note_core_len if not who else
                                                                     Limits.note_len]
        while len(t.notes) >= Limits.notes:
            self.store.delete_note(t.id, t.notes.pop(0)[0])
        n = t.notes[-1][0] + 1 if t.notes else 1
        t.notes.append((n, time.time(), who, text))
        self.store.add_note(t.id, n, t.notes[-1][1], who, text)

    @staticmethod
    def note_line(note):
        _, at, who, text = note
        return "NOTE %s %s %s" % (time.strftime("%Y-%m-%d", time.gmtime(at)), who or "-", text)

    def set_data(self, t, key, value):
        """Keep (value None: forget) one key of a table's data."""
        if value is None:
            t.data.pop(key, None)
        else:
            t.data[key] = value
        self.store.set_tdata(t.id, key, value)

    def event(self, t, text, by=None, tied=None):
        """Add an event to a table, track whose turn it is, and store both. by: (who, program, machine) for
        an event a player's move made (CLI), kept with it. Every way a game ends comes here as its END event,
        so this is where its scores are counted (score_game); tied: the seats sharing a draw, when the
        game's plugin names them."""
        t.add_event(text)
        self.store.add_event(t.id, t.seq, text, by)
        self.notice(t, text)
        if text.startswith("END") and text != "END abandoned" and t.vis == "public":
            for name in {s for s in t.seats if s}:     # a game played to its end, for each player's profile
                self.store.bump_stat(name, "game", "%s/%d" % (t.type, t.ver))
        if text.startswith("END ") and text != "END abandoned":
            self.score_game(t, text.split()[1:], tied)
        if text.startswith("TURN "):
            t.turn_seat = int(text.split()[1])
            t.deadline = time.time() + t.turn_secs
        elif text.startswith("GONE "):
            t.gone.add(int(text.split()[1]))
        elif text.startswith("END"):
            t.status = "done"
            t.turn_seat = -1
            t.deadline = 0.0

    def clients_visible(self, sess, t):
        """May this connection see what a table's players play with (CLIENT events, VUE C, LST clients)? At a
        public table everyone may; at a private one its players and its creator (docs/protocol.md, Clients and
        machines)."""
        if t.vis == "public":
            return True
        me = sess.player.name.lower()
        return me == t.owner.lower() or any(s and s.lower() == me for s in t.seats)

    def seat_client(self, t, seat, sess):
        """The player in seat, playing on (a move, a chat line), plays with what this connection said (CLI): when
        that is new for the seat, keep it and tell the table with the event CLIENT seat text (client_say). A
        connection that said nothing leaves the seat's as it was; bots say nothing. True if an event was made.
        Sitting down (cmd_jon) sets the seat's afresh."""
        claim = (sess.prog, sess.mach)
        if bots.is_bot(sess.player) or not any(claim) or t.clients.get(seat) == claim:
            return False
        t.clients[seat] = claim
        self.client_event(t, seat)
        return True

    def client_event(self, t, seat):
        """The event CLIENT seat text: what the seat's player plays with, as much as fits the EVT line."""
        head = "CLIENT %d " % seat
        room = MAXLINE - 2 - len("EVT %s %d %s" % (t.id, t.seq + 1, head))
        self.event(t, head + client_say(*t.clients[seat], most=min(room, Limits.client_say_len)))

    def forget_clients(self, t, seats):
        """An account is deleted: what it played with at these seats goes, from the table and from its CLIENT
        events, which keep only the seat."""
        for i in seats:
            t.clients.pop(i, None)
            self.store.forget_client_events(t.id, i)
        heads = tuple("CLIENT %d " % i for i in seats)
        for k, (seq, text) in enumerate(t.events):
            if heads and text.startswith(heads):
                t.events[k] = (seq, " ".join(text.split()[:2]))

    def client_lines(self, t):
        """CLIENT seat text for each seat whose player said what they play with (VUE C, LST clients)."""
        return ["CLIENT %d %s" % (i, client_say(*t.clients[i])) for i in sorted(t.clients)
                if i < len(t.seats) and t.seats[i] and any(t.clients[i])]

    def notice(self, t, text):
        """The notices an event gives (NFY), before it changes the table's turn: turn, for the seat whose turn
        it is now (who: the seat that had the turn); end, for every seat (who: the seat the result names, or
        another player); join, for the others seated at an open table when a person sits down (who: the one
        who sat down). Bots get none and cause no join."""
        f = text.split()
        names, out = t.seats, []
        bot = set(bots.seats(self.players, names))

        def other(i, pick=None):
            if pick is not None and pick != i and 0 <= pick < len(names) and names[pick]:
                return names[pick]
            return next((n for j, n in enumerate(names) if n and j != i), "-")
        if f[0] == "TURN" and len(f) > 1 and f[1].isdigit():
            seat, prev = int(f[1]), t.turn_seat
            if seat < len(names) and names[seat] and seat not in bot:
                out.append(("turn", names[seat], names[prev] if 0 <= prev < len(names) and prev != seat
                            and names[prev] else "-"))
        elif f[0] == "END":
            pick = int(f[2]) if len(f) > 2 and f[2].isdigit() else None
            out += [("end", n, other(i, pick)) for i, n in enumerate(names) if n and i not in bot]
        elif f[0] == "JOIN" and len(f) > 2 and t.status == "open" and not bots.is_name(f[2]):
            out += [("join", n, f[2]) for i, n in enumerate(names) if n and n != f[2] and i not in bot]
        # what the one who sat down plays with (CLIENT): the notice goes to players seated there, who may see it
        say = client_say(*t.clients[int(f[1])]) if f[0] == "JOIN" and f[1].isdigit() and int(f[1]) in t.clients \
            else ""
        for kind, name, who in out:
            self.notice_seq += 1
            self.notices.append((self.notice_seq, kind, t.id, name, who, "%s/%d" % (t.type, t.ver),
                                 say if kind == "join" else ""))
        if out:
            del self.notices[:-Limits.notices_keep]
            try:
                asyncio.get_running_loop().create_task(self._notices_ready())
            except RuntimeError:        # no loop (a test calling event() directly): nobody can be waiting
                pass

    async def _notices_ready(self):
        async with self.notice_cond:
            self.notice_cond.notify_all()

    # ---- scores (TOP, PFL SCORE; docs/protocol.md, Scores) --------------------------------------------------
    def points(self, reason):
        """The points for a result (SCORE_REASONS): the setting scores.win, .draw, .loss or .resign (a timeout
        scores as a resignation), a whole number from 0 to SCORE_MOST; unset or anything else: its default."""
        key = "scores.resign" if reason == "timeout" else "scores." + reason
        v = str(self.settings.get(key) or "").strip()
        return int(v) if v.isdigit() and int(v) <= SCORE_MOST else SCORE_SETTINGS[key]

    def scores_shown(self):
        """scores.show: the leaderboard (TOP) and the scores on profiles (PFL SCORE) are shown; on by default."""
        return str(self.settings.get("scores.show") or "on").strip().lower() not in ("off", "0", "no")

    @staticmethod
    def score_list(words, n):
        """The per-seat scores at the end of a result ("6-6-2-2", "10/-2/10", "12 -3 -9"), or None."""
        text = " ".join(words)
        parts = text.split("/") if "/" in text else words if len(words) == n else text.split("-")
        try:
            nums = [int(x) for x in parts]
        except ValueError:
            return None
        return nums if len(nums) == n else None

    @staticmethod
    def game_results(t, end, tied=None):
        """How a game that ended with END end came out for each seat: {seat: reason}, the reasons of
        SCORE_REASONS. A seat that left a game flagged drop earlier (GONE) has the reason it left with. For
        the seats still playing: after END resign or END timeout, that seat has it and the others win; after
        END win, that seat wins and the others lose; after END draw, the seats that share the draw have it and
        the others lose. The seats sharing a draw are the ones the plugin named (tied), else the seats still
        playing with the top score the result lists, else every seat still playing. {} when the end says
        nothing the core can read."""
        n = len(t.seats)
        left = {}
        for _, e in t.events:
            f = e.split()
            if f[0] == "GONE" and len(f) > 2 and f[1].isdigit():
                left[int(f[1])] = "timeout" if f[2] == "timeout" else "resign"
        out = {i: left.get(i, "resign") for i in t.gone if 0 <= i < n and t.seats[i]}
        live = [i for i in range(n) if i not in t.gone and t.seats[i]]
        kind = end[0] if end else ""
        seat = int(end[1]) if len(end) > 1 and end[1].isdigit() and int(end[1]) < n else None
        if kind in ("resign", "timeout") and seat is not None:
            out.update({i: kind if i == seat else "win" for i in live})
        elif kind == "win" and seat is not None:
            out.update({i: "win" if i == seat else "loss" for i in live})
        elif kind == "draw":
            share = [i for i in (tied or []) if isinstance(i, int) and i in live]
            if not share and len(live) > 2:
                nums = Server.score_list(end[1:], n)
                if nums:
                    top = max(nums[i] for i in live)
                    share = [i for i in live if nums[i] == top]
            share = share or live
            out.update({i: "draw" if i in share else "loss" for i in live})
        else:
            return {}
        return out

    def score_game(self, t, end, tied=None):
        """A game ended (END end): each seated player's points for it, counted at once (Store.add_scores).
        Bots are not scored; a player in two seats counts once, by the first."""
        rows, seen = [], set()
        for seat, reason in sorted(self.game_results(t, end, tied).items()):
            name = t.seats[seat]
            if name.lower() in seen or bots.is_bot(self.players.get(name.lower())):
                continue
            seen.add(name.lower())
            rows.append((name, self.points(reason), reason))
        if rows:
            self.store.add_scores(t.id, "%s/%d" % (t.type, t.ver), rows)

    def leaderboard(self, gtype=None):
        """[(rank, name, points, wins, draws, losses, resigns, games)] of the players with a score (bots and
        names with no account left out), the most points first; players with as many points share a rank."""
        out, rank, last = [], 0, None
        for i, row in enumerate(r for r in self.store.scoreboard(gtype) if self.players.get(r[0])
                                and not bots.is_bot(self.players.get(r[0]))):
            if row[1] != last:
                rank, last = i + 1, row[1]
            out.append((rank, self.players[row[0]].name) + tuple(row[1:]))
        return out

    # ---- housekeeping -------------------------------------------------
    async def sweep_forever(self):
        while True:
            await asyncio.sleep(Limits.sweep_secs)
            await self.sweep()

    async def sweep(self):
        now = time.time()
        self.accounts.sweep()
        if self.keyring is not None:            # rotated or removed frontend keys: close their connections
            self.keyring.load()
            for sess in list(self.frontend_conns):
                if self.keyring.fingerprint(sess.frontend) != sess.fingerprint:
                    sess.closing = True
                    try:
                        sess.writer.transport.abort()
                    except Exception:
                        pass
            if self.keyring.stamp is not None:      # the directory was read (not a passing error)
                self.end_stale_sessions()
        for tid, t in list(self.tables.items()):
            idle = now - t.touched
            if t.status == "open" and idle > self.limit("games.open_hours") * 3600:
                del self.tables[tid]
                self.store.delete_table(tid)
            elif t.status == "done" and idle > Limits.done_keep_secs:
                del self.tables[tid]
                self.store.delete_table(tid)
            elif t.status == "open" and t.invites and now - min(i["at"] for i in t.invites.values()) > Limits.invite_secs:
                async with t.cond:
                    if t.status == "open" and self.expire_invites(t, now):
                        self.save(t)
                        t.cond.notify_all()
            elif t.status == "playing" and t.deadline and now > t.deadline:
                async with t.cond:
                    if t.status == "playing" and now > t.deadline:
                        await self.leave_game(t, t.turn_seat, "timeout")
                        self.save(t)
                        t.cond.notify_all()
        await self.stop_idle_plugins()

    async def leave_game(self, t, seat, why):
        """A seat resigns or runs out of time (why). With more than two seats still playing and a plugin
        flagged "drop", the plugin drops that seat and the others play on (GONE seat why); otherwise, or
        if the plugin refuses, the game ends for everyone: END resign|timeout seat. Call with t.cond held."""
        if "drop" in (t.plugin.info.get("flags") or []) and len(t.seats) - len(t.gone) > 2:
            r = await t.plugin.call({"op": "drop", "rules": t.rules, "state": t.state, "seat": seat, "why": why})
            if "error" not in r and "state" in r:
                t.state = r["state"]
                for ev in r.get("events", []):
                    self.event(t, ev)
                t.gone.add(seat)
                if "end" in r:
                    self.event(t, "END " + r["end"], tied=r.get("tied"))
                return
        self.event(t, "END %s %d" % (why, seat))

    def end_stale_sessions(self):
        """Sessions issued under a key that has since been rotated or removed
        end (a captured session id dies with the key it was issued under)."""
        names = {rec.frontend for rec in self.accounts.sessions.values() if rec.frontend}
        for name in names:
            fp = self.keyring.fingerprint(name)
            if fp is None and os.path.exists(os.path.join(self.keyring.dir, name + ".key")):
                continue        # there but unreadable or malformed (a hand edit): not a removal
            n = self.accounts.end_frontend(name, fp)
            if n:
                print("parlord: %d session%s of server extension %s ended (its key changed)"
                      % (n, "" if n == 1 else "s", name), file=sys.stderr, flush=True)

    # ---- connections --------------------------------------------------
    async def handle_frontend(self, reader, writer):
        """The frontend channel: TLS 1.3 with the frontend's pre-shared key
        first; then each server extension within its own share."""
        if len(self.frontend_conns) + len(self.handshakes) >= Limits.max_frontend_conns:
            writer.transport.abort()
            return
        self.handshakes.per_source, self.handshakes.total = Limits.handshakes_per_source, Limits.max_handshakes
        src = self.handshakes.enter(writer)
        try:
            who = await frontends.accept(reader, writer, self.keyring, "frontend")
        finally:
            self.handshakes.leave(src, writer)
        if who is None:
            return
        name = who[0]
        if self.frontend_count.get(name, 0) >= Limits.max_conns_per_frontend:
            writer.transport.abort()
            return
        sess = Session(writer, *who)
        self.frontend_conns.add(sess)
        self.frontend_count[name] = self.frontend_count.get(name, 0) + 1
        try:
            await self.serve_conn(reader, writer, sess)
        finally:
            self.frontend_conns.discard(sess)
            self.frontend_count[name] -= 1
            if not self.frontend_count[name]:
                del self.frontend_count[name]

    async def handle(self, reader, writer):
        """The public port: plain text for 8-bit clients. The client sends PARLORNET/1 first, whose line
        ending sets the connection's (parlord/native.py); then the greeting."""
        if self.conns >= Limits.max_conns:
            writer.write(b"ERR BUSY server is full\r\n")
            writer.close()
            return
        self.conns += 1
        try:
            lines = native.Lines(reader, MAXLINE)
            try:
                eol = await lines.start(Limits.first_line_secs, Limits.ending_secs)
            except asyncio.TimeoutError:
                eol = None
                writer.write(native.opening_error(*native.WRONG, self.docs_url()))
            except native.BadOpening as e:
                eol = None
                writer.write(native.opening_error(e.code, e.text, self.docs_url()))
            if eol is None:
                writer.close()
                return
            peer = writer.get_extra_info("peername")
            sess = Session(writer, source=source_key(peer[0]) if peer else None, eol=eol)
            await self.serve_conn(reader, writer, sess, lines)
        except (ConnectionError, OSError):
            pass
        finally:
            self.conns -= 1

    async def serve_conn(self, reader, writer, sess, lines=None):
        """lines: the public port's reader (native.Lines); the extension channel reads CR LF lines."""
        self.sessions.add(sess)
        try:
            await self.send(sess, [self.greeting()])
            while not sess.closing:
                good = True
                try:
                    if lines is not None:
                        got = await lines.line(Limits.idle_secs)
                        raw, good = got if got is not None else (b"", True)     # b"": the end of input
                    else:
                        raw = await asyncio.wait_for(reader.readline(), Limits.idle_secs)
                except asyncio.TimeoutError:
                    await self.send(sess, [err("IDLE", "closing")])
                    break
                except (ValueError, asyncio.LimitOverrunError, native.TooLong):
                    await self.send(sess, [err("TOOLONG", "lines are 80 bytes at most")])
                    break
                if not raw and (lines is None or got is None):
                    break
                if len(raw) > MAXLINE + 2:
                    await self.send(sess, [err("TOOLONG", "lines are 80 bytes at most")])
                    break
                line = raw.decode("ascii", "replace").strip()
                if not line and good:
                    continue
                if not sess.allow():
                    await self.send(sess, [err("RATE", "slow down")])
                    continue
                if not good:
                    await self.send(sess, [err("NEWLINE", "lines end with %s here" % native.NAMES[sess.eol])])
                    continue
                try:
                    out = await self.dispatch(sess, line)
                except PluginError:
                    out = [err("PLUGIN", "game failed, try again")]
                except Exception as e:  # never let one request take the server down
                    # the verb only: a sign-in line carries a credential or a session id
                    print("internal error on %s: %s" % (line.split()[0][:4].upper(), type(e).__name__),
                          file=sys.stderr)
                    out = [err("INTERNAL", "")]
                await self.send(sess, out)
        except (ConnectionError, OSError):
            pass
        finally:
            self.sessions.discard(sess)
            self.protocols.pop(sess, None)        # its protocols are listed while it is connected
            if sess.player:
                sess.player.conns -= 1
                sess.player.last_seen = time.time()
                self.save_player(sess.player)
            self.accounts.connection_closed(sess)
            try:
                writer.close()
            except Exception:
                pass

    def greeting(self):
        """PARLORNET/1 name AUTH=... SIGNUP=... [LABEL=...], within the protocol's
        line limit: a long label is shortened (the name is at most 32)."""
        g = "PARLORNET/%d %s %s" % (VERSION, self.name, self.auth.greeting())
        if len(g) > MAXLINE - 2 and " LABEL=" in g:
            head, label = g.rsplit(" LABEL=", 1)
            room = MAXLINE - 2 - len(head) - len(" LABEL=")
            g = head + (" LABEL=" + label[:room] if room >= 4 else "")
        return g[:MAXLINE - 2]

    async def send(self, sess, lines):
        data = b"".join(l.encode("ascii", "replace") + sess.eol for l in lines)
        sess.writer.write(data)
        await sess.writer.drain()

    # ---- request dispatch ---------------------------------------------
    async def dispatch(self, sess, line):
        parts = line.split()
        verb = parts[0].upper()
        args = parts[1:]
        fn = getattr(self, "cmd_" + verb.lower(), None)
        if fn is None or not verb.isalpha():
            return [err("UNKNOWN", "no such request")]
        ended, player = self.accounts.current(sess)       # every request asks the accounts component
        if player is not sess.player:
            if sess.player:
                sess.player.conns -= 1
            sess.player = player
            if player:
                player.conns += 1
        if ended:
            sess.cursor = {}
            return [err("SIGNEDOUT", "your session has ended; sign in again")]
        if verb not in SIGNIN_VERBS and sess.player is None:
            return [err("NOHELO", "say HELO first")]
        out = await fn(sess, args, line)
        if sess.player is not None and (verb in ACTIVE_VERBS or (verb in ("PRF", "PFL") and args[:1] and
                                                                  args[0].upper() in ("SET", "DEL"))):
            sess.player.active = time.time()
        return out

    def table_for(self, args):
        if not args:
            return None, [err("SYNTAX", "table id needed")]
        t = self.tables.get(args[0])
        if t is None:
            return None, [err("NOTABLE", "no such table")]
        return t, None

    async def cmd_helo(self, sess, args, line, verb="HELO", mode="any"):
        """HELO name: sign in, or sign up a name that has no account. USER name: sign in only
        (an unknown name is ERR NOPLAYER). REG name: sign up only (a taken name is ERR NAMEINUSE)."""
        if sess.player:
            return [err("STATE", "already said HELO")]
        most = 1 if mode == "signup" else 2
        if not args or not NAME_RE.match(args[0]) or len(args) > most:
            return [err("SYNTAX", "%s name%s; name is letters, digits, - and _ up to 12"
                        % (verb, "" if mode == "signup" else " [credential]"))]
        given = args[1] if len(args) > 1 else None
        if given and not WORD_RE.match(given):
            return [err("SYNTAX", "%s name [credential]" % verb)]
        full = self.server_full(args[0])
        if full:
            return [full]
        return self.signed(sess, await self.accounts.begin(sess, args[0], given, mode))

    async def cmd_user(self, sess, args, line):
        return await self.cmd_helo(sess, args, line, "USER", "signin")

    async def cmd_reg(self, sess, args, line):
        return await self.cmd_helo(sess, args, line, "REG", "signup")

    async def cmd_can(self, sess, args, line):
        """The sign-in step kinds this client can show: text, secret, yn, info.
        A step it cannot show is refused (ERR CANNOT) instead of sent."""
        kinds = set(",".join(args).lower().split(",")) - {""}
        if not kinds or not kinds <= STEP_KINDS:
            return [err("SYNTAX", "CAN kind[,kind...]; kinds are text, secret, yn, info")]
        sess.can = frozenset(kinds)
        return [ok(",".join(sorted(kinds)))]

    async def cmd_src(self, sess, args, line):
        """SRC address (extension channel only, before signing in): the address
        of the client this connection serves, for the per-address limits on
        sign-up and mail. The public port uses the connection's own address."""
        if not sess.frontend:
            return [err("NOTFRONTEND", "SRC is only for server extensions")]
        if sess.player or sess.pending:
            return [err("STATE", "SRC comes before HELO")]
        if len(args) != 1 or not SRC_RE.match(args[0]) or source_key(args[0]) is None:
            return [err("SYNTAX", "SRC address")]
        sess.source = source_key(args[0])
        return [ok()]

    async def cmd_ses(self, sess, args, line):
        """Resume a session (frontend channel only)."""
        if sess.player:
            return [err("STATE", "already signed in")]
        if len(args) != 1:
            return [err("SYNTAX", "SES session")]
        return self.signed(sess, self.accounts.resume(sess, args[0]), active=False)    # resuming is not activity

    async def cmd_out(self, sess, args, line):
        """End this session, or with 'all' every session of the player."""
        if args and args[0].lower() != "all":
            return [err("SYNTAX", "OUT [all]")]
        r = self.accounts.sign_out(sess, everywhere=bool(args))
        if sess.player:
            sess.player.conns -= 1
            sess.player = None
        sess.cursor = {}
        return [err(r[1], r[2])] if r[0] == "err" else [ok(*r[2])]

    async def cmd_motd(self, sess, args, line):
        """The operator's message of the day, as lines of at most MOTD_WIDTH
        characters (so each MOTD line fits the protocol's 80 bytes)."""
        return ["MOTD " + l if l else "MOTD" for l in self.motd_lines()] + [ok()]

    async def cmd_tim(self, sess, args, line):
        """TIM: how long the player has been connected and the server's time, for a client with no clock of
        its own: TIM secs now, then OK. No database, and not activity (dispatch, ACTIVE_VERBS)."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return ["TIM %d %s" % (self.connected_secs(sess), now), ok()]

    @staticmethod
    def connected_secs(sess):
        """TIM's secs, whole seconds. On the public port the sign-in lasts as long as the connection, so it
        is the connection's age. On the extension channel a server extension may serve one player over many
        connections (the web opens one for every page and resumes the session with SES), so a connection
        signed in counts from the session's start: the sign-in on that server extension, which is where the
        player's connection to it began as far as the core knows. Before signing in, the connection's age."""
        rec = sess.signin
        if sess.frontend and rec is not None and rec.hash is not None:
            start = rec.since
        else:
            start = sess.opened
        return max(0, int(time.monotonic() - start))

    # ---- protocols: what each connected server extension serves (PRO, PRD, PRL) ------------------
    # docs/protocol.md, Protocols; docs/design/protocol-declarations.md
    @staticmethod
    def _printable(text, most):
        return 0 < len(text) <= most and all(" " <= c <= "~" for c in text)

    async def cmd_pro(self, sess, args, line):
        """PRO key port name (extension channel only): this connection serves the protocol key on the public
        port port, under name. PRL lists it for as long as the connection is open. Declaring a key again
        replaces its port and name and keeps its description."""
        if not sess.frontend:
            return [err("NOTFRONTEND", "PRO is only for server extensions")]
        parts = line.strip().split(None, 3)
        if (len(parts) < 4 or not PROTO_KEY_RE.match(parts[1]) or not re.match(r"^[0-9]{1,5}\Z", parts[2])
                or not 0 < int(parts[2]) < 65536 or not self._printable(parts[3].strip(), PROTO_NAME_LEN)):
            return [err("SYNTAX", "PRO key port name; a name is 1 to %d printable characters" % PROTO_NAME_LEN)]
        mine = self.protocols.setdefault(sess, {})
        if parts[1] not in mine and len(mine) >= PROTO_PER_CONN:
            return [err("LIMIT", "at most %d protocols a connection" % PROTO_PER_CONN)]
        mine[parts[1]] = [int(parts[2]), parts[3].strip(), mine.get(parts[1], [0, "", ""])[2]]
        return [ok()]

    async def cmd_prd(self, sess, args, line):
        """PRD key text (extension channel only): the one-line description of a protocol this connection
        declared with PRO."""
        if not sess.frontend:
            return [err("NOTFRONTEND", "PRD is only for server extensions")]
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not self._printable(parts[2].strip(), PROTO_DESC_LEN):
            return [err("SYNTAX", "PRD key text; the text is 1 to %d printable characters" % PROTO_DESC_LEN)]
        entry = self.protocols.get(sess, {}).get(parts[1])
        if entry is None:
            return [err("NOPROTO", "declare the protocol with PRO first")]
        entry[2] = parts[2].strip()
        return [ok()]

    def protocol_list(self):
        """[(key, port, name, description)]: the core's own PARLORNET/1 on the public port, and every protocol a
        connected server extension declared, by port and then key, each key and port once (two connections
        declaring the same one: the first, or the first with a description)."""
        out = {}
        if self.public_port:
            out[(NATIVE_PROTOCOL[0], self.public_port)] = NATIVE_PROTOCOL[1:]
        for decl in list(self.protocols.values()):
            for key, (port, name, desc) in decl.items():
                if (key, port) not in out or (desc and not out[(key, port)][1]):
                    out[(key, port)] = (name, desc)
        return [(k, p, n, d) for (k, p), (n, d) in sorted(out.items(), key=lambda kv: (kv[0][1], kv[0][0]))]

    async def cmd_prl(self, sess, args, line):
        """The protocols this server speaks now: DOC url (the documentation site, unless the operator cleared
        it), then PRO key port name and PRD key text for each, then OK. Allowed before signing in."""
        url = self.docs_url()
        out = ["DOC " + url] if url else []
        for key, port, name, desc in self.protocol_list():
            out.append("PRO %s %d %s" % (key, port, name))
            if desc:
                out.append("PRD %s %s" % (key, desc))
        return out + [ok()]

    async def cmd_aut(self, sess, args, line):
        """The answer to an ASK: sign-in, sign-up, or a password change. The value
        is the rest of the line as typed (a password may hold spaces)."""
        if len(args) < 2:
            return [err("SYNTAX", "AUT field value")]
        return self.signed(sess, await self.accounts.answer(sess, args[0].lower(), line.split(None, 2)[2][:64]))

    async def cmd_pwd(self, sess, args, line):
        """Change the password (sign-in password): the server asks for the current
        one, a new one and the new one again."""
        return self.signed(sess, self.accounts.change(sess))

    def signed_in(self, p):
        """Signed in now: a live connection on any frontend, or a session that has not ended (a web session)."""
        return p.conns > 0 or bool(self.accounts.by_player.get(p.name.lower()))

    def server_full(self, name):
        """ERR BUSY when players.online_max players are signed in and name is not one of them; else None.
        Bots neither count nor are refused."""
        if bots.is_name(name):
            return None
        p = self.players.get(name.lower())
        if p is not None and self.signed_in(p):
            return None
        most = self.limit("players.online_max")
        n = sum(1 for q in self.players.values() if not bots.is_bot(q) and self.signed_in(q))
        if n < most:
            return None
        return err("BUSY", "wait=60 the server is full (%d players), try again later" % most)

    def signed(self, sess, r, active=True):
        if r[0] == "ask":
            field, label, secret = r[1:4]
            extra = r[4] if len(r) > 4 else {}
            out = ["INF " + l if l else "INF" for l in wrap(extra.get("info", []) + extra.get("note", []),
                                                              MAXLINE - 6)]
            return out + [("ASK %s %s%s%s%s%s" % (field, label.replace(" ", "_"), " secret" if secret else "",
                                                  " yn" if extra.get("yn") else "",
                                                  " cred" if extra.get("cred") else "",
                                                  " new" if extra.get("new") else ""))[:MAXLINE - 2]]
        if r[0] == "err":
            return [err(r[1], r[2])]
        if r[0] == "done":
            return [ok()]
        sess.player = r[1]
        sess.player.conns += 1
        if active:
            sess.player.active = time.time()    # signing in is activity
        return [ok(*r[2])]

    async def cmd_bye(self, sess, args, line):
        sess.closing = True
        return [ok()]

    async def cmd_gam(self, sess, args, line):
        out = []
        offered = self.offered()
        for key, p in self.plugins.items():
            i = p.info
            for rules in i["rules"]:
                if "%s:%s" % (key, rules) not in offered:
                    continue
                flags = ",".join(i.get("flags") or [])
                out.append(("GAM %s %s %s %d %d %s" % (key, rules, i["name"].replace(" ", "_"),
                                                         i["min"], i["max"], flags)).rstrip())
        return out + [ok()]

    LST_SYNTAX = "LST [open|playing] [mine] [game=type] [player=name] [table=id]"   # [clients]: no room

    async def cmd_lst(self, sess, args, line):
        """The tables open or playing that the asker may see (docs/protocol.md, LST), filtered by the words
        given: open or playing, mine (seated, invited or the creator), game=type[/ver], player=name (seated
        there; a private table only if the asker is seated too), table=id. First the asker's own tables and
        invitations, all of them; then the others, open ones newest first, then games being played newest
        first, at most lists.max of them, and MORE n when n more are left out. all (the extension channel
        only) lists every one of the others."""
        me = sess.player.name
        key = me.lower()
        status = game = who = tid = None
        mine = every = clients = False
        for a in args:
            k, eq, v = a.partition("=")
            k, low = k.lower(), a.lower()
            if low in ("open", "playing") and status is None:
                status = low
            elif low == "mine":
                mine = True
            elif low == "clients" and not clients:
                clients = True
            elif low == "all":
                if sess.frontend is None:
                    return [err("NOTFRONTEND", "LST all is only for server extensions")]
                every = True
            elif eq and k == "game" and game is None and v:
                game = v.lower()
            elif eq and k == "player" and who is None and NAME_RE.match(v):
                who = v.lower()
            elif eq and k == "table" and tid is None and v.isdigit():
                tid = v
            else:
                return [err("SYNTAX", self.LST_SYNTAX)]
        own, others = [], []
        for t in self.tables.values():
            if t.status == "done" or not t.visible_to(me) or (tid is not None and t.id != tid):
                continue
            if (status and t.status != status) or (game and game not in (t.type, "%s/%d" % (t.type, t.ver))):
                continue
            seated = any(s and s.lower() == key for s in t.seats)
            if who is not None and (not any(s and s.lower() == who for s in t.seats)
                                    or (t.vis == "private" and not seated)):
                continue
            is_mine = seated or key in t.invites or t.owner.lower() == key
            if mine and not is_mine:
                continue
            (own if is_mine else others).append(t)
        own.sort(key=lambda t: int(t.id))
        others.sort(key=lambda t: (t.status != "open", -int(t.id)))
        most = len(others) if every else self.limit("lists.max")
        out = []
        for t in own + others[:most]:
            out.append(self.tab_line(t))
            if clients and self.clients_visible(sess, t):   # what each seated player plays with
                out += self.client_lines(t)
        if len(others) > most:
            out.append("MORE %d" % (len(others) - most))
        return out + [ok()]

    def tab_line(self, t):
        filled = sum(1 for s in t.seats if s)
        names = ",".join(s or "-" for s in t.seats)
        return "TAB %s %s/%d %s %d/%d %s %s turn=%d%s%s%s" % (
            t.id, t.type, t.ver, t.rules, filled, len(t.seats), t.status, names, t.turn_secs,
            " vis=private" if t.vis == "private" else "", self.bot_field(t),
            " integrity=false" if t.no_integrity is not None else "")

    def bot_field(self, t):
        """' bot=1,2': the seats held by bots (parlord/bots.py), for LST; '' when there are none."""
        b = bots.seats(self.players, t.seats)
        return " bot=" + ",".join(map(str, b)) if b else ""

    async def cmd_new(self, sess, args, line):
        if len(args) < 2:
            return [err("SYNTAX", "NEW type/ver rules [key=val ...]")]
        key = args[0] if "/" in args[0] else args[0] + "/1"
        plugin = self.plugins.get(key)
        offered = self.offered()
        if plugin is None or not any(o.startswith(key + ":") for o in offered):
            return [err("NOGAME", "this server does not host that game")]
        if args[1] not in plugin.info["rules"] or "%s:%s" % (key, args[1]) not in offered:
            return [err("NORULES", "unknown ruleset")]
        if len(self.tables) >= self.limit("games.tables_max"):
            return [err("FULL", "too many tables, try later")]
        opts = {}
        turn = self.turn_default()
        vis = "public"
        seats = plugin.info["min"]
        for kv in args[2:8]:
            k, _, v = kv.partition("=")
            if not (k and v and len(k) <= 12 and len(v) <= 12 and k.isalnum()):
                return [err("BADOPT", "options are key=val")]
            if k == "turn":
                turn = parse_duration(v)
                most = self.limit("games.turn_max_days")
                if turn is None or not Limits.turn_min <= turn <= most * 86400:
                    return [err("BADOPT", "turn is a time per turn, 1m to %dd (like 5m, 1h, 1d)" % most)]
                continue
            if k == "players":
                if not v.isdigit() or not plugin.info["min"] <= int(v) <= plugin.info["max"]:
                    return [err("BADOPT", "players is %d to %d for this game"
                                % (plugin.info["min"], plugin.info["max"]))]
                seats = int(v)
                continue
            if k == "vis":
                vis = v.lower()
                if vis not in ("public", "private"):
                    return [err("BADOPT", "vis is public or private")]
                continue
            opts[k] = v
        e = self.seat_limit(sess.player, plugin, starts=seats <= 1)     # the creator sits down next (JON)
        if e:
            return [e]
        probe = await plugin.call({"op": "new", "rules": args[1], "players": seats, "options": opts, "seed": 0})
        if "error" in probe:
            return [err("BADOPT", probe["error"])]
        t = Table(str(self.next_id), plugin, args[1], opts, seats, table_seed())
        t.turn_secs = turn
        t.vis = vis
        t.owner = sess.player.name
        self.next_id += 1
        self.store.set("next_id", self.next_id)
        self.tables[t.id] = t
        if opts.get("racks") == "open":         # every seated player's view will carry every rack and the bag
            self.disclose(t, OPEN_RACKS_WHY)
        self.save(t)
        return [ok(t.id)]

    def limit(self, key):
        """A setting of SETTING_LIMITS (the admin menus or the settings file): a whole number from 1; unset,
        empty, 0 or not a number: its default in Limits."""
        try:
            n = int(str(self.settings.get(key) or "0").strip())
        except ValueError:
            n = 0
        return n if n >= 1 else getattr(Limits, SETTING_LIMITS[key][0])

    def seat_limit(self, p, plugin, starts):
        """The ERR LIMIT line if player p may not sit down at a table of plugin's game now, else None. Refused
        while p is playing games.active_max games (tables playing where p holds a seat they have not left), or,
        when sitting down leaves p waiting (the game does not start at once), while p is seated at
        games.waiting_per_type open tables of that game type. A game that starts never counts against p at
        that moment: tables p waits at may all fill and start, so p can play more than games.active_max,
        by at most the tables they were waiting at. Bots are exempt."""
        if bots.is_bot(p):
            return None
        key, game = p.name.lower(), (plugin.info["type"], plugin.info["ver"])
        playing = waiting = 0
        for t in self.tables.values():
            mine = [i for i, s in enumerate(t.seats) if s and s.lower() == key]
            if not mine:
                continue
            if t.status == "playing" and any(i not in t.gone for i in mine):
                playing += 1
            elif t.status == "open" and (t.type, t.ver) == game:
                waiting += 1
        most = self.limit("games.active_max")
        if playing >= most:
            games = "%d game%s" % (playing, "" if playing == 1 else "s")
            return err("LIMIT", ("you are playing %s, the most allowed here; finish one first" % games)
                       if playing == most else
                       "you are playing %s, over the %d allowed; finish one first" % (games, most))
        if not starts and waiting >= self.limit("games.waiting_per_type"):
            room = MAXLINE - 2 - len("ERR LIMIT ")
            tables = "%d %%s table%s" % (waiting, "" if waiting == 1 else "s")
            for name in (plugin.info["name"], plugin.info["type"]):      # the game's name, if it fits
                for text in ("you are already waiting at %s; start or leave one",
                             "you are waiting at %s; start or leave one"):
                    if len(text % (tables % name)) <= room:
                        return err("LIMIT", text % (tables % name))
            return err("LIMIT", "you are waiting at %d tables of this game" % waiting)
        return None

    async def cmd_jon(self, sess, args, line):
        t, e = self.table_for(args)
        if e:
            return e
        async with t.cond:
            if t.status != "open":
                return [err("CLOSED", "table is not open")]
            name = sess.player.name
            key = name.lower()
            if t.seat_of(name) >= 0:
                return [err("STATE", "already seated")]
            inv = t.invites.get(key)
            if t.vis == "private" and inv is None and key != t.owner.lower():
                return [err("PRIVATE", "this table is by invitation only")]
            held = t.held(but=key)
            if len(args) > 1:
                if not args[1].isdigit() or int(args[1]) >= len(t.seats):
                    return [err("SYNTAX", "JON table [seat]")]
                seat = int(args[1])
                if t.seats[seat] is not None:
                    return [err("TAKEN", "that seat is taken")]
                if seat in held:
                    return [err("TAKEN", "that seat is held for an invited player")]
            elif inv and inv["seat"] >= 0 and t.seats[inv["seat"]] is None:
                seat = inv["seat"]
            else:
                free = [i for i, s_ in enumerate(t.seats) if s_ is None and i not in held]
                if not free:
                    return [err("TAKEN", "the free seats are held for invited players")]
                seat = free[0]
            starts = None not in [s_ if i != seat else name for i, s_ in enumerate(t.seats)]
            e = self.seat_limit(sess.player, t.plugin, starts)
            if e:
                return [e]
            if starts:
                r = await t.plugin.call({"op": "new", "rules": t.rules, "players": len(t.seats), "options": t.options,
                                         "seed": t.seed})
                if "error" in r:
                    return [err("BADOPT", r["error"])]
            else:
                r = None
            t.seats[seat] = name
            if inv is not None:                 # used up
                del t.invites[key]
                self.store.delete_invite(t.id, key)
            t.clients.pop(seat, None)
            if not bots.is_bot(sess.player) and (sess.prog or sess.mach):
                t.clients[seat] = (sess.prog, sess.mach)     # for the join notice (NFY)
            self.event(t, "JOIN %d %s" % (seat, name))
            if seat in t.clients:               # right after JOIN: what they play with
                self.client_event(t, seat)
            if r is not None:
                for k in list(t.invites):       # the game starts: invitations end
                    self.drop_invite(t, k, "started")
                t.state = r["state"]
                t.status = "playing"
                self.event(t, "START")
                for ev in r.get("events", []):
                    self.event(t, ev)
            self.save(t)
            t.cond.notify_all()
            sess.cursor[t.id] = t.seq
        return [ok(seat)]

    async def cmd_lve(self, sess, args, line):
        """Stand up from an open table, or stop watching. A running game is not
        affected: the player stays seated and can come back (RES resigns)."""
        t, e = self.table_for(args)
        if e:
            return e
        async with t.cond:
            name = sess.player.name
            seat = t.seat_of(name)
            if seat < 0:
                t.watchers.discard(name)
                return [ok()]
            if t.status == "open":
                t.seats[seat] = None
                t.clients.pop(seat, None)
                self.event(t, "LEAVE %d" % seat)
                self.drop_invites_from(t, name)
                if not any(t.seats):
                    del self.tables[t.id]
                    self.store.delete_table(t.id)
                else:
                    self.save(t)
                t.cond.notify_all()
        return [ok()]

    async def cmd_inv(self, sess, args, line):
        """Invite a player to an open table, optionally to a seat held for them."""
        t, e = self.table_for(args)
        if e:
            return e
        if len(args) < 2 or not NAME_RE.match(args[1]) or (len(args) > 2 and not args[2].isdigit()):
            return [err("SYNTAX", "INV table name [seat]")]
        async with t.cond:
            me = sess.player.name
            if t.seat_of(me) < 0:
                return [err("NOTSEATED", "only a player seated at the table can invite")]
            if t.status != "open":
                return [err("CLOSED", "table is not open")]
            p = self.players.get(args[1].lower())
            if p is None:
                return [err("NOPLAYER", "no player by that name")]
            key = p.name.lower()
            if t.seat_of(p.name) >= 0:
                return [err("STATE", "already seated")]
            seat = -1
            if len(args) > 2:
                seat = int(args[2])
                if seat >= len(t.seats):
                    return [err("SYNTAX", "INV table name [seat]")]
                if t.seats[seat] is not None:
                    return [err("TAKEN", "that seat is taken")]
                if seat in t.held(but=key):
                    return [err("TAKEN", "that seat is held for another invited player")]
            most = Limits.invites_per_free_seat * sum(1 for x in t.seats if x is None)
            if key not in t.invites and len(t.invites) >= most:
                return [err("LIMIT", "this table has %d invitations out, twice its free seats" % len(t.invites))]
            t.invites[key] = {"name": p.name, "from": me, "seat": seat, "at": time.time()}
            self.store.save_invite(t.id, key, t.invites[key])
            self.event(t, "INVITE %s %s" % (seat if seat >= 0 else "-", p.name))
            self.save(t)
            t.cond.notify_all()
        return [ok("offline")] if not self.online(p) else [ok()]     # it waits for them (IVL), up to its expiry

    async def cmd_unv(self, sess, args, line):
        """Withdraw an invitation (a seated player), or decline one (the invited player)."""
        t, e = self.table_for(args)
        if e:
            return e
        if len(args) < 2 or not NAME_RE.match(args[1]):
            return [err("SYNTAX", "UNV table name")]
        async with t.cond:
            me = sess.player.name
            key = args[1].lower()
            if t.seat_of(me) < 0 and key != me.lower():
                return [err("NOTSEATED", "only a player seated at the table can withdraw an invitation")]
            if key not in t.invites:
                return [err("NOINVITE", "no such invitation")]
            self.drop_invite(t, key, "declined" if key == me.lower() else "withdrawn")
            self.save(t)
            t.cond.notify_all()
        return [ok()]

    async def cmd_ivl(self, sess, args, line):
        """This player's invitations."""
        key = sess.player.name.lower()
        out = []
        for t in sorted(self.tables.values(), key=lambda t: (len(t.id), t.id)):
            inv = t.invites.get(key)
            if inv and t.status == "open":
                out.append("IVT %s %s/%d %s %s %s%s" % (t.id, t.type, t.ver, t.rules, inv["from"], t.status,
                                                        " seat=%d" % inv["seat"] if inv["seat"] >= 0 else ""))
            ask = self.racks_ask(t) if t.status == "playing" else None
            seat = t.seat_of(sess.player.name)
            if ask and seat >= 0 and seat not in ask[0] and seat in self.racks_humans(t):
                out.append("IVR %s %s/%d %s %s racks" % (t.id, t.type, t.ver, t.rules, t.seats[ask[0][0]]))
        return out + [ok()]

    def presence_idle(self):
        """presence.idle: seconds without activity after which a player is no longer online."""
        try:
            return max(1.0, float(self.settings.get("presence.idle", Limits.presence_idle)))
        except ValueError:
            return float(Limits.presence_idle)

    def online(self, p, now=None):
        """Online: signed in (a live connection on any frontend, or a web session not ended) and active in
        the last presence.idle seconds (ACTIVE_VERBS, signing in). A bot is online while it is connected."""
        if p is None or getattr(p, "suspended", False):
            return False
        if bots.is_bot(p):
            return p.conns > 0
        if p.conns <= 0 and not self.accounts.by_player.get(p.name.lower()):
            return False
        return (now or time.time()) - getattr(p, "active", 0.0) <= self.presence_idle()

    async def cmd_act(self, sess, args, line):
        """The player did something here that needs no other request (a frontend's own command, such as
        a chat between two of its players): it counts as activity (dispatch). OK."""
        return [ok()]

    async def cmd_who(self, sess, args, line):
        """The players online now (online()): PLR name idle=secs [bot], by name, at most players.online_max
        and the bots; idle is the seconds since the player's last activity (0 for a bot)."""
        now = time.time()
        out = []
        for p in sorted(self.players.values(), key=lambda p: p.name.lower()):
            if self.online(p, now):
                b = bots.is_bot(p)
                out.append("PLR %s idle=%d%s" % (p.name, 0 if b else max(0, int(now - getattr(p, "active", now))),
                                                  " bot" if b else ""))
        return out[:self.limit("players.online_max") + len(bots.NAMES)] + [ok()]

    async def cmd_res(self, sess, args, line):
        t, e = self.table_for(args)
        if e:
            return e
        async with t.cond:
            seat = t.seat_of(sess.player.name)
            if seat < 0:
                return [err("NOTSEATED", "you are not playing at this table")]
            if t.status != "playing":
                return [err("NOGAME", "the game is not running")]
            if seat in t.gone:
                return [err("STATE", "you have already left this game")]
            await self.leave_game(t, seat, "resign")
            self.save(t)
            t.cond.notify_all()
            lines = t.lines_since(sess.cursor.get(t.id, 0), not self.clients_visible(sess, t))
            sess.cursor[t.id] = t.seq
        return lines + [ok()]

    async def cmd_see(self, sess, args, line):
        t, e = self.table_for(args)
        if e:
            return e
        name = sess.player.name
        if not t.visible_to(name) and name not in t.watchers:
            return [err("PRIVATE", "this table is by invitation only")]
        if len(t.watchers) >= 20:
            return [err("FULL", "too many watchers")]
        t.watchers.add(sess.player.name)
        sess.cursor[t.id] = t.seq
        return [ok()]

    @staticmethod
    def end_words(t):
        """How a game the server ended came to an end, in words, from its END event."""
        end = next((e[4:].split() for _, e in reversed(t.events) if e.startswith("END ")), [])
        who = t.seats[int(end[1])] if len(end) > 1 and end[1].isdigit() and int(end[1]) < len(t.seats) else None
        if end[:1] == ["resign"] and who:
            return "%s resigned" % who
        if end[:1] == ["timeout"] and who:
            return "%s ran out of time" % who
        return "the game was ended"

    def _seat_for_view(self, sess, t):
        seat = t.seat_of(sess.player.name)
        if seat < 0 and sess.player.name not in t.watchers:
            return None
        return seat

    async def cmd_vue(self, sess, args, line):
        t, e = self.table_for(args)
        if e:
            return e
        seat = self._seat_for_view(sess, t)
        if seat is None:
            return [err("NOTAT", "sit or watch first")]
        flags = "".join(args[1:]).upper()
        out = ["V %s/%d %d" % (t.type, t.ver, t.seq)]
        if t.state is None:
            out.append("S Waiting for players")
        else:
            call = {"op": "view", "rules": t.rules, "state": t.state, "seat": seat}
            if "O" in flags and sess.frontend and seat >= 0 and t.no_integrity is not None:
                call["open"] = True     # every rack and the bag, for the extension whose program receives them
            r = await t.plugin.call(call)
            wire, status = r.get("wire", []), r.get("status", "")
            if t.status == "done" and "Game over" not in status:
                # The server ended the game (a resignation, a clock run out, the operator): the game's own
                # state never heard of it, so the view says it is over instead of whose move it was.
                wire = ["W -1" if l.startswith("W ") else l for l in wire]
                status = "Game over: " + self.end_words(t)
            if "G" in flags:
                out += r.get("geometry", [])
            out += wire
            b = bots.seats(self.players, t.seats)
            if b:                       # the seats held by bots (parlord/bots.py)
                out.append("BOT " + " ".join(map(str, b)))
            if t.status == "playing" and t.turn_seat >= 0:
                out.append("CLK %d %d %d" % (t.turn_seat, max(0, int(t.deadline - time.time())), t.turn_secs))
            if "T" in flags:
                out.append("S " + status)
            if "M" in flags and seat >= 0 and t.status == "playing":
                m = await t.plugin.call({"op": "moves", "rules": t.rules, "state": t.state, "seat": seat})
                out += ["M " + " ".join(mv) if isinstance(mv, list) else "M " + mv
                        for mv in m.get("moves", [])]
        if "C" in flags and self.clients_visible(sess, t):     # what each player plays with
            out += self.client_lines(t)
        if t.no_integrity is not None:      # INT: the table is marked integrity=false, and why
            out.append("INT false " + t.no_integrity)
        sess.cursor[t.id] = t.seq
        return out + [ok()]

    async def cmd_mov(self, sess, args, line):
        return await self._move(sess, args, check=False)

    async def cmd_chk(self, sess, args, line):
        return await self._move(sess, args, check=True)

    async def _move(self, sess, args, check):
        t, e = self.table_for(args)
        if e:
            return e
        if len(args) < 2:
            return [err("SYNTAX", "MOV table move")]
        async with t.cond:
            seat = t.seat_of(sess.player.name)
            if seat < 0:
                return [err("NOTSEATED", "you are not playing at this table")]
            if t.status != "playing":
                return [err("NOGAME", "the game is not running")]
            if seat in t.gone:
                return [err("STATE", "you have left this game")]
            if any(a.startswith("!") for a in args[1:]) and not (len(args) == 2 and args[1] in ("!undo", "!end")):
                return [err("SYNTAX", "bad move")]
            r = await t.plugin.call({"op": "apply", "rules": t.rules, "state": t.state, "seat": seat,
                                     "move": args[1:], "check": check})
            if "error" in r:
                return [err("ILLEGAL", r["error"])]
            if check:
                return [ok(r.get("info", ""))]
            t.state = r["state"]
            self.seat_client(t, seat, sess)     # playing on with another program or machine: the table hears
            by = (sess.player.name, sess.prog, sess.mach)
            made = list(r.get("events", []))
            for ev in made:
                self.event(t, ev, by)
            done = not r.get("staged")
            if "end" in r:
                self.event(t, "END " + r["end"], by, tied=r.get("tied"))
                done = True
            await self.turn_made(t, seat, sess, made, done)
            self.save(t)
            t.cond.notify_all()
            since = sess.cursor.get(t.id, 0)
            lines = t.lines_since(since, not self.clients_visible(sess, t))
            sess.cursor[t.id] = t.seq
        return lines + [ok(r.get("staged") or "")]

    # ---- the turns played: who, with which program, on which machine (TRN, FEED) -----------------------
    async def turn_made(self, t, seat, sess, events, done):
        """A move was played: its events join the seat's turn; when the turn is over (done: the move was not
        a step of a staged turn still going on), the turn is recorded (Store.add_turn), with the player's
        program and machine as the connection said them (CLI) and the turn in words from the game's plugin
        (say), and counted for the player's profile at a public table. A turn that made no event (a ship placed
        before the game) is not recorded: there is nothing public to say about it."""
        prev_seat, steps = t.steps
        evs = (steps if prev_seat == seat else []) + list(events)
        if not done:
            t.steps = (seat, evs)
            return
        t.steps = (-1, [])
        if not evs:
            return
        name = sess.player.name
        row = {"table_id": t.id, "seq": t.seq, "at": time.time(), "gtype": "%s/%d" % (t.type, t.ver), "vis": t.vis,
               "seat": seat, "name": name, "client": sess.prog, "machine": sess.mach,
               "say": await self.say(t, seat, evs)}
        self.store.add_turn(row, Limits.turns_keep)
        if t.vis == "public":
            self.store.bump_stat(name, "prog", sess.prog, row["at"])
            self.store.bump_stat(name, "mach", sess.mach, row["at"])

    async def say(self, t, seat, events):
        """A turn in a few words, without the player's name ("played FRAULEIN for 65"), from the game's plugin
        when its info says it can ("say": true; docs/plugins.md), worked out from the turn's public events
        only; "" otherwise. Never the move's own words: they may carry what is hidden (a ship placed)."""
        if not t.plugin.info.get("say"):
            return ""
        try:
            r = await t.plugin.call({"op": "say", "rules": t.rules, "seat": seat, "events": events})
        except PluginError:
            return ""
        text = r.get("say") if isinstance(r, dict) else None
        return clean_claim(text, Limits.say_len) if isinstance(text, str) else ""

    def claims_visible(self, sess, vis, players):
        """May this connection see the programs and machines of a table's turns? At a public table everyone
        may; at a private one only the players at it (players: the names seated there, or who played turns
        there once the table is gone)."""
        if vis == "public":
            return True
        me = sess.player.name.lower()
        return any(p and p.lower() == me for p in players)

    @staticmethod
    def turn_lines(row, claims):
        """A turn as lines: TRN table seq time seat name type/ver, then TRX MOVE text (the turn in words), and
        with claims TRX PROG text and TRX MACH text; each TRX only when there is something to say."""
        out = ["TRN %s %d %s %d %s %s" % (row["table_id"], row["seq"], utc_stamp(row["at"]), row["seat"],
                                          row["name"], row["gtype"])]
        if row["say"]:
            out.append("TRX MOVE " + row["say"])
        if claims and row["client"]:
            out.append("TRX PROG " + row["client"])
        if claims and row["machine"]:
            out.append("TRX MACH " + row["machine"])
        return [l[:MAXLINE - 2] for l in out]

    async def cmd_trn(self, sess, args, line):
        """TRN table [n]: the table's turns after event number n (0: from the start), oldest first, at most the
        latest Limits.turns_list: turn_lines each, then OK count. The programs and machines only at a public
        table, or for the players at a private one (claims_visible). A table that is gone is still answered
        from the turns kept, to those who could see it."""
        if not args or len(args) > 2 or (len(args) > 1 and not args[1].isdigit()):
            return [err("SYNTAX", "TRN table [n]")]
        after = int(args[1]) if len(args) > 1 else 0
        t = self.tables.get(args[0])
        if t is not None:
            if not self.may_see(sess, t):
                return [err("PRIVATE", "this table is by invitation only")]
            rows = self.store.table_turns(t.id, after, Limits.turns_list)
            claims = self.claims_visible(sess, t.vis, t.seats)
        else:
            rows = self.store.table_turns(args[0], 0, Limits.turns_keep)
            if not rows:
                return [err("NOTABLE", "no such table")]
            players = [r["name"] for r in rows]
            vis = "public" if all(r["vis"] == "public" for r in rows) else "private"
            if not self.claims_visible(sess, vis, players):
                return [err("PRIVATE", "this table is by invitation only")]
            claims = True
            rows = [r for r in rows if r["seq"] > after][-Limits.turns_list:]
        out = []
        for r in rows:
            out += self.turn_lines(r, claims)
        return out + [ok(len(rows))]

    async def cmd_feed(self, sess, args, line):
        """FEED [n [name]]: the latest n turns at public tables (Limits.feed_default; at most Limits.feed_max),
        newest first, as TRN gives them, then OK count; with a name, only that player's. A private table's
        turns are never in it."""
        if len(args) > 2 or (args and not args[0].isdigit()) or (len(args) > 1 and not NAME_RE.match(args[1])):
            return [err("SYNTAX", "FEED [n [name]]")]
        n = min(Limits.feed_max, max(1, int(args[0]))) if args else Limits.feed_default
        rows = self.store.recent_turns(n, args[1] if len(args) > 1 else None)
        out = []
        for r in rows:
            out += self.turn_lines(r, True)
        return out + [ok(len(rows))]

    # ---- what a client says it is (CLI) ----------------------------------------------------------------
    async def cmd_cli(self, sess, args, line):
        """CLI: what this connection said it is, CLI PROG text and CLI MACH text, then OK. CLI PROG text: the
        client program and its version; CLI MACH text: the machine. Each is cleaned (clean_claim); nothing
        after the word forgets it. Allowed before signing in. Only a label for the turns this connection plays
        (TRN, FEED, PFL): it is never believed about anything else (docs/design/trust.md)."""
        if not args:
            return ["CLI %s %s" % (f, v) for f, v in (("PROG", sess.prog), ("MACH", sess.mach)) if v] + [ok()]
        field = args[0].upper()
        if field not in CLAIM_FIELDS:
            return [err("SYNTAX", "CLI, CLI PROG text or CLI MACH text")]
        parts = line.strip().split(None, 2)
        value = clean_claim(parts[2] if len(parts) > 2 else "")
        if field == "PROG":
            sess.prog = value
        else:
            sess.mach = value
        return [ok()]

    # ---- profiles (PFL) ------------------------------------------------------------------------
    async def cmd_pfl(self, sess, args, line):
        """PFL [name]: a player's profile (yourself without a name): PFL NAME name, PFL PREF text (the
        preferred machine, if they gave one), PFL GAME type/ver n (games played to their end at public tables,
        by type), PFL PROG n text and PFL MACH n text (the programs and machines they played turns with at
        public tables, by turns), the most first, at most Limits.profile_top of each; then OK. PFL SET PREF
        [text]: set your own preferred machine (cleaned as CLI's; nothing forgets it)."""
        if args and args[0].upper() == "SET" and len(args) >= 2:
            if args[1].upper() != "PREF":
                return [err("SYNTAX", "PFL SET PREF text")]
            parts = line.strip().split(None, 3)
            self.store.set_profile_machine(sess.player.name, clean_claim(parts[3] if len(parts) > 3 else ""))
            return [ok()]
        if len(args) > 1 or (args and not NAME_RE.match(args[0])):
            return [err("SYNTAX", "PFL [name] or PFL SET PREF text")]
        p = self.players.get(args[0].lower()) if args else sess.player
        if p is None or getattr(p, "pending", 0):
            return [err("NOPLAYER", "no player by that name")]
        out = ["PFL NAME " + p.name]
        pref = self.store.profile_machine(p.name)
        if pref:
            out.append("PFL PREF " + pref)
        if self.scores_shown() and not bots.is_bot(p):
            pts, wins, draws, losses, resigns, games = self.store.score(p.name)
            rank = next((r[0] for r in self.leaderboard() if r[1] == p.name), None) if games else None
            out.append("PFL SCORE %d %d %d %d %d %s" % (pts, wins, draws, losses, resigns, rank or "-"))
        top = Limits.profile_top
        out += ["PFL GAME %s %d" % (v, n) for v, n in self.store.stats(p.name, "game", top)]
        out += ["PFL PROG %d %s" % (n, v) for v, n in self.store.stats(p.name, "prog", top)]
        out += ["PFL MACH %d %s" % (n, v) for v, n in self.store.stats(p.name, "mach", top)]
        return [l[:MAXLINE - 2] for l in out] + [ok()]

    # ---- the leaderboard (TOP) -------------------------------------------------------------------------
    TOP_SYNTAX = "TOP [n] [game=type]"

    async def cmd_top(self, sess, args, line):
        """TOP [n] [game=type]: the leaderboard, TOP rank name points wins draws losses resigns for the first
        n players (Limits.top_default, at most Limits.top_max), by every game or one; then OK count, the
        players with a score. Bots are never listed. Allowed before signing in; ERR NOSCORES when the setting
        scores.show is off."""
        if not self.scores_shown():
            return [err("NOSCORES", "this server shows no scores")]
        most = game = None
        for a in args:
            k, eq, v = a.partition("=")
            if a.isdigit() and most is None:
                most = int(a)
            elif eq and k.lower() == "game" and game is None and v:
                game = v.lower()
            else:
                return [err("SYNTAX", self.TOP_SYNTAX)]
        most = Limits.top_default if most is None else max(1, min(most, Limits.top_max))
        gtype = None
        if game is not None:
            gtype = next((k for k, p in self.plugins.items() if game in (k, p.info["type"])), None)
            if gtype is None:
                return [err("NOGAME", "this server does not host that game")]
        board = self.leaderboard(gtype)
        return ["TOP %d %s %d %d %d %d %d" % r[:7] for r in board[:most]] + [ok(len(board))]

    async def cmd_und(self, sess, args, line):
        return await self._staged(sess, args, "!undo")

    async def cmd_end(self, sess, args, line):
        return await self._staged(sess, args, "!end")

    async def _staged(self, sess, args, token):
        t, e = self.table_for(args)
        if e:
            return e
        if "staged" not in (t.plugin.info.get("flags") or []):
            return [err("NOSTAGE", "this game has no staged turns")]
        return await self._move(sess, [args[0], token], check=False)

    async def cmd_sin(self, sess, args, line):
        t, e = self.table_for(args)
        if e:
            return e
        if self._seat_for_view(sess, t) is None:
            return [err("NOTAT", "sit or watch first")]
        try:
            n = int(args[1]) if len(args) > 1 else sess.cursor.get(t.id, 0)
        except ValueError:
            return [err("SYNTAX", "SIN table n")]
        lines = t.lines_since(n, not self.clients_visible(sess, t))
        sess.cursor[t.id] = t.seq
        return lines + [ok(t.seq)]

    async def cmd_wat(self, sess, args, line):
        t, e = self.table_for(args)
        if e:
            return e
        if self._seat_for_view(sess, t) is None:
            return [err("NOTAT", "sit or watch first")]
        try:
            n = int(args[1]) if len(args) > 1 else sess.cursor.get(t.id, 0)
            secs = min(30.0, float(args[2])) if len(args) > 2 else 20.0
        except ValueError:
            return [err("SYNTAX", "WAT table n [secs]")]
        try:
            async with t.cond:
                await asyncio.wait_for(t.cond.wait_for(lambda: t.seq > n), secs)
        except asyncio.TimeoutError:
            pass
        lines = t.lines_since(n, not self.clients_visible(sess, t))
        sess.cursor[t.id] = t.seq
        return lines + [ok(t.seq)]

    async def cmd_cht(self, sess, args, line):
        t, e = self.table_for(args)
        if e:
            return e
        seat = self._seat_for_view(sess, t)
        if seat is None:
            return [err("NOTAT", "sit or watch first")]
        text = line.split(None, 2)[2].strip() if len(args) > 1 else ""
        text = "".join(c for c in text if " " <= c <= "~")[:56]
        if not text:
            return [err("SYNTAX", "CHT table text")]
        async with t.cond:
            if seat >= 0 and t.status != "done" and self.seat_client(t, seat, sess):
                self.save(t)
            self.event(t, "CHT %s %s" % (seat if seat >= 0 else "-", text))
            t.cond.notify_all()
        return [ok()]

    # ---- a table's notes (NOTE) --------------------------------------------------------------------
    def may_see(self, sess, t):
        name = sess.player.name
        return t.visible_to(name) or name in t.watchers

    async def cmd_note(self, sess, args, line):
        """NOTE table: the table's notes, NOTE date name text each (name "-" for the core's own), oldest
        first, then OK n. NOTE table text: a seated player adds a note."""
        t, e = self.table_for(args)
        if e:
            return e
        if not self.may_see(sess, t):
            return [err("PRIVATE", "this table is by invitation only")]
        if len(args) == 1:
            return [self.note_line(n) for n in t.notes] + [ok(len(t.notes))]
        name = sess.player.name
        if t.seat_of(name) < 0:
            return [err("NOTSEATED", "only a player seated at the table can add a note")]
        text = line.strip().split(None, 2)[2].strip()
        if len(text) > Limits.note_len or not printable(text):
            return [err("BADNOTE", "a note is 1 to %d printable ASCII characters" % Limits.note_len)]
        if len(t.notes) >= Limits.notes:
            return [err("LIMIT", "this table has %d notes, as many as it keeps" % Limits.notes)]
        self.add_note(t, name, text)
        return [ok(len(t.notes))]

    # ---- a little data kept with a table (TDL, TDS, TDD): the core and server extensions -----------
    def tdata_table(self, sess, args, verb):
        if not sess.frontend:
            return None, [err("NOTFRONTEND", "%s is only for server extensions" % verb)]
        t, e = self.table_for(args)
        if e:
            return None, e
        if not self.may_see(sess, t):
            return None, [err("PRIVATE", "this table is by invitation only")]
        return t, None

    @staticmethod
    def tdata_key(args):
        key = args[1].lower() if len(args) > 1 else ""
        if not TDATA_KEY_RE.match(key) or len(key) > Limits.tdata_key_len:
            return None, [err("BADKEY", "a key is 1 to %d of a-z 0-9 _ -" % Limits.tdata_key_len)]
        if key in TDATA_CORE:
            return None, [err("RESERVED", "the core keeps %s itself" % key)]
        return key, None

    async def cmd_tdl(self, sess, args, line):
        """TDL table (extension channel only): the table's data, TDL key value each, by key, then OK."""
        t, e = self.tdata_table(sess, args, "TDL")
        if e:
            return e
        return ["TDL %s %s" % kv for kv in sorted(t.data.items())] + [ok()]

    async def cmd_tds(self, sess, args, line):
        """TDS table key value (extension channel only): keep a value with the table."""
        t, e = self.tdata_table(sess, args, "TDS")
        if e:
            return e
        key, e = self.tdata_key(args)
        if e:
            return e
        parts = line.strip().split(None, 3)
        value = parts[3].strip() if len(parts) > 3 else ""
        if not value or len(value) > Limits.tdata_value_len or not printable(value):
            return [err("BADVALUE", "a value is 1 to %d printable ASCII characters" % Limits.tdata_value_len)]
        if key not in t.data and sum(1 for k in t.data if k not in TDATA_CORE) >= Limits.tdata_keys:
            return [err("LIMIT", "at most %d keys a table; TDD one first" % Limits.tdata_keys)]
        self.set_data(t, key, value)
        return [ok()]

    async def cmd_tdd(self, sess, args, line):
        """TDD table key (extension channel only): forget a key of the table's data."""
        t, e = self.tdata_table(sess, args, "TDD")
        if e:
            return e
        key, e = self.tdata_key(args)
        if e:
            return e
        if key in t.data:
            self.set_data(t, key, None)
        return [ok()]

    # ---- a table marked integrity=false (INT) ------------------------------------------------------------
    def disclose(self, t, why):
        """Mark a table integrity=false: the server gives out something it normally keeps (every rack, the bag),
        and why says what. One way: the mark stays for the rest of the game and the game goes on as before. The
        first reason is kept; marking again changes nothing. The core shows the mark (LST, VUE), writes a note
        and tells the table with the event INT false why. The caller saves the table."""
        if t.no_integrity is not None:
            return False
        self.set_data(t, INTEGRITY_KEY, "false " + why)
        self.add_note(t, "", "Integrity: " + why)
        self.event(t, "INT false " + why)
        return True

    async def cmd_int(self, sess, args, line):
        """INT table false text (extension channel only): the extension's program receives what the server
        normally keeps at this table (Networdz is sent every rack and the bag), so the table is marked
        integrity=false, with text saying what (docs/protocol.md, Tables marked integrity=false). Only a player
        seated there or its creator may, before the game is over. One way: OK when the table is marked already,
        which keeps its first reason."""
        if not sess.frontend:
            return [err("NOTFRONTEND", "INT is only for server extensions")]
        t, e = self.table_for(args)
        if e:
            return e
        parts = line.strip().split(None, 3)
        why = "".join(c for c in (parts[3] if len(parts) > 3 else "") if " " <= c <= "~").strip()
        if len(parts) < 3 or parts[2].lower() != "false" or not why:
            return [err("SYNTAX", "INT table false text")]
        if len(why) > Limits.integrity_why_len:
            return [err("BADVALUE", "the text is 1 to %d printable ASCII characters" % Limits.integrity_why_len)]
        async with t.cond:
            name = sess.player.name
            if t.seat_of(name) < 0 and name.lower() != t.owner.lower():
                return [err("NOTSEATED", "only the table's creator or a player seated there can mark it")]
            if t.status == "done":
                return [err("STATE", "the game is over")]
            if self.disclose(t, why):
                self.save(t)
                t.cond.notify_all()
        return [ok()]

    # ---- opening the racks of a running table (OPR) ----------------------------------------------
    @staticmethod
    def racks_ask(t):
        """A pending request to open the racks, from the table's data: ([seats that asked or agreed, the
        asker first], what for), or None."""
        f = t.data.get("racks", "").split(" ", 2)
        if len(f) < 2 or f[0] != "ask":
            return None
        return [int(x) for x in f[1].split(",") if x.isdigit()], f[2] if len(f) > 2 else ""

    def racks_humans(self, t):
        """The seats whose players must agree to open the racks: every player still in the game but bots."""
        return [i for i, s in enumerate(t.seats)
                if s and i not in t.gone and not bots.is_bot(self.players.get(s.lower()))]

    async def cmd_opr(self, sess, args, line):
        """OPR table [text]: a seated player asks to open the racks of a running game (every seat's view then
        shows every rack and the bag in draw order, as racks=open from the start does), or agrees to another
        player's request; text says what for. With every other player a bot, or once each player has agreed,
        the racks open: OK open. Otherwise OK asked, and the others are asked (IVL). OPR table no declines the
        request (the player who asked withdraws it). One way: open racks are never hidden again."""
        t, e = self.table_for(args)
        if e:
            return e
        parts = line.strip().split(None, 2)
        rest = parts[2].strip() if len(parts) > 2 else ""
        no = rest.lower() == "no"
        why = "" if no else "".join(c for c in rest if " " <= c <= "~").strip()[:Limits.racks_why_len].strip()
        async with t.cond:
            name = sess.player.name
            seat = t.seat_of(name)
            if seat < 0:
                return [err("NOTSEATED", "you are not playing at this table")]
            if t.status != "playing":
                return [err("NOGAME", "the game is not running")]
            if seat in t.gone:
                return [err("STATE", "you have left this game")]
            if "racks" not in (t.plugin.info.get("options") or []):
                return [err("NORACKS", "this game has no racks to open")]
            if t.options.get("racks") == "open":
                return [err("STATE", "the racks are open already")] if no else [ok("open")]
            ask = self.racks_ask(t)
            if no:
                if ask is None:
                    return [err("NOASK", "nobody has asked to open the racks")]
                self.set_data(t, "racks", None)
                self.event(t, "RACKS no %d" % seat)
                self.add_note(t, "", "%s %s" % (name, "withdrew the request to open the racks"
                                                 if seat == ask[0][0] else "declined to open the racks"))
                self.save(t)
                t.cond.notify_all()
                return [ok()]
            agreed, purpose = ask if ask else ([], why)
            if seat in agreed:
                return [ok("asked")]
            agreed = agreed + [seat]
            if all(i in agreed for i in self.racks_humans(t)):
                r = await t.plugin.call({"op": "option", "rules": t.rules, "state": t.state, "key": "racks",
                                         "value": "open"})
                if "error" in r or "state" not in r:
                    return [err("NORACKS", r.get("error", "this game cannot open its racks"))]
                t.state = r["state"]
                t.options = dict(t.options, racks="open")
                self.set_data(t, "racks", None)
                if ask:
                    self.event(t, "RACKS yes %d" % seat)
                self.event(t, "RACKS open %d" % agreed[0])
                self.add_note(t, "", "%s agreed: the racks are open" % name if ask else
                              "%s opened the racks%s" % (name, " for " + purpose if purpose else ""))
                self.disclose(t, OPEN_RACKS_WHY)
                answer = "open"
            else:
                self.set_data(t, "racks", "ask %s %s" % (",".join(map(str, agreed)), purpose))
                if ask:
                    self.event(t, "RACKS yes %d" % seat)
                    self.add_note(t, "", "%s agreed to open the racks" % name)
                else:
                    self.event(t, ("RACKS ask %d %s" % (seat, purpose)).rstrip())
                    self.add_note(t, "", "%s asked to open the racks%s" % (name, " for " + purpose if purpose
                                                                           else ""))
                answer = "asked"
            self.save(t)
            t.cond.notify_all()
        return [ok(answer)]

    # ---- notices for players away from the table (NFY) ----------------------------------------
    async def cmd_nfy(self, sess, args, line):
        """NFY: the current notice number. NFY n [secs]: the notices after n (waiting up to secs, at most
        Limits.notice_wait, while there are none) for the players who listed their kind under the PRF key
        notify in this connection's namespace: NFY seq kind table name token who type/ver lines, then OK seq.
        Extension channel only; a number past the last (the core restarted) counts from 0."""
        if not sess.frontend:
            return [err("NOTFRONTEND", "NFY is only for server extensions")]
        if not args:
            return [ok(self.notice_seq)]
        try:
            n = int(args[0])
            secs = min(float(Limits.notice_wait), max(0.0, float(args[1]))) if len(args) > 1 else 0.0
        except ValueError:
            return [err("SYNTAX", "NFY [n [secs]]")]
        if n > self.notice_seq:
            n = 0
        if secs and self.notice_seq <= n:
            try:
                async with self.notice_cond:
                    await asyncio.wait_for(self.notice_cond.wait_for(lambda: self.notice_seq > n), secs)
            except asyncio.TimeoutError:
                pass
        ns, wants, out = self.pref_ns(sess), {}, []
        for seq, kind, tid, name, who, typ, say in self.notices:
            if seq <= n:
                continue
            key = name.lower()
            if key not in wants:
                words = (self.store.pref_get(ns, key, NOTICE_PREF) or "").split()
                wants[key] = (set(words[0].split(",")) if words else set(),
                              words[1] if len(words) > 1 and NOTICE_TOKEN_RE.match(words[1]) else "-")
            kinds, token = wants[key]
            text = "NFY %d %s %s %s %s %s %s" % (seq, kind, tid, name, token, who, typ)
            if kind in kinds and len(text) <= MAXLINE - 2:
                out.append(text)
                if say:             # what the one who sat down plays with, for the line before
                    out.append("NFX CLIENT " + say)
        return out + [ok(self.notice_seq)]

    # ---- a little data per extension and player (PRF) ------------------------------------
    @staticmethod
    def pref_ns(sess):
        """The namespace a connection's PRF requests use: the server extension's key name, the one its
        TLS handshake proved (never a word of the request), or NATIVE_NS on the public port. The two web
        frontends, the 1997 pages (web) and ParlorNet Play (play), share one namespace (PREF_SHARED), so a
        player's choices there, such as the Grid Words colors, follow them from one to the other."""
        ns = sess.frontend or NATIVE_NS
        return PREF_SHARED.get(ns, ns)

    async def cmd_prf(self, sess, args, line):
        """PRF GET key | SET key value | DEL key | LST: the signed-in player's data in this connection's
        namespace (pref_ns). There is no way to name another player or another namespace."""
        sub = args[0].upper() if args else ""
        ns, who = self.pref_ns(sess), sess.player.name.lower()
        if sub == "LST" and len(args) == 1:
            return ["PRF %s %s" % kv for kv in self.store.prefs(ns, who)] + [ok()]
        if sub not in ("GET", "SET", "DEL") or len(args) < 2 or (sub != "SET" and len(args) > 2) \
                or (sub == "SET" and len(args) < 3):
            return [err("SYNTAX", "PRF GET key, PRF SET key value, PRF DEL key or PRF LST")]
        key = args[1].lower()
        if not PREF_KEY_RE.match(key) or len(key) > Limits.pref_key_len:
            return [err("BADKEY", "a key is 1 to %d of A-Z a-z 0-9 _ -" % Limits.pref_key_len)]
        if sub == "GET":
            value = self.store.pref_get(ns, who, key)
            return (["PRF %s %s" % (key, value)] if value is not None else []) + [ok()]
        if sub == "DEL":
            self.store.pref_del(ns, who, key)
            return [ok()]
        value = line.strip().split(None, 3)[3]
        if len(value) > Limits.pref_value_len or not all(" " <= c <= "~" for c in value):
            return [err("BADVALUE", "a value is 1 to %d printable ASCII characters" % Limits.pref_value_len)]
        have = dict(self.store.prefs(ns, who))
        have[key] = value
        if len(have) > Limits.pref_keys:
            return [err("LIMIT", "at most %d keys; PRF DEL one first" % Limits.pref_keys)]
        if sum(len(k) + len(v) for k, v in have.items()) > Limits.pref_bytes:
            return [err("LIMIT", "at most %d bytes of keys and values" % Limits.pref_bytes)]
        self.store.pref_set(ns, who, key, value)
        return [ok()]


async def serve(host, port, plugin_dir, name, db=":memory:", auth="password", auth_label=None, admin_port=None,
                motd="", extra_settings=None, admin_telnet=None, frontend_host="127.0.0.1", frontend_port=None,
                keys=None, signup=None, admin_host="127.0.0.1"):
    """frontend_port: the frontend channel (TLS 1.3, a pre-shared key per frontend
    from the keys directory); admin_port: the admin socket (parlord/admin.py), on
    127.0.0.1, also TLS 1.3 but only for keys with the admin role; admin_telnet:
    the admin adapter's port, named in the first-launch banner."""
    from .admin import ADMIN_TELNET_PORT, Admin
    srv = Server(plugin_dir, name, db, auth, auth_label, signup)
    srv.default_motd = motd
    for k, v in (extra_settings or {}).items():
        srv.settings.setdefault(k, v)       # from a settings file (secrets): not written to the database
    srv.name = srv.accounts.server_name = srv.settings.get("server.name") or name
    srv.use_auth()
    await srv.load_plugins()
    srv.load_state()
    servers = [await asyncio.start_server(srv.handle, host, port, limit=1024)]
    srv.public_port = servers[0].sockets[0].getsockname()[1]
    where = "%s:%d" % (host, port)
    if keys and (frontend_port or admin_port):
        srv.keyring = frontends.Keyring(keys)
    if frontend_port and srv.keyring:
        servers.append(await asyncio.start_server(srv.handle_frontend, frontend_host, frontend_port, limit=1024))
        where += ", extensions on %s:%d (TLS 1.3, %d enrolled)" % (frontend_host, frontend_port,
                                                                  len(srv.keyring.load()))
    admin = admin_server = None
    if admin_port and srv.keyring:
        admin = Admin(srv, admin_telnet or ADMIN_TELNET_PORT)
        admin_server = await asyncio.start_server(admin.handle, admin_host, admin_port, limit=65536)
        servers.append(admin_server)
        where += ", admin on %s:%d (TLS 1.3, admin keys)" % (admin_host, admin_port)
    sweeper = asyncio.create_task(srv.sweep_forever())
    print("parlord: %s on %s, games: %s" % (srv.name, where, ", ".join(srv.plugins) or "none"), flush=True)
    if admin:
        admin.print_banner()
    try:
        await asyncio.gather(*(s.serve_forever() for s in servers))
    finally:
        sweeper.cancel()
        if admin_server:
            admin_server.close()
        for p in srv.plugins.values():
            p.stop()
