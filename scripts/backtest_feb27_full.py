"""
Full-day backtest for Feb 27, 2026.
Downloads M15 OHLC from IB Gateway and runs Alt2 + Alt3 + baseline.
Simulates what would have happened if broker connection was stable all day.
Uses exact same logic as app/forecast/engine.py.
"""
import os, sys, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ── Indicators (exact copy from forecast engine) ─────────────

def sma(data, period):
    if len(data) < period:
        return None
    return sum(data[-period:]) / period

def rsi(data, period=14):
    if len(data) < period + 1:
        return None
    changes = [data[i] - data[i-1] for i in range(len(data)-period, len(data))]
    gains = [c if c > 0 else 0 for c in changes]
    losses = [-c if c < 0 else 0 for c in changes]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def adx_calc(highs, lows, closes, period=14):
    n = len(closes)
    if n < period * 2 or len(highs) != n or len(lows) != n:
        return None
    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
    if len(tr_list) < period:
        return None
    atr = sum(tr_list[:period]) / period
    plus_di_sum = sum(plus_dm[:period]) / period
    minus_di_sum = sum(minus_dm[:period]) / period
    dx_list = []
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        plus_di_sum = (plus_di_sum * (period - 1) + plus_dm[i]) / period
        minus_di_sum = (minus_di_sum * (period - 1) + minus_dm[i]) / period
        if atr == 0:
            continue
        plus_di = 100 * plus_di_sum / atr
        minus_di = 100 * minus_di_sum / atr
        di_sum = plus_di + minus_di
        dx_list.append(100 * abs(plus_di - minus_di) / di_sum if di_sum else 0)
    if len(dx_list) < period:
        return None
    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period
    return adx

def vote_ma_cross_inverted(closes, fast=20, slow=50):
    if len(closes) < slow:
        return 0
    f, s = sma(closes, fast), sma(closes, slow)
    if f is None or s is None:
        return 0
    if f > s: return -1  # inverted
    if f < s: return 1   # inverted
    return 0

def vote_price_vs_ma(closes, period=20):
    if len(closes) < period:
        return 0
    ma = sma(closes, period)
    if ma is None or ma == 0:
        return 0
    if closes[-1] > ma: return 1
    if closes[-1] < ma: return -1
    return 0

def vote_momentum(closes, lookback=6):
    if len(closes) < lookback + 1:
        return 0
    change = closes[-1] - closes[-lookback - 1]
    if change > 0: return 1
    if change < 0: return -1
    return 0

def vote_rsi_trend(closes, period=14):
    r = rsi(closes, period)
    if r is None:
        return 0
    if r > 55: return 1
    if r < 45: return -1
    return 0

def vote_rsi_momentum(closes, period=14):
    r = rsi(closes, period)
    if r is None:
        return 0
    if r > 70: return -1   # overbought → expect reversal down
    if r < 30: return 1    # oversold → expect reversal up
    return 0

def vote_atr_trend(highs, lows, closes, ma_fast_val, ma_slow_val):
    if ma_fast_val is None or ma_slow_val is None:
        return 0
    n = len(closes)
    if n < 20 or len(highs) < 20 or len(lows) < 20:
        return 0
    tr_list = []
    for i in range(max(1, n-14), n):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not tr_list:
        return 0
    atr = sum(tr_list) / len(tr_list)
    if atr == 0:
        return 0
    ma_diff = ma_fast_val - ma_slow_val
    normalised = ma_diff / atr
    if normalised > 0.5: return 1
    if normalised < -0.5: return -1
    return 0

# ── Strategies ───────────────────────────────────────────────

ALT2_SYMBOLS = {"EURCHF","CHFJPY","EURJPY","AUDJPY","CADJPY","NZDJPY","EURGBP","USDJPY"}

def compute_baseline(symbol, closes, highs, lows, ma_fast=20, ma_slow=50):
    """Current system: all 7 indicators with equal weight, aggregate vote."""
    if len(closes) < ma_slow:
        return None, {}
    
    votes = {}
    votes["ma_cross_inv"] = vote_ma_cross_inverted(closes, ma_fast, ma_slow)
    votes["price_vs_ma"] = vote_price_vs_ma(closes, ma_fast)
    votes["momentum"] = vote_momentum(closes, 6)
    votes["rsi_trend"] = vote_rsi_trend(closes)
    votes["rsi_momentum"] = vote_rsi_momentum(closes)
    
    if len(highs) >= 20 and len(lows) >= 20:
        ma_f = sma(closes, ma_fast)
        ma_s = sma(closes, ma_slow)
        votes["atr_trend"] = vote_atr_trend(highs, lows, closes, ma_f, ma_s)
    
    # Simple majority vote
    total_vote = sum(v for v in votes.values())
    if total_vote > 0:
        return "up", votes
    elif total_vote < 0:
        return "down", votes
    return None, votes

