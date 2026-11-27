# Tera Term

## History

Takashi Teranishi's Tera Term came out in 1994 for Windows 3.1. Tera Term Pro 2.3 (March 1998) is for Windows 95 and NT. The Tera Term Project continues it as open source, and its 5.x versions run on current Windows with color and UTF-8.

## Getting it

Use 5.x on current Windows, or Pro 2.3 on Windows 95 and NT. Its readme says to use 1.x on Windows 3.1.

## Setting it up

- In New connection, choose TCP/IP, with host `{host}`, service Telnet and TCP port `6502`.
- It emulates a VT100 in an 80 by 24 window by default.

## Known quirks

- Tera Term Pro 2.3 doesn't show the name you type at `Login` or at `Name you want`, because its local echo is off and the parlor leaves the echo to the terminal until you sign in. The name still gets there. Local echo in Setup > Terminal should show it.

## Screenshots

![The PuTTY window titled parlor - PuTTY, showing the start of ParlorNet's title in colored blocks](../../static/img/telnet-title.gif "Every telnet program reaches the same screens. Here PuTTY shows ParlorNet's title page.")
![The lobby in PuTTY, with bob's Checkers game listed under Other tables](../../static/img/telnet-lobby.gif "The lobby lists the tables being played, here bob's game against the bot.")
![Half of a Backgammon board in PuTTY, with a white checker waiting on the bar](../../static/img/telnet-backgammon.gif "A game of Backgammon against the bot is under way, and cora has a checker on the bar.")
![Part of a Grid Words board in PuTTY, with the words CLING and PROVENLY among the colored bonus squares](../../static/img/telnet-gridwords.gif "A game of Grid Words is under way, and the colored squares mark the letter and word bonuses.")
