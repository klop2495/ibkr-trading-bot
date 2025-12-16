import json
from typing import Protocol, runtime_checkable
from urllib import error, request

from app.agents.errors import AgentCallError, AgentTimeout
from app.agents.schemas import AgentRequest, AgentResponse


@runtime_checkable
class AgentClient(Protocol):
    def analyze(self, agent_request: AgentRequest) -> AgentResponse:
        ...


class OpenAIAgentClient:
    """
    Minimal OpenAI client using stdlib only. Designed to be safe-by-default:
    - No new dependencies required.
    - Raises AgentCallError/AgentTimeout for orchestrator fallback when not configured.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str = "gpt-4o-mini",
        endpoint: str = "https://api.openai.com/v1/chat/completions",
        timeout_seconds: float = 5.0,
    ):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def analyze(self, agent_request: AgentRequest) -> AgentResponse:
        if not self.api_key:
            raise AgentCallError("OpenAI API key not configured; agent disabled")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an agent gate returning JSON strictly matching AgentResponse.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "decision_id": agent_request.decision_id,
                            "context_flags": agent_request.context_flags,
                            "features_summary": agent_request.features_summary,
                            "position_state": agent_request.position_state,
                        }
                    ),
                },
            ],
            "max_tokens": 128,
            "temperature": 0,
        }

        req = request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = resp.read()
        except error.URLError as exc:  # timeout or network
            if isinstance(exc.reason, TimeoutError):
                raise AgentTimeout("OpenAI call timed out") from exc
            raise AgentCallError(f"OpenAI call failed: {exc}") from exc
        except TimeoutError as exc:
            raise AgentTimeout("OpenAI call timed out") from exc
        except Exception as exc:
            raise AgentCallError(f"OpenAI call failed: {exc}") from exc

        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
            response_payload = json.loads(content)
        except Exception as exc:
            raise AgentCallError(f"Invalid OpenAI response format: {exc}") from exc

        try:
            return AgentResponse.model_validate(response_payload)
        except Exception as exc:
            raise AgentCallError(f"AgentResponse validation failed: {exc}") from exc
