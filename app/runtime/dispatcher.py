from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from app.models.events import AnyEvent
from app.models.pm import PositionAction, PositionActionPlan
from app.pm.position_manager import PositionManager
from app.runtime.event_queue import PriorityEventQueue
from app.runtime.symbol_mutex import SymbolMutex
from app.storage.repositories import RiskEventsRepo


@dataclass
class FreezeRegistry:
    frozen_symbols: Set[str] = field(default_factory=set)

    def freeze_symbol(self, symbol: Optional[str]) -> None:
        if symbol:
            self.frozen_symbols.add(symbol)

    def is_frozen(self, symbol: Optional[str]) -> bool:
        return bool(symbol) and symbol in self.frozen_symbols


@dataclass
class EventDispatcher:
    queue: PriorityEventQueue
    mutex: SymbolMutex
    pm: PositionManager
    risk_events: RiskEventsRepo
    freeze: FreezeRegistry = field(default_factory=FreezeRegistry)

    def submit(self, event: AnyEvent) -> None:
        self.queue.push(event)

    def drain_once(self) -> Optional[PositionActionPlan]:
        evt = self.queue.pop()
        if evt is None:
            return None

        symbol = evt.symbol or "__GLOBAL__"
        with self.mutex.lock(symbol):
            plan = self.pm.handle(evt)

            # log PM recommendation
            if plan.risk_event_type and plan.risk_event_message:
                self.risk_events.insert(
                    event_type=plan.risk_event_type,
                    severity=("critical" if plan.severity == "critical" else "info"),
                    symbol=plan.symbol,
                    message=plan.risk_event_message,
                    data=plan.risk_event_data,
                )

            # apply freeze locally (runtime gate)
            for a in plan.actions:
                if a.action == PositionAction.FREEZE_SYMBOL:
                    self.freeze.freeze_symbol(a.symbol)
                    self.risk_events.insert(
                        event_type="FREEZE_SYMBOL",
                        severity="critical",
                        symbol=a.symbol,
                        message="Symbol frozen by PM",
                        data={"reason": a.reason, **(a.data or {})},
                    )

            # NOTE: Execution hooks intentionally NOT implemented here:
            # - REQUEST_CLOSE_POSITION -> execution layer will place close order
            # - REQUEST_REPLACE_PROTECTION -> execution layer will recreate SL/TP deterministically
            # - REQUEST_RECON_NOW -> recon scheduler can run immediately
            return plan
