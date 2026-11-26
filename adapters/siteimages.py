"""The game server's small pictures (docs/site.md): a photo of each kind of computer beside its row of Play
from your computer on the welcome page, and an icon for each game, which shows its play, on the Games page,
each game's page and the lobby's Start a table.

They are GIFs in adapters/img/, read once when the web extension starts: machines/<anchor>.gif (an anchor of
PLAY_MACHINES in adapters/clients.py, or any-old and any-new), at most 56 by 44 pixels (MACHINE_BOX), and
games/<type>.gif, 32 pixels square (GAME_SIZE), each with a transparent background. web.py serves them at
/img/m/<anchor>.gif and /img/g/<type>.gif. The computers' photos are from Wikimedia Commons, each credited in
adapters/img/CREDITS.tsv (its source, author and license, and what was changed), from which the credits page
(/credits.htm) is built. The game icons and the picture for Any new computer are ParlorNet's own, drawn by
tools/site_icons.py.
"""
import os

from .webstyle import esc

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img")
CREDITS_FILE = os.path.join(DIR, "CREDITS.tsv")
MACHINE_URL = "/img/m/%s.gif"
GAME_URL = "/img/g/%s.gif"
CREDITS_PAGE = "/credits.htm"
MACHINE_BOX = (56, 44)      # the most a computer's picture may be across and down, in pixels
GAME_SIZE = 32              # a game's icon, square

# What each drawn picture shows, its ALT text (a photo's is the "shows" column of CREDITS.tsv).
GAME_ALT = {"reversi": "Black and white discs", "backgammon": "Checkers on the points",
            "gridwords": "Letter tiles", "cannonfire": "A cannon and its shell's arc",
            "dotsandboxes": "Dots, lines and a box", "fiveinarow": "Five stones in a row",
            "mancala": "Pits with seeds", "checkers": "Checkers on a board",
            "navalcombat": "A ship, a hit and misses", "rummytile": "Numbered tiles"}
DRAWN_MACHINES = {"any-new": "A laptop and a phone"}


def credits():
    """CREDITS.tsv's rows, in its order: dicts of picture, shows, source, author, license, license_url,
    changes."""
    with open(CREDITS_FILE, encoding="utf-8") as f:
        lines = [l.rstrip("\n").split("\t") for l in f if l.strip()]
    head = lines[0]
    return [dict(zip(head, l)) for l in lines[1:]]


def gif_size(data):
    """A GIF's width and height, from its logical screen descriptor."""
    return data[6] | data[7] << 8, data[8] | data[9] << 8


def _load():
    """{url: GIF bytes} and {url: (width, height, alt)} of every picture in DIR that has its ALT text."""
    alts = {"machines/%s.gif" % k: v for k, v in DRAWN_MACHINES.items()}
    alts.update(("games/%s.gif" % k, v) for k, v in GAME_ALT.items())
    alts.update((r["picture"], r["shows"]) for r in credits())
    images, tags = {}, {}
    for rel, alt in alts.items():
        kind, name = rel.split("/")
        path = os.path.join(DIR, kind, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        u = (MACHINE_URL if kind == "machines" else GAME_URL) % name[:-len(".gif")]
        images[u] = data
        tags[u] = gif_size(data) + (alt,)
    return images, tags


IMAGES, TAGS = _load()


def img(u, align=None):
    """An <img> for a picture here, with its size and ALT text; "" when there is none."""
    if u not in TAGS:
        return ""
    w, h, alt = TAGS[u]
    return '<img src="%s" width="%d" height="%d"%s alt="%s">' % (
        u, w, h, ' align="%s"' % align if align else "", esc(alt))


def machine_img(anchor):
    return img(MACHINE_URL % anchor)


def game_img(gtype, align="middle"):
    return img(GAME_URL % gtype, align)
