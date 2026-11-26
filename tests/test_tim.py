"""TIM (docs/protocol.md, Client requests): the seconds connected and the server's UTC time, for clients with
no clock. Before and after signing in, on the public port and the extension channel; the format; the seconds
never run backward; it is not activity (WHO's idle=); no database; and the chat command /time.

    python3 -m unittest discover -s tests -p "test_tim.py"
"""
import asyncio
import calendar
import os
import re
import sys
import time
import unittest

from test_core import ROOT, make_keys
from test_adapters import Client, TelnetBase

sys.path.insert(0, ROOT)
from adapters import chatcmd as CC  # noqa: E402
from adapters.common import CoreClient  # noqa: E402
from parlord import frontends  # noqa: E402
from parlord.core import MAXLINE, Server  # noqa: E402
from speed import slow  # noqa: E402

TIM_RE = re.compile(r"^TIM (\d+) (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\Z")


class Public:
    """One public-port connection: request -> (data lines, final line)."""

    def __init__(self, r, w):
        self.r, self.w = r, w

    async def req(self, line):
        self.w.write(line.encode() + b"\r\n")
        data = []
        while True:
            got = (await self.r.readline()).decode().rstrip("\r\n")
            if got.startswith(("OK", "ERR", "ASK")):
                return data, got
            data.append(got)

    def close(self):
        self.w.close()


class Tim(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.keys = make_keys("web")
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "none")
        self.srv.keyring = frontends.Keyring(self.keys)
        self.fe = await asyncio.start_server(self.srv.handle_frontend, "127.0.0.1", 0)
        self.pub = await asyncio.start_server(self.srv.handle, "127.0.0.1", 0)
        self.held = []

    async def asyncTearDown(self):
        for c in self.held:
            c.close()
        self.fe.close()
        self.pub.close()

    async def public(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.pub.sockets[0].getsockname()[1])
        w.write(b"PARLORNET/1\r\n")
        await r.readline()          # the greeting
        c = Public(r, w)
        self.held.append(c)
        return c

    async def ext(self):
        c = await CoreClient.connect("127.0.0.1", self.fe.sockets[0].getsockname()[1],
                                     os.path.join(self.keys, "web.key"))
        self.held.append(c)
        return c

    def newest(self):
        """The core's Session of the connection opened last."""
        return max(self.srv.sessions, key=lambda s: s.opened)

    async def tim(self, c):
        """TIM on c -> (secs, the server's time as seconds since the epoch), checking the reply's shape."""
        data, final = await c.req("TIM")
        self.assertEqual(final, "OK")
        self.assertEqual(len(data), 1, data)
        m = TIM_RE.match(data[0])
        self.assertIsNotNone(m, data[0])
        self.assertLessEqual(len(data[0]) + 2, MAXLINE)
        return int(m.group(1)), calendar.timegm(time.strptime(m.group(2), "%Y-%m-%dT%H:%M:%SZ"))

    async def test_public_port_before_and_after_signing_in(self):
        c = await self.public()
        self.newest().opened -= 100                 # the connection opened 100 seconds ago
        secs, now = await self.tim(c)               # before signing in: nothing yet
        self.assertEqual(secs, 0)
        self.assertLessEqual(abs(now - time.time()), 2)     # UTC: the same instant as the epoch clock
        self.assertEqual((await c.req("HELO ann"))[1], "OK -")
        secs, _ = await self.tim(c)                 # signed in just now
        self.assertLessEqual(secs, 2)
        self.newest().signed_in -= 100              # the sign-in was 100 seconds ago
        after, _ = await self.tim(c)
        self.assertGreaterEqual(after, 100)
        self.assertLess(after, 110)
        self.assertEqual((await c.req("OUT"))[1], "OK 1")
        secs, _ = await self.tim(c)                 # signing out ends the time signed in
        self.assertEqual(secs, 0)

    async def test_nobody_sits_at_the_sign_in(self):
        """A public-port connection that hasn't signed in within Limits.signin_secs of connecting, or of
        signing out, is closed, whatever it sends. Signed in, it stays."""
        from parlord.core import Limits
        old = Limits.signin_secs
        Limits.signin_secs = 1.0
        try:
            c = await self.public()
            await self.tim(c)                       # talking doesn't keep it open
            await asyncio.sleep(1.3)
            self.assertEqual((await c.r.readline()).decode().rstrip("\r\n"),
                             "ERR IDLE closing: sign in within 5 minutes of connecting")
            d = await self.public()
            self.assertEqual((await d.req("HELO ann"))[1], "OK -")
            await asyncio.sleep(1.3)
            self.assertEqual((await d.req("TIM"))[1], "OK")     # signed in: it stays
            self.assertEqual((await d.req("OUT"))[1], "OK 1")
            await asyncio.sleep(1.3)                # signed out: the limit starts again
            self.assertEqual((await d.r.readline()).decode().rstrip("\r\n"),
                             "ERR IDLE closing: sign in within 5 minutes of connecting")
        finally:
            Limits.signin_secs = old

    async def test_extension_channel_counts_from_the_session(self):
        x = await self.ext()
        self.newest().opened -= 40
        secs, _ = await self.tim(x)                 # before signing in: nothing yet
        self.assertEqual(secs, 0)
        _, final = await x.req("HELO ann")
        sid = final.split()[1]
        secs, _ = await self.tim(x)                 # signed in: since the sign-in, just now
        self.assertLessEqual(secs, 2)
        self.newest().signin.since -= 300           # the sign-in was 300 seconds ago
        y = await self.ext()                        # a new connection resumes it (as the web does for each page)
        self.assertEqual(await y.req("SES " + sid), ([], "OK ann"))
        secs, _ = await self.tim(y)
        self.assertGreaterEqual(secs, 300)
        self.assertLess(secs, 310)
        self.assertEqual((await y.req("OUT"))[1], "OK 1")
        secs, _ = await self.tim(y)                 # signed out: nothing again
        self.assertEqual(secs, 0)

    async def test_secs_never_run_backward(self):
        c = await self.public()
        await c.req("HELO bob")
        x = await self.ext()
        await x.req("HELO ann")
        last = {c: -1, x: -1}
        for _ in range(3):
            for conn in (c, x):
                secs, _ = await self.tim(conn)
                self.assertGreaterEqual(secs, last[conn])
                last[conn] = secs
            await asyncio.sleep(0.6)
        self.assertGreaterEqual(last[c], 1)         # 1.2 seconds have passed
        self.assertGreaterEqual(last[x], 1)

    async def test_not_activity_and_no_database(self):
        c = await self.public()
        await c.req("HELO ann")
        p = self.srv.accounts.players["ann"]
        p.active -= 500                             # ann did something 500 seconds ago
        before = p.active
        seen = []
        self.srv.store.db.conn.set_trace_callback(seen.append)
        try:
            for _ in range(5):
                await self.tim(c)
        finally:
            self.srv.store.db.conn.set_trace_callback(None)
        self.assertEqual(seen, [])                  # no statement reached the database
        self.assertEqual(p.active, before)
        data, _ = await c.req("WHO")
        idle = int(re.search(r"PLR ann idle=(\d+)", " ".join(data)).group(1))
        self.assertGreaterEqual(idle, 500)
        await c.req("ACT")                          # ACT does count
        data, _ = await c.req("WHO")
        self.assertIn("PLR ann idle=0", data)


