from app.market_data.timeframes import TF_TO_SECONDS


def is_warmup_ready(counts, required, symbols, timeframes):
    for sym in symbols:
        for tf in timeframes:
            if counts.get((sym, tf), 0) < required:
                return False
    return True


def test_warmup_ready_only_when_all_reached():
    symbols = ["EURUSD", "GBPUSD"]
    tfs = ["M15", "H1"]
    counts = {("EURUSD", "M15"): 300, ("EURUSD", "H1"): 300, ("GBPUSD", "M15"): 300, ("GBPUSD", "H1"): 299}
    assert is_warmup_ready(counts, 300, symbols, tfs) is False
    counts[("GBPUSD", "H1")] = 300
    assert is_warmup_ready(counts, 300, symbols, tfs) is True
