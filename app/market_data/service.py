import os
import math
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from app.market_data.buffer import MarketDataBuffer
from app.market_data.indicators import atr, rsi, sma
from app.market_data.timeframes import timeframe_seconds
from app.models.snapshot import MarketSnapshot
from app.storage.repositories import RiskEventsRepo, SnapshotsRepo


class MarketDataService:
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

        # gap detection
        interval = timeframe_seconds(timeframe)
        for prev, curr in zip(deduped, deduped[1:]):
            if (curr[0] - prev[0]).total_seconds() > 1.5 * interval:
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
        spread = None
        try:
            spread = self.fetcher.fetch_spread(symbol)
        except Exception:
            spread = None

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
            spread=0.0 if spread is None or (isinstance(spread, float) and math.isnan(spread)) else spread,
        )
        if self.snapshots_repo:
            self.snapshots_repo.insert(snap)
        return snap
