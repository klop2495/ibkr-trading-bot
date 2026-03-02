import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from app.agents.runner import AgentsAggregator, aggregate_decision
from app.models.decision import DecisionV1
from app.models.risk_verdict import RiskVerdictV1
from app.models.signal_preview import (
    Confidence,
    DataQuality,
    Direction,
    SetupType,
    SignalPreviewV1,
    SpreadQuality,
)
from app.risk.engine_v1 import RiskEngineV1
from app.storage.bot_settings_repo import BotSettingsRepo
from app.storage.db import SupabaseDB
from app.storage.repositories import DecisionsRepo, RiskEventsRepo, RiskVerdictsRepo, SignalPreviewsRepo, TradesHistoryRepo
from app.execution.service import ExecutionService, ExecutionMode, ExecutionResult

# Phase 0: Shadow mode parallel decisions
from app.models.parallel_decision import ParallelDecisionV1
from app.storage.parallel_decisions_repo import ParallelDecisionsRepo

# Phase 1: Data sources
from app.data_sources import EconomicCalendarFetcher, COTReportsFetcher, DXYFetcher
from app.agents.safety import SourceHealthMonitor, BudgetLimiter, AgentCache, ResponseValidator

# Phase 3-4: LLM Agents and integration
from app.agents.parallel_runner import ParallelDecisionRunner, create_parallel_runner

# Phase 6: Signal generation from IB Gateway market data
from app.market_data.service import MarketDataService
from app.market_data.seed_fetcher import SeedFetcher
from app.signals.engine_v1 import SignalEngineV1
from app.storage.repositories import SnapshotsRepo
from app.models.bot_settings import DEFAULT_SYMBOLS
from app.broker.state_service import BrokerStateService
from app.broker.ib_utils import IBFailSafeState
from app.notifications.telegram import TelegramNotifier
from app.forecast.signal_lifecycle import SignalLifecycleManager


DEFAULT_BACKFILL_BATCH = 25
DEFAULT_BACKFILL_MAX_PER_TICK = 400
DEFAULT_BACKFILL_MAX_SECONDS = 1.5
DEFAULT_BACKFILL_SAFETY_MS = 150
DEFAULT_BACKFILL_EMA_ALPHA = 0.2
DEFAULT_IDLE_BACKOFF_BASE = 2.0
DEFAULT_IDLE_BACKOFF_MAX = 60.0
DEFAULT_EQUITY = 10000.0  # Default equity for dry-run mode
DEFAULT_POSITION_SYNC_INTERVAL = 300  # Sync positions every 5 minutes


def _safe_uuid(value: Any) -> Optional[UUID]:
    try:
        return UUID(str(value))
    except Exception:
        return None


def persist_control_decision_and_verdict(
    *,
    preview: SignalPreviewV1,
    preview_id: UUID,
    params: Any,
    settings: Any,
    agents_aggregator: Optional[AgentsAggregator],
    risk_engine: RiskEngineV1,
    decisions_repo: DecisionsRepo,
    risk_verdicts_repo: RiskVerdictsRepo,
    risk_events_repo: Optional[RiskEventsRepo],
) -> Tuple[Optional[str], Optional[str], Optional[DecisionV1], Any]:
    decision: Optional[DecisionV1] = None
    verdict = None
    try:
        agg = aggregate_decision(preview, agents_aggregator.run(preview, params, signal_preview_id=preview_id)) if agents_aggregator else None
    except Exception:
        agg = None

    trade_allowed = False
    risk_modifier = 1.0
    flags = list(getattr(preview, "flags", []) or [])
    commentary = None
    safe_setup = preview.setup_type if isinstance(getattr(preview, "setup_type", None), SetupType) else SetupType(getattr(getattr(preview, "setup_type", None), "value", SetupType.NO_TRADE.value))
    safe_direction = preview.direction if isinstance(getattr(preview, "direction", None), Direction) else Direction(getattr(getattr(preview, "direction", None), "value", Direction.FLAT.value))
    if safe_setup not in (SetupType.NO_TRADE,) and safe_direction not in (Direction.FLAT,):
        if agg:
            trade_allowed = bool(agg.get("trade_allowed"))
            risk_modifier = agg.get("risk_modifier", 1.0)
            flags.extend(agg.get("flags") or [])
            commentary = agg.get("commentary")
    ts_utc = getattr(preview, "ts_utc", None) or datetime.now(timezone.utc)
    decision = DecisionV1(
        ts_utc=ts_utc,
        symbol=getattr(preview, "symbol", ""),
        signal_preview_id=preview_id,
        trade_allowed=trade_allowed,
        risk_modifier=risk_modifier,
        flags=flags,
        commentary=commentary,
    )
    try:
        decision_id = decisions_repo.insert_decision(decision)
    except Exception as exc:
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="CONTROL_DECISION_PERSIST",
                severity="ERROR",
                message="persist decision failed",
                data={"signal_preview_id": str(preview_id), "symbol": preview.symbol, "error": str(exc)},
            )
        return None, None, decision, None

    decision.id = decision_id
    try:
        verdict = risk_engine.evaluate(decision, settings)
    except Exception as exc:
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="RISK_VERDICT_PERSIST",
                severity="ERROR",
                message="risk evaluate failed",
                data={"signal_preview_id": str(preview_id), "decision_id": decision_id, "symbol": preview.symbol, "error": str(exc)},
            )
        return decision_id, None, decision, None

    try:
        verdict_id = risk_verdicts_repo.insert_verdict(verdict)
    except Exception as exc:
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="RISK_VERDICT_PERSIST",
                severity="ERROR",
                message="persist verdict failed",
                data={"signal_preview_id": str(preview_id), "decision_id": decision_id, "symbol": preview.symbol, "error": str(exc)},
            )
        return decision_id, None, decision, None

    return decision_id, verdict_id, decision, verdict


def build_decision(preview: SignalPreviewV1, preview_id: UUID, params: Any, agents_aggregator: Optional[AgentsAggregator]) -> DecisionV1:
    decision: Optional[DecisionV1] = None
    try:
        agg = aggregate_decision(preview, agents_aggregator.run(preview, params, signal_preview_id=preview_id)) if agents_aggregator else None
    except Exception:
        agg = None

    trade_allowed = False
    risk_modifier = 1.0
    flags = list(getattr(preview, "flags", []) or [])
    commentary = None
    safe_setup = preview.setup_type if isinstance(getattr(preview, "setup_type", None), SetupType) else SetupType(getattr(getattr(preview, "setup_type", None), "value", SetupType.NO_TRADE.value))
    safe_direction = preview.direction if isinstance(getattr(preview, "direction", None), Direction) else Direction(getattr(getattr(preview, "direction", None), "value", Direction.FLAT.value))
    if safe_setup not in (SetupType.NO_TRADE,) and safe_direction not in (Direction.FLAT,):
        if agg:
            trade_allowed = bool(agg.get("trade_allowed"))
            risk_modifier = agg.get("risk_modifier", 1.0)
            flags.extend(agg.get("flags") or [])
            commentary = agg.get("commentary")
    ts_utc = getattr(preview, "ts_utc", None) or datetime.now(timezone.utc)
    decision = DecisionV1(
        ts_utc=ts_utc,
        symbol=getattr(preview, "symbol", ""),
        signal_preview_id=preview_id,
        trade_allowed=trade_allowed,
        risk_modifier=risk_modifier,
        flags=flags,
        commentary=commentary,
    )
    return decision


def _parse_enum(enum_cls: Any, raw: Any, default: Any, aliases: Optional[Dict[str, Any]] = None) -> Any:
    """
    Safely parse enums from legacy/unknown values with a deterministic fallback.
    """
    if isinstance(raw, enum_cls):
        return raw
    if raw is None:
        return default
    if aliases and isinstance(raw, str):
        lookup = aliases.get(raw.upper())
        if lookup is not None:
            return lookup
    try:
        return enum_cls(raw)
    except Exception:
        return default


def _parse_direction(raw: Any) -> Direction:
    """
    Parse direction safely, mapping legacy "FLAT" strings and unknown values to a neutral default.
    """
    return _parse_enum(Direction, raw, Direction.FLAT, aliases={"FLAT": Direction.FLAT})


def _preview_from_row(row: Dict[str, Any]) -> SignalPreviewV1:
    return SignalPreviewV1(
        ts_utc=datetime.fromisoformat(row.get("ts_utc")) if isinstance(row.get("ts_utc"), str) else row.get("ts_utc", datetime.now(timezone.utc)),
        symbol=row.get("symbol") or "",
        timeframe_trigger=row.get("timeframe_trigger") or "",
        setup_type=_parse_enum(SetupType, row.get("setup_type"), SetupType.NO_TRADE),
        direction=_parse_direction(row.get("direction")),
        setup_present=bool(row.get("setup_present", False)),
        entry_triggered=bool(row.get("entry_triggered", False)),
        confidence=_parse_enum(Confidence, row.get("confidence"), Confidence.LOW),
        rr=float(row.get("rr") or 0.0),
        data_quality=_parse_enum(DataQuality, row.get("data_quality"), DataQuality.OK),
        spread_quality=_parse_enum(SpreadQuality, row.get("spread_quality"), SpreadQuality.OK),
        flags=row.get("flags") or [],
        sl_distance_pips=float(row.get("sl_distance_pips")) if row.get("sl_distance_pips") is not None else None,
        tp_distance_pips=float(row.get("tp_distance_pips")) if row.get("tp_distance_pips") is not None else None,
    )


