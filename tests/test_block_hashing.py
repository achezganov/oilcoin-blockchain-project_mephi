import unittest

from blockchain import Block, Blockchain


def _genesis_tx() -> dict:
    return {
        "tx_id": "GENESIS_TX",
        "sender": "SYSTEM",
        "recipient": "GENESIS",
        "amount": 0.0,
        "fee": 0.0,
        "burn": 0.0,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "type": "genesis",
    }


class BlockHashingTests(unittest.TestCase):
    def test_new_block_defaults_to_v2(self):
        block = Block(
            index=2,
            timestamp="2026-01-01T00:00:00+00:00",
            transactions=[],
            previous_hash="abc",
            nonce=0,
        )
        self.assertEqual(block.version, 2)

    def test_old_block_without_version_loads_as_v1(self):
        block = Block.from_dict(
            {
                "index": 1,
                "timestamp": "2026-01-01T00:00:00+00:00",
                "transactions": [],
                "previous_hash": "abc",
                "nonce": 7,
                "hash": "",
            }
        )
        self.assertEqual(block.version, 1)

    def test_old_block_without_mining_metadata_loads(self):
        block = Block.from_dict(
            {
                "version": 1,
                "index": 2,
                "timestamp": "2026-01-01T00:00:00+00:00",
                "transactions": [],
                "previous_hash": "abc",
                "nonce": 1,
                "hash": "",
            }
        )
        self.assertIsNone(block.mining_started_at)
        self.assertIsNone(block.mining_finished_at)
        self.assertIsNone(block.mining_duration_seconds)

    def test_v2_hash_ignores_mining_metadata(self):
        block = Block(
            version=2,
            index=3,
            timestamp="2026-01-01T01:00:00+00:00",
            transactions=[{"type": "mining_reward", "amount": 50.0}],
            previous_hash="prev",
            nonce=9,
        )
        base_hash = block.calculate_hash()

        block.mining_started_at = "2026-01-01T01:00:00+00:00"
        block.mining_finished_at = "2026-01-01T01:00:01+00:00"
        block.mining_duration_seconds = 1.2345
        self.assertEqual(base_hash, block.calculate_hash())

    def test_changing_mining_duration_does_not_change_v2_hash(self):
        block = Block(
            version=2,
            index=5,
            timestamp="2026-01-01T02:00:00+00:00",
            transactions=[{"type": "miner_fee", "amount": 1.0}],
            previous_hash="prev",
            nonce=10,
        )
        hash_before = block.calculate_hash()
        block.mining_duration_seconds = 999.0
        hash_after = block.calculate_hash()
        self.assertEqual(hash_before, hash_after)

    def test_changing_mining_started_or_finished_does_not_change_v2_hash(self):
        block = Block(
            version=2,
            index=5,
            timestamp="2026-01-01T02:00:00+00:00",
            transactions=[{"type": "miner_fee", "amount": 1.0}],
            previous_hash="prev",
            nonce=10,
        )
        hash_before = block.calculate_hash()
        block.mining_started_at = "2026-01-01T02:00:01+00:00"
        block.mining_finished_at = "2026-01-01T02:00:02+00:00"
        self.assertEqual(hash_before, block.calculate_hash())

    def test_changing_transaction_amount_changes_hash(self):
        block = Block(
            version=2,
            index=6,
            timestamp="2026-01-01T03:00:00+00:00",
            transactions=[{"type": "transfer", "amount": 10.0, "fee": 2.0, "burn": 1.0}],
            previous_hash="prev",
            nonce=11,
        )
        hash_before = block.calculate_hash()
        block.transactions[0]["amount"] = 11.0
        self.assertNotEqual(hash_before, block.calculate_hash())

    def test_changing_previous_hash_changes_hash(self):
        block = Block(
            version=2,
            index=7,
            timestamp="2026-01-01T03:00:00+00:00",
            transactions=[{"type": "transfer", "amount": 10.0, "fee": 2.0, "burn": 1.0}],
            previous_hash="prev_1",
            nonce=11,
        )
        hash_before = block.calculate_hash()
        block.previous_hash = "prev_2"
        self.assertNotEqual(hash_before, block.calculate_hash())

    def test_previous_hash_link_validation_works(self):
        genesis = Block(
            version=2,
            index=0,
            timestamp="2026-01-01T00:00:00+00:00",
            transactions=[_genesis_tx()],
            previous_hash="0",
            nonce=0,
        )
        block_1 = Block(
            version=2,
            index=1,
            timestamp="2026-01-01T00:01:00+00:00",
            transactions=[
                {
                    "tx_id": "R1",
                    "sender": "SYSTEM",
                    "recipient": "OIL_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "amount": 50.0,
                    "fee": 0.0,
                    "burn": 0.0,
                    "timestamp": "2026-01-01T00:01:00+00:00",
                    "type": "mining_reward",
                }
            ],
            previous_hash=genesis.hash,
            nonce=0,
        )
        block_1.mine_block(difficulty=1)

        chain = Blockchain(difficulty=1, chain=[genesis, block_1], pending_transactions=[])
        self.assertTrue(chain.is_chain_valid()[0])

        block_1.previous_hash = "broken"
        block_1.hash = block_1.calculate_hash()
        ok, message = chain.is_chain_valid()
        self.assertFalse(ok)
        self.assertIn("Invalid previous hash link", message)


if __name__ == "__main__":
    unittest.main()