class TimeCommand(unittest.TestCase):
    """/time in the shared chat commands (adapters/chatcmd.py)."""

    def said(self, reply):
        said = []

        async def req(text):
            self.assertEqual(text, "TIM")
            return reply
        c = CC.Console(said.extend, req, "ann", CC.NAMES)
        asyncio.run(c.handle("/time"))
        return said

    def test_time(self):
        self.assertEqual(self.said((["TIM 754 2026-11-24T21:05:33Z"], "OK")),
                         ["Signed in 12:34. Server time 21:05 UTC."])
        self.assertEqual(self.said((["TIM 3725 2026-11-24T09:00:00Z"], "OK")),      # two lines on 40 columns
                         ["Signed in 1:02:05.", "Server time 09:00 UTC."])
        self.assertEqual(self.said((["TIM 5 2026-11-24T00:00:00Z"], "OK")),
                         ["Signed in 0:05. Server time 00:00 UTC."])
        self.assertEqual(self.said((["TIM 0 2026-11-24T00:00:00Z"], "OK")),        # not signed in yet
                         ["Server time 00:00 UTC."])
        self.assertEqual(self.said(([], "ERR UNKNOWN no such request")), ["This server does not tell the time."])
        self.assertEqual(self.said(([], "OK")), ["The server's time is not known."])

    def test_help_and_fits_a_40_column_screen(self):
        self.assertIn("/time: time signed in, server time", CC.help_lines(CC.NAMES))
        for secs in (0, 3599, 3600, 86399, 999999):
            for line in CC.time_text(["TIM %d 2026-11-24T23:59:59Z" % secs], "OK"):
                self.assertLessEqual(len(line), 40, line)
        self.assertEqual(CC.time_text(["TIM 3725 2026-11-24T09:00:00Z"], "OK", width=80),
                         ["Signed in 1:02:05. Server time 09:00 UTC."])

    def test_against_the_core(self):
        async def go():
            srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "none")
            pub = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
            r, w = await asyncio.open_connection("127.0.0.1", pub.sockets[0].getsockname()[1])
            w.write(b"PARLORNET/1\r\n")
            await r.readline()
            c = Public(r, w)
            await c.req("HELO ann")
            max(srv.sessions, key=lambda s: s.opened).signed_in -= 754
            said = []
            await CC.Console(said.extend, c.req, "ann", ["time"]).handle("/time")
            c.close()
            pub.close()
            return said
        said = asyncio.run(go())
        self.assertTrue(re.match(r"^Signed in 12:3[45]\. Server time \d\d:\d\d UTC\.\Z", said[0]), said)


@slow
class TelnetTime(TelnetBase):
    """The telnet lobby's /time, in plain text and on the full screen."""
    auth = "none"

    def test_time_in_the_menu(self):
        a = Client(self.tel_port)
        self.login(a, "tess", "4")                  # plain text
        time.sleep(1.2)                             # signed in a second ago: TIM says 1 or more
        a.send("time\r\n")
        out = a.until("UTC.")
        self.assertTrue(re.search(r"Signed in \d+:\d\d\. Server time \d\d:\d\d UTC\.", out), out)
        a.send("help\r\n")
        self.assertIn("/time the time signed in", a.until("/quit "))
        a.close()
        b = Client(self.tel_port)
        self.login(b, "tom", "1")                    # full screen
        b.send("/time\r")
        b.until("Server time", screen=True)
        b.close()


if __name__ == "__main__":
    unittest.main()
