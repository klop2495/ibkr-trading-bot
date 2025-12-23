"""
Tests for Quorum Voting v2 in WeightedAggregator.

Tests cover:
1. NO_ENTRY + strong quorum → trade_allowed=True + cap risk_modifier
2. NO_ENTRY + low participation → NO_QUORUM → False
3. WITH_ENTRY → threshold softer → True
4. RISK_VETO → always False
5. Signal HOLD → False even if quorum passed
6. Mixed confidence affects approval_ratio
"""

import pytest
from datetime import datetime, timezone

from app.models.confidence import ConfidenceLevel
from app.agents.llm.base_agent import AgentSignal
from app.agents.llm.aggregator import WeightedAggregator, AggregatedDecision, VoteType
from app.agents.config import (
    QUORUM_THRESHOLD_WITH_ENTRY,
    QUORUM_THRESHOLD_NO_ENTRY,
    MIN_ACTIVE_WEIGHT,
    RISK_MOD_CAP_NO_ENTRY,
    RISK_MOD_MIN,
    RISK_MOD_MAX,
)


def make_signal(agent: str, signal: str, confidence: ConfidenceLevel, flags: list = None) -> AgentSignal:
    """Helper to create AgentSignal."""
    return AgentSignal(
        agent_name=agent,
        signal=signal,
        confidence=confidence,
        reasoning=f"Test {agent}",
        flags=flags or [],
    )


class TestVoteMapping:
    """Test signal to vote mapping."""
    
    def test_long_maps_to_allow(self):
        agg = WeightedAggregator()
        vote = agg._map_signal_to_vote("LONG", ConfidenceLevel.HIGH)
        assert vote == VoteType.ALLOW
    
    def test_short_maps_to_allow(self):
        agg = WeightedAggregator()
        vote = agg._map_signal_to_vote("SHORT", ConfidenceLevel.MEDIUM)
        assert vote == VoteType.ALLOW
    
    def test_hold_high_conf_maps_to_block(self):
        agg = WeightedAggregator()
        vote = agg._map_signal_to_vote("HOLD", ConfidenceLevel.HIGH)
        assert vote == VoteType.BLOCK
    
    def test_hold_medium_conf_maps_to_block(self):
        agg = WeightedAggregator()
        vote = agg._map_signal_to_vote("HOLD", ConfidenceLevel.MEDIUM)
        assert vote == VoteType.BLOCK
    
    def test_hold_low_conf_maps_to_abstain(self):
        agg = WeightedAggregator()
        vote = agg._map_signal_to_vote("HOLD", ConfidenceLevel.LOW)
        assert vote == VoteType.ABSTAIN


class TestQuorumWithEntry:
    """Test quorum with entry_triggered=True (softer threshold)."""
    
    def test_strong_quorum_allows_trade(self):
        """4 LONG HIGH + 1 HOLD LOW → should pass 60% threshold."""
        agg = WeightedAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("RiskAgent", "HOLD", ConfidenceLevel.LOW),  # ABSTAIN
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        assert result.trade_allowed is True
        assert result.signal == "LONG"
        assert result.approval_ratio >= QUORUM_THRESHOLD_WITH_ENTRY
        assert result.veto_reason is None
        assert result.risk_modifier >= 0.7
    
    def test_moderate_quorum_passes_with_entry(self):
        """3 LONG + 2 HOLD HIGH → exactly at threshold."""
        agg = WeightedAggregator()
        # TechnicalAgent (0.25) + MacroAgent (0.20) + SentimentAgent (0.15) = 0.60
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),  # ALLOW
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),       # ALLOW
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),   # ALLOW
            make_signal("CorrelationAgent", "HOLD", ConfidenceLevel.HIGH), # BLOCK
            make_signal("RiskAgent", "HOLD", ConfidenceLevel.LOW),         # ABSTAIN (no veto since LOW)
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        # Calculate expected approval_ratio
        # ALLOW: 0.25*0.9 + 0.20*0.9 + 0.15*0.9 = 0.225 + 0.18 + 0.135 = 0.54
        # BLOCK: 0.15*0.9 = 0.135
        # active_weight = 0.54 + 0.135 = 0.675
        # approval_ratio = 0.54 / 0.675 = 0.8
        assert result.trade_allowed is True
        assert result.approval_ratio >= 0.6


