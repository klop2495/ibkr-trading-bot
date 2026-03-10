"""
Deprecated position sync.

CFD-only mode uses BrokerStateService for broker-first reconciliation.
"""


class PositionSyncService:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError("PositionSyncService is deprecated. Use BrokerStateService.")


def create_position_sync_service(*args, **kwargs):
    raise RuntimeError("PositionSyncService is deprecated. Use BrokerStateService.")
