from __future__ import annotations

import json
import logging
from typing import Final

import pytest
from httpx import Response
from sqlalchemy import select

from app.core.audit.service import drain_audit_log_tasks
from app.core.auth.dependencies import require_dashboard_write_access
from app.core.exceptions import DashboardPermissionError
from app.db.models import AuditLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

_EXPECTED_HEADERS: Final = (
    ("cache-control", "no-store, no-cache, must-revalidate, private"),
    ("pragma", "no-cache"),
    ("expires", "0"),
)


def _headers(response: Response) -> tuple[tuple[str, str | None], ...]:
    return tuple((name, response.headers.get(name)) for name, _value in _EXPECTED_HEADERS)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("/api/api-keys", "/api/api-keys/"))
async def test_api_key_secret_no_store_when_created(
    async_client,
    caplog: pytest.LogCaptureFixture,
    path: str,
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = await async_client.post(path, json={"name": f"secret-{path.endswith('/')}"})
        assert await drain_audit_log_tasks(timeout_seconds=1) is True

    assert response.status_code == 200
    assert _headers(response) == _EXPECTED_HEADERS
    payload = response.json()
    assert payload["key"].startswith("sk-clb-")
    async with SessionLocal() as session:
        result = await session.execute(select(AuditLog).where(AuditLog.action == "api_key_created"))
        audit_log = result.scalar_one()
    assert audit_log.details == json.dumps({"key_id": payload["id"]})
    assert payload["key"] not in caplog.text
    assert payload["key"] not in (audit_log.details or "")


@pytest.mark.asyncio
async def test_api_key_secret_no_store_when_regenerated(
    async_client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = await async_client.post("/api/api-keys/", json={"name": "regenerated-secret"})
    assert created.status_code == 200
    assert await drain_audit_log_tasks(timeout_seconds=1) is True
    caplog.clear()

    with caplog.at_level(logging.DEBUG):
        regenerated = await async_client.post(f"/api/api-keys/{created.json()['id']}/regenerate")

    assert regenerated.status_code == 200
    assert _headers(regenerated) == _EXPECTED_HEADERS
    assert regenerated.json()["key"] != created.json()["key"]
    async with SessionLocal() as session:
        audit_logs = (await session.execute(select(AuditLog))).scalars().all()
    serialized = json.dumps([row.details for row in audit_logs])
    assert regenerated.json()["key"] not in caplog.text
    assert regenerated.json()["key"] not in serialized


@pytest.mark.asyncio
async def test_api_key_secret_no_store_preserves_write_authorization(
    app_instance,
    async_client,
) -> None:
    async def reject_write_access() -> None:
        raise DashboardPermissionError(
            "Read-only dashboard access cannot modify dashboard state",
            code="read_only_access",
        )

    app_instance.dependency_overrides[require_dashboard_write_access] = reject_write_access
    try:
        denied = (
            await async_client.post("/api/api-keys", json={"name": "blocked"}),
            await async_client.post("/api/api-keys/", json={"name": "blocked-slash"}),
            await async_client.post("/api/api-keys/missing/regenerate"),
        )
    finally:
        app_instance.dependency_overrides.pop(require_dashboard_write_access, None)

    assert [(response.status_code, response.json()["error"]["code"]) for response in denied] == [
        (403, "read_only_access"),
        (403, "read_only_access"),
        (403, "read_only_access"),
    ]
    assert all(
        _headers(response) == (("cache-control", None), ("pragma", None), ("expires", None)) for response in denied
    )
