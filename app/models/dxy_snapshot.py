"""
DXY (Dollar Index) snapshot model.

Phase 1: Represents US Dollar Index data for correlation analysis.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class DXYSnapshot:
    """
    US Dollar Index snapshot.
    
    Used by CorrelationAgent to assess USD strength.
    DXY is inversely correlated with EURUSD, GBPUSD, etc.
    """
    value: float  # Current DXY value (typically 90-110)
    timestamp: datetime
    
    # Moving averages for trend
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    
    # Daily change
    daily_change_pct: Optional[float] = None  # e.g., 0.5 for +0.5%
    
    # Volatility
    atr_14: Optional[float] = None  # 14-day ATR
    
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def trend(self) -> str:
        """Get trend direction based on MAs."""
        if self.sma_20 is None or self.sma_50 is None:
            return "UNKNOWN"
        
        if self.value > self.sma_20 > self.sma_50:
            return "UP"
        elif self.value < self.sma_20 < self.sma_50:
            return "DOWN"
        return "NEUTRAL"
    
    @property
    def vs_sma(self) -> str:
        """Position relative to key MA."""
        if self.sma_50 is None:
            return "UNKNOWN"
        
        pct_diff = (self.value - self.sma_50) / self.sma_50 * 100
        
        if pct_diff > 1.0:
            return "ABOVE"
        elif pct_diff < -1.0:
            return "BELOW"
        return "AT"
    
    @property
    def daily_direction(self) -> str:
        """Daily price direction."""
        if self.daily_change_pct is None:
            return "UNKNOWN"
        
        if self.daily_change_pct > 0.2:
            return "UP"
        elif self.daily_change_pct < -0.2:
            return "DOWN"
        return "FLAT"
    
    @property
    def volatility_level(self) -> str:
        """Volatility regime based on ATR."""
        if self.atr_14 is None:
            return "UNKNOWN"
        
        # Typical DXY ATR is 0.3-0.8
        if self.atr_14 > 0.8:
            return "HIGH"
        elif self.atr_14 < 0.3:
            return "LOW"
        return "NORMAL"
    
    def staleness_minutes(self) -> float:
        """Minutes since data was fetched."""
        now = datetime.now(timezone.utc)
        delta = now - self.timestamp
        return delta.total_seconds() / 60.0
    
    def is_stale(self, max_minutes: float = 30.0) -> bool:
        """Check if data is too old."""
        return self.staleness_minutes() > max_minutes
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "value": round(self.value, 3),
            "timestamp": self.timestamp.isoformat(),
            "sma_20": round(self.sma_20, 3) if self.sma_20 else None,
            "sma_50": round(self.sma_50, 3) if self.sma_50 else None,
            "sma_200": round(self.sma_200, 3) if self.sma_200 else None,
            "daily_change_pct": round(self.daily_change_pct, 2) if self.daily_change_pct else None,
            "trend": self.trend,
            "vs_sma": self.vs_sma,
            "volatility_level": self.volatility_level,
        }
    
    def to_agent_input(self) -> dict:
        """
        Convert to categorical format for LLM agents.
        
        Returns categories, NOT numbers (per v2.1 spec).
        But includes 'value' for data_status checks.
        """
        return {
            "value": self.value,  # For data availability check
            "trend": self.trend,
            "vs_50sma": self.vs_sma,
            "daily_direction": self.daily_direction,
            "volatility": self.volatility_level,
            "is_stale": self.is_stale(),
        }
