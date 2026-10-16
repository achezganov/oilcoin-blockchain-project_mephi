import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
import arena
import colony
import district
import engine
import layout
import npc
from conftest import post,register
from database import unit_of_work,connect,initialize
from storage import load_blockchain,save_blockchain
from scripts.backup import create_backup

@pytest.fixture
def clock(monkeypatch):
    value=[1_900_008_000]
    monkeypatch.setattr(district,'now_seconds',lambda:value[0])
    return value


def run(application, fn):
    with application.app_context(),unit_of_work(application.config['DATABASE_PATH']):return fn()


def world(application,address):return run(application,lambda:colony.view(address))


def funds(application,address,**resources):
    with connect(application.config['DATABASE_PATH']) as conn:
        for key,value in resources.items():
            assert key in ('crude','fuel','parts')
            conn.execute(f'UPDATE estates SET {key}=? WHERE address=?',(value,address))


def ca(client,action,**data):return post(client,'/colony/action/'+action,data)


def setup_factory(application,address, fuel=40,parts=40):
    def operation():
        state,levels=district._read(address,district.now_seconds());data=state['_colony']
        data['inventory']={'pipe':40,'workshop':1,'battery':1,'warehouse':1,'pump_station':1,'compressor':1,'fuel_station':1}
        data['fuel_pct']=fuel;data['parts_pct']=parts;data['size']=12
        district._save(state)
        # A connected pipe spine with dedicated refinery and workshop branches.
        objects=[dict(kind='hq',x=0,y=0),dict(kind='pump',x=0,y=3),dict(kind='depot',x=6,y=0),dict(kind='refinery',x=3,y=3),dict(kind='garage',x=6,y=6),dict(kind='workshop',x=3,y=6)]
        objects += [dict(kind='pipe',x=2,y=y) for y in range(3,8)]
        assert layout.save(address,json.dumps(objects),'0')[0]
        return objects
    return run(application,operation)


def test_independent_connected_lines_conserve_input_and_collect_once(client,application,clock):
    address=register(client);setup_factory(application,address)
    clock[0]+=1000
    info=world(application,address)
    assert info['network']['fuel'] and info['network']['parts']
    assert info['buffer']=={'crude':32,'fuel':24,'parts':46}
    assert info['profile']['parts_credit']==40
    before=client.get('/api/base').json['resources']
    ca(client,'collect');after=client.get('/api/base').json['resources']
    assert after=={'crude':before['crude']+32,'fuel':before['fuel']+24,'parts':before['parts']+46}
    ca(client,'collect');assert client.get('/api/base').json['resources']==after


def test_disconnected_shares_remain_crude_and_switch_is_not_retroactive(client,application,clock):
    address=register(client)
    ca(client,'allocation',fuel_pct='50',parts_pct='50')
    clock[0]+=200
    assert world(application,address)['buffer']=={'crude':32,'fuel':0,'parts':0}
    setup_factory(application,address,50,50)
    clock[0]+=1000
    info=world(application,address)
    assert info['buffer']['crude']==32 and info['buffer']['fuel']==30 and info['buffer']['parts']==58


def test_pipeline_projection_stable_under_polling_and_daily_quota(client,application,clock):
    address=register(client);setup_factory(application,address,100,0)
    with connect(application.config['DATABASE_PATH']) as conn:conn.execute("UPDATE estates SET ready_crude=0 WHERE address=?",(address,))
    for _ in range(200):
        clock[0]+=10
        run(application,lambda:colony.touch(address))
    info=world(application,address)
    assert info['buffer']['fuel']==120 and info['profile']['generated']==200
    for _ in range(6):
        clock[0]+=1000;ca(client,'collect')
        funds(application,address,crude=0,fuel=0)
    info=world(application,address)
    assert info['profile']['generated']<=600
    clock[0]=district.calendar(clock[0])['day_end']+100
    assert world(application,address)['flow']['remaining']>0


