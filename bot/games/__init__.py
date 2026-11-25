"""One strategy per game type (docs/bot.md). A module here has:

    KEY                 the game type it plays ("reversi/1")
    smart(table, v)     play one whole turn (async), the bot's simple strategy
    random_turn(t, v)   play one whole turn at random (tests; a fallback when smart's moves are refused)
    my_turn(v, seat)    optional: is there something for seat to do (default: W is seat)
    dropped(v, seat)    optional: has seat left a game the others play on (default: no)

A module is imported when its game is first played."""
import importlib

MODULES = {
    "reversi/1": "reversi", "backgammon/1": "backgammon", "checkers/1": "checkers",
    "fiveinarow/1": "fiveinarow", "dotsandboxes/1": "dotsandboxes", "mancala/1": "mancala",
    "navalcombat/1": "navalcombat", "gridwords/1": "gridwords", "cannonfire/1": "cannonfire",
    "rummytile/1": "rummytile",
}
_cache = {}


def default_my_turn(v, seat):
    return v.turn == seat


def default_dropped(v, seat):
    return False


def get(key):
    """The strategy module for a game type, or None if the bot does not play it."""
    if key not in MODULES:
        return None
    m = _cache.get(key)
    if m is None:
        m = importlib.import_module("bot.games." + MODULES[key])
        if not hasattr(m, "my_turn"):
            m.my_turn = default_my_turn
        if not hasattr(m, "dropped"):
            m.dropped = default_dropped
        _cache[key] = m
    return m


def seat_flags(v, seat, tag="G"):
    """1 in a per-seat flag line (G: seats that left the game) for seat."""
    g = v.one(tag)
    return bool(g and seat < len(g) and g[seat] == "1")
