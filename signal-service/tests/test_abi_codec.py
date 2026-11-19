import unittest

from signal_service import abi_codec

TOKEN = "0x0000000000000000000000000000000000007e57"


def word(value: int) -> bytes:
    return value.to_bytes(32, "big")


class TestAbiCodec(unittest.TestCase):
    def test_selectors(self):
        self.assertEqual(abi_codec.selector("updateSignalSigned(address,uint256,bool,uint256,bytes)").hex(),
                         "172700eb")
        self.assertEqual(abi_codec.selector("getSignal(address)").hex(), "1329c0a9")
        self.assertEqual(abi_codec.selector("priceOf(address)").hex(), "b95ed06f")
        self.assertEqual(abi_codec.selector("DOMAIN_SEPARATOR()").hex(), "3644e515")

    def test_encode_update_signal_signed_matches_cast(self):
        # Reference vector produced by `cast calldata` (Foundry 1.9.0) for
        # updateSignalSigned(0x...7e57, 78, true, 1795003600, 0x11 * 65).
        sig = bytes([0x11] * 65)
        expected = (
            bytes.fromhex("172700eb")
            + word(0x7E57)            # token
            + word(78)                # triggerPrice
            + word(1)                 # armed
            + word(1795003600)        # deadline
            + word(160)               # offset of bytes arg
            + word(65)                # signature length
            + sig + b"\x00" * 31      # padded to a 32-byte multiple
        )
        actual = abi_codec.encode_update_signal_signed(TOKEN, 78, True, 1795003600, sig)
        self.assertEqual(actual, expected)

    def test_decode_signal(self):
        # Layout recorded from the live registry after the attempt-5 loop:
        # triggerPrice=90, armed=true, nonce=1, updatedAt=1795001087.
        payload = word(90) + word(1) + word(1) + word(1795001087)
        state = abi_codec.decode_signal(payload)
        self.assertEqual(state, {"trigger_price": 90, "armed": True,
                                 "nonce": 1, "updated_at": 1795001087})

    def test_decode_uint256(self):
        self.assertEqual(abi_codec.decode_uint256(word(85)), 85)


if __name__ == "__main__":
    unittest.main()
