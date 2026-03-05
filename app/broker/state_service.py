from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app.broker.keys import fx_contract_snapshot, fx_display_symbol, instrument_key
from app.models.broker_state import BrokerOrder, BrokerPosition, BrokerState, SyncResult
from app.storage.repositories import RiskEventsRepo, TradesHistoryRepo
from app.storage.bot_settings_repo import BotSettingsRepo


logger = logging.getLogger(__name__)

PENDING_KEY_GRACE_SECONDS = int(os.getenv("BROKER_PENDING_KEY_GRACE_SECONDS", "180"))
ORPHAN_RECENT_GRACE_SECONDS = int(os.getenv("BROKER_ORPHAN_RECENT_GRACE_SECONDS", "1800"))


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
        bot_settings_repo: Optional[BotSettingsRepo] = None,
        owner_user_id: Optional[str] = None,
    ) -> None:
        self._ib = ib
        self._trades_history_repo = trades_history_repo
        self._risk_events_repo = risk_events_repo
        self._bot_settings_repo = bot_settings_repo
        self._owner_user_id = owner_user_id
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
        return fx_display_symbol(contract)

    def _parse_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _pick_snapshot_exit_price(self, trade: dict, ticker: Any) -> Optional[float]:
        side = str(trade.get("side") or "").upper()
        bid = self._parse_float(getattr(ticker, "bid", None))
        ask = self._parse_float(getattr(ticker, "ask", None))
        last = self._parse_float(getattr(ticker, "last", None))
        close = self._parse_float(getattr(ticker, "close", None))

        # Closing BUY -> sell at bid. Closing SELL -> buy at ask.
        if side.startswith("B") and bid > 0:
            return bid
        if side.startswith("S") and ask > 0:
            return ask
        if last > 0:
            return last
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        if close > 0:
            return close
        return None

    def _market_snapshot_exit_price(self, trade: dict) -> Optional[float]:
        symbol = str(trade.get("symbol") or "").upper()
        if not symbol:
            return None
        try:
            from app.broker.contracts import create_cfd_fx_contract

            contract = create_cfd_fx_contract(self._ib, symbol)
            ticker = self._ib.reqMktData(contract, snapshot=True)

            attempts = 20
            for _ in range(attempts):
                # For ib_insync, sleep must run through IB event loop.
                self._ib.sleep(0.1)
                px = self._pick_snapshot_exit_price(trade, ticker)
                if px is not None:
                    return float(px)

            return self._pick_snapshot_exit_price(trade, ticker)
        except Exception:
            return None

    def _fetch_positions(self) -> Dict[str, BrokerPosition]:
        positions: Dict[str, BrokerPosition] = {}
        for pos in self._ib.positions():
            contract = getattr(pos, "contract", None)
            symbol = self._normalize_symbol(contract)
            key = instrument_key(contract)
            quantity = float(getattr(pos, "position", 0.0) or 0.0)
            if abs(quantity) <= 0.0:
                continue
            if not key:
                logger.warning(
                    "position missing instrument key",
                    extra=fx_contract_snapshot(contract, quantity),
                )
                continue
            logger.info("fx_position", extra=fx_contract_snapshot(contract, quantity))
            positions[key] = BrokerPosition(
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
            key = instrument_key(contract) if contract else None
            orders.append(
                BrokerOrder(
                    order_id=int(order_id),
                    symbol=self._normalize_symbol(contract),
                    instrument_key=key,
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
        symbol_upper = symbol.upper()
        if symbol_upper.startswith("CASH:") or ":" in symbol_upper:
            return state.positions.get(symbol_upper)
        for position in state.positions.values():
            if position.symbol.upper() == symbol_upper:
                return position
        return None

    def get_open_orders_for_symbol(self, symbol: str) -> List[BrokerOrder]:
        symbol_upper = symbol.upper()
        state = self.get_state()
        if ":" in symbol_upper:
            return [o for o in state.open_orders if (o.instrument_key or "").upper() == symbol_upper]
        return [o for o in state.open_orders if o.symbol.upper() == symbol_upper]

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
        symbol_upper = symbol.upper()
        if ":" in symbol_upper:
            if state.positions.get(symbol_upper):
                return False, "broker_has_position"
            if any((order.instrument_key or "").upper() == symbol_upper for order in state.open_orders):
                return False, "broker_has_open_orders"
        else:
            if any(pos.symbol.upper() == symbol_upper for pos in state.positions.values()):
                return False, "broker_has_position"
            if any(order.symbol.upper() == symbol_upper for order in state.open_orders):
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
        symbol_upper = symbol.upper()
        if ":" in symbol_upper:
            if symbol_upper not in state.positions:
                return False, "broker_no_position"
        else:
            if not any(pos.symbol.upper() == symbol_upper for pos in state.positions.values()):
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

    def _get_recent_orphan(self, instrument_key_value: str) -> Optional[dict]:
        if not self._trades_history_repo:
            return None
        try:
            return self._trades_history_repo.get_latest_orphan_by_instrument_key(instrument_key_value)
        except Exception:
            return None

    def _is_recent_orphan(self, orphan: Optional[dict], now: datetime) -> bool:
        if not orphan:
            return False
        opened_at = orphan.get("opened_at") or orphan.get("created_at")
        opened_ts = self._parse_ib_time(opened_at) if opened_at else None
        if not opened_ts:
            return False
        return (now - opened_ts).total_seconds() <= ORPHAN_RECENT_GRACE_SECONDS

    def _validate_cfd_snapshot(
        self,
        positions: List[Any],
        open_trades: List[Any],
    ) -> Tuple[List[dict], List[dict]]:
        unexpected: List[dict] = []
        missing: List[dict] = []

        def check_contract(contract: Any, context: str, extra: Optional[dict] = None) -> None:
            if not contract:
                missing.append({"context": context, **(extra or {})})
                return
            sec_type = getattr(contract, "secType", None)
            con_id = getattr(contract, "conId", None)
            if sec_type != "CFD":
                unexpected.append(
                    {
                        "context": context,
                        "secType": sec_type,
                        "conId": con_id,
                        "localSymbol": getattr(contract, "localSymbol", None),
                        "symbol": getattr(contract, "symbol", None),
                        "currency": getattr(contract, "currency", None),
                        **(extra or {}),
                    }
                )
            if not con_id or int(con_id) <= 0:
                missing.append(
                    {
                        "context": context,
                        "secType": sec_type,
                        "conId": con_id,
                        "localSymbol": getattr(contract, "localSymbol", None),
                        "symbol": getattr(contract, "symbol", None),
                        "currency": getattr(contract, "currency", None),
                        **(extra or {}),
                    }
                )

        for pos in positions:
            contract = getattr(pos, "contract", None)
            check_contract(contract, "position", fx_contract_snapshot(contract, getattr(pos, "position", 0.0)))

        for trade in open_trades:
            contract = getattr(trade, "contract", None)
            check_contract(contract, "trade")

        return unexpected, missing

    def _candidate_keys_by_symbol(
        self,
        state: BrokerState,
        open_trades: List[Any],
    ) -> Dict[str, Set[str]]:
        by_symbol: Dict[str, Set[str]] = {}
        for key, position in state.positions.items():
            symbol = str(position.symbol or "").upper()
            if symbol and key:
                by_symbol.setdefault(symbol, set()).add(key)
        for order in state.open_orders:
            symbol = str(order.symbol or "").upper()
            key = str(order.instrument_key or "").upper()
            if symbol and key:
                by_symbol.setdefault(symbol, set()).add(key)
        for trade in open_trades:
            contract = getattr(trade, "contract", None)
            symbol = self._normalize_symbol(contract).upper()
            key = instrument_key(contract) if contract else None
            if symbol and key:
                by_symbol.setdefault(symbol, set()).add(key)
        return by_symbol

    def _candidate_keys_by_order_id(
        self,
        state: BrokerState,
        open_trades: List[Any],
    ) -> Dict[int, Set[str]]:
        by_order_id: Dict[int, Set[str]] = {}
        for order in state.open_orders:
            key = str(order.instrument_key or "").upper()
            order_id = getattr(order, "order_id", None)
            if key and order_id is not None:
                by_order_id.setdefault(int(order_id), set()).add(key)
        for trade in open_trades:
            contract = getattr(trade, "contract", None)
            key = instrument_key(contract) if contract else None
            order = getattr(trade, "order", None)
            order_id = getattr(order, "orderId", None) if order else None
            if key and order_id is not None:
                by_order_id.setdefault(int(order_id), set()).add(key)
        return by_order_id

    def _is_recent_pending_without_order_id(self, trade: dict, now: datetime) -> bool:
        status = str(trade.get("status") or "").upper()
        if status != "PENDING":
            return False
        if trade.get("ib_order_id") is not None:
            return False
        opened_at = trade.get("opened_at") or trade.get("created_at")
        opened_ts = self._parse_ib_time(opened_at) if opened_at else None
        if not opened_ts:
            return False
        return (now - opened_ts).total_seconds() <= PENDING_KEY_GRACE_SECONDS

    def _normalize_recovered_key(self, key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        key = str(key).upper()
        if key.startswith("CFD:"):
            suffix = key.split(":", 1)[1]
            if suffix.isdigit():
                return key
        return None

    def _repair_missing_instrument_key(
        self,
        trade: dict,
        key_candidates: Dict[str, Set[str]],
        order_key_candidates: Dict[int, Set[str]],
    ) -> Optional[str]:
        ib_order_id = trade.get("ib_order_id")
        if ib_order_id is not None:
            try:
                order_candidates = order_key_candidates.get(int(ib_order_id), set())
            except Exception:
                order_candidates = set()
            if len(order_candidates) == 1:
                recovered_key = self._normalize_recovered_key(next(iter(order_candidates)))
                if recovered_key:
                    return self._save_recovered_key(trade, recovered_key)

        symbol = str(trade.get("symbol") or "").upper()
        if not symbol:
            return None
        candidates = key_candidates.get(symbol) or set()
        if len(candidates) != 1:
            return None
        recovered_key = self._normalize_recovered_key(next(iter(candidates)))
        if not recovered_key:
            return None
        return self._save_recovered_key(trade, recovered_key)

    def _save_recovered_key(self, trade: dict, recovered_key: str) -> Optional[str]:
        symbol = str(trade.get("symbol") or "").upper()
        existing_meta = trade.get("meta")
        meta = existing_meta if isinstance(existing_meta, dict) else {}
        meta["instrument_key"] = recovered_key
        trade["meta"] = meta
        trade_id = str(trade.get("id") or "")
        if self._trades_history_repo and trade_id:
            try:
                self._trades_history_repo.update_meta(trade_id, meta)
            except Exception:
                pass
        self._log_event(
            "MISSING_INSTRUMENT_KEY_REPAIRED",
            "warn",
            "Recovered instrument_key for active trade",
            {"trade_id": trade_id, "symbol": symbol, "instrument_key": recovered_key},
        )
        return recovered_key

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

    def _calculate_trade_pnl(self, trade: dict, exit_price: float) -> Tuple[Optional[float], Optional[float]]:
        side = str(trade.get("side") or "").upper()
        if side.startswith("B"):
            side_sign = 1.0
        elif side.startswith("S"):
            side_sign = -1.0
        else:
            return None, None

        try:
            entry_price = float(trade.get("entry_price"))
            quantity = abs(float(trade.get("quantity")))
        except Exception:
            return None, None

        if quantity <= 0:
            return None, None

        delta = (float(exit_price) - entry_price) * side_sign
        pnl = delta * quantity
        pip_value = 0.01 if "JPY" in str(trade.get("symbol") or "").upper() else 0.0001
        if pip_value <= 0:
            return pnl, None
        pnl_pips = delta / pip_value
        return pnl, pnl_pips

    def _determine_close_reason(
        self,
        trade: dict,
        executions: List[Any],
    ) -> Tuple[str, Optional[float]]:
        symbol = str(trade.get("symbol") or "").upper()
        if not symbol:
            return "UNKNOWN", None
        trade_meta = trade.get("meta") or {}
        trade_key = trade_meta.get("instrument_key")
        if not trade_key:
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
            exec_key = instrument_key(contract) if contract else None
            if exec_key != trade_key:
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

    def _enter_safe_mode(self, reason: str, data: Optional[dict] = None) -> None:
        if self._bot_settings_repo and self._owner_user_id:
            try:
                self._bot_settings_repo.update(self._owner_user_id, {"trading_enabled": False})
            except Exception:
                pass
        self._log_event(
            event_type="BROKER_SAFE_MODE",
            severity="CRITICAL",
            message=f"Trading disabled: {reason}",
            data=data or {"reason": reason},
        )

    def sync_with_db(self) -> SyncResult:
        result = SyncResult(timestamp=self._now())
        with self._sync_lock:
            try:
                state = self.get_state(force_refresh=True)
                raw_positions = list(self._ib.positions())
                raw_open_orders = list(self._ib.openOrders())
                open_trades = list(self._ib.openTrades())
            except Exception as exc:
                result.errors.append(str(exc))
                self._log_event(
                    "BROKER_SYNC_UNTRUSTED",
                    "warn",
                    f"Broker snapshot unavailable: {exc}",
                    {"error": str(exc)},
                )
                return result

            if not state.connected:
                result.errors.append("broker_disconnected")
                self._log_event(
                    "BROKER_SYNC_UNTRUSTED",
                    "warn",
                    "Broker not connected during sync",
                    {},
                )
                return result

            unexpected, missing = self._validate_cfd_snapshot(
                raw_positions,
                open_trades,
            )
            if unexpected:
                result.errors.append("unexpected_sectype")
                self._log_event(
                    "UNEXPECTED_SECTYPE",
                    "CRITICAL",
                    "Unexpected secType detected in broker snapshot",
                    {"samples": unexpected[:10]},
                )
                self._enter_safe_mode("unexpected_sectype", {"samples": unexpected[:10]})
                return result
            if missing:
                result.errors.append("missing_conId")
                self._log_event(
                    "MISSING_CONID",
                    "CRITICAL",
                    "Missing conId detected in broker snapshot",
                    {"samples": missing[:10]},
                )
                self._enter_safe_mode("missing_conid", {"samples": missing[:10]})
                return result

            if not self._trades_history_repo:
                result.errors.append("trades_history_repo_missing")
                return result

            try:
                healed_raw = self._trades_history_repo.heal_inconsistent_open_trades()
                try:
                    healed = int(healed_raw or 0)
                except Exception:
                    healed = 0
                if healed > 0:
                    result.mismatches.append(f"healed_open_closed_inconsistent:{healed}")
                    self._log_event(
                        "BROKER_SYNC_HEAL",
                        "warn",
                        "Healed inconsistent OPEN trades with close markers",
                        {"fixed_rows": healed},
                    )
            except Exception as exc:
                result.errors.append(f"heal_inconsistent_failed:{exc}")

            try:
                db_trades = self._trades_history_repo.get_active_trades_full()
            except Exception as exc:
                result.errors.append(f"db_fetch_failed:{exc}")
                return result

            key_candidates = self._candidate_keys_by_symbol(state, open_trades)
            order_key_candidates = self._candidate_keys_by_order_id(state, open_trades)
            now_ts = self._now()
            trades_by_key: Dict[str, List[dict]] = {}
            for trade in db_trades:
                meta = trade.get("meta") or {}
                trade_key = meta.get("instrument_key")
                if not trade_key:
                    if self._is_recent_pending_without_order_id(trade, now_ts):
                        result.mismatches.append(f"pending_missing_key_grace:{trade.get('id')}")
                        continue
                    repaired_key = self._repair_missing_instrument_key(trade, key_candidates, order_key_candidates)
                    if repaired_key:
                        trade_key = repaired_key
                    else:
                        result.errors.append("missing_instrument_key")
                        self._log_event(
                            "MISSING_INSTRUMENT_KEY",
                            "CRITICAL",
                            "Active trade missing instrument_key",
                            {"trade_id": trade.get("id"), "symbol": trade.get("symbol")},
                        )
                        self._enter_safe_mode("missing_instrument_key", {"trade_id": trade.get("id")})
                        return result
                normalized_trade_key = self._normalize_recovered_key(str(trade_key))
                if not normalized_trade_key:
                    result.errors.append("unexpected_trade_key")
                    self._log_event(
                        "UNEXPECTED_SECTYPE",
                        "CRITICAL",
                        "Trade instrument_key is not CFD",
                        {"trade_id": trade.get("id"), "instrument_key": trade_key},
                    )
                    self._enter_safe_mode("unexpected_trade_key", {"trade_id": trade.get("id")})
                    return result
                trades_by_key.setdefault(normalized_trade_key, []).append(trade)

            broker_flat = (
                len(state.positions) == 0
                and len(state.open_orders) == 0
                and len(open_trades) == 0
            )

            if broker_flat:
                executions: List[Any] = []
                try:
                    executions = list(self._ib.reqExecutions())
                except Exception:
                    executions = []

                for trade in db_trades:
                    symbol = str(trade.get("symbol") or "").upper()
                    try:
                        _, exit_from_exec = self._determine_close_reason(trade, executions)
                        exit_px = exit_from_exec
                        if exit_px is None:
                            exit_px = self._market_snapshot_exit_price(trade)
                        if exit_px is None:
                            exit_px = float(trade.get("entry_price") or 0.0)
                        pnl, pnl_pips = self._calculate_trade_pnl(trade, exit_px)
                        self._trades_history_repo.close_trade(
                            trade_id=str(trade.get("id")),
                            exit_price=exit_px,
                            close_reason="BROKER_FLAT",
                            pnl=pnl,
                            pnl_pips=pnl_pips,
                        )
                        if symbol:
                            result.positions_closed.append(symbol)
                    except Exception as exc:
                        result.errors.append(f"close_failed:{symbol}:{exc}")
                return result

            positions = state.positions
            broker_position_keys = set(positions.keys())
            broker_order_keys = {o.instrument_key for o in state.open_orders if o.instrument_key}

            # CASE A/B: broker positions
            for key, position in positions.items():
                db_list = trades_by_key.get(key, [])
                if db_list:
                    if len(db_list) > 1:
                        result.mismatches.append(f"multiple_db_trades:{position.symbol}:{len(db_list)}")
                    db_trade = db_list[0]
                    raw_qty = float(db_trade.get("quantity") or 0.0)
                    side = str(db_trade.get("side") or "").upper()
                    if side.startswith("S"):
                        db_qty = -abs(raw_qty)
                    else:
                        db_qty = abs(raw_qty)
                    if abs(db_qty - position.quantity) > 1e-6:
                        result.mismatches.append(
                            f"quantity_mismatch:{position.symbol}:db={db_qty}:broker={position.quantity}"
                        )
                    continue

                # CASE B: broker has position, DB missing -> create orphan record
                orphan = self._get_recent_orphan(key)
                if orphan:
                    if self._is_recent_orphan(orphan, now_ts):
                        result.mismatches.append(f"orphan_recent_exists:{key}")
                    else:
                        result.mismatches.append(f"orphan_exists:{key}")
                    continue
                if self._trades_history_repo.has_orphan_by_instrument_key(key):
                    result.mismatches.append(f"orphan_exists:{key}")
                    continue
                side = "BUY" if position.quantity > 0 else "SELL"
                try:
                    self._trades_history_repo.create_trade(
                        symbol=position.symbol,
                        side=side,
                        quantity=abs(position.quantity),
                        entry_price=position.avg_cost,
                        stop_loss=None,
                        take_profit=None,
                        mode="paper",
                        ib_order_id=None,
                        status="ORPHAN_POSITION",
                        meta={"instrument_key": key},
                    )
                    result.positions_opened.append(position.symbol)
                    self._log_event(
                        "BROKER_ORPHAN_POSITION",
                        "warn",
                        f"Broker position without DB record: {position.symbol}",
                        {"symbol": position.symbol, "quantity": position.quantity, "broker_key": key},
                    )
                except Exception as exc:
                    result.errors.append(f"orphan_create_failed:{position.symbol}:{exc}")

            # CASE C: DB has trade, broker has no position
            executions: List[Any] = []
            try:
                executions = list(self._ib.reqExecutions())
            except Exception:
                executions = []

            for trade_key, trades in trades_by_key.items():
                if trade_key in broker_position_keys:
                    continue
                if trade_key in broker_order_keys:
                    result.mismatches.append(f"open_order_without_position:{trade_key}")
                    continue
                for trade in trades:
                    reason, exit_price = self._determine_close_reason(trade, executions)
                    if exit_price is None:
                        exit_price = self._market_snapshot_exit_price(trade)
                    if exit_price is None:
                        exit_price = float(trade.get("entry_price") or 0.0)
                    symbol = str(trade.get("symbol") or "").upper()
                    try:
                        pnl, pnl_pips = self._calculate_trade_pnl(trade, float(exit_price))
                        self._trades_history_repo.close_trade(
                            trade_id=str(trade.get("id")),
                            exit_price=float(exit_price),
                            close_reason=reason,
                            pnl=pnl,
                            pnl_pips=pnl_pips,
                        )
                        if symbol:
                            result.positions_closed.append(symbol)
                    except Exception as exc:
                        result.errors.append(f"close_failed:{symbol}:{exc}")

            # Orphan SL/TP orders (log only)
            for order in state.open_orders:
                if order.parent_id and order.instrument_key and order.instrument_key not in broker_position_keys:
                    result.mismatches.append(f"orphan_order:{order.order_id}:{order.instrument_key}")
                    self._log_event(
                        "BROKER_ORPHAN_ORDER",
                        "warn",
                        "Orphan order without position",
                        {
                            "order_id": order.order_id,
                            "symbol": order.symbol,
                            "instrument_key": order.instrument_key,
                            "parent_id": order.parent_id,
                        },
                    )

        return result
