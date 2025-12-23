"""
Parallel Decision Runner - Full Integration.

Phase 0: Shadow mode with stubs.
Phase 4: Real LLM agents with safety gates.

Runs rules engine (real) + GPT agents (real or stubs) + hybrid.
All decisions are logged to parallel_decisions for comparison.
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


class ParallelDecisionRunner:
    """
    Runs all three strategies in parallel and logs results.
    
    Phase 0 (Shadow Mode):
    - rules: Uses existing AgentsAggregator logic
    - gpt: Returns HOLD stubs
    - hybrid: Mirrors rules (since gpt=HOLD)
    
    Phase 4 (Full Integration):
    - gpt: Real LLM agents with safety gates
    - hybrid: Weighted blend of rules + gpt
    """

    def __init__(
        self,
        active_strategy: str = "rules",
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
                             "rules" (default), "gpt", or "hybrid".
            llm_enabled: Whether to use real LLM agents (Phase 4+).
            budget_limiter: BudgetLimiter instance for API cost control.
            agent_cache: AgentCache instance for response caching.
            response_validator: ResponseValidator for output validation.
            context_builder: ContextBuilder for preparing agent inputs.
            agents: List of LLM agent instances.
            aggregator: WeightedAggregator for combining agent signals.
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
        Run all strategies and create a parallel decision record.
        
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

        # 1. Extract rules decision (from existing logic)
        rules_signal, rules_confidence, rules_flags = self._extract_rules_decision(preview, decision)

        # 2. Run GPT agents (real or stubs)
        gpt_aggregated = None
        if self.llm_enabled and self.agents:
            gpt_result = self._run_llm_agents(preview, symbol, account_state)
            gpt_signal, gpt_score, gpt_consensus, gpt_consensus_count, gpt_details, gpt_aggregated = gpt_result
        else:
            gpt_result = self._run_gpt_stubs()
            gpt_signal, gpt_score, gpt_consensus, gpt_consensus_count, gpt_details = gpt_result

        # 3. Compute hybrid
        hybrid_signal, hybrid_score = self._compute_hybrid(
            rules_signal, rules_confidence,
            gpt_signal, gpt_score,
        )

        # 4. Determine executed strategy
        executed_strategy, executed_signal = self._select_strategy(
            rules_signal, gpt_signal, hybrid_signal
        )

        # 5. Build safety status
        source_health = self._get_source_health() or {}
        budget_status = self._get_budget_status() or "OK"
        validation_failures = self._get_validation_failures() or 0

        return ParallelDecisionV1(
            ts_utc=ts_utc,
            symbol=symbol,
            # Rules
            rules_signal=rules_signal,
            rules_confidence=rules_confidence,
            rules_flags=rules_flags,
            # GPT
            gpt_signal=gpt_signal,
            gpt_score=gpt_score,
            gpt_consensus=gpt_consensus,
            gpt_consensus_count=gpt_consensus_count,
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
            # Safety (Phase 4)
            source_health=source_health,
            budget_status=budget_status,
            cache_hits=self._cache_hits,
            validation_failures=validation_failures,
        )

    def _extract_rules_decision(
        self,
        preview: SignalPreviewV1,
        decision: Any,
    ) -> tuple[str, str, List[str]]:
        """Extract rules decision from existing DecisionV1."""
        direction = getattr(preview, "direction", Direction.FLAT)
        if isinstance(direction, Direction):
            direction_val = direction.value
        else:
            direction_val = str(direction) if direction else "flat"

        if direction_val == "long":
            signal = "LONG"
        elif direction_val == "short":
            signal = "SHORT"
        else:
            signal = "HOLD"

        trade_allowed = getattr(decision, "trade_allowed", False)
        if not trade_allowed:
            signal = "HOLD"

        confidence = getattr(preview, "confidence", None)
        if confidence:
            confidence_val = confidence.value if hasattr(confidence, "value") else str(confidence)
        else:
            confidence_val = "low"

        flags = list(getattr(decision, "flags", []) or [])

        return signal, confidence_val, flags

    def _run_llm_agents(
        self,
        preview: SignalPreviewV1,
        symbol: str,
        account_state: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, float, bool, int, List[Dict[str, Any]], Optional[Any]]:
        """
        Run real LLM agents with safety gates and quorum voting.
        
        Returns:
            (signal, score, consensus, consensus_count, agent_details, aggregated_decision)
        """
        # Check budget
        if self.budget_limiter:
            allowed, status = self.budget_limiter.can_call()
            if not allowed:
                self._budget_blocked += 1
                logger.warning(f"LLM call blocked: {status}")
                return self._run_gpt_stubs()
        
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
                    signal_obj = type('CachedSignal', (), {
                        'agent_name': cached.agent_name,
                        'signal': cached.signal,
                        'confidence': ConfidenceLevel(cached.confidence),
                        'reasoning': cached.reasoning,
                        'flags': cached.flags,
                        'from_cache': True,
                        'validation_passed': True,
                        'to_dict': lambda s=cached: {
                            'agent': s.agent_name,
                            'signal': s.signal,
                            'confidence': s.confidence,
                            'reasoning': s.reasoning[:200],
                            'flags': s.flags,
                            'from_cache': True,
                        }
                    })()
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
                agent_details.append({
                    'agent': agent.name,
                    'signal': 'HOLD',
                    'confidence': 'low',
                    'error': str(e)[:100],
                })
        
        # Record budget usage
        if self.budget_limiter and self._llm_calls > 0:
            self.budget_limiter.record_call()
        
        # Aggregate signals with quorum voting
        if self.aggregator and agent_signals:
            # Check candidate validity and entry_triggered from preview
            from app.agents.runner import is_candidate_valid
            candidate_valid = is_candidate_valid(preview)
            entry_triggered = getattr(preview, "entry_triggered", False)
            
            result = self.aggregator.aggregate(
                agent_signals,
                entry_triggered=entry_triggered,
                candidate_valid=candidate_valid,
            )
            return (
                result.signal,
                result.vote_score,
                result.consensus_level == "STRONG",
                len([s for s in agent_signals if s.signal == result.signal]),
                agent_details,
                result,  # Return full aggregated decision
            )
        
        # Fallback if no aggregator
        return self._run_gpt_stubs() + (None,)

    def _run_gpt_stubs(self) -> tuple[str, float, bool, int, List[Dict[str, Any]]]:
        """Phase 0: Return HOLD stubs for GPT agents."""
        return (
            "HOLD",
            0.0,
            False,
            0,
            [
                {"agent": "TechnicalAgent", "signal": "HOLD", "confidence": "low", "stub": True},
                {"agent": "MacroAgent", "signal": "HOLD", "confidence": "low", "stub": True},
                {"agent": "SentimentAgent", "signal": "HOLD", "confidence": "low", "stub": True},
                {"agent": "CorrelationAgent", "signal": "HOLD", "confidence": "low", "stub": True},
                {"agent": "RiskAgent", "signal": "HOLD", "confidence": "low", "stub": True},
            ],
        )

    def _compute_hybrid(
        self,
        rules_signal: str,
        rules_confidence: str,
        gpt_signal: str,
        gpt_score: float,
    ) -> tuple[str, float]:
        """
        Compute hybrid decision.
        
        Phase 0: Mirrors rules.
        Phase 4: Weighted blend with configurable weights.
        """
        # Weights (configurable via env)
        rules_weight = float(os.getenv("HYBRID_RULES_WEIGHT", "0.6"))
        gpt_weight = float(os.getenv("HYBRID_GPT_WEIGHT", "0.4"))
        
        # Convert signals to scores
        signal_scores = {"LONG": 1.0, "SHORT": -1.0, "HOLD": 0.0}
        confidence_multiplier = {"high": 1.0, "medium": 0.7, "low": 0.4}
        
        rules_score = signal_scores.get(rules_signal, 0.0)
        rules_score *= confidence_multiplier.get(rules_confidence, 0.4)
        
        # Weighted combination
        hybrid_score = (rules_weight * rules_score) + (gpt_weight * gpt_score)
        
        # Threshold for signal
        threshold = float(os.getenv("HYBRID_THRESHOLD", "0.2"))
        
        if hybrid_score > threshold:
            hybrid_signal = "LONG"
        elif hybrid_score < -threshold:
            hybrid_signal = "SHORT"
        else:
            hybrid_signal = "HOLD"
        
        return hybrid_signal, round(hybrid_score, 4)

    def _select_strategy(
        self,
        rules_signal: str,
        gpt_signal: str,
        hybrid_signal: str,
    ) -> tuple[str, str]:
        """Select which strategy to execute."""
        if self.active_strategy == "gpt":
            return "gpt", gpt_signal
        elif self.active_strategy == "hybrid":
            return "hybrid", hybrid_signal
        else:
            return "rules", rules_signal

    def _get_source_health(self) -> Optional[Dict[str, Any]]:
        """Get source health status if monitor available."""
        # Will be populated by main.py integration
        return None

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
        }


# Factory function for creating runner with all components
def create_parallel_runner(
    llm_enabled: bool = False,
    active_strategy: str = "rules",
    economic_calendar: Optional[Any] = None,
    cot_reports: Optional[Any] = None,
    dxy_fetcher: Optional[Any] = None,
    llm_client: Optional[Any] = None,
) -> ParallelDecisionRunner:
    """
    Create a ParallelDecisionRunner with all components.
    
    Args:
        llm_enabled: Whether to enable LLM agents.
        active_strategy: Strategy to use for execution.
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
        CorrelationAgent, RiskAgent, WeightedAggregator
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
    
    # Create aggregator
    aggregator = WeightedAggregator()
    
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
    """Create a shadow mode parallel decision (Phase 0 compatibility)."""
    runner = ParallelDecisionRunner(active_strategy="rules", llm_enabled=False)
    return runner.run(preview, preview_id, decision, decision_id)
