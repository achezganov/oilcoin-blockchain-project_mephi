"""Server-authoritative base economy. OIL payments stay on the signed ledger.

Resources, timers and league points are off-chain game state. GET projections
never write; every action runs inside the HTTP SQLite write transaction.
"""
import time
from datetime import datetime, timedelta, timezone

from accounts import decrypt_private_key
from database import unit_of_work
from game import TREASURY_ADDRESS, award_event, rig_state

BUILDINGS = {
    'hq': dict(name='Штаб', role='Открывает новые уровни зданий, рынок и расширения острова.', oil=40, parts=80, seconds=60, weight=50),
    'pump': dict(name='Нефтяная вышка', role='Добывает нефть, даже когда вы вне игры. Собирайте накопленное.', oil=20, parts=40, seconds=30, weight=30),
    'depot': dict(name='Резервуары', role='Увеличивают вместимость ресурсов и накопителя вышки.', oil=15, parts=35, seconds=30, weight=20),
    'refinery': dict(name='Переработка', role='Автоматически выпускает топливо. Каждый уровень выше первого: +10% базового выхода.', oil=25, parts=50, seconds=45, weight=30),
    'garage': dict(name='Гараж', role='Обслуживает мастерскую: каждый уровень выше первого даёт +10% базового выхода деталей.', oil=30, parts=60, seconds=45, weight=40),
}
RESOURCES = {'crude': 'нефть', 'fuel': 'топливо', 'parts': 'детали'}
DAILY_CRUDE_PER_LEVEL = 600
EXPEDITION_REPAIR_PER_SECTOR = 5
REPEAT_LOOT_DIVISOR = 400
RECIPES = {
    'fuel': dict(name='Топливо', description='Для экспедиций и заказов города.', cost={'crude': 20}, output='fuel', amount=12, seconds=30, level=1),
    'parts': dict(name='Комплект деталей', description='Для строительства и больших заказов.', cost={'crude': 20, 'fuel': 6}, output='parts', amount=35, seconds=45, level=2),
}
CONTRACTS = (
    dict(id='terminal', name='Заказ нефтетерминала', description='Отправьте сырьё в порт.', cost={'crude': 80}, parts=35, xp=20, points=20),
    dict(id='city', name='Заправки города', description='Пополните запас топлива для города.', cost={'fuel': 12}, parts=45, xp=25, points=30),
    dict(id='road', name='Большая логистика', description='Снабдите ремонтную колонну.', cost={'fuel': 18, 'parts': 10}, parts=70, xp=35, points=50),
)
SECTORS = (
    dict(id=1, name='Заброшенный причал', terrain='ПОБЕРЕЖЬЕ', power=30, parts=45, crude=30, xp=35, points=40),
    dict(id=2, name='Соляная дорога', terrain='ПУСТОШЬ', power=48, parts=70, crude=45, xp=45, points=55),
    dict(id=3, name='Станция «Север»', terrain='ХОЛМЫ', power=72, parts=100, crude=60, xp=60, points=70),
    dict(id=4, name='Каньон дронов', terrain='КАНЬОН', power=102, parts=140, crude=85, xp=75, points=90),
    dict(id=5, name='Чёрный перевал', terrain='ГОРЫ', power=134, parts=190, crude=110, xp=95, points=115),
    dict(id=6, name='Цитадель «Омега»', terrain='ФИНАЛ', power=170, parts=260, crude=150, xp=130, points=150),
)
APPROACHES = {
    'careful': dict(name='Разведка', description='Экономия топлива, больше добычи. Нужен сильный отряд.', fuel=6, power=0, loot=125),
    'balanced': dict(name='Конвой', description='Баланс силы, расхода и добычи.', fuel=10, power=10, loot=100),
    'assault': dict(name='Прорыв', description='Максимальная сила; больше топлива, меньше добычи.', fuel=18, power=24, loot=80),
}
LEAGUES = ((0, 'Бронзовая'), (100, 'Серебряная'), (300, 'Золотая'), (700, 'Платиновая'), (1200, 'Мастерская'))


def now_seconds():
    return int(time.time())


