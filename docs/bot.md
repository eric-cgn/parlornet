# The bot

The bot is a player anyone can invite to a table, so a player who is alone on a server can still play every game. It also keeps an open table of every two-player game, so a player can sit down and start a game at once. It plays one simple strategy per game: good enough to beat a player who moves at random, easy enough to beat with some thought.

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

Options: `--names` (the bot names to play as, default all four), `--cap` (tables at once for each name, default 20), `--no-open-tables` (keep no open tables, below), `--think` (seconds before each turn, default `0.5,2`), `--pace` (least seconds between two requests on one connection, default 0.12), `--poll` (seconds between looks for invitations, default 2), `--gridwords-secs` (Grid Words search time a turn, default 1).

## How it plays a table

- **It joins other tables only when invited.** Every bot name looks at its invitations (`IVL`) every 2 seconds and sits down (`JON`) at each table it is invited to. It never sits down at another player's table uninvited, and it never invites anyone. The only tables it opens are its open tables (below).
- **Talk**: one line when it sits down ("Hello! I am the bot, a simple player. Good luck!") and "Good game!" at the end.
- **Pace**: a pause of 0.5 to 2 seconds before each turn, well inside any turn clock (at least a minute). It waits for the other players with `WAT`, never by polling quickly, and keeps under the core's rate limit of 10 requests a second on each connection.
- **Load**: at most 20 tables at once for each bot name; an invitation over that is declined (`UNV`). Its open tables don't count while they wait for a player. The server's own limits for players (games played at once, open tables waited at, players signed in) do not apply to bots.
- **Leaving**: it stops playing a table when the game is over (by its end, a resignation, the clock or the operator), when the people at an open table have all stood up (it stands up too, so the table goes), or when its seat has left a game the others play on. If the people go away during a game, their clocks run out and the game ends.
- **Resigning**: it never resigns, and plays every game to its end. It accepts a resignation offered in Backgammon and answers draw offers in Checkers.
- **After a restart** it finds the tables where its names are seated (`LST`) and goes on playing them. It sits back down at its open tables to wait, as Open tables below says.
- **Memory**: one process for all four names. The Grid Words word list is read once, when the first Grid Words turn needs it, into the plugin's own compact Lexicon (about 1.5 MB). See Memory below.

## Open tables

The bot keeps one open table of every two-player game the server offers: Reversi, Backgammon, Checkers, Five in a Row, Mancala and Naval Combat. A player who wants a game against the bot can sit down at one and start at once, without choosing anything. Games for more than two players get no open table, and players invite the bot to those as before.

- **The table.** The bot makes it with `NEW type rules` and nothing more, so it has the game's default rules (the first ruleset `GAM` lists), the server's default time per turn (`server.turn_default`, a day unless the operator changed it) and two seats. It is public. The bot keeps the table data `bot open` with it (`TDS`, docs/protocol.md) and sits in seat 0.
- **A player sits down.** The table has two seats, so the game starts when a player sits down. The bot takes the mark off (`TDD`), greets the player and plays the game as at any other table. At the same moment it opens the next table of that game, so a game has one open table at all times and never two. When two players try to sit at one table together, the core seats one of them. The other gets `ERR CLOSED` and can sit at the new table a moment later.
- **Keeping the count.** The bot looks at every table (`LST all`) every 2 seconds (`--poll`), and at once when a player sits at one of its open tables or one goes away. One of its open tables is an open public table that carries the mark and where only bots sit. Each look opens a table for every game that has none. When the operator turns a game off in the admin menus (`games.off`), the bot stands up from that game's table, and the table goes. When a game is turned on, its table appears with the next look. When the operator ends an open table, or the server removes one because nothing happened there for `games.open_hours`, the next look opens a new one. If a look finds two tables of one game, it keeps the older one and stands up from the other. Only a bot stopped halfway through making a table can leave two.
- **Restarts.** The open tables are kept in the core's database like any other table, so they stay through a restart of the core or the bot. The bot finds them by their mark and sits back down to wait, without opening new ones. A marked table nobody sits at yet, which a bot stopped between `NEW` and `JON` leaves, is taken up the same way.
- **Several bot names.** The name `bot` sits at every open table while it plays fewer than `--cap` games, and `bot1`, then `bot2` and `bot3`, take over after that. Only the bot process that plays the name `bot` keeps open tables. A second bot process with other names (`--names bot2,bot3`) never opens one, so two processes can't open a table of the same game twice. `--no-open-tables` turns open tables off, and the bot then stands up from any it finds after a restart.

The lobbies list them with the other open tables, as tables where the bot waits for a player, and each one is joined in one step. They come after the tables where a person waits and before the games being played, so they never push a person's table out of sight. The lobby doesn't mention the bot otherwise (docs/design.md, The lobby).

- **Web and ParlorNet Play**: a row for each, with "bot (bot)" in its seat and a Join link or button. Joining sits you down, and the game starts.
- **Telnet**: a line under Other tables with `/join` and the table's number in the plain lobby, and a row under Other tables in the menu lobby, where Enter sits you down.
- **ParlorNet for 8-bit computers**: a line under "Waiting for a player", with a Join button.
- **Chat consoles** (the shared chat commands of docs/chat-commands.md, and the command lines of the web, telnet and Play): `/list` shows them with the other open tables, and `/join` with the table's number sits you down. `/join bot` sits you at the bot's open table of the game the console plays. Where the bot waits at tables of several games, it names them with their numbers. A console that plays only some rules of a game, as batnav and Awale do, leaves out the tables it can't play.
- **RSCARDS** leaves them out of its Other tables box, which has three rows, because the Bot button beside each game plays the bot.
- **GameMaster**: the ParlorNet rulebook's All games tab lists them after the tables where a person waits.
- **FIBS**: `join bot` sits you at the bot's Backgammon table.

Inviting the bot works as before. Play the bot, `/invite bot`, `match bot` in IOS and ICDS, a challenge to the bot in Outland and Bot in GameMaster's Opponent menu still make a table of the player's own, with the settings they chose, and the bot sits down there. A command that looks for a table where a named player waits passes over the bot's open tables, and so does GameMaster's "anyone".

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

Inviting the bot makes a table of your own, so you choose the rules, the time per turn and, in a game for more than two, the number of players. Open tables, above, says how to sit down at a table where the bot already waits.

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

`tests/test_bot_open.py` checks the open tables, with a core and the bot in one process, and it watches every change the core saves so it can fail the moment a game has two. It checks one table of every two-player game with the defaults and none for the others, a player sitting down and the next table opening, two players racing for one table, a game turned off and on, a table the operator ends and one the idle limit removes, a restart of the bot and of the core, tables left by a bot stopped halfway, the names sharing the tables, and that `/invite bot` still makes the player's own table. `tests/test_bot.py` checks that no person signs in as a bot under any sign-in method and that the bot's key signs in as nothing else; that each strategy beats a random player (seeded, both seats); a whole game of every game against a random player over a real server, with the bot's greeting and last words and no move refused; three bots at a table of four (Rummy Tile Classic, Cannon Fire); the invitations from the web, telnet and the native protocol; that it never joins uninvited, follows the people out of an open table and leaves when a turn clock ends the game; and its memory.
