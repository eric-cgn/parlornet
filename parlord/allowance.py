"""Connection classes and the time allowance (docs/protocol.md, Time allowance).

Every connection on the public port is in a class. A connection from one of the addresses in the setting
dialin.gateways that opens with a PROXY protocol v2 header for a call (parlord/native.py) is in the dial-in
class, dialin. Every other connection is in the default class. Each class has the same three limits. Each is
a setting in minutes, and 0 means no limit:

    CLASS.call_mins          one connection, counted from when its call came up, which the gateway says
    CLASS.user_month_mins    one player in a calendar month (UTC), counted while they are signed in
    CLASS.pool_month_mins    the whole parlor in a calendar month, counted from connecting

The dial-in class has 75, 60 and 150 minutes by default, and the default class has no limits. A setting comes
from the admin store or the settings file, else from the environment as PARLOR_ and the key in capitals with
_ for the dot (dialin.call_mins is PARLOR_DIALIN_CALL_MINS), else the default here. Without dialin.gateways no
connection is in the dial-in class. A connection is metered when its class has a limit at the moment it
connects. The values are read again each time, so a change takes effect at once. Connections on the extension
channel are not metered, except one that the dial-in extension says carries a call (DIA, parlord/core.py), which
is in the dial-in class.

The minutes used are kept by month, class and player in the table allowance, with the empty name for the
pool, and in memory while the server runs. A connection's time is charged when it signs in or out, when it
closes, and at every sweep. Time not yet charged counts too, so the time left is always current. The time
left for a connection is the smallest of the call's, the player's month and the pool's, or None when none of
them has a limit. At NOTICE_SECS left the connection gets a notice, an INF line in front of its next reply.
When no time is left it gets ERR LIMIT with the limit that ran out, and it is closed. A player whose month is
used up may still sign in. They are told so and closed GRACE_SECS later.
"""
import calendar
import os
import sys
import time

from .accounts import proxy_list, trusted_proxy

DEFAULT, DIALIN = "default", "dialin"
CLASSES = (DEFAULT, DIALIN)
LIMITS = ("call_mins", "user_month_mins", "pool_month_mins")
DEFAULTS = {DEFAULT: (0, 0, 0), DIALIN: (75, 60, 150)}
MOST_MINS = 31 * 24 * 60          # the most a limit takes: a month of minutes
GATEWAYS = "dialin.gateways"
PRIOR_MOST = 24 * 3600           # the most of a gateway's seconds before the connection that are believed
NOTICE_SECS = (300, 60)          # the notices: 5 minutes and 1 minute left
GRACE_SECS = 20                  # a player whose month is used up is closed this long after signing in
CHECK_SECS = 5.0                 # the longest a metered connection goes without its time being looked at
# How each class's limits read in words: what one connection is, and the word before "minutes" and "time".
WORDS = {DEFAULT: ("connection", ""), DIALIN: ("call", "dial-in ")}


def setting_keys():
    """Every setting of this module: dialin.gateways and CLASS.LIMIT for each class."""
    return [GATEWAYS] + ["%s.%s" % (c, k) for c in CLASSES for k in LIMITS]


def env_name(key):
    """The environment's name for a setting: dialin.call_mins -> PARLOR_DIALIN_CALL_MINS."""
    return "PARLOR_" + key.upper().replace(".", "_")


def setting(settings, key):
    """A setting's text: the admin store or the settings file (settings), else the environment, else ""."""
    v = settings.get(key)
    if v is None:
        v = os.environ.get(env_name(key), "")
    return (v or "").strip()


def minutes(text):
    """A limit's text -> a whole number of minutes from 0 to MOST_MINS, or None when it is not one."""
    return int(text) if text.isdigit() and int(text) <= MOST_MINS else None


def month_of(at):
    """The calendar month (UTC) of a time: "2026-11"."""
    return time.strftime("%Y-%m", time.gmtime(at))


def next_month(at):
    """When the calendar month (UTC) after the month of at starts."""
    t = time.gmtime(at)
    y, m = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
    return float(calendar.timegm((y, m, 1, 0, 0, 0)))


