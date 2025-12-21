"""
COT (Commitment of Traders) report model.

Phase 1: Represents CFTC positioning data for sentiment analysis.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class COTReport:
    """
    CFTC Commitment of Traders report data for a currency.
    
    Contains non-commercial (speculator) positioning data.
    Released weekly, typically Tuesday data released Friday.
    """
    symbol: str  # e.g., "EURUSD" or underlying like "EUR"
    report_date: datetime
    
    # Non-commercial (speculator) positions
    long_positions: int
    short_positions: int
    
    # Calculated fields
    net_position: int = 0
    
    # Change from previous week
    long_change: int = 0
    short_change: int = 0
    net_change: int = 0
    
    # Historical context
    percentile_52w: Optional[float] = None  # 0.0-1.0 (where current net is vs 52-week range)
    
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def __post_init__(self):
        """Calculate derived fields."""
        if self.net_position == 0:
            self.net_position = self.long_positions - self.short_positions
        if self.net_change == 0:
            self.net_change = self.long_change - self.short_change
    
    @property
    def bias(self) -> str:
        """Get directional bias based on net position."""
        if self.net_position > 0:
            return "LONG"
        elif self.net_position < 0:
            return "SHORT"
        return "NEUTRAL"
    
    @property
    def change_direction(self) -> str:
        """Get weekly change direction."""
        if self.net_change > 0:
            return "INCREASING"
        elif self.net_change < 0:
            return "DECREASING"
        return "FLAT"
    
    @property
    def percentile_bucket(self) -> str:
        """Get 52-week percentile as category."""
        if self.percentile_52w is None:
            return "UNKNOWN"
        if self.percentile_52w >= 0.8:
            return "EXTREME_HIGH"
        elif self.percentile_52w >= 0.6:
            return "HIGH"
        elif self.percentile_52w >= 0.4:
            return "MEDIUM"
        elif self.percentile_52w >= 0.2:
            return "LOW"
        return "EXTREME_LOW"
    
    def staleness_days(self) -> float:
        """Days since report date."""
        now = datetime.now(timezone.utc)
        delta = now - self.report_date
        return delta.total_seconds() / 86400.0
    
    def is_stale(self, max_days: float = 10.0) -> bool:
        """COT data is typically 3-5 days old when released, stale if > max_days."""
        return self.staleness_days() > max_days
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "symbol": self.symbol,
            "report_date": self.report_date.isoformat(),
            "long_positions": self.long_positions,
            "short_positions": self.short_positions,
            "net_position": self.net_position,
            "long_change": self.long_change,
            "short_change": self.short_change,
            "net_change": self.net_change,
            "bias": self.bias,
            "change_direction": self.change_direction,
            "percentile_52w": self.percentile_52w,
            "percentile_bucket": self.percentile_bucket,
            "staleness_days": round(self.staleness_days(), 1),
        }
    
    def to_agent_input(self) -> dict:
        """
        Convert to categorical format for LLM agents.
        
        Returns categories, NOT numbers (per v2.1 spec).
        """
        return {
            "symbol": self.symbol,
            "bias": self.bias,
            "weekly_change": self.change_direction,
            "percentile": self.percentile_bucket,
            "is_stale": self.is_stale(),
        }
