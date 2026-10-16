import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
import arena
import campaign
import card_builds
import chests
import colony
import district
import engine
from blockchain import ATOMIC_UNITS
from conftest import post,register
from database import connect,unit_of_work,initialize
from storage import load_blockchain,save_blockchain
from scripts.backup import create_backup,verify_snapshot

@pytest.fixture
def clock(monkeypatch):
    value=[1_900_008_000]
    monkeypatch.setattr(district,'now_seconds',lambda:value[0])
    return value

def run(app,fn):
    with app.app_context(),unit_of_work(app.config['DATABASE_PATH']):return fn()

def state(client):return client.get('/api/campaign').json

def play(client,card,lane=0):
    match=state(client)['match'];p=match['players'][0]
    return post(client,'/campaign/play',dict(match_id=match['id'],card_id=card,lane=lane,revision=p['hand_revision']))

def prepare_win(app,address,clock,mode='stage',stage=1):
    def setup():
        base,_=district._read(address,clock[0]);base['_colony']['energy']=15;district._save(base)
        assert campaign.start(address,mode,str(stage))[0]
        row=campaign.active(address);battle=json.loads(row['state'])
        battle['players'][1]['towers']=[0,0]
        with unit_of_work() as conn:conn.execute('UPDATE pve_matches SET state=? WHERE id=?',(json.dumps(battle),row['id']))
        return row['id']
    match_id=run(app,setup);clock[0]+=1
    return run(app,lambda:campaign.advance(match_id,clock[0]))

def quote(app,address,kind):
    return run(app,lambda:chests.quotes(load_blockchain(app.config['DATABASE_PATH']),address)[0][kind])


def test_campaign_server_commands_privacy_and_cross_mode_exclusion(client,application,clock):
    address=register(client)
    assert post(client,'/campaign/start',{'stage':'2'}).status_code==409
    assert post(client,'/campaign/start',{'mode':'daily'}).status_code==409
    assert post(client,'/campaign/start',{'stage':'1'}).json['ok']
    info=state(client);match=info['match']
    assert info['world']['energy']['current']==7
    assert not {'stats','deck','levels','elixir'} & match['players'][1].keys()
    assert post(client,'/arena/queue',{'mode':'join'}).status_code==409
    assert post(client,'/campaign/start',{'stage':'1'}).status_code==409
    fields=dict(match_id=match['id'],card_id=0,lane=0,revision=0)
    assert post(client,'/campaign/play',fields).json['ok']
    assert post(client,'/campaign/play',fields).status_code==409
    other=application.test_client();register(other)
    assert post(other,'/campaign/play',dict(fields,revision=1)).status_code==409
    clock[0]+=8
    run(application,lambda:campaign.advance(match['id']))
    after=state(client)['match']
    assert after['players'][1]['played']>=1 and after['tick']==8
    assert any(u['action']=='walk' for u in after['units'])
    assert post(client,'/campaign/surrender',{'match_id':match['id']}).json['ok']
    assert post(client,'/arena/queue',{'mode':'private'}).json['ok']
    assert post(client,'/campaign/start',{'stage':'1'}).status_code==409


