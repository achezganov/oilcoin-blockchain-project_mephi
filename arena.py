"""Two human players, authoritative one-second battle simulation and card economy."""
import json
import secrets
import uuid
from database import unit_of_work
import district
import colony
import card_builds
import chests

CARDS = [
 dict(id=0,name='Рин · Механик',rarity='common',role='Быстрый боец',cost=2,hp=80,attack=12,speed=4,range=9,age=27),
 dict(id=1,name='Вера · Страж',rarity='common',role='Держит линию',cost=4,hp=240,attack=10,speed=2,range=9,age=31),
 dict(id=2,name='Ника · Следопыт',rarity='common',role='Дальний урон',cost=3,hp=75,attack=19,speed=3,range=25,age=26),
 dict(id=3,name='Элла · Медик',rarity='rare',role='Лечит союзников рядом',cost=3,hp=95,attack=6,speed=3,range=20,age=28),
 dict(id=4,name='Лилит · Искра',rarity='epic',role='Урон по группе',cost=5,hp=105,attack=22,speed=3,range=20,age=29),
 dict(id=5,name='Ада · Канонир',rarity='rare',role='Осадный урон',cost=4,hp=120,attack=27,speed=2,range=18,age=32),
 dict(id=6,name='Ирис · Иней',rarity='epic',role='Замедляет противника',cost=4,hp=110,attack=15,speed=3,range=23,age=30),
 dict(id=7,name='Селена · Адмирал',rarity='legendary',role='Лидер штурма',cost=6,hp=200,attack=24,speed=3,range=15,age=34),
]
RARITIES={'common':'Обычная','rare':'Редкая','epic':'Эпическая','legendary':'Легендарная'}
CHESTS=chests.SPECS
DURATION=120


def draw(guaranteed=False):
    roll=secrets.randbelow(100)
    rarity=('rare' if roll<80 else 'epic' if roll<98 else 'legendary') if guaranteed else ('common' if roll<60 else 'rare' if roll<88 else 'epic' if roll<98 else 'legendary')
    return secrets.choice([card['id'] for card in CARDS if card['rarity']==rarity])


def add_card(data, card):
    key=str(card)
    if key in data['cards']:data['cards'][key]['copies']+=1
    else:data['cards'][key]={'level':1,'copies':0}


def open_chest(address,kind,request_id):
    if kind!='scout' or not chests.valid_request(request_id):return False,'Премиальные сундуки открываются только за OIL.'
    with unit_of_work() as conn:
        if chests.used(conn,address,request_id):return False,'Этот сундук уже заказан или открыт.'
        district.ensure_base(address);state,_=district._read(address,district.now_seconds());spec=CHESTS[kind]
        if not district._can_spend(state,spec['cost']):return False,'Не хватает ресурсов на сундук.'
        district._spend(state,spec['cost']);district._save(state)
        cards=chests.deliver(conn,address,kind,request_id)
    return True,'Сундук открыт: '+', '.join(CARDS[c]['name'] for c in cards)


def collection(address,chain=None):
    data=colony.profile(address)
    with unit_of_work(write=False) as conn:
        rows=conn.execute('SELECT * FROM chest_events WHERE address=? ORDER BY id DESC LIMIT 5',(address,)).fetchall()
        orders=conn.execute("SELECT * FROM chest_orders WHERE address=? ORDER BY id DESC LIMIT 10",(address,)).fetchall()
    specs,index=chests.quotes(chain,address) if chain else (CHESTS,{})
    return dict(cards=[dict(c,**{k:v for k,v in card_builds.stats(c,data['cards'].get(str(c['id']),{})).items()},
                           owned=str(c['id']) in data['cards'],progress=data['cards'].get(str(c['id']),{}),tree=card_builds.TREES[c['id']]) for c in CARDS],
                profile=data,chests=specs,economy=index,orders=[dict(r) for r in orders],rarities=RARITIES,request_id=uuid.uuid4().hex,
                recent=[dict(r,cards=json.loads(r['payload'])) for r in rows])


