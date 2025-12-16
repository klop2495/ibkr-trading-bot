from datetime import datetime, timezone
from typing import Dict, List

from pydantic import BaseModel, ConfigDict

from app.agents.config import AgentConfig
from app.agents.gate import evaluate_agents
from app.agents.reporting import build_agent_report
from app.agents.schemas import (
    AgentDecision,
    AgentResponse,
    AgentRequest,
    FeatureBins,
    PerSymbolAgentState,
    PortfolioAgentState,
    SignalSummary,
)
from app.storage.agent_reports_repo import AgentReportsRepo


class _PerSymbolPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_summary: dict
    feature_bins: dict
    data_quality: dict | None = None
    spread_quality: dict | None = None


def build_agent_request_from_inputs(
    ts_utc: datetime,
    universe: List[str],
    timeframes: List[str],
    per_symbol_inputs: Dict[str, Dict],
    portfolio_inputs: Dict,
) -> AgentRequest:
    per_symbol_states: List[PerSymbolAgentState] = []
    for sym, payload in per_symbol_inputs.items():
        validated = _PerSymbolPayload.model_validate(payload)
        per_symbol_states.append(
            PerSymbolAgentState(
                symbol=sym,
                signal_summary=SignalSummary(**validated.signal_summary),
                feature_bins=FeatureBins(**validated.feature_bins),
                data_quality=validated.data_quality,
                spread_quality=validated.spread_quality,
            )
        )

    portfolio_state = PortfolioAgentState(**portfolio_inputs)

    return AgentRequest(
        ts_utc=ts_utc,
        universe=universe,
        timeframes=timeframes,
        per_symbol=per_symbol_states,
        portfolio=portfolio_state,
        safe_mode=False,
    )


def agents_precheck(
    ts_utc: datetime,
    universe: List[str],
    per_symbol_inputs: Dict[str, Dict],
    portfolio_inputs: Dict,
    config: AgentConfig | None = None,
) -> AgentDecision:
    req = build_agent_request_from_inputs(
        ts_utc=ts_utc,
        universe=universe,
        timeframes=["M15", "H1", "H4"],
        per_symbol_inputs=per_symbol_inputs,
        portfolio_inputs=portfolio_inputs,
    )
    return evaluate_agents(req, config=config)


def agents_precheck_and_persist(
    decision_id: str,
    ts_utc: datetime,
    universe: List[str],
    per_symbol_inputs: Dict[str, Dict],
    portfolio_inputs: Dict,
    config: AgentConfig | None = None,
    agent_reports_repo: AgentReportsRepo | None = None,
) -> AgentDecision:
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=timezone.utc)

    req = build_agent_request_from_inputs(
        ts_utc=ts_utc,
        universe=universe,
        timeframes=["M15", "H1", "H4"],
        per_symbol_inputs=per_symbol_inputs,
        portfolio_inputs=portfolio_inputs,
    )
    decision = evaluate_agents(req, config=config)

    if agent_reports_repo:
        try:
            report = build_agent_report(decision_id=decision_id, ts_utc=ts_utc, request=req, decision=decision)
            agent_reports_repo.insert(report=report, ts_utc=ts_utc, scope="portfolio", symbol=None)
        except Exception:
            return AgentDecision(
                response=AgentResponse(
                    trade_allowed=False,
                    risk_modifier=0.5,
                    flags=["agent_report_persist_failed"],
                    comment="agent report persist failed",
                ),
                per_agent=None,
                timed_out=False,
                fallback_used=True,
                error="persist_failed",
            )

    return decision
