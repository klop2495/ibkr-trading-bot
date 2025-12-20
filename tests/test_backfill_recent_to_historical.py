from datetime import datetime, timedelta, timezone
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

    def lt(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) < value]
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


def _row(ts):
    return {
        "id": str(uuid4()),
        "ts_utc": ts.isoformat(),
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


def test_switches_to_historical_when_recent_drained():
    now = datetime.now(timezone.utc)
    recent_decided = [now - timedelta(minutes=i) for i in range(50)]  # all decided
    historical_pending = [now - timedelta(days=2) - timedelta(minutes=i) for i in range(5)]  # pending older
    previews = [_row(ts) for ts in recent_decided + historical_pending]
    decisions = [{"signal_preview_id": p["id"]} for p in previews[: len(recent_decided)]]

    client = FakeClient(previews, decisions)

    processed_total, fetched_total, scanned_total, sample_ids, stop_reason, _, _, _, _, _, _, _, _, _ = main_mod.run_backfill_tick(
        client=client,
        params=None,
        settings=SimpleNamespace(trading_enabled=True, symbols=None),
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
        batch_size=50,
        backfill_max_per_tick=60,
        backfill_max_seconds=5.0,
        enabled=True,
        adaptive=False,
        stable_fast_ticks=0,
        historical_cursor_ts=None,
    )

    assert fetched_total > 0
    assert processed_total == fetched_total
    assert scanned_total >= 55
    assert stop_reason in {"DRAINED_BATCH", "MAX_PER_TICK", "MAX_SECONDS", "RECENT_DRAINED"}
