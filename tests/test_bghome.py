"""The Backgammon home board's side (PRF bghome, adapters/common.py): "right" draws points 1-6 at the bottom right
as always; "left" draws the mirror image on the web board image and the telnet board. Only the drawing and the
mapping of clicks to points change: the moves sent and the point numbers shown stay the same."""
import asyncio
import os
import re
import sys
import unittest

from game_checks import ansi_screens, plain_screen
from test_adapters import Browser, Native, Stack

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters import board  # noqa: E402
from adapters.common import BG_HOME_PREF, BG_HOMES  # noqa: E402
from adapters.games import backgammon as BG  # noqa: E402
from adapters.telnet import Canvas  # noqa: E402

# seat 0 to move with a checker on the bar; a stack of 7 on point 6 and one borne off by seat 1
LINES = ["V backgammon/1 7", "P 1 -2 0 0 0 0 7 0 3 0 0 0 -5", "P 13 4 0 0 0 -3 0 -4 0 0 0 0 0",
         "B 1 0", "O 0 1", "D 3 5", "C 2 0", "W 0", "Q move", "M bar-22", "M bar-20", "S White to move"]
TRAY = (60, 44, 28)


def image(home, seat=0, sel="bar"):
    v = BG.parse_view(LINES)
    if home:
        v["home"] = home
    return BG.board_image(v, seat, sel)


