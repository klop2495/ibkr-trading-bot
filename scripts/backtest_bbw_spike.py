"""
BBW Spike Filter Backtest — Feb 27, 2026.
Compares: Alt2 | Alt2+BBW_spike | Alt2+blacklist | Alt2+both
BBW spike = Bollinger Band Width rate of change detects session transitions.
"""
import os, sys, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ── Indicators ───────────────────────────────────────────────

def sma(data, period):
    if len(data) < period:
        return None
    return sum(data[-period:]) / period

def bollinger_bands(closes, period=20, std_mult=2.0):
    if len(closes) < period:
        return None
    data = closes[-period:]
    middle = sum(data) / period
    if middle == 0:
        return None
    variance = sum((x - middle) ** 2 for x in data) / period
    std = variance ** 0.5
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    width = (upper - lower) / middle
    return {"width": width, "upper": upper, "lower": lower, "middle": middle}

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

ALT2_SYMBOLS = {"EURCHF","CHFJPY","EURJPY","AUDJPY","CADJPY","NZDJPY","EURGBP","USDJPY"}
BLACKLIST_HOURS = {6, 8, 13, 14, 18}

def compute_alt2_core(symbol, closes, highs, lows, ma_fast=20, ma_slow=50):
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
            ib.connectAsync(host, port, clientId=173, timeout=15, readonly=True)
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
BBW_LOOKBACK = 3          # compare BBW now vs N bars ago
BBW_SPIKE_THRESHOLDS = [1.3, 1.5, 1.8, 2.0]  # test multiple thresholds

print("=" * 90)
print(f"BBW SPIKE FILTER BACKTEST — {TARGET_DATE}")
print(f"BBW spike = BBW_now > BBW_{BBW_LOOKBACK}_bars_ago × threshold")
print(f"Testing thresholds: {BBW_SPIKE_THRESHOLDS}")
print(f"Blacklist hours: {sorted(BLACKLIST_HOURS)}")
print("=" * 90)

all_symbols = sorted(ALT2_SYMBOLS)
print(f"\nSymbols: {all_symbols}")
ohlc_data = fetch_ohlc_from_ib(all_symbols, duration="2 D")

if not ohlc_data:
    print("No data fetched. Exiting.")
    sys.exit(1)

# ── Collect all Alt2 signals with BBW data ───────────────────

all_signals = []

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

    # Precompute BBW for every bar
    bbw_cache = {}
    for i in range(max(20, start_idx - BBW_LOOKBACK - 5), len(bars)):
        c_s = closes[:i+1]
        bb = bollinger_bands(c_s, period=20, std_mult=2.0)
        bbw_cache[i] = bb["width"] if bb else None

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
            continue

        c_slice = closes[:i+1]
        h_slice = highs[:i+1]
        l_slice = lows[:i+1]

        direction, adx_now, ma, pv = compute_alt2_core(sym, c_slice, h_slice, l_slice)
        if direction is None:
            continue

        bbw_now = bbw_cache.get(i)
        bbw_prev = bbw_cache.get(i - BBW_LOOKBACK)
        bbw_ratio = bbw_now / bbw_prev if (bbw_now and bbw_prev and bbw_prev > 0) else None

        ok = (direction == actual)
        is_blacklisted = hour in BLACKLIST_HOURS

        all_signals.append({
            "ts": ts, "sym": sym, "dir": direction, "actual": actual,
            "ok": ok, "adx": adx_now, "hour": hour,
            "bbw": bbw_now, "bbw_prev": bbw_prev, "bbw_ratio": bbw_ratio,
            "blacklisted": is_blacklisted,
        })


# ── Print detailed signal table ──────────────────────────────

print(f"\n{'='*95}")
print(f"  ALL ALT2 SIGNALS WITH BBW DATA")
print(f"{'='*95}")
print(f"\n  {'TS':<18} {'SYM':<9} {'DIR':<5} {'ACT':<5} {'OK':<3} {'ADX':>5} {'BBW':>8} {'BBWp':>8} {'Ratio':>6} {'H':>2}")
print(f"  {'-'*88}")
for s in all_signals:
    ok_s = "✓" if s["ok"] else "✗"
    bbw_s = f"{s['bbw']:.5f}" if s['bbw'] else "—"
    bbwp_s = f"{s['bbw_prev']:.5f}" if s['bbw_prev'] else "—"
    ratio_s = f"{s['bbw_ratio']:.2f}" if s['bbw_ratio'] else "—"
    bl = "BL" if s["blacklisted"] else ""
    print(f"  {s['ts']:<18} {s['sym']:<9} {s['dir']:<5} {s['actual']:<5} {ok_s:<3} {s['adx']:>5.1f} {bbw_s:>8} {bbwp_s:>8} {ratio_s:>6} {s['hour']:>2} {bl}")


# ── Comparison across thresholds ─────────────────────────────

