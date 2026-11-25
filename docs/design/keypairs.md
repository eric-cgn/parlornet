# Design note: a keypair per process (proposal, not built)

This is a proposal for a later version; it is not in use yet. Every process runs in its own container, and the core and each server extension hold their own keypair (Ed25519). The core lists the extensions' public keys, each extension pins the core's as SSH `known_hosts` pins a host key, and both sides authenticate on connect. Keys are added, rotated and revoked by editing those lists, with no certificate authority.

## 1. Today and what changes

Today the channel is TLS 1.3 with an external pre-shared key per server extension (docs/security-design.md, Extension channel; `parlord/frontends.py`, `CoreClient.connect` in `adapters/common.py`). The same 32-byte secret sits in the core's key directory (`/data/frontends/NAME.key`) and in the extension's container.

| Threat | Today: PSK per extension | Keypairs |
|---|---|---|
| Extension's key file stolen | holder acts as that extension to the core, and can pose as the core to that extension (the key is symmetric) | holder acts as that extension to the core; cannot pose as the core |
| Core's key directory stolen (a copy of `/data`, a backup) | every extension's key: act as any extension, and pose as the core to every extension | public keys only; nothing usable. The core's private key is kept outside `/data` (section 3) |
| Compromised extension (code execution) | its own sessions, its own `SRC`; in split mode nothing else | the same: no change |
| Compromised core | everything (the database); also every extension key | everything (the database); no extension's private key |
| Replay | none: fresh handshake randoms, `psk_dhe_ke` (ephemeral DH), no 0-RTT (Python's `ssl` has no early data) | none: TLS 1.3 CertificateVerify signs the handshake transcript, ephemeral DH |
| Impersonating the core | needs that extension's PSK | needs the core's private key |
| Enrollment | the secret key file is copied to the extension's host | only public keys move; a private key never leaves where it was made |

The gain is in the two key-theft rows and in enrollment. Against a compromised process it adds nothing: that is the container layout's job (section 4).

## 2. Handshake options without a CA

**(a) TLS 1.3, self-signed certificates as key carriers, pinned on both sides (standard library only).** Each process has an Ed25519 key and a self-signed certificate for it (`CN=name`, `basicConstraints=critical,CA:FALSE`, a validity of 100 years, since OpenSSL checks the dates of a trust anchor and Python cannot turn that off). Python's `ssl` has no verify callback, so pinning is two steps: (1) each side loads the other side's listed certificates as its only trust anchors (`load_verify_locations(cadata=...)`), `verify_mode = CERT_REQUIRED`, `check_hostname = False`; OpenSSL then accepts exactly the listed certificates and checks the CertificateVerify signature; (2) after the handshake each side takes `getpeercert(binary_form=True)`, computes the fingerprint (SHA-256 of the SubjectPublicKeyInfo, found by a short DER walk) and looks it up in its list; the name and role come from the matching line, never from the certificate's subject. Tested here with Python 3.13.18 and OpenSSL 3.5.9: a listed Ed25519 client certificate completes the handshake and maps to its name; an unlisted one fails with `CERTIFICATE_VERIFY_FAILED: self-signed certificate`. Mutual pinning therefore works without a CA; the custom step is the fingerprint lookup after the handshake. Key generation is not in the standard library: `openssl req -x509 -newkey ed25519 -nodes -keyout NAME.pem -out NAME.crt -days 36500 -subj /CN=NAME -addext basicConstraints=critical,CA:FALSE` on the host (OpenSSL 1.1.1 or later; macOS's `/usr/bin/openssl`, LibreSSL 3.3.6, has no Ed25519; Debian's has).

**(b) TLS 1.3 raw public keys (RFC 7250).** This is exactly the model, with no certificate at all. OpenSSL has it since 3.2, but Python's `ssl` exposes no API for it (3.13), so it is not usable.

**(c) A signed-challenge step inside the existing PSK channel (Ed25519).** After the PSK handshake each side sends a nonce and signs both nonces, both names and the channel binding. Python has no `tls-exporter` (no `export_keying_material`); `get_channel_binding("tls-unique")` returns matching 32-byte values on both ends of a TLS 1.3 PSK connection (tested), though RFC 9266 does not define `tls-unique` for TLS 1.3. Without a binding, someone holding the PSK and on the path can relay the challenge. It keeps the PSK (two secrets per extension) and needs the `cryptography` package (the standard library has no Ed25519): a new dependency, which needs Eric's approval.

**(d) Noise (SSH-like), e.g. `Noise_KK_25519_ChaChaPoly_BLAKE2s`.** Both static keys are known in advance, which is this model. It replaces TLS with hand-wired framing, uses X25519 keys rather than Ed25519, and needs `cryptography` and a Noise library (or our own). It is the most new code to review.

**Recommendation: (a).**

| Cost | (a) |
|---|---|
| Dependencies | none in the image; `openssl` (1.1.1+) on the host to make keys |
| Python | no change (3.13 stays; (a) itself does not need 3.13) |
| Code | about 200 lines: contexts and the fingerprint lookup in `parlord/frontends.py` and `CoreClient.connect` (PSK callbacks go), a list reader, the key tool (`add`, `remove`, `list`, `fingerprint`), key making and mounts in `deploy/install-split.sh`, the sweep keyed by fingerprint, tests |
| Operations | list lines carry a certificate (about 400 characters of base64), not a bare 32-byte key; certificates are made once and do not expire in practice |

## 3. The key lists

| File | Lives | Mounted |
|---|---|---|
| core: its private key and certificate | host `/srv/parlornet-keys/core.pem`, `core.crt`, uid 10042, mode 400 | core, read-only, `/run/parlornet/` |
| core: `extensions` (the extensions' public keys) | host `/srv/parlornet-keys/extensions`, mode 444 | core, read-only |
| each extension: its private key and certificate | host `/srv/parlornet-keys/NAME/key.pem`, `.crt`, its uid, mode 400 | that extension only, read-only |
| each extension: `known_cores` (the core's public key) | host `/srv/parlornet-keys/known_cores`, mode 444 | every extension, read-only |

The core's private key is not in `/data`, so a copy of the database does not carry it.

Each list has one key per line, with `#` comments:

    # name  role      algorithm   key (base64 DER certificate)  comment
    web     frontend  ed25519-cert MIIBNjCB6aADAgECAhR...        made 2026-12-01
    admin   admin     ed25519-cert MIIBOTCB7KADAgECAhR...
    parlord core      ed25519-cert MIIBMjCB5aADAgECAhR...        (known_cores)

A name may have two lines (overlap). Fingerprints are shown as `SHA256:` and the unpadded base64 of SHA-256 over the SubjectPublicKeyInfo, the same form as `ssh-keygen -l`, and checkable without our code: `openssl pkey -in key.pem -pubout -outform DER | openssl dgst -sha256 -binary | base64`.

- **Add**: make the key where it will live; append the certificate line to the other side's list. Both sides reread their list on each handshake (by modification time, as `Keyring` does now).
- **Rotate with overlap**: make the new key; add its line beside the old one; restart the extension on the new key; remove the old line. For the core: add the new line to `known_cores`, restart the core on the new key, remove the old line.
- **Revoke**: remove the line. The core's sweep (every 10 seconds) closes connections whose fingerprint is no longer listed and ends the sessions issued under it, as for a removed key today. An extension checks `known_cores` at each new connection; its live connections to a revoked core key end when that core restarts, or at once by restarting the extension.
- **Sessions**: they stay bound to name and fingerprint. Proposed: `SES` succeeds while the session's fingerprint is still listed under the same name, so overlap keeps players signed in and removal ends them (today any rotation ends them; question 2).
- **Admin console**: a "Server extensions" screen lists name, role, fingerprint, comment, live connections and last handshake per line, and the fingerprint of any refused handshake (`unknown key SHA256:... as web`). The lists are read-only mounts, so the screen shows the host command to run rather than editing (question 3).
- **Out-of-band check**: after adding a line, read the fingerprint on the side that holds the private key (the `openssl` line above, or `python -m parlord.frontends fingerprint`) and compare it with what the console shows, over a separate channel (two SSH sessions, a phone call for a remote host).

## 4. The container layout

The layout of `deploy/install-split.sh`; the keypair change only replaces its key copies with the mounts above.

| Port | Container | Bound to | Reached by |
|---|---|---|---|
| 6500, 6502, 6503, 6505, 6506 (Outland's 4000) | web, telnet, ios, fibs, outland | its address on its own network `parlor-NAME` (no NAT) | published on the host |
| 6501 | core | its address on `parlor` (its way out to SMTP) | published on the host |
| 6590, 6599 | core | its address on `parlor-int` (`--internal`) only | the extensions over `parlor-int`; never published |
| 6510 | admin | its address on `parlor-admin` | published on the host's loopback only |

Every container: `--user` its own uid, `--cap-drop ALL`, no capability added, `no-new-privileges`, read-only root, no host networking.

Keypairs do nothing directly about the Docker Desktop reach (Residuals, R2-4), which is network-level: an extension reaching the core's 6501, other extensions' published ports, the admin container's 6510, and the host's loopback through `host.docker.internal`. Keypairs change who can complete the handshake on 6590 and 6599, which already needs a key. The fix belongs to the host:

- name every bridge (`-o com.docker.network.bridge.name=br-pn-NAME`; today only `br-parlor` is named) so firewall rules can match them;
- on the host firewall, drop forwarding from each `br-pn-NAME` except to `br-pn-int`, and drop input from those bridges to the host itself (the bridge gateway reaches every host service listening on 0.0.0.0; Docker does not block that).

Docker Desktop cannot be fixed this way (its VM and port forwarder are not the host's firewall); split mode there is for development only.

### The Linux check (before split mode is recommended)

The check must not run on a production host, because it creates networks and containers and floods 6590. It also can't run on a host whose Docker daemon has `iptables`, `ip-forward` or the default bridge turned off, since Docker then enforces neither `--internal` nor bridge isolation, or on a host whose own firewall drops traffic by default, since every result would then come from that firewall and not from Docker.

The check needs a throwaway Linux VM with a stock Docker Engine (iptables on) and no restrictive host firewall. Its outbound traffic should be cut at the hypervisor, and the VM deleted afterwards.

It needs Docker Engine with root (`sudo`), `python3` on the host, the repository at main on the VM, ports 6500-6510 free on the VM's loopback. Run from the repository root:

    sudo docker build -t parlornet:splitcheck .
    sudo PARLOR_PREFIX=pnrt PARLOR_NET=pnrt PARLOR_SUBNET=172.31.4.0/24 PARLOR_NET_BASE=172.31 \
      PARLOR_DATA=/srv/pnrt PARLOR_KEYDIR=/srv/pnrt-keys PARLOR_PUBLISH_IP=127.0.0.1 \
      PARLOR_AUTH=password PARLOR_SIGNUP=password sh deploy/install-split.sh parlornet:splitcheck
    # reach from a compromised extension (the web's and the admin container's view)
    for c in pnrt-web pnrt-admin; do sudo docker exec -i $c python - \
      172.31.5.2:6590 172.31.5.2:6599 172.31.4.10:6501 \
      172.31.12.2:6502 172.31.13.2:6503 172.31.14.2:6505 172.31.15.2:4000 172.31.16.2:6510 172.31.11.2:6500 \
      172.31.11.1:22 172.31.11.1:6501 172.31.5.1:22 HOST_LAN_IP:22 HOST_LAN_IP:6501 \
      OUTSIDE_IP:8080 < review/split2_probe.py; done
    # OUTSIDE_IP: a listener the tester starts on a separate network (not the internet)
    # slots: baseline, then with the web flooding the extension channel
    python3 review/split2_check.py 20 baseline
    sudo docker exec -i pnrt-web python - flood 172.31.5.2 6590 40 < review/split2_probe.py &
    sleep 3; python3 review/split2_check.py 20 flood; wait
    # the rules Docker made
    sudo iptables -S FORWARD; sudo iptables -S DOCKER-ISOLATION-STAGE-1; sudo iptables -S DOCKER-ISOLATION-STAGE-2
    sysctl net.ipv4.conf.all.route_localnet

The check passes when each probe prints its own uid (10043 web, 10049 admin) and `reach` only for 172.31.5.2:6590 and 172.31.5.2:6599; every other target `blocked` (a container's own address excepted). The host-gateway and LAN targets may be reachable until the input rules above are in place; that is recorded as a finding, then re-run with the rules. `split2_check.py` shows 20/20 and 3/3 both times. If `route_localnet` is 1, a check from another LAN machine that 6510 (loopback-only) is not reachable is added.

Clean up:

    sudo docker rm -f pnrt-core pnrt-web pnrt-telnet pnrt-ios pnrt-fibs pnrt-outland pnrt-admin
    sudo docker network rm pnrt pnrt-int pnrt-web pnrt-telnet pnrt-ios pnrt-fibs pnrt-outland pnrt-admin
    sudo rm -rf /srv/pnrt /srv/pnrt-keys
    sudo docker image rm parlornet:splitcheck

The result holds for the Docker version tested. Compare the production host's Docker version before relying on it there.

## 5. Migration and rollback

All processes run on one host and are replaced together, so there is no period with both channels: the new version speaks only (a).

1. The Linux check passes on the test host, with the firewall rules.
2. On the production host: `docker stop parlornet`; copy `/srv/parlornet` to `/srv/parlornet.bak-DATE`.
3. `install-split.sh` (new version): makes the keys and lists under `/srv/parlornet-keys`, removes the `parlornet` container, starts the core and the extensions on the same ports. It leaves `/srv/parlornet/frontends` (the PSK keys) as it is.
4. Add the bridge rules to the host firewall.
5. Check: every public port answers; the core's log names each extension and fingerprint at its handshake; the console lists them.

Every stored session ends once (their fingerprints are PSK fingerprints): players sign in again.

To roll back, run `docker rm -f parlornet-core parlornet-web parlornet-telnet parlornet-ios parlornet-fibs parlornet-outland parlornet-admin`, then `sh install.sh parlornet:OLD`. The old container uses `/srv/parlornet` and its PSK keys as before; sessions issued in split mode end at their first resume. If the new version changed the database schema, restore the copy from step 2 (games since then are lost). The extra networks can stay or be removed.

## 6. Open questions

1. Option (a), standard library only, or (c)/(d) with the `cryptography` dependency?
2. Rotation: keep an extension's sessions while its old and new keys are both listed (proposed), or end them at once as today?
3. Admin console: show the lists and the host commands (proposed; the lists stay read-only mounts), or edit them (the core's list would move to a writable place the core controls)?
4. Keypairs pay off with separate containers or remote extensions; in the single default container one user reads every file anyway. Build them only together with moving parlornet.org to split mode?
5. The host firewall needs rules for the new bridges. Who makes that change, and when?
6. Which host runs the Linux check? A throwaway VM with a stock Docker Engine is proposed.
