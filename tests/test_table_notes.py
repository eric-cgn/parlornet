"""A table's notes (NOTE), the data kept with a table (TDL, TDS, TDD) and tables marked integrity=false (INT): the
core in-process, its database across a restart, and a database from before notes and table data."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

from test_core import ROOT
from test_gridwords import WORDS, best_play, write_words
from test_invites import FakeWriter

sys.path.insert(0, ROOT)
from parlord.core import Limits, Server, Session  # noqa: E402


class Base(unittest.IsolatedAsyncioTestCase):
    db = ":memory:"

    @classmethod
    def setUpClass(cls):
        cls.words_file = write_words(WORDS)
        cls.old_words = os.environ.get("GRIDWORDS_WORDS")
        os.environ["GRIDWORDS_WORDS"] = cls.words_file

    @classmethod
    def tearDownClass(cls):
        if cls.old_words is None:
            os.environ.pop("GRIDWORDS_WORDS", None)
        else:
            os.environ["GRIDWORDS_WORDS"] = cls.old_words
        os.remove(cls.words_file)

    async def asyncSetUp(self):
        self.servers = []
        self.srv = await self.server()

    async def server(self, db=None):
        srv = Server(os.path.join(ROOT, "plugins"), "test", db or self.db, "none")
        await srv.load_plugins()
        srv.load_state()
        self.servers.append(srv)
        return srv

    async def asyncTearDown(self):
        for srv in self.servers:
            for p in srv.plugins.values():
                p.stop()
                if p.proc:
                    await p.proc.wait()

    async def player(self, name, frontend=None, srv=None, bot=False):
        """A player signed in on the public port, or on an extension's channel (frontend: its key name)."""
        sess = Session(FakeWriter(), frontend=frontend)
        srv = srv or self.srv
        r = await srv.dispatch(sess, "HELO %s" % name)
        self.assertTrue(r[-1].startswith("OK"), r)
        if bot:
            srv.players[name.lower()].bot = True        # as parlord/bots.py flags the bot's accounts
        return sess

    async def req(self, sess, line, srv=None):
        return await (srv or self.srv).dispatch(sess, line)

    async def game(self, *names, rules="standard", options="", srv=None):
        """A running Grid Words table of these players (the first makes it) -> its id."""
        srv = srv or self.srv
        tid = (await self.req(names[0], "NEW gridwords %s players=%d %s" % (rules, len(names), options), srv))[-1]
        tid = tid.split()[1]
        for s in names:
            self.assertTrue((await self.req(s, "JON %s" % tid, srv))[-1].startswith("OK"))
        self.assertEqual(srv.tables[tid].status, "playing")
        return tid

    async def two_moves(self, a, b, tid):
        """a plays its best word (or exchanges), then b passes."""
        view = await self.req(a, "VUE %s" % tid)
        found = best_play(view, {w.upper() for w in WORDS.split()})
        mv = " ".join(found[1]) if found else "EXCH %s" % self.srv.tables[tid].state["racks"][0][:3]
        self.assertTrue((await self.req(a, "MOV %s %s" % (tid, mv)))[-1].startswith("OK"))
        self.assertTrue((await self.req(b, "MOV %s PASS" % tid))[-1].startswith("OK"))


