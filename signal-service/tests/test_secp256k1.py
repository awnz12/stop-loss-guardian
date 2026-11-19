import unittest

from signal_service import secp256k1
from signal_service.keccak256 import keccak256

N = secp256k1.N


class TestSecp256k1(unittest.TestCase):
    def test_private_key_1_address(self):
        # Well-known: key 0x01 -> 0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf
        self.assertEqual(secp256k1.private_key_to_address(1),
                         "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf")

    def test_sign_recover_roundtrip(self):
        priv = 0x1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF
        digest = keccak256(b"penjaga stop-loss")
        v, r, s = secp256k1.sign_digest(priv, digest)
        self.assertIn(v, (27, 28))
        self.assertLessEqual(s, N // 2, "signature must be low-s (contract ecrecover parity)")
        recovered = secp256k1.recover_address(digest, v, r, s)
        self.assertEqual(recovered.lower(), secp256k1.private_key_to_address(priv).lower())

    def test_signature_is_deterministic(self):
        digest = keccak256(b"deterministic")
        self.assertEqual(secp256k1.signature_bytes(7, digest),
                         secp256k1.signature_bytes(7, digest))

    def test_signature_bytes_layout(self):
        sig = secp256k1.signature_bytes(3, keccak256(b"layout"))
        self.assertEqual(len(sig), 65)
        self.assertIn(sig[64], (27, 28))


if __name__ == "__main__":
    unittest.main()
