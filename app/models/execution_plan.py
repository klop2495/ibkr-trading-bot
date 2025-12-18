from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ExecutionPlanV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: UUID
    signal_preview_id: UUID
    symbol: str
    action: Literal["HOLD", "OPEN", "CLOSE"]
    notes: Optional[str] = None
