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


def _reward_tx() -> dict:
    return {
        "tx_id": "R1",
        "sender": "SYSTEM",
        "recipient": "OIL_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "amount": 50.0,
        "fee": 0.0,
        "burn": 0.0,
        "timestamp": "2026-01-01T00:01:00+00:00",
        "type": "mining_reward",
    }


class ChainValidationTests(unittest.TestCase):
    def _build_valid_chain(self) -> Blockchain:
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
            transactions=[_reward_tx()],
            previous_hash=genesis.hash,
            nonce=0,
        )
        block_1.mine_block(difficulty=1)
        return Blockchain(difficulty=1, chain=[genesis, block_1], pending_transactions=[])

    def test_invalid_current_hash_detected(self):
        chain = self._build_valid_chain()
        chain.chain[1].hash = "0000broken"
        ok, message = chain.is_chain_valid()
        self.assertFalse(ok)
        self.assertIn("Invalid current hash", message)

    def test_invalid_pow_detected(self):
        chain = self._build_valid_chain()
        chain.chain[1].nonce = 0
        chain.chain[1].hash = chain.chain[1].calculate_hash()
        while chain.chain[1].hash.startswith("0" * chain.difficulty):
            chain.chain[1].nonce += 1
            chain.chain[1].hash = chain.chain[1].calculate_hash()
        ok, message = chain.is_chain_valid()
        self.assertFalse(ok)
        self.assertIn("Invalid proof-of-work", message)


if __name__ == "__main__":
    unittest.main()