def test_stage_rewards_once_daily_day_locked_and_avatar_unlock(client,application,clock):
    address=register(client)
    for stage in range(1,5):
        result=prepare_win(application,address,clock,stage=stage)
        assert result['winner']==0
    assert state(client)['daily']['unlocked'] and state(client)['completed']==4
    before=client.get('/api/base').json['resources']
    result=prepare_win(application,address,clock,stage=4)
    assert 'Тренировочная' in result['rewards'][address]
    assert client.get('/api/base').json['resources']==before
    first_day=district.calendar(clock[0])['day']
    boss=state(client)['daily']['id']
    prepare_win(application,address,clock,'daily')
    info=state(client);assert info['daily']['claimed']
    assert info['world']['profile']['boss_medals']==1
    assert 'boss:'+str(boss) in info['world']['avatars']
    post(client,'/colony/action/avatar',{'avatar':'boss:'+str(boss)})
    assert state(client)['world']['profile']['avatar']=='boss:'+str(boss)
    prepare_win(application,address,clock,'daily')
    assert state(client)['world']['profile']['boss_medals']==1
    # A fight started on the previous UTC day cannot claim the new day's reward.
    def start_old():
        base,_=district._read(address,clock[0]);base['_colony']['energy']=15;district._save(base)
        assert campaign.start(address,'daily',None)[0]
    run(application,start_old)
    row=run(application,lambda:campaign.active(address))
    battle=json.loads(row['state']);battle['players'][1]['towers']=[0,0]
    with connect(application.config['DATABASE_PATH']) as conn:conn.execute('UPDATE pve_matches SET state=? WHERE id=?',(json.dumps(battle),row['id']))
    clock[0]=district.calendar(clock[0])['day_end']+1
    run(application,lambda:campaign.advance(row['id']))
    assert not state(client)['daily']['claimed']
    prepare_win(application,address,clock,'daily')
    assert state(client)['world']['profile']['boss_medals']==2
    with connect(application.config['DATABASE_PATH']) as conn:
        assert conn.execute('SELECT COUNT(*) FROM pve_rewards WHERE address=?',(address,)).fetchone()[0]==6
        assert conn.execute('SELECT 1 FROM pve_rewards WHERE reward_key=?',('daily:'+first_day,)).fetchone()


@pytest.mark.parametrize('boss_id',[0,1,2])
def test_boss_powers_are_timed_and_do_not_resurrect(boss_id):
    state=dict(tick=19,pve=dict(spec=dict(boss=True),boss=campaign.BOSSES[boss_id]),
               players=[{},dict(towers=[0,100],tower_max=200)],units=[dict(id=1,side=0,x=60,hp=100),dict(id=2,side=0,x=20,hp=100)])
    before=copy.deepcopy(state);campaign._power(state);assert state==before
    state['tick']=20;campaign._power(state)
    assert state['boss_event']['name']==campaign.BOSSES[boss_id]['power']
    if boss_id==0:assert state['players'][1]['towers']==[0,118]
    elif boss_id==1:assert [u['hp'] for u in state['units']]==[86,100]
    else:assert all(u['slow_until']==24 for u in state['units'])


def test_parallel_pve_start_spends_energy_once(client,application,clock):
    address=register(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:run(application,lambda:campaign.start(address,'stage','1')),[0,1]))
    assert sum(ok for ok,_ in results)==1
    assert state(client)['world']['energy']['current']==7


def test_pve_timeout_restored_worker_and_no_passive_win(client,application,clock,tmp_path):
    address=register(client);post(client,'/campaign/start',{'stage':'1'})
    match=state(client)['match'];backup=tmp_path/'battle.db'
    create_backup(Path(application.config['DATABASE_PATH']),backup);initialize(backup)
    clock[0]+=120
    with application.app_context():engine.step(str(backup))
    with connect(backup) as conn:
        row=conn.execute('SELECT status,state FROM pve_matches WHERE id=?',(match['id'],)).fetchone()
        saved=json.loads(row['state']);assert row['status']=='finished' and saved['winner']!=0
        assert not conn.execute('SELECT 1 FROM pve_rewards').fetchone()
    assert state(client)['match']['status']=='active' # original file not touched


def test_builds_require_levels_and_are_snapshotted(client,application,clock):
    address=register(client)
    def promote():
        base,_=district._read(address,clock[0]);base['_colony']['cards']['0'].update(level=3,copies=4);district._save(base)
    assert not run(application,lambda:card_builds.choose(address,'0','1','0'))[0]
    run(application,promote)
    assert run(application,lambda:card_builds.choose(address,'0','1','0'))[0]
    post(client,'/campaign/start',{'stage':'1'})
    assert play(client,0).json['ok']
    hp=state(client)['match']['units'][0]['hp'];assert hp==156 # 96 + two 30 HP talents
    assert run(application,lambda:card_builds.choose(address,'0','0','0'))[0]
    saved=run(application,lambda:json.loads(campaign.active(address)['state']))
    assert saved['players'][0]['stats']['0']['hp']==156
    stats=card_builds.stats(arena.CARDS[0],run(application,lambda:colony.profile(address))['cards']['0'])
    assert stats['hp']==96 and stats['attack']==22


