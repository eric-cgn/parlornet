"""The core's admin interface (docs/accounts.md, admin panel).

A second listener (127.0.0.1:6599 in the container, never published) for the
admin adapter (adapters/admin.py). Like the frontend channel it is TLS 1.3
with a pre-shared key (parlord/frontends.py), but it accepts only keys
enrolled with the admin role, so no ordinary frontend (web, telnet, IOS...)
can make admin requests, and the player protocol never gains admin power.
Then the admin password is asked as well (docs/security-design.md).

Requests and replies are JSON objects, one per line:

    > {"op": "login", "password": "..."}
    < {"ok": true, "setup": false}
    > {"op": "set", "key": "server.name", "value": "myparlor"}
    < {"ok": true}
    > {"op": "suspend", "name": "eve"}
    < {"ok": false, "error": "no such player"}

Before anything else a connection signs in with the admin password. Until
one is set, the core makes a one-time setup password at each start and prints
it to its log (docker logs); signing in with it allows only set_password.
Wrong passwords lock the admin sign-in on the same schedule as players
(auth.lock_secs, the count halving after quiet stretches), for the client
address the admin adapter passes with the login ("source"), so one address
guessing cannot keep the operator out from another. The counts are kept in
memory (an attacker cannot restart the core).

To clear a forgotten admin password (the next start prints a new setup
password):

    python3 -m parlord.admin --db /data/parlord.db --reset-password
"""
import asyncio
import hashlib
import hmac
import json
import secrets
import sys
import time

from . import auth
from .core import SCORE_MOST, SCORE_SETTINGS, SETTING_LIMITS

ADMIN_PORT = 6599           # the core's admin socket, loopback only
ADMIN_TELNET_PORT = 6510    # the admin adapter (blue-screen menus) players never see
MAXREQ = 65536
IDLE_SECS = 3600
MAX_ADMIN_CONNS = 8
MIN_PASSWORD = 8
SETUP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"    # no 0/o, 1/l/i

# Settings the admin may change, with a check for each: returns the value to
# store, or raises ValueError with the reason. None removes the setting.
NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_")


def _name(v):
    if not 1 <= len(v) <= 32 or not set(v) <= NAME_CHARS:
        raise ValueError("a name is 1 to 32 letters, digits, '.', '-' and '_' (no spaces)")
    return v


def _motd(v):
    v = v.replace("\r\n", "\n").replace("\r", "\n")
    if any(not (" " <= c <= "~" or c == "\n") for c in v):
        raise ValueError("the message is plain ASCII text")
    lines = v.rstrip("\n").split("\n")
    if len(lines) > 20 or len(v) > 2000:
        raise ValueError("the message is at most 20 lines")
    return "\n".join(l.rstrip() for l in lines)


def _url(v):
    """site.contribute_url: the address for the MOTD's {contribute} marker (by default the project's home,
    parlord.PROJECT_URL), and site.docs_url: the documentation site the web links (by default
    parlord.DOCS_URL): at most a MOTD line long (73), printable ASCII without spaces. Empty clears it: the
    line holding the marker is left out, or the web links no docs."""
    v = v.strip()
    if v and (len(v) > 73 or not all(" " < c <= "~" for c in v) or "://" not in v):
        raise ValueError("an address like https://example.org/parlornet, at most 73 characters, no spaces")
    return v


