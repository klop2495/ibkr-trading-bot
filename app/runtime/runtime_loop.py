from __future__ import annotations

import time
from typing import Optional

from app.pm.position_manager import PositionManager
from app.recon.reconciler import Reconciler
from app.runtime.dispatcher import EventDispatcher
from app.runtime.event_queue import PriorityEventQueue
from app.runtime.runtime_config import RuntimeConfig
from app.runtime.symbol_mutex import SymbolMutex
from app.storage.db import SupabaseDB
from app.storage.repositories import RiskEventsRepo
from app.models.events import ReconMismatchEvent


def _default_compare_fn() -> Optional[ReconMismatchEvent]:
    return None


def run_runtime_loop() -> None:
    cfg = RuntimeConfig()

    db = SupabaseDB()
    risk_events = RiskEventsRepo(db)

    queue = PriorityEventQueue(
        max_depth=cfg.EVENT_QUEUE_MAX_DEPTH,
        marketdata_keep_last=cfg.MARKETDATA_KEEP_LAST,
    )
    mutex = SymbolMutex()
    pm = PositionManager()
    dispatcher = EventDispatcher(queue=queue, mutex=mutex, pm=pm, risk_events=risk_events)

    reconciler = Reconciler(risk_events=risk_events, compare_fn=_default_compare_fn)

    risk_events.insert(
        event_type="BOOT",
        severity="info",
        message="Runtime loop started",
        data={
            "EVENT_QUEUE_MAX_DEPTH": cfg.EVENT_QUEUE_MAX_DEPTH,
            "MARKETDATA_KEEP_LAST": cfg.MARKETDATA_KEEP_LAST,
            "RECON_INTERVAL_SEC": cfg.RECON_INTERVAL_SEC,
        },
    )

    next_recon = time.time() + cfg.RECON_INTERVAL_SEC

    while True:
        now = time.time()

        if now >= next_recon:
            try:
                evt = reconciler.run_once()
                if evt is not None:
                    dispatcher.submit(evt)
            except Exception as e:
                risk_events.insert(
                    event_type="RECON_ERROR",
                    severity="error",
                    message="Reconciliation loop error",
                    data={"error": str(e)},
                )
            next_recon = now + cfg.RECON_INTERVAL_SEC

        try:
            dispatcher.drain_once()
        except Exception as e:
            risk_events.insert(
                event_type="DISPATCH_ERROR",
                severity="error",
                message="Dispatch loop error",
                data={"error": str(e)},
            )

        time.sleep(0.05)
