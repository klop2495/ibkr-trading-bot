"""
Correlation Analysis Agent.

Phase 6 Update: Uses REAL DXY data from Yahoo Finance.
Phase 7 Update: Integrates CurrencyStrengthMeter for cross-pair analysis.

data_status = REAL when DXY snapshot is fresh, PARTIAL when stale.
"""

import logging
from typing import Any, Dict, Optional

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.agents.llm.data_status import DataStatus
from app.models.confidence import ConfidenceLevel

# Phase 7: Import currency strength meter
try:
    from app.market_data.currency_strength import CurrencyStrengthMeter
    CURRENCY_STRENGTH_AVAILABLE = True
except ImportError:
    CURRENCY_STRENGTH_AVAILABLE = False


logger = logging.getLogger(__name__)


class CorrelationAgent(BaseLLMAgent):
    """
    Correlation/Inter-market analysis specialist.
    
    Focuses on:
    - DXY (Dollar Index) relationship
    - Currency strength analysis (Phase 7: using CurrencyStrengthMeter)
    - Correlated pair confirmation
    - Cross-rate analysis
    - Divergence detection
    
    Data source: DXY from Yahoo Finance (REAL), currency prices from IB Gateway.
    Does NOT provide: specific prices, SL/TP levels, lot sizes.
    """
    
    name = "CorrelationAgent"
    version = "2.1"  # Phase 7: version bump
    weight = 0.15  # 15% contribution to LLM decision
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Phase 7: Initialize currency strength meter
        self._strength_meter: Optional[CurrencyStrengthMeter] = None
        if CURRENCY_STRENGTH_AVAILABLE:
            try:
                self._strength_meter = CurrencyStrengthMeter()
                logger.info("CorrelationAgent: CurrencyStrengthMeter initialized")
            except Exception as e:
                logger.warning(f"CorrelationAgent: Failed to init strength meter: {e}")
    
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
    
    def _calculate_currency_strength(self, context: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        """
        Phase 7: Calculate currency strength from prices.
        
        Returns strength analysis dict or empty dict if unavailable.
        """
        if not self._strength_meter:
            return {"currency_strength_available": False}
        
        current_prices = context.get("current_prices", {})
        previous_24h_prices = context.get("previous_24h_prices", {})
        
        if not current_prices or not previous_24h_prices:
            return {"currency_strength_available": False}
        
        try:
            # Calculate strengths
            strengths = self._strength_meter.calculate(current_prices, previous_24h_prices)
            
            if not strengths:
                return {"currency_strength_available": False}
            
            # Get pair analysis
            pair_analysis = self._strength_meter.analyze_pair(symbol, strengths)
            
            # Get overall context
            strength_context = self._strength_meter.format_for_context(strengths)
            
            # Merge results
            return {
                **strength_context,
                **pair_analysis,
            }
            
        except Exception as e:
            logger.warning(f"CorrelationAgent: Currency strength calculation failed: {e}")
            return {"currency_strength_available": False}
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        """
        Prepare categorical correlation data.
        
        Uses DXY snapshot and currency strength analysis.
        Phase 7: Integrates CurrencyStrengthMeter.
        """
        dxy = context.get("dxy_snapshot", {})
        correlations = context.get("correlations", {})
        
        # Determine if symbol has USD component
        has_usd = "USD" in symbol
        usd_is_base = symbol.startswith("USD")
        usd_is_quote = symbol.endswith("USD")
        
        # Get correlated pairs data
        correlated_pairs = correlations.get(symbol, {})
        
        # Phase 7: Calculate currency strength
        strength_data = self._calculate_currency_strength(context, symbol)
        
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
                
                # Phase 7: Currency Strength Data
                "currency_strength_available": strength_data.get("currency_strength_available", False),
                "strength_signal": strength_data.get("strength_signal", "NEUTRAL"),
                "strength_differential": strength_data.get("strength_differential", 0.0),
                "strongest_currency": strength_data.get("strongest_currency"),
                "weakest_currency": strength_data.get("weakest_currency"),
                "base_strength": strength_data.get("base_strength", 0.0),
                "base_rank": strength_data.get("base_rank", 0),
                "quote_strength": strength_data.get("quote_strength", 0.0),
                "quote_rank": strength_data.get("quote_rank", 0),
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

Your role: Analyze inter-market relationships (DXY, currency strength, correlated pairs) for confirmation or divergence.

INPUT: You receive categorical correlation data (DXY trend, currency strength, correlated pair signals).

OUTPUT: Respond with JSON only:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "confidence_float": 0.0 to 1.0,
  "reasoning": "Brief explanation (max 150 chars)",
  "flags": ["FLAG1", "FLAG2"]
}