def test_shop_ownership_expansion_and_builder_limits(client,application,clock):
    address=register(client);funds(application,address,crude=5000,parts=5000,fuel=1000)
    ca(client,'buy',kind='workshop')
    assert world(application,address)['profile']['inventory']['workshop']==1
    before=client.get('/api/base').json['resources'];ca(client,'buy',kind='workshop')
    assert client.get('/api/base').json['resources']==before
    objects=client.get('/api/base').json['layout']['objects']+[dict(kind='battery',x=0,y=0)]
    response=post(client,'/base/action/layout',{'layout':json.dumps(objects),'revision':'0'},)
    assert client.get('/api/base').json['layout']['revision']==0
    for _ in range(5):ca(client,'builder')
    assert world(application,address)['profile']['builders']==5
    ca(client,'expand');assert world(application,address)['profile']['size']==8
    with connect(application.config['DATABASE_PATH']) as conn:conn.execute("UPDATE buildings SET level=3 WHERE address=? AND kind='hq'",(address,))
    ca(client,'expand');ca(client,'expand');ca(client,'expand')
    assert world(application,address)['profile']['size']==12


def test_worker_automatic_mining_rotation_no_duplicate_ticks_and_auto_upgrades(client,application,clock):
    address=register(client);other=application.test_client();address2=register(other)
    for a in (address,address2):run(application,lambda:colony.touch(a))
    with application.app_context():first=engine.step(application.config['DATABASE_PATH'])
    assert first['height']==1
    with application.app_context():assert engine.step(application.config['DATABASE_PATH'])['height']==1
    clock[0]+=30
    with application.app_context():assert engine.step(application.config['DATABASE_PATH'])['height']==2
    assert client.get('/api/base').json['oil']==50 and other.get('/api/base').json['oil']==50
    post(client,'/base/action/build',{'kind':'hq','target':'2'})
    clock[0]+=30
    with application.app_context():engine.step(application.config['DATABASE_PATH'])
    assert client.get('/api/base').json['build']['ready_at']==clock[0]+60
    clock[0]+=60
    with application.app_context():engine.step(application.config['DATABASE_PATH'])
    assert client.get('/api/base').json['levels']['hq']==2 and client.get('/api/base').json['build'] is None


def test_parallel_builders_complete_without_claim_buttons(client,application,clock):
    address=register(client);funds(application,address,crude=1000,parts=1000)
    ca(client,'builder')
    with connect(application.config['DATABASE_PATH']) as conn:conn.execute("UPDATE buildings SET level=2 WHERE address=? AND kind='hq'",(address,))
    post(client,'/mine/quick');post(client,'/mine/quick')
    post(client,'/base/action/build',{'kind':'pump','target':'2'});post(client,'/base/action/build',{'kind':'depot','target':'2'})
    assert len(world(application,address)['jobs'])==2
    # Restart must not recreate the removed v3 single-builder index.
    initialize(application.config['DATABASE_PATH'])
    assert len(world(application,address)['jobs'])==2
    post(client,'/base/action/build',{'kind':'garage','target':'2'});assert len(world(application,address)['jobs'])==2
    post(client,'/mine/quick');clock[0]+=30
    run(application,lambda:engine.finish_ready(address))
    assert client.get('/api/base').json['levels']['pump']==2 and client.get('/api/base').json['levels']['depot']==2


def test_chest_nonce_probability_boundaries_and_deck_validation(client,application,monkeypatch):
    address=register(client);funds(application,address,crude=2000,parts=2000,fuel=200)
    monkeypatch.setattr(arena.secrets,'randbelow',lambda n:99)
    post(client,'/mine/quick')
    import chests
    quote=run(application,lambda:chests.quotes(load_blockchain(application.config['DATABASE_PATH']),address)[0]['captain']['quote'])
    data={'action':'chest','kind':'captain','request_id':'same-chest-key','payment':'oil','quote':quote}
    post(client,'/cards',data);post(client,'/cards',data)
    post(client,'/mine/quick')
    profile=world(application,address)['profile'];assert profile['cards']['7']['copies']==4
    with connect(application.config['DATABASE_PATH']) as conn:assert conn.execute('SELECT COUNT(*) FROM chest_events').fetchone()[0]==1
    post(client,'/cards',{'action':'deck','card':['7']*6});assert world(application,address)['profile']['deck']==[0,1,2,3,4,5]
    post(client,'/cards',{'action':'deck','card':['0','1','2','3','4','7']});assert world(application,address)['profile']['deck']==[0,1,2,3,4,7]
    post(client,'/cards',{'action':'upgrade','card_id':'7'});assert world(application,address)['profile']['cards']['7']['level']==2


