"""Push notifications for ParlorNet Play (docs/play-notifications.md): "your turn", "your game ended" and
"someone sat down at your table", delivered by the browser's push service (Web Push: RFC 8030, with VAPID,
RFC 8292, and message encryption, RFC 8291) even when no Play page is open.

How the extension learns of them: the core keeps notices (docs/protocol.md, Notices) for the players who
listed the kinds they want under the PRF key notify in this extension's namespace. One connection asks for
them with NFY and waits for more, as the turn polls do with WAT. Each notice names the player, the table,
the other player concerned and the game, and carries the token this extension wrote into notify when the
player subscribed; a subscription is used only while its token matches, so the subscriptions of a deleted
account never reach whoever takes the name next.

What is sent: the kind, the table number, the game's name and the other player's name, and for a join what
the one who sat down plays with, when the core says it (the NFX CLIENT line after the notice, as the CLIENT
event words it: "with GameMaster 1.0.0 on a Macintosh Plus"). Never a move, a position, a rack or a chat
line. A player whose Play page is open and in view (it asked something in the last few seconds) gets no
notification: the page tells them itself.

Where it is kept: the subscriptions (the push service's address and the browser's two keys, per account) in
push.db and the VAPID key in vapid.key, both in the extension's data directory (--data), owner only. The key
is made on the first start and never printed or logged. A subscription the push service answers 404 or 410
for is deleted; so is every subscription of an account when its owner turns notifications off everywhere.

Push is on only with a public https address (play.public_url), a contact (play.push_contact: the operator's
mailto: or https: address, which push services may use to reach the operator) and a data directory. The
messages go out on a few worker threads, each request with a short timeout, so a slow push service never
holds up the event loop or play.
"""
import asyncio
import concurrent.futures
import http.client
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import ssl
import sys
import threading
import time
from urllib.parse import urlsplit

from . import pushcrypto as C
from .common import CoreError

KINDS = ("turn", "end", "join")
DEFAULT_KINDS = ("turn", "end")
PREF = "notify"                 # the PRF key (parlord/core.py NOTICE_PREF)
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8}\Z")
# The push services of today's browsers (Chrome, Edge, Firefox, Safari, and the browsers built on them): a
# subscription's endpoint must be https on one of these, so the extension never posts to an address a player
# made up (a host on the operator's own network, say). --push-hosts adds others.
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "push.apple.com",
              "notify.windows.com")
CONTACT_RE = re.compile(r"^(?:mailto:[^\s@]+@[^\s@]+|https://\S+)\Z")
ENDPOINT_MAX = 1024
SUBS_PER_PLAYER = 10            # the newest kept
SUBS_MAX = 20000
SEND_SECS = 10                  # one request to a push service
WORKERS = 4
QUEUE_MAX = 500                 # messages waiting; past this, new ones are dropped (said in the log)
TTL = 86400                     # how long a push service keeps a message for a device that is off
IN_VIEW_SECS = 12               # a Play page that asked something this recently is in view: no notification
NFY_WAIT = 25
RETRY = 10
PAD = 32                        # payloads are padded to a multiple of this, so names' lengths do not show
JWT_SECS = 6 * 3600             # a VAPID JWT is made again after this (it is valid for 12 hours)


def contact(text):
    """play.push_contact -> the VAPID sub claim, or "" when it is not a mailto: or https: address."""
    text = (text or "").strip()
    return text if CONTACT_RE.match(text) and len(text) <= 200 else ""


def host_list(text):
    """--push-hosts: comma-separated host names (or host:port) added to PUSH_HOSTS."""
    return [h.strip().lower() for h in (text or "").split(",") if h.strip()]


def endpoint_ok(url, extra=()):
    """Whether a subscription's endpoint may be posted to: https on a known push service (a host in
    PUSH_HOSTS or below one, port 443), or a host:port listed in extra (--push-hosts), where a loopback
    address may also be plain http (a local push service, as the tests use)."""
    if not isinstance(url, str) or len(url) > ENDPOINT_MAX or any(c in url for c in " \r\n\t\0"):
        return False
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.username or u.password or u.fragment or not host or not u.path.startswith("/"):
        return False
    netloc = "%s:%d" % (host, port) if port else host
    if netloc in extra or host in extra:
        if u.scheme == "https":
            return True
        try:
            return u.scheme == "http" and ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False
    return (u.scheme == "https" and port in (None, 443)
            and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS))


