"""
Agent Performance Tracker Module.

v2.0 Updates:
- Weighted PnL (larger wins/losses have more impact)
- Time decay (recent trades matter more)
- Confidence-weighted scoring
- Persistence support

Source: Adapted from RL feedback loop concepts
Integration: Used by Aggregator for dynamic weight adjustment.
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class TradeOutcome:
    """Result of a closed trade"""
    trade_id: str
    symbol: str
    direction: str  # "BUY" or "SELL"
    entry_price: float
    exit_price: float
    pnl: float  # Absolute PnL
    pnl_pips: float  # PnL in pips
    agent_votes: Dict[str, str]  # {'TechnicalAgent': 'LONG', ...}
    agent_confidences: Dict[str, float]  # {'TechnicalAgent': 0.75, ...}
    final_signal: str  # Final decision that was executed
    hold_time_minutes: int = 0  # How long position was held
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AgentStats:
    """Detailed performance statistics for an agent"""
    correct_calls: int = 0
    incorrect_calls: int = 0
    abstains: int = 0
    
    # Weighted metrics
    weighted_correct: float = 0.0  # Sum of (confidence * pnl_weight) for correct
    weighted_incorrect: float = 0.0  # Sum of (confidence * pnl_weight) for incorrect
    
    # PnL tracking
    total_pnl_when_followed: float = 0.0  # PnL when agent's advice was followed
    total_pnl_when_ignored: float = 0.0   # PnL when agent's advice was ignored
    
    # Recent performance (last N trades)
    recent_accuracy: float = 0.5
    
    @property
    def total_calls(self) -> int:
        return self.correct_calls + self.incorrect_calls
    
    @property
    def accuracy(self) -> float:
        if self.total_calls == 0:
            return 0.5  # Default 50%
        return self.correct_calls / self.total_calls
    
    @property
    def weighted_accuracy(self) -> float:
        """Accuracy weighted by confidence and PnL"""
        total = self.weighted_correct + self.weighted_incorrect
        if total == 0:
            return 0.5
        return self.weighted_correct / total
    
    @property
    def value_added(self) -> float:
        """Net value added by following this agent"""
        return self.total_pnl_when_followed - self.total_pnl_when_ignored


@dataclass 
class TrackerConfig:
    """Configuration for performance tracking"""
    # Learning rate
    base_learning_rate: float = 0.02  # 2% adjustment per trade
    max_learning_rate: float = 0.05   # Cap for high-confidence trades
    
    # Weight bounds
    min_weight: float = 0.05  # Minimum 5% weight
    max_weight: float = 0.40  # Maximum 40% weight
    
    # Time decay
    half_life_days: float = 14.0  # After 14 days, impact is halved
    
    # PnL weighting
    pnl_cap_multiplier: float = 3.0  # Cap PnL impact at 3x average
    min_trades_for_pnl_weight: int = 5  # Need 5 trades to calculate average
    
    # Activation
    min_trades_for_adjustment: int = 10  # Minimum trades before adjusting weights
    recent_window: int = 20  # Last N trades for recent accuracy
    
    # Persistence
    lookback_days: int = 30  # Only consider trades from this period


class AgentPerformanceTracker:
    """
    Advanced agent performance tracker with weighted PnL and time decay.
    
    Features:
    - PnL-weighted learning (big wins/losses matter more)
    - Time decay (recent performance matters more)
    - Confidence weighting (high-confidence calls matter more)
    - Value-added tracking
    
    Usage:
    ```python
    tracker = AgentPerformanceTracker()
    
    # After trade closes
    outcome = TradeOutcome(
        trade_id="123",
        symbol="EURUSD",
        direction="BUY",
        entry_price=1.0850,
        exit_price=1.0900,
        pnl=50.0,
        pnl_pips=50,
        agent_votes={"TechnicalAgent": "LONG", "RiskAgent": "HOLD"},
        agent_confidences={"TechnicalAgent": 0.75, "RiskAgent": 0.6},
        final_signal="LONG"
    )
    tracker.record_outcome(outcome)
    
    # Get adjusted weights
    weights = tracker.get_adjusted_weights()
    ```
    """
    
    DEFAULT_WEIGHTS = {
        'TechnicalAgent': 0.25,
        'MacroAgent': 0.20,
        'SentimentAgent': 0.15,
        'CorrelationAgent': 0.15,
        'RiskAgent': 0.25
    }
    
    def __init__(self, config: Optional[TrackerConfig] = None):
        self.config = config or TrackerConfig()
        self.weights = self.DEFAULT_WEIGHTS.copy()
        self.outcomes: List[TradeOutcome] = []
        self.agent_stats: Dict[str, AgentStats] = defaultdict(AgentStats)
        self._average_pnl: float = 0.0
        self._pnl_std: float = 0.0
    
    def _calculate_time_decay(self, timestamp: datetime) -> float:
        """
        Calculate time decay factor using exponential decay.
        
        Factor = 0.5 ^ (days_ago / half_life)
        - Today: 1.0
        - half_life days ago: 0.5
        - 2x half_life days ago: 0.25
        """
        now = datetime.now(timezone.utc)
        days_ago = (now - timestamp).total_seconds() / 86400
        
        if days_ago < 0:
            days_ago = 0
        
        decay = math.pow(0.5, days_ago / self.config.half_life_days)
        return decay
    
    def _calculate_pnl_weight(self, pnl: float) -> float:
        """
        Calculate PnL weight factor.
        
        Larger absolute PnL = more impact, but capped.
        Uses sigmoid-like scaling to prevent outliers from dominating.
        """
        if len(self.outcomes) < self.config.min_trades_for_pnl_weight:
            return 1.0  # No weighting with insufficient data
        
        # Update average PnL
        pnl_values = [abs(o.pnl) for o in self.outcomes[-50:]]  # Last 50 trades
        self._average_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else 1.0
        
        if self._average_pnl == 0:
            return 1.0
        
        # Ratio of this PnL to average
        ratio = abs(pnl) / self._average_pnl
        
        # Cap at configured multiplier
        capped_ratio = min(ratio, self.config.pnl_cap_multiplier)
        
        # Scale to 0.5 - 2.0 range (small trades still count, big trades count more)
        weight = 0.5 + (capped_ratio / self.config.pnl_cap_multiplier) * 1.5
        
        return weight
    
    def _calculate_learning_rate(
        self, 
        confidence: float, 
        pnl_weight: float, 
        time_decay: float
    ) -> float:
        """Calculate effective learning rate for this trade"""
        base = self.config.base_learning_rate
        
        # Scale by confidence (0.5-1.0 range mapped to 0.5-1.5x)
        confidence_factor = 0.5 + confidence
        
        # Combine all factors
        effective_rate = base * confidence_factor * pnl_weight * time_decay
        
        # Cap at max learning rate
        return min(effective_rate, self.config.max_learning_rate)
    
    def record_outcome(self, outcome: TradeOutcome) -> Dict[str, float]:
        """
        Record trade outcome and update agent weights.
        
        Args:
            outcome: TradeOutcome with all trade details
            
        Returns:
            Dict of weight changes per agent
        """
        self.outcomes.append(outcome)
        
        is_win = outcome.pnl > 0
        trade_direction = "LONG" if outcome.direction == "BUY" else "SHORT"
        
        # Calculate global factors
        time_decay = self._calculate_time_decay(outcome.timestamp)
        pnl_weight = self._calculate_pnl_weight(outcome.pnl)
        
        weight_changes: Dict[str, float] = {}
        
        for agent_name, vote in outcome.agent_votes.items():
            if agent_name not in self.weights:
                continue
            
            stats = self.agent_stats[agent_name]
            confidence = outcome.agent_confidences.get(agent_name, 0.5)
            
            # Calculate effective learning rate
            learning_rate = self._calculate_learning_rate(confidence, pnl_weight, time_decay)
            
            # Handle HOLD/ABSTAIN
            if vote in ('HOLD', 'ABSTAIN'):
                stats.abstains += 1
                
                # If agent abstained and trade lost, that's partially good
                if not is_win:
                    stats.weighted_correct += 0.3 * time_decay * pnl_weight
                    stats.total_pnl_when_ignored += outcome.pnl
                continue
            
            # Agent gave directional signal
            agent_agreed = (vote == trade_direction)
            
            if agent_agreed:
                # Agent agreed with executed direction
                stats.total_pnl_when_followed += outcome.pnl
                
                if is_win:
                    # Correct call - reward
                    stats.correct_calls += 1
                    stats.weighted_correct += confidence * time_decay * pnl_weight
                    delta = +learning_rate
                else:
                    # Agreed but lost - partial penalty
                    stats.incorrect_calls += 1
                    stats.weighted_incorrect += confidence * time_decay * pnl_weight
                    delta = -learning_rate * 0.5
            else:
                # Agent disagreed with executed direction
                stats.total_pnl_when_ignored += outcome.pnl
                
                if is_win:
                    # Agent was wrong (would have missed winning trade)
                    stats.incorrect_calls += 1
                    stats.weighted_incorrect += confidence * time_decay * pnl_weight
                    delta = -learning_rate
                else:
                    # Agent was right (correctly avoided losing trade)
                    stats.correct_calls += 1
                    stats.weighted_correct += confidence * time_decay * pnl_weight
                    delta = +learning_rate * 0.5
            
            # Apply weight change
            old_weight = self.weights[agent_name]
            new_weight = max(self.config.min_weight, 
                           min(self.config.max_weight, old_weight + delta))
            self.weights[agent_name] = new_weight
            weight_changes[agent_name] = new_weight - old_weight
        
        # Normalize weights
        self._normalize_weights()
        
        # Update recent accuracy
        self._update_recent_accuracy()
        
        logger.info(
            f"Recorded outcome: {outcome.symbol} PnL={outcome.pnl:.2f} "
            f"pnl_weight={pnl_weight:.2f} time_decay={time_decay:.2f}"
        )
        
        return weight_changes
    
    def _normalize_weights(self) -> None:
        """Normalize weights to sum to 1.0"""
        total = sum(self.weights.values())
        if total > 0:
            for agent in self.weights:
                self.weights[agent] /= total
    
    def _update_recent_accuracy(self) -> None:
        """Update recent accuracy for each agent"""
        if len(self.outcomes) < 3:
            return
        
        recent = self.outcomes[-self.config.recent_window:]
        
        for agent_name in self.weights.keys():
            correct = 0
            total = 0
            
            for outcome in recent:
                vote = outcome.agent_votes.get(agent_name)
                if vote in ('HOLD', 'ABSTAIN', None):
                    continue
                
                trade_dir = "LONG" if outcome.direction == "BUY" else "SHORT"
                is_win = outcome.pnl > 0
                agreed = (vote == trade_dir)
                
                total += 1
                if (agreed and is_win) or (not agreed and not is_win):
                    correct += 1
            
            if total > 0:
                self.agent_stats[agent_name].recent_accuracy = correct / total
    
    def get_adjusted_weights(self) -> Dict[str, float]:
        """
        Get current adjusted weights.
        
        Returns default weights if not enough trades.
        """
        total_trades = len(self.outcomes)
        
        if total_trades < self.config.min_trades_for_adjustment:
            logger.debug(f"Insufficient trades ({total_trades}) for weight adjustment")
            return self.DEFAULT_WEIGHTS.copy()
        
        return self.weights.copy()
    
    def get_agent_metrics(self, agent_name: str) -> Dict[str, Any]:
        """Get detailed metrics for an agent"""
        stats = self.agent_stats[agent_name]
        weight = self.weights.get(agent_name, 0.0)
        default_weight = self.DEFAULT_WEIGHTS.get(agent_name, 0.0)
        
        return {
            "current_weight": round(weight, 4),
            "default_weight": default_weight,
            "weight_change": round(weight - default_weight, 4),
            "accuracy": round(stats.accuracy, 3),
            "weighted_accuracy": round(stats.weighted_accuracy, 3),
            "recent_accuracy": round(stats.recent_accuracy, 3),
            "correct_calls": stats.correct_calls,
            "incorrect_calls": stats.incorrect_calls,
            "abstains": stats.abstains,
            "value_added": round(stats.value_added, 2),
            "pnl_when_followed": round(stats.total_pnl_when_followed, 2),
            "pnl_when_ignored": round(stats.total_pnl_when_ignored, 2),
        }
    
    def get_performance_report(self) -> Dict[str, Any]:
        """Get comprehensive performance report"""
        report = {
            "total_trades": len(self.outcomes),
            "min_trades_required": self.config.min_trades_for_adjustment,
            "weights_active": len(self.outcomes) >= self.config.min_trades_for_adjustment,
            "current_weights": {k: round(v, 4) for k, v in self.weights.items()},
            "default_weights": self.DEFAULT_WEIGHTS.copy(),
            "agents": {}
        }
        
        for agent_name in self.weights.keys():
            report["agents"][agent_name] = self.get_agent_metrics(agent_name)
        
        # Calculate overall stats
        if self.outcomes:
            total_pnl = sum(o.pnl for o in self.outcomes)
            winning = sum(1 for o in self.outcomes if o.pnl > 0)
            report["overall"] = {
                "total_pnl": round(total_pnl, 2),
                "win_rate": round(winning / len(self.outcomes), 3),
                "avg_pnl": round(total_pnl / len(self.outcomes), 2),
            }
        
        return report
    
    def cleanup_old_outcomes(self) -> int:
        """Remove outcomes older than lookback_days"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.lookback_days)
        original = len(self.outcomes)
        self.outcomes = [o for o in self.outcomes if o.timestamp > cutoff]
        removed = original - len(self.outcomes)
        
        if removed > 0:
            logger.info(f"Cleaned up {removed} old trade outcomes")
        
        return removed
    
    def reset(self) -> None:
        """Reset tracker to default state"""
        self.weights = self.DEFAULT_WEIGHTS.copy()
        self.outcomes.clear()
        self.agent_stats.clear()
        self._average_pnl = 0.0
        logger.info("Performance tracker reset")
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize tracker state for persistence"""
        return {
            "weights": self.weights.copy(),
            "outcomes": [
                {
                    "trade_id": o.trade_id,
                    "symbol": o.symbol,
                    "direction": o.direction,
                    "pnl": o.pnl,
                    "pnl_pips": o.pnl_pips,
                    "agent_votes": o.agent_votes,
                    "agent_confidences": o.agent_confidences,
                    "final_signal": o.final_signal,
                    "timestamp": o.timestamp.isoformat(),
                }
                for o in self.outcomes[-100:]  # Keep last 100
            ],
            "agent_stats": {
                name: {
                    "correct": stats.correct_calls,
                    "incorrect": stats.incorrect_calls,
                    "abstains": stats.abstains,
                    "weighted_correct": stats.weighted_correct,
                    "weighted_incorrect": stats.weighted_incorrect,
                    "pnl_followed": stats.total_pnl_when_followed,
                    "pnl_ignored": stats.total_pnl_when_ignored,
                }
                for name, stats in self.agent_stats.items()
            },
            "config": {
                "half_life_days": self.config.half_life_days,
                "min_trades": self.config.min_trades_for_adjustment,
            }
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentPerformanceTracker':
        """Restore tracker from serialized state"""
        tracker = cls()
        
        # Restore weights
        tracker.weights = data.get("weights", cls.DEFAULT_WEIGHTS.copy())
        
        # Restore agent stats
        for name, stats_data in data.get("agent_stats", {}).items():
            stats = AgentStats(
                correct_calls=stats_data.get("correct", 0),
                incorrect_calls=stats_data.get("incorrect", 0),
                abstains=stats_data.get("abstains", 0),
                weighted_correct=stats_data.get("weighted_correct", 0.0),
                weighted_incorrect=stats_data.get("weighted_incorrect", 0.0),
                total_pnl_when_followed=stats_data.get("pnl_followed", 0.0),
                total_pnl_when_ignored=stats_data.get("pnl_ignored", 0.0),
            )
            tracker.agent_stats[name] = stats
        
        # Restore outcomes (simplified - just for counting)
        for o_data in data.get("outcomes", []):
            try:
                outcome = TradeOutcome(
                    trade_id=o_data["trade_id"],
                    symbol=o_data["symbol"],
                    direction=o_data["direction"],
                    entry_price=0,  # Not stored
                    exit_price=0,
                    pnl=o_data["pnl"],
                    pnl_pips=o_data.get("pnl_pips", 0),
                    agent_votes=o_data["agent_votes"],
                    agent_confidences=o_data.get("agent_confidences", {}),
                    final_signal=o_data["final_signal"],
                    timestamp=datetime.fromisoformat(o_data["timestamp"])
                )
                tracker.outcomes.append(outcome)
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to restore outcome: {e}")
        
        return tracker


# === Testing ===

if __name__ == "__main__":
    print("Testing AgentPerformanceTracker v2.0 (Weighted PnL + Time Decay)")
    print("=" * 70)
    
    tracker = AgentPerformanceTracker()
    
    # Simulate some trades
    trades = [
        # Big win, TechnicalAgent correct
        TradeOutcome(
            trade_id="1", symbol="EURUSD", direction="BUY",
            entry_price=1.08, exit_price=1.09, pnl=100, pnl_pips=100,
            agent_votes={"TechnicalAgent": "LONG", "RiskAgent": "HOLD"},
            agent_confidences={"TechnicalAgent": 0.8, "RiskAgent": 0.6},
            final_signal="LONG",
            timestamp=datetime.now(timezone.utc) - timedelta(days=1)
        ),
        # Small loss, TechnicalAgent wrong
        TradeOutcome(
            trade_id="2", symbol="GBPUSD", direction="SELL",
            entry_price=1.27, exit_price=1.275, pnl=-25, pnl_pips=-50,
            agent_votes={"TechnicalAgent": "LONG", "RiskAgent": "HOLD"},
            agent_confidences={"TechnicalAgent": 0.6, "RiskAgent": 0.5},
            final_signal="SHORT",
            timestamp=datetime.now(timezone.utc) - timedelta(hours=12)
        ),
        # Medium win, RiskAgent correctly abstained on risky trade that won anyway
        TradeOutcome(
            trade_id="3", symbol="USDJPY", direction="BUY",
            entry_price=157, exit_price=158, pnl=50, pnl_pips=100,
            agent_votes={"TechnicalAgent": "LONG", "RiskAgent": "HOLD"},
            agent_confidences={"TechnicalAgent": 0.7, "RiskAgent": 0.8},
            final_signal="LONG",
            timestamp=datetime.now(timezone.utc)
        ),
    ]
    
    for trade in trades:
        changes = tracker.record_outcome(trade)
        print(f"\nTrade {trade.trade_id}: {trade.symbol} PnL=${trade.pnl}")
        for agent, change in changes.items():
            print(f"  {agent}: {change:+.4f}")
    
    print("\n" + "=" * 70)
    print("Performance Report:")
    report = tracker.get_performance_report()
    
    print(f"\nTotal trades: {report['total_trades']}")
    print(f"Weights active: {report['weights_active']}")
    
    print("\nCurrent Weights:")
    for agent, weight in report['current_weights'].items():
        default = report['default_weights'][agent]
        change = weight - default
        print(f"  {agent}: {weight:.3f} ({change:+.3f})")
    
    print("\nAgent Details:")
    for agent, metrics in report['agents'].items():
        print(f"\n  {agent}:")
        print(f"    Accuracy: {metrics['accuracy']:.1%} (weighted: {metrics['weighted_accuracy']:.1%})")
        print(f"    Calls: {metrics['correct_calls']}W / {metrics['incorrect_calls']}L / {metrics['abstains']}A")
        print(f"    Value Added: ${metrics['value_added']:.2f}")
