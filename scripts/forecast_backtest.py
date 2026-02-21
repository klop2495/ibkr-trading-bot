#!/usr/bin/env python3
"""
Forecast Backtest Script

Runs the forecast engine on historical data stored in Supabase (market_snapshots)
and compares predictions with actual price movements.

Usage:
    python -m scripts.forecast_backtest --days 7 --symbol EURUSD
    python -m scripts.forecast_backtest --days 30
    python -m scripts.forecast_backtest --days 14 --symbol GBPUSD --save

Options:
    --days N        Number of days to backtest (default: 7)
    --symbol SYM    Backtest only one symbol (default: all)
    --save          Save results to Supabase forecast_backtests table
    --verbose       Print per-forecast details
"""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.forecast import FORECAST_HORIZONS
from app.forecast.indicators_vote import (
    aggregate_votes,
    vote_atr_trend,
    vote_ma_cross,
    vote_momentum,
    vote_price_vs_ma,
    vote_rsi_extreme,
    vote_rsi_trend,
)
from app.forecast.engine import HORIZON_CONFIG, MIN_BARS_REQUIRED
from app.market_data.indicators import sma as calc_sma


class MockMarketDataService:
    """Provides OHLC data from a snapshot dictionary for a given point in time."""

    def __init__(self, snapshots: Dict[str, Dict[str, List[Dict[str, Any]]]]):
        self._data = snapshots

    def get_ohlc(self, symbol: str, timeframe: str, n_bars: int = 250, up_to_idx: int = -1) -> Optional[Dict]:
        rows = self._data.get(symbol, {}).get(timeframe, [])
        if not rows:
            return None
        end = up_to_idx if up_to_idx >= 0 else len(rows)
        start = max(0, end - n_bars)
        subset = rows[start:end]
        if not subset:
            return None
        return {
            "opens": [r["open"] for r in subset],
            "highs": [r["high"] for r in subset],
            "lows": [r["low"] for r in subset],
            "closes": [r["close"] for r in subset],
        }


