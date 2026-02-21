"""
Forecast Verifier — checks past forecasts against actual price movements.

Runs periodically in the main loop. For each unverified forecast whose
horizon has elapsed, compares the predicted direction with actual price change.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.models.forecast import FORECAST_HORIZONS

logger = logging.getLogger(__name__)

# Map horizon_minutes → column prefix
HORIZON_PREFIXES = {30: "h30", 60: "h60", 240: "h240", 1440: "h1440"}


class ForecastVerifier:
    """
    Verifies past forecasts by comparing predicted direction with actual price.

    Usage:
        verifier = ForecastVerifier(db)
        count = verifier.verify_pending(market_data_service, symbols)
    """

    def __init__(self, db: Any) -> None:
        self.db = db
        self._last_log: Optional[str] = None

    def verify_pending(
        self,
        market_data_service: Any,
        symbols: List[str],
        limit: int = 100,
    ) -> int:
        """
        Find unverified forecasts whose horizons have elapsed and verify them.

        Returns number of forecasts updated.
        """
        now = datetime.now(timezone.utc)
        # Only check forecasts older than 30 minutes (shortest horizon)
        cutoff = now - timedelta(minutes=30)

        try:
            result = (
                self.db.client.table("price_forecasts")
                .select("id, ts_utc, symbol, base_price, "
                        "h30_direction, h60_direction, h240_direction, h1440_direction, "
                        "h30_correct, h60_correct, h240_correct, h1440_correct")
                .is_("verified_at", "null")
                .lte("ts_utc", cutoff.isoformat())
                .order("ts_utc", desc=False)
                .limit(limit)
                .execute()
            )
            rows = result.data or []
        except Exception as exc:
            logger.warning(f"forecast_verify_fetch_error: {exc}")
            return 0

        if not rows:
            return 0

        updated = 0
        for row in rows:
            try:
                if self._verify_row(row, now, market_data_service):
                    updated += 1
            except Exception as exc:
                logger.warning(f"forecast_verify_row_error id={row.get('id')} error={exc}")

        return updated

    def _verify_row(
        self,
        row: Dict[str, Any],
        now: datetime,
        mds: Any,
    ) -> bool:
        """Verify a single forecast row. Returns True if updated."""
        row_id = row.get("id")
        ts_raw = row.get("ts_utc")
        symbol = row.get("symbol")
        base_price = row.get("base_price")

        if not row_id or not ts_raw or not symbol:
            return False

        if isinstance(ts_raw, str):
            forecast_ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        else:
            forecast_ts = ts_raw

        if not hasattr(forecast_ts, 'tzinfo') or forecast_ts.tzinfo is None:
            forecast_ts = forecast_ts.replace(tzinfo=timezone.utc)

        # Get current price for this symbol
        current_price = self._get_current_price(symbol, mds)
        if current_price is None:
            return False

        # If base_price is missing, we can't verify
        if base_price is None:
            return False

        update_data: Dict[str, Any] = {}
        all_verified = True

        for horizon_min, prefix in HORIZON_PREFIXES.items():
            correct_col = f"{prefix}_correct"
            # Skip already verified horizons
            if row.get(correct_col) is not None:
                continue

            horizon_end = forecast_ts + timedelta(minutes=horizon_min)
            if now < horizon_end:
                # Horizon hasn't elapsed yet
                all_verified = False
                continue

            predicted = row.get(f"{prefix}_direction")
            if not predicted or predicted == "neutral":
                # Neutral predictions are always "correct" (no claim made)
                update_data[correct_col] = True
                update_data[f"{prefix}_actual"] = "neutral"
                update_data[f"{prefix}_actual_price"] = current_price
                continue

            # Use current price as best approximation
            actual_price = current_price

            # Determine actual direction
            price_change = actual_price - base_price
            if abs(price_change) < 1e-6:
                actual_dir = "neutral"
            elif price_change > 0:
                actual_dir = "up"
            else:
                actual_dir = "down"

            is_correct = (predicted == actual_dir)
            update_data[correct_col] = is_correct
            update_data[f"{prefix}_actual"] = actual_dir
            update_data[f"{prefix}_actual_price"] = actual_price

        if not update_data:
            return False

        # Mark as fully verified if all horizons checked
        if all_verified:
            update_data["verified_at"] = now.isoformat()

        try:
            self.db.client.table("price_forecasts").update(update_data).eq("id", row_id).execute()
            return True
        except Exception as exc:
            logger.warning(f"forecast_verify_update_error id={row_id} error={exc}")
            return False

    def _get_current_price(self, symbol: str, mds: Any) -> Optional[float]:
        """Get latest close price from market data service cache."""
        if mds is None:
            return None
        try:
            ohlc = mds.get_ohlc(symbol, "M15", n_bars=1)
            if ohlc and ohlc.get("closes"):
                return ohlc["closes"][-1]
        except Exception:
            pass
        return None
