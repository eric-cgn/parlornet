"""Grid Words colors: the default palette, the presets, a player's own choice, and the color arithmetic
that checks them (docs/design/gridwords-colours.md).

A palette is seven colors, one per element: the board's empty squares, the four premium kinds (triple
and double word, triple and double letter), the tiles and the letters on them. Every other color a board
uses (the grid lines, the ink of a premium's label, a blank's letter, the start arrow) is worked out from
those seven, so any choice stays readable.

The default and the presets keep clear of the look of the best-known commercial crossword game (red and
pink word squares, dark and light blue letter squares, beige tiles, a green or tan board); a player's own
colors are theirs to choose. Every palette is drawn with the premiums' names on their squares (3W, 2W,
3L, 2L) and a frame on the word squares, so no square is told by color alone.

A player's choice is kept with PRF under the key gwcolors, in each server extension's own namespace: the
web's (the HTML 3.2 pages and /play/ share it) and telnet's. The value is a preset's name, or "custom:"
and the seven colors as hex, in ELEMENTS order:

    slate
    custom:2f343b,e07b00,f5d22e,6a3fb5,c9b6f5,fafafa,15181c

Pure Python, no dependencies; adapters/games/gridwords.py draws with it, and the web extension serves it
to the modern client as JSON (client_json)."""
import json
import math
import re

PREF_KEY = "gwcolors"

# The elements a player can color, in the order of a stored custom palette.
ELEMENTS = ("board", "tw", "dw", "tl", "dl", "tile", "letter")
ELEMENT_NAMES = {"board": "Board", "tw": "Triple word", "dw": "Double word", "tl": "Triple letter",
                 "dl": "Double letter", "tile": "Tiles", "letter": "Letters"}
PREMIUMS = ("tw", "dw", "tl", "dl")
LABELS = {"tw": "3W", "dw": "2W", "tl": "3L", "dl": "2L"}
LEGEND = {"tw": "triple word", "dw": "double word", "tl": "triple letter", "dl": "double letter"}


def hexrgb(text):
    """'#2f343b' or '2F343B' -> (47, 52, 59); ValueError for anything else."""
    t = text.strip().lstrip("#")
    if not re.match(r"^[0-9A-Fa-f]{6}\Z", t):
        raise ValueError("not a color: %r" % text)
    return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))


def rgbhex(rgb):
    return "%02x%02x%02x" % tuple(int(c) for c in rgb)


def _pal(**kw):
    return {k: hexrgb(v) for k, v in kw.items()}


# The presets, in the order they are offered; the first is the default.
PRESETS = {
    "slate": _pal(board="2f343b", tw="e07b00", dw="f5d22e", tl="7b3aa8", dl="c9b6f5", tile="fafafa",
                  letter="15181c"),
    "daylight": _pal(board="e9ebee", tw="b05f00", dw="f2c230", tl="4b2f8a", dl="bfa8f0", tile="2b3038",
                     letter="ffffff"),
    "contrast": _pal(board="000000", tw="ff8c00", dw="ffe600", tl="8b3df5", dl="e6d2ff", tile="ffffff",
                     letter="000000"),
    "mono": _pal(board="ffffff", tw="4d4d4d", dw="969696", tl="737373", dl="c8c8c8", tile="1a1a1a",
                 letter="ffffff"),
}
PRESET_NAMES = {"slate": "Slate (the default)", "daylight": "Daylight", "contrast": "High contrast",
                "mono": "Grayscale"}
DEFAULT = "slate"

# The look the default and the presets keep clear of (docs/design/gridwords-colours.md): approximate colors
# of the well-known commercial crossword game's board, and the colors this server's own boards used before.
# For the tests only; nothing is drawn with them.
TRADE_DRESS = {
    "tw": [hexrgb("d9393b"), (200, 40, 40)],                 # red triple word
    "dw": [hexrgb("f5b3b5"), (240, 165, 160)],               # pink double word
    "tl": [hexrgb("2f6fb2"), (40, 90, 190)],                 # dark blue triple letter
    "dl": [hexrgb("a9d7ee"), (160, 200, 240)],               # light blue double letter
    "tile": [hexrgb("f1d9a8"), (248, 222, 150)],             # beige tiles
    "board": [hexrgb("0e6b4f"), hexrgb("d8c9a3"), (222, 216, 190), (70, 90, 70)],   # green or tan board
}

