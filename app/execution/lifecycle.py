from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


ACTIVE_EXECUTION_STATUSES = {
    "PENDING",
    "SUBMITTED",
    "OPEN",
}

TERMINAL_EXECUTION_STATUSES = {
    "CLOSED",
    "CANCELLED",
    "REJECTED",
    "EXPIRED",
    "DRY_RUN",
}

SPECIAL_EXECUTION_STATUSES = {
    "ORPHAN_POSITION",
}

ALLOWED_EXECUTION_STATUSES = (
    ACTIVE_EXECUTION_STATUSES
    | TERMINAL_EXECUTION_STATUSES
    | SPECIAL_EXECUTION_STATUSES
)

ALLOWED_EXECUTION_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"SUBMITTED", "OPEN", "CANCELLED", "REJECTED", "EXPIRED"},
    "SUBMITTED": {"OPEN", "CANCELLED", "REJECTED", "EXPIRED"},
    "OPEN": {"CLOSED"},
    "CLOSED": set(),
    "CANCELLED": set(),
    "REJECTED": set(),
    "EXPIRED": set(),
    "DRY_RUN": set(),
    "ORPHAN_POSITION": {"CLOSED"},
}


@dataclass(frozen=True)
class StaleExecutionThresholds:
    pending_minutes: int
    submitted_minutes: int


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_execution_status(status: Optional[str]) -> str:
    return str(status or "").strip().upper()


def is_terminal_execution_status(status: Optional[str]) -> bool:
    return normalize_execution_status(status) in TERMINAL_EXECUTION_STATUSES


def is_active_execution_status(status: Optional[str]) -> bool:
    return normalize_execution_status(status) in ACTIVE_EXECUTION_STATUSES


def validate_execution_transition(current_status: Optional[str], new_status: str) -> None:
    current = normalize_execution_status(current_status)
    target = normalize_execution_status(new_status)

    if target not in ALLOWED_EXECUTION_STATUSES:
        raise ValueError(f"unknown execution status: {target}")

    if not current or current == target:
        return

    allowed_targets = ALLOWED_EXECUTION_TRANSITIONS.get(current)
    if allowed_targets is None:
        raise ValueError(f"unknown current execution status: {current}")
    if target not in allowed_targets:
        raise ValueError(f"invalid execution status transition: {current} -> {target}")


def infer_completion_status(status: Optional[str], exit_price: Any = None, pnl: Any = None) -> str:
    normalized = normalize_execution_status(status)
    if normalized in {"PENDING", "SUBMITTED", "OPEN"}:
        return "active"
    if normalized == "DRY_RUN":
        return "dry_run"
    if normalized == "EXPIRED":
        return "expired"
    if normalized == "CANCELLED":
        return "cancelled"
    if normalized == "REJECTED":
        return "rejected"
    if normalized == "ORPHAN_POSITION":
        return "recovered_from_broker"
    if normalized == "CLOSED":
        if exit_price is None or pnl is None:
            return "incomplete"
        return "complete"
    return "unknown"


def infer_close_source(close_reason: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
    if fallback:
        return fallback
    reason = str(close_reason or "").strip().upper()
    if not reason:
        return None
    if reason in {"TP_HIT", "SL_HIT"}:
        return "broker_bracket"
    if reason in {"MANUAL", "MANUAL_CLOSE"}:
        return "manual"
    if reason == "BROKER_FLAT":
        return "broker_reconcile"
    return "broker"


def append_status_trace(
    status_trace: Any,
    *,
    from_status: Optional[str],
    to_status: str,
    reason: Optional[str] = None,
    actor: Optional[str] = None,
    at: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    trace = list(status_trace or [])
    ts = (at or utcnow()).isoformat()
    trace.append(
        {
            "ts": ts,
            "from": normalize_execution_status(from_status) or None,
            "to": normalize_execution_status(to_status),
            "reason": reason,
            "actor": actor,
        }
    )
    return trace


def merge_integrity_flags(existing: Any, *new_flags: Optional[str]) -> list[str]:
    flags = {str(flag).strip() for flag in (existing or []) if str(flag).strip()}
    for flag in new_flags:
        if flag and str(flag).strip():
            flags.add(str(flag).strip())
    return sorted(flags)


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_fx_symbol(symbol: Optional[str]) -> Optional[tuple[str, str]]:
    clean = str(symbol or "").replace("/", "").replace(".", "").replace(" ", "").upper()
    if len(clean) < 6:
        return None
    return (clean[:3], clean[3:6])


def _price_scale_flags(symbol: Optional[str], price: Any, field_name: str) -> list[str]:
    parsed = _parse_fx_symbol(symbol)
    numeric = _coerce_float(price)
    if parsed is None or numeric is None:
        return []

    _, quote = parsed
    flags: list[str] = []
    if numeric <= 0:
        flags.append(f"CORRUPTED_{field_name}")
        return flags

    if quote == "JPY":
        if numeric < 10:
            flags.append(f"SUSPECT_{field_name}_SCALE")
    else:
        if numeric >= 10:
            flags.append(f"SUSPECT_{field_name}_SCALE")
    return flags


def infer_integrity_flags(
    *,
    symbol: Optional[str],
    status: Optional[str],
    close_reason: Optional[str],
    close_source: Optional[str],
    entry_price: Any = None,
    exit_price: Any = None,
    pnl: Any = None,
    pnl_pips: Any = None,
) -> list[str]:
    normalized_status = normalize_execution_status(status)
    normalized_reason = str(close_reason or "").strip().upper()
    normalized_source = str(close_source or "").strip().lower()

    flags: list[str] = []
    flags.extend(_price_scale_flags(symbol, entry_price, "ENTRY_PRICE"))
    flags.extend(_price_scale_flags(symbol, exit_price, "EXIT_PRICE"))

    entry_value = _coerce_float(entry_price)
    exit_value = _coerce_float(exit_price)
    pnl_value = _coerce_float(pnl)
    pnl_pips_value = _coerce_float(pnl_pips)

    if normalized_status == "CLOSED":
        if exit_value is None:
            flags.append("MISSING_EXIT_PRICE")
        if pnl_value is None or pnl_pips_value is None:
            flags.append("MISSING_PNL")
        if normalized_reason == "UNKNOWN":
            flags.append("UNKNOWN_CLOSE_REASON")
        if normalized_reason == "SYNC_PHANTOM":
            flags.append("PHANTOM_CLOSE")
        if normalized_reason == "MANUAL_CLEANUP":
            flags.append("MANUAL_CLEANUP_CLOSE")
        if normalized_reason == "BROKER_FLAT" or normalized_source == "broker_reconcile":
            flags.append("RECONCILE_CLOSE")
        if pnl_pips_value is not None and abs(pnl_pips_value) > 1000:
            flags.append("EXTREME_PNL_PIPS")
        if (
            entry_value is not None
            and exit_value is not None
            and abs(entry_value - exit_value) < 1e-12
            and normalized_reason in {"UNKNOWN", "BROKER_FLAT", "SYNC_PHANTOM", "MANUAL_CLEANUP"}
        ):
            flags.append("ZERO_MOVE_DIRTY_CLOSE")

    return merge_integrity_flags([], *flags)


def stale_cutoff(threshold_minutes: int) -> datetime:
    return utcnow() - timedelta(minutes=max(0, int(threshold_minutes)))
