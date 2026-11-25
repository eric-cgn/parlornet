"""The fixes for the security review of the sign-in and session redesign
(docs/security-design.md, "Review findings and fixes"): one test class per
finding, numbered as in the review. The review's own reproductions are in
review/ (python3.13 review/r1_extension_exhaustion.py and so on)."""
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import common  # noqa: E402
from adapters.common import CoreClient, CoreError  # noqa: E402
from parlord import auth, core as pcore, frontends  # noqa: E402
from parlord.accounts import source_key  # noqa: E402
from parlord.core import Limits, Server, Session  # noqa: E402

SID = r"^OK [A-Za-z0-9_-]{22}$"
SECRET = "Correct horse 1"          # the password of the players made below (a test value, never printed)


def server(signin, signup=None, db=":memory:"):
    srv = Server(os.path.join(ROOT, "plugins"), "test", db, signin, None, signup)
    srv.settings["email.transport"] = "log"
    return srv


def public(source=None):
    return Session(None, source=source)


def frontend(name="web", fp="f", source=None):
    return Session(None, frontend=name, fingerprint=fp, source=source)


async def say(srv, conn, line):
    return (await srv.dispatch(conn, line))[-1]


async def password_player(srv, name, email):
    """A player with a password (SECRET) and an address on file (made directly, as the review's harness
    does)."""
    p = srv.accounts.player(name, create=True)
    p.cred, p.email, p.verified = auth.hash_password(SECRET), email, True
    srv.accounts.save_player(p)
    return p


def body(srv):
    return srv.auth.mailer.last["body"]


def mailed_password(srv):
    """The temporary password in the last mail (never shown by a failing assertion)."""
    return re.search(r"Password:\s+(\S+)", body(srv)).group(1)


def spawn(*args):
    p = subprocess.Popen([sys.executable, "-m"] + list(args), cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    p.stdout.readline()
    return p


def raw_http(port, data, timeout=10):
    import socket
    s = socket.create_connection(("127.0.0.1", port), timeout)
    s.sendall(data if isinstance(data, bytes) else data.encode("latin-1"))
    out = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        out += chunk
    s.close()
    return out.decode("latin-1")


def post(path, form, cookie=None):
    h = "POST %s HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/x-www-form-urlencoded\r\n" % path
    if cookie:
        h += "Cookie: %s\r\n" % cookie
    return h + "Content-Length: %d\r\n\r\n%s" % (len(form), form)


# ---- 1: pre-login connections cannot exhaust the core's extension channel --------------

class Writer:
    """A connection the gate may close."""

    def __init__(self):
        self.data, self.closed = b"", False
        w = self

        class T:
            def abort(self):
                w.closed = True
        self.transport = T()

    def write(self, b):
        self.data += b


class F01Gate(unittest.TestCase):
    def test_pending_per_address_and_in_all(self):
        g = common.Gate(max_signed=2, pending_max=5, per_addr=2)
        ws = [Writer() for _ in range(3)]
        a = [g.admit("192.0.2.1", w) for w in ws]
        self.assertTrue(all(t is not None for t in a))  # a third from one address is let in...
        self.assertTrue(ws[0].closed)                   # ...and that address's oldest goes
        self.assertIn(b"Server is full", ws[0].data)
        self.assertFalse(a[0].sign_in())                # a closed one cannot sign in
        a[1].touch()                                    # a[1] is active, so a[2] is the idler
        others = [g.admit("192.0.2.%d" % i, Writer()) for i in range(2, 6)]
        self.assertTrue(all(t is not None for t in others))         # nobody refused
        self.assertEqual(sum(len(v) for v in g.pending.values()), 5)  # five pending in all
        self.assertEqual(g.pending["192.0.2.1"], [a[1]])            # the busiest address lost its idlest
        self.assertTrue(a[1].sign_in())
        self.assertTrue(others[1].sign_in())
        self.assertFalse(others[2].sign_in())           # two signed in is the most
        self.assertIsNone(g.admit("192.0.2.9"))         # and then nobody new is let in
        for t in a + others:
            t.release()
        self.assertEqual((g.signed, g.pending), (0, {}))

    def test_r2_2_forty_addresses_with_one_each_do_not_evict_a_waiting_player(self):
        g = common.Gate(max_signed=40)                          # IOS and Outland: 40 pending places
        player = Writer()
        mine = g.admit("192.0.2.200", player)
        refused = 0
        for rnd in range(3):
            for i in range(40):
                w = Writer()
                t = g.admit("198.18.%d.%d" % (rnd, i), w)
                refused += t is None
        self.assertFalse(player.closed)
        self.assertEqual(mine.state, "pending")
        self.assertGreater(refused, 0)                          # the newcomers wait outside instead
        # behind one NAT: a second player there is let in by closing an address that holds more
        g2 = common.Gate(max_signed=20)
        a = [g2.admit("203.0.113.5", Writer()) for _ in range(4)]
        others = [g2.admit("198.18.9.%d" % i, Writer()) for i in range(16)]
        self.assertTrue(all(a) and all(others))
        t = g2.admit("192.0.2.7", Writer())
        self.assertIsNotNone(t)
        self.assertEqual(len(g2.pending["203.0.113.5"]), 3)    # the busiest address's newest went
        self.assertEqual(g2.pending["203.0.113.5"], a[:3])

    def test_ipv6_by_its_48_and_scaling(self):
        g = common.Gate(max_signed=60, per_addr=4)
        self.assertEqual(g.pending_max, 60)             # grows with the adapter's size
        self.assertEqual(common.Gate(10).pending_max, common.PENDING_MAX)
        ws = [Writer() for _ in range(5)]
        for i, w in enumerate(ws):
            g.admit("2001:db8:1:2::%x" % (i + 1), w)
        self.assertEqual(list(g.pending), ["2001:db8:1::/48"])
        self.assertEqual(len(g.pending["2001:db8:1::/48"]), 4)
        self.assertTrue(ws[0].closed)
        g.admit("::ffff:192.0.2.7", Writer())
        self.assertIn("192.0.2.7", g.pending)


class F01CoreShares(unittest.IsolatedAsyncioTestCase):
    """Each server extension has its own share of the extension channel."""

    async def test_one_extension_cannot_starve_the_others(self):
        keys = make_keys("ios", "web")
        srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "none")
        srv.keyring = frontends.Keyring(keys)
        await srv.load_plugins()
        listener = await asyncio.start_server(srv.handle_frontend, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        old = Limits.max_conns_per_frontend
        Limits.max_conns_per_frontend = 3
        held = []
        try:
            for _ in range(3):
                held.append(await CoreClient.connect("127.0.0.1", port, os.path.join(keys, "ios.key")))
            with self.assertRaises((CoreError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError)):
                c = await CoreClient.connect("127.0.0.1", port, os.path.join(keys, "ios.key"))
                held.append(c)
                await c.req("MOTD")             # the core closed it: its share is used up
            web = await CoreClient.connect("127.0.0.1", port, os.path.join(keys, "web.key"))
            self.assertEqual((await web.req("HELO wendy"))[1][:3], "OK ")   # another extension still gets in
            web.close()
        finally:
            Limits.max_conns_per_frontend = old
            for c in held:
                c.close()
            listener.close()
            for p in srv.plugins.values():
                p.stop()


class F01Adapters(unittest.TestCase):
    """The review's reproduction: 410 idle connects to IOS and FIBS, then the
    web and telnet still work; and connections that do send a login line are
    capped per client address."""

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe = free_port(), free_port()
        cls.ports = {n: free_port() for n in ("ios", "fibs", "web", "telnet")}
        cls.keys = make_keys(*cls.ports)
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "none")]
        for n, port in cls.ports.items():
            cls.procs.append(spawn(*adapter_args("adapters." + n, port, cls.fe, cls.keys, n)))

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
            p.stdout.close()

    def test_idle_and_login_floods(self):
        import resource
        import socket
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < 2048:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, hard), hard))
        socks = []
        try:
            for i in range(410):
                socks.append(socket.create_connection(("127.0.0.1", self.ports["ios" if i % 2 else "fibs"]), 5))
            time.sleep(1)
            r = raw_http(self.ports["web"], post("/signup.htm", "n=alice"))
            self.assertIn("302 Found", r.split("\r\n")[0])
            self.assertIn("/lobby.htm", r)                      # signed up and in (sign-in none)
            # connections that send a login line: four at a time from one address, then "full"
            logins = []
            for i in range(6):
                s = socket.create_connection(("127.0.0.1", self.ports["ios"]), 5)
                logins.append(s)
            time.sleep(0.5)
            refused = 0
            for s in logins:
                s.settimeout(3)
                try:
                    data = s.recv(4096)
                except OSError:
                    data = b""
                refused += b"Server is full" in data
            self.assertGreaterEqual(refused, 2)
        finally:
            for s in socks:
                s.close()
        t = socket.create_connection(("127.0.0.1", self.ports["telnet"]), 5)
        t.settimeout(5)
        data = b""
        end = time.time() + 10
        while b"Login (" not in data and time.time() < end:
            data += t.recv(4096)
        t.close()
        self.assertIn(b"Login (", data)


