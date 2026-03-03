"""
Price Direction Forecast Models.

Forecast generates directional predictions for 4 time horizons:
- 30 minutes
- 60 minutes (1 hour)
- 240 minutes (4 hours)
- 1440 minutes (24 hours)

Read-only module: does NOT influence trade execution.
"""

import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

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
    # Per-indicator vote audit trail
    h30_votes_json: Optional[dict] = None     # {"ma_cross_inv": -1, "momentum": 1, ...}

    # A/B test: alternative scoring with inverted contrarian weights
    h30_alt_direction: Optional[str] = None   # direction from alt scoring
    h30_alt_strength: Optional[float] = None  # strength from alt scoring

    # A/B test 2: ma_cross_inv direction when ma!=pv + ADX>=30 + top8 symbols
    h30_alt2_direction: Optional[str] = None  # direction by ma_cross_inv only (None if filter not passed)
    h30_alt2_trade_eligible: Optional[bool] = None  # execution eligibility (soft filter, does not suppress logging)

    # A/B test 3 (legacy): ma!=pv + ADX>=30 + momentum=ma + top8 (stricter filter)
    h30_alt3_direction: Optional[str] = None  # kept for backward compatibility

    # Alt3-v2: independent strict variant (not a direct Alt2 subset)
    h30_alt3v2_direction: Optional[str] = None
    h30_alt3v2_trade_eligible: Optional[bool] = None
    h30_alt3v2_score: Optional[float] = None
    h30_alt3v2_meta_json: Optional[dict] = None

    # Alt4: hybrid strategy (hour-based original/inverted mode)
    h30_alt4_direction: Optional[str] = None
    h30_alt4_correct: Optional[bool] = None
    h30_alt4_trade_eligible: Optional[bool] = None

    # Volatility regime filter (Phase 3 — BBW toxic band detection)
    vol_regime_blocked: Optional[bool] = None   # True if BBW in toxic band
    vol_regime_reason: Optional[str] = None     # e.g. "TOXIC_BBW_BAND"

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
        # Volatility regime
        if self.vol_regime_blocked is not None:
            row["vol_regime_blocked"] = self.vol_regime_blocked
        if self.vol_regime_reason is not None:
            row["vol_regime_reason"] = self.vol_regime_reason
        # Per-indicator vote audit
        if self.h30_votes_json is not None:
            row["h30_votes_json"] = self.h30_votes_json
        # A/B alt1: DISABLED — inverted weights strategy is unprofitable
        # if self.h30_alt_direction is not None:
        #     row["h30_alt_direction"] = self.h30_alt_direction
        # if self.h30_alt_strength is not None:
        #     row["h30_alt_strength"] = round(self.h30_alt_strength, 4)
        # A/B alt2: selective signal (ma!=pv + ADX>=30 + top8)
        if self.h30_alt2_direction is not None:
            row["h30_alt2_direction"] = self.h30_alt2_direction
            row["h30_alt2_trade_eligible"] = bool(self.h30_alt2_trade_eligible)
        # A/B alt3: stricter filter (ma!=pv + ADX>=30 + momentum=ma + top8)
        if self.h30_alt3_direction is not None:
            row["h30_alt3_direction"] = self.h30_alt3_direction
        # Alt3-v2: independent strict variant
        if self.h30_alt3v2_direction is not None:
            row["h30_alt3v2_direction"] = self.h30_alt3v2_direction
            row["h30_alt3v2_trade_eligible"] = bool(self.h30_alt3v2_trade_eligible)
            if self.h30_alt3v2_score is not None:
                row["h30_alt3v2_score"] = round(self.h30_alt3v2_score, 4)
            if self.h30_alt3v2_meta_json is not None:
                row["h30_alt3v2_meta_json"] = self.h30_alt3v2_meta_json
        # Alt4: hour-based original/inverted strategy
        if self.h30_alt4_direction is not None:
            row["h30_alt4_direction"] = self.h30_alt4_direction
            row["h30_alt4_trade_eligible"] = bool(self.h30_alt4_trade_eligible)
        return row
