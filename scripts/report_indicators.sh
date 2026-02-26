#!/bin/bash
# Indicator accuracy report from h30_votes_json
# Usage: bash scripts/report_indicators.sh [hours]
# Default: 24 hours

HOURS=${1:-24}

docker exec ibkr-trading-bot python3 -c "
import os, math, json
from supabase import create_client
from datetime import datetime, timezone, timedelta
from collections import defaultdict

def wilson_lb(k, n, z=1.96):
    if n == 0: return 0.0
    p = k / n
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    h = (z * math.sqrt((p*(1-p) + z*z/(4*n))/n)) / d
    return max(0.0, c - h)

def fmt(k, n):
    if n == 0: return 'n/a'
    return f'{k}/{n}={100*k//n}%'

sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))
hours = $HOURS
cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

res = (sb.table('price_forecasts')
    .select('ts_utc,symbol,h30_direction,h30_correct,h30_confidence,h30_aligned,bb_width,h30_votes_json')
    .gte('ts_utc', cutoff)
    .not_.is_('h30_correct', 'null')
    .neq('h30_direction', 'neutral')
    .not_.is_('h30_votes_json', 'null')
    .order('ts_utc', desc=True)
    .limit(2000)
    .execute())

rows = res.data or []

print('=' * 70)
print(f'INDICATOR ACCURACY REPORT — last {hours}h')
print(f'Cutoff: {cutoff[:19]}')
print(f'Rows with votes + verified + non-neutral: {len(rows)}')
print('=' * 70)

if len(rows) < 5:
    print('Not enough data yet. Need at least 5 verified rows with votes.')
    exit()

# Overall accuracy
correct = sum(1 for r in rows if r['h30_correct'])
total = len(rows)
print(f'OVERALL: {correct}/{total} = {100*correct/total:.1f}% | Wilson LB: {100*wilson_lb(correct,total):.1f}%')

# Quality filtered
qrows = [r for r in rows if (r.get('h30_confidence') or '').lower() == 'medium' and (r.get('h30_aligned') or 0) >= 4]
qcorrect = sum(1 for r in qrows if r['h30_correct'])
qtotal = len(qrows)
if qtotal > 0:
    print(f'QUALITY (MED+al>=4): {qcorrect}/{qtotal} = {100*qcorrect/qtotal:.1f}% | Wilson LB: {100*wilson_lb(qcorrect,qtotal):.1f}%')
print()

# Per-indicator analysis
indicators = ['ma_cross_inv', 'rsi_trend', 'rsi_momentum', 'price_vs_ma', 'momentum', 'atr_trend', 'secondary_ma']

stats = {}
for ind in indicators:
    stats[ind] = {'agree_c': 0, 'agree_t': 0, 'opp_c': 0, 'opp_t': 0, 'neu_c': 0, 'neu_t': 0}

for r in rows:
    votes = r.get('h30_votes_json', {})
    if not isinstance(votes, dict):
        continue
    direction = r['h30_direction']
    is_correct = r['h30_correct']
    dir_sign = 1 if direction == 'up' else -1

    for ind in indicators:
        v = votes.get(ind)
        if v is None:
            continue
        s = stats[ind]
        if v == 0:
            s['neu_t'] += 1
            if is_correct:
                s['neu_c'] += 1
        elif v == dir_sign:
            s['agree_t'] += 1
            if is_correct:
                s['agree_c'] += 1
        else:
            s['opp_t'] += 1
            if is_correct:
                s['opp_c'] += 1

print('PER-INDICATOR ANALYSIS')
print(f'{\"Indicator\":<15} {\"Agrees->Acc\":<14} {\"Opposes->Acc\":<14} {\"Neutral->Acc\":<14} {\"Delta\":<8} {\"Signal\"}')
print('-' * 70)

for ind in indicators:
    s = stats[ind]
    at, ac = s['agree_t'], s['agree_c']
    ot, oc = s['opp_t'], s['opp_c']
    nt, nc = s['neu_t'], s['neu_c']

    agree_str = f'{ac}/{at}={100*ac//at}%' if at > 0 else 'n/a'
    opp_str = f'{oc}/{ot}={100*oc//ot}%' if ot > 0 else 'n/a'
    neu_str = f'{nc}/{nt}={100*nc//nt}%' if nt > 0 else 'n/a'

    delta = ''
    signal = ''
    if at >= 3 and ot >= 3:
        d = (ac/at - oc/ot) * 100
        delta = f'{d:+.0f}%'
        if d > 10:
            signal = 'GOOD'
        elif d < -10:
            signal = 'CONTRARIAN'
        else:
            signal = 'WEAK'

    print(f'{ind:<15} {agree_str:<14} {opp_str:<14} {neu_str:<14} {delta:<8} {signal}')

print()

# Participation rate
print('PARTICIPATION RATE (non-neutral votes / total rows)')
print('-' * 70)
for ind in indicators:
    s = stats[ind]
    active = s['agree_t'] + s['opp_t']
    pct = 100 * active / total if total > 0 else 0
    bar = '#' * int(pct / 2)
    print(f'{ind:<15} {active:>4}/{total} = {pct:5.1f}% {bar}')

print()

# Vote pattern analysis
print('VOTE PATTERN ANALYSIS (top combos by count, n>=3)')
print('-' * 70)
combos = defaultdict(lambda: [0, 0])
for r in rows:
    votes = r.get('h30_votes_json', {})
    if not isinstance(votes, dict):
        continue
    direction = r['h30_direction']
    dir_sign = 1 if direction == 'up' else -1
    agreeing = sorted([k for k, v in votes.items() if v == dir_sign])
    key = '+'.join(agreeing) if agreeing else '(none agree)'
    combos[key][1] += 1
    if r['h30_correct']:
        combos[key][0] += 1

sorted_combos = sorted(combos.items(), key=lambda x: x[1][1], reverse=True)
print(f'{\"Agreeing indicators\":<50} {\"Acc\":<10} {\"N\"}')
for pattern, (c, t) in sorted_combos[:10]:
    if t >= 3:
        pct = 100 * c / t
        print(f'{pattern:<50} {c}/{t}={pct:.0f}%{\"\":>3} n={t}')

print()
print('=' * 70)
" 2>&1