# ---- 2: SMTP with verified TLS -----------------------------------------------------------

def make_certs(d):
    """A CA and a certificate for localhost signed by it; and a self-signed one for another name."""
    def run(*a):
        subprocess.run(["openssl"] + list(a), check=True, capture_output=True, cwd=d)
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
        "-subj", "/CN=test CA", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=keyCertSign")
    run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", "/CN=localhost")
    with open(os.path.join(d, "ext.cnf"), "w") as f:
        f.write("subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\nsubjectKeyIdentifier=hash\n"
                "authorityKeyIdentifier=keyid,issuer\nkeyUsage=digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\n")
    run("x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out",
        "srv.pem", "-days", "2", "-extfile", "ext.cnf")
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "evil.key", "-out", "evil.pem", "-days", "2",
        "-subj", "/CN=attacker.invalid")
    return {k: os.path.join(d, k) for k in ("ca.pem", "srv.pem", "srv.key", "evil.pem", "evil.key")}


class FakeRelay:
    """An SMTP relay with STARTTLS on 127.0.0.1 (its own thread and loop)."""

    def __init__(self, cert, key, starttls=True):
        self.ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ctx.load_cert_chain(cert, key)
        self.starttls = starttls
        self.creds, self.mails = [], []
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.loop.set_exception_handler(lambda loop, ctx: None)    # refused handshakes are expected
            self.srv = self.loop.run_until_complete(asyncio.start_server(self.handle, "127.0.0.1", 0))
            self.port = self.srv.sockets[0].getsockname()[1]
            ready.set()
            self.loop.run_forever()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(5)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)

    async def handle(self, r, w):
        async def say(t):
            w.write((t + "\r\n").encode())
            await w.drain()
        try:
            await say("220 relay ESMTP")
            data = None
            while True:
                line = await r.readline()
                if not line:
                    return
                l = line.decode().rstrip("\r\n")
                u = l.upper()
                if data is not None:
                    if l == ".":
                        self.mails.append("\n".join(data))
                        data = None
                        await say("250 queued")
                    else:
                        data.append(l)
                elif u.startswith("EHLO"):
                    await say("250-relay\r\n%s250 AUTH PLAIN LOGIN" % ("250-STARTTLS\r\n" if self.starttls else ""))
                elif u == "STARTTLS":
                    await say("220 go ahead")
                    await w.start_tls(self.ctx)
                elif u.startswith("AUTH PLAIN"):
                    self.creds.append(base64.b64decode(l.split()[2]).split(b"\0"))
                    await say("235 ok")
                elif u.startswith("DATA"):
                    data = []
                    await say("354 go")
                elif u.startswith("QUIT"):
                    await say("221 bye")
                    return
                else:
                    await say("250 ok")
        except (OSError, ssl.SSLError):
            return


