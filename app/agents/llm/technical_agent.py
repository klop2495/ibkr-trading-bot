"""
Technical Analysis Agent.

Phase 6 Update: Uses real market data from signal_preview/snapshot.
Phase 7 Update: Integrates CandlestickPatternDetector for pattern analysis.

Always has REAL data status (technical data comes from IB Gateway).
"""

import logging
from typing import Any, Dict, Optional

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.agents.llm.data_status import DataStatus
from app.models.confidence import ConfidenceLevel

# Phase 7: Import candlestick pattern detector
try:
    from app.market_data.candlestick_patterns import CandlestickPatternDetector
    CANDLESTICK_AVAILABLE = True
except ImportError:
    CANDLESTICK_AVAILABLE = False


logger = logging.getLogger(__name__)


class TechnicalAgent(BaseLLMAgent):
    """
    Technical analysis specialist.
    
    Focuses on:
    - Trend direction (SMA alignment)
    - Momentum (RSI zones)
    - Volatility (ATR relative)
    - Price action patterns (Phase 7: enhanced with candlestick detector)
    - Support/Resistance zones
    
    Data source: IB Gateway market data (always REAL).
    Does NOT provide: specific prices, SL/TP levels, lot sizes.
    """
    
    name = "TechnicalAgent"
    version = "2.1"  # Phase 7: version bump
    weight = 0.25  # 25% contribution to LLM decision
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Phase 7: Initialize candlestick pattern detector
        self._pattern_detector: Optional[CandlestickPatternDetector] = None
        if CANDLESTICK_AVAILABLE:
            try:
                self._pattern_detector = CandlestickPatternDetector()
                logger.info("TechnicalAgent: CandlestickPatternDetector initialized")
            except Exception as e:
                logger.warning(f"TechnicalAgent: Failed to init pattern detector: {e}")
    
    def check_data_status(self, context: Dict[str, Any], symbol: str) -> DataStatus:
        """
        Technical data is always from IB Gateway.
        Check if we have technical context with basic indicators.
        """
        technical = context.get("technical", {})
        
        # Minimal required: trend_short exists
        if not technical.get("trend_short"):
            return DataStatus.MISSING
        
        # Check if we have RSI zone (important indicator)
        if technical.get("rsi_zone") == "UNKNOWN":
            return DataStatus.PARTIAL
        
        return DataStatus.REAL
    
    def _detect_candlestick_patterns(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 7: Detect candlestick patterns from OHLC data.
        
        Returns pattern context dict or empty dict if unavailable.
        """
        if not self._pattern_detector:
            return {}
        
        ohlc = context.get("ohlc", {})
        if not ohlc:
            return {}
        
        opens = ohlc.get("opens", [])
        highs = ohlc.get("highs", [])
        lows = ohlc.get("lows", [])
        closes = ohlc.get("closes", [])
        
        if not opens or len(opens) < 5:
            return {}
        
        try:
            # Get ATR for filtering
            atr = context.get("atr_current")
            
            # Detect patterns
            patterns = self._pattern_detector.detect_all(
                opens, highs, lows, closes, 
                atr=atr,
                lookback=5
            )
            
            # Format for context
            return self._pattern_detector.format_for_context(patterns)
            
        except Exception as e:
            logger.warning(f"TechnicalAgent: Pattern detection failed: {e}")
            return {}
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        """
        Prepare categorical technical data.
        
        Converts raw OHLCV/indicators into categories.
        Phase 7: Integrates candlestick pattern detection.
        """
        technical = context.get("technical", {})
        
        # Phase 7: Detect candlestick patterns
        pattern_data = self._detect_candlestick_patterns(context)
        
        # Merge pattern data with technical context
        # Pattern detector provides more accurate candle_pattern than default
        candle_pattern = pattern_data.get("candle_pattern") or technical.get("candle_pattern", "NONE")
        candle_pattern_name = pattern_data.get("candle_pattern_name") or "NONE"
        candle_pattern_bias = pattern_data.get("candle_pattern_bias", 0.0)
        candle_patterns_detected = pattern_data.get("candle_patterns_detected", [])
        
        return {
            "symbol": symbol,
            "agent": self.name,
            "data": {
                # Trend
                "trend_short": technical.get("trend_short", "NEUTRAL"),  # UP/DOWN/NEUTRAL
                "trend_medium": technical.get("trend_medium", "NEUTRAL"),
                "trend_long": technical.get("trend_long", "NEUTRAL"),
                "sma_alignment": technical.get("sma_alignment", "MIXED"),  # BULLISH/BEARISH/MIXED
                
                # Momentum
                "rsi_zone": technical.get("rsi_zone", "NEUTRAL"),  # OVERSOLD/NEUTRAL/OVERBOUGHT
                "macd_signal": technical.get("macd_signal", "NEUTRAL"),  # BULLISH/BEARISH/NEUTRAL
                "momentum_divergence": technical.get("momentum_divergence", "NONE"),  # BULLISH/BEARISH/NONE
                
                # Volatility
                "volatility_level": technical.get("volatility_level", "NORMAL"),  # LOW/NORMAL/HIGH
                "atr_relative": technical.get("atr_relative", "NORMAL"),  # COMPRESSED/NORMAL/EXPANDED
                
                # Price action (Phase 7: Enhanced with pattern detector)
                "candle_pattern": candle_pattern,  # BULLISH/BEARISH/NONE
                "candle_pattern_name": candle_pattern_name,  # Hammer, Engulfing, etc.
                "candle_pattern_bias": candle_pattern_bias,  # -1.0 to +1.0
                "candle_patterns_detected": candle_patterns_detected,  # List of pattern names
                "candle_direction": technical.get("candle_direction", "NEUTRAL"),  # BULLISH/BEARISH/NEUTRAL
                
                # Structure
                "near_support": technical.get("near_support", False),
                "near_resistance": technical.get("near_resistance", False),
                "breakout_detected": technical.get("breakout_detected", "NONE"),  # BULLISH/BEARISH/NONE
            }
        }
    
    def get_system_prompt(self) -> str:
        return """You are a Technical Analysis Agent for forex trading.

Your role: Analyze technical indicators and price action to determine trade direction.

INPUT: You receive categorical technical data (trends, RSI zones, candlestick patterns).

OUTPUT: Respond with JSON only:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "confidence_float": 0.0 to 1.0,
  "reasoning": "Brief explanation (max 150 chars)",
  "flags": ["FLAG1", "FLAG2"]
}