@pytest.mark.parametrize('card',arena.CARDS)
def test_every_build_changes_actual_deploy_stats(card):
    for branch in (0,1):
        stats=card_builds.stats(card,dict(level=3,branch=branch,leaf=0))
        stat=next(iter(card_builds.TREES[card['id']][branch]['stats']))
        baseline=card_builds.stats(card,dict(level=3))
        assert stats[stat]==baseline[stat]+2*card_builds.TREES[card['id']][branch]['stats'][stat]
        player=dict(stats={str(card['id']):stats},levels={str(card['id']):3},elixir=10,deck=[card['id']],played=0,hand_revision=0)
        battle=dict(players=[player],units=[],next_unit=1,tick=0)
        arena.deploy(battle,0,card['id'],0)
        assert battle['units'][0][stat]==stats[stat]


def test_oil_chest_block_delivery_nonce_and_tampering(client,application,clock,tmp_path):
    address=register(client);post(client,'/mine/quick');post(client,'/mine/quick')
    spec=quote(application,address,'admiral')
    fields=dict(action='chest',payment='oil',kind='admiral',quote=spec['quote'],request_id='oil-admiral-1')
    before=run(application,lambda:colony.profile(address))['cards']
    post(client,'/cards',dict(fields,quote=spec['quote']+'bad'))
    assert not client.get('/api/cards/orders').json['orders']
    post(client,'/cards',fields);post(client,'/cards',fields)
    assert len(client.get('/api/cards/orders').json['orders'])==1
    assert run(application,lambda:colony.profile(address))['cards']==before
    chain=run(application,lambda:load_blockchain(application.config['DATABASE_PATH']))
    assert len(chain.pending_transactions)==1
    assert chain.pending_transactions[0]['amount']==float(spec['price'])
    assert chain.pending_transactions[0]['fee']==float(spec['fee'])
    create_backup(Path(application.config['DATABASE_PATH']),tmp_path/'pending.db')
    post(client,'/mine/quick')
    assert client.get('/api/cards/orders').json['orders'][0]['status']=='opened'
    with connect(application.config['DATABASE_PATH']) as conn:
        rows=conn.execute('SELECT * FROM chest_events').fetchall();assert len(rows)==1
        cards=json.loads(rows[0]['payload']);assert len(cards)==8 and arena.CARDS[cards[0]]['rarity'] in ('epic','legendary')
    after=run(application,lambda:colony.profile(address))['cards']
    run(application,lambda:chests.settle(load_blockchain(application.config['DATABASE_PATH'])))
    assert run(application,lambda:colony.profile(address))['cards']==after
    create_backup(Path(application.config['DATABASE_PATH']),tmp_path/'opened.db')


def test_chest_premium_rejects_resources_and_quote_is_bound_to_owner_kind(client,application):
    address=register(client);post(client,'/mine/quick')
    spec=quote(application,address,'captain')
    for kind in ('captain','admiral'):
        post(client,'/cards',dict(action='chest',payment='resources',kind=kind,request_id='resources-'+kind))
    other=application.test_client();register(other);post(other,'/mine/quick')
    post(other,'/cards',dict(action='chest',payment='oil',kind='captain',request_id='stolen-quote',quote=spec['quote']))
    post(client,'/cards',dict(action='chest',payment='oil',kind='admiral',request_id='wrong-kind',quote=spec['quote']))
    with connect(application.config['DATABASE_PATH']) as conn:
        assert conn.execute('SELECT COUNT(*) FROM chest_events').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM chest_orders').fetchone()[0]==0


