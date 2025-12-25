"""
Volatility Regime Detector Module.

Source: Adapted from github.com/MehranTaghian/DQN-Trading
Detects market volatility regime for position sizing.

Integration: Used by RiskAgent and PositionSizer.
"""

import logging
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


class VolatilityRegime(Enum):
    """Market volatility regime classification"""
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class VolatilityRegimeDetector:
    """
    Detect current volatility regime for adaptive trading.
    
    Usage:
    ```python
    detector = VolatilityRegimeDetector()
    regime = detector.detect(atr_values, close_prices)
    position_mult = detector.get_position_multiplier(regime)
    ```
    """
    
    def __init__(
        self,
        low_threshold: float = 0.5,
        high_threshold: float = 1.5,
        extreme_threshold: float = 2.5,
        lookback: int = 20
    ):
        """
        Initialize detector.
        
        Args:
            low_threshold: ATR ratio below this = LOW volatility
            high_threshold: ATR ratio above this = HIGH volatility
            extreme_threshold: ATR ratio above this = EXTREME volatility
            lookback: Periods to calculate average ATR
        """
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold
        self.extreme_threshold = extreme_threshold
        self.lookback = lookback
    
    def detect(
        self,
        atr_values: List[float],
        close_prices: List[float]
    ) -> VolatilityRegime:
        """
        Detect current volatility regime.
        
        Args:
            atr_values: List of ATR values
            close_prices: List of close prices
            
        Returns:
            VolatilityRegime classification
        """
        if len(atr_values) < self.lookback or len(close_prices) < self.lookback:
            return VolatilityRegime.NORMAL
        
        # Normalize ATR as percentage of price
        current_atr = atr_values[-1]
        current_close = close_prices[-1]
        
        if current_close <= 0:
            return VolatilityRegime.NORMAL
        
        normalized_current = (current_atr / current_close) * 100
        
        # Calculate historical average
        historical_normalized = []
        for i in range(-self.lookback, 0):
            if close_prices[i] > 0:
                normalized = (atr_values[i] / close_prices[i]) * 100
                historical_normalized.append(normalized)
        
        if not historical_normalized:
            return VolatilityRegime.NORMAL
        
        avg_normalized = sum(historical_normalized) / len(historical_normalized)
        
        # Calculate ratio
        if avg_normalized <= 0:
            return VolatilityRegime.NORMAL
        
        ratio = normalized_current / avg_normalized
        
        # Classify
        if ratio >= self.extreme_threshold:
            return VolatilityRegime.EXTREME
        elif ratio >= self.high_threshold:
            return VolatilityRegime.HIGH
        elif ratio <= self.low_threshold:
            return VolatilityRegime.LOW
        else:
            return VolatilityRegime.NORMAL
    
    def detect_from_atr_only(
        self,
        current_atr: float,
        average_atr: float
    ) -> VolatilityRegime:
        """
        Quick detection using pre-calculated ATR values.
        
        Args:
            current_atr: Current ATR value
            average_atr: Average ATR over lookback period
            
        Returns:
            VolatilityRegime classification
        """
        if average_atr <= 0:
            return VolatilityRegime.NORMAL
        
        ratio = current_atr / average_atr
        
        if ratio >= self.extreme_threshold:
            return VolatilityRegime.EXTREME
        elif ratio >= self.high_threshold:
            return VolatilityRegime.HIGH
        elif ratio <= self.low_threshold:
            return VolatilityRegime.LOW
        else:
            return VolatilityRegime.NORMAL
    
    def get_position_multiplier(self, regime: VolatilityRegime) -> float:
        """
        Get position size multiplier based on regime.
        
        Lower multiplier = smaller position in high volatility.
        
        Returns:
            Multiplier (0.3 to 1.2)
        """
        return {
            VolatilityRegime.LOW: 1.2,      # Can take larger positions
            VolatilityRegime.NORMAL: 1.0,   # Standard size
            VolatilityRegime.HIGH: 0.6,     # Reduce position
            VolatilityRegime.EXTREME: 0.3   # Minimal position
        }[regime]
    
    def get_sl_multiplier(self, regime: VolatilityRegime) -> float:
        """
        Get stop-loss multiplier (wider stops in high volatility).
        
        Returns:
            Multiplier (0.8 to 1.8)
        """
        return {
            VolatilityRegime.LOW: 0.8,
            VolatilityRegime.NORMAL: 1.0,
            VolatilityRegime.HIGH: 1.3,
            VolatilityRegime.EXTREME: 1.8
        }[regime]
    
    def get_tp_multiplier(self, regime: VolatilityRegime) -> float:
        """
        Get take-profit multiplier (larger targets possible in high volatility).
        
        Returns:
            Multiplier (0.9 to 1.5)
        """
        return {
            VolatilityRegime.LOW: 0.9,
            VolatilityRegime.NORMAL: 1.0,
            VolatilityRegime.HIGH: 1.2,
            VolatilityRegime.EXTREME: 1.5
        }[regime]
    
    def should_trade(self, regime: VolatilityRegime) -> bool:
        """
        Check if trading is recommended in current regime.
        
        Returns False for EXTREME volatility.
        """
        return regime != VolatilityRegime.EXTREME
    
    def format_for_context(
        self,
        regime: VolatilityRegime,
        current_atr: Optional[float] = None,
        average_atr: Optional[float] = None
    ) -> dict:
        """
        Format regime data for agent context.
        
        Returns dict suitable for RiskAgent's prepare_input.
        """
        return {
            "volatility_regime": regime.value,
            "volatility_regime_tradeable": self.should_trade(regime),
            "position_multiplier": self.get_position_multiplier(regime),
            "sl_multiplier": self.get_sl_multiplier(regime),
            "atr_current": current_atr,
            "atr_average": average_atr,
        }
