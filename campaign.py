"""Server-authoritative PvE using the same combat engine as the human arena.

Bots are local battle state, never accounts or participants in the PvP queue.
Rewards are single-use ledger rows; daily identity is fixed at battle start (UTC).
"""
import json
import uuid
from database import unit_of_work
import district
import colony
import arena
import card_builds

BOSSES=[
 dict(id=0,name='Кракен · Хранитель рифа',zone='Лазурный риф',theme='reef',
      power='Ремонт панциря',hint='Каждые 20 секунд восстанавливает до 18 здоровья живым башням. Сосредоточьте осадный урон на одной линии.',reward='Печать хранителя',card=4),
 dict(id=1,name='Веспера · Сердце печи',zone='Пепельная гавань',theme='ember',
      power='Огненный залп',hint='Каждые 20 секунд наносит 14 урона вашим отрядам за рекой. Поддерживайте наступление медиком.',reward='Печать пламени',card=4),
 dict(id=2,name='Борей · Ледяной адмирал',zone='Северный предел',theme='frost',
      power='Холодный фронт',hint='Каждые 20 секунд замедляет ваши отряды на 4 секунды. Дальнобойные карты сохраняют темп атаки.',reward='Печать севера',card=6),
]
NAMES=['Высадка','Тихая бухта','Волнорез','Страж рифа','Горящие доки','Литейный двор','Чёрный причал','Сердце печи','Туманная отмель','Северный мост','Ледяной форт','Последний адмирал']


