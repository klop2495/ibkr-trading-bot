from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    symbol: str
    raw_signal: Literal["long", "short", "flat"]
    entry_triggered: bool
    sl_pips: float
    tp_pips: float
    confidence: float
    flags: List[str] = Field(default_factory=list)
