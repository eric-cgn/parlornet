"""One image per board (adapters/board.py, web.board_html, /board.gif, /click.htm): the GIF encoder, every
game's board image and its map, what one page costs in requests, hidden pieces, the cache, keep-alive, and
Cannon Fire's craters on the web and by telnet."""
import asyncio
import http.client
import random
import re
import socket
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from game_checks import LONG, MAX_IMAGES, ScreenCore, board_image, session, web_page
from test_adapters import Client, FakeIO, Native, Stack

import test_cannonfire as TC
import test_gridwords as TG
import test_navalcombat as TN
from adapters import board as B, games, web as W
from adapters.games import cannonfire as CF, gridwords as GW, navalcombat as NC

# a view of each game with something to click, as (lines, seat, sel)
SAMPLES = {
    "reversi/1": (["V reversi/1 5", "BRD 8", "R 1 ........", "R 2 ........", "R 3 ........", "R 4 ...01...",
                   "R 5 ...10...", "R 6 ........", "R 7 ........", "R 8 ........", "C 2 2", "W 0", "M d3", "M c4",
                   "M f5", "M e6", "S Black to move"], 0, ""),
    "backgammon/1": (["V backgammon/1 7", "P 1 -2 0 0 0 0 7 0 3 0 0 0 -5", "P 13 4 0 0 0 -3 0 -4 0 0 0 0 0",
                      "B 1 0", "O 0 1", "D 3 5", "C 2 0", "W 0", "Q move", "M bar-22", "M bar-20",
                      "S White to move"], 0, ""),
    "checkers/1": (["V checkers/1 3", "BRD 8", "B xxxxxxxxxxxX........oooooooooooO", "C 11 1 11 1", "W 0", "Q move",
                    "M 9-13", "M 9-14", "M 10-14", "M 10-15", "M 11-15", "M 11-16", "M 12-16", "S Black to move"],
                   0, "10"),
    "fiveinarow/1": (["V fiveinarow/1 9", "BRD 19", "RUL freestyle", "P 0 h8 i9 j10", "P 1 h9 h10", "C 3 2",
                      "L 0 j10", "W 1", "S White to move"], 1, ""),
    "dotsandboxes/1": (["V dotsandboxes/1 9", "BRD 4 3", "HL 1 --..", "HL 2 --..", "HL 3 ....", "HL 4 ....",
                        "VL 1 |||..", "VL 2 .....", "VL 3 .....", "BX 1 01..", "BX 2 ....", "BX 3 ....", "C 1 1",
                        "LL b1-b2", "W 0", "M c1-d1", "M d1-e1", "M a2-a3", "S A to move"], 0, ""),
    "mancala/1": (["V mancala/1 4", "B 4 4 0 5 5 5 1 4 4 4 4 4 26 0", "RU kalah 4", "LM 0 3", "W 1", "M 1", "M 2",
                   "M 3", "S bob to move"], 1, ""),
}


def samples():
    """Every game with a board image: (module, lines, seat, sel, events)."""
    out = [(games.get(k), lines, seat, sel, ()) for k, (lines, seat, sel) in SAMPLES.items()]
    st = TN.late_game()
    out.append((NC, TN.vue(st, 0), 0, "", ()))
    out.append((NC, TN.vue(TN.placing_view(0, 2), 0), 0, "a5", ()))
    out.append((GW, TG.view_lines(TG.midgame(), 0), 0, "8.10.V", ()))
    st, events = TC.midgame(shots=1)
    assert st["turn"] >= 0
    out.append((CF, TC.view_lines(st, st["turn"]), st["turn"], "", events))
    return out


def gif_of(data):
    return B.decode_gif(data)


