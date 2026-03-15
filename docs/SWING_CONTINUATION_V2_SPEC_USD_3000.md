# Swing Continuation V2 Spec

Date: 2026-03-15
Execution currency model: `USD only`
Sizing model: equity-adaptive
Strategy class: deterministic rules, no LLM dependency for entry logic

## Goal

Replace the current surrogate signal logic:

- H4 MA alignment
- H1 MA alignment
- M15 RSI + near-MA confirm

with a real structural continuation model:

- market regime
- impulse leg
- pullback
- continuation trigger
- structural invalidation
- structural SL/TP

## Strategy Intent

Trade only continuation setups in the direction of the dominant higher-timeframe trend.

The setup must answer five questions explicitly:

1. Is there a valid trend regime?
2. Was there a real impulse leg?
3. Is the market currently in a pullback, not a reversal?
4. Has continuation re-asserted itself on M15?
5. Where is the structural invalidation point?

If any answer is unclear, the signal must resolve to `NO_TRADE`.

## Timeframe Roles

- `H4`
  - regime and directional bias
  - trend filter only
- `H1`
  - continuation context
  - pullback state validation
- `M15`
  - structural setup detection
  - trigger
  - stop placement reference

## Definitions

## Swing Point

A swing point is a local extremum confirmed by symmetric lookback.

Recommended default:

- `swing_lookback_bars = 3`

Rules:

- swing high:
  - bar high is greater than highs of `N` bars on the left
  - bar high is greater than highs of `N` bars on the right
- swing low:
  - bar low is lower than lows of `N` bars on the left
  - bar low is lower than lows of `N` bars on the right

Confirmation delay:

- a swing is only confirmed after the right-side bars exist

## Structural Leg

An impulse leg is the move from one confirmed opposing swing to the next confirmed directional swing.

Long example:

- confirmed swing low
- followed by confirmed swing high
- leg must exceed minimum structural distance

Short example:

- confirmed swing high
- followed by confirmed swing low

## Pullback

A pullback is a retracement against the impulse direction that:

- does not break structural invalidation
- remains within allowed retracement depth
- does not flip higher timeframe regime

## Continuation Trigger

A continuation trigger is a structural reclaim in the trend direction after pullback.

Long example:

- pullback creates local lower highs / local swing low
- price reclaims local M15 swing high or closes above pullback structure pivot

Short example:

- pullback creates local higher lows / local swing high
- price breaks below local M15 swing low or closes below pullback structure pivot

## Regime Filter

H4 defines allowed direction.

Recommended regime logic:

- long bias when:
  - `ma_fast > ma_slow`
  - slope of `ma_slow` is non-negative
- short bias when:
  - `ma_fast < ma_slow`
  - slope of `ma_slow` is non-positive
- otherwise:
  - no regime

Optional future extension:

- ADX or market structure trend test

For V2 initial implementation:

- keep MA-based H4 regime
- require non-flat bias

## H1 Continuation Context

H1 must confirm that current price action is a retracement inside the H4 trend, not a regime break.

Long context:

- H4 bias = long
- H1 fast/slow alignment must not be strongly opposite
- H1 price is in pullback or re-acceleration, but not in confirmed bearish structure break

Short context:

- mirrored logic

Fail-safe rule:

- if H1 clearly opposes H4, block the setup

## M15 Structural Setup Detection

The setup exists only if all of the following are true:

1. There is a confirmed directional impulse leg on M15
2. There is a pullback against that leg
3. Pullback depth is acceptable
4. Structural invalidation has not been breached

## Long Setup

Required sequence:

1. H4 regime = long
2. On M15, detect latest confirmed impulse:
   - last confirmed swing low `L0`
   - subsequent confirmed swing high `H1`
   - `H1 - L0` must exceed minimum impulse size
3. Detect pullback after `H1`
4. Pullback low `L1` must satisfy:
   - `L1 > L0`
   - retracement depth between configured min/max
5. Setup becomes `setup_present = true`

## Short Setup

Required sequence:

1. H4 regime = short
2. On M15, detect latest confirmed impulse:
   - last confirmed swing high `H0`
   - subsequent confirmed swing low `L1`
   - `H0 - L1` must exceed minimum impulse size
3. Detect pullback after `L1`
4. Pullback high `H1` must satisfy:
   - `H1 < H0`
   - retracement depth between configured min/max
5. Setup becomes `setup_present = true`

## Recommended Structural Parameters

Defaults for initial V2:

- `swing_lookback_bars = 3`
- `swing_min_separation_bars = 4`
- `min_impulse_pips = 12`
- `min_pullback_pips = 5`
- `max_pullback_ratio = 0.65`
- `min_pullback_ratio = 0.20`
- `sl_buffer_pips = 2`
- `min_sl_pips = 8`
- `max_sl_pips = 30`

Rationale:

- stop must be structural but not excessively wide
- continuation setup must survive normal FX noise but not drift into deep reversal territory
- small accounts benefit from compact setups, but the engine must remain equity-adaptive

## Trigger Logic

Setup presence does not imply entry.

Entry requires structural continuation trigger on M15.

## Long Trigger

After valid pullback:

- define trigger pivot as the latest local swing high formed inside the pullback
- `entry_triggered = true` when:
  - current candle closes above that pivot
  - or high breaks pivot and close remains in top half of candle range

Secondary confirms:

- `RSI >= 52`
- close above M15 fast MA

Secondary confirms may boost confidence, but must not define structure.

## Short Trigger

After valid pullback:

- define trigger pivot as latest local swing low formed inside the pullback
- `entry_triggered = true` when:
  - candle closes below that pivot
  - or low breaks pivot and close remains in lower half of candle range

