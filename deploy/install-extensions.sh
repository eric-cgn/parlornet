#!/bin/sh
# Runs some of a parlor's server extensions on this host, against a core on another host. Run it as root
# once the image is loaded here, with the core's extension channel and the names of the extensions:
#   sudo PARLOR_CORE=10.0.0.1:6590 sh install-extensions.sh parlornet:VERSION web telnet play
# Running it again replaces the containers and keeps the data. It removes the containers of extensions
# that an earlier run started and this one does not name.
#
# The core's host runs deploy/install.sh with PARLOR_FRONTEND_PUBLISH and PARLOR_REMOTE_EXTENSIONS, which
# publish its extension channel on a private link and give a key for each of these extensions. Copy each
# key to this host's key directory as NAME.key. deploy/README.md, "Server extensions on another host",
# shows both hosts.
#
# Each extension runs in a container of its own under its own uid, as in deploy/install-split.sh. The
# script makes the container's copy of its key belong to that uid, with mode 400, and mounts it read-only.
# Every container runs with --cap-drop ALL, no capability added, no-new-privileges and a read-only root.
# None uses host networking. They share one Docker network, parlor-ext, whose subnet is 172.30.6.0/24 and
# whose gateway is 172.30.6.1. The containers cannot reach each other on it. A proxy on this host reaches
# them from the gateway, so 172.30.6.1 is the value for PARLOR_WEB_PROXIES and PARLOR_PLAY_PROXIES.
#
# These settings come from this script's environment:
#   PARLOR_CORE       the core's extension channel as host:port, such as 10.0.0.1:6590 over WireGuard
#                     (required)
#   PARLOR_PUBLISH_IP  the host address that the players' ports are published on (default: every address)
#   PARLOR_KEYDIR     the directory that holds the keys copied from the core's host (/srv/parlornet-keys)
#   PARLOR_DATA       the directory for the extensions' own files (/srv/parlornet-extensions). ParlorNet
#                     Play keeps its push subscriptions and its VAPID key in play/ there.
#   PARLOR_WEB_HOSTS, PARLOR_WEB_URL, PARLOR_WEB_PROXIES  the web's names, its own address behind a proxy,
#                     and the proxies it believes, as for deploy/install.sh
#   PARLOR_PLAY_URL, PARLOR_PLAY_PROXIES, PARLOR_PLAY_PUSH_CONTACT  the same for ParlorNet Play. The web
#                     also takes PARLOR_PLAY_URL, for its links to Play.
#   PARLOR_PLAY_OPEN=1  publish 6513 like the players' ports. Without it, Play is on the host's loopback
#                     only, for a TLS proxy on this host.
#   PARLOR_MEMORY     each container's memory limit, Docker's --memory (default 96m)
#   PARLOR_MEMORY_SWAP  each container's limit on memory and swap together, Docker's --memory-swap
#                     (default: Docker's own)
# The admin menus' values for these settings reach only the extensions in the core's container.
set -eu
IMAGE=${1:?usage: install-extensions.sh IMAGE NAME...}
shift
[ $# -gt 0 ] || { echo "usage: install-extensions.sh IMAGE NAME..." >&2; exit 2; }
CORE=${PARLOR_CORE:?"PARLOR_CORE is the core's extension channel, host:port"}
case $CORE in *:[0-9]*) ;; *) echo "install-extensions.sh: PARLOR_CORE is host:port" >&2; exit 2 ;; esac
KEYDIR=${PARLOR_KEYDIR:-/srv/parlornet-keys}
DATA=${PARLOR_DATA:-/srv/parlornet-extensions}
IP=${PARLOR_PUBLISH_IP:+$PARLOR_PUBLISH_IP:}
PLAY_IP=127.0.0.1:
[ "${PARLOR_PLAY_OPEN:-}" = 1 ] && PLAY_IP=$IP
MEMORY="--memory ${PARLOR_MEMORY:-96m}"
if [ -n "${PARLOR_MEMORY_SWAP:-}" ]; then MEMORY="$MEMORY --memory-swap $PARLOR_MEMORY_SWAP"; fi
NET=parlor-ext
LABEL=org.parlornet.extension

