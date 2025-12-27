"""
Tests for Phase 4: Integration.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from app.models.signal_preview import (
    SignalPreviewV1, Direction, Confidence, SetupType,
    DataQuality, SpreadQuality, TimeframeTrigger
)
from app.models.decision import DecisionV1
from app.models.confidence import ConfidenceLevel
from app.agents.llm.context_builder import ContextBuilder, AgentContext
from app.agents.parallel_runner import ParallelDecisionRunner, create_parallel_runner


class TestAgentContext:
    """Tests for AgentContext dataclass."""
    
    def test_basic_context(self):
        """Test creating basic context."""
        ctx = AgentContext(
            symbol="EURUSD",
            timestamp=datetime.now(timezone.utc),
        )
        
        assert ctx.symbol == "EURUSD"
        assert ctx.technical == {}
        assert ctx.macro == {}
    
    def test_to_dict(self):
        """Test converting context to dict."""
        ctx = AgentContext(
            symbol="GBPUSD",
            timestamp=datetime.now(timezone.utc),
            technical={"trend_short": "UP"},
            macro={"EUR_cb_stance": "HAWKISH"},
        )
        
        d = ctx.to_dict()
        
        assert d["symbol"] == "GBPUSD"
        assert d["technical"]["trend_short"] == "UP"
        assert "timestamp" in d


class TestContextBuilder:
    """Tests for ContextBuilder."""
    
    def test_initialization(self):
        """Test builder initialization."""
        builder = ContextBuilder()
        
        assert builder.economic_calendar is None
        assert builder.cot_reports is None
        assert builder.dxy_fetcher is None
    
    def test_build_basic(self):
        """Test building basic context."""
        builder = ContextBuilder()
        ctx = builder.build("EURUSD")
        
        assert ctx.symbol == "EURUSD"
        assert ctx.timestamp is not None
        assert "EUR_cb_stance" in ctx.macro
        assert "USD_cb_stance" in ctx.macro
    
    def test_build_with_preview(self):
        """Test building context with signal preview."""
        builder = ContextBuilder()
        
        preview = SignalPreviewV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe_trigger=TimeframeTrigger.M15,
            setup_type=SetupType.SWING_CONTINUATION,
            direction=Direction.LONG,
            setup_present=True,
            entry_triggered=True,
            confidence=Confidence.HIGH,
            rr=2.0,
            data_quality=DataQuality.OK,
            spread_quality=SpreadQuality.OK,
            flags=["strong_trend"],
        )
        
        ctx = builder.build("EURUSD", signal_preview=preview)
        
        assert ctx.technical["trend_short"] == "UP"
        assert ctx.technical["sma_alignment"] == "BULLISH"
    
    def test_build_with_short_preview(self):
        """Test building context with short signal."""
        builder = ContextBuilder()
        
        preview = SignalPreviewV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="USDJPY",
            timeframe_trigger=TimeframeTrigger.M15,
            setup_type=SetupType.SWING_REVERSAL,
            direction=Direction.SHORT,
            setup_present=True,
            entry_triggered=True,
            confidence=Confidence.NORMAL,
            rr=1.5,
            data_quality=DataQuality.OK,
            spread_quality=SpreadQuality.OK,
            flags=[],
        )
        
        ctx = builder.build("USDJPY", signal_preview=preview)
        
        assert ctx.technical["trend_short"] == "DOWN"
        assert ctx.technical["sma_alignment"] == "BEARISH"
    
    def test_build_session_detection(self):
        """Test session detection."""
        builder = ContextBuilder()
        ctx = builder.build("EURUSD")
        
        assert ctx.session["current"] in ("TOKYO", "LONDON", "OVERLAP", "NEW_YORK", "INACTIVE")
        assert isinstance(ctx.session["optimal_for_symbol"], bool)
    
    def test_build_with_account_state(self):
        """Test building context with account state."""
        builder = ContextBuilder()
        
        account = {
            "drawdown_pct": 3.5,
            "daily_loss_limit_near": False,
            "exposure_level": "LOW",
        }
        
        ctx = builder.build("EURUSD", account_state=account)
        
        assert ctx.account["drawdown_level"] == "MEDIUM"
        assert ctx.account["open_exposure_level"] == "LOW"
    
    def test_macro_cb_stance(self):
        """Test central bank stance mapping."""
        builder = ContextBuilder()
        
        # EURUSD
        ctx = builder.build("EURUSD")
        assert "EUR_cb_stance" in ctx.macro
        assert "USD_cb_stance" in ctx.macro
        
        # USDJPY
        ctx = builder.build("USDJPY")
        assert "USD_cb_stance" in ctx.macro
        assert "JPY_cb_stance" in ctx.macro


class TestParallelDecisionRunner:
    """Tests for ParallelDecisionRunner."""
    
    def test_initialization_default(self):
        """Test default initialization."""
        runner = ParallelDecisionRunner()
        
        assert runner.active_strategy == "hybrid"
        assert runner.llm_enabled is False
        assert runner.agents == []
    
    def test_initialization_with_llm(self):
        """Test initialization with LLM enabled."""
        runner = ParallelDecisionRunner(
            active_strategy="hybrid",
            llm_enabled=True,
        )
        
        assert runner.active_strategy == "hybrid"
        assert runner.llm_enabled is True
    
    def test_run_shadow_mode(self):
        """Test running in shadow mode (Phase 0)."""
        runner = ParallelDecisionRunner(active_strategy="rules", llm_enabled=False)
        
        preview = SignalPreviewV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe_trigger=TimeframeTrigger.M15,
            setup_type=SetupType.SWING_CONTINUATION,
            direction=Direction.LONG,
            setup_present=True,
            entry_triggered=True,
            confidence=Confidence.HIGH,
            rr=2.0,
            data_quality=DataQuality.OK,
            spread_quality=SpreadQuality.OK,
            flags=[],
        )
        
        decision = DecisionV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="EURUSD",
            signal_preview_id=uuid4(),
            trade_allowed=True,
            risk_modifier=1.0,
            flags=["TREND_STRONG"],
        )
        
        result = runner.run(preview, uuid4(), decision)
        
        assert result.symbol == "EURUSD"
        assert result.rules_signal == "LONG"
        assert result.rules_confidence == "high"
        assert result.gpt_signal == "HOLD"  # LLM contour disabled -> HOLD
        assert result.executed_strategy == "rules"
        assert result.executed_signal == "LONG"
    
    def test_run_with_trade_not_allowed(self):
        """Test running when trade not allowed."""
        runner = ParallelDecisionRunner(active_strategy="rules", llm_enabled=False)
        
        preview = SignalPreviewV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="GBPUSD",
            timeframe_trigger=TimeframeTrigger.M15,
            setup_type=SetupType.NO_TRADE,
            direction=Direction.FLAT,
            setup_present=False,
            entry_triggered=False,
            confidence=Confidence.LOW,
            rr=0.0,
            data_quality=DataQuality.OK,
            spread_quality=SpreadQuality.OK,
            flags=[],
        )
        
        decision = DecisionV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="GBPUSD",
            signal_preview_id=uuid4(),
            trade_allowed=False,
            risk_modifier=1.0,
            flags=[],
        )
        
        result = runner.run(preview, uuid4(), decision)
        
        assert result.rules_signal == "HOLD"
        assert result.executed_signal == "HOLD"
    
    def test_strategy_selection_rules(self):
        """Test strategy selection - rules."""
        runner = ParallelDecisionRunner(active_strategy="rules")
        
        strategy, signal = runner._select_strategy("LONG", "SHORT", "HOLD")
        
        assert strategy == "rules"
        assert signal == "LONG"
    
    def test_strategy_selection_gpt(self):
        """Test strategy selection - gpt."""
        runner = ParallelDecisionRunner(active_strategy="llm")
        
        strategy, signal = runner._select_strategy("LONG", "SHORT", "HOLD")
        
        assert strategy == "llm"
        assert signal == "SHORT"
    
    def test_strategy_selection_hybrid(self):
        """Test strategy selection - hybrid."""
        runner = ParallelDecisionRunner(active_strategy="hybrid")
        
        strategy, signal = runner._select_strategy("LONG", "SHORT", "HOLD")
        
        assert strategy == "hybrid"
        assert signal == "HOLD"
    
    def test_compute_hybrid_long(self):
        """Test hybrid computation favoring LONG."""
        runner = ParallelDecisionRunner()
        
        # Positive rules + positive llm score
        score, signal = runner._compute_hybrid_score(rules_score=0.9, llm_score=0.5, risk_veto=False)
        
        assert signal == "LONG"
        assert score > 0
    
    def test_compute_hybrid_mixed(self):
        """Test hybrid computation with mixed signals."""
        runner = ParallelDecisionRunner()
        
        # Opposing contributions should cancel out
        score, signal = runner._compute_hybrid_score(rules_score=0.3, llm_score=-0.3, risk_veto=False)
        
        # Should be close to zero or HOLD
        assert abs(score) < 0.2
        assert signal == "HOLD"
    
    def test_get_stats(self):
        """Test getting runner stats."""
        runner = ParallelDecisionRunner(llm_enabled=True)
        
        stats = runner.get_stats()
        
        assert stats["calls_total"] == 0
        assert stats["llm_enabled"] is True
        assert stats["active_strategy"] == "hybrid"


class TestCreateParallelRunner:
    """Tests for factory function."""
    
    def test_create_shadow_runner(self):
        """Test creating shadow mode runner."""
        runner = create_parallel_runner(llm_enabled=False)
        
        assert runner.llm_enabled is False
        assert runner.budget_limiter is not None
        assert runner.agent_cache is not None
        assert runner.response_validator is not None
        assert len(runner.agents) == 5
    
    def test_create_llm_runner(self):
        """Test creating LLM-enabled runner."""
        runner = create_parallel_runner(
            llm_enabled=True,
            active_strategy="hybrid",
        )
        
        assert runner.llm_enabled is True
        assert runner.active_strategy == "hybrid"
        assert runner.aggregator is not None


class TestIntegrationPipeline:
    """Integration tests for full pipeline."""
    
    def test_full_pipeline_shadow(self):
        """Test full pipeline in shadow mode."""
        # Create runner
        runner = create_parallel_runner(llm_enabled=False, active_strategy="rules")
        
        # Create preview
        preview = SignalPreviewV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="AUDUSD",
            timeframe_trigger=TimeframeTrigger.M15,
            setup_type=SetupType.SWING_CONTINUATION,
            direction=Direction.SHORT,
            setup_present=True,
            entry_triggered=True,
            confidence=Confidence.NORMAL,
            rr=1.8,
            data_quality=DataQuality.OK,
            spread_quality=SpreadQuality.OK,
            flags=["downtrend"],
        )
        
        # Create decision
        decision = DecisionV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="AUDUSD",
            signal_preview_id=uuid4(),
            trade_allowed=True,
            risk_modifier=0.8,
            flags=["RISK_REDUCED"],
        )
        
        # Run
        result = runner.run(preview, uuid4(), decision)
        
        # Verify
        assert result.symbol == "AUDUSD"
        assert result.rules_signal == "SHORT"
        assert result.gpt_signal == "HOLD"  # Shadow mode
        assert result.hybrid_signal in ("SHORT", "HOLD")
        assert result.executed_strategy == "rules"
        
        # Check stats
        stats = runner.get_stats()
        assert stats["calls_total"] == 1
    
    def test_full_pipeline_mock_llm(self):
        """Test full pipeline with mock LLM agents."""
        # Create runner with LLM enabled but no client (mock mode)
        runner = create_parallel_runner(
            llm_enabled=True,
            active_strategy="hybrid",
        )
        
        # Create preview
        preview = SignalPreviewV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe_trigger=TimeframeTrigger.M15,
            setup_type=SetupType.SWING_CONTINUATION,
            direction=Direction.LONG,
            setup_present=True,
            entry_triggered=True,
            confidence=Confidence.HIGH,
            rr=2.5,
            data_quality=DataQuality.OK,
            spread_quality=SpreadQuality.OK,
            flags=[],
        )
        
        decision = DecisionV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="EURUSD",
            signal_preview_id=uuid4(),
            trade_allowed=True,
            risk_modifier=1.0,
            flags=[],
        )
        
        # Run
        result = runner.run(preview, uuid4(), decision)
        
        # Verify
        assert result.symbol == "EURUSD"
        assert result.rules_signal == "LONG"
        # GPT should have real mock responses now
        assert result.gpt_signal in ("LONG", "SHORT", "HOLD")
        assert result.executed_strategy == "hybrid"
        
        # Check that agents were called
        stats = runner.get_stats()
        assert stats["calls_total"] == 1
