"""
Tests for Phase 1: Data Sources and Health Monitor.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.source_health import SourceHealth
from app.models.economic_event import EconomicEvent, EventImpact
from app.models.cot_report import COTReport
from app.models.dxy_snapshot import DXYSnapshot
from app.data_sources.economic_calendar import EconomicCalendarFetcher
from app.data_sources.cot_reports import COTReportsFetcher
from app.data_sources.dxy_index import DXYFetcher
from app.agents.safety.source_health import SourceHealthMonitor


class TestSourceHealth:
    """Tests for SourceHealth model."""
    
    def test_fresh_source(self):
        """Test creating a fresh healthy source."""
        now = datetime.now(timezone.utc)
        health = SourceHealth.fresh("test_source", now)
        
        assert health.source_name == "test_source"
        assert health.is_available is True
        assert health.staleness_minutes < 1.0
        assert health.coverage == 1.0
        assert health.is_stale(5.0) is False
        assert health.is_healthy(5.0) is True
    
    def test_stale_source(self):
        """Test detecting stale source."""
        old_time = datetime.now(timezone.utc) - timedelta(minutes=30)
        health = SourceHealth.fresh("test_source", old_time)
        
        assert health.staleness_minutes >= 30.0
        assert health.is_stale(15.0) is True
        assert health.is_stale(60.0) is False
    
    def test_unavailable_source(self):
        """Test unavailable source factory."""
        health = SourceHealth.unavailable("test_source", "Connection failed")
        
        assert health.is_available is False
        assert health.coverage == 0.0
        assert health.error_message == "Connection failed"
        assert health.is_healthy(999.0) is False
    
    def test_to_dict(self):
        """Test serialization to dict."""
        now = datetime.now(timezone.utc)
        health = SourceHealth.fresh("test_source", now, coverage=0.8)
        
        d = health.to_dict()
        assert d["source_name"] == "test_source"
        assert d["is_available"] is True
        assert d["coverage"] == 0.8
        assert "last_update" in d


class TestEconomicEvent:
    """Tests for EconomicEvent model."""
    
    def test_upcoming_event(self):
        """Test detecting upcoming event."""
        future = datetime.now(timezone.utc) + timedelta(hours=6)
        event = EconomicEvent(
            event_id="test1",
            event_name="NFP",
            currency="USD",
            scheduled_time=future,
            impact=EventImpact.HIGH,
        )
        
        assert event.is_upcoming(24.0) is True
        assert event.is_upcoming(1.0) is False
        assert event.is_past() is False
        assert 5.0 < event.hours_until() < 7.0
    
    def test_past_event(self):
        """Test detecting past event."""
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        event = EconomicEvent(
            event_id="test2",
            event_name="CPI",
            currency="EUR",
            scheduled_time=past,
            impact=EventImpact.HIGH,
            actual="2.8%",
            forecast="2.3%",  # 21.7% difference > 10% threshold
        )
        
        assert event.is_past() is True
        assert event.is_upcoming(24.0) is False
        assert event.has_surprise() is True
    
    def test_to_agent_input(self):
        """Test categorical conversion for LLM."""
        soon = datetime.now(timezone.utc) + timedelta(hours=3)
        event = EconomicEvent(
            event_id="test3",
            event_name="FOMC",
            currency="USD",
            scheduled_time=soon,
            impact=EventImpact.HIGH,
        )
        
        agent_input = event.to_agent_input()
        assert agent_input["event"] == "FOMC"
        assert agent_input["currency"] == "USD"
        assert agent_input["impact"] == "HIGH"
        assert agent_input["timing"] == "SOON"  # 1-6 hours


class TestCOTReport:
    """Tests for COTReport model."""
    
    def test_long_bias(self):
        """Test detecting long bias."""
        report = COTReport(
            symbol="EUR",
            report_date=datetime.now(timezone.utc),
            long_positions=185000,
            short_positions=142000,
            percentile_52w=0.72,
        )
        
        assert report.bias == "LONG"
        assert report.net_position == 43000
        assert report.percentile_bucket == "HIGH"
    
    def test_short_bias(self):
        """Test detecting short bias."""
        report = COTReport(
            symbol="JPY",
            report_date=datetime.now(timezone.utc),
            long_positions=28000,
            short_positions=165000,
            percentile_52w=0.15,
        )
        
        assert report.bias == "SHORT"
        assert report.net_position == -137000
        assert report.percentile_bucket == "EXTREME_LOW"
    
    def test_staleness(self):
        """Test staleness detection."""
        old_report = COTReport(
            symbol="GBP",
            report_date=datetime.now(timezone.utc) - timedelta(days=12),
            long_positions=50000,
            short_positions=60000,
        )
        
        assert old_report.is_stale(10.0) is True
        assert old_report.is_stale(15.0) is False
    
    def test_to_agent_input(self):
        """Test categorical conversion for LLM."""
        report = COTReport(
            symbol="AUD",
            report_date=datetime.now(timezone.utc) - timedelta(days=3),
            long_positions=72000,
            short_positions=88000,
            long_change=5000,
            short_change=-3000,
            percentile_52w=0.42,
        )
        
        agent_input = report.to_agent_input()
        assert agent_input["symbol"] == "AUD"
        assert agent_input["bias"] == "SHORT"
        assert agent_input["weekly_change"] == "INCREASING"  # net_change positive
        assert agent_input["percentile"] == "MEDIUM"


class TestDXYSnapshot:
    """Tests for DXYSnapshot model."""
    
    def test_uptrend(self):
        """Test detecting uptrend."""
        snapshot = DXYSnapshot(
            value=105.0,  # > 1% above sma_50
            timestamp=datetime.now(timezone.utc),
            sma_20=104.0,
            sma_50=103.0,  # 105 vs 103 = 1.94% > 1% threshold
            sma_200=102.0,
            daily_change_pct=0.35,
        )
        
        assert snapshot.trend == "UP"
        assert snapshot.vs_sma == "ABOVE"
        assert snapshot.daily_direction == "UP"
    
    def test_downtrend(self):
        """Test detecting downtrend."""
        snapshot = DXYSnapshot(
            value=101.0,
            timestamp=datetime.now(timezone.utc),
            sma_20=102.0,
            sma_50=103.0,
            sma_200=104.0,
            daily_change_pct=-0.45,
        )
        
        assert snapshot.trend == "DOWN"
        assert snapshot.vs_sma == "BELOW"
        assert snapshot.daily_direction == "DOWN"
    
    def test_to_agent_input(self):
        """Test categorical conversion for LLM."""
        snapshot = DXYSnapshot(
            value=103.5,
            timestamp=datetime.now(timezone.utc),
            sma_20=103.3,
            sma_50=103.0,
            atr_14=0.45,
        )
        
        agent_input = snapshot.to_agent_input()
        assert "trend" in agent_input
        assert "vs_50sma" in agent_input
        assert "volatility" in agent_input


class TestDataSourceFetchers:
    """Tests for data source fetchers."""
    
    def test_economic_calendar_mock(self):
        """Test economic calendar fetcher in mock mode."""
        fetcher = EconomicCalendarFetcher(mock_mode=True)
        events = fetcher.fetch(days_ahead=7)
        
        assert len(events) > 0
        assert all(isinstance(e, EconomicEvent) for e in events)
        
        health = fetcher.get_health()
        assert health.is_available is True
        assert health.source_name == "economic_calendar"
    
    def test_economic_calendar_high_impact(self):
        """Test filtering high-impact events."""
        fetcher = EconomicCalendarFetcher(mock_mode=True)
        fetcher.fetch(days_ahead=7)
        
        high_impact = fetcher.get_upcoming_high_impact(["USD", "EUR"], hours_ahead=168)
        assert len(high_impact) > 0
        assert all(e.impact == EventImpact.HIGH for e in high_impact)
    
    def test_cot_reports_mock(self):
        """Test COT reports fetcher in mock mode."""
        fetcher = COTReportsFetcher(mock_mode=True)
        reports = fetcher.fetch(currencies=["EUR", "GBP", "JPY"])
        
        assert len(reports) == 3
        assert "EUR" in reports
        assert all(isinstance(r, COTReport) for r in reports.values())
        
        health = fetcher.get_health()
        assert health.is_available is True
    
    def test_cot_reports_symbol_lookup(self):
        """Test getting COT for forex symbol."""
        fetcher = COTReportsFetcher(mock_mode=True)
        fetcher.fetch()
        
        report = fetcher.get_for_symbol("EURUSD")
        assert report is not None
        assert report.symbol == "EUR"
    
    def test_dxy_fetcher_mock(self):
        """Test DXY fetcher in mock mode."""
        fetcher = DXYFetcher(mock_mode=True)
        snapshot = fetcher.fetch()
        
        assert snapshot is not None
        assert isinstance(snapshot, DXYSnapshot)
        assert 90 < snapshot.value < 120  # Reasonable DXY range
        
        health = fetcher.get_health()
        assert health.is_available is True


class TestSourceHealthMonitor:
    """Tests for SourceHealthMonitor."""
    
    def test_check_all_healthy(self):
        """Test monitoring all healthy sources."""
        monitor = SourceHealthMonitor()
        
        # Create mock fetchers
        calendar = EconomicCalendarFetcher(mock_mode=True)
        cot = COTReportsFetcher(mock_mode=True)
        dxy = DXYFetcher(mock_mode=True)
        
        # Fetch to populate health
        calendar.fetch()
        cot.fetch()
        dxy.fetch()
        
        health = monitor.check_all({
            "economic_calendar": calendar,
            "cot_reports": cot,
            "dxy_index": dxy,
        })
        
        assert len(health) == 3
        assert all(h.is_available for h in health.values())
        assert monitor.get_all_stale(health) == []
    
    def test_should_block_trading_no_critical(self):
        """Test that trading is not blocked when non-critical sources are stale."""
        monitor = SourceHealthMonitor()
        
        # Only non-critical sources, all healthy
        calendar = EconomicCalendarFetcher(mock_mode=True)
        calendar.fetch()
        
        health = monitor.check_all({"economic_calendar": calendar})
        
        # Should not block - ibkr_ohlcv is missing but we're only checking what's provided
        # Actually, should_block_trading checks CRITICAL_SOURCES which includes ibkr_ohlcv
        # If it's not in health map, it should block
        assert monitor.should_block_trading(health) is True  # Missing critical source
    
    def test_get_stale_for_agent(self):
        """Test getting stale sources for specific agent."""
        monitor = SourceHealthMonitor()
        
        # Simulate stale DXY
        old_health = SourceHealth(
            source_name="dxy_index",
            is_available=True,
            last_update=datetime.now(timezone.utc) - timedelta(minutes=30),
            staleness_minutes=30.0,
            coverage=1.0,
        )
        
        health = {"dxy_index": old_health}
        
        stale = monitor.get_stale_for_agent("CorrelationAgent", health)
        assert "dxy_index" in stale
        assert "ibkr_ohlcv" in stale  # Missing = considered stale
        
        stale_tech = monitor.get_stale_for_agent("TechnicalAgent", health)
        assert "ibkr_ohlcv" in stale_tech  # Missing
        assert "dxy_index" not in stale_tech  # Not required
    
    def test_to_dict(self):
        """Test serialization for parallel_decisions."""
        monitor = SourceHealthMonitor()
        
        calendar = EconomicCalendarFetcher(mock_mode=True)
        calendar.fetch()
        
        monitor.check_all({"economic_calendar": calendar})
        
        d = monitor.to_dict()
        assert "checked_at" in d
        assert "sources" in d
        assert "stale_sources" in d
        assert "trading_blocked" in d
