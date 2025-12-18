from datetime import datetime, timezone
from typing import Optional

from app.models.order_intent import OrderIntentV1
from app.models.decision import DecisionV1
from app.models.signal_preview import SignalPreviewV1, Direction
from app.models.execution_report_v1 import ExecutionReportV1


def _side_from_preview(preview: SignalPreviewV1) -> str:
    if preview.direction == Direction.LONG:
        return "buy"
    if preview.direction == Direction.SHORT:
        return "sell"
    return "flat"


def create_order_intent(
    plan_action: str,
    execution_report: ExecutionReportV1,
    decision: DecisionV1,
    preview: SignalPreviewV1,
) -> OrderIntentV1:
    ts = execution_report.ts_utc or decision.ts_utc or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if plan_action == "HOLD":
        # no intent needed
        raise ValueError("plan_action HOLD should not create intent")
    side = _side_from_preview(preview)
    return OrderIntentV1(
        ts_utc=ts,
        symbol=decision.symbol,
        execution_report_id=execution_report.id,
        decision_id=decision.id or decision.signal_preview_id,
        signal_preview_id=decision.signal_preview_id,
        intent_type="OPEN_MARKET",
        side=side,
        status="CREATED",
        flags=[],
        details={"plan_action": plan_action},
    )
