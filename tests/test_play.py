"""ParlorNet Play's server extension (adapters/playserver.py, docs/modern-client.md): the client's static files
at /play/, PARLORNET/1 over a WebSocket at /play/ws (adapters/wsbridge.py, docs/protocol.md, WebSocket) and its
own session cookie, on its own port; behind a TLS proxy (play.public_url, play.proxies); and the web
extension with and without it (/play answers 301, links the Play port, or says Play is not here). Stacks of
processes (core, play, web), and a small WebSocket client written here."""
import base64
import http.client
import json
import os
import re
import socket
import sys
import time
import unittest

from test_core import adapter_args, free_port, make_keys, stack_args
from test_adapters import MailCapture, Native, Stack, plain, spawn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters import playserver as P, wsbridge  # noqa: E402

HOST = "parlornet.org:6513"
PUBLIC = "https://play.example/play/"


class WS:
    """A WebSocket client: the handshake, masked frames out, frames in."""

    def __init__(self, port, origin="http://" + HOST, cookie=None, headers=None, path="/play/ws", host=HOST):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.key = base64.b64encode(os.urandom(16)).decode()
        h = {"Host": host, "Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Key": self.key,
             "Sec-WebSocket-Version": "13", "Sec-WebSocket-Protocol": "parlornet1"}
        if origin:
            h["Origin"] = origin
        if cookie:
            h["Cookie"] = cookie
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        self.s.sendall(("GET %s HTTP/1.1\r\n" % path + "".join("%s: %s\r\n" % kv for kv in h.items()) + "\r\n")
                       .encode())
        self.buf = b""
        head = self._until(b"\r\n\r\n").decode("latin-1")
        self.status = int(head.split()[1])
        self.head = head
        self.lines = []

    def _until(self, mark):
        while mark not in self.buf:
            got = self.s.recv(4096)
            if not got:
                raise EOFError(self.buf)
            self.buf += got
        i = self.buf.index(mark) + len(mark)
        out, self.buf = self.buf[:i], self.buf[i:]
        return out

    def _exact(self, n):
        while len(self.buf) < n:
            got = self.s.recv(4096)
            if not got:
                raise EOFError(self.buf)
            self.buf += got
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send_frame(self, opcode, payload, fin=True, mask=True, length=None):
        n = len(payload) if length is None else length
        b0 = (0x80 if fin else 0) | opcode
        if n < 126:
            head = bytes([b0, (0x80 if mask else 0) | n])
        else:
            head = bytes([b0, (0x80 if mask else 0) | 126]) + n.to_bytes(2, "big")
        if mask:
            m = os.urandom(4)
            head += m
            payload = bytes(c ^ m[i & 3] for i, c in enumerate(payload))
        self.s.sendall(head + payload)

    def send(self, line):
        self.send_frame(1, line.encode())

    def frame(self):
        b0, b1 = self._exact(2)
        n = b1 & 0x7F
        if n == 126:
            n = int.from_bytes(self._exact(2), "big")
        self.assert_unmasked = not b1 & 0x80
        return b0 & 0x0F, self._exact(n)

    def recv(self):
        """The next text line; pings answered; a close frame is returned as ('CLOSE', code)."""
        while True:
            op, data = self.frame()
            if op == 1:
                return data.decode()
            if op == 9:
                self.send_frame(10, data)
            elif op == 8:
                return ("CLOSE", int.from_bytes(data[:2], "big") if len(data) >= 2 else None)

    def req(self, line):
        """Send a request; the lines of its reply, through the final OK, ERR or ASK."""
        self.send(line)
        out = []
        while True:
            l = self.recv()
            if isinstance(l, tuple):
                out.append(l)
                return out
            out.append(l)
            if l.startswith(("OK", "ERR", "ASK")):
                return out

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def kill_all(procs):
    for p in procs:
        p.kill()
        p.wait()
        p.stdout.close()
        p.stderr.close()


class PlayStack(unittest.TestCase):
    """The core and the Play extension, with play_args; no web extension (Play works without it)."""
    play_args = ()
    core_extra = ()
    origin = "http://" + HOST

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.play_port = free_port(), free_port(), free_port()
        cls.keys = make_keys("play")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password", *cls.core_extra)]
        cls.procs.append(spawn(*adapter_args("adapters.playserver", cls.play_port, cls.fe, cls.keys, "play"),
                               *cls.play_args))

    @classmethod
    def tearDownClass(cls):
        kill_all(cls.procs)

    def ws(self, **kw):
        kw.setdefault("origin", self.origin)
        w = WS(self.play_port, **kw)
        self.addCleanup(w.close)
        return w

    def http(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.play_port, timeout=5)
        h = {"Host": HOST}
        if body is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def signed_in(self, name, password, first=(), **kw):
        """A WebSocket signed in as name (the account made if there is none); first: lines sent, each answered
        OK, between the greeting and the sign-in, as the page sends CLI."""
        w = self.ws(**kw)
        self.assertEqual(w.status, 101)
        self.assertTrue(w.recv().startswith("PARLORNET/1 "))
        for line in first:
            self.assertEqual(w.req(line), ["OK"], line)
        r = w.req("USER %s" % name)
        if r[-1].startswith("ERR NOPLAYER"):
            r = w.req("REG %s" % name)
            self.assertTrue(r[-1].startswith("ASK password"), r)
            r = w.req("AUT password %s" % password)
            self.assertTrue(r[-1].startswith("ASK confirm"), r)
            r = w.req("AUT confirm %s" % password)
        else:
            self.assertTrue(r[-1].startswith("ASK password"), r)
            r = w.req("AUT password %s" % password)
        self.assertEqual(r[-1], "OK -")
        hnd = [l for l in r if l.startswith("HND ")]
        self.assertEqual(len(hnd), 1)
        return w, hnd[0].split()[1]


