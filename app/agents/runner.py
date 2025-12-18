import concurrent.futures
import time
from typing import Dict, List, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent_report import AgentReport
from app.models.signal_preview import SignalPreviewV1
from app.models.signals_params import SignalsParams
from app.storage.agent_reports_repo import AgentReportsRepo
from app.signals.engine_v1 import (
    FLAG_SIGNALS_RULES_NOT_SPECIFIED,
    FLAG_SIGNALS_PARAMS_INVALID,
    FLAG_REGIME_H4_NEUTRAL,
    FLAG_REGIME_UNKNOWN,
    FLAG_SPREAD_WIDE,
    FLAG_SPREAD_UNKNOWN,
    FLAG_DATA_GAP,
    FLAG_DATA_DUP,
    FLAG_DATA_STALE,
    FLAG_ENTRY_NOT_TRIGGERED,
)
from app.signals.engine_v1 import Direction


class AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview: SignalPreviewV1
    params: SignalsParams


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_allowed: bool
    risk_modifier: float = Field(default=1.0, ge=0.0, le=2.0)
    flags: List[str] = Field(default_factory=list)
    commentary: str | None = None
    error: str | None = None


class BaseAgent(Protocol):
    name: str
    version: str

    def run(self, agent_input: AgentInput) -> AgentResult: ...


class RegimeAgent:
    name = "RegimeAgent"
    version = "1.0"

    def run(self, agent_input: AgentInput) -> AgentResult:
        preview = agent_input.preview
        flags = list(preview.flags)
        blocked = False
        if preview.direction == Direction.FLAT:
            blocked = True
        for f in (FLAG_REGIME_H4_NEUTRAL, FLAG_REGIME_UNKNOWN):
            if f in preview.flags:
                blocked = True
        return AgentResult(
            trade_allowed=not blocked,
            risk_modifier=1.0,
            flags=flags,
            commentary=None if not blocked else "regime block",
        )


class QualityAgent:
    name = "QualityAgent"
    version = "1.0"

    def run(self, agent_input: AgentInput) -> AgentResult:
        preview = agent_input.preview
        flags = list(preview.flags)
        blocked = False
        for f in (FLAG_SPREAD_WIDE, FLAG_SPREAD_UNKNOWN, FLAG_DATA_GAP, FLAG_DATA_DUP, FLAG_DATA_STALE):
            if f in preview.flags:
                blocked = True
        return AgentResult(
            trade_allowed=not blocked,
            risk_modifier=1.0,
            flags=flags,
            commentary=None if not blocked else "quality block",
        )


class AgentsAggregator:
    def __init__(
        self,
        agents: List[BaseAgent] | None = None,
        timeout_sec: float = 1.0,
        reports_repo: AgentReportsRepo | None = None,
    ):
        self.agents = agents or [RegimeAgent(), QualityAgent()]
        self.timeout_sec = timeout_sec
        self.reports_repo = reports_repo

    def _fail_result(self, preview: SignalPreviewV1, agent: BaseAgent, error: str) -> AgentResult:
        return AgentResult(
            trade_allowed=False,
            risk_modifier=1.0,
            flags=preview.flags + [FLAG_SIGNALS_PARAMS_INVALID],
            commentary=error,
            error=error,
        )

    def run(self, preview: SignalPreviewV1, params: SignalsParams, signal_preview_id=None) -> Dict[str, AgentResult]:
        if not params.is_configured():
            return {}

        results: Dict[str, AgentResult] = {}
        input_payload = AgentInput(preview=preview, params=params)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.agents)) as executor:
            future_map = {
                executor.submit(self._run_single, agent, input_payload): agent for agent in self.agents
            }
            for future, agent in future_map.items():
                try:
                    result = future.result(timeout=self.timeout_sec)
                except Exception as exc:
                    result = self._fail_result(preview, agent, str(exc))
                results[agent.name] = result
                if self.reports_repo:
                    try:
                        report = AgentReport(
                            schema_version=1,
                            ts_utc=preview.ts_utc,
                            symbol=preview.symbol,
                            scope="symbol",
                            agent_name=agent.name,
                            agent_version=agent.version,
                            trade_allowed=result.trade_allowed,
                            risk_modifier=result.risk_modifier,
                            flags=result.flags,
                            comment=result.commentary,
                            error=result.error,
                            signal_preview_id=signal_preview_id,
                        )
                        self.reports_repo.insert(
                            report=report, ts_utc=preview.ts_utc, scope="symbol", symbol=preview.symbol
                        )
                    except Exception:
                        # fail-safe: ignore persistence errors
                        pass
        return results

    def _run_single(self, agent: BaseAgent, payload: AgentInput) -> AgentResult:
        start = time.perf_counter()
        result = agent.run(payload)
        _ = time.perf_counter() - start
        return result


def aggregate_decision(preview: SignalPreviewV1, agent_results: Dict[str, AgentResult]):
    if not agent_results:
        return {
            "trade_allowed": False,
            "risk_modifier": preview.rr,
            "flags": preview.flags + [FLAG_SIGNALS_RULES_NOT_SPECIFIED],
            "commentary": "agents not run",
        }
    trade_allowed = preview.setup_present and preview.entry_triggered
    risk_modifier = 1.0
    flags: List[str] = list(preview.flags)
    commentary_parts: List[str] = []
    for res in agent_results.values():
        trade_allowed = trade_allowed and res.trade_allowed
        risk_modifier = min(risk_modifier, res.risk_modifier)
        flags.extend(res.flags)
        if res.commentary:
            commentary_parts.append(res.commentary)
    flags = sorted(set(flags))
    return {
        "trade_allowed": trade_allowed,
        "risk_modifier": risk_modifier,
        "flags": flags,
        "commentary": "; ".join(commentary_parts) if commentary_parts else None,
    }
