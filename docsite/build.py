"""Builds the docs site (http://docs.parlornet.org/) from the repository: tools/build_docs.py runs build().

The site serves every ParlorNet server alike. Its pages, from the site's root:

    index.html              Clients: the summary grid, each game's native clients, Your own program, and the
                            browsers and telnet programs once, under Any Game (Generic) Clients
                            (adapters/clients.py), with a finder
    protocols.html          the protocols reference: an entry per key (docsite/protocols.py)
    browsers.html           what a browser needs and each browser's setup
    telnet.html             the same for telnet programs
    clients/<id>.html       one client's own page: its ratings and facts from adapters/clients.py and its prose from
                            docs/clients/<id>.md (docsite/clientpages.py), with its screenshots in img/
    setup/<id>.html         the old address of a period client's setup, pointing to its page
    eclipse-68k-fix.html    ParlorNet's patcher for Reversi The Eclipse 1.2 (docsite/eclipse.py)
    parlornet-rulebook.html ParlorNet's rulebook for GameMaster (docsite/rulebook.py)
    gamemaster-rulebooks.html  ParlorNet's game rulebooks for GameMaster and their installer
                            (docsite/gamerulebooks.py)
    download/, img/         the downloads of the patcher and the rulebooks' installer, and their READMEs (from
                            the releases pinned in docsite/downloads.json, docsite/downloads.py), the
                            patcher's screenshot, and the site's emblem
    docs.html               the docs in groups, with a finder
    docs/<name>.html        every Markdown file under docs/, rendered by adapters/mdhtml.py
    404.html, CNAME, .nojekyll   for GitHub Pages

Only docs/ is read for docs pages (docs/clients/ becomes the client pages instead): nothing from deploy/ or
anywhere else is published. A link in a doc to another doc becomes a link to its page; to a client's Markdown,
a link to the client's page; to any other file of the repository, a link to it at the project's home on
GitHub; to http://docs.parlornet.org/..., a link within the site. Every page is built in the web site's
frame and palette (adapters/webstyle.py), HTML 3.2 as on the game server, with the docs site's own navigation
and footer, and links relative to the page, so the site works from any address or a folder.

The finders: on index.html and docs.html, a script writes a box (and, on the Clients page, platform and
processor menus) that hides the rows that do not match. A browser without the script, or without the DOM
it needs, shows no box and the full list; no content depends on it. The Clients page also reads ?plat=,
?arch= and ?q= from its address, so the summary grid's links and the game server's old /frontends.htm
filters land on a narrowed list; without the script they land on the full one.
"""
import json
import os
import posixpath
import re
import shutil

from parlord import DOCS_URL, PROJECT_URL
from adapters import clients as C
from adapters import webstyle as S
from adapters.mdhtml import to_html
from adapters.webstyle import a, esc, h1, h2, p, ul
from . import clientpages, eclipse, gamerulebooks, protocols, rulebook

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
OUT = os.path.join(ROOT, "build", "docs-site")
CNAME = "docs.parlornet.org"
MARKER = ".parlornet-docs"          # in a folder this tool wrote, so it may empty it before writing again
GITHUB_BLOB = PROJECT_URL + "/blob/main/"
GITHUB_TREE = PROJECT_URL + "/tree/main/"

NAV = [("/index.html", "Clients"), ("/protocols.html", "Protocols"), ("/browsers.html", "Browsers"),
       ("/telnet.html", "Telnet programs"), ("/docs.html", "Docs"), (PROJECT_URL, "Source")]
FOOT = ("These pages cover every ParlorNet server. | %s | ParlorNet is free software."
        % a(PROJECT_URL, "Source code"))

