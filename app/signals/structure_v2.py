from dataclasses import dataclass
from typing import Iterable, Literal, Optional


Direction = Literal["long", "short"]
SwingKind = Literal["high", "low"]


@dataclass(frozen=True)
class StructuralBar:
    index: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class SwingPoint:
    index: int
    kind: SwingKind
    price: float


@dataclass(frozen=True)
class StructuralSetup:
    direction: Direction
    setup_present: bool
    entry_triggered: bool
    invalidated: bool
    reason: str
    trigger_price: Optional[float] = None
    invalidation_price: Optional[float] = None
    entry_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    stop_distance_pips: Optional[float] = None
    take_profit_pips: Optional[float] = None
    retracement_ratio: Optional[float] = None
    impulse_start_index: Optional[int] = None
    impulse_end_index: Optional[int] = None
    anchor_index: Optional[int] = None
    impulse_score: Optional[float] = None
    pullback_score: Optional[float] = None
    trigger_score: Optional[float] = None


def pip_size(symbol: str) -> float:
    return 0.01 if (symbol or "").upper().endswith("JPY") else 0.0001


def normalize_bars(bars: Iterable[dict | StructuralBar]) -> list[StructuralBar]:
    normalized: list[StructuralBar] = []
    for i, bar in enumerate(bars):
        if isinstance(bar, StructuralBar):
            normalized.append(bar)
            continue
        normalized.append(
            StructuralBar(
                index=int(bar.get("index", i)),
                open=float(bar["open"]),
                high=float(bar["high"]),
                low=float(bar["low"]),
                close=float(bar["close"]),
            )
        )
    return normalized


def detect_swings(
    bars: Iterable[dict | StructuralBar],
    *,
    lookback: int,
    min_separation: int,
) -> list[SwingPoint]:
    seq = normalize_bars(bars)
    swings: list[SwingPoint] = []
    if lookback < 1 or len(seq) < (lookback * 2 + 1):
        return swings

    for i in range(lookback, len(seq) - lookback):
        bar = seq[i]
        left = seq[i - lookback:i]
        right = seq[i + 1:i + 1 + lookback]

        is_high = all(bar.high > other.high for other in left + right)
        is_low = all(bar.low < other.low for other in left + right)

        if is_high:
            candidate = SwingPoint(index=bar.index, kind="high", price=bar.high)
            _append_swing(swings, candidate, min_separation=min_separation)
        if is_low:
            candidate = SwingPoint(index=bar.index, kind="low", price=bar.low)
            _append_swing(swings, candidate, min_separation=min_separation)

    return swings


def _append_swing(swings: list[SwingPoint], candidate: SwingPoint, *, min_separation: int) -> None:
    if not swings:
        swings.append(candidate)
        return
    last = swings[-1]
    if candidate.kind != last.kind:
        swings.append(candidate)
        return
    if candidate.index - last.index >= min_separation:
        swings.append(candidate)
        return
    if candidate.kind == "high" and candidate.price > last.price:
        swings[-1] = candidate
    if candidate.kind == "low" and candidate.price < last.price:
        swings[-1] = candidate


