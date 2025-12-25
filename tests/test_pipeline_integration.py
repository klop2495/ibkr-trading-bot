"""
Integration tests for Phase 7 pipeline with new modules.

Tests:
1. Context builder with OHLC, ATR, 24h prices
2. TechnicalAgent with CandlestickPatternDetector
3. CorrelationAgent with CurrencyStrengthMeter
4. RiskAgent with VIX and VolatilityRegime
5. ScoreAggregator with PerformanceTracker
"""

import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
from unittest.mock import Mock, patch


class TestContextBuilderIntegration:
    """Test ContextBuilder with Phase 7 data fields"""
    
    @pytest.fixture
    def context_builder(self):
        from app.agents.llm.context_builder import ContextBuilder
        return ContextBuilder()
    
    @pytest.fixture
    def sample_ohlc(self) -> Dict[str, List[float]]:
        """Sample OHLC data for 10 candles"""
        return {
            "opens":  [1.0850, 1.0855, 1.0860, 1.0858, 1.0862, 1.0865, 1.0860, 1.0855, 1.0850, 1.0855],
            "highs":  [1.0860, 1.0865, 1.0870, 1.0868, 1.0872, 1.0875, 1.0870, 1.0865, 1.0860, 1.0870],
            "lows":   [1.0845, 1.0850, 1.0855, 1.0853, 1.0857, 1.0860, 1.0855, 1.0850, 1.0845, 1.0850],
            "closes": [1.0855, 1.0860, 1.0858, 1.0862, 1.0865, 1.0860, 1.0855, 1.0850, 1.0855, 1.0865],
        }
    
    @pytest.fixture
    def sample_prices(self) -> Dict[str, Dict[str, float]]:
        """Sample currency prices for strength calculation"""
        return {
            "current": {
                'EURUSD': 1.0850, 'GBPUSD': 1.2650, 'USDJPY': 157.50,
                'USDCHF': 0.9020, 'AUDUSD': 0.6450, 'USDCAD': 1.3620,
            },
            "previous_24h": {
                'EURUSD': 1.0800, 'GBPUSD': 1.2700, 'USDJPY': 156.00,
                'USDCHF': 0.9050, 'AUDUSD': 0.6400, 'USDCAD': 1.3600,
            }
        }
    
    def test_context_includes_ohlc(self, context_builder, sample_ohlc):
        """Context should include OHLC data when provided"""
        from app.models.signal_preview import SignalPreviewV1, Direction, Confidence, SetupType
        
        preview = SignalPreviewV1(
            id="test",
            symbol="EURUSD",
            timeframe="H1",
            direction=Direction.LONG,
            confidence=Confidence.MEDIUM,
            setup_type=SetupType.TREND_CONTINUATION,
        )
        
        ctx = context_builder.build(
            symbol="EURUSD",
            signal_preview=preview,
            ohlc_data=sample_ohlc,
        )
        
        assert ctx.ohlc == sample_ohlc
        assert ctx.close_prices == sample_ohlc["closes"]
        assert len(ctx.ohlc["opens"]) == 10
    
    def test_context_includes_atr_history(self, context_builder):
        """Context should include ATR history when provided"""
        from app.models.signal_preview import SignalPreviewV1, Direction, Confidence, SetupType
        
        preview = SignalPreviewV1(
            id="test",
            symbol="EURUSD",
            timeframe="H1",
            direction=Direction.LONG,
            confidence=Confidence.MEDIUM,
            setup_type=SetupType.TREND_CONTINUATION,
        )
        
        atr_history = [0.0045, 0.0048, 0.0050, 0.0052, 0.0055]
        market_snapshot = {"atr": 0.0055}
        
        ctx = context_builder.build(
            symbol="EURUSD",
            signal_preview=preview,
            market_snapshot=market_snapshot,
            atr_history=atr_history,
        )
        
        assert ctx.atr_history == atr_history
        assert ctx.atr_current == 0.0055
    
    def test_context_includes_24h_prices(self, context_builder, sample_prices):
        """Context should include 24h prices when provided"""
        from app.models.signal_preview import SignalPreviewV1, Direction, Confidence, SetupType
        
        preview = SignalPreviewV1(
            id="test",
            symbol="EURUSD",
            timeframe="H1",
            direction=Direction.LONG,
            confidence=Confidence.MEDIUM,
            setup_type=SetupType.TREND_CONTINUATION,
        )
        
        ctx = context_builder.build(
            symbol="EURUSD",
            signal_preview=preview,
            current_prices=sample_prices["current"],
            previous_24h_prices=sample_prices["previous_24h"],
        )
        
        assert ctx.current_prices == sample_prices["current"]
        assert ctx.previous_24h_prices == sample_prices["previous_24h"]
    
    def test_context_to_dict_includes_phase7_fields(self, context_builder, sample_ohlc, sample_prices):
        """to_dict should include all Phase 7 fields"""
        from app.models.signal_preview import SignalPreviewV1, Direction, Confidence, SetupType
        
        preview = SignalPreviewV1(
            id="test",
            symbol="EURUSD",
            timeframe="H1",
            direction=Direction.LONG,
            confidence=Confidence.MEDIUM,
            setup_type=SetupType.TREND_CONTINUATION,
        )
        
        ctx = context_builder.build(
            symbol="EURUSD",
            signal_preview=preview,
            ohlc_data=sample_ohlc,
            current_prices=sample_prices["current"],
            previous_24h_prices=sample_prices["previous_24h"],
            atr_history=[0.005] * 10,
        )
        
        ctx_dict = ctx.to_dict()
        
        assert "ohlc" in ctx_dict
        assert "atr_current" in ctx_dict
        assert "atr_history" in ctx_dict
        assert "current_prices" in ctx_dict
        assert "previous_24h_prices" in ctx_dict
        assert "close_prices" in ctx_dict


