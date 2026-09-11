import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import RequestLog
from app.db.session import get_background_session
from app.modules.model_sources import governance
from app.modules.model_sources.forwarding import SourceResponsesCompletion, SourceUsage
from app.modules.model_sources.health_checks import (
    HEALTH_CHECK_INTERVAL_SECONDS,
    CompanyModelHealthScheduler,
)
from app.modules.proxy.source_admission import get_source_bulkhead
from app.modules.request_logs.repository import RequestLogsRepository
from tests.integration.test_llmbox_source import create_source, install_cache

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/models", "/v1/models?client_version=0.154.0", "/backend-api/codex/models"])
async def test_catalog_health_recovery_and_isolation(async_client, monkeypatch, tmp_path, path):
    cache = install_cache(monkeypatch, tmp_path)
    source = (await create_source(async_client, models=[{"model": "company-test"}, {"model": "sibling"}])).json()
    sid = source["id"]
    await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})

    async def visible():
        response = await async_client.get(path)
        assert response.status_code == 200
        body = response.json()
        return {row.get("id", row.get("slug")) for row in body.get("data", body.get("models", []))}

    async def record(
        model="company-test",
        status="success",
        latency=1000,
        first=500,
        age=0,
        code=None,
        source=governance.COMPANY_HEALTH_CHECK_SOURCE,
    ):
        async with get_background_session() as session:
            await RequestLogsRepository(session).add_log(
                account_id=None,
                request_id=str(uuid4()),
                model=model,
                model_source_id=sid,
                model_source_kind="llmbox",
                status=status,
                input_tokens=1,
                output_tokens=1,
                latency_ms=latency,
                latency_first_token_ms=first,
                error_code=code,
                source=source,
                requested_at=governance.utcnow() - timedelta(hours=age),
            )

    assert "company-test" not in await visible()
    await record(age=4)
    assert "company-test" not in await visible()
    await record()
    await record(model="sibling")
    assert {"company-test", "sibling"} <= await visible()
    await record(status="error", code="model_source_timeout", source="proxy")
    assert "company-test" in await visible()
    await record(status="error", code="company_health_check_timeout")
    assert "company-test" not in await visible()
    assert "sibling" in await visible()
    await record(latency=31000)
    assert "company-test" not in await visible()
    await record(first=31000)
    assert "company-test" not in await visible()
    await record(latency=None)
    assert "company-test" not in await visible()
    await record()
    assert "company-test" in await visible()
    await async_client.patch(f"/api/model-sources/{sid}", json={"localTokenBudget": 1})
    assert "company-test" not in await visible()
    await async_client.patch(f"/api/model-sources/{sid}", json={"localTokenBudget": None})
    cache.unlink()
    assert "sibling" not in await visible()
    configured = (await async_client.get("/api/model-sources/")).json()["sources"][0]
    assert len(configured["models"]) == 2


@pytest.mark.asyncio
async def test_scheduler_probes_due_models_and_records_health(async_client, monkeypatch, tmp_path):
    install_cache(monkeypatch, tmp_path)
    source = (await create_source(async_client)).json()
    sid = source["id"]
    await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
    calls = []

    async def successful_probe(_source, payload, **_kwargs):
        calls.append(payload)
        return SourceResponsesCompletion(
            payload={"id": "health"},
            usage=SourceUsage(input_tokens=3, output_tokens=1),
            timings=None,
            upstream_status_code=200,
        )

    monkeypatch.setattr("app.modules.model_sources.health_checks.forward_responses", successful_probe)
    scheduler = CompanyModelHealthScheduler(check_interval_seconds=HEALTH_CHECK_INTERVAL_SECONDS)
    await scheduler._probe_once_as_leader()
    assert calls == [
        {
            "model": "company-test",
            "input": "Reply with exactly OK.",
            "stream": False,
            "max_output_tokens": 8,
        }
    ]
    models = (await async_client.get("/v1/models")).json()
    assert "company-test" in {row["id"] for row in models["data"]}

    await scheduler._probe_once_as_leader()
    assert len(calls) == 1
    async with get_background_session() as session:
        row = (
            await session.execute(
                select(RequestLog.status, RequestLog.input_tokens, RequestLog.output_tokens).where(
                    RequestLog.model_source_id == sid,
                    RequestLog.source == governance.COMPANY_HEALTH_CHECK_SOURCE,
                )
            )
        ).one()
    assert row == ("success", 3, 1)


@pytest.mark.asyncio
async def test_scheduler_timeout_hides_model_and_releases_capacity(async_client, monkeypatch, tmp_path):
    install_cache(monkeypatch, tmp_path)
    source = (await create_source(async_client, maxConcurrency=1)).json()
    sid = source["id"]
    await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})

    async def slow_probe(_source, _payload, **_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr("app.modules.model_sources.health_checks.forward_responses", slow_probe)
    scheduler = CompanyModelHealthScheduler(probe_timeout_seconds=0.01)
    await scheduler._probe_once_as_leader()

    async with get_background_session() as session:
        row = (
            await session.execute(
                select(RequestLog.status, RequestLog.error_code).where(
                    RequestLog.model_source_id == sid,
                    RequestLog.source == governance.COMPANY_HEALTH_CHECK_SOURCE,
                )
            )
        ).one()
    assert row == ("error", "company_health_check_timeout")
    assert get_source_bulkhead().in_flight(sid) == 0
    models = (await async_client.get("/v1/models")).json()
    assert "company-test" not in {row["id"] for row in models["data"]}
