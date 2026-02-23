#!/usr/bin/env python3
"""
Quick check of Adaptive Forecast Gate status.
Run on server: python3 scripts/check_gate.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.storage.db import SupabaseDB
from app.forecast.gate import AdaptiveForecastGate


def main():
    db = SupabaseDB()
    gate = AdaptiveForecastGate(db=db)

    # Force refresh
    gate._refresh_accuracy()

    status = gate.get_status()

    print("=" * 70)
    print(f"  ADAPTIVE FORECAST GATE STATUS")
    print(f"  Enabled: {status['enabled']}  |  Horizon: {status['horizon']}  |  Window: {status['window']}")
    print(f"  Block < {status['block_below']:.0%}  |  Unblock >= {status['unblock_above']:.0%}  |  Min samples: {status['min_samples']}")
    print(f"  Last refresh: {status['last_refresh']}")
    print("=" * 70)

    pairs = status.get("pairs", {})
    if not pairs:
        print("\n  No data yet — verifier hasn't produced enough results.")
        return

    # Sort: blocked first, then by accuracy desc
    sorted_pairs = sorted(
        pairs.items(),
        key=lambda x: (
            0 if x[1].get("blocked") else 1,
            -(x[1].get("accuracy") or 0),
        ),
    )

    print(f"\n  {'PAIR':<10} {'STATUS':<14} {'ACCURACY':>8}  {'CORRECT':>7} / {'TOTAL':<5}  {'VERDICT'}")
    print(f"  {'-'*10} {'-'*14} {'-'*8}  {'-'*7}   {'-'*5}  {'-'*20}")

    blocked_count = 0
    watch_count = 0
    active_count = 0
    no_data_count = 0

    for sym, info in sorted_pairs:
        acc = info.get("accuracy")
        samples = info.get("samples", 0)
        correct = info.get("correct", 0)
        blocked = info.get("blocked", False)
        pair_status = info.get("status", "unknown")

        if blocked:
            status_str = "🔴 BLOCKED"
            blocked_count += 1
        elif pair_status == "insufficient_data":
            status_str = "⏳ NO DATA"
            no_data_count += 1
        elif acc is not None and acc < 0.60:
            status_str = "🟡 WATCH"
            watch_count += 1
        else:
            status_str = "🟢 ACTIVE"
            active_count += 1

        acc_str = f"{acc:.0%}" if acc is not None else "n/a"

        # Verdict
        if blocked:
            verdict = f"accuracy {acc_str} < {gate._block_below:.0%}"
        elif pair_status == "insufficient_data":
            verdict = f"need {gate._min_samples - samples} more samples"
        elif acc is not None and acc < 0.60:
            verdict = f"borderline — close to block threshold"
        elif acc is not None and acc >= 0.70:
            verdict = "strong performer ✅"
        elif acc is not None and acc >= 0.60:
            verdict = "acceptable"
        else:
            verdict = ""

        print(f"  {sym:<10} {status_str:<14} {acc_str:>8}  {correct:>7} / {samples:<5}  {verdict}")

    print(f"\n  Summary: {active_count} active, {watch_count} watch, {blocked_count} blocked, {no_data_count} no data")
    print(f"  Blocked symbols: {sorted(gate.blocked_symbols) if gate.blocked_symbols else 'none'}")

    # List all 16 expected pairs
    expected = [
        "EURAUD", "EURCHF", "USDCAD", "AUDUSD", "EURUSD", "EURGBP",
        "GBPUSD", "NZDUSD", "AUDJPY", "CADJPY", "CHFJPY", "EURJPY",
        "GBPJPY", "NZDJPY", "USDJPY", "USDCHF",
    ]
    missing = [p for p in expected if p not in pairs]
    if missing:
        print(f"\n  ⚠️  Missing pairs (no verified data): {', '.join(missing)}")


if __name__ == "__main__":
    main()
