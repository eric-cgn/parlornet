"""SQLite storage for parlord: players, sessions, tables (with game state),
events, invitations, each table's notes and data, the turns played (who, with which client program, on
which machine), the players' profiles and their scores. Passwords (temporary ones too) are stored as scrypt
hashes, session ids as SHA-256 hashes, and the mail tables' addresses as keyed hashes
(docs/security-design.md, data at rest).

A database in an earlier format (players without the temp column: from before sign-up mailed a
password) is not migrated: it is moved aside to <path>.old-<date and time> and a new one is made. A later
one gains the tables it lacks, and the events table gains the columns it lacks (MIGRATE_COLUMNS), so
everything it held is kept.

Write-through: every change is saved when it happens, and the server loads
everything back on start, so a restart loses nothing."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    key TEXT PRIMARY KEY, name TEXT NOT NULL, cred TEXT NOT NULL DEFAULT '', temp TEXT NOT NULL DEFAULT '',
    pending REAL NOT NULL DEFAULT 0, created REAL NOT NULL, last_seen REAL NOT NULL, email TEXT,
    verified INTEGER NOT NULL DEFAULT 0, suspended INTEGER NOT NULL DEFAULT 0, fails INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0, missed_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tables (
    id TEXT PRIMARY KEY, gtype TEXT NOT NULL, rules TEXT NOT NULL, options TEXT NOT NULL,
    seed INTEGER NOT NULL, seats TEXT NOT NULL, status TEXT NOT NULL, state TEXT,
    seq INTEGER NOT NULL, turn_secs INTEGER NOT NULL, turn_seat INTEGER NOT NULL,
    deadline REAL NOT NULL, created REAL NOT NULL, touched REAL NOT NULL,
    vis TEXT NOT NULL DEFAULT 'public', owner TEXT NOT NULL DEFAULT '', gone TEXT NOT NULL DEFAULT '[]',
    clients TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS invites (
    table_id TEXT NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL, inviter TEXT NOT NULL,
    seat INTEGER NOT NULL, at REAL NOT NULL, PRIMARY KEY (table_id, key));
CREATE TABLE IF NOT EXISTS events (
    table_id TEXT NOT NULL, seq INTEGER NOT NULL, text TEXT NOT NULL, at REAL NOT NULL,
    PRIMARY KEY (table_id, seq));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    hash TEXT PRIMARY KEY, player TEXT NOT NULL, frontend TEXT NOT NULL, method TEXT NOT NULL,
    created REAL NOT NULL, used REAL NOT NULL, fp TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS email_log (addr TEXT NOT NULL, name TEXT NOT NULL, at REAL NOT NULL, new INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'signup', source TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS email_blocked (addr TEXT PRIMARY KEY, why TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS trusted (key TEXT NOT NULL, source TEXT NOT NULL, at REAL NOT NULL,
    PRIMARY KEY (key, source));
CREATE TABLE IF NOT EXISTS prefs (ns TEXT NOT NULL, player TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    PRIMARY KEY (ns, player, key));
CREATE TABLE IF NOT EXISTS signup_cooldown (h TEXT PRIMARY KEY, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notes (table_id TEXT NOT NULL, n INTEGER NOT NULL, at REAL NOT NULL, who TEXT NOT NULL,
    text TEXT NOT NULL, PRIMARY KEY (table_id, n));
CREATE TABLE IF NOT EXISTS tdata (table_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    PRIMARY KEY (table_id, key));
CREATE TABLE IF NOT EXISTS turns (table_id TEXT NOT NULL, seq INTEGER NOT NULL, at REAL NOT NULL, gtype TEXT NOT NULL,
    vis TEXT NOT NULL, seat INTEGER NOT NULL, name TEXT NOT NULL, client TEXT NOT NULL DEFAULT '',
    machine TEXT NOT NULL DEFAULT '', say TEXT NOT NULL DEFAULT '', PRIMARY KEY (table_id, seq));
CREATE INDEX IF NOT EXISTS turns_at ON turns (at);
CREATE TABLE IF NOT EXISTS profiles (player TEXT PRIMARY KEY, machine TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS playstats (player TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
    n INTEGER NOT NULL, last REAL NOT NULL, PRIMARY KEY (player, kind, value));
CREATE TABLE IF NOT EXISTS scores (player TEXT PRIMARY KEY, points INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0, draws INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0,
    resigns INTEGER NOT NULL DEFAULT 0, games INTEGER NOT NULL DEFAULT 0, last REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS scored (table_id TEXT NOT NULL, player TEXT NOT NULL, gtype TEXT NOT NULL,
    points INTEGER NOT NULL, reason TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (table_id, player));
CREATE INDEX IF NOT EXISTS scored_at ON scored (at);
"""
# Columns a table gained after it was first made: (table, column, its definition). A database made before
# them gets them, empty, when the server starts (Store.migrate); the rows it had are kept.
MIGRATE_COLUMNS = [
    ("events", "who", "TEXT NOT NULL DEFAULT ''"),        # the player whose move made the event
    ("events", "client", "TEXT NOT NULL DEFAULT ''"),     # ... the client program they said they used (CLI)
    ("events", "machine", "TEXT NOT NULL DEFAULT ''"),    # ... and the machine
    ("tables", "clients", "TEXT NOT NULL DEFAULT '{}'"),  # what each seat's player plays with (CLIENT events)
]


