import unittest

from wallet import (
    address_from_public_key, generate_wallet, generate_wallet_keys,
    is_valid_address, public_key_from_private, sign_message, verify_signature,
)


class WalletTests(unittest.TestCase):
    def test_generated_keys_are_consistent_and_distinct(self):
        first, second = generate_wallet_keys(), generate_wallet_keys()
        self.assertTrue(is_valid_address(first['address']))
        self.assertNotEqual(first, second)
        self.assertEqual(public_key_from_private(first['private_key']), first['public_key'])
        self.assertEqual(address_from_public_key(first['public_key']), first['address'])
        self.assertTrue(is_valid_address(generate_wallet()))

    def test_signature_binds_message_and_public_key(self):
        wallet, other = generate_wallet_keys(), generate_wallet_keys()
        signature = sign_message(wallet['private_key'], b'transfer:one')
        self.assertTrue(verify_signature(wallet['public_key'], signature, b'transfer:one'))
        self.assertFalse(verify_signature(wallet['public_key'], signature, b'transfer:two'))
        self.assertFalse(verify_signature(other['public_key'], signature, b'transfer:one'))
        self.assertFalse(verify_signature('invalid', signature, b'transfer:one'))
        self.assertFalse(verify_signature(wallet['public_key'], 'invalid', b'transfer:one'))

    def test_address_validation_rejects_ambiguous_inputs(self):
        for value in (None, True, [], ' OIL_' + 'a' * 32, 'OIL_' + 'A' * 32,
                      'OIL_' + 'a' * 31, 'OIL_' + 'g' * 32, 'OIL_' + 'a' * 32 + '\n'):
            with self.subTest(value=value):
                self.assertFalse(is_valid_address(value))


if __name__ == '__main__':
    unittest.main()
