from app.models.signals_params import SignalsParams
from app.signals.engine import SignalEngine


class _FakeRiskRepo:
    def __init__(self):
        self.calls = 0
        self.last_event = None

    def insert(self, event_type, severity="info", message=None, **kwargs):
        self.calls += 1
        self.last_event = (event_type, severity, message)


def test_warn_rules_not_specified_logs_once_and_skips_duplicates():
    engine = SignalEngine()
    repo = _FakeRiskRepo()

    engine.warn_rules_not_specified(repo, SignalsParams())
    engine.warn_rules_not_specified(repo, SignalsParams())

    assert repo.calls == 1
    assert repo.last_event[0] == "SIGNALS_RULES_NOT_SPECIFIED"
    assert repo.last_event[1] == "warn"


def test_warn_rules_not_specified_resets_when_configured():
    engine = SignalEngine()
    repo = _FakeRiskRepo()
    engine.warn_rules_not_specified(repo, SignalsParams())
    assert repo.calls == 1
    # now configured -> should reset internal flag
    cfg = SignalsParams.model_validate(
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
            "spread": {"wide_spread_pips": 0.0},
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
    engine.warn_rules_not_specified(repo, cfg)
    engine.warn_rules_not_specified(repo, SignalsParams())
    # after becoming configured, a missing config later can log again
    assert repo.calls == 2


def test_warn_rules_not_specified_does_nothing_when_configured():
    engine = SignalEngine()
    repo = _FakeRiskRepo()
    cfg = SignalsParams.model_validate(
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
            "spread": {"wide_spread_pips": 0.0},
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
    engine.warn_rules_not_specified(repo, cfg)
    assert repo.calls == 0
