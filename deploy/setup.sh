#!/bin/sh
# Installs a parlor in /opt/parlornet, where Docker Compose runs it and systemd starts it at boot. Run it as
# root on a Linux host with Docker, its compose plugin and systemd.
#   sudo sh deploy/setup.sh                          installs the parlor, or updates its files from this copy
#   sudo sh /opt/parlornet/setup.sh update VERSION   switches to the image of another release
# The first run asks for the server's name, how players sign in and sign up, and the addresses of the web
# site and ParlorNet Play behind an HTTPS proxy. It writes the answers to /opt/parlornet/.env. To answer
# without being asked, give them in the environment as PARLOR_NAME, PARLOR_AUTH, PARLOR_SIGNUP, PARLOR_WEB_URL
# and PARLOR_PLAY_URL. A later run keeps .env, the settings file and the data. It replaces compose.yaml,
# env.example, the systemd unit and this script, and restarts the parlor.
set -eu
DIR=/opt/parlornet
UNIT=/etc/systemd/system/parlornet.service
IMAGE=ghcr.io/eric-cgn/parlornet

die() { echo "setup.sh: $*" >&2; exit 2; }

# The value .env gives a setting, or $2 when .env does not set it
get() {
	v=$(sed -n "s/^$1=//p" "$DIR/.env" 2>/dev/null | tail -n 1)
	echo "${v:-$2}"
}

# Sets the variable $1 from the environment, or else from an answer to the question $2, or else to $3
ask() {
	eval "given=\${$1+yes}"
	if [ -z "$given" ]; then
		printf '%s%s: ' "$2" "${3:+ [$3]}" >&2
		read -r answer || answer=
		eval "$1=\${answer:-\$3}"
	fi
	eval "value=\$$1"
	case $value in
	*[!A-Za-z0-9._:/@,+\ -]*) die "$1 holds only letters, digits, spaces and . _ : / @ , + -" ;;
	esac
}

# Turns the line "# KEY=..." of the .env being written into "KEY=VALUE"
put() {
	K=$1 V=$2 awk '!done && $0 ~ "^# " ENVIRON["K"] "=" { print ENVIRON["K"] "=" ENVIRON["V"]; done = 1; next }
		{ print }' "$NEW" > "$NEW.tmp"
	mv "$NEW.tmp" "$NEW"
}

[ "$(id -u)" = 0 ] || die "run it as root"
docker compose version >/dev/null 2>&1 || die "it needs Docker and Docker's compose plugin"

if [ "${1:-}" = update ]; then
	V=${2:-}
	echo "$V" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+' || die "usage: setup.sh update VERSION, such as 0.3.18"
	[ -z "$(get PARLOR_IMAGE '')" ] || die "PARLOR_IMAGE in $DIR/.env chooses the image. Change it there."
	sed "s|$IMAGE:[0-9.]*}|$IMAGE:$V}|" "$DIR/compose.yaml" > "$DIR/compose.yaml.new"
	grep -q "$IMAGE:$V}" "$DIR/compose.yaml.new" || die "$DIR/compose.yaml names no release's image"
	mv "$DIR/compose.yaml.new" "$DIR/compose.yaml"
	(cd "$DIR" && docker compose pull)
	systemctl restart parlornet
	echo "setup.sh: the parlor runs $IMAGE:$V"
	exit 0
fi
[ $# = 0 ] || die "usage: setup.sh, or setup.sh update VERSION"

SRC=$(cd "$(dirname "$0")" && pwd -P)
install -d -m 755 "$DIR"
if [ "$SRC" != "$DIR" ]; then
	install -m 644 "$SRC/compose.yaml" "$SRC/env.example" "$DIR/"
	install -m 755 "$SRC/setup.sh" "$DIR/"
	install -m 644 "$SRC/parlornet.service" "$UNIT"
fi

if [ ! -f "$DIR/.env" ]; then
	ask PARLOR_NAME "The server's name" parlornet
	ask PARLOR_AUTH "How players sign in, none or password" password
	case $PARLOR_AUTH in none | password) ;; *) die "PARLOR_AUTH is none or password" ;; esac
	ask PARLOR_SIGNUP "How players sign up, none, password or email" "$PARLOR_AUTH"
	case $PARLOR_SIGNUP in none | password | email) ;; *) die "PARLOR_SIGNUP is none, password or email" ;; esac
	ask PARLOR_WEB_URL "The web site's https:// address behind a proxy on this host, if it has one" ""
	ask PARLOR_PLAY_URL "ParlorNet Play's https:// address behind a proxy on this host, if it has one" ""
	NEW=$DIR/.env.new
	cp "$DIR/env.example" "$NEW"
	put PARLOR_NAME "$PARLOR_NAME"
	put PARLOR_AUTH "$PARLOR_AUTH"
	put PARLOR_SIGNUP "$PARLOR_SIGNUP"
	# A proxy on this host reaches the container from its network's gateway.
	[ -z "$PARLOR_WEB_URL" ] || { put PARLOR_WEB_URL "$PARLOR_WEB_URL"; put PARLOR_WEB_PROXIES 172.30.4.1; }
	[ -z "$PARLOR_PLAY_URL" ] || { put PARLOR_PLAY_URL "$PARLOR_PLAY_URL"; put PARLOR_PLAY_PROXIES 172.30.4.1; }
	chmod 600 "$NEW"
	mv "$NEW" "$DIR/.env"
	echo "setup.sh: wrote $DIR/.env"
fi

# The players, the tables and the keys belong to the container's user. The settings file starts empty.
install -d -m 700 -o 10042 -g 10042 "$(get PARLOR_DATA_DIR /srv/parlornet)"
install -d -m 755 "$(get PARLOR_SITE_DIR /srv/parlornet-site)"
CONF=$(get PARLOR_SETTINGS_FILE /etc/parlornet/settings.conf)
if [ ! -e "$CONF" ]; then
	install -d -m 755 "$(dirname "$CONF")"
	: > "$CONF"
	chown 10042:10042 "$CONF"
	chmod 400 "$CONF"
fi

systemctl daemon-reload
systemctl enable parlornet
systemctl restart parlornet
echo "setup.sh: the parlor is starting. \"docker logs parlornet\" shows its one-time setup password, and"
echo "\"telnet 127.0.0.1 6510\" opens the admin menus on this host."
