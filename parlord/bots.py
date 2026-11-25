"""Bot players (docs/bot.md): the accounts the bot signs in as, and the rule
that only the bot's own server extension can sign in as them.

The bot (bot/, its own process) reaches the core on the extension channel with
its own key, named "bot". A connection holding that key may sign in, with
the name alone, as one of the bot names below and as nothing else; every other
connection is refused those names, whatever the sign-in method (under sign-in
none too). So no person can sign in as a bot, and the bot holds no credential
but its key. The account is made at the bot's first sign-in and flagged as a
bot (the meta entry bot:<name>), which LST and VUE show.

An account that had one of these names before they were reserved stays its
owner's: it signs in as before, and the bot does not use that name.
"""

KEY_NAME = "bot"                            # the extension key whose connections act as bots
NAMES = ("bot", "bot1", "bot2", "bot3")     # one seat each at a table: bot, then bot1 to bot3


def is_name(name):
    return (name or "").lower() in NAMES


def flag_key(name):
    return "bot:" + name.lower()


def is_bot(player):
    return bool(player is not None and getattr(player, "bot", False))


def load(accounts):
    """Mark the bot accounts among the players loaded from the database."""
    for n in NAMES:
        p = accounts.players.get(n)
        if p is not None and accounts.store.get(flag_key(n)) == "1":
            p.bot = True


def begin(accounts, conn, name):
    """HELO, USER or REG before the sign-in method sees it: None when the bot
    rules have nothing to say (the method goes on as usual), else the result:
    ("ok", player, None) for the bot's own connection signing in as a bot
    name, ("err", ...) for anything else that touches a bot name or comes
    from the bot's key."""
    bot_conn = getattr(conn, "frontend", None) == KEY_NAME
    if not is_name(name):
        return ("err", "RESERVED", "the bot's key signs in only as a bot") if bot_conn else None
    key = name.lower()
    p = accounts.players.get(key)
    if p is not None and not is_bot(p):
        # a person's account from before the names were reserved: theirs, as it was
        return ("err", "RESERVED", "%s is a person's name here" % key) if bot_conn else None
    if not bot_conn:
        return ("err", "RESERVED", "%s is a bot here; invite it to a table (INV)" % key)
    if p is None:
        p = accounts.player(key, create=True)
        p.bot = True
        accounts.store.set(flag_key(key), "1")
    return ("ok", p, None)


def seats(players, names):
    """The seats held by bots, from a table's seat names (None for an empty seat)."""
    return [i for i, n in enumerate(names) if n and is_bot(players.get(n.lower()))]
