# ParlorNet in one container: the table host (parlord), the frontends the
# admin panel turns on, and the admin menus on port 6510.
#
#   docker build -t parlornet .
#   docker run -d --name parlornet --restart unless-stopped \
#       -p 6500-6505:6500-6505 -p 6507:6507 -p 127.0.0.1:6510:6510 -v parlornet:/data parlornet
#
# Users: by default everything runs as parlord (uid 10042), unprivileged, and
# works with --cap-drop ALL and no capability added. Opt-in, with extra
# capabilities: --user 0 --cap-add SETUID --cap-add SETGID --cap-add KILL runs
# each server extension as its own pn-<name> user, which can read its own key
# and nothing else of /data (docs/security-design.md, Containment;
# deploy/install-split.sh runs one container per extension instead).
# GameMaster (Mac): the ParlorNet rulebook connects to 6508 (-p 6508:6508) and carries every game over that
# one connection. NetBoxes (Mac) joins on 6512 (-p 6512:6512). The batnav clients (X11, ncurses, Windows 3.x)
# connect to 6514 (-p 6514:6514). Awale (Mac) connects over IRC to
# 6515 (-p 6515:6515). Networdz 2.0 (Windows) hosts its game and connects to 6516 (-p 6516:6516).
# NetLuff (Mac) always connects to port 1744:
# publish 6517 under both numbers (-p 6517:6517 -p 1744:6517). xcheckers (X11) connects to 6518
# (-p 6518:6518). Worthy Opponent (DOS) dials 6519 by modem (-p 6519:6519). GEnie's RSCARDS front ends
# dial 6520 through a WiFi modem or a serial bridge (-p 6520:6520). ParlorNet Play, for today's
# browsers, is on 6513:
# publish it on the host's loopback (-p 127.0.0.1:6513:6513) for a reverse proxy there that terminates TLS
# (deploy/README.md), with -e PARLOR_PLAY_URL=https://<name>/play/ and -e PARLOR_PLAY_PROXIES=<the proxy's
# address as the container sees it>.
#   docker logs parlornet        # first launch: how to set it up, with a one-time password
#   telnet 127.0.0.1 6510
#
# README.md ("Run your own parlor") has more.
# Python 3.13 or later: the frontend channel uses TLS 1.3 pre-shared keys (ssl PSK callbacks)
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# the system's CA certificates: the SMTP relay's certificate is verified (parlord/auth.py)
RUN test -s /etc/ssl/certs/ca-certificates.crt
RUN groupadd --system --gid 10042 parlord \
    && useradd --system --uid 10042 --gid 10042 --no-create-home --shell /usr/sbin/nologin parlord \
    && for x in web:10043 telnet:10044 ios:10045 fibs:10047 outland:10048 admin:10049 bot:10050 doom:10051 \
                gamemaster:10052 netsprocket:10054 play:10055 awale:10056 batnav:10057 netluff:10058 icds:10059 \
                worthy:10060 networdz:10061 rscards:10062; do \
         n=${x%%:*}; i=${x##*:}; \
         groupadd --system --gid "$i" "pn-$n" \
         && useradd --system --uid "$i" --gid "$i" --no-create-home --shell /usr/sbin/nologin "pn-$n" \
         && usermod -aG "pn-$n" parlord; \
       done \
    && install -d -m 700 -o parlord -g parlord /data
WORKDIR /app
COPY parlord ./parlord
COPY adapters ./adapters
# the bot player (docs/bot.md): a client of the core, reading the plugins' rules code
COPY bot ./bot
COPY plugins ./plugins
# Grid Words' word list (ENABLE, plugins/gridwords/SOURCES.md) comes with the plugins
RUN test -s /app/plugins/gridwords/words.txt
COPY deploy/start.py deploy/motd.txt ./deploy/
USER parlord
VOLUME /data
# 6500 web, 6501 native protocol, 6502 telnet, 6503 IOS, 6505 FIBS, 6507 DOoM, 4000 Outland,
# 6508 GameMaster (the ParlorNet rulebook), 6512 NetBoxes,
# 6513 ParlorNet Play (plain HTTP, for a TLS proxy in front), 6514 batnav, 6515 Awale, 6516 Networdz,
# 6517 NetLuff (publish it as 1744 too), 6518 ICDS (xcheckers), 6519 Worthy Opponent and
# 6520 RSCARDS (a WiFi modem or a serial bridge).
# The admin menus
# (6510) are not exposed:
# publish them on the host's loopback only (-p 127.0.0.1:6510:6510).
EXPOSE 6500 6501 6502 6503 6505 6507 4000 6508 6512 6513 6514 6515 6516 6517 6518 6519 6520
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import socket; s = socket.create_connection(('127.0.0.1', 6501), 3); s.sendall(b'PARLORNET/1\\r\\n'); assert s.recv(12) == b'PARLORNET/1 '"]
CMD ["python", "/app/deploy/start.py"]
