from datetime import datetime, timezone

import time

from app.broker.ib_utils import IBTimeoutError
from app.market_data.service import MarketDataService


class DummyFetcher:
    def fetch_historical_bars(self, *_args, **_kwargs):
        raise IBTimeoutError("timeout")


class DummyRiskRepo:
    def __init__(self):
        self.events = []

    def insert(self, **kwargs):
        self.events.append(kwargs)


def _monotonic_seq(values):
    iterator = iter(values)
    last = values[-1]

    def _next():
        nonlocal last
        try:
            last = next(iterator)
        except StopIteration:
            pass
        return last

    return _next


def test_market_data_error_rate_limit(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_ERROR_WINDOW_S", "60")
    monkeypatch.setenv("MARKET_DATA_MAX_ERROR_LOGS", "20")
    monkeypatch.setattr(time, "monotonic", _monotonic_seq([0.0, 10.0, 70.0]))

    risk_repo = DummyRiskRepo()
    service = MarketDataService(
        symbols=["EURUSD"],
        timeframes=["M15"],
        warmup_bars_min=10,
        fetcher=DummyFetcher(),
        risk_events_repo=risk_repo,
    )

    now = datetime.now(timezone.utc)
    service.process(now)
    assert len(risk_repo.events) == 1

    service.process(now)
    assert len(risk_repo.events) == 1

    service.process(now)
    assert len(risk_repo.events) == 2
