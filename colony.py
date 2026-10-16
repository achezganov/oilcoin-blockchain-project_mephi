"""Colony v4: persistent allocation, connected factories, collection and shop.

GET projections remain read-only. All commands run in the outer SQLite write
transaction. Active profiles are saved by the worker; offline gains are projected on return.
"""
import json
import secrets
from collections import Counter
from database import unit_of_work
import district

SHOP = {
    'pipe': dict(name='Трубы ×10', icon='╋', cost={'parts':10,'crude':20}, limit=240, size=1, description='Соедините вышку и заводы. Каждая клетка трубы занимает один сегмент.'),
    'pump_station': dict(name='Малая вышка', icon='♟', cost={'parts':90,'crude':120}, limit=3, size=2, description='+2 нефти за 10 секунд и +600 к дневному пределу. Работает после размещения.'),
    'workshop': dict(name='Мастерская', icon='⚙', cost={'parts':60,'crude':80}, limit=1, size=2, description='Независимая линия деталей: 6 нефти → 7 деталей. Нужна труба от вышки.'),
    'fuel_station': dict(name='Катализатор', icon='ϟ', cost={'parts':120,'fuel':40}, limit=1, size=2, description='+25% выхода топлива при подключении к нефтесети.'),
    'compressor': dict(name='Компрессор', icon='◎', cost={'parts':160,'fuel':60}, limit=1, size=2, description='+25% добычи и дневной квоты, когда подключён к нефтесети.'),
    'battery': dict(name='Энергоцентр', icon='✦', cost={'parts':180,'fuel':80}, limit=1, size=2, description='Энергия PvP: максимум 15 вместо 10; восстановление раз в 3 вместо 5 минут.'),
    'warehouse': dict(name='Склад', icon='▤', cost={'parts':100,'crude':100}, limit=2, size=2, description='+150 места для нефти, +75 топлива, +250 деталей после размещения.'),
}
AVATARS = [f'card:{i}' for i in range(8)] + [f'adviser:{i}' for i in range(4)]


def default(now):
    return dict(avatar='adviser:0', tutorial=0, builders=1, size=8, inventory={},
                fuel_pct=0, parts_pct=0, cursor=0, fuel_credit=0, parts_credit=0,
                buffer_fuel=0, buffer_parts=0, generated_day=district.calendar(now)['day'], generated=0,
                energy=10, energy_at=now, last_seen=0, trophy=0, cards={str(i):{'level':1,'copies':0} for i in range(6)},
                deck=[0,1,2,3,4,5], revision=0)


def profile(address, now=None):
    now = district.now_seconds() if now is None else now
    with unit_of_work(write=False) as conn:
        row = conn.execute('SELECT payload FROM colony_profiles WHERE address=?',(address,)).fetchone()
    return json.loads(row[0]) if row else default(now)


def save_profile(address, data):
    with unit_of_work() as conn:
        conn.execute('INSERT INTO colony_profiles VALUES(?,?) ON CONFLICT(address) DO UPDATE SET payload=excluded.payload',
                     (address,json.dumps(data,separators=(',',':'))))


def network(address, data=None, objects=None):
    import layout
    data = data or profile(address)
    objects = objects if objects is not None else layout.read(address, include_colony=False)['objects']
    pipes = {(o['x'],o['y']) for o in objects if o['kind']=='pipe'}
    def adjacent(item):
        size = 2 if item['kind'] in district.BUILDINGS or item['kind'] in SHOP and SHOP[item['kind']]['size']==2 else 1
        x,y=item['x'],item['y']
        return {(x-1,y+i) for i in range(size)}|{(x+size,y+i) for i in range(size)}|{(x+i,y-1) for i in range(size)}|{(x+i,y+size) for i in range(size)}
    live=set()
    for obj in objects:
        if obj['kind'] in ('pump','pump_station'): live |= adjacent(obj)&pipes
    queue=list(live)
    while queue:
        x,y=queue.pop()
        for cell in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
            if cell in pipes and cell not in live: live.add(cell);queue.append(cell)
    powered={o['kind'] for o in objects if adjacent(o)&live}
    placed=Counter(o['kind'] for o in objects)
    return dict(powered=sorted(powered),placed=dict(placed),live=[list(cell) for cell in sorted(live)],
                fuel='refinery' in powered,parts='workshop' in powered,booster='compressor' in powered,
                catalyst='fuel_station' in powered)


def storage_bonus(address):
    count=network(address)['placed'].get('warehouse',0)
    return {'crude':count*150,'fuel':count*75,'parts':count*250}


