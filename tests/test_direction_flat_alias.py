from app import main as main_mod
from app.models.signal_preview import Direction, SetupType, Confidence, DataQuality, SpreadQuality


def test_preview_from_row_accepts_flat_string():
    row = {
        "id": "1",
        "ts_utc": "2024-01-01T00:00:00+00:00",
        "symbol": "EURUSD",
        "timeframe_trigger": "M15",
        "setup_type": SetupType.NO_TRADE.value,
        "direction": "FLAT",
        "setup_present": False,
        "entry_triggered": False,
        "confidence": Confidence.LOW.value,
        "rr": 0.0,
        "data_quality": DataQuality.OK.value,
        "spread_quality": SpreadQuality.OK.value,
        "flags": [],
    }

    preview = main_mod._preview_from_row(row)

    assert preview.direction == Direction.FLAT
    assert preview.symbol == "EURUSD"
