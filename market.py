"""Player sell orders: off-chain goods escrow, signed OIL payment, block delivery.

All mutations join the HTTP BEGIN IMMEDIATE transaction, including ledger save.
Pending orders cannot expire or be cancelled: signed payments are irrevocable.
"""
from decimal import Decimal
from accounts import decrypt_private_key
from blockchain import to_units, from_units, ATOMIC_UNITS
from database import unit_of_work
import district

MIN_PRICE = ATOMIC_UNITS // 100
MAX_PRICE = 10_000 * ATOMIC_UNITS


def money(units):
    return format(Decimal(units) / ATOMIC_UNITS, '.8f').rstrip('0').rstrip('.')


def fee(price):
    return max(MIN_PRICE, (price * 5 + 99) // 100)


def payment_matches(row, tx):
    return (tx is not None and tx['type'] == 'transfer' and tx['sender'] == row['buyer']
            and tx['recipient'] == row['seller'] and to_units(tx['amount']) == row['price_units']
            and to_units(tx['fee']) == row['fee_units'])


def unlocked(address):
    _, levels = district._read(address, district.now_seconds())
    return levels['hq'] >= 2


def held(address):
    """Outgoing escrow and paid incoming deliveries count toward storage limits."""
    with unit_of_work(write=False) as conn:
        rows = conn.execute('''SELECT resource,SUM(quantity) AS amount FROM market_orders WHERE
          (seller=? AND status IN ('open','pending')) OR (buyer=? AND status='pending') GROUP BY resource''',
                            (address, address)).fetchall()
    return {row['resource']: row['amount'] for row in rows}


def create(address, resource, quantity, price):
    if not unlocked(address):
        return False, 'Рынок открывается после улучшения штаба до уровня 2.'
    try:
        if resource not in district.RESOURCES or not isinstance(quantity, str) or not quantity.isascii() or not quantity.isdecimal() or len(quantity) > 4:
            raise ValueError
        amount = int(quantity); units = to_units(price)
        if not 1 <= amount <= 1000 or not MIN_PRICE <= units <= MAX_PRICE:
            raise ValueError
    except (ValueError, TypeError):
        return False, 'Лот: 1–1000 единиц ресурса; цена всего лота от 0.01 до 10 000 OIL, до 8 знаков после точки.'
    now = district.now_seconds(); district.ensure_base(address, now)
    with unit_of_work() as conn:
        active = conn.execute("SELECT COUNT(*) FROM market_orders WHERE seller=? AND status IN ('open','pending')", (address,)).fetchone()[0]
        if active >= 5:
            return False, 'Максимум пять активных лотов. Снимите старые или дождитесь подтверждения.'
        state, _ = district._read(address, now)
        if state[resource] < amount:
            return False, 'Недостаточно ресурса на складе.'
        state[resource] -= amount; district._save(state)
        conn.execute('''INSERT INTO market_orders(seller,resource,quantity,price_units,fee_units,status,created_at,expires_at)
                     VALUES(?,?,?,?,?,'open',?,?)''', (address, resource, amount, units, fee(units), now, now+86400))
    return True, 'Лот выставлен на 24 часа. Товар зарезервирован; OIL поступят после покупки и блока.'


def cancel(address, order_id):
    with unit_of_work() as conn:
        row = conn.execute("SELECT * FROM market_orders WHERE id=? AND seller=? AND status='open'", (order_id, address)).fetchone()
        if row is None:
            return False, 'Можно снять только собственный лот, который ещё не оплачен.'
        state, _ = district._read(address, district.now_seconds())
        state[row['resource']] += row['quantity']; district._save(state)
        conn.execute("UPDATE market_orders SET status='cancelled' WHERE id=?", (row['id'],))
    return True, 'Лот снят. Весь товар возвращён на склад.'


def buy(chain, account, order_id):
    address = account['address']; now = district.now_seconds()
    if not unlocked(address):
        return False, 'Для торговли нужен штаб 2.'
    with unit_of_work() as conn:
        row = conn.execute("SELECT * FROM market_orders WHERE id=? AND status='open' AND expires_at>?", (order_id, now)).fetchone()
        if not row:
            return False, 'Лот уже куплен, снят или истёк. Выберите другой.'
        if row['seller'] == address:
            return False, 'Нельзя купить собственный лот.'
        state, levels = district._read(address, now)
        resource = row['resource']
        if state[resource] + held(address).get(resource, 0) + row['quantity'] > district.capacities(levels['depot'], address)[resource]:
            return False, 'Мало места на складе с учётом выставленных и ожидающих товаров.'
        ok, _ = chain.add_transaction(address, row['seller'], from_units(row['price_units']), from_units(row['fee_units']),
                                      private_key=decrypt_private_key(account))
        if not ok:
            return False, 'Недостаточно доступных OIL для цены и комиссии. Учтите ожидающие переводы.'
        conn.execute("UPDATE market_orders SET status='pending',buyer=?,tx_id=? WHERE id=?",
                     (address, chain.pending_transactions[-1]['tx_id'], row['id']))
    return True, 'Покупка подписана. Добудьте блок: продавец получит OIL, вы — товар. Отмена после подписи недоступна.'


def settle(chain):
    confirmed = {tx['tx_id']: tx for block in chain.chain for tx in block.transactions}
    with unit_of_work() as conn:
        for row in conn.execute("SELECT * FROM market_orders WHERE status='pending'").fetchall():
            tx = confirmed.get(row['tx_id'])
            if tx is None:
                continue
            if not payment_matches(row, tx):
                raise ValueError('Market payment does not match reserved goods')
            state, _ = district._read(row['buyer'], district.now_seconds())
            state[row['resource']] += row['quantity']; district._save(state)
            conn.execute("UPDATE market_orders SET status='filled',filled_at=? WHERE id=?", (district.now_seconds(), row['id']))


def view(chain, address, resource='', page=1):
    now = district.now_seconds(); period = now - 7*86400
    with unit_of_work(write=False) as conn:
        where = "m.status='open' AND m.expires_at>? AND (?='' OR m.resource=?)"
        params = (now, resource, resource)
        total = conn.execute('SELECT COUNT(*) FROM market_orders m WHERE '+where, params).fetchone()[0]
        page = min(max(1, page), max(1, (total+19)//20))
        orders = conn.execute('SELECT m.*,a.label FROM market_orders m JOIN accounts a ON a.address=m.seller WHERE '+where+
                             ' ORDER BY m.created_at DESC,m.id DESC LIMIT 20 OFFSET ?', (*params, (page-1)*20)).fetchall()
        mine = conn.execute("SELECT * FROM market_orders WHERE seller=? OR buyer=? ORDER BY CASE WHEN status IN ('open','pending') THEN 0 ELSE 1 END,id DESC LIMIT 50", (address, address)).fetchall()
        stats = conn.execute("SELECT resource,COUNT(*) AS trades,SUM(quantity) AS quantity,SUM(price_units) AS volume,SUM(fee_units/2) AS burned FROM market_orders WHERE status='filled' AND filled_at>=? GROUP BY resource", (period,)).fetchall()
    def display(row):
        item = dict(row)
        item.update(price=money(row['price_units']), fee=money(row['fee_units']), total=money(row['price_units']+row['fee_units']),
                    expired=row['expires_at'] <= now, unit_price=money(row['price_units']//row['quantity']))
        return item
    state, _ = district._read(address, now)
    return dict(orders=[display(row) for row in orders], mine=[display(row) for row in mine], stock={key:state[key] for key in district.RESOURCES},
                stats=[dict(row, average=money(row['volume']//row['quantity']), oil=money(row['volume']), burn=money(row['burned'])) for row in stats],
                unlocked=unlocked(address), available=money(to_units(chain.get_balance(address))-to_units(chain.get_reserved_pending(address))),
                resources=district.RESOURCES, resource=resource, page=page, pages=max(1,(total+19)//20))
