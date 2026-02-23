"""
Full analysis of indicator and consolidated forecast accuracy.
Compares OLD (pre-weighted) vs NEW (weighted) engine results.
Run: docker exec ibkr-trading-bot python3 /app/scripts/full_analysis.py
"""
import sys
sys.path.insert(0, '/app')

from datetime import datetime, timezone, timedelta
from typing import List, Dict
from app.storage.db import SupabaseDB

db = SupabaseDB()
now = datetime.now(timezone.utc)

# ─────────────────────────────────────────
# SECTION 1: Consolidated forecast accuracy (all verified, last 14h)
# ─────────────────────────────────────────
since = (now - timedelta(hours=14)).isoformat()

res = db.client.table('price_forecasts').select(
    'symbol, ts_utc, base_price, '
    'h30_direction, h30_correct, h30_strength, h30_confidence, h30_actual_price, h30_aligned, h30_total, '
    'h60_direction, h60_correct, h60_strength, h60_confidence, h60_actual_price, '
    'h240_direction, h240_correct, h240_strength, h240_confidence, '
    'h1440_direction, h1440_correct, '
    'dominant_direction, all_aligned'
).gte('ts_utc', since).not_.is_('verified_at', 'null').neq('h30_actual', 'weekend').limit(2000).execute()
rows = res.data or []

print(f"{'='*80}")
print(f"  CONSOLIDATED FORECAST ACCURACY (last 14h, verified)")
print(f"  Total rows: {len(rows)}")
print(f"{'='*80}")

prefixes = [('h30','30m'), ('h60','1h'), ('h240','4h'), ('h1440','24h')]
symbols = sorted(set(r['symbol'] for r in rows))

# Overall by horizon
print(f"\n--- BY HORIZON ---")
print(f"{'Horizon':<8s} | {'All':>14s} | {'High Conf':>14s} | {'Medium':>14s} | {'Low':>14s}")
print(f"{'-'*72}")
for prefix, label in prefixes:
    def stats(filt_rows, pf):
        t = sum(1 for r in filt_rows if r.get(f'{pf}_correct') is not None and r.get(f'{pf}_direction') not in (None, 'neutral'))
        c = sum(1 for r in filt_rows if r.get(f'{pf}_correct') == True and r.get(f'{pf}_direction') not in (None, 'neutral'))
        return c, t
    
    c_all, t_all = stats(rows, prefix)
    c_hi, t_hi = stats([r for r in rows if r.get(f'{prefix}_confidence') == 'high'], prefix)
    c_med, t_med = stats([r for r in rows if r.get(f'{prefix}_confidence') == 'medium'], prefix)
    c_lo, t_lo = stats([r for r in rows if r.get(f'{prefix}_confidence') == 'low'], prefix)
    
    def fmt(c, t):
        if t == 0: return '-'
        return f"{c}/{t} {int(c/t*100)}%"
    
    print(f"{label:<8s} | {fmt(c_all, t_all):>14s} | {fmt(c_hi, t_hi):>14s} | {fmt(c_med, t_med):>14s} | {fmt(c_lo, t_lo):>14s}")

# By symbol (h30 only - most data)
print(f"\n--- H30 BY SYMBOL ---")
print(f"{'Symbol':<9s} | {'Total':>12s} | {'High':>12s} | {'Medium':>12s} | {'Low':>12s} | {'Direction':>10s} | {'Aligned':>8s}")
print(f"{'-'*82}")

