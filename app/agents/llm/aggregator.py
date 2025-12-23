"""
Weighted Aggregator for LLM agent signals with Quorum Voting.

Phase 3: Combines signals from all agents using weighted voting.
Phase 5: Quorum Voting v2 - replaces unanimous voting with weighted quorum.

Key changes from v1:
- trade_allowed based on quorum (approval_ratio >= threshold), not unanimity
- entry_triggered affects threshold: stricter quorum when no entry trigger
- RiskAgent has absolute veto power
- risk_modifier derived from approval_ratio with caps
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from app.models.confidence import ConfidenceLevel, confidence_to_float
from app.agents.llm.base_agent import AgentSignal
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
            "flags": self.flags[:10],  # Limit flags
            "agents_count": self.agents_count,
            "quorum_threshold": self.quorum_threshold,
        }


class WeightedAggregator:
    """
    Aggregates signals from multiple LLM agents using Quorum Voting.
    
    Quorum Voting v2:
    - Each agent votes: ALLOW (directional signal) or BLOCK (HOLD+high conf) or ABSTAIN
    - approval_ratio = allow_score / (allow_score + block_score)
    - trade_allowed = (approval_ratio >= threshold) AND (active_weight >= min) AND (no veto)
    - RiskAgent can veto any trade
    - Stricter threshold when entry_triggered=False
    """
    
    # Default weights (should sum to 1.0)
    DEFAULT_WEIGHTS = {
        "TechnicalAgent": 0.25,
        "MacroAgent": 0.20,
        "SentimentAgent": 0.15,
        "CorrelationAgent": 0.15,
        "RiskAgent": 0.25,
    }
    
    # Thresholds for direction signal (legacy)
    SIGNAL_THRESHOLD = 0.15
    CONSENSUS_STRONG = 0.6
    CONSENSUS_MODERATE = 0.4
    
    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        signal_threshold: float = 0.15,
        risk_veto_enabled: bool = True,
    ):
        """
        Initialize aggregator.
        
        Args:
            weights: Custom agent weights (default uses DEFAULT_WEIGHTS).
            signal_threshold: Minimum score to trigger directional signal.
            risk_veto_enabled: If True, RiskAgent BLOCK vetoes all trades.
        """
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.signal_threshold = signal_threshold
        self.risk_veto_enabled = risk_veto_enabled if risk_veto_enabled is not None else RISK_VETO_ENABLED
    
    def _map_signal_to_vote(self, signal: str, confidence: ConfidenceLevel) -> VoteType:
        """
        Map agent signal to vote type.
        
        Mapping:
        - LONG/SHORT -> ALLOW (agent approves trade in that direction)
        - HOLD + confidence >= MEDIUM -> BLOCK (agent explicitly against)
        - HOLD + confidence == LOW -> ABSTAIN (agent uncertain)
        """
        if signal in ("LONG", "SHORT"):
            return VoteType.ALLOW
        # signal == "HOLD"
        if confidence in (ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH):
            return VoteType.BLOCK
        return VoteType.ABSTAIN
    
    def _calculate_risk_modifier(self, approval_ratio: float, entry_triggered: bool) -> float:
        """
        Calculate risk_modifier from approval_ratio.
        
        Higher consensus = higher risk_modifier (larger position allowed).
        When entry_triggered=False, cap at RISK_MOD_CAP_NO_ENTRY.
        """
        if approval_ratio < 0.6:
            # Should not happen if trade_allowed=True, but fail-safe
            base_modifier = RISK_MOD_MIN
        elif approval_ratio < 0.75:
            base_modifier = 0.70
        elif approval_ratio < 0.9:
            base_modifier = 0.85
        else:
            base_modifier = 1.0
        
        # Apply cap for no-entry trades
        if not entry_triggered:
            base_modifier = min(base_modifier, RISK_MOD_CAP_NO_ENTRY)
        
        # Clamp to valid range
        return max(RISK_MOD_MIN, min(RISK_MOD_MAX, base_modifier))
    
    def aggregate(
        self,
        signals: List[AgentSignal],
        entry_triggered: bool = True,
        candidate_valid: bool = True,
    ) -> AggregatedDecision:
        """
        Aggregate multiple agent signals into final decision using Quorum Voting.
        
        Args:
            signals: List of AgentSignal from each agent.
            entry_triggered: Whether M15 entry confirmation exists.
            candidate_valid: Whether rules candidate is valid (setup_type!=NO_TRADE, direction!=FLAT).
        
        Returns:
            AggregatedDecision with quorum-based trade_allowed.
        """
        if not signals:
            return self._empty_decision()
        
        # Choose quorum threshold based on entry_triggered
        quorum_threshold = QUORUM_THRESHOLD_WITH_ENTRY if entry_triggered else QUORUM_THRESHOLD_NO_ENTRY
        
        # Build signal/confidence/vote maps
        agent_signals: Dict[str, str] = {}
        agent_votes: Dict[str, str] = {}
        agent_confidences: Dict[str, str] = {}
        all_flags: List[str] = []
        
        risk_signal: Optional[AgentSignal] = None
        risk_vote: Optional[VoteType] = None
        
        # Calculate scores
        allow_score = 0.0
        block_score = 0.0
        abstain_weight = 0.0
        total_weight = 0.0
        
        # For direction calculation (legacy)
        direction_score = 0.0
        
        for sig in signals:
            weight = self.weights.get(sig.agent_name, 0.1)
            conf_float = confidence_to_float(sig.confidence)
            vote = self._map_signal_to_vote(sig.signal, sig.confidence)
            
            agent_signals[sig.agent_name] = sig.signal
            agent_votes[sig.agent_name] = vote.value
            agent_confidences[sig.agent_name] = sig.confidence.value
            all_flags.extend(sig.flags)
            
            # Track RiskAgent
            if sig.agent_name == "RiskAgent":
                risk_signal = sig
                risk_vote = vote
            
            # Calculate weighted scores by vote type
            effective_weight = weight * conf_float
            
            if vote == VoteType.ALLOW:
                allow_score += effective_weight
            elif vote == VoteType.BLOCK:
                block_score += effective_weight
            else:  # ABSTAIN
                abstain_weight += weight
            
            total_weight += weight
            
            # Direction score (for LONG/SHORT/HOLD signal)
            if sig.signal == "LONG":
                direction_score += effective_weight
            elif sig.signal == "SHORT":
                direction_score -= effective_weight
        
        # Calculate active weight (ALLOW + BLOCK, excluding ABSTAIN)
        active_weight = allow_score + block_score
        
        # Check for RiskAgent veto
        risk_blocked = False
        veto_reason: Optional[str] = None
        
        if self.risk_veto_enabled and risk_vote == VoteType.BLOCK:
            risk_blocked = True
            veto_reason = "RISK_VETO"
            logger.info("WeightedAggregator: RiskAgent veto - trade blocked")
        
        # Calculate approval_ratio (avoid div by zero)
        if active_weight > 0:
            approval_ratio = allow_score / active_weight
        else:
            approval_ratio = 0.0
        
        # Determine trade_allowed via quorum
        trade_allowed = False
        
        if risk_blocked:
            trade_allowed = False
            # veto_reason already set
        elif not candidate_valid:
            trade_allowed = False
            veto_reason = "NO_CANDIDATE"
        elif active_weight < MIN_ACTIVE_WEIGHT:
            trade_allowed = False
            veto_reason = "NO_QUORUM"
            logger.info(f"WeightedAggregator: No quorum - active_weight={active_weight:.2f} < {MIN_ACTIVE_WEIGHT}")
        elif approval_ratio < quorum_threshold:
            trade_allowed = False
            veto_reason = "LOW_APPROVAL"
            logger.info(f"WeightedAggregator: Low approval - ratio={approval_ratio:.2f} < threshold={quorum_threshold}")
        else:
            trade_allowed = True
        
        # Calculate direction signal (legacy for hybrid mode)
        normalized_direction = direction_score / total_weight if total_weight > 0 else 0.0
        
        if normalized_direction > self.signal_threshold:
            final_signal = "LONG"
        elif normalized_direction < -self.signal_threshold:
            final_signal = "SHORT"
        else:
            final_signal = "HOLD"
        
        # If signal is HOLD, trade_allowed must be False
        if final_signal == "HOLD" and trade_allowed:
            trade_allowed = False
            veto_reason = "SIGNAL_HOLD"
        
        # Calculate consensus level (legacy)
        consensus = self._calculate_consensus(signals, final_signal)
        
        # Calculate overall confidence
        confidence = self._calculate_confidence(signals, consensus, risk_blocked)
        
        # Calculate risk_modifier from approval_ratio
        risk_modifier = self._calculate_risk_modifier(approval_ratio, entry_triggered) if trade_allowed else RISK_MOD_MIN
        
        # Dedupe flags
        unique_flags = list(dict.fromkeys(all_flags))
        if risk_blocked:
            unique_flags.insert(0, "RISK_VETO")
        if veto_reason == "NO_QUORUM":
            unique_flags.insert(0, "NO_QUORUM")
        if veto_reason == "LOW_APPROVAL":
            unique_flags.insert(0, "LOW_APPROVAL")
        if not entry_triggered and trade_allowed:
            unique_flags.insert(0, "NO_ENTRY_TRIGGER")
        
        return AggregatedDecision(
            signal=final_signal,
            confidence=confidence,
            trade_allowed=trade_allowed,
            approval_ratio=round(approval_ratio, 4),
            active_weight=round(active_weight, 4),
            allow_score=round(allow_score, 4),
            block_score=round(block_score, 4),
            abstain_weight=round(abstain_weight, 4),
            risk_blocked=risk_blocked,
            veto_reason=veto_reason,
            risk_modifier=round(risk_modifier, 4),
            vote_score=round(normalized_direction, 4),
            consensus_level=consensus,
            agent_signals=agent_signals,
            agent_votes=agent_votes,
            agent_confidences=agent_confidences,
            flags=unique_flags[:15],
            agents_count=len(signals),
            quorum_threshold=quorum_threshold,
        )
    
    def _calculate_consensus(self, signals: List[AgentSignal], final_signal: str) -> str:
        """Calculate level of agreement among agents."""
        if not signals:
            return "NONE"
        
        agreeing = sum(1 for s in signals if s.signal == final_signal)
        ratio = agreeing / len(signals)
        
        if ratio >= self.CONSENSUS_STRONG:
            return "STRONG"
        elif ratio >= self.CONSENSUS_MODERATE:
            return "MODERATE"
        elif ratio > 0:
            return "WEAK"
        return "NONE"
    
    def _calculate_confidence(
        self, 
        signals: List[AgentSignal], 
        consensus: str,
        risk_blocked: bool,
    ) -> ConfidenceLevel:
        """Calculate overall confidence from agent confidences and consensus."""
        if risk_blocked:
            return ConfidenceLevel.LOW
        
        if not signals:
            return ConfidenceLevel.LOW
        
        # Average confidence weighted by agent weight
        total_conf = 0.0
        total_weight = 0.0
        
        for sig in signals:
            weight = self.weights.get(sig.agent_name, 0.1)
            conf_value = confidence_to_float(sig.confidence)
            total_conf += weight * conf_value
            total_weight += weight
        
        avg_conf = total_conf / total_weight if total_weight > 0 else 0.3
        
        # Adjust by consensus
        if consensus == "STRONG":
            avg_conf *= 1.1
        elif consensus == "WEAK" or consensus == "NONE":
            avg_conf *= 0.7
        
        # Map to enum
        if avg_conf >= 0.7:
            return ConfidenceLevel.HIGH
        elif avg_conf >= 0.45:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW
    
    def _empty_decision(self) -> AggregatedDecision:
        """Return empty decision when no signals provided."""
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