class Play(PlayStack):
    """Play on its own port, reached directly (no public URL, no proxy) and with no web extension."""

    # ---- the handshake -----------------------------------------------------------------------------
    def test_handshake(self):
        w = self.ws()
        self.assertEqual(w.status, 101)
        self.assertIn("Sec-WebSocket-Accept: %s" % wsbridge.accept_key(w.key), w.head)
        self.assertIn("Sec-WebSocket-Protocol: parlornet1", w.head)
        g = w.recv()
        self.assertRegex(g, r"^PARLORNET/1 \S+ AUTH=password SIGNUP=password")
        self.assertTrue(w.assert_unmasked)
        self.assertEqual(self.ws(origin="https://" + HOST).status, 101)     # a TLS proxy that keeps the Host

    def test_handshake_refusals(self):
        self.assertEqual(self.ws(origin="http://evil.example").status, 403)
        self.assertEqual(self.ws(origin=None).status, 403)
        self.assertEqual(self.ws(origin="http://" + HOST + ".evil.example").status, 403)
        self.assertEqual(self.ws(origin="http://parlornet.org:6500").status, 403)     # another port
        self.assertEqual(self.ws(headers={"Sec-WebSocket-Version": "8"}).status, 426)
        self.assertEqual(self.ws(headers={"Sec-WebSocket-Key": "short"}).status, 400)
        self.assertEqual(self.ws(headers={"Connection": "keep-alive"}).status, 400)
        self.assertEqual(self.ws(path="/lobby.htm").status, 404)
        # X-Forwarded-Proto from an address that is not a trusted proxy is not believed
        self.assertEqual(self.ws(headers={"X-Forwarded-Proto": "https"}).status, 101)

    def test_accept_key(self):
        # the example in RFC 6455, 1.3
        self.assertEqual(wsbridge.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    # ---- framing and limits ----------------------------------------------------------------------------
    def opened(self):
        w = self.ws()
        w.recv()
        return w

    def test_unmasked_frame_closes(self):
        w = self.opened()
        w.send_frame(1, b"GAM", mask=False)
        self.assertEqual(w.recv(), ("CLOSE", 1002))

    def test_binary_closes(self):
        w = self.opened()
        w.send_frame(2, b"GAM")
        self.assertEqual(w.recv(), ("CLOSE", 1003))

    def test_oversized_frame_closes(self):
        w = self.opened()
        w.send_frame(1, b"x" * 200)
        self.assertEqual(w.recv(), ("CLOSE", 1009))

    def test_long_line(self):
        w = self.opened()
        w.send("CHT 1 " + "x" * 90)
        self.assertEqual(w.recv(), "ERR TOOLONG lines are 80 bytes at most")
        self.assertEqual(w.recv(), ("CLOSE", 1009))

    def test_not_ascii(self):
        w = self.opened()
        w.send_frame(1, "MOTD é".encode())
        self.assertEqual(w.recv(), ("CLOSE", 1007))
        w = self.opened()
        w.send_frame(1, b"\xff\xfe")
        self.assertEqual(w.recv(), ("CLOSE", 1007))

    def test_fragments_and_ping(self):
        w = self.opened()
        w.send_frame(9, b"hi")
        op, data = w.frame()
        self.assertEqual((op, data), (10, b"hi"))
        w.send_frame(1, b"GA", fin=False)
        w.send_frame(9, b"x")               # a control frame between fragments
        w.send_frame(0, b"M")
        self.assertEqual(w.frame(), (10, b"x"))
        lines = []
        while not lines or not lines[-1].startswith("OK"):
            lines.append(w.recv())
        self.assertTrue(any(l.startswith("GAM reversi/1") for l in lines))

    def test_close_is_answered(self):
        w = self.opened()
        w.send_frame(8, (1000).to_bytes(2, "big"))
        self.assertEqual(w.recv(), ("CLOSE", 1000))

    def test_extension_requests_refused(self):
        w = self.opened()
        for line in ("SRC 10.0.0.1", "PRO X 1 x", "PRD X x", "NFY", "NFY 0 5", "SES AAAAAAAAAAAAAAAAAAAAAA"):
            self.assertTrue(w.req(line)[-1].startswith("ERR NOTFRONTEND"), line)
        self.assertEqual(w.req("SES")[-1], "ERR NOSESSION no session to resume")
        self.assertTrue(w.req("MOTD")[-1].startswith("OK"))      # the connection goes on

    # ---- sign-up, sign-in, a game, the session (no web extension running) -------------------------------
    def test_sign_in_and_play(self):
        w, h = self.signed_in("wsann", "wsann-Pass-word")       # a new account: REG through the bridge
        tid = w.req("NEW reversi standard turn=1h")[-1].split()[1]
        self.assertEqual(w.req("JON %s" % tid)[-1], "OK 0")
        n = Native(self.core, "wsbob", "wsbob-Pass-word")
        self.addCleanup(n.close)
        self.assertEqual(n.req("JON %s" % tid)[-1], "OK 1")
        v = w.req("VUE %s GMT" % tid)
        self.assertEqual(v[-1], "OK")
        moves = [l.split()[1] for l in v if l.startswith("M ")]
        self.assertTrue(moves)
        r = w.req("MOV %s %s" % (tid, moves[0]))
        self.assertEqual(r[-1], "OK")
        self.assertTrue(any(" PUT 0 %s" % moves[0] in l for l in r), r)
        bob = n.req("VUE %s M" % tid)
        bm = [l.split()[1] for l in bob if l.startswith("M ")]
        self.assertEqual(n.req("MOV %s %s" % (tid, bm[0]))[-1], "OK")
        r = w.req("SIN %s" % tid)
        self.assertTrue(any(" PUT 1 %s" % bm[0] in l for l in r), r)
        self.assertEqual(w.req("CHT %s good luck" % tid)[-1], "OK")
        # PRF on the modern client is the Play extension's own namespace
        self.assertTrue(w.req("PRF SET gwcolors daylight")[-1].startswith("OK"))
        self.assertIn("PRF gwcolors daylight", w.req("PRF GET gwcolors"))
        self.assertNotIn("PRF gwcolors daylight", n.req("PRF GET gwcolors"))
        # signing in again (USER) on a new WebSocket
        w2, _ = self.signed_in("wsann", "wsann-Pass-word")
        self.assertTrue(w2.req("LST")[-1].startswith("OK"))

    def test_client_turns_and_profiles(self):
        """What the page sends and reads for the turns and profiles, through the bridge: CLI before signing in,
        TRN and FEED with the program and machine it named, PFL and PFL SET PREF."""
        prog, mach = "ParlorNet Play in Firefox 132", "macOS"
        w, _ = self.signed_in("wsturn", "wsturn-Pass-word", first=("CLI PROG " + prog, "CLI MACH " + mach))
        self.assertEqual(w.req("CLI"), ["CLI PROG " + prog, "CLI MACH " + mach, "OK"])
        tid = w.req("NEW reversi standard turn=1h")[-1].split()[1]
        self.assertEqual(w.req("JON %s" % tid)[-1], "OK 0")
        n = Native(self.core, "wsturnb", "wsturnb-Pass-word")
        self.addCleanup(n.close)
        self.assertEqual(n.req("JON %s" % tid)[-1], "OK 1")
        move = next(l.split()[1] for l in w.req("VUE %s M" % tid) if l.startswith("M "))
        self.assertEqual(w.req("MOV %s %s" % (tid, move))[-1], "OK")
        r = w.req("TRN %s" % tid)
        self.assertEqual(r[-1], "OK 1")
        self.assertRegex(r[0], r"^TRN %s \d+ \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ 0 wsturn reversi/1\Z" % tid)
        self.assertIn("TRX PROG " + prog, r)
        self.assertIn("TRX MACH " + mach, r)
        seq = int(r[0].split()[2])
        self.assertEqual(w.req("TRN %s %d" % (tid, seq)), ["OK 0"])         # nothing after the last one
        feed = w.req("FEED 30")
        self.assertTrue(feed[-1].startswith("OK"))
        self.assertIn(r[0], feed)
        # the profile: a preferred machine of one's own, read by anyone; an unknown name
        self.assertEqual(w.req("PFL SET PREF Macintosh SE/30"), ["OK"])
        self.assertIn("PFL PREF Macintosh SE/30", n.req("PFL wsturn"))
        mine = w.req("PFL")
        self.assertEqual(mine[0], "PFL NAME wsturn")
        self.assertIn("PFL PROG 1 " + prog, mine)
        self.assertIn("PFL MACH 1 " + mach, mine)
        self.assertEqual(w.req("PFL SET PREF"), ["OK"])
        self.assertNotIn("PFL PREF", " ".join(w.req("PFL wsturn")))
        self.assertTrue(w.req("PFL nobodyhere")[-1].startswith("ERR NOPLAYER"))

    def test_session_handoff_and_resume(self):
        w, h = self.signed_in("wscy", "wscy-Pass-word")
        # the handle sets the Play extension's own session cookie, once
        r, _ = self.http("POST", "/play/session", "h=" + h, {"Origin": "http://" + HOST})
        self.assertEqual(r.status, 200)
        cookie = r.getheader("Set-Cookie")
        self.assertRegex(cookie, r"^pnplay=[A-Za-z0-9_-]{22}; Path=/play/; HttpOnly; SameSite=Strict\Z")  # no Secure
        sid = re.match(r"pnplay=([A-Za-z0-9_-]{22});", cookie).group(1)
        r, _ = self.http("POST", "/play/session", "h=" + h)
        self.assertEqual(r.status, 403)
        # a new WebSocket with the cookie resumes with SES alone; the session id is never sent to it
        w2 = self.ws(cookie="pnplay=" + sid)
        w2.recv()
        self.assertEqual(w2.req("SES")[-1], "OK wscy")
        self.assertTrue(w2.req("LST")[-1].startswith("OK"))
        # the web extension's cookie means nothing here
        w4 = self.ws(cookie="pn=" + sid)
        w4.recv()
        self.assertEqual(w4.req("SES")[-1], "ERR NOSESSION no session to resume")
        # signing out ends it; out=1 clears the cookie
        self.assertTrue(w2.req("OUT")[-1].startswith("OK"))
        w3 = self.ws(cookie="pnplay=" + sid)
        w3.recv()
        self.assertTrue(w3.req("SES")[-1].startswith("ERR NOSESSION"))
        r, _ = self.http("POST", "/play/session", "out=1")
        self.assertIn("pnplay=; Path=/play/", r.getheader("Set-Cookie"))
        self.assertIn("Max-Age=0", r.getheader("Set-Cookie"))

    def test_session_refuses_another_origin(self):
        w, h = self.signed_in("wsev", "wsev-Pass-word")
        r, _ = self.http("POST", "/play/session", "h=" + h, {"Origin": "http://evil.example"})
        self.assertEqual(r.status, 403)
        self.assertIsNone(r.getheader("Set-Cookie"))
        self.assertEqual(self.http("GET", "/play/session")[0].status, 405)

    def test_no_session_id_reaches_the_browser(self):
        w, h = self.signed_in("wsdi", "wsdi-Pass-word")
        self.assertFalse(re.search(r"\b[A-Za-z0-9_-]{22}\b", " ".join(w.req("WHO"))))

    # ---- the static files ------------------------------------------------------------------------------
    def test_static_files(self):
        for path in ("/play", "/"):
            r, _ = self.http("GET", path)
            self.assertEqual((r.status, r.getheader("Location")), (302, "/play/"))
        r, body = self.http("GET", "/play/")
        body = body.decode()
        self.assertEqual(r.status, 200)
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIsNone(r.getheader("Strict-Transport-Security"))         # never HSTS
        self.assertIn("<canvas", body)
        self.assertIn('<meta name="pn-classic" content="">', body)    # no web extension: no classic pages
        for src in re.findall(r'(?:src|href)="([^"]+)"', body):
            if src == "/welcome.htm":                                   # hidden by the script without it
                continue
            self.assertFalse(src.startswith(("http:", "https:", "//")), src)    # nothing from elsewhere
            r, _ = self.http("GET", "/play/" + src if not src.startswith("/") else src)
            self.assertEqual(r.status, 200, src)
            self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        r, data = self.http("GET", "/play/palette.json")
        self.assertEqual(json.loads(data)["default"], "slate")
        r, data = self.http("HEAD", "/play/app.js")
        self.assertEqual((r.status, data), (200, b""))
        self.assertEqual(self.http("GET", "/play/../playserver.py")[0].status, 404)
        self.assertEqual(self.http("GET", "/play/nothing.js")[0].status, 404)
        self.assertEqual(self.http("GET", "/welcome.htm")[0].status, 404)       # the 1997 pages are not here
        self.assertEqual(self.http("GET", "/play/ws")[0].status, 426)
        # no public https address: no push notifications, and the page has no key
        self.assertIn('<meta name="pn-push" content="">', body)
        r, data = self.http("POST", "/play/push", "op=kinds&kinds=turn", {"Origin": "http://" + HOST})
        self.assertEqual((r.status, json.loads(data)["error"]), (503, "Notifications are not offered on this server."))

    def test_page_parts(self):
        """The page's own parts: the message of the day (a dialog, a button for it, a cookie with a hash of the
        text), the manifest and icons for the Home Screen, the unfinished mark in the new-game picker, the
        Backgammon home side and the Cannon Fire module."""
        r, body = self.http("GET", "/play/")
        body = body.decode()
        for part in ('id="d-motd"', 'id="b-motd"', 'aria-label="Message of the day"', 'rel="manifest"',
                     'rel="apple-touch-icon" href="icon-180.png"', 'id="new-unfinished"', 'id="s-bghome-left"',
                     'src="game-cannonfire.js"', 'id="s-notify"', 'href="notifications.html"'):
            self.assertIn(part, body)
        with open(os.path.join(ROOT, "adapters", "play", "app.js"), encoding="utf-8") as f:
            app = f.read()
        self.assertIn('"pnmotd="', app)
        self.assertIn('"badge unfinished"', app)
        from adapters.common import BG_HOME_PREF, BG_HOMES
        self.assertIn('"PRF SET %s " + home' % BG_HOME_PREF, app)          # the key the web and telnet read
        for side in BG_HOMES:
            self.assertIn('id="s-bghome-%s" value="%s"' % (side, side), body)
        manifest = json.loads(self.http("GET", "/play/manifest.webmanifest")[1])
        self.assertEqual((manifest["display"], manifest["scope"]), ("standalone", "./"))
        r, _ = self.http("GET", "/play/manifest.webmanifest")
        self.assertEqual(r.getheader("Content-Type"), "application/manifest+json")

    def test_declares_its_protocol(self):
        n = Native(self.core, "wsprl", "wsprl-Pass-word")
        self.addCleanup(n.close)
        deadline = time.time() + 15
        while True:
            rows = [l for l in n.req("PRL") if l.startswith("PRO HTTPS ")]
            if rows or time.time() > deadline:
                break
            time.sleep(0.3)
        self.assertEqual(rows, ["PRO HTTPS %d ParlorNet Play" % self.play_port])


class PlayEmail(MailCapture, PlayStack):
    """Sign-up under sign-in password and sign-up email, as the client sends it over the WebSocket: REG, the
    address, the mailed temporary password, then a password of the player's own (its ASK carries new, so the
    browser offers to make one), and the session handle; later a plain sign-in."""

    @classmethod
    def setUpClass(cls):
        cls.core_extra = ("--signup", "email", "--settings-file", cls.mail_settings())
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.tmp.cleanup()

    def opened(self):
        w = self.ws()
        self.assertEqual(w.status, 101)
        self.assertTrue(w.recv().startswith("PARLORNET/1 "))
        return w

    def test_sign_up_by_email(self):
        w = self.opened()
        self.assertEqual(w.req("REG wsem")[-1], "ASK email Email_address")
        r = w.req("AUT email wsem@example.org")
        self.assertIn("INF We emailed a password for wsem to wsem@example.org.", r)
        self.assertEqual(r[-1], "ASK password Password_from_the_email secret")
        temp = self.mailed_password("wsem@example.org")
        r = w.req("AUT password %s" % temp)
        self.assertIn("INF That was a temporary password. Choose a password of your own now.", r)
        self.assertEqual(r[-1], "ASK password New_password secret new")
        self.assertEqual(w.req("AUT password Wsem own 1")[-1], "ASK confirm Type_it_again secret new")
        r = w.req("AUT confirm Wsem own 1")
        self.assertEqual(r[-1], "OK -")
        self.assertEqual(len([l for l in r if l.startswith("HND ")]), 1)
        v = self.opened()
        self.assertEqual(v.req("USER wsem")[-1], "ASK password Password secret cred")
        self.assertEqual(v.req("AUT password Wsem own 1")[-1], "OK -")

    def test_the_client_reads_the_new_flag(self):
        """The page's code: ASK ... new is parsed, and a new password is asked with autocomplete new-password."""
        with open(os.path.join(ROOT, "adapters", "play", "net.js"), encoding="utf-8") as f:
            net = f.read()
        with open(os.path.join(ROOT, "adapters", "play", "app.js"), encoding="utf-8") as f:
            app = f.read()
        self.assertIn('isNew: f.indexOf("new") > 2', net)
        self.assertIn('a.isNew ? "new-password"', app)


class BehindProxy(PlayStack):
    """Play behind a TLS proxy: play.public_url is https, and 127.0.0.1 (where the tests connect from) is a
    trusted proxy."""
    play_args = ("--public-url", PUBLIC, "--proxies", "127.0.0.1,10.99.0.0/16")
    origin = "https://play.example"

    def test_origin_is_the_public_urls(self):
        self.assertEqual(self.ws().status, 101)
        self.assertEqual(self.ws(host="127.0.0.1:%d" % self.play_port).status, 101)    # whatever Host it passes
        self.assertEqual(self.ws(origin="http://play.example").status, 403)
        self.assertEqual(self.ws(origin="https://play.example:8443").status, 403)
        self.assertEqual(self.ws(origin="http://" + HOST).status, 403)                 # the port itself

    def test_secure_cookie_and_redirect(self):
        w, h = self.signed_in("wspx", "wspx-Pass-word")
        r, _ = self.http("POST", "/play/session", "h=" + h, {"Origin": "https://play.example"})
        self.assertEqual(r.status, 200)
        self.assertRegex(r.getheader("Set-Cookie"), r"^pnplay=[A-Za-z0-9_-]{22}; Path=/play/; HttpOnly; "
                                                    r"SameSite=Strict; Secure\Z")
        r, _ = self.http("GET", "/play")
        self.assertEqual(r.getheader("Location"), PUBLIC)
        r, _ = self.http("GET", "/play/")
        self.assertIsNone(r.getheader("Strict-Transport-Security"))

    def test_forwarded_for_from_a_trusted_proxy(self):
        """Each browser's address comes from X-Forwarded-For: four still signing in from one address are its
        limit (a fifth closes the first), and other addresses behind the same proxy are not counted with it."""
        def opened(addr):
            w = self.ws(headers={"X-Forwarded-For": "%s, 10.99.0.5" % addr, "X-Forwarded-Proto": "https"})
            self.assertTrue(w.recv().startswith("PARLORNET/1 "))
            return w
        others = [opened("192.0.2.%d" % i) for i in range(1, 6)]
        same = [opened("198.51.100.7") for _ in range(4)]
        for w in others + same:
            self.assertTrue(w.req("MOTD")[-1].startswith("OK"))
        opened("198.51.100.7")
        with self.assertRaises((EOFError, ConnectionError, socket.timeout)):
            same[0].req("MOTD")
        for w in others + same[1:]:
            self.assertTrue(w.req("MOTD")[-1].startswith("OK"))


class Untrusted(PlayStack):
    """A proxy list without 127.0.0.1: X-Forwarded-For from here is ignored, so every WebSocket counts against
    127.0.0.1."""
    play_args = ("--proxies", "10.99.0.1")

    def test_forwarded_for_is_ignored(self):
        ws = []
        for i in range(5):
            w = self.ws(headers={"X-Forwarded-For": "192.0.2.%d" % i})
            w.recv()
            ws.append(w)
        with self.assertRaises((EOFError, ConnectionError, socket.timeout)):
            ws[0].req("MOTD")


def read_play(name):
    with open(os.path.join(ROOT, "adapters", "play", name), encoding="utf-8") as f:
        return f.read()


class PageTurns(unittest.TestCase):
    """The page's code for CLI, the profile (PFL), the activity (FEED) and a table's turns (TRN): how it is
    wired, and (with Node, when it is installed) the parsing and the words in net.js."""

    def test_cli_on_every_connection(self):
        app = read_play("app.js")
        say = app[app.index("function sayClient()"):]
        say = say[:say.index("\n  }\n")]
        self.assertIn('req("CLI PROG " + c.prog)', say)
        self.assertIn('if (c.mach) req("CLI MACH " + c.mach)', say)
        self.assertIn("PNNet.clientClaim(navigator)", say)
        conn = app[app.index("function connect()"):app.index("function lost()")]
        # right after the greeting, before MOTD and SES: so also before a sign-in and on every reconnect
        self.assertLess(conn.index("sayClient();"), conn.index('req("MOTD")'))
        net = read_play("net.js")
        self.assertIn("d = nav.userAgentData", net)          # Client Hints first, else the User-Agent string
        self.assertIn("ua = String(nav.userAgent", net)

    def test_profile_wiring(self):
        app, html = read_play("app.js"), read_play("index.html")
        self.assertIn('req("PFL " + name)', app)
        self.assertIn('req("PFL SET PREF" + (v ? " " + v : ""))', app)
        self.assertIn(".slice(0, 40)", app[app.index("function setupProfile()"):app.index("function openProfile(")])
        for part in ('<dialog id="d-profile" aria-labelledby="h-profile">', 'id="f-pref"', 'id="i-pref" maxlength="40"',
                     'aria-describedby="pf-pref-hint"', 'id="pf-games"', 'id="pf-progs"', 'id="pf-machs"', 'id="pf-recent"'):
            self.assertIn(part, html)
        # names open it: the lobby's tables, invitations and who is online, the seats, table talk, notes, turns
        self.assertIn('cls: "plink"', app)
        self.assertIn('"aria-haspopup": "dialog"', app)
        self.assertGreaterEqual(app.count("nameLink("), 9)
        self.assertIn("this.linked(text)", app)

    def test_activity_wiring(self):
        app, html = read_play("app.js"), read_play("index.html")
        self.assertIn('req("FEED " + (page ? 30 : 5))', app)
        self.assertIn('req("FEED 5 " + (p.name || name))', app)
        self.assertIn("FEED_SECS = 45", app)
        self.assertIn('setInterval(function () { if (!document.hidden) loadFeed(); }, FEED_SECS * 1000)', app)
        self.assertIn('location.hash === "#/activity"', app)
        for part in ('id="v-activity"', 'id="b-activity" aria-label="Activity"', 'id="feed-card"', 'href="#/activity"'):
            self.assertIn(part, html)

    def test_turns_on_the_table(self):
        app = read_play("app.js")
        self.assertIn('req("TRN " + this.tid + " " + (this.turnSeq || 0))', app)
        self.assertIn("TURNS_SECS = 3", app)
        self.assertIn('li.setAttribute("data-seq", seq)', app)
        self.assertIn('el("span", {cls: "via", text: via})', app)
        self.assertIn(".log li .via", read_play("play.css"))

    def test_join_client_wiring(self):
        """What each player plays with (CLIENT): asked with the lobby's list and every view, kept out of the
        game's own events."""
        app = read_play("app.js")
        self.assertIn('req("LST clients")', app)
        self.assertIn('return ok(r) ? r : req("LST")', app, "a core without the word: the list as before")
        self.assertIn('req("VUE " + self.tid + " GMTC")', app)
        self.assertNotIn('" GMT")', app)
        self.assertNotIn('" MT" :', app)
        self.assertIn('"JOIN", "CLIENT", "LEAVE"', app)
        self.assertIn('if (kind === "CLIENT") { this.client(text, joined, quiet); return; }', app)
        self.assertIn('cls: "meta via"', app)
        self.assertIn('seated && this.clients[i] ? el("span", {cls: "via", text: this.clients[i]})', app)

    def test_join_client_in_the_log(self):
        """The table talk: a CLIENT finishes the JOIN line before it, later says the player now plays with it,
        and says nothing without text. With Node, when it is installed."""
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("Node is not installed")
        app = read_play("app.js")
        events = app[app.index("  // the events the shell itself describes"):app.index("  // the table's notes (NOTE)")]
        log = app[app.index("  // a line in the table talk"):app.index("  // The table's turns (TRN)")]
        js = """const vm = require('vm'), fs = require('fs');
const P = vm.runInNewContext(fs.readFileSync(%s, 'utf8') + ';PNNet', {});
class El { constructor(t) { this.tag = t; this.children = []; this.attrs = {}; }
  appendChild(c) { this.children.push(c); c.parent = this; return c; }
  removeChild(c) { this.children.splice(this.children.indexOf(c), 1); }
  get lastChild() { return this.children[this.children.length - 1]; }
  get firstChild() { return this.children[0]; }
  setAttribute(k, v) { this.attrs[k] = v; } }
const said = [], logEl = new El('ol');
function text(e) { return e.text != null ? e.text : (e.children || []).map(text).join(''); }
function el(tag, attrs, kids) { const e = new El(tag); if (attrs && attrs.text != null) e.text = attrs.text;
  (kids || []).forEach((c) => { if (c != null) e.appendChild(typeof c === 'string' ? {text: c} : c); }); return e; }
const ctx = {PNNet: P, el, $: () => logEl, nameLink: (n) => ({text: n}), announce: (t) => said.push(t), sound() {},
  document: {createTextNode: (t) => ({text: t})}, Table: function () { this.names = ['ann', 'bob']; this.clients = {};
  this.tid = '1'; this.mod = null; }, out: null};
vm.runInNewContext(`Table.prototype.nameOf = function (s) { var n = this.names[s]; return n && n !== "-" ? n : "seat " + (s + 1); };
Table.prototype.renderPlayers = function () {}; Table.prototype.loadNotes = function () {};
` + %s, ctx);
const t = new ctx.Table();
['JOIN 0 ann', 'CLIENT 0 with GameMaster 1.0.0 on a Macintosh Plus', 'JOIN 1 bob', 'CLIENT 1', 'START',
 'CLIENT 1 with Lynx 2.8.9'].forEach((e, i) => t.event(e, false, i + 1));
console.log(JSON.stringify({log: logEl.children.map(text), clients: t.clients, said: said,
  parsed: [P.parseClient('CLIENT 2 with X on a Y'), P.parseClient('CLIENT 2'), P.parseClient('CLIENT x y')]}));
""" % (json.dumps(os.path.join(ROOT, "adapters", "play", "net.js")), json.dumps(events + log))
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(got["log"], ["ann sits down with GameMaster 1.0.0 on a Macintosh Plus", "bob sits down",
                                      "The game starts", "bob now plays with Lynx 2.8.9"])
        self.assertEqual(got["clients"], {"0": "with GameMaster 1.0.0 on a Macintosh Plus", "1": "with Lynx 2.8.9"})
        self.assertIn("ann sits down with GameMaster 1.0.0 on a Macintosh Plus", got["said"])
        self.assertEqual(got["parsed"], [{"seat": 2, "text": "with X on a Y"}, None, None])

    def node(self, script):
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("Node is not installed")
        js = ("const vm = require('vm'), fs = require('fs');\n"
              "const P = vm.runInNewContext(fs.readFileSync(%s, 'utf8') + ';PNNet', {});\n"
              "console.log(JSON.stringify((function () { %s })()));"
              % (json.dumps(os.path.join(ROOT, "adapters", "play", "net.js")), script))
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_client_claim(self):
        uas = {
            "ff": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0",
                   "ParlorNet Play in Firefox 132", "macOS"),
            "safari": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                       "Version/18.1 Safari/605.1.15", "ParlorNet Play in Safari 18", "macOS"),
            "iphone": ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
                       "Gecko) Version/18.1 Mobile/15E148 Safari/604.1", "ParlorNet Play in Safari 18", "iPhone"),
            "edge": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0", "ParlorNet Play in Edge 131", "Windows"),
            "android": ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Mobile Safari/537.36", "ParlorNet Play in Chrome 131", "Android"),
            "cros": ("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/131.0.0.0 Safari/537.36", "ParlorNet Play in Chrome 131", "ChromeOS"),
            "linux": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/131.0.0.0 Safari/537.36", "ParlorNet Play in Chrome 131", "Linux"),
            "unknown": ("curl/8.7.1", "ParlorNet Play", ""),
        }
        got = self.node("const u = %s, o = {}; for (const k in u) o[k] = P.clientClaim({userAgent: u[k]}); return o;"
                        % json.dumps({k: v[0] for k, v in uas.items()}))
        for k, (_, prog, mach) in uas.items():
            self.assertEqual(got[k], {"prog": prog, "mach": mach}, k)
            self.assertLessEqual(len(prog), 40)
        # an iPad that says it is a Mac; User-Agent Client Hints first where the browser has them
        self.assertEqual(self.node("return P.clientClaim({userAgent: %s, maxTouchPoints: 5}).mach;"
                                   % json.dumps(uas["safari"][0])), "iPad")
        hints = {"brands": [{"brand": "Not A(Brand", "version": "8"}, {"brand": "Chromium", "version": "131"},
                            {"brand": "Microsoft Edge", "version": "131"}], "platform": "Windows"}
        self.assertEqual(self.node("return P.clientClaim({userAgent: '', userAgentData: %s});" % json.dumps(hints)),
                         {"prog": "ParlorNet Play in Edge 131", "mach": "Windows"})
        self.assertEqual(self.node("return P.clientClaim({});"), {"prog": "ParlorNet Play", "mach": ""})

    def test_words_and_parsing(self):
        got = self.node("""return [
            P.viaText('Outland Gateway', 'Macintosh'), P.viaText('ParlorNet for the Apple IIe 1.1', 'Apple IIe (enhanced)'),
            P.viaText('ParlorNet Play in Firefox 132', 'macOS'), P.viaText('Netscape Navigator 3.0', ''), P.viaText('', 'iPhone'),
            P.viaText('', ''),
            P.parseTurns(['TRN 12 41 2026-11-23T14:05:09Z 0 eric gridwords/1', 'TRX MOVE played FRAULEIN for 65',
                          'TRX PROG Outland Gateway', 'TRX MACH Macintosh', 'TRX SOMETHING new',
                          'TRN 9 17 2026-11-23T14:02:40Z 1 ann reversi/1']),
            P.parseProfile(['PFL NAME eric', 'PFL PREF Macintosh SE/30', 'PFL GAME gridwords/1 12',
                            'PFL PROG 140 Outland Gateway', 'PFL MACH 162 Macintosh']),
            P.parseProfile(['PFL NAME ann', 'PFL SCORE 13 3 0 1 1 2', 'PFL GAME reversi/1 5']).score,
            P.parseProfile(['PFL NAME bo', 'PFL SCORE 0 0 0 0 0 -']).score,
            P.parseTop(['TOP 1 ann 22 5 1 0 0', 'TOP 2 bob 13 3 0 1 1', 'TOP x', 'MORE 1'])];""")
        self.assertEqual(got[:6], ["with the Outland Gateway on a Macintosh",
                                   "with ParlorNet for the Apple IIe 1.1 on an Apple IIe (enhanced)",
                                   "with ParlorNet Play in Firefox 132 on macOS", "with Netscape Navigator 3.0",
                                   "on an iPhone", ""])
        self.assertEqual(got[6], [
            {"tid": "12", "seq": 41, "at": "2026-11-23T14:05:09Z", "seat": 0, "name": "eric", "key": "gridwords/1",
             "move": "played FRAULEIN for 65", "prog": "Outland Gateway", "mach": "Macintosh"},
            {"tid": "9", "seq": 17, "at": "2026-11-23T14:02:40Z", "seat": 1, "name": "ann", "key": "reversi/1",
             "move": "", "prog": "", "mach": ""}])
        self.assertEqual(got[7], {"name": "eric", "pref": "Macintosh SE/30", "games": [{"key": "gridwords/1", "n": 12}],
                                  "progs": [{"n": 140, "text": "Outland Gateway"}],
                                  "machs": [{"n": 162, "text": "Macintosh"}], "score": None})
        self.assertEqual(got[8], {"points": 13, "wins": 3, "draws": 0, "losses": 1, "resigns": 1, "rank": 2})
        self.assertEqual(got[9]["rank"], None)
        self.assertEqual(got[10], [
            {"rank": 1, "name": "ann", "points": 22, "wins": 5, "draws": 1, "losses": 0, "resigns": 0},
            {"rank": 2, "name": "bob", "points": 13, "wins": 3, "draws": 0, "losses": 1, "resigns": 1}])


