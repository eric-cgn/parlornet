"""What each player plays with, told when they sit down (docs/protocol.md, Clients and machines): the CLIENT event
right after JOIN and when a seat's program changes, VUE's C flag, LST's clients word, the join notice's NFX
line, the rule that a private table's are seen only by its players and its creator, and the seats' programs
kept across a restart."""
import os
import shutil
import sys
import tempfile
import unittest

from test_core import ROOT
from test_table_notes import Notes

sys.path.insert(0, ROOT)
from parlord.core import MAXLINE, Limits, client_say  # noqa: E402

Base = Notes.__bases__[0]     # the in-process core of test_table_notes
GM = ("GameMaster 1.0.0 + ParlorNet 0.5", "Macintosh Plus")
APPLE = ("ParlorNet for the Apple IIe 1.1", "Apple IIe (enhanced)")


def kinds(lines, kind):
    return [l.split(" ", 3)[3] for l in lines if l.startswith("EVT ") and l.split()[3] == kind]


class Say(unittest.TestCase):
    def test_words(self):
        self.assertEqual(client_say(*GM), "with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus")
        self.assertEqual(client_say("Netscape Navigator 3.0", ""), "with Netscape Navigator 3.0")
        self.assertEqual(client_say("", "Apple IIe (enhanced)"), "on an Apple IIe (enhanced)")
        self.assertEqual(client_say("Firefox 128", "macOS"), "with Firefox 128 on macOS")
        self.assertEqual(client_say("Outland Gateway", "Macintosh"), "with the Outland Gateway on a Macintosh")
        self.assertEqual(client_say("", ""), "")

    def test_short(self):
        """At most Limits.client_say_len: the program whole, the machine cut at a word or left out."""
        long_prog, long_mach = "P" * 40, "Macintosh Quadra 840AV with a DayStar card"[:40]
        self.assertEqual(client_say(long_prog, long_mach), "with %s on a Macintosh" % long_prog)
        self.assertEqual(client_say(long_prog, "Y" * 40), "with " + long_prog)
        text = client_say("BobGrid 2.3", "Sun SPARCstation 20 with two CPUs and a big disk card")
        self.assertLessEqual(len(text), Limits.client_say_len)
        self.assertEqual(text, "with BobGrid 2.3 on a Sun SPARCstation 20 with two CPUs and")
        self.assertEqual(client_say("GameMaster 1.0.0 + ParlorNet 0.5 abcd", "Macintosh", most=50),
                         "with GameMaster 1.0.0 + ParlorNet 0.5 abcd", "not even 'on a Macintosh' fits")
        self.assertEqual(client_say("GameMaster 1.0.0 + ParlorNet 0.5", "Macintosh Plus", most=55),
                         "with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh")


