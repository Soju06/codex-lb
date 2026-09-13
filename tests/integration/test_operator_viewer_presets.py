"""Product-path coverage for ``enable-operator-viewer-presets``.

An Operator and a Viewer are created the way a team does it (admin invites,
the person accepts and signs in) and then hit the routes the permission matrix
separates. The second half covers ``totp_required_for_admin_role`` (D9): who it
binds, the enable guard, the enrolment counts and the migration.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pyotp
import pytest
from alembic import command
from anyio import to_thread
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, Permission, PresetRoleSlug
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade
from app.db.models import (
    Account,
    AccountStatus,
    DashboardRoleGrant,
    DashboardRoleRecord,
    DashboardSettings,
)
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

USERS = "/api/dashboard-users"
ADMIN_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
OPERATOR_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]
VIEWER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]
ADMIN_PASSWORD = "password123"
PASSWORD = "invited-password-1"

_HEAD_REVISION = inspect_migration_state(get_settings().database_url).head_revision
_PARENT_REVISION = "20260909_040000_add_dashboard_user_invites"
_TARGET_REVISION = "20260910_000000_add_totp_required_for_admin_role"


# --- helpers ---


@asynccontextmanager
async def _client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


async def _setup_admin(client: AsyncClient) -> str:
    response = await client.post("/api/dashboard-auth/password/setup", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["user"]["id"]


async def _invite_and_accept(admin: AsyncClient, person: AsyncClient, username: str, role_id: str) -> str:
    created = await admin.post(USERS, json={"username": username, "roleId": role_id})
    assert created.status_code == 201, created.text
    accepted = await person.post(
        "/api/dashboard-auth/invite/accept", json={"token": created.json()["invite"]["token"], "password": PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text
    return created.json()["user"]["id"]


async def _login(client: AsyncClient, username: str, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post(
        "/api/dashboard-auth/password/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _enrol_totp(client: AsyncClient) -> str:
    started = await client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = await client.post(
        "/api/dashboard-auth/totp/setup/confirm", json={"secret": secret, "code": pyotp.TOTP(secret).now()}
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret


async def _present_totp(client: AsyncClient, secret: str) -> None:
    """The migrated ``admin`` row is the designated emergency account: once it holds a
    secret every request needs it, whatever the two toggles say, so the session that
    enrolled it presents the code once."""

    verified = await client.post("/api/dashboard-auth/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    assert verified.status_code == 200, verified.text


async def _custom_role(slug: str, *permissions: Permission) -> str:
    role_id = str(uuid.uuid4())
    async with SessionLocal() as session:
        session.add(DashboardRoleRecord(id=role_id, slug=slug, name=slug.title(), kind="custom"))
        session.add_all(
            DashboardRoleGrant(role_id=role_id, permission=permission.value, scope="all") for permission in permissions
        )
        await session.commit()
    return role_id


async def _set_admin_role_policy(enabled: bool) -> None:
    """Flip the admin-role requirement directly (the API guard needs the actor's own secret)."""

    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardSettings))).scalar_one()
        row.totp_required_for_admin_role = enabled
        await session.commit()
    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


