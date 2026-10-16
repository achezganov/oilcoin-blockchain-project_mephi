from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import pytest

import district
from conftest import post, register
from database import connect, initialize, SCHEMA, unit_of_work
from scripts.backup import create_backup, verify_snapshot
from storage import load_blockchain


@pytest.fixture
def farm_clock(monkeypatch):
    clock = [1_900_000_000]
    monkeypatch.setattr(district, 'now_seconds', lambda: clock[0])
    return clock


def base(client):
    response = client.get('/api/base')
    assert response.status_code == 200
    return response.json


def action(client, name, **data):
    return post(client, '/base/action/' + name, data)


def test_starter_base_and_public_visits(client, application):
    address = register(client, '<script>bad</script>')
    state = base(client)
    assert state['resources'] == {'crude': 60, 'fuel': 20, 'parts': 120}
    assert state['power'] == 26 and state['rating'] == 170 and state['oil'] == 0
    for path in ['/base', '/rankings', '/rankings?mode=base', '/rankings?mode=xp', '/base/' + address]:
        response = client.get(path)
        assert response.status_code == 200
        assert '<script>bad</script>' not in response.get_data(as_text=True)
    visitor = application.test_client()
    page = visitor.get('/base/' + address).get_data(as_text=True)
    assert '/base/action/' not in page
    assert 'resource-bar' not in page and 'ЖУРНАЛ БАЗЫ' not in page
    assert 'data-crude-store' not in page and 'data-crude-ready' not in page and 'data-crude-budget' not in page
    assert visitor.get('/api/base').status_code == 302
    assert post(visitor, '/base/action/collect').status_code == 302
    assert visitor.post('/base/action/collect').status_code == 400
    assert client.get('/rankings?mode=unknown').status_code == 400
    assert client.get('/base/not-an-address').status_code == 404


def test_offline_production_capacity_and_repeated_collection(client, farm_clock):
    register(client)
    action(client, 'collect')
    assert base(client)['resources']['crude'] == 72
    action(client, 'collect')
    assert base(client)['resources']['crude'] == 72
    farm_clock[0] += 10 * 10_000
    assert base(client)['ready_crude'] == 300
    action(client, 'collect')
    assert base(client)['resources']['crude'] == 300
    assert base(client)['ready_crude'] == 72
    action(client, 'produce', recipe='fuel', batches='1')
    action(client, 'collect')
    assert base(client)['resources']['crude'] == 300
    assert base(client)['ready_crude'] == 52


def test_get_base_has_no_writes_and_old_accounts_can_start(client, application):
    address = register(client)
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('DELETE FROM buildings WHERE address=?', (address,))
        conn.execute('DELETE FROM estates WHERE address=?', (address,))
    assert base(client)['resources']['parts'] == 120
    with connect(application.config['DATABASE_PATH']) as conn:
        assert conn.execute('SELECT COUNT(*) FROM estates').fetchone()[0] == 0
    action(client, 'collect')
    assert base(client)['resources']['crude'] == 72


def test_production_deadlines_costs_and_no_double_claim(client, farm_clock):
    register(client)
    action(client, 'produce', recipe='fuel', batches='3')
    state = base(client); job = state['production']
    assert state['resources']['crude'] == 0 and job['output_amount'] == 36
    assert job['ready_at'] - job['started_at'] == 90
    action(client, 'produce', recipe='fuel', batches='1')
    assert base(client)['production']['id'] == job['id']
    action(client, 'claim-production', job_id=str(job['id']))
    assert base(client)['resources']['fuel'] == 20
    farm_clock[0] += 90
    action(client, 'claim-production', job_id=str(job['id']))
    assert base(client)['resources']['fuel'] == 56
    action(client, 'claim-production', job_id=str(job['id']))
    assert base(client)['resources']['fuel'] == 56
    assert client.get('/api/game').json['xp'] == 15


def test_invalid_recipe_batches_locked_recipe_and_input_ignored(client):
    register(client)
    for fields in [dict(recipe='parts', batches='1'), dict(recipe='fuel', batches='-3'), dict(recipe='fuel', batches='9999'), dict(recipe='other', batches='1')]:
        action(client, 'produce', **fields)
    assert base(client)['resources'] == {'crude': 60, 'fuel': 20, 'parts': 120}
    action(client, 'produce', recipe='fuel', batches='1', output_amount='999999', ready_at='0')
    assert base(client)['production']['output_amount'] == 12


