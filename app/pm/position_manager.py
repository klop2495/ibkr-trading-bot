from __future__ import annotations

from app.models.events import AnyEvent, PartialFillEvent, SLRejectedEvent, ReconMismatchEvent
from app.models.pm import PositionAction, PositionActionItem, PositionActionPlan


class PositionManager:
    """
    PM orchestrates intent-only actions (no prices / SL/TP numbers / lots).
    """

    def handle(self, event: AnyEvent) -> PositionActionPlan:
        if isinstance(event, PartialFillEvent):
            return self._on_partial_fill(event)
        if isinstance(event, SLRejectedEvent):
            return self._on_sl_rejected(event)
        if isinstance(event, ReconMismatchEvent):
            return self._on_recon_mismatch(event)

        return PositionActionPlan(symbol=event.symbol, severity="info", actions=[])

    def _on_partial_fill(self, e: PartialFillEvent) -> PositionActionPlan:
        return PositionActionPlan(
            symbol=e.symbol,
            severity="warning",
            actions=[
                PositionActionItem(
                    action=PositionAction.REQUEST_REPLACE_PROTECTION,
                    symbol=e.symbol,
                    reason="Partial fill: replace protection deterministically",
                    data={"order_id": e.order_id, "filled_qty": e.filled_qty, "remaining_qty": e.remaining_qty},
                ),
                PositionActionItem(
                    action=PositionAction.REQUEST_RECON_NOW,
                    symbol=e.symbol,
                    reason="Partial fill: immediate reconciliation requested",
                    data={"order_id": e.order_id},
                ),
            ],
            risk_event_type="PM_PARTIAL_FILL",
            risk_event_message="PM: partial fill => replace protection + recon now",
            risk_event_data={"order_id": e.order_id, "filled_qty": e.filled_qty, "remaining_qty": e.remaining_qty},
        )

    def _on_sl_rejected(self, e: SLRejectedEvent) -> PositionActionPlan:
        # strict fail-safe
        return PositionActionPlan(
            symbol=e.symbol,
            severity="critical",
            actions=[
                PositionActionItem(
                    action=PositionAction.FREEZE_SYMBOL,
                    symbol=e.symbol,
                    reason="SL rejected: freeze trading for symbol",
                    data={"broker_error": e.broker_error},
                ),
                PositionActionItem(
                    action=PositionAction.REQUEST_CLOSE_POSITION,
                    symbol=e.symbol,
                    reason="SL rejected: close position immediately (fail-safe)",
                    data={"position_id": e.position_id},
                ),
                PositionActionItem(
                    action=PositionAction.REQUEST_RECON_NOW,
                    symbol=e.symbol,
                    reason="SL rejected: immediate reconciliation requested",
                    data={"position_id": e.position_id},
                ),
            ],
            risk_event_type="SL_REJECTED",
            risk_event_message="SL rejected => freeze symbol + close + recon now",
            risk_event_data={"position_id": e.position_id, "broker_error": e.broker_error},
        )

    def _on_recon_mismatch(self, e: ReconMismatchEvent) -> PositionActionPlan:
        if e.mismatch_kind == "CRITICAL":
            return PositionActionPlan(
                symbol=e.symbol,
                severity="critical",
                actions=[
                    PositionActionItem(
                        action=PositionAction.FREEZE_SYMBOL,
                        symbol=e.symbol,
                        reason="Critical recon mismatch: freeze symbol",
                        data={"details": e.details},
                    ),
                    PositionActionItem(
                        action=PositionAction.REQUEST_CLOSE_POSITION,
                        symbol=e.symbol,
                        reason="Critical recon mismatch: close position to restore safety",
                        data={"details": e.details},
                    ),
                ],
                risk_event_type="RECON_MISMATCH_CRITICAL",
                risk_event_message="Critical recon mismatch => freeze + close",
                risk_event_data={"details": e.details},
            )

        return PositionActionPlan(
            symbol=e.symbol,
            severity="warning",
            actions=[
                PositionActionItem(
                    action=PositionAction.REQUEST_RECON_NOW,
                    symbol=e.symbol,
                    reason="Minor recon mismatch: recon refresh requested",
                    data={"details": e.details},
                )
            ],
            risk_event_type="RECON_MISMATCH_MINOR",
            risk_event_message="Minor recon mismatch => recon refresh requested",
            risk_event_data={"details": e.details},
        )
