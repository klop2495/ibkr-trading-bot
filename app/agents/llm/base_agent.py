"""
Base LLM Agent class.

Phase 3: All agents inherit from this base class.
Provides common interface for LLM calls with safety gates.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.confidence import ConfidenceLevel, parse_confidence


logger = logging.getLogger(__name__)


@dataclass
class AgentSignal:
    """
    Signal returned by an LLM agent.
    
    Contains categorical decision (no numerical prices/SL/TP).
    """
    agent_name: str
    signal: str  # LONG, SHORT, HOLD
    confidence: ConfidenceLevel
    reasoning: str
    flags: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Metadata
    from_cache: bool = False
    validation_passed: bool = True
    
    def to_dict(self) -> dict:
        """Convert to dict for storage in parallel_decisions.gpt_agent_details."""
        return {
            "agent": self.agent_name,
            "signal": self.signal,
            "confidence": self.confidence.value,
            "reasoning": self.reasoning[:200],  # Truncate for storage
            "flags": self.flags,
            "from_cache": self.from_cache,
            "validation_passed": self.validation_passed,
        }


class BaseLLMAgent(ABC):
    """
    Base class for all LLM agents.
    
    Each agent:
    1. Prepares categorical input data (no raw prices)
    2. Calls LLM with structured prompt
    3. Returns AgentSignal with enum confidence
    
    Subclasses must implement:
    - name: Agent identifier
    - weight: Contribution to final decision (0.0-1.0)
    - prepare_input(): Convert market data to categories
    - get_system_prompt(): Return the system prompt
    """
    
    name: str = "BaseAgent"
    version: str = "1.0"
    weight: float = 0.2  # Default weight
    
    def __init__(
        self,
        llm_client: Optional[Any] = None,
        model: str = "gpt-4o-mini",
        temperature: float = 0.1,
        max_tokens: int = 300,
    ):
        """
        Initialize agent.
        
        Args:
            llm_client: OpenAI-compatible client (None for mock mode).
            model: Model to use for completions.
            temperature: Sampling temperature (low = more deterministic).
            max_tokens: Maximum response tokens.
        """
        self.llm_client = llm_client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._mock_mode = llm_client is None
    
    @abstractmethod
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        """
        Prepare categorical input for LLM.
        
        CRITICAL: Must return categories, NOT raw numbers.
        Example: {"trend": "UP", "rsi_zone": "OVERBOUGHT"} 
        NOT: {"price": 1.0732, "rsi": 72.5}
        
        Args:
            context: Market data context (snapshots, events, etc.)
            symbol: Trading symbol (e.g., "EURUSD")
        
        Returns:
            Dict with categorical data for LLM prompt.
        """
        pass
    
    @abstractmethod
    def get_system_prompt(self) -> str:
        """
        Get the system prompt for this agent.
        
        Must include:
        - Agent role and focus
        - Expected JSON output format
        - Rules about not mentioning prices/SL/TP
        - Confidence level guidelines
        
        Returns:
            System prompt string.
        """
        pass
    
    def call(self, context: Dict[str, Any], symbol: str) -> AgentSignal:
        """
        Call the LLM and return a signal.
        
        Args:
            context: Market data context.
            symbol: Trading symbol.
        
        Returns:
            AgentSignal with decision.
        """
        try:
            # Prepare input
            input_data = self.prepare_input(context, symbol)
            
            if self._mock_mode:
                return self._mock_response(symbol, input_data)
            
            # Call LLM
            response = self._call_llm(input_data)
            
            # Parse response
            return self._parse_response(response)
            
        except Exception as e:
            logger.error(f"{self.name}: call failed - {e}")
            return AgentSignal(
                agent_name=self.name,
                signal="HOLD",
                confidence=ConfidenceLevel.LOW,
                reasoning=f"Agent error: {str(e)[:100]}",
                flags=["AGENT_ERROR"],
                validation_passed=False,
            )
    
    def _call_llm(self, input_data: dict) -> dict:
        """
        Make the actual LLM API call.
        
        Args:
            input_data: Prepared categorical input.
        
        Returns:
            Parsed JSON response from LLM.
        """
        response = self.llm_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": json.dumps(input_data, indent=2)},
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        
        content = response.choices[0].message.content
        return json.loads(content)
    
    def _parse_response(self, response: dict) -> AgentSignal:
        """
        Parse LLM response into AgentSignal.
        
        Args:
            response: Parsed JSON from LLM.
        
        Returns:
            AgentSignal with parsed values.
        """
        signal = response.get("signal", "HOLD").upper()
        if signal not in ("LONG", "SHORT", "HOLD"):
            signal = "HOLD"
        
        confidence = parse_confidence(response.get("confidence", "low"))
        reasoning = response.get("reasoning", "")
        flags = response.get("flags", [])
        
        if not isinstance(flags, list):
            flags = []
        
        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            flags=flags,
        )
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """
        Generate mock response for testing without LLM.
        
        Subclasses can override for more realistic mocks.
        
        Args:
            symbol: Trading symbol.
            input_data: Prepared input (for context).
        
        Returns:
            Mock AgentSignal (always HOLD with LOW confidence).
        """
        return AgentSignal(
            agent_name=self.name,
            signal="HOLD",
            confidence=ConfidenceLevel.LOW,
            reasoning=f"Mock response for {symbol} (no LLM client)",
            flags=["MOCK_MODE"],
        )
