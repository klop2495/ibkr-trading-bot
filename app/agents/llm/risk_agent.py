"""
Risk Assessment Agent.

Phase 6 Update: Has VETO power but only based on REAL data.
If calendar is mock, cannot claim "high-impact event soon".
"""

from typing import Any, Dict

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.agents.llm.data_status import DataStatus
from app.models.confidence import ConfidenceLevel


class RiskAgent(BaseLLMAgent):
    """
    Risk management specialist.
    
    UNIQUE ROLE: Has VETO power to block trades.
    
    Focuses on:
    - Volatility assessment (from real IB Gateway data)
    - Session timing (computed locally - always REAL)
    - Current drawdown state (from account - REAL)
    - News event proximity (only if calendar is REAL)
    - Overall risk environment
    
    VETO only based on REAL data:
    - Can veto for high volatility (IB Gateway data)
    - Can veto for bad session timing (computed)
    - Can veto for drawdown limits (account data)
    - CANNOT veto for "event risk" if calendar is mock
    
    Does NOT provide: specific prices, SL/TP levels, lot sizes.
    """
    
    name = "RiskAgent"
    version = "2.0"
    weight = 0.25  # 25% contribution - high weight for risk management
    
    def check_data_status(self, context: Dict[str, Any], symbol: str) -> DataStatus:
        """
        RiskAgent has access to multiple data sources.
        
        REAL sources (always available):
        - Volatility from IB Gateway
        - Session timing (computed)
        - Account drawdown
        
        PARTIAL/MISSING sources:
        - Economic calendar (mock)
        
        Overall: REAL if we have volatility/session data.
        """
        risk = context.get("risk", {})
        session = context.get("session", {})
        account = context.get("account", {})
        
        # Must have at least session data (always computed)
        if not session.get("current"):
            return DataStatus.PARTIAL
        
        # Check if we have volatility data from IB Gateway
        if risk.get("volatility_regime") in (None, "UNKNOWN"):
            return DataStatus.PARTIAL
        
        return DataStatus.REAL
    
    def _has_real_calendar(self, context: Dict[str, Any]) -> bool:
        """Check if economic calendar is REAL (not mock)."""
        macro = context.get("macro", {})
        source_health = context.get("source_health", {})
        calendar_health = source_health.get("economic_calendar", {})
        
        # Default to False (mock) if not explicitly marked as real
        return calendar_health.get("mock_mode", True) is False
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        """
        Prepare categorical risk data.
        
        IMPORTANT: If calendar is mock, do NOT include event risk.
        """
        risk = context.get("risk", {})
        session = context.get("session", {})
        account = context.get("account", {})
        calendar = context.get("economic_calendar", [])
        
        # Check if calendar is REAL
        has_real_calendar = self._has_real_calendar(context)
        
        # Only check for high-impact events if calendar is REAL
        high_impact_soon = False
        if has_real_calendar:
            high_impact_soon = any(
                e.get("impact") == "HIGH" and e.get("timing_category") in ("IMMINENT", "SOON")
                for e in calendar
            )
        
        return {
            "symbol": symbol,
            "agent": self.name,
            "data": {
                # Volatility (from IB Gateway - REAL)
                "volatility_regime": risk.get("volatility_regime", "NORMAL"),  # LOW/NORMAL/HIGH/EXTREME
                "volatility_expanding": risk.get("volatility_expanding", False),
                "recent_spike": risk.get("recent_volatility_spike", False),
                
                # Session timing (computed locally - REAL)
                "current_session": session.get("current", "INACTIVE"),  # TOKYO/LONDON/NEW_YORK/OVERLAP/INACTIVE
                "optimal_session": session.get("optimal_for_symbol", False),
                "session_start_near": session.get("session_start_near", False),
                "session_end_near": session.get("session_end_near", False),
                
                # Account state (from broker - REAL)
                "drawdown_level": account.get("drawdown_level", "NORMAL"),  # NONE/SMALL/MEDIUM/LARGE/CRITICAL
                "daily_loss_limit_near": account.get("daily_loss_limit_near", False),
                "open_exposure": account.get("open_exposure_level", "NONE"),  # NONE/LOW/MEDIUM/HIGH
                
                # Event risk (ONLY if calendar is REAL)
                "high_impact_event_soon": high_impact_soon,
                "calendar_is_real": has_real_calendar,  # Flag for transparency
                "weekend_approaching": session.get("weekend_approaching", False),
                
                # Overall risk environment
                "market_stress": risk.get("market_stress", "NORMAL"),  # LOW/NORMAL/ELEVATED/HIGH
                "liquidity_condition": risk.get("liquidity", "NORMAL"),  # THIN/NORMAL/DEEP
            }
        }
    
    def get_system_prompt(self) -> str:
        return """You are a Risk Management Agent for forex trading.

Your role: Assess whether current conditions are suitable for trading.
You have VETO power - you can block trades when risk is elevated.

INPUT: You receive categorical risk data (volatility, session, account state).

OUTPUT: Respond with JSON only:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "confidence_float": 0.0 to 1.0,
  "risk_veto": true | false,
  "reasoning": "Brief explanation (max 150 chars)",
  "flags": ["FLAG1", "FLAG2"]
}

CONFIDENCE_FLOAT GUIDELINES:
- 0.9: All risk factors green, optimal conditions
- 0.7: Minor concerns, generally acceptable
- 0.5: Some elevated risk
- 0.3: High risk, recommend reduced position

RISK_VETO:
- Set to TRUE only for SEVERE risk conditions
- You must have REAL data to justify veto
- If calendar_is_real=false, CANNOT veto for "event risk"

VETO TRIGGERS (set risk_veto=true):
- Volatility regime EXTREME
- Drawdown level CRITICAL
- REAL high-impact event IMMINENT (only if calendar_is_real=true)

NON-VETO CONCERNS (lower confidence, but don't veto):
- High volatility (not extreme)
- Session suboptimal
- Weekend approaching

RULES:
1. NEVER mention specific prices, SL/TP levels, or position sizes
2. Your primary job is to PROTECT capital
3. Use HOLD liberally when risk is elevated
4. You don't pick direction - use LONG/SHORT only to CONFIRM other agents
5. Flags: HIGH_VOLATILITY, EVENT_RISK, DRAWDOWN_WARNING, SESSION_OPTIMAL/SUBOPTIMAL, etc."""
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """Generate mock response based on input data."""
        data = input_data.get("data", {})
        
        flags = ["MOCK_MODE"]
        risk_veto = False
        
        # Check for VETO triggers (only with REAL data)
        veto_reasons = []
        
        volatility = data.get("volatility_regime", "NORMAL")
        if volatility == "EXTREME":
            veto_reasons.append("EXTREME_VOLATILITY")
            flags.append("EXTREME_VOLATILITY")
            risk_veto = True
        elif volatility == "HIGH":
            flags.append("HIGH_VOLATILITY")
        
        drawdown = data.get("drawdown_level", "NORMAL")
        if drawdown == "CRITICAL":
            veto_reasons.append("CRITICAL_DRAWDOWN")
            flags.append("CRITICAL_DRAWDOWN")
            risk_veto = True
        elif drawdown == "LARGE":
            flags.append("DRAWDOWN_WARNING")
        
        # Only check event risk if calendar is REAL
        calendar_real = data.get("calendar_is_real", False)
        if calendar_real and data.get("high_impact_event_soon"):
            veto_reasons.append("EVENT_IMMINENT")
            flags.append("EVENT_RISK_REAL")
            risk_veto = True
        
        session = data.get("current_session", "INACTIVE")
        if session == "INACTIVE":
            flags.append("SESSION_INACTIVE")
        
        if data.get("daily_loss_limit_near"):
            flags.append("LOSS_LIMIT_NEAR")
        
        # Decision
        if risk_veto:
            sig = AgentSignal(
                agent_name=self.name,
                signal="HOLD",
                confidence=ConfidenceLevel.HIGH,  # High confidence in veto
                reasoning=f"VETO: {', '.join(veto_reasons[:2])}",
                data_status=DataStatus.REAL,
                risk_veto=True,
                flags=flags,
            )
            sig.confidence_float = 0.9
            return sig
        
        # Non-veto concerns
        if volatility == "HIGH" or drawdown in ("MEDIUM", "LARGE") or session == "INACTIVE":
            sig = AgentSignal(
                agent_name=self.name,
                signal="HOLD",
                confidence=ConfidenceLevel.MEDIUM,
                reasoning=f"Risk elevated but not critical",
                data_status=DataStatus.REAL,
                risk_veto=False,
                flags=flags,
            )
            sig.confidence_float = 0.5
            return sig
        
        # Risk is acceptable - neutral confirmation
        optimal = data.get("optimal_session", False)
        if optimal:
            flags.append("SESSION_OPTIMAL")
            confidence_float = 0.7
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence_float = 0.5
            confidence = ConfidenceLevel.LOW
        
        sig = AgentSignal(
            agent_name=self.name,
            signal="HOLD",  # RiskAgent doesn't pick direction
            confidence=confidence,
            reasoning=f"Risk acceptable, session={session}",
            data_status=DataStatus.REAL,
            risk_veto=False,
            flags=flags,
        )
        sig.confidence_float = confidence_float
        return sig
