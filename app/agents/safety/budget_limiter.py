"""
Budget Limiter for LLM API calls.

Phase 2: Controls rate and cost of LLM API usage.
Prevents runaway costs and rate limit issues.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from typing import Tuple


logger = logging.getLogger(__name__)


@dataclass
class BudgetLimiter:
    """
    Rate and cost limiter for LLM API calls.
    
    Tracks:
    - Calls per minute (rate limiting)
    - Cost per day (budget control)
    - Weekend check (no calls on Sat/Sun)
    
    Returns status that gets logged to parallel_decisions.budget_status.
    """
    
    # Limits
    max_calls_per_minute: int = 30
    max_cost_per_day: float = 5.0  # USD
    
    # Cost per call (approximate for Claude/GPT-4o-mini)
    default_cost_per_call: float = 0.002  # ~$0.002 per call
    
    # Weekend check (can be disabled for testing)
    check_weekend: bool = True
    
    # Counters
    calls_this_minute: int = field(default=0)
    cost_today: float = field(default=0.0)
    
    # Timestamps for reset logic
    minute_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    day_start: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    
    def can_call(self) -> Tuple[bool, str]:
        """
        Check if an LLM call is allowed.
        
        Returns:
            (allowed, status) where status is one of:
            - "OK": Call allowed
            - "RATE_LIMIT": Too many calls this minute
            - "BUDGET_LIMIT": Daily budget exceeded
            - "WEEKEND": Market closed on weekends
        """
        self._reset_if_needed()
        
        # Check weekend (Saturday=5, Sunday=6)
        if self.check_weekend:
            now = datetime.now(timezone.utc)
            if now.weekday() in (5, 6):
                logger.debug("BudgetLimiter: WEEKEND - market closed, skipping LLM calls")
                return False, "WEEKEND"
        
        if self.calls_this_minute >= self.max_calls_per_minute:
            logger.warning(
                f"BudgetLimiter: RATE_LIMIT - {self.calls_this_minute}/{self.max_calls_per_minute} calls/min"
            )
            return False, "RATE_LIMIT"
        
        if self.cost_today >= self.max_cost_per_day:
            logger.warning(
                f"BudgetLimiter: BUDGET_LIMIT - ${self.cost_today:.2f}/${self.max_cost_per_day:.2f} today"
            )
            return False, "BUDGET_LIMIT"
        
        return True, "OK"
    
    def record_call(self, cost: float | None = None) -> None:
        """
        Record that an LLM call was made.
        
        Args:
            cost: Cost of the call in USD. Uses default if None.
        """
        self._reset_if_needed()
        
        actual_cost = cost if cost is not None else self.default_cost_per_call
        self.calls_this_minute += 1
        self.cost_today += actual_cost
        
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"BudgetLimiter: call recorded - "
                f"{self.calls_this_minute}/{self.max_calls_per_minute} calls/min, "
                f"${self.cost_today:.3f}/${self.max_cost_per_day:.2f} today"
            )
    
    def get_status(self) -> str:
        """Get current budget status for logging."""
        allowed, status = self.can_call()
        return status
    
    def get_stats(self) -> dict:
        """Get detailed stats for monitoring."""
        self._reset_if_needed()
        now = datetime.now(timezone.utc)
        is_weekend = now.weekday() in (5, 6)
        return {
            "calls_this_minute": self.calls_this_minute,
            "max_calls_per_minute": self.max_calls_per_minute,
            "cost_today_usd": round(self.cost_today, 4),
            "max_cost_per_day_usd": self.max_cost_per_day,
            "budget_remaining_usd": round(self.max_cost_per_day - self.cost_today, 4),
            "rate_remaining": self.max_calls_per_minute - self.calls_this_minute,
            "is_weekend": is_weekend,
            "day_of_week": now.strftime("%A"),
        }
    
    def _reset_if_needed(self) -> None:
        """Reset counters if minute/day has changed."""
        now = datetime.now(timezone.utc)
        
        # Reset minute counter
        if now.minute != self.minute_start.minute or now.hour != self.minute_start.hour:
            if self.calls_this_minute > 0 and logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"BudgetLimiter: minute reset, was {self.calls_this_minute} calls")
            self.calls_this_minute = 0
            self.minute_start = now
        
        # Reset daily counter
        if now.date() != self.day_start:
            if self.cost_today > 0:
                logger.info(f"BudgetLimiter: day reset, spent ${self.cost_today:.2f} yesterday")
            self.cost_today = 0.0
            self.day_start = now.date()
