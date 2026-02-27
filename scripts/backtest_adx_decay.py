"""
Full-day backtest for Feb 27, 2026 — with ADX decay filter.
Compares: Alt2 original vs Alt2 + ADX decay vs Alt2 + blacklist vs Alt2 + both.
"""
import os, sys, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ── Indicators ───────────────────────────────────────────────

def sma(data, period):
    if len(data) < period:
        return None
    return sum(data[-period:]) / period

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
    if f > s: return -1
    if f < s: return 1
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

ALT2_SYMBOLS = {"EURCHF","CHFJPY","EURJPY","AUDJPY","CADJPY","NZDJPY","EURGBP","USDJPY"}
BLACKLIST_HOURS = {6, 8, 13, 14, 18}

def compute_alt2_core(symbol, closes, highs, lows, ma_fast=20, ma_slow=50):
    """Core Alt2: returns (direction, adx_now, ma, pv) or None."""
    if symbol not in ALT2_SYMBOLS:
        return None, None, 0, 0
    ma = vote_ma_cross_inverted(closes, ma_fast, ma_slow)
    pv = vote_price_vs_ma(closes, ma_fast)
    if ma == 0 or pv == 0 or ma == pv:
        return None, None, ma, pv
    adx_val = adx_calc(highs, lows, closes, 14)
    if adx_val is None or adx_val < 30:
        return None, adx_val, ma, pv
    direction = "up" if ma == 1 else "down"
    return direction, adx_val, ma, pv

def adx_is_decaying(adx_now, adx_prev, threshold=0.95):
    """True if ADX is falling (decaying trend)."""
    if adx_now is None or adx_prev is None:
        return False
    return adx_now < adx_prev * threshold


# ── IB Gateway fetch ─────────────────────────────────────────

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
            ib.connectAsync(host, port, clientId=172, timeout=15, readonly=True)
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
ADX_LOOKBACK = 3  # compare ADX now vs ADX N bars ago

print("=" * 80)
print(f"ADX DECAY FILTER BACKTEST — {TARGET_DATE}")
print(f"Comparing: Alt2 | Alt2+ADX_decay | Alt2+blacklist | Alt2+both")
print(f"ADX decay: skip if ADX_now < ADX_{ADX_LOOKBACK}_bars_ago * 0.95")
print(f"Blacklist hours: {sorted(BLACKLIST_HOURS)}")
print("=" * 80)

all_symbols = sorted(ALT2_SYMBOLS)
print(f"\nSymbols: {all_symbols}")
ohlc_data = fetch_ohlc_from_ib(all_symbols, duration="2 D")

if not ohlc_data:
    print("No data fetched. Exiting.")
    sys.exit(1)

# ── Run backtest with all 4 variants ─────────────────────────

# Results: dict of variant_name -> list of signal dicts
variants = {
    "Alt2 (original)": [],
    "Alt2 + ADX_decay": [],
    "Alt2 + blacklist": [],
    "Alt2 + both": [],
}

for sym in sorted(ohlc_data.keys()):
    bars = ohlc_data[sym]
    if len(bars) < 60:
        continue

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    start_idx = None
    for i, b in enumerate(bars):
        if TARGET_DATE in b["ts"]:
            start_idx = i
            break
    if start_idx is None or start_idx < 50:
        continue

    # Precompute ADX for every bar (for decay comparison)
    adx_cache = {}
    for i in range(max(start_idx - ADX_LOOKBACK, 50), len(bars)):
        c_s = closes[:i+1]
        h_s = highs[:i+1]
        l_s = lows[:i+1]
        adx_cache[i] = adx_calc(h_s, l_s, c_s, 14)

    for i in range(max(start_idx, 50), len(bars) - 2, 2):
        if TARGET_DATE not in bars[i]["ts"]:
            if bars[i]["ts"] > TARGET_DATE + " 23:59":
                break
            continue

        ts = bars[i]["ts"][:16]
        hour = int(bars[i]["ts"][11:13])
        base_price = closes[i]
        target_price = closes[i + 2]

        if target_price > base_price:
            actual = "up"
        elif target_price < base_price:
            actual = "down"
        else:
            continue  # neutral — skip

        c_slice = closes[:i+1]
        h_slice = highs[:i+1]
        l_slice = lows[:i+1]

        direction, adx_now, ma, pv = compute_alt2_core(sym, c_slice, h_slice, l_slice)

        if direction is None:
            continue

        # ADX decay check
        adx_prev = adx_cache.get(i - ADX_LOOKBACK)
        is_decaying = adx_is_decaying(adx_now, adx_prev, threshold=0.95)
        is_blacklisted = hour in BLACKLIST_HOURS

        ok = (direction == actual)

        signal = {
            "ts": ts, "sym": sym, "dir": direction, "actual": actual,
            "ok": ok, "adx": adx_now, "adx_prev": adx_prev,
            "decay": is_decaying, "hour": hour, "ma": ma, "pv": pv,
        }

        # Original Alt2 — all signals
        variants["Alt2 (original)"].append(signal)

        # Alt2 + ADX decay filter
        if not is_decaying:
            variants["Alt2 + ADX_decay"].append(signal)

        # Alt2 + blacklist
        if not is_blacklisted:
            variants["Alt2 + blacklist"].append(signal)

        # Alt2 + both filters
        if not is_decaying and not is_blacklisted:
            variants["Alt2 + both"].append(signal)


