"""The operator's own files for the web site, read from the site directory (deploy/README.md, A site of your
own). welcome.htm is shown on the welcome page right after the message of the day. pages/<name>.htm is a page
of its own at /<name>.htm. Its first line is its title, written as <title>Its title</title>, and the rest is its
body. Both are HTML 3.2 fragments, with placeholders that are filled in when a page is built
(parlord/sitetext.py). The web extension reads them once, when it starts, and the log says what it found and
what it left out.
"""
import os
import re
import sys

WELCOME = "welcome.htm"
PAGES = "pages"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}\Z")
TITLE_RE = re.compile(r"\A[ \t\n]*<title>([^<>\n]{1,60})</title>[ \t]*(?:\n|\Z)", re.I)
MOST_BYTES = 32 * 1024      # a file larger than this is left out
MOST_PAGES = 64


def say(line):
    print(line, file=sys.stderr, flush=True)


def ascii_html(text):
    """HTML with each character beyond ASCII written as a decimal character reference, as every page is."""
    return "".join(c if ord(c) < 128 else "&#%d;" % ord(c) for c in text)


class Site:
    """What the site directory holds: welcome (the fragment for the welcome page, "" without one) and pages
    ({"/dial-in.htm": (title, body)}, the title as plain text, the body as HTML)."""

    def __init__(self, welcome="", pages=None):
        self.welcome = welcome
        self.pages = pages or {}


def read(path, label, log=say):
    """A file's text, UTF-8, with its line ends made \\n; None, and a line in the log, when it cannot be used."""
    try:
        with open(path, "rb") as f:
            data = f.read(MOST_BYTES + 1)
    except OSError as e:
        log("site: %s cannot be read (%s), so it is left out" % (label, e.strerror or type(e).__name__))
        return None
    if len(data) > MOST_BYTES:
        log("site: %s is larger than %d KB, so it is left out" % (label, MOST_BYTES // 1024))
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        log("site: %s is not UTF-8 text, so it is left out" % label)
        return None
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def load(directory, builtin, log=say):
    """The site directory's files -> Site. builtin: the paths of the site's own pages, which a page of the
    operator's may not take. A page that would take one, a name that is not 1 to 32 lower-case letters, digits
    and -, a page without its title line, and a file that cannot be read are left out, each with a line in the
    log."""
    site = Site()
    if not directory:
        return site
    if not os.path.isdir(directory):
        log("site: %s is not a directory, so the site has no files of its own" % directory)
        return site
    path = os.path.join(directory, WELCOME)
    if os.path.isfile(path):
        site.welcome = ascii_html(read(path, WELCOME, log) or "")
    folder = os.path.join(directory, PAGES)
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    for fn in names:
        label = "%s/%s" % (PAGES, fn)
        if fn.startswith("."):
            continue
        name = fn[:-len(".htm")] if fn.endswith(".htm") else ""
        if not NAME_RE.match(name) or not os.path.isfile(os.path.join(folder, fn)):
            log("site: %s is not named <name>.htm, with a name of 1 to 32 lower-case letters, digits and -, so "
                "it is not served" % label)
            continue
        url = "/%s.htm" % name
        if url in builtin:
            log("site: %s would take the place of the site's own page %s, so it is not served" % (label, url))
            continue
        if len(site.pages) >= MOST_PAGES:
            log("site: %s is past the first %d pages, so it is not served" % (label, MOST_PAGES))
            continue
        text = read(os.path.join(folder, fn), label, log)
        if text is None:
            continue
        m = TITLE_RE.match(text)
        if not m or not m.group(1).strip():
            log("site: %s does not start with its title, as <title>Its title</title>, so it is not served"
                % label)
            continue
        site.pages[url] = (m.group(1).strip(), ascii_html(text[m.end():]))
    found = (["welcome.htm"] if site.welcome.strip() else []) + sorted(site.pages)
    log("site: %s has %s" % (directory, ", ".join(found) if found else "nothing the web site shows"))
    return site