def deck_action(address,form):
    district.ensure_base(address);state,_=district._read(address,district.now_seconds());data=state['_colony']
    try:
        cards=[int(x) for x in form.getlist('card')]
        if len(cards)!=6 or len(set(cards))!=6 or any(str(c) not in data['cards'] for c in cards):raise ValueError
    except (TypeError,ValueError):return False,'Выберите ровно шесть разных открытых карт.'
    data['deck']=cards;district._save(state)
    return True,'Колода сохранена. В начавшемся бою действует колода на момент подбора.'


def upgrade(address,card_id):
    district.ensure_base(address);state,_=district._read(address,district.now_seconds());data=state['_colony']
    item=data['cards'].get(card_id)
    if not item or item['level']>=3:return False,'Карта недоступна или уже максимального уровня.'
    copies=2*item['level'];cost=50*item['level']
    if item['copies']<copies or state['parts']<cost:return False,f'Нужно {copies} дубликата и {cost} деталей.'
    item['copies']-=copies;item['level']+=1;state['parts']-=cost;district._save(state)
    return True,'Карта улучшена: +10% здоровья и урона от базовых значений за уровень.'


def active(address):
    with unit_of_work(write=False) as conn:
        row=conn.execute("SELECT * FROM arena_matches WHERE status='active' AND (p1=? OR p2=?) ORDER BY created_at DESC LIMIT 1",(address,address)).fetchone()
    return dict(row) if row else None


def _new_player(address,now):
    district.ensure_base(address,now);state,_=district._read(address,now);data=state['_colony']
    data['energy']-=3;district._save(state)
    return dict(address=address,deck=list(data['deck']),levels={k:v['level'] for k,v in data['cards'].items()},
                stats={k:card_builds.stats(CARDS[int(k)],v) for k,v in data['cards'].items()},
                elixir=5,hand_revision=0,played=0,towers=[300,300],tower_max=300,seen=now)


def queue(address,mode='join',room=''):
    now=district.now_seconds()
    if mode not in ('join','private','cancel'):return False,'Неизвестный режим поиска.'
    with unit_of_work() as conn:
        conn.execute('DELETE FROM arena_queue WHERE heartbeat<?',(now-45,))
        if active(address):return True,'Бой уже идёт.'
        import campaign
        if campaign.active(address):return False,'Сначала завершите бой кампании.'
        if mode=='cancel':conn.execute('DELETE FROM arena_queue WHERE address=?',(address,));return True,'Поиск остановлен. Энергия не списывалась.'
        district.ensure_base(address);state,_=district._read(address,now);data=state['_colony'];district._save(state)
        if data['energy']<3:return False,'Для боя нужно 3 энергии. Дождитесь восстановления.'
        own=conn.execute('SELECT * FROM arena_queue WHERE address=?',(address,)).fetchone()
        if own and (own['room'] or mode!='join'):return True,'Поиск уже идёт.'
        code=room.strip().upper() if isinstance(room,str) else ''
        if code and (len(code)!=6 or not code.isascii() or not code.isalnum()):return False,'Код комнаты: шесть латинских букв или цифр.'
        if mode=='private':
            for _ in range(5):
                code=secrets.token_hex(3).upper()
                if not conn.execute('SELECT 1 FROM arena_queue WHERE room=?',(code,)).fetchone():
                    conn.execute('INSERT INTO arena_queue VALUES(?,?,?,?)',(address,now,now,code))
                    return True,'Комната создана: '+code
            return False,'Не удалось создать свободный код. Попробуйте ещё раз.'
        candidates=conn.execute('SELECT * FROM arena_queue WHERE address!=? AND '+('room=?' if code else 'room IS NULL')+' ORDER BY joined_at',(address,code) if code else (address,)).fetchall()
        for candidate in candidates:
            other=candidate['address']
            if active(other) or campaign.active(other):continue
            other_state,_=district._read(other,now);other_data=other_state['_colony']
            if other_data['energy']<3:conn.execute('DELETE FROM arena_queue WHERE address=?',(other,));continue
            waiting_since=min(candidate['joined_at'],own['joined_at'] if own else now)
            if not code and abs(other_data['trophy']-data['trophy'])>200 and now-waiting_since<30:continue
            match_id=uuid.uuid4().hex
            players=[_new_player(other,now),_new_player(address,now)]
            game=dict(players=players,units=[],tick=0,next_unit=1,winner=None,reason='',rewards={},started=now,version=0)
            conn.execute("INSERT INTO arena_matches VALUES(?,?,?,?,'active',?,?)",(match_id,other,address,json.dumps(game),now,now))
            conn.execute('DELETE FROM arena_queue WHERE address IN (?,?)',(other,address))
            return True,'Соперник найден. Бой начался!'
        if code:return False,'Комната не найдена или соперник ушёл. Обновите код.'
        if not own:conn.execute('INSERT INTO arena_queue VALUES(?,?,?,NULL)',(address,now,now))
    return True,'Ищем живого соперника. Энергия спишется только при старте.'


