import re

from wallet import is_valid_address
from blockchain import to_units, from_units


HEX_64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _safe_lower(value: str) -> str:
    return (value or "").strip().lower()


def _tx_identifier(tx: dict) -> str:
    return str(tx.get("tx_id", "")).strip()


def find_block_by_index(blockchain, index):
    try:
        target = int(index)
    except (TypeError, ValueError):
        return None

    for block in blockchain.chain:
        if block.index == target:
            return block
    return None


def find_block_by_hash(blockchain, block_hash):
    target = _safe_lower(block_hash)
    if not target:
        return None
    for block in blockchain.chain:
        if _safe_lower(getattr(block, "hash", "")) == target:
            return block
    return None


def find_transaction_location(blockchain, tx_id):
    target = _safe_lower(tx_id)
    if not target:
        return None

    for block in blockchain.chain:
        for tx in block.transactions:
            current_id = _safe_lower(_tx_identifier(tx))
            if current_id and current_id == target:
                return {
                    "status": "confirmed",
                    "block_index": block.index,
                    "transaction": tx,
                }

    for tx in blockchain.pending_transactions:
        current_id = _safe_lower(_tx_identifier(tx))
        if current_id and current_id == target:
            return {
                "status": "pending",
                "block_index": None,
                "transaction": tx,
            }
    return None


def find_transaction_by_id(blockchain, tx_id):
    location = find_transaction_location(blockchain, tx_id)
    if not location:
        return None
    return location.get("transaction")


def get_pending_transactions_for_address(blockchain, address):
    target = (address or "").strip()
    result = []
    for tx in blockchain.pending_transactions:
        if tx.get("sender") == target or tx.get("recipient") == target:
            result.append(tx)
    return result


def get_address_transactions(blockchain, address):
    target = (address or "").strip()
    operations = []

    for block in blockchain.chain:
        for tx in block.transactions:
            if tx.get("sender") == target or tx.get("recipient") == target:
                operations.append(
                    {
                        "status": "confirmed",
                        "block_index": block.index,
                        "transaction": tx,
                    }
                )

    for tx in blockchain.pending_transactions:
        if tx.get("sender") == target or tx.get("recipient") == target:
            operations.append(
                {
                    "status": "pending",
                    "block_index": None,
                    "transaction": tx,
                }
            )

    operations.sort(
        key=lambda item: item.get("transaction", {}).get("timestamp", ""),
        reverse=True,
    )
    return operations


def get_address_details(blockchain, address):
    target = (address or "").strip()
    confirmed_balance = blockchain.get_balance(target)
    reserved_pending = blockchain.get_reserved_pending(target)
    available_balance = confirmed_balance - reserved_pending

    operations = get_address_transactions(blockchain, target)

    stats = summarize_operations(operations, target)
    return {
        "address": target,
        "valid_address": is_valid_address(target),
        "confirmed_balance": round(confirmed_balance, 8),
        "reserved_pending": round(reserved_pending, 8),
        "available_balance": round(available_balance, 8),
        "stats": stats,
        "operations": operations,
    }


def summarize_operations(operations, target):
    total_sent = 0
    total_received = 0
    total_mining_rewards = 0
    total_miner_fees = 0
    total_burned_by_wallet = 0
    confirmed_count = 0
    pending_count = 0

    for op in operations:
        tx = op["transaction"]
        status = op["status"]
        tx_type = tx.get("type")
        amount = to_units(tx.get("amount", 0))
        burn = to_units(tx.get("burn", 0))

        if status == "confirmed":
            confirmed_count += 1
        else:
            pending_count += 1

        if status != "confirmed":
            continue

        if tx_type == "transfer":
            if tx.get("sender") == target:
                total_sent += amount
                total_burned_by_wallet += burn
            if tx.get("recipient") == target:
                total_received += amount
        elif tx_type == "mining_reward" and tx.get("recipient") == target:
            total_mining_rewards += amount
        elif tx_type == "miner_fee" and tx.get("recipient") == target:
            total_miner_fees += amount

    return {
        "total_sent": from_units(total_sent),
        "total_received": from_units(total_received),
        "total_mining_rewards": from_units(total_mining_rewards),
        "total_miner_fees": from_units(total_miner_fees),
        "total_burned_by_wallet": from_units(total_burned_by_wallet),
        "confirmed_transaction_count": confirmed_count,
        "pending_transaction_count": pending_count,
    }


def search_explorer(blockchain, query):
    q = (query or "").strip()
    if len(q) > 128:
        return {"type": "invalid", "target": "", "message": "Запрос должен содержать не более 128 символов.", "data": None, "redirect_url": None}
    if not q:
        return {
            "type": "invalid",
            "target": "",
            "message": "Введите номер блока, хэш, tx_id или адрес кошелька.",
            "data": None,
            "redirect_url": None,
        }

    if q.isascii() and q.isdecimal():
        block = find_block_by_index(blockchain, int(q))
        if block:
            return {
                "type": "block",
                "target": q,
                "message": "Найден блок по индексу.",
                "data": {"index": block.index, "hash": block.hash},
                "redirect_url": f"/block/{block.index}",
            }
        return {
            "type": "not_found",
            "target": q,
            "message": f"Ничего не найдено по запросу: {q}",
            "data": None,
            "redirect_url": None,
        }

    if q.startswith("OIL_"):
        if not is_valid_address(q):
            return {
                "type": "invalid",
                "target": q,
                "message": "Запрос не похож на номер блока, хэш, tx_id или адрес OilCoin.",
                "data": None,
                "redirect_url": None,
            }
        return {
            "type": "address",
            "target": q,
            "message": "Найден адрес кошелька.",
            "data": {"address": q},
            "redirect_url": f"/address/{q}",
        }

    if HEX_64_RE.match(q):
        block = find_block_by_hash(blockchain, q)
        if block:
            return {
                "type": "block",
                "target": q,
                "message": "Найден блок по хэшу.",
                "data": {"index": block.index, "hash": block.hash},
                "redirect_url": f"/block/{block.index}",
            }

    location = find_transaction_location(blockchain, q)
    if location:
        tx = location["transaction"]
        tx_id = _tx_identifier(tx)
        return {
            "type": "transaction",
            "target": q,
            "message": "Найдена транзакция.",
            "data": {
                "status": location["status"],
                "block_index": location["block_index"],
                "tx_id": tx_id,
            },
            "redirect_url": f"/tx/{tx_id}",
        }

    return {
        "type": "not_found",
        "target": q,
        "message": f"Ничего не найдено по запросу: {q}",
        "data": None,
        "redirect_url": None,
    }
