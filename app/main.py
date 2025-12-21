import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
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
from app.storage.repositories import DecisionsRepo, RiskEventsRepo, RiskVerdictsRepo, SignalPreviewsRepo
from app.execution.service import ExecutionService, ExecutionMode, ExecutionResult

# Phase 0: Shadow mode parallel decisions
from app.models.parallel_decision import ParallelDecisionV1
from app.storage.parallel_decisions_repo import ParallelDecisionsRepo

# Phase 1: Data sources
from app.data_sources import EconomicCalendarFetcher, COTReportsFetcher, DXYFetcher
from app.agents.safety import SourceHealthMonitor, BudgetLimiter, AgentCache, ResponseValidator

# Phase 3-4: LLM Agents and integration
from app.agents.parallel_runner import ParallelDecisionRunner, create_parallel_runner


DEFAULT_BACKFILL_BATCH = 25
DEFAULT_BACKFILL_MAX_PER_TICK = 400
DEFAULT_BACKFILL_MAX_SECONDS = 1.5
DEFAULT_BACKFILL_SAFETY_MS = 150
DEFAULT_BACKFILL_EMA_ALPHA = 0.2
DEFAULT_IDLE_BACKOFF_BASE = 2.0
DEFAULT_IDLE_BACKOFF_MAX = 60.0
DEFAULT_EQUITY = 10000.0  # Default equity for dry-run mode


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
        
        # Get signal_preview_ids to fetch SL/TP
        signal_preview_ids = [row.get("signal_preview_id") for row in verdicts_rows if row.get("signal_preview_id")]
        signal_previews_map: Dict[str, Dict[str, Any]] = {}
        if signal_preview_ids:
            previews_res = (
                client.table("signal_previews")
                .select("id, sl_distance_pips, tp_distance_pips")
                .in_("id", signal_preview_ids)
                .execute()
            )
            previews_rows = getattr(previews_res, "data", None) or []
            signal_previews_map = {str(row.get("id")): row for row in previews_rows}
        
        # Check which verdicts have already been executed (via risk_events)
        executed_check = (
            client.table("risk_events")
            .select("data")
            .in_("event_type", ["EXECUTION_SUBMIT", "EXECUTION_DRY_RUN"])
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
            
            # Get SL/TP from signal_preview
            signal_preview_id = verdict_row.get("signal_preview_id")
            sl_pips = None
            tp_pips = None
            if signal_preview_id:
                preview_data = signal_previews_map.get(str(signal_preview_id), {})
                sl_pips = preview_data.get("sl_distance_pips")
                tp_pips = preview_data.get("tp_distance_pips")
            
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
                
                # Execute with SL/TP from signal_preview
                result = execution_service.execute(
                    decision,
                    verdict,
                    settings,
                    stop_loss_pips=float(sl_pips) if sl_pips is not None else None,
                    take_profit_pips=float(tp_pips) if tp_pips is not None else None,
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

    # Phase 1: Data sources (mock mode for now)
    data_sources_enabled = os.getenv("DATA_SOURCES_ENABLED", "1") != "0"
    economic_calendar = EconomicCalendarFetcher(mock_mode=True)
    cot_reports = COTReportsFetcher(mock_mode=True)
    dxy_fetcher = DXYFetcher(mock_mode=True)
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
        print("Phase 1: Data sources ENABLED (mock mode)")

    # Initialize ExecutionService
    owner_uuid_str = str(owner_uuid)
    execution_service = ExecutionService(risk_events_repo=risk_events_repo)
    default_equity = float(os.getenv("DEFAULT_EQUITY", str(DEFAULT_EQUITY)))
    execution_service.update_equity(default_equity)
    execution_mode = execution_service._determine_mode(bot_settings_repo.get(owner_uuid_str))
    print(f"ExecutionService initialized mode={execution_mode.value} equity={default_equity}")

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
        if execution_enabled:
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
