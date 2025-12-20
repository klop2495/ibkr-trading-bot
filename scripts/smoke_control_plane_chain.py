#!/usr/bin/env python
"""
Smoke test: persist decision + risk verdict for the latest signal_preview.
Uses service-role Supabase client; no execution/broker calls.
Run as: `python scripts/smoke_control_plane_chain.py`
"""

import os
import sys
from datetime import datetime, timezone
from uuid import UUID, uuid4
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.storage.db import SupabaseDB  # noqa: E402
from app.storage.repositories import DecisionsRepo, RiskVerdictsRepo, RiskEventsRepo  # noqa: E402
from app.models.decision import DecisionV1  # noqa: E402
from app.risk.engine_v1 import RiskEngineV1  # noqa: E402


def main():
    missing_env = [name for name in ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "BOT_OWNER_USER_ID"] if not os.getenv(name)]
    if missing_env:
        print(f"Missing env vars: {', '.join(missing_env)}", file=sys.stderr)
        return

    try:
        db = SupabaseDB()
    except Exception as exc:
        print(f"Supabase init failed: {exc}", file=sys.stderr)
        return

    supa = db.client
    decisions = DecisionsRepo(db)
    verdicts = RiskVerdictsRepo(db)
    risk_events = RiskEventsRepo(db)
    risk_engine = RiskEngineV1()

    res = supa.table("signal_previews").select("*").order("ts_utc", desc=True).limit(1).execute()
    rows = getattr(res, "data", None) or []
    if not rows:
        print("No signal_previews found; cannot smoke control-plane.")
        return
    pv = rows[0]
    preview_id = str(pv["id"])
    symbol = pv.get("symbol")
    ts = pv.get("ts_utc") or datetime.now(timezone.utc).isoformat()
    try:
        ts_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        ts_dt = datetime.now(timezone.utc)

    decision = DecisionV1(
        ts_utc=ts_dt,
        symbol=symbol,
        signal_preview_id=UUID(preview_id),
        trade_allowed=False,
        risk_modifier=1.0,
        flags=["SMOKE_CHAIN"],
        commentary="smoke_control_plane_chain",
    )

    try:
        decision_id = decisions.insert_decision(decision)
        decision.id = decision_id
        print(f"decision_id={decision_id} preview_id={preview_id}")
    except Exception as exc:
        try:
            risk_events.insert(
                event_type="CONTROL_DECISION_PERSIST",
                severity="ERROR",
                message="smoke decision persist failed",
                data={"signal_preview_id": preview_id, "symbol": symbol, "error": str(exc)},
            )
        except Exception as log_exc:
            print(f"risk_event log failed: {log_exc}", file=sys.stderr)
        print(f"decision persist failed: {exc}", file=sys.stderr)
        return

    try:
        verdict = risk_engine.evaluate(decision, type("S", (), {"trading_enabled": True})())
        verdict_id = verdicts.insert_verdict(verdict)
        print(f"verdict_id={verdict_id} decision_id={decision_id} preview_id={preview_id}")
        try:
            join_res = supa.rpc(
                "sql",
                {
                    "query": """
                    select p.id as preview_id, d.id as decision_id, v.id as verdict_id
                    from signal_previews p
                    join control_decisions d on d.signal_preview_id = p.id
                    join risk_verdicts v on v.decision_id = d.id
                    where p.id = %(preview_id)s
                    order by p.ts_utc desc
                    limit 1;
                    """,
                    "params": {"preview_id": preview_id},
                },
            ).execute()
            join_rows = getattr(join_res, "data", None) or []
            if join_rows:
                jr = join_rows[0]
                print(f"chain_ok preview_id={jr.get('preview_id')} decision_id={jr.get('decision_id')} verdict_id={jr.get('verdict_id')}")
        except Exception:
            # Not all supabase setups expose sql RPC; ignore.
            pass
    except Exception as exc:
        try:
            risk_events.insert(
                event_type="RISK_VERDICT_PERSIST",
                severity="ERROR",
                message="smoke verdict persist failed",
                data={"signal_preview_id": preview_id, "symbol": symbol, "error": str(exc), "decision_id": str(decision.id)},
            )
        except Exception as log_exc:
            print(f"risk_event log failed: {log_exc}", file=sys.stderr)
        print(f"verdict persist failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
