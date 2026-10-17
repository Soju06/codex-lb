from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.auth.dependencies import validate_dashboard_session
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.schemas import ReportsOptionsResponse
from app.modules.reports.service import ReportsService

pytestmark = pytest.mark.integration


async def test_concurrent_options_requests_share_cache_initialized_at_startup(async_client, app_instance, monkeypatch):
    caches = app_instance.state.reports_caches
    entered = asyncio.Event()
    release = asyncio.Event()

    async def compute(**kwargs):
        entered.set()
        await release.wait()
        return ReportsOptionsResponse(models=["m1"], useragents=[])

    compute_mock = AsyncMock(side_effect=compute)
    monkeypatch.setattr(ReportsService, "get_options", compute_mock)
    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        requests = [tasks.create_task(async_client.get("/api/reports/options")) for _ in range(10)]
        await entered.wait()
        release.set()
    assert app_instance.state.reports_caches is caches
    assert all(request.result().status_code == 200 for request in requests)
    assert all(request.result().json() == {"models": ["m1"], "useragents": []} for request in requests)
    compute_mock.assert_awaited_once()


async def test_options_are_scoped_without_full_report_or_speed_queries(async_client, db_setup, monkeypatch):
    import app.modules.reports.repository as module

    monkeypatch.setattr(module, "_daily_speed_medians_stmt", lambda *_: pytest.fail("options executed median SQL"))
    monkeypatch.setattr(ReportsRepository, "aggregate_summary", AsyncMock(side_effect=AssertionError("full report")))
    async with SessionLocal() as session:
        for model, group, key in [
            ("m1", "CLI", "key1"),
            ("m2", None, "key1"),
            ("m3", "SDK", "key2"),
            ("m4", " ", "key1"),
        ]:
            session.add(
                RequestLog(
                    request_id=model,
                    model=model,
                    status="success",
                    api_key_id=key,
                    useragent_group=group,
                    requested_at=datetime(2026, 6, 1),
                )
            )
        await session.commit()
    response = await async_client.get(
        "/api/reports/options", params={"start_date": "2026-06-01", "end_date": "2026-06-30", "api_key_id": "key1"}
    )
    assert response.status_code == 200
    assert response.json() == {"models": ["m1", "m2", "m4"], "useragents": ["CLI", "Missing User-Agent"]}


async def test_long_report_skips_speed_and_reuses_successful_cache(async_client, db_setup, monkeypatch):
    import app.modules.reports.repository as module

    monkeypatch.setattr(module, "_daily_speed_medians_stmt", lambda *_: pytest.fail("long report executed median SQL"))
    query = {"start_date": "2026-06-01", "end_date": "2026-08-29"}
    first = await async_client.get("/api/reports", params=query)
    assert first.status_code == 200
    assert first.json()["speedMetricsAvailable"] is False
    assert first.json()["speedMetricsMaxDays"] == 7
    monkeypatch.setattr(ReportsRepository, "aggregate_summary", AsyncMock(side_effect=AssertionError("cache miss")))
    second = await async_client.get("/api/reports", params=query)
    assert second.status_code == 200
    assert second.json() == first.json()


async def test_options_validate_dates_and_require_dashboard_auth(async_client, app_instance):
    for query in [
        {"start_date": "2026-06-02", "end_date": "2026-06-01"},
        {"start_date": "2020-01-01", "end_date": "2026-01-01"},
    ]:
        response = await async_client.get("/api/reports/options", params=query)
        assert response.status_code == 400

    async def unauthorized():
        raise HTTPException(status_code=401)

    app_instance.dependency_overrides[validate_dashboard_session] = unauthorized
    assert (await async_client.get("/api/reports/options")).status_code == 401
