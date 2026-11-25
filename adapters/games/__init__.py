"""Per-game frontend code, one module per game type, found by this registry.

Each module here (adapters/games/<game>.py) holds everything the web site and
telnet need for one game type: its view parser, its event texts, its web board
and images, its telnet screens and its Games page text. web.py and telnet.py
look games up here, so adding a module (with its plugin in plugins/<game>/)
makes the game appear in the web lobby, the telnet menu, the Games table and
/games.htm without editing them. docs/games-howto.md says what a module must
define; Reversi (reversi.py) is the worked example.

Modules whose names start with "_" are helpers, not games. Discovery is lazy
(on first use), because the game modules import adapters.telnet and
adapters.web, which import this package.
"""
import importlib
import pkgutil
import re
import sys
from collections.abc import Mapping

# What every game module must define (docs/games-howto.md has the details).
REQUIRED = {
    "KEY": "the game type and version, as in GAM: 'reversi/1'",
    "NAME": "the display name: 'Reversi'",
    "ORDER": "its place in the build order (README.md): menus and tables list games by it",
    "PLAYERS": "players as text: '2' or '2-4'",
    "ABOUT": "(about, [rules]) for the Games page, plain text",
    "parse_view": "VUE lines -> dict, with rows truthy once the game has started",
    "describe_event": "(event text, seat names) -> a line for the log, or None if not this game's event",
    "web_parts": "(v, tid, link, seat, names, sel) -> (board, side, hint, actions) HTML 3.2",
    "images": "() -> {'/img/<game>/name.gif': GIF bytes} (the tiles board_image draws with; may be empty)",
    "MOVE_RE": "compiled regex: the move words the web page may send (/move.htm mv=)",
    "SEL_RE": "compiled regex for /table.htm sel= (a picked piece), or None",
    "AnsiScreen": "the full-screen telnet class (a subclass of telnet.AnsiGame)",
    "PlainScreen": "the plain telnet class (a subclass of telnet.PlainGame)",
}

# Optional: board_image(v, seat, sel) -> adapters.board.Canvas, the board as one image with its clickable
# areas (web_parts shows it with web.board_html); board_click(v, seat, sel, x, y) -> (path, {params}) or
# None, for a board clickable anywhere (Canvas.anywhere); web_events(v, events), what the board shows from
# the table's events; MOVE_FIELDS with move_from_fields (MOVE_FIELDS_VIEW: it is given the table's parsed view
# too, and may raise common.Refused with a reason for the player); refusal(move, reason) -> the text a player
# reads when the core refuses a move; WEB_CHECK; RULE_NAMES. (Period game clients for a game are listed in
# adapters/clients.py, not here.)

# Query names the web site uses itself, which a game's MOVE_FIELDS may not take.
# the web's own query and form fields: the session (s), the action check (x), the one-time handle after
# sign-in (h), the sign-in form (n, k, lid, f, v), the board image's keys (q, st, d), and the pages' own
RESERVED_FIELDS = {"s", "x", "h", "n", "k", "t", "r", "js", "w", "mv", "sel", "g", "ru", "np", "turn", "vis", "who",
                   "sure", "new", "lid", "f", "v", "all", "note", "q", "st", "d"}
FIELD_NAME_RE = re.compile(r"^[a-z]{1,8}\Z")

_games = None       # KEY -> module, in ORDER; None until discovered
_problems = {}      # module name -> what is missing or wrong


def problems(mod):
    """What a game module lacks or gets wrong (an empty list if nothing)."""
    out = ["%s: missing (%s)" % (k, why) for k, why in REQUIRED.items() if not hasattr(mod, k)]
    if out:
        return out
    if not isinstance(mod.KEY, str) or mod.KEY.count("/") != 1:
        out.append("KEY: %r is not type/version" % (mod.KEY,))
    if not isinstance(mod.ABOUT, tuple) or len(mod.ABOUT) != 2 or not isinstance(mod.ABOUT[1], list):
        out.append("ABOUT: must be (about, [rules])")
    fields = getattr(mod, "MOVE_FIELDS", None)
    if fields is not None:
        if (not isinstance(fields, (list, tuple)) or not 1 <= len(fields) <= 6 or len(set(fields)) != len(fields)
                or not all(isinstance(f, str) and FIELD_NAME_RE.match(f) and f not in RESERVED_FIELDS
                           for f in fields)):
            out.append("MOVE_FIELDS: 1 to 6 different names of 1 to 8 letters a-z, none of %s"
                       % " ".join(sorted(RESERVED_FIELDS)))
        if not callable(getattr(mod, "move_from_fields", None)):
            out.append("move_from_fields: missing (MOVE_FIELDS needs it: [values] -> a move, or None)")
    return out


def _discover():
    global _games
    if _games is None:
        _problems.clear()
        found = []
        for info in pkgutil.iter_modules(__path__):
            if info.name.startswith("_"):
                continue
            name = "%s.%s" % (__name__, info.name)
            try:
                mod = importlib.import_module(name)
            except Exception as e:        # one broken game must not take the others down
                _problems[info.name] = ["import failed: %r" % e]
                print("games: %s not loaded: %r" % (info.name, e), file=sys.stderr, flush=True)
                continue
            bad = problems(mod)
            if bad:
                _problems[info.name] = bad
                print("games: %s not loaded: %s" % (info.name, "; ".join(bad)), file=sys.stderr, flush=True)
                continue
            found.append(mod)
        found.sort(key=lambda m: (m.ORDER, m.KEY))
        _games = {}
        for m in found:
            if m.KEY in _games:
                _problems[m.__name__] = ["KEY %s is already %s" % (m.KEY, _games[m.KEY].__name__)]
                continue
            _games[m.KEY] = m
    return _games


def reload():
    """Forget what was found, so the next lookup looks again (for tests that add a module)."""
    global _games
    _games = None
    _images.clear()
    importlib.invalidate_caches()


def all_games():
    """Every game module, in ORDER."""
    return list(_discover().values())


def get(key):
    """The module for a game type ('reversi/1'), or None."""
    return _discover().get(key)


def by_type():
    """{type word ('reversi'): module}, in ORDER."""
    return {k.split("/")[0]: m for k, m in _discover().items()}


def rules_name(key, rules):
    """A ruleset's display name for the menus ('Store rules' for mancala/1 kalah): the module's optional
    RULE_NAMES {ruleset: name}, else the ruleset word with a capital."""
    m = get(key)
    name = (getattr(m, "RULE_NAMES", None) or {}).get(rules) or rules
    return name[:1].upper() + name[1:]


def load_problems():
    """{module name: [problems]} for modules that were skipped, after discovery."""
    _discover()
    return dict(_problems)


class Lookup(Mapping):
    """A read-only {KEY: make(module)} view of the registry, worked out when used; web.py's GAME_PAGES and
    telnet.py's RENDERERS are two of these."""

    def __init__(self, make):
        self.make = make

    def __getitem__(self, key):
        m = get(key)
        if m is None:
            raise KeyError(key)
        return self.make(m)

    def __iter__(self):
        return iter(list(_discover()))

    def __len__(self):
        return len(_discover())


_images = {}


class Images(Mapping):
    """{path: GIF bytes} for every game's images, made on first use."""

    def _all(self):
        if not _images:
            for m in all_games():
                _images.update(m.images())
        return _images

    def __getitem__(self, path):
        return self._all()[path]

    def __iter__(self):
        return iter(self._all())

    def __len__(self):
        return len(self._all())

    def __contains__(self, path):
        return isinstance(path, str) and path.startswith("/img/") and path in self._all()
