from pydantic import BaseModel, Field
from typing import Literal, List
from datetime import datetime

class BotSettings(BaseModel):
    id: int
    owner_user_id: str

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

    symbols: List[str] = Field(default_factory=list)

    updated_at: datetime
