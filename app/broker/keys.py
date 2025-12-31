from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def cash_instrument_key(contract: Any) -> str:
    con_id = getattr(contract, "conId", None)
    if con_id and int(con_id) > 0:
        return f"CASH:{int(con_id)}"

    local_symbol = getattr(contract, "localSymbol", None) or ""
    if local_symbol:
        logger.warning(
            "CASH conId missing; using localSymbol fallback",
            extra={
                "secType": getattr(contract, "secType", None),
                "conId": con_id,
                "localSymbol": local_symbol,
            },
        )
        return f"CASH:{local_symbol}"

    logger.warning(
        "CASH conId/localSymbol missing; using UNKNOWN key",
        extra={
            "secType": getattr(contract, "secType", None),
            "conId": con_id,
            "localSymbol": local_symbol,
            "symbol": getattr(contract, "symbol", None),
            "currency": getattr(contract, "currency", None),
        },
    )
    return "CASH:UNKNOWN"


def instrument_key(contract: Any) -> str:
    sec_type = getattr(contract, "secType", None)
    if sec_type == "CASH":
        return cash_instrument_key(contract)

    con_id = getattr(contract, "conId", None)
    if con_id and int(con_id) > 0:
        return f"{sec_type}:{int(con_id)}"
    symbol = getattr(contract, "symbol", None) or "UNKNOWN"
    return f"{sec_type}:{symbol}"


def fx_display_symbol(contract: Any) -> str:
    if not contract:
        return "UNKNOWN"
    sec_type = getattr(contract, "secType", None)
    if sec_type != "CASH":
        return (getattr(contract, "symbol", None) or "UNKNOWN").upper()

    local_symbol = getattr(contract, "localSymbol", None) or ""
    if local_symbol:
        return local_symbol.replace(".", "").replace("/", "").upper()

    symbol = getattr(contract, "symbol", None) or ""
    currency = getattr(contract, "currency", None) or ""
    return f"{symbol}{currency}".upper()


def fx_contract_snapshot(contract: Any, position: float) -> dict:
    return {
        "secType": getattr(contract, "secType", None),
        "conId": getattr(contract, "conId", None),
        "localSymbol": getattr(contract, "localSymbol", None),
        "symbol": getattr(contract, "symbol", None),
        "currency": getattr(contract, "currency", None),
        "position": position,
    }