Secondary confirms:

- `RSI <= 48`
- close below M15 fast MA

## Invalidation Logic

Invalidation must exist before entry and after entry.

## Pre-entry Invalidation

Long setup invalidates if:

- pullback low breaks below impulse origin swing low `L0`
- or pullback depth exceeds `max_pullback_ratio`
- or H4 regime is lost

Short setup invalidates if:

- pullback high breaks above impulse origin swing high `H0`
- or pullback depth exceeds `max_pullback_ratio`
- or H4 regime is lost

When invalidated before trigger:

- `setup_present = false`
- `entry_triggered = false`
- flags include `SETUP_INVALIDATED`

## Post-entry Invalidation

This becomes the live stop loss.

Long:

- stop below pullback swing low minus buffer

Short:

- stop above pullback swing high plus buffer

## Structural SL

SL must be derived from structure, then bounded.

Formula:

- long:
  - `sl_price = pullback_low - sl_buffer`
- short:
  - `sl_price = pullback_high + sl_buffer`

Convert to pips:

- `sl_distance_pips = abs(entry_price - sl_price) / pip_size`

Bounds:

- if `< min_sl_pips`, either:
  - extend to `min_sl_pips`
  - or reject as too tight
- if `> max_sl_pips`, reject setup as too wide

Recommended behavior:

- reject too-wide setups
- allow too-tight setups by clamping to `min_sl_pips`

## Structural TP

TP should be derived from RR, not fixed pips.

Formula:

- `tp_distance_pips = sl_distance_pips * rr`

Where:

- `rr = 2.0` for normal confidence
- `rr = 2.5` for high confidence continuation

For the 3000 USD model, recommended initial cap:

- `max_tp_pips = 75`

If derived TP is above cap:

- keep trade but cap TP to `max_tp_pips`

## Confidence Model

Use confidence only to adjust RR and maybe risk modifier, not to override structure.

## High Confidence

Requires all:

- H4 and H1 aligned
- pullback depth moderate, not deep
- trigger candle closes decisively in continuation direction
- spread quality ok
- data quality ok

## Normal Confidence

Valid structure present, but one or more strength conditions missing.

## Low Confidence

Should normally not be tradable for continuation strategy.

Recommendation:

- if confidence is low, force `trade_allowed = false`

## Preview Contract V2

`SignalPreviewV1` is insufficient for structural continuation.

Need either a V2 model or extended fields.

Required additional fields:

- `regime_direction`
- `impulse_start_price`
- `impulse_end_price`
- `pullback_start_price`
- `pullback_end_price`
- `trigger_price`
- `invalidation_price`
- `retracement_ratio`
- `impulse_pips`
- `pullback_pips`
- `structural_state`

Minimal structural state values:

- `NO_SETUP`
- `IMPULSE_FOUND`
- `PULLBACK_ACTIVE`
- `TRIGGER_READY`
- `TRIGGERED`
- `INVALIDATED`

## Decision Policy Under V2

Decision can only become tradable if:

- setup type is not `NO_TRADE`
- direction is not `FLAT`
- setup present is true
- entry triggered is true
- invalidation not breached
- `sl_distance_pips` exists and is valid

This preserves fail-safe behavior.

## Equity-Adaptive Risk Model

Position size formula:

- `units = risk_amount / (sl_pips * pip_value_per_unit_usd)`

Where:

- `risk_amount = current_equity * risk_per_trade_pct / 100`

Practical consequences:

- 10 pip SL allows larger positions
- 25-30 pip SL sharply reduces size
- wide-stop setups may become non-viable on smaller accounts

## Strategy Constraints for Smaller Accounts

The strategy itself must not assume a fixed account size.

However, for smaller accounts the practical preference is:

- reject setups with `sl_distance_pips > 30`
- prefer setups with `sl_distance_pips` in `10..22`
- reject low-liquidity / wide spread conditions
- trade only during core liquid hours

If current equity is too low for the derived setup:

- the trade must be skipped
- not forced

Recommended trade hours:

- `07:00-11:00 UTC`
- `13:00-17:00 UTC`

Avoid:

- late Asia
- rollover
- thin Friday close conditions

## Broker Validation Required

Before live rollout, verify specifically for FX CFD:

- minimum accepted quantity
- quantity step size
- real margin requirement by symbol
- whether small quantities are accepted on the account type
- whether broker-side leverage and buying power permit the projected order

Do not assume spot FX/IDEALPRO rules apply to CFD contracts.

## Recommended First Implementation Scope

Phase 1:

- keep H4 regime via MA alignment
- implement true M15 swing detection
- implement impulse/pullback/trigger
- implement invalidation
- derive structural SL/TP

Phase 2:

- add H1 continuation context refinement
- add pullback zone quality scoring
- add trend strength filters

Phase 3:

- connect risk modifier to structural quality
- retire fixed default SL/TP for continuation trades

## Expected Benefits

- setup names match actual behavior
- every trade has a structural reason
- stop loss is market-derived
- invalidation is explicit
- sizing becomes consistent with setup geometry
- easier post-trade analysis

## Non-goals for V2 Initial Release

- reversal setups
- ML/LLM-based entry logic
- multi-entry pyramiding
- partial exits
- adaptive trailing logic tied to structure

## Implementation Notes

Implementation should start by adding pure functions for:

- swing detection
- impulse detection
- pullback validation
- trigger detection
- structural SL/TP derivation

Then wrap them into the signal engine.

This must be test-first:

- deterministic candle fixtures
- explicit expected swing points
- explicit expected invalidation
- explicit expected SL/TP outputs
