"""
Analyze individual indicator accuracy for verified forecasts.
Uses available market_snapshots columns: close, atr, rsi, ma_fast, ma_slow.
Run inside container: python3 /app/scripts/analyze_indicators.py
"""
import sys
sys.path.insert(0, '/app')

from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from app.storage.db import SupabaseDB

db = SupabaseDB()

# Get verified forecasts from last 12h (non-weekend)
since = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
res = db.client.table('price_forecasts').select(
    'symbol, ts_utc, base_price, '
    'h30_direction, h30_correct, h30_strength, h30_confidence, h30_actual_price'
).gte('ts_utc', since).not_.is_('verified_at', 'null').execute()
rows = [r for r in (res.data or []) if r.get('h30_correct') is not None and r.get('h30_direction') not in (None, 'neutral')]

print(f"Total verified h30 forecasts: {len(rows)}")

INDICATOR_NAMES = ['ma_cross', 'rsi_trend', 'rsi_extreme', 'price_vs_ma', 'momentum']

# Stats
indicator_stats: Dict[str, Dict[str, int]] = {
    name: {'agree_correct': 0, 'agree_wrong': 0, 'disagree_correct': 0, 'disagree_wrong': 0, 'neutral': 0}
    for name in INDICATOR_NAMES
}
pair_indicator_stats: Dict[str, Dict[str, Dict[str, int]]] = {}

# Cache bars per symbol+hour
bars_cache: Dict[str, List[Dict]] = {}


def get_bars(symbol: str, before_ts: str, n_bars: int = 250):
    cache_key = f"{symbol}_{before_ts[:13]}"
    if cache_key in bars_cache:
        return bars_cache[cache_key]
    
    res = db.client.table('market_snapshots').select(
        'ts, close, rsi, ma_fast, ma_slow, atr'
    ).eq('symbol', symbol).eq('timeframe', 'M15').lte(
        'ts', before_ts
    ).order('ts', desc=True).limit(n_bars).execute()
    
    data = list(reversed(res.data or []))
    bars_cache[cache_key] = data
    return data


def recalc_votes(bars: List[Dict]) -> Dict[str, int]:
    """Recalculate individual indicator votes from snapshot data."""
    if len(bars) < 20:
        return {}
    
    closes = [float(b['close']) for b in bars if b.get('close') is not None]
    if len(closes) < 20:
        return {}
    
    latest = bars[-1]
    prev_bars = bars[:-3] if len(bars) > 3 else bars[:-1]
    prev = prev_bars[-1] if prev_bars else None
    
    ma_fast = latest.get('ma_fast')
    ma_slow = latest.get('ma_slow')
    rsi_now = latest.get('rsi')
    rsi_prev = prev.get('rsi') if prev else None
    
    votes = {}
    
    # 1. MA Cross: fast > slow = UP
    if ma_fast is not None and ma_slow is not None and ma_slow != 0:
        if ma_fast > ma_slow:
            votes['ma_cross'] = 1
        elif ma_fast < ma_slow:
            votes['ma_cross'] = -1
        else:
            votes['ma_cross'] = 0
    else:
        votes['ma_cross'] = 0
    
    # 2. RSI Trend: RSI > 50 and rising = UP
    if rsi_now is not None and rsi_prev is not None:
        if rsi_now > 50 and rsi_now > rsi_prev:
            votes['rsi_trend'] = 1
        elif rsi_now < 50 and rsi_now < rsi_prev:
            votes['rsi_trend'] = -1
        else:
            votes['rsi_trend'] = 0
    else:
        votes['rsi_trend'] = 0
    
    # 3. RSI Extreme: oversold = UP (bounce), overbought = DOWN
    if rsi_now is not None:
        if rsi_now <= 30:
            votes['rsi_extreme'] = 1
        elif rsi_now >= 70:
            votes['rsi_extreme'] = -1
        else:
            votes['rsi_extreme'] = 0
    else:
        votes['rsi_extreme'] = 0
    
    # 4. Price vs MA: close > ma_fast = UP
    if ma_fast is not None and closes:
        price = closes[-1]
        if price > ma_fast:
            votes['price_vs_ma'] = 1
        elif price < ma_fast:
            votes['price_vs_ma'] = -1
        else:
            votes['price_vs_ma'] = 0
    else:
        votes['price_vs_ma'] = 0
    
    # 5. Momentum: close > close[-6] = UP
    lookback = 6
    if len(closes) > lookback:
        current = closes[-1]
        past = closes[-(lookback + 1)]
        if past != 0:
            if current > past:
                votes['momentum'] = 1
            elif current < past:
                votes['momentum'] = -1
            else:
                votes['momentum'] = 0
        else:
            votes['momentum'] = 0
    else:
        votes['momentum'] = 0
    
    return votes


processed = 0
skipped = 0

