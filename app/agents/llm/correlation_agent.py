"""
Correlation Analysis Agent.

Phase 6 Update: Uses REAL DXY data from Yahoo Finance.
data_status = REAL when DXY snapshot is fresh, PARTIAL when stale.
"""

from typing import Any, Dict

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.agents.llm.data_status import DataStatus
from app.models.confidence import ConfidenceLevel


class CorrelationAgent(BaseLLMAgent):
    """
    Correlation/Inter-market analysis specialist.
    
    Focuses on:
    - DXY (Dollar Index) relationship
    - Correlated pair confirmation
    - Cross-rate analysis
    - Divergence detection
    - Risk asset correlation
    
    Data source: DXY from Yahoo Finance (REAL), correlated pairs from IB Gateway
    Does NOT provide: specific prices, SL/TP levels, lot sizes.
    """
    
    name = "CorrelationAgent"
    version = "2.0"
    weight = 0.15  # 15% contribution to LLM decision
    
    def check_data_status(self, context: Dict[str, Any], symbol: str) -> DataStatus:
        """
        Check if we have REAL DXY data.
        
        DXY is fetched from Yahoo Finance - check staleness.
        """
        dxy = context.get("dxy_snapshot", {})
        
        # Check for explicit mock indicator
        if dxy.get("_mock_mode", False):
            return DataStatus.MISSING
        
        # Check source health
        source_health = context.get("source_health", {})
        dxy_health = source_health.get("dxy_index", {})
        
        # If explicitly marked as mock
        if dxy_health.get("mock_mode", False):
            return DataStatus.MISSING
        
        # If no DXY data
        if not dxy or not dxy.get("value"):
            return DataStatus.MISSING
        
        # Check staleness (if stale_minutes provided)
        stale_minutes = dxy_health.get("staleness_minutes", 0)
        if stale_minutes > 60:  # More than 1 hour stale
            return DataStatus.PARTIAL
        
        # If DXY has trend and value, it's real
        if dxy.get("trend") and dxy.get("value"):
            return DataStatus.REAL
        
        return DataStatus.PARTIAL
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        """
        Prepare categorical correlation data.
        
        Uses DXY snapshot and cross-pair analysis.
        """
        dxy = context.get("dxy_snapshot", {})
        correlations = context.get("correlations", {})
        
        # Determine if symbol has USD component
        has_usd = "USD" in symbol
        usd_is_base = symbol.startswith("USD")
        usd_is_quote = symbol.endswith("USD")
        
        # Get correlated pairs data
        correlated_pairs = correlations.get(symbol, {})
        
        return {
            "symbol": symbol,
            "agent": self.name,
            "data": {
                # DXY Analysis
                "dxy_trend": dxy.get("trend", "NEUTRAL"),  # UP/DOWN/NEUTRAL
                "dxy_vs_sma": dxy.get("vs_sma", "AT"),  # ABOVE/BELOW/AT
                "dxy_daily_direction": dxy.get("daily_direction", "NEUTRAL"),
                "dxy_volatility": dxy.get("volatility_level", "NORMAL"),  # LOW/NORMAL/HIGH
                
                # USD relationship
                "has_usd": has_usd,
                "usd_is_base": usd_is_base,
                "usd_is_quote": usd_is_quote,
                
                # Expected DXY impact on symbol
                "dxy_expected_impact": self._calculate_dxy_impact(symbol, dxy),
                
                # Correlated pairs
                "primary_correlation": correlated_pairs.get("primary_pair", ""),
                "primary_direction": correlated_pairs.get("primary_direction", "NEUTRAL"),
                "correlation_confirms": correlated_pairs.get("confirms", False),
                
                # Divergence
                "divergence_detected": correlated_pairs.get("divergence", False),
                "divergence_type": correlated_pairs.get("divergence_type", "NONE"),
            }
        }
    
    def _calculate_dxy_impact(self, symbol: str, dxy: dict) -> str:
        """
        Calculate expected impact of DXY move on symbol.
        
        DXY up = USD strength:
        - EURUSD, GBPUSD, etc. should go DOWN (negative correlation)
        - USDJPY, USDCHF, etc. should go UP (positive correlation)
        """
        dxy_trend = dxy.get("trend", "NEUTRAL")
        
        if "USD" not in symbol:
            return "INDIRECT"  # No direct USD exposure
        
        usd_is_quote = symbol.endswith("USD")
        
        if dxy_trend == "UP":
            if usd_is_quote:
                return "BEARISH"  # DXY up -> USD strong -> EURUSD down
            else:
                return "BULLISH"  # DXY up -> USD strong -> USDJPY up
        elif dxy_trend == "DOWN":
            if usd_is_quote:
                return "BULLISH"  # DXY down -> USD weak -> EURUSD up
            else:
                return "BEARISH"  # DXY down -> USD weak -> USDJPY down
        
        return "NEUTRAL"
    
    def get_system_prompt(self) -> str:
        return """You are a Correlation Analysis Agent for forex trading.

Your role: Analyze inter-market relationships (DXY, correlated pairs) for confirmation or divergence.

INPUT: You receive categorical correlation data (DXY trend, correlated pair signals).

OUTPUT: Respond with JSON only:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "confidence_float": 0.0 to 1.0,
  "reasoning": "Brief explanation (max 150 chars)",
  "flags": ["FLAG1", "FLAG2"]
}

CONFIDENCE_FLOAT GUIDELINES:
- 0.9: DXY aligned + correlated pair strongly confirms
- 0.7: DXY or correlated pair supports, other neutral
- 0.5: No clear correlation signal
- 0.3: Mixed signals or divergence present

RULES:
1. NEVER mention specific prices, SL/TP levels, or position sizes
2. DXY trend is crucial for USD pairs
3. Correlated pair confirmation increases confidence
4. Divergences from correlated pairs are warning signs
5. Flags: DXY_ALIGNED, CORRELATION_CONFIRMS, DIVERGENCE_WARNING, USD_STRENGTH/WEAKNESS, etc.

SIGNAL GUIDELINES:
- LONG: DXY impact supports long + correlated pair confirms
- SHORT: DXY impact supports short + correlated pair confirms
- HOLD: Divergence detected or conflicting correlations"""
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """Generate mock response based on input data."""
        data = input_data.get("data", {})
        
        dxy_impact = data.get("dxy_expected_impact", "NEUTRAL")
        correlation_confirms = data.get("correlation_confirms", False)
        divergence = data.get("divergence_detected", False)
        
        # Check for divergence first
        if divergence:
            sig = AgentSignal(
                agent_name=self.name,
                signal="HOLD",
                confidence=ConfidenceLevel.MEDIUM,
                reasoning=f"Mock: Divergence detected in correlated pairs",
                data_status=DataStatus.REAL,  # DXY is real
                flags=["DIVERGENCE_WARNING", "MOCK_MODE"],
            )
            sig.confidence_float = 0.5
            return sig
        
        # Follow DXY impact
        flags = ["MOCK_MODE"]
        
        if dxy_impact == "BULLISH":
            signal = "LONG"
            confidence_float = 0.7 if correlation_confirms else 0.5
            confidence = ConfidenceLevel.MEDIUM if correlation_confirms else ConfidenceLevel.LOW
            flags.append("DXY_ALIGNED")
        elif dxy_impact == "BEARISH":
            signal = "SHORT"
            confidence_float = 0.7 if correlation_confirms else 0.5
            confidence = ConfidenceLevel.MEDIUM if correlation_confirms else ConfidenceLevel.LOW
            flags.append("DXY_ALIGNED")
        else:
            signal = "HOLD"
            confidence_float = 0.3
            confidence = ConfidenceLevel.LOW
            flags.append("NO_DXY_SIGNAL")
        
        if correlation_confirms:
            flags.append("CORRELATION_CONFIRMS")
        
        sig = AgentSignal(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=f"Mock: {symbol} dxy_impact={dxy_impact} confirms={correlation_confirms}",
            data_status=DataStatus.REAL,  # DXY is real from Yahoo Finance
            flags=flags,
        )
        sig.confidence_float = confidence_float
        return sig
