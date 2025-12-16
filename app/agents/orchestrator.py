from datetime import datetime
from typing import Dict, Iterable

from app.agents.client import AgentClient
from app.agents.errors import AgentCallError, AgentTimeout
from app.agents.schemas import AgentDecision, AgentRequest, AgentResponse
from app.agents.state import AgentsCircuitBreakerState
from app.models.agent_report import AgentReport
from app.storage.agent_reports_repo import AgentReportsRepo
from app.storage.repositories import RiskEventsRepo


class AgentsOrchestrator:
    """
    Deterministic aggregation across multiple agents with fail-safe fallbacks and a circuit breaker.
    """

    def __init__(
        self,
        agents: Dict[str, AgentClient],
        agent_roles: Dict[str, str] | None = None,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
        state: AgentsCircuitBreakerState | None = None,
        now_fn=None,
        agent_reports_repo: AgentReportsRepo | None = None,
        risk_events_repo: RiskEventsRepo | None = None,
    ):
        self.agents = agents
        self.agent_roles = agent_roles or {}
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.state = state or AgentsCircuitBreakerState()
        self.now_fn = now_fn or AgentsCircuitBreakerState.utc_now
        self.agent_reports_repo = agent_reports_repo
        self.risk_events_repo = risk_events_repo

    def analyze(self, agent_request: AgentRequest) -> AgentDecision:
        now = self.now_fn()

        was_open = self.state.opened_at is not None
        if self.state.is_open(now, self.cooldown_seconds):
            self._log_risk_event("AGENT_CB_ON", "warn", message="Circuit breaker open; agents skipped")
            self._log_trade_blocked(agent_request.decision_id, reason="circuit_breaker_open")
            return AgentDecision(
                decision_id=agent_request.decision_id,
                trade_allowed=False,
                risk_modifier=1.0,
                flags=["AGENT_CIRCUIT_BREAKER"],
                comment="Circuit breaker open; agents skipped",
                per_agent={},
            )

        if was_open and self.state.opened_at is None:
            self._log_risk_event("AGENT_CB_OFF", "info", message="Circuit breaker cooldown elapsed")

        per_agent: Dict[str, AgentResponse] = {}
        failure_count = 0

        for name, client in self.agents.items():
            role = self.agent_roles.get(name, "generic")
            try:
                resp = client.analyze(agent_request)
            except AgentTimeout as exc:
                failure_count += 1
                resp = self._fallback_response(agent_request.decision_id, role, timeout=True, error=exc)
                self._log_risk_event(
                    "AGENT_TIMEOUT",
                    "warn",
                    message=f"Agent {name} timeout",
                    data={"role": role, "decision_id": agent_request.decision_id},
                )
            except AgentCallError as exc:
                failure_count += 1
                resp = self._fallback_response(agent_request.decision_id, role, timeout=False, error=exc)
                self._log_risk_event(
                    "AGENT_ERROR",
                    "error",
                    message=f"Agent {name} error",
                    data={"role": role, "decision_id": agent_request.decision_id},
                )
            per_agent[name] = resp
            self._log_agent_report(agent_request.decision_id, name, resp)

        if failure_count > 0:
            cb_was_closed = self.state.opened_at is None
            self.state.record_failures(failure_count, now, self.failure_threshold)
            if cb_was_closed and self.state.opened_at is not None:
                self._log_risk_event(
                    "AGENT_CB_ON",
                    "warn",
                    message="Circuit breaker opened after agent failures",
                    data={"decision_id": agent_request.decision_id},
                )
        else:
            self.state.record_success()

        aggregated = self._aggregate(agent_request.decision_id, per_agent.values())
        aggregated.per_agent = per_agent
        if aggregated.trade_allowed is False:
            self._log_trade_blocked(agent_request.decision_id, reason="agents_blocked")
        return aggregated

    def _aggregate(self, decision_id: str, responses: Iterable[AgentResponse]) -> AgentDecision:
        trade_allowed = True
        risk_modifier = 1.0
        flags: set[str] = set()
        comment_parts = []

        for resp in responses:
            trade_allowed = trade_allowed and resp.trade_allowed
            risk_modifier = min(risk_modifier, resp.risk_modifier)
            flags.update(resp.flags)
            if resp.comment:
                comment_parts.append(resp.comment)

        return AgentDecision(
            decision_id=decision_id,
            trade_allowed=trade_allowed,
            risk_modifier=risk_modifier,
            flags=sorted(flags),
            comment="; ".join(comment_parts) if comment_parts else None,
            per_agent={},
        )

    def _fallback_response(
        self, decision_id: str, role: str, timeout: bool, error: Exception
    ) -> AgentResponse:
        if timeout and role == "volatility":
            return AgentResponse(
                decision_id=decision_id,
                trade_allowed=True,
                risk_modifier=0.8,
                flags=["VOLATILITY_FALLBACK"],
                comment=f"Volatility agent timeout fallback: {error}",
            )
        if timeout and role == "news":
            return AgentResponse(
                decision_id=decision_id,
                trade_allowed=False,
                risk_modifier=1.0,
                flags=["NEWS_TIMEOUT"],
                comment=f"News agent timeout fallback: {error}",
            )
        if timeout and role == "data_quality":
            return AgentResponse(
                decision_id=decision_id,
                trade_allowed=False,
                risk_modifier=1.0,
                flags=["DATA_QUALITY_TIMEOUT"],
                comment=f"Data quality agent timeout fallback: {error}",
            )
        # Generic failure-safe fallback
        return AgentResponse(
            decision_id=decision_id,
            trade_allowed=False,
            risk_modifier=1.0,
            flags=["AGENT_ERROR"],
            comment=f"Agent failure ({role}): {error}",
        )

    def _log_agent_report(self, decision_id: str, agent_name: str, response: AgentResponse) -> None:
        if not self.agent_reports_repo:
            return
        report = AgentReport(
            trade_allowed=response.trade_allowed,
            risk_modifier=response.risk_modifier,
            flags=response.flags,
            comment=response.comment,
        )
        self.agent_reports_repo.insert(decision_id=decision_id, agent_name=agent_name, report=report)

    def _log_risk_event(
        self,
        event_type: str,
        severity: str = "info",
        symbol: str | None = None,
        message: str | None = None,
        data: dict | None = None,
    ) -> None:
        if not self.risk_events_repo:
            return
        self.risk_events_repo.insert(
            event_type=event_type, severity=severity, symbol=symbol, message=message, data=data
        )

    def _log_trade_blocked(self, decision_id: str, reason: str) -> None:
        self._log_risk_event(
            "AGENT_TRADE_BLOCKED",
            "warn",
            message="Trade blocked by agents",
            data={"decision_id": decision_id, "reason": reason},
        )
