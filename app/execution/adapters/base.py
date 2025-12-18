from typing import Protocol, Optional

from pydantic import BaseModel, ConfigDict

from app.models.order_intent import OrderIntentV1


class PreparedBrokerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    intent_type: str
    side: str
    notes: Optional[str] = None


class ExecutionAdapter(Protocol):
    def prepare(self, order_intent: OrderIntentV1) -> PreparedBrokerRequest:
        ...