def month_start(at):
    """When the calendar month (UTC) that at is in started."""
    t = time.gmtime(at)
    return float(calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0)))


def mins_text(secs):
    """A notice's time in whole minutes, rounded up: "5 minutes" for 4 and a half, "1 minute" for 40 seconds."""
    m = max(1, -(-int(secs) // 60))
    return "1 minute" if m == 1 else "%d minutes" % m


class Meter:
    """One metered connection: its class, when its call started (wall clock), when it was last charged, and
    the player it charges (a lower-case name) while one is signed in, else None."""

    def __init__(self, cls, start, now):
        self.cls, self.start, self.charged, self.who = cls, start, now, None


class Allowance:
    """The time allowance of one server: settings (the server's dict, read live), store (parlord/store.py) for
    the minutes used, clock for the wall clock (tests give their own)."""

    def __init__(self, settings, store, clock=time.time):
        self.settings, self.store, self.clock = settings, store, clock
        self.meters = set()
        self.used = {}                  # (month, class, player) -> seconds; player "" for the pool
        self.loaded = set()             # (month, class) read from the store
        self._nets = (None, [])         # (the text of dialin.gateways, its networks)

    # ---- settings ----------------------------------------------------------------------------------------
    def gateways(self):
        """dialin.gateways as networks. A value that is not a list of addresses or networks counts as none, and
        the log says so once."""
        text = setting(self.settings, GATEWAYS)
        if text != self._nets[0]:
            try:
                nets = proxy_list(text, GATEWAYS)
            except ValueError as e:
                print("parlord: no dial-in gateway is trusted, because %s" % e, file=sys.stderr, flush=True)
                nets = []
            self._nets = (text, nets)
        return self._nets[1]

    def trusted(self, ip):
        """True when ip (an ip_address, or None) is one of the dial-in gateways."""
        return trusted_proxy(ip, self.gateways())

    def limits(self, cls):
        """A class's limits in seconds: (call, user's month, pool's month), None for each that has none."""
        out = []
        for key, default in zip(LIMITS, DEFAULTS[cls]):
            n = minutes(setting(self.settings, "%s.%s" % (cls, key)))
            n = default if n is None else n
            out.append(n * 60 if n else None)
        return tuple(out)

    # ---- the minutes used ----------------------------------------------------------------------------------
    def _load(self, month, cls):
        if (month, cls) not in self.loaded:
            for player, secs in self.store.allowance_used(month, cls):
                self.used[(month, cls, player)] = secs
            self.loaded.add((month, cls))

    def used_secs(self, month, cls, player=""):
        """The seconds charged in a month to a player of a class, or with player "" to its pool."""
        self._load(month, cls)
        return self.used.get((month, cls, player), 0.0)

    def add(self, cls, who, start, end):
        """Charge the time from start to end to the class's pool and, when who is a player, to them, each
        part in its own month."""
        while end > start:
            cut = min(end, next_month(start))
            month = month_of(start)
            self._load(month, cls)
            for player in ("", who) if who else ("",):
                k = (month, cls, player)
                self.used[k] = self.used.get(k, 0.0) + (cut - start)
                self.store.add_allowance(month, cls, player, cut - start)
            start = cut

    def forget_old(self, now=None):
        """Forget the months before the previous one, in memory and in the store."""
        keep = month_of(month_start(now or self.clock()) - 1)
        self.store.forget_allowance_before(keep)
        for k in [k for k in self.used if k[0] < keep]:
            del self.used[k]
        self.loaded = {k for k in self.loaded if k[0] >= keep}

    def forget_player(self, name):
        """An account is deleted (the store has dropped its rows): its minutes go from memory too, and a call
        it is signed in on charges only the pool from now on. The pool keeps every minute."""
        key, now = name.lower(), self.clock()
        for m in self.meters:
            if m.who == key:
                self.add(m.cls, None, m.charged, now)
                m.charged, m.who = max(m.charged, now), None
        for k in [k for k in self.used if k[2] == key]:
            del self.used[k]

    # ---- meters --------------------------------------------------------------------------------------------
    def open(self, cls, prior=0):
        """A connection of class cls connects, its call up for prior seconds already -> its Meter, or None
        when the class has no limits. The prior seconds are charged to the pool now."""
        if not any(self.limits(cls)):
            return None
        now = self.clock()
        prior = max(0, min(int(prior), PRIOR_MOST))
        m = Meter(cls, now - prior, now)
        self.add(cls, None, now - prior, now)
        self.meters.add(m)
        return m

    def charge(self, m, now=None):
        """Charge a meter's time since it was last charged."""
        now = self.clock() if now is None else now
        if now > m.charged:
            self.add(m.cls, m.who, m.charged, now)
        m.charged = max(m.charged, now)

    def sign(self, m, who):
        """The connection signs in as who (a name), or out (None): what came before is charged as it was."""
        who = who.lower() if who else None
        if who != m.who:
            self.charge(m)
            m.who = who

    def close(self, m):
        self.charge(m)
        self.meters.discard(m)

    def charge_all(self):
        now = self.clock()
        for m in list(self.meters):
            self.charge(m, now)

    def parts(self, m, now=None):
        """The time left for a meter by limit -> {"call": seconds, "user": seconds, "pool": seconds}, with only
        the limits that apply: the player's month only while one is signed in. Whole seconds, never below 0.
        Time not yet charged counts, this connection's and the others'."""
        now = self.clock() if now is None else now
        call, user, pool = self.limits(m.cls)
        month, since = month_of(now), month_start(now)
        pending = lambda x: now - max(x.charged, since)     # noqa: E731  (this month's, not yet charged)
        out = {}
        if call:
            out["call"] = call - (now - m.start)
        if user and m.who:
            out["user"] = user - self.used_secs(month, m.cls, m.who) - sum(
                pending(x) for x in self.meters if x.cls == m.cls and x.who == m.who)
        if pool:
            out["pool"] = pool - self.used_secs(month, m.cls) - sum(pending(x) for x in self.meters if x.cls == m.cls)
        return {k: max(0, int(v)) for k, v in out.items()}

    def left(self, m, now=None):
        """The time left for a meter -> (seconds, which): the smallest of parts(), and which of "call", "user"
        and "pool" it is. (None, None) when none of them has a limit."""
        out = self.parts(m, now)
        if not out:
            return None, None
        which = min(out, key=lambda k: (out[k], k))
        return out[which], which

    def rate(self, m, which):
        """How many seconds of which pass in a second: the connections that share it."""
        if which == "pool":
            return max(1, sum(1 for x in self.meters if x.cls == m.cls))
        if which == "user":
            return max(1, sum(1 for x in self.meters if x.cls == m.cls and x.who == m.who))
        return 1

    # ---- what the player is told ----------------------------------------------------------------------------
    def notice(self, m, which, secs):
        """The notice for so many seconds left: "This call ends in 5 minutes." """
        one, kind = WORDS[m.cls]
        when = mins_text(secs)
        if which == "call":
            return "This %s ends in %s." % (one, when)
        if which == "user":
            return "Your %stime this month ends in %s." % (kind, when)
        return "The parlor's %stime this month ends in %s." % (kind, when)

    def reason(self, m, which):
        """ERR LIMIT's text when which ran out: "closing: this call has had its 75 minutes"."""
        one, kind = WORDS[m.cls]
        call, user, pool = (n // 60 if n else 0 for n in self.limits(m.cls))
        if which == "call":
            return "closing: this %s has had its %d minutes" % (one, call)
        if which == "user":
            return "closing: you have used your %d %sminutes this month" % (user, kind)
        return "closing: the parlor has used its %d %sminutes this month" % (pool, kind)

    def used_up(self, m):
        """The lines for a player who signs in with their month used up."""
        one, kind = WORDS[m.cls]
        user = (self.limits(m.cls)[1] or 0) // 60
        return ["You have used your %d %sminutes this month." % (user, kind),
                "This %s closes in %g seconds." % (one, GRACE_SECS)]
