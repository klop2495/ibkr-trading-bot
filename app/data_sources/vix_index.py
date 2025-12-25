"""
VIX (Fear Index) Fetcher Module.

Source: Uses yfinance (same as DXY fetcher)
Provides market fear/greed data for RiskAgent.

Integration: Used by RiskAgent for risk assessment.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import yfinance
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance not installed. VIX fetcher disabled. Run: pip install yfinance")


@dataclass
class VIXSnapshot:
    """VIX data snapshot"""
    value: float
    regime: str  # EXTREME_GREED, GREED, FEAR, EXTREME_FEAR
    risk_multiplier: float  # 0.4 to 1.2
    timestamp: datetime
    is_mock: bool = False


class VIXFetcher:
    """
    Fetch VIX (CBOE Volatility Index) data.
    
    VIX Interpretation:
    - < 12: Extreme greed (complacency)
    - 12-20: Greed (normal bull market)
    - 20-30: Fear (elevated uncertainty)
    - > 30: Extreme fear (panic)
    
    Usage in RiskAgent:
    ```python
    fetcher = VIXFetcher()
    vix = fetcher.get_snapshot()
    if vix.regime == "EXTREME_FEAR":
        # Reduce risk exposure
    ```
    """
    
    VIX_SYMBOL = "^VIX"
    
    # VIX thresholds
    EXTREME_GREED_THRESHOLD = 12
    GREED_THRESHOLD = 20
    FEAR_THRESHOLD = 30
    
    def __init__(self):
        if not YFINANCE_AVAILABLE:
            logger.warning("VIXFetcher initialized without yfinance")
        self._last_snapshot: Optional[VIXSnapshot] = None
        self._last_fetch: Optional[datetime] = None
        self._cache_minutes = 5  # Cache for 5 minutes
    
    def get_snapshot(self, use_cache: bool = True) -> VIXSnapshot:
        """
        Get current VIX snapshot.
        
        Args:
            use_cache: Use cached value if fresh enough
            
        Returns:
            VIXSnapshot with current data or mock
        """
        # Check cache
        if use_cache and self._is_cache_valid():
            return self._last_snapshot
        
        if not YFINANCE_AVAILABLE:
            return self._mock_snapshot()
        
        try:
            ticker = yf.Ticker(self.VIX_SYMBOL)
            data = ticker.history(period="1d")
            
            if data.empty:
                logger.warning("VIX data empty, using mock")
                return self._mock_snapshot()
            
            vix_value = float(data['Close'].iloc[-1])
            
            snapshot = VIXSnapshot(
                value=round(vix_value, 2),
                regime=self._classify_regime(vix_value),
                risk_multiplier=self._calculate_risk_multiplier(vix_value),
                timestamp=datetime.now(timezone.utc),
                is_mock=False
            )
            
            # Update cache
            self._last_snapshot = snapshot
            self._last_fetch = datetime.now(timezone.utc)
            
            return snapshot
            
        except Exception as e:
            logger.error(f"VIX fetch failed: {e}")
            return self._mock_snapshot()
    
    def _is_cache_valid(self) -> bool:
        """Check if cached data is still valid."""
        if not self._last_snapshot or not self._last_fetch:
            return False
        
        age = (datetime.now(timezone.utc) - self._last_fetch).total_seconds()
        return age < self._cache_minutes * 60
    
    def _classify_regime(self, vix_value: float) -> str:
        """Classify VIX into regime category."""
        if vix_value < self.EXTREME_GREED_THRESHOLD:
            return "EXTREME_GREED"
        elif vix_value < self.GREED_THRESHOLD:
            return "GREED"
        elif vix_value < self.FEAR_THRESHOLD:
            return "FEAR"
        else:
            return "EXTREME_FEAR"
    
    def _calculate_risk_multiplier(self, vix_value: float) -> float:
        """
        Calculate risk adjustment multiplier.
        
        Lower multiplier = reduce risk exposure.
        
        Returns:
            Multiplier (0.4 to 1.2)
        """
        if vix_value < 15:
            return 1.2   # Low fear = can take more risk
        elif vix_value < 20:
            return 1.0   # Normal
        elif vix_value < 25:
            return 0.8   # Reduce slightly
        elif vix_value < 30:
            return 0.6   # Reduce more
        else:
            return 0.4   # Minimal risk
    
    def _mock_snapshot(self) -> VIXSnapshot:
        """Return mock snapshot when data unavailable."""
        return VIXSnapshot(
            value=18.0,  # Moderate value
            regime="GREED",
            risk_multiplier=1.0,
            timestamp=datetime.now(timezone.utc),
            is_mock=True
        )
    
    def format_for_context(self, snapshot: Optional[VIXSnapshot] = None) -> dict:
        """
        Format VIX data for agent context.
        
        Returns dict suitable for RiskAgent's prepare_input.
        """
        if snapshot is None:
            snapshot = self.get_snapshot()
        
        return {
            "vix_value": snapshot.value,
            "vix_regime": snapshot.regime,
            "vix_risk_multiplier": snapshot.risk_multiplier,
            "vix_is_mock": snapshot.is_mock,
            "vix_elevated": snapshot.regime in ("FEAR", "EXTREME_FEAR"),
        }
