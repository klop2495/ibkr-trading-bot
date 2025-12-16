from datetime import datetime, timezone
from typing import Dict, List, Tuple

from pydantic import BaseModel, ConfigDict

from app.market_data.timeframes import timeframe_seconds


class QAIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str
    issue: str
    ts_utc: datetime


class QATracker:
    def __init__(self):
        self.last_ts: Dict[Tuple[str, str], datetime] = {}

    def process(self, symbol: str, timeframe: str, ts_utc: datetime) -> List[QAIssue]:
        issues: List[QAIssue] = []
        key = (symbol, timeframe)
        now = datetime.now(timezone.utc)
        interval = timeframe_seconds(timeframe)

        last = self.last_ts.get(key)
        if last:
            if ts_utc == last:
                issues.append(QAIssue(symbol=symbol, timeframe=timeframe, issue="DATA_DUP", ts_utc=ts_utc))
            elif (ts_utc - last).total_seconds() > 1.5 * interval:
                issues.append(QAIssue(symbol=symbol, timeframe=timeframe, issue="DATA_GAP", ts_utc=ts_utc))
        if last and (now - last).total_seconds() > 2 * interval:
            issues.append(QAIssue(symbol=symbol, timeframe=timeframe, issue="DATA_STALE", ts_utc=ts_utc))

        self.last_ts[key] = ts_utc
        return issues
