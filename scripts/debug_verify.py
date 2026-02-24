"""Debug script to check verification quality."""
from app.storage.db import SupabaseDB

db = SupabaseDB()

for sym in ['CHFJPY', 'AUDUSD', 'NZDUSD', 'EURUSD', 'GBPUSD']:
    print(f'=== {sym} ===')
    res = db.client.table('price_forecasts').select(
        'ts_utc, base_price, h30_direction, h30_correct, h30_actual_price, '
        'h60_direction, h60_correct, h60_actual_price, '
        'h240_direction, h240_correct, h240_actual_price, '
        'h1440_direction, h1440_correct, h1440_actual_price'
    ).not_.is_('verified_at', 'null').neq('h30_actual', 'weekend').eq(
        'symbol', sym
    ).order('ts_utc', desc=True).limit(5).execute()
    
    for r in res.data or []:
        bp = r.get('base_price')
        print(f'  ts={r["ts_utc"][:16]} base={bp}')
        for p, l in [('h30','30m'),('h60','1h'),('h240','4h'),('h1440','24h')]:
            ap = r.get(f'{p}_actual_price')
            d = r.get(f'{p}_direction')
            c = r.get(f'{p}_correct')
            diff = round(ap - bp, 6) if ap and bp else None
            print(f'    {l}: dir={d:8s} actual={ap} diff={diff} correct={c}')
    print()

# Check: how many have h30_actual_price == h60_actual_price == h240_actual_price
print('=== SAME-PRICE CHECK (should be 0 after reset) ===')
res2 = db.client.table('price_forecasts').select(
    'id, symbol, h30_actual_price, h60_actual_price, h240_actual_price'
).not_.is_('verified_at', 'null').neq('h30_actual', 'weekend').limit(1000).execute()
same_count = 0
for r in res2.data or []:
    prices = [r.get('h30_actual_price'), r.get('h60_actual_price'), r.get('h240_actual_price')]
    non_null = [p for p in prices if p is not None]
    if len(non_null) >= 2 and len(set(non_null)) == 1:
        same_count += 1
        if same_count <= 3:
            print(f'  SAME: {r["symbol"]} h30={prices[0]} h60={prices[1]} h240={prices[2]}')
print(f'Total with same price across horizons: {same_count}/1000')
