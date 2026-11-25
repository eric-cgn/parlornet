"""The Eclipse 68k Fix page of the docs site (eclipse-68k-fix.html) and its downloads: ParlorNet's own patcher
for Reversi The Eclipse 1.2 on 68k Macs. Its source, its build and its releases are in its own repository
(eric-cgn/parlornet-eclipse-fix); the downloads and the README come from its release pinned in
docsite/downloads.json (docsite/downloads.py). The screenshot is static/img/eclipse-68k-fix.gif."""
import os

from adapters import clients as C
from adapters.webstyle import a, callout, esc, font, h1, h2, p
from . import downloads

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(ROOT, "static")

ECLIPSE_FIX = C.ECLIPSE_FIX
SLUG = "eclipse-68k-fix"
README = "eclipse-68k-fix-readme.txt"
ECLIPSE_FIX_GIF = "/img/eclipse-68k-fix.gif"
STATIC = {ECLIPSE_FIX_GIF: ("img/eclipse-68k-fix.gif", "image/gif")}
# The game's Info-Mac file (its link is in adapters/clients.py) as checked on 2026-11-21: size and MD5 of the .hqx
# (its SHA-256 then was 7eadbc9330481e1314a04ae97ac04974768d27d5bc5d14011f4d63d5646be01e).
# It holds a StuffIt archive with Reversi The Eclipse 1.2, whose CODE 1 passes the patcher's check.
ECLIPSE_GAME_FACTS = (2609150, "cb02ff0e009ff39907e2f30bdf1cc1d5")


def static_file(path):
    with open(os.path.join(STATIC_DIR, STATIC[path][0]), "rb") as f:
        return f.read()


def files():
    """{site path: bytes} of the screenshot, and the README and the downloads when they were fetched."""
    out = {u.lstrip("/"): static_file(u) for u in STATIC}
    out.update(downloads.files(README))
    out.update(downloads.program_files(SLUG))
    return out


