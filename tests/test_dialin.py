"""The dial-in extension (adapters/dialin.py; docs/protocol.md, Dial-in gateways): its window of 5 seconds, a
call that goes to the native protocol and one that goes to telnet, a PROXY header from an address that is not
a gateway, and the time allowance of a call either way.

The core, its extension channel and the extension run in the test's own event loop. Dialin runs the window as
built, 5 seconds, and is a slow test. Quick runs the same tests with a window of 1 second, and runs in the
fast suite.

    python3 -m unittest discover -s tests -p "test_dialin.py"
"""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from speed import slow
from test_allowance import GATEWAY, NOV_30, Clock, header
from test_core import ROOT, make_keys

sys.path.insert(0, ROOT)
from adapters import dialin  # noqa: E402
from adapters.common import CoreClient  # noqa: E402
from parlord import allowance as A  # noqa: E402
from parlord import frontends  # noqa: E402
from parlord.core import Server  # noqa: E402

KEYS = make_keys("dialin", "web")


class Line:
    """The caller's end of a call: what the gateway relays."""

    def __init__(self, r, w):
        self.r, self.w = r, w
        self.got = b""

    def send(self, data):
        self.w.write(data)

    async def until(self, text, secs=10):
        """Read until text has come -> everything read so far."""
        text = text.encode() if isinstance(text, str) else text
        while text not in self.got:
            data = await asyncio.wait_for(self.r.read(4096), secs)
            if not data:
                raise AssertionError("closed before %r came: %r" % (text, self.got[-300:]))
            self.got += data
        return self.got

    async def quiet(self, secs):
        """Nothing comes for secs."""
        try:
            data = await asyncio.wait_for(self.r.read(4096), secs)
        except asyncio.TimeoutError:
            return
        raise AssertionError("%r came within %.1f seconds" % (data, secs))

    async def closed(self, secs=10):
        while True:
            data = await asyncio.wait_for(self.r.read(4096), secs)
            if not data:
                return self.got
            self.got += data


