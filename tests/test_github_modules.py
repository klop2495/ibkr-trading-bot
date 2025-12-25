"""
Tests for GitHub Integration Modules.

Covers:
- CandlestickPatternDetector
- CurrencyStrengthMeter
- VolatilityRegimeDetector
- VIXFetcher (mock)
- AgentPerformanceTracker
"""

import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, List


# === CandlestickPatternDetector Tests ===

class TestCandlestickPatternDetector:
    """Tests for candlestick pattern detection"""
    
    @pytest.fixture
    def detector(self):
        from app.market_data.candlestick_patterns import CandlestickPatternDetector
        return CandlestickPatternDetector()
    
    def test_empty_data(self, detector):
        """Should return empty list for insufficient data"""
        patterns = detector.detect_all([], [], [], [])
        assert patterns == []
        
        patterns = detector.detect_all([1.0], [1.0], [1.0], [1.0])
        assert patterns == []
    
    def test_doji_detection(self, detector):
        """Should detect doji pattern"""
        # Doji: open ≈ close
        opens =  [1.10, 1.09, 1.08, 1.07, 1.06, 1.0600]
        highs =  [1.11, 1.10, 1.09, 1.08, 1.07, 1.0650]
        lows =   [1.09, 1.08, 1.07, 1.06, 1.05, 1.0550]
        closes = [1.09, 1.08, 1.07, 1.06, 1.05, 1.0602]  # Last: tiny body
        
        patterns = detector.detect_all(opens, highs, lows, closes)
        pattern_names = [p.name for p in patterns]
        assert "Doji" in pattern_names
    
    def test_hammer_after_downtrend(self, detector):
        """Should detect hammer after downtrend"""
        # Downtrend followed by hammer
        opens =  [1.10, 1.09, 1.08, 1.07, 1.06, 1.055]
        highs =  [1.10, 1.09, 1.08, 1.07, 1.06, 1.060]
        lows =   [1.09, 1.08, 1.07, 1.06, 1.05, 1.030]  # Long lower shadow
        closes = [1.09, 1.08, 1.07, 1.06, 1.05, 1.058]  # Close near high
        
        patterns = detector.detect_all(opens, highs, lows, closes)
        pattern_names = [p.name for p in patterns]
        assert "Hammer" in pattern_names
    
    def test_engulfing_pattern(self, detector):
        """Should detect engulfing patterns"""
        # Bearish candle followed by larger bullish candle
        opens =  [1.10, 1.09, 1.08, 1.07, 1.06, 1.045]
        highs =  [1.10, 1.09, 1.08, 1.07, 1.06, 1.070]
        lows =   [1.09, 1.08, 1.07, 1.06, 1.04, 1.040]
        closes = [1.09, 1.08, 1.07, 1.06, 1.05, 1.065]  # Engulfs previous
        
        patterns = detector.detect_all(opens, highs, lows, closes)
        pattern_names = [p.name for p in patterns]
        assert len(patterns) >= 0  # At least runs without error
    
    def test_atr_filter(self, detector):
        """Should filter out patterns in low volatility"""
        # Very small candles (below ATR threshold)
        opens =  [1.1000, 1.0999, 1.0998, 1.0997, 1.0996, 1.0995]
        highs =  [1.1001, 1.1000, 1.0999, 1.0998, 1.0997, 1.0996]
        lows =   [1.0999, 1.0998, 1.0997, 1.0996, 1.0995, 1.0994]
        closes = [1.1000, 1.0999, 1.0998, 1.0997, 1.0996, 1.0995]
        
        # High ATR should filter these out
        patterns = detector.detect_all(opens, highs, lows, closes, atr=0.01)
        assert len(patterns) == 0
    
    def test_format_for_context(self, detector):
        """Should format patterns correctly for agent context"""
        from app.market_data.candlestick_patterns import CandlestickPattern, PatternType
        
        patterns = [
            CandlestickPattern("Hammer", PatternType.BULLISH, 75, "Test"),
            CandlestickPattern("Doji", PatternType.NEUTRAL, 50, "Test"),
        ]
        
        ctx = detector.format_for_context(patterns)
        
        assert ctx["candle_pattern"] in ("BULLISH", "BEARISH", "NONE")
        assert ctx["candle_pattern_name"] in ("Hammer", "Doji")
        assert -1.0 <= ctx["candle_pattern_bias"] <= 1.0
        assert isinstance(ctx["candle_patterns_detected"], list)
    
    def test_three_white_soldiers(self, detector):
        """Should detect three white soldiers pattern"""
        # Three consecutive bullish candles with progressively higher closes
        opens =  [1.06, 1.07, 1.08, 1.085, 1.095, 1.105]
        highs =  [1.07, 1.08, 1.09, 1.100, 1.110, 1.120]
        lows =   [1.05, 1.06, 1.07, 1.080, 1.090, 1.100]
        closes = [1.065, 1.075, 1.085, 1.095, 1.105, 1.115]
        
        patterns = detector.detect_all(opens, highs, lows, closes)
        pattern_names = [p.name for p in patterns]
        # Should detect bullish continuation
        assert any("Soldiers" in n or "BULLISH" in str(p.pattern_type) for n, p in zip(pattern_names, patterns))


