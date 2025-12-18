from datetime import datetime, timezone
from uuid import uuid4

from app.agents.runner import AgentsAggregator
from app.models.signal_preview import Confidence, DataQuality, Direction, SetupType, SignalPreviewV1, SpreadQuality
from app.models.signals_params import SignalsParams


class FakeRepo:
    def __init__(self):
        self.inserted = []

    def insert(self, report, ts_utc, scope, symbol):
        self.inserted.append(report)


def _params():
    return SignalsParams.model_validate(
        {
            "schema_version": 1,
            "enabled": True,
            "rules_version": 1,
            "timeframes": {"h4": "H4", "h1": "H1", "m15": "M15"},
            "gates": {
                "require_warmup": True,
                "require_data_ok": True,
                "require_spread_ok": True,
                "disallow_h4_neutral": True,
            },
            "spread": {"wide_spread_pips": 0.5},
            "structure": {
                "swing_lookback_bars": 20,
                "swing_min_separation_bars": 5,
                "sl_buffer_mode": "PIPS",
                "sl_buffer_pips": 5,
                "min_sl_pips": 5,
                "max_sl_pips": 50,
            },
            "confidence": {"high_conf_rule": "H4_H1_ALIGN_AND_M15_MOMENTUM_CONFIRM"},
            "rr": {"mode": "contextual", "base_rr": 2.0, "high_conf_rr": 3.0},
            "filters": {"trade_hours_utc": ["06:00-20:00"]},
            "m15_confirm": {"rsi_period": 14, "rsi_long_min": 55, "rsi_short_max": 45, "sma_period": 50},
        }
    )


def _preview():
    return SignalPreviewV1(
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


def test_agent_reports_receive_signal_preview_id():
    repo = FakeRepo()
    agg = AgentsAggregator(reports_repo=repo)
    preview_id = uuid4()
    agg.run(_preview(), _params(), signal_preview_id=preview_id)
    assert repo.inserted, "agent reports not inserted"
    assert repo.inserted[0].signal_preview_id == preview_id
