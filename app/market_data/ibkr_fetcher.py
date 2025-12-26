import os
from datetime import datetime
from typing import Any, List, Optional


# Mapping from our timeframe format to IB Gateway format
TIMEFRAME_MAP = {
    "M1": "1 min",
    "M5": "5 mins",
    "M15": "15 mins",
    "M30": "30 mins",
    "H1": "1 hour",
    "H4": "4 hours",
    "D1": "1 day",
}

# Minutes per timeframe for duration calculation
TIMEFRAME_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


class IBKRFetcher:
    def __init__(
        self,
        ib: Any | None = None,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
    ):
        self._ib = ib
        self._owns_connection = ib is None  # True if we created the connection
        # Use provided values or fall back to environment variables
        self._host = host or os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
        self._port = port or int(os.getenv("IB_GATEWAY_PORT", "4004"))
        self._client_id = client_id or int(os.getenv("IB_CLIENT_ID", "10"))

    def _ensure_connected(self):
        """
        Ensure IB connection is active.
        
        If we were given an external IB instance, we just check it's connected
        and raise an error if not (caller is responsible for reconnection).
        
        If we created our own IB instance, we can reconnect.
        """
        from ib_insync import IB
        
        if self._ib is None:
            self._ib = IB()
            self._owns_connection = True
        
        if not self._ib.isConnected():
            if not self._owns_connection:
                # External IB instance lost connection - don't try to reconnect
                # Let the caller handle reconnection
                raise RuntimeError(f"ibkr_connection_lost: external IB connection is not connected")
            
            # We own the connection, try to reconnect
            host = os.getenv("IB_GATEWAY_HOST", self._host)
            port = int(os.getenv("IB_GATEWAY_PORT", str(self._port)))
            client_id = int(os.getenv("IB_CLIENT_ID", str(self._client_id)))
            
            try:
                self._ib.RequestTimeout = 60
                self._ib.connect(
                    host, 
                    port, 
                    clientId=client_id, 
                    timeout=30
                )
                # Update instance vars on successful reconnect
                self._host = host
                self._port = port
                self._client_id = client_id
                print(f"ibkr_reconnect host={host} port={port} client_id={client_id}")
            except Exception as e:
                print(f"ibkr_connection_failed host={host} port={port} error={e}")
                raise RuntimeError(f"ibkr_connection_failed: host={host} port={port} error={e}") from e
        
        return self._ib

    def _make_forex_contract(self, symbol: str):
        """Convert symbol like 'EUR/USD' to Forex contract."""
        from ib_insync import Forex
        pair = symbol.replace("/", "")
        return Forex(pair)

    def _convert_timeframe(self, timeframe: str) -> str:
        """Convert our timeframe format to IB Gateway format."""
        return TIMEFRAME_MAP.get(timeframe, timeframe)

    def _calc_duration(self, timeframe: str, bars_needed: int) -> str:
        """Calculate duration string from bars needed."""
        minutes_per_bar = TIMEFRAME_MINUTES.get(timeframe, 15)
        total_minutes = bars_needed * minutes_per_bar
        days = (total_minutes // 1440) + 2  # Add buffer for weekends/gaps
        days = min(days, 30)  # Cap at 30 days
        return f"{days} D"

    def fetch_historical_bars(self, symbol: str, timeframe: str, end_dt_utc: datetime, warmup_bars_min: int) -> List[Any]:
        try:
            ib = self._ensure_connected()
            contract = self._make_forex_contract(symbol)
            ib.qualifyContracts(contract)
            ib_timeframe = self._convert_timeframe(timeframe)
            duration = self._calc_duration(timeframe, warmup_bars_min)
            bars = ib.reqHistoricalData(
                contract=contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=ib_timeframe,
                whatToShow='MIDPOINT',
                useRTH=False,
                formatDate=1,
            )
            if os.getenv("CONTROL_PLANE_LOG_LEVEL", "INFO").upper() == "DEBUG":
                print(f"ibkr_fetch symbol={symbol} tf={timeframe} duration={duration} bars={len(bars or [])}")
            return bars or []
        except Exception as exc:
            # Log the error with details
            print(f"ibkr_fetch_error symbol={symbol} tf={timeframe} error={exc}")
            # Only reset connection if we own it
            if self._owns_connection and self._ib:
                try:
                    self._ib.disconnect()
                except:
                    pass
                self._ib = None
            raise RuntimeError(f"ibkr_fetch_failed: symbol={symbol} tf={timeframe} error={exc}") from exc

    def fetch_spread(self, symbol: str) -> float | None:
        try:
            ib = self._ensure_connected()
            contract = self._make_forex_contract(symbol)
            ib.qualifyContracts(contract)
            ticker = ib.reqMktData(contract, "", False, False)
            ib.sleep(0.1)
            if ticker and ticker.bid is not None and ticker.ask is not None:
                return float(ticker.ask - ticker.bid)
            return None
        except Exception:
            return None
