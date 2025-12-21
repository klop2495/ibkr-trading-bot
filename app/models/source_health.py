"""
Source health model for monitoring data freshness.

Phase 1: Tracks availability, staleness, and coverage of each data source.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class SourceHealth:
    """
    Health status of a data source.
    
    Used by SourceHealthMonitor to track if data is fresh enough for trading.
    """
    source_name: str
    is_available: bool
    last_update: datetime
    staleness_minutes: float
    coverage: float = 1.0  # 0.0 - 1.0 (fraction of expected data present)
    error_message: Optional[str] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def is_stale(self, max_age_minutes: float) -> bool:
        """Check if data is older than acceptable threshold."""
        return self.staleness_minutes > max_age_minutes
    
    def is_healthy(self, max_age_minutes: float, min_coverage: float = 0.5) -> bool:
        """
        Check if source is healthy for trading.
        
        Args:
            max_age_minutes: Maximum acceptable staleness.
            min_coverage: Minimum acceptable data coverage (0.0-1.0).
        
        Returns:
            True if source is available, fresh, and has adequate coverage.
        """
        return (
            self.is_available 
            and not self.is_stale(max_age_minutes) 
            and self.coverage >= min_coverage
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "source_name": self.source_name,
            "is_available": self.is_available,
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "staleness_minutes": round(self.staleness_minutes, 2),
            "coverage": round(self.coverage, 2),
            "error_message": self.error_message,
            "checked_at": self.checked_at.isoformat(),
        }
    
    @classmethod
    def unavailable(cls, source_name: str, error: str) -> "SourceHealth":
        """Factory for unavailable sources."""
        now = datetime.now(timezone.utc)
        return cls(
            source_name=source_name,
            is_available=False,
            last_update=now,
            staleness_minutes=float("inf"),
            coverage=0.0,
            error_message=error,
            checked_at=now,
        )
    
    @classmethod
    def fresh(cls, source_name: str, last_update: datetime, coverage: float = 1.0) -> "SourceHealth":
        """Factory for fresh, healthy sources."""
        now = datetime.now(timezone.utc)
        staleness = (now - last_update).total_seconds() / 60.0
        return cls(
            source_name=source_name,
            is_available=True,
            last_update=last_update,
            staleness_minutes=staleness,
            coverage=coverage,
            error_message=None,
            checked_at=now,
        )
