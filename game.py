"""Off-chain game progression; upgrade payments remain on the public ledger."""
import hashlib
from datetime import datetime, timezone
from accounts import decrypt_private_key
from database import unit_of_work

TREASURY_ADDRESS = "OIL_" + hashlib.sha256(b"oilcoin-game-treasury-v1").hexdigest()[:32]
MISSIONS = [
    ("first_block", "Первый блок", "Добудьте блок с настоящим Proof of Work.", 50),
    ("first_transfer", "Первая подпись", "Отправьте перевод и дождитесь подтверждения блоком.", 75),
    ("first_upgrade", "Буровая в работе", "Оплатите улучшение и подтвердите платёж майнингом.", 100),
]

def _confirmed_ids(chain):
    return {tx["tx_id"] for block in chain.chain for tx in block.transactions}

def rig_state(chain, address):
    with unit_of_work(write=False) as conn:
        rows = list(conn.execute("SELECT level,tx_id FROM upgrades WHERE address=? ORDER BY level", (address,)))
    confirmed = _confirmed_ids(chain)
    return max([row[0] for row in rows if row[1] in confirmed] or [0]), any(row[1] not in confirmed for row in rows)

def award_event(address, key, xp):
    with unit_of_work() as conn:
        conn.execute("INSERT OR IGNORE INTO game_events(address,event_key,xp,created_at) VALUES(?,?,?,?)", (address, key, xp, datetime.now(timezone.utc).isoformat()))

def record_mining(chain, address, block, rig_level):
    award_event(address, "block:" + block.hash, 25 + 5 * rig_level)
    settle_missions(chain)

def mission_conditions(chain, address):
    txs = [tx for block in chain.chain for tx in block.transactions]
    with unit_of_work(write=False) as conn:
        mined = conn.execute("SELECT 1 FROM game_events WHERE address=? AND event_key LIKE 'block:%' LIMIT 1", (address,)).fetchone()
    return [bool(mined) or any(tx["type"] == "mining_reward" and tx["recipient"] == address for tx in txs),
            any(tx["type"] == "transfer" and tx["sender"] == address for tx in txs), rig_state(chain,address)[0] > 0]

def settle_missions(chain):
    with unit_of_work() as conn:
        addresses = [row[0] for row in conn.execute("SELECT address FROM accounts")]
    for address in addresses:
        for mission, completed in zip(MISSIONS, mission_conditions(chain, address)):
            if completed:
                award_event(address, "mission:" + address + ":" + mission[0], mission[3])

def get_game_state(chain, address):
    with unit_of_work(write=False) as conn:
        xp = conn.execute("SELECT COALESCE(SUM(xp),0) FROM game_events WHERE address=?", (address,)).fetchone()[0]
        claimed = {row[0] for row in conn.execute("SELECT event_key FROM game_events WHERE address=?", (address,))}
    rig, pending = rig_state(chain, address)
    level = 1 + xp // 100
    missions = [{"title": title, "description": description, "completed": "mission:"+address+":"+key in claimed,
        "progress": int("mission:"+address+":"+key in claimed), "target": 1, "reward_xp": reward} for key,title,description,reward in MISSIONS]
    return {"level": level, "xp": xp, "next_level_xp": level * 100, "xp_progress": xp % 100,
        "title": ["Стажёр", "Бурильщик", "Инженер", "Мастер", "Директор"][min(4, (level-1)//2)],
        "rig_level": rig, "upgrade_cost": 10 * 2**rig, "upgrade_pending": pending,
        "missions": missions, "treasury_address": TREASURY_ADDRESS}

def leaderboard(chain):
    with unit_of_work(write=False) as conn:
        rows = conn.execute("""SELECT a.label,a.address,COALESCE(SUM(g.xp),0) AS xp FROM accounts a
            LEFT JOIN game_events g ON g.address=a.address GROUP BY a.address ORDER BY xp DESC,a.created_at LIMIT 10""").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["level"] = 1 + item["xp"] // 100
        with unit_of_work(write=False) as conn:
            own_blocks = {row[0].removeprefix("block:") for row in conn.execute("SELECT event_key FROM game_events WHERE address=? AND event_key LIKE 'block:%'", (item["address"],))}
        own_blocks.update(block.hash for block in chain.chain if any(tx["type"] == "mining_reward" and tx["recipient"] == item["address"] for tx in block.transactions))
        item["blocks_mined"] = len(own_blocks)
        result.append(item)
    return result

def purchase_upgrade(chain, account):
    level, pending = rig_state(chain, account["address"])
    if pending:
        return False, "Сначала подтвердите предыдущую покупку майнингом блока."
    if level >= 5:
        return False, "Буровая уже улучшена до максимума."
    ok, message = chain.add_transaction(account["address"], TREASURY_ADDRESS, 10 * 2**level, 0, private_key=decrypt_private_key(account))
    if ok:
        with unit_of_work() as conn:
            conn.execute("INSERT INTO upgrades(address,level,tx_id) VALUES(?,?,?)", (account["address"],level+1,chain.pending_transactions[-1]["tx_id"]))
        return True, "Оплата улучшения в мемпуле. Добудьте блок для активации."
    return False, message
