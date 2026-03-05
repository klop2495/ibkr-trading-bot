from datetime import datetime, timezone


class CircuitBreakerState:
    def __init__(self) -> None:
        self.failures: int = 0
        self.opened_at_utc: datetime | None = None
        self.last_failure_utc: datetime | None = None

    def is_open(self, now_utc: datetime, cooldown_sec: int) -> bool:
        if self.opened_at_utc is None:
            return False
        return (now_utc - self.opened_at_utc).total_seconds() < cooldown_sec

    def record_failure(self, now_utc: datetime, threshold: int) -> None:
        self.failures += 1
        self.last_failure_utc = now_utc
        if self.failures >= threshold and self.opened_at_utc is None:
            self.opened_at_utc = now_utc

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at_utc = None
        self.last_failure_utc = None

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)
