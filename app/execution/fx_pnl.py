from __future__ import annotations

from typing import Any, Callable, Optional, Tuple


PIP_SIZES = {
    "JPY": 0.01,
}


def normalize_fx_symbol(symbol: str) -> tuple[str, str]:
    clean = str(symbol or "").replace("/", "").replace(".", "").strip().upper()
    if len(clean) < 6:
        return "", ""
    return clean[:3], clean[3:6]


def get_pip_size(symbol: str) -> float:
    _, quote = normalize_fx_symbol(symbol)
    if quote == "JPY":
        return PIP_SIZES["JPY"]
    return 0.0001


def calculate_pnl_pips(symbol: str, side: str, entry_price: float, exit_price: float) -> Optional[float]:
    pip_size = get_pip_size(symbol)
    if pip_size <= 0:
        return None

    side_upper = str(side or "").upper()
    if side_upper.startswith("B"):
        return (float(exit_price) - float(entry_price)) / pip_size
    if side_upper.startswith("S"):
        return (float(entry_price) - float(exit_price)) / pip_size
    return None


def choose_rate_to_usd(
    quote_ccy: str,
    *,
    exit_price: float,
    base_ccy: str,
    rate_resolver: Optional[Callable[[str], Optional[float]]] = None,
) -> Optional[float]:
    quote = str(quote_ccy or "").upper()
    base = str(base_ccy or "").upper()
    if quote == "USD":
        return 1.0

    if base == "USD" and exit_price and exit_price > 0:
        return 1.0 / float(exit_price)

    if rate_resolver:
        resolved = rate_resolver(quote)
        if resolved and resolved > 0:
            return resolved

    return None


def calculate_realized_pnl_usd(
    *,
    symbol: str,
    side: str,
    entry_price: Any,
    exit_price: Any,
    quantity: Any,
    rate_resolver: Optional[Callable[[str], Optional[float]]] = None,
) -> Tuple[Optional[float], Optional[float]]:
    try:
        entry = float(entry_price)
        exit_px = float(exit_price)
        qty = abs(float(quantity))
    except Exception:
        return None, None

    if qty <= 0:
        return None, None

    base, quote = normalize_fx_symbol(symbol)
    pnl_pips = calculate_pnl_pips(symbol, side, entry, exit_px)
    if pnl_pips is None:
        return None, None

    raw_quote_pnl = pnl_pips * get_pip_size(symbol) * qty
    usd_rate = choose_rate_to_usd(
        quote,
        exit_price=exit_px,
        base_ccy=base,
        rate_resolver=rate_resolver,
    )
    if usd_rate is None or usd_rate <= 0:
        return None, pnl_pips

    return raw_quote_pnl * usd_rate, pnl_pips

