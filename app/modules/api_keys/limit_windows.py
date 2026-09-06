from __future__ import annotations

from datetime import datetime, timedelta

from app.db.models import LimitWindow

_DAILY_LIMIT_UTC_OFFSET = timedelta(hours=7)  # Asia/Ho_Chi_Minh


def next_limit_reset(now: datetime, window: LimitWindow) -> datetime:
    if window == LimitWindow.FIVE_HOURS:
        return now + timedelta(hours=5)
    if window == LimitWindow.SEVEN_DAYS:
        return now + timedelta(days=7)
    if window == LimitWindow.DAILY:
        local_now = now + _DAILY_LIMIT_UTC_OFFSET
        local_midnight = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return local_midnight - _DAILY_LIMIT_UTC_OFFSET
    if window == LimitWindow.WEEKLY:
        return now + timedelta(days=7)
    if window == LimitWindow.MONTHLY:
        return now + timedelta(days=30)
    return now + timedelta(days=7)


def advance_limit_reset(reset_at: datetime, now: datetime, window: LimitWindow) -> datetime:
    if window == LimitWindow.DAILY and reset_at <= now:
        return next_limit_reset(now, window)
    delta = limit_window_delta(window)
    next_reset = reset_at
    while next_reset <= now:
        next_reset += delta
    return next_reset


def limit_window_delta(window: LimitWindow) -> timedelta:
    if window == LimitWindow.FIVE_HOURS:
        return timedelta(hours=5)
    if window == LimitWindow.SEVEN_DAYS:
        return timedelta(days=7)
    if window == LimitWindow.DAILY:
        return timedelta(days=1)
    if window == LimitWindow.WEEKLY:
        return timedelta(days=7)
    if window == LimitWindow.MONTHLY:
        return timedelta(days=30)
    return timedelta(days=7)
