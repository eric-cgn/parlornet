#!/bin/sh
# Starts, or restarts, the ParlorNet container on a Docker host. Run it as root once the
# image is loaded on the host. Running it again replaces the container and keeps the data.
#   sudo sh install.sh parlornet:VERSION
# It publishes ports 6500 (HTTP), 6501 (native), 6502 (telnet), 6503 (IOS), 6505 (FIBS),
# 6506 (Outland, the container's port 4000), 6507 (DOoM), 6508 (GameMaster, through the ParlorNet
# rulebook), 6512 (NetBoxes), 6514 (batnav), 6515 (Awale), 6516 (Networdz),
# 6517 (NetLuff), which it also publishes as 1744, the port NetLuff always connects to, 6518 (ICDS,
# for xcheckers), 6519 (Worthy Opponent, dialed by modem) and 6520 (GEnie's RSCARDS front ends, by
# WiFi modem or serial bridge). The host's own firewall decides who can reach them.
# ParlorNet Play (6513, plain HTTP) is published on the host's loopback only, for a reverse proxy on the host
# that terminates TLS (deploy/README.md); PARLOR_PLAY_OPEN=1 publishes it on every address instead, for a
# parlor without a proxy.
#
# These settings come from this script's environment, for example
#   sudo PARLOR_NAME=games.example.org PARLOR_AUTH=password PARLOR_SIGNUP=email sh install.sh IMAGE
#   PARLOR_NAME       the server's name, shown in its greeting and in the mail it sends
#   PARLOR_AUTH       how players sign in: none or password (default password)
#   PARLOR_SIGNUP     how they sign up: none, password, or email, which mails them a password
#                     (default: the one PARLOR_AUTH goes with, none or password)
#   PARLOR_WEB_HOSTS  the names the web answers to, comma-separated (default: any name)
#   PARLOR_WEB_URL    the web site's own address when a proxy serves it on the usual ports, such as
#                     https://games.example.org/; its pages name their address there as canonical
#   PARLOR_WEB_PROXIES  the proxy's address as the container sees it, whose X-Forwarded-For the web
#                     believes: 172.30.4.1 (this network's gateway) for a proxy on the host
#   PARLOR_PLAY_URL   the address players open for ParlorNet Play, through the proxy, such as
#                     https://games.example.org/play/; the web's /play answers 301 there
#   PARLOR_PLAY_PROXIES  the proxy's address as the container sees it, whose X-Forwarded-For the Play
#                     extension believes: 172.30.4.1 (this network's gateway) for a proxy on the host
#   PARLOR_PLAY_OPEN=1  publish 6513 on every address, not just the host's loopback
#   PARLOR_PLAY_PUSH_CONTACT  your mailto: or https: address, which ParlorNet Play gives the browsers' push
#                     services; Play's push notifications are off without it (and without PARLOR_PLAY_URL)
#   PARLOR_DIALIN_GATEWAYS  the dial-in gateways' addresses as the container sees them, comma-separated; a
#                     call they carry to 6501 has the dial-in time limits (deploy/README.md)
#   PARLOR_DIALIN_CALL_MINS, PARLOR_DIALIN_USER_MONTH_MINS, PARLOR_DIALIN_POOL_MONTH_MINS  those limits in
#                     minutes: one call, one player in a month, the whole parlor in a month (75, 60, 150)
#   PARLOR_DEFAULT_CALL_MINS, PARLOR_DEFAULT_USER_MONTH_MINS, PARLOR_DEFAULT_POOL_MONTH_MINS  the same for
#                     every other connection to 6501 (none by default)
# The admin menus and /etc/parlornet/settings.conf can set them too, and take precedence.
set -eu
IMAGE=${1:?usage: install.sh IMAGE}

# Its own bridge, 172.30.4.0/24; the container reaches out only to the SMTP relay and, for Play's push
# notifications, the browsers' push services (HTTPS)
docker network inspect parlor >/dev/null 2>&1 || docker network create --subnet 172.30.4.0/24 \
	-o com.docker.network.bridge.name=br-parlor parlor >/dev/null

# Players, tables and games live in SQLite here, outside the container. The
# container runs as uid 10042 with no capabilities; every process in it is that
# user (docs/security-design.md, Containment: the alternatives are opt-in).
install -d -m 700 -o 10042 -g 10042 /srv/parlornet

# The admin menus (6510) are published on the host's loopback only. From another
# machine, open an SSH tunnel to the host (ssh -L 6510:127.0.0.1:6510 HOST), then
# telnet 127.0.0.1 6510.
docker rm -f parlornet >/dev/null 2>&1 || true
docker run -d --name parlornet --restart unless-stopped \
	--network parlor -p 6500:6500 -p 6501:6501 -p 6502:6502 -p 6503:6503 -p 6505:6505 -p 6506:4000 -p 6507:6507 \
	-p 6508:6508 -p 6512:6512 -p 6514:6514 -p 6515:6515 -p 6516:6516 -p 6517:6517 -p 1744:6517 -p 6518:6518 \
	-p 6519:6519 -p 6520:6520 \
	-p "$([ "${PARLOR_PLAY_OPEN:-}" = 1 ] || echo 127.0.0.1:)6513:6513" \
	-p 127.0.0.1:6510:6510 \
	-e PARLOR_MOTD_FILE=/app/deploy/motd.txt \
	$(for v in PARLOR_NAME PARLOR_AUTH PARLOR_SIGNUP PARLOR_WEB_HOSTS PARLOR_WEB_URL PARLOR_WEB_PROXIES \
		PARLOR_PLAY_URL PARLOR_PLAY_PROXIES \
		PARLOR_PLAY_PUSH_CONTACT PARLOR_DIALIN_GATEWAYS PARLOR_DIALIN_CALL_MINS PARLOR_DIALIN_USER_MONTH_MINS \
		PARLOR_DIALIN_POOL_MONTH_MINS PARLOR_DEFAULT_CALL_MINS PARLOR_DEFAULT_USER_MONTH_MINS \
		PARLOR_DEFAULT_POOL_MONTH_MINS; do
		eval "x=\${$v:-}"; [ -n "$x" ] && echo "-e $v=$x"; done) \
	-v /srv/parlornet:/data \
	$([ -f /etc/parlornet/settings.conf ] && echo "-v /etc/parlornet/settings.conf:/run/parlornet/settings.conf:ro") \
	--user 10042:10042 --read-only --tmpfs /tmp:size=16m --cap-drop ALL --security-opt no-new-privileges \
	--pids-limit 64 --memory 448m \
	"$IMAGE"
