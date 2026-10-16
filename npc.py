"""A finite NPC merchant wallet. Quotes are bounded, trades use the same ledger."""
import hashlib
import json
import secrets
from accounts import create_wallet_account, find_wallet_account
from blockchain import to_units
from database import unit_of_work
import district
import market


def account():
    with unit_of_work(write=False) as conn:
        row=conn.execute("SELECT payload FROM runtime_state WHERE key='merchant'").fetchone()
    return find_wallet_account(json.loads(row[0])) if row else None


def ensure():
    existing=account()
    if existing:return existing
    merchant=create_wallet_account('Караван «Меридиан» · NPC',secrets.token_urlsafe(40),is_demo=True)
    district.ensure_base(merchant['address'])
    with unit_of_work() as conn:
        conn.execute('UPDATE estates SET crude=600,fuel=180,parts=400,ready_crude=0 WHERE address=?',(merchant['address'],))
        conn.execute("UPDATE buildings SET level=2 WHERE address=? AND kind IN ('hq','depot')",(merchant['address'],))
        conn.execute("INSERT INTO runtime_state VALUES('merchant',?)",(json.dumps(merchant['address']),))
    return merchant


def offers(chain,address):
    now=district.now_seconds();slot=now//3600
    merchant=account();active=slot%2==0 and now%3600<1200
    next_at=slot*3600+1200 if active else (slot+2-slot%2)*3600
    if not merchant:return dict(active=False,next_at=next_at,offers=[],label='Караван ещё не прибыл')
    wallet=to_units(chain.get_balance(merchant['address']))-to_units(chain.get_reserved_pending(merchant['address']))
    state,_=district._read(merchant['address'],now)
    result=[]
    with unit_of_work(write=False) as conn:
        used={row[0] for row in conn.execute('SELECT offer FROM npc_deals WHERE address=? AND slot=?',(address,slot))}
        for index,(resource,baseline,quantity) in enumerate([('crude',3_000_000,40),('fuel',7_000_000,20),('parts',9_000_000,30)]):
            recent=conn.execute("SELECT COUNT(*),COUNT(DISTINCT seller),SUM(price_units),SUM(quantity) FROM market_orders WHERE status='filled' AND resource=? AND filled_at>=? AND seller!=? AND buyer!=?",(resource,now-7*86400,merchant['address'],merchant['address'])).fetchone()
            reference=max(baseline*80//100,min(baseline*120//100,recent[2]//recent[3])) if recent[0]>=5 and recent[1]>=3 else baseline
            seed=int(hashlib.sha256(f'{slot}:{resource}'.encode()).hexdigest()[:8],16)
            factor=[85,95,105,115,125][seed%5];direction='sell' if (seed//5)%2==0 else 'buy'
            price=max(market.MIN_PRICE,reference*factor*quantity//100)
            available=state[resource]>=quantity if direction=='sell' else wallet>=price+market.fee(price)
            result.append(dict(id=index,resource=resource,quantity=quantity,direction=direction,price=market.money(price),
                               total=market.money(price+market.fee(price)),price_units=price,factor=factor,reference=market.money(reference),
                               available=available and index not in used,used=index in used))
    return dict(active=active,next_at=next_at,slot=slot,offers=result,label=merchant['label'],balance=market.money(wallet))


def trade(chain,player,slot,offer_id):
    quote=offers(chain,player['address'])
    if not quote['active'] or str(quote['slot'])!=slot:return False,'Караван уехал или цена обновилась.'
    offer=next((o for o in quote['offers'] if str(o['id'])==offer_id),None)
    if not offer or not offer['available']:return False,'Предложение исчерпано или у каравана недостаточно резервов.'
    merchant=account();seller,buyer=(merchant,player) if offer['direction']=='sell' else (player,merchant)
    with unit_of_work() as conn:
        conn.execute('SAVEPOINT npc_trade')
        ok,message=market.create(seller['address'],offer['resource'],str(offer['quantity']),offer['price'])
        if ok:
            order_id=conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            ok,message=market.buy(chain,buyer,str(order_id))
        if not ok:conn.execute('ROLLBACK TO npc_trade')
        else:conn.execute('INSERT INTO npc_deals VALUES(?,?,?)',(player['address'],quote['slot'],offer['id']))
        conn.execute('RELEASE npc_trade')
    return ok,message
