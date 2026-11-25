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

## Known quirks

Nothing known yet.

## Screenshots

None yet.
