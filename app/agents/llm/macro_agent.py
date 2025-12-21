"""
Macro/Fundamental Agent.

Phase 3: Analyzes economic calendar, central bank policy.
Returns categorical signals based on macro factors.
"""

from typing import Any, Dict, List

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.models.confidence import ConfidenceLevel


class MacroAgent(BaseLLMAgent):
    """
    Macroeconomic analysis specialist.
    
    Focuses on:
    - Economic calendar events
    - Central bank policy stance
    - Interest rate differentials
    - Economic surprises
    - Risk events timing
    
    Does NOT provide: specific prices, SL/TP levels, lot sizes.
    """
    
    name = "MacroAgent"
    version = "1.0"
    weight = 0.20  # 20% contribution to final decision
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        """
        Prepare categorical macro data.
        
        Uses economic calendar events in categorical format.
        """
        macro = context.get("macro", {})
        calendar = context.get("economic_calendar", [])
        
        # Extract base and quote currencies from symbol
        base_ccy = symbol[:3] if len(symbol) >= 3 else "USD"
        quote_ccy = symbol[3:6] if len(symbol) >= 6 else "USD"
        
        # Filter events for relevant currencies
        relevant_events = [
            e for e in calendar 
            if e.get("currency") in (base_ccy, quote_ccy)
        ]
        
        # Categorize upcoming events
        high_impact_soon = any(
            e.get("impact") == "HIGH" and e.get("timing_category") in ("IMMINENT", "SOON")
            for e in relevant_events
        )
        
        recent_surprises = [
            e for e in relevant_events
            if e.get("has_surprise") and e.get("timing_category") == "PAST"
        ]
        
        return {
            "symbol": symbol,
            "agent": self.name,
            "data": {
                # Currencies
                "base_currency": base_ccy,
                "quote_currency": quote_ccy,
                
                # Central bank stance
                "base_cb_stance": macro.get(f"{base_ccy}_cb_stance", "NEUTRAL"),  # HAWKISH/DOVISH/NEUTRAL
                "quote_cb_stance": macro.get(f"{quote_ccy}_cb_stance", "NEUTRAL"),
                
                # Rate differential direction
                "rate_differential_direction": macro.get("rate_diff_direction", "NEUTRAL"),  # WIDENING_FAVOR_BASE/NARROWING/STABLE
                
                # Economic momentum
                "base_economic_momentum": macro.get(f"{base_ccy}_momentum", "NEUTRAL"),  # IMPROVING/DETERIORATING/STABLE
                "quote_economic_momentum": macro.get(f"{quote_ccy}_momentum", "NEUTRAL"),
                
                # Calendar
                "high_impact_event_soon": high_impact_soon,
                "recent_surprise_count": len(recent_surprises),
                "recent_surprise_direction": self._get_surprise_direction(recent_surprises, base_ccy),
                
                # Risk environment
                "risk_sentiment": macro.get("risk_sentiment", "NEUTRAL"),  # RISK_ON/RISK_OFF/NEUTRAL
            }
        }
    
    def _get_surprise_direction(self, surprises: List[dict], base_ccy: str) -> str:
        """Determine direction of recent surprises for base currency."""
        if not surprises:
            return "NONE"
        
        base_surprises = [s for s in surprises if s.get("currency") == base_ccy]
        if not base_surprises:
            return "NONE"
        
        # Check if surprises were positive or negative for base
        positive = sum(1 for s in base_surprises if s.get("surprise_direction") == "POSITIVE")
        negative = sum(1 for s in base_surprises if s.get("surprise_direction") == "NEGATIVE")
        
        if positive > negative:
            return "POSITIVE_BASE"
        elif negative > positive:
            return "NEGATIVE_BASE"
        return "MIXED"
    
    def get_system_prompt(self) -> str:
        return """You are a Macroeconomic Analysis Agent for forex trading.

Your role: Analyze fundamental factors to determine currency strength bias.

INPUT: You receive categorical macro data (CB stance, rate differentials, economic momentum).

OUTPUT: Respond with JSON only:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "reasoning": "Brief explanation (max 150 chars)",
  "flags": ["FLAG1", "FLAG2"]
}

RULES:
1. NEVER mention specific prices, SL/TP levels, or position sizes
2. Focus on relative currency strength from macro factors
3. HOLD before high-impact events (reduce risk)
4. Weight central bank divergence highly
5. Flags: CB_DIVERGENCE, RATE_ADVANTAGE, EVENT_RISK, DATA_SURPRISE, etc.

SIGNAL GUIDELINES:
- LONG: Base currency fundamentally stronger (hawkish CB, improving data)
- SHORT: Quote currency fundamentally stronger
- HOLD: Mixed fundamentals or imminent high-impact event

CONFIDENCE GUIDELINES:
- HIGH: Clear CB divergence + aligned economic momentum
- MEDIUM: Some fundamental advantage, no conflicts
- LOW: Mixed signals or event risk nearby"""
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """Generate mock response based on input data."""
        data = input_data.get("data", {})
        
        # Check for event risk
        if data.get("high_impact_event_soon"):
            return AgentSignal(
                agent_name=self.name,
                signal="HOLD",
                confidence=ConfidenceLevel.MEDIUM,
                reasoning=f"Mock: High-impact event soon, avoid new positions",
                flags=["EVENT_RISK", "MOCK_MODE"],
            )
        
        # Simple CB stance comparison
        base_stance = data.get("base_cb_stance", "NEUTRAL")
        quote_stance = data.get("quote_cb_stance", "NEUTRAL")
        
        if base_stance == "HAWKISH" and quote_stance != "HAWKISH":
            signal = "LONG"
            confidence = ConfidenceLevel.MEDIUM
            flags = ["CB_DIVERGENCE", "BASE_HAWKISH", "MOCK_MODE"]
        elif quote_stance == "HAWKISH" and base_stance != "HAWKISH":
            signal = "SHORT"
            confidence = ConfidenceLevel.MEDIUM
            flags = ["CB_DIVERGENCE", "QUOTE_HAWKISH", "MOCK_MODE"]
        else:
            signal = "HOLD"
            confidence = ConfidenceLevel.LOW
            flags = ["NO_CB_DIVERGENCE", "MOCK_MODE"]
        
        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=f"Mock: {symbol} base_cb={base_stance} quote_cb={quote_stance}",
            flags=flags,
        )
