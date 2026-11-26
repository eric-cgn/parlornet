"""The game server's small pictures (adapters/siteimages.py, docs/site.md): a photo of every kind of computer on
the welcome page and an icon for every game, each a small GIF with a transparent background; every photo is
credited in adapters/img/CREDITS.tsv under a free license; the drawn ones are what tools/site_icons.py draws.

    python3 -m unittest discover -s tests -p "test_site_images.py"
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from adapters import clients as C, siteimages as PIC, web as W  # noqa: E402

MOST_BYTES = 2048               # a picture's file, at most
MOST_COLORS = 16                # in its color table, the transparent one included
# The licenses a photo may come under: the public domain, CC0, or Creative Commons Attribution (and
# Share-Alike), with the license's address on creativecommons.org.
LICENSES = ("Public domain", "CC0 1.0", "CC BY ", "CC BY-SA ")


def transparent_index(data):
    """The transparent color's index from a GIF's graphic control extension, or None."""
    at = data.find(b"\x21\xf9\x04")
    return data[at + 6] if at >= 0 and data[at + 3] & 1 else None


class Pictures(unittest.TestCase):

    def test_every_machine_and_game_has_its_picture(self):
        anchors = [m[0] for m in C.PLAY_MACHINES] + [W.ANY_OLD[0], W.ANY_NEW[0]]
        for anchor in anchors:
            u = PIC.MACHINE_URL % anchor
            self.assertIn(u, PIC.IMAGES, anchor)
            w, h, alt = PIC.TAGS[u]
            self.assertTrue(w <= PIC.MACHINE_BOX[0] and h <= PIC.MACHINE_BOX[1], anchor)
            self.assertTrue(max(w, h) >= 40, anchor)            # scaled down, but not to a speck
            self.assertTrue(alt.strip(), anchor)
        for r in W.game_rows():
            u = PIC.GAME_URL % r["type"]
            self.assertIn(u, PIC.IMAGES, r["type"])
            self.assertEqual(PIC.TAGS[u][:2], (PIC.GAME_SIZE, PIC.GAME_SIZE), r["type"])
            self.assertTrue(PIC.TAGS[u][2].strip(), r["type"])
        self.assertEqual(len(PIC.IMAGES), len(anchors) + len(W.game_rows()))
        for u in PIC.IMAGES:            # served by the web extension with its other images
            self.assertIs(W.SITE_IMAGES[u], PIC.IMAGES[u])

    def test_small_gifs_with_a_transparent_background(self):
        for u, data in PIC.IMAGES.items():
            self.assertTrue(data.startswith(b"GIF89a"), u)
            self.assertLessEqual(len(data), MOST_BYTES, u)
            self.assertTrue(data[10] & 0x80, u)                 # a global color table
            self.assertLessEqual(2 << (data[10] & 7), MOST_COLORS, u)
            self.assertEqual(transparent_index(data), 0, u)
            self.assertEqual(data[13:16], b"\xff\xff\xff", u)   # white where transparency is not shown
            self.assertEqual(data[-1:], b";", u)

    def test_every_photo_is_credited_under_a_free_license(self):
        credits = PIC.credits()
        drawn = {"machines/%s.gif" % k for k in PIC.DRAWN_MACHINES}
        files = {"machines/" + f for f in os.listdir(os.path.join(PIC.DIR, "machines"))}
        self.assertEqual({c["picture"] for c in credits}, files - drawn)
        self.assertEqual(len(credits), len(files - drawn))
        for c in credits:
            self.assertEqual(set(c), {"picture", "shows", "source", "author", "license", "license_url", "changes"})
            self.assertTrue(c["source"].startswith("https://commons.wikimedia.org/wiki/File:"), c["picture"])
            self.assertTrue(c["author"].strip() and c["shows"].strip() and c["changes"].strip(), c["picture"])
            self.assertTrue(c["license"].startswith(LICENSES), c["picture"])
            if c["license"] == "Public domain":
                self.assertEqual(c["license_url"], "", c["picture"])
            else:
                self.assertRegex(c["license_url"], r"^https://creativecommons\.org/(licenses|publicdomain)/[a-z-]+/"
                                                   r"[0-9.]+/$", c["picture"])

    def test_the_drawn_pictures_are_the_tools(self):
        """The game icons and Any new computer's picture are exactly what tools/site_icons.py draws, so they
        can be drawn again."""
        import site_icons
        drawn = site_icons.drawings()
        self.assertEqual(set(drawn), {"games/%s.gif" % t for t in PIC.GAME_ALT} |
                         {"machines/%s.gif" % k for k in PIC.DRAWN_MACHINES})
        for rel, data in drawn.items():
            with open(os.path.join(PIC.DIR, rel), "rb") as f:
                self.assertEqual(f.read(), data, rel)
        for name, draw in site_icons.GAMES.items():         # every color web-safe, as the site's own
            for color in draw().palette:
                self.assertTrue(all(v % 0x33 == 0 for v in color), (name, color))
        for color in site_icons.any_new().palette:
            self.assertTrue(all(v % 0x33 == 0 for v in color), color)


if __name__ == "__main__":
    unittest.main()