def test_avatar_tutorial_and_private_visit_do_not_leak_resources(client,application):
    address=register(client)
    ca(client,'avatar',avatar='card:7');ca(client,'tutorial',step='5')
    assert world(application,address)['profile']['avatar']=='card:7'
    ca(client,'avatar',avatar='javascript:alert(1)');assert world(application,address)['profile']['avatar']=='card:7'
    html=application.test_client().get('/base/'+address).text
    assert 'data-inventory=' not in html and 'data-stat-resource' not in html and 'data-editor-form' not in html
    assert 'profile.avatar' not in html


@pytest.fixture
def duel(application,clock):
    players=[application.test_client(),application.test_client()]
    addresses=[register(c,f'Player {i}') for i,c in enumerate(players)]
    first=post(players[0],'/arena/queue',{'mode':'private'}).json
    code=first['state']['waiting']['room']
    assert post(players[1],'/arena/queue',{'mode':'join','room':code}).json['ok']
    return players,addresses


def play(client,card,lane=0):
    match=client.get('/api/arena').json['match'];mine=match['players'][match['side']]
    return post(client,'/arena/play',{'match_id':match['id'],'card_id':str(card),'lane':str(lane),'revision':str(mine['hand_revision'])})


def test_pvp_two_humans_hidden_hand_replay_guard_and_energy(duel,application,clock):
    players,addresses=duel;match=players[0].get('/api/arena').json['match'];side=match['side']
    assert 'deck' not in match['players'][1-side] and 'elixir' not in match['players'][1-side]
    assert all(world(application,a)['energy']['current']==7 for a in addresses)
    response=play(players[0],0);assert response.json['ok']
    replay=post(players[0],'/arena/play',{'match_id':match['id'],'card_id':'1','lane':'0','revision':'0'})
    assert replay.status_code==409
    assert play(players[0],1).status_code==409 # only 3 elixir remains
    outsider=application.test_client();register(outsider,'Outsider')
    assert post(outsider,'/arena/play',{'match_id':match['id'],'card_id':'0','lane':'0','revision':'0'}).status_code==409
    clock[0]+=10
    run(application,lambda:arena.advance(match['id']))
    assert players[0].get('/api/arena').json['match']['units'][0]['x']>7
    assert play(players[0],1).json['ok']
    assert play(players[1],0).json['ok'];assert play(players[1],2).json['ok']
    clock[0]+=25;run(application,lambda:arena.advance(match['id']))
    result=post(players[1],'/arena/surrender',{'match_id':match['id']}).json['state']['match']
    assert result['status']=='finished' and result['winner']==side
    assert world(application,addresses[0])['profile']['trophy']==25
    before=players[0].get('/api/base').json['resources']
    run(application,lambda:arena.advance(match['id']))
    assert players[0].get('/api/base').json['resources']==before


def test_pvp_timeout_draw_reconnect_backup(duel,application,clock,tmp_path):
    players,addresses=duel;match=players[0].get('/api/arena').json['match']
    path=Path(application.config['DATABASE_PATH']);backup=tmp_path/'arena.db';create_backup(path,backup)
    clock[0]+=121
    run(application,lambda:arena.advance(match['id']))
    state=players[0].get('/api/arena').json['match']
    assert state['status']=='finished' and state['winner'] is None
    assert world(application,addresses[0])['profile']['trophy']==0
    from app import create_app
    restored_app=create_app({**dict(application.config),'DATABASE_PATH':str(backup)})
    with restored_app.app_context():engine.step(str(backup))
    with connect(backup) as conn:assert conn.execute('SELECT status FROM arena_matches').fetchone()[0]=='finished'