class Integrity(Base):
    """A table marked integrity=false: the server gave out something it normally keeps. One way, with a reason;
    LST, VUE, a note and an INT event show it; the game goes on as before."""
    WHY = "a Networdz program receives the bag and all racks"

    async def test_an_extension_marks_its_players_table(self):
        nw, bob = await self.player("ann", frontend="networdz"), await self.player("bob")
        tid = (await self.req(bob, "NEW gridwords standard"))[-1].split()[1]
        self.assertEqual((await self.req(bob, "INT %s false my reasons" % tid))[0][:15], "ERR NOTFRONTEND")
        self.assertEqual((await self.req(nw, "INT %s false %s" % (tid, self.WHY)))[0][:13], "ERR NOTSEATED")
        self.assertFalse(any("integrity" in l for l in await self.req(bob, "LST")))
        self.assertFalse(any(l.startswith("INT") for l in await self.req(bob, "VUE %s" % tid)))
        self.assertTrue((await self.req(nw, "JON %s" % tid))[-1].startswith("OK"))
        self.assertEqual(await self.req(nw, "INT %s false %s" % (tid, self.WHY)), ["OK"])
        t = self.srv.tables[tid]
        lst = [l for l in await self.req(bob, "LST") if l.startswith("TAB %s " % tid)]
        self.assertTrue(lst[0].endswith(" integrity=false"), lst)
        self.assertIn("INT false " + self.WHY, await self.req(nw, "VUE %s" % tid))
        self.assertIn("EVT %s %d INT false %s" % (tid, t.seq, self.WHY), await self.req(nw, "SIN %s 0" % tid))
        self.assertEqual([n[3] for n in t.notes], ["Integrity: " + self.WHY])
        # one way, and the first reason stays; the game starts and goes on as at any table
        self.assertEqual(await self.req(nw, "INT %s false something else" % tid), ["OK"])
        self.assertEqual((t.no_integrity, len(t.notes)), (self.WHY, 1))
        self.assertTrue((await self.req(bob, "JON %s" % tid))[-1].startswith("OK"))
        self.assertEqual(t.status, "playing")
        self.assertEqual(sum(1 for _, e in t.events if e.startswith("INT ")), 1)
        self.assertEqual((await self.req(nw, "INT %s false %s" % (tid, "x" * 50)))[0][:12], "ERR BADVALUE")
        self.assertEqual((await self.req(nw, "INT %s true" % tid))[0][:10], "ERR SYNTAX")
        await self.req(bob, "RES %s" % tid)
        self.assertEqual((await self.req(nw, "INT %s false %s" % (tid, self.WHY)))[0][:9], "ERR STATE")

    async def test_a_running_game_may_be_marked(self):
        nw, robo = await self.player("ann", frontend="networdz"), await self.player("robo", bot=True)
        tid = await self.game(nw, robo)
        self.assertEqual(await self.req(nw, "INT %s false %s" % (tid, self.WHY)), ["OK"])
        self.assertEqual(self.srv.tables[tid].no_integrity, self.WHY)

    async def test_the_open_view_is_for_the_extension_at_a_marked_table(self):
        """VUE O: every rack and the bag, only for a seated player on an extension's channel at a marked table;
        everyone else's view, and VUE O anywhere else, is as before."""
        nw, bob = await self.player("ann", frontend="networdz"), await self.player("bob")
        web = await self.player("cy", frontend="web")
        tid = await self.game(nw, bob)
        other = await self.game(web, await self.player("dee"))
        rk = lambda lines: [l for l in lines if l.startswith(("RK ", "BO "))]
        self.assertEqual(rk(await self.req(nw, "VUE %s O" % tid)), [])              # not marked yet
        await self.req(nw, "INT %s false %s" % (tid, self.WHY))
        st = self.srv.tables[tid].state
        lines = await self.req(nw, "VUE %s O" % tid)
        self.assertIn("RK 1 %s" % st["racks"][1], lines)
        self.assertEqual("".join(l[3:] for l in lines if l.startswith("BO ")), st["bag"][::-1])
        self.assertEqual(rk(await self.req(nw, "VUE %s" % tid)), [])                # only when asked
        self.assertEqual(rk(await self.req(bob, "VUE %s O" % tid)), [])             # a native player's view
        self.assertEqual(rk(await self.req(web, "VUE %s O" % other)), [])           # a table not marked

    def test_the_reasons_fit(self):
        self.assertLessEqual(len(self.WHY), Limits.integrity_why_len)
        self.assertLessEqual(len("EVT 999999 999999 INT false " + "x" * Limits.integrity_why_len), 78)


