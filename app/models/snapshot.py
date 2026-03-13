from pydantic import BaseModel
from datetime import datetime
from typing import Literal

class MarketSnapshot(BaseModel):
    schema_version: int = 1
    timestamp: datetime
    symbol: str
    timeframe: Literal["S5", "M15", "H1", "H4"]
    close: float
    atr: float
    rsi: float
    ma_fast: float
    ma_slow: float
    spread: float
    data_quality: Literal["ok", "gap", "dup", "stale", "unknown"] = "ok"