# The docs index (docs.html): (heading, [(path, title, what it is)]), in reading order. Any doc not listed
# here is listed after these by its file name, by folder, so a new doc is never left out.
DOC_GROUPS = [
    ("For players", [
        ("/index.html", "Clients", "the programs that play each game, for each kind of computer, with their setup"),
        ("/browsers.html", "Browsers", "what a browser needs, and setting each one up"),
        ("/telnet.html", "Telnet programs", "what a telnet program needs, and setting each one up"),
        ("/protocols.html", "Protocols", "every protocol a ParlorNet server can speak, by its key"),
        ("/docs/protocols.html", "Supported protocols", "how to connect, sign up and sign in with each "
         "protocol, and its history"),
        ("/docs/accounts.html", "Accounts", "sign-in methods, sign-up and what a server can ask"),
        ("/docs/modern-client.html", "ParlorNet Play", "the client for today's browsers, phones and tablets"),
        ("/docs/play-notifications.html", "Play notifications", "turning ParlorNet Play's notifications on and off "
         "in each browser, and what they say"),
        ("/docs/apple2.html", "ParlorNet for the Apple IIe", "the player's guide to the Apple IIe and IIgs program"),
        ("/docs/serial.html", "Serial connections", "reaching a parlor over a serial line: WiFi modems, a Unix "
         "login, ATZ, tcpser and bridges, with the cable"),
        ("/docs/chat-commands.html", "Chat commands", "the commands typed in the chat-style consoles"),
        ("/docs/clients.html", "Frontend downloads", "where each period program comes from, with licenses"),
        ("/eclipse-68k-fix.html", "Eclipse 68k Fix", "lets Reversi The Eclipse 1.2 connect from a 68k Mac"),
        ("/parlornet-rulebook.html", "ParlorNet rulebook", "plays a parlor's games from GameMaster on a Mac"),
        ("/gamemaster-rulebooks.html", "Game rulebooks", "ParlorNet's games for GameMaster, and their installer"),
    ]),
    ("For frontend writers", [
        ("/docs/pnp.html", "Protocol guide", "PARLORNET/1 for frontend writers, with signing in, tables, every "
         "game and sample sessions"),
        ("/docs/protocol.html", "Native protocol", "the reference"),
        ("/docs/transports.html", "Transports", "serial lines, modems and bridges"),
        ("/docs/bandwidth.html", "Bandwidth", "what a game costs on a slow line"),
        ("/docs/design/trust.html", "Trust", "the rules for clients and server extensions, with the server deciding"),
    ]),
    ("For operators and developers", [
        (PROJECT_URL, "Source code", "the project's home, for running your own server or contributing"),
        ("/docs/adapters.html", "Server extensions", "how they are built"),
        ("/docs/architecture.html", "Architecture", "the core, the games and the extensions"),
        ("/docs/games-howto.html", "Adding a game", "the plugin and its frontend module"),
        ("/docs/plugins.html", "Game plugins", "the plugin interface"),
        ("/docs/security-design.html", "Security design", "identity, sign-in, sessions and the extension channel"),
        ("/docs/auth-federation.html", "Auth and federation", "deployment and federation"),
        ("/docs/site.html", "Web sites", "the game server's pages, this docs site, and their page frame"),
        ("/docs/backlog.html", "Backlog", "ideas recorded for later"),
    ]),
]
FOLDERS = [("types", "Game type specs"), ("design", "Design notes"), ("", "Other docs")]
SKIP_IN_INDEX = {"/docs/frontends.html"}        # the old frontends guide: a short page that links on


def games():
    """[(name, type)] of every game in build order (adapters/web.py's list and the game modules)."""
    from adapters.web import game_rows
    return [(r["name"], r["type"]) for r in game_rows()]


def doc_files():
    """{site path: Markdown file} of every doc under docs/: /docs/types/reversi-1.html -> its path."""
    out = {}
    for root, _, files in os.walk(DOCS_DIR):
        if os.path.relpath(root, DOCS_DIR).replace(os.sep, "/").split("/")[0] == "clients":
            continue                    # the client pages (docsite/clientpages.py)
        for f in files:
            if f.endswith(".md"):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, DOCS_DIR)[:-3].replace(os.sep, "/")
                out["/docs/%s.html" % rel] = full
    return dict(sorted(out.items()))


def frame(title, body, current=None, head_extra="", refresh=None):
    return S.frame(title, body, nav=S.nav_html(NAV, current), foot=FOOT, head_extra=head_extra, refresh=refresh)


# ---- the finders ------------------------------------------------------------------------------------

