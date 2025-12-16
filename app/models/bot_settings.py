from datetime import datetime
from typing import List, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


DEFAULT_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
]


class BotSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: UUID

    trading_enabled: bool = False
    mode: Literal["paper", "live"] = "paper"

    risk_per_trade: float = Field(default=0.005, ge=0.0, le=0.05)
    max_open_positions: int = Field(default=3, ge=0, le=20)

    max_trades_per_day_portfolio: int = Field(default=2, ge=0, le=50)
    max_trades_per_day_per_symbol: int = Field(default=1, ge=0, le=20)
    max_usd_side_positions: int = Field(default=2, ge=0, le=20)

    daily_loss_limit: float = Field(default=0.015, ge=0.0, le=0.5)
    loss_streak_breaker: int = Field(default=3, ge=0, le=20)
    breaker_pause_hours: int = Field(default=24, ge=0, le=168)

    max_effective_leverage: float = Field(default=2.0, ge=0.0, le=50.0)
    max_margin_utilization: float = Field(default=0.35, ge=0.0, le=1.0)

    warmup_bars_min: int = Field(default=300, ge=0)
    symbols: List[str] = Field(default_factory=list)

    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        cleaned = []
        seen = set()
        for s in v:
            if not isinstance(s, str):
                continue
            name = s.strip().upper()
            if not name or name in seen:
                continue
            seen.add(name)
            cleaned.append(name)
        return cleaned
