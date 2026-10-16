"""Single-node worker steps. The DB lock makes duplicate workers idempotent."""
import json
from database import unit_of_work
from storage import load_blockchain, save_blockchain
from game import record_mining, rig_state
import district
import colony
import market
import arena
import npc
import campaign
import chests

BLOCK_INTERVAL=30


def runtime(key,default=None):
    with unit_of_work(write=False) as conn:
        row=conn.execute('SELECT payload FROM runtime_state WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(key,value):
    with unit_of_work() as conn:
        conn.execute('INSERT INTO runtime_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',(key,json.dumps(value)))


def finish_ready(address=None):
    now=district.now_seconds()
    with unit_of_work() as conn:
        condition=' AND address=?' if address else '';args=(now,address) if address else (now,)
        for row in conn.execute('SELECT id,address FROM construction_jobs WHERE claimed_at IS NULL AND ready_at<=?'+condition,args).fetchall():
            district.finish_build(row['address'],str(row['id']))
        # Legacy paid production is delivered once; new manual batches are retired.
        for row in conn.execute('SELECT id,address FROM production_jobs WHERE claimed_at IS NULL AND ready_at<=?'+condition,args).fetchall():
            district.claim_production(row['address'],str(row['id']))


def step(path=None):
    now=district.now_seconds()
    with unit_of_work(path) as conn:
        chain=load_blockchain(path)
        merchant=npc.ensure()
        addresses=[row[0] for row in conn.execute('SELECT address FROM accounts WHERE is_demo=0 ORDER BY created_at,address')]
        for address in addresses:
            # Projection is lazy; save only while the game is active or a queue needs work.
            # Offline gains remain reproducible when a player returns.
            data=colony.profile(address,now)
            if data['last_seen']>=now-120:
                district.ensure_base(address);state,_=district._read(address,now);district._save(state)
        finish_ready()
        for row in conn.execute("SELECT id FROM arena_matches WHERE status='active'").fetchall():arena.advance(row['id'],now)
        for row in conn.execute("SELECT id FROM pve_matches WHERE status='active'").fetchall():campaign.advance(row['id'],now)
        conn.execute('DELETE FROM arena_queue WHERE heartbeat<?',(now-45,))
        arena.match_waiting()
        eligible=[a for a in addresses if colony.profile(a,now)['last_seen']>=now-120]
        senders={tx['sender'] for tx in chain.pending_transactions}
        eligible=sorted(set(eligible)|{a for a in addresses if a in senders})
        if not eligible and chain.pending_transactions and addresses:eligible=[addresses[0]]
        last=runtime('last_block_at',now-BLOCK_INTERVAL)
        if eligible and now-last>=BLOCK_INTERVAL:
            previous=runtime('last_miner','')
            miner=next((a for a in eligible if a>previous),eligible[0])
            level,_=rig_state(chain,miner)
            ok,_,block=chain.mine_pending_transactions(miner)
            if ok:
                record_mining(chain,miner,block,level);district.confirm_construction(chain,now);market.settle(chain);chests.settle(chain)
                save_blockchain(chain,path);put('last_block_at',now);put('last_miner',miner)
        put('heartbeat',now)
        put('next_block_at',runtime('last_block_at',now)+BLOCK_INTERVAL)
        return dict(height=chain.get_latest_block().index,active=len(eligible),now=now)
