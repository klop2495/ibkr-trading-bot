"""
Alt2 Backtest for Feb 27, 2026.
Downloads M15 OHLC from IB Gateway and runs exact Alt2 logic.
Compares with real signals and shows missed opportunities.
"""
import os, sys, json
from datetime import datetime, timezone, timedelta

# ── helpers copied from forecast engine ──────────────────────

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
    tr_list = []
    plus_dm = []
    minus_dm = []
    for i in range(1, n):
        h = highs[i]; l = lows[i]; pc = closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
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
        if di_sum == 0:
            dx_list.append(0)
        else:
            dx_list.append(100 * abs(plus_di - minus_di) / di_sum)
    
    if len(dx_list) < period:
        return None
    
    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period
    return adx

def vote_ma_cross_inverted(closes, fast=20, slow=50):
    if len(closes) < slow:
        return 0
    f = sma(closes, fast)
    s = sma(closes, slow)
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

ALT2_SYMBOLS = {"EURCHF","CHFJPY","EURJPY","AUDJPY","CADJPY","NZDJPY","EURGBP","USDJPY"}

def compute_alt2(symbol, closes, highs, lows, ma_fast=20, ma_slow=50):
    """Returns (direction, adx_value) or (None, adx_value)"""
    if symbol not in ALT2_SYMBOLS:
        return None, None
    
    ma = vote_ma_cross_inverted(closes, ma_fast, ma_slow)
    pv = vote_price_vs_ma(closes, ma_fast)
    
    if ma == 0 or pv == 0 or ma == pv:
        return None, None
    
    adx_val = adx_calc(highs, lows, closes, 14)
    if adx_val is None or adx_val < 30:
        return None, adx_val
    
    direction = "up" if ma == 1 else "down"
    return direction, adx_val


# ── Main backtest ────────────────────────────────────────────

print("=" * 70)
print("ALT2 BACKTEST — 27 February 2026")
print("=" * 70)

from ib_insync import IB, Forex
import asyncio

def fetch_ohlc_from_ib(symbols, duration="2 D", bar_size="15 mins"):
    """Fetch M15 OHLC from IB Gateway."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    host = os.getenv("IB_GATEWAY_HOST", "ib-gateway")
    port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
    
    ib = IB()
    all_data = {}
    
    try:
        loop.run_until_complete(
            ib.connectAsync(host, port, clientId=170, timeout=15, readonly=True)
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
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="MIDPOINT",
                useRTH=False,
                formatDate=2,
            )
            
            if bars:
                all_data[sym] = [{
                    "ts": str(b.date),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
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

symbols = list(ALT2_SYMBOLS)
print("\nFetching M15 OHLC from IB Gateway...")
ohlc_data = fetch_ohlc_from_ib(symbols)

if not ohlc_data:
    print("No data fetched. Exiting.")
    sys.exit(1)

# Run backtest
print("\n" + "=" * 70)
print("BACKTEST RESULTS")
print("=" * 70)

total = 0
correct = 0
live_total = 0
live_correct = 0
missed_total = 0
missed_correct = 0
results = []

for sym in sorted(ohlc_data.keys()):
    bars = ohlc_data[sym]
    if len(bars) < 60:
        continue
    
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    
    # Find midnight Feb 27 index
    mi = None
    for i, b in enumerate(bars):
        ts = b["ts"]
        if "2026-02-27" in ts:
            mi = i
            break
    
    if mi is None or mi < 50:
        print(f"  {sym}: not enough pre-midnight data (mi={mi})")
        continue
    
    # Dedup: only check every 2nd bar (every 30 min) to avoid signal spam
    for i in range(max(mi, 50), len(bars) - 2, 2):
        c_slice = closes[:i+1]
        h_slice = highs[:i+1]
        l_slice = lows[:i+1]
        
        direction, adx_val = compute_alt2(sym, c_slice, h_slice, l_slice)
        
        if direction is None:
            continue
        
        # Check 30 min ahead (2 x M15 bars)
        base_price = closes[i]
        target_price = closes[i + 2]
        
        if target_price > base_price:
            actual = "up"
        elif target_price < base_price:
            actual = "down"
        else:
            actual = "neutral"
        
        ok = (direction == actual)
        total += 1
        if ok:
            correct += 1
        
        ts = bars[i]["ts"]
        is_live = ts < "2026-02-27 06:07"
        
        if is_live:
            live_total += 1
            if ok: live_correct += 1
        else:
            missed_total += 1
            if ok: missed_correct += 1
        
        tag = "LIVE" if is_live else "MISS"
        results.append({
            "ts": ts[:16],
            "sym": sym,
            "dir": direction,
            "actual": actual,
            "ok": ok,
            "adx": adx_val,
            "tag": tag,
        })

# Print results
print(f"\n{'TS':<18} {'SYM':<9} {'ALT2':<6} {'ACTUAL':<7} {'OK':<4} {'ADX':<6} {'TAG'}")
print("-" * 65)
for r in results:
    ok_str = "Y" if r["ok"] else "N"
    print(f"{r['ts']:<18} {r['sym']:<9} {r['dir']:<6} {r['actual']:<7} {ok_str:<4} {r['adx']:.1f}  {r['tag']}")

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
if total:
    print(f"Total:  {correct}/{total} = {correct/total*100:.1f}%")
if live_total:
    print(f"LIVE (00:00-06:07):  {live_correct}/{live_total} = {live_correct/live_total*100:.1f}%")
if missed_total:
    print(f"MISSED (06:07+):     {missed_correct}/{missed_total} = {missed_correct/missed_total*100:.1f}%")
else:
    print("MISSED: 0 signals")

# P&L calculation (stake=100 per signal)
print(f"\nP&L (stake=100):")
pnl = sum(100 if r["ok"] else -100 for r in results)
live_pnl = sum(100 if r["ok"] else -100 for r in results if r["tag"] == "LIVE")
miss_pnl = sum(100 if r["ok"] else -100 for r in results if r["tag"] == "MISS")
print(f"  Total P&L:  {pnl:+d}")
print(f"  LIVE P&L:   {live_pnl:+d}")
print(f"  MISSED P&L: {miss_pnl:+d}")

# By symbol
print(f"\nBy symbol:")
from collections import defaultdict
sym_stats = defaultdict(lambda: [0, 0])
for r in results:
    sym_stats[r["sym"]][0] += 1
    if r["ok"]: sym_stats[r["sym"]][1] += 1
for s in sorted(sym_stats, key=lambda s: sym_stats[s][1]/sym_stats[s][0] if sym_stats[s][0] else 0, reverse=True):
    t, c = sym_stats[s]
    print(f"  {s:<10} {c}/{t} = {c/t*100:.0f}%")

# By hour
print(f"\nBy hour (UTC):")
hour_stats = defaultdict(lambda: [0, 0])
for r in results:
    h = int(r["ts"][11:13])
    hour_stats[h][0] += 1
    if r["ok"]: hour_stats[h][1] += 1
for h in sorted(hour_stats):
    t, c = hour_stats[h]
    print(f"  {h:02d}:00  {c}/{t} = {c/t*100:.0f}%")
