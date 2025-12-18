from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4, UUID

from app.models.decision import DecisionV1
from app.models.execution_plan import ExecutionPlanV1
from app.models.execution_report_v1 import ExecutionReportV1
from app.models.risk_verdict import RiskVerdictV1
from app.storage.repositories import ExecutionReportsRepo

FLAG_BLOCKED_BY_RISK = "EXECUTION_BLOCKED_BY_RISK"


def run_execution_if_allowed(
    verdict: RiskVerdictV1,
    decision: DecisionV1,
    engine,
    repo: ExecutionReportsRepo,
) -> Optional[ExecutionReportV1]:
    """
    Persist execution report only when risk allows. Stub engine, no broker calls.
    """
    if not verdict.trade_allowed:
        return None
    plan: Optional[ExecutionPlanV1] = engine.plan(verdict, decision)
    if plan is None:
        return None
    ts = verdict.ts_utc or decision.ts_utc or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    report = ExecutionReportV1(
        ts_utc=ts,
        decision_id=decision.id or decision.signal_preview_id,
        signal_preview_id=decision.signal_preview_id,
        status="PLANNED",
        reason_flags=[],
        details={"action": plan.action},
        id=uuid4(),
    )
    report_id = repo.insert_report(report)
    try:
        report.id = UUID(report_id)
    except Exception:
        pass
    return report