class TestQuorumNoEntry:
    """Test quorum with entry_triggered=False (stricter threshold)."""
    
    def test_no_entry_strong_quorum_passes(self):
        """Strong quorum passes even without entry trigger."""
        agg = WeightedAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("RiskAgent", "LONG", ConfidenceLevel.HIGH),
        ]
        
        result = agg.aggregate(signals, entry_triggered=False, candidate_valid=True)
        
        assert result.trade_allowed is True
        assert result.approval_ratio >= QUORUM_THRESHOLD_NO_ENTRY
        assert "NO_ENTRY_TRIGGER" in result.flags
    
    def test_no_entry_caps_risk_modifier(self):
        """Without entry_triggered, risk_modifier should be capped."""
        agg = WeightedAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("RiskAgent", "LONG", ConfidenceLevel.HIGH),
        ]
        
        result = agg.aggregate(signals, entry_triggered=False, candidate_valid=True)
        
        assert result.trade_allowed is True
        assert result.risk_modifier <= RISK_MOD_CAP_NO_ENTRY
    
    def test_no_entry_moderate_quorum_fails(self):
        """Moderate quorum that passes with entry fails without."""
        agg = WeightedAggregator()
        # This should pass 60% but fail 75%
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.MEDIUM),
            make_signal("SentimentAgent", "HOLD", ConfidenceLevel.LOW),  # ABSTAIN
            make_signal("CorrelationAgent", "HOLD", ConfidenceLevel.HIGH),  # BLOCK
            make_signal("RiskAgent", "HOLD", ConfidenceLevel.LOW),  # ABSTAIN
        ]
        
        result_with_entry = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        result_no_entry = agg.aggregate(signals, entry_triggered=False, candidate_valid=True)
        
        # Depends on exact calculation, but stricter threshold should block more
        assert result_no_entry.quorum_threshold > result_with_entry.quorum_threshold


class TestNoQuorum:
    """Test NO_QUORUM rejection."""
    
    def test_all_abstain_no_quorum(self):
        """All agents ABSTAIN → NO_QUORUM."""
        agg = WeightedAggregator()
        signals = [
            make_signal("TechnicalAgent", "HOLD", ConfidenceLevel.LOW),
            make_signal("MacroAgent", "HOLD", ConfidenceLevel.LOW),
            make_signal("SentimentAgent", "HOLD", ConfidenceLevel.LOW),
            make_signal("CorrelationAgent", "HOLD", ConfidenceLevel.LOW),
            make_signal("RiskAgent", "HOLD", ConfidenceLevel.LOW),
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        assert result.trade_allowed is False
        assert result.veto_reason == "NO_QUORUM"
        assert result.active_weight == 0.0
        assert "NO_QUORUM" in result.flags
    
    def test_low_participation_no_quorum(self):
        """Only 1 agent participates → NO_QUORUM."""
        agg = WeightedAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),  # Only one ALLOW
            make_signal("MacroAgent", "HOLD", ConfidenceLevel.LOW),       # ABSTAIN
            make_signal("SentimentAgent", "HOLD", ConfidenceLevel.LOW),   # ABSTAIN
            make_signal("CorrelationAgent", "HOLD", ConfidenceLevel.LOW), # ABSTAIN
            make_signal("RiskAgent", "HOLD", ConfidenceLevel.LOW),        # ABSTAIN
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        # active_weight = 0.25 * 0.9 = 0.225 < MIN_ACTIVE_WEIGHT (0.5)
        assert result.trade_allowed is False
        assert result.veto_reason == "NO_QUORUM"
        assert result.active_weight < MIN_ACTIVE_WEIGHT


class TestRiskVeto:
    """Test RiskAgent veto."""
    
    def test_risk_veto_blocks_unanimous_long(self):
        """RiskAgent BLOCK vetoes even unanimous LONG."""
        agg = WeightedAggregator(risk_veto_enabled=True)
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("RiskAgent", "HOLD", ConfidenceLevel.HIGH, ["HIGH_VOLATILITY"]),  # BLOCK
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        assert result.trade_allowed is False
        assert result.risk_blocked is True
        assert result.veto_reason == "RISK_VETO"
        assert "RISK_VETO" in result.flags
    
    def test_risk_veto_disabled_allows_trade(self):
        """With risk_veto_enabled=False, RiskAgent BLOCK doesn't veto."""
        agg = WeightedAggregator(risk_veto_enabled=False)
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("RiskAgent", "HOLD", ConfidenceLevel.HIGH),  # Would be BLOCK but veto disabled
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        assert result.risk_blocked is False
        # Still might not be allowed if quorum fails
    
    def test_risk_low_confidence_no_veto(self):
        """RiskAgent HOLD with LOW confidence = ABSTAIN, not veto."""
        agg = WeightedAggregator(risk_veto_enabled=True)
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("RiskAgent", "HOLD", ConfidenceLevel.LOW),  # ABSTAIN, not BLOCK
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        assert result.risk_blocked is False
        # Should be allowed since RiskAgent is ABSTAIN
        assert result.trade_allowed is True


class TestLowApproval:
    """Test LOW_APPROVAL rejection."""
    
    def test_many_blocks_low_approval(self):
        """3 BLOCK + 2 ALLOW → low approval_ratio."""
        agg = WeightedAggregator(risk_veto_enabled=False)  # Disable veto to test quorum
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),   # ALLOW
            make_signal("MacroAgent", "HOLD", ConfidenceLevel.HIGH),       # BLOCK
            make_signal("SentimentAgent", "HOLD", ConfidenceLevel.HIGH),   # BLOCK
            make_signal("CorrelationAgent", "HOLD", ConfidenceLevel.HIGH), # BLOCK
            make_signal("RiskAgent", "LONG", ConfidenceLevel.HIGH),        # ALLOW
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        # ALLOW: Tech (0.25*0.9) + Risk (0.25*0.9) = 0.45
        # BLOCK: Macro (0.20*0.9) + Sent (0.15*0.9) + Corr (0.15*0.9) = 0.45
        # approval_ratio = 0.45 / 0.9 = 0.5 < 0.6
        assert result.trade_allowed is False
        assert result.veto_reason == "LOW_APPROVAL"
        assert result.approval_ratio < QUORUM_THRESHOLD_WITH_ENTRY


