from types import SimpleNamespace
from uuid import uuid4

import app.main as main_mod
from app.models.signal_preview import Confidence, DataQuality, Direction, SetupType, SpreadQuality


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self._order_ascending = True
        self._limit = None

    def select(self, *args, **kwargs):
        return self

    def order(self, _col, cfg=None, **kwargs):
        desc = kwargs.get("desc")
        if desc is not None:
            self._order_ascending = not bool(desc)
        elif isinstance(cfg, dict):
            self._order_ascending = cfg.get("ascending", True)
        elif cfg is not None:
            self._order_ascending = not bool(cfg)
        else:
            self._order_ascending = True
        return self

    def in_(self, field, values):
        self.rows = [row for row in self.rows if row.get(field) in values]
        return self

    def gt(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) > value]
        return self

    def limit(self, value):
        self._limit = int(value)
        return self

    def execute(self):
        rows = sorted(self.rows, key=lambda r: r.get("ts_utc", ""), reverse=not self._order_ascending)
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class FakeClient:
    def __init__(self, preview_rows, decision_rows=None):
        self.preview_rows = preview_rows
        self.decision_rows = decision_rows or []

    def table(self, name):
        if name == "control_decisions":
            return FakeQuery(self.decision_rows)
        return FakeQuery(self.preview_rows)


def _row(symbol="EURUSD"):
    return {
        "id": str(uuid4()),
        "ts_utc": "2024-01-01T00:00:00+00:00",
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


def test_symbols_empty_fallback_fetches_all(monkeypatch):
    client = FakeClient([_row(), _row("GBPUSD")])
    # repos return deterministic ids
    decisions_repo = type("D", (), {"insert_decisions_bulk": lambda self, decs: [str(uuid4()) for _ in decs], "insert_decision": lambda self, dec: str(uuid4())})()
    verdicts_repo = type(
        "V",
        (),
        {
            "insert_verdicts_bulk": lambda self, verd: [str(uuid4()) for _ in verd],
            "insert_verdict": lambda self, v: str(uuid4()),
        },
    )()

    processed_total, fetched_total, scanned_total, _, stop_reason, _, _, _, _, _, _, _, _, _ = main_mod.run_backfill_tick(
        client=client,
        params=None,
        settings=SimpleNamespace(trading_enabled=True, symbols=[]),
        agents_aggregator=None,
        risk_engine=main_mod.RiskEngineV1(),
        decisions_repo=decisions_repo,
        risk_verdicts_repo=verdicts_repo,
        risk_events_repo=None,
        batch_size=5,
        backfill_max_per_tick=5,
        backfill_max_seconds=5.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
        historical_cursor_ts=None,
        symbols_empty=True,
    )

    assert scanned_total >= 2
    assert fetched_total == 2
    assert processed_total == fetched_total
    assert stop_reason in {"DRAINED_BATCH", "MAX_PER_TICK", "MAX_SECONDS"}


def test_symbols_filter_applied_when_present(monkeypatch):
    client = FakeClient([_row(), _row("GBPUSD")])

    processed_total, fetched_total, scanned_total, _, stop_reason, _, _, _, _, _, _, _, _, _ = main_mod.run_backfill_tick(
        client=client,
        params=None,
        settings=SimpleNamespace(trading_enabled=True, symbols=["EURUSD"]),
        agents_aggregator=None,
        risk_engine=main_mod.RiskEngineV1(),
        decisions_repo=type("D", (), {"insert_decisions_bulk": lambda self, decs: [str(uuid4()) for _ in decs], "insert_decision": lambda self, dec: str(uuid4())})(),
        risk_verdicts_repo=type(
            "V",
            (),
            {
                "insert_verdicts_bulk": lambda self, verd: [str(uuid4()) for _ in verd],
                "insert_verdict": lambda self, v: str(uuid4()),
            },
        )(),
        risk_events_repo=None,
        batch_size=5,
        backfill_max_per_tick=5,
        backfill_max_seconds=5.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
        historical_cursor_ts=None,
        symbols_empty=False,
    )

    assert scanned_total >= 1
    assert fetched_total == 1
    assert processed_total == 1
    assert stop_reason in {"DRAINED_BATCH", "MAX_PER_TICK", "MAX_SECONDS"}