async def _seed_account() -> None:
    async with SessionLocal() as session:
        session.add(
            Account(
                id="acc-1",
                chatgpt_account_id="chatgpt-account-123",
                email="alice.smith@example.com",
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
        )
        await session.commit()


def _error(response) -> tuple[str, str | None]:
    error = response.json()["error"]
    return error["code"], error.get("param")


def _assert_permission_required(response, permission: Permission) -> None:
    assert response.status_code == 403, response.text
    assert _error(response) == ("permission_required", permission.value)


# --- A. operator / viewer separation through the real product path ---


@pytest.mark.asyncio
async def test_operator_runs_operations_but_not_security_or_sensitive_reads(async_client: AsyncClient, app_instance):
    await _setup_admin(async_client)
    await _seed_account()
    async with _client(app_instance) as operator:
        await _invite_and_accept(async_client, operator, "ops", OPERATOR_ROLE)

        session = (await operator.get("/api/dashboard-auth/session")).json()
        assert session["user"]["role"]["slug"] == "operator"
        assert "write" in session["permissions"]
        assert session["accessSummary"] is None

        # Operations: accounts, API keys, model sources, sticky sessions, non-security settings.
        assert (await operator.get("/api/accounts")).json()["accounts"][0]["email"] == "alice.smith@example.com"
        assert (await operator.put("/api/accounts/acc-1/alias", json={"alias": "ops-alias"})).status_code == 200
        created_key = await operator.post("/api/api-keys", json={"name": "ops-key"})
        assert created_key.status_code == 200, created_key.text
        assert (await operator.get("/api/api-keys")).status_code == 200
        assert (await operator.get("/api/sticky-sessions")).status_code == 200
        assert (await operator.get("/api/settings/upstream-proxy")).status_code == 200
        assert (await operator.get("/api/oauth/status")).status_code == 200
        saved = await operator.put("/api/settings", json={"stickyThreadsEnabled": False})
        assert saved.status_code == 200, saved.text
        assert saved.json()["stickyThreadsEnabled"] is False

        # Sensitive reads and the security boundary stay admin-only.
        _assert_permission_required(await operator.get("/api/conversations"), Permission.CONVERSATIONS_READ)
        _assert_permission_required(
            await operator.get("/api/conversation-archive/records", params={"request_id": "x"}),
            Permission.CONVERSATIONS_READ,
        )
        _assert_permission_required(
            await operator.get("/api/request-logs", params={"conversation_id": "conv-1"}),
            Permission.CONVERSATIONS_READ,
        )
        _assert_permission_required(await operator.get("/api/audit-logs"), Permission.AUDIT_READ)
        _assert_permission_required(await operator.post("/api/accounts/acc-1/export/auth"), Permission.ACCOUNTS_EXPORT)
        for body in ({"guestAccessEnabled": True}, {"totpRequiredForAdminRole": True}, {"apiKeyAuthEnabled": True}):
            _assert_permission_required(await operator.put("/api/settings", json=body), Permission.SECURITY_WRITE)
        _assert_permission_required(
            await operator.post("/api/dashboard-auth/guest/password", json={"password": "guest-secret-1"}),
            Permission.SECURITY_WRITE,
        )
        _assert_permission_required(
            await operator.post("/api/firewall/ips", json={"ipAddress": "10.0.0.1"}), Permission.SECURITY_WRITE
        )
        _assert_permission_required(await operator.get("/api/dashboard-users"), Permission.USERS_MANAGE)
        _assert_permission_required(await operator.post(USERS, json={"username": "x"}), Permission.USERS_MANAGE)

        # Security fields re-sent unchanged (the client posts the whole form) are not a change.
        current = (await operator.get("/api/settings")).json()
        assert (
            await operator.put("/api/settings", json={"guestAccessEnabled": current["guestAccessEnabled"]})
        ).status_code == 200


@pytest.mark.asyncio
async def test_viewer_reads_the_guest_surface_and_mutates_nothing(async_client: AsyncClient, app_instance):
    await _setup_admin(async_client)
    await _seed_account()
    async with _client(app_instance) as viewer:
        await _invite_and_accept(async_client, viewer, "viewer", VIEWER_ROLE)

        session = (await viewer.get("/api/dashboard-auth/session")).json()
        assert session["user"]["role"]["slug"] == "viewer"
        assert "write" not in session["permissions"]

        # The guest read surface, with the same identity masking guests get (no accounts:write).
        listing = await viewer.get("/api/accounts")
        assert listing.status_code == 200, listing.text
        [account] = listing.json()["accounts"]
        assert account["email"] == "a***@example.com"
        assert account["chatgptAccountId"] is None
        for path in (
            "/api/dashboard/overview",
            "/api/usage/summary",
            "/api/request-logs",
            "/api/settings",
            "/api/models",
        ):
            assert (await viewer.get(path)).status_code == 200, path

        # Inventory and topology reads are closed, like for guests.
        _assert_permission_required(await viewer.get("/api/api-keys"), Permission.API_KEYS_READ)
        _assert_permission_required(await viewer.get("/api/sticky-sessions"), Permission.OPS_WRITE)
        _assert_permission_required(await viewer.get("/api/settings/upstream-proxy"), Permission.OPS_WRITE)
        _assert_permission_required(await viewer.get("/api/conversations"), Permission.CONVERSATIONS_READ)
        _assert_permission_required(await viewer.get("/api/audit-logs"), Permission.AUDIT_READ)
        _assert_permission_required(await viewer.get("/api/dashboard-users"), Permission.USERS_MANAGE)

        # Every mutation fails on a write-class gate (the legacy alias or a write permission).
        for method, path, body, code in (
            ("PUT", "/api/settings", {"stickyThreadsEnabled": False}, "read_only_access"),
            ("PUT", "/api/accounts/acc-1/alias", {"alias": "nope"}, "permission_required"),
            ("POST", "/api/api-keys", {"name": "nope"}, "permission_required"),
            ("POST", "/api/accounts/acc-1/pause", None, "permission_required"),
        ):
            response = await viewer.request(method, path, json=body)
            assert response.status_code == 403, (method, path, response.text)
            assert _error(response)[0] == code, (method, path)
        _assert_permission_required(await viewer.post("/api/accounts/acc-1/export/auth"), Permission.ACCOUNTS_EXPORT)
        _assert_permission_required(
            await viewer.post("/api/firewall/ips", json={"ipAddress": "10.0.0.1"}), Permission.SECURITY_WRITE
        )

    # The admin still sees the unmasked identity the viewer did not.
    assert (await async_client.get("/api/accounts")).json()["accounts"][0]["email"] == "alice.smith@example.com"


# --- B. totp_required_for_admin_role ---


@pytest.mark.asyncio
async def test_admin_role_policy_binds_admin_level_accounts_only(async_client: AsyncClient, app_instance):
    """Policy on: the operator signs in as before; the admin without a secret and a custom
    role holding one privileged permission are parked at the enrolment gate."""

    await _setup_admin(async_client)
    auditor_role = await _custom_role(
        "auditor", Permission.DASHBOARD_READ, Permission.ACCOUNTS_READ, Permission.AUDIT_READ
    )
    async with _client(app_instance) as operator, _client(app_instance) as auditor:
        await _invite_and_accept(async_client, operator, "ops", OPERATOR_ROLE)
        await _invite_and_accept(async_client, auditor, "auditor", auditor_role)
        await _set_admin_role_policy(True)

        # Operator: not admin-level, nothing changes.
        assert (await operator.get("/api/settings")).status_code == 200
        session = (await operator.get("/api/dashboard-auth/session")).json()
        assert session["totpEnrollmentRequired"] is False
        assert session["totpRequiredOnLogin"] is False
        login = await _login(operator, "ops")
        assert login["totpEnrollmentRequired"] is False

        # Admin preset without a secret: held at the gate, self-service routes stay open.
        blocked = await async_client.get("/api/settings")
        assert blocked.status_code == 403
        assert _error(blocked)[0] == "totp_enrollment_required"
        session = (await async_client.get("/api/dashboard-auth/session")).json()
        assert session["authenticated"] is True
        assert session["totpEnrollmentRequired"] is True
        assert session["accessSummary"] is None
        assert (await async_client.get("/api/dashboard-auth/me")).status_code == 200
        closed = await async_client.post("/api/dashboard-auth/guest/password", json={"password": "guest-secret-1"})
        assert closed.status_code == 403 and _error(closed)[0] == "totp_enrollment_required"

        # Custom role with audit:read is admin-level too.
        held = await auditor.get("/api/dashboard/overview")
        assert held.status_code == 403 and _error(held)[0] == "totp_enrollment_required"
        assert (await auditor.get("/api/dashboard-auth/session")).json()["totpEnrollmentRequired"] is True

        # Enrolling releases the gate; the next session must then present the code.
        await _enrol_totp(async_client)
        pending = await async_client.get("/api/settings")
        assert pending.status_code == 401 and _error(pending)[0] == "totp_required"
        assert (await async_client.get("/api/dashboard-auth/session")).json()["totpRequiredOnLogin"] is True
    await _set_admin_role_policy(False)


@pytest.mark.asyncio
async def test_enabling_a_totp_requirement_needs_the_actors_own_secret(async_client: AsyncClient, app_instance):
    admin_id = await _setup_admin(async_client)
    auditor_role = await _custom_role(
        "auditor", Permission.DASHBOARD_READ, Permission.ACCOUNTS_READ, Permission.AUDIT_READ
    )
    async with (
        _client(app_instance) as admin2,
        _client(app_instance) as operator,
        _client(app_instance) as viewer,
        _client(app_instance) as auditor,
    ):
        await _invite_and_accept(async_client, admin2, "admin2", ADMIN_ROLE)
        await _invite_and_accept(async_client, operator, "ops", OPERATOR_ROLE)
        await _invite_and_accept(async_client, viewer, "viewer", VIEWER_ROLE)
        await _invite_and_accept(async_client, auditor, "auditor", auditor_role)

        before = (await async_client.get("/api/settings")).json()
        assert before["totpRequiredForAdminRole"] is False
        assert "totpConfigured" not in before  # per-account: the session response carries it
        assert before["usersWithoutTotpCount"] == 5
        assert before["adminsWithoutTotpCount"] == 3  # admin, admin2, auditor

        for body in ({"totpRequiredForAdminRole": True}, {"totpRequiredOnLogin": True}):
            refused = await async_client.put("/api/settings", json=body)
            assert refused.status_code == 400, refused.text
            assert _error(refused)[0] == "invalid_totp_config"
        assert (await async_client.get("/api/settings")).json()["totpRequiredForAdminRole"] is False

        secret = await _enrol_totp(async_client)
        await _present_totp(async_client, secret)
        assert (await async_client.get("/api/dashboard-auth/session")).json()["totpConfigured"] is True
        assert (await admin2.get("/api/dashboard-auth/session")).json()["totpConfigured"] is False

        enabled = await async_client.put("/api/settings", json={"totpRequiredForAdminRole": True})
        assert enabled.status_code == 200, enabled.text
        payload = enabled.json()
        assert payload["totpRequiredForAdminRole"] is True
        assert payload["usersWithoutTotpCount"] == 4
        assert payload["adminsWithoutTotpCount"] == 2  # admin2, auditor

        # Non-security saves by others still work while the requirement is on
        # (the guard fires on enabling, not on every save that carries the flag).
        assert (await operator.put("/api/settings", json={"stickyThreadsEnabled": False})).status_code == 200

        # The other admin is held until enrolment; the operator and viewer are not.
        held = await admin2.get("/api/settings")
        assert held.status_code == 403 and _error(held)[0] == "totp_enrollment_required"
        assert (await operator.get("/api/dashboard-auth/session")).json()["totpEnrollmentRequired"] is False
        assert (await viewer.get("/api/dashboard-auth/session")).json()["totpEnrollmentRequired"] is False

        # The acting admin already presented its code when it enrolled, and keeps working.
        me = await async_client.get("/api/dashboard-auth/me")
        assert me.status_code == 200 and me.json()["id"] == admin_id
        assert (await async_client.get("/api/settings")).status_code == 200
    await _set_admin_role_policy(False)


@pytest.mark.asyncio
async def test_an_unenrolled_migrated_admin_no_longer_blocks_the_global_requirement(
    async_client: AsyncClient, app_instance
):
    """Release N refused this to protect a previous-release replica that read the legacy row.

    Nothing reads that row now, so the only condition left is the one that was
    always about the acting account: whoever turns the requirement on must hold
    a secret. The unenrolled migrated account is held at the enrolment gate like
    any other, and no settings response reports a compatibility state for it.
    """

    admin_id = await _setup_admin(async_client)
    async with _client(app_instance) as admin2:
        await _invite_and_accept(async_client, admin2, "admin2", ADMIN_ROLE)
        admin2_secret = await _enrol_totp(admin2)
        verified = await admin2.post("/api/dashboard-auth/totp/verify", json={"code": pyotp.TOTP(admin2_secret).now()})
        assert verified.status_code == 200, verified.text

        # The migrated admin has a password and no secret; the requirement goes on anyway.
        enabled = await admin2.put("/api/settings", json={"totpRequiredOnLogin": True})
        assert enabled.status_code == 200, enabled.text
        body = enabled.json()
        assert body["totpRequiredOnLogin"] is True
        assert "compatAdminUnenrolled" not in body
        assert (await admin2.put("/api/settings", json={"totpRequiredForAdminRole": True})).status_code == 200

        # ...and that account meets the gate it was just given, like anyone else.
        assert (await async_client.get("/api/dashboard-auth/me")).status_code == 200
        await async_client.post("/api/dashboard-auth/logout", json={})
        login = await async_client.post(
            "/api/dashboard-auth/password/login", json={"username": "admin", "password": "password123"}
        )
        assert login.status_code == 200 and login.json()["totpEnrollmentRequired"] is True
        held = await async_client.get("/api/settings")
        assert held.status_code == 403 and _error(held)[0] == "totp_enrollment_required"
        await _present_totp(async_client, await _enrol_totp(async_client))
        assert (await async_client.get("/api/dashboard-auth/me")).json()["id"] == admin_id
    await _set_admin_role_policy(False)
    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardSettings))).scalar_one()
        row.totp_required_on_login = False
        await session.commit()
    await get_settings_cache().invalidate()


