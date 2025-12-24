"""
Score-based Aggregator for LLM agents.

Phase 6: Replaces Quorum Voting with Score-based aggregation.

Key changes:
- LLM контур выдает llm_score ∈ [-1..+1] (направление и сила)
- Agents with data_status=MISSING are EXCLUDED from calculation
- RiskAgent veto still works but only with REAL data
- No more "unanimous veto" - just weighted scores
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.confidence import ConfidenceLevel
from app.agents.llm.base_agent import AgentSignal
from app.agents.llm.data_status import DataStatus


logger = logging.getLogger(__name__)


# Thresholds
LLM_SIGNAL_THRESHOLD = 0.15  # |llm_score| > threshold → LONG/SHORT


@dataclass
class LLMContourResult:
    """
    Result from LLM agents aggregation.
    
    This is the OUTPUT of the LLM контур, ready for 60/40 blend with Rules.
    """
    # Core output
    llm_score: float  # -1.0 to +1.0 (direction and strength)
    llm_signal: str   # LONG/SHORT/HOLD derived from score
    llm_confidence: float  # 0.0 to 1.0 (normalized confidence)
    llm_active_weight: float  # 0.0 to 1.0 (how much of total weight participated)
    
    # Risk veto
    risk_veto: bool
    risk_veto_reason: Optional[str]
    
    # Agent breakdown
    agent_scores: Dict[str, float]  # agent -> score contribution
    agent_signals: Dict[str, str]   # agent -> signal
    agent_confidences: Dict[str, float]  # agent -> confidence_float
    agent_data_status: Dict[str, str]  # agent -> data_status
    
    # Participation stats
    participating_agents: int
    abstaining_agents: int
    total_agents: int
    
    # Flags
    flags: List[str] = field(default_factory=list)
    
    # Timestamp
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict:
        """Convert to dict for storage."""
        return {
            "llm_score": round(self.llm_score, 4),
            "llm_signal": self.llm_signal,
            "llm_confidence": round(self.llm_confidence, 4),
            "llm_active_weight": round(self.llm_active_weight, 4),
            "risk_veto": self.risk_veto,
            "risk_veto_reason": self.risk_veto_reason,
            "agent_scores": {k: round(v, 4) for k, v in self.agent_scores.items()},
            "agent_signals": self.agent_signals,
            "agent_confidences": {k: round(v, 3) for k, v in self.agent_confidences.items()},
            "agent_data_status": self.agent_data_status,
            "participating_agents": self.participating_agents,
            "abstaining_agents": self.abstaining_agents,
            "total_agents": self.total_agents,
            "flags": self.flags[:15],
        }


class ScoreAggregator:
    """
    Aggregates LLM agent signals into a single score.
    
    Formula:
    1. For each agent with data_status != MISSING:
       - agent_vote = +1 (LONG), -1 (SHORT), 0 (HOLD)
       - effective_weight = weight * confidence_float
    
    2. llm_score = sum(agent_vote * effective_weight) / sum(effective_weight)
       If sum(effective_weight) == 0 → llm_score = 0
    
    3. llm_signal:
       - LONG if llm_score > LLM_SIGNAL_THRESHOLD
       - SHORT if llm_score < -LLM_SIGNAL_THRESHOLD
       - else HOLD
    
    4. RiskAgent veto (if risk_veto=True): llm_signal → HOLD, add RISK_VETO flag
    """
    
    # Default agent weights (should sum to ~1.0)
    DEFAULT_WEIGHTS = {
        "TechnicalAgent": 0.25,
        "MacroAgent": 0.20,
        "SentimentAgent": 0.15,
        "CorrelationAgent": 0.15,
        "RiskAgent": 0.25,
    }
    
    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        signal_threshold: float = LLM_SIGNAL_THRESHOLD,
    ):
        """
        Initialize aggregator.
        
        Args:
            weights: Agent weights (default uses DEFAULT_WEIGHTS).
            signal_threshold: Score threshold for LONG/SHORT signal.
        """
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.signal_threshold = signal_threshold
    
    def aggregate(self, signals: List[AgentSignal]) -> LLMContourResult:
        """
        Aggregate agent signals into LLM contour result.
        
        Args:
            signals: List of AgentSignal from each agent.
        
        Returns:
            LLMContourResult with llm_score, llm_signal, etc.
        """
        if not signals:
            return self._empty_result()
        
        # Separate participating vs abstaining agents
        participating = []
        abstaining = []
        risk_veto = False
        risk_veto_reason = None
        
        for sig in signals:
            if sig.data_status == DataStatus.MISSING:
                abstaining.append(sig)
            else:
                participating.append(sig)
                
                # Check for RiskAgent veto
                if sig.agent_name == "RiskAgent" and sig.risk_veto:
                    risk_veto = True
                    risk_veto_reason = self._extract_veto_reason(sig)
        
        # Calculate scores only from participating agents
        agent_scores: Dict[str, float] = {}
        agent_signals: Dict[str, str] = {}
        agent_confidences: Dict[str, float] = {}
        agent_data_status: Dict[str, str] = {}
        
        total_weighted_score = 0.0
        total_effective_weight = 0.0
        total_possible_weight = sum(self.weights.get(sig.agent_name, 0.1) for sig in signals)
        
        all_flags: List[str] = []
        
        for sig in signals:
            weight = self.weights.get(sig.agent_name, 0.1)
            
            agent_signals[sig.agent_name] = sig.signal
            agent_confidences[sig.agent_name] = sig.confidence_float
            agent_data_status[sig.agent_name] = sig.data_status.value
            all_flags.extend(sig.flags)
            
            if sig.data_status == DataStatus.MISSING:
                agent_scores[sig.agent_name] = 0.0
                continue
            
            # Vote: +1 LONG, -1 SHORT, 0 HOLD
            if sig.signal == "LONG":
                vote = 1.0
            elif sig.signal == "SHORT":
                vote = -1.0
            else:
                vote = 0.0
            
            effective_weight = weight * sig.confidence_float
            weighted_score = vote * effective_weight
            
            agent_scores[sig.agent_name] = weighted_score
            total_weighted_score += weighted_score
            total_effective_weight += effective_weight
        
        # Calculate llm_score
        if total_effective_weight > 0:
            llm_score = total_weighted_score / total_effective_weight
        else:
            llm_score = 0.0
        
        # Clamp to [-1, 1]
        llm_score = max(-1.0, min(1.0, llm_score))
        
        # Calculate llm_confidence (normalized effective weight)
        if total_possible_weight > 0:
            llm_confidence = total_effective_weight / total_possible_weight
        else:
            llm_confidence = 0.0
        
        # Calculate active weight ratio
        participating_weight = sum(self.weights.get(sig.agent_name, 0.1) for sig in participating)
        llm_active_weight = participating_weight / total_possible_weight if total_possible_weight > 0 else 0.0
        
        # Determine llm_signal
        if risk_veto:
            llm_signal = "HOLD"
            all_flags.insert(0, "RISK_VETO")
            logger.info(f"ScoreAggregator: RiskAgent VETO - {risk_veto_reason}")
        elif llm_score > self.signal_threshold:
            llm_signal = "LONG"
        elif llm_score < -self.signal_threshold:
            llm_signal = "SHORT"
        else:
            llm_signal = "HOLD"
        
        # Add participation flags
        if len(abstaining) > 0:
            all_flags.append(f"AGENTS_ABSTAINING:{len(abstaining)}")
        if llm_active_weight < 0.5:
            all_flags.append("LOW_LLM_PARTICIPATION")
        
        # Dedupe flags
        unique_flags = list(dict.fromkeys(all_flags))
        
        logger.info(
            f"ScoreAggregator: llm_score={llm_score:.3f} llm_signal={llm_signal} "
            f"participating={len(participating)}/{len(signals)} "
            f"active_weight={llm_active_weight:.2f} veto={risk_veto}"
        )
        
        return LLMContourResult(
            llm_score=llm_score,
            llm_signal=llm_signal,
            llm_confidence=llm_confidence,
            llm_active_weight=llm_active_weight,
            risk_veto=risk_veto,
            risk_veto_reason=risk_veto_reason,
            agent_scores=agent_scores,
            agent_signals=agent_signals,
            agent_confidences=agent_confidences,
            agent_data_status=agent_data_status,
            participating_agents=len(participating),
            abstaining_agents=len(abstaining),
            total_agents=len(signals),
            flags=unique_flags[:15],
        )
    
    def _extract_veto_reason(self, sig: AgentSignal) -> str:
        """Extract veto reason from RiskAgent signal."""
        # Look for specific flags
        for flag in sig.flags:
            if flag in ("EXTREME_VOLATILITY", "CRITICAL_DRAWDOWN", "EVENT_IMMINENT"):
                return flag
        return sig.reasoning[:50] if sig.reasoning else "RISK_VETO"
    
    def _empty_result(self) -> LLMContourResult:
        """Return empty result when no signals provided."""
        return LLMContourResult(
            llm_score=0.0,
            llm_signal="HOLD",
            llm_confidence=0.0,
            llm_active_weight=0.0,
            risk_veto=False,
            risk_veto_reason=None,
            agent_scores={},
            agent_signals={},
            agent_confidences={},
            agent_data_status={},
            participating_agents=0,
            abstaining_agents=0,
            total_agents=0,
            flags=["NO_AGENTS"],
        )


# Legacy compatibility alias
WeightedAggregator = ScoreAggregator