def test_queue_expiration_cancel_and_parallel_join(application,clock):
    clients=[application.test_client() for _ in range(3)]
    addresses=[register(c,str(i)) for i,c in enumerate(clients)]
    post(clients[0],'/arena/queue',{'mode':'join'})
    tokens=[c.get('/api/csrf').json['csrf_token'] for c in clients[1:]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair:pair[0].post('/arena/queue',data={'csrf_token':pair[1],'mode':'join'}),zip(clients[1:],tokens)))
    with connect(application.config['DATABASE_PATH']) as conn:
        assert conn.execute("SELECT COUNT(*) FROM arena_matches WHERE status='active'").fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM arena_queue').fetchone()[0]==1
    clock[0]+=46
    assert not clients[2].get('/api/arena').json['waiting']


def test_npc_is_finite_and_uses_real_signed_payment(client,application,clock):
    address=register(client)
    with connect(application.config['DATABASE_PATH']) as conn:conn.execute("UPDATE buildings SET level=2 WHERE address=? AND kind='hq'",(address,))
    post(client,'/mine/quick')
    run(application,npc.ensure)
    # Find an arrival with a sell offer; the merchant has no minted OIL.
    while True:
        clock[0]=((clock[0]//7200)+1)*7200+10
        quote=run(application,lambda:npc.offers(load_blockchain(),address))
        offer=next((o for o in quote['offers'] if o['direction']=='sell'),None)
        if offer:break
    post(client,'/market/npc',{'slot':str(quote['slot']),'offer':str(offer['id'])})
    assert len(client.get('/api/pending').json)==1
    post(client,'/market/npc',{'slot':str(quote['slot']),'offer':str(offer['id'])})
    assert len(client.get('/api/pending').json)==1
    post(client,'/mine/quick')
    assert run(application,lambda:load_blockchain().get_balance(npc.account()['address']))==float(offer['price'])


def test_manual_mining_and_batches_disabled_for_normal_app(client,application):
    register(client);application.config.update(ALLOW_MANUAL_MINING=False,ALLOW_LEGACY_PRODUCTION=False)
    assert post(client,'/mine/quick').status_code==403
    post(client,'/base/action/produce',{'recipe':'fuel','batches':'1'})
    assert client.get('/api/base').json['production'] is None


def test_waiting_public_players_match_after_range_expands(application,clock):
    first,second=application.test_client(),application.test_client()
    a,b=register(first),register(second)
    def trophies():
        data=colony.profile(b);data['trophy']=1000;colony.save_profile(b,data)
    run(application,trophies)
    post(first,'/arena/queue',{'mode':'join'});post(second,'/arena/queue',{'mode':'join'})
    assert first.get('/api/arena').json['match'] is None
    clock[0]+=31
    with application.app_context():engine.step(application.config['DATABASE_PATH'])
    assert first.get('/api/arena').json['match']['status']=='active'
    assert second.get('/api/arena').json['match']['id']==first.get('/api/arena').json['match']['id']


def test_production_midnight_and_polling_are_equivalent(application,clock):
    clock[0]=district.calendar(clock[0])['day_end']-17
    first,second=application.test_client(),application.test_client()
    a,b=register(first),register(second)
    for address in (a,b):setup_factory(application,address,40,40)
    for _ in range(20):
        clock[0]+=10;run(application,lambda:colony.touch(a))
    left,right=world(application,a),world(application,b)
    assert left['buffer']==right['buffer']
    assert left['profile']['generated']==right['profile']['generated']==19
    assert left['profile']['fuel_credit']==right['profile']['fuel_credit']
    assert left['profile']['parts_credit']==right['profile']['parts_credit']


def test_building_yield_bonuses_and_catalyst_preserve_caps(client,application,clock):
    address=register(client);setup_factory(application,address,40,40)
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute("UPDATE buildings SET level=5 WHERE address=? AND kind IN ('refinery','garage')",(address,))
    clock[0]+=1000
    info=world(application,address)
    assert info['buffer']=={'crude':32,'fuel':33,'parts':65}
    # Saving a layout settles earlier production with the old multiplier.
    before=info['buffer']
    objects=client.get('/api/base').json['layout']['objects']+[dict(kind='fuel_station',x=0,y=6)]
    assert run(application,lambda:layout.save(address,json.dumps(objects),'1'))[0]
    assert world(application,address)['buffer']==before
    clock[0]+=1000
    assert world(application,address)['buffer']['fuel']==75
    clock[0]+=8*3600
    info=world(application,address)
    assert info['buffer']['fuel']<=150 and info['buffer']['parts']<=500


def test_repeat_opponent_and_daily_reward_cap(duel,application,clock):
    players,addresses=duel;match=players[0].get('/api/arena').json['match']
    for player in players:
        assert play(player,0).json['ok'];assert play(player,2).json['ok']
    clock[0]+=20
    post(players[1],'/arena/surrender',{'match_id':match['id']})
    before=[world(application,a)['profile']['trophy'] for a in addresses]
    code=post(players[0],'/arena/queue',{'mode':'private'}).json['state']['waiting']['room']
    post(players[1],'/arena/queue',{'mode':'join','room':code})
    for player in players:play(player,0);play(player,2)
    clock[0]+=20;match=players[0].get('/api/arena').json['match']
    post(players[1],'/arena/surrender',{'match_id':match['id']})
    assert [world(application,a)['profile']['trophy'] for a in addresses]==before
    # Five distinct earlier opponents exhaust the same UTC day's quota.
    opponents=[register(application.test_client(),f'Prior {i}') for i in range(5)]
    with connect(application.config['DATABASE_PATH']) as conn:
        conn.execute('DELETE FROM arena_rewards')
        conn.executemany('INSERT INTO arena_rewards VALUES(?,?,?)',[(addresses[0],district.calendar(clock[0])['day'],opponent) for opponent in opponents])
    state={'tick':20,'players':[{'address':a,'played':2} for a in addresses],'winner':0,'rewards':{}}
    run(application,lambda:arena._reward({},state))
    assert 'Тренировочный' in state['rewards'][addresses[0]]
    assert world(application,addresses[0])['profile']['trophy']==before[0]


def test_simultaneous_combat_has_no_seat_order_advantage():
    from copy import deepcopy
    players=[dict(elixir=5,towers=[300,300]),dict(elixir=5,towers=[300,300])]
    units=[dict(id=1,side=0,card=0,lane=0,x=43,hp=80,max_hp=80,attack=12),dict(id=2,side=1,card=0,lane=0,x=57,hp=80,max_hp=80,attack=12)]
    state=dict(players=players,units=units,tick=0);reverse=deepcopy(state);reverse['units'].reverse()
    for _ in range(10):arena._step(state);arena._step(reverse)
    assert state['players']==reverse['players']
    assert sorted(state['units'],key=lambda u:u['id'])==sorted(reverse['units'],key=lambda u:u['id'])


def test_requeue_same_second_returns_new_active_match(duel,application,clock):
    players,_=duel;first=players[0].get('/api/arena').json['match']
    post(players[0],'/arena/surrender',{'match_id':first['id']})
    code=post(players[0],'/arena/queue',{'mode':'private'}).json['state']['waiting']['room']
    result=post(players[1],'/arena/queue',{'mode':'join','room':code}).json['state']['match']
    assert result['status']=='active' and result['id']!=first['id']


def test_schema_three_migration_keeps_chain_stock_and_pending_construction(client,application,clock):
    address=register(client);post(client,'/mine/quick')
    post(client,'/base/action/build',{'kind':'hq','target':'2'})
    chain_before=client.get('/api/chain').json
    before=client.get('/api/base').json
    path=application.config['DATABASE_PATH']
    with connect(path) as conn:
        for table in ('colony_profiles','runtime_state','chest_events','arena_queue','arena_matches','arena_rewards','npc_deals'):
            conn.execute(f'DROP TABLE {table}')
        conn.execute('DROP INDEX one_building_upgrade')
        conn.execute('CREATE UNIQUE INDEX one_builder ON construction_jobs(address) WHERE claimed_at IS NULL')
        conn.execute('UPDATE schema_version SET version=3')
    initialize(path)
    with connect(path) as conn:
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0]==5
        assert conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]==1
    assert client.get('/api/chain').json==chain_before
    after=client.get('/api/base').json
    assert after['resources']==before['resources'] and after['build']['tx_id']==before['build']['tx_id']
    with application.app_context():engine.step(path)
    clock[0]+=61
    with application.app_context():engine.step(path)
    assert client.get('/api/base').json['levels']['hq']==2