print(f"\n{'='*95}")
print(f"  COMPARISON — BBW SPIKE FILTER THRESHOLDS")
print(f"{'='*95}")

# Build variants
def eval_variant(signals, name, filter_fn):
    kept = [s for s in signals if filter_fn(s)]
    t = len(kept)
    c = sum(1 for s in kept if s["ok"])
    acc = c / t * 100 if t else 0
    pnl = c * 80 - (t - c) * 100
    filtered = len(signals) - t
    return {"name": name, "total": t, "correct": c, "acc": acc, "pnl": pnl, "filtered": filtered, "signals": kept}

variants = []

# Original
variants.append(eval_variant(all_signals, "Alt2 (original)", lambda s: True))

# Blacklist only
variants.append(eval_variant(all_signals, "Alt2 + blacklist", lambda s: not s["blacklisted"]))

# BBW spike at different thresholds
for thr in BBW_SPIKE_THRESHOLDS:
    name = f"Alt2 + BBW_spike>{thr}"
    variants.append(eval_variant(all_signals, name,
        lambda s, t=thr: s["bbw_ratio"] is None or s["bbw_ratio"] <= t))

# BBW spike + blacklist combo at each threshold
for thr in BBW_SPIKE_THRESHOLDS:
    name = f"Alt2 + BBW>{thr} + BL"
    variants.append(eval_variant(all_signals, name,
        lambda s, t=thr: (not s["blacklisted"]) and (s["bbw_ratio"] is None or s["bbw_ratio"] <= t)))

print(f"\n  {'Variant':<30} {'Signals':>8} {'Correct':>8} {'Acc':>7} {'P&L':>8} {'Filtered':>9}")
print(f"  {'-'*74}")
for v in variants:
    print(f"  {v['name']:<30} {v['total']:>8} {v['correct']:>8} {v['acc']:>6.1f}% {v['pnl']:>+8d} {v['filtered']:>9}")


# ── BBW Spike Analysis: what does the filter catch? ──────────

print(f"\n{'='*95}")
print(f"  BBW SPIKE ANALYSIS (threshold = 1.5)")
print(f"{'='*95}")

spike_15 = [s for s in all_signals if s["bbw_ratio"] and s["bbw_ratio"] > 1.5]
no_spike_15 = [s for s in all_signals if s["bbw_ratio"] is None or s["bbw_ratio"] <= 1.5]

if spike_15:
    c = sum(1 for s in spike_15 if s["ok"])
    print(f"\n  CAUGHT (BBW spike > 1.5):  {c}/{len(spike_15)} = {c/len(spike_15)*100:.1f}% accuracy")
    print(f"  These signals had sudden volatility expansion:")
    for s in spike_15:
        ok_s = "✓" if s["ok"] else "✗"
        print(f"    {s['ts']} {s['sym']:<9} {s['dir']:<5} {ok_s} BBW={s['bbw']:.5f} prev={s['bbw_prev']:.5f} ratio={s['bbw_ratio']:.2f}  h={s['hour']:02d}{'  BL' if s['blacklisted'] else ''}")

if no_spike_15:
    c = sum(1 for s in no_spike_15 if s["ok"])
    print(f"\n  KEPT (no spike):  {c}/{len(no_spike_15)} = {c/len(no_spike_15)*100:.1f}% accuracy")

# Blacklist vs BBW overlap
print(f"\n  OVERLAP ANALYSIS:")
bl_signals = [s for s in all_signals if s["blacklisted"]]
bbw_spike = [s for s in all_signals if s["bbw_ratio"] and s["bbw_ratio"] > 1.5]
overlap = [s for s in all_signals if s["blacklisted"] and s.get("bbw_ratio") and s["bbw_ratio"] > 1.5]
bl_only = [s for s in bl_signals if not (s.get("bbw_ratio") and s["bbw_ratio"] > 1.5)]
bbw_only = [s for s in bbw_spike if not s["blacklisted"]]
print(f"    Blacklist catches:       {len(bl_signals)} signals")
print(f"    BBW spike>1.5 catches:   {len(bbw_spike)} signals")
print(f"    Overlap (both catch):    {len(overlap)} signals")
print(f"    Blacklist ONLY catches:  {len(bl_only)} signals")
print(f"    BBW spike ONLY catches:  {len(bbw_only)} signals")

if bbw_only:
    c = sum(1 for s in bbw_only if s["ok"])
    print(f"\n  BBW-only catches (outside blacklist hours) — {c}/{len(bbw_only)} = {c/len(bbw_only)*100:.1f}%:")
    for s in bbw_only:
        ok_s = "✓" if s["ok"] else "✗"
        print(f"    {s['ts']} {s['sym']:<9} {s['dir']:<5} {ok_s} ratio={s['bbw_ratio']:.2f} h={s['hour']:02d}")

print(f"\n  Model: binary options, win=+80, loss=-100, stake=100")
