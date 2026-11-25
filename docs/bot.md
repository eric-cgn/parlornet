# The bot

The bot is a player anyone can invite to a table, so a player who is alone on a server can still play every game. It plays one simple strategy per game: good enough to beat a player who moves at random, easy enough to beat with some thought.

## What it is

The bot is a program of its own (`bot/`), a client of the core like the web or telnet server extensions. It reaches the core on the extension channel (TLS 1.3) with its own key, named `bot`, and signs in with a bot name alone:

- **Its names**: `bot`, `bot1`, `bot2` and `bot3`. Each name is one seat, so a table of four can have three bots. The core reserves these names: nobody can sign up or sign in as one of them, under any sign-in method, from any frontend (`ERR RESERVED`). Only a connection that holds the bot's key can sign in as a bot, and that key can sign in as nothing else. The account is made at the bot's first sign-in and flagged as a bot in the core (parlord/bots.py).
- **The flag**: `LST` adds `bot=1,2` (the seats held by bots) to a table's `TAB` line, and `VUE` adds a line `BOT 1 2` once the game runs. The web lobby shows a bot as `bot (bot)`.
- **An old account**: if someone had an account named `bot` (or `bot1` to `bot3`) before the names were reserved, it stays theirs and signs in as before; the bot does not use that name.

The bot uses the extension channel rather than the public port because there it needs no password, so it holds no credential but its key; the key is enrolled, rotated and revoked like every server extension's (`python -m parlord.frontends`), and rotating it ends the bot's sessions; and the core can tie the bot names to that one key, so no person can sign in as a bot even on a server with sign-in `none`.

## Running it

**In the container.** The bot is in the admin menus' Adapters list (`bot`, no port) and on by default. `deploy/start.py` enrolls its key at the first start (`/data/frontends/bot.key`) and starts it with the others; a change takes effect when the container restarts. Under the opt-in user per process, it runs as `pn-bot` (uid 10050) and reads only its own key.

**On its own**, against any ParlorNet server whose operator gave you a key named `bot`:

    python -m parlord.frontends --keys /data/frontends add bot      # on the server
    python3.13 -m bot --core HOST:6590 --key bot.key                  # where the bot runs

Options: `--names` (the bot names to play as, default all four), `--cap` (tables at once for each name, default 20), `--think` (seconds before each turn, default `0.5,2`), `--pace` (least seconds between two requests on one connection, default 0.12), `--poll` (seconds between looks for invitations, default 2), `--gridwords-secs` (Grid Words search time a turn, default 1).

## How it plays a table

- **It joins only when invited.** Every bot name looks at its invitations (`IVL`) every 2 seconds and sits down (`JON`) at each table it is invited to. It never sits down uninvited, never invites anyone, and never opens a table.
- **Talk**: one line when it sits down ("Hello! I am the bot, a simple player. Good luck!") and "Good game!" at the end.
- **Pace**: a pause of 0.5 to 2 seconds before each turn, well inside any turn clock (at least a minute). It waits for the other players with `WAT`, never by polling quickly, and keeps under the core's rate limit of 10 requests a second on each connection.
- **Load**: at most 20 tables at once for each bot name; an invitation over that is declined (`UNV`). The server's own limits for players (games played at once, open tables waited at, players signed in) do not apply to bots.
- **Leaving**: it stops playing a table when the game is over (by its end, a resignation, the clock or the operator), when the people at an open table have all stood up (it stands up too, so the table goes), or when its seat has left a game the others play on. If the people go away during a game, their clocks run out and the game ends.
- **Resigning**: it never resigns, and plays every game to its end. It accepts a resignation offered in Backgammon and answers draw offers in Checkers.
- **After a restart** it finds the tables where its names are seated (`LST`) and goes on playing them.
- **Memory**: one process for all four names. The Grid Words word list is read once, when the first Grid Words turn needs it, into the plugin's own compact Lexicon (about 1.5 MB). See Memory below.

## Each game's strategy

The bot rebuilds the game's state from its view and runs the game's own rules code (each plugin is a set of pure functions, `bot/rules.py`) to look ahead. The server stays the judge: every move goes through `MOV`, and Grid Words plays are checked with `CHK` first.

