"""The ParlorNet rulebook page of the docs site (parlornet-rulebook.html) and its downloads: ParlorNet's own
rulebook for GameMaster 1.0.0, which signs a Mac in to a parlor and carries its games over one connection.
Its source, its build and its releases are in the rulebooks' own repository (eric-cgn/parlornet-gamemaster,
rulebooks/parlornet). It has no download of its own: it comes in Install ParlorNet Rulebooks
(docsite/gamerulebooks.py). The README comes from that repository's release pinned in docsite/downloads.json
(docsite/downloads.py)."""
from adapters import clients as C
from adapters.webstyle import a, font, h1, h2, p
from . import downloads

PAGE = "/parlornet-rulebook.html"
SLUG = "parlornet-rulebook"
README = "parlornet-rulebook-readme.txt"
SOURCE = "%s/tree/main/rulebooks/parlornet" % downloads.repo_url(downloads.GAMEMASTER_REPO)


def files():
    """{site path: bytes} of the README when it was fetched."""
    return downloads.files(README)


def body():
    """parlornet-rulebook.html: what the rulebook does, what it needs, how to use it, how it works, and the
    downloads with sizes and checksums."""
    parts = [("need", "What you need"), ("use", "Using it"), ("how", "How it works"),
             ("download", "Download"), ("licence", "License")]
    steps = ["Install it with %s, which puts it and the game rulebooks in the folder that holds GameMaster."
             % a("/gamemaster-rulebooks.html#download", "Install ParlorNet Rulebooks"),
             "Open GameMaster and choose File &gt; New Game &gt; ParlorNet.",
             "The Server field holds the parlor&#39;s name and the Port field its GameMaster port, "
             "<tt>6508</tt>. Type your name on that parlor and click <b>Sign in</b>. If the parlor asks for a "
             "password or a code, type it and click <b>OK</b>.",
             "The window has three tabs. On <b>New game</b>, choose a game, then pick the opponent from the "
             "Opponent menu: <b>Bot</b>, the first choice; <b>Wait for opponent</b>, for whoever joins your "
             "table; one of the players online; or <b>Other...</b>, which asks for the name of any player, "
             "online or not. The button beside it says what you will play: click <b>Othello vs the bot</b>, "
             "for example. GameMaster opens the game, connected, and you move first.",
             "When another player invites you, the New game tab says so: <b>Accept</b> opens the game and "
             "<b>Decline</b> turns the invitation down.",
             "<b>Your games</b> lists the games you are playing, wherever you started them, with how many on "
             "the tab. <b>Resume</b> opens one of ParlorNet&#39;s own games in GameMaster where it stands, as "
             "the parlor keeps it. Othello, Dots&amp;Boxes and BattleShips cannot be taken up half way in "
             "GameMaster, so play those on the web or by telnet.",
             "<b>All games</b> lists the tables where someone waits for a player, with <b>Join</b>, and the "
             "games other players are playing.",
             "<b>Sign out</b> ends the sign-in and the games started through it."]
    return (
        h1("ParlorNet rulebook for GameMaster") +
        p("A rulebook for GameMaster 1.0.0, the Macintosh game program of 1991, that connects your Mac to a "
          "ParlorNet parlor. You put it in the folder with GameMaster like any other rulebook and choose File "
          "&gt; New Game &gt; ParlorNet. Its window signs you in, starts Othello, Dots&amp;Boxes, "
          "BattleShips or one of ParlorNet&#39;s own games against the bot or another player, takes "
          "invitations, and takes up the games you are already playing. Each game opens in "
          "GameMaster&#39;s own window, already connected, "
          "and two or more games can run at once. A parlor plays GameMaster only through this rulebook, which "
          "carries GameMaster&#39;s own Othello, Dots&amp;Boxes and BattleShips rulebooks as well as "
          "ParlorNet&#39;s six. GameMaster and its rulebooks are not included; the %s says "
          "where to find them." % a(C.BY_ID["gamemaster"].href(), "GameMaster page")) +
        p(" | ".join(a("#" + n, t) for n, t in parts), 2) +
        h2("need", "What you need") +
        p("A Mac Plus or later with System 6.0 or later, MacTCP or Open Transport&#39;s TCP/IP, and GameMaster "
          "1.0.0 with the rulebooks of the games you play: its own Othello, Dots&amp;Boxes and BattleShips, and "
          "ParlorNet&#39;s %s. The rulebook works only with " % a("/gamemaster-rulebooks.html", "game rulebooks") +
          "GameMaster 1.0.0 as it was released. It checks GameMaster&#39;s code first, and if the copy "
          "differs, the lobby still opens but no game starts from it.") +
        h2("use", "Using it") +
        "<ol>\n%s</ol>\n" % "".join("<li>%s\n" % font(s) for s in steps) +
        p("Keep the ParlorNet window open while you play. Closing it, or signing out, ends the games it "
          "started. A game you took up with Resume stays at its table instead, and you can take it up again.") +
        h2("how", "How it works") +
        p("GameMaster sends all of its network traffic through one small part of its code, and the rest of "
          "the program reaches that part through a table of addresses in memory. While the ParlorNet window "
          "is open, the rulebook points some of those addresses at its own code. The games it starts then "
          "travel over the window&#39;s single connection to the parlor, and every other connection still "
          "goes to GameMaster&#39;s own code, so calls you make yourself with Game &gt; Connect work as "
          "before. Nothing is written to GameMaster or to any file, and closing the window puts the table "
          "back.") +
        p("The parlor relays the sign-in to its own accounts, and a game that comes over your signed-in "
          "connection is yours. Neither your Mac&#39;s address nor its name counts.") +
        h2("download", "Download") +
        p("The ParlorNet rulebook comes in %s, with ParlorNet&#39;s game rulebooks."
          % a("/gamemaster-rulebooks.html#download", "Install ParlorNet Rulebooks")) +
        h2("licence", "License") +
        p("The ParlorNet rulebook is free software under the MIT license; the full text is in the %s. Its "
          "source is in %s of %s." % (downloads.readme_link(README), a(SOURCE, "rulebooks/parlornet"),
                                      a(downloads.repo_url(downloads.GAMEMASTER_REPO), downloads.GAMEMASTER_REPO))) +
        p("GameMaster is by Rhys Hollow, with Peter N. Lewis and Quinn. This rulebook is not affiliated with "
          "them and contains none of GameMaster."))
