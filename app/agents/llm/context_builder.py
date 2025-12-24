"""
Context Builder for LLM Agents.

Phase 6 Update: Added source_health tracking for data_status detection.
Agents use source_health to determine if data is REAL or MISSING.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.signal_preview import SignalPreviewV1, Direction, Confidence, SetupType


logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """
    Context object containing all data for LLM agents.
    
    All data is categorical - no raw prices or numbers.
    
    Phase 6: Added source_health for agents to check data availability.
    """
    symbol: str
    timestamp: datetime
    
    # Technical data (from signal_preview + indicators)
    technical: Dict[str, Any] = field(default_factory=dict)
    
    # Macro/Fundamental data (from economic_calendar + cb_stance)
    macro: Dict[str, Any] = field(default_factory=dict)
    
    # Economic calendar events
    economic_calendar: List[Dict[str, Any]] = field(default_factory=list)
    
    # COT positioning data
    cot_reports: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # DXY snapshot
    dxy_snapshot: Dict[str, Any] = field(default_factory=dict)
    
    # Correlation data
    correlations: Dict[str, Any] = field(default_factory=dict)
    
    # Risk/Session data
    risk: Dict[str, Any] = field(default_factory=dict)
    session: Dict[str, Any] = field(default_factory=dict)
    
    # Account state
    account: Dict[str, Any] = field(default_factory=dict)
    
    # Sentiment data
    sentiment: Dict[str, Any] = field(default_factory=dict)
    
    # Phase 6: Source health for data_status detection
    source_health: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Convert to dict for passing to agents."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "technical": self.technical,
            "macro": self.macro,
            "economic_calendar": self.economic_calendar,
            "cot_reports": self.cot_reports,
            "dxy_snapshot": self.dxy_snapshot,
            "correlations": self.correlations,
            "risk": self.risk,
            "session": self.session,
            "account": self.account,
            "sentiment": self.sentiment,
            "source_health": self.source_health,
        }


class ContextBuilder:
    """
    Builds AgentContext from various data sources.
    
    Transforms raw data into categorical format suitable for LLM agents.
    
    Phase 6: Tracks source health (mock_mode) for each data source.
    """
    
    # Central bank stance mappings (simplified)
    CB_STANCE = {
        "EUR": "NEUTRAL",
        "USD": "HAWKISH",
        "GBP": "NEUTRAL",
        "JPY": "DOVISH",
        "AUD": "NEUTRAL",
        "CAD": "NEUTRAL",
        "CHF": "NEUTRAL",
        "NZD": "NEUTRAL",
    }
    
    def __init__(
        self,
        economic_calendar_fetcher: Optional[Any] = None,
        cot_reports_fetcher: Optional[Any] = None,
        dxy_fetcher: Optional[Any] = None,
    ):
        """
        Initialize context builder with data fetchers.
        
        Args:
            economic_calendar_fetcher: EconomicCalendarFetcher instance
            cot_reports_fetcher: COTReportsFetcher instance
            dxy_fetcher: DXYFetcher instance
        """
        self.economic_calendar = economic_calendar_fetcher
        self.cot_reports = cot_reports_fetcher
        self.dxy_fetcher = dxy_fetcher
    
    def build(
        self,
        symbol: str,
        signal_preview: Optional[SignalPreviewV1] = None,
        account_state: Optional[Dict[str, Any]] = None,
        market_snapshot: Optional[Dict[str, Any]] = None,
    ) -> AgentContext:
        """
        Build complete context for LLM agents.
        
        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            signal_preview: Optional SignalPreviewV1 with technical data
            account_state: Optional account state dict
            market_snapshot: Optional market data with indicators
        
        Returns:
            AgentContext with all categorical data and source_health.
        """
        ctx = AgentContext(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
        )
        
        # Build source_health first (agents use this to detect data_status)
        ctx.source_health = self._build_source_health()
        
        # Build technical context from signal_preview and market_snapshot
        if signal_preview:
            ctx.technical = self._build_technical(signal_preview, market_snapshot)
        
        # Build macro context
        ctx.macro = self._build_macro(symbol)
        
        # Get economic calendar events
        ctx.economic_calendar = self._get_calendar_events(symbol)
        
        # Get COT reports
        ctx.cot_reports = self._get_cot_data(symbol)
        
        # Get DXY snapshot
        ctx.dxy_snapshot = self._get_dxy_data()
        
        # Build session/risk context
        ctx.session = self._build_session()
        ctx.risk = self._build_risk(market_snapshot)
        
        # Build account context
        if account_state:
            ctx.account = self._build_account(account_state)
        
        return ctx
    
    def _build_source_health(self) -> Dict[str, Dict[str, Any]]:
        """
        Build source health status for each data source.
        
        Agents use this to determine if data is REAL or MOCK.
        """
        health = {}
        
        # Economic Calendar
        if self.economic_calendar:
            mock_mode = getattr(self.economic_calendar, 'mock_mode', True)
            health["economic_calendar"] = {
                "mock_mode": mock_mode,
                "available": not mock_mode,
            }
        else:
            health["economic_calendar"] = {"mock_mode": True, "available": False}
        
        # COT Reports
        if self.cot_reports:
            mock_mode = getattr(self.cot_reports, 'mock_mode', True)
            health["cot_reports"] = {
                "mock_mode": mock_mode,
                "available": not mock_mode,
            }
        else:
            health["cot_reports"] = {"mock_mode": True, "available": False}
        
        # DXY Index - uses real Yahoo Finance API
        if self.dxy_fetcher:
            mock_mode = getattr(self.dxy_fetcher, 'mock_mode', False)
            last_fetch = getattr(self.dxy_fetcher, '_last_fetch', None)
            staleness_minutes = 0.0
            if last_fetch:
                staleness_minutes = (datetime.now(timezone.utc) - last_fetch).total_seconds() / 60.0
            health["dxy_index"] = {
                "mock_mode": mock_mode,
                "available": True,  # DXY uses real Yahoo Finance API
                "staleness_minutes": staleness_minutes,
            }
        else:
            health["dxy_index"] = {"mock_mode": True, "available": False}
        
        return health
    
    def _build_technical(
        self, 
        preview: SignalPreviewV1, 
        market_snapshot: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Extract technical data from signal_preview and market_snapshot.
        
        Phase 6: Also includes RSI/ATR if available in market_snapshot.
        """
        # Map direction to trend
        direction = preview.direction
        if isinstance(direction, Direction):
            direction_val = direction.value
        else:
            direction_val = str(direction).lower()
        
        if direction_val == "long":
            trend_short = "UP"
        elif direction_val == "short":
            trend_short = "DOWN"
        else:
            trend_short = "NEUTRAL"
        
        # Map confidence to momentum strength
        confidence = preview.confidence
        if isinstance(confidence, Confidence):
            conf_val = confidence.value
        else:
            conf_val = str(confidence).lower()
        
        # Map setup_type to pattern
        setup = preview.setup_type
        if isinstance(setup, SetupType):
            setup_val = setup.value
        else:
            setup_val = str(setup).lower() if setup else "no_trade"
        
        pattern = "NONE"
        if "continuation" in setup_val.lower():
            pattern = "TREND_CONTINUATION"
        elif "reversal" in setup_val.lower():
            pattern = "REVERSAL"
        elif "breakout" in setup_val.lower():
            pattern = "BREAKOUT"
        
        # RSI zone from flags or market_snapshot
        rsi_zone = "NEUTRAL"
        flags = preview.flags or []
        
        if any("overbought" in f.lower() for f in flags):
            rsi_zone = "OVERBOUGHT"
        elif any("oversold" in f.lower() for f in flags):
            rsi_zone = "OVERSOLD"
        
        # Phase 6: Get RSI from market_snapshot if available
        if market_snapshot:
            rsi_val = market_snapshot.get("rsi")
            if rsi_val is not None:
                if rsi_val > 70:
                    rsi_zone = "OVERBOUGHT"
                elif rsi_val < 30:
                    rsi_zone = "OVERSOLD"
        
        # Volatility level from ATR
        volatility_level = "NORMAL"
        if market_snapshot:
            atr_percentile = market_snapshot.get("atr_percentile")
            if atr_percentile is not None:
                if atr_percentile > 80:
                    volatility_level = "HIGH"
                elif atr_percentile < 20:
                    volatility_level = "LOW"
        
        return {
            "trend_short": trend_short,
            "trend_medium": trend_short,  # Simplified - same as short
            "trend_long": "NEUTRAL",
            "sma_alignment": "BULLISH" if trend_short == "UP" else "BEARISH" if trend_short == "DOWN" else "MIXED",
            "rsi_zone": rsi_zone,
            "macd_signal": trend_short if trend_short != "NEUTRAL" else "NEUTRAL",
            "momentum_divergence": "NONE",
            "volatility_level": volatility_level,
            "atr_relative": "NORMAL",
            "candle_pattern": pattern,
            "candle_direction": "BULLISH" if trend_short == "UP" else "BEARISH" if trend_short == "DOWN" else "NEUTRAL",
            "near_support": any("support" in f.lower() for f in flags),
            "near_resistance": any("resistance" in f.lower() for f in flags),
            "breakout_detected": "BULLISH" if "breakout" in setup_val.lower() and trend_short == "UP" else "BEARISH" if "breakout" in setup_val.lower() and trend_short == "DOWN" else "NONE",
        }
    
    def _build_macro(self, symbol: str) -> Dict[str, Any]:
        """Build macro context for symbol."""
        base = symbol[:3] if len(symbol) >= 3 else "EUR"
        quote = symbol[3:6] if len(symbol) >= 6 else "USD"
        
        base_stance = self.CB_STANCE.get(base, "NEUTRAL")
        quote_stance = self.CB_STANCE.get(quote, "NEUTRAL")
        
        # Rate differential direction
        rate_diff = "NEUTRAL"
        if base_stance == "HAWKISH" and quote_stance != "HAWKISH":
            rate_diff = "WIDENING_FAVOR_BASE"
        elif quote_stance == "HAWKISH" and base_stance != "HAWKISH":
            rate_diff = "WIDENING_FAVOR_QUOTE"
        
        # Phase 6: Mark if calendar is real
        has_real_calendar = False
        if self.economic_calendar:
            has_real_calendar = not getattr(self.economic_calendar, 'mock_mode', True)
        
        return {
            f"{base}_cb_stance": base_stance,
            f"{quote}_cb_stance": quote_stance,
            "rate_diff_direction": rate_diff,
            f"{base}_momentum": "STABLE",
            f"{quote}_momentum": "STABLE",
            "risk_sentiment": "NEUTRAL",
            "has_real_calendar": has_real_calendar,
            "_mock_mode": not has_real_calendar,  # For agent detection
        }
    
    def _get_calendar_events(self, symbol: str) -> List[Dict[str, Any]]:
        """Get economic calendar events for symbol currencies."""
        if not self.economic_calendar:
            return []
        
        try:
            base = symbol[:3]
            quote = symbol[3:6] if len(symbol) >= 6 else "USD"
            
            events = self.economic_calendar.get_upcoming_high_impact(
                currencies=[base, quote],
                hours_ahead=48,
            )
            
            return [e.to_agent_input() for e in events]
        except Exception as e:
            logger.warning(f"Failed to get calendar events: {e}")
            return []
    
    def _get_cot_data(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        """Get COT data for symbol currencies."""
        if not self.cot_reports:
            return {}
        
        try:
            base = symbol[:3]
            report = self.cot_reports.get_for_symbol(symbol)
            
            if report:
                return {base: report.to_agent_input()}
            return {}
        except Exception as e:
            logger.warning(f"Failed to get COT data: {e}")
            return {}
    
    def _get_dxy_data(self) -> Dict[str, Any]:
        """Get DXY snapshot data."""
        if not self.dxy_fetcher:
            return {}
        
        try:
            snapshot = self.dxy_fetcher.get_current()
            if snapshot:
                return snapshot.to_agent_input()
            return {}
        except Exception as e:
            logger.warning(f"Failed to get DXY data: {e}")
            return {}
    
    def _build_session(self) -> Dict[str, Any]:
        """Build session context based on current time."""
        now = datetime.now(timezone.utc)
        hour = now.hour
        weekday = now.weekday()
        
        # Determine session
        if 22 <= hour or hour < 7:
            session = "TOKYO"
        elif 7 <= hour < 12:
            session = "LONDON"
        elif 12 <= hour < 17:
            session = "OVERLAP"  # London/NY overlap
        elif 17 <= hour < 22:
            session = "NEW_YORK"
        else:
            session = "INACTIVE"
        
        # Weekend check
        weekend_approaching = weekday == 4 and hour >= 20  # Friday evening
        
        return {
            "current": session,
            "optimal_for_symbol": session in ("LONDON", "OVERLAP", "NEW_YORK"),
            "session_start_near": False,
            "session_end_near": False,
            "weekend_approaching": weekend_approaching,
        }
    
    def _build_risk(self, market_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build risk context."""
        volatility_regime = "NORMAL"
        
        # Phase 6: Get volatility from market_snapshot if available
        if market_snapshot:
            atr_percentile = market_snapshot.get("atr_percentile")
            if atr_percentile is not None:
                if atr_percentile > 90:
                    volatility_regime = "EXTREME"
                elif atr_percentile > 75:
                    volatility_regime = "HIGH"
                elif atr_percentile < 20:
                    volatility_regime = "LOW"
        
        return {
            "volatility_regime": volatility_regime,
            "volatility_expanding": False,
            "recent_volatility_spike": False,
            "market_stress": "NORMAL",
            "liquidity": "NORMAL",
        }
    
    def _build_account(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Build account context from state."""
        drawdown = state.get("drawdown_pct", 0)
        
        if drawdown >= 10:
            dd_level = "CRITICAL"
        elif drawdown >= 5:
            dd_level = "LARGE"
        elif drawdown >= 2:
            dd_level = "MEDIUM"
        elif drawdown > 0:
            dd_level = "SMALL"
        else:
            dd_level = "NONE"
        
        return {
            "drawdown_level": dd_level,
            "daily_loss_limit_near": state.get("daily_loss_limit_near", False),
            "open_exposure_level": state.get("exposure_level", "NONE"),
        }
