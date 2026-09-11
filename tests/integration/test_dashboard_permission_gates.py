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


# Captured at import time: once monkeypatched, the module attribute is a mock and
# can no longer serve as the FastAPI override key.
_ORIGINAL_VALIDATE_DASHBOARD_SESSION = auth_dependencies.validate_dashboard_session


def _use_principal(app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch, principal: DashboardPrincipal) -> None:
    # Router-level and permission dependencies resolve the module attribute at
    # call time; handler parameters captured ``Depends(validate_dashboard_session)``
    # at import time and need the FastAPI override as well.
    monkeypatch.setattr(auth_dependencies, "validate_dashboard_session", AsyncMock(return_value=principal))
    monkeypatch.setitem(app_instance.dependency_overrides, _ORIGINAL_VALIDATE_DASHBOARD_SESSION, lambda: principal)


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


# --- PR-0a-2: guest-restricted surfaces ---------------------------------------


async def _seed_account(email: str = "alice.smith@example.com") -> str:
    from datetime import UTC, datetime

    from app.db.models import Account, AccountStatus
    from app.db.session import SessionLocal

    account = Account(
        id="acc-1",
        chatgpt_account_id="chatgpt-account-123",
        email=email,
        alias=None,
        workspace_id="ws-123",
        workspace_label="Acme Workspace",
        plan_type="plus",
        status=AccountStatus.ACTIVE,
        access_token_encrypted=b"",
        refresh_token_encrypted=b"",
        id_token_encrypted=b"",
        last_refresh=datetime(2026, 9, 1, tzinfo=UTC),
    )
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()
    return account.id


