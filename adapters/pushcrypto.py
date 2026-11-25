"""The cryptography Web Push needs, in plain Python with the standard library only (the image has no crypto
package, and the project adds none):

- P-256 (NIST, secp256r1): key pairs, ECDH, and ECDSA with SHA-256 (ES256), with deterministic nonces as in
  RFC 6979, so signing does not depend on the quality of a random number at that moment;
- AES-128 and AES-128-GCM (FIPS 197, NIST SP 800-38D);
- HKDF with SHA-256 (RFC 5869), as RFC 8188 and RFC 8291 use it;
- the aes128gcm content coding (RFC 8188) and Web Push message encryption (RFC 8291);
- VAPID (RFC 8292): the ES256 JWT and the Authorization header.

tests/test_push.py checks it against the vectors of those RFCs, RFC 6979's P-256 signatures, FIPS 197, the
NIST GCM test cases and a NIST P-256 ECDH vector.

Python's integers are not constant-time, so neither is this. The keys it holds are the Play extension's
VAPID key, which only lets its holder ask push services to deliver to this server's own subscribers, and
throwaway keys made for one message each. The work runs on a thread of its own, away from the event loop,
and its timing is lost behind the push services' network round trips.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

# ---- P-256 --------------------------------------------------------------------------------------------------
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
     0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)
_INF = (0, 1, 0)            # the point at infinity, in Jacobian coordinates (Z = 0)


def on_curve(pt):
    if pt is None:
        return False
    x, y = pt
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0


def _double(pt):
    X1, Y1, Z1 = pt
    if Z1 == 0 or Y1 == 0:
        return _INF
    delta = Z1 * Z1 % P
    gamma = Y1 * Y1 % P
    beta = X1 * gamma % P
    alpha = 3 * (X1 - delta) * (X1 + delta) % P
    X3 = (alpha * alpha - 8 * beta) % P
    Z3 = ((Y1 + Z1) * (Y1 + Z1) - gamma - delta) % P
    Y3 = (alpha * (4 * beta - X3) - 8 * gamma * gamma) % P
    return X3, Y3, Z3


def _add(p1, p2):
    X1, Y1, Z1 = p1
    X2, Y2, Z2 = p2
    if Z1 == 0:
        return p2
    if Z2 == 0:
        return p1
    Z1Z1, Z2Z2 = Z1 * Z1 % P, Z2 * Z2 % P
    U1, U2 = X1 * Z2Z2 % P, X2 * Z1Z1 % P
    S1, S2 = Y1 * Z2 * Z2Z2 % P, Y2 * Z1 * Z1Z1 % P
    if U1 == U2:
        return _double(p1) if S1 == S2 else _INF
    H, R = (U2 - U1) % P, (S2 - S1) % P
    H2 = H * H % P
    H3 = H * H2 % P
    U1H2 = U1 * H2 % P
    X3 = (R * R - H3 - 2 * U1H2) % P
    Y3 = (R * (U1H2 - X3) - S1 * H3) % P
    return X3, Y3, H * Z1 * Z2 % P


def _affine(pt):
    X, Y, Z = pt
    if Z == 0:
        return None
    zi = pow(Z, -1, P)
    zi2 = zi * zi % P
    return X * zi2 % P, Y * zi2 * zi % P


def mul(k, pt=G):
    """k times the point pt (affine) -> an affine point, or None for infinity. A Montgomery ladder: the same
    steps whatever the bits of k."""
    r0, r1 = _INF, (pt[0], pt[1], 1)
    for i in range(255, -1, -1):
        if (k >> i) & 1:
            r0, r1 = _add(r0, r1), _double(r1)
        else:
            r1, r0 = _add(r0, r1), _double(r0)
    return _affine(r0)


def _add_affine(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return _affine(_add((a[0], a[1], 1), (b[0], b[1], 1)))


def new_private():
    """A private key: a number from 1 to N-1."""
    return secrets.randbelow(N - 1) + 1


def public_bytes(d):
    """The uncompressed public key (65 bytes: 04, x, y) of the private key d."""
    x, y = mul(d)
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def point(data):
    """An uncompressed public key -> (x, y). ValueError unless it is a point on the curve."""
    if len(data) != 65 or data[0] != 4:
        raise ValueError("not an uncompressed P-256 point")
    pt = (int.from_bytes(data[1:33], "big"), int.from_bytes(data[33:], "big"))
    if not on_curve(pt):
        raise ValueError("not a point on P-256")
    return pt


def ecdh(d, peer):
    """The shared secret (the x coordinate, 32 bytes) of the private key d and the peer's public key (bytes)."""
    s = mul(d, point(peer))
    if s is None:
        raise ValueError("no shared secret")
    return s[0].to_bytes(32, "big")


