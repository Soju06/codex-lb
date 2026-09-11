from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.model_sources.governance import COMPANY_HEALTH_CHECK_SOURCE
from app.modules.proxy.source_admission import get_source_bulkhead
from app.modules.request_logs.repository import RequestLogsRepository
from tests.integration.test_llmbox_source import create_source, install_cache

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_dashboard_probe_states_and_catalog_agree(async_client, monkeypatch, tmp_path):
    install_cache(monkeypatch, tmp_path)
    names = ["healthy", "failed", "slow", "stale", "unknown", "disabled"]
    created = (await create_source(async_client, models=[{"model": name} for name in names])).json()
    sid = created["id"]
    await async_client.patch(
        f"/api/model-sources/{sid}",
        json={
            "isEnabled": True,
            "models": [{"model": name, "isEnabled": name != "disabled"} for name in names],
        },
    )
    now = utcnow()
    async with get_background_session() as session:
        repo = RequestLogsRepository(session)
        for name in names:
            if name == "unknown":
                continue
            await repo.add_log(
                account_id=None,
                request_id=str(uuid4()),
                model=name,
                model_source_id=sid,
                model_source_kind="llmbox",
                source=COMPANY_HEALTH_CHECK_SOURCE,
                status="error" if name == "failed" else "success",
                error_code="company_health_check_timeout" if name == "failed" else None,
                error_message="private upstream context must not be displayed" if name == "failed" else None,
                latency_ms=31000 if name == "slow" else 1000,
                input_tokens=None if name == "failed" else 3,
                output_tokens=None if name == "failed" else 1,
                requested_at=now - timedelta(hours=4 if name == "stale" else 0),
            )
        # User traffic must neither validate an unknown model nor replace a failed probe.
        for name in ("unknown", "failed"):
            await repo.add_log(
                account_id=None,
                request_id=str(uuid4()),
                model=name,
                model_source_id=sid,
                source="model_source",
                input_tokens=1,
                output_tokens=1,
                error_code=None,
                status="success",
                latency_ms=50,
            )
    response = await async_client.get("/api/model-sources/")
    assert response.status_code == 200
    assert "private upstream context" not in response.text
    source = response.json()["sources"][0]
    checks = {model["model"]: model["healthCheck"] for model in source["models"]}
    assert {name: check["state"] for name, check in checks.items()} == {
        "healthy": "healthy",
        "failed": "unhealthy",
        "slow": "slow",
        "stale": "stale",
        "unknown": "unknown",
        "disabled": "healthy",
    }
    assert checks["failed"]["inputTokens"] is None
    assert checks["failed"]["errorCode"] == "company_health_check_timeout"
    assert checks["healthy"]["inputTokens"] == 3
    assert checks["healthy"]["checkedAt"].endswith("Z")
    assert checks["healthy"]["nextDueAt"] is not None
    assert checks["stale"]["checkedAt"] is not None
    assert checks["unknown"]["checkedAt"] is None
    assert checks["disabled"]["nextDueAt"] is None
    assert source["companyStatus"]["healthCheckSummary"] == {
        "enabledModels": 5,
        "freshChecks": 3,
        "visibleModels": 1,
        "intervalSeconds": 7200,
        "timeoutSeconds": 30,
        "freshnessSeconds": 10800,
    }
    for path in ("/v1/models", "/backend-api/codex/models"):
        body = (await async_client.get(path)).json()
        catalog = {row.get("id", row.get("slug")) for row in body.get("data", body.get("models", []))}
        assert {name for name, check in checks.items() if check["catalogVisible"]} == catalog.intersection(names)


@pytest.mark.asyncio
async def test_dashboard_reads_are_passive_and_busy_is_not_probe_failure(async_client, monkeypatch, tmp_path):
    cache = install_cache(monkeypatch, tmp_path)
    source = (await create_source(async_client, maxConcurrency=1)).json()
    sid = source["id"]
    await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
    async with get_background_session() as session:
        await RequestLogsRepository(session).add_log(
            account_id=None,
            request_id=str(uuid4()),
            model="company-test",
            model_source_id=sid,
            source=COMPANY_HEALTH_CHECK_SOURCE,
            status="success",
            latency_ms=50,
            error_code=None,
            input_tokens=5,
            output_tokens=1,
        )

    async def forbidden_probe(*_args, **_kwargs):
        pytest.fail("A dashboard/catalog read must not trigger inference")

    monkeypatch.setattr("app.modules.model_sources.health_checks.forward_responses", forbidden_probe)

    async def read():
        return (await async_client.get("/api/model-sources/")).json()["sources"][0]

    bulkhead = get_source_bulkhead()
    slot = bulkhead.try_acquire(sid, 1)
    assert slot is not None
    try:
        current = await read()
        assert current["companyStatus"]["inFlight"] == 1
        assert current["models"][0]["healthCheck"]["catalogVisible"] is True
    finally:
        bulkhead.release(slot)
    assert (await read())["companyStatus"]["inFlight"] == 0
    await async_client.patch(f"/api/model-sources/{sid}", json={"localTokenBudget": 1})
    check = (await read())["models"][0]["healthCheck"]
    assert check["state"] == "healthy" and check["catalogVisible"] is False
    await async_client.patch(f"/api/model-sources/{sid}", json={"localTokenBudget": None})
    cache.unlink()
    check = (await read())["models"][0]["healthCheck"]
    assert check["state"] == "healthy" and check["catalogVisible"] is False
    await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": False})
    check = (await read())["models"][0]["healthCheck"]
    assert check["catalogVisible"] is False and check["nextDueAt"] is None