@unittest.skipUnless(shutil.which("openssl"), "needs the openssl command to make test certificates")
class F02SmtpTls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="pnsmtp")
        cls.certs = make_certs(cls.dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, True)

    def settings(self, relay, **kw):
        s = {"email.transport": "smtp", "email.smtp_host": "localhost", "email.smtp_port": str(relay.port),
             "email.smtp_user": "u", "email.smtp_password": "relay-password", "email.from": "noreply@example.org"}
        s.update(kw)
        return s

    def test_an_impostor_relay_gets_nothing(self):
        relay = FakeRelay(self.certs["evil.pem"], self.certs["evil.key"])
        try:
            m = auth.Mailer(self.settings(relay))
            self.assertFalse(m.send("alice@example.org", "Your new ParlorNet password", "Password: abcd"))
            self.assertIn("CERTIFICATE_VERIFY_FAILED", m.error.upper().replace(" ", "_"))
            self.assertEqual((relay.creds, relay.mails), ([], []))
        finally:
            relay.close()

    def test_no_starttls_no_mail(self):
        relay = FakeRelay(self.certs["srv.pem"], self.certs["srv.key"], starttls=False)
        try:
            m = auth.Mailer(self.settings(relay, **{"email.smtp_ca_file": self.certs["ca.pem"]}))
            self.assertFalse(m.send("alice@example.org", "s", "b"))
            self.assertEqual(relay.creds, [])
        finally:
            relay.close()

    def test_a_verified_relay_gets_the_mail_and_the_name_is_checked(self):
        relay = FakeRelay(self.certs["srv.pem"], self.certs["srv.key"])
        try:
            m = auth.Mailer(self.settings(relay, **{"email.smtp_ca_file": self.certs["ca.pem"]}))
            self.assertTrue(m.send("alice@example.org", "s", "hello"), m.error)
            self.assertEqual(relay.creds, [[b"", b"u", b"relay-password"]])
            self.assertEqual(len(relay.mails), 1)
            # the same relay reached by another name: the host name is the TLS server name, so it fails
            m = auth.Mailer(self.settings(relay, **{"email.smtp_ca_file": self.certs["ca.pem"],
                                                    "email.smtp_host": "127.0.0.1"}))
            self.assertFalse(m.send("alice@example.org", "s", "hello"))
            self.assertEqual(len(relay.creds), 1)
        finally:
            relay.close()

    def test_the_takeover_by_reset_fails_closed(self):
        """The review's chain: a reset for a player through an impostor relay. Nothing goes, and no
        temporary password is set."""
        relay = FakeRelay(self.certs["evil.pem"], self.certs["evil.key"])
        try:
            srv = server("password", "email")
            srv.settings.update(self.settings(relay))

            async def go():
                await password_player(srv, "alice", "alice@example.org")
                s = public()
                await say(srv, s, "HELO alice")
                return await say(srv, s, "AUT password reset")
            self.assertEqual(asyncio.run(go()), "ERR NOMAIL the email could not be sent; try again later")
            self.assertEqual((relay.creds, relay.mails), ([], []))
            self.assertEqual(srv.players["alice"].temp, "")
            self.assertTrue(auth.check_password(SECRET, srv.players["alice"].cred), "the password is as it was")
        finally:
            relay.close()

    def test_context(self):
        ctx = auth.tls_context({})
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)


# ---- 3 and 4: every code counts; decay; recovery while locked ------------------------------

