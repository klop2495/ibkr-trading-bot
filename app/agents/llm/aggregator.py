"""
Weighted Aggregator for LLM agent signals.

Phase 3: Combines signals from all agents using weighted voting.
Produces final decision with aggregate confidence.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.confidence import ConfidenceLevel, confidence_to_weight
from app.agents.llm.base_agent import AgentSignal


logger = logging.getLogger(__name__)


@dataclass
class AggregatedDecision:
    """
    Final aggregated decision from all agents.
    
    Contains weighted vote results and agent details.
    """
    signal: str  # LONG, SHORT, HOLD
    confidence: ConfidenceLevel
    
    # Voting details
    vote_score: float  # -1.0 to +1.0 (negative = SHORT, positive = LONG)
    consensus_level: str  # STRONG, MODERATE, WEAK, NONE
    
    # Agent breakdown
    agent_signals: Dict[str, str]  # agent_name -> signal
    agent_confidences: Dict[str, str]  # agent_name -> confidence
    
    # Flags aggregated from all agents
    flags: List[str] = field(default_factory=list)
    
    # Metadata
    agents_count: int = 0
    risk_blocked: bool = False  # True if RiskAgent blocked the trade
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict:
        """Convert to dict for storage in parallel_decisions."""
        return {
            "signal": self.signal,
            "confidence": self.confidence.value,
            "vote_score": round(self.vote_score, 4),
            "consensus_level": self.consensus_level,
            "agent_signals": self.agent_signals,
            "agent_confidences": self.agent_confidences,
            "flags": self.flags[:10],  # Limit flags
            "agents_count": self.agents_count,
            "risk_blocked": self.risk_blocked,
        }


class WeightedAggregator:
    """
    Aggregates signals from multiple LLM agents.
    
    Uses weighted voting where:
    - Each agent has a base weight (0.0-1.0)
    - Confidence scales the effective weight
    - RiskAgent can veto trades
    
    Signal calculation:
    - LONG contributes +weight, SHORT contributes -weight
    - Final score > threshold → LONG
    - Final score < -threshold → SHORT
    - Otherwise → HOLD
    """
    
    # Default weights (should sum to 1.0)
    DEFAULT_WEIGHTS = {
        "TechnicalAgent": 0.25,
        "MacroAgent": 0.20,
        "SentimentAgent": 0.15,
        "CorrelationAgent": 0.15,
        "RiskAgent": 0.25,
    }
    
    # Thresholds
    SIGNAL_THRESHOLD = 0.15  # Min score to trigger LONG/SHORT
    CONSENSUS_STRONG = 0.6   # 60%+ agreement
    CONSENSUS_MODERATE = 0.4  # 40%+ agreement
    
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
            risk_veto_enabled: If True, RiskAgent HOLD blocks all trades.
        """
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.signal_threshold = signal_threshold
        self.risk_veto_enabled = risk_veto_enabled
    
    def aggregate(self, signals: List[AgentSignal]) -> AggregatedDecision:
        """
        Aggregate multiple agent signals into final decision.
        
        Args:
            signals: List of AgentSignal from each agent.
        
        Returns:
            AggregatedDecision with weighted result.
        """
        if not signals:
            return self._empty_decision()
        
        # Build signal/confidence maps
        agent_signals: Dict[str, str] = {}
        agent_confidences: Dict[str, str] = {}
        all_flags: List[str] = []
        risk_signal: Optional[AgentSignal] = None
        
        for sig in signals:
            agent_signals[sig.agent_name] = sig.signal
            agent_confidences[sig.agent_name] = sig.confidence.value
            all_flags.extend(sig.flags)
            
            if sig.agent_name == "RiskAgent":
                risk_signal = sig
        
        # Check for RiskAgent veto
        risk_blocked = False
        if self.risk_veto_enabled and risk_signal:
            if risk_signal.signal == "HOLD" and risk_signal.confidence in (ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH):
                risk_blocked = True
                logger.info("WeightedAggregator: RiskAgent veto - HOLD enforced")
        
        # Calculate weighted score
        total_score = 0.0
        total_weight = 0.0
        directional_agents = 0
        
        for sig in signals:
            weight = self.weights.get(sig.agent_name, 0.1)
            conf_multiplier = confidence_to_weight(sig.confidence)
            effective_weight = weight * conf_multiplier
            
            if sig.signal == "LONG":
                total_score += effective_weight
                directional_agents += 1
            elif sig.signal == "SHORT":
                total_score -= effective_weight
                directional_agents += 1
            # HOLD contributes 0
            
            total_weight += weight
        
        # Normalize score
        if total_weight > 0:
            normalized_score = total_score / total_weight
        else:
            normalized_score = 0.0
        
        # Determine signal
        if risk_blocked:
            final_signal = "HOLD"
        elif normalized_score > self.signal_threshold:
            final_signal = "LONG"
        elif normalized_score < -self.signal_threshold:
            final_signal = "SHORT"
        else:
            final_signal = "HOLD"
        
        # Calculate consensus level
        consensus = self._calculate_consensus(signals, final_signal)
        
        # Determine overall confidence
        confidence = self._calculate_confidence(signals, consensus, risk_blocked)
        
        # Dedupe flags
        unique_flags = list(dict.fromkeys(all_flags))
        if risk_blocked:
            unique_flags.insert(0, "RISK_VETO")
        
        return AggregatedDecision(
            signal=final_signal,
            confidence=confidence,
            vote_score=round(normalized_score, 4),
            consensus_level=consensus,
            agent_signals=agent_signals,
            agent_confidences=agent_confidences,
            flags=unique_flags[:15],  # Limit to 15 flags
            agents_count=len(signals),
            risk_blocked=risk_blocked,
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
            conf_value = confidence_to_weight(sig.confidence)
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
            vote_score=0.0,
            consensus_level="NONE",
            agent_signals={},
            agent_confidences={},
            flags=["NO_AGENTS"],
            agents_count=0,
            risk_blocked=False,
        )
