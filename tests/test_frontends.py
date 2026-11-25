"""The frontend channel (docs/security-design.md): TLS 1.3 with a pre-shared
key per frontend; SES only there; enroll, rotate and revoke."""
import asyncio
import os
import ssl
import subprocess
import sys
import time
import unittest

from test_core import ROOT, Client, free_port, make_keys

sys.path.insert(0, ROOT)
from adapters.common import CoreClient, CoreError  # noqa: E402
from parlord import frontends  # noqa: E402


class Channel(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.keys = make_keys("web", "telnet")
        frontends.write_key(self.keys, "boss", "admin")
        self.port, self.fe = free_port(), free_port()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "parlord", "--port", str(self.port), "--frontend-port", str(self.fe),
             "--keys", self.keys, "--auth", "none", "--sweep", "0.3", "--rate", "1000", "--burst", "1000"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.banner = self.proc.stdout.readline().decode()

    async def asyncTearDown(self):
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()

    def key(self, name):
        return os.path.join(self.keys, name + ".key")

    async def fe_client(self, name="web"):
        return await CoreClient.connect("127.0.0.1", self.fe, self.key(name))

    async def test_tls13_with_the_key_and_nothing_else(self):
        self.assertIn("extensions on 127.0.0.1:%d (TLS 1.3, 3 enrolled)" % self.fe, self.banner)
        c = await self.fe_client()
        so = c.w.get_extra_info("ssl_object")
        self.assertEqual(so.version(), "TLSv1.3")
        self.assertTrue(so.session_reused)                     # the PSK was used
        self.assertTrue(c.greeting.startswith("PARLORNET/1 "))
        c.close()
        # a wrong key, an unknown name, an admin key on the frontend listener: no handshake
        wrong = os.path.join(self.keys, "wrong")
        os.makedirs(wrong)
        frontends.write_key(wrong, "web", "frontend")
        frontends.write_key(wrong, "stranger", "frontend")
        for path in (os.path.join(wrong, "web.key"), os.path.join(wrong, "stranger.key"), self.key("boss")):
            with self.assertRaises(CoreError):
                await CoreClient.connect("127.0.0.1", self.fe, path)
        # plain text gets nothing at all
        r, w = await asyncio.open_connection("127.0.0.1", self.fe)
        w.write(b"HELO eve\r\nSES AAAAAAAAAAAAAAAAAAAAAA\r\n")
        await w.drain()
        self.assertEqual(await asyncio.wait_for(r.read(100), 12), b"")
        w.close()
        # TLS without the key (a certificate-less client offering no PSK) is refused too
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        with self.assertRaises((ssl.SSLError, ConnectionError)):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", self.fe, ssl=ctx), 5)
        # no frontend key: the client refuses to connect at all
        with self.assertRaises(CoreError):
            await CoreClient.connect("127.0.0.1", self.fe, os.path.join(self.keys, "nonexistent.key"))

    async def test_ses_only_on_the_channel(self):
        c = await self.fe_client()
        sid = await c.helo("ann")
        self.assertRegex(sid, r"^[A-Za-z0-9_-]{22}$")
        c.close()
        p = await Client.connect(self.port)                    # the public port never resumes
        self.assertEqual((await p.req("SES " + sid))[0], "ERR NOTFRONTEND SES is only for frontends")
        self.assertEqual((await p.req("HELO ann"))[0], "OK -")  # and never shows a session id
        d = await self.fe_client()
        self.assertEqual(await d.resume(sid), "ann")
        self.assertEqual((await d.req("LST"))[1], "OK")
        d.close()
        t = await self.fe_client("telnet")                     # another frontend's session: no
        with self.assertRaises(CoreError):
            await t.resume(sid)
        t.close()

    async def test_rotate_and_remove_close_connections(self):
        c = await self.fe_client("telnet")
        await c.helo("bea")
        self.assertEqual(frontends.main(["--keys", self.keys, "rotate", "telnet"]), 0)
        with self.assertRaises((CoreError, OSError, asyncio.TimeoutError)):
            for _ in range(20):
                await c.req("LST")
                await asyncio.sleep(0.2)
        d = await self.fe_client("telnet")                     # the new key works
        d.close()
        e = await self.fe_client("web")
        self.assertEqual(frontends.main(["--keys", self.keys, "remove", "web"]), 0)
        with self.assertRaises((CoreError, OSError, asyncio.TimeoutError)):
            for _ in range(20):
                await e.req("LST")
                await asyncio.sleep(0.2)
        with self.assertRaises(CoreError):
            await CoreClient.connect("127.0.0.1", self.fe, os.path.join(self.keys, "telnet.key.gone"))


class KeyFiles(unittest.TestCase):
    def test_enrol(self):
        d = make_keys()
        path = frontends.write_key(d, "web", "frontend")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        name, role, key = frontends.read_key(path)
        self.assertEqual((name, role, len(key)), ("web", "frontend", 32))
        with self.assertRaises(FileExistsError):              # never overwritten by accident
            frontends.write_key(d, "web", "frontend")
        frontends.write_key(d, "web", "frontend", replace=True)
        self.assertNotEqual(frontends.read_key(path)[2], key)
        for bad in ("Web", "a b", "", "x" * 40, "../x"):
            with self.assertRaises(ValueError):
                frontends.write_key(d, bad, "frontend")
        with open(path) as f:
            self.assertNotIn("\n\n", f.read())
        out = subprocess.run([sys.executable, "-m", "parlord.frontends", "--keys", d, "add", "web", "ios"],
                             cwd=ROOT, capture_output=True, text=True).stdout
        self.assertIn("web: already enrolled", out)
        self.assertIn("ios: enrolled as frontend", out)
        self.assertNotIn(open(os.path.join(d, "ios.key")).read().split()[-1], out)   # the key is never printed
        self.assertEqual(sorted(frontends.Keyring(d).load()), ["ios", "web"])
        time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