for row in rows:
    symbol = row['symbol']
    ts = row['ts_utc']
    direction = row['h30_direction']
    correct = row['h30_correct']
    
    expected_sign = 1 if direction == 'up' else -1
    
    bars = get_bars(symbol, ts)
    if len(bars) < 20:
        skipped += 1
        continue
    
    votes = recalc_votes(bars)
    if not votes:
        skipped += 1
        continue
    
    processed += 1
    
    if symbol not in pair_indicator_stats:
        pair_indicator_stats[symbol] = {
            name: {'agree_correct': 0, 'agree_wrong': 0, 'disagree_correct': 0, 'disagree_wrong': 0, 'neutral': 0}
            for name in INDICATOR_NAMES
        }
    
    for name, vote in votes.items():
        if vote == 0:
            indicator_stats[name]['neutral'] += 1
            pair_indicator_stats[symbol][name]['neutral'] += 1
            continue
        
        aligned = (vote == expected_sign)
        
        if aligned and correct:
            indicator_stats[name]['agree_correct'] += 1
            pair_indicator_stats[symbol][name]['agree_correct'] += 1
        elif aligned and not correct:
            indicator_stats[name]['agree_wrong'] += 1
            pair_indicator_stats[symbol][name]['agree_wrong'] += 1
        elif not aligned and correct:
            indicator_stats[name]['disagree_correct'] += 1
            pair_indicator_stats[symbol][name]['disagree_correct'] += 1
        elif not aligned and not correct:
            indicator_stats[name]['disagree_wrong'] += 1
            pair_indicator_stats[symbol][name]['disagree_wrong'] += 1

print(f"Processed: {processed}, Skipped: {skipped}")
print()

print("=== INDICATOR PREDICTIVE POWER ===")
print(f"  {'Indicator':<14s} | {'Agree->OK':>10s} | {'Agree->Fail':>11s} | {'Acc%':>5s} | {'Disagree':>9s} | {'Neutral':>8s}")
print(f"  {'-'*68}")

for name in INDICATOR_NAMES:
    s = indicator_stats[name]
    ac = s['agree_correct']
    aw = s['agree_wrong']
    dc = s['disagree_correct']
    dw = s['disagree_wrong']
    n = s['neutral']
    agree_total = ac + aw
    acc = f"{int(ac/agree_total*100)}%" if agree_total > 0 else "-"
    disagree_total = dc + dw
    print(f"  {name:<14s} | {ac:>10d} | {aw:>11d} | {acc:>5s} | {disagree_total:>9d} | {n:>8d}")

print()
print("=== INDICATOR SIGNAL VALUE (agree_acc vs disagree_acc) ===")
print(f"  {'Indicator':<14s} | {'Agree Acc':>10s} | {'Disagree Acc':>12s} | {'Delta':>6s} | {'Value':>12s}")
print(f"  {'-'*64}")

for name in INDICATOR_NAMES:
    s = indicator_stats[name]
    ac = s['agree_correct']
    aw = s['agree_wrong']
    dc = s['disagree_correct']
    dw = s['disagree_wrong']
    
    agree_total = ac + aw
    agree_rate = ac / agree_total if agree_total > 0 else 0
    disagree_total = dc + dw
    disagree_rate = dc / disagree_total if disagree_total > 0 else 0
    
    delta = agree_rate - disagree_rate
    
    a_str = f"{int(agree_rate*100)}%({agree_total})" if agree_total > 0 else "-"
    d_str = f"{int(disagree_rate*100)}%({disagree_total})" if disagree_total > 0 else "-"
    delta_str = f"{delta:+.0%}" if agree_total > 0 and disagree_total > 0 else "-"
    
    if agree_total > 0 and disagree_total > 0:
        val = "STRONG" if delta > 0.15 else "GOOD" if delta > 0.05 else "WEAK" if delta > 0 else "CONTRARIAN"
    else:
        val = "N/A"
    
    print(f"  {name:<14s} | {a_str:>10s} | {d_str:>12s} | {delta_str:>6s} | {val:>12s}")

# Per top-pair breakdown
print()
print("=== TOP PAIRS: INDICATOR BREAKDOWN ===")
top_pairs = ['CADJPY', 'GBPJPY', 'CHFJPY', 'NZDUSD', 'USDCAD', 'EURGBP', 'EURJPY']

for sym in top_pairs:
    if sym not in pair_indicator_stats:
        continue
    sr = [r for r in rows if r['symbol'] == sym and r.get('h30_correct') is not None]
    total = len([r for r in sr if r.get('h30_direction') not in (None, 'neutral')])
    correct = len([r for r in sr if r.get('h30_correct') == True])
    pct = f"{int(correct/total*100)}%" if total > 0 else "-"
    
    print(f"\n--- {sym} (h30: {correct}/{total} = {pct}) ---")
    print(f"  {'Indicator':<14s} | {'AgreeOK':>8s} | {'AgreeFail':>9s} | {'Acc':>5s} | {'DisagreeOK':>10s} | {'DisagreeFail':>12s} | {'Neutral':>7s}")
    print(f"  {'-'*78}")
    for name in INDICATOR_NAMES:
        s = pair_indicator_stats[sym][name]
        ac = s['agree_correct']
        aw = s['agree_wrong']
        dc = s['disagree_correct']
        dw = s['disagree_wrong']
        n = s['neutral']
        total_a = ac + aw
        acc = f"{int(ac/total_a*100)}%" if total_a > 0 else "-"
        print(f"  {name:<14s} | {ac:>8d} | {aw:>9d} | {acc:>5s} | {dc:>10d} | {dw:>12d} | {n:>7d}")
