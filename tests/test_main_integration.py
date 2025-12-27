"""
Tests for Phase 5: main.py integration.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import MagicMock, patch

# Skip module if Supabase SDK not available in local environment
pytest.importorskip("supabase")

from app.models.signal_preview import (
    SignalPreviewV1, Direction, Confidence, SetupType,
    DataQuality, SpreadQuality, TimeframeTrigger
)
from app.models.decision import DecisionV1
from app.models.parallel_decision import ParallelDecisionV1


class TestMainIntegration:
    """Tests for main.py Phase 5 integration."""
    
    def test_create_openai_client_no_key(self):
        """Test OpenAI client creation without API key."""
        from app.main import _create_openai_client
        
        with patch.dict('os.environ', {'OPENAI_API_KEY': ''}, clear=False):
            # Need to reload to pick up env change
            import importlib
            import app.main
            importlib.reload(app.main)
            
            client = app.main._create_openai_client()
            assert client is None
    
    def test_preview_from_row(self):
        """Test _preview_from_row conversion."""
        from app.main import _preview_from_row
        
        row = {
            "ts_utc": "2024-01-15T10:30:00+00:00",
            "symbol": "EURUSD",
            "timeframe_trigger": "M15",
            "setup_type": "SWING_CONTINUATION",
            "direction": "long",
            "setup_present": True,
            "entry_triggered": True,
            "confidence": "high",
            "rr": 2.5,
            "data_quality": "ok",
            "spread_quality": "ok",
            "flags": ["test_flag"],
            "sl_distance_pips": 25.0,
            "tp_distance_pips": 50.0,
        }
        
        preview = _preview_from_row(row)
        
        assert preview.symbol == "EURUSD"
        assert preview.direction == Direction.LONG
        assert preview.confidence == Confidence.HIGH
        assert preview.rr == 2.5
    
    def test_safe_uuid_valid(self):
        """Test _safe_uuid with valid UUID."""
        from app.main import _safe_uuid
        
        test_uuid = uuid4()
        result = _safe_uuid(str(test_uuid))
        
        assert result == test_uuid
    
    def test_safe_uuid_invalid(self):
        """Test _safe_uuid with invalid UUID."""
        from app.main import _safe_uuid
        
        result = _safe_uuid("not-a-uuid")
        
        assert result is None
    
    def test_parse_direction_long(self):
        """Test _parse_direction with long."""
        from app.main import _parse_direction
        
        result = _parse_direction("long")
        
        assert result == Direction.LONG
    
    def test_parse_direction_flat(self):
        """Test _parse_direction with FLAT alias."""
        from app.main import _parse_direction
        
        result = _parse_direction("FLAT")
        
        assert result == Direction.FLAT
    
    def test_parse_direction_unknown(self):
        """Test _parse_direction with unknown value."""
        from app.main import _parse_direction
        
        result = _parse_direction("unknown")
        
        assert result == Direction.FLAT


class TestRunParallelShadowTick:
    """Tests for run_parallel_shadow_tick function."""
    
    def test_returns_zero_when_no_client(self):
        """Test returns zeros when client is None."""
        from app.main import run_parallel_shadow_tick
        from app.agents.parallel_runner import create_parallel_runner
        
        runner = create_parallel_runner(llm_enabled=False)
        
        result = run_parallel_shadow_tick(
            client=None,
            parallel_repo=MagicMock(),
            parallel_runner=runner,
            risk_events_repo=None,
            limit=20,
        )
        
        assert result["logged"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["llm_calls"] == 0
        assert result["cache_hits"] == 0


class TestParallelRunnerIntegration:
    """Tests for ParallelDecisionRunner in main.py context."""
    
    def test_runner_creation_shadow_mode(self):
        """Test runner creation in shadow mode."""
        from app.agents.parallel_runner import create_parallel_runner
        
        runner = create_parallel_runner(
            llm_enabled=False,
            active_strategy="rules",
        )
        
        assert runner.llm_enabled is False
        assert runner.active_strategy == "rules"
        assert len(runner.agents) == 5
    
    def test_runner_creation_llm_mode(self):
        """Test runner creation with LLM enabled."""
        from app.agents.parallel_runner import create_parallel_runner
        
        runner = create_parallel_runner(
            llm_enabled=True,
            active_strategy="hybrid",
        )
        
        assert runner.llm_enabled is True
        assert runner.active_strategy == "hybrid"
        assert runner.aggregator is not None
    
    def test_runner_with_data_sources(self):
        """Test runner with data sources."""
        from app.agents.parallel_runner import create_parallel_runner
        from app.data_sources import EconomicCalendarFetcher, COTReportsFetcher, DXYFetcher
        
        calendar = EconomicCalendarFetcher(mock_mode=True)
        cot = COTReportsFetcher(mock_mode=True)
        dxy = DXYFetcher(mock_mode=True)
        
        runner = create_parallel_runner(
            llm_enabled=True,
            active_strategy="hybrid",
            economic_calendar=calendar,
            cot_reports=cot,
            dxy_fetcher=dxy,
        )
        
        assert runner.context_builder.economic_calendar == calendar
        assert runner.context_builder.cot_reports == cot
        assert runner.context_builder.dxy_fetcher == dxy


class TestEndToEndPipeline:
    """End-to-end tests for Phase 5 pipeline."""
    
    def test_full_pipeline_mock_mode(self):
        """Test full pipeline in mock mode."""
        from app.agents.parallel_runner import create_parallel_runner
        from app.data_sources import EconomicCalendarFetcher, COTReportsFetcher, DXYFetcher
        
        # Setup
        calendar = EconomicCalendarFetcher(mock_mode=True)
        cot = COTReportsFetcher(mock_mode=True)
        dxy = DXYFetcher(mock_mode=True)
        
        runner = create_parallel_runner(
            llm_enabled=True,
            active_strategy="hybrid",
            economic_calendar=calendar,
            cot_reports=cot,
            dxy_fetcher=dxy,
        )
        
        # Create test data
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
        
        decision = DecisionV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="EURUSD",
            signal_preview_id=uuid4(),
            trade_allowed=True,
            risk_modifier=1.0,
            flags=["TREND_STRONG"],
        )
        
        # Run pipeline
        result = runner.run(preview, uuid4(), decision)
        
        # Verify result
        assert isinstance(result, ParallelDecisionV1)
        assert result.symbol == "EURUSD"
        assert result.rules_signal == "LONG"
        assert result.executed_strategy == "hybrid"
        assert result.gpt_signal in ("LONG", "SHORT", "HOLD")
        
        # Check stats
        stats = runner.get_stats()
        assert stats["calls_total"] >= 1
    
    def test_full_pipeline_with_account_state(self):
        """Test full pipeline with account state."""
        from app.agents.parallel_runner import create_parallel_runner
        
        runner = create_parallel_runner(
            llm_enabled=True,
            active_strategy="hybrid",
        )
        
        # Create test data
        preview = SignalPreviewV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="GBPUSD",
            timeframe_trigger=TimeframeTrigger.M15,
            setup_type=SetupType.SWING_REVERSAL,
            direction=Direction.SHORT,
            setup_present=True,
            entry_triggered=True,
            confidence=Confidence.NORMAL,
            rr=1.8,
            data_quality=DataQuality.OK,
            spread_quality=SpreadQuality.OK,
            flags=[],
        )
        
        decision = DecisionV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="GBPUSD",
            signal_preview_id=uuid4(),
            trade_allowed=True,
            risk_modifier=0.8,
            flags=["RISK_REDUCED"],
        )
        
        account_state = {
            "drawdown_pct": 2.5,
            "daily_loss_limit_near": False,
            "exposure_level": "LOW",
        }
        
        # Run pipeline with account state
        result = runner.run(
            preview,
            uuid4(),
            decision,
            account_state=account_state,
        )
        
        # Verify result
        assert result.symbol == "GBPUSD"
        assert result.rules_signal == "SHORT"
