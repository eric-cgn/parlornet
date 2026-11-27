"""The frontends a parlor can run, with their ports (docs/adapters.md), and the
bot (docs/bot.md), a client of the core with no port of its own.

The admin panel stores which ones run (setting adapters.enabled); the
container's start script (deploy/start.py) reads it and starts those. With
nothing stored, PARLOR_ADAPTERS chooses them, and without that every installed
one runs (chosen). An entry whose module is not in this copy of ParlorNet is
shown as not installed and never started.
"""
import importlib.util
import os

# name, module, port (None: it listens on none), what it is
ADAPTERS = [
    ("web", "adapters.web", 6500, "HTTP: pages for any browser"),
    ("telnet", "adapters.telnet", 6502, "telnet: menus and boards"),
    ("ios", "adapters.ios", 6503, "Reversi for IOS clients"),
    ("fibs", "adapters.fibs", 6505, "Backgammon for FIBS clients"),
    ("outland", "adapters.outland", 4000, "Backgammon and Reversi for the Outland Gateway (host port 6506)"),
    ("doom", "adapters.doom", 6507, "Grid Words for DOoM clients (BobGrid, telnet)"),
    ("gamemaster", "adapters.gamemaster", 6508, "Othello, Dots, BattleShips and more for GameMaster (Mac)"),
    ("admin", "adapters.admin", 6510, "admin menus (this screen)"),
    ("netsprocket", "adapters.netsprocket", 6512, "Dots and Boxes for NetBoxes (Mac, NetSprocket)"),
    ("awale", "adapters.awale", 6515, "Mancala for Awale (Mac, over IRC)"),
    ("play", "adapters.playserver", 6513, "ParlorNet Play: today's browsers and phones (HTTPS proxy)"),
    ("batnav", "adapters.batnav", 6514, "Naval Combat for batnav clients (X11, ncurses, Windows 3.x)"),
    ("networdz", "adapters.networdz", 6516, "Grid Words for Networdz 2.0 (Windows), which hosts"),
    ("netluff", "adapters.netluff", 6517, "Five in a Row for NetLuff (Mac; host port 1744 too)"),
    ("icds", "adapters.icds", 6518, "Checkers for xcheckers (X11) and ICDS clients"),
    ("worthy", "adapters.worthy", 6519, "Reversi, Checkers, Five in a Row for Worthy Opponent (DOS, modem)"),
    ("rscards", "adapters.rscards", 6520, "All ten games for GEnie's RSCARDS (modem or serial line)"),
    ("dialin", "adapters.dialin", 6521, "modem calls from dial-in gateways: ParlorNet clients, telnet"),
    ("bot", "bot", None, "the bot: a player anyone can invite"),
]
NAMES = [a[0] for a in ADAPTERS]
# Ports published under another number than the one the extension listens on: the port players connect to,
# which the extension declares to the core (--public-port; adapters/declare.py).
PUBLISHED = {"outland": 6506, "netluff": 1744}       # NetLuff always connects to 1744 (adapters/netluff.py)


def installed(module):
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def default_enabled():
    return [name for name, module, _, _ in ADAPTERS if installed(module)]


def chosen(stored):
    """The ones the container starts: the admin menus' list (stored, None when they saved none), else
    PARLOR_ADAPTERS, else every installed one. The start script and the admin menus' Adapters screen both
    use it, so the screen shows what runs."""
    if stored is None:
        stored = os.environ.get("PARLOR_ADAPTERS") or ",".join(default_enabled())
    return parse(stored)


def parse(value):
    """A stored or environment list ('web,telnet,admin') -> known names, in catalog order."""
    want = set(x.strip().lower() for x in (value or "").split(",") if x.strip())
    return [n for n in NAMES if n in want]
