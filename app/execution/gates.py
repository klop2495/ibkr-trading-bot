import os
from typing import Any

from app.models.bot_settings import BotSettings


def is_execution_enabled(settings: BotSettings, env: dict[str, Any] | None = None) -> bool:
    env = env or os.environ
    try:
        if env.get("IBKR_ENABLED") != "1":
            return False
        if env.get("EXECUTION_ENABLED") != "1":
            return False
        if not getattr(settings, "trading_enabled", False):
            return False
        mode = getattr(settings, "mode", "paper")
        if mode == "paper":
            return True
        allow_live = env.get("ALLOW_LIVE_EXECUTION") == "1"
        return allow_live and mode == "live"
    except Exception:
        return False
