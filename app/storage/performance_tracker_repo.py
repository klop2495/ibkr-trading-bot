"""
Performance Tracker Repository.

Phase 7: Persistence for AgentPerformanceTracker state.

Stores/loads tracker state to/from Supabase.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.storage.db import SupabaseDB


logger = logging.getLogger(__name__)


class PerformanceTrackerRepo:
    """
    Repository for persisting AgentPerformanceTracker state.
    
    Phase 7: Enables tracker state to survive restarts.
    
    Table: performance_tracker_state
    Schema:
    - id: uuid (primary key)
    - key: text (unique) - typically "default" for main tracker
    - state_json: jsonb - full tracker state
    - weights: jsonb - current weights (for quick access)
    - total_trades: int - count of recorded trades
    - updated_at: timestamptz
    - created_at: timestamptz
    """
    
    table = "performance_tracker_state"
    
    def __init__(self, db: SupabaseDB):
        self.db = db
    
    def save_state(
        self,
        state: Dict[str, Any],
        key: str = "default",
    ) -> bool:
        """
        Save tracker state to database.
        
        Args:
            state: Dict from AgentPerformanceTracker.to_dict()
            key: Identifier for this tracker instance (default: "default")
        
        Returns:
            True if saved successfully.
        """
        try:
            # Extract summary info for quick queries
            weights = state.get("weights", {})
            total_trades = len(state.get("outcomes", []))
            
            payload = {
                "key": key,
                "state_json": state,
                "weights": weights,
                "total_trades": total_trades,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            
            # Upsert (update if exists, insert if not)
            res = (
                self.db.client.table(self.table)
                .upsert(payload, on_conflict="key")
                .execute()
            )
            
            logger.info(f"Performance tracker state saved: key={key} trades={total_trades}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save performance tracker state: {e}")
            return False
    
    def load_state(self, key: str = "default") -> Optional[Dict[str, Any]]:
        """
        Load tracker state from database.
        
        Args:
            key: Identifier for tracker instance
        
        Returns:
            State dict or None if not found.
        """
        try:
            res = (
                self.db.client.table(self.table)
                .select("state_json")
                .eq("key", key)
                .limit(1)
                .execute()
            )
            
            if res.data:
                state = res.data[0].get("state_json")
                if state:
                    logger.info(f"Performance tracker state loaded: key={key}")
                    return state
            
            logger.info(f"No performance tracker state found for key={key}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to load performance tracker state: {e}")
            return None
    
    def get_current_weights(self, key: str = "default") -> Optional[Dict[str, float]]:
        """
        Get current weights without loading full state.
        
        Quick query for displaying weights without loading all outcomes.
        """
        try:
            res = (
                self.db.client.table(self.table)
                .select("weights")
                .eq("key", key)
                .limit(1)
                .execute()
            )
            
            if res.data:
                return res.data[0].get("weights")
            return None
            
        except Exception as e:
            logger.error(f"Failed to get current weights: {e}")
            return None
    
    def get_summary(self, key: str = "default") -> Optional[Dict[str, Any]]:
        """
        Get summary info without full state.
        """
        try:
            res = (
                self.db.client.table(self.table)
                .select("weights, total_trades, updated_at")
                .eq("key", key)
                .limit(1)
                .execute()
            )
            
            if res.data:
                return res.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Failed to get summary: {e}")
            return None
    
    def delete_state(self, key: str = "default") -> bool:
        """
        Delete tracker state (for reset).
        """
        try:
            self.db.client.table(self.table).delete().eq("key", key).execute()
            logger.info(f"Performance tracker state deleted: key={key}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete performance tracker state: {e}")
            return False


# SQL to create table (run in Supabase SQL editor):
"""
CREATE TABLE IF NOT EXISTS performance_tracker_state (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    key TEXT UNIQUE NOT NULL DEFAULT 'default',
    state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    total_trades INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for quick lookups
CREATE INDEX IF NOT EXISTS idx_performance_tracker_key ON performance_tracker_state(key);

-- RLS policies (if needed)
ALTER TABLE performance_tracker_state ENABLE ROW LEVEL SECURITY;
"""
