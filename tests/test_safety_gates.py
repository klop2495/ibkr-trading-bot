"""
Tests for Phase 2: Safety Gates.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.confidence import ConfidenceLevel, confidence_to_weight, parse_confidence
from app.agents.safety.budget_limiter import BudgetLimiter
from app.agents.safety.agent_cache import AgentCache
from app.agents.safety.response_validator import ResponseValidator


class TestConfidenceLevel:
    """Tests for ConfidenceLevel enum and helpers."""
    
    def test_confidence_values(self):
        """Test enum values."""
        assert ConfidenceLevel.LOW.value == "low"
        assert ConfidenceLevel.MEDIUM.value == "medium"
        assert ConfidenceLevel.HIGH.value == "high"
    
    def test_confidence_to_weight(self):
        """Test weight mapping."""
        assert confidence_to_weight(ConfidenceLevel.LOW) == 0.3
        assert confidence_to_weight(ConfidenceLevel.MEDIUM) == 0.6
        assert confidence_to_weight(ConfidenceLevel.HIGH) == 0.9
    
    def test_parse_confidence(self):
        """Test parsing confidence strings."""
        assert parse_confidence("high") == ConfidenceLevel.HIGH
        assert parse_confidence("HIGH") == ConfidenceLevel.HIGH
        assert parse_confidence("h") == ConfidenceLevel.HIGH
        
        assert parse_confidence("medium") == ConfidenceLevel.MEDIUM
        assert parse_confidence("med") == ConfidenceLevel.MEDIUM
        assert parse_confidence("normal") == ConfidenceLevel.MEDIUM
        
        assert parse_confidence("low") == ConfidenceLevel.LOW
        assert parse_confidence("unknown") == ConfidenceLevel.LOW
        assert parse_confidence("") == ConfidenceLevel.LOW
        assert parse_confidence(None) == ConfidenceLevel.LOW


class TestBudgetLimiter:
    """Tests for BudgetLimiter."""
    
    def test_initial_state_allows_calls(self):
        """Test that fresh limiter allows calls."""
        limiter = BudgetLimiter(check_weekend=False)
        allowed, status = limiter.can_call()
        
        assert allowed is True
        assert status == "OK"
    
    def test_rate_limit(self):
        """Test rate limiting after too many calls."""
        limiter = BudgetLimiter(max_calls_per_minute=3, check_weekend=False)
        
        # Make 3 calls
        for _ in range(3):
            limiter.record_call()
        
        allowed, status = limiter.can_call()
        assert allowed is False
        assert status == "RATE_LIMIT"
    
    def test_budget_limit(self):
        """Test budget limiting after too much spend."""
        limiter = BudgetLimiter(max_cost_per_day=0.01, check_weekend=False)
        
        # Make expensive calls
        for _ in range(10):
            limiter.record_call(cost=0.002)
        
        allowed, status = limiter.can_call()
        assert allowed is False
        assert status == "BUDGET_LIMIT"
    
    def test_get_stats(self):
        """Test stats reporting."""
        limiter = BudgetLimiter(max_calls_per_minute=10, max_cost_per_day=1.0, check_weekend=False)
        limiter.record_call(cost=0.01)
        limiter.record_call(cost=0.02)
        
        stats = limiter.get_stats()
        assert stats["calls_this_minute"] == 2
        assert stats["cost_today_usd"] == 0.03
        assert stats["budget_remaining_usd"] == 0.97
        assert stats["rate_remaining"] == 8
    
    def test_weekend_check(self):
        """Test weekend check can be enabled/disabled."""
        # With check enabled (default)
        limiter_with_check = BudgetLimiter(check_weekend=True)
        stats = limiter_with_check.get_stats()
        assert "is_weekend" in stats
        assert "day_of_week" in stats
        
        # With check disabled - should NEVER return WEEKEND
        limiter_no_check = BudgetLimiter(check_weekend=False)
        allowed, status = limiter_no_check.can_call()
        assert status != "WEEKEND"
        assert allowed is True
        assert status == "OK"


class TestAgentCache:
    """Tests for AgentCache."""
    
    def test_cache_miss(self):
        """Test cache miss returns None."""
        cache = AgentCache(ttl_minutes=15)
        result = cache.get("TechnicalAgent", "EURUSD")
        
        assert result is None
        assert cache.get_stats()["misses"] == 1
    
    def test_cache_hit(self):
        """Test cache hit returns stored value."""
        cache = AgentCache(ttl_minutes=15)
        
        cache.set(
            agent_name="TechnicalAgent",
            symbol="EURUSD",
            signal="LONG",
            confidence="high",
            reasoning="Strong uptrend",
            flags=["TREND_UP"],
        )
        
        result = cache.get("TechnicalAgent", "EURUSD")
        
        assert result is not None
        assert result.signal == "LONG"
        assert result.confidence == "high"
        assert result.reasoning == "Strong uptrend"
        assert result.flags == ["TREND_UP"]
        assert cache.get_stats()["hits"] == 1
    
    def test_different_symbols_different_keys(self):
        """Test that different symbols have different cache entries."""
        cache = AgentCache(ttl_minutes=15)
        
        cache.set("TechnicalAgent", "EURUSD", "LONG", "high", "EUR bullish", [])
        cache.set("TechnicalAgent", "GBPUSD", "SHORT", "low", "GBP bearish", [])
        
        eur_result = cache.get("TechnicalAgent", "EURUSD")
        gbp_result = cache.get("TechnicalAgent", "GBPUSD")
        
        assert eur_result.signal == "LONG"
        assert gbp_result.signal == "SHORT"
    
    def test_get_key_bucketing(self):
        """Test that keys are bucketed by time."""
        cache = AgentCache(ttl_minutes=15)
        
        key = cache.get_key("TestAgent", "USDJPY")
        
        # Key should contain agent, symbol, and timestamp
        assert "TestAgent" in key
        assert "USDJPY" in key
    
    def test_clear_expired(self):
        """Test clearing expired entries."""
        cache = AgentCache(ttl_minutes=15)
        
        # Manually add an "expired" entry by manipulating the cache
        cache.set("TestAgent", "EURUSD", "LONG", "high", "test", [])
        
        # Clear expired (should clear nothing since entry is fresh)
        cleared = cache.clear_expired()
        assert cleared == 0
        assert len(cache._cache) == 1


class TestResponseValidator:
    """Tests for ResponseValidator."""
    
    def test_valid_response(self):
        """Test validation of a valid response."""
        validator = ResponseValidator()
        
        response = {
            "signal": "LONG",
            "confidence": "high",
            "reasoning": "Strong uptrend with bullish momentum",
        }
        
        is_valid, violation = validator.validate(response)
        assert is_valid is True
        assert violation is None
    
    def test_invalid_price_in_reasoning(self):
        """Test detection of price in reasoning."""
        validator = ResponseValidator()
        
        response = {
            "signal": "LONG",
            "confidence": "high",
            "reasoning": "Buy at 1.0732 for good entry",
        }
        
        is_valid, violation = validator.validate(response)
        assert is_valid is False
        assert violation == "price"
    
    def test_invalid_sl_tp_in_reasoning(self):
        """Test detection of SL/TP in reasoning."""
        validator = ResponseValidator()
        
        response = {
            "signal": "SHORT",
            "confidence": "medium",
            "reasoning": "Set stop loss at 50 pips",
        }
        
        is_valid, violation = validator.validate(response)
        assert is_valid is False
        assert violation == "sl_tp"
    
    def test_invalid_position_size(self):
        """Test detection of position size in reasoning."""
        validator = ResponseValidator()
        
        response = {
            "signal": "LONG",
            "confidence": "high",
            "reasoning": "Open 0.5 lot position",
        }
        
        is_valid, violation = validator.validate(response)
        assert is_valid is False
        assert violation == "position_size"
    
    def test_invalid_pip_calculation(self):
        """Test detection of pip calculation in reasoning."""
        validator = ResponseValidator()
        
        response = {
            "signal": "LONG",
            "confidence": "high",
            "reasoning": "Target 100 pips profit",
        }
        
        is_valid, violation = validator.validate(response)
        assert is_valid is False
        assert violation == "pip_calc"
    
    def test_invalid_signal(self):
        """Test detection of invalid signal value."""
        validator = ResponseValidator()
        
        response = {
            "signal": "BUY",  # Should be LONG
            "confidence": "high",
            "reasoning": "Bullish setup",
        }
        
        is_valid, violation = validator.validate(response)
        assert is_valid is False
        assert violation == "invalid_signal"
    
    def test_sanitize_valid(self):
        """Test sanitization of valid response."""
        validator = ResponseValidator()
        
        response = {
            "signal": "SHORT",
            "confidence": "medium",
            "reasoning": "Bearish divergence on RSI",
            "flags": ["RSI_DIVERGENCE"],
        }
        
        result = validator.sanitize(response)
        
        assert result.is_valid is True
        assert result.signal == "SHORT"
        assert result.confidence == ConfidenceLevel.MEDIUM
        assert result.reasoning == "Bearish divergence on RSI"
        assert "RSI_DIVERGENCE" in result.flags
    
    def test_sanitize_invalid(self):
        """Test sanitization of invalid response."""
        validator = ResponseValidator()
        
        response = {
            "signal": "LONG",
            "confidence": "high",
            "reasoning": "Enter at 1.0850 with TP at 1.0900",
        }
        
        result = validator.sanitize(response)
        
        assert result.is_valid is False
        assert result.signal == "HOLD"
        assert result.confidence == ConfidenceLevel.LOW
        assert "LLM_SANITIZED" in result.flags
        assert result.violation_type == "price"
    
    def test_get_stats(self):
        """Test stats reporting."""
        validator = ResponseValidator()
        
        # Valid response
        validator.validate({"signal": "LONG", "confidence": "high", "reasoning": "Good"})
        # Invalid response
        validator.validate({"signal": "LONG", "confidence": "high", "reasoning": "Buy at 1.0732"})
        
        stats = validator.get_stats()
        assert stats["total_validations"] == 2
        assert stats["failures"] == 1
        assert stats["failure_rate"] == 0.5