def build_continuation_setup(
    *,
    symbol: str,
    bars: Iterable[dict | StructuralBar],
    direction: Direction,
    lookback: int,
    min_separation: int,
    sl_buffer_pips: float,
    min_sl_pips: float,
    max_sl_pips: float,
    rr: float,
    max_retracement_ratio: float = 0.75,
) -> StructuralSetup:
    seq = normalize_bars(bars)
    if len(seq) < max(lookback * 2 + 3, 7):
        return StructuralSetup(direction=direction, setup_present=False, entry_triggered=False, invalidated=False, reason="insufficient_bars")

    swings = detect_swings(seq, lookback=lookback, min_separation=min_separation)
    if len(swings) < 4:
        return StructuralSetup(direction=direction, setup_present=False, entry_triggered=False, invalidated=False, reason="insufficient_swings")

    structure = _find_trend_structure(swings, direction=direction)
    if structure is None:
        return StructuralSetup(direction=direction, setup_present=False, entry_triggered=False, invalidated=False, reason="trend_structure_not_found")

    anchor, impulse_end = structure
    anchor_bar_idx = _bar_position(seq, anchor.index)
    impulse_end_bar_idx = _bar_position(seq, impulse_end.index)
    if anchor_bar_idx is None or impulse_end_bar_idx is None or impulse_end_bar_idx >= len(seq) - 1:
        return StructuralSetup(direction=direction, setup_present=False, entry_triggered=False, invalidated=False, reason="post_impulse_missing")

    post_impulse = seq[impulse_end_bar_idx + 1:]
    latest = seq[-1]
    buffer = sl_buffer_pips * pip_size(symbol)
    avg_bar_range = _average_bar_range(seq[max(0, impulse_end_bar_idx - 8): impulse_end_bar_idx + 1])

    if direction == "long":
        pullback_extreme = min(bar.low for bar in post_impulse)
        invalidation_price = anchor.price
        retracement = impulse_end.price - pullback_extreme
        impulse_size = impulse_end.price - anchor.price
        invalidated = pullback_extreme <= invalidation_price
        trigger_price = max(bar.high for bar in post_impulse[:-1]) if len(post_impulse) > 1 else None
        entry_triggered = bool(trigger_price is not None and latest.close > trigger_price)
        stop_loss = pullback_extreme - buffer
        stop_distance_pips = (latest.close - stop_loss) / pip_size(symbol)
        take_profit_pips = stop_distance_pips * rr
        take_profit = latest.close + (take_profit_pips * pip_size(symbol))
    else:
        pullback_extreme = max(bar.high for bar in post_impulse)
        invalidation_price = anchor.price
        retracement = pullback_extreme - impulse_end.price
        impulse_size = anchor.price - impulse_end.price
        invalidated = pullback_extreme >= invalidation_price
        trigger_price = min(bar.low for bar in post_impulse[:-1]) if len(post_impulse) > 1 else None
        entry_triggered = bool(trigger_price is not None and latest.close < trigger_price)
        stop_loss = pullback_extreme + buffer
        stop_distance_pips = (stop_loss - latest.close) / pip_size(symbol)
        take_profit_pips = stop_distance_pips * rr
        take_profit = latest.close - (take_profit_pips * pip_size(symbol))

    if impulse_size <= 0:
        return StructuralSetup(direction=direction, setup_present=False, entry_triggered=False, invalidated=False, reason="invalid_impulse_geometry")

    retracement_ratio = retracement / impulse_size
    impulse_score = _score_impulse_quality(impulse_size=impulse_size, avg_bar_range=avg_bar_range)
    pullback_score = _score_pullback_quality(retracement_ratio=retracement_ratio)
    trigger_score = _score_trigger_quality(latest=latest, trigger_price=trigger_price, direction=direction, symbol=symbol)
    if invalidated:
        return StructuralSetup(
            direction=direction,
            setup_present=False,
            entry_triggered=False,
            invalidated=True,
            reason="setup_invalidated",
            invalidation_price=invalidation_price,
            retracement_ratio=retracement_ratio,
            impulse_start_index=anchor.index,
            impulse_end_index=impulse_end.index,
            anchor_index=anchor.index,
            impulse_score=impulse_score,
            pullback_score=pullback_score,
            trigger_score=0.0,
        )

    if retracement_ratio <= 0 or retracement_ratio > max_retracement_ratio:
        return StructuralSetup(
            direction=direction,
            setup_present=False,
            entry_triggered=False,
            invalidated=False,
            reason="pullback_out_of_range",
            invalidation_price=invalidation_price,
            retracement_ratio=retracement_ratio,
            impulse_start_index=anchor.index,
            impulse_end_index=impulse_end.index,
            anchor_index=anchor.index,
            impulse_score=impulse_score,
            pullback_score=pullback_score,
            trigger_score=0.0,
        )

    if stop_distance_pips < min_sl_pips or stop_distance_pips > max_sl_pips:
        return StructuralSetup(
            direction=direction,
            setup_present=False,
            entry_triggered=False,
            invalidated=False,
            reason="sl_out_of_range",
            trigger_price=trigger_price,
            invalidation_price=invalidation_price,
            stop_distance_pips=stop_distance_pips,
            retracement_ratio=retracement_ratio,
            impulse_start_index=anchor.index,
            impulse_end_index=impulse_end.index,
            anchor_index=anchor.index,
            impulse_score=impulse_score,
            pullback_score=pullback_score,
            trigger_score=trigger_score if entry_triggered else 0.0,
        )

    return StructuralSetup(
        direction=direction,
        setup_present=True,
        entry_triggered=entry_triggered,
        invalidated=False,
        reason="entry_triggered" if entry_triggered else "pullback_ready",
        trigger_price=trigger_price,
        invalidation_price=invalidation_price,
        entry_price=latest.close if entry_triggered else None,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit if entry_triggered else None,
        stop_distance_pips=stop_distance_pips,
        take_profit_pips=take_profit_pips if entry_triggered else None,
        retracement_ratio=retracement_ratio,
        impulse_start_index=anchor.index,
        impulse_end_index=impulse_end.index,
        anchor_index=anchor.index,
        impulse_score=impulse_score,
        pullback_score=pullback_score,
        trigger_score=trigger_score if entry_triggered else 0.0,
    )


