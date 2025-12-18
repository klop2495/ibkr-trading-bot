# Signals Rules v1 Spec

## Overview & principles
- Deterministic, rule-based signals; no LLM involvement.
- No prices/levels in outputs; only categorical states and optional distances (pips) without computing position sizing.
- Variant A gating: if `signals_params.is_configured()` is false, do **not** write to `signals`; emit `SIGNALS_RULES_NOT_SPECIFIED`; produce NO_TRADE/flat preview.
- Managed configuration lives in `bot_settings.signals_params`; UI edits it, runtime validates it.

## Setup catalog v1
- `SWING_CONTINUATION`
- `SWING_REVERSAL`
- `NO_TRADE`

## Timeframe roles
- H4: context/regime/bias. If H4 is neutral/unknown → NO_TRADE.
- H1: confirmation; alignment with H4 enables high confidence (with M15 confirm).
- M15: trigger; structure + momentum confirmation.

## Global gates
- Warm-up ready (`warmup_bars_min` satisfied).
- `data_quality` ok (no gap/dup/stale).
- `spread_quality` ok (not wide).
- H4 not neutral/unknown.
- Optional `trade_hours_utc` filter; outside window → NO_TRADE.

## Entry/exit semantics
- `setup_present`: setup is structurally found and ready.
- `entry_triggered`: trigger conditions satisfied on M15.
- `invalidation`: if invalidation condition hits before trigger, setup is void (flags apply).

### SWING_CONTINUATION (trend continuation)
- `setup_present`: last confirmed swing extremum exists (M15) and pullback zone defined.
- `entry_triggered`: price returned to pullback zone and M15 confirmation occurs in trend direction.
- `invalidation`: price breaks swing extremum against direction before trigger → setup invalidated.

### SWING_REVERSAL (reversal from extremum)
- `setup_present`: swing extremum exists and M15 indicates structure/momentum shift against prior move.
- `entry_triggered`: true on confirmation.
- Confidence lower if H4/H1 not aligned; alignment not required.

## Confidence & RR rules
- RR mode: contextual.
- `base_rr = 2.0`
- `high_conf_rr = 3.0`
- `high_confidence = (H4 trend aligns with H1 trend) AND (M15 momentum confirms)`

## Flags v1 (stable keys)
- Data/Quality: `DATA_WARMUP_NOT_READY`, `DATA_GAP`, `DATA_DUP`, `DATA_STALE`
- Spread/Market: `SPREAD_WIDE`, `SPREAD_UNKNOWN`
- Regime/TF: `REGIME_H4_NEUTRAL`, `REGIME_UNKNOWN`, `TF_MISMATCH_H4_H1`
- Structure/Setup: `SETUP_NOT_FOUND`, `SETUP_INVALIDATED`, `ENTRY_NOT_TRIGGERED`, `SWING_NOT_DETECTED`
- Config: `SIGNALS_RULES_NOT_SPECIFIED`, `SIGNALS_PARAMS_INVALID`

## SignalPreview v1 schema
- Required:
  - `ts_utc` (ISO string)
  - `symbol` (string)
  - `timeframe_trigger = "M15"` (v1 fixed)
  - `setup_type` (`SWING_CONTINUATION` | `SWING_REVERSAL` | `NO_TRADE`)
  - `direction` = `long` | `short` | `flat`
  - `setup_present` (bool)
  - `entry_triggered` (bool)
  - `confidence` = `low` | `normal` | `high`
  - `rr` (float; v1 uses 2.0 or 3.0)
  - `data_quality` = `ok` | `stale` | `gap` | `dup` | `unknown`
  - `spread_quality` = `ok` | `wide` | `unknown`
  - `flags` (string[])
- Optional (stage-5 ready, still no prices):
  - `sl_distance_pips` (float)
  - `tp_distance_pips` (float)

## signals_params v1 schema
- Managed keys:
  - `schema_version` (1)
  - `enabled` (bool)
  - `rules_version` (1)
  - `timeframes` { `h4`, `h1`, `m15` }
  - `gates` { `require_warmup`, `require_data_ok`, `require_spread_ok`, `disallow_h4_neutral` }
  - `spread` { `wide_spread_pips` }
  - `structure` { `swing_lookback_bars`, `swing_min_separation_bars`, `sl_buffer_mode`, `sl_buffer_pips`, `min_sl_pips`, `max_sl_pips` }
  - `confidence` { `high_conf_rule`: `"H4_H1_ALIGN_AND_M15_MOMENTUM_CONFIRM"` }
  - `rr` { `mode`: `"contextual"`, `base_rr`, `high_conf_rr` }
  - `filters` { `trade_hours_utc`: `["06:00-20:00"]` }
  - `symbol_overrides` { `"EURUSD": { ... }` }
- Notes:
  - `wide_spread_pips` depends on per-symbol pip size; pip size must be deterministic (JPY pairs differ).
  - `trade_hours_utc` format: `"HH:MM-HH:MM"`. v1 allows same-day ranges; cross-midnight ranges are disallowed unless explicitly added later.
- Example:
```json
{
  "schema_version": 1,
  "enabled": true,
  "rules_version": 1,
  "timeframes": { "h4": "H4", "h1": "H1", "m15": "M15" },
  "gates": {
    "require_warmup": true,
    "require_data_ok": true,
    "require_spread_ok": true,
    "disallow_h4_neutral": true
  },
  "spread": { "wide_spread_pips": 0.8 },
  "structure": {
    "swing_lookback_bars": 20,
    "swing_min_separation_bars": 5,
    "sl_buffer_mode": "PIPS",
    "sl_buffer_pips": 5,
    "min_sl_pips": 5,
    "max_sl_pips": 50
  },
  "confidence": {
    "high_conf_rule": "H4_H1_ALIGN_AND_M15_MOMENTUM_CONFIRM"
  },
  "rr": { "mode": "contextual", "base_rr": 2.0, "high_conf_rr": 3.0 },
  "filters": { "trade_hours_utc": ["06:00-20:00"] },
  "symbol_overrides": {
    "EURUSD": {
      "spread": { "wide_spread_pips": 0.6 },
      "rr": { "base_rr": 2.0, "high_conf_rr": 3.0 }
    }
  }
}
```

## Non-goals / future work
- SL/TP computation, position sizing, and persistence to `signals` (beyond Preview) are future stages.
- Numeric trading math, broker execution, and agents/risk wiring are out of scope here.
