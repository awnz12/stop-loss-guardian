"""EIP-712 typed-data encoding for AISignalRegistry signed updates.

Verified field-by-field against src/origin/AISignalRegistry.sol:

- Domain (constructor): name "AgenticStopLossSignal", version "1",
  chainId = block.chainid at deployment, verifyingContract = the registry.
  DOMAIN_SEPARATOR = keccak256(abi.encode(
      keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
      keccak256("AgenticStopLossSignal"), keccak256("1"), chainId, registry))
- Struct: type string "SignalUpdate(address token,uint256 triggerPrice,bool armed,uint256 nonce,uint256 deadline)"
  structHash = keccak256(abi.encode(TYPEHASH, token, triggerPrice, armed, nonce, deadline))
  where nonce MUST be the token's current on-chain signal nonce + 1
  (the contract computes expectedNonce itself and hashes it in — a signature
  over any other nonce recovers the wrong signer and reverts BadSignature).
- Digest: keccak256("\\x19\\x01" || DOMAIN_SEPARATOR || structHash).
- Signature: 65 bytes r || s || v, v in {27, 28} (the contract also accepts
  v < 27 by adding 27, and rejects anything else).
"""

from .keccak256 import keccak256
from . import secp256k1

DOMAIN_NAME = "AgenticStopLossSignal"
DOMAIN_VERSION = "1"

_DOMAIN_TYPE_STRING = b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
SIGNAL_TYPE_STRING = b"SignalUpdate(address token,uint256 triggerPrice,bool armed,uint256 nonce,uint256 deadline)"

DOMAIN_TYPEHASH = keccak256(_DOMAIN_TYPE_STRING)
SIGNAL_TYPEHASH = keccak256(SIGNAL_TYPE_STRING)


def _addr_bytes(address: str) -> bytes:
    a = address.lower()
    if a.startswith("0x"):
        a = a[2:]
    raw = bytes.fromhex(a)
    if len(raw) != 20:
        raise ValueError(f"not an address: {address}")
    return raw


def _abi_word(value) -> bytes:
    """One 32-byte ABI word for uint256 / bool / address values."""
    if isinstance(value, bool):
        return (1 if value else 0).to_bytes(32, "big")
    if isinstance(value, int):
        return value.to_bytes(32, "big")
    if isinstance(value, str):  # address
        return b"\x00" * 12 + _addr_bytes(value)
    if isinstance(value, bytes):  # bytes32
        if len(value) != 32:
            raise ValueError("bytes word must be 32 bytes")
        return value
    raise TypeError(f"cannot ABI-encode {type(value)}")


def domain_separator(chain_id: int, verifying_contract: str) -> bytes:
    """Recompute the registry's DOMAIN_SEPARATOR exactly as its constructor does."""
    return keccak256(
        _abi_word(DOMAIN_TYPEHASH)
        + _abi_word(keccak256(DOMAIN_NAME.encode()))
        + _abi_word(keccak256(DOMAIN_VERSION.encode()))
        + _abi_word(chain_id)
        + _abi_word(verifying_contract)
    )


def struct_hash(token: str, trigger_price: int, armed: bool, nonce: int, deadline: int) -> bytes:
    return keccak256(
        _abi_word(SIGNAL_TYPEHASH)
        + _abi_word(token)
        + _abi_word(trigger_price)
        + _abi_word(armed)
        + _abi_word(nonce)
        + _abi_word(deadline)
    )


def update_digest(chain_id: int, registry: str, token: str, trigger_price: int,
                  armed: bool, nonce: int, deadline: int) -> bytes:
    """The final EIP-712 digest the operator signs."""
    return keccak256(
        b"\x19\x01"
        + domain_separator(chain_id, registry)
        + struct_hash(token, trigger_price, armed, nonce, deadline)
    )


def sign_update(priv: int, chain_id: int, registry: str, token: str, trigger_price: int,
                armed: bool, nonce: int, deadline: int) -> bytes:
    """Produce the 65-byte operator signature over a signal update."""
    digest = update_digest(chain_id, registry, token, trigger_price, armed, nonce, deadline)
    return secp256k1.signature_bytes(priv, digest)