def test_construction_requires_signed_confirmation_then_server_timer(client, farm_clock):
    address = register(client)
    action(client, 'build', kind='hq', target='2')
    assert base(client)['build'] is None and base(client)['resources']['parts'] == 120
    post(client, '/mine/quick')
    action(client, 'build', kind='pump', target='2')
    assert base(client)['build'] is None
    action(client, 'build', kind='hq', target='2')
    state = base(client); job = state['build']
    assert state['resources']['parts'] == 40 and state['oil'] == 10
    pending = client.get('/api/pending').json[0]
    assert pending['tx_id'] == job['tx_id'] and pending['signature'] and pending['sender'] == address
    assert pending['recipient'] == district.TREASURY_ADDRESS and pending['amount'] == 40
    assert job['ready_at'] is None
    farm_clock[0] += 500
    action(client, 'finish-build', job_id=str(job['id']))
    assert base(client)['levels']['hq'] == 1
    post(client, '/mine/quick')
    assert base(client)['build']['ready_at'] == farm_clock[0] + 60
    farm_clock[0] += 59
    action(client, 'finish-build', job_id=str(job['id']))
    assert base(client)['levels']['hq'] == 1
    farm_clock[0] += 1
    action(client, 'finish-build', job_id=str(job['id']))
    assert base(client)['levels']['hq'] == 2 and base(client)['build'] is None
    xp = client.get('/api/game').json['xp']
    action(client, 'finish-build', job_id=str(job['id']))
    action(client, 'build', kind='hq', target='2')
    assert client.get('/api/game').json['xp'] == xp and base(client)['build'] is None


def test_other_miner_confirms_build_and_builder_is_single(client, application, farm_clock):
    register(client); post(client, '/mine/quick')
    action(client, 'build', kind='hq', target='2')
    job = base(client)['build']
    action(client, 'build', kind='hq', target='2')
    assert len(client.get('/api/pending').json) == 1
    other = application.test_client(); register(other)
    post(other, '/mine/quick')
    assert base(client)['build']['ready_at'] == farm_clock[0] + 60
    farm_clock[0] += 61
    action(other, 'finish-build', job_id=str(job['id']))
    assert base(client)['levels']['hq'] == 1


def test_build_payment_failure_rolls_back_all_game_state(client, application, monkeypatch):
    import app as module
    register(client); post(client, '/mine/quick')
    def fail(*args):
        raise ValueError('Injected write failure')
    monkeypatch.setattr(module, 'save_blockchain', fail)
    assert action(client, 'build', kind='hq', target='2').status_code == 503
    assert base(client)['resources']['parts'] == 120
    assert base(client)['build'] is None
    assert client.get('/api/pending').json == []
    assert base(client)['oil'] == 50


def test_contract_once_per_utc_day_and_rollover(client, farm_clock):
    register(client); day = base(client)['period']['day']
    action(client, 'contract', contract='city', day=day)
    state = base(client)
    assert state['points'] == 30 and state['resources']['parts'] == 165 and state['resources']['fuel'] == 8
    action(client, 'contract', contract='city', day=day)
    assert base(client)['points'] == 30
    farm_clock[0] = state['period']['day_end']
    assert not next(contract for contract in base(client)['contracts'] if contract['id'] == 'city')['completed']
    action(client, 'contract', contract='city', day=day)
    assert base(client)['resources']['parts'] == 165
    assert client.get('/api/game').json['xp'] == 25


