import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import district
import layout
import market
from blockchain import to_units
from conftest import post, register
from database import connect, unit_of_work, initialize
from storage import load_blockchain
from scripts.backup import create_backup


def base(client):
    return client.get('/api/base').json


def action(client, name, **data):
    return post(client, '/base/action/'+name, data)


@pytest.fixture
def clock(monkeypatch):
    clock = [1_900_000_000]
    monkeypatch.setattr(district, 'now_seconds', lambda: clock[0])
    return clock


@pytest.fixture
def traders(application):
    clients = [application.test_client() for _ in range(3)]
    addresses = [register(c, f'Trader {i}') for i, c in enumerate(clients)]
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute("UPDATE buildings SET level=2 WHERE kind='hq'")
    for client in clients:
        post(client, '/mine/quick')
    return clients, addresses


def rows(application):
    with connect(application.config['DATABASE_PATH']) as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM market_orders ORDER BY id')]


def sell(client, **data):
    return post(client, '/market/sell', dict(resource='parts', quantity='20', price='10', **data))


def test_layout_persists_charges_only_new_decor_and_guards_stale_tabs(client, application):
    address = register(client)
    original = base(client)['layout']
    objects = original['objects']
    objects[0]['x'] = 1
    objects += [dict(kind='road', x=0, y=7), dict(kind='tree', x=1, y=7)]
    action(client, 'layout', layout=json.dumps(objects), revision='0')
    saved = base(client)
    assert saved['layout']['objects'] == objects and saved['resources']['parts'] == 110
    action(client, 'layout', layout=json.dumps(objects), revision='0')
    assert base(client)['resources']['parts'] == 110 and base(client)['layout']['revision'] == 1
    objects[-1]['x'] = 2
    action(client, 'layout', layout=json.dumps(objects), revision='1')
    assert base(client)['resources']['parts'] == 110
    visitor = application.test_client()
    page = visitor.get('/base/'+address).text
    assert 'object-tree' in page and 'data-editor-form' not in page
    with unit_of_work(application.config['DATABASE_PATH'], write=False):
        assert layout.read(address)['objects'] == objects


@pytest.mark.parametrize('bad', [None, '{}', 'null', '[null]', '[true]', 'NaN', 'x'*8001])
def test_layout_rejects_malformed_data(client, bad):
    register(client)
    action(client, 'layout', layout=bad, revision='0')
    assert base(client)['layout']['revision'] == 0


@pytest.mark.parametrize('change', ['overlap', 'outside', 'missing', 'duplicate', 'boolean', 'unknown'])
def test_layout_rejects_invalid_footprints(client, change):
    register(client); objects = base(client)['layout']['objects']
    if change == 'overlap': objects[1].update(x=3, y=0)
    elif change == 'outside': objects[0]['x'] = 7
    elif change == 'missing': objects.pop()
    elif change == 'duplicate': objects.append(dict(kind='hq', x=0, y=0))
    elif change == 'boolean': objects[0]['x'] = True
    else: objects[0]['kind'] = '<script>'
    action(client, 'layout', layout=json.dumps(objects), revision='0')
    assert base(client)['layout']['revision'] == 0 and base(client)['resources']['parts'] == 120


def test_market_block_delivery_conserves_goods_and_burns_fee(traders, application, tmp_path):
    (seller, buyer, miner), addresses = traders
    before = [base(c)['oil'] for c in (seller, buyer, miner)]
    sell(seller)
    assert base(seller)['resources']['parts'] == 100
    post(buyer, '/market/buy', {'order_id': '1', 'price': '0', 'quantity': '99999', 'fee': '0'})
    order = rows(application)[0]
    assert order['status'] == 'pending' and order['buyer'] == addresses[1]
    assert order['fee_units'] == 50_000_000 and base(buyer)['oil'] == before[1]-10.5
    assert base(buyer)['resources']['parts'] == 120
    post(seller, '/market/cancel', {'order_id': '1'})
    assert rows(application)[0]['status'] == 'pending'
    snapshot = tmp_path/'pending-market.db'
    create_backup(Path(application.config['DATABASE_PATH']), snapshot)
    post(miner, '/mine/quick')
    assert rows(application)[0]['status'] == 'filled'
    assert base(seller)['oil'] == before[0]+10
    assert base(buyer)['resources']['parts'] == 140
    assert base(miner)['oil'] == before[2]+50.25
    with unit_of_work(application.config['DATABASE_PATH'], write=False):
        chain = load_blockchain()
        assert to_units(chain.burned_supply) == 25_000_000
        assert chain.is_chain_valid()[0]
        market.settle(chain)  # repeated callbacks must not redeliver
    assert base(buyer)['resources']['parts'] == 140
    assert b'10 OIL' in buyer.get('/market').data
    from app import create_app
    restored = create_app({**dict(application.config), 'DATABASE_PATH': str(snapshot)}).test_client()
    with restored.session_transaction() as session: session['wallet_address'] = addresses[2]
    post(restored, '/mine/quick')
    with connect(snapshot) as conn:
        assert conn.execute('SELECT parts FROM estates WHERE address=?', (addresses[1],)).fetchone()[0] == 140