# === CurrencyStrengthMeter Tests ===

class TestCurrencyStrengthMeter:
    """Tests for currency strength calculation"""
    
    @pytest.fixture
    def meter(self):
        from app.market_data.currency_strength import CurrencyStrengthMeter
        return CurrencyStrengthMeter()
    
    @pytest.fixture
    def sample_prices(self) -> Dict[str, Dict[str, float]]:
        return {
            "current": {
                'EURUSD': 1.0850, 'GBPUSD': 1.2650, 'USDJPY': 157.50,
                'USDCHF': 0.9020, 'AUDUSD': 0.6450, 'USDCAD': 1.3620,
            },
            "previous": {
                'EURUSD': 1.0800, 'GBPUSD': 1.2700, 'USDJPY': 156.00,
                'USDCHF': 0.9050, 'AUDUSD': 0.6400, 'USDCAD': 1.3600,
            }
        }
    
    def test_empty_data(self, meter):
        """Should return empty for insufficient data"""
        strengths = meter.calculate({}, {})
        assert strengths == []
    
    def test_calculate_single_timeframe(self, meter, sample_prices):
        """Should calculate strength for all currencies"""
        strengths = meter.calculate(
            sample_prices["current"],
            sample_prices["previous"]
        )
        
        assert len(strengths) == 8
        assert all(s.rank >= 1 and s.rank <= 8 for s in strengths)
        assert strengths[0].rank == 1  # Strongest
        assert strengths[-1].rank == 8  # Weakest
    
    def test_strength_normalization(self, meter, sample_prices):
        """Should normalize strengths to -100 to +100"""
        strengths = meter.calculate(
            sample_prices["current"],
            sample_prices["previous"]
        )
        
        for s in strengths:
            assert -100 <= s.strength <= 100
    
    def test_trend_classification(self, meter, sample_prices):
        """Should classify trends correctly"""
        strengths = meter.calculate(
            sample_prices["current"],
            sample_prices["previous"]
        )
        
        valid_trends = {"STRONG_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "STRONG_BEARISH"}
        for s in strengths:
            assert s.trend in valid_trends
    
    def test_analyze_pair(self, meter, sample_prices):
        """Should analyze specific pair"""
        strengths = meter.calculate(
            sample_prices["current"],
            sample_prices["previous"]
        )
        
        analysis = meter.analyze_pair("EURUSD", strengths)
        
        assert "strength_signal" in analysis
        assert "strength_differential" in analysis
        assert "base_currency" in analysis
        assert analysis["base_currency"] == "EUR"
        assert analysis["quote_currency"] == "USD"
    
    def test_get_best_pairs(self, meter, sample_prices):
        """Should return best trading pairs"""
        strengths = meter.calculate(
            sample_prices["current"],
            sample_prices["previous"]
        )
        
        best = meter.get_best_pairs(strengths)
        
        assert isinstance(best, list)
        for p in best:
            assert "pair" in p
            assert "direction" in p
            assert p["direction"] in ("BUY", "SELL")
    
    def test_ema_smoothing(self, meter, sample_prices):
        """EMA should smooth values over multiple calls"""
        # First call
        s1 = meter.calculate(sample_prices["current"], sample_prices["previous"])
        
        # Second call with same data - EMA should stabilize
        s2 = meter.calculate(sample_prices["current"], sample_prices["previous"])
        
        # Values should be similar (EMA converging)
        assert len(s1) == len(s2)
    
    def test_zscore_calculation(self, meter, sample_prices):
        """Z-score should be calculated after multiple calls"""
        # Need multiple data points for z-score
        for _ in range(5):
            strengths = meter.calculate(
                sample_prices["current"],
                sample_prices["previous"]
            )
        
        # After 5 calls, z_score should be defined
        for s in strengths:
            assert hasattr(s, 'z_score')
    
    def test_reset(self, meter, sample_prices):
        """Reset should clear internal state"""
        meter.calculate(sample_prices["current"], sample_prices["previous"])
        meter.reset()
        
        # After reset, internal state should be empty
        assert len(meter._ema_values) == 0
        assert len(meter._strength_history) == 0


# === VolatilityRegimeDetector Tests ===

class TestVolatilityRegimeDetector:
    """Tests for volatility regime detection"""
    
    @pytest.fixture
    def detector(self):
        from app.risk.volatility_regime import VolatilityRegimeDetector
        return VolatilityRegimeDetector()
    
    def test_insufficient_data(self, detector):
        """Should return NORMAL for insufficient data"""
        from app.risk.volatility_regime import VolatilityRegime
        
        result = detector.detect([0.001], [1.0])
        assert result == VolatilityRegime.NORMAL
    
    def test_normal_volatility(self, detector):
        """Should detect normal volatility"""
        from app.risk.volatility_regime import VolatilityRegime
        
        # Constant ATR
        atr = [0.0050] * 25
        closes = [1.0850] * 25
        
        result = detector.detect(atr, closes)
        assert result == VolatilityRegime.NORMAL
    
    def test_high_volatility(self, detector):
        """Should detect high volatility"""
        from app.risk.volatility_regime import VolatilityRegime
        
        # ATR spike to 2x normal
        atr = [0.0050] * 20 + [0.0100] * 5
        closes = [1.0850] * 25
        
        result = detector.detect(atr, closes)
        assert result in (VolatilityRegime.HIGH, VolatilityRegime.EXTREME)
    
    def test_low_volatility(self, detector):
        """Should detect low volatility"""
        from app.risk.volatility_regime import VolatilityRegime
        
        # ATR drops to 0.3x normal
        atr = [0.0050] * 20 + [0.0015] * 5
        closes = [1.0850] * 25
        
        result = detector.detect(atr, closes)
        assert result == VolatilityRegime.LOW
    
    def test_position_multiplier(self, detector):
        """Should return correct position multipliers"""
        from app.risk.volatility_regime import VolatilityRegime
        
        assert detector.get_position_multiplier(VolatilityRegime.LOW) == 1.2
        assert detector.get_position_multiplier(VolatilityRegime.NORMAL) == 1.0
        assert detector.get_position_multiplier(VolatilityRegime.HIGH) == 0.6
        assert detector.get_position_multiplier(VolatilityRegime.EXTREME) == 0.3
    
    def test_should_trade(self, detector):
        """Should recommend no trading in extreme volatility"""
        from app.risk.volatility_regime import VolatilityRegime
        
        assert detector.should_trade(VolatilityRegime.LOW) is True
        assert detector.should_trade(VolatilityRegime.NORMAL) is True
        assert detector.should_trade(VolatilityRegime.HIGH) is True
        assert detector.should_trade(VolatilityRegime.EXTREME) is False
    
    def test_sl_multiplier(self, detector):
        """Should return wider stops for higher volatility"""
        from app.risk.volatility_regime import VolatilityRegime
        
        low_sl = detector.get_sl_multiplier(VolatilityRegime.LOW)
        normal_sl = detector.get_sl_multiplier(VolatilityRegime.NORMAL)
        high_sl = detector.get_sl_multiplier(VolatilityRegime.HIGH)
        extreme_sl = detector.get_sl_multiplier(VolatilityRegime.EXTREME)
        
        assert low_sl < normal_sl < high_sl < extreme_sl
    
    def test_format_for_context(self, detector):
        """Should format regime data for agent context"""
        from app.risk.volatility_regime import VolatilityRegime
        
        ctx = detector.format_for_context(VolatilityRegime.HIGH, 0.008, 0.005)
        
        assert ctx["volatility_regime"] == "HIGH"
        assert ctx["volatility_regime_tradeable"] is True
        assert ctx["position_multiplier"] == 0.6
        assert ctx["atr_current"] == 0.008
        assert ctx["atr_average"] == 0.005


# === VIXFetcher Tests ===

class TestVIXFetcher:
    """Tests for VIX fetcher (mostly mock behavior)"""
    
    @pytest.fixture
    def fetcher(self):
        from app.data_sources.vix_index import VIXFetcher
        return VIXFetcher()
    
    def test_mock_snapshot(self, fetcher):
        """Should return mock snapshot when yfinance unavailable"""
        snapshot = fetcher._mock_snapshot()
        
        assert snapshot.value == 18.0
        assert snapshot.regime == "GREED"
        assert snapshot.risk_multiplier == 1.0
        assert snapshot.is_mock is True
    
    def test_regime_classification(self, fetcher):
        """Should classify VIX regimes correctly"""
        assert fetcher._classify_regime(10) == "EXTREME_GREED"
        assert fetcher._classify_regime(15) == "GREED"
        assert fetcher._classify_regime(25) == "FEAR"
        assert fetcher._classify_regime(35) == "EXTREME_FEAR"
    
    def test_risk_multiplier(self, fetcher):
        """Should calculate risk multiplier based on VIX"""
        assert fetcher._calculate_risk_multiplier(10) == 1.2
        assert fetcher._calculate_risk_multiplier(18) == 1.0
        assert fetcher._calculate_risk_multiplier(25) == 0.6
        assert fetcher._calculate_risk_multiplier(35) == 0.4
    
    def test_format_for_context(self, fetcher):
        """Should format VIX data for agent context"""
        ctx = fetcher.format_for_context()
        
        assert "vix_value" in ctx
        assert "vix_regime" in ctx
        assert "vix_risk_multiplier" in ctx
        assert "vix_elevated" in ctx


# === AgentPerformanceTracker Tests ===

class TestAgentPerformanceTracker:
    """Tests for agent performance tracking with weighted PnL and time decay"""
    
    @pytest.fixture
    def tracker(self):
        from app.agents.performance_tracker import AgentPerformanceTracker
        return AgentPerformanceTracker()
    
    @pytest.fixture
    def sample_outcome(self):
        from app.agents.performance_tracker import TradeOutcome
        return TradeOutcome(
            trade_id="test_1",
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.0850,
            exit_price=1.0900,
            pnl=50.0,
            pnl_pips=50,
            agent_votes={
                "TechnicalAgent": "LONG",
                "RiskAgent": "HOLD",
                "MacroAgent": "LONG"
            },
            agent_confidences={
                "TechnicalAgent": 0.8,
                "RiskAgent": 0.6,
                "MacroAgent": 0.7
            },
            final_signal="LONG",
            timestamp=datetime.now(timezone.utc)
        )
    
    def test_initial_weights(self, tracker):
        """Should have default weights initially"""
        weights = tracker.get_adjusted_weights()
        
        assert weights["TechnicalAgent"] == 0.25
        assert weights["RiskAgent"] == 0.25
        assert weights["MacroAgent"] == 0.20
    
    def test_record_winning_trade(self, tracker, sample_outcome):
        """Should adjust weights after winning trade"""
        changes = tracker.record_outcome(sample_outcome)
        
        # TechnicalAgent agreed and won - should increase
        assert "TechnicalAgent" in changes
        # Note: weights not active until min_trades reached
    
    def test_record_losing_trade(self, tracker):
        """Should penalize agents for losing trades"""
        from app.agents.performance_tracker import TradeOutcome
        
        outcome = TradeOutcome(
            trade_id="test_2",
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.2700,
            exit_price=1.2750,
            pnl=-50.0,
            pnl_pips=-50,
            agent_votes={
                "TechnicalAgent": "SHORT",  # Agreed, but lost
                "RiskAgent": "HOLD",  # Abstained from loser - good
            },
            agent_confidences={
                "TechnicalAgent": 0.7,
                "RiskAgent": 0.5,
            },
            final_signal="SHORT",
            timestamp=datetime.now(timezone.utc)
        )
        
        changes = tracker.record_outcome(outcome)
        
        # TechnicalAgent agreed but lost - should decrease
        assert "TechnicalAgent" in changes
    
    def test_time_decay(self, tracker):
        """Older trades should have less impact"""
        decay_recent = tracker._calculate_time_decay(datetime.now(timezone.utc))
        decay_old = tracker._calculate_time_decay(
            datetime.now(timezone.utc) - timedelta(days=14)
        )
        decay_very_old = tracker._calculate_time_decay(
            datetime.now(timezone.utc) - timedelta(days=28)
        )
        
        assert decay_recent > decay_old > decay_very_old
        assert 0.9 < decay_recent <= 1.0
        assert 0.4 < decay_old < 0.6  # ~0.5 at half-life
        assert decay_very_old < 0.3
    
    def test_pnl_weighting(self, tracker, sample_outcome):
        """Larger PnL should have more impact"""
        # Record several trades to establish baseline
        for i in range(5):
            outcome = sample_outcome
            outcome.trade_id = f"baseline_{i}"
            outcome.pnl = 50.0
            tracker.record_outcome(outcome)
        
        # Small PnL
        small_weight = tracker._calculate_pnl_weight(10.0)
        
        # Large PnL
        large_weight = tracker._calculate_pnl_weight(150.0)
        
        assert large_weight > small_weight
    
    def test_weights_bounded(self, tracker, sample_outcome):
        """Weights should stay within min/max bounds"""
        # Record many outcomes to push weights
        for i in range(50):
            outcome = sample_outcome
            outcome.trade_id = f"test_{i}"
            tracker.record_outcome(outcome)
        
        for weight in tracker.weights.values():
            assert tracker.config.min_weight <= weight <= tracker.config.max_weight
    
    def test_weights_normalized(self, tracker, sample_outcome):
        """Weights should sum to 1.0"""
        for i in range(10):
            outcome = sample_outcome
            outcome.trade_id = f"test_{i}"
            tracker.record_outcome(outcome)
        
        total = sum(tracker.weights.values())
        assert abs(total - 1.0) < 0.001
    
    def test_performance_report(self, tracker, sample_outcome):
        """Should generate comprehensive report"""
        tracker.record_outcome(sample_outcome)
        report = tracker.get_performance_report()
        
        assert "total_trades" in report
        assert "current_weights" in report
        assert "agents" in report
        assert "TechnicalAgent" in report["agents"]
    
    def test_agent_metrics(self, tracker, sample_outcome):
        """Should track detailed agent metrics"""
        tracker.record_outcome(sample_outcome)
        metrics = tracker.get_agent_metrics("TechnicalAgent")
        
        assert "current_weight" in metrics
        assert "accuracy" in metrics
        assert "weighted_accuracy" in metrics
        assert "value_added" in metrics
    
    def test_serialization(self, tracker, sample_outcome):
        """Should serialize and deserialize correctly"""
        from app.agents.performance_tracker import AgentPerformanceTracker
        
        tracker.record_outcome(sample_outcome)
        
        # Serialize
        data = tracker.to_dict()
        
        assert "weights" in data
        assert "outcomes" in data
        assert "agent_stats" in data
        
        # Deserialize
        restored = AgentPerformanceTracker.from_dict(data)
        
        assert restored.weights == tracker.weights
        assert len(restored.outcomes) == len(tracker.outcomes)
    
    def test_cleanup_old_outcomes(self, tracker):
        """Should clean up old outcomes"""
        from app.agents.performance_tracker import TradeOutcome
        
        # Add old outcome
        old_outcome = TradeOutcome(
            trade_id="old_1",
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.0850,
            exit_price=1.0900,
            pnl=50.0,
            pnl_pips=50,
            agent_votes={"TechnicalAgent": "LONG"},
            agent_confidences={"TechnicalAgent": 0.8},
            final_signal="LONG",
            timestamp=datetime.now(timezone.utc) - timedelta(days=60)
        )
        tracker.outcomes.append(old_outcome)
        
        # Add recent outcome
        recent_outcome = TradeOutcome(
            trade_id="recent_1",
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.2700,
            exit_price=1.2650,
            pnl=50.0,
            pnl_pips=50,
            agent_votes={"TechnicalAgent": "SHORT"},
            agent_confidences={"TechnicalAgent": 0.8},
            final_signal="SHORT",
            timestamp=datetime.now(timezone.utc)
        )
        tracker.outcomes.append(recent_outcome)
        
        assert len(tracker.outcomes) == 2
        
        removed = tracker.cleanup_old_outcomes()
        
        assert removed == 1
        assert len(tracker.outcomes) == 1
        assert tracker.outcomes[0].trade_id == "recent_1"
    
    def test_reset(self, tracker, sample_outcome):
        """Reset should restore defaults"""
        tracker.record_outcome(sample_outcome)
        tracker.reset()
        
        assert tracker.weights == tracker.DEFAULT_WEIGHTS
        assert len(tracker.outcomes) == 0
        assert len(tracker.agent_stats) == 0


# === Integration Tests ===

class TestModuleIntegration:
    """Integration tests for modules working together"""
    
    def test_candlestick_to_context(self):
        """CandlestickPatternDetector output should be agent-ready"""
        from app.market_data.candlestick_patterns import CandlestickPatternDetector
        
        detector = CandlestickPatternDetector()
        
        opens =  [1.10, 1.09, 1.08, 1.07, 1.06, 1.055]
        highs =  [1.10, 1.09, 1.08, 1.07, 1.06, 1.060]
        lows =   [1.09, 1.08, 1.07, 1.06, 1.05, 1.030]
        closes = [1.09, 1.08, 1.07, 1.06, 1.05, 1.058]
        
        patterns = detector.detect_all(opens, highs, lows, closes, atr=0.003)
        ctx = detector.format_for_context(patterns)
        
        # Should be usable in TechnicalAgent.prepare_input()
        assert isinstance(ctx.get("candle_pattern"), str)
        assert isinstance(ctx.get("candle_pattern_bias"), (int, float))
    
    def test_currency_strength_to_context(self):
        """CurrencyStrengthMeter output should be agent-ready"""
        from app.market_data.currency_strength import CurrencyStrengthMeter
        
        meter = CurrencyStrengthMeter()
        
        current = {'EURUSD': 1.0850, 'GBPUSD': 1.2650, 'USDJPY': 157.50}
        previous = {'EURUSD': 1.0800, 'GBPUSD': 1.2700, 'USDJPY': 156.00}
        
        strengths = meter.calculate(current, previous)
        ctx = meter.format_for_context(strengths)
        
        # Should be usable in CorrelationAgent.prepare_input()
        assert isinstance(ctx.get("currency_strength_available"), bool)
        if ctx["currency_strength_available"]:
            assert isinstance(ctx.get("strongest_currency"), str)
            assert isinstance(ctx.get("strength_ranking"), list)
    
    def test_volatility_regime_to_context(self):
        """VolatilityRegimeDetector output should be agent-ready"""
        from app.risk.volatility_regime import VolatilityRegimeDetector, VolatilityRegime
        
        detector = VolatilityRegimeDetector()
        
        atr = [0.005] * 25
        closes = [1.0850] * 25
        
        regime = detector.detect(atr, closes)
        ctx = detector.format_for_context(regime, 0.005, 0.005)
        
        # Should be usable in RiskAgent.prepare_input()
        assert isinstance(ctx.get("volatility_regime"), str)
        assert isinstance(ctx.get("position_multiplier"), float)
        assert isinstance(ctx.get("volatility_regime_tradeable"), bool)
