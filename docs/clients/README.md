# Client pages

Each file here is one client's page on the docs site, published as `clients/<id>.html`. The id is the client's id in `adapters/clients.py`, and every client there must have a page here, and every page a client. A test checks both.

## Adding a quirk or fixing a page

Edit the client's Markdown file, here or with the "Edit this page on GitHub" link at the end of its page. Keep the five sections in this order, each with its heading:

1. **History:** who made the program, when, its versions and what it was known for, in a few paragraphs at most. Say where the facts come from and mark what is not known. Don't guess.
2. **Getting it:** anything to say beyond the download links, such as which version to take or what it costs. The links themselves are in `adapters/clients.py`.
3. **Setting it up:** how to point it at a ParlorNet server. Write `{host}` for the server's name, so that the page can show it as *host*, and give the usual port. For a program that connects over a serial port, link to [Serial connections](../serial.md) rather than repeating it.
4. **Known quirks:** what a player will run into, and what to do about it, one item each. Write "Nothing known yet." when nothing is known.
5. **Screenshots:** one line per picture, written as `![What it shows](../../static/img/name.gif "Caption")`, or "None yet.". The words in brackets are the picture's ALT text, for a browser that shows no pictures, and the caption goes under the picture.

Write plain sentences, as you would explain the program to a friend.

## Screenshots

A page has three or four screenshots, or none. Each is a GIF in `static/img/`, named after the client, such as `eclipse-game.gif`. It is under 200 pixels wide and no more than 21 KB, so that pages load quickly on a slow modem. Crop it to the window that matters, or reduce it by a whole number such as a half, and never stretch it. Keep anything personal out of it, such as your own user name or address. Screenshots of a program's own windows are fine to publish.

Telnet programs and web browsers show the same screens, so their pages share a set. Every telnet program's page shows `telnet-title.gif`, `telnet-lobby.gif` and `telnet-backgammon.gif`, and may add one picture of its own that shows something only that program does. The period browsers share the 1997 pages in `web-1997-*.gif`, and the current browsers share ParlorNet Play in `play-*.gif`.

## Adding a client

Read [CONTRIBUTING.md](https://github.com/eric-cgn/parlornet/blob/main/CONTRIBUTING.md) first: before working on a client, read the terms it came with, and work only in ways they allow.


Add its entry to `adapters/clients.py`: its name, year, the systems it runs on, its version, the protocol and port, where to get it, and its ratings once it has been played (below). Then add its page here with the outline above. A client that ParlorNet does not serve yet gets the status `planned` and is listed under Planned on the Clients page.

## Ratings

Every client's page starts with its ratings, one for each platform, processor (or build) and version that has been tried. The ratings live with the client's entry in `adapters/clients.py`, as `Rating`s: the medal, the platform, the processors or build, the version, a few plain sentences of explanation and the date of the test. The marks mean:

- **Gold:** it works fully. A whole game was played with the program itself on a parlor, and anything odd it does has a plain remedy under Known quirks.
- **Silver:** it works once the program itself is patched. Say which patch and why, and link to it. Add-ons and setup, such as a rulebook or a published port, are not patches.
- **Bronze:** it works, but something in play goes wrong or behaves differently from one time to the next. Say what.
- **Unsupported:** ParlorNet examined it and ruled it out. Say why in the rating's explanation, in plain words. Unsupported programs go in `UNSUPPORTED_CLIENTS`, not with the native clients.
- **Not yet rated:** nobody has played a whole game with it on that platform and version yet. Say what has been tested instead, such as scripted clients that behave as the program does.

A rating comes only from a real test with the program itself, on the platform and version it names: on real hardware or in an emulator, a whole game against the bot or another player. When you have played one, add a `Rating` with what you tested, on what and when, and update its Known quirks with anything you ran into. A client marked "untested" loses that mark once it has a rating. Places a client runs on without a rating show as not yet rated by themselves.

## Links, never software

Link to the author's own page when it still exists, and otherwise to a public archive such as Info-Mac, Aminet or the Internet Archive. Never add the program itself, or any part of it, to this repository.

ParlorNet's own programs for period machines, such as its rulebooks for GameMaster and Eclipse 68k Fix, are not in this repository either. Each is built and released in a repository of its own ([eric-cgn/parlornet-gamemaster](https://github.com/eric-cgn/parlornet-gamemaster), [eric-cgn/parlornet-eclipse-fix](https://github.com/eric-cgn/parlornet-eclipse-fix)), and the docs site serves its release files from its own page. Link to that page, as the GameMaster and Eclipse pages do.

## Groups still running

When a group or server still plays with a client or its protocol, add it to `COMMUNITIES` in `adapters/clients.py`: its name, its link, where to connect, and a few plain sentences on what it is, who runs it, what you need and how to join. Connect to it first to check that it is running, and update `COMMUNITIES_CHECKED`. The build puts it in the Community section of the page of every client it serves.
