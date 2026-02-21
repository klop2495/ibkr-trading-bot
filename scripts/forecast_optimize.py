#!/usr/bin/env python3
"""
Forecast Parameter Optimizer

Grid search over indicator parameters to find optimal settings
for each horizon and symbol group.

Runs the forecast backtest engine with different parameter combinations
and reports accuracy for each.

Usage:
    python -m scripts.forecast_optimize --days 30
    python -m scripts.forecast_optimize --days 30 --symbol EURJPY --top 10
    python -m scripts.forecast_optimize --days 30 --horizon 60 --quick

Outputs a ranked table of parameter combos by accuracy.
"""

import argparse
import itertools
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.forecast import FORECAST_HORIZONS
from app.market_data.indicators import rsi as calc_rsi, sma as calc_sma


# ── Voter functions (inlined to support dynamic params) ──────────────

def vote_ma_cross(closes, fast_period, slow_period):
    if len(closes) < slow_period:
        return 0
    fast = calc_sma(closes, fast_period)
    slow = calc_sma(closes, slow_period)
    if fast is None or slow is None:
        return 0
    return 1 if fast > slow else (-1 if fast < slow else 0)


def vote_rsi_trend(closes, period=14, lookback=3):
    if len(closes) < period + 1 + lookback:
        return 0
    rsi_now = calc_rsi(closes, period)
    rsi_prev = calc_rsi(closes[:-lookback], period)
    if rsi_now is None or rsi_prev is None:
        return 0
    if rsi_now > 50 and rsi_now > rsi_prev:
        return 1
    if rsi_now < 50 and rsi_now < rsi_prev:
        return -1
    return 0


def vote_rsi_extreme(closes, period=14, oversold=30.0, overbought=70.0):
    if len(closes) < period + 1:
        return 0
    rsi_val = calc_rsi(closes, period)
    if rsi_val is None:
        return 0
    if rsi_val <= oversold:
        return 1
    if rsi_val >= overbought:
        return -1
    return 0


def vote_price_vs_ma(closes, ma_period):
    if len(closes) < ma_period:
        return 0
    ma = calc_sma(closes, ma_period)
    if ma is None or ma == 0:
        return 0
    return 1 if closes[-1] > ma else (-1 if closes[-1] < ma else 0)


def vote_momentum(closes, lookback):
    if len(closes) < lookback + 1:
        return 0
    current = closes[-1]
    past = closes[-(lookback + 1)]
    if past == 0:
        return 0
    return 1 if current > past else (-1 if current < past else 0)


def aggregate_votes(votes, min_ratio=0.2):
    total = len(votes)
    if total == 0:
        return "neutral", 0.0
    ups = sum(1 for v in votes if v > 0)
    downs = sum(1 for v in votes if v < 0)
    net = ups - downs
    ratio = abs(net) / total
    if ratio < min_ratio:
        return "neutral", ratio
    direction = "up" if net > 0 else "down"
    return direction, ratio


# ── Data fetching (reuses backtest logic) ────────────────────────────

def _round_ts(ts_str, interval_minutes):
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    epoch = int(dt.timestamp())
    rounded = epoch - (epoch % (interval_minutes * 60))
    return datetime.fromtimestamp(rounded, tz=timezone.utc).isoformat()


def fetch_closes(db, symbols, since, until):
    result = {}
    tf_mins = {"M15": 15, "H1": 60, "H4": 240}
    for symbol in symbols:
        result[symbol] = {}
        for tf, interval in tf_mins.items():
            all_rows = []
            cursor = since.isoformat()
            for _ in range(20):
                res = (
                    db.client.table("market_snapshots")
                    .select("ts, close")
                    .eq("symbol", symbol)
                    .eq("timeframe", tf)
                    .gte("ts", cursor)
                    .lte("ts", until.isoformat())
                    .order("ts", desc=False)
                    .limit(1000)
                    .execute()
                )
                rows = res.data or []
                if not rows:
                    break
                all_rows.extend(rows)
                if len(rows) < 1000:
                    break
                cursor = rows[-1]["ts"]

            seen = {}
            for r in all_rows:
                if r.get("close") is None:
                    continue
                key = _round_ts(r["ts"], interval)
                seen[key] = {"ts": key, "close": float(r["close"])}
            result[symbol][tf] = sorted(seen.values(), key=lambda x: x["ts"])
    return result


# ── Parameter grid ───────────────────────────────────────────────────

