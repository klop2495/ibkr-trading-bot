from datetime import datetime
from typing import Dict, List, Tuple

from app.market_data.buffer import MarketDataBuffer
from app.market_data.indicators import atr, rsi, sma
from app.market_data.qa import QATracker
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
        qa_tracker: QATracker | None = None,
        buffer: MarketDataBuffer | None = None,
        snapshots_repo: SnapshotsRepo | None = None,
        risk_events_repo: RiskEventsRepo | None = None,
    ):
        self.symbols = symbols
        self.timeframes = timeframes
        self.warmup_bars_min = warmup_bars_min
        self.fetcher = fetcher
        self.qa = qa_tracker or QATracker()
        self.buffer = buffer or MarketDataBuffer()
        self.snapshots_repo = snapshots_repo
        self.risk_events_repo = risk_events_repo

    def is_warmup_ready(self, counts: Dict[Tuple[str, str], int]) -> bool:
        for sym in self.symbols:
            for tf in self.timeframes:
                if counts.get((sym, tf), 0) < self.warmup_bars_min:
                    return False
        return True

    def process(self, end_dt_utc: datetime) -> bool:
        counts: Dict[Tuple[str, str], int] = {}
        for sym in self.symbols:
            for tf in self.timeframes:
                bars = self.fetcher.fetch_historical_bars(sym, tf, end_dt_utc, self.warmup_bars_min)
                counts[(sym, tf)] = len(bars)
                if not bars:
                    continue
                self._handle_bars(sym, tf, bars)
        return self.is_warmup_ready(counts)

    def _handle_bars(self, symbol: str, timeframe: str, bars):
        latest = bars[-1]
        ts = getattr(latest, "date", None) or getattr(latest, "time", None)
        if ts is None:
            return
        issues = self.qa.process(symbol, timeframe, ts)
        if self.risk_events_repo and issues:
            for i in issues:
                self.risk_events_repo.insert(
                    event_type=i.issue,
                    severity="warn",
                    symbol=symbol,
                    message=f"market data QA: {i.issue}",
                )

        closes = [getattr(b, "close", None) for b in bars]
        highs = [getattr(b, "high", None) for b in bars]
        lows = [getattr(b, "low", None) for b in bars]
        if None in closes or None in highs or None in lows:
            return
        atr_val = atr(highs, lows, closes)
        rsi_val = rsi(closes)
        sma50 = sma(closes, 50)
        sma200 = sma(closes, 200)
        spread = None
        try:
            spread = self.fetcher.fetch_spread(symbol)
        except Exception:
            spread = None

        if self.snapshots_repo:
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
                spread=spread or 0.0,
            )
            self.snapshots_repo.insert(snap)
