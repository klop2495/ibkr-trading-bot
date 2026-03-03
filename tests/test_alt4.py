from datetime import datetime, timezone

from app.forecast.engine import ForecastEngine


def _ts(hour: int) -> datetime:
    return datetime(2026, 3, 3, hour, 0, 0, tzinfo=timezone.utc)


def test_alt4_inverted_hour_15():
    engine = ForecastEngine()
    direction, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="medium",
        ts_utc=_ts(15),
    )
    assert direction == "down"
    assert eligible is True


def test_alt4_original_hour_9():
    engine = ForecastEngine()
    direction, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="down",
        h30_confidence="high",
        ts_utc=_ts(9),
    )
    assert direction == "down"
    assert eligible is True


def test_alt4_skip_hour_7():
    engine = ForecastEngine()
    direction, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="medium",
        ts_utc=_ts(7),
    )
    assert direction is None
    assert eligible is None


def test_alt4_neutral_input():
    engine = ForecastEngine()
    direction, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="neutral",
        h30_confidence="medium",
        ts_utc=_ts(15),
    )
    assert direction is None
    assert eligible is None


def test_alt4_excluded_symbol():
    engine = ForecastEngine()
    direction, eligible = engine._compute_alt4_signal(
        symbol="CADJPY",
        h30_direction="up",
        h30_confidence="medium",
        ts_utc=_ts(15),
    )
    assert direction == "down"
    assert eligible is False


def test_alt4_low_confidence():
    engine = ForecastEngine()
    direction, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="low",
        ts_utc=_ts(15),
    )
    assert direction == "down"
    assert eligible is False


def test_alt4_custom_env(monkeypatch):
    monkeypatch.setenv("ALT4_ORIGINAL_HOURS", "8")
    monkeypatch.setenv("ALT4_INVERTED_HOURS", "12")
    monkeypatch.setenv("ALT4_EXCLUDE_SYMBOLS", "EURUSD")
    monkeypatch.setenv("ALT4_ELIGIBLE_CONFIDENCE", "high")
    engine = ForecastEngine()
    direction, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="high",
        ts_utc=_ts(12),
    )
    assert direction == "down"
    assert eligible is False
