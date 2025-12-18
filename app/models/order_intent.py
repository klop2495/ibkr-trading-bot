from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class OrderIntentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    intent_version: int = 1
    ts_utc: datetime
    symbol: str
    execution_report_id: UUID
    decision_id: UUID
    signal_preview_id: UUID
    intent_type: str
    side: Literal["buy", "sell", "flat"]
    status: str = "CREATED"
    flags: List[str] = Field(default_factory=list)
    details: Optional[dict] = None

    def to_db_row(self) -> dict:
        return {
            "id": str(self.id),
            "intent_version": self.intent_version,
            "ts_utc": self.ts_utc.isoformat(),
            "symbol": self.symbol,
            "execution_report_id": str(self.execution_report_id),
            "decision_id": str(self.decision_id),
            "signal_preview_id": str(self.signal_preview_id),
            "intent_type": self.intent_type,
            "side": self.side,
            "status": self.status,
            "flags": self.flags,
            "details": self.details or {},
        }