def keys_ok(p256dh, auth):
    """The browser's keys (base64url): a P-256 point and a 16-byte secret."""
    try:
        C.point(C.unb64u(p256dh))
        return len(C.unb64u(auth)) == 16
    except (ValueError, TypeError):
        return False


def kinds_value(text):
    """A list of kinds from a form ("turn,end") -> the canonical text, "-" for none; None if it is not one."""
    got = [k for k in (text or "").replace(" ", "").split(",") if k]
    if any(k not in KINDS for k in got):
        return None
    return ",".join(k for k in KINDS if k in got) or "-"


def payload(kind, table, game, who, via=""):
    """The message: JSON the service worker turns into the notification's text (adapters/play/sw.js). via: what
    the one who sat down plays with (a join only), "v" when there is something to say."""
    msg = {"k": kind, "t": table, "g": game, "w": who}
    if via:
        msg["v"] = via
    return json.dumps(msg, separators=(",", ":")).encode()


NFX_CLIENT = "NFX CLIENT "


def with_clients(lines):
    """NFY's lines -> (NFY line, what the one who sat down plays with or "") for each notice: the NFX CLIENT line
    that follows a notice belongs to it; an NFX word this code does not know is skipped."""
    out = []
    for l in lines:
        if l.startswith("NFY "):
            out.append([l, ""])
        elif l.startswith(NFX_CLIENT) and out and not out[-1][1]:
            out[-1][1] = l[len(NFX_CLIENT):].strip()
    return [tuple(x) for x in out]


class Store:
    """The subscriptions, in SQLite (push.db in the data directory, owner only)."""

    def __init__(self, path):
        self.lock = threading.Lock()
        old = os.umask(0o077)
        try:
            self.db = sqlite3.connect(path, check_same_thread=False)
        finally:
            os.umask(old)
        self.db.execute("CREATE TABLE IF NOT EXISTS subs (endpoint TEXT PRIMARY KEY, name TEXT NOT NULL, "
                        "p256dh TEXT NOT NULL, auth TEXT NOT NULL, token TEXT NOT NULL, at REAL NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS subs_name ON subs (name)")
        self.db.commit()

    def add(self, endpoint, name, p256dh, auth, token):
        with self.lock:
            key = name.lower()
            self.db.execute("INSERT OR REPLACE INTO subs VALUES (?, ?, ?, ?, ?, ?)",
                            (endpoint, key, p256dh, auth, token, time.time()))
            old = [r[0] for r in self.db.execute("SELECT endpoint FROM subs WHERE name=? ORDER BY at DESC",
                                                 (key,))][SUBS_PER_PLAYER:]
            old += [r[0] for r in self.db.execute("SELECT endpoint FROM subs ORDER BY at DESC")][SUBS_MAX:]
            for e in old:
                self.db.execute("DELETE FROM subs WHERE endpoint=?", (e,))
            self.db.commit()

    def remove(self, endpoint, name=None):
        """Forget a subscription (only the named player's, when a name is given). How many remain for that
        player (when named)."""
        with self.lock:
            if name is None:
                self.db.execute("DELETE FROM subs WHERE endpoint=?", (endpoint,))
                self.db.commit()
                return None
            key = name.lower()
            self.db.execute("DELETE FROM subs WHERE endpoint=? AND name=?", (endpoint, key))
            self.db.commit()
            return self.db.execute("SELECT COUNT(*) FROM subs WHERE name=?", (key,)).fetchone()[0]

    def retoken(self, name, token):
        with self.lock:
            self.db.execute("UPDATE subs SET token=? WHERE name=?", (token, name.lower()))
            self.db.commit()

    def of(self, name, token):
        """The subscriptions of a player that carry this token: [(endpoint, p256dh, auth)]."""
        with self.lock:
            return list(self.db.execute("SELECT endpoint, p256dh, auth FROM subs WHERE name=? AND token=?",
                                        (name.lower(), token)))

    def count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM subs").fetchone()[0]


