# Telnet (Windows)

## History

Windows 95 and NT came with Microsoft Telnet 1.0, a small window program. Windows 2000 and later have a console telnet instead.

## Getting it

From Windows Vista on, turn on Telnet Client in Windows Features (Turn Windows features on or off).

## Setting it up

- Type `telnet {host} 6502` in the Run box or at a command prompt.
- The console telnet takes `/t ansi` or `/t vt100` to set the terminal type.

## Known quirks

- The Windows 95 and NT program does VT100 but has no color and no line characters, so pick the `ansi` or `plain` display.
- Whether it sends the arrow keys has not been checked. If they do not move, choose the movement keys on the Telnet programs page.

## Screenshots

![The PuTTY window titled parlor - PuTTY, showing the start of ParlorNet's title in colored blocks](../../static/img/telnet-title.gif "Every telnet program reaches the same screens. Here PuTTY shows ParlorNet's title page.")
![The lobby in PuTTY, with bob's Checkers game listed under Other tables](../../static/img/telnet-lobby.gif "The lobby lists the tables being played, here bob's game against the bot.")
![Half of a Backgammon board in PuTTY, with a white checker waiting on the bar](../../static/img/telnet-backgammon.gif "A game of Backgammon against the bot is under way, and cora has a checker on the bar.")
![Part of a Grid Words board in PuTTY, with the words CLING and PROVENLY among the colored bonus squares](../../static/img/telnet-gridwords.gif "A game of Grid Words is under way, and the colored squares mark the letter and word bonuses.")