INK_DARK, INK_LIGHT = hexrgb("15181c"), hexrgb("ffffff")


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


def preset_of(pal):
    """The preset a palette is, or "custom"."""
    e = encode(pal)
    return e if e in PRESETS else "custom"


def from_fields(get):
    """A palette from form fields: get(name) -> text. preset= names a preset; preset=custom takes the seven
    colors from fields named as ELEMENTS ('#rrggbb' or 'rrggbb'). None when something is not a color."""
    p = (get("preset") or "").strip().lower()
    if p in PRESETS:
        return dict(PRESETS[p])
    if p != "custom":
        return None
    try:
        return {k: hexrgb(get(k) or "") for k in ELEMENTS}
    except ValueError:
        return None


# ---- color arithmetic: luminance, contrast, color difference, color-vision deficiencies -------------

def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _unlin(c):
    c = min(1.0, max(0.0, c))
    return 255.0 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055)


def luminance(rgb):
    """Relative luminance (WCAG 2), 0 for black to 1 for white."""
    r, g, b = (_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    """The WCAG contrast ratio of two colors, 1 to 21."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# Machado, Oliveira and Fernandes (2009), severity 1.0: the three dichromacies, on linear RGB.
CVD = {
    "protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
                   (-0.003882, -0.048116, 1.051998)),
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
                     (-0.011820, 0.042940, 0.968881)),
    "tritanopia": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602),
                   (0.004733, 0.691367, 0.303900)),
}


def simulate(rgb, kind):
    """How a color looks with a color-vision deficiency (a key of CVD, or "achromatopsia": luminance only)."""
    if kind == "achromatopsia":
        g = _unlin(luminance(rgb))
        return (g, g, g)
    lin = [_lin(c) for c in rgb]
    return tuple(_unlin(sum(m * c for m, c in zip(row, lin))) for row in CVD[kind])


def lab(rgb):
    """sRGB -> CIELAB (D65)."""
    r, g, b = (_lin(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3.0) if t > 0.008856 else 7.787 * t + 16 / 116.0
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def delta_e(c1, c2):
    """CIEDE2000 color difference of two sRGB colors: about 2 is just noticeable, over 10 clearly different."""
    L1, a1, b1 = lab(c1)
    L2, a2, b2 = lab(c2)
    cbar = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(cbar ** 7 / (cbar ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1 = math.degrees(math.atan2(b1, a1p)) % 360
    h2 = math.degrees(math.atan2(b2, a2p)) % 360
    dl, dc = L2 - L1, c2p - c1p
    if c1p * c2p == 0:
        dh = 0.0
    elif abs(h2 - h1) <= 180:
        dh = h2 - h1
    else:
        dh = h2 - h1 - 360 if h2 > h1 else h2 - h1 + 360
    dH = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))
    lbar, cbarp = (L1 + L2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbar = h1 + h2
    elif abs(h1 - h2) <= 180:
        hbar = (h1 + h2) / 2
    else:
        hbar = (h1 + h2 + 360) / 2 if h1 + h2 < 360 else (h1 + h2 - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(hbar - 30)) + 0.24 * math.cos(math.radians(2 * hbar))
         + 0.32 * math.cos(math.radians(3 * hbar + 6)) - 0.20 * math.cos(math.radians(4 * hbar - 63)))
    dtheta = 30 * math.exp(-((hbar - 275) / 25) ** 2)
    rc = 2 * math.sqrt(cbarp ** 7 / (cbarp ** 7 + 25.0 ** 7))
    sl = 1 + 0.015 * (lbar - 50) ** 2 / math.sqrt(20 + (lbar - 50) ** 2)
    sc, sh = 1 + 0.045 * cbarp, 1 + 0.015 * cbarp * t
    rt = -math.sin(math.radians(2 * dtheta)) * rc
    return math.sqrt((dl / sl) ** 2 + (dc / sc) ** 2 + (dH / sh) ** 2 + rt * (dc / sc) * (dH / sh))


# ---- what a board draws with, worked out from the seven ---------------------------------------------------

def best_ink(bg, candidates=(INK_DARK, INK_LIGHT)):
    """Of candidates, the one with the most contrast on bg."""
    return max(candidates, key=lambda c: contrast(bg, c))


def mix(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def derived(pal):
    """Every color a board draws with: the seven, and grid (lines between squares), label_<kind> (the ink of
    a premium's name and frame), blank (a blank's letter on a tile), arrow (the start arrow on an empty
    square) and points (the tile values)."""
    out = {k: tuple(pal[k]) for k in ELEMENTS}
    board = out["board"]
    out["grid"] = mix(board, (255, 255, 255), 0.16) if luminance(board) < 0.18 else mix(board, (0, 0, 0), 0.32)
    for k in PREMIUMS:
        out["label_" + k] = best_ink(out[k])
    # a blank's letter: an accent that reads on the tile (a premium color), else the letters' own color
    accents = [out[k] for k in ("tl", "tw", "dl", "dw") if contrast(out[k], out["tile"]) >= 3.0]
    out["blank"] = accents[0] if accents else out["letter"]
    out["arrow"] = best_ink(board, (out["tile"], out["dw"], out["dl"], INK_DARK, INK_LIGHT))
    out["points"] = out["letter"]
    return out


def problems(pal, cvd_min=10.0, normal_min=12.0, label_min=4.5, letter_min=7.0):
    """What makes a palette hard to read, as text (an empty list when nothing does): the board, the four
    premiums and the tiles must differ by at least normal_min (CIEDE2000) as seen normally and cvd_min with
    each dichromacy; each premium's label at least label_min contrast; the letters on the tiles letter_min."""
    out = []
    keys = ("board",) + PREMIUMS + ("tile",)
    for kind in ("normal",) + tuple(CVD):
        need = normal_min if kind == "normal" else cvd_min
        seen = {k: (pal[k] if kind == "normal" else simulate(pal[k], kind)) for k in keys}
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                d = delta_e(seen[a], seen[b])
                if d < need:
                    out.append("%s and %s differ by %.1f (%s)" % (a, b, d, kind))
    d = derived(pal)
    for k in PREMIUMS:
        c = contrast(pal[k], d["label_" + k])
        if c < label_min:
            out.append("the %s label has contrast %.1f" % (k, c))
    c = contrast(pal["tile"], pal["letter"])
    if c < letter_min:
        out.append("letters on tiles have contrast %.1f" % c)
    return out


def trade_dress_distance(pal):
    """How far a palette keeps from the look in TRADE_DRESS: the smallest color difference of a premium from
    any of the four premium colors there, of the tiles from the beige tiles, and of the board from the
    green and tan boards, as [(difference, element, reference color)], smallest first."""
    out = []
    for k in ("board",) + PREMIUMS + ("tile",):
        roles = PREMIUMS if k in PREMIUMS else (k,)
        out.append(min((delta_e(pal[k], r), k, r) for role in roles for r in TRADE_DRESS[role]))
    return sorted(out)


# ---- telnet: the nearest ANSI colors ----------------------------------------------------------------------

# The eight ANSI colors (SGR 30-37 and 40-47) as a VGA text screen shows them, and the bright ones of the
# foreground (bold 30-37).
ANSI = (("black", (0, 0, 0)), ("red", (170, 0, 0)), ("green", (0, 170, 0)), ("yellow", (170, 85, 0)),
        ("blue", (0, 0, 170)), ("magenta", (170, 0, 170)), ("cyan", (0, 170, 170)), ("white", (170, 170, 170)))
ANSI_BRIGHT = (("gray", (85, 85, 85)), ("bright red", (255, 85, 85)), ("bright green", (85, 255, 85)),
               ("bright yellow", (255, 255, 85)), ("bright blue", (85, 85, 255)),
               ("bright magenta", (255, 85, 255)), ("bright cyan", (85, 255, 255)),
               ("bright white", (255, 255, 255)))


def nearest_ansi(rgb, taken=()):
    """The index (0-7) of the ANSI background color nearest rgb, skipping those in taken."""
    return min((i for i in range(8) if i not in taken), key=lambda i: delta_e(rgb, ANSI[i][1]))


def nearest_ansi_fg(rgb, avoid=None):
    """(index 0-7, bright) of the ANSI foreground color nearest rgb, among the 16; avoid: a background index
    the result must not equal (the same color on itself)."""
    best = None
    for bright, table in ((False, ANSI), (True, ANSI_BRIGHT)):
        for i, (_, c) in enumerate(table):
            if avoid is not None and i == avoid and not bright:
                continue
            d = delta_e(rgb, c)
            if best is None or d < best[0]:
                best = (d, i, bright)
    return best[1], best[2]


def ansi_map(pal, distinct=True):
    """{element: background index 0-7} for board, tile and the four premiums: each the nearest ANSI color; with
    distinct (the presets), one already used is skipped, in the order board, tile, tw, dw, tl, dl, so every
    kind of square still differs. A player's own picks (distinct=False) are taken as they are."""
    out, taken = {}, []
    for k in ("board", "tile") + PREMIUMS:
        i = nearest_ansi(pal[k], taken if distinct else ())
        out[k] = i
        taken.append(i)
    return out


def ansi_attrs(pal, distinct=True):
    """The SGR attributes the telnet board draws with: {"" (an empty square), "ctr", "TW", "DW", "TL", "DL",
    "tile", "blank", "last" (the last word's tiles), "pend" (letters typed, not yet played), "mark" (the start
    arrow)}. The cursor is any of these in reverse video."""
    m = ansi_map(pal, distinct)

    def on(bg_index, fg_rgb=None, bold=None):
        bg = ANSI[bg_index][1]
        if fg_rgb is None:      # the label: black or bright white, whichever reads better on it
            dark = contrast(bg, (0, 0, 0)) >= contrast(bg, (255, 255, 255))
            return "0;%s;%d" % ("30" if dark else "1;37", 40 + bg_index)
        i, bright = nearest_ansi_fg(fg_rgb, avoid=bg_index)
        if i == bg_index and bright == (bold or False):
            i, bright = (0, False) if contrast(bg, (0, 0, 0)) >= 4 else (7, True)
        return "0;%s%d;%d" % ("1;" if bright or bold else "", 30 + i, 40 + bg_index)
    d = derived(pal)
    out = {"": on(m["board"], d["grid"]), "TW": on(m["tw"]), "DW": on(m["dw"]), "TL": on(m["tl"]), "DL": on(m["dl"])}
    out["ctr"] = out["DW"]
    out["tile"] = on(m["tile"], pal["letter"])
    blank_i, _ = nearest_ansi_fg(d["blank"], avoid=m["tile"])
    out["blank"] = "0;%d;%d" % (30 + (blank_i if blank_i != m["tile"] else 1), 40 + m["tile"])
    tile_bg = 40 + m["tile"]
    out["last"] = "0;1;%d;%d" % (34 if m["tile"] not in (0, 4) else 33, tile_bg)   # the last word's letters
    out["pend"] = "0;7;%s" % out["tile"].split(";", 1)[1]           # the tile's colors, reversed
    out["mark"] = on(m["board"], d["arrow"], bold=True)
    return out


ANSI_CHOICES = tuple(name for name, _ in ANSI)


def ansi_custom(picks):
    """A custom palette from telnet's picks {element: ANSI color name}: each element the VGA color itself
    (letter may be a bright one)."""
    table = dict(ANSI + ANSI_BRIGHT)
    return {k: table[picks[k]] for k in ELEMENTS}


# ---- the modern client -------------------------------------------------------------------------------------

def client_json():
    """The presets and what the modern client needs to draw them, as JSON: /play/palette.json."""
    def entry(p):
        d = derived(p)
        return {k: "#" + rgbhex(v) for k, v in d.items()}
    return json.dumps({"key": PREF_KEY, "default": DEFAULT, "elements": list(ELEMENTS),
                       "names": ELEMENT_NAMES, "labels": LABELS, "legend": LEGEND,
                       "presets": [{"id": k, "name": PRESET_NAMES[k], "colours": entry(p)}
                                   for k, p in PRESETS.items()]}, sort_keys=True)