def test_expedition_choices_progress_rewards_and_weekly_cap(client, farm_clock):
    register(client)
    action(client, 'expedition', sector='2', approach='assault')
    action(client, 'expedition', sector='1', approach='careful')
    assert base(client)['expedition'] is None and base(client)['resources']['fuel'] == 20
    action(client, 'expedition', sector='1', approach='balanced')
    state = base(client); job = state['expedition']
    assert state['resources']['fuel'] == 10 and job['power'] == 36
    action(client, 'claim-expedition', job_id=str(job['id']))
    assert base(client)['cleared'] == 0
    farm_clock[0] += 40
    action(client, 'claim-expedition', job_id=str(job['id']))
    state = base(client)
    assert state['resources'] == {'crude': 90, 'parts': 160, 'fuel': 10}
    assert state['cleared'] == 1 and state['points'] == 40 and state['sectors'][1]['unlocked']
    action(client, 'claim-expedition', job_id=str(job['id']))
    assert base(client)['points'] == 40 and base(client)['resources']['parts'] == 160
    action(client, 'expedition', sector='1', approach='balanced')
    job = base(client)['expedition']; farm_clock[0] += 40
    action(client, 'claim-expedition', job_id=str(job['id']))
    assert base(client)['points'] == 40 and base(client)['resources']['parts'] == 155
    farm_clock[0] = base(client)['period']['week_end']
    assert base(client)['points'] == 0 and base(client)['cleared'] == 1
    assert not base(client)['sectors'][0]['league_claimed']


def test_expedition_claim_on_week_boundary_cannot_award_twice(client, application, farm_clock):
    address = register(client)
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('UPDATE estates SET fuel=100 WHERE address=?', (address,))
    farm_clock[0] = base(client)['period']['week_end'] - 10
    action(client, 'expedition', sector='1', approach='balanced')
    job = base(client)['expedition']; farm_clock[0] += 40
    action(client, 'claim-expedition', job_id=str(job['id']))
    assert base(client)['points'] == 40
    action(client, 'expedition', sector='1', approach='balanced')
    job = base(client)['expedition']; farm_clock[0] += 40
    action(client, 'claim-expedition', job_id=str(job['id']))
    assert base(client)['points'] == 40


def test_parallel_contract_requests_only_charge_and_reward_once(client, application):
    address = register(client)
    clients = [application.test_client(), application.test_client()]
    for item in clients:
        with item.session_transaction() as session:
            session['wallet_address'] = address
    tokens = [item.get('/api/csrf').json['csrf_token'] for item in clients]
    day = base(client)['period']['day']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: pair[0].post('/base/action/contract', data={'csrf_token': pair[1], 'day': day, 'contract': 'city'}), zip(clients, tokens)))
    assert all(result.status_code == 302 for result in results)
    assert base(client)['points'] == 30 and base(client)['resources']['parts'] == 165


