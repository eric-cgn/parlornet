"""The public port's start (docs/protocol.md, Connecting): the client's opening first, PARLORNET/1 and its
line ending, which sets the connection's; lines in and out use that ending only. Real sockets, an in-process
server."""
import asyncio
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from parlord import core as C  # noqa: E402

GREETING = b"PARLORNET/1 test AUTH=none SIGNUP=none"
OPEN = b"PARLORNET/1"
WRONG = b"ERR PROTOCOL wrong protocol; see http://docs.parlornet.org/docs/protocol.html\r\n"
WRONG_VERSION = b"ERR VERSION wrong version; see http://docs.parlornet.org/docs/protocol.html\r\n"


class Start(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = (C.Limits.first_line_secs, C.Limits.ending_secs)
        self.env = os.environ.pop("PARLOR_DOCS_URL", None)
        self.srv = C.Server(os.path.join(ROOT, "plugins"), name="test", auth="none")
        self.srv.default_motd = "Welcome.\nTwo lines."
        self.server = await asyncio.start_server(self.srv.handle, "127.0.0.1", 0, limit=1024)
        self.port = self.server.sockets[0].getsockname()[1]
        self.open = []

    async def asyncTearDown(self):
        for w in self.open:
            w.close()
        self.server.close()
        C.Limits.first_line_secs, C.Limits.ending_secs = self.saved
        if self.env is not None:
            os.environ["PARLOR_DOCS_URL"] = self.env

    async def connect(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        self.open.append(w)
        return r, w

    async def read_for(self, r, secs=0.6):
        """Everything that comes within secs (or until the server closes)."""
        out = b""
        end = time.monotonic() + secs
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return out
            try:
                chunk = await asyncio.wait_for(r.read(4096), left)
            except asyncio.TimeoutError:
                return out
            if not chunk:
                return out
            out += chunk

    async def closed(self, r, secs=2.0):
        """What came before the server closed the connection (fails if it stays open)."""
        out = b""
        while True:
            chunk = await asyncio.wait_for(r.read(4096), secs)
            if not chunk:
                return out
            out += chunk

    async def start(self, first, then=None, gap=0.0):
        """Send first (and then, gap seconds later); returns (reader, writer, greeting bytes, secs from the
        first byte to the greeting)."""
        r, w = await self.connect()
        t0 = time.monotonic()
        w.write(first)
        if then is not None:
            await asyncio.sleep(gap)
            w.write(then)
        got = await asyncio.wait_for(r.readuntil(GREETING), 3)
        return r, w, got, time.monotonic() - t0

    @staticmethod
    async def upto(r, marker):
        """Everything up to and including marker."""
        return await asyncio.wait_for(r.readuntil(marker), 2)

    def test_defaults(self):
        self.assertEqual(C.Limits.first_line_secs, 20.0)
        self.assertEqual(C.Limits.ending_secs, 0.25)

    # ---- the four endings, each pair against its single byte after 250 ms ----

    async def check_ending(self, first, then, gap, eol):
        r, w, got, secs = await self.start(first, then, gap)
        self.assertEqual(got, GREETING)                         # nothing before the greeting
        w.write(b"MOTD" + eol)
        out = await self.upto(r, b"lines." + eol + b"OK" + eol)
        self.assertTrue(out.startswith(eol + b"MOTD Welcome." + eol), out)    # the greeting ends with the client's ending
        lines = out[len(eol):].split(eol)
        self.assertEqual(lines[-1], b"")                        # ends with the ending
        self.assertEqual(lines[1:], [b"MOTD Two lines.", b"OK", b""])
        for l in lines[:-1]:
            self.assertNotIn(b"\r", l)
            self.assertNotIn(b"\n", l)
            self.assertLessEqual(len(l) + len(eol), 80)
        return secs

    async def test_cr_lf(self):
        await self.check_ending(OPEN + b"\r\n", None, 0, b"\r\n")

    async def test_cr_alone_after_250ms(self):
        secs = await self.check_ending(OPEN + b"\r", None, 0, b"\r")
        self.assertGreaterEqual(secs, 0.2)                      # it waited for an LF that never came
        self.assertLess(secs, 1.5)

    async def test_lf_cr(self):
        await self.check_ending(OPEN + b"\n\r", None, 0, b"\n\r")

    async def test_lf_alone_after_250ms(self):
        secs = await self.check_ending(OPEN + b"\n", None, 0, b"\n")
        self.assertGreaterEqual(secs, 0.2)                      # an LF first waits for a CR as well
        self.assertLess(secs, 1.5)

    async def test_pair_in_two_writes_within_250ms(self):
        await self.check_ending(OPEN + b"\r", b"\n", 0.05, b"\r\n")
        await self.check_ending(OPEN + b"\n", b"\r", 0.05, b"\n\r")

    async def test_opening_in_pieces(self):
        r, w = await self.connect()
        for piece in (b"\r\n", b"PARL", b"ORNET", b"/", b"1", b"\r\n"):
            w.write(piece)
            await asyncio.sleep(0.05)
        self.assertEqual(await asyncio.wait_for(r.readline(), 2), GREETING + b"\r\n")

    async def test_second_byte_too_late_is_a_single_byte_ending(self):
        """CR, then LF after 250 ms: the ending is CR, and the LF is a line with the wrong ending."""
        for first, late, name in ((b"\r", b"\n", b"CR"), (b"\n", b"\r", b"LF")):
            r, w, got, _ = await self.start(OPEN + first)
            self.assertEqual(await r.readexactly(1), first)
            await asyncio.sleep(0.1)
            w.write(late + b"BYE" + first)
            self.assertEqual(await self.closed(r), b"ERR NEWLINE lines end with " + name + b" here" + first
                             + b"OK" + first)

    async def test_a_request_right_after_the_opening_is_the_next_line(self):
        for first, eol in ((b"PARLORNET/1\rHELO ann\r", b"\r"), (b"PARLORNET/1\nHELO ann\n", b"\n"),
                           (b"PARLORNET/1\r\nHELO ann\r\n", b"\r\n")):
            r, w, got, _ = await self.start(first)
            self.assertEqual(await self.upto(r, b"OK -" + eol), eol + b"OK -" + eol, first)

    async def test_empty_lines_after_the_opening_are_ignored(self):
        r, w, got, _ = await self.start(OPEN + b"\r\r")          # CR alone, then an empty CR line
        w.write(b"BYE\r")
        self.assertEqual(await self.closed(r), b"\rOK\r")

    # ---- what may come before the opening, and its case ----

    async def test_up_to_ten_bytes_before_the_opening_are_ignored(self):
        for junk in (b"\r", b"\r\n", b"ATZ\r\nOK\r\n", b"0123456789", b"\xff\xfb\x01\x00 PARL\r", b"PARLORNET\r"):
            r, w, got, _ = await self.start(junk + OPEN + b"\r\n")
            self.assertEqual(got, GREETING, junk)
            w.write(b"BYE\r\n")
            self.assertEqual(await self.closed(r), b"\r\nOK\r\n", junk)

    async def test_eleven_bytes_before_the_opening_are_refused(self):
        r, w = await self.connect()
        t0 = time.monotonic()
        w.write(b"01234567890" + OPEN + b"\r\n")
        self.assertEqual(await self.closed(r), WRONG)
        self.assertLess(time.monotonic() - t0, 0.5)             # at once, not at the time limit

    async def test_any_case(self):
        for opening in (b"parlornet/1", b"ParlorNet/1", b"pArLoRnEt/1"):
            r, w, got, _ = await self.start(opening + b"\r\n")
            self.assertEqual(got, GREETING, opening)

    # ---- the wrong protocol: ERR, then the close ----

    async def refused(self, first, expect):
        r, w = await self.connect()
        t0 = time.monotonic()
        w.write(first)
        out = await self.closed(r)
        self.assertEqual(out, expect, first)
        self.assertLess(time.monotonic() - t0, 0.5, first)     # at once
        self.assertLessEqual(len(out), 80)
        return out

    async def test_http_is_refused(self):
        await self.refused(b"GET / HTTP/1.0\r\n\r\n", WRONG)
        await self.refused(b"GET /protocols.htm HTTP/1.1\r\nHost: parlor\r\n\r\n", WRONG)

    async def test_telnet_negotiation_is_refused(self):
        iac = b"\xff\xfd\x03\xff\xfb\x18\xff\xfb\x1f\xff\xfb\x20"        # DO SGA, WILL TTYPE, NAWS, TSPEED
        await self.refused(iac, WRONG)
        await self.refused(b"\xff\xfb\x01\xff\xfd\x01\xff\xfb\x18\xff\xfd", WRONG)       # 11 bytes
        r, w, got, _ = await self.start(b"\xff\xfb\x01\xff\xfd\x01" + OPEN + b"\r\n")   # 6 bytes, then the opening
        self.assertEqual(got, GREETING)

    async def test_other_text_is_refused(self):
        for text in (b"hello there, parlor\r\n", b"HELO ann\r\n\r\n\r\n", b"PARLORNET/1 HELO\r\n", b"PARLORNET 1\r\n",
                     b"x" * 200):
            await self.refused(text, WRONG)

    async def test_anything_but_cr_or_lf_after_the_1_is_refused(self):
        for text in (b"PARLORNET/1 \r\n", b"PARLORNET/1.0\r\n", b"PARLORNET/1HELO\r\n", b"PARLORNET/1\t",
                     b"PARLORNET/\r\n", b"PARLORNET/x\r\n"):
            await self.refused(text, WRONG)

    async def test_another_version_is_refused(self):
        for text in (b"PARLORNET/2\r\n", b"parlornet/0\r", b"PARLORNET/10\r\n", b"PARLORNET/2", b"\r\nPARLORNET/3\n"):
            await self.refused(text, WRONG_VERSION)

    async def test_error_lines_fit_and_end_with_cr_lf(self):
        for line in (WRONG, WRONG_VERSION):
            self.assertTrue(line.endswith(b"\r\n"))
            self.assertLessEqual(len(line), 80)

    async def test_the_docs_address_is_the_operators(self):
        self.srv.settings["site.docs_url"] = "http://example.org/pn"          # the / is added
        await self.refused(b"GET / HTTP/1.0\r\n\r\n",
                           b"ERR PROTOCOL wrong protocol; see http://example.org/pn/docs/protocol.html\r\n")

    async def test_no_docs_address_when_cleared(self):
        self.srv.settings["site.docs_url"] = ""
        await self.refused(b"GET / HTTP/1.0\r\n\r\n", b"ERR PROTOCOL wrong protocol\r\n")
        await self.refused(b"PARLORNET/2\r\n", b"ERR VERSION wrong version\r\n")

    async def test_no_docs_address_when_it_does_not_fit(self):
        self.srv.settings["site.docs_url"] = "http://docs.example.org/a-long-path-for-the-parlor-docs/"
        await self.refused(b"GET / HTTP/1.0\r\n\r\n", b"ERR PROTOCOL wrong protocol\r\n")
        self.srv.settings["site.docs_url"] = "http://docs.example.org/xxxx/"     # ERR VERSION: 78 bytes, just fits
        line = await self.refused(b"PARLORNET/9\r\n", b"ERR VERSION wrong version; see "
                                  b"http://docs.example.org/xxxx/docs/protocol.html\r\n")
        self.assertEqual(len(line), 80)

    async def test_a_client_that_closes_first_gets_nothing(self):
        r, w = await self.connect()
        w.write(b"PARLOR")
        w.write_eof()
        self.assertEqual(await self.closed(r), b"")

    # ---- the time limit ----

    async def test_the_time_limit_for_the_opening(self):
        C.Limits.first_line_secs = 0.5                          # the rule, shortened for the test
        for first in (b"", b"\r\n", b"PARLORNET/", b"PARLORNET/1"):
            r, w = await self.connect()
            t0 = time.monotonic()
            w.write(first)
            self.assertEqual(await self.closed(r, 3), WRONG, first)
            self.assertGreaterEqual(time.monotonic() - t0, 0.45)
            self.assertLess(time.monotonic() - t0, 2.0)

    async def test_an_opening_in_time_is_enough(self):
        C.Limits.first_line_secs = 0.5
        r, w = await self.connect()
        await asyncio.sleep(0.3)
        w.write(OPEN + b"\r\n")
        self.assertEqual(await asyncio.wait_for(r.readline(), 2), GREETING + b"\r\n")
        await asyncio.sleep(0.4)                                # past the start's limit: no longer applies
        w.write(b"BYE\r\n")
        self.assertEqual(await self.closed(r), b"OK\r\n")

    # ---- after the start: exactly that ending ----

    async def test_a_mismatched_ending_is_refused_and_the_connection_stays(self):
        cases = ((b"\r\n", b"GAM\n", b"CR LF"), (b"\r\n", b"GAM\r\r\n", b"CR LF"), (b"\n", b"GAM\r\n", b"LF"),
                 (b"\r", b"GAM\n", b"CR"), (b"\n\r", b"GAM\r\n\r", b"LF CR"))
        for first, bad, name in cases:
            r, w, _, _ = await self.start(OPEN + first)
            eol = await r.readexactly(len(first))
            self.assertEqual(eol, first)
            w.write(bad + b"HELO ann" + eol)
            out = await self.upto(r, b"OK -" + eol)
            # refused, not carried out (no GAM lines), and the connection goes on
            self.assertEqual(out, b"ERR NEWLINE lines end with " + name + b" here" + eol + b"OK -" + eol, (first, bad))

    async def test_lines_with_the_ending_work_and_empty_lines_are_ignored(self):
        r, w, _, _ = await self.start(OPEN + b"\n\r")
        w.write(b"\n\rHELO ann\n\r\n\rLST\n\rBYE\n\r")
        self.assertEqual(await self.closed(r), b"\n\rOK -\n\rOK\n\rOK\n\r")

    async def test_too_long_still_closes(self):
        r, w, _, _ = await self.start(OPEN + b"\r\n")
        w.write(b"X" * 200 + b"\r\n")
        self.assertEqual(await self.closed(r), b"\r\nERR TOOLONG lines are 80 bytes at most\r\n")


if __name__ == "__main__":
    unittest.main()