class Rows:
    """A statement's rows, read while the connection was ours."""

    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class OneAtATime:
    """The connection, one statement at a time: sign-ins run in worker threads next to the event loop,
    and sqlite3 refuses two threads in one connection at once (InterfaceError). Each statement's rows are
    read before the next statement may start."""

    def __init__(self, db):
        self.conn, self.lock = db, threading.Lock()

    def execute(self, *args):
        with self.lock:
            return Rows(self.conn.execute(*args).fetchall())

    def executescript(self, script):
        with self.lock:
            self.conn.executescript(script)

    def close(self):
        with self.lock:
            self.conn.close()


def set_aside(path, now=None):
    """Move a database file of an earlier format (and its -wal and -shm files) aside, to
    <path>.old-<date and time>, so a fresh one is made in its place. Returns the new name."""
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    aside = "%s.old-%s" % (path, time.strftime("%Y%m%d-%H%M%S", time.gmtime(now)))
    os.replace(path, aside)
    for ext in ("-wal", "-shm"):
        if os.path.exists(path + ext):
            os.replace(path + ext, aside + ext)
    return aside


def old_format(db):
    """True if the database holds players in an earlier format (no temp column)."""
    cols = [r[1] for r in db.execute("PRAGMA table_info(players)").fetchall()]
    return bool(cols) and "temp" not in cols


