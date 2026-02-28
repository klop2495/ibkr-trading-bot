"""
Deterministic pip value calculations for FX pairs.
Supports USD account currency.
"""

from typing import Optional


def get_pip_size(symbol: str) -> float:
    """Return pip size for a symbol (0.01 for JPY pairs, else 0.0001)."""
    if symbol and symbol.upper().endswith("JPY"):
        return 0.01
    return 0.0001


# Approximate conversion rates for pip value calculation
APPROX_RATES = {
    "EURUSD": 1.04,
    "USDJPY": 157.0,
    "GBPUSD": 1.25,
    "USDCHF": 0.90,
    "AUDUSD": 0.62,
    "NZDUSD": 0.56,
    "USDCAD": 1.44,
    "EURJPY": 163.0,
    "GBPJPY": 196.0,
    "CHFJPY": 175.0,
    "AUDJPY": 97.0,
    "CADJPY": 109.0,
    "EURGBP": 0.83,
    "EURCHF": 0.94,
    "EURAUD": 1.67,
    "GBPCHF": 1.13,
}


def pip_value_per_unit(symbol: str, price: float, account_currency: str = "USD") -> Optional[float]:
    """
    Pip value per unit of base currency, in account currency.
    
    Supports USD account currency.
    
    Args:
        symbol: Currency pair (e.g., 'EURUSD', 'CHFJPY')
        price: Current price of the pair
        account_currency: Account currency ('USD')
    
    Returns:
        Pip value per 1 unit, or None if unsupported.
    """
    if price is None or price <= 0:
        return None
    
    sym = (symbol or "").upper()
    pip = get_pip_size(sym)
    acc = account_currency.upper()
    
    if len(sym) != 6:
        return None
    if acc != "USD":
        return None
    
    quote = sym[3:6]
    pip_value_quote = pip
    
    # Quote currency matches account currency
    if quote == acc:
        return pip_value_quote
    
    # Base currency matches account currency (e.g. USDJPY)
    if sym[:3] == acc:
        return pip_value_quote / price
    
    # JPY quote currency
    if quote == "JPY":
        return pip_value_quote / APPROX_RATES.get("USDJPY", 157.0)
    
    # CHF quote currency
    if quote == "CHF":
        return pip_value_quote / APPROX_RATES.get("USDCHF", 0.90)
    
    # GBP quote currency
    if quote == "GBP":
        return pip_value_quote * APPROX_RATES.get("GBPUSD", 1.25)
    
    # AUD quote currency
    if quote == "AUD":
        return pip_value_quote * APPROX_RATES.get("AUDUSD", 0.62)
    
    # CAD quote currency
    if quote == "CAD":
        return pip_value_quote / APPROX_RATES.get("USDCAD", 1.44)
    
    # NZD quote currency
    if quote == "NZD":
        return pip_value_quote * APPROX_RATES.get("NZDUSD", 0.56)
    
    # Fallback for other pairs - rough estimate
    return pip_value_quote / price if price > 1 else pip_value_quote * price