class Client(unittest.TestCase):
    """The pure parts: the client behind proxies, the public URL, the proxy list."""

    def play(self, proxies, url=""):
        return P.Play("127.0.0.1", 1, key_file="unused", url=url, proxies=P.proxy_list(proxies))

    def test_client_address(self):
        p = self.play("10.0.0.1, 10.1.0.0/16, ::1")
        xff = {"x-forwarded-for": "203.0.113.9, 198.51.100.2, 10.1.2.3", "x-forwarded-proto": "https"}
        self.assertEqual(p.client("10.0.0.1", xff), ("198.51.100.2", "https"))     # the last that is no proxy
        self.assertEqual(p.client("::ffff:10.0.0.1", xff), ("198.51.100.2", "https"))
        self.assertEqual(p.client("::1", {"x-forwarded-for": "2001:db8::1"}), ("2001:db8::1", None))
        self.assertEqual(p.client("10.0.0.2", xff), ("10.0.0.2", None))           # not a proxy: ignored
        self.assertEqual(p.client("10.0.0.1", {}), ("10.0.0.1", None))
        self.assertEqual(p.client("10.0.0.1", {"x-forwarded-for": "198.51.100.2, junk"}), ("10.0.0.1", None))
        self.assertEqual(p.client("10.0.0.1", {"x-forwarded-for": "junk, 198.51.100.2"}), ("198.51.100.2", None))
        self.assertEqual(p.client("10.0.0.1", {"x-forwarded-proto": "gopher"}), ("10.0.0.1", None))
        self.assertEqual(self.play("").client("10.0.0.1", xff), ("10.0.0.1", None))

    def test_public_url(self):
        self.assertEqual(P.public_url("https://parlornet.org/play/"),
                         ("https://parlornet.org/play/", "https://parlornet.org"))
        self.assertEqual(P.public_url("https://Parlornet.org:443"), ("https://parlornet.org/play/",
                                                                     "https://parlornet.org"))
        self.assertEqual(P.public_url("http://10.0.0.5:8080/play"), ("http://10.0.0.5:8080/play/",
                                                                     "http://10.0.0.5:8080"))
        self.assertEqual(P.public_url(""), ("", ""))
        for bad in ("ftp://x/play/", "https://x/other/", "https://u@x/play/", "https://x/play/?a=1", "x/play/"):
            with self.assertRaises(ValueError, msg=bad):
                P.public_url(bad)
        self.assertEqual(P.url_port("https://parlornet.org/play/"), 443)
        self.assertEqual(P.url_port("http://h:8080/play/"), 8080)
        with self.assertRaises(ValueError):
            P.proxy_list("10.0.0.1, nonsense")

    def test_args(self):
        a = P.parse_args(["--public-url", "https://parlornet.org/play/", "--proxies", "172.30.4.1"])
        self.assertEqual((a.port, a.public_port, a.url), (6513, 443, "https://parlornet.org/play/"))
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            a = P.parse_args(["--public-url", "not a url", "--proxies", "nonsense"])     # left out, in the log
        self.assertIn("not used", err.getvalue())
        self.assertEqual((a.url, a.proxy_nets, a.public_port), ("", [], None))

    def test_protocol_fits(self):
        from parlord import core
        key, name, desc = P.PROTOCOL
        self.assertRegex(key, core.PROTO_KEY_RE)
        self.assertLessEqual(len(name), core.PROTO_NAME_LEN)
        self.assertLessEqual(len(desc), core.PROTO_DESC_LEN)


