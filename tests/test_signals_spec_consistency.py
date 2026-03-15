from pathlib import Path

from app.models.signal_preview import (
    Confidence,
    DataQuality,
    Direction,
    SetupType,
    SpreadQuality,
)
from app.signals.engine_v1 import (
    FLAG_DATA_DUP,
    FLAG_DATA_GAP,
    FLAG_DATA_STALE,
    FLAG_DATA_WARMUP_NOT_READY,
    FLAG_ENTRY_NOT_TRIGGERED,
    FLAG_FALLBACK_SL_FROM_SETTINGS,
    FLAG_FALLBACK_TP_FROM_SETTINGS,
    FLAG_LEGACY_CONFIRM_FALLBACK,
    FLAG_REGIME_H4_NEUTRAL,
    FLAG_REGIME_UNKNOWN,
    FLAG_SETUP_INVALIDATED,
    FLAG_SETUP_NOT_FOUND,
    FLAG_SIGNALS_PARAMS_INVALID,
    FLAG_SIGNALS_RULES_NOT_SPECIFIED,
    FLAG_SPREAD_UNKNOWN,
    FLAG_SPREAD_WIDE,
    FLAG_STRUCTURAL_SL_TP,
    FLAG_SWING_NOT_DETECTED,
    FLAG_TF_MISMATCH_H4_H1,
)


def test_flags_and_enums_match_spec():
    doc = Path("docs/signals_rules_v1.md").read_text()

    code_flags = {
        FLAG_DATA_WARMUP_NOT_READY,
        FLAG_DATA_GAP,
        FLAG_DATA_DUP,
        FLAG_DATA_STALE,
        FLAG_SPREAD_WIDE,
        FLAG_SPREAD_UNKNOWN,
        FLAG_REGIME_H4_NEUTRAL,
        FLAG_REGIME_UNKNOWN,
        FLAG_TF_MISMATCH_H4_H1,
        FLAG_SETUP_NOT_FOUND,
        FLAG_SETUP_INVALIDATED,
        FLAG_ENTRY_NOT_TRIGGERED,
        FLAG_SWING_NOT_DETECTED,
        FLAG_STRUCTURAL_SL_TP,
        FLAG_LEGACY_CONFIRM_FALLBACK,
        FLAG_FALLBACK_SL_FROM_SETTINGS,
        FLAG_FALLBACK_TP_FROM_SETTINGS,
        FLAG_SIGNALS_RULES_NOT_SPECIFIED,
        FLAG_SIGNALS_PARAMS_INVALID,
    }

    expected_flags = code_flags  # doc is canonical; enforce equality via containment checks
    for flag in expected_flags:
        assert flag in doc, f"Flag {flag} missing in spec doc"

    # Ensure enums are documented
    for val in [e.value for e in SetupType]:
        assert val in doc, f"SetupType {val} missing in spec doc"
    for val in [e.value for e in Direction]:
        assert val in doc, f"Direction {val} missing in spec doc"
    for val in [e.value for e in Confidence]:
        assert val in doc, f"Confidence {val} missing in spec doc"
    for val in [e.value for e in DataQuality]:
        assert val in doc, f"DataQuality {val} missing in spec doc"
    for val in [e.value for e in SpreadQuality]:
        assert val in doc, f"SpreadQuality {val} missing in spec doc"


def test_no_signals_persistence_in_main():
    main_text = Path("app/main.py").read_text()
    assert "SignalsRepo" not in main_text
