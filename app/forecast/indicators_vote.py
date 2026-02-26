"""
Indicator voting functions for price direction forecast.

Each voter returns +1 (UP), -1 (DOWN), or 0 (NEUTRAL).
Voters are designed to be stateless and work with raw price/indicator lists.

Indicator weights are based on empirical analysis:
- momentum: STRONG predictor (85% when aligned), weight=2
- price_vs_ma: GOOD predictor (60%), weight=1
- ma_cross: CONTRARIAN (-28% delta), INVERTED, weight=1
- rsi_extreme: CONTRARIAN (-88% delta), INVERTED, weight=1
- rsi_trend: too often neutral, weight=1
- atr_trend: requires OHLC data not stored in snapshots, weight=1
"""

from typing import List, Optional, Tuple

from app.market_data.indicators import rsi as calc_rsi, sma as calc_sma


def vote_ma_cross(closes: List[float], fast_period: int = 50, slow_period: int = 200) -> int:
    """
    Moving Average crossover direction.
    UP if fast MA > slow MA, DOWN if fast MA < slow MA.
    """
    if len(closes) < slow_period:
        return 0
    fast = calc_sma(closes, fast_period)
    slow = calc_sma(closes, slow_period)
    if fast is None or slow is None:
        return 0
    if fast > slow:
        return 1
    if fast < slow:
        return -1
    return 0


def vote_ma_cross_inverted(closes: List[float], fast_period: int = 50, slow_period: int = 200) -> int:
    """
    INVERTED MA Cross — empirical analysis shows ma_cross is contrarian.
    When fast > slow, actual short-term move tends DOWN (mean reversion).
    """
    raw = vote_ma_cross(closes, fast_period, slow_period)
    return -raw  # invert


def vote_rsi_trend(closes: List[float], period: int = 14, lookback: int = 3) -> int:
    """
    RSI trend: UP if RSI > 50 and rising, DOWN if RSI < 50 and falling.
    Uses lookback to determine direction of RSI movement.
    """
    if len(closes) < period + 1 + lookback:
        return 0
    rsi_now = calc_rsi(closes, period)
    rsi_prev = calc_rsi(closes[:-lookback], period)
    if rsi_now is None or rsi_prev is None:
        return 0
    if rsi_now > 50 and rsi_now > rsi_prev:
        return 1
    if rsi_now < 50 and rsi_now < rsi_prev:
        return -1
    return 0


def vote_rsi_extreme(closes: List[float], period: int = 14, oversold: float = 30.0, overbought: float = 70.0) -> int:
    """
    RSI extreme / mean-reversion signal.
    UP if oversold (potential bounce), DOWN if overbought (potential drop).
    """
    if len(closes) < period + 1:
        return 0
    rsi_val = calc_rsi(closes, period)
    if rsi_val is None:
        return 0
    if rsi_val <= oversold:
        return 1  # oversold → expect bounce up
    if rsi_val >= overbought:
        return -1  # overbought → expect pullback down
    return 0


def vote_rsi_momentum(closes: List[float], period: int = 14, oversold: float = 30.0, overbought: float = 70.0) -> int:
    """
    INVERTED RSI extreme — empirical analysis shows RSI extreme is momentum, not mean-reversion.
    Overbought (RSI>70) → price continues UP (momentum).
    Oversold (RSI<30) → price continues DOWN (momentum).
    """
    raw = vote_rsi_extreme(closes, period, oversold, overbought)
    return -raw  # invert: overbought=UP(momentum), oversold=DOWN(momentum)


def vote_price_vs_ma(closes: List[float], ma_period: int = 50) -> int:
    """
    Price position relative to MA.
    UP if close > MA, DOWN if close < MA.
    """
    if len(closes) < ma_period:
        return 0
    ma = calc_sma(closes, ma_period)
    if ma is None or ma == 0:
        return 0
    price = closes[-1]
    if price > ma:
        return 1
    if price < ma:
        return -1
    return 0


