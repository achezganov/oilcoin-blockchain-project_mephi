"""Money-supply-indexed signed chest purchases; delivery is atomic with the block."""
import json
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import current_app
from blockchain import to_units, from_units, ATOMIC_UNITS
from accounts import decrypt_private_key
from database import unit_of_work
from game import TREASURY_ADDRESS
import district
import market

SPECS={
 'scout':dict(name='Сундук разведчика',cost={'crude':120,'parts':40},draws=3,base_oil=5,guarantee='Любая редкость'),
 'captain':dict(name='Сундук капитана',cost={},draws=5,base_oil=25,guarantee='Одна редкая карта или выше'),
 'admiral':dict(name='Сундук адмирала',cost={},draws=8,base_oil=60,guarantee='Одна эпическая карта или выше'),
}

def economy(chain):
    # Treasury funds are locked game sinks, not spendable player money. Pending
    # transactions don't change the confirmed index. Every player sees one price.
    liquid=max(0,to_units(chain.calculate_circulating_supply())-to_units(chain.get_balance(TREASURY_ADDRESS)))
    basis_points=min(100000,10000+liquid//(10*ATOMIC_UNITS))
    return dict(liquid=market.money(liquid),basis_points=basis_points,multiplier=f'{basis_points/10000:.4f}')


def serializer():return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],salt='chest-quote-v1')


def quotes(chain,address):
    index=economy(chain);result={}
    for key,spec in SPECS.items():
        price=(spec['base_oil']*ATOMIC_UNITS*index['basis_points']+9999)//10000;fee=market.fee(price)
        result[key]=dict(spec,price=market.money(price),fee=market.money(fee),total=market.money(price+fee),
                         quote=serializer().dumps([address,key,price,fee]))
    return result,index


def valid_request(value):return isinstance(value,str) and 8<=len(value)<=64


def used(conn,address,request_id):
    return conn.execute('SELECT 1 FROM chest_events WHERE address=? AND request_id=? UNION ALL SELECT 1 FROM chest_orders WHERE address=? AND request_id=?',(address,request_id,address,request_id)).fetchone()


def rewards(kind):
    import arena
    spec=SPECS[kind]
    cards=[arena.draw(kind=='captain' and i==0) for i in range(spec['draws'])]
    if kind=='admiral':
        rarity='epic' if arena.secrets.randbelow(100)<90 else 'legendary'
        cards[0]=arena.secrets.choice([c['id'] for c in arena.CARDS if c['rarity']==rarity])
    return cards


def deliver(conn,address,kind,request_id):
    import arena
    state,_=district._read(address,district.now_seconds());cards=rewards(kind)
    for card in cards:arena.add_card(state['_colony'],card)
    district._save(state)
    conn.execute('INSERT INTO chest_events(address,kind,payload,created_at,request_id) VALUES(?,?,?,?,?)',(address,kind,json.dumps(cards),district.now_seconds(),request_id))
    return cards


def buy(chain,account,kind,request_id,quote):
    address=account['address']
    if kind not in SPECS or not valid_request(request_id):return False,'Некорректный сундук или ключ запроса.'
    with unit_of_work() as conn:
        if used(conn,address,request_id):return False,'Заказ уже принят. Повторного списания нет.'
        try:
            owner,quoted_kind,price,fee=serializer().loads(quote,max_age=300)
            if owner!=address or quoted_kind!=kind or type(price) is not int or type(fee) is not int or price<=0 or fee!=market.fee(price):raise ValueError
        except (BadSignature,SignatureExpired,ValueError,TypeError):return False,'Цена устарела или неверна. Обновите страницу: до подтверждения ничего не списано.'
        ok,_=chain.add_transaction(address,TREASURY_ADDRESS,from_units(price),from_units(fee),private_key=decrypt_private_key(account))
        if not ok:return False,'Не хватает доступных OIL на сундук и комиссию. Учтите ожидающие переводы.'
        conn.execute("INSERT INTO chest_orders(address,kind,price_units,fee_units,tx_id,request_id,status,created_at) VALUES(?,?,?,?,?,?,'pending',?)",(address,kind,price,fee,chain.pending_transactions[-1]['tx_id'],request_id,district.now_seconds()))
    return True,'Оплата подписана. Сундук откроется автоматически после блока; карты появятся в коллекции.'


def payment_matches(row,tx):
    return bool(tx and tx['type']=='transfer' and tx['sender']==row['address'] and tx['recipient']==TREASURY_ADDRESS and to_units(tx['amount'])==row['price_units'] and to_units(tx['fee'])==row['fee_units'])


def settle(chain):
    confirmed={tx['tx_id']:tx for block in chain.chain for tx in block.transactions}
    with unit_of_work() as conn:
        for row in conn.execute("SELECT * FROM chest_orders WHERE status='pending'").fetchall():
            tx=confirmed.get(row['tx_id'])
            if tx is None:continue
            if not payment_matches(row,tx):raise ValueError('Chest payment does not match its order')
            deliver(conn,row['address'],row['kind'],row['request_id'])
            conn.execute("UPDATE chest_orders SET status='opened',opened_at=? WHERE id=?",(district.now_seconds(),row['id']))
