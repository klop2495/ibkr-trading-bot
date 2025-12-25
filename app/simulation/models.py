"""
SimulationEngine Models - Data structures for simulation
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4


class SimTradeStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    BLOCKED = "BLOCKED"


class SimCloseReason(str, Enum):
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    MANUAL = "MANUAL"
    EXPIRED = "EXPIRED"


class SimSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SimEventType(str, Enum):
    # Trade lifecycle
    TRADE_SUBMIT = "TRADE_SUBMIT"
    TRADE_OPEN = "TRADE_OPEN"
    TRADE_CLOSE_TP = "TRADE_CLOSE_TP"
    TRADE_CLOSE_SL = "TRADE_CLOSE_SL"
    TRADE_BLOCKED = "TRADE_BLOCKED"
    
    # Price updates
    PRICE_UPDATE = "PRICE_UPDATE"
    
    # Equity updates
    EQUITY_UPDATE = "EQUITY_UPDATE"
    EQUITY_BASELINE = "EQUITY_BASELINE"
    
    # System
    ENGINE_START = "ENGINE_START"
    ENGINE_STOP = "ENGINE_STOP"
    ERROR = "ERROR"


class BlockReason(str, Enum):
    TRADE_NOT_ALLOWED = "TRADE_NOT_ALLOWED"
    ENTRY_NOT_TRIGGERED = "ENTRY_NOT_TRIGGERED"
    MAX_POSITIONS_REACHED = "MAX_POSITIONS_REACHED"
    SYMBOL_ALREADY_OPEN = "SYMBOL_ALREADY_OPEN"
    MAX_LEVERAGE_EXCEEDED = "MAX_LEVERAGE_EXCEEDED"
    BELOW_MIN_SIZE = "BELOW_MIN_SIZE"
    NO_PRICE_DATA = "NO_PRICE_DATA"
    INVALID_SL_TP = "INVALID_SL_TP"


@dataclass
class SimTrade:
    """Simulated trade record"""
    id: UUID = field(default_factory=uuid4)
    
    # References
    decision_id: Optional[str] = None
    signal_preview_id: Optional[str] = None
    verdict_id: Optional[str] = None
    
    # Trade details
    symbol: str = ""
    side: SimSide = SimSide.BUY
    quantity: float = 0.0
    
    # Prices
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    current_price: Optional[float] = None
    
    # P&L
    pnl: Optional[float] = None
    pnl_pips: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    
    # Status
    status: SimTradeStatus = SimTradeStatus.PENDING
    close_reason: Optional[SimCloseReason] = None
    block_reason: Optional[BlockReason] = None
    
    # Timestamps
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now())
    
    # Risk parameters at entry
    equity_at_entry: Optional[float] = None
    risk_cash: Optional[float] = None
    sl_pips: Optional[float] = None
    tp_pips: Optional[float] = None
    notional: Optional[float] = None
    risk_modifier: float = 1.0


@dataclass
class SimFill:
    """Simulated fill/execution record"""
    id: UUID = field(default_factory=uuid4)
    trade_id: UUID = field(default_factory=uuid4)
    
    fill_type: str = "ENTRY"  # ENTRY or EXIT
    symbol: str = ""
    side: SimSide = SimSide.BUY
    quantity: float = 0.0
    price: float = 0.0
    
    # Market data at fill
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread: Optional[float] = None
    
    timestamp: datetime = field(default_factory=lambda: datetime.now())


@dataclass
class SimEquityPoint:
    """Point on equity curve"""
    id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now())
    
    # Equity components
    baseline_equity: float = 0.0  # From IBKR
    sim_equity: float = 0.0       # Simulated equity
    
    closed_pnl: float = 0.0       # Realized P&L
    open_pnl: float = 0.0         # Unrealized P&L (mark-to-market)
    
    # Exposure
    total_exposure: float = 0.0   # Sum of notional values
    num_open_positions: int = 0
    
    # Source
    equity_source: str = "IBKR"   # IBKR or DEFAULT


@dataclass  
class SimEvent:
    """Simulation event for logging/diagnostics"""
    id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now())
    
    event_type: SimEventType = SimEventType.ENGINE_START
    severity: str = "INFO"  # INFO, WARN, ERROR
    
    # Context
    symbol: Optional[str] = None
    trade_id: Optional[str] = None
    decision_id: Optional[str] = None
    
    # Details
    message: str = ""
    data: dict = field(default_factory=dict)


# Forex pip sizes and minimum lot sizes for IdealPro
FOREX_PIP_SIZES = {
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "AUDUSD": 0.0001,
    "NZDUSD": 0.0001,
    "USDCAD": 0.0001,
    "USDCHF": 0.0001,
    "USDJPY": 0.01,
    # Add more pairs as needed
}

# IdealPro minimum sizes (in base currency units)
IDEALPRO_MIN_SIZES = {
    "EURUSD": 20000,
    "GBPUSD": 20000,
    "AUDUSD": 25000,
    "NZDUSD": 35000,
    "USDCAD": 25000,
    "USDCHF": 25000,
    "USDJPY": 2500000,  # 25000 USD equivalent
}

# Default minimum if pair not in dict
DEFAULT_MIN_SIZE = 25000
DEFAULT_PIP_SIZE = 0.0001
