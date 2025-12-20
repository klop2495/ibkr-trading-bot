#!/usr/bin/env python
"""
State capture for control-plane chain (signal_previews -> control_decisions -> risk_verdicts).

Usage:
  python scripts/state_capture_control_plane.py

Requires env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, BOT_OWNER_USER_ID.
Optional: STATE_CAPTURE_SMOKE=1 to insert a smoke decision+verdict for the latest preview.
Outputs:
  STATE_CAPTURE_CONTROL_PLANE.json
  STATE_CAPTURE_CONTROL_PLANE.md
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.storage.db import SupabaseDB  # noqa: E402
from app.storage.repositories import DecisionsRepo, RiskVerdictsRepo, RiskEventsRepo  # noqa: E402
from app.models.decision import DecisionV1  # noqa: E402
from app.risk.engine_v1 import RiskEngineV1  # noqa: E402


REQUIRED_ENV = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "BOT_OWNER_USER_ID"]
JSON_OUTPUT = ROOT / "STATE_CAPTURE_CONTROL_PLANE.json"
MD_OUTPUT = ROOT / "STATE_CAPTURE_CONTROL_PLANE.md"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_key(key: str) -> Dict[str, Any]:
    if not key:
        return {}
    info: Dict[str, Any] = {"len": len(key)}
    if key.startswith("eyJ") and len(key) >= 12:
        info["prefix"] = key[:6]
        info["suffix"] = key[-6:]
    else:
        info["hint"] = "non-jwt"
    return info


def safe_env_summary() -> Dict[str, Any]:
    return {
        "supabase_url": os.getenv("SUPABASE_URL", ""),
        "owner_user_id": os.getenv("BOT_OWNER_USER_ID", ""),
        "service_role_key": redact_key(os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")),
    }


def missing_env() -> List[str]:
    return [name for name in REQUIRED_ENV if not os.getenv(name)]


def fetch_count(client, table: str, warnings: List[str]) -> Optional[int]:
    try:
        res = client.table(table).select("id", count="exact", head=True).execute()
        cnt = getattr(res, "count", None)
        if cnt is None:
            data = getattr(res, "data", None) or []
            return len(data)
        return int(cnt)
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"count_failed:{table}:{exc}")
        return None


def fetch_latest_ts(client, table: str, warnings: List[str]) -> Optional[str]:
    try:
        res = (
            client.table(table)
            .select("ts_utc")
            .order("ts_utc", desc=True)
            .limit(1)
            .maybe_single()
        )
        return (res.data or {}).get("ts_utc") if hasattr(res, "data") else None
    except Exception as exc:  # pragma: no cover
        warnings.append(f"freshness_failed:{table}:{exc}")
        return None


def fetch_top_errors(client, warnings: List[str]) -> List[Dict[str, Any]]:
    event_types = ["SIGNAL_PREVIEW_PERSIST", "CONTROL_DECISION_PERSIST", "RISK_VERDICT_PERSIST"]
    try:
        res = (
            client.table("risk_events")
            .select("ts,event_type,severity,message,data")
            .in_("event_type", event_types)
            .order("ts", desc=True)
            .limit(20)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        result = []
        for r in rows:
            result.append(
                {
                    "ts": r.get("ts"),
                    "event_type": r.get("event_type"),
                    "severity": r.get("severity"),
                    "message": r.get("message"),
                    "error": (r.get("data") or {}).get("error") if isinstance(r.get("data"), dict) else None,
                }
            )
        return result
    except Exception as exc:  # pragma: no cover
        warnings.append(f"top_errors_failed:{exc}")
        return []


def _fetch_id_set(client, table: str, column: str, version_field: Optional[str], warnings: List[str]) -> set[str]:
    ids: set[str] = set()
    query = client.table(table).select(column)
    filtered = False
    if version_field:
        try:
            query = query.eq(version_field, 1)
            filtered = True
        except Exception:
            warnings.append(f"version_field_missing:{table}.{version_field}")
    try:
        res = query.execute()
        for row in getattr(res, "data", None) or []:
            val = row.get(column)
            if val:
                ids.add(str(val))
    except Exception as exc:
        warnings.append(f"id_set_failed:{table}:{exc}")
    if not filtered and version_field:
        warnings.append(f"no_version_filter:{table}")
    return ids


def coverage_gaps(
    client,
    warnings: List[str],
    signal_previews_total: Optional[int],
    control_decisions_total: Optional[int],
) -> Tuple[Optional[int], Optional[int], Optional[int], Dict[str, Any]]:
    previews_no_decision = None
    decisions_no_verdict = None
    verdicts_no_decision = None

    preview_ids_with_decision = _fetch_id_set(client, "control_decisions", "signal_preview_id", "decision_version", warnings)
    decision_ids_with_verdict = _fetch_id_set(client, "risk_verdicts", "decision_id", "risk_version", warnings)

    if signal_previews_total is not None:
        previews_no_decision = max(signal_previews_total - len(preview_ids_with_decision), 0)
    if control_decisions_total is not None:
        decisions_no_verdict = max(control_decisions_total - len(decision_ids_with_verdict), 0)

    try:
        verdict_ids = decision_ids_with_verdict
        if verdict_ids:
            check = client.table("control_decisions").select("id").in_("id", list(verdict_ids)).execute()
            present = {row.get("id") for row in (getattr(check, "data", None) or [])}
            missing = 0
            for did in verdict_ids:
                if did not in present:
                    missing += 1
            verdicts_no_decision = missing
        else:
            verdicts_no_decision = 0
    except Exception as exc:
        warnings.append(f"coverage_verdicts_no_decision:{exc}")

    sanity = {"decisions_missing_preview_fk": None, "verdicts_missing_decision_fk": None}
    try:
        res = (
            client.table("control_decisions")
            .select("id", count="exact", head=True)
            .is_("signal_preview_id", None)
            .execute()
        )
        sanity["decisions_missing_preview_fk"] = getattr(res, "count", None)
    except Exception as exc:
        warnings.append(f"fk_decision_preview_fk:{exc}")
    try:
        res = (
            client.table("risk_verdicts")
            .select("id", count="exact", head=True)
            .is_("decision_id", None)
            .execute()
        )
        sanity["verdicts_missing_decision_fk"] = getattr(res, "count", None)
    except Exception as exc:
        warnings.append(f"fk_verdict_decision_fk:{exc}")

    return previews_no_decision, decisions_no_verdict, verdicts_no_decision, sanity


def fk_checks(client, warnings: List[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "decisions_missing_preview": 0,
        "verdicts_missing_decision": 0,
        "verdict_preview_mismatch": 0,
        "decisions_missing_preview_fk": None,
        "verdicts_missing_decision_fk": None,
    }
    try:
        res = (
            client.table("control_decisions")
            .select("id", count="exact", head=True)
            .is_("signal_preview_id", None)
            .execute()
        )
        result["decisions_missing_preview_fk"] = getattr(res, "count", None)
    except Exception as exc:
        warnings.append(f"fk_decision_preview_fk:{exc}")

    try:
        res = (
            client.table("risk_verdicts")
            .select("id", count="exact", head=True)
            .is_("decision_id", None)
            .execute()
        )
        result["verdicts_missing_decision_fk"] = getattr(res, "count", None)
    except Exception as exc:
        warnings.append(f"fk_verdict_decision_fk:{exc}")

    try:
        dec_res = (
            client.table("control_decisions")
            .select("id, signal_preview_id")
            .order("ts_utc", desc=True)
            .limit(20)
            .execute()
        )
        decisions = getattr(dec_res, "data", None) or []
        preview_ids = {d.get("signal_preview_id") for d in decisions if d.get("signal_preview_id")}
        preview_lookup: Dict[str, bool] = {}
        if preview_ids:
            pv_res = client.table("signal_previews").select("id").in_("id", list(preview_ids)).execute()
            for r in getattr(pv_res, "data", None) or []:
                preview_lookup[r.get("id")] = True
        for d in decisions:
            spid = d.get("signal_preview_id")
            if spid and not preview_lookup.get(spid):
                result["decisions_missing_preview"] += 1
    except Exception as exc:
        warnings.append(f"fk_decision_preview_check:{exc}")

    try:
        ver_res = (
            client.table("risk_verdicts")
            .select("id, decision_id, signal_preview_id")
            .order("ts_utc", desc=True)
            .limit(20)
            .execute()
        )
        verdicts = getattr(ver_res, "data", None) or []
        decision_ids = {v.get("decision_id") for v in verdicts if v.get("decision_id")}
        decisions_lookup: Dict[str, Dict[str, Any]] = {}
        if decision_ids:
            dec_res = client.table("control_decisions").select("id, signal_preview_id").in_("id", list(decision_ids)).execute()
            for r in getattr(dec_res, "data", None) or []:
                decisions_lookup[r.get("id")] = r
        for v in verdicts:
            did = v.get("decision_id")
            spid = v.get("signal_preview_id")
            if not did or did not in decisions_lookup:
                result["verdicts_missing_decision"] += 1
                continue
            dec = decisions_lookup[did]
            if spid and dec.get("signal_preview_id") and spid != dec.get("signal_preview_id"):
                result["verdict_preview_mismatch"] += 1
    except Exception as exc:
        warnings.append(f"fk_verdict_decision_check:{exc}")

    return result


@dataclass
class SmokeResult:
    preview_id: Optional[str] = None
    decision_id: Optional[str] = None
    verdict_id: Optional[str] = None
    ok: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_smoke(client, warnings: List[str]) -> SmokeResult:
    result = SmokeResult()
    try:
        res = client.table("signal_previews").select("*").order("ts_utc", desc=True).limit(1).execute()
        rows = getattr(res, "data", None) or []
        if not rows:
            result.error = "no_previews"
            return result
        pv = rows[0]
        result.preview_id = str(pv.get("id"))
        symbol = pv.get("symbol")
        ts_raw = pv.get("ts_utc") or now_iso()
        try:
            ts_dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except Exception:
            ts_dt = datetime.now(timezone.utc)

        db = SupabaseDB()
        decisions_repo = DecisionsRepo(db)
        verdicts_repo = RiskVerdictsRepo(db)
        events_repo = RiskEventsRepo(db)
        risk_engine = RiskEngineV1()

        decision = DecisionV1(
            ts_utc=ts_dt,
            symbol=symbol,
            signal_preview_id=UUID(result.preview_id),
            trade_allowed=False,
            risk_modifier=1.0,
            flags=["SMOKE_DECISION"],
            commentary="state_capture_smoke",
        )
        decision_id = decisions_repo.insert_decision(decision)
        decision.id = decision_id
        result.decision_id = decision_id

        verdict = risk_engine.evaluate(decision, type("S", (), {"trading_enabled": True})())
        verdict_id = verdicts_repo.insert_verdict(verdict)
        result.verdict_id = verdict_id
        result.ok = True
    except Exception as exc:  # pragma: no cover
        result.error = str(exc)
        try:
            db = SupabaseDB()
            events_repo = RiskEventsRepo(db)
            events_repo.insert(
                event_type="STATE_CAPTURE_SMOKE",
                severity="ERROR",
                message="state capture smoke failed",
                data={"signal_preview_id": result.preview_id, "error": str(exc)},
            )
        except Exception:
            warnings.append("smoke_log_failed")
    return result


def write_json(data: Dict[str, Any]) -> None:
    JSON_OUTPUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def write_md(summary: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append("# STATE_CAPTURE_CONTROL_PLANE")
    lines.append(f"- timestamp: {summary.get('meta', {}).get('ts')}")
    env = summary.get("env", {})
    lines.append(f"- supabase_url: {env.get('supabase_url', '')}")
    lines.append(f"- owner_user_id: {env.get('owner_user_id', '')}")
    lines.append("")
    lines.append("## Counts")
    for k, v in (summary.get("counts") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Coverage")
    for k, v in (summary.get("coverage") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Freshness")
    for k, v in (summary.get("freshness") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## FK checks")
    for k, v in (summary.get("fk_checks") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Sanity")
    for k, v in (summary.get("sanity") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Top errors")
    top_errors = summary.get("top_errors") or []
    if not top_errors:
        lines.append("- none")
    else:
        for err in top_errors:
            parts = [err.get("ts"), err.get("event_type"), err.get("severity"), err.get("message"), err.get("error")]
            lines.append(f"- {' | '.join(str(p) for p in parts if p)}")
    lines.append("")
    lines.append("## Warnings")
    warns = summary.get("warnings") or []
    if not warns:
        lines.append("- none")
    else:
        for w in warns:
            lines.append(f"- {w}")
    lines.append("")
    lines.append("## Smoke")
    smoke = summary.get("smoke") or {}
    if smoke:
        lines.append(f"- ok: {smoke.get('ok')}")
        lines.append(f"- preview_id: {smoke.get('preview_id')}")
        lines.append(f"- decision_id: {smoke.get('decision_id')}")
        lines.append(f"- verdict_id: {smoke.get('verdict_id')}")
        lines.append(f"- error: {smoke.get('error')}")
    else:
        lines.append("- not run")
    lines.append("")
    lines.append("## Next actions")
    next_actions: List[str] = []
    cov = summary.get("coverage") or {}
    fk = summary.get("fk_checks") or {}
    if cov.get("previews_without_decision"):
        next_actions.append("Проверить persist decision: есть превью без решения.")
    if cov.get("decisions_without_verdict"):
        next_actions.append("Проверить persist verdict: решения без вердиктов.")
    if fk.get("verdicts_missing_decision"):
        next_actions.append("FK verdict -> decision нарушен.")
    if not next_actions:
        next_actions.append("Цепочка выглядит целой.")
    for na in next_actions:
        lines.append(f"- {na}")
    MD_OUTPUT.write_text("\n".join(lines))


def main() -> int:
    missing = missing_env()
    if missing:
        print(f"missing_env: {missing}")
        return 2

    warnings: List[str] = []
    meta = {"ts": now_iso()}
    env_summary = safe_env_summary()

    try:
        db = SupabaseDB()
    except Exception as exc:
        print(f"supabase_init_failed: {exc}", file=sys.stderr)
        return 1

    client = db.client
    counts = {
        "signal_previews_total": fetch_count(client, "signal_previews", warnings),
        "control_decisions_total": fetch_count(client, "control_decisions", warnings),
        "risk_verdicts_total": fetch_count(client, "risk_verdicts", warnings),
        "risk_events_total": fetch_count(client, "risk_events", warnings),
    }

    coverage = {}
    cov_previews, cov_decisions, cov_verdicts, sanity = coverage_gaps(
        client,
        warnings,
        signal_previews_total=counts["signal_previews_total"],
        control_decisions_total=counts["control_decisions_total"],
    )
    coverage["previews_without_decision"] = cov_previews
    coverage["decisions_without_verdict"] = cov_decisions
    coverage["verdicts_without_decision"] = cov_verdicts

    freshness = {
        "signal_previews_latest_ts": fetch_latest_ts(client, "signal_previews", warnings),
        "control_decisions_latest_ts": fetch_latest_ts(client, "control_decisions", warnings),
        "risk_verdicts_latest_ts": fetch_latest_ts(client, "risk_verdicts", warnings),
    }

    fk = fk_checks(client, warnings)
    top_errors = fetch_top_errors(client, warnings)

    smoke_result = {}
    exit_code = 0
    if os.getenv("STATE_CAPTURE_SMOKE") == "1":
        sr = run_smoke(client, warnings)
        smoke_result = sr.to_dict()
        if not sr.ok:
            exit_code = 1

    summary = {
        "meta": meta,
        "env": env_summary,
        "counts": counts,
        "coverage": coverage,
        "freshness": freshness,
        "fk_checks": fk,
        "sanity": sanity,
        "top_errors": top_errors,
        "warnings": warnings,
        "smoke": smoke_result,
    }

    write_json(summary)
    write_md(summary)

    print(json.dumps({"ok": exit_code == 0, "warnings": warnings, "smoke": smoke_result}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
