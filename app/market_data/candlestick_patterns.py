"""
Candlestick Pattern Detection Module (Pure Python).

NO external dependencies - all patterns implemented manually.
Based on classic Japanese candlestick pattern definitions.

v2.0 Updates:
- ATR-based filtering (ignore patterns in low volatility)
- Configurable thresholds aligned with TA-Lib where possible
- Trend context validation

Integration: Used by TechnicalAgent to enhance signal generation.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class PatternType(Enum):
    """Type of candlestick pattern"""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class CandlestickPattern:
    """Detected candlestick pattern"""
    name: str
    pattern_type: PatternType
    strength: int  # 0-100 (TA-Lib compatible: 0, 100, or -100 mapped to 0-100)
    description: str
    candle_index: int = -1  # Which candle triggered the pattern


@dataclass 
class Candle:
    """Single candlestick data"""
    open: float
    high: float
    low: float
    close: float
    
    @property
    def body(self) -> float:
        """Absolute body size"""
        return abs(self.close - self.open)
    
    @property
    def range(self) -> float:
        """High - Low (full candle range)"""
        return self.high - self.low
    
    @property
    def body_pct(self) -> float:
        """Body as percentage of range"""
        if self.range == 0:
            return 0.0
        return self.body / self.range
    
    @property
    def upper_shadow(self) -> float:
        """Upper shadow (wick) size"""
        return self.high - max(self.open, self.close)
    
    @property
    def lower_shadow(self) -> float:
        """Lower shadow (tail) size"""
        return min(self.open, self.close) - self.low
    
    @property
    def upper_shadow_pct(self) -> float:
        """Upper shadow as percentage of range"""
        if self.range == 0:
            return 0.0
        return self.upper_shadow / self.range
    
    @property
    def lower_shadow_pct(self) -> float:
        """Lower shadow as percentage of range"""
        if self.range == 0:
            return 0.0
        return self.lower_shadow / self.range
    
    @property
    def is_bullish(self) -> bool:
        """Green candle (close > open)"""
        return self.close > self.open
    
    @property
    def is_bearish(self) -> bool:
        """Red candle (close < open)"""
        return self.close < self.open
    
    @property
    def midpoint(self) -> float:
        """Middle of the body"""
        return (self.open + self.close) / 2
    
    @property
    def body_top(self) -> float:
        """Top of the body"""
        return max(self.open, self.close)
    
    @property
    def body_bottom(self) -> float:
        """Bottom of the body"""
        return min(self.open, self.close)


@dataclass
class PatternConfig:
    """
    Configuration for pattern detection thresholds.
    
    Aligned with TA-Lib defaults where documented.
    See: https://github.com/TA-Lib/ta-lib/tree/main/src/ta_func
    """
    # Body size thresholds (as % of range)
    doji_body_pct: float = 0.05          # TA-Lib: ~5% body = doji
    small_body_pct: float = 0.25         # Small body for spinning top, stars
    large_body_pct: float = 0.60         # Large body for engulfing, soldiers
    
    # Shadow thresholds
    shadow_very_short_pct: float = 0.05  # Almost no shadow
    shadow_short_pct: float = 0.10       # Short shadow
    shadow_long_pct: float = 0.60        # Long shadow (hammer/shooting star)
    
    # Trend detection
    trend_lookback: int = 5              # Candles to check for trend
    trend_threshold_pct: float = 0.005   # 0.5% move = trend
    
    # ATR filter
    min_atr_multiplier: float = 0.3      # Pattern candle range must be > 0.3 * ATR
    
    # Gap detection (for star patterns)
    gap_threshold_pct: float = 0.001     # 0.1% gap


class CandlestickPatternDetector:
    """
    Pure Python candlestick pattern detector with ATR filtering.
    
    Thresholds aligned with TA-Lib where possible.
    
    Usage:
    ```python
    detector = CandlestickPatternDetector()
    
    # With ATR filter (recommended)
    patterns = detector.detect_all(opens, highs, lows, closes, atr=current_atr)
    
    # Without ATR filter
    patterns = detector.detect_all(opens, highs, lows, closes)
    
    # Get formatted output for agent
    context = detector.format_for_context(patterns)
    ```
    """
    
    def __init__(self, config: Optional[PatternConfig] = None):
        self.config = config or PatternConfig()
    
    def _to_candles(
        self,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float]
    ) -> List[Candle]:
        """Convert price lists to Candle objects"""
        n = min(len(opens), len(highs), len(lows), len(closes))
        return [
            Candle(open=opens[i], high=highs[i], low=lows[i], close=closes[i])
            for i in range(n)
        ]
    
    def _calculate_atr(self, candles: List[Candle], period: int = 14) -> float:
        """Calculate ATR for filtering"""
        if len(candles) < period + 1:
            return 0.0
        
        true_ranges = []
        for i in range(1, len(candles)):
            c = candles[i]
            prev_close = candles[i-1].close
            tr = max(
                c.high - c.low,
                abs(c.high - prev_close),
                abs(c.low - prev_close)
            )
            true_ranges.append(tr)
        
        if len(true_ranges) < period:
            return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0
        
        return sum(true_ranges[-period:]) / period
    
    def _detect_trend(self, candles: List[Candle], end_index: int) -> str:
        """
        Detect trend before a candle.
        
        Returns: "UP", "DOWN", or "NEUTRAL"
        """
        lookback = self.config.trend_lookback
        start_index = max(0, end_index - lookback)
        
        if end_index - start_index < 2:
            return "NEUTRAL"
        
        start_price = candles[start_index].close
        end_price = candles[end_index - 1].close  # Candle before pattern
        
        if start_price == 0:
            return "NEUTRAL"
        
        change_pct = (end_price - start_price) / start_price
        
        if change_pct > self.config.trend_threshold_pct:
            return "UP"
        elif change_pct < -self.config.trend_threshold_pct:
            return "DOWN"
        return "NEUTRAL"
    
    def _passes_atr_filter(self, candle: Candle, atr: Optional[float]) -> bool:
        """Check if candle is significant enough (not noise)"""
        if atr is None or atr <= 0:
            return True  # No filter if ATR not provided
        
        return candle.range >= atr * self.config.min_atr_multiplier
    
    def detect_all(
        self,
        open_prices: List[float],
        high_prices: List[float],
        low_prices: List[float],
        close_prices: List[float],
        atr: Optional[float] = None,
        lookback: int = 5
    ) -> List[CandlestickPattern]:
        """
        Detect all candlestick patterns on recent candles.
        
        Args:
            open_prices: List of open prices
            high_prices: List of high prices
            low_prices: List of low prices
            close_prices: List of close prices
            atr: Current ATR value for filtering (recommended)
            lookback: Number of recent candles to analyze
            
        Returns:
            List of detected CandlestickPattern objects
        """
        if len(open_prices) < 5:
            return []
        
        candles = self._to_candles(open_prices, high_prices, low_prices, close_prices)
        
        # Calculate ATR if not provided
        if atr is None:
            atr = self._calculate_atr(candles)
        
        patterns_found: List[CandlestickPattern] = []
        
        # Analyze last N candles
        start_idx = max(3, len(candles) - lookback)  # Need at least 3 previous candles
        
        for i in range(start_idx, len(candles)):
            c = candles[i]      # Current candle
            c1 = candles[i-1]   # Previous
            c2 = candles[i-2]   # 2 ago
            
            # ATR filter - skip insignificant candles
            if not self._passes_atr_filter(c, atr):
                continue
            
            # Get trend context
            trend = self._detect_trend(candles, i)
            
            # === Single Candle Patterns ===
            
            # Doji
            if self._is_doji(c):
                patterns_found.append(CandlestickPattern(
                    name="Doji",
                    pattern_type=PatternType.NEUTRAL,
                    strength=50,
                    description="Market indecision",
                    candle_index=i
                ))
            
            # Hammer (bullish, after downtrend)
            if self._is_hammer(c) and trend == "DOWN":
                patterns_found.append(CandlestickPattern(
                    name="Hammer",
                    pattern_type=PatternType.BULLISH,
                    strength=75,
                    description="Bullish reversal after downtrend",
                    candle_index=i
                ))
            
            # Hanging Man (bearish, after uptrend) - same shape as hammer
            if self._is_hammer(c) and trend == "UP":
                patterns_found.append(CandlestickPattern(
                    name="Hanging Man",
                    pattern_type=PatternType.BEARISH,
                    strength=70,
                    description="Bearish reversal after uptrend",
                    candle_index=i
                ))
            
            # Inverted Hammer (bullish, after downtrend)
            if self._is_inverted_hammer(c) and trend == "DOWN":
                patterns_found.append(CandlestickPattern(
                    name="Inverted Hammer",
                    pattern_type=PatternType.BULLISH,
                    strength=65,
                    description="Potential bullish reversal",
                    candle_index=i
                ))
            
            # Shooting Star (bearish, after uptrend) - same shape as inverted hammer
            if self._is_inverted_hammer(c) and trend == "UP":
                patterns_found.append(CandlestickPattern(
                    name="Shooting Star",
                    pattern_type=PatternType.BEARISH,
                    strength=75,
                    description="Bearish reversal signal",
                    candle_index=i
                ))
            
            # === Two Candle Patterns ===
            
            # Engulfing
            engulfing = self._is_engulfing(c, c1)
            if engulfing == "BULLISH" and trend == "DOWN":
                patterns_found.append(CandlestickPattern(
                    name="Bullish Engulfing",
                    pattern_type=PatternType.BULLISH,
                    strength=85,
                    description="Strong bullish reversal",
                    candle_index=i
                ))
            elif engulfing == "BEARISH" and trend == "UP":
                patterns_found.append(CandlestickPattern(
                    name="Bearish Engulfing",
                    pattern_type=PatternType.BEARISH,
                    strength=85,
                    description="Strong bearish reversal",
                    candle_index=i
                ))
            
            # Harami
            harami = self._is_harami(c, c1)
            if harami == "BULLISH" and trend == "DOWN":
                patterns_found.append(CandlestickPattern(
                    name="Bullish Harami",
                    pattern_type=PatternType.BULLISH,
                    strength=60,
                    description="Potential bullish reversal",
                    candle_index=i
                ))
            elif harami == "BEARISH" and trend == "UP":
                patterns_found.append(CandlestickPattern(
                    name="Bearish Harami",
                    pattern_type=PatternType.BEARISH,
                    strength=60,
                    description="Potential bearish reversal",
                    candle_index=i
                ))
            
            # Piercing Line
            if self._is_piercing(c, c1) and trend == "DOWN":
                patterns_found.append(CandlestickPattern(
                    name="Piercing Line",
                    pattern_type=PatternType.BULLISH,
                    strength=70,
                    description="Bullish reversal pattern",
                    candle_index=i
                ))
            
            # Dark Cloud Cover
            if self._is_dark_cloud(c, c1) and trend == "UP":
                patterns_found.append(CandlestickPattern(
                    name="Dark Cloud Cover",
                    pattern_type=PatternType.BEARISH,
                    strength=70,
                    description="Bearish reversal pattern",
                    candle_index=i
                ))
            
            # === Three Candle Patterns ===
            
            # Morning Star
            if self._is_morning_star(c, c1, c2) and trend == "DOWN":
                patterns_found.append(CandlestickPattern(
                    name="Morning Star",
                    pattern_type=PatternType.BULLISH,
                    strength=90,
                    description="Strong 3-candle bullish reversal",
                    candle_index=i
                ))
            
            # Evening Star
            if self._is_evening_star(c, c1, c2) and trend == "UP":
                patterns_found.append(CandlestickPattern(
                    name="Evening Star",
                    pattern_type=PatternType.BEARISH,
                    strength=90,
                    description="Strong 3-candle bearish reversal",
                    candle_index=i
                ))
            
            # Three White Soldiers
            if self._is_three_white_soldiers(c, c1, c2):
                patterns_found.append(CandlestickPattern(
                    name="Three White Soldiers",
                    pattern_type=PatternType.BULLISH,
                    strength=95,
                    description="Strong bullish continuation",
                    candle_index=i
                ))
            
            # Three Black Crows
            if self._is_three_black_crows(c, c1, c2):
                patterns_found.append(CandlestickPattern(
                    name="Three Black Crows",
                    pattern_type=PatternType.BEARISH,
                    strength=95,
                    description="Strong bearish continuation",
                    candle_index=i
                ))
        
        # Remove duplicates (keep highest strength per pattern name)
        unique: Dict[str, CandlestickPattern] = {}
        for p in patterns_found:
            if p.name not in unique or p.strength > unique[p.name].strength:
                unique[p.name] = p
        
        return list(unique.values())
    
    # === Single Candle Pattern Detection ===
    
    def _is_doji(self, c: Candle) -> bool:
        """
        Doji: very small body relative to range.
        TA-Lib: body <= 10% of average body over 10 periods (simplified here)
        """
        if c.range == 0:
            return False
        return c.body_pct <= self.config.doji_body_pct
    
    def _is_hammer(self, c: Candle) -> bool:
        """
        Hammer: small body at top, long lower shadow, tiny upper shadow.
        TA-Lib criteria:
        - Small real body at upper end
        - Lower shadow at least 2x real body
        - Little or no upper shadow
        """
        if c.range == 0:
            return False
        
        # Small body
        if c.body_pct > self.config.small_body_pct:
            return False
        
        # Long lower shadow (at least 2x body)
        if c.lower_shadow < c.body * 2:
            return False
        
        # Very short upper shadow
        if c.upper_shadow_pct > self.config.shadow_short_pct:
            return False
        
        return True
    
    def _is_inverted_hammer(self, c: Candle) -> bool:
        """
        Inverted Hammer: small body at bottom, long upper shadow, tiny lower shadow.
        """
        if c.range == 0:
            return False
        
        # Small body
        if c.body_pct > self.config.small_body_pct:
            return False
        
        # Long upper shadow (at least 2x body)
        if c.upper_shadow < c.body * 2:
            return False
        
        # Very short lower shadow
        if c.lower_shadow_pct > self.config.shadow_short_pct:
            return False
        
        return True
    
    # === Two Candle Pattern Detection ===
    
    def _is_engulfing(self, c: Candle, prev: Candle) -> Optional[str]:
        """
        Engulfing: current body completely engulfs previous body.
        TA-Lib: opposite colors, current body engulfs previous body.
        """
        # Must be opposite colors
        if c.is_bullish == prev.is_bullish:
            return None
        
        # Current body must be larger
        if c.body <= prev.body:
            return None
        
        # Current must engulf previous body
        if c.is_bullish:
            # Bullish: current opens at/below prev close, closes at/above prev open
            if c.open <= prev.close and c.close >= prev.open:
                return "BULLISH"
        else:
            # Bearish: current opens at/above prev close, closes at/below prev open
            if c.open >= prev.close and c.close <= prev.open:
                return "BEARISH"
        
        return None
    
    def _is_harami(self, c: Candle, prev: Candle) -> Optional[str]:
        """
        Harami: small current body inside previous large body.
        TA-Lib: opposite colors, current body inside previous body.
        """
        # Previous must have large body
        if prev.body_pct < self.config.large_body_pct:
            return None
        
        # Current must have small body (relative to previous)
        if c.body >= prev.body * 0.5:
            return None
        
        # Current body must be inside previous body
        if c.body_top > prev.body_top or c.body_bottom < prev.body_bottom:
            return None
        
        # Determine type based on previous candle
        if prev.is_bearish:
            return "BULLISH"
        else:
            return "BEARISH"
    
    def _is_piercing(self, c: Candle, prev: Candle) -> bool:
        """
        Piercing Line: bearish candle followed by bullish that closes above 50% of prev body.
        """
        # Previous must be bearish with decent body
        if not prev.is_bearish or prev.body_pct < self.config.large_body_pct:
            return False
        
        # Current must be bullish
        if not c.is_bullish:
            return False
        
        # Current opens below previous close
        if c.open >= prev.close:
            return False
        
        # Current closes above 50% of previous body (midpoint)
        prev_midpoint = prev.close + (prev.open - prev.close) / 2
        if c.close < prev_midpoint:
            return False
        
        # But not above previous open (would be engulfing)
        if c.close >= prev.open:
            return False
        
        return True
    
    def _is_dark_cloud(self, c: Candle, prev: Candle) -> bool:
        """
        Dark Cloud Cover: bullish candle followed by bearish that closes below 50% of prev body.
        """
        # Previous must be bullish with decent body
        if not prev.is_bullish or prev.body_pct < self.config.large_body_pct:
            return False
        
        # Current must be bearish
        if not c.is_bearish:
            return False
        
        # Current opens above previous close
        if c.open <= prev.close:
            return False
        
        # Current closes below 50% of previous body
        prev_midpoint = prev.open + (prev.close - prev.open) / 2
        if c.close > prev_midpoint:
            return False
        
        # But not below previous open (would be engulfing)
        if c.close <= prev.open:
            return False
        
        return True
    
    # === Three Candle Pattern Detection ===
    
    def _is_morning_star(self, c: Candle, c1: Candle, c2: Candle) -> bool:
        """
        Morning Star: bearish -> small body (gap down) -> bullish.
        Strong bullish reversal.
        """
        # First candle (c2) must be bearish with large body
        if not c2.is_bearish or c2.body_pct < self.config.large_body_pct:
            return False
        
        # Middle candle (c1) must have small body
        if c1.body_pct > self.config.small_body_pct:
            return False
        
        # Third candle (c) must be bullish with large body
        if not c.is_bullish or c.body_pct < self.config.large_body_pct:
            return False
        
        # Third closes above midpoint of first
        c2_midpoint = c2.close + (c2.open - c2.close) / 2
        if c.close < c2_midpoint:
            return False
        
        return True
    
    def _is_evening_star(self, c: Candle, c1: Candle, c2: Candle) -> bool:
        """
        Evening Star: bullish -> small body (gap up) -> bearish.
        Strong bearish reversal.
        """
        # First candle (c2) must be bullish with large body
        if not c2.is_bullish or c2.body_pct < self.config.large_body_pct:
            return False
        
        # Middle candle (c1) must have small body
        if c1.body_pct > self.config.small_body_pct:
            return False
        
        # Third candle (c) must be bearish with large body
        if not c.is_bearish or c.body_pct < self.config.large_body_pct:
            return False
        
        # Third closes below midpoint of first
        c2_midpoint = c2.open + (c2.close - c2.open) / 2
        if c.close > c2_midpoint:
            return False
        
        return True
    
    def _is_three_white_soldiers(self, c: Candle, c1: Candle, c2: Candle) -> bool:
        """
        Three White Soldiers: three consecutive bullish candles with higher closes.
        Each opens within previous body.
        """
        # All must be bullish with decent bodies
        for candle in [c, c1, c2]:
            if not candle.is_bullish or candle.body_pct < 0.5:
                return False
        
        # Progressive higher closes
        if not (c.close > c1.close > c2.close):
            return False
        
        # Each opens within previous body (not gap up)
        if c.open < c1.body_bottom or c.open > c1.body_top:
            return False
        if c1.open < c2.body_bottom or c1.open > c2.body_top:
            return False
        
        return True
    
    def _is_three_black_crows(self, c: Candle, c1: Candle, c2: Candle) -> bool:
        """
        Three Black Crows: three consecutive bearish candles with lower closes.
        Each opens within previous body.
        """
        # All must be bearish with decent bodies
        for candle in [c, c1, c2]:
            if not candle.is_bearish or candle.body_pct < 0.5:
                return False
        
        # Progressive lower closes
        if not (c.close < c1.close < c2.close):
            return False
        
        # Each opens within previous body
        if c.open < c1.body_bottom or c.open > c1.body_top:
            return False
        if c1.open < c2.body_bottom or c1.open > c2.body_top:
            return False
        
        return True
    
    # === Output Formatting ===
    
    def get_pattern_category(self, patterns: List[CandlestickPattern]) -> str:
        """
        Get overall pattern category.
        Returns: "BULLISH", "BEARISH", or "NONE"
        """
        if not patterns:
            return "NONE"
        
        bullish_score = sum(p.strength for p in patterns if p.pattern_type == PatternType.BULLISH)
        bearish_score = sum(p.strength for p in patterns if p.pattern_type == PatternType.BEARISH)
        
        # Need significant advantage
        if bullish_score > bearish_score * 1.2:
            return "BULLISH"
        elif bearish_score > bullish_score * 1.2:
            return "BEARISH"
        return "NONE"
    
    def get_strongest_pattern(self, patterns: List[CandlestickPattern]) -> Optional[str]:
        """Get name of strongest detected pattern."""
        if not patterns:
            return None
        return max(patterns, key=lambda p: p.strength).name
    
    def get_bias(self, patterns: List[CandlestickPattern]) -> float:
        """
        Calculate directional bias from patterns.
        Returns: -1.0 (strong bearish) to +1.0 (strong bullish)
        """
        if not patterns:
            return 0.0
        
        score = 0.0
        total_weight = 0.0
        
        for p in patterns:
            weight = p.strength / 100.0
            total_weight += weight
            
            if p.pattern_type == PatternType.BULLISH:
                score += weight
            elif p.pattern_type == PatternType.BEARISH:
                score -= weight
        
        if total_weight == 0:
            return 0.0
        
        return max(-1.0, min(1.0, score / total_weight))
    
    def format_for_context(self, patterns: List[CandlestickPattern]) -> Dict[str, Any]:
        """
        Format patterns for TechnicalAgent context.
        """
        category = self.get_pattern_category(patterns)
        strongest = self.get_strongest_pattern(patterns)
        bias = self.get_bias(patterns)
        
        return {
            "candle_pattern": category,
            "candle_pattern_name": strongest or "NONE",
            "candle_pattern_bias": round(bias, 3),
            "candle_patterns_detected": [p.name for p in sorted(patterns, key=lambda x: x.strength, reverse=True)[:3]],
            "candle_pattern_count": len(patterns),
            "candle_pattern_strength": max((p.strength for p in patterns), default=0),
        }


# === Testing ===

if __name__ == "__main__":
    print("Testing CandlestickPatternDetector v2.0")
    print("=" * 50)
    
    detector = CandlestickPatternDetector()
    
    # Test data: downtrend followed by hammer
    opens =  [1.1000, 1.0980, 1.0950, 1.0920, 1.0900, 1.0870, 1.0850, 1.0830, 1.0810, 1.0850]
    highs =  [1.1010, 1.0990, 1.0960, 1.0930, 1.0910, 1.0880, 1.0860, 1.0840, 1.0860, 1.0870]
    lows =   [1.0980, 1.0950, 1.0920, 1.0890, 1.0870, 1.0840, 1.0820, 1.0800, 1.0750, 1.0820]
    closes = [1.0985, 1.0955, 1.0925, 1.0895, 1.0875, 1.0845, 1.0825, 1.0805, 1.0855, 1.0865]
    
    # With ATR filter
    atr = 0.0030  # Typical EURUSD ATR
    patterns = detector.detect_all(opens, highs, lows, closes, atr=atr)
    
    print(f"\nDetected {len(patterns)} patterns (with ATR filter={atr}):")
    for p in patterns:
        print(f"  [{p.pattern_type.value:7}] {p.name}: strength={p.strength}")
    
    print("\nContext output:")
    ctx = detector.format_for_context(patterns)
    for k, v in ctx.items():
        print(f"  {k}: {v}")
