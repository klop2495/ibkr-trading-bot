from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import app.main as main_mod
from app.models.signal_preview import Confidence, DataQuality, Direction, SetupType, SpreadQuality


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self._limit = None

    def select(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def in_(self, field, values):
        self.rows = [row for row in self.rows if row.get(field) in values]
        return self

    def limit(self, limit_value):
        try:
            self._limit = int(limit_value)
        except Exception:
            self._limit = None
        return self

    def execute(self):
        rows = self.rows
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class FakeClient:
    def __init__(self, preview_rows, decision_rows):
        self.preview_rows = preview_rows
        self.decision_rows = decision_rows

    def table(self, name):
        if name == "control_decisions":
            return FakeQuery(self.decision_rows)
        return FakeQuery(self.preview_rows)


class DummyDecisionsRepo:
    def __init__(self):
        self.bulk_called = False
        self.saved = []

    def insert_decisions_bulk(self, decisions):
        self.bulk_called = True
        self.saved.extend(decisions)
        return [str(uuid4()) for _ in decisions]

    def insert_decision(self, decision):
        self.saved.append(decision)
        return str(uuid4())


class DummyVerdictsRepo:
    def __init__(self):
        self.bulk_called = False
        self.saved = []

    def insert_verdicts_bulk(self, verdicts):
        self.bulk_called = True
        self.saved.extend(verdicts)
        return [str(uuid4()) for _ in verdicts]

    def insert_verdict(self, verdict):
        self.saved.append(verdict)
        return str(uuid4())


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


def test_pending_detection_filters_decisions():
    first = _row()
    second = _row("GBPUSD")
    preview_rows = [first, second]
    decision_rows = [{"signal_preview_id": first["id"]}]
    client = FakeClient(preview_rows, decision_rows)
    decisions_repo = DummyDecisionsRepo()
    verdicts_repo = DummyVerdictsRepo()

    result = main_mod.process_pending_previews(
        client=client,
        params=None,
        settings=SimpleNamespace(trading_enabled=True),
        agents_aggregator=None,
        risk_engine=main_mod.RiskEngineV1(),
        decisions_repo=decisions_repo,
        risk_verdicts_repo=verdicts_repo,
        risk_events_repo=None,
        limit=10,
        start_ts=main_mod.time.perf_counter(),
        max_seconds=1.0,
        safety_ms=150,
    )

    assert result["fetched"] == 1
    assert result["processed"] == 1
    assert decisions_repo.bulk_called is True
    assert verdicts_repo.bulk_called is True
    # ensured decision with existing link was skipped
    assert all(str(dec.signal_preview_id) != first["id"] for dec in decisions_repo.saved)
