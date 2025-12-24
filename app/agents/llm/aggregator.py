"""
Weighted Aggregator for LLM agent signals.

Phase 6: Updated to work with data_status and confidence_float.
This file is kept for backwards compatibility with existing tests.

For new code, use ScoreAggregator from score_aggregator.py.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from app.models.confidence import ConfidenceLevel, confidence_to_float
from app.agents.llm.base_agent import AgentSignal
from app.agents.llm.data_status import DataStatus
from app.agents.config import (
    QUORUM_THRESHOLD_WITH_ENTRY,
    QUORUM_THRESHOLD_NO_ENTRY,
    MIN_ACTIVE_WEIGHT,
    RISK_MOD_CAP_NO_ENTRY,
    RISK_MOD_MIN,
    RISK_MOD_MAX,
    RISK_VETO_ENABLED,
)


logger = logging.getLogger(__name__)


class VoteType(str, Enum):
    """Vote type for quorum calculation."""
    ALLOW = "allow"    # Agent approves the trade
    BLOCK = "block"    # Agent explicitly blocks the trade
    ABSTAIN = "abstain"  # Agent is uncertain / no opinion


@dataclass
class AggregatedDecision:
    """
    Final aggregated decision from all agents.
    
    Contains quorum voting results and agent details.
    """
    # Direction signal (for execution)
    signal: str  # LONG, SHORT, HOLD
    confidence: ConfidenceLevel
    
    # Quorum voting results
    trade_allowed: bool
    approval_ratio: float  # 0.0 to 1.0 (allow_score / active_weight)
    active_weight: float   # Sum of weights for ALLOW + BLOCK votes
    
    # Score breakdown
    allow_score: float
    block_score: float
    abstain_weight: float
    
    # Veto and rejection reason
    risk_blocked: bool
    veto_reason: Optional[str]  # "RISK_VETO", "NO_QUORUM", "LOW_APPROVAL", "NO_CANDIDATE"
    
    # For execution
    risk_modifier: float  # 0.5 to 1.0
    
    # Legacy compatibility
    vote_score: float  # -1.0 to +1.0 for direction (kept for hybrid calc)
    consensus_level: str  # STRONG, MODERATE, WEAK, NONE
    
    # Agent breakdown
    agent_signals: Dict[str, str]  # agent_name -> signal (LONG/SHORT/HOLD)
    agent_votes: Dict[str, str]    # agent_name -> vote (ALLOW/BLOCK/ABSTAIN)
    agent_confidences: Dict[str, str]  # agent_name -> confidence
    
    # Flags aggregated from all agents
    flags: List[str] = field(default_factory=list)
    
    # Metadata
    agents_count: int = 0
    quorum_threshold: float = 0.6
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict:
        """Convert to dict for storage in parallel_decisions."""
        return {
            "signal": self.signal,
            "confidence": self.confidence.value,
            "trade_allowed": self.trade_allowed,
            "approval_ratio": round(self.approval_ratio, 4),
            "active_weight": round(self.active_weight, 4),
            "allow_score": round(self.allow_score, 4),
            "block_score": round(self.block_score, 4),
            "risk_blocked": self.risk_blocked,
            "veto_reason": self.veto_reason,
            "risk_modifier": round(self.risk_modifier, 4),
            "vote_score": round(self.vote_score, 4),
            "consensus_level": self.consensus_level,
            "agent_signals": self.agent_signals,
            "agent_votes": self.agent_votes,
            "agent_confidences": self.agent_confidences,
            "flags": self.flags[:10],  # Limit flags for storage
            "agents_count": self.agents_count,
            "quorum_threshold": round(self.quorum_threshold, 4),
        }


class WeightedAggregator:
    """
    Aggregates LLM agent signals using weighted quorum voting.
    
    Phase 6 Update:
    - Agents with data_status=MISSING are treated as ABSTAIN
    - Uses confidence_float if available
    - RiskAgent veto only if data_status != MISSING
    
    Default agent weights (must sum to ~1.0):
    - TechnicalAgent: 0.25
    - MacroAgent: 0.20
    - SentimentAgent: 0.15
    - CorrelationAgent: 0.15
    - RiskAgent: 0.25
    """
    
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
        risk_veto_enabled: bool = RISK_VETO_ENABLED,
    ):
        """
        Initialize aggregator.
        
        Args:
            weights: Custom agent weights (default uses DEFAULT_WEIGHTS).
            risk_veto_enabled: Whether RiskAgent can veto trades.
        """
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.risk_veto_enabled = risk_veto_enabled
    
    def aggregate(
        self,
        signals: List[AgentSignal],
        entry_triggered: bool = False,
        candidate_valid: bool = True,
    ) -> AggregatedDecision:
        """
        Aggregate agent signals into final decision.
        
        Args:
            signals: List of AgentSignal from each agent.
            entry_triggered: Whether rules engine detected entry trigger.
            candidate_valid: Whether the candidate is valid for trading.
        
        Returns:
            AggregatedDecision with quorum results.
        """
        if not signals:
            return self._no_agents_decision()
        
        # Choose threshold based on entry_triggered
        threshold = QUORUM_THRESHOLD_WITH_ENTRY if entry_triggered else QUORUM_THRESHOLD_NO_ENTRY
        
        # Phase 6: Check for agents with MISSING data (treat as ABSTAIN)
        agent_signals: Dict[str, str] = {}
        agent_votes: Dict[str, str] = {}
        agent_confidences: Dict[str, str] = {}
        
        allow_score = 0.0
        block_score = 0.0
        abstain_weight = 0.0
        
        direction_weighted = 0.0  # For vote_score calculation
        total_direction_weight = 0.0
        
        all_flags: List[str] = []
        risk_blocked = False
        risk_veto_reason: Optional[str] = None
        
        for sig in signals:
            agent_name = sig.agent_name
            weight = self.weights.get(agent_name, 0.1)
            
            # Get confidence float (Phase 6: use confidence_float if available)
            if hasattr(sig, 'confidence_float') and sig.confidence_float is not None:
                conf_float = sig.confidence_float
            else:
                conf_float = confidence_to_float(sig.confidence)
            
            # Phase 6: Check data_status - MISSING = ABSTAIN
            data_status = getattr(sig, 'data_status', DataStatus.REAL)
            if data_status == DataStatus.MISSING:
                vote = VoteType.ABSTAIN
                agent_votes[agent_name] = "ABSTAIN"
                agent_signals[agent_name] = sig.signal
                agent_confidences[agent_name] = sig.confidence.value
                abstain_weight += weight
                all_flags.extend(sig.flags)
                continue
            
            # Map signal to vote
            vote = self._map_signal_to_vote(sig.signal, sig.confidence)
            
            agent_signals[agent_name] = sig.signal
            agent_votes[agent_name] = vote.value.upper()
            agent_confidences[agent_name] = sig.confidence.value
            all_flags.extend(sig.flags)
            
            # Weighted vote
            effective_weight = weight * conf_float
            
            if vote == VoteType.ALLOW:
                allow_score += effective_weight
            elif vote == VoteType.BLOCK:
                block_score += effective_weight
            else:
                abstain_weight += weight
            
            # Direction for vote_score (only from participating agents)
            if sig.signal == "LONG":
                direction_weighted += effective_weight
                total_direction_weight += effective_weight
            elif sig.signal == "SHORT":
                direction_weighted -= effective_weight
                total_direction_weight += effective_weight
            
            # Phase 6: RiskAgent veto only if data_status != MISSING
            if (
                self.risk_veto_enabled
                and agent_name == "RiskAgent"
                and data_status != DataStatus.MISSING
            ):
                # Check for risk_veto flag (Phase 6)
                if getattr(sig, 'risk_veto', False):
                    risk_blocked = True
                    for flag in sig.flags:
                        if flag in ("EXTREME_VOLATILITY", "CRITICAL_DRAWDOWN", "EVENT_IMMINENT"):
                            risk_veto_reason = flag
                            break
                    if not risk_veto_reason:
                        risk_veto_reason = sig.reasoning[:30] if sig.reasoning else "RISK_VETO"
                # Also check old-style veto (HOLD with HIGH confidence)
                elif vote == VoteType.BLOCK:
                    risk_blocked = True
                    risk_veto_reason = sig.flags[0] if sig.flags else "RISK_ELEVATED"
        
        # Calculate active weight and approval ratio
        active_weight = allow_score + block_score
        
        if active_weight == 0:
            approval_ratio = 0.0
        else:
            approval_ratio = allow_score / active_weight
        
        # Calculate vote_score for hybrid calculation
        if total_direction_weight > 0:
            vote_score = direction_weighted / total_direction_weight
        else:
            vote_score = 0.0
        vote_score = max(-1.0, min(1.0, vote_score))
        
        # Determine trade_allowed
        veto_reason: Optional[str] = None
        trade_allowed = True
        
        if not candidate_valid:
            trade_allowed = False
            veto_reason = "NO_CANDIDATE"
        elif risk_blocked:
            trade_allowed = False
            veto_reason = "RISK_VETO"
            all_flags.insert(0, "RISK_VETO")
        elif active_weight < MIN_ACTIVE_WEIGHT:
            trade_allowed = False
            veto_reason = "NO_QUORUM"
            all_flags.append("NO_QUORUM")
        elif approval_ratio < threshold:
            trade_allowed = False
            veto_reason = "LOW_APPROVAL"
            all_flags.append("LOW_APPROVAL")
        
        # Determine signal based on vote_score
        if vote_score > 0.15:
            signal = "LONG"
        elif vote_score < -0.15:
            signal = "SHORT"
        else:
            signal = "HOLD"
        
        # If signal is HOLD, trade not allowed
        if signal == "HOLD":
            trade_allowed = False
            if not veto_reason:
                veto_reason = "SIGNAL_HOLD"
        
        # Calculate risk_modifier
        if not trade_allowed:
            risk_modifier = RISK_MOD_MIN
        else:
            # Scale from approval_ratio
            risk_modifier = RISK_MOD_MIN + (approval_ratio * (RISK_MOD_MAX - RISK_MOD_MIN))
            if not entry_triggered:
                risk_modifier = min(risk_modifier, RISK_MOD_CAP_NO_ENTRY)
                all_flags.append("NO_ENTRY_TRIGGER")
        
        risk_modifier = round(max(RISK_MOD_MIN, min(RISK_MOD_MAX, risk_modifier)), 4)
        
        # Determine consensus level
        if approval_ratio >= 0.9:
            consensus_level = "STRONG"
        elif approval_ratio >= 0.7:
            consensus_level = "MODERATE"
        elif approval_ratio >= 0.5:
            consensus_level = "WEAK"
        else:
            consensus_level = "NONE"
        
        # Determine confidence
        if approval_ratio >= 0.8 and signal != "HOLD":
            confidence = ConfidenceLevel.HIGH
        elif approval_ratio >= 0.6:
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW
        
        # Dedupe flags
        unique_flags = list(dict.fromkeys(all_flags))
        
        return AggregatedDecision(
            signal=signal,
            confidence=confidence,
            trade_allowed=trade_allowed,
            approval_ratio=round(approval_ratio, 4),
            active_weight=round(active_weight, 4),
            allow_score=round(allow_score, 4),
            block_score=round(block_score, 4),
            abstain_weight=round(abstain_weight, 4),
            risk_blocked=risk_blocked,
            veto_reason=veto_reason,
            risk_modifier=risk_modifier,
            vote_score=round(vote_score, 4),
            consensus_level=consensus_level,
            agent_signals=agent_signals,
            agent_votes=agent_votes,
            agent_confidences=agent_confidences,
            flags=unique_flags[:15],
            agents_count=len(signals),
            quorum_threshold=threshold,
        )
    
    def _map_signal_to_vote(self, signal: str, confidence: ConfidenceLevel) -> VoteType:
        """
        Map agent signal + confidence to vote type.
        
        LONG/SHORT → ALLOW (agent has directional opinion)
        HOLD + HIGH/MEDIUM → BLOCK (agent actively opposes trading)
        HOLD + LOW → ABSTAIN (agent uncertain)
        """
        if signal in ("LONG", "SHORT"):
            return VoteType.ALLOW
        elif signal == "HOLD":
            if confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM):
                return VoteType.BLOCK
            else:
                return VoteType.ABSTAIN
        return VoteType.ABSTAIN
    
    def _no_agents_decision(self) -> AggregatedDecision:
        """Return decision when no agents provided."""
        return AggregatedDecision(
            signal="HOLD",
            confidence=ConfidenceLevel.LOW,
            trade_allowed=False,
            approval_ratio=0.0,
            active_weight=0.0,
            allow_score=0.0,
            block_score=0.0,
            abstain_weight=0.0,
            risk_blocked=False,
            veto_reason="NO_AGENTS",
            risk_modifier=RISK_MOD_MIN,
            vote_score=0.0,
            consensus_level="NONE",
            agent_signals={},
            agent_votes={},
            agent_confidences={},
            flags=["NO_AGENTS"],
            agents_count=0,
            quorum_threshold=QUORUM_THRESHOLD_WITH_ENTRY,
        )
