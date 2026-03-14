from __future__ import annotations

import argparse
from typing import Optional

from app.execution.fx_pnl import calculate_realized_pnl_usd
from app.storage.db import SupabaseDB


def resolve_rate(db: SupabaseDB, quote_ccy: str, as_of: Optional[str]) -> Optional[float]:
    direct = f"{quote_ccy}USD"
    inverse = f"USD{quote_ccy}"
    via_direct = f"{quote_ccy}EUR"
    via_inverse = f"EUR{quote_ccy}"
    candidates = [direct, inverse, via_direct, via_inverse, "EURUSD"]

    query = (
        db.client.table("market_snapshots")
        .select("symbol,timeframe,ts,created_at,close")
        .in_("symbol", candidates)
        .in_("timeframe", ["S5", "M1", "M15"])
        .order("ts", desc=True)
        .limit(100)
    )
    if as_of:
        query = query.lte("ts", as_of)

    rows = query.execute().data or []
    best = {}
    rank = {"S5": 0, "M1": 1, "M15": 2}
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        tf = str(row.get("timeframe") or "").upper()
        close = row.get("close")
        if not sym or close in (None, 0):
            continue
        tf_rank = rank.get(tf, 99)
        if sym not in best or tf_rank < best[sym][0]:
            best[sym] = (tf_rank, float(close))

    if direct in best:
        return best[direct][1]
    if inverse in best and best[inverse][1] > 0:
        return 1.0 / best[inverse][1]

    eurusd = best.get("EURUSD", (99, None))[1]
    if eurusd and via_direct in best:
        return best[via_direct][1] * eurusd
    if eurusd and via_inverse in best and best[via_inverse][1] > 0:
        return eurusd / best[via_inverse][1]
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="paper", help="paper/live/all")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = SupabaseDB()
    query = (
        db.client.table("trades_history")
        .select("id,symbol,side,quantity,entry_price,exit_price,pnl,pnl_pips,closed_at,last_status_at,status,mode")
        .eq("status", "CLOSED")
        .not_.is_("entry_price", "null")
        .not_.is_("exit_price", "null")
        .order("closed_at", desc=True)
        .limit(args.limit)
    )
    if args.mode != "all":
        query = query.eq("mode", args.mode)
    rows = query.execute().data or []

    changed = 0
    skipped = 0
    for row in rows:
        closed_at = row.get("closed_at") or row.get("last_status_at")
        pnl, pnl_pips = calculate_realized_pnl_usd(
            symbol=str(row.get("symbol") or ""),
            side=str(row.get("side") or ""),
            entry_price=row.get("entry_price"),
            exit_price=row.get("exit_price"),
            quantity=row.get("quantity"),
            rate_resolver=lambda quote, db=db, as_of=closed_at: resolve_rate(db, quote, as_of),
        )
        if pnl is None or pnl_pips is None:
            skipped += 1
            continue

        old_pnl = row.get("pnl")
        old_pips = row.get("pnl_pips")
        if old_pnl is not None and old_pips is not None:
            if round(float(old_pnl), 2) == round(float(pnl), 2) and round(float(old_pips), 1) == round(float(pnl_pips), 1):
                continue

        changed += 1
        print(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "old_pnl": old_pnl,
                "new_pnl": round(float(pnl), 6),
                "old_pnl_pips": old_pips,
                "new_pnl_pips": round(float(pnl_pips), 6),
                "closed_at": closed_at,
            }
        )

        if args.apply:
            db.client.table("trades_history").update(
                {"pnl": float(pnl), "pnl_pips": float(pnl_pips)}
            ).eq("id", row["id"]).execute()

    print({"rows": len(rows), "changed": changed, "skipped": skipped, "apply": args.apply})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