def process_pending_previews(
    *,
    client: Any,
    params: Any,
    settings: Any,
    agents_aggregator: Optional[AgentsAggregator],
    risk_engine: RiskEngineV1,
    decisions_repo: DecisionsRepo,
    risk_verdicts_repo: RiskVerdictsRepo,
    risk_events_repo: Optional[RiskEventsRepo],
    limit: int,
    start_ts: float,
    max_seconds: float,
    safety_ms: int,
    scan_mode: str = "recent",
    cursor_ts_utc: Optional[str] = None,
    out_last_ts: Optional[list] = None,
    symbols_empty_fallback_used: Optional[list] = None,
) -> Dict[str, Any]:
    if client is None:
        return {
            "processed": 0,
            "fetched": 0,
            "scanned": 0,
            "fetch_ms": 0.0,
            "persist_ms": 0.0,
            "early_break": False,
            "sample": None,
        }

    fetch_start = time.perf_counter()
    try:
        query = client.table("signal_previews").select(
            "id, ts_utc, symbol, timeframe_trigger, setup_type, direction, setup_present, entry_triggered, confidence, rr, data_quality, spread_quality, flags, sl_distance_pips, tp_distance_pips"
        )
        if scan_mode == "recent":
            query = query.order("ts_utc", desc=True)
        elif scan_mode == "backward":
            query = query.order("ts_utc", desc=True)
        else:
            query = query.order("ts_utc", desc=False)
        query = query.limit(limit)
        symbols = getattr(settings, "symbols", None)
        if symbols:
            query = query.in_("symbol", symbols)
        else:
            if symbols_empty_fallback_used is not None:
                symbols_empty_fallback_used.append(True)
        if scan_mode == "historical" and cursor_ts_utc:
            query = query.gt("ts_utc", cursor_ts_utc)
        if scan_mode == "backward" and cursor_ts_utc:
            query = query.lt("ts_utc", cursor_ts_utc)
        res = query.execute()
        rows = getattr(res, "data", None) or []
        if rows and out_last_ts is not None:
            out_last_ts.append(rows[-1].get("ts_utc"))
        preview_ids = []
        for row in rows:
            raw_id = row.get("id")
            if raw_id:
                try:
                    preview_ids.append(str(UUID(str(raw_id))))
                except Exception:
                    preview_ids.append(str(raw_id))
        pending_rows = list(rows)
        if preview_ids:
            decisions_res = (
                client.table("control_decisions").select("signal_preview_id").in_("signal_preview_id", preview_ids).execute()
            )
            decisions_rows = getattr(decisions_res, "data", None) or []
            decided_ids = set()
            for d_row in decisions_rows:
                sid = d_row.get("signal_preview_id")
                if sid:
                    decided_ids.add(str(sid))
            pending_rows = [row for row in rows if str(row.get("id")) not in decided_ids]
        if os.getenv("CONTROL_PLANE_LOG_LEVEL", "INFO").upper() == "DEBUG":
            first_ts = rows[0].get("ts_utc") if rows else None
            last_ts = rows[-1].get("ts_utc") if rows else None
            print(
                f"backfill_scan mode={scan_mode} cursor={cursor_ts_utc} first_ts={first_ts} last_ts={last_ts} rows={len(rows)} pending={len(pending_rows)}"
            )
    except Exception as exc:
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="CONTROL_DECISION_PERSIST",
                severity="ERROR",
                message="fetch pending previews failed",
                data={"error": str(exc)},
            )
        return {
            "processed": 0,
            "fetched": 0,
            "scanned": 0,
            "fetch_ms": 0.0,
            "persist_ms": 0.0,
            "early_break": False,
            "sample": None,
        }
    fetch_ms = (time.perf_counter() - fetch_start) * 1000

    processed = 0
    sample_ids: Optional[Tuple[str, str, str]] = None
    early_break = False
    budget_ms = max_seconds * 1000

    items: list[Tuple[Any, SignalPreviewV1]] = []
    for row in pending_rows:
        now_ms = (time.perf_counter() - start_ts) * 1000
        if now_ms >= budget_ms - safety_ms:
            early_break = True
            break
        preview_id = _safe_uuid(row.get("id")) or uuid4()
        preview = _preview_from_row(row)
        items.append((preview_id, preview))
        now_ms = (time.perf_counter() - start_ts) * 1000
        if now_ms >= budget_ms - safety_ms:
            early_break = True
            break

    if not items:
        return {
            "processed": processed,
            "fetched": len(pending_rows),
            "scanned": len(rows),
            "fetch_ms": fetch_ms,
            "persist_ms": 0.0,
            "early_break": early_break,
            "sample": sample_ids,
        }

    persist_start = time.perf_counter()
    decisions: list[DecisionV1] = []
    verdicts: list[Any] = []
    try:
        for preview_id, preview in items:
            decision = build_decision(preview, preview_id, params, agents_aggregator)
            decisions.append(decision)
        decision_ids = decisions_repo.insert_decisions_bulk(decisions)
        for decision, dec_id in zip(decisions, decision_ids):
            decision.id = dec_id
            verdict = risk_engine.evaluate(decision, settings)
            verdict.decision_id = decision.id
            verdict.signal_preview_id = decision.signal_preview_id
            verdicts.append(verdict)
        verdict_ids = risk_verdicts_repo.insert_verdicts_bulk(verdicts)
        for verdict, v_id in zip(verdicts, verdict_ids):
            verdict.id = v_id
        processed = len(verdicts)
        if processed > 0:
            sample_ids = (str(decision_ids[0]), str(verdict_ids[0]), str(items[0][0]))
    except Exception as exc:
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="CONTROL_PLANE_BACKFILL",
                severity="ERROR",
                message="bulk persist failed; fallback to per-item",
                data={"error": str(exc)},
            )
        processed = 0
        sample_ids = None
        for preview_id, preview in items:
            now_ms = (time.perf_counter() - start_ts) * 1000
            if now_ms >= budget_ms - safety_ms:
                early_break = True
                break
            decision_id, verdict_id, _, _ = persist_control_decision_and_verdict(
                preview=preview,
                preview_id=preview_id,
                params=params,
                settings=settings,
                agents_aggregator=agents_aggregator,
                risk_engine=risk_engine,
                decisions_repo=decisions_repo,
                risk_verdicts_repo=risk_verdicts_repo,
                risk_events_repo=risk_events_repo,
            )
            if decision_id and verdict_id:
                processed += 1
                if sample_ids is None:
                    sample_ids = (decision_id, verdict_id, str(preview_id))
            now_ms = (time.perf_counter() - start_ts) * 1000
            if now_ms >= budget_ms - safety_ms:
                early_break = True
                break

    persist_ms = (time.perf_counter() - persist_start) * 1000
    return {
        "processed": processed,
        "fetched": len(pending_rows),
        "scanned": len(rows),
        "fetch_ms": fetch_ms,
        "persist_ms": persist_ms,
        "early_break": early_break,
        "sample": sample_ids,
    }


