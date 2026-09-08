"""Behavioural checks for the fine-grained dashboard permission gates.

The built-in ``admin`` and ``guest`` presets already cover the two extremes.
These tests use hand-built principals that hold the legacy ``write`` alias
without a specific privileged permission, proving that each sensitive route is
gated by its own permission rather than by the generic write gate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

import app.core.auth.dependencies as auth_dependencies
from app.core.auth.dashboard_access import (
    ADMIN_GRANTS,
    DashboardPrincipal,
    DashboardRole,
    Permission,
    Scope,
    admin_principal,
    guest_principal,
    legacy_permissions,
)
from app.core.auth.dashboard_mode import DashboardAuthMode

pytestmark = pytest.mark.integration


def _principal_without(*removed: Permission) -> DashboardPrincipal:
    grants = {permission: scope for permission, scope in ADMIN_GRANTS.items() if permission not in removed}
    return DashboardPrincipal(
        role=DashboardRole.ADMIN,
        permissions=legacy_permissions(grants),
        auth_mode=DashboardAuthMode.STANDARD,
        grants=grants,
    )


def _principal_with_only(**grants: Scope) -> DashboardPrincipal:
    typed = {Permission(name.replace("__", ":")): scope for name, scope in grants.items()}
    return DashboardPrincipal(
        role=DashboardRole.ADMIN,
        permissions=legacy_permissions(typed),
        auth_mode=DashboardAuthMode.STANDARD,
        grants=typed,
    )


def _use_principal(app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch, principal: DashboardPrincipal) -> None:
    # Router-level and permission dependencies resolve the module attribute at
    # call time; handler parameters captured ``Depends(validate_dashboard_session)``
    # at import time and need the FastAPI override as well.
    original = auth_dependencies.validate_dashboard_session
    monkeypatch.setattr(auth_dependencies, "validate_dashboard_session", AsyncMock(return_value=principal))
    monkeypatch.setitem(app_instance.dependency_overrides, original, lambda: principal)


def _assert_permission_required(response, permission: Permission) -> None:
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "permission_required"
    assert error["param"] == permission.value


@pytest.mark.asyncio
async def test_account_export_requires_accounts_export_not_write(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = _principal_without(Permission.ACCOUNTS_EXPORT)
    assert principal.can(auth_dependencies.DashboardPermission.WRITE)
    _use_principal(app_instance, monkeypatch, principal)

    _assert_permission_required(
        await async_client.post("/api/accounts/missing/export/auth"), Permission.ACCOUNTS_EXPORT
    )

    # The same principal keeps ordinary account writes.
    response = await async_client.put("/api/accounts/missing/alias", json={"alias": "still-writable"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_security_settings_fields_require_security_write(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_principal(app_instance, monkeypatch, _principal_without(Permission.SECURITY_WRITE))
    current = (await async_client.get("/api/settings")).json()

    for field in ("totpRequiredOnLogin", "apiKeyAuthEnabled", "guestAccessEnabled", "hideUpstreamQuotaFromApiKeys"):
        response = await async_client.put("/api/settings", json={field: not current[field]})
        _assert_permission_required(response, Permission.SECURITY_WRITE)
    _assert_permission_required(
        await async_client.put(
            "/api/settings", json={"dashboardSessionTtlSeconds": current["dashboardSessionTtlSeconds"] + 3600}
        ),
        Permission.SECURITY_WRITE,
    )

    # Re-sending the stored value is not a change and needs no extra permission.
    response = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": current["apiKeyAuthEnabled"]})
    assert response.status_code == 200, response.text

    # Non-security settings stay writable for the same principal.
    response = await async_client.put("/api/settings", json={"stickyThreadsEnabled": True})
    assert response.status_code == 200, response.text
    assert response.json()["stickyThreadsEnabled"] is True


@pytest.mark.asyncio
async def test_full_form_save_with_unchanged_security_fields_does_not_require_security_write(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dashboard client submits every field on each save; unchanged security values must not trip the gate."""

    _use_principal(app_instance, monkeypatch, _principal_without(Permission.SECURITY_WRITE))
    current = (await async_client.get("/api/settings")).json()

    full_form = dict(current)
    full_form["stickyThreadsEnabled"] = not current["stickyThreadsEnabled"]
    response = await async_client.put("/api/settings", json=full_form)
    assert response.status_code == 200, response.text
    assert response.json()["stickyThreadsEnabled"] is full_form["stickyThreadsEnabled"]

    changed_form = dict(current)
    changed_form["guestAccessEnabled"] = not current["guestAccessEnabled"]
    _assert_permission_required(await async_client.put("/api/settings", json=changed_form), Permission.SECURITY_WRITE)


