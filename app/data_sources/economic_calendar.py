"""
Economic Calendar Fetcher.

Phase 1: Fetches high-impact economic events for MacroAgent.
Source: Forex Factory (via web scraping) or Investing.com API.

Note: In production, consider using a paid API for reliability.
This implementation uses in-memory mock data for testing.
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.data_sources.base_source import BaseDataSource
from app.models.economic_event import EconomicEvent, EventImpact
from app.models.source_health import SourceHealth


logger = logging.getLogger(__name__)


class EconomicCalendarFetcher(BaseDataSource):
    """
    Fetches economic calendar events.
    
    Phase 1: Uses mock data. Phase 2+ will integrate real API.
    
    Expected API sources (future):
    - Forex Factory (scraping)
    - Investing.com (API)
    - Trading Economics (paid API)
    """
    
    source_name = "economic_calendar"
    
    def __init__(self, mock_mode: bool = True):
        """
        Initialize fetcher.
        
        Args:
            mock_mode: If True, return mock data. If False, call real API.
        """
        self.mock_mode = mock_mode
        self._last_fetch: Optional[datetime] = None
        self._last_data: List[EconomicEvent] = []
        self._last_error: Optional[str] = None
        self._coverage: float = 1.0
    
    def fetch(self, days_ahead: int = 7) -> List[EconomicEvent]:
        """
        Fetch economic events for the next N days.
        
        Args:
            days_ahead: How many days ahead to fetch.
        
        Returns:
            List of EconomicEvent objects.
        """
        try:
            if self.mock_mode:
                events = self._generate_mock_events(days_ahead)
            else:
                events = self._fetch_from_api(days_ahead)
            
            self._last_fetch = datetime.now(timezone.utc)
            self._last_data = events
            self._last_error = None
            self._coverage = 1.0
            
            logger.info(f"EconomicCalendarFetcher: fetched {len(events)} events")
            return events
            
        except Exception as e:
            self._last_error = str(e)
            self._coverage = 0.0
            logger.error(f"EconomicCalendarFetcher error: {e}")
            return self._last_data  # Return cached if available
    
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
    
    def get_upcoming_high_impact(
        self, 
        currencies: List[str], 
        hours_ahead: float = 24.0
    ) -> List[EconomicEvent]:
        """
        Get upcoming high-impact events for specific currencies.
        
        Args:
            currencies: List of currency codes (USD, EUR, etc.)
            hours_ahead: How many hours ahead to look.
        
        Returns:
            Filtered list of high-impact events.
        """
        return [
            e for e in self._last_data
            if e.currency in currencies
            and e.impact == EventImpact.HIGH
            and e.is_upcoming(hours_ahead)
        ]
    
    def get_recent_surprises(self, currencies: List[str]) -> List[EconomicEvent]:
        """
        Get recent events that had surprise results.
        
        Args:
            currencies: List of currency codes.
        
        Returns:
            List of past events with actual != forecast.
        """
        return [
            e for e in self._last_data
            if e.currency in currencies
            and e.is_past()
            and e.has_surprise()
        ]
    
    def _generate_mock_events(self, days_ahead: int) -> List[EconomicEvent]:
        """Generate mock events for testing."""
        now = datetime.now(timezone.utc)
        events = []
        
        # Define some typical events
        event_templates = [
            ("USD", "Non-Farm Payrolls", EventImpact.HIGH),
            ("USD", "FOMC Statement", EventImpact.HIGH),
            ("USD", "Core CPI m/m", EventImpact.HIGH),
            ("EUR", "ECB Press Conference", EventImpact.HIGH),
            ("EUR", "German ZEW Survey", EventImpact.MEDIUM),
            ("GBP", "BOE Rate Decision", EventImpact.HIGH),
            ("GBP", "UK CPI y/y", EventImpact.HIGH),
            ("JPY", "BOJ Rate Decision", EventImpact.HIGH),
            ("AUD", "RBA Rate Decision", EventImpact.HIGH),
            ("CAD", "BOC Rate Decision", EventImpact.HIGH),
            ("CHF", "SNB Rate Decision", EventImpact.HIGH),
            ("NZD", "RBNZ Rate Decision", EventImpact.HIGH),
            ("USD", "Initial Jobless Claims", EventImpact.MEDIUM),
            ("USD", "Retail Sales m/m", EventImpact.MEDIUM),
            ("EUR", "Flash PMI", EventImpact.MEDIUM),
        ]
        
        # Distribute events across the week
        for i in range(days_ahead * 3):  # ~3 events per day
            template = event_templates[i % len(event_templates)]
            currency, name, impact = template
            
            # Randomish but deterministic timing
            day_offset = i // 3
            hour = (9 + (i * 7) % 12)  # 9am-8pm
            scheduled = now + timedelta(days=day_offset, hours=hour - now.hour)
            
            # Generate deterministic event_id
            event_id = hashlib.md5(f"{name}:{scheduled.date()}".encode()).hexdigest()[:12]
            
            events.append(EconomicEvent(
                event_id=event_id,
                event_name=name,
                currency=currency,
                scheduled_time=scheduled,
                impact=impact,
                forecast="0.2%" if "CPI" in name or "Retail" in name else None,
                previous="0.1%" if "CPI" in name or "Retail" in name else None,
            ))
        
        return sorted(events, key=lambda e: e.scheduled_time)
    
    def _fetch_from_api(self, days_ahead: int) -> List[EconomicEvent]:
        """
        Fetch from real API (placeholder for Phase 2+).
        
        TODO: Implement real API integration.
        Options:
        - Forex Factory scraping (fragile)
        - Investing.com API
        - Trading Economics (paid)
        """
        raise NotImplementedError("Real API not implemented yet. Use mock_mode=True")
