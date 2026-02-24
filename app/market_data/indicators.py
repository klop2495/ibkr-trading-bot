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


def ema(values: List[float], period: int) -> Optional[float]:
    """Exponential Moving Average."""
    if len(values) < period or period <= 0:
        return None
    k = 2 / (period + 1)
    result = sum(values[:period]) / period  # SMA seed
    for v in values[period:]:
        result = v * k + result * (1 - k)
    return result


def adx(high: List[float], low: List[float], close: List[float], period: int = 14) -> Optional[float]:
    """
    Average Directional Index — measures trend strength (not direction).
    ADX < 20 = weak/no trend (range), ADX > 25 = trending.
    Returns ADX value (0-100) or None if insufficient data.
    """
    n = len(close)
    if n < period * 2 + 1 or len(high) != n or len(low) != n:
        return None

    # Step 1: Calculate +DM, -DM, TR
    plus_dm_list = []
    minus_dm_list = []
    tr_list = []
    for i in range(1, n):
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        tr_list.append(tr)

    if len(tr_list) < period * 2:
        return None

    # Step 2: Smoothed +DM, -DM, TR (Wilder's smoothing)
    atr_smooth = sum(tr_list[:period])
    plus_dm_smooth = sum(plus_dm_list[:period])
    minus_dm_smooth = sum(minus_dm_list[:period])

    dx_list = []
    for i in range(period, len(tr_list)):
        atr_smooth = atr_smooth - (atr_smooth / period) + tr_list[i]
        plus_dm_smooth = plus_dm_smooth - (plus_dm_smooth / period) + plus_dm_list[i]
        minus_dm_smooth = minus_dm_smooth - (minus_dm_smooth / period) + minus_dm_list[i]

        if atr_smooth == 0:
            continue
        plus_di = 100 * plus_dm_smooth / atr_smooth
        minus_di = 100 * minus_dm_smooth / atr_smooth
        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_list.append(0.0)
        else:
            dx_list.append(100 * abs(plus_di - minus_di) / di_sum)

    if len(dx_list) < period:
        return None

    # Step 3: ADX = smoothed average of DX
    adx_val = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx_val = (adx_val * (period - 1) + dx_list[i]) / period

    return round(adx_val, 4)


def bollinger_bands(
    closes: List[float], period: int = 20, std_mult: float = 2.0
) -> Optional[dict]:
    """
    Bollinger Bands: upper, middle (SMA), lower, width, percent_b.
    Width = (upper - lower) / middle — normalized band width.
    """
    if len(closes) < period or period <= 0:
        return None
    data = closes[-period:]
    middle = sum(data) / period
    if middle == 0:
        return None
    variance = sum((x - middle) ** 2 for x in data) / period
    std = variance ** 0.5
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    width = (upper - lower) / middle
    price = closes[-1]
    percent_b = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return {
        "upper": round(upper, 6),
        "middle": round(middle, 6),
        "lower": round(lower, 6),
        "width": round(width, 6),
        "percent_b": round(percent_b, 4),
        "std": round(std, 6),
    }


def keltner_channel(
    high: List[float], low: List[float], close: List[float],
    ema_period: int = 20, atr_period: int = 10, atr_mult: float = 1.5,
) -> Optional[dict]:
    """
    Keltner Channel: EMA ± ATR multiplier.
    Used with Bollinger Bands to detect squeeze.
    """
    if len(close) < max(ema_period, atr_period + 1):
        return None
    mid = ema(close, ema_period)
    atr_val = atr(high, low, close, atr_period)
    if mid is None or atr_val is None:
        return None
    upper = mid + atr_mult * atr_val
    lower = mid - atr_mult * atr_val
    return {
        "upper": round(upper, 6),
        "middle": round(mid, 6),
        "lower": round(lower, 6),
    }


def is_squeeze(
    high: List[float], low: List[float], close: List[float],
    bb_period: int = 20, bb_std: float = 2.0,
    kc_ema_period: int = 20, kc_atr_period: int = 10, kc_atr_mult: float = 1.5,
) -> Optional[bool]:
    """
    Squeeze detection: BB inside Keltner Channel.
    True = squeeze (low volatility, no trend), False = no squeeze.
    """
    bb = bollinger_bands(close, bb_period, bb_std)
    kc = keltner_channel(high, low, close, kc_ema_period, kc_atr_period, kc_atr_mult)
    if bb is None or kc is None:
        return None
    return bb["lower"] > kc["lower"] and bb["upper"] < kc["upper"]


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
