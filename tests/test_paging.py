"""Paged replies (docs/protocol.md, Paged replies): PAG bytes and CNT on the public port. A page is whole lines
within the budget, each counted with its line ending, and the CNT line counts too. CNT brings the next page,
and the last one ends with the reply's own final line. Any other request drops the rest, and CNT with nothing
waiting is ERR STATE. PAG has bounds, and nothing is paged without it. A long LST and a long VUE page correctly.
The reply to every request that changes something fits one page at the fewest bytes. ERR IDLE and ERR LIMIT
close a connection while a reply waits, and the time allowance's notices count toward the page. The extension
channel refuses PAG.

    python3 -m unittest discover -s tests -p "test_paging.py"
"""
import asyncio
import calendar
import os
import sys
import unittest
from unittest import mock

from test_allowance import Clock, header
from test_auth import mail_password
from test_core import ROOT, make_keys

sys.path.insert(0, ROOT)
from adapters.common import CoreClient  # noqa: E402
from parlord import allowance as A  # noqa: E402
from parlord import core, frontends, native  # noqa: E402
from parlord.core import Limits, Server, Session  # noqa: E402

MIN = native.PAG_MIN
FINALS = ("OK", "ERR", "ASK", "CNT")
NOV_29 = calendar.timegm((2026, 11, 29, 12, 0, 0))     # a fixed time for the time allowance


class Writer:
    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


def nbytes(lines, eol=b"\r\n"):
    """The bytes a reply's lines take on the wire."""
    return sum(len(l) + len(eol) for l in lines)


class Pub:
    """One public-port connection that reads raw lines, so a page's bytes can be counted."""

    def __init__(self, r, w, eol):
        self.r, self.w, self.eol = r, w, eol

    async def raw(self):
        """The next line as the server sent it, its ending included."""
        if self.eol == b"\r":
            got = await asyncio.wait_for(self.r.readuntil(b"\r"), 10)
        else:
            got = await asyncio.wait_for(self.r.readuntil(self.eol), 10)
        self.assertEnds(got)
        return got

    def assertEnds(self, got):
        if not got.endswith(self.eol) or (self.eol == b"\n" and got.endswith(b"\r\n")):
            raise AssertionError("line %r does not end with %r" % (got, self.eol))

    async def page(self):
        """Lines up to the next final line or CNT: (the lines as text, the bytes they took)."""
        out, size = [], 0
        while True:
            got = await self.raw()
            size += len(got)
            text = got[:-len(self.eol)].decode()
            out.append(text)
            if text.split(" ", 1)[0] in FINALS:
                return out, size

    async def send(self, line):
        self.w.write(line.encode() + self.eol)
        await self.w.drain()

    async def req(self, line):
        """One request: the first page of its reply, as text."""
        await self.send(line)
        return (await self.page())[0]

    async def whole(self, line, most=None):
        """A request and CNT until its reply ends: ([pages], [the bytes of each page])."""
        await self.send(line)
        pages, sizes = [], []
        while True:
            lines, size = await self.page()
            pages.append(lines)
            sizes.append(size)
            if lines[-1] != native.CNT:
                return pages, sizes
            await self.send("CNT")


