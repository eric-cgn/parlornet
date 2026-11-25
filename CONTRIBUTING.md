# Contributing to ParlorNet

Contributions are welcome: a new client, a fix to a client's page, a game, a frontend, a test, or a correction to the docs. The guides for each are in the docs: [Adding a game](docs/games-howto.md), [Client pages](docs/clients/README.md) (adding a client, ratings and screenshots), and the protocol references in [docs/protocols.md](docs/protocols.md) and [docs/protocol.md](docs/protocol.md).

ParlorNet's own software for period machines lives in repositories of its own, with its own build: the rulebooks for GameMaster and their installer in [eric-cgn/parlornet-gamemaster](https://github.com/eric-cgn/parlornet-gamemaster), Eclipse 68k Fix in [eric-cgn/parlornet-eclipse-fix](https://github.com/eric-cgn/parlornet-eclipse-fix), and ParlorNet's own programs for 8-bit computers in [eric-cgn/parlornet-8bit](https://github.com/eric-cgn/parlornet-8bit). Changes to those programs go there; their pages on the docs site, and the server's side of what they speak, stay here. The guidance below applies to them too.

## Pull requests

Pull requests go to the `dev` branch. Each one is tested there and squashed into a single commit, and `main` takes only merges from `dev`, which is how a release is made (README.md, Branches and releases). A pull request into `main` from any other branch fails its check.

## Ports to other 8-bit computers

ParlorNet's 8-bit programs share one core, the protocol, the lobby, the table and the ten games, and each machine adds its own screen, input, storage and connection code. The Apple II family (the IIe and IIgs) and the Commodore 64 are done; a Commodore 128 runs the Commodore 64 version, booting it by itself. Ports to other machines are welcome, and these would fit especially well: the Commodore 128 in its own 80-column mode, the Atari 8-bit computers (the same 6502 and compiler, with FujiNet or a serial modem), the ZX Spectrum, the MSX and the Amstrad CPC (Z80, with the z88dk compiler), the Tandy Color Computer (6809, with the CMOC compiler), and CP/M as one text-mode program for many machines. The parlornet-8bit repository describes how a port is built and what it needs before it is merged.

## Names, trademarks and terms

Many of the games ParlorNet plays are old and free for anyone to make, but some of their best-known versions carry trademarks: in their names, their logos, and sometimes in the look of their boards and pieces. ParlorNet uses names of its own for its games (Grid Words, Rummy Tile Classic, Naval Combat, Cannon Fire) and draws them in its own way. Please do the same in anything you contribute: describe a game in your own words, use ParlorNet's names for it, and don't copy a commercial product's names, artwork or look.

Many period programs also came with terms: a license, a shareware notice, a service's terms of use. Some of those terms rule out the kind of study or change that working with ParlorNet would need, and some programs can't be part of ParlorNet at all because of them. Before working on a client, read the terms it came with, and work only in ways those terms allow. Don't accept terms on someone else's behalf, and don't contribute anything learned in a way the terms forbid.

ParlorNet won't accept contributions that infringe trademarks, break agreements, or otherwise run afoul of legal or social obligations, including the respect owed to the people and communities who made and still keep these games alive. Where exactly those lines fall depends on the program, the terms and the circumstances, and the project's maintainers make the final call. If you're not sure, ask before you start, or leave it out. A contribution can be declined or removed later if it turns out to cross one of these lines.

## Other people's software

ParlorNet links to period programs; it never hosts, bundles or redistributes them, and it never includes code, files or artwork from them. Where a program needs a change to play on a parlor, ParlorNet's own patcher may make it in the player's own copy, only where the program's terms allow it. Code under an open-source license may be read for facts about a protocol, but ParlorNet's code is its own, under its MIT license.

## Existing services

Where an original game server is still running, ParlorNet points players to it. ParlorNet exists to let these games be played, not to take players away from anyone.