CONFIDENCE_FLOAT GUIDELINES:
- 0.9: Multiple strong factors align (trend+momentum+pattern)
- 0.7: Two factors align, others neutral
- 0.5: Single factor or mixed signals
- 0.3: Weak signal, low conviction

CANDLESTICK PATTERNS:
- candle_pattern_name tells you the specific pattern (Hammer, Engulfing, etc.)
- candle_pattern_bias is a numeric score (-1 to +1)
- Use patterns to confirm trend signals, not as sole entry reason

RULES:
1. NEVER mention specific prices, SL/TP levels, or position sizes
2. Focus only on directional bias from technical factors
3. Use HOLD when signals conflict or are unclear
4. Flags should be categorical: TREND_ALIGNED, RSI_EXTREME, DIVERGENCE, PATTERN_CONFIRMED, etc.

SIGNAL GUIDELINES:
- LONG: Uptrend + bullish momentum + supportive pattern
- SHORT: Downtrend + bearish momentum + bearish pattern
- HOLD: Mixed signals, ranging market, or insufficient data"""
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """Generate mock response based on input data."""
        data = input_data.get("data", {})
        
        # Extract key indicators
        trend_short = data.get("trend_short", "NEUTRAL")
        rsi_zone = data.get("rsi_zone", "NEUTRAL")
        sma_alignment = data.get("sma_alignment", "MIXED")
        candle_pattern = data.get("candle_pattern", "NONE")
        candle_pattern_bias = data.get("candle_pattern_bias", 0.0)
        
        # Count supporting factors
        factors_long = 0
        factors_short = 0
        
        if trend_short == "UP":
            factors_long += 1
        elif trend_short == "DOWN":
            factors_short += 1
            
        if sma_alignment == "BULLISH":
            factors_long += 1
        elif sma_alignment == "BEARISH":
            factors_short += 1
            
        if rsi_zone == "OVERSOLD":
            factors_long += 1  # Reversal opportunity
        elif rsi_zone == "OVERBOUGHT":
            factors_short += 1  # Reversal opportunity
        
        # Phase 7: Consider candlestick pattern
        if candle_pattern == "BULLISH" or candle_pattern_bias > 0.3:
            factors_long += 1
        elif candle_pattern == "BEARISH" or candle_pattern_bias < -0.3:
            factors_short += 1
        
        # Determine signal based on factors
        flags = ["MOCK_MODE"]
        
        if factors_long >= 2 and rsi_zone != "OVERBOUGHT":
            signal = "LONG"
            confidence_float = 0.7 if factors_long >= 3 else 0.55
            confidence = ConfidenceLevel.MEDIUM if confidence_float >= 0.6 else ConfidenceLevel.LOW
            flags.extend(["TREND_UP", "BULLISH_SETUP"])
            if candle_pattern == "BULLISH":
                flags.append("PATTERN_CONFIRMED")
        elif factors_short >= 2 and rsi_zone != "OVERSOLD":
            signal = "SHORT"
            confidence_float = 0.7 if factors_short >= 3 else 0.55
            confidence = ConfidenceLevel.MEDIUM if confidence_float >= 0.6 else ConfidenceLevel.LOW
            flags.extend(["TREND_DOWN", "BEARISH_SETUP"])
            if candle_pattern == "BEARISH":
                flags.append("PATTERN_CONFIRMED")
        else:
            signal = "HOLD"
            confidence_float = 0.3
            confidence = ConfidenceLevel.LOW
            flags.append("NO_CLEAR_SIGNAL")
        
        sig = AgentSignal(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=f"Mock: {symbol} trend={trend_short} rsi={rsi_zone} pattern={candle_pattern}",
            data_status=DataStatus.REAL,
            flags=flags,
        )
        sig.confidence_float = confidence_float
        return sig
