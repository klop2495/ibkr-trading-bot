import io
import json
import socket
from datetime import datetime, timezone
from unittest import mock
from urllib import error

import pytest

from app.agents.errors import AgentHTTPError, AgentSchemaError, AgentTimeout
from app.agents.openai_client import OpenAIResponsesClient
from app.agents.schemas import (
    AgentRequest,
    AgentResponse,
    FeatureBins,
    PerSymbolAgentState,
    PortfolioAgentState,
    SignalSummary,
)


def make_request():
    return AgentRequest(
        ts_utc=datetime.now(timezone.utc),
        universe=["EURUSD"],
        timeframes=["M15"],
        per_symbol=[
            PerSymbolAgentState(
                symbol="EURUSD",
                signal_summary=SignalSummary(direction="long", setup_present=True, entry_triggered=False),
                feature_bins=FeatureBins(volatility="normal", trend="up", momentum="neutral"),
                data_quality="ok",
                spread_quality="ok",
            )
        ],
        portfolio=PortfolioAgentState(
            open_positions_count=0,
            usd_side_bias="neutral",
            daily_trade_count_portfolio=0,
            daily_trade_count_per_symbol={"EURUSD": 0},
        ),
        safe_mode=False,
    )


class FakeResponse:
    def __init__(self, status: int, body: bytes):
        self._status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body

    def getcode(self):
        return self._status


def test_analyze_success_and_payload_contains_schema(monkeypatch):
    agent_request = make_request()
    out_json = AgentResponse(trade_allowed=True, risk_modifier=0.9, flags=["ok"], comment="fine").model_dump_json()
    response_body = json.dumps(
        {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": out_json},
                    ],
                }
            ]
        }
    ).encode("utf-8")

    sent_payload = {}

    def fake_urlopen(req, timeout):
        sent_payload["timeout"] = timeout
        sent_payload["data"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse(200, response_body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OpenAIResponsesClient(api_key="sk-test", model="gpt-4o-mini")
    result = client.analyze(agent_request)

    assert isinstance(result, AgentResponse)
    assert result.risk_modifier == 0.9
    assert sent_payload["data"]["store"] is False
    assert sent_payload["data"]["text"]["format"]["type"] == "json_schema"
    assert sent_payload["data"]["text"]["format"]["strict"] is True
    assert sent_payload["timeout"] == client.timeout_sec


def test_analyze_schema_error_on_bad_output(monkeypatch):
    agent_request = make_request()
    bad_body = json.dumps(
        {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": '{"trade_allowed":true}'},  # missing fields
                    ],
                }
            ]
        }
    ).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: FakeResponse(200, bad_body))

    client = OpenAIResponsesClient(api_key="sk-test", model="gpt-4o-mini")
    with pytest.raises(AgentSchemaError):
        client.analyze(agent_request)


def test_analyze_http_error(monkeypatch):
    agent_request = make_request()
    err = error.HTTPError(
        url="http://example.com",
        code=500,
        msg="server error",
        hdrs=None,
        fp=io.BytesIO(b"fail"),
    )

    def fake_urlopen(req, timeout):
        raise err

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OpenAIResponsesClient(api_key="sk-test", model="gpt-4o-mini")
    with pytest.raises(AgentHTTPError):
        client.analyze(agent_request)


def test_analyze_timeout(monkeypatch):
    agent_request = make_request()

    def fake_urlopen(req, timeout):
        raise socket.timeout("timed out")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OpenAIResponsesClient(api_key="sk-test", model="gpt-4o-mini", timeout_sec=1.0)
    with pytest.raises(AgentTimeout):
        client.analyze(agent_request)
