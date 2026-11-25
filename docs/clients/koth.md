# KOTH (King of the Hill)

## History

KOTH, King of the Hill, is an artillery game for Unix and Linux in which tanks on a hilly landscape take turns, or fire all at once, lobbing shells at each other. It draws through LibGGI, so it runs under X11 and on other displays. A pre-release, 0.3, came out in June 1999, Debian packaged 0.7.6 in August 1999, and 0.8.0, from February 2003, is the last version. It is free software under the GPL. Players ran their own server, `koths`, on TCP port 8086, and the clients connect to it.

ParlorNet read the source of 0.7.6 and 0.8.0 to see whether its Cannon Fire could be played from KOTH. Every KOTH client flies each shell and digs each crater with its own physics, and the server passes on only the shooter, the angle, the speed and the weapon. The server can reset the landscape and the tanks at any time, but it cannot say where a shell went. On a parlor the server decides every shot by Cannon Fire's rules, so a KOTH player would watch a shell land in one place and then see the world jump to where the parlor put it. The work is tabled in the [backlog](../backlog.md), with the options that were set out.

## Getting it

The project's page has its news, and ibiblio keeps the 0.8.0 source. ParlorNet links to them and serves nothing of KOTH.

## Setting it up

KOTH does not play on a parlor, for the reason under History. Its players can play Cannon Fire on the web, by telnet, in ParlorNet Play or with ParlorNet's Cannon Fire rulebook for GameMaster.

## Known quirks

- Versions 0.7.6 and 0.8.0 refuse each other, since each checks that the server speaks its own protocol version.
- By its source, a KOTH server and its clients send numbers in the byte order of the machine they were built on, so KOTH on a PC and KOTH on a SPARC or PowerPC machine would not understand each other. This has not been checked on the wire.
- In take-turns games the server gives each shot 30 seconds; a player who does not fire loses the turn.

## Screenshots

None yet.
