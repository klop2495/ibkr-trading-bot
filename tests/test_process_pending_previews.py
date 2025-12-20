from types import SimpleNamespace

import pytest

import app.main as main_mod
from uuid import uuid4


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, data):
        self._data = list(data)
        self._limit = None

    def select(self, *args, **kwargs):
        return self

    def is_(self, *args, **kwargs):
        return self

    def in_(self, field, values):
        self._data = [row for row in self._data if row.get(field) in values]
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        if args:
            try:
                self._limit = int(args[0])
            except Exception:
                self._limit = None
        return self

    def execute(self):
        rows = self._data
        if self._limit is not None:
            rows = rows[: self._limit]
        return FakeResponse(rows)


class FakeClient:
    def __init__(self, preview_rows, decision_rows=None):
        self.preview_rows = preview_rows
        self.decision_rows = decision_rows or []

    def table(self, _table):
        if _table == "control_decisions":
            return FakeQuery(self.decision_rows)
        return FakeQuery(self.preview_rows)


class DummyDecisionsRepo:
    def __init__(self):
        self.saved = []
        self.bulk_called = False

    def insert_decisions_bulk(self, decisions):
        ids = []
        self.bulk_called = True
        for dec in decisions:
            self.saved.append(dec)
            ids.append(str(uuid4()))
        return ids

    def insert_decision(self, dec):
        self.saved.append(dec)
        return str(uuid4())


class DummyVerdictsRepo:
    def __init__(self):
        self.saved = []
        self.bulk_called = False

    def insert_verdicts_bulk(self, verdicts):
        ids = []
        self.bulk_called = True
        for v in verdicts:
            self.saved.append(v)
            ids.append(str(uuid4()))
        return ids

    def insert_verdict(self, v):
        self.saved.append(v)
        return str(uuid4())


class DummyEventsRepo:
    def __init__(self):
        self.events = []

    def insert(self, **kwargs):
        self.events.append(kwargs)
        return kwargs


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([], 0),
        ([{"id": "p1", "ts_utc": "2024-01-01T00:00:00Z", "symbol": "EURUSD", "timeframe_trigger": "M15", "setup_type": "NO_TRADE", "direction": "flat", "setup_present": False, "entry_triggered": False, "confidence": "low", "rr": 0.0, "data_quality": "ok", "spread_quality": "ok", "flags": []}], 1),
    ],
)
def test_process_pending_previews_calls_persist(monkeypatch, rows, expected):
    fake_client = FakeClient(rows)
    decisions_repo = DummyDecisionsRepo()
    verdicts_repo = DummyVerdictsRepo()
    events_repo = DummyEventsRepo()

    result = main_mod.process_pending_previews(
        client=fake_client,
        params=None,
        settings=SimpleNamespace(trading_enabled=True),
        agents_aggregator=None,
        risk_engine=main_mod.RiskEngineV1(),
        decisions_repo=decisions_repo,
        risk_verdicts_repo=verdicts_repo,
        risk_events_repo=events_repo,
        limit=10,
        start_ts=main_mod.time.perf_counter(),
        max_seconds=1.0,
        safety_ms=150,
    )

    assert result["processed"] == expected
    assert decisions_repo.bulk_called is (expected > 0)
    assert verdicts_repo.bulk_called is (expected > 0)
    assert result["fetch_ms"] >= 0
    assert result["persist_ms"] >= 0
    assert result["early_break"] is False
    assert result["fetched"] == expected
    if expected == 0:
        assert result["sample"] is None
    else:
        assert result["sample"] is not None
