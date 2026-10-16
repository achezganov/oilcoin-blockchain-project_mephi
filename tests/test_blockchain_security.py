import copy
import unittest
from decimal import Decimal

from blockchain import (
    Blockchain, Block, _transfer_id, _transfer_payload, from_units, to_units,
    verify_transaction_signature,
)
from wallet import generate_wallet_keys, sign_message


class BlockchainSecurityTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = generate_wallet_keys(), generate_wallet_keys()
        self.chain = Blockchain(difficulty=1)
        self.assertTrue(self.chain.mine_pending_transactions(self.a['address'])[0])

    def transfer(self, amount=10, fee=2):
        return self.chain.add_transaction(
            self.a['address'], self.b['address'], amount, fee,
            private_key=self.a['private_key'],
        )

    def sign_modified(self, tx):
        tx['signature'] = sign_message(self.a['private_key'], _transfer_payload(tx))
        tx['tx_id'] = _transfer_id(tx)
        return tx

    def remine_last(self):
        self.chain.chain[-1].mine_block(self.chain.difficulty)

    def test_amount_parser_is_exact(self):
        self.assertEqual(to_units('0.00000001'), 1)
        self.assertEqual(to_units(Decimal('1.23000000000000000000000000000')), 123000000)
        for value in (True, None, '', [], float('nan'), float('inf'), '0.000000001',
                      '1.00000000000000000000000000001', '1e-1000000', '10000001'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                to_units(value)
        for units in (1, 999999999999999, 1000000000000000):
            self.assertEqual(to_units(from_units(units)), units)

    def test_transfer_requires_owners_private_key(self):
        self.assertFalse(self.chain.add_transaction(self.a['address'], self.b['address'], 1, 0)[0])
        self.assertFalse(self.chain.add_transaction(
            self.a['address'], self.b['address'], 1, 0, private_key=self.b['private_key'])[0])
        self.assertEqual(self.chain.pending_transactions, [])

    def test_signed_transfer_mining_conserves_money(self):
        self.assertTrue(self.transfer()[0])
        tx = self.chain.pending_transactions[0]
        self.assertTrue(verify_transaction_signature(tx))
        self.assertEqual(self.chain.get_reserved_pending(self.a['address']), 12)
        self.assertTrue(self.chain.mine_pending_transactions(self.b['address'])[0])
        self.assertEqual(self.chain.get_balance(self.a['address']), 38)
        self.assertEqual(self.chain.get_balance(self.b['address']), 61)
        self.assertEqual(self.chain.calculate_total_supply(), 100)
        self.assertEqual(self.chain.calculate_circulating_supply(), 99)
        self.assertEqual(self.chain.burned_supply, 1)
        self.assertTrue(self.chain.is_chain_valid()[0])

    def test_fractional_atomic_fee_preserves_exact_supply(self):
        self.assertTrue(self.transfer('0.00000001', '0.00000003')[0])
        self.assertEqual(to_units(self.chain.pending_transactions[0]['burn']), 1)
        self.assertTrue(self.chain.mine_pending_transactions(self.b['address'])[0])
        total = to_units(self.chain.get_balance(self.a['address'])) + to_units(self.chain.get_balance(self.b['address']))
        self.assertEqual(total, to_units(self.chain.calculate_circulating_supply()))
        self.assertEqual(to_units(self.chain.burned_supply), 1)

    def test_pending_reservations_prevent_double_spending(self):
        self.assertTrue(self.transfer(30, 0)[0])
        self.assertFalse(self.transfer(21, 0)[0])
        self.assertEqual(len(self.chain.pending_transactions), 1)

    def test_pending_incoming_is_not_spendable(self):
        self.assertTrue(self.transfer(30, 0)[0])
        self.assertFalse(self.chain.add_transaction(
            self.b['address'], self.a['address'], 1, 0, self.b['private_key'])[0])

    def test_forged_signature_blocks_mining_without_mutation(self):
        self.assertTrue(self.transfer()[0])
        self.chain.pending_transactions[0]['recipient'] = generate_wallet_keys()['address']
        before = self.chain.to_dict()
        self.assertFalse(self.chain.mine_pending_transactions(self.a['address'])[0])
        self.assertEqual(before, self.chain.to_dict())

    def test_duplicate_pending_transaction_blocks_mining(self):
        self.assertTrue(self.transfer()[0])
        self.chain.pending_transactions.append(copy.deepcopy(self.chain.pending_transactions[0]))
        self.assertFalse(self.chain.validate_pending_transactions()[0])
        self.assertFalse(self.chain.mine_pending_transactions(self.a['address'])[0])

    def test_confirmed_transaction_cannot_be_replayed(self):
        self.assertTrue(self.transfer()[0])
        original = copy.deepcopy(self.chain.pending_transactions[0])
        self.assertTrue(self.chain.mine_pending_transactions(self.a['address'])[0])
        self.chain.pending_transactions.append(original)
        self.assertFalse(self.chain.validate_pending_transactions()[0])

    def test_sequences_continue_after_reload(self):
        self.assertTrue(self.transfer(1, 0)[0])
        self.assertTrue(self.chain.mine_pending_transactions(self.b['address'])[0])
        self.chain = Blockchain.from_dict(self.chain.to_dict())
        self.assertEqual(self.chain.get_next_sequence(self.a['address']), 2)
        self.assertTrue(self.transfer(1, 0)[0])
        self.assertEqual(self.chain.pending_transactions[0]['sequence'], 2)
        self.assertTrue(self.chain.validate_pending_transactions()[0])

    def test_cross_network_replay_is_rejected(self):
        self.assertTrue(self.transfer()[0])
        other = Blockchain(difficulty=1)
        other.mine_pending_transactions(self.a['address'])
        other.pending_transactions.append(copy.deepcopy(self.chain.pending_transactions[0]))
        self.assertFalse(other.validate_pending_transactions()[0])

    def test_signed_overspend_is_rejected_during_chain_replay(self):
        self.assertTrue(self.transfer(30, 0)[0])
        self.chain.mine_pending_transactions(self.b['address'])
        block = self.chain.chain[-1]
        tx = copy.deepcopy(block.transactions[0])
        tx['amount'] = 21
        tx['sequence'] = 2
        block.transactions.insert(1, self.sign_modified(tx))
        self.remine_last()
        ok, message = self.chain.is_chain_valid()
        self.assertFalse(ok)
        self.assertIn('double-spend', message)

    def test_unsigned_transfer_is_invalid_even_after_remining(self):
        self.assertTrue(self.transfer()[0])
        self.chain.mine_pending_transactions(self.a['address'])
        del self.chain.chain[-1].transactions[0]['signature']
        self.remine_last()
        self.assertFalse(self.chain.is_chain_valid()[0])

    def test_reward_overpayment_is_invalid_even_below_max_supply(self):
        self.chain.chain[-1].transactions[0]['amount'] = 51
        self.remine_last()
        self.assertFalse(self.chain.is_chain_valid()[0])

    def test_multiple_rewards_and_fake_fees_are_invalid(self):
        original = self.chain.to_dict()
        for tx_type in ('mining_reward', 'miner_fee'):
            with self.subTest(tx_type=tx_type):
                self.chain = Blockchain.from_dict(original)
                tx = copy.deepcopy(self.chain.chain[-1].transactions[0])
                tx.update(tx_id='forged-extra-payout', type=tx_type, amount=1)
                self.chain.chain[-1].transactions.append(tx)
                self.remine_last()
                self.assertFalse(self.chain.is_chain_valid()[0])

    def test_fees_cannot_be_omitted_or_diverted(self):
        self.assertTrue(self.transfer()[0])
        self.chain.mine_pending_transactions(self.a['address'])
        original = self.chain.to_dict()
        self.chain.chain[-1].transactions.pop()
        self.remine_last()
        self.assertFalse(self.chain.is_chain_valid()[0])
        self.chain = Blockchain.from_dict(original)
        self.chain.chain[-1].transactions[-1]['recipient'] = self.b['address']
        self.remine_last()
        self.assertFalse(self.chain.is_chain_valid()[0])

    def test_cap_is_lifetime_minted_not_circulating_supply(self):
        self.chain = Blockchain(difficulty=1, max_supply=75)
        self.chain.mine_pending_transactions(self.a['address'])
        self.assertTrue(self.transfer(10, 2)[0])
        self.chain.mine_pending_transactions(self.b['address'])
        self.assertEqual(self.chain.chain[-1].transactions[-2]['amount'], 25)
        self.chain.mine_pending_transactions(self.b['address'])
        self.assertEqual(self.chain.chain[-1].transactions, [])
        self.assertEqual(self.chain.calculate_total_supply(), 75)
        self.assertEqual(self.chain.calculate_circulating_supply(), 74)
        self.assertTrue(self.chain.is_chain_valid()[0])

    def test_configuration_is_committed_in_genesis(self):
        for field, value in (('difficulty', 2), ('mining_reward', 1), ('max_supply', 2)):
            with self.subTest(field=field):
                chain = Blockchain.from_dict(self.chain.to_dict())
                setattr(chain, field, value)
                self.assertFalse(chain.is_chain_valid()[0])

    def test_block_indices_and_genesis_structure_are_enforced(self):
        self.chain.chain[-1].index = 7
        self.remine_last()
        self.assertFalse(self.chain.is_chain_valid()[0])
        genesis = self.chain.chain[0]
        genesis.transactions.append(copy.deepcopy(genesis.transactions[0]))
        genesis.hash = genesis.calculate_hash()
        self.assertFalse(self.chain.is_chain_valid()[0])

    def test_bad_config_and_serialized_data_fail_closed(self):
        for value in (0, -1, True, 7, '1'):
            with self.subTest(difficulty=value), self.assertRaises(ValueError):
                Blockchain(difficulty=value)
        payload = self.chain.to_dict()
        payload['chain'][0].pop('hash')
        with self.assertRaises(ValueError):
            Blockchain.from_dict(payload)
        payload = self.chain.to_dict()
        payload['pending_transactions'] = 'corrupt'
        with self.assertRaises(ValueError):
            Blockchain.from_dict(payload)

    def test_export_is_a_detached_snapshot(self):
        self.assertTrue(self.transfer()[0])
        exported = self.chain.to_dict()
        exported['pending_transactions'][0]['amount'] = 999
        exported['chain'][1]['transactions'][0]['amount'] = 999
        self.assertTrue(self.chain.is_chain_valid()[0])
        self.assertTrue(self.chain.validate_pending_transactions()[0])


if __name__ == '__main__':
    unittest.main()