def test_money_index_global_and_capped(client,application):
    address=register(client)
    class Supply:
        def __init__(self,supply,treasury):self.supply=supply;self.treasury=treasury
        def calculate_circulating_supply(self):return self.supply
        def get_balance(self,address):return self.treasury
    assert chests.economy(Supply(1000,0))['basis_points']==10100
    assert chests.economy(Supply(1000,500))['basis_points']==10050
    assert chests.economy(Supply(1_000_000,0))['basis_points']==100000
    a,b=run(application,lambda:(chests.quotes(Supply(1000,0),address)[0],chests.quotes(Supply(1000,0),'other')[0]))
    assert a['admiral']['total']==b['admiral']['total']=='63.63'


def test_partial_collection_fills_exactly_all_resources_and_preserves_remainder(client,application,clock):
    address=register(client)
    def fill():
        base,levels=district._read(address,clock[0]);caps=district.capacities(levels['depot'],address)
        for key in caps:base[key]=caps[key]-3
        base['ready_crude']=11;base['_colony']['buffer_fuel']=17;base['_colony']['buffer_parts']=23;district._save(base)
        return caps
    caps=run(application,fill);post(client,'/colony/action/collect')
    info=run(application,lambda:colony.view(address))
    assert client.get('/api/base').json['resources']==caps
    assert info['buffer']==dict(crude=8,fuel=14,parts=20)
    assert all(i['percent']==100 for i in info['storage'].values())
    post(client,'/colony/action/collect');assert run(application,lambda:colony.view(address))['buffer']==info['buffer']
    with connect(application.config['DATABASE_PATH']) as conn:conn.execute('UPDATE estates SET crude=crude-5, fuel=fuel-5,parts=parts-5 WHERE address=?',(address,))
    post(client,'/colony/action/collect')
    assert client.get('/api/base').json['resources']==caps
    assert run(application,lambda:colony.view(address))['buffer']==dict(crude=3,fuel=9,parts=15)


def test_schema_four_upgrade_keeps_every_existing_row(client,application):
    address=register(client);post(client,'/mine/quick')
    path=application.config['DATABASE_PATH']
    with connect(path) as conn:
        for table in ('pve_rewards','pve_matches','chest_orders'):conn.execute('DROP TABLE '+table)
        conn.execute('UPDATE schema_version SET version=4')
        tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_version'")]
        before={t:[tuple(r) for r in conn.execute('SELECT * FROM '+t)] for t in tables}
    initialize(path);initialize(path)
    with connect(path) as conn:
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0]==5
        assert before=={t:[tuple(r) for r in conn.execute('SELECT * FROM '+t)] for t in tables}


def test_campaign_cards_pages_and_guest_privacy(client,application):
    address=register(client)
    for path in ('/cards','/campaign','/arena','/profile'):
        response=client.get(path);assert response.status_code==200
    guest=application.test_client().get('/base/'+address).text
    assert 'data-world=' not in guest and 'buffer_parts' not in guest
    assert client.post('/campaign/start',data={'stage':'1'}).status_code==400


def test_chest_quote_expiry_and_pending_price_stays_locked(client,application,monkeypatch):
    from itsdangerous.timed import TimestampSigner
    address=register(client);post(client,'/mine/quick')
    from itsdangerous import URLSafeTimedSerializer
    moment=[1000]
    class QuoteClock(TimestampSigner):
        def get_timestamp(self):return moment[0]
    monkeypatch.setattr(chests,'serializer',lambda:URLSafeTimedSerializer(application.config['SECRET_KEY'],salt='chest-quote-v1',signer=QuoteClock))
    spec=quote(application,address,'captain');moment[0]=1301
    post(client,'/cards',dict(action='chest',payment='oil',kind='captain',request_id='expired-quote',quote=spec['quote']))
    assert client.get('/api/cards/orders').json['orders']==[]
    fresh=quote(application,address,'captain')
    post(client,'/mine/quick') # changes the confirmed money index after viewing the price
    assert quote(application,address,'captain')['price']!=fresh['price']
    post(client,'/cards',dict(action='chest',payment='oil',kind='captain',request_id='locked-price',quote=fresh['quote']))
    chain=run(application,lambda:load_blockchain(application.config['DATABASE_PATH']))
    assert chain.pending_transactions[-1]['amount']==float(fresh['price'])


