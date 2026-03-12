"""
Forecast Verifier — checks past forecasts against actual price movements.

Runs periodically in the main loop. For each unverified forecast whose
horizon has elapsed, compares the predicted direction with actual price change.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Map horizon_minutes → column prefix
HORIZON_PREFIXES = {30: "h30", 60: "h60", 240: "h240", 1440: "h1440"}


@dataclass
class AltVerified:
    row_id: str
    symbol: str
    variant: str  # "alt2" | "alt3" | "alt3v2" | "alt4" | "alt5"
    correct: bool
    ts_utc: str


@dataclass
class VerifyResult:
    count: int = 0
    alt_results: List[AltVerified] = field(default_factory=list)


class ForecastVerifier:
    """
    Verifies past forecasts by comparing predicted direction with actual price.

    Uses historical prices from market_snapshots (M15 bars) for accurate
    verification at the exact horizon end time, not current price.

    Usage:
        verifier = ForecastVerifier(db)
        result = verifier.verify_pending(market_data_service, symbols)
        print(result.count, len(result.alt_results))
    """

    def __init__(self, db: Any) -> None:
        self.db = db
        self._last_log: Optional[str] = None
        self._h30_strict_expiry = os.getenv("FORECAST_VERIFY_H30_STRICT_EXPIRY", "1").strip().lower() not in {
            "0", "false", "no", "off"
        }

    @staticmethod
    def _is_weekend(ts: datetime) -> bool:
        """Check if timestamp falls during forex weekend (Fri 22:00 - Sun 22:00 UTC)."""
        wd = ts.weekday()  # 0=Mon .. 6=Sun
        hour = ts.hour
        if wd == 5:  # Saturday
            return True
        if wd == 6 and hour < 22:  # Sunday before 22:00
            return True
        if wd == 4 and hour >= 22:  # Friday after 22:00
            return True
        return False

    def reset_bad_verifications(self, batch: int = 500) -> int:
        """
        Reset verifications where all horizons got the same actual_price
        (indicates stale/incorrect price was used).
        """
        try:
            # Find verified rows where h30 and h240 have identical actual_price
            # (impossible in practice — different time horizons)
            res = (
                self.db.client.table("price_forecasts")
                .select("id, h30_actual_price, h60_actual_price, h240_actual_price, h1440_actual_price")
                .not_.is_("verified_at", "null")
                .order("ts_utc", desc=False)
                .limit(batch)
                .execute()
            )
            rows = res.data or []
            reset_ids = []
            for row in rows:
                prices = [row.get(f"{p}_actual_price") for p in ["h30", "h60", "h240", "h1440"]]
                non_null = [p for p in prices if p is not None]
                if len(non_null) >= 2 and len(set(non_null)) == 1:
                    # All actual prices identical — bad verification
                    reset_ids.append(row["id"])
            if not reset_ids:
                return 0
            # Reset in batches of 50
            reset_count = 0
            for i in range(0, len(reset_ids), 50):
                batch_ids = reset_ids[i:i+50]
                self.db.client.table("price_forecasts").update({
                    "verified_at": None,
                    "h30_correct": None, "h30_actual": None, "h30_actual_price": None,
                    "h60_correct": None, "h60_actual": None, "h60_actual_price": None,
                    "h240_correct": None, "h240_actual": None, "h240_actual_price": None,
                    "h1440_correct": None, "h1440_actual": None, "h1440_actual_price": None,
                }).in_("id", batch_ids).execute()
                reset_count += len(batch_ids)
            return reset_count
        except Exception as exc:
            logger.warning(f"reset_bad_verifications error: {exc}")
            return 0

    def verify_pending(
        self,
        market_data_service: Any,
        symbols: List[str],
        limit: int = 100,
    ) -> VerifyResult:
        """
        Find unverified forecasts whose horizons have elapsed and verify them.

        Returns structured verification result for lifecycle feedback.
        """
        now = datetime.now(timezone.utc)
        # Only check forecasts older than 30 minutes (shortest horizon)
        cutoff = now - timedelta(minutes=30)

        try:
            result = (
                self.db.client.table("price_forecasts")
                .select("id, ts_utc, symbol, base_price, "
                        "h30_direction, h60_direction, h240_direction, h1440_direction, "
                        "h30_correct, h60_correct, h240_correct, h1440_correct, "
                        "h30_alt_direction, h30_alt2_direction, h30_alt3_direction, "
                        "h30_alt3v2_direction, h30_alt4_direction, h30_alt5_direction")
                .is_("verified_at", "null")
                .lte("ts_utc", cutoff.isoformat())
                .order("ts_utc", desc=False)
                .limit(limit)
                .execute()
            )
            rows = result.data or []
        except Exception as exc:
            logger.warning(f"forecast_verify_fetch_error: {exc}")
            return VerifyResult()

        if not rows:
            return VerifyResult()

        verify_result = VerifyResult()
        for row in rows:
            try:
                row_updated, alt_verified = self._verify_row(row, now, market_data_service)
                if row_updated:
                    verify_result.count += 1
                if alt_verified:
                    verify_result.alt_results.extend(alt_verified)
            except Exception as exc:
                logger.warning(f"forecast_verify_row_error id={row.get('id')} error={exc}")

        return verify_result

    def _verify_row(
        self,
        row: Dict[str, Any],
        now: datetime,
        mds: Any,
    ) -> Tuple[bool, List[AltVerified]]:
        """Verify a single forecast row. Returns (updated, alt_verifications)."""
        row_id = row.get("id")
        ts_raw = row.get("ts_utc")
        symbol = row.get("symbol")
        base_price = row.get("base_price")

        if not row_id or not ts_raw or not symbol:
            return False, []

        if isinstance(ts_raw, str):
            forecast_ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        else:
            forecast_ts = ts_raw

        if not hasattr(forecast_ts, 'tzinfo') or forecast_ts.tzinfo is None:
            forecast_ts = forecast_ts.replace(tzinfo=timezone.utc)

        # Skip forecasts generated during weekend (no valid market data)
        if self._is_weekend(forecast_ts):
            # Mark as verified with neutral results to avoid re-processing
            try:
                self.db.client.table("price_forecasts").update({
                    "verified_at": now.isoformat(),
                    "h30_correct": None, "h30_actual": "weekend",
                    "h60_correct": None, "h60_actual": "weekend",
                    "h240_correct": None, "h240_actual": "weekend",
                    "h1440_correct": None, "h1440_actual": "weekend",
                }).eq("id", row_id).execute()
            except Exception:
                pass
            return True, []

        # If base_price is missing, reconstruct from historical M15 bar at forecast time
        if base_price is None:
            base_price = self._get_historical_price(symbol, forecast_ts)
            if base_price is not None:
                try:
                    self.db.client.table("price_forecasts").update(
                        {"base_price": base_price}
                    ).eq("id", row_id).execute()
                except Exception:
                    pass
            else:
                # Fallback: use current price from cache (less accurate)
                base_price = self._get_current_price(symbol, mds)
                if base_price is None:
                    return False, []

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
                update_data[correct_col] = True
                update_data[f"{prefix}_actual"] = "neutral"
                # Still verify alt strategies for H30 even when original is neutral
                if horizon_min == 30:
                    alt2_dir = row.get("h30_alt2_direction")
                    alt3_dir = row.get("h30_alt3_direction")
                    alt3v2_dir = row.get("h30_alt3v2_direction")
                    alt4_dir = row.get("h30_alt4_direction")
                    alt5_dir = row.get("h30_alt5_direction")
                    if alt2_dir or alt3_dir or alt3v2_dir or alt4_dir or alt5_dir:
                        # Need actual price to determine actual_dir
                        _actual_price = self._get_historical_price(
                            symbol,
                            horizon_end,
                            strict_expiry=(horizon_min == 30 and self._h30_strict_expiry),
                        )
                        if _actual_price is None:
                            _actual_price = self._get_current_price(symbol, mds)
                        if _actual_price is not None and base_price is not None:
                            _pc = _actual_price - base_price
                            if abs(_pc) < 1e-6:
                                _adir = "neutral"
                            elif _pc > 0:
                                _adir = "up"
                            else:
                                _adir = "down"
                            if alt2_dir and alt2_dir != "neutral":
                                update_data["h30_alt2_correct"] = (alt2_dir == _adir)
                            if alt3_dir and alt3_dir != "neutral":
                                update_data["h30_alt3_correct"] = (alt3_dir == _adir)
                            if alt3v2_dir and alt3v2_dir != "neutral":
                                update_data["h30_alt3v2_correct"] = (alt3v2_dir == _adir)
                            if alt4_dir and alt4_dir != "neutral":
                                update_data["h30_alt4_correct"] = (alt4_dir == _adir)
                            if alt5_dir and alt5_dir != "neutral":
                                update_data["h30_alt5_correct"] = (alt5_dir == _adir)
                continue

            # Get actual price at horizon end from historical data
            actual_price = self._get_historical_price(
                symbol,
                horizon_end,
                strict_expiry=(horizon_min == 30 and self._h30_strict_expiry),
            )
            if actual_price is None:
                # Fallback to current price if horizon just elapsed
                actual_price = self._get_current_price(symbol, mds)
            if actual_price is None:
                all_verified = False
                continue

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

            # A/B test: verify alt direction for H30
            if horizon_min == 30:
                alt_dir = row.get("h30_alt_direction")
                if alt_dir and alt_dir != "neutral":
                    update_data["h30_alt_correct"] = (alt_dir == actual_dir)
                elif alt_dir == "neutral":
                    update_data["h30_alt_correct"] = True
                # A/B test 2: verify alt2
                alt2_dir = row.get("h30_alt2_direction")
                if alt2_dir and alt2_dir != "neutral":
                    update_data["h30_alt2_correct"] = (alt2_dir == actual_dir)
                # A/B test 3: verify alt3
                alt3_dir = row.get("h30_alt3_direction")
                if alt3_dir and alt3_dir != "neutral":
                    update_data["h30_alt3_correct"] = (alt3_dir == actual_dir)
                # Alt3-v2: verify independent variant
                alt3v2_dir = row.get("h30_alt3v2_direction")
                if alt3v2_dir and alt3v2_dir != "neutral":
                    update_data["h30_alt3v2_correct"] = (alt3v2_dir == actual_dir)
                # Alt4: verify independent variant
                alt4_dir = row.get("h30_alt4_direction")
                if alt4_dir and alt4_dir != "neutral":
                    update_data["h30_alt4_correct"] = (alt4_dir == actual_dir)
                # Alt5: verify inversion of Alt3 with env filters
                alt5_dir = row.get("h30_alt5_direction")
                if alt5_dir and alt5_dir != "neutral":
                    update_data["h30_alt5_correct"] = (alt5_dir == actual_dir)

        # Determine if we should force-verify to prevent queue blocking
        age_hours = (now - forecast_ts).total_seconds() / 3600

        if not update_data and all_verified:
            # All horizons already verified, just stamp it
            update_data["verified_at"] = now.isoformat()
        elif not update_data:
            # Nothing new to update — force if old enough
            if age_hours > 4:
                update_data["verified_at"] = now.isoformat()
            else:
                return False, []

        # Mark as fully verified if all horizons checked OR forecast old enough
        if all_verified:
            update_data["verified_at"] = now.isoformat()
        elif age_hours > 4:
            # >4h old — force verified with partial results to prevent queue blocking
            update_data["verified_at"] = now.isoformat()

        alt_verified: List[AltVerified] = []
        ts_out = forecast_ts.isoformat()
        alt2_correct = update_data.get("h30_alt2_correct")
        alt3_correct = update_data.get("h30_alt3_correct")
        alt3v2_correct = update_data.get("h30_alt3v2_correct")
        alt4_correct = update_data.get("h30_alt4_correct")
        alt5_correct = update_data.get("h30_alt5_correct")
        if isinstance(alt2_correct, bool) and row.get("h30_alt2_direction") not in (None, "neutral"):
            alt_verified.append(
                AltVerified(
                    row_id=str(row_id),
                    symbol=str(symbol),
                    variant="alt2",
                    correct=alt2_correct,
                    ts_utc=ts_out,
                )
            )
        if isinstance(alt3_correct, bool) and row.get("h30_alt3_direction") not in (None, "neutral"):
            alt_verified.append(
                AltVerified(
                    row_id=str(row_id),
                    symbol=str(symbol),
                    variant="alt3",
                    correct=alt3_correct,
                    ts_utc=ts_out,
                )
            )
        if isinstance(alt3v2_correct, bool) and row.get("h30_alt3v2_direction") not in (None, "neutral"):
            alt_verified.append(
                AltVerified(
                    row_id=str(row_id),
                    symbol=str(symbol),
                    variant="alt3v2",
                    correct=alt3v2_correct,
                    ts_utc=ts_out,
                )
            )
        if isinstance(alt4_correct, bool) and row.get("h30_alt4_direction") not in (None, "neutral"):
            alt_verified.append(
                AltVerified(
                    row_id=str(row_id),
                    symbol=str(symbol),
                    variant="alt4",
                    correct=alt4_correct,
                    ts_utc=ts_out,
                )
            )
        if isinstance(alt5_correct, bool) and row.get("h30_alt5_direction") not in (None, "neutral"):
            alt_verified.append(
                AltVerified(
                    row_id=str(row_id),
                    symbol=str(symbol),
                    variant="alt5",
                    correct=alt5_correct,
                    ts_utc=ts_out,
                )
            )

        try:
            self.db.client.table("price_forecasts").update(update_data).eq("id", row_id).execute()
            return True, alt_verified
        except Exception as exc:
            logger.warning(f"forecast_verify_update_error id={row_id} error={exc}")
            return False, []

    def _get_historical_price(
        self,
        symbol: str,
        target_ts: datetime,
        strict_expiry: bool = False,
    ) -> Optional[float]:
        """
        Get close price from market_snapshots around target_ts.

        Default mode picks the bar nearest to target_ts inside a wide window.
        Strict-expiry mode picks the first snapshot at or after target_ts, which
        better matches binary expiry semantics for H30 verification.
        """
        try:
            # Check if target_ts falls on weekend (forex closed Fri 22:00 - Sun 22:00 UTC)
            wd = target_ts.weekday()  # 0=Mon .. 6=Sun
            hour = target_ts.hour
            if wd == 5:  # Saturday — market closed
                return None
            if wd == 6 and hour < 22:  # Sunday before 22:00 — still closed
                return None
            if wd == 4 and hour >= 22:  # Friday after 22:00 — closed
                return None

            if strict_expiry:
                window_end = (target_ts + timedelta(minutes=15)).isoformat()
                res = (
                    self.db.client.table("market_snapshots")
                    .select("close, ts")
                    .eq("symbol", symbol)
                    .eq("timeframe", "M15")
                    .gte("ts", target_ts.isoformat())
                    .lte("ts", window_end)
                    .order("ts", desc=False)
                    .limit(20)
                ).execute()
                rows = res.data or []
                for row in rows:
                    close = row.get("close")
                    if close is not None:
                        return float(close)
                return None

            # Legacy mode: look back up to 30 min before target, up to 15 min after.
            window_start = (target_ts - timedelta(minutes=30)).isoformat()
            window_end = (target_ts + timedelta(minutes=15)).isoformat()
            res = (
                self.db.client.table("market_snapshots")
                .select("close, ts")
                .eq("symbol", symbol)
                .eq("timeframe", "M15")
                .gte("ts", window_start)
                .lte("ts", window_end)
                .order("ts", desc=False)
                .limit(50)
            ).execute()
            rows = res.data or []
            if not rows:
                return None

            best = None
            best_delta = float("inf")
            for row in rows:
                if row.get("close") is None:
                    continue
                bar_ts_str = row.get("ts", "")
                if not bar_ts_str:
                    continue
                bar_ts = datetime.fromisoformat(bar_ts_str.replace("Z", "+00:00"))
                if bar_ts.tzinfo is None:
                    bar_ts = bar_ts.replace(tzinfo=timezone.utc)
                delta = abs((target_ts - bar_ts).total_seconds())
                if delta < best_delta:
                    best_delta = delta
                    best = float(row["close"])
            return best
        except Exception:
            pass
        return None

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
