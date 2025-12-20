#!/usr/bin/env python
"""
Quick smoke: create one control_decision and risk_verdict for the latest signal_preview.
Uses service role Supabase client; no execution/broker calls.
"""

import os
import sys
from uuid import UUID
from datetime import datetime, timezone

from app.storage.db import SupabaseDB
from app.storage.repositories import DecisionsRepo, RiskVerdictsRepo
from app.models.decision import DecisionV1
from app.risk.engine_v1 import RiskEngineV1
from app.storage.repositories import RiskEventsRepo


def main():
    db = SupabaseDB()
    supa = db.client
    risk_events = RiskEventsRepo(db)
    decisions_repo = DecisionsRepo(db)
    verdicts_repo = RiskVerdictsRepo(db)
    risk_engine = RiskEngineV1()

    # latest preview
    res = supa.table("signal_previews").select("*").order("ts_utc", desc=True).limit(1).execute()
    rows = getattr(res, "data", None) or []
    if not rows:
        print("No signal_previews found; nothing to do")
        return
    pv = rows[0]
    preview_id = pv["id"]
    symbol = pv.get("symbol")
    ts = pv.get("ts_utc") or datetime.now(timezone.utc).isoformat()
    try:
        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        ts_dt = datetime.now(timezone.utc)

    decision = DecisionV1(
        ts_utc=ts_dt,
        symbol=symbol,
        signal_preview_id=UUID(str(preview_id)),
        trade_allowed=False,
        risk_modifier=1.0,
        flags=["SMOKE"],
        commentary="smoke_control_plane",
    )

    try:
        decision_id = decisions_repo.insert_decision(decision)
        decision.id = decision_id
        print(f"Inserted decision_id={decision_id} for preview_id={preview_id}")
    except Exception as exc:
        try:
            risk_events.insert(
                event_type="CONTROL_DECISION_PERSIST",
                severity="ERROR",
                message="smoke decision persist failed",
                data={"signal_preview_id": str(preview_id), "symbol": symbol, "error": str(exc)},
            )
        except Exception as log_exc:
            print(f"risk_event log failed: {log_exc}", file=sys.stderr)
        print(f"Decision persist failed: {exc}", file=sys.stderr)
        return

    try:
        verdict = risk_engine.evaluate(decision, type("S", (), {"trading_enabled": True})())
        verdict_id = verdicts_repo.insert_verdict(verdict)
        print(f"Inserted verdict_id={verdict_id} for decision_id={decision_id}")
    except Exception as exc:
        try:
            risk_events.insert(
                event_type="RISK_VERDICT_PERSIST",
                severity="ERROR",
                message="smoke verdict persist failed",
                data={"signal_preview_id": str(preview_id), "symbol": symbol, "error": str(exc), "decision_id": str(decision.id)},
            )
        except Exception as log_exc:
            print(f"risk_event log failed: {log_exc}", file=sys.stderr)
        print(f"Verdict persist failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
