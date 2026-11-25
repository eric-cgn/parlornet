# Networdz 2.0

## History

Networdz is a crossword tile game for Windows 3.1 and later by Lance Frohman of San Francisco. Version 2.0 has files dated October 1996 and was US$12 shareware, sold on CD collections from 1996 to 1998. One to four people play, on one PC or over a network or the Internet, in 16 languages. Version 2.0 was the first to play over a network: two to four PCs over a LAN or the Internet, through Winsock. It also plays against the computer and has a chat window, a timed option and tournament rules, and its configuration files change the board, the tiles and the language.

There is no server: one player's copy is the host. It chooses the rules, shuffles the bag, checks every word against its own dictionary, and connects out to each remote player's copy. ParlorNet answers as the remote copies, so a Networdz player hosts a game whose other seats are played at the parlor: on the web, by telnet, in ParlorNet Play, on any other Grid Words client, or by the bot ([protocols guide](../protocols.md#networdz)).

## Getting it

The archive below is from Walnut Creek's Giga Games 5 CD of June 1997. It holds the program, its help file, the English and Spanish dictionaries and the configuration files. You don't need its installer: copy the files into a folder such as `C:\NETWORDZ`.

## Setting it up

You need Windows 3.1 or later with Winsock. Make your account on the web site or by telnet first.

1. Close Networdz. Add the lines under `NETWORDZ.INI` below to that file in your Windows folder (make it if it is not there): the parlor's port, `6516`, and the rules the parlor does not play, turned off.
2. Save the text under `PARLOR.CFG` below in the Networdz folder: the standard board, the 100 standard tiles and their points, and racks of 7.
3. Start Networdz, choose Options, Configuration and open `PARLOR.CFG`.
4. In Options, Players, make Player 1 a Person with your name, and make Player 2 (and 3 and 4, for more players) Remote, each with `{host}` (the parlor's name or address) as its IP name. Set the others to none.
5. In Options, Communicate, keep "Local / Dial Mode", click Connect, then OK. The Remote players show as ParlorNet.
6. Choose Options, Chat, where the parlor's lines come. Type `/signin NAME PASSWORD` and press Enter. If the server emailed you your password, sign in once on the web or by telnet first, where you choose a password of your own.
7. Type `/invite NAME` for each player you want, `/invite bot` for the bot (once for each seat it takes), or `/open` to let anyone sit down. Each Remote player takes the name of the player who sits there.
8. When everyone is seated, choose Game, New Game.

`NETWORDZ.INI`:

```
[Remote]
Internet Protocol Port=6516
[Rules]
Trade Blanks=0
Exchange Four of a Kind=0
Timed=0
Tournament Rules=0
```

`PARLOR.CFG`:

```
[Board]
Squares per side=15
row 1=T..d...T...d..T
row 2=.D...t...t...D.
row 3=..D...d.d...D..
row 4=d..D...d...D..d
row 5=....D.....D....
row 6=.t...t...t...t.
row 7=..d...d.d...d..
row 8=T..d...C...d..T
row 9=..d...d.d...d..
row 10=.t...t...t...t.
row 11=....D.....D....
row 12=d..D...d...D..d
row 13=..D...d.d...D..
row 14=.D...t...t...D.
row 15=T..d...T...d..T

[Tiles]
Blank Count=2
Letters=ABCDEFGHIJKLMNOPQRSTUVWXYZ
Tiles per rack=7
A Tiles=9
A Points=1
B Tiles=2
B Points=3
C Tiles=2
C Points=3
D Tiles=4
D Points=2
E Tiles=12
E Points=1
F Tiles=2
F Points=4
G Tiles=3
G Points=2
H Tiles=2
H Points=4
I Tiles=9
I Points=1
J Tiles=1
J Points=8
K Tiles=1
K Points=5
L Tiles=4
L Points=1
M Tiles=2
M Points=3
N Tiles=6
N Points=1
O Tiles=8
O Points=1
P Tiles=2
P Points=3
Q Tiles=1
Q Points=10
R Tiles=6
R Points=1
S Tiles=4
S Points=1
T Tiles=6
T Points=1
U Tiles=4
U Points=1
V Tiles=2
V Points=4
W Tiles=2
W Points=4
X Tiles=1
X Points=8
Y Tiles=2
Y Points=4
Z Tiles=1
Z Points=10
```

The Chat window takes the [shared chat commands](../chat-commands.md):

- `/invite NAME` invites a player to your table, who may be on the web, by telnet or anywhere else, and `/invite bot` invites the bot.
- `/open` opens your table to anyone, and `/close` closes it before the game starts.
- `/resign` resigns the game, after a second `/resign` to confirm.
- `/list` lists your games and the open tables, `/who` lists the players online, `/note` reads or adds a note to the game, `/motd` shows the message of the day, `/help` lists the commands and `/quit` signs out.

Anything else you type is chat with the other players.

## Known quirks

- Your PC is the host, so your copy of Networdz deals the tiles and checks the words with its own dictionary. The parlor marks the table `integrity=false` ("the host's Networdz deals the tiles"): it cannot keep the game's secrets or decide it. Every rack is shown on every copy of Networdz anyway. The other players see the mark in the table's notes.
- A word your dictionary does not have is refused for the other players too, even when the parlor's word list has it. They are told in the table's chat and play again, and the table refuses that word from then on, the bot's too.
- Your own words are taken as your Networdz takes them.
- The parlor plays the standard board and tiles only. With Networdz's own default board (17 by 17, 128 tiles) or another configuration the chat asks you to load `PARLOR.CFG`, and the game waits.
- Trade Blanks, Exchange 4 of a kind, Timed and Tournament Rules are not played here. The chat asks you to turn them off (Options, Rules) and to start a New Game.
- Every Remote player must be the parlor; no Computer players or second Person.
- Networdz's own New Game waits, with "... is picking a tile to start", until every seat at your table is taken. A player who sits down then shows as ParlorNet until that game is over.
- The parlor's own lines in the chat come from a seat nobody sits in, which Networdz shows as ": text", or before anyone sits down, as "ParlorNet: text".
- When a game ends at the parlor first (a resignation, or a turn clock run out), the chat says so; choose Stop Game.
- Stop Game, or quitting Networdz, leaves the table running at the parlor: finish it on the web or by telnet, or `/resign` it. Networdz cannot load a game back from the parlor.
- Each game needs its own table: `/invite` the players again, then New Game.

## Screenshots

![Networdz 2.0 at the end of a game it hosted against the bot through the parlor, under Wine](../../static/img/networdz-game.gif)
