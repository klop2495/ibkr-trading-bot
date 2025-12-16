#!/usr/bin/env bash
set -e

MODELS_DIR="app/models"
mkdir -p "$MODELS_DIR"

# =========================
# snapshot.py
# =========================
cat > "$MODELS_DIR/snapshot.py" <<'PY'
from pydantic import BaseModel
from datetime import datetime
from typing import Literal

class MarketSnapshot(BaseModel):
    schema_version: int = 1
    timestamp: datetime
    symbol: str
    timeframe: Literal["M15", "H1", "H4"]
    close: float
    atr: float
    rsi: float
    ma_fast: float
    ma_slow: float
    spread: float
PY

# =========================
# signal.py
# =========================
cat > "$MODELS_DIR/signal.py" <<'PY'
from pydantic import BaseModel
from typing import Literal

class Signal(BaseModel):
    schema_version: int = 1
    symbol: str
    raw_signal: Literal["long", "short", "flat"]
    entry_triggered: bool
    sl_pips: float
    tp_pips: float
    confidence: float
PY

# =========================
# agent_report.py
# =========================
cat > "$MODELS_DIR/agent_report.py" <<'PY'
from pydantic import BaseModel
from typing import List, Optional

class AgentReport(BaseModel):
    schema_version: int = 1
    trade_allowed: bool
    risk_modifier: float
    flags: List[str]
    comment: Optional[str] = None
PY

# =========================
# decision.py
# =========================
cat > "$MODELS_DIR/decision.py" <<'PY'
from pydantic import BaseModel
from typing import List, Optional, Literal
from uuid import UUID

class Decision(BaseModel):
    schema_version: int = 1
    decision_id: UUID
    symbol: str
    action: Literal["open", "close", "hold"]
    direction: Optional[Literal["buy", "sell"]] = None
    volume: Optional[float] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    reason_codes: List[str]
PY

# =========================
# execution.py
# =========================
cat > "$MODELS_DIR/execution.py" <<'PY'
from pydantic import BaseModel
from typing import Optional, Literal
from uuid import UUID

class ExecutionReport(BaseModel):
    schema_version: int = 1
    decision_id: UUID
    order_id: Optional[str]
    status: Literal[
        "accepted",
        "filled",
        "rejected",
        "cancelled",
        "sl_failed",
        "tp_failed",
    ]
    message: Optional[str] = None
PY

# =========================
# __init__.py
# =========================
cat > "$MODELS_DIR/__init__.py" <<'PY'
from .snapshot import MarketSnapshot
from .signal import Signal
from .agent_report import AgentReport
from .decision import Decision
from .execution import ExecutionReport

__all__ = [
    "MarketSnapshot",
    "Signal",
    "AgentReport",
    "Decision",
    "ExecutionReport",
]
PY

echo "✅ Pydantic models created in app/models/"

