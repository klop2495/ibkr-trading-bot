from app.agents.config import AgentConfig, load_agent_config_from_env
from app.agents.openai_client import OpenAIResponsesClient
from app.agents.orchestrator import AgentsOrchestrator
from app.agents.schemas import AgentDecision, AgentRequest, AgentResponse


def evaluate_agents(request: AgentRequest, config: AgentConfig | None = None) -> AgentDecision:
    cfg = config or load_agent_config_from_env()

    if not cfg.enabled:
        return AgentDecision(
            response=AgentResponse(
                trade_allowed=True,
                risk_modifier=1.0,
                flags=["agents_disabled"],
                comment="agents disabled",
            ),
            per_agent=None,
            timed_out=False,
            fallback_used=False,
            error=None,
        )

    if not cfg.api_key:
        return AgentDecision(
            response=AgentResponse(
                trade_allowed=False,
                risk_modifier=0.5,
                flags=["agent_misconfigured"],
                comment="agent misconfigured",
            ),
            per_agent=None,
            timed_out=False,
            fallback_used=True,
            error="missing_api_key",
        )

    client = OpenAIResponsesClient(
        api_key=cfg.api_key,
        model=cfg.model,
        base_url=cfg.base_url,
        timeout_sec=cfg.timeout_sec,
        store=cfg.store,
    )
    orchestrator = AgentsOrchestrator(
        client=client,
        timeout_sec=cfg.timeout_sec,
        cb_failure_threshold=cfg.cb_failure_threshold,
        cb_cooldown_sec=cfg.cb_cooldown_sec,
    )
    return orchestrator.analyze(request)
