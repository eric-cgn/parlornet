"""Dial-in gateways and the time allowance (docs/protocol.md, Dial-in gateways and Time allowance;
parlord/native.py and parlord/allowance.py): the PROXY v2 header (good, cut short, from an address that is not a
gateway, with a wrong signature), the classes of connections, the minutes charged across a month boundary, the
smallest limit, TIM's left=, the notices at 5 minutes and 1 minute, ERR LIMIT and the close.

    python3 -m unittest discover -s tests -p "test_allowance.py"
"""
import asyncio
import calendar
import os
import struct
import sys
import unittest
from unittest import mock

from test_core import ROOT, make_keys

sys.path.insert(0, ROOT)
from adapters.common import CoreClient  # noqa: E402
from parlord import allowance as A  # noqa: E402
from parlord import admin, frontends, native  # noqa: E402
from parlord.core import MAXLINE, Server  # noqa: E402
from parlord.store import Store  # noqa: E402

NOV_30 = calendar.timegm((2026, 11, 30, 23, 50, 0))     # ten minutes before December, UTC
GATEWAY = "127.0.0.1"


def header(prior=None, cmd=1, family=0x11, tlvs=b"", size=None):
    """A PROXY v2 header: a call from 192.0.2.7 to the parlor, its caller ID and modulation, and TLV_PRIOR's
    seconds when prior is given. size: a length field other than the true one."""
    addrs = {0x11: bytes([192, 0, 2, 7, 192, 0, 2, 1]) + struct.pack(">HH", 40000, 6501), 0x00: b"",
             0x21: bytes(15) + b"\x01" + bytes(16) + struct.pack(">HH", 1, 2)}[family]
    body = addrs + b"\xe0\x00\x045551" + b"\xe2\x00\x04V.34" + tlvs
    if prior is not None:
        body += b"\xe4\x00\x04" + struct.pack(">I", prior)
    return native.PROXY_SIG + bytes([0x20 | cmd, family]) + struct.pack(">H", len(body) if size is None
                                                                          else size) + body


def reader_of(data, eof=True):
    r = asyncio.StreamReader()
    r.feed_data(data)
    if eof:
        r.feed_eof()
    return r


class Header(unittest.IsolatedAsyncioTestCase):
    """Lines.proxy: the header itself, before any server."""

    async def proxy(self, data, trusted=True, eof=True, secs=1.0):
        lines = native.Lines(reader_of(data, eof))
        return lines, await lines.proxy(secs, trusted)

    async def test_good(self):
        lines, h = await self.proxy(header(prior=12) + b"PARLORNET/1\r\nHELO ann\r\n")
        self.assertFalse(h.local)
        self.assertEqual((h.src, h.prior), ("192.0.2.7", 12))
        self.assertEqual(h.call, {"caller": "5551", "modulation": "V.34"})
        self.assertEqual(await lines.start(1.0, 0.1), b"\r\n")        # the opening comes after it
        self.assertEqual(await lines.line(1.0), (b"HELO ann", True))

    async def test_unspecified_ipv6_and_local(self):
        _, h = await self.proxy(header(family=0x00) + b"PARLORNET/1\r")
        self.assertEqual((h.local, h.src, h.prior), (False, None, 0))
        _, h = await self.proxy(header(family=0x21) + b"PARLORNET/1\r")
        self.assertEqual(h.src, "::1")
        _, h = await self.proxy(header(cmd=0) + b"PARLORNET/1\r")
        self.assertTrue(h.local)

    async def test_no_header(self):
        for opening in (b"PARLORNET/1\r\n", b"\r\nPARLORNET/1\r\n", b"\r\n\r\nPARLORNET/1\r\n"):
            lines, h = await self.proxy(opening, trusted=False)
            self.assertIsNone(h)
            self.assertEqual(await lines.start(1.0, 0.1), b"\r\n")     # the bytes stay for the opening

    async def test_cut_short(self):
        whole = header(prior=12)
        for cut in (12, 15, 20, len(whole) - 1):
            with self.assertRaises(native.BadOpening):              # the client closed in the middle of it
                await self.proxy(whole[:cut])
            with self.assertRaises(asyncio.TimeoutError):           # the rest never came
                await self.proxy(whole[:cut], eof=False, secs=0.2)

    async def test_not_a_gateway(self):
        with self.assertRaises(native.BadOpening) as e:
            await self.proxy(header() + b"PARLORNET/1\r\n", trusted=False)
        self.assertEqual(e.exception.code, "PROTOCOL")
        with self.assertRaises(native.BadOpening):                  # the signature alone is enough
            await self.proxy(native.PROXY_SIG, trusted=False, eof=False)

    async def test_wrong_signature(self):
        bad = bytearray(header())
        bad[8] = ord("X")                                           # QXIT
        lines, h = await self.proxy(bytes(bad) + b"PARLORNET/1\r\n")
        self.assertIsNone(h)                                        # not a header: the opening fails instead
        with self.assertRaises(native.BadOpening):
            await lines.start(1.0, 0.1)

    async def test_malformed(self):
        for data in (header()[:12] + b"\x11" + header()[13:],      # version 1
                     header()[:12] + b"\x22" + header()[13:],      # command 2
                     header()[:13] + b"\x12" + header()[14:],      # UDP
                     header(size=600),                              # longer than PROXY_MOST
                     header(tlvs=b"\xe4\x00\x02\x00\x01"),         # TLV_PRIOR of 2 bytes
                     header(tlvs=b"\xe3\x00\x09ab"),               # a TLV longer than what is left
                     header(tlvs=b"\xe3\x00")):                     # half a TLV
            with self.assertRaises(native.BadOpening, msg=data):
                await self.proxy(data + b"PARLORNET/1\r\n")
        _, h = await self.proxy(header(tlvs=b"\x05\x00\x02ok") + b"PARLORNET/1\r")
        self.assertIsNotNone(h)                                     # an unknown TLV is passed over


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


