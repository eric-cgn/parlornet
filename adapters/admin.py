"""Admin adapter: ParlorNet's administration as blue-screen menus over telnet.

In the manner of dialog and whiptail: a blue screen, gray dialog boxes with
shadows, highlighted buttons; arrow keys, Tab, Enter and Esc. 80x24, in the
pc (CP437), utf8 and ASCII character sets, ANSI color.

It listens on the container's admin port (6510) and talks to the core's admin
socket (parlord/admin.py), which answers only on loopback. It has no power of
its own: every change is a request to the core, after the admin password.

    python3 -m adapters.admin --port 6510 --core-admin 127.0.0.1:6599
"""
import argparse
import asyncio
import json
import os
import ssl
import sys
import textwrap
import time

from . import catalog
from .telnet import (CHARSET, CLEAR, COLS, ROWS, Canvas, Painter, TelnetIO, fmt_secs, glyph_set,
                     mode_for_ttype)

MAX_SESSIONS = 4
MODES = [("pc", "IBM PC characters, ANSI color"), ("utf8", "Unicode characters, ANSI color"),
         ("ansi", "ASCII characters, ANSI color")]
BLUE_CLEAR = "\x1b[0;37;44m\x1b[2J\x1b[H"   # erase to blue (terminals erase with the current background)

# dialog's colors
S_SCREEN = "0;37;44"
S_BACK = "0;1;36;44"
S_HINT = "0;36;44"
D_BODY = "0;30;47"
D_LIGHT = "0;1;37;47"
D_DARK = "0;30;47"
D_TITLE = "0;1;34;47"
D_TAG = "0;1;34;47"
D_HOT = "0;31;47"
D_SEL = "0;1;37;44"
D_SELHOT = "0;1;33;44"
D_FIELD = "0;1;37;46"
D_NOTE = "0;34;47"
SHADOW = "0;1;30;40"

TURNS = [("5m", 300, "5 minutes"), ("15m", 900, "15 minutes"), ("1h", 3600, "1 hour"), ("1d", 86400, "1 day"),
         ("3d", 259200, "3 days"), ("5d", 432000, "5 days")]
SIGNIN_METHODS = [("none", "a name alone: a LAN, a conference, a festival"),
                  ("password", "name plus a password the player chose")]
SIGNUP_METHODS = [("none", "claim a free name (with sign-in none)"),
                  ("password", "choose a password, type it again (with sign-in password)"),
                  ("email", "mail a password to an address; the player then chooses one (with sign-in "
                            "password)")]
LIMIT_TAGS = {"per_address_hour": "Address/hour", "per_address_day": "Address/day",
              "per_name_hour": "Name/hour", "per_name_day": "Name/day", "per_connection": "Connection",
              "per_minute": "Server/minute", "per_hour": "Server/hour", "per_day": "Server/day",
              "new_per_day": "New/day"}

H_MENU = "Arrows choose, Enter selects, Tab moves to the buttons, Esc goes back"
H_CHECK = "Arrows choose, Space turns on or off, Enter saves, Esc cancels"
H_RADIO = "Arrows choose, Enter selects, Esc cancels"
H_INPUT = "Type, then Enter. Tab moves to the buttons, Esc cancels"
H_EDIT = "Type the text. Tab moves to OK and Cancel, Esc cancels"
H_MSG = "Enter continues"