CONFIDENCE_FLOAT GUIDELINES:
- 0.9: DXY aligned + currency strength strongly confirms + correlated pair confirms
- 0.7: Two of three factors support, other neutral
- 0.5: Single factor or weak confirmation
- 0.3: Mixed signals or divergence present

CURRENCY STRENGTH ANALYSIS:
- strength_signal: Overall signal from currency strength analysis (BULLISH/BEARISH/NEUTRAL)
- strength_differential: Difference between base and quote strength
- Use currency strength to confirm or contradict DXY analysis

RULES:
1. NEVER mention specific prices, SL/TP levels, or position sizes
2. DXY trend is crucial for USD pairs
3. Currency strength differential > 40 is a strong signal
4. Correlated pair confirmation increases confidence
5. Divergences from correlated pairs are warning signs
6. Flags: DXY_ALIGNED, STRENGTH_CONFIRMS, DIVERGENCE_WARNING, USD_STRENGTH/WEAKNESS, etc.

SIGNAL GUIDELINES:
- LONG: DXY impact supports long + currency strength confirms + correlated pair confirms
- SHORT: DXY impact supports short + currency strength confirms + correlated pair confirms
- HOLD: Divergence detected, conflicting correlations, or weak strength differential"""
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """Generate mock response based on input data."""
        data = input_data.get("data", {})
        
        dxy_impact = data.get("dxy_expected_impact", "NEUTRAL")
        correlation_confirms = data.get("correlation_confirms", False)
        divergence = data.get("divergence_detected", False)
        
        # Phase 7: Consider currency strength
        strength_available = data.get("currency_strength_available", False)
        strength_signal = data.get("strength_signal", "NEUTRAL")
        strength_differential = data.get("strength_differential", 0.0)
        
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
        
        # Follow DXY impact and currency strength
        flags = ["MOCK_MODE"]
        
        # Phase 7: Consider both DXY and currency strength
        bullish_signals = 0
        bearish_signals = 0
        
        if dxy_impact == "BULLISH":
            bullish_signals += 1
            flags.append("DXY_ALIGNED")
        elif dxy_impact == "BEARISH":
            bearish_signals += 1
            flags.append("DXY_ALIGNED")
        
        if strength_available:
            if strength_signal in ("BULLISH", "STRONG_BULLISH") or strength_differential > 20:
                bullish_signals += 1
                flags.append("STRENGTH_CONFIRMS")
            elif strength_signal in ("BEARISH", "STRONG_BEARISH") or strength_differential < -20:
                bearish_signals += 1
                flags.append("STRENGTH_CONFIRMS")
        
        if correlation_confirms:
            # Add confirmation based on existing direction
            if bullish_signals > bearish_signals:
                bullish_signals += 1
            elif bearish_signals > bullish_signals:
                bearish_signals += 1
            flags.append("CORRELATION_CONFIRMS")
        
        # Determine signal
        if bullish_signals >= 2:
            signal = "LONG"
            confidence_float = 0.7 if bullish_signals >= 3 else 0.55
            confidence = ConfidenceLevel.MEDIUM if confidence_float >= 0.6 else ConfidenceLevel.LOW
        elif bearish_signals >= 2:
            signal = "SHORT"
            confidence_float = 0.7 if bearish_signals >= 3 else 0.55
            confidence = ConfidenceLevel.MEDIUM if confidence_float >= 0.6 else ConfidenceLevel.LOW
        else:
            signal = "HOLD"
            confidence_float = 0.3
            confidence = ConfidenceLevel.LOW
            flags.append("NO_CLEAR_SIGNAL")
        
        sig = AgentSignal(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=f"Mock: {symbol} dxy={dxy_impact} strength={strength_signal} diff={strength_differential:.0f}",
            data_status=DataStatus.REAL,  # DXY is real from Yahoo Finance
            flags=flags,
        )
        sig.confidence_float = confidence_float
        return sig
