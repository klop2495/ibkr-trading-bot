from datetime import datetime
from typing import Any, List, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ReconciliationReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    recon_version: int = 1
    broker_request_id: UUID
    order_intent_id: UUID
    execution_report_id: UUID
    decision_id: UUID
    signal_preview_id: UUID
    ts_utc: datetime
    status: Literal["OK", "WARN", "ERROR", "SKIPPED"]
    flags: List[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    def to_db_row(self) -> dict:
        return {
            "id": str(self.id),
            "recon_version": self.recon_version,
            "broker_request_id": str(self.broker_request_id),
            "order_intent_id": str(self.order_intent_id),
            "execution_report_id": str(self.execution_report_id),
            "decision_id": str(self.decision_id),
            "signal_preview_id": str(self.signal_preview_id),
            "ts_utc": self.ts_utc.isoformat(),
            "status": self.status,
            "flags": self.flags,
            "details": self.details or {},
        }