def test_backup_rejects_mismatched_chest_amount(client,application,tmp_path):
    address=register(client);post(client,'/mine/quick');spec=quote(application,address,'captain')
    post(client,'/cards',dict(action='chest',payment='oil',kind='captain',request_id='backup-order',quote=spec['quote']))
    path=tmp_path/'copy.db';create_backup(Path(application.config['DATABASE_PATH']),path)
    with connect(path) as conn:conn.execute('UPDATE chest_orders SET price_units=price_units+1')
    with pytest.raises(ValueError,match='Chest order'):verify_snapshot(path)


def test_collection_counts_escrow_and_does_not_block_other_resources(client,application,clock):
    address=register(client)
    def fill():
        base,_=district._read(address,clock[0]);base.update(crude=300,fuel=148,parts=499,ready_crude=20)
        base['_colony'].update(buffer_fuel=10,buffer_parts=10);district._save(base)
        with unit_of_work() as conn:
            conn.execute("UPDATE buildings SET level=2 WHERE address=? AND kind='hq'",(address,))
        import market
        assert market.create(address,'crude','50','1')[0]
    run(application,fill)
    post(client,'/colony/action/collect');info=run(application,lambda:colony.view(address))
    assert info['storage']['crude']['stored']==250 and info['storage']['crude']['reserved']==50
    assert info['storage']['crude']['percent']==100
    assert info['buffer']==dict(crude=20,fuel=8,parts=9)
    assert info['storage']['fuel']['stored']==150 and info['storage']['parts']['stored']==500


def test_talent_armor_healing_and_range_affect_common_engine():
    def unit(id,card,side,x,hp=None,**kwargs):
        stats=card_builds.stats(arena.CARDS[card],dict(level=3,branch=0,leaf=0));stats.update(kwargs)
        return dict(stats,id=id,card=card,side=side,lane=0,x=x,max_hp=stats['hp'],hp=hp if hp is not None else stats['hp'])
    def battle(units):return dict(tick=0,players=[dict(towers=[300,300],elixir=0),dict(towers=[300,300],elixir=0)],units=units)
    guard=unit(1,1,0,50);enemy=unit(2,0,1,52,attack=12)
    state=battle([guard,enemy]);old=guard['hp'];arena._step(state)
    assert guard['hp']==old-8 # mastery bastion subtracts 4 from this actual 12 damage strike
    medic=unit(3,3,0,40);ally=unit(4,1,0,42,hp=100)
    state=battle([medic,ally]);arena._step(state);assert ally['hp']==117 and medic['healing']
    sniper=unit(5,2,0,35);foe=unit(6,1,1,70)
    state=battle([sniper,foe]);old=foe['hp'];arena._step(state)
    assert sniper['action']=='attack' and foe['hp']==old-max(1,sniper['attack']-foe['armor'])


def test_chest_cards_and_block_roll_back_together_when_worker_save_fails(client,application,clock,monkeypatch):
    address=register(client);post(client,'/mine/quick');spec=quote(application,address,'captain')
    post(client,'/cards',dict(action='chest',payment='oil',kind='captain',request_id='atomic-purchase',quote=spec['quote']))
    before_cards=run(application,lambda:colony.profile(address))['cards']
    before_chain=run(application,lambda:load_blockchain(application.config['DATABASE_PATH']).to_dict())
    original=engine.save_blockchain
    def interrupted(*args,**kwargs):raise RuntimeError('simulated interrupted persistence')
    monkeypatch.setattr(engine,'save_blockchain',interrupted)
    with application.app_context(),pytest.raises(RuntimeError,match='interrupted'):
        engine.step(application.config['DATABASE_PATH'])
    assert run(application,lambda:colony.profile(address))['cards']==before_cards
    assert run(application,lambda:load_blockchain(application.config['DATABASE_PATH']).to_dict())==before_chain
    assert client.get('/api/cards/orders').json['orders'][0]['status']=='pending'
    monkeypatch.setattr(engine,'save_blockchain',original)
    with application.app_context():engine.step(application.config['DATABASE_PATH'])
    assert client.get('/api/cards/orders').json['orders'][0]['status']=='opened'