def compute_alt2(symbol, closes, highs, lows, ma_fast=20, ma_slow=50):
    """Alt2: ma_cross_inv != price_vs_ma AND ADX >= 30 AND top-8."""
    if symbol not in ALT2_SYMBOLS:
        return None, None, {}
    ma = vote_ma_cross_inverted(closes, ma_fast, ma_slow)
    pv = vote_price_vs_ma(closes, ma_fast)
    if ma == 0 or pv == 0 or ma == pv:
        return None, None, {"ma": ma, "pv": pv}
    adx_val = adx_calc(highs, lows, closes, 14)
    if adx_val is None or adx_val < 30:
        return None, adx_val, {"ma": ma, "pv": pv, "adx": adx_val}
    direction = "up" if ma == 1 else "down"
    return direction, adx_val, {"ma": ma, "pv": pv, "adx": adx_val}

def compute_alt3(symbol, closes, highs, lows, ma_fast=20, ma_slow=50):
    """Alt3: Alt2 + momentum must agree with ma_cross_inv."""
    if symbol not in ALT2_SYMBOLS:
        return None, None, {}
    ma = vote_ma_cross_inverted(closes, ma_fast, ma_slow)
    pv = vote_price_vs_ma(closes, ma_fast)
    mom = vote_momentum(closes, 6)
    if ma == 0 or pv == 0 or ma == pv:
        return None, None, {"ma": ma, "pv": pv, "mom": mom}
    adx_val = adx_calc(highs, lows, closes, 14)
    if adx_val is None or adx_val < 30:
        return None, adx_val, {"ma": ma, "pv": pv, "mom": mom, "adx": adx_val}
    # Alt3 extra: momentum must agree with ma_cross_inv
    if mom != ma:
        return None, adx_val, {"ma": ma, "pv": pv, "mom": mom, "adx": adx_val, "veto": "mom!=ma"}
    direction = "up" if ma == 1 else "down"
    return direction, adx_val, {"ma": ma, "pv": pv, "mom": mom, "adx": adx_val}


# ── IB Gateway data fetch ────────────────────────────────────

from ib_insync import IB, Forex
import asyncio

def fetch_ohlc_from_ib(symbols, duration="2 D", bar_size="15 mins"):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    host = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
    port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
    ib = IB()
    all_data = {}
    try:
        loop.run_until_complete(
            ib.connectAsync(host, port, clientId=171, timeout=15, readonly=True)
        )
        if not ib.isConnected():
            print("ERROR: Cannot connect to IB Gateway")
            return all_data
        print(f"Connected to IB Gateway {host}:{port}")
        for sym in symbols:
            contract = Forex(pair=sym)
            try:
                ib.qualifyContracts(contract)
            except:
                pass
            bars = ib.reqHistoricalData(
                contract, endDateTime="", durationStr=duration,
                barSizeSetting=bar_size, whatToShow="MIDPOINT",
                useRTH=False, formatDate=2,
            )
            if bars:
                all_data[sym] = [{
                    "ts": str(b.date), "open": b.open, "high": b.high,
                    "low": b.low, "close": b.close,
                } for b in bars]
                print(f"  {sym}: {len(bars)} bars, {bars[0].date} -> {bars[-1].date}")
            else:
                print(f"  {sym}: NO DATA")
            ib.sleep(0.5)
    except Exception as e:
        print(f"IB error: {e}")
    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except:
            pass
        loop.close()
    return all_data


# ── Main ─────────────────────────────────────────────────────

TARGET_DATE = "2026-02-27"

print("=" * 80)
print(f"FULL-DAY BACKTEST — {TARGET_DATE}")
print("Alt2 + Alt3 + Baseline | M15 bars from IB Gateway")
print("Simulating: what if broker connection was stable all day")
print("=" * 80)

all_symbols = list(ALT2_SYMBOLS)
print(f"\nSymbols: {sorted(all_symbols)}")
print(f"Fetching M15 OHLC from IB Gateway (2 days for warmup)...")
ohlc_data = fetch_ohlc_from_ib(all_symbols, duration="2 D")

if not ohlc_data:
    print("No data fetched. Exiting.")
    sys.exit(1)

# ── Run backtest ─────────────────────────────────────────────

results_alt2 = []
results_alt3 = []
results_base = []

