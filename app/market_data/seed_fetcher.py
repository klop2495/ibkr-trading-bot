from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List

from app.market_data.timeframes import timeframe_seconds


@dataclass
class SeedBar:
    date: datetime
    open: float
    high: float
    low: float
    close: float


class SeedFetcher:
    """
    Deterministic seed data generator for offline runs.
    """

    def __init__(self, seed: int = 42, spread_value: float = 0.0002):
        self.seed = seed
        self.spread_value = spread_value

    def fetch_historical_bars(self, symbol: str, timeframe: str, end_dt_utc: datetime, warmup_bars_min: int) -> List[SeedBar]:
        interval_sec = timeframe_seconds(timeframe)
        total = warmup_bars_min + 5
        base_ts = end_dt_utc.replace(tzinfo=timezone.utc) if end_dt_utc.tzinfo is None else end_dt_utc
        bars: List[SeedBar] = []
        # deterministic price path
        for i in range(total):
            idx = self.seed + i
            ts = base_ts - timedelta(seconds=interval_sec * (total - i))
            price = 1.0 + (idx % 100) * 0.0001
            bar = SeedBar(
                date=ts,
                open=price,
                high=price + 0.0002,
                low=price - 0.0002,
                close=price + 0.00005,
            )
            bars.append(bar)
        return bars

    def fetch_spread(self, symbol: str) -> float:
        return self.spread_value
