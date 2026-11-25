# xfibs

## History

xfibs is an X11 and Motif client for FIBS, the First Internet Backgammon Server, by Torstein Hansen with patches by David Eggert and Mike Quinn. The manual page of version 0.8 is dated 22 August 1995, and no earlier release was found. Its source was spread through the X Consortium's contributed software, and its license says it is "freely distributable without licensing fees".

## Getting it

It comes as source only, so you build it yourself with a C compiler and Motif.

## Setting it up

- In `~/.xfibsrc`, set `Host {host}` and `Port 6505`. With a user name there too, it logs in by itself.
- On ParlorNet you can play Backgammon matches against anyone on the server.

## Known quirks

- On a current Linux, the build needs two small changes: drop the optional SOCKS lines from the Imakefile, and replace `sys_errlist` with `strerror()`. With those it builds on Debian 12 against `libmotif-dev`.
- Games here are unrated, so ratings show as 0.00. Redoubles and stored messages are not offered.

## Screenshots

None yet.
