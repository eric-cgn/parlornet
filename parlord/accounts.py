"""The accounts component (docs/security-design.md): the only code that decides
who a connection is. It owns the players, runs the sign-in method
(parlord/auth.py), and creates, resumes, touches, ends and expires sessions.

The core calls it through a connection object `conn` with these attributes:
    frontend      the frontend's key name on the frontend channel, else None
    fingerprint   the fingerprint of the key it connected with (sessions are bound to it)
    source        the client's address where known (the public port; SRC from a
                  server extension), for per-address limits; else None
    pending       the method's dialog state (the method owns it)
    signin        the connection's SessionRec once signed in, else None
    ses_fails     failed SES on this connection
    key_ok        True right after this connection signed in with the account's password: it may ask for
                  a sign-in key (KEY NEW) once
Results are wire-neutral tuples:
    ("ok", player, words)   words for the OK line
    ("ask", field, label, secret)
    ("err", code, text)
"""
import asyncio
import hashlib
import hmac
import ipaddress
import re
import secrets
import sys
import threading
import time

from . import auth, bots

SID_RE = re.compile(r"^[A-Za-z0-9_-]{22}\Z")
SES_TRIES = 3               # failed SES on one connection before SES is refused there
TOUCH_WRITE_SECS = 60       # write a session's last use at most this often
LOCK_STRIPES = 64
# Sign-in keys (docs/accounts.md, Staying signed in): a computer where a player stays signed in keeps one,
# never the password. 128 random bits, 22 characters, stored only as SHA-256 like session ids.
KEYS_MAX = 20               # keys one player may hold at once (ERR LIMIT past it)
KEY_IDLE_SECS = 90 * 86400  # the default of keys.idle_secs: a key unused this long is forgotten
KEY_LABEL_LEN = 32          # a key's label (KEY NEW label): printable ASCII, cleaned by the core


STEP_KINDS = frozenset(("text", "secret", "yn", "info"))     # what a frontend may declare with CAN


def step_kinds(r):
    """The kinds an ask step needs: text or secret (or yn), plus info if it has lines to show."""
    extra = r[4] if len(r) > 4 else {}
    kinds = {"yn" if extra.get("yn") else ("secret" if r[3] else "text")}
    if extra.get("info"):
        kinds.add("info")
    return kinds


class Player:
    def __init__(self, name, cred="", created=None, last_seen=None, email=None, verified=0, suspended=0,
                 temp="", fails=0, locked_until=0.0, pending=0.0, missed_at=0.0):
        self.name = name
        self.cred = cred or ""              # sign-in password: its scrypt hash ("" for none yet, or after an operator reset)
        self.temp = temp or ""              # a temporary password: "until$scrypt hash" (auth.temp_cred), else ""
        self.pending = pending or 0.0       # a sign-up email's account not yet signed into: deleted at this time
        self.email, self.verified, self.suspended = email, bool(verified), bool(suspended)
        self.fails, self.locked_until = fails, locked_until
        self.missed_at = missed_at or 0.0     # the last wrong answer (the count halves after quiet stretches)
        self.conns = 0
        self.active = 0.0                   # the last activity (parlord/core.py, ACTIVE_VERBS): who is online
        self.created = created or time.time()
        self.last_seen = last_seen or time.time()


class SessionRec:
    """One sign-in. hash is None for a connection-bound session (public port).
    fp: the fingerprint of the server extension's key it was issued under. since: created on the
    monotonic clock (time.monotonic), so the seconds since it (TIM) never run backward with the wall clock."""
    __slots__ = ("hash", "key", "frontend", "method", "created", "used", "written", "ended", "fp", "since", "devkey")

    def __init__(self, h, key, frontend, method, created, used, fp="", devkey=0):
        self.hash, self.key, self.frontend, self.method = h, key, frontend, method
        self.created, self.used, self.written, self.ended = created, used, used, False
        self.fp = fp or ""
        self.devkey = devkey or 0       # the number of the sign-in key it signed in with or was given (0: none)
        self.since = time.monotonic() - max(0.0, time.time() - created)


