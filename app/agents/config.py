import os
from dataclasses import dataclass


@dataclass
class AgentConfig:
    enabled: bool = False
    model: str = "gpt-4.1-mini"
    timeout_sec: float = 3.0
    cb_failure_threshold: int = 3
    cb_cooldown_sec: int = 3600
    base_url: str = "https://api.openai.com"
    store: bool = False
    api_key: str | None = None


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def load_agent_config_from_env() -> AgentConfig:
    enabled = _parse_bool(os.environ.get("AGENTS_ENABLED"), False)
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini"
    timeout_sec = _parse_float(os.environ.get("AGENT_TIMEOUT_SEC"), 3.0)
    cb_failure_threshold = _parse_int(os.environ.get("AGENT_CB_FAILURE_THRESHOLD"), 3)
    cb_cooldown_sec = _parse_int(os.environ.get("AGENT_CB_COOLDOWN_SEC"), 3600)
    base_url = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com"
    store = _parse_bool(os.environ.get("OPENAI_STORE"), False)

    return AgentConfig(
        enabled=enabled,
        model=model,
        timeout_sec=timeout_sec,
        cb_failure_threshold=cb_failure_threshold,
        cb_cooldown_sec=cb_cooldown_sec,
        base_url=base_url,
        store=store,
        api_key=api_key if api_key else None,
    )
