from typing import List, Optional


def sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def atr(high: List[float], low: List[float], close: List[float], period: int = 14) -> Optional[float]:
    if len(high) != len(low) or len(low) != len(close):
        raise ValueError("high, low, close lengths must match")
    if len(close) < period + 1 or period <= 0:
        return None
    trs = []
    for i in range(1, len(close)):
        high_low = high[i] - low[i]
        high_prev_close = abs(high[i] - close[i - 1])
        low_prev_close = abs(low[i] - close[i - 1])
        trs.append(max(high_low, high_prev_close, low_prev_close))
    return sum(trs[-period:]) / period


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1 or period <= 0:
        return None
    gains = []
    losses = []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