class Calls:
    """The tests, for a window of WINDOW seconds: a native opening EARLY and LATE in it, keys typed at TYPING."""

    async def asyncSetUp(self):
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "none")
        self.srv.keyring = frontends.Keyring(KEYS)
        self.srv.settings["dialin.gateways"] = GATEWAY
        self.clock = Clock(NOV_30 - 86400)
        self.srv.allowance.clock = self.clock
        self.fe = await asyncio.start_server(self.srv.handle_frontend, "127.0.0.1", 0)
        core_port = self.fe.sockets[0].getsockname()[1]
        patch = mock.patch.object(dialin.CallCore, "key_file", os.path.join(KEYS, "dialin.key"))
        patch.start()
        self.addCleanup(patch.stop)
        args = SimpleNamespace(core_host="127.0.0.1", core_port=core_port, window=self.WINDOW)
        self.ext = await asyncio.start_server(dialin.handler(args), "127.0.0.1", 0)
        self.lines = []

    async def asyncTearDown(self):
        for c in self.lines:
            c.w.close()
        self.ext.close()
        self.fe.close()
        await asyncio.sleep(0.05)

    async def call(self, first=b""):
        r, w = await asyncio.open_connection("127.0.0.1", self.ext.sockets[0].getsockname()[1])
        c = Line(r, w)
        self.lines.append(c)
        if first:
            c.send(first)
        return c

    def newest(self):
        return max(self.srv.sessions, key=lambda s: s.opened)

    async def account(self, name):
        """An account for name, made over the extension channel (sign-in none: HELO makes it)."""
        x = await CoreClient.connect("127.0.0.1", self.fe.sockets[0].getsockname()[1],
                                     os.path.join(KEYS, "web.key"))
        await x.req("HELO %s" % name)
        await x.finish()

    async def native(self, at):
        c = await self.call(header(prior=60))
        await c.quiet(at)                                       # nothing is sent while the window is open
        c.send(b"PARLORNET/1\r\n")
        got = await c.until(b"\r\n", secs=1.0)                  # the greeting, at once
        self.assertTrue(got.startswith(b"PARLORNET/1 test AUTH=none"), got)
        s = self.newest()
        self.assertEqual((s.frontend, s.cls, s.source, s.call["caller"]), (None, A.DIALIN, "192.0.2.7", "5551"))
        self.assertEqual(s.eol, b"\r\n")
        c.send(b"TIM\r\n")
        got = await c.until(b"OK\r\n")
        left = int(got.split(b"left=")[1].split()[0])
        self.assertLessEqual(75 * 60 - left, 62 + int(at))      # the gateway's minute and the window count
        c.send(b"SES Wq1aT0x7f3VbQ0x5hY2LmA\r\n")
        self.assertIn(b"ERR NOTFRONTEND", await c.until(b"ERR NOTFRONTEND"))      # the public port's rules
        c.send(b"HELO ann\r\n")
        self.assertIn(b"OK -\r\n", await c.until(b"OK -\r\n"))  # and its sign-in reply

    async def test_native_early(self):
        await self.native(self.EARLY)

    async def test_native_late(self):
        await self.native(self.LATE)

    async def test_native_at_once(self):
        """An 8-bit client sends its opening the moment the modems connect, in the same packet as the header,
        and its first request after the greeting. Stray bytes before it are allowed, as on 6501."""
        c = await self.call(header() + b"\x00~\rPARLORNET/1\r")
        got = await c.until(b"\r", secs=1.0)
        self.assertTrue(got.startswith(b"PARLORNET/1 test "), got)
        await asyncio.sleep(0.3)                                # past the 250 ms for a second byte
        self.assertEqual(self.newest().eol, b"\r")
        c.send(b"TIM\r")
        self.assertIn(b"\rTIM 0 ", await c.until(b"OK\r"))

    async def telnet_signin(self, c, name="ann"):
        """Through the title page and the login: the session's lobby."""
        await c.until("Press Enter")
        c.send(b"\r")
        await c.until("Login (your name")
        c.send(name.encode() + b"\r")
        await c.until("Press Enter for the lobby")
        c.send(b"\r")
        await c.until("> ")

    async def test_silence_goes_to_telnet(self):
        await self.account("ann")
        c = await self.call(header())
        await c.quiet(self.WINDOW - 0.3)
        got = await c.until("Press Enter", secs=2)
        self.assertNotIn(b"\xff", got)                          # no telnet commands
        self.assertNotIn(b"Press Enter to choose a terminal type", got)
        self.assertIn(b"########", got)                         # the title page, 80 columns
        self.assertLessEqual(max(len(l) for l in got.split(b"\r\n")), 80)
        await self.telnet_signin(c)
        self.assertIn(b"ann\r\n", c.got)                        # what the caller typed, echoed
        s = next(s for s in self.srv.sessions if s.player and s.player.name == "ann")
        self.assertEqual((s.frontend, s.cls, s.source, s.call), ("dialin", A.DIALIN, "192.0.2.7",
                                                                  {"caller": "5551", "modulation": "V.34"}))
        self.assertRegex(c.got, rb" onl (59|60)m rem\r\n")    # the plain menu's header: ann's month, from TIM
        c.send(b"quit\r")
        await c.until("Type Y for yes")
        c.send(b"y\r")
        self.assertIn(b"Bye.", await c.closed())

    async def test_typing_goes_to_telnet(self):
        """Keys typed during the window are not the opening: telnet when the window ends, and what was typed is
        dropped. Then nothing switches it back."""
        c = await self.call(header())
        await asyncio.sleep(self.TYPING)
        c.send(b"hello\r")
        await c.quiet(self.WINDOW - self.TYPING - 0.3)
        await c.until("Press Enter", secs=2)
        self.assertNotIn(b"hello", c.got)                       # neither echoed nor taken as the Enter
        c.send(b"PARLORNET/1\r\n")                              # too late: these are keys now
        await c.until("Login (your name")
        self.assertIn(b"PARLORNET/1", c.got.split(b"Press Enter")[-1])      # echoed as typed
        self.assertNotIn(b"AUTH=", c.got)

    async def test_no_header(self):
        """A connection without a header, the gateway's or anyone's: the default class either way."""
        c = await self.call(b"PARLORNET/1\r\n")
        await c.until(b"\r\n", secs=1.0)
        self.assertEqual((self.newest().cls, self.newest().meter, self.newest().source), (A.DEFAULT, None, GATEWAY))
        t = await self.call()
        await t.until("Press Enter", secs=self.WINDOW + 2)
        x = next(s for s in self.srv.sessions if s.frontend == "dialin")
        self.assertEqual((x.cls, x.meter, x.source), (A.DEFAULT, None, GATEWAY))

    async def test_stray_bytes(self):
        """Line noise early in the window does not decide anything: the opening after it still goes native, with
        at most 10 bytes before it. More than 10 is telnet, when the window ends."""
        c = await self.call(header())
        await asyncio.sleep(self.EARLY)
        c.send(b"\x00\x7f+")
        await asyncio.sleep(self.TYPING)
        c.send(b"PARLORNET/1\r\n")
        self.assertTrue((await c.until(b"\r\n", secs=1.0)).startswith(b"PARLORNET/1 test "))
        d = await self.call(header() + b"12345678901PARLORNET/1\r\n")
        await d.quiet(self.WINDOW - 0.3)
        await d.until("Press Enter", secs=2)

    async def test_who_may_say_what_a_call_is(self):
        port = self.fe.sockets[0].getsockname()[1]
        web = await CoreClient.connect("127.0.0.1", port, os.path.join(KEYS, "web.key"))
        for line in ("DIA 127.0.0.1", "DIA 127.0.0.1 5", "DIA NATIVE 127.0.0.1"):
            self.assertTrue((await web.req(line))[1].startswith("ERR NOTFRONTEND"), line)
        web.close()
        x = await CoreClient.connect("127.0.0.1", port, os.path.join(KEYS, "dialin.key"))
        self.assertEqual((await x.req("DIA 10.9.9.9"))[1], "ERR GATEWAY not a dial-in gateway")
        self.assertEqual((await x.req("DIA 10.9.9.9 5"))[1], "ERR GATEWAY not a dial-in gateway")
        self.assertTrue((await x.req("DIA caller 5551"))[1].startswith("ERR STATE"))
        self.assertEqual((await x.req("DIA 127.0.0.1"))[1], "OK")
        self.assertEqual((await x.req("DIA 127.0.0.1 5"))[1], "OK")
        s = next(s for s in self.srv.sessions if s.frontend == "dialin")
        self.assertEqual((s.cls, s.source), (A.DIALIN, GATEWAY))
        self.assertEqual((await x.req("SRC 2001:db8:4006:812::200e"))[1], "OK")
        self.assertEqual(s.source, "2001:db8:4006::/48")
        self.assertEqual(self.srv.allowance.left(s.meter), (75 * 60 - 5, "call"))      # up 5 seconds already
        self.assertEqual((await x.req("DIA caller ANN SMITH " + "x" * 57))[1], "OK")
        self.assertEqual(s.call["caller"], ("ANN SMITH " + "x" * 57)[:64])
        for line in ("DIA 127.0.0.1 5", "DIA NATIVE 127.0.0.1"):
            self.assertTrue((await x.req(line))[1].startswith("ERR STATE"), line)
        await x.req("HELO ann")
        self.assertTrue((await x.req("DIA 127.0.0.1"))[1].startswith("ERR STATE"))
        x.close()

    async def test_a_header_from_another_address(self):
        for gateways in ("10.0.0.1", ""):                       # another address; none, the feature off
            self.srv.settings["dialin.gateways"] = gateways
            c = await self.call(header() + b"PARLORNET/1\r\n")
            got = await c.closed(secs=1.0)                      # at once, not when the window ends
            self.assertTrue(got.startswith(b"ERR PROTOCOL wrong protocol"), got)
            self.assertEqual(got.count(b"\r\n"), 1)
            self.assertFalse(any(s.cls == A.DIALIN for s in self.srv.sessions))
        self.srv.settings["dialin.gateways"] = GATEWAY
        c = await self.call(header(size=3))                     # a gateway's malformed header
        self.assertTrue((await c.closed(secs=1.0)).startswith(b"ERR PROTOCOL"))

    async def test_native_time_allowance(self):
        self.srv.settings["dialin.call_mins"] = "10"
        c = await self.call(header() + b"PARLORNET/1\r\n")
        await c.until(b"\r\n", secs=1.0)
        c.send(b"HELO ann\r\n")
        await c.until(b"OK -\r\n")
        self.clock.at += 5 * 60 + 1
        c.send(b"LST\r\n")
        self.assertIn(b"INF This call ends in 5 minutes.\r\nOK\r\n", await c.until(b"OK\r\n"))
        self.clock.at += 5 * 60
        got = await c.closed()                                  # without a request
        self.assertTrue(got.endswith(b"ERR LIMIT closing: this call has had its 10 minutes\r\n"), got[-80:])
        await asyncio.sleep(0.1)
        self.assertGreaterEqual(self.srv.allowance.used_secs("2026-11", A.DIALIN, "ann"), 10 * 60)

    async def test_telnet_time_allowance(self):
        """A telnet call has the dial-in limits: the notices show on the screen by themselves, and ERR LIMIT
        ends the call with its reason."""
        await self.account("ann")
        self.srv.settings["dialin.call_mins"] = "10"
        with mock.patch.object(dialin, "WATCH_SECS", 0.2):
            c = await self.call(header())
            await self.telnet_signin(c)
            s = next(s for s in self.srv.sessions if s.player and s.player.name == "ann")
            self.assertEqual(s.cls, A.DIALIN)
            self.clock.at += 5 * 60 + 1
            await c.until("This call ends in 5 minutes.")
            self.clock.at += 4 * 60
            await c.until("This call ends in 1 minute.")
            self.clock.at += 60
            got = await c.closed()
        self.assertIn(b"This call has had its 10 minutes.", got)
        await asyncio.sleep(0.1)
        self.assertEqual(self.srv.allowance.meters, set())
        self.assertGreaterEqual(self.srv.allowance.used_secs("2026-11", A.DIALIN, "ann"), 9 * 60)

    async def test_telnet_pool_used_up(self):
        self.srv.allowance.add(A.DIALIN, "bob", self.clock.at - 3 * 3600, self.clock.at - 30 * 60)
        c = await self.call(header())
        got = await c.closed(secs=self.WINDOW + 2)
        self.assertIn(b"The parlor has used its 150 dial-in minutes this month.", got)
        self.assertNotIn(b"Login", got)


@slow
class Dialin(Calls, unittest.IsolatedAsyncioTestCase):
    """The window as built: 5 seconds."""

    WINDOW = dialin.WINDOW_SECS
    EARLY, LATE, TYPING = 0.2, 4.9, 1.0


class Quick(Calls, unittest.IsolatedAsyncioTestCase):
    """The same with a window of 1 second, for the fast suite."""

    WINDOW = 1.0
    EARLY, LATE, TYPING = 0.05, 0.85, 0.2


if __name__ == "__main__":
    unittest.main()
