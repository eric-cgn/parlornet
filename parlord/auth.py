"""Sign-in and sign-up (docs/accounts.md, docs/security-design.md).

Two pluggable registries, run as one server-driven dialog:
  sign-in methods (SIGNIN), checked at every sign-in: none, password;
  sign-up methods (SIGNUP), which make the account and what the sign-in
  method needs: none, password, email.
The operator picks one of each; COMBOS lists the pairs that work. Recovery
(a forgotten password) is part of the sign-in method: a temporary password
by mail where sign-up left an address on file, else an operator action.

Sign-up email makes a pending account and mails it a temporary password. The
first sign-in with it confirms the account and asks for a password of the
player's own before anything else; a pending account nobody signs into within
PENDING_SECS is deleted by the accounts component's sweep.

A method only checks a credential or sets one up; sessions, suspension and
everything after a successful check belong to parlord/accounts.py. begin()
answers HELO (sign in, or sign up a free name), USER (sign in only) and REG
(sign up only), answer() answers AUT, and each step is one of a few generic
kinds that any frontend able to show a line and read one back can handle:
    ("ok", player, None[, {"revoke": True}])
                                        done; revoke: a credential was replaced,
                                        the player's other sessions end
    ("ask", field, label, secret[, extra])
                                        the server wants a value; extra may hold
                                        "info" (lines the player needs first),
                                        "note" (lines that help), "yn" (yes/no),
                                        "cred" (it asks for the account's
                                        credential, the only question a client's
                                        password field may answer) and "new"
                                        (a new password is being chosen)
    ("err", code, text)                 refused (text may start with wait=N)
Labels are shown to players; spaces are written as _ on the wire.
"""
import hashlib
import json
import os
import hmac
import re
import secrets
import smtplib
import ssl
import sys
import threading
import time
from email.message import EmailMessage

YES, NO = ("y", "yes"), ("n", "no")


def ask(field, label, secret=False, info=None, yn=False, note=None, cred=False, new=False):
    """An ask step: info lines the player needs before it (a frontend that
    cannot show them is refused the step), note lines that help but are not
    needed (shown where the frontend can), whether it is yes/no, and whether a
    new password is being chosen (new)."""
    extra = {}
    if info:
        extra["info"] = list(info)
    if note:
        extra["note"] = list(note)
    if yn:
        extra["yn"] = True
    if cred:
        extra["cred"] = True     # asks for the account's credential: a client's password field may answer
    if new:
        extra["new"] = True      # a new password: never the one a client keeps, which may not answer it
    return ("ask", field, label, secret, extra)


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}\Z")

# Email sending caps (docs/accounts.md): setting email.limit.<key> -> (default,
# the most the admin panel allows, what it counts).
EMAIL_LIMITS = {
    "per_address_hour": (3, 20, "to one address in an hour"),
    "per_address_day": (6, 50, "to one address in a day"),
    "per_name_hour": (2, 20, "for one name in an hour"),
    "per_name_day": (4, 50, "for one name in a day"),
    "per_connection": (2, 10, "on one connection"),
    "per_source_hour": (4, 50, "asked from one client address in an hour"),
    "per_source_day": (10, 200, "asked from one client address in a day"),
    "per_minute": (3, 10, "sign-up, server-wide in a minute"),
    "per_hour": (20, 200, "sign-up, server-wide in an hour"),
    "per_day": (60, 200, "sign-up, server-wide in a day"),
    "new_per_day": (30, 200, "to new addresses in a day"),
    "recovery_per_minute": (3, 10, "recovery asked by strangers, server-wide in a minute"),
    "recovery_per_hour": (20, 200, "recovery asked by strangers, server-wide in an hour"),
    "recovery_per_day": (60, 200, "recovery asked by strangers, server-wide in a day"),
    "own_recovery_per_hour": (20, 200, "owners' own recovery, server-wide in an hour"),
    "own_recovery_per_day": (60, 200, "owners' own recovery, server-wide in a day"),
}
# Sign-up limits (docs/accounts.md, Sign-up limits): setting -> (default, least, most, what it is).
SIGNUP_LIMITS = {
    "signup.address_cooldown": (7 * 86400, 3600, 365 * 86400,
                                "after a sign-up email, none to that address again for this long (seconds)"),
    "signup.max_pending": (60, 1, 1000, "email sign-ups not yet signed into, at once"),
}
SOURCE_BOUNCES = 2          # addresses a client address had mailed that bounced in a day: no more sign-ups from it
SIGNUPS_PER_SOURCE = (20, 600)  # sign-ups a client address may start in that many seconds
# Wrong answers (docs/security-design.md, Lockouts and rate limits): besides each name's own
# lockout, a budget per client address and one for the whole server. Both count wrong answers
# for names the address is not trusted for (it has not signed in as that name lately); a
# trusted address is never held up by them, only by the name's own lockout.
SOURCE_MISSES = (12, 86400)     # wrong answers one client address may make in that many seconds
GLOBAL_MISSES = (48, 86400)     # wrong answers from untrusted addresses, the whole server
TRUST_SECS = 30 * 86400         # a successful sign-in from an address trusts it for that name this long
CHECKS_PER_SOURCE = (30, 60)    # password checks (scrypt) one client address may ask for in that many seconds
RECOVERY_PER_REQUESTER = 2      # recovery mails for one name asked from one client address in a day
# Words some frontends take at their login prompt instead of a name: telnet's "new" starts a sign-up, and
# so does FIBS's "guest". And names that would pass for the server or its staff in a chat: ParlorNet (the
# name Networdz shows the parlor's lines under), the server's own name, and the usual titles. Nobody can
# sign up as one of them, through any frontend or the protocol.
RESERVED_NAMES = frozenset(("new", "guest", "parlornet", "admin", "administrator", "sysop", "system", "server",
                            "operator", "moderator", "staff", "root"))
# A new account's name (docs/accounts.md): it starts with a letter, and a - or _ comes only between two
# letters or digits. Names from before the rule keep working; sign-in takes any name of NAME_RE.
NEW_NAME_RE = re.compile(r"^[A-Za-z](?:[-_]?[A-Za-z0-9]){0,11}\Z")
NEW_NAME_RULE = ("Names start with a letter and are up to 12 letters, digits, - and _, "
                 "with - and _ only between letters or digits.")