class Accounting(unittest.TestCase):
    """Allowance on its own, with a clock of its own."""

    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.db.close)
        self.settings = {}
        self.clock = Clock(NOV_30)
        self.a = A.Allowance(self.settings, self.store, self.clock)

    def test_across_a_month_boundary(self):
        m = self.a.open(A.DIALIN, prior=120)            # up two minutes before the gateway connected
        self.clock.at += 60
        self.a.sign(m, "Ann")                           # signs in a minute later: 3 minutes so far, pool only
        self.clock.at += 19 * 60                        # 19 minutes signed in, 9 in November and 10 in December
        self.a.charge(m)
        self.assertEqual(self.a.used_secs("2026-11", A.DIALIN), 12 * 60)
        self.assertEqual(self.a.used_secs("2026-11", A.DIALIN, "ann"), 9 * 60)
        self.assertEqual(self.a.used_secs("2026-12", A.DIALIN), 10 * 60)
        self.assertEqual(self.a.used_secs("2026-12", A.DIALIN, "ann"), 10 * 60)
        # the store has the same, so a restart keeps it
        again = A.Allowance(self.settings, self.store, self.clock)
        self.assertEqual(again.used_secs("2026-12", A.DIALIN, "ann"), 10 * 60)
        self.assertEqual(again.used_secs("2026-11", A.DIALIN), 12 * 60)
        # in December, ann's month is new: 60 minutes less the 10 so far
        self.assertEqual(self.a.left(m), (50 * 60, "user"))

    def test_signing_out_ends_the_players_charge_not_the_calls(self):
        m = self.a.open(A.DIALIN)
        self.a.sign(m, "ann")
        self.clock.at += 5 * 60
        self.a.sign(m, None)
        self.clock.at += 5 * 60
        self.a.close(m)
        self.assertEqual(self.a.used_secs("2026-11", A.DIALIN, "ann"), 5 * 60)
        self.assertEqual(self.a.used_secs("2026-11", A.DIALIN), 10 * 60)
        self.assertEqual(self.a.meters, set())

    def test_the_smallest_limit(self):
        m = self.a.open(A.DIALIN)
        self.assertEqual(self.a.left(m), (75 * 60, "call"))            # nobody signed in: the call's
        self.a.sign(m, "ann")
        self.assertEqual(self.a.left(m), (60 * 60, "user"))            # ann has 60 this month
        self.a.add(A.DIALIN, None, NOV_30 - 100 * 60, NOV_30 - 100 * 60 + 110 * 60)   # 110 of the pool's 150 used
        self.assertEqual(self.a.left(m), (40 * 60, "pool"))
        other = self.a.open(A.DIALIN)                                   # another call draws on the pool too
        self.clock.at += 60
        self.assertEqual(self.a.left(m), (38 * 60, "pool"))
        self.assertEqual(self.a.rate(m, "pool"), 2)
        self.a.close(other)
        self.settings.update({"dialin.pool_month_mins": "0", "dialin.user_month_mins": "0"})   # 0: no limit
        self.assertEqual(self.a.left(m), (74 * 60, "call"))
        self.settings["dialin.call_mins"] = "0"
        self.assertEqual(self.a.left(m), (None, None))
        self.settings["dialin.call_mins"] = "many"                     # not a number: the default
        self.assertEqual(self.a.left(m)[1], "call")

    def test_settings_from_the_environment(self):
        with mock.patch.dict(os.environ, {"PARLOR_DIALIN_CALL_MINS": "30", "PARLOR_DEFAULT_CALL_MINS": "45",
                                          "PARLOR_DIALIN_GATEWAYS": "10.9.0.0/16"}):
            self.assertEqual(self.a.limits(A.DIALIN), (30 * 60, 60 * 60, 150 * 60))
            self.assertEqual(self.a.limits(A.DEFAULT), (45 * 60, None, None))
            self.assertTrue(self.a.trusted(native.ipaddress.ip_address("10.9.8.7")))
            self.settings["dialin.call_mins"] = "20"                    # the store and the file come first
            self.assertEqual(self.a.limits(A.DIALIN)[0], 20 * 60)
        self.assertEqual(self.a.limits(A.DEFAULT), (None, None, None))  # the default class has none
        self.assertIsNone(self.a.open(A.DEFAULT))                       # so its connections are not metered
        self.assertFalse(self.a.trusted(native.ipaddress.ip_address("10.9.8.7")))   # no gateways: off

    def test_old_months_are_forgotten(self):
        self.a.add(A.DIALIN, "ann", NOV_30 - 70 * 86400, NOV_30 - 70 * 86400 + 60)   # September
        self.a.add(A.DIALIN, "ann", NOV_30 - 40 * 86400, NOV_30 - 40 * 86400 + 60)   # October
        self.a.forget_old(NOV_30)
        self.assertEqual(self.store.allowance_used("2026-09", A.DIALIN), [])
        self.assertEqual(len(self.store.allowance_used("2026-10", A.DIALIN)), 2)

    def test_words(self):
        m = self.a.open(A.DIALIN)
        self.assertEqual(self.a.notice(m, "call", 300), "This call ends in 5 minutes.")
        self.assertEqual(self.a.notice(m, "user", 60), "Your dial-in time this month ends in 1 minute.")
        self.assertEqual(self.a.notice(m, "pool", 200), "The parlor's dial-in time this month ends in 4 minutes.")
        self.assertEqual(self.a.reason(m, "call"), "closing: this call has had its 75 minutes")
        self.assertEqual(self.a.reason(m, "user"), "closing: you have used your 60 dial-in minutes this month")
        self.assertEqual(self.a.reason(m, "pool"), "closing: the parlor has used its 150 dial-in minutes this month")
        self.settings["default.call_mins"] = "45"
        d = self.a.open(A.DEFAULT)
        self.assertEqual(self.a.notice(d, "call", 300), "This connection ends in 5 minutes.")
        self.assertEqual(self.a.reason(d, "call"), "closing: this connection has had its 45 minutes")
        self.settings.update({k: str(A.MOST_MINS) for k in A.setting_keys() if k != A.GATEWAYS})
        for which in ("call", "user", "pool"):
            self.assertLessEqual(len("ERR LIMIT " + self.a.reason(m, which)) + 2, MAXLINE)
            self.assertLessEqual(len("INF " + self.a.notice(m, which, A.MOST_MINS * 60)) + 2, MAXLINE)

    def test_admin_settings(self):
        self.assertEqual(admin.SETTINGS["dialin.call_mins"]("30"), "30")
        self.assertEqual(admin.SETTINGS["dialin.call_mins"]("0"), "0")          # no limit
        self.assertEqual(admin.SETTINGS["default.pool_month_mins"](""), "")     # the default
        for bad in ("-1", "x", str(A.MOST_MINS + 1)):
            with self.assertRaises(ValueError):
                admin.SETTINGS["dialin.user_month_mins"](bad)
        self.assertEqual(admin.SETTINGS["dialin.gateways"]("10.0.0.1, 10.1.0.0/16"), "10.0.0.1,10.1.0.0/16")
        with self.assertRaises(ValueError):
            admin.SETTINGS["dialin.gateways"]("gateway.example.org")


