from __future__ import annotations

from datetime import datetime, timedelta

TEAM_WINDOWS: tuple[str, ...] = ("day", "week", "month")


def window_start(window: str, now: datetime) -> datetime:
    """Start of the UTC calendar window containing ``now``."""

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "day":
        return midnight
    if window == "week":
        return midnight - timedelta(days=midnight.weekday())
    if window == "month":
        return midnight.replace(day=1)
    raise ValueError(f"Unknown team window: {window}")


def window_end(window: str, now: datetime) -> datetime:
    """Exclusive end of the UTC calendar window containing ``now``."""

    start = window_start(window, now)
    if window == "day":
        return start + timedelta(days=1)
    if window == "week":
        return start + timedelta(days=7)
    if window == "month":
        if start.month == 12:
            return start.replace(year=start.year + 1, month=1)
        return start.replace(month=start.month + 1)
    raise ValueError(f"Unknown team window: {window}")
