from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def naive_utc_to_epoch(dt: datetime) -> int:
    """Convert a naive-UTC datetime to Unix epoch seconds."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def from_epoch_seconds(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)
