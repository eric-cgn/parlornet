#!/bin/sh
# OPT-IN, a proposal (docs/security-design.md, Containment, option a): ParlorNet
# as one container per process instead of one container. The core holds the
# database; each server extension runs in its own container under its own uid
# with only its own key, mounted read-only. Every container runs with
# --cap-drop ALL, no capability added, no-new-privileges, a read-only root and
# --user. No host networking. Networks:
#   $NET-int    --internal (no way out): the core's extension channel (6590)
#               and admin socket (6599), bound to the core's address there
#               only; the core and the server extensions join it
#   $NET        the core's public side: the native port (6501) and the way
#               out to the SMTP relay
#   $NET-NAME   one per server extension, without NAT (no way out): its
#               published port, bound to its address there only
# So a server extension reaches only the core's 6590 and 6599 (which want a
# key; the admin socket also an admin-role key), not the core's 6501, not the
# other extensions' ports, and not the internet.
#
#   sudo sh install-split.sh parlornet:VERSION
#
# It replaces the single `parlornet` container of install.sh (the same ports).
# Server extensions are the ones listed below, not the admin menus' choice.
# Environment, for testing elsewhere: PARLOR_NET, PARLOR_SUBNET (/24, the
# public network), PARLOR_NET_BASE (the first two octets of the other
# networks, 172.30), PARLOR_DATA, PARLOR_KEYDIR, PARLOR_PREFIX,
# PARLOR_PUBLISH_IP (bind published ports to that address), PARLOR_CHOWN=no
# (leave key copies to the invoking user, mode 444: only for a test machine
# where chown is not possible), PARLOR_WEB_HOSTS (web.hosts). ParlorNet Play (6513) is published on the
# host's loopback only, for a TLS proxy on the host (deploy/README.md), or on PARLOR_PUBLISH_IP or every
# address with PARLOR_PLAY_OPEN=1; PARLOR_PLAY_URL (play.public_url) and PARLOR_PLAY_PROXIES (play.proxies:
# for a proxy on the host, the gateway of its own network, 172.30.18.1 by default) go to it. Play's push
# notifications are off here: its network has no way out to the push services. PARLOR_SITE_DIR, a directory
# of the site's own files, is mounted read-only into the core's container for its motd.txt and into the web
# extension's for welcome.htm and pages/ (deploy/README.md, "A site of your own").
set -eu
IMAGE=${1:?usage: install-split.sh IMAGE}
NET=${PARLOR_NET:-parlor}
SUBNET=${PARLOR_SUBNET:-172.30.4.0/24}
BASE=${PARLOR_NET_BASE:-172.30}
DATA=${PARLOR_DATA:-/srv/parlornet}
KEYDIR=${PARLOR_KEYDIR:-/srv/parlornet-keys}
PREFIX=${PARLOR_PREFIX:-parlornet}
IP=${PARLOR_PUBLISH_IP:+$PARLOR_PUBLISH_IP:}
CORE_UID=10042
CORE_PUB=${SUBNET%.*}.10            # the core on $NET
CORE_INT=$BASE.5.2                  # the core on $NET-int

net() {     # name, subnet, extra options (sh has no locals: names of its own)
	net_name=$1 net_subnet=$2
	shift 2
	docker network inspect "$net_name" >/dev/null 2>&1 ||
		docker network create --subnet "$net_subnet" "$@" "$net_name" >/dev/null
}
if [ "$NET" = parlor ]; then
	net parlor "$SUBNET" -o com.docker.network.bridge.name=br-parlor
else
	net "$NET" "$SUBNET"
fi
net "$NET-int" "$BASE.5.0/24" --internal

# the same flags for every container: no capabilities, nothing gained by exec, read-only
SAFE="--cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp:size=16m"

if [ "${PARLOR_CHOWN:-yes}" = no ]; then
	install -d -m 755 "$DATA" "$KEYDIR"
else
	install -d -m 700 -o $CORE_UID -g $CORE_UID "$DATA"
	install -d -m 755 -o 0 -g 0 "$KEYDIR"
fi

PLAY_PUB=127.0.0.1:6513
[ "${PARLOR_PLAY_OPEN:-}" = 1 ] && PLAY_PUB=6513

# name uid published-port container-port network-number (published on the host's loopback for admin)
EXTENSIONS="web 10043 6500 6500 11
telnet 10044 6502 6502 12
ios 10045 6503 6503 13
fibs 10047 6505 6505 14
outland 10048 6506 4000 15
doom 10051 6507 6507 17
play 10055 $PLAY_PUB 6513 18
admin 10049 127.0.0.1:6510 6510 16"

# Keys: enrolled in the core's key directory, as the core's user, in a throwaway container
# with the same flags and no network; each extension gets a copy owned by its own uid, mode 400.
docker run --rm --user $CORE_UID:$CORE_UID $SAFE --network none -v "$DATA:/data" --entrypoint python "$IMAGE" \
	-m parlord.frontends --keys /data/frontends add web telnet ios fibs outland doom play >/dev/null
docker run --rm --user $CORE_UID:$CORE_UID $SAFE --network none -v "$DATA:/data" --entrypoint python "$IMAGE" \
	-m parlord.frontends --keys /data/frontends add admin --role admin >/dev/null