FINDER_JS = r"""<script language="JavaScript"><!--
var pnRuns = %(runs)s, pnRows = [], pnGroups = [], pnCount = null;
function pnText(e) { return (e.textContent || e.innerText || "").toLowerCase(); }
function pnPick(s) { return s ? s.options[s.selectedIndex].value : ""; }
function pnFind() {
  var f = document.forms["pnfind"], words = f.q.value.toLowerCase().split(/\s+/), plat = pnPick(f.plat),
      arch = pnPick(f.arch), shown = 0, i, j, k;
  for (i = 0; i < pnRows.length; i++) {
    var r = pnRows[i], ok = true, t = pnText(r.el);
    for (j = 0; j < words.length; j++) if (words[j] && t.indexOf(words[j]) < 0) ok = false;
    if (ok && (plat || arch)) {
      ok = false;
      for (k = 0; k < r.runs.length; k++) {
        var pa = r.runs[k].split(":");
        if ((!plat || pa[0] == plat) && (!arch || pa[1] == arch)) ok = true;
      }
    }
    r.el.style.display = ok ? "" : "none";
    r.on = ok;
    if (ok) shown++;
  }
  for (i = 0; i < pnGroups.length; i++) {
    var g = pnGroups[i], any = false;
    for (j = 0; j < g.rows.length; j++) if (g.rows[j].on) any = true;
    g.el.style.display = any ? "" : "none";
    g.note.style.display = any ? "none" : "";
  }
  pnCount.nodeValue = " Showing " + shown + " of " + pnRows.length + ".";
}
function pnReset() {
  var f = document.forms["pnfind"];
  f.q.value = "";
  if (f.plat) { f.plat.selectedIndex = 0; f.arch.selectedIndex = 0; }
  pnFind();
}
function pnGroup(el, row, none) {
  for (var i = 0; i < pnGroups.length; i++) if (pnGroups[i].el == el) { pnGroups[i].rows.push(row); return; }
  var note = document.createElement("p"), face = document.createElement("font");
  face.setAttribute("face", %(face)s);
  face.setAttribute("size", "2");
  face.appendChild(document.createTextNode(none));
  note.appendChild(face);
  note.style.display = "none";
  el.parentNode.insertBefore(note, el);
  pnGroups.push({el: el, rows: [row], note: note});
}
function pnInit() {
  var f = document.forms["pnfind"], i, j;
  if (!f) return;
  if (pnRuns) {
    var trs = document.getElementsByTagName("tr");
    for (i = 0; i < trs.length; i++) {
      var links = trs[i].getElementsByTagName("a");
      for (j = 0; j < links.length; j++) {
        var h = links[j].getAttribute("href");
        if (pnRuns[h] && pnText(links[j]) == "setup") {
          var row = {el: trs[i], runs: pnRuns[h], on: true}, t = trs[i];
          while (t && t.tagName != "TABLE") t = t.parentNode;
          pnRows.push(row);
          if (t) pnGroup(t, row, /^(browsers|telnet)\.html/.test(h) ? %(none_here)s : %(no_native_here)s);
          break;
        }
      }
    }
  } else {
    var lis = document.getElementsByTagName("li");
    for (i = 0; i < lis.length; i++) {
      var row = {el: lis[i], runs: [], on: true};
      pnRows.push(row);
      pnGroup(lis[i].parentNode, row, "Nothing here matches.");
    }
  }
  pnCount = document.createTextNode("");
  f.getElementsByTagName("font")[0].appendChild(pnCount);
  var q = location.search.substring(1).split("&");
  for (i = 0; i < q.length; i++) {
    var kv = q[i].split("="), v = decodeURIComponent((kv[1] || "").replace(/\+/g, " ")), s = f[kv[0]];
    if (kv[0] == "q") f.q.value = v;
    else if ((kv[0] == "plat" || kv[0] == "arch") && s)
      for (j = 0; j < s.options.length; j++) if (s.options[j].value == v) s.selectedIndex = j;
  }
  pnFind();
  if (location.hash) location.hash = location.hash;
}
if (document.getElementsByTagName && document.createTextNode && document.forms) {
  document.write(%(form)s);
  window.onload = pnInit;
}
//--></script>
"""