# ---- the catalog and deploy/start.py ----------------------------------------------------------------------

class Catalog(unittest.TestCase):
    def test_play_is_an_extension_like_the_others(self):
        from adapters import catalog
        self.assertIn(("play", "adapters.playserver", 6513), [a[:3] for a in catalog.ADAPTERS])
        self.assertIn("play", catalog.default_enabled())
        self.assertEqual(catalog.parse("play"), ["play"])
        self.assertEqual(catalog.parse("play,web"), ["web", "play"])
        self.assertNotIn("play", catalog.PUBLISHED)
        with open(os.path.join(ROOT, "Dockerfile")) as f:
            docker = f.read()
        self.assertIn("play:10055", docker)
        self.assertRegex(docker, r"EXPOSE [0-9 ]* 6513\b")
        for script in ("install.sh", "install-split.sh"):
            with open(os.path.join(ROOT, "deploy", script)) as f:
                text = f.read()
            self.assertIn("6513", text, script)
            self.assertIn("PARLOR_PLAY_URL", text, script)
            self.assertIn("PARLOR_PLAY_PROXIES", text, script)
            self.assertNotIn("Strict-Transport", text, script)

    def test_install_publishes_play_on_loopback_unless_asked(self):
        import subprocess
        with open(os.path.join(ROOT, "deploy", "install.sh")) as f:
            line = next(l.strip() for l in f if "6513:6513" in l).rstrip("\\").strip()
        for env, want in (({}, "-p 127.0.0.1:6513:6513"), ({"PARLOR_PLAY_OPEN": "1"}, "-p 6513:6513")):
            out = subprocess.run(["sh", "-eu", "-c", "echo " + line], capture_output=True, text=True,
                                 env=dict(env, PATH=os.environ.get("PATH", "/usr/bin:/bin"))).stdout.strip()
            self.assertEqual(out, want)
        with open(os.path.join(ROOT, "deploy", "install.sh")) as f:
            text = f.read()
        loop = text[text.index("$(for v in"):text.index("done)") + 5]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARLOR_PLAY_URL": "https://games.example.org/play/",
               "PARLOR_PLAY_PROXIES": "172.30.4.1"}
        out = subprocess.run(["sh", "-eu", "-c", "echo " + loop], env=env, capture_output=True, text=True).stdout
        self.assertEqual(out.split(), ["-e", "PARLOR_PLAY_URL=https://games.example.org/play/",
                                       "-e", "PARLOR_PLAY_PROXIES=172.30.4.1"])


