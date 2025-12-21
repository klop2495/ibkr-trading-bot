"""
Data Sources package for Phase 1.

Contains fetchers for external data:
- EconomicCalendarFetcher: High-impact events from Forex Factory
- COTReportsFetcher: CFTC positioning data
- DXYFetcher: Dollar index from FRED/Yahoo

Each fetcher implements BaseDataSource interface and provides health status.
"""

from app.data_sources.base_source import BaseDataSource
from app.data_sources.economic_calendar import EconomicCalendarFetcher
from app.data_sources.cot_reports import COTReportsFetcher
from app.data_sources.dxy_index import DXYFetcher

__all__ = [
    "BaseDataSource",
    "EconomicCalendarFetcher",
    "COTReportsFetcher",
    "DXYFetcher",
]
