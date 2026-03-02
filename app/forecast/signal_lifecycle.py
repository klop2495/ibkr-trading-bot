"""
Signal Lifecycle Manager — deduplication + cooldown with escalation for Alt2 signals.

Ensures one active signal per symbol at a time:
- NEW signal → PENDING (waiting for verification, 30 min)
- PENDING → VERIFIED ✓ → streak reset, allow new signal immediately
- PENDING → VERIFIED ✗ → COOLDOWN (escalating)

Cooldown escalation:
- 1st miss: 30 min cooldown (skip 1 M15 cycle)
- 2nd consecutive miss: 60 min cooldown
- 3rd+ consecutive miss: block until next UTC hour change

Blacklist hours (session transitions where Alt2 fails):
- Hours 06, 08, 13, 14, 18 UTC → signal generation blocked

Config via env vars:
  SIGNAL_LIFECYCLE_ENABLED=1
  SIGNAL_COOLDOWN_1=30         (minutes after 1st miss)
  SIGNAL_COOLDOWN_2=60         (minutes after 2nd consecutive miss)
  SIGNAL_COOLDOWN_3=session    (block until next UTC hour after 3rd miss)
  SIGNAL_BLACKLIST_HOURS=06,08,13,14,18
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

BLACKLIST_HOURS_DEFAULT = {6, 8, 13, 14, 18}


def _parse_hours(env_val: str) -> Set[int]:
    hours = set()
    for part in env_val.split(","):
        part = part.strip()
        if part.isdigit():
            h = int(part)
            if 0 <= h <= 23:
                hours.add(h)
    return hours


class SignalState:
    """State for a single symbol's signal lifecycle."""
    __slots__ = (
        "symbol", "pending_direction", "pending_ts",
        "miss_streak", "cooldown_until", "last_verified_ts",
        "last_result",
    )

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.pending_direction: Optional[str] = None  # "up" / "down" / None
        self.pending_ts: Optional[datetime] = None     # when signal was created
        self.miss_streak: int = 0                       # consecutive misses
        self.cooldown_until: Optional[datetime] = None  # blocked until this time
        self.last_verified_ts: Optional[datetime] = None
        self.last_result: Optional[bool] = None         # True=correct, False=wrong