echo "$EXTENSIONS" | while read -r n uid pub port num; do
	if [ "${PARLOR_CHOWN:-yes}" = no ]; then
		install -m 444 "$DATA/frontends/$n.key" "$KEYDIR/$n.key"
	else
		install -m 400 -o "$uid" -g "$uid" "$DATA/frontends/$n.key" "$KEYDIR/$n.key"
	fi
done

[ "$PREFIX" = parlornet ] && { docker rm -f parlornet >/dev/null 2>&1 || true; }    # install.sh's single container
docker rm -f "$PREFIX-core" >/dev/null 2>&1 || true
SETTINGS=
[ -f /etc/parlornet/settings.conf ] && SETTINGS="-v /etc/parlornet/settings.conf:/run/parlornet/settings.conf:ro"
SETTINGS_ARG=
[ -n "$SETTINGS" ] && SETTINGS_ARG="--settings-file /run/parlornet/settings.conf"
SITE= MOTD=/app/deploy/motd.txt
if [ -n "${PARLOR_SITE_DIR:-}" ]; then
	case $PARLOR_SITE_DIR in
	/*[!A-Za-z0-9/._-]* | [!/]*)
		echo "install-split.sh: PARLOR_SITE_DIR is an absolute path of letters, digits and / . _ -" >&2
		exit 2 ;;
	esac
	[ -d "$PARLOR_SITE_DIR" ] || { echo "install-split.sh: PARLOR_SITE_DIR $PARLOR_SITE_DIR is not a directory" >&2; exit 2; }
	SITE="-v $PARLOR_SITE_DIR:/run/parlornet/site:ro"
	[ -f "$PARLOR_SITE_DIR/motd.txt" ] && MOTD=/run/parlornet/site/motd.txt
fi
# The core: the database; 6501 on its public address, 6590 and 6599 on its internal one only.
# shellcheck disable=SC2086
docker create --name "$PREFIX-core" --restart unless-stopped --network "$NET" --ip "$CORE_PUB" \
	--user $CORE_UID:$CORE_UID $SAFE --pids-limit 64 --memory 192m \
	--health-cmd "python -c \"import socket; socket.create_connection(('$CORE_PUB', 6501), 3)\"" \
	-p "${IP}6501:6501" -v "$DATA:/data" $SETTINGS $SITE --entrypoint python "$IMAGE" \
	-m parlord --host "$CORE_PUB" --port 6501 --db /data/parlord.db --name "${PARLOR_NAME:-parlornet}" \
	--auth "${PARLOR_AUTH:-password}" --signup "${PARLOR_SIGNUP:-email}" --motd "$MOTD" \
	--frontend-host "$CORE_INT" --frontend-port 6590 --keys /data/frontends \
	--admin-host "$CORE_INT" --admin-port 6599 --admin-telnet 6510 $SETTINGS_ARG >/dev/null
docker network connect --ip "$CORE_INT" --alias parlord "$NET-int" "$PREFIX-core"
docker start "$PREFIX-core" >/dev/null

echo "$EXTENSIONS" | while read -r n uid pub port num; do
	docker rm -fv "$PREFIX-$n" >/dev/null 2>&1 || true      # -v: the image's empty /data volume
	# its own network, without NAT: its published port, and no way out
	net "$NET-$n" "$BASE.$num.0/24" -o com.docker.network.bridge.enable_ip_masquerade=false
	addr=$BASE.$num.2
	case $pub in *:*) publish="$pub:$port" ;; *) publish="${IP}$pub:$port" ;; esac
	extra=
	if [ "$n" = admin ]; then core="--core-admin parlord:6599"; else core="--core parlord:6590"; fi
	module=adapters.$n
	[ "$n" = web ] && [ -n "${PARLOR_WEB_HOSTS:-}" ] && extra="--hosts $PARLOR_WEB_HOSTS"
	[ "$n" = web ] && [ -n "${PARLOR_PLAY_URL:-}" ] && extra="$extra --play-url $PARLOR_PLAY_URL"
	mount=
	[ "$n" = web ] && [ -n "$SITE" ] && { mount=$SITE; extra="$extra --site /run/parlornet/site"; }
	if [ "$n" = play ]; then        # its port is its public URL's, else 6513
		module=adapters.playserver
		[ -n "${PARLOR_PLAY_URL:-}" ] && extra="--public-url $PARLOR_PLAY_URL"
		[ -n "${PARLOR_PLAY_PROXIES:-}" ] && extra="$extra --proxies $PARLOR_PLAY_PROXIES"
	elif [ "$n" != admin ]; then
		extra="$extra --public-port $pub"     # the port players connect to, declared to the core
	fi
	# shellcheck disable=SC2086
	docker create --name "$PREFIX-$n" --restart unless-stopped --network "$NET-$n" --ip "$addr" \
		--user "$uid:$uid" $SAFE --no-healthcheck --pids-limit 16 --memory 96m -p "$publish" \
		-v "$KEYDIR/$n.key:/run/parlornet/key:ro" $mount --entrypoint python "$IMAGE" \
		-m "$module" --host "$addr" --port "$port" $core --key /run/parlornet/key $extra >/dev/null
	docker network connect "$NET-int" "$PREFIX-$n"
	docker start "$PREFIX-$n" >/dev/null
done
echo "started: $PREFIX-core and $(echo "$EXTENSIONS" | awk '{printf "%s ", $1}')"
