# Installing on a Docker host

The scripts in `deploy/` install a ParlorNet server that stays up on a Linux host with Docker. They run the same image as the single `docker run` in the main README's [Run your own parlor](../README.md#run-your-own-parlor), with tighter limits.

To install, get the source from https://github.com/eric-cgn/parlornet and build the image. You can build it on the host, or build it elsewhere and load it on the host. You can also pull a release's image from GitHub's container registry. Its name is `ghcr.io/eric-cgn/parlornet:<version>`, and the release's `IMAGE-DIGEST` file names it by its digest. Then copy the scripts to the host and run one of them as root:

```sh
docker build --platform linux/amd64 -t parlornet:1 .
sudo PARLOR_NAME=games.example.org PARLOR_AUTH=password PARLOR_SIGNUP=email sh deploy/install.sh parlornet:1
```

`install.sh` runs one container named `parlornet`. The container runs as uid 10042 with every capability dropped, no new privileges, a read-only root file system, a 448 MB memory limit and a Docker network of its own. The script publishes the players' ports, which are 6500 to 6508, 6512 and 6514 to 6520, and it publishes 6517 as 1744 as well. It puts the admin menus (6510) and ParlorNet Play (6513) on the host's loopback address only.

Players, tables and settings live in `/srv/parlornet` on the host, so a restart or an upgrade keeps them. If you run the script again with a newer image, it replaces the container and keeps the data.