class F03EveryCodeCounts(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_answers_after_a_reset_count(self):
        """After a reset, a wrong answer counts toward the name's lockout like any other; while the name is
        locked the password is not checked, but the mailed temporary password still is."""
        srv = server("password", "email")
        p = await password_player(srv, "dave", "dave@example.org")
        s = public()
        await say(srv, s, "HELO dave")
        self.assertEqual(await say(srv, s, "AUT password reset"), "ASK password Password secret")
        self.assertTrue(re.search(r"Password:\s+[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}\n", body(srv)), "the form")
        pw = mailed_password(srv)
        self.assertEqual(await say(srv, s, "AUT password abcd-efgh-jkmn"),
                         "ERR LOCKED wait=60 Wrong password; try again in 60 seconds")
        self.assertEqual(p.fails, 1)
        s = public()
        await say(srv, s, "HELO dave")
        self.assertTrue((await say(srv, s, "AUT password " + SECRET)).startswith("ERR LOCKED"))  # not checked
        self.assertEqual(p.fails, 1)
        s = public()
        await say(srv, s, "HELO dave")
        self.assertEqual(await say(srv, s, "AUT password " + pw), "ASK password New_password secret new")
        self.assertEqual((p.fails, p.locked_until), (0, 0.0))

    async def test_a_strangers_reset_takes_nothing_from_the_owner(self):
        """A reset mails a temporary password to the address on file only; the old password keeps working
        until the temporary one is used, and using it moves the mail to the owners' own budget."""
        srv = server("password", "email")
        p = await password_player(srv, "alice", "alice@example.org")
        s = public(source="203.0.113.7")
        await say(srv, s, "HELO alice")
        self.assertEqual(await say(srv, s, "AUT password reset"), "ASK password Password secret")
        self.assertEqual(srv.auth.mailer.last["to"], "alice@example.org")
        pw = mailed_password(srv)
        self.assertEqual(srv.store.email_count(0, name="alice", kind="recovery")[0], 1)    # a stranger's
        o = public()
        await say(srv, o, "HELO alice")
        self.assertEqual(await say(srv, o, "AUT password " + SECRET), "OK -")              # still hers
        o = public()
        await say(srv, o, "HELO alice")
        self.assertEqual(await say(srv, o, "AUT password " + pw), "ASK password New_password secret new")
        self.assertEqual(srv.store.email_count(0, name="alice", kind="recovery-own")[0], 1)
        self.assertEqual(srv.store.email_count(0, name="alice", kind="recovery")[0], 0)
        await say(srv, o, "AUT password Alice's new 2")
        self.assertEqual(await say(srv, o, "AUT confirm Alice's new 2"), "OK -")
        self.assertEqual(p.temp, "")
        self.assertFalse(auth.check_password(SECRET, p.cred), "the old password ended")

    def test_the_math(self):
        """What the limits allow an attacker with no mailbox (docs/security-design.md)."""
        # a temporary password: 12 characters from 31, about 59 bits
        space = len(auth.TEMP_ALPHABET) ** 12
        self.assertGreater(space, 2 ** 59)
        # it is checked while the name is locked too, but only as often as one client address may ask for
        # password checks (CHECKS_PER_SOURCE), for the day it works: far below one in a million million
        n, secs = auth.CHECKS_PER_SOURCE
        self.assertLess(n * (auth.TEMP_SECS / secs) / space, 1e-12)
        # a password: once locked, at most 6 guesses a day for a name, 48 untrusted ones for the server
        self.assertLessEqual(86400 / auth.LOCK_MAX, 6)
        self.assertEqual(auth.GLOBAL_MISSES, (48, 86400))


class F04LockoutDecayAndRecovery(unittest.IsolatedAsyncioTestCase):
    def test_decay_and_cap(self):
        self.assertEqual(auth.lock_secs(30), auth.LOCK_MAX)
        self.assertEqual(auth.LOCK_MAX, 4 * 3600)
        now = 1_000_000.0
        self.assertEqual(auth.decay(8, now - auth.DECAY_SECS + 1, now), (8, now - auth.DECAY_SECS + 1))
        self.assertEqual(auth.decay(8, now - auth.DECAY_SECS, now)[0], 4)
        self.assertEqual(auth.decay(8, now - 3 * auth.DECAY_SECS - 5, now)[0], 1)

    async def test_a_locked_name_can_still_recover_by_mail(self):
        srv = server("password", "email")
        p = await password_player(srv, "carol", "carol@example.org")
        p.fails, p.missed_at, p.locked_until = 6, time.time(), time.time() + 3600
        self.assertTrue((await say(srv, public(), "HELO carol whatever")).startswith("ERR LOCKED"))
        s = public()
        out = await srv.dispatch(s, "HELO carol")
        self.assertEqual(out[-1], "ASK password Password secret cred")
        self.assertIn("Answer reset", " ".join(out))
        self.assertTrue((await say(srv, s, "AUT password " + SECRET)).startswith("ERR LOCKED"))   # not checked
        self.assertEqual(p.fails, 6)
        s = public()
        await say(srv, s, "HELO carol")
        self.assertEqual(await say(srv, s, "AUT password reset"), "ASK password Password secret")
        self.assertEqual(await say(srv, s, "AUT password " + mailed_password(srv)),
                         "ASK password New_password secret new")
        await say(srv, s, "AUT password Carol's new 3")
        self.assertEqual(await say(srv, s, "AUT confirm Carol's new 3"), "OK -")
        self.assertEqual((p.fails, p.locked_until), (0, 0.0))

    async def test_the_count_decays(self):
        srv = server("password")
        p = srv.accounts.player("bo", create=True)
        p.cred = auth.hash_password("bo secret 1")
        p.fails, p.missed_at, p.locked_until = 7, time.time() - 2 * auth.DECAY_SECS - 1, 0.0
        s = public()
        await say(srv, s, "HELO bo")
        r = await say(srv, s, "AUT password wrong one")
        self.assertEqual(p.fails, 7 // 4 + 1)                              # halved twice, then one more
        self.assertIn("wait=%d " % auth.lock_secs(p.fails), r)

    async def test_without_an_address_the_lock_stands(self):
        srv = server("password")
        p = srv.accounts.player("ned", create=True)
        p.cred, p.locked_until = auth.hash_password("ned secret 1"), time.time() + 60
        self.assertTrue((await say(srv, public(), "HELO ned")).startswith("ERR LOCKED"))


# ---- 5: sign-up never tells who owns an address ---------------------------------------------

class F05NoAddressOwner(unittest.IsolatedAsyncioTestCase):
    async def run_signup(self, srv, name, addr):
        s = public()
        await say(srv, s, "HELO %s" % name)
        await say(srv, s, "AUT confirm y")
        out = await srv.dispatch(s, "AUT email %s" % addr)
        return s, [l.replace(addr, "ADDR").replace(name, "NAME") for l in out]

    async def test_same_answer_and_a_notice(self):
        srv = server("password", "email")
        await password_player(srv, "alice", "alice@example.org")
        _, taken = await self.run_signup(srv, "probe1", "alice@example.org")
        mail = srv.auth.mailer.last
        self.assertEqual(mail["to"], "alice@example.org")
        self.assertTrue("already has a\nname there: alice" in mail["body"], "the notice")
        self.assertTrue("probe1" not in mail["body"] and "Password:" not in mail["body"],
                        "nothing the requester typed, and no password")
        s, free = await self.run_signup(srv, "probe2", "free@example.org")
        self.assertEqual(taken, free)
        self.assertFalse(any("alice" in l for l in taken))
        # both are pending accounts of the same shape; only the one whose mail went has the address
        a, b = srv.players["probe1"], srv.players["probe2"]
        self.assertTrue(a.pending and b.pending and a.temp and b.temp)
        self.assertEqual((a.email, b.email), (None, "free@example.org"))
        self.assertIs(srv.accounts.player_by_email("alice@example.org"), srv.players["alice"])

    async def test_nothing_signs_in_to_a_sign_up_with_an_address_in_use(self):
        srv = server("password", "email")
        await password_player(srv, "alice", "alice@example.org")
        await self.run_signup(srv, "probe1", "alice@example.org")
        for guess in (SECRET, "abcd-efgh-jkmn", "reset"):
            srv.players["probe1"].locked_until = 0
            c = public()
            await say(srv, c, "USER probe1")
            r = await say(srv, c, "AUT password " + guess)
            self.assertTrue(r.startswith("ERR LOCKED") and "Wrong password" in r, r)
        self.assertTrue(srv.players["probe1"].pending)
        srv.accounts.expire(time.time() + auth.PENDING_SECS + 1)
        self.assertIsNone(srv.accounts.player("probe1"))                 # and it goes in a day
        self.assertIsNotNone(srv.accounts.player("alice"))


# ---- 6: the web's sign-in dialogs -----------------------------------------------------------

class F06WebSignins(unittest.TestCase):
    def web(self):
        from adapters import web
        keys = make_keys("web")
        return web, web.Web("127.0.0.1", 1, os.path.join(keys, "web.key"))

    def test_per_address_and_oldest_out(self):
        web, w = self.web()

        class Core:
            closed = False

            def close(self):
                self.closed = True
        made = []
        for i in range(web.SIGNINS_PER_ADDR + 2):
            c = Core()
            made.append(c)
            w.make_room("203.0.113.1")
            w.signins["l%d" % i] = web.Dialogue(c, "n", time.time() + 300 + i, None, "203.0.113.1")
        self.assertEqual(len(w.signins), web.SIGNINS_PER_ADDR)
        self.assertTrue(made[0].closed and made[1].closed and not made[-1].closed)
        # full: a dialog goes only at an address holding more than the newcomer's would
        for i in range(web.MAX_SIGNINS - len(w.signins)):
            self.assertTrue(w.make_room("198.51.100.%d" % i))
            w.signins["m%d" % i] = web.Dialogue(Core(), "n", time.time() + 400 + i, None, "198.51.100.%d" % i)
        self.assertEqual(len(w.signins), web.MAX_SIGNINS)
        self.assertTrue(w.make_room("192.0.2.9"))          # 203.0.113.1 holds 4: its newest goes
        self.assertEqual(len(w.signins), web.MAX_SIGNINS - 1)
        self.assertEqual(sum(d.addr == "203.0.113.1" for d in w.signins.values()), web.SIGNINS_PER_ADDR - 1)
        self.assertEqual(web.SIGNIN_IDLE_SECS, 300)


class WebStack(unittest.TestCase):
    auth = "password"
    signup = None

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("web")
        extra = ["--signup", cls.signup] if cls.signup else []
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", cls.auth, *extra)]
        cls.procs.append(spawn(*adapter_args("adapters.web", cls.port, cls.fe, cls.keys, "web")))

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
            p.stdout.close()


class F06WebFlood(WebStack):
    def test_a_hundred_posts_do_not_block_sign_in(self):
        p = raw_http(self.port, post("/signup.htm", "n=amy"))          # amy signs up before the flood
        lid = re.search(r'name="lid" value="([^"]+)"', p).group(1)
        p = raw_http(self.port, post("/login.htm", "lid=%s&f=password&v=amy-secret-1" % lid))
        lid = re.search(r'name="lid" value="([^"]+)"', p).group(1)
        p = raw_http(self.port, post("/login.htm", "lid=%s&f=confirm&v=amy-secret-1" % lid))
        self.assertIn("302 Found", p.split("\r\n")[0])
        first = raw_http(self.port, post("/signup.htm", "n=x00000"))
        lid = re.search(r'name="lid" value="([^"]+)"', first).group(1)
        for i in range(1, 100):
            r = raw_http(self.port, post("/signup.htm", "n=x%05d" % i))
            self.assertIn("200 OK", r.split("\r\n")[0])
        r = raw_http(self.port, post("/login.htm", "n=amy"))
        self.assertIn("200 OK", r.split("\r\n")[0])
        self.assertIn("Password:", r)                       # amy's sign-in goes on
        self.assertNotIn("Busy", r)
        r = raw_http(self.port, post("/signup.htm", "n=x77777"))
        self.assertIn("Too many sign-ups from your address", r)     # the core's per-address sign-up limit
        r = raw_http(self.port, post("/login.htm", "lid=%s&f=password&v=whatever1" % lid))
        self.assertIn("That sign-in has expired", r)        # one address keeps at most four


# ---- 7: email availability -------------------------------------------------------------------

ASKED = "ASK password Password_from_the_email secret"     # the step after the address, whatever happened


class F07EmailAvailability(unittest.IsolatedAsyncioTestCase):
    async def signup(self, srv, name, addr, source):
        s = frontend(source=source)
        await say(srv, s, "HELO %s" % name)
        await say(srv, s, "AUT confirm y")
        return await say(srv, s, "AUT email %s" % addr)

    async def test_per_source_caps(self):
        srv = server("password", "email")
        for i in range(4):
            self.assertEqual(await self.signup(srv, "a%d" % i, "a%d@example.org" % i, "203.0.113.5"), ASKED)
            srv.store.db.execute("UPDATE email_log SET at = at - 61")
        r = await self.signup(srv, "a9", "a9@example.org", "203.0.113.5")
        self.assertTrue(r.startswith("ERR BUSY wait=3600 Too many emails asked from your address"), r)
        self.assertEqual(await self.signup(srv, "b1", "b1@example.org", "198.51.100.7"), ASKED)

    async def test_recovery_has_its_own_budget(self):
        srv = server("password", "email")
        srv.settings["email.limit.per_hour"] = "3"
        srv.settings["email.limit.per_source_hour"] = "50"
        await password_player(srv, "alice", "alice@example.org")
        for i in range(3):
            await self.signup(srv, "f%d" % i, "f%d@example.net" % i, "203.0.113.%d" % i)
            srv.store.db.execute("UPDATE email_log SET at = at - 61")
        self.assertTrue((await self.signup(srv, "f9", "f9@example.net", "203.0.113.99")).startswith("ERR BUSY"))
        s = public()
        await say(srv, s, "HELO alice")
        self.assertEqual(await say(srv, s, "AUT password reset"), "ASK password Password secret")

    async def test_bounces_from_one_source_hold_only_that_source(self):
        srv = server("password", "email")
        srv.settings["email.limit.per_source_hour"] = "20"
        await password_player(srv, "alice", "alice@example.org")
        for i in range(4):
            await self.signup(srv, "bnc%d" % i, "nobody%d@bounce.example" % i, "203.0.113.66")
            srv.store.db.execute("UPDATE email_log SET at = at - 61")
        for i in range(4):
            srv.store.block_email("nobody%d@bounce.example" % i, "listed in the blocked file")
        self.assertFalse(srv.new_signups_held())
        r = await self.signup(srv, "bnc9", "nobody9@bounce.example", "203.0.113.66")
        self.assertTrue(r.startswith("ERR BUSY wait=86400 Mail asked from your address bounced"), r)
        self.assertEqual(await self.signup(srv, "newbie", "newbie@example.org", "198.51.100.1"), ASKED)
        # bounces asked from four different addresses do hold new sign-ups; recovery still goes
        for i in range(4):
            await self.signup(srv, "m%d" % i, "m%d@bounce.example" % i, "192.0.2.%d" % i)
            srv.store.db.execute("UPDATE email_log SET at = at - 61")
            srv.store.block_email("m%d@bounce.example" % i, "refused by the mail server")
        self.assertTrue(srv.new_signups_held())
        self.assertEqual(await self.signup(srv, "late", "late@example.org", "198.51.100.2"),
                         "ERR PAUSED new email sign-ups are paused; try again later")
        s = public()
        await say(srv, s, "HELO alice")
        self.assertEqual(await say(srv, s, "AUT password reset"), "ASK password Password secret")

    async def test_sign_up_notices_do_not_use_up_recovery(self):
        srv = server("password", "email")
        await password_player(srv, "alice", "alice@example.org")
        for i in range(3):
            await self.signup(srv, "p%d" % i, "alice@example.org", "203.0.113.%d" % i)
            srv.store.db.execute("UPDATE email_log SET at = at - 61")
        s = public()
        await say(srv, s, "HELO alice")
        self.assertEqual(await say(srv, s, "AUT password reset"), "ASK password Password secret")

    async def test_src_only_on_the_extension_channel(self):
        srv = server("none")
        self.assertTrue((await say(srv, public(), "SRC 192.0.2.1")).startswith("ERR NOTFRONTEND"))
        f = frontend()
        self.assertEqual(await say(srv, f, "SRC 2001:db8::1"), "OK")
        self.assertEqual(f.source, "2001:db8::/48")
        self.assertTrue((await say(srv, frontend(), "SRC evil\\nSES")).startswith("ERR SYNTAX"))
        self.assertEqual(source_key("::ffff:192.0.2.1"), "192.0.2.1")


# ---- 8: containment ----------------------------------------------------------------------------

def load_start(env):
    """deploy/start.py as a module, with its environment (PARLOR_DB and the rest) set."""
    import importlib.util
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location("pnstart_%d" % id(env), os.path.join(ROOT, "deploy", "start.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class F08Containment(unittest.TestCase):
    def test_keys_go_to_their_extensions_group_in_the_per_user_layout_only(self):
        d = tempfile.mkdtemp(prefix="pnkeys")
        try:
            gid = os.getgid()
            with mock.patch.object(frontends, "key_group", lambda name: gid if name == "web" else None):
                one = frontends.write_key(d, "web", "frontend")         # key directory 700: the one-user layout
                self.assertEqual(os.stat(one).st_mode & 0o777, 0o600)
                os.chmod(d, 0o711)                                      # the per-user layout
                frontends.main(["--keys", d, "rotate", "web"])
                admin = frontends.write_key(d, "admin", "admin")
            web = os.path.join(d, "web.key")
            self.assertEqual(os.stat(web).st_mode & 0o777, 0o640)
            self.assertEqual(os.stat(web).st_gid, gid)
            self.assertEqual(os.stat(admin).st_mode & 0o777, 0o600)     # no pn-admin group here: owner only
        finally:
            shutil.rmtree(d, True)

    def test_default_deploy_adds_no_capabilities(self):
        with open(os.path.join(ROOT, "Dockerfile")) as f:
            docker = f.read()
        self.assertIn("\nUSER parlord\n", docker)                       # unprivileged by default
        expose = [l for l in docker.splitlines() if l.startswith("EXPOSE")][0]
        self.assertNotIn("6510", expose)
        for script in ("install.sh", "install-split.sh"):
            with open(os.path.join(ROOT, "deploy", script)) as f:
                text = f.read()
            self.assertNotIn("--cap-add", text, script)
            self.assertNotIn("--network host", text, script)
            self.assertIn("--cap-drop ALL", text, script)
            self.assertIn("no-new-privileges", text, script)
        with open(os.path.join(ROOT, "deploy", "install.sh")) as f:
            install = f.read()
        self.assertIn("--user 10042:10042", install)
        self.assertIn("-p 127.0.0.1:6510:6510", install)

    def test_capabilities_decide_the_mode(self):
        st = load_start({})
        d = tempfile.mkdtemp(prefix="pnstatus")
        try:
            path = os.path.join(d, "status")
            with open(path, "w") as f:
                f.write("Name:\tpython\nCapEff:\t0000000000000000\n")
            self.assertEqual(st.capabilities(path), 0)
        finally:
            shutil.rmtree(d, True)
        with mock.patch.object(st.os, "geteuid", lambda: 0):
            self.assertFalse(st.can_switch_users(0))                          # --cap-drop ALL
            self.assertFalse(st.can_switch_users(1 << st.CAP_SETUID))
            self.assertTrue(st.can_switch_users((1 << st.CAP_SETUID) | (1 << st.CAP_SETGID)))
        with mock.patch.object(st.os, "geteuid", lambda: 10042):
            self.assertFalse(st.can_switch_users((1 << 40) - 1))              # not root: never

    def test_start_runs_without_capabilities(self):
        """No capability (and not root): one user for everything, one clear log
        line, tight modes, every adapter started."""
        d = tempfile.mkdtemp(prefix="pndata")
        try:
            db = os.path.join(d, "parlord.db")
            env = {"PARLOR_DB": db, "PARLOR_ADAPTERS": "web,telnet", "PARLOR_SETTINGS": os.path.join(d, "none.conf")}
            st = load_start(env)
            started = []
            out = []
            with mock.patch.dict(os.environ, env), mock.patch.object(st, "capabilities", lambda *a: 0), \
                    mock.patch.object(st, "start", lambda name, args: started.append((name, args))), \
                    mock.patch.object(st.time, "sleep", mock.Mock(side_effect=KeyboardInterrupt)), \
                    mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))), \
                    mock.patch.object(st.signal, "signal"), mock.patch.object(st.sys, "argv", ["start.py"]):
                with self.assertRaises(KeyboardInterrupt):
                    st.main()
            self.assertIn("start: per-extension users are off: every process runs as uid %d" % os.getuid(), out)
            self.assertEqual([n for n, _ in started], ["web", "telnet", "parlord"])
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(os.path.join(d, "frontends")).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(os.path.join(d, "frontends", "web.key")).st_mode & 0o777, 0o600)
            r = subprocess.run([sys.executable, os.path.join(ROOT, "deploy", "start.py"), "--prepare"],
                               env=dict(os.environ, **env), capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]), {"names": ["web", "telnet"], "settings": None,
                                                                          "web_hosts": "", "play_url": "",
                                                                          "play_proxies": "", "play_push_contact": "",
                                                                          "play_dir": os.path.join(d, "play"),
                                                                          "idle": {}})
            self.assertEqual(os.stat(os.path.join(d, "play")).st_mode & 0o777, 0o700)     # Play's own files
        finally:
            shutil.rmtree(d, True)

    def test_root_without_capabilities_refuses_to_run_everything_as_root(self):
        st = load_start({})
        with mock.patch.object(st.os, "geteuid", lambda: 0), mock.patch.object(st, "capabilities", lambda *a: 0), \
                mock.patch.object(st.signal, "signal"), mock.patch.object(st.sys, "argv", ["start.py"]):
            with self.assertRaises(SystemExit) as e:
                st.main()
        self.assertIn("--user 10042", str(e.exception))