def finder(clients):
    """The finder's script. clients: the Clients page's (text, platform and processor, over the client rows);
    otherwise the docs index's (text, over the list items)."""
    def select(name, options):
        return '<select name="%s" onchange="pnFind()">%s</select>' % (name, "".join(
            '<option value="%s">%s' % (v, esc(label)) for v, label in [("", "Any")] + options))
    menus = (" Platform: %s Processor: %s" % (select("plat", C.PLATFORMS), select("arch", C.ARCHS))
             if clients else "")
    form = ('<form name="pnfind" action="#" onsubmit="pnFind(); return false;"><p><font face="%s" size="2">'
            'Find: <input name="q" size="16" onkeyup="pnFind()">%s <input type="button" value="Show all" '
            'onclick="pnReset()"></font></p></form>' % (S.TEXT_FACE, menus))
    runs = ({c.href().lstrip("/"): ["%s:%s" % (pl, ar) for pl, archs in c.runs for ar in archs] for c in C.ALL}
            if clients else None)
    return FINDER_JS % {"runs": json.dumps(runs, sort_keys=True),
                        "form": json.dumps(form).replace("</", "<\\/"),     # no end tag inside the script
                        "none_here": json.dumps(C.NONE_HERE), "no_native_here": json.dumps(C.NO_NATIVE_HERE),
                        "face": json.dumps(S.TEXT_FACE)}


# ---- pages ------------------------------------------------------------------------------------------

def doc_title(md, rel):
    return next((l[2:].strip() for l in md.split("\n") if l.startswith("# ")), rel)


def link_fixer(md_dir):
    """The link fix for a doc in md_dir (a folder of the repository: docs, docs/types): another doc -> its
    page; a link into the docs site -> within the site; any other file of the repository -> GitHub."""
    def fix(u):
        if u.startswith(DOCS_URL):
            rest = u[len(DOCS_URL):]
            return "/" + (rest if rest and not rest.startswith(("#", "?")) else "index.html" + rest)
        if re.match(r"^[a-z]+:", u) or u.startswith("#"):
            return u
        path, sep, frag = u.partition("#")
        target = posixpath.normpath(posixpath.join(md_dir, path))
        full = os.path.join(ROOT, *target.split("/"))
        m = re.match(r"^docs/clients/([a-z0-9-]+)\.md$", target)
        if m and m.group(1) in C.BY_ID:
            return C.BY_ID[m.group(1)].href() + sep + frag
        if target.startswith("docs/clients/") and os.path.exists(full):
            return (GITHUB_TREE if os.path.isdir(full) else GITHUB_BLOB) + target + sep + frag
        if target.startswith("static/img/") and os.path.isfile(full):
            return "/img/" + target.rsplit("/", 1)[1]
        if target.startswith("docs/") and target.endswith(".md") and os.path.isfile(full):
            return "/" + target[:-3] + ".html" + sep + frag
        if os.path.isdir(full):
            return GITHUB_TREE + target
        return GITHUB_BLOB + target + sep + frag
    return fix


def doc_page(path, md_file):
    with open(md_file, encoding="utf-8") as f:
        md = f.read().encode("ascii", "replace").decode("ascii")
    rel = path[len("/docs/"):-len(".html")]
    body = to_html(md, link_fixer(posixpath.dirname("docs/" + rel)))
    body += p(a(GITHUB_BLOB + "docs/" + rel + ".md", "This doc on GitHub"), 2)
    return frame("ParlorNet: %s" % doc_title(md, rel), body, "/docs.html")


def docs_index(docs):
    """docs.html: the docs in groups with a line on each, then every other doc by folder and file name."""
    listed, out = set(), []
    for title, items in DOC_GROUPS:
        lines = []
        for href, name, what in items:
            if href.startswith("/docs/") and href.split("#")[0] not in docs:
                continue
            listed.add(href)
            lines.append("%s: %s" % (a(href, "<b>%s</b>" % esc(name)), esc(what)))
        out.append(h2(None, esc(title)) + ul(lines))
    rest = {}
    for path in docs:
        if path not in listed and path not in SKIP_IN_INDEX:
            rest.setdefault(path[len("/docs/"):-len(".html")].rpartition("/")[0], []).append(path)
    order = [f for f, _ in FOLDERS]
    for folder in sorted(rest, key=lambda f: order.index(f) if f in order else len(order)):
        out.append(h2(folder or None, esc(dict(FOLDERS).get(folder, folder))) +
                   ul(a(x, esc(x[len("/docs/"):-len(".html")].rpartition("/")[2])) for x in rest[folder]))
    body = (h1("Docs") +
            p("These docs cover how ParlorNet works and how to play, connect, write a frontend or run a server. "
              "They are written in Markdown in the docs folder of the source.") +
            finder(False) + "".join(out))
    return frame("ParlorNet: docs", body, "/docs.html")