QUICK_GRID = {
    "ma_fast": [10, 20, 30],
    "ma_slow": [50, 100, 200],
    "momentum_lookback": [5, 10, 20],
    "rsi_period": [14],
    "rsi_lookback": [3],
    "min_ratio": [0.2],
}

FULL_GRID = {
    "ma_fast": [8, 12, 20, 30, 50],
    "ma_slow": [30, 50, 100, 150, 200],
    "momentum_lookback": [3, 6, 10, 15, 24],
    "rsi_period": [7, 10, 14, 21],
    "rsi_lookback": [2, 3, 5],
    "min_ratio": [0.15, 0.2, 0.3],
}

# Map horizon → primary + secondary timeframe
HORIZON_TF = {
    30:   ("M15", None),
    60:   ("H1", "M15"),
    240:  ("H4", "H1"),
    1440: ("H4", "H1"),
}


def build_combos(grid):
    keys = sorted(grid.keys())
    combos = []
    for vals in itertools.product(*(grid[k] for k in keys)):
        combo = dict(zip(keys, vals))
        # Skip invalid: fast must be < slow
        if combo["ma_fast"] >= combo["ma_slow"]:
            continue
        combos.append(combo)
    return combos


# ── Run single backtest with given params ────────────────────────────

MIN_BARS = 30  # Lower than production for more data points


