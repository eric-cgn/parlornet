"""Scores and the leaderboard (docs/protocol.md, Scores): the points each ending gives (a win, a loss, a draw,
a resignation, a timeout; games of three or more with a winner, the others, players who left and a shared
win), bots left out, the point settings and scores.show, the reset that keeps the games' record, TOP and
PFL SCORE, a database from before scores, and the frontends: the web's leaderboard page, telnet's scores
command and the chat commands /top and /score."""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

from test_adapters import Browser, Stack
from test_adapters import query_of  # noqa: E402
from test_core import ROOT
import test_profiles_web as TPW
from test_table_notes import Notes

sys.path.insert(0, ROOT)
from parlord import admin as core_admin  # noqa: E402
from parlord.core import MAXLINE  # noqa: E402
from parlord.store import Store  # noqa: E402

Base = Notes.__bases__[0]     # the in-process core of test_table_notes


class Helpers(Base):
    async def table(self, game, *players, opts=""):
        """A running table of game with these players seated in order -> the Table."""
        first = players[0]
        n = len(players)
        rules = self.srv.plugins["%s/1" % game].info["rules"][0]
        tid = (await self.req(first, "NEW %s %s" % (game, opts or ("%s players=%d" % (rules, n)))))[-1]
        self.assertTrue(tid.startswith("OK"), tid)
        tid = tid.split()[1]
        for p in players:
            self.assertTrue((await self.req(p, "JON %s" % tid))[-1].startswith("OK"))
        t = self.srv.tables[tid]
        self.assertEqual(t.status, "playing")
        return t

    def score(self, name):
        return self.srv.store.score(name)

    def scored(self, tid):
        return dict((p, (pts, why)) for p, pts, why in self.srv.store.db.execute(
            "SELECT player, points, reason FROM scored WHERE table_id=?", (tid,)).fetchall())


