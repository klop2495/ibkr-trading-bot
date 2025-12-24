"""
Tests for Phase 6: Two-Contour Architecture.

Tests cover:
1. ScoreAggregator calculates llm_score correctly
2. Agents with MISSING data ABSTAIN (don't affect score)
3. RiskAgent veto works only with REAL data
4. 60/40 blend produces correct hybrid_score
5. No hallucinations - Macro/Sentiment ABSTAIN when mock
"""

import pytest
from datetime import datetime, timezone

from app.models.confidence import ConfidenceLevel
from app.agents.llm.base_agent import AgentSignal
from app.agents.llm.data_status import DataStatus
from app.agents.llm.score_aggregator import ScoreAggregator, LLMContourResult


def make_signal(
    agent: str,
    signal: str,
    confidence_float: float,
    data_status: DataStatus = DataStatus.REAL,
    risk_veto: bool = False,
    flags: list = None,
) -> AgentSignal:
    """Helper to create AgentSignal with explicit confidence_float."""
    conf_level = ConfidenceLevel.HIGH if confidence_float >= 0.7 else ConfidenceLevel.MEDIUM if confidence_float >= 0.5 else ConfidenceLevel.LOW
    sig = AgentSignal(
        agent_name=agent,
        signal=signal,
        confidence=conf_level,
        reasoning=f"Test {agent}",
        data_status=data_status,
        risk_veto=risk_veto,
        flags=flags or [],
    )
    # Override the auto-derived confidence_float with our explicit value
    sig.confidence_float = confidence_float
    return sig


class TestScoreAggregatorBasic:
    """Test basic score aggregation."""
    
    def test_all_long_high_confidence(self):
        """All agents LONG with high confidence -> llm_score close to +1."""
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.9),
            make_signal("MacroAgent", "LONG", 0.9),
            make_signal("SentimentAgent", "LONG", 0.9),
            make_signal("CorrelationAgent", "LONG", 0.9),
            make_signal("RiskAgent", "LONG", 0.9),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.llm_signal == "LONG"
        assert result.llm_score > 0.8
        assert result.llm_active_weight == 1.0
        assert result.participating_agents == 5
        assert result.abstaining_agents == 0
    
    def test_all_short_high_confidence(self):
        """All agents SHORT with high confidence -> llm_score close to -1."""
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "SHORT", 0.9),
            make_signal("MacroAgent", "SHORT", 0.9),
            make_signal("SentimentAgent", "SHORT", 0.9),
            make_signal("CorrelationAgent", "SHORT", 0.9),
            make_signal("RiskAgent", "SHORT", 0.9),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.llm_signal == "SHORT"
        assert result.llm_score < -0.8
    
    def test_mixed_signals_hold(self):
        """Mixed signals -> llm_score near 0 -> HOLD."""
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.7),   # +1 * 0.25 * 0.7 = +0.175
            make_signal("MacroAgent", "SHORT", 0.7),      # -1 * 0.20 * 0.7 = -0.14
            make_signal("SentimentAgent", "LONG", 0.5),   # +1 * 0.15 * 0.5 = +0.075
            make_signal("CorrelationAgent", "SHORT", 0.5), # -1 * 0.15 * 0.5 = -0.075
            make_signal("RiskAgent", "HOLD", 0.5),        # 0 * 0.25 * 0.5 = 0
        ]
        
        result = agg.aggregate(signals)
        
        # Score should be small, result in HOLD
        assert abs(result.llm_score) < 0.15
        assert result.llm_signal == "HOLD"
    
    def test_confidence_affects_weight(self):
        """Higher confidence has more weight."""
        agg = ScoreAggregator()
        
        # Low confidence LONG vs high confidence SHORT
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.3),   # Weak LONG
            make_signal("MacroAgent", "SHORT", 0.9),      # Strong SHORT
            make_signal("SentimentAgent", "HOLD", 0.5),
            make_signal("CorrelationAgent", "HOLD", 0.5),
            make_signal("RiskAgent", "HOLD", 0.5),
        ]
        
        result = agg.aggregate(signals)
        
        # SHORT should win due to higher confidence
        assert result.llm_score < 0
        assert result.llm_signal == "SHORT"


