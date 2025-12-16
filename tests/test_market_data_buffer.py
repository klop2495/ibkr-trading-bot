from app.market_data.buffer import MarketDataBuffer


def test_buffer_keeps_last_per_key():
    buf = MarketDataBuffer()
    buf.push("EURUSD", "M15", 1)
    buf.push("EURUSD", "M15", 2)
    buf.push("GBPUSD", "H1", 3)

    snap = buf.pop_all()
    assert snap[("EURUSD", "M15")] == 2
    assert snap[("GBPUSD", "H1")] == 3
    assert buf.pop_all() == {}