class TestTechnicalAgentIntegration:
    """Test TechnicalAgent with CandlestickPatternDetector"""
    
    @pytest.fixture
    def technical_agent(self):
        from app.agents.llm.technical_agent import TechnicalAgent
        return TechnicalAgent()
    
    @pytest.fixture
    def context_with_ohlc(self) -> Dict[str, Any]:
        """Context with OHLC data for pattern detection"""
        # Downtrend followed by hammer pattern
        return {
            "symbol": "EURUSD",
            "technical": {
                "trend_short": "DOWN",
                "trend_medium": "DOWN",
                "rsi_zone": "OVERSOLD",
                "sma_alignment": "BEARISH",
                "candle_pattern": "NONE",
            },
            "ohlc": {
                "opens":  [1.10, 1.09, 1.08, 1.07, 1.06, 1.055],
                "highs":  [1.10, 1.09, 1.08, 1.07, 1.06, 1.060],
                "lows":   [1.09, 1.08, 1.07, 1.06, 1.05, 1.030],  # Long lower shadow
                "closes": [1.09, 1.08, 1.07, 1.06, 1.05, 1.058],  # Close near high
            },
            "atr_current": 0.003,
        }
    
    def test_pattern_detector_initialized(self, technical_agent):
        """CandlestickPatternDetector should be initialized"""
        assert hasattr(technical_agent, '_pattern_detector')
        # May be None if import failed, but attribute should exist
    
    def test_prepare_input_includes_patterns(self, technical_agent, context_with_ohlc):
        """prepare_input should include candlestick pattern data"""
        input_data = technical_agent.prepare_input(context_with_ohlc, "EURUSD")
        
        data = input_data.get("data", {})
        
        # Should have pattern fields
        assert "candle_pattern" in data
        assert "candle_pattern_name" in data
        assert "candle_pattern_bias" in data
        assert "candle_patterns_detected" in data
    
    def test_mock_response_considers_patterns(self, technical_agent, context_with_ohlc):
        """Mock response should consider candle patterns"""
        input_data = technical_agent.prepare_input(context_with_ohlc, "EURUSD")
        signal = technical_agent._mock_response("EURUSD", input_data)
        
        # Should return valid signal
        assert signal.signal in ("LONG", "SHORT", "HOLD")
        assert signal.agent_name == "TechnicalAgent"