# ---- 9: no control characters reach the core ----------------------------------------------------

class F09NoInjection(unittest.IsolatedAsyncioTestCase):
    async def test_requests_with_control_characters_are_refused(self):
        class W:
            data = b""

            def write(self, b):
                self.data += b

            async def drain(self):
                pass
        c = CoreClient(None, W())
        for bad in ("AUT password longpassword1\nSES " + "A" * 22, "AUT v a\rb", "AUT v a\x00", "AUT v \x7f"):
            with self.assertRaises(CoreError):
                await c.req(bad)
        self.assertEqual(c.w.data, b"")
        self.assertIsNone(pcore.NAME_RE.match("bob\n"))
        from adapters import web, ios, telnet
        for rx in (web.NAME_RE, web.SID_RE, ios.NAME_RE, telnet.NAME_RE):
            self.assertIsNone(rx.match("bob\n"), rx.pattern)


class F09Web(WebStack):
    def test_a_newline_in_an_answer_is_refused(self):
        from urllib.parse import quote
        p = raw_http(self.port, post("/signup.htm", "n=mallory"))
        lid = re.search(r'name="lid" value="([^"]+)"', p).group(1)
        p = raw_http(self.port, post("/login.htm", "lid=%s&f=password&v=%s"
                                     % (lid, quote("longpassword1\nSES AAAAAAAAAAAAAAAAAAAAAA"))))
        self.assertIn("That has characters that cannot be used", p)

    def test_host_header(self):
        r = raw_http(self.port, "GET / HTTP/1.0\r\nHost: evil.example\nSet-Cookie: pn=AAAAAAAAAAAAAAAAAAAAAA\r\n\r\n")
        self.assertTrue(r.startswith("HTTP/1.0 400 "), r[:60])
        self.assertNotIn("Set-Cookie: pn=AAAA", r)
        r = raw_http(self.port, "GET / HTTP/1.0\r\nHost: <script>\r\n\r\n")
        self.assertIn("Location: http://parlornet.org/welcome.htm", r)
        r = raw_http(self.port, "GET / HTTP/1.0\r\nHost: play.example:6500\r\n\r\n")
        self.assertIn("Location: http://play.example:6500/welcome.htm", r)


