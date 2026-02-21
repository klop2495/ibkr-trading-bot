#!/usr/bin/env python3
"""
Forecast Backtest Script

Runs the forecast engine on historical data stored in Supabase (market_snapshots)
and compares predictions with actual price movements.

Uses only 'close' prices from market_snapshots (open/high/low not available).
ATR-based vote is skipped; 5 of 6 indicators are used.

Usage:
    python -m scripts.forecast_backtest --days 7 --symbol EURUSD
    python -m scripts.forecast_backtest --days 30
    python -m scripts.forecast_backtest --days 14 --symbol GBPUSD --save --verbose
"""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.forecast import FORECAST_HORIZONS
from app.forecast.indicators_vote import (
    aggregate_votes,
    vote_ma_cross,
    vote_momentum,
    vote_price_vs_ma,
    vote_rsi_extreme,
    vote_rsi_trend,
)
from app.forecast.engine import HORIZON_CONFIG, MIN_BARS_REQUIRED


def _round_ts(ts_str: str, interval_minutes: int) -> str:
    """Round a timestamp string down to the nearest interval."""
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    epoch = int(dt.timestamp())
    rounded = epoch - (epoch % (interval_minutes * 60))
    return datetime.fromtimestamp(rounded, tz=timezone.utc).isoformat()


def _tf_minutes(tf: str) -> int:
    return {"M15": 15, "H1": 60, "H4": 240}[tf]


