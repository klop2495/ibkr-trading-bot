# Phase 6: Two-Contour Architecture - Implementation Summary

## Overview

Implemented "двухконтурная" схема (Rules Engine 60% + LLM Engine 40%) для гибридных торговых решений.

## Key Changes

### 1. New DataStatus Enum
**File:** `app/agents/llm/data_status.py`
- `REAL`: Agent has fresh data from API
- `PARTIAL`: Agent has stale/incomplete data
- `MISSING`: Agent has no data → ABSTAIN from voting

### 2. Updated AgentSignal
**File:** `app/agents/llm/base_agent.py`
- Added `confidence_float: float` (0.0-1.0) for score calculation
- Added `data_status: DataStatus` for participation tracking
- Added `risk_veto: bool` (only RiskAgent can set)
- Backwards compatible: `confidence_float` auto-derived from categorical if not provided

### 3. Agent Updates

| Agent | data_status | Notes |
|-------|-------------|-------|
| TechnicalAgent | REAL | IB Gateway data |
| CorrelationAgent | REAL | DXY from Yahoo Finance |
| MacroAgent | MISSING | Economic calendar is mock |
| SentimentAgent | MISSING | COT is mock |
| RiskAgent | REAL | Session/volatility/account |

### 4. New ScoreAggregator
**File:** `app/agents/llm/score_aggregator.py`

Formula:
```
For each agent with data_status != MISSING:
  agent_vote = +1 (LONG), -1 (SHORT), 0 (HOLD)
  effective_weight = weight * confidence_float

llm_score = Σ(agent_vote * effective_weight) / Σ(effective_weight)
llm_signal = LONG if llm_score > 0.15, SHORT if < -0.15, else HOLD

If RiskAgent.risk_veto=True → llm_signal = HOLD
```

### 5. Updated ParallelDecisionRunner
**File:** `app/agents/parallel_runner.py`

Hybrid blend:
```
hybrid_score = 0.6 * rules_score + 0.4 * llm_score
```

Environment variables:
- `HYBRID_RULES_WEIGHT`: default 0.6
- `HYBRID_LLM_WEIGHT`: default 0.4
- `HYBRID_THRESHOLD`: default 0.15

### 6. Tests
**File:** `tests/test_two_contour.py`
- ScoreAggregator tests
- Data status abstention tests
- Risk veto tests
- 60/40 blend tests
- No hallucinations tests

## Log Output Example

```
HYBRID_TICK symbol=EURUSD ts=2024-01-15T10:30:00+00:00 rules_score=0.540 llm_score=0.350 hybrid_score=0.464 llm_active_wt=0.65 risk_veto=False strategy=hybrid signal=LONG
```

## Test Commands

```bash
# Run new tests
pytest tests/test_two_contour.py -v

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term-missing
```

## Files Changed

1. `app/agents/llm/data_status.py` - NEW
2. `app/agents/llm/base_agent.py` - UPDATED (AgentSignal, __post_init__)
3. `app/agents/llm/technical_agent.py` - UPDATED (check_data_status)
4. `app/agents/llm/macro_agent.py` - UPDATED (returns MISSING)
5. `app/agents/llm/sentiment_agent.py` - UPDATED (returns MISSING)
6. `app/agents/llm/correlation_agent.py` - UPDATED (real DXY)
7. `app/agents/llm/risk_agent.py` - UPDATED (risk_veto flag)
8. `app/agents/llm/score_aggregator.py` - NEW
9. `app/agents/llm/aggregator.py` - UPDATED (backwards compat)
10. `app/agents/llm/context_builder.py` - UPDATED (source_health)
11. `app/agents/llm/__init__.py` - UPDATED (exports)
12. `app/agents/parallel_runner.py` - UPDATED (60/40 blend)
13. `tests/test_two_contour.py` - NEW

## Self-Check

- [x] AgentSignal backwards compatible with old tests
- [x] Macro/Sentiment return MISSING when calendar/COT are mock
- [x] llm_score varies with confidence (0.3 vs 0.9)
- [x] 60/40 blend implemented correctly
- [x] Risk veto works and is logged
- [x] No logging of secrets
- [x] HYBRID_TICK log line shows all scores

## Undo Commands

```bash
# Revert to previous commit
git checkout HEAD~1 -- \
  app/agents/llm/base_agent.py \
  app/agents/llm/technical_agent.py \
  app/agents/llm/macro_agent.py \
  app/agents/llm/sentiment_agent.py \
  app/agents/llm/correlation_agent.py \
  app/agents/llm/risk_agent.py \
  app/agents/llm/aggregator.py \
  app/agents/llm/context_builder.py \
  app/agents/llm/__init__.py \
  app/agents/parallel_runner.py

# Remove new files
rm app/agents/llm/data_status.py
rm app/agents/llm/score_aggregator.py
rm tests/test_two_contour.py
```

## Algorithm Before/After

### Before (Quorum Voting)
```
LLM: Each agent votes ALLOW/BLOCK/ABSTAIN
     approval_ratio = allow_score / active_weight
     trade_allowed = approval_ratio >= threshold
     RiskAgent HOLD = always BLOCK
     → Binary decision (trade or no trade)
```

### After (Score-based 60/40)
```
Rules: direction × confidence → rules_score ∈ [-1, +1]
LLM:   Σ(vote × weight × conf) / Σ(weight × conf) → llm_score ∈ [-1, +1]
       MISSING agents excluded
       RiskAgent.risk_veto = absolute veto
Blend: hybrid_score = 0.6 × rules_score + 0.4 × llm_score
       signal = LONG if > 0.15, SHORT if < -0.15, else HOLD
```
