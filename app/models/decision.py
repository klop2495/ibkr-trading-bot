from pydantic import BaseModel
from typing import List, Optional, Literal
from uuid import UUID

class Decision(BaseModel):
    schema_version: int = 1
    decision_id: UUID
    symbol: str
    action: Literal["open", "close", "hold"]
    direction: Optional[Literal["buy", "sell"]] = None
    volume: Optional[float] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    reason_codes: List[str]
