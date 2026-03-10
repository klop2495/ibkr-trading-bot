"""
Deprecated position reconciler.

CFD-only mode uses BrokerStateService for broker-first reconciliation.
"""


class PositionReconciler:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError("PositionReconciler is deprecated. Use BrokerStateService.")


def run_reconciliation(*args, **kwargs):
    raise RuntimeError("PositionReconciler is deprecated. Use BrokerStateService.")
