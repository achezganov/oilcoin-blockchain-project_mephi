#!/usr/bin/env python3
"""Create and verify a consistent OilCoin SQLite backup without importing Flask."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain import Blockchain
from market import payment_matches


def verify_snapshot(path: Path) -> None:
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('SQLite foreign key check failed')
        if connection.execute('SELECT version FROM schema_version').fetchall() not in ([(1,)], [(2,)], [(3,)], [(4,)], [(5,)]):
            raise ValueError('Unsupported database schema version')
        if connection.execute('SELECT version FROM schema_version').fetchone()[0] >= 2:
            expected = {'estates', 'buildings', 'construction_jobs', 'production_jobs', 'expedition_jobs', 'farm_events'}
            present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not expected <= present:
                raise ValueError('Farm schema tables are missing')
            if connection.execute('SELECT version FROM schema_version').fetchone()[0] >= 3:
                if not {'base_layouts', 'extraction_days', 'market_orders'} <= present:
                    raise ValueError('Market schema tables are missing')
        if connection.execute('SELECT version FROM schema_version').fetchone()[0] >= 4:
            present={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'colony_profiles','runtime_state','chest_events','arena_queue','arena_matches','arena_rewards','npc_deals'} <= present:
                raise ValueError('Colony schema tables are missing')
        if connection.execute('SELECT version FROM schema_version').fetchone()[0] >= 5:
            if not {'pve_matches','pve_rewards','chest_orders'} <= present:
                raise ValueError('Campaign schema tables are missing')
        row = connection.execute('SELECT payload FROM chain_state WHERE id=1').fetchone()
        if row is None:
            raise ValueError('Chain state is missing')
        state = json.loads(row[0])
        if not isinstance(state, dict):
            raise ValueError('Chain metadata must be an object')
        state['chain'] = []
        for height, block_hash, payload in connection.execute('SELECT height,hash,payload FROM blocks ORDER BY height'):
            block = json.loads(payload)
            if not isinstance(block, dict) or block.get('index') != height or block.get('hash') != block_hash:
                raise ValueError('Block index does not match its payload')
            state['chain'].append(block)
        state['pending_transactions'] = []
        for tx_id, payload in connection.execute('SELECT tx_id,payload FROM pending_transactions ORDER BY position'):
            tx = json.loads(payload)
            if not isinstance(tx, dict) or tx.get('tx_id') != tx_id:
                raise ValueError('Mempool index does not match its payload')
            state['pending_transactions'].append(tx)
        chain = Blockchain.from_dict(state)
        for valid, message in (chain.is_chain_valid(), chain.validate_pending_transactions()):
            if not valid:
                raise ValueError(message)
        if connection.execute('SELECT version FROM schema_version').fetchone()[0] >= 3:
            confirmed = {tx['tx_id']:tx for block in chain.chain for tx in block.transactions}
            pending = {tx['tx_id']:tx for tx in chain.pending_transactions}
            connection.row_factory = sqlite3.Row
            for order in connection.execute("SELECT * FROM market_orders WHERE status IN ('pending','filled')"):
                pool = pending if order['status'] == 'pending' else confirmed
                if not payment_matches(order, pool.get(order['tx_id'])):
                    raise ValueError('Market reservation does not match its blockchain payment')
        if connection.execute('SELECT version FROM schema_version').fetchone()[0] >= 5:
            from chests import payment_matches as chest_payment_matches
            for order in connection.execute('SELECT * FROM chest_orders'):
                pool=pending if order['status']=='pending' else confirmed
                if not chest_payment_matches(order,pool.get(order['tx_id'])):
                    raise ValueError('Chest order does not match its blockchain payment')
                delivered=connection.execute('SELECT 1 FROM chest_events WHERE address=? AND request_id=?',(order['address'],order['request_id'])).fetchone()
                if bool(delivered)!=(order['status']=='opened'):
                    raise ValueError('Chest delivery does not match its order status')
    finally:
        connection.close()


def create_backup(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    if not source.is_file():
        raise ValueError(f'Database does not exist: {source}')
    if source == destination or destination.exists():
        raise ValueError('Destination must be a new file; existing backups are never overwritten')
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.oilcoin-backup-', suffix='.db', dir=destination.parent)
    os.close(descriptor)  # mkstemp creates mode 0600, including when umask is permissive.
    temporary_path = Path(temporary)
    try:
        src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30)
        dst = sqlite3.connect(temporary)
        try:
            src.backup(dst, pages=128, sleep=0.1)
        finally:
            dst.close()
            src.close()
        verify_snapshot(temporary_path)
        # Hard link is an atomic no-overwrite publish on the same filesystem.
        os.link(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='New backup filename (must not exist)')
    parser.add_argument('--database', type=Path, default=Path(os.environ.get('DATABASE_PATH', Path(__file__).resolve().parents[1] / 'data' / 'oilcoin.db')))
    args = parser.parse_args()
    try:
        create_backup(args.database, args.destination)
    except (ValueError, sqlite3.Error, OSError) as error:
        parser.exit(1, f'Backup failed: {error}\n')
    print(f'Backup created and chain verified: {args.destination}')
    print('Keep the matching SECRET_KEY from .env in a separate protected backup.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
