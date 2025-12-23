"""
Confidence level enum for LLM agents.

Phase 2: LLM agents return categorical confidence, not floats.
Weights are computed in code, not by LLM.
"""

from enum import Enum
from typing import Dict


class ConfidenceLevel(str, Enum):
    """
    Confidence level returned by LLM agents.
    
    LLMs are bad at numerical estimation, so we use categorical levels.
    The weights are defined in code, not requested from LLM.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Weights for aggregation (defined in code, not by LLM)
CONFIDENCE_WEIGHTS: Dict[ConfidenceLevel, float] = {
    ConfidenceLevel.LOW: 0.3,
    ConfidenceLevel.MEDIUM: 0.6,
    ConfidenceLevel.HIGH: 0.9,
}


def confidence_to_weight(conf: ConfidenceLevel) -> float:
    """
    Convert confidence level to numerical weight.
    
    Args:
        conf: ConfidenceLevel enum value.
    
    Returns:
        Float weight for aggregation (0.3, 0.6, or 0.9).
    """
    return CONFIDENCE_WEIGHTS.get(conf, 0.3)


def parse_confidence(value: str) -> ConfidenceLevel:
    """
    Parse confidence string to enum, with fallback to LOW.
    
    Args:
        value: String like "low", "medium", "high" (case-insensitive).
    
    Returns:
        ConfidenceLevel enum value.
    """
    if not value:
        return ConfidenceLevel.LOW
    
    normalized = value.lower().strip()
    
    if normalized in ("high", "h"):
        return ConfidenceLevel.HIGH
    elif normalized in ("medium", "med", "m", "normal"):
        return ConfidenceLevel.MEDIUM
    else:
        return ConfidenceLevel.LOW


def confidence_to_float(conf: ConfidenceLevel | str) -> float:
    """
    Convert confidence level to float for quorum calculations.
    
    Args:
        conf: ConfidenceLevel enum or string.
    
    Returns:
        Float value: LOW=0.3, MEDIUM=0.6, HIGH=0.9
    """
    if isinstance(conf, str):
        conf = parse_confidence(conf)
    return CONFIDENCE_WEIGHTS.get(conf, 0.3)
