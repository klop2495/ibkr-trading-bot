from datetime import datetime, timezone

from app.models.signals_params import SignalsParams
from app.models.snapshot import MarketSnapshot
from app.signals.engine_v1 import (
    FLAG_DATA_WARMUP_NOT_READY,
    FLAG_LEGACY_CONFIRM_FALLBACK,
    FLAG_SPREAD_WIDE,
    FLAG_REGIME_H4_NEUTRAL,
    FLAG_STRUCTURAL_SL_TP,
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


def _structural_params():
    params = _params()
    params.structure.swing_lookback_bars = 1
    params.structure.swing_min_separation_bars = 1
    params.structure.max_sl_pips = 80
    return params


def _snap(tf: str, ma_fast: float, ma_slow: float, rsi: float = 50, close: float = 1.0, spread: float = 0.1):
    ts = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    return MarketSnapshot(
        schema_version=1,
        timestamp=ts,
        symbol="EURUSD",
        timeframe=tf,
        close=close,
        atr=1.0,
        rsi=rsi,
        ma_fast=ma_fast,
        ma_slow=ma_slow,
        spread=spread,
    )


def _ohlc(rows):
    opens, highs, lows, closes = [], [], [], []
    for o, h, l, c in rows:
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes}


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


def test_structural_path_sets_pullback_ready_without_trigger():
    engine = SignalEngineV1(_structural_params())
    snaps = {
        "H4": _snap("H4", 2.0, 1.0, rsi=60, close=1.2),
        "H1": _snap("H1", 2.0, 1.0, rsi=60, close=1.2),
        "M15": _snap("M15", 1.0, 0.8, rsi=55, close=1.0105),
    }
    preview = engine.compute_preview_for_symbol(
        "EURUSD",
        snaps,
        warmup_ready=True,
        ohlc_by_timeframe={
            "M15": _ohlc(
                [
                    (1.0000, 1.0020, 0.9990, 1.0010),
                    (1.0010, 1.0060, 1.0000, 1.0050),
                    (1.0050, 1.0040, 0.9970, 0.9980),
                    (0.9980, 1.0100, 0.9990, 1.0090),
                    (1.0090, 1.0080, 1.0010, 1.0020),
                    (1.0020, 1.0150, 1.0030, 1.0140),
                    (1.0140, 1.0110, 1.0080, 1.0090),
                    (1.0090, 1.0120, 1.0070, 1.0100),
                    (1.0100, 1.0115, 1.0085, 1.0105),
                ]
            )
        },
    )
    assert preview.setup_present is True
    assert preview.entry_triggered is False
    assert preview.setup_type == SetupType.SWING_CONTINUATION
    assert preview.direction == Direction.LONG
    assert preview.sl_distance_pips is not None
    assert FLAG_STRUCTURAL_SL_TP in preview.flags


def test_structural_path_sets_entry_triggered_and_structural_distances():
    engine = SignalEngineV1(_structural_params())
    snaps = {
        "H4": _snap("H4", 2.0, 1.0, rsi=60, close=1.2),
        "H1": _snap("H1", 2.0, 1.0, rsi=60, close=1.2),
        "M15": _snap("M15", 1.0, 0.8, rsi=60, close=1.0135),
    }
    preview = engine.compute_preview_for_symbol(
        "EURUSD",
        snaps,
        warmup_ready=True,
        ohlc_by_timeframe={
            "M15": _ohlc(
                [
                    (1.0000, 1.0020, 0.9990, 1.0010),
                    (1.0010, 1.0060, 1.0000, 1.0050),
                    (1.0050, 1.0040, 0.9970, 0.9980),
                    (0.9980, 1.0100, 0.9990, 1.0090),
                    (1.0090, 1.0080, 1.0010, 1.0020),
                    (1.0020, 1.0150, 1.0030, 1.0140),
                    (1.0140, 1.0110, 1.0080, 1.0090),
                    (1.0090, 1.0120, 1.0070, 1.0100),
                    (1.0100, 1.0140, 1.0090, 1.0135),
                ]
            )
        },
    )
    assert preview.setup_present is True
    assert preview.entry_triggered is True
    assert preview.setup_type == SetupType.SWING_CONTINUATION
    assert preview.direction == Direction.LONG
    assert preview.sl_distance_pips is not None
    assert preview.tp_distance_pips is not None
    assert FLAG_STRUCTURAL_SL_TP in preview.flags


def test_legacy_path_sets_fallback_flag_and_no_distances():
    engine = SignalEngineV1(_params())
    snaps = {
        "H4": _snap("H4", 2.0, 1.0, rsi=60, close=1.2),
        "H1": _snap("H1", 2.0, 1.0, rsi=60, close=1.2),
        "M15": _snap("M15", 1.0, 0.8, rsi=60, close=1.1),
    }
    preview = engine.compute_preview_for_symbol("EURUSD", snaps, warmup_ready=True)
    assert FLAG_LEGACY_CONFIRM_FALLBACK in preview.flags
    assert preview.sl_distance_pips is None
    assert preview.tp_distance_pips is None
