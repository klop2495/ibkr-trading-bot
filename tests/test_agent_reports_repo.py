from datetime import datetime, timezone

from app.models.agent_report import AgentReport
from app.storage.agent_reports_repo import AgentReportsRepo


class DummyResult:
    data = []


class DummyTable:
    def __init__(self):
        self.payload = None
        self.executed = False
        self.table_name = None

    def insert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        self.executed = True
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


def test_agent_report_serialization_and_insert():
    db = DummyDB()
    repo = AgentReportsRepo(db=db)  # type: ignore[arg-type]
    report = AgentReport(trade_allowed=True, risk_modifier=0.9, flags=["OK"], comment="ok")
    res = repo.insert(decision_id="d1", agent_name="mock", report=report)

    assert res["count"] == 0 or res["count"] == len(DummyResult().data or [])
    table = db.client.table_obj
    assert table.table_name == "agent_reports"
    assert table.payload["decision_id"] == "d1"
    assert table.payload["agent_name"] == "mock"
    assert table.payload["risk_modifier"] == 0.9
    assert table.payload["flags"] == ["OK"]
