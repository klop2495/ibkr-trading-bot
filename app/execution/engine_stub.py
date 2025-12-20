from datetime import datetime, timezone
from typing import Optional

from app.models.execution_plan import ExecutionPlanV1
from app.models.decision import DecisionV1
from app.models.risk_verdict import RiskVerdictV1


class ExecutionEngineStub:
    def plan(self, verdict: RiskVerdictV1, decision: DecisionV1) -> Optional[ExecutionPlanV1]:
        if not verdict.trade_allowed:
            return None
        if not decision.id:
            raise ValueError("decision.id is required")
        ts = verdict.ts_utc or decision.ts_utc or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ExecutionPlanV1(
            decision_id=decision.id,
            signal_preview_id=decision.signal_preview_id,
            symbol=decision.symbol,
            action="OPEN",
            notes=None,
        )
