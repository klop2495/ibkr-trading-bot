import json
import socket
from typing import Any
from urllib import error, request

from app.agents.errors import (
    AgentHTTPError,
    AgentRefusal,
    AgentSchemaError,
    AgentTimeout,
)
from app.agents.schemas import AgentRequest, AgentResponse


class OpenAIResponsesClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com",
        timeout_sec: float = 3.0,
        store: bool = False,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.store = store

    def analyze(self, request_obj: AgentRequest) -> AgentResponse:
        payload = self._build_payload(request_obj)
        http_req = request.Request(
            f"{self.base_url}/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with request.urlopen(http_req, timeout=self.timeout_sec) as resp:
                status = resp.getcode()
                body = resp.read()
        except error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else None
            raise AgentHTTPError(
                f"OpenAI HTTP error: {exc}", status_code=getattr(exc, "code", None), body=body.decode("utf-8", "ignore") if body else None
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise AgentTimeout("OpenAI call timed out") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise AgentTimeout("OpenAI call timed out") from exc
            raise AgentHTTPError(f"OpenAI network error: {exc}", status_code=None, body=None) from exc
        except Exception as exc:
            raise AgentHTTPError(f"OpenAI unexpected error: {exc}", status_code=None, body=None) from exc

        if status is None or status < 200 or status >= 300:
            raise AgentHTTPError("OpenAI non-2xx response", status_code=status, body=body.decode("utf-8", "ignore"))

        try:
            data = json.loads(body)
        except Exception as exc:
            raise AgentSchemaError(f"Invalid JSON body: {exc}") from exc

        output_text = self._extract_output_text(data)
        if output_text is None:
            raise AgentRefusal("No structured output_text in response")

        try:
            return AgentResponse.model_validate_json(output_text)
        except Exception as exc:
            raise AgentSchemaError(f"AgentResponse validation failed: {exc}") from exc

    def _build_payload(self, request_obj: AgentRequest) -> dict[str, Any]:
        system_prompt = (
            "You are an agent gate. Respond ONLY with JSON matching the provided schema. "
            "No market prices or position sizes; the only numeric field is risk_modifier (0.5..1.0). "
            "If uncertain or data quality is not ok, set trade_allowed=false, risk_modifier=0.5, add flag 'uncertain'."
        )
        return {
            "model": self.model,
            "store": self.store,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request_obj.model_dump_json()},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "strict": True,
                    "schema": AgentResponse.model_json_schema(),
                }
            },
        }

    def _extract_output_text(self, data: Any) -> str | None:
        output = data.get("output")
        if not isinstance(output, list):
            return None
        for item in output:
            if item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for elem in content:
                if elem.get("type") == "output_text":
                    text_val = elem.get("text")
                    if isinstance(text_val, str):
                        return text_val
        return None