def reserved(name, server_name=None):
    n = (name or "").lower()
    return n in RESERVED_NAMES or bool(server_name and n == server_name.lower())


def reserved_refusal(name):
    return ("err", "RESERVED", "%s is a reserved word here; choose another name" % name.lower())


def new_name_refusal(name, server_name=None):
    """None when name may be a new account's, else the refusal: a reserved word, or outside the rule."""
    if reserved(name, server_name):
        return reserved_refusal(name)
    if not NEW_NAME_RE.match(name or ""):
        return ("err", "NAMERULE", "names start with a letter; - and _ go between letters or digits")
    return None


def transport(settings):
    """'smtp' or 'log' (print mails to the log): as set, else smtp once a relay is named."""
    return settings.get("email.transport") or ("smtp" if settings.get("email.smtp_host") else "log")


LOCK_MAX = 4 * 3600         # the longest wait after wrong answers
DECAY_SECS = 8 * 3600       # each such quiet stretch since the last wrong answer halves the count


def lock_secs(fails):
    """Wait after the nth wrong answer: 1 minute after one, 15 minutes after
    three, growing by the same factor (sqrt 15) each time; at most 4 hours."""
    if fails <= 0:
        return 0
    return int(min(LOCK_MAX, 60 * 15 ** ((fails - 1) / 2.0)))


