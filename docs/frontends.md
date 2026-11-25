# Frontends

A frontend is the program a player uses to reach a parlor: a game program of its day speaking its own protocol (a native client), a web browser or a telnet program (the fallback), or a program written for ParlorNet's own protocol. Each frontend speaks one protocol, and on the server a server extension speaks it back and passes the player's moves on to the core.

ParlorNet is for native clients: retro game programs first released about 25 to 50 years ago (1977 to 2000 for the 2027 release), and new native-client work goes there. Browsers and telnet programs are the fallback for any computer, old or new, and the rule does not apply to them. [clients.md](clients.md#the-retro-window) has the policy.

The list of frontends lives on the docs site's [Clients page](http://docs.parlornet.org/), game by game: for each game, a table of its native clients and then every browser and telnet program (the fallback), with the year, the systems each runs on, its protocol's key and a link to its own page, and a finder by platform and processor. Every client has its own page, with its history, setup, known quirks and screenshots (for example [Reversi The Eclipse](http://docs.parlornet.org/clients/eclipse.html)). What every browser needs is on the [Browsers page](http://docs.parlornet.org/browsers.html), and what every telnet program needs on the [Telnet programs page](http://docs.parlornet.org/telnet.html). It is built from `adapters/clients.py` and the pages in [clients/](clients/README.md).

- The protocols, their server extensions, how to connect and sign in, and their history: [protocols.md](protocols.md). The web site is [HTTP and HTML 3.2](protocols.md#web-site), telnet is [telnet](protocols.md#telnet), and a program of your own speaks the [native protocol](protocols.md#native-protocol).
- Where each program comes from, with its license, the evidence it meets the site's needs, and the games no period client reaches yet: [clients.md](clients.md).
- What each game is and the rules played here: the web site's [Games page](http://parlornet.org/games.htm).
