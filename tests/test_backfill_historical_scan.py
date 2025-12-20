from datetime import datetime, timedelta, timezone
from uuid import uuid4

import app.main as main_mod
from app.models.signal_preview import Confidence, DataQuality, Direction, SetupType, SpreadQuality


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self._limit = None
        self._order_ascending = True

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

    def lt(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) < value]
        return self

    def limit(self, value):
        try:
            self._limit = int(value)
        except Exception:
            self._limit = None
        return self

    def execute(self):
        rows = sorted(self.rows, key=lambda r: r.get("ts_utc", ""), reverse=not self._order_ascending)
        if self._limit is not None:
            rows = rows[: self._limit]
        return type("R", (), {"data": rows})


class FakeClient:
    def __init__(self, preview_rows, decision_rows):
        self.preview_rows = preview_rows
        self.decision_rows = decision_rows

    def table(self, name):
        if name == "control_decisions":
            return FakeQuery(self.decision_rows)
        return FakeQuery(self.preview_rows)


def _row(ts_utc):
    return {
        "id": ts_utc.isoformat(),
        "ts_utc": ts_utc.isoformat(),
        "symbol": "EURUSD",
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


def test_historical_scan_processes_old_pending(monkeypatch):
    now = datetime.now(timezone.utc)
    ts1 = now - timedelta(days=5)
    ts2 = now - timedelta(days=4)
    ts3 = now
    previews = [_row(ts3), _row(ts2), _row(ts1)]
    decisions = [{"signal_preview_id": previews[0]["id"]}, {"signal_preview_id": previews[1]["id"]}]

    client = FakeClient(previews, decisions)

    processed_total, fetched_total, scanned_total, sample_ids, stop_reason, _, _, _, _, _, _, _, _, new_cursor = main_mod.run_backfill_tick(
        client=client,
        params=None,
        settings=type("S", (), {"trading_enabled": True, "symbols": None})(),
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
        batch_size=2,
        backfill_max_per_tick=2,
        backfill_max_seconds=5.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
        historical_cursor_ts=None,
    )

    assert fetched_total == 1  # only oldest pending processed
    assert processed_total == 1
    assert stop_reason != "NO_PENDING"
    assert sample_ids is not None
    assert new_cursor is not None
    assert new_cursor >= ts2.isoformat()


def test_historical_advances_cursor_when_first_batch_drained(monkeypatch):
    now = datetime.now(timezone.utc)
    ts1 = now - timedelta(days=5)
    ts2 = now - timedelta(days=4)
    ts3 = now - timedelta(days=3)
    previews = [_row(ts1), _row(ts2), _row(ts3)]
    decisions = [
        {"signal_preview_id": previews[1]["id"]},
        {"signal_preview_id": previews[2]["id"]},
    ]

    client = FakeClient(previews, decisions)

    processed_total, fetched_total, scanned_total, sample_ids, stop_reason, _, _, _, _, _, _, _, _, new_cursor = main_mod.run_backfill_tick(
        client=client,
        params=None,
        settings=type("S", (), {"trading_enabled": True, "symbols": None})(),
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
        batch_size=2,
        backfill_max_per_tick=3,
        backfill_max_seconds=5.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
        historical_cursor_ts=None,
    )

    assert scanned_total >= 1
    assert fetched_total >= 1
    assert processed_total >= 1
    assert stop_reason in {"DRAINED_BATCH", "MAX_PER_TICK", "MAX_SECONDS"}