def fetch_closes(
    db: Any,
    symbols: List[str],
    since: datetime,
    until: datetime,
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Fetch close prices from market_snapshots with pagination and dedup into bars."""
    result: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    for symbol in symbols:
        result[symbol] = {}
        for tf in ("M15", "H1", "H4"):
            interval = _tf_minutes(tf)
            all_rows: List[Dict[str, Any]] = []
            cursor = since.isoformat()
            page_limit = 1000
            max_pages = 20

            try:
                for _ in range(max_pages):
                    res = (
                        db.client.table("market_snapshots")
                        .select("ts, close")
                        .eq("symbol", symbol)
                        .eq("timeframe", tf)
                        .gte("ts", cursor)
                        .lte("ts", until.isoformat())
                        .order("ts", desc=False)
                        .limit(page_limit)
                        .execute()
                    )
                    rows = res.data or []
                    if not rows:
                        break
                    all_rows.extend(rows)
                    if len(rows) < page_limit:
                        break
                    # Move cursor past last row
                    cursor = rows[-1]["ts"]

                # Deduplicate: keep last close per rounded interval
                seen: Dict[str, Dict[str, Any]] = {}
                for r in all_rows:
                    if r.get("close") is None:
                        continue
                    key = _round_ts(r["ts"], interval)
                    seen[key] = {"ts": key, "close": float(r["close"])}

                bars = sorted(seen.values(), key=lambda x: x["ts"])
                result[symbol][tf] = bars
            except Exception as exc:
                print(f"  Warning: Failed to fetch {symbol}/{tf}: {exc}")
                result[symbol][tf] = []

    return result


def run_forecast_at_index(
    symbol: str,
    data: Dict[str, Dict[str, List[Dict[str, Any]]]],
    m15_index: int,
) -> Optional[Dict[str, Any]]:
    """Run forecast using close-only data at a given M15 bar index."""
    # Build closes cache per timeframe up to m15_index
    closes_cache: Dict[str, List[float]] = {}

    m15_bars = data.get(symbol, {}).get("M15", [])
    if m15_index > len(m15_bars):
        return None

    m15_closes = [b["close"] for b in m15_bars[:m15_index]]
    closes_cache["M15"] = m15_closes

    # For H1/H4 use all bars up to the timestamp of current M15 bar
    if m15_index > 0:
        current_ts = m15_bars[m15_index - 1]["ts"]
        for tf in ("H1", "H4"):
            tf_bars = data.get(symbol, {}).get(tf, [])
            tf_closes = [b["close"] for b in tf_bars if b["ts"] <= current_ts]
            closes_cache[tf] = tf_closes

    # Check minimum data
    available = {tf: c for tf, c in closes_cache.items() if len(c) >= MIN_BARS_REQUIRED}
    if not available:
        return None

    base_price = None
    for tf in ("M15", "H1", "H4"):
        if closes_cache.get(tf):
            base_price = closes_cache[tf][-1]
            break
    if base_price is None:
        return None

    horizons = {}
    for horizon_min in FORECAST_HORIZONS:
        config = HORIZON_CONFIG[horizon_min]
        primary_tf = config["primary_tf"]
        secondary_tf = config["secondary_tf"]
        momentum_lookback = config["momentum_lookback"]
        ma_fast = config["ma_fast"]
        ma_slow = config["ma_slow"]

        closes = closes_cache.get(primary_tf, [])
        if len(closes) < MIN_BARS_REQUIRED and secondary_tf:
            closes = closes_cache.get(secondary_tf, [])

        if len(closes) < 15:
            horizons[horizon_min] = {"direction": "neutral", "strength": 0.0}
            continue

        votes: List[int] = []
        votes.append(vote_ma_cross(closes, ma_fast, ma_slow))
        votes.append(vote_rsi_trend(closes))
        if horizon_min <= 240:
            votes.append(vote_rsi_extreme(closes))
        votes.append(vote_price_vs_ma(closes, ma_fast))
        votes.append(vote_momentum(closes, momentum_lookback))
        # ATR vote skipped — no OHLC data available

        if secondary_tf:
            sec_closes = closes_cache.get(secondary_tf, [])
            if len(sec_closes) >= ma_slow:
                votes.append(vote_ma_cross(sec_closes, ma_fast, ma_slow))

        direction_str, confidence_str, strength, aligned, total = aggregate_votes(votes)
        horizons[horizon_min] = {"direction": direction_str, "strength": strength}

    return {"base_price": base_price, "horizons": horizons}


def main():
    parser = argparse.ArgumentParser(description="Forecast Backtest")
    parser.add_argument("--days", type=int, default=7, help="Days to backtest")
    parser.add_argument("--symbol", type=str, default=None, help="Single symbol")
    parser.add_argument("--save", action="store_true", help="Save results to DB")
    parser.add_argument("--verbose", action="store_true", help="Print details")
    args = parser.parse_args()

    from app.storage.db import SupabaseDB
    from app.models.bot_settings import DEFAULT_SYMBOLS

    db = SupabaseDB()
    if not db.ping():
        print("ERROR: Cannot connect to Supabase")
        sys.exit(1)

    symbols = [args.symbol.upper()] if args.symbol else DEFAULT_SYMBOLS
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.days + 2)

    print(f"Forecast Backtest")
    print(f"  Period: {args.days} days")
    print(f"  Symbols: {len(symbols)}")
    print(f"  Note: ATR vote skipped (no OHLC in snapshots)")
    print(f"  Fetching historical data...")

    data = fetch_closes(db, symbols, since, until)

    total_bars = 0
    for sym in symbols:
        m15_count = len(data.get(sym, {}).get("M15", []))
        total_bars += m15_count
        if args.verbose:
            h1 = len(data.get(sym, {}).get("H1", []))
            h4 = len(data.get(sym, {}).get("H4", []))
            print(f"  {sym}: M15={m15_count} H1={h1} H4={h4}")
    print(f"  Total M15 bars: {total_bars}")

    if total_bars == 0:
        print("ERROR: No historical data available. Run bot with market data first.")
        sys.exit(1)

    backtest_start = until - timedelta(days=args.days)
    results: Dict[str, Dict[int, Dict[str, int]]] = {}

    for sym in symbols:
        results[sym] = {h: {"total": 0, "correct": 0} for h in FORECAST_HORIZONS}

        m15_bars = data.get(sym, {}).get("M15", [])
        if len(m15_bars) < MIN_BARS_REQUIRED + 10:
            if args.verbose:
                print(f"  {sym}: Skipping (insufficient data: {len(m15_bars)} bars)")
            continue

        for i in range(MIN_BARS_REQUIRED, len(m15_bars)):
            bar = m15_bars[i]
            bar_ts = bar["ts"]
            if isinstance(bar_ts, str):
                bar_dt = datetime.fromisoformat(bar_ts.replace("Z", "+00:00"))
            else:
                bar_dt = bar_ts

            if bar_dt < backtest_start:
                continue

            # Every 4th bar (hourly)
            if i % 4 != 0:
                continue

            forecast = run_forecast_at_index(sym, data, i)
            if forecast is None:
                continue

            base_price = forecast["base_price"]

            for horizon_min, h_data in forecast["horizons"].items():
                predicted = h_data["direction"]
                if predicted == "neutral":
                    continue

                bars_ahead = horizon_min // 15
                target_idx = i + bars_ahead
                if target_idx >= len(m15_bars):
                    continue

                actual_price = m15_bars[target_idx]["close"]
                price_change = actual_price - base_price

                if abs(price_change) < 1e-6:
                    actual_dir = "neutral"
                elif price_change > 0:
                    actual_dir = "up"
                else:
                    actual_dir = "down"

                is_correct = (predicted == actual_dir)
                results[sym][horizon_min]["total"] += 1
                if is_correct:
                    results[sym][horizon_min]["correct"] += 1

                if args.verbose and results[sym][horizon_min]["total"] <= 2:
                    icon = "✅" if is_correct else "❌"
                    print(f"    {sym} h{horizon_min}: pred={predicted} actual={actual_dir} {icon}")

    # Results
    print("\n" + "=" * 72)
    print("BACKTEST RESULTS")
    print("=" * 72)

    header = f"{'Symbol':<10}"
    for h in FORECAST_HORIZONS:
        header += f" {'h'+str(h):>12}"
    print(header)
    print("-" * 72)

    grand: Dict[int, Dict[str, int]] = {h: {"total": 0, "correct": 0} for h in FORECAST_HORIZONS}

    for sym in sorted(symbols):
        line = f"{sym:<10}"
        for h in FORECAST_HORIZONS:
            t = results[sym][h]["total"]
            c = results[sym][h]["correct"]
            grand[h]["total"] += t
            grand[h]["correct"] += c
            if t > 0:
                pct = c / t * 100
                line += f" {pct:>6.1f}%({t:>3})"
            else:
                line += f"{'—':>12}"
        print(line)

    print("-" * 72)
    line = f"{'TOTAL':<10}"
    all_t = 0
    all_c = 0
    for h in FORECAST_HORIZONS:
        t = grand[h]["total"]
        c = grand[h]["correct"]
        all_t += t
        all_c += c
        if t > 0:
            line += f" {c/t*100:>6.1f}%({t:>3})"
        else:
            line += f"{'—':>12}"
    print(line)

    if all_t > 0:
        print(f"\nOverall accuracy: {all_c/all_t*100:.1f}% ({all_c}/{all_t})")
    else:
        print("\nNo predictions to evaluate.")

    if args.save and all_t > 0:
        try:
            payload = {
                "ts_utc": datetime.now(timezone.utc).isoformat(),
                "days": args.days,
                "symbols": symbols,
                "total_predictions": all_t,
                "total_correct": all_c,
                "overall_accuracy": round(all_c / all_t, 4),
                "per_horizon": {
                    str(h): {
                        "total": grand[h]["total"],
                        "correct": grand[h]["correct"],
                        "accuracy": round(grand[h]["correct"] / grand[h]["total"], 4) if grand[h]["total"] > 0 else 0,
                    }
                    for h in FORECAST_HORIZONS
                },
                "per_symbol": {
                    sym: {str(h): results[sym][h] for h in FORECAST_HORIZONS}
                    for sym in symbols
                },
            }
            db.client.table("forecast_backtests").insert(payload).execute()
            print(f"\nResults saved to forecast_backtests table.")
        except Exception as exc:
            print(f"\nWarning: Failed to save: {exc}")


if __name__ == "__main__":
    main()
