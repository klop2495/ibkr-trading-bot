import math

from app.market_data.indicators import atr, rsi, sma


def test_sma_basic():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1, 2, 3], 5) is None


def test_atr_insufficient_bars():
    assert atr([1, 2], [1, 2], [1, 2], period=14) is None


def test_atr_basic():
    high = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]
    low = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
    close = list(range(9, 24))
    value = atr(high, low, close, period=14)
    assert value is not None
    assert value > 0


def test_rsi_insufficient():
    assert rsi([1, 2, 3], period=14) is None


def test_rsi_basic():
    data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    value = rsi(data, period=14)
    assert value is not None
    assert value > 50
