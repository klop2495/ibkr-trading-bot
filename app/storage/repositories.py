from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from typing import Any, Optional

from app.models import MarketSnapshot, Signal
from app.models.signal_preview import SignalPreviewV1
from app.models.decision import DecisionV1
from app.models.risk_verdict import RiskVerdictV1
from app.models.execution_report_v1 import ExecutionReportV1
from app.models.order_intent import OrderIntentV1
from app.models.broker_request import BrokerRequestV1
from app.models.reconciliation_report_v1 import ReconciliationReportV1
from app.models.order_intent import OrderIntentV1
from app.storage.db import SupabaseDB


class BaseRepo:
    def __init__(self, db: SupabaseDB) -> None:
        self.db = db


class SnapshotsRepo(BaseRepo):
    table = "market_snapshots"

    def insert(self, snap: MarketSnapshot) -> dict:
        payload = {
            "schema_version": snap.schema_version,
            "ts": snap.timestamp.isoformat(),
            "symbol": snap.symbol,
            "timeframe": snap.timeframe,
            "close": snap.close,
            "atr": snap.atr,
            "rsi": snap.rsi,
            "ma_fast": snap.ma_fast,
            "ma_slow": snap.ma_slow,
            "spread": snap.spread,
        }
        res = self.db.client.table(self.table).insert(payload).execute()
        return {"count": len(res.data or []), "data": res.data}


class SignalsRepo(BaseRepo):
    table = "signals"

    def insert(self, sig: Signal) -> dict:
        payload = {
            "schema_version": sig.schema_version,
            "ts": datetime.utcnow().isoformat(),
            "symbol": sig.symbol,
            "raw_signal": sig.raw_signal,
            "entry_triggered": sig.entry_triggered,
            "sl_pips": sig.sl_pips,
            "tp_pips": sig.tp_pips,
            "confidence": sig.confidence,
        }
        res = self.db.client.table(self.table).insert(payload).execute()
        return {"count": len(res.data or []), "data": res.data}