@pytest.mark.asyncio
async def test_password_removal_resets_the_admin_role_requirement(async_client: AsyncClient):
    """A solo install that removes its password and sets a new one must not park the new admin at enrolment."""

    await _setup_admin(async_client)
    secret = await _enrol_totp(async_client)
    await _present_totp(async_client, secret)
    assert (await async_client.put("/api/settings", json={"totpRequiredForAdminRole": True})).status_code == 200

    removed = await async_client.request("DELETE", "/api/dashboard-auth/password", json={"password": ADMIN_PASSWORD})
    assert removed.status_code == 200, removed.text

    await _setup_admin(async_client)
    settings = await async_client.get("/api/settings")
    assert settings.status_code == 200, settings.text
    assert settings.json()["totpRequiredForAdminRole"] is False
    assert settings.json()["totpRequiredOnLogin"] is False


@pytest.mark.asyncio
async def test_admin_role_policy_change_is_audited(async_client: AsyncClient):
    from app.core.audit.service import drain_audit_log_tasks
    from app.db.models import AuditLog

    await _setup_admin(async_client)
    await _present_totp(async_client, await _enrol_totp(async_client))
    response = await async_client.put("/api/settings", json={"totpRequiredForAdminRole": True})
    assert response.status_code == 200, response.text
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        row = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "settings_changed").order_by(AuditLog.id.desc())
                )
            )
            .scalars()
            .first()
        )
    assert row is not None
    assert "totp_required_for_admin_role" in (row.details or "")
    await _set_admin_role_policy(False)


