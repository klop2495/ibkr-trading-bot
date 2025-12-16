from __future__ import annotations

# lower number = higher priority
PRIORITY = {
    "SL_REJECTED": 10,
    "RECON_MISMATCH": 20,
    "PARTIAL_FILL": 30,
    "MARKET_DATA_MISSING": 40,
    "NEW_DECISION": 50,
    "MARKET_DATA_BAR": 60,
}

DEFAULT_PRIORITY = 90


def priority_for(event_type: str) -> int:
    return PRIORITY.get(event_type, DEFAULT_PRIORITY)
