from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SetupType(str, Enum):
    SWING_CONTINUATION = "SWING_CONTINUATION"
    SWING_REVERSAL = "SWING_REVERSAL"
    NO_TRADE = "NO_TRADE"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Confidence(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class DataQuality(str, Enum):
    OK = "ok"
    STALE = "stale"
    GAP = "gap"
    DUP = "dup"
    UNKNOWN = "unknown"


class SpreadQuality(str, Enum):
    OK = "ok"
    WIDE = "wide"
    UNKNOWN = "unknown"


class TimeframeTrigger(str, Enum):
    M15 = "M15"


class SignalPreviewV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts_utc: datetime
    symbol: str
    timeframe_trigger: TimeframeTrigger = TimeframeTrigger.M15
    setup_type: SetupType
    direction: Direction
    setup_present: bool
    entry_triggered: bool
    confidence: Confidence
    rr: float
    data_quality: DataQuality
    spread_quality: SpreadQuality
    flags: List[str] = Field(default_factory=list)
    sl_distance_pips: Optional[float] = None
    tp_distance_pips: Optional[float] = None

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            val = v.strip().upper()
            if not val:
                raise ValueError("symbol cannot be empty")
            return val
        return v

    def to_db_row(self) -> dict:
        return {
            "ts_utc": self.ts_utc.isoformat(),
            "symbol": self.symbol,
            "timeframe_trigger": self.timeframe_trigger.value,
            "setup_type": self.setup_type.value,
            "direction": self.direction.value,
            "setup_present": self.setup_present,
            "entry_triggered": self.entry_triggered,
            "confidence": self.confidence.value,
            "rr": self.rr,
            "data_quality": self.data_quality.value,
            "spread_quality": self.spread_quality.value,
            "flags": self.flags,
            "sl_distance_pips": self.sl_distance_pips,
            "tp_distance_pips": self.tp_distance_pips,
            "engine_version": 1,
        }
