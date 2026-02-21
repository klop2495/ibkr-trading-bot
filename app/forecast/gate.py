"""
Forecast Gate — filters trades based on forecast accuracy and direction alignment.

Reads backtest accuracy data and latest forecast to:
1. Block trades on symbols with accuracy < threshold (default 45%)
2. Block trades where direction conflicts with dominant forecast

This is a soft gate — it can be disabled via FORECAST_GATE_ENABLED=0
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default accuracy threshold: block symbols below this
DEFAULT_ACCURACY_THRESHOLD = 0.45

# Symbols to block based on backtest results (accuracy < 45%)
# Updated from backtest run: 60 days, 6266 predictions
# These symbols had overall accuracy below 45% across all horizons
BACKTEST_LOW_ACCURACY_SYMBOLS = {
    "EURUSD",   # 33.6% overall
    "GBPUSD",   # 37.0% overall
    "AUDUSD",   # 38.9% overall
    "USDCAD",   # 38.7% overall
    "USDCHF",   # 37.6% overall
    "NZDUSD",   # 43.1% overall
}


class ForecastGate:
    """
    Gate that uses forecast data to filter trade execution.
    
    Two checks:
    1. Symbol accuracy gate: blocks symbols with historically low forecast accuracy
    2. Direction alignment: blocks trades where signal direction opposes forecast
    """

    def __init__(
        self,
        accuracy_threshold: float = DEFAULT_ACCURACY_THRESHOLD,
        blocked_symbols: Optional[set] = None,
        enabled: bool = True,
        direction_check_enabled: bool = True,
    ):
        self._threshold = accuracy_threshold
        self._blocked_symbols = blocked_symbols if blocked_symbols is not None else BACKTEST_LOW_ACCURACY_SYMBOLS
        self._enabled = enabled
        self._direction_check_enabled = direction_check_enabled
        # Cache of latest forecasts: symbol -> {direction, strength, ts}
        self._forecast_cache: Dict[str, Dict[str, Any]] = {}
        logger.info(
            f"ForecastGate initialized: enabled={enabled} threshold={accuracy_threshold} "
            f"blocked={len(self._blocked_symbols)} symbols direction_check={direction_check_enabled}"
        )

    @property
    def blocked_symbols(self) -> set:
        return self._blocked_symbols

    @property
    def enabled(self) -> bool:
        return self._enabled

    def update_forecast(self, symbol: str, dominant_direction: str, strength: float) -> None:
        """Update cached forecast for a symbol."""
        self._forecast_cache[symbol] = {
            "direction": dominant_direction,
            "strength": strength,
            "ts": datetime.now(timezone.utc),
        }

    def update_forecasts_batch(self, forecasts: list) -> None:
        """Update cached forecasts from a batch of ForecastResult objects."""
        for fc in forecasts:
            symbol = getattr(fc, "symbol", "")
            dominant = getattr(fc, "dominant_direction", None)
            if dominant is None and hasattr(fc, "horizons"):
                # Compute dominant from horizons
                ups = sum(1 for h in fc.horizons if getattr(h, "direction", None) and h.direction.value == "up")
                downs = sum(1 for h in fc.horizons if getattr(h, "direction", None) and h.direction.value == "down")
                if ups > downs:
                    dominant = "up"
                elif downs > ups:
                    dominant = "down"
                else:
                    dominant = "neutral"
            # Avg strength
            strength = 0.0
            if hasattr(fc, "horizons") and fc.horizons:
                strengths = [getattr(h, "strength", 0.0) for h in fc.horizons]
                strength = sum(strengths) / len(strengths) if strengths else 0.0
            if symbol and dominant:
                self.update_forecast(symbol, dominant, strength)

    def check(self, symbol: str, trade_direction: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a trade should be allowed.
        
        Args:
            symbol: Trading pair (e.g. "EURUSD")
            trade_direction: "BUY"/"LONG" or "SELL"/"SHORT"
            
        Returns:
            Tuple of (allowed: bool, reason: Optional[str])
        """
        if not self._enabled:
            return True, None

        # Check 1: Symbol accuracy gate
        if symbol in self._blocked_symbols:
            reason = f"forecast_low_accuracy:{symbol}"
            logger.info(f"ForecastGate blocked {symbol}: low backtest accuracy")
            return False, reason

        # Check 2: Direction alignment
        if self._direction_check_enabled:
            forecast = self._forecast_cache.get(symbol)
            if forecast:
                fc_dir = forecast["direction"]
                fc_age = (datetime.now(timezone.utc) - forecast["ts"]).total_seconds()
                
                # Only use recent forecasts (< 5 minutes old)
                if fc_age < 300:
                    trade_dir_normalized = trade_direction.upper()
                    if trade_dir_normalized in ("BUY", "LONG"):
                        if fc_dir == "down" and forecast["strength"] > 0.6:
                            reason = f"forecast_direction_conflict:trade=BUY forecast=DOWN strength={forecast['strength']:.2f}"
                            logger.info(f"ForecastGate blocked {symbol}: direction conflict")
                            return False, reason
                    elif trade_dir_normalized in ("SELL", "SHORT"):
                        if fc_dir == "up" and forecast["strength"] > 0.6:
                            reason = f"forecast_direction_conflict:trade=SELL forecast=UP strength={forecast['strength']:.2f}"
                            logger.info(f"ForecastGate blocked {symbol}: direction conflict")
                            return False, reason

        return True, None

    def get_status(self) -> Dict[str, Any]:
        """Get gate status for dashboard."""
        return {
            "enabled": self._enabled,
            "threshold": self._threshold,
            "blocked_symbols": sorted(self._blocked_symbols),
            "direction_check_enabled": self._direction_check_enabled,
            "cached_forecasts": len(self._forecast_cache),
        }
