"""Boundary cases reproduced during the project audit."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from analytics import get_chart_data, get_tokenomics_summary
from blockchain import Blockchain
from conftest import post, register
from database import connect, unit_of_work
from explorer_utils import get_address_details
from scripts.backup import create_backup
from scripts.worker_health import is_healthy
from wallet import generate_wallet_keys


@pytest.mark.parametrize('query', ['²', '①', '⁰', '４', '١'])
def test_unicode_search_does_not_become_a_server_error(client, query):
    assert client.get('/api/explorer/search', query_string={'q': query}).status_code == 200
    assert client.get('/explorer/search', query_string={'q': query}).status_code == 200


def test_demo_generation_ignores_existing_npc_payments(application, client):
    import npc
    from accounts import load_wallet_accounts
    from demo import create_demo_data
    from storage import load_blockchain, save_blockchain

    with application.app_context(), unit_of_work(application.config['DATABASE_PATH']):
        merchant = npc.ensure()
        chain = load_blockchain()
        assert chain.mine_pending_transactions(merchant['address'])[0]
        wallets = load_wallet_accounts()
        ok, _, result = create_demo_data(chain, wallets)
        assert ok and not result['scenario_skipped']
        assert result['blocks_mined'] == 6
        assert len(result['demo_addresses']) == 5
        assert merchant['address'] not in result['demo_addresses'].values()
        save_blockchain(chain)


@pytest.mark.parametrize('duration', ['bad', {}, [], -1, True, float('inf'), float('nan'), 10**400])
def test_untrusted_mining_metadata_cannot_break_statistics(duration):
    chain = Blockchain(difficulty=1)
    assert chain.mine_pending_transactions(generate_wallet_keys()['address'])[0]
    chain.chain[-1].mining_duration_seconds = duration
    assert chain.is_chain_valid()[0]
    assert get_tokenomics_summary(chain)['average_block_mining_time'] is None
    assert get_chart_data(chain)['mining_time_by_block'][-1] == 0
    restored = Blockchain.from_dict(chain.to_dict())
    assert restored.chain[-1].mining_duration_seconds is None
    assert restored.is_chain_valid()[0]


def test_malformed_duration_does_not_break_http_pages(application, client):
    register(client)
    post(client, '/mine/quick')
    with connect(application.config['DATABASE_PATH']) as conn:
        payload = json.loads(conn.execute('SELECT payload FROM blocks WHERE height=1').fetchone()[0])
        payload['mining_duration_seconds'] = 'not-a-number'
        payload['mining_started_at'] = {'untrusted': True}
        conn.execute('UPDATE blocks SET payload=? WHERE height=1', (json.dumps(payload),))
    for route in ('/', '/tokenomics', '/api/tokenomics', '/block/1'):
        assert client.get(route).status_code == 200


def test_finite_mining_durations_do_not_overflow_the_average():
    chain = Blockchain(difficulty=1)
    for _ in range(2):
        assert chain.mine_pending_transactions(generate_wallet_keys()['address'])[0]
        chain.chain[-1].mining_duration_seconds = 1e308
    assert get_tokenomics_summary(chain)['average_block_mining_time'] == 1e308


def test_wallet_volume_keeps_atomic_precision_after_many_transfers():
    address = 'OIL_' + 'a'*32
    txs = [dict(type='transfer', sender=address, recipient='OIL_'+'b'*32,
                amount=1000.00000001, burn=0.00000001, timestamp='2026-01-01T00:00:00+00:00')
           for _ in range(1000)]
    chain = SimpleNamespace(chain=[SimpleNamespace(index=1, transactions=txs)], pending_transactions=[],
                            get_balance=lambda _: 0, get_reserved_pending=lambda _: 0)
    stats = get_address_details(chain, address)['stats']
    assert stats['total_sent'] == 1000000.00001
    assert stats['total_burned_by_wallet'] == 0.00001


@pytest.mark.parametrize('payload', ['[]', 'null', '"text"'])
def test_malformed_chain_metadata_fails_cleanly(application, client, tmp_path, payload):
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('UPDATE chain_state SET payload=?', (payload,))
    assert client.get('/health').status_code == 503
    assert client.get('/docs/blockchain').status_code == 200
    with pytest.raises(ValueError):
        create_backup(Path(application.config['DATABASE_PATH']), tmp_path/'bad.db')
    assert not (tmp_path/'bad.db').exists()


@pytest.mark.parametrize('heartbeat,expected', [(1000, True), (981, True), (980, False),
                                               (1001, False), ('1000', False), (True, False),
                                               (None, False), ({}, False)])
def test_worker_health_requires_recent_numeric_heartbeat(tmp_path, heartbeat, expected):
    path = tmp_path/'worker.db'
    with connect(path) as conn:
        conn.execute('CREATE TABLE runtime_state(key TEXT PRIMARY KEY,payload TEXT)')
        conn.execute('INSERT INTO runtime_state VALUES(?,?)', ('heartbeat', json.dumps(heartbeat)))
    assert is_healthy(path, now=1000) is expected


def legacy_migration():
    path = Path(__file__).resolve().parents[1]/'migrations/001_add_block_versions.py'
    spec = importlib.util.spec_from_file_location('legacy_block_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('payload', ['[]', 'null', '{"chain":[null]}'])
def test_legacy_migration_rejects_wrong_json_shape(tmp_path, payload):
    source = tmp_path/'blockchain.json'
    source.write_text(payload)
    assert not legacy_migration().migrate_blockchain_file(str(source))[0]
    assert source.read_text() == payload
    assert not list(tmp_path.glob('blockchain.backup.*'))


def test_legacy_migration_keeps_each_backup_and_original_on_write_failure(tmp_path, monkeypatch):
    module = legacy_migration()
    source = tmp_path/'blockchain.json'
    original = '{"chain":[{"index":0}]}'
    source.write_text(original)
    assert module.migrate_blockchain_file(str(source))[0]
    first = next(tmp_path.glob('blockchain.backup.*'))
    assert first.read_text() == original
    assert module.migrate_blockchain_file(str(source))[0]
    assert len(list(tmp_path.glob('blockchain.backup.*'))) == 2
    assert first.read_text() == original

    source.write_text(original)
    def fail_replace(*_):
        raise OSError('Injected replace failure')
    monkeypatch.setattr(module.os, 'replace', fail_replace)
    assert not module.migrate_blockchain_file(str(source))[0]
    assert source.read_text() == original
    assert not list(tmp_path.glob('.oilcoin-migration-*'))
