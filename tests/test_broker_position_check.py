"""
Tests for broker position check functionality (AI_RULES 2.5).

Verifies that:
1. ExecutionService checks broker positions before opening new trades
2. Duplicate positions on same symbol are blocked
3. PositionReconciler has safety checks for phantom cleanup
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from app.execution.service import ExecutionService, ExecutionMode, ExecutionResult
from app.reconciliation.position_reconciler import PositionReconciler, ReconciliationReport


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
        """Should return position quantity if symbol has open position."""
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
        assert result == -25000.0
    
    def test_no_position_returns_none(self):
        """Should return None if no position for symbol."""
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
        """Should return None if position is 0."""
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


class TestPositionReconcilerSafety:
    """Tests for PositionReconciler safety checks."""
    
    def test_skips_phantom_cleanup_when_broker_has_zero_positions(self):
        """Should skip phantom cleanup if broker shows 0 positions but DB has trades."""
        mock_db = MagicMock()
        reconciler = PositionReconciler(db=mock_db, auto_close_phantoms=True)
        
        report = ReconciliationReport(
            timestamp=datetime.now(timezone.utc),
            broker_connected=True,
            db_open_trades=2,
            broker_positions=0,
        )
        
        db_trades = [
            {"id": "trade1", "symbol": "EURUSD", "status": "OPEN", "quantity": 25000},
            {"id": "trade2", "symbol": "GBPUSD", "status": "OPEN", "quantity": 20000},
        ]
        broker_positions = {}  # Empty - 0 positions
        
        reconciler._compare_positions(db_trades, broker_positions, report)
        
        # Should NOT have phantom trades because of safety check
        assert len(report.phantom_trades) == 0
        # Should have error message about safety skip
        assert any("Safety skip" in err for err in report.errors)
    
    def test_grace_period_for_new_trades(self):
        """Should not mark fresh trades as phantom (< 5 min old)."""
        mock_db = MagicMock()
        reconciler = PositionReconciler(db=mock_db, auto_close_phantoms=True)
        
        report = ReconciliationReport(
            timestamp=datetime.now(timezone.utc),
            broker_connected=True,
            db_open_trades=1,
            broker_positions=1,
        )
        
        # Trade opened 2 minutes ago
        fresh_time = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        
        db_trades = [
            {"id": "trade1", "symbol": "EURUSD", "status": "OPEN", "quantity": 25000, "opened_at": fresh_time},
        ]
        
        # Broker has different position (GBPUSD) - EURUSD would be phantom
        broker_positions = {
            "GBPUSD": {"quantity": 20000, "side": "BUY"},
        }
        
        reconciler._compare_positions(db_trades, broker_positions, report)
        
        # Should NOT be marked as phantom due to grace period
        assert len(report.phantom_trades) == 0
    
    def test_old_trades_can_be_phantom(self):
        """Should mark old trades as phantom if not at broker."""
        mock_db = MagicMock()
        
        # Mock the close_phantom_trade method
        mock_db.client.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        
        reconciler = PositionReconciler(db=mock_db, auto_close_phantoms=True)
        
        report = ReconciliationReport(
            timestamp=datetime.now(timezone.utc),
            broker_connected=True,
            db_open_trades=1,
            broker_positions=1,
        )
        
        # Trade opened 10 minutes ago (past grace period)
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        
        db_trades = [
            {"id": "trade1", "symbol": "EURUSD", "status": "OPEN", "quantity": 25000, "opened_at": old_time},
        ]
        
        # Broker has different position
        broker_positions = {
            "GBPUSD": {"quantity": 20000, "side": "BUY"},
        }
        
        reconciler._compare_positions(db_trades, broker_positions, report)
        
        # Should be marked as phantom (trade is old enough)
        assert len(report.phantom_trades) == 1
        assert report.phantom_trades[0].symbol == "EURUSD"


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