@pytest.mark.asyncio
async def test_guest_reads_masked_account_identity(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_account()
    _use_principal(app_instance, monkeypatch, guest_principal())

    listing = await async_client.get("/api/accounts")
    assert listing.status_code == 200, listing.text
    [account] = listing.json()["accounts"]
    assert account["email"] == "a***@example.com"
    assert account["displayName"] == "a***@example.com"
    assert account["chatgptAccountId"] is None
    assert account["workspaceId"] is None
    assert account["workspaceLabel"] is None
    assert account["planType"] == "plus"
    assert "alice" not in listing.text
    assert "chatgpt-account-123" not in listing.text

    overview = await async_client.get("/api/dashboard/overview")
    assert overview.status_code == 200, overview.text
    assert "alice" not in overview.text
    assert "chatgpt-account-123" not in overview.text


@pytest.mark.asyncio
async def test_account_writer_reads_full_account_identity(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_account()
    _use_principal(app_instance, monkeypatch, admin_principal(auth_mode=DashboardAuthMode.STANDARD))

    listing = await async_client.get("/api/accounts")
    assert listing.status_code == 200
    [account] = listing.json()["accounts"]
    assert account["email"] == "alice.smith@example.com"
    assert account["chatgptAccountId"] == "chatgpt-account-123"
    assert account["workspaceLabel"] == "Acme Workspace"


@pytest.mark.asyncio
async def test_guest_request_log_search_does_not_match_account_email(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from app.db.models import RequestLog
    from app.db.session import SessionLocal

    account_id = await _seed_account()
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                account_id=account_id,
                request_id="req-email-oracle",
                model="gpt-5",
                status="success",
                requested_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            )
        )
        await session.commit()

    _use_principal(app_instance, monkeypatch, admin_principal(auth_mode=DashboardAuthMode.STANDARD))
    admin_hits = await async_client.get("/api/request-logs", params={"search": "alice.smith"})
    assert admin_hits.status_code == 200
    assert admin_hits.json()["total"] == 1

    _use_principal(app_instance, monkeypatch, guest_principal())
    guest_hits = await async_client.get("/api/request-logs", params={"search": "alice.smith"})
    assert guest_hits.status_code == 200
    assert guest_hits.json()["total"] == 0
    guest_by_request_id = await async_client.get("/api/request-logs", params={"search": "req-email-oracle"})
    assert guest_by_request_id.json()["total"] == 1


@pytest.mark.asyncio
async def test_guest_request_log_options_omit_api_key_inventory(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from app.db.models import RequestLog
    from app.db.session import SessionLocal

    _use_principal(app_instance, monkeypatch, admin_principal(auth_mode=DashboardAuthMode.STANDARD))
    created = await async_client.post("/api/api-keys/", json={"name": "inventory-key"})
    assert created.status_code in (200, 201), created.text
    key_id = created.json()["id"]
    # Filter options are derived from logged requests, so log one for the key.
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                api_key_id=key_id,
                request_id="req-inventory",
                model="gpt-5",
                status="success",
                requested_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()
    admin_options = await async_client.get("/api/request-logs/options")
    assert admin_options.status_code == 200
    assert [key["name"] for key in admin_options.json()["apiKeys"]] == ["inventory-key"]

    _use_principal(app_instance, monkeypatch, guest_principal())
    guest_options = await async_client.get("/api/request-logs/options")
    assert guest_options.status_code == 200
    guest_payload = guest_options.json()
    assert guest_payload["apiKeys"] == []
    assert "inventory-key" not in guest_options.text
    admin_payload = admin_options.json()
    for facet in ("accountIds", "modelOptions", "statuses"):
        assert guest_payload[facet] == admin_payload[facet]

    # Request-log rows are readable by guests but must not carry the key identity,
    # and search must not match key ids or names.
    guest_logs = await async_client.get("/api/request-logs")
    assert guest_logs.status_code == 200
    [entry] = guest_logs.json()["requests"]
    assert entry["apiKeyId"] is None
    assert entry["apiKeyName"] is None
    assert "inventory-key" not in guest_logs.text
    assert (await async_client.get("/api/request-logs", params={"search": "inventory-key"})).json()["total"] == 0
    assert (await async_client.get("/api/request-logs", params={"search": key_id})).json()["total"] == 0

    _use_principal(app_instance, monkeypatch, admin_principal(auth_mode=DashboardAuthMode.STANDARD))
    admin_logs = await async_client.get("/api/request-logs")
    [entry] = admin_logs.json()["requests"]
    assert entry["apiKeyId"] == key_id
    assert entry["apiKeyName"] == "inventory-key"
    assert (await async_client.get("/api/request-logs", params={"search": "inventory-key"})).json()["total"] == 1


@pytest.mark.asyncio
async def test_request_log_count_cache_is_keyed_by_identity_visibility(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warm admin count for an e-mail search must not be served to a guest."""

    from datetime import UTC, datetime

    from app.db.models import RequestLog
    from app.db.session import SessionLocal
    from app.modules.request_logs import repository as logs_repository_module

    account_id = await _seed_account()
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                account_id=account_id,
                request_id="req-cache-oracle",
                model="gpt-5",
                status="success",
                requested_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            )
        )
        await session.commit()
    monkeypatch.setattr(logs_repository_module, "_COUNT_CACHE_TTL_SECONDS", 30.0)
    logs_repository_module._clear_recent_count_cache()

    _use_principal(app_instance, monkeypatch, admin_principal(auth_mode=DashboardAuthMode.STANDARD))
    assert (await async_client.get("/api/request-logs", params={"search": "alice.smith"})).json()["total"] == 1

    _use_principal(app_instance, monkeypatch, guest_principal())
    assert (await async_client.get("/api/request-logs", params={"search": "alice.smith"})).json()["total"] == 0


@pytest.mark.asyncio
async def test_guest_cannot_read_inventory_and_topology_surfaces(
    app_instance: FastAPI, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_principal(app_instance, monkeypatch, guest_principal())

    for path, permission in (
        ("/api/api-keys/", Permission.API_KEYS_READ),
        ("/api/api-keys/some-key/trends", Permission.API_KEYS_READ),
        ("/api/api-keys/some-key/usage-7d", Permission.API_KEYS_READ),
        ("/api/settings/upstream-proxy", Permission.OPS_WRITE),
        ("/api/settings/runtime/connect-address", Permission.OPS_WRITE),
        ("/api/sticky-sessions", Permission.OPS_WRITE),
        ("/api/oauth/status", Permission.ACCOUNTS_WRITE),
    ):
        _assert_permission_required(await async_client.get(path), permission)

    # Guest-safe aggregate reads keep working.
    for path in ("/api/usage/summary", "/api/reports", "/api/request-logs", "/api/models"):
        response = await async_client.get(path)
        assert response.status_code == 200, (path, response.text)