def test_v1_migration_preserves_existing_state_and_rejects_unknown_schema(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute('INSERT INTO chain_state VALUES(1,?)', ('historical-state',))
    initialize(path)
    with connect(path) as conn:
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == 5
        assert conn.execute('SELECT payload FROM chain_state').fetchone()[0] == 'historical-state'
        assert conn.execute('SELECT COUNT(*) FROM estates').fetchone()[0] == 0
        conn.execute('UPDATE schema_version SET version=99')
    with pytest.raises(RuntimeError):
        initialize(path)


def test_restart_and_backup_keep_queues_timers_and_resources(client, application, tmp_path, farm_clock):
    address = register(client); post(client, '/mine/quick')
    action(client, 'build', kind='hq', target='2')
    post(client, '/mine/quick')
    action(client, 'produce', recipe='fuel', batches='1')
    action(client, 'expedition', sector='1', approach='balanced')
    state = base(client)
    destination = tmp_path / 'farm-backup.db'
    create_backup(Path(application.config['DATABASE_PATH']), destination); verify_snapshot(destination)
    from app import create_app
    restored = create_app({**dict(application.config), 'DATABASE_PATH': str(destination)}).test_client()
    with restored.session_transaction() as session:
        session['wallet_address'] = address
    assert base(restored) == state
    farm_clock[0] += 60
    action(restored, 'finish-build', job_id=str(state['build']['id']))
    action(restored, 'claim-production', job_id=str(state['production']['id']))
    action(restored, 'claim-expedition', job_id=str(state['expedition']['id']))
    assert base(restored)['levels']['hq'] == 2 and base(restored)['cleared'] == 1
    assert base(client)['levels']['hq'] == 1 and base(client)['cleared'] == 0


def test_rankings_exclude_demo_and_use_actual_scores(client, application):
    address = register(client, 'Leader')
    action(client, 'contract', contract='city', day=base(client)['period']['day'])
    other = application.test_client(); demo = register(other, 'Demo')
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('UPDATE accounts SET is_demo=1 WHERE address=?', (demo,))
    with unit_of_work(application.config['DATABASE_PATH'], write=False):
        board = district.rankings(address)
    assert board['total'] == 1 and board['players'][0]['address'] == address
    assert board['me']['rank'] == 1 and board['me']['points'] == 30


def test_all_buildings_and_campaign_are_reachable(client, application, farm_clock, monkeypatch):
    import app as module
    monkeypatch.setattr(module.time, 'time', lambda: farm_clock[0])
    address = register(client)
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('UPDATE estates SET parts=5000,fuel=200 WHERE address=?', (address,))
    for kind in district.BUILDINGS:
        for target in range(2, 6):
            while base(client)['oil'] < district.BUILDINGS[kind]['oil'] * (target - 1):
                farm_clock[0] += 61
                assert post(client, '/mine/quick').status_code == 302
            assert action(client, 'build', kind=kind, target=str(target)).status_code == 302
            farm_clock[0] += 61; post(client, '/mine/quick')
            job = base(client)['build']
            farm_clock[0] = job['ready_at']
            action(client, 'finish-build', job_id=str(job['id']))
            assert base(client)['levels'][kind] == target
    state = base(client)
    assert state['rating'] == 850 and state['resources']['parts'] == 2350
    assert state['capacity'] == {'crude': 1500, 'fuel': 750, 'parts': 2500}
    for _ in range(5):
        cost = client.get('/api/game').json['upgrade_cost']
        while base(client)['oil'] < cost:
            farm_clock[0] += 61; post(client, '/mine/quick')
        post(client, '/game/upgrade'); farm_clock[0] += 61; post(client, '/mine/quick')
    assert base(client)['power'] == 150
    for sector in range(1, 7):
        action(client, 'expedition', sector=str(sector), approach='assault')
        job = base(client)['expedition']; assert job
        farm_clock[0] = job['ready_at']
        action(client, 'claim-expedition', job_id=str(job['id']))
    assert base(client)['cleared'] == 6 and base(client)['points'] == 520
    action(client, 'build', kind='pump', target='6')
    assert base(client)['build'] is None
    assert client.get('/api/tokenomics').json['validation']['is_valid']


def test_new_week_allows_route_points_again_without_resetting_campaign(client, application, farm_clock):
    address = register(client)
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('UPDATE estates SET fuel=100 WHERE address=?', (address,))
    for iteration in range(2):
        action(client, 'expedition', sector='1', approach='balanced')
        job = base(client)['expedition']; farm_clock[0] += 40
        action(client, 'claim-expedition', job_id=str(job['id']))
        assert base(client)['points'] == 40 and base(client)['cleared'] == 1
        if iteration == 0:
            farm_clock[0] = base(client)['period']['week_end']
            assert base(client)['points'] == 0


def test_backup_rejects_missing_farm_schema(client, application, tmp_path):
    register(client)
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('DROP TABLE farm_events')
    with pytest.raises(ValueError, match='Farm schema tables'):
        create_backup(Path(application.config['DATABASE_PATH']), tmp_path / 'broken-farm.db')


def test_real_v1_wallet_chain_and_pending_survive_migration(client, application):
    address = register(client); post(client, '/mine/quick'); post(client, '/game/upgrade')
    pending = client.get('/api/pending').json
    balance = client.get(f'/api/balance/{address}').json
    with connect(application.config['DATABASE_PATH']) as conn:
        for table in ('construction_jobs', 'production_jobs', 'expedition_jobs', 'farm_events', 'buildings', 'estates'):
            conn.execute(f'DROP TABLE {table}')
        conn.execute('UPDATE schema_version SET version=1')
    from app import create_app
    upgraded = create_app(dict(application.config)).test_client()
    assert post(upgraded, '/login', {'address': address, 'password': 'student-pass-123'}).status_code == 302
    assert upgraded.get('/api/pending').json == pending
    assert upgraded.get(f'/api/balance/{address}').json == balance
    post(upgraded, '/mine/quick')
    assert upgraded.get('/api/game').json['rig_level'] == 1
    assert base(upgraded)['resources'] == {'crude': 60, 'fuel': 20, 'parts': 120}
    action(upgraded, 'collect')
    assert base(upgraded)['resources']['crude'] == 72
