import copy
from accounts import new_wallet_account, decrypt_private_key


DEMO_PASSWORD = "OilCoin-demo-2026"
DEMO_LABELS = [
    "Alice Driller",
    "Bob Barrel",
    "Carol Miner",
    "Dave Trader",
    "Eve Observer",
]


def is_demo_wallet(label_or_account) -> bool:
    if isinstance(label_or_account, dict):
        return bool(label_or_account.get("is_demo"))
    if isinstance(label_or_account, str):
        return label_or_account.strip() in DEMO_LABELS
    return False


def _find_wallet_by_label(wallet_accounts: dict, label: str) -> dict | None:
    for wallet in wallet_accounts.get("wallets", []):
        if wallet.get("is_demo") and str(wallet.get("label", "")).strip() == label:
            return wallet
    return None


def create_demo_wallets(wallet_accounts) -> list[dict]:
    if not isinstance(wallet_accounts, dict):
        wallet_accounts = {"wallets": []}
    if "wallets" not in wallet_accounts or not isinstance(wallet_accounts["wallets"], list):
        wallet_accounts["wallets"] = []

    created = []
    for label in DEMO_LABELS:
        existing = _find_wallet_by_label(wallet_accounts, label)
        if existing:
            existing["is_demo"] = True
            continue

        account = new_wallet_account(label, DEMO_PASSWORD, is_demo=True)
        wallet_accounts["wallets"].append(account)
        created.append(account)
    return created


def _has_existing_demo_activity(blockchain, demo_addresses: set[str]) -> bool:
    for block in blockchain.chain:
        for tx in block.transactions:
            sender = tx.get("sender")
            recipient = tx.get("recipient")
            if sender in demo_addresses or recipient in demo_addresses:
                if tx.get("type") in {"transfer", "mining_reward", "miner_fee"}:
                    return True

    for tx in blockchain.pending_transactions:
        if tx.get("sender") in demo_addresses or tx.get("recipient") in demo_addresses:
            return True
    return False


def _wallet_map(demo_wallets: list[dict]) -> dict[str, str]:
    return {wallet["label"]: wallet["address"] for wallet in demo_wallets}


def run_demo_scenario(blockchain, demo_wallets) -> dict:
    labels = _wallet_map(demo_wallets)
    addresses = set(labels.values())
    keys = {wallet["address"]: decrypt_private_key(wallet) for wallet in demo_wallets}

    if _has_existing_demo_activity(blockchain, addresses):
        return {
            "blocks_mined": 0,
            "transactions_confirmed": 0,
            "transactions_pending": len(
                [
                    tx
                    for tx in blockchain.pending_transactions
                    if tx.get("sender") in addresses or tx.get("recipient") in addresses
                ]
            ),
            "total_demo_volume": 0.0,
            "scenario_skipped": True,
        }

    blocks_mined = 0
    confirmed_transfers = 0
    total_demo_volume = 0.0

    # Initial funding via mining
    for miner in [labels["Alice Driller"], labels["Carol Miner"], labels["Alice Driller"], labels["Carol Miner"]]:
        ok, _msg, _block = blockchain.mine_pending_transactions(miner)
        if not ok:
            raise ValueError(_msg)
        blocks_mined += 1

    planned_transfers = [
        (labels["Alice Driller"], labels["Bob Barrel"], 10.0, 2.0),
        (labels["Alice Driller"], labels["Dave Trader"], 15.0, 1.5),
        (labels["Carol Miner"], labels["Bob Barrel"], 5.0, 1.0),
    ]

    for sender, recipient, amount, fee in planned_transfers:
        ok, _msg = blockchain.add_transaction(sender, recipient, amount, fee, private_key=keys[sender])
        if not ok:
            raise ValueError(_msg)
        confirmed_transfers += 1
        total_demo_volume += amount

    ok, _msg, _block = blockchain.mine_pending_transactions(labels["Carol Miner"])
    if not ok:
        raise ValueError(_msg)
    blocks_mined += 1

    ok, _msg = blockchain.add_transaction(
        labels["Dave Trader"], labels["Eve Observer"], 5.0, 1.0, private_key=keys[labels["Dave Trader"]]
    )
    if not ok:
        raise ValueError(_msg)
    confirmed_transfers += 1
    total_demo_volume += 5.0

    ok, _msg, _block = blockchain.mine_pending_transactions(labels["Alice Driller"])
    if not ok:
        raise ValueError(_msg)
    blocks_mined += 1

    pending_seed = [
        (labels["Bob Barrel"], labels["Eve Observer"], 3.0, 0.6),
        (labels["Carol Miner"], labels["Dave Trader"], 4.0, 0.8),
    ]

    pending_created = 0
    for sender, recipient, amount, fee in pending_seed:
        ok, _msg = blockchain.add_transaction(sender, recipient, amount, fee, private_key=keys[sender])
        if not ok:
            raise ValueError(_msg)
        pending_created += 1

    return {
        "blocks_mined": blocks_mined,
        "transactions_confirmed": confirmed_transfers,
        "transactions_pending": pending_created,
        "total_demo_volume": round(total_demo_volume, 8),
        "scenario_skipped": False,
    }


def create_demo_data(blockchain, wallet_accounts) -> tuple[bool, str, dict]:
    valid, message = blockchain.is_chain_valid()
    if not valid:
        return False, f"Cannot generate demo data on invalid chain: {message}", {}

    original_chain, original_wallets = blockchain, wallet_accounts
    blockchain = copy.deepcopy(blockchain)
    wallet_accounts = copy.deepcopy(wallet_accounts)
    created_wallets = create_demo_wallets(wallet_accounts)
    demo_wallets = [_find_wallet_by_label(wallet_accounts, label) for label in DEMO_LABELS]
    demo_wallets = [wallet for wallet in demo_wallets if wallet is not None]

    if len(demo_wallets) < len(DEMO_LABELS):
        return False, "Demo wallets are incomplete. Please retry generation.", {}

    try:
        scenario = run_demo_scenario(blockchain, demo_wallets)
    except ValueError as error:
        return False, f"Демо-сценарий не выполнен, состояние сохранено: {error}", {}
    original_chain.chain = blockchain.chain
    original_chain.pending_transactions = blockchain.pending_transactions
    original_chain.burned_supply = blockchain.burned_supply
    original_wallets.clear()
    original_wallets.update(wallet_accounts)

    summary = {
        "wallets_created": len(created_wallets),
        "blocks_mined": scenario["blocks_mined"],
        "transactions_confirmed": scenario["transactions_confirmed"],
        "transactions_pending": scenario["transactions_pending"],
        "total_demo_volume": scenario["total_demo_volume"],
        "demo_addresses": {w["label"]: w["address"] for w in demo_wallets},
        "scenario_skipped": scenario.get("scenario_skipped", False),
    }

    if summary["scenario_skipped"]:
        return True, "Demo wallets already exist and demo activity is already present.", summary

    return True, "Demo data generated successfully.", summary
