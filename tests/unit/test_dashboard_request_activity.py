from __future__ import annotations

from datetime import datetime
from typing import cast

import pytest

from app.modules.accounts.usage_time_rollup_read import LabeledWindow
from app.modules.dashboard import service as service_module
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.service import DashboardService
from app.modules.request_logs.repository import RequestActivityDay


class _ActivityRepository:
    def __init__(self) -> None:
        self.windows: list[LabeledWindow] = []

    async def aggregate_request_activity(self, windows: list[LabeledWindow]) -> list[RequestActivityDay]:
        self.windows = windows
        return [RequestActivityDay(date="2026-08-10", requests=1)]


@pytest.mark.asyncio
async def test_request_activity_starts_on_first_day_of_month_five_months_back(monkeypatch) -> None:
    repository = _ActivityRepository()
    monkeypatch.setattr(service_module, "utcnow", lambda: datetime(2026, 8, 10, 12, 34, 56))

    response = await DashboardService(cast(DashboardRepository, repository)).get_request_activity()

    assert repository.windows[0] == (
        "2026-03-01",
        datetime(2026, 3, 1),
        datetime(2026, 3, 2),
    )
    assert repository.windows[-1] == (
        "2026-08-10",
        datetime(2026, 8, 10),
        datetime(2026, 8, 10, 12, 34, 56),
    )
    assert response.days[0].date == "2026-08-10"


@pytest.mark.asyncio
async def test_request_activity_uses_independent_dst_safe_local_midnights(monkeypatch) -> None:
    repository = _ActivityRepository()
    monkeypatch.setattr(service_module, "utcnow", lambda: datetime(2026, 8, 10, 12, 34, 56))

    await DashboardService(cast(DashboardRepository, repository)).get_request_activity("America/New_York")

    windows = {label: (start, end) for label, start, end in repository.windows}
    assert windows["2026-03-08"] == (datetime(2026, 3, 8, 5), datetime(2026, 3, 9, 4))
    assert windows["2026-03-09"] == (datetime(2026, 3, 9, 4), datetime(2026, 3, 10, 4))


@pytest.mark.asyncio
async def test_request_activity_invalid_timezone_falls_back_to_utc(monkeypatch) -> None:
    repository = _ActivityRepository()
    monkeypatch.setattr(service_module, "utcnow", lambda: datetime(2026, 8, 10, 12, 34, 56))

    await DashboardService(cast(DashboardRepository, repository)).get_request_activity("not/a-timezone")

    assert repository.windows[0][1:] == (datetime(2026, 3, 1), datetime(2026, 3, 2))
