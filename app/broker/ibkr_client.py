from datetime import datetime
from typing import Any, List

from app.models.ibkr import (
    IBKRAccountSummary,
    IBKRAccountValue,
    IBKRConnectionConfig,
    IBKRPosition,
    IBKRPositionsSnapshot,
)


class IBKRClient:
    def __init__(self, config: IBKRConnectionConfig, ib: Any | None = None) -> None:
        self.config = config
        if ib is None:
            from ib_insync import IB

            self.ib = IB()
        else:
            self.ib = ib

    def connect(self) -> None:
        try:
            self.ib.connect(
                self.config.host,
                self.config.port,
                clientId=self.config.client_id,
                readonly=True,
            )
        except Exception as exc:
            raise RuntimeError("ibkr_connect_failed") from exc

    def disconnect(self) -> None:
        try:
            if hasattr(self.ib, "disconnect"):
                self.ib.disconnect()
        except Exception:
            # swallow disconnect issues; disconnect should be safe
            pass

    def is_connected(self) -> bool:
        try:
            return bool(self.ib.isConnected())
        except Exception:
            return False

    def get_account_summary(self, ts_utc: datetime) -> IBKRAccountSummary:
        try:
            raw_values = self.ib.accountSummary()
        except Exception as exc:
            raise RuntimeError("ibkr_fetch_failed") from exc

        values: List[IBKRAccountValue] = []
        for v in raw_values:
            values.append(
                IBKRAccountValue(
                    tag=getattr(v, "tag", None),
                    value=getattr(v, "value", None),
                    currency=getattr(v, "currency", None),
                    account=getattr(v, "account", None),
                )
            )
        return IBKRAccountSummary(ts_utc=ts_utc, values=values)

    def get_positions_snapshot(self, ts_utc: datetime) -> IBKRPositionsSnapshot:
        try:
            raw_positions = self.ib.positions()
        except Exception as exc:
            raise RuntimeError("ibkr_fetch_failed") from exc

        positions: List[IBKRPosition] = []
        for p in raw_positions:
            contract = getattr(p, "contract", None)
            positions.append(
                IBKRPosition(
                    account=getattr(p, "account", None),
                    symbol=getattr(contract, "symbol", None),
                    sec_type=getattr(contract, "secType", None) if contract else None,
                    currency=getattr(contract, "currency", None) if contract else None,
                    exchange=getattr(contract, "exchange", None) if contract else None,
                    position=getattr(p, "position", 0.0),
                    avg_cost=getattr(p, "avgCost", None),
                )
            )

        return IBKRPositionsSnapshot(ts_utc=ts_utc, positions=positions)
