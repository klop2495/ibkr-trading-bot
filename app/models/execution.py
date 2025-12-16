from pydantic import BaseModel
from typing import Optional, Literal
from uuid import UUID

class ExecutionReport(BaseModel):
    schema_version: int = 1
    decision_id: UUID
    order_id: Optional[str]
    status: Literal[
        "accepted",
        "filled",
        "rejected",
        "cancelled",
        "sl_failed",
        "tp_failed",
    ]
    message: Optional[str] = None