for sym in symbols:
    sr = [r for r in rows if r['symbol'] == sym]
    h30r = [r for r in sr if r.get('h30_correct') is not None and r.get('h30_direction') not in (None, 'neutral')]
    if not h30r:
        continue
    
    t = len(h30r)
    c = sum(1 for r in h30r if r['h30_correct'])
    
    hi = [r for r in h30r if r.get('h30_confidence') == 'high']
    med = [r for r in h30r if r.get('h30_confidence') == 'medium']
    lo = [r for r in h30r if r.get('h30_confidence') == 'low']
    
    c_hi = sum(1 for r in hi if r['h30_correct'])
    c_med = sum(1 for r in med if r['h30_correct'])
    c_lo = sum(1 for r in lo if r['h30_correct'])
    
    # Direction breakdown
    up_r = [r for r in h30r if r['h30_direction'] == 'up']
    dn_r = [r for r in h30r if r['h30_direction'] == 'down']
    up_c = sum(1 for r in up_r if r['h30_correct'])
    dn_c = sum(1 for r in dn_r if r['h30_correct'])
    
    dir_str = ""
    if up_r:
        dir_str += f"U:{up_c}/{len(up_r)}"
    if dn_r:
        dir_str += f" D:{dn_c}/{len(dn_r)}"
    
    # Aligned
    al = [r for r in h30r if r.get('all_aligned')]
    al_c = sum(1 for r in al if r['h30_correct'])
    
    def fmt(c, t):
        if t == 0: return '-'
        return f"{c}/{t} {int(c/t*100)}%"
    
    print(f"{sym:<9s} | {fmt(c,t):>12s} | {fmt(c_hi,len(hi)):>12s} | {fmt(c_med,len(med)):>12s} | {fmt(c_lo,len(lo)):>12s} | {dir_str:>10s} | {fmt(al_c,len(al)):>8s}")

# Totals
all_h30 = [r for r in rows if r.get('h30_correct') is not None and r.get('h30_direction') not in (None, 'neutral')]
t_total = len(all_h30)
c_total = sum(1 for r in all_h30 if r['h30_correct'])
print(f"{'-'*82}")
print(f"{'TOTAL':<9s} | {c_total}/{t_total} {int(c_total/t_total*100) if t_total else 0}%")

# ─────────────────────────────────────────
# SECTION 2: Strength buckets
# ─────────────────────────────────────────
print(f"\n{'='*80}")
print(f"  H30 ACCURACY BY STRENGTH BUCKET")
print(f"{'='*80}")
print(f"{'Bucket':<10s} | {'Correct':>8s} | {'Total':>6s} | {'Acc':>6s}")
print(f"{'-'*36}")

for lo, hi, label in [(0.0, 0.2, '<20%'), (0.2, 0.4, '20-40%'), (0.4, 0.6, '40-60%'), (0.6, 0.8, '60-80%'), (0.8, 1.01, '80-100%')]:
    bucket = [r for r in all_h30 if r.get('h30_strength') is not None and lo <= r['h30_strength'] < hi]
    c = sum(1 for r in bucket if r['h30_correct'])
    t = len(bucket)
    pct = f"{int(c/t*100)}%" if t > 0 else "-"
    print(f"{label:<10s} | {c:>8d} | {t:>6d} | {pct:>6s}")

# ─────────────────────────────────────────
# SECTION 3: Per-indicator accuracy (recalculated)
# ─────────────────────────────────────────
print(f"\n{'='*80}")
print(f"  INDIVIDUAL INDICATOR ACCURACY (recalculated from snapshots)")
print(f"{'='*80}")

INDICATOR_NAMES = ['ma_cross_inv', 'rsi_trend', 'rsi_momentum', 'price_vs_ma', 'momentum']
WEIGHTS = {'ma_cross_inv': 1.0, 'rsi_trend': 1.0, 'rsi_momentum': 1.0, 'price_vs_ma': 1.0, 'momentum': 2.0}

indicator_stats = {name: {'correct': 0, 'wrong': 0, 'neutral': 0} for name in INDICATOR_NAMES}
pair_indicator = {}

bars_cache = {}

def get_bars(symbol, before_ts, n=250):
    key = f"{symbol}_{before_ts[:13]}"
    if key in bars_cache:
        return bars_cache[key]
    r = db.client.table('market_snapshots').select(
        'ts, close, rsi, ma_fast, ma_slow'
    ).eq('symbol', symbol).eq('timeframe', 'M15').lte('ts', before_ts).order('ts', desc=True).limit(n).execute()
    data = list(reversed(r.data or []))
    bars_cache[key] = data
    return data


