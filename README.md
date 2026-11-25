# ParlorNet

Turn-based game server for any computer, old or new: network multiplayer games for any machine from an 8-bit micro up.

ParlorNet brings back the protocols of old game servers. Festivals, events and private groups can run one server for many games, and people can play on it with the original game programs. For the 2027 release, "retro" means game programs first released from 1977 to 2000, about 25 to 50 years back, and new native-client work targets that window; web browsers and telnet programs are the fallback for any computer (docs/clients.md). FIBS is still running at fibs.com (telnet fibs.com 4321).

Home: https://github.com/eric-cgn/parlornet (the source; run your own server, or contribute: see CONTRIBUTING.md, including its notes on trademarks and other programs' terms).

The docs site is http://docs.parlornet.org/. It lists the programs that play each game on each kind of computer, describes every protocol, and has these docs as web pages. `tools/build_docs.py` builds it.

- **Server** (Python): accounts, lobby, tables, turn handling. Deployable by anyone. Knows no game rules.
- **Plugins**: each game runs out of the core's process: Python plugins together in one plugin host, others (any language) in a process of their own, each started when needed and stopped when idle. See docs/plugins.md; adding a game (plugin, type spec, web and telnet frontend module, tests): docs/games-howto.md.
- **Protocol**: line-based ASCII, polling-friendly. See docs/protocol.md; for writing a frontend of your own, the guide with sample sessions for every game is docs/pnp.md.
- **Server extensions**: separate programs beside the server, each speaking one protocol to players' frontends (web, ParlorNet Play, telnet, IOS, FIBS, Outland, DOoM, GameMaster, NetSprocket, batnav, Awale, Networdz, NetLuff and Worthy Opponent); in the code, the `adapters/` package. See docs/adapters.md.
- **The bot**: a player for every game that anyone can invite to a table, a client of the core in `bot/`. See docs/bot.md.
- **Frontends**: the programs players use: a browser, a telnet program, or a game program of its day speaking its own protocol (a native client). See docs/frontends.md and the docs site's Clients page.
- **ParlorNet's own classic Mac software**: built and released in repositories of its own, each with its own build and release workflow. ParlorNet's rulebooks for GameMaster and their installer are in [eric-cgn/parlornet-gamemaster](https://github.com/eric-cgn/parlornet-gamemaster), and Eclipse 68k Fix, the patcher for Reversi The Eclipse on 68k Macs, is in [eric-cgn/parlornet-eclipse-fix](https://github.com/eric-cgn/parlornet-eclipse-fix). The docs site serves their release files over plain HTTP, each pinned by its SHA-256 in `docsite/downloads.json`. ParlorNet Play, the browser client, is part of the server (`adapters/play/`).
- **Docs site**: `python3 tools/build_docs.py` builds it into `build/docs-site`. On every push to main, `.github/workflows/docs.yml` builds it and publishes it to GitHub Pages at http://docs.parlornet.org/. See docs/site.md.

Games (original implementations of public rules, generic or original names; the web site's `/games.htm` describes each one):
Reversi first: the simplest game, used to prove the server, plugin interface, typed views, the telnet extension (one telnet server for all games), custom clients and a game-network extension (the Othello-server protocol). Then Backgammon, Grid Words and Cannon Fire as the harder test cases (dice and staged turns; sparse boards, a hidden rack and a huge move space; terrain heightmap). Then Dots and Boxes, Five in a Row, Mancala, Checkers, Naval Combat, and last Rummy Tile Classic (the most complex).

Supported protocols and their usual ports (6500 HTTP 1997/2027, 6501 native, 6502 telnet, 6503 IOS, 6505 FIBS, 6506 Outland, 6507 DOoM, 6508 GameMaster, 6512 NetBoxes over NetSprocket, 6513 ParlorNet Play behind an HTTPS proxy, 6514 batnav, 6515 Awale over IRC, 6516 Networdz, 6517 NetLuff, published as 1744 too, 6518 ICDS for xcheckers, 6519 Worthy Opponent by modem, 6520 RSCARDS by modem or serial line): docs/protocols.md; each server extension declares its protocol to the core, and a server's `/protocols.htm` lists what it speaks now; the server extensions that serve them: docs/adapters.md. Admin menus: port 6510 (docs/accounts.md). Container: Dockerfile; parlornet.org's deploy files: deploy/. Specs: docs/types/. License: MIT.

Daemon: `parlord`. Wire banner: `PARLORNET/1`. Each deployment is "a parlor"; federated parlors make up ParlorNet.

## Run your own parlor

ParlorNet is one Docker container. Get the source from https://github.com/eric-cgn/parlornet, build it from its top directory and start it:

```sh
docker build -t parlornet .
docker run -d --name parlornet --restart unless-stopped \
    -p 6500-6505:6500-6505 -p 6506:4000 -p 6507-6508:6507-6508 -p 6512:6512 -p 6514:6514 -p 6515-6517:6515-6517 -p 1744:6517 -p 6518:6518 -p 6519:6519 -p 6520:6520 \
    -p 127.0.0.1:6510:6510 -v parlornet:/data parlornet
docker logs parlornet
```

On first launch the log says the parlor is not set up yet and gives a one-time setup password. Set it up from the same machine with `telnet 127.0.0.1 6510` (from elsewhere, through SSH: `ssh -L 6510:127.0.0.1:6510 you@host`, then `telnet 127.0.0.1 6510`). The admin menus ask for the setup password, then a permanent admin password, and then offer the server name, the welcome message, the sign-in method, email, games, server extensions, accounts and tables. Port 6510 is the admin port; publish it on the host's loopback as above, not to the world (it asks for the admin password either way).

Players use 6500 (web), 6501 (native protocol), 6502 (telnet), 6503 (IOS Reversi clients), 6505 (FIBS clients), 6506 (the Outland Gateway; the container listens on 4000), 6507 (DOoM clients), 6508 (GameMaster), 6512 (NetBoxes), 6514 (the batnav clients), 6515 (Awale), 6516 (Networdz), 6517 (NetLuff, which always connects to 1744: publish 6517 under 1744 as well, as the lines above do), 6518 (xcheckers, over ICDS), 6519 (Worthy Opponent, dialed by modem) and 6520 (GEnie's RSCARDS front ends, through a WiFi modem or a serial bridge). ParlorNet Play, the client for today's browsers and phones, has its own port, 6513: add `-p 6513:6513` and open `http://` your host `:6513/play/`, or put it behind an HTTPS proxy as deploy/README.md shows. Players, tables and settings are kept in the `/data` volume, with the keys the frontends use to reach the core (`/data/frontends`, made at first start). The container runs as one unprivileged user (uid 10042) and needs no capabilities (`--cap-drop ALL` works); docs/security-design.md (Containment) describes two opt-in ways to separate the frontends further.

By default players sign up by choosing a password and sign in with it. Signing up is its own step: New account on the web, `new` at the telnet login, `guest` on FIBS clients, a free name in an IOS client's login window. The sign-in and sign-up methods are set in the admin menus (Sign-in); docs/accounts.md explains them.

**A LAN, a club night or a festival.** Players sign in with a name alone (sign-in and sign-up `none`):

```sh
docker run -d --name parlornet --restart unless-stopped -e PARLOR_NAME=festival -e PARLOR_AUTH=none \
    -p 6500-6505:6500-6505 -p 6506:4000 -p 6507-6508:6507-6508 -p 6512:6512 -p 6514:6514 -p 6515-6517:6515-6517 -p 1744:6517 -p 6518:6518 -p 6519:6519 -p 6520:6520 \
    -p 127.0.0.1:6510:6510 -v parlornet:/data parlornet
```

**The open internet.** Players sign in with a password, and sign-up checks their email address (sign-in `password`, sign-up `email`). Sign-up mails the player a temporary password that works for 24 hours; the first sign-in with it asks for a password of their own, and an account nobody signs into within a day is deleted. So the parlor needs an SMTP relay, any that offers plain SMTP with a send-only user. Set the relay in the admin menus (Sign-in), or keep its settings out of the database in a file that only the container's user may read (`chown 10042 settings.conf; chmod 400 settings.conf`):

```
email.smtp_host=smtp.example.org
email.smtp_port=587
email.smtp_user=parlor
email.smtp_password=...
email.from=ParlorNet <noreply@example.org>
```

The relay is always reached over TLS (STARTTLS, or TLS on port 465) and its certificate is checked against `email.smtp_host`, so give its name, not its address.

```sh
docker run -d --name parlornet --restart unless-stopped -e PARLOR_NAME=games.example.org \
    -e PARLOR_AUTH=password -e PARLOR_SIGNUP=email \
    -v /etc/parlornet/settings.conf:/run/parlornet/settings.conf:ro \
    -p 6500-6505:6500-6505 -p 6506:4000 -p 6507-6508:6507-6508 -p 6512:6512 -p 6514:6514 -p 6515-6517:6515-6517 -p 1744:6517 -p 6518:6518 -p 6519:6519 -p 6520:6520 \
    -p 127.0.0.1:6510:6510 -v parlornet:/data parlornet
```

`PARLOR_NAME`, `PARLOR_AUTH` (sign-in: `none` or `password`), `PARLOR_SIGNUP` (sign-up: `none`, `password`, or `email`, which mails a temporary password; by default `none` with sign-in `none` and `password` with sign-in `password`), `PARLOR_ADAPTERS` (for example `web,telnet,admin`), `PARLOR_MOTD_FILE`, `PARLOR_CONTRIBUTE_URL` (the address for the message of the day's `{contribute}`; by default this project's home; empty: that line is left out) and `PARLOR_DOCS_URL` (the docs site the web pages link to; by default http://docs.parlornet.org/; empty: no link) are defaults: once the admin menus set a value, the database's value is used. `PARLOR_ADMIN=1` starts the admin menus even if they were turned off. A forgotten admin password is cleared with `docker exec parlornet python -m parlord.admin --db /data/parlord.db --reset-password` and a restart, which prints a new setup password.

## Branches and releases

The version is in `VERSION`. Changes go into `dev` by pull request, squashed; `main` takes only merges from `dev`, which is how a release is made. Every pull request and every push to `main` runs the whole test suite (`python3 -m unittest discover -s tests`, on Python 3.13) and builds the Docker image (`.github/workflows/ci.yml`), and a pull request into `main` fails unless it comes from `dev`. Every push to `dev` runs the same tests and build on the commit `dev` then has (`.github/workflows/dev.yml`). There is no dev pre-release: the server ships as its source, so the `dev` branch is the dev build.

A push to `main` whose version has no release yet runs the release (`.github/workflows/release.yml`). It runs the tests, builds the image, tags the commit `v<version>` and publishes a GitHub Release of the source. No image is published, since each parlor builds its own. Every push to `main` also rebuilds the docs site and publishes it at http://docs.parlornet.org/ (`.github/workflows/docs.yml`).
