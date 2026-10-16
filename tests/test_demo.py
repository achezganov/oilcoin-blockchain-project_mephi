import unittest

from blockchain import Blockchain
from demo import DEMO_PASSWORD, create_demo_data, create_demo_wallets, is_demo_wallet


class DemoModeTests(unittest.TestCase):
    def test_create_demo_wallets(self):
        wallet_accounts = {"wallets": []}
        created = create_demo_wallets(wallet_accounts)
        self.assertEqual(len(created), 5)
        self.assertEqual(len(wallet_accounts["wallets"]), 5)
        for wallet in wallet_accounts["wallets"]:
            self.assertTrue(wallet.get("is_demo"))
            self.assertNotEqual(wallet.get("password_hash"), DEMO_PASSWORD)

    def test_demo_generation_is_safe_on_second_run(self):
        blockchain = Blockchain(difficulty=1)
        wallet_accounts = {"wallets": []}

        ok, _msg, summary_1 = create_demo_data(blockchain, wallet_accounts)
        self.assertTrue(ok)
        self.assertEqual(summary_1["wallets_created"], 5)
        self.assertGreaterEqual(summary_1["transactions_pending"], 1)
        self.assertTrue(blockchain.is_chain_valid()[0])

        ok2, _msg2, summary_2 = create_demo_data(blockchain, wallet_accounts)
        self.assertTrue(ok2)
        self.assertEqual(summary_2["wallets_created"], 0)
        self.assertTrue(summary_2.get("scenario_skipped"))

    def test_is_demo_wallet_detects_account_and_label(self):
        self.assertTrue(is_demo_wallet("Alice Driller"))
        self.assertTrue(
            is_demo_wallet(
                {
                    "label": "Random",
                    "is_demo": True,
                }
            )
        )


if __name__ == "__main__":
    unittest.main()


def test_failed_demo_does_not_leave_partial_chain_or_accounts():
    chain = Blockchain(difficulty=1, max_supply=1)
    before = chain.to_dict()
    accounts = {"wallets": []}
    ok, _, _ = create_demo_data(chain, accounts)
    assert not ok
    assert chain.to_dict() == before
    assert accounts == {"wallets": []}


def test_demo_does_not_adopt_normal_account_with_demo_label():
    from accounts import new_wallet_account
    ordinary = new_wallet_account("Alice Driller", "some-password")
    accounts = {"wallets": [ordinary]}
    created = create_demo_wallets(accounts)
    assert len(created) == 5
    assert not is_demo_wallet(ordinary)
    assert all(wallet['address'] != ordinary['address'] for wallet in created)
