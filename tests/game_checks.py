"""Checks every game's frontend module must pass (docs/games-howto.md). A game's own tests
(tests/test_<game>.py) call these with its views; tests/test_games.py runs the generic ones for every
module in adapters/games/.

    from game_checks import ansi_screens, plain_screen, web_page
"""
import asyncio
import os
import re

from test_adapters import LONG, FakeIO, T  # noqa: F401  (LONG: a 12-letter name, the longest allowed)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODES = ("pc", "utf8", "ansi")


class ScreenCore:
    """Stands in for the core behind a game screen: GAM lists every game module, LST the one table,
    VUE answers with the view lines given; every request is kept in reqs. CMD runs the shared commands
    (parlord/commands.py, as the core does) with these requests, as the player me; a typed move is no
    command here unless a test gives typed(words), the game's reading of it."""

    def __init__(self, key, lines, names=("ann", "bob"), tid="3"):
        from adapters import games
        self.lines, self.reqs = lines, []
        self.me = names[0] if names else "ann"
        self.auth, self.label = "password", "Password"    # as CoreClient has them, from the server's greeting
        self.gam = ["GAM %s standard %s 2 2" % (m.KEY, m.NAME.replace(" ", "_")) for m in games.all_games()]
        self.tabs = ["TAB %s %s standard %d/2 playing %s turn=300" % (tid, key, len(names), ",".join(names))]

    async def req(self, text):
        self.reqs.append(text)
        verb = text.split()[0]
        if verb == "GAM":
            return self.gam, "OK"
        if verb == "LST":
            return self.tabs, "OK"
        if verb == "VUE":
            return self.lines, "OK"
        if verb == "SIN":
            return [], "OK 0"
        if verb == "CMD":
            return await self.cmd(text)
        return [], "OK"

    async def cmd(self, text):
        """CMD [table] [YES] /line, as the core answers it (fake_cmd)."""
        from test_adapters import fake_cmd
        return await fake_cmd(self.req, text, self.me, self.tabs, self.gam, getattr(self, "typed", None))


def reads_typed(plugin, state, seat, rules="standard"):
    """A prepare(screen) for ansi_screens and plain_screen: the core's CMD reads a typed move with the game's own
    plugin (its op_typed) over state, as the core does (docs/plugins.md, typed)."""
    async def typed(words):
        return plugin.op_typed({"op": "typed", "rules": rules, "state": state, "seat": seat, "words": words})

    def prepare(g):
        g.s.core.typed = typed
    return prepare


def session(io, core, mode, name=LONG):
    """A telnet Session for tests, signed in as name."""
    if isinstance(core, ScreenCore):
        core.me = name
    return T.Session(io, core, mode, name)


def ansi_screens(tc, module, lines, me=0, keys=(), names=(LONG, LONG.upper()), log=True, prepare=None):
    """Draw the module's full screen in every ANSI mode with the view lines given, as seat me (-1
    watches), pressing keys (as TelnetIO reports them). Checks every screen shown: 24 rows of at most 80 columns, nothing drawn off the canvas, in
    the bottom-right cell or that scrolls, and only characters the mode's character set has. Returns
    {mode: (every screen's text joined, the requests sent to the core)}. prepare(screen), if given, runs
    before the first screen (to set state the view does not carry)."""
    out = {}
    for mode in MODES:
        io = FakeIO(mode, list(keys))
        core = ScreenCore(module.KEY, lines, names)
        s = session(io, core, mode)
        frame = s.painter.frame

        def checked(cv, cursor=None, full=False, frame=frame):
            tc.assertFalse(cv.clipped, "%s: drawn outside 80x24" % mode)
            tc.assertEqual(len(cv.ch), 24)
            tc.assertTrue(all(len(r) == 80 for r in cv.ch))
            data = frame(cv, cursor, full)
            tc.assertNotIn("\n", data)
            return data
        s.painter.frame = checked
        g = module.AnsiScreen(s, "3", module.KEY, me)
        g.names, g.turn_secs, g.v = list(names), 300, g.parse(lines)
        if log:
            g.log = ["<%s> %s" % (LONG, "a long line of chat " * 4), "%s did something" % LONG] * 8
        if prepare:
            prepare(g)
        g.snap()

        async def drive():
            await g.show()
            while io.keys:
                await g.on_key(await io.readkey())
                await g.show()
            await io.readkey()
        with tc.assertRaises(EOFError):
            asyncio.run(drive())
        tc.assertEqual(io.vt.errors, [], mode)
        for scr in io.screens:
            tc.assertEqual(len(scr), 24)
            tc.assertTrue(all(len(l) <= 80 for l in scr), mode)
            tc.assertLessEqual(len(scr[23]), 79, mode)
        out[mode] = ("\n".join("\n".join(scr) for scr in io.screens), core.reqs)
    return out