class Store:
    def __init__(self, path=":memory:"):
        if path != ":memory:" and os.path.exists(path):
            probe = sqlite3.connect(path)
            try:
                old = old_format(probe)
            finally:
                probe.close()
            if old:
                aside = set_aside(path)
                print("store: %s was in an earlier format (accounts from before sign-up mailed a password); "
                      "it is now %s, and a new, empty database starts in its place: players, tables and "
                      "settings start over, and the admin menus ask to be set up again" % (path, aside),
                      file=sys.stderr, flush=True)
                self.set_aside = aside
        # sign-in work (mail, hashing) runs in worker threads; statements are short and taken one at a time
        self.db = OneAtATime(sqlite3.connect(path, isolation_level=None, check_same_thread=False))
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA secure_delete=ON")     # a deleted row (a pending account's address) is overwritten
        self.db.executescript(SCHEMA)
        self.migrate()

    def migrate(self):
        """Add the columns of MIGRATE_COLUMNS that a database from an earlier version lacks."""
        for table, col, decl in MIGRATE_COLUMNS:
            have = [r[1] for r in self.db.execute("PRAGMA table_info(%s)" % table).fetchall()]
            if col not in have:
                self.db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))

    # ---- players --------------------------------------------------------
    def save_player(self, p):
        self.db.execute("INSERT INTO players (key, name, cred, temp, pending, created, last_seen, email, verified,"
                        " suspended, fails, locked_until, missed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET cred=excluded.cred, temp=excluded.temp, "
                        "pending=excluded.pending, last_seen=excluded.last_seen, email=excluded.email, "
                        "verified=excluded.verified, suspended=excluded.suspended, fails=excluded.fails, "
                        "locked_until=excluded.locked_until, missed_at=excluded.missed_at",
                        (p.name.lower(), p.name, p.cred, p.temp or "", p.pending or 0.0, p.created, p.last_seen,
                         p.email, int(p.verified), int(p.suspended), p.fails, p.locked_until, p.missed_at))

    def delete_player(self, name):
        self.db.execute("DELETE FROM players WHERE key=?", (name.lower(),))
        self.db.execute("DELETE FROM sessions WHERE player=?", (name.lower(),))
        self.db.execute("DELETE FROM prefs WHERE player=?", (name.lower(),))
        self.db.execute("DELETE FROM profiles WHERE player=?", (name.lower(),))
        self.db.execute("DELETE FROM playstats WHERE player=?", (name.lower(),))
        self.db.execute("DELETE FROM scores WHERE player=?", (name.lower(),))
        self.db.execute("DELETE FROM scored WHERE player=?", (name.lower(),))
        self.db.execute("DELETE FROM turns WHERE lower(name)=?", (name.lower(),))
        self.db.execute("UPDATE events SET client='', machine='' WHERE lower(who)=?", (name.lower(),))

    # ---- profiles (PFL): what the player entered, and the facts counted from their play ----------
    def profile_machine(self, player):
        r = self.db.execute("SELECT machine FROM profiles WHERE player=?", (player.lower(),)).fetchone()
        return r[0] if r else ""

    def set_profile_machine(self, player, machine):
        if machine:
            self.db.execute("INSERT OR REPLACE INTO profiles (player, machine) VALUES (?,?)", (player.lower(), machine))
        else:
            self.db.execute("DELETE FROM profiles WHERE player=?", (player.lower(),))

    def bump_stat(self, player, kind, value, at=None):
        """Count one more of something a player did on a public table: kind "game" (a game of type value
        played to its end), "prog" (a turn with the client program value) or "mach" (a turn on the machine)."""
        if not value:
            return
        self.db.execute("INSERT INTO playstats (player, kind, value, n, last) VALUES (?,?,?,1,?) "
                        "ON CONFLICT(player, kind, value) DO UPDATE SET n=n+1, last=excluded.last",
                        (player.lower(), kind, value, at or time.time()))

    def stats(self, player, kind, most):
        """[(value, n)] of one kind for a player, the most first (then the latest), at most most."""
        return self.db.execute("SELECT value, n FROM playstats WHERE player=? AND kind=? ORDER BY n DESC, last DESC"
                               " LIMIT ?", (player.lower(), kind, most)).fetchall()

    # ---- scores (TOP, PFL SCORE): the totals since the last reset, and each game's points per player -------
    # A reset empties the totals (scores) and starts the per-game counts afresh (meta scores:since); the record
    # of each game's points (scored) stays, as the tables, events and turns do.
    SCORE_COLS = ("points", "wins", "draws", "losses", "resigns", "games")
    REASON_COL = {"win": "wins", "draw": "draws", "loss": "losses", "resign": "resigns", "timeout": "resigns"}

    def add_scores(self, tid, gtype, rows, at=None):
        """A game ended: rows [(player, points, reason)], reason win, draw, loss, resign or timeout. A game
        counts once for each player (a second call for the same table and player changes nothing)."""
        at = at or time.time()
        for player, points, reason in rows:
            key = player.lower()
            if self.db.execute("SELECT 1 FROM scored WHERE table_id=? AND player=?", (tid, key)).fetchone():
                continue
            self.db.execute("INSERT INTO scored (table_id, player, gtype, points, reason, at) VALUES (?,?,?,?,?,?)",
                            (tid, key, gtype, points, reason, at))
            col = self.REASON_COL[reason]
            self.db.execute("INSERT INTO scores (player, points, %s, games, last) VALUES (?,?,1,1,?) "
                            "ON CONFLICT(player) DO UPDATE SET points=points+excluded.points, %s=%s+1, "
                            "games=games+1, last=excluded.last" % (col, col, col), (key, points, at))

    def score(self, player):
        """(points, wins, draws, losses, resigns, games) of a player since the last reset; zeros for none."""
        r = self.db.execute("SELECT %s FROM scores WHERE player=?" % ", ".join(self.SCORE_COLS),
                            (player.lower(),)).fetchone()
        return tuple(r) if r else (0,) * len(self.SCORE_COLS)

    def scoreboard(self, gtype=None):
        """[(player, points, wins, draws, losses, resigns, games)] of every player with a game since the last
        reset, the most points first (then the most wins, the fewest games, the name); with gtype, the points
        earned in that game alone."""
        order = " ORDER BY 2 DESC, 3 DESC, 7 ASC, 1 ASC"
        if gtype is None:
            return self.db.execute("SELECT player, %s FROM scores WHERE games > 0" % ", ".join(self.SCORE_COLS)
                                   + order).fetchall()
        since = float(self.get("scores:since", "0") or 0)
        return self.db.execute(
            "SELECT player, SUM(points), SUM(reason = 'win'), SUM(reason = 'draw'), SUM(reason = 'loss'),"
            " SUM(reason IN ('resign', 'timeout')), COUNT(*) FROM scored WHERE gtype=? AND at >= ?"
            " GROUP BY player" + order, (gtype, since)).fetchall()

    def reset_scores(self, at=None):
        """Everyone's score back to nothing; the record of the games stays. Returns how many players had one."""
        n = self.db.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
        self.db.execute("DELETE FROM scores")
        self.set("scores:since", repr(at or time.time()))
        return n

    # ---- the turns played (TRN, FEED): one row per turn, kept when its table goes ----------------
    TURN_COLS = ("table_id", "seq", "at", "gtype", "vis", "seat", "name", "client", "machine", "say")

    def add_turn(self, row, keep):
        """row: a dict of TURN_COLS. Past keep rows, the oldest are forgotten (the counts in playstats stay)."""
        self.db.execute("INSERT OR REPLACE INTO turns (%s) VALUES (%s)" % (", ".join(self.TURN_COLS),
                                                                         ",".join("?" * len(self.TURN_COLS))),
                        tuple(row[c] for c in self.TURN_COLS))
        n = self.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        if n > keep:
            self.db.execute("DELETE FROM turns WHERE rowid IN (SELECT rowid FROM turns ORDER BY at, rowid LIMIT ?)",
                            (n - keep,))

    def _turn_rows(self, rows):
        return [dict(zip(self.TURN_COLS, r)) for r in rows]

    def table_turns(self, tid, after, most):
        """The turns of one table after event number after, oldest first: the latest most of them."""
        rows = self.db.execute("SELECT %s FROM turns WHERE table_id=? AND seq>? ORDER BY seq DESC LIMIT ?"
                               % ", ".join(self.TURN_COLS), (tid, after, most)).fetchall()
        return list(reversed(self._turn_rows(rows)))

    def recent_turns(self, most, name=None):
        """The latest turns at public tables, newest first (one player's only, with name)."""
        q, a = "SELECT %s FROM turns WHERE vis='public'" % ", ".join(self.TURN_COLS), []
        if name:
            q += " AND lower(name)=?"
            a.append(name.lower())
        return self._turn_rows(self.db.execute(q + " ORDER BY at DESC, rowid DESC LIMIT ?", a + [most]).fetchall())

    # ---- what server extensions keep for a player (PRF; the core checks the limits) ----
    def prefs(self, ns, player):
        """[(key, value)] of one namespace and player, by key."""
        return self.db.execute("SELECT key, value FROM prefs WHERE ns=? AND player=? ORDER BY key",
                               (ns, player)).fetchall()

    def pref_get(self, ns, player, key):
        r = self.db.execute("SELECT value FROM prefs WHERE ns=? AND player=? AND key=?", (ns, player, key)).fetchone()
        return r[0] if r else None

    def pref_set(self, ns, player, key, value):
        self.db.execute("INSERT OR REPLACE INTO prefs (ns, player, key, value) VALUES (?,?,?,?)",
                        (ns, player, key, value))

    def pref_del(self, ns, player, key):
        self.db.execute("DELETE FROM prefs WHERE ns=? AND player=? AND key=?", (ns, player, key))

    def pref_usage(self, player):
        """{namespace: (keys, bytes)} for one player: counts only, never the values (the admin menus)."""
        rows = self.db.execute("SELECT ns, COUNT(*), SUM(LENGTH(key) + LENGTH(value)) FROM prefs WHERE player=?"
                               " GROUP BY ns ORDER BY ns", (player,)).fetchall()
        return {ns: (n, b or 0) for ns, n, b in rows}

    def load_players(self):
        return self.db.execute("SELECT name, cred, created, last_seen, email, verified, suspended, temp, fails,"
                               " locked_until, pending, missed_at FROM players").fetchall()

    # ---- sessions (only hashes of the ids) -------------------------------
    def save_session(self, rec):
        self.db.execute("INSERT OR REPLACE INTO sessions (hash, player, frontend, method, created, used, fp)"
                        " VALUES (?,?,?,?,?,?,?)", (rec.hash, rec.key, rec.frontend, rec.method, rec.created, rec.used,
                                                    rec.fp or ""))

    def touch_session(self, h, used):
        self.db.execute("UPDATE sessions SET used=? WHERE hash=?", (used, h))

    def delete_session(self, h):
        self.db.execute("DELETE FROM sessions WHERE hash=?", (h,))

    def delete_sessions_of(self, key, keep=None):
        self.db.execute("DELETE FROM sessions WHERE player=? AND hash IS NOT ?", (key, keep))

    def load_sessions(self):
        return self.db.execute("SELECT hash, player, frontend, method, created, used, fp FROM sessions").fetchall()

    def settings(self):
        rows = self.db.execute("SELECT key, value FROM meta WHERE key LIKE 'set:%'").fetchall()
        return {k[4:]: v for k, v in rows}

    def set_setting(self, key, value):
        if value is None:
            self.db.execute("DELETE FROM meta WHERE key=?", ("set:" + key,))
        else:
            self.set("set:" + key, value)

    # ---- tables ---------------------------------------------------------
    def save_table(self, t):
        self.db.execute(
            "INSERT OR REPLACE INTO tables (id, gtype, rules, options, seed, seats, status, state, seq,"
            " turn_secs, turn_seat, deadline, created, touched, vis, owner, gone, clients)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t.id, "%s/%d" % (t.type, t.ver), t.rules, json.dumps(t.options), t.seed, json.dumps(t.seats),
             t.status, json.dumps(t.state) if t.state is not None else None, t.seq, t.turn_secs,
             t.turn_seat, t.deadline, t.created, t.touched, t.vis, t.owner, json.dumps(sorted(t.gone)),
             json.dumps({str(k): list(v) for k, v in sorted(getattr(t, "clients", {}).items())})))

    def delete_table(self, tid):
        self.db.execute("DELETE FROM tables WHERE id=?", (tid,))
        self.db.execute("DELETE FROM events WHERE table_id=?", (tid,))
        self.db.execute("DELETE FROM invites WHERE table_id=?", (tid,))
        self.db.execute("DELETE FROM notes WHERE table_id=?", (tid,))
        self.db.execute("DELETE FROM tdata WHERE table_id=?", (tid,))

    def load_tables(self):
        cols = ("id", "gtype", "rules", "options", "seed", "seats", "status", "state", "seq", "turn_secs",
                "turn_seat", "deadline", "created", "touched", "vis", "owner", "gone", "clients")
        rows = self.db.execute("SELECT %s FROM tables" % ", ".join(cols)).fetchall()
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["options"] = json.loads(d["options"])
            d["seats"] = json.loads(d["seats"])
            d["state"] = json.loads(d["state"]) if d["state"] else None
            d["gone"] = json.loads(d["gone"] or "[]")
            d["clients"] = json.loads(d["clients"] or "{}")
            out.append(d)
        return out

    # ---- invitations ----------------------------------------------------
    def save_invite(self, tid, key, inv):
        self.db.execute("INSERT OR REPLACE INTO invites (table_id, key, name, inviter, seat, at) VALUES (?,?,?,?,?,?)",
                        (tid, key, inv["name"], inv["from"], inv["seat"], inv["at"]))

    def delete_invite(self, tid, key):
        self.db.execute("DELETE FROM invites WHERE table_id=? AND key=?", (tid, key))

    def load_invites(self, tid):
        """{player key: {"name", "from", "seat", "at"}} for one table."""
        rows = self.db.execute("SELECT key, name, inviter, seat, at FROM invites WHERE table_id=?", (tid,)).fetchall()
        return {k: {"name": n, "from": f, "seat": s, "at": a} for k, n, f, s, a in rows}

    # ---- a table's notes and its data (NOTE, TDL/TDS/TDD; the core checks the limits) ----------------
    def add_note(self, tid, n, at, who, text):
        self.db.execute("INSERT OR REPLACE INTO notes (table_id, n, at, who, text) VALUES (?,?,?,?,?)",
                        (tid, n, at, who, text))

    def delete_note(self, tid, n):
        self.db.execute("DELETE FROM notes WHERE table_id=? AND n=?", (tid, n))

    def load_notes(self, tid):
        """[(n, at, who, text)] of one table, oldest first."""
        return self.db.execute("SELECT n, at, who, text FROM notes WHERE table_id=? ORDER BY n", (tid,)).fetchall()

    def set_tdata(self, tid, key, value):
        if value is None:
            self.db.execute("DELETE FROM tdata WHERE table_id=? AND key=?", (tid, key))
        else:
            self.db.execute("INSERT OR REPLACE INTO tdata (table_id, key, value) VALUES (?,?,?)", (tid, key, value))

    def load_tdata(self, tid):
        """{key: value} of one table."""
        return dict(self.db.execute("SELECT key, value FROM tdata WHERE table_id=?", (tid,)).fetchall())

    # ---- events ---------------------------------------------------------
    def add_event(self, tid, seq, text, by=None):
        """by: (who, client, machine) for an event a player's move made."""
        who, client, machine = by or ("", "", "")
        self.db.execute("INSERT OR REPLACE INTO events (table_id, seq, text, at, who, client, machine)"
                        " VALUES (?,?,?,?,?,?,?)", (tid, seq, text, time.time(), who, client, machine))

    def forget_client_events(self, tid, seat):
        """A table's CLIENT events for a seat keep only the seat (CLIENT seat): an account was deleted."""
        self.db.execute("UPDATE events SET text=? WHERE table_id=? AND text LIKE ?",
                        ("CLIENT %d" % seat, tid, "CLIENT %d %%" % seat))

    def load_events(self, tid, keep):
        rows = self.db.execute("SELECT seq, text FROM events WHERE table_id=? ORDER BY seq DESC LIMIT ?",
                               (tid, keep)).fetchall()
        return list(reversed(rows))

    # ---- email bookkeeping (docs/accounts.md, email sending limits) ---------------
    # The mail tables keep an address only as addr_hash(address), so the database holds no list of
    # addresses that never became accounts (docs/security-design.md, Data at rest).
    def addr_hash(self, addr):
        """An email address (lower case, no surrounding spaces) as HMAC-SHA256 under a random key of this
        database's own (meta addr:key), in hex."""
        key = getattr(self, "_addr_key", None)
        if key is None:
            k = self.get("addr:key")
            if not k:
                k = secrets.token_hex(32)
                self.db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('addr:key', ?)", (k,))
                k = self.get("addr:key")
            key = self._addr_key = bytes.fromhex(k)
        return hmac.new(key, (addr or "").strip().lower().encode("utf-8", "replace"), hashlib.sha256).hexdigest()

    def email_count(self, since, addr=None, name=None, new=None, kind=None, source=None):
        q, a = "SELECT COUNT(*), MAX(at) FROM email_log WHERE at >= ?", [since]
        if isinstance(kind, (tuple, list)):
            q += " AND kind IN (%s)" % ",".join("?" * len(kind))
            a.extend(kind)
        elif kind is not None:
            q += " AND kind=?"
            a.append(kind)
        if source is not None:
            q += " AND source=?"
            a.append(source)
        if addr is not None:
            q += " AND addr=?"
            a.append(self.addr_hash(addr))
        if name is not None:
            q += " AND name=?"
            a.append(name.lower())
        if new is not None:
            q += " AND new=?"
            a.append(int(new))
        n, last = self.db.execute(q, a).fetchone()
        return n, last or 0

    def email_sent(self, addr, name, new, kind="signup", source=""):
        self.db.execute("INSERT INTO email_log (addr, name, at, new, kind, source) VALUES (?,?,?,?,?,?)",
                        (self.addr_hash(addr), name.lower(), time.time(), int(new), kind, source or ""))
        self.db.execute("DELETE FROM email_log WHERE at < ?", (time.time() - 3 * 86400,))

    def recovery_done(self, name):
        """The latest recovery mail for name was used (its temporary password
        signed in): it was its owner's (kind recovery-own from now on)."""
        self.db.execute("UPDATE email_log SET kind='recovery-own' WHERE rowid = (SELECT rowid FROM email_log"
                        " WHERE name=? AND kind='recovery' ORDER BY at DESC LIMIT 1)", (name.lower(),))

    def email_blocked(self, addr):
        return self.db.execute("SELECT why FROM email_blocked WHERE addr=?", (self.addr_hash(addr),)).fetchone()

    def block_email(self, addr, why):
        self.db.execute("INSERT OR REPLACE INTO email_blocked (addr, why, at) VALUES (?,?,?)",
                        (self.addr_hash(addr), why, time.time()))

    def blocked_since(self, since):
        return self.db.execute("SELECT COUNT(*) FROM email_blocked WHERE at >= ?", (since,)).fetchone()[0]

    def blocked_origins_since(self, since):
        """Addresses blocked since then that parlord itself mailed in the day
        before (a provider's list holds other senders' addresses too), counted
        by who asked for the mail: the source address of the latest mail
        logged to it, or the address itself when no source is known. One
        requester with many undeliverable addresses counts once (docs/accounts.md)."""
        return self.db.execute(
            "SELECT COUNT(DISTINCT COALESCE((SELECT l.source FROM email_log l WHERE l.addr=b.addr AND l.source != ''"
            " ORDER BY l.at DESC LIMIT 1), 'addr:' || b.addr)) FROM email_blocked b WHERE b.at >= ?"
            " AND EXISTS (SELECT 1 FROM email_log m WHERE m.addr=b.addr AND m.at >= ?)",
            (since, since - 86400)).fetchone()[0]

    # ---- the sign-up address cooldown (docs/accounts.md): addr_hash -> until when --------
    def cooldown_until(self, h):
        r = self.db.execute("SELECT until FROM signup_cooldown WHERE h=?", (h,)).fetchone()
        return r[0] if r else 0.0

    def set_cooldown(self, h, until):
        self.db.execute("INSERT OR REPLACE INTO signup_cooldown (h, until) VALUES (?,?)", (h, until))

    def clear_cooldown(self, h=None):
        """One address's cooldown (h), or every one; returns how many were running."""
        now = time.time()
        if h is None:
            n = self.cooldowns(now)
            self.db.execute("DELETE FROM signup_cooldown")
            return n
        n = 1 if self.cooldown_until(h) > now else 0
        self.db.execute("DELETE FROM signup_cooldown WHERE h=?", (h,))
        return n

    def cooldowns(self, now):
        """How many addresses are in their cooldown; the ones past it are forgotten."""
        self.db.execute("DELETE FROM signup_cooldown WHERE until <= ?", (now,))
        return self.db.execute("SELECT COUNT(*) FROM signup_cooldown").fetchone()[0]

    # ---- trusted addresses: a player signed in from there lately ---------------
    def trust(self, key, source, at):
        self.db.execute("INSERT OR REPLACE INTO trusted (key, source, at) VALUES (?,?,?)", (key, source, at))

    def trusted_at(self, key, source):
        r = self.db.execute("SELECT at FROM trusted WHERE key=? AND source=?", (key, source)).fetchone()
        return r[0] if r else 0.0

    def forget_trust(self, before, key=None):
        if key is None:
            self.db.execute("DELETE FROM trusted WHERE at < ?", (before,))
        else:
            self.db.execute("DELETE FROM trusted WHERE key=?", (key,))

    def blocked_for_source(self, source, since):
        """How many addresses a source asked mail for were blocked since then."""
        return self.db.execute(
            "SELECT COUNT(DISTINCT b.addr) FROM email_blocked b JOIN email_log l ON l.addr=b.addr"
            " WHERE b.at >= ? AND l.source=?", (since, source)).fetchone()[0]

    # ---- meta -----------------------------------------------------------
    def get(self, key, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, str(value)))
