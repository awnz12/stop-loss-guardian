"""Minimal secp256k1 ECDSA: deterministic signing (RFC 6979) and recovery.

Only what the signal service needs:
- derive the Ethereum address from a private key,
- sign a 32-byte digest producing (v, r, s) with v in {27, 28} and low-s,
  exactly the shape AISignalRegistry._recover accepts,
- recover the signer address from a digest + signature (used by tests to
  prove the EIP-712 payloads are valid before anything is broadcast).
"""

import hashlib
import hmac as hmac_mod

# Curve parameters (SEC 2).
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)

from .keccak256 import keccak256


def _point_add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    x1, y1 = a
    x2, y2 = b
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if a == b:
        slope = (3 * x1 * x1) * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow((x2 - x1) % P, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    y3 = (slope * (x1 - x3) - y1) % P
    return (x3, y3)


def _point_mul(k: int, point=G):
    result = None
    addend = point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def _rfc6979_nonce(priv: int, digest: bytes) -> int:
    """Deterministic per-message nonce, RFC 6979 with HMAC-SHA256."""
    bx = priv.to_bytes(32, "big")
    # bits2octets(h1): reduce the hash modulo the curve order.
    bh = (int.from_bytes(digest, "big") % N).to_bytes(32, "big")
    v = b"\x01" * 32
    k = b"\x00" * 32
    k = hmac_mod.new(k, v + b"\x00" + bx + bh, hashlib.sha256).digest()
    v = hmac_mod.new(k, v, hashlib.sha256).digest()
    k = hmac_mod.new(k, v + b"\x01" + bx + bh, hashlib.sha256).digest()
    v = hmac_mod.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac_mod.new(k, v, hashlib.sha256).digest()
        candidate = int.from_bytes(v, "big")
        if 1 <= candidate < N:
            return candidate
        k = hmac_mod.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac_mod.new(k, v, hashlib.sha256).digest()


def private_key_to_address(priv: int) -> str:
    """Ethereum address (EIP-55 checksummed) for a private key integer."""
    x, y = _point_mul(priv)
    pub = x.to_bytes(32, "big") + y.to_bytes(32, "big")
    addr = keccak256(pub)[-20:].hex()
    # EIP-55 checksum casing.
    hash_hex = keccak256(addr.encode()).hex()
    out = "".join(c.upper() if int(hash_hex[i], 16) >= 8 else c for i, c in enumerate(addr))
    return "0x" + out


def sign_digest(priv: int, digest: bytes):
    """Sign a 32-byte digest. Returns (v, r, s) with v in {27, 28}, s low."""
    if len(digest) != 32:
        raise ValueError("digest must be 32 bytes")
    z = int.from_bytes(digest, "big")
    k = _rfc6979_nonce(priv, digest)
    rx, ry = _point_mul(k)
    r = rx % N
    s = (pow(k, -1, N) * (z + r * priv)) % N
    recid = (ry & 1) | (2 if rx >= N else 0)
    if s > N // 2:  # low-s normalization flips the recovery parity
        s = N - s
        recid ^= 1
    return (27 + recid, r, s)


def signature_bytes(priv: int, digest: bytes) -> bytes:
    """65-byte r || s || v signature, the layout the registry's _recover reads."""
    v, r, s = sign_digest(priv, digest)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([v])


def recover_address(digest: bytes, v: int, r: int, s: int) -> str:
    """Recover the signer address from a digest and (v, r, s). Mirrors ecrecover."""
    if v >= 27:
        v -= 27
    x = r + (v >> 1) * N
    if x >= P:
        raise ValueError("recovered x out of range")
    # y from x: y^2 = x^3 + 7 (mod p); p % 4 == 3 so sqrt is a^( (p+1)/4 ).
    y_sq = (pow(x, 3, P) + 7) % P
    y = pow(y_sq, (P + 1) // 4, P)
    if (y & 1) != (v & 1):
        y = P - y
    r_point = (x, y)
    z = int.from_bytes(digest, "big")
    # Q = r^-1 (s*R - z*G)
    q = _point_mul(pow(r, -1, N), _point_add(_point_mul(s, r_point), _point_mul((-z) % N)))
    xq, yq = q
    pub = xq.to_bytes(32, "big") + yq.to_bytes(32, "big")
    addr = keccak256(pub)[-20:].hex()
    hash_hex = keccak256(addr.encode()).hex()
    out = "".join(c.upper() if int(hash_hex[i], 16) >= 8 else c for i, c in enumerate(addr))
    return "0x" + out