def _find_trend_structure(swings: list[SwingPoint], *, direction: Direction) -> Optional[tuple[SwingPoint, SwingPoint]]:
    if direction == "long":
        for i in range(len(swings) - 1, 0, -1):
            end = swings[i]
            if end.kind != "high":
                continue
            start = next((s for s in reversed(swings[:i]) if s.kind == "low"), None)
            if start is None:
                continue
            prior_highs = [s.price for s in swings[: i - 1] if s.kind == "high"]
            if prior_highs and end.price <= max(prior_highs):
                continue
            return start, end
    else:
        for i in range(len(swings) - 1, 0, -1):
            end = swings[i]
            if end.kind != "low":
                continue
            start = next((s for s in reversed(swings[:i]) if s.kind == "high"), None)
            if start is None:
                continue
            prior_lows = [s.price for s in swings[: i - 1] if s.kind == "low"]
            if prior_lows and end.price >= min(prior_lows):
                continue
            return start, end
    return None


def _bar_position(bars: list[StructuralBar], swing_index: int) -> Optional[int]:
    for i, bar in enumerate(bars):
        if bar.index == swing_index:
            return i
    return None


def _average_bar_range(bars: list[StructuralBar]) -> float:
    ranges = [max(bar.high - bar.low, 0.0) for bar in bars]
    valid = [value for value in ranges if value > 0]
    if not valid:
        return 0.0
    return sum(valid) / len(valid)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _score_impulse_quality(*, impulse_size: float, avg_bar_range: float) -> float:
    if impulse_size <= 0 or avg_bar_range <= 0:
        return 0.0
    ratio = impulse_size / avg_bar_range
    normalized = _clamp((ratio - 1.0) / 4.0, 0.0, 1.0)
    return round(normalized * 25.0, 1)


def _score_pullback_quality(*, retracement_ratio: float) -> float:
    if retracement_ratio <= 0:
        return 0.0
    if 0.2 <= retracement_ratio <= 0.5:
        return 20.0
    if 0.1 <= retracement_ratio < 0.2:
        return round(10.0 + ((retracement_ratio - 0.1) / 0.1) * 10.0, 1)
    if 0.5 < retracement_ratio <= 0.65:
        return round(20.0 - ((retracement_ratio - 0.5) / 0.15) * 8.0, 1)
    if 0.65 < retracement_ratio <= 0.75:
        return round(12.0 - ((retracement_ratio - 0.65) / 0.10) * 8.0, 1)
    return 0.0


def _score_trigger_quality(
    *,
    latest: StructuralBar,
    trigger_price: Optional[float],
    direction: Direction,
    symbol: str,
) -> float:
    if trigger_price is None:
        return 0.0
    bar_range = max(latest.high - latest.low, pip_size(symbol))
    if direction == "long":
        breakout = latest.close - trigger_price
        close_location = (latest.close - latest.low) / bar_range
    else:
        breakout = trigger_price - latest.close
        close_location = (latest.high - latest.close) / bar_range
    if breakout <= 0:
        return 0.0
    breakout_ratio = _clamp(breakout / bar_range, 0.0, 1.0)
    location_ratio = _clamp(close_location, 0.0, 1.0)
    return round((breakout_ratio * 12.0) + (location_ratio * 8.0), 1)
