"""Rummy Tile colors for ParlorNet Play (adapters/rtpalette.py, docs/design/rummytile-colours.md): the default and
the presets keep clear of cream tiles, draw numbers that read on the tiles in the four family colors, and keep
the four apart with normal color vision and with color-vision deficiencies; a player's choice as kept with PRF;
the JSON the Play extension serves."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters import palette as P, rtpalette as R  # noqa: E402


def closest(pal, kind):
    """The smallest color difference (CIEDE2000) between two of the four family colors, as seen with kind."""
    seen = {f: (pal[f] if kind == "normal" else P.simulate(pal[f], kind)) for f in R.FAMILIES}
    return min(P.delta_e(seen[a], seen[b]) for i, a in enumerate(R.FAMILIES) for b in R.FAMILIES[i + 1:])


class Presets(unittest.TestCase):
    def test_every_preset_is_readable(self):
        for name, pal in R.PRESETS.items():
            self.assertEqual(R.problems(pal), [], name)

    def test_the_numbers_read_on_the_tiles(self):
        """Each number color has a WCAG contrast of at least 3 on the tile, the level for large text (the numbers
        are bold and fill about half the tile's height); in the high-contrast preset at least 4.5."""
        for name, pal in R.PRESETS.items():
            for f in R.FAMILIES:
                self.assertGreaterEqual(P.contrast(pal["tile"], pal[f]), 3.0, (name, f))
        for f in R.FAMILIES:
            self.assertGreaterEqual(P.contrast(R.PRESETS["contrast"]["tile"], R.PRESETS["contrast"][f]), 4.5, f)

    def test_the_four_colours_stay_apart(self):
        """The four family colors differ by at least 12 (CIEDE2000) seen normally and 10 with each simulated
        dichromacy; in the color-blind-safe preset by at least 18 with each."""
        for name, pal in R.PRESETS.items():
            self.assertGreaterEqual(closest(pal, "normal"), 12, name)
            for kind in P.CVD:
                self.assertGreaterEqual(closest(pal, kind), 10, (name, kind))
        for kind in P.CVD:
            self.assertGreaterEqual(closest(R.PRESETS["cvd"], kind), 18, kind)

    def test_black_stays_near_black(self):
        for name, pal in R.PRESETS.items():
            self.assertLessEqual(P.lab(pal["k"])[0], 15, name)

    def test_kept_clear_of_cream_tiles(self):
        for name, pal in R.PRESETS.items():
            self.assertGreaterEqual(R.trade_dress_distance(pal), 7, name)
            self.assertLessEqual(P.lab(pal["tile"])[2], 0.5, name)        # no warm (yellowish) tint
        self.assertGreaterEqual(R.trade_dress_distance(R.PRESETS[R.DEFAULT]), 14)

    def test_the_presets_offered(self):
        self.assertEqual(list(R.PRESETS)[0], R.DEFAULT)
        self.assertIn("cvd", R.PRESETS)
        self.assertIn("contrast", R.PRESETS)
        self.assertEqual(set(R.PRESET_NAMES), set(R.PRESETS))
        okabe_ito = {"b": "0072b2", "k": "000000"}                  # Okabe and Ito's blue and black
        self.assertEqual({f: P.rgbhex(R.PRESETS["cvd"][f]) for f in okabe_ito}, okabe_ito)

    def test_problems_finds_what_is_wrong(self):
        bad = dict(R.PRESETS["slate"], y=(242, 194, 48), b=R.PRESETS["slate"]["r"], ink=(120, 130, 140),
                   table=(200, 210, 220))
        text = " ".join(R.problems(bad))
        self.assertIn("r and b differ", text)
        self.assertIn("the y numbers on the tiles have contrast 1.", text)       # a bright yellow on a light tile
        self.assertIn("joker's star", text)
        self.assertIn("tiles on the table", text)


class Choice(unittest.TestCase):
    def test_encode_and_decode(self):
        for name, pal in R.PRESETS.items():
            self.assertEqual(R.encode(pal), name)
            self.assertEqual(R.decode(name), pal)
        own = dict(R.PRESETS["slate"], r=(200, 0, 0))
        v = R.encode(own)
        self.assertEqual(v, "custom:24313c,e0e8f5,15181c,c80000,2563c9,1b1e23,a07c00")
        self.assertEqual(R.decode(v), own)
        self.assertEqual(R.decode(v.upper()), own)
        for junk in ("", "nonsense", "custom:", "custom:24313c,e0e8f5", "custom:" + ",".join(["zzzzzz"] * 7)):
            self.assertIsNone(R.decode(junk), junk)
            self.assertEqual(R.palette(junk), R.PRESETS[R.DEFAULT])
        self.assertEqual(R.PREF_KEY, "rtcolors")

    def test_derived(self):
        d = R.derived(R.PRESETS["slate"])
        self.assertEqual(set(d), set(R.ELEMENTS) | {"tray", "mark"})
        self.assertEqual(d["mark"], P.INK_LIGHT)                   # a dark table: white labels
        light = R.derived(dict(R.PRESETS["slate"], table=(230, 232, 236)))
        self.assertEqual(light["mark"], P.INK_DARK)

    def test_client_json(self):
        j = json.loads(R.client_json())
        self.assertEqual((j["key"], j["default"], j["elements"]), ("rtcolors", "slate", list(R.ELEMENTS)))
        self.assertEqual([p["id"] for p in j["presets"]], list(R.PRESETS))
        slate = j["presets"][0]["colours"]
        self.assertEqual(slate["tile"], "#e0e8f5")
        self.assertEqual(slate["y"], "#a07c00")
        self.assertEqual(slate["tray"], "#" + P.rgbhex(R.derived(R.PRESETS["slate"])["tray"]))
        self.assertEqual(j["names"]["ink"], "Joker star")

    def test_the_page_works_them_out_the_same_way(self):
        """app.js's rtDerived and its fallback default agree with the Python."""
        with open(os.path.join(ROOT, "adapters", "play", "app.js"), encoding="utf-8") as f:
            app = f.read()
        self.assertIn('o.tray = dark ? mix(p.table, "#ffffff", 0.10) : mix(p.table, "#000000", 0.12);', app)
        self.assertNotIn('o["label_" + f]', app)
        slate = R.PRESETS["slate"]
        fallback = "rtDerived({" + ", ".join('%s: "#%s"' % (k, P.rgbhex(slate[k])) for k in R.ELEMENTS) + "})"
        self.assertIn(fallback, app)


if __name__ == "__main__":
    unittest.main()
