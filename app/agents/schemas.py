from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    decision_id: str
    ts: datetime
    symbols: List[str]
    timeframes: List[str]
    context_flags: List[str] = Field(default_factory=list)
    features_summary: Dict[str, str] = Field(default_factory=dict)
    position_state: Dict[str, str] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    decision_id: str
    trade_allowed: bool
    risk_modifier: float = Field(default=1.0, ge=0.5, le=1.0)
    flags: List[str] = Field(default_factory=list)
    comment: Optional[str] = None


class AgentDecision(BaseModel):
    decision_id: str
    trade_allowed: bool
    risk_modifier: float = Field(default=1.0, ge=0.5, le=1.0)
    flags: List[str] = Field(default_factory=list)
    comment: Optional[str] = None
    per_agent: Dict[str, AgentResponse] = Field(default_factory=dict)
