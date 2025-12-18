from datetime import datetime, timezone
from uuid import uuid4

from app.models.order_intent import OrderIntentV1
from app.models.broker_request import BrokerRequestV1
from app.models.reconciliation_report_v1 import ReconciliationReportV1
from app.reconciliation.engine_stub import ReconcilerStub, FLAG_DISABLED


def _broker_request(status="PREPARED"):
    intent_id = uuid4()
    decision_id = uuid4()
    preview_id = uuid4()
    exec_id = uuid4()
    return BrokerRequestV1(
        ts_utc=datetime.now(timezone.utc),
        order_intent_id=intent_id,
        decision_id=decision_id,
        signal_preview_id=preview_id,
        execution_report_id=exec_id,
        status=status,
        flags=[],
        payload={"side": "buy", "intent_type": "OPEN_MARKET"},
    )


def test_reconciliation_disabled_returns_skipped():
    br = _broker_request()
    recon = ReconcilerStub(enabled=False).reconcile(br)
    assert recon.status == "SKIPPED"
    assert FLAG_DISABLED in recon.flags


def test_reconciliation_enabled_ok():
    br = _broker_request()
    recon = ReconcilerStub(enabled=True).reconcile(br)
    assert recon.status in ("OK", "WARN")


def test_reconciliation_repo_idempotent():
    class DupThenSelect:
        def __init__(self):
            self.insert_called = False

        def insert(self, payload):
            self.insert_called = True
            raise Exception("duplicate key value violates unique constraint")

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def execute(self):
            return type("Obj", (), {"data": [{"id": "existing-recon"}]})()

    from app.storage.repositories import ReconciliationReportsRepo

    repo = ReconciliationReportsRepo(db=type("Obj", (), {"client": type("Obj", (), {"table": lambda self, _: DupThenSelect()})()})())  # type: ignore
    recon = ReconciliationReportV1(
        broker_request_id=uuid4(),
        order_intent_id=uuid4(),
        execution_report_id=uuid4(),
        decision_id=uuid4(),
        signal_preview_id=uuid4(),
        ts_utc=datetime.now(timezone.utc),
        status="OK",
        flags=[],
        details={},
    )
    recon_id = repo.insert_report(recon)
    assert recon_id == "existing-recon"
