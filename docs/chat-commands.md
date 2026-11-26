# Chat commands

Some services give players a chat-style console: a box where they type lines to the server. On these consoles the same commands work the same way, with one meaning and one help text each. Any frontend can also send a command line to the core with the request `CMD`, which runs these commands too ([protocol.md](protocol.md), Commands).

## The commands

| Command | What it does |
|---|---|
| `/list` | your games of this kind, with the table, the opponent, whose move it is and the time left, then the open tables where someone waits for a player |
| `/resume [table]` | brings a running game back, the first one when no table is given |
| `/resign [table]` | resigns the game you are playing, or the table given. It asks first: type `/resign` again within 30 seconds to resign, or `/resign TABLE yes` to resign at once |
| `/invite NAME` | invites a player to a game. The bot is a player too: `/invite bot` plays the bot |
| `/uninvite NAME` | withdraws an invitation to the table you are at |
| `/open` | opens a table anyone can join |
| `/join TABLE` or `/join NAME` | sits you down at an open table, given its number from `/list` or the name of the player waiting there. No invitation is needed. If NAME has invited you, it takes that invitation, as `/accept NAME` does |
| `/close` | closes your open table |
| `/accept NAME` | takes NAME's invitation, as `/join NAME` does |
| `/decline NAME` | refuses NAME's invitation |
| `/who [NAME]` | lists the players online. With a name, tells about that player: online, idle or not online, the machine they prefer to play on (their profile), and their tables as `/tables NAME` lists them |
| `/tables NAME` | lists the tables where NAME sits: open ones where they wait, with `/join TABLE` when you could sit down there, and the games they are playing, which you can watch elsewhere. It lists at most 20 tables besides your own, then says how many more there are |
| `/top` | shows the first five of the leaderboard with their points, and the note that any score is easy to get on a public server, since the leaderboard is meant for conferences, festivals and LAN parties |
| `/score [NAME]` | shows your score, or NAME's: the points, the wins, draws, losses and resignations, and the rank on the leaderboard |
| `/note [text]` | shows the notes of the game you are playing; with text, adds a note of up to 48 characters to it. Notes are kept with the table, and anyone who may see the table can read them |
| `/say TEXT` | says a line at the table you are at. It is for a command line, since on a chat console every line without a slash is said anyway |
| `/motd` | shows the message of the day |
| `/time` | shows how long you have been signed in and the server's time in UTC: "Signed in 12:34. Server time 21:05 UTC." From an hour on, the time signed in has hours too (1:02:05), and the two sentences then take a line each, so each fits a 40-column screen. Before signing in it shows the server's time alone |
| `/signin NAME PASSWORD` | signs in, on a console that signs in through chat |
| `/quit` | signs out; `/bye` does the same |
| `/help` | lists the commands this console offers |

A line that starts with `/` is a command, and anything else is chat. A command a console does not offer gets the answer "Not available here.", sometimes with a pointer to the client's own way of doing it.

Without invitations, a game takes three steps: one player types `/open`, the other types `/list` to see the open tables with their numbers and who waits at each, then `/join 12` or `/join dan` to sit down.

`/invite NAME` plays NAME. If NAME has invited you already, it takes that invitation, and if NAME waits alone at an open table, it sits you down there. Otherwise it opens a private table and invites NAME to it.

Every console says what each player plays with, where their program said so ([protocol.md](protocol.md), Clients and machines). When someone sits down at your table, the line says it: "ann sits down with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus." When you sit down with someone, `/join`, `/accept` and `/invite` say it too: "You sat down at table 12 with ann, who plays with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus." `/list` and `/tables` put it after each waiting player's name, as "dan (with Netscape Navigator 3.0 on a Macintosh) waits for a player", and a player who plays on from another program is told with a short line, "ann now plays with RSCARDS 5.02 on an IBM PC." A console that cannot show a long line cuts it at a word, and leaves the machine out first. Where nothing is known, nothing is said: a player whose program named nothing, and a private table's players for anyone who is neither seated there nor its creator. Where the program shows a join its own way, as Outland's challenge, Awale's acceptance or the match line of ICDS, the parlor adds a line of its own after it, such as "ann plays with GameMaster 1.0.0 on a Macintosh Plus."

## Which consoles have which