class Start(unittest.TestCase):
    """deploy/start.py starts play like any extension, with play.public_url and play.proxies from the settings
    file (else the environment), and gives the web the public URL."""

    def started(self, env, conf=None):
        import importlib.util
        import shutil
        import tempfile
        from unittest import mock
        d = tempfile.mkdtemp(prefix="pndata")
        self.addCleanup(shutil.rmtree, d, True)
        settings = os.path.join(d, "settings.conf")
        if conf:
            with open(settings, "w") as f:
                f.write(conf)
        env = dict(env, PARLOR_DB=os.path.join(d, "parlord.db"), PARLOR_SETTINGS=settings)
        with mock.patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location("pnstart_play_%d" % id(env),
                                                          os.path.join(ROOT, "deploy", "start.py"))
            st = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(st)
        started = []
        with mock.patch.dict(os.environ, env), mock.patch.object(st, "capabilities", lambda *a: 0), \
                mock.patch.object(st, "start", lambda name, args: started.append((name, args))), \
                mock.patch.object(st.time, "sleep", mock.Mock(side_effect=KeyboardInterrupt)), \
                mock.patch("builtins.print"), mock.patch.object(st.signal, "signal"), \
                mock.patch.object(st.sys, "argv", ["start.py"]):
            with self.assertRaises(KeyboardInterrupt):
                st.main()
        return dict(started)

    def test_settings_file(self):
        got = self.started({"PARLOR_ADAPTERS": "web,play", "PARLOR_PLAY_URL": "https://env.example/play/"},
                           "play.public_url=https://games.example.org/play/\nplay.proxies=172.30.4.1\n")
        self.assertEqual(got["play"][:3], ["adapters.playserver", "--host", "0.0.0.0"])
        self.assertEqual(got["play"][got["play"].index("--port") + 1], "6513")
        self.assertTrue(got["play"][got["play"].index("--key") + 1].endswith("play.key"))
        self.assertEqual(got["play"][-4:], ["--public-url", "https://games.example.org/play/",
                                            "--proxies", "172.30.4.1"])
        self.assertTrue(got["play"][got["play"].index("--data") + 1].endswith(os.sep + "play"))
        self.assertNotIn("--push-contact", got["play"])
        alone = self.started({"PARLOR_ADAPTERS": "play"}, "play.push_contact=mailto:ops@example.org\n")["play"]
        self.assertEqual(alone[alone.index("--push-contact") + 1], "mailto:ops@example.org")
        self.assertEqual(got["web"][-2:], ["--play-url", "https://games.example.org/play/"])

    def test_environment_and_each_alone(self):
        got = self.started({"PARLOR_ADAPTERS": "play", "PARLOR_PLAY_URL": "https://env.example/play/",
                            "PARLOR_PLAY_PROXIES": "10.0.0.1"})
        self.assertEqual(sorted(got), ["parlord", "play"])            # no web: play alone
        self.assertEqual(got["play"][-4:], ["--public-url", "https://env.example/play/", "--proxies", "10.0.0.1"])
        got = self.started({"PARLOR_ADAPTERS": "web", "PARLOR_PLAY_URL": "", "PARLOR_PLAY_PROXIES": ""})
        self.assertEqual(sorted(got), ["parlord", "web"])             # no play: the web alone
        self.assertNotIn("--play-url", got["web"])