class Public:
    """One public-port connection: request -> (data lines, final line)."""

    def __init__(self, r, w):
        self.r, self.w = r, w

    async def read(self):
        return (await asyncio.wait_for(self.r.readline(), 10)).decode().rstrip("\r\n")

    async def req(self, line):
        self.w.write(line.encode() + b"\r\n")
        data = []
        while True:
            got = await self.read()
            if got.startswith(("OK", "ERR", "ASK")):
                return data, got
            data.append(got)


class Served(unittest.IsolatedAsyncioTestCase):
    """The core: who is in which class, TIM's left=, the notices and the close."""

    async def asyncSetUp(self):
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "none")
        self.srv.settings["dialin.gateways"] = GATEWAY
        self.clock = Clock(NOV_30 - 86400)
        self.srv.allowance.clock = self.clock
        self.pub = await asyncio.start_server(self.srv.handle, "127.0.0.1", 0)
        self.held = []

    async def asyncTearDown(self):
        for c in self.held:
            c.w.close()
        self.pub.close()

    async def connect(self, first=b""):
        r, w = await asyncio.open_connection("127.0.0.1", self.pub.sockets[0].getsockname()[1])
        w.write(first + b"PARLORNET/1\r\n")
        c = Public(r, w)
        self.held.append(c)
        c.greeting = await c.read()
        return c

    def newest(self):
        return max(self.srv.sessions, key=lambda s: s.opened)

    async def tim_left(self, c):
        data, final = await c.req("TIM")
        self.assertEqual(final, "OK")
        tim = [l for l in data if l.startswith("TIM ")]
        self.assertLessEqual(len(tim[0]) + 2, MAXLINE)
        self.assertEqual([f.split("=")[0] for f in tim[0].split()[3:]], ["left", "call", "month"])
        self.fields = tim[0].split()[4:]
        return tim[0].split()[3], [l for l in data if l.startswith("INF ")]

    async def test_classes(self):
        c = await self.connect(header(prior=5 * 60))
        self.assertTrue(c.greeting.startswith("PARLORNET/1 test"))
        s = self.newest()
        self.assertEqual((s.cls, s.source, s.call["caller"]), (A.DIALIN, "192.0.2.7", "5551"))
        self.assertEqual(await self.tim_left(c), ("left=%d" % (70 * 60), []))      # the call's 5 minutes count
        self.assertEqual(self.fields, ["call=%d" % (70 * 60), "month=unl"])         # nobody signed in yet
        d = await self.connect()                                       # the gateway without a header
        self.assertEqual((self.newest().cls, self.newest().meter), (A.DEFAULT, None))
        self.assertEqual(await self.tim_left(d), ("left=unl", []))
        await self.connect(header(cmd=0))                              # LOCAL: the gateway's own connection
        self.assertEqual((self.newest().cls, self.newest().source), (A.DEFAULT, GATEWAY))

    async def test_a_forged_header(self):
        for gateways in ("10.0.0.1", ""):                              # another address; none, the feature off
            self.srv.settings["dialin.gateways"] = gateways
            c = await self.connect(header())
            self.assertTrue(c.greeting.startswith("ERR PROTOCOL wrong protocol"), c.greeting)
            self.assertEqual(await c.r.read(), b"")                    # and closed
        self.srv.settings["dialin.gateways"] = GATEWAY
        c = await self.connect(header(size=3))                         # a gateway's malformed header
        self.assertTrue(c.greeting.startswith("ERR PROTOCOL"), c.greeting)

    async def test_the_extension_channel_has_no_limit(self):
        keys = make_keys("web")
        self.srv.keyring = frontends.Keyring(keys)
        self.srv.settings["default.call_mins"] = "10"
        fe = await asyncio.start_server(self.srv.handle_frontend, "127.0.0.1", 0)
        x = await CoreClient.connect("127.0.0.1", fe.sockets[0].getsockname()[1], os.path.join(keys, "web.key"))
        await x.req("HELO ann")
        data, _ = await x.req("TIM")
        self.assertTrue(data[0].endswith(" left=unl call=unl month=unl"), data)
        x.close()
        fe.close()
        d = await self.connect()                                       # on the public port the default class has it
        self.assertEqual(await self.tim_left(d), ("left=600", []))

    async def test_notices_and_the_close(self):
        self.srv.settings["dialin.call_mins"] = "10"
        c = await self.connect(header())
        self.assertEqual((await c.req("HELO ann"))[1], "OK -")
        self.clock.at += 5 * 60 + 1
        self.assertEqual(await self.tim_left(c), ("left=299", ["INF This call ends in 5 minutes."]))
        self.assertEqual(await self.tim_left(c), ("left=299", []))                 # once
        self.assertEqual(self.fields, ["call=299", "month=%d" % (60 * 60 - 301)])
        self.clock.at += 4 * 60 - 1
        data, final = await c.req("LST")                                           # any reply carries it
        self.assertEqual((data[0], final), ("INF This call ends in 1 minute.", "OK"))
        self.clock.at += 61
        _, final = await c.req("TIM")
        self.assertEqual(final, "ERR LIMIT closing: this call has had its 10 minutes")
        self.assertEqual(await c.r.read(), b"")
        self.assertEqual(self.srv.allowance.used_secs("2026-11", A.DIALIN, "ann"), 10 * 60 + 1)

    async def test_closed_without_a_request(self):
        """The time runs out while the client waits: ERR LIMIT comes by itself, and the connection closes."""
        c = await self.connect(header(prior=75 * 60 - 1))
        await asyncio.sleep(0.1)
        self.clock.at += 2
        self.assertEqual(await c.read(), "ERR LIMIT closing: this call has had its 75 minutes")
        self.assertEqual(await c.r.read(), b"")

    def test_the_longest_tim_fits(self):
        line = "TIM %d 2026-11-24T21:05:33Z left=%d call=%d month=%d" % ((2 ** 32,) + (A.MOST_MINS * 60,) * 3)
        self.assertLessEqual(len(line) + 2, MAXLINE)

    async def test_the_pool_is_empty(self):
        self.srv.allowance.add(A.DIALIN, "bob", self.clock.at - 3 * 3600, self.clock.at - 30 * 60)
        c = await self.connect(header())
        self.assertTrue(c.greeting.startswith("PARLORNET/1 "))
        self.assertEqual(await c.read(), "ERR LIMIT closing: the parlor has used its 150 dial-in minutes this month")
        self.assertEqual(await c.r.read(), b"")
        d = await self.connect()                                       # the default class is not touched
        self.assertEqual(await self.tim_left(d), ("left=unl", []))

    async def test_the_players_month_is_used_up(self):
        self.srv.allowance.add(A.DIALIN, "ann", self.clock.at - 2 * 3600, self.clock.at - 3600)
        c = await self.connect(header())
        with mock.patch.object(A, "GRACE_SECS", 0.5):
            data, final = await c.req("HELO ann")
        self.assertEqual(final, "OK -")                                # ann may sign in, and is told
        self.assertEqual(data, ["INF You have used your 60 dial-in minutes this month.",
                                "INF This call closes in 0.5 seconds."])
        self.assertEqual(await c.read(), "ERR LIMIT closing: you have used your 60 dial-in minutes this month")
        self.assertEqual(await c.r.read(), b"")

    async def test_the_month_runs_out_during_the_call(self):
        self.srv.allowance.add(A.DIALIN, "ann", self.clock.at - 2 * 3600, self.clock.at - 2 * 3600 + 58 * 60)
        c = await self.connect(header())
        _, final = await c.req("HELO ann")
        self.assertEqual(final, "OK -")
        self.assertEqual(await self.tim_left(c),                       # under 5 minutes: told at once
                         ("left=120", ["INF Your dial-in time this month ends in 2 minutes."]))
        self.assertEqual(self.fields, ["call=%d" % (75 * 60), "month=120"])
        self.clock.at += 61
        self.assertEqual(await self.tim_left(c),
                         ("left=59", ["INF Your dial-in time this month ends in 1 minute."]))
        self.assertEqual((await c.req("OUT"))[1], "OK 1")              # signed out: the call goes on
        self.clock.at += 120
        self.assertEqual(await self.tim_left(c), ("left=%d" % (75 * 60 - 181), []))
        self.assertEqual(self.fields, ["call=%d" % (75 * 60 - 181), "month=unl"])    # signed out: no month
        self.assertEqual((await c.req("HELO bob"))[1], "OK -")
        _, final = await c.req("HELO x")                               # another request, still open
        self.assertTrue(final.startswith("ERR STATE"), final)

    async def test_deleting_the_account_forgets_its_minutes(self):
        c = await self.connect(header())
        await c.req("HELO ann")
        self.clock.at += 60
        self.srv.allowance.charge_all()
        self.clock.at += 60
        await self.srv.delete_player(self.srv.player("ann"))          # while ann is still on the call
        self.assertEqual(await c.r.read(), b"")                        # which closes
        await asyncio.sleep(0.1)
        self.assertEqual(self.srv.allowance.meters, set())
        self.assertEqual(self.srv.allowance.used_secs("2026-11", A.DIALIN, "ann"), 0)
        self.assertEqual(self.srv.store.allowance_used("2026-11", A.DIALIN), [("", 120.0)])


if __name__ == "__main__":
    unittest.main()
