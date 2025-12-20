from typing import Dict, Tuple, Any


class MarketDataBuffer:
    """
    Backpressure buffer: keep only the latest N events per (symbol, timeframe).
    """

    def __init__(self, max_bars: int = 1):
        self._store: Dict[Tuple[str, str], list[Any]] = {}
        self.max_bars = max_bars

    def push(self, symbol: str, timeframe: str, bars: list[Any] | Any) -> list[Any]:
        if not isinstance(bars, (list, tuple)):
            bars = [bars]
        existing = self._store.get((symbol, timeframe), [])
        combined = existing + list(bars)
        trimmed = combined[-self.max_bars :] if len(combined) > self.max_bars else combined
        self._store[(symbol, timeframe)] = trimmed
        return trimmed

    def get(self, symbol: str, timeframe: str) -> list[Any]:
        return self._store.get((symbol, timeframe), [])

    def pop_all(self) -> Dict[Tuple[str, str], list[Any]]:
        if self.max_bars == 1:
            snapshot = {k: v[-1] for k, v in self._store.items() if v}
        else:
            snapshot = dict(self._store)
        self._store.clear()
        return snapshot
