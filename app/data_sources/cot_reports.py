"""
COT Reports Fetcher.

Phase 2: Fetches CFTC Commitment of Traders data for SentimentAgent.
Source: CFTC.gov direct CSV download.

Note: COT data is released weekly (Friday ~3:30pm ET) for Tuesday's positions.
Data is typically 3-5 days old when released.

CFTC Data URLs:
- Current year: https://www.cftc.gov/dea/newcot/deacmesf.txt
- Historical: https://www.cftc.gov/files/dea/history/deacmesf{YEAR}.zip
"""

import csv
import io
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from app.data_sources.base_source import BaseDataSource
from app.models.cot_report import COTReport
from app.models.source_health import SourceHealth


logger = logging.getLogger(__name__)


# CFTC contract codes for Forex futures (CME)
# Format: (Market Name in CFTC file, CFTC Contract Code)
FOREX_COT_CONTRACTS = {
    "EUR": ("EURO FX", "099741"),
    "GBP": ("BRITISH POUND", "096742"),
    "JPY": ("JAPANESE YEN", "097741"),
    "AUD": ("AUSTRALIAN DOLLAR", "232741"),
    "CAD": ("CANADIAN DOLLAR", "090741"),
    "CHF": ("SWISS FRANC", "092741"),
    "NZD": ("NZ DOLLAR", "112741"),
    "MXN": ("MEXICAN PESO", "095741"),
}

# CFTC CME Futures Only (Short Format) URL
CFTC_URL = "https://www.cftc.gov/dea/newcot/deacmesf.txt"