# ---- the web extension with and without Play ---------------------------------------------------------------

class WebOnly(Stack):
    """The 1997 pages with Play disabled: no link to it anywhere, and /play says it is not here."""

    def test_no_play(self):
        _, _, _, body = self.get("/welcome.htm")
        self.assertNotIn("ParlorNet Play", body)
        self.assertNotIn("/play/", body)
        for path in ("/play", "/play/", "/play/app.js"):
            st, loc, _, body = self.get(path)
            self.assertEqual((st, loc), (404, None), path)
            self.assertIn("ParlorNet Play is not offered on this server.", body)
        w = WS(self.web_port, origin="http://parlornet.org:6500", host="parlornet.org:6500")   # no WebSocket here
        self.addCleanup(w.close)
        self.assertEqual(w.status, 404)


class WebAndPlay(unittest.TestCase):
    """The web extension beside a running Play extension: linked at its port, or at play.public_url (and
    then /play answers 301 there)."""

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe = free_port(), free_port()
        cls.web_port, cls.web2_port, cls.play_port = free_port(), free_port(), free_port()
        cls.keys = make_keys("web", "play")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password")]
        cls.procs.append(spawn(*adapter_args("adapters.playserver", cls.play_port, cls.fe, cls.keys, "play")))
        cls.procs.append(spawn(*adapter_args("adapters.web", cls.web_port, cls.fe, cls.keys, "web")))
        cls.procs.append(spawn(*adapter_args("adapters.web", cls.web2_port, cls.fe, cls.keys, "web"),
                               "--play-url", PUBLIC))

    @classmethod
    def tearDownClass(cls):
        kill_all(cls.procs)

    def get(self, port, path):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", path, headers={"Host": "parlornet.org:6500"})
        r = c.getresponse()
        return r.status, r.getheader("Location"), r.read().decode("latin-1")

    def welcome_link(self, port):
        """The welcome page's link to Play, once the Play extension has declared itself (or None)."""
        deadline = time.time() + 20
        while True:
            m = re.search(r'<a href="([^"]+)"><b>ParlorNet Play</b></a>', plain(self.get(port, "/welcome.htm")[2]))
            if m or time.time() > deadline:
                return m and m.group(1)
            time.sleep(0.5)

    def test_linked_at_its_port(self):
        want = "http://parlornet.org:%d/play/" % self.play_port
        self.assertEqual(self.welcome_link(self.web_port), want)
        for path in ("/play", "/play/"):
            st, loc, body = self.get(self.web_port, path)
            self.assertEqual((st, loc), (200, None))
            self.assertIn('<a href="%s">' % want, body)

    def test_play_names_the_classic_pages(self):
        """Play's page names the classic pages by their full address: the Host's name, the web's port. Two web
        extensions run here; the core lists protocols by port, and Play takes the first HTTP one, the lower
        port of the two."""
        base = "http://parlornet.org:%d/" % min(self.web_port, self.web2_port)
        deadline = time.time() + 40
        while True:
            c = http.client.HTTPConnection("127.0.0.1", self.play_port, timeout=5)
            c.request("GET", "/play/", headers={"Host": "parlornet.org:6513"})
            body = c.getresponse().read().decode()
            if base in body or time.time() > deadline:
                break
            time.sleep(1)
        self.assertIn('<meta name="pn-classic" content="%s">' % base, body)
        self.assertIn('href="%swelcome.htm"' % base, body)
        self.assertNotIn("protocols.htm", body)                         # no list of protocols on either site

    def test_redirect_to_the_public_url(self):
        for path in ("/play", "/play/", "/play/ws"):
            st, loc, _ = self.get(self.web2_port, path)
            self.assertEqual((st, loc), (301, PUBLIC), path)
        self.assertEqual(self.welcome_link(self.web2_port), PUBLIC)


if __name__ == "__main__":
    unittest.main()


class SharedPrefs(unittest.TestCase):
    def test_play_and_web_share_one_namespace(self):
        """Play's PRF data is kept under the web's name, so the Grid Words colors follow a player between
        the 1997 pages and Play; every other extension keeps its own."""
        from parlord import core

        class S:
            def __init__(self, f):
                self.frontend = f
        self.assertEqual(core.Server.pref_ns(S("play")), "web")
        self.assertEqual(core.Server.pref_ns(S("web")), "web")
        self.assertEqual(core.Server.pref_ns(S("telnet")), "telnet")
        self.assertEqual(core.Server.pref_ns(S(None)), core.NATIVE_NS)


# ---- Rummy Tile Classic in Play (adapters/play/game-rummytile.js, docs/design/rummytile-colours.md) ----------

# The module in Node with a stub shell: views in, clicks on the board's spots, the steps it sends out.
RT_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
function El(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this.text = ''; this.on = {}; }
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
El.prototype.getAttribute = function (k) { return this.attrs[k]; };
El.prototype.addEventListener = function (k, f) { this.on[k] = f; };
El.prototype.contains = function () { return false; };
El.prototype.querySelector = function () { return null; };
const board = new El('canvas'); board.clientWidth = %(cw)d; board.clientHeight = %(ch)d;
board.getBoundingClientRect = () => ({left: 0, top: 0, width: %(cw)d, height: %(ch)d});
const store = {};
const ctx = {console, Math, JSON, performance: {now: () => 0},
  localStorage: {getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; }},
  document: {getElementById: (id) => id === 'board' ? board : new El('x'), activeElement: null},
  PNGfx: {FONT: 'sans', roundRect() {}, shade: (c) => c}};
ctx.window = {innerWidth: %(cw)d, innerHeight: %(ch)d, matchMedia: () => ({matches: %(narrow)s})};
ctx.matchMedia = ctx.window.matchMedia;
let mod = null;
ctx.PN = {register: (k, m) => { mod = m; }};
vm.runInNewContext(fs.readFileSync(%(file)s, 'utf8'), ctx);
function el(tag, attrs, kids) {
  const e = new El(tag);
  Object.keys(attrs || {}).forEach((k) => {
    if (k === 'text') e.text = attrs[k]; else if (k === 'on') Object.assign(e.on, attrs.on); else e.attrs[k] = attrs[k];
  });
  (kids || []).forEach((c) => { if (c != null) e.appendChild(typeof c === 'string' ? {text: c} : c); });
  return e;
}
const VIEW = %(view)s, sent = [], toasts = [], said = [];
let turn = 0, over = false;
const api = {tid: '1', key: 'rummytile/1', seat: () => 0, names: () => ['ann', 'bob'], name: (s) => ['ann', 'bob', 'cy', 'di'][s],
  mov: (t) => { sent.push(t); return Promise.resolve('OK more'); }, chk: () => Promise.resolve('OK'),
  und: () => { sent.push('UND'); return Promise.resolve('OK'); }, end: () => { sent.push('END'); return Promise.resolve('OK'); },
  sound() {}, announce: (t) => said.push(t), toast: (t) => toasts.push(t), redraw() {}, motion: () => false,
  rtPalette: () => ({table: '#24313c', tile: '#e0e8f5', ink: '#15181c', r: '#a8241c', b: '#2563c9', k: '#1b1e23', y: '#a07c00',
                     tray: '#3a4650', mark: '#ffffff'}),
  panel: new El('section'), el, clear: (e) => { e.children = []; return e; },
  over: () => over, myTurn: () => turn === 0 && !over,
  common: () => ({status: over ? 'Game over: seat 1 wins (scores -20 20)' : '', scores: over ? {0: -20, 1: 20} : {}})};
