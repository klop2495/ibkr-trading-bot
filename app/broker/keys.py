from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def cash_instrument_key(contract: Any) -> Optional[str]:
    con_id = getattr(contract, "conId", None)
    if con_id and int(con_id) > 0:
        return f"CASH:{int(con_id)}"

    logger.warning(
        "CASH conId missing; cannot build instrument key",
        extra={
            "secType": getattr(contract, "secType", None),
            "conId": con_id,
            "localSymbol": getattr(contract, "localSymbol", None),
            "symbol": getattr(contract, "symbol", None),
            "currency": getattr(contract, "currency", None),
        },
    )
    return None


def instrument_key(contract: Any) -> Optional[str]:
    sec_type = getattr(contract, "secType", None)
    con_id = getattr(contract, "conId", None)
    if not sec_type:
        logger.warning("contract secType missing; cannot build instrument key")
        return None
    if not con_id or int(con_id) <= 0:
        logger.warning(
            "contract conId missing; cannot build instrument key",
            extra={
                "secType": sec_type,
                "conId": con_id,
                "localSymbol": getattr(contract, "localSymbol", None),
                "symbol": getattr(contract, "symbol", None),
                "currency": getattr(contract, "currency", None),
            },
        )
        return None
    return f"{sec_type}:{int(con_id)}"


def fx_display_symbol(contract: Any) -> str:
    if not contract:
        return "UNKNOWN"
    sec_type = getattr(contract, "secType", None)
    local_symbol = getattr(contract, "localSymbol", None) or ""
    if local_symbol:
        return local_symbol.replace(".", "").replace("/", "").upper()
    if sec_type != "CASH":
        return (getattr(contract, "symbol", None) or "UNKNOWN").upper()

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