class WebImage(unittest.TestCase):
    def test_right_is_the_layout_of_before(self):
        self.assertEqual(image(None).gif(), image("right").gif())
        self.assertEqual(BG_HOMES[0], "right")

    def test_left_is_the_mirror_image(self):
        """Every clickable area of the left layout is the mirror of the right one's, with the same link; the
        rows with no text on them are the right layout's pixels in reverse."""
        for seat, sel in ((0, "bar"), (0, ""), (1, ""), (-1, "")):
            right, left = image("right", seat, sel), image("left", seat, sel)
            self.assertEqual((left.w, left.h), (right.w, right.h))
            w = right.w

            def mirrored(areas):
                return sorted((shape, (w - 1 - x2, y1, w - 1 - x1, y2), alt, path, params)
                              for shape, (x1, y1, x2, y2), alt, path, params in areas)
            self.assertEqual(mirrored(right.areas), sorted(left.areas), (seat, sel))
            y_top = BG.BG_LABEL + BG.BG_FRAME
            for y in (y_top + 2, y_top + 40, y_top + 2 * BG.BG_H * BG.BG_SLICES + BG.BG_MID - 3):
                row_r = right.px[y * w:(y + 1) * w]
                row_l = left.px[y * w:(y + 1) * w]
                self.assertEqual(bytes(row_l), bytes(row_r[::-1]), (seat, sel, y))

    def test_points_and_tray_move_to_the_left(self):
        """Point 1 at the bottom left and 24 at the top left (the tray beside them); the area for entering at
        22 is on the left half, and still plays bar-22."""
        for home, near in (("right", False), ("left", True)):
            c = image(home)
            gif = board.decode_gif(c.gif())
            y = BG.BG_LABEL + BG.BG_FRAME + BG.BG_H * BG.BG_SLICES + BG.BG_MID + 40     # below the cube
            left_px = gif.palette[gif.pixels[y * gif.w + BG.BG_FRAME + 5]]
            right_px = gif.palette[gif.pixels[y * gif.w + gif.w - BG.BG_FRAME - 5]]
            self.assertEqual(left_px == TRAY, near, home)
            self.assertEqual(right_px == TRAY, not near, home)
            area = next(a for a in c.areas if a[2] == "bar to 22")
            self.assertEqual(dict(area[4]), {"mv": "bar-22"})
            self.assertEqual(area[1][2] < c.w // 2, near, home)
            self.assertEqual(board.hit(c.areas, (area[1][0] + area[1][2]) // 2, area[1][1] + 3), area)

    def test_tall_stacks_show_plus_the_rest(self):
        """Five places on a point: 7 on point 6 is four checkers and +3; four on the bar is two and +2; five on
        a point are all drawn. No stack shows its total."""
        v = BG.parse_view(LINES)
        v["pts"][12] = 5
        v["bar"] = [4, 0]
        c = BG.board_image(v, 0, "")
        words = [w for w, _, _ in c.words]
        self.assertIn("+3", words)
        self.assertIn("+2", words)
        on_board = [w for w, _, y in c.words if BG.BG_LABEL < y < c.h - BG.BG_LABEL]
        self.assertNotIn("7", on_board)
        self.assertNotIn("4", on_board)
        checkers = [p for p, _, _ in c.used if p.startswith("/img/bg/") and p[10] in "wk"]
        self.assertEqual(len(checkers), 2 + 4 + 3 + 5 + 5 + 3 + 4)    # points 1, 6 (7), 8, 12, 13, 17, 19

    def test_side_panel_links_the_switch(self):
        from game_checks import web_page
        html = web_page(self, BG, LINES)
        self.assertIn("Your home board is on the right", html)
        self.assertRegex(html, r'href="/bghome\.htm\?[^"]*h=left')
        v = BG.parse_view(LINES)
        v["home"] = "left"
        parts = BG.web_parts(v, "3", lambda path, **q: path + "?" + "&".join("%s=%s" % kv for kv in q.items()),
                             0, ["ann", "bob"], "")
        self.assertIn("Your home board is on the left", parts[1])
        self.assertIn("h=right", parts[1])


class Telnet(unittest.TestCase):
    def board(self, home, me=0):
        class Sess:
            mode, io, core, name = "plain", None, None, "ann"
        s = Sess()
        s.bghome = home
        g = BG.PlainBackgammon(s, "3", "backgammon/1", me)
        g.v, g.names = BG.parse_view(LINES), ["ann", "bob"]
        cv = Canvas()
        g.draw_bg(cv)
        return g, cv, [l.rstrip() for l in cv.lines()]

    def test_left_is_the_mirror_image(self):
        _, _, right = self.board("right")
        g, cv, left = self.board("left")
        self.assertEqual(right[1].split(), ["13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24",
                                            "off"])
        self.assertEqual(left[1].split(), ["off", "24", "23", "22", "21", "20", "19", "18", "17", "16", "15", "14",
                                           "13"])
        self.assertEqual(left[19].split(), ["off"] + [str(n) for n in range(1, 13)])
        # the frame, the points and the checkers are the right layout's columns in reverse (a glyph's mirror
        # image is itself: "(O)", "\\:/"); the tray's slabs read left to right in either
        mirror = str.maketrans("()<>/\\", ")(><\\/")
        for y in range(2, 19):
            if y == 10:
                continue        # the middle row: the dice and BAR are text
            row_r, row_l = right[y].ljust(48), left[y].ljust(48)
            self.assertEqual(row_l[5:48], row_r[1:44][::-1].translate(mirror), y)
            self.assertEqual(row_l[1] + row_l[2:5].strip(), row_r[47] + row_r[44:47].strip(), y)
        self.assertEqual(left[10].index("BAR"), 25)

    def test_clicks_and_cursor_map_to_the_points(self):
        """In the left layout a click at the bottom left is point 1 and the left arrow moves toward 12."""
        g, cv, _ = self.board("left")
        self.assertEqual(g.tok_at(cv.hit(7, 15)[1:]), "1")
        self.assertEqual(g.tok_at(cv.hit(44, 15)[1:]), "12")
        self.assertEqual(g.tok_at(cv.hit(3, 15)[1:]), "off")
        self.assertEqual(g.tok_at(cv.hit(26, 5)[1:]), "bar")
        self.assertEqual(g.tok_at(cv.hit(13, 5)[1:]), "22")
        g, cv, _ = self.board("right")
        self.assertEqual(g.tok_at(cv.hit(40, 15)[1:]), "1")
        self.assertEqual(g.tok_at(cv.hit(45, 15)[1:]), "off")

    def test_full_screen_plays_the_same_moves(self):
        """Clicks on the bar then on 22 in the left layout send bar-22; a typed 13-7 is sent as typed; the
        arrows move the cursor the way they point."""
        def left(g):
            g.s.bghome = "left"
        for keys, sent in (([("CLICK", 26, 12), "WAIT", ("CLICK", 13, 5)], "MOV 3 bar-22"),
                           (["1", "3", "-", "7", "ENTER"], "MOV 3 13-7")):
            out = ansi_screens(self, BG, LINES, 0, keys, prepare=left)
            for mode, (_, reqs) in out.items():
                self.assertIn(sent, reqs, mode)

        def cursor(home, keys):
            """From point 1, after the arrow keys: (the point under the cursor, its screen column)."""
            class Sess:
                bghome = home
            g = BG.AnsiBackgammon.__new__(BG.AnsiBackgammon)     # only the cursor: no screen
            g.s, g.v, g.me, g.cur = Sess(), BG.parse_view(LINES), 0, (1, 12)
            for k in keys:
                g.on_arrow(k)
            return g.tok_at(g.cur), g.mx(BG.bg_x(g.cur[1]))
        self.assertEqual(cursor("right", []), ("1", 40))
        self.assertEqual(cursor("right", ["LEFT"]), ("2", 37))
        self.assertEqual(cursor("left", []), ("1", 6))
        self.assertEqual(cursor("left", ["RIGHT"]), ("2", 9))
        self.assertEqual(cursor("left", ["LEFT"]), ("off", 2))

    def test_home_command_keeps_the_choice(self):
        """home left (or H and a choice) keeps bghome in telnet's own data and draws the board mirrored; the
        kept value is read back at the next screen (load_home); a missing or odd value is right."""
        class Core:
            def __init__(self):
                self.kept = {}

            async def load_pref(self, key, choices, default):
                v = (self.kept.get(key) or "").lower()
                return v if v in choices else default

            async def save_pref(self, key, value):
                self.kept[key] = value
                return True
        core = Core()
        self.assertEqual(asyncio.run(BG.load_home(core)), "right")
        core.kept[BG_HOME_PREF] = "sideways"
        self.assertEqual(asyncio.run(BG.load_home(core)), "right")
        core.kept[BG_HOME_PREF] = "left"
        self.assertEqual(asyncio.run(BG.load_home(core)), "left")
        core.kept.clear()

        def with_core(g):
            g.core = core
        out = ansi_screens(self, BG, LINES, 0, ["h", "WAIT", "DOWN", "ENTER"], prepare=with_core)
        self.assertEqual(core.kept, {BG_HOME_PREF: "left"})
        for mode, (text, _) in out.items():
            self.assertIn("Your home board is on the left, kept for next time.", text, mode)
        core.kept.clear()
        out = ansi_screens(self, BG, LINES, 0, [":", "h", "o", "m", "e", " ", "l", "e", "f", "t", "ENTER"],
                           prepare=with_core)
        self.assertEqual(core.kept, {BG_HOME_PREF: "left"})
        text = plain_screen(self, BG, LINES, prepare=lambda g: setattr(g.s, "bghome", "left"))
        self.assertIn("  off  1  2  3  4  5  6", text)


class Web(Stack):
    """The Backgammon board page: kept with the player (PRF bghome, the web's namespace), and the board image
    and the table's switch follow it."""

    def test_page_saves_and_the_table_follows(self):
        b = Browser(self.web_port)
        b.login("bgh1", "bgh1-Pass-word")
        keep = b.keep()
        b.act = lambda path: b.get(path + ("&" if "?" in path else "?") + keep)
        st, _, _, body = b.act("/lobby.htm")
        self.assertIn("/bghome.htm", body)
        st, _, _, body = b.act("/bghome.htm")
        self.assertEqual(st, 200)
        self.assertIn("Backgammon board", body)
        self.assertRegex(body, r'value="right" checked')
        self.assertNotRegex(body.lower(), r"<style|style=|class=|id=")     # HTML 3.2
        st, _, _, body = b.get("/bghome.htm?h=left")                        # without the action check: no
        self.assertNotIn("Saved.", body)
        st, _, _, body = b.act("/bghome.htm?h=left")
        self.assertIn("Saved.", body)
        self.assertRegex(body, r'value="left" checked')
        st, _, _, body = b.act("/bghome.htm?h=sideways")                    # not a side: nothing changes
        self.assertNotIn("Saved.", body)
        self.assertRegex(body, r'value="left" checked')
        # a Backgammon table: drawn with the tray on the left, and its switch goes back to the table
        st, loc, _, body = b.act("/new.htm?g=backgammon/1&turn=1h")
        st, _, _, body = b.go(loc)
        tid = re.search(r"Backgammon - table (\d+)", body).group(1)
        n = Native(self.core, "bgh2", "bgh2-Pass-word")
        try:
            self.assertEqual(n.req("JON %s" % tid)[-1], "OK 1")

            def tray_left():
                st, _, _, body = b.act("/table.htm?t=%s" % tid)
                src = re.search(r'<img src="(/board.gif\?[^"]+)"', body).group(1).replace("&amp;", "&")
                gif = board.decode_gif(b.get(src)[3].encode("latin-1"))
                y = BG.BG_LABEL + BG.BG_FRAME + BG.BG_H * BG.BG_SLICES + BG.BG_MID + 40     # below the cube
                return body, gif.palette[gif.pixels[y * gif.w + BG.BG_FRAME + 5]] == TRAY
            body, left = tray_left()
            self.assertTrue(left)
            self.assertIn("Your home board is on the left", body)
            switch = b.link(r"/bghome\.htm\?.*h=right", body)
            st, loc, _, _ = b.go(switch)
            self.assertEqual(st, 302)
            self.assertIn("/table.htm?", loc)
            body, left = tray_left()
            self.assertFalse(left)
            self.assertIn("Your home board is on the right", body)
            st, _, _, body = b.act("/bghome.htm")
            self.assertRegex(body, r'value="right" checked')
        finally:
            n.close()


if __name__ == "__main__":
    unittest.main()
