"""A small Markdown-to-HTML 3.2 converter for the project's own docs.

Covers what docs/ uses: headings, paragraphs, - and 1. lists, pipe tables,
fenced code blocks, `code`, **bold**, [text](link), and a picture on a line
of its own, ![caption](file), which the caller draws (image). Every heading carries an
<a name> anchor named as GitHub names it (slug()). Output has no CSS and no
hex character references, so 1990s browsers show it. The docs read as a manual:
body text and tables in the browser's own text face (no FONT per paragraph or
cell, which would add a third to a long doc), headings in the site's heading
face and color, and tables with the site's header row and alternating rows."""
import re

from .webstyle import DEEP, HEAD_FACE, IVORY, SAGE

HEAD_SIZES = {1: 5, 2: 4, 3: 3}     # FONT SIZE per heading level; deeper ones keep the browser's own size


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def inline(text, link_fix):
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) > 1:
            out.append("<tt>%s</tt>" % esc(p[1:-1]))
            continue
        p = esc(p)
        p = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", p)
        p = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: '<a href="%s">%s</a>' % (link_fix(m.group(2)), m.group(1)), p)
        out.append(p)
    return "".join(out)


def slug(heading, seen):
    """A heading's anchor name, as GitHub makes it, so links like protocols.md#telnet work both on
    GitHub and here: the heading's text, lower case, without punctuation, spaces as hyphens; a repeat
    gets -1, -2 ..."""
    text = re.sub(r"\[([^\]]+)\]\([^)\s]+\)", r"\1", heading)
    text = re.sub(r"[^\w\- ]", "", text.replace("`", "").replace("*", "").lower(), flags=re.ASCII)
    base = text.strip().replace(" ", "-") or "section"
    name, n = base, 0
    while name in seen:
        n += 1
        name = "%s-%d" % (base, n)
    seen.add(name)
    return name


IMAGE_RE = re.compile(r"^!\[([^\]]+)\]\(([^)\s]+)\)$")


def plain_image(caption, url, link_fix):
    """A picture without its size, for a caller that draws none of its own."""
    return '<p><img src="%s" alt="[%s]"><br>\n%s</p>' % (esc(link_fix(url)), esc(caption), esc(caption))


def to_html(md, link_fix=lambda u: u, image=None):
    """md as HTML 3.2. link_fix(url) gives each link's address; image(caption, url) the HTML of a picture on a
    line of its own (plain_image when None)."""
    image = image or (lambda caption, url: plain_image(caption, url, link_fix))
    lines = md.split("\n")
    out, i = [], 0
    para = []
    seen = set()

    def flush():
        if para:
            out.append("<p>%s</p>" % inline(" ".join(para), link_fix))
            para.clear()
    while i < len(lines):
        l = lines[i]
        s = l.strip()
        if s.startswith("```"):
            flush()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(esc(lines[i]))
                i += 1
            out.append("<pre>%s</pre>" % "\n".join(code))
        elif IMAGE_RE.match(s):
            flush()
            out.append(image(*IMAGE_RE.match(s).groups()))
        elif re.match(r"^#{1,6} ", s):
            flush()
            n = len(s) - len(s.lstrip("#"))
            name = slug(s[n + 1:], seen)
            text = inline(s[n + 1:], link_fix)
            # HTML 3.2 anchors may not nest: a heading that holds a link gets an empty anchor before it
            size = ' size="%d"' % HEAD_SIZES[n] if n in HEAD_SIZES else ""
            text = '<font face="%s"%s color="%s">%s</font>' % (HEAD_FACE, size, DEEP, text)
            anchor = ('<a name="%s"></a>%s' if "<a " in text else '<a name="%s">%s</a>') % (name, text)
            out.append("<h%d>%s</h%d>" % (n, anchor, n))
        elif s.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.match(r"^:?-+:?$", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            i -= 1
            html = ['<table border="0" cellpadding="4" cellspacing="0">']
            for r, cells in enumerate(rows):
                tag = "th" if r == 0 else "td"
                colour = SAGE if r == 0 else IVORY if r % 2 == 0 else None
                html.append('<tr%s valign="top">%s</tr>' % (
                    ' bgcolor="%s"' % colour if colour else "",
                    "".join("<%s%s>%s</%s>" % (tag, ' align="left"' if tag == "th" else "", inline(c, link_fix), tag)
                            for c in cells)))
            html.append("</table>")
            out.append("\n".join(html))
        elif re.match(r"^(- |\d+\. )", s):
            flush()
            ordered = bool(re.match(r"^\d+\. ", s))
            items = []
            while i < len(lines) and (re.match(r"^\s*(- |\d+\. )", lines[i]) or
                                      (lines[i].startswith("  ") and lines[i].strip() and items)):
                t = lines[i].strip()
                if re.match(r"^(- |\d+\. )", t):
                    items.append(re.sub(r"^(- |\d+\. )", "", t))
                else:
                    items[-1] += " " + t
                i += 1
            i -= 1
            tag = "ol" if ordered else "ul"
            out.append("<%s>\n%s\n</%s>" % (tag, "\n".join("<li>%s</li>" % inline(t, link_fix) for t in items), tag))
        elif not s:
            flush()
        else:
            para.append(s)
        i += 1
    flush()
    return "\n".join(out)
