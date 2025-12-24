"""
LLM Agents package for Phase 6.

Contains 5 specialized agents that analyze market data:
- TechnicalAgent: Price action, indicators, chart patterns (REAL data from IB Gateway)
- MacroAgent: Economic calendar, central bank policy (MISSING until real API)
- SentimentAgent: COT positioning, retail sentiment (MISSING until real CFTC API)
- CorrelationAgent: Cross-pair analysis, DXY correlation (REAL from Yahoo Finance)
- RiskAgent: Volatility, session, drawdown assessment (REAL, has VETO power)

Phase 6 changes:
- Agents report data_status (REAL/PARTIAL/MISSING)
- Agents with MISSING data ABSTAIN from voting (don't participate in llm_score)
- confidence_float (0.0-1.0) used for score calculation
- ScoreAggregator replaces QuorumVoting
- RiskAgent veto only with REAL data
"""

from app.agents.llm.data_status import DataStatus
from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.agents.llm.technical_agent import TechnicalAgent
from app.agents.llm.macro_agent import MacroAgent
from app.agents.llm.sentiment_agent import SentimentAgent
from app.agents.llm.correlation_agent import CorrelationAgent
from app.agents.llm.risk_agent import RiskAgent
from app.agents.llm.score_aggregator import ScoreAggregator, LLMContourResult
from app.agents.llm.context_builder import ContextBuilder, AgentContext

# Legacy compatibility
from app.agents.llm.score_aggregator import ScoreAggregator as WeightedAggregator

__all__ = [
    # Data status
    "DataStatus",
    # Base
    "BaseLLMAgent",
    "AgentSignal",
    # Agents
    "TechnicalAgent",
    "MacroAgent",
    "SentimentAgent",
    "CorrelationAgent",
    "RiskAgent",
    # Aggregation
    "ScoreAggregator",
    "LLMContourResult",
    "WeightedAggregator",  # Legacy alias
    # Context
    "ContextBuilder",
    "AgentContext",
]
