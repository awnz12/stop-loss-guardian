"""Tiny ABI codec for the handful of calls the service makes.

Covered:
- AISignalRegistry.updateSignalSigned(address,uint256,bool,uint256,bytes)  (write, relayed)
- AISignalRegistry.getSignal(address) -> (uint256,bool,uint256,uint256)     (read)
- AISignalRegistry.DOMAIN_SEPARATOR() / SIGNAL_TYPEHASH() / aiOperator()    (reads)
- MockPriceFeed.priceOf(address) -> uint256                                 (read)

Encoding of updateSignalSigned is regression-tested against `cast calldata`
output (see tests/test_abi_codec.py).
"""

from .keccak256 import keccak256
from .eip712 import _abi_word, _addr_bytes


def selector(signature: str) -> bytes:
    return keccak256(signature.encode())[:4]


def encode_call(signature: str, *args) -> bytes:
    """Encode a call whose arguments are all static-size (no dynamic types)."""
    return selector(signature) + b"".join(_abi_word(a) for a in args)


def encode_update_signal_signed(token: str, trigger_price: int, armed: bool,
                                deadline: int, signature: bytes) -> bytes:
    """updateSignalSigned calldata: 5 head words (bytes arg -> offset 160),
    then length + signature padded to a 32-byte multiple."""
    if len(signature) != 65:
        raise ValueError("signature must be 65 bytes")
    head = (
        _abi_word(token)
        + _abi_word(trigger_price)
        + _abi_word(armed)
        + _abi_word(deadline)
        + _abi_word(5 * 32)  # offset of the bytes argument
    )
    tail = _abi_word(len(signature)) + signature + b"\x00" * ((32 - len(signature) % 32) % 32)
    return selector("updateSignalSigned(address,uint256,bool,uint256,bytes)") + head + tail


def encode_get_signal(token: str) -> bytes:
    return encode_call("getSignal(address)", token)


def encode_price_of(token: str) -> bytes:
    return encode_call("priceOf(address)", token)


def decode_uint256(data: bytes) -> int:
    if len(data) < 32:
        raise ValueError("short uint256 response")
    return int.from_bytes(data[:32], "big")


def decode_signal(data: bytes) -> dict:
    """Decode getSignal's (triggerPrice, armed, nonce, updatedAt) tuple."""
    if len(data) < 128:
        raise ValueError("short getSignal response")
    return {
        "trigger_price": int.from_bytes(data[0:32], "big"),
        "armed": int.from_bytes(data[32:64], "big") != 0,
        "nonce": int.from_bytes(data[64:96], "big"),
        "updated_at": int.from_bytes(data[96:128], "big"),
    }


def to_hex(data: bytes) -> str:
    return "0x" + data.hex()


def topic_for_address(address: str) -> str:
    """32-byte log topic for an indexed address argument."""
    return "0x" + (b"\x00" * 12 + _addr_bytes(address)).hex()
