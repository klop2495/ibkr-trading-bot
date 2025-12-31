from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class BrokerPosition:
    symbol: str
    quantity: float
    avg_cost: float
    unrealized_pnl: float
    currency: str


@dataclass
class BrokerOrder:
    order_id: int
    symbol: str
    action: str
    quantity: float
    order_type: str
    limit_price: Optional[float]
    stop_price: Optional[float]
    parent_id: Optional[int]
    status: str


@dataclass
class BrokerState:
    connected: bool
    timestamp: datetime
    positions: Dict[str, BrokerPosition] = field(default_factory=dict)
    open_orders: List[BrokerOrder] = field(default_factory=list)
    cash_balances: Dict[str, float] = field(default_factory=dict)
    net_liquidation: float = 0.0
    available_funds: float = 0.0
    buying_power: float = 0.0


@dataclass
class SyncResult:
    timestamp: datetime
    positions_opened: List[str] = field(default_factory=list)
    positions_closed: List[str] = field(default_factory=list)
    orders_cancelled: List[int] = field(default_factory=list)
    mismatches: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
