from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

Severity = Literal["info", "warning", "error", "critical"]


class PositionAction(str, Enum):
    FREEZE_SYMBOL = "freeze_symbol"
    FREEZE_PORTFOLIO = "freeze_portfolio"
    REQUEST_CLOSE_POSITION = "request_close_position"
    REQUEST_REPLACE_PROTECTION = "request_replace_protection"
    REQUEST_RECON_NOW = "request_recon_now"


class PositionActionItem(BaseModel):
    action: PositionAction
    symbol: Optional[str] = None
    reason: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)


class PositionActionPlan(BaseModel):
    symbol: Optional[str] = None
    severity: Severity = "info"
    actions: List[PositionActionItem] = Field(default_factory=list)

    risk_event_type: Optional[str] = None
    risk_event_message: Optional[str] = None
    risk_event_data: Dict[str, Any] = Field(default_factory=dict)
