"""
Source Health Monitor.

Phase 1: Monitors freshness of all data sources.
Blocks trading if critical sources are stale.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.source_health import SourceHealth


logger = logging.getLogger(__name__)


class SourceHealthMonitor:
    """
    Monitors health of all data sources.
    
    Responsibilities:
    - Track last update time for each source
    - Determine if data is stale
    - Block trading if critical sources unavailable
    - Provide health info for parallel_decisions.source_health
    """
    
    # Maximum acceptable staleness per source (in minutes)
    MAX_STALENESS = {
        "ibkr_ohlcv": 5.0,           # Critical - blocks all trading
        "economic_calendar": 60.0,    # 1 hour
        "cot_reports": 10080.0,       # 7 days (weekly data)
        "dxy_index": 15.0,            # 15 minutes
    }
    
    # Which agents require which sources
    AGENT_SOURCES = {
        "TechnicalAgent": ["ibkr_ohlcv"],
        "MacroAgent": ["economic_calendar"],
        "SentimentAgent": ["cot_reports"],
        "CorrelationAgent": ["dxy_index", "ibkr_ohlcv"],
        "RiskAgent": ["ibkr_ohlcv"],
    }
    
    # Critical sources that block all trading if stale
    CRITICAL_SOURCES = {"ibkr_ohlcv"}
    
    def __init__(self):
        """Initialize monitor."""
        self._health_cache: Dict[str, SourceHealth] = {}
        self._last_check: Optional[datetime] = None
    
    def check_all(
        self,
        data_sources: Dict[str, Any],
    ) -> Dict[str, SourceHealth]:
        """
        Check health of all data sources.
        
        Args:
            data_sources: Dict mapping source_name to fetcher instance.
                         Each fetcher must have get_health() method.
        
        Returns:
            Dict mapping source_name to SourceHealth.
        """
        health_map = {}
        
        for source_name, fetcher in data_sources.items():
            try:
                if hasattr(fetcher, "get_health"):
                    health = fetcher.get_health()
                else:
                    # Fallback for sources without get_health
                    health = self._infer_health(source_name, fetcher)
                health_map[source_name] = health
            except Exception as e:
                logger.warning(f"Failed to get health for {source_name}: {e}")
                health_map[source_name] = SourceHealth.unavailable(source_name, str(e))
        
        self._health_cache = health_map
        self._last_check = datetime.now(timezone.utc)
        
        # Log any stale sources
        stale = self.get_all_stale(health_map)
        if stale:
            logger.warning(f"Stale data sources: {stale}")
        
        return health_map
    
    def should_block_trading(self, health: Optional[Dict[str, SourceHealth]] = None) -> bool:
        """
        Check if trading should be blocked due to critical source issues.
        
        Args:
            health: Health map (uses cache if None).
        
        Returns:
            True if any critical source is stale/unavailable.
        """
        if health is None:
            health = self._health_cache
        
        for source_name in self.CRITICAL_SOURCES:
            if source_name not in health:
                logger.warning(f"Critical source {source_name} not in health map")
                return True
            
            source_health = health[source_name]
            max_age = self.MAX_STALENESS.get(source_name, 5.0)
            
            if not source_health.is_healthy(max_age):
                logger.warning(
                    f"Critical source {source_name} is unhealthy: "
                    f"available={source_health.is_available}, "
                    f"stale={source_health.is_stale(max_age)}, "
                    f"coverage={source_health.coverage}"
                )
                return True
        
        return False
    
    def get_stale_for_agent(
        self,
        agent_name: str,
        health: Optional[Dict[str, SourceHealth]] = None,
    ) -> List[str]:
        """
        Get list of stale sources required by a specific agent.
        
        Args:
            agent_name: Name of the agent (e.g., "TechnicalAgent").
            health: Health map (uses cache if None).
        
        Returns:
            List of stale source names. Empty if all sources are fresh.
        """
        if health is None:
            health = self._health_cache
        
        required = self.AGENT_SOURCES.get(agent_name, [])
        stale = []
        
        for source_name in required:
            if source_name not in health:
                stale.append(source_name)
                continue
            
            source_health = health[source_name]
            max_age = self.MAX_STALENESS.get(source_name, 60.0)
            
            if source_health.is_stale(max_age):
                stale.append(source_name)
        
        return stale
    
    def get_all_stale(self, health: Optional[Dict[str, SourceHealth]] = None) -> List[str]:
        """Get all stale sources."""
        if health is None:
            health = self._health_cache
        
        stale = []
        for source_name, source_health in health.items():
            max_age = self.MAX_STALENESS.get(source_name, 60.0)
            if source_health.is_stale(max_age):
                stale.append(source_name)
        
        return stale
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert health status to dict for parallel_decisions.source_health.
        
        Returns:
            Dict suitable for JSONB storage.
        """
        return {
            "checked_at": self._last_check.isoformat() if self._last_check else None,
            "sources": {
                name: health.to_dict() 
                for name, health in self._health_cache.items()
            },
            "stale_sources": self.get_all_stale(),
            "trading_blocked": self.should_block_trading(),
        }
    
    def _infer_health(self, source_name: str, fetcher: Any) -> SourceHealth:
        """
        Infer health from fetcher state when get_health() is not available.
        
        Args:
            source_name: Name of the source.
            fetcher: Fetcher instance.
        
        Returns:
            Inferred SourceHealth.
        """
        now = datetime.now(timezone.utc)
        
        # Try to get last_fetch from fetcher
        last_fetch = getattr(fetcher, "_last_fetch", None)
        last_error = getattr(fetcher, "_last_error", None)
        
        if last_fetch is None:
            return SourceHealth.unavailable(source_name, "Never fetched")
        
        if last_error:
            return SourceHealth(
                source_name=source_name,
                is_available=False,
                last_update=last_fetch,
                staleness_minutes=(now - last_fetch).total_seconds() / 60.0,
                coverage=0.0,
                error_message=last_error,
            )
        
        return SourceHealth.fresh(source_name, last_fetch)
