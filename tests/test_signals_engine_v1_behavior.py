from datetime import datetime, timezone

from app.models.signals_params import SignalsParams
from app.models.snapshot import MarketSnapshot
from app.signals.engine_v1 import (
    FLAG_DATA_WARMUP_NOT_READY,
    FLAG_SPREAD_WIDE,
    FLAG_REGIME_H4_NEUTRAL,
    FLAG_TF_MISMATCH_H4_H1,
    SignalEngineV1,
    Confidence,
    SetupType,
    Direction,
)


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


def _snap(tf: str, ma_fast: float, ma_slow: float, rsi: float = 50, close: float = 1.0, spread: float = 0.1):
    return MarketSnapshot(
        schema_version=1,
        timestamp=datetime.now(timezone.utc),
        symbol="EURUSD",
        timeframe=tf,
        close=close,
        atr=1.0,
        rsi=rsi,
        ma_fast=ma_fast,
        ma_slow=ma_slow,
        spread=spread,
    )


def test_gate_warmup_not_ready_no_trade_flag():
    engine = SignalEngineV1(_params())
    preview = engine.compute_preview_for_symbol("EURUSD", {"M15": _snap("M15", 1, 0.5)}, warmup_ready=False)
    assert preview.setup_type == SetupType.NO_TRADE
    assert preview.direction == Direction.FLAT
    assert FLAG_DATA_WARMUP_NOT_READY in preview.flags


def test_gate_spread_wide_no_trade_flag():
    engine = SignalEngineV1(_params())
    snaps = {"M15": _snap("M15", 1, 0.5, spread=1.0), "H1": _snap("H1", 1, 0.5), "H4": _snap("H4", 1, 0.5)}
    preview = engine.compute_preview_for_symbol("EURUSD", snaps, warmup_ready=True)
    assert preview.setup_type == SetupType.NO_TRADE
    assert FLAG_SPREAD_WIDE in preview.flags


def test_gate_h4_neutral_no_trade_flag():
    engine = SignalEngineV1(_params())
    snaps = {"M15": _snap("M15", 1, 1), "H1": _snap("H1", 1, 1), "H4": _snap("H4", 1, 1)}
    preview = engine.compute_preview_for_symbol("EURUSD", snaps, warmup_ready=True)
    assert preview.setup_type == SetupType.NO_TRADE
    assert FLAG_REGIME_H4_NEUTRAL in preview.flags


def test_confirm_c_long_rsi_and_close_above_sma():
    engine = SignalEngineV1(_params())
    snaps = {
        "H4": _snap("H4", 2.0, 1.0, rsi=60, close=1.2),
        "H1": _snap("H1", 2.0, 1.0, rsi=60, close=1.2),
        "M15": _snap("M15", 1.0, 0.8, rsi=60, close=1.1),
    }
    preview = engine.compute_preview_for_symbol("EURUSD", snaps, warmup_ready=True)
    assert preview.entry_triggered is True
    assert preview.direction == Direction.LONG


def test_confirm_c_short_rsi_and_close_below_sma():
    engine = SignalEngineV1(_params())
    snaps = {
        "H4": _snap("H4", 0.5, 1.0, rsi=40, close=0.8),
        "H1": _snap("H1", 0.5, 1.0, rsi=40, close=0.8),
        "M15": _snap("M15", 0.8, 1.0, rsi=40, close=0.7),
    }
    preview = engine.compute_preview_for_symbol("EURUSD", snaps, warmup_ready=True)
    assert preview.entry_triggered is True
    assert preview.direction == Direction.SHORT


def test_alignment_mismatch_sets_tf_mismatch_and_base_rr():
    engine = SignalEngineV1(_params())
    snaps = {
        "H4": _snap("H4", 2.0, 1.0, rsi=60, close=1.2),
        "H1": _snap("H1", 0.5, 1.0, rsi=40, close=0.8),
        "M15": _snap("M15", 1.0, 0.8, rsi=60, close=1.1),
    }
    preview = engine.compute_preview_for_symbol("EURUSD", snaps, warmup_ready=True)
    assert FLAG_TF_MISMATCH_H4_H1 in preview.flags
    assert preview.confidence != Confidence.HIGH
    assert preview.rr == 2.0


def test_high_confidence_sets_high_rr():
    engine = SignalEngineV1(_params())
    snaps = {
        "H4": _snap("H4", 2.0, 1.0, rsi=60, close=1.2),
        "H1": _snap("H1", 2.0, 1.0, rsi=60, close=1.2),
        "M15": _snap("M15", 1.0, 0.8, rsi=60, close=1.1),
    }
    preview = engine.compute_preview_for_symbol("EURUSD", snaps, warmup_ready=True)
    assert preview.confidence == Confidence.HIGH
    assert preview.rr == 3.0