class Scoring(Helpers):
    async def test_a_win_and_a_loss(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        t = await self.table("fiveinarow", ann, bob)
        for a, b in (("e5", "a9"), ("e4", "b9"), ("e3", "c9"), ("e2", "d9")):
            await self.req(ann, "MOV %s %s" % (t.id, a))
            await self.req(bob, "MOV %s %s" % (t.id, b))
        out = await self.req(ann, "MOV %s e6" % t.id)
        self.assertIn("EVT %s 22 END win 0 e2-e6" % t.id, out)
        self.assertEqual(self.score("ann"), (4, 1, 0, 0, 0, 1))
        self.assertEqual(self.score("bob"), (1, 0, 0, 1, 0, 1))
        self.assertEqual(self.scored(t.id), {"ann": (4, "win"), "bob": (1, "loss")})

    async def test_a_draw(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        t = await self.table("checkers", ann, bob)
        self.assertEqual((await self.req(ann, "MOV %s draw" % t.id))[-1], "OK")
        out = await self.req(bob, "MOV %s accept" % t.id)
        self.assertTrue(any(l.endswith("END draw agreed") for l in out), out)
        self.assertEqual(self.score("ann"), (2, 0, 1, 0, 0, 1))
        self.assertEqual(self.score("bob"), (2, 0, 1, 0, 0, 1))

    async def test_a_resignation(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        t = await self.table("reversi", ann, bob)
        await self.req(bob, "RES %s" % t.id)
        self.assertEqual(self.score("bob"), (0, 0, 0, 0, 1, 1))
        self.assertEqual(self.score("ann"), (4, 1, 0, 0, 0, 1))
        self.assertEqual(self.scored(t.id), {"ann": (4, "win"), "bob": (0, "resign")})

    async def test_a_timeout_scores_as_a_resignation(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        t = await self.table("reversi", ann, bob)
        t.deadline = time.time() - 1
        await self.srv.sweep()
        self.assertEqual(t.status, "done")
        self.assertEqual(self.scored(t.id), {"ann": (0, "timeout"), "bob": (4, "win")})
        self.assertEqual(self.score("ann"), (0, 0, 0, 0, 1, 1))

    async def test_three_players_winner_others_and_one_who_left(self):
        """A seat that resigned while the others played on gets 0; at the end the winner 4, the others 1."""
        ann, bob, cy = await self.player("ann"), await self.player("bob"), await self.player("cy")
        t = await self.table("dotsandboxes", ann, bob, cy)
        out = await self.req(cy, "RES %s" % t.id)
        self.assertTrue(any(l.endswith("GONE 2 resign") for l in out), out)
        self.assertEqual(self.score("cy"), (0, 0, 0, 0, 0, 0), "nothing until the game ends")
        self.srv.event(t, "END win 1 3-5-1")
        self.assertEqual(self.scored(t.id), {"ann": (1, "loss"), "bob": (4, "win"), "cy": (0, "resign")})

    async def test_three_players_one_times_out_then_one_resigns(self):
        ann, bob, cy = await self.player("ann"), await self.player("bob"), await self.player("cy")
        t = await self.table("dotsandboxes", ann, bob, cy)
        t.deadline = time.time() - 1
        await self.srv.sweep()                       # ann's turn runs out: she leaves, the others play on
        self.assertEqual(t.status, "playing")
        self.assertEqual(t.gone, {0})
        await self.req(cy, "RES %s" % t.id)          # two left: this ends the game
        self.assertEqual(t.status, "done")
        self.assertEqual(self.scored(t.id), {"ann": (0, "timeout"), "bob": (4, "win"), "cy": (0, "resign")})

    async def test_a_shared_win(self):
        """A tie for first: those tied draw (2 each), the rest of those still playing lose (1), whatever way the
        result lists the scores."""
        names = ["ann", "bob", "cy", "dee"]
        for end, share in (("draw 4-4-1-0", ("ann", "bob")), ("draw 10/-2/10/3", ("ann", "cy")),
                           ("draw 12 -4 12 -20", ("ann", "cy"))):
            with self.subTest(end=end):
                ps = [await self.player(n) for n in names]
                t = await self.table("gridwords", *ps, opts="standard players=4")
                self.srv.event(t, "END " + end)
                self.assertEqual(self.scored(t.id), {n: (2, "draw") if n in share else (1, "loss") for n in names})

    async def test_a_shared_win_the_plugin_names(self):
        """Cannon Fire: the last tanks destroyed each other. The plugin names them (tied), whatever the points."""
        ann, bob, cy = await self.player("ann"), await self.player("bob"), await self.player("cy")
        t = await self.table("cannonfire", ann, bob, cy)
        self.srv.event(t, "END draw 90-10-20", tied=[1, 2])
        self.assertEqual(self.scored(t.id), {"ann": (1, "loss"), "bob": (2, "draw"), "cy": (2, "draw")})

    def test_cannon_fire_names_the_tanks_that_share_a_draw(self):
        from test_cannonfire import aim_at, fire, flat
        st = flat(xs=(10, 12))
        st["tanks"] = [[10, 10], [12, 10]]
        r = fire(st, 0, *aim_at(st, 0, 12))
        self.assertTrue(r["end"].startswith("draw "))
        self.assertEqual(r["tied"], [0, 1])

    async def test_an_abandoned_game_scores_nothing(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        t = await self.table("reversi", ann, bob)
        await self.srv.end_table(t.id)
        self.assertEqual(self.scored(t.id), {})
        self.assertEqual(self.score("ann"), (0,) * 6)

    async def test_the_bot_is_not_scored_and_not_listed(self):
        ann, robo = await self.player("ann"), await self.player("robo", bot=True)
        t = await self.table("reversi", ann, robo)
        await self.req(ann, "RES %s" % t.id)
        self.assertEqual(self.scored(t.id), {"ann": (0, "resign")}, "the game against the bot counts")
        # a bot's row (from before it was flagged, say) is never listed either
        self.srv.store.add_scores("x1", "reversi/1", [("robo", 4, "win")])
        top = await self.req(ann, "TOP")
        self.assertEqual(top, ["TOP 1 ann 0 0 0 0 1", "OK 1"])
        self.assertNotIn("PFL SCORE", " ".join(await self.req(ann, "PFL robo")))

    async def test_a_game_counts_once(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        t = await self.table("reversi", ann, bob)
        await self.req(bob, "RES %s" % t.id)
        self.srv.score_game(t, ["resign", "1"])
        self.assertEqual(self.score("ann"), (4, 1, 0, 0, 0, 1))

    async def test_settings_change_the_points(self):
        for k, v in (("scores.win", "3"), ("scores.loss", "0"), ("scores.draw", "1"), ("scores.resign", "2")):
            self.srv.set_setting(k, v)
        ann, bob, cy = await self.player("ann"), await self.player("bob"), await self.player("cy")
        t = await self.table("dotsandboxes", ann, bob, cy)
        await self.req(cy, "RES %s" % t.id)
        self.srv.event(t, "END win 0 5-3-1")
        self.assertEqual(self.scored(t.id), {"ann": (3, "win"), "bob": (0, "loss"), "cy": (2, "resign")})
        t = await self.table("checkers", ann, bob)
        await self.req(ann, "MOV %s draw" % t.id)
        await self.req(bob, "MOV %s accept" % t.id)
        self.assertEqual(self.scored(t.id), {"ann": (1, "draw"), "bob": (1, "draw")})
        # a value out of range or not a number is the default; empty is the default
        self.srv.set_setting("scores.win", "1000")
        self.assertEqual(self.srv.points("win"), 4)
        self.srv.set_setting("scores.win", None)
        self.assertEqual(self.srv.points("win"), 4)
        self.assertEqual(self.srv.points("timeout"), 2, "a timeout scores as a resignation")

    async def test_the_admin_settings_and_the_reset(self):
        a = core_admin.Admin(self.srv)
        conn = core_admin.AdminConn()
        conn.state = "admin"
        self.assertEqual(a.op_set(conn, {"key": "scores.win", "value": "5"}), {"ok": True})
        self.assertEqual(a.op_set(conn, {"key": "scores.resign", "value": "0"}), {"ok": True})
        self.assertEqual(self.srv.settings["scores.resign"], "0", "0 is a value here, not the default")
        self.assertFalse(a.op_set(conn, {"key": "scores.win", "value": "100"})["ok"])
        self.assertFalse(a.op_set(conn, {"key": "scores.win", "value": "x"})["ok"])
        self.assertEqual(a.op_set(conn, {"key": "scores.win", "value": ""}), {"ok": True})
        self.assertNotIn("scores.win", self.srv.settings)
        self.assertFalse(a.op_set(conn, {"key": "scores.show", "value": "maybe"})["ok"])
        self.assertEqual(a.op_set(conn, {"key": "scores.show", "value": "off"}), {"ok": True})
        g = a.op_get(conn, {})
        self.assertEqual(g["effective"]["scores.show"], "off")
        self.assertEqual(g["effective"]["scores.win"], "4")
        self.assertEqual(g["effective"]["scores.resign"], "0")
        a.op_set(conn, {"key": "scores.show", "value": "on"})
        ann, bob = await self.player("ann"), await self.player("bob")
        t = await self.table("reversi", ann, bob)
        await self.req(bob, "RES %s" % t.id)
        self.assertEqual(a.op_reset_scores(conn, {}), {"ok": True, "players": 2})
        self.assertEqual(self.score("ann"), (0,) * 6)
        self.assertEqual(await self.req(ann, "TOP"), ["OK 0"])
        self.assertEqual(await self.req(ann, "TOP game=reversi"), ["OK 0"], "the per-game board starts over too")
        # the history stays: the table, its events, its turns and the record of its points
        self.assertEqual(self.scored(t.id), {"ann": (4, "win"), "bob": (0, "resign")})
        self.assertIn(t.id, self.srv.tables)
        self.assertTrue(self.srv.store.load_events(t.id, 500))
        t2 = await self.table("reversi", ann, bob)
        await self.req(ann, "RES %s" % t2.id)
        self.assertEqual(await self.req(ann, "TOP"), ["TOP 1 bob 4 1 0 0 0", "TOP 2 ann 0 0 0 0 1", "OK 2"])

    async def test_top_and_pfl(self):
        ann, bob, cy = await self.player("ann"), await self.player("bob"), await self.player("cy")
        for loser, game in ((bob, "reversi"), (cy, "reversi"), (cy, "mancala")):
            t = await self.table(game, ann, loser)
            await self.req(loser, "RES %s" % t.id)
        t = await self.table("checkers", bob, cy)
        await self.req(bob, "MOV %s draw" % t.id)
        await self.req(cy, "MOV %s accept" % t.id)
        self.assertEqual(await self.req(bob, "TOP"),
                         ["TOP 1 ann 12 3 0 0 0", "TOP 2 bob 2 0 1 0 1", "TOP 2 cy 2 0 1 0 2", "OK 3"])
        self.assertEqual(await self.req(bob, "TOP 1"), ["TOP 1 ann 12 3 0 0 0", "OK 3"])
        self.assertEqual(await self.req(bob, "TOP 2 game=reversi"),
                         ["TOP 1 ann 8 2 0 0 0", "TOP 2 bob 0 0 0 0 1", "OK 3"])
        self.assertEqual(await self.req(bob, "TOP game=checkers/1"),
                         ["TOP 1 bob 2 0 1 0 0", "TOP 1 cy 2 0 1 0 0", "OK 2"])
        self.assertEqual(await self.req(bob, "TOP game=chess"), ["ERR NOGAME this server does not host that game"])
        for bad in ("TOP x", "TOP 1 2", "TOP game=", "TOP game=a game=b"):
            self.assertTrue((await self.req(bob, bad))[-1].startswith("ERR SYNTAX"), bad)
        self.assertEqual(len(await self.req(bob, "TOP 500")), 4, "at most top_max, here all 3")
        self.assertEqual((await self.req(bob, "PFL ann"))[1], "PFL SCORE 12 3 0 0 0 1")
        self.assertEqual((await self.req(bob, "PFL cy"))[1], "PFL SCORE 2 0 1 0 2 2")
        dee = await self.player("dee")
        self.assertEqual(await self.req(dee, "PFL"), ["PFL NAME dee", "PFL SCORE 0 0 0 0 0 -", "OK"])
        # TOP before signing in
        from test_invites import FakeWriter
        from parlord.core import Session
        self.assertEqual((await self.req(Session(FakeWriter()), "TOP 1")), ["TOP 1 ann 12 3 0 0 0", "OK 3"])

    async def test_lines_fit(self):
        names = ["abcdefghijkl", "mnopqrstuvwx"]
        a, b = [await self.player(n) for n in names]
        self.srv.store.add_scores("x", "reversi/1", [(n, 10 ** 9, "win") for n in names])
        for line in (await self.req(a, "TOP 50")) + (await self.req(a, "PFL " + names[1])):
            self.assertLessEqual(len(line) + 2, MAXLINE, line)

    async def test_scores_show_off(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        t = await self.table("reversi", ann, bob)
        self.srv.set_setting("scores.show", "off")
        await self.req(bob, "RES %s" % t.id)
        self.assertEqual(self.score("ann")[0], 4, "games are still scored")
        self.assertEqual(await self.req(ann, "TOP"), ["ERR NOSCORES this server shows no scores"])
        self.assertEqual(await self.req(ann, "PFL ann"), ["PFL NAME ann", "PFL GAME reversi/1 1", "OK"])
        self.srv.set_setting("scores.show", "on")
        self.assertEqual((await self.req(ann, "TOP"))[0], "TOP 1 ann 4 1 0 0 0")

    async def test_deleting_an_account_deletes_its_score(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        t = await self.table("reversi", ann, bob)
        await self.req(bob, "RES %s" % t.id)
        await self.srv.delete_player(self.srv.players["bob"])
        self.assertEqual(await self.req(ann, "TOP"), ["TOP 1 ann 4 1 0 0 0", "OK 1"])
        self.assertEqual(self.scored(t.id), {"ann": (4, "win")})


class ChatCommands(Helpers):
    """/top and /score (adapters/chatcmd.py) against the core in-process."""

    def console(self, sess):
        from adapters import chatcmd
        said = []

        async def req(text):
            out = await self.req(sess, text)
            return out[:-1], out[-1]
        return chatcmd.Console(said.extend, req, sess.player.name, chatcmd.NAMES), said

    async def test_top_and_score(self):
        from adapters.common import SCORES_NOTE
        ann, bob = await self.player("ann"), await self.player("bob")
        c, said = self.console(bob)
        await c.handle("/top")
        self.assertEqual(said, ["No scores yet.", SCORES_NOTE])
        t = await self.table("reversi", ann, bob)
        await self.req(bob, "RES %s" % t.id)
        del said[:]
        await c.handle("/top")
        self.assertEqual(said, ["Top: 1 ann 4, 2 bob 0.", SCORES_NOTE])
        del said[:]
        await c.handle("/score")
        await c.handle("/score ann")
        await c.handle("/score nobody")
        await c.handle("/score a.b")
        self.assertEqual(said, ["bob: 0 points: 0 wins, 0 draws, 0 losses, 1 resigned, number 2.",
                                "ann: 4 points: 1 win, 0 draws, 0 losses, 0 resigned, number 1.",
                                "No player called nobody.", "Type /score or /score NAME."])
        from adapters import chatcmd
        self.assertEqual(chatcmd.help_lines(["top", "score"]),
                         ["/top: the leaderboard", "/score [NAME]: your score, or NAME's"])
        self.srv.set_setting("scores.show", "off")
        del said[:]
        await c.handle("/top")
        await c.handle("/score")
        self.assertEqual(said, ["This server shows no scores.", "This server shows no scores."])

    def test_every_console_offers_them(self):
        from adapters import awale, batnav, netluff, nsp_netboxes, outland
        for m in (awale, batnav, netluff, nsp_netboxes, outland):
            self.assertTrue({"top", "score"} <= set(m.COMMANDS), m.__name__)


TELNET_LOGIN = ((b"new", "Name you want"), (b"tess", "Choose a password"), (b"tess-Pass-word", "again"),
                (b"tess-Pass-word", "Press Enter"), (b"", "or /help for the commands."))


class WebAndTelnet(Stack):
    """The leaderboard page, the score on a profile, the links to it, and telnet's scores command."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ann, cls.bob = Browser(cls.web_port), Browser(cls.web_port)
        cls.ann.login("ann", "ann-Pass-word")
        cls.bob.login("bob", "bob-Pass-word")
        tid = query_of(cls.ann.act("/new.htm?g=reversi%2F1&turn=1h")[1])["t"][0]
        cls.bob.act("/join.htm?t=%s" % tid)
        cls.bob.act("/resign.htm?t=%s&sure=1" % tid)

    def test_the_leaderboard_page(self):
        from adapters.common import SCORES_NOTE
        from test_site import check
        st, _, _, page = self.get("/leaderboard.htm")          # no sign-in needed
        self.assertEqual(st, 200)
        self.assertEqual(check(page).problems, [])
        self.assertIn('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">', page)
        text = re.sub(r"<[^>]+>", "", page)
        self.assertIn(SCORES_NOTE, text)
        self.assertRegex(page, r'<a href="/player.htm\?who=ann">ann</a>.*?>4<')
        self.assertIn("http-equiv=\"refresh\"", page)
        st, _, _, page = self.get("/leaderboard.htm?g=reversi%2F1")
        self.assertEqual(st, 200)
        self.assertIn("Leaderboard: Reversi", page)
        self.assertIn(SCORES_NOTE, re.sub(r"<[^>]+>", "", page))
        self.assertEqual(self.get("/leaderboard.htm?g=chess%2F1")[0], 404)

    def test_links_and_the_profile(self):
        self.assertIn('href="/leaderboard.htm"', self.bob.get("/lobby.htm")[3])
        self.assertIn('href="/leaderboard.htm"', self.get("/welcome.htm")[3])    # the Games section, for everyone
        page = self.bob.get("/player.htm?who=ann")[3]
        self.assertIn("Score: <b>4 points: 1 win, 0 draws, 0 losses, 0 resigned</b>, number 1 on the leaderboard",
                      page)
        self.assertIn('href="/leaderboard.htm"', page)

    def test_telnet_scores(self):
        from adapters.common import SCORES_NOTE
        s = telnet_session(self)
        s.sendall(b"top\r\n")                          # the shared /top and /score, run on the server
        out = self.until(s, "parties.")
        s.sendall(b"score ann\r\n")
        prof = self.until(s, "number 1")
        s.close()
        self.assertIn("Leaderboard:", out)
        self.assertIn("Top: 1 ann 4, 2 bob 0.", out)
        self.assertIn(" ".join(SCORES_NOTE.split()[:6]), " ".join(out.split()))
        self.assertIn("4 points: 1 win", prof)

    until = TPW.TelnetProfiles.until


def telnet_session(test):
    s = TPW.TelnetProfiles.connect(test, "VT100")
    test.until(s, "Login")
    for send, want in TELNET_LOGIN:
        s.sendall(send + b"\r\n")
        test.assertIn(want, test.until(s, want))
    return s


class ScoresOff(Stack):
    """scores.show off (here from the settings file): no leaderboard page, link, command or score."""

    @classmethod
    def setUpClass(cls):
        cls.settings = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        cls.settings.write("scores.show=off\n")
        cls.settings.close()
        cls.core_extra = ("--settings-file", cls.settings.name)
        super().setUpClass()
        cls.ann = Browser(cls.web_port)
        cls.ann.login("ann", "ann-Pass-word")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        os.remove(cls.settings.name)

    def test_nothing_shows(self):
        self.assertEqual(self.get("/leaderboard.htm")[0], 404)
        self.assertNotIn("/leaderboard.htm", self.ann.get("/lobby.htm")[3])
        self.assertNotIn("/leaderboard.htm", self.get("/welcome.htm")[3])
        self.assertNotIn("Score:", self.ann.get("/player.htm")[3])
        s = telnet_session(self)
        s.sendall(b"top\r\n")
        out = self.until(s, "scores.")
        s.close()
        self.assertIn("This server shows no scores.", out)

    until = TPW.TelnetProfiles.until


class OlderDatabase(unittest.TestCase):
    def test_a_database_from_before_scores_gains_the_tables(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "p.db")
            Store(path).db.close()
            con = sqlite3.connect(path)
            con.execute("DROP TABLE scores")
            con.execute("DROP TABLE scored")
            con.execute("INSERT INTO meta (key, value) VALUES ('next_id', '7')")
            con.commit()
            con.close()
            st = Store(path)
            self.assertEqual(st.get("next_id"), "7", "everything else is kept")
            st.add_scores("1", "reversi/1", [("ann", 4, "win")])
            self.assertEqual(st.score("ann"), (4, 1, 0, 0, 0, 1))
            st.db.close()
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
