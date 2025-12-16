from pydantic import BaseModel
from typing import List, Optional

class AgentReport(BaseModel):
    schema_version: int = 1
    trade_allowed: bool
    risk_modifier: float
    flags: List[str]
    comment: Optional[str] = None
