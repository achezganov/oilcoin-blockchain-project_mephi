"""Verifiable single-node Proof-of-Work ledger (not peer-to-peer consensus).

Balances use integer atomic units (10**-8 OIL), while JSON/display amounts
remain numbers. Transfers require Ed25519 signatures and sender sequences.
Validation reconstructs balances, rewards, fees and supply from genesis.
"""

import copy
import hashlib
import json
import math
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from wallet import (
    address_from_public_key, is_valid_address, public_key_from_private,
    sign_message, verify_signature,
)

ATOMIC_UNITS = 100_000_000
# JSON numbers round-trip without losing an atomic unit within this range.
MAX_MONEY_UNITS = 1_000_000_000_000_000
ALLOWED_TX_TYPES = {"genesis", "transfer", "mining_reward", "miner_fee"}
TRANSFER_FIELDS = {
    "tx_id", "sender", "recipient", "amount", "fee", "burn", "timestamp",
    "type", "version", "network_id", "sequence", "public_key", "signature",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def to_units(value: Any) -> int:
    """Parse exact money; reject rounding, NaN, infinity and bool."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Amount must be a finite number with at most 8 decimal places.")
    text = str(value)
    if len(text) > 128:
        raise ValueError("Monetary value is too large.")
    try:
        number = Decimal(text)
        if not number.is_finite() or number.copy_abs() > Decimal(MAX_MONEY_UNITS) / ATOMIC_UNITS:
            raise ValueError("Monetary value is non-finite or outside the supported range.")
        # Decimal multiplication uses the active precision context. Inspect the
        # original digits first so a tiny fractional tail cannot round away.
        digits = list(number.as_tuple().digits)
        exponent = number.as_tuple().exponent
        while digits and digits[-1] == 0:
            digits.pop()
            exponent += 1
        if digits and exponent < -8:
            raise ValueError("Amounts support at most 8 decimal places; no rounding is allowed.")
        scaled = number * ATOMIC_UNITS
        if scaled != scaled.to_integral_value():
            raise ValueError("Amounts support at most 8 decimal places; no rounding is allowed.")
        return int(scaled)
    except InvalidOperation as exc:
        raise ValueError("Amount must be a finite number.") from exc


def from_units(value: int) -> float:
    return float(Decimal(value) / ATOMIC_UNITS)


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except (ValueError, TypeError, OverflowError):
        return False


def valid_mining_duration(value: Any) -> bool:
    try:
        return type(value) in (int, float) and value >= 0 and math.isfinite(value)
    except OverflowError:
        return False


def _transfer_payload(tx: dict) -> bytes:
    # Domain separation and integer amounts make the signature unambiguous.
    return _canonical({
        "domain": "oilcoin-transfer-v1", "version": tx["version"],
        "network_id": tx["network_id"], "sender": tx["sender"],
        "recipient": tx["recipient"], "amount_units": to_units(tx["amount"]),
        "fee_units": to_units(tx["fee"]), "burn_units": to_units(tx["burn"]),
        "sequence": tx["sequence"], "timestamp": tx["timestamp"],
        "type": tx["type"], "public_key": tx["public_key"],
    })


def _transfer_id(tx: dict) -> str:
    return hashlib.sha256(_transfer_payload(tx) + bytes.fromhex(tx["signature"])).hexdigest()


def verify_transaction_signature(tx: dict) -> bool:
    try:
        return (
            isinstance(tx, dict) and tx.get("type") == "transfer"
            and set(tx) == TRANSFER_FIELDS
            and type(tx["version"]) is int and tx["version"] == 1
            and tx["sender"] == address_from_public_key(tx["public_key"])
            and verify_signature(tx["public_key"], tx["signature"], _transfer_payload(tx))
            and tx["tx_id"] == _transfer_id(tx)
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


class Block:
    def __init__(
        self, index: int, timestamp: str, transactions: list[dict],
        previous_hash: str, nonce: int = 0, block_hash: str = "", version: int = 2,
        mining_started_at: Optional[str] = None,
        mining_finished_at: Optional[str] = None,
        mining_duration_seconds: Optional[float] = None,
    ) -> None:
        self.version = version
        self.index = index
        self.timestamp = timestamp
        self.transactions = copy.deepcopy(transactions)
        self.previous_hash = previous_hash
        self.nonce = nonce
        self.mining_started_at = mining_started_at if _valid_timestamp(mining_started_at) else None
        self.mining_finished_at = mining_finished_at if _valid_timestamp(mining_finished_at) else None
        self.mining_duration_seconds = mining_duration_seconds if valid_mining_duration(mining_duration_seconds) else None
        self.hash = block_hash or self.calculate_hash()

    def calculate_hash(self) -> str:
        if self.version == 1:
            transactions_json = json.dumps(self.transactions, sort_keys=True, allow_nan=False)
            payload = f"{self.index}{self.timestamp}{transactions_json}{self.previous_hash}{self.nonce}"
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return hashlib.sha256(_canonical({
            "version": self.version, "index": self.index,
            "timestamp": self.timestamp, "transactions": self.transactions,
            "previous_hash": self.previous_hash, "nonce": self.nonce,
        })).hexdigest()

    def mine_block(self, difficulty: int) -> None:
        if type(difficulty) is not int or not 1 <= difficulty <= 6:
            raise ValueError("Difficulty must be an integer between 1 and 6.")
        target = "0" * difficulty
        started_perf = time.perf_counter()
        self.mining_started_at = _now_iso()
        self.hash = self.calculate_hash()
        while not self.hash.startswith(target):
            self.nonce += 1
            self.hash = self.calculate_hash()
        self.mining_finished_at = _now_iso()
        self.mining_duration_seconds = round(time.perf_counter() - started_perf, 8)

    def to_dict(self) -> dict:
        return {
            "version": self.version, "index": self.index, "timestamp": self.timestamp,
            "transactions": copy.deepcopy(self.transactions), "previous_hash": self.previous_hash,
            "nonce": self.nonce, "hash": self.hash, "mining_started_at": self.mining_started_at,
            "mining_finished_at": self.mining_finished_at,
            "mining_duration_seconds": self.mining_duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Block":
        if not isinstance(data, dict) or not isinstance(data.get("transactions"), list):
            raise ValueError("Block must contain a transaction list.")
        # Do not coerce malformed consensus data into seemingly valid values.
        return cls(
            version=data.get("version", 1), index=data.get("index"),
            timestamp=data.get("timestamp"), transactions=data["transactions"],
            previous_hash=data.get("previous_hash"), nonce=data.get("nonce", 0),
            block_hash=data.get("hash", ""), mining_started_at=data.get("mining_started_at"),
            mining_finished_at=data.get("mining_finished_at"),
            mining_duration_seconds=data.get("mining_duration_seconds"),
        )


class Blockchain:
    def __init__(
        self, difficulty: int = 4, mining_reward: float = 50.0,
        max_supply: float = 1_000_000.0, burned_supply: float = 0.0,
        chain: Optional[list[Block]] = None,
        pending_transactions: Optional[list[dict]] = None,
    ) -> None:
        self.difficulty = difficulty
        self.mining_reward = from_units(to_units(mining_reward))
        self.max_supply = from_units(to_units(max_supply))
        self.burned_supply = from_units(to_units(burned_supply))
        self._check_configuration()
        self.chain = chain if chain is not None else [self.create_genesis_block()]
        self.pending_transactions = copy.deepcopy(pending_transactions) if pending_transactions is not None else []

    def _check_configuration(self) -> None:
        if type(self.difficulty) is not int or not 1 <= self.difficulty <= 6:
            raise ValueError("Difficulty must be an integer between 1 and 6.")
        if to_units(self.mining_reward) <= 0 or to_units(self.max_supply) <= 0:
            raise ValueError("Mining reward and maximum supply must be positive.")
        if not 0 <= to_units(self.burned_supply) <= to_units(self.max_supply):
            raise ValueError("Burned supply is outside the permitted range.")

    def _consensus_rules(self) -> dict:
        return {
            "protocol": "oilcoin-signed-v1", "difficulty": self.difficulty,
            "mining_reward_units": to_units(self.mining_reward),
            "max_supply_units": to_units(self.max_supply), "atomic_units": ATOMIC_UNITS,
        }

    @property
    def network_id(self) -> str:
        return self.chain[0].hash

    def create_genesis_block(self) -> Block:
        timestamp = _now_iso()
        return Block(
            index=0, timestamp=timestamp, previous_hash="0", nonce=0,
            transactions=[{
                "tx_id": "GENESIS_TX", "sender": "SYSTEM", "recipient": "GENESIS",
                "amount": 0.0, "fee": 0.0, "burn": 0.0, "timestamp": timestamp,
                "type": "genesis", "consensus": self._consensus_rules(),
                "network_nonce": uuid.uuid4().hex,
            }],
        )

    def _make_transaction(
        self, sender: str, recipient: str, amount: float, fee: float,
        burn: float, tx_type: str,
    ) -> dict:
        return {
            "tx_id": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            "sender": sender, "recipient": recipient,
            "amount": from_units(to_units(amount)), "fee": from_units(to_units(fee)),
            "burn": from_units(to_units(burn)), "timestamp": _now_iso(), "type": tx_type,
        }

    def get_latest_block(self) -> Block:
        return self.chain[-1]

    def _minted_units(self) -> int:
        return sum(to_units(tx["amount"]) for block in self.chain for tx in block.transactions
                   if tx.get("type") == "mining_reward")

    def calculate_total_supply(self) -> float:
        return from_units(self._minted_units())

    def calculate_circulating_supply(self) -> float:
        return from_units(self._minted_units() - to_units(self.burned_supply))

    def _confirmed_state(self) -> tuple[dict, dict]:
        balances: dict[str, int] = defaultdict(int)
        sequences: dict[str, int] = defaultdict(int)
        for block in self.chain:
            for tx in block.transactions:
                if tx.get("type") == "transfer":
                    balances[tx["sender"]] -= to_units(tx["amount"]) + to_units(tx["fee"])
                    balances[tx["recipient"]] += to_units(tx["amount"])
                    sequences[tx["sender"]] = tx["sequence"]
                elif tx.get("type") in {"mining_reward", "miner_fee"}:
                    balances[tx["recipient"]] += to_units(tx["amount"])
        return balances, sequences

    def get_balance(self, address: str) -> float:
        balance = 0
        for block in self.chain:
            for tx in block.transactions:
                if tx.get("type") == "transfer":
                    if tx.get("sender") == address:
                        balance -= to_units(tx["amount"]) + to_units(tx["fee"])
                    if tx.get("recipient") == address:
                        balance += to_units(tx["amount"])
                elif tx.get("type") in {"mining_reward", "miner_fee"} and tx.get("recipient") == address:
                    balance += to_units(tx["amount"])
        return from_units(balance)

    def _pending_reserved_units(self, address: str) -> int:
        return sum(to_units(tx["amount"]) + to_units(tx["fee"])
                   for tx in self.pending_transactions
                   if tx.get("type") == "transfer" and tx.get("sender") == address)

    def get_reserved_pending(self, address: str) -> float:
        return from_units(self._pending_reserved_units(address))

    def get_next_sequence(self, address: str) -> int:
        _, sequences = self._confirmed_state()
        return sequences[address] + sum(
            1 for tx in self.pending_transactions if tx.get("sender") == address
        ) + 1

    def _validate_transfer(self, tx: dict) -> tuple[bool, str]:
        if not isinstance(tx, dict) or tx.get("type") != "transfer":
            return False, "Pending transaction type must be transfer."
        sender, recipient = tx.get("sender"), tx.get("recipient")
        if not is_valid_address(sender) or not is_valid_address(recipient):
            return False, "Transfer address format is invalid."
        if sender == recipient:
            return False, "Transfer sender and recipient cannot be equal."
        try:
            amount, fee, burn = (to_units(tx.get(k)) for k in ("amount", "fee", "burn"))
        except ValueError as exc:
            return False, str(exc)
        if amount <= 0 or fee < 0 or burn < 0:
            return False, "Transfer amount must be positive and fee/burn non-negative."
        if burn != fee // 2:
            return False, "Transfer burn must be half the fee, rounded down to an atomic unit."
        if type(tx.get("sequence")) is not int or tx["sequence"] < 1:
            return False, "Transfer sequence must be a positive integer."
        if tx.get("network_id") != self.network_id:
            return False, "Transfer belongs to a different network."
        if not _valid_timestamp(tx.get("timestamp")):
            return False, "Transfer timestamp must contain a timezone."
        if not verify_transaction_signature(tx):
            return False, "Transfer signature or transaction ID is invalid."
        return True, "OK"

    _validate_pending_transfer = _validate_transfer

    def validate_pending_transactions(self) -> tuple[bool, str]:
        if not isinstance(self.pending_transactions, list):
            return False, "Pending pool must be a list."
        try:
            balances, sequences = self._confirmed_state()
            seen = {tx["tx_id"] for block in self.chain for tx in block.transactions}
            # Incoming pending funds cannot finance a new transfer.
            reserved: dict[str, int] = defaultdict(int)
            for tx in self.pending_transactions:
                ok, error = self._validate_transfer(tx)
                if not ok:
                    return False, error
                sender = tx["sender"]
                if tx["tx_id"] in seen:
                    return False, "Duplicate or replayed transaction ID in pending pool."
                if tx["sequence"] != sequences[sender] + 1:
                    return False, "Pending transfer sequence is invalid (replay or missing transfer)."
                spend = to_units(tx["amount"]) + to_units(tx["fee"])
                reserved[sender] += spend
                if reserved[sender] > balances[sender]:
                    return False, "Insufficient confirmed balance for pending transfers."
                sequences[sender] += 1
                seen.add(tx["tx_id"])
            return True, "Pending pool is valid."
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            return False, f"Malformed pending pool: {exc}"

    def add_transaction(
        self, sender: str, recipient: str, amount: float, fee: float,
        private_key: Optional[str] = None,
    ) -> tuple[bool, str]:
        sender = sender.strip() if isinstance(sender, str) else ""
        recipient = recipient.strip() if isinstance(recipient, str) else ""
        if not is_valid_address(sender) or not is_valid_address(recipient):
            return False, "Sender or recipient address format is invalid."
        if sender == recipient:
            return False, "Sender and recipient cannot be the same."
        try:
            amount_units, fee_units = to_units(amount), to_units(fee)
        except ValueError as exc:
            return False, str(exc)
        if amount_units <= 0 or fee_units < 0:
            return False, "Amount must be positive and fee cannot be negative."
        if not private_key:
            return False, "The sender's private key is required to sign the transfer."
        try:
            public_key = public_key_from_private(private_key)
            if address_from_public_key(public_key) != sender:
                return False, "Private key does not belong to the sender."
        except (ValueError, TypeError):
            return False, "Private key is invalid."
        ok, error = self.is_chain_valid()
        if not ok:
            return False, f"Cannot add to invalid chain: {error}"
        ok, error = self.validate_pending_transactions()
        if not ok:
            return False, f"Cannot add to invalid pending pool: {error}"
        available = to_units(self.get_balance(sender)) - self._pending_reserved_units(sender)
        if amount_units + fee_units > available:
            return False, "Insufficient available balance (confirmed balance minus pending reserves)."
        tx = self._make_transaction(
            sender, recipient, from_units(amount_units), from_units(fee_units),
            from_units(fee_units // 2), "transfer",
        )
        tx.update({"version": 1, "network_id": self.network_id,
                   "sequence": self.get_next_sequence(sender), "public_key": public_key})
        tx["signature"] = sign_message(private_key, _transfer_payload(tx))
        tx["tx_id"] = _transfer_id(tx)
        self.pending_transactions.append(tx)
        return True, "Signed transaction added to pending pool."

    def mine_pending_transactions(self, miner_address: str) -> tuple[bool, str, Optional[Block]]:
        ok, error = self.is_chain_valid()
        if not ok:
            return False, f"Cannot mine on invalid chain: {error}", None
        miner_address = miner_address.strip() if isinstance(miner_address, str) else ""
        if not is_valid_address(miner_address):
            return False, "Miner address format is invalid.", None
        ok, error = self.validate_pending_transactions()
        if not ok:
            return False, f"Pending transaction is invalid: {error}", None
        total_burn = sum(to_units(tx["burn"]) for tx in self.pending_transactions)
        total_miner_fee = sum(to_units(tx["fee"]) - to_units(tx["burn"])
                              for tx in self.pending_transactions)
        reward = min(to_units(self.mining_reward), to_units(self.max_supply) - self._minted_units())
        transactions = copy.deepcopy(self.pending_transactions)
        for tx_type, amount in (("mining_reward", reward), ("miner_fee", total_miner_fee)):
            if amount:
                transactions.append(self._make_transaction(
                    "SYSTEM", miner_address, from_units(amount), 0.0, 0.0, tx_type))
        new_block = Block(index=len(self.chain), timestamp=_now_iso(),
                          transactions=transactions, previous_hash=self.get_latest_block().hash)
        new_block.mine_block(self.difficulty)
        self.chain.append(new_block)
        self.burned_supply = from_units(to_units(self.burned_supply) + total_burn)
        self.pending_transactions = []
        return True, "Block mined successfully.", new_block

    def is_chain_valid(self) -> tuple[bool, str]:
        try:
            return self._validate_chain()
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
            return False, f"Malformed blockchain data: {exc}"

    def _validate_chain(self) -> tuple[bool, str]:
        self._check_configuration()
        if not isinstance(self.chain, list) or not self.chain:
            return False, "Blockchain is empty. Genesis block is missing."
        balances: dict[str, int] = defaultdict(int)
        sequences: dict[str, int] = defaultdict(int)
        seen: set[str] = set()
        minted, expected_burned = 0, 0
        for i, block in enumerate(self.chain):
            if type(block.version) is not int or block.version not in {1, 2}:
                return False, f"Unsupported block version at block index {i}."
            if type(block.index) is not int or block.index != i:
                return False, f"Invalid block index: expected {i}."
            if type(block.nonce) is not int or block.nonce < 0:
                return False, f"Invalid nonce at block index {i}."
            if not _valid_timestamp(block.timestamp) or not isinstance(block.transactions, list):
                return False, f"Invalid block timestamp or transaction list at block index {i}."
            if block.hash != block.calculate_hash():
                return False, f"Invalid current hash at block index {i}: stored hash does not match calculated hash."
            if i == 0:
                if block.previous_hash != "0" or block.nonce != 0 or len(block.transactions) != 1:
                    return False, "Genesis block structure is invalid."
                if not isinstance(block.transactions[0], dict) or block.transactions[0].get("type") != "genesis":
                    return False, "Genesis block must contain exactly one genesis transaction."
            else:
                if block.previous_hash != self.chain[i - 1].hash:
                    return False, f"Invalid previous hash link at block index {i}."
                if not block.hash.startswith("0" * self.difficulty):
                    return False, f"Invalid proof-of-work at block index {i}."
            rewards, miner_fees = [], []
            block_fees, block_burned = 0, 0
            payouts_started = False
            for tx in block.transactions:
                if not isinstance(tx, dict) or tx.get("type") not in ALLOWED_TX_TYPES:
                    return False, f"Invalid transaction type at block index {i}."
                tx_id = tx.get("tx_id")
                if not isinstance(tx_id, str) or not tx_id or len(tx_id) > 128 or tx_id in seen:
                    return False, f"Invalid or duplicate transaction ID at block index {i}."
                seen.add(tx_id)
                amount, fee, burn = (to_units(tx.get(k)) for k in ("amount", "fee", "burn"))
                if min(amount, fee, burn) < 0 or not _valid_timestamp(tx.get("timestamp")):
                    return False, f"Invalid transaction values or timestamp at block index {i}."
                tx_type = tx["type"]
                if tx_type == "genesis":
                    if (i != 0 or tx_id != "GENESIS_TX" or tx.get("sender") != "SYSTEM"
                            or tx.get("recipient") != "GENESIS" or (amount, fee, burn) != (0, 0, 0)):
                        return False, "Genesis transaction is invalid or appears outside the genesis block."
                    if "consensus" in tx and tx["consensus"] != self._consensus_rules():
                        return False, "Consensus settings differ from the rules committed in genesis."
                elif tx_type == "transfer":
                    if payouts_started:
                        return False, f"Transfers must precede system payouts at block index {i}."
                    ok, error = self._validate_transfer(tx)
                    if not ok:
                        return False, f"Invalid transaction at block index {i}: {error}"
                    sender, recipient = tx["sender"], tx["recipient"]
                    if tx["sequence"] != sequences[sender] + 1:
                        return False, f"Invalid transfer sequence at block index {i} (replay or missing transfer)."
                    if balances[sender] < amount + fee:
                        return False, f"Insufficient sender balance at block index {i} (double-spend)."
                    balances[sender] -= amount + fee
                    balances[recipient] += amount
                    sequences[sender] += 1
                    block_fees += fee - burn
                    block_burned += burn
                else:
                    payouts_started = True
                    if (i == 0 or tx.get("sender") != "SYSTEM" or not is_valid_address(tx.get("recipient"))
                            or amount <= 0 or fee or burn):
                        return False, f"Invalid system payout at block index {i}."
                    (rewards if tx_type == "mining_reward" else miner_fees).append(tx)
            if i > 0:
                expected_reward = min(to_units(self.mining_reward), to_units(self.max_supply) - minted)
                if (len(rewards) != (1 if expected_reward else 0)
                        or sum(to_units(t["amount"]) for t in rewards) != expected_reward):
                    return False, f"Invalid mining reward at block index {i}: expected {from_units(expected_reward)} OIL."
                if (len(miner_fees) != (1 if block_fees else 0)
                        or sum(to_units(t["amount"]) for t in miner_fees) != block_fees):
                    return False, f"Invalid miner fee payout at block index {i}."
                if rewards and miner_fees and rewards[0]["recipient"] != miner_fees[0]["recipient"]:
                    return False, f"Mining reward and fee recipients differ at block index {i}."
                for tx in rewards + miner_fees:
                    balances[tx["recipient"]] += to_units(tx["amount"])
                minted += expected_reward
                expected_burned += block_burned
                if sum(balances.values()) != minted - expected_burned:
                    return False, f"Supply conservation failed at block index {i}."
        if expected_burned != to_units(self.burned_supply):
            return False, "Invalid supply: burned supply is inconsistent with confirmed transfers."
        return True, "Blockchain is valid."

    def to_dict(self) -> dict:
        return {
            "chain": [block.to_dict() for block in self.chain],
            "pending_transactions": copy.deepcopy(self.pending_transactions),
            "difficulty": self.difficulty, "mining_reward": self.mining_reward,
            "max_supply": self.max_supply, "burned_supply": self.burned_supply,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Blockchain":
        if not isinstance(data, dict):
            raise ValueError("Blockchain payload must be a dictionary.")
        chain_data, pending = data.get("chain"), data.get("pending_transactions", [])
        if not isinstance(chain_data, list) or not chain_data:
            raise ValueError("Blockchain chain data is missing or invalid.")
        if not isinstance(pending, list):
            raise ValueError("Pending pool must be a list.")
        for item in chain_data:
            if (not isinstance(item, dict) or not isinstance(item.get("hash"), str)
                    or len(item["hash"]) != 64
                    or any(c not in "0123456789abcdef" for c in item["hash"])):
                raise ValueError("Stored blocks must have an explicit SHA-256 hash.")
        return cls(
            difficulty=data.get("difficulty", 4), mining_reward=data.get("mining_reward", 50.0),
            max_supply=data.get("max_supply", 1_000_000.0), burned_supply=data.get("burned_supply", 0.0),
            chain=[Block.from_dict(item) for item in chain_data], pending_transactions=pending,
        )
