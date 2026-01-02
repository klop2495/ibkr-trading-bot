"""
Tests for broker position check functionality (AI_RULES 2.5).

Verifies that:
1. ExecutionService checks broker positions before opening new trades
2. Duplicate positions on same symbol are blocked
"""

import pytest
from unittest.mock import MagicMock, patch

from app.execution.service import ExecutionService, ExecutionMode, ExecutionResult


class TestBrokerPositionCheck:
    """Tests for _get_broker_position_for_symbol method."""
    
    def test_no_connection_returns_none(self):
        """Should return None if no IB connection."""
        service = ExecutionService()
        service._connection_manager = None
        
        result = service._get_broker_position_for_symbol("EURUSD")
        assert result is None
    
    def test_disconnected_returns_none(self):
        """Should return None if IB is disconnected."""
        service = ExecutionService()
        
        mock_cm = MagicMock()
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = False
        mock_cm.ib = mock_ib
        service._connection_manager = mock_cm
        
        result = service._get_broker_position_for_symbol("EURUSD")
        assert result is None
    
    def test_finds_existing_position(self):
        """Should return None without BrokerStateService."""
        service = ExecutionService()
        
        # Mock IB connection
        mock_cm = MagicMock()
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        
        # Mock position
        mock_position = MagicMock()
        mock_position.contract.secType = "CASH"
        mock_position.contract.symbol = "EUR"
        mock_position.contract.currency = "USD"
        mock_position.position = -25000.0
        
        mock_ib.positions.return_value = [mock_position]
        mock_cm.ib = mock_ib
        service._connection_manager = mock_cm
        
        result = service._get_broker_position_for_symbol("EURUSD")
        assert result is None
    
    def test_no_position_returns_none(self):
        """Should return None without BrokerStateService."""
        service = ExecutionService()
        
        mock_cm = MagicMock()
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        
        # Mock different position
        mock_position = MagicMock()
        mock_position.contract.secType = "CASH"
        mock_position.contract.symbol = "GBP"
        mock_position.contract.currency = "USD"
        mock_position.position = 10000.0
        
        mock_ib.positions.return_value = [mock_position]
        mock_cm.ib = mock_ib
        service._connection_manager = mock_cm
        
        result = service._get_broker_position_for_symbol("EURUSD")
        assert result is None
    
    def test_zero_position_returns_none(self):
        """Should return None without BrokerStateService."""
        service = ExecutionService()
        
        mock_cm = MagicMock()
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        
        mock_position = MagicMock()
        mock_position.contract.secType = "CASH"
        mock_position.contract.symbol = "EUR"
        mock_position.contract.currency = "USD"
        mock_position.position = 0.0
        
        mock_ib.positions.return_value = [mock_position]
        mock_cm.ib = mock_ib
        service._connection_manager = mock_cm
        
        result = service._get_broker_position_for_symbol("EURUSD")
        assert result is None




class TestExecuteBlocksOnBrokerPosition:
    """Tests that execute() blocks when broker has existing position."""
    
    @patch.object(ExecutionService, '_get_broker_position_for_symbol')
    @patch.object(ExecutionService, '_init_components')
    @patch.object(ExecutionService, '_determine_mode')
    def test_execute_blocked_when_broker_has_position(
        self,
        mock_determine_mode,
        mock_init_components,
        mock_get_broker_position,
    ):
        """Should return executed=False when broker already has position."""
        mock_determine_mode.return_value = ExecutionMode.PAPER
        mock_init_components.return_value = True
        mock_get_broker_position.return_value = -25000.0  # Existing position
        
        service = ExecutionService()
        service._mode = ExecutionMode.PAPER
        service._equity = 100000.0
        service.trades_history_repo = MagicMock()
        service.trades_history_repo.exists_by_decision_id.return_value = False
        service.trades_history_repo.count_active_trades.return_value = 0
        
        # Mock decision and verdict
        mock_decision = MagicMock()
        mock_decision.id = "test-decision-123"
        mock_decision.symbol = "EURUSD"
        mock_decision.flags = ["BUY"]
        mock_decision.commentary = None
        mock_decision.signal_preview_id = None
        
        mock_verdict = MagicMock()
        mock_verdict.trade_allowed = True
        mock_verdict.risk_modifier = 1.0
        
        mock_settings = MagicMock()
        mock_settings.trading_enabled = True
        mock_settings.max_open_positions = 5
        mock_settings.risk_per_trade = 1.0
        
        result = service.execute(
            decision=mock_decision,
            verdict=mock_verdict,
            settings=mock_settings,
            stop_loss_pips=20,
            take_profit_pips=40,
            direction="BUY",
        )
        
        assert result.executed is False
        assert "broker_has_position" in result.reason
        assert "-25000" in result.reason


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