def calendar(now=None):
    current = datetime.fromtimestamp(now if now is not None else now_seconds(), timezone.utc)
    start = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return dict(day=current.strftime('%Y-%m-%d'), week_start=int(start.timestamp()),
                week_end=int((start + timedelta(days=7)).timestamp()),
                day_end=int((current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()))


def capacities(level, address=None):
    limits = {'crude':300*level, 'fuel':150*level, 'parts':500*level}
    if address:
        import colony
        bonus = colony.storage_bonus(address)
        limits = {key:value+bonus[key] for key,value in limits.items()}
    return limits


def collection_budget(address, levels, now):
    with unit_of_work(write=False) as conn:
        row = conn.execute('SELECT collected FROM extraction_days WHERE address=? AND day=?', (address, calendar(now)['day'])).fetchone()
    limit = DAILY_CRUDE_PER_LEVEL * levels['pump']
    return dict(limit=limit, remaining=max(0, limit-(row[0] if row else 0)))


def storage_used(address, state):
    from market import held
    escrow = held(address)
    return {key: state[key] + escrow.get(key, 0) for key in RESOURCES}


def ensure_base(address, now=None):
    now = now_seconds() if now is None else now
    with unit_of_work() as conn:
        conn.execute('INSERT OR IGNORE INTO estates VALUES(?,?,?,?,?,?)', (address, 60, 20, 120, 12, now))
        conn.executemany('INSERT OR IGNORE INTO buildings VALUES(?,?,1)', [(address, kind) for kind in BUILDINGS])


def _read(address, now):
    with unit_of_work(write=False) as conn:
        row = conn.execute('SELECT * FROM estates WHERE address=?', (address,)).fetchone()
        levels = {row['kind']: row['level'] for row in conn.execute('SELECT kind,level FROM buildings WHERE address=?', (address,))}
    state = dict(row) if row else dict(address=address, crude=60, fuel=20, parts=120, ready_crude=12, last_tick=now)
    levels = levels or dict.fromkeys(BUILDINGS, 1)
    import colony
    state = colony.project(address, state, levels, now)
    return state, levels


def _save(state):
    if '_colony' in state:
        import colony
        colony.save_profile(state['address'], state['_colony'])
    with unit_of_work() as conn:
        conn.execute('UPDATE estates SET crude=?,fuel=?,parts=?,ready_crude=?,last_tick=? WHERE address=?',
                     tuple(state[k] for k in ('crude', 'fuel', 'parts', 'ready_crude', 'last_tick', 'address')))


def _active(table, address):
    # Table names only come from module constants, never request values.
    assert table in {'construction_jobs', 'production_jobs', 'expedition_jobs'}
    with unit_of_work(write=False) as conn:
        row = conn.execute(f'SELECT * FROM {table} WHERE address=? AND claimed_at IS NULL', (address,)).fetchone()
    return dict(row) if row else None


def _can_spend(state, cost):
    return all(state[resource] >= amount for resource, amount in cost.items())


def _spend(state, cost):
    for resource, amount in cost.items():
        state[resource] -= amount


def _event(address, key, title, xp=0, points=0, now=None):
    now = now_seconds() if now is None else now
    with unit_of_work() as conn:
        inserted = conn.execute('INSERT OR IGNORE INTO farm_events(address,event_key,title,points,created_at) VALUES(?,?,?,?,?)',
                                (address, key, title, points, now)).rowcount
    if inserted and xp:
        award_event(address, 'farm:' + address + ':' + key, xp)


def confirm_construction(chain, now=None):
    """Run after mining, in the same transaction as persisting the new block."""
    now = now_seconds() if now is None else now
    confirmed = {tx['tx_id'] for block in chain.chain for tx in block.transactions}
    with unit_of_work() as conn:
        for row in conn.execute('SELECT id,tx_id,duration FROM construction_jobs WHERE started_at IS NULL').fetchall():
            if row['tx_id'] in confirmed:
                conn.execute('UPDATE construction_jobs SET started_at=?,ready_at=? WHERE id=?', (now, now + row['duration'], row['id']))


def get_base(chain, address):
    import layout
    now = now_seconds()
    state, levels = _read(address, now)
    limits = capacities(levels['depot'], address)
    budget = collection_budget(address, levels, now)
    used = storage_used(address, state)
    period = calendar(now)
    with unit_of_work(write=False) as conn:
        events = [dict(row) for row in conn.execute('SELECT * FROM farm_events WHERE address=? ORDER BY id DESC', (address,))]
    keys = {event['event_key'] for event in events}
    points = sum(event['points'] for event in events if period['week_start'] <= event['created_at'] < period['week_end'])
    league = max((item for item in LEAGUES if item[0] <= points), key=lambda item: item[0])
    next_league = next((item for item in LEAGUES if item[0] > points), None)
    build = _active('construction_jobs', address)
    with unit_of_work(write=False) as conn:
        build_count = conn.execute('SELECT COUNT(*) FROM construction_jobs WHERE address=? AND claimed_at IS NULL',(address,)).fetchone()[0]
    builders_full = build_count >= state['_colony']['builders']
    production = _active('production_jobs', address)
    expedition = _active('expedition_jobs', address)
    rig, _ = rig_state(chain, address)
    power = levels['garage'] * 18 + levels['hq'] * 8 + rig * 4
    available_oil = round(chain.get_balance(address) - chain.get_reserved_pending(address), 8)
    buildings = []
    for kind, spec in BUILDINGS.items():
        level = levels[kind]
        reason = ('Максимальный уровень' if level >= 5 else 'Строители заняты' if builders_full else
                  'Сначала улучшите штаб' if kind != 'hq' and level >= levels['hq'] else
                  'Не хватает деталей' if state['parts'] < spec['parts'] * level else
                  'Нужны OIL — добудьте блок' if available_oil < spec['oil'] * level else '')
        buildings.append(dict(spec, kind=kind, level=level, target=level + 1, cost_oil=spec['oil'] * level,
                              cost_parts=spec['parts'] * level, duration=spec['seconds'] * level, reason=reason))
    recipes = [dict(spec, id=key, unlocked=levels['refinery'] >= spec['level']) for key, spec in RECIPES.items()]
    contracts = [dict(spec, completed=f"contract:{period['day']}:{spec['id']}" in keys,
                      affordable=_can_spend(state, spec['cost'])) for spec in CONTRACTS]
    sectors = [dict(spec, cleared=f"sector:{spec['id']}" in keys, daily_claimed=f"sortie:{period['day']}:{spec['id']}" in keys,
                    repair=EXPEDITION_REPAIR_PER_SECTOR * spec['id'], league_claimed=f"sector-week:{period['week_start']}:{spec['id']}" in keys,
                    unlocked=spec['id'] == 1 or f"sector:{spec['id'] - 1}" in keys) for spec in SECTORS]
    for job in (build, production, expedition):
        if job:
            job['remaining'] = max(0, (job['ready_at'] or now) - now)
            job['ready'] = job['ready_at'] is not None and job['ready_at'] <= now
    return dict(resources={key: state[key] for key in RESOURCES}, capacity=limits, ready_crude=state['ready_crude'],
                collectable=min(state['ready_crude'], max(0, limits['crude'] - used['crude']), budget['remaining']),
                rate=levels['pump'] * 6, production_tick=state['last_tick'], levels=levels, buildings=buildings, build=build, production=production,
                expedition=expedition, recipes=recipes, contracts=contracts, sectors=sectors, approaches=APPROACHES,
                power=power, rating=sum(BUILDINGS[kind]['weight'] * level for kind, level in levels.items()),
                points=points, league=league[1], next_league=next_league, period=period, now=now,
                events=events[:8], cleared=sum(sector['cleared'] for sector in sectors), oil=available_oil,
                resource_names=RESOURCES, layout=layout.read(address), budget=budget, storage_used=used)


def collect(address):
    now = now_seconds(); ensure_base(address, now)
    state, levels = _read(address, now)
    budget = collection_budget(address, levels, now)
    amount = min(state['ready_crude'], max(0, capacities(levels['depot'], address)['crude'] - storage_used(address, state)['crude']), budget['remaining'])
    if not amount:
        return False, 'Накопитель пуст, склад занят или исчерпана суточная квота. Квота обновляется в 00:00 UTC.'
    state['crude'] += amount; state['ready_crude'] -= amount; _save(state)
    with unit_of_work() as conn:
        conn.execute('INSERT INTO extraction_days VALUES(?,?,?) ON CONFLICT(address,day) DO UPDATE SET collected=collected+excluded.collected',
                     (address, calendar(now)['day'], amount))
    return True, f'Собрано {amount} ед. нефти. Отправьте её в переработку или выполните заказ.'


def start_build(chain, account, kind, target):
    if kind not in BUILDINGS:
        return False, 'Неизвестное здание.'
    address = account['address']; now = now_seconds(); ensure_base(address, now)
    state, levels = _read(address, now); level = levels[kind]; spec = BUILDINGS[kind]
    if target != str(level + 1) or level >= 5:
        return False, 'Уровень изменился. Обновите страницу базы.'
    with unit_of_work(write=False) as conn:
        jobs=conn.execute('SELECT kind FROM construction_jobs WHERE address=? AND claimed_at IS NULL',(address,)).fetchall()
    if len(jobs) >= state['_colony']['builders'] or any(row['kind']==kind for row in jobs):
        return False, 'Строители заняты или это здание уже улучшается.'
    if kind != 'hq' and level >= levels['hq']:
        return False, 'Сначала улучшите штаб: уровень здания не может превышать его уровень.'
    if state['parts'] < spec['parts'] * level:
        return False, 'Не хватает деталей. Выполните заказ или экспедицию.'
    ok, message = chain.add_transaction(address, TREASURY_ADDRESS, spec['oil'] * level, 0, private_key=decrypt_private_key(account))
    if not ok:
        return False, 'Недостаточно доступных OIL. Добудьте блок или дождитесь входящего перевода.'
    state['parts'] -= spec['parts'] * level; _save(state)
    with unit_of_work() as conn:
        conn.execute('INSERT INTO construction_jobs(address,kind,target_level,tx_id,duration,created_at) VALUES(?,?,?,?,?,?)',
                     (address, kind, level + 1, chain.pending_transactions[-1]['tx_id'], spec['seconds'] * level, now))
    return True, 'Заказ оплачен OIL и деталями. Подтвердите перевод блоком — после этого начнётся строительство.'


def finish_build(address, job_id):
    now = now_seconds()
    with unit_of_work(write=False) as conn:
        row=conn.execute('SELECT * FROM construction_jobs WHERE address=? AND id=? AND claimed_at IS NULL',(address,job_id)).fetchone()
    job=dict(row) if row else None
    if not job:
        return False, 'Эта стройка уже завершена или не принадлежит вам.'
    if job['ready_at'] is None or job['ready_at'] > now:
        return False, 'Стройка ещё не готова: сначала подтверждение блока, затем таймер.'
    state, _ = _read(address, now)
    if job["kind"] == "pump":
        state["last_tick"] = now
    _save(state)  # Accrue at the old rate before applying a new pump level.
    with unit_of_work() as conn:
        conn.execute('UPDATE buildings SET level=? WHERE address=? AND kind=?', (job['target_level'], address, job['kind']))
        conn.execute('UPDATE construction_jobs SET claimed_at=? WHERE id=?', (now, job['id']))
    _event(address, f"build:{job['id']}", f"{BUILDINGS[job['kind']]['name']}: уровень {job['target_level']}", xp=30 * job['target_level'], now=now)
    return True, 'Улучшение готово! Здание стало сильнее, строитель свободен.'


def start_production(address, recipe_id, batches):
    if recipe_id not in RECIPES or batches not in ('1', '3'):
        return False, 'Выберите рецепт и одну или три партии.'
    now = now_seconds(); ensure_base(address, now); state, levels = _read(address, now)
    recipe = RECIPES[recipe_id]; count = int(batches)
    if _active('production_jobs', address):
        return False, 'Сначала заберите готовую продукцию.'
    if levels['refinery'] < recipe['level']:
        return False, 'Для этого рецепта нужен завод второго уровня.'
    cost = {key: value * count for key, value in recipe['cost'].items()}
    if not _can_spend(state, cost):
        return False, 'Не хватает сырья. Соберите нефть или приготовьте топливо.'
    output = recipe['amount'] * count
    if storage_used(address, state)[recipe['output']] + output > capacities(levels['depot'], address)[recipe['output']]:
        return False, 'Для этой партии мало места. Потратьте запас или улучшите резервуары.'
    _spend(state, cost); _save(state)
    duration = max(10, recipe['seconds'] - 4 * (levels['refinery'] - 1)) * count
    with unit_of_work() as conn:
        conn.execute('INSERT INTO production_jobs(address,recipe,batches,output_resource,output_amount,started_at,ready_at) VALUES(?,?,?,?,?,?,?)',
                     (address, recipe_id, count, recipe['output'], output, now, now + duration))
    return True, 'Производство запущено. Можно закрыть страницу — таймер продолжит идти.'


def claim_production(address, job_id):
    now = now_seconds(); job = _active('production_jobs', address)
    if not job or str(job['id']) != job_id:
        return False, 'Эта партия уже получена или не принадлежит вам.'
    if now < job['ready_at']:
        return False, 'Партия ещё не готова.'
    state, _ = _read(address, now); state[job['output_resource']] += job['output_amount']; _save(state)
    with unit_of_work() as conn:
        conn.execute('UPDATE production_jobs SET claimed_at=? WHERE id=?', (now, job['id']))
    _event(address, f"production:{job['id']}", f"Получено: {RESOURCES[job['output_resource']]} +{job['output_amount']}", xp=5 * job['batches'], now=now)
    return True, 'Продукция на складе. Выполните заказ или подготовьте экспедицию.'


def complete_contract(address, contract_id, day):
    now = now_seconds()
    if day != calendar(now)['day']:
        return False, 'Заказы обновились. Откройте страницу заново.'
    spec = next((item for item in CONTRACTS if item['id'] == contract_id), None)
    if not spec:
        return False, 'Неизвестный заказ.'
    ensure_base(address, now); key = f'contract:{day}:{contract_id}'
    with unit_of_work(write=False) as conn:
        if conn.execute('SELECT 1 FROM farm_events WHERE address=? AND event_key=?', (address, key)).fetchone():
            return False, 'Этот заказ уже выполнен сегодня.'
    state, _ = _read(address, now)
    if not _can_spend(state, spec['cost']):
        return False, 'Не хватает ресурсов для заказа.'
    _spend(state, spec['cost']); state['parts'] += spec['parts']; _save(state)
    _event(address, key, spec['name'], spec['xp'], spec['points'], now)
    return True, f"Заказ доставлен: +{spec['parts']} деталей, +{spec['xp']} XP, +{spec['points']} очков лиги!"


def start_expedition(chain, address, sector_id, approach):
    spec = next((item for item in SECTORS if str(item['id']) == sector_id), None)
    if not spec or approach not in APPROACHES:
        return False, 'Выберите маршрут и тактику.'
    now = now_seconds(); ensure_base(address, now)
    if _active('expedition_jobs', address):
        return False, 'Отряд уже в пути. Сначала получите его отчёт.'
    with unit_of_work(write=False) as conn:
        cleared = {row[0] for row in conn.execute("SELECT event_key FROM farm_events WHERE address=? AND event_key LIKE 'sector:%'", (address,))}
    if spec['id'] > 1 and f"sector:{spec['id'] - 1}" not in cleared:
        return False, 'Сначала пройдите предыдущий сектор.'
    state, levels = _read(address, now); tactic = APPROACHES[approach]; rig, _ = rig_state(chain, address)
    power = levels['garage'] * 18 + levels['hq'] * 8 + rig * 4 + tactic['power']
    if power < spec['power']:
        return False, 'Недостаточно силы. Улучшите гараж, штаб или оборудование кошелька; попробуйте тактику «Прорыв».'
    repair = EXPEDITION_REPAIR_PER_SECTOR * spec['id']
    if state['fuel'] < tactic['fuel'] or state['parts'] < repair:
        return False, f'Для рейса нужно {tactic["fuel"]} топлива и {repair} деталей на обслуживание.'
    repeat = f"sector:{spec['id']}" in cleared
    day_key = f"sortie:{calendar(now)['day']}:{spec['id']}"
    with unit_of_work() as conn:
        already = conn.execute('SELECT 1 FROM farm_events WHERE address=? AND event_key=?', (address, day_key)).fetchone()
        reward_parts = (0 if already else spec['parts'] * tactic['loot'] // REPEAT_LOOT_DIVISOR) if repeat else spec['parts'] * tactic['loot'] // 100
        state['fuel'] -= tactic['fuel']; state['parts'] -= repair; _save(state)
        _event(address, day_key, f"Снабжение маршрута {spec['id']}: −{repair} деталей", now=now)
        conn.execute('INSERT INTO expedition_jobs(address,sector,approach,power,reward_parts,reward_crude,reward_xp,reward_points,started_at,ready_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (address, spec['id'], approach, power, reward_parts, 0 if repeat else spec['crude'],
                      5 if repeat else spec['xp'], spec['points'], now, now + 30 + spec['id'] * 10))
    return True, 'Отряд отправлен. Силы достаточно для победы; по возвращении заберите добычу.'


def claim_expedition(address, job_id):
    now = now_seconds(); job = _active('expedition_jobs', address)
    if not job or str(job['id']) != job_id:
        return False, 'Этот отчёт уже получен или не принадлежит вам.'
    if now < job['ready_at']:
        return False, 'Отряд ещё в пути.'
    state, _ = _read(address, now); state['parts'] += job['reward_parts']; state['crude'] += job['reward_crude']; _save(state)
    with unit_of_work() as conn:
        conn.execute('UPDATE expedition_jobs SET claimed_at=? WHERE id=?', (now, job['id']))
    _event(address, f"sector:{job['sector']}", f"Сектор открыт: {SECTORS[job['sector'] - 1]['name']}", now=now)
    _event(address, f"sector-week:{calendar(now)['week_start']}:{job['sector']}", f"Маршрут недели: {SECTORS[job['sector'] - 1]['name']}", points=job['reward_points'], now=now)
    _event(address, f"expedition:{job['id']}", f"Экспедиция: +{job['reward_parts']} деталей, +{job['reward_crude']} нефти", xp=job['reward_xp'], now=now)
    return True, 'Победа! Добыча на складе, новый маршрут открыт. Первый рейс по маршруту за неделю даёт очки лиги.'


def rankings(address=None, mode='week'):
    period = calendar()
    with unit_of_work(write=False) as conn:
        rows = conn.execute('''SELECT a.address,a.label,a.created_at,
          COALESCE((SELECT SUM(points) FROM farm_events f WHERE f.address=a.address AND f.created_at>=? AND f.created_at<?),0) AS points,
          COALESCE((SELECT SUM(xp) FROM game_events g WHERE g.address=a.address),0) AS xp,
          COALESCE((SELECT SUM(level * CASE kind WHEN 'hq' THEN 50 WHEN 'pump' THEN 30 WHEN 'depot' THEN 20 WHEN 'refinery' THEN 30 ELSE 40 END) FROM buildings b WHERE b.address=a.address),170) AS rating
          FROM accounts a WHERE a.is_demo=0''', (period['week_start'], period['week_end'])).fetchall()
    import colony
    rows=[dict(row,trophy=colony.profile(row['address'])['trophy']) for row in rows]
    column = {'week': 'points', 'base': 'rating', 'xp': 'xp', 'pvp':'trophy'}[mode]
    players = sorted((dict(row) for row in rows), key=lambda row: (-row[column], row['created_at'], row['address']))
    for rank, player in enumerate(players, 1):
        player['rank'] = rank
        player['league'] = max((league for league in LEAGUES if league[0] <= player['points']), key=lambda league: league[0])[1]
    return dict(players=players[:50], me=next((player for player in players if player['address'] == address), None),
                total=len(players), mode=mode, period=period)