class SignalLifecycleManager:
    """
    Manages signal deduplication and cooldown per symbol.

    Usage:
        mgr = SignalLifecycleManager()

        # Before recording a new Alt2 signal:
        allowed, reason = mgr.can_signal(symbol, direction, now_utc)
        if allowed:
            mgr.record_signal(symbol, direction, now_utc)
            # ... write to DB

        # When verifier confirms/denies:
        mgr.record_verification(symbol, correct=True/False, now_utc)
    """

    def __init__(self, supabase_client=None):
        self._enabled = os.getenv("SIGNAL_LIFECYCLE_ENABLED", "1") == "1"
        self._cooldown_1 = int(os.getenv("SIGNAL_COOLDOWN_1", "30"))  # minutes
        self._cooldown_2 = int(os.getenv("SIGNAL_COOLDOWN_2", "60"))
        self._cooldown_3_mode = os.getenv("SIGNAL_COOLDOWN_3", "session")  # "session" = until next hour
        self._blacklist_blocking = os.getenv("SIGNAL_BLACKLIST_BLOCKING", "0") == "1"
        self._blacklist_hours = _parse_hours(
            os.getenv("SIGNAL_BLACKLIST_HOURS", ",".join(str(h) for h in sorted(BLACKLIST_HOURS_DEFAULT)))
        )

        self._states: Dict[str, SignalState] = {}
        self._supabase = supabase_client
        self._persist_key = "signal_lifecycle_state"  # row key in KV table
        self._last_persist_ts: float = 0
        self._persist_interval: float = 30.0  # persist every 30s max

        logger.info(
            f"SignalLifecycleManager init: enabled={self._enabled} "
            f"cooldowns={self._cooldown_1}/{self._cooldown_2}/{self._cooldown_3_mode}min "
            f"blacklist_blocking={self._blacklist_blocking} "
            f"blacklist_hours={sorted(self._blacklist_hours)} "
            f"supabase={'yes' if self._supabase else 'no'}"
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def blacklist_hours(self) -> Set[int]:
        return self._blacklist_hours

    def _get_state(self, symbol: str) -> SignalState:
        if symbol not in self._states:
            self._states[symbol] = SignalState(symbol)
        return self._states[symbol]

    def is_blacklisted_hour(self, now: Optional[datetime] = None) -> Tuple[bool, int]:
        """Check if current UTC hour is in blacklist. Returns (is_blacklisted, current_hour)."""
        if now is None:
            now = datetime.now(timezone.utc)
        hour = now.hour
        return hour in self._blacklist_hours, hour

    def can_signal(
        self, symbol: str, direction: str, now: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a new signal is allowed for this symbol.

        Returns (allowed, reason).
        Reason is None if allowed, otherwise a string explaining the block.
        """
        if not self._enabled:
            return True, None

        if now is None:
            now = datetime.now(timezone.utc)

        # Check 1: Blacklist hours
        is_bl, hour = self.is_blacklisted_hour(now)
        if is_bl and self._blacklist_blocking:
            return False, f"blacklist_hour:{hour:02d}"

        state = self._get_state(symbol)

        # Check 2: Pending signal not yet verified
        if state.pending_direction is not None and state.pending_ts is not None:
            age_min = (now - state.pending_ts).total_seconds() / 60
            # If pending for > 45 min without verification, auto-expire
            # (verifier might have missed it)
            if age_min < 45:
                return False, f"pending_signal:{state.pending_direction} age={age_min:.0f}m"
            else:
                # Auto-expire stale pending
                logger.warning(
                    f"lifecycle_auto_expire {symbol} pending_dir={state.pending_direction} "
                    f"age={age_min:.0f}m — treating as miss"
                )
                self.record_verification(symbol, correct=False, now=now)

        # Check 3: Cooldown active
        if state.cooldown_until is not None and now < state.cooldown_until:
            remaining = (state.cooldown_until - now).total_seconds() / 60
            return False, f"cooldown:streak={state.miss_streak} remaining={remaining:.0f}m"

        return True, None

    def record_signal(self, symbol: str, direction: str, now: Optional[datetime] = None) -> None:
        """Record that a new signal has been emitted for this symbol."""
        if now is None:
            now = datetime.now(timezone.utc)
        state = self._get_state(symbol)
        state.pending_direction = direction
        state.pending_ts = now
        logger.info(f"lifecycle_signal_recorded {symbol} dir={direction}")
        self.persist_to_supabase()

    def record_verification(
        self, symbol: str, correct: bool, now: Optional[datetime] = None,
    ) -> None:
        """Record verification result and apply cooldown if needed."""
        if now is None:
            now = datetime.now(timezone.utc)
        state = self._get_state(symbol)

        state.last_verified_ts = now
        state.last_result = correct
        state.pending_direction = None
        state.pending_ts = None

        if correct:
            # Reset streak on success
            if state.miss_streak > 0:
                logger.info(
                    f"lifecycle_streak_reset {symbol} prev_streak={state.miss_streak}"
                )
            state.miss_streak = 0
            state.cooldown_until = None
        else:
            # Escalating cooldown
            state.miss_streak += 1
            streak = state.miss_streak

            if streak == 1:
                cd_min = self._cooldown_1
                state.cooldown_until = now + timedelta(minutes=cd_min)
            elif streak == 2:
                cd_min = self._cooldown_2
                state.cooldown_until = now + timedelta(minutes=cd_min)
            else:
                # 3+: block until next UTC hour
                if self._cooldown_3_mode == "session":
                    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                    state.cooldown_until = next_hour
                    cd_min = (next_hour - now).total_seconds() / 60
                else:
                    cd_min = int(self._cooldown_3_mode) if self._cooldown_3_mode.isdigit() else 120
                    state.cooldown_until = now + timedelta(minutes=cd_min)

            logger.info(
                f"lifecycle_cooldown {symbol} streak={streak} "
                f"cooldown={cd_min:.0f}m until={state.cooldown_until.isoformat()}"
            )
        self.persist_to_supabase()

    def get_status(self) -> Dict[str, Any]:
        """Get lifecycle status for dashboard API."""
        now = datetime.now(timezone.utc)
        is_bl, current_hour = self.is_blacklisted_hour(now)

        symbols_status = {}
        for sym, state in sorted(self._states.items()):
            status = "ready"
            if state.pending_direction:
                status = "pending"
            elif state.cooldown_until and now < state.cooldown_until:
                status = "cooldown"

            symbols_status[sym] = {
                "status": status,
                "pending_direction": state.pending_direction,
                "pending_ts": state.pending_ts.isoformat() if state.pending_ts else None,
                "pending_age_min": round((now - state.pending_ts).total_seconds() / 60, 1) if state.pending_ts else None,
                "miss_streak": state.miss_streak,
                "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until and now < state.cooldown_until else None,
                "cooldown_remaining_min": round((state.cooldown_until - now).total_seconds() / 60, 1) if state.cooldown_until and now < state.cooldown_until else None,
                "last_result": state.last_result,
                "last_verified": state.last_verified_ts.isoformat() if state.last_verified_ts else None,
            }

        return {
            "enabled": self._enabled,
            "current_hour_utc": current_hour,
            "is_blacklisted_hour": is_bl,
            "blacklist_hours": sorted(self._blacklist_hours),
            "blacklist_blocking": self._blacklist_blocking,
            "cooldown_config": {
                "streak_1": self._cooldown_1,
                "streak_2": self._cooldown_2,
                "streak_3": self._cooldown_3_mode,
            },
            "symbols": symbols_status,
            "summary": {
                "total_tracked": len(self._states),
                "pending": sum(1 for s in self._states.values() if s.pending_direction),
                "in_cooldown": sum(1 for s in self._states.values() if s.cooldown_until and now < s.cooldown_until),
                "ready": sum(1 for s in self._states.values() if not s.pending_direction and (not s.cooldown_until or now >= s.cooldown_until)),
            },
        }

    def restore_from_supabase(self) -> int:
        """Restore in-memory lifecycle states from persisted bot_kv JSON.
        Returns number of symbols restored."""
        data = self.load_from_supabase(self._supabase)
        if not data or not isinstance(data, dict):
            return 0
        symbols = data.get("symbols")
        if not isinstance(symbols, dict):
            return 0

        now = datetime.now(timezone.utc)
        restored = 0
        for sym, row in symbols.items():
            if not isinstance(sym, str) or not isinstance(row, dict):
                continue
            state = self._get_state(sym.upper())

            pending_dir = row.get("pending_direction")
            if isinstance(pending_dir, str) and pending_dir in ("up", "down"):
                state.pending_direction = pending_dir
                pending_ts_raw = row.get("pending_ts")
                pending_age = row.get("pending_age_min")
                parsed_pending_ts = None
                if isinstance(pending_ts_raw, str):
                    try:
                        parsed_pending_ts = datetime.fromisoformat(pending_ts_raw.replace("Z", "+00:00"))
                    except Exception:
                        parsed_pending_ts = None
                if parsed_pending_ts is not None:
                    state.pending_ts = parsed_pending_ts
                elif isinstance(pending_age, (int, float)):
                    state.pending_ts = now - timedelta(minutes=float(pending_age))

            miss_streak = row.get("miss_streak")
            if isinstance(miss_streak, int) and miss_streak >= 0:
                state.miss_streak = miss_streak

            cooldown_until = row.get("cooldown_until")
            if isinstance(cooldown_until, str):
                try:
                    state.cooldown_until = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
                except Exception:
                    state.cooldown_until = None

            last_verified = row.get("last_verified")
            if isinstance(last_verified, str):
                try:
                    state.last_verified_ts = datetime.fromisoformat(last_verified.replace("Z", "+00:00"))
                except Exception:
                    state.last_verified_ts = None

            last_result = row.get("last_result")
            if isinstance(last_result, bool):
                state.last_result = last_result

            restored += 1

        if restored:
            logger.info(f"lifecycle_restore restored_symbols={restored}")
        return restored

    # ── Supabase persistence ──────────────────────────────────────────

    def persist_to_supabase(self, force: bool = False) -> bool:
        """Persist full lifecycle status as JSON to Supabase bot_kv table.
        Rate-limited to once per _persist_interval seconds unless force=True."""
        if not self._supabase:
            return False
        now_mono = time.monotonic()
        if not force and (now_mono - self._last_persist_ts) < self._persist_interval:
            return False
        try:
            import json
            status = self.get_status()
            payload = json.dumps(status, default=str)
            # Upsert into bot_kv table (key-value store)
            self._supabase.table("bot_kv").upsert(
                {"key": self._persist_key, "value": payload, "updated_at": datetime.now(timezone.utc).isoformat()},
                on_conflict="key",
            ).execute()
            self._last_persist_ts = now_mono
            return True
        except Exception as exc:
            logger.warning(f"lifecycle_persist_error: {exc}")
            return False

    @staticmethod
    def load_from_supabase(supabase_client) -> Optional[Dict[str, Any]]:
        """Load lifecycle status from Supabase bot_kv. Used by dashboard container."""
        if not supabase_client:
            return None
        try:
            import json
            res = supabase_client.table("bot_kv").select("value, updated_at").eq(
                "key", "signal_lifecycle_state"
            ).limit(1).execute()
            rows = getattr(res, "data", None) or []
            if not rows:
                return None
            data = json.loads(rows[0]["value"])
            data["_persisted_at"] = rows[0].get("updated_at")
            return data
        except Exception as exc:
            logger.warning(f"lifecycle_load_error: {exc}")
            return None

    def get_signal_status(self, symbol: str) -> str:
        """Get simple status string for a symbol: ready/pending/cooldown/blacklisted."""
        now = datetime.now(timezone.utc)
        is_bl, _ = self.is_blacklisted_hour(now)
        if is_bl and self._blacklist_blocking:
            return "blacklisted"
        state = self._get_state(symbol)
        if state.pending_direction:
            return "pending"
        if state.cooldown_until and now < state.cooldown_until:
            return "cooldown"
        return "ready"
