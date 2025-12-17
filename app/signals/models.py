from datetime import datetime
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FeatureBins(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trend: Literal["up", "down", "sideways", "unknown"]
    volatility: Literal["low", "normal", "high", "unknown"]
    momentum: Literal["up", "down", "neutral", "unknown"]


class SignalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["long", "short", "flat"]
    setup_present: bool
    entry_triggered: bool


class SignalPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts_utc: datetime
    symbol: str
    feature_bins: FeatureBins
    signal_summary: SignalSummary
    data_quality: Literal["ok", "stale", "gap", "dup", "unknown"]
    spread_quality: Literal["ok", "wide", "unknown"]
    flags: List[str] = Field(default_factory=list)

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