for sym in sorted(ohlc_data.keys()):
    bars = ohlc_data[sym]
    if len(bars) < 60:
        print(f"  {sym}: insufficient data ({len(bars)} bars)")
        continue
    
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    
    # Find first bar of target date
    start_idx = None
    for i, b in enumerate(bars):
        if TARGET_DATE in b["ts"]:
            start_idx = i
            break
    
    if start_idx is None or start_idx < 50:
        print(f"  {sym}: target date not found or insufficient warmup (idx={start_idx})")
        continue
    
    # Step through every 2nd bar (30 min intervals = dedup)
    for i in range(max(start_idx, 50), len(bars) - 2, 2):
        # Only bars within target date
        if TARGET_DATE not in bars[i]["ts"]:
            if bars[i]["ts"] > TARGET_DATE + " 23:59":
                break
            continue
        
        ts = bars[i]["ts"][:16]
        hour = int(bars[i]["ts"][11:13])
        base_price = closes[i]
        target_price = closes[i + 2]  # 30 min ahead
        
        if target_price > base_price:
            actual = "up"
        elif target_price < base_price:
            actual = "down"
        else:
            actual = "neutral"
        
        c_slice = closes[:i+1]
        h_slice = highs[:i+1]
        l_slice = lows[:i+1]
        
        # Baseline
        base_dir, base_votes = compute_baseline(sym, c_slice, h_slice, l_slice)
        if base_dir is not None and actual != "neutral":
            ok = (base_dir == actual)
            results_base.append({
                "ts": ts, "sym": sym, "dir": base_dir, "actual": actual,
                "ok": ok, "hour": hour, "votes": base_votes,
            })
        
        # Alt2
        alt2_dir, alt2_adx, alt2_meta = compute_alt2(sym, c_slice, h_slice, l_slice)
        if alt2_dir is not None and actual != "neutral":
            ok = (alt2_dir == actual)
            results_alt2.append({
                "ts": ts, "sym": sym, "dir": alt2_dir, "actual": actual,
                "ok": ok, "adx": alt2_adx, "hour": hour, "meta": alt2_meta,
            })
        
        # Alt3
        alt3_dir, alt3_adx, alt3_meta = compute_alt3(sym, c_slice, h_slice, l_slice)
        if alt3_dir is not None and actual != "neutral":
            ok = (alt3_dir == actual)
            results_alt3.append({
                "ts": ts, "sym": sym, "dir": alt3_dir, "actual": actual,
                "ok": ok, "adx": alt3_adx, "hour": hour, "meta": alt3_meta,
            })


# ── Print results ────────────────────────────────────────────

def print_strategy(name, results):
    print(f"\n{'='*80}")
    print(f"  {name}")
    print(f"{'='*80}")
    
    if not results:
        print("  No signals generated.")
        return
    
    total = len(results)
    correct = sum(1 for r in results if r["ok"])
    acc = correct / total * 100 if total else 0
    
    print(f"\n  {'TS':<18} {'SYM':<9} {'DIR':<6} {'ACTUAL':<7} {'OK':<4}", end="")
    if "adx" in results[0]:
        print(f" {'ADX':<6}", end="")
    print()
    print(f"  {'-'*65}")
    
    for r in results:
        ok_str = "✓" if r["ok"] else "✗"
        line = f"  {r['ts']:<18} {r['sym']:<9} {r['dir']:<6} {r['actual']:<7} {ok_str:<4}"
        if "adx" in r and r.get("adx") is not None:
            line += f" {r['adx']:.1f}"
        print(line)
    
    print(f"\n  ACCURACY: {correct}/{total} = {acc:.1f}%")
    
    # P&L (binary: win +80, loss -100)
    wins = correct
    losses = total - correct
    pnl = wins * 80 - losses * 100
    print(f"  P&L (stake=100): wins={wins}×80 = +{wins*80}, losses={losses}×100 = -{losses*100}")
    print(f"  NET P&L: {pnl:+d}")
    
    # By symbol
    print(f"\n  By symbol:")
    sym_stats = defaultdict(lambda: [0, 0])
    for r in results:
        sym_stats[r["sym"]][0] += 1
        if r["ok"]: sym_stats[r["sym"]][1] += 1
    for s in sorted(sym_stats, key=lambda s: sym_stats[s][1]/sym_stats[s][0] if sym_stats[s][0] else 0, reverse=True):
        t, c = sym_stats[s]
        print(f"    {s:<10} {c}/{t} = {c/t*100:.0f}%")
    
    # By hour
    print(f"\n  By hour (UTC):")
    hour_stats = defaultdict(lambda: [0, 0])
    for r in results:
        hour_stats[r["hour"]][0] += 1
        if r["ok"]: hour_stats[r["hour"]][1] += 1
    for h in sorted(hour_stats):
        t, c = hour_stats[h]
        pct = c/t*100 if t else 0
        bar = "█" * int(pct / 5)
        print(f"    {h:02d}:00  {c:>2}/{t:<2} = {pct:5.1f}%  {bar}")


print_strategy("BASELINE (current 7-indicator system)", results_base)
print_strategy("ALT2 (ma!=pv + ADX>=30 + top-8)", results_alt2)
print_strategy("ALT3 (Alt2 + momentum confirms)", results_alt3)

# ── Comparison summary ───────────────────────────────────────

print(f"\n{'='*80}")
print(f"  COMPARISON SUMMARY — {TARGET_DATE}")
print(f"{'='*80}")

strategies = [
    ("Baseline", results_base),
    ("Alt2", results_alt2),
    ("Alt3", results_alt3),
]

print(f"\n  {'Strategy':<35} {'Signals':>8} {'Correct':>8} {'Accuracy':>9} {'P&L':>8}")
print(f"  {'-'*72}")
for name, res in strategies:
    t = len(res)
    c = sum(1 for r in res if r["ok"])
    acc = c/t*100 if t else 0
    pnl = c * 80 - (t - c) * 100
    print(f"  {name:<35} {t:>8} {c:>8} {acc:>8.1f}% {pnl:>+8d}")

print(f"\n  Binary model: win=+80, loss=-100, stake=100/trade")
print(f"  Data: M15 OHLC from IB Gateway, step=30min (dedup)")
print(f"  Period: {TARGET_DATE} 00:00 - latest available")