def run_single_backtest(
    data: Dict[str, Dict[str, List[Dict]]],
    symbols: List[str],
    horizon: int,
    params: Dict[str, Any],
    backtest_start: datetime,
) -> Dict[str, Any]:
    """Run backtest for one param combo on one horizon. Returns accuracy stats."""
    primary_tf, secondary_tf = HORIZON_TF[horizon]
    total = 0
    correct = 0

    for sym in symbols:
        m15_bars = data.get(sym, {}).get("M15", [])
        if len(m15_bars) < MIN_BARS + 10:
            continue

        # Build closes per timeframe
        def get_closes_up_to(tf, idx):
            if tf == "M15":
                return [b["close"] for b in m15_bars[:idx]]
            # For H1/H4, use all bars with ts <= current M15 bar ts
            current_ts = m15_bars[idx - 1]["ts"] if idx > 0 else ""
            tf_bars = data.get(sym, {}).get(tf, [])
            return [b["close"] for b in tf_bars if b["ts"] <= current_ts]

        for i in range(MIN_BARS, len(m15_bars)):
            bar = m15_bars[i]
            bar_dt = datetime.fromisoformat(bar["ts"].replace("Z", "+00:00"))
            if bar_dt < backtest_start:
                continue
            if i % 4 != 0:
                continue

            closes = get_closes_up_to(primary_tf, i)
            if len(closes) < MIN_BARS and secondary_tf:
                closes = get_closes_up_to(secondary_tf, i)
            if len(closes) < 15:
                continue

            # Run votes with given params
            votes = []
            votes.append(vote_ma_cross(closes, params["ma_fast"], params["ma_slow"]))
            votes.append(vote_rsi_trend(closes, params["rsi_period"], params["rsi_lookback"]))
            if horizon <= 240:
                votes.append(vote_rsi_extreme(closes, params["rsi_period"]))
            votes.append(vote_price_vs_ma(closes, params["ma_fast"]))
            votes.append(vote_momentum(closes, params["momentum_lookback"]))

            # Secondary TF confirmation
            if secondary_tf:
                sec_closes = get_closes_up_to(secondary_tf, i)
                if len(sec_closes) >= params["ma_slow"]:
                    votes.append(vote_ma_cross(sec_closes, params["ma_fast"], params["ma_slow"]))

            direction, strength = aggregate_votes(votes, params["min_ratio"])
            if direction == "neutral":
                continue

            # Check actual outcome
            bars_ahead = horizon // 15
            target_idx = i + bars_ahead
            if target_idx >= len(m15_bars):
                continue

            actual_price = m15_bars[target_idx]["close"]
            base_price = closes[-1]
            price_change = actual_price - base_price

            if abs(price_change) < 1e-6:
                actual_dir = "neutral"
            else:
                actual_dir = "up" if price_change > 0 else "down"

            if direction == actual_dir:
                correct += 1
            total += 1

    accuracy = correct / total if total > 0 else 0
    return {"total": total, "correct": correct, "accuracy": accuracy}


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Forecast Parameter Optimizer")
    parser.add_argument("--days", type=int, default=30, help="Days of historical data")
    parser.add_argument("--symbol", type=str, default=None, help="Single symbol")
    parser.add_argument("--horizon", type=int, default=None, help="Single horizon (30/60/240/1440)")
    parser.add_argument("--top", type=int, default=15, help="Show top N results")
    parser.add_argument("--quick", action="store_true", help="Use quick (smaller) grid")
    parser.add_argument("--save", action="store_true", help="Print recommended config")
    args = parser.parse_args()

    from app.storage.db import SupabaseDB
    from app.models.bot_settings import DEFAULT_SYMBOLS

    db = SupabaseDB()
    symbols = [args.symbol.upper()] if args.symbol else DEFAULT_SYMBOLS
    horizons = [args.horizon] if args.horizon else FORECAST_HORIZONS

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.days + 2)
    backtest_start = until - timedelta(days=args.days)

    grid = QUICK_GRID if args.quick else FULL_GRID
    combos = build_combos(grid)

    print(f"Forecast Parameter Optimizer")
    print(f"  Period: {args.days} days")
    print(f"  Symbols: {len(symbols)}")
    print(f"  Horizons: {horizons}")
    print(f"  Grid: {'quick' if args.quick else 'full'} ({len(combos)} combos)")
    print(f"  Fetching data...")

    data = fetch_closes(db, symbols, since, until)
    total_bars = sum(len(data.get(s, {}).get("M15", [])) for s in symbols)
    print(f"  Total M15 bars: {total_bars}")

    if total_bars == 0:
        print("ERROR: No data.")
        sys.exit(1)

    # Run grid search per horizon
    for horizon in horizons:
        print(f"\n{'='*72}")
        print(f"HORIZON h{horizon} ({horizon}m)")
        print(f"{'='*72}")

        results = []
        t0 = time.time()

        for idx, params in enumerate(combos):
            res = run_single_backtest(data, symbols, horizon, params, backtest_start)
            results.append({**params, **res})
            if (idx + 1) % 50 == 0:
                elapsed = time.time() - t0
                print(f"  ... {idx+1}/{len(combos)} combos tested ({elapsed:.1f}s)")

        elapsed = time.time() - t0
        print(f"  Tested {len(combos)} combos in {elapsed:.1f}s")

        # Sort by accuracy (descending), then by total predictions (descending)
        results.sort(key=lambda r: (r["accuracy"], r["total"]), reverse=True)

        # Print top N
        print(f"\n  Top {args.top} parameter combinations:")
        print(f"  {'Rank':>4}  {'Accuracy':>8}  {'N':>5}  {'MA_f':>4}  {'MA_s':>5}  {'Mom':>3}  {'RSI_p':>5}  {'RSI_lb':>6}  {'MinR':>5}")
        print(f"  {'-'*60}")

        for rank, r in enumerate(results[:args.top], 1):
            if r["total"] == 0:
                continue
            print(
                f"  {rank:>4}  {r['accuracy']*100:>7.1f}%  {r['total']:>5}  "
                f"{r['ma_fast']:>4}  {r['ma_slow']:>5}  {r['momentum_lookback']:>3}  "
                f"{r['rsi_period']:>5}  {r['rsi_lookback']:>6}  {r['min_ratio']:>5.2f}"
            )

        # Also show current defaults for comparison
        current = {"ma_fast": 20, "ma_slow": 50, "momentum_lookback": 10, "rsi_period": 14, "rsi_lookback": 3, "min_ratio": 0.2}
        if horizon == 1440:
            current["ma_fast"] = 50
            current["ma_slow"] = 200
            current["momentum_lookback"] = 24
        cur_res = run_single_backtest(data, symbols, horizon, current, backtest_start)
        print(f"\n  Current defaults: accuracy={cur_res['accuracy']*100:.1f}% (N={cur_res['total']})")

        if results and results[0]["total"] > 0 and cur_res["total"] > 0:
            improvement = results[0]["accuracy"] - cur_res["accuracy"]
            print(f"  Best improvement: {improvement*100:+.1f}pp")

        # Print recommended config
        if args.save and results and results[0]["total"] >= 50:
            best = results[0]
            print(f"\n  Recommended HORIZON_CONFIG[{horizon}]:")
            print(f'    {horizon}: {{"primary_tf": "{HORIZON_TF[horizon][0]}", '
                  f'"secondary_tf": {repr(HORIZON_TF[horizon][1])}, '
                  f'"momentum_lookback": {best["momentum_lookback"]}, '
                  f'"ma_fast": {best["ma_fast"]}, '
                  f'"ma_slow": {best["ma_slow"]}}},')


if __name__ == "__main__":
    main()
