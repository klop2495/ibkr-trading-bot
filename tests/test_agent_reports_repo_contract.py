from datetime import datetime, timezone

from app.models.agent_report import AgentReport
from app.storage.agent_reports_repo import AgentReportsRepo


class DummyResult:
    def __init__(self):
        self.data = []


class DummyTable:
    def __init__(self):
        self.payload = None
        self.table_name = None

    def insert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        return DummyResult()


class DummyClient:
    def __init__(self):
        self.table_obj = DummyTable()

    def table(self, name):
        self.table_obj.table_name = name
        return self.table_obj


class DummyDB:
    def __init__(self):
        self.client = DummyClient()


def test_agent_reports_repo_payload():
    db = DummyDB()
    repo = AgentReportsRepo(db=db)  # type: ignore[arg-type]
    report = AgentReport(trade_allowed=True, risk_modifier=0.9, flags=["ok"], comment="c")
    res = repo.insert(report=report)

    table = db.client.table_obj
    assert table.table_name == "agent_reports"
    assert table.payload["schema_version"] == 1
    assert table.payload["ts"] == ts.isoformat()
    assert table.payload["scope"] == "portfolio"
    assert table.payload["symbol"] is None
    assert table.payload["trade_allowed"] is True
    assert table.payload["risk_modifier"] == 0.9
    assert table.payload["flags"] == ["ok"]
    assert res["count"] == 0 or res["count"] == len(DummyResult().data or [])