# ---- 10: a rotated or removed key ends the sessions issued under it --------------------------------

class F10KeyRotation(unittest.IsolatedAsyncioTestCase):
    async def test_rotate_and_remove(self):
        keys = make_keys("web", "telnet")
        srv = server("none")
        srv.keyring = frontends.Keyring(keys)
        srv.keyring.load()
        fp = srv.keyring.fingerprint("web")
        sid = (await say(srv, frontend("web", fp), "HELO ann")).split()[1]
        tsid = (await say(srv, frontend("telnet", srv.keyring.fingerprint("telnet")), "HELO tim")).split()[1]
        self.assertEqual(await say(srv, frontend("web", fp), "SES " + sid), "OK ann")
        time.sleep(0.01)
        frontends.main(["--keys", keys, "rotate", "web"])
        new = frontends.Keyring(keys)
        new.load()
        # before any sweep: a connection made with the new key cannot resume it
        self.assertTrue((await say(srv, frontend("web", new.fingerprint("web")), "SES " + sid))
                        .startswith("ERR NOSESSION"))
        sid = (await say(srv, frontend("web", fp), "HELO ann")).split()[1]
        await srv.sweep()                                  # the sweep ends it in the database too
        self.assertEqual(srv.store.db.execute("SELECT COUNT(*) FROM sessions WHERE frontend='web'").fetchone()[0], 0)
        self.assertEqual(await say(srv, frontend("telnet", srv.keyring.fingerprint("telnet")), "SES " + tsid),
                         "OK tim")
        frontends.main(["--keys", keys, "remove", "telnet"])
        await srv.sweep()
        self.assertEqual(srv.store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)


