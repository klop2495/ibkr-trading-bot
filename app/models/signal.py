from pydantic import BaseModel
from typing import Literal

class Signal(BaseModel):
    schema_version: int = 1
    symbol: str
    raw_signal: Literal["long", "short", "flat"]
    entry_triggered: bool
    sl_pips: float
    tp_pips: float
    confidence: float
