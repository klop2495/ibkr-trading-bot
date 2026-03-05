from datetime import datetime
from typing import Callable, Dict

from app.agents.errors import (
    AgentError,
    AgentHTTPError,
    AgentRefusal,
    AgentSchemaError,
    AgentTimeout,
)
from app.agents.openai_client import OpenAIResponsesClient
from app.agents.schemas import AgentDecision, AgentRequest, AgentResponse
from app.agents.state import CircuitBreakerState


class AgentsOrchestrator:
    """
    Deterministic aggregation:
    - any trade_allowed false blocks overall
    - risk_modifier is min across responses
    - flags are union
    Currently single OpenAI call; structured for multiple agents later.
    """

    def __init__(
        self,
        client: OpenAIResponsesClient,
        timeout_sec: float = 3.0,
        cb_failure_threshold: int = 3,
        cb_cooldown_sec: int = 3600,
        now_func: Callable[[], datetime] | None = None,
        state: CircuitBreakerState | None = None,
    ) -> None:
        self.client = client
        self.timeout_sec = timeout_sec
        self.cb_failure_threshold = cb_failure_threshold
        self.cb_cooldown_sec = cb_cooldown_sec
        self.now_func = now_func or CircuitBreakerState.utc_now
        self.state = state or CircuitBreakerState()

    def analyze(self, request_obj: AgentRequest) -> AgentDecision:
        now = self.now_func()

        breaker_open = self.state.is_open(now, self.cb_cooldown_sec)
        if breaker_open:
            fallback = self._fallback_response(reason_flag="agent_circuit_breaker", timed_out=False)
            return AgentDecision(
                response=fallback,
                per_agent=None,
                timed_out=False,
                fallback_used=True,
                error="circuit_breaker_open",
            )

        if self.state.opened_at_utc is not None and not breaker_open:
            # Cooldown elapsed; reset breaker to allow new attempts.
            self.state.record_success()

        responses: Dict[str, AgentResponse] = {}

        try:
            resp = self.client.analyze(request_obj)
            responses["openai"] = resp
            self.state.record_success()
            aggregated = self._aggregate(responses)
            return AgentDecision(
                response=aggregated,
                per_agent=responses,
                timed_out=False,
                fallback_used=False,
                error=None,
            )
        except AgentTimeout:
            self.state.record_failure(now, self.cb_failure_threshold)
            fallback = self._fallback_response(reason_flag="agent_timeout", timed_out=True)
            return AgentDecision(
                response=fallback,
                per_agent=None,
                timed_out=True,
                fallback_used=True,
                error="timeout",
            )
        except (AgentHTTPError, AgentSchemaError, AgentRefusal, AgentError) as exc:
            self.state.record_failure(now, self.cb_failure_threshold)
            fallback = self._fallback_response(reason_flag="agent_error", timed_out=False)
            return AgentDecision(
                response=fallback,
                per_agent=None,
                timed_out=False,
                fallback_used=True,
                error=type(exc).__name__,
            )

    def _aggregate(self, responses: Dict[str, AgentResponse]) -> AgentResponse:
        trade_allowed = True
        risk_modifier = 1.0
        flags: set[str] = set()
        comments = []
        for resp in responses.values():
            trade_allowed = trade_allowed and resp.trade_allowed
            risk_modifier = min(risk_modifier, resp.risk_modifier)
            flags.update(resp.flags)
            if resp.comment:
                comments.append(resp.comment)
        return AgentResponse(
            trade_allowed=trade_allowed,
            risk_modifier=risk_modifier,
            flags=sorted(flags),
            comment="; ".join(comments) if comments else "ok",
        )

    def _fallback_response(self, reason_flag: str, timed_out: bool) -> AgentResponse:
        flags = [reason_flag]
        return AgentResponse(
            trade_allowed=False,
            risk_modifier=0.5,
            flags=flags,
            comment="fallback",
        )
