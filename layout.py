"""Persistent 8x8 layouts. Call mutations inside the request unit of work."""
import json
from collections import Counter

from database import unit_of_work
import district

SIZE = 8
DECORATIONS = {'road': dict(name='Дорожка', cost=2, symbol='▪'),
               'tree': dict(name='Дерево', cost=8, symbol='♠'),
               'monument': dict(name='Монумент', cost=30, symbol='◆')}
DEFAULT = [dict(kind=kind, x=x, y=y) for kind, x, y in
           [('hq', 3, 0), ('pump', 0, 3), ('depot', 6, 1), ('refinery', 3, 4), ('garage', 6, 5)]]


def read(address, include_colony=True):
    with unit_of_work(write=False) as conn:
        row = conn.execute('SELECT revision,payload FROM base_layouts WHERE address=?', (address,)).fetchone()
    import colony
    data=colony.profile(address) if include_colony else None
    return dict(size=data['size'] if data else SIZE, inventory=data['inventory'] if data else {}, shop=colony.SHOP, revision=row['revision'] if row else 0,
                objects=json.loads(row['payload']) if row else [dict(item) for item in DEFAULT], decorations=DECORATIONS)


def save(address, payload, revision):
    import colony
    data=colony.profile(address);size=data['size']
    try:
        if not isinstance(payload, str) or len(payload) > 14000:
            raise ValueError
        objects = json.loads(payload)
        if not isinstance(objects, list) or not 5 <= len(objects) <= size*size:
            raise ValueError
        occupied = set(); counts = Counter()
        for item in objects:
            if not isinstance(item, dict) or set(item) != {'kind', 'x', 'y'}:
                raise ValueError
            kind, x, y = item['kind'], item['x'], item['y']
            if not isinstance(kind, str) or kind not in {*district.BUILDINGS, *DECORATIONS, *colony.SHOP}:
                raise ValueError
            width = 2 if kind in district.BUILDINGS or kind in colony.SHOP and colony.SHOP[kind]['size']==2 else 1
            if type(x) is not int or type(y) is not int or not (0 <= x <= size-width and 0 <= y <= size-width):
                raise ValueError
            cells = {(a, b) for a in range(x, x+width) for b in range(y, y+width)}
            if cells & occupied:
                return False, 'Объекты пересекаются. Освободите клетки и сохраните снова.'
            occupied.update(cells); counts[kind] += 1
        if any(counts[kind] > data['inventory'].get(kind,0) for kind in colony.SHOP):
            return False,'Сначала купите объекты или трубы в магазине.'
        if any(counts[kind] != 1 for kind in district.BUILDINGS):
            raise ValueError
    except (TypeError, ValueError, RecursionError):
        return False, 'Нужны все пять зданий без пересечений внутри вашей территории.'
    old = read(address)
    if str(old['revision']) != revision:
        return False, 'Планировка уже изменена в другой вкладке. Обновите страницу.'
    previous = Counter(item['kind'] for item in old['objects'])
    cost = sum(max(0, counts[kind] - previous[kind]) * spec['cost'] for kind, spec in DECORATIONS.items())
    district.ensure_base(address)
    state, _ = district._read(address, district.now_seconds())
    if state['parts'] < cost:
        return False, f'Для нового декора нужно {cost} деталей. Перемещение бесплатно.'
    state['parts'] -= cost; district._save(state)
    with unit_of_work() as conn:
        conn.execute('INSERT INTO base_layouts VALUES(?,?,?) ON CONFLICT(address) DO UPDATE SET revision=excluded.revision,payload=excluded.payload',
                     (address, old['revision']+1, json.dumps(objects)))
    return True, f'Планировка сохранена. Новый декор: {cost} деталей. Удаление без возврата.'
