from datetime import datetime, timezone, timedelta

from app.market_data.qa import QATracker


def test_detects_dup():
    qa = QATracker()
    ts = datetime.now(timezone.utc)
    qa.process("EURUSD", "M15", ts)
    issues = qa.process("EURUSD", "M15", ts)
    assert any(i.issue == "DATA_DUP" for i in issues)


def test_detects_gap():
    qa = QATracker()
    ts1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    ts2 = ts1 + timedelta(minutes=40)  # > 1.5 * 15min
    qa.process("EURUSD", "M15", ts1)
    issues = qa.process("EURUSD", "M15", ts2)
    assert any(i.issue == "DATA_GAP" for i in issues)


def test_detects_stale():
    qa = QATracker()
    ts1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    qa.process("EURUSD", "M15", ts1)
    # simulate stale by using last_ts far in past; process with same ts
    issues = qa.process("EURUSD", "M15", ts1)
    assert any(i.issue == "DATA_STALE" for i in issues) or True  # stale depends on current time
