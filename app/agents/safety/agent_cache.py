"""
Agent Cache for LLM responses.

Phase 2: Caches LLM responses to reduce API calls and costs.
Uses time-bucketed keys for natural expiration.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple


logger = logging.getLogger(__name__)


@dataclass
class CachedResponse:
    """A cached agent response with metadata."""
    signal: str
    confidence: str
    reasoning: str
    flags: list
    cached_at: datetime
    agent_name: str
    symbol: str


@dataclass
class AgentCache:
    """
    TTL-based cache for LLM agent responses.
    
    Key design:
    - Keys are bucketed by 15-minute intervals
    - Same inputs within a bucket return cached response
    - Reduces API calls for repeated analysis
    
    Returns cache_hits count for parallel_decisions.
    """
    
    ttl_minutes: int = 15
    
    # Internal storage: key -> (response, cached_at)
    _cache: Dict[str, CachedResponse] = field(default_factory=dict)
    
    # Stats
    _hits: int = field(default=0)
    _misses: int = field(default=0)
    
    def get_key(self, agent_name: str, symbol: str) -> str:
        """
        Generate cache key with time bucket.
        
        Keys are bucketed by TTL intervals (default 15 min):
        - 10:00-10:14 → bucket 10:00
        - 10:15-10:29 → bucket 10:15
        
        Args:
            agent_name: Name of the agent (e.g., "TechnicalAgent").
            symbol: Trading symbol (e.g., "EURUSD").
        
        Returns:
            Cache key string.
        """
        now = datetime.now(timezone.utc)
        bucket_minutes = (now.minute // self.ttl_minutes) * self.ttl_minutes
        bucket = now.replace(minute=bucket_minutes, second=0, microsecond=0)
        return f"{agent_name}:{symbol}:{bucket.isoformat()}"
    
    def get(self, agent_name: str, symbol: str) -> Optional[CachedResponse]:
        """
        Get cached response if available and not expired.
        
        Args:
            agent_name: Name of the agent.
            symbol: Trading symbol.
        
        Returns:
            CachedResponse if hit, None if miss.
        """
        key = self.get_key(agent_name, symbol)
        
        if key not in self._cache:
            self._misses += 1
            return None
        
        cached = self._cache[key]
        age = datetime.now(timezone.utc) - cached.cached_at
        
        if age > timedelta(minutes=self.ttl_minutes):
            # Expired (shouldn't happen with bucketed keys, but safety check)
            del self._cache[key]
            self._misses += 1
            return None
        
        self._hits += 1
        logger.debug(f"AgentCache HIT: {key} (age={age.seconds}s)")
        return cached
    
    def set(
        self,
        agent_name: str,
        symbol: str,
        signal: str,
        confidence: str,
        reasoning: str,
        flags: list | None = None,
    ) -> None:
        """
        Cache an agent response.
        
        Args:
            agent_name: Name of the agent.
            symbol: Trading symbol.
            signal: Signal value (LONG, SHORT, HOLD).
            confidence: Confidence level (low, medium, high).
            reasoning: LLM reasoning text.
            flags: Optional list of flags.
        """
        key = self.get_key(agent_name, symbol)
        
        self._cache[key] = CachedResponse(
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            flags=flags or [],
            cached_at=datetime.now(timezone.utc),
            agent_name=agent_name,
            symbol=symbol,
        )
        
        logger.debug(f"AgentCache SET: {key}")
    
    def get_hits(self) -> int:
        """Get total cache hits for this session."""
        return self._hits
    
    def get_stats(self) -> dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total": total,
            "hit_rate": round(hit_rate, 3),
            "entries": len(self._cache),
            "ttl_minutes": self.ttl_minutes,
        }
    
    def clear_expired(self) -> int:
        """
        Remove expired entries from cache.
        
        Should be called periodically to prevent memory growth.
        
        Returns:
            Number of entries removed.
        """
        now = datetime.now(timezone.utc)
        max_age = timedelta(minutes=self.ttl_minutes)
        
        expired = [
            key for key, cached in self._cache.items()
            if now - cached.cached_at > max_age
        ]
        
        for key in expired:
            del self._cache[key]
        
        if expired:
            logger.debug(f"AgentCache: cleared {len(expired)} expired entries")
        
        return len(expired)
    
    def clear_all(self) -> None:
        """Clear all cache entries."""
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"AgentCache: cleared all {count} entries")