class Events(Base):
    async def said(self, sess, prog, mach=""):
        await self.req(sess, "CLI PROG " + prog)
        if mach:
            await self.req(sess, "CLI MACH " + mach)

    async def test_long_event_fits(self):
        ann = await self.player("ann")
        await self.said(ann, "Z" * 40, "Y" * 40)
        tid = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        t = self.srv.tables[tid]
        t.id, t.seq = "99999", 999990        # a long table number and event number
        self.srv.tables["99999"] = t
        await self.req(ann, "JON 99999")
        line = (await self.req(ann, "SIN 99999 999990"))[1]
        self.assertTrue(line.startswith("EVT 99999 999992 CLIENT 0 with ZZZ"), line)
        self.assertLessEqual(len(line), MAXLINE - 2)

    async def test_join_then_client(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        await self.said(ann, *GM)
        tid = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(bob, "SEE %s" % tid)
        out = await self.req(bob, "SIN %s 0" % tid)
        self.assertEqual(out[:2], ["EVT %s 1 JOIN 0 ann" % tid,
                                   "EVT %s 2 CLIENT 0 with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus" % tid])
        await self.req(bob, "LVE %s" % tid)
        await self.req(bob, "JON %s" % tid)     # bob said nothing: nothing said for him
        out = await self.req(ann, "SIN %s 2" % tid)
        self.assertEqual(kinds(out, "JOIN"), ["JOIN 1 bob"])
        self.assertEqual(kinds(out, "CLIENT"), [])
        self.assertEqual(out[1], "EVT %s 4 START" % tid, "START follows, with no CLIENT for bob")
        for l in out:
            self.assertLessEqual(len(l), MAXLINE - 2)

    async def test_change_is_told_once(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        await self.said(ann, *APPLE)
        tid = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(bob, "JON %s" % tid)
        ann2 = await self.player("ann")          # ann again, from another program
        await self.said(ann2, *GM)
        seq = self.srv.tables[tid].seq
        await self.req(ann2, "MOV %s d3" % tid)
        out = await self.req(bob, "SIN %s %d" % (tid, seq))
        self.assertEqual(out[0], "EVT %s %d CLIENT 0 %s" % (tid, seq + 1, client_say(*GM)), "before the move")
        await self.req(bob, "MOV %s c3" % tid)
        seq = self.srv.tables[tid].seq
        await self.req(ann2, "CHT %s hello" % tid)
        await self.req(ann, "CHT %s again" % tid)     # this connection said Apple: told again
        out = await self.req(bob, "SIN %s %d" % (tid, seq))
        self.assertEqual(kinds(out, "CLIENT"), ["CLIENT 0 " + client_say(*APPLE)])
        quiet = await self.player("ann")          # a connection that said nothing changes nothing
        seq = self.srv.tables[tid].seq
        await self.req(quiet, "MOV %s %s" % (tid, (await self.req(quiet, "VUE %s M" % tid))[-2].split()[1]))
        self.assertEqual(kinds(await self.req(bob, "SIN %s %d" % (tid, seq)), "CLIENT"), [])
        # the turn records are as before: the move's own events only
        self.assertFalse(any("CLIENT" in l for l in await self.req(bob, "TRN %s" % tid)))

    async def test_bots_say_nothing(self):
        ann = await self.player("ann")
        bot = await self.player("robo", bot=True)
        await self.said(bot, "ParlorNet bot")
        tid = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(bot, "JON %s" % tid)
        self.assertEqual(kinds(await self.req(ann, "SIN %s 0" % tid), "CLIENT"), [])

    async def test_view_and_list(self):
        ann, bob, cy = await self.player("ann"), await self.player("bob"), await self.player("cy")
        await self.said(ann, *GM)
        await self.said(bob, "", "Amiga 500")
        tid = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(cy, "SEE %s" % tid)
        view = await self.req(cy, "VUE %s C" % tid)
        self.assertEqual(view, ["V reversi/1 2", "S Waiting for players", "CLIENT 0 " + client_say(*GM), "OK"])
        self.assertNotIn("CLIENT", " ".join(await self.req(cy, "VUE %s" % tid)), "only with C")
        await self.req(bob, "JON %s" % tid)
        view = await self.req(cy, "VUE %s CM" % tid)
        self.assertEqual([l for l in view if l.startswith("CLIENT")],
                         ["CLIENT 0 " + client_say(*GM), "CLIENT 1 on an Amiga 500"])
        lst = await self.req(cy, "LST clients")
        i = lst.index(next(l for l in lst if l.startswith("TAB %s " % tid)))
        self.assertEqual(lst[i + 1:i + 3], ["CLIENT 0 " + client_say(*GM), "CLIENT 1 on an Amiga 500"])
        self.assertFalse(any(l.startswith("CLIENT") for l in await self.req(cy, "LST")), "only with clients")
        self.assertTrue((await self.req(cy, "LST clients clients"))[-1].startswith("ERR SYNTAX"))

    async def test_private_table(self):
        """Seen by the players seated there and the creator; an invited player who watches sees the seat only."""
        ann, bob, cy = await self.player("ann"), await self.player("bob"), await self.player("cy")
        await self.said(bob, *APPLE)
        tid = (await self.req(ann, "NEW gridwords standard players=3 vis=private"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(ann, "INV %s bob" % tid)
        await self.req(bob, "JON %s" % tid)
        await self.req(bob, "INV %s cy" % tid)
        await self.req(ann, "LVE %s" % tid)      # ann, the creator, is not seated now
        await self.req(ann, "SEE %s" % tid)
        await self.req(cy, "SEE %s" % tid)
        told = "CLIENT 1 " + client_say(*APPLE)
        self.assertIn(told, kinds(await self.req(ann, "SIN %s 0" % tid), "CLIENT"))
        out = await self.req(cy, "SIN %s 0" % tid)
        self.assertEqual(kinds(out, "CLIENT"), ["CLIENT 1"], "the invited watcher: the seat, not the program")
        self.assertFalse(any("Apple" in l for l in out))
        self.assertNotIn("CLIENT", " ".join(await self.req(cy, "VUE %s C" % tid)))
        lst = await self.req(cy, "LST clients")
        self.assertTrue(any(l.startswith("TAB %s " % tid) for l in lst))
        self.assertFalse(any(l.startswith("CLIENT") for l in lst))
        self.assertIn(told, await self.req(ann, "VUE %s C" % tid))
        self.assertIn(told, await self.req(ann, "LST clients"))
        await self.req(cy, "JON %s" % tid)       # seated now: cy sees it
        self.assertIn(told, kinds(await self.req(cy, "SIN %s 0" % tid), "CLIENT"))

    async def test_join_notice(self):
        ann, bob = await self.player("ann", frontend="play"), await self.player("bob")
        await self.req(ann, "PRF SET notify join Tok12345")
        await self.said(bob, *GM)
        tid = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        n = int((await self.req(ann, "NFY"))[-1].split()[1])
        await self.req(bob, "JON %s" % tid)
        out = await self.req(ann, "NFY %d" % n)
        self.assertEqual(out[0], "NFY %d join %s ann Tok12345 bob reversi/1" % (n + 1, tid))
        self.assertEqual(out[1], "NFX CLIENT " + client_say(*GM))


class Deleted(Base):
    async def test_deleting_an_account_forgets_its_programs(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        await self.req(ann, "CLI PROG " + GM[0])
        tid = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(bob, "JON %s" % tid)
        await self.srv.delete_player(self.srv.players["ann"])
        out = await self.req(bob, "SIN %s 0" % tid)
        self.assertIn("EVT %s 2 CLIENT 0" % tid, out)
        self.assertFalse(any(GM[0] in l for l in out))
        self.assertFalse(any(l.startswith("CLIENT") for l in await self.req(bob, "VUE %s C" % tid)))
        rows = self.srv.store.db.execute("SELECT text FROM events WHERE table_id=?", (tid,)).fetchall()
        self.assertFalse(any(GM[0] in r[0] for r in rows))


class Restart(Base):
    db = None

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "p.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    async def test_kept(self):
        ann = await self.player("ann")
        await self.req(ann, "CLI PROG " + GM[0])
        tid = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        srv2 = await self.server(self.db)
        cy = await self.player("cy", srv=srv2)
        await self.req(cy, "SEE %s" % tid, srv2)
        self.assertIn("CLIENT 0 with " + GM[0], await self.req(cy, "VUE %s C" % tid, srv2))


if __name__ == "__main__":
    unittest.main()
