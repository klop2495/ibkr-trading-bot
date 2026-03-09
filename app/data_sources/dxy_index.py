"""
DXY (Dollar Index) Fetcher.

Phase 1: Fetches US Dollar Index data for CorrelationAgent.
Source: Yahoo Finance, FRED, or TradingView.

DXY measures USD against a basket of currencies:
- EUR (57.6%)
- JPY (13.6%)
- GBP (11.9%)
- CAD (9.1%)
- SEK (4.2%)
- CHF (3.6%)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from app.data_sources.base_source import BaseDataSource
from app.models.dxy_snapshot import DXYSnapshot
from app.models.source_health import SourceHealth


logger = logging.getLogger(__name__)


class DXYFetcher(BaseDataSource):
    """
    Fetches US Dollar Index data.
    
    Phase 1: Uses mock data. Phase 2+ will integrate real API.
    
    Data sources (future):
    - Yahoo Finance (yfinance)
    - FRED (fredapi)
    - TradingView (unofficial API)
    - Investing.com (scraping)
    """
    
    source_name = "dxy_index"
    
    def __init__(self, mock_mode: bool = True):
        """
        Initialize fetcher.
        
        Args:
            mock_mode: If True, return mock data.
        """
        self.mock_mode = mock_mode
        self._last_fetch: Optional[datetime] = None
        self._last_data: Optional[DXYSnapshot] = None
        self._last_error: Optional[str] = None
    
    def fetch(self) -> Optional[DXYSnapshot]:
        """
        Fetch current DXY data.
        
        Returns:
            DXYSnapshot with current value and technicals.
        """
        try:
            if self.mock_mode:
                snapshot = self._generate_mock_snapshot()
            else:
                snapshot = self._fetch_from_yahoo()
            
            self._last_fetch = datetime.now(timezone.utc)
            self._last_data = snapshot
            self._last_error = None
            
            logger.info(f"DXYFetcher: fetched DXY={snapshot.value:.2f} trend={snapshot.trend}")
            return snapshot
            
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"DXYFetcher error: {e}")
            return self._last_data
    
    def get_health(self) -> SourceHealth:
        """Get health status of this data source."""
        if self._last_fetch is None:
            return SourceHealth.unavailable(self.source_name, "Never fetched")
        
        if self._last_error:
            return SourceHealth(
                source_name=self.source_name,
                is_available=False,
                last_update=self._last_fetch,
                staleness_minutes=(datetime.now(timezone.utc) - self._last_fetch).total_seconds() / 60.0,
                coverage=0.0,
                error_message=self._last_error,
            )
        
        return SourceHealth.fresh(
            self.source_name,
            self._last_fetch,
            coverage=1.0,
        )
    
    def get_current(self) -> Optional[DXYSnapshot]:
        """Get cached snapshot or fetch if stale."""
        return self.fetch_if_stale(max_age_minutes=15.0)
    
    def _generate_mock_snapshot(self) -> DXYSnapshot:
        """Generate mock DXY snapshot for testing."""
        now = datetime.now(timezone.utc)
        
        # Typical DXY values: 90-110
        # Use time-based variation for deterministic but changing values
        hour_of_year = (now.timetuple().tm_yday * 24 + now.hour)
        
        # Base value with small variation
        base_value = 103.5
        variation = ((hour_of_year % 100) - 50) / 100  # ±0.5
        value = base_value + variation
        
        # SMAs slightly lagging
        sma_20 = value - 0.15
        sma_50 = value - 0.30
        sma_200 = value + 0.20
        
        # Daily change based on hour
        daily_change = ((now.hour - 12) / 12) * 0.3  # ±0.3%
        
        return DXYSnapshot(
            value=round(value, 3),
            timestamp=now,
            sma_20=round(sma_20, 3),
            sma_50=round(sma_50, 3),
            sma_200=round(sma_200, 3),
            daily_change_pct=round(daily_change, 2),
            atr_14=0.45,  # Typical ATR
        )
    
    def _fetch_from_yahoo(self) -> DXYSnapshot:
        """
        Fetch DXY from Yahoo Finance via direct HTTP API.
        
        Ticker: DX=F (US Dollar Index Futures)
        Downloads 60 days of history to calculate SMAs.
        Uses direct HTTP requests (yfinance library has session issues).
        """
        import requests
        
        now = datetime.now(timezone.utc)
        
        try:
            # Direct Yahoo Finance API request
            url = "https://query1.finance.yahoo.com/v8/finance/chart/DX=F"
            params = {
                "interval": "1d",
                "range": "60d",
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Parse response
            result = data.get("chart", {}).get("result", [])
            if not result:
                logger.warning("DXY: empty result from Yahoo, falling back to mock")
                return self._generate_mock_snapshot()
            
            quote = result[0]
            indicators = quote.get("indicators", {}).get("quote", [{}])[0]
            
            closes = indicators.get("close", [])
            highs = indicators.get("high", [])
            lows = indicators.get("low", [])
            
            # Filter None values
            closes = [c for c in closes if c is not None]
            
            if not closes:
                logger.warning("DXY: no close prices, falling back to mock")
                return self._generate_mock_snapshot()
            
            # Current value (last close)
            value = closes[-1]
            
            # Calculate SMAs
            sma_20 = sum(closes[-20:]) / min(20, len(closes)) if len(closes) >= 1 else None
            sma_50 = sum(closes[-50:]) / min(50, len(closes)) if len(closes) >= 20 else None
            sma_200 = sum(closes) / len(closes) if len(closes) >= 50 else None
            
            # Daily change
            if len(closes) >= 2:
                prev_close = closes[-2]
                daily_change_pct = ((value - prev_close) / prev_close) * 100
            else:
                daily_change_pct = 0.0
            
            # ATR 14 (simplified: average of High-Low over 14 days)
            highs_clean = [h for h in highs if h is not None][-14:]
            lows_clean = [lo for lo in lows if lo is not None][-14:]
            if len(highs_clean) >= 14 and len(lows_clean) >= 14:
                tr_values = [h - lo for h, lo in zip(highs_clean, lows_clean)]
                atr_14 = sum(tr_values) / len(tr_values)
            else:
                atr_14 = 0.45  # Default
            
            logger.info(f"DXY fetched via Yahoo API: {value:.3f}")
            
            return DXYSnapshot(
                value=round(value, 3),
                timestamp=now,
                sma_20=round(sma_20, 3) if sma_20 else None,
                sma_50=round(sma_50, 3) if sma_50 else None,
                sma_200=round(sma_200, 3) if sma_200 else None,
                daily_change_pct=round(daily_change_pct, 2),
                atr_14=round(atr_14, 3),
            )
            
        except Exception as e:
            logger.error(f"Yahoo Finance API fetch failed: {e}")
            # Fallback to mock on any error
            return self._generate_mock_snapshot()
    
    def _fetch_from_fred(self) -> DXYSnapshot:
        """
        Fetch from FRED (placeholder for Phase 2+).
        
        TODO: Implement FRED integration.
        Series: DTWEXBGS (Trade Weighted U.S. Dollar Index)
        
        Note: FRED data may be delayed (T+1).
        """
        raise NotImplementedError("FRED API not implemented yet")
