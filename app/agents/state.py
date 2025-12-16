from datetime import datetime, timedelta, timezone


class AgentsCircuitBreakerState:
    def __init__(self) -> None:
        self.failure_count: int = 0
        self.opened_at: datetime | None = None

    def is_open(self, now: datetime, cooldown_seconds: float) -> bool:
        if self.opened_at is None:
            return False
        if now - self.opened_at >= timedelta(seconds=cooldown_seconds):
            self.reset()
            return False
        return True

    def record_failures(self, count: int, now: datetime, threshold: int) -> None:
        self.failure_count += count
        if self.failure_count >= threshold and self.opened_at is None:
            self.opened_at = now

    def record_success(self) -> None:
        # Reset only when not in open state; open state is time-bound.
        if self.opened_at is None:
            self.failure_count = 0

    def reset(self) -> None:
        self.failure_count = 0
        self.opened_at = None

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)
