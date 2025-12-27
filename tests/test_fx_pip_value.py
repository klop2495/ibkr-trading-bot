from app.pm.fx_pip_value import get_pip_size, pip_value_per_unit


def test_pip_size_jpy():
    assert get_pip_size("USDJPY") == 0.01
    assert get_pip_size("EURUSD") == 0.0001


def test_pip_value_per_unit_quote_usd():
    # EURUSD quote USD: pip value per unit = pip size
    val = pip_value_per_unit("EURUSD", price=1.1000, account_currency="USD")
    assert val == 0.0001


def test_pip_value_per_unit_base_usd():
    # USDJPY base USD: pip value per unit = pip_size / price
    val = pip_value_per_unit("USDJPY", price=150.0, account_currency="USD")
    assert val == 0.01 / 150.0


def test_pip_value_invalid_currency():
    assert pip_value_per_unit("EURUSD", price=1.1, account_currency="EUR") is None

