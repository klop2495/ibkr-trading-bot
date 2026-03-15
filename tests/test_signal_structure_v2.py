from app.signals.structure_v2 import (
    StructuralBar,
    build_continuation_setup,
    detect_swings,
)


def _bars(rows):
    return [
        StructuralBar(index=i, open=o, high=h, low=l, close=c)
        for i, (o, h, l, c) in enumerate(rows)
    ]


def test_detect_swings_finds_basic_pivots():
    bars = _bars(
        [
            (1.00, 1.01, 0.99, 1.00),
            (1.00, 1.03, 0.995, 1.02),
            (1.02, 1.06, 1.01, 1.05),
            (1.05, 1.03, 0.98, 0.99),
            (0.99, 1.01, 0.95, 0.97),
            (0.97, 1.02, 0.97, 1.01),
            (1.01, 1.05, 1.00, 1.04),
        ]
    )
    swings = detect_swings(bars, lookback=1, min_separation=1)
    assert [s.kind for s in swings] == ["high", "low"]
    assert swings[0].price == 1.06
    assert swings[1].price == 0.95


def test_build_long_continuation_setup_pullback_ready_without_trigger():
    bars = _bars(
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
    setup = build_continuation_setup(
        symbol="EURUSD",
        bars=bars,
        direction="long",
        lookback=1,
        min_separation=1,
        sl_buffer_pips=2.0,
        min_sl_pips=5.0,
        max_sl_pips=80.0,
        rr=2.0,
    )
    assert setup.setup_present is True
    assert setup.entry_triggered is False
    assert setup.reason == "pullback_ready"
    assert setup.retracement_ratio is not None
    assert 0 < setup.retracement_ratio < 0.75
    assert setup.stop_distance_pips is not None


def test_build_long_continuation_setup_entry_triggered_on_break_of_pullback_high():
    bars = _bars(
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
    setup = build_continuation_setup(
        symbol="EURUSD",
        bars=bars,
        direction="long",
        lookback=1,
        min_separation=1,
        sl_buffer_pips=2.0,
        min_sl_pips=5.0,
        max_sl_pips=80.0,
        rr=2.0,
    )
    assert setup.setup_present is True
    assert setup.entry_triggered is True
    assert setup.reason == "entry_triggered"
    assert setup.entry_price == 1.0135
    assert setup.take_profit_price is not None
    assert setup.take_profit_pips is not None


def test_build_long_continuation_setup_invalidated_on_break_of_anchor_low():
    bars = _bars(
        [
            (1.0000, 1.0020, 0.9990, 1.0010),
            (1.0010, 1.0060, 1.0000, 1.0050),
            (1.0050, 1.0040, 0.9970, 0.9980),
            (0.9980, 1.0100, 0.9990, 1.0090),
            (1.0090, 1.0080, 1.0010, 1.0020),
            (1.0020, 1.0150, 1.0030, 1.0140),
            (1.0140, 1.0110, 0.9960, 0.9980),
            (0.9980, 1.0010, 0.9950, 0.9970),
            (0.9970, 0.9990, 0.9940, 0.9950),
        ]
    )
    setup = build_continuation_setup(
        symbol="EURUSD",
        bars=bars,
        direction="long",
        lookback=1,
        min_separation=1,
        sl_buffer_pips=2.0,
        min_sl_pips=5.0,
        max_sl_pips=50.0,
        rr=2.0,
    )
    assert setup.setup_present is False
    assert setup.invalidated is True
    assert setup.reason == "setup_invalidated"


def test_build_short_continuation_setup_entry_triggered():
    bars = _bars(
        [
            (1.0200, 1.0210, 1.0180, 1.0190),
            (1.0190, 1.0240, 1.0185, 1.0230),
            (1.0230, 1.0160, 1.0140, 1.0150),
            (1.0150, 1.0220, 1.0160, 1.0210),
            (1.0210, 1.0130, 1.0110, 1.0120),
            (1.0120, 1.0170, 1.0120, 1.0160),
            (1.0160, 1.0155, 1.0130, 1.0140),
            (1.0140, 1.0145, 1.0115, 1.0125),
            (1.0125, 1.0130, 1.0095, 1.0100),
        ]
    )
    setup = build_continuation_setup(
        symbol="EURUSD",
        bars=bars,
        direction="short",
        lookback=1,
        min_separation=1,
        sl_buffer_pips=2.0,
        min_sl_pips=5.0,
        max_sl_pips=150.0,
        rr=2.0,
    )
    assert setup.setup_present is True
    assert setup.entry_triggered is True
    assert setup.direction == "short"
    assert setup.take_profit_price is not None


def test_build_setup_rejects_wide_structural_stop():
    bars = _bars(
        [
            (1.0000, 1.0020, 0.9990, 1.0010),
            (1.0010, 1.0060, 1.0000, 1.0050),
            (1.0050, 1.0040, 0.9970, 0.9980),
            (0.9980, 1.0100, 0.9990, 1.0090),
            (1.0090, 1.0080, 1.0010, 1.0020),
            (1.0020, 1.0300, 1.0030, 1.0290),
            (1.0290, 1.0200, 1.0080, 1.0100),
            (1.0100, 1.0190, 1.0090, 1.0180),
            (1.0180, 1.0220, 1.0175, 1.0210),
        ]
    )
    setup = build_continuation_setup(
        symbol="EURUSD",
        bars=bars,
        direction="long",
        lookback=1,
        min_separation=1,
        sl_buffer_pips=2.0,
        min_sl_pips=5.0,
        max_sl_pips=20.0,
        rr=2.0,
    )
    assert setup.setup_present is False
    assert setup.reason == "sl_out_of_range"
