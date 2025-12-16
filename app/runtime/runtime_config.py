from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


@dataclass(frozen=True)
class RuntimeConfig:
    # queue / backpressure
    EVENT_QUEUE_MAX_DEPTH: int = _env_int("EVENT_QUEUE_MAX_DEPTH", 500)
    MARKETDATA_KEEP_LAST: bool = os.getenv("MARKETDATA_KEEP_LAST", "1").lower() in ("1", "true", "yes")

    # latency budgets
    MAX_AGENT_TIMEOUT_MS: int = _env_int("MAX_AGENT_TIMEOUT_MS", 2500)
    MAX_DECISION_LATENCY_MS: int = _env_int("MAX_DECISION_LATENCY_MS", 2000)

    # staleness thresholds
    MAX_SNAPSHOT_STALENESS_SEC_M15: int = _env_int("MAX_SNAPSHOT_STALENESS_SEC_M15", 90)
    MAX_SNAPSHOT_STALENESS_SEC_H1: int = _env_int("MAX_SNAPSHOT_STALENESS_SEC_H1", 600)
    MAX_SNAPSHOT_STALENESS_SEC_H4: int = _env_int("MAX_SNAPSHOT_STALENESS_SEC_H4", 2400)

    # reconciliation
    RECON_INTERVAL_SEC: int = _env_int("RECON_INTERVAL_SEC", 5)
