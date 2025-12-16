from __future__ import annotations

from app.models.events import SLRejectedEvent
from app.pm.position_manager import PositionManager
from app.runtime.dispatcher import EventDispatcher
from app.runtime.event_queue import PriorityEventQueue
from app.runtime.symbol_mutex import SymbolMutex
from app.storage.db import SupabaseDB
from app.storage.repositories import RiskEventsRepo


def main():
    db = SupabaseDB()
    risk_events = RiskEventsRepo(db)

    queue = PriorityEventQueue(max_depth=100, marketdata_keep_last=True)
    dispatcher = EventDispatcher(
        queue=queue,
        mutex=SymbolMutex(),
        pm=PositionManager(),
        risk_events=risk_events,
    )

    dispatcher.submit(
        SLRejectedEvent(symbol="EURUSD", broker_error="TEST: SL rejected (simulated)", position_id="pos-test-1")
    )

    plan = dispatcher.drain_once()
    print(plan.model_dump() if hasattr(plan, "model_dump") else plan.dict())


if __name__ == "__main__":
    main()
