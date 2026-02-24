"""
Price Direction Forecast Models.

Forecast generates directional predictions for 4 time horizons:
- 30 minutes
- 60 minutes (1 hour)
- 240 minutes (4 hours)
- 1440 minutes (24 hours)

Read-only module: does NOT influence trade execution.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ForecastDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"


class ForecastConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


FORECAST_HORIZONS = [30, 60, 240, 1440]


class ForecastHorizon(BaseModel):
    """Single-horizon directional forecast."""

    model_config = ConfigDict(extra="forbid")

    horizon_minutes: int
    direction: ForecastDirection
    confidence: ForecastConfidence
    strength: float = Field(ge=0.0, le=1.0)
    method: str = "multi_indicator"
    indicators_aligned: int = 0
    indicators_total: int = 0

    @field_validator("horizon_minutes")
    @classmethod
    def validate_horizon(cls, v):
        if v not in FORECAST_HORIZONS:
            raise ValueError(f"horizon_minutes must be one of {FORECAST_HORIZONS}, got {v}")
        return v


class ForecastResult(BaseModel):
    """Complete forecast for one symbol across all horizons."""

    model_config = ConfigDict(extra="forbid")

    ts_utc: datetime
    symbol: str
    horizons: List[ForecastHorizon]
    data_quality: str = "ok"
    flags: List[str] = Field(default_factory=list)
    base_price: Optional[float] = None  # close price at forecast time

    # Advanced filter metadata (Phase 2 — computed but not blocking initially)
    adx_value: Optional[float] = None        # ADX(14) on primary TF — trend strength
    bb_width: Optional[float] = None         # Bollinger Band width (normalized)
    bb_squeeze: Optional[bool] = None        # True if BB inside Keltner (squeeze)
    mtf_conflict: Optional[bool] = None      # True if H4 disagrees with H30 direction
    mtf_h4_direction: Optional[str] = None   # H4 dominant direction for reference
    spread_pips: Optional[float] = None      # Current spread from broker (if available)

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, v):
        if isinstance(v, str):
            val = v.strip().upper()
            if not val:
                raise ValueError("symbol cannot be empty")
            return val
        return v

    def horizon(self, minutes: int) -> Optional[ForecastHorizon]:
        """Get forecast for a specific horizon."""
        for h in self.horizons:
            if h.horizon_minutes == minutes:
                return h
        return None

    def all_aligned(self) -> bool:
        """Check if all horizons point in the same direction (excluding NEUTRAL)."""
        dirs = [h.direction for h in self.horizons if h.direction != ForecastDirection.NEUTRAL]
        if len(dirs) < 2:
            return False
        return len(set(dirs)) == 1

    def dominant_direction(self) -> ForecastDirection:
        """Most common non-neutral direction, or NEUTRAL if tied."""
        up = sum(1 for h in self.horizons if h.direction == ForecastDirection.UP)
        down = sum(1 for h in self.horizons if h.direction == ForecastDirection.DOWN)
        if up > down:
            return ForecastDirection.UP
        if down > up:
            return ForecastDirection.DOWN
        return ForecastDirection.NEUTRAL

    def to_db_row(self) -> dict:
        """Flatten for Supabase insertion."""
        row = {
            "ts_utc": self.ts_utc.isoformat(),
            "symbol": self.symbol,
            "data_quality": self.data_quality,
            "flags": self.flags,
            "all_aligned": self.all_aligned(),
            "dominant_direction": self.dominant_direction().value,
            "base_price": self.base_price,
        }
        for h in self.horizons:
            prefix = f"h{h.horizon_minutes}"
            row[f"{prefix}_direction"] = h.direction.value
            row[f"{prefix}_confidence"] = h.confidence.value
            row[f"{prefix}_strength"] = round(h.strength, 4)
            row[f"{prefix}_aligned"] = h.indicators_aligned
            row[f"{prefix}_total"] = h.indicators_total
        # Advanced filter metadata
        if self.adx_value is not None:
            row["adx_value"] = round(self.adx_value, 2)
        if self.bb_width is not None:
            row["bb_width"] = round(self.bb_width, 6)
        if self.bb_squeeze is not None:
            row["bb_squeeze"] = self.bb_squeeze
        if self.mtf_conflict is not None:
            row["mtf_conflict"] = self.mtf_conflict
        if self.mtf_h4_direction is not None:
            row["mtf_h4_direction"] = self.mtf_h4_direction
        if self.spread_pips is not None:
            row["spread_pips"] = round(self.spread_pips, 2)
        return row