class COTReportsFetcher(BaseDataSource):
    """
    Fetches CFTC Commitment of Traders reports from CFTC.gov.
    
    Downloads weekly COT data directly from CFTC website.
    Data is released every Friday at 3:30pm ET.
    
    Uses "Futures Only" short format for CME currencies.
    """
    
    source_name = "cot_reports"
    
    def __init__(self, mock_mode: bool = False):
        """
        Initialize fetcher.
        
        Args:
            mock_mode: If True, return mock data instead of fetching.
        """
        self.mock_mode = mock_mode
        self._last_fetch: Optional[datetime] = None
        self._last_data: Dict[str, COTReport] = {}
        self._last_error: Optional[str] = None
        self._coverage: float = 1.0
        self._raw_data: List[Dict] = []  # Cached parsed CSV data
    
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
    
    def _fetch_from_cftc(self, currencies: List[str]) -> Dict[str, COTReport]:
        """
        Fetch real COT data from CFTC.gov.
        
        Downloads the current year's CME Futures Only Short Format file.
        Parses CSV and extracts forex currency data.
        """
        reports = {}
        
        # Download COT data
        try:
            logger.info(f"COT: Downloading from {CFTC_URL}")
            response = requests.get(
                CFTC_URL,
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)"}
            )
            response.raise_for_status()
            
            # Parse CSV
            content = response.text
            self._raw_data = self._parse_cftc_csv(content)
            
            logger.info(f"COT: Parsed {len(self._raw_data)} rows from CFTC")
            
        except Exception as e:
            logger.error(f"COT: Failed to download from CFTC: {e}")
            # Try to use cached data
            if self._raw_data:
                logger.info("COT: Using cached data")
            else:
                raise RuntimeError(f"Failed to fetch COT data: {e}")
        
        # Extract data for each currency
        for currency in currencies:
            if currency not in FOREX_COT_CONTRACTS:
                continue
            
            market_name, contract_code = FOREX_COT_CONTRACTS[currency]
            
            # Find the latest report for this currency
            report = self._extract_currency_report(currency, market_name, contract_code)
            if report:
                reports[currency] = report
        
        return reports
    
    def _parse_cftc_csv(self, content: str) -> List[Dict]:
        """
        Parse CFTC CSV content.
        
        The CFTC short format columns vary but we need:
        - Market_and_Exchange_Names
        - As_of_Date_In_Form_YYMMDD
        - Asset Manager and Leveraged Money positions (speculators)
        """
        rows = []
        
        # Use csv reader
        reader = csv.reader(io.StringIO(content))
        
        # Read header row
        header = next(reader, None)
        if not header:
            return rows
        
        # Create column name mapping (strip whitespace)
        col_map = {name.strip(): idx for idx, name in enumerate(header)}
        
        # Log available columns for debugging
        logger.debug(f"COT columns: {list(col_map.keys())[:20]}...")
        
        for row in reader:
            if len(row) < 10:
                continue
            
            try:
                # Get market name and date
                market_idx = col_map.get("Market_and_Exchange_Names", 0)
                date_idx = col_map.get("As_of_Date_In_Form_YYMMDD", 1)
                
                market_name = row[market_idx].strip() if market_idx < len(row) else ""
                date_str = row[date_idx].strip() if date_idx < len(row) else ""
                
                # Try different column name patterns for positions
                # Pattern 1: Asset_Mgr_Positions_Long_All
                # Pattern 2: Pct_of_OI_Asset_Mgr_Long_All
                
                asset_long = self._get_col_value(row, col_map, [
                    "Asset_Mgr_Positions_Long_All",
                    "AssetMgr_Positions_Long_All", 
                    "Dealer_Positions_Long_All"
                ])
                asset_short = self._get_col_value(row, col_map, [
                    "Asset_Mgr_Positions_Short_All",
                    "AssetMgr_Positions_Short_All",
                    "Dealer_Positions_Short_All"
                ])
                lev_long = self._get_col_value(row, col_map, [
                    "Lev_Money_Positions_Long_All",
                    "LevMoney_Positions_Long_All"
                ])
                lev_short = self._get_col_value(row, col_map, [
                    "Lev_Money_Positions_Short_All",
                    "LevMoney_Positions_Short_All"
                ])
                
                # Combined speculator positions
                spec_long = asset_long + lev_long
                spec_short = asset_short + lev_short
                
                # Parse date (YYMMDD format)
                try:
                    report_date = datetime.strptime(date_str, "%y%m%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    # Try alternative format YYYY-MM-DD
                    try:
                        report_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        report_date = datetime.now(timezone.utc)
                
                rows.append({
                    "market_name": market_name,
                    "report_date": report_date,
                    "spec_long": spec_long,
                    "spec_short": spec_short,
                })
                
            except (IndexError, ValueError) as e:
                logger.debug(f"COT: Failed to parse row: {e}")
                continue
        
        return rows
    
    def _get_col_value(self, row: List[str], col_map: Dict[str, int], col_names: List[str]) -> int:
        """Try multiple column names and return first valid value."""
        for name in col_names:
            idx = col_map.get(name, -1)
            if idx >= 0 and idx < len(row):
                val = self._safe_int(row[idx])
                if val != 0:
                    return val
        return 0
    
    def _safe_int(self, value: str) -> int:
        """Safely convert string to int."""
        try:
            return int(value.strip().replace(",", ""))
        except (ValueError, AttributeError):
            return 0
    
    def _extract_currency_report(
        self,
        currency: str,
        market_name: str,
        contract_code: str,
    ) -> Optional[COTReport]:
        """
        Extract COT report for a specific currency.
        
        Finds the latest report matching the market name.
        """
        # Find all rows matching this currency
        matching_rows = [
            row for row in self._raw_data
            if market_name.upper() in row["market_name"].upper()
        ]
        
        if not matching_rows:
            logger.warning(f"COT: No data found for {currency} ({market_name})")
            return None
        
        # Sort by date descending to get latest
        matching_rows.sort(key=lambda x: x["report_date"], reverse=True)
        
        # Get latest and previous for change calculation
        latest = matching_rows[0]
        previous = matching_rows[1] if len(matching_rows) > 1 else None
        
        # Calculate net position
        net_position = latest["spec_long"] - latest["spec_short"]
        
        # Calculate changes from previous week
        long_change = 0
        short_change = 0
        if previous:
            long_change = latest["spec_long"] - previous["spec_long"]
            short_change = latest["spec_short"] - previous["spec_short"]
        
        # Calculate 52-week percentile
        percentile = self._calculate_percentile(currency, net_position, matching_rows)
        
        logger.info(f"COT {currency}: long={latest['spec_long']} short={latest['spec_short']} net={net_position} pct={percentile}")
        
        return COTReport(
            symbol=currency,
            report_date=latest["report_date"],
            long_positions=latest["spec_long"],
            short_positions=latest["spec_short"],
            net_position=net_position,
            long_change=long_change,
            short_change=short_change,
            percentile_52w=percentile,
        )
    
    def _calculate_percentile(
        self,
        currency: str,
        current_net: int,
        historical_rows: List[Dict],
    ) -> Optional[float]:
        """
        Calculate 52-week percentile for current net position.
        
        Returns 0.0 (extreme low) to 1.0 (extreme high).
        """
        # Get net positions from last 52 weeks (approximately)
        net_positions = [
            row["spec_long"] - row["spec_short"]
            for row in historical_rows[:52]  # Last 52 reports
        ]
        
        if len(net_positions) < 4:  # Need at least a month of data
            return None
        
        # Calculate percentile
        min_net = min(net_positions)
        max_net = max(net_positions)
        
        if max_net == min_net:
            return 0.5  # All same value
        
        percentile = (current_net - min_net) / (max_net - min_net)
        return round(max(0.0, min(1.0, percentile)), 2)
    
    def _generate_mock_reports(self, currencies: List[str]) -> Dict[str, COTReport]:
        """Generate mock COT reports for testing."""
        now = datetime.now(timezone.utc)
        
        # Mock report date (previous Tuesday)
        days_since_tuesday = (now.weekday() - 1) % 7
        report_date = now - timedelta(days=days_since_tuesday)
        report_date = report_date.replace(hour=0, minute=0, second=0, microsecond=0)
        
        reports = {}
        
        # Mock data with realistic values
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
