"""
Sentiment Analysis Agent.

Phase 3: Analyzes COT positioning, retail sentiment.
Returns categorical signals based on positioning data.
"""

from typing import Any, Dict

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.models.confidence import ConfidenceLevel


class SentimentAgent(BaseLLMAgent):
    """
    Sentiment/Positioning analysis specialist.
    
    Focuses on:
    - COT commercial positioning
    - COT speculator positioning
    - Positioning extremes
    - Position changes
    - Retail sentiment (contrarian)
    
    Does NOT provide: specific prices, SL/TP levels, lot sizes.
    """
    
    name = "SentimentAgent"
    version = "1.0"
    weight = 0.15  # 15% contribution to final decision
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        """
        Prepare categorical sentiment data.
        
        Uses COT reports and sentiment indicators.
        """
        cot = context.get("cot_reports", {})
        sentiment = context.get("sentiment", {})
        
        # Extract base currency COT
        base_ccy = symbol[:3] if len(symbol) >= 3 else "EUR"
        cot_data = cot.get(base_ccy, {})
        
        return {
            "symbol": symbol,
            "agent": self.name,
            "data": {
                # COT Positioning
                "cot_commercial_bias": cot_data.get("bias", "NEUTRAL"),  # LONG/SHORT/NEUTRAL
                "cot_speculator_bias": cot_data.get("speculator_bias", "NEUTRAL"),
                "cot_percentile_bucket": cot_data.get("percentile_bucket", "MEDIUM"),  # EXTREME_HIGH/HIGH/MEDIUM/LOW/EXTREME_LOW
                "cot_change_direction": cot_data.get("change_direction", "FLAT"),  # INCREASING/DECREASING/FLAT
                
                # Positioning extremes
                "positioning_extreme": cot_data.get("percentile_bucket") in ("EXTREME_HIGH", "EXTREME_LOW"),
                "extreme_type": self._get_extreme_type(cot_data),
                
                # Retail sentiment (contrarian indicator)
                "retail_bias": sentiment.get("retail_bias", "NEUTRAL"),  # LONG/SHORT/NEUTRAL
                "retail_extreme": sentiment.get("retail_extreme", False),
                
                # Smart money alignment
                "smart_money_aligned": self._check_smart_money(cot_data),
            }
        }
    
    def _get_extreme_type(self, cot_data: dict) -> str:
        """Determine type of positioning extreme."""
        bucket = cot_data.get("percentile_bucket", "MEDIUM")
        bias = cot_data.get("bias", "NEUTRAL")
        
        if bucket == "EXTREME_HIGH":
            return "CROWDED_LONG"
        elif bucket == "EXTREME_LOW":
            return "CROWDED_SHORT"
        return "NONE"
    
    def _check_smart_money(self, cot_data: dict) -> bool:
        """Check if commercials and speculators diverge (smart money signal)."""
        commercial = cot_data.get("bias", "NEUTRAL")
        speculator = cot_data.get("speculator_bias", "NEUTRAL")
        
        # Smart money = commercials opposite to speculators
        if commercial == "LONG" and speculator == "SHORT":
            return True
        if commercial == "SHORT" and speculator == "LONG":
            return True
        return False
    
    def get_system_prompt(self) -> str:
        return """You are a Sentiment Analysis Agent for forex trading.

Your role: Analyze positioning data (COT, retail) to identify crowded trades and reversals.

INPUT: You receive categorical sentiment data (COT bias, percentiles, retail positioning).

OUTPUT: Respond with JSON only:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "reasoning": "Brief explanation (max 150 chars)",
  "flags": ["FLAG1", "FLAG2"]
}

RULES:
1. NEVER mention specific prices, SL/TP levels, or position sizes
2. Extreme positioning often precedes reversals (contrarian)
3. Commercial hedgers (smart money) are often right at turns
4. Retail sentiment is a contrarian indicator
5. Flags: EXTREME_POSITIONING, SMART_MONEY_DIVERGENCE, CONTRARIAN_SETUP, etc.

SIGNAL GUIDELINES:
- LONG: Extreme short positioning (crowded short), commercials long
- SHORT: Extreme long positioning (crowded long), commercials short
- HOLD: Neutral positioning or conflicting signals

CONFIDENCE GUIDELINES:
- HIGH: Extreme percentile + smart money divergence
- MEDIUM: Notable positioning bias, no extremes
- LOW: Neutral positioning or mixed signals"""
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """Generate mock response based on input data."""
        data = input_data.get("data", {})
        
        extreme_type = data.get("extreme_type", "NONE")
        smart_money = data.get("smart_money_aligned", False)
        
        # Contrarian logic for extremes
        if extreme_type == "CROWDED_LONG":
            signal = "SHORT"
            confidence = ConfidenceLevel.MEDIUM if smart_money else ConfidenceLevel.LOW
            flags = ["CROWDED_LONG", "CONTRARIAN_SHORT", "MOCK_MODE"]
        elif extreme_type == "CROWDED_SHORT":
            signal = "LONG"
            confidence = ConfidenceLevel.MEDIUM if smart_money else ConfidenceLevel.LOW
            flags = ["CROWDED_SHORT", "CONTRARIAN_LONG", "MOCK_MODE"]
        else:
            signal = "HOLD"
            confidence = ConfidenceLevel.LOW
            flags = ["NEUTRAL_POSITIONING", "MOCK_MODE"]
        
        if smart_money:
            flags.append("SMART_MONEY_DIVERGENCE")
        
        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=f"Mock: {symbol} extreme={extreme_type} smart_money={smart_money}",
            flags=flags,
        )