@pytest.mark.asyncio
async def test_settings_gate_precedence(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A read-only guest is rejected by the generic write gate first.
    _use_principal(app_instance, monkeypatch, guest_principal())
    response = await async_client.put("/api/settings", json={"guestAccessEnabled": True})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "read_only_access"
    assert "param" not in response.json()["error"]

    # security:write alone does not bypass the generic write gate.
    _use_principal(
        app_instance,
        monkeypatch,
        _principal_with_only(dashboard__read=Scope.ALL, ops__write=Scope.ALL, security__write=Scope.ALL),
    )
    response = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "read_only_access"


@pytest.mark.asyncio
async def test_security_boundary_mutations_require_security_write(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_principal(app_instance, monkeypatch, _principal_without(Permission.SECURITY_WRITE))

    _assert_permission_required(
        await async_client.post("/api/firewall/ips", json={"ipAddress": "203.0.113.9"}),
        Permission.SECURITY_WRITE,
    )
    _assert_permission_required(
        await async_client.delete("/api/firewall/ips/203.0.113.9"),
        Permission.SECURITY_WRITE,
    )
    _assert_permission_required(
        await async_client.post(
            "/api/settings/upstream-proxy/endpoints",
            json={"name": "Egress", "scheme": "http", "host": "proxy.internal", "port": 8080},
        ),
        Permission.SECURITY_WRITE,
    )
    _assert_permission_required(
        await async_client.post("/api/dashboard-auth/guest/password", json={"password": "guest-secret-1"}),
        Permission.SECURITY_WRITE,
    )
    _assert_permission_required(
        await async_client.delete("/api/dashboard-auth/guest/password"),
        Permission.SECURITY_WRITE,
    )


@pytest.mark.asyncio
async def test_sensitive_reads_require_their_permission(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_principal(app_instance, monkeypatch, _principal_without(Permission.AUDIT_READ, Permission.CONVERSATIONS_READ))

    _assert_permission_required(await async_client.get("/api/audit-logs"), Permission.AUDIT_READ)
    _assert_permission_required(await async_client.get("/api/conversations"), Permission.CONVERSATIONS_READ)
    _assert_permission_required(
        await async_client.get("/api/request-logs", params={"conversation_id": "conv-1"}),
        Permission.CONVERSATIONS_READ,
    )


@pytest.mark.asyncio
async def test_account_window_projections_require_accounts_read(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_principal(app_instance, monkeypatch, _principal_with_only(dashboard__read=Scope.ALL))

    for path in ("/api/dashboard/overview", "/api/dashboard/projections", "/api/usage/summary", "/api/usage/window"):
        _assert_permission_required(await async_client.get(path), Permission.ACCOUNTS_READ)

    response = await async_client.get("/api/models")
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_admin_preset_is_unaffected(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_principal(app_instance, monkeypatch, admin_principal(auth_mode=DashboardAuthMode.STANDARD))

    assert (await async_client.get("/api/audit-logs")).status_code == 200
    assert (await async_client.get("/api/dashboard/overview")).status_code == 200
    assert (await async_client.put("/api/settings", json={"stickyThreadsEnabled": True})).status_code == 200
    assert (await async_client.post("/api/accounts/missing/export/auth")).status_code == 404