def _rfc6979_k(d, h):
    """The deterministic nonces of RFC 6979, section 3.2, for SHA-256 on P-256 (qlen = hlen = 256)."""
    x = d.to_bytes(32, "big")
    h1 = (int.from_bytes(h, "big") % N).to_bytes(32, "big")
    V, K = b"\x01" * 32, b"\x00" * 32
    K = hmac.new(K, V + b"\x00" + x + h1, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    K = hmac.new(K, V + b"\x01" + x + h1, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    while True:
        V = hmac.new(K, V, hashlib.sha256).digest()
        k = int.from_bytes(V, "big")
        if 1 <= k < N:
            yield k
        K = hmac.new(K, V + b"\x00", hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()


def sign(d, msg):
    """ECDSA with SHA-256 -> (r, s)."""
    h = hashlib.sha256(msg).digest()
    z = int.from_bytes(h, "big")
    for k in _rfc6979_k(d, h):
        r = mul(k)[0] % N
        if not r:
            continue
        s = pow(k, -1, N) * (z + r * d) % N
        if s:
            return r, s


def verify(pub, msg, r, s):
    """Whether (r, s) is an ECDSA SHA-256 signature of msg by the public key pub (bytes)."""
    if not (1 <= r < N and 1 <= s < N):
        return False
    try:
        q = point(pub)
    except ValueError:
        return False
    z = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    w = pow(s, -1, N)
    pt = _add_affine(mul(z * w % N), mul(r * w % N, q))
    return pt is not None and pt[0] % N == r


# ---- AES-128 ------------------------------------------------------------------------------------------------
def _sbox():
    """The AES S-box, worked out (FIPS 197, 5.1.1): the inverse in GF(2^8), then the affine map."""
    def gmul(a, b):
        p = 0
        while b:
            if b & 1:
                p ^= a
            a = ((a << 1) ^ 0x11B) if a & 0x80 else a << 1
            b >>= 1
        return p
    inv = [0] * 256
    for a in range(1, 256):
        for b in range(1, 256):
            if gmul(a, b) == 1:
                inv[a] = b
                break
    box = []
    for a in range(256):
        x = inv[a]
        s = x
        for i in range(1, 5):
            s ^= ((x << i) | (x >> (8 - i))) & 0xFF
        box.append(s ^ 0x63)
    return bytes(box)


SBOX = _sbox()
_X2 = bytes(((a << 1) ^ 0x11B) if a & 0x80 else a << 1 for a in range(256))


def _expand(key):
    if len(key) != 16:
        raise ValueError("AES-128 takes a 16-byte key")
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[t[1]] ^ rcon, SBOX[t[2]], SBOX[t[3]], SBOX[t[0]]]
            rcon = _X2[rcon]
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[r * 4:r * 4 + 4], []) for r in range(11)]


def _encrypt_block(rk, block):
    s = [b ^ k for b, k in zip(block, rk[0])]
    for r in range(1, 11):
        s = [SBOX[b] for b in s]
        # ShiftRows: the state is column by column, byte i is row i % 4 of column i // 4
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]
        if r < 10:
            m = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                t = a0 ^ a1 ^ a2 ^ a3
                m += [a0 ^ t ^ _X2[a0 ^ a1], a1 ^ t ^ _X2[a1 ^ a2], a2 ^ t ^ _X2[a2 ^ a3], a3 ^ t ^ _X2[a3 ^ a0]]
            s = m
        s = [b ^ k for b, k in zip(s, rk[r])]
    return bytes(s)