def fetch_historical_snapshots(
    db: Any,
    symbols: List[str],
    since: datetime,
    until: datetime,
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Fetch market_snapshots from Supabase, grouped by symbol+timeframe."""
    result: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    for symbol in symbols:
        result[symbol] = {}
        for tf in ("M15", "H1", "H4"):
            try:
                res = (
                    db.client.table("market_snapshots")
                    .select("ts, open, high, low, close")
                    .eq("symbol", symbol)
                    .eq("timeframe", tf)
                    .gte("ts", since.isoformat())
                    .lte("ts", until.isoformat())
                    .order("ts", desc=False)
                    .limit(5000)
                    .execute()
                )
                rows = res.data or []
                result[symbol][tf] = [
                    {
                        "ts": r["ts"],
                        "open": float(r["open"]),
                        "high": float(r["high"]),
                        "low": float(r["low"]),
                        "close": float(r["close"]),
                    }
                    for r in rows
                    if r.get("close") is not None
                ]
            except Exception as exc:
                print(f"  Warning: Failed to fetch {symbol}/{tf}: {exc}")
                result[symbol][tf] = []

    return result


def run_forecast_at_index(
    symbol: str,
    mds: MockMarketDataService,
    bar_index: int,
) -> Optional[Dict[str, Any]]:
    """Run forecast engine logic for a single symbol at a given bar index."""
    bars_cache: Dict[str, Dict[str, List[float]]] = {}
    for tf in ("M15", "H1", "H4"):
        ohlc = mds.get_ohlc(symbol, tf, n_bars=250, up_to_idx=bar_index)
        if ohlc and ohlc.get("closes"):
            bars_cache[tf] = ohlc
        else:
            bars_cache[tf] = {"opens": [], "highs": [], "lows": [], "closes": []}

    available_tfs = [tf for tf, d in bars_cache.items() if len(d.get("closes", [])) >= MIN_BARS_REQUIRED]
    if not available_tfs:
        return None

    base_price = None
    for tf in ("M15", "H1", "H4"):
        closes = bars_cache.get(tf, {}).get("closes", [])
        if closes:
            base_price = closes[-1]
            break

    if base_price is None:
        return None

    horizons = {}
    for horizon_min in FORECAST_HORIZONS:
        config = HORIZON_CONFIG[horizon_min]
        primary_tf = config["primary_tf"]
        secondary_tf = config["secondary_tf"]
        momentum_lookback = config["momentum_lookback"]
        ma_fast_period = config["ma_fast"]
        ma_slow_period = config["ma_slow"]

        primary = bars_cache.get(primary_tf, {})
        closes = primary.get("closes", [])
        highs = primary.get("highs", [])
        lows = primary.get("lows", [])

        if len(closes) < MIN_BARS_REQUIRED and secondary_tf:
            secondary = bars_cache.get(secondary_tf, {})
            closes = secondary.get("closes", [])
            highs = secondary.get("highs", [])
            lows = secondary.get("lows", [])

        if len(closes) < 15:
            horizons[horizon_min] = {"direction": "neutral", "strength": 0.0}
            continue

        votes: List[int] = []
        votes.append(vote_ma_cross(closes, ma_fast_period, ma_slow_period))
        votes.append(vote_rsi_trend(closes))
        if horizon_min <= 240:
            votes.append(vote_rsi_extreme(closes))
        votes.append(vote_price_vs_ma(closes, ma_fast_period))
        votes.append(vote_momentum(closes, momentum_lookback))
        if len(highs) >= 20 and len(lows) >= 20:
            ma_f = calc_sma(closes, ma_fast_period)
            ma_s = calc_sma(closes, ma_slow_period)
            votes.append(vote_atr_trend(highs, lows, closes, ma_f, ma_s))
        if secondary_tf:
            sec_data = bars_cache.get(secondary_tf, {})
            sec_closes = sec_data.get("closes", [])
            if len(sec_closes) >= ma_slow_period:
                votes.append(vote_ma_cross(sec_closes, ma_fast_period, ma_slow_period))

        direction_str, confidence_str, strength, aligned, total = aggregate_votes(votes)
        horizons[horizon_min] = {"direction": direction_str, "strength": strength}

    return {"base_price": base_price, "horizons": horizons}


def main():
    parser = argparse.ArgumentParser(description="Forecast Backtest")
    parser.add_argument("--days", type=int, default=7, help="Days to backtest")
    parser.add_argument("--symbol", type=str, default=None, help="Single symbol (default: all)")
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
    since = until - timedelta(days=args.days + 2)  # Extra days for warmup

    print(f"Forecast Backtest")
    print(f"  Period: {args.days} days")
    print(f"  Symbols: {len(symbols)}")
    print(f"  Fetching historical data...")

    snapshots = fetch_historical_snapshots(db, symbols, since, until)

    # Count available data
    total_bars = 0
    for sym in symbols:
        m15_count = len(snapshots.get(sym, {}).get("M15", []))
        total_bars += m15_count
        if args.verbose:
            print(f"  {sym}: M15={m15_count} bars")
    print(f"  Total M15 bars: {total_bars}")

    if total_bars == 0:
        print("ERROR: No historical data available. Run bot with market data first.")
        sys.exit(1)

    # Run backtest: step through M15 bars, generate forecast, check outcome
    backtest_start = until - timedelta(days=args.days)
    results: Dict[str, Dict[int, Dict[str, int]]] = {}

    for sym in symbols:
        results[sym] = {}
        for h in FORECAST_HORIZONS:
            results[sym][h] = {"total": 0, "correct": 0}

        m15_bars = snapshots.get(sym, {}).get("M15", [])
        if len(m15_bars) < MIN_BARS_REQUIRED + 10:
            print(f"  {sym}: Skipping (insufficient data: {len(m15_bars)} bars)")
            continue

        mds = MockMarketDataService(snapshots)

        # Step through every 4th M15 bar (= every hour) within backtest period
        for i in range(MIN_BARS_REQUIRED, len(m15_bars)):
            bar = m15_bars[i]
            bar_ts = bar["ts"]
            if isinstance(bar_ts, str):
                bar_dt = datetime.fromisoformat(bar_ts.replace("Z", "+00:00"))
            else:
                bar_dt = bar_ts

            if bar_dt < backtest_start:
                continue

            # Only run every 4th bar (hourly) to save compute
            if i % 4 != 0:
                continue

            forecast = run_forecast_at_index(sym, mds, i)
            if forecast is None:
                continue

            base_price = forecast["base_price"]

            for horizon_min, h_data in forecast["horizons"].items():
                predicted = h_data["direction"]
                if predicted == "neutral":
                    continue

                # Find actual price at horizon end
                bars_ahead = horizon_min // 15  # M15 bars
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

                if args.verbose and results[sym][horizon_min]["total"] <= 3:
                    print(f"  {sym} {bar_ts} h{horizon_min}: pred={predicted} actual={actual_dir} {'✅' if is_correct else '❌'}")

    # Print results
    print("\n" + "=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)

    header = f"{'Symbol':<10}"
    for h in FORECAST_HORIZONS:
        label = f"h{h}"
        header += f" {label:>12}"
    print(header)
    print("-" * 70)

    grand_totals: Dict[int, Dict[str, int]] = {h: {"total": 0, "correct": 0} for h in FORECAST_HORIZONS}

    for sym in sorted(symbols):
        line = f"{sym:<10}"
        for h in FORECAST_HORIZONS:
            t = results[sym][h]["total"]
            c = results[sym][h]["correct"]
            grand_totals[h]["total"] += t
            grand_totals[h]["correct"] += c
            if t > 0:
                pct = c / t * 100
                line += f" {pct:>6.1f}%({t:>3})"
            else:
                line += f"{'—':>12}"
        print(line)

    print("-" * 70)
    line = f"{'TOTAL':<10}"
    all_total = 0
    all_correct = 0
    for h in FORECAST_HORIZONS:
        t = grand_totals[h]["total"]
        c = grand_totals[h]["correct"]
        all_total += t
        all_correct += c
        if t > 0:
            pct = c / t * 100
            line += f" {pct:>6.1f}%({t:>3})"
        else:
            line += f"{'—':>12}"
    print(line)

    if all_total > 0:
        overall = all_correct / all_total * 100
        print(f"\nOverall accuracy: {overall:.1f}% ({all_correct}/{all_total})")
    else:
        print("\nNo predictions to evaluate.")

    # Save to DB
    if args.save and all_total > 0:
        try:
            payload = {
                "ts_utc": datetime.now(timezone.utc).isoformat(),
                "days": args.days,
                "symbols": symbols,
                "total_predictions": all_total,
                "total_correct": all_correct,
                "overall_accuracy": round(all_correct / all_total, 4) if all_total > 0 else 0,
                "per_horizon": {
                    str(h): {
                        "total": grand_totals[h]["total"],
                        "correct": grand_totals[h]["correct"],
                        "accuracy": round(grand_totals[h]["correct"] / grand_totals[h]["total"], 4)
                        if grand_totals[h]["total"] > 0 else 0,
                    }
                    for h in FORECAST_HORIZONS
                },
                "per_symbol": {
                    sym: {
                        str(h): {
                            "total": results[sym][h]["total"],
                            "correct": results[sym][h]["correct"],
                        }
                        for h in FORECAST_HORIZONS
                    }
                    for sym in symbols
                },
            }
            db.client.table("forecast_backtests").insert(payload).execute()
            print(f"\nResults saved to forecast_backtests table.")
        except Exception as exc:
            print(f"\nWarning: Failed to save results: {exc}")
            print("You may need to create the forecast_backtests table first.")


if __name__ == "__main__":
    main()