def test_market_concurrent_buyers_only_one_payment(traders, application):
    clients, _ = traders; sell(clients[0])
    tokens = [c.get('/api/csrf').json['csrf_token'] for c in clients[1:]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: pair[0].post('/market/buy', data={'csrf_token': pair[1], 'order_id': '1'}), zip(clients[1:], tokens)))
    assert all(result.status_code == 302 for result in results)
    assert len(clients[0].get('/api/pending').json) == 1
    post(clients[0], '/mine/quick')
    assert sorted(base(c)['resources']['parts'] for c in clients[1:]) == [120, 140]


def test_market_rollback_payment_and_delivery_on_persist_failure(traders, application, monkeypatch):
    import app as module
    clients, _ = traders; sell(clients[0])
    original = module.save_blockchain
    def fail(*args, **kwargs): raise RuntimeError('disk full')
    monkeypatch.setattr(module, 'save_blockchain', fail)
    with pytest.raises(RuntimeError): post(clients[1], '/market/buy', {'order_id': '1'})
    assert rows(application)[0]['status'] == 'open'
    assert not clients[1].get('/api/pending').json
    monkeypatch.setattr(module, 'save_blockchain', original)
    post(clients[1], '/market/buy', {'order_id': '1'})
    monkeypatch.setattr(module, 'save_blockchain', fail)
    with pytest.raises(RuntimeError): post(clients[2], '/mine/quick')
    assert rows(application)[0]['status'] == 'pending' and base(clients[1])['resources']['parts'] == 120
    monkeypatch.setattr(module, 'save_blockchain', original)
    post(clients[2], '/mine/quick')
    assert base(clients[1])['resources']['parts'] == 140


def test_market_expiry_cancel_ownership_and_capacity(traders, application, clock):
    (seller, buyer, other), addresses = traders
    sell(seller)
    post(seller, '/market/buy', {'order_id': '1'})
    post(other, '/market/cancel', {'order_id': '1'})
    assert rows(application)[0]['status'] == 'open'
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('UPDATE estates SET parts=490 WHERE address=?', (addresses[1],))
    post(buyer, '/market/buy', {'order_id': '1'})
    assert rows(application)[0]['status'] == 'open'
    clock[0] += 86400
    post(other, '/market/buy', {'order_id': '1'})
    assert rows(application)[0]['status'] == 'open'
    for _ in range(2): post(seller, '/market/cancel', {'order_id': '1'})
    assert base(seller)['resources']['parts'] == 120
    assert rows(application)[0]['status'] == 'cancelled'


@pytest.mark.parametrize('price', ['NaN','inf','0','-2','0.0001','10001','1.000000001'])
def test_market_rejects_invalid_prices(traders, application, price):
    post(traders[0][0], '/market/sell', {'resource':'parts','quantity':'20','price':price})
    assert rows(application) == []


def test_escrow_cannot_be_used_as_extra_crude_storage(traders, application):
    (seller, buyer, _), addresses = traders
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('UPDATE estates SET crude=300,ready_crude=300 WHERE address=?', (addresses[0],))
    post(seller, '/market/sell', {'resource':'crude','quantity':'100','price':'0.01'})
    action(seller, 'collect')
    assert base(seller)['resources']['crude'] == 200 and base(seller)['collectable'] == 0
    post(buyer, '/market/buy', {'order_id':'1'})
    assert rows(application)[0]['fee_units'] == 1_000_000
    action(seller, 'collect')
    assert base(seller)['resources']['crude'] == 200
    post(buyer, '/mine/quick')
    action(seller, 'collect')
    assert base(seller)['resources']['crude'] == 300


