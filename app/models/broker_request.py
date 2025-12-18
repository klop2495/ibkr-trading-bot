from datetime import datetime
from typing import List
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class BrokerRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    request_version: int = 1
    ts_utc: datetime
    order_intent_id: UUID
    decision_id: UUID
    signal_preview_id: UUID
    status: str
    flags: List[str] = Field(default_factory=list)
    payload: dict = Field(default_factory=dict)

    def to_db_row(self) -> dict:
        return {
            "id": str(self.id),
            "request_version": self.request_version,
            "ts_utc": self.ts_utc.isoformat(),
            "order_intent_id": str(self.order_intent_id),
            "decision_id": str(self.decision_id),
            "signal_preview_id": str(self.signal_preview_id),
            "status": self.status,
            "flags": self.flags,
            "payload": self.payload or {},
        }
