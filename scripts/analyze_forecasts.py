#!/usr/bin/env python3
"""
Comprehensive forecast accuracy analysis for the last N hours.
Run on server: python3 scripts/analyze_forecasts.py [hours]
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.storage.db import SupabaseDB


def main():
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    horizon = sys.argv[2] if len(sys.argv) > 2 else "h30"

    db = SupabaseDB()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    dir_col = f"{horizon}_direction"
    conf_col = f"{horizon}_confidence"
    correct_col = f"{horizon}_correct"
    strength_col = f"{horizon}_strength"

    print(f"Fetching verified {horizon} forecasts from last {hours} hours...")

    res = db.client.table("price_forecasts").select(
        f"symbol, ts_utc, {dir_col}, {conf_col}, {correct_col}, {strength_col}, "
        f"dominant_direction, all_aligned, data_quality"
    ).not_.is_("verified_at", "null").not_.is_(
        correct_col, "null"
    ).neq(
        dir_col, "neutral"
    ).gte("ts_utc", cutoff).order("ts_utc", desc=True).limit(5000).execute()

    rows = res.data or []
    print(f"Total verified forecasts: {len(rows)}\n")

    if not rows:
        print("No data. Exiting.")
        return

    # ========== 1. GLOBAL ACCURACY ==========
    total = len(rows)
    correct_total = sum(1 for r in rows if r[correct_col] is True)
    print("=" * 70)
    print(f"  GLOBAL ACCURACY ({horizon}, last {hours}h)")
    print(f"  {correct_total}/{total} = {correct_total/total:.1%}")
    print("=" * 70)

    # ========== 2. PER-PAIR ACCURACY ==========
    pair_stats = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in rows:
        sym = r["symbol"]
        pair_stats[sym]["total"] += 1
        if r[correct_col] is True:
            pair_stats[sym]["correct"] += 1

    sorted_pairs = sorted(pair_stats.items(), key=lambda x: x[1]["correct"] / max(x[1]["total"], 1), reverse=True)

    print(f"\n  {'PAIR':<10} {'ACCURACY':>8} {'CORRECT':>8} / {'TOTAL':<5} {'BAR'}")
    print(f"  {'-'*10} {'-'*8} {'-'*8}   {'-'*5} {'-'*20}")
    for sym, s in sorted_pairs:
        acc = s["correct"] / s["total"] if s["total"] else 0
        bar = "█" * int(acc * 20) + "░" * (20 - int(acc * 20))
        marker = " ✅" if acc >= 0.65 else " ⚠️" if acc >= 0.55 else " ❌"
        print(f"  {sym:<10} {acc:>7.0%}  {s['correct']:>7} / {s['total']:<5} {bar}{marker}")

    # ========== 3. PER-CONFIDENCE ACCURACY ==========
    conf_stats = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in rows:
        conf = (r.get(conf_col) or "unknown").lower()
        conf_stats[conf]["total"] += 1
        if r[correct_col] is True:
            conf_stats[conf]["correct"] += 1

    print(f"\n  ACCURACY BY CONFIDENCE:")
    for conf in ["high", "medium", "low", "unknown"]:
        s = conf_stats.get(conf, {"correct": 0, "total": 0})
        if s["total"] == 0:
            continue
        acc = s["correct"] / s["total"]
        print(f"  {conf.upper():<10} {acc:.0%} ({s['correct']}/{s['total']})")

    # ========== 4. PER-DIRECTION ACCURACY ==========
    dir_stats = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in rows:
        d = (r.get(dir_col) or "unknown").lower()
        dir_stats[d]["total"] += 1
        if r[correct_col] is True:
            dir_stats[d]["correct"] += 1

    print(f"\n  ACCURACY BY DIRECTION:")
    for d in ["up", "down"]:
        s = dir_stats.get(d, {"correct": 0, "total": 0})
        if s["total"] == 0:
            continue
        acc = s["correct"] / s["total"]
        print(f"  {d.upper():<10} {acc:.0%} ({s['correct']}/{s['total']})")

    # ========== 5. PER-PAIR × CONFIDENCE ==========
    pair_conf = defaultdict(lambda: defaultdict(lambda: {"correct": 0, "total": 0}))
    for r in rows:
        sym = r["symbol"]
        conf = (r.get(conf_col) or "unknown").lower()
        pair_conf[sym][conf]["total"] += 1
        if r[correct_col] is True:
            pair_conf[sym][conf]["correct"] += 1

    print(f"\n  PER-PAIR × CONFIDENCE:")
    print(f"  {'PAIR':<10} {'HIGH':>12} {'MEDIUM':>12} {'LOW':>12}")
    print(f"  {'-'*10} {'-'*12} {'-'*12} {'-'*12}")
    for sym, _ in sorted_pairs:
        parts = []
        for conf in ["high", "medium", "low"]:
            s = pair_conf[sym][conf]
            if s["total"] == 0:
                parts.append(f"{'—':>12}")
            else:
                acc = s["correct"] / s["total"]
                parts.append(f"{acc:.0%} ({s['correct']}/{s['total']})")
        print(f"  {sym:<10} {parts[0]:>12} {parts[1]:>12} {parts[2]:>12}")

    # ========== 6. PER-INDICATOR ACCURACY (if indicators_detail available) ==========
    indicator_stats = defaultdict(lambda: {"correct": 0, "total": 0})
    indicator_pair_stats = defaultdict(lambda: defaultdict(lambda: {"correct": 0, "total": 0}))
    has_indicators = False

    for r in rows:
        detail = r.get("indicators_detail")
        if not detail or not isinstance(detail, dict):
            continue
        has_indicators = True
        is_correct = r[correct_col] is True
        sym = r["symbol"]
        forecast_dir = (r.get(dir_col) or "").lower()

        for ind_name, ind_val in detail.items():
            # ind_val could be dict with 'direction' or just a string
            if isinstance(ind_val, dict):
                ind_dir = (ind_val.get("direction") or ind_val.get("signal") or "").lower()
            elif isinstance(ind_val, str):
                ind_dir = ind_val.lower()
            else:
                continue

            if ind_dir not in ("up", "down"):
                continue

            # Check if this indicator agreed with the final forecast direction
            agreed = ind_dir == forecast_dir
            indicator_stats[ind_name]["total"] += 1
            indicator_pair_stats[f"{sym}:{ind_name}"]["total"] = indicator_pair_stats[f"{sym}:{ind_name}"].get("total", 0)

            # If indicator agreed with forecast AND forecast was correct → indicator was correct
            # If indicator disagreed with forecast AND forecast was wrong → indicator was correct
            if (agreed and is_correct) or (not agreed and not is_correct):
                indicator_stats[ind_name]["correct"] += 1

    if has_indicators:
        print(f"\n  PER-INDICATOR ACCURACY (agreement with correct outcome):")
        sorted_indicators = sorted(indicator_stats.items(), key=lambda x: x[1]["correct"] / max(x[1]["total"], 1), reverse=True)
        for ind, s in sorted_indicators:
            if s["total"] < 5:
                continue
            acc = s["correct"] / s["total"]
            print(f"  {ind:<20} {acc:.0%} ({s['correct']}/{s['total']})")

    # ========== 7. HOURLY ACCURACY TREND ==========
    hourly = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in rows:
        ts = r.get("ts_utc", "")
        if isinstance(ts, str) and len(ts) >= 13:
            hour_key = ts[:13]  # "2025-02-23T19"
            hourly[hour_key]["total"] += 1
            if r[correct_col] is True:
                hourly[hour_key]["correct"] += 1

    if hourly:
        print(f"\n  HOURLY ACCURACY TREND:")
        for hour_key in sorted(hourly.keys()):
            s = hourly[hour_key]
            acc = s["correct"] / s["total"] if s["total"] else 0
            bar = "█" * int(acc * 20) + "░" * (20 - int(acc * 20))
            print(f"  {hour_key} {acc:>5.0%} ({s['correct']:>3}/{s['total']:<3}) {bar}")

    # ========== 8. ALL-ALIGNED ACCURACY ==========
    aligned_correct = sum(1 for r in rows if r.get("all_aligned") and r[correct_col] is True)
    aligned_total = sum(1 for r in rows if r.get("all_aligned"))
    not_aligned_correct = correct_total - aligned_correct
    not_aligned_total = total - aligned_total

    print(f"\n  ALIGNED vs NOT-ALIGNED:")
    if aligned_total > 0:
        print(f"  All-aligned:  {aligned_correct}/{aligned_total} = {aligned_correct/aligned_total:.0%}")
    if not_aligned_total > 0:
        print(f"  Not-aligned:  {not_aligned_correct}/{not_aligned_total} = {not_aligned_correct/not_aligned_total:.0%}")

    # ========== 9. STRENGTH BUCKETS ==========
    strength_buckets = {"0-30%": {"c": 0, "t": 0}, "30-60%": {"c": 0, "t": 0}, "60-80%": {"c": 0, "t": 0}, "80-100%": {"c": 0, "t": 0}}
    for r in rows:
        st = r.get(strength_col)
        if st is None:
            continue
        st = float(st)
        if st < 0.3:
            bucket = "0-30%"
        elif st < 0.6:
            bucket = "30-60%"
        elif st < 0.8:
            bucket = "60-80%"
        else:
            bucket = "80-100%"
        strength_buckets[bucket]["t"] += 1
        if r[correct_col] is True:
            strength_buckets[bucket]["c"] += 1

    print(f"\n  ACCURACY BY STRENGTH:")
    for bucket, s in strength_buckets.items():
        if s["t"] == 0:
            continue
        acc = s["c"] / s["t"]
        print(f"  {bucket:<10} {acc:.0%} ({s['c']}/{s['t']})")

    print(f"\n{'='*70}")
    print(f"  Analysis complete: {total} forecasts over {hours}h")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
