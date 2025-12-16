from datetime import datetime
from typing import Dict, List

from app.agents.config import AgentConfig
from app.agents.gate import evaluate_agents
from app.agents.schemas import (
    AgentDecision,
    AgentRequest,
    FeatureBins,
    PerSymbolAgentState,
    PortfolioAgentState,
    SignalSummary,
)


def build_agent_request_from_inputs(
    ts_utc: datetime,
    universe: List[str],
    timeframes: List[str],
    per_symbol_inputs: Dict[str, Dict],
    portfolio_inputs: Dict,
) -> AgentRequest:
    per_symbol_states: List[PerSymbolAgentState] = []
    for sym, payload in per_symbol_inputs.items():
        per_symbol_states.append(
            PerSymbolAgentState(
                symbol=sym,
                signal_summary=SignalSummary(**payload["signal_summary"]),
                feature_bins=FeatureBins(**payload["feature_bins"]),
                data_quality=payload["data_quality"],
                spread_quality=payload["spread_quality"],
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
