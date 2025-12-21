"""
Base data source interface.

Phase 1: All data sources implement this interface for consistent health monitoring.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

from app.models.source_health import SourceHealth


class BaseDataSource(ABC):
    """
    Base class for all external data sources.
    
    Each source must implement:
    - fetch(): Get latest data
    - get_health(): Return SourceHealth status
    - source_name: Unique identifier
    """
    
    source_name: str = "base"
    
    # Subclasses should set these
    _last_fetch: Optional[datetime] = None
    _last_data: Optional[Any] = None
    _last_error: Optional[str] = None
    
    @abstractmethod
    def fetch(self) -> Any:
        """
        Fetch latest data from source.
        
        Should update _last_fetch, _last_data, _last_error.
        
        Returns:
            The fetched data (type depends on implementation).
        """
        pass
    
    @abstractmethod
    def get_health(self) -> SourceHealth:
        """
        Get current health status of this source.
        
        Returns:
            SourceHealth with availability, staleness, and coverage info.
        """
        pass
    
    def fetch_if_stale(self, max_age_minutes: float) -> Any:
        """
        Fetch only if cached data is stale.
        
        Args:
            max_age_minutes: Maximum acceptable age of cached data.
        
        Returns:
            Cached data if fresh, or newly fetched data.
        """
        health = self.get_health()
        if health.is_stale(max_age_minutes) or self._last_data is None:
            return self.fetch()
        return self._last_data
