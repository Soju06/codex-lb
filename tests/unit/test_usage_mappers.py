from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.usage.types import UsageWindowRow
from app.db.models import UsageHistory
from app.modules.usage.mappers import usage_history_to_window_row

pytestmark = pytest.mark.unit


def _entry(
    *,
    account_id: str = "acc1",
    used_percent: float = 42.5,
    reset_at: int | None = 1_700_000_000,
    window_minutes: int | None = 60,
    recorded_at: datetime | None = None,
) -> UsageHistory:
    entry = UsageHistory()
    entry.account_id = account_id
    entry.used_percent = used_percent
    entry.reset_at = reset_at
    entry.window_minutes = window_minutes
    entry.recorded_at = recorded_at or datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
    return entry


def test_maps_all_window_fields() -> None:
    entry = _entry()
    row = usage_history_to_window_row(entry)
    assert isinstance(row, UsageWindowRow)
    assert row.account_id == "acc1"
    assert row.used_percent == pytest.approx(42.5)
    assert row.reset_at == 1_700_000_000
    assert row.window_minutes == 60
    assert row.recorded_at == datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def test_passes_through_nullable_fields() -> None:
    entry = _entry(reset_at=None, window_minutes=None)
    row = usage_history_to_window_row(entry)
    assert row.reset_at is None
    assert row.window_minutes is None
    # Required fields stay populated.
    assert row.account_id == "acc1"
    assert row.used_percent == pytest.approx(42.5)


def test_returns_distinct_rows_per_entry() -> None:
    a = _entry(account_id="acc-a", used_percent=10.0)
    b = _entry(account_id="acc-b", used_percent=20.0)
    rows = [usage_history_to_window_row(e) for e in (a, b)]
    assert [r.account_id for r in rows] == ["acc-a", "acc-b"]
    assert [r.used_percent for r in rows] == [pytest.approx(10.0), pytest.approx(20.0)]
