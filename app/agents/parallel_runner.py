"""
Parallel Decision Runner - Phase 6: Two-Contour Architecture.

Rules Engine (60%) + LLM Engine (40%) = Hybrid Decision.

Key changes from Phase 4:
- LLM контур выдает llm_score ∈ [-1..+1] вместо quorum voting
- rules_score ∈ [-1..+1] извлекается из signal_preview
- hybrid_score = 0.6 * rules_score + 0.4 * llm_score
- Agents with MISSING data ABSTAIN (don't affect llm_score)
- RiskAgent veto still works but only with REAL data
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.models.parallel_decision import ParallelDecisionV1
from app.models.signal_preview import Direction, SignalPreviewV1
from app.models.confidence import ConfidenceLevel


logger = logging.getLogger(__name__)


# Thresholds
HYBRID_THRESHOLD = float(os.getenv("HYBRID_THRESHOLD", "0.7"))
RULES_WEIGHT = float(os.getenv("HYBRID_RULES_WEIGHT", "0.6"))
LLM_WEIGHT = float(os.getenv("HYBRID_LLM_WEIGHT", "0.4"))


class ParallelDecisionRunner:
    """
    Runs Rules + LLM engines in parallel and creates hybrid decision.
    
    Phase 6 (Two-Contour Architecture):
    - rules_score: -1.0 to +1.0 from signal_preview direction/confidence
    - llm_score: -1.0 to +1.0 from LLM agents (ScoreAggregator)
    - hybrid_score = 0.6 * rules_score + 0.4 * llm_score
    - Thresholds determine final LONG/SHORT/HOLD
    """

    def __init__(
        self,
        active_strategy: str = "hybrid",
        llm_enabled: bool = False,
        budget_limiter: Optional[Any] = None,
        agent_cache: Optional[Any] = None,
        response_validator: Optional[Any] = None,
        context_builder: Optional[Any] = None,
        agents: Optional[List[Any]] = None,
        aggregator: Optional[Any] = None,
    ):
        """
        Args:
            active_strategy: Which strategy to use for execution.
                             "rules", "llm", or "hybrid" (default).
            llm_enabled: Whether to use real LLM agents.
            budget_limiter: BudgetLimiter instance for API cost control.
            agent_cache: AgentCache instance for response caching.
            response_validator: ResponseValidator for output validation.
            context_builder: ContextBuilder for preparing agent inputs.
            agents: List of LLM agent instances.
            aggregator: ScoreAggregator for combining agent signals.
        """
        self.active_strategy = active_strategy
        self.llm_enabled = llm_enabled
        self.budget_limiter = budget_limiter
        self.agent_cache = agent_cache
        self.response_validator = response_validator
        self.context_builder = context_builder
        self.agents = agents or []
        self.aggregator = aggregator
        
        # Stats
        self._calls_total = 0
        self._llm_calls = 0
        self._cache_hits = 0
        self._budget_blocked = 0

    def run(
        self,
        preview: SignalPreviewV1,
        preview_id: UUID,
        decision: Any,  # DecisionV1
        decision_id: Optional[str] = None,
        account_state: Optional[Dict[str, Any]] = None,
    ) -> ParallelDecisionV1:
        """
        Run both engines and create hybrid decision.
        
        Args:
            preview: The signal preview being processed.
            preview_id: UUID of the signal preview.
            decision: The DecisionV1 from existing logic.
            decision_id: UUID of the control_decision (if already persisted).
            account_state: Optional account state for context.
        
        Returns:
            ParallelDecisionV1 ready for persistence.
        """
        self._calls_total += 1
        ts_utc = getattr(preview, "ts_utc", None) or datetime.now(timezone.utc)
        symbol = getattr(preview, "symbol", "")

        # 1. Extract rules_score from signal_preview
        rules_signal, rules_score, rules_confidence, rules_flags = self._extract_rules_score(preview, decision)

        # 2. Run LLM контур
        if self.llm_enabled and self.agents:
            llm_result = self._run_llm_contour(preview, symbol, account_state)
        else:
            llm_result = self._empty_llm_result()
        
        llm_signal = llm_result.get("llm_signal", "HOLD")
        llm_score = llm_result.get("llm_score", 0.0)
        llm_active_weight = llm_result.get("llm_active_weight", 0.0)
        risk_veto = llm_result.get("risk_veto", False)
        gpt_details = llm_result.get("agent_details", [])

        # 3. Compute hybrid_score (60% rules + 40% llm)
        hybrid_score, hybrid_signal = self._compute_hybrid_score(
            rules_score, llm_score, risk_veto
        )

        # 4. Determine executed strategy
        executed_strategy, executed_signal = self._select_strategy(
            rules_signal, llm_signal, hybrid_signal
        )

        # 5. Build safety status
        source_health = llm_result.get("source_health") or {}
        budget_status = self._get_budget_status() or "OK"
        validation_failures = self._get_validation_failures() or 0

        # 6. Log the hybrid tick
        self._log_hybrid_tick(
            symbol, ts_utc, rules_score, llm_score, hybrid_score,
            llm_active_weight, risk_veto, executed_strategy, executed_signal
        )

        return ParallelDecisionV1(
            ts_utc=ts_utc,
            symbol=symbol,
            # Rules
            rules_signal=rules_signal,
            rules_confidence=rules_confidence,
            rules_flags=rules_flags,
            # GPT (renamed from quorum voting)
            gpt_signal=llm_signal,
            gpt_score=llm_score,
            gpt_consensus=llm_active_weight >= 0.5,  # Backwards compat
            gpt_consensus_count=llm_result.get("participating_agents", 0),
            gpt_agent_details=gpt_details,
            # Hybrid
            hybrid_signal=hybrid_signal,
            hybrid_score=hybrid_score,
            # Execution
            executed_strategy=executed_strategy,
            executed_signal=executed_signal,
            # Links
            signal_preview_id=preview_id,
            control_decision_id=UUID(decision_id) if decision_id else None,
            # Safety
            source_health=source_health,
            budget_status=budget_status,
            cache_hits=self._cache_hits,
            validation_failures=validation_failures,
        )

    def _extract_rules_score(
        self,
        preview: SignalPreviewV1,
        decision: Any,
    ) -> tuple[str, float, str, List[str]]:
        """
        Extract rules_score from signal_preview.
        
        Returns:
            (signal, score, confidence, flags)
            
        Score calculation:
        - direction: LONG -> +1, SHORT -> -1, FLAT -> 0
        - confidence multiplier: HIGH -> 0.9, NORMAL -> 0.6, LOW -> 0.3
        - trade_allowed=False -> score = 0
        """
        direction = getattr(preview, "direction", Direction.FLAT)
        if isinstance(direction, Direction):
            direction_val = direction.value
        else:
            direction_val = str(direction) if direction else "flat"

        # Direction to base score
        if direction_val == "long":
            signal = "LONG"
            base_score = 1.0
        elif direction_val == "short":
            signal = "SHORT"
            base_score = -1.0
        else:
            signal = "HOLD"
            base_score = 0.0

        # Confidence multiplier
        confidence = getattr(preview, "confidence", None)
        if confidence:
            confidence_val = confidence.value if hasattr(confidence, "value") else str(confidence)
        else:
            confidence_val = "low"
        
        confidence_multiplier = {
            "high": 0.9,
            "normal": 0.6,
            "low": 0.3,
        }.get(confidence_val.lower(), 0.3)
        
        # Calculate rules_score
        rules_score = base_score * confidence_multiplier

        # If rules are HOLD or blocked, they contribute 0 to hybrid score
        trade_allowed = getattr(decision, "trade_allowed", False)
        if signal == "HOLD" or not trade_allowed:
            rules_score = 0.0
            signal = "HOLD"

        flags = list(getattr(decision, "flags", []) or [])

        return signal, rules_score, confidence_val, flags

    def _run_llm_contour(
        self,
        preview: SignalPreviewV1,
        symbol: str,
        account_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run LLM agents and aggregate into llm_score.
        
        Returns dict with:
        - llm_score: -1.0 to +1.0
        - llm_signal: LONG/SHORT/HOLD
        - llm_active_weight: 0.0 to 1.0
        - risk_veto: bool
        - agent_details: list of dicts
        - participating_agents: int
        """
        # Check budget
        if self.budget_limiter:
            allowed, status = self.budget_limiter.can_call()
            if not allowed:
                self._budget_blocked += 1
                logger.warning(f"LLM call blocked: {status}")
                return self._empty_llm_result()
        
        # Build context
        if self.context_builder:
            context = self.context_builder.build(
                symbol=symbol,
                signal_preview=preview,
                account_state=account_state,
            )
            context_dict = context.to_dict()
        else:
            context_dict = {"symbol": symbol}
        
        # Run each agent
        agent_signals = []
        agent_details = []
        
        for agent in self.agents:
            try:
                # Check cache first
                cached = None
                if self.agent_cache:
                    cached = self.agent_cache.get(agent.name, symbol)
                
                if cached:
                    self._cache_hits += 1
                    # Reconstruct AgentSignal from cache
                    from app.agents.llm.base_agent import AgentSignal
                    from app.agents.llm.data_status import DataStatus
                    
                    signal_obj = AgentSignal(
                        agent_name=cached.agent_name,
                        signal=cached.signal,
                        confidence=ConfidenceLevel(cached.confidence),
                        confidence_float=cached.confidence_float if hasattr(cached, 'confidence_float') else 0.5,
                        reasoning=cached.reasoning,
                        data_status=DataStatus(cached.data_status) if hasattr(cached, 'data_status') else DataStatus.REAL,
                        flags=cached.flags,
                        from_cache=True,
                    )
                else:
                    # Call agent
                    signal_obj = agent.call(context_dict, symbol)
                    self._llm_calls += 1
                    
                    # Validate response
                    if self.response_validator:
                        validated = self.response_validator.sanitize({
                            'signal': signal_obj.signal,
                            'confidence': signal_obj.confidence.value,
                            'reasoning': signal_obj.reasoning,
                            'flags': signal_obj.flags,
                        })
                        signal_obj.signal = validated.signal
                        signal_obj.confidence = validated.confidence
                        signal_obj.validation_passed = validated.is_valid
                    
                    # Cache the response
                    if self.agent_cache and signal_obj.validation_passed:
                        self.agent_cache.set(
                            agent_name=agent.name,
                            symbol=symbol,
                            signal=signal_obj.signal,
                            confidence=signal_obj.confidence.value,
                            reasoning=signal_obj.reasoning,
                            flags=signal_obj.flags,
                        )
                
                agent_signals.append(signal_obj)
                agent_details.append(signal_obj.to_dict())
                
            except Exception as e:
                logger.error(f"Agent {agent.name} failed: {e}")
                from app.agents.llm.data_status import DataStatus
                agent_details.append({
                    'agent': agent.name,
                    'signal': 'HOLD',
                    'confidence': 'low',
                    'confidence_float': 0.0,
                    'data_status': 'missing',
                    'error': str(e)[:100],
                })
        
        # Record budget usage
        if self.budget_limiter and self._llm_calls > 0:
            self.budget_limiter.record_call()
        
        # Aggregate signals with ScoreAggregator
        if self.aggregator and agent_signals:
            result = self.aggregator.aggregate(agent_signals)
            return {
                "llm_score": result.llm_score,
                "llm_signal": result.llm_signal,
                "llm_active_weight": result.llm_active_weight,
                "risk_veto": result.risk_veto,
                "agent_details": agent_details,
                "participating_agents": result.participating_agents,
                "source_health": context_dict.get("source_health", {}),
            }
        
        return self._empty_llm_result()

    def _empty_llm_result(self) -> Dict[str, Any]:
        """Return empty LLM result when agents disabled or failed."""
        return {
            "llm_score": 0.0,
            "llm_signal": "HOLD",
            "llm_active_weight": 0.0,
            "risk_veto": False,
            "agent_details": [],
            "participating_agents": 0,
            "source_health": {},
        }

    def _compute_hybrid_score(
        self,
        rules_score: float,
        llm_score: float,
        risk_veto: bool,
    ) -> tuple[float, str]:
        """
        Compute hybrid_score using 60/40 blend.
        
        Formula: hybrid_score = 0.6 * rules_score + 0.4 * llm_score
        
        If risk_veto: hybrid_signal = HOLD regardless of score.
        
        Returns:
            (hybrid_score, hybrid_signal)
        """
        # Weighted combination
        hybrid_score = (RULES_WEIGHT * rules_score) + (LLM_WEIGHT * llm_score)
        
        # Clamp to [-1, 1]
        hybrid_score = max(-1.0, min(1.0, hybrid_score))
        hybrid_score = round(hybrid_score, 4)
        
        # Determine signal
        if risk_veto:
            hybrid_signal = "HOLD"
        elif hybrid_score >= HYBRID_THRESHOLD:
            hybrid_signal = "LONG"
        elif hybrid_score <= -HYBRID_THRESHOLD:
            hybrid_signal = "SHORT"
        else:
            hybrid_signal = "HOLD"
        
        return hybrid_score, hybrid_signal

    def _select_strategy(
        self,
        rules_signal: str,
        llm_signal: str,
        hybrid_signal: str,
    ) -> tuple[str, str]:
        """Select which strategy to execute."""
        if self.active_strategy == "llm":
            return "llm", llm_signal
        elif self.active_strategy == "rules":
            return "rules", rules_signal
        else:
            return "hybrid", hybrid_signal

    def _log_hybrid_tick(
        self,
        symbol: str,
        ts_utc: datetime,
        rules_score: float,
        llm_score: float,
        hybrid_score: float,
        llm_active_weight: float,
        risk_veto: bool,
        strategy: str,
        signal: str,
    ):
        """Log the hybrid tick with all scores."""
        print(
            f"HYBRID_TICK symbol={symbol} ts={ts_utc.isoformat()} "
            f"rules_score={rules_score:.3f} llm_score={llm_score:.3f} "
            f"hybrid_score={hybrid_score:.3f} llm_active_wt={llm_active_weight:.2f} "
            f"risk_veto={risk_veto} strategy={strategy} signal={signal}"
        )

    def _get_budget_status(self) -> Optional[str]:
        """Get budget status."""
        if self.budget_limiter:
            return self.budget_limiter.get_status()
        return None

    def _get_validation_failures(self) -> Optional[int]:
        """Get validation failure count."""
        if self.response_validator:
            return self.response_validator.get_failures()
        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get runner statistics."""
        return {
            "calls_total": self._calls_total,
            "llm_calls": self._llm_calls,
            "cache_hits": self._cache_hits,
            "budget_blocked": self._budget_blocked,
            "llm_enabled": self.llm_enabled,
            "active_strategy": self.active_strategy,
            "rules_weight": RULES_WEIGHT,
            "llm_weight": LLM_WEIGHT,
        }


# Factory function for creating runner with all components
def create_parallel_runner(
    llm_enabled: bool = False,
    active_strategy: str = "hybrid",
    economic_calendar: Optional[Any] = None,
    cot_reports: Optional[Any] = None,
    dxy_fetcher: Optional[Any] = None,
    llm_client: Optional[Any] = None,
) -> ParallelDecisionRunner:
    """
    Create a ParallelDecisionRunner with all components.
    
    Args:
        llm_enabled: Whether to enable LLM agents.
        active_strategy: Strategy to use for execution (rules/llm/hybrid).
        economic_calendar: EconomicCalendarFetcher instance.
        cot_reports: COTReportsFetcher instance.
        dxy_fetcher: DXYFetcher instance.
        llm_client: OpenAI-compatible client.
    
    Returns:
        Configured ParallelDecisionRunner.
    """
    from app.agents.safety import BudgetLimiter, AgentCache, ResponseValidator
    from app.agents.llm.context_builder import ContextBuilder
    from app.agents.llm import (
        TechnicalAgent, MacroAgent, SentimentAgent,
        CorrelationAgent, RiskAgent, ScoreAggregator
    )
    
    # Create safety gates
    budget_limiter = BudgetLimiter(
        max_calls_per_minute=int(os.getenv("LLM_MAX_CALLS_PER_MINUTE", "30")),
        max_cost_per_day=float(os.getenv("LLM_MAX_COST_PER_DAY", "5.0")),
    )
    agent_cache = AgentCache(
        ttl_minutes=int(os.getenv("LLM_CACHE_TTL_MINUTES", "15"))
    )
    response_validator = ResponseValidator()
    
    # Create context builder
    context_builder = ContextBuilder(
        economic_calendar_fetcher=economic_calendar,
        cot_reports_fetcher=cot_reports,
        dxy_fetcher=dxy_fetcher,
    )
    
    # Create agents
    agents = [
        TechnicalAgent(llm_client=llm_client),
        MacroAgent(llm_client=llm_client),
        SentimentAgent(llm_client=llm_client),
        CorrelationAgent(llm_client=llm_client),
        RiskAgent(llm_client=llm_client),
    ]
    
    # Create score aggregator
    aggregator = ScoreAggregator()
    
    return ParallelDecisionRunner(
        active_strategy=active_strategy,
        llm_enabled=llm_enabled,
        budget_limiter=budget_limiter,
        agent_cache=agent_cache,
        response_validator=response_validator,
        context_builder=context_builder,
        agents=agents,
        aggregator=aggregator,
    )


# Convenience function for Phase 0 compatibility
def create_parallel_decision_shadow(
    preview: SignalPreviewV1,
    preview_id: UUID,
    decision: Any,
    decision_id: Optional[str] = None,
) -> ParallelDecisionV1:
    """Create a shadow mode parallel decision (backwards compat)."""
    runner = ParallelDecisionRunner(active_strategy="rules", llm_enabled=False)
    return runner.run(preview, preview_id, decision, decision_id)