const g = mod.create(api);
const tick = () => new Promise((r) => setTimeout(r, 1));
async function clicks(list) {
  for (const w of list) {
    const p = g.spot(w);
    if (!p) { sent.push('no spot ' + w); continue; }
    g.pointer('down', p); g.pointer('up', p); await tick();
  }
}
const texts = (e) => (e.text || '') + ' ' + (e.children || []).map(texts).join(' ');
(async function () { %(script)s })().then((out) => console.log(JSON.stringify(out)));
"""

RT_VIEW = ["V rummytile/1 5", "TIL RBKY 13 2 2", "P 70", "C 14 14", "I 1 0", "G 0 0", "W 0", "ST 1 r Y8 Y9 Y10 Y11",
           "ST 2 g* R4 B4 Y4", "ST 3 x* K1", "H R1 R4 R6 B4 B5 B12 K3 K11 Y4 Y6 Y8 Y8 Y9 J", "TP 1 - sets"]


class RummyTile(unittest.TestCase):
    """Rummy Tile Classic's board in Play: wired into the page, its steps from clicks and drags on the board, its
    words, the end, a phone held upright; and no face on the joker. With Node, when it is installed."""

    def run_node(self, script, view=RT_VIEW, cw=1000, ch=700, narrow=False):
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("Node is not installed")
        js = RT_HARNESS % {"cw": cw, "ch": ch, "narrow": "true" if narrow else "false", "script": script,
                           "view": json.dumps(view),
                           "file": json.dumps(os.path.join(ROOT, "adapters", "play", "game-rummytile.js"))}
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_wired_into_the_page(self):
        html, app = read_play("index.html"), read_play("app.js")
        self.assertIn('<script src="game-rummytile.js"></script>', html)
        self.assertLess(html.index('src="app.js"'), html.index('src="game-rummytile.js"'))
        for part in ('id="s-rt"', 'id="s-rt-presets"', 'id="s-rt-custom"', 'id="s-rt-preview"'):
            self.assertIn(part, html)
        self.assertIn('req("PRF GET rtcolors")', app)
        self.assertIn('req("PRF SET rtcolors " + rtv)', app)
        self.assertIn('fetch("/play/rummytile-colours.json"', app)
        self.assertIn("rtPalette: rtPalette", app)
        # the chooser's unfinished tag follows from a module being registered
        self.assertIn('games[g.key] ? null : el("span", {cls: "badge unfinished", text: "unfinished"})', app)
        self.assertIn('PN.register("rummytile/1"', read_play("game-rummytile.js"))

    def test_no_face_on_the_joker(self):
        js = read_play("game-rummytile.js")
        for ch in js:
            self.assertFalse(0x2639 <= ord(ch) <= 0x263B or 0x1F600 <= ord(ch) <= 0x1F64F, "a face: %r" % ch)
        self.assertNotIn('ctx.fillText("J"', js)                   # the joker: a drawn star alone
        self.assertIn("i % 2 ? R * 0.42 : R", js)

    def test_the_number_in_its_colour(self):
        """A tile shows its number alone, in its family's color; a joker a star in the ink, with no letter."""
        got = self.run_node("""const calls = [], pal = api.rtPalette();
            const c = {fillRect() {}, beginPath() {}, lineTo() {}, closePath() {},
                       fill() { calls.push(['fill', this.fillStyle]); }, fillText(t) { calls.push(['text', t, this.fillStyle]); }};
            ['R7', 'B12', 'K1', 'Y10', 'J'].forEach((t) => mod.paintTile(c, 52, 70, t, pal));
            return calls;""")
        self.assertEqual(got, [["text", "7", "#a8241c"], ["text", "12", "#2563c9"], ["text", "1", "#1b1e23"],
                               ["text", "10", "#a07c00"], ["fill", "#15181c"]])

    def test_steps_from_clicks(self):
        got = self.run_node("""g.view(VIEW); g.frame(0);
            await clicks(['rack:0', 'rack:1', 'new', 'rack:13', 'set:1', 'tile:1.2', 'tile:2.1', 'tile:1.3', 'btn:split',
                          'tile:3.1', 'rack:2', 'set:3', 'set:2', 'btn:undo', 'btn:reset']);
            return {sent, rack: g.rack()};""")
        # R1 R4 into a new set; the joker into set 1; Y9 to set 2; split set 1 before tile 3; K1 back to the rack (a
        # click on the rack with a table tile picked); set 3 picked by its label and joined onto set 2; undo; reset
        self.assertEqual(got["sent"], ["R1R4-n", "J-1", "1.2-2", "1/3", "3.1-r", "3-2", "UND", "reset"])
        self.assertEqual(got["rack"][:3], ["R1", "R4", "R6"])

    def test_drag_and_the_two_presses_of_done(self):
        got = self.run_node("""g.view(VIEW); g.frame(0);
            const a = g.spot('rack:3'), b = g.spot('tile:2.2');
            g.pointer('down', a); g.pointer('move', {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}); g.pointer('move', b);
            g.pointer('up', b);
            await tick();
            await clicks(['btn:done']);
            const first = sent.length;
            await clicks(['btn:done']);
            return {sent, first, toasts};""")
        self.assertEqual(got["sent"][0], "B4-2")
        self.assertEqual(got["first"], 1)               # set 3 is not valid: the first Done only warns
        self.assertIn("Press Done again", got["toasts"][0])
        self.assertEqual(got["sent"][1:], ["END"])

    def test_sorting_and_waiting(self):
        got = self.run_node("""g.view(VIEW);
            const byColour = g.rack().join(' ');
            g.key({key: 'o'});
            const byNumber = g.rack().join(' ');
            g.view(VIEW.map((l) => l === 'W 0' ? 'W 1' : l));
            turn = 1;
            await clicks(['rack:0', 'new']);
            return {byColour, byNumber, sent, toasts};""")
        self.assertEqual(got["byColour"], "R1 R4 R6 B4 B5 B12 K3 K11 Y4 Y6 Y8 Y8 Y9 J")
        self.assertEqual(got["byNumber"], "R1 K3 R4 B4 Y4 B5 R6 Y6 Y8 Y8 Y9 K11 B12 J")
        self.assertEqual(got["sent"], [])
        self.assertEqual(got["toasts"], ["Wait for your turn."])

    def test_before_the_first_meld(self):
        """No words or marks on the sets: a set that is not valid lies on a red mat with a red edge, one made or
        changed this turn has a light edge, and the sets locked before the first meld have dim tiles. The line over
        the buttons, the status and the panel say it in words."""
        view = [l for l in RT_VIEW if not l.startswith(("I ", "TP "))] + ["I 0 1", "TP 3 24 meld"]
        got = self.run_node("""g.view(VIEW); const f = g.frame(0);
            return {labels: f.items.filter((i) => i.t === 'text').map((i) => i.text), status: g.status(),
                    quads: f.items.filter((i) => i.t === 'quad').map((i) => [i.color, i.alpha]),
                    dim: f.items.filter((i) => i.t === 'tile' && /^Y\\d+d#/.test(i.key)).length, panel: texts(api.panel)};""", view=view)
        words = " ".join(l for l in got["labels"] if l != "bob 14 ✓")
        for w in ("run", "group", "not a set", "locked", "●", "✓"):
            self.assertNotIn(w, words)
        self.assertEqual([l for l in got["labels"] if "✗" in l], ["✗ first meld 24 / 30"])    # the bar's line only
        self.assertIn(["#ea6d62", 0.3], got["quads"])                # set 3, one tile: a red mat
        self.assertEqual(got["quads"].count(["#ea6d62", 0.85]), 4)   # and a red edge
        self.assertEqual(got["quads"].count(["#ffd27a", 0.55]), 4)   # set 2, made this turn: a light edge
        self.assertEqual(got["dim"], 4)                               # set 1, from before this turn: locked
        self.assertIn("First meld: 24 of 30 points.", got["status"])
        self.assertIn("locked until your first meld", got["status"])
        self.assertIn("Pool: 70 tiles.", got["panel"])

    def test_sets_moved_and_kept(self):
        """A set dragged by its mat goes to that place on the grid, with no step sent; the places are kept in this
        browser and found again by a new page; a set the server changes keeps its place, one that goes frees its
        place, a new one takes the first free place; Shift and an arrow move a picked set."""
        got = self.run_node("""g.view(VIEW); g.frame(0);
            const first = g.places();
            const a = g.spot('set:2'), b = g.spot('cell:4,1');
            g.pointer('down', a); g.pointer('move', {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}); g.pointer('move', b); g.pointer('up', b);
            const moved = g.places();
            const g2 = mod.create(api); g2.view(VIEW); g2.frame(0);
            const again = g2.places();
            const VIEW2 = VIEW.filter((l) => !l.startsWith('ST ')).concat(['ST 1 r Y7 Y8 Y9 Y10 Y11', 'ST 2 g R4 B4 Y4', 'ST 3 r* R1 R2 R3']);
            g2.view(VIEW2); g2.frame(0);
            const after = g2.places();
            await clicks(['set:2']);
            g.key({key: 'ArrowRight', shiftKey: true});
            const nudged = g.places();
            g.key({key: 'ArrowDown', shiftKey: true});
            return {sent, first, last: g.places(), moved, again, after, nudged, keys: Object.keys(store)};""")
        place = lambda ps: {p["tiles"]: (p["c"], p["r"]) for p in ps}
        self.assertEqual(got["sent"], [])
        self.assertEqual([(p["c"], p["r"]) for p in got["first"]][:2], [(0, 0), (9, 0)])     # in rows, in table order
        self.assertEqual(place(got["moved"])["R4 B4 Y4"], (4, 1))     # at most a row below the rest
        self.assertEqual(place(got["again"]), place(got["moved"]))
        self.assertIn("pn-rt-layout::1", got["keys"])
        after = place(got["after"])
        self.assertEqual(after["Y7 Y8 Y9 Y10 Y11"], (0, 0))          # grew where it was
        self.assertEqual(after["R4 B4 Y4"], (4, 1))
        self.assertEqual(after["R1 R2 R3"], (11, 0))                 # new: the first free place
        self.assertEqual(place(got["nudged"])["R4 B4 Y4"], (6, 1))     # a tile to the right
        self.assertEqual(place(got["last"])["R4 B4 Y4"], (6, 1))       # no further down than a row below the rest

    def test_the_smallest_zoom(self):
        """By default the zoom shows the whole board, but never smaller than the board with its sets in rows: a
        set moved far away is a pan away. + and - zoom (not below that size) and 0 shows the default again."""
        got = self.run_node("""g.view(VIEW); g.frame(0);
            const z0 = g.zoomState();
            await clicks(['set:3']);
            for (let i = 0; i < 30; i++) g.key({key: 'ArrowRight', shiftKey: true});
            const followed = g.zoomState(), far = g.places()[2];
            g.key({key: '0'});
            const z1 = g.zoomState(), v1 = g.frame(0).view;
            g.key({key: '-'}); const z2 = g.zoomState();
            g.key({key: '+'}); g.key({key: '+'}); const z3 = g.zoomState();
            g.key({key: '0'}); const z4 = g.zoomState();
            return {z0, z1, v1, z2, z3, z4, sent, followed, far};""")
        z0, z1, z2, z3, z4 = (got[k] for k in ("z0", "z1", "z2", "z3", "z4"))
        self.assertEqual(got["sent"], [])
        self.assertAlmostEqual(z0["s"], max(z0["minS"], z0["fitS"]))
        w = got["followed"]["win"]                                       # the view follows a set moved by keys
        self.assertTrue(w["x"] <= got["far"]["x0"] and got["far"]["x1"] <= w["x"] + w["w"])
        self.assertTrue(z0["all"])                                       # the whole board in view
        self.assertLess(z1["fitS"], z1["minS"])                          # spread out: it would fit only smaller
        self.assertAlmostEqual(z1["s"], z1["minS"])                      # so the zoom stays at the smallest
        self.assertFalse(z1["all"])
        self.assertLess(z1["win"]["w"], z1["board"]["w"])
        self.assertAlmostEqual(z1["win"]["x"], -0.15)                    # from the left edge
        self.assertAlmostEqual(z1["win"]["y"] + z1["win"]["h"], z1["board"]["h"] + 0.15)   # and the bottom (the rack)
        self.assertEqual(got["v1"]["win"], z1["win"])
        self.assertAlmostEqual(z2["s"], z1["minS"])                      # - goes no smaller
        self.assertGreater(z3["s"], z1["minS"] * 1.5)
        self.assertTrue(z3["user"])
        self.assertAlmostEqual(z4["s"], z1["minS"])
        self.assertFalse(z4["user"])

    def test_words_for_the_events(self):
        got = self.run_node("""return ['DEAL 14 78', 'PUT 0 R7B7K7 n', 'PUT 1 R13 4', 'MOVE 0 3 R7 n', 'BACK 0 3 R7',
            'MERGE 1 3 5', 'SPLIT 0 3 4', 'MELD 0 39', 'DONE 1 2', 'UNDONE 1 meld', 'DRAW 0 77', 'PASS 1', 'OUT 0',
            'END win 0 25 -25', 'END draw 0 0', 'SET 1 r Y8 Y9 Y10', 'DEL 2', 'TURN 1'].map((e) => g.describe(e));""")
        self.assertEqual(got, [
            "The deal: 14 tiles each, 78 in the pool", "ann puts R7 B7 K7 into a new set", "bob puts R13 into set 4",
            "ann moves R7 from set 3 to a new set", "ann takes R7 back from set 3", "bob joins set 3 onto set 5",
            "ann splits set 3 before its tile 4", "ann makes the first meld: 39 points", "bob ends the turn: 2 tiles played",
            "bob's turn does not count (the first meld was under 30 points) and is taken back",
            "ann draws a tile (77 left in the pool)", "bob passes: the pool is empty", "ann has played every tile",
            "Game over: ann wins (ann 25, bob -25)", "Game over: a draw (ann 0, bob 0)", None, None, None])

    def test_the_end_and_four_players(self):
        view = ["P 0", "C 0 3 5 2", "I 1 1 1 0", "G 0 0 1 0", "W -", "ST 1 r R1 R2 R3", "T 0 -10", "T 1 10", "H R5 R6 R7"]
        got = self.run_node("""over = true; g.view(VIEW); const f = g.frame(0);
            return {labels: f.items.filter((i) => i.t === 'text').map((i) => i.text), panel: texts(api.panel),
                    btns: ['btn:done', 'btn:undo'].map((b) => g.spot(b))};""", view=view, cw=390, ch=600, narrow=True)
        self.assertIn("Game over: bob wins", got["labels"])
        self.assertIn("bob  +20  (3 tiles left)", got["labels"])
        self.assertIn("di 2", got["labels"])
        self.assertIn("cy 5 ✓ (left)", got["labels"])
        self.assertIn("Game over: bob wins.", got["panel"])
        self.assertEqual(got["btns"], [None, None])                 # no turn buttons after the end

    def test_a_phone_held_upright(self):
        got = self.run_node("""g.view(VIEW); const f = g.frame(0); return f.view;""", cw=390, ch=844, narrow=True)
        self.assertLess(got["w"], 11)                                 # a narrow board, taller than wide
        self.assertGreater(got["h"], got["w"])


