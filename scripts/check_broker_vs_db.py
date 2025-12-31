import os
import sys
from typing import List

from ib_insync import IB

from app.broker.keys import fx_contract_snapshot, fx_display_symbol, instrument_key
from app.storage.db import SupabaseDB
from app.storage.repositories import TradesHistoryRepo


def main() -> int:
    host = os.getenv("IB_GATEWAY_HOST", os.getenv("IBKR_HOST", "127.0.0.1"))
    port = int(os.getenv("IB_GATEWAY_PORT", os.getenv("IBKR_PORT", "4004")))
    client_id = int(os.getenv("IB_CLIENT_ID_DIAG", "7001"))

    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=10)
    except Exception as exc:
        print(f"BROKER_UNTRUSTED: connect_failed {exc}")
        return 3

    try:
        positions = [p for p in ib.positions() if getattr(p, "position", 0.0)]
        open_orders = list(ib.openOrders())
        open_trades = list(ib.openTrades())
    except Exception as exc:
        print(f"BROKER_UNTRUSTED: snapshot_failed {exc}")
        return 3
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    print("BROKER POSITIONS (FX CASH):")
    broker_symbols: List[str] = []
    for pos in positions:
        contract = getattr(pos, "contract", None)
        if getattr(contract, "secType", None) != "CASH":
            continue
        qty = float(getattr(pos, "position", 0.0) or 0.0)
        snapshot = fx_contract_snapshot(contract, qty)
        key = instrument_key(contract)
        display = fx_display_symbol(contract)
        broker_symbols.append(display)
        print(
            f"{display} key={key} qty={qty} "
            f"secType={snapshot.get('secType')} conId={snapshot.get('conId')} "
            f"localSymbol={snapshot.get('localSymbol')} symbol={snapshot.get('symbol')} "
            f"currency={snapshot.get('currency')}"
        )

    print(f"openOrders={len(open_orders)} openTrades={len(open_trades)}")

    db = SupabaseDB()
    if db.disabled:
        print("BROKER_UNTRUSTED: db_disabled")
        return 3

    trades_repo = TradesHistoryRepo(db)
    try:
        active_trades = trades_repo.get_active_trades_full()
    except Exception as exc:
        print(f"BROKER_UNTRUSTED: db_fetch_failed {exc}")
        return 3

    db_symbols = [str(t.get("symbol") or "").upper() for t in active_trades if t.get("symbol")]
    active_count = len(active_trades)
    nonzero_count = len([t for t in active_trades if (t.get("quantity") or 0) != 0])

    print(f"DB active trades={active_count} nonzero_qty={nonzero_count}")

    broker_set = {s.upper() for s in broker_symbols}
    db_set = {s.upper() for s in db_symbols}

    if broker_set != db_set:
        missing_in_broker = sorted(db_set - broker_set)
        missing_in_db = sorted(broker_set - db_set)
        print(f"MISMATCH missing_in_broker={missing_in_broker} missing_in_db={missing_in_db}")
        return 2

    print("CONSISTENT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