@pytest.mark.asyncio
async def test_admin_role_policy_migration_upgrades_and_downgrades(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'admin-totp.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, _PARENT_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:

        async def _columns() -> set[str]:
            async with engine.connect() as conn:
                return {row[1] for row in await conn.execute(text("PRAGMA table_info('dashboard_settings')"))}

        assert "totp_required_for_admin_role" not in await _columns()
        await to_thread.run_sync(lambda: run_upgrade(db_url, _TARGET_REVISION, bootstrap_legacy=False))
        assert "totp_required_for_admin_role" in await _columns()
        config = _build_alembic_config(db_url)
        # Re-running the upgrade body over the existing column is a no-op.
        await to_thread.run_sync(lambda: command.stamp(config, _PARENT_REVISION))
        await to_thread.run_sync(lambda: run_upgrade(db_url, _TARGET_REVISION, bootstrap_legacy=False))
        column_sql = (
            "SELECT dflt_value, [notnull] FROM pragma_table_info('dashboard_settings') "
            "WHERE name = 'totp_required_for_admin_role'"
        )
        async with engine.connect() as conn:
            default = (await conn.execute(text(column_sql))).one()
        assert default[1] == 1 and str(default[0]).lower() in {"0", "false", "'0'"}
        await to_thread.run_sync(lambda: command.downgrade(config, _PARENT_REVISION))
        assert "totp_required_for_admin_role" not in await _columns()
        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        # A later Phase-2 revision may stack on top; the walk must still reach head.
        assert result.current_revision == _HEAD_REVISION
    finally:
        await engine.dispose()