def calc_indicator(bars, name):
    """Calculate single indicator vote from snapshot data."""
    if len(bars) < 20:
        return 0
    
    closes = [float(b['close']) for b in bars if b.get('close') is not None]
    if len(closes) < 20:
        return 0
    
    latest = bars[-1]
    prev = bars[-4] if len(bars) > 3 else bars[-2] if len(bars) > 1 else None
    
    ma_fast = latest.get('ma_fast')
    ma_slow = latest.get('ma_slow')
    rsi_now = latest.get('rsi')
    rsi_prev = prev.get('rsi') if prev else None
    
    if name == 'ma_cross_inv':
        if ma_fast is not None and ma_slow is not None and ma_slow != 0:
            if ma_fast > ma_slow: return -1  # inverted
            if ma_fast < ma_slow: return 1   # inverted
        return 0
    
    elif name == 'rsi_trend':
        if rsi_now is not None and rsi_prev is not None:
            if rsi_now > 50 and rsi_now > rsi_prev: return 1
            if rsi_now < 50 and rsi_now < rsi_prev: return -1
        return 0
    
    elif name == 'rsi_momentum':
        if rsi_now is not None:
            if rsi_now <= 30: return -1  # inverted: oversold = momentum DOWN
            if rsi_now >= 70: return 1   # inverted: overbought = momentum UP
        return 0
    
    elif name == 'price_vs_ma':
        if ma_fast is not None and closes:
            price = closes[-1]
            if price > ma_fast: return 1
            if price < ma_fast: return -1
        return 0
    
    elif name == 'momentum':
        lookback = 6
        if len(closes) > lookback:
            curr = closes[-1]
            past = closes[-(lookback + 1)]
            if past != 0:
                if curr > past: return 1
                if curr < past: return -1
        return 0
    
    return 0


processed = 0

for row in all_h30:
    symbol = row['symbol']
    ts = row['ts_utc']
    direction = row['h30_direction']
    correct = row['h30_correct']
    actual_dir_sign = 1 if direction == 'up' else -1
    
    bars = get_bars(symbol, ts)
    if len(bars) < 20:
        continue
    
    processed += 1
    
    if symbol not in pair_indicator:
        pair_indicator[symbol] = {name: {'correct': 0, 'wrong': 0, 'neutral': 0} for name in INDICATOR_NAMES}
    
    for name in INDICATOR_NAMES:
        vote = calc_indicator(bars, name)
        
        if vote == 0:
            indicator_stats[name]['neutral'] += 1
            pair_indicator[symbol][name]['neutral'] += 1
            continue
        
        # Does this indicator's vote match the ACTUAL price movement?
        # We need to determine actual direction from base_price vs actual_price
        bp = row.get('base_price')
        ap = row.get('h30_actual_price')
        if bp and ap:
            actual_sign = 1 if ap > bp else -1
            if vote == actual_sign:
                indicator_stats[name]['correct'] += 1
                pair_indicator[symbol][name]['correct'] += 1
            else:
                indicator_stats[name]['wrong'] += 1
                pair_indicator[symbol][name]['wrong'] += 1
        else:
            indicator_stats[name]['neutral'] += 1
            pair_indicator[symbol][name]['neutral'] += 1

print(f"Processed: {processed} forecasts")
print()

# Individual indicator accuracy (does indicator predict actual direction?)
print(f"{'Indicator':<15s} | {'Weight':>6s} | {'Correct':>8s} | {'Wrong':>6s} | {'Acc':>6s} | {'Neutral':>8s} | {'Active%':>8s}")
print(f"{'-'*68}")

