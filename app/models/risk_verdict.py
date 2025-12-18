from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RiskVerdictV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    risk_version: int = 1
    ts_utc: datetime
    symbol: str
    decision_id: UUID
    signal_preview_id: UUID
    trade_allowed: bool
    risk_modifier: float = Field(ge=0.0, le=2.0)
    flags: List[str] = Field(default_factory=list)
    commentary: Optional[str] = None

    def to_db_row(self) -> dict:
        return {
            "risk_version": self.risk_version,
            "ts_utc": self.ts_utc.isoformat(),
            "symbol": self.symbol,
            "decision_id": str(self.decision_id),
            "signal_preview_id": str(self.signal_preview_id),
            "trade_allowed": self.trade_allowed,
            "risk_modifier": self.risk_modifier,
            "flags": self.flags,
            "commentary": self.commentary,
        }