class Push:
    """The push side of a Play extension (play: the Play object, for its core connections and its bridge)."""

    def __init__(self, play, data_dir, sub, extra_hosts=()):
        self.play = play
        self.sub = contact(sub)
        self.extra = tuple(extra_hosts)
        self.on, self.why = False, ""
        self.key = None
        self.public = ""            # the applicationServerKey, base64url (the page's meta pn-push)
        self.store = None
        self.games = {}             # type/ver -> the game's name, from GAM
        self.jwts = {}              # push service origin -> (Authorization value, made at)
        self.queue = None
        self.pool = None
        self.sent = self.failed = self.dropped = 0
        if not play.url.startswith("https:"):
            self.why = "Play has no public https address (play.public_url)"
        elif not self.sub:
            self.why = "no contact is set (play.push_contact: a mailto: or https: address)"
        elif not data_dir:
            self.why = "the extension has no data directory (--data)"
        else:
            try:
                os.makedirs(data_dir, mode=0o700, exist_ok=True)
                self.key = C.load_or_make_key(os.path.join(data_dir, "vapid.key"))
                self.store = Store(os.path.join(data_dir, "push.db"))
            except (OSError, ValueError, sqlite3.Error) as e:
                self.why = "the data directory cannot be used (%s)" % type(e).__name__
            else:
                self.public = C.b64u(C.public_bytes(self.key))
                self.on = True

    def start(self):
        """Start the notice loop and the senders (on the running event loop)."""
        if not self.on:
            print("play: push notifications are off: %s" % self.why, flush=True)
            return
        print("play: push notifications are on (%d subscriptions)" % self.store.count(), flush=True)
        self.queue = asyncio.Queue(QUEUE_MAX)
        self.pool = concurrent.futures.ThreadPoolExecutor(WORKERS, thread_name_prefix="push")
        self.tasks = [asyncio.ensure_future(self.notices())] + [asyncio.ensure_future(self.sender())
                                                                 for _ in range(WORKERS)]

    # ---- the notices, from the core ----------------------------------------------------------------------
    async def notices(self):
        seq = None
        while True:
            core = None
            try:
                core = await self.play.connect()
                data, final = await core.req("GAM")
                for l in data:
                    f = l.split()
                    if len(f) > 3 and f[0] == "GAM":
                        self.games.setdefault(f[1], f[3].replace("_", " "))
                if seq is None:
                    _, final = await core.req("NFY")
                    if not final.startswith("OK "):
                        print("play: the core does not give notices (NFY); push notifications are off", flush=True)
                        return
                    seq = int(final.split()[1])
                while True:
                    data, final = await core.req("NFY %d %d" % (seq, NFY_WAIT))
                    if not final.startswith("OK "):
                        raise CoreError("NFY")
                    seq = int(final.split()[1])
                    for l, via in with_clients(data):
                        self.notice(l, via)
            except (OSError, CoreError, ssl.SSLError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                    ValueError, IndexError):
                pass
            finally:
                if core is not None:
                    core.close()
            await asyncio.sleep(RETRY)

    def notice(self, line, via=""):
        """NFY seq kind table name token who type/ver -> messages queued for that player's subscriptions. via: the
        NFX CLIENT text of a join."""
        f = line.split()
        if len(f) != 8 or f[0] != "NFY" or f[2] not in KINDS or not TOKEN_RE.match(f[5]):
            return
        _, _, kind, table, name, token, who, typ = f
        if self.play.bridge.in_use(name, IN_VIEW_SECS):
            return
        game = self.games.get(typ) or typ.split("/")[0].capitalize()
        body = payload(kind, table, game, "" if who == "-" else who, via if kind == "join" else "")
        for endpoint, p256dh, auth in self.store.of(name, token):
            try:
                self.queue.put_nowait((endpoint, p256dh, auth, body, "pn" + table))
            except asyncio.QueueFull:
                self.dropped += 1
                if self.dropped % 100 == 1:
                    print("play: push: too many messages waiting; %d dropped" % self.dropped, flush=True)

    # ---- sending --------------------------------------------------------------------------------------------
    async def sender(self):
        loop = asyncio.get_running_loop()
        while True:
            endpoint, p256dh, auth, body, topic = await self.queue.get()
            try:
                status = await loop.run_in_executor(self.pool, self.send, endpoint, p256dh, auth, body, topic)
            except Exception as e:          # the kind only: an endpoint is a capability, not for the log
                status = None
                print("play: push failed: %s" % type(e).__name__, file=sys.stderr, flush=True)
            if status in (404, 410):
                await loop.run_in_executor(self.pool, self.store.remove, endpoint)
            elif status is not None and 200 <= status < 300:
                self.sent += 1
            else:
                self.failed += 1

    def authorization(self, origin):
        hit = self.jwts.get(origin)
        if not hit or time.time() - hit[1] > JWT_SECS:
            hit = self.jwts[origin] = (C.vapid_header(self.key, origin, self.sub), time.time())
        return hit[0]

    def send(self, endpoint, p256dh, auth, body, topic):
        """One message to one subscription (a worker thread): the push service's HTTP status."""
        if not endpoint_ok(endpoint, self.extra):
            return 410
        data = C.webpush_encrypt(body + b" " * (-len(body) % PAD), C.unb64u(p256dh), C.unb64u(auth))
        u = urlsplit(endpoint)
        origin = "%s://%s" % (u.scheme, u.netloc)
        if u.scheme == "https":
            conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=SEND_SECS,
                                               context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=SEND_SECS)
        try:
            conn.request("POST", u.path + ("?" + u.query if u.query else ""), body=data, headers={
                "TTL": str(TTL), "Urgency": "normal", "Topic": topic, "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream", "Authorization": self.authorization(origin)})
            r = conn.getresponse()
            r.read(4096)
            return r.status
        finally:
            conn.close()

    # ---- the page's requests (POST /play/push) ---------------------------------------------------------------
    async def request(self, req, form):
        """op=on (endpoint, p256dh, auth, kinds): keep this browser's subscription for the signed-in player
        and set the kinds; op=kinds (kinds): change the kinds; op=off (endpoint): forget this browser's
        subscription, and when it was the player's last, the notify key too. The player is the one whose
        session the cookie holds (resumed on a connection of this extension's own). Answers JSON."""
        def one(k):
            return (form.get(k) or [""])[0]
        op = one("op")
        if not self.on:
            return 503, {"error": "Notifications are not offered on this server."}
        sid = req.get("sid")
        if not sid or not self.play.resume_allowed(req["src"], sid):
            return 403, {"error": "Sign in first."}
        kinds = kinds_value(one("kinds")) if op in ("on", "kinds") else "-"
        endpoint = one("endpoint")
        if op not in ("on", "off", "kinds") or kinds is None:
            return 400, {"error": "That request is not understood."}
        if op == "on" and not endpoint_ok(endpoint, self.extra):
            return 400, {"error": "This browser's push service is not one this server sends to."}
        if op == "on" and not keys_ok(one("p256dh"), one("auth")):
            return 400, {"error": "The subscription's keys are not valid."}
        core = await self.play.connect(req)
        try:
            _, final = await core.req("SES %s" % sid)
            if not final.startswith("OK "):
                self.play.note_failed_resume(req["src"], sid)
                return 403, {"error": "Sign in first."}
            name = final.split()[1]
            data, _ = await core.req("PRF GET %s" % PREF)
            words = data[0].split()[2:] if data else []
            token = words[1] if len(words) > 1 and TOKEN_RE.match(words[1]) else None
            if op == "off":
                left = self.store.remove(endpoint, name)
                if not left:
                    await core.req("PRF DEL %s" % PREF)
                return 200, {"ok": True, "kinds": "-" if not left else (words[0] if words else "-")}
            if token is None:
                token = secrets.token_urlsafe(6)
                self.store.retoken(name, token)
            if op == "on":
                if kinds == "-":
                    kinds = words[0] if words and words[0] != "-" else ",".join(DEFAULT_KINDS)
                self.store.add(endpoint, name, one("p256dh"), one("auth"), token)
            _, final = await core.req("PRF SET %s %s %s" % (PREF, kinds, token))
            if not final.startswith("OK"):
                return 409, {"error": "Your notification settings could not be kept."}
            return 200, {"ok": True, "kinds": kinds}
        finally:
            core.close()
