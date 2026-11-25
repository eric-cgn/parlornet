# Supported protocols and server extensions

A parlor supports several protocols, one per port. Each is served by a server extension: a program on the server side that speaks that one protocol to frontends, the programs players use (a web browser, a telnet program, Reversi The Eclipse, MacFIBS and the like: [frontends.md](frontends.md)), and the native protocol to the core behind it. The native protocol is served by the core itself, with no extension in between. Every protocol reaches the same players and the same tables, so a game started in a browser can be watched from a terminal and joined from a 1990s Mac game. How the server extensions are built is in [adapters.md](adapters.md); in the code they are the `adapters/` package. For each protocol, this page says how to connect, sign up and sign in, what you can do, what it needs, and where the protocol came from.

| Key | Usual port | Protocol | Origin | Years in use | Server extension |
|---|---|---|---|---|---|
| `HTTPS` | 443 | [ParlorNet Play](#parlornet-play) | ParlorNet, 2026: PARLORNET/1 over a WebSocket (RFC 6455, 2011) | 2026 to now | Play extension (`adapters/playserver.py`; port 6513 behind a TLS proxy) |
| `NETLUFF` | 1744 | [NetLuff, for the Mac's NetLuff 1.4](#netluff) | Henrik Linder, Sweden, 1998 | 1998 to the early 2000s | NetLuff extension (`adapters/netluff.py`; it listens on 6517, published as 1744, NetLuff's own port) |
| `HTTP` | 6500 | [HTTP and HTML 3.2](#web-site) | HTML 3.2: W3C Recommendation, January 1997 | 1997 to now | web extension (`adapters/web.py`) |
| `PARLORNET/1` | 6501 | [ParlorNet protocol (PARLORNET/1)](#native-protocol) | ParlorNet, 2026 | 2026 to now | the core itself (`parlord`) |
| `TELNET` | 6502 | [Telnet with ANSI escape codes](#telnet) | Telnet: RFC 854, 1983; ANSI X3.64 / ECMA-48, 1970s; IBM PC characters (CP437), 1981 | 1983 to now | telnet extension (`adapters/telnet.py`) |
| `IOS` | 6503 | [Internet Othello Server (IOS)](#ios) | Igor Durdanovic, 1993, for the Waterloo computer Othello tournament | 1993 to about 2000 | IOS extension (`adapters/ios.py`) |
| `FIBS` | 6505 | [First Internet Backgammon Server (FIBS)](#fibs) | Andreas Schneider, 1992; the CLIP client mode, 1997 | 1992 to now | FIBS extension (`adapters/fibs.py`) |
| `OUTLAND` | 6506 | [Outland](#outland) | Outland, Inc., Palo Alto: a Mac online games service | 1994 to the late 1990s | Outland extension (`adapters/outland.py`; port 4000 in the container) |
| `DOOM` | 6507 | [DOoM crossword-game server](#doom) | John Chew, late 1993, to replace ScrabbleMOO | 1993 to about 2005 | DOoM extension (`adapters/doom.py`) |
| `GAMEMASTER` | 6508 | [GameMaster](#gamemaster) | Rhys Hollow, Perth, December 1991 | 1991 to the mid 1990s | GameMaster extension (`adapters/gamemaster.py`; through the ParlorNet rulebook) |
| `NETSPROCKET` | 6512 | [NetSprocket, for NetBoxes](#netsprocket) | Apple, 1996-99 (NetSprocket 1.7); NetBoxes: Will Helling, 2000 | 2000 to the early 2000s | NetSprocket extension (`adapters/netsprocket.py`) |
| `BATNAV` | 6514 | [Batalla Naval (batnav)](#batnav) | Ricardo Quesada, Buenos Aires, December 1995 | 1995 to the late 1990s | batnav extension (`adapters/batnav.py`) |
| `AWALE` | 6515 | [IRC, for Awale](#awale) | IRC: Jarkko Oikarinen, 1988 (RFC 1459, 1993); Awale's messages: Didier and Olivier Guillion, 1997 | 1997 to 2002 | Awale extension (`adapters/awale.py`) |
| `NETWORDZ` | 6516 | [Networdz, for Networdz 2.0 on Windows](#networdz) | Lance Frohman, San Francisco, 1996 | 1996 to the late 1990s | Networdz extension (`adapters/networdz.py`) |
| `ICDS` | 6518 | [Internet Checkers and Draughts Server (ICDS)](#icds) | Michael Rohrer, derived from FICS; release 6 in September 2000 | about 1999 to the early 2000s | ICDS extension (`adapters/icds.py`) |
| `WORTHY` | 6519 | [Worthy Opponent, for DOS by modem](#worthy) | Future Ideas Today, Inc., Lakewood, Colorado, 1988-90 | 1988 to the early 1990s | Worthy Opponent extension (`adapters/worthy.py`; dialed with ATDT host:6519) |
| `RSCARDS` | 6520 | [RSCARDS, for GEnie's graphics front ends](#rscards) | J. Weaver Jr., Factory Programming, 1984-94, for GEnie | 1987 to 1995 | RSCARDS extension (`adapters/rscards.py`) |

Each protocol has a key (`HTTPS`, `HTTP`, `PARLORNET/1`, `TELNET`, `IOS`, `FIBS`, `OUTLAND`, `DOOM`, `GAMEMASTER`, `NETSPROCKET`, `BATNAV`, `AWALE`, `NETLUFF`, `NETWORDZ`, `ICDS`, `WORTHY`, `RSCARDS`). Each server extension declares its key, its port, a name and a one-line description to the core when it connects ([protocol.md](protocol.md#protocols-pro-prd-prl)), and a server's own Protocols page (`/protocols.htm`) lists what is connected now, with its ports; the ports above are the usual ones. Each key there links to its entry in the docs site's [protocols reference](http://docs.parlornet.org/protocols.html).

Status today: ParlorNet Play, the web site (1997 side), telnet, the native protocol, IOS, FIBS, Outland, DOoM, GameMaster, the NetSprocket service, the batnav service, the Awale service, the NetLuff service, the Networdz service, the ICDS service, the Worthy Opponent service and the RSCARDS service are running. IOS has been played with Reversi The Eclipse 1.2 and FIBS with MacFIBS 3, both on a PowerBook 165, and Outland with the Outland Gateway (a whole game of Reversi and the start of one of Backgammon), GameMaster with GameMaster 1.0.0, the NetSprocket service with NetBoxes 1.1, the Awale service with Awale 2.2.2c, the NetLuff service with NetLuff 1.4 and the Worthy Opponent service with Worthy Opponent 2.0 (DOSBox's modem) and the RSCARDS service with GEnie's IBM PC and Mac front ends, all under an emulator, the Networdz service with Networdz 2.0 under Wine, and the batnav service with batnav's X client (in an emulated 32-bit Linux), its ncurses client and its GTK client, and the ICDS service with xcheckers 2.2.3 on Linux; DOoM has been tested with BobGrid on Linux and with scripted clients. The web site's 2027 side is planned. The docs site's [Clients page](http://docs.parlornet.org/) has the programs that can play each game, game by game, each with its own page: its history, where to get it, how to point it at a server, how to sign in and its known quirks ([clients.md](clients.md)).

Every server extension signs you in the same way, by passing on what the server asks for ([Signing up and signing in](#signing-up-and-signing-in)).

## Web site

The web site is the pages on port 6500, from the welcome page on, served by the web extension (`adapters/web.py`); the frontend is your browser. It has two sides on the same port: 1997, which runs now, and 2027, which is planned. ParlorNet Play, the client for today's browsers that runs now, is a server extension of its own ([below](#parlornet-play)).

### 1997 side

**Connect:** open `http://` the parlor's name, port 6500 (for example `http://parlornet.org:6500/`); the server sends `/` to the welcome page. It is plain HTTP, with no encryption.

**Signing up and signing in:** Sign in and New account are separate, on the welcome page and in the navigation bar. For a new account, type the name you want and press Make the account. The server then asks, one page at a time, for what its methods need: a password to choose and type again, or, on a server that signs up by email, your email address, then the password the server mails you and a password of your own. To come back, use Sign in with your name and password (or leave the password empty and answer the next page). A name with no account is told so, with a link to New account; signing in never makes an account. Your session is kept in a cookie (in the page's links for a browser without cookies) and ends when you sign out or close the browser; your password never appears in an address.

**What you can do:** in the lobby, see every table with its game, seats and players, join an open one, watch a running one, and start a table of any game the server hosts, with a time per turn (5 minutes to 5 days) and public or private seating (by invitation). Invite players to your table, and accept or decline invitations. Play Reversi by clicking a yellow dot, and Backgammon by clicking a checker and then where it goes, with buttons to roll, double, take, drop, undo and end the turn. Your home board is drawn at the bottom right; if you prefer it at the bottom left, the link beside the board (or Backgammon board under Your account in the lobby) draws the mirror image, and the points keep their numbers. Resign (after a confirmation), stand up or stop watching. Each player's name links to their profile: the machine they prefer to play on, the games they finished and the programs and machines they played with at public tables; set your own preferred machine under Your account. Activity lists the latest turns at public tables, each with the program and machine it was played with. Sign out, or sign out everywhere; on a password server, change your password. The Protocols page (`/protocols.htm`) lists the protocols the server speaks now, each linking to its entry on the docs site, http://docs.parlornet.org/, which has these docs, the clients and their setup.

**Refresh:** a table page updates itself while you wait for the other player. Without JavaScript it reloads itself (an HTML meta refresh) at the rate you pick on the page: off, every 3, 5, 10, 30 or 60 seconds (5 is the default); it does not reload while it is your turn or after the game ends. With JavaScript and `XMLHttpRequest`, a small script asks the server every 2 seconds whether anything changed and reloads only when it did. JavaScript is optional: every page works without it.

**What it needs:** a browser that shows HTML 3.2 tables, forms with menus (SELECT), and GIF images with an image map, server-side (ISMAP) or client-side (USEMAP). The pages use no CSS and no frames (a cookie holds the session where the browser keeps one), and only named and decimal character references. On a 68000 Mac (Plus, SE, Classic, PowerBook 100), the recommended browser is [Mill](http://mill.1in1e6.com/) (freeware from OneInAMillion Software); on a System 7 Mac with a 68020 or later, or a PowerPC Mac, it is Netscape Navigator 3.01. The [Browsers page](http://docs.parlornet.org/browsers.html) lists every browser known to meet these needs, with its setup; the evidence for each is in [clients.md](clients.md). [site.md](site.md) shows how the pages look in each class of browser.

**History:** HTML 3.2 was published as a W3C Recommendation on 14 January 1997 ([W3C](https://www.w3.org/TR/REC-html32)); NCSA Mosaic, the browser the page is written to work in, dates from 1993. The JavaScript polling uses `XMLHttpRequest`, which browsers took up in the early 2000s; older browsers simply use the meta refresh.

### 2027 side

This side is planned, not built. It is a modern site on the same port, with a JSON API under `/api/` giving the native protocol's typed views and canvas, WebAssembly and WebGL clients on top. `/2027/` is reserved for it. Both sides are clients of the same game server, so 1997 and 2027 players share tables.

## ParlorNet Play

ParlorNet Play is the client for today's browsers, phones and tablets, served by the Play extension (`adapters/playserver.py`) on port 6513. [modern-client.md](modern-client.md) describes it.

**Connect:** open the address the operator gives, usually `https://` the parlor's name and `/play/` (for example `https://parlornet.org/play/`). A reverse proxy on the server terminates TLS and passes the requests to port 6513. On a parlor without a proxy, open `http://` the parlor's name, port 6513, `/play/`. The web site's `/play` sends you there, and its welcome page links it while Play runs.

**Signing up and signing in:** Sign in and New account are tabs on the first page, and the server's questions come one at a time, as on the web site. After signing in, the browser keeps Play's own session cookie, so a reload carries on without signing in again. The classic pages keep a separate session, since they are another site.

**What you can do:** everything the web site offers, with animated boards for Backgammon, Reversi, Grid Words, Cannon Fire and Rummy Tile Classic, and a plain view with the legal moves for any other game (those games are marked unfinished where a table is started). Where the parlor offers them, Play can also send notifications when it is your turn ([play-notifications.md](play-notifications.md)).

**What it needs:** a current browser with JavaScript and WebSockets.

**History:** the WebSocket protocol was published as RFC 6455 in December 2011. ParlorNet Play carries ParlorNet's own protocol over it, one request or reply line per message.

## Telnet

The telnet extension (`adapters/telnet.py`) serves it, and the frontend is any telnet program.

**Connect:** telnet to the parlor's name, port 6502 (for example `telnet parlornet.org 6502`).

**Signing up and signing in:** at the `Login` prompt, type `new` for a new account, then the name you want. The server then asks, one prompt at a time, for what its methods need: a password to choose and type again, or, on a server that signs up by email, your email address, then the password the server mails you and a password of your own. To come back, type your name at `Login` and answer the prompt for your password. A name with no account is told so, with a reminder about `new`. Passwords are not echoed. Then pick a display.

**Display modes:** the terminal type your telnet program reports picks a default, and a menu shows a sample line in each mode so you can pick the one that looks right:

- `pc`: ANSI color and IBM PC (CP437) line and block characters; the default when no type is reported (BBS-style terminals such as SyncTERM and ANSI programs on DOS).
- `utf8`: ANSI color and the same characters in Unicode (xterm, PuTTY, iTerm, GNOME Terminal and the like).
- `ansi`: ANSI color, ASCII characters only (VT100-class terminals).
- `plain`: no escape codes at all; line by line with a prompt, and moves are typed (`d3`, `13-9`).

**What you can do:** in the three ANSI modes the screen is a full-screen menu and board. The menu lists your tables (opponent, time per turn, whose move, time left), the other tables, and an entry to start a table of each game. Move with the arrow keys and choose with Enter; single keys are listed in a status bar (N new table, W watch, P password on a password server, O options, ? help, Q log off). On a board, arrow keys move a cursor, Enter or Space plays, T talks to the table, R resigns after a confirmation, and Esc goes back to the menu while the game goes on. In Backgammon, H (or typing `home left` or `home right`) chooses which side your home board is drawn on; moves are typed the same either way. Typed commands work everywhere too (`join 1`, `new 5m`, `d3`, `say hi`, `quit`); in the menu, `who` lists the players online, `profile name` shows a player's profile, `machine` sets your preferred machine, `activity` lists the latest turns at public tables and `time` shows how long you have been connected and the server's time. Leaving a table or hanging up never resigns.

**Movement keys:** if your telnet program sends no arrow keys, letters can move the cursor instead: IJKL, ESDF, WASD or HJKL (vi). Choose them with O in the menu (or K at the login's last prompt, offered when the terminal type suggests no arrow keys). The arrows still work. Those four letters then always move; to type a command or a word that starts with one of them, press `:` first (`:join 3`), and a key whose letter is one of them is `:` then the letter (`:d`). The choice is kept for your next visit.

**Mouse:** when the terminal type is one that reports mouse clicks (xterm-class terminals: xterm, PuTTY, iTerm, Alacritty, kitty, Konsole, GNOME Terminal, WezTerm, foot, and screen or tmux; not the Linux console), you can click menu rows, squares, checkers, dialog buttons and status bar keys. The keyboard always works without it.

**What it needs:** for the full screen, a telnet program with VT100 or ANSI cursor positioning and a screen of 80 columns by 24 rows; arrow keys, color and line characters are optional (letter keys can stand in for the arrows). Without cursor positioning, the `plain` mode plays line by line. The [Telnet programs page](http://docs.parlornet.org/telnet.html) lists programs known to work, with their setup. If the telnet program reports its window size and the window is smaller than 80x24, the screen asks you to make it larger and redraws once it is. Only the cells that change are sent, so moving the cursor costs about 100 bytes and a 2400 baud line is usable.

**History:** Telnet is the Internet's original remote terminal protocol; the version in use today is RFC 854, May 1983 ([RFC 854](https://www.rfc-editor.org/rfc/rfc854)). The terminal type and window size come from telnet options added later (RFC 1091, terminal type, 1989; RFC 1073, window size, 1988). The color and cursor codes are the ANSI escape sequences of ECMA-48 (first edition 1976) and ANSI X3.64 (1979), as made popular by the DEC VT100 (1978). The `pc` characters are code page 437, the character set of the IBM PC (1981). The `utf8` mode uses UTF-8, designed in 1992.

## Native protocol

**Connect:** port 6501. The client sends `PARLORNET/1` first, ending with CR LF, CR, LF CR or LF, within 20 seconds; the server answers with its greeting and uses that line ending from then on. The core serves this protocol itself, with no server extension in between. It is the protocol every server extension uses to talk to the core, and it is open to anyone writing a frontend of their own (a native ParlorNet client).

**Signing up and signing in:** both go through the same dialog: the client sends `USER name` to sign in or `REG name` to sign up (or `HELO name`, which does either), and the server sends lines to show (`INF`) and asks for each value it needs (`ASK field label`), which the client answers with `AUT field value` until the server says `OK`. A client that shows each line and label and sends back what the player types can sign up and sign in under any method ([accounts.md](accounts.md#the-dialog-sign-in-sign-up-recovery)).

**What you can do:** list games and tables, start, join, watch and leave tables, invite players, play moves (including staged turns such as a Backgammon turn), chat, and poll for events. Each game type has its own typed view and move grammar, so a client can draw the game its own way.

**What it needs:** a machine that can open a TCP connection and read and write lines of 7-bit ASCII text. Lines are at most 80 bytes, nothing is sent unasked, and there are no keepalives, so an 8-bit micro can poll at its own pace. A machine with no clock can ask for the time it has been connected and the server's time with `TIM`, for example with its regular lobby poll, and count the seconds between polls itself. The protocol is in [protocol.md](protocol.md), with a guide and sample sessions for every game in [pnp.md](pnp.md); each game type has its spec in `docs/types/`, such as [types/reversi-1.md](types/reversi-1.md) and [types/backgammon-1.md](types/backgammon-1.md).

**History:** ParlorNet designed it in 2026 for this project. Its greeting line is `PARLORNET/1`.

## IOS

The IOS extension (`adapters/ios.py`) serves it.

**Connect:** port 6503, from an IOS client. In Reversi The Eclipse, type the parlor's name as the host and 6503 as the port in its Internet Othello Server dialog.

**Signing up and signing in:** IOS login names are at most 8 letters and digits. Give your name and your password. A new name signs up in the client's own window, before the lobby: the server's questions appear as prompts there (`Type it again: `, `Sign up as tia? (y/n): `, `Email address: `, `New password: `) and what you type in the window answers them; the password you gave is used only where the account's password is asked for. With no password given, the same prompt asks for it.

**What you can do:** play Reversi against anyone on the parlor, whether they came by IOS, the web or telnet. `who` lists the players who are online, wherever they connected, and the bot. `match <name>` accepts their challenge if they sent one. Otherwise it joins their open Reversi table if they are waiting at one, or opens a table and invites them. The bot sits down within a few seconds. The challenger's minutes become the time per turn. Invitations from anywhere arrive as challenges, and `accept` and `decline` answer them, moves are squares like `d3` (passes are made for you), `ob <game>` watches a game, `board` shows a board, `tell` and `say` talk, and `resign` resigns. `set board 1` adds text boards for people using IOS from a terminal. When a game starts, a line after its announcement says what your opponent plays with, such as "nettie plays with Netscape Navigator 3.0 on a Power Macintosh.", when their program said so, and another line follows if they play on from another program. Games are unrated: ParlorNet keeps no ratings.

**What it needs:** an IOS client.

- Reversi The Eclipse 1.2 (Lars Graulund, Freeverse Software, 1997), Mac OS 7 to 9. It works on PowerPC Macs as it is. On 68k Macs the program itself mangles any host name shorter than 32 characters, so it cannot connect. [Eclipse 68k Fix](http://docs.parlornet.org/eclipse-68k-fix.html), ParlorNet's patcher, fixes your copy by changing two bytes; its page has the explanation and the downloads.
- Other IOS clients, such as OLIOS (X11, OpenLook, 1994-95), are untested.

**History:** the Internet Othello Server was written in 1993 by Igor Durdanovic, after the Fall 1993 computer Othello tournament at Waterloo, where the server provided was poor; he modeled its commands and look on other Internet game servers such as the Internet Chess Server (ICS). It ran at Paderborn, Germany, and moved with him to the NEC Research Institute in Princeton in Fall 1996 (`external.nj.nec.com`, port 5000). It had over 1000 users by the late 1990s, and around 1999-2000 it was replaced on the same host and port by its successor, GGS. The IOS protocol is text meant for telnet, read by clients by line and column; no manual for it survives, so the IOS extension follows what Eclipse 1.2 and the OLIOS client source parse. Sources: Durdanovic's history page ([Wayback copy](https://web.archive.org/web/2000/http://external.nj.nec.com/homepages/igord/history.htm)).

## FIBS

The FIBS extension (`adapters/fibs.py`) serves it.

**Not FIBS:** the First Internet Backgammon Server (FIBS) still runs at `fibs.com` port 4321, and it is the place for serious Backgammon, with its long-standing community and ratings. ParlorNet speaks the FIBS protocol only so that vintage FIBS clients can play ParlorNet's games, which are aimed at vintage machines, events and casual play. ParlorNet is not FIBS and is not affiliated with it. Its FIBS-protocol service says so in its login banner and never connects to fibs.com. The [games page](http://parlornet.org/games.htm#backgammon) has more.

**Connect:** port 6505, from a FIBS client or a terminal. FIBS itself was on port 4321, so give the client the parlor's name and 6505:

- MacFIBS 3: in the Server pane of its Preferences, type the parlor's name under Name or IP Address and 6505 under Port Number.
- FIBS/W: the host and port are in Advanced Options.
- xfibs: `Host` and `Port` in `.xfibsrc`. xibc: edit the `spawn telnet` line in the script.
- Any terminal: `telnet parlornet.org 6505`, with FIBS's text board (`set boardstyle 1` or `2`).

**Signing up and signing in:** give your name and your password. As on FIBS, the login only signs in, and a name with no account is told so. To sign up, log in as `guest` as on FIBS, type `name` and the name you want, and answer the prompts.

**What you can do:** play Backgammon against anyone on the parlor, whatever they connected with. `invite <name> <points>` (or `unlimited` for a single money game) makes a private table and invites them; `join <name>` accepts, resumes a match, or sits at their open table, and `decline <name>` declines. Invitations made on the web or by telnet arrive in FIBS's own words. `who` lists the players who are online and the bot. Matches of several games are played at one table. Moves are typed or sent by the client (`move 13-9 8-7`, `bar-22`, `6-off`), with `roll`, `double`, `accept`, `reject` and `resign`. There are also `who`, `watch`, `look`, `board` and `pip`, and chat with `tell`, `say`, `kibitz`, `whisper` and `shout` (whispers and shouts reach FIBS players only). Ratings are left out (games here are unrated, and the rating field clients expect shows 0.00), as are redoubles and beavers and stored messages. FIBS has no turn clock, but the table's time per turn still applies. When a match starts or is taken up again, a line of its own after FIBS's join message says what your opponent plays with, such as "** cleo joined with TestClient v1.", when their program said so; FIBS's own lines stay as clients expect them.

**What it needs:** a FIBS client, or any terminal.

- MacFIBS 3 (Paul Ferguson, 1998): Macs with a 68020 or later, or PowerPC Macs, System 7, MacTCP or Open Transport.
- FIBS/W 1.30 (Robin Davies, 1995): Windows 3.1.
- xfibs 0.8 and xibc 0.31: X11 (1994-95).
- Later clients that use CLIP.

**History:** the First Internet Backgammon Server was announced on 19 July 1992 by Andreas Schneider. It began in Aachen, Germany, moved to Chalmers in Sweden in December 1993, and was the only Internet backgammon server until 1997. It was designed for people on telnet, "similar to a MUD"; the first graphical clients, MacFIBS and FIBS/W, came in 1994. Schneider's CLIP specification (version 1.008, March 1997) added a mode that is easier for programs to parse. Patti Beadles has run FIBS since 1996, and it still answers at `fibs.com` port 4321 (checked 21 November 2026). Sources: [CLIP specification, Wayback copy](http://web.archive.org/web/20070112115910/http://www.fibs.demon.co.uk:80/CLIP/clip.html); [Backgammon FAQ, 1995](https://bkgm.com/articles/Damish/backgammon-faq.html).

## Outland

The Outland extension (`adapters/outland.py`) serves it. It listens on 4000 in the container, Outland's own port, and the host publishes it as 6506.

**Connect:** port 6506. In the Outland Gateway, open the Outland TCP Tool's settings and type the parlor's name as the host and 6506 as the port (the field is editable; Outland's own port was 4000). Nothing else on the Mac needs changing.

**Signing up and signing in:** the Gateway's account name is your ParlorNet name, and its password is your ParlorNet password. The first time you sign in with a password the server emailed you, it asks for a password of your own; this client cannot ask that, so sign in once on the web or by telnet first. As with Outland's own service, the Gateway only signs in: make your account on the web site (New account) or by telnet (log in as `new`) first. For a name with no account the Gateway shows where, then hangs up. The Gateway's own account creation is not used.

**What you can do:** play single games of Backgammon with Outland Backgammon and of Reversi with Outland Reversi. The Backgammon lobby lists the players who are online, wherever they connected, and the bot. You can challenge anyone there. A challenge to another Outland player goes straight to them. A challenge to a player waiting at an open table joins that table. A challenge to anyone else, such as the bot or a web player, becomes an invitation they answer from their own program. Invitations from the web or telnet arrive as challenges. `/open` in the lobby chat opens a table that web and telnet players can join, and you get a challenge when someone sits down; the lobby's line before it says what they play with, such as "ann sat down at your table 12 with Netscape Navigator 3.0 on a Macintosh.", when their program said so. The server rolls the dice. Doubles from web and telnet players are offered to you to take or drop; your own double is refused, because the client offers the cube only after the server has already rolled. Outland Reversi's waiting room lists the same players. An invitation there goes straight to another Outland Reversi player, sits you down at a table where someone waits, or becomes an invitation to anyone else, and you choose whether to move first or last. When a game starts, the parlor's line in the game's talk says what your opponent plays with. Both lobbies take the shared chat commands ([chat-commands.md](chat-commands.md)). Watching, draws, high scores and software downloads are left out.

**What it needs:** Outland Gateway 1.3.2 with Outland Backgammon or Outland Reversi: a Mac with a 68020 or later, or a PowerPC Mac (both games are 68k code), with System 7 and MacTCP or Open Transport. The Info-Mac copy of the Outland installer has no games, and Info-Mac has Reversi on its own but not Backgammon; the full set with both games is on a 1998 Macworld CD (see the Clients page).

**History:** Outland was a Mac online games service run by Outland, Inc. from Palo Alto from 1994. The client was free, the service cost US$9.95 a month, and players reached it by modem or over the Internet. Backgammon (by Aaron Barnett) was part of it by early 1995. Its protocol was never published; what the Outland extension speaks was recovered from the 1994-95 client software. The service closed in the late 1990s (the exact date is not established) and the company is gone. Sources: [Outland's own pages, 1996, Wayback copy](http://web.archive.org/web/19961113073225/http://outland.com:80/Backgammon.html).

## DOoM

The DOoM extension (`adapters/doom.py`) serves it.

**Not DOoM:** DOoM was John Chew's crossword-game server, and none of its servers is known to run today. ParlorNet speaks DOoM's commands so that DOoM's clients, BobGrid and any telnet or MUD client, can play ParlorNet's Grid Words with anyone on the parlor. The game is Grid Words on the standard board, with the `classic` ruleset on boards made here (docs/types/gridwords-1.md). The service says in its banner that it is not DOoM.

**Connect:** port 6507. In BobGrid, type `/HOST parlornet.org 6507` and then `/GO` (BobGrid's own default is a DOoM host on port 7777). From a terminal or a MUD client such as TinyFugue or MUDDweller, connect to the parlor's name on port 6507.

**Signing up and signing in:** type `connect name password` (or `co name password`), as on DOoM. The password is your ParlorNet password. BobGrid sends this line by itself once `/CONWIZ` has saved your name and password. A name with no account is told so. To make an account, type `request name` and answer the server's questions at the prompts, or sign up on the web or by telnet.

**What you can do:** after connecting you are in the Gaming Room, where every Grid Words table on the server is a board called `bd_` and its number. `look` lists them. `newboard` sets up a board and sits you at it, and `invite <name>` asks someone to it, the bot too (`invite bot`). `join bd_12` goes to a board and watches it, and `join` there sits down in a free seat; the game starts when every seat is taken. On your turn, `place 8d WORD` (row first for across, column first for down, lower case or `?`-style marks for blanks, as in DOoM's `help place`) shows what the play scores, `commit` plays it and `undo` takes it back; `exchange` and `pass` are the alternatives. `b` shows the board, `track` the unseen tiles, `time` the scores and clock, `log` and `log2` the game, and `"`, `kibitz` and `page` are for talking. Invitations made on the web or by telnet arrive as a line of text, and `accept <name>` or `decline <name>` answers them. `who` lists the players who are online and the bot. BobGrid redraws its board, log and tile tracker by itself on each turn. After "nate joins the game." a line says what the player plays with, such as "nate plays with BobGrid 2.3 on a Sun SPARCstation.", when their program said so; going to a board lists it for each player there, after the board, and a player who plays on from another program is announced again. DOoM's world of rooms and objects is left out, as are the board Workshop (every board here has the standard layout and tiles), ratings, time penalties and pausing the clock (the table's time per turn applies instead). There are no challenges either, because every word is checked against the server's word list when it is played; `ospd word` looks words up.

**What it needs:** BobGrid 2.3 (Mark Watkins, 1999), a Java 1.1 program for Windows with Sun's JRE 1.1.8, for Unix with Java 1.1, and in principle for the Mac with MRJ, or any telnet or MUD client.

**History:** John Chew wrote DOoM, the "Distributed Object-oriented Mud", in Perl from late 1993, after ScrabbleMOO closed. It ran public crossword-game servers (MarlDOoM, WisDOoM) and a server for expert players (PoslDOoM), reached by telnet and later over HTTP too, and called itself a generic crossword-game server, since each board's layout and tiles were set by its owner. By late 2000 the DOoM servers had over 6,000 users and nearly 200,000 rated games. Mark Watkins' BobGrid gave it a graphical front end in 1999. Sources: [DOoM FAQ, Wayback copy](http://web.archive.org/web/20110715110416/http://www.poslarchive.com/math/doom/faq.html); [BobGrid's pages, Wayback copy](http://web.archive.org/web/20000823043542/http://www.math.uga.edu:80/~mwatkins/BobGrid.html).

## GameMaster

The GameMaster extension (`adapters/gamemaster.py`) serves it on port 6508, through ParlorNet's own rulebook for GameMaster, which a Mac needs to play on a parlor.

**The ParlorNet rulebook:** ParlorNet's own [rulebook for GameMaster](http://docs.parlornet.org/parlornet-rulebook.html) goes in the folder with GameMaster. File > New Game > ParlorNet opens its window, which connects to the parlor on port 6508 and signs you in with the parlor's own steps (your name, then a password if the server asks). It starts Othello, Dots&Boxes, BattleShips or one of ParlorNet's own games against the bot, anyone or a named player, and shows the player's invitations with Accept and Decline. GameMaster opens each game in its own window, already connected, and two or more can run at once. All of them travel over the rulebook's one connection, which opens with `PARLORNET/1` and uses the framing of the client protocol (the extension's docstring lists its lines), so the parlor needs no other port and no address. A game is yours because it comes over the connection you signed in on. Closing the ParlorNet window or signing out ends its games. The rulebook needs a Mac Plus or later with System 6.0 or later and MacTCP, and GameMaster 1.0.0 as it was released.

**Without the rulebook:** a game connected with GameMaster's own Game > Connect is turned down. GameMaster shows it as "Game refused" with the parlor "isn't interested", and the parlor logs the call. GameMaster cannot be taught the ParlorNet rulebook by the parlor either, because it learns a rulebook only from the Mac that calls it, never from the one it calls. Make your account on the web site (New account) or by telnet (log in as `new`) first, then sign in in the rulebook's window.

**Playing the bot:** choose Bot in the rulebook's Opponent menu. A game started with Wait for opponent also gets the bot if nobody joins its table within a minute.

**What you can do:** play Dots and Boxes with the Dots&Boxes rulebook, Reversi with Othello, and Naval Combat with BattleShips, against anyone on the parlor, and Checkers, Mancala (Kalah), Five in a Row, Grid Words, Rummy Tile, Cannon Fire and Backgammon with ParlorNet's own [game rulebooks](http://docs.parlornet.org/gamemaster-rulebooks.html), which show a move only once the parlor has accepted it. All ten start from the ParlorNet rulebook's window. For a named player or the bot, the parlor opens a private table and invites them, and waits up to three minutes for them to sit down. For anyone, the parlor first takes up an invitation to you or joins an open table where someone waits, and otherwise opens a public table that web and telnet players can join. A named player who has already invited you to a table is taken up the same way. Your new game always moves first, as GameMaster's caller does, so you take the first seat. In Othello and ParlorNet's own games, when the other player already holds the first seat, the parlor asks your Mac to swap players (the Mac says the parlor "wishes to swap players"). With OK you play second (black in Othello), and with No the game ends. The tables use the rules of the GameMaster rulebooks: Dots and Boxes on 13 by 11 boxes, won as soon as one player holds more than half of them (`majority`), and Naval Combat with a salvo of six shots a turn (`salvo`). At a `salvo` table, each player's own program receives the other player's fleet when shooting starts, because GameMaster works out the hits on the Mac. The web and telnet do not show it. A saved game in progress is turned down, because a table always starts from the beginning. Requests from the Mac to swap sides or restart are declined, and when your Mac hangs up during a game, the game is resigned for you. GameMaster announces a game in its own words, so the rulebook's window adds a note when the game starts with what your opponent plays with, such as "Table 12: webcl plays with Netscape Navigator 3.0 on a Macintosh.", when their program said so, and another if they play on from another program. Other rulebooks are refused, and no rulebook code is ever sent or taken.

**What it needs:** GameMaster 1.0.0 with its User Kit rulebooks Dots&Boxes, Othello and BattleShips, and ParlorNet's rulebooks, on a Mac with System 6 or 7 and MacTCP. Macs on AppleTalk only cannot reach it.

**History:** GameMaster was written by Rhys Hollow at the University of Western Australia in 1991, with Peter N. Lewis (the MacTCP code and five rulebooks) and Quinn (the programmer's manual). It is a game server for two players: each game is a rulebook, a small code resource, and two Macs play it over MacTCP or AppleTalk, with GameMaster teaching a missing rulebook to the other Mac. It was released as Povertyware in December 1991 with a developer's kit holding the Pascal source of every rulebook. Isolation, Gravity Wars and Crystal Ball, an opponent finder, followed in 1992-93.

## NetSprocket

Served by the NetSprocket extension (`adapters/netsprocket.py`) on port 6512, for NetBoxes 1.1 (Dots and Boxes) on PowerPC Macs. NetBoxes uses Apple's NetSprocket 1.7, and the parlor is the host. NetSprocket's join request carries no game ID on TCP, so the parlor knows the program by the first message it sends after joining.

**What it needs:** Mac OS 8 or 9, Open Transport's TCP/IP, and Apple's `NetSprocketLib` in the Extensions folder.

**Connect:** Join Net Game in NetBoxes' File menu, then TCP/IP, with the parlor's name as the host and `6512` as the port. The parlor is always the host.

**Signing up and signing in:** the Join dialog's player name is your ParlorNet name, and its password is your ParlorNet password. They go to the parlor in the join request. The first time you sign in with a password the server emailed you, it asks for a password of your own; this client cannot ask that, so sign in once on the web or by telnet first. NetBoxes never shows NetSprocket's refusal, so the parlor says why in its chat window and then closes, and NetBoxes says "The game host has left!". Make your account on the web site (New account) or by telnet (log in as `new`) first.

**What you can do:** the program's chat window opens with ParlorNet as the host's player, and it takes the shared chat commands ([chat-commands.md](chat-commands.md)): `/invite bot` plays the bot, `/invite NAME` invites a player on any frontend (another Mac too), `/open` opens a table anyone can join, `/join TABLE` or `/join NAME` sits down at an open table, `/accept NAME` and `/decline NAME` answer an invitation, `/list` lists your games and the open tables, `/close` closes your open table, `/resign` resigns the game, and `/who`, `/motd`, `/help` and `/quit` do what they say. Anything else you type is chat with the other players. A game starts by itself when every seat is taken. The tables are standard Dots and Boxes tables (docs/types/dotsandboxes-1.md) of any size the type has and for two to four players, wherever they were made; `/open` and `/invite` take a size (`7x4`, boxes across and down) and `/open` a number of players. The other players appear with their names and their own faces, in the table's turn order, and the parlor starts each game in NetBoxes itself.

**Who moves first:** NetBoxes lets only the player who started the game move first. When the parlor gives a Mac the first turn, the player before it passes first in that Mac's game only, and the table records no such pass.

**What it leaves out:** mines, random lines at the start, and NetBoxes' points as a score (the parlor counts boxes, and says the result in the chat); the majority ruleset's tables.

**History:** Apple's NetSprocket, part of its Game Sprockets, was a networking library for Mac games over AppleTalk and TCP/IP, from 1996 to version 1.7 in 1999. Will Helling's NetBoxes (2000), shareware, used it for Dots and Boxes among up to four players, with a few bytes a message: every copy keeps the board, the turn and its own points.

## batnav

Served by the batnav extension (`adapters/batnav.py`) on port 6514, for the clients of Batalla Naval (batnav), the networked Battleship of 1995-98.

**What it needs:** a batnav client of 0.50 or 0.60: the X client (`xbtclient`, XView), the ncurses client (`nbtclient`), the Windows 3.x client (`btwin`, with a Winsock TCP/IP stack), or 0.60's GTK client (`gbnclient`). They are in the author's tarballs, with the source ([batnav's page](http://docs.parlornet.org/clients/batnav.html)).

**Connect:** batnav's own server used port 1995, so give the client the parlor's name and 6514: `xbtclient -s parlornet.org -p 6514 -u yourname`, and the same for `nbtclient` and `gbnclient`, or the host and port in the client's Config window (Conectar in the Windows client).

**Signing up and signing in:** the client sends only a name when it connects. On a parlor that signs in by name alone, that name signs you in. Otherwise send the message `/signin name password` with the client's Send Message (in the ncurses client `\signin name password`, since its message field takes the slash as a backspace). The first time you sign in with a password the server emailed you, it asks for a password of your own; this client cannot ask that, so sign in once on the web or by telnet first. Make your account on the web site (New account) or by telnet (log in as `new`) first.

**What you can do:** play Naval Combat with batnav's rules (ten ships, not touching; `batnav` in docs/types/navalcombat-1.md) against anyone on the parlor, whatever they connected with. Place your fleet and press Send Board, then send `/invite bot` to play the bot, `/invite NAME` for a player, or `/open` for anyone; the message window takes the shared chat commands ([chat-commands.md](chat-commands.md)), and anything else is chat at your table. The game starts when every fleet at the table is ready. You shoot on your turn by clicking the enemy board, and the client shows the hits on your own fleet as they come. The other fleet is never sent to your client, not even to one that asks for it the way batnav's own server allowed. Leaving or losing the connection resigns a game in progress.

**What it leaves out:** games of three to five players (a Naval Combat table seats two); the Start button, since the game starts by itself; player numbers as batnav's server gave them (each client is player 1 to itself).

**History:** Ricardo Quesada wrote Batalla Naval in Buenos Aires: the first public version, 0.28, is dated 28 December 1995. Its server held every fleet and judged every shot for up to five players, through fixed 108-byte records. Clients came for X11 (XView, and GTK from 1998), for terminals (ncurses, 1996) and for Windows 3.x (Horacio Pena, 1996). It is GPL.

## Awale

Served by the Awale extension (`adapters/awale.py`) on port 6515, for Awale 2.2.2 to 2.4 by Didier and Olivier Guillion (Myriad), which plays Mancala with Oware rules ("live", in its own words) over IRC.

**What it needs:** Awale 2.2.2 or later of the 2.x versions; on the Mac, MacTCP or Open Transport. The program's `Internet` folder holds two plain text files that it reads at startup. In `serveurs.cfg`, the list of IRC servers, put one line with the parlor: `ParlorNet;name;host;6515` (a network name, a site name of your choice, the parlor's name and the port). In `internet.cfg`, change the second line, the mail relay that the program sends each game's result to, to a single `;`: that switches the mail off, which would otherwise end every game with an error about a server that no longer exists.

**Connect:** choose Internet > Network setup, pick ParlorNet in the list, fill in your name, then Connect now. The program logs on, joins its channel and lists the players there: the parlor's players who are online, the bot, and ParlorNet itself, which answers in the message box.

**Signing up and signing in:** Awale sends no password, and the program refuses a chat line that begins with `/`, so commands begin with `!` here. Type `!signin name password` in Write to group and press Send. On a parlor that signs in by name alone, the name in Network setup signs you in as you connect. The first time you sign in with a password the server emailed you, it asks for a password of your own; this client cannot ask that, so sign in once on the web or by telnet first. Make your account on the web site (New account) or by telnet (log in as `new`) first.

**What you can do:** pick a player in the list and click Challenge: that invites them to an Oware table (or takes their invitation, or sits you at their open Oware table), and they accept when they sit down. The bot accepts at once. An invitation to you from any frontend arrives as a challenge from that player, with Accept and Decline. Write to group takes the shared chat commands ([chat-commands.md](chat-commands.md)) with `!` in front: `!invite NAME`, `!open`, `!join TABLE` or `!join NAME`, `!accept NAME`, `!decline NAME`, `!list`, `!close`, `!resign`, `!note`, `!who`, `!motd`, `!help` and `!quit`. During a game, what you type in the board's box is chat with your opponent. Cancel game on the Mac resigns the table.

**Who moves first:** Awale decides by numbers that the two programs exchange, the bigger one moving first. The parlor's player answers so that the table's first seat moves first: at a table you open by challenging, that is you.

**The rules:** Awale's own "Awale" rule is ParlorNet's `oware` ruleset in every move: twelve or more seeds skip the starting hole, 2s and 3s are captured on the opponent's side, a capture that would leave the opponent no seed captures nothing, and a player must feed an opponent who has none when a move can. They end differently. The parlor ends the game when a player has more than 24 seeds and when a position repeats; Awale plays on, so the parlor ends it on the Mac as a deadlock, and Awale shows the seeds captured so far. When the player to move cannot feed the other, Awale lets that player move once more and then ends the game itself, with the same final count as the parlor.

**What it leaves out:** ratings (every player shows Awale's starting 100 points), watching other games, the other rules of the program, mailing results, and coming back to a game: Awale cannot resume one, so a game left when the program quits or the connection drops stays at the parlor, to be finished on the web or by telnet (or `!resign`).

**History:** IRC (Internet Relay Chat) was written by Jarkko Oikarinen in 1988 and described in RFC 1459 in 1993. Awale 2.2.2 (December 1997) used public IRC networks as its meeting place: each copy joined a keyed channel, introduced itself and exchanged its moves with the other copies as numbered messages. Version 3.0 (2002) moved Internet play to Myriad's own server.

## NetLuff

Served by the NetLuff extension (`adapters/netluff.py`), for NetLuff 1.4, Henrik Linder's five-in-a-row program for Macs with Open Transport (68030 or later, and PowerPC). The extension listens on 6517, and the parlor publishes it as 1744 too, because NetLuff has no port setting and always connects to 1744.

**What it needs:** NetLuff 1.4 ([Info-Mac](http://ftp.funet.fi/pub/mac/info-mac/game/net-luff-14.hqx); US$10 shareware), System 7.5.1 or later (7.1 with the Thread Manager) and Open Transport's TCP/IP.

**Connect:** type your ParlorNet name in NetLuff's name box, the parlor's name or address in the "Remote IP here" box, and click Connect. NetLuff connects to port 1744. A parlor that does not publish 1744 cannot be reached from NetLuff.

**Signing up and signing in:** NetLuff sends the name in its name box, but nothing secret, so the name alone never signs in. In the chat box, type `/signin password` to sign in with that name, or `/signin name password`. The first time you sign in with a password the server emailed you, it asks for a password of your own; this client cannot ask that, so sign in once on the web or by telnet first. Make your account on the web site (New account) or by telnet (log in as `new`) first. The password shows in your own chat box as you typed it, as every line does there; only the parlor receives it.

**What you can do:** the chat box takes the shared chat commands ([chat-commands.md](chat-commands.md)): `/invite bot` plays the bot, `/invite NAME` invites a player on any frontend (another NetLuff too), `/open` opens a table anyone can join, `/join TABLE` or `/join NAME` sits down at an open table, `/accept NAME` and `/decline NAME` answer an invitation, `/list` lists your games and the open tables, `/close` closes your open table, `/resign` resigns the game, `/note` reads or adds a note, and `/who`, `/motd`, `/help` and `/quit` do what they say. Invitations arrive in the chat box with NetLuff's page sound. Anything else you type is chat with your opponent. When the game starts, the window's title names your opponent and the parlor says who moves first; you click squares as in a game between two copies of NetLuff.

**The game:** NetLuff's board is 20 by 20, and five or more in a row wins, so its tables are Five in a Row tables with the `freestyle` ruleset and `size=20` (docs/types/fiveinarow-1.md). A NetLuff player sits down only at such a table made from NetLuff; when a player on the web or by telnet invites you, `/invite NAME` them to a table of yours instead. The parlor decides who moves first. When your opponent makes five, NetLuff draws the line in red, shows "Loser!" and disconnects, as it does with another copy; when you make five, NetLuff shows "Victory!" and disconnects. A resignation, the turn clock or a full board ends the game in the chat box. NetLuff clears its board only when it connects, so each connection plays one game: click Disconnect, then Connect, to play again. Disconnecting during a game resigns it, since NetLuff cannot show a game in progress again.

**What it leaves out:** NetLuff's page button (not passed on), watching, resuming a game, other board sizes and the `standard` ruleset.

**History:** Henrik Linder wrote NetLuff in REALbasic and released version 1.4, as shareware, on 10 October 1998. Two copies play over TCP: one waits for a connection on port 1744 and the other connects to it, and they exchange short text messages with two-letter tags. Each copy checks only its own player's lines and tells the other when it has won.

## Networdz

Served by the Networdz extension (`adapters/networdz.py`) on port 6516, for Networdz 2.0, Lance Frohman's crossword-tile game for Windows 3.1 and later, which plays two to four PCs over Winsock TCP. Networdz has no server: one player's copy hosts the game and connects out to the others' copies, which wait in Answer mode. The parlor is always the answering side. Your copy hosts and connects to the parlor once for each Remote player, and each of those seats is played at the parlor, on the web, by telnet, in ParlorNet Play, on any other Grid Words client or by the bot. The parlor never connects out, so your PC need not be reachable.

**What it needs:** Networdz 2.0 ([Discmaster](https://discmaster.textfiles.com/file/10566/Giga%20Games%205%20%28Walnut%20Creek%29%20%28June%201997%29.iso/windows/puzzles/networdz.zip); US$12 shareware) and Winsock. In `NETWORDZ.INI`, in the Windows directory, `Internet Protocol Port=6516` under `[Remote]`. Networdz takes its board and tiles from a configuration file that every player must have the same, so load ParlorNet's (`PARLOR.CFG`, on the client's page): the standard 15 by 15 board, the 100 standard tiles and racks of 7. Turn off Trade Blanks, Exchange 4 of a kind, Timed and Tournament Rules (Options, Rules).

**Connect:** in Options, Players, make yourself Player 1, a Person, and make each other player Remote with the parlor's name or address. In Options, Communicate, keep "Local / Dial Mode" and click Connect. The remote players show as ParlorNet until someone sits in their seat.

**Signing up and signing in:** Networdz sends names but nothing secret. In Options, Chat, type `/signin name password`; once a game has named you, `/signin password` does too. The first time you sign in with a password the server emailed you, it asks for a password of your own; this client cannot ask that, so sign in once on the web or by telnet first. Make your account on the web site (New account) or by telnet (log in as `new`) first.

**What you can do:** the Chat window takes the shared chat commands ([chat-commands.md](chat-commands.md)). `/invite NAME` and `/invite bot` make your table, with a seat for you and one for each Remote player, and invite players to it; `/open` makes it a table anyone can join, and `/close` closes it before the game. `/resign`, `/list`, `/note`, `/who`, `/motd`, `/help` and `/quit` do what they say. Anything else you type is chat at the table, and the other players' chat comes back from their seats. When everyone is seated, choose New Game.

**The game:** your copy of Networdz hosts, so it shuffles the bag, deals your tiles and checks every word with its own dictionary, and the parlor follows it. The table is marked `integrity=false` ("the host's Networdz deals the tiles", [protocol.md](protocol.md#tables-that-keep-no-integrity-int)): the parlor cannot keep its secrets or decide the game, and every rack is shown on every copy of Networdz anyway. The other seats' tiles are drawn at the parlor, from the same bag. A word your dictionary refuses is taken back at the parlor, the player is told why and plays again, and the table refuses that word from then on. Your own words are taken as your Networdz takes them. Networdz ends the game by its own rules, and so does the table.

**What it leaves out:** Trade Blanks, Exchange 4 of a kind, Timed and Tournament Rules, computer players and a second person at the host, other configurations and languages, saved games, watching, and coming back to a game: after Stop Game the table goes on at the parlor, to be finished on the web or by telnet (or `/resign`).

**History:** Lance Frohman released Networdz 2.0 as shareware in October 1996, the first version to play over a network. Its help file says there is no server for Networdz, which is peer to peer: the host's copy runs the game and the others answer. Its messages are binary, each a type letter with a fixed length.

## ICDS

Served by the ICDS extension (`adapters/icds.py`) on port 6518, for xcheckers 2.2.3, Peter Chiocchetti's checkerboard for X11, and for anyone at a telnet program.

**What it needs:** xcheckers 2.2.3 built from its source ([xcheckers' page](http://docs.parlornet.org/clients/xcheckers.html)) and a `telnet` program, or a telnet program alone.

**Connect:** `xcheckers -icds -host parlornet.org -port 6518`, from a terminal: xcheckers runs telnet to the parlor and shows the board, and you type in the terminal. From a telnet program, `telnet parlornet.org 6518`.

**Signing up and signing in:** as on FIBS, give your name at `login:` and your password at `password:`. The login only signs in: type `guest` at `login:` to make an account, then the name you want, and answer the prompts. With a password the server emailed you, the prompts then ask for a password of your own, typed twice. In xcheckers' terminal the password shows as you type it.

**What you can do:** play American checkers against anyone on the parlor, whatever they connected with. `match bot` plays the bot and `match NAME` challenges a player, or takes up their challenge; a number after the name sets the minutes a turn, and `black` or `white` your side. Challenges from anywhere arrive as `Challenge:` lines, which `accept NAME` and `decline NAME` answer. In xcheckers you move by dragging a piece; at a terminal you type `11-15`, or `15x22` for a jump. A jump of several pieces may be given by its first and last squares (`15-31`), as xcheckers sends it, or a jump at a time. `draw` offers a draw or accepts one, `resign` resigns, `refresh` shows the board again, `games` and `observe N` watch a game, `who` and `finger` show who is online, `tell`, `say` and `shout` talk, and the shared `/` commands work too ([chat-commands.md](chat-commands.md)). Boards come as text (style 1) until you `set style 5`, which xcheckers does by itself.

**The rules:** the `american` ruleset of `checkers/1` (types/checkers-1.md): the squares numbered 1 to 32 as in published games and in xcheckers, Black first, captures compulsory, a jump that can go on must go on, and a man crowned on the far row ends the turn. A game with no capture and no man moved for 40 moves each is a draw.

**What it leaves out:** ratings, game clocks (the table's time per turn applies, and xcheckers' clock shows at most 9:59 of it), and the other boards xcheckers knows: international, Italian and Russian draughts.

**History:** Michael Rohrer's Internet Checkers and Draughts Server was derived from FICS, the Free Internet Chess Server, and released under the GPL; its sixth release came out in September 2000, and it has since disappeared with its source. xcheckers (2000) was its graphical client. The extension follows what xcheckers' source reads and sends: a one-line board in "style 5", a few refusals that start with the server's `icds% ` prompt, and moves as two square numbers.

## Worthy

Worthy Opponent, by modem. Served by the Worthy Opponent extension (`adapters/worthy.py`), on port 6519, for Worthy Opponent 2.0, Future Ideas Today's pack of two-player modem games for the IBM PC (DOS). The program plays against another copy over a Hayes modem; the parlor is that other copy, the one that answers.

**What it needs:** Worthy Opponent 2.0 ([Discmaster](https://discmaster.textfiles.com/file/13114/GameWare%20Collection%20%28CMS%20Software%29%20%281993%29.iso/games/w_games/worthy.zip); US$15 shareware), a PC with a color screen, and a modem that can dial a host and port: a WiFi modem, or the emulated modem of DOSBox, DOSBox-X or DOSBox Staging (`serial1=modem`).

**Connect:** in the program's phone book (F3), add an entry whose number is `ATDT {host}:6519` (the program sends a number that starts with `AT` as it stands) and press Enter in its Dial column. The number field takes any characters, so no phone-book trick of the modem is needed. Set the clocks first if you like (F8, the game, then F7): the program's own clock runs on your PC.

**Signing up and signing in:** the program sends the name from its Name menu (F1, Name), but nothing secret, so the name alone never signs in. The F6 line lights up: open the Talk window and type `/signin password` to sign in with that name, or `/signin name password`. Make your account on the web site (New account) or by telnet (log in as `new`) first; a password the server emailed you needs one sign-in on the web or by telnet first. The password shows in your own Talk window as you typed it.

**What you can do:** pick the game with F8: CornerStone (Reversi), Checkers or Quinte (Five in a Row). The parlor enters it at once, and the program says who moves first; it draws that itself. Then, in the Talk window, `/invite bot` plays the bot, `/invite NAME` invites a player on any frontend, `/open` opens a table anyone can join, and `/join TABLE`, `/join NAME` and `/accept NAME` take a seat at someone's table: the shared chat commands ([chat-commands.md](chat-commands.md)), with `/draw` and `/nodraw` for Checkers' draw offers. Your seat is the one the program's draw gave you; when a table's free seat moves the other way, the parlor says so, and you leave the game (F10) and pick it again for a new draw. Anything else you type is chat with your opponent.

**The game:** CornerStone tables are Reversi tables (8 by 8), Checkers tables are `american` with no move limit, as in the program, and Quinte tables are Five in a Row tables with the `freestyle` ruleset and `size=19`, the program's board, where six in a row wins too. You play with the program's own keys, and your opponent's moves appear as if another copy played them. The program shows a game's end by itself; a resignation, the server's turn clock or an agreed draw ends it in the Talk window, and the parlor leaves the game. Leaving the game with F10 while it runs, or hanging up, resigns it.

**What it leaves out:** the program's nine other games (Backgammon among them: the program makes its own dice from its draw, and the server rolls its own), watching and resuming a game.

**History:** Future Ideas Today, Inc. of Lakewood, Colorado, published Worthy Opponent in 1988 and version 2.0 in January 1990, as shareware. Two copies play over a modem: the copy that dialed picks the games and the clocks, the copies exchange short frames of two data bytes with a checksum and an acknowledgment, and each copy plays both sides of the game from the other's keystrokes and a shared random seed.

## RSCARDS

Served by the RSCARDS extension (`adapters/rscards.py`) on port 6520, for the graphics front ends of GEnie's multiplayer games: the Mac (color and mono, 5.0), IBM PC (5.02), Commodore 128 (5.00 color, 2.10 mono) and Amiga (4.01) versions of RSCARDS. All ten of ParlorNet's games play on it, with screens of the parlor's own.

**What it needs:** an RSCARDS front end ([its page](http://docs.parlornet.org/clients/rscards.html) links the archive copies) and a way from its serial port to TCP: a WiFi modem (`ATDT host:6520`), a serial bridge, or an emulator's modem or serial bridge. The line runs at 1200 or 2400 baud, 8N1. A WiFi modem may be in raw or telnet mode: the extension refuses every telnet option and sends none.

**Connect:** dial the parlor at port 6520. The parlor shows a one-line welcome in the front end's terminal and switches it into graphics (the auto-shift: `ENQ ENQ ENQ 1`, the front end's `FLC n 9`, the parlor's `LAUNCH 1 n`). A front end started by hand, and the IBM PC version, which has no terminal, send their identification (`RSCARDS'v05.000`, `RSCARDS$v05.020`) and the parlor answers it, whenever it comes.

When no front end answers within about 12 seconds, the parlor shows a short guide, 40 columns wide: run the RSCARDS program now (on the IBM PC, go to DOS and type `RSCARDS 1`; on the Mac, File > RSCARDS), or press T for the text games. T, or RETURN twice, carries on with the telnet service's menus and boards on the same connection, so a plain terminal need not dial again; a line that does not speak telnet gets none of telnet's codes. The trigger comes again every 15 seconds while the guide waits, for a front end loaded after a terminal program dialed, and the guide once more after two minutes.

**Signing up and signing in:** in graphics, in two fields: your ParlorNet name, then your password, which does not show as you type it. A new name is offered a sign-up, with the server's own questions in the same fields. The name alone never signs in where the parlor uses passwords.

**What you can do:** the lobby lists the ten games with Bot (a table with the bot) and Open (a table anyone can join) beside each, your running games, your invitations with Yes and No, and other tables, open ones to join and games being played with Watch; a click goes to any of them. Each game has its own screen (the client's page has them all), played by clicks; moves can be typed as well. The bottom line takes chat and the shared chat commands ([chat-commands.md](chat-commands.md)): `/invite NAME`, `/open`, `/join TABLE` or `/join NAME`, `/accept NAME`, `/decline NAME`, `/list`, `/resume`, `/resign`, `/note`, `/who`, `/tables NAME`, `/top`, `/score`, `/motd`, `/help` and `/quit`.

**On a slow line:** screens are built for 2400 baud. The static parts of a board are drawn once, partly from shapes the front end keeps (its macros), and a move sends only the squares it changed, a status line and a message: a whole screen is 1 to 2.5 KB, a move and the reply a few hundred bytes ([bandwidth.md](bandwidth.md#rscards-screens) has the numbers for each game). Packets go out a few at a time ahead of their acknowledgments, and a bad one is sent again from where the front end lost it.

**What it leaves out:** GEnie's own games and screens (Chess, Blackjack, Poker and Bridge are not ParlorNet games), the Waiting Room's rooms and the GEnie menus.

**History:** RSCARDS was GEnie's graphics games system from about 1987 to 1995, written by J. Weaver Jr. of Factory Programming. The host program ran on GEnie and drew every screen; the front ends, one per kind of computer, drew what it sent: packets of 7-bit text with a checksum, each acknowledged by the front end.

## Signing up and signing in

Neither frontends nor server extensions know the sign-in or sign-up method: the server names what it wants, the server extension passes that on, and the frontend shows it and sends your answer back. The operator picks one way to sign in and one way to sign up ([accounts.md](accounts.md#methods)):

- **Name only** (sign-in and sign-up `none`): for a LAN or a club night; anyone may be anyone, and there is nothing to sign up for.
- **Password** (sign-in `password`, sign-up `password`): choose a password when you sign up; your name and password sign you in. A password is at least 8 characters and uses three of these four kinds: lowercase letters, capital letters, digits and symbols. Change it in the web lobby or with P in the telnet menu.
- **Password by email** (sign-in `password`, sign-up `email`): sign-up asks for your email address and mails you a password that works for 24 hours. The first time you sign in with it, you choose a password of your own; an account nobody signs into within a day is deleted. If you forget your password, answer `reset` at the password prompt and a new temporary password goes to the address on file; your old one keeps working until you use it. Without an address on file, the operator gives you a temporary password.

A password is never sent to the server unless it asks for one, and never shows in a page, an address or a log.

| Protocol | Sign up | Sign in |
|---|---|---|
| Web site | yes, every method: New account | Sign in: name, then each answer the server asks for |
| ParlorNet Play | yes, every method: New account | Sign in: name, then each answer the server asks for |
| Telnet | yes, every method: log in as `new` | name, then each answer the server asks for |
| Native protocol | yes, every method: `REG` (the `INF` / `ASK` / `AUT` dialog) | `USER`, the same dialog (`HELO` does either) |
| IOS | yes, in the client's own window: a free name signs up | name and password |
| FIBS | yes, in the client's window: log in as `guest` | name and password |
| Outland | on the web or by telnet first | name and password (a mailed password: on the web or by telnet first) |
| DOoM | yes: `request name` | `connect name password` |
| GameMaster | on the web or by telnet first | in the ParlorNet rulebook's window: name, then each answer the server asks for |
| NetBoxes | on the web or by telnet first | in the Join dialog: name and password (a mailed password: on the web or by telnet first) |
| batnav | on the web or by telnet first | a message: `/signin name password` (a mailed password: on the web or by telnet first); the name alone on a parlor without passwords |
| Awale | on the web or by telnet first | in Write to group: `!signin name password` (a mailed password: on the web or by telnet first); under name alone, the name in Network setup |
| ICDS | yes, in the terminal: log in as `guest` | name and password |
| NetLuff | on the web or by telnet first | in the chat box: `/signin password` for the name in NetLuff's name box, or `/signin name password` (a mailed password: on the web or by telnet first) |
| Networdz | on the web or by telnet first | in the Chat window: `/signin name password` (a mailed password: on the web or by telnet first) |
| Worthy Opponent | on the web or by telnet first | in the Talk window (F6): `/signin password` for the name from the program's Name menu, or `/signin name password` (a mailed password: on the web or by telnet first) |
| RSCARDS | yes, in the sign-in fields: a new name signs up | in two fields: name, then each answer the server asks for |

After a wrong answer the name is locked for a minute, longer after repeated ones (at most 4 hours). While it is locked, `reset` still gets a new temporary password by email.

Operators have their own console, separate from these protocols.