class RummyTileServed(PlayStack):
    """The Play extension serves the Rummy Tile presets and the module with the page's own policy, and keeps a
    player's colors with PRF."""

    def test_colours_and_module(self):
        from adapters import rtpalette
        r, data = self.http("GET", "/play/rummytile-colours.json")
        self.assertEqual(r.status, 200)
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertEqual(json.loads(data), json.loads(rtpalette.client_json()))
        r, data = self.http("GET", "/play/game-rummytile.js")
        self.assertEqual(r.status, 200)
        self.assertIn(b'PN.register("rummytile/1"', data)
        w, _ = self.signed_in("wsrt", "wsrt-Pass-word")
        self.assertTrue(w.req("PRF SET rtcolors cvd")[-1].startswith("OK"))
        self.assertIn("PRF rtcolors cvd", w.req("PRF GET rtcolors"))


BG_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
function El(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.text = ''; }
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
const ctx = {console, Math, JSON, performance: {now: () => 0}, PNGfx: {FONT: 'sans', roundRect() {}, shade: (c) => c, dieFace: () => [0, 0, 0, 1], quat: () => [0, 0, 0, 1], qmul: (a) => a, slerp: (a) => a}};
let mod = null;
ctx.PN = {register: (k, m) => { mod = m; }};
vm.runInNewContext(fs.readFileSync(%(file)s, 'utf8'), ctx);
const el = (tag) => new El(tag);
const api = {tid: '1', key: 'backgammon/1', seat: () => 0, names: () => ['ann', 'bob'], name: (s) => ['ann', 'bob'][s],
  sound() {}, announce() {}, toast() {}, redraw() {}, motion: () => false, bghome: () => 'right',
  panel: new El('section'), el, clear: (e) => { e.children = []; return e; }, over: () => false, myTurn: () => false};
const g = mod.create(api);
g.view(%(view)s, {});
const items = g.frame(0).items;
console.log(JSON.stringify({discs: items.filter((it) => it.t === 'disc').length,
  labels: items.filter((it) => it.t === 'tile' || it.t === 'text').map((it) => it.text)}));
"""


class BackgammonStacks(unittest.TestCase):
    """Backgammon in Play: a stack squeezes closer together up to nine checkers; past that it shows eight and
    +n in the ninth place, on the points and the bar. With Node, when it is installed."""

    def test_tall_stacks(self):
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("Node is not installed")
        view = ["V backgammon/1 40", "P 1 0 0 0 0 0 11 0 9 0 0 0 0", "P 13 0 0 0 0 0 0 -10 0 0 0 0 -5",
                "B 0 12", "O 0 0", "D 5 3", "C 1 -", "W 1", "Q move"]
        js = BG_HARNESS % {"file": json.dumps(os.path.join(ROOT, "adapters", "play", "game-backgammon.js")),
                           "view": json.dumps(view)}
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(got["discs"], 8 + 9 + 8 + 5 + 8)          # 11 on 6, 9 on 8, 10 on 19, 5 on 24, 12 on the bar
        self.assertEqual(sorted(got["labels"]), ["+2", "+3", "+4"])
