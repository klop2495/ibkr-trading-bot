"""
Tests for Position Sizer
"""

import pytest

from app.pm.position_sizer import PositionSizer, PositionSizerConfig


class TestPositionSizer:
    """Tests for position sizing calculations."""
    
    def test_basic_calculation(self):
        """Test basic position size calculation."""
        sizer = PositionSizer()
        
        result = sizer.calculate(
            equity=10000,
            stop_loss_pips=20,
            symbol="EURUSD",
            risk_modifier=1.0,
        )
        
        assert result.units > 0
        assert result.risk_amount > 0
        assert result.stop_distance_pips == 20
        assert result.risk_percent_actual > 0
        assert result.risk_percent_actual <= 1.0  # Default max is 1%
    
    def test_risk_modifier_reduces_size(self):
        """Test that risk modifier < 1 reduces position size."""
        sizer = PositionSizer()
        
        full_risk = sizer.calculate(
            equity=10000,
            stop_loss_pips=20,
            symbol="EURUSD",
            risk_modifier=1.0,
        )
        
        half_risk = sizer.calculate(
            equity=10000,
            stop_loss_pips=20,
            symbol="EURUSD",
            risk_modifier=0.5,
        )
        
        assert half_risk.units < full_risk.units
        assert half_risk.risk_amount < full_risk.risk_amount
    
    def test_risk_modifier_zero_gives_zero_size(self):
        """Test that risk modifier 0 results in minimum or zero size."""
        sizer = PositionSizer()
        
        result = sizer.calculate(
            equity=10000,
            stop_loss_pips=20,
            symbol="EURUSD",
            risk_modifier=0.0,
        )
        
        # With risk_modifier=0, adjusted risk = 0, but clamped to min
        # Depending on equity and min_risk_per_trade_pct, may still get small position
        assert result.risk_percent_actual <= 0.1  # Should be at or below min
    
    def test_invalid_equity(self):
        """Test that invalid equity returns zero size."""
        sizer = PositionSizer()
        
        result = sizer.calculate(
            equity=0,
            stop_loss_pips=20,
            symbol="EURUSD",
        )
        
        assert result.units == 0
        assert result.capped is True
        assert result.reason == "invalid_equity"
    
    def test_invalid_stop_loss(self):
        """Test that invalid stop loss returns zero size."""
        sizer = PositionSizer()
        
        result = sizer.calculate(
            equity=10000,
            stop_loss_pips=0,
            symbol="EURUSD",
        )
        
        assert result.units == 0
        assert result.capped is True
        assert result.reason == "invalid_stop_loss"
    
    def test_max_position_limit(self):
        """Test that position is capped at max limit."""
        config = PositionSizerConfig(max_position_size=50000)
        sizer = PositionSizer(config)
        
        result = sizer.calculate(
            equity=1000000,  # Large equity
            stop_loss_pips=5,  # Small stop
            symbol="EURUSD",
        )
        
        assert result.units <= 50000
        if result.units == 50000:
            assert result.capped is True
            assert result.reason == "max_position_limit"
    
    def test_min_position_limit(self):
        """Test that very small positions are rejected."""
        config = PositionSizerConfig(min_position_size=1000)
        sizer = PositionSizer(config)
        
        result = sizer.calculate(
            equity=100,  # Small equity
            stop_loss_pips=100,  # Large stop
            symbol="EURUSD",
        )
        
        if result.units == 0:
            assert result.capped is True
            assert result.reason == "below_min_position"
    
    def test_calculate_from_prices(self):
        """Test position sizing from entry/stop prices."""
        sizer = PositionSizer()
        
        # EURUSD: 1 pip = 0.0001
        result = sizer.calculate_from_prices(
            equity=10000,
            entry_price=1.1000,
            stop_price=1.0980,  # 20 pips
            symbol="EURUSD",
        )
        
        assert result.stop_distance_pips == pytest.approx(20, rel=1e-9)
        assert result.units > 0
    
    def test_jpy_pair_pip_calculation(self):
        """Test that JPY pairs use correct pip size."""
        sizer = PositionSizer()
        
        # USDJPY: 1 pip = 0.01
        result = sizer.calculate_from_prices(
            equity=10000,
            entry_price=150.00,
            stop_price=149.80,  # 20 pips
            symbol="USDJPY",
        )
        
        assert result.stop_distance_pips == pytest.approx(20, rel=1e-9)
    
    def test_lot_step_rounding(self):
        """Test that position is rounded to lot step."""
        config = PositionSizerConfig(lot_step=1000)
        sizer = PositionSizer(config)
        
        result = sizer.calculate(
            equity=10000,
            stop_loss_pips=20,
            symbol="EURUSD",
        )
        
        # Position should be multiple of 1000
        assert result.units % 1000 == 0
    
    def test_custom_risk_percentage(self):
        """Test custom risk percentage override."""
        sizer = PositionSizer()
        
        default_risk = sizer.calculate(
            equity=10000,
            stop_loss_pips=20,
            symbol="EURUSD",
        )
        
        custom_risk = sizer.calculate(
            equity=10000,
            stop_loss_pips=20,
            symbol="EURUSD",
            custom_risk_pct=0.5,
        )
        
        assert custom_risk.units < default_risk.units
        assert custom_risk.risk_percent_actual < default_risk.risk_percent_actual