![What install.sh runs: the players' programs, the reverse proxy for ParlorNet Play, and the processes in the container](../static/img/diagram-parlor.gif)

The script reads `PARLOR_NAME`, `PARLOR_AUTH`, `PARLOR_SIGNUP`, `PARLOR_ADAPTERS`, `PARLOR_WEB_HOSTS`, `PARLOR_WEB_URL`, `PARLOR_WEB_PROXIES`, `PARLOR_PLAY_URL`, `PARLOR_PLAY_PROXIES` and `PARLOR_PLAY_OPEN` from its environment. `PARLOR_PUBLISH_IP` publishes the players' ports on one address of the host instead of every address. A proxy on the host then reaches the web site at that address rather than at 127.0.0.1. The comments at the top of the script explain each one. Settings you would rather keep out of the environment, such as the SMTP relay's password, go in `/etc/parlornet/settings.conf`. Make that file readable only by uid 10042 (`chown 10042 settings.conf; chmod 400 settings.conf`). If the file exists, the script mounts it into the container.

With `PARLOR_SIGNUP=email`, signing up mails each new player a temporary password, so the server needs an SMTP relay. [The open internet](../README.md#the-open-internet) in the main README shows how to set one. The first sign-in with the temporary password confirms the account and asks the player to choose a password of their own. An account that nobody signs into within 24 hours is deleted.

Your host's firewall decides who can reach the ports. If you limit new connections per address, leave room for the web pages, since each one loads a board image and a few small images.

## Upgrading from a version before mailed passwords

Earlier versions kept accounts in another format, and that database is not carried over. When the server starts and finds a database in the earlier format, it renames it to `parlord.db.old-<date and time>` in the same directory and starts a new, empty one. The players, the tables and the games in progress are gone. The admin menus ask to be set up again, and `docker logs parlornet` shows a new setup password. Settings given in the environment or in `/etc/parlornet/settings.conf` still apply. Delete the old file once you no longer need it.

## ParlorNet Play over HTTPS

ParlorNet Play, the client for current browsers, is a server extension of its own on port 6513. It speaks plain HTTP and expects a reverse proxy on the host to handle TLS, so `install.sh` publishes it on `127.0.0.1:6513` only. Give the container its public address and the proxy's address as the container sees it. For a proxy on the host, that address is 172.30.4.1, the gateway of the network that `install.sh` creates:

```sh
sudo PARLOR_PLAY_URL=https://games.example.org/play/ PARLOR_PLAY_PROXIES=172.30.4.1 sh deploy/install.sh parlornet:1
```

The same two settings can go in `/etc/parlornet/settings.conf` as `play.public_url` and `play.proxies`. With them, the WebSocket accepts only pages from that address, and the session cookie is `Secure`. The player's own address is taken from `X-Forwarded-For`, and `/play` on the web site redirects there with a 301. If you have no proxy, `PARLOR_PLAY_OPEN=1` publishes 6513 on every address, and players open `http://` your host `:6513/play/`.

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

Then tell the web extension about the proxy, the same way as for ParlorNet Play:

```sh
sudo PARLOR_WEB_URL=https://games.example.org/ PARLOR_WEB_PROXIES=172.30.4.1 sh deploy/install.sh parlornet:1
```

`PARLOR_WEB_PROXIES` lists the proxies' addresses or networks as the container sees them, separated by commas. In `/etc/parlornet/settings.conf` the same setting is `web.proxies`. For a request from one of these proxies, the web site takes the visitor's own address from `X-Forwarded-For`. That way the limits per address, on sign-ups, wrong passwords and requests in progress, count each visitor and not the proxy. The site also takes the scheme from `X-Forwarded-Proto`, so its redirects stay on https. A request from any other address is taken as it comes, and its forwarded headers are ignored.

`PARLOR_WEB_URL` (`web.public_url`) is the site's own address. With it set, every page answered without a query names its address there as canonical, in a `Link: <...>; rel="canonical"` header. The copies at port 6500, on plain http and under other names then all count as one page. The site's address `/` is the welcome page itself, not a redirect to it.

Do not add `Strict-Transport-Security` on this name. Browsers would apply it to every port, including the 1997-style pages on 6500, which stay plain HTTP for the old computers they serve. Certbot renews the certificate on its own timer.

## Dial-in gateways and their time limits

A dial-in gateway answers modem calls and carries each one to the native port, 6501. It may start the connection with a PROXY protocol header that says the connection is a call. The parlor believes that header only from the addresses you list, as the container sees them. A gateway on the host itself comes from 172.30.4.1:

```sh
sudo PARLOR_DIALIN_GATEWAYS=172.30.4.1 sh deploy/install.sh parlornet:1
```

Without `PARLOR_DIALIN_GATEWAYS` (`dialin.gateways` in the settings file), the parlor trusts no gateway, and a PROXY header from anywhere is refused. Calls have three limits in minutes. `PARLOR_DIALIN_CALL_MINS` limits one call (75 by default), `PARLOR_DIALIN_USER_MONTH_MINS` one player's calls in a calendar month (60), and `PARLOR_DIALIN_POOL_MONTH_MINS` all calls together in a month (150). 0 means no limit. docs/accounts.md lists these settings with the same limits for other connections, and docs/protocol.md describes the header and what callers are told.

## Administration

The admin menus listen on port 6510 on the host's loopback address. From another machine, open an SSH tunnel to the host and connect through it:

```sh
ssh -L 6510:127.0.0.1:6510 you@your-host
telnet 127.0.0.1 6510
```

Until you set an admin password, every start prints a one-time setup password in `docker logs parlornet`.

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

The container's memory limit is 448 MiB unless you set `PARLOR_MEMORY`, which `install.sh` gives to Docker as `--memory`. `PARLOR_MEMORY_SWAP` goes to `--memory-swap`, the limit on memory and swap together, and it must be at least `PARLOR_MEMORY`. Without it, Docker uses its own default. On a host that runs nothing but the parlor, give the container nearly all of the host's memory and keep a small reserve, so that the host can still be reached over SSH when the container is full. On a host with 2 GiB, for example:

```sh
sudo PARLOR_MEMORY=1792m PARLOR_MEMORY_SWAP=1792m sh deploy/install.sh parlornet:1
```

`install-extensions.sh` takes the same two settings and gives them to each of its containers. Its default limit is 96 MiB a container.
