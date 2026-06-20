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


@pytest.mark.asyncio
async def test_get_reports_serializes_useragent_breakdown_and_model_request_counts() -> None:
    repo = SimpleNamespace(
        aggregate_summary=AsyncMock(
            side_effect=[
                SimpleNamespace(
                    total_cost_usd=1.2,
                    total_input_tokens=12,
                    total_output_tokens=6,
                    total_cached_tokens=2,
                    total_requests=2,
                    total_errors=0,
                    active_accounts=1,
                ),
                SimpleNamespace(
                    total_cost_usd=0.4,
                    total_input_tokens=4,
                    total_output_tokens=2,
                    total_cached_tokens=0,
                    total_requests=1,
                    total_errors=0,
                    active_accounts=1,
                ),
            ]
        ),
        aggregate_daily_rows=AsyncMock(
            return_value=[
                SimpleNamespace(
                    date="2026-06-01",
                    requests=2,
                    input_tokens=12,
                    output_tokens=6,
                    cached_input_tokens=2,
                    cost_usd=1.2,
                    active_accounts=1,
                    error_count=0,
                )
            ]
        ),
        aggregate_by_model=AsyncMock(return_value=[SimpleNamespace(model="gpt-5.1", cost_usd=1.2, request_count=2)]),
        aggregate_by_account=AsyncMock(
            return_value=[SimpleNamespace(account_id="acc_reports", alias="Reports", cost_usd=1.2, request_count=2)]
        ),
        aggregate_by_useragent=AsyncMock(
            return_value=[SimpleNamespace(useragent_group="opencode", cost_usd=1.2, request_count=2)]
        ),
        earliest_report_activity_at=AsyncMock(return_value=datetime(2026, 5, 1, 0, 0, 0)),
    )
    service = ReportsService(cast(ReportsRepository, repo))

    result = await service.get_reports(
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 1),
        useragent_group="opencode",
    )

    repo.aggregate_summary.assert_any_await(
        datetime(2026, 6, 1, 0, 0, 0),
        datetime(2026, 6, 2, 0, 0, 0),
        None,
        None,
        "opencode",
    )
    repo.aggregate_daily_rows.assert_awaited_once_with(
        date(2026, 6, 1),
        date(2026, 6, 1),
        timezone.utc,
        None,
        None,
        "opencode",
    )
    repo.aggregate_by_model.assert_awaited_once_with(
        datetime(2026, 6, 1, 0, 0, 0),
        datetime(2026, 6, 2, 0, 0, 0),
        None,
        None,
        "opencode",
    )
    repo.aggregate_by_account.assert_awaited_once_with(
        datetime(2026, 6, 1, 0, 0, 0),
        datetime(2026, 6, 2, 0, 0, 0),
        None,
        None,
        "opencode",
    )
    repo.aggregate_by_useragent.assert_awaited_once_with(
        datetime(2026, 6, 1, 0, 0, 0),
        datetime(2026, 6, 2, 0, 0, 0),
        None,
        None,
        "opencode",
    )
    repo.earliest_report_activity_at.assert_awaited_once_with(None, None, "opencode")

    assert result.by_model[0].model == "gpt-5.1"
    assert result.by_model[0].requests == 2
    assert result.by_useragent[0].useragent == "opencode"
    assert result.by_useragent[0].requests == 2
    assert result.by_useragent[0].percentage == 100.0