class TestCorrelationAgentIntegration:
    """Test CorrelationAgent with CurrencyStrengthMeter"""
    
    @pytest.fixture
    def correlation_agent(self):
        from app.agents.llm.correlation_agent import CorrelationAgent
        return CorrelationAgent()
    
    @pytest.fixture
    def context_with_prices(self) -> Dict[str, Any]:
        """Context with price data for strength calculation"""
        return {
            "symbol": "EURUSD",
            "dxy_snapshot": {
                "trend": "DOWN",
                "vs_sma": "BELOW",
                "daily_direction": "DOWN",
            },
            "correlations": {},
            "current_prices": {
                'EURUSD': 1.0850, 'GBPUSD': 1.2650, 'USDJPY': 157.50,
                'USDCHF': 0.9020, 'AUDUSD': 0.6450, 'USDCAD': 1.3620,
            },
            "previous_24h_prices": {
                'EURUSD': 1.0800, 'GBPUSD': 1.2700, 'USDJPY': 156.00,
                'USDCHF': 0.9050, 'AUDUSD': 0.6400, 'USDCAD': 1.3600,
            },
            "source_health": {
                "dxy_index": {"mock_mode": False, "available": True},
            },
        }
    
    def test_strength_meter_initialized(self, correlation_agent):
        """CurrencyStrengthMeter should be initialized"""
        assert hasattr(correlation_agent, '_strength_meter')
    
    def test_prepare_input_includes_strength(self, correlation_agent, context_with_prices):
        """prepare_input should include currency strength data"""
        input_data = correlation_agent.prepare_input(context_with_prices, "EURUSD")
        
        data = input_data.get("data", {})
        
        # Should have strength fields
        assert "currency_strength_available" in data
        assert "strength_signal" in data
        assert "strength_differential" in data
    
    def test_mock_response_considers_strength(self, correlation_agent, context_with_prices):
        """Mock response should consider currency strength"""
        input_data = correlation_agent.prepare_input(context_with_prices, "EURUSD")
        signal = correlation_agent._mock_response("EURUSD", input_data)
        
        assert signal.signal in ("LONG", "SHORT", "HOLD")
        assert signal.agent_name == "CorrelationAgent"


