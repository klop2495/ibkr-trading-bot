from app import main as main_mod
from app.models.signal_preview import Confidence, DataQuality, Direction, SetupType, SpreadQuality


def test_preview_from_row_handles_legacy_strings():
    row = {
        "id": "1",
        "ts_utc": "2024-01-01T00:00:00+00:00",
        "symbol": "EURUSD",
        "timeframe_trigger": "M15",
        "setup_type": "NO_TRADE",
        "direction": "FLAT",
        "setup_present": False,
        "entry_triggered": False,
        "confidence": "LOW",
        "rr": 0.0,
        "data_quality": "OK",
        "spread_quality": "OK",
        "flags": [],
    }

    preview = main_mod._preview_from_row(row)

    assert preview.direction == Direction.FLAT
    assert preview.setup_type == SetupType.NO_TRADE
    assert preview.confidence == Confidence.LOW
    assert preview.data_quality == DataQuality.OK
    assert preview.spread_quality == SpreadQuality.OK