class AttrScreen:
    """An 80x24 terminal that keeps each cell's character and how it looks (bold, reverse video, foreground,
    background), replaying what the Painter sends: SGR, cursor moves, clears and text."""

    BLANK = (" ", False, False, None, None)

    def __init__(self):
        self.cells = [[self.BLANK] * 80 for _ in range(24)]
        self.x = self.y = 0
        self.look = [False, False, None, None]

    def feed(self, text):
        i = 0
        while i < len(text):
            c = text[i]
            if c == "\x1b" and text[i + 1:i + 2] == "[":
                j = i + 2
                while not "@" <= text[j] <= "~":
                    j += 1
                params, final = text[i + 2:j], text[j]
                nums = [int(n) if n.isdigit() else 0 for n in params.split(";")] if params else []
                if final == "m":
                    codes = params.split(";") if params else ["0"]
                    if codes[0] in ("", "0"):
                        self.look = [False, False, None, None]
                    b, r, f, g = T.Painter._parts(params)
                    self.look = [self.look[0] or b, self.look[1] or r, f or self.look[2], g or self.look[3]]
                elif final == "H":
                    self.y = (nums[0] if nums and nums[0] else 1) - 1
                    self.x = (nums[1] if len(nums) > 1 and nums[1] else 1) - 1
                elif final == "C":
                    self.x += nums[0] if nums and nums[0] else 1
                elif final == "J":
                    self.cells = [[self.BLANK] * 80 for _ in range(24)]
                i = j + 1
                continue
            if c == "\r":
                self.x = 0
            elif c == "\n":
                self.y += 1
            elif c >= " ":
                self.cells[self.y][self.x] = (c,) + tuple(self.look)
                self.x += 1
            i += 1

    @staticmethod
    def look_of(attr):
        """How a Canvas attribute ("0;7;30;47") looks: (bold, reverse, foreground, background)."""
        return T.Painter._parts(attr)

    def wrong_cells(self, cv):
        """The cells that do not show what the canvas holds: [(x, y, wanted, shown)]."""
        out = []
        for y in range(24):
            for x in range(80 if y < 23 else 79):
                want = (cv.ch[y][x],) + self.look_of(cv.at[y][x])
                if self.cells[y][x] != want:
                    out.append((x, y, want, self.cells[y][x]))
        return out


def plain_screen(tc, module, lines, me=0, names=(LONG, LONG.upper()), prepare=None):
    """The module's plain screen with the view lines given: every line (and the prompt) at most 80
    columns and ASCII. Returns the text."""
    io = FakeIO("plain")
    s = session(io, ScreenCore(module.KEY, lines, names), "plain")
    g = module.PlainScreen(s, "3", module.KEY, me)
    g.names, g.v = list(names), g.parse(lines)
    g.log = ["x" * 120] * 6
    if prepare:
        prepare(g)
    out = g.lines() + g.full_prompt().split("\n")
    for l in out:
        tc.assertLessEqual(len(l), 80, l)
        l.encode("ascii")
    return "\n".join(out)


HTML32_BAD = ("<script", "<style", "style=", "class=", "javascript:", "&#x", "<div", "<span", "id=")
# What web.py's link() adds to every link of a signed-in page, here as for a browser without cookies: the
# session (s=) and the action check (x=). Every link and every form of a game's page must carry both.
SID, CSRF = "S" * 22, "X" * 43


def page_link(path, **q):
    """A link as web.py's link() makes it for a browser without cookies."""
    from adapters.web import url
    return url(path, s=SID, x=CSRF, **q)


MAX_IMAGES = 4          # distinct images a game page may ask for (the board, a few small ones)


