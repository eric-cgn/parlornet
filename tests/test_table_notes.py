"""A table's notes (NOTE), the data kept with a table (TDL, TDS, TDD) and opening the racks of a running game
(OPR): the core in-process, its database across a restart, and a database from before notes and table data."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

from test_core import ROOT
from test_gridwords import WORDS, P, best_play, write_words
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

    async def game(self, *names, rules="english", options="", srv=None):
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


class OpenRacks(Base):
    async def test_against_the_bot_at_once(self):
        ann, robo = await self.player("ann"), await self.player("robo", bot=True)
        tid = await self.game(ann, robo)
        t = self.srv.tables[tid]
        await self.two_moves(ann, robo, tid)
        self.assertFalse(any(l.startswith(("RK ", "BO ")) for l in await self.req(ann, "VUE %s" % tid)))
        before = dict(t.state)
        self.assertEqual(await self.req(ann, "OPR %s my own program" % tid), ["OK open"])
        # the state is the one a racks=open table makes, with the same bag, racks, scores and seed
        self.assertEqual(t.state, dict(before, open=True))
        self.assertEqual(t.options["racks"], "open")
        view = await self.req(ann, "VUE %s" % tid)
        self.assertEqual(view[1:-1], P.op_view({"state": dict(before, open=True), "seat": 0})["wire"]
                         + ["BOT 1", view[-2]])
        self.assertIn("RK 0 %s" % before["racks"][0], view)
        self.assertIn("RK 1 %s" % before["racks"][1], view)
        self.assertEqual("".join(l[3:] for l in view if l.startswith("BO ")), before["bag"][::-1])
        watcher = await self.player("wes")
        await self.req(watcher, "SEE %s" % tid)
        self.assertFalse(any(l.startswith(("RK ", "BO ")) for l in await self.req(watcher, "VUE %s" % tid)))
        # recorded in the history and in the notes; one way
        self.assertEqual(t.events[-1][1], "RACKS open 0")
        notes = await self.req(ann, "NOTE %s" % tid)
        self.assertRegex(notes[0], r"^NOTE \d{4}-\d\d-\d\d - ann opened the racks for my own program$")
        self.assertEqual(notes[-1], "OK 1")
        self.assertEqual(await self.req(ann, "OPR %s" % tid), ["OK open"])
        self.assertEqual((await self.req(ann, "OPR %s no" % tid))[0][:9], "ERR STATE")
        self.assertEqual(len(t.notes), 1)

    async def test_a_converted_table_plays_as_one_made_open(self):
        """The same seed, moves and conversion give the state of a table made with racks=open."""
        ann, robo = await self.player("ann"), await self.player("robo", bot=True)
        tid = await self.game(ann, robo)
        t = self.srv.tables[tid]
        moves = []
        for _ in range(2):
            for who, seat in ((ann, 0), (robo, 1)):
                view = await self.req(who, "VUE %s" % tid)
                found = best_play(view, {w.upper() for w in WORDS.split()})
                mv = list(found[1]) if found else ["EXCH", t.state["racks"][seat][:2]]
                self.assertTrue((await self.req(who, "MOV %s %s" % (tid, " ".join(mv))))[-1].startswith("OK"))
                moves.append((seat, mv))
            if len(moves) == 2:
                self.assertEqual(await self.req(ann, "OPR %s" % tid), ["OK open"])
        st = P.op_new({"players": 2, "options": {"racks": "open"}, "seed": t.seed, "rules": "english"})["state"]
        for seat, mv in moves:
            st = P.op_apply({"state": st, "seat": seat, "move": mv, "rules": "english"})["state"]
        self.assertEqual(t.state, st)

    async def test_with_a_human_who_agrees(self):
        ann, cy = await self.player("ann", frontend="doom"), await self.player("cy", frontend="web")
        tid = await self.game(ann, cy)
        t = self.srv.tables[tid]
        self.assertEqual(await self.req(ann, "OPR %s my own program" % tid), ["OK asked"])
        self.assertEqual(await self.req(ann, "OPR %s" % tid), ["OK asked"])        # asked already
        self.assertNotIn("racks", t.options)
        for s in (ann, cy):                     # hidden until cy agrees
            self.assertFalse(any(l.startswith(("RK ", "BO ")) for l in await self.req(s, "VUE %s" % tid)))
        self.assertEqual(await self.req(cy, "IVL"), ["IVR %s gridwords/1 english ann racks" % tid, "OK"])
        self.assertEqual(await self.req(ann, "IVL"), ["OK"])
        self.assertEqual(t.events[-1][1], "RACKS ask 0 my own program")
        self.assertEqual(await self.req(ann, "TDL %s" % tid), ["TDL racks ask 0 my own program", "OK"])
        self.assertEqual(await self.req(cy, "OPR %s" % tid), ["OK open"])
        self.assertEqual([e for _, e in t.events[-2:]], ["RACKS yes 1", "RACKS open 0"])
        self.assertTrue(any(l.startswith("RK 0 ") for l in await self.req(cy, "VUE %s" % tid)))
        self.assertEqual([n[3] for n in t.notes], ["ann asked to open the racks for my own program",
                                                   "cy agreed: the racks are open"])
        self.assertEqual(await self.req(cy, "IVL"), ["OK"])
        self.assertEqual(await self.req(ann, "TDL %s" % tid), ["OK"])

    async def test_with_a_human_who_declines(self):
        ann, cy = await self.player("ann"), await self.player("cy")
        tid = await self.game(ann, cy)
        t = self.srv.tables[tid]
        self.assertEqual((await self.req(cy, "OPR %s no" % tid))[0][:9], "ERR NOASK")
        self.assertEqual(await self.req(ann, "OPR %s for the CD" % tid), ["OK asked"])
        self.assertEqual(await self.req(cy, "OPR %s no" % tid), ["OK"])
        self.assertEqual(t.events[-1][1], "RACKS no 1")
        self.assertNotIn("racks", t.options)
        self.assertNotIn("racks", t.data)
        self.assertFalse(any(l.startswith(("RK ", "BO ")) for l in await self.req(ann, "VUE %s" % tid)))
        self.assertEqual(await self.req(cy, "IVL"), ["OK"])
        self.assertEqual(t.notes[-1][3], "cy declined to open the racks")
        # asked again and withdrawn by the asker
        self.assertEqual(await self.req(ann, "OPR %s" % tid), ["OK asked"])
        self.assertEqual(await self.req(ann, "OPR %s no" % tid), ["OK"])
        self.assertEqual(t.notes[-1][3], "ann withdrew the request to open the racks")

    async def test_refusals(self):
        ann, cy, wes = await self.player("ann"), await self.player("cy"), await self.player("wes")
        tid = await self.game(ann, cy)
        self.assertEqual((await self.req(wes, "OPR %s" % tid))[0][:13], "ERR NOTSEATED")
        open_tid = (await self.req(ann, "NEW gridwords english"))[-1].split()[1]
        await self.req(ann, "JON %s" % open_tid)
        self.assertEqual((await self.req(ann, "OPR %s" % open_tid))[0][:10], "ERR NOGAME")
        rv = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % rv)
        await self.req(cy, "JON %s" % rv)
        self.assertEqual((await self.req(ann, "OPR %s" % rv))[0][:11], "ERR NORACKS")
        self.assertEqual((await self.req(ann, "OPR 999"))[0][:11], "ERR NOTABLE")


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
        priv = (await self.req(ann, "NEW gridwords english vis=private"))[-1].split()[1]
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
        self.assertEqual((await self.req(ann, "TDS %s racks open" % tid))[0][:12], "ERR RESERVED")
        self.assertEqual((await self.req(ann, "TDD %s racks" % tid))[0][:12], "ERR RESERVED")
        self.assertEqual((await self.req(ann, "TDS %s k %s" % (tid, "v" * 57)))[0][:12], "ERR BADVALUE")
        self.assertEqual((await self.req(ann, "TDS %s k" % tid))[0][:12], "ERR BADVALUE")
        longest = "TDS %s abcdefgh %s" % ("999999", "v" * Limits.tdata_value_len)
        self.assertLessEqual(len(longest), 78)
        for i in range(Limits.tdata_keys - 1):
            self.assertEqual(await self.req(ann, "TDS %s k%d %d" % (tid, i, i)), ["OK"])
        self.assertEqual((await self.req(ann, "TDS %s k99 x" % tid))[0][:9], "ERR LIMIT")
        self.assertEqual(await self.req(ann, "TDS %s k0 again" % tid), ["OK"])      # a key it has: replaced
        # the core's own key does not count against the extensions' keys
        self.assertEqual(await self.req(ann, "OPR %s" % tid), ["OK asked"])
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

    async def test_notes_data_and_open_racks_survive_a_restart(self):
        ann, robo = await self.player("ann", frontend="doom"), await self.player("robo", bot=True)
        tid = await self.game(ann, robo)
        await self.req(ann, "NOTE %s from telnet" % tid)
        await self.req(ann, "TDS %s client doom" % tid)
        self.assertEqual(await self.req(ann, "OPR %s my own program" % tid), ["OK open"])
        old = self.srv.tables[tid]
        srv = await self.server(self.db)
        t = srv.tables[tid]
        self.assertEqual(t.notes, old.notes)
        self.assertEqual(t.data, {"client": "doom"})
        self.assertEqual((t.options, t.state), (old.options, old.state))
        ann2 = await self.player("ann", srv=srv)
        self.assertEqual(len(await self.req(ann2, "NOTE %s" % tid, srv)), 3)
        self.assertTrue(any(l.startswith("RK 1 ") for l in await self.req(ann2, "VUE %s" % tid, srv)))
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


class PluginOption(unittest.TestCase):
    def test_only_racks_open(self):
        st = P.op_new({"players": 2, "options": {}, "seed": 5})["state"]
        r = P.op_option({"state": st, "key": "racks", "value": "open"})
        self.assertEqual(r["state"], dict(st, open=True))
        self.assertNotIn("open", st)                    # the state given is not changed
        self.assertIn("error", P.op_option({"state": st, "key": "racks", "value": "hidden"}))
        self.assertIn("error", P.op_option({"state": st, "key": "size", "value": "open"}))


class Frontends(unittest.TestCase):
    """What the web, telnet, ParlorNet Play and the chat consoles make of notes and RACKS events."""

    def test_pending_requests_and_their_words(self):
        from adapters.common import describe_racks_event, parse_racks_asks, racks_pending
        evs = ["START", "RACKS ask 0 my own program"]
        self.assertEqual(racks_pending(evs, 1), (0, "my own program"))
        self.assertIsNone(racks_pending(evs, 0))                    # the asker waits for no answer of its own
        self.assertIsNone(racks_pending(evs, -1))                   # a watcher is not asked
        self.assertEqual(racks_pending(evs + ["RACKS yes 1"], 2), (0, "my own program"))
        self.assertIsNone(racks_pending(evs + ["RACKS yes 1"], 1))
        self.assertIsNone(racks_pending(evs + ["RACKS no 1"], 1))
        self.assertIsNone(racks_pending(evs + ["RACKS open 0"], 1))
        names = ["eric", "ann"]
        self.assertEqual(describe_racks_event("RACKS ask 0 my own program", names),
                         "eric asks to open the racks for my own program")
        self.assertEqual(describe_racks_event("RACKS no 1", names), "ann said no to opening the racks")
        self.assertEqual(describe_racks_event("RACKS open 0", names), "The racks are open now, as eric asked")
        self.assertIsNone(describe_racks_event("CHT 0 RACKS", names))
        self.assertEqual(parse_racks_asks(["IVT 3 reversi/1 standard bo open", "IVR 25 gridwords/1 english eric racks",
                                           "OK"]),
                         [{"id": "25", "key": "gridwords/1", "rules": "english", "from": "eric"}])

    def test_note_lines_and_the_web_page(self):
        from adapters.chatcmd import note_lines
        from adapters.web import notes_html
        data = ["NOTE 2026-11-02 - eric opened the racks for my own program", "NOTE 2026-11-22 ann <b>hi</b>",
                "OK 2"]
        self.assertEqual(note_lines(data), ["Nov 2 eric opened the racks for my own program",
                                            "Nov 22 ann: <b>hi</b>"])
        self.assertEqual(note_lines(data, 1, day=False), ["ann: <b>hi</b>"])
        link = lambda p, **kw: p + "?" + "&".join("%s=%s" % kv for kv in sorted(kw.items()))   # noqa: E731
        page = notes_html("25", link, note_lines(data), 1, (0, "my own program"), ["eric", "ann"])
        self.assertIn("eric asks to open the racks for my own program.", page)
        self.assertIn("/racks.htm?ans=open", page)
        self.assertIn("/racks.htm?ans=no", page)
        self.assertIn("&lt;b&gt;hi&lt;/b&gt;", page)
        self.assertIn('action="/note.htm"', page)
        watching = notes_html("25", link, [], -1, None, ["eric", "ann"])
        self.assertNotIn("note.htm", watching)
        self.assertNotIn("racks", watching)


del Base

if __name__ == "__main__":
    unittest.main()