def length_text(secs):
    """Seconds as the shortest exact length the settings take back: 2h, 30m, 90s."""
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs and secs % n == 0:
            return "%d%s" % (secs // n, unit)
    return "%ds" % secs

class AdminIO(TelnetIO):
    """TelnetIO plus Tab, Shift-Tab and Delete."""

    def _byte(self, b):
        if self.esc == 0 and b == 9:
            self.keys.append("TAB")
            self.cr = False
            return
        if self.esc == 2 and b == ord("Z") and self.csi == "":
            self.esc = 0
            self.keys.append("BTAB")
            return
        if self.esc == 2 and b == ord("~") and self.csi == "3":
            self.esc = 0
            self.keys.append("DEL")
            return
        super()._byte(b)


class CoreGone(Exception):
    pass


class AdminCore:
    """One connection to the core's admin socket (JSON lines), over TLS 1.3
    with this adapter's admin-role key (python -m parlord.frontends add admin
    --role admin), then the admin password."""

    key_file = os.environ.get("PARLOR_ADMIN_KEY")

    def __init__(self, r, w):
        self.r, self.w = r, w

    @classmethod
    async def connect(cls, host, port, key_file=None):
        from parlord.frontends import client_context, read_key
        path = key_file or cls.key_file
        if not path:
            raise CoreGone("the admin adapter has no key for the core (--key)")
        try:
            ident, _, key = read_key(path)
            r, w = await asyncio.wait_for(asyncio.open_connection(
                host, port, ssl=client_context(ident, key), server_hostname="parlord", limit=1 << 20), 5)
        except ConnectionRefusedError:
            raise
        except (OSError, ValueError, ssl.SSLError, asyncio.IncompleteReadError) as e:
            raise CoreGone("the core refused the admin key (%s)" % type(e).__name__)
        so = w.get_extra_info("ssl_object")
        if so is None or so.version() != "TLSv1.3" or not so.session_reused:
            w.close()
            raise CoreGone("the admin channel is not TLS 1.3 with this key")
        return cls(r, w)

    async def req(self, op, **kw):
        kw["op"] = op
        try:
            self.w.write((json.dumps(kw) + "\n").encode())
            await self.w.drain()
            line = await asyncio.wait_for(self.r.readline(), 60)
        except (OSError, asyncio.TimeoutError) as e:
            raise CoreGone(str(e))
        if not line:
            raise CoreGone("the server closed the admin connection")
        return json.loads(line)

    def close(self):
        try:
            self.w.close()
        except Exception:
            pass


def wrap(text, width):
    out = []
    for para in text.split("\n"):
        out += textwrap.wrap(para, width) or [""]
    return out


# ---- the widgets ------------------------------------------------------------------

class UI:
    """dialog-style widgets on an 80x24 cell screen; only changes are sent."""

    def __init__(self, io, mode):
        self.io, self.mode = io, mode
        self.g = glyph_set(mode)
        self.painter = Painter()
        self.backtitle = "ParlorNet administration"

    # ---- drawing ------------------------------------------------------------------
    def base(self, hint=""):
        cv = Canvas()
        for y in range(ROWS):
            cv.put(0, y, " " * COLS, S_SCREEN)
        cv.put(1, 0, self.backtitle[:COLS - 2], S_BACK)
        cv.put(1, 1, self.g["h"] * (COLS - 2), S_HINT)
        cv.put(1, ROWS - 1, hint[:COLS - 3], S_HINT)
        return cv

    def frame(self, cv, title, w, h):
        """A raised gray box with a shadow, centered between the title rows and the hint row."""
        g = self.g
        x, y = (COLS - w) // 2, 2 + max(0, (20 - h) // 2)
        for r in range(y + 1, min(y + h + 1, ROWS - 1)):
            for c in (x + w, x + w + 1):
                if c < COLS:
                    cv.at[r][c] = SHADOW
        if y + h < ROWS - 1:
            for c in range(x + 2, min(x + w + 2, COLS)):
                cv.at[y + h][c] = SHADOW
        for r in range(y, y + h):
            cv.put(x, r, " " * w, D_BODY)
        cv.put(x, y, g["tl"] + g["h"] * (w - 2), D_LIGHT)
        cv.put(x + w - 1, y, g["tr"], D_DARK)
        for r in range(y + 1, y + h - 1):
            cv.put(x, r, g["v"], D_LIGHT)
            cv.put(x + w - 1, r, g["v"], D_DARK)
        cv.put(x, y + h - 1, g["bl"], D_LIGHT)
        cv.put(x + 1, y + h - 1, g["h"] * (w - 2) + g["br"], D_DARK)
        if title:
            t = " %s " % title
            cv.put(x + (w - len(t)) // 2, y, t, D_TITLE)
        return x, y

    def sunken(self, cv, x, y, w, h):
        g = self.g
        cv.put(x, y, g["tl"] + g["h"] * (w - 2), D_DARK)
        cv.put(x + w - 1, y, g["tr"], D_LIGHT)
        for r in range(y + 1, y + h - 1):
            cv.put(x, r, g["v"], D_DARK)
            cv.put(x + w - 1, r, g["v"], D_LIGHT)
        cv.put(x, y + h - 1, g["bl"], D_DARK)
        cv.put(x + 1, y + h - 1, g["h"] * (w - 2) + g["br"], D_LIGHT)

    def buttons(self, cv, x, y, w, h, labels, active):
        """The separator and the button row at the bottom of a box; returns
        where the cursor goes when a button has the focus."""
        g = self.g
        cv.put(x, y + h - 3, g["lj"] + g["h"] * (w - 2), D_LIGHT)
        cv.put(x + w - 1, y + h - 3, g["rj"], D_DARK)
        cells = [l.center(max(6, len(l))) for l in labels]
        gap = max(1, (w - 2 - sum(len(c) + 2 for c in cells)) // (len(cells) + 1))
        bx, by, pos = x + 1 + gap, y + h - 2, None
        for i, c in enumerate(cells):
            on = i == active
            cv.put(bx, by, "<" + c + ">", D_SEL if on else D_BODY)
            k = len(c) - len(c.lstrip())
            cv.put(bx + 1 + k, by, c.strip()[0], D_SELHOT if on else D_HOT)
            if on:
                pos = (bx + 1 + k, by)
            bx += len(c) + 2 + gap
        return pos

    async def paint(self, cv, cursor):
        data = self.painter.frame(cv, cursor or (COLS - 2, ROWS - 1))
        if data:
            self.io.write(data.replace(CLEAR, BLUE_CLEAR))
            await self.io.flush()

    async def key(self):
        while True:
            k = await self.io.readkey()
            if k != "REDRAW":
                return k
            self.painter.invalidate()
            return "REDRAW"

    @staticmethod
    def width_for(text, *extra):
        longest = max([len(p) for p in text.split("\n")] + [0])
        return min(COLS - 6, max([min(longest, 64) + 6, 40] + list(extra)))

    @staticmethod
    def button_focus(k, focus, nb):
        """Tab, Shift-Tab, Left and Right between the body (0) and the buttons (1..nb)."""
        if k == "TAB":
            return (focus + 1) % (nb + 1)
        if k == "BTAB":
            return (focus - 1) % (nb + 1)
        if k in ("LEFT", "RIGHT") and focus > 0:
            return 1 + (focus - 1 + (1 if k == "RIGHT" else -1)) % nb
        return None

    # ---- message boxes ------------------------------------------------------------
    async def msgbox(self, title, text, buttons=("OK",), default=0, lines=None):
        """Text (wrapped) or ready-made lines, scrolled when long. Returns the
        index of the button pressed, or None for Esc."""
        w = self.width_for(text if lines is None else "\n".join(lines),
                           len(title) + 8, sum(max(6, len(b)) + 4 for b in buttons) + 4)
        body = wrap(text, w - 4) if lines is None else [l[:w - 4] for l in lines]
        rows = min(len(body), 14)
        h = rows + 5
        top, focus = 0, default + 1
        hint = H_MSG + (", arrows scroll" if len(body) > rows else "")
        while True:
            cv = self.base(hint)
            x, y = self.frame(cv, title, w, h)
            for i, l in enumerate(body[top:top + rows]):
                cv.put(x + 2, y + 1 + i, l, D_BODY)
            if len(body) > rows:
                cv.put(x + w - 8, y + h - 3, " %3d%% " % (100 * (top + rows) // len(body)), D_NOTE)
            pos = self.buttons(cv, x, y, w, h, buttons, focus - 1)
            await self.paint(cv, pos)
            k = await self.key()
            if k == "ESC":
                return None
            if k == "ENTER" or k == " ":
                return focus - 1
            if k in ("UP", "DOWN", "PGUP", "PGDN", "HOME", "END"):
                step = {"UP": -1, "DOWN": 1, "PGUP": -rows, "PGDN": rows, "HOME": -len(body), "END": len(body)}[k]
                top = max(0, min(len(body) - rows, top + step))
                continue
            if k in ("TAB", "RIGHT"):
                focus = focus % len(buttons) + 1
            elif k in ("BTAB", "LEFT"):
                focus = (focus - 2) % len(buttons) + 1
            elif len(k) == 1:
                for i, b in enumerate(buttons):
                    if b[0].lower() == k.lower():
                        return i

    async def yesno(self, title, text, default_yes=False):
        return await self.msgbox(title, text, ("Yes", "No"), 0 if default_yes else 1) == 0

    async def infobox(self, title, text):
        w = self.width_for(text, len(title) + 8)
        body = wrap(text, w - 4)
        cv = self.base("")
        x, y = self.frame(cv, title, w, len(body) + 2)
        for i, l in enumerate(body):
            cv.put(x + 2, y + 1 + i, l, D_BODY)
        await self.paint(cv, None)

    # ---- one line of text -------------------------------------------------------------
    async def inputbox(self, title, text, init="", hidden=False, maxlen=64, buttons=("OK", "Cancel")):
        w = self.width_for(text, len(title) + 8, 50)
        body = wrap(text, w - 4)
        h = len(body) + 7
        buf, c, off, focus = list(init), len(init), 0, 0
        fw = w - 4
        while True:
            cv = self.base(H_INPUT)
            x, y = self.frame(cv, title, w, h)
            for i, l in enumerate(body):
                cv.put(x + 2, y + 1 + i, l, D_BODY)
            if c - off >= fw:
                off = c - fw + 1
            if c < off:
                off = c
            shown = ("*" * len(buf) if hidden else "".join(buf))[off:off + fw]
            fy = y + len(body) + 2
            cv.put(x + 2, fy, shown.ljust(fw), D_FIELD)
            pos = self.buttons(cv, x, y, w, h, buttons, focus - 1)
            await self.paint(cv, (x + 2 + c - off, fy) if focus == 0 else pos)
            k = await self.key()
            f = self.button_focus(k, focus, len(buttons))
            if f is not None:
                focus = f
            elif k == "ESC":
                return None
            elif k == "ENTER":
                return "".join(buf) if focus <= 1 else None
            elif focus == 0:
                if k == "BS" and c > 0:
                    del buf[c - 1]
                    c -= 1
                elif k == "DEL" and c < len(buf):
                    del buf[c]
                elif k == "LEFT":
                    c = max(0, c - 1)
                elif k == "RIGHT":
                    c = min(len(buf), c + 1)
                elif k == "HOME":
                    c = 0
                elif k == "END":
                    c = len(buf)
                elif len(k) == 1 and len(buf) < maxlen:
                    buf.insert(c, k)
                    c += 1
            elif len(k) == 1:
                for i, b in enumerate(buttons):
                    if b[0].lower() == k.lower():
                        return "".join(buf) if i == 0 else None

    # ---- lists: menu, checklist, radiolist ------------------------------------------
    async def listbox(self, title, text, items, sel=0, buttons=("Select", "Back"), kind="menu"):
        """items: [tag, desc] or [tag, desc, on]. menu and radio return the
        chosen tag, check the list of tags that are on; Esc or the last button
        gives None."""
        items = [[it[0], it[1], bool(it[2]) if len(it) > 2 else False] for it in items]
        n = len(items)
        mark = 0 if kind == "menu" else 4
        tagw = max([len(it[0]) for it in items] + [1])
        descw = max([len(it[1]) for it in items] + [0])
        w = self.width_for(text, len(title) + 8, mark + tagw + descw + 11,
                           sum(max(6, len(b)) + 4 for b in buttons) + 4)
        body = wrap(text, w - 4) if text else []
        rows = max(1, min(n, 14 - len(body)))
        h = len(body) + rows + 6
        sel, top, focus = max(0, min(sel, n - 1)), 0, 0
        hint = {"menu": H_MENU, "check": H_CHECK, "radio": H_RADIO}[kind]
        while True:
            top = min(max(top, sel - rows + 1), sel)
            cv = self.base(hint)
            x, y = self.frame(cv, title, w, h)
            for i, l in enumerate(body):
                cv.put(x + 2, y + 1 + i, l, D_BODY)
            ly = y + 1 + len(body)
            self.sunken(cv, x + 2, ly, w - 4, rows + 2)
            cur = None
            for i in range(rows):
                k = top + i
                if k >= n:
                    break
                tag, desc, on = items[k]
                r, act = ly + 1 + i, k == sel
                attr = D_SEL if act else D_BODY
                cv.put(x + 3, r, " " * (w - 6), attr)
                cx = x + 4
                if kind == "check":
                    cv.put(cx, r, "[%s]" % ("X" if on else " "), attr)
                elif kind == "radio":
                    cv.put(cx, r, "(%s)" % ("*" if on else " "), attr)
                cx += mark
                cv.put(cx, r, tag[:1], D_SELHOT if act else D_HOT)
                cv.put(cx + 1, r, tag[1:], D_SEL if act else D_TAG)
                room = x + w - 4 - (cx + tagw + 2)
                if room > 0:
                    cv.put(cx + tagw + 2, r, desc[:room], attr)
                if act:
                    cur = (cx - (3 if mark else 0), r)
            if top > 0:
                cv.put(x + w - 9, ly, "(-)", D_NOTE)
            if top + rows < n:
                cv.put(x + w - 9, ly + rows + 1, "(+)", D_NOTE)
            pos = self.buttons(cv, x, y, w, h, buttons, focus - 1)
            await self.paint(cv, cur if focus == 0 else pos)
            k = await self.key()
            f = self.button_focus(k, focus, len(buttons))
            if f is not None:
                focus = f
            elif k == "ESC":
                return None
            elif k in ("UP", "DOWN", "PGUP", "PGDN", "HOME", "END"):
                step = {"UP": -1, "DOWN": 1, "PGUP": -rows, "PGDN": rows, "HOME": -n, "END": n}[k]
                sel = max(0, min(n - 1, sel + step))
            elif k == " " and kind == "check":
                items[sel][2] = not items[sel][2]
            elif k == " " and kind == "radio":
                for it in items:
                    it[2] = it is items[sel]
            else:
                pressed = None
                if k == "ENTER":
                    pressed = max(0, focus - 1)
                elif len(k) == 1 and k.isalnum():
                    hits = [i for i in list(range(sel + 1, n)) + list(range(0, sel + 1))
                            if items[i][0][:1].lower() == k.lower()]
                    if hits:
                        sel, focus = hits[0], 0
                    elif focus:
                        pressed = next((i for i, b in enumerate(buttons) if b[0].lower() == k.lower()), None)
                if pressed is not None:
                    if pressed and pressed == len(buttons) - 1:
                        return None         # the last button cancels
                    return [it[0] for it in items if it[2]] if kind == "check" else items[sel][0]

    async def menu(self, title, text, items, sel=0, buttons=("Select", "Back")):
        return await self.listbox(title, text, items, sel, buttons, "menu")

    async def checklist(self, title, text, items):
        return await self.listbox(title, text, items, 0, ("OK", "Cancel"), "check")

    async def radiolist(self, title, text, items, current):
        items = [[t, d, t == current] for t, d in items]
        sel = next((i for i, it in enumerate(items) if it[2]), 0)
        return await self.listbox(title, text, items, sel, ("OK", "Cancel"), "radio")

    # ---- several lines of text ----------------------------------------------------------
    async def editbox(self, title, text, init, maxlines=20):
        w = COLS - 4
        body = wrap(text, w - 4)
        lw = w - 6
        lines = []
        for para in init.split("\n"):
            lines += textwrap.wrap(para, lw) or [""]
        lines = lines[:maxlines] or [""]
        rows = min(maxlines, 14 - len(body))
        h = len(body) + rows + 6
        r = c = top = focus = 0
        buttons = ("OK", "Cancel")
        while True:
            r = max(0, min(r, len(lines) - 1))
            c = max(0, min(c, len(lines[r])))
            top = min(max(top, r - rows + 1), r)
            cv = self.base(H_EDIT)
            x, y = self.frame(cv, title, w, h)
            for i, l in enumerate(body):
                cv.put(x + 2, y + 1 + i, l, D_BODY)
            ly = y + 1 + len(body)
            self.sunken(cv, x + 2, ly, w - 4, rows + 2)
            for i in range(rows):
                text_ = lines[top + i] if top + i < len(lines) else ""
                cv.put(x + 3, ly + 1 + i, text_.ljust(lw), D_FIELD)
            cv.put(x + w - 16, ly + rows + 1, " line %d/%d " % (r + 1, len(lines)), D_NOTE)
            pos = self.buttons(cv, x, y, w, h, buttons, focus - 1)
            await self.paint(cv, (x + 3 + c, ly + 1 + r - top) if focus == 0 else pos)
            k = await self.key()
            f = self.button_focus(k, focus, 2)
            if f is not None:
                focus = f
                continue
            if k == "ESC":
                return None
            if focus:
                pressed = focus if k == "ENTER" else {"o": 1, "c": 2}.get(k.lower()) if len(k) == 1 else None
                if pressed == 1:
                    return "\n".join(l.rstrip() for l in lines).strip("\n")
                if pressed == 2:
                    return None
                continue
            line = lines[r]
            if k == "ENTER":
                if len(lines) < maxlines:
                    lines[r:r + 1] = [line[:c], line[c:]]
                    r, c = r + 1, 0
            elif k == "BS":
                if c > 0:
                    lines[r] = line[:c - 1] + line[c:]
                    c -= 1
                elif r > 0 and len(lines[r - 1]) + len(line) <= lw:
                    c = len(lines[r - 1])
                    lines[r - 1:r + 1] = [lines[r - 1] + line]
                    r -= 1
            elif k == "DEL":
                if c < len(line):
                    lines[r] = line[:c] + line[c + 1:]
                elif r + 1 < len(lines) and len(line) + len(lines[r + 1]) <= lw:
                    lines[r:r + 2] = [line + lines[r + 1]]
            elif k == "UP":
                r -= 1
            elif k == "DOWN":
                r += 1
            elif k == "LEFT":
                if c == 0 and r > 0:
                    r, c = r - 1, len(lines[r - 1])
                else:
                    c -= 1
            elif k == "RIGHT":
                if c == len(line) and r + 1 < len(lines):
                    r, c = r + 1, 0
                else:
                    c += 1
            elif k == "HOME":
                c = 0
            elif k == "END":
                c = len(line)
            elif k == "PGUP":
                r -= rows
            elif k == "PGDN":
                r += rows
            elif len(k) == 1:
                if len(line) < lw:
                    lines[r] = line[:c] + k + line[c:]
                    c += 1
                elif c == len(line) and len(lines) < maxlines:
                    # a full line wraps at its last space, as typing runs on
                    cut = line.rfind(" ") + 1 or len(line)
                    lines[r:r + 1] = [line[:cut].rstrip(), line[cut:] + k]
                    r, c = r + 1, len(line) - cut + 1


# ---- the admin screens ------------------------------------------------------------------

def ago(ts):
    if not ts:
        return "never"
    return fmt_secs(time.time() - ts) + " ago"


def period(secs):
    """A setting's length: '7 days', '1 hour', '90 minutes'."""
    for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= n and secs % n == 0:
            return "%d %s%s" % (secs // n, unit, "" if secs == n else "s")
    return "%d seconds" % secs


def compact(secs):
    """A length as typed: 7d, 1h, 30m, or seconds."""
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n and secs % n == 0:
            return "%d%s" % (secs // n, unit)
    return str(secs)


class Admin:
    def __init__(self, ui, core):
        self.ui, self.core = ui, core
        self.name = ""

    async def call(self, op, **kw):
        """A request to the core; a refusal is shown and gives None."""
        r = await self.core.req(op, **kw)
        if not r.get("ok"):
            await self.ui.msgbox("Not done", r.get("error", "refused"))
            return None
        return r

    async def get(self):
        r = await self.call("get")
        if r:
            self.name = r["effective"]["server.name"]
            self.ui.backtitle = "ParlorNet administration: %s" % self.name
        return r

    async def set(self, key, value):
        return await self.call("set", key=key, value=value) is not None

    # ---- sign-in ------------------------------------------------------------------------
    async def sign_in(self, configured):
        ui = self.ui
        if not configured:
            text = ("ParlorNet is not set up yet. Type the one-time setup password from the container's "
                    "log (docker logs <container>).")
            title = "First-time setup"
        else:
            text, title = "Admin password for %s:" % self.name, "Sign in"
        for _ in range(3):
            pw = await ui.inputbox(title, text, hidden=True, maxlen=128)
            if pw is None:
                return False
            r = await self.core.req("login", password=pw, source=getattr(self.core, "source", ""))
            if r.get("ok"):
                if r.get("setup"):
                    return await self.new_password(first=True)
                return True
            await ui.msgbox("Not signed in", r.get("error", "refused"))
        return False

    async def new_password(self, first=False):
        ui = self.ui
        text = ("Choose the permanent admin password: at least 8 characters. " if first else
                "The new admin password: at least 8 characters. ")
        text += "Keep it somewhere safe."
        while True:
            pw = await ui.inputbox("Admin password", text, hidden=True, maxlen=128)
            if pw is None:
                return False
            again = await ui.inputbox("Admin password", "Type it again:", hidden=True, maxlen=128)
            if again is None:
                return False
            if again != pw:
                await ui.msgbox("Admin password", "The two did not match. Try again.")
                continue
            if await self.call("set_password", password=pw):
                if first:
                    await ui.msgbox("Welcome", "The admin password is set. Next, look through Server and "
                                    "Sign-in. This screen is at the same port whenever you need it.")
                else:
                    await ui.msgbox("Admin password", "The admin password is changed.")
                return True

    # ---- main menu ------------------------------------------------------------------------
    MAIN = [("Server", "Name, message of the day, time per turn, players"),
            ("Sign-in", "How players sign in; email relay and limits"),
            ("Games", "Games offered; limits on tables and games"),
            ("Adapters", "Which frontends run, on which ports"),
            ("Accounts", "Find, suspend, reset or delete players"),
            ("Tables", "List the tables, end one"),
            ("Scores", "Leaderboard, points per result, reset"),
            ("Status", "Uptime, connections, players, email"),
            ("Password", "Change the admin password"),
            ("Exit", "Sign out")]

    async def run(self):
        sel = 0
        while True:
            await self.get()
            tag = await self.ui.menu("Main menu", "Choose with the arrow keys and Enter.", self.MAIN, sel,
                                     ("Select", "Exit"))
            if tag is None or tag == "Exit":
                return
            sel = [t for t, _ in self.MAIN].index(tag)
            await {"Server": self.server, "Sign-in": self.signin, "Games": self.games,
                   "Adapters": self.adapters, "Accounts": self.accounts, "Tables": self.tables,
                   "Scores": self.scores, "Status": self.status, "Password": self.new_password}[tag]()

    # ---- server -----------------------------------------------------------------------------
    async def server(self):
        ui, sel = self.ui, 0
        while True:
            g = await self.get()
            if not g:
                return
            e = g["effective"]
            motd = e["server.motd"].strip().split("\n")[0] if e["server.motd"].strip() else "(none)"
            turn = int(e["server.turn_default"])
            url = e.get("site.contribute_url") or "(cleared: its line is left out)"
            docs = e.get("site.docs_url", "(not known)") or "(cleared: no link)"
            items = [("Name", e["server.name"]), ("Message", motd[:40] + ("..." if len(motd) > 40 else "")),
                     ("Contribute", url[:40] + ("..." if len(url) > 40 else "")),
                     ("Docs", docs[:40] + ("..." if len(docs) > 40 else "")),
                     ("Turn time", next((d for _, s, d in TURNS if s == turn), fmt_secs(turn)) + " per turn"),
                     ("Web names", g["settings"].get("web.hosts") or "(any)"),
                     ("Idle time", length_text(int(e.get("presence.idle") or 1800)) + " until a player is idle")]
            items += self.count_items(g, ["players.online_max"])
            tag = await ui.menu("Server", "", items, sel)
            if tag is None:
                return
            sel = [t for t, _ in items].index(tag)
            if tag == "Name":
                v = await ui.inputbox("Server name", "The short name players' programs see in the greeting, "
                                      "and the server named in the mail it sends. Letters, digits, '.', '-' and '_'.",
                                      e["server.name"], maxlen=32)
                if v is not None:
                    await self.set("server.name", v)
            elif tag == "Message":
                v = await ui.editbox("Message of the day", "Shown to players before they sign in (telnet, the "
                                     "web, IOS clients). Plain text; a blank line between paragraphs.",
                                     e["server.motd"])
                if v is not None:
                    await self.set("server.motd", v)
            elif tag == "Contribute":
                v = await ui.inputbox("Contribute address", "Put in the message of the day where it says "
                                      "{contribute}: where to run your own server or contribute (by default "
                                      "the project's home). Empty: the line holding {contribute} is left out. "
                                      "Up to 73 characters.", e.get("site.contribute_url", ""), maxlen=73)
                if v is not None:
                    await self.set("site.contribute_url", v)
            elif tag == "Docs":
                v = await ui.inputbox("Docs address", "The documentation site the web pages link to (Docs, and "
                                      "the client pages; by default http://docs.parlornet.org/). "
                                      "Empty: no link to documentation. Up to 73 characters.",
                                      g["settings"].get("site.docs_url", e.get("site.docs_url", "")), maxlen=73)
                if v is not None:
                    await self.set("site.docs_url", v)
            elif tag == "Turn time":
                cur = next((t for t, s, _ in TURNS if s == turn), None)
                v = await ui.radiolist("Time per turn", "The time per turn of a new table when its creator does "
                                       "not choose one. Running tables keep their own.",
                                       [(t, d) for t, _, d in TURNS], cur)
                if v is not None:
                    await self.set("server.turn_default", v)
            elif tag == "Idle time":
                v = await ui.inputbox("Idle time", "A player who does nothing for this long drops off every list "
                                      "of who is online. They come back as soon as they do something. Type a "
                                      "length such as 30m or 2h, from 1 minute to 7 days.",
                                      length_text(int(e.get("presence.idle") or 1800)), maxlen=12)
                if v is not None:
                    await self.set("presence.idle", v)
            elif tag == "Players online":
                await self.count(g, tag)
            elif tag == "Web names":
                v = await ui.inputbox("Web names", "The names the web pages answer to, comma-separated, like "
                                      "parlornet.org:6500,www.parlornet.org:6500. A redirect for any other name "
                                      "goes to the first. Empty: any. Takes effect when the container restarts.",
                                      g["settings"].get("web.hosts", ""), maxlen=200)
                if v is not None:
                    await self.set("web.hosts", v)

    # ---- sign-in -----------------------------------------------------------------------------
    async def set_auth(self, key, value):
        """Set the sign-in or sign-up method; say what else changed or why not."""
        r = await self.call("set", key=key, value=value)
        if not r:
            return
        if r.get("note"):
            await self.ui.msgbox("Sign-in", r["note"])
        g = await self.get()
        if g and g["effective"]["auth.signup"] == "email" and g["effective"]["email.transport"] == "log":
            await self.ui.msgbox("Email", "No SMTP relay is set yet: until one is, mails go nowhere and only "
                                 "their address and subject are logged. Set it up in this menu.")

    async def signin(self):
        ui, sel = self.ui, 0
        while True:
            g = await self.get()
            if not g:
                return
            s, e = g["settings"], g["effective"]
            method, signup = e["auth.signin"], e["auth.signup"]
            items = [("Sign-in", "%s: %s" % (method, dict(SIGNIN_METHODS).get(method, ""))),
                     ("Sign-up", "%s: %s" % (signup, dict(SIGNUP_METHODS).get(signup, ""))),
                     ("Label", s.get("auth.label") or "(the method's own: %s)" % (e["auth.label"] or "none"))]
            mail = signup == "email"
            if mail:
                st = (await self.core.req("email"))["email"]
                log = e["email.transport"] == "log"
                items += [("SMTP host", s.get("email.smtp_host") or "(none: mails go to the container log)"),
                          ("SMTP port", s.get("email.smtp_port") or "587"),
                          ("SMTP user", s.get("email.smtp_user") or "(none)"),
                          ("SMTP password", "(set)" if s.get("email.smtp_password") else "(not set)"),
                          ("From", s.get("email.from") or "(none)"),
                          ("Limits", "%d sent this hour, %d today (%d new)" % (st["hour"], st["day"], st["new_day"])),
                          ("Sign-up limits", "%d pending at most, each for 24 hours; one email per address in %s"
                           % tuple(f(g["signup_limits"][k][0]) for f, k in (
                               (int, "signup.max_pending"), (period, "signup.address_cooldown")))),
                          ("Sending", ("paused" if st["paused"] else "on") +
                           ("; new addresses held after bounces" if st["new_held"] else "") +
                           ("; to the log" if log else "")),
                          ("Test email", "send a test message")]
            tag = await ui.menu("Sign-in", "How players prove who they are. Changes apply to the next sign-in.",
                                items, sel)
            if tag is None:
                return
            sel = [t for t, _ in items].index(tag)
            if tag == "Sign-in":
                v = await ui.radiolist("Sign-in method", "Checked at every sign-in. none suits a LAN or a "
                                       "festival; password anything else, with sign-up email on the open "
                                       "internet.", SIGNIN_METHODS, method)
                if v is not None:
                    await self.set_auth("auth.signin", v)
            elif tag == "Sign-up":
                v = await ui.radiolist("Sign-up method", "How a new player makes an account. It must give the "
                                       "sign-in method what it needs: none with none; password with password; "
                                       "email with password.", SIGNUP_METHODS, signup)
                if v is not None:
                    await self.set_auth("auth.signup", v)
            elif tag == "Label":
                v = await ui.inputbox("Label", "What players are asked for, as their programs show it. Empty "
                                      "for the method's own label.", s.get("auth.label", ""), maxlen=40)
                if v is not None:
                    await self.set("auth.label", v)
            elif tag in ("SMTP host", "SMTP port", "SMTP user", "From"):
                key, text = {"SMTP host": ("email.smtp_host", "The mail relay (STARTTLS), for example "
                                           "smtp.example.org. Empty: mails go to the container log."),
                             "SMTP port": ("email.smtp_port", "Usually 587."),
                             "SMTP user": ("email.smtp_user", "The user name for the relay."),
                             "From": ("email.from", "The sender, as the relay allows it: noreply@example.org "
                                      "or ParlorNet <noreply@example.org>.")}[tag]
                v = await ui.inputbox(tag, text, s.get(key, ""), maxlen=120)
                if v is not None:
                    await self.set(key, v)
            elif tag == "SMTP password":
                v = await ui.inputbox("SMTP password", "Type the relay's password. It is stored in the "
                                      "server's database and never shown. Empty leaves it as it is.",
                                      hidden=True, maxlen=200)
                if v:
                    if await self.set("email.smtp_password", v):
                        await ui.msgbox("SMTP password", "Saved.")
            elif tag == "Limits":
                await self.limits()
            elif tag == "Sign-up limits":
                await self.signup_limits()
            elif tag == "Sending":
                st = (await self.core.req("email"))["email"]
                if st["paused"] or st["new_held"]:
                    if await ui.yesno("Sending", "Resume sending email?" + (
                            " New addresses were held because more than 3 addresses bounced in a day."
                            if st["new_held"] else ""), True):
                        await self.call("resume_email")
                elif await ui.yesno("Sending", "Pause all email? Sign-ups and resets by email are refused "
                                    "until you resume."):
                    await self.call("pause_email")
            elif tag == "Test email":
                to = await ui.inputbox("Test email", "Send a test message to:", maxlen=120)
                if to:
                    await ui.infobox("Test email", "Sending to %s ..." % to)
                    r = await self.core.req("test_email", to=to)
                    if r.get("ok"):
                        await ui.msgbox("Test email", "Sent to %s%s." % (
                            to, "" if r.get("transport") == "smtp" else " (to the container log: no SMTP host)"))
                    else:
                        await ui.msgbox("Test email", r.get("error", "not sent"))

    async def limits(self):
        ui, sel = self.ui, 0
        while True:
            g = await self.get()
            if not g:
                return
            st = (await self.core.req("email"))["email"]
            lim = g["limits"]
            keys = list(LIMIT_TAGS)
            items = [(LIMIT_TAGS[k], "%-3d %s (default %d)" % (lim[k][0], lim[k][3], lim[k][1])) for k in keys]
            text = ("Sent: %d in the last minute, %d this hour, %d today (%d to new addresses). Addresses "
                    "blocked today: %d." % (st["minute"], st["hour"], st["day"], st["new_day"], st["blocked_day"]))
            tag = await ui.menu("Email limits", text, items, sel)
            if tag is None:
                return
            sel = [t for t, _ in items].index(tag)
            k = keys[sel]
            v = await ui.inputbox(tag, "Emails %s, at most (0 to %d):" % (lim[k][3], lim[k][2]), str(lim[k][0]),
                                  maxlen=4)
            if v is not None:
                await self.set("email.limit." + k, v.strip())

    SIGNUP_TAGS = [("signup.max_pending", "Pending at most", "New accounts nobody has signed into yet, at "
                    "once (%d to %d). Each is deleted after 24 hours if nobody signs in. Over the limit, new "
                    "sign-ups are refused: too many sign-ups in progress."),
                   ("signup.address_cooldown", "Address cooldown", "After a sign-up email (a password or the "
                    "notice to an address in use), none to that address again for this long, like 7d "
                    "(%s to %s).")]

    async def signup_limits(self):
        ui, sel = self.ui, 0
        while True:
            g = await self.get()
            if not g:
                return
            lim = g["signup_limits"]
            items = []
            for key, tag, _ in self.SIGNUP_TAGS:
                cur, default = lim[key][0], lim[key][1]
                show = str if key == "signup.max_pending" else period
                items.append((tag, "%s (default %s)" % (show(cur), show(default))))
            tag = await ui.menu("Sign-up limits", "One sign-up email per address in the cooldown, and a cap on "
                                "new accounts nobody has signed into yet. Recovery mail has its own limits.", items, sel)
            if tag is None:
                return
            sel = [t for t, _ in items].index(tag)
            key, _, text = self.SIGNUP_TAGS[sel]
            least, most = lim[key][2], lim[key][3]
            if key == "signup.max_pending":
                text, init = text % (least, most), str(lim[key][0])
            else:
                text, init = text % (period(least), period(most)), compact(lim[key][0])
            v = await ui.inputbox(tag, text, init, maxlen=10)
            if v is not None:
                await self.set(key, v.strip())

    # ---- games and adapters ----------------------------------------------------------------
    # The settings that are a whole number (parlord/core.py, SETTING_LIMITS): menu item, setting, default, the
    # line the menu shows after the number, and what the input box says. The Games menu shows all but the
    # first, which is in the Server menu.
    COUNTS = [
        ("Players online", "players.online_max", 200, "players signed in at once, at most",
         "The most players signed in at once, on every frontend together (a web session counts until it ends). "
         "Past it, a sign-in is refused with a note that the server is full; players already signed in can "
         "sign in again elsewhere. Bots are exempt. A whole number from 1 to 5000; 0 or empty for the "
         "default, 200."),
        ("Active games", "games.active_max", 20, "games a player plays at once",
         "The most games one player may be playing at once. At that many, they cannot sit down at another table "
         "until a game ends. Games already waiting can still start, so a player can go over this by the tables "
         "they were waiting at. Bots are exempt. A whole number from 1 to 999; 0 or empty for the default, 20."),
        ("Waiting tables", "games.waiting_per_type", 2, "open tables a player waits at, per game",
         "The most open tables of one game, such as Reversi, that one player may sit at waiting for opponents. "
         "Other games are counted on their own. Bots are exempt. A whole number from 1 to 99; 0 or empty for "
         "the default, 2."),
        ("Tables", "games.tables_max", 400, "tables on the server, finished included",
         "The most tables on the server, open, playing and finished (kept a day) together. Past it, new tables "
         "are refused until some go. Each takes up to about 100 KB of memory. A whole number from 1 to 5000; 0 "
         "or empty for the default, 400."),
        ("Open tables kept", "games.open_hours", 24, "hours an unjoined open table stays",
         "How long an open table stays listed while nobody joins it and nothing happens there, in hours. Then "
         "it goes. A whole number from 1 to 720; 0 or empty for the default, 24."),
        ("Longest turn", "games.turn_max_days", 5, "days per turn at most",
         "The longest time per turn a new table may set, in days. Running tables keep their own. A whole "
         "number from 1 to 30; 0 or empty for the default, 5."),
        ("List length", "lists.max", 20, "other players' tables in a table list",
         "How many tables a table list shows besides the player's own tables and invitations, which are "
         "always all listed: open tables newest first, then games being played, and a note of how many more "
         "there are. A whole number from 1 to 200; 0 or empty for the default, 20.")]

    async def count(self, g, tag):
        """Ask for one of COUNTS and set it."""
        _, key, default, _, text = next(x for x in self.COUNTS if x[0] == tag)
        v = await self.ui.inputbox(tag, text, g["effective"].get(key) or str(default), maxlen=4)
        if v is not None:
            await self.set(key, v)

    def count_items(self, g, keys):
        e = g["effective"]
        return [(tag, "%s %s" % (e.get(key) or default, what)) for tag, key, default, what, _ in self.COUNTS
                if key in keys]

    async def games(self):
        ui, sel = self.ui, 0
        while True:
            g = await self.get()
            if not g:
                return
            off = set(x for x in g["settings"].get("games.off", "").split(",") if x)
            rulesets = ["%s:%s" % (gm["key"], r) for gm in g["games"] for r in gm["rules"]]
            on = [k for k in rulesets if k not in off and k.split(":")[0] not in off]
            items = [("Offered", "%d of %d games and rulesets" % (len(on), len(rulesets)))]
            items += self.count_items(g, [k for _, k, _, _, _ in self.COUNTS[1:]])
            tag = await ui.menu("Games", "The games offered, and how many tables and games a player may have.",
                                items, sel)
            if tag is None:
                return
            sel = [t for t, _ in items].index(tag)
            if tag == "Offered":
                await self.offered(g)
                continue
            await self.count(g, tag)

    async def offered(self, g):
        off = set(x for x in g["settings"].get("games.off", "").split(",") if x)
        items = []
        for gm in g["games"]:
            for rules in gm["rules"]:
                key = "%s:%s" % (gm["key"], rules)
                items.append((key, "%s, %s rules" % (gm["name"], rules), key not in off and gm["key"] not in off))
        if not items:
            await self.ui.msgbox("Games", "No game plugins are installed.")
            return
        on = await self.ui.checklist("Games", "The games and rulesets offered for new tables. Tables already "
                                     "running play on.", items)
        if on is None:
            return
        if not on and not await self.ui.yesno("Games", "No game would be offered: nobody could start a table. "
                                              "Save anyway?"):
            return
        await self.set("games.off", ",".join(k for k, _, _ in items if k not in on))

    async def adapters(self):
        g = await self.get()
        if not g:
            return
        stored = g["settings"].get("adapters.enabled")
        enabled = catalog.parse(stored) if stored is not None else catalog.default_enabled()
        items = [(name, "%s  %s%s" % (port or "-", what, "" if catalog.installed(mod) else " (not installed)"),
                  name in enabled) for name, mod, port, what in catalog.ADAPTERS]
        on = await self.ui.checklist("Adapters", "Which frontends the container starts. A change takes effect "
                                     "when the container restarts (docker restart). The native protocol "
                                     "(6501) always runs.", items)
        if on is None:
            return
        if "admin" not in on and not await self.ui.yesno(
                "Adapters", "Without the admin adapter this screen is gone after the restart. To bring it "
                "back, start the container with PARLOR_ADMIN=1. Turn it off?"):
            return
        if await self.set("adapters.enabled", ",".join(on)):
            await self.ui.msgbox("Adapters", "Saved. It takes effect when the container restarts.")

    # ---- accounts ------------------------------------------------------------------------------
    async def accounts(self):
        ui = self.ui
        q = await ui.inputbox("Accounts", "Find accounts by part of a name or an address. Enter alone lists "
                              "them all.", maxlen=60)
        if q is None:
            return
        sel = 0
        while True:
            r = await self.call("accounts", q=q)
            if not r:
                return
            if not r["accounts"]:
                await ui.msgbox("Accounts", "No account matches." if q else "There are no accounts yet.")
                return
            items = []
            for a in r["accounts"]:
                flags = [f for f, on in (("online", a["online"]), ("suspended", a["suspended"]),
                                         ("locked", a["locked"])) if on]
                if a.get("pending"):
                    flags.append("pending, deleted in %s" % fmt_secs(a["pending"]))
                items.append((a["name"], ("%s %s" % (a["email"] or "-", " ".join(flags))).strip()))
            more = " Showing the first %d." % len(items) if r["total"] > len(items) else ""
            tag = await ui.menu("Accounts", "%d found.%s" % (r["total"], more), items, sel)
            if tag is None:
                return
            sel = [t for t, _ in items].index(tag)
            await self.account(next(a for a in r["accounts"] if a["name"] == tag))

    async def account(self, a):
        ui = self.ui
        g = await self.get()
        method = g["effective"]["auth.signin"] if g else ""
        reset = {"password": "a temporary password to give the player (the old one stops working)"}.get(
            method, "clear a lockout")
        items = [("Unsuspend", "let the player sign in again") if a["suspended"] else
                 ("Suspend", "refuse sign-in; signs the player out now"),
                 ("Reset", reset), ("Delete", "remove the account (tables keep the name)")]
        text = "Address: %s%s. Created %s; last seen %s.%s" % (
            a["email"] or "none", "" if a["verified"] or not a["email"] else " (unverified)", ago(a["created"]),
            ago(a["last_seen"]), " Locked for %s after wrong answers." % fmt_secs(a["locked"]) if a["locked"] else "")
        if a.get("pending"):
            text += (" Nobody has signed in with the password mailed at sign-up yet; the account is deleted in %s"
                     " unless someone does." % fmt_secs(a["pending"]))
        kept = a.get("prefs") or {}         # what server extensions keep for the player (PRF): counts only
        if kept:
            text += " Kept by extensions: %s." % ", ".join("%s %d key%s, %d bytes" % (n, k, "" if k == 1 else "s", b)
                                                           for n, (k, b) in sorted(kept.items()))
        tag = await ui.menu(a["name"], text, items)
        if tag in ("Suspend", "Unsuspend"):
            if await self.call(tag.lower(), name=a["name"]):
                await ui.msgbox(a["name"], "%s is %s." % (a["name"], "suspended" if tag == "Suspend" else
                                                          "no longer suspended"))
        elif tag == "Reset":
            if await ui.yesno("Reset", "Reset %s: %s?" % (a["name"], reset)):
                r = await self.call("reset", name=a["name"])
                if r:
                    await ui.msgbox("Reset", r.get("text", "Done."))
        elif tag == "Delete":
            if await ui.yesno("Delete", "Delete the account %s? This cannot be undone; the name becomes free."
                              % a["name"]):
                if await self.call("delete", name=a["name"]):
                    await ui.msgbox("Delete", "%s is deleted." % a["name"])

    # ---- tables ------------------------------------------------------------------------------------
    async def tables(self):
        ui, sel = self.ui, 0
        while True:
            r = await self.call("tables")
            if not r:
                return
            if not r["tables"]:
                await ui.msgbox("Tables", "There are no tables.")
                return
            items = []
            for t in r["tables"]:
                who = " v ".join(s or "-" for s in t["seats"])
                state = {"open": "open", "done": "finished"}.get(t["status"], "playing")
                vis = "private" if t.get("vis") == "private" else ""
                items.append((t["id"], "%-10s %-20s %-8s %-7s %s/turn" % (t["name"][:10], who[:20], state, vis,
                                                                         fmt_secs(t["turn_secs"]))))
            tag = await ui.menu("Tables", "Choose a table to end it.", items, sel)
            if tag is None:
                return
            sel = [t for t, _ in items].index(tag)
            t = next(t for t in r["tables"] if t["id"] == tag)
            what = ("The game ends as abandoned and stays listed as finished for a day."
                    if t["status"] == "playing" else "The table is removed.")
            if await ui.yesno("End table %s" % tag, "End table %s (%s)? %s" % (tag, t["name"], what)):
                if await self.call("end_table", id=tag):
                    await ui.msgbox("Tables", "Table %s is ended." % tag)

    # ---- scores (parlord/core.py, SCORE_SETTINGS; docs/accounts.md) --------------------------------------------
    # Menu item, setting, default, what a result is.
    POINTS = [("Win", "scores.win", 4, "a win"), ("Draw", "scores.draw", 2, "a draw, for each player sharing it"),
              ("Loss", "scores.loss", 1, "a loss (a game played to its end)"),
              ("Resign", "scores.resign", 0, "resigning, running out of time or leaving a game")]

    async def scores(self):
        ui, sel = self.ui, 0
        while True:
            g = await self.get()
            if not g:
                return
            e = g["effective"]
            shown = e.get("scores.show", "on") == "on"
            items = [("Leaderboard", "shown" if shown else "not shown, and no scores on profiles")]
            items += [(tag, "%s points for %s" % (e.get(key, default), what))
                      for tag, key, default, what in self.POINTS]
            items.append(("Reset scores", "every player's score back to 0"))
            tag = await ui.menu("Scores", "Every finished game scores points, against the bot too. On a public "
                                "server any score is easy to get: the leaderboard is meant for conferences, "
                                "festivals and LAN parties.", items, sel)
            if tag is None:
                return
            sel = [t for t, _ in items].index(tag)
            if tag == "Leaderboard":
                v = await ui.radiolist("Leaderboard", "Show the leaderboard (web, telnet, ParlorNet Play, chat "
                                       "commands) and each player's score on their profile? Games are scored "
                                       "either way.", [("on", "Shown"), ("off", "Not shown")],
                                       "on" if shown else "off")
                if v is not None:
                    await self.set("scores.show", v)
            elif tag == "Reset scores":
                if await ui.yesno("Reset scores", "Set every player's score back to 0, for a festival starting, "
                                  "say? The games stay recorded: tables, turns and the points each game gave. "
                                  "This cannot be undone."):
                    r = await self.call("reset_scores")
                    if r:
                        await ui.msgbox("Scores", "The scores are reset (%d players had one)." % r["players"])
            else:
                _, key, default, what = next(x for x in self.POINTS if x[0] == tag)
                v = await ui.inputbox(tag, "The points for %s. A whole number from 0 to 99; empty for the "
                                      "default, %d. Games already scored keep their points." % (what, default),
                                      e.get(key) or str(default), maxlen=2)
                if v is not None:
                    await self.set(key, v)

    # ---- status ---------------------------------------------------------------------------------------
    async def status(self):
        while True:
            r = await self.call("status")
            if not r:
                return
            m, t = r["email"], r["tables"]
            signin, _, signup = r["auth"].partition("+")
            lines = ["Server        %s (sign-in: %s, sign-up: %s)" % (r["name"], signin, signup),
                     "Up for        %s" % fmt_secs(r["uptime"]),
                     "Connections   %d to the core, %d admin" % (r["connections"], r["admin_connections"]),
                     "Players       %d accounts, %d online" % (r["players"], r["online"]),
                     "Tables        %d open, %d playing, %d finished" % (t["open"], t["playing"], t["done"]),
                     "Games         %s" % (", ".join(r["games"]) or "none offered"),
                     "Email sent    %d this minute, %d this hour, %d today (%d new)"
                     % (m["minute"], m["hour"], m["day"], m["new_day"]),
                     "Blocked       %d addresses today" % m["blocked_day"],
                     "Sending       %s%s" % ("paused" if m["paused"] else "on",
                                             ", new addresses held" if m["new_held"] else "")]
            b = r.get("budgets")
            if b:
                lines += ["Budgets       %s" % ("USED UP: players on new addresses or mail are refused" if b["alarm"]
                                                else "fine"),
                          "  wrong answers  %d of %d today (untrusted addresses), %d address%s at their %d"
                          % (b["wrong_server"], b["wrong_server_limit"], b["addresses_spent"],
                             "" if b["addresses_spent"] == 1 else "es", b["wrong_per_address_limit"]),
                          "  mail today     sign-up %d/%d, strangers' recovery %d/%d, owners' %d/%d"
                          % tuple(b["signup_day"] + b["recovery_day"] + b["own_recovery_day"])]
            su = r.get("signups")
            if su:
                lines += ["Sign-ups      %d of %d pending (each deleted after %d hours)"
                          % (su["pending"], su["max_pending"], su["pending_ttl"] // 3600),
                          "              %d address%s in cooldown (one email in %s)"
                          % (su["cooldowns"], "" if su["cooldowns"] == 1 else "es", period(su["address_cooldown"]))]
            pressed = await self.ui.msgbox("Status", "", ("OK", "Refresh", "Clear budgets", "Sign-ups"), lines=lines)
            if pressed == 2:
                if await self.ui.yesno("Clear budgets", "Free the sign-in and mail budgets now? Wrong answers "
                                       "and mail counted so far stop counting against the server and client "
                                       "addresses (each name's own lockout and caps stay). Do this when an "
                                       "attack is over."):
                    await self.call("clear_budgets")
                continue
            if pressed == 3:
                await self.cooldowns(su)
                continue
            if pressed != 1:
                return

    async def cooldowns(self, su):
        """Clear the sign-up cooldown of one address, or of every address."""
        ui = self.ui
        wait = period(su["address_cooldown"]) if su else "the cooldown"
        tag = await ui.menu("Sign-ups", "After a sign-up email, an address gets no other for %s. Clearing lets "
                            "it have one again now. Pending sign-ups go on as they are." % wait,
                            [("One address", "type the address to clear"),
                             ("All addresses", "clear every address's cooldown")])
        if tag == "One address":
            addr = await ui.inputbox("One address", "The address to clear. It is only compared with what the "
                                     "server keeps (a keyed hash of each address) and is not stored.", maxlen=120)
            if addr:
                r = await self.call("clear_cooldown", address=addr)
                if r:
                    await ui.msgbox("Sign-ups", "Cleared: that address may have a sign-up email again."
                                    if r["cleared"] else "That address was not in its cooldown.")
        elif tag == "All addresses":
            if await ui.yesno("Clear address cooldowns", "Clear the sign-up cooldown of every address (%d now)? "
                              "Each may then have one sign-up email again." % (su["cooldowns"] if su else 0)):
                r = await self.call("clear_cooldowns")
                if r:
                    await ui.msgbox("Sign-ups", "Cleared %d address%s." % (r["cleared"],
                                                                       "" if r["cleared"] == 1 else "es"))


# ---- sessions ------------------------------------------------------------------------------------

async def main_session(io, args):
    ttype = await io.negotiate()
    guess = mode_for_ttype(ttype)
    default = guess if guess in ("pc", "utf8", "ansi") else ("pc" if guess is None else "ansi")
    io.write("\nParlorNet administration.\n\n")
    try:
        core = await AdminCore.connect(args.core_host, args.core_port)
        peer = io.w.get_extra_info("peername")
        core.source = peer[0] if peer else ""       # wrong admin passwords lock this address only
        hello = await core.req("hello")
    except (OSError, asyncio.TimeoutError, CoreGone, ValueError):
        io.write("The ParlorNet server is not answering yet. Try again in a moment.\n")
        await io.flush()
        return
    try:
        io.write("These screens need an ANSI terminal of 80x24. Display (pick the first line whose sample "
                 "looks right):\n")
        for i, (m, what) in enumerate(MODES, 1):
            io.write("  %d) %-34s" % (i, what))
            g = glyph_set(m)
            sample = "\x1b[0;37;44m %s%s%s \x1b[0;30;47m < OK > \x1b[0m" % (g["tl"], g["h"] * 3, g["tr"])
            io.raw(sample.encode(CHARSET[m]))
            io.write("%s\n" % ("   <- default" if m == default else ""))
        order = [m for m, _ in MODES]
        io.write("Choice [%d]: " % (order.index(default) + 1))
        await io.flush()
        choice = (await io.readline()).strip()
        mode = order[int(choice) - 1] if choice in ("1", "2", "3") else default
        io.charset = CHARSET[mode]
        io.char_mode()
        ui = UI(io, mode)
        admin = Admin(ui, core)
        admin.name = hello.get("name", "")
        ui.backtitle = "ParlorNet administration: %s" % admin.name
        if await admin.sign_in(hello.get("configured", True)):
            await admin.run()
        io.write("\x1b[0m\x1b[2J\x1b[HSigned out.\n")
        await io.flush()
    finally:
        core.close()


async def serve(args):
    sessions = 0

    async def handle(reader, writer):
        nonlocal sessions
        if sessions >= MAX_SESSIONS:
            writer.write(b"The admin screens are busy; try again soon.\r\n")
            writer.close()
            return
        sessions += 1
        io = AdminIO(reader, writer)
        try:
            await main_session(io, args)
        except (EOFError, ConnectionError, asyncio.TimeoutError, CoreGone, OSError):
            pass
        except Exception as e:
            print("admin session error: %r" % e, file=sys.stderr, flush=True)
        finally:
            sessions -= 1
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    print("admin adapter on %s:%d -> core admin %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.admin")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6510)
    ap.add_argument("--core-admin", default="127.0.0.1:6599", help="the core's admin socket")
    ap.add_argument("--key", default=None, help="this adapter's admin-role key file (python -m parlord.frontends)")
    a = ap.parse_args(argv)
    if a.key:
        AdminCore.key_file = a.key
    a.core_host, _, p = a.core_admin.rpartition(":")
    a.core_port = int(p)
    return a


if __name__ == "__main__":
    try:
        asyncio.run(serve(parse_args()))
    except KeyboardInterrupt:
        pass
