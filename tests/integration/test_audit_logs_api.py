from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

import app.core.auth.dependencies as auth_dependencies
from app.core.auth.dashboard_access import DashboardAuthMode, admin_principal, guest_principal
from app.db.models import AuditLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

ACTION = "guest_identity_privacy_test"
ACTOR_IP = "203.0.113.77"
REQUEST_ID = "req-sensitive"
DETAILS = '{"account_id":"acc-sensitive","key_id":"key-sensitive"}'


async def _insert_sensitive_audit_log() -> None:
    async with SessionLocal() as session:
        session.add(
            AuditLog(
                action=ACTION,
                actor_ip=ACTOR_IP,
                details=DETAILS,
                request_id=REQUEST_ID,
                timestamp=datetime(2026, 8, 31, tzinfo=UTC),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_guest_security_audit_identity_is_denied(
    app_instance: FastAPI,
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _insert_sensitive_audit_log()
    original = auth_dependencies.validate_dashboard_session
    app_instance.dependency_overrides[original] = guest_principal
    monkeypatch.setattr(
        auth_dependencies,
        "validate_dashboard_session",
        AsyncMock(return_value=guest_principal()),
    )
    try:
        response = await async_client.get("/api/audit-logs", params={"action": ACTION})
    finally:
        app_instance.dependency_overrides.pop(original, None)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_access_required"
    assert ACTOR_IP not in response.text
    assert "acc-sensitive" not in response.text
    assert "key-sensitive" not in response.text
    assert REQUEST_ID not in response.text


@pytest.mark.asyncio
async def test_admin_security_audit_identity_preserves_operator_detail(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _insert_sensitive_audit_log()
    monkeypatch.setattr(
        auth_dependencies,
        "validate_dashboard_session",
        AsyncMock(
            return_value=admin_principal(
                auth_mode=DashboardAuthMode.STANDARD,
            )
        ),
    )
    response = await async_client.get("/api/audit-logs", params={"action": ACTION})

    assert response.status_code == 200
    entry = response.json()[0]
    assert entry["actorIp"] == ACTOR_IP
    assert entry["details"] == {
        "account_id": "acc-sensitive",
        "key_id": "key-sensitive",
    }
    assert entry["requestId"] == REQUEST_ID