def energy(data, net, now):
    upgraded=bool(net['placed'].get('battery'))
    cap=15 if upgraded else 10; interval=180 if upgraded else 300
    ticks=max(0,now-data['energy_at'])//interval
    data['energy']=min(cap,data['energy']+ticks)
    data['energy_at']=now if data['energy']>=cap else data['energy_at']+ticks*interval
    return dict(current=data['energy'],capacity=cap,interval=interval,next_at=data['energy_at']+interval)


def project(address,state,levels,now):
    data=profile(address,now); net=network(address,data)
    bonus=net['placed'].get('warehouse',0)
    caps={'crude':300*levels['depot']+150*bonus,'fuel':150*levels['depot']+75*bonus,'parts':500*levels['depot']+250*bonus}
    rate=levels['pump']+2*net['placed'].get('pump_station',0)
    quota=600*(levels['pump']+net['placed'].get('pump_station',0))
    # Quarter-units retain the compressor's 25% gain without floating point.
    rate_q=rate*(5 if net['booster'] else 4)
    quota=quota*(5 if net['booster'] else 4)//4
    end=state['last_tick']+max(0,now-state['last_tick'])//10*10
    start=max(state['last_tick'],end-8*3600)  # eight hours catch-up; full buffers stop output
    while start<end:
        # Attribute a completed ten-second tick to its completion day. Keep
        # the same tick origin across UTC midnight and arbitrary polling.
        day=district.calendar(start+10)
        until=min(end,day['day_end']-1)
        ticks=(until-start)//10
        if data['generated_day']!=day['day']:
            data['generated_day']=day['day'];data['generated']=0
        gross_q=ticks*rate_q+data.get('pump_credit',0)
        gross=min(gross_q//4,max(0,quota-data['generated']))
        data['pump_credit']=gross_q%4
        fp=data['fuel_pct'] if net['fuel'] else 0; pp=data['parts_pct'] if net['parts'] else 0
        allocations={'fuel':0,'parts':0,'crude':0}
        full,tail=divmod(gross,100)
        allocations.update(fuel=full*fp,parts=full*pp,crude=full*(100-fp-pp))
        for i in range(tail):
            slot=((data['cursor']+i)*37)%100
            allocations['fuel' if slot<fp else 'parts' if slot<fp+pp else 'crude']+=1
        data['cursor']=(data['cursor']+tail)%100
        # Each refinery/garage level adds 10% of the corresponding base yield.
        for key,numerator,denominator in [('fuel',3*(9+levels['refinery']),40 if net['catalyst'] else 50),('parts',7*(9+levels['garage']),60)]:
            credit_key=key+'_credit'; buffer_key='buffer_'+key
            old_denom=data.get(key+'_denom',5 if key=='fuel' else 6)
            if old_denom!=denominator:
                data[credit_key]=data[credit_key]*denominator//old_denom
            data[key+'_denom']=denominator
            credit=data[credit_key]
            space=max(0,caps[key]-data[buffer_key])
            used=min(allocations[key],max(0,(space*denominator+denominator-1-credit)//numerator))
            value=used*numerator+credit
            data[buffer_key]+=value//denominator;data[credit_key]=value%denominator
            allocations['crude']+=allocations[key]-used
        space=max(0,caps['crude']-state['ready_crude'])
        kept=min(space,allocations['crude']);state['ready_crude']+=kept
        data['generated']+=gross-(allocations['crude']-kept)
        start+=ticks*10
    state['last_tick']=end
    if data['generated_day']!=district.calendar(now)['day']:
        data['generated_day']=district.calendar(now)['day'];data['generated']=0
    energy(data,net,now)
    state['_colony']=data
    state['_flow']=dict(network=net,rate=rate_q*6/4,quota=quota,remaining=max(0,quota-data['generated']))
    return state


def available_avatars(data):
    return AVATARS + ['boss:'+str(i) for i in data.get('boss_avatars',[]) if i in (0,1,2)]


def view(address):
    now=district.now_seconds();state,levels=district._read(address,now);data=state['_colony'];net=state['_flow']['network']
    with unit_of_work(write=False) as conn:
        jobs=[dict(row) for row in conn.execute('SELECT * FROM construction_jobs WHERE address=? AND claimed_at IS NULL ORDER BY id',(address,))]
        runtime={row['key']:json.loads(row['payload']) for row in conn.execute('SELECT * FROM runtime_state')}
    buffers={'crude':state['ready_crude'],'fuel':data['buffer_fuel'],'parts':data['buffer_parts']}
    caps=district.capacities(levels['depot'],address);used=district.storage_used(address,state)
    storage={r:dict(stored=state[r],used=used[r],reserved=max(0,used[r]-state[r]),capacity=caps[r],
                    take=min(buffers[r],max(0,caps[r]-used[r]))) for r in caps}
    for r,item in storage.items():item.update(remaining=buffers[r]-item['take'],percent=min(100,round(100*item['used']/item['capacity'],1)))
    return dict(storage=storage,profile=data,network=net,flow=state['_flow'],energy=energy(data,net,now),jobs=jobs,
                shop=SHOP,avatars=available_avatars(data),worker=runtime,buffer={'crude':state['ready_crude'],'fuel':data['buffer_fuel'],'parts':data['buffer_parts']},
                builder_cost={'parts':100*data['builders'],'crude':100*data['builders']},
                expansion_cost={'parts':200*(data['size']//2-3),'crude':200*(data['size']//2-3)},now=now)


def touch(address):
    district.ensure_base(address);state,_=district._read(address,district.now_seconds())
    state['_colony']['last_seen']=district.now_seconds();district._save(state)


def collect_all(address):
    district.ensure_base(address);now=district.now_seconds();state,levels=district._read(address,now)
    data=state['_colony'];limits=district.capacities(levels['depot'],address);used=district.storage_used(address,state)
    taken={}
    for resource,field in [('crude','ready_crude'),('fuel','buffer_fuel'),('parts','buffer_parts')]:
        source=state if resource=='crude' else data
        amount=min(source[field],max(0,limits[resource]-used[resource]));taken[resource]=amount
        source[field]-=amount;state[resource]+=amount
    district._save(state)
    return bool(sum(taken.values())), ('Склад заполнен. Готовые ресурсы сохранены в буферах.' if not sum(taken.values()) else 'Собрано: '+', '.join(f'{n} {district.RESOURCES[k]}' for k,n in taken.items())+'. Остаток ждёт места на складе.')


def action(address, command, form):
    district.ensure_base(address);now=district.now_seconds();state,levels=district._read(address,now);data=state['_colony']
    cost={}; message='Изменения сохранены.'
    if command=='allocation':
        try:
            fuel=int(form.get('fuel_pct',''));parts=int(form.get('parts_pct',''))
            if not 0<=fuel<=100 or not 0<=parts<=100 or fuel+parts>100: raise ValueError
        except (TypeError,ValueError):return False,'Доли должны быть от 0 до 100%, сумма — не больше 100%.'
        data['fuel_pct']=fuel;data['parts_pct']=parts;data['cursor']=0
        message='Доли сохранены. Неподключённые линии оставляют свою долю нефтью.'
    elif command=='buy':
        kind=form.get('kind');spec=SHOP.get(kind)
        if not spec:return False,'Неизвестный объект.'
        count=data['inventory'].get(kind,0);amount=10 if kind=='pipe' else 1
        if count+amount>spec['limit']:return False,'Достигнут предел объектов этого типа.'
        cost=spec['cost'];data['inventory'][kind]=count+amount
        message=f'{spec["name"]} на складе объектов. Разместите на базе.'
    elif command=='builder':
        if data['builders']>=5:return False,'У вас уже пять строителей.'
        cost={'parts':100*data['builders'],'crude':100*data['builders']};data['builders']+=1
        message='Новый строитель готов: можно улучшать ещё одно здание одновременно.'
    elif command=='expand':
        if data['size']>=12:return False,'База уже расширена до 12×12.'
        if levels['hq']<data['size']//2-2:return False,'Для расширения сначала улучшите штаб.'
        cost={'parts':200*(data['size']//2-3),'crude':200*(data['size']//2-3)};data['size']+=2
        message=f'Территория расширена до {data["size"]}×{data["size"]}.'
    elif command=='avatar':
        if form.get('avatar') not in available_avatars(data):return False,'Выберите аватар из галереи.'
        data['avatar']=form['avatar']
    elif command=='tutorial':
        try:step=int(form.get('step',''))
        except (ValueError,TypeError):return False,'Неизвестный шаг.'
        if not 0<=step<=5:return False,'Неизвестный шаг.'
        data['tutorial']=step
    else:return False,'Неизвестное действие.'
    if not district._can_spend(state,cost):return False,'Не хватает ресурсов. Соберите производство или продайте лишнее на рынке.'
    district._spend(state,cost);data['revision']+=1;district._save(state)
    return True,message
