"""
Deterministic pip value calculations for FX pairs.
Supports USD and EUR account currencies.
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
    
    Supports major pairs and crosses with USD or EUR account currency.
    
    Args:
        symbol: Currency pair (e.g., 'EURUSD', 'CHFJPY')
        price: Current price of the pair
        account_currency: Account currency ('USD' or 'EUR')
    
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
    
    quote = sym[3:6]
    pip_value_quote = pip
    
    # Quote currency matches account currency
    if quote == acc:
        return pip_value_quote
    
    # USD quote with EUR account
    if quote == "USD" and acc == "EUR":
        return pip_value_quote / APPROX_RATES.get("EURUSD", 1.04)
    
    # EUR quote with USD account
    if quote == "EUR" and acc == "USD":
        return pip_value_quote * APPROX_RATES.get("EURUSD", 1.04)
    
    # JPY quote currency
    if quote == "JPY":
        if acc == "USD":
            return pip_value_quote / APPROX_RATES.get("USDJPY", 157.0)
        if acc == "EUR":
            return pip_value_quote / APPROX_RATES.get("EURJPY", 163.0)
    
    # CHF quote currency
    if quote == "CHF":
        if acc == "USD":
            return pip_value_quote / APPROX_RATES.get("USDCHF", 0.90)
        if acc == "EUR":
            return pip_value_quote / APPROX_RATES.get("EURCHF", 0.94)
    
    # GBP quote currency
    if quote == "GBP":
        if acc == "USD":
            return pip_value_quote * APPROX_RATES.get("GBPUSD", 1.25)
        if acc == "EUR":
            return pip_value_quote / APPROX_RATES.get("EURGBP", 0.83)
    
    # AUD quote currency
    if quote == "AUD":
        if acc == "USD":
            return pip_value_quote * APPROX_RATES.get("AUDUSD", 0.62)
        if acc == "EUR":
            return pip_value_quote / APPROX_RATES.get("EURAUD", 1.67)
    
    # CAD quote currency
    if quote == "CAD":
        if acc == "USD":
            return pip_value_quote / APPROX_RATES.get("USDCAD", 1.44)
        if acc == "EUR":
            eurusd = APPROX_RATES.get("EURUSD", 1.04)
            usdcad = APPROX_RATES.get("USDCAD", 1.44)
            return pip_value_quote / (eurusd * usdcad)
    
    # NZD quote currency
    if quote == "NZD":
        if acc == "USD":
            return pip_value_quote * APPROX_RATES.get("NZDUSD", 0.56)
        if acc == "EUR":
            # EURNZD approximation
            eurusd = APPROX_RATES.get("EURUSD", 1.04)
            nzdusd = APPROX_RATES.get("NZDUSD", 0.56)
            return pip_value_quote / (eurusd / nzdusd)
    
    # Fallback for other pairs - rough estimate
    return pip_value_quote / price if price > 1 else pip_value_quote * price
