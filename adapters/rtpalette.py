"""Rummy Tile Classic colors for ParlorNet Play: the default palette, the presets, a player's own choice, and
the checks that keep them readable (docs/design/rummytile-colours.md).

A palette is seven colors: the table the sets lie on, the tile body, the ink of the joker's star, and the
four color families of the protocol (r red, b blue, k black, y yellow, docs/types/rummytile-1.md). A tile
is drawn flat in the tile color with its number in its family's color, so the color of the number
carries the family; a joker carries a star in the ink. Every other color Play draws with (the rack tray,
the marks on the sets) is worked out from the seven. The default and the presets keep the four number
colors readable on the tiles and apart from each other with normal color vision and with each of the
three dichromacies; the color-blind-safe and high-contrast presets are there for players who need more.

The default and the presets keep clear of the look of the best-known commercial tile-rummy game (cream or
ivory tiles, a smiling joker); a player's own colors are theirs to choose.

A player's choice is kept with PRF under the key rtcolors, in the namespace ParlorNet Play shares with the
web pages. The value is a preset's name, or "custom:" and the seven colors as hex, in ELEMENTS order:

    slate
    custom:24313c,e0e8f5,15181c,a8241c,2563c9,1b1e23,a07c00

Pure Python, no dependencies; the color arithmetic is Grid Words' (adapters/palette.py). The Play
extension serves the presets to the page as JSON (client_json)."""
import json

from .palette import INK_DARK, INK_LIGHT, CVD, contrast, delta_e, hexrgb, mix, rgbhex, simulate

PREF_KEY = "rtcolors"

# The elements a player can color, in the order of a stored custom palette.
ELEMENTS = ("table", "tile", "ink", "r", "b", "k", "y")
FAMILIES = ("r", "b", "k", "y")
ELEMENT_NAMES = {"table": "Table", "tile": "Tiles", "ink": "Joker star", "r": "Red (R)", "b": "Blue (B)",
                 "k": "Black (K)", "y": "Yellow (Y)"}


def _pal(**kw):
    return {k: hexrgb(v) for k, v in kw.items()}


# The presets, in the order they are offered; the first is the default. The numbers are drawn in the four
# family colors on the tile, so each of the four is dark enough to read on it; yellow is a dark gold for that
# reason. Red and yellow are kept apart in lightness, red darker, because with red-green color blindness
# that is what tells them apart.
PRESETS = {
    "slate": _pal(table="24313c", tile="e0e8f5", ink="15181c", r="a8241c", b="2563c9", k="1b1e23", y="a07c00"),
    # Okabe and Ito's blue and black, with a deep red and a dark gold set wider apart in lightness
    "cvd": _pal(table="2b2f36", tile="eef3f8", ink="000000", r="952818", b="0072b2", k="000000", y="b47a00"),
    # every number at a contrast of 4.5 or more on white
    "contrast": _pal(table="000000", tile="ffffff", ink="000000", r="900000", b="0047ff", k="000000", y="8f7200"),
}
PRESET_NAMES = {"slate": "Slate (the default)", "cvd": "Color-blind safe", "contrast": "High contrast"}
DEFAULT = "slate"

# The look the default and the presets keep clear of (docs/design/rummytile-colours.md): approximate colors
# of the cream and ivory tiles of the well-known commercial tile-rummy game. For the tests only; nothing is
# drawn with them.
TRADE_DRESS_TILES = [hexrgb("f3ebd3"), hexrgb("fff8e7"), hexrgb("efe4c6"), hexrgb("f7f0dc")]


# ---- a player's choice, as kept with PRF ----------------------------------------------------------------

def encode(pal):
    """A palette -> its PRF value: a preset's name when it is one, else custom:..."""
    for name, p in PRESETS.items():
        if all(tuple(p[k]) == tuple(pal[k]) for k in ELEMENTS):
            return name
    return "custom:" + ",".join(rgbhex(pal[k]) for k in ELEMENTS)


def decode(value):
    """A PRF value -> a palette (a new dict), or None when it is not one (then the default applies)."""
    v = (value or "").strip().lower()
    if v in PRESETS:
        return dict(PRESETS[v])
    if v.startswith("custom:"):
        parts = v[7:].split(",")
        if len(parts) == len(ELEMENTS):
            try:
                return {k: hexrgb(c) for k, c in zip(ELEMENTS, parts)}
            except ValueError:
                return None
    return None


def palette(value=None):
    """The palette for a PRF value: the player's own, else the default."""
    return decode(value) or dict(PRESETS[DEFAULT])


# ---- what Play draws with, worked out from the seven ---------------------------------------------------------

def derived(pal):
    """Every color the board draws with: the seven, and tray (the rack tray, a little lighter or darker than
    the table) and mark (the ink of labels on the table)."""
    out = {k: tuple(pal[k]) for k in ELEMENTS}
    table = out["table"]
    dark = contrast(table, INK_DARK) < contrast(table, INK_LIGHT)
    out["tray"] = mix(table, (255, 255, 255), 0.10) if dark else mix(table, (0, 0, 0), 0.12)
    out["mark"] = INK_LIGHT if dark else INK_DARK
    return out


def problems(pal, cvd_min=10.0, normal_min=12.0, number_min=3.0, ink_min=7.0, table_min=3.0):
    """What makes a palette hard to read, as text (an empty list when nothing does): the tile body and the four
    family colors must differ by at least normal_min (CIEDE2000) as seen normally and cvd_min with each
    dichromacy; the numbers, in the family colors, at least number_min contrast (WCAG) on the tiles; the
    joker's star ink_min; the tiles on the table table_min."""
    out = []
    keys = ("tile",) + FAMILIES
    for kind in ("normal",) + tuple(CVD):
        need = normal_min if kind == "normal" else cvd_min
        seen = {k: (pal[k] if kind == "normal" else simulate(pal[k], kind)) for k in keys}
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                d = delta_e(seen[a], seen[b])
                if d < need:
                    out.append("%s and %s differ by %.1f (%s)" % (a, b, d, kind))
    for f in FAMILIES:
        c = contrast(pal["tile"], pal[f])
        if c < number_min:
            out.append("the %s numbers on the tiles have contrast %.1f" % (f, c))
    c = contrast(pal["tile"], pal["ink"])
    if c < ink_min:
        out.append("the joker's star on the tiles has contrast %.1f" % c)
    c = contrast(pal["tile"], pal["table"])
    if c < table_min:
        out.append("the tiles on the table have contrast %.1f" % c)
    return out


def trade_dress_distance(pal):
    """How far the tile color keeps from the cream and ivory tiles in TRADE_DRESS_TILES: the smallest color
    difference (CIEDE2000)."""
    return min(delta_e(pal["tile"], r) for r in TRADE_DRESS_TILES)


# ---- the modern client ---------------------------------------------------------------------------------------

def client_json():
    """The presets and what the modern client needs to draw them, as JSON: /play/rummytile-colours.json."""
    def entry(p):
        return {k: "#" + rgbhex(v) for k, v in derived(p).items()}
    return json.dumps({"key": PREF_KEY, "default": DEFAULT, "elements": list(ELEMENTS), "names": ELEMENT_NAMES,
                       "presets": [{"id": k, "name": PRESET_NAMES[k], "colours": entry(p)}
                                   for k, p in PRESETS.items()]}, sort_keys=True)
