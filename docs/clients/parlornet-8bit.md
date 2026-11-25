# ParlorNet for the Apple IIe, IIgs and C64

## History

ParlorNet wrote these programs in 2026 for its own parlors, in its own repository, [eric-cgn/parlornet-8bit](https://github.com/eric-cgn/parlornet-8bit). Version 1.0, for the Apple IIe and IIgs, came out in November 2026. Version 2.0 followed the same month and added the Commodore 64, whose program a Commodore 128 runs too. The machines share one core, with the protocol, the lobby, the table, the chat, the dialer and the ten games' rules, and each adds its own screen, keys, disk and connection code.

All ten games are there: Backgammon, Cannon Fire, Checkers, Dots and Boxes, Five in a Row, Grid Words, Mancala, Naval Combat, Reversi and Rummy Tile Classic, in color or monochrome. The parlor deals, judges every move and runs the bot, so the computer only shows what the parlor sends and sends what the player chooses.

## Getting it

Each release has four files: `parlornet.dsk` boots an Apple IIe by itself, `parlor-files.po` holds the program for a disk with your own ProDOS, `parlornet-gs.po` is the Apple IIgs program for GS/OS with Marinetti, and `parlornet.d64` is the Commodore 64's 1541 disk. Write a disk with the tool you use for disk images (ADTPro for the Apple, OpenCBM for a 1541), or use it as it is with a floppy emulator, an SD2IEC, an Ultimate 64 or an emulator.

## Setting it up

The programs speak ParlorNet's own protocol, on the parlor's native port, `{host}:6501`.

**Apple IIe.** You need 64K or more and a Super Serial Card or an Uthernet II card. Boot `parlornet.dsk` and press S for the settings. With the serial card, the program takes the line to be a modem: set the number to `{host}:6501`, and press P to dial and sign in. With an Uthernet II, choose it in the settings, give this Apple's address, netmask, gateway and DNS server and the parlor's name, and press P. The [Apple IIe guide](../apple2.md) covers every setting, ProDOS and the console, and [Serial connections](../serial.md) covers WiFi modems, a Unix login and the cable.

**Apple IIgs.** Install and set up Marinetti 3.0 for your network first; it is not part of ParlorNet. Run PARLORNET from `parlornet-gs.po`, press S to set the parlor's name and port, `{host}` and `6501`, and press P. On a IIgs with a Super Serial Card or an Uthernet II, the Apple IIe disk also runs as it is.

**Commodore 64 and 128.** You need a SwiftLink-compatible modem on the expansion port, or a Supra 2400-compatible one on the user port, such as most WiFi modems for the C64. Type `LOAD"*",8,1`, or switch a C128 on with the disk in drive 8. Press S to choose the modem and set the number to `{host}:6501`, then press P to dial and sign in. On a Zimodem, set the number to `ATD"{host}:6501"`, quotes and all, so that the modem connects plainly.

## Known quirks

- The parlor hangs up when the first line of the protocol has not come within 20 seconds. When you connect by hand from the console, go into ParlorNet mode straight away.
- A Zimodem's ATDT asks for a telnet connection, which garbles the native port. Dial with `ATD` and the host in quotes, as Setting it up says.
- On a stock 1541 the C64 program takes about 37 seconds to show its title and about 30 more the first time it loads the lobby. A faster drive or an SD2IEC takes less.
- The Apple's self-booting disk keeps its settings only until the computer is switched off. Run it from ProDOS to keep them.

## Screenshots

None yet.
