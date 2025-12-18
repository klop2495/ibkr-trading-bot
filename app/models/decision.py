from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    decision_version: int = 1
    ts_utc: datetime
    symbol: str
    signal_preview_id: UUID
    trade_allowed: bool
    risk_modifier: float = Field(ge=0.0, le=2.0)
    flags: List[str] = Field(default_factory=list)
    commentary: Optional[str] = None
    engine_version: int = 1
    agents_version: int = 1

    def to_db_row(self) -> dict:
        return {
            "decision_version": self.decision_version,
            "ts_utc": self.ts_utc.isoformat(),
            "symbol": self.symbol,
            "signal_preview_id": str(self.signal_preview_id),
            "trade_allowed": self.trade_allowed,
            "risk_modifier": self.risk_modifier,
            "flags": self.flags,
            "commentary": self.commentary,
            "engine_version": self.engine_version,
            "agents_version": self.agents_version,
        }