def _turn(v):
    from .core import Limits, parse_duration
    secs = parse_duration(v)
    if secs is None or not Limits.turn_min <= secs <= Limits.turn_max:
        raise ValueError("a time per turn is 1m to %dd (like 5m, 1h, 1d)" % (Limits.turn_max // 86400))
    return str(secs)


def _choice(*allowed):
    def check(v):
        if v not in allowed:
            raise ValueError("one of: " + ", ".join(allowed))
        return v
    return check


def _text(maxlen, ascii_only=True):
    def check(v):
        if len(v) > maxlen or (ascii_only and any(not " " <= c <= "~" for c in v)):
            raise ValueError("at most %d printable ASCII characters" % maxlen)
        return v.strip()
    return check


def _port(v):
    if not v.isdigit() or not 1 <= int(v) <= 65535:
        raise ValueError("a port is a number from 1 to 65535")
    return v


def _addr(v):
    a = v.strip()
    inner = a[a.find("<") + 1:a.rfind(">")] if "<" in a and a.endswith(">") else a
    if not auth.EMAIL_RE.match(inner) or len(a) > 120 or any(not " " <= c <= "~" for c in a):
        raise ValueError("an address like noreply@example.org or Name <noreply@example.org>")
    return a


def _list(v):
    items = [x.strip() for x in v.split(",") if x.strip()]
    for x in items:
        if not set(x) <= NAME_CHARS | set("/:"):
            raise ValueError("a comma-separated list of names")
    return ",".join(items)


def _limit(key):
    default, top, _ = auth.EMAIL_LIMITS[key]

    def check(v):
        if not v.isdigit() or not 0 <= int(v) <= top:
            raise ValueError("a whole number from 0 to %d" % top)
        return v
    return check


def _signup_limit(key):
    """signup.address_cooldown: a length (7d, 1h, 30m, or seconds);
    signup.max_pending: a whole number. Stored as a whole number."""
    default, least, most, _ = auth.SIGNUP_LIMITS[key]
    seconds = key != "signup.max_pending"

    def check(v):
        v = v.strip().lower()
        if seconds:
            from .core import parse_duration
            n = int(v) if v.isdigit() else parse_duration(v)
        else:
            n = int(v) if v.isdigit() else None
        if n is None or not least <= n <= most:
            if seconds:
                raise ValueError("a length from %s to %s, like 7d, 12h or 30m"
                                 % (auth.period_text(least), auth.period_text(most)))
            raise ValueError("a whole number from %d to %d" % (least, most))
        return str(n)
    return check


def _idle(v):
    """presence.idle: a length (30m, 1h, or seconds), 1 minute to 7 days. Stored as seconds."""
    from .core import parse_duration
    v = v.strip().lower()
    n = int(v) if v.isdigit() else parse_duration(v)
    if n is None or not 60 <= n <= 7 * 86400:
        raise ValueError("a length from 1 minute to 7 days, like 30m or 2h")
    return str(n)


def _count(key):
    """A setting of core.SETTING_LIMITS (players.online_max, games.active_max, lists.max...): a whole number
    from 1; 0 or empty clears the setting, so the default applies."""
    top = SETTING_LIMITS[key][1]

    def check(v):
        v = v.strip()
        if v in ("", "0"):
            return ""
        if not v.isdigit() or not 1 <= int(v) <= top:
            raise ValueError("a whole number from 1 to %d (0 or empty: the default)" % top)
        return str(int(v))
    return check


def _points(v):
    """scores.win, scores.draw, scores.loss, scores.resign: a whole number from 0; empty clears the setting,
    so the default applies."""
    v = v.strip()
    if v == "":
        return ""
    if not v.isdigit() or int(v) > SCORE_MOST:
        raise ValueError("a whole number from 0 to %d (empty: the default)" % SCORE_MOST)
    return str(int(v))


SETTINGS = {
    "presence.idle": _idle,
    "scores.show": _choice("on", "off"),
    "server.name": _name,
    "server.motd": _motd,
    "site.contribute_url": _url,
    "site.docs_url": _url,
    "server.turn_default": _turn,
    "auth.signin": _choice(*sorted(auth.SIGNIN)),
    "auth.signup": _choice(*sorted(auth.SIGNUP)),
    "auth.label": _text(40),
    "email.transport": _choice("smtp", "log"),
    "email.smtp_host": _text(120),
    "email.smtp_port": _port,
    "email.smtp_user": _text(200),
    "email.smtp_password": _text(200, ascii_only=False),
    "email.from": _addr,
    "email.blocked_file": _text(200),
    "games.off": _list,
    "adapters.enabled": _list,
    "web.hosts": _list,
}
SETTINGS.update({k: _count(k) for k in SETTING_LIMITS})
SETTINGS.update({k: _points for k in SCORE_SETTINGS})
SETTINGS.update({"email.limit." + k: _limit(k) for k in auth.EMAIL_LIMITS})
SETTINGS.update({k: _signup_limit(k) for k in auth.SIGNUP_LIMITS})
SECRET = ("email.smtp_password",)


# ---- passwords ------------------------------------------------------------------

def hash_password(pw, salt=None):
    """scrypt with a random salt: 'scrypt$n$r$p$salt$hash' (hex)."""
    salt = salt or secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (n, r, p, salt.hex(), h.hex())


def check_password(pw, stored):
    try:
        kind, n, r, p, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024, dklen=len(h) // 2)
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


def setup_password():
    return "-".join("".join(secrets.choice(SETUP_ALPHABET) for _ in range(4)) for _ in range(3))


def banner(setup, port=ADMIN_TELNET_PORT):
    rule = "=" * 72
    return "\n".join([
        rule,
        "  ParlorNet is not set up yet.",
        "",
        "  To set it up, telnet to the admin port:",
        "",
        "      telnet <host> %d" % port,
        "",
        "  or, if that port is only open on the host's loopback, through SSH:",
        "",
        "      ssh -L %d:127.0.0.1:%d you@host   then   telnet 127.0.0.1 %d" % (port, port, port),
        "",
        "  One-time setup password:   %s" % setup,
        "",
        "  The first sign-in sets the permanent admin password. Until then a",
        "  new setup password is printed here at every start.",
        rule,
    ])


# ---- the admin socket -------------------------------------------------------------

class AdminConn:
    def __init__(self):
        self.state = "new"      # new | setup (signed in with the setup password) | admin


class Admin:
    def __init__(self, server, tui_port=ADMIN_TELNET_PORT):
        self.srv = server
        self.st = server.store
        self.tui_port = tui_port
        self.setup = None
        self.login_lock = asyncio.Lock()
        self.fails = {}             # client address -> [wrong passwords, last wrong one, locked until]
        from . import frontends
        self.handshakes = frontends.Handshakes(per_source=2, total=8)   # unauthenticated sockets
        if not self.st.get("admin:pw"):
            self.setup = setup_password()       # in memory only; a new one at each start

    def configured(self):
        return bool(self.st.get("admin:pw"))

    def print_banner(self):
        if not self.configured():
            print(banner(self.setup, self.tui_port), flush=True)

    # ---- connections ------------------------------------------------------------
    async def handle(self, reader, writer):
        """TLS 1.3 with an admin-role key first (no key, no admin socket)."""
        from . import frontends
        if self.srv.admin_conns >= MAX_ADMIN_CONNS:
            writer.transport.abort()
            return
        if self.srv.keyring is None:
            writer.transport.abort()
            return
        src = self.handshakes.enter(writer)
        try:
            who = await frontends.accept(reader, writer, self.srv.keyring, "admin")
        finally:
            self.handshakes.leave(src, writer)
        if who is None:
            return
        conn = AdminConn()
        self.srv.admin_conns += 1
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(reader.readline(), IDLE_SECS)
                except (asyncio.TimeoutError, ValueError, asyncio.LimitOverrunError):
                    break
                if not raw:
                    break
                try:
                    req = json.loads(raw)
                    if not isinstance(req, dict):
                        raise ValueError
                except ValueError:
                    rep = {"ok": False, "error": "a request is one JSON object per line"}
                else:
                    try:
                        rep = await self.dispatch(conn, req)
                    except Exception as e:   # never let one request take the server down
                        print("admin: internal error on %r: %r" % (req.get("op"), e), file=sys.stderr, flush=True)
                        rep = {"ok": False, "error": "internal error"}
                writer.write((json.dumps(rep) + "\n").encode())
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self.srv.admin_conns -= 1
            try:
                writer.close()
            except Exception:
                pass

    async def dispatch(self, conn, req):
        op = str(req.get("op", ""))
        if op == "hello":
            return {"ok": True, "name": self.srv.name, "configured": self.configured()}
        if op == "login":
            return await self.op_login(conn, str(req.get("password", "")), str(req.get("source", ""))[:64])
        if conn.state == "setup" and op != "set_password":
            return {"ok": False, "error": "set the permanent admin password first"}
        if conn.state == "new":
            return {"ok": False, "error": "sign in first"}
        fn = getattr(self, "op_" + op, None) if op.isidentifier() else None
        if fn is None or op == "login":
            return {"ok": False, "error": "no such request"}
        r = fn(conn, req)
        if asyncio.iscoroutine(r):
            r = await r
        return r

    # ---- sign-in --------------------------------------------------------------
    async def op_login(self, conn, pw, source=""):
        async with self.login_lock:         # one at a time, so a wrong answer locks the next try
            return await self._login(conn, pw, source)

    async def _login(self, conn, pw, source=""):
        now = time.time()
        fails, missed, until = self.fails.get(source, (0, 0.0, 0.0))
        if until > now:
            left = int(until - now) + 1
            return {"ok": False, "wait": left,
                    "error": "Too many wrong passwords; try again in %s" % auth.wait_text(left)}
        stored = self.st.get("admin:pw")
        if stored:
            good, state = await asyncio.to_thread(check_password, pw, stored), "admin"   # scrypt: off the loop
        else:
            good, state = bool(self.setup) and auth.same(pw.strip().lower(), self.setup), "setup"
        if not good:
            fails, missed = auth.decay(fails, missed, now)
            fails += 1
            secs = auth.lock_secs(fails)
            if len(self.fails) > 10000:     # bounded: forget addresses whose wait is over
                self.fails = {k: v for k, v in self.fails.items() if v[2] > now}
            self.fails[source] = (fails, now, now + secs)
            print("admin: wrong admin password from %s (%d)" % (source or "?", fails), file=sys.stderr, flush=True)
            return {"ok": False, "wait": secs, "error": "Wrong password; try again in %s" % auth.wait_text(secs)}
        self.fails.pop(source, None)
        conn.state = state
        return {"ok": True, "setup": state == "setup"}

    async def op_set_password(self, conn, req):
        pw = str(req.get("password", ""))
        if len(pw) < MIN_PASSWORD or len(pw) > 128:
            return {"ok": False, "error": "a password is %d to 128 characters" % MIN_PASSWORD}
        if any(not " " <= c <= "~" for c in pw):
            return {"ok": False, "error": "a password is printable ASCII characters"}
        self.st.set("admin:pw", await asyncio.to_thread(hash_password, pw))
        self.setup = None
        conn.state = "admin"
        print("parlord: the admin password was set", flush=True)
        return {"ok": True}

    # ---- settings -------------------------------------------------------------
    def op_get(self, conn, req):
        s = dict(self.srv.settings)
        for k in SECRET:
            s[k] = "set" if s.get(k) else ""
        games = [{"key": k, "name": p.info["name"], "rules": list(p.info["rules"])}
                 for k, p in self.srv.plugins.items()]
        return {"ok": True, "settings": s, "games": games,
                "effective": {"server.name": self.srv.name,
                              "server.motd": self.srv.motd(),
                              "site.contribute_url": self.srv.contribute_url(),
                              "site.docs_url": self.srv.docs_url(),
                              "server.turn_default": str(self.srv.turn_default()),
                              "presence.idle": str(int(self.srv.presence_idle())),
                              **{k: str(self.srv.limit(k)) for k in SETTING_LIMITS},
                              **{k: str(self.srv.points(k.split(".")[1])) for k in SCORE_SETTINGS},
                              "scores.show": "on" if self.srv.scores_shown() else "off",
                              "auth.signin": self.srv.auth.signin.name,
                              "auth.signup": self.srv.auth.signup.name,
                              "auth.label": self.srv.auth.label,
                              "email.transport": auth.transport(self.srv.settings)},
                "limits": {k: [int(self.srv.settings.get("email.limit." + k, d)), d, top, label]
                           for k, (d, top, label) in auth.EMAIL_LIMITS.items()},
                "signup_limits": {k: [self.srv.accounts.signups.setting(k), d, least, most, label]
                                  for k, (d, least, most, label) in auth.SIGNUP_LIMITS.items()}}

    def op_set(self, conn, req):
        key, value = str(req.get("key", "")), req.get("value")
        check = SETTINGS.get(key)
        if check is None:
            return {"ok": False, "error": "no such setting"}
        if value is not None:
            if not isinstance(value, str):
                return {"ok": False, "error": "a setting is text"}
            try:
                value = check(value)
            except ValueError as e:
                return {"ok": False, "error": str(e)}
            if value == "" and key not in ("server.motd", "games.off", "adapters.enabled", "web.hosts",
                                           "site.contribute_url",      # "": cleared (its MOTD line goes)
                                           "site.docs_url"):           # "": cleared (no link to the docs)
                value = None
        note = None
        if key in ("auth.signin", "auth.signup") and value is not None:
            signin = value if key == "auth.signin" else self.srv.auth.signin.name
            signup = value if key == "auth.signup" else self.srv.auth.signup.name
            problem = auth.combo_problem(signin, signup)
            if problem and key == "auth.signup":
                return {"ok": False, "error": "sign-in %s with sign-up %s cannot work: %s" % (signin, signup, problem)}
            if problem:         # a new sign-in method brings the sign-up it needs (both take effect together)
                self.srv.settings["auth.signup"] = auth.DEFAULT_SIGNUP[signin]
                self.st.set_setting("auth.signup", auth.DEFAULT_SIGNUP[signin])
                note = "Sign-up is now %s, which sign-in %s needs." % (auth.DEFAULT_SIGNUP[signin], signin)
        self.srv.set_setting(key, value)
        return dict({"ok": True}, **({"note": note} if note else {}))

    # ---- status -----------------------------------------------------------------
    def email_counts(self):
        st, now = self.st, time.time()
        s = self.srv.settings
        mail = self.srv.auth.mail
        L = mail.lim
        return {"minute": st.email_count(now - 60)[0], "hour": st.email_count(now - 3600)[0],
                "day": st.email_count(now - 86400)[0], "new_day": st.email_count(now - 86400, new=True)[0],
                "blocked_day": st.blocked_since(now - 86400), "paused": s.get("email.paused") == "1",
                "new_held": self.srv.new_signups_held(),
                # the server budgets as they count (since the window, or since the operator cleared them)
                "signup_day": [st.email_count(mail.since(now, 86400), kind="signup")[0], L("per_day")],
                "recovery_day": [st.email_count(mail.since(now, 86400), kind="recovery")[0], L("recovery_per_day")],
                "own_recovery_day": [st.email_count(mail.since(now, 86400), kind="recovery-own")[0],
                                     L("own_recovery_per_day")]}

    def budgets(self):
        """The sign-in and mail budgets, with whether any is used up (an alarm)."""
        b = self.srv.accounts.budgets()
        e = self.email_counts()
        alarm = (b["wrong_server"] >= b["wrong_server_limit"] or
                 any(e[k][0] >= e[k][1] for k in ("signup_day", "recovery_day", "own_recovery_day")))
        return dict(b, **{k: e[k] for k in ("signup_day", "recovery_day", "own_recovery_day")}, alarm=alarm)

    def op_budgets(self, conn, req):
        return {"ok": True, "budgets": self.budgets()}

    def op_clear_budgets(self, conn, req):
        """Free the wrong-answer, password-check and server mail budgets now (not
        the per-name lockouts, nor the caps per name, address or client address)."""
        self.srv.accounts.clear_budgets()
        self.srv.set_setting("budgets.cleared", repr(time.time()))
        print("admin: the operator cleared the sign-in and mail budgets", file=sys.stderr, flush=True)
        return {"ok": True, "budgets": self.budgets()}

    # ---- sign-up limits (docs/accounts.md): pending sign-ups, the address cooldown ----------
    def op_signups(self, conn, req):
        return {"ok": True, "signups": self.srv.accounts.signups.status()}

    def op_clear_cooldowns(self, conn, req):
        """Every address may have its sign-up email again (pending sign-ups go on as they are)."""
        n = self.srv.accounts.signups.clear()
        print("admin: the operator cleared the sign-up cooldown of every address (%d)" % n, file=sys.stderr,
              flush=True)
        return {"ok": True, "cleared": n, "signups": self.srv.accounts.signups.status()}

    def op_clear_cooldown(self, conn, req):
        """One address may have its sign-up email again. The address is hashed for the lookup, and
        neither kept nor logged."""
        addr = str(req.get("address", "")).strip().lower()
        if not auth.EMAIL_RE.match(addr):
            return {"ok": False, "error": "that is not an email address"}
        n = self.srv.accounts.signups.clear(addr)
        print("admin: the operator cleared the sign-up cooldown of one address", file=sys.stderr, flush=True)
        return {"ok": True, "cleared": n, "signups": self.srv.accounts.signups.status()}

    def op_status(self, conn, req):
        srv = self.srv
        tables = {"open": 0, "playing": 0, "done": 0}
        for t in srv.tables.values():
            tables[t.status] = tables.get(t.status, 0) + 1
        return {"ok": True, "name": srv.name, "uptime": int(time.time() - srv.started), "connections": srv.conns,
                "admin_connections": srv.admin_conns, "players": len(srv.players),
                "online": sum(1 for p in srv.players.values() if p.conns > 0), "tables": tables,
                "auth": srv.auth.name, "games": sorted(srv.offered()), "email": self.email_counts(),
                "budgets": self.budgets(), "signups": srv.accounts.signups.status()}

    def op_email(self, conn, req):
        return {"ok": True, "email": self.email_counts()}

    def op_pause_email(self, conn, req):
        self.srv.set_setting("email.paused", "1")
        return {"ok": True}

    def op_resume_email(self, conn, req):
        self.srv.set_setting("email.paused", None)
        if self.srv.new_signups_held():
            # bounces held new sign-ups (docs/accounts.md): count only bounces from now on
            self.srv.set_setting("email.new_ok_since", repr(time.time()))
        return {"ok": True}

    async def op_test_email(self, conn, req):
        to = str(req.get("to", "")).strip().lower()
        if not auth.EMAIL_RE.match(to):
            return {"ok": False, "error": "that is not an email address"}
        st, now, s = self.st, time.time(), self.srv.settings
        lim = lambda k: int(s.get("email.limit." + k, auth.EMAIL_LIMITS[k][0]))  # noqa: E731
        if st.email_count(now - 60)[0] >= lim("per_minute") or st.email_count(now - 3600)[0] >= lim("per_hour"):
            return {"ok": False, "error": "the server-wide sending limit is reached; try again later"}
        st.email_sent(to, "-admin-", False, "admin")
        mailer = auth.Mailer(s)
        sent = await asyncio.to_thread(
            mailer.send, to, "ParlorNet test message",
            "This is a test message from the ParlorNet server %s.\n\nIts email settings work.\n" % self.srv.name)
        if not sent:
            return {"ok": False, "error": "not sent: %s" % getattr(mailer, "error", "unknown error")}
        return {"ok": True, "transport": auth.transport(s)}

    # ---- accounts ---------------------------------------------------------------
    def player_info(self, p):
        return {"name": p.name, "email": p.email or "", "verified": p.verified, "suspended": p.suspended,
                "online": p.conns > 0, "created": int(p.created), "last_seen": int(p.last_seen),
                "locked": max(0, int(p.locked_until - time.time())),
                "pending": max(0, int(p.pending - time.time())) if p.pending else 0,
                "prefs": self.pref_usage(p)}

    def pref_usage(self, p):
        """What each server extension keeps for the player (PRF): {name: [keys, bytes]}, counts only;
        the operator never sees the values. The public port's namespace is shown as "native"."""
        from .core import NATIVE_NS
        return {("native" if ns == NATIVE_NS else ns): [n, b]
                for ns, (n, b) in self.srv.store.pref_usage(p.name.lower()).items()}

    def op_accounts(self, conn, req):
        q = str(req.get("q", "")).strip().lower()
        out = [self.player_info(p) for p in sorted(self.srv.players.values(), key=lambda p: p.name.lower())
               if not q or q in p.name.lower() or q in (p.email or "")]
        return {"ok": True, "total": len(out), "accounts": out[:500]}

    def _player(self, req):
        p = self.srv.player(str(req.get("name", "")))
        if p is None:
            return None, {"ok": False, "error": "no such player"}
        return p, None

    # Account actions go through the accounts component (parlord/accounts.py):
    # each ends the player's sessions, and the core closes their connections.
    def op_suspend(self, conn, req):
        p, e = self._player(req)
        if e:
            return e
        self.srv.suspend_player(p.name, True)
        return {"ok": True}

    def op_unsuspend(self, conn, req):
        p, e = self._player(req)
        if e:
            return e
        self.srv.suspend_player(p.name, False)
        return {"ok": True}

    async def op_reset(self, conn, req):
        """password: a temporary password, shown to the admin to give the player
        (the old one stops working); any method: the lockout is cleared and
        every session ends."""
        p, e = self._player(req)
        if e:
            return e
        good, text, code = await self.srv.reset_player(p.name)
        if not good:
            return {"ok": False, "error": text}
        return dict({"ok": True, "text": text}, **({"code": code} if code else {}))

    async def op_delete(self, conn, req):
        p, e = self._player(req)
        if e:
            return e
        await self.srv.delete_player(p)
        return {"ok": True}

    # ---- tables -----------------------------------------------------------------
    def op_tables(self, conn, req):
        now = time.time()
        out = []
        for t in sorted(self.srv.tables.values(), key=lambda t: int(t.id) if t.id.isdigit() else 0):
            out.append({"id": t.id, "game": "%s/%d" % (t.type, t.ver), "name": t.plugin.info["name"],
                        "rules": t.rules, "status": t.status, "seats": t.seats, "turn_secs": t.turn_secs,
                        "turn_seat": t.turn_seat, "left": max(0, int(t.deadline - now)) if t.deadline else 0,
                        "created": int(t.created), "touched": int(t.touched),
                        "vis": t.vis, "owner": t.owner, "invited": sorted(i["name"] for i in t.invites.values())})
        return {"ok": True, "tables": out}

    # ---- scores ---------------------------------------------------------------------------------------
    def op_reset_scores(self, conn, req):
        """Everyone's score back to nothing (a festival starting, say). The games' record stays: tables,
        events, turns and the points each game gave."""
        n = self.srv.store.reset_scores()
        print("admin: the operator reset the scores (%d players had one)" % n, file=sys.stderr, flush=True)
        return {"ok": True, "players": n}

    async def op_end_table(self, conn, req):
        ok = await self.srv.end_table(str(req.get("id", "")))
        return {"ok": True} if ok else {"ok": False, "error": "no such table"}


def main(argv=None):
    import argparse
    from .store import Store
    ap = argparse.ArgumentParser(prog="parlord.admin", description="ParlorNet admin maintenance")
    ap.add_argument("--db", required=True, help="parlord's SQLite file")
    ap.add_argument("--reset-password", action="store_true",
                    help="forget the admin password; the next start prints a new setup password")
    a = ap.parse_args(argv)
    if not a.reset_password:
        ap.error("nothing to do")
    st = Store(a.db)
    for k in ("admin:pw", "admin:fails", "admin:locked_until"):
        st.db.execute("DELETE FROM meta WHERE key=?", (k,))
    print("The admin password is cleared. Restart ParlorNet; its log then shows a new setup password.")


if __name__ == "__main__":
    main()