def vote_momentum(closes: List[float], lookback: int = 10) -> int:
    """
    Rate of Change (ROC) momentum.
    UP if current close > close N bars ago, DOWN otherwise.
    """
    if len(closes) < lookback + 1:
        return 0
    current = closes[-1]
    past = closes[-(lookback + 1)]
    if past == 0:
        return 0
    if current > past:
        return 1
    if current < past:
        return -1
    return 0


def vote_atr_trend(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    ma_fast: Optional[float] = None,
    ma_slow: Optional[float] = None,
    period: int = 14,
    lookback: int = 5,
) -> int:
    """
    ATR regime vote: expanding ATR + existing trend = trend confirmation.
    If ATR expanding and trend is up → UP.
    If ATR expanding and trend is down → DOWN.
    If ATR contracting → NEUTRAL (trend weakening).
    """
    from app.market_data.indicators import atr as calc_atr

    if len(closes) < period + 1 + lookback:
        return 0
    atr_now = calc_atr(highs, lows, closes, period)
    atr_prev = calc_atr(highs[:-lookback], lows[:-lookback], closes[:-lookback], period)
    if atr_now is None or atr_prev is None:
        return 0

    # Determine trend from MA if available, otherwise from price momentum
    trend = 0
    if ma_fast is not None and ma_slow is not None:
        if ma_fast > ma_slow:
            trend = 1
        elif ma_fast < ma_slow:
            trend = -1
    else:
        trend = vote_momentum(closes, lookback)

    # ATR expanding = confirms trend direction
    if atr_now > atr_prev and trend != 0:
        return trend
    # ATR contracting = weakening signal
    return 0


def aggregate_votes(votes: List[int]) -> Tuple[str, str, float, int, int]:
    """
    Aggregate indicator votes into direction, confidence, strength.

    Returns:
        (direction, confidence, strength, aligned_count, total_count)
    """
    total = len(votes)
    if total == 0:
        return ("neutral", "low", 0.0, 0, 0)

    ups = sum(1 for v in votes if v > 0)
    downs = sum(1 for v in votes if v < 0)
    net = ups - downs
    ratio = abs(net) / total

    if ratio < 0.2:
        return ("neutral", "low", ratio, 0, total)

    direction = "up" if net > 0 else "down"
    aligned = ups if net > 0 else downs

    if ratio >= 0.7:
        confidence = "high"
    elif ratio >= 0.4:
        confidence = "medium"
    else:
        confidence = "low"

    return (direction, confidence, round(ratio, 4), aligned, total)


def aggregate_weighted_votes(
    votes: List[Tuple[int, float]],
) -> Tuple[str, str, float, int, int]:
    """
    Aggregate weighted indicator votes.

    Args:
        votes: list of (vote, weight) tuples. vote is +1/-1/0, weight is positive float.

    Returns:
        (direction, confidence, strength, aligned_count, total_count)
    """
    # Filter out neutral votes for direction calc, but count them for total
    active = [(v, w) for v, w in votes if v != 0]
    total_count = len(votes)
    active_count = len(active)

    if active_count == 0:
        return ("neutral", "low", 0.0, 0, total_count)

    total_weight = sum(abs(w) for _, w in active)
    if total_weight == 0:
        return ("neutral", "low", 0.0, 0, total_count)

    weighted_sum = sum(v * w for v, w in active)
    # strength = how much weight is aligned / total weight (abs for negative weights)
    strength = abs(weighted_sum) / total_weight

    if strength < 0.2:
        return ("neutral", "low", round(strength, 4), 0, total_count)

    direction = "up" if weighted_sum > 0 else "down"
    # Count votes whose effective contribution aligns with final direction
    aligned = sum(1 for v, w in active if (v * w > 0) == (weighted_sum > 0))

    if strength >= 0.7:
        confidence = "high"
    elif strength >= 0.4:
        confidence = "medium"
    else:
        confidence = "low"

    return (direction, confidence, round(strength, 4), aligned, total_count)