# ── Print detailed results ───────────────────────────────────

def print_variant(name, results):
    print(f"\n{'='*85}")
    print(f"  {name}")
    print(f"{'='*85}")
    if not results:
        print("  No signals.")
        return

    total = len(results)
    correct = sum(1 for r in results if r["ok"])
    acc = correct / total * 100

    print(f"\n  {'TS':<18} {'SYM':<9} {'DIR':<5} {'ACT':<5} {'OK':<3} {'ADX':>5} {'ADXp':>5} {'DEC':<4} {'H':>2}")
    print(f"  {'-'*78}")
    for r in results:
        ok_s = "✓" if r["ok"] else "✗"
        dec_s = "↓" if r["decay"] else ""
        adx_s = f"{r['adx']:.1f}" if r['adx'] else "—"
        adxp_s = f"{r['adx_prev']:.1f}" if r['adx_prev'] else "—"
        bl = "BL" if r["hour"] in BLACKLIST_HOURS else ""
        print(f"  {r['ts']:<18} {r['sym']:<9} {r['dir']:<5} {r['actual']:<5} {ok_s:<3} {adx_s:>5} {adxp_s:>5} {dec_s:<4} {r['hour']:>2} {bl}")

    wins = correct
    losses = total - correct
    pnl = wins * 80 - losses * 100
    print(f"\n  ACCURACY: {correct}/{total} = {acc:.1f}%")
    print(f"  P&L: wins={wins}×80={wins*80:+d} losses={losses}×100={losses*100:+d} → NET {pnl:+d}")

    # By symbol
    sym_stats = defaultdict(lambda: [0, 0])
    for r in results:
        sym_stats[r["sym"]][0] += 1
        if r["ok"]: sym_stats[r["sym"]][1] += 1
    print(f"\n  By symbol:")
    for s in sorted(sym_stats, key=lambda s: sym_stats[s][1]/max(sym_stats[s][0],1), reverse=True):
        t, c = sym_stats[s]
        print(f"    {s:<10} {c}/{t} = {c/t*100:.0f}%")

    # By hour
    hour_stats = defaultdict(lambda: [0, 0])
    for r in results:
        hour_stats[r["hour"]][0] += 1
        if r["ok"]: hour_stats[r["hour"]][1] += 1
    print(f"\n  By hour:")
    for h in sorted(hour_stats):
        t, c = hour_stats[h]
        pct = c/t*100 if t else 0
        bar = "█" * int(pct / 5)
        bl = " ← BLACKLIST" if h in BLACKLIST_HOURS else ""
        print(f"    {h:02d}:00  {c:>2}/{t:<2} = {pct:5.1f}%  {bar}{bl}")


for name in ["Alt2 (original)", "Alt2 + ADX_decay", "Alt2 + blacklist", "Alt2 + both"]:
    print_variant(name, variants[name])


# ── Comparison summary ───────────────────────────────────────

print(f"\n{'='*85}")
print(f"  COMPARISON — {TARGET_DATE}")
print(f"{'='*85}")
print(f"\n  {'Variant':<25} {'Signals':>8} {'Correct':>8} {'Acc':>7} {'P&L':>8} {'Filtered':>9}")
print(f"  {'-'*68}")

orig_count = len(variants["Alt2 (original)"])
for name, res in variants.items():
    t = len(res)
    c = sum(1 for r in res if r["ok"])
    acc = c/t*100 if t else 0
    pnl = c * 80 - (t - c) * 100
    filt = orig_count - t
    print(f"  {name:<25} {t:>8} {c:>8} {acc:>6.1f}% {pnl:>+8d} {filt:>9}")

# Decay analysis: what did the filter catch?
print(f"\n  ADX Decay Filter Analysis:")
caught = [r for r in variants["Alt2 (original)"] if r["decay"]]
passed = [r for r in variants["Alt2 (original)"] if not r["decay"]]
if caught:
    c_ok = sum(1 for r in caught if r["ok"])
    print(f"    CAUGHT (decaying ADX):  {c_ok}/{len(caught)} = {c_ok/len(caught)*100:.1f}% accuracy")
    print(f"    → These were REMOVED (correctly filtering out bad signals)")
if passed:
    p_ok = sum(1 for r in passed if r["ok"])
    print(f"    PASSED (rising/stable):  {p_ok}/{len(passed)} = {p_ok/len(passed)*100:.1f}% accuracy")
    print(f"    → These were KEPT")

# Blacklist analysis
print(f"\n  Blacklist Hours Analysis:")
bl_caught = [r for r in variants["Alt2 (original)"] if r["hour"] in BLACKLIST_HOURS]
bl_passed = [r for r in variants["Alt2 (original)"] if r["hour"] not in BLACKLIST_HOURS]
if bl_caught:
    c_ok = sum(1 for r in bl_caught if r["ok"])
    print(f"    IN blacklist hours:   {c_ok}/{len(bl_caught)} = {c_ok/len(bl_caught)*100:.1f}% accuracy")
if bl_passed:
    p_ok = sum(1 for r in bl_passed if r["ok"])
    print(f"    OUT blacklist hours:  {p_ok}/{len(bl_passed)} = {p_ok/len(bl_passed)*100:.1f}% accuracy")

print(f"\n  Model: binary options, win=+80, loss=-100, stake=100")
print(f"  ADX decay: current < {ADX_LOOKBACK} bars ago × 0.95")
