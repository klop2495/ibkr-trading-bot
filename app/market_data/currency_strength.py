"""
Currency Strength Meter Module.

v2.0 Updates:
- Multi-timeframe (MTF) support
- EMA smoothing instead of simple average
- Z-score normalization
- Configurable parameters

Source: Adapted from github.com/EarnForex/Currency-Strength-Lines
Integration: Used by CorrelationAgent for cross-pair analysis.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class CurrencyStrength:
    """Strength of individual currency"""
    currency: str
    strength: float  # Normalized strength (-100 to +100)
    raw_strength: float  # Raw percentage change
    trend: str  # STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH, STRONG_BEARISH
    rank: int  # 1-8 (1 = strongest)
    z_score: float = 0.0  # Standard deviations from mean
    momentum: float = 0.0  # Rate of change in strength


@dataclass
class TimeframeData:
    """Price data for a single timeframe"""
    current_prices: Dict[str, float]
    previous_prices: Dict[str, float]
    weight: float = 1.0  # Weight for MTF aggregation


@dataclass
class StrengthConfig:
    """Configuration for strength calculation"""
    # Timeframe weights (should sum to 1.0)
    timeframe_weights: Dict[str, float] = field(default_factory=lambda: {
        "M15": 0.15,
        "H1": 0.35,
        "H4": 0.35,
        "D1": 0.15
    })
    
    # EMA periods
    ema_period: int = 5  # Smoothing period for strength values
    
    # Z-score calculation
    zscore_lookback: int = 20  # Periods for mean/std calculation
    
    # Trend thresholds (in normalized units)
    strong_threshold: float = 50.0
    weak_threshold: float = 20.0
    
    # Minimum pairs required
    min_pairs_for_calculation: int = 3


class CurrencyStrengthMeter:
    """
    Multi-timeframe Currency Strength Meter with EMA smoothing.
    
    Features:
    - MTF aggregation with configurable weights
    - EMA smoothing to reduce noise
    - Z-score normalization for comparability
    - Momentum calculation for trend detection
    
    Usage:
    ```python
    meter = CurrencyStrengthMeter()
    
    # Single timeframe (simple)
    strengths = meter.calculate(current_prices, previous_prices)
    
    # Multi-timeframe (recommended)
    strengths = meter.calculate_mtf({
        "H1": TimeframeData(current_h1, previous_h1, weight=0.4),
        "H4": TimeframeData(current_h4, previous_h4, weight=0.4),
        "D1": TimeframeData(current_d1, previous_d1, weight=0.2),
    })
    
    # Get pair analysis
    analysis = meter.analyze_pair("EURUSD", strengths)
    ```
    """
    
    CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD']
    
    # All 28 major forex pairs
    ALL_PAIRS = [
        'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD',
        'EURGBP', 'EURJPY', 'EURCHF', 'EURAUD', 'EURCAD', 'EURNZD',
        'GBPJPY', 'GBPCHF', 'GBPAUD', 'GBPCAD', 'GBPNZD',
        'AUDJPY', 'AUDCHF', 'AUDCAD', 'AUDNZD',
        'CADJPY', 'CADCHF',
        'CHFJPY',
        'NZDJPY', 'NZDCHF', 'NZDCAD'
    ]
    
    def __init__(self, config: Optional[StrengthConfig] = None):
        self.config = config or StrengthConfig()
        self._pair_map = self._build_pair_map()
        self._strength_history: Dict[str, List[float]] = defaultdict(list)
        self._ema_values: Dict[str, float] = {}
    
    def _build_pair_map(self) -> Dict[str, Tuple[str, str]]:
        """Build map of pair -> (base, quote)"""
        return {pair: (pair[:3], pair[3:]) for pair in self.ALL_PAIRS}
    
    def _calculate_ema(self, current: float, previous_ema: Optional[float], period: int) -> float:
        """Calculate Exponential Moving Average"""
        if previous_ema is None:
            return current
        
        multiplier = 2.0 / (period + 1)
        return (current - previous_ema) * multiplier + previous_ema
    
    def _calculate_zscore(self, value: float, history: List[float]) -> float:
        """Calculate Z-score (standard deviations from mean)"""
        if len(history) < 2:
            return 0.0
        
        mean = sum(history) / len(history)
        variance = sum((x - mean) ** 2 for x in history) / len(history)
        std = variance ** 0.5
        
        if std == 0:
            return 0.0
        
        return (value - mean) / std
    
    def _normalize_strength(self, raw_strength: float, min_val: float, max_val: float) -> float:
        """Normalize strength to -100 to +100 range"""
        if max_val == min_val:
            return 0.0
        
        # Scale to -100 to +100
        range_val = max_val - min_val
        normalized = ((raw_strength - min_val) / range_val) * 200 - 100
        return max(-100.0, min(100.0, normalized))
    
    def _classify_trend(self, strength: float) -> str:
        """Classify strength into trend category"""
        if strength > self.config.strong_threshold:
            return "STRONG_BULLISH"
        elif strength > self.config.weak_threshold:
            return "BULLISH"
        elif strength < -self.config.strong_threshold:
            return "STRONG_BEARISH"
        elif strength < -self.config.weak_threshold:
            return "BEARISH"
        return "NEUTRAL"
    
    def calculate(
        self,
        current_prices: Dict[str, float],
        previous_prices: Dict[str, float],
        apply_ema: bool = True
    ) -> List[CurrencyStrength]:
        """
        Calculate strength of all currencies (single timeframe).
        
        Args:
            current_prices: Current pair prices {'EURUSD': 1.0850, ...}
            previous_prices: Prices from reference period
            apply_ema: Apply EMA smoothing
            
        Returns:
            List of CurrencyStrength sorted by strength (strongest first)
        """
        # Calculate raw percentage change for each pair
        changes: Dict[str, float] = {}
        for pair in self.ALL_PAIRS:
            if pair in current_prices and pair in previous_prices:
                prev = previous_prices[pair]
                curr = current_prices[pair]
                if prev > 0:
                    changes[pair] = ((curr - prev) / prev) * 100
        
        if len(changes) < self.config.min_pairs_for_calculation:
            logger.warning(f"Insufficient pairs for strength calculation: {len(changes)}")
            return []
        
        # Aggregate strength for each currency
        currency_scores: Dict[str, List[float]] = {c: [] for c in self.CURRENCIES}
        
        for pair, change in changes.items():
            if pair not in self._pair_map:
                continue
            base, quote = self._pair_map[pair]
            
            # If pair goes up, base is stronger, quote is weaker
            if base in currency_scores:
                currency_scores[base].append(change)
            if quote in currency_scores:
                currency_scores[quote].append(-change)
        
        # Calculate raw strength (average of all pair contributions)
        raw_strengths: Dict[str, float] = {}
        for currency, scores in currency_scores.items():
            if scores:
                raw_strengths[currency] = sum(scores) / len(scores)
            else:
                raw_strengths[currency] = 0.0
        
        # Apply EMA smoothing
        if apply_ema:
            for currency, raw in raw_strengths.items():
                prev_ema = self._ema_values.get(currency)
                ema = self._calculate_ema(raw, prev_ema, self.config.ema_period)
                self._ema_values[currency] = ema
                raw_strengths[currency] = ema
        
        # Update history for Z-score
        for currency, strength in raw_strengths.items():
            history = self._strength_history[currency]
            history.append(strength)
            # Keep only lookback period
            if len(history) > self.config.zscore_lookback:
                self._strength_history[currency] = history[-self.config.zscore_lookback:]
        
        # Normalize to -100 to +100
        if raw_strengths:
            min_strength = min(raw_strengths.values())
            max_strength = max(raw_strengths.values())
        else:
            min_strength = max_strength = 0.0
        
        # Build results
        results = []
        for currency in self.CURRENCIES:
            raw = raw_strengths.get(currency, 0.0)
            normalized = self._normalize_strength(raw, min_strength, max_strength)
            history = self._strength_history.get(currency, [])
            z_score = self._calculate_zscore(raw, history)
            
            # Calculate momentum (change from previous)
            momentum = 0.0
            if len(history) >= 2:
                momentum = history[-1] - history[-2]
            
            results.append(CurrencyStrength(
                currency=currency,
                strength=round(normalized, 2),
                raw_strength=round(raw, 4),
                trend=self._classify_trend(normalized),
                rank=0,
                z_score=round(z_score, 2),
                momentum=round(momentum, 4)
            ))
        
        # Sort and assign ranks
        results.sort(key=lambda x: x.strength, reverse=True)
        for i, cs in enumerate(results):
            cs.rank = i + 1
        
        return results
    
    def calculate_mtf(
        self,
        timeframe_data: Dict[str, TimeframeData]
    ) -> List[CurrencyStrength]:
        """
        Calculate strength using multiple timeframes.
        
        Args:
            timeframe_data: Dict of timeframe -> TimeframeData
            
        Returns:
            List of CurrencyStrength with weighted MTF aggregation
        """
        if not timeframe_data:
            return []
        
        # Calculate strength for each timeframe
        tf_strengths: Dict[str, Dict[str, float]] = {}
        total_weight = 0.0
        
        for tf, data in timeframe_data.items():
            # Temporarily disable EMA for individual TF calculations
            strengths = self.calculate(
                data.current_prices, 
                data.previous_prices,
                apply_ema=False
            )
            
            # Store as currency -> strength dict
            tf_strengths[tf] = {s.currency: s.raw_strength for s in strengths}
            total_weight += data.weight
        
        if total_weight == 0:
            return []
        
        # Weighted average across timeframes
        aggregated: Dict[str, float] = {c: 0.0 for c in self.CURRENCIES}
        
        for tf, data in timeframe_data.items():
            tf_dict = tf_strengths.get(tf, {})
            normalized_weight = data.weight / total_weight
            
            for currency in self.CURRENCIES:
                aggregated[currency] += tf_dict.get(currency, 0.0) * normalized_weight
        
        # Apply EMA to aggregated values
        for currency, raw in aggregated.items():
            prev_ema = self._ema_values.get(currency)
            ema = self._calculate_ema(raw, prev_ema, self.config.ema_period)
            self._ema_values[currency] = ema
            aggregated[currency] = ema
        
        # Update history
        for currency, strength in aggregated.items():
            history = self._strength_history[currency]
            history.append(strength)
            if len(history) > self.config.zscore_lookback:
                self._strength_history[currency] = history[-self.config.zscore_lookback:]
        
        # Normalize
        if aggregated:
            min_s = min(aggregated.values())
            max_s = max(aggregated.values())
        else:
            min_s = max_s = 0.0
        
        # Build results
        results = []
        for currency in self.CURRENCIES:
            raw = aggregated.get(currency, 0.0)
            normalized = self._normalize_strength(raw, min_s, max_s)
            history = self._strength_history.get(currency, [])
            z_score = self._calculate_zscore(raw, history)
            
            momentum = 0.0
            if len(history) >= 2:
                momentum = history[-1] - history[-2]
            
            results.append(CurrencyStrength(
                currency=currency,
                strength=round(normalized, 2),
                raw_strength=round(raw, 4),
                trend=self._classify_trend(normalized),
                rank=0,
                z_score=round(z_score, 2),
                momentum=round(momentum, 4)
            ))
        
        results.sort(key=lambda x: x.strength, reverse=True)
        for i, cs in enumerate(results):
            cs.rank = i + 1
        
        return results
    
    def analyze_pair(
        self,
        symbol: str,
        strengths: List[CurrencyStrength]
    ) -> Dict[str, any]:
        """
        Analyze a specific pair using currency strengths.
        
        Args:
            symbol: Pair to analyze (e.g., "EURUSD")
            strengths: Calculated currency strengths
            
        Returns:
            Analysis dict for agent context
        """
        if len(symbol) != 6:
            return self._empty_analysis()
        
        base = symbol[:3]
        quote = symbol[3:]
        
        strength_map = {s.currency: s for s in strengths}
        base_s = strength_map.get(base)
        quote_s = strength_map.get(quote)
        
        if not base_s or not quote_s:
            return self._empty_analysis()
        
        # Calculate differential
        differential = base_s.strength - quote_s.strength
        
        # Determine signal based on differential and trends
        if differential > 40 and base_s.trend in ("BULLISH", "STRONG_BULLISH"):
            signal = "STRONG_BULLISH"
        elif differential > 20:
            signal = "BULLISH"
        elif differential < -40 and quote_s.trend in ("BULLISH", "STRONG_BULLISH"):
            signal = "STRONG_BEARISH"
        elif differential < -20:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"
        
        return {
            "strength_signal": signal,
            "strength_differential": round(differential, 2),
            "base_currency": base,
            "base_strength": base_s.strength,
            "base_trend": base_s.trend,
            "base_rank": base_s.rank,
            "base_zscore": base_s.z_score,
            "quote_currency": quote,
            "quote_strength": quote_s.strength,
            "quote_trend": quote_s.trend,
            "quote_rank": quote_s.rank,
            "quote_zscore": quote_s.z_score,
        }
    
    def _empty_analysis(self) -> Dict[str, any]:
        """Return empty analysis when data unavailable"""
        return {
            "strength_signal": "NEUTRAL",
            "strength_differential": 0.0,
            "base_currency": None,
            "base_strength": 0.0,
            "quote_currency": None,
            "quote_strength": 0.0,
        }
    
    def get_best_pairs(
        self,
        strengths: List[CurrencyStrength],
        top_n: int = 2
    ) -> List[Dict[str, str]]:
        """
        Get best pairs to trade (strongest vs weakest currencies).
        
        Returns:
            List of {'pair': 'EURUSD', 'direction': 'BUY', 'strength_diff': 80.5}
        """
        if len(strengths) < 4:
            return []
        
        strong = [s for s in strengths if s.rank <= top_n]
        weak = [s for s in strengths if s.rank >= len(strengths) - top_n + 1]
        
        recommendations = []
        for s in strong:
            for w in weak:
                pair = self._find_pair(s.currency, w.currency)
                if pair:
                    base, quote = self._pair_map.get(pair, (None, None))
                    if base and quote:
                        direction = "BUY" if base == s.currency else "SELL"
                        diff = abs(s.strength - w.strength)
                        recommendations.append({
                            "pair": pair,
                            "direction": direction,
                            "strong_currency": s.currency,
                            "weak_currency": w.currency,
                            "strength_differential": round(diff, 2),
                        })
        
        # Sort by differential
        recommendations.sort(key=lambda x: x["strength_differential"], reverse=True)
        return recommendations[:5]
    
    def _find_pair(self, curr1: str, curr2: str) -> Optional[str]:
        """Find pair for two currencies"""
        pair1 = curr1 + curr2
        pair2 = curr2 + curr1
        
        if pair1 in self.ALL_PAIRS:
            return pair1
        if pair2 in self.ALL_PAIRS:
            return pair2
        return None
    
    def format_for_context(self, strengths: List[CurrencyStrength]) -> Dict[str, any]:
        """
        Format strengths for CorrelationAgent context.
        """
        if not strengths:
            return {
                "currency_strength_available": False,
                "strongest_currency": None,
                "weakest_currency": None,
            }
        
        strongest = strengths[0]
        weakest = strengths[-1]
        
        return {
            "currency_strength_available": True,
            "strongest_currency": strongest.currency,
            "strongest_strength": strongest.strength,
            "strongest_trend": strongest.trend,
            "strongest_zscore": strongest.z_score,
            "weakest_currency": weakest.currency,
            "weakest_strength": weakest.strength,
            "weakest_trend": weakest.trend,
            "weakest_zscore": weakest.z_score,
            "strength_ranking": [s.currency for s in strengths],
            "strength_spread": round(strongest.strength - weakest.strength, 2),
        }
    
    def reset(self):
        """Reset internal state (EMA, history)"""
        self._strength_history.clear()
        self._ema_values.clear()


# === Testing ===

if __name__ == "__main__":
    print("Testing CurrencyStrengthMeter v2.0 (MTF + EMA)")
    print("=" * 60)
    
    meter = CurrencyStrengthMeter()
    
    # Simulate price data
    current = {
        'EURUSD': 1.0850, 'GBPUSD': 1.2650, 'USDJPY': 157.50,
        'USDCHF': 0.9020, 'AUDUSD': 0.6450, 'USDCAD': 1.3620,
        'NZDUSD': 0.5920, 'EURGBP': 0.8580, 'EURJPY': 170.90,
    }
    
    previous = {
        'EURUSD': 1.0800, 'GBPUSD': 1.2700, 'USDJPY': 156.00,
        'USDCHF': 0.9050, 'AUDUSD': 0.6400, 'USDCAD': 1.3600,
        'NZDUSD': 0.5900, 'EURGBP': 0.8500, 'EURJPY': 168.50,
    }
    
    # Calculate single timeframe
    strengths = meter.calculate(current, previous)
    
    print("\nSingle Timeframe Results:")
    print("-" * 50)
    for s in strengths:
        print(f"  {s.rank}. {s.currency}: {s.strength:+6.1f} ({s.trend}) z={s.z_score:+.2f}")
    
    # Pair analysis
    print("\nPair Analysis (EURUSD):")
    analysis = meter.analyze_pair("EURUSD", strengths)
    for k, v in analysis.items():
        print(f"  {k}: {v}")
    
    # Best pairs
    print("\nBest Pairs to Trade:")
    best = meter.get_best_pairs(strengths)
    for p in best:
        print(f"  {p['pair']} {p['direction']}: diff={p['strength_differential']}")
