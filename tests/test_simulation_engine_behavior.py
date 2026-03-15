from unittest.mock import MagicMock

from app.simulation.engine import SimulationEngine
from app.simulation.models import BlockReason


def test_process_verdict_blocks_when_preview_distances_missing():
    engine = SimulationEngine(db=MagicMock())
    engine.sim_equity = 3000.0
    engine._fetch_decision = MagicMock(return_value={"symbol": "EURUSD"})
    engine._fetch_signal_preview = MagicMock(
        return_value={
            "entry_triggered": True,
            "direction": "LONG",
            "sl_distance_pips": None,
            "tp_distance_pips": None,
            "flags": ["LEGACY_CONFIRM_FALLBACK"],
        }
    )
    engine._check_admission = MagicMock(return_value=None)
    engine._block_trade = MagicMock()
    engine._open_trade = MagicMock()

    engine._process_verdict(
        {
            "id": "verdict-1",
            "decision_id": "decision-1",
            "signal_preview_id": "preview-1",
            "risk_modifier": 1.0,
        }
    )

    engine._block_trade.assert_called_once()
    args, kwargs = engine._block_trade.call_args
    assert args[1] == BlockReason.INVALID_SL_TP
    assert kwargs["data"]["sl_distance_pips"] is None
    assert kwargs["data"]["tp_distance_pips"] is None
    engine._open_trade.assert_not_called()
