# ParlorNet for the Apple IIe

ParlorNet for the Apple IIe plays the parlor's games on an Apple IIe, in Hi-Res color or monochrome, over a Super Serial Card or an Uthernet II card. It speaks the parlor's native protocol (PARLORNET/1, [protocol.md](protocol.md)) on the parlor's native port, 6501. The parlor deals, judges every move and runs the bot, so the Apple only shows what the parlor sends and sends what the player chooses. The program's source and its build are in a repository of their own, parlornet-apple2; this page is the player's guide.

All ten games are there: Backgammon, Cannon Fire, Checkers, Dots and Boxes, Five in a Row, Grid Words, Mancala, Naval Combat, Reversi and Rummy Tile Classic.

## What you need

- An Apple IIe with 64K or more (an enhanced IIe, or an unenhanced one; the program uses only the 6502's own instructions).
- A Super Serial Card, or an Uthernet II card. An AppleMouse II card is optional; without one, the arrow keys move the pointer.
- A color or a monochrome monitor. Open-Apple-D, or D on the start screen, switches between the two looks, and the parlor remembers your choice for the next time you sign in.

## The disk

The program's build (`tools/build_apple2.sh` in parlornet-8bit) makes `build/parlornet.dsk`, a 140K disk in DOS 3.3 sector order. It boots by itself, with no operating system on it, and holds the program and every game. Write it to a floppy with the tool you use for disk images (ADTPro, for example), or use it as it is with a floppy emulator or in an emulator. The program reads each game from the disk when you sit down at one of its tables, so leave the disk in the drive.

To run the program from your own ProDOS disk or hard drive instead, see ProDOS below.

## Connecting

The program opens with its title screen and waits there until you press a key, then goes on to the start screen. The start screen offers P (connect and sign in), C (the console), S (the settings) and D (the display). The settings have the connection: the Super Serial Card's slot, speed, bits and parity, flow control and echo and the modem's dialing, or the Uthernet II's slot and network addresses.

### A serial line

Set the Super Serial Card's speed to match the other end (9600 or 19200, 8N1, is right for most). The program takes the line to be a modem, and P dials it:

1. It sends the init string, `ATZ` at first, and waits a few seconds for OK. A line that says nothing goes on to the dial all the same. An empty init string sends nothing.
2. It dials `ATDT` and the number, `ATDTparlornet.org:6501` at first, and waits up to a minute for CONNECT, showing what the modem says.
3. On CONNECT it sends its first line, `PARLORNET/1`, at once, so the parlor's 20 seconds for it are never a race, and goes on to the sign-in.

If the modem answers NO CARRIER, BUSY, NO DIALTONE, NO ANSWER or ERROR, or no CONNECT comes within the minute, the program says so and offers to dial again (Return), the console (Open-Apple-C) or the start screen (Esc). Esc while it dials sends a Return, which stops a modem's dial, and goes back to the start screen.

The modem's settings are under the serial card's: the **init string**, `ATZ` at first, or anything your modem needs, such as `AT&F` (key 6; empty sends nothing), the **number**, `parlornet.org:6501` at first (key 7), and **Pulse dial** (key 8), off at first: the program dials `ATDT` and the number, or `ATDP` with Pulse dial on. Settings saved by an older version are reset to the defaults. A number that starts with AT is sent as the whole dial command, ignoring Pulse dial. On a Zimodem, set the number to `ATD"parlornet.org:6501"`, which dials the native port plainly ([serial.md](serial.md#a-wifi-modem)). An empty number is for a line with no modem, and P then sends the first line straight away.

When the session ends (Open-Apple-Q), the program says goodbye to the parlor, the parlor hangs up and the modem says NO CARRIER. A modem that stays connected gets the Hayes escape, `+++` with a second of quiet on each side, then `ATH`. The program sends `+++` only then, never during a session. If the connection is lost while you play, the program says so, and Return dials again.

What the card is connected to decides what to set:

- **A WiFi modem or a modem emulator** (tcpser, or an emulator's modem): press P.
- **A Unix shell login:** install ATZ and press P (below).
- **A bridge to the parlor's port**, such as ser2net or socat on a computer, or a null-modem cable to a machine that relays the line to the parlor's native port: empty the number in the settings, then press P.

[serial.md](serial.md) covers every way over a serial line, with the cable.

### Through a Unix shell

If the serial line leads to a Unix shell (a login on a Linux machine or a Raspberry Pi, say), the shell can play the modem with ATZ, a short program installed under that name on the shell's PATH ([serial.md](serial.md#atz-for-programs-that-dial)). Log the line in once from the console (C), then go back to the start screen with Open-Apple-Q and press P. The program's `ATZ` reaches the shell, which echoes it and runs ATZ, and ATZ answers OK, as a modem answers a reset. The program then dials, ATZ connects to the parlor and says CONNECT, and the program signs in, with no console step and no Open-Apple-P race. After Open-Apple-Q, ATZ waits for the next dial, and P dials again. To get the shell prompt back, press C for the console and Control-C. If a connection is still up there, hang it up first as with a modem: wait a moment, type `+++`, wait, then `ATH` and Return.

The shell can also open the connection by hand with netcat. Press C for the console, log in, and type:

```
stty raw -echo; nc parlornet.org 6501; stty sane
```

Then press Open-Apple-P instead of Return. Open-Apple-P sends the command's Return and goes straight into ParlorNet mode, whose first line follows at once; the parlor closes a connection whose first line has not come within 20 seconds, so pressing Return first and Open-Apple-P later can be too slow. `stty raw -echo` passes every byte through unchanged and stops the shell from echoing what the Apple sends, nc connects to the parlor's native port, and `stty sane` puts the terminal back when the connection ends. When you close the connection from the Apple (Open-Apple-Q), the parlor hangs up, nc ends and you are back at the shell prompt in the console. OpenBSD's nc (the usual one on Debian and Ubuntu) and Nmap's ncat end only when you press a key after that, and the key is lost; the traditional nc and `busybox nc` end at once ([serial.md](serial.md#by-hand-with-nc)).

In the console, Open-Apple-P always goes into ParlorNet mode on the line as it is, without dialing.

### An Uthernet II card

In the settings press C until the connection says Uthernet II, then set the card's slot, this Apple's address, the netmask, the gateway, the DNS server, the parlor's name (or its address) and the port (6501). P looks the name up and connects. The program drives the card's W5100 chip itself, so nothing else is needed. The addresses are kept only while the program runs, unless you run it from ProDOS (below), which saves the settings.

## Signing in

The program sends `PARLORNET/1` and waits for the parlor's greeting. Type your name, or `new` for a new account, and answer the questions the parlor asks: a password, or an email address and the temporary password it sends you, whatever the parlor's way of signing in is. Open-Apple-C goes to the console at any time and Open-Apple-P comes back.

## The lobby

The lobby lists your games, invitations and the tables waiting for a player. New game asks which game, which rules and how many players where there is a choice, and whom to play: the bot, anyone who joins, or a player you name. Refresh asks the parlor again; the lobby also refreshes itself every half minute.

## At a table

The pointer moves with the arrow keys from one thing you can click to the next, and Return or the space bar clicks. With a mouse, point and click. Every table has Lobby, Resign, Chat and Keys along the bottom, and these keys:

- Open-Apple-T: say something to the table. Messages from the others appear on the line above the status line. So does what each player who sits down plays with, such as "bob joined with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus" (the parlor's `CLIENT` event), and "bob now plays with ..." when they come back from another program.
- Open-Apple-R: resign (the program asks first).
- Open-Apple-D: color or monochrome.
- Open-Apple-C: the console; Open-Apple-L: fetch the table again.
- Esc: back to the lobby. Leaving a table never resigns; the seat waits and the turn clock runs.
- Keys shows these on screen; pressed again, it shows the game's own keys.

The screens show the Open-Apple key as its apple symbol. On an older Apple IIe, which has no MouseText, the text screens write it as OA- instead.

While it is someone else's turn the program asks the parlor every few seconds what has happened.

### The games

**Backgammon.** Your home board is at the bottom right; H moves it to the bottom left (the board is mirrored, and the parlor remembers the choice as the preference `bghome`). Roll, then click the point to move from and the point to move to, or the tray to bear off. The points you can reach are marked. Done ends the turn and Undo takes a step back (D and U on the keyboard; R rolls). The dice are drawn on your side, four for doubles, and each is blanked as it is played. The doubling cube sits on the bar showing 64 until someone doubles, then in the tray on its owner's side.

**Cannon Fire.** The buttons under the arena change the angle and the power by 5; the keys , and . change the angle by 1, and - and = the power. The line from your tank shows the aim. Fire shoots (F); Move< and Move> drive your tank before the shot, four times a game. The last shell's path stays on the screen until the next shot.

**Checkers.** Your men start at the bottom. Click a piece, then the square it moves to; the squares it can reach are marked. After a jump that must go on, the same piece stays chosen. Done (D) and Undo (U) finish or rethink the turn, and Offer draw is in the panel.

**Dots and Boxes.** Click a dot, then the dot next to it, to draw the line between them. A box you close is filled with your color and letter, and you move again.

**Five in a Row.** Click an empty point. Black (seat 0) is a ring and White a solid stone; the last stone has a mark in the middle, and the winning line is framed. Boards up to 15 by 15 are played on the Apple; a larger one can be played on the web or by telnet.

**Grid Words.** Click the empty square your word starts on (click it again for down), then type the letters from your rack, or click the rack's tiles. The letters fill the empty squares in turn, and the tiles already on the board join the word. As each tile is laid the parlor tells you the score, or why the play would be refused. Delete takes the last tile back, Clear all of them, and Return or Play sends the word. Swap: click the tiles to exchange, then Swap again. A tile shows its letter only, in the middle of the tile. Rest the pointer on a tile, on the board or the rack, and the message line gives its points; Open-Apple-V shows the points of every letter and the blank, as the table's rules give them, until you press a key. In color a tile is a green square with its letter in black, orange while you are laying it and violet for a blank. In monochrome a tile is a white square with its letter in black and its upper-left and lower-right corners cut away, so the tiles of a word run together as they do in color; while you are laying a tile it shows just those two corners around its letter. A blank's letter is in lower case. In color, word squares are orange and letter squares violet, a full square for a triple and a diamond for a double, with a legend beside the board; in monochrome every square carries its name.

**Mancala.** Your row is along the bottom with your store on the right. Click a pit, or press 1 to 6.

**Naval Combat.** While placing, the large grid is your own waters: click one end of the ship and then the other (the squares where the other end can go are marked), or Random places the rest; Ready when the fleet is complete. Then the large grid is the other player's waters, where a click fires, and the small grid on the right is yours.

**Reversi.** Your legal squares are marked on your turn; click one.

**Rummy Tile Classic.** The table's sets are rows across the top and your rack is at the bottom; each tile shows its color letter (R, B, K, Y, or J for a joker) and number, with a bar in its color on a color monitor. Click rack tiles to choose them (several at once), then click a set to add them to it, or New to make a new set. Click a tile on the table, then another set to move it there, New for a new set, Split to split its set before it, or your rack to take it back (a tile you played this turn). A set marked ! is not valid yet and one marked * was made or changed this turn. Done ends the turn: if the table is valid it counts, otherwise everything goes back and you draw a tile. Undo takes back a step and Reset the whole turn.

## ProDOS

The program can also run from ProDOS, from a floppy or a hard drive, where it saves its settings. ProDOS itself is not part of ParlorNet; use your own copy. The build also makes `build/prodos/`: `PARLOR.SYSTEM`, the start-up file, and `PARLOR.DAT`, the program and its games, together with `parlor-files.po`, a ProDOS volume that holds just those two files. Copy both files into one directory of your ProDOS disk and run `PARLOR.SYSTEM` from your program selector, or name it as the first system file on the disk so ProDOS starts it.

Running from ProDOS needs a IIe with 128K (an extended 80-column card), because the program keeps its games in the auxiliary language card while ProDOS stays in the main one. It disconnects the /RAM disk, as Apple's notes for such programs ask, so anything kept on /RAM is lost, and /RAM stays disconnected until the Apple is started again. The settings are written to `PARLOR.CFG` beside the program whenever you leave the settings screen, and read when it starts. Under ProDOS the settings can also turn the title screen off (T). Open-Apple-Q on the start screen returns to ProDOS.

## The Apple IIgs

On a IIgs the program also comes as a GS/OS application, PARLORNET, that connects through Marinetti, the IIgs TCP/IP stack, over whatever network Marinetti is set up for: an Ethernet card, a SLIP or PPP line on a serial port, or MacIP. You need System 6.0.1 and Marinetti 3.0, installed and set up in its TCP/IP control panel, and 64K free in one bank. Copy PARLORNET and PARLOR.DAT from the 800K disk into one folder, or run them from the disk, and open PARLORNET from the Finder. Press S to set the parlor's name and port, then P: the program starts Marinetti, connects to the network if it isn't connected yet, looks up the parlor and signs in. The games, keys and mouse are as on the IIe, the settings (with the title screen's T) are kept in PARLOR.CFG beside the program, and Open-Apple-Q on the start screen quits to the Finder. The IIe disk and PARLOR.SYSTEM also run on a IIgs, with a Super Serial Card or an Uthernet II in a slot.
