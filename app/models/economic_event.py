"""
Economic event model for calendar data.

Phase 1: Represents high-impact economic events from Forex Factory or similar sources.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class EventImpact(str, Enum):
    """Impact level of economic event."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class EconomicEvent:
    """
    A single economic calendar event.
    
    Examples: NFP, FOMC, GDP, CPI, etc.
    """
    event_id: str
    event_name: str
    currency: str  # USD, EUR, GBP, etc.
    scheduled_time: datetime
    impact: EventImpact
    actual: Optional[str] = None  # May be string like "3.5%"
    forecast: Optional[str] = None
    previous: Optional[str] = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def hours_until(self) -> float:
        """Hours until event (negative if past)."""
        now = datetime.now(timezone.utc)
        delta = self.scheduled_time - now
        return delta.total_seconds() / 3600.0
    
    def is_upcoming(self, hours_ahead: float = 24.0) -> bool:
        """Check if event is within specified hours."""
        h = self.hours_until()
        return 0 <= h <= hours_ahead
    
    def is_past(self) -> bool:
        """Check if event has already occurred."""
        return self.hours_until() < 0
    
    def has_surprise(self) -> bool:
        """Check if actual differs significantly from forecast."""
        if not self.actual or not self.forecast:
            return False
        try:
            # Try to parse as numbers
            actual_val = float(self.actual.replace("%", "").replace("K", "000").replace("M", "000000"))
            forecast_val = float(self.forecast.replace("%", "").replace("K", "000").replace("M", "000000"))
            # Surprise if > 10% difference
            if forecast_val != 0:
                return abs((actual_val - forecast_val) / forecast_val) > 0.10
        except (ValueError, TypeError):
            pass
        return self.actual != self.forecast
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "currency": self.currency,
            "scheduled_time": self.scheduled_time.isoformat(),
            "impact": self.impact.value,
            "actual": self.actual,
            "forecast": self.forecast,
            "previous": self.previous,
            "hours_until": round(self.hours_until(), 2),
            "is_upcoming": self.is_upcoming(),
        }
    
    def to_agent_input(self) -> dict:
        """
        Convert to categorical format for LLM agents.
        
        Returns categories, NOT numbers (per v2.1 spec).
        """
        hours = self.hours_until()
        
        if hours < 0:
            time_bucket = "PAST"
        elif hours < 1:
            time_bucket = "IMMINENT"  # < 1 hour
        elif hours < 6:
            time_bucket = "SOON"  # 1-6 hours
        elif hours < 24:
            time_bucket = "TODAY"  # 6-24 hours
        else:
            time_bucket = "FUTURE"  # > 24 hours
        
        return {
            "event": self.event_name,
            "currency": self.currency,
            "impact": self.impact.value.upper(),
            "timing": time_bucket,
            "has_surprise": self.has_surprise() if self.is_past() else None,
        }
