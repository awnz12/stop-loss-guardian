import unittest

from signal_service import eip712, secp256k1

REGISTRY = "0xE2C38a9D4fb88038775bc1ca00bB920CD0a416a8"
TOKEN = "0x0000000000000000000000000000000000007e57"
CHAIN_ID = 84532

# Recorded from the deployed registry on Base Sepolia, 2026-11-18:
ON_CHAIN_DOMAIN_SEPARATOR = "ab70fa9c9077dee2f6b74d77d93475f5c626795692b25806f3da1f9a6e0b37fd"
ON_CHAIN_SIGNAL_TYPEHASH = "1c462d68622d694f9df61a38ad11519e99a1f4b05897e1cb94a928c78d72fffb"
DOMAIN_TYPEHASH_KNOWN = "8b73c3c69bb8fe3d512ecc4cf759cc79239f7b179b0ffacaa9a75d522b39400f"


class TestEip712(unittest.TestCase):
    def test_domain_typehash(self):
        self.assertEqual(eip712.DOMAIN_TYPEHASH.hex(), DOMAIN_TYPEHASH_KNOWN)

    def test_signal_typehash_matches_contract(self):
        self.assertEqual(eip712.SIGNAL_TYPEHASH.hex(), ON_CHAIN_SIGNAL_TYPEHASH)

    def test_domain_separator_matches_contract(self):
        # The registry computes this in its constructor from (name, version,
        # chainId, address) — our recomputation must agree byte-for-byte.
        self.assertEqual(eip712.domain_separator(CHAIN_ID, REGISTRY).hex(),
                         ON_CHAIN_DOMAIN_SEPARATOR)

    def test_sign_and_recover(self):
        priv = 1
        digest = eip712.update_digest(CHAIN_ID, REGISTRY, TOKEN, 78, True, 2, 1795003600)
        sig = eip712.sign_update(priv, CHAIN_ID, REGISTRY, TOKEN, 78, True, 2, 1795003600)
        r = int.from_bytes(sig[0:32], "big")
        s = int.from_bytes(sig[32:64], "big")
        v = sig[64]
        self.assertEqual(secp256k1.recover_address(digest, v, r, s),
                         "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf")

    def test_nonce_changes_digest(self):
        d1 = eip712.update_digest(CHAIN_ID, REGISTRY, TOKEN, 78, True, 2, 1795003600)
        d2 = eip712.update_digest(CHAIN_ID, REGISTRY, TOKEN, 78, True, 3, 1795003600)
        self.assertNotEqual(d1, d2)


if __name__ == "__main__":
    unittest.main()
