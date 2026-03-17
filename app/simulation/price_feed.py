"""
PriceFeed - Real-time price data for simulation
Sources: IBKR bid/ask, IBKR bars, or market_snapshots fallback
"""
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app.broker.contracts import create_cfd_fx_contract
from app.broker.ib_utils import ib_request_with_timeout


@dataclass
class PriceQuote:
    """Current price quote for a symbol"""
    symbol: str
    bid: float
    ask: float
    mid: float
    spread: float
    timestamp: datetime
    source: str  # "IBKR", "BARS", "DB"
    
    @property
    def spread_pips(self) -> float:
        """Spread in pips"""
        if "JPY" in self.symbol:
            return self.spread * 100
        return self.spread * 10000


class PriceFeed:
    """
    Price feed for simulation engine.
    Priority: IBKR streaming > IBKR bars > market_snapshots (fallback)
    """
    
    def __init__(
        self,
        ib_client: Optional[Any] = None,
        db_client: Optional[Any] = None,
        use_bars_fallback: bool = True,
        use_db_fallback: bool = True,
        cache_ttl_seconds: int = 5,
    ):
        self.ib = ib_client
        self.db = db_client
        self.use_bars_fallback = use_bars_fallback
        self.use_db_fallback = use_db_fallback
        self.cache_ttl = cache_ttl_seconds
        
        # Price cache: symbol -> (quote, timestamp)
        self._cache: Dict[str, Tuple[PriceQuote, float]] = {}
        
        # Contracts cache for IBKR
        self._contracts: Dict[str, Any] = {}
        self._historical_timeout_s = float(os.getenv("IBKR_HISTORICAL_TIMEOUT_S", "30"))
    
    def get_quote(self, symbol: str) -> Optional[PriceQuote]:
        """
        Get current price quote for symbol.
        Uses cache if fresh, otherwise fetches from sources.
        """
        # Check cache
        if symbol in self._cache:
            quote, cached_at = self._cache[symbol]
            if time.time() - cached_at < self.cache_ttl:
                return quote
        
        # Try sources in priority order
        quote = None
        
        # 1. Try IBKR streaming/snapshot
        if self.ib is not None:
            quote = self._fetch_ibkr_quote(symbol)
        
        # 2. Fallback to IBKR bars
        if quote is None and self.use_bars_fallback and self.ib is not None:
            quote = self._fetch_ibkr_bars(symbol)
        
        # 3. Fallback to database
        if quote is None and self.use_db_fallback and self.db is not None:
            quote = self._fetch_db_quote(symbol)
        
        # Cache result
        if quote is not None:
            self._cache[symbol] = (quote, time.time())
        
        return quote
    
    def get_quotes(self, symbols: list) -> Dict[str, PriceQuote]:
        """Get quotes for multiple symbols"""
        result = {}
        for symbol in symbols:
            quote = self.get_quote(symbol)
            if quote:
                result[symbol] = quote
        return result
    
    def _get_contract(self, symbol: str) -> Any:
        """Get or create IBKR CFD FX contract."""
        if self.ib is None or not self.ib.isConnected():
            return None
        if symbol not in self._contracts:
            try:
                self._contracts[symbol] = create_cfd_fx_contract(self.ib, symbol)
            except Exception:
                return None
        return self._contracts[symbol]
    
    def _fetch_ibkr_quote(self, symbol: str) -> Optional[PriceQuote]:
        """Fetch quote from IBKR market data"""
        if self.ib is None or not self.ib.isConnected():
            return None
        
        try:
            contract = self._get_contract(symbol)
            if contract is None:
                return None
            
            # Request market data snapshot
            ticker = self.ib.reqMktData(contract, '', False, False)
            self.ib.sleep(0.5)  # Wait for data
            
            bid = ticker.bid
            ask = ticker.ask
            
            # Validate prices
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                self.ib.cancelMktData(contract)
                return None
            
            mid = (bid + ask) / 2
            spread = ask - bid
            
            self.ib.cancelMktData(contract)
            
            return PriceQuote(
                symbol=symbol,
                bid=bid,
                ask=ask,
                mid=mid,
                spread=spread,
                timestamp=datetime.now(timezone.utc),
                source="IBKR",
            )
        except Exception as e:
            print(f"IBKR quote error for {symbol}: {e}")
            return None
    
    def _fetch_ibkr_bars(self, symbol: str) -> Optional[PriceQuote]:
        """Fetch from IBKR historical bars (M1)"""
        if self.ib is None or not self.ib.isConnected():
            return None
        
        try:
            contract = self._get_contract(symbol)
            if contract is None:
                return None
            
            # Request last 1-minute bar
            bars = ib_request_with_timeout(
                self.ib,
                lambda: self.ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr="60 S",
                    barSizeSetting="1 min",
                    whatToShow="MIDPOINT",
                    useRTH=False,
                    formatDate=1,
                ),
                self._historical_timeout_s,
                description="ibkr_reqHistoricalData_sim",
            )
            
            if not bars:
                return None
            
            last_bar = bars[-1]
            close = last_bar.close
            
            # Estimate bid/ask from close (assume small spread)
            spread_estimate = 0.0001 if "JPY" not in symbol else 0.01
            bid = close - spread_estimate / 2
            ask = close + spread_estimate / 2
            
            return PriceQuote(
                symbol=symbol,
                bid=bid,
                ask=ask,
                mid=close,
                spread=spread_estimate,
                timestamp=datetime.now(timezone.utc),
                source="BARS",
            )
        except Exception as e:
            print(f"IBKR bars error for {symbol}: {e}")
            return None
    
    def _fetch_db_quote(self, symbol: str) -> Optional[PriceQuote]:
        """Fetch from market_snapshots table (fallback)"""
        if self.db is None:
            return None
        
        try:
            res = (
                self.db.table("market_snapshots")
                .select("close, ts")
                .eq("symbol", symbol)
                .eq("timeframe", "M15")
                .order("ts", desc=True)
                .limit(1)
                .execute()
            )
            
            if not res.data:
                return None
            
            row = res.data[0]
            close = float(row["close"])
            
            # Estimate spread
            spread_estimate = 0.0001 if "JPY" not in symbol else 0.01
            bid = close - spread_estimate / 2
            ask = close + spread_estimate / 2
            
            return PriceQuote(
                symbol=symbol,
                bid=bid,
                ask=ask,
                mid=close,
                spread=spread_estimate,
                timestamp=datetime.now(timezone.utc),
                source="DB",
            )
        except Exception as e:
            print(f"DB quote error for {symbol}: {e}")
            return None
    
    def clear_cache(self):
        """Clear price cache"""
        self._cache.clear()
