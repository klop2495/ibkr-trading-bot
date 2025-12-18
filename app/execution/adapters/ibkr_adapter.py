from app.execution.adapters.base import ExecutionAdapter, PreparedBrokerRequest
from app.models.order_intent import OrderIntentV1


class IBKRExecutionAdapter(ExecutionAdapter):
    """
    Prepare-only adapter. No network calls; just shapes a broker request payload.
    """

    def prepare(self, order_intent: OrderIntentV1) -> PreparedBrokerRequest:
        return PreparedBrokerRequest(
            symbol=order_intent.symbol,
            intent_type=order_intent.intent_type,
            side=order_intent.side,
            notes="ibkr_prepare_only",
        )
