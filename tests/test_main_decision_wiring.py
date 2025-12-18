from datetime import datetime, timezone
from uuid import uuid4

from app.main import main  # noqa: F401  # ensure import works
from app.models.signal_preview import Confidence, DataQuality, Direction, SetupType, SignalPreviewV1, SpreadQuality
from app.models.signals_params import SignalsParams
from app.agents.runner import aggregate_decision


def test_aggregate_decision_fields():
    # Minimal wiring test: ensure commentary/flags propagate and no signal_rules flag added when configured
    preview = SignalPreviewV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        setup_type=SetupType.SWING_CONTINUATION,
        direction=Direction.LONG,
        setup_present=True,
        entry_triggered=True,
        confidence=Confidence.NORMAL,
        rr=2.0,
        data_quality=DataQuality.OK,
        spread_quality=SpreadQuality.OK,
        flags=[],
    )
    agg = aggregate_decision(preview, {"agent": type("Obj", (), {"trade_allowed": True, "risk_modifier": 0.8, "flags": [], "commentary": None})()})  # type: ignore[arg-type]
    assert agg["trade_allowed"] is True
    assert agg["risk_modifier"] == 0.8
    assert "SIGNALS_RULES_NOT_SPECIFIED" not in agg["flags"]
