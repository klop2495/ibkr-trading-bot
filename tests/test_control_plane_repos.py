from uuid import UUID

from app.storage.repositories import DecisionsRepo, RiskVerdictsRepo


class DummyResult:
    def __init__(self, data=None):
        self.data = data or []


class DummyTable:
    def __init__(self):
        self.insert_called = False
        self.payload = None
        self.select_called = False
        self.eq_called = []

    def insert(self, payload):
        self.insert_called = True
        self.payload = payload
        return self

    def execute(self):
        return DummyResult([])  # simulate no rows returned

    def select(self, *args, **kwargs):
        self.select_called = True
        return self

    def eq(self, *args, **kwargs):
        self.eq_called.append((args, kwargs))
        return self

    def limit(self, *args, **kwargs):
        return self


class DummyClient:
    def __init__(self):
        self.table_obj = DummyTable()

    def table(self, name):
        return self.table_obj


def test_decisions_repo_insert_return_id_generates_uuid():
    client = DummyClient()
    repo = DecisionsRepo(db=type("Obj", (), {"client": client})())  # type: ignore
    decision = type(
        "Dec",
        (),
        {
            "to_db_row": lambda self=None: {"signal_preview_id": "prev-1", "decision_version": 1},
        },
    )()

    decision_id = repo.insert_decision(decision)

    assert client.table_obj.insert_called is True
    assert UUID(decision_id)
    assert decision_id == client.table_obj.payload["id"]


def test_risk_verdicts_repo_insert_return_id_generates_uuid():
    client = DummyClient()
    repo = RiskVerdictsRepo(db=type("Obj", (), {"client": client})())  # type: ignore
    verdict = type(
        "Verdict",
        (),
        {
            "to_db_row": lambda self=None: {"decision_id": "dec-1", "risk_version": 1},
        },
    )()

    verdict_id = repo.insert_verdict(verdict)

    assert client.table_obj.insert_called is True
    assert UUID(verdict_id)
    assert verdict_id == client.table_obj.payload["id"]
