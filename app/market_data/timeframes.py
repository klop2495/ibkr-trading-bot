from typing import Dict

TF_TO_SECONDS: Dict[str, int] = {
    "M15": 15 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
}


def timeframe_seconds(tf: str) -> int:
    if tf not in TF_TO_SECONDS:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return TF_TO_SECONDS[tf]
