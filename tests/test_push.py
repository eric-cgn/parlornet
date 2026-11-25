"""Push notifications for ParlorNet Play (docs/play-notifications.md): the cryptography in adapters/pushcrypto.py
against the RFCs' examples and NIST's vectors, the helpers of adapters/playpush.py, the core's notices (NFY,
docs/protocol.md, Notices), and the whole path: a browser's subscription posted to the Play extension, a game
played, and a local push service that decrypts what it receives."""
import asyncio
import http.client
import http.server
import json
import os
import re
import shutil
import socket
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest

from test_core import adapter_args, free_port, make_keys, stack_args
from test_adapters import Native, spawn
from test_play import WS, kill_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters import playpush as PP, pushcrypto as C  # noqa: E402
from adapters.common import CoreClient  # noqa: E402

h = bytes.fromhex
u = C.unb64u


# ---- the cryptography -------------------------------------------------------------------------------------
class AES(unittest.TestCase):
    def test_fips197(self):
        # FIPS 197, appendix C.1
        self.assertEqual(C.aes128_encrypt_block(h("000102030405060708090a0b0c0d0e0f"),
                                                h("00112233445566778899aabbccddeeff")).hex(),
                         "69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_gcm_nist(self):
        """The GCM spec's test cases 1 to 4 (AES-128), as NIST SP 800-38D's validation uses them."""
        self.assertEqual(C.gcm_encrypt(bytes(16), bytes(12), b"").hex(), "58e2fccefa7e3061367f1d57a4e7455a")
        self.assertEqual(C.gcm_encrypt(bytes(16), bytes(12), bytes(16)).hex(),
                         "0388dace60b6a392f328c2b971b2fe78" "ab6e47d42cec13bdf53a67b21257bddf")
        k, iv = h("feffe9928665731c6d6a8f9467308308"), h("cafebabefacedbaddecaf888")
        p = h("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
              "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255")
        c3 = ("42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"
              "21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091473f5985")
        self.assertEqual(C.gcm_encrypt(k, iv, p).hex(), c3 + "4d5c2af327cd64a62cf35abd2ba6fab4")
        aad = h("feedfacedeadbeeffeedfacedeadbeefabaddad2")
        out = C.gcm_encrypt(k, iv, p[:60], aad)
        self.assertEqual(out.hex(), c3[:120] + "5bc94fbc3221a5db94fae95ae7121a47")
        self.assertEqual(C.gcm_decrypt(k, iv, out, aad), p[:60])
        bad = out[:-1] + bytes([out[-1] ^ 1])
        with self.assertRaises(ValueError):
            C.gcm_decrypt(k, iv, bad, aad)
        with self.assertRaises(ValueError):
            C.gcm_decrypt(k, iv, out, b"other")


class P256(unittest.TestCase):
    D = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721      # RFC 6979, A.2.5

    def test_public_key(self):
        self.assertEqual(C.public_bytes(self.D).hex(), "04"
                         "60fed4ba255a9d31c961eb74c6356d68c049b8923b61fa6ce669622e60f29fb6"
                         "7903fe1008b8bc99a41ae9e95628bc64f2f1b20c2d7e9f5177a3c294d4462299")
        self.assertIsNone(C.mul(C.N))           # the group's order: the point at infinity

    def test_rfc6979_signatures(self):
        """ECDSA with SHA-256 and the deterministic nonce of RFC 6979, A.2.5: "sample" and "test"."""
        for msg, r, s in ((b"sample", "efd48b2aacb6a8fd1140dd9cd45e81d69d2c877b56aaf991c34d0ea84eaf3716",
                           "f7cb1c942d657c41d436c7a1b6e29f65f3e900dbb9aff4064dc4ab2f843acda8"),
                          (b"test", "f1abb023518351cd71d881567b1ea663ed3efcf6c5132b354f28d3b0b7d38367",
                           "019f4113742a2b14bd25926b49c649155f267e60d3814b4c0cc84250e46f0083")):
            got = C.sign(self.D, msg)
            self.assertEqual(got, (int(r, 16), int(s, 16)), msg)
            self.assertTrue(C.verify(C.public_bytes(self.D), msg, *got))
            self.assertFalse(C.verify(C.public_bytes(self.D), msg + b"!", *got))
            self.assertFalse(C.verify(C.public_bytes(self.D), msg, got[0], got[1] ^ 1))

    def test_ecdh_nist(self):
        """NIST CAVS, ECC CDH primitive, P-256, COUNT = 0."""
        d = 0x7d7dc5f71eb29ddaf80d6214632eeae03d9058af1fb6d22ed80badb62bc1a534
        self.assertEqual(C.public_bytes(d).hex(), "04"
                         "ead218590119e8876b29146ff89ca61770c4edbbf97d38ce385ed281d8a6b230"
                         "28af61281fd35e2fa7002523acc85a429cb06ee6648325389f59edfce1405141")
        peer = b"\x04" + h("700c48f77f56584c5cc632ca65640db91b6bacce3a4df6b42ce7cc838833d287"
                           "db71e509e3fd9b060ddb20ba5c51dcc5948d46fbf640dfe0441782cab85fa4ac")
        self.assertEqual(C.ecdh(d, peer).hex(), "46fc62106420ff012e54a434fbdd2d25ccc5852060561e68040dd7778997bd7b")

    def test_points_are_checked(self):
        good = C.public_bytes(5)
        for bad in (good[:-1] + bytes([good[-1] ^ 1]), b"\x02" + good[1:33], good[:64], b"\x04" + bytes(64),
                    b"\x04" + (C.P).to_bytes(32, "big") + good[33:]):
            with self.assertRaises(ValueError):
                C.ecdh(7, bad)
            self.assertFalse(PP.keys_ok(C.b64u(bad), C.b64u(bytes(16))))
        self.assertTrue(PP.keys_ok(C.b64u(good), C.b64u(bytes(16))))
        self.assertFalse(PP.keys_ok(C.b64u(good), C.b64u(bytes(15))))
        self.assertFalse(PP.keys_ok("!!", "@@"))


class ContentCoding(unittest.TestCase):
    def test_rfc8188_examples(self):
        self.assertEqual(C.ece_decrypt(u("I1BsxtFttlv3u_Oo94xnmwAAEAAA-NAVub2qFgBEuQKRapoZu-IxkIva3MEB1PD-ly8Thjg"),
                                       u("yqdlZ-tYemfogSmv7Ws5PQ")), b"I am the walrus")
        two = u("uNCkWiNYzKTnBN9ji3-qWAAAABkCYTHOG8chz_gnvgOqdGYovxyjuqRyJFjEDyoF1Fvkj6hQPdPHI51OEUKEpgz3SsLWIqS_uA")
        self.assertEqual(C.ece_header(two)[1:3], (25, b"a1"))
        self.assertEqual(C.ece_decrypt(two, u("BO3ZVPxUlnLORbVGMpbT1Q")), b"I am the walrus")
        # the first example, made again from its salt
        body = C.ece_encrypt(b"I am the walrus", u("yqdlZ-tYemfogSmv7Ws5PQ"), u("I1BsxtFttlv3u_Oo94xnmw"))
        self.assertEqual(C.b64u(body), "I1BsxtFttlv3u_Oo94xnmwAAEAAA-NAVub2qFgBEuQKRapoZu-IxkIva3MEB1PD-ly8Thjg")
        with self.assertRaises(ValueError):
            C.ece_decrypt(body, u("BO3ZVPxUlnLORbVGMpbT1Q"))

    def test_rfc8291_example(self):
        """RFC 8291, section 5 and appendix A: the watermelon message, byte for byte, and back."""
        ua_pub = u("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4")
        auth = u("BTBZMqHH6r4Tts7J_aSIgg")
        as_priv = int.from_bytes(u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big")
        ua_priv = int.from_bytes(u("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"), "big")
        self.assertEqual(C.public_bytes(ua_priv), ua_pub)
        self.assertEqual(C.ecdh(as_priv, ua_pub), u("kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs"))
        body = C.webpush_encrypt(b"When I grow up, I want to be a watermelon", ua_pub, auth, as_priv,
                                 u("DGv6ra1nlYgDCS1FRnbzlw"))
        self.assertEqual(C.b64u(body), "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
                                       "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
                                       "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
        self.assertEqual(C.webpush_decrypt(body, ua_priv, auth), b"When I grow up, I want to be a watermelon")

    def test_round_trip_with_new_keys(self):
        ua = C.new_private()
        auth = os.urandom(16)
        msg = PP.payload("turn", "12", "Reversi", "ann")
        body = C.webpush_encrypt(msg, C.public_bytes(ua), auth, pad=20)
        self.assertEqual(json.loads(C.webpush_decrypt(body, ua, auth)), {"k": "turn", "t": "12", "g": "Reversi", "w": "ann"})
        self.assertNotEqual(body, C.webpush_encrypt(msg, C.public_bytes(ua), auth, pad=20))     # new keys, new salt
        with self.assertRaises(ValueError):
            C.webpush_decrypt(body, ua, os.urandom(16))


class Vapid(unittest.TestCase):
    def test_jwt(self):
        d = C.new_private()
        head = C.vapid_header(d, "https://fcm.googleapis.com", "mailto:ops@example.org", exp=2000000000)
        m = re.match(r"^vapid t=([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+), k=([A-Za-z0-9_-]+)\Z", head)
        self.assertTrue(m, head)
        self.assertEqual(json.loads(u(m.group(1))), {"typ": "JWT", "alg": "ES256"})
        self.assertEqual(json.loads(u(m.group(2))), {"aud": "https://fcm.googleapis.com", "exp": 2000000000,
                                                     "sub": "mailto:ops@example.org"})
        sig = u(m.group(3))
        self.assertEqual(len(sig), 64)
        self.assertEqual(u(m.group(4)), C.public_bytes(d))
        self.assertTrue(C.verify(u(m.group(4)), ("%s.%s" % (m.group(1), m.group(2))).encode(),
                                 int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")))

    def test_key_file(self):
        d = tempfile.mkdtemp(prefix="pnvapid")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "vapid.key")
        k = C.load_or_make_key(path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(C.load_or_make_key(path), k)
        with open(path, "w") as f:
            f.write("not a key\n")
        with self.assertRaises(ValueError):
            C.load_or_make_key(path)


class Helpers(unittest.TestCase):
    def test_endpoints(self):
        for ok in ("https://fcm.googleapis.com/fcm/send/abc:def", "https://updates.push.services.mozilla.com/wpush/v2/x",
                   "https://web.push.apple.com/QM5d", "https://wns2-par02p.notify.windows.com/w/?token=abc"):
            self.assertTrue(PP.endpoint_ok(ok), ok)
        for bad in ("http://fcm.googleapis.com/fcm/send/x", "https://fcm.googleapis.com:8443/x",
                    "https://evil.example/fcm.googleapis.com", "https://fcm.googleapis.com.evil.example/x",
                    "https://user@fcm.googleapis.com/x", "https://10.0.0.1/x", "http://127.0.0.1:9/x",
                    "https://fcm.googleapis.com/a b", "ftp://fcm.googleapis.com/x", "https://x" + "/a" * 600, None):
            self.assertFalse(PP.endpoint_ok(bad), bad)
        self.assertTrue(PP.endpoint_ok("http://127.0.0.1:9/x", ("127.0.0.1:9",)))       # a local push service
        self.assertFalse(PP.endpoint_ok("http://127.0.0.1:10/x", ("127.0.0.1:9",)))
        self.assertFalse(PP.endpoint_ok("http://push.example:9/x", ("push.example:9",)))  # plain http: loopback only
        self.assertTrue(PP.endpoint_ok("https://push.example:9/x", ("push.example:9",)))

    def test_kinds_and_contact(self):
        self.assertEqual(PP.kinds_value("join,turn"), "turn,join")
        self.assertEqual(PP.kinds_value(""), "-")
        self.assertIsNone(PP.kinds_value("turn,moves"))
        self.assertEqual(PP.contact(" mailto:ops@example.org "), "mailto:ops@example.org")
        self.assertEqual(PP.contact("https://example.org/contact"), "https://example.org/contact")
        for bad in ("ops@example.org", "http://example.org/", "mailto:", "javascript:alert(1)"):
            self.assertEqual(PP.contact(bad), "", bad)

    def test_payload_says_little(self):
        p = json.loads(PP.payload("end", "7", "Grid Words", "bob"))
        self.assertEqual(set(p), {"k", "t", "g", "w"})
        p = json.loads(PP.payload("join", "7", "Grid Words", "bob", "with Lynx 2.8.9"))
        self.assertEqual(p["v"], "with Lynx 2.8.9", "a join: what the one who sat down plays with")

    def test_the_join_notification_says_with_what(self):
        """sw.js: a join's body starts with what the player plays with, when the message carries it. With Node."""
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("Node is not installed")
        js = ("const vm = require('vm'), fs = require('fs'), ctx = {self: {addEventListener() {}}};\n"
              "vm.runInNewContext(fs.readFileSync(%s, 'utf8'), ctx);\n"
              "console.log(JSON.stringify([ctx.text({k: 'join', t: '12', g: 'Reversi', w: 'bob', v: 'with Lynx 2.8.9'}),"
              " ctx.text({k: 'join', t: '12', g: 'Reversi', w: 'bob'})]));"
              % json.dumps(os.path.join(ROOT, "adapters", "play", "sw.js")))
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [
            {"title": "bob sat down at your Reversi table", "body": "With Lynx 2.8.9. Table 12."},
            {"title": "bob sat down at your Reversi table", "body": "Table 12."}])

    def test_nfx_belongs_to_the_notice_before_it(self):
        lines = ["NFY 3 join 7 ann Tok12345 bob reversi/1", "NFX CLIENT with GameMaster 1.0.0 on a Macintosh Plus",
                 "NFY 4 join 8 ann Tok12345 cy reversi/1", "NFX SOMETHING new", "NFY 5 turn 7 ann - bob reversi/1"]
        self.assertEqual(PP.with_clients(lines), [
            ("NFY 3 join 7 ann Tok12345 bob reversi/1", "with GameMaster 1.0.0 on a Macintosh Plus"),
            ("NFY 4 join 8 ann Tok12345 cy reversi/1", ""), ("NFY 5 turn 7 ann - bob reversi/1", "")])


# ---- the core's notices -----------------------------------------------------------------------------------
class Notices(unittest.IsolatedAsyncioTestCase):
    """NFY on the extension channel: who gets which notice, the token, the wait, and the refusals."""

    async def asyncSetUp(self):
        self.core, self.fe = free_port(), free_port()
        self.keys = make_keys("play", "telnet")
        self.proc = spawn(*stack_args(self.core, self.fe, self.keys), "--auth", "none")

    async def asyncTearDown(self):
        kill_all([self.proc])

    async def ext(self, name="play", player=None):
        c = await CoreClient.connect("127.0.0.1", self.fe, os.path.join(self.keys, name + ".key"))
        if player:
            _, final = await c.req("HELO %s" % player)
            self.assertTrue(final.startswith("OK "), final)
        self.addAsyncCleanup(self.closed, c)
        return c

    @staticmethod
    async def closed(c):
        c.close()

    async def test_notices(self):
        feed = await self.ext()
        self.assertEqual((await feed.req("NFY"))[1], "OK 0")
        ann, bob = await self.ext(player="ann"), await self.ext(player="bob")
        self.assertEqual((await ann.req("PRF SET notify turn,end Tok_9-ab"))[1], "OK")
        _, final = await ann.req("NEW reversi standard")
        tid = final.split()[1]
        await ann.req("JON %s" % tid)
        # NFY n secs waits for the next notice
        waiting = asyncio.ensure_future(feed.req("NFY 0 10"))
        await asyncio.sleep(0.3)
        self.assertFalse(waiting.done())
        await bob.req("JON %s" % tid)                   # join (ann does not want it), then START and TURN 0
        data, final = await asyncio.wait_for(waiting, 5)
        self.assertEqual(data, ["NFY 2 turn %s ann Tok_9-ab - reversi/1" % tid])
        self.assertEqual(final, "OK 2")
        v, _ = await ann.req("VUE %s M" % tid)
        move = [l.split()[1] for l in v if l.startswith("M ")][0]
        await ann.req("MOV %s %s" % (tid, move))        # TURN 1: bob's turn, and bob wants nothing
        await bob.req("RES %s" % tid)
        data, final = await feed.req("NFY 2")
        self.assertEqual(data, ["NFY 4 end %s ann Tok_9-ab bob reversi/1" % tid])
        # another extension's namespace (telnet) has no notify key for ann: nothing for it
        tel = await self.ext("telnet")
        self.assertEqual(await tel.req("NFY 0"), ([], "OK 5"))
        # a number past the latest (the core restarted) is read from 0
        self.assertEqual(len((await feed.req("NFY 999"))[0]), 2)
        self.assertEqual((await feed.req("NFY x"))[1].split()[:2], ["ERR", "SYNTAX"])

    async def test_join_and_no_token(self):
        feed = await self.ext()
        ann, bob = await self.ext(player="ann"), await self.ext(player="bob")
        await ann.req("PRF SET notify join")
        _, final = await ann.req("NEW reversi standard")
        tid = final.split()[1]
        await ann.req("JON %s" % tid)
        await bob.req("JON %s" % tid)
        data, _ = await feed.req("NFY 0")
        self.assertEqual(data, ["NFY 1 join %s ann - bob reversi/1" % tid], "bob's program named nothing: no NFX")
        cy = await self.ext(player="cy")
        await cy.req("CLI PROG Lynx 2.8.9")
        _, final = await ann.req("NEW reversi standard")
        tid = final.split()[1]
        await ann.req("JON %s" % tid)
        await cy.req("JON %s" % tid)
        data, _ = await feed.req("NFY 1")
        self.assertEqual([l.split(" ", 2)[2] for l in data[:1]] + data[1:],
                         ["join %s ann - cy reversi/1" % tid, "NFX CLIENT with Lynx 2.8.9"])

    async def test_refused_on_the_public_port(self):
        n = Native(self.core, "cy", "x")
        self.addCleanup(n.close)
        self.assertTrue(n.req("NFY")[-1].startswith("ERR NOTFRONTEND"))


# ---- the whole path, with a local push service -------------------------------------------------------------
class PushService(http.server.BaseHTTPRequestHandler):
    """A push service on 127.0.0.1: keeps each POST (path, headers, body); paths with /gone/ answer 410."""
    got = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        PushService.got.append((self.path, dict(self.headers), body))
        self.send_response(410 if "/gone/" in self.path else 201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class Browser:
    """What a browser holds for one subscription: its key pair and auth secret."""

    def __init__(self, endpoint):
        self.d = C.new_private()
        self.auth = os.urandom(16)
        self.endpoint = endpoint

    def fields(self, kinds="turn,end,join"):
        return "op=on&endpoint=%s&p256dh=%s&auth=%s&kinds=%s" % (self.endpoint, C.b64u(C.public_bytes(self.d)),
                                                                C.b64u(self.auth), kinds)

    def read(self, body):
        return json.loads(C.webpush_decrypt(body, self.d, self.auth))


class EndToEnd(unittest.TestCase):
    ORIGIN = "https://play.example"
    CONTACT = "mailto:ops@play.example"

    @classmethod
    def setUpClass(cls):
        cls.svc = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PushService)
        cls.svc_port = cls.svc.server_address[1]
        threading.Thread(target=cls.svc.serve_forever, daemon=True).start()
        cls.data = tempfile.mkdtemp(prefix="pnplaydata")
        cls.core, cls.fe, cls.play_port = free_port(), free_port(), free_port()
        cls.keys = make_keys("play")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password")]
        cls.procs.append(spawn(*adapter_args("adapters.playserver", cls.play_port, cls.fe, cls.keys, "play"),
                               "--public-url", cls.ORIGIN + "/play/", "--proxies", "127.0.0.1",
                               "--data", cls.data, "--push-contact", cls.CONTACT,
                               "--push-hosts", "127.0.0.1:%d" % cls.svc_port))

    @classmethod
    def tearDownClass(cls):
        out = b""
        for p in cls.procs:
            p.kill()
            p.wait()
            out += p.stdout.read() + p.stderr.read()
            p.stdout.close()
            p.stderr.close()
        cls.svc.shutdown()
        key = open(os.path.join(cls.data, "vapid.key")).read().strip()
        shutil.rmtree(cls.data, True)
        assert key.encode() not in out, "the VAPID key reached the log"

    def http(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.play_port, timeout=10)
        hd = {"Host": "play.example", "X-Forwarded-Proto": "https"}
        if body is not None:
            hd["Content-Type"] = "application/x-www-form-urlencoded"
        hd.update(headers or {})
        c.request(method, path, body=body, headers=hd)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def signed_in_cookie(self, name):
        """A Play session for name (signed up over the WebSocket), as the pnplay cookie."""
        w = WS(self.play_port, origin=self.ORIGIN, host="play.example")
        self.addCleanup(w.close)
        w.recv()
        r = w.req("USER %s" % name)
        if r[-1].startswith("ERR NOPLAYER"):
            w.req("REG %s" % name)
            w.req("AUT password %s-Pass-word" % name)
            r = w.req("AUT confirm %s-Pass-word" % name)
        else:
            r = w.req("AUT password %s-Pass-word" % name)
        hnd = [l for l in r if l.startswith("HND ")][0].split()[1]
        r, _ = self.http("POST", "/play/session", "h=" + hnd, {"Origin": self.ORIGIN})
        w.close()
        return re.match(r"(pnplay=[A-Za-z0-9_-]{22});", r.getheader("Set-Cookie")).group(1)

    def post(self, body, cookie, origin=ORIGIN):
        hd = {"Cookie": cookie} if cookie else {}
        if origin:
            hd["Origin"] = origin
        r, data = self.http("POST", "/play/push", body, hd)
        return r.status, json.loads(data or b"{}")

    def messages(self, n, path, secs=10):
        end = time.time() + secs
        while time.time() < end:
            got = [g for g in PushService.got if g[0] == path]
            if len(got) >= n:
                return got
            time.sleep(0.1)
        self.fail("%d messages to %s; got %r" % (n, path, [g[0] for g in PushService.got]))

    def test_the_page_has_the_key(self):
        r, body = self.http("GET", "/play/")
        key = re.search(r'<meta name="pn-push" content="([A-Za-z0-9_-]*)">', body.decode()).group(1)
        self.assertEqual(len(u(key)), 65)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.data, "vapid.key")).st_mode), 0o600)
        for f in ("sw.js", "manifest.webmanifest", "notifications.html", "icon-180.png", "icon-192.png"):
            self.assertEqual(self.http("GET", "/play/" + f)[0].status, 200, f)

    def test_refusals(self):
        b = Browser("http://127.0.0.1:%d/push/x" % self.svc_port)
        cookie = self.signed_in_cookie("pxeve")
        self.assertEqual(self.post(b.fields(), cookie, origin="https://evil.example")[0], 403)
        self.assertEqual(self.post(b.fields(), cookie, origin=None)[0], 403)
        self.assertEqual(self.post(b.fields(), None)[0], 403)
        self.assertEqual(self.post(b.fields(), "pnplay=" + "A" * 22)[0], 403)
        evil = Browser("http://10.0.0.1/push")
        self.assertEqual(self.post(evil.fields(), cookie)[0], 400)
        self.assertEqual(self.post(b.fields().replace("p256dh=", "p256dh=AA"), cookie)[0], 400)
        self.assertEqual(self.post(b.fields(kinds="moves"), cookie)[0], 400)
        self.assertEqual(self.http("GET", "/play/push")[0].status, 405)

    def test_a_game_reaches_the_browser(self):
        path = "/push/ann-%d" % os.getpid()
        phone, old = Browser("http://127.0.0.1:%d%s" % (self.svc_port, path)), \
            Browser("http://127.0.0.1:%d/gone/ann" % self.svc_port)
        cookie = self.signed_in_cookie("pxann")
        self.assertEqual(self.post(phone.fields(), cookie), (200, {"ok": True, "kinds": "turn,end,join"}))
        self.assertEqual(self.post(old.fields(), cookie)[0], 200)
        db = os.path.join(self.data, "push.db")
        self.assertEqual(sqlite3.connect(db).execute("SELECT COUNT(*) FROM subs WHERE name='pxann'").fetchone()[0], 2)
        ann, bob = Native(self.core, "pxann", "pxann-Pass-word"), Native(self.core, "pxbob", "pxbob-Pass-word")
        self.addCleanup(ann.close)
        self.addCleanup(bob.close)
        tid = ann.req("NEW reversi standard")[-1].split()[1]
        ann.req("JON %s" % tid)
        bob.req("CLI PROG Lynx 2.8.9")
        bob.req("JON %s" % tid)
        got = self.messages(2, path)
        # sent on several threads, so in either order
        self.assertEqual(sorted((phone.read(g[2]) for g in got), key=lambda m: m["k"]),
                         [{"k": "join", "t": tid, "g": "Reversi", "w": "pxbob", "v": "with Lynx 2.8.9"},
                          {"k": "turn", "t": tid, "g": "Reversi", "w": ""}])
        _, hd, body = got[0]
        self.assertEqual(hd["Content-Encoding"], "aes128gcm")
        self.assertEqual(hd["Topic"], "pn" + tid)
        self.assertEqual(hd["TTL"], "86400")
        m = re.match(r"^vapid t=([^.]+)\.([^.]+)\.([^,]+), k=(\S+)\Z", hd["Authorization"])
        claims = json.loads(u(m.group(2)))
        self.assertEqual((claims["aud"], claims["sub"]), ("http://127.0.0.1:%d" % self.svc_port, self.CONTACT))
        sig = u(m.group(3))
        self.assertTrue(C.verify(u(m.group(4)), ("%s.%s" % (m.group(1), m.group(2))).encode(),
                                 int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")))
        self.assertNotIn(b"pxbob", got[0][2] + got[1][2])
        self.assertEqual(len(C.webpush_decrypt(body, phone.d, phone.auth)) % PP.PAD, 0)
        # the push service said 410 for the old one: it is gone
        end = time.time() + 5
        while time.time() < end and sqlite3.connect(db).execute(
                "SELECT COUNT(*) FROM subs WHERE name='pxann'").fetchone()[0] != 1:
            time.sleep(0.1)
        self.assertEqual(sqlite3.connect(db).execute("SELECT endpoint FROM subs WHERE name='pxann'").fetchall(),
                         [(phone.endpoint,)])
        # only the end from now on; bob resigns
        self.assertEqual(self.post("op=kinds&kinds=end", cookie), (200, {"ok": True, "kinds": "end"}))
        v = ann.req("VUE %s M" % tid)
        ann.req("MOV %s %s" % (tid, [l.split()[1] for l in v if l.startswith("M ")][0]))
        bob.req("RES %s" % tid)
        got = self.messages(3, path)
        self.assertEqual(phone.read(got[2][2]), {"k": "end", "t": tid, "g": "Reversi", "w": "pxbob"})
        # off for this device, the last one: the notify key goes too
        self.assertEqual(self.post("op=off&endpoint=" + phone.endpoint, cookie)[0], 200)
        self.assertEqual(sqlite3.connect(db).execute("SELECT COUNT(*) FROM subs WHERE name='pxann'").fetchone()[0], 0)
        w = WS(self.play_port, origin=self.ORIGIN, host="play.example", cookie=cookie)
        self.addCleanup(w.close)
        w.recv()
        self.assertEqual(w.req("SES")[-1], "OK pxann")
        self.assertEqual(w.req("PRF GET notify"), ["OK"])
        self.assertTrue(w.req("NFY 0")[-1].startswith("ERR NOTFRONTEND"))

    def test_nothing_while_the_page_is_in_view(self):
        path = "/push/cy-%d" % os.getpid()
        phone = Browser("http://127.0.0.1:%d%s" % (self.svc_port, path))
        cookie = self.signed_in_cookie("pxcy")
        self.assertEqual(self.post(phone.fields(), cookie)[0], 200)
        w = WS(self.play_port, origin=self.ORIGIN, host="play.example", cookie=cookie)
        self.addCleanup(w.close)
        w.recv()
        self.assertEqual(w.req("SES")[-1], "OK pxcy")
        tid = w.req("NEW reversi standard")[-1].split()[1]
        w.req("JON %s" % tid)
        dee = Native(self.core, "pxdee", "pxdee-Pass-word")
        self.addCleanup(dee.close)
        dee.req("JON %s" % tid)
        time.sleep(1.5)
        self.assertEqual([g for g in PushService.got if g[0] == path], [])


if __name__ == "__main__":
    unittest.main()
