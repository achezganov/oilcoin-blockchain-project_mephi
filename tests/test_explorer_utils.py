import unittest

from blockchain import Block, Blockchain
from explorer_utils import (
    find_block_by_hash,
    find_block_by_index,
    find_transaction_by_id,
    get_address_transactions,
    search_explorer,
)


class ExplorerUtilsTests(unittest.TestCase):
    def setUp(self):
        self.addr_a = "OIL_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.addr_b = "OIL_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        genesis = Block(
            version=2,
            index=0,
            timestamp="2026-01-01T00:00:00+00:00",
            transactions=[
                {
                    "tx_id": "GENESIS_TX",
                    "sender": "SYSTEM",
                    "recipient": "GENESIS",
                    "amount": 0.0,
                    "fee": 0.0,
                    "burn": 0.0,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "type": "genesis",
                }
            ],
            previous_hash="0",
            nonce=0,
        )

        block_1 = Block(
            version=2,
            index=1,
            timestamp="2026-01-01T00:01:00+00:00",
            transactions=[
                {
                    "tx_id": "TX_A",
                    "sender": "SYSTEM",
                    "recipient": self.addr_a,
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
        block_1.mine_block(1)

        self.chain = Blockchain(difficulty=1, chain=[genesis, block_1], pending_transactions=[])
        self.chain.pending_transactions = [
            {
                "tx_id": "PENDING_1",
                "sender": self.addr_a,
                "recipient": self.addr_b,
                "amount": 5.0,
                "fee": 1.0,
                "burn": 0.5,
                "timestamp": "2026-01-01T00:02:00+00:00",
                "type": "transfer",
            }
        ]

    def test_find_block_by_index(self):
        block = find_block_by_index(self.chain, 1)
        self.assertIsNotNone(block)
        self.assertEqual(block.index, 1)

    def test_find_block_by_hash(self):
        block = find_block_by_hash(self.chain, self.chain.chain[1].hash)
        self.assertIsNotNone(block)
        self.assertEqual(block.index, 1)

    def test_find_transaction_by_tx_id(self):
        tx = find_transaction_by_id(self.chain, "tx_a")
        self.assertIsNotNone(tx)
        self.assertEqual(tx["recipient"], self.addr_a)

    def test_address_transactions_include_confirmed_and_pending(self):
        ops = get_address_transactions(self.chain, self.addr_a)
        statuses = {op["status"] for op in ops}
        self.assertIn("confirmed", statuses)
        self.assertIn("pending", statuses)

    def test_search_numeric_query_finds_block(self):
        result = search_explorer(self.chain, "1")
        self.assertEqual(result["type"], "block")

    def test_search_oil_address_detects_address(self):
        result = search_explorer(self.chain, self.addr_a)
        self.assertEqual(result["type"], "address")

    def test_invalid_search_returns_not_found_or_invalid(self):
        result = search_explorer(self.chain, "qwerty_not_found")
        self.assertIn(result["type"], {"not_found", "invalid"})


if __name__ == "__main__":
    unittest.main()
