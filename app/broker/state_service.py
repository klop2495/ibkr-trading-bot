from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.models.broker_state import (
    BrokerOrder,
    BrokerPosition,
    BrokerState,
    SyncResult,
)
from app.storage.repositories import RiskEventsRepo, TradesHistoryRepo


logger = logging.getLogger(__name__)


class BrokerConnectionError(RuntimeError):
    """Raised when broker state is requested without an active connection."""


class BrokerStateService:
    """
    Single source of truth for broker account state.
    All components must read state only via this service.
    """

    def __init__(
        self,
        ib: Any,
        trades_history_repo: Optional[TradesHistoryRepo] = None,
        risk_events_repo: Optional[RiskEventsRepo] = None,
    ) -> None:
        self._ib = ib
        self._trades_history_repo = trades_history_repo
        self._risk_events_repo = risk_events_repo
        self._cache_ttl_seconds = 5.0
        self._cache_ts = 0.0
        self._cache_state: Optional[BrokerState] = None
        self._sync_lock = threading.RLock()

    @property
    def sync_lock(self) -> threading.RLock:
        return self._sync_lock

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _is_connected(self) -> bool:
        try:
            return bool(self._ib and self._ib.isConnected())
        except Exception:
            return False

    def _normalize_symbol(self, contract: Any) -> str:
        if not contract:
            return "UNKNOWN"
        sec_type = getattr(contract, "secType", None)
        if sec_type == "CASH":
            base = getattr(contract, "symbol", "") or ""
            quote = getattr(contract, "currency", "") or ""
            return f"{base}{quote}".upper()
        symbol = getattr(contract, "symbol", None)
        return (symbol or "UNKNOWN").upper()

    def _parse_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _fetch_positions(self) -> Dict[str, BrokerPosition]:
        positions: Dict[str, BrokerPosition] = {}
        for pos in self._ib.positions():
            contract = getattr(pos, "contract", None)
            symbol = self._normalize_symbol(contract)
            quantity = float(getattr(pos, "position", 0.0) or 0.0)
            if abs(quantity) <= 0.0:
                continue
            positions[symbol] = BrokerPosition(
                symbol=symbol,
                quantity=quantity,
                avg_cost=self._parse_float(getattr(pos, "avgCost", 0.0)),
                unrealized_pnl=self._parse_float(getattr(pos, "unrealizedPNL", 0.0)),
                currency=getattr(contract, "currency", None) if contract else "",
            )
        return positions

    def _fetch_open_orders(self) -> List[BrokerOrder]:
        orders: List[BrokerOrder] = []
        trades_map: Dict[int, Any] = {}
        try:
            for trade in self._ib.openTrades():
                order = getattr(trade, "order", None)
                order_id = getattr(order, "orderId", None)
                if order_id is not None:
                    trades_map[int(order_id)] = trade
        except Exception:
            trades_map = {}

        for order in self._ib.openOrders():
            order_id = getattr(order, "orderId", None)
            if order_id is None:
                continue
            trade = trades_map.get(int(order_id))
            contract = getattr(trade, "contract", None) if trade else None
            status = "OPEN"
            if trade and getattr(trade, "orderStatus", None):
                status = getattr(trade.orderStatus, "status", status)
            orders.append(
                BrokerOrder(
                    order_id=int(order_id),
                    symbol=self._normalize_symbol(contract),
                    action=str(getattr(order, "action", "") or ""),
                    quantity=self._parse_float(getattr(order, "totalQuantity", 0.0)),
                    order_type=str(getattr(order, "orderType", "") or ""),
                    limit_price=getattr(order, "lmtPrice", None),
                    stop_price=getattr(order, "auxPrice", None),
                    parent_id=getattr(order, "parentId", None),
                    status=status,
                )
            )
        return orders

    def _fetch_account_summary(self) -> Tuple[Dict[str, float], float, float, float]:
        cash_balances: Dict[str, float] = {}
        net_liquidation = 0.0
        available_funds = 0.0
        buying_power = 0.0
        for item in self._ib.accountSummary():
            tag = getattr(item, "tag", None)
            value = getattr(item, "value", None)
            currency = getattr(item, "currency", None) or ""
            if tag in ("CashBalance", "TotalCashValue", "SettledCash"):
                cash_balances[currency] = self._parse_float(value)
            elif tag == "NetLiquidation":
                net_liquidation = self._parse_float(value)
            elif tag == "AvailableFunds":
                available_funds = self._parse_float(value)
            elif tag == "BuyingPower":
                buying_power = self._parse_float(value)
        return cash_balances, net_liquidation, available_funds, buying_power

    def get_state(self, force_refresh: bool = False) -> BrokerState:
        with self._sync_lock:
            if not self._is_connected():
                raise BrokerConnectionError("IB Gateway not connected")
            now = time.monotonic()
            if (
                not force_refresh
                and self._cache_state is not None
                and (now - self._cache_ts) <= self._cache_ttl_seconds
            ):
                return self._cache_state

            positions = self._fetch_positions()
            open_orders = self._fetch_open_orders()
            cash_balances, net_liquidation, available_funds, buying_power = self._fetch_account_summary()
            state = BrokerState(
                connected=True,
                timestamp=self._now(),
                positions=positions,
                open_orders=open_orders,
                cash_balances=cash_balances,
                net_liquidation=net_liquidation,
                available_funds=available_funds,
                buying_power=buying_power,
            )
            self._cache_state = state
            self._cache_ts = now
            return state

    def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        state = self.get_state()
        return state.positions.get(symbol.upper())

    def get_open_orders_for_symbol(self, symbol: str) -> List[BrokerOrder]:
        symbol_upper = symbol.upper()
        state = self.get_state()
        return [o for o in state.open_orders if o.symbol == symbol_upper]

    def get_cash_balance(self, currency: str) -> float:
        state = self.get_state()
        return state.cash_balances.get(currency, 0.0)

    def has_position(self, symbol: str) -> bool:
        position = self.get_position(symbol)
        return bool(position and abs(position.quantity) > 0)

    def has_pending_orders(self, symbol: str) -> bool:
        return len(self.get_open_orders_for_symbol(symbol)) > 0

    def is_healthy(self) -> bool:
        return self._is_connected()

    def invalidate_cache(self) -> None:
        with self._sync_lock:
            self._cache_state = None
            self._cache_ts = 0.0

    def wait_for_connection(self, timeout: int = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_connected():
                return True
            time.sleep(1.0)
        return False

    def can_open_position(self, symbol: str, side: str, quantity: float) -> Tuple[bool, str]:
        try:
            state = self.get_state()
        except BrokerConnectionError:
            return False, "broker_disconnected"
        if not state.connected:
            return False, "broker_disconnected"
        if state.positions.get(symbol.upper()):
            return False, "broker_has_position"
        if any(order.symbol == symbol.upper() for order in state.open_orders):
            return False, "broker_has_open_orders"
        if quantity <= 0:
            return False, "invalid_quantity"
        if side.upper() not in ("BUY", "SELL"):
            return False, "invalid_side"
        return True, "ok"

    def can_close_position(self, symbol: str) -> Tuple[bool, str]:
        try:
            state = self.get_state()
        except BrokerConnectionError:
            return False, "broker_disconnected"
        if not state.connected:
            return False, "broker_disconnected"
        if symbol.upper() not in state.positions:
            return False, "broker_no_position"
        return True, "ok"

    def _parse_ib_time(self, value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None
        return None

    def _close_reason_from_price(
        self,
        symbol: str,
        price: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
    ) -> Optional[str]:
        pip_value = 0.01 if "JPY" in symbol.upper() else 0.0001
        tolerance = pip_value * 2
        if stop_loss is not None and abs(price - stop_loss) <= tolerance:
            return "SL_HIT"
        if take_profit is not None and abs(price - take_profit) <= tolerance:
            return "TP_HIT"
        return None

    def _determine_close_reason(
        self,
        trade: dict,
        executions: List[Any],
    ) -> Tuple[str, Optional[float]]:
        symbol = str(trade.get("symbol") or "").upper()
        if not symbol:
            return "UNKNOWN", None

        trade_side = str(trade.get("side") or "").upper()
        opened_at = trade.get("opened_at") or trade.get("created_at")
        opened_ts = self._parse_ib_time(opened_at) if opened_at else None
        trade_ib_order_id = trade.get("ib_order_id")
        stop_loss = trade.get("stop_loss")
        take_profit = trade.get("take_profit")

        candidates: List[Tuple[datetime, float]] = []
        for fill in executions:
            contract = getattr(fill, "contract", None)
            exec_obj = getattr(fill, "execution", None) or fill
            exec_symbol = self._normalize_symbol(contract)
            if exec_symbol != symbol:
                continue
            exec_time = self._parse_ib_time(getattr(exec_obj, "time", None))
            if opened_ts and exec_time and exec_time < opened_ts:
                continue
            exec_side = str(getattr(exec_obj, "side", "") or "").upper()
            if trade_side and exec_side:
                if trade_side.startswith("B") and exec_side.startswith("B"):
                    continue
                if trade_side.startswith("S") and exec_side.startswith("S"):
                    continue
            order_id = getattr(exec_obj, "orderId", None)
            if trade_ib_order_id and order_id == trade_ib_order_id:
                continue
            price = self._parse_float(getattr(exec_obj, "price", None))
            if exec_time:
                candidates.append((exec_time, price))

        if not candidates:
            return "UNKNOWN", None

        candidates.sort(key=lambda item: item[0])
        _, price = candidates[-1]
        reason = self._close_reason_from_price(symbol, price, stop_loss, take_profit)
        if reason:
            return reason, price
        return "MANUAL", price

    def _log_event(self, event_type: str, severity: str, message: str, data: Optional[dict] = None) -> None:
        if not self._risk_events_repo:
            return
        try:
            self._risk_events_repo.insert(
                event_type=event_type,
                severity=severity,
                message=message,
                data=data or {},
            )
        except Exception:
            pass

    def sync_with_db(self) -> SyncResult:
        result = SyncResult(timestamp=self._now())
        with self._sync_lock:
            try:
                state = self.get_state(force_refresh=True)
            except Exception as exc:
                result.errors.append(str(exc))
                return result

            if not self._trades_history_repo:
                result.errors.append("trades_history_repo_missing")
                return result

            try:
                db_trades = self._trades_history_repo.get_active_trades_full()
            except Exception as exc:
                result.errors.append(f"db_fetch_failed:{exc}")
                return result

            trades_by_symbol: Dict[str, List[dict]] = {}
            for trade in db_trades:
                symbol = str(trade.get("symbol") or "").upper()
                if not symbol:
                    continue
                trades_by_symbol.setdefault(symbol, []).append(trade)

            positions = state.positions

            # CASE A/B: broker positions
            for symbol, position in positions.items():
                db_list = trades_by_symbol.get(symbol, [])
                if db_list:
                    if len(db_list) > 1:
                        result.mismatches.append(f"multiple_db_trades:{symbol}:{len(db_list)}")
                    db_trade = db_list[0]
                    db_qty = float(db_trade.get("quantity") or 0.0)
                    if abs(db_qty - position.quantity) > 1e-6:
                        result.mismatches.append(
                            f"quantity_mismatch:{symbol}:db={db_qty}:broker={position.quantity}"
                        )
                    continue

                # CASE B: broker has position, DB missing -> create orphan record
                side = "BUY" if position.quantity > 0 else "SELL"
                try:
                    self._trades_history_repo.create_trade(
                        symbol=symbol,
                        side=side,
                        quantity=abs(position.quantity),
                        entry_price=position.avg_cost,
                        stop_loss=None,
                        take_profit=None,
                        mode="paper",
                        ib_order_id=None,
                        status="ORPHAN_POSITION",
                    )
                    result.positions_opened.append(symbol)
                    self._log_event(
                        "BROKER_ORPHAN_POSITION",
                        "warn",
                        f"Broker position without DB record: {symbol}",
                        {"symbol": symbol, "quantity": position.quantity},
                    )
                except Exception as exc:
                    result.errors.append(f"orphan_create_failed:{symbol}:{exc}")

            # CASE C: DB has trade, broker has no position
            executions: List[Any] = []
            try:
                executions = list(self._ib.reqExecutions())
            except Exception:
                executions = []

            for symbol, trades in trades_by_symbol.items():
                if symbol in positions:
                    continue
                for trade in trades:
                    reason, exit_price = self._determine_close_reason(trade, executions)
                    if exit_price is None:
                        exit_price = float(trade.get("entry_price") or 0.0)
                    try:
                        self._trades_history_repo.close_trade(
                            trade_id=str(trade.get("id")),
                            exit_price=float(exit_price),
                            close_reason=reason,
                        )
                        result.positions_closed.append(symbol)
                    except Exception as exc:
                        result.errors.append(f"close_failed:{symbol}:{exc}")

            # Orphan SL/TP orders (log only)
            broker_symbols = set(positions.keys())
            for order in state.open_orders:
                if order.parent_id and order.symbol not in broker_symbols:
                    result.mismatches.append(f"orphan_order:{order.order_id}:{order.symbol}")
                    self._log_event(
                        "BROKER_ORPHAN_ORDER",
                        "warn",
                        "Orphan order without position",
                        {
                            "order_id": order.order_id,
                            "symbol": order.symbol,
                            "parent_id": order.parent_id,
                        },
                    )

        return result
