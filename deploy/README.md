# Installing on a Docker host

There are two ways to install a ParlorNet server that stays up on a Linux host with Docker. Most parlors use `setup.sh`, which installs a Docker Compose project and a systemd unit. The main README's [Run your own parlor](../README.md#run-your-own-parlor) gives its three steps. The install scripts `install.sh`, `install-extensions.sh` and `install-split.sh` are for setups that the compose project doesn't cover, such as server extensions on another host or a container for each process. [The install scripts](#the-install-scripts) below describes them. Both ways run the same image with the same limits.

## What setup.sh installs

Run `sudo sh deploy/setup.sh` from the source. It needs Docker Engine, Docker's compose plugin and systemd. The script puts these files in `/opt/parlornet`:

- `compose.yaml` runs the container.
- `.env` holds the parlor's settings. The first run writes it from your answers.
- `env.example` lists every setting with its default and a line or two about it.
- `setup.sh` is a copy of the script, for updates.

It installs the unit `parlornet.service`, which runs `docker compose up -d` in `/opt/parlornet` at boot, after Docker starts, and `docker compose down` at shutdown. It enables the unit and starts it. It also makes three things that the container uses:

- `/srv/parlornet` holds the players, the tables and the settings that the admin menus save. It belongs to uid 10042, and only that uid can enter it.
- `/etc/parlornet/settings.conf` holds settings you would rather keep out of `.env`, such as the SMTP relay's password. The script creates it empty, readable only by uid 10042.
- `/srv/parlornet-site` holds the site's own files, as [A site of your own](#a-site-of-your-own) describes. The script creates it empty.

The container is named `parlornet`. It runs as uid 10042 with every capability dropped, no new privileges, a read-only root file system, a limit of 64 processes and a 448 MB memory limit. It has a Docker network of its own named `parlor`, with the subnet 172.30.4.0/24. The players' ports are 6500 to 6508, 6512 and 6514 to 6520, and the dial-in gateways' port is 6521. They are published on every address of the host, and 6517 is published as 1744 as well. The admin menus (6510) and ParlorNet Play (6513) are on the host's loopback address only.

To change a setting, edit `/opt/parlornet/.env` and run `sudo systemctl restart parlornet`. The settings that place the container on the host are in the same file. `PARLOR_PUBLISH_IP` publishes the players' ports on one address of the host instead of every address. A proxy on the host then reaches the web site at that address rather than at 127.0.0.1. `PARLOR_DATA_DIR`, `PARLOR_SETTINGS_FILE` and `PARLOR_SITE_DIR` move the three paths above. If you change one of them, run `setup.sh` again to create it.

With `PARLOR_SIGNUP=email`, signing up mails each new player a temporary password, so the server needs an SMTP relay. [The open internet](../README.md#the-open-internet) in the main README shows how to set one. The first sign-in with the temporary password confirms the account and asks the player to choose a password of their own. An account that nobody signs into within 24 hours is deleted.

Your host's firewall decides who can reach the ports. If you limit new connections per address, leave room for the web pages, since each one loads a board image and a few small images.

![The parlor that setup.sh installs: the players' programs, the reverse proxy for ParlorNet Play, and the processes in the container](../static/img/diagram-parlor.gif)

### Updates and your own image

Running `setup.sh` again keeps `.env`, the settings file and the data. It replaces `compose.yaml`, `env.example`, the unit and its own copy, and restarts the parlor. Each release's `compose.yaml` names that release's image, `ghcr.io/eric-cgn/parlornet:<version>`, so running a newer release's script moves the parlor to the newer image. To switch the image without new files, give the release's version to the installed copy:

```sh
sudo sh /opt/parlornet/setup.sh update 0.3.19
```

It changes the version in `/opt/parlornet/compose.yaml`, pulls the image and restarts the parlor. Each release's `IMAGE-DIGEST` file names its image by its digest, if you want to check what you pulled.

To run an image you built yourself, build it on the host and name it in `.env`:

```sh
sudo docker build -t parlornet:local .
echo PARLOR_IMAGE=parlornet:local | sudo tee -a /opt/parlornet/.env
sudo systemctl restart parlornet
```

`sudo docker compose logs` in `/opt/parlornet` and `sudo docker logs parlornet` both show the log. `sudo systemctl stop parlornet` stops the parlor until the next boot or the next start.

## Upgrading from a version before mailed passwords

Earlier versions kept accounts in another format, and that database is not carried over. When the server starts and finds a database in the earlier format, it renames it to `parlord.db.old-<date and time>` in the same directory and starts a new, empty one. The players, the tables and the games in progress are gone. The admin menus ask to be set up again, and `docker logs parlornet` shows a new setup password. Settings given in the environment or in `/etc/parlornet/settings.conf` still apply. Delete the old file once you no longer need it.

## ParlorNet Play over HTTPS

ParlorNet Play, the client for current browsers, is a server extension of its own on port 6513. It speaks plain HTTP and expects a reverse proxy on the host to handle TLS, so it is published on `127.0.0.1:6513` only. Give the container its public address and the proxy's address as the container sees it. For a proxy on the host, that address is 172.30.4.1, the gateway of the parlor's network. If you gave `setup.sh` an address for Play, these lines are already in `/opt/parlornet/.env`:

```
PARLOR_PLAY_URL=https://games.example.org/play/
PARLOR_PLAY_PROXIES=172.30.4.1
```

`install.sh` takes the same settings from its environment, as do the other examples below. The same two settings can also go in `/etc/parlornet/settings.conf` as `play.public_url` and `play.proxies`. With them, the WebSocket accepts only pages from that address, and the session cookie is `Secure`. The player's own address is taken from `X-Forwarded-For`, and `/play` on the web site redirects there with a 301. If you have no proxy, set `PARLOR_PLAY_PUBLISH` in `.env` to nothing, or `PARLOR_PLAY_OPEN=1` for `install.sh`. Then 6513 is published on every address, and players open `http://` your host `:6513/play/`.

Here is an nginx configuration for the proxy. It uses a certificate from Let's Encrypt, which you can get with `certbot --nginx -d games.example.org` or `certbot certonly --webroot`. Port 80 sends `/play` to https:

```nginx
server {
    listen 443 ssl;
    server_name games.example.org;
    ssl_certificate     /etc/letsencrypt/live/games.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/games.example.org/privkey.pem;

    location /play/ {
        proxy_pass http://127.0.0.1:6513;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    location = /play { return 301 https://$host/play/; }
}

server {
    listen 80;
    server_name games.example.org;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location /play { return 301 https://$host/play/; }
}
```

## The web site on the usual ports

The web site itself is on port 6500, and players can keep using that port. To serve the site at your name's own address as well, at `https://games.example.org/` and `http://games.example.org/`, let the same proxy pass everything else to 6500. Pass the requests on rather than redirect them. A redirect to another port is not a page, so search engines would index nothing. Port 80 must also not send old browsers to https, because they cannot speak it. Add this `location` to both server blocks above:

```nginx
    location / {
        proxy_pass http://127.0.0.1:6500;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
```

Then tell the web extension about the proxy in `.env`, the same way as for ParlorNet Play:

```
PARLOR_WEB_URL=https://games.example.org/
PARLOR_WEB_PROXIES=172.30.4.1
```

`PARLOR_WEB_PROXIES` lists the proxies' addresses or networks as the container sees them, separated by commas. In `/etc/parlornet/settings.conf` the same setting is `web.proxies`. For a request from one of these proxies, the web site takes the visitor's own address from `X-Forwarded-For`. That way the limits per address, on sign-ups, wrong passwords and requests in progress, count each visitor and not the proxy. The site also takes the scheme from `X-Forwarded-Proto`, so its redirects stay on https. A request from any other address is taken as it comes, and its forwarded headers are ignored.

`PARLOR_WEB_URL` (`web.public_url`) is the site's own address. With it set, every page answered without a query names its address there as canonical, in a `Link: <...>; rel="canonical"` header. The copies at port 6500, on plain http and under other names then all count as one page. The site's address `/` is the welcome page itself, not a redirect to it.

Do not add `Strict-Transport-Security` on this name. Browsers would apply it to every port, including the 1997-style pages on 6500, which stay plain HTTP for the old computers they serve. Certbot renews the certificate on its own timer.

## Dial-in gateways and their time limits

A dial-in gateway answers modem calls and carries each one to the dial-in extension on port 6521, so point the gateway at that port. A ParlorNet client sends `PARLORNET/1` within 5 seconds of the modems connecting and gets the native protocol. A terminal program gets the telnet service in plain text. The extension is called `dialin`. If you saved a list in the admin menus' Adapters screen, turn it on there. The gateway may start each connection with a PROXY protocol header that says the connection is a call. The parlor believes that header only from the addresses you list, as the container sees them. A gateway on the host itself comes from 172.30.4.1:

```
PARLOR_DIALIN_GATEWAYS=172.30.4.1
```

Without `PARLOR_DIALIN_GATEWAYS` (`dialin.gateways` in the settings file), the parlor trusts no gateway, and a PROXY header from anywhere is refused. Calls have three limits in minutes, whether they go to the native protocol or to telnet. `PARLOR_DIALIN_CALL_MINS` limits one call (75 by default), `PARLOR_DIALIN_USER_MONTH_MINS` one player's calls in a calendar month (60), and `PARLOR_DIALIN_POOL_MONTH_MINS` all calls together in a month (150). 0 means no limit. docs/accounts.md lists these settings with the same limits for other connections, and docs/protocol.md describes the header and what callers are told.

## A site of your own

A server can show words of its own on the web site and in the message of the day, such as how to reach it by modem or when it hosts an event. It can also offer game rulesets of its own. These files go in a directory on the host, outside the image. With `setup.sh` it is `/srv/parlornet-site`. For `install.sh`, give the directory's path as `PARLOR_SITE_DIR`:

```sh
sudo PARLOR_SITE_DIR=/srv/parlornet-site sh deploy/install.sh parlornet:1
```

The directory is mounted read-only at `/run/parlornet/site` in the container. Every file in it is optional, and a server with an empty directory or none at all shows the image's own message of the day. The server extensions may run under users of their own, so make the files readable by everyone, for example with `chmod -R a+rX /srv/parlornet-site`. The server reads the files when it starts. After you change one, run `sudo systemctl restart parlornet`, or `docker restart parlornet` for `install.sh`.

- `motd.txt` is the message of the day. It takes the place of the image's `deploy/motd.txt` when the server starts. A message of the day saved in the admin menus still takes the place of both. It is plain ASCII, at most 20 lines, and each line of up to 73 characters is shown as written, so it can hold an ASCII layout.
- `welcome.htm` is a piece of HTML 3.2 that the welcome page shows right after the message of the day, in its Welcome section. Write it as you would write the inside of a page's body, with paragraphs, lists, links and tables, and leave out `<html>`, `<head>` and `<body>`.
- `pages/NAME.htm` is a page of its own at `/NAME.htm` on the web site. NAME is 1 to 32 lower-case letters, digits and hyphens. The file's first line is the page's title, written as `<title>Dial-in access</title>`, and the rest of the file is the page's body, in HTML 3.2 as in `welcome.htm`. The page comes in the site's frame with its navigation bar, with the breadcrumb line Welcome > its title under the bar and its title as its heading. Nothing links to it on its own, so link to it from `welcome.htm`.
- `rules/gridwords/ID.json` adds a Grid Words ruleset whose rules id is ID, next to the standard rules that every release has. ID is a lower-case letter and then up to 11 lower-case letters or digits. The file gives what each letter is worth, how many of each the bag holds and where the bonus squares are on the 15 by 15 board, and it may set how many turns without a word end a game. The Ruleset files part of docs/types/gridwords-1.md describes the format. Players can then pick the ruleset wherever they start a table, by its id with a capital letter. A period client that draws its own board plays only the rulesets its server extension names.

The web extension refuses a page whose name the site already uses, such as `lobby` or `login`, and a page without its title line. It also refuses a file that is larger than 32 KB or that is not UTF-8 text. `docker logs parlornet` names each file it leaves out and says why. The game does the same for a ruleset file it can't use, with a line that starts `parlord: plugin gridwords:`.

### Placeholders

The files name the server's addresses and ports through placeholders. The server fills them in from its configuration when it builds the message of the day or a page, so the same files work on any host and stay right when a name or a port changes. A placeholder is a word in braces:

- `{name}` is the server's name, from `PARLOR_NAME` or the admin menus.
- `{host}` is the host name in `PARLOR_WEB_URL` when that is set, and otherwise the server's name.
- `{web}` is the web site's address. It is `PARLOR_WEB_URL` when that is set, and otherwise `http://`, the host and the web extension's port.
- `{play}` is ParlorNet Play's address while Play runs. It is `PARLOR_PLAY_URL` when that is set, and otherwise `http://`, the host, Play's port and `/play/`.
- `{docs}` is the docs site's address (`site.docs_url`).
- `{contribute}` is the project's home, where anyone can get the source (`site.contribute_url`).
- `{port:KEY}` is the public port that the server extension for the protocol KEY declared to the core, such as `{port:TELNET}`, `{port:HTTP}` or `{port:PARLORNET/1}`. The keys are the ones in the docs site's protocols reference.

Write `{{` for a brace `{` and `}}` for a brace `}`. The server only looks for these words and never runs anything in the files. In the HTML files it escapes each value for HTML. A placeholder it doesn't know stays in the text as you wrote it, and so does one that has no value at the moment, such as `{play}` while ParlorNet Play is off. The log names each of them once. The message of the day leaves out a line that holds a placeholder with no value instead.

The message of the day takes its values from the core's settings, and the web files take theirs from the web extension's. When some server extensions run on another host, as [Server extensions on another host](#server-extensions-on-another-host) describes, `install-extensions.sh` takes `PARLOR_SITE_DIR` too. Its directory on the front host holds `welcome.htm` and `pages/`, and `motd.txt` and `rules/` stay in the directory on the core's host. `install-split.sh` takes `PARLOR_SITE_DIR` as well.

### An example

Here is a site directory for a server named games.example.org that also takes modem calls. Its message of the day, `/srv/parlornet-site/motd.txt`, is two lines:

```text
Welcome to {name}.
Telnet to {host} port {port:TELNET}, or browse {web}.
```

Its `welcome.htm` points to a page about calling in:

```html
<p>You can also play by modem. <a href="/dial-in.htm">Dial-in access</a> says how.</p>
```

That page is `pages/dial-in.htm`:

```html
<title>Dial-in access</title>
<p>{name} answers modem calls at 555-0100, at up to 14,400 bit/s. Set your terminal program to
8 data bits, no parity and 1 stop bit, and dial. Each call lasts up to 75 minutes.</p>
<p>On the Internet, telnet to <tt>{host}</tt> port {port:TELNET} instead.</p>
```

## Administration

The admin menus listen on port 6510 on the host's loopback address. From another machine, open an SSH tunnel to the host and connect through it:

```sh
ssh -L 6510:127.0.0.1:6510 you@your-host
telnet 127.0.0.1 6510
```

Until you set an admin password, every start prints a one-time setup password in `docker logs parlornet`.

## The install scripts

`install.sh` runs the same container with a single `docker run`, without Compose or systemd. Docker's restart policy brings it back after a reboot. It reads its settings from its environment and takes the image's name as its argument. You can build the image on the host, or build it elsewhere and load it on the host. You can also pull a release's image from GitHub's container registry. Then copy the scripts to the host and run one of them as root:

```sh
docker build --platform linux/amd64 -t parlornet:1 .
sudo PARLOR_NAME=games.example.org PARLOR_AUTH=password PARLOR_SIGNUP=email sh deploy/install.sh parlornet:1
```

The container has the same name, user, limits, network and ports as the one that `setup.sh` installs. It uses `/srv/parlornet` for the data, and it mounts `/etc/parlornet/settings.conf` if that file exists. If you run the script again with a newer image, it replaces the container and keeps the data. The script reads `PARLOR_NAME`, `PARLOR_AUTH`, `PARLOR_SIGNUP`, `PARLOR_ADAPTERS`, `PARLOR_WEB_HOSTS`, `PARLOR_WEB_URL`, `PARLOR_WEB_PROXIES`, `PARLOR_PLAY_URL`, `PARLOR_PLAY_PROXIES`, `PARLOR_PLAY_OPEN`, `PARLOR_PUBLISH_IP`, `PARLOR_SITE_DIR`, `PARLOR_MEMORY` and the other settings from its environment. The comments at the top of the script explain each one. Make the settings file readable only by uid 10042 (`chown 10042 settings.conf; chmod 400 settings.conf`).

Both ways use the same paths, so a host can change from one to the other and keep its data. Only one of them can run at a time, because they use the same container name, network and ports. To change from `install.sh` to `setup.sh`, remove the old container and its network first, with `docker rm -f parlornet` and `docker network rm parlor`.

## More separation

`install-split.sh` is an optional alternative that runs each server process in a container of its own, under its own uid and with only its own key. It uses the same image and the same ports. The Containment part of [docs/security-design.md](../docs/security-design.md#containment) explains the trade-offs, and the comments at the top of the script describe its networks and options.

## Server extensions on another host

A parlor can run some of its server extensions on a second host, against the core on the first. The extension channel then crosses from one host to the other. It is TLS 1.3 with a pre-shared key for each extension, the same as inside the container, but it should still cross only a private link between the hosts, such as WireGuard or a private network. The core treats an extension on another host exactly like one in its own container.

In this example, the front host runs the web site (6500), telnet (6502) and ParlorNet Play (6513). The back host runs the core and every other extension. The back host's address on the private link is 10.0.0.1. Players reach the back host at 192.0.2.10 and the front host at 192.0.2.20. Build or load the same image on both hosts.

On the back host, run `install.sh` like this:

```sh
sudo PARLOR_NAME=games.example.org PARLOR_AUTH=password PARLOR_SIGNUP=email \
    PARLOR_ADAPTERS=ios,fibs,outland,doom,gamemaster,netsprocket,batnav,awale,networdz,netluff,icds,worthy,rscards,admin,bot \
    PARLOR_PUBLISH_IP=192.0.2.10 PARLOR_FRONTEND_PUBLISH=10.0.0.1 PARLOR_REMOTE_EXTENSIONS=web,telnet,play \
    sh deploy/install.sh parlornet:1
```

`PARLOR_ADAPTERS` leaves out the three extensions that run on the front host. If you saved a list in the admin menus' Adapters screen, that list wins, so turn the three off there as well. `PARLOR_PUBLISH_IP` keeps the players' ports off the link. `PARLOR_FRONTEND_PUBLISH` publishes the extension channel, 6590, on the link's address and nowhere else. Inside the container the core then listens for extensions on every address (`PARLOR_FRONTEND_HOST=0.0.0.0`), and the extensions in the container still connect to it over loopback. The admin socket stays inside the container. Docker can publish a port on the link's address only while the link is up, so bring the link up before Docker starts, for example by ordering `wg-quick@wg0` before `docker.service`.

`PARLOR_REMOTE_EXTENSIONS` enrolls a key for each extension that runs on the other host, and it keeps any key that is already enrolled. The script puts a copy of each key in `/srv/parlornet-keys` and prints their names. Only root can read them. Copy them over SSH to `/srv/parlornet-keys` on the front host, and keep them readable only by root there too. After you rotate a key with `docker exec parlornet python -m parlord.frontends --keys /data/frontends rotate web`, run `install.sh` again to refresh the copy, copy it again, and run `install-extensions.sh` again on the front host.

On the front host, run `install-extensions.sh` with the core's address on the link and the names of the extensions:

```sh
sudo PARLOR_CORE=10.0.0.1:6590 PARLOR_PUBLISH_IP=192.0.2.20 \
    PARLOR_WEB_URL=https://games.example.org/ PARLOR_WEB_PROXIES=172.30.6.1 \
    PARLOR_PLAY_URL=https://games.example.org/play/ PARLOR_PLAY_PROXIES=172.30.6.1 \
    PARLOR_PLAY_PUSH_CONTACT=mailto:ops@example.org \
    sh deploy/install-extensions.sh parlornet:1 web telnet play
```

Each extension runs in a container of its own, under its own uid, with its own key mounted read-only. The containers have the same limits as those of `install-split.sh`, and they use no host networking. They share a Docker network named `parlor-ext`, with the subnet 172.30.6.0/24, and they cannot reach each other on it. Its gateway, 172.30.6.1, is the address the containers see for a proxy on the front host, so it goes in `PARLOR_WEB_PROXIES` and `PARLOR_PLAY_PROXIES`. ParlorNet Play is published on the front host's `127.0.0.1:6513`, for the TLS proxy, so the nginx configuration above works there for Play. Because of `PARLOR_PUBLISH_IP`, the `location /` for the web site passes to `http://192.0.2.20:6500` instead. Play keeps its push subscriptions and its VAPID key in `/srv/parlornet-extensions/play`. The script takes `PARLOR_WEB_HOSTS` and `PARLOR_PLAY_OPEN` as `install.sh` does. The admin menus' values for the web and Play settings reach only the extensions in the core's container, so give them to this script.

Running `install-extensions.sh` again replaces its containers and keeps the data. It also removes the container of any extension it ran before that you no longer name. The comments at the top of the script list its other settings.

The sign-in mail names the web and telnet ports at `PARLOR_NAME`, so that name should reach the front host in this example. Players who use the back host's ports need its own address, or a name of its own.

## Memory

With every server extension on, the core and the bot, the container uses about 400 MiB at rest on linux/amd64, and more with every game loaded. Each server process holds about 20 MiB of its own, and ParlorNet Play needs no more than telnet. The plugin host that runs the games starts with the first game. Once no game is being played, it stops after `plugins.idle` seconds, 600 by default. `docker stats parlornet` shows the current figure.

The container's memory limit is 448 MiB unless you set `PARLOR_MEMORY`, which Docker takes as the container's memory limit. `PARLOR_MEMORY_SWAP` is the limit on memory and swap together, and it must be at least `PARLOR_MEMORY`. Without it, Docker uses its own default. On a host that runs nothing but the parlor, give the container nearly all of the host's memory and keep a small reserve, so that the host can still be reached over SSH when the container is full. On a host with 2 GiB, for example, put this in `.env`:

```
PARLOR_MEMORY=1792m
PARLOR_MEMORY_SWAP=1792m
```

`install-extensions.sh` takes the same two settings and gives them to each of its containers. Its default limit is 96 MiB a container.
