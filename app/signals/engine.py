"""Compatibility wrapper for legacy imports."""

from app.signals.engine_v1 import *  # noqa: F403
from app.signals.engine_v1 import SignalEngineV1
from app.models.signals_params import SignalsParams


class SignalEngine(SignalEngineV1):
    def __init__(self, params: SignalsParams | None = None):
        super().__init__(params or SignalsParams())
        self._rules_not_specified_logged = False

    def warn_rules_not_specified(self, risk_events_repo, params: SignalsParams | None = None) -> None:
        cfg = params or self.params
        if cfg.is_configured():
            self._rules_not_specified_logged = False
            return
        if self._rules_not_specified_logged:
            return
        if risk_events_repo:
            risk_events_repo.insert(
                event_type="SIGNALS_RULES_NOT_SPECIFIED",
                severity="warn",
                message="signals params are not configured; signal generation is skipped",
            )
        self._rules_not_specified_logged = True
