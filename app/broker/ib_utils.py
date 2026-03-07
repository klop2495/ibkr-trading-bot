from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional


class IBCallError(RuntimeError):
    code = "ib_call_error"


class IBTimeoutError(IBCallError):
    code = "ib_timeout"


class IBGatewayNotReady(IBCallError):
    code = "ib_gateway_not_ready"


class IBConnectionError(IBCallError):
    code = "ib_connection_failed"


def ib_call_with_timeout(
    fn: Callable[[], Any],
    timeout_s: float,
    *,
    description: str = "ib_call",
    on_timeout: Optional[Callable[[], None]] = None,
) -> Any:
    result: dict[str, Any] = {}
    error: dict[str, Exception] = {}
    done = threading.Event()

    def _run() -> None:
        loop = None
        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            result["value"] = fn()
        except Exception as exc:  # pragma: no cover - pass-through
            error["exc"] = exc
        finally:
            if loop:
                loop.close()
                asyncio.set_event_loop(None)
            done.set()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    if not done.wait(timeout_s):
        if on_timeout:
            try:
                on_timeout()
            except Exception:
                pass
        raise IBTimeoutError(f"{description} timed out after {timeout_s}s")
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


def ib_request_with_timeout(
    ib: Any,
    fn: Callable[[], Any],
    timeout_s: float,
    *,
    description: str = "ib_request",
) -> Any:
    previous_timeout = getattr(ib, "RequestTimeout", None)
    try:
        ib.RequestTimeout = max(previous_timeout or 0, timeout_s)
        return fn()
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise IBTimeoutError(f"{description} timed out after {timeout_s}s") from exc
    finally:
        if previous_timeout is not None:
            ib.RequestTimeout = previous_timeout


def ib_probe_ready(ib: Any, timeout_s: float = 5.0) -> None:
    try:
        ib_request_with_timeout(
            ib,
            lambda: ib.reqCurrentTime(),
            timeout_s,
            description="ib_probe_current_time",
        )
    except Exception as exc:
        raise IBGatewayNotReady(str(exc)) from exc


def connect_with_backoff(
    ib: Any,
    *,
    host: str,
    port: int,
    client_id: int,
    timeout_s: float,
    max_attempts: int,
    backoff_schedule: Optional[list[float]] = None,
    probe_timeout_s: float = 5.0,
) -> None:
    delays = backoff_schedule or [1.0, 2.0, 5.0, 10.0, 30.0]
    last_error: Optional[Exception] = None
    for attempt in range(max_attempts):
        delay = delays[min(attempt, len(delays) - 1)]
        try:
            ib.RequestTimeout = max(getattr(ib, "RequestTimeout", 0) or 0, timeout_s)
            ib.connect(host, port, clientId=client_id, timeout=timeout_s)
            ib_probe_ready(ib, timeout_s=probe_timeout_s)
            return
        except Exception as exc:
            last_error = exc
            try:
                ib.disconnect()
            except Exception:
                pass
            time.sleep(delay)
    raise IBConnectionError(str(last_error) if last_error else "unknown")


@dataclass
class IBFailSafeState:
    disabled: bool = False
    untrusted_since: Optional[datetime] = None
    ok_streak: int = 0
    last_reason: Optional[str] = None

    def mark_untrusted(self, reason: str, now: datetime, disable_after_s: int) -> bool:
        if self.untrusted_since is None:
            self.untrusted_since = now
        self.ok_streak = 0
        self.last_reason = reason
        if self.disabled:
            return False
        if (now - self.untrusted_since).total_seconds() >= disable_after_s:
            self.disabled = True
            return True
        return False

    def mark_ok(self, now: datetime, recovery_cycles: int) -> bool:
        self.last_reason = None
        if not self.disabled:
            self.untrusted_since = None
            self.ok_streak = 0
            return False
        self.ok_streak += 1
        if self.ok_streak >= recovery_cycles:
            self.disabled = False
            self.untrusted_since = None
            self.ok_streak = 0
            return True
        return False

    def untrusted_duration_s(self, now: datetime) -> float:
        if not self.untrusted_since:
            return 0.0
        return (now - self.untrusted_since).total_seconds()