def match_waiting():
    """Widen existing public searches without waiting for a third arrival."""
    now=district.now_seconds()
    with unit_of_work() as conn:
        candidates=conn.execute('SELECT address FROM arena_queue WHERE room IS NULL AND joined_at<=? AND heartbeat>=? ORDER BY joined_at',(now-30,now-45)).fetchall()
        for row in candidates:
            if conn.execute('SELECT 1 FROM arena_queue WHERE address=?',(row['address'],)).fetchone():
                queue(row['address'])


def heartbeat(address):
    now=district.now_seconds()
    with unit_of_work() as conn:
        conn.execute('UPDATE arena_queue SET heartbeat=? WHERE address=?',(now,address))
        row=active(address)
        if row:
            state=json.loads(row['state']);side=0 if row['p1']==address else 1
            state['players'][side]['seen']=now
            conn.execute('UPDATE arena_matches SET state=? WHERE id=?',(json.dumps(state),row['id']))


def deploy(state,side,card_id,lane):
    """Shared validated deployment for humans and the PvE commander."""
    player=state['players'][side];card=CARDS[card_id]
    stats=player.get('stats',{}).get(str(card_id)) or card_builds.stats(card,{'level':player['levels'][str(card_id)]})
    state['units'].append(dict(stats,id=state['next_unit'],card=card_id,side=side,lane=lane,x=7 if side==0 else 93,
                              max_hp=stats['hp'],action='spawn',action_tick=state['tick']))
    state['next_unit']+=1;player['elixir']-=card['cost'];player['played']+=1;player['hand_revision']+=1
    player['deck'].remove(card_id);player['deck'].append(card_id);player['seen']=district.now_seconds()


