"""
Response Validator for LLM outputs.

Phase 2: Validates and sanitizes LLM responses.
Blocks responses that contain forbidden content (prices, SL/TP, lot sizes).
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from app.models.confidence import ConfidenceLevel, parse_confidence


logger = logging.getLogger(__name__)


@dataclass
class ValidatedResponse:
    """A validated and sanitized agent response."""
    signal: str
    confidence: ConfidenceLevel
    reasoning: str
    flags: List[str]
    is_valid: bool
    violation_type: Optional[str] = None


@dataclass
class ResponseValidator:
    """
    Validates LLM responses to ensure safety rules.
    
    Blocks responses containing:
    - Specific prices (1.0732)
    - SL/TP recommendations
    - Position sizes (lots, units)
    - Pip calculations
    - Entry/exit prices
    
    Returns validation_failures count for parallel_decisions.
    """
    
    # Patterns that indicate forbidden content
    FORBIDDEN_PATTERNS: List[Tuple[str, str]] = field(default_factory=lambda: [
        (r"\d+\.\d{4,}", "price"),                    # 1.0732, 145.5678
        (r"(?:SL|TP|stop.?loss|take.?profit)", "sl_tp"),
        (r"\d+\.?\d*\s*(?:lot|unit|position)", "position_size"),
        (r"\d+\.?\d*\s*pip", "pip_calc"),
        (r"(?:enter|exit|buy|sell)\s+at\s+\d", "entry_price"),
        (r"target\s*(?:price|level)?\s*[:=]?\s*\d", "target_price"),
        (r"stop\s*[:=]?\s*\d", "stop_price"),
        (r"risk\s*[:=]?\s*\d+%", "risk_percent"),
    ])
    
    # Valid signals
    VALID_SIGNALS = {"LONG", "SHORT", "HOLD"}
    
    # Stats
    _validation_failures: int = field(default=0)
    _validations_total: int = field(default=0)
    
    def validate(self, response: dict) -> Tuple[bool, Optional[str]]:
        """
        Validate an LLM response.
        
        Args:
            response: Dict with signal, confidence, reasoning, etc.
        
        Returns:
            (is_valid, violation_type) where violation_type is None if valid.
        """
        self._validations_total += 1
        
        # Check reasoning for forbidden patterns
        reasoning = response.get("reasoning", "")
        
        for pattern, violation_type in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, reasoning, re.IGNORECASE):
                self._validation_failures += 1
                logger.warning(f"ResponseValidator: VIOLATION {violation_type} in reasoning")
                return False, violation_type
        
        # Validate signal
        signal = response.get("signal", "").upper()
        if signal not in self.VALID_SIGNALS:
            self._validation_failures += 1
            logger.warning(f"ResponseValidator: INVALID_SIGNAL '{signal}'")
            return False, "invalid_signal"
        
        # Validate confidence is parseable
        confidence = response.get("confidence", "")
        if not self._is_valid_confidence(confidence):
            # This is a soft failure - we can parse to LOW
            logger.debug(f"ResponseValidator: unknown confidence '{confidence}', defaulting to LOW")
        
        return True, None
    
    def sanitize(self, response: dict) -> ValidatedResponse:
        """
        Validate and sanitize an LLM response.
        
        If invalid, returns a safe HOLD response with sanitization flags.
        
        Args:
            response: Raw LLM response dict.
        
        Returns:
            ValidatedResponse with sanitized values.
        """
        is_valid, violation = self.validate(response)
        
        if not is_valid:
            return ValidatedResponse(
                signal="HOLD",
                confidence=ConfidenceLevel.LOW,
                reasoning=f"Response sanitized: {violation}",
                flags=["LLM_SANITIZED", f"VIOLATION_{violation.upper()}"],
                is_valid=False,
                violation_type=violation,
            )
        
        # Parse confidence
        conf_str = response.get("confidence", "low")
        confidence = parse_confidence(conf_str)
        
        # Get flags (ensure it's a list)
        raw_flags = response.get("flags", [])
        if isinstance(raw_flags, list):
            flags = list(raw_flags)
        else:
            flags = []
        
        return ValidatedResponse(
            signal=response.get("signal", "HOLD").upper(),
            confidence=confidence,
            reasoning=response.get("reasoning", ""),
            flags=flags,
            is_valid=True,
            violation_type=None,
        )
    
    def get_failures(self) -> int:
        """Get total validation failures for this session."""
        return self._validation_failures
    
    def get_stats(self) -> dict:
        """Get validation statistics."""
        failure_rate = (
            self._validation_failures / self._validations_total 
            if self._validations_total > 0 else 0.0
        )
        return {
            "total_validations": self._validations_total,
            "failures": self._validation_failures,
            "failure_rate": round(failure_rate, 4),
        }
    
    def _is_valid_confidence(self, value: str) -> bool:
        """Check if confidence string is a known valid value."""
        if not value:
            return False
        normalized = value.lower().strip()
        return normalized in ("low", "medium", "high", "l", "m", "h", "med", "normal")
