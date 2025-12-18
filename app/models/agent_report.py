from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    ts_utc: datetime | None = None
    symbol: str | None = None
    scope: str | None = None
    decision_id: str | None = None
    signal_preview_id: UUID | None = None
    agent_name: str | None = None
    agent_version: str | None = None
    trade_allowed: bool
    risk_modifier: float = Field(default=1.0, ge=0.0, le=2.0)
    flags: List[str] = Field(default_factory=list)
    comment: Optional[str] = None
    latency_ms: float | None = None
    error: str | None = None