def test_market_unlock_limits_and_csrf(client, traders, application):
    register(client)
    sell(client)
    assert rows(application) == []
    seller = traders[0][0]
    assert seller.post('/market/sell').status_code == 400
    assert application.test_client().get('/market').status_code == 302
    for _ in range(6): sell(seller)
    assert len(rows(application)) == 5
    assert client.get('/market?resource=invalid').status_code == 400
    assert client.get('/market?page=invalid').status_code == 400


def test_daily_extraction_quota_and_reset(client, application, clock):
    address = register(client)
    for _ in range(3):
        with connect(application.config['DATABASE_PATH']) as conn:
            conn.execute('UPDATE estates SET crude=0,ready_crude=300 WHERE address=?', (address,))
        action(client, 'collect')
    assert base(client)['resources']['crude'] == 0 and base(client)['budget']['remaining'] == 0
    clock[0] = base(client)['period']['day_end']
    action(client, 'collect')
    assert base(client)['resources']['crude'] == 300 and base(client)['budget']['remaining'] == 300


def test_repeated_routes_have_bounded_rewards_and_recurring_cost(client, application, clock):
    address = register(client)
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('UPDATE estates SET fuel=100 WHERE address=?', (address,))
    for iteration in range(3):
        if iteration == 1: clock[0] = base(client)['period']['day_end']
        before = base(client)['resources']
        action(client, 'expedition', sector='1', approach='balanced')
        job = base(client)['expedition']
        assert job['reward_parts'] == [45, 11, 0][iteration]
        assert job['reward_crude'] == (30 if iteration == 0 else 0)
        assert base(client)['resources']['parts'] == before['parts']-5
        clock[0] += 40
        action(client, 'claim-expedition', job_id=str(job['id']))
    assert base(client)['resources']['crude'] == 90


def test_v2_migration_preserves_resources_buildings_and_pending(traders, application):
    client = traders[0][0]; before = base(client)
    path = application.config['DATABASE_PATH']
    with connect(path) as conn:
        tables = ('accounts','estates','buildings','blocks','pending_transactions','game_events')
        snapshot = {table:[tuple(row) for row in conn.execute('SELECT * FROM '+table)] for table in tables}
        for table in ('base_layouts','extraction_days','market_orders'): conn.execute('DROP TABLE '+table)
        conn.execute('UPDATE schema_version SET version=2')
    initialize(path)
    with connect(path) as conn:
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == 5
        assert snapshot == {table:[tuple(row) for row in conn.execute('SELECT * FROM '+table)] for table in tables}
    assert base(client)['resources'] == before['resources']


def test_parallel_layout_saves_cannot_charge_twice(client, application):
    address = register(client)
    objects = base(client)['layout']['objects'] + [dict(kind='road', x=0, y=0)]
    clients = [application.test_client(), application.test_client()]
    for c in clients:
        with c.session_transaction() as session: session['wallet_address'] = address
    tokens = [c.get('/api/csrf').json['csrf_token'] for c in clients]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: pair[0].post('/base/action/layout', data={
            'csrf_token':pair[1], 'revision':'0', 'layout':json.dumps(objects)}), zip(clients,tokens)))
    assert base(client)['resources']['parts'] == 118 and base(client)['layout']['revision'] == 1


def test_paid_order_survives_expiry_and_backup_rejects_mismatched_payment(traders, application, clock, tmp_path):
    seller, buyer, miner = traders[0]; sell(seller)
    post(buyer, '/market/buy', {'order_id':'1'})
    clock[0] += 2*86400
    before = rows(application)
    assert buyer.get('/market').status_code == 200
    assert rows(application) == before  # GET does not expire or refund a signed order.
    post(miner, '/mine/quick')
    assert rows(application)[0]['status'] == 'filled' and base(buyer)['resources']['parts'] == 140
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('UPDATE market_orders SET price_units=price_units+1 WHERE id=1')
    with pytest.raises(ValueError, match='Market reservation'):
        create_backup(Path(application.config['DATABASE_PATH']), tmp_path/'corrupt-market.db')
