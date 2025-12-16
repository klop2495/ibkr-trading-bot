from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Union
from pydantic import BaseModel, Field


EventType = Literal[
    "PARTIAL_FILL",
    "SL_REJECTED",
    "RECON_MISMATCH",
    "MARKET_DATA_BAR",
    "MARKET_DATA_MISSING",
    "NEW_DECISION",
]


class BaseEvent(BaseModel):
    event_type: EventType
    ts: datetime = Field(default_factory=datetime.utcnow)
    symbol: Optional[str] = None


class PartialFillEvent(BaseEvent):
    event_type: Literal["PARTIAL_FILL"] = "PARTIAL_FILL"
    order_id: str
    filled_qty: float
    remaining_qty: float


class SLRejectedEvent(BaseEvent):
    event_type: Literal["SL_REJECTED"] = "SL_REJECTED"
    position_id: Optional[str] = None
    broker_error: str


class ReconMismatchEvent(BaseEvent):
    event_type: Literal["RECON_MISMATCH"] = "RECON_MISMATCH"
    mismatch_kind: Literal["MINOR", "CRITICAL"]
    details: str


class MarketDataBarEvent(BaseEvent):
    event_type: Literal["MARKET_DATA_BAR"] = "MARKET_DATA_BAR"
    timeframe: Literal["M15", "H1", "H4"]
    bar_ts: datetime


class MarketDataMissingEvent(BaseEvent):
    event_type: Literal["MARKET_DATA_MISSING"] = "MARKET_DATA_MISSING"
    timeframe: Literal["M15", "H1", "H4"]
    last_seen_ts: datetime


class NewDecisionEvent(BaseEvent):
    event_type: Literal["NEW_DECISION"] = "NEW_DECISION"
    decision_id: str


AnyEvent = Union[
    PartialFillEvent,
    SLRejectedEvent,
    ReconMismatchEvent,
    MarketDataBarEvent,
    MarketDataMissingEvent,
    NewDecisionEvent,
]
