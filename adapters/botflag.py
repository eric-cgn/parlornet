"""The bot player for the frontends (docs/bot.md): which seats are bots (the
core flags them: bot=seat,... on LST's TAB lines), and which bot name to invite
next (each bot name is one seat: bot, then bot1 to bot3)."""
from parlord.bots import NAMES, listed  # noqa: F401  (listed: the frontends' player lists)


def bot_seats(lines):
    """LST lines -> {table id: set of the seats held by bots}."""
    out = {}
    for line in lines:
        f = line.split()
        if len(f) >= 7 and f[0] == "TAB":
            seats = next((x[4:] for x in f[7:] if x.startswith("bot=")), "")
            out[f[1]] = {int(s) for s in seats.split(",") if s.isdigit()}
    return out


def bot_names(lines):
    """LST lines -> {table id: set of the names (lower case) of the bots seated there}."""
    out = {}
    for line in lines:
        f = line.split()
        if len(f) >= 7 and f[0] == "TAB":
            names = f[6].split(",")
            seats = bot_seats([line]).get(f[1], set())
            out[f[1]] = {names[i].lower() for i in seats if i < len(names)}
    return out


def next_name(taken):
    """The first bot name not seated or invited at a table (taken: names there), or None."""
    low = {n.lower() for n in taken}
    return next((n for n in NAMES if n not in low), None)


def label(name, bots):
    """A seat's name as a list shows it: 'bot (bot)' for a bot (bots: the bot names there, lower case)."""
    return "%s (bot)" % name if name.lower() in bots else name


def is_bot_name(name):
    """A bot's name (bot, bot1 to bot3)? Only the core knows whether an account by that name is the bot's; a
    frontend uses this for wording only."""
    return (name or "").lower() in NAMES
