from datetime import datetime, timezone

from app.forecast import alt6
from app.forecast.alt6 import compute_alt6_signal
from app.models.forecast import (
    ForecastConfidence,
    ForecastDirection,
    ForecastHorizon,
    ForecastResult,
)


def _forecast(symbol: str = "EURUSD", direction: str = "up", *, bb_squeeze: bool = True, bb_width: float = 0.005) -> ForecastResult:
    return ForecastResult(
        ts_utc=datetime(2026, 3, 16, 10, 3, tzinfo=timezone.utc),
        symbol=symbol,
        horizons=[
            ForecastHorizon(
                horizon_minutes=30,
                direction=ForecastDirection(direction),
                confidence=ForecastConfidence.MEDIUM,
                strength=0.6,
                indicators_aligned=4,
                indicators_total=6,
            )
        ],
        bb_squeeze=bb_squeeze,
        bb_width=bb_width,
    )


def test_alt6_returns_direction_for_valid_up_setup(monkeypatch):
    forecast = _forecast("EURUSD", "up")
    s5 = [(datetime(2026, 3, 16, 10, 2, 0, tzinfo=timezone.utc), 1.1000)] * 10
    monkeypatch.setattr(alt6, "squeeze_breakout_confirm_strict_s5_v2", lambda forecast, s5: True)
    monkeypatch.setattr(alt6, "_breakout_metrics", lambda forecast, s5: (5.0, 0.4))
    monkeypatch.setattr(alt6, "extension_veto", lambda forecast, s5: False)

    direction, eligible, reason = compute_alt6_signal(forecast, s5)

    assert direction == "up"
    assert eligible is True
    assert reason is None


def test_alt6_uses_horizon_model_not_missing_flat_field(monkeypatch):
    forecast = _forecast("USDJPY", "down")
    s5 = [(datetime(2026, 3, 16, 10, 2, 0, tzinfo=timezone.utc), 150.0)] * 10
    monkeypatch.setattr(alt6, "squeeze_breakout_confirm_strict_s5_v2", lambda forecast, s5: True)
    monkeypatch.setattr(alt6, "_breakout_metrics", lambda forecast, s5: (5.0, 0.4))
    monkeypatch.setattr(alt6, "extension_veto", lambda forecast, s5: False)

    direction, eligible, reason = compute_alt6_signal(forecast, s5)

    assert direction == "down"
    assert eligible is True
    assert reason is None


def test_alt6_rejects_stale_breakout(monkeypatch):
    forecast = _forecast("EURUSD", "up")
    s5 = [(datetime(2026, 3, 16, 10, 2, 0, tzinfo=timezone.utc), 1.1000)] * 10
    monkeypatch.setattr(alt6, "DEFAULT_MAX_BREAKOUT_AGE_SEC", 5)
    monkeypatch.setattr(alt6, "squeeze_breakout_confirm_strict_s5_v2", lambda forecast, s5: True)
    monkeypatch.setattr(alt6, "_breakout_metrics", lambda forecast, s5: (30.0, 0.3))
    monkeypatch.setattr(alt6, "extension_veto", lambda forecast, s5: False)

    direction, eligible, reason = compute_alt6_signal(forecast, s5)

    assert direction is None
    assert eligible is False
    assert reason == "BREAKOUT_TOO_OLD"


def test_alt6_rejects_late_entry_far_from_breakout(monkeypatch):
    forecast = _forecast("EURUSD", "up")
    s5 = [(datetime(2026, 3, 16, 10, 2, 0, tzinfo=timezone.utc), 1.1000)] * 10
    monkeypatch.setattr(alt6, "DEFAULT_MAX_BREAKOUT_DISTANCE_PIPS", 0.5)
    monkeypatch.setattr(alt6, "DEFAULT_MAX_BREAKOUT_AGE_SEC", 60)
    monkeypatch.setattr(alt6, "squeeze_breakout_confirm_strict_s5_v2", lambda forecast, s5: True)
    monkeypatch.setattr(alt6, "_breakout_metrics", lambda forecast, s5: (10.0, 1.2))
    monkeypatch.setattr(alt6, "extension_veto", lambda forecast, s5: False)

    direction, eligible, reason = compute_alt6_signal(forecast, s5)

    assert direction is None
    assert eligible is False
    assert reason == "TOO_FAR_FROM_BREAKOUT"