class Notes(Base):
    async def test_players_add_and_everyone_who_sees_the_table_reads(self):
        ann, cy, wes = await self.player("ann"), await self.player("cy"), await self.player("wes")
        tid = await self.game(ann, cy)
        self.assertEqual(await self.req(ann, "NOTE %s" % tid), ["OK 0"])
        self.assertEqual(await self.req(ann, "NOTE %s  resumed on the CD, move 3 " % tid), ["OK 1"])
        self.assertRegex((await self.req(wes, "NOTE %s" % tid))[0],
                         r"^NOTE \d{4}-\d\d-\d\d ann resumed on the CD, move 3$")
        self.assertEqual((await self.req(wes, "NOTE %s hi" % tid))[0][:13], "ERR NOTSEATED")
        self.assertEqual((await self.req(ann, "NOTE %s %s" % (tid, "x" * 49)))[0][:11], "ERR BADNOTE")
        self.assertEqual(await self.req(ann, "NOTE %s %s" % (tid, "x" * 48)), ["OK 2"])
        line = (await self.req(ann, "NOTE %s" % tid))[1]
        self.assertLessEqual(len("NOTE 2026-11-22 abcdefghijkl " + "x" * 48), 78)
        self.assertTrue(line.endswith("x" * 48))
        # a private table's notes are its players' and invited players' only
        priv = (await self.req(ann, "NEW gridwords standard vis=private"))[-1].split()[1]
        await self.req(ann, "JON %s" % priv)
        await self.req(ann, "NOTE %s just us" % priv)
        self.assertEqual((await self.req(wes, "NOTE %s" % priv))[0][:11], "ERR PRIVATE")
        await self.req(ann, "INV %s wes" % priv)
        self.assertEqual(len(await self.req(wes, "NOTE %s" % priv)), 2)

    async def test_the_cap(self):
        ann, cy = await self.player("ann"), await self.player("cy")
        tid = await self.game(ann, cy)
        t = self.srv.tables[tid]
        for i in range(Limits.notes):
            self.assertEqual(await self.req(ann if i % 2 else cy, "NOTE %s note %d" % (tid, i)), ["OK %d" % (i + 1)])
        self.assertEqual((await self.req(ann, "NOTE %s one more" % tid))[0][:9], "ERR LIMIT")
        # the core's own note still goes in, in place of the oldest
        self.srv.add_note(t, "", "y" * 80)
        notes = await self.req(cy, "NOTE %s" % tid)
        self.assertEqual(len(notes), Limits.notes + 1)
        self.assertTrue(notes[0].endswith("note 1"))
        self.assertTrue(notes[-2].endswith(" - " + "y" * Limits.note_core_len))
        self.assertTrue(all(len(l) <= 78 for l in notes))


class TableData(Base):
    async def test_limits(self):
        ann, cy = await self.player("ann", frontend="doom"), await self.player("cy")
        tid = await self.game(ann, cy)
        self.assertEqual((await self.req(cy, "TDL %s" % tid))[0][:15], "ERR NOTFRONTEND")
        self.assertEqual((await self.req(cy, "TDS %s client x" % tid))[0][:15], "ERR NOTFRONTEND")
        self.assertEqual(await self.req(ann, "TDS %s Client doom 1993" % tid), ["OK"])
        self.assertEqual(await self.req(ann, "TDL %s" % tid), ["TDL client doom 1993", "OK"])
        self.assertEqual((await self.req(ann, "TDS %s abcdefghi x" % tid))[0][:10], "ERR BADKEY")
        self.assertEqual((await self.req(ann, "TDS %s a.b x" % tid))[0][:10], "ERR BADKEY")
        self.assertEqual((await self.req(ann, "TDS %s integrity x" % tid))[0][:10], "ERR BADKEY")    # the core's
        self.assertEqual((await self.req(ann, "TDD %s integrity" % tid))[0][:10], "ERR BADKEY")
        self.assertEqual((await self.req(ann, "TDS %s k %s" % (tid, "v" * 57)))[0][:12], "ERR BADVALUE")
        self.assertEqual((await self.req(ann, "TDS %s k" % tid))[0][:12], "ERR BADVALUE")
        longest = "TDS %s abcdefgh %s" % ("999999", "v" * Limits.tdata_value_len)
        self.assertLessEqual(len(longest), 78)
        for i in range(Limits.tdata_keys - 1):
            self.assertEqual(await self.req(ann, "TDS %s k%d %d" % (tid, i, i)), ["OK"])
        self.assertEqual((await self.req(ann, "TDS %s k99 x" % tid))[0][:9], "ERR LIMIT")
        self.assertEqual(await self.req(ann, "TDS %s k0 again" % tid), ["OK"])      # a key it has: replaced
        # the core's own key does not count against the extensions' keys
        self.assertEqual(await self.req(ann, "INT %s false %s" % (tid, Integrity.WHY)), ["OK"])
        self.assertEqual(len(self.srv.tables[tid].data), Limits.tdata_keys + 1)
        self.assertEqual(await self.req(ann, "TDD %s k0" % tid), ["OK"])
        self.assertEqual(await self.req(ann, "TDD %s k0" % tid), ["OK"])
        self.assertEqual(await self.req(ann, "TDS %s k99 x" % tid), ["OK"])
        self.assertEqual(len(await self.req(ann, "TDL %s" % tid)), Limits.tdata_keys + 2)