def _step(state):
    state['tick']+=1;tick=state['tick'];players=state['players'];units=state['units']
    state['events']=[]
    if tick%2==0 or tick>90:
        for player in players:player['elixir']=min(10,player['elixir']+1)
    moves={};damage={};tower_damage=[[0,0],[0,0]];heals={};slows={}
    def hit(victim,amount):damage[victim['id']]=damage.get(victim['id'],0)+max(1,amount-victim.get('armor',0))
    def event(kind,unit,target):
        state['events'].append(dict(kind=kind,side=unit['side'],lane=unit['lane'],x=unit['x'],target=target,tick=tick))
    for unit in units:
        card=CARDS[unit['card']];direction=1 if unit['side']==0 else -1;enemy=1-unit['side'];reach=unit.get('range',card['range'])
        unit['action']='idle';unit['action_tick']=tick;unit['healing']=False
        foes=[u for u in units if u['side']==enemy and u['lane']==unit['lane']]
        target=min(foes,key=lambda u:(abs(u['x']-unit['x']),u['id']),default=None)
        healing=unit.get('heal',7 if unit['card']==3 else 0)
        if healing:
            friends=[u for u in units if u['side']==unit['side'] and u['lane']==unit['lane'] and abs(u['x']-unit['x'])<=18 and u['hp']<u['max_hp']]
            patient=min(friends,key=lambda u:(u['hp']/u['max_hp'],u['id']),default=None)
            if patient:
                heals[patient['id']]=heals.get(patient['id'],0)+healing;unit['healing']=True;event('heal',unit,patient['x'])
        if target and abs(target['x']-unit['x'])<=reach:
            radius=unit.get('splash',8 if unit['card']==4 else 0)
            victims=[u for u in foes if abs(u['x']-target['x'])<=radius] if radius else [target]
            for victim in victims:hit(victim,unit['attack'])
            slow=unit.get('slow',2 if unit['card']==6 else 0)
            if slow:slows[target['id']]=max(slows.get(target['id'],0),slow)
            unit['action']='attack';event('frost' if slow else 'blast' if radius else 'attack',unit,target['x'])
        elif abs((100 if enemy==1 else 0)-unit['x'])<=reach and players[enemy]['towers'][unit['lane']]>0:
            tower_damage[enemy][unit['lane']]+=unit['attack']+unit.get('siege',8 if unit['card']==5 else 0)
            unit['action']='siege';event('attack',unit,100 if enemy==1 else 0)
        else:
            speed=unit.get('speed',card['speed']);speed=max(1,speed//2) if unit.get('slow_until',0)>=tick else speed
            moves[unit['id']]=max(1,min(99,unit['x']+direction*speed));unit['action']='walk'
    for side,player in enumerate(players):
        for lane in (0,1):
            if player['towers'][lane]>0:
                enemies=[u for u in units if u['side']!=side and u['lane']==lane and abs(u['x']-(0 if side==0 else 100))<=27]
                target=min(enemies,key=lambda u:(abs(u['x']-(0 if side==0 else 100)),u['id']),default=None)
                if target:
                    hit(target,10);event('tower',dict(side=side,lane=lane,x=0 if side==0 else 100),target['x'])
            player['towers'][lane]=max(0,player['towers'][lane]-tower_damage[side][lane])
    for unit in units:
        unit['x']=moves.get(unit['id'],unit['x'])
        unit['hp']=min(unit['max_hp'],unit['hp']+heals.get(unit['id'],0))-damage.get(unit['id'],0)
        if unit['id'] in slows:unit['slow_until']=max(unit.get('slow_until',0),tick+slows[unit['id']])
        if unit['hp']<=0:event('defeat',unit,unit['x'])
    state['units']=[u for u in units if u['hp']>0]


def _reward(row,state):
    now=district.now_seconds();day=district.calendar(now)['day']
    with unit_of_work() as conn:
        for side,player in enumerate(state['players']):
            address=player['address'];opponent=state['players'][1-side]['address']
            count=conn.execute('SELECT COUNT(*) FROM arena_rewards WHERE address=? AND day=?',(address,day)).fetchone()[0]
            used=conn.execute('SELECT 1 FROM arena_rewards WHERE address=? AND day=? AND opponent=?',(address,day,opponent)).fetchone()
            eligible=state['tick']>=20 and all(p['played']>=2 for p in state['players']) and count<5 and not used and state['winner'] is not None
            if not eligible:state['rewards'][address]='Тренировочный результат: без ресурсов и изменения рейтинга.';continue
            won=state['winner']==side
            base,_=district._read(address,now);data=base['_colony']
            base['parts']+=80 if won else 15;base['fuel']+=20 if won else 5
            data['trophy']=max(0,data['trophy']+(25 if won else -15))
            if won:
                card=draw();add_card(data,card)
                reward=f'80 деталей, 20 топлива, карта {CARDS[card]["name"]}, +25 трофеев'
            else:reward='15 деталей, 5 топлива, −15 трофеев (минимум 0)'
            district._save(base)
            conn.execute('INSERT INTO arena_rewards VALUES(?,?,?)',(address,day,opponent))
            state['rewards'][address]=reward


def advance(match_id, now=None):
    now=district.now_seconds() if now is None else now
    with unit_of_work() as conn:
        row=conn.execute('SELECT * FROM arena_matches WHERE id=?',(match_id,)).fetchone()
        if not row:return None
        state=json.loads(row['state'])
        if row['status']=='finished':return state
        target=min(DURATION,max(0,now-row['created_at']))
        while state['tick']<target:
            _step(state)
            scores=[sum(p['towers']) for p in state['players']]
            if min(scores)==0 or state['tick']==DURATION:
                state['winner']=0 if scores[0]>scores[1] else 1 if scores[1]>scores[0] else None
                state['reason']='Время вышло' if state['tick']==DURATION else 'Обе башни разрушены';break
        finished=bool(state['reason'])
        if finished:_reward(row,state)
        conn.execute('UPDATE arena_matches SET state=?,status=?,updated_at=? WHERE id=?',(json.dumps(state),'finished' if finished else 'active',now,match_id))
        return state


def play(address, match_id, card_id, lane, revision):
    with unit_of_work() as conn:
        row=conn.execute('SELECT * FROM arena_matches WHERE id=? AND (p1=? OR p2=?)',(match_id,address,address)).fetchone()
        if not row:return False,'Бой не найден.'
        state=advance(match_id);side=0 if row['p1']==address else 1;player=state['players'][side]
        if state['reason']:return False,'Бой уже завершён.'
        try:card_id=int(card_id);lane=int(lane);revision=int(revision)
        except (TypeError,ValueError):return False,'Выберите карту и линию.'
        if revision!=player['hand_revision']:return False,'Рука уже изменилась. Выберите карту заново.'
        if lane not in (0,1) or card_id not in player['deck'][:4]:return False,'Этой карты нет в руке или линия неверна.'
        card=CARDS[card_id]
        if player['elixir']<card['cost']:return False,'Недостаточно эликсира.'
        if sum(u['side']==side for u in state['units'])>=24:return False,'На поле уже 24 ваших отряда.'
        if player['towers'][lane]==0 and state['players'][1-side]['towers'][lane]==0:return False,'На этой линии обе башни разрушены. Играйте на другой.'
        deploy(state,side,card_id,lane)
        conn.execute('UPDATE arena_matches SET state=? WHERE id=?',(json.dumps(state),match_id))
    return True,'Карта разыграна.'


def surrender(address,match_id):
    with unit_of_work() as conn:
        row=conn.execute("SELECT * FROM arena_matches WHERE id=? AND status='active' AND (p1=? OR p2=?)",(match_id,address,address)).fetchone()
        if not row:return False,'Активный бой не найден.'
        state=advance(match_id)
        if state['reason']:return False,'Бой уже завершён.'
        state['winner']=1 if row['p1']==address else 0;state['reason']='Соперник сдался'
        _reward(row,state)
        conn.execute("UPDATE arena_matches SET state=?,status='finished' WHERE id=?",(json.dumps(state),match_id))
    return True,'Бой завершён. Энергия не возвращается.'


def snapshot(address, match_id=None):
    now=district.now_seconds()
    with unit_of_work(write=False) as conn:
        waiting=conn.execute('SELECT * FROM arena_queue WHERE address=? AND heartbeat>=?',(address,now-45)).fetchone()
        if match_id:
            row=conn.execute('SELECT * FROM arena_matches WHERE id=? AND (p1=? OR p2=?)',(match_id,address,address)).fetchone()
        else:row=conn.execute("SELECT * FROM arena_matches WHERE p1=? OR p2=? ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,created_at DESC,rowid DESC LIMIT 1",(address,address)).fetchone()
        labels={r['address']:r['label'] for r in conn.execute('SELECT address,label FROM accounts WHERE address IN (?,?)',(row['p1'],row['p2']))} if row else {}
    state=json.loads(row['state']) if row else None
    if state:
        own=0 if row['p1']==address else 1
        for side,player in enumerate(state['players']):
            player['label']=labels[player['address']]
            player['avatar']=colony.profile(player['address'])['avatar']
            if side!=own:
                for private in ('deck','levels','stats','elixir','hand_revision'):player.pop(private,None)
        state.update(id=row['id'],status=row['status'],side=own,server_now=now)
    return dict(match=state,waiting=dict(waiting) if waiting else None,world=colony.view(address),cards=CARDS)