def stages():
    return [dict(id=i,name=name,chapter=(i-1)//4,boss=i%4==0,parts=25+10*i,fuel=10+5*i,
                 tower=150+15*i,scale=70+4*i,interval=max(2,7-(i-1)//2)) for i,name in enumerate(NAMES,1)]


def active(address):
    with unit_of_work(write=False) as conn:
        row=conn.execute("SELECT * FROM pve_matches WHERE address=? AND status='active'",(address,)).fetchone()
    return dict(row) if row else None


def cleared(address):
    with unit_of_work(write=False) as conn:
        rows=conn.execute("SELECT reward_key FROM pve_rewards WHERE address=? AND reward_key LIKE 'stage:%'",(address,)).fetchall()
    return {int(row[0].split(':')[1]) for row in rows}


def start(address,mode,stage_id):
    now=district.now_seconds();day=district.calendar(now)['day']
    with unit_of_work() as conn:
        if active(address):return False,'Бой кампании уже идёт. Продолжите его.'
        if arena.active(address):return False,'Сначала завершите бой PvP.'
        if conn.execute('SELECT 1 FROM arena_queue WHERE address=? AND heartbeat>=?',(address,now-45)).fetchone():return False,'Сначала отмените поиск соперника на арене.'
        completed=cleared(address)
        if mode=='daily':
            if 4 not in completed:return False,'Ежедневный босс откроется после этапа 4.'
            boss_id=(now//86400)%3
            spec=dict(id=0,name='Ежедневное испытание',chapter=boss_id,boss=True,parts=120,fuel=40,tower=360,scale=116,interval=2)
            key='daily:'+day
        elif mode=='stage':
            try:stage_id=int(stage_id)
            except (TypeError,ValueError):return False,'Выберите этап.'
            if not 1<=stage_id<=12 or (stage_id>1 and stage_id-1 not in completed):return False,'Пройдите предыдущий этап.'
            spec=stages()[stage_id-1];boss_id=spec['chapter'];key='stage:'+str(stage_id)
        else:return False,'Неизвестный режим кампании.'
        state,_=district._read(address,now)
        if state['_colony']['energy']<3:return False,'Для боя нужно 3 энергии.'
        player=arena._new_player(address,now)
        boss=BOSSES[boss_id]
        decks=[[1,2,0,3,5,4],[1,4,0,5,3,2],[1,6,2,3,7,5]]
        deck=decks[boss_id]
        stats={str(c):card_builds.stats(arena.CARDS[c],{'level':1}) for c in deck}
        for values in stats.values():
            values['hp']=values['hp']*spec['scale']//100;values['attack']=max(1,values['attack']*spec['scale']//100)
        bot=dict(address='bot',label=boss['name'] if spec['boss'] else 'Гарнизон · '+spec['name'],avatar='boss:'+str(boss_id),
                 deck=deck.copy(),levels={str(c):1 for c in deck},stats=stats,elixir=5,hand_revision=0,played=0,
                 towers=[spec['tower']]*2,tower_max=spec['tower'],seen=now)
        match_id=uuid.uuid4().hex
        state=dict(players=[player,bot],units=[],tick=0,next_unit=1,winner=None,reason='',rewards={},started=now,version=0,
                   pve=dict(mode=mode,spec=spec,boss=boss,key=key,day=day),events=[],boss_event=None)
        conn.execute("INSERT INTO pve_matches VALUES(?,?,?,'active',?,?)",(match_id,address,json.dumps(state),now,now))
        conn.execute('DELETE FROM arena_queue WHERE address=?',(address,))
    return True,'Бой начался. Выберите карту и линию.'


def _ai(state):
    spec=state['pve']['spec'];tick=state['tick'];bot=state['players'][1]
    if tick%spec['interval'] or sum(u['side']==1 for u in state['units'])>=24:return
    choices=[c for c in bot['deck'][:4] if arena.CARDS[c]['cost']<=bot['elixir']]
    if not choices:return
    lanes=[lane for lane in (0,1) if bot['towers'][lane]>0 or state['players'][0]['towers'][lane]>0]
    if not lanes:return
    threats={lane:sum(u['attack'] for u in state['units'] if u['side']==0 and u['lane']==lane and u['x']>45) for lane in lanes}
    lane=max(lanes,key=lambda x:(threats[x],-state['players'][0]['towers'][x],-(x-(tick//spec['interval'])%2)**2))
    friends=[u for u in state['units'] if u['side']==1 and u['lane']==lane]
    # Defend approaching units, send tanks ahead, support injured troops with medic.
    priorities=[3,4,6,1,5,2,7,0] if any(u['hp']<u['max_hp']*.7 for u in friends) else [1,7,0,2,5,4,6,3] if not friends else [5,2,4,6,3,0,7,1]
    card=min(choices,key=priorities.index)
    arena.deploy(state,1,card,lane)


def _power(state):
    spec=state['pve']['spec']
    if not spec['boss'] or not state['tick'] or state['tick']%20:return
    boss=state['pve']['boss'];bot=state['players'][1]
    if boss['id']==0:bot['towers']=[min(bot['tower_max'],hp+18) if hp else 0 for hp in bot['towers']]
    elif boss['id']==1:
        for unit in state['units']:
            if unit['side']==0 and unit['x']>=50:unit['hp']-=max(1,14-unit.get('armor',0))
        state['units']=[u for u in state['units'] if u['hp']>0]
    else:
        for unit in state['units']:
            if unit['side']==0:unit['slow_until']=max(unit.get('slow_until',0),state['tick']+4)
    state['boss_event']=dict(tick=state['tick'],name=boss['power'],theme=boss['theme'])


def _reward(row,state):
    address=row['address'];pve=state['pve'];now=district.now_seconds()
    if state['winner']!=0:
        state['rewards'][address]='Награда за победу ещё впереди. Энергия восстановится автоматически.';return
    with unit_of_work() as conn:
        if conn.execute('SELECT 1 FROM pve_rewards WHERE address=? AND reward_key=?',(address,pve['key'])).fetchone():
            state['rewards'][address]='Тренировочная победа. Награда за этот этап или день уже получена.';return
        base,_=district._read(address,now);data=base['_colony'];spec=pve['spec']
        base['parts']+=spec['parts'];base['fuel']+=spec['fuel']
        reward=f"{spec['parts']} деталей, {spec['fuel']} топлива"
        if pve['mode']=='daily':
            boss=pve['boss'];arena.add_card(data,boss['card'])
            owned=set(data.get('boss_avatars',[]));owned.add(boss['id']);data['boss_avatars']=sorted(owned)
            data['boss_medals']=data.get('boss_medals',0)+1
            reward+=f", карта {arena.CARDS[boss['card']]['name']}, {boss['reward']} и аватар босса"
        elif spec['boss']:
            card=arena.draw(True);arena.add_card(data,card);reward+=', карта '+arena.CARDS[card]['name']
        district._save(base)
        conn.execute('INSERT INTO pve_rewards VALUES(?,?,?,?)',(address,pve['key'],row['id'],now))
        state['rewards'][address]=reward+'. Награда сохранена.'


def advance(match_id,now=None):
    now=district.now_seconds() if now is None else now
    with unit_of_work() as conn:
        row=conn.execute('SELECT * FROM pve_matches WHERE id=?',(match_id,)).fetchone()
        if not row:return None
        state=json.loads(row['state'])
        if row['status']=='finished':return state
        target=min(arena.DURATION,max(0,now-row['created_at']))
        while state['tick']<target:
            _ai(state);arena._step(state);_power(state)
            scores=[sum(p['towers']) for p in state['players']]
            if min(scores)==0 or state['tick']==arena.DURATION:
                # Both sides compete by remaining fraction; otherwise a boss with
                # more starting HP could win a passive timeout without combat.
                ratios=[scores[i]*state['players'][1-i].get('tower_max',300) for i in (0,1)]
                state['winner']=0 if ratios[0]>ratios[1] else 1 if ratios[1]>ratios[0] else None
                state['reason']='Время вышло' if state['tick']==arena.DURATION else 'Обе башни разрушены';break
        finished=bool(state['reason'])
        if finished:_reward(row,state)
        conn.execute('UPDATE pve_matches SET state=?,status=?,updated_at=? WHERE id=?',(json.dumps(state),'finished' if finished else 'active',now,match_id))
        return state


def command(address,action,form):
    if action=='start':return start(address,form.get('mode','stage'),form.get('stage'))
    with unit_of_work() as conn:
        row=conn.execute("SELECT * FROM pve_matches WHERE id=? AND address=? AND status='active'",(form.get('match_id'),address)).fetchone()
        if not row:return False,'Активный бой не найден.'
        state=advance(row['id'])
        if state['reason']:return False,'Бой уже завершён.'
        if action=='surrender':
            state['winner']=1;state['reason']='Вы отступили';_reward(row,state)
            conn.execute("UPDATE pve_matches SET state=?,status='finished',updated_at=? WHERE id=?",(json.dumps(state),district.now_seconds(),row['id']))
            return True,'Бой завершён. Можно попробовать другой билд.'
        if action!='play':return False,'Неизвестное действие.'
        try:card=int(form.get('card_id'));lane=int(form.get('lane'));revision=int(form.get('revision'))
        except (ValueError,TypeError):return False,'Выберите карту и линию.'
        player=state['players'][0]
        if revision!=player['hand_revision']:return False,'Рука уже изменилась. Выберите карту заново.'
        if card not in player['deck'][:4] or lane not in (0,1):return False,'Эта карта или линия недоступна.'
        if player['elixir']<arena.CARDS[card]['cost']:return False,'Недостаточно эликсира.'
        if sum(u['side']==0 for u in state['units'])>=24:return False,'На поле уже 24 ваших отряда.'
        if player['towers'][lane]==0 and state['players'][1]['towers'][lane]==0:return False,'На этой линии обе башни разрушены.'
        arena.deploy(state,0,card,lane)
        conn.execute('UPDATE pve_matches SET state=? WHERE id=?',(json.dumps(state),row['id']))
    return True,'Карта разыграна.'


def snapshot(address):
    now=district.now_seconds();day=district.calendar(now);completed=cleared(address)
    with unit_of_work(write=False) as conn:
        row=conn.execute("SELECT * FROM pve_matches WHERE address=? ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,created_at DESC,rowid DESC LIMIT 1",(address,)).fetchone()
        claimed=bool(conn.execute('SELECT 1 FROM pve_rewards WHERE address=? AND reward_key=?',(address,'daily:'+day['day'])).fetchone())
        account=conn.execute('SELECT label FROM accounts WHERE address=?',(address,)).fetchone()
    state=json.loads(row['state']) if row else None
    if state:
        state['players'][0].update(label=account['label'],avatar=colony.profile(address)['avatar'])
        for private in ('deck','levels','stats','elixir','hand_revision'):state['players'][1].pop(private,None)
        state.update(id=row['id'],status=row['status'],side=0,server_now=now)
    return dict(match=state,waiting=None,world=colony.view(address),cards=arena.CARDS,
                stages=[dict(s,complete=s['id'] in completed,unlocked=s['id']==1 or s['id']-1 in completed) for s in stages()],
                bosses=BOSSES,daily=dict(BOSSES[(now//86400)%3],unlocked=4 in completed,claimed=claimed,reset=day['day_end'],day=day['day']),
                completed=len(completed))
