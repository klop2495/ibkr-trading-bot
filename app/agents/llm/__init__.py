"""
LLM Agents package for Phase 3.

Contains 5 specialized agents that analyze market data:
- TechnicalAgent: Price action, indicators, chart patterns
- MacroAgent: Economic calendar, central bank policy
- SentimentAgent: COT positioning, retail sentiment
- CorrelationAgent: Cross-pair analysis, DXY correlation
- RiskAgent: Volatility, session, drawdown assessment

All agents return categorical signals (LONG/SHORT/HOLD) with 
enum confidence levels (LOW/MEDIUM/HIGH) - no numerical calculations.
"""

from app.agents.llm.base_agent import BaseLLMAgent, AgentSignal
from app.agents.llm.technical_agent import TechnicalAgent
from app.agents.llm.macro_agent import MacroAgent
from app.agents.llm.sentiment_agent import SentimentAgent
from app.agents.llm.correlation_agent import CorrelationAgent
from app.agents.llm.risk_agent import RiskAgent
from app.agents.llm.aggregator import WeightedAggregator, AggregatedDecision
from app.agents.llm.context_builder import ContextBuilder, AgentContext

__all__ = [
    "BaseLLMAgent",
    "AgentSignal",
    "TechnicalAgent",
    "MacroAgent",
    "SentimentAgent",
    "CorrelationAgent",
    "RiskAgent",
    "WeightedAggregator",
    "AggregatedDecision",
    "ContextBuilder",
    "AgentContext",
]
