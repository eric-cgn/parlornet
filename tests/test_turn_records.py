"""What a client says it is (CLI), the turns recorded with it (TRN, FEED), players' profiles (PFL), and the
rule that a private table's programs and machines are seen only by its players (docs/protocol.md)."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

from test_core import ROOT
from test_table_notes import Notes

sys.path.insert(0, ROOT)
from parlord.core import Limits, MAXLINE, clean_claim  # noqa: E402
from parlord.store import Store  # noqa: E402

Base = Notes.__bases__[0]     # the in-process core of test_table_notes (removed from its module)
APPLE = ("ParlorNet for the Apple IIe 1.1", "Apple IIe (enhanced)")
OUTLAND = ("Outland Gateway", "Macintosh")


class Claims(Base):
    async def test_cli_before_and_after_sign_in(self):
        sess = await self.player("ann")
        from test_invites import FakeWriter
        from parlord.core import Session
        fresh = Session(FakeWriter())
        self.assertEqual(await self.req(fresh, "CLI PROG %s" % APPLE[0]), ["OK"])
        self.assertEqual(await self.req(fresh, "CLI MACH %s" % APPLE[1]), ["OK"])
        self.assertEqual(await self.req(fresh, "CLI"), ["CLI PROG " + APPLE[0], "CLI MACH " + APPLE[1], "OK"])
        self.assertTrue((await self.req(fresh, "HELO bea"))[-1].startswith("OK"))
        self.assertEqual(await self.req(fresh, "CLI"), ["CLI PROG " + APPLE[0], "CLI MACH " + APPLE[1], "OK"])
        self.assertEqual(await self.req(sess, "CLI"), ["OK"], "optional: nothing said, nothing listed")
        self.assertEqual(await self.req(sess, "CLI MACH Macintosh SE/30"), ["OK"])
        self.assertEqual(await self.req(sess, "CLI MACH"), ["OK"], "nothing after the word forgets it")
        self.assertEqual(await self.req(sess, "CLI"), ["OK"])
        self.assertTrue((await self.req(sess, "CLI ROM x"))[-1].startswith("ERR SYNTAX"))

    async def test_cleaning_and_limits(self):
        self.assertEqual(clean_claim("  Mac\tPlus \x07 "), "Mac Plus")
        self.assertEqual(clean_claim("caf\u00e9 au lait"), "caf au lait")
        self.assertEqual(clean_claim("x" * 50), "x" * Limits.claim_len)
        self.assertEqual(clean_claim(("a " * 30)), ("a " * 20).strip())
        self.assertEqual(clean_claim("\x01\x02"), "")
        sess = await self.player("ann")
        await self.req(sess, "CLI PROG " + "Z" * 70)
        await self.req(sess, "CLI MACH   Power   Macintosh  ")
        self.assertEqual(await self.req(sess, "CLI"), ["CLI PROG " + "Z" * 40, "CLI MACH Power Macintosh", "OK"])
        await self.req(sess, "CLI PROG \x7f\x01")
        self.assertEqual(await self.req(sess, "CLI"), ["CLI MACH Power Macintosh", "OK"])


class Turns(Base):
    async def table(self, a, b, vis="public"):
        tid = (await self.req(a, "NEW reversi standard vis=%s" % vis))[-1].split()[1]
        await self.req(a, "JON %s" % tid)
        if vis == "private":
            await self.req(a, "INV %s %s" % (tid, b.player.name))
        await self.req(b, "JON %s" % tid)
        return tid

    async def test_each_turn_with_its_program_and_machine(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        await self.req(ann, "CLI PROG %s" % APPLE[0])
        await self.req(ann, "CLI MACH %s" % APPLE[1])
        tid = await self.table(ann, bob)
        await self.req(ann, "MOV %s d3" % tid)
        await self.req(bob, "MOV %s c3" % tid)
        out = await self.req(bob, "TRN %s" % tid)
        self.assertEqual(out[-1], "OK 2")
        self.assertRegex(out[0], r"^TRN %s 7 \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ 0 ann reversi/1$" % tid)
        self.assertEqual(out[1:4], ["TRX MOVE played d3, turning 1", "TRX PROG " + APPLE[0], "TRX MACH " + APPLE[1]])
        self.assertRegex(out[4], r"^TRN %s 9 \S+ 1 bob reversi/1$" % tid)
        self.assertEqual(out[5:], ["TRX MOVE played c3, turning 1", "OK 2"], "bob said nothing: no PROG or MACH")
        self.assertEqual((await self.req(ann, "TRN %s 7" % tid))[-1], "OK 1", "after an event number")
        # every event a move made is stored with the player, the program and the machine
        rows = self.srv.store.db.execute("SELECT text, who, client, machine FROM events WHERE table_id=? AND who!=''"
                                         " ORDER BY seq", (tid,)).fetchall()
        self.assertEqual(rows[0], ("PUT 0 d3 FLP d4", "ann") + APPLE)
        self.assertEqual(rows[-1][1:], ("bob", "", ""))
        for l in out:
            self.assertLessEqual(len(l) + 2, MAXLINE)

    async def test_a_staged_turn_is_one_record(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        await self.req(ann, "CLI PROG MacFIBS 3")
        await self.req(bob, "CLI PROG MacFIBS 3")
        tid = (await self.req(ann, "NEW backgammon standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(bob, "JON %s" % tid)
        t = self.srv.tables[tid]
        for _ in range(3):
            s = ann if t.state["turn"] == 0 else bob
            while True:
                ms = [l[2:] for l in await self.req(s, "VUE %s M" % tid) if l.startswith("M ")]
                r = await self.req(s, "MOV %s %s" % (tid, ms[0]))
                if r[-1] != "OK more":
                    break
            if r[-1] == "OK done":
                await self.req(s, "END %s" % tid)
        out = await self.req(ann, "TRN %s" % tid)
        self.assertEqual(out[-1], "OK 3")
        moves = [l for l in out if l.startswith("TRX MOVE ")]
        self.assertEqual(len(moves), 3)
        self.assertRegex(moves[1], r"^TRX MOVE rolled \d-\d, moved \S+/\S+")

    async def test_private_tables_keep_their_clients_to_their_players(self):
        ann, bob, cy = await self.player("ann"), await self.player("bob"), await self.player("cy")
        for s in (ann, bob):
            await self.req(s, "CLI PROG %s" % OUTLAND[0])
            await self.req(s, "CLI MACH %s" % OUTLAND[1])
        tid = await self.table(ann, bob, "private")
        await self.req(ann, "MOV %s d3" % tid)
        mine = await self.req(bob, "TRN %s" % tid)
        self.assertIn("TRX PROG " + OUTLAND[0], mine, "the players at the table see them")
        self.assertEqual(await self.req(cy, "TRN %s" % tid), ["ERR PRIVATE this table is by invitation only"])
        feed = await self.req(cy, "FEED 30")
        self.assertEqual(feed, ["OK 0"], "never in the public feed")
        self.assertEqual(await self.req(ann, "FEED"), ["OK 0"], "not even for its own players")
        # an invited player may see the table, and who played what, but not with what
        dan = await self.player("dan")
        self.srv.tables[tid].invites["dan"] = {"name": "dan", "from": "ann", "seat": -1, "at": 0}
        seen = await self.req(dan, "TRN %s" % tid)
        self.assertTrue(seen[0].startswith("TRN %s " % tid))
        self.assertFalse([l for l in seen if l.startswith(("TRX PROG", "TRX MACH"))])
        # nor counted on a profile (the game is not over: no points either)
        self.assertEqual(await self.req(cy, "PFL ann"), ["PFL NAME ann", "PFL SCORE 0 0 0 0 0 -", "OK"])
        # once the table is gone, its turns are still the players' alone
        await self.srv.end_table(tid)
        await self.srv.end_table(tid)
        self.assertNotIn(tid, self.srv.tables)
        self.assertIn("TRX MACH Macintosh", await self.req(ann, "TRN %s" % tid))
        self.assertEqual(await self.req(cy, "TRN %s" % tid), ["ERR PRIVATE this table is by invitation only"])
        self.assertEqual((await self.req(cy, "TRN 999"))[-1], "ERR NOTABLE no such table")

    async def test_feed_newest_first_and_its_limits(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        await self.req(bob, "CLI PROG Netscape Navigator 3.0")
        await self.req(bob, "CLI MACH Macintosh")
        tid = await self.table(ann, bob)
        await self.req(ann, "MOV %s d3" % tid)
        await self.req(bob, "MOV %s c3" % tid)
        feed = await self.req(ann, "FEED")
        self.assertEqual(feed[-1], "OK 2")
        self.assertRegex(feed[0], r"^TRN %s 9 \S+ 1 bob reversi/1$" % tid)
        self.assertEqual(feed[1:4], ["TRX MOVE played c3, turning 1", "TRX PROG Netscape Navigator 3.0",
                                     "TRX MACH Macintosh"])
        self.assertEqual((await self.req(ann, "FEED 1"))[-1], "OK 1")
        self.assertEqual((await self.req(ann, "FEED 0"))[-1], "OK 1", "at least one")
        mine = await self.req(bob, "FEED 5 ann")
        self.assertEqual(mine[-1], "OK 1", "one player's turns")
        self.assertRegex(mine[0], r"^TRN %s 7 \S+ 0 ann reversi/1$" % tid)
        self.assertEqual(await self.req(bob, "FEED 5 nobody"), ["OK 0"])
        self.assertTrue((await self.req(ann, "FEED 5 a.b"))[-1].startswith("ERR SYNTAX"))
        self.assertTrue((await self.req(ann, "FEED x"))[-1].startswith("ERR SYNTAX"))
        from test_invites import FakeWriter
        from parlord.core import Session
        self.assertTrue((await self.req(Session(FakeWriter()), "FEED"))[-1].startswith("ERR NOHELO"))

    async def test_no_move_words_for_a_hidden_move(self):
        """A ship placed is not a turn: nothing public happened, so nothing is recorded."""
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = (await self.req(ann, "NEW navalcombat classic"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(bob, "JON %s" % tid)
        r = await self.req(ann, "MOV %s random" % tid)
        self.assertTrue(r[-1].startswith("OK"), r)
        self.assertEqual(await self.req(bob, "TRN %s" % tid), ["OK 0"])
        await self.req(ann, "MOV %s ready" % tid)
        self.assertIn("TRX MOVE readied the fleet", await self.req(bob, "TRN %s" % tid))

    async def test_kept_only_up_to_the_limit(self):
        old = Limits.turns_keep
        Limits.turns_keep = 1
        self.addCleanup(setattr, Limits, "turns_keep", old)
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.table(ann, bob)
        await self.req(ann, "MOV %s d3" % tid)
        await self.req(bob, "MOV %s c3" % tid)
        self.assertEqual((await self.req(ann, "FEED"))[-1], "OK 1")


class Profiles(Base):
    async def test_profile_over_the_protocol(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        self.assertEqual(await self.req(ann, "PFL"), ["PFL NAME ann", "PFL SCORE 0 0 0 0 0 -", "OK"])
        self.assertEqual(await self.req(ann, "PFL SET PREF  Macintosh\tSE/30 "), ["OK"])
        self.assertEqual(await self.req(bob, "PFL ANN"), ["PFL NAME ann", "PFL PREF Macintosh SE/30",
                                                             "PFL SCORE 0 0 0 0 0 -", "OK"])
        self.assertEqual(await self.req(ann, "PFL SET PREF " + "m" * 60), ["OK"])
        self.assertEqual((await self.req(ann, "PFL"))[1], "PFL PREF " + "m" * 40)
        self.assertEqual(await self.req(ann, "PFL SET PREF"), ["OK"])
        self.assertEqual(await self.req(ann, "PFL"), ["PFL NAME ann", "PFL SCORE 0 0 0 0 0 -", "OK"])
        self.assertEqual(await self.req(ann, "PFL nobody"), ["ERR NOPLAYER no player by that name"])
        self.assertTrue((await self.req(ann, "PFL SET MOOD happy"))[-1].startswith("ERR SYNTAX"))
        self.assertTrue((await self.req(ann, "PFL a b"))[-1].startswith("ERR SYNTAX"))
        # what was played at public tables is counted
        for s, (prog, mach) in ((ann, OUTLAND), (bob, APPLE)):
            await self.req(s, "CLI PROG " + prog)
            await self.req(s, "CLI MACH " + mach)
        tid = (await self.req(ann, "NEW reversi standard size=4"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(bob, "JON %s" % tid)
        await self.req(ann, "MOV %s b1" % tid)
        await self.req(bob, "RES %s" % tid)
        out = await self.req(bob, "PFL ann")
        self.assertEqual(out, ["PFL NAME ann", "PFL SCORE 4 1 0 0 0 1", "PFL GAME reversi/1 1",
                               "PFL PROG 1 " + OUTLAND[0],
                               "PFL MACH 1 " + OUTLAND[1], "OK"])
        self.assertEqual(await self.req(ann, "PFL bob"), ["PFL NAME bob", "PFL SCORE 0 0 0 0 1 2",
                                                          "PFL GAME reversi/1 1", "OK"],
                         "bob resigned before he played a turn")

    async def test_deleting_an_account_deletes_its_profile_and_turns(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        await self.req(ann, "PFL SET PREF Macintosh Plus")
        await self.req(ann, "CLI PROG Eclipse")
        tid = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(bob, "JON %s" % tid)
        await self.req(ann, "MOV %s d3" % tid)
        await self.srv.delete_player(self.srv.players["ann"])
        db = self.srv.store.db
        self.assertEqual(db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM playstats WHERE player='ann'").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE client!=''").fetchone()[0], 0)


class Migration(unittest.TestCase):
    def test_an_older_database_gains_the_columns_and_keeps_its_events(self):
        d = tempfile.mkdtemp(prefix="pnturns")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "parlor.db")
        db = sqlite3.connect(path)             # the events table as it was before CLI
        db.execute("CREATE TABLE events (table_id TEXT NOT NULL, seq INTEGER NOT NULL, text TEXT NOT NULL,"
                   " at REAL NOT NULL, PRIMARY KEY (table_id, seq))")
        db.execute("INSERT INTO events VALUES ('7', 1, 'JOIN 0 ann', 1.0)")
        db.commit()
        db.close()
        s = Store(path)
        cols = [r[1] for r in s.db.execute("PRAGMA table_info(events)").fetchall()]
        self.assertTrue({"who", "client", "machine"} <= set(cols))
        self.assertEqual(s.load_events("7", 10), [(1, "JOIN 0 ann")])
        s.add_event("7", 2, "PUT 0 d3", ("ann", "Eclipse", "Macintosh"))
        self.assertEqual(s.db.execute("SELECT who, client, machine FROM events WHERE seq=2").fetchone(),
                         ("ann", "Eclipse", "Macintosh"))
        s.db.close()
        Store(path).db.close()          # a second start changes nothing


class TurnsAcrossRestart(Base):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pnturns")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "parlor.db")

    async def test_turns_and_profiles_survive(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        await self.req(ann, "CLI PROG %s" % OUTLAND[0])
        await self.req(ann, "PFL SET PREF Macintosh LC")
        tid = (await self.req(ann, "NEW reversi standard"))[-1].split()[1]
        await self.req(ann, "JON %s" % tid)
        await self.req(bob, "JON %s" % tid)
        await self.req(ann, "MOV %s d3" % tid)
        srv = await self.server(self.db)
        ann2 = await self.player("ann", srv=srv)
        self.assertIn("TRX PROG " + OUTLAND[0], await self.req(ann2, "TRN %s" % tid, srv))
        self.assertIn("PFL PREF Macintosh LC", await self.req(ann2, "PFL", srv))


class SayOps(unittest.TestCase):
    """Each game's plugin says a turn in words from its public events (the say request, docs/plugins.md)."""

    def plugin(self, game):
        import importlib.util
        spec = importlib.util.spec_from_file_location("say_" + game, os.path.join(ROOT, "plugins", game, "plugin.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertTrue(m.OPS["info"]({}).get("say"))
        return lambda seat, *evs: m.OPS["say"]({"seat": seat, "events": list(evs)})["say"]

    def test_every_game(self):
        self.assertEqual(self.plugin("gridwords")(0, "PLAY 0 8 8 H FRAULEIN 65", "DRAW 0 7", "TURN 1"),
                         "played FRAULEIN for 65")
        self.assertEqual(self.plugin("gridwords")(1, "EXCH 1 3"), "exchanged 3 tiles")
        self.assertEqual(self.plugin("reversi")(0, "PUT 0 d3 FLP d4", "TURN 1"), "played d3, turning 1")
        self.assertEqual(self.plugin("backgammon")(0, "ROLL 0 6 3", "STEP 0 13 7", "STEP 0 8 5 HIT", "UNDO 0 8 5",
                                                   "STEP 0 7 4", "TURN 1", "ROLL 1 2 2"),
                         "rolled 6-3, moved 13/7 7/4")
        self.assertEqual(self.plugin("checkers")(0, "JUMP 0 15 22 18", "JUMP 0 22 29 25", "CROWN 0 29"),
                         "jumped 15x22x29, crowned")
        self.assertEqual(self.plugin("fiveinarow")(1, "PUT 1 h8"), "played h8")
        self.assertEqual(self.plugin("mancala")(0, "SOW 0 3 4 x", "CAP 0 5"), "sowed pit 3, capturing 5")
        self.assertEqual(self.plugin("dotsandboxes")(0, "LINE 0 a1-b1 BOX a1 b1"), "drew a1-b1 and took 2 boxes")
        self.assertEqual(self.plugin("navalcombat")(1, "SHOT 1 b4 sunk 3 b2 b3 b4"), "fired at b4: sank a 3-square ship")
        self.assertEqual(self.plugin("cannonfire")(2, "FIRE 2 45 60", "TRJ 1 2", "DMG 0 40"),
                         "fired at angle 45, power 60")
        self.assertEqual(self.plugin("rummytile")(0, "PUT 0 R5,R6,R7 n", "MELD 0 30", "DONE 0 3"),
                         "made the first meld, 30 points")
        self.assertEqual(self.plugin("rummytile")(0, "DRAW 0 40"), "drew a tile")
        self.assertEqual(self.plugin("reversi")(1, "PUT 0 d3 FLP d4"), "", "another seat's events say nothing")


del Base

if __name__ == "__main__":
    unittest.main()
