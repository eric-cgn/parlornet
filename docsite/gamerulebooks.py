"""The game rulebooks page of the docs site (gamemaster-rulebooks.html) and its downloads: ParlorNet's own game
rulebooks for GameMaster 1.0.0 and Install ParlorNet Rulebooks, the installer made with EUP that holds them and
the ParlorNet rulebook. Their source, their build and their releases are in their own repository
(eric-cgn/parlornet-gamemaster); the installer and the README come from its release pinned in
docsite/downloads.json (docsite/downloads.py)."""
import collections

from adapters import clients as C
from adapters.webstyle import a, data_table, font, h1, h2, p
from . import downloads

PAGE = "/gamemaster-rulebooks.html"
README = "gamemaster-rulebooks-readme.txt"
SOURCE = downloads.repo_url(downloads.GAMEMASTER_REPO)


# slug: the rulebook's name in its repository's releases; name: the file on the Mac; rb_id: its GMRZ ID string;
# game: what it plays.
Rulebook = collections.namedtuple("Rulebook", "slug name rb_id game")
RULEBOOKS = [
    Rulebook("checkers-rulebook", "Checkers", "Checkers [ParlorNet]",
             "Checkers, American rules"),
    Rulebook("mancala-rulebook", "Mancala", "Mancala [ParlorNet]",
             "Mancala, Kalah rules with four seeds a pit"),
    Rulebook("fiveinarow-rulebook", "Five in a Row", "Five in a Row [ParlorNet]",
             "Five in a Row on a 15 by 15 board: five or more in a line wins"),
    Rulebook("gridwords-rulebook", "Grid Words", "Grid Words [ParlorNet]",
             "Grid Words, the crossword game, by the classic rules on the standard board; on a parlor only"),
    Rulebook("rummytile-rulebook", "Rummy Tile", "Rummy Tile [ParlorNet]",
             "Rummy Tile, tile rummy for two by the standard rules; on a parlor only"),
    Rulebook("cannonfire-rulebook", "Cannon Fire", "Cannon Fire [ParlorNet]",
             "Cannon Fire, two tanks shelling each other across a landscape; on a parlor only"),
]


def files():
    """{site path: bytes} of the README and the installer when they were fetched."""
    out = downloads.files(README)
    out.update(downloads.program_files(downloads.INSTALLER))
    return out