def run_backfill_tick(
    *,
    client: Any,
    params: Any,
    settings: Any,
    agents_aggregator: Optional[AgentsAggregator],
    risk_engine: RiskEngineV1,
    decisions_repo: DecisionsRepo,
    risk_verdicts_repo: RiskVerdictsRepo,
    risk_events_repo: Optional[RiskEventsRepo],
    batch_size: int,
    backfill_max_per_tick: int,
    backfill_max_seconds: float,
    enabled: bool,
    adaptive: bool,
    stable_fast_ticks: int,
    safety_ms: int = DEFAULT_BACKFILL_SAFETY_MS,
    ema_alpha: float = DEFAULT_BACKFILL_EMA_ALPHA,
    persist_ema_ms: Optional[float] = None,
    historical_cursor_ts: Optional[str] = None,
    symbols_empty: bool = False,
) -> Tuple[int, int, int, Optional[Tuple[str, str, str]], str, float, Optional[str], bool, int, float, float, bool, int, Optional[str]]:
    if not enabled:
        return (
            0,
            0,
            0,
            None,
            "DISABLED",
            0.0,
            None,
            False,
            batch_size,
            0.0,
            0.0,
            False,
            stable_fast_ticks,
            None,
        )

    batch_cap = max(1, batch_size)
    start_ts = time.perf_counter()
    budget_ms = backfill_max_seconds * 1000
    processed_total = 0
    fetched_total = 0
    scanned_total = 0
    fetch_ms_total = 0.0
    persist_ms_total = 0.0
    sample_ids: Optional[Tuple[str, str, str]] = None
    stop_reason = "NO_PENDING"
    error_message = None
    early_break = False
    new_cursor_ts: Optional[str] = historical_cursor_ts

    try:
        def _run_scan(
            scan_mode: str, limit: int, cursor_ts: Optional[str]
        ) -> Tuple[int, int, float, float, bool, Optional[Tuple[str, str, str]], Optional[str], Optional[str], Optional[str], int]:
            out_last_ts: list = []
            symbols_empty_flag: list = []
            batch_result = process_pending_previews(
                client=client,
                params=params,
                settings=settings,
                agents_aggregator=agents_aggregator,
                risk_engine=risk_engine,
                decisions_repo=decisions_repo,
                risk_verdicts_repo=risk_verdicts_repo,
                risk_events_repo=risk_events_repo,
                limit=limit,
                start_ts=start_ts,
                max_seconds=backfill_max_seconds,
                safety_ms=safety_ms,
                scan_mode=scan_mode,
                cursor_ts_utc=cursor_ts,
                out_last_ts=out_last_ts,
                symbols_empty_fallback_used=symbols_empty_flag if symbols_empty else None,
            )
            contract_error: Optional[str] = None
            result_type: Optional[str] = None
            if not isinstance(batch_result, dict):
                contract_error = "non-dict"
                result_type = str(type(batch_result))
            else:
                required_keys = {"processed", "fetched", "fetch_ms", "persist_ms", "early_break", "sample", "scanned"}
                missing = required_keys - set(batch_result.keys())
                if missing:
                    contract_error = f"missing_keys={sorted(missing)}"
            if contract_error:
                return 0, 0, 0.0, 0.0, False, None, contract_error, None, result_type, False, 0
            processed = batch_result.get("processed") or 0
            fetched = batch_result.get("fetched") or 0
            scanned = batch_result.get("scanned") or 0
            fetch_ms = batch_result.get("fetch_ms") or 0.0
            persist_ms = batch_result.get("persist_ms") or 0.0
            early = bool(batch_result.get("early_break") or False)
            sample = batch_result.get("sample")
            last_ts = out_last_ts[-1] if out_last_ts else None
            used_fallback = bool(symbols_empty_flag)
            if sample_ids is None and sample is not None:
                return processed, fetched, fetch_ms, persist_ms, early, sample, None, last_ts, result_type, used_fallback, scanned
            return processed, fetched, fetch_ms, persist_ms, early, None, None, last_ts, result_type, used_fallback, scanned

        scan_mode = "recent"
        force_backward_once = False
        last_ts_recent: Optional[str] = None
        while processed_total < backfill_max_per_tick:
            elapsed_ms = (time.perf_counter() - start_ts) * 1000
            if not (force_backward_once and scan_mode == "backward") and elapsed_ms >= budget_ms - safety_ms:
                stop_reason = "MAX_SECONDS"
                early_break = True
                break
            remaining = backfill_max_per_tick - processed_total
            limit = min(batch_cap, remaining)
            cursor_arg = None
            if scan_mode == "historical":
                cursor_arg = new_cursor_ts
            elif scan_mode == "backward":
                cursor_arg = new_cursor_ts or last_ts_recent
            (
                processed,
                fetched,
                fetch_ms,
                persist_ms,
                early,
                sample,
                contract_error,
                last_ts,
                result_type,
                used_fallback,
                scanned,
            ) = _run_scan(scan_mode, limit, cursor_arg)
            if contract_error:
                error_message = "BACKFILL_CONTRACT_VIOLATION"
                stop_reason = "ERROR"
                if risk_events_repo:
                    risk_events_repo.insert(
                        event_type="CONTROL_PLANE_BACKFILL",
                        severity="ERROR",
                        message="process_pending_previews contract violation",
                        data={
                            "type": result_type,
                            "keys": None,
                            "error": contract_error,
                        },
                    )
                break
            fetched_total += fetched
            scanned_total += scanned
            fetch_ms_total += fetch_ms
            persist_ms_total += persist_ms
            processed_total += processed
            if sample_ids is None and sample is not None:
                sample_ids = sample
            if scan_mode == "historical":
                if last_ts:
                    new_cursor_ts = str(last_ts)
                elif scanned == 0:
                    new_cursor_ts = None

            elapsed_ms = (time.perf_counter() - start_ts) * 1000
            if early or elapsed_ms >= budget_ms - safety_ms:
                stop_reason = "MAX_SECONDS"
                early_break = True
                break
            if processed_total >= backfill_max_per_tick:
                stop_reason = "MAX_PER_TICK"
                break
            if used_fallback and risk_events_repo:
                risk_events_repo.insert(
                    event_type="CONTROL_PLANE_BACKFILL",
                    severity="WARN",
                    message="symbols empty; fallback to all symbols",
                    data={},
                )
            if fetched == 0 and scan_mode == "historical":
                if scanned == 0:
                    stop_reason = "NO_PENDING"
                    break
                # scanned > 0 but all already decided; advance cursor and keep scanning until budget stops us
                if last_ts is not None:
                    continue
                stop_reason = "DRAINED_BATCH"
                break
            if fetched == 0 and scan_mode == "recent":
                if scanned > 0:
                    # Recent has rows but all already processed - try backward
                    force_backward_once = True
                    last_ts_recent = last_ts
                    new_cursor_ts = last_ts
                    scan_mode = "backward"
                    continue
                else:
                    stop_reason = "NO_PENDING"
                    break
            if fetched == 0 and scan_mode == "backward":
                if scanned > 0:
                    # Both recent and backward scanned rows but all processed
                    stop_reason = "FULLY_DRAINED"
                else:
                    stop_reason = "NO_PENDING"
                break
            if fetched == 0 and scanned == 0:
                stop_reason = "NO_PENDING"
                break
            if fetched < limit:
                stop_reason = "DRAINED_BATCH"
                break
            # continue same scan_mode until limits hit
    except Exception as exc:
        error_message = str(exc)
        stop_reason = "ERROR"
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="CONTROL_PLANE_BACKFILL",
                severity="ERROR",
                message="backfill tick error",
                data={"error": str(exc)},
            )

    elapsed_ms = (time.perf_counter() - start_ts) * 1000
    slow = elapsed_ms > budget_ms + safety_ms
    processed_total = min(processed_total, fetched_total)

    per_item_ms = None
    if processed_total > 0 and persist_ms_total >= 0:
        per_item_ms = persist_ms_total / processed_total
        if per_item_ms <= 0:
            per_item_ms = float(safety_ms)
    if per_item_ms is None:
        per_item_ms = persist_ema_ms
    if per_item_ms is None:
        per_item_ms = safety_ms  # default small value to avoid div0
    next_batch = batch_cap
    if adaptive:
        estimated = max(1.0, per_item_ms)
        budget_for_items = max(budget_ms - fetch_ms_total - safety_ms, 1.0)
        target = int(budget_for_items // estimated)
        target = max(1, min(target, DEFAULT_BACKFILL_MAX_PER_TICK))
        next_batch = target
        ema_value = per_item_ms if persist_ema_ms is None else (ema_alpha * per_item_ms + (1 - ema_alpha) * persist_ema_ms)
        # smoothing
        next_batch = max(1, min(int(next_batch), DEFAULT_BACKFILL_MAX_PER_TICK))
        persist_ema_ms = ema_value

    return (
        processed_total,
        fetched_total,
        scanned_total,
        sample_ids,
        stop_reason,
        elapsed_ms,
        error_message,
        slow,
        next_batch,
        fetch_ms_total,
        persist_ms_total,
        early_break,
        stable_fast_ticks,
        new_cursor_ts,
    )


def _hybrid_execution_gates(
    preview: Dict[str, Any],
    *,
    require_entry_triggered: bool,
    require_data_ok: bool,
    require_spread_ok: bool,
) -> tuple[bool, str | None]:
    if not preview:
        return False, "preview_missing"
    if preview.get("setup_type") == "NO_TRADE" or not preview.get("setup_present", False):
        return False, "setup_missing"
    direction = (preview.get("direction") or "").lower()
    if direction not in ("long", "short"):
        return False, "direction_flat"
    if require_entry_triggered and not preview.get("entry_triggered", False):
        return False, "entry_not_triggered"
    if require_data_ok and preview.get("data_quality") != "ok":
        return False, "data_quality_block"
    if require_spread_ok and preview.get("spread_quality") != "ok":
        return False, "spread_quality_block"
    return True, None


def _run_execution_tick_hybrid(
    *,
    client: Any,
    settings: Any,
    execution_service: ExecutionService,
    risk_events_repo: Optional[RiskEventsRepo],
    limit: int = 10,
) -> Dict[str, Any]:
    if client is None:
        return {"executed": 0, "skipped": 0, "errors": 0}

    executed = 0
    skipped = 0
    errors = 0

    trading_enabled = getattr(settings, "trading_enabled", False)
    if isinstance(trading_enabled, property) or not trading_enabled:
        return {"executed": 0, "skipped": 0, "errors": 0}

    hybrid_threshold = float(os.getenv("HYBRID_EXECUTION_THRESHOLD", os.getenv("HYBRID_THRESHOLD", "0.7")))
    require_entry_triggered = os.getenv("HYBRID_REQUIRE_ENTRY_TRIGGERED", "0") != "0"
    params = getattr(settings, "signals_params", None)
    gates = getattr(params, "gates", None)
    require_data_ok = True
    require_spread_ok = True
    if gates:
        require_data_ok = bool(getattr(gates, "require_data_ok", True))
        require_spread_ok = bool(getattr(gates, "require_spread_ok", True))

    try:
        parallel_res = (
            client.table("parallel_decisions")
            .select("id, ts_utc, symbol, hybrid_signal, hybrid_score, signal_preview_id, control_decision_id")
            .order("ts_utc", desc=True)
            .limit(limit * 5)
            .execute()
        )
        parallel_rows = getattr(parallel_res, "data", None) or []
        if not parallel_rows:
            return {"executed": 0, "skipped": 0, "errors": 0}

        candidates: List[Dict[str, Any]] = []
        for row in parallel_rows:
            score = row.get("hybrid_score")
            if score is None:
                continue
            try:
                score_val = float(score)
            except Exception:
                continue
            if abs(score_val) < hybrid_threshold:
                continue
            signal = (row.get("hybrid_signal") or "").upper()
            if signal not in ("LONG", "SHORT"):
                signal = "LONG" if score_val > 0 else "SHORT"
            preview_id = row.get("signal_preview_id")
            if not preview_id:
                continue
            decision_id = row.get("control_decision_id")
            if not decision_id:
                continue
            candidates.append(
                {
                    "decision_id": str(decision_id),
                    "parallel_id": str(row.get("id")),
                    "symbol": row.get("symbol") or "",
                    "signal": signal,
                    "score": score_val,
                    "signal_preview_id": str(preview_id),
                    "ts_utc": row.get("ts_utc"),
                }
            )
            if len(candidates) >= limit:
                break

        if not candidates:
            return {"executed": 0, "skipped": 0, "errors": 0}

        # Load decisions for risk_modifier/flags if available
        decision_ids = [row.get("decision_id") for row in candidates if row.get("decision_id")]
        decisions_map: Dict[str, Dict[str, Any]] = {}
        if decision_ids:
            decisions_res = (
                client.table("control_decisions")
                .select("id, symbol, signal_preview_id, trade_allowed, risk_modifier, flags, commentary, created_at")
                .in_("id", decision_ids)
                .execute()
            )
            decisions_rows = getattr(decisions_res, "data", None) or []
            decisions_map = {str(row.get("id")): row for row in decisions_rows}

        # Load current prices from market_snapshots
        symbols_to_load = list(set(row.get("symbol") for row in candidates if row.get("symbol")))
        if symbols_to_load:
            prices_res = (
                client.table("market_snapshots")
                .select("symbol, close, ts")
                .in_("symbol", symbols_to_load)
                .eq("timeframe", "M15")
                .order("ts", desc=True)
                .execute()
            )
            prices_rows = getattr(prices_res, "data", None) or []
            seen_symbols: set = set()
            for row in prices_rows:
                symbol = row.get("symbol")
                if symbol and symbol not in seen_symbols:
                    price = row.get("close")
                    if price is not None:
                        execution_service.update_price(symbol, float(price))
                        seen_symbols.add(symbol)

        # Load signal previews
        preview_ids = [row.get("signal_preview_id") for row in candidates if row.get("signal_preview_id")]
        previews_map: Dict[str, Dict[str, Any]] = {}
        if preview_ids:
            previews_res = (
                client.table("signal_previews")
                .select(
                    "id, sl_distance_pips, tp_distance_pips, direction, entry_triggered, setup_present, setup_type, data_quality, spread_quality"
                )
                .in_("id", preview_ids)
                .execute()
            )
            previews_rows = getattr(previews_res, "data", None) or []
            previews_map = {str(row.get("id")): row for row in previews_rows}

        # Deduplicate already executed decisions (last 24h)
        from datetime import timedelta
        dedup_cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        executed_check = (
            client.table("risk_events")
            .select("data")
            .in_("event_type", ["EXECUTION_SUBMIT", "EXECUTION_DRY_RUN"])
            .gte("created_at", dedup_cutoff)
            .execute()
        )
        executed_rows = getattr(executed_check, "data", None) or []
        already_executed = set()
        for row in executed_rows:
            data = row.get("data") or {}
            if isinstance(data, dict):
                dec_id = data.get("decision_id")
                if dec_id:
                    already_executed.add(dec_id)

        risk_engine = RiskEngineV1()
        for row in candidates:
            decision_id = row.get("decision_id")
            if not decision_id:
                skipped += 1
                continue
            if decision_id in already_executed:
                skipped += 1
                continue

            preview = previews_map.get(row.get("signal_preview_id"))
            allowed, _ = _hybrid_execution_gates(
                preview,
                require_entry_triggered=require_entry_triggered,
                require_data_ok=require_data_ok,
                require_spread_ok=require_spread_ok,
            )
            if not allowed:
                skipped += 1
                continue

            decision_row = decisions_map.get(decision_id, {})
            risk_modifier = decision_row.get("risk_modifier")
            if risk_modifier is None:
                risk_modifier = 1.0
            flags = decision_row.get("flags") or []
            commentary = decision_row.get("commentary")

            sl_pips = preview.get("sl_distance_pips") if preview else None
            tp_pips = preview.get("tp_distance_pips") if preview else None
            direction = "long" if row.get("signal") == "LONG" else "short"

            try:
                decision_ts = decision_row.get("created_at") or row.get("ts_utc")
                decision = DecisionV1(
                    ts_utc=decision_ts if isinstance(decision_ts, datetime) else datetime.fromisoformat(decision_ts) if decision_ts else datetime.now(timezone.utc),
                    symbol=row.get("symbol") or "",
                    signal_preview_id=_safe_uuid(row.get("signal_preview_id")),
                    trade_allowed=True,
                    risk_modifier=risk_modifier,
                    flags=flags,
                    commentary=commentary,
                )
                decision.id = _safe_uuid(decision_id)

                verdict = risk_engine.evaluate(decision, settings)
                result = execution_service.execute(
                    decision,
                    verdict,
                    settings,
                    stop_loss_pips=float(sl_pips) if sl_pips is not None else None,
                    take_profit_pips=float(tp_pips) if tp_pips is not None else None,
                    direction=direction,
                    final_signal=row.get("signal"),
                )

                if result.executed:
                    executed += 1
                    if os.getenv("CONTROL_PLANE_LOG_LEVEL", "INFO").upper() == "DEBUG":
                        sl_tp_info = f" SL={result.stop_loss_price} TP={result.take_profit_price}" if result.is_bracket else ""
                        print(f"execution_tick strategy=hybrid symbol={decision.symbol} mode={result.mode.value} side={result.side.value if result.side else 'N/A'} qty={result.quantity}{sl_tp_info}")
                else:
                    skipped += 1
            except Exception as exc:
                errors += 1
                if risk_events_repo:
                    risk_events_repo.insert(
                        event_type="EXECUTION_TICK_ERROR",
                        severity="error",
                        message=f"Execution tick error: {exc}",
                        data={"decision_id": decision_id, "error": str(exc)},
                    )

    except Exception as exc:
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="EXECUTION_TICK_ERROR",
                severity="error",
                message=f"Execution tick failed: {exc}",
                data={"error": str(exc)},
            )
        errors += 1

    return {"executed": executed, "skipped": skipped, "errors": errors}


