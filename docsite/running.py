"""running.html: running a ParlorNet server of your own, made from the repository's own instructions.

The page joins two files of the source, read as they are, so the docs site and the repository always say the
same thing: the "Run your own parlor" section of README.md (the quick start, one docker run) and deploy/README.md
(installing on a Docker host for a server that stays up, ParlorNet Play over HTTPS, administration). Nothing else
outside docs/ is read: not the README's other sections, and nothing else in deploy/.

A link in either file to a heading of the other one, or of itself, goes to that heading on this page; any other
link is fixed as in the docs (docsite/build.py, link_fixer).
"""
import os
import posixpath
import re

from adapters import clients as C
from adapters.mdhtml import to_html
from adapters.webstyle import a, h1, p
from parlord import PROJECT_URL
from . import clientpages

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = C.RUNNING_PAGE
TITLE = "Run a server"
README = "README.md"
README_SECTION = "Run your own parlor"
DEPLOY = "deploy/README.md"
GITHUB_BLOB = PROJECT_URL + "/blob/main/"


def read(rel):
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read().encode("ascii", "replace").decode("ascii")


def section(md, heading):
    """The ## heading section of md, its heading line included, up to the next ## heading."""
    lines = md.split("\n")
    start = lines.index("## " + heading)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).strip() + "\n"


def demote(md):
    """Every heading one level down (# to ##), outside code blocks."""
    out, code = [], False
    for line in md.split("\n"):
        if line.strip().startswith("```"):
            code = not code
        out.append("#" + line if not code and re.match(r"^#{1,5} ", line) else line)
    return "\n".join(out)


def sources():
    """[(file, its directory, its Markdown as shown here)]: the README's section, then deploy/README.md."""
    return [(README, "", section(read(README), README_SECTION)), (DEPLOY, "deploy", demote(read(DEPLOY)))]


def body(link_fixer):
    """The page's content. link_fixer(directory) is the docs' link fix for Markdown in that directory."""
    parts = sources()
    names = set()
    for _, _, md in parts:          # the anchors this page has, named as GitHub names them
        names.update(re.findall(r'<a name="([^"]+)"', to_html(md)))
    first = {rel: re.findall(r'<a name="([^"]+)"', to_html(md))[0] for rel, _, md in parts}

    def fixer(md_dir):
        base = link_fixer(md_dir)

        def fix(u):
            path, _, frag = u.partition("#")
            if path and not re.match(r"^[a-z]+:", u):
                target = posixpath.normpath(posixpath.join(md_dir, path))
                if target in first and (not frag or frag in names):
                    return "#" + (frag or first[target])
            elif not path and frag:
                return u
            return base(u)
        return fix

    html = []
    for _, md_dir, md in parts:
        fix = fixer(md_dir)
        html.append(to_html(md, fix, clientpages.figure))
    return (h1(TITLE) +
            p("Anyone can run a ParlorNet server for a club, an event or the open internet. A ParlorNet server is "
              "called a parlor, and it runs in one Docker container that you build from the source. ParlorNet is "
              "free software under the MIT license, and its source is at %s."
              % a(PROJECT_URL, "github.com/eric-cgn/parlornet")) +
            p("This page has two parts, both taken from the instructions in the source. The first part, %s, starts "
              "a parlor with one <tt>docker run</tt>. The second part, %s, installs a parlor that stays up. It "
              "also covers %s and %s."
              % (a("#" + first[README], README_SECTION), a("#" + first[DEPLOY], "Installing on a Docker host"),
                 a("#parlornet-play-over-https", "ParlorNet Play over HTTPS"), a("#administration", "administration"))) +
            "\n".join(html) +
            p("This page is made from %s and %s in the source, where you also find the install scripts. The %s "
              "page lists the other docs for operators." % (
                  a(GITHUB_BLOB + README + "#run-your-own-parlor", "README.md"), a(GITHUB_BLOB + DEPLOY, DEPLOY),
                  a("/docs.html", "Docs")), 2))