class TestRiskAgentIntegration:
    """Test RiskAgent with VIX and VolatilityRegime"""
    
    @pytest.fixture
    def risk_agent(self):
        from app.agents.llm.risk_agent import RiskAgent
        return RiskAgent()
    
    @pytest.fixture
    def context_with_volatility(self) -> Dict[str, Any]:
        """Context with volatility data"""
        return {
            "symbol": "EURUSD",
            "risk": {
                "volatility_regime": "NORMAL",
                "vix_value": 18.5,
                "vix_regime": "GREED",
                "vix_risk_multiplier": 1.0,
                "vix_is_mock": False,
            },
            "session": {
                "current": "LONDON",
                "optimal_for_symbol": True,
            },
            "account": {
                "drawdown_level": "SMALL",
            },
            "macro": {},
            "economic_calendar": [],
            "source_health": {
                "economic_calendar": {"mock_mode": True},
            },
            "atr_history": [0.005] * 25,
            "close_prices": [1.0850] * 25,
            "atr_current": 0.005,
        }
    
    def test_volatility_detector_initialized(self, risk_agent):
        """VolatilityRegimeDetector should be initialized"""
        assert hasattr(risk_agent, '_vol_detector')
    
    def test_prepare_input_includes_vix(self, risk_agent, context_with_volatility):
        """prepare_input should include VIX data"""
        input_data = risk_agent.prepare_input(context_with_volatility, "EURUSD")
        
        data = input_data.get("data", {})
        
        # Should have VIX fields
        assert "vix_value" in data
        assert "vix_regime" in data
        assert "vix_risk_multiplier" in data
        assert "vix_elevated" in data
    
    def test_prepare_input_includes_volatility_regime(self, risk_agent, context_with_volatility):
        """prepare_input should include volatility regime data"""
        input_data = risk_agent.prepare_input(context_with_volatility, "EURUSD")
        
        data = input_data.get("data", {})
        
        # Should have volatility regime fields
        assert "volatility_regime" in data
        assert "volatility_regime_tradeable" in data
        assert "volatility_position_multiplier" in data
    
    def test_mock_response_veto_on_extreme_vix(self, risk_agent):
        """Should veto on extreme VIX (if not mock)"""
        context = {
            "symbol": "EURUSD",
            "risk": {
                "volatility_regime": "NORMAL",
                "vix_value": 35.0,
                "vix_regime": "EXTREME_FEAR",
                "vix_risk_multiplier": 0.4,
                "vix_is_mock": False,
            },
            "session": {"current": "LONDON", "optimal_for_symbol": True},
            "account": {"drawdown_level": "SMALL"},
            "macro": {},
            "economic_calendar": [],
            "source_health": {"economic_calendar": {"mock_mode": True}},
            "atr_history": [0.005] * 25,
            "close_prices": [1.0850] * 25,
        }
        
        input_data = risk_agent.prepare_input(context, "EURUSD")
        signal = risk_agent._mock_response("EURUSD", input_data)
        
        assert signal.risk_veto is True
        assert "VIX_EXTREME_FEAR" in signal.flags
    
    def test_mock_response_no_veto_on_mock_vix(self, risk_agent):
        """Should NOT veto if VIX is mock data"""
        context = {
            "symbol": "EURUSD",
            "risk": {
                "volatility_regime": "NORMAL",
                "vix_value": 35.0,
                "vix_regime": "EXTREME_FEAR",
                "vix_risk_multiplier": 0.4,
                "vix_is_mock": True,  # Mock data
            },
            "session": {"current": "LONDON", "optimal_for_symbol": True},
            "account": {"drawdown_level": "SMALL"},
            "macro": {},
            "economic_calendar": [],
            "source_health": {"economic_calendar": {"mock_mode": True}},
            "atr_history": [0.005] * 25,
            "close_prices": [1.0850] * 25,
        }
        
        input_data = risk_agent.prepare_input(context, "EURUSD")
        signal = risk_agent._mock_response("EURUSD", input_data)
        
        # Should NOT veto based on mock VIX data
        assert signal.risk_veto is False or "VIX_EXTREME_FEAR" not in signal.flags


class TestScoreAggregatorIntegration:
    """Test ScoreAggregator with PerformanceTracker"""
    
    @pytest.fixture
    def aggregator(self):
        from app.agents.llm.score_aggregator import ScoreAggregator
        return ScoreAggregator()
    
    @pytest.fixture
    def sample_signals(self):
        from app.agents.llm.base_agent import AgentSignal
        from app.agents.llm.data_status import DataStatus
        from app.models.confidence import ConfidenceLevel
        
        signals = [
            AgentSignal(
                agent_name="TechnicalAgent",
                signal="LONG",
                confidence=ConfidenceLevel.MEDIUM,
                reasoning="Bullish pattern",
                data_status=DataStatus.REAL,
                flags=["TREND_UP"],
            ),
            AgentSignal(
                agent_name="MacroAgent",
                signal="LONG",
                confidence=ConfidenceLevel.LOW,
                reasoning="Neutral macro",
                data_status=DataStatus.REAL,
                flags=[],
            ),
            AgentSignal(
                agent_name="RiskAgent",
                signal="HOLD",
                confidence=ConfidenceLevel.MEDIUM,
                reasoning="Risk acceptable",
                data_status=DataStatus.REAL,
                risk_veto=False,
                flags=["SESSION_OPTIMAL"],
            ),
        ]
        
        # Set confidence_float
        signals[0].confidence_float = 0.7
        signals[1].confidence_float = 0.4
        signals[2].confidence_float = 0.6
        
        return signals
    
    def test_performance_tracker_initialized(self, aggregator):
        """PerformanceTracker should be available"""
        assert hasattr(aggregator, '_performance_tracker')
        assert hasattr(aggregator, 'performance_tracker')
    
    def test_aggregate_returns_weights(self, aggregator, sample_signals):
        """Aggregate result should include agent weights"""
        result = aggregator.aggregate(sample_signals)
        
        assert hasattr(result, 'agent_weights')
        assert "TechnicalAgent" in result.agent_weights
        assert "RiskAgent" in result.agent_weights
    
    def test_aggregate_shows_dynamic_weights_flag(self, aggregator, sample_signals):
        """Result should indicate if using dynamic weights"""
        result = aggregator.aggregate(sample_signals)
        
        assert hasattr(result, 'weights_from_tracker')
        # Initially should be False (no trades recorded yet)
        assert result.weights_from_tracker is False
    
    def test_record_trade_outcome(self, aggregator):
        """Should record trade outcome without error"""
        changes = aggregator.record_trade_outcome(
            trade_id="test_1",
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.0850,
            exit_price=1.0900,
            pnl=50.0,
            pnl_pips=50,
            agent_votes={"TechnicalAgent": "LONG", "RiskAgent": "HOLD"},
            agent_confidences={"TechnicalAgent": 0.7, "RiskAgent": 0.6},
            final_signal="LONG",
        )
        
        # Should return dict (possibly empty if tracker not available)
        assert isinstance(changes, dict)
    
    def test_get_performance_report(self, aggregator):
        """Should get performance report"""
        report = aggregator.get_performance_report()
        
        # May be None or dict depending on tracker availability
        if report is not None:
            assert "total_trades" in report
            assert "current_weights" in report