for name in INDICATOR_NAMES:
    s = indicator_stats[name]
    c = s['correct']
    w = s['wrong']
    n = s['neutral']
    total = c + w
    acc = f"{int(c/total*100)}%" if total > 0 else "-"
    active_pct = f"{int(total/(total+n)*100)}%" if (total+n) > 0 else "-"
    weight = WEIGHTS[name]
    print(f"{name:<15s} | {weight:>6.1f} | {c:>8d} | {w:>6d} | {acc:>6s} | {n:>8d} | {active_pct:>8s}")

# Simulated weighted vote accuracy
print(f"\n{'='*80}")
print(f"  SIMULATED WEIGHTED VOTE vs ACTUAL DIRECTION")
print(f"{'='*80}")

sim_correct = 0
sim_wrong = 0
sim_neutral = 0

for row in all_h30:
    symbol = row['symbol']
    ts = row['ts_utc']
    bp = row.get('base_price')
    ap = row.get('h30_actual_price')
    
    if not bp or not ap:
        continue
    
    actual_sign = 1 if ap > bp else -1
    
    bars = get_bars(symbol, ts)
    if len(bars) < 20:
        continue
    
    # Calculate weighted vote
    weighted_sum = 0
    total_weight = 0
    for name in INDICATOR_NAMES:
        vote = calc_indicator(bars, name)
        if vote != 0:
            w = WEIGHTS[name]
            weighted_sum += vote * w
            total_weight += w
    
    if total_weight == 0:
        sim_neutral += 1
        continue
    
    predicted_sign = 1 if weighted_sum > 0 else -1
    
    if predicted_sign == actual_sign:
        sim_correct += 1
    else:
        sim_wrong += 1

sim_total = sim_correct + sim_wrong
sim_acc = f"{int(sim_correct/sim_total*100)}%" if sim_total > 0 else "-"
print(f"Weighted vote accuracy: {sim_correct}/{sim_total} ({sim_acc})")
print(f"Neutral (no active indicators): {sim_neutral}")

# Compare: what if we used ONLY momentum + price_vs_ma?
print(f"\n--- ALTERNATIVE: Only momentum(2x) + price_vs_ma(1x) ---")
alt_correct = 0
alt_wrong = 0
alt_neutral = 0

for row in all_h30:
    bp = row.get('base_price')
    ap = row.get('h30_actual_price')
    if not bp or not ap:
        continue
    actual_sign = 1 if ap > bp else -1
    bars = get_bars(row['symbol'], row['ts_utc'])
    if len(bars) < 20:
        continue
    
    mom = calc_indicator(bars, 'momentum')
    pvm = calc_indicator(bars, 'price_vs_ma')
    
    w_sum = mom * 2.0 + pvm * 1.0
    if w_sum == 0:
        alt_neutral += 1
        continue
    
    pred = 1 if w_sum > 0 else -1
    if pred == actual_sign:
        alt_correct += 1
    else:
        alt_wrong += 1

alt_total = alt_correct + alt_wrong
alt_acc = f"{int(alt_correct/alt_total*100)}%" if alt_total > 0 else "-"
print(f"Accuracy: {alt_correct}/{alt_total} ({alt_acc})")
print(f"Neutral: {alt_neutral}")

# Compare: only momentum
print(f"\n--- ALTERNATIVE: Only momentum ---")
mom_correct = 0
mom_wrong = 0
mom_neutral = 0

for row in all_h30:
    bp = row.get('base_price')
    ap = row.get('h30_actual_price')
    if not bp or not ap:
        continue
    actual_sign = 1 if ap > bp else -1
    bars = get_bars(row['symbol'], row['ts_utc'])
    if len(bars) < 20:
        continue
    
    mom = calc_indicator(bars, 'momentum')
    if mom == 0:
        mom_neutral += 1
        continue
    
    if mom == actual_sign:
        mom_correct += 1
    else:
        mom_wrong += 1

mom_total = mom_correct + mom_wrong
mom_acc = f"{int(mom_correct/mom_total*100)}%" if mom_total > 0 else "-"
print(f"Accuracy: {mom_correct}/{mom_total} ({mom_acc})")
print(f"Neutral: {mom_neutral}")

