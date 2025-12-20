from datetime import datetime, timezone
from uuid import uuid4

import app.main as main_mod
from app.models.signal_preview import SignalPreviewV1, SetupType, Direction, Confidence, DataQuality, SpreadQuality


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self._limit = None

    def select(self, *args, **kwargs):
        return self

    def in_(self, field, values):
        self.rows = [row for row in self.rows if row.get(field) in values]
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        if _args:
            try:
                self._limit = int(_args[0])
            except Exception:
                self._limit = None
        return self

    def execute(self):
        rows = self.rows
        if self._limit is not None:
            rows = rows[: self._limit]
        return FakeResponse(rows)


class FakeClient:
    def __init__(self, preview_rows, decision_rows=None):
        self.preview_rows = preview_rows
        self.decision_rows = decision_rows or []

    def table(self, _name):
        if _name == "control_decisions":
            return FakeQuery(self.decision_rows)
        return FakeQuery(self.preview_rows)


class BulkDecisionsRepo:
    def __init__(self):
        self.bulk_called = False
        self.saved = []

    def insert_decisions_bulk(self, decisions):
        self.bulk_called = True
        self.saved.extend(decisions)
        return [str(uuid4()) for _ in decisions]

    def insert_decision(self, dec):
        self.saved.append(dec)
        return str(uuid4())


class BulkVerdictsRepo:
    def __init__(self, fail_bulk=False):
        self.bulk_called = False
        self.saved = []
        self.fail_bulk = fail_bulk

    def insert_verdicts_bulk(self, verdicts):
        if self.fail_bulk:
            raise RuntimeError("bulk-fail")
        self.bulk_called = True
        self.saved.extend(verdicts)
        return [str(uuid4()) for _ in verdicts]

    def insert_verdict(self, verdict):
        self.saved.append(verdict)
        return str(uuid4())


class DummyRiskEventsRepo:
    def __init__(self):
        self.events = []

    def insert(self, **kwargs):
        self.events.append(kwargs)
        return kwargs


def _row(symbol="EURUSD"):
    return {
        "id": str(uuid4()),
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "timeframe_trigger": "M15",
        "setup_type": SetupType.NO_TRADE.value,
        "direction": Direction.FLAT.value,
        "setup_present": False,
        "entry_triggered": False,
        "confidence": Confidence.LOW.value,
        "rr": 0.0,
        "data_quality": DataQuality.OK.value,
        "spread_quality": SpreadQuality.OK.value,
        "flags": [],
    }


def test_process_pending_previews_bulk_path(monkeypatch):
    rows = [_row(), _row("GBPUSD")]
    client = FakeClient(rows)
    decisions_repo = BulkDecisionsRepo()
    verdicts_repo = BulkVerdictsRepo()
    result = main_mod.process_pending_previews(
        client=client,
        params=None,
        settings=type("S", (), {"trading_enabled": True})(),
        agents_aggregator=None,
        risk_engine=main_mod.RiskEngineV1(),
        decisions_repo=decisions_repo,
        risk_verdicts_repo=verdicts_repo,
        risk_events_repo=DummyRiskEventsRepo(),
        limit=10,
        start_ts=main_mod.time.perf_counter(),
        max_seconds=1.0,
        safety_ms=150,
    )
    assert result["fetched"] == 2
    assert result["processed"] == 2
    assert decisions_repo.bulk_called is True
    assert verdicts_repo.bulk_called is True
    assert result["sample"] is not None


def test_process_pending_previews_bulk_fallback(monkeypatch):
    rows = [_row()]
    client = FakeClient(rows)
    decisions_repo = BulkDecisionsRepo()
    verdicts_repo = BulkVerdictsRepo(fail_bulk=True)
    events_repo = DummyRiskEventsRepo()
    result = main_mod.process_pending_previews(
        client=client,
        params=None,
        settings=type("S", (), {"trading_enabled": True})(),
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
    assert result["fetched"] == 1
    assert result["processed"] == 1
    assert verdicts_repo.bulk_called is False
    assert events_repo.events and events_repo.events[0]["event_type"] == "CONTROL_PLANE_BACKFILL"
