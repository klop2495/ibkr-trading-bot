"""
Adaptive Forecast Gate — dynamically blocks/unblocks pairs based on rolling accuracy.

Instead of a static blocklist, queries the last N verified forecasts per symbol
and calculates rolling accuracy. Pairs below threshold are blocked, pairs above
are allowed. Refreshes every REFRESH_INTERVAL_SECONDS.

Quality filter (empirical, 1000-sample analysis 2026-02-24):
  - MEDIUM confidence + aligned>=4 = 86% accuracy (63/73 samples)
  - Trading hours 08-11, 20-23 UTC = 91-100% for top pairs
  - HIGH confidence is a trap (lagging consensus), LOW too noisy

Config via env vars:
  FORECAST_GATE_ENABLED=1          (default: on)
  FORECAST_GATE_WINDOW=50          (rolling window size per pair)
  FORECAST_GATE_BLOCK_BELOW=0.55   (block if accuracy < 55%)
  FORECAST_GATE_UNBLOCK_ABOVE=0.60 (unblock if accuracy >= 60%)
  FORECAST_GATE_MIN_SAMPLES=15     (need at least 15 samples to decide)
  FORECAST_GATE_REFRESH=300        (refresh every 5 minutes)
  FORECAST_GATE_HORIZON=h30        (which horizon to evaluate)
  FORECAST_GATE_QUALITY_FILTER=1   (enable confidence+aligned filter)
  FORECAST_GATE_MIN_ALIGNED=4      (minimum indicators aligned)
  FORECAST_GATE_REQUIRED_CONFIDENCE=medium  (required confidence level)
  FORECAST_GATE_TRADING_HOURS=08,09,10,11,20,21,22,23  (UTC hours to allow)
  FORECAST_GATE_HOURS_FILTER=1     (enable trading hours filter)
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def _parse_trading_hours(env_val: str) -> Set[int]:
    """Parse comma-separated UTC hours string into a set of ints."""
    hours = set()
    for part in env_val.split(","):
        part = part.strip()
        if part.isdigit():
            h = int(part)
            if 0 <= h <= 23:
                hours.add(h)
    return hours


class AdaptiveForecastGate:
    """
    Gate that dynamically blocks/unblocks pairs based on rolling forecast accuracy.

    Uses hysteresis: block below 55%, unblock above 60%.
    This prevents rapid toggling at the boundary.

    Quality filter (Phase 2, empirical 2026-02-24):
    - Requires MEDIUM confidence + aligned >= 4 indicators
    - Optionally restricts to profitable trading hours (UTC)
    - MEDIUM + top pairs + aligned>=4 = 86% accuracy on 73 samples
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

        # Quality filter settings (empirical from 1000-sample analysis)
        self._quality_filter_enabled = os.getenv("FORECAST_GATE_QUALITY_FILTER", "1") == "1"
        self._min_aligned = int(os.getenv("FORECAST_GATE_MIN_ALIGNED", "4"))
        self._required_confidence = os.getenv("FORECAST_GATE_REQUIRED_CONFIDENCE", "medium").lower()

        # Trading hours filter
        self._hours_filter_enabled = os.getenv("FORECAST_GATE_HOURS_FILTER", "1") == "1"
        default_hours = "08,09,10,11,20,21,22,23"
        self._trading_hours = _parse_trading_hours(
            os.getenv("FORECAST_GATE_TRADING_HOURS", default_hours)
        )

        # Dynamic state
        self._blocked_symbols: set = set()
        self._pair_accuracy: Dict[str, Dict[str, Any]] = {}
        self._last_refresh: float = 0.0

        # Forecast cache for direction + quality checks
        self._forecast_cache: Dict[str, Dict[str, Any]] = {}

        # Quality filter stats (for dashboard)
        self._quality_stats = {
            "total_checked": 0,
            "passed": 0,
            "blocked_confidence": 0,
            "blocked_aligned": 0,
            "blocked_hours": 0,
        }

        logger.info(
            f"AdaptiveForecastGate initialized: enabled={enabled} "
            f"window={self._window} block<{self._block_below} unblock>={self._unblock_above} "
            f"min_samples={self._min_samples} refresh={self._refresh_interval}s horizon={self._horizon} "
            f"quality_filter={self._quality_filter_enabled} min_aligned={self._min_aligned} "
            f"required_conf={self._required_confidence} "
            f"hours_filter={self._hours_filter_enabled} hours={sorted(self._trading_hours)}"
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

            res = self._db.client.table("price_forecasts").select(
                f"symbol, {dir_col}, {correct_col}"
            ).not_.is_("verified_at", "null").not_.is_(
                correct_col, "null"
            ).neq(
                dir_col, "neutral"
            ).order("ts_utc", desc=True).limit(
                self._window * 20
            ).execute()

            rows = res.data or []

            per_pair: Dict[str, List[bool]] = {}
            for row in rows:
                sym = row["symbol"]
                if sym not in per_pair:
                    per_pair[sym] = []
                if len(per_pair[sym]) < self._window:
                    per_pair[sym].append(row[correct_col] is True)

            old_blocked = self._blocked_symbols.copy()

            for sym, results in per_pair.items():
                n = len(results)
                if n < self._min_samples:
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

                if sym in self._blocked_symbols:
                    if acc >= self._unblock_above:
                        self._blocked_symbols.discard(sym)
                        logger.info(
                            f"AdaptiveGate UNBLOCKED {sym}: accuracy={acc:.1%} "
                            f"({correct}/{n}) >= {self._unblock_above:.0%}"
                        )
                else:
                    if acc < self._block_below:
                        self._blocked_symbols.add(sym)
                        logger.info(
                            f"AdaptiveGate BLOCKED {sym}: accuracy={acc:.1%} "
                            f"({correct}/{n}) < {self._block_below:.0%}"
                        )

            changes = self._blocked_symbols.symmetric_difference(old_blocked)
            if changes:
                logger.info(f"AdaptiveGate changes: {changes}")

            logger.info(
                f"AdaptiveGate refresh: {len(per_pair)} pairs evaluated, "
                f"{len(self._blocked_symbols)} blocked: {sorted(self._blocked_symbols)}"
            )

        except Exception as e:
            logger.error(f"AdaptiveGate refresh failed: {e}", exc_info=True)

    def update_forecast(self, symbol: str, dominant_direction: str, strength: float,
                        h30_confidence: Optional[str] = None,
                        h30_aligned: Optional[int] = None,
                        h30_total: Optional[int] = None) -> None:
        """Update cached forecast for a symbol with quality data."""
        self._forecast_cache[symbol] = {
            "direction": dominant_direction,
            "strength": strength,
            "ts": datetime.now(timezone.utc),
            "h30_confidence": h30_confidence,
            "h30_aligned": h30_aligned,
            "h30_total": h30_total,
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
            h30_confidence = None
            h30_aligned = None
            h30_total = None
            if hasattr(fc, "horizons") and fc.horizons:
                strengths = [getattr(h, "strength", 0.0) for h in fc.horizons]
                strength = sum(strengths) / len(strengths) if strengths else 0.0
                # Extract h30 horizon data for quality filter
                for h in fc.horizons:
                    if getattr(h, "horizon_minutes", None) == 30:
                        conf = getattr(h, "confidence", None)
                        h30_confidence = conf.value if hasattr(conf, "value") else str(conf) if conf else None
                        h30_aligned = getattr(h, "indicators_aligned", None)
                        h30_total = getattr(h, "indicators_total", None)
                        break
            if symbol and dominant:
                self.update_forecast(
                    symbol, dominant, strength,
                    h30_confidence=h30_confidence,
                    h30_aligned=h30_aligned,
                    h30_total=h30_total,
                )

    def check(self, symbol: str, trade_direction: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a trade should be allowed.

        Checks (in order):
          1. Rolling accuracy gate (pair blocked?)
          2. Quality filter (confidence + aligned count)
          3. Trading hours filter (UTC hour)
          4. Direction alignment (forecast vs trade direction)

        Returns:
            (allowed, reason) — reason is None if allowed
        """
        if not self._enabled:
            return True, None

        # Auto-refresh
        self.refresh_if_needed()

        self._quality_stats["total_checked"] += 1

        # Check 1: Rolling accuracy gate
        if symbol in self._blocked_symbols:
            acc_info = self._pair_accuracy.get(symbol, {})
            acc_str = f"{acc_info.get('accuracy', 0):.0%}" if acc_info.get('accuracy') else "n/a"
            reason = f"adaptive_gate_blocked:{symbol} rolling_acc={acc_str}"
            logger.info(f"AdaptiveGate blocked trade {symbol}: {reason}")
            return False, reason

        # Check 2: Quality filter (confidence + aligned)
        if self._quality_filter_enabled:
            forecast = self._forecast_cache.get(symbol)
            if forecast:
                fc_age = (datetime.now(timezone.utc) - forecast["ts"]).total_seconds()
                if fc_age < 600:  # use forecasts up to 10 min old
                    # Check confidence level
                    fc_conf = (forecast.get("h30_confidence") or "").lower()
                    if fc_conf and fc_conf != self._required_confidence:
                        self._quality_stats["blocked_confidence"] += 1
                        reason = (
                            f"quality_filter:confidence={fc_conf} "
                            f"required={self._required_confidence}"
                        )
                        logger.info(f"QualityFilter blocked {symbol}: {reason}")
                        return False, reason

                    # Check aligned count
                    fc_aligned = forecast.get("h30_aligned")
                    if fc_aligned is not None and fc_aligned < self._min_aligned:
                        self._quality_stats["blocked_aligned"] += 1
                        reason = (
                            f"quality_filter:aligned={fc_aligned} "
                            f"min_required={self._min_aligned}"
                        )
                        logger.info(f"QualityFilter blocked {symbol}: {reason}")
                        return False, reason

        # Check 3: Trading hours filter
        if self._hours_filter_enabled and self._trading_hours:
            current_hour = datetime.now(timezone.utc).hour
            if current_hour not in self._trading_hours:
                self._quality_stats["blocked_hours"] += 1
                reason = (
                    f"hours_filter:current_hour={current_hour} "
                    f"allowed={sorted(self._trading_hours)}"
                )
                logger.info(f"HoursFilter blocked {symbol}: {reason}")
                return False, reason

        # Check 4: Direction alignment
        if self._direction_check_enabled:
            forecast = self._forecast_cache.get(symbol)
            if forecast:
                fc_dir = forecast["direction"]
                fc_age = (datetime.now(timezone.utc) - forecast["ts"]).total_seconds()

                if fc_age < 300:
                    trade_dir = trade_direction.upper()
                    if trade_dir in ("BUY", "LONG") and fc_dir == "down" and forecast["strength"] > 0.6:
                        reason = f"direction_conflict:trade=BUY forecast=DOWN str={forecast['strength']:.2f}"
                        return False, reason
                    elif trade_dir in ("SELL", "SHORT") and fc_dir == "up" and forecast["strength"] > 0.6:
                        reason = f"direction_conflict:trade=SELL forecast=UP str={forecast['strength']:.2f}"
                        return False, reason

        self._quality_stats["passed"] += 1
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

            if blocked:
                gate_status = "blocked"
            elif insufficient:
                gate_status = "insufficient_data"
            elif acc is not None and acc < watch_above:
                gate_status = "watch"
                watch_symbols.append(sym)
            else:
                gate_status = "active"

            # Add forecast cache info for this pair
            fc = self._forecast_cache.get(sym, {})
            pairs_status[sym] = {
                "accuracy": acc,
                "samples": info.get("samples", 0),
                "correct": info.get("correct", 0),
                "blocked": blocked,
                "gate_status": gate_status,
                "status": info.get("status", "unknown"),
                "h30_confidence": fc.get("h30_confidence"),
                "h30_aligned": fc.get("h30_aligned"),
                "h30_total": fc.get("h30_total"),
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
            # Quality filter config & stats
            "quality_filter": {
                "enabled": self._quality_filter_enabled,
                "min_aligned": self._min_aligned,
                "required_confidence": self._required_confidence,
                "hours_filter_enabled": self._hours_filter_enabled,
                "trading_hours_utc": sorted(self._trading_hours),
                "stats": dict(self._quality_stats),
            },
        }


# Alias for backward compatibility with ExecutionService import
ForecastGate = AdaptiveForecastGate
