from types import SimpleNamespace

import app.main as main_mod


class DummyQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self._limit = None

    def select(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def in_(self, *_args, **_kwargs):
        return self

    def limit(self, _limit):
        self._limit = _limit
        return self

    def execute(self):
        rows = self.rows
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class DummyClient:
    def __init__(self, preview_rows, decision_rows=None):
        self.preview_rows = preview_rows
        self.decision_rows = decision_rows or []

    def table(self, _name):
        if _name == "control_decisions":
            return DummyQuery(self.decision_rows)
        return DummyQuery(self.preview_rows)


def test_process_pending_previews_returns_contract_dict(monkeypatch):
    client = DummyClient([], [])
    result = main_mod.process_pending_previews(
        client=client,
        params=None,
        settings=SimpleNamespace(trading_enabled=True),
        agents_aggregator=None,
        risk_engine=main_mod.RiskEngineV1(),
        decisions_repo=None,
        risk_verdicts_repo=None,
        risk_events_repo=None,
        limit=5,
        start_ts=main_mod.time.perf_counter(),
        max_seconds=1.0,
        safety_ms=150,
    )
    assert isinstance(result, dict)
    for key in ["processed", "fetched", "fetch_ms", "persist_ms", "early_break", "sample", "scanned"]:
        assert key in result
    assert result["processed"] == 0
    assert result["fetched"] == 0
    assert result["scanned"] >= 0
    assert result["scanned"] == 0