class Gif(unittest.TestCase):
    def test_round_trip(self):
        rnd = random.Random(5)
        for _ in range(150):
            w, h = rnd.randint(1, 70), rnd.randint(1, 70)
            n = rnd.choice([1, 2, 3, 4, 7, 16, 33, 200, 256])
            pal = [(i, 255 - i, i // 2) for i in range(n)]
            if rnd.random() < 0.5:
                px = bytes(rnd.randrange(n) for _ in range(w * h))
            else:
                px = bytes((x // 5 + y // 3) % n for y in range(h) for x in range(w))
            img = gif_of(B.encode_gif(w, h, pal, px))
            self.assertEqual((img.w, img.h, img.pixels), (w, h, px))
            self.assertEqual(img.palette[:n], pal)
        px = bytes(rnd.randrange(256) for _ in range(300 * 300))      # the code table fills and starts again
        self.assertEqual(gif_of(B.encode_gif(300, 300, [(i, i, i) for i in range(256)], px)).pixels, px)

    def test_old_tiles_decode(self):
        for path, data in W.IMAGES.items():
            img = gif_of(data)
            self.assertGreater(img.w * img.h, 0, path)

    def test_hit(self):
        areas = [("rect", (0, 0, 9, 9), "a", "/move.htm", ()), ("circle", (20, 20, 5), "b", "/move.htm", ()),
                 ("poly", (30, 0, 40, 10, 30, 20, 20, 10), "c", "/move.htm", ())]
        self.assertEqual(B.hit(areas, 9, 9)[2], "a")
        self.assertEqual(B.hit(areas, 23, 23)[2], "b")
        self.assertEqual(B.hit(areas, 30, 10)[2], "c")
        self.assertIsNone(B.hit(areas, 15, 2))
        self.assertIsNone(B.hit(areas, 21, 2))


class Boards(unittest.TestCase):
    def test_every_game(self):
        """Every game with a board image: a valid GIF with a valid map, and a page that asks for at most
        MAX_IMAGES images; every game module but Rummy Tile (tiles as text) has one."""
        seen = set()
        for module, lines, seat, sel, events in samples():
            seen.add(module.KEY)
            with self.subTest(game=module.KEY, sel=sel):
                c = board_image(self, module, lines, seat, sel, events)
                self.assertTrue(c.areas or c.anywhere, "nothing to click")
                html = web_page(self, module, lines, seat, sel=sel, events=events)
                self.assertEqual(len(re.findall(r'<img src="/board\.gif\?', html)), 1)
                srcs = set(re.findall(r'<img src="([^"]+)"', html))
                self.assertEqual(len(srcs), 1)                  # the board, everything drawn in it
                self.assertLessEqual(len(srcs), MAX_IMAGES)
                for href in re.findall(r'<area [^>]*href="([^"]+)"', html):
                    self.assertIn("x=", href)
        self.assertEqual(seen | {"rummytile/1"}, {m.KEY for m in games.all_games()})
        self.assertFalse(hasattr(games.get("rummytile/1"), "board_image"))

    def test_render_time_and_size(self):
        for module, lines, seat, sel, events in samples():
            v = module.parse_view(lines)
            if getattr(module, "web_events", None):
                module.web_events(v, list(events))
            module.board_image(v, seat, sel)              # the tiles decoded once
            t0 = time.perf_counter()
            gif = module.board_image(v, seat, sel).gif()
            ms = (time.perf_counter() - t0) * 1000
            self.assertLess(ms, 400, module.KEY)
            self.assertLess(len(gif), 24000, module.KEY)

    def test_cache_is_bounded(self):
        cache = B.BoardCache(max_bytes=60000, per_table=4)
        c = B.Canvas(100, 100)
        c.fill(10, 10, 50, 50, (200, 0, 0))
        for t in range(30):
            for seq in range(8):
                cache.render(("reversi", str(t), 0, "", "d%d" % seq), lambda: c)
        self.assertLessEqual(cache.bytes, 60000)
        self.assertEqual(cache.bytes, sum(b.cost for b in cache.items.values()))
        per = {}
        for k in cache.items:
            per[k[:2]] = per.get(k[:2], 0) + 1
        self.assertLessEqual(max(per.values()), 4)
        self.assertIn(("reversi", "29", 0, "", "d7"), cache.items)          # the newest is kept
        self.assertIsNotNone(cache.get(("reversi", "29", 0, "", "d7")))

    def test_digest_ignores_the_clock(self):
        lines = SAMPLES["reversi/1"][0]
        a = games.get("reversi/1").parse_view(lines + ["CLK 0 100 300"])
        b = games.get("reversi/1").parse_view(lines + ["CLK 0 99 300"])
        self.assertEqual(B.view_digest(a), B.view_digest(b))
        b["check"] = ("d3", None)
        self.assertEqual(B.view_digest(a), B.view_digest(b))
        b["rows"] = b["rows"][:-1] + ["........"]
        b["counts"] = (3, 2)
        self.assertNotEqual(B.view_digest(a), B.view_digest(b))


SHIP = NC.PAL[3]           # a ship's gray: on a hit too, never on sea, a miss or a sunk square


class Hidden(unittest.TestCase):
    """A board image is drawn from the viewer's own view: another fleet or rack never shows."""

    def grid_pixels(self, c, i):
        n = 10
        x0 = NC.grid_x(i, n)
        return [c.get(x, y) for y in range(NC.MARGIN, NC.MARGIN + n * NC.CELL)
                for x in range(x0, x0 + n * NC.CELL)]

    def test_fleets(self):
        st = TN.started()
        for seat in (0, 1):
            c = board_image(self, NC, TN.vue(st, seat), seat)
            self.assertIn(SHIP, self.grid_pixels(c, 0))           # your own fleet
            self.assertNotIn(SHIP, self.grid_pixels(c, 1))        # nothing of the other's, before any hit
        c = board_image(self, NC, TN.vue(st, -1), -1)
        self.assertNotIn(SHIP, self.grid_pixels(c, 0) + self.grid_pixels(c, 1))
        # shots fired: the other's grid shows only hit and sunk squares, never a ship that was not hit
        st = TN.late_game()
        v = NC.parse_view(TN.vue(st, 0))
        c = board_image(self, NC, TN.vue(st, 0), 0)
        hits = {(x, y) for y, row in enumerate(v["boards"][1]) for x, ch in enumerate(row) if ch == "x"}
        for y in range(10):
            for x in range(10):
                px = [c.get(NC.grid_x(1, 10) + x * NC.CELL + i, NC.MARGIN + y * NC.CELL + i) for i in range(2, 18)]
                self.assertEqual(SHIP in px, (x, y) in hits, (x, y))

    def test_racks(self):
        st = TG.midgame()
        racks = {}
        for seat in (0, 1):
            c = board_image(self, GW, TG.view_lines(st, seat), seat)
            y_rack = GW.MARGIN + 15 * GW.CELL + GW.RACK_GAP
            racks[seat] = [p for p, _, y in c.used if y == y_rack]
            v = GW.parse_view(TG.view_lines(st, seat))
            self.assertEqual(racks[seat], [GW.tile_name(t) for t in v["rack"]])
        self.assertNotEqual(racks[0], racks[1])
        watch = board_image(self, GW, TG.view_lines(st, -1), -1)
        seat0 = board_image(self, GW, TG.view_lines(st, 0), 0)
        self.assertLess(watch.h, seat0.h)                          # no rack at all
        self.assertEqual({y for _, _, y in watch.used}, {GW.MARGIN + i * GW.CELL for i in range(15)})


def board_src(body):
    m = re.search(r'<img src="(/board\.gif\?[^"]+)" width="(\d+)" height="(\d+)"', body)
    return (m.group(1).replace("&amp;", "&"), int(m.group(2)), int(m.group(3))) if m else None


def img_srcs(body):
    return set(re.findall(r'<img src="([^"]+)"', body))


class Live(Stack):
    """The web extension and the core: every game's page, /board.gif, /click.htm, keep-alive."""

    def players(self, *names):
        out = []
        for n in names:
            b = self.browser()
            b.login(n, n + "-Pass-word")
            out.append(b)
        return out

    def raw(self, b, path, headers=None):
        """A GET from browser b's cookies, keeping the response's headers."""
        c = http.client.HTTPConnection("127.0.0.1", self.web_port, timeout=10)
        h = {"Host": "parlornet.org:6500", "Cookie": "; ".join("%s=%s" % kv for kv in b.jar.items())}
        h.update(headers or {})
        c.request("GET", path, headers=h)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, dict((k.lower(), v) for k, v in r.getheaders()), body

    def new_table(self, a, b, key):
        st, loc, _, _ = a.act("/new.htm?g=%s&turn=1d" % key.replace("/", "%2F"))
        self.assertEqual(st, 302, key)
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        self.assertEqual(b.act("/join.htm?t=%s" % tid)[0], 302)
        return tid

    def test_every_game_page(self):
        """Each game's page asks for at most MAX_IMAGES images, one of them the board: a GIF of the size
        the page says, kept by the browser (private, with Last-Modified; 304 when asked again), for both
        seats and a watcher."""
        ann, bob, cy = self.players("ann", "bob", "cy")
        for m in games.all_games():
            with self.subTest(game=m.KEY):
                tid = self.new_table(ann, bob, m.KEY)
                for b, extra in ((ann, ""), (bob, ""), (cy, "&w=1")):
                    body = b.get("/table.htm?t=%s&r=0%s" % (tid, extra))[3]
                    self.assertLessEqual(len(img_srcs(body)), 1 if m.KEY != "rummytile/1" else 0, body[-800:])
                    got = board_src(body)
                    if m.KEY == "rummytile/1":
                        self.assertIsNone(got)
                        continue
                    src, w, h = got
                    st, hd, data = self.raw(b, src)
                    self.assertEqual((st, hd["content-type"]), (200, "image/gif"))
                    img = gif_of(data)
                    self.assertEqual((img.w, img.h), (w, h))
                    self.assertEqual(hd["cache-control"], "private, max-age=86400")
                    st, hd2, data = self.raw(b, src, {"If-Modified-Since": hd["last-modified"]})
                    self.assertEqual((st, data), (304, b""))

    def test_board_needs_the_session(self):
        ann, bob = self.players("ann", "bob")
        tid = self.new_table(ann, bob, "reversi/1")
        src = board_src(ann.get("/table.htm?t=%s" % tid)[3])[0]
        st, hd, body = self.raw(self.browser(), src)                       # no session: the welcome page
        self.assertEqual(hd["content-type"], "text/html")
        self.assertIn(b"Sign in to play", body)

    def test_click_reaches_the_area_link(self):
        """/click.htm?...?x,y inside an area plays that area's move, as following its link does; outside every
        area, or from a page whose board has changed since, it shows the table and plays nothing; it needs
        the action check (x=) like any action."""
        ann, bob = self.players("ann", "bob")
        nat = Native(self.core, "ann", "ann-Pass-word")
        self.addCleanup(nat.close)

        def events(tid):
            return [l.split(" ", 3)[3] for l in nat.req("SIN %s 0" % tid) if l.startswith("EVT ")]
        moved = []
        for how in ("click", "link"):
            tid = self.new_table(ann, bob, "reversi/1")
            body = ann.get("/table.htm?t=%s&r=0" % tid)[3]
            areas = re.findall(r'<area shape="rect" coords="(\d+),(\d+),(\d+),(\d+)" href="([^"]+)" alt="([^"]+)">',
                               body)
            self.assertEqual(len(areas), 4)
            x1, y1, x2, y2, href, alt = areas[0]
            click = re.search(r'<a href="(/click\.htm\?[^"]+)"><img src="/board\.gif', body).group(1).replace("&amp;", "&")
            self.assertIn("x=", click)
            before = events(tid)
            if how == "click":
                st, _, _, page = ann.get(click + "?1,1")                    # the corner: no area
                self.assertEqual(st, 200)
                self.assertIn("Reversi - table %s" % tid, page)
                self.assertEqual(events(tid), before)
                stale = re.sub(r"d=[0-9a-f]+", "d=000000000000", click)
                st, _, _, page = ann.get(stale + "?%d,%d" % ((int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2))
                self.assertEqual(st, 200)
                self.assertEqual(events(tid), before)
                no_x = re.sub(r"x=[0-9a-f]+&", "", click)
                page = ann.get(no_x + "?%d,%d" % ((int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2))[3]
                self.assertIn("That link is out of date.", page)
                st, loc, _, _ = ann.get(click + "?%d,%d" % ((int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2))
            else:
                st, loc, _, _ = ann.get(href.replace("&amp;", "&"))
            self.assertEqual(st, 302)
            self.assertIn("/table.htm?", loc)
            moved.append([e for e in events(tid) if e not in before])
        self.assertEqual(moved[0], moved[1])
        self.assertTrue(moved[0][0].startswith("PUT 0 "), moved)

    def test_board_url_shows_only_its_own_seat(self):
        """Naval Combat: one player's board URL, fetched with the other player's or a watcher's session, gives
        that session's own view (not kept by the browser): never the first player's fleet."""
        ann, bob, cy = self.players("ann", "bob", "cy")
        tid = self.new_table(ann, bob, "navalcombat/1")
        for b in (ann, bob):
            body = b.get("/table.htm?t=%s&r=0" % tid)[3]
            for mv in ("random", "ready"):
                st, loc, _, _ = b.get(b.link(r"^/move\.htm\?.*mv=%s" % mv, body))
                body = b.go(loc)[3]
        src = board_src(ann.get("/table.htm?t=%s" % tid)[3])[0]
        st, hd, data = self.raw(ann, src)
        mine = gif_of(data)
        self.assertEqual(hd["cache-control"], "private, max-age=86400")

        def grid(img, i):
            x0 = NC.grid_x(i, 10)
            return {img.palette[img.pixels[y * img.w + x]] for y in range(NC.MARGIN, NC.MARGIN + 200)
                    for x in range(x0, x0 + 200)}
        self.assertIn(SHIP, grid(mine, 0))
        for other in (bob, cy):
            if other is cy:
                cy.get("/table.htm?t=%s&w=1" % tid)
            st, hd, data = self.raw(other, src)
            self.assertEqual(st, 200)
            self.assertEqual(hd["cache-control"], "no-store")            # not this session's URL
            img = gif_of(data)
            left_seat_is_ann = other is cy                                 # a watcher sees seat 0 on the left
            self.assertNotIn(SHIP, grid(img, 0 if left_seat_is_ann else 1))
            if other is cy:
                self.assertNotIn(SHIP, grid(img, 1))

    def test_keep_alive(self):
        """HTTP/1.0 with Connection: keep-alive, and HTTP/1.1, may send more requests on one connection;
        Connection: close (or plain HTTP/1.0) ends it after one."""
        for version, conn, keeps in (("HTTP/1.0", "Keep-Alive", True), ("HTTP/1.1", "", True),
                                     ("HTTP/1.1", "close", False), ("HTTP/1.0", "", False)):
            s = socket.create_connection(("127.0.0.1", self.web_port), timeout=5)
            self.addCleanup(s.close)
            req = "GET /welcome.htm %s\r\nHost: parlornet.org\r\n%s\r\n" % (
                version, "Connection: %s\r\n" % conn if conn else "")
            s.sendall(req.encode())
            first = self.read_response(s)
            self.assertIn(b"200 OK", first[0])
            self.assertIn(b"Connection: %s" % (b"keep-alive" if keeps else b"close"), first[0])
            s.sendall(req.encode())
            try:
                second = self.read_response(s)
            except (ConnectionError, socket.timeout, ValueError):
                second = None
            self.assertEqual(second is not None, keeps, (version, conn))

    @staticmethod
    def read_response(s):
        data = b""
        while b"\r\n\r\n" not in data:
            d = s.recv(65536)
            if not d:
                raise ConnectionError("closed")
            data += d
        head, _, body = data.partition(b"\r\n\r\n")
        n = int(re.search(rb"Content-Length: (\d+)", head).group(1))
        while len(body) < n:
            d = s.recv(65536)
            if not d:
                raise ConnectionError("closed")
            body += d
        return head, body


class Craters(Stack):
    """Cannon Fire: after a hit, every viewer's page and board image, and a telnet screen, show the new
    ground; the crater (radius 7) is plain to see, the freshly dug ground in its own shade."""

    def test_the_crater_shows_everywhere(self):
        ola, pia, cy = (self.browser() for _ in range(3))
        for name, b in (("ola", ola), ("pia", pia), ("cy", cy)):
            b.login(name, name + "-Pass-word")
        nat = Native(self.core, "ola", "ola-Pass-word")
        self.addCleanup(nat.close)
        tid = nat.req("NEW cannonfire classic seed=2026")[-1].split()[1]
        self.assertEqual(nat.req("JON %s" % tid)[-1], "OK 0")
        self.assertEqual(pia.act("/join.htm?t=%s" % tid)[0], 302)
        tel = Client(self.tel_port)
        self.addCleanup(tel.close)
        tel.until("Login")
        tel.send("new\r\n")
        tel.until("Name you want")
        tel.send("tim\r\n")
        tel.until("Choose a password")
        tel.send("tim-Pass-word\r\n")
        tel.until("Type it again")
        tel.send("tim-Pass-word\r\n")
        tel.until("Choice [")
        tel.send("4\r\n")                                   # plain text
        tel.until("Press Enter")
        tel.send("\r\n")
        tel.until("Your tables")
        tel.send("watch %s\r\n" % tid)
        tel.until("you are watching ==")
        before_text = self.landscape(tel.until(" +" + "-" * 72 + "+\r\n", timeout=8) and tel.raw.decode("latin-1"))

        def heights():
            z = sorted((int(l.split()[1]), l.split()[2]) for l in nat.req("VUE %s G" % tid) if l.startswith("Z "))
            return [ord(ch) - 0x30 for _, part in z for ch in part]

        def boards():
            out = {}
            for who, b, extra in (("ola", ola, ""), ("pia", pia, ""), ("cy", cy, "&w=1")):
                body = b.get("/table.htm?t=%s&r=0%s" % (tid, extra))[3]
                src = board_src(body)[0]
                out[who] = (src, gif_of(self.raw_get(b, src)))
            return out
        h0, b0 = heights(), boards()
        tel.pump()
        tel.mark = len(tel.raw)
        st, _, _, _ = ola.act("/move.htm?t=%s&angle=45&power=60" % tid)       # the recorded first shot: HIT 66 6 7
        self.assertEqual(st, 302)
        h1, b1 = heights(), boards()
        changed = [i for i in range(72) if h1[i] != h0[i]]
        self.assertGreaterEqual(len(changed), 10)
        self.assertGreaterEqual(max(h0[i] - h1[i] for i in changed), 5)
        k = CF.scale({"w": 72})
        height = (CF.HMAX + 1 + CF.TOP) * k
        for who in b0:
            src0, img0 = b0[who]
            src1, img1 = b1[who]
            self.assertNotEqual(src0, src1, who)                          # a new image for the new view
            colours0 = {img0.palette[p] for p in set(img0.pixels)}
            colours1 = {img1.palette[p] for p in set(img1.pixels)}
            self.assertNotIn(CF.FRESH, colours0, who)
            self.assertIn(CF.FRESH, colours1, who)                        # the freshly dug ground
            for i in changed:
                if h0[i] - h1[i] >= 3 and abs(i - 66) > 2 and abs(i - 61) > 3:
                    x, y = i * k + 1, height - 1 - (h0[i] - 1) * k     # just under the old surface
                    was, now = img0.palette[img0.pixels[y * img0.w + x]], img1.palette[img1.pixels[y * img1.w + x]]
                    self.assertNotIn(was, CF.SKY_BANDS, (who, i))
                    self.assertIn(now, CF.SKY_BANDS + [CF.TRAIL], (who, i))
        tel.until("falls with the ground: 90 hp", timeout=8)   # the log under the landscape drawn after the hit
        after_text = self.landscape(tel.raw.decode("latin-1"))
        self.assertNotEqual(before_text, after_text)

    @staticmethod
    def landscape(text):
        """The last landscape printed by the plain telnet screen: the lines between its two frames."""
        frame = " +" + "-" * 72 + "+"
        parts = text.replace("\r", "").split(frame + "\n")
        return parts[-2] if len(parts) >= 3 else ""

    def raw_get(self, b, path):
        c = http.client.HTTPConnection("127.0.0.1", self.web_port, timeout=10)
        c.request("GET", path, headers={"Host": "parlornet.org:6500",
                                        "Cookie": "; ".join("%s=%s" % kv for kv in b.jar.items())})
        r = c.getresponse()
        data = r.read()
        c.close()
        self.assertEqual(r.status, 200)
        return data


class TelnetScreenAfterAHit(unittest.TestCase):
    """The full-screen telnet landscape is drawn from the view it last fetched: after a hit (a new VUE with
    the crater) the screen shows the new ground."""

    def test_full_screen(self):
        st = TC.new(2, 2026)
        after = TC.fire(st, 0, 45, 60)["state"]
        screens = []
        for state in (st, after):
            io = FakeIO("ansi")
            core = ScreenCore(CF.KEY, TC.view_lines(state, 1), (LONG, "bob"))
            s = session(io, core, "ansi")
            g = CF.AnsiScreen(s, "3", CF.KEY, 1)
            g.names, g.turn_secs = [LONG, "bob"], 300

            async def go(g=g):
                await g.refresh()
                return g.compose()
            cv = asyncio.run(go())
            screens.append(["".join(row) for row in cv.ch[CF.LAND_TOP:CF.LAND_TOP + CF.LAND_ROWS]])
        self.assertNotEqual(screens[0], screens[1])


if __name__ == "__main__":
    unittest.main()