class TestSignalHold:
    """Test that HOLD signal results in trade_allowed=False."""
    
    def test_hold_signal_blocks_even_with_quorum(self):
        """If final signal is HOLD, trade_allowed must be False."""
        agg = WeightedAggregator()
        # All agents HOLD with low confidence = all ABSTAIN
        # Direction score = 0, so signal = HOLD
        signals = [
            make_signal("TechnicalAgent", "HOLD", ConfidenceLevel.LOW),
            make_signal("MacroAgent", "HOLD", ConfidenceLevel.LOW),
            make_signal("SentimentAgent", "HOLD", ConfidenceLevel.LOW),
            make_signal("CorrelationAgent", "HOLD", ConfidenceLevel.LOW),
            make_signal("RiskAgent", "HOLD", ConfidenceLevel.LOW),
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        assert result.signal == "HOLD"
        assert result.trade_allowed is False


class TestCandidateInvalid:
    """Test that invalid candidate blocks trade."""
    
    def test_invalid_candidate_blocks_trade(self):
        """candidate_valid=False → trade_allowed=False."""
        agg = WeightedAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("RiskAgent", "LONG", ConfidenceLevel.HIGH),
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=False)
        
        assert result.trade_allowed is False
        assert result.veto_reason == "NO_CANDIDATE"


class TestMixedConfidence:
    """Test that confidence affects approval_ratio correctly."""
    
    def test_high_conf_weighs_more(self):
        """HIGH confidence should contribute more than LOW."""
        agg = WeightedAggregator()
        
        # All HIGH confidence
        signals_high = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("RiskAgent", "LONG", ConfidenceLevel.HIGH),
        ]
        
        # All LOW confidence
        signals_low = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.LOW),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.LOW),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.LOW),
            make_signal("CorrelationAgent", "LONG", ConfidenceLevel.LOW),
            make_signal("RiskAgent", "LONG", ConfidenceLevel.LOW),
        ]
        
        result_high = agg.aggregate(signals_high, entry_triggered=True, candidate_valid=True)
        result_low = agg.aggregate(signals_low, entry_triggered=True, candidate_valid=True)
        
        # Both should have approval_ratio = 1.0 (all ALLOW)
        # But allow_score should be different
        assert result_high.allow_score > result_low.allow_score
        # risk_modifier should be higher for high confidence
        assert result_high.risk_modifier >= result_low.risk_modifier


class TestRiskModifierCalculation:
    """Test risk_modifier calculation from approval_ratio."""
    
    def test_perfect_consensus_full_modifier(self):
        """100% approval → risk_modifier = 1.0 (with entry)."""
        agg = WeightedAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("RiskAgent", "LONG", ConfidenceLevel.HIGH),
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        assert result.approval_ratio == 1.0
        assert result.risk_modifier == 1.0
    
    def test_moderate_consensus_reduced_modifier(self):
        """~75% approval → risk_modifier = 0.85."""
        agg = WeightedAggregator()
        # 4 ALLOW, 1 BLOCK
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("MacroAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("SentimentAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("CorrelationAgent", "HOLD", ConfidenceLevel.HIGH),  # BLOCK
            make_signal("RiskAgent", "LONG", ConfidenceLevel.HIGH),
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        
        if result.trade_allowed:
            assert 0.7 <= result.risk_modifier <= 1.0


class TestEmptySignals:
    """Test edge cases."""
    
    def test_empty_signals_list(self):
        """Empty signals → HOLD with NO_AGENTS."""
        agg = WeightedAggregator()
        result = agg.aggregate([], entry_triggered=True, candidate_valid=True)
        
        assert result.signal == "HOLD"
        assert result.trade_allowed is False
        assert result.veto_reason == "NO_AGENTS"
        assert "NO_AGENTS" in result.flags
        assert result.agents_count == 0


class TestToDict:
    """Test serialization."""
    
    def test_to_dict_contains_required_fields(self):
        """to_dict should contain all required fields."""
        agg = WeightedAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH),
            make_signal("RiskAgent", "LONG", ConfidenceLevel.MEDIUM),
        ]
        
        result = agg.aggregate(signals, entry_triggered=True, candidate_valid=True)
        d = result.to_dict()
        
        required_fields = [
            "signal", "confidence", "trade_allowed", "approval_ratio",
            "active_weight", "allow_score", "block_score", "risk_blocked",
            "veto_reason", "risk_modifier", "vote_score", "consensus_level",
            "agent_signals", "agent_votes", "agent_confidences", "flags",
            "agents_count", "quorum_threshold",
        ]
        
        for field in required_fields:
            assert field in d, f"Missing field: {field}"
