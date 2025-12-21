"""
Technical Analysis Agent.

Phase 3: Analyzes price action, indicators, chart patterns.
Returns categorical signals based on technical factors.
"""

from typing import Any, Dict

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.models.confidence import ConfidenceLevel


class TechnicalAgent(BaseLLMAgent):
    """
    Technical analysis specialist.
    
    Focuses on:
    - Trend direction (SMA alignment)
    - Momentum (RSI zones)
    - Volatility (ATR relative)
    - Price action patterns
    - Support/Resistance zones
    
    Does NOT provide: specific prices, SL/TP levels, lot sizes.
    """
    
    name = "TechnicalAgent"
    version = "1.0"
    weight = 0.25  # 25% contribution to final decision
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        """
        Prepare categorical technical data.
        
        Converts raw OHLCV/indicators into categories.
        """
        technical = context.get("technical", {})
        
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
                
                # Price action
                "candle_pattern": technical.get("candle_pattern", "NONE"),  # ENGULFING/DOJI/PIN_BAR/NONE
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

INPUT: You receive categorical technical data (trends, RSI zones, patterns).

OUTPUT: Respond with JSON only:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "reasoning": "Brief explanation (max 150 chars)",
  "flags": ["FLAG1", "FLAG2"]
}

RULES:
1. NEVER mention specific prices, SL/TP levels, or position sizes
2. Focus only on directional bias from technical factors
3. Use HIGH confidence only when multiple factors align
4. Use HOLD when signals conflict or are unclear
5. Flags should be categorical: TREND_ALIGNED, RSI_EXTREME, DIVERGENCE, PATTERN_FORMED, etc.

SIGNAL GUIDELINES:
- LONG: Uptrend + bullish momentum + supportive structure
- SHORT: Downtrend + bearish momentum + resistance
- HOLD: Mixed signals, ranging market, or insufficient data

CONFIDENCE GUIDELINES:
- HIGH: 3+ factors strongly aligned
- MEDIUM: 2 factors aligned, others neutral
- LOW: Single factor or conflicting signals"""
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """Generate mock response based on input data."""
        data = input_data.get("data", {})
        
        # Simple logic for mock
        trend_short = data.get("trend_short", "NEUTRAL")
        rsi_zone = data.get("rsi_zone", "NEUTRAL")
        
        if trend_short == "UP" and rsi_zone != "OVERBOUGHT":
            signal = "LONG"
            confidence = ConfidenceLevel.MEDIUM
            flags = ["TREND_UP", "MOCK_MODE"]
        elif trend_short == "DOWN" and rsi_zone != "OVERSOLD":
            signal = "SHORT"
            confidence = ConfidenceLevel.MEDIUM
            flags = ["TREND_DOWN", "MOCK_MODE"]
        else:
            signal = "HOLD"
            confidence = ConfidenceLevel.LOW
            flags = ["NO_CLEAR_SIGNAL", "MOCK_MODE"]
        
        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=f"Mock: {symbol} trend={trend_short} rsi={rsi_zone}",
            flags=flags,
        )
