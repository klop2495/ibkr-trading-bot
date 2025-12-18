from datetime import datetime
from typing import Any, List, Optional, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ExecutionReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    execution_version: int = 1
    ts_utc: datetime
    decision_id: UUID
    signal_preview_id: UUID
    status: Literal["SKIPPED", "PLANNED"]
    reason_flags: List[str] = Field(default_factory=list)
    details: Optional[dict[str, Any]] = None

    def to_db_row(self) -> dict:
        return {
            "id": str(self.id),
            "execution_version": self.execution_version,
            "ts_utc": self.ts_utc.isoformat(),
            "decision_id": str(self.decision_id),
            "signal_preview_id": str(self.signal_preview_id),
            "status": self.status,
            "reason_flags": self.reason_flags,
            "details": self.details or {},
        }
