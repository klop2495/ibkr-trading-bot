"""
Data Status enum for LLM agents.

Phase 6: Agents report data availability to prevent "hallucinations".
If agent has no real data, it must return MISSING and ABSTAIN from voting.
"""

from enum import Enum


class DataStatus(str, Enum):
    """
    Data availability status for agent input.
    
    REAL: Agent has real, fresh data from API/source.
    PARTIAL: Agent has some data but incomplete (e.g., stale DXY).
    MISSING: Agent has no real data - must ABSTAIN from voting.
    
    Agents with MISSING status:
    - Return signal="HOLD", confidence=0.0
    - Do NOT participate in llm_score calculation
    - Add flag "NO_<SOURCE>_DATA"
    """
    REAL = "real"
    PARTIAL = "partial"
    MISSING = "missing"