def decay(fails, missed_at, now=None):
    """(fails, missed_at) after halving the count once for every DECAY_SECS
    without a wrong answer, so a name's count does not stay high for ever."""
    now = now or time.time()
    if fails <= 0 or not missed_at or now - missed_at < DECAY_SECS:
        return fails, missed_at
    n = int((now - missed_at) // DECAY_SECS)
    return fails >> min(n, 31), missed_at + n * DECAY_SECS


def wait_text(secs):
    if secs < 90:
        return "%d second%s" % (secs, "" if secs == 1 else "s")
    if secs < 5400:
        return "%d minutes" % round(secs / 60.0)
    return "%.1f hours" % (secs / 3600.0)


def period_text(secs):
    """A setting's length for people: '7 days', '1 hour', '90 minutes'."""
    for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= n and secs % n == 0:
            return "%d %s%s" % (secs // n, unit, "" if secs == n else "s")
    return wait_text(secs)


def past_time(value, now):
    """A stored time (seconds, as text) if it is a number in the past, else 0.0:
    a time from the future, or not a number, is ignored."""
    try:
        t = float(value or 0)
    except ValueError:
        return 0.0
    return t if 0.0 <= t <= now else 0.0


def tls_context(settings):
    """The TLS context for the relay: the system's CA certificates, the chain
    and the host name checked (the relay host is the TLS server name);
    email.smtp_ca_file adds a CA of the operator's own (a private relay)."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    extra = settings.get("email.smtp_ca_file")
    if extra:
        ctx.load_verify_locations(cafile=extra)
    return ctx


class Mailer:
    """Sends sign-in mail. 'smtp' sends it; 'log' (the default) sends nothing and
    logs only the address and subject, never the body (it holds a password);
    'test', for the test suite only, logs no more than 'log' does and keeps the
    mail in self.last and, as a JSON line, in the file email.test_file names
    (for tests that run the server in a process of its own).

    SMTP is always encrypted and the relay's certificate is always verified
    (tls_context): STARTTLS on any port but 465, implicit TLS (SMTPS) on 465
    or with email.smtp_tls=tls. A relay that offers no STARTTLS, or whose
    certificate does not verify for its host name, gets nothing: no
    credentials and no mail."""

    def __init__(self, settings):
        self.s = settings

    def send(self, to, subject, body):
        kind = transport(self.s)
        self.error = ""
        self.last = {"to": to, "subject": subject, "body": body}
        if kind in ("log", "test"):
            print("mail to %s: %s" % (to, subject), flush=True)
            if kind == "test" and self.s.get("email.test_file"):
                with open(self.s["email.test_file"], "a", encoding="utf-8") as f:
                    f.write(json.dumps(self.last) + "\n")
            return True
        host = self.s.get("email.smtp_host", "")
        port = int(self.s.get("email.smtp_port", "587"))
        msg = EmailMessage()
        msg["From"] = self.s.get("email.from", "")
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        self.permanent = False
        try:
            ctx = tls_context(self.s)
            if port == 465 or self.s.get("email.smtp_tls") == "tls":
                smtp = smtplib.SMTP_SSL(host, port, timeout=15, context=ctx)
            else:
                smtp = smtplib.SMTP(host, port, timeout=15)
            with smtp:
                if not isinstance(smtp, smtplib.SMTP_SSL):
                    smtp.starttls(context=ctx)     # refuses (SMTPNotSupportedError) if the relay offers none
                smtp.login(self.s.get("email.smtp_user", ""), self.s.get("email.smtp_password", ""))
                smtp.send_message(msg)
            return True
        except smtplib.SMTPRecipientsRefused as e:
            codes = [c for c, _ in e.recipients.values()]
            self.permanent = any(500 <= c < 600 for c in codes)
            self.error = "the mail server refused the address (%s)" % ", ".join(str(c) for c in codes)
            print("mail to %s refused: %r" % (to, codes), file=sys.stderr, flush=True)
            return False
        except (OSError, smtplib.SMTPException) as e:
            self.error = str(e) or e.__class__.__name__
            print("mail to %s failed: %r" % (to, e), file=sys.stderr, flush=True)
            return False


# ---- passwords ---------------------------------------------------------------------

PW_MIN, PW_MAX = 8, 64
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
PW_RULE = ("A password needs at least 8 characters (at most 64) and at least three of these four kinds: "
           "lowercase letters, capital letters, digits and symbols.")
HASH_SLOTS = threading.BoundedSemaphore(2)              # scrypt runs at most this many at once
TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"       # temporary passwords: no 0/o, 1/l/i
TEMP_SECS = 86400           # a temporary password (sign-up, forgotten password, the operator's reset) works this long
PENDING_SECS = 86400        # a pending account nobody signs into within this long is deleted
TEMP_TEXT = "%d hours" % (TEMP_SECS // 3600)
NEWPASS_TEXT = "Sign in on the web or by telnet first to choose your password."


def same(a, b):
    """Constant-time equality of two strings of any characters (a non-ASCII
    answer is just a wrong one)."""
    return hmac.compare_digest(str(a).encode("utf-8", "replace"), str(b).encode("utf-8", "replace"))


def hash_password(pw):
    """scrypt with a 16-byte salt; the parameters are stored with the hash."""
    salt = secrets.token_bytes(16)
    with HASH_SLOTS:
        h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32,
                           maxmem=64 * 1024 * 1024)
    return "scrypt$%d$%d$%d$%s$%s" % (SCRYPT_N, SCRYPT_R, SCRYPT_P, salt.hex(), h.hex())


def check_password(pw, stored):
    """True if pw matches a stored scrypt hash (constant-time comparison)."""
    try:
        kind, n, r, p, salt, want = (stored or "").split("$")
        if kind != "scrypt" or int(n) > 2 ** 20:
            return False
        with HASH_SLOTS:
            h = hashlib.scrypt(pw.encode("utf-8", "replace"), salt=bytes.fromhex(salt), n=int(n), r=int(r),
                               p=int(p), dklen=len(want) // 2, maxmem=256 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(h.hex(), want)


def kinds_of(pw):
    """How many of the four kinds a password uses: lowercase, capitals, digits, symbols (anything else)."""
    return sum((any("a" <= c <= "z" for c in pw), any("A" <= c <= "Z" for c in pw),
                any("0" <= c <= "9" for c in pw), any(not c.isalnum() for c in pw)))


def password_problem(pw):
    """Why a chosen password cannot be used, in plain words, or None."""
    if len(pw) < PW_MIN:
        return "That password is too short. " + PW_RULE
    if len(pw) > PW_MAX:
        return "That password is too long. " + PW_RULE
    if any(not " " <= c <= "~" for c in pw):
        return "A password is plain ASCII: letters, digits, symbols and spaces."
    if pw != pw.strip():
        return "A password cannot start or end with a space."
    n = kinds_of(pw)
    if n < 3:
        return "That password uses only %s of the four kinds. %s" % ("one" if n < 2 else "two", PW_RULE)
    return None


def temp_password():
    """A temporary password: three groups of four from TEMP_ALPHABET (about 59 bits), lower case and
    without look-alike characters, so it is easy to read from a mail and type on any keyboard."""
    return "-".join("".join(secrets.choice(TEMP_ALPHABET) for _ in range(4)) for _ in range(3))


def temp_key(value):
    """What of a typed temporary password is checked: lower case, without spaces and dashes."""
    return re.sub(r"[\s-]", "", (value or "").lower())


def temp_cred(pw, now=None):
    """The stored form of a temporary password: when it stops working, then its scrypt hash."""
    return "%d$%s" % (int((now or time.time()) + TEMP_SECS), hash_password(temp_key(pw)))


def temp_live(temp, now=None):
    """True if a stored temporary password has not reached its time."""
    until = (temp or "").split("$", 1)[0]
    return until.isdigit() and (now or time.time()) <= int(until)


def check_temp(value, temp, now=None):
    """True if value is the stored temporary password and it still works."""
    if not temp_live(temp, now):
        return False
    return check_password(temp_key(value), temp.split("$", 1)[1])


def password_lines(name, pw):
    return ["    Name:      %s" % name, "    Password:  %s" % pw]


# ---- shared pieces ---------------------------------------------------------------

class Part:
    """A sign-in or sign-up method. acc: the Accounts component; mail: MailOut."""
    name = "?"
    label = ""

    def __init__(self, acc, mail, label=None):
        self.acc, self.mail = acc, mail
        if label:
            self.label = label

    def locked(self, p):
        """("err", "LOCKED", text) while the player is locked out, else None."""
        left = int(p.locked_until - time.time()) if p else 0
        if left > 0:
            return ("err", "LOCKED", "wait=%d Too many wrong answers for %s; try again in %s"
                    % (left, p.name, wait_text(left)))
        return None

    def budget(self, conn, p):
        """Before a credential is checked: None, or the refusal when the client
        address has used up its wrong answers, or the server its untrusted ones
        (a trusted address, one that signed in as this name lately, is exempt)."""
        return self.acc.miss_budget(getattr(conn, "source", None), p.name)

    def count_miss(self, p, conn=None):
        """Count a wrong answer: the name's count decays first, then the lockout
        grows; the client address's and the server's budgets count it too.
        Returns the wait."""
        self.acc.note_miss(getattr(conn, "source", None), p.name)
        now = time.time()
        p.fails, p.missed_at = decay(p.fails, getattr(p, "missed_at", 0), now)
        p.fails += 1
        p.missed_at = now
        secs = lock_secs(p.fails)
        p.locked_until = now + secs
        self.acc.save_player(p)
        return secs

    def missed(self, p, what, conn=None):
        secs = self.count_miss(p, conn)
        return ("err", "LOCKED", "wait=%d Wrong %s; try again in %s" % (secs, what, wait_text(secs)))

    def passed(self, p):
        if p.fails or p.locked_until:
            p.fails, p.locked_until, p.missed_at = 0, 0.0, 0.0
            self.acc.save_player(p)


def choose_password(pend, field, value, first_label="New password", info=None, cred=True, avoid=None):
    """The choose-and-confirm steps, shared by sign-up, the first sign-in with a temporary password and
    a change. pend holds step "new" or "again". Returns an ask step, or ("chosen", hash). cred: the steps
    may be answered by a client's password field (a sign-up only). avoid: (key, mac) of a temporary
    password the new one may not be. Between the two answers only a keyed HMAC of the first is held (a
    random key per dialog, never stored); the slow scrypt hash is made once, after the two match, so a
    mismatch costs nothing."""
    if pend["step"] == "new" and field == "password":
        if avoid and hmac.compare_digest(
                hmac.new(avoid[0], temp_key(value).encode("utf-8", "replace"), hashlib.sha256).digest(), avoid[1]):
            problem = "Choose a password of your own, not the one we emailed. " + PW_RULE
        else:
            problem = password_problem(value)
        if problem:
            return ask("password", first_label, True, info=[problem], cred=cred, new=True)
        key = secrets.token_bytes(32)
        pend.update(step="again", hkey=key, mac=hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest())
        return ask("confirm", "Type it again", True, cred=cred, new=True)
    if pend["step"] == "again" and field == "confirm":
        key, mac = pend.pop("hkey", None), pend.pop("mac", None)
        if key and hmac.compare_digest(hmac.new(key, value.encode("utf-8", "replace"), hashlib.sha256).digest(), mac):
            return ("chosen", hash_password(value))
        pend.update(step="new")
        return ask("password", first_label, True, info=["The two did not match. " + PW_RULE], cred=cred, new=True)
    return ("err", "STATE", "not what was asked")


SIGNUP_LAST = ("Nothing is kept until you sign in with this password. If you did not sign up for an account, "
               "you can ignore this message and it will expire shortly.")


class MailOut:
    """Mail for sign-up and recovery, within the caps of docs/accounts.md. The
    message carries nothing the requester typed but the address it goes to and
    the name (letters, digits, - and _, at most 12)."""

    def __init__(self, acc):
        self.acc = acc
        self.mailer = Mailer(acc.settings)
        self.blocked_seen = None

    def read_blocked_file(self):
        """Addresses never to mail again, one per line, from the file named by the
        setting email.blocked_file (read when it changes). The public release only
        reads the file; whatever fills it (a mail provider's bounce and complaint
        list, say) is the operator's own, separate service."""
        path = self.acc.settings.get("email.blocked_file")
        if not path:
            return
        try:
            stamp = os.stat(path).st_mtime_ns
            if stamp == self.blocked_seen:
                return
            with open(path, encoding="ascii", errors="replace") as f:
                for line in f:
                    addr = line.strip().lower()
                    if addr and not addr.startswith("#") and EMAIL_RE.match(addr) \
                            and not self.acc.store.email_blocked(addr):
                        self.acc.store.block_email(addr, "listed in the blocked file")
            self.blocked_seen = stamp
        except OSError:
            pass

    @property
    def issuer(self):
        return self.acc.server_name or "ParlorNet"

    def where(self, name):
        """Where to sign in, as a sentence: the web and telnet addresses the server speaks now."""
        places = self.acc.places() if callable(getattr(self.acc, "places", None)) else []
        if not places:
            return "Sign in as %s where you signed up." % name
        return "Sign in as %s %s." % (name, " or ".join(places))

    def lim(self, key, default=None):
        return int(self.acc.settings.get("email.limit." + key, EMAIL_LIMITS[key][0] if default is None
                                         else default))

    def caps(self, conn, name, addr, kind="signup"):
        """None if a mail may go out now, else the refusal. kind is "signup"
        (any mail during a sign-up, including the notice to an address that
        already has a name) or "recovery" (to the address on file of an
        existing account): each has its own server-wide budget, so a flood of
        sign-ups cannot use up recovery. A sign-up mail passes the same checks
        whether or not its address is in use, so the answer never tells."""
        st, now, L = self.acc.store, time.time(), self.lim
        source = getattr(conn, "source", None)
        self.read_blocked_file()
        if self.acc.settings.get("email.paused") == "1":
            return ("err", "PAUSED", "email sign-up is paused on this server; try again later")
        if st.email_blocked(addr):
            return ("err", "NOMAIL", "mail to that address cannot be delivered")
        if getattr(conn, "mails", 0) >= L("per_connection"):
            return ("err", "BUSY", "wait=0 too many emails on this connection; reconnect to try again")
        if source:
            if st.email_count(now - 3600, source=source)[0] >= L("per_source_hour"):
                return ("err", "BUSY", "wait=3600 Too many emails asked from your address; try again in an hour")
            if st.email_count(now - 86400, source=source)[0] >= L("per_source_day"):
                return ("err", "BUSY", "wait=86400 Too many emails asked from your address today")
            if kind == "signup" and st.blocked_for_source(source, now - 86400) >= SOURCE_BOUNCES:
                return ("err", "BUSY", "wait=86400 Mail asked from your address bounced; try again tomorrow")
        if kind == "recovery":
            # Recovery is counted against the asking client address first: at most
            # RECOVERY_PER_REQUESTER for one name from one address a day. An address the
            # name has signed in from lately (its owner's) has its own allowance and is not
            # held up by what strangers asked for that name or address ("recovery-own" mail).
            if not getattr(conn, "operator", False) and st.email_count(
                    now - 86400, name=name, source=source or "", kind=("recovery", "recovery-own"))[0] \
                    >= RECOVERY_PER_REQUESTER:
                return ("err", "BUSY", "wait=86400 You asked for this name's recovery twice today; try again tomorrow")
            if getattr(conn, "operator", False) or self.acc.is_trusted(name, source):
                if now - st.email_count(now - 3600, addr=addr, kind="recovery-own")[1] < 60:
                    return ("err", "BUSY", "wait=60 An email just went to this address; try again in a minute")
                return self.server_recovery_caps(st, now, L, own=True)
        # Recovery to an address on file counts only recovery mail to it, so sign-ups naming that
        # address (each sends its owner a notice) cannot use up its owner's recovery.
        ak = "recovery" if kind == "recovery" else None
        n, last = st.email_count(now - 3600, addr=addr, kind=ak)
        if now - last < 60:
            return ("err", "BUSY", "wait=%d An email just went to this address; try again in a minute"
                    % (60 - (now - last) + 1))
        if n >= L("per_address_hour"):
            return ("err", "BUSY", "wait=3600 Too many emails to this address; try again in an hour")
        if st.email_count(now - 86400, addr=addr, kind=ak)[0] >= L("per_address_day"):
            return ("err", "BUSY", "wait=86400 Too many emails to this address today; try again tomorrow")
        shared = ("signup", "recovery", "admin")        # not the owners' own recovery mail
        # (recovery: no hourly cap per name; each asking address has its own two a day, so one
        # address cannot use up the name's hour, and it takes two to use up its day)
        if (kind != "recovery" and st.email_count(now - 3600, name=name, kind=shared)[0] >= L("per_name_hour")) or \
                st.email_count(now - 86400, name=name, kind=shared)[0] >= L("per_name_day"):
            return ("err", "BUSY", "wait=3600 Too many emails for this name; try again later")
        if kind == "recovery":
            return self.server_recovery_caps(st, now, L)
        if st.email_count(self.since(now, 60), kind="signup")[0] >= L("per_minute") or \
                st.email_count(self.since(now, 3600), kind="signup")[0] >= L("per_hour"):
            return ("err", "BUSY", "wait=300 Sign-up is busy just now; try again in a few minutes")
        if st.email_count(self.since(now, 86400), kind="signup")[0] >= L("per_day"):
            return ("err", "BUSY", "wait=86400 Email sign-up is full for today; try again tomorrow")
        if st.email_count(now - 86400, new=True)[0] >= L("new_per_day"):
            return ("err", "BUSY", "wait=86400 New sign-ups are full for today; try again tomorrow")
        if self.acc.new_signups_held():
            return ("err", "PAUSED", "new email sign-ups are paused; try again later")
        return None

    def since(self, now, secs):
        """The start of a server-wide window: its length back, or when the
        operator last cleared the budgets (admin menus), whichever is later.
        A clear time that is not in the past (a hand-edited settings file) is
        ignored: it would otherwise switch the server-wide mail limits off."""
        return max(now - secs, past_time(self.acc.settings.get("budgets.cleared"), now))

    def server_recovery_caps(self, st, now, L, own=False):
        """The server's recovery budgets: one for strangers' requests (kind
        "recovery"), one for owners' own (kind "recovery-own": asked from an
        address the name signed in from, or finished by signing in with the
        mailed password), so strangers' resets never use up the owners'."""
        if own:
            if st.email_count(self.since(now, 3600), kind="recovery-own")[0] >= L("own_recovery_per_hour"):
                return ("err", "BUSY", "wait=300 Email is busy just now; try again in a few minutes")
            if st.email_count(self.since(now, 86400), kind="recovery-own")[0] >= L("own_recovery_per_day"):
                return ("err", "BUSY", "wait=86400 Email is full for today; try again tomorrow")
            return None
        if st.email_count(self.since(now, 60), kind="recovery")[0] >= L("recovery_per_minute") or \
                st.email_count(self.since(now, 3600), kind="recovery")[0] >= L("recovery_per_hour"):
            return ("err", "BUSY", "wait=300 Email is busy just now; try again in a few minutes")
        if st.email_count(self.since(now, 86400), kind="recovery")[0] >= L("recovery_per_day"):
            return ("err", "BUSY", "wait=86400 Email is full for today; try again tomorrow")
        return None

    SEND_SECS_MAX = 15.0

    def send(self, conn, name, addr, subject, body, kind="signup", hold=False):
        """None if sent, else the refusal. hold: a sign-up mail the address cooldown holds back. It passes
        the same caps and is counted as if sent, and takes about as long as sending has lately, but nothing
        goes out, so the requester cannot tell it from a mail that went."""
        refused = self.caps(conn, name, addr, kind)
        if refused:
            return refused
        source = getattr(conn, "source", None)
        if kind == "recovery" and (getattr(conn, "operator", False) or self.acc.is_trusted(name, source)):
            kind = "recovery-own"
        self.acc.store.email_sent(addr, name, kind == "signup", kind, source or "")
        conn.mails = getattr(conn, "mails", 0) + 1
        if hold:
            time.sleep(getattr(self, "send_secs", 0.0))
            return None
        began = time.monotonic()
        sent = self.mailer.send(addr, subject, body)
        if kind == "signup":            # how long a send takes, for held mail to take as long
            took = min(self.SEND_SECS_MAX, time.monotonic() - began)
            self.send_secs = 0.8 * getattr(self, "send_secs", took) + 0.2 * took
        if sent:
            return None
        if getattr(self.mailer, "permanent", False):
            self.acc.store.block_email(addr, "refused by the mail server")
            return ("err", "NOMAIL", "mail to that address cannot be delivered")
        return ("err", "NOMAIL", "the email could not be sent; try again later")

    def welcome(self, conn, name, addr, pw):
        """A sign-up's mail: the name, its temporary password, where to sign in, and that it expires.
        None if sent, else the refusal."""
        body = ("Here is the password for your new ParlorNet account on %s.\n\n%s\n\n"
                "%s It works for %s. The first time you sign in with it, you choose a password of "
                "your own.\n\n%s\n" % (self.issuer, "\n".join(password_lines(name, pw)), self.where(name),
                                       TEMP_TEXT, SIGNUP_LAST))
        return self.send(conn, name, addr, "Your ParlorNet password", body)

    def forgot(self, conn, p, pw):
        """A forgotten password's mail to the address on file: a temporary password. None, or the refusal."""
        body = ("Someone asked for a new password for %s on %s, perhaps you.\n\n%s\n\n"
                "%s It works for %s. When you sign in with it, you choose a new password. Until then "
                "your old password keeps working.\n\nIf you did not ask for this, ignore this message; "
                "nothing changes.\n" % (p.name, self.issuer, "\n".join(password_lines(p.name, pw)),
                                        self.where(p.name), TEMP_TEXT))
        return self.send(conn, p.name, p.email, "Your new ParlorNet password", body, kind="recovery")

    def held(self, conn, name, addr):
        """A sign-up whose address had its one sign-up email within the cooldown: no mail, but the same
        caps, count and time as one (send, hold). None, or the refusal any sign-up mail would get."""
        return self.send(conn, name, addr, None, None, hold=True)

    def notice(self, conn, name, addr, owner):
        """A sign-up gave an address that already has a name: tell its owner
        (the sign-up dialog goes on exactly as for a free address, so it
        never tells who owns what). It is that address's one sign-up email
        for the cooldown, like any other. None if sent, else the refusal."""
        return self.send(conn, name, addr, "Your ParlorNet address",
                         "Someone asked to sign up a new name on %s with this address. It already has a\n"
                         "name there: %s. To play, sign in as %s. If you forgot your password, answer\n"
                         "reset when the server asks for it, and we email you a new one.\n\n"
                         "If you did not ask for this, ignore this message.\n"
                         % (self.issuer, owner.name, owner.name))


# ---- sign-in methods: checked at every sign-in ----------------------------------------

class NoneSignIn(Part):
    """The name alone (a LAN, a festival): asks nothing."""
    name = "none"
    needs = None

    def begin(self, conn, p, given):
        return ("ok", p, None)

    def answer(self, conn, field, value):
        return ("err", "STATE", "nothing was asked")


RESET_SENT = ["We emailed a new password to the address on file.",
              "It works for 24 hours; your old password works until you use it.",
              "Type the new one here, or sign in with it later."]
CHANGE_NOW = ["That was a temporary password. Choose a password of your own now."]


class PasswordSignIn(Part):
    """A password the player chose, stored as an scrypt hash, or a temporary
    password (p.temp: until when, then its scrypt hash) from a sign-up mail, a
    forgotten password's mail or the operator's reset. A right temporary
    password confirms a pending account and asks at once for a password of the
    player's own; only then is the player signed in. Forgotten: with an
    address on file, answer reset for a temporary password by mail (this goes
    ahead while the name is locked: it has its own limits, and a temporary
    password is checked while locked); the old password keeps working until
    the temporary one is used. Without an address, the operator's reset gives
    one."""
    name = "password"
    label = "Password"
    needs = "password"

    @staticmethod
    def recoverable(p):
        return bool(p.email) and not p.pending

    def begin(self, conn, p, given):
        lock = self.locked(p)
        if lock and not self.recoverable(p) and not temp_live(p.temp):
            return lock
        if given:
            return lock or ("err", "SYNTAX", "HELO name, then the password when it is asked")
        conn.pending = {"flow": "signin", "name": p.name, "step": "password"}
        if lock:
            left = int(p.locked_until - time.time())
            note = ["Too many wrong answers for %s: sign-in waits %s." % (p.name, wait_text(left))]
            if self.recoverable(p):
                note.append("Answer reset to get a new password by email now.")
        elif p.pending:
            note = ["Type the password we emailed you."]
        else:
            note = ["Forgot your password? Answer reset and we email you a new one."] if self.recoverable(p) else None
        return ask("password", self.label, True, cred=True, note=note)

    def change_begin(self, conn, p):
        conn.pending = {"flow": "change", "name": p.name, "step": "current"}
        return ask("current", "Current password", True)

    def answer(self, conn, field, value):
        pend = conn.pending
        p = self.acc.player(pend["name"])
        if p is None:
            conn.pending = None
            return ("err", "STATE", "no such player")
        step = pend["step"]
        if step in ("password", "current") and field == step:
            if step == "password" and value.strip().lower() == "reset" and self.recoverable(p):
                pw = temp_password()
                refused = self.mail.forgot(conn, p, pw)
                if refused:
                    conn.pending = None
                    return refused
                p.temp = temp_cred(pw)
                self.acc.save_player(p)
                return ask("password", self.label, True, info=RESET_SENT)
            refused = self.acc.rate(conn, "check", CHECKS_PER_SOURCE)
            if refused:
                conn.pending = None
                return refused
            # A temporary password (about 59 bits, mailed or given by the operator) works while the name is
            # locked and while the wrong-answer budgets are spent, so recovery by mail always goes ahead.
            if step == "password" and check_temp(value, p.temp):
                return self.change_now(pend, p, value)
            lock = self.locked(p) or (self.budget(conn, p) if step == "password" else None)
            if lock:
                conn.pending = None
                return lock
            if check_password(value, p.cred):
                self.passed(p)
                if step == "current":
                    pend.update(step="new")
                    return ask("password", "New password", True, info=[PW_RULE], new=True)
                conn.pending = None
                return ("ok", p, None)
            conn.pending = None
            return self.missed(p, "password", conn)
        if step in ("new", "again"):
            r = choose_password(pend, field, value, cred=False, avoid=pend.get("avoid"))
            if r[0] != "chosen":
                if r[0] == "err":
                    conn.pending = None
                return r
            conn.pending = None
            p.cred, p.temp, p.pending = r[1], "", 0.0
            p.fails, p.locked_until, p.missed_at = 0, 0.0, 0.0
            self.acc.save_player(p)
            return ("ok", p, None, {"revoke": True})    # a new password: other sessions end
        conn.pending = None
        return ("err", "STATE", "not what was asked")

    def change_now(self, pend, p, value):
        """A right temporary password: the account is confirmed (a pending one keeps its name from now on)
        and the player chooses a password of their own before signing in."""
        if p.pending:
            p.pending, p.verified = 0.0, True
            other = self.acc.player_by_email(p.email)
            if other is not None and other is not p:
                p.email = None              # an address belongs to one account: the one confirmed first
        else:
            self.acc.store.recovery_done(p.name)    # the owner's: off the strangers' budget, onto the owners'
        p.fails, p.locked_until, p.missed_at = 0, 0.0, 0.0
        self.acc.save_player(p)
        key = secrets.token_bytes(32)
        pend.update(step="new", avoid=(key, hmac.new(key, temp_key(value).encode("utf-8", "replace"),
                                                     hashlib.sha256).digest()))
        return ask("password", "New password", True, info=CHANGE_NOW + [PW_RULE], new=True)

    def operator_reset(self, p):
        """A temporary password for the operator to give the player: the old password stops working, the
        temporary one works within TEMP_SECS, and then a new password is chosen. (text, password)."""
        pw = temp_password()
        p.cred, p.temp = "", temp_cred(pw)
        return ("Temporary password for %s: %s. They sign in with it within 24 hours and choose a new "
                "password." % (p.name, pw), pw)


# ---- sign-up methods: make the account and what the sign-in method needs ------------------

class NoneSignUp(Part):
    """Claim a free name: nothing is asked (with sign-in none)."""
    name = "none"

    def begin(self, conn, name, given, explicit=False):
        return create(self.acc, name)

    def answer(self, conn, field, value):
        return ("err", "STATE", "nothing was asked")


class PasswordSignUp(Part):
    """Choose a password and confirm it (with sign-in password)."""
    name = "password"

    def begin(self, conn, name, given, explicit=False):
        if given:
            return ("err", "SYNTAX", "HELO name alone; then choose a password when it is asked")
        conn.pending = {"flow": "signup", "name": name, "step": "new"}
        first = ("New account %s: choose a password." if explicit else
                 "%s is new here. To sign up, choose a password.") % name
        return ask("password", "Choose a password", True, cred=True, note=[first, PW_RULE], new=True)

    def answer(self, conn, field, value):
        pend = conn.pending
        r = choose_password(pend, field, value, "Choose a password")
        if r[0] != "chosen":
            if r[0] == "err":
                conn.pending = None
            return r
        conn.pending = None
        return create(self.acc, pend["name"], cred=r[1])


def create(acc, name, cred="", email=None, temp="", pending=0.0):
    """The new account, unless the name was taken while its sign-up ran, is a reserved word or breaks the rule.
    pending: a sign-up email's account, kept until then unless someone signs in with its temporary password
    (temp)."""
    refusal = new_name_refusal(name, getattr(acc, "server_name", None))
    if refusal:
        return refusal
    if acc.player(name):
        return ("err", "NAMEINUSE", "that name was just taken")
    p = acc.player(name, create=True)
    p.cred, p.temp, p.email, p.pending = cred, temp, email, pending
    acc.save_player(p)
    return ("ok", p, None)


class Signups:
    """The sign-up limits of docs/accounts.md, held by the accounts component:
    how many pending accounts there may be at once, and the address cooldown
    (in the database, by addr_hash only; the address itself is never stored
    for it)."""

    def __init__(self, acc):
        self.acc = acc
        self.lock = threading.Lock()

    def setting(self, key):
        default, least, most, _ = SIGNUP_LIMITS[key]
        try:
            v = int(self.acc.settings.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(least, min(most, v))

    @property
    def cooldown(self):
        return self.setting("signup.address_cooldown")

    def again_lines(self):
        """Shown after every sign-up mail step, whether a mail went or not."""
        return ["If you haven't received the email, it may take a while;",
                "you can ask again after %s." % period_text(self.cooldown)]

    def count(self, now=None):
        return self.acc.pending_count(now)

    def full(self, now=None):
        """The refusal while signup.max_pending accounts are pending, else None."""
        now = now or time.time()
        waiting = [p.pending for p in list(self.acc.players.values()) if p.pending > now]
        if len(waiting) < self.setting("signup.max_pending"):
            return None
        left = int(min(waiting) - now) + 1
        return ("err", "BUSY", "wait=%d Too many sign-ups in progress; try again in %s" % (left, wait_text(left)))

    def first_mail(self, h, now=None):
        """True if no sign-up email went to this address within the cooldown; the cooldown starts now
        (unhold() if the mail then does not go). False: hold the mail back."""
        now = now or time.time()
        st = self.acc.store
        with self.lock:
            if st.cooldown_until(h) > now:
                return False
            st.set_cooldown(h, now + self.cooldown)
            return True

    def unhold(self, h):
        with self.lock:
            self.acc.store.clear_cooldown(h)

    def clear(self, addr=None):
        """For the operator: one address's cooldown (the address is hashed for the lookup and never kept)
        or every one. Returns how many were running."""
        st = self.acc.store
        with self.lock:
            return st.clear_cooldown(None if addr is None else st.addr_hash(addr))

    def status(self, now=None):
        now = now or time.time()
        return {"pending": self.count(now), "max_pending": self.setting("signup.max_pending"),
                "pending_ttl": PENDING_SECS, "address_cooldown": self.cooldown,
                "cooldowns": self.acc.store.cooldowns(now)}


class EmailSignUp(Part):
    """Sign up with an address: a pending account is made and its temporary
    password mailed; the dialog then asks for that password, which signs in
    like any later sign-in with it (PasswordSignIn). One sign-up email per
    address in the cooldown, at most signup.max_pending pending accounts at
    once, each for PENDING_SECS (Signups)."""
    name = "email"

    def __init__(self, acc, mail, signin):
        super().__init__(acc, mail)
        self.signin = signin

    def begin(self, conn, name, given, explicit=False):
        """HELO for a free name first asks whether to sign up (the name may be a typing mistake); REG, an
        explicit sign-up, goes straight to the address."""
        if given:
            return ("err", "NOPLAYER", "no such player; HELO name alone to sign up")
        if explicit:
            conn.pending = {"flow": "signup", "name": name, "step": "email"}
            return self.address_question(["New account: %s." % name])
        conn.pending = {"flow": "signup", "name": name, "step": "confirm"}
        return ask("confirm", "Sign up as %s? (y/n)" % name, yn=True,
                   info=["%s is not signed up on this server." % name])

    @staticmethod
    def address_question(first=()):
        info = ["We email you a password to sign in with; then you choose your own."]
        return ask("email", "Email address", info=list(first) + info)

    def answer(self, conn, field, value):
        pend = conn.pending
        value = value.strip()
        step = pend["step"]
        if step == "confirm" and field == "confirm":
            if value.lower() in NO:
                conn.pending = None
                return ("err", "CANCELLED", "not signed up")
            if value.lower() not in YES:
                return ask("confirm", "Sign up as %s? (y/n)" % pend["name"], yn=True)
            pend["step"] = "email"
            return self.address_question()
        if step == "email" and field == "email":
            addr = value.lower()
            if not EMAIL_RE.match(addr):
                return ask("email", "Email address", info=["That is not an email address."])
            name = pend["name"]
            if self.acc.player(name):
                conn.pending = None
                return ("err", "NAMEINUSE", "that name was just taken")
            refused = self.mail_step(conn, name, addr)
            if refused:
                conn.pending = None
                return refused
            conn.pending = {"flow": "signin", "name": name, "step": "password"}
            return ask("password", "Password from the email", True,
                       info=["We emailed a password for %s to %s." % (name, addr),
                             "It works for %s. Type it here, or sign in with it later." % TEMP_TEXT]
                       + self.acc.signups.again_lines())
        conn.pending = None
        return ("err", "STATE", "not what was asked")

    def mail_step(self, conn, name, addr):
        """The address step's mail and the pending account, within the sign-up limits: None, or the
        refusal. Every path answers alike, makes a pending account the same way and does the same hashing
        work, so the requester learns neither whether the address has a name (finding 5) nor whether a
        mail went:
          - an address mailed for a sign-up within the cooldown: no mail (held);
          - an address that already has a name: its one sign-up email is a notice to its owner;
          - else the temporary password, the only one that signs in to the account.
        Held mail passes the same caps, is counted and takes as long as a mail (MailOut.send). The other
        two make the account with the hash of a temporary password nobody was sent, so nothing signs in
        to it and it is deleted when its time ends."""
        sig = self.acc.signups
        full = sig.full()
        if full:
            return full
        h = self.acc.store.addr_hash(addr)
        first = sig.first_mail(h)
        owner = self.acc.player_by_email(addr) if first else None
        pw = temp_password()
        temp = temp_cred(pw)
        if not first:
            refused = self.mail.held(conn, name, addr)
        elif owner is not None:
            refused = self.mail.notice(conn, name, addr, owner)
        else:
            refused = self.mail.welcome(conn, name, addr, pw)
        if refused:
            if first:
                sig.unhold(h)                   # nothing went: the address may have its email later
            return refused
        mailed = first and owner is None
        r = create(self.acc, name, temp=temp, email=addr if mailed else None, pending=time.time() + PENDING_SECS)
        return None if r[0] == "ok" else r


# ---- the two registries and what goes together --------------------------------------------

SIGNIN = {"none": NoneSignIn, "password": PasswordSignIn}
SIGNUP = {"none": NoneSignUp, "password": PasswordSignUp, "email": EmailSignUp}
COMBOS = {("none", "none"), ("password", "password"), ("password", "email")}
DEFAULT_SIGNUP = {"none": "none", "password": "password"}


def combo_problem(signin, signup):
    """Why a sign-in and sign-up pair cannot work together, or None."""
    if signin not in SIGNIN:
        return "sign-in is one of: %s" % ", ".join(SIGNIN)
    if signup not in SIGNUP:
        return "sign-up is one of: %s" % ", ".join(SIGNUP)
    if (signin, signup) in COMBOS:
        return None
    return {
        "none": "with sign-in none anyone is anyone; sign-up is none too",
        "password": "sign-in password needs a sign-up that sets one: password or email",
    }[signin]


class Auth:
    """The operator's sign-in method and sign-up method, as one dialog."""

    def __init__(self, acc, signin, signup, label=None):
        self.acc = acc
        self.starts = {}            # client address -> times it started a sign-up (SIGNUPS_PER_SOURCE)
        self.starts_lock = threading.Lock()
        self.mail = MailOut(acc)
        self.signin = SIGNIN[signin](acc, self.mail, label)
        self.signup = EmailSignUp(acc, self.mail, self.signin) if signup == "email" else SIGNUP[signup](acc, self.mail)
        self.name = "%s+%s" % (signin, signup)      # sessions belong to this pair
        self.label = self.signin.label
        self.mailer = self.mail.mailer

    def greeting(self):
        """AUTH=signin SIGNUP=signup, and LABEL when the operator set its own."""
        g = "AUTH=%s SIGNUP=%s" % (self.signin.name, self.signup.name)
        if self.label and self.label != type(self.signin).label:
            g += " LABEL=%s" % self.label.replace(" ", "_")
        return g

    def begin(self, conn, name, given, mode="any"):
        """mode "any" (HELO): sign in a name that has an account, else sign it up; "signin" (USER): sign in
        only, an unknown name is refused (ERR NOPLAYER); "signup" (REG): sign up only, a taken name is
        refused (ERR NAMEINUSE). Neither tells more than HELO always has (a free name starts a sign-up
        there); unknown names asked for with USER count against the client address as sign-ups do."""
        p = self.acc.player(name)
        if mode == "signup":
            if given:
                return ("err", "SYNTAX", "REG name; then answer what is asked")
            return self.start_signup(conn, name, p, None, True)
        if p is not None and p.suspended:
            return ("err", "SUSPENDED", "this name is suspended")
        if p is not None:
            return self.signin.begin(conn, p, given)
        if mode == "signin":
            busy = self.acc.rate(conn, "unknown", SIGNUPS_PER_SOURCE)
            if busy:
                return busy
            return ("err", "NOPLAYER", "no account named %s here" % name)
        return self.start_signup(conn, name, None, given, False)

    def start_signup(self, conn, name, p, given, explicit):
        """The sign-up method for a free name that is not a reserved word and fits the rule for new names."""
        refusal = new_name_refusal(name, getattr(self.acc, "server_name", None))
        if refusal:
            return refusal
        if p is not None:
            return ("err", "NAMEINUSE", "the name %s is taken; choose another" % name)
        if not self.signup_allowed(getattr(conn, "source", None)):
            return ("err", "BUSY", "wait=600 Too many sign-ups from your address; try again later")
        if self.signup.name == "email" and not given:
            full = self.acc.signups.full()
            if full:
                return full
        return self.signup.begin(conn, name, given, explicit)

    def signup_allowed(self, source, now=None):
        """At most SIGNUPS_PER_SOURCE sign-ups started from one client address
        (where it is known: the public port, and server extensions that pass
        it with SRC) in a sliding window, kept in memory."""
        if not source:
            return True
        n, secs = SIGNUPS_PER_SOURCE
        now = now or time.time()
        with self.starts_lock:
            if len(self.starts) > 10000:
                for k in [k for k, v in self.starts.items() if not v or now - v[-1] > secs]:
                    del self.starts[k]
            q = [t for t in self.starts.get(source, ()) if now - t < secs]
            if len(q) >= n:
                self.starts[source] = q
                return False
            q.append(now)
            self.starts[source] = q
            return True

    def answer(self, conn, field, value):
        pend = getattr(conn, "pending", None)
        if not pend:
            return ("err", "STATE", "nothing was asked")
        return (self.signup if pend.get("flow") == "signup" else self.signin).answer(conn, field, value)

    def change_begin(self, conn, p):
        if not hasattr(self.signin, "change_begin"):
            return ("err", "NOAUTH", "this server's sign-in has no password to change")
        return self.signin.change_begin(conn, p)