class TestDataStatusAbstention:
    """Test that MISSING data agents don't affect score."""
    
    def test_missing_agents_abstain(self):
        """Agents with MISSING data don't participate in score."""
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.9, DataStatus.REAL),
            make_signal("MacroAgent", "SHORT", 0.9, DataStatus.MISSING),  # ABSTAIN
            make_signal("SentimentAgent", "SHORT", 0.9, DataStatus.MISSING),  # ABSTAIN
            make_signal("CorrelationAgent", "LONG", 0.9, DataStatus.REAL),
            make_signal("RiskAgent", "LONG", 0.9, DataStatus.REAL),
        ]
        
        result = agg.aggregate(signals)
        
        # Only Tech, Corr, Risk participate - all LONG
        assert result.llm_signal == "LONG"
        assert result.llm_score > 0.5
        assert result.participating_agents == 3
        assert result.abstaining_agents == 2
        assert "AGENTS_ABSTAINING:2" in result.flags
    
    def test_all_missing_returns_hold(self):
        """All agents MISSING -> llm_score = 0, HOLD."""
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.9, DataStatus.MISSING),
            make_signal("MacroAgent", "LONG", 0.9, DataStatus.MISSING),
            make_signal("SentimentAgent", "LONG", 0.9, DataStatus.MISSING),
            make_signal("CorrelationAgent", "LONG", 0.9, DataStatus.MISSING),
            make_signal("RiskAgent", "LONG", 0.9, DataStatus.MISSING),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.llm_score == 0.0
        assert result.llm_signal == "HOLD"
        assert result.llm_active_weight == 0.0
        assert result.participating_agents == 0
        assert result.abstaining_agents == 5
    
    def test_macro_sentiment_abstain_when_mock(self):
        """Verify Macro+Sentiment ABSTAIN doesn't block trades."""
        agg = ScoreAggregator()
        
        # Real-world scenario: Macro+Sentiment are mock (MISSING)
        # Technical, Correlation, Risk are real
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.8, DataStatus.REAL),
            make_signal("MacroAgent", "HOLD", 0.0, DataStatus.MISSING),  # ABSTAIN
            make_signal("SentimentAgent", "HOLD", 0.0, DataStatus.MISSING),  # ABSTAIN
            make_signal("CorrelationAgent", "LONG", 0.7, DataStatus.REAL),
            make_signal("RiskAgent", "HOLD", 0.6, DataStatus.REAL),  # Neutral, not veto
        ]
        
        result = agg.aggregate(signals)
        
        # Should get LONG signal from Tech+Corr
        assert result.llm_signal == "LONG"
        assert result.llm_score > 0.15
        assert result.participating_agents == 3
        assert result.abstaining_agents == 2


class TestRiskVeto:
    """Test RiskAgent veto behavior."""
    
    def test_risk_veto_blocks_trade(self):
        """RiskAgent veto -> HOLD regardless of other signals."""
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.9),
            make_signal("MacroAgent", "LONG", 0.9),
            make_signal("SentimentAgent", "LONG", 0.9),
            make_signal("CorrelationAgent", "LONG", 0.9),
            make_signal("RiskAgent", "HOLD", 0.9, DataStatus.REAL, risk_veto=True, flags=["EXTREME_VOLATILITY"]),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.llm_signal == "HOLD"
        assert result.risk_veto is True
        assert result.risk_veto_reason == "EXTREME_VOLATILITY"
        assert "RISK_VETO" in result.flags
    
    def test_risk_veto_only_from_risk_agent(self):
        """Only RiskAgent's veto flag is respected."""
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.9, risk_veto=True),  # Should be ignored
            make_signal("MacroAgent", "LONG", 0.9),
            make_signal("SentimentAgent", "LONG", 0.9),
            make_signal("CorrelationAgent", "LONG", 0.9),
            make_signal("RiskAgent", "LONG", 0.9),  # No veto
        ]
        
        result = agg.aggregate(signals)
        
        assert result.llm_signal == "LONG"
        assert result.risk_veto is False
    
    def test_risk_missing_no_veto(self):
        """RiskAgent with MISSING data can't veto."""
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.9, DataStatus.REAL),
            make_signal("MacroAgent", "LONG", 0.9, DataStatus.REAL),
            make_signal("SentimentAgent", "LONG", 0.9, DataStatus.REAL),
            make_signal("CorrelationAgent", "LONG", 0.9, DataStatus.REAL),
            make_signal("RiskAgent", "HOLD", 0.9, DataStatus.MISSING, risk_veto=True),  # ABSTAIN
        ]
        
        result = agg.aggregate(signals)
        
        # RiskAgent is MISSING, so veto doesn't apply
        # But wait - we check veto before checking data_status in current impl
        # Let's verify the expected behavior
        assert result.llm_signal == "LONG"


