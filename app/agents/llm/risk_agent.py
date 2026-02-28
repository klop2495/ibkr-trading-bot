"""
Risk Assessment Agent.

Phase 6 Update: Has VETO power but only based on REAL data.
Phase 7 Update: Integrates VIXFetcher and VolatilityRegimeDetector.

If calendar is mock, cannot claim "high-impact event soon".
"""

import logging
from typing import Any, Dict, Optional

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.agents.llm.data_status import DataStatus
from app.models.confidence import ConfidenceLevel

# Phase 7: Import volatility regime detector
try:
    from app.risk.volatility_regime import VolatilityRegimeDetector
    VOLATILITY_REGIME_AVAILABLE = True
except ImportError:
    VOLATILITY_REGIME_AVAILABLE = False


logger = logging.getLogger(__name__)


class RiskAgent(BaseLLMAgent):
    """
    Risk management specialist.
    
    UNIQUE ROLE: Has VETO power to block trades.
    
    Focuses on:
    - Volatility assessment (from real IB Gateway data)
    - Volatility regime detection (Phase 7: using VolatilityRegimeDetector)
    - VIX analysis (Phase 7: market fear index)
    - Session timing (computed locally - always REAL)
    - Current drawdown state (from account - REAL)
    - News event proximity (only if calendar is REAL)
    - Overall risk environment
    
    VETO only based on REAL data:
    - Can veto for extreme volatility regime (from ATR analysis)
    - Can veto for extreme VIX (market fear)
    - Can veto for bad session timing (computed)
    - Can veto for drawdown limits (account data)
    - CANNOT veto for "event risk" if calendar is mock
    
    Does NOT provide: specific prices, SL/TP levels, lot sizes.
    """
    
    name = "RiskAgent"
    version = "2.1"  # Phase 7: version bump
    weight = 0.25  # 25% contribution - high weight for risk management
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Phase 7: Initialize volatility regime detector
        self._vol_detector: Optional[VolatilityRegimeDetector] = None
        if VOLATILITY_REGIME_AVAILABLE:
            try:
                self._vol_detector = VolatilityRegimeDetector()
                logger.info("RiskAgent: VolatilityRegimeDetector initialized")
            except Exception as e:
                logger.warning(f"RiskAgent: Failed to init volatility detector: {e}")
    
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
        # Must have at least session data (always computed)
        if not session.get("current"):
            return DataStatus.PARTIAL
        
        # Check if we have volatility data from IB Gateway
        if risk.get("volatility_regime") in (None, "UNKNOWN"):
            return DataStatus.PARTIAL
        
        return DataStatus.REAL
    
    def _has_real_calendar(self, context: Dict[str, Any]) -> bool:
        """Check if economic calendar is REAL (not mock)."""
        source_health = context.get("source_health", {})
        calendar_health = source_health.get("economic_calendar", {})
        
        # Default to False (mock) if not explicitly marked as real
        return calendar_health.get("mock_mode", True) is False
    
    def _detect_volatility_regime(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 7: Detect volatility regime from ATR data.
        
        Returns regime context dict.
        """
        if not self._vol_detector:
            return {}
        
        atr_history = context.get("atr_history", [])
        close_prices = context.get("close_prices", [])
        # Need sufficient data
        if not atr_history or len(atr_history) < 10 or not close_prices or len(close_prices) < 10:
            return {}
        
        try:
            # Detect regime
            regime = self._vol_detector.detect(atr_history, close_prices)
            
            # Get multipliers
            position_mult = self._vol_detector.get_position_multiplier(regime)
            sl_mult = self._vol_detector.get_sl_multiplier(regime)
            should_trade = self._vol_detector.should_trade(regime)
            
            return {
                "volatility_regime_detected": regime.value,
                "volatility_regime_tradeable": should_trade,
                "volatility_position_multiplier": position_mult,
                "volatility_sl_multiplier": sl_mult,
            }
            
        except Exception as e:
            logger.warning(f"RiskAgent: Volatility regime detection failed: {e}")
            return {}
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        """
        Prepare categorical risk data.
        
        IMPORTANT: If calendar is mock, do NOT include event risk.
        Phase 7: Integrates volatility regime detection and VIX.
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
        
        # Phase 7: Detect volatility regime
        vol_regime_data = self._detect_volatility_regime(context)
        
        # Use detected regime if available, fallback to context
        volatility_regime = vol_regime_data.get(
            "volatility_regime_detected",
            risk.get("volatility_regime", "NORMAL")
        )
        regime_tradeable = vol_regime_data.get("volatility_regime_tradeable", True)
        position_mult = vol_regime_data.get("volatility_position_multiplier", 1.0)
        sl_mult = vol_regime_data.get("volatility_sl_multiplier", 1.0)
        
        # Phase 7: Get VIX data from risk context
        vix_value = risk.get("vix_value")
        vix_regime = risk.get("vix_regime", "GREED")
        vix_risk_mult = risk.get("vix_risk_multiplier", 1.0)
        vix_is_mock = risk.get("vix_is_mock", True)
        vix_elevated = vix_regime in ("FEAR", "EXTREME_FEAR") if vix_regime else False
        
        return {
            "symbol": symbol,
            "agent": self.name,
            "data": {
                # Volatility (Phase 7: enhanced with regime detection)
                "volatility_regime": volatility_regime,  # LOW/NORMAL/HIGH/EXTREME
                "volatility_regime_tradeable": regime_tradeable,
                "volatility_position_multiplier": position_mult,
                "volatility_sl_multiplier": sl_mult,
                "volatility_expanding": risk.get("volatility_expanding", False),
                "recent_spike": risk.get("recent_volatility_spike", False),
                
                # Phase 7: VIX (market fear index)
                "vix_value": vix_value,
                "vix_regime": vix_regime,  # EXTREME_GREED/GREED/FEAR/EXTREME_FEAR
                "vix_risk_multiplier": vix_risk_mult,
                "vix_elevated": vix_elevated,
                "vix_is_mock": vix_is_mock,
                
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

INPUT: You receive categorical risk data (volatility regime, VIX, session, account state).

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
- Volatility regime EXTREME (volatility_regime_tradeable=false)
- VIX regime EXTREME_FEAR (vix_value > 30, unless vix_is_mock=true)
- Drawdown level CRITICAL
- REAL high-impact event IMMINENT (only if calendar_is_real=true)

NON-VETO CONCERNS (lower confidence, but don't veto):
- High volatility (not extreme)
- VIX in FEAR zone (elevated but not extreme)
- Session suboptimal
- Weekend approaching

RULES:
1. NEVER mention specific prices, SL/TP levels, or position sizes
2. Your primary job is to PROTECT capital
3. Use HOLD liberally when risk is elevated
4. You don't pick direction - use LONG/SHORT only to CONFIRM other agents
5. Flags: EXTREME_VOLATILITY, VIX_ELEVATED, EVENT_RISK, DRAWDOWN_WARNING, SESSION_OPTIMAL/SUBOPTIMAL, etc."""
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """Generate mock response based on input data."""
        data = input_data.get("data", {})
        
        flags = ["MOCK_MODE"]
        risk_veto = False
        
        # Check for VETO triggers (only with REAL data)
        veto_reasons = []
        
        # Phase 7: Check volatility regime
        volatility = data.get("volatility_regime", "NORMAL")
        regime_tradeable = data.get("volatility_regime_tradeable", True)
        
        if volatility == "EXTREME" or not regime_tradeable:
            veto_reasons.append("EXTREME_VOLATILITY")
            flags.append("EXTREME_VOLATILITY")
            risk_veto = True
        elif volatility == "HIGH":
            flags.append("HIGH_VOLATILITY")
        
        # Phase 7: Check VIX (only if not mock)
        vix_regime = data.get("vix_regime", "GREED")
        vix_is_mock = data.get("vix_is_mock", True)
        vix_elevated = data.get("vix_elevated", False)
        
        if not vix_is_mock and vix_regime == "EXTREME_FEAR":
            veto_reasons.append("VIX_EXTREME_FEAR")
            flags.append("VIX_EXTREME_FEAR")
            risk_veto = True
        elif vix_elevated:
            flags.append("VIX_ELEVATED")
        
        # Check drawdown
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
        if volatility == "HIGH" or drawdown in ("MEDIUM", "LARGE") or session == "INACTIVE" or vix_elevated:
            sig = AgentSignal(
                agent_name=self.name,
                signal="HOLD",
                confidence=ConfidenceLevel.MEDIUM,
                reasoning=f"Risk elevated but not critical (vol={volatility} vix={vix_regime})",
                data_status=DataStatus.REAL,
                risk_veto=False,
                flags=flags,
            )
            sig.confidence_float = 0.5
            return sig
        
        # Risk is acceptable - neutral confirmation
        optimal = data.get("optimal_session", False)
        position_mult = data.get("volatility_position_multiplier", 1.0)
        
        if optimal and position_mult >= 1.0:
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
            reasoning=f"Risk acceptable, session={session}, pos_mult={position_mult:.1f}",
            data_status=DataStatus.REAL,
            risk_veto=False,
            flags=flags,
        )
        sig.confidence_float = confidence_float
        return sig
