from datetime import datetime, timezone

from app.forecast.engine import ForecastEngine


def _ts(hour: int) -> datetime:
    return datetime(2026, 3, 3, hour, 0, 0, tzinfo=timezone.utc)


def test_alt4_inverted_hour_15():
    engine = ForecastEngine()
    direction, mode, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="medium",
        ts_utc=_ts(15),
    )
    assert direction == "down"
    assert mode == "inverted"
    assert eligible is True


def test_alt4_original_hour_9():
    engine = ForecastEngine()
    direction, mode, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="down",
        h30_confidence="high",
        ts_utc=_ts(9),
    )
    assert direction == "down"
    assert mode == "original"
    assert eligible is True


def test_alt4_skip_hour_7():
    engine = ForecastEngine()
    direction, mode, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="medium",
        ts_utc=_ts(7),
    )
    assert direction is None
    assert mode is None
    assert eligible is None


def test_alt4_neutral_input():
    engine = ForecastEngine()
    direction, mode, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="neutral",
        h30_confidence="medium",
        ts_utc=_ts(15),
    )
    assert direction is None
    assert mode is None
    assert eligible is None


def test_alt4_excluded_symbol():
    engine = ForecastEngine()
    direction, mode, eligible = engine._compute_alt4_signal(
        symbol="CADJPY",
        h30_direction="up",
        h30_confidence="medium",
        ts_utc=_ts(15),
    )
    assert direction == "down"
    assert mode == "inverted"
    assert eligible is False


def test_alt4_eligible_medium():
    engine = ForecastEngine()
    direction, mode, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="medium",
        ts_utc=_ts(15),
    )
    assert direction == "down"
    assert mode == "inverted"
    assert eligible is True


def test_alt4_low_confidence():
    engine = ForecastEngine()
    direction, mode, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="low",
        ts_utc=_ts(15),
    )
    assert direction == "down"
    assert mode == "inverted"
    assert eligible is False


def test_alt4_mode_field():
    engine = ForecastEngine()
    _, mode_a, _ = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="medium",
        ts_utc=_ts(9),
    )
    _, mode_b, _ = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="medium",
        ts_utc=_ts(15),
    )
    assert mode_a == "original"
    assert mode_b == "inverted"


def test_alt4_all_hours():
    engine = ForecastEngine()
    for hour in range(24):
        direction, mode, eligible = engine._compute_alt4_signal(
            symbol="EURUSD",
            h30_direction="up",
            h30_confidence="medium",
            ts_utc=_ts(hour),
        )
        if hour in {9, 10, 19}:
            assert direction == "up"
            assert mode == "original"
            assert eligible is True
        elif hour in {15, 20, 21, 22}:
            assert direction == "down"
            assert mode == "inverted"
            assert eligible is True
        else:
            assert direction is None
            assert mode is None
            assert eligible is None


def test_alt4_no_impact_on_alt2():
    engine = ForecastEngine()
    votes = {"ma_cross_inv": 1, "price_vs_ma": -1, "momentum": -1}
    alt2_dir = engine._compute_alt2_signal("EURUSD", votes, 40.0)
    assert alt2_dir == "up"

    # Alt4 should not mutate the votes object or affect alt2 outcome.
    _ = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="down",
        h30_confidence="medium",
        ts_utc=_ts(15),
    )
    alt2_dir_after = engine._compute_alt2_signal("EURUSD", votes, 40.0)
    assert alt2_dir_after == "up"


def test_alt4_custom_env(monkeypatch):
    monkeypatch.setenv("ALT4_ORIGINAL_HOURS", "8")
    monkeypatch.setenv("ALT4_INVERTED_HOURS", "12")
    monkeypatch.setenv("ALT4_EXCLUDE_SYMBOLS", "EURUSD")
    monkeypatch.setenv("ALT4_ELIGIBLE_CONFIDENCE", "high")
    engine = ForecastEngine()
    direction, mode, eligible = engine._compute_alt4_signal(
        symbol="EURUSD",
        h30_direction="up",
        h30_confidence="high",
        ts_utc=_ts(12),
    )
    assert direction == "down"
    assert mode == "inverted"
    assert eligible is False