class TestLLMScoreCalculation:
    """Test the llm_score formula."""
    
    def test_score_formula_example_1(self):
        """
        2 LONG (0.9), 1 SHORT (0.6), 2 MISSING
        
        Weights: Tech=0.25, Macro=0.20, Sent=0.15, Corr=0.15, Risk=0.25
        
        Participating:
        - TechnicalAgent: LONG * 0.25 * 0.9 = +0.225
        - MacroAgent: MISSING (abstain)
        - SentimentAgent: MISSING (abstain)
        - CorrelationAgent: LONG * 0.15 * 0.9 = +0.135
        - RiskAgent: SHORT * 0.25 * 0.6 = -0.15
        
        sum = 0.225 + 0.135 - 0.15 = 0.21
        effective_weight = 0.225 + 0.135 + 0.15 = 0.51
        llm_score = 0.21 / 0.51 ≈ 0.412
        """
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.9, DataStatus.REAL),
            make_signal("MacroAgent", "LONG", 0.9, DataStatus.MISSING),
            make_signal("SentimentAgent", "LONG", 0.9, DataStatus.MISSING),
            make_signal("CorrelationAgent", "LONG", 0.9, DataStatus.REAL),
            make_signal("RiskAgent", "SHORT", 0.6, DataStatus.REAL),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.participating_agents == 3
        assert result.llm_score > 0.3  # Positive because more LONG than SHORT
        assert result.llm_signal == "LONG"
    
    def test_score_bounds(self):
        """llm_score should always be in [-1, +1]."""
        agg = ScoreAggregator()
        
        # All LONG with max confidence
        signals = [
            make_signal("TechnicalAgent", "LONG", 1.0),
            make_signal("MacroAgent", "LONG", 1.0),
            make_signal("SentimentAgent", "LONG", 1.0),
            make_signal("CorrelationAgent", "LONG", 1.0),
            make_signal("RiskAgent", "LONG", 1.0),
        ]
        
        result = agg.aggregate(signals)
        assert -1.0 <= result.llm_score <= 1.0
        assert result.llm_score == 1.0  # Should be exactly 1.0


class TestHybridBlend:
    """Test 60/40 blend calculation (via ParallelDecisionRunner)."""
    
    def test_blend_rules_positive_llm_negative(self):
        """rules_score=+1, llm_score=-1 -> hybrid_score=0.2."""
        from app.agents.parallel_runner import ParallelDecisionRunner
        
        runner = ParallelDecisionRunner()
        
        # Direct call to internal method
        hybrid_score, hybrid_signal = runner._compute_hybrid_score(
            rules_score=1.0,  # Strong LONG
            llm_score=-1.0,   # Strong SHORT
            risk_veto=False,
        )
        
        # 0.6 * 1.0 + 0.4 * (-1.0) = 0.6 - 0.4 = 0.2
        assert abs(hybrid_score - 0.2) < 0.01
        assert hybrid_signal == "LONG"  # 0.2 > threshold 0.15
    
    def test_blend_rules_negative_llm_positive(self):
        """rules_score=-1, llm_score=+1 -> hybrid_score=-0.2."""
        from app.agents.parallel_runner import ParallelDecisionRunner
        
        runner = ParallelDecisionRunner()
        
        hybrid_score, hybrid_signal = runner._compute_hybrid_score(
            rules_score=-1.0,
            llm_score=1.0,
            risk_veto=False,
        )
        
        # 0.6 * (-1.0) + 0.4 * 1.0 = -0.6 + 0.4 = -0.2
        assert abs(hybrid_score - (-0.2)) < 0.01
        assert hybrid_signal == "SHORT"  # -0.2 < -threshold
    
    def test_blend_both_positive(self):
        """Both positive -> stronger LONG."""
        from app.agents.parallel_runner import ParallelDecisionRunner
        
        runner = ParallelDecisionRunner()
        
        hybrid_score, _ = runner._compute_hybrid_score(
            rules_score=0.8,
            llm_score=0.6,
            risk_veto=False,
        )
        
        # 0.6 * 0.8 + 0.4 * 0.6 = 0.48 + 0.24 = 0.72
        assert abs(hybrid_score - 0.72) < 0.01
    
    def test_blend_risk_veto_overrides(self):
        """Risk veto -> HOLD regardless of scores."""
        from app.agents.parallel_runner import ParallelDecisionRunner
        
        runner = ParallelDecisionRunner()
        
        hybrid_score, hybrid_signal = runner._compute_hybrid_score(
            rules_score=1.0,
            llm_score=1.0,
            risk_veto=True,
        )
        
        assert hybrid_signal == "HOLD"


