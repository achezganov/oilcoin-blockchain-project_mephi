"""Validated atomic persistence. Malformed data is never silently reset."""
import json
import os
from blockchain import Blockchain
from database import unit_of_work

def load_blockchain(filename=None, difficulty=None):
    with unit_of_work(filename) as conn:
        state = conn.execute("SELECT payload FROM chain_state WHERE id=1").fetchone()
        if state is None:
            if conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]:
                raise ValueError("Missing chain metadata")
            chain = Blockchain(difficulty=difficulty if difficulty is not None else int(os.getenv("MINING_DIFFICULTY", "3")))
            save_blockchain(chain, filename)
            return chain
        payload = json.loads(state[0])
        if not isinstance(payload, dict):
            raise ValueError("Chain metadata must be an object")
        payload["chain"] = []
        for row in conn.execute("SELECT height,hash,payload FROM blocks ORDER BY height"):
            block = json.loads(row[2])
            if not isinstance(block, dict) or block.get("index") != row[0] or block.get("hash") != row[1]:
                raise ValueError("Block index metadata does not match its payload")
            payload["chain"].append(block)
        payload["pending_transactions"] = []
        for row in conn.execute("SELECT tx_id,payload FROM pending_transactions ORDER BY position"):
            tx = json.loads(row[1])
            if not isinstance(tx, dict) or tx.get("tx_id") != row[0]:
                raise ValueError("Mempool index does not match transaction ID")
            payload["pending_transactions"].append(tx)
        chain = Blockchain.from_dict(payload)
        valid, message = chain.is_chain_valid()
        if not valid:
            raise ValueError("Corrupt blockchain: " + message)
        valid, message = chain.validate_pending_transactions()
        if not valid:
            raise ValueError("Corrupt mempool: " + message)
        return chain

def save_blockchain(blockchain, filename=None):
    for valid, message in (blockchain.is_chain_valid(), blockchain.validate_pending_transactions()):
        if not valid:
            raise ValueError(message)
    payload = blockchain.to_dict()
    blocks = payload.pop("chain")
    pending = payload.pop("pending_transactions")
    with unit_of_work(filename) as conn:
        existing = dict(conn.execute("SELECT height,hash FROM blocks"))
        if len(existing) > len(blocks):
            raise ValueError("Refusing to truncate confirmed chain")
        for block in blocks:
            height = block["index"]
            if height in existing and existing[height] != block["hash"]:
                raise ValueError("Refusing to overwrite confirmed block")
            conn.execute("INSERT OR IGNORE INTO blocks VALUES (?,?,?)", (height, block["hash"], json.dumps(block)))
        conn.execute("INSERT INTO chain_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (json.dumps(payload),))
        conn.execute("DELETE FROM pending_transactions")
        conn.executemany("INSERT INTO pending_transactions VALUES (?,?,?)", [(i, tx["tx_id"], json.dumps(tx)) for i, tx in enumerate(pending)])
