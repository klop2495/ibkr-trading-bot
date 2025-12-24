"""
Technical Analysis Agent.

Phase 6 Update: Uses real market data from signal_preview/snapshot.
Always has REAL data status (technical data comes from IB Gateway).
"""

from typing import Any, Dict

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.agents.llm.data_status import DataStatus
from app.models.confidence import ConfidenceLevel, confidence_to_float


class TechnicalAgent(BaseLLMAgent):
    """
    Technical analysis specialist.
    
    Focuses on:
    - Trend direction (SMA alignment)
    - Momentum (RSI zones)
    - Volatility (ATR relative)
    - Price action patterns
    - Support/Resistance zones
    
    Data source: IB Gateway market data (always REAL).
    Does NOT provide: specific prices, SL/TP levels, lot sizes.
    """
    
    name = "TechnicalAgent"
    version = "2.0"
    weight = 0.25  # 25% contribution to LLM decision
    
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
  "confidence_float": 0.0 to 1.0,
  "reasoning": "Brief explanation (max 150 chars)",
  "flags": ["FLAG1", "FLAG2"]
}

CONFIDENCE_FLOAT GUIDELINES:
- 0.9: Multiple strong factors align (trend+momentum+pattern)
- 0.7: Two factors align, others neutral
- 0.5: Single factor or mixed signals
- 0.3: Weak signal, low conviction

RULES:
1. NEVER mention specific prices, SL/TP levels, or position sizes
2. Focus only on directional bias from technical factors
3. Use HOLD when signals conflict or are unclear
4. Flags should be categorical: TREND_ALIGNED, RSI_EXTREME, DIVERGENCE, PATTERN_FORMED, etc.

SIGNAL GUIDELINES:
- LONG: Uptrend + bullish momentum + supportive structure
- SHORT: Downtrend + bearish momentum + resistance
- HOLD: Mixed signals, ranging market, or insufficient data"""
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """Generate mock response based on input data."""
        data = input_data.get("data", {})
        
        # Extract key indicators
        trend_short = data.get("trend_short", "NEUTRAL")
        rsi_zone = data.get("rsi_zone", "NEUTRAL")
        sma_alignment = data.get("sma_alignment", "MIXED")
        
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
        
        # Determine signal based on factors
        flags = ["MOCK_MODE"]
        
        if factors_long >= 2 and rsi_zone != "OVERBOUGHT":
            signal = "LONG"
            confidence_float = 0.7 if factors_long >= 3 else 0.55
            confidence = ConfidenceLevel.MEDIUM if confidence_float >= 0.6 else ConfidenceLevel.LOW
            flags.extend(["TREND_UP", "BULLISH_SETUP"])
        elif factors_short >= 2 and rsi_zone != "OVERSOLD":
            signal = "SHORT"
            confidence_float = 0.7 if factors_short >= 3 else 0.55
            confidence = ConfidenceLevel.MEDIUM if confidence_float >= 0.6 else ConfidenceLevel.LOW
            flags.extend(["TREND_DOWN", "BEARISH_SETUP"])
        else:
            signal = "HOLD"
            confidence_float = 0.3
            confidence = ConfidenceLevel.LOW
            flags.append("NO_CLEAR_SIGNAL")
        
        sig = AgentSignal(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=f"Mock: {symbol} trend={trend_short} rsi={rsi_zone}",
            data_status=DataStatus.REAL,
            flags=flags,
        )
        sig.confidence_float = confidence_float
        return sig