def aes128_encrypt_block(key, block):
    return _encrypt_block(_expand(key), block)


# ---- AES-128-GCM --------------------------------------------------------------------------------------------
_R = 0xE1 << 120


def _gmul(x, y):
    z, v = 0, y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        v = (v >> 1) ^ _R if v & 1 else v >> 1
    return z


def _ghash(h, aad, ct):
    y = 0
    data = aad + b"\x00" * (-len(aad) % 16) + ct + b"\x00" * (-len(ct) % 16)
    data += (8 * len(aad)).to_bytes(8, "big") + (8 * len(ct)).to_bytes(8, "big")
    for i in range(0, len(data), 16):
        y = _gmul(y ^ int.from_bytes(data[i:i + 16], "big"), h)
    return y


def _ctr(rk, j0, data):
    out = bytearray()
    c = int.from_bytes(j0[12:], "big")
    for i in range(0, len(data), 16):
        c = (c + 1) & 0xFFFFFFFF
        ks = _encrypt_block(rk, j0[:12] + c.to_bytes(4, "big"))
        out += bytes(a ^ b for a, b in zip(data[i:i + 16], ks))
    return bytes(out)


def gcm_encrypt(key, iv, plaintext, aad=b""):
    """AES-128-GCM with a 96-bit IV -> ciphertext followed by the 16-byte tag."""
    if len(iv) != 12:
        raise ValueError("GCM here takes a 12-byte IV")
    rk = _expand(key)
    h = int.from_bytes(_encrypt_block(rk, b"\x00" * 16), "big")
    j0 = iv + b"\x00\x00\x00\x01"
    ct = _ctr(rk, j0, plaintext)
    tag = _ghash(h, aad, ct) ^ int.from_bytes(_encrypt_block(rk, j0), "big")
    return ct + tag.to_bytes(16, "big")


def gcm_decrypt(key, iv, data, aad=b""):
    """The plaintext; ValueError when the tag does not match."""
    if len(iv) != 12 or len(data) < 16:
        raise ValueError("bad GCM input")
    rk = _expand(key)
    h = int.from_bytes(_encrypt_block(rk, b"\x00" * 16), "big")
    j0 = iv + b"\x00\x00\x00\x01"
    ct, tag = data[:-16], data[-16:]
    want = _ghash(h, aad, ct) ^ int.from_bytes(_encrypt_block(rk, j0), "big")
    if not hmac.compare_digest(want.to_bytes(16, "big"), tag):
        raise ValueError("the GCM tag does not match")
    return _ctr(rk, j0, ct)


