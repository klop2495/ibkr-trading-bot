"""
Base LLM Agent class.

Phase 6 Update: Added data_status reporting to prevent hallucinations.
Agents must report MISSING if they don't have real data.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.confidence import ConfidenceLevel, parse_confidence, confidence_to_float
from app.agents.llm.data_status import DataStatus


logger = logging.getLogger(__name__)


@dataclass
class AgentSignal:
    """
    Signal returned by an LLM agent.
    
    Phase 6 update:
    - confidence_float: numeric confidence 0.0-1.0 for score calculation
    - data_status: REAL/PARTIAL/MISSING - determines if agent participates
    - risk_veto: Only RiskAgent can set this True to veto trade
    
    If data_status == MISSING:
    - Agent does NOT participate in llm_score calculation
    - signal should be HOLD, confidence_float should be 0.0
    """
    agent_name: str
    signal: str  # LONG, SHORT, HOLD
    confidence: ConfidenceLevel  # Categorical for LLM compatibility
    reasoning: str
    
    # Phase 6: Optional numeric confidence (auto-derived from categorical if not set)
    confidence_float: Optional[float] = None
    
    # Phase 6: Data availability
    data_status: DataStatus = DataStatus.REAL
    
    # Phase 6: Risk veto (only RiskAgent can set)
    risk_veto: bool = False
    
    flags: List[str] = field(default_factory=list)
    observations: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Metadata
    from_cache: bool = False
    validation_passed: bool = True
    
    def __post_init__(self):
        """Auto-derive confidence_float from confidence if not provided."""
        if self.confidence_float is None:
            self.confidence_float = confidence_to_float(self.confidence)
    
    def to_dict(self) -> dict:
        """Convert to dict for storage in parallel_decisions.gpt_agent_details."""
        return {
            "agent": self.agent_name,
            "signal": self.signal,
            "confidence": self.confidence.value,
            "confidence_float": round(self.confidence_float, 3),
            "data_status": self.data_status.value,
            "risk_veto": self.risk_veto,
            "reasoning": self.reasoning[:200],  # Truncate for storage
            "flags": self.flags[:10],  # Limit flags
            "observations": self.observations,
            "from_cache": self.from_cache,
            "validation_passed": self.validation_passed,
        }
    
    @property
    def participates(self) -> bool:
        """Agent participates in score calculation only if has real data."""
        return self.data_status != DataStatus.MISSING


class BaseLLMAgent(ABC):
    """
    Base class for all LLM agents.
    
    Each agent:
    1. Checks data availability via check_data_status()
    2. If MISSING → returns ABSTAIN signal immediately
    3. If REAL/PARTIAL → prepares input and calls LLM
    4. Returns AgentSignal with numeric confidence
    
    Subclasses must implement:
    - name: Agent identifier
    - weight: Contribution to final decision (0.0-1.0)
    - check_data_status(): Return DataStatus based on context
    - prepare_input(): Convert market data to categories
    - get_system_prompt(): Return the system prompt
    """
    
    name: str = "BaseAgent"
    version: str = "2.0"
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
    
    def check_data_status(self, context: Dict[str, Any], symbol: str) -> DataStatus:
        """
        Check if agent has real data to make a decision.
        
        Override in subclasses to check specific data sources.
        Default: REAL (agent always has data).
        
        Args:
            context: Market data context.
            symbol: Trading symbol.
        
        Returns:
            DataStatus indicating data availability.
        """
        return DataStatus.REAL
    
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
        
        Phase 6 flow:
        1. Check data_status
        2. If MISSING → return ABSTAIN immediately
        3. Otherwise → prepare input and call LLM
        
        Args:
            context: Market data context.
            symbol: Trading symbol.
        
        Returns:
            AgentSignal with decision.
        """
        # Step 1: Check data availability
        data_status = self.check_data_status(context, symbol)
        
        # Step 2: If no data, ABSTAIN
        if data_status == DataStatus.MISSING:
            return self._abstain_signal(symbol, "No real data available")
        
        try:
            # Step 3: Prepare input
            input_data = self.prepare_input(context, symbol)
            
            if self._mock_mode:
                signal = self._mock_response(symbol, input_data)
                signal.data_status = data_status
                return signal
            
            # Step 4: Call LLM
            response = self._call_llm(input_data)
            
            # Step 5: Parse response
            signal = self._parse_response(response)
            signal.data_status = data_status
            return signal
            
        except Exception as e:
            logger.error(f"{self.name}: call failed - {e}")
            sig = AgentSignal(
                agent_name=self.name,
                signal="HOLD",
                confidence=ConfidenceLevel.LOW,
                reasoning=f"Agent error: {str(e)[:100]}",
                data_status=DataStatus.MISSING,  # Mark as missing on error
                flags=["AGENT_ERROR"],
                validation_passed=False,
            )
            sig.confidence_float = 0.0
            return sig
    
    def _abstain_signal(self, symbol: str, reason: str) -> AgentSignal:
        """
        Return ABSTAIN signal when agent has no data.
        
        ABSTAIN = HOLD with confidence=0.0 and data_status=MISSING.
        Agent will NOT participate in llm_score calculation.
        """
        sig = AgentSignal(
            agent_name=self.name,
            signal="HOLD",
            confidence=ConfidenceLevel.LOW,
            reasoning=f"ABSTAIN: {reason}",
            data_status=DataStatus.MISSING,
            flags=[f"NO_{self.name.upper().replace('AGENT', '')}_DATA", "ABSTAIN"],
        )
        sig.confidence_float = 0.0
        return sig
    
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
        
        Phase 6: Extracts numeric confidence_float.
        
        Args:
            response: Parsed JSON from LLM.
        
        Returns:
            AgentSignal with parsed values.
        """
        signal = response.get("signal", "HOLD").upper()
        if signal not in ("LONG", "SHORT", "HOLD"):
            signal = "HOLD"
        
        # Parse confidence (categorical for backwards compat)
        confidence = parse_confidence(response.get("confidence", "low"))
        
        # Phase 6: Also get numeric confidence if provided
        confidence_raw = response.get("confidence_float")
        if confidence_raw is not None:
            try:
                confidence_float = float(confidence_raw)
                confidence_float = max(0.0, min(1.0, confidence_float))
            except (TypeError, ValueError):
                confidence_float = confidence_to_float(confidence)
        else:
            # Fallback: derive from categorical
            confidence_float = confidence_to_float(confidence)
        
        reasoning = response.get("reasoning", "")
        flags = response.get("flags", [])
        
        if not isinstance(flags, list):
            flags = []
        
        # Phase 6: Check for risk_veto (only RiskAgent should set this)
        risk_veto = bool(response.get("risk_veto", False))
        
        sig = AgentSignal(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            risk_veto=risk_veto,
            flags=flags,
        )
        sig.confidence_float = confidence_float
        return sig
    
    def _mock_response(self, symbol: str, input_data: dict) -> AgentSignal:
        """
        Generate mock response for testing without LLM.
        
        Subclasses should override for more realistic mocks.
        
        Args:
            symbol: Trading symbol.
            input_data: Prepared input (for context).
        
        Returns:
            Mock AgentSignal (always HOLD with LOW confidence).
        """
        sig = AgentSignal(
            agent_name=self.name,
            signal="HOLD",
            confidence=ConfidenceLevel.LOW,
            reasoning=f"Mock response for {symbol} (no LLM client)",
            data_status=DataStatus.REAL,
            flags=["MOCK_MODE"],
        )
        sig.confidence_float = 0.3
        return sig