def pages():
    """{site path: page HTML, links still from the site's root}."""
    gs = games()
    docs = doc_files()
    out = {
        "/index.html": frame("ParlorNet: clients", C.frontends_body(gs, finder(True)), "/index.html"),
        "/protocols.html": frame("ParlorNet: protocols", protocols.protocols_body(gs), "/protocols.html"),
        "/browsers.html": frame("ParlorNet: browsers", C.browsers_body(), "/browsers.html"),
        "/telnet.html": frame("ParlorNet: telnet programs", C.telnet_body(), "/telnet.html"),
        "/eclipse-68k-fix.html": frame("Eclipse 68k Fix", eclipse.eclipse_fix_body()),
        rulebook.PAGE: frame("ParlorNet rulebook for GameMaster", rulebook.body()),
        gamerulebooks.PAGE: frame("ParlorNet game rulebooks for GameMaster", gamerulebooks.body()),
        "/docs.html": docs_index(docs),
        "/404.html": frame("ParlorNet: not found", h1("Not found") + p(
            "There is no page at this address. Try the %s, %s or %s page."
            % (a("/index.html", "Clients"), a("/protocols.html", "Protocols"), a("/docs.html", "Docs")))),
    }
    fix = link_fixer(clientpages.PAGES_REL)
    for c in C.EVERY:
        out[c.href()] = frame("ParlorNet: %s" % c.name, clientpages.client_body(c, fix), "/index.html")
    for path, c in C.OLD_SETUP_PAGES.items():
        out[path] = frame("ParlorNet: %s" % c.name, clientpages.moved_body(c), "/index.html",
                          refresh=(0, relative(path.lstrip("/"), c.href())))
    for path, md_file in docs.items():
        out[path] = doc_page(path, md_file)
    return out


LINK_RE = re.compile(r'(\b(?:href|src)=")(/(?!/)[^"]*)(")')


def relative(page, target):
    """A link from the site's root (/docs/pnp.html#x) as seen from page (docs/types/reversi-1.html)."""
    m = re.match(r"([^?#]*)(.*)\Z", target, re.S)
    path, rest = m.group(1), m.group(2)
    if path in ("", "/"):
        path = "/index.html"
    rel = posixpath.relpath(path.lstrip("/"), posixpath.dirname(page) or ".")
    return rel + rest


def relativize(page, html):
    return LINK_RE.sub(lambda m: m.group(1) + relative(page, m.group(2)) + m.group(3), html)


def files():
    """{site path: bytes}: every file of the site, pages with their links made relative (404.html keeps them
    from the root: GitHub Pages shows it at any address)."""
    out = {}
    for path, html in pages().items():
        name = path.lstrip("/")
        out[name] = (html if name == "404.html" else relativize(name, html)).encode("ascii")
    from adapters.web import make_gif
    out[S.EMBLEM.lstrip("/")] = S.emblem_gif(make_gif)
    out.update(eclipse.files())
    out.update(rulebook.files())
    out.update(gamerulebooks.files())
    out.update(clientpages.files())
    out["CNAME"] = (CNAME + "\n").encode("ascii")
    out[".nojekyll"] = b""
    out[MARKER] = b"Written by tools/build_docs.py; the folder is emptied before each build.\n"
    return out


def build(out_dir=OUT):
    """Write the site into out_dir (made if need be). A folder this tool wrote before is emptied first; any
    other folder must be empty. Returns {site path: bytes}."""
    if os.path.isdir(out_dir) and os.listdir(out_dir):
        if not os.path.exists(os.path.join(out_dir, MARKER)):
            raise SystemExit("%s is not empty and was not written by this tool; give an empty folder" % out_dir)
        for name in os.listdir(out_dir):
            full = os.path.join(out_dir, name)
            shutil.rmtree(full) if os.path.isdir(full) and not os.path.islink(full) else os.unlink(full)
    site = files()
    for name, data in site.items():
        full = os.path.join(out_dir, *name.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
    return site