# The server extensions players connect to (adapters/catalog.py), with the uid each has in the image, the
# port it listens on, the host ports it is published on, and the port it declares to the core when that is
# another one. The admin menus and the bot stay with the core.
EXTENSIONS="web 10043 adapters.web 6500 6500 -
telnet 10044 adapters.telnet 6502 6502 -
ios 10045 adapters.ios 6503 6503 -
fibs 10047 adapters.fibs 6505 6505 -
outland 10048 adapters.outland 4000 6506 6506
doom 10051 adapters.doom 6507 6507 -
gamemaster 10052 adapters.gamemaster 6508 6508 -
netsprocket 10054 adapters.netsprocket 6512 6512 -
play 10055 adapters.playserver 6513 6513 -
batnav 10057 adapters.batnav 6514 6514 -
awale 10056 adapters.awale 6515 6515 -
networdz 10061 adapters.networdz 6516 6516 -
netluff 10058 adapters.netluff 6517 6517,1744 1744
icds 10059 adapters.icds 6518 6518 -
worthy 10060 adapters.worthy 6519 6519 -
rscards 10062 adapters.rscards 6520 6520 -"

for n in "$@"; do
	case $n in *[!a-z0-9-]* | "") n= ;; esac
	if [ -z "$n" ] || ! echo "$EXTENSIONS" | grep -q "^$n "; then
		echo "install-extensions.sh: $* names an extension that this script does not run" >&2
		exit 2
	fi
	if [ ! -f "$KEYDIR/$n.key" ]; then
		echo "install-extensions.sh: $KEYDIR/$n.key is missing. Copy it from the core's host." >&2
		exit 2
	fi
done

docker network inspect "$NET" >/dev/null 2>&1 || docker network create --subnet 172.30.6.0/24 --gateway 172.30.6.1 \
	-o com.docker.network.bridge.name=br-parlor-ext -o com.docker.network.bridge.enable_icc=false "$NET" >/dev/null

# the same flags for every container: no capabilities, nothing gained by exec, read-only
SAFE="--cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp:size=16m"
install -d -m 700 -o 0 -g 0 "$KEYDIR" "$DATA"

# The containers of an earlier run that this one does not name
for c in $(docker ps -aq --filter "label=$LABEL"); do
	n=$(docker inspect -f "{{index .Config.Labels \"$LABEL\"}}" "$c")
	case " $* " in *" $n "*) ;; *) docker rm -fv "$c" >/dev/null ;; esac
done

for n in "$@"; do
	read -r name uid module port publish declare <<EOF
$(echo "$EXTENSIONS" | grep "^$n ")
EOF
	chown "$uid:$uid" "$KEYDIR/$name.key"
	chmod 400 "$KEYDIR/$name.key"
	ports=
	for p in $(echo "$publish" | tr ',' ' '); do
		if [ "$name" = play ]; then ports="$ports -p $PLAY_IP$p:$port"; else ports="$ports -p $IP$p:$port"; fi
	done
	extra=
	if [ "$declare" != - ]; then extra="--public-port $declare"; fi
	vars=
	if [ "$name" = web ]; then vars="PARLOR_WEB_HOSTS PARLOR_WEB_URL PARLOR_WEB_PROXIES PARLOR_PLAY_URL"; fi
	if [ "$name" = play ]; then vars="PARLOR_PLAY_URL PARLOR_PLAY_PROXIES PARLOR_PLAY_PUSH_CONTACT"; fi
	env=
	for v in $vars; do
		eval "x=\${$v:-}"
		if [ -n "$x" ]; then env="$env -e $v=$x"; fi
	done
	data=
	if [ "$name" = play ]; then         # Play's own files: push subscriptions and its VAPID key
		install -d -m 700 -o "$uid" -g "$uid" "$DATA/play"
		data="-v $DATA/play:/data -e PARLOR_PLAY_DATA=/data"
	fi
	docker rm -fv "parlornet-$name" >/dev/null 2>&1 || true     # -v: the image's empty /data volume
	# shellcheck disable=SC2086
	docker run -d --name "parlornet-$name" --label "$LABEL=$name" --restart unless-stopped --network "$NET" \
		--user "$uid:$uid" $SAFE --no-healthcheck --pids-limit 16 $MEMORY $ports $env $data \
		-v "$KEYDIR/$name.key:/run/parlornet/key:ro" --entrypoint python "$IMAGE" \
		-m "$module" --host 0.0.0.0 --port "$port" --core "$CORE" --key /run/parlornet/key $extra >/dev/null
done
echo "install-extensions.sh: started $* against the core at $CORE"