def run_execution_tick(
    *,
    client: Any,
    settings: Any,
    execution_service: ExecutionService,
    risk_events_repo: Optional[RiskEventsRepo],
    limit: int = 10,
) -> Dict[str, Any]:
    """
    Process pending verdicts for execution.
    
    Finds recent verdicts with trade_allowed=True that haven't been executed yet,
    and submits them to the execution service with SL/TP from signal_preview.
    """
    if client is None:
        return {"executed": 0, "skipped": 0, "errors": 0}

    execution_strategy = os.getenv("EXECUTION_STRATEGY") or os.getenv("ACTIVE_STRATEGY", "rules")
    if execution_strategy == "hybrid":
        return _run_execution_tick_hybrid(
            client=client,
            settings=settings,
            execution_service=execution_service,
            risk_events_repo=risk_events_repo,
            limit=limit,
        )

    executed = 0
    skipped = 0
    errors = 0

    try:
        # Get recent verdicts with trade_allowed=True
        verdicts_res = (
            client.table("risk_verdicts")
            .select("id, decision_id, signal_preview_id, trade_allowed, risk_modifier, flags, created_at")
            .eq("trade_allowed", True)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        verdicts_rows = getattr(verdicts_res, "data", None) or []
        
        if not verdicts_rows:
            return {"executed": 0, "skipped": 0, "errors": 0}
        
        # Get corresponding decisions
        decision_ids = [row.get("decision_id") for row in verdicts_rows if row.get("decision_id")]
        if not decision_ids:
            return {"executed": 0, "skipped": 0, "errors": 0}
        
        decisions_res = (
            client.table("control_decisions")
            .select("id, symbol, signal_preview_id, trade_allowed, risk_modifier, flags, commentary, created_at")
            .in_("id", decision_ids)
            .execute()
        )
        decisions_rows = getattr(decisions_res, "data", None) or []
        decisions_map = {row.get("id"): row for row in decisions_rows}
        
        # Load current prices from market_snapshots for all symbols
        symbols_to_load = list(set(row.get("symbol") for row in decisions_rows if row.get("symbol")))
        if symbols_to_load:
            prices_res = (
                client.table("market_snapshots")
                .select("symbol, close, ts")
                .in_("symbol", symbols_to_load)
                .eq("timeframe", "M15")
                .order("ts", desc=True)
                .execute()
            )
            prices_rows = getattr(prices_res, "data", None) or []
            # Take latest price per symbol
            seen_symbols: set = set()
            for row in prices_rows:
                symbol = row.get("symbol")
                if symbol and symbol not in seen_symbols:
                    price = row.get("close")
                    if price is not None:
                        execution_service.update_price(symbol, float(price))
                        seen_symbols.add(symbol)
        
        # Get signal_preview_ids to fetch SL/TP and direction
        signal_preview_ids = [row.get("signal_preview_id") for row in verdicts_rows if row.get("signal_preview_id")]
        signal_previews_map: Dict[str, Dict[str, Any]] = {}
        if signal_preview_ids:
            previews_res = (
                client.table("signal_previews")
                .select("id, sl_distance_pips, tp_distance_pips, direction")
                .in_("id", signal_preview_ids)
                .execute()
            )
            previews_rows = getattr(previews_res, "data", None) or []
            signal_previews_map = {str(row.get("id")): row for row in previews_rows}
        
        # Check which verdicts have already been executed (via risk_events)
        # Only check recent events (last 24 hours) to avoid blocking on old executions
        from datetime import timedelta
        dedup_cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        executed_check = (
            client.table("risk_events")
            .select("data")
            .in_("event_type", ["EXECUTION_SUBMIT", "EXECUTION_DRY_RUN"])
            .gte("created_at", dedup_cutoff)
            .execute()
        )
        executed_rows = getattr(executed_check, "data", None) or []
        already_executed = set()
        for row in executed_rows:
            data = row.get("data") or {}
            if isinstance(data, dict):
                dec_id = data.get("decision_id")
                if dec_id:
                    already_executed.add(dec_id)
        
        for verdict_row in verdicts_rows:
            decision_id = verdict_row.get("decision_id")
            if not decision_id:
                skipped += 1
                continue
            
            if decision_id in already_executed:
                skipped += 1
                continue
            
            decision_row = decisions_map.get(decision_id)
            if not decision_row:
                skipped += 1
                continue
            
            # Get SL/TP and direction from signal_preview
            signal_preview_id = verdict_row.get("signal_preview_id")
            sl_pips = None
            tp_pips = None
            direction = None
            if signal_preview_id:
                preview_data = signal_previews_map.get(str(signal_preview_id), {})
                sl_pips = preview_data.get("sl_distance_pips")
                tp_pips = preview_data.get("tp_distance_pips")
                direction = preview_data.get("direction")
            
            # Build DecisionV1 and RiskVerdictV1 from rows
            try:
                decision = DecisionV1(
                    ts_utc=datetime.fromisoformat(decision_row.get("created_at")) if decision_row.get("created_at") else datetime.now(timezone.utc),
                    symbol=decision_row.get("symbol") or "",
                    signal_preview_id=_safe_uuid(decision_row.get("signal_preview_id")),
                    trade_allowed=decision_row.get("trade_allowed", False),
                    risk_modifier=decision_row.get("risk_modifier", 1.0),
                    flags=decision_row.get("flags") or [],
                    commentary=decision_row.get("commentary"),
                )
                decision.id = decision_id
                
                verdict = RiskVerdictV1(
                    ts_utc=datetime.fromisoformat(verdict_row.get("created_at")) if verdict_row.get("created_at") else datetime.now(timezone.utc),
                    symbol=decision_row.get("symbol") or "",
                    decision_id=_safe_uuid(decision_id),
                    signal_preview_id=_safe_uuid(verdict_row.get("signal_preview_id")),
                    trade_allowed=verdict_row.get("trade_allowed", False),
                    risk_modifier=verdict_row.get("risk_modifier", 1.0),
                    flags=verdict_row.get("flags") or [],
                )
                
                # Execute with SL/TP and direction from signal_preview
                result = execution_service.execute(
                    decision,
                    verdict,
                    settings,
                    stop_loss_pips=float(sl_pips) if sl_pips is not None else None,
                    take_profit_pips=float(tp_pips) if tp_pips is not None else None,
                    direction=direction,
                )
                
                if result.executed:
                    executed += 1
                    if os.getenv("CONTROL_PLANE_LOG_LEVEL", "INFO").upper() == "DEBUG":
                        sl_tp_info = f" SL={result.stop_loss_price} TP={result.take_profit_price}" if result.is_bracket else ""
                        print(f"execution_tick symbol={decision.symbol} mode={result.mode.value} side={result.side.value if result.side else 'N/A'} qty={result.quantity}{sl_tp_info}")
                else:
                    skipped += 1
                    
            except Exception as exc:
                errors += 1
                if risk_events_repo:
                    risk_events_repo.insert(
                        event_type="EXECUTION_TICK_ERROR",
                        severity="error",
                        message=f"Execution tick error: {exc}",
                        data={"decision_id": decision_id, "error": str(exc)},
                    )
    
    except Exception as exc:
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="EXECUTION_TICK_ERROR",
                severity="error",
                message=f"Execution tick failed: {exc}",
                data={"error": str(exc)},
            )
        errors += 1
    
    return {"executed": executed, "skipped": skipped, "errors": errors}


def format_backfill_status(
    *,
    log_level: str,
    processed_total: int,
    fetched_total: int,
    scanned_total: int,
    elapsed_ms: float,
    stop_reason: str,
    sample_ids: Optional[Tuple[str, str, str]] = None,
    error_message: Optional[str] = None,
    slow: bool = False,
    batch_size: int = 0,
    fetch_ms: float = 0.0,
    persist_ms: float = 0.0,
    early_break: bool = False,
    stable_fast_ticks: int = 0,
) -> str:
    parts = [
        "control_plane_backfill",
        f"fetched={fetched_total}",
        f"scanned={scanned_total}",
        f"processed={processed_total}",
        f"batch_cap={batch_size}",
        f"fetch_ms={int(fetch_ms)}",
        f"persist_ms={int(persist_ms)}",
        f"total_ms={int(elapsed_ms)}",
        f"stop_reason={stop_reason}",
        f"early_break={early_break}",
        f"stable_fast_ticks={stable_fast_ticks}",
    ]
    if sample_ids and log_level == "DEBUG":
        parts.append(f"sample_decision={sample_ids[0]}")
    if error_message:
        parts.append(f"error={error_message}")
    if slow:
        parts.append("slow=true")
    return " ".join(parts)


def run_parallel_shadow_tick(
    *,
    client: Any,
    parallel_repo: ParallelDecisionsRepo,
    parallel_runner: ParallelDecisionRunner,
    risk_events_repo: Optional[RiskEventsRepo],
    limit: int = 20,
) -> Dict[str, Any]:
    """
    Phase 4: Full LLM agents parallel decision logging.
    
    Reads recent control_decisions that don't have parallel_decisions yet,
    runs LLM agents (or mocks), creates parallel decision records.
    
    This runs AFTER the main backfill tick, so control_decisions already exist.
    """
    if client is None:
        return {"logged": 0, "skipped": 0, "errors": 0, "llm_calls": 0, "cache_hits": 0}
    
    logged = 0
    skipped = 0
    errors = 0
    llm_calls = 0
    cache_hits = 0
    
    try:
        # Get recent control_decisions
        decisions_res = (
            client.table("control_decisions")
            .select("id, ts_utc, symbol, signal_preview_id, trade_allowed, risk_modifier, flags")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        decisions_rows = getattr(decisions_res, "data", None) or []
        
        if not decisions_rows:
            return {"logged": 0, "skipped": 0, "errors": 0, "llm_calls": 0, "cache_hits": 0}
        
        # Get decision IDs to check which already have parallel records
        decision_ids = [row.get("id") for row in decisions_rows if row.get("id")]
        if not decision_ids:
            return {"logged": 0, "skipped": 0, "errors": 0, "llm_calls": 0, "cache_hits": 0}
        
        # Check existing parallel_decisions
        existing_res = (
            client.table("parallel_decisions")
            .select("control_decision_id")
            .in_("control_decision_id", decision_ids)
            .execute()
        )
        existing_rows = getattr(existing_res, "data", None) or []
        existing_ids = {row.get("control_decision_id") for row in existing_rows if row.get("control_decision_id")}
        
        # Get signal_previews for full data
        signal_preview_ids = [row.get("signal_preview_id") for row in decisions_rows if row.get("signal_preview_id")]
        previews_map: Dict[str, SignalPreviewV1] = {}
        if signal_preview_ids:
            previews_res = (
                client.table("signal_previews")
                .select("id, ts_utc, symbol, timeframe_trigger, setup_type, direction, setup_present, entry_triggered, confidence, rr, data_quality, spread_quality, flags, sl_distance_pips, tp_distance_pips")
                .in_("id", signal_preview_ids)
                .execute()
            )
            previews_rows = getattr(previews_res, "data", None) or []
            for row in previews_rows:
                try:
                    previews_map[str(row.get("id"))] = _preview_from_row(row)
                except Exception:
                    pass
        
        # Create parallel decisions for missing ones
        parallel_records = []
        for dec_row in decisions_rows:
            dec_id = dec_row.get("id")
            if not dec_id or dec_id in existing_ids:
                skipped += 1
                continue
            
            # Get preview
            sp_id = dec_row.get("signal_preview_id")
            preview = previews_map.get(str(sp_id)) if sp_id else None
            
            if not preview:
                # Create minimal preview from decision row
                try:
                    ts_raw = dec_row.get("ts_utc")
                    if isinstance(ts_raw, str):
                        ts_utc = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    else:
                        ts_utc = ts_raw or datetime.now(timezone.utc)
                    
                    preview = SignalPreviewV1(
                        ts_utc=ts_utc,
                        symbol=dec_row.get("symbol") or "",
                        timeframe_trigger="M15",
                        setup_type=SetupType.NO_TRADE,
                        direction=Direction.FLAT,
                        setup_present=False,
                        entry_triggered=False,
                        confidence=Confidence.LOW,
                        rr=0.0,
                        data_quality=DataQuality.OK,
                        spread_quality=SpreadQuality.OK,
                        flags=dec_row.get("flags") or [],
                    )
                except Exception:
                    skipped += 1
                    continue
            
            # Build decision for runner
            try:
                decision = DecisionV1(
                    ts_utc=preview.ts_utc,
                    symbol=preview.symbol,
                    signal_preview_id=_safe_uuid(sp_id),
                    trade_allowed=dec_row.get("trade_allowed", False),
                    risk_modifier=dec_row.get("risk_modifier", 1.0),
                    flags=dec_row.get("flags") or [],
                )
                
                # Run parallel runner with LLM agents
                parallel_dec = parallel_runner.run(
                    preview=preview,
                    preview_id=_safe_uuid(sp_id) or uuid4(),
                    decision=decision,
                    decision_id=dec_id,
                )
                
                parallel_records.append(parallel_dec)
                
            except Exception as exc:
                errors += 1
                if risk_events_repo:
                    risk_events_repo.insert(
                        event_type="PARALLEL_LLM_ERROR",
                        severity="error",
                        message=f"Failed to run parallel agents: {exc}",
                        data={"decision_id": dec_id, "error": str(exc)},
                    )
        
        # Get stats from runner
        runner_stats = parallel_runner.get_stats()
        llm_calls = runner_stats.get("llm_calls", 0)
        cache_hits = runner_stats.get("cache_hits", 0)
        
        # Bulk insert
        if parallel_records:
            try:
                parallel_repo.insert_bulk(parallel_records)
                logged = len(parallel_records)
            except Exception as exc:
                errors += len(parallel_records)
                if risk_events_repo:
                    risk_events_repo.insert(
                        event_type="PARALLEL_LLM_BULK_ERROR",
                        severity="error",
                        message=f"Bulk insert failed: {exc}",
                        data={"count": len(parallel_records), "error": str(exc)},
                    )
    
    except Exception as exc:
        errors += 1
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="PARALLEL_LLM_TICK_ERROR",
                severity="error",
                message=f"LLM tick failed: {exc}",
                data={"error": str(exc)},
            )
    
    return {
        "logged": logged,
        "skipped": skipped,
        "errors": errors,
        "llm_calls": llm_calls,
        "cache_hits": cache_hits,
    }


