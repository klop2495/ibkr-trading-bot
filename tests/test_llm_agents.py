"""
Tests for Phase 3: LLM Agents.
"""

import pytest
from datetime import datetime, timezone

from app.models.confidence import ConfidenceLevel
from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.agents.llm.technical_agent import TechnicalAgent
from app.agents.llm.macro_agent import MacroAgent
from app.agents.llm.sentiment_agent import SentimentAgent
from app.agents.llm.correlation_agent import CorrelationAgent
from app.agents.llm.risk_agent import RiskAgent
from app.agents.llm.aggregator import WeightedAggregator, AggregatedDecision


class TestAgentSignal:
    """Tests for AgentSignal dataclass."""
    
    def test_basic_signal(self):
        """Test creating a basic signal."""
        signal = AgentSignal(
            agent_name="TestAgent",
            signal="LONG",
            confidence=ConfidenceLevel.HIGH,
            reasoning="Test reasoning",
            flags=["TEST_FLAG"],
        )
        
        assert signal.agent_name == "TestAgent"
        assert signal.signal == "LONG"
        assert signal.confidence == ConfidenceLevel.HIGH
        assert "TEST_FLAG" in signal.flags
    
    def test_to_dict(self):
        """Test converting signal to dict."""
        signal = AgentSignal(
            agent_name="TechnicalAgent",
            signal="SHORT",
            confidence=ConfidenceLevel.MEDIUM,
            reasoning="Strong downtrend with bearish momentum",
            flags=["TREND_DOWN", "RSI_OVERSOLD"],
        )
        
        d = signal.to_dict()
        
        assert d["agent"] == "TechnicalAgent"
        assert d["signal"] == "SHORT"
        assert d["confidence"] == "medium"
        assert "TREND_DOWN" in d["flags"]


class TestTechnicalAgent:
    """Tests for TechnicalAgent."""
    
    def test_initialization(self):
        """Test agent initialization."""
        agent = TechnicalAgent()
        
        assert agent.name == "TechnicalAgent"
        assert agent.weight == 0.25
        assert agent._mock_mode is True  # No LLM client
    
    def test_prepare_input(self):
        """Test input preparation."""
        agent = TechnicalAgent()
        context = {
            "technical": {
                "trend_short": "UP",
                "trend_medium": "UP",
                "rsi_zone": "NEUTRAL",
                "volatility_level": "NORMAL",
            }
        }
        
        result = agent.prepare_input(context, "EURUSD")
        
        assert result["symbol"] == "EURUSD"
        assert result["data"]["trend_short"] == "UP"
        assert result["data"]["rsi_zone"] == "NEUTRAL"
    
    def test_mock_call_uptrend(self):
        """Test mock call with uptrend."""
        agent = TechnicalAgent()
        context = {
            "technical": {
                "trend_short": "UP",
                "rsi_zone": "NEUTRAL",
            }
        }
        
        signal = agent.call(context, "EURUSD")
        
        assert signal.agent_name == "TechnicalAgent"
        assert signal.signal == "LONG"
        assert signal.confidence == ConfidenceLevel.MEDIUM
        assert "TREND_UP" in signal.flags
    
    def test_mock_call_downtrend(self):
        """Test mock call with downtrend."""
        agent = TechnicalAgent()
        context = {
            "technical": {
                "trend_short": "DOWN",
                "rsi_zone": "NEUTRAL",
            }
        }
        
        signal = agent.call(context, "GBPUSD")
        
        assert signal.signal == "SHORT"
        assert "TREND_DOWN" in signal.flags
    
    def test_mock_call_overbought(self):
        """Test mock call with overbought RSI."""
        agent = TechnicalAgent()
        context = {
            "technical": {
                "trend_short": "UP",
                "rsi_zone": "OVERBOUGHT",  # Should prevent LONG
            }
        }
        
        signal = agent.call(context, "EURUSD")
        
        assert signal.signal == "HOLD"


