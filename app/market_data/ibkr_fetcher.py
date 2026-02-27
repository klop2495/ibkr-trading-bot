import os
import time
from datetime import datetime
from typing import Any, List, Optional

from app.broker.contracts import create_cash_fx_contract, create_cfd_fx_contract
from app.broker.ib_utils import (
    IBConnectionError,
    IBGatewayNotReady,
    IBTimeoutError,
    connect_with_backoff,
    ib_probe_ready,
    ib_request_with_timeout,
)

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
    """
    IBKR market data fetcher.
    
    Connection modes:
    1. Injected IB instance (ib=ib_conn): Fetcher does NOT own connection,
       will NOT reconnect. If connection lost, raises error for caller to handle.
    2. Own connection (ib=None): Fetcher creates and owns IB instance,
       uses IB_CLIENT_ID_MARKETDATA for connection.
    """
    
    # Max retries for Error 326 (clientId collision)
    MAX_CLIENT_ID_RETRIES = 10
    
    def __init__(
        self,
        ib: Any | None = None,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
    ):
        self._ib = ib
        self._owns_connection = ib is None  # True if we need to create connection
        # Use provided values or fall back to environment variables
        self._host = host or os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
        self._port = port or int(os.getenv("IB_GATEWAY_PORT", "4004"))
        # Use role-specific clientId for marketdata
        self._client_id = client_id or int(os.getenv(
            "IB_CLIENT_ID_MARKETDATA",
            os.getenv("IB_CLIENT_ID", "11")  # Fallback to 11 (different from main=10)
        ))
        self._connect_attempts = int(os.getenv("IBKR_RECONNECT_ATTEMPTS", "5"))
        self._probe_timeout_s = float(os.getenv("IBKR_PROBE_TIMEOUT_S", "5"))
        self._historical_timeout_s = float(os.getenv("IBKR_HISTORICAL_TIMEOUT_S", "30"))
        self._history_contract_mode = os.getenv("IBKR_HISTORY_CONTRACT_MODE", "cash").lower()
        self._bars_cache: dict[tuple[str, str], list[Any]] = {}
        self._last_bar_ts: dict[tuple[str, str], datetime] = {}
        self._bars_cache_ts: dict[tuple[str, str], float] = {}  # epoch seconds when cache was filled

    def force_reconnect(self) -> bool:
        """Force disconnect and reconnect. Returns True if connected."""
        self._safe_disconnect()
        self._ib = None
        try:
            self._ensure_connected()
            return self._ib is not None and self._ib.isConnected()
        except Exception as e:
            print(f"force_reconnect failed: {e}")
            return False

    def _safe_disconnect(self) -> None:
        if not self._ib:
            return
        try:
            self._ib.disconnect()
        except Exception:
            pass
        if self._owns_connection:
            self._ib = None

    def _ensure_connected(self):
        """
        Ensure IB connection is active.
        
        If we were given an external IB instance (injected), we NEVER reconnect.
        We just check isConnected() and raise error if not connected.
        Caller (main loop) is responsible for reconnection.
        
        If we own the connection, we can connect/reconnect with retry on Error 326.
        """
        from ib_insync import IB
        
        # Case 1: Injected IB instance - don't own connection
        if not self._owns_connection and self._ib is not None and self._ib.isConnected():
            return self._ib
        
        # Case 2: We own the connection - create if needed
        if self._ib is None:
            self._ib = IB()

        if self._ib.isConnected():
            return self._ib

        if self._owns_connection and self._ib and not self._ib.isConnected():
            # Fresh IB instance to avoid stale event loop after disconnects.
            self._safe_disconnect()
            self._ib = IB()
        
        # Need to connect - try with retry on Error 326
        host = os.getenv("IB_GATEWAY_HOST", self._host)
        port = int(os.getenv("IB_GATEWAY_PORT", str(self._port)))
        base_client_id = int(os.getenv(
            "IB_CLIENT_ID_MARKETDATA",
            os.getenv("IB_CLIENT_ID", str(self._client_id))
        ))
        
        last_error = None
        last_gateway_error = None
        backoff_schedule = [1.0, 2.0, 5.0, 10.0, 30.0]
        for attempt in range(self._connect_attempts):
            client_id = base_client_id
            try:
                # Handle clientId collision by walking forward
                for cid_offset in range(self.MAX_CLIENT_ID_RETRIES):
                    client_id = base_client_id + cid_offset
                    try:
                        connect_with_backoff(
                            self._ib,
                            host=host,
                            port=port,
                            client_id=client_id,
                            timeout_s=30,
                            max_attempts=1,
                            backoff_schedule=[0.0],
                            probe_timeout_s=self._probe_timeout_s,
                        )
                        self._client_id = client_id
                        print(f"ibkr_marketdata_connected host={host} port={port} client_id={client_id}")
                        return self._ib
                    except Exception as e:
                        error_str = str(e).lower()
                        if "326" in str(e) or "client id" in error_str or "already in use" in error_str:
                            print(f"ibkr_clientid_collision client_id={client_id} attempt={cid_offset+1}/{self.MAX_CLIENT_ID_RETRIES}")
                            continue
                        if isinstance(e, IBGatewayNotReady):
                            last_gateway_error = e
                        else:
                            last_error = e
                        break
            except Exception as e:
                if isinstance(e, IBGatewayNotReady):
                    last_gateway_error = e
                else:
                    last_error = e
            delay = backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
            err = last_gateway_error or last_error
            print(f"ibkr_connection_failed host={host} port={port} attempt={attempt+1}/{self._connect_attempts} error={err}")
            if delay > 0:
                time.sleep(delay)
        
        # All retries exhausted
        print(f"ibkr_connection_failed_all_retries host={host} port={port} base_client_id={base_client_id}")
        if last_gateway_error:
            raise IBGatewayNotReady(str(last_gateway_error)) from last_gateway_error
        raise IBConnectionError(
            f"ibkr_connection_failed: exhausted {self._connect_attempts} attempts"
        ) from last_error

    def _make_cfd_contract(self, ib: Any, symbol: str):
        """Create CFD FX contract for the given symbol."""
        return create_cfd_fx_contract(ib, symbol)

    def _make_history_contract(self, ib: Any, symbol: str):
        """Create contract for historical data (CASH by default)."""
        if self._history_contract_mode == "cfd":
            return create_cfd_fx_contract(ib, symbol)
        return create_cash_fx_contract(ib, symbol)

    def _convert_timeframe(self, timeframe: str) -> str:
        """Convert our timeframe format to IB Gateway format."""
        return TIMEFRAME_MAP.get(timeframe, timeframe)

    def _calc_duration(self, timeframe: str, bars_needed: int) -> str:
        """Calculate duration string from bars needed."""
        minutes_per_bar = TIMEFRAME_MINUTES.get(timeframe, 15)
        total_minutes = bars_needed * minutes_per_bar
        days = (total_minutes // 1440) + 2  # Add buffer for weekends/gaps
        max_days = int(os.getenv("IBKR_HISTORY_MAX_DAYS", "60"))
        days = min(days, max_days)
        return f"{days} D"

    def fetch_historical_bars(self, symbol: str, timeframe: str, end_dt_utc: datetime, warmup_bars_min: int) -> List[Any]:
        key = (symbol, timeframe)
        interval_minutes = TIMEFRAME_MINUTES.get(timeframe, 15)
        interval_seconds = interval_minutes * 60
        # Cache refresh interval: re-fetch even within same bar to get updated close
        cache_max_age_s = int(os.getenv("IBKR_BARS_CACHE_MAX_AGE_S", "60"))
        if key in self._bars_cache and key in self._last_bar_ts:
            last_ts = self._last_bar_ts[key]
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=end_dt_utc.tzinfo or None)
            epoch = int(end_dt_utc.timestamp())
            current_bar_epoch = epoch - (epoch % interval_seconds)
            current_bar_ts = datetime.fromtimestamp(current_bar_epoch, tz=end_dt_utc.tzinfo)
            # Check cache age — even if same bar, refresh if stale
            cache_ts = self._bars_cache_ts.get(key)
            cache_age = (end_dt_utc.timestamp() - cache_ts) if cache_ts else float('inf')
            if last_ts >= current_bar_ts and cache_age < cache_max_age_s:
                return self._bars_cache[key]

        try:
            ib = self._ensure_connected()
            contract = ib_request_with_timeout(
                ib,
                lambda: self._make_history_contract(ib, symbol),
                self._historical_timeout_s,
                description="ibkr_qualify_contract",
            )
            ib_timeframe = self._convert_timeframe(timeframe)
            duration = self._calc_duration(timeframe, warmup_bars_min)
            bars = ib_request_with_timeout(
                ib,
                lambda: ib.reqHistoricalData(
                    contract=contract,
                    endDateTime="",
                    durationStr=duration,
                    barSizeSetting=ib_timeframe,
                    whatToShow="MIDPOINT",
                    useRTH=False,
                    formatDate=1,
                ),
                self._historical_timeout_s,
                description="ibkr_reqHistoricalData",
            )
            if os.getenv("CONTROL_PLANE_LOG_LEVEL", "INFO").upper() == "DEBUG":
                print(f"ibkr_fetch symbol={symbol} tf={timeframe} duration={duration} bars={len(bars or [])}")
            bars = bars or []
            if bars:
                last_bar_ts = getattr(bars[-1], "date", None) or getattr(bars[-1], "time", None)
                if last_bar_ts is not None:
                    self._bars_cache[key] = bars
                    self._last_bar_ts[key] = last_bar_ts
                    self._bars_cache_ts[key] = time.time()
            return bars
        except IBTimeoutError:
            if self._owns_connection:
                self._safe_disconnect()
            raise
        except IBGatewayNotReady:
            raise
        except IBConnectionError:
            raise
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
            contract = self._make_cfd_contract(ib, symbol)
            ticker = ib.reqMktData(contract, "", False, False)
            ib.sleep(0.1)
            if ticker and ticker.bid is not None and ticker.ask is not None:
                return float(ticker.ask - ticker.bid)
            return None
        except Exception:
            return None