class Persistence(Base):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pnnotes")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "parlor.db")

    async def test_notes_data_and_the_integrity_mark_survive_a_restart(self):
        ann, robo = await self.player("ann", frontend="doom"), await self.player("robo", bot=True)
        tid = await self.game(ann, robo)
        await self.req(ann, "NOTE %s from telnet" % tid)
        await self.req(ann, "TDS %s client doom" % tid)
        self.assertEqual(await self.req(ann, "INT %s false %s" % (tid, Integrity.WHY)), ["OK"])
        old = self.srv.tables[tid]
        srv = await self.server(self.db)
        t = srv.tables[tid]
        self.assertEqual(t.notes, old.notes)
        self.assertEqual(t.data, {"client": "doom", "integrity": "false " + Integrity.WHY})
        self.assertEqual((t.options, t.state, t.no_integrity), (old.options, old.state, Integrity.WHY))
        ann2 = await self.player("ann", srv=srv)
        self.assertEqual(len(await self.req(ann2, "NOTE %s" % tid, srv)), 3)
        self.assertIn("INT false " + Integrity.WHY, await self.req(ann2, "VUE %s" % tid, srv))
        # a table that is removed takes its notes and data with it
        await srv.end_table(tid)
        await srv.end_table(tid)
        db = sqlite3.connect(self.db)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM tdata").fetchone()[0], 0)
        db.close()

    async def test_a_database_from_before_notes_keeps_its_tables(self):
        ann, cy = await self.player("ann"), await self.player("cy")
        tid = await self.game(ann, cy)
        await self.two_moves(ann, cy, tid)
        old = self.srv.tables[tid]
        db = sqlite3.connect(self.db)                   # as it was before: no notes, no table data
        db.execute("DROP TABLE notes")
        db.execute("DROP TABLE tdata")
        db.commit()
        db.close()
        srv = await self.server(self.db)
        t = srv.tables[tid]
        self.assertEqual((t.seats, t.status, t.state, t.seq, t.rules, t.options, t.seed),
                         (old.seats, old.status, old.state, old.seq, old.rules, old.options, old.seed))
        self.assertEqual(t.events, old.events)
        self.assertEqual((t.notes, t.data), ([], {}))
        ann2 = await self.player("ann", srv=srv)
        self.assertEqual(await self.req(ann2, "NOTE %s back" % tid, srv), ["OK 1"])
        srv2 = await self.server(self.db)
        self.assertEqual(len(srv2.tables[tid].notes), 1)


class Frontends(unittest.TestCase):
    """What the frontends make of notes (note_lines, the web page) and of the INT event."""

    def test_note_lines_and_the_web_page(self):
        from adapters.chatcmd import note_lines
        from adapters.web import notes_html
        data = ["NOTE 2026-11-02 - Integrity: " + Integrity.WHY, "NOTE 2026-11-22 ann <b>hi</b>", "OK 2"]
        self.assertEqual(note_lines(data), ["Nov 2 Integrity: " + Integrity.WHY, "Nov 22 ann: <b>hi</b>"])
        self.assertEqual(note_lines(data, 1, day=False), ["ann: <b>hi</b>"])
        link = lambda p, **kw: p + "?" + "&".join("%s=%s" % kv for kv in sorted(kw.items()))   # noqa: E731
        page = notes_html("25", link, note_lines(data), 1)
        self.assertIn("&lt;b&gt;hi&lt;/b&gt;", page)
        self.assertIn('action="/note.htm"', page)
        watching = notes_html("25", link, [], -1)
        self.assertNotIn("note.htm", watching)

    def test_the_integrity_event_in_words(self):
        from adapters.common import describe_integrity_event
        self.assertEqual(describe_integrity_event("INT false " + Integrity.WHY), "Integrity: %s." % Integrity.WHY)
        self.assertIsNone(describe_integrity_event("CHT 0 INT false x"))


del Base

if __name__ == "__main__":
    unittest.main()