class TestNoHallucinations:
    """Test that agents don't "fantasize" without real data."""
    
    def test_macro_agent_abstains_when_mock(self):
        """MacroAgent with mock calendar returns MISSING status."""
        from app.agents.llm.macro_agent import MacroAgent
        from app.agents.llm.data_status import DataStatus
        
        agent = MacroAgent()
        
        # Context with mock calendar
        context = {
            "macro": {"_mock_mode": True},
            "economic_calendar": [],
            "source_health": {
                "economic_calendar": {"mock_mode": True},
            },
        }
        
        status = agent.check_data_status(context, "EURUSD")
        assert status == DataStatus.MISSING
    
    def test_sentiment_agent_abstains_when_mock(self):
        """SentimentAgent with mock COT returns MISSING status."""
        from app.agents.llm.sentiment_agent import SentimentAgent
        from app.agents.llm.data_status import DataStatus
        
        agent = SentimentAgent()
        
        # Context with mock COT
        context = {
            "cot_reports": {},
            "sentiment": {"_mock_mode": True},
            "source_health": {
                "cot_reports": {"mock_mode": True},
            },
        }
        
        status = agent.check_data_status(context, "EURUSD")
        assert status == DataStatus.MISSING
    
    def test_correlation_agent_real_with_dxy(self):
        """CorrelationAgent with real DXY returns REAL status."""
        from app.agents.llm.correlation_agent import CorrelationAgent
        from app.agents.llm.data_status import DataStatus
        
        agent = CorrelationAgent()
        
        # Context with real DXY
        context = {
            "dxy_snapshot": {"value": 103.5, "trend": "UP"},
            "source_health": {
                "dxy_index": {"mock_mode": False, "staleness_minutes": 5},
            },
        }
        
        status = agent.check_data_status(context, "EURUSD")
        assert status == DataStatus.REAL
    
    def test_technical_agent_always_real(self):
        """TechnicalAgent with IB Gateway data returns REAL status."""
        from app.agents.llm.technical_agent import TechnicalAgent
        from app.agents.llm.data_status import DataStatus
        
        agent = TechnicalAgent()
        
        # Context with technical data
        context = {
            "technical": {"trend_short": "UP", "rsi_zone": "NEUTRAL"},
        }
        
        status = agent.check_data_status(context, "EURUSD")
        assert status == DataStatus.REAL


class TestEmptyAndEdgeCases:
    """Test edge cases."""
    
    def test_empty_signals(self):
        """Empty signals list -> HOLD."""
        agg = ScoreAggregator()
        result = agg.aggregate([])
        
        assert result.llm_score == 0.0
        assert result.llm_signal == "HOLD"
        assert result.total_agents == 0
        assert "NO_AGENTS" in result.flags
    
    def test_single_agent(self):
        """Single agent works correctly."""
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.8),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.llm_signal == "LONG"
        assert result.llm_score == 1.0  # (1 * 0.25 * 0.8) / (0.25 * 0.8) = 1.0
        assert result.participating_agents == 1
    
    def test_to_dict_serialization(self):
        """Result can be serialized to dict."""
        agg = ScoreAggregator()
        signals = [
            make_signal("TechnicalAgent", "LONG", 0.8),
            make_signal("RiskAgent", "HOLD", 0.5),
        ]
        
        result = agg.aggregate(signals)
        d = result.to_dict()
        
        assert "llm_score" in d
        assert "llm_signal" in d
        assert "agent_scores" in d
        assert isinstance(d["agent_scores"], dict)
