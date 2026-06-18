from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.modules.reports.repository import DailyReportRangeTooLargeError, ReportsRepository
from app.modules.reports.service import ReportsService

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_get_reports_rejects_oversized_range_after_applying_default_end_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = SimpleNamespace(
        aggregate_summary=AsyncMock(),
        aggregate_daily_rows=AsyncMock(),
        aggregate_by_model=AsyncMock(),
        aggregate_by_account=AsyncMock(),
        earliest_report_activity_at=AsyncMock(),
    )
    service = ReportsService(cast(ReportsRepository, repo))
    fixed_now = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.modules.reports.service.utcnow", lambda: fixed_now)

    with pytest.raises(DailyReportRangeTooLargeError, match="730 days or less"):
        await service.get_reports(start_date=date(2020, 1, 1))

    repo.aggregate_summary.assert_not_awaited()
    repo.aggregate_daily_rows.assert_not_awaited()
    repo.aggregate_by_model.assert_not_awaited()
    repo.aggregate_by_account.assert_not_awaited()
    repo.earliest_report_activity_at.assert_not_awaited()
