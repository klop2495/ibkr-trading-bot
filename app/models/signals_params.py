from typing import Dict, Literal

from pydantic import BaseModel, ConfigDict, Field


class SignalsTimeframes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    h4: Literal["H4"] = "H4"
    h1: Literal["H1"] = "H1"
    m15: Literal["M15"] = "M15"


class SignalsGates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_warmup: bool = True
    require_data_ok: bool = True
    require_spread_ok: bool = True
    disallow_h4_neutral: bool = True


class SignalsSpread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wide_spread_pips: float | None = Field(default=None, ge=0)


class SignalsStructure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    swing_lookback_bars: int | None = Field(default=None, ge=1)
    swing_min_separation_bars: int | None = Field(default=None, ge=1)
    sl_buffer_mode: Literal["PIPS", "NONE"] | None = None
    sl_buffer_pips: float | None = Field(default=None, ge=0)
    min_sl_pips: float | None = Field(default=None, ge=0)
    max_sl_pips: float | None = Field(default=None, ge=0)


class SignalsConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high_conf_rule: Literal["H4_H1_ALIGN_AND_M15_MOMENTUM_CONFIRM"] | None = None


class SignalsRR(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["contextual"] = "contextual"
    base_rr: float | None = Field(default=None, gt=0)
    high_conf_rr: float | None = Field(default=None, gt=0)


class SignalsFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_hours_utc: list[str] | None = None


class SignalsM15Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rsi_period: int | None = Field(default=None, ge=1)
    rsi_long_min: float | None = None
    rsi_short_max: float | None = None
    rsi_overbought: float | None = Field(default=70.0, ge=50.0, le=100.0)
    rsi_oversold: float | None = Field(default=30.0, ge=0.0, le=50.0)
    sma_period: int | None = Field(default=None, ge=1)
    pullback_atr_mult: float | None = Field(default=0.35, ge=0.0)
    pullback_max_pips: float | None = Field(default=15.0, ge=0.0)


class SignalsScoring(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_regime_score: float = Field(default=12.0, ge=0.0, le=25.0)
    min_setup_score: float = Field(default=40.0, ge=0.0, le=70.0)
    min_entry_score: float = Field(default=50.0, ge=0.0, le=100.0)
    high_confidence_score: float = Field(default=70.0, ge=0.0, le=100.0)
    regime_separation_atr_cap: float = Field(default=1.5, gt=0.0)
    trigger_break_buffer_pips: float = Field(default=0.5, ge=0.0, le=10.0)


class SignalsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    enabled: bool = False
    rules_version: int = 0
    timeframes: SignalsTimeframes = Field(default_factory=SignalsTimeframes)
    gates: SignalsGates = Field(default_factory=SignalsGates)
    spread: SignalsSpread = Field(default_factory=SignalsSpread)
    structure: SignalsStructure = Field(default_factory=SignalsStructure)
    confidence: SignalsConfidence = Field(default_factory=SignalsConfidence)
    rr: SignalsRR = Field(default_factory=SignalsRR)
    filters: SignalsFilters = Field(default_factory=SignalsFilters)
    m15_confirm: SignalsM15Confirm = Field(default_factory=SignalsM15Confirm)
    scoring: SignalsScoring = Field(default_factory=SignalsScoring)
    symbol_overrides: Dict[str, Dict] = Field(default_factory=dict)

    def is_configured(self) -> bool:
        if self.schema_version != 1 or not self.enabled or self.rules_version != 1:
            return False
        required = [
            self.timeframes.h4,
            self.timeframes.h1,
            self.timeframes.m15,
            self.spread.wide_spread_pips,
            self.structure.swing_lookback_bars,
            self.structure.swing_min_separation_bars,
            self.confidence.high_conf_rule,
            self.rr.base_rr,
            self.rr.high_conf_rr,
            self.m15_confirm.rsi_period,
            self.m15_confirm.rsi_long_min,
            self.m15_confirm.rsi_short_max,
            self.m15_confirm.sma_period,
        ]
        return all(v is not None for v in required)