def eclipse_fix_body():
    """eclipse-68k-fix.html: what the 68k bug in Reversi The Eclipse 1.2 is, what the patcher changes,
    how to use it, and the downloads with sizes and checksums."""
    host = C.HOST
    game = next(url for _, _, url, _ in C.BY_ID["eclipse"].get if url and url.startswith(C.INFO_MAC))
    parts = [("bug", "The bug"), ("fix", "What the fix changes"), ("use", "Using it"), ("download", "Download"),
             ("game", "The game"), ("search", "Was it ever fixed?"), ("licence", "License")]
    steps = ["Duplicate Reversi The Eclipse 1.2 in the Finder and keep the original.",
             "Open Eclipse 68k Fix. Its window explains what it does.",
             "Click <b>Choose Eclipse...</b> and select the copy. The file dialog lists only the game&#39;s "
             "applications (creator <tt>LPRV</tt>).",
             "Confirm. The patcher writes the fix, reads it back from disk and tells you the result."]
    body = (
        h1("Eclipse 68k Fix") +
        p("Eclipse 68k Fix is a small classic Macintosh application that fixes a bug in your copy of "
          "<i>Reversi The Eclipse 1.2</i> (Lars Graulund, published by Freeverse Software, 1997). On 68k Macs "
          "the game cannot connect to an Internet Othello Server, ParlorNet included; with the fix it can. It "
          "changes two bytes. The game is not included.") +
        callout(p("<b>Don&#39;t have the game?</b> Info-Mac has the copy the patcher is made for: %s\n"
                  "(%s, MD5 <tt>%s</tt>). The patcher accepts only this version, as shipped, and leaves any "
                  "other alone."
                  % (a(game, "<b>%s</b>" % esc(game.rsplit("/", 1)[1])), downloads.kb(ECLIPSE_GAME_FACTS[0]),
                     ECLIPSE_GAME_FACTS[1]), 2)) +
        p(" | ".join(a("#" + n, esc(t)) for n, t in parts), 2) +
        '<p><img src="%s" width="448" height="258" alt="[The Eclipse 68k Fix window on System 7.6.1]"></p>\n'
        % ECLIPSE_FIX_GIF +
        h2("bug", "The bug") +
        p("On 68k Macs, Reversi The Eclipse 1.2 turns any host name shorter than 32 characters into repeats of "
          "its first letter before it looks the name up: <tt>parlornet.org</tt> becomes "
          "<tt>ppppppppppppp</tt>, and even the game&#39;s own default, <tt>external.nj.nec.com</tt>, becomes "
          "<tt>eeeeeeeeeeeeeeeeeee</tt>. Numeric addresses go the same way, so the game can never connect. A "
          "real 68k Mac does this too, so the fault is in the game&#39;s code, not the system or an emulator. "
          "PowerPC Macs are not affected.") +
        p("The game is a fat application: PowerPC code and 68k code built from the same source. Before it "
          "connects, it turns the host name from a C string into a Pascal string in place, moving the "
          "characters up one byte with <tt>memcpy</tt>. The 68k compiler expanded that <tt>memcpy</tt> "
          "inline: for 32 bytes or more it calls the system&#39;s <tt>BlockMoveData</tt>, which copes with "
          "overlapping blocks, but for shorter names it uses a byte loop that copies the first letter all the "
          "way along. The PowerPC code calls <tt>BlockMoveData</tt> for every length, which is why the game "
          "connects from a PowerPC Mac.") +
        h2("fix", "What the fix changes") +
        p("The fix changes two bytes in the game&#39;s 68k code, in resource <tt>CODE</tt> 1. The branch that "
          "picks the byte loop for short names (<tt>65 04</tt>, <tt>bcs.b</tt>) becomes a no-op (<tt>4E 71</tt>, "
          "<tt>nop</tt>), so every name takes the <tt>BlockMoveData</tt> path the game already uses for long "
          "ones. The PowerPC code, the Finder information and the other resources stay as they were, so the "
          "patched copy still runs on a PowerPC Mac.") +
        p("Before it writes, the patcher checks the size of <tt>CODE</tt> 1, a cryptographic hash of the 64 bytes "
          "around the branch and a CRC-32 of the whole resource, so it only ever patches Reversi The Eclipse 1.2 "
          "as it was shipped. It carries only these checksums, none of the game&#39;s code.") +
        h2("use", "Using it") +
        p("You need a 68k Mac with System 7 or later. (The patcher also runs on PowerPC Macs, as a 68k "
          "application, but they do not need the fix.)") +
        "<ol>\n%s</ol>\n" % "".join("<li>%s\n" % font(s) for s in steps) +
        p("It changes nothing, and says why, if the copy is not exactly Reversi The Eclipse 1.2 as shipped, if "
          "it is already fixed, if the file or the disk is locked, or if the game is running.") +
        p("Then open the game&#39;s Internet Othello Server dialog and type <tt>%s</tt> as the host, the name "
          "of the ParlorNet server you play on, and <tt>6503</tt> as the port. The IOS part of the %s tells you "
          "how to sign in." % (host, a(C.PROTOCOLS_DOC + "#ios", "Supported protocols guide"))) +
        h2("download", "Download") +
        downloads.downloads_html(SLUG, README) +
        h2("game", "The game") +
        p("Reversi The Eclipse was shareware, US$19.95, and Freeverse no longer sells it. Version 1.2, uploaded "
          "to Info-Mac by Freeverse in November 1997, was the last; the copy above is that upload.") +
        h2("search", "Was it ever fixed?") +
        p("No later version or patch was ever published. A search in November 2026 of the Info-Mac archive and "
          "its Hyperarchive mirror, Freeverse&#39;s own pages from 1998 to 2002 in the Wayback Machine, "
          "Macintosh Repository, Macintosh Garden, Othello sites and the Usenet Mac games groups found nothing "
          "after 1.2 and no report of this bug; Freeverse&#39;s FAQ said &quot;There are no known bugs in "
          "Eclipse&quot;. It probably went unnoticed because the game&#39;s Internet play stopped working on "
          "every Mac soon after release, when the server moved and was later replaced by the Generic Game "
          "Server.") +
        h2("licence", "License") +
        p("Eclipse 68k Fix is free software under the MIT license; the full text is in the %s. Its source is in "
          "%s." % (downloads.readme_link(README),
                   a(downloads.repo_url(downloads.ECLIPSE_REPO), downloads.ECLIPSE_REPO))) +
        p("Reversi The Eclipse is by Lars Graulund and was published by Freeverse Software. This patcher is not "
          "affiliated with either and contains none of the game."))
    return body
