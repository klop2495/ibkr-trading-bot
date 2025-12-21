"""
Risk Assessment Agent.

Phase 3: Analyzes volatility, session timing, drawdown risk.
Returns categorical signals focused on risk management.
"""

from typing import Any, Dict

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.models.confidence import ConfidenceLevel


class RiskAgent(BaseLLMAgent):
    """
    Risk management specialist.
    
    Focuses on:
    - Volatility assessment
    - Session timing (London, NY overlap)
    - Current drawdown state
    - News event proximity
    - Overall risk environment
    
    Does NOT provide: specific prices, SL/TP levels, lot sizes.
    Acts as a "gatekeeper" - can recommend HOLD to reduce exposure.
    """
    
    name = "RiskAgent"
    version = "1.0"
    weight = 0.25  # 25% contribution - high weight for risk management
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        """
        Prepare categorical risk data.
        
        Focuses on factors that affect trade safety.
        """
        risk = context.get("risk", {})
        session = context.get("session", {})
        account = context.get("account", {})
        calendar = context.get("economic_calendar", [])
        
        # Check for high-impact events soon
        high_impact_soon = any(
            e.get("impact") == "HIGH" and e.get("timing_category") in ("IMMINENT", "SOON")
            for e in calendar
        )
        
        return {
            "symbol": symbol,
            "agent": self.name,
            "data": {
                # Volatility
                "volatility_regime": risk.get("volatility_regime", "NORMAL"),  # LOW/NORMAL/HIGH/EXTREME
                "volatility_expanding": risk.get("volatility_expanding", False),
                "recent_spike": risk.get("recent_volatility_spike", False),
                
                # Session timing
                "current_session": session.get("current", "INACTIVE"),  # TOKYO/LONDON/NEW_YORK/OVERLAP/INACTIVE
                "optimal_session": session.get("optimal_for_symbol", False),
                "session_start_near": session.get("session_start_near", False),
                "session_end_near": session.get("session_end_near", False),
                
                # Account state
                "drawdown_level": account.get("drawdown_level", "NORMAL"),  # NONE/SMALL/MEDIUM/LARGE/CRITICAL
                "daily_loss_limit_near": account.get("daily_loss_limit_near", False),
                "open_exposure": account.get("open_exposure_level", "NONE"),  # NONE/LOW/MEDIUM/HIGH
                
                # Event risk
                "high_impact_event_soon": high_impact_soon,
                "weekend_approaching": session.get("weekend_approaching", False),
                
                # Overall risk environment
                "market_stress": risk.get("market_stress", "NORMAL"),  # LOW/NORMAL/ELEVATED/HIGH
                "liquidity_condition": risk.get("liquidity", "NORMAL"),  # THIN/NORMAL/DEEP
            }
        }
    
    def get_system_prompt(self) -> str:
        return """You are a Risk Management Agent for forex trading.

Your role: Assess whether current conditions are suitable for trading.
You act as a GATEKEEPER - recommend HOLD when risk is elevated.

INPUT: You receive categorical risk data (volatility, session, account state).

OUTPUT: Respond with JSON only:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "reasoning": "Brief explanation (max 150 chars)",
  "flags": ["FLAG1", "FLAG2"]
}

RULES:
1. NEVER mention specific prices, SL/TP levels, or position sizes
2. Your primary job is to PROTECT capital
3. Recommend HOLD liberally when risk is elevated
4. You don't pick direction - use LONG/SHORT only to CONFIRM other agents
5. Flags: HIGH_VOLATILITY, EVENT_RISK, DRAWDOWN_WARNING, SESSION_OPTIMAL/SUBOPTIMAL, etc.

SIGNAL GUIDELINES:
- LONG/SHORT: Only when risk environment is favorable (confirm other agents)
- HOLD: Default response when ANY risk factor is elevated

HOLD TRIGGERS (recommend HOLD):
- Volatility regime HIGH or EXTREME
- Drawdown level LARGE or CRITICAL
- High-impact event IMMINENT
- Session INACTIVE or suboptimal
- Weekend approaching with exposure
- Daily loss limit near

CONFIDENCE GUIDELINES:
- HIGH: All risk factors green, optimal conditions
- MEDIUM: Some minor concerns, generally acceptable
- LOW: Elevated risk, consider reduced position"""
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """Generate mock response based on input data."""
        data = input_data.get("data", {})
        
        flags = ["MOCK_MODE"]
        
        # Check for HOLD triggers
        hold_reasons = []
        
        volatility = data.get("volatility_regime", "NORMAL")
        if volatility in ("HIGH", "EXTREME"):
            hold_reasons.append("HIGH_VOLATILITY")
            flags.append("HIGH_VOLATILITY")
        
        drawdown = data.get("drawdown_level", "NORMAL")
        if drawdown in ("LARGE", "CRITICAL"):
            hold_reasons.append("DRAWDOWN_WARNING")
            flags.append("DRAWDOWN_WARNING")
        
        if data.get("high_impact_event_soon"):
            hold_reasons.append("EVENT_RISK")
            flags.append("EVENT_RISK")
        
        session = data.get("current_session", "INACTIVE")
        if session == "INACTIVE":
            hold_reasons.append("SESSION_INACTIVE")
            flags.append("SESSION_INACTIVE")
        
        if data.get("daily_loss_limit_near"):
            hold_reasons.append("LOSS_LIMIT_NEAR")
            flags.append("LOSS_LIMIT_NEAR")
        
        # Decision
        if hold_reasons:
            return AgentSignal(
                agent_name=self.name,
                signal="HOLD",
                confidence=ConfidenceLevel.HIGH,  # High confidence in risk assessment
                reasoning=f"Mock: Risk elevated - {', '.join(hold_reasons[:2])}",
                flags=flags,
            )
        
        # Risk is acceptable - neutral confirmation
        optimal = data.get("optimal_session", False)
        if optimal:
            flags.append("SESSION_OPTIMAL")
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW
        
        return AgentSignal(
            agent_name=self.name,
            signal="HOLD",  # RiskAgent doesn't pick direction in mock mode
            confidence=confidence,
            reasoning=f"Mock: Risk acceptable, session={session}",
            flags=flags,
        )
