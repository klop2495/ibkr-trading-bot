"""
Adaptive Forecast Gate — dynamically blocks/unblocks pairs based on rolling accuracy.

Instead of a static blocklist, queries the last N verified forecasts per symbol
and calculates rolling accuracy. Pairs below threshold are blocked, pairs above
are allowed. Refreshes every REFRESH_INTERVAL_SECONDS.

Config via env vars:
  FORECAST_GATE_ENABLED=1          (default: on)
  FORECAST_GATE_WINDOW=50          (rolling window size per pair)
  FORECAST_GATE_BLOCK_BELOW=0.55   (block if accuracy < 55%)
  FORECAST_GATE_UNBLOCK_ABOVE=0.60 (unblock if accuracy >= 60%)
  FORECAST_GATE_MIN_SAMPLES=15     (need at least 15 samples to decide)
  FORECAST_GATE_REFRESH=300        (refresh every 5 minutes)
  FORECAST_GATE_HORIZON=h30        (which horizon to evaluate)
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AdaptiveForecastGate:
    """
    Gate that dynamically blocks/unblocks pairs based on rolling forecast accuracy.

    Uses hysteresis: block below 55%, unblock above 60%.
    This prevents rapid toggling at the boundary.
    """

    def __init__(
        self,
        db=None,
        enabled: bool = True,
        window: int = 50,
        block_below: float = 0.55,
        unblock_above: float = 0.60,
        min_samples: int = 15,
        refresh_interval: int = 300,
        horizon: str = "h30",
        direction_check_enabled: bool = True,
    ):
        self._db = db
        self._enabled = enabled
        self._window = int(os.getenv("FORECAST_GATE_WINDOW", str(window)))
        self._block_below = float(os.getenv("FORECAST_GATE_BLOCK_BELOW", str(block_below)))
        self._unblock_above = float(os.getenv("FORECAST_GATE_UNBLOCK_ABOVE", str(unblock_above)))
        self._min_samples = int(os.getenv("FORECAST_GATE_MIN_SAMPLES", str(min_samples)))
        self._refresh_interval = int(os.getenv("FORECAST_GATE_REFRESH", str(refresh_interval)))
        self._horizon = os.getenv("FORECAST_GATE_HORIZON", horizon)
        self._direction_check_enabled = direction_check_enabled

        # Dynamic state
        self._blocked_symbols: set = set()
        self._pair_accuracy: Dict[str, Dict[str, Any]] = {}  # symbol -> {accuracy, samples, last_update}
        self._last_refresh: float = 0.0

        # Forecast cache for direction checks
        self._forecast_cache: Dict[str, Dict[str, Any]] = {}

        logger.info(
            f"AdaptiveForecastGate initialized: enabled={enabled} "
            f"window={self._window} block<{self._block_below} unblock>={self._unblock_above} "
            f"min_samples={self._min_samples} refresh={self._refresh_interval}s horizon={self._horizon}"
        )

    @property
    def blocked_symbols(self) -> set:
        return self._blocked_symbols

    @property
    def enabled(self) -> bool:
        return self._enabled

    def refresh_if_needed(self) -> None:
        """Refresh accuracy data if stale."""
        now = time.time()
        if now - self._last_refresh < self._refresh_interval:
            return
        self._refresh_accuracy()
        self._last_refresh = now

    def _refresh_accuracy(self) -> None:
        """Query DB for rolling accuracy per pair and update blocked set."""
        if not self._db:
            logger.warning("AdaptiveForecastGate: no DB connection, skipping refresh")
            return

        try:
            h = self._horizon
            dir_col = f"{h}_direction"
            correct_col = f"{h}_correct"

            # Get recent verified forecasts with non-null correct column
            res = self._db.client.table("price_forecasts").select(
                f"symbol, {dir_col}, {correct_col}"
            ).not_.is_("verified_at", "null").not_.is_(
                correct_col, "null"
            ).neq(
                dir_col, "neutral"
            ).order("ts_utc", desc=True).limit(
                self._window * 20  # enough for all pairs
            ).execute()

            rows = res.data or []

            # Group by symbol
            per_pair: Dict[str, List[bool]] = {}
            for row in rows:
                sym = row["symbol"]
                if sym not in per_pair:
                    per_pair[sym] = []
                if len(per_pair[sym]) < self._window:
                    per_pair[sym].append(row[correct_col] is True)

            # Calculate accuracy and update blocked set
            old_blocked = self._blocked_symbols.copy()

            for sym, results in per_pair.items():
                n = len(results)
                if n < self._min_samples:
                    # Not enough data — keep current state (don't block or unblock)
                    self._pair_accuracy[sym] = {
                        "accuracy": None,
                        "samples": n,
                        "status": "insufficient_data",
                    }
                    continue

                correct = sum(results)
                acc = correct / n

                self._pair_accuracy[sym] = {
                    "accuracy": round(acc, 3),
                    "samples": n,
                    "correct": correct,
                    "status": "ok",
                }

                # Hysteresis logic
                if sym in self._blocked_symbols:
                    # Currently blocked — unblock only if above unblock threshold
                    if acc >= self._unblock_above:
                        self._blocked_symbols.discard(sym)
                        logger.info(
                            f"AdaptiveGate UNBLOCKED {sym}: accuracy={acc:.1%} "
                            f"({correct}/{n}) >= {self._unblock_above:.0%}"
                        )
                else:
                    # Currently allowed — block if below block threshold
                    if acc < self._block_below:
                        self._blocked_symbols.add(sym)
                        logger.info(
                            f"AdaptiveGate BLOCKED {sym}: accuracy={acc:.1%} "
                            f"({correct}/{n}) < {self._block_below:.0%}"
                        )

            # Log summary
            changes = self._blocked_symbols.symmetric_difference(old_blocked)
            if changes:
                logger.info(f"AdaptiveGate changes: {changes}")

            logger.info(
                f"AdaptiveGate refresh: {len(per_pair)} pairs evaluated, "
                f"{len(self._blocked_symbols)} blocked: {sorted(self._blocked_symbols)}"
            )

        except Exception as e:
            logger.error(f"AdaptiveGate refresh failed: {e}", exc_info=True)

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
                ups = sum(1 for h in fc.horizons if getattr(h, "direction", None) and h.direction.value == "up")
                downs = sum(1 for h in fc.horizons if getattr(h, "direction", None) and h.direction.value == "down")
                if ups > downs:
                    dominant = "up"
                elif downs > ups:
                    dominant = "down"
                else:
                    dominant = "neutral"
            strength = 0.0
            if hasattr(fc, "horizons") and fc.horizons:
                strengths = [getattr(h, "strength", 0.0) for h in fc.horizons]
                strength = sum(strengths) / len(strengths) if strengths else 0.0
            if symbol and dominant:
                self.update_forecast(symbol, dominant, strength)

    def check(self, symbol: str, trade_direction: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a trade should be allowed.

        Returns:
            (allowed, reason) — reason is None if allowed
        """
        if not self._enabled:
            return True, None

        # Auto-refresh
        self.refresh_if_needed()

        # Check 1: Rolling accuracy gate
        if symbol in self._blocked_symbols:
            acc_info = self._pair_accuracy.get(symbol, {})
            acc_str = f"{acc_info.get('accuracy', 0):.0%}" if acc_info.get('accuracy') else "n/a"
            reason = f"adaptive_gate_blocked:{symbol} rolling_acc={acc_str}"
            logger.info(f"AdaptiveGate blocked trade {symbol}: {reason}")
            return False, reason

        # Check 2: Direction alignment
        if self._direction_check_enabled:
            forecast = self._forecast_cache.get(symbol)
            if forecast:
                fc_dir = forecast["direction"]
                fc_age = (datetime.now(timezone.utc) - forecast["ts"]).total_seconds()

                if fc_age < 300:  # only use fresh forecasts
                    trade_dir = trade_direction.upper()
                    if trade_dir in ("BUY", "LONG") and fc_dir == "down" and forecast["strength"] > 0.6:
                        reason = f"direction_conflict:trade=BUY forecast=DOWN str={forecast['strength']:.2f}"
                        return False, reason
                    elif trade_dir in ("SELL", "SHORT") and fc_dir == "up" and forecast["strength"] > 0.6:
                        reason = f"direction_conflict:trade=SELL forecast=UP str={forecast['strength']:.2f}"
                        return False, reason

        return True, None

    def get_status(self) -> Dict[str, Any]:
        """Get gate status for dashboard."""
        watch_above = float(os.getenv("FORECAST_GATE_WATCH_ABOVE", str(self._unblock_above)))

        pairs_status = {}
        watch_symbols = []
        for sym, info in sorted(self._pair_accuracy.items()):
            acc = info.get("accuracy")
            blocked = sym in self._blocked_symbols
            insufficient = info.get("status") == "insufficient_data"

            # Determine gate_status: blocked > watch > active > insufficient_data
            if blocked:
                gate_status = "blocked"
            elif insufficient:
                gate_status = "insufficient_data"
            elif acc is not None and acc < watch_above:
                gate_status = "watch"
                watch_symbols.append(sym)
            else:
                gate_status = "active"

            pairs_status[sym] = {
                "accuracy": acc,
                "samples": info.get("samples", 0),
                "correct": info.get("correct", 0),
                "blocked": blocked,
                "gate_status": gate_status,
                "status": info.get("status", "unknown"),
            }

        return {
            "enabled": self._enabled,
            "type": "adaptive",
            "window": self._window,
            "block_below": self._block_below,
            "unblock_above": self._unblock_above,
            "watch_above": watch_above,
            "min_samples": self._min_samples,
            "horizon": self._horizon,
            "blocked_symbols": sorted(self._blocked_symbols),
            "blocked_count": len(self._blocked_symbols),
            "watch_symbols": sorted(watch_symbols),
            "watch_count": len(watch_symbols),
            "pairs": pairs_status,
            "last_refresh": datetime.fromtimestamp(self._last_refresh, tz=timezone.utc).isoformat() if self._last_refresh else None,
        }


# Alias for backward compatibility with ExecutionService import
ForecastGate = AdaptiveForecastGate
