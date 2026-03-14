from app.execution.fx_pnl import calculate_realized_pnl_usd


def test_calculate_realized_pnl_usd_direct_quote():
    pnl, pips = calculate_realized_pnl_usd(
        symbol="GBPUSD",
        side="SELL",
        entry_price=1.34210,
        exit_price=1.33410,
        quantity=26000,
    )

    assert round(pips, 1) == 80.0
    assert round(pnl, 2) == 208.0


def test_calculate_realized_pnl_usd_inverse_quote_uses_exit_price():
    pnl, pips = calculate_realized_pnl_usd(
        symbol="USDCHF",
        side="BUY",
        entry_price=0.77980,
        exit_price=0.78360,
        quantity=20000,
    )

    assert round(pips, 1) == 38.0
    assert round(pnl, 2) == 96.99


def test_calculate_realized_pnl_usd_cross_uses_rate_resolver():
    rates = {"JPY": 1 / 92.105}
    pnl, pips = calculate_realized_pnl_usd(
        symbol="NZDJPY",
        side="SELL",
        entry_price=92.540,
        exit_price=92.105,
        quantity=40000,
        rate_resolver=lambda quote: rates.get(quote),
    )

    assert round(pips, 1) == 43.5
    assert round(pnl, 2) == 188.91


def test_calculate_realized_pnl_usd_cross_returns_none_when_rate_missing():
    pnl, pips = calculate_realized_pnl_usd(
        symbol="EURGBP",
        side="BUY",
        entry_price=0.87425,
        exit_price=0.87445,
        quantity=20000,
        rate_resolver=lambda _quote: None,
    )

    assert round(pips, 1) == 2.0
    assert pnl is None
