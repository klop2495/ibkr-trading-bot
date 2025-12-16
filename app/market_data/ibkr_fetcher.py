from datetime import datetime
from typing import Any, List


class IBKRFetcher:
    def __init__(self, ib: Any | None = None):
        self._ib = ib

    def _ensure_ib(self):
        if self._ib is None:
            from ib_insync import IB

            self._ib = IB()
        return self._ib

    def fetch_historical_bars(self, symbol: str, timeframe: str, end_dt_utc: datetime, warmup_bars_min: int) -> List[Any]:
        ib = self._ensure_ib()
        try:
            bars = ib.reqHistoricalData(
                contract=ib.qualifyContracts(ib.forex(symbol))[0],
                endDateTime=end_dt_utc,
                durationStr=f"{warmup_bars_min} D",
                barSizeSetting=timeframe,
                whatToShow="MIDPOINT",
                useRTH=True,
                formatDate=1,
            )
            return bars or []
        except Exception as exc:
            raise RuntimeError("ibkr_fetch_failed") from exc

    def fetch_spread(self, symbol: str) -> float | None:
        ib = self._ensure_ib()
        try:
            ticker = ib.reqMktData(ib.qualifyContracts(ib.forex(symbol))[0], "", False, False)
            # wait briefly for data if needed
            ib.sleep(0.1)
            if ticker and ticker.bid is not None and ticker.ask is not None:
                return float(ticker.ask - ticker.bid)
            return None
        except Exception:
            return None
