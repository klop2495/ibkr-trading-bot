from __future__ import annotations

import heapq
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from app.models.events import AnyEvent, MarketDataBarEvent
from app.runtime.event_priorities import priority_for


@dataclass(order=True)
class _HeapItem:
    priority: int
    created_ms: int
    token: int


class PriorityEventQueue:
    def __init__(self, max_depth: int = 500, marketdata_keep_last: bool = True):
        self.max_depth = max_depth
        self.marketdata_keep_last = marketdata_keep_last
        self._heap: list[_HeapItem] = []
        self._events: Dict[int, AnyEvent] = {}
        self._token = 0
        self._md_latest: Dict[Tuple[str, str], int] = {}

    def __len__(self) -> int:
        return len(self._events)

    def push(self, event: AnyEvent) -> None:
        prio = priority_for(event.event_type)
        created_ms = int(time.time() * 1000)
        self._token += 1
        token = self._token

        if self.marketdata_keep_last and isinstance(event, MarketDataBarEvent):
            key = (event.symbol or "", event.timeframe)
            old = self._md_latest.get(key)
            if old is not None:
                self._events.pop(old, None)
            self._md_latest[key] = token

        if len(self._events) >= self.max_depth:
            self._drop_for_capacity(incoming_priority=prio)

        if len(self._events) >= self.max_depth and prio > 20:
            return

        self._events[token] = event
        heapq.heappush(self._heap, _HeapItem(priority=prio, created_ms=created_ms, token=token))

    def pop(self) -> Optional[AnyEvent]:
        while self._heap:
            item = heapq.heappop(self._heap)
            evt = self._events.pop(item.token, None)
            if evt is None:
                continue

            if self.marketdata_keep_last and isinstance(evt, MarketDataBarEvent):
                key = (evt.symbol or "", evt.timeframe)
                if self._md_latest.get(key) == item.token:
                    self._md_latest.pop(key, None)

            return evt
        return None

    def _drop_for_capacity(self, incoming_priority: int) -> None:
        if incoming_priority <= 50:
            md = [t for t, e in self._events.items() if getattr(e, "event_type", "") == "MARKET_DATA_BAR"]
            if md:
                self._events.pop(md[0], None)
                return

        worst_token = None
        worst_prio = -1
        for t, e in self._events.items():
            p = priority_for(getattr(e, "event_type", ""))
            if p > worst_prio:
                worst_prio = p
                worst_token = t
        if worst_token is not None:
            self._events.pop(worst_token, None)