| Game | Strategy |
|---|---|
| Reversi | A corner if one is free; never a square next to an empty corner unless nothing else is left; otherwise the move that flips the most discs. |
| Backgammon | Every way to play the roll is tried and the positions scored: checkers hit, points made (more in its home board), blots left within reach of the other side, checkers borne off, its pip count. It never doubles; it takes a double when its pip count is at most about 12% behind the doubler's, else it drops. |
| Checkers | Every whole turn is tried (captures are compulsory and run to their end): the most pieces taken, then the least the other side can take back, then crowning, then keeping its back row. It accepts a draw offer when it is not ahead. |
| Five in a Row | A winning point; else a point that blocks the other player's win; else the point that best builds its own lines and blocks theirs (an open three of theirs weighs nearly as much as an open four of its own). |
| Dots and Boxes | A line that closes a box when there is one; else a line that draws no box's third side; else the line that hands over the fewest boxes. |
| Mancala | A move that earns another turn (kalah); then the move that gains its store the most (a capture counts in full) less what the other side could capture straight after; then the move that keeps the most seeds on its own side. It plays only the moves the server lists, so oware's must-feed rule holds. |
| Naval Combat | The fleet placed at random by the server. Shooting: next to its hits on a ship not yet sunk, along the line once two hits line up; otherwise at random on a checkerboard of the squares not yet fired at (in batnav, not beside a sunk ship). |
| Grid Words | The highest-scoring play it finds in about a second: every row and column from each square that can reach a tile on the board, rack letters placed left to right, a branch dropped as soon as no word in the list starts that way or a cross word would not be in the list; each play is scored by the game's rules (a blank costs a little) and confirmed with `CHK`. With nothing found it exchanges its rack but blanks and S while the bag holds 7 tiles, else passes. |
| Cannon Fire | It aims at the nearest tank still standing, trying angles every 5 degrees and powers every 5 with the game's own flight and blast rules, and fires the shot that hurts other tanks most and itself least, with a small random error (up to 2 degrees, 3 points of power) so it can be beaten. It never drives. |
| Rummy Tile Classic | It plays from its own rack only and never rearranges the table: the combination of runs and groups (a joker filling a gap) that puts down the most tiles, before its first meld only once they reach 30 points; after the first meld it also lays tiles off onto the table's sets. Then `END`, which draws when it played nothing. |

## Inviting it

- **Web**: in the lobby, check **Play the bot** beside Start a game: the table is made, you sit down and a bot is invited to every other seat. At an open table of yours, **Invite the bot** invites one more.
- **Telnet**: `new reversi bot` (any game, players and time as usual: `new rummytile 4p 1h bot`) makes the table with bots in the other seats. At an open table of yours, type `bot`; the waiting box says so.
- **Native protocol**: `INV table bot` (then `bot1`, `bot2`, `bot3` for more seats). The bot sits down within a few seconds.
- **IOS**: In Reversi The Eclipse the bot appears in the player list. Pick it and press Match. From a terminal, type `match bot`. Either way the server makes a table and invites the bot to the other seat.
- **FIBS**: The bot appears in `who` and `rawwho` and is always ready. Type `invite bot 1`, or give another match length or `unlimited`.
- **Outland**: The bot appears in the lobby list and is always available. Challenge it, and it accepts when it sits down.
- **DOoM**: `invite bot`, and the bot joins you on a new board.
- **GameMaster**: in the ParlorNet rulebook's window, choose Bot in the Opponent menu.
- **NetBoxes, batnav, NetLuff and Worthy Opponent**: `/invite bot` in the chat box (Worthy Opponent's Talk window); in Awale, `!invite bot` (chat-commands.md).
- **RSCARDS**: click Bot beside a game in the lobby; at an open table of yours, click Invite the bot or type `/invite bot` on the bottom line.
- **ParlorNet Play**: check **Play the bot** when starting a table, or press **Invite the bot** at an open table of yours.

The bot shows up in these lists while it runs. The core counts it as online for as long as its process is connected, as docs/accounts.md explains under "Who is online".

## Memory

The container has a 448 MB limit (256 MB at first, 320 MB once the bot came, 448 MB with sixteen server extensions). Measured in `python:3.13-slim` (arm64) with a core and a random player at every game: the bot idles at 29 MB resident (17 MB anonymous, 12 MB shared library and file pages); after 30 games, ten at a time, Grid Words among them, it holds 36 MB (24 MB anonymous). Most of that is the floor of any Python process that speaks TLS here: Python alone is 8 MB, and importing asyncio (with ssl and OpenSSL) brings it to 24 MB, as in the core and every server extension. The bot's own data is small: the word list's 1.5 MB, and at most about 2.5 MB during a Grid Words search (it remembers at most 20,000 prefixes and keeps only its best few plays).

## Tests

`tests/test_bot.py` checks that no person signs in as a bot under any sign-in method and that the bot's key signs in as nothing else; that each strategy beats a random player (seeded, both seats); a whole game of every game against a random player over a real server, with the bot's greeting and last words and no move refused; three bots at a table of four (Rummy Tile Classic, Cannon Fire); the invitations from the web, telnet and the native protocol; that it never joins uninvited, follows the people out of an open table and leaves when a turn clock ends the game; and its memory.
