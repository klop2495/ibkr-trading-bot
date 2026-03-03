"""H30 Original by-hour mode helpers.

Env:
- H30_ORIGINAL_MODE=off|normal|invert|by_hour
- H30_ORIGINAL_INVERT_HOURS=1,5,11,15,16
- H30_ORIGINAL_NORMAL_HOURS=2,3,4,9,10,12
"""

from __future__ import annotations

import os
from typing import Optional, Set, Tuple


def _parse_hours(raw: str) -> Set[int]:
    out: Set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            h = int(part)
        except Exception:
            continue
        if 0 <= h <= 23:
            out.add(h)
    return out


def classify_original_mode(hour_utc: Optional[int]) -> str:
    """Return one of: NORM | INV | OFF."""
    mode = (os.getenv("H30_ORIGINAL_MODE", "off") or "off").strip().lower()
    if mode == "normal":
        return "NORM"
    if mode == "invert":
        return "INV"
    if mode != "by_hour":
        return "OFF"
    if hour_utc is None:
        return "OFF"

    inv = _parse_hours(os.getenv("H30_ORIGINAL_INVERT_HOURS", ""))
    norm = _parse_hours(os.getenv("H30_ORIGINAL_NORMAL_HOURS", ""))
    if hour_utc in inv:
        return "INV"
    if hour_utc in norm:
        return "NORM"
    return "OFF"


def effective_h30_direction(direction: Optional[str], mode_badge: str) -> Optional[str]:
    """Apply mode to original H30 direction."""
    d = (direction or "").lower()
    if d not in {"up", "down"}:
        return direction
    if mode_badge == "INV":
        return "down" if d == "up" else "up"
    if mode_badge == "NORM":
        return d
    return None


def classify_with_effective(direction: Optional[str], hour_utc: Optional[int]) -> Tuple[str, Optional[str]]:
    badge = classify_original_mode(hour_utc)
    eff = effective_h30_direction(direction, badge)
    return badge, eff

