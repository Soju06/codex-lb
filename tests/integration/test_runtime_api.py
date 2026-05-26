from __future__ import annotations

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.modules.runtime.schemas import RuntimeVersionResponse

pytestmark = pytest.mark.integration


class _RuntimeVersionServiceStub:
    async def get_version_status(self) -> RuntimeVersionResponse:
        return RuntimeVersionResponse(
            current_version="1.19.0",
            latest_version="1.20.0",
            update_available=True,
            checked_at=datetime(2026, 5, 26, 0, 0, 0),
            source="github",
            release_url="https://github.com/Soju06/codex-lb/releases/latest",
        )


@pytest.mark.asyncio
async def test_runtime_version_endpoint_returns_camel_case_contract(async_client, monkeypatch) -> None:
    from app.modules.runtime import api as runtime_api

    monkeypatch.setattr(runtime_api, "get_runtime_version_service", lambda: _RuntimeVersionServiceStub())

    response = await async_client.get("/api/runtime/version")

    assert response.status_code == 200
    assert response.json() == {
        "currentVersion": "1.19.0",
        "latestVersion": "1.20.0",
        "updateAvailable": True,
        "checkedAt": "2026-05-26T00:00:00Z",
        "source": "github",
        "releaseUrl": "https://github.com/Soju06/codex-lb/releases/latest",
    }


@pytest.mark.asyncio
async def test_runtime_version_endpoint_requires_dashboard_auth_for_remote_clients(app_instance) -> None:
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("203.0.113.11", 50001))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as remote_client:
            response = await remote_client.get("/api/runtime/version")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bootstrap_required"
