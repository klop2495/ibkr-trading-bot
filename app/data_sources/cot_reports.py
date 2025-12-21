"""
COT Reports Fetcher.

Phase 1: Fetches CFTC Commitment of Traders data for SentimentAgent.
Source: CFTC (via quandl or direct download).

Note: COT data is released weekly (Friday) for Tuesday's positions.
Data is typically 3-5 days old when released.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.data_sources.base_source import BaseDataSource
from app.models.cot_report import COTReport
from app.models.source_health import SourceHealth


logger = logging.getLogger(__name__)


# CFTC contract mapping for Forex futures
FOREX_COT_CONTRACTS = {
    "EUR": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
    "GBP": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE",
    "JPY": "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
    "AUD": "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "CAD": "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "CHF": "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE",
    "NZD": "NEW ZEALAND DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "MXN": "MEXICAN PESO - CHICAGO MERCANTILE EXCHANGE",
}


class COTReportsFetcher(BaseDataSource):
    """
    Fetches CFTC Commitment of Traders reports.
    
    Phase 1: Uses mock data. Phase 2+ will integrate real CFTC data.
    
    Data sources (future):
    - CFTC.gov (direct download, CSV)
    - Nasdaq Data Link (formerly Quandl)
    - TradingView (scraping)
    """
    
    source_name = "cot_reports"
    
    def __init__(self, mock_mode: bool = True):
        """
        Initialize fetcher.
        
        Args:
            mock_mode: If True, return mock data.
        """
        self.mock_mode = mock_mode
        self._last_fetch: Optional[datetime] = None
        self._last_data: Dict[str, COTReport] = {}
        self._last_error: Optional[str] = None
        self._coverage: float = 1.0
    
    def fetch(self, currencies: Optional[List[str]] = None) -> Dict[str, COTReport]:
        """
        Fetch COT reports for specified currencies.
        
        Args:
            currencies: List of currency codes (EUR, GBP, etc.).
                       If None, fetches all available.
        
        Returns:
            Dict mapping currency code to COTReport.
        """
        if currencies is None:
            currencies = list(FOREX_COT_CONTRACTS.keys())
        
        try:
            if self.mock_mode:
                reports = self._generate_mock_reports(currencies)
            else:
                reports = self._fetch_from_cftc(currencies)
            
            self._last_fetch = datetime.now(timezone.utc)
            self._last_data = reports
            self._last_error = None
            self._coverage = len(reports) / len(currencies) if currencies else 1.0
            
            logger.info(f"COTReportsFetcher: fetched {len(reports)} reports")
            return reports
            
        except Exception as e:
            self._last_error = str(e)
            self._coverage = 0.0
            logger.error(f"COTReportsFetcher error: {e}")
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
                coverage=self._coverage,
                error_message=self._last_error,
            )
        
        return SourceHealth.fresh(
            self.source_name,
            self._last_fetch,
            coverage=self._coverage,
        )
    
    def get_for_symbol(self, symbol: str) -> Optional[COTReport]:
        """
        Get COT report for a forex symbol.
        
        Args:
            symbol: Forex pair like "EURUSD"
        
        Returns:
            COTReport for the base currency, or None.
        """
        if len(symbol) >= 3:
            base = symbol[:3].upper()
            return self._last_data.get(base)
        return None
    
    def _generate_mock_reports(self, currencies: List[str]) -> Dict[str, COTReport]:
        """Generate mock COT reports for testing."""
        now = datetime.now(timezone.utc)
        
        # Mock report date (previous Tuesday)
        days_since_tuesday = (now.weekday() - 1) % 7
        report_date = now - timedelta(days=days_since_tuesday)
        report_date = report_date.replace(hour=0, minute=0, second=0, microsecond=0)
        
        reports = {}
        
        # Mock data with realistic-ish values
        mock_data = {
            "EUR": {"long": 185000, "short": 142000, "pct": 0.72},
            "GBP": {"long": 45000, "short": 62000, "pct": 0.35},
            "JPY": {"long": 28000, "short": 165000, "pct": 0.15},
            "AUD": {"long": 72000, "short": 88000, "pct": 0.42},
            "CAD": {"long": 38000, "short": 95000, "pct": 0.28},
            "CHF": {"long": 12000, "short": 28000, "pct": 0.45},
            "NZD": {"long": 18000, "short": 25000, "pct": 0.38},
        }
        
        for currency in currencies:
            if currency not in mock_data:
                continue
            
            data = mock_data[currency]
            
            # Add some weekly change
            long_change = int(data["long"] * 0.05)  # ±5% change
            short_change = int(data["short"] * 0.03)
            
            reports[currency] = COTReport(
                symbol=currency,
                report_date=report_date,
                long_positions=data["long"],
                short_positions=data["short"],
                long_change=long_change,
                short_change=-short_change,
                percentile_52w=data["pct"],
            )
        
        return reports
    
    def _fetch_from_cftc(self, currencies: List[str]) -> Dict[str, COTReport]:
        """
        Fetch from real CFTC data (placeholder for Phase 2+).
        
        TODO: Implement real CFTC data download.
        Options:
        - CFTC.gov CSV download (weekly)
        - Nasdaq Data Link (Quandl) API
        """
        raise NotImplementedError("Real CFTC API not implemented yet. Use mock_mode=True")