def board_image(tc, module, lines, seat=0, sel="", events=()):
    """The module's board image (board_image) for the view lines given, as seat, checked: a GIF that
    decodes to the canvas's size and pixels, and every area inside the image, leading to a page /click.htm
    may answer with a move the game's MOVE_RE takes (/move.htm) or a sel= its SEL_RE takes (/table.htm).
    events: the table's events, for the module's web_events. Returns the Canvas (used: the tiles drawn,
    words: the text, areas)."""
    from adapters import board, web
    v = module.parse_view(lines)
    if getattr(module, "web_events", None):
        module.web_events(v, list(events))
    c = module.board_image(v, seat, sel)
    gif = c.gif()
    img = board.decode_gif(gif)
    tc.assertEqual((img.w, img.h), (c.w, c.h))
    tc.assertEqual(img.pixels, bytes(c.px))
    tc.assertLessEqual(len(c.palette), 256)
    for shape, coords, alt, path, params in c.areas:
        tc.assertIn(shape, ("rect", "circle", "poly"))
        tc.assertIn(path, web.CLICK_TARGETS)
        tc.assertTrue(alt)
        xs, ys = (coords[0::2], coords[1::2]) if shape != "circle" else ([coords[0]], [coords[1]])
        tc.assertTrue(all(-c.w <= x <= 2 * c.w for x in xs) and all(-c.h <= y <= 2 * c.h for y in ys), coords)
        q = dict(params)
        if path == "/move.htm":
            tc.assertTrue(module.MOVE_RE.match(q["mv"]), q)
        if "sel" in q:
            tc.assertTrue(module.SEL_RE.match(q["sel"]), q)
        hit = board.hit(c.areas, *_inside_point(shape, coords))
        tc.assertIsNotNone(hit)
    return c


def _inside_point(shape, coords):
    """A point inside an area: a rect's or circle's center, a polygon's vertex average."""
    if shape == "rect":
        return (coords[0] + coords[2]) // 2, (coords[1] + coords[3]) // 2
    if shape == "circle":
        return coords[0], coords[1]
    return sum(coords[0::2]) // (len(coords) // 2), sum(coords[1::2]) // (len(coords) // 2)


def web_page(tc, module, lines, seat=0, names=(LONG, "bob"), sel="", check=None, events=()):
    """The module's web parts for the view lines given, as seat (-1 watches): HTML 3.2 only (no CSS,
    script, div or span; named or decimal entities; ASCII), every image one of the module's own (or a
    shared one) or its board image (/board.gif, with the session) with width, height and alt, at most
    MAX_IMAGES of them, every link to a page of this site, every map area's link with s= and x=. check:
    /check.htm's (move, reply), put in the view as web.py does; events for the module's web_events.
    Returns the parts joined."""
    v = module.parse_view(lines)
    if check:
        v["check"] = check
    if getattr(module, "web_events", None):
        module.web_events(v, list(events))

    parts = module.web_parts(v, "3", page_link, seat, list(names), sel)
    tc.assertEqual(len(parts), 4)
    html = "\n".join(parts)
    low = html.lower()
    for bad in HTML32_BAD:
        tc.assertNotIn(bad, low)
    tc.assertEqual(re.findall(r"&(?!(?:amp|lt|gt|quot|nbsp|#\d+);)", html), [])
    tc.assertTrue(all(ord(ch) < 128 for ch in html))
    images = module.images()
    srcs = set()
    for tag in re.findall(r"<img [^>]*>", html):
        src = re.search(r'src="([^"]+)"', tag).group(1)
        srcs.add(src)
        if src.startswith("/board.gif?"):
            tc.assertIn("s=%s" % SID, src)
            tc.assertNotRegex(src, r"[?;]x=")         # the image changes nothing: no action check
            if "usemap=" in tag:
                tc.assertIn(' ismap usemap="#board"', tag)
        else:
            tc.assertIn(src, images, tag)
        for attr in ("width=", "height=", "alt="):
            tc.assertIn(attr, tag)
    tc.assertLessEqual(len(srcs), MAX_IMAGES)
    for area in re.findall(r"<area [^>]*>", html):
        tc.assertRegex(area, r'^<area shape="(rect|circle|poly)" coords="[0-9,-]+" href="[^"]+" alt="[^"]*">$')
    for href in re.findall(r'href="([^"]+)"', html):
        tc.assertTrue(href.startswith("/") and ".htm" in href, href)
        tc.assertIn("s=%s;x=%s" % (SID, CSRF), href)       # ; between fields, never &amp; (web.url)
        tc.assertNotIn("&", href)
    for form in re.findall(r"<form .*?</form>", html, re.S):     # GET forms carry them as hidden fields
        for name, value in (("s", SID), ("x", CSRF)):
            tc.assertIn('<input type="hidden" name="%s" value="%s">' % (name, value), form)
    return html


def module_problems(module):
    """What the registry would refuse, plus the image rules: GIFs under /img/<game>/ (Reversi and
    Backgammon keep the paths they had before the registry)."""
    from adapters import games
    out = games.problems(module)
    word = module.KEY.split("/")[0]
    legacy = {"reversi": "/img/", "backgammon": "/img/bg/"}.get(word)
    for path, data in module.images().items():
        if not path.startswith(legacy or "/img/%s/" % word) or not path.endswith(".gif"):
            out.append("image %s is not under /img/%s/" % (path, word))
        if not data.startswith(b"GIF89a") or not data.endswith(b"\x00;"):
            out.append("image %s is not a GIF" % path)
    return out
