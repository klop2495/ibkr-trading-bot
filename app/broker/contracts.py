from __future__ import annotations

from typing import Any

def _normalize_pair(pair: str) -> tuple[str, str]:
    normalized = pair.replace(".", "").replace("/", "").replace(" ", "").upper()
    if len(normalized) != 6:
        raise ValueError(f"invalid_fx_pair:{pair}")
    return normalized[:3], normalized[3:]


def ensure_conid(contract: Any) -> None:
    con_id = getattr(contract, "conId", None)
    if not con_id or int(con_id) <= 0:
        raise RuntimeError("missing_conId_after_qualify")


def create_cfd_fx_contract(ib: Any, pair: str) -> Any:
    base, quote = _normalize_pair(pair)
    from ib_insync import Contract

    contract = Contract(
        secType="CFD",
        symbol=base,
        currency=quote,
        exchange="SMART",
    )
    qualified = ib.qualifyContracts(contract)
    if qualified:
        contract = qualified[0]
    ensure_conid(contract)
    return contract


def create_cash_fx_contract(ib: Any, pair: str) -> Any:
    base, quote = _normalize_pair(pair)
    from ib_insync import Contract

    contract = Contract(
        secType="CASH",
        symbol=base,
        currency=quote,
        exchange="IDEALPRO",
    )
    qualified = ib.qualifyContracts(contract)
    if qualified:
        contract = qualified[0]
    ensure_conid(contract)
    return contract
