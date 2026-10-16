#!/usr/bin/env python3
"""Deterministic flow bounds, not a forecast of player prices or demand."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from district import APPROACHES, CONTRACTS, SECTORS, DAILY_CRUDE_PER_LEVEL, EXPEDITION_REPAIR_PER_SECTOR, REPEAT_LOOT_DIVISOR
from market import fee, money
from colony import SHOP
from engine import BLOCK_INTERVAL


def report():
    max_power = 5*18 + 5*8 + 5*4
    daily_parts = daily_fuel = repairs = 0
    for sector in SECTORS:
        tactic = min((a for a in APPROACHES.values() if max_power+a['power'] >= sector['power']), key=lambda a: a['fuel'])
        daily_parts += sector['parts']*tactic['loot']//REPEAT_LOOT_DIVISOR
        daily_fuel += tactic['fuel']; repairs += EXPEDITION_REPAIR_PER_SECTOR*sector['id']
    extra=SHOP['pump_station']['limit']
    max_crude=DAILY_CRUDE_PER_LEVEL*(5+extra)*5//4
    automatic=dict(block_interval_seconds=BLOCK_INTERVAL,blocks_per_day=86400//BLOCK_INTERVAL,
                   oil_per_day_before_cap=86400//BLOCK_INTERVAL*50,
                   max_crude_per_day=max_crude,max_crude_per_minute=(5+2*extra)*6*1.25,
                   all_fuel_max=max_crude*42//40,all_parts_max=max_crude*98//60,
                   pvp_wins_per_day=5,pvp_max_parts_per_day=400,pvp_max_fuel_per_day=100,pvp_max_cards_per_day=5)
    return dict(islands=automatic,legacy_campaign_note='Other fields below describe the retained v3 campaign',daily_crude=[DAILY_CRUDE_PER_LEVEL*level for level in range(1,6)],
                campaign_crude=sum(s['crude'] for s in SECTORS), repeat_crude=0,
                contract_net_parts=sum(c['parts']-c['cost'].get('parts',0) for c in CONTRACTS),
                daily_route_parts=daily_parts, daily_route_repairs=repairs,
                daily_route_net_parts=daily_parts-repairs, daily_route_fuel=daily_fuel,
                fee_for_100_oil=money(fee(100*10**8)), burn_for_100_oil=money(fee(100*10**8)//2))


if __name__ == '__main__':
    import json
    print(json.dumps(report(), ensure_ascii=False, indent=2))