class Base(unittest.IsolatedAsyncioTestCase):
    signin = "none"

    async def asyncSetUp(self):
        for name, value in (("rate_burst", 10000), ("rate_per_sec", 10000.0)):
            p = mock.patch.object(Limits, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", self.signin)
        await self.srv.load_plugins()
        self.srv.settings["dialin.gateways"] = "127.0.0.1"
        self.pub = await asyncio.start_server(self.srv.handle, "127.0.0.1", 0)
        self.held = []

    async def asyncTearDown(self):
        for c in self.held:
            c.w.close()
        self.pub.close()
        for p in self.srv.plugins.values():
            p.stop()
            if p.proc:
                await p.proc.wait()

    async def connect(self, eol=b"\r\n", first=b"", name=None):
        r, w = await asyncio.open_connection("127.0.0.1", self.pub.sockets[0].getsockname()[1])
        w.write(first + b"PARLORNET/1" + eol)
        c = Pub(r, w, eol)
        self.held.append(c)
        c.greeting = (await c.raw())[:-len(eol)].decode()
        if name:
            self.assertEqual(await c.req("HELO " + name), ["OK -"])
        return c

    async def player(self, name):
        """A player signed in without a connection of its own, for setting tables up."""
        sess = Session(Writer())
        self.assertEqual(await self.srv.dispatch(sess, "HELO " + name), ["OK -"])
        return sess

    async def do(self, sess, line):
        return await self.srv.dispatch(sess, line)

    def check_pages(self, pages, sizes, most, eol=b"\r\n"):
        """Every page but the last ends with CNT and is as full as whole lines make it; every page fits."""
        for i, (lines, size) in enumerate(zip(pages, sizes)):
            self.assertLessEqual(size, most, lines)
            self.assertEqual(size, nbytes(lines, eol))
            if i < len(pages) - 1:
                self.assertEqual(lines[-1], native.CNT)
                nxt = pages[i + 1][0]
                self.assertGreater(size + len(nxt) + len(eol), most, "the next line would have fit")
        self.assertNotEqual(pages[-1][-1], native.CNT)
        self.assertTrue(pages[-1][-1].startswith(("OK", "ERR", "ASK")), pages[-1])

    @staticmethod
    def joined(pages):
        return [l for p in pages for l in (p[:-1] if p[-1] == native.CNT else p)]


class Splitting(unittest.TestCase):
    """native.page on its own."""

    def test_whole_lines_within_the_budget(self):
        lines = ["X" * 98] * 10 + ["OK"]                    # 100 bytes a line with CR LF
        now, rest = native.page(lines, 600, b"\r\n")
        self.assertEqual(now, ["X" * 98] * 5 + ["CNT"])     # 500, and CNT's 5 make 505; a sixth would be 605
        self.assertEqual(rest, ["X" * 98] * 5 + ["OK"])
        now, rest = native.page(rest, 600, b"\r\n")         # 504 bytes left: the last page
        self.assertEqual((now, rest), (["X" * 98] * 5 + ["OK"], None))

    def test_the_cnt_line_counts(self):
        lines = ["X" * 98] * 6 + ["OK"]                     # 604 bytes
        self.assertEqual(native.page(lines, 604, b"\r\n"), (lines, None))      # fits exactly: sent whole
        now, rest = native.page(lines, 603, b"\r\n")
        self.assertEqual(now[-1], "CNT")
        self.assertEqual(len(now), 6)                       # 5 lines and CNT: 6 lines and CNT would be 605
        now, rest = native.page(lines, 605, b"\r\n")
        self.assertEqual(rest, None)

    def test_the_line_ending_counts(self):
        lines = ["X" * 99] * 6 + ["OK"]                     # 100 bytes a line with LF, 101 with CR LF
        self.assertIsNone(native.page(lines, 603, b"\n")[1])
        self.assertEqual(len(native.page(lines, 603, b"\r\n")[0]), 6)

    def test_no_paging(self):
        lines = ["X" * 78] * 100 + ["OK"]
        self.assertEqual(native.page(lines, 0, b"\r\n"), (lines, None))


class Requests(Base):
    async def test_pag_and_its_bounds(self):
        c = await self.connect()                            # before signing in
        for bad in ("PAG", "PAG %d" % (MIN - 1), "PAG %d" % (native.PAG_MAX + 1), "PAG 1000000", "PAG x",
                    "PAG 600 700", "PAG -600"):
            self.assertEqual(await c.req(bad), ["ERR SYNTAX PAG 0, or PAG bytes from 512 to 65535"], bad)
        for good in ("PAG %d" % MIN, "PAG %d" % native.PAG_MAX, "pag 0", "PAG 00600"):
            self.assertEqual(await c.req(good), ["OK"], good)
        self.assertEqual(self.newest().pag, 600)

    def newest(self):
        return max(self.srv.sessions, key=lambda s: s.opened)

    async def test_no_paging_without_pag(self):
        c = await self.connect()
        gam = await c.req("GAM")
        self.assertGreater(nbytes(gam), MIN)
        self.assertNotIn("CNT", gam)
        self.assertEqual(gam[-1], "OK")
        await c.req("PAG %d" % MIN)
        self.assertEqual(await c.req("PAG 0"), ["OK"])      # off again
        self.assertEqual(await c.req("GAM"), gam)

    async def test_pages_continue_to_the_final_line(self):
        for eol in (b"\r\n", b"\n", b"\r", b"\n\r"):
            c = await self.connect(eol)
            whole = await c.req("GAM")
            self.assertEqual(await c.req("PAG %d" % MIN), ["OK"])
            pages, sizes = await c.whole("GAM")
            self.assertGreater(len(pages), 1)
            self.check_pages(pages, sizes, MIN, eol)
            self.assertEqual(self.joined(pages), whole)

    async def test_help_pages(self):
        """CMD /help only shows: its INF lines may page like any other reply."""
        c = await self.connect(name="ann")
        whole = await c.req("CMD /help")
        self.assertGreater(nbytes(whole), MIN)
        await c.req("PAG %d" % MIN)
        pages, sizes = await c.whole("CMD /help")
        self.check_pages(pages, sizes, MIN)
        self.assertEqual(self.joined(pages), whole)

    async def test_another_request_drops_the_rest(self):
        c = await self.connect(name="ann")
        await c.req("PAG %d" % MIN)
        first = await c.req("GAM")
        self.assertEqual(first[-1], "CNT")
        tim = await c.req("TIM")                            # answered as usual
        self.assertTrue(tim[0].startswith("TIM ") and tim[-1] == "OK", tim)
        self.assertEqual(await c.req("CNT"), ["ERR STATE no reply is waiting for CNT"])
        self.assertEqual((await c.req("GAM"))[-1], "CNT")
        self.assertEqual(await c.req("PAG 600"), ["OK"])    # PAG is another request too
        self.assertEqual(await c.req("CNT"), ["ERR STATE no reply is waiting for CNT"])

    async def test_cnt_with_nothing_waiting(self):
        c = await self.connect()
        self.assertEqual(await c.req("CNT"), ["ERR STATE no reply is waiting for CNT"])     # before signing in
        await c.req("HELO ann")
        self.assertEqual(await c.req("CNT"), ["ERR STATE no reply is waiting for CNT"])     # no PAG
        await c.req("PAG %d" % MIN)
        self.assertEqual(await c.req("CNT"), ["ERR STATE no reply is waiting for CNT"])
        self.assertEqual(await c.req("MOTD"), ["OK"])       # a reply that fits leaves nothing
        self.assertEqual(await c.req("CNT"), ["ERR STATE no reply is waiting for CNT"])

    async def test_a_refused_line_leaves_the_rest(self):
        """ERR RATE and ERR NEWLINE refuse a line before it is run: the rest still waits."""
        c = await self.connect(name="ann")
        await c.req("PAG %d" % MIN)
        self.assertEqual((await c.req("GAM"))[-1], "CNT")
        c.w.write(b"LST\n")                                 # the wrong ending
        self.assertEqual((await c.page())[0], ["ERR NEWLINE lines end with CR LF here"])
        self.newest().tokens = 0
        with mock.patch.object(Limits, "rate_per_sec", 0.0):
            self.assertEqual(await c.req("LST"), ["ERR RATE slow down"])
        self.newest().tokens = 10
        self.assertEqual((await c.whole("CNT"))[0][-1][-1], "OK")

    async def test_a_long_list_of_tables(self):
        """LST clients with 25 tables besides the asker's: 20 of them, a CLIENT line for each seated player,
        MORE 5 and OK, across pages."""
        self.srv.set_setting("games.waiting_per_type", "99")
        for name in ("bob", "cy", "dee"):
            p = await self.player(name)
            await self.do(p, "CLI PROG ParlorNet 1.0")
            await self.do(p, "CLI MACH Apple IIe Enhanced 128K")
            for _ in range(9 if name != "dee" else 7):
                tid = (await self.do(p, "NEW reversi standard"))[-1].split()[1]
                self.assertEqual((await self.do(p, "JON %s" % tid))[-1], "OK 0")
        c = await self.connect(name="ann")
        whole = await c.req("LST clients")
        self.assertEqual(sum(1 for l in whole if l.startswith("TAB ")), 20)
        self.assertEqual(sum(1 for l in whole if l.startswith("CLIENT ")), 20)
        self.assertEqual(whole[-2:], ["MORE 5", "OK"])
        await c.req("PAG %d" % MIN)
        pages, sizes = await c.whole("LST clients")
        self.assertGreaterEqual(len(pages), 4)
        self.check_pages(pages, sizes, MIN)
        self.assertEqual(self.joined(pages), whole)
        for p in pages[:-1]:                                # a TAB line and its CLIENT line may be on two pages
            self.assertTrue(all(l.split()[0] in ("TAB", "CLIENT", "MORE", "CNT") for l in p))

    async def test_a_long_view_with_its_moves(self):
        """Dots and Boxes, majority, at the start: every line of the board is a move, over 3 KB of view."""
        ann = await self.connect(name="ann")
        bob = await self.player("bob")
        self.assertEqual(await ann.req("NEW dotsandboxes majority"), ["OK 1"])
        await ann.req("JON 1 0")
        await self.do(bob, "JON 1 1")
        whole = await ann.req("VUE 1 GMT")
        self.assertGreater(nbytes(whole), 3000)
        self.assertGreater(sum(1 for l in whole if l.startswith("M ")), 100)
        for most in (MIN, 1000, 1024):
            await ann.req("PAG %d" % most)
            pages, sizes = await ann.whole("VUE 1 GMT")
            self.assertGreater(len(pages), 3)
            self.check_pages(pages, sizes, most)
            self.assertEqual(self.joined(pages), whole)
            await ann.req("PAG 0")

    async def test_the_extension_channel_refuses_pag(self):
        if not frontends.supported():
            self.skipTest("the extension channel needs Python 3.13")
        keys = make_keys("web")
        self.srv.keyring = frontends.Keyring(keys)
        fe = await asyncio.start_server(self.srv.handle_frontend, "127.0.0.1", 0)
        x = await CoreClient.connect("127.0.0.1", fe.sockets[0].getsockname()[1], os.path.join(keys, "web.key"))
        try:
            self.assertEqual(await x.req("PAG 600"), ([], "ERR NOTPUBLIC PAG is only for the public port"))
            self.assertEqual(await x.req("CNT"), ([], "ERR STATE no reply is waiting for CNT"))
            await x.req("HELO ann")
            data, final = await x.req("GAM")
            self.assertEqual(final, "OK")
            self.assertNotIn("CNT", data)
        finally:
            x.close()
            fe.close()


class Closes(Base):
    """The lines the server sends by itself: ERR IDLE and ERR LIMIT while a reply waits, and the time allowance's
    notices, which go in front of the next page and count toward it."""

    async def test_err_idle_while_a_reply_waits(self):
        with mock.patch.object(Limits, "idle_secs", 0.5):
            c = await self.connect(name="ann")
            await c.req("PAG %d" % MIN)
            self.assertEqual((await c.req("GAM"))[-1], "CNT")
            self.assertEqual(await c.page(), (["ERR IDLE closing"], len("ERR IDLE closing\r\n")))
            self.assertEqual(await c.r.read(), b"")

    async def test_notices_count_toward_the_page(self):
        self.clock = Clock(NOV_29)
        self.srv.allowance.clock = self.clock
        self.srv.settings["dialin.call_mins"] = "10"
        c = await self.connect(first=header(), name="ann")
        await c.req("PAG %d" % MIN)
        whole = await c.req("GAM")                          # PAG's own reply fit, so this is the first page
        self.assertEqual(whole[-1], "CNT")
        while (await c.whole("CNT"))[0][-1][-1] != "OK":
            pass
        self.clock.at += 5 * 60 + 1
        await c.send("GAM")
        lines, size = await c.page()
        self.assertEqual(lines[0], "INF This call ends in 5 minutes.")      # in front, on the first page
        self.assertEqual(lines[-1], "CNT")
        self.assertLessEqual(size, MIN)
        self.assertGreater(size + 50, MIN)                  # full: the notice took room from the reply
        self.clock.at += 4 * 60 - 1
        await c.send("CNT")                                 # the next notice comes in front of the next page
        lines, size = await c.page()
        self.assertEqual(lines[0], "INF This call ends in 1 minute.")
        self.assertLessEqual(size, MIN)
        self.clock.at += 61                                 # the time runs out while the rest waits
        self.assertEqual(await c.page(), (["ERR LIMIT closing: this call has had its 10 minutes"],
                                          len("ERR LIMIT closing: this call has had its 10 minutes\r\n")))
        self.assertEqual(await c.r.read(), b"")


class Moves(Base):
    """MOV, UND, END and RES on a connection that pages: the reply carries the oldest unseen events that fit one
    page with its final line, and SIN brings the rest, in order, with none lost or repeated."""

    def seqs(self, lines):
        return [int(l.split()[2]) for l in lines if l.startswith("EVT ")]

    async def rest(self, c, tid):
        """SIN after the move, through every page: its events."""
        pages, sizes = await c.whole("SIN %s" % tid)
        self.check_pages(pages, sizes, MIN)
        return [l for l in self.joined(pages) if l.startswith("EVT ")]

    async def chat(self, others, tid, n):
        for i in range(n):
            s = others[i % len(others)]
            self.assertEqual(await self.do(s, "CHT %s %02d %s" % (tid, i, "c" * 50)), ["OK"])

    async def test_a_shot_at_a_table_of_eight(self):
        """Cannon Fire for eight, a shot that damages three tanks, with chat from the others before it."""
        ann = await self.connect(name="ann")
        others = [await self.player("p%d" % i) for i in range(1, 8)]
        self.assertEqual(await ann.req("NEW cannonfire classic players=8 w=64 seed=30"), ["OK 1"])
        self.assertEqual(await ann.req("JON 1"), ["OK 0"])
        for s in others:
            self.assertTrue((await self.do(s, "JON 1"))[-1].startswith("OK"))
        await ann.req("SIN 1")
        start = self.srv.tables["1"].seq
        await self.chat(others, "1", 6)
        await ann.req("PAG %d" % MIN)
        await ann.send("MOV 1 FIRE 94 100")
        reply, size = await ann.page()
        self.assertLessEqual(size, MIN)
        self.assertEqual(reply[-1], "OK")                   # one page, ended by the real final line
        t = self.srv.tables["1"]
        shot = t.lines_since(start)
        self.assertGreater(nbytes(shot + ["OK"]), MIN)      # whole, it would not have fit
        got = [l for l in reply if l.startswith("EVT ")] + await self.rest(ann, "1")
        self.assertEqual(got, shot)                         # every event once, in order
        self.assertEqual(self.seqs(got), list(range(start + 1, t.seq + 1)))
        self.assertTrue(any(" DMG " in l for l in got) and any(" FIRE 0 94 100" in l for l in got))
        self.assertEqual(await ann.req("SIN 1"), ["OK %d" % t.seq])    # nothing twice

    async def test_chat_piles_up_before_a_move(self):
        ann = await self.connect(name="ann")
        bob, cy = await self.player("bob"), await self.player("cy")
        await ann.req("NEW reversi standard")
        await ann.req("JON 1 0")
        await self.do(bob, "JON 1 1")
        await self.do(cy, "SEE 1")
        await ann.req("SIN 1")
        await ann.req("PAG %d" % MIN)
        t = self.srv.tables["1"]
        for line, final in (("MOV 1 d3", "OK"), ("RES 1", "OK")):
            start = t.seq
            await self.chat([bob, cy], "1", 20)             # about 1,500 bytes of chat ann has not read
            await ann.send(line)
            reply, size = await ann.page()
            self.assertLessEqual(size, MIN, line)
            self.assertEqual(reply[-1], final, line)
            sent = [l for l in reply if l.startswith("EVT ")]
            self.assertTrue(sent and all(" CHT " in l for l in sent), sent)     # the oldest first
            self.assertGreater(size + 70, MIN)              # as many as fit
            got = sent + await self.rest(ann, "1")
            self.assertEqual(got, t.lines_since(start), line)
            self.assertEqual(self.seqs(got), list(range(start + 1, t.seq + 1)))
            if line.startswith("MOV"):
                self.assertTrue(any(" PUT 0 d3 " in l for l in got[-3:]))      # the move's own, after the chat
                await self.do(bob, "SIN 1")
                bob_moves = [l[2:] for l in await self.do(bob, "VUE 1 M") if l.startswith("M ")]
                await self.do(bob, "MOV 1 " + bob_moves[0])
                await ann.whole("SIN 1")
        self.assertIn("END resign 0", t.lines_since(0)[-1])

    async def test_staged_turns(self):
        """UND and END in Backgammon carry as many events as fit as well. The table's seed is fixed, so its dice
        give ann a turn of more than one step to take back."""
        ann = await self.connect(name="ann")
        bob = await self.player("bob")
        with mock.patch.object(core, "table_seed", lambda: 2):
            await ann.req("NEW backgammon standard")
        await ann.req("JON 1 0")
        await self.do(bob, "JON 1 1")
        t = self.srv.tables["1"]
        while t.turn_seat != 0:                             # bob's turn first: he plays it
            await self.do(bob, "SIN 1")
            mv = [l[2:] for l in await self.do(bob, "VUE 1 M") if l.startswith("M ")][0]
            if (await self.do(bob, "MOV 1 " + mv))[-1] == "OK done":
                await self.do(bob, "END 1")
        await ann.req("PAG %d" % MIN)
        moves = [l[2:] for l in await ann.req("VUE 1 M") if l.startswith("M ")]
        if moves == ["roll"]:
            await ann.req("MOV 1 roll")
            moves = [l[2:] for l in await ann.req("VUE 1 M") if l.startswith("M ")]
        await ann.whole("SIN 1")
        self.assertTrue((await ann.req("MOV 1 " + moves[0]))[-1].startswith("OK"))
        await ann.whole("SIN 1")
        steps = [("UND 1", " UNDO 0 "), ("MOV 1 " + moves[0], " STEP 0 ")]
        for line, kind in steps:
            mark = t.seq
            await self.chat([bob], "1", 12)
            await ann.send(line)
            reply, size = await ann.page()
            self.assertLessEqual(size, MIN)
            self.assertTrue(reply[-1].startswith("OK"), reply)
            got = [l for l in reply if l.startswith("EVT ")] + await self.rest(ann, "1")
            self.assertEqual(self.seqs(got), list(range(mark + 1, t.seq + 1)), line)
            self.assertIn(kind, got[-1])
            if line.startswith("MOV") and reply[-1] == "OK more":
                steps.append(("MOV 1 " + [l[2:] for l in await ann.req("VUE 1 M") if l.startswith("M ")][0],
                              " STEP 0 "))
            elif line.startswith("MOV") and reply[-1] == "OK done":
                steps.append(("END 1", " TURN 1"))

    async def test_no_paging_carries_every_event(self):
        ann = await self.connect(name="ann")
        bob = await self.player("bob")
        await ann.req("NEW reversi standard")
        await ann.req("JON 1 0")
        await self.do(bob, "JON 1 1")
        await ann.req("SIN 1")
        start = self.srv.tables["1"].seq
        await self.chat([bob], "1", 20)
        reply = await ann.req("MOV 1 d3")
        self.assertGreater(nbytes(reply), MIN)
        self.assertEqual(reply[:-1], self.srv.tables["1"].lines_since(start))
        self.assertEqual(await ann.req("SIN 1"), ["OK %d" % self.srv.tables["1"].seq])

    async def test_a_typed_move_in_cmd(self):
        """CMD reads whole replies inside: a typed move's events still come with SIN, none lost."""
        ann = await self.connect(name="ann")
        bob = await self.player("bob")
        await ann.req("NEW reversi standard")
        await ann.req("JON 1 0")
        await self.do(bob, "JON 1 1")
        await ann.req("SIN 1")
        start = self.srv.tables["1"].seq
        await self.chat([bob], "1", 20)
        await ann.req("PAG %d" % MIN)
        self.assertEqual(await ann.req("CMD 1 /d3"), ["OK"])
        got = await self.rest(ann, "1")
        self.assertEqual(self.seqs(got), list(range(start + 1, self.srv.tables["1"].seq + 1)))


class Fits(Base):
    """The reply to every request that changes something fits one page at the fewest bytes, PAG_MIN, with CR
    LF. Here the clients read their events (SIN) before each move. Moves.test_* has the events that pile up."""

    def fits(self, reply, what):
        self.assertLessEqual(nbytes(reply), MIN, "%s: %d bytes: %r" % (what, nbytes(reply), reply))
        return reply

    async def fit(self, sess, line):
        return self.fits(await self.do(sess, line), line)

    async def test_tables_and_moves(self):
        ann, bob, cy = await self.player("ann"), await self.player("bob"), await self.player("cy")
        for s in (ann, bob, cy):
            for line in ("CLI PROG ParlorNet 1.0.0 for the Apple II family", "CLI MACH Apple IIe Enhanced 128K",
                         "PRF SET colors amber on black", "PRF DEL colors", "PFL SET PREF Apple IIe", "ACT",
                         "PAG %d" % MIN, "PAG 0"):
                await self.fit(s, line)
        tid = (await self.fit(ann, "NEW reversi standard turn=5m vis=private players=2"))[-1].split()[1]
        await self.fit(ann, "JON %s 0" % tid)
        await self.fit(ann, "INV %s bob 1" % tid)
        await self.fit(ann, "INV %s cy" % tid)
        await self.fit(cy, "UNV %s cy" % tid)
        await self.fit(cy, "SEE %s" % tid)
        await self.fit(bob, "JON %s" % tid)
        await self.fit(cy, "CHT %s %s" % (tid, "x" * 56))
        await self.fit(ann, "NOTE %s %s" % (tid, "y" * 48))
        for _ in range(6):                                  # the usual turn: read the events, then move
            for s in (ann, bob):
                await self.do(s, "SIN %s" % tid)
                moves = [l[2:] for l in await self.do(s, "VUE %s M" % tid) if l.startswith("M ")]
                if moves:
                    self.assertTrue((await self.fit(s, "MOV %s %s" % (tid, moves[0])))[-1].startswith("OK"))
        await self.do(ann, "SIN %s" % tid)
        await self.fit(ann, "RES %s" % tid)
        await self.fit(cy, "LVE %s" % tid)
        t2 = (await self.fit(bob, "NEW backgammon standard"))[-1].split()[1]
        await self.fit(bob, "JON %s" % t2)
        await self.fit(bob, "LVE %s" % t2)
        await self.fit(ann, "OUT")

    async def test_commands(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        await self.fit(ann, "CMD /open reversi")
        tid = self.srv.next_id - 1
        for line in ("CMD %d /invite bob" % tid, "CMD %d /uninvite bob" % tid, "CMD %d /invite bob" % tid,
                     "CMD %d /note %s" % (tid, "z" * 48), "CMD %d /say %s" % (tid, "w" * 56), "CMD %d /close" % tid,
                     "CMD /open checkers", "CMD /invite bob gridwords"):
            await self.fit(ann, line)
        g = self.srv.next_id - 1
        await self.fit(bob, "CMD /join %d" % g)
        for line in ("CMD %d /h8 across QUIZ" % g, "CMD %d /pass" % g, "CMD %d YES /pass" % g,
                     "CMD %d /resign" % g, "CMD %d YES /resign" % g, "CMD /quit", "CMD YES /quit"):
            await self.fit(ann, line)

    async def test_staged_turns(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = (await self.do(ann, "NEW backgammon standard"))[-1].split()[1]
        await self.do(ann, "JON %s 0" % tid)
        await self.do(bob, "JON %s 1" % tid)
        for _ in range(8):
            for s in (ann, bob):
                await self.do(s, "SIN %s" % tid)
                moves = [l[2:] for l in await self.do(s, "VUE %s M" % tid) if l.startswith("M ")]
                if not moves:
                    continue
                r = await self.fit(s, "MOV %s %s" % (tid, moves[0]))
                if r[-1] == "OK more" and moves[0] != "roll":
                    await self.fit(s, "UND %s" % tid)
                    r = await self.fit(s, "MOV %s %s" % (tid, moves[0]))
                if r[-1] == "OK done":
                    await self.fit(s, "END %s" % tid)

    async def test_the_allowance_notices_with_a_sign_in(self):
        """A player whose month is used up signs in: the reply with its two notices fits."""
        clock = Clock(NOV_29)
        self.srv.allowance.clock = clock
        self.srv.allowance.add(A.DIALIN, "ann", clock.at - 2 * 3600, clock.at - 3600)
        c = await self.connect(first=header())
        await c.req("PAG %d" % MIN)
        await c.send("HELO ann")
        lines, size = await c.page()
        self.assertEqual(lines[-1], "OK -")
        self.assertEqual(len(lines), 3)
        self.assertLessEqual(size, MIN)


class SignInFits(unittest.IsolatedAsyncioTestCase):
    """Sign-in, sign-up and a password change under sign-in password, sign-up email: the longest names and
    addresses, the password rule, the mail's lines and a lockout's, each with the two longest notices of the time
    allowance in front. Driven through dispatch: CR LF is the longest line ending, so these sizes are the most
    any connection gets."""

    async def asyncSetUp(self):
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "password", None, "email")
        self.srv.settings["email.transport"] = "log"
        m = self.srv.allowance.open(A.DIALIN)
        self.notes = ["INF " + n for n in self.srv.allowance.used_up(m)]
        self.srv.allowance.close(m)

    async def fit(self, sess, line):
        reply = await self.srv.dispatch(sess, line)
        self.assertLessEqual(nbytes(self.notes + reply), MIN, "%s: %d bytes with the notices: %r"
                             % (line.split()[0], nbytes(self.notes + reply), reply))
        return reply

    async def test_sign_up_and_sign_in(self):
        name, addr = "abcdefghijkl", "a" * 41 + "@" + "b" * 10 + ".example.org"     # 64 characters, AUT's most
        self.assertEqual(len(addr), 64)
        s = Session(Writer())
        await self.fit(s, "HELO " + name)                   # not signed up: the question
        await self.fit(s, "AUT confirm y")
        r = await self.fit(s, "AUT email " + addr)
        self.assertEqual(r[-1], "ASK password Password_from_the_email secret")
        pw = mail_password(self.srv)
        await self.fit(s, "AUT password nope")              # a wrong one ends the dialog
        await self.fit(s, "USER " + name)                   # the pending account's question
        r = await self.fit(s, "AUT password " + pw)
        self.assertEqual(r[-1], "ASK password New_password secret new")
        await self.fit(s, "AUT password short")             # refused, with the rule
        await self.fit(s, "AUT password Fresh-start9")
        await self.fit(s, "AUT confirm Other-start9")       # no match: the rule again
        await self.fit(s, "AUT password Fresh-start9")
        r = await self.fit(s, "AUT confirm Fresh-start9")
        self.assertEqual(r[-1], "OK -")
        await self.fit(s, "KEY NEW Apple IIe Enhanced 128K with a long label")
        await self.fit(s, "KEY DEL")
        await self.fit(s, "PWD")
        await self.fit(s, "AUT current Fresh-start9")
        await self.fit(s, "AUT password Fresh-start10")
        await self.fit(s, "AUT confirm Fresh-start10")
        await self.fit(s, "OUT all")
        t = Session(Writer())
        for _ in range(4):                                  # wrong answers, then the lockout's words
            await self.fit(t, "USER " + name)
            await self.fit(t, "AUT password Wrong-guess1")
        await self.fit(t, "USER " + name)
        await self.fit(t, "AUT password reset")             # a forgotten password: mailed
        u = Session(Writer())
        await self.fit(u, "REG mnopqrstuvwx")
        await self.fit(u, "AUT email " + addr.replace("a" * 41, "c" * 41))
        await self.fit(Session(Writer()), "USER nobody")
        await self.fit(Session(Writer()), "REG admin")


if __name__ == "__main__":
    unittest.main()