def _create_openai_client():
    """Create OpenAI client if API key is available."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key)
    except ImportError:
        print("Warning: openai package not installed, LLM agents will run in mock mode")
        return None
    except Exception as e:
        print(f"Warning: Failed to create OpenAI client: {e}")
        return None


# Phase 6: Signal generation tick - generates signal_previews from market data
DEFAULT_SIGNAL_GEN_INTERVAL = 60  # Generate signals every 60 seconds
DEFAULT_SIGNAL_GEN_TIMEFRAMES = ["M15", "H1", "H4"]


def run_signal_generation_tick(
    *,
    market_data_service: Optional[MarketDataService],
    signal_engine: Optional[SignalEngineV1],
    signal_previews_repo: SignalPreviewsRepo,
    risk_events_repo: Optional[RiskEventsRepo],
    settings: Any,
) -> Dict[str, Any]:
    """
    Phase 6: Signal generation tick.
    
    Fetches market data from IB Gateway (or SeedFetcher in mock mode),
    generates signal_previews via SignalEngineV1, and persists to DB.
    
    This is the SOURCE of signal_previews that the control plane processes.
    """
    if market_data_service is None or signal_engine is None:
        return {"generated": 0, "warmup_ready": False, "errors": 0, "mode": "disabled"}
    
    generated = 0
    errors = 0
    warmup_ready = False
    
    try:
        # Fetch market data and check warmup
        end_dt_utc = datetime.now(timezone.utc)
        warmup_ready, snapshots = market_data_service.process(end_dt_utc)
        if market_data_service.is_ib_untrusted():
            error_code, _ = market_data_service.last_ib_error()
            return {
                "generated": 0,
                "warmup_ready": False,
                "errors": 1,
                "mode": "ib_untrusted",
                "ib_error_code": error_code,
            }
        
        if not warmup_ready:
            return {
                "generated": 0,
                "warmup_ready": False,
                "errors": 0,
                "mode": "warmup",
                "snapshots": len(snapshots),
            }
        
        if not snapshots:
            return {
                "generated": 0,
                "warmup_ready": warmup_ready,
                "errors": 0,
                "mode": "no_data",
            }
        
        # Generate signal previews
        previews = signal_engine.compute_previews(snapshots, warmup_ready=warmup_ready)
        
        # Apply SL/TP from settings
        default_sl = getattr(settings, "default_sl_pips", 20.0)
        default_tp = getattr(settings, "default_tp_pips", 40.0)
        
        for preview in previews:
            try:
                # Set SL/TP distances
                preview.sl_distance_pips = default_sl
                preview.tp_distance_pips = default_tp
                
                # Insert into DB
                signal_previews_repo.insert_preview(preview)
                generated += 1
            except Exception as exc:
                # Duplicate key is acceptable (idempotent)
                msg = str(exc).lower()
                if "duplicate key" in msg or "unique constraint" in msg:
                    pass  # Already exists, skip
                else:
                    errors += 1
                    # Always log insert errors to stdout
                    print(f"signal_gen_insert_error symbol={preview.symbol} tf={getattr(preview, 'timeframe_trigger', 'N/A')} error={exc}")
                    if risk_events_repo:
                        risk_events_repo.insert(
                            event_type="SIGNAL_GEN_INSERT_ERROR",
                            severity="error",
                            message=f"Failed to insert signal preview: {exc}",
                            data={"symbol": preview.symbol, "error": str(exc)},
                        )
    
    except Exception as exc:
        errors += 1
        # Always log tick errors to stdout for visibility
        print(f"signal_gen_tick_error error={exc}")
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="SIGNAL_GEN_TICK_ERROR",
                severity="error",
                message=f"Signal generation tick failed: {exc}",
                data={"error": str(exc)},
            )
    
    return {
        "generated": generated,
        "warmup_ready": warmup_ready,
        "errors": errors,
        "mode": "active",
    }


def _create_ib_connection(host: str, port: int, client_id: int, retries: int = 3, retry_delay: float = 5.0):
    """Create IB Gateway connection with retry logic and clientId collision handling."""
    from ib_insync import IB
    from app.broker.ib_utils import ib_probe_ready, IBGatewayNotReady
    
    MAX_CLIENTID_RETRIES = 10
    
    for attempt in range(1, retries + 1):
        try:
            print(f"IB Gateway connection attempt {attempt}/{retries}: {host}:{port} clientId={client_id}")
            ib = IB()
            ib.RequestTimeout = 60
            ib.connect(host, port, clientId=client_id, timeout=60)
            ib_probe_ready(ib, timeout_s=5.0)
            print(f"IB Gateway connected successfully on attempt {attempt}")
            return ib
        except Exception as e:
            error_str = str(e).lower()
            # Check for Error 326 (clientId collision)
            if "326" in str(e) or "client id" in error_str or "already in use" in error_str:
                print(f"clientId {client_id} collision, trying next...")
                # Try incrementing clientId
                for cid_attempt in range(MAX_CLIENTID_RETRIES):
                    new_client_id = client_id + cid_attempt + 1
                    try:
                        ib = IB()
                        ib.RequestTimeout = 60
                        ib.connect(host, port, clientId=new_client_id, timeout=60)
                        ib_probe_ready(ib, timeout_s=5.0)
                        print(f"IB Gateway connected with clientId={new_client_id}")
                        return ib
                    except Exception as e2:
                        if "326" in str(e2) or "client id" in str(e2).lower():
                            continue
                        if isinstance(e2, IBGatewayNotReady):
                            time.sleep(retry_delay)
                            continue
                        # Other error - break inner loop
                        break
            
            print(f"IB Gateway connection attempt {attempt} failed: {type(e).__name__}: {e}")
            if attempt < retries:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
    
    print(f"Warning: Failed to connect to IB Gateway after {retries} attempts")
    return None


def _fetch_equity_from_ib(ib) -> Optional[float]:
    """
    Fetch account equity (NetLiquidation) from IB Gateway.
    
    P0-D: Use real equity for position sizing and exposure checks.
    Returns None if unable to fetch.
    """
    if ib is None or not ib.isConnected():
        return None
    
    try:
        account_values = ib.accountSummary()
        for av in account_values:
            if av.tag == 'NetLiquidation':
                equity = float(av.value)
                print(f"IB equity fetched: {equity} {av.currency}")
                return equity
        print("Warning: NetLiquidation not found in account summary")
        return None
    except Exception as e:
        print(f"Warning: Failed to fetch equity from IB: {e}")
        return None


def main():
    owner_user_id_raw = os.getenv("BOT_OWNER_USER_ID")
    if not owner_user_id_raw:
        print("BOT_OWNER_USER_ID is not set; exiting", file=sys.stderr)
        sys.exit(1)
    try:
        owner_uuid = UUID(owner_user_id_raw)
    except Exception:
        print("BOT_OWNER_USER_ID is invalid UUID; exiting", file=sys.stderr)
        sys.exit(1)

    try:
        poll_seconds = float(os.getenv("BOT_SETTINGS_POLL_SECONDS", "10"))
    except Exception:
        poll_seconds = 10.0

    db = SupabaseDB()
    print("Supabase ping:", db.ping())
    bot_settings_repo = BotSettingsRepo(db)
    risk_events_repo = RiskEventsRepo(db)
    signal_previews_repo = SignalPreviewsRepo(db)
    decisions_repo = DecisionsRepo(db)
    risk_verdicts_repo = RiskVerdictsRepo(db)
    agents_aggregator = AgentsAggregator(reports_repo=None)
    risk_engine = RiskEngineV1()

    # Phase 0: Parallel decisions shadow mode
    parallel_decisions_repo = ParallelDecisionsRepo(db)
    parallel_shadow_enabled = os.getenv("PARALLEL_SHADOW_ENABLED", "1") != "0"
    parallel_shadow_limit = int(os.getenv("PARALLEL_SHADOW_LIMIT", "20"))
    last_parallel_log: Optional[str] = None

    # Phase 1: Data sources
    # DXY: Real Yahoo Finance API integration (done)
    # TODO: EconomicCalendarFetcher - integrate real API (Investing.com or ForexFactory)
    # TODO: COTReportsFetcher - integrate CFTC data
    data_sources_enabled = os.getenv("DATA_SOURCES_ENABLED", "1") != "0"
    data_sources_mock = os.getenv("DATA_SOURCES_MOCK", "0") == "1"  # Real data by default
    economic_calendar = EconomicCalendarFetcher(mock_mode=True)  # TODO: real API
    cot_reports = COTReportsFetcher(mock_mode=data_sources_mock)  # Real CFTC data
    dxy_fetcher = DXYFetcher(mock_mode=data_sources_mock)  # Real Yahoo Finance API
    source_health_monitor = SourceHealthMonitor()
    data_sources_fetch_interval = int(os.getenv("DATA_SOURCES_FETCH_INTERVAL", "300"))  # 5 min default
    last_data_sources_fetch = 0.0
    last_data_sources_log: Optional[str] = None

    # Phase 2: Safety gates (initialized in parallel_runner)
    
    # Phase 3-5: LLM Agents with full integration
    llm_enabled = os.getenv("LLM_AGENTS_ENABLED", "0") == "1"
    active_strategy = os.getenv("ACTIVE_STRATEGY", "rules")  # rules, gpt, hybrid
    
    # Create OpenAI client (returns None if no API key)
    llm_client = _create_openai_client() if llm_enabled else None
    
    # Create parallel runner with all components
    parallel_runner = create_parallel_runner(
        llm_enabled=llm_enabled,
        active_strategy=active_strategy,
        economic_calendar=economic_calendar,
        cot_reports=cot_reports,
        dxy_fetcher=dxy_fetcher,
        llm_client=llm_client,
    )
    
    # Log initialization status
    if parallel_shadow_enabled:
        mode_str = "LLM" if llm_enabled else "MOCK"
        client_str = "OpenAI" if llm_client else "None"
        print(f"Phase 5: Parallel agents ENABLED mode={mode_str} strategy={active_strategy} client={client_str}")
    
    if data_sources_enabled:
        mode_str = "MOCK" if data_sources_mock else "REAL"
        print(f"Phase 1: Data sources ENABLED mode={mode_str}")

    # Phase 6: Signal generation from market data
    signal_gen_enabled = os.getenv("SIGNAL_GEN_ENABLED", "1") != "0"
    signal_gen_mock = os.getenv("SIGNAL_GEN_MOCK", "0") == "1"
    signal_gen_interval = int(os.getenv("SIGNAL_GEN_INTERVAL", str(DEFAULT_SIGNAL_GEN_INTERVAL)))
    last_signal_gen_tick = 0.0
    last_signal_gen_log: Optional[str] = None
    
    market_data_service: Optional[MarketDataService] = None
    signal_engine_instance: Optional[SignalEngineV1] = None
    snapshots_repo = SnapshotsRepo(db)
    
    if signal_gen_enabled:
        # Get symbols from settings or use defaults
        initial_settings = bot_settings_repo.get(str(owner_uuid))
        symbols = getattr(initial_settings, "symbols", None) or DEFAULT_SYMBOLS
        warmup_bars = getattr(initial_settings, "warmup_bars_min", 100)
        signals_params = getattr(initial_settings, "signals_params", None)
        
        # Create fetcher (mock or real IB Gateway)
        if signal_gen_mock:
            fetcher = SeedFetcher(seed=42)  # Deterministic mock data
        else:
            ib_host = os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
            ib_port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
            ib_client_id = int(os.getenv(
                "IB_CLIENT_ID_MARKETDATA",
                os.getenv("IB_CLIENT_ID", "152")
            ))
            from app.market_data.ibkr_fetcher import IBKRFetcher
            fetcher = IBKRFetcher(
                host=ib_host,
                port=ib_port,
                client_id=ib_client_id,
            )
        
        # Initialize MarketDataService
        market_data_service = MarketDataService(
            symbols=symbols,
            timeframes=DEFAULT_SIGNAL_GEN_TIMEFRAMES,
            warmup_bars_min=warmup_bars,
            fetcher=fetcher,
            snapshots_repo=snapshots_repo,
            risk_events_repo=risk_events_repo,
        )
        
        # Initialize SignalEngine
        if signals_params:
            signal_engine_instance = SignalEngineV1(params=signals_params)
        else:
            from app.models.signals_params import SignalsParams
            signal_engine_instance = SignalEngineV1(params=SignalsParams())
        
        mode_str = "MOCK" if signal_gen_mock else "IBKR"
        print(f"Phase 6: Signal generation ENABLED mode={mode_str} symbols={len(symbols)} interval={signal_gen_interval}s")
    else:
        print("Phase 6: Signal generation DISABLED")

    # Phase 8: Price direction forecast engine (read-only)
    forecast_enabled = os.getenv("FORECAST_ENABLED", "1") != "0"
    forecast_interval = int(os.getenv("FORECAST_INTERVAL", "900"))  # 15 min: aligned with M15 bar close
    last_forecast_tick = 0.0
    forecast_engine = None
    forecast_repo = None
    # Bar-aligned forecast trigger: track last M15 bar timestamp to detect new bars
    _last_m15_bar_ts: Optional[str] = None
    # Change detection: track previous quality filter results
    _prev_passed_set: set = set()

    forecast_verifier = None
    forecast_verify_interval = int(os.getenv("FORECAST_VERIFY_INTERVAL", "60"))  # verify every 60s
    forecast_verify_batch = int(os.getenv("FORECAST_VERIFY_BATCH", "500"))  # 500 per tick
    last_forecast_verify_tick = 0.0
    if forecast_enabled and signal_gen_enabled:
        try:
            from app.forecast.engine import ForecastEngine
            from app.storage.forecast_repo import ForecastRepo
            from app.forecast.verifier import ForecastVerifier
            forecast_engine = ForecastEngine()
            forecast_repo = ForecastRepo(db)
            forecast_verifier = ForecastVerifier(db)
            print(f"Phase 8: Forecast engine ENABLED interval={forecast_interval}s verify_interval={forecast_verify_interval}s verify_batch={forecast_verify_batch}")
        except Exception as exc:
            print(f"Phase 8: Forecast engine FAILED to init: {exc}")
            forecast_enabled = False
    else:
        print(f"Phase 8: Forecast engine DISABLED (forecast_enabled={forecast_enabled} signal_gen={signal_gen_enabled})")

    # Signal Lifecycle Manager — dedup + cooldown for Alt2 signals
    signal_lifecycle = SignalLifecycleManager(supabase_client=db.client)
    if signal_lifecycle.enabled:
        _restored = signal_lifecycle.restore_from_supabase()
        print(f"SignalLifecycleManager: enabled blacklist_hours={sorted(signal_lifecycle.blacklist_hours)}")
        if _restored:
            print(f"SignalLifecycleManager: restored symbols={_restored}")
        # Register with dashboard for /api/signal-lifecycle endpoint
        from app.dashboard import set_signal_lifecycle_manager
        set_signal_lifecycle_manager(signal_lifecycle)
        # Persist initial state so dashboard can read it immediately
        signal_lifecycle.persist_to_supabase(force=True)

    # Telegram notifications for binary signals
    tg_notifier = TelegramNotifier()
    # If TG_ALT_ONLY=1 (default), send only Alt2/Alt3 alerts.
    tg_alt_only = os.getenv("TG_ALT_ONLY", "1") != "0"
    tg_notify_lost = (os.getenv("TG_NOTIFY_LOST_SIGNALS", "1") != "0") and not tg_alt_only
    # Track trading hours transitions
    _prev_in_trading_hours: Optional[bool] = None
    # Trading hours for notifications (same as quality filter)
    _tg_trading_hours = {8, 9, 10, 11, 17, 18, 19}

    # Initialize ExecutionService + BrokerStateService
    owner_uuid_str = str(owner_uuid)
    trades_history_repo = TradesHistoryRepo(db)
    ib_host = os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
    ib_port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
    ib_client_id = int(os.getenv("IB_CLIENT_ID_MAIN", os.getenv("IB_CLIENT_ID", "151")))
    broker_ib = _create_ib_connection(ib_host, ib_port, ib_client_id, retries=1)

    broker_state_service = BrokerStateService(
        ib=broker_ib,
        trades_history_repo=trades_history_repo,
        risk_events_repo=risk_events_repo,
        bot_settings_repo=bot_settings_repo,
        owner_user_id=owner_uuid_str,
    )
    execution_service = ExecutionService(
        risk_events_repo=risk_events_repo,
        trades_history_repo=trades_history_repo,
        broker_state_service=None,
        bot_settings_repo=bot_settings_repo,
        owner_user_id=owner_uuid_str,
    )
    
    # P0-D: Get equity from IB Gateway if connected, else fallback to env
    default_equity = float(os.getenv("DEFAULT_EQUITY", str(DEFAULT_EQUITY)))
    ib_conn_for_equity = None
    if not signal_gen_mock:
        ib_host = os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
        ib_port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
        ib_client_id = int(os.getenv("IB_CLIENT_ID_EQUITY", "102"))
        ib_conn_for_equity = _create_ib_connection(ib_host, ib_port, ib_client_id, retries=1)
    
    equity_from_ib = _fetch_equity_from_ib(ib_conn_for_equity)
    if equity_from_ib is not None:
        execution_service.update_equity(equity_from_ib)
        print(f"ExecutionService equity from IB: {equity_from_ib}")
        # Save equity to Supabase for frontend access
        try:
            risk_events_repo.insert(
                event_type="EQUITY_UPDATE",
                severity="info",
                message=f"Equity updated from IB Gateway: {equity_from_ib}",
                data={"equity": equity_from_ib, "source": "ibkr"},
            )
        except Exception as e:
            print(f"Warning: Failed to save equity to Supabase: {e}")
    else:
        execution_service.update_equity(default_equity)
        print(f"ExecutionService equity from env: {default_equity}")
        # Save default equity to Supabase
        try:
            risk_events_repo.insert(
                event_type="EQUITY_UPDATE",
                severity="info",
                message=f"Equity set to default: {default_equity}",
                data={"equity": default_equity, "source": "env"},
            )
        except Exception as e:
            print(f"Warning: Failed to save equity to Supabase: {e}")
    
    # Disconnect equity connection if separate
    if ib_conn_for_equity is not None:
        try:
            ib_conn_for_equity.disconnect()
        except Exception:
            pass
    
    execution_mode = execution_service._determine_mode(bot_settings_repo.get(owner_uuid_str))
    current_equity = execution_service.get_equity()
    print(f"ExecutionService initialized mode={execution_mode.value} equity={current_equity}")

    # ClientId audit log - show all configured IDs on startup
    _audit_main = os.getenv("IB_CLIENT_ID_MAIN", os.getenv("IB_CLIENT_ID", "151"))
    _audit_marketdata = os.getenv("IB_CLIENT_ID_MARKETDATA", os.getenv("IB_CLIENT_ID", "11"))
    _audit_execution = os.getenv("IBKR_EXECUTION_CLIENT_ID", "153")
    _audit_equity = os.getenv("IB_CLIENT_ID_EQUITY", "154")
    print(f"ibkr_client_ids main={_audit_main} marketdata={_audit_marketdata} execution={_audit_execution} equity={_audit_equity}")

    # Broker state sync - broker is source of truth
    broker_sync_interval = int(os.getenv("BROKER_SYNC_INTERVAL", str(DEFAULT_POSITION_SYNC_INTERVAL)))
    if broker_sync_interval < DEFAULT_POSITION_SYNC_INTERVAL:
        broker_sync_interval = DEFAULT_POSITION_SYNC_INTERVAL
    last_broker_sync_tick = 0.0
    last_broker_sync_log: Optional[str] = None

    print(f"BrokerStateService sync interval={broker_sync_interval}s")
    try:
        if broker_state_service.wait_for_connection(timeout=30):
            startup_sync = broker_state_service.sync_with_db()
            startup_log = (
                f"broker_sync_startup opened={len(startup_sync.positions_opened)} "
                f"closed={len(startup_sync.positions_closed)} mismatches={len(startup_sync.mismatches)}"
            )
            print(startup_log)
            last_broker_sync_log = startup_log
            if startup_sync.errors and risk_events_repo:
                for err in startup_sync.errors:
                    risk_events_repo.insert(
                        event_type="BROKER_SYNC_ERROR",
                        severity="error",
                        message=f"Startup broker sync failed: {err}",
                        data={"error": err},
                    )
        else:
            if risk_events_repo:
                risk_events_repo.insert(
                    event_type="BROKER_SYNC_ERROR",
                    severity="error",
                    message="Broker connection timeout during startup sync",
                    data={},
                )
    except Exception as exc:
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="BROKER_SYNC_ERROR",
                severity="error",
                message=f"Startup broker sync failed: {exc}",
                data={"error": str(exc)},
            )

    batch_size = int(os.getenv("CONTROL_PLANE_BACKFILL_BATCH_SIZE", str(DEFAULT_BACKFILL_BATCH)))
    backfill_max_per_tick = int(os.getenv("CONTROL_PLANE_BACKFILL_MAX_PER_TICK", str(DEFAULT_BACKFILL_MAX_PER_TICK)))
    backfill_max_seconds = float(os.getenv("CONTROL_PLANE_BACKFILL_MAX_SECONDS", str(DEFAULT_BACKFILL_MAX_SECONDS)))
    safety_ms = int(os.getenv("CONTROL_PLANE_BACKFILL_SAFETY_MS", str(DEFAULT_BACKFILL_SAFETY_MS)))
    ema_alpha = float(os.getenv("CONTROL_PLANE_BACKFILL_EMA_ALPHA", str(DEFAULT_BACKFILL_EMA_ALPHA)))
    backfill_enabled = os.getenv("CONTROL_PLANE_BACKFILL_ENABLED", "1") != "0"
    adaptive = os.getenv("CONTROL_PLANE_BACKFILL_ADAPTIVE", "1") != "0"

    persist_ema_ms: Optional[float] = None
    stable_fast_ticks = 0
    idle_ticks = 0
    idle_backoff_enabled = os.getenv("CONTROL_PLANE_IDLE_BACKOFF_ENABLED", "1") != "0"
    idle_backoff_max = float(os.getenv("CONTROL_PLANE_IDLE_BACKOFF_MAX_SECONDS", str(DEFAULT_IDLE_BACKOFF_MAX)))
    idle_backoff_base = float(os.getenv("CONTROL_PLANE_IDLE_BACKOFF_BASE", str(DEFAULT_IDLE_BACKOFF_BASE)))
    execution_enabled = os.getenv("EXECUTION_ENABLED") == "1"
    execution_tick_limit = int(os.getenv("EXECUTION_TICK_LIMIT", "10"))
    last_stop_reason = None
    last_execution_log: Optional[str] = None
    historical_cursor_ts: Optional[str] = None

    last_logged_symbols = None
    last_logged_found = None
    
    # Stats logging interval
    stats_log_interval = int(os.getenv("STATS_LOG_INTERVAL_TICKS", "10"))
    tick_count = 0
    ib_fail_safe = IBFailSafeState()
    ib_fail_safe_threshold_s = int(os.getenv("IB_FAILSAFE_UNTRUSTED_SECONDS", "300"))
    ib_recovery_cycles = int(os.getenv("IB_FAILSAFE_RECOVERY_CYCLES", "3"))
    ib_fail_safe_warn_window_s = int(os.getenv("IB_FAILSAFE_WARN_WINDOW_S", "60"))
    last_ib_warn_ts = 0.0

    while True:
        tick_count += 1
        settings = bot_settings_repo.get(owner_uuid_str)
        symbols_empty_flag = False
        symbols_list = getattr(settings, "symbols", None)
        if isinstance(symbols_list, list) and len(symbols_list) == 0:
            symbols_empty_flag = True
        if symbols_list != last_logged_symbols or bot_settings_repo.last_found_row != last_logged_found:
            print(
                f"bot_settings found_row={bot_settings_repo.last_found_row} symbols_count={len(symbols_list or [])}",
                file=sys.stderr,
            )
            last_logged_symbols = symbols_list
            last_logged_found = bot_settings_repo.last_found_row
        
        # Phase 6: Signal generation tick (generates signal_previews)
        if signal_gen_enabled:
            now_ts = time.time()
            if now_ts - last_signal_gen_tick >= signal_gen_interval:
                signal_result = run_signal_generation_tick(
                    market_data_service=market_data_service,
                    signal_engine=signal_engine_instance,
                    signal_previews_repo=signal_previews_repo,
                    risk_events_repo=risk_events_repo,
                    settings=settings,
                )
                signal_gen_log = (
                    f"signal_gen generated={signal_result['generated']} "
                    f"warmup_ready={signal_result['warmup_ready']} "
                    f"errors={signal_result['errors']} "
                    f"mode={signal_result['mode']}"
                )
                if signal_gen_log != last_signal_gen_log or signal_result["generated"] > 0:
                    print(signal_gen_log)
                    last_signal_gen_log = signal_gen_log
                last_signal_gen_tick = now_ts
                if not signal_gen_mock and market_data_service:
                    now_dt = datetime.now(timezone.utc)
                    if signal_result.get("mode") == "ib_untrusted":
                        reason = signal_result.get("ib_error_code") or "ib_untrusted"
                        changed = ib_fail_safe.mark_untrusted(reason, now_dt, ib_fail_safe_threshold_s)
                        if not ib_fail_safe.disabled:
                            now_monotonic = time.monotonic()
                            if now_monotonic - last_ib_warn_ts >= ib_fail_safe_warn_window_s:
                                last_ib_warn_ts = now_monotonic
                                if risk_events_repo:
                                    risk_events_repo.insert(
                                        event_type="IB_UNTRUSTED_WARN",
                                        severity="warn",
                                        message="IB gateway untrusted (short outage)",
                                        data={"reason": reason},
                                    )
                        if changed and risk_events_repo:
                            risk_events_repo.insert(
                                event_type="IB_UNTRUSTED",
                                severity="critical",
                                message="IB gateway unavailable; trading blocked",
                                data={"reason": reason},
                            )
                    else:
                        changed = ib_fail_safe.mark_ok(now_dt, ib_recovery_cycles)
                        if changed and risk_events_repo:
                            risk_events_repo.insert(
                                event_type="IB_RECOVERED",
                                severity="info",
                                message="IB gateway recovered; trading unblocked",
                                data={},
                            )
                    execution_service.set_fail_safe(ib_fail_safe.disabled, reason=ib_fail_safe.last_reason)

        # Phase 8: Price direction forecast — triggered by NEW M15 bar close
        # Instead of a fixed timer, detect when M15 bars cache gets a new bar.
        # This eliminates 0-14 min random latency between bar close and forecast.
        # Fallback: still run on timer if bar detection fails.
        if signal_gen_enabled and forecast_enabled and market_data_service:
            now_ts = time.time()
            # Detect new M15 bar by checking latest bar timestamp for any symbol
            new_bar_detected = False
            try:
                for _sym in (getattr(settings, "symbols", None) or [])[:1]:  # check first symbol
                    _bars = market_data_service._bars_cache.get((_sym, "M15"), [])
                    if _bars:
                        _latest_ts = str(getattr(_bars[-1], "date", None) or getattr(_bars[-1], "time", ""))
                        if _latest_ts and _latest_ts != _last_m15_bar_ts:
                            if _last_m15_bar_ts is not None:  # skip first iteration
                                new_bar_detected = True
                            _last_m15_bar_ts = _latest_ts
            except Exception:
                pass
            # Trigger: new bar OR timer fallback
            timer_trigger = (now_ts - last_forecast_tick >= forecast_interval)
            if new_bar_detected or timer_trigger:
                try:
                    active_symbols = getattr(settings, "symbols", None) or []
                    if active_symbols:
                        forecasts = forecast_engine.compute_all(
                            market_data_service=market_data_service,
                            symbols=active_symbols,
                        )
                        # Signal Lifecycle: filter alt2/alt3 signals (dedup + cooldown + blacklist)
                        # Variant policy:
                        # - Alt2 and Alt3 are tracked independently for A/B comparison.
                        # - Lifecycle keys are variant-aware: "SYMBOL#alt2" / "SYMBOL#alt3".
                        if forecasts and signal_lifecycle.enabled:
                            _lc_now = datetime.now(timezone.utc)
                            _lc_blocked = 0
                            for fc in forecasts:
                                _alt2_dir = getattr(fc, "h30_alt2_direction", None)
                                _alt3_dir = getattr(fc, "h30_alt3_direction", None)
                                if _alt2_dir:
                                    _key2 = f"{fc.symbol}#alt2"
                                    _ok2, _reason2 = signal_lifecycle.can_signal(_key2, _alt2_dir, _lc_now)
                                    if not _ok2:
                                        setattr(fc, "h30_alt2_direction", None)
                                        _lc_blocked += 1
                                    else:
                                        signal_lifecycle.record_signal(_key2, _alt2_dir, _lc_now)

                                if _alt3_dir:
                                    _key3 = f"{fc.symbol}#alt3"
                                    _ok3, _reason3 = signal_lifecycle.can_signal(_key3, _alt3_dir, _lc_now)
                                    if not _ok3:
                                        setattr(fc, "h30_alt3_direction", None)
                                        _lc_blocked += 1
                                    else:
                                        signal_lifecycle.record_signal(_key3, _alt3_dir, _lc_now)
                            if _lc_blocked > 0:
                                print(f"lifecycle_filter blocked={_lc_blocked}")

                        if forecasts and forecast_repo:
                            fc_result = forecast_repo.insert_batch(forecasts)
                            fc_count = fc_result.get("count", 0)
                            aligned = sum(1 for f in forecasts if f.all_aligned())
                            trigger_type = "bar" if new_bar_detected else "timer"
                            if fc_count > 0:
                                print(f"forecast generated={fc_count} aligned={aligned}/{len(forecasts)} trigger={trigger_type}")
                            # Update forecast gate cache
                            if execution_service and execution_service.forecast_gate:
                                execution_service.forecast_gate.update_forecasts_batch(forecasts)

                            # Alt2/Alt3 Telegram notifications — only when forecasts actually written to DB
                            if fc_count > 0:
                                try:
                                    # Get symbols that were actually inserted (not deduped)
                                    _inserted_syms = set()
                                    _fc_data = fc_result.get("data")
                                    if _fc_data:
                                        for _row in _fc_data:
                                            _s = _row.get("symbol") if isinstance(_row, dict) else None
                                            if _s:
                                                _inserted_syms.add(_s)
                                    for fc in forecasts:
                                        # Only notify for freshly inserted forecasts
                                        if _inserted_syms and fc.symbol not in _inserted_syms:
                                            continue
                                        for strat, attr in [("alt2", "h30_alt2_direction"), ("alt3", "h30_alt3_direction")]:
                                            alt_dir = getattr(fc, attr, None)
                                            if alt_dir:
                                                import json as _json
                                                _votes = None
                                                if fc.h30_votes_json:
                                                    try:
                                                        _votes = _json.loads(fc.h30_votes_json) if isinstance(fc.h30_votes_json, str) else fc.h30_votes_json
                                                    except Exception:
                                                        pass
                                                tg_notifier.notify_alt_signal(
                                                    symbol=fc.symbol,
                                                    strategy=strat,
                                                    direction=alt_dir,
                                                    base_price=fc.base_price or 0,
                                                    adx_value=fc.adx_value,
                                                    votes=_votes,
                                                    h4_direction=fc.mtf_h4_direction,
                                                )
                                except Exception as alt_tg_exc:
                                    print(f"tg_alt_notify_error: {alt_tg_exc}")

                            # Quality filter screening log with change detection
                            gate = execution_service.forecast_gate if execution_service else None
                            if gate and getattr(gate, '_quality_filter_enabled', False):
                                current_hour = datetime.now(timezone.utc).hour
                                passed_list = []
                                all_pairs_info = []
                                for fc in forecasts:
                                    h30 = fc.horizon(30)
                                    if not h30 or h30.direction.value == "neutral":
                                        continue
                                    conf = h30.confidence.value
                                    aligned_count = h30.indicators_aligned
                                    total = h30.indicators_total
                                    direction = h30.direction.value.upper()
                                    strength = h30.strength

                                    reasons = []
                                    if conf != gate._required_confidence:
                                        reasons.append(f"conf={conf}")
                                    if aligned_count < gate._min_aligned:
                                        reasons.append(f"aligned={aligned_count}<{gate._min_aligned}")
                                    if gate._hours_filter_enabled and current_hour not in gate._trading_hours:
                                        reasons.append(f"hour={current_hour}")

                                    passed = not reasons
                                    if passed:
                                        passed_list.append(f"{fc.symbol}:{direction}")
                                    all_pairs_info.append((
                                        fc.symbol, direction, conf, aligned_count, total, strength, passed, reasons
                                    ))

                                # Change detection: compare with previous cycle
                                current_passed_set = set(passed_list)
                                changed = current_passed_set != _prev_passed_set
                                new_signals = current_passed_set - _prev_passed_set
                                lost_signals = _prev_passed_set - current_passed_set

                                # Full log on changes, summary-only when stable
                                if changed or new_bar_detected:
                                    for sym, direction, conf, al, tot, strength, passed, reasons in all_pairs_info:
                                        mark = "✅" if passed else "❌"
                                        # Get advanced filter metadata for this symbol
                                        fc_match = next((f for f in forecasts if f.symbol == sym), None)
                                        adv = ""
                                        if fc_match:
                                            parts = []
                                            if fc_match.adx_value is not None:
                                                adx_flag = "🔴" if fc_match.adx_value < 20 else "🟢"
                                                parts.append(f"ADX={fc_match.adx_value:.1f}{adx_flag}")
                                            if fc_match.bb_squeeze is not None:
                                                sq_flag = "🔴SQ" if fc_match.bb_squeeze else ""
                                                if sq_flag:
                                                    parts.append(sq_flag)
                                            if fc_match.bb_width is not None:
                                                parts.append(f"BBw={fc_match.bb_width:.4f}")
                                            if fc_match.mtf_conflict is True:
                                                parts.append(f"MTF_CONFLICT🔴(H4={fc_match.mtf_h4_direction})")
                                            elif fc_match.mtf_h4_direction:
                                                parts.append(f"H4={fc_match.mtf_h4_direction}")
                                            if parts:
                                                adv = " | " + " ".join(parts)
                                        print(
                                            f"forecast_quality {sym:8s} {direction:4s} "
                                            f"conf={conf:6s} aligned={al}/{tot} "
                                            f"str={strength:.2f} hour={current_hour:02d} "
                                            f"{mark} {','.join(reasons) if reasons else 'PASSED'}{adv}"
                                        )
                                    if new_signals:
                                        print(f"🟢 NEW_SIGNALS: {', '.join(sorted(new_signals))}")
                                        # Alt1 Telegram notifications DISABLED — only Alt2/Alt3 sent via TG
                                        # (Alt2/Alt3 notifications are sent above in the forecast insert block)
                                    if lost_signals:
                                        print(f"🔴 LOST_SIGNALS: {', '.join(sorted(lost_signals))}")
                                        if tg_notify_lost:
                                            try:
                                                tg_notifier.notify_lost_signals(lost_signals)
                                            except Exception as tg_exc:
                                                print(f"tg_notify_lost_error: {tg_exc}")

                                # Always print summary
                                print(
                                    f"forecast_quality_summary passed={len(passed_list)}/{len(forecasts)} "
                                    f"hour={current_hour:02d} trigger={trigger_type} "
                                    f"changed={'YES' if changed else 'no'} "
                                    f"[{', '.join(passed_list) if passed_list else 'none'}]"
                                )
                                _prev_passed_set = current_passed_set

                                # Trading hours transition notifications
                                if not tg_alt_only:
                                    in_hours_now = current_hour in _tg_trading_hours
                                    if _prev_in_trading_hours is not None and in_hours_now != _prev_in_trading_hours:
                                        try:
                                            if in_hours_now:
                                                tg_notifier.notify_trading_hours_start(current_hour)
                                            else:
                                                tg_notifier.notify_trading_hours_end(current_hour)
                                                # Send daily report when session ends
                                                # hour=12 = after morning session, hour=20 = after evening session
                                                if current_hour == 20 or current_hour == 12:
                                                    try:
                                                        tg_notifier.notify_daily_report(db.client)
                                                    except Exception as dr_exc:
                                                        print(f"tg_daily_report_error: {dr_exc}")
                                        except Exception as tg_exc:
                                            print(f"tg_hours_notify_error: {tg_exc}")
                                    _prev_in_trading_hours = in_hours_now
                except Exception as exc:
                    print(f"forecast_error: {exc}")
                last_forecast_tick = now_ts

        # Forecast verification — runs independently, more frequently than forecast generation
        if forecast_verifier and market_data_service and forecast_enabled:
            now_ts = time.time()
            if now_ts - last_forecast_verify_tick >= forecast_verify_interval:
                try:
                    active_symbols = getattr(settings, "symbols", None) or []
                    verify_result = forecast_verifier.verify_pending(
                        market_data_service=market_data_service,
                        symbols=active_symbols,
                        limit=forecast_verify_batch,
                    )
                    if verify_result.count > 0:
                        print(f"forecast_verified count={verify_result.count}")
                        # Feed verification results to lifecycle manager
                        if signal_lifecycle.enabled:
                            try:
                                for _av in verify_result.alt_results:
                                    _variant = str(getattr(_av, "variant", "") or "").lower()
                                    if _variant not in ("alt2", "alt3"):
                                        continue
                                    _sym = str(getattr(_av, "symbol", "") or "").upper()
                                    if not _sym:
                                        continue
                                    _correct = bool(getattr(_av, "correct", False))
                                    signal_lifecycle.record_verification(f"{_sym}#{_variant}", correct=_correct)
                            except Exception as _lc_exc:
                                print(f"lifecycle_verify_error: {_lc_exc}")
                except Exception as vexc:
                    print(f"forecast_verify_error: {vexc}")
                last_forecast_verify_tick = now_ts

        (
            processed_total,
            fetched_total,
            scanned_total,
            sample_ids,
            stop_reason,
            elapsed_ms,
            error_message,
            slow,
            next_batch_size,
            fetch_ms_total,
            persist_ms_total,
            early_break,
            stable_fast_ticks,
            historical_cursor_ts,
        ) = run_backfill_tick(
            client=db.client,
            params=getattr(settings, "signals_params", None),
            settings=settings,
            agents_aggregator=agents_aggregator,
            risk_engine=risk_engine,
            decisions_repo=decisions_repo,
            risk_verdicts_repo=risk_verdicts_repo,
            risk_events_repo=risk_events_repo,
            batch_size=batch_size,
            backfill_max_per_tick=backfill_max_per_tick,
            backfill_max_seconds=backfill_max_seconds,
            enabled=backfill_enabled,
            adaptive=adaptive,
            stable_fast_ticks=stable_fast_ticks,
            safety_ms=safety_ms,
            ema_alpha=ema_alpha,
            persist_ema_ms=persist_ema_ms,
            historical_cursor_ts=historical_cursor_ts,
            symbols_empty=symbols_empty_flag,
        )
        batch_size = next_batch_size
        persist_ema_ms = persist_ms_total / processed_total if processed_total else persist_ema_ms
        log_needed = processed_total > 0 or stop_reason != last_stop_reason
        last_stop_reason = stop_reason
        if log_needed:
            slow_suffix = " slow=true" if slow else ""
            symbols_suffix = ""
            if symbols_empty_flag:
                symbols_suffix = " symbols_filter=ALL"
            print(
                f"control_plane_backfill fetched={fetched_total} scanned={scanned_total} processed={processed_total} batch_cap={batch_size} next_batch={next_batch_size} "
                f"fetch_ms={int(fetch_ms_total)} persist_ms={int(persist_ms_total)} total_ms={int(elapsed_ms)} stop_reason={stop_reason} early_break={early_break}{slow_suffix}{symbols_suffix}"
            )
        # Run execution tick if enabled
        if execution_enabled and not execution_service.is_fail_safe_blocked():
            exec_result = run_execution_tick(
                client=db.client,
                settings=settings,
                execution_service=execution_service,
                risk_events_repo=risk_events_repo,
                limit=execution_tick_limit,
            )
            if exec_result["executed"] > 0 or exec_result["errors"] > 0:
                exec_log = f"execution_tick executed={exec_result['executed']} skipped={exec_result['skipped']} errors={exec_result['errors']}"
                if exec_log != last_execution_log:
                    print(exec_log)
                    last_execution_log = exec_log

        # Phase 5: Parallel LLM agents mode logging
        if parallel_shadow_enabled:
            parallel_result = run_parallel_shadow_tick(
                client=db.client,
                parallel_repo=parallel_decisions_repo,
                parallel_runner=parallel_runner,
                risk_events_repo=risk_events_repo,
                limit=parallel_shadow_limit,
            )
            if parallel_result["logged"] > 0 or parallel_result["errors"] > 0:
                llm_suffix = ""
                if llm_enabled:
                    llm_suffix = f" llm_calls={parallel_result['llm_calls']} cache_hits={parallel_result['cache_hits']}"
                parallel_log = f"parallel_agents logged={parallel_result['logged']} skipped={parallel_result['skipped']} errors={parallel_result['errors']}{llm_suffix}"
                if parallel_log != last_parallel_log:
                    print(parallel_log)
                    last_parallel_log = parallel_log

        # Phase 1: Periodic data sources fetch
        if data_sources_enabled:
            now_ts = time.time()
            if now_ts - last_data_sources_fetch >= data_sources_fetch_interval:
                try:
                    economic_calendar.fetch(days_ahead=7)
                    cot_reports.fetch()
                    dxy_fetcher.fetch()
                    
                    # Check health of all sources
                    health = source_health_monitor.check_all({
                        "economic_calendar": economic_calendar,
                        "cot_reports": cot_reports,
                        "dxy_index": dxy_fetcher,
                    })
                    
                    stale = source_health_monitor.get_all_stale(health)
                    data_sources_log = f"data_sources fetched=3 stale={len(stale)}"
                    if stale:
                        data_sources_log += f" stale_list={stale}"
                    
                    if data_sources_log != last_data_sources_log:
                        print(data_sources_log)
                        last_data_sources_log = data_sources_log
                    
                    last_data_sources_fetch = now_ts
                except Exception as exc:
                    if risk_events_repo:
                        risk_events_repo.insert(
                            event_type="DATA_SOURCES_FETCH_ERROR",
                            severity="error",
                            message=f"Data sources fetch failed: {exc}",
                            data={"error": str(exc)},
                        )

        # Broker state periodic sync + update live prices for open trades
        now_ts = time.time()
        if now_ts - last_broker_sync_tick >= broker_sync_interval:
            try:
                sync_result = broker_state_service.sync_with_db()
                sync_log = (
                    f"broker_sync opened={len(sync_result.positions_opened)} "
                    f"closed={len(sync_result.positions_closed)} mismatches={len(sync_result.mismatches)}"
                )
                if sync_log != last_broker_sync_log:
                    print(sync_log)
                    last_broker_sync_log = sync_log
                if sync_result.errors and risk_events_repo:
                    for err in sync_result.errors:
                        risk_events_repo.insert(
                            event_type="BROKER_SYNC_ERROR",
                            severity="error",
                            message=f"Broker sync failed: {err}",
                            data={"error": err},
                        )
                # Update current_price + unrealized_pnl for open trades from broker positions
                try:
                    state = broker_state_service.get_state()
                    if state and state.positions:
                        open_trades = trades_history_repo.get_active_trades_full() if trades_history_repo else []
                        updated_count = 0
                        for trade in open_trades:
                            meta = trade.get("meta") or {}
                            trade_key = meta.get("instrument_key")
                            if not trade_key:
                                continue
                            pos = state.positions.get(trade_key)
                            if not pos:
                                continue
                            # Calculate mid-price from avg_cost + unrealized_pnl
                            entry_price = float(trade.get("entry_price") or 0)
                            qty = float(trade.get("quantity") or 0)
                            side = str(trade.get("side") or "").upper()
                            upnl = pos.unrealized_pnl
                            current_price = None
                            if qty > 0 and entry_price > 0:
                                if side.startswith("B") or side == "LONG":
                                    current_price = entry_price + (upnl / qty)
                                else:
                                    current_price = entry_price - (upnl / qty)
                            try:
                                update_data = {"unrealized_pnl": upnl}
                                if current_price is not None:
                                    update_data["current_price"] = current_price
                                db.client.table("trades_history").update(
                                    update_data
                                ).eq("id", str(trade.get("id"))).execute()
                                updated_count += 1
                            except Exception:
                                pass
                        if updated_count > 0:
                            print(f"broker_prices_updated count={updated_count}")
                except Exception as price_exc:
                    print(f"broker_prices_update_error: {price_exc}")
            except Exception as exc:
                print(f"broker_sync_tick_error: {exc}")
                if risk_events_repo:
                    risk_events_repo.insert(
                        event_type="BROKER_SYNC_ERROR",
                        severity="error",
                        message=f"Broker sync failed: {exc}",
                        data={"error": str(exc)},
                    )
            last_broker_sync_tick = now_ts

        # Periodic stats logging
        if tick_count % stats_log_interval == 0 and llm_enabled:
            runner_stats = parallel_runner.get_stats()
            budget_status = parallel_runner.budget_limiter.get_status() if parallel_runner.budget_limiter else "N/A"
            cache_stats = parallel_runner.agent_cache.get_stats() if parallel_runner.agent_cache else {}
            print(
                f"llm_stats calls={runner_stats['llm_calls']} cache_hits={runner_stats['cache_hits']} "
                f"budget={budget_status} cache_entries={cache_stats.get('entries', 0)} "
                f"cache_hit_rate={cache_stats.get('hit_rate', 0):.1%}"
            )

        # Idle backoff for drained states
        if stop_reason in ("NO_PENDING", "FULLY_DRAINED"):
            idle_ticks += 1
        else:
            idle_ticks = 0
        sleep_for = poll_seconds
        if idle_backoff_enabled and stop_reason in ("NO_PENDING", "FULLY_DRAINED"):
            # Exponential backoff: base^idle_ticks capped at max
            exp_delay = min(idle_backoff_base ** idle_ticks, idle_backoff_max)
            sleep_for = max(poll_seconds, exp_delay)
            if idle_ticks == 1 or idle_ticks % 10 == 0:
                print(f"control_plane_idle stop_reason={stop_reason} idle_ticks={idle_ticks} sleep_for={sleep_for:.1f}s")
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
