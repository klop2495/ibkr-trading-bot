"""
Parallel decision model for shadow mode comparison.

Phase 0: Logs all three strategy decisions (rules, gpt, hybrid) side by side.
GPT starts as HOLD stubs until real agents are implemented.
"""

from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ParallelDecisionV1(BaseModel):
    """
    Captures decisions from all three strategies for comparison.
    
    In Phase 0 (shadow mode):
    - rules_* fields contain real decisions
    - gpt_* fields are HOLD stubs
    - hybrid_* mirrors rules (since gpt=HOLD)
    - executed_strategy is always "rules"
    """
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    ts_utc: datetime
    symbol: str

    # Rules Engine (existing logic)
    rules_signal: str | None = None          # LONG, SHORT, HOLD
    rules_confidence: str | None = None      # low, normal, high
    rules_flags: List[str] = Field(default_factory=list)

    # GPT Agents (stubs in Phase 0)
    gpt_signal: str = "HOLD"
    gpt_score: float = 0.0
    gpt_consensus: bool = False
    gpt_consensus_count: int = 0
    gpt_agent_details: List[Dict[str, Any]] = Field(default_factory=list)

    # Hybrid (blend)
    hybrid_signal: str | None = None
    hybrid_score: float = 0.0

    # Safety metrics (Phase 2+)
    budget_status: str = "OK"
    cache_hits: int = 0
    source_health: Dict[str, Any] = Field(default_factory=dict)
    validation_failures: int = 0

    # Execution tracking
    executed_strategy: str | None = None     # rules, gpt, hybrid, none
    executed_signal: str | None = None

    # Outcome (filled after trade closes)
    outcome_pips: float | None = None
    outcome_result: str | None = None        # win, loss, breakeven

    # Links to existing tables
    signal_preview_id: UUID | None = None
    control_decision_id: UUID | None = None

    def to_db_row(self) -> dict:
        """Convert to Supabase insert payload."""
        return {
            "ts_utc": self.ts_utc.isoformat(),
            "symbol": self.symbol,
            "rules_signal": self.rules_signal,
            "rules_confidence": self.rules_confidence,
            "rules_flags": self.rules_flags,
            "gpt_signal": self.gpt_signal,
            "gpt_score": self.gpt_score,
            "gpt_consensus": self.gpt_consensus,
            "gpt_consensus_count": self.gpt_consensus_count,
            "gpt_agent_details": self.gpt_agent_details,
            "hybrid_signal": self.hybrid_signal,
            "hybrid_score": self.hybrid_score,
            "budget_status": self.budget_status,
            "cache_hits": self.cache_hits,
            "source_health": self.source_health,
            "validation_failures": self.validation_failures,
            "executed_strategy": self.executed_strategy,
            "executed_signal": self.executed_signal,
            "outcome_pips": self.outcome_pips,
            "outcome_result": self.outcome_result,
            "signal_preview_id": str(self.signal_preview_id) if self.signal_preview_id else None,
            "control_decision_id": str(self.control_decision_id) if self.control_decision_id else None,
        }

    @classmethod
    def create_shadow(
        cls,
        ts_utc: datetime,
        symbol: str,
        rules_signal: str,
        rules_confidence: str,
        rules_flags: List[str],
        signal_preview_id: UUID | None = None,
        control_decision_id: UUID | None = None,
    ) -> "ParallelDecisionV1":
        """
        Factory for Phase 0 shadow mode.
        
        Creates a parallel decision where:
        - rules = actual decision
        - gpt = HOLD stub
        - hybrid = mirrors rules
        - executed = rules
        """
        return cls(
            ts_utc=ts_utc,
            symbol=symbol,
            # Rules (real)
            rules_signal=rules_signal,
            rules_confidence=rules_confidence,
            rules_flags=rules_flags,
            # GPT (stub)
            gpt_signal="HOLD",
            gpt_score=0.0,
            gpt_consensus=False,
            gpt_consensus_count=0,
            gpt_agent_details=[],
            # Hybrid (mirrors rules in shadow mode)
            hybrid_signal=rules_signal,
            hybrid_score=1.0 if rules_signal != "HOLD" else 0.0,
            # Execution (always rules in shadow mode)
            executed_strategy="rules",
            executed_signal=rules_signal,
            # Links
            signal_preview_id=signal_preview_id,
            control_decision_id=control_decision_id,
        )