class DecisionsRepo(BaseRepo):
    table = "control_decisions"

    def _new_id(self) -> str:
        return str(uuid4())

    def _is_unique_violation(self, exc: Exception) -> bool:
        code = getattr(exc, "code", None) or getattr(exc, "sqlstate", None)
        if code == "23505":
            return True
        for attr in ("args", "detail", "details", "context"):
            val = getattr(exc, attr, None)
            if val and "23505" in str(val):
                return True
            if val and "duplicate key value violates unique constraint" in str(val).lower():
                return True
        msg = str(exc).lower()
        if "duplicate key value violates unique constraint" in msg:
            return True
        return False

    def insert_decision(self, dec) -> str:
        payload = dec.to_db_row()
        if "id" not in payload or not payload.get("id"):
            payload["id"] = self._new_id()
        try:
            res = self.db.client.table(self.table).insert(payload).execute()
            rows = getattr(res, "data", None) or []
            if rows:
                return rows[0].get("id") or payload["id"]
            return payload["id"]
        except Exception as exc:
            if not self._is_unique_violation(exc):
                raise
        res = (
            self.db.client.table(self.table)
            .select("id")
            .eq("signal_preview_id", str(payload["signal_preview_id"]))
            .eq("decision_version", payload["decision_version"])
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("id")
        raise RuntimeError("decision id not found after insert")

    def insert_decisions_bulk(self, decisions: list[DecisionV1]) -> list[str]:
        if not decisions:
            return []
        payloads = []
        ids = []
        for dec in decisions:
            row = dec.to_db_row()
            if "id" not in row or not row.get("id"):
                row["id"] = self._new_id()
            ids.append(row["id"])
            payloads.append(row)
        res = self.db.client.table(self.table).insert(payloads).execute()
        rows = getattr(res, "data", None) or []
        if rows and len(rows) == len(payloads):
            return [r.get("id") or ids[i] for i, r in enumerate(rows)]
        return ids


class RiskVerdictsRepo(BaseRepo):
    table = "risk_verdicts"

    def _new_id(self) -> str:
        return str(uuid4())

    def _is_unique_violation(self, exc: Exception) -> bool:
        code = getattr(exc, "code", None) or getattr(exc, "sqlstate", None)
        if code == "23505":
            return True
        for attr in ("args", "detail", "details", "context"):
            val = getattr(exc, attr, None)
            if val and "23505" in str(val):
                return True
            if val and "duplicate key value violates unique constraint" in str(val).lower():
                return True
        msg = str(exc).lower()
        if "duplicate key value violates unique constraint" in msg:
            return True
        return False

    def insert_verdict(self, verdict: RiskVerdictV1) -> str:
        payload = verdict.to_db_row()
        if "id" not in payload or not payload.get("id"):
            payload["id"] = self._new_id()
        try:
            res = self.db.client.table(self.table).insert(payload).execute()
            rows = getattr(res, "data", None) or []
            if rows:
                return rows[0].get("id") or payload["id"]
            return payload["id"]
        except Exception as exc:
            if not self._is_unique_violation(exc):
                raise
        res = (
            self.db.client.table(self.table)
            .select("id")
            .eq("decision_id", str(payload["decision_id"]))
            .eq("risk_version", payload["risk_version"])
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("id")
        raise RuntimeError("risk verdict id not found after insert")

    def insert_verdicts_bulk(self, verdicts: list[RiskVerdictV1]) -> list[str]:
        if not verdicts:
            return []
        payloads = []
        ids = []
        for v in verdicts:
            row = v.to_db_row()
            if "id" not in row or not row.get("id"):
                row["id"] = self._new_id()
            ids.append(row["id"])
            payloads.append(row)
        res = self.db.client.table(self.table).insert(payloads).execute()
        rows = getattr(res, "data", None) or []
        if rows and len(rows) == len(payloads):
            return [r.get("id") or ids[i] for i, r in enumerate(rows)]
        return ids


class ExecutionReportsRepo(BaseRepo):
    table = "control_execution_reports"

    def _is_unique_violation(self, exc: Exception) -> bool:
        code = getattr(exc, "code", None) or getattr(exc, "sqlstate", None)
        if code == "23505":
            return True
        for attr in ("args", "detail", "details", "context"):
            val = getattr(exc, attr, None)
            if val and "23505" in str(val):
                return True
            if val and "duplicate key value violates unique constraint" in str(val).lower():
                return True
        msg = str(exc).lower()
        if "duplicate key value violates unique constraint" in msg:
            return True
        return False

    def insert_report(self, rep: ExecutionReportV1) -> str:
        payload = rep.to_db_row()
        if "id" not in payload or not payload.get("id"):
            payload["id"] = str(uuid4())
        try:
            res = self.db.client.table(self.table).insert(payload).execute()
            rows = getattr(res, "data", None) or []
            if rows:
                return rows[0].get("id") or payload["id"]
            return payload["id"]
        except Exception as exc:
            if not self._is_unique_violation(exc):
                raise
        res = (
            self.db.client.table(self.table)
            .select("id")
            .eq("decision_id", str(payload["decision_id"]))
            .eq("execution_version", payload["execution_version"])
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("id")
        raise RuntimeError("execution report id not found after insert")


class RiskEventsRepo(BaseRepo):
    table = "risk_events"

    def insert(
        self,
        event_type: str,
        severity: str = "info",
        symbol: Optional[str] = None,
        message: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> dict:
        payload = {
            "ts": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "severity": severity,
            "symbol": symbol,
            "message": message,
            "data": data or {},
        }
        res = self.db.client.table(self.table).insert(payload).execute()
        return {"count": len(res.data or []), "data": res.data}


class SignalPreviewsRepo(BaseRepo):
    table = "signal_previews"

    def insert_preview(self, preview: SignalPreviewV1) -> None:
        _ = self.insert_preview_return_id(preview)

    def insert_preview_return_id(self, preview: SignalPreviewV1):
        payload = preview.to_db_row()
        payload["id"] = str(uuid4())
        try:
            res = self.db.client.table(self.table).insert(payload).execute()
            rows = getattr(res, "data", None) or []
            if rows:
                return rows[0].get("id", payload["id"])
            return payload["id"]
        except Exception as exc:
            # Idempotent: unique violation is acceptable
            msg = str(exc).lower()
            if "duplicate key" in msg or "unique constraint" in msg:
                pass
            else:
                raise
        # fetch existing on conflict
        res = (
            self.db.client.table(self.table)
            .select("id")
            .eq("ts_utc", payload["ts_utc"])
            .eq("symbol", payload["symbol"])
            .eq("timeframe_trigger", payload["timeframe_trigger"])
            .eq("engine_version", payload["engine_version"])
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("id")
        raise RuntimeError("signal_preview id not found after insert")


class OrderIntentsRepo(BaseRepo):
    table = "control_order_intents"

    def _is_unique_violation(self, exc: Exception) -> bool:
        code = getattr(exc, "code", None) or getattr(exc, "sqlstate", None)
        if code == "23505":
            return True
        for attr in ("args", "detail", "details", "context"):
            val = getattr(exc, attr, None)
            if val and "23505" in str(val):
                return True
            if val and "duplicate key value violates unique constraint" in str(val).lower():
                return True
        msg = str(exc).lower()
        if "duplicate key value violates unique constraint" in msg:
            return True
        return False

    def insert_intent(self, intent: OrderIntentV1) -> str:
        payload = intent.to_db_row()
        if "id" not in payload or not payload.get("id"):
            payload["id"] = str(uuid4())
        try:
            res = self.db.client.table(self.table).insert(payload).execute()
            rows = getattr(res, "data", None) or []
            if rows:
                return rows[0].get("id") or payload["id"]
            return payload["id"]
        except Exception as exc:
            if not self._is_unique_violation(exc):
                raise
        res = (
            self.db.client.table(self.table)
            .select("id")
            .eq("execution_report_id", str(payload["execution_report_id"]))
            .eq("intent_version", payload["intent_version"])
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("id")
        raise RuntimeError("order intent id not found after insert")


class BrokerRequestsRepo(BaseRepo):
    table = "control_broker_requests"

    def _is_unique_violation(self, exc: Exception) -> bool:
        code = getattr(exc, "code", None) or getattr(exc, "sqlstate", None)
        if code == "23505":
            return True
        for attr in ("args", "detail", "details", "context"):
            val = getattr(exc, attr, None)
            if val and "23505" in str(val):
                return True
            if val and "duplicate key value violates unique constraint" in str(val).lower():
                return True
        msg = str(exc).lower()
        if "duplicate key value violates unique constraint" in msg:
            return True
        return False

    def insert_request(self, req: BrokerRequestV1) -> str:
        payload = req.to_db_row()
        if "id" not in payload or not payload.get("id"):
            payload["id"] = str(uuid4())
        try:
            res = self.db.client.table(self.table).insert(payload).execute()
            rows = getattr(res, "data", None) or []
            if rows:
                return rows[0].get("id") or payload["id"]
            return payload["id"]
        except Exception as exc:
            if not self._is_unique_violation(exc):
                raise
        res = (
            self.db.client.table(self.table)
            .select("id")
            .eq("order_intent_id", str(payload["order_intent_id"]))
            .eq("request_version", payload["request_version"])
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("id")
        raise RuntimeError("broker request id not found after insert")


class ReconciliationReportsRepo(BaseRepo):
    table = "control_reconciliation_reports"

    def _is_unique_violation(self, exc: Exception) -> bool:
        code = getattr(exc, "code", None) or getattr(exc, "sqlstate", None)
        if code == "23505":
            return True
        for attr in ("args", "detail", "details", "context"):
            val = getattr(exc, attr, None)
            if val and "23505" in str(val):
                return True
            if val and "duplicate key value violates unique constraint" in str(val).lower():
                return True
        msg = str(exc).lower()
        if "duplicate key value violates unique constraint" in msg:
            return True
        return False

    def insert_report(self, rep: ReconciliationReportV1) -> str:
        payload = rep.to_db_row()
        if "id" not in payload or not payload.get("id"):
            payload["id"] = str(uuid4())
        try:
            res = self.db.client.table(self.table).insert(payload).execute()
            rows = getattr(res, "data", None) or []
            if rows:
                return rows[0].get("id") or payload["id"]
            return payload["id"]
        except Exception as exc:
            if not self._is_unique_violation(exc):
                raise
        res = (
            self.db.client.table(self.table)
            .select("id")
            .eq("broker_request_id", str(payload["broker_request_id"]))
            .eq("recon_version", payload["recon_version"])
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("id")
        raise RuntimeError("reconciliation report id not found after insert")


class TradesHistoryRepo(BaseRepo):
    """Repository for trades_history table - tracks open/closed trades with P/L."""
    table = "trades_history"

    def create_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: Optional[float],
        stop_loss: Optional[float],
        take_profit: Optional[float],
        mode: str = "paper",
        signal_preview_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        ib_order_id: Optional[int] = None,
        status: str = "PENDING",
    ) -> str:
        """
        Create a new trade record with initial status.
        
        P0-B: Trade lifecycle:
        - PENDING: Order created, not yet confirmed by broker
        - SUBMITTED: Order sent to broker, awaiting fill
        - OPEN: Order filled, position is open
        - CLOSED: Position closed (SL/TP hit or manual)
        - CANCELLED: Order cancelled before fill
        - REJECTED: Order rejected by broker
        """
        trade_id = str(uuid4())
        payload = {
            "id": trade_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "mode": mode,
            "status": status,
            "opened_at": datetime.utcnow().isoformat(),
            "signal_preview_id": signal_preview_id,
            "decision_id": decision_id,
            "ib_order_id": ib_order_id,
        }
        self.db.client.table(self.table).insert(payload).execute()
        return trade_id

    def exists_by_decision_id(self, decision_id: str) -> bool:
        """Check if a trade already exists for the given decision_id."""
        if not decision_id:
            return False
        res = (
            self.db.client.table(self.table)
            .select("id")
            .eq("decision_id", decision_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return len(rows) > 0

    def get_active_trades(self, symbol: Optional[str] = None) -> list[dict]:
        """Get active trades (PENDING/SUBMITTED/OPEN), optionally filtered by symbol."""
        query = (
            self.db.client.table(self.table)
            .select("id, symbol, status, decision_id, ib_order_id")
            .in_("status", ["PENDING", "SUBMITTED", "OPEN"])
        )
        if symbol:
            query = query.eq("symbol", symbol)
        res = query.execute()
        return getattr(res, "data", None) or []

    def count_active_trades(self, symbol: Optional[str] = None) -> int:
        """Count active trades (PENDING/SUBMITTED/OPEN), optionally by symbol."""
        active = self.get_active_trades(symbol=symbol)
        return len(active)

    def open_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: Optional[float],
        stop_loss: Optional[float],
        take_profit: Optional[float],
        mode: str = "paper",
        signal_preview_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> str:
        """Create a new trade record with status OPEN (legacy compatibility)."""
        return self.create_trade(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            mode=mode,
            signal_preview_id=signal_preview_id,
            decision_id=decision_id,
            status="OPEN",
        )

    def update_status(
        self,
        trade_id: str,
        status: str,
        entry_price: Optional[float] = None,
        ib_order_id: Optional[int] = None,
        error_message: Optional[str] = None,
        quantity: Optional[float] = None,
    ) -> None:
        """
        Update trade status.
        
        P0-B: Called when order status changes:
        - PENDING -> SUBMITTED (order sent)
        - SUBMITTED -> OPEN (order filled)
        - SUBMITTED -> CANCELLED (order cancelled)
        - SUBMITTED -> REJECTED (order rejected)
        
        Also updates quantity if it was adjusted by FX Funds Guard.
        """
        payload = {
            "status": status,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if entry_price is not None:
            payload["entry_price"] = entry_price
        if ib_order_id is not None:
            payload["ib_order_id"] = ib_order_id
        if error_message is not None:
            payload["error_message"] = error_message
        if quantity is not None:
            payload["quantity"] = quantity
        
        self.db.client.table(self.table).update(payload).eq("id", trade_id).execute()

    def get_trade_by_ib_order_id(self, ib_order_id: int) -> Optional[dict]:
        """Get trade by IB order ID."""
        res = (
            self.db.client.table(self.table)
            .select("*")
            .eq("ib_order_id", ib_order_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def get_pending_trades(self) -> list[dict]:
        """Get all trades in PENDING or SUBMITTED status."""
        res = (
            self.db.client.table(self.table)
            .select("*")
            .in_("status", ["PENDING", "SUBMITTED"])
            .execute()
        )
        return res.data or []

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        close_reason: str,
        pnl: Optional[float] = None,
        pnl_pips: Optional[float] = None,
    ) -> None:
        """Close an existing trade with exit details."""
        payload = {
            "exit_price": exit_price,
            "close_reason": close_reason,
            "pnl": pnl,
            "pnl_pips": pnl_pips,
            "status": "CLOSED",
            "closed_at": datetime.utcnow().isoformat(),
        }
        self.db.client.table(self.table).update(payload).eq("id", trade_id).execute()

    def get_open_trades(self, symbol: Optional[str] = None) -> list[dict]:
        """Get all open trades, optionally filtered by symbol."""
        query = self.db.client.table(self.table).select("*").eq("status", "OPEN")
        if symbol:
            query = query.eq("symbol", symbol)
        res = query.order("opened_at", desc=True).execute()
        return res.data or []

    def get_all_open_trades(self) -> list[dict]:
        """Get all open trades (no filter)."""
        res = (
            self.db.client.table(self.table)
            .select("*")
            .eq("status", "OPEN")
            .execute()
        )
        return res.data or []

    def count_open_trades(self) -> int:
        """Count all open trades."""
        res = (
            self.db.client.table(self.table)
            .select("id", count="exact")
            .eq("status", "OPEN")
            .execute()
        )
        return res.count if hasattr(res, 'count') and res.count is not None else len(res.data or [])

    def get_trade_by_id(self, trade_id: str) -> Optional[dict]:
        """Get a single trade by ID."""
        res = self.db.client.table(self.table).select("*").eq("id", trade_id).limit(1).execute()
        return res.data[0] if res.data else None

    def get_recent_trades(self, limit: int = 100) -> list[dict]:
        """Get recent trades ordered by opened_at."""
        res = (
            self.db.client.table(self.table)
            .select("*")
            .order("opened_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []


def make_repos(db: SupabaseDB) -> dict[str, Any]:
    """
    Convenience factory.
    """
    repos = {
        "snapshots": SnapshotsRepo(db),
        "signals": SignalsRepo(db),
        "decisions": DecisionsRepo(db),
        "risk_events": RiskEventsRepo(db),
        "trades_history": TradesHistoryRepo(db),
    }
    try:
        from app.storage.agent_reports_repo import AgentReportsRepo  # local import to avoid circular

        repos["agent_reports"] = AgentReportsRepo(db)
    except Exception:
        pass
    repos["signal_previews"] = SignalPreviewsRepo(db)
    repos["order_intents"] = OrderIntentsRepo(db)
    repos["broker_requests"] = BrokerRequestsRepo(db)
    repos["reconciliation_reports"] = ReconciliationReportsRepo(db)
    repos["execution_reports"] = ExecutionReportsRepo(db)
    
    # Phase 7: Performance tracker persistence
    try:
        from app.storage.performance_tracker_repo import PerformanceTrackerRepo
        repos["performance_tracker"] = PerformanceTrackerRepo(db)
    except Exception:
        pass
    
    return repos