| Console | Commands |
|---|---|
| Outland Backgammon, the lobby chat | `/list`, `/resume`, `/open`, `/close`, `/decline`, `/who`, `/tables`, `/top`, `/score`, `/motd`, `/time`, `/help` |
| Outland Reversi, public talk | `/list`, `/resume`, `/open`, `/close`, `/decline`, `/who`, `/tables`, `/top`, `/score`, `/motd`, `/time`, `/help` |
| NetBoxes, the chat window | `/list`, `/resign`, `/invite`, `/open`, `/join`, `/close`, `/accept`, `/decline`, `/who`, `/tables`, `/top`, `/score`, `/motd`, `/time`, `/quit`, `/help`; `/open` and `/invite` take a size (`7x4`), and `/open` 3 or 4 players |
| batnav, the client's Send Message window | `/list`, `/resign`, `/invite`, `/open`, `/join`, `/close`, `/accept`, `/decline`, `/who`, `/tables`, `/top`, `/score`, `/note`, `/motd`, `/time`, `/signin`, `/quit`, `/help` |
| NetLuff, the program's chat box | `/list`, `/resign`, `/invite`, `/open`, `/join`, `/close`, `/accept`, `/decline`, `/who`, `/tables`, `/top`, `/score`, `/note`, `/motd`, `/time`, `/signin`, `/quit`, `/help` |
| Networdz, the Chat window | `/list`, `/resign`, `/invite`, `/open`, `/close`, `/accept`, `/decline`, `/who`, `/tables`, `/note`, `/motd`, `/time`, `/signin`, `/quit`, `/top`, `/score`, `/help` |
| Worthy Opponent, the Talk window (F6) | `/list`, `/resign`, `/invite`, `/open`, `/join`, `/close`, `/accept`, `/decline`, `/who`, `/tables`, `/note`, `/motd`, `/time`, `/signin`, `/quit`, `/top`, `/score`, `/help`; `/draw` and `/nodraw` for Checkers' draw offers |
| Awale, Write to group (typed with `!`) | `!list`, `!resign`, `!invite`, `!open`, `!join`, `!close`, `!accept`, `!decline`, `!who`, `!tables`, `!top`, `!score`, `!note`, `!motd`, `!time`, `!signin`, `!quit`, `!help` |
| RSCARDS, the lobby's bottom line | `/list`, `/resume`, `/invite`, `/open`, `/join`, `/close`, `/accept`, `/decline`, `/who`, `/tables`, `/top`, `/score`, `/motd`, `/quit`, `/help`; `/invite` and `/open` make a table of the game whose name was clicked |
| RSCARDS, a game's bottom line | `/list`, `/resign`, `/invite`, `/who`, `/tables`, `/top`, `/score`, `/note`, `/motd`, `/help`, `/quit` |

RSCARDS's consoles have no `/time`: RSCARDS front ends, like GameMaster, run on machines with clocks of their own, and nothing changes for them.

In Outland, invitations are made and answered in the client's own windows: pick a player in the list and click Invite, or answer the invitation's dialog. Inviting a player who waits at an open table sits you down there. `/invite`, `/join` and `/accept` say so, and `/list` names the player waiting at each open table. Outland's `/open` table is for players on the web and by telnet, and someone who sits down there reaches the Outland player as a challenge. A game is resigned in its own window, so the lobbies have no `/resign`.

GameMaster has no console. [ParlorNet's own rulebook](http://docs.parlornet.org/parlornet-rulebook.html), which a Mac needs to play GameMaster on a parlor, has a window instead. Its Opponent menu stands for `/invite bot` (Bot), `/open` (Wait for opponent), `/who` (the players online it lists) and `/invite NAME` (a player, or Other...). Its Your games tab and Resume are `/list` and `/resume`, its All games tab and Join are the open tables of `/list` and `/join`, and Sign out and an invitation's Accept and Decline are `/quit`, `/accept` and `/decline`.

In batnav's clients, the player sends each command as a message (Send Msg, or Enviar Mensaje in the Windows client), and signs in with `/signin NAME PASSWORD` first. A command may also start with a backslash, `\invite bot`, because the ncurses client's message field takes the slash key as a backspace; its player sees the commands written that way. A table's game starts when every fleet at it is ready, so the clients' own Start button only says what the game waits for.

Awale refuses a chat line that begins with `/` ("Illegal character."), so in Awale every command begins with `!` instead, and the parlor's answers show them that way. Awale's own Challenge, Accept and Decline buttons do what `!invite NAME`, `!accept NAME` and `!decline NAME` do, and Cancel game resigns.

## Services with their own commands

These services keep the commands their clients were written for, since the clients send and read them:

- FIBS (MacFIBS, FIBS/W, xfibs and others): `invite NAME`, `join NAME`, `decline`, `who`, `motd`, `bye`. `invite bot` plays the bot.
- DOoM (BobGrid, MUD clients): `invite`, `accept`, `decline`, `who`, as DOoM's help describes them. `invite bot` plays the bot.
- IOS (Reversi The Eclipse): `match NAME`, `accept`, `decline`, `who`.
- ICDS (xcheckers, telnet programs): `match NAME` (`match bot` plays the bot), `accept`, `decline`, `who`, `quit`, as on FICS. Lines that start with `/` take the shared commands as well: `/list`, `/resign`, `/invite`, `/open`, `/join`, `/close`, `/accept`, `/decline`, `/who`, `/top`, `/score`, `/note`, `/motd`, `/time`, `/quit` and `/help`.
- The telnet menus: numbered menus and the keys they show. Typed `time` there shows the time signed in and the server's time, as `/time` does.
- The web: buttons and forms.

## The command request

`CMD [table] /line` runs a command line for any frontend, as the player typed it ([protocol.md](protocol.md), Commands). It offers `/list`, `/resign`, `/invite`, `/uninvite`, `/open`, `/join`, `/close`, `/who`, `/tables`, `/top`, `/score`, `/note`, `/say`, `/motd`, `/time`, `/quit` and `/help`, and at a table where the player sits, a typed move such as `/d3`. At the open table where the player waits, `/invite NAME` invites NAME there. `/open` and `/invite` take a game after their other words, as in `/open reversi`. `/resign` and `/quit` answer with a dialog to open, and its Yes confirms them. `/accept` and `/decline` answer that an invitation is answered in its own dialog, since no command answers an offer ([design.md](design.md), the principles).

## For server extensions

`parlord/commands.py` holds the set: the parsing, the help lines and the commands that need only the core. The core runs it for `CMD`, and `adapters/chatcmd.py` gives it to the consoles, with the lines that tell a console's player what each player plays with. A console gives it a way to show lines, a way to send requests to the core, the player's name and the commands it offers, and handles the commands that depend on its own client itself. The module's docstring describes the interface.
