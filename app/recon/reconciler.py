from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from app.models.events import ReconMismatchEvent
from app.storage.repositories import RiskEventsRepo


class ReconCompareFn(Protocol):
    def __call__(self) -> Optional[ReconMismatchEvent]:
        """
        Deterministic compare: broker truth vs local state.
        Return event if mismatch, else None.
        """
        ...


@dataclass
class Reconciler:
    risk_events: RiskEventsRepo
    compare_fn: ReconCompareFn

    def run_once(self) -> Optional[ReconMismatchEvent]:
        evt = self.compare_fn()
        if evt is None:
            return None

        severity = "critical" if evt.mismatch_kind == "CRITICAL" else "warning"
        self.risk_events.insert(
            event_type="RECON_MISMATCH",
            severity=severity,
            symbol=evt.symbol,
            message=f"Recon mismatch ({evt.mismatch_kind}): {evt.details}",
            data={"details": evt.details, "mismatch_kind": evt.mismatch_kind},
        )
        return evt
