"""
Recommended windows helpers (UTC).

Fail-closed policy:
- If FORECAST_RECOMMENDED_WINDOWS_UTC is missing or invalid, no windows are active.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class UtcWindow:
    start_min: int
    end_min: int
    label: str


def _parse_hm(text: str) -> Optional[int]:
    text = text.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) == 1:
        if not parts[0].isdigit():
            return None
        h = int(parts[0])
        m = 0
    elif len(parts) == 2:
        if not parts[0].isdigit() or not parts[1].isdigit():
            return None
        h = int(parts[0])
        m = int(parts[1])
    else:
        return None
    if h < 0 or h > 23 or m < 0 or m > 59:
        return None
    return h * 60 + m


def _to_label(start_min: int, end_min: int) -> str:
    def hm(v: int) -> str:
        return f"{(v // 60):02d}:{(v % 60):02d}"

    return f"{hm(start_min)}-{hm(end_min)}"


def get_recommended_windows_utc() -> List[UtcWindow]:
    raw = (os.getenv("FORECAST_RECOMMENDED_WINDOWS_UTC", "") or "").strip()
    if not raw:
        return []
    out: List[UtcWindow] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        bits = token.split("-")
        if len(bits) != 2:
            continue
        start = _parse_hm(bits[0])
        end = _parse_hm(bits[1])
        if start is None or end is None:
            continue
        out.append(UtcWindow(start_min=start, end_min=end, label=_to_label(start, end)))
    return out


def minute_in_window(minute_of_day: int, window: UtcWindow) -> bool:
    if window.start_min <= window.end_min:
        return window.start_min <= minute_of_day <= window.end_min
    # cross-midnight
    return minute_of_day >= window.start_min or minute_of_day <= window.end_min


def classify_utc_timestamp(ts: str) -> Tuple[bool, Optional[str], Optional[int], Optional[int]]:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return False, None, None, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    minute_of_day = dt_utc.hour * 60 + dt_utc.minute
    windows = get_recommended_windows_utc()
    for w in windows:
        if minute_in_window(minute_of_day, w):
            return True, w.label, dt_utc.hour, dt_utc.minute
    return False, None, dt_utc.hour, dt_utc.minute


def get_recommended_window_labels() -> List[str]:
    return [w.label for w in get_recommended_windows_utc()]


def is_alt6_recommended_timestamp(ts: str) -> bool:
    in_window, _, _, _ = classify_utc_timestamp(ts)
    return in_window