def body():
    """gamemaster-rulebooks.html: the games, what they need, installing them with the installer,
    playing, and the downloads with sizes and checksums."""
    parts = [("games", "The games"), ("need", "What you need"), ("install", "Installing"), ("play", "Playing"),
             ("download", "Download"), ("licence", "License")]
    rows = [[a("/parlornet-rulebook.html", "ParlorNet"), "the lobby: sign in, start games, take invitations"]]
    rows += [[r.name, r.game] for r in RULEBOOKS]
    installer = ["Download the installer below and open it. Its first screen says what it installs; click "
                 "<b>Continue</b>.",
                 "Install Location names the folder the rulebooks go into. If it is not the folder that holds "
                 "GameMaster, click <b>Choose...</b> and pick that folder. The installer installs only into a "
                 "folder that holds GameMaster.",
                 "Click <b>Install</b>, then <b>Quit</b> when it says the rulebooks are installed."]
    play = ["Open GameMaster and choose File &gt; New Game &gt; ParlorNet. Sign in, choose a game and an "
            "opponent from the Opponent menu, and click the button, for example <b>Checkers vs the bot</b>. "
            "GameMaster opens the game, connected.",
            "A game you are already playing, started on the web, by telnet or in GameMaster, can be taken up "
            "in GameMaster: click <b>Resume</b> beside it on the ParlorNet window&#39;s Your games tab. The "
            "parlor sends the whole position, the board and your rack or tiles, the scores and whose turn it "
            "is, and the game goes on from there.",
            "Click where you move: in Checkers the piece, then each square it lands on; in Mancala one of "
            "your pits, along the bottom row; in Five in a Row a point. In Grid Words click the square the "
            "word starts on (again for down), then the tiles of your rack, or type the letters, and click "
            "<b>Play</b>; <b>Exchange...</b> and <b>Pass</b> are beside the rack. In Rummy Tile select tiles of "
            "your rack or one of the table, then click a set, or <b>New set</b>, <b>Split</b>, <b>Join</b> or "
            "<b>To rack</b>; <b>End turn</b> (or <b>Draw a tile</b>) ends it. In Cannon Fire set the angle and "
            "the power with the buttons or the arrow keys, then <b>Fire</b> (or Return); <b>Drive left</b> and "
            "<b>Drive right</b> move the tank first, once a turn. Your move shows on the board "
            "once the parlor has checked and accepted it; a move it refuses comes back with the reason.",
            "<b>Resign</b> is beside the board, and in Checkers <b>Offer draw</b>. Resign asks for a second "
            "click.",
            "In Checkers, Mancala and Five in a Row, against a friend&#39;s Mac with the same rulebook, start a "
            "new game and use GameMaster&#39;s Game &gt; Connect, as with any rulebook. The other Mac checks "
            "each move the same way."]
    return (
        h1("ParlorNet game rulebooks for GameMaster") +
        p("Rulebooks for GameMaster 1.0.0, the Macintosh game program of 1991, that play ParlorNet&#39;s games. "
          "Each plays on a parlor, against the bot or another player; Checkers, Mancala and Five in a Row also "
          "play between two Macs like any other rulebook. The %s starts the games on a parlor. GameMaster and its own rulebooks are not included; "
          "the %s says where to find them." % (a("/parlornet-rulebook.html", "ParlorNet rulebook"),
                                               a(C.BY_ID["gamemaster"].href(), "GameMaster page"))) +
        p(" | ".join(a("#" + n, t) for n, t in parts), 2) +
        h2("games", "The games") +
        data_table(["Rulebook", "Plays"], rows, "100%") +
        p("Othello, Dots&amp;Boxes and BattleShips play on a parlor with GameMaster&#39;s own rulebooks of "
          "those names, started from the ParlorNet rulebook&#39;s window like the others. Grid Words, Rummy Tile and Cannon Fire play only on a parlor, which "
          "deals the tiles, flies the shells and checks every play.") +
        h2("need", "What you need") +
        p("A Mac Plus or later with System 6.0 or later, and GameMaster 1.0.0. On a parlor, MacTCP or Open "
          "Transport&#39;s TCP/IP and the ParlorNet rulebook. The installer needs System 6.0.3 or later.") +
        h2("install", "Installing") +
        p("The installer puts the ParlorNet rulebook and every game rulebook in the folder that holds "
          "GameMaster. Custom Install picks which.") +
        "<ol>\n%s</ol>\n" % "".join("<li>%s\n" % font(s) for s in installer) +
        h2("play", "Playing") +
        "<ol>\n%s</ol>\n" % "".join("<li>%s\n" % font(s) for s in play) +
        h2("download", "Download") +
        p("<b>Install ParlorNet Rulebooks</b>, all of them in one installer:") +
        downloads.downloads_html(downloads.INSTALLER, README) +
        h2("licence", "License") +
        p("The rulebooks are free software under the MIT license; the full text is in the %s. Their source "
          "is in %s. The installer is made with %s, which carries parts of newlib, Multiversal Interfaces, "
          "and the Retro68 and GCC runtimes; their notices are in EUP&#39;s licenses folder."
          % (downloads.readme_link(README), a(SOURCE, downloads.GAMEMASTER_REPO),
             a("https://github.com/eric-cgn/eup", "EUP"))) +
        p("GameMaster is by Rhys Hollow, with Peter N. Lewis and Quinn. These rulebooks are not affiliated "
          "with them and contain none of GameMaster."))
