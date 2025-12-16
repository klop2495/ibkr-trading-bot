from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator


ALLOWED_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
]

ALLOWED_TIMEFRAMES = ["M15", "H1", "H4"]


class SignalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["long", "short", "flat"]
    setup_present: bool
    entry_triggered: bool


class FeatureBins(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volatility: Literal["low", "normal", "high"]
    trend: Literal["up", "down", "range"]
    momentum: Literal["oversold", "neutral", "overbought"]


class PerSymbolAgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    signal_summary: SignalSummary
    feature_bins: FeatureBins
    data_quality: Literal["ok", "stale", "gap", "duplicate"]
    spread_quality: Literal["ok", "wide"]

    @model_validator(mode="after")
    def validate_symbol(self):
        if self.symbol not in ALLOWED_SYMBOLS:
            raise ValueError(f"symbol {self.symbol} not in allowed universe")
        return self


class PortfolioAgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_positions_count: int = Field(ge=0)
    usd_side_bias: Literal["usd_long", "usd_short", "neutral"]
    daily_trade_count_portfolio: int = Field(ge=0)
    daily_trade_count_per_symbol: Dict[str, int]

    @model_validator(mode="after")
    def validate_symbol_counts(self):
        invalid_keys = [k for k in self.daily_trade_count_per_symbol.keys() if k not in ALLOWED_SYMBOLS]
        if invalid_keys:
            raise ValueError(f"daily_trade_count_per_symbol contains invalid symbols: {invalid_keys}")
        for sym, count in self.daily_trade_count_per_symbol.items():
            if count < 0:
                raise ValueError(f"daily_trade_count_per_symbol[{sym}] must be >= 0")
        return self


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts_utc: datetime
    universe: List[str]
    timeframes: List[str]
    per_symbol: List[PerSymbolAgentState]
    portfolio: PortfolioAgentState
    safe_mode: bool

    @model_validator(mode="after")
    def validate_universe_and_timeframes(self):
        invalid_symbols = [s for s in self.universe if s not in ALLOWED_SYMBOLS]
        if invalid_symbols:
            raise ValueError(f"universe contains invalid symbols: {invalid_symbols}")
        invalid_timeframes = [tf for tf in self.timeframes if tf not in ALLOWED_TIMEFRAMES]
        if invalid_timeframes:
            raise ValueError(f"timeframes contains invalid entries: {invalid_timeframes}")
        # Ensure per_symbol symbols are within universe
        per_symbols = [ps.symbol for ps in self.per_symbol]
        outside = [s for s in per_symbols if s not in self.universe]
        if outside:
            raise ValueError(f"per_symbol contains symbols not in universe: {outside}")
        return self


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_allowed: bool
    risk_modifier: float = Field(1.0, ge=0.5, le=1.0)
    flags: List[str]
    comment: str = Field(min_length=1, max_length=240)


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: AgentResponse
    per_agent: Optional[Dict[str, AgentResponse]] = None
    timed_out: bool = False
    fallback_used: bool = False
    error: Optional[str] = None