def source_key(addr):
    """A client address as the key for per-address limits, budgets, trust and
    eviction choices: an IPv4 address as is (an IPv4-mapped IPv6 one as
    IPv4), an IPv6 address by its /48 (one subscriber or site can hold a whole
    /48, 65,536 /64s, so a /64 key would let one attacker look like many);
    None if it is not an IP address."""
    try:
        ip = ipaddress.ip_address((addr or "").strip())
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.version == 6:
        return str(ipaddress.ip_network("%s/48" % ip, strict=False))
    return str(ip)


def sid_hash(sid):
    return hashlib.sha256(sid.encode("ascii")).hexdigest()


class Accounts:
    def __init__(self, store, settings, server_name="parlornet", method="password", label=None, signup=None):
        self.store = store
        self.settings = settings
        self.server_name = server_name
        self.players = {}
        self.sessions = {}          # hash -> SessionRec (resumable sessions)
        self.by_player = {}         # player key -> set of SessionRec (all, connection-bound too)
        self.default_method = (method, label, signup)
        self.locks = [threading.Lock() for _ in range(LOCK_STRIPES)]    # by name, a fixed number
        self.ses_failed = {}        # frontend -> failed SES count (logged, never the ids)
        self.budget_lock = threading.Lock()
        self.misses = {}            # client address (or "?") -> times of its untrusted wrong answers
        self.global_misses = []     # times of untrusted wrong answers, the whole server
        self.rates = {}             # (what, client address) -> recent times (rate())
        self.signups = auth.Signups(self)       # how many pending accounts at once, the address cooldown
        self.places = lambda: []    # where to sign in, for mail ("on the web at ..."): the core sets it
        self.use_method()

    # ---- settings --------------------------------------------------------------
    def use_method(self):
        """The sign-in and sign-up methods: from the settings (admin panel), else
        the command line. A pair that cannot work falls back to the sign-in
        method's usual sign-up, with a warning. Sessions made under another pair
        stop working (see valid())."""
        signin = self.settings.get("auth.signin") or self.settings.get("auth.method") or self.default_method[0]
        if signin not in auth.SIGNIN:
            print("accounts: unknown sign-in method %r; using password" % signin, file=sys.stderr, flush=True)
            signin = "password"
        signup = (self.settings.get("auth.signup") or self.default_method[2]
                  or auth.DEFAULT_SIGNUP[signin])
        problem = auth.combo_problem(signin, signup)
        if problem:
            print("accounts: sign-in %s with sign-up %s cannot work (%s); using sign-up %s"
                  % (signin, signup, problem, auth.DEFAULT_SIGNUP[signin]), file=sys.stderr, flush=True)
            signup = auth.DEFAULT_SIGNUP[signin]
        label = self.settings.get("auth.label") or self.default_method[1]
        self.method = auth.Auth(self, signin, signup, label)

    def secs(self, key, default):
        try:
            return max(1, int(self.settings.get(key, default)))
        except ValueError:
            return default

    def greeting(self):
        return self.method.greeting()

    # ---- players ---------------------------------------------------------------
    def load(self):
        for row in self.store.load_players():
            self.players[row[0].lower()] = Player(*row)
        bots.load(self)             # the bot accounts (parlord/bots.py)
        now = time.time()
        for h, key, frontend, method, created, used, fp, devkey in self.store.load_sessions():
            rec = SessionRec(h, key, frontend, method, created, used, fp, devkey)
            if key in self.players and self.valid(rec, now, idle=True):
                self.sessions[h] = rec
                self.by_player.setdefault(key, set()).add(rec)
            else:
                self.store.delete_session(h)

    def player(self, name, create=False):
        p = self.players.get(name.lower())
        if p is None and create:
            p = Player(name)
            self.players[name.lower()] = p
            self.save_player(p)
        return p

    def player_by_email(self, addr):
        """The account an address belongs to: never a pending one (a sign-up nobody has signed into)."""
        return next((p for p in list(self.players.values()) if p.email == addr and not p.pending), None)

    def pending_count(self, now=None):
        now = now or time.time()
        return sum(1 for p in list(self.players.values()) if p.pending > now)

    def save_player(self, p):
        if not getattr(p, "deleted", False):
            self.store.save_player(p)

    # ---- wrong-answer budgets and trusted addresses (docs/security-design.md) ----
    def is_trusted(self, name, source):
        """True if this client address signed in as name within TRUST_SECS."""
        if not source:
            return False
        return time.time() - self.store.trusted_at(name.lower(), source) < auth.TRUST_SECS

    def trust(self, name, source):
        if source:
            self.store.trust(name.lower(), source, time.time())

    @staticmethod
    def window(times, secs, now):
        while times and now - times[0] >= secs:
            times.pop(0)
        return times

    def miss_budget(self, source, name, now=None):
        """None, or the refusal: the client address has used up its wrong
        answers (SOURCE_MISSES), or the server its untrusted ones (GLOBAL_MISSES).
        An address trusted for the name is exempt."""
        if self.is_trusted(name, source):
            return None
        now = now or time.time()
        with self.budget_lock:
            n, secs = auth.SOURCE_MISSES
            mine = self.window(self.misses.get(source or "?", []), secs, now)
            if len(mine) >= n:
                left = int(mine[0] + secs - now) + 1
                return ("err", "LOCKED", "wait=%d Too many wrong answers from your address; try again in %s"
                        % (left, auth.wait_text(left)))
            g, gsecs = auth.GLOBAL_MISSES
            every = self.window(self.global_misses, gsecs, now)
            if len(every) >= g:
                left = int(every[0] + gsecs - now) + 1
                return ("err", "BUSY", "wait=%d Too many wrong answers on this server; try later, or answer reset"
                        % left)
        return None

    def budgets(self, now=None):
        """For the admin menus: the wrong-answer budgets as they stand."""
        now = now or time.time()
        n, secs = auth.SOURCE_MISSES
        g, gsecs = auth.GLOBAL_MISSES
        with self.budget_lock:
            spent = sum(1 for q in self.misses.values() if len(self.window(q, secs, now)) >= n)
            used = len(self.window(self.global_misses, gsecs, now))
            busy = sum(1 for (what, _), q in self.rates.items()
                       if what == "check" and len(self.window(q, auth.CHECKS_PER_SOURCE[1], now)) >=
                       auth.CHECKS_PER_SOURCE[0])
        return {"wrong_server": used, "wrong_server_limit": g, "addresses_spent": spent,
                "wrong_per_address_limit": n, "addresses_checks_held": busy}

    def clear_budgets(self):
        """The operator clears the wrong-answer and password-check budgets
        (the per-name lockouts are not touched)."""
        with self.budget_lock:
            self.misses, self.global_misses, self.rates = {}, [], {}

    def note_miss(self, source, name, now=None):
        """Count a wrong answer against the client address and the server
        (not when the address is trusted for the name)."""
        if self.is_trusted(name, source):
            return
        now = now or time.time()
        with self.budget_lock:
            if len(self.misses) > 10000:
                self.misses = {k: v for k, v in self.misses.items() if v and now - v[-1] < auth.SOURCE_MISSES[1]}
            q = self.misses.setdefault(source or "?", [])
            q.append(now)
            del q[:-4 * auth.SOURCE_MISSES[0]]
            self.global_misses.append(now)
            del self.global_misses[:-4 * auth.GLOBAL_MISSES[0]]

    def rate(self, conn, what, limit, now=None):
        """None, or a refusal once a client address asked for `what` more than
        limit (n, seconds) allows. Unknown addresses share one bucket."""
        n, secs = limit
        now = now or time.time()
        key = (what, getattr(conn, "source", None) or "?")
        with self.budget_lock:
            if len(self.rates) > 10000:
                self.rates = {k: v for k, v in self.rates.items() if v and now - v[-1] < 3600}
            q = self.window(self.rates.setdefault(key, []), secs, now)
            if len(q) >= n:
                left = int(q[0] + secs - now) + 1
                return ("err", "BUSY", "wait=%d Too many tries from your address; try again in %s"
                        % (left, auth.wait_text(left)))
            q.append(now)
        return None

    # ---- the sign-in dialog -------------------------------------------------
    def lock_for(self, name):
        """One sign-in per name at a time (no replayed code slips in between
        check and save); names share a fixed set of locks so memory stays bounded."""
        h = hashlib.sha256((name or "").lower().encode("ascii", "replace")).digest()
        return self.locks[h[0] % LOCK_STRIPES]

    async def begin(self, conn, name, given, mode="any"):
        """HELO (mode "any"), USER ("signin") or REG ("signup"). The method runs in a worker thread (it may
        send mail or hash), one sign-in per name at a time; the session is made on the event loop."""
        if conn.signin is not None:
            return ("err", "STATE", "already signed in")
        conn.pending = None
        method = self.method
        r = bots.begin(self, conn, name)        # bot names, and the bot's own key (parlord/bots.py)
        if r is not None:
            return self.finish(conn, r, method)

        def run():
            with self.lock_for(name):
                return method.begin(conn, name, given, mode)
        return self.finish(conn, await asyncio.to_thread(run), method)

    async def answer(self, conn, field, value):
        """AUT: the next step of a sign-in, a sign-up, or (signed in) a password change."""
        pend = conn.pending or {}
        if conn.signin is not None and pend.get("flow") != "change":
            return ("err", "STATE", "already signed in")
        method = self.method

        def run():
            with self.lock_for(pend.get("name")):
                return method.answer(conn, field, value)
        return self.finish(conn, await asyncio.to_thread(run), method)

    def finish(self, conn, r, method):
        """After the method: an ask step the frontend cannot show is refused
        cleanly; a success refuses suspended players and creates the session."""
        if r[0] == "ask":
            need = step_kinds(r)
            missing = sorted(need - getattr(conn, "can", STEP_KINDS))
            if missing:
                conn.pending = None
                extra = r[4] if len(r) > 4 else {}
                if extra.get("new") and not extra.get("cred"):
                    return ("err", "NEWPASS", auth.NEWPASS_TEXT)
                return ("err", "CANNOT", "this client cannot show the next step; use the web or telnet")
            return r
        if r[0] != "ok":
            return r
        p = r[1]
        conn.pending = None
        if method is not self.method:
            return ("err", "STATE", "the sign-in method changed; sign in again")
        extra = r[3] if len(r) > 3 else {}
        if conn.signin is not None:     # a password change: other sessions end, this one stays, and keys go
            self.end_all(p.name.lower(), keep=conn.signin)
            self.forget_keys(p.name.lower())
            self.set_devkey(conn.signin, 0)
            conn.key_ok = self.keys_offered(p)
            return ("done",)
        if p.suspended:
            return ("err", "SUSPENDED", "this name is suspended")
        if extra.get("revoke"):        # a new password: every other session ends and every sign-in key goes
            self.end_all(p.name.lower())
            self.forget_keys(p.name.lower())
        self.trust(p.name, getattr(conn, "source", None))       # this address is the player's now
        rec, sid = self.create(p, conn.frontend, getattr(conn, "fingerprint", None), extra.get("key", 0))
        conn.signin = rec
        conn.key_ok = not extra.get("key") and self.keys_offered(p)    # signed in with the password itself
        p.last_seen = time.time()
        self.save_player(p)
        return ("ok", p, [sid if conn.frontend else "-"])

    # ---- sessions --------------------------------------------------------------
    def create(self, p, frontend, fp=None, devkey=0):
        now = time.time()
        key = p.name.lower()
        sid = None
        if frontend:
            sid = secrets.token_urlsafe(16)
            rec = SessionRec(sid_hash(sid), key, frontend, self.method.name, now, now, fp, devkey)
            self.sessions[rec.hash] = rec
            self.store.save_session(rec)
        else:
            rec = SessionRec(None, key, None, self.method.name, now, now, devkey=devkey)
        self.by_player.setdefault(key, set()).add(rec)
        return rec, sid

    def valid(self, rec, now=None, idle=False):
        """Not ended, under the current method, younger than the absolute limit,
        and (when resuming) used within the idle limit."""
        now = now or time.time()
        if rec.ended or rec.method != self.method.name:
            return False
        if now - rec.created > self.secs("session.max_secs", 86400):
            return False
        if idle and now - rec.used > self.secs("session.idle_secs", 7200):
            return False
        p = self.players.get(rec.key)
        return p is not None and not p.suspended

    def resume(self, conn, sid):
        """SES sid: frontend connections only, for sessions that frontend was
        issued under the key it holds now (a rotated or removed key ends them)."""
        if not conn.frontend:
            return ("err", "NOTFRONTEND", "SES is only for frontends")
        if conn.signin is not None:
            return ("err", "STATE", "already signed in")
        if conn.ses_fails >= SES_TRIES:
            return ("err", "RATE", "too many failed SES on this connection")
        rec = self.sessions.get(sid_hash(sid)) if SID_RE.match(sid or "") else None
        stale = rec is not None and (not self.valid(rec, idle=True) or (
            rec.frontend == conn.frontend and not hmac.compare_digest(rec.fp or "", getattr(conn, "fingerprint", "") or "")))
        if rec is None or stale or not hmac.compare_digest(rec.frontend or "", conn.frontend):
            if stale:
                self.end(rec)
            conn.ses_fails += 1
            n = self.ses_failed[conn.frontend] = self.ses_failed.get(conn.frontend, 0) + 1
            if n in (10, 100, 1000) or n % 10000 == 0:
                print("accounts: %d failed SES from frontend %s" % (n, conn.frontend), file=sys.stderr, flush=True)
            return ("err", "NOSESSION", "no such session; sign in again")
        p = self.players[rec.key]
        self.touch(rec)
        conn.signin = rec
        return ("ok", p, [p.name])

    def touch(self, rec, now=None):
        rec.used = now or time.time()
        if rec.hash and rec.used - rec.written > TOUCH_WRITE_SECS:
            rec.written = rec.used
            self.store.touch_session(rec.hash, rec.used)

    def current(self, conn):
        """The connection's player, or None. A session that ended elsewhere (OUT
        all, suspend, delete, a new credential, a method change) or grew too old
        signs the connection out: returns ("ended", None) once for that."""
        rec = conn.signin
        if rec is None:
            return None, None
        now = time.time()
        if not self.valid(rec, now):
            self.end(rec)
            conn.signin = None
            return "ended", None
        self.touch(rec, now)
        return None, self.players[rec.key]

    def end(self, rec):
        rec.ended = True
        if rec.hash:
            self.sessions.pop(rec.hash, None)
            self.store.delete_session(rec.hash)
        s = self.by_player.get(rec.key)
        if s is not None:
            s.discard(rec)
            if not s:
                del self.by_player[rec.key]

    def end_frontend(self, frontend, fp):
        """End the sessions a server extension was issued under any key but fp
        (its key was rotated; None: removed). Returns how many."""
        n = 0
        for rec in list(self.sessions.values()):
            if rec.frontend == frontend and (fp is None or rec.fp != fp):
                self.end(rec)
                n += 1
        return n

    def end_all(self, key, keep=None):
        """End every session of a player (but keep); returns how many."""
        n = 0
        for rec in list(self.by_player.get(key, ())):
            if rec is not keep:
                self.end(rec)
                n += 1
        self.store.delete_sessions_of(key, keep.hash if keep is not None else None)
        return n

    def sign_out(self, conn, everywhere=False):
        """OUT (this session) or OUT all (every session of the player). Signing out always forgets the
        sign-in key the session signed in with or was given. OUT all forgets every key of the player."""
        rec = conn.signin
        if rec is None:
            return ("err", "NOHELO", "not signed in")
        conn.signin = None
        conn.key_ok = False
        if everywhere:
            n = self.end_all(rec.key)
            self.forget_keys(rec.key)
        else:
            self.end(rec)
            n = 1
            if rec.devkey:
                self.store.delete_key(rec.key, rec.devkey)
        return ("ok", None, [str(n)])

    def connection_closed(self, conn):
        """A connection-bound session (public port) ends with its connection."""
        rec = conn.signin
        if rec is not None and rec.hash is None:
            self.end(rec)
        conn.signin = None

    def change(self, conn):
        """PWD: start changing the password (sign-in password only): the current
        one, a new one, the new one again. The player's other sessions end."""
        rec = conn.signin
        p = self.players.get(rec.key) if rec else None
        if p is None:
            return ("err", "NOHELO", "not signed in")
        return self.finish(conn, self.method.change_begin(conn, p), self.method)

    # ---- account management (the admin calls these) ------------------------------
    def suspend(self, name, on=True):
        """Suspend (every session ends at once) or lift a suspension (and the lockout)."""
        p = self.player(name)
        if p is None:
            return False
        p.suspended = bool(on)
        if not on:
            p.fails, p.locked_until = 0, 0.0
        self.save_player(p)
        if on:
            self.end_all(p.name.lower())
        return True

    def delete(self, name):
        p = self.players.pop(name.lower(), None)
        if p is None:
            return False
        p.deleted = True            # a connection closing afterwards must not save it back
        self.end_all(name.lower())
        self.store.forget_trust(0, name.lower())
        self.store.delete_player(name)
        return True

    async def reset(self, name):
        """For the operator: clear the lockout and end every session. Sign-in
        password: a temporary password for the operator to give the player, who
        signs in with it once and chooses a new password (the old one stops
        working). Returns (ok, text for the operator, the temporary password or None)."""
        p = self.player(name)
        if p is None:
            return False, "no such player", None
        p.fails, p.locked_until = 0, 0.0
        self.forget_keys(p.name.lower())        # every computer where the player stays signed in signs in again
        signin = self.method.signin
        code = None
        if signin.name == "password":
            text, code = signin.operator_reset(p)
        else:
            text = "Lockout cleared for %s; signed out everywhere." % p.name
        self.save_player(p)
        self.end_all(p.name.lower())
        return True, text, code

    def new_signups_held(self):
        """Bounces from more than 3 requesters in a day (blocked addresses,
        counted by the client address that asked for the mail; one requester
        with many bad addresses counts once and is refused on its own) hold
        sign-up mail until the operator resumes sending (docs/accounts.md).
        Recovery mail to addresses on file is never held."""
        now = time.time()
        since = max(now - 86400, auth.past_time(self.settings.get("email.new_ok_since"), now))     # future: ignored
        return self.store.blocked_origins_since(since) > 3

    def expire(self, now=None):
        """Pending accounts past their time are deleted (their names are free again), and temporary
        passwords past theirs are dropped. Returns the names deleted."""
        now = now or time.time()
        gone = []
        for p in list(self.players.values()):
            if p.pending and p.pending <= now:
                if self.delete(p.name):
                    gone.append(p.name)
            elif p.temp and not auth.temp_live(p.temp, now):
                p.temp = ""
                self.save_player(p)
        return gone

    def sweep(self, now=None):
        now = now or time.time()
        self.expire(now)                # a pending account nobody signed into in a day: gone, its name free
        if now - getattr(self, "trust_swept", 0) > 3600:
            self.trust_swept = now
            self.store.forget_trust(now - auth.TRUST_SECS)
            self.store.forget_unused_keys(now - self.key_idle())
        for rec in list(self.sessions.values()):
            if not self.valid(rec, now, idle=True):
                self.end(rec)

    # ---- sign-in keys: staying signed in (docs/accounts.md, Staying signed in) ------------------------
    # A computer where the player chose "Stay signed in" asks for a key right after signing in with the
    # password (KEY NEW) and keeps it instead of the password. Next time it answers the password question
    # with the key (AUT key, checked by the sign-in method under the name's lock, auth.PasswordSignIn), so a
    # wrong key counts like a wrong password. Only a key's SHA-256 is stored, with the channel it was issued
    # on (the server extension's key name, or "" for the public port), which is the only channel where it
    # works. Signing out forgets it, and a password change or the operator's reset forgets them all.
    def key_idle(self):
        return self.secs("keys.idle_secs", KEY_IDLE_SECS)

    def keys_offered(self, p):
        """Whether a player may have sign-in keys: sign-in password, and not a bot."""
        return self.method.signin.name == "password" and not bots.is_bot(p)

    def live_keys(self, key, now=None):
        """The player's keys used within keys.idle_secs: [(n, hash, frontend, label, created, used)]. Older
        ones are forgotten on the way."""
        now = now or time.time()
        rows = self.store.keys_of(key)
        live = [r for r in rows if now - r[5] <= self.key_idle()]
        for r in rows:
            if r not in live:
                self.store.delete_key(key, r[0])
        return live

    def has_keys(self, p):
        return bool(self.live_keys(p.name.lower()))

    def key_check(self, p, value, conn, now=None):
        """The number of the player's key that value is, for the channel conn is on, if it is live; else 0.
        Every key of the player is compared (constant time, no early end), so the time taken does not say
        which or whether one matched. A right key is marked used now."""
        now = now or time.time()
        value = (value or "").strip()
        h = sid_hash(value if SID_RE.match(value) else "-" * 22)
        channel = getattr(conn, "frontend", None) or ""
        found = 0
        for n, kh, kfe, _label, _created, _used in self.live_keys(p.name.lower(), now):
            same = hmac.compare_digest(kh.encode("ascii"), h.encode("ascii"))
            here = hmac.compare_digest(kfe.encode("ascii", "replace"), channel.encode("ascii", "replace"))
            if same and here and SID_RE.match(value):
                found = n
        if found:
            self.store.touch_key(p.name.lower(), found, now)
        return found

    def set_devkey(self, rec, n):
        rec.devkey = n or 0
        if rec.hash:
            self.store.set_session_devkey(rec.hash, rec.devkey)

    def key_issue(self, conn, label):
        """KEY NEW: a sign-in key for this computer, once, right after signing in with the password on this
        connection. ("ok", key) or ("err", code, text). The key is returned once and never kept."""
        rec = conn.signin
        p = self.players.get(rec.key) if rec else None
        if p is None:
            return ("err", "NOHELO", "not signed in")
        if not self.keys_offered(p):
            return ("err", "NOAUTH", "this server signs in without a password, so there is no key to keep")
        if not getattr(conn, "key_ok", False):
            return ("err", "STATE", "a key is given right after signing in with your password")
        if len(self.live_keys(rec.key)) >= KEYS_MAX:
            return ("err", "LIMIT", "you already stay signed in on %d computers, and that is the most" % KEYS_MAX)
        key = secrets.token_urlsafe(16)
        n = self.store.add_key(rec.key, sid_hash(key), conn.frontend or "", label, time.time())
        self.set_devkey(rec, n)
        conn.key_ok = False
        return ("ok", key)

    def key_list(self, conn, now=None):
        """KEY LST: [(n, created, used, here, label)] of the signed-in player's live keys; here: the key this
        session signed in with or was given."""
        rec = conn.signin
        return [(n, created, used, n == rec.devkey, label)
                for n, _h, _fe, label, created, used in self.live_keys(rec.key, now)]

    def key_forget(self, conn, which=None):
        """KEY DEL (this session's key), KEY DEL n or KEY DEL all. Sessions that signed in with a forgotten key,
        or were given it, end, except this connection's own, so forgetting a computer signs it out. Returns
        ("ok", how many) or ("err", code, text)."""
        rec = conn.signin
        if which is None:
            if not rec.devkey:
                return ("err", "NOKEY", "this computer has no key")
            gone = {rec.devkey}
            n = self.store.delete_key(rec.key, rec.devkey)
        elif which == "all":
            gone = {r[0] for r in self.store.keys_of(rec.key)}
            n = self.store.delete_keys(rec.key)
        else:
            if not self.store.delete_key(rec.key, which):
                return ("err", "NOKEY", "there is no key %d" % which)
            gone, n = {which}, 1
        for other in list(self.by_player.get(rec.key, ())):
            if other is not rec and other.devkey in gone:
                self.end(other)
        if rec.devkey in gone:
            self.set_devkey(rec, 0)
        return ("ok", n)

    def forget_keys(self, key):
        """Every sign-in key of a player goes (a password change, the operator's reset, OUT all)."""
        for rec in list(self.by_player.get(key, ())):
            if rec.devkey:
                self.set_devkey(rec, 0)
        return self.store.delete_keys(key)