class TestMacroAgent:
    """Tests for MacroAgent."""
    
    def test_initialization(self):
        """Test agent initialization."""
        agent = MacroAgent()
        
        assert agent.name == "MacroAgent"
        assert agent.weight == 0.20
    
    def test_mock_call_event_risk(self):
        """Test mock call with high-impact event."""
        agent = MacroAgent()
        context = {
            "macro": {},
            "economic_calendar": [
                {"currency": "EUR", "impact": "HIGH", "timing_category": "IMMINENT"}
            ]
        }
        
        signal = agent.call(context, "EURUSD")
        
        assert signal.signal == "HOLD"
        assert "EVENT_RISK" in signal.flags
    
    def test_mock_call_hawkish_base(self):
        """Test mock call with hawkish base currency."""
        agent = MacroAgent()
        context = {
            "macro": {
                "EUR_cb_stance": "HAWKISH",
                "USD_cb_stance": "NEUTRAL",
            },
            "economic_calendar": []
        }
        
        signal = agent.call(context, "EURUSD")
        
        assert signal.signal == "LONG"
        assert "CB_DIVERGENCE" in signal.flags


class TestSentimentAgent:
    """Tests for SentimentAgent."""
    
    def test_initialization(self):
        """Test agent initialization."""
        agent = SentimentAgent()
        
        assert agent.name == "SentimentAgent"
        assert agent.weight == 0.15
    
    def test_mock_call_crowded_long(self):
        """Test mock call with crowded long positioning."""
        agent = SentimentAgent()
        context = {
            "cot_reports": {
                "EUR": {
                    "bias": "LONG",
                    "percentile_bucket": "EXTREME_HIGH",
                }
            }
        }
        
        signal = agent.call(context, "EURUSD")
        
        assert signal.signal == "SHORT"  # Contrarian
        assert "CROWDED_LONG" in signal.flags
    
    def test_mock_call_crowded_short(self):
        """Test mock call with crowded short positioning."""
        agent = SentimentAgent()
        context = {
            "cot_reports": {
                "EUR": {
                    "bias": "SHORT",
                    "percentile_bucket": "EXTREME_LOW",
                }
            }
        }
        
        signal = agent.call(context, "EURUSD")
        
        assert signal.signal == "LONG"  # Contrarian


class TestCorrelationAgent:
    """Tests for CorrelationAgent."""
    
    def test_initialization(self):
        """Test agent initialization."""
        agent = CorrelationAgent()
        
        assert agent.name == "CorrelationAgent"
        assert agent.weight == 0.15
    
    def test_dxy_impact_calculation(self):
        """Test DXY impact calculation for different pairs."""
        agent = CorrelationAgent()
        
        # DXY up = USD strong = EURUSD down
        impact = agent._calculate_dxy_impact("EURUSD", {"trend": "UP"})
        assert impact == "BEARISH"
        
        # DXY up = USD strong = USDJPY up
        impact = agent._calculate_dxy_impact("USDJPY", {"trend": "UP"})
        assert impact == "BULLISH"
        
        # DXY down = USD weak = EURUSD up
        impact = agent._calculate_dxy_impact("EURUSD", {"trend": "DOWN"})
        assert impact == "BULLISH"
    
    def test_mock_call_divergence(self):
        """Test mock call with divergence detected."""
        agent = CorrelationAgent()
        context = {
            "dxy_snapshot": {"trend": "UP"},
            "correlations": {
                "EURUSD": {"divergence": True}
            }
        }
        
        signal = agent.call(context, "EURUSD")
        
        assert signal.signal == "HOLD"
        assert "DIVERGENCE_WARNING" in signal.flags


