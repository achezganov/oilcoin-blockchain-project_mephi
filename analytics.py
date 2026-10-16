"""Read-only metrics derived from confirmed ledger entries."""
from decimal import Decimal
from blockchain import valid_mining_duration

def _sum(txs, field):
    return float(sum((Decimal(str(tx.get(field, 0))) for tx in txs), Decimal(0)))

def get_total_transaction_count(blockchain):
    return sum(tx.get("type") != "genesis" for block in blockchain.chain for tx in block.transactions)

def get_tokenomics_summary(blockchain):
    txs = [tx for block in blockchain.chain for tx in block.transactions]
    transfers = [tx for tx in txs if tx["type"] == "transfer"]
    fees = [tx for tx in txs if tx["type"] == "miner_fee"]
    durations = [block.mining_duration_seconds for block in blockchain.chain[1:]
                 if valid_mining_duration(block.mining_duration_seconds)]
    minted = blockchain.calculate_total_supply()
    remaining = round(blockchain.max_supply - minted, 8)
    count = get_total_transaction_count(blockchain)
    return {
        "max_supply": blockchain.max_supply, "total_minted_supply": minted,
        "circulating_supply": blockchain.calculate_circulating_supply(),
        "burned_supply": blockchain.burned_supply, "remaining_supply": remaining,
        "current_mining_reward": blockchain.mining_reward, "difficulty": blockchain.difficulty,
        "total_blocks": len(blockchain.chain), "total_confirmed_transactions": count,
        "total_pending_transactions": len(blockchain.pending_transactions),
        "total_transfer_volume": _sum(transfers, "amount"), "total_fees_paid": _sum(transfers, "fee"),
        "total_miner_fees_earned": _sum(fees, "amount"), "total_burned_from_fees": _sum(transfers, "burn"),
        "average_transaction_fee": round(_sum(transfers, "fee") / len(transfers), 8) if transfers else 0,
        "average_transactions_per_block": round(count / max(1, len(blockchain.chain)-1), 2),
        "average_block_mining_time": round(float(sum((Decimal(str(value)) for value in durations), Decimal(0)) / len(durations)), 5) if durations else None,
        "estimated_next_block_reward": min(blockchain.mining_reward, remaining),
    }

def get_chart_data(blockchain):
    result = {key: [] for key in ("block_indexes", "total_supply_by_block", "burned_supply_by_block", "transactions_per_block", "average_fee_per_block", "cumulative_volume_by_block", "mining_time_by_block")}
    supply = burn = volume = Decimal(0)
    for block in blockchain.chain:
        transfers = [tx for tx in block.transactions if tx["type"] == "transfer"]
        supply += sum((Decimal(str(tx["amount"])) for tx in block.transactions if tx["type"] == "mining_reward"), Decimal(0))
        burn += sum((Decimal(str(tx["burn"])) for tx in transfers), Decimal(0))
        volume += sum((Decimal(str(tx["amount"])) for tx in transfers), Decimal(0))
        duration = block.mining_duration_seconds if valid_mining_duration(block.mining_duration_seconds) else 0
        values = (block.index, float(supply), float(burn), sum(tx["type"] != "genesis" for tx in block.transactions), round(_sum(transfers,"fee")/len(transfers),8) if transfers else 0, float(volume), duration)
        for key, value in zip(result, values):
            result[key].append(value)
    return result
