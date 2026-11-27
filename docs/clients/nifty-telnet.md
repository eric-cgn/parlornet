# NiftyTelnet

## History

Chris Newman's NiftyTelnet 1.1 is from 1996. It is freeware for System 7 Macs and offers "complete VT102 emulation".

## Getting it

The Info-Mac copy is version 1.1.

## Setting it up

- It needs MacTCP or Open Transport.
- NiftyTelnet has no port field. Choose New Connection and type the host name, a colon and the port in the Hostname field, as in `{host}:6502`. With a space instead, it says it can't find a host by that name.

## Known quirks

- It reports itself as a VT102, so the parlor sends plain ASCII boards in black and white.

## Screenshots

![The PuTTY window titled parlor - PuTTY, showing the start of ParlorNet's title in colored blocks](../../static/img/telnet-title.gif "Every telnet program reaches the same screens. Here PuTTY shows ParlorNet's title page.")
![The lobby in PuTTY, with bob's Checkers game listed under Other tables](../../static/img/telnet-lobby.gif "The lobby lists the tables being played, here bob's game against the bot.")
![Half of a Backgammon board in PuTTY, with a white checker waiting on the bar](../../static/img/telnet-backgammon.gif "A game of Backgammon against the bot is under way, and cora has a checker on the bar.")
![NiftyTelnet's New Connection dialog with the parlor's address and :6502 in the Hostname field](../../static/img/nifty-telnet-new-connection.gif "NiftyTelnet has no port field, so the port goes after the host name with a colon.")