class TestRiskAgent:
    """Tests for RiskAgent."""
    
    def test_initialization(self):
        """Test agent initialization."""
        agent = RiskAgent()
        
        assert agent.name == "RiskAgent"
        assert agent.weight == 0.25  # High weight for risk
    
    def test_mock_call_high_volatility(self):
        """Test mock call with high volatility."""
        agent = RiskAgent()
        context = {
            "risk": {"volatility_regime": "HIGH"},
            "session": {"current": "LONDON"},
            "account": {},
            "economic_calendar": [],
        }
        
        signal = agent.call(context, "EURUSD")
        
        assert signal.signal == "HOLD"
        assert "HIGH_VOLATILITY" in signal.flags
    
    def test_mock_call_event_risk(self):
        """Test mock call with event risk."""
        agent = RiskAgent()
        context = {
            "risk": {"volatility_regime": "NORMAL"},
            "session": {"current": "LONDON"},
            "account": {},
            "economic_calendar": [
                {"impact": "HIGH", "timing_category": "IMMINENT"}
            ],
        }
        
        signal = agent.call(context, "EURUSD")
        
        assert signal.signal == "HOLD"
        assert "EVENT_RISK" in signal.flags
    
    def test_mock_call_inactive_session(self):
        """Test mock call with inactive session."""
        agent = RiskAgent()
        context = {
            "risk": {"volatility_regime": "NORMAL"},
            "session": {"current": "INACTIVE"},
            "account": {},
            "economic_calendar": [],
        }
        
        signal = agent.call(context, "EURUSD")
        
        assert signal.signal == "HOLD"
        assert "SESSION_INACTIVE" in signal.flags
    
    def test_mock_call_acceptable_risk(self):
        """Test mock call with acceptable risk."""
        agent = RiskAgent()
        context = {
            "risk": {"volatility_regime": "NORMAL"},
            "session": {"current": "LONDON", "optimal_for_symbol": True},
            "account": {"drawdown_level": "SMALL"},
            "economic_calendar": [],
        }
        
        signal = agent.call(context, "EURUSD")
        
        # RiskAgent doesn't pick direction, just validates
        assert signal.signal == "HOLD"
        assert signal.confidence in (ConfidenceLevel.LOW, ConfidenceLevel.MEDIUM)