print(f"\n{'='*80}")
print(f"  TOP PAIRS: PER-INDICATOR BREAKDOWN")
print(f"{'='*80}")

top = ['GBPJPY', 'CADJPY', 'CHFJPY', 'NZDUSD', 'USDCAD', 'EURGBP']
for sym in top:
    if sym not in pair_indicator:
        continue
    sr = [r for r in all_h30 if r['symbol'] == sym]
    t = len(sr)
    c = sum(1 for r in sr if r['h30_correct'])
    pct = f"{int(c/t*100)}%" if t > 0 else "-"
    
    print(f"\n  {sym} (forecast: {c}/{t} = {pct})")
    print(f"  {'Indicator':<15s} | {'Correct':>8s} | {'Wrong':>6s} | {'Acc':>6s} | {'Neutral':>8s}")
    print(f"  {'-'*52}")
    for name in INDICATOR_NAMES:
        s = pair_indicator[sym][name]
        cc = s['correct']
        ww = s['wrong']
        nn = s['neutral']
        tt = cc + ww
        acc = f"{int(cc/tt*100)}%" if tt > 0 else "-"
        print(f"  {name:<15s} | {cc:>8d} | {ww:>6d} | {acc:>6s} | {nn:>8d}")

# ─────────────────────────────────────────
# SECTION: Simulate NEW weights (ma_cross_inv=2, momentum=0.5)
# ─────────────────────────────────────────
print(f"\n{'='*80}")
print(f"  SIMULATED: NEW WEIGHTS (ma_cross_inv=2.0, price_vs_ma=1.0, rest=0.5)")
print(f"{'='*80}")

NEW_WEIGHTS = {'ma_cross_inv': 2.0, 'price_vs_ma': 1.0, 'momentum': 0.5, 'rsi_momentum': 0.5, 'rsi_trend': 0.5}

new_correct = 0
new_wrong = 0
new_neutral = 0
new_per_pair = {}

for row in all_h30:
    symbol = row['symbol']
    bp = row.get('base_price')
    ap = row.get('h30_actual_price')
    if not bp or not ap:
        continue
    actual_sign = 1 if ap > bp else -1
    
    bars = get_bars(symbol, row['ts_utc'])
    if len(bars) < 20:
        continue
    
    w_sum = 0
    t_weight = 0
    for name in INDICATOR_NAMES:
        vote = calc_indicator(bars, name)
        if vote != 0:
            w = NEW_WEIGHTS[name]
            w_sum += vote * w
            t_weight += w
    
    if t_weight == 0:
        new_neutral += 1
        continue
    
    pred = 1 if w_sum > 0 else -1
    if symbol not in new_per_pair:
        new_per_pair[symbol] = {'c': 0, 'w': 0}
    
    if pred == actual_sign:
        new_correct += 1
        new_per_pair[symbol]['c'] += 1
    else:
        new_wrong += 1
    new_per_pair[symbol]['w'] = new_per_pair[symbol].get('w', 0) + (0 if pred == actual_sign else 1)

new_total = new_correct + new_wrong
new_acc = f"{int(new_correct/new_total*100)}%" if new_total > 0 else "-"
old_acc = f"61%"  # from current weighted vote
print(f"NEW weights: {new_correct}/{new_total} ({new_acc}) vs OLD weights: 251/407 (61%)")
print(f"Neutral: {new_neutral}")

print(f"\n  {'Symbol':<9s} | {'New Acc':>10s} | {'Samples':>8s}")
print(f"  {'-'*34}")
for sym in sorted(new_per_pair.keys()):
    p = new_per_pair[sym]
    t = p['c'] + p['w']
    a = f"{int(p['c']/t*100)}%" if t > 0 else '-'
    print(f"  {sym:<9s} | {p['c']}/{t} {a:>5s} | {t:>8d}")

print(f"\nDone. Timestamp: {now.isoformat()[:19]}")