# ---- HKDF and the aes128gcm content coding (RFC 8188) -------------------------------------------------------
def hkdf(salt, ikm, info, length):
    """HKDF-SHA-256 for at most 32 bytes of output (one block of expansion, all these RFCs need)."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def _keys(salt, ikm):
    return (hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16),
            hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12))


def _nonce(base, seq):
    return (int.from_bytes(base, "big") ^ seq).to_bytes(12, "big")


def ece_encrypt(plaintext, ikm, salt=None, keyid=b"", rs=4096, pad=0):
    """The aes128gcm coding of plaintext in a single record (what a push message is): header, then the
    record, with pad zero bytes of padding after the delimiter."""
    salt = os.urandom(16) if salt is None else salt
    if len(plaintext) + 1 + pad + 16 > rs or len(keyid) > 255:
        raise ValueError("the message does not fit one record")
    cek, nonce = _keys(salt, ikm)
    header = salt + rs.to_bytes(4, "big") + bytes([len(keyid)]) + keyid
    return header + gcm_encrypt(cek, _nonce(nonce, 0), plaintext + b"\x02" + b"\x00" * pad)


def ece_header(body):
    """(salt, rs, keyid, the records) of an aes128gcm body."""
    if len(body) < 21:
        raise ValueError("too short")
    idlen = body[20]
    if len(body) < 21 + idlen:
        raise ValueError("too short")
    return body[:16], int.from_bytes(body[16:20], "big"), body[21:21 + idlen], body[21 + idlen:]


def ece_decrypt(body, ikm):
    """The plaintext of an aes128gcm body, of one or more records; ValueError if it is not well formed."""
    salt, rs, _, data = ece_header(body)
    if rs < 18:
        raise ValueError("bad record size")
    cek, nonce = _keys(salt, ikm)
    out = bytearray()
    records = [data[i:i + rs] for i in range(0, len(data), rs)] or [b""]
    for seq, rec in enumerate(records):
        last = seq == len(records) - 1
        plain = gcm_decrypt(cek, _nonce(nonce, seq), rec).rstrip(b"\x00")
        if not plain or plain[-1] != (2 if last else 1):
            raise ValueError("bad padding delimiter")
        out += plain[:-1]
    return bytes(out)


# ---- Web Push message encryption (RFC 8291) -----------------------------------------------------------------
def _webpush_ikm(ecdh_secret, auth_secret, ua_public, as_public):
    prk_key = hmac.new(auth_secret, ecdh_secret, hashlib.sha256).digest()
    return hmac.new(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01", hashlib.sha256).digest()


def webpush_encrypt(payload, ua_public, auth_secret, as_private=None, salt=None, pad=0):
    """A push message body for the browser's subscription keys (p256dh: ua_public, 65 bytes; auth: 16 bytes).
    A new key pair (as_private) and salt are made for each message unless given (the RFC's example)."""
    if len(auth_secret) != 16:
        raise ValueError("the auth secret is 16 bytes")
    d = new_private() if as_private is None else as_private
    as_public = public_bytes(d)
    ikm = _webpush_ikm(ecdh(d, ua_public), auth_secret, ua_public, as_public)
    return ece_encrypt(payload, ikm, salt, as_public, 4096, pad)


def webpush_decrypt(body, ua_private, auth_secret):
    """What a browser does with a push message: its own private key and auth secret -> the payload."""
    _, _, as_public, _ = ece_header(body)
    ua_public = public_bytes(ua_private)
    ikm = _webpush_ikm(ecdh(ua_private, as_public), auth_secret, ua_public, as_public)
    return ece_decrypt(body, ikm)


# ---- VAPID (RFC 8292) ---------------------------------------------------------------------------------------
def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text):
    text = "".join(text.split())
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def vapid_jwt(d, aud, sub, exp=None):
    """The ES256 JWT that identifies this server to a push service (aud: the push service's origin)."""
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps({"aud": aud, "exp": int(exp or time.time() + 12 * 3600), "sub": sub},
                           separators=(",", ":")).encode())
    r, s = sign(d, ("%s.%s" % (head, body)).encode("ascii"))
    return "%s.%s.%s" % (head, body, b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big")))


def vapid_header(d, aud, sub, exp=None):
    """The Authorization header's value: vapid t=<JWT>, k=<the public key>."""
    return "vapid t=%s, k=%s" % (vapid_jwt(d, aud, sub, exp), b64u(public_bytes(d)))


def load_or_make_key(path):
    """The VAPID private key kept at path, made there on first use (mode 600, created exclusively). Its value
    is never printed or logged. ValueError if the file is not a key."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path, encoding="ascii") as f:
            text = f.read().strip()
        try:
            d = int.from_bytes(unb64u(text), "big")
        except (ValueError, TypeError):
            raise ValueError("the VAPID key file does not hold a key")
        if len(text) != 43 or not 1 <= d < N:
            raise ValueError("the VAPID key file does not hold a key")
        return d
    d = new_private()
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(b64u(d.to_bytes(32, "big")) + "\n")
    return d
