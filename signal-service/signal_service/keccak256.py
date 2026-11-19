"""Pure-Python Keccak-256 (the original Keccak padding, NOT NIST SHA3-256).

Ethereum uses Keccak-256: pad10*1 with domain suffix 0x01, rate 1088 bits,
capacity 512 bits, Keccak-f[1600] permutation. Python's hashlib only ships
SHA3-256 (suffix 0x06), which produces different digests, so the permutation
is implemented here directly. No third-party dependencies.
"""

_MASK64 = (1 << 64) - 1

# Rho rotation offsets r[x][y].
_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)

# Iota round constants, 24 rounds.
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

_RATE_BYTES = 136  # 1088 bits


def _rotl64(x: int, n: int) -> int:
    if n == 0:
        return x & _MASK64
    return ((x << n) | (x >> (64 - n))) & _MASK64


def _keccak_f1600(state: list) -> None:
    """One Keccak-f[1600] permutation, in place. state = 25 lanes, index x + 5*y."""
    for rc in _RC:
        # Theta
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]
        # Rho + Pi
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl64(state[x + 5 * y], _ROT[x][y])
        # Chi
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
        # Iota
        state[0] ^= rc
        for i in range(25):
            state[i] &= _MASK64


def keccak256(data: bytes) -> bytes:
    """Return the 32-byte Keccak-256 digest of ``data``."""
    state = [0] * 25
    # Padding: suffix 0x01, then zeros, final block byte OR 0x80 (pad10*1).
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % _RATE_BYTES != _RATE_BYTES - 1:
        padded.append(0x00)
    padded.append(0x80)
    for offset in range(0, len(padded), _RATE_BYTES):
        block = padded[offset:offset + _RATE_BYTES]
        for i in range(_RATE_BYTES // 8):
            state[i] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _keccak_f1600(state)
    out = bytearray()
    for i in range(_RATE_BYTES // 8):
        out += state[i].to_bytes(8, "little")
    return bytes(out[:32])
