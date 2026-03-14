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


def stale_cutoff(threshold_minutes: int) -> datetime:
    return utcnow() - timedelta(minutes=max(0, int(threshold_minutes)))
