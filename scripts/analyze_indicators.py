"""
Analyze individual indicator accuracy for verified forecasts.
Recalculates votes from market_snapshots data and cross-references with verification results.
Run inside container: python3 scripts/analyze_indicators.py
"""
import sys
sys.path.insert(0, '/app')

from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from app.storage.db import SupabaseDB
from app.forecast.indicators_vote import (
    vote_ma_cross, vote_rsi_trend, vote_rsi_extreme,
    vote_price_vs_ma, vote_momentum, vote_atr_trend,
    aggregate_votes,
)
from app.market_data.indicators import sma as calc_sma

db = SupabaseDB()

# Get verified forecasts from last 12h (non-weekend)
since = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
res = db.client.table('price_forecasts').select(
    'symbol, ts_utc, base_price, '
    'h30_direction, h30_correct, h30_strength, h30_confidence, h30_actual_price'
).gte('ts_utc', since).not_.is_('verified_at', 'null').execute()
rows = [r for r in (res.data or []) if r.get('h30_correct') is not None and r.get('h30_direction') not in (None, 'neutral')]

print(f"Total verified h30 forecasts: {len(rows)}")

# For each forecast, load M15 bars from market_snapshots at that timestamp
# and recalculate individual votes
INDICATOR_NAMES = ['ma_cross', 'rsi_trend', 'rsi_extreme', 'price_vs_ma', 'momentum', 'atr_trend']

# Stats: indicator -> {correct_when_aligned: N, wrong_when_aligned: N, ...}
indicator_stats: Dict[str, Dict[str, int]] = {
    name: {'agree_correct': 0, 'agree_wrong': 0, 'disagree_correct': 0, 'disagree_wrong': 0, 'neutral': 0}
    for name in INDICATOR_NAMES
}

# Per-pair stats
pair_indicator_stats: Dict[str, Dict[str, Dict[str, int]]] = {}

# Cache M15 bars per symbol to avoid repeated queries
bars_cache: Dict[str, List[Dict]] = {}

def get_bars(symbol: str, before_ts: str, n_bars: int = 250):
    """Load M15 bars before given timestamp."""
    cache_key = f"{symbol}_{before_ts[:13]}"  # cache per hour
    if cache_key in bars_cache:
        return bars_cache[cache_key]
    
    res = db.client.table('market_snapshots').select(
        'ts, open, high, low, close'
    ).eq('symbol', symbol).eq('timeframe', 'M15').lte(
        'ts', before_ts
    ).order('ts', desc=True).limit(n_bars).execute()
    
    data = list(reversed(res.data or []))
    bars_cache[cache_key] = data
    return data


def recalc_votes(bars: List[Dict]) -> Dict[str, int]:
    """Recalculate individual indicator votes from bars."""
    closes = [float(b['close']) for b in bars if b.get('close')]
    highs = [float(b['high']) for b in bars if b.get('high')]
    lows = [float(b['low']) for b in bars if b.get('low')]
    
    if len(closes) < 50:
        return {}
    
    votes = {}
    votes['ma_cross'] = vote_ma_cross(closes, 20, 50)
    votes['rsi_trend'] = vote_rsi_trend(closes)
    votes['rsi_extreme'] = vote_rsi_extreme(closes)
    votes['price_vs_ma'] = vote_price_vs_ma(closes, 20)
    votes['momentum'] = vote_momentum(closes, 6)
    
    if len(highs) >= 20 and len(lows) >= 20:
        ma_f = calc_sma(closes, 20)
        ma_s = calc_sma(closes, 50)
        votes['atr_trend'] = vote_atr_trend(highs, lows, closes, ma_f, ma_s)
    else:
        votes['atr_trend'] = 0
    
    return votes


processed = 0
skipped = 0

for row in rows:
    symbol = row['symbol']
    ts = row['ts_utc']
    direction = row['h30_direction']  # 'up' or 'down'
    correct = row['h30_correct']
    
    expected_sign = 1 if direction == 'up' else -1
    
    bars = get_bars(symbol, ts)
    if len(bars) < 50:
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
        
        aligned = (vote == expected_sign)  # indicator agreed with forecast direction
        
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

# Global indicator accuracy
print("=== INDICATOR ACCURACY (when indicator votes, is the forecast correct?) ===")
print(f"{'Indicator':<14s} | {'Agree+Correct':>14s} | {'Agree+Wrong':>12s} | {'Agree Acc':>10s} | {'Neutral':>8s} | {'Disagree':>9s}")
print("-" * 82)

for name in INDICATOR_NAMES:
    s = indicator_stats[name]
    ac = s['agree_correct']
    aw = s['agree_wrong']
    dc = s['disagree_correct']
    dw = s['disagree_wrong']
    n = s['neutral']
    agree_total = ac + aw
    agree_acc = f"{int(ac/agree_total*100)}%" if agree_total > 0 else "-"
    disagree_total = dc + dw
    print(f"{name:<14s} | {ac:>14d} | {aw:>12d} | {agree_acc:>10s} | {n:>8d} | {disagree_total:>9d}")

print()
print("=== INDICATOR PREDICTIVE POWER (does indicator vote predict outcome?) ===")
print(f"{'Indicator':<14s} | {'Vote=Dir Acc':>12s} | {'Vote!=Dir Acc':>13s} | {'Delta':>6s} | {'Signal Value':>12s}")
print("-" * 72)

for name in INDICATOR_NAMES:
    s = indicator_stats[name]
    ac = s['agree_correct']
    aw = s['agree_wrong']
    dc = s['disagree_correct']
    dw = s['disagree_wrong']
    
    # When indicator agrees with forecast direction, how often is forecast correct?
    agree_total = ac + aw
    agree_rate = ac / agree_total if agree_total > 0 else 0
    
    # When indicator disagrees, how often is forecast correct?
    disagree_total = dc + dw
    disagree_rate = dc / disagree_total if disagree_total > 0 else 0
    
    delta = agree_rate - disagree_rate
    
    agree_str = f"{int(agree_rate*100)}% ({agree_total})" if agree_total > 0 else "-"
    disagree_str = f"{int(disagree_rate*100)}% ({disagree_total})" if disagree_total > 0 else "-"
    delta_str = f"{delta:+.0%}" if agree_total > 0 and disagree_total > 0 else "-"
    
    # Signal value: positive = good predictor, negative = contrarian
    if agree_total > 0 and disagree_total > 0:
        signal = "GOOD" if delta > 0.1 else "WEAK" if delta > 0 else "CONTRARIAN"
    else:
        signal = "N/A"
    
    print(f"{name:<14s} | {agree_str:>12s} | {disagree_str:>13s} | {delta_str:>6s} | {signal:>12s}")

# Per top-pair breakdown
print()
print("=== TOP PAIRS: INDICATOR BREAKDOWN ===")
top_pairs = ['CADJPY', 'GBPJPY', 'CHFJPY', 'NZDUSD', 'USDCAD']

for sym in top_pairs:
    if sym not in pair_indicator_stats:
        continue
    print(f"\n--- {sym} ---")
    print(f"  {'Indicator':<14s} | {'Agree OK':>9s} | {'Agree Fail':>10s} | {'Acc':>5s} | {'Neutral':>8s}")
    print(f"  {'-'*58}")
    for name in INDICATOR_NAMES:
        s = pair_indicator_stats[sym][name]
        ac = s['agree_correct']
        aw = s['agree_wrong']
        total = ac + aw
        acc = f"{int(ac/total*100)}%" if total > 0 else "-"
        n = s['neutral']
        print(f"  {name:<14s} | {ac:>9d} | {aw:>10d} | {acc:>5s} | {n:>8d}")