# ---- 11: the web's resume limit and a shared address ---------------------------------------------

class F11SharedAddress(unittest.TestCase):
    def test_a_session_that_resumed_before_still_does(self):
        from adapters import web
        w = web.Web("127.0.0.1", 1, os.path.join(make_keys("web"), "web.key"))
        good = "G" * 22
        w.good[hashlib.sha256(good.encode()).hexdigest()] = time.time()
        w.ses_fails["203.0.113.1"] = [time.time()] * 10
        self.assertFalse(w.resume_allowed("203.0.113.1", "J" * 22))
        self.assertTrue(w.resume_allowed("203.0.113.1", good))
        self.assertTrue(w.resume_allowed("203.0.113.2", "J" * 22))


class F11Web(WebStack):
    auth = "none"

    def test_junk_from_a_neighbour_keeps_the_victims_cookie(self):
        r = raw_http(self.port, post("/signup.htm", "n=victim"))
        sid = re.search(r"Set-Cookie: pn=([A-Za-z0-9_-]{22})", r).group(1)
        get = "GET /lobby.htm HTTP/1.0\r\nHost: 127.0.0.1\r\nCookie: pn=%s\r\n\r\n"
        self.assertIn("You are <b>victim</b>", raw_http(self.port, get % sid))
        for i in range(10):
            raw_http(self.port, get % ("A" * 21 + chr(ord("A") + i)))
        r = raw_http(self.port, get % sid)
        self.assertIn("You are <b>victim</b>", r)
        self.assertNotIn("Max-Age=0", r)
        r = raw_http(self.port, get % ("B" * 22))           # a new junk id is not even tried, nor cleared
        self.assertNotIn("Max-Age=0", r)


# ---- 12: a deleted player's seats, games and tables ----------------------------------------------

class F12DeletedName(unittest.IsolatedAsyncioTestCase):
    async def test_nothing_passes_to_the_next_owner_of_the_name(self):
        srv = server("none")
        await srv.load_plugins()
        try:
            a, b = public(), public()
            await say(srv, a, "HELO alice")
            await say(srv, b, "HELO bob")
            priv = (await say(srv, a, "NEW reversi/1 standard vis=private")).split()[1]
            await say(srv, a, "JON %s" % priv)
            await say(srv, a, "INV %s bob" % priv)
            await say(srv, b, "JON %s" % priv)                 # playing, private
            pub = (await say(srv, a, "NEW reversi/1 standard")).split()[1]
            await say(srv, a, "JON %s" % pub)                  # open, public, alice seated
            pub2 = (await say(srv, b, "NEW reversi/1 standard")).split()[1]
            await say(srv, b, "JON %s" % pub2)
            await say(srv, b, "INV %s alice" % pub2)
            await srv.delete_player(srv.players["alice"])
            self.assertNotIn(priv, srv.tables)                 # the game ended (abandoned) and, private, went
            self.assertNotIn(pub, srv.tables)                  # her open table had nobody else
            self.assertEqual(srv.tables[pub2].invites, {})
            m = public()
            await say(srv, m, "HELO alice")                    # someone new takes the name
            self.assertEqual(await srv.dispatch(m, "LST"), ["TAB %s reversi/1 standard 1/2 open bob,- turn=86400"
                                                            % pub2, "OK"])
            self.assertTrue((await say(srv, m, "RES %s" % priv)).startswith("ERR NOTABLE"))
        finally:
            for p in srv.plugins.values():
                p.stop()

    async def test_a_running_public_game_ends_abandoned(self):
        srv = server("none")
        await srv.load_plugins()
        try:
            a, b = public(), public()
            await say(srv, a, "HELO amy")
            await say(srv, b, "HELO ben")
            tid = (await say(srv, a, "NEW reversi/1 standard")).split()[1]
            await say(srv, a, "JON %s" % tid)
            await say(srv, b, "JON %s" % tid)
            await srv.delete_player(srv.players["amy"])
            t = srv.tables[tid]
            self.assertEqual((t.status, t.events[-1][1], t.owner), ("done", "END abandoned", ""))
            m = public()
            await say(srv, m, "HELO amy")
            self.assertTrue((await say(srv, m, "RES %s" % tid)).startswith("ERR NOGAME"))
        finally:
            for p in srv.plugins.values():
                p.stop()


