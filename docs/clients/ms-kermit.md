# MS-DOS Kermit

## History

Columbia University's Kermit for the PC dates from 1982. TCP/IP has been built in since 3.11 (1991), and ANSI color and IBM PC line drawing since 3.14 (1995). Version 3.15 (1997) is the last. The Kermit Project has released it under the Revised 3-Clause BSD license since July 2011.

## Getting it

The Kermit Project's page has the manual. Its own archive links did not answer when checked, so the program comes from a copy of that archive.

## Setting it up

- TCP/IP goes through a packet driver for your network card.
- Then type `telnet {host} 6502`.
- Without a network card, Kermit's serial terminal reaches port 6502 through a WiFi modem or a login on a Unix machine ([Serial connections](../serial.md)).
- Its ANSI emulation draws the `pc` display's line characters on the PC's own screen.
- Over a modem line Kermit sends no terminal type, so the parlor starts its plain display unless you press Enter within about three seconds of CONNECT. Press Enter, then choose 1 for the IBM PC display.

## Known quirks

- The name you type at `Login` doesn't show, because the parlor leaves the echo to the terminal until you sign in. The name still gets there.
- Through DOSBox's emulated modem, a few stray characters showed around the first prompts, because the modem passed the parlor's telnet bytes on as they were. A WiFi modem that handles telnet may not show them.

## Screenshots

![The PuTTY window titled parlor - PuTTY, showing the start of ParlorNet's title in colored blocks](../../static/img/telnet-title.gif "Every telnet program reaches the same screens. Here PuTTY shows ParlorNet's title page.")
![The lobby in PuTTY, with bob's Checkers game listed under Other tables](../../static/img/telnet-lobby.gif "The lobby lists the tables being played, here bob's game against the bot.")
![Half of a Backgammon board in PuTTY, with a white checker waiting on the bar](../../static/img/telnet-backgammon.gif "A game of Backgammon against the bot is under way, and cora has a checker on the bar.")