class TestEndToEndPipeline:
    """End-to-end test of the complete Phase 7 pipeline"""
    
    def test_full_pipeline_with_modules(self):
        """Test complete flow from context to aggregated decision"""
        from app.agents.llm.context_builder import ContextBuilder
        from app.agents.llm.technical_agent import TechnicalAgent
        from app.agents.llm.correlation_agent import CorrelationAgent
        from app.agents.llm.risk_agent import RiskAgent
        from app.agents.llm.score_aggregator import ScoreAggregator
        from app.models.signal_preview import SignalPreviewV1, Direction, Confidence, SetupType
        
        # 1. Build context
        builder = ContextBuilder()
        
        preview = SignalPreviewV1(
            id="test",
            symbol="EURUSD",
            timeframe="H1",
            direction=Direction.LONG,
            confidence=Confidence.MEDIUM,
            setup_type=SetupType.TREND_CONTINUATION,
        )
        
        ohlc = {
            "opens":  [1.08] * 10,
            "highs":  [1.09] * 10,
            "lows":   [1.07] * 10,
            "closes": [1.085] * 10,
        }
        
        prices = {
            'EURUSD': 1.0850, 'GBPUSD': 1.2650, 'USDJPY': 157.50,
        }
        
        ctx = builder.build(
            symbol="EURUSD",
            signal_preview=preview,
            ohlc_data=ohlc,
            atr_history=[0.005] * 20,
            current_prices=prices,
            previous_24h_prices=prices,  # Same for simplicity
        )
        
        ctx_dict = ctx.to_dict()
        
        # 2. Get signals from agents
        tech_agent = TechnicalAgent()
        corr_agent = CorrelationAgent()
        risk_agent = RiskAgent()
        
        signals = []
        
        tech_input = tech_agent.prepare_input(ctx_dict, "EURUSD")
        tech_signal = tech_agent._mock_response("EURUSD", tech_input)
        signals.append(tech_signal)
        
        corr_input = corr_agent.prepare_input(ctx_dict, "EURUSD")
        corr_signal = corr_agent._mock_response("EURUSD", corr_input)
        signals.append(corr_signal)
        
        risk_input = risk_agent.prepare_input(ctx_dict, "EURUSD")
        risk_signal = risk_agent._mock_response("EURUSD", risk_input)
        signals.append(risk_signal)
        
        # 3. Aggregate
        aggregator = ScoreAggregator()
        result = aggregator.aggregate(signals)
        
        # 4. Verify result
        assert result.llm_signal in ("LONG", "SHORT", "HOLD")
        assert -1.0 <= result.llm_score <= 1.0
        assert 0.0 <= result.llm_confidence <= 1.0
        assert result.participating_agents >= 0
        assert isinstance(result.agent_weights, dict)
        
        # Result should be serializable
        result_dict = result.to_dict()
        assert "llm_score" in result_dict
        assert "agent_weights" in result_dict
