"""Grid Words colors (adapters/palette.py, docs/design/gridwords-colours.md): the default and the presets keep
clear of the well-known crossword game's look and read well with every common color-vision deficiency; a
player's own colors are kept with PRF and drawn on the web board and in telnet's ANSI colors."""
import asyncio
import os
import re
import sys
import unittest

from test_adapters import Browser, Native, Stack

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters import board, palette as PAL  # noqa: E402
from adapters.games import gridwords as G  # noqa: E402


class Presets(unittest.TestCase):
    def test_default_is_slate(self):
        self.assertEqual(PAL.DEFAULT, "slate")
        self.assertEqual(list(PAL.PRESETS)[0], PAL.DEFAULT)
        self.assertEqual(PAL.palette(None), PAL.PRESETS["slate"])
        self.assertEqual(PAL.palette("not a palette"), PAL.PRESETS["slate"])

    def test_not_the_trade_dress(self):
        """No preset puts a premium near any of red, pink, dark blue or light blue, its tiles near beige or its
        board near green or tan (CIEDE2000; 2 is just noticeable, over 10 clearly another color)."""
        for name, pal in PAL.PRESETS.items():
            for d, k, ref in PAL.trade_dress_distance(pal):
                need = 12 if k == "board" else 14
                self.assertGreaterEqual(d, need, (name, k, ref, d))
        # and the board images no longer use the old colors at all
        old = {rgb for refs in PAL.TRADE_DRESS.values() for rgb in refs}
        self.assertFalse(old & set(G.PALETTE))

    def test_readable_with_colour_vision_deficiencies(self):
        """Board, premiums and tiles differ clearly as seen normally and with protanopia, deuteranopia and
        tritanopia; labels and letters have WCAG contrast (4.5 and 7)."""
        for name, pal in PAL.PRESETS.items():
            self.assertEqual(PAL.problems(pal), [], name)

    def test_problems_finds_a_bad_palette(self):
        bad = dict(PAL.PRESETS["slate"], tw=(200, 40, 40), dw=(40, 160, 40))     # red and green
        self.assertTrue(any("deuteranopia" in t or "protanopia" in t for t in PAL.problems(bad)))
        grey = dict(PAL.PRESETS["slate"], letter=(240, 240, 240))
        self.assertTrue(any("letters on tiles" in t for t in PAL.problems(grey)))

    def test_colour_arithmetic(self):
        self.assertAlmostEqual(PAL.contrast((0, 0, 0), (255, 255, 255)), 21.0, places=3)
        self.assertAlmostEqual(PAL.delta_e((10, 20, 30), (10, 20, 30)), 0.0)
        # a reference pair from Sharma, Wu and Dalal's CIEDE2000 test data, in Lab: checked through lab()
        self.assertLess(PAL.delta_e((255, 0, 0), (254, 0, 0)), 1.0)
        self.assertGreater(PAL.delta_e((255, 0, 0), (0, 128, 0)), 50)
        # a dichromat sees red and green alike
        red, green = (200, 40, 40), (90, 140, 40)
        self.assertLess(PAL.delta_e(PAL.simulate(red, "deuteranopia"), PAL.simulate(green, "deuteranopia")),
                        PAL.delta_e(red, green) / 2)


