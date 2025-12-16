from typing import Dict, Tuple, Any


class MarketDataBuffer:
    """
    Backpressure buffer: keep only the latest event per (symbol, timeframe).
    """

    def __init__(self):
        self._store: Dict[Tuple[str, str], Any] = {}

    def push(self, symbol: str, timeframe: str, bar: Any) -> None:
        self._store[(symbol, timeframe)] = bar

    def pop_all(self) -> Dict[Tuple[str, str], Any]:
        snapshot = dict(self._store)
        self._store.clear()
        return snapshot
