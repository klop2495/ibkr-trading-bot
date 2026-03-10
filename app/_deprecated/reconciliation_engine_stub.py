from datetime import datetime, timezone
from typing import Any, Optional

from app.models.broker_request import BrokerRequestV1
from app.models.reconciliation_report_v1 import ReconciliationReportV1

FLAG_DISABLED = "RECONCILIATION_DISABLED"
FLAG_ERROR = "RECONCILIATION_ERROR"
FLAG_SIDE_MISMATCH = "RECON_SIDE_MISMATCH"
FLAG_UNSUPPORTED = "RECON_UNSUPPORTED_INTENT"


class ReconcilerStub:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def reconcile(
        self,
        broker_request: BrokerRequestV1,
        context: Optional[dict[str, Any]] = None,
    ) -> ReconciliationReportV1:
        ts = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            if not self.enabled:
                return ReconciliationReportV1(
                    broker_request_id=broker_request.id,
                    order_intent_id=broker_request.order_intent_id,
                    execution_report_id=broker_request.execution_report_id,
                    decision_id=broker_request.decision_id,
                    signal_preview_id=broker_request.signal_preview_id,
                    ts_utc=ts,
                    status="SKIPPED",
                    flags=[FLAG_DISABLED],
                    details={"reason": "reconciliation_disabled"},
                )
            flags = []
            status = "OK"
            payload = broker_request.payload or {}
            side = payload.get("side")
            if side not in ("buy", "sell", "flat"):
                status = "WARN"
                flags.append(FLAG_SIDE_MISMATCH)
            intent_type = payload.get("intent_type")
            if intent_type not in ("OPEN_MARKET",):
                status = "WARN"
                flags.append(FLAG_UNSUPPORTED)
            return ReconciliationReportV1(
                broker_request_id=broker_request.id,
                order_intent_id=broker_request.order_intent_id,
                execution_report_id=broker_request.execution_report_id,
                decision_id=broker_request.decision_id,
                signal_preview_id=broker_request.signal_preview_id,
                ts_utc=ts,
                status=status,
                flags=flags,
                details={"validated": True},
            )
        except Exception:
            return ReconciliationReportV1(
                broker_request_id=broker_request.id,
                order_intent_id=broker_request.order_intent_id,
                execution_report_id=broker_request.execution_report_id,
                decision_id=broker_request.decision_id,
                signal_preview_id=broker_request.signal_preview_id,
                ts_utc=ts,
                status="ERROR",
                flags=[FLAG_ERROR],
                details={"reason": "exception"},
            )