class Values(unittest.TestCase):
    def test_encode_and_decode(self):
        for name, pal in PAL.PRESETS.items():
            self.assertEqual(PAL.encode(pal), name)
            self.assertEqual(PAL.decode(name), pal)
        mine = dict(PAL.PRESETS["slate"], tw=(1, 2, 3))
        v = PAL.encode(mine)
        self.assertTrue(v.startswith("custom:"))
        self.assertLessEqual(len(v), 56)                    # a PRF value
        self.assertRegex(v, r"^[\x20-\x7e]+\Z")
        self.assertEqual(PAL.decode(v), mine)
        self.assertEqual(PAL.decode(v.upper()), mine)
        for junk in ("", "custom:", "custom:1,2,3", "custom:" + ",".join(["zzzzzz"] * 7), "slate2"):
            self.assertIsNone(PAL.decode(junk), junk)

    def test_from_fields(self):
        f = {"preset": "daylight"}
        self.assertEqual(PAL.from_fields(f.get), PAL.PRESETS["daylight"])
        f = dict(preset="custom", **{k: "#%02x0000" % i for i, k in enumerate(PAL.ELEMENTS)})
        self.assertEqual(PAL.from_fields(f.get)["dw"], (2, 0, 0))
        f["tile"] = "beige"
        self.assertIsNone(PAL.from_fields(f.get))
        self.assertIsNone(PAL.from_fields({"preset": "nope"}.get))

    def test_client_json(self):
        import json
        d = json.loads(PAL.client_json())
        self.assertEqual(d["default"], "slate")
        self.assertEqual([p["id"] for p in d["presets"]], list(PAL.PRESETS))
        self.assertEqual(d["presets"][0]["colours"]["tw"], "#e07b00")
        self.assertIn("label_tw", d["presets"][0]["colours"])


class Images(unittest.TestCase):
    def test_board_in_the_players_colours(self):
        geo = ["BRD 15 15", "CTR 8 8", "PRM TW 1 1", "PRM DL 4 1"]
        lines = ["V gridwords/1 3"] + geo + ["X 8 8 A 9 8 T", "R ABC", "K 0 3", "K 1 7", "BAG 80", "T 0 2",
                                             "T 1 0", "W 1"]
        for value, pal in (("slate", PAL.PRESETS["slate"]), ("daylight", PAL.PRESETS["daylight"]),
                           ("custom:123456,ff0000,00ff00,0000ff,ffff00,eeeeee,000000", None)):
            pal = pal or PAL.decode(value)
            v = G.parse_view(lines)
            v["palette"] = value
            c = G.board_image(v, 0, "")
            img = board.decode_gif(c.gif())
            colours = set(img.palette)
            for k in PAL.ELEMENTS:
                self.assertIn(tuple(pal[k]), colours, (value, k))
        # the digest of the view (the image's address) changes with the colors
        v1, v2 = G.parse_view(lines), G.parse_view(lines)
        v1["palette"], v2["palette"] = "slate", "mono"
        self.assertNotEqual(board.view_digest(v1), board.view_digest(v2))

    def test_swatch(self):
        self.assertIsNone(G.swatch("junk"))
        img = board.decode_gif(G.swatch("custom:123456,ff0000,00ff00,0000ff,ffff00,eeeeee,000000"))
        self.assertIn((255, 0, 0), img.palette)
        self.assertEqual((img.w, img.h), (145, 25))


class Telnet(unittest.TestCase):
    def test_presets_map_to_distinct_ansi_colours(self):
        for name, pal in PAL.PRESETS.items():
            m = PAL.ansi_map(pal)
            self.assertEqual(len(set(m.values())), len(m), name)
            at = G.colour_attrs(name)
            self.assertEqual(len({at[k] for k in ("", "TW", "DW", "TL", "DL", "tile")}), 6, name)
            for k, attr in at.items():
                self.assertRegex(attr, r"^0(;[0-9]{1,2})+\Z", (name, k))

    def test_nearest(self):
        self.assertEqual(PAL.ANSI[PAL.nearest_ansi((10, 10, 10))][0], "black")
        self.assertEqual(PAL.ANSI[PAL.nearest_ansi((220, 220, 220))][0], "white")
        self.assertEqual(PAL.ANSI[PAL.nearest_ansi((180, 0, 160))][0], "magenta")
        self.assertEqual(PAL.ANSI[PAL.nearest_ansi((10, 10, 10), taken=[0])][0], "blue")

    def test_default_telnet_board(self):
        at = G.colour_attrs("slate")
        self.assertTrue(at[""].endswith(";40"))          # a dark board
        self.assertTrue(at["tile"].endswith(";47"))      # light tiles
        self.assertNotIn(";41", at["TW"])                 # not red
        self.assertFalse(at["tile"].endswith(";43"))      # nor yellow-brown tiles

    def test_own_picks_are_taken_as_they_are(self):
        picks = {k: "red" for k in PAL.ELEMENTS}
        picks["letter"] = "bright white"
        value = PAL.encode(PAL.ansi_custom(picks))
        at = G.colour_attrs(value)
        self.assertTrue(at["TW"].endswith(";41") and at["DL"].endswith(";41"))

    def test_screen_uses_the_kept_colours(self):
        """The Grid Words screen loads the kept value once and draws with it; the colors command keeps a
        new one."""
        class Core:
            def __init__(self):
                self.kept = {"gwcolors": "contrast"}

            async def pref_get(self, key):
                return self.kept.get(key)

            async def save_pref(self, key, value):
                self.kept[key] = value
                return True

            async def req(self, text):
                return [], "OK"

        class Sess:
            pass
        s = Sess()
        s.core, s.io, s.name = Core(), None, "ann"
        self.assertEqual(asyncio.run(G.load_colours(s.core)), "contrast")
        s.core.kept = {}
        self.assertEqual(asyncio.run(G.load_colours(s.core)), "slate")


