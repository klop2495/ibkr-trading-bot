"""
Market Data Service.

Phase 7 Update: Added methods for OHLC, ATR history, 24h prices.
"""

import os
import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.market_data.buffer import MarketDataBuffer
from app.market_data.indicators import atr, rsi, sma
from app.market_data.timeframes import timeframe_seconds
from app.models.snapshot import MarketSnapshot
from app.storage.repositories import RiskEventsRepo, SnapshotsRepo


class MarketDataService:
    """
    Service for fetching and processing market data.
    
    Phase 7: Added methods for:
    - get_ohlc() - OHLC data for candlestick pattern detection
    - get_atr_history() - ATR history for volatility regime detection
    - get_current_prices() - Current prices for all symbols
    - get_24h_prices() - Prices from 24 hours ago for currency strength
    """
    
    def __init__(
        self,
        symbols: List[str],
        timeframes: List[str],
        warmup_bars_min: int,
        fetcher,
        buffer: MarketDataBuffer | None = None,
        snapshots_repo: SnapshotsRepo | None = None,
        risk_events_repo: RiskEventsRepo | None = None,
    ):
        self.symbols = symbols
        self.timeframes = timeframes
        self.warmup_bars_min = warmup_bars_min
        self.fetcher = fetcher
        # keep a few bars beyond warmup to absorb bursts
        self.buffer = buffer or MarketDataBuffer(max_bars=warmup_bars_min + 5)
        self.snapshots_repo = snapshots_repo
        self.risk_events_repo = risk_events_repo
        self.last_bar_counts: Dict[Tuple[str, str], int] = {}
        self.last_qa_issues: Dict[Tuple[str, str], List[str]] = {}
        
        # Phase 7: Cache for bars data (symbol, timeframe) -> list of bars
        self._bars_cache: Dict[Tuple[str, str], List[Any]] = {}
        self._bars_cache_timestamp: Dict[Tuple[str, str], datetime] = {}
        
        # Phase 7: Cache for 24h prices
        self._prices_24h_cache: Dict[str, float] = {}
        self._prices_24h_timestamp: Optional[datetime] = None

    def is_warmup_ready(self, counts: Dict[Tuple[str, str], int]) -> bool:
        for sym in self.symbols:
            for tf in self.timeframes:
                if counts.get((sym, tf), 0) < self.warmup_bars_min:
                    return False
        return True

    def process(self, end_dt_utc: datetime):
        snapshots: List[MarketSnapshot] = []
        counts: Dict[Tuple[str, str], int] = {}
        fetch_errors: List[str] = []
        debug_log = os.getenv("CONTROL_PLANE_LOG_LEVEL", "INFO").upper() == "DEBUG"
        self.last_qa_issues.clear()
        
        for sym in self.symbols:
            for tf in self.timeframes:
                try:
                    bars = self.fetcher.fetch_historical_bars(sym, tf, end_dt_utc, self.warmup_bars_min)
                except Exception as exc:
                    # Log fetch errors to stdout for visibility
                    error_msg = f"market_data_fetch_error symbol={sym} tf={tf} error={exc}"
                    print(error_msg)
                    fetch_errors.append(f"{sym}/{tf}")
                    if self.risk_events_repo:
                        self.risk_events_repo.insert(
                            event_type="MARKET_DATA_FETCH_ERROR",
                            severity="error",
                            symbol=sym,
                            message=f"Failed to fetch bars: {exc}",
                            data={"symbol": sym, "timeframe": tf, "error": str(exc)},
                        )
                    counts[(sym, tf)] = 0
                    continue
                    
                filtered_bars, issues = self._prepare_bars(sym, tf, bars, end_dt_utc)
                self.last_qa_issues[(sym, tf)] = issues
                if self.risk_events_repo and issues:
                    for issue in issues:
                        self.risk_events_repo.insert(
                            event_type=issue,
                            severity="warn",
                            symbol=sym,
                            message=f"market data QA: {issue}",
                            data={"symbol": sym, "timeframe": tf},
                        )
                counts[(sym, tf)] = len(filtered_bars)
                
                # Phase 7: Cache bars for OHLC retrieval
                if filtered_bars:
                    self._bars_cache[(sym, tf)] = filtered_bars
                    self._bars_cache_timestamp[(sym, tf)] = datetime.now(timezone.utc)
                
                if debug_log:
                    print(f"market_data symbol={sym} tf={tf} bars={len(filtered_bars)} warmup_min={self.warmup_bars_min}")
                
                if not filtered_bars:
                    continue
                snap = self._handle_bars(sym, tf, filtered_bars)
                if snap:
                    snapshots.append(snap)
        
        warmup_ready = self.is_warmup_ready(counts)
        
        # Log warmup status summary if not ready or if there were errors
        if not warmup_ready or fetch_errors:
            missing = []
            for sym in self.symbols:
                for tf in self.timeframes:
                    cnt = counts.get((sym, tf), 0)
                    if cnt < self.warmup_bars_min:
                        missing.append(f"{sym}/{tf}:{cnt}/{self.warmup_bars_min}")
            if missing:
                print(f"warmup_incomplete pairs={len(missing)} missing={missing[:5]}{'...' if len(missing) > 5 else ''}")
            if fetch_errors:
                print(f"fetch_errors count={len(fetch_errors)} pairs={fetch_errors[:5]}{'...' if len(fetch_errors) > 5 else ''}")
        
        return warmup_ready, snapshots

    def _prepare_bars(self, symbol: str, timeframe: str, bars, end_dt_utc: datetime):
        if not bars:
            return [], []
        # extract timestamps and sort
        pairs = []
        for b in bars:
            ts = getattr(b, "date", None) or getattr(b, "time", None)
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            pairs.append((ts, b))
        pairs.sort(key=lambda x: x[0])
        issues: List[str] = []

        # deduplicate
        seen_ts = set()
        deduped: List[Tuple[datetime, any]] = []
        for ts, bar in pairs:
            if ts in seen_ts:
                issues.append("DATA_DUP")
                continue
            seen_ts.add(ts)
            deduped.append((ts, bar))

        # gap detection (focus on last 24h window)
        interval = timeframe_seconds(timeframe)
        cutoff = end_dt_utc - timedelta(hours=24)
        recent = [pair for pair in deduped if pair[0] >= cutoff]
        for prev, curr in zip(recent, recent[1:]):
            if (curr[0] - prev[0]).total_seconds() > 1.5 * interval:
                if self._is_weekend_gap(prev[0], curr[0]):
                    continue
                issues.append("DATA_GAP")
                break

        # stale detection
        now = datetime.now(timezone.utc)
        if deduped and (now - deduped[-1][0]).total_seconds() > 2 * interval:
            issues.append("DATA_STALE")

        # backpressure limit
        trimmed = deduped[-self.buffer.max_bars :]
        self.last_bar_counts[(symbol, timeframe)] = len(trimmed)
        return [b for _, b in trimmed], issues

    @staticmethod
    def _is_weekend_gap(prev_ts: datetime, curr_ts: datetime) -> bool:
        """
        Ignore expected FX weekend gaps (Fri close -> Sun open).
        """
        if curr_ts <= prev_ts:
            return False
        day = prev_ts.date()
        end_day = curr_ts.date()
        while day <= end_day:
            if day.weekday() in (5, 6):  # Saturday/Sunday
                return True
            day += timedelta(days=1)
        return False

    def _handle_bars(self, symbol: str, timeframe: str, bars):
        latest = bars[-1]
        ts = getattr(latest, "date", None) or getattr(latest, "time", None)
        if ts is None:
            return None

        closes = [getattr(b, "close", None) for b in bars]
        highs = [getattr(b, "high", None) for b in bars]
        lows = [getattr(b, "low", None) for b in bars]
        if None in closes or None in highs or None in lows:
            return None
        atr_val = atr(highs, lows, closes)
        rsi_val = rsi(closes)
        sma50 = sma(closes, 50)
        sma200 = sma(closes, 200)
        spread_raw = None
        try:
            spread_raw = self.fetcher.fetch_spread(symbol)
        except Exception:
            spread_raw = None

        spread = self._convert_spread_to_pips(symbol, spread_raw)
        data_quality = self._data_quality_from_issues(self.last_qa_issues.get((symbol, timeframe), []))

        snap = MarketSnapshot(
            schema_version=1,
            timestamp=ts,
            symbol=symbol,
            timeframe=timeframe,
            close=closes[-1],
            atr=atr_val or 0.0,
            rsi=rsi_val or 0.0,
            ma_fast=sma50 or 0.0,
            ma_slow=sma200 or 0.0,
            spread=spread,
            data_quality=data_quality,
        )
        if self.snapshots_repo:
            self.snapshots_repo.insert(snap)
        return snap

    @staticmethod
    def _pip_size(symbol: str) -> float:
        """
        Return pip size for a symbol.

        JPY pairs use 0.01, all others 0.0001.
        """
        if symbol and symbol.upper().endswith("JPY"):
            return 0.01
        return 0.0001

    def _convert_spread_to_pips(self, symbol: str, spread_raw: Optional[float]) -> float:
        """
        Convert spread in price terms to pips.

        Args:
            symbol: FX symbol (e.g., "EURUSD")
            spread_raw: price difference ask - bid

        Returns:
            Spread in pips (float), defaults to 0.0 on missing/NaN.
        """
        if spread_raw is None:
            return 0.0
        try:
            if isinstance(spread_raw, float) and math.isnan(spread_raw):
                return 0.0
        except Exception:
            return 0.0

        pip_size = self._pip_size(symbol)
        if pip_size <= 0:
            return 0.0
        return float(spread_raw) / pip_size

    @staticmethod
    def _data_quality_from_issues(issues: List[str]) -> str:
        """Map QA issues to data_quality."""
        if not issues:
            return "ok"
        if "DATA_GAP" in issues:
            return "gap"
        if "DATA_DUP" in issues:
            return "dup"
        if "DATA_STALE" in issues:
            return "stale"
        return "unknown"
    
    # ========== Phase 7: New Methods ==========
    
    def get_ohlc(
        self, 
        symbol: str, 
        timeframe: Optional[str] = None,
        n_bars: int = 50,
    ) -> Dict[str, List[float]]:
        """
        Get OHLC data for a symbol.
        
        Phase 7: Used by TechnicalAgent for candlestick pattern detection.
        
        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            timeframe: Timeframe (default: first available)
            n_bars: Number of bars to return (default: 50)
        
        Returns:
            Dict with 'opens', 'highs', 'lows', 'closes' lists.
            Empty dict if data not available.
        """
        # Use first timeframe if not specified
        tf = timeframe or (self.timeframes[0] if self.timeframes else "H1")
        
        # Get from cache
        bars = self._bars_cache.get((symbol, tf), [])
        
        if not bars:
            return {"opens": [], "highs": [], "lows": [], "closes": []}
        
        # Take last n_bars
        bars = bars[-n_bars:]
        
        opens = []
        highs = []
        lows = []
        closes = []
        
        for b in bars:
            o = getattr(b, "open", None)
            h = getattr(b, "high", None)
            l = getattr(b, "low", None)
            c = getattr(b, "close", None)
            
            if o is not None and h is not None and l is not None and c is not None:
                opens.append(float(o))
                highs.append(float(h))
                lows.append(float(l))
                closes.append(float(c))
        
        return {
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
        }
    
    def get_atr_history(
        self, 
        symbol: str, 
        timeframe: Optional[str] = None,
        n_periods: int = 20,
    ) -> List[float]:
        """
        Get ATR history for a symbol.
        
        Phase 7: Used by RiskAgent for volatility regime detection.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe (default: first available)
            n_periods: Number of ATR values to return
        
        Returns:
            List of ATR values, oldest first.
        """
        tf = timeframe or (self.timeframes[0] if self.timeframes else "H1")
        
        bars = self._bars_cache.get((symbol, tf), [])
        
        if len(bars) < 14:  # Need minimum for ATR calculation
            return []
        
        # Extract OHLC
        highs = [getattr(b, "high", 0) for b in bars]
        lows = [getattr(b, "low", 0) for b in bars]
        closes = [getattr(b, "close", 0) for b in bars]
        
        # Calculate rolling ATR
        atr_values = []
        period = 14
        
        for i in range(period, len(bars)):
            h = highs[i-period:i]
            l = lows[i-period:i]
            c = closes[i-period:i]
            atr_val = atr(h, l, c)
            if atr_val is not None:
                atr_values.append(atr_val)
        
        return atr_values[-n_periods:]
    
    def get_current_prices(self) -> Dict[str, float]:
        """
        Get current prices for all symbols.
        
        Phase 7: Used by CorrelationAgent for currency strength calculation.
        
        Returns:
            Dict mapping symbol to current close price.
        """
        prices = {}
        
        for symbol in self.symbols:
            for tf in self.timeframes:
                bars = self._bars_cache.get((symbol, tf), [])
                if bars:
                    close = getattr(bars[-1], "close", None)
                    if close is not None:
                        prices[symbol] = float(close)
                    break  # Use first available timeframe
        
        return prices
    
    def get_24h_prices(self) -> Dict[str, float]:
        """
        Get prices from 24 hours ago for all symbols.
        
        Phase 7: Used by CorrelationAgent for currency strength calculation.
        
        For H1 timeframe: look back 24 bars
        For M15 timeframe: look back 96 bars
        For H4 timeframe: look back 6 bars
        
        Returns:
            Dict mapping symbol to price from ~24h ago.
        """
        prices = {}
        
        # Calculate lookback based on timeframe
        lookback_bars = {
            "M1": 1440,
            "M5": 288,
            "M15": 96,
            "M30": 48,
            "H1": 24,
            "H4": 6,
            "D1": 1,
        }
        
        for symbol in self.symbols:
            for tf in self.timeframes:
                bars = self._bars_cache.get((symbol, tf), [])
                if not bars:
                    continue
                
                # Get lookback count for this timeframe
                lb = lookback_bars.get(tf, 24)
                
                # Find bar from ~24h ago
                if len(bars) > lb:
                    target_bar = bars[-(lb + 1)]
                elif len(bars) > 1:
                    target_bar = bars[0]  # Use oldest available
                else:
                    continue
                
                close = getattr(target_bar, "close", None)
                if close is not None:
                    prices[symbol] = float(close)
                break  # Use first available timeframe
        
        return prices
    
    def get_close_prices(
        self,
        symbol: str,
        timeframe: Optional[str] = None,
        n_bars: int = 50,
    ) -> List[float]:
        """
        Get close prices for a symbol.
        
        Phase 7: Used by RiskAgent for volatility regime detection.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe (default: first available)
            n_bars: Number of prices to return
        
        Returns:
            List of close prices, oldest first.
        """
        tf = timeframe or (self.timeframes[0] if self.timeframes else "H1")
        
        bars = self._bars_cache.get((symbol, tf), [])
        
        if not bars:
            return []
        
        closes = []
        for b in bars[-n_bars:]:
            c = getattr(b, "close", None)
            if c is not None:
                closes.append(float(c))
        
        return closes
    
    def get_market_data_for_context(
        self,
        symbol: str,
        timeframe: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get all market data needed for context building.
        
        Phase 7: Convenience method for ContextBuilder.
        
        Returns:
            Dict with ohlc, atr_history, current_prices, previous_24h_prices.
        """
        return {
            "ohlc": self.get_ohlc(symbol, timeframe),
            "atr_history": self.get_atr_history(symbol, timeframe),
            "close_prices": self.get_close_prices(symbol, timeframe),
            "current_prices": self.get_current_prices(),
            "previous_24h_prices": self.get_24h_prices(),
        }