class TestWeightedAggregator:
    """Tests for WeightedAggregator."""
    
    def test_initialization(self):
        """Test aggregator initialization."""
        agg = WeightedAggregator()
        
        assert agg.weights["TechnicalAgent"] == 0.25
        assert agg.weights["RiskAgent"] == 0.25
        assert agg.signal_threshold == 0.15
    
    def test_aggregate_unanimous_long(self):
        """Test aggregation with unanimous LONG."""
        agg = WeightedAggregator()
        signals = [
            AgentSignal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH, "trend up", []),
            AgentSignal("MacroAgent", "LONG", ConfidenceLevel.HIGH, "hawkish", []),
            AgentSignal("SentimentAgent", "LONG", ConfidenceLevel.HIGH, "crowded short", []),
            AgentSignal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH, "dxy supports", []),
            AgentSignal("RiskAgent", "LONG", ConfidenceLevel.HIGH, "risk ok", []),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.signal == "LONG"
        assert result.confidence == ConfidenceLevel.HIGH
        assert result.consensus_level == "STRONG"
        assert result.vote_score > 0.5
    
    def test_aggregate_unanimous_short(self):
        """Test aggregation with unanimous SHORT."""
        agg = WeightedAggregator()
        signals = [
            AgentSignal("TechnicalAgent", "SHORT", ConfidenceLevel.HIGH, "trend down", []),
            AgentSignal("MacroAgent", "SHORT", ConfidenceLevel.MEDIUM, "dovish", []),
            AgentSignal("SentimentAgent", "SHORT", ConfidenceLevel.MEDIUM, "crowded long", []),
            AgentSignal("CorrelationAgent", "SHORT", ConfidenceLevel.LOW, "dxy supports", []),
            AgentSignal("RiskAgent", "SHORT", ConfidenceLevel.MEDIUM, "risk ok", []),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.signal == "SHORT"
        assert result.vote_score < -0.5
    
    def test_aggregate_mixed_signals(self):
        """Test aggregation with mixed signals."""
        agg = WeightedAggregator()
        signals = [
            AgentSignal("TechnicalAgent", "LONG", ConfidenceLevel.LOW, "weak up", []),
            AgentSignal("MacroAgent", "SHORT", ConfidenceLevel.LOW, "weak down", []),
            AgentSignal("SentimentAgent", "HOLD", ConfidenceLevel.LOW, "neutral", []),
            AgentSignal("CorrelationAgent", "HOLD", ConfidenceLevel.LOW, "mixed", []),
            AgentSignal("RiskAgent", "HOLD", ConfidenceLevel.LOW, "uncertain", []),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.signal == "HOLD"
        assert abs(result.vote_score) < 0.15
        # 3 of 5 agents returned HOLD = 60% = STRONG consensus for HOLD
        assert result.consensus_level == "STRONG"
        assert result.confidence == ConfidenceLevel.LOW
    
    def test_aggregate_risk_veto(self):
        """Test aggregation with RiskAgent veto."""
        agg = WeightedAggregator(risk_veto_enabled=True)
        signals = [
            AgentSignal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH, "trend up", []),
            AgentSignal("MacroAgent", "LONG", ConfidenceLevel.HIGH, "hawkish", []),
            AgentSignal("SentimentAgent", "LONG", ConfidenceLevel.HIGH, "contrarian", []),
            AgentSignal("CorrelationAgent", "LONG", ConfidenceLevel.HIGH, "aligned", []),
            AgentSignal("RiskAgent", "HOLD", ConfidenceLevel.HIGH, "HIGH VOLATILITY", ["HIGH_VOLATILITY"]),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.signal == "HOLD"  # Vetoed by RiskAgent
        assert result.risk_blocked is True
        assert "RISK_VETO" in result.flags
    
    def test_aggregate_risk_veto_disabled(self):
        """Test aggregation with RiskAgent veto disabled."""
        agg = WeightedAggregator(risk_veto_enabled=False)
        signals = [
            AgentSignal("TechnicalAgent", "LONG", ConfidenceLevel.HIGH, "trend up", []),
            AgentSignal("MacroAgent", "LONG", ConfidenceLevel.HIGH, "hawkish", []),
            AgentSignal("RiskAgent", "HOLD", ConfidenceLevel.HIGH, "risk elevated", []),
        ]
        
        result = agg.aggregate(signals)
        
        assert result.signal == "LONG"  # Not vetoed
        assert result.risk_blocked is False
    
    def test_aggregate_empty(self):
        """Test aggregation with no signals."""
        agg = WeightedAggregator()
        result = agg.aggregate([])
        
        assert result.signal == "HOLD"
        assert result.confidence == ConfidenceLevel.LOW
        assert result.agents_count == 0
        assert "NO_AGENTS" in result.flags
    
    def test_to_dict(self):
        """Test converting decision to dict."""
        decision = AggregatedDecision(
            signal="LONG",
            confidence=ConfidenceLevel.HIGH,
            vote_score=0.75,
            consensus_level="STRONG",
            agent_signals={"TechnicalAgent": "LONG"},
            agent_confidences={"TechnicalAgent": "high"},
            flags=["TREND_UP"],
            agents_count=5,
            risk_blocked=False,
        )
        
        d = decision.to_dict()
        
        assert d["signal"] == "LONG"
        assert d["confidence"] == "high"
        assert d["vote_score"] == 0.75
        assert d["consensus_level"] == "STRONG"


class TestAllAgentsIntegration:
    """Integration tests for all agents working together."""
    
    def test_full_pipeline_bullish(self):
        """Test full pipeline with bullish context."""
        # Create all agents
        technical = TechnicalAgent()
        macro = MacroAgent()
        sentiment = SentimentAgent()
        correlation = CorrelationAgent()
        risk = RiskAgent()
        aggregator = WeightedAggregator()
        
        # Bullish context
        context = {
            "technical": {
                "trend_short": "UP",
                "rsi_zone": "NEUTRAL",
            },
            "macro": {
                "EUR_cb_stance": "HAWKISH",
                "USD_cb_stance": "NEUTRAL",
            },
            "economic_calendar": [],
            "cot_reports": {
                "EUR": {"bias": "NEUTRAL", "percentile_bucket": "MEDIUM"}
            },
            "dxy_snapshot": {"trend": "DOWN"},  # USD weak = EURUSD up
            "correlations": {},
            "risk": {"volatility_regime": "NORMAL"},
            "session": {"current": "LONDON", "optimal_for_symbol": True},
            "account": {},
        }
        
        # Get signals from all agents
        signals = [
            technical.call(context, "EURUSD"),
            macro.call(context, "EURUSD"),
            sentiment.call(context, "EURUSD"),
            correlation.call(context, "EURUSD"),
            risk.call(context, "EURUSD"),
        ]
        
        # Aggregate
        result = aggregator.aggregate(signals)
        
        # Should lean bullish
        assert result.agents_count == 5
        assert result.signal in ("LONG", "HOLD")  # May be HOLD due to RiskAgent
        assert len(result.agent_signals) == 5
