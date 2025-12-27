from datetime import datetime, timedelta, timezone

import pytest

from app.market_data.seed_fetcher import SeedFetcher, SeedBar
from app.market_data.service import MarketDataService


class DummySnapshotsRepo:
    def __init__(self):
        self.rows = []

    def insert(self, snap):
        self.rows.append(snap)


class DummyRiskRepo:
    def __init__(self):
        self.events = []

    def insert(self, event_type, severity="info", symbol=None, message=None, data=None):
        self.events.append((event_type, severity, symbol, message, data))


def test_seed_fetcher_generates_snapshots_and_backpressure():
    fetcher = SeedFetcher(seed=1)
    snap_repo = DummySnapshotsRepo()
    risk_repo = DummyRiskRepo()
    svc = MarketDataService(
        symbols=["EURUSD"],
        timeframes=["M15", "H1", "H4"],
        warmup_bars_min=5,
        fetcher=fetcher,
        snapshots_repo=snap_repo,
        risk_events_repo=risk_repo,
    )
    ready, snaps = svc.process(end_dt_utc=datetime.now(timezone.utc))
    assert ready is True
    assert len(snaps) == 3
    assert len(snap_repo.rows) == 3
    assert risk_repo.events == []
    # backpressure kept warmup+5 bars
    assert svc.last_bar_counts[("EURUSD", "M15")] == 10


class GapFetcher:
    def fetch_historical_bars(self, symbol, timeframe, end_dt_utc, warmup_bars_min):
        ts = end_dt_utc.replace(tzinfo=timezone.utc)
        bar1 = SeedBar(date=ts - timedelta(minutes=30), open=1.0, high=1.1, low=0.9, close=1.05)
        bar2 = SeedBar(date=ts - timedelta(minutes=2), open=1.1, high=1.2, low=1.0, close=1.15)
        bar3 = SeedBar(date=ts - timedelta(minutes=2), open=1.1, high=1.2, low=1.0, close=1.15)  # duplicate ts
        return [bar1, bar2, bar3]

    def fetch_spread(self, symbol):
        return 0.0


def test_qa_logs_duplicates_and_gaps():
    fetcher = GapFetcher()
    risk_repo = DummyRiskRepo()
    svc = MarketDataService(
        symbols=["EURUSD"],
        timeframes=["M15"],
        warmup_bars_min=2,
        fetcher=fetcher,
        snapshots_repo=None,
        risk_events_repo=risk_repo,
    )
    ready, snaps = svc.process(end_dt_utc=datetime.now(timezone.utc))
    assert ready is True
    assert len(snaps) == 1
    issues = {e[0] for e in risk_repo.events}
    assert "DATA_DUP" in issues or "DATA_GAP" in issues
    # data_quality mapped
    assert svc.last_qa_issues[("EURUSD", "M15")]


class SpreadFetcher:
    def __init__(self, price_spread: float):
        self.price_spread = price_spread

    def fetch_historical_bars(self, symbol, timeframe, end_dt_utc, warmup_bars_min):
        ts = end_dt_utc.replace(tzinfo=timezone.utc)
        return [
            SeedBar(date=ts - timedelta(minutes=3), open=1.0, high=1.1, low=0.9, close=1.0),
            SeedBar(date=ts - timedelta(minutes=2), open=1.0, high=1.1, low=0.9, close=1.0),
            SeedBar(date=ts - timedelta(minutes=1), open=1.0, high=1.1, low=0.9, close=1.0),
        ]

    def fetch_spread(self, symbol):
        return self.price_spread


@pytest.mark.parametrize(
    "symbol,price_spread,expected_pips",
    [
        ("EURUSD", 0.0002, 2.0),  # pip size 0.0001
        ("USDJPY", 0.02, 2.0),    # pip size 0.01
    ],
)
def test_spread_converted_to_pips(symbol, price_spread, expected_pips):
    fetcher = SpreadFetcher(price_spread=price_spread)
    snap_repo = DummySnapshotsRepo()
    svc = MarketDataService(
        symbols=[symbol],
        timeframes=["M15"],
        warmup_bars_min=2,
        fetcher=fetcher,
        snapshots_repo=snap_repo,
        risk_events_repo=None,
    )
    ready, snaps = svc.process(end_dt_utc=datetime.now(timezone.utc))
    assert ready is True
    assert len(snap_repo.rows) == 1
    assert snap_repo.rows[0].spread == pytest.approx(expected_pips)
