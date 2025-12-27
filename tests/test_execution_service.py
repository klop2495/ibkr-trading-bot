"""
Tests for ExecutionService
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

from app.execution.service import (
    ExecutionService,
    ExecutionMode,
    ExecutionResult,
    ExecutionServiceCallback,
)
from app.broker.oms import OrderSide, OrderStatus, OrderType
from app.models.decision import DecisionV1
from app.models.risk_verdict import RiskVerdictV1
from app.models.bot_settings import BotSettings


@pytest.fixture
def mock_settings():
    """Create mock BotSettings."""
    return BotSettings(
        owner_user_id=uuid4(),
        trading_enabled=True,
        mode="paper",
        risk_per_trade=0.5,
    )


@pytest.fixture
def mock_decision():
    """Create mock DecisionV1."""
    decision = DecisionV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        signal_preview_id=uuid4(),
        trade_allowed=True,
        risk_modifier=1.0,
        flags=["LONG", "HIGH_CONFIDENCE"],
        commentary="Strong bullish setup",
    )
    decision.id = str(uuid4())
    return decision


@pytest.fixture
def mock_verdict():
    """Create mock RiskVerdictV1."""
    return RiskVerdictV1(
        ts_utc=datetime.now(timezone.utc),
        symbol="EURUSD",
        decision_id=uuid4(),
        signal_preview_id=uuid4(),
        trade_allowed=True,
        risk_modifier=1.0,
        flags=[],
    )


@pytest.fixture
def execution_service():
    """Create ExecutionService instance."""
    return ExecutionService(risk_events_repo=None)


class TestExecutionMode:
    """Tests for ExecutionMode enum."""
    
    def test_execution_mode_values(self):
        assert ExecutionMode.DISABLED.value == "disabled"
        assert ExecutionMode.DRY_RUN.value == "dry_run"
        assert ExecutionMode.PAPER.value == "paper"
        assert ExecutionMode.LIVE.value == "live"


class TestExecutionService:
    """Tests for ExecutionService."""
    
    def test_init(self, execution_service):
        assert execution_service._equity == ExecutionService.DEFAULT_EQUITY
        assert execution_service._mode == ExecutionMode.DISABLED
        assert execution_service._initialized is False
    
    def test_update_equity(self, execution_service):
        execution_service.update_equity(50000.0)
        assert execution_service.get_equity() == 50000.0
    
    def test_get_equity(self, execution_service):
        assert execution_service.get_equity() == ExecutionService.DEFAULT_EQUITY
    
    @patch.dict("os.environ", {}, clear=True)
    def test_determine_mode_disabled_no_env(self, execution_service, mock_settings):
        mode = execution_service._determine_mode(mock_settings)
        assert mode == ExecutionMode.DISABLED
    
    @patch.dict("os.environ", {"EXECUTION_ENABLED": "1", "EXECUTION_DRY_RUN": "1"})
    def test_determine_mode_dry_run(self, execution_service, mock_settings):
        mode = execution_service._determine_mode(mock_settings)
        assert mode == ExecutionMode.DRY_RUN
    
    @patch.dict("os.environ", {"EXECUTION_ENABLED": "1", "IBKR_ENABLED": "1"})
    def test_determine_mode_paper(self, execution_service, mock_settings):
        mode = execution_service._determine_mode(mock_settings)
        assert mode == ExecutionMode.PAPER
    
    @patch.dict("os.environ", {"EXECUTION_ENABLED": "1", "IBKR_ENABLED": "1", "ALLOW_LIVE_EXECUTION": "1"})
    def test_determine_mode_live(self, execution_service):
        settings = BotSettings(
            owner_user_id=uuid4(),
            trading_enabled=True,
            mode="live",
        )
        mode = execution_service._determine_mode(settings)
        assert mode == ExecutionMode.LIVE
    
    def test_determine_side_from_flags_long(self, execution_service, mock_decision, mock_verdict):
        mock_decision.flags = ["LONG"]
        side = execution_service._determine_side(mock_decision, mock_verdict)
        assert side == OrderSide.BUY
    
    def test_determine_side_from_flags_short(self, execution_service, mock_decision, mock_verdict):
        mock_decision.flags = ["SHORT"]
        side = execution_service._determine_side(mock_decision, mock_verdict)
        assert side == OrderSide.SELL
    
    def test_determine_side_from_flags_buy(self, execution_service, mock_decision, mock_verdict):
        mock_decision.flags = ["BUY_SIGNAL"]
        side = execution_service._determine_side(mock_decision, mock_verdict)
        assert side == OrderSide.BUY
    
    def test_determine_side_from_commentary(self, execution_service, mock_decision, mock_verdict):
        mock_decision.flags = []
        mock_decision.commentary = "Strong LONG opportunity"
        side = execution_service._determine_side(mock_decision, mock_verdict)
        assert side == OrderSide.BUY
    
    def test_determine_side_none(self, execution_service, mock_decision, mock_verdict):
        mock_decision.flags = []
        mock_decision.commentary = "Neutral market"
        side = execution_service._determine_side(mock_decision, mock_verdict)
        assert side is None
    
    @patch.dict("os.environ", {}, clear=True)
    def test_execute_disabled(self, execution_service, mock_decision, mock_verdict, mock_settings):
        result = execution_service.execute(mock_decision, mock_verdict, mock_settings)
        assert result.executed is False
        assert result.mode == ExecutionMode.DISABLED
        assert result.reason == "execution_disabled"
    
    @patch.dict("os.environ", {"EXECUTION_ENABLED": "1", "EXECUTION_DRY_RUN": "1"})
    def test_execute_risk_not_allowed(self, execution_service, mock_decision, mock_verdict, mock_settings):
        mock_verdict.trade_allowed = False
        result = execution_service.execute(mock_decision, mock_verdict, mock_settings)
        assert result.executed is False
        assert result.mode == ExecutionMode.DRY_RUN
        assert result.reason == "risk_not_allowed"
    
    @patch.dict("os.environ", {"EXECUTION_ENABLED": "1", "EXECUTION_DRY_RUN": "1"})
    def test_execute_cannot_determine_side(self, execution_service, mock_decision, mock_verdict, mock_settings):
        mock_decision.flags = []
        mock_decision.commentary = "Neutral"
        result = execution_service.execute(mock_decision, mock_verdict, mock_settings)
        assert result.executed is False
        assert result.reason == "cannot_determine_side"
    
    @patch.dict("os.environ", {"EXECUTION_ENABLED": "1", "EXECUTION_DRY_RUN": "1"})
    def test_execute_dry_run_success(self, execution_service, mock_decision, mock_verdict, mock_settings):
        result = execution_service.execute(mock_decision, mock_verdict, mock_settings)
        assert result.executed is True
        assert result.mode == ExecutionMode.DRY_RUN
        assert result.symbol == "EURUSD"
        assert result.side == OrderSide.BUY
        assert result.quantity > 0
        assert result.status == OrderStatus.FILLED
        assert result.dry_run_log is not None
        assert "DRY_RUN" in result.dry_run_log
    
    @patch.dict("os.environ", {"EXECUTION_ENABLED": "1", "EXECUTION_DRY_RUN": "1"})
    def test_execute_dry_run_with_short(self, execution_service, mock_decision, mock_verdict, mock_settings):
        mock_decision.flags = ["SHORT"]
        result = execution_service.execute(mock_decision, mock_verdict, mock_settings)
        assert result.executed is True
        assert result.side == OrderSide.SELL
        assert result.mode == ExecutionMode.DRY_RUN

    @patch.dict("os.environ", {"EXECUTION_ENABLED": "1", "EXECUTION_DRY_RUN": "1"})
    def test_position_sizer_uses_settings_risk_percent(self, execution_service, mock_decision, mock_verdict):
        settings = BotSettings(
            owner_user_id=uuid4(),
            trading_enabled=True,
            mode="paper",
            risk_per_trade=0.5,  # 0.5%
        )
        result = execution_service.execute(mock_decision, mock_verdict, settings)
        assert result.mode == ExecutionMode.DRY_RUN
        assert execution_service._position_sizer is not None
        assert execution_service._position_sizer.config.max_risk_per_trade_pct == 0.5

    def test_calculate_position_size(self, execution_service):
        result = execution_service._calculate_position_size(
            symbol="EURUSD",
            risk_modifier=1.0,
            stop_loss_pips=20.0,
        )
        assert result.units > 0
        assert result.risk_amount > 0
    
    def test_shutdown(self, execution_service):
        execution_service.shutdown()
        assert execution_service._initialized is False


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""
    
    def test_execution_result_creation(self):
        result = ExecutionResult(
            executed=True,
            mode=ExecutionMode.DRY_RUN,
            order_id=uuid4(),
            symbol="EURUSD",
            side=OrderSide.BUY,
            quantity=1000.0,
            status=OrderStatus.FILLED,
        )
        assert result.executed is True
        assert result.mode == ExecutionMode.DRY_RUN
        assert result.symbol == "EURUSD"
        assert result.side == OrderSide.BUY
        assert result.quantity == 1000.0
    
    def test_execution_result_defaults(self):
        result = ExecutionResult(
            executed=False,
            mode=ExecutionMode.DISABLED,
        )
        assert result.order_id is None
        assert result.symbol == ""
        assert result.side is None
        assert result.quantity == 0.0
        assert result.reason is None


class TestExecutionServiceCallback:
    """Tests for ExecutionServiceCallback."""
    
    def test_callback_creation(self):
        callback = ExecutionServiceCallback(risk_events_repo=None)
        assert callback.risk_events_repo is None
    
    def test_callback_with_repo(self):
        mock_repo = Mock()
        callback = ExecutionServiceCallback(risk_events_repo=mock_repo)
        assert callback.risk_events_repo == mock_repo
