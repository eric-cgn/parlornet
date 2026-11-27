"""Placeholders in the operator's texts and pages: the message of the day, and the site directory's
welcome.htm and pages (deploy/README.md, A site of your own). Each one is filled in from the server's
configuration when the text is built, so the same files work on any server.

A placeholder is a word in braces, such as {host}, or {port:TELNET} for a protocol's public port. Write {{ for
a brace { and }} for a brace }. The text is only searched for these, and nothing in it is run. A placeholder
this module does not know is left as it was written, and the log says so once. So is one with no value right
now, such as {play} while ParlorNet Play is not running, except in the message of the day, which leaves out
every line that holds one (as it always has for a cleared {contribute}).

    {name}        the server's name (server.name, else PARLOR_NAME)
    {host}        the host name in web.public_url when that is set, else the server's name
    {web}         the web site's address: web.public_url, else http://{host}:{port:HTTP}/
    {play}        ParlorNet Play's address: play.public_url, else http://{host}:{port:HTTPS}/play/ while it runs
    {docs}        the docs site (site.docs_url)
    {contribute}  the project's home, where to get the source or contribute (site.contribute_url)
    {port:KEY}    the public port of the protocol KEY as the core lists it in PRL: {port:TELNET},
                  {port:PARLORNET/1}, {port:FIBS} and so on
"""
import re
import sys
from urllib.parse import urlsplit

NAMES = ("name", "host", "web", "play", "docs", "contribute")
# {{, }}, or a placeholder's shape: a letter, then letters, digits and _ : / . -
TOKEN_RE = re.compile(r"\{\{|\}\}|\{([A-Za-z][A-Za-z0-9_:/.-]{0,40})\}")
PORT_RE = re.compile(r"^port:([A-Za-z][A-Za-z0-9/-]{0,11})\Z")
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})\Z")
_logged = set()


def usable_url(url):
    """url if it can go in a text as it is, printable ASCII with no spaces; else ""."""
    url = (url or "").strip()
    return url if all(" " < c <= "~" for c in url) else ""


def host_of_url(url):
    """The host name in an http or https address, or "" when there is none."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host if HOST_RE.match(host) else ""


def values(name, ports, web="", play="", docs="", contribute=""):
    """The placeholders' values, {placeholder: text}, from what the caller knows of its configuration. name:
    the server's name; ports: [(key, port)] as PRL lists them; web and play: web.public_url and
    play.public_url ("" when not set); docs and contribute: site.docs_url and site.contribute_url ("" when the
    operator cleared them). A placeholder with no value is not in the result."""
    out = {}
    ports = {k.upper(): p for k, p in ports or ()}
    for key, port in ports.items():
        out["port:" + key] = str(port)
    if name:
        out["name"] = name
    web, play = usable_url(web), usable_url(play)
    host = host_of_url(web) or (name if HOST_RE.match(name or "") else "")
    if host:
        out["host"] = host

    def own(key, path):
        port = ports.get(key)
        return "http://%s%s%s" % (host, "" if port == 80 else ":%d" % port, path) if host and port else ""
    for k, v in (("web", web or own("HTTP", "/")), ("play", play or own("HTTPS", "/play/")),
                 ("docs", usable_url(docs)), ("contribute", usable_url(contribute))):
        if v:
            out[k] = v
    return out


def known(word):
    return word in NAMES or bool(PORT_RE.match(word))


def lookup(word, vals):
    """A placeholder's value, or None when it has none."""
    m = PORT_RE.match(word)
    return vals.get("port:" + m.group(1).upper()) if m else vals.get(word)


def note(where, word, why, log=None):
    """Say once in the log that where holds a placeholder that is shown as written."""
    if (where, word, why) in _logged:
        return
    _logged.add((where, word, why))
    (log or (lambda s: print(s, file=sys.stderr, flush=True)))(
        "site: %s has {%s}, which %s, so it is shown as written" % (
            where, word, "is not a placeholder" if why == "unknown" else "has no value now"))


def expand(text, vals, where, escape=None, log=None):
    """text with each placeholder replaced by its value (put through escape, such as HTML escaping, when
    given), {{ and }} by single braces. An unknown placeholder, and one with no value now, stay as written
    and are logged once."""
    def one(m):
        tok = m.group(0)
        if tok in ("{{", "}}"):
            return tok[0]
        word = m.group(1)
        v = lookup(word, vals) if known(word) else None
        if v is None:
            note(where, word, "no value" if known(word) else "unknown", log)
            return tok
        return escape(v) if escape else v
    return TOKEN_RE.sub(one, text)


def expand_lines(lines, vals, where, log=None):
    """Lines of plain text, such as the message of the day, with their placeholders filled in. A line that
    holds a known placeholder with no value is left out. Unknown ones stay as written, and are logged once."""
    out = []
    for line in lines:
        words = [m.group(1) for m in TOKEN_RE.finditer(line) if m.group(1)]
        if any(known(w) and lookup(w, vals) is None for w in words):
            continue
        out.append(expand(line, vals, where, log=log))
    return out
