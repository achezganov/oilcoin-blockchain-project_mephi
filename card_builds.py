"""Explicit, deterministic talent trees shared by PvP and PvE snapshots."""

def node(name, text, **stats):
    return dict(name=name, text=text, stats=stats)


TREES = {
 0: [node('Диверсант','+4 урона',attack=4), node('Полевой инженер','+30 здоровья',hp=30)],
 1: [node('Бастион','−2 входящего урона за удар',armor=2), node('Авангард','+1 скорости',speed=1)],
 2: [node('Снайпер','+6 дальности',range=6), node('Охотник','+5 урона',attack=5)],
 3: [node('Хирург','+5 лечения за такт',heal=5), node('Боевой медик','+9 урона',attack=9)],
 4: [node('Пиромант','+5 радиуса взрыва',splash=5), node('Дуэлянт','+9 урона',attack=9)],
 5: [node('Осадница','+14 урона башням',siege=14), node('Артиллерист','+7 дальности',range=7)],
 6: [node('Вечная мерзлота','+2 секунды замедления',slow=2), node('Ледяное копьё','+7 урона',attack=7)],
 7: [node('Флагман','+70 здоровья',hp=70), node('Шторм','+1 скорости',speed=1)],
}
# A level-2 specialization forks again at level 3. Leaves are mutually exclusive.
for card, branches in TREES.items():
    for index, branch in enumerate(branches):
        primary = next(iter(branch['stats']))
        amount = next(iter(branch['stats'].values()))
        branch['leaves'] = [node('Мастерство', 'Ещё '+branch['text'].lower(), **{primary: amount}),
                            node('Стойкость' if index==0 else 'Натиск',
                                 '+25 здоровья' if index==0 else '+4 урона', **({'hp':25} if index==0 else {'attack':4}))]


def stats(card, progress):
    level=progress.get('level',1)
    result=dict(hp=card['hp']*(9+level)//10,attack=card['attack']*(9+level)//10,
                range=card['range'],speed=card['speed'],armor=0,
                heal=7 if card['id']==3 else 0,splash=8 if card['id']==4 else 0,
                slow=2 if card['id']==6 else 0,siege=8 if card['id']==5 else 0)
    branch=progress.get('branch');leaf=progress.get('leaf')
    if level>=2 and branch in (0,1):
        nodes=[TREES[card['id']][branch]]
        if level>=3 and leaf in (0,1):nodes.append(nodes[0]['leaves'][leaf])
        for item in nodes:
            for key,value in item['stats'].items():result[key]+=value
    return result


def choose(address,card_id,branch,leaf):
    import district
    try:
        card_id=str(int(card_id));branch=int(branch);leaf=int(leaf) if leaf not in (None,'') else None
    except (ValueError,TypeError):return False,'Выберите специализацию и талант.'
    if branch not in (0,1) or leaf not in (None,0,1):return False,'Неизвестный талант.'
    state,_=district._read(address,district.now_seconds());item=state['_colony']['cards'].get(card_id)
    if not item or item['level']<2:return False,'Специализация открывается на уровне 2.'
    if leaf is not None and item['level']<3:return False,'Финальный талант открывается на уровне 3.'
    item.update(branch=branch,leaf=leaf);district._save(state)
    return True,'Билд сохранён. Изменения действуют со следующего боя. Смена бесплатна.'
