from datetime import datetime, timezone

from pydantic import ValidationError

from app.models.snapshot import MarketSnapshot
from app.signals.engine import SignalEngine
from app.signals.models import FeatureBins, SignalPreview, SignalSummary


def test_signal_model_forbids_extra_and_normalizes_symbol():
    s = SignalPreview(
        ts_utc=datetime.now(timezone.utc),
        symbol=" eurusd ",
        feature_bins=FeatureBins(trend="unknown", volatility="unknown", momentum="unknown"),
        signal_summary=SignalSummary(direction="flat", setup_present=False, entry_triggered=False),
        data_quality="unknown",
        spread_quality="unknown",
    )
    assert s.symbol == "EURUSD"
    try:
        SignalPreview(
            ts_utc=datetime.now(timezone.utc),
            symbol="EURUSD",
            feature_bins=FeatureBins(trend="unknown", volatility="unknown", momentum="unknown"),
            signal_summary=SignalSummary(direction="flat", setup_present=False, entry_triggered=False),
            data_quality="unknown",
            spread_quality="unknown",
            extra_field=True,  # type: ignore
        )
    except ValidationError:
        pass
    else:
        assert False, "expected ValidationError"


def test_engine_returns_safe_defaults_with_flags():
    engine = SignalEngine()
    snap = MarketSnapshot(
        timestamp=datetime.now(timezone.utc),
        symbol="EURUSD",
        timeframe="M15",
        close=1.0,
        atr=1.0,
        rsi=1.0,
        ma_fast=1.0,
        ma_slow=1.0,
        spread=0.1,
    )
    signals = engine.compute_signals([snap])
    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_summary.direction == "flat"
    assert sig.feature_bins.trend == "unknown"
    assert "binning_not_specified" in sig.flags
    assert "signals_rules_not_specified" in sig.flags
