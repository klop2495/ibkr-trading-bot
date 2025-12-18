from app.execution.adapters.base import ExecutionAdapter, PreparedBrokerRequest
from app.models.order_intent import OrderIntentV1


class NullExecutionAdapter(ExecutionAdapter):
    def prepare(self, order_intent: OrderIntentV1) -> PreparedBrokerRequest:
        return PreparedBrokerRequest(
            symbol=order_intent.symbol,
            intent_type=order_intent.intent_type,
            side=order_intent.side,
            notes="execution disabled",
        )