class Web(Stack):
    """The colors page on the web site: kept with the player, drawn on the board image."""

    def test_colours_page_saves_and_the_board_follows(self):
        b = Browser(self.web_port)
        b.login("pal1", "pal1-Pass-word")
        keep = b.keep()
        b.act = lambda path: b.get(path + ("&" if "?" in path else "?") + keep)
        st, _, _, body = b.act("/colours.htm")
        self.assertEqual(st, 200)
        self.assertIn("Grid Words colors", body)
        self.assertRegex(body, r'value="slate" checked')
        self.assertNotRegex(body.lower(), r"<style|style=|class=|id=")     # HTML 3.2
        st, _, _, body = b.act("/colours.htm?save=1&preset=daylight")
        self.assertIn("Saved.", body)
        self.assertRegex(body, r'value="daylight" checked')
        q = "&".join("%s=%%23%s" % (k, v) for k, v in zip(PAL.ELEMENTS, ("123456", "ff8800", "ffee00", "8800ff",
                                                                         "ddccff", "f0f0f0", "000000")))
        st, _, _, body = b.act("/colours.htm?save=1&preset=custom&" + q)
        self.assertIn("Saved.", body)
        self.assertRegex(body, r'value="custom" checked')
        self.assertIn('value="#123456"', body)
        sw = re.search(r'src="(/swatch.gif\?[^"]+)"', body).group(1).replace("&amp;", "&")
        st, _, ctype, gif = b.get(sw)
        self.assertEqual((st, ctype), (200, "image/gif"))
        st, _, _, body = b.act("/colours.htm?save=1&preset=custom&board=beige")
        self.assertIn("nothing was changed", body)
        # without the action check, nothing is saved
        st, _, _, body = b.get("/colours.htm?save=1&preset=mono")
        self.assertNotIn("Saved.", body)
        # a Grid Words table: its board image is drawn in the kept colors
        st, loc, _, body = b.act("/new.htm?g=gridwords/1&turn=1h")
        st, _, _, body = b.go(loc)
        tid = re.search(r"Grid Words - table (\d+)", body).group(1)
        n = Native(self.core, "pal2", "pal2-Pass-word")
        try:
            self.assertEqual(n.req("JON %s" % tid)[-1], "OK 1")
            st, _, _, body = b.act("/table.htm?t=%s" % tid)
            self.assertIn("/colours.htm", body)
            src = re.search(r'<img src="(/board.gif\?[^"]+)"', body).group(1).replace("&amp;", "&")
            st, _, ctype, gif = b.get(src)
            img = board.decode_gif(gif.encode("latin-1"))
            self.assertIn((0x12, 0x34, 0x56), img.palette)
            self.assertIn((0xff, 0x88, 0x00), img.palette)
        finally:
            n.close()
