"""
Deterministic pip value calculations for FX majors (account currency USD).
"""

from typing import Optional


def get_pip_size(symbol: str) -> float:
    """Return pip size for a symbol (0.01 for JPY pairs, else 0.0001)."""
    if symbol and symbol.upper().endswith("JPY"):
        return 0.01
    return 0.0001


def pip_value_per_unit(symbol: str, price: float, account_currency: str = "USD") -> Optional[float]:
    """
    Pip value per unit of base currency, in account currency.

    Supports v1 majors with account_currency USD.
    - If quote is USD (e.g., EURUSD): pip value per unit = pip_size
    - If base is USD (e.g., USDJPY, USDCHF, USDCAD): pip value per unit = pip_size / price
    Other symbols/account currencies are unsupported → None (fail-safe).
    """
    if price is None or price <= 0:
        return None
    if account_currency.upper() != "USD":
        return None

    sym = (symbol or "").upper()
    pip = get_pip_size(sym)

    # Quote currency is USD (EURUSD, GBPUSD, AUDUSD, NZDUSD)
    if sym.endswith("USD"):
        return pip

    # Base currency is USD (USDJPY, USDCHF, USDCAD)
    if sym.startswith("USD"):
        return pip / price

    # Unsupported pair for v1 scope
    return None