# ---- 13: sign-up costs ------------------------------------------------------------------------------

class F13SignupCost(unittest.IsolatedAsyncioTestCase):
    async def test_a_mismatch_costs_no_scrypt_and_sources_are_limited(self):
        srv = server("password")
        calls = []
        real = auth.hashlib.scrypt

        def counting(*a, **kw):
            calls.append(1)
            return real(*a, **kw)
        with mock.patch.object(auth.hashlib, "scrypt", counting):
            s = public(source="203.0.113.9")
            await say(srv, s, "HELO zz1")
            await say(srv, s, "AUT password Aaaaaaa1")
            await say(srv, s, "AUT confirm Bbbbbbb2")
            self.assertEqual(calls, [])
            await say(srv, s, "AUT password Aaaaaaa1")
            self.assertEqual(await say(srv, s, "AUT confirm Aaaaaaa1"), "OK -")
            self.assertEqual(len(calls), 1)                    # once, after the two matched
        n, secs = auth.SIGNUPS_PER_SOURCE
        for i in range(n - 1):
            self.assertTrue((await say(srv, public(source="203.0.113.9"), "HELO q%d" % i)).startswith("ASK"))
        self.assertEqual(await say(srv, public(source="203.0.113.9"), "HELO qq"),
                         "ERR BUSY wait=600 Too many sign-ups from your address; try again later")
        self.assertTrue((await say(srv, public(source="198.51.100.9"), "HELO qq")).startswith("ASK"))
        self.assertTrue((await say(srv, public(source="203.0.113.9"), "HELO zz1")).startswith("ASK password Password"))

    def test_at_most_two_hashes_at_once(self):
        running, most = [0], [0]
        lock = threading.Lock()
        real = auth.hashlib.scrypt

        def slow(*a, **kw):
            with lock:
                running[0] += 1
                most[0] = max(most[0], running[0])
            time.sleep(0.05)
            with lock:
                running[0] -= 1
            return real(*a, **kw)
        with mock.patch.object(auth.hashlib, "scrypt", slow):
            ts = [threading.Thread(target=auth.hash_password, args=("password%d" % i,)) for i in range(8)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(most[0], 2)


# ---- 15: the smaller items ---------------------------------------------------------------------------

class F15Informational(unittest.IsolatedAsyncioTestCase):
    async def test_non_ascii_answer_is_a_wrong_one(self):
        stored = auth.temp_cred("abcd-efgh-jkmn")
        self.assertFalse(auth.check_temp("abcd-efgh-jkm\ufffd", stored))
        self.assertFalse(auth.check_password("abc\ufffd", auth.hash_password("abcd")))
        self.assertFalse(auth.same("abc\ufffd", "abcd"))
        srv = server("password", "email")
        await password_player(srv, "dave", "dave@example.org")
        s = public()
        await say(srv, s, "HELO dave")
        await say(srv, s, "AUT password reset")
        self.assertEqual(await say(srv, s, "AUT password 1234567\ufffd"),
                         "ERR LOCKED wait=60 Wrong password; try again in 60 seconds")

    async def test_lines_fit_80_bytes(self):
        srv = server("password")
        srv.set_setting("server.name", "n" * 32)
        srv.set_setting("auth.label", "L" * 40)
        g = srv.greeting()
        self.assertEqual(g, "PARLORNET/1 " + "n" * 32 + " AUTH=password SIGNUP=password")     # no room: no label
        srv.set_setting("server.name", "n" * 20)
        g = srv.greeting()
        self.assertEqual(len(g), 78)
        self.assertTrue(g.startswith("PARLORNET/1 " + "n" * 20 + " AUTH=password SIGNUP=password LABEL=LLLL"))
        srv.set_setting("server.motd", "short line\n" + "x" * 200 + "\n\nwords " * 30)
        out = await srv.dispatch(public(), "MOTD")
        self.assertTrue(all(len(l) <= 78 for l in out), [len(l) for l in out])
        self.assertIn("MOTD " + "x" * pcore.MOTD_WIDTH, out)

    def test_logging_leaves_out_chat_and_non_commands(self):
        from adapters import ios
        self.assertEqual(common.loggable("tell bob my secret", "tell"), "tell (text not logged)")
        self.assertEqual(common.loggable("hunter2", None), "(not a command; not logged)")
        self.assertEqual(common.loggable("match bob 5", "match"), "'match bob 5'")
        self.assertEqual(ios.verb_of("t bob hi"), "tell")
        self.assertEqual(ios.verb_of("kibitz hi"), "say")
        self.assertEqual(ios.verb_of("d3"), "pm")
        self.assertIsNone(ios.verb_of("correct-horse"))
        self.assertNotIn("whatever it is", ios.__doc__)

    async def test_the_operators_temporary_password_expires(self):
        srv = server("password")
        p = srv.accounts.player("bo", create=True)
        p.cred = auth.hash_password("Bo secret 1")
        good, text, code = await srv.accounts.reset("bo")
        self.assertTrue("24 hours" in text, "says how long")
        self.assertEqual(p.cred, "")                       # the old password stops working
        until = int(p.temp.split("$")[0])
        self.assertAlmostEqual(until, time.time() + auth.TEMP_SECS, delta=5)
        live = p.temp
        p.temp = "%d$%s" % (int(time.time()) - 1, live.split("$", 1)[1])
        s = public()
        await say(srv, s, "HELO bo")
        self.assertEqual(await say(srv, s, "AUT password " + code),
                         "ERR LOCKED wait=60 Wrong password; try again in 60 seconds")    # past its time
        p.temp, p.locked_until = live, 0
        s = public()
        await say(srv, s, "HELO bo")
        self.assertEqual(await say(srv, s, "AUT password " + code), "ASK password New_password secret new")

    def test_remove_takes_names_only(self):
        d = tempfile.mkdtemp(prefix="pnkeys")
        try:
            keys = os.path.join(d, "keys")
            frontends.write_key(keys, "web", "frontend")
            outside = os.path.join(d, "x.key")
            with open(outside, "w") as f:
                f.write("not a key\n")
            for cmd in (["remove", "../x"], ["rotate", "../x"], ["add", "a/b"]):
                self.assertEqual(frontends.main(["--keys", keys] + cmd), 2)
            self.assertTrue(os.path.exists(outside))
        finally:
            shutil.rmtree(d, True)


if __name__ == "__main__":
    unittest.main()
