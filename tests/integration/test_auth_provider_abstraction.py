"""Product-path coverage for ``auth-provider-abstraction``.

Trusted-header requests now resolve to accounts: unknown identities are
provisioned with the provider's default role (admin, D10), pre-created
SSO-only accounts are linked on their first request, refused identities get
``identity_not_provisioned`` and the pending-identity session, and the
provider settings API edits the resolver's knobs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pyotp
import pytest
from alembic import command
from anyio import to_thread
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.modules.dashboard_users.identity_resolver as identity_resolver
from app.core.audit.service import drain_audit_log_tasks
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.config.settings import get_settings
from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade
from app.db.models import (
    AuditLog,
    AuthProviderKind,
    DashboardAuthProvider,
    DashboardIdentity,
    DashboardUser,
    DashboardUserInvite,
)
from app.db.session import SessionLocal
from app.modules.auth_providers.seed import auth_provider_id

pytestmark = pytest.mark.integration

ACCEPT_PASSWORD = "password123"

USERS = "/api/dashboard-users"
PROVIDERS = "/api/auth-providers"
SESSION = "/api/dashboard-auth/session"
ADMIN_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
OPERATOR_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]
VIEWER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]
MEMBER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.MEMBER]
TRUSTED_HEADER_PROVIDER_ID = auth_provider_id(AuthProviderKind.TRUSTED_HEADER)

_HEAD_REVISION = inspect_migration_state(get_settings().database_url).head_revision
_PARENT_REVISION = "20260910_000000_add_totp_required_for_admin_role"
_TARGET_REVISION = "20260910_010000_add_dashboard_auth_providers"


# --- helpers ---


def _trusted_header_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", DashboardAuthMode.TRUSTED_HEADER.value)
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", "Remote-User")
    get_settings.cache_clear()


def _as(subject: str) -> dict[str, str]:
    return {"Remote-User": subject}


def _error(response) -> str:
    return response.json()["error"]["code"]


async def _stepped_up(client: AsyncClient, headers: dict[str, str]) -> None:
    """Sensitive mutations need a recent step-up (H5); a header account without a password enrols TOTP for it."""

    start = await client.post("/api/dashboard-auth/totp/setup/start", json={}, headers=headers)
    assert start.status_code == 200, start.text
    code = pyotp.TOTP(start.json()["secret"]).now()
    confirm = await client.post(
        "/api/dashboard-auth/totp/setup/confirm", json={"secret": start.json()["secret"], "code": code}, headers=headers
    )
    assert confirm.status_code == 200, confirm.text
    stepped = await client.post("/api/dashboard-auth/step-up", json={"code": code}, headers=headers)
    assert stepped.status_code == 200, stepped.text


async def _rows(action: str) -> list[AuditLog]:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _user_by_username(username: str) -> DashboardUser | None:
    async with SessionLocal() as session:
        return (
            await session.execute(select(DashboardUser).where(DashboardUser.username == username))
        ).scalar_one_or_none()


async def _users_named(username: str) -> list[DashboardUser]:
    async with SessionLocal() as session:
        rows = await session.execute(select(DashboardUser).where(DashboardUser.username == username))
        return list(rows.scalars().all())


async def _identity(subject: str) -> DashboardIdentity | None:
    async with SessionLocal() as session:
        return (
            await session.execute(
                select(DashboardIdentity)
                .where(DashboardIdentity.provider == "trusted_header")
                .where(DashboardIdentity.subject == subject)
            )
        ).scalar_one_or_none()


async def _set_provider(**values: Any) -> None:
    async with SessionLocal() as session:
        provider = await session.get(DashboardAuthProvider, TRUSTED_HEADER_PROVIDER_ID)
        assert provider is not None
        for key, value in values.items():
            setattr(provider, key, value)
        await session.commit()
    from app.core.auth.providers.registry import get_auth_provider_registry

    get_auth_provider_registry().clear()
    identity_resolver.get_identity_resolution_cache().clear()


async def _seed_legacy_account(username: str, subject: str, role_id: str) -> str:
    """An account as an earlier release would have left it: manual role, one identity."""

    async with SessionLocal() as session:
        user = DashboardUser(id=str(uuid.uuid4()), username=username, role_id=role_id, role_source="manual")
        session.add(user)
        await session.flush()
        session.add(
            DashboardIdentity(user_id=user.id, provider="trusted_header", provider_key="default", subject=subject)
        )
        await session.commit()
        return user.id


# --- JIT provisioning (D10) ---


@pytest.mark.asyncio
async def test_unknown_identity_becomes_an_admin_account_by_default(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)

    session = await async_client.get(SESSION, headers=_as("Alice@Example.com"))
    assert session.status_code == 200, session.text
    body = session.json()
    assert body["authenticated"] is True
    assert body["authMode"] == "trusted_header"
    assert body["authMethod"] == "trusted_header"
    assert body["user"]["username"] == "alice.example.com"
    assert body["user"]["role"]["slug"] == "admin"
    assert "users:manage:all" in body["permissions"]
    assert body["accessSummary"]["usersTotal"] == 1
    assert body["accessSummary"]["providersEnabled"] == ["password", "trusted_header"]
    assert [p["kind"] for p in body["login"]["providers"]] == ["password", "trusted_header"]
    assert body["login"]["pendingIdentity"] is False

    allowed = await async_client.get("/api/settings", headers=_as("alice@example.com"))
    assert allowed.status_code == 200

    user = await _user_by_username("alice.example.com")
    assert user is not None
    assert user.role_id == ADMIN_ROLE
    assert user.role_source == "mapping"
    assert user.status == "active"
    assert user.password_hash is None
    assert user.display_name == "Alice@Example.com"
    assert user.email == "alice@example.com"
    identity = await _identity("alice@example.com")
    assert identity is not None and identity.user_id == user.id and identity.last_seen_at is not None
    # Naive UTC in the naive columns (PostgreSQL refuses aware values there; SQLite would store the offset).
    async with SessionLocal() as session:
        raw = (
            await session.execute(
                text(
                    "SELECT u.last_login_at, i.last_seen_at FROM dashboard_users u"
                    " JOIN dashboard_identities i ON i.user_id = u.id WHERE u.username = 'alice.example.com'"
                )
            )
        ).one()
    assert all("+" not in str(value) for value in raw), raw

    created = await _rows("user_created")
    assert len(created) == 1
    assert created[0].actor_user_id is None and created[0].auth_method == "trusted_header"
    assert '"jit": true' in (created[0].details or "")
    linked = await _rows("identity_linked")
    assert len(linked) == 1 and linked[0].actor_user_id == user.id and linked[0].target_id == user.id


@pytest.mark.asyncio
async def test_proxy_user_named_admin_never_inherits_the_migrated_row(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)

    session = await async_client.get(SESSION, headers=_as("admin"))
    assert session.json()["user"]["username"] == "admin-2"
    assert await _user_by_username("admin") is None

    # The break-glass local admin stays creatable next to proxy-created accounts...
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup", json={"password": "password123"}, headers=_as("admin")
    )
    assert setup.status_code == 200, setup.text
    assert setup.json()["user"]["username"] == "admin-2"
    compat = await _user_by_username("admin")
    assert compat is not None and compat.password_hash is not None and compat.is_break_glass is True
    # ...and a second proxy spelling collides onto the next suffix.
    again = await async_client.get(SESSION, headers=_as("Admin "))
    assert again.json()["user"]["username"] == "admin-2"


@pytest.mark.asyncio
async def test_refused_identity_gets_pending_session_and_401(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    await _set_provider(unknown_identity_role_id=None)

    session = await async_client.get(SESSION, headers=_as("nobody@example.com"))
    assert session.status_code == 200
    body = session.json()
    assert body["authenticated"] is False
    assert body["authMode"] == "trusted_header"
    assert body["user"] is None
    assert body["accessSummary"] is None
    assert body["login"]["pendingIdentity"] is True

    blocked = await async_client.get("/api/settings", headers=_as("nobody@example.com"))
    assert blocked.status_code == 401
    assert _error(blocked) == "identity_not_provisioned"
    assert await _user_by_username("nobody.example.com") is None

    failed = await _rows("login_failed")
    assert len(failed) == 1
    details = failed[0].details or ""
    assert '"reason": "unknown_identity"' in details
    assert '"subject": "nobody@example.com"' in details
    assert '"email": "nobody@example.com"' in details
    assert '"groups": []' in details
    assert failed[0].severity == "warning"


@pytest.mark.asyncio
async def test_disabled_account_is_refused(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    assert (await async_client.get(SESSION, headers=_as("alice"))).json()["authenticated"] is True
    async with SessionLocal() as session:
        user = (await session.execute(select(DashboardUser).where(DashboardUser.username == "alice"))).scalar_one()
        user.status = "disabled"
        await session.commit()
    from app.core.auth.dashboard_users_cache import get_dashboard_users_cache

    await get_dashboard_users_cache().invalidate()

    blocked = await async_client.get("/api/settings", headers=_as("alice"))
    assert blocked.status_code == 401
    assert _error(blocked) == "account_disabled"
    session_response = await async_client.get(SESSION, headers=_as("alice"))
    assert session_response.json()["authenticated"] is False
    assert session_response.json()["login"]["pendingIdentity"] is True


# --- existing accounts are not re-evaluated (D10) ---


@pytest.mark.asyncio
async def test_upgraded_install_keeps_existing_roles(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    admin_id = await _seed_legacy_account("ops-admin", "ops@example.com", ADMIN_ROLE)
    viewer_id = await _seed_legacy_account("watcher", "watcher@example.com", VIEWER_ROLE)

    admin_session = await async_client.get(SESSION, headers=_as("ops@example.com"))
    assert admin_session.json()["user"] == {
        "id": admin_id,
        "username": "ops-admin",
        "displayName": None,
        "role": {"id": ADMIN_ROLE, "slug": "admin", "name": "Admin", "kind": "preset"},
    }
    viewer_session = await async_client.get(SESSION, headers=_as("watcher@example.com"))
    assert viewer_session.json()["user"]["id"] == viewer_id
    assert viewer_session.json()["user"]["role"]["slug"] == "viewer"
    assert "write" not in viewer_session.json()["permissions"]

    async with SessionLocal() as session:
        users = {u.username: u for u in (await session.execute(select(DashboardUser))).scalars().all()}
    assert users["ops-admin"].role_id == ADMIN_ROLE and users["ops-admin"].role_source == "manual"
    assert users["watcher"].role_id == VIEWER_ROLE and users["watcher"].role_source == "manual"
    assert set(users) == {"ops-admin", "watcher"}
    assert await _rows("user_created") == []


@pytest.mark.asyncio
async def test_resolution_is_throttled_per_identity(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    from app.core.cache.invalidation import get_cache_invalidation_poller

    # Provisioning an account invalidates the dashboard_users namespace, and the poller
    # clears the resolution cache when it sees that bump — so let both accounts exist and
    # acknowledge this replica's own bumps first. Otherwise the counter below measures a
    # mid-test invalidation rather than the throttle. Only _provision() invalidates; the
    # "already seen" path this test exercises does not.
    for subject in ("alice", "bob"):
        assert (await async_client.get(SESSION, headers=_as(subject))).status_code == 200
    poller = get_cache_invalidation_poller()
    assert poller is not None
    await poller._poll_once()
    identity_resolver.get_identity_resolution_cache().clear()

    calls: list[str] = []
    original = identity_resolver.IdentityResolver.resolve

    async def counting(self, identity, provider, *, actor_ip):
        calls.append(identity.subject)
        return await original(self, identity, provider, actor_ip=actor_ip)

    monkeypatch.setattr(identity_resolver.IdentityResolver, "resolve", counting)
    # What is under test is "one resolution per identity while the entry is live", not the
    # length of the window: pin the TTL so a slow request path cannot age the entry out
    # mid-test and turn the throttle into a second resolution.
    monkeypatch.setattr(identity_resolver.get_identity_resolution_cache(), "_ttl_seconds", 300.0)

    for _ in range(3):
        assert (await async_client.get("/api/settings", headers=_as("alice"))).status_code == 200
    assert (await async_client.get(SESSION, headers=_as("alice"))).status_code == 200
    assert calls == ["alice"]
    assert (await async_client.get(SESSION, headers=_as("bob"))).status_code == 200
    assert calls == ["alice", "bob"]


# --- pre-created accounts and e-mail linking ---


@pytest.mark.asyncio
async def test_sso_only_account_is_linked_on_first_request(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    admin = _as("alice@example.com")
    assert (await async_client.get(SESSION, headers=admin)).json()["user"]["role"]["slug"] == "admin"
    await _stepped_up(async_client, admin)

    created = await async_client.post(
        USERS,
        json={
            "username": "bob",
            "roleId": VIEWER_ROLE,
            "ssoOnly": True,
            "expectedIdentity": {"provider": "trusted_header", "providerKey": "default", "subject": "Bob@Example.com"},
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    assert created.json()["invite"] is None
    assert created.json()["user"]["status"] == "invited"
    invites = await async_client.get(f"{USERS}/invites", headers=admin)
    assert [(i["username"], i["ssoOnly"]) for i in invites.json()] == [("bob", True)]

    duplicate = await async_client.post(
        USERS,
        json={
            "username": "bob2",
            "roleId": VIEWER_ROLE,
            "ssoOnly": True,
            "expectedIdentity": {"provider": "trusted_header", "subject": "bob@example.com"},
        },
        headers=admin,
    )
    assert duplicate.status_code == 409 and _error(duplicate) == "identity_taken"  # one open invite per identity
    without_identity = await async_client.post(
        USERS, json={"username": "x", "roleId": VIEWER_ROLE, "ssoOnly": True}, headers=admin
    )
    assert without_identity.status_code == 422

    first = await async_client.get(SESSION, headers=_as("bob@example.com"))
    assert first.json()["authenticated"] is True
    assert first.json()["user"]["username"] == "bob"
    assert first.json()["user"]["role"]["slug"] == "viewer"
    bob = await _user_by_username("bob")
    assert bob is not None and bob.status == "active" and bob.role_source == "manual" and bob.password_hash is None
    async with SessionLocal() as session:
        invite = (
            await session.execute(select(DashboardUserInvite).where(DashboardUserInvite.user_id == bob.id))
        ).scalar_one()
    assert invite.consumed_at is not None and invite.sso_only is True
    assert (await _identity("bob@example.com")) is not None
    linked = [row for row in await _rows("identity_linked") if row.target_id == bob.id]
    assert len(linked) == 1 and '"via": "invite"' in (linked[0].details or "")
    # The second pre-created row expecting the same identity is now refused at creation time.
    taken = await async_client.post(
        USERS,
        json={
            "username": "bob3",
            "roleId": VIEWER_ROLE,
            "ssoOnly": True,
            "expectedIdentity": {"provider": "trusted_header", "subject": "bob@example.com"},
        },
        headers=admin,
    )
    assert taken.status_code == 409 and _error(taken) == "identity_taken"


@pytest.mark.asyncio
async def test_sso_only_needs_an_active_non_password_provider(async_client: AsyncClient) -> None:
    setup = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    assert setup.status_code == 200
    refused = await async_client.post(
        USERS,
        json={
            "username": "bob",
            "roleId": VIEWER_ROLE,
            "ssoOnly": True,
            "expectedIdentity": {"provider": "trusted_header", "subject": "bob"},
        },
    )
    assert refused.status_code == 409
    assert _error(refused) == "sso_not_available"
    session = await async_client.get(SESSION)
    assert [p["kind"] for p in session.json()["login"]["providers"]] == ["password"]
    assert session.json()["accessSummary"]["providersEnabled"] == ["password"]


@pytest.mark.asyncio
async def test_link_by_email_is_off_by_default_and_links_when_on(
    async_client: AsyncClient, app_instance, monkeypatch
) -> None:
    _trusted_header_mode(monkeypatch)
    admin = _as("root@example.com")
    assert (await async_client.get(SESSION, headers=admin)).status_code == 200
    await _stepped_up(async_client, admin)
    created = await async_client.post(
        USERS, json={"username": "carol", "email": "Carol@Example.com", "roleId": OPERATOR_ROLE}, headers=admin
    )
    assert created.status_code == 201, created.text
    token = created.json()["invite"]["token"]
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as other:
        accepted = await other.post(
            "/api/dashboard-auth/invite/accept", json={"token": token, "password": ACCEPT_PASSWORD}
        )
        assert accepted.status_code == 200, accepted.text
    carol = await _user_by_username("carol")
    assert carol is not None

    # Off: a new account is provisioned; the e-mail stays on the identity row only.
    off = await async_client.get(SESSION, headers=_as("carol@example.com"))
    assert off.json()["user"]["username"] == "carol.example.com"
    jit = await _user_by_username("carol.example.com")
    assert jit is not None and jit.email is None and jit.id != carol.id
    identity = await _identity("carol@example.com")
    assert identity is not None and identity.email == "carol@example.com"

    # On: the existing account with that e-mail is linked instead.
    await _set_provider(link_by_email=True)
    created = await async_client.post(
        USERS, json={"username": "dave", "email": "dave@example.com", "roleId": VIEWER_ROLE}, headers=admin
    )
    token = created.json()["invite"]["token"]
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as other:
        assert (
            await other.post("/api/dashboard-auth/invite/accept", json={"token": token, "password": ACCEPT_PASSWORD})
        ).status_code == 200
    dave = await _user_by_username("dave")
    assert dave is not None
    on = await async_client.get(SESSION, headers=_as("Dave@Example.com"))
    assert on.json()["user"]["id"] == dave.id
    identity = await _identity("dave@example.com")
    assert identity is not None and identity.user_id == dave.id
    linked = [row for row in await _rows("identity_linked") if row.target_id == dave.id]
    assert len(linked) == 1 and '"via": "email"' in (linked[0].details or "")


# --- provider settings API ---


@pytest.mark.asyncio
async def test_provider_api_lists_and_edits_the_resolver_knobs(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    admin = _as("alice")
    assert (await async_client.get(SESSION, headers=admin)).status_code == 200
    await _stepped_up(async_client, admin)
    alice = await _user_by_username("alice")
    assert alice is not None

    listed = await async_client.get(PROVIDERS, headers=admin)
    assert listed.status_code == 200, listed.text
    by_kind = {row["kind"]: row for row in listed.json()}
    assert set(by_kind) == {"password", "trusted_header"}
    assert by_kind["password"]["active"] is True and by_kind["password"]["unknownIdentityRoleId"] is None
    trusted = by_kind["trusted_header"]
    assert trusted["id"] == TRUSTED_HEADER_PROVIDER_ID
    assert trusted["active"] is True and trusted["enabled"] is True
    assert trusted["unknownIdentityRoleId"] == ADMIN_ROLE
    assert trusted["noMatchRoleId"] == VIEWER_ROLE
    assert trusted["linkByEmail"] is False and trusted["idpMfaEnforced"] is False
    # The header names are topology, not database: the settings card shows the
    # values it cannot edit next to the variables that set them.
    assert trusted["config"] == {"identityHeader": "Remote-User", "groupsHeader": "Remote-Groups"}
    assert by_kind["password"]["config"] == {}

    not_assignable = await async_client.patch(
        f"{PROVIDERS}/{TRUSTED_HEADER_PROVIDER_ID}", json={"unknownIdentityRoleId": MEMBER_ROLE}, headers=admin
    )
    assert not_assignable.status_code == 422 and _error(not_assignable) == "role_not_assignable"
    unknown = await async_client.patch(f"{PROVIDERS}/{uuid.uuid4()}", json={"linkByEmail": True}, headers=admin)
    assert unknown.status_code == 404 and _error(unknown) == "provider_not_found"
    extra = await async_client.patch(
        f"{PROVIDERS}/{TRUSTED_HEADER_PROVIDER_ID}", json={"configEncrypted": "x"}, headers=admin
    )
    assert extra.status_code == 422

    patched = await async_client.patch(
        f"{PROVIDERS}/{TRUSTED_HEADER_PROVIDER_ID}",
        json={"unknownIdentityRoleId": VIEWER_ROLE, "idpMfaEnforced": True, "label": "Authelia"},
        headers=admin,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["unknownIdentityRoleId"] == VIEWER_ROLE
    assert patched.json()["idpMfaEnforced"] is True and patched.json()["label"] == "Authelia"
    audit = await _rows("provider_updated")
    assert len(audit) == 1 and audit[0].actor_user_id == alice.id and audit[0].target_id == TRUSTED_HEADER_PROVIDER_ID
    assert '"unknown_identity_role_id"' in (audit[0].details or "")

    # The next unknown identity gets the new default; alice keeps admin (JIT-only).
    newcomer = await async_client.get(SESSION, headers=_as("newcomer"))
    assert newcomer.json()["user"]["role"]["slug"] == "viewer"
    assert (await async_client.get(SESSION, headers=admin)).json()["user"]["role"]["slug"] == "admin"
    labels = sorted(p["label"] for p in newcomer.json()["login"]["providers"])
    assert labels == ["Authelia", "Password"]

    # A viewer cannot read providers; a caller may only hand out roles within its own grants.
    forbidden = await async_client.get(PROVIDERS, headers=_as("newcomer"))
    assert forbidden.status_code == 403 and _error(forbidden) == "permission_required"


@pytest.mark.asyncio
async def test_provider_edits_need_an_attributable_account(async_client: AsyncClient) -> None:
    listed = await async_client.get(PROVIDERS)
    assert listed.status_code == 200
    assert {row["kind"]: row["active"] for row in listed.json()} == {"password": True, "trusted_header": False}
    refused = await async_client.patch(f"{PROVIDERS}/{TRUSTED_HEADER_PROVIDER_ID}", json={"linkByEmail": True})
    assert refused.status_code == 409 and _error(refused) == "admin_account_required"


@pytest.mark.asyncio
async def test_provider_role_handout_is_bounded_by_the_callers_grants(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    manager_id = str(uuid.uuid4())
    async with SessionLocal() as session:
        from app.db.models import DashboardRoleGrant, DashboardRoleRecord

        session.add(DashboardRoleRecord(id=manager_id, slug="sec", name="Sec", kind="custom"))
        session.add_all(
            DashboardRoleGrant(role_id=manager_id, permission=permission, scope="all")
            for permission in ("dashboard:read", "accounts:read", "ops:write", "security:write")
        )
        await session.commit()
    await _seed_legacy_account("sec-officer", "sec@example.com", manager_id)
    await _stepped_up(async_client, _as("sec@example.com"))

    too_wide = await async_client.patch(
        f"{PROVIDERS}/{TRUSTED_HEADER_PROVIDER_ID}",
        json={"unknownIdentityRoleId": ADMIN_ROLE},
        headers=_as("sec@example.com"),
    )
    assert too_wide.status_code == 403 and _error(too_wide) == "insufficient_delegation"
    within = await async_client.patch(
        f"{PROVIDERS}/{TRUSTED_HEADER_PROVIDER_ID}",
        json={"unknownIdentityRoleId": VIEWER_ROLE},
        headers=_as("sec@example.com"),
    )
    assert within.status_code == 200


# --- schema ---


@pytest.mark.asyncio
async def test_providers_migration_upgrades_and_downgrades(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'providers.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, _PARENT_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        await to_thread.run_sync(lambda: run_upgrade(db_url, _TARGET_REVISION, bootstrap_legacy=False))
        config = _build_alembic_config(db_url)
        # Re-running the upgrade body is a no-op (table guard, insert-ignore seed, column guard).
        await to_thread.run_sync(lambda: command.stamp(config, _PARENT_REVISION))
        await to_thread.run_sync(lambda: run_upgrade(db_url, _TARGET_REVISION, bootstrap_legacy=False))
        async with engine.connect() as conn:
            columns = {row[1] for row in await conn.execute(text("PRAGMA table_info('dashboard_auth_providers')"))}
            invite_columns = {row[1] for row in await conn.execute(text("PRAGMA table_info('dashboard_user_invites')"))}
            seeded = [
                tuple(row)
                for row in await conn.execute(
                    text(
                        "SELECT kind, provider_key, enabled, unknown_identity_role_id, no_match_role_id"
                        " FROM dashboard_auth_providers ORDER BY kind"
                    )
                )
            ]
        assert columns == {
            "id",
            "kind",
            "provider_key",
            "enabled",
            "label",
            "config_encrypted",
            "unknown_identity_role_id",
            "no_match_role_id",
            "link_by_email",
            "skip_role_sync",
            "idp_mfa_enforced",
            "created_at",
            "updated_at",
        }
        assert {"expected_provider", "expected_provider_key", "expected_subject"} <= invite_columns
        assert seeded == [
            ("password", "default", 1, None, None),
            ("trusted_header", "default", 1, ADMIN_ROLE, VIEWER_ROLE),
        ]
        await to_thread.run_sync(lambda: command.downgrade(config, _PARENT_REVISION))
        async with engine.connect() as conn:
            tables = {row[0] for row in await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            invite_columns = {row[1] for row in await conn.execute(text("PRAGMA table_info('dashboard_user_invites')"))}
        assert "dashboard_auth_providers" not in tables
        assert not {"expected_provider", "expected_provider_key", "expected_subject"} & invite_columns
        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
    finally:
        await engine.dispose()


# --- review round: break-glass, reserved admin, SSO-only enforcement, fallback ---


@pytest.mark.asyncio
async def test_break_glass_setup_needs_a_managing_account_behind_the_proxy(
    async_client: AsyncClient, monkeypatch
) -> None:
    _trusted_header_mode(monkeypatch)
    assert (await async_client.get(SESSION, headers=_as("alice"))).json()["user"]["role"]["slug"] == "admin"
    await _set_provider(unknown_identity_role_id=VIEWER_ROLE)
    assert (await async_client.get(SESSION, headers=_as("watcher"))).json()["user"]["role"]["slug"] == "viewer"
    await _set_provider(unknown_identity_role_id=None)

    bare = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    assert bare.status_code == 401 and _error(bare) == "proxy_auth_required"
    refused = await async_client.post(
        "/api/dashboard-auth/password/setup", json={"password": "password123"}, headers=_as("stranger")
    )
    assert refused.status_code == 401 and _error(refused) == "identity_not_provisioned"
    viewer = await async_client.post(
        "/api/dashboard-auth/password/setup", json={"password": "password123"}, headers=_as("watcher")
    )
    assert viewer.status_code == 403 and _error(viewer) == "permission_required"
    assert await _user_by_username("admin") is None

    allowed = await async_client.post(
        "/api/dashboard-auth/password/setup", json={"password": "password123"}, headers=_as("alice")
    )
    assert allowed.status_code == 200, allowed.text
    compat = await _user_by_username("admin")
    assert compat is not None and compat.is_break_glass is True


@pytest.mark.asyncio
async def test_admin_is_reserved_for_the_break_glass_account(
    async_client: AsyncClient, app_instance, monkeypatch
) -> None:
    _trusted_header_mode(monkeypatch)
    admin = _as("alice")
    assert (await async_client.get(SESSION, headers=admin)).status_code == 200
    await _stepped_up(async_client, admin)

    reserved = await async_client.post(USERS, json={"username": "Admin", "roleId": VIEWER_ROLE}, headers=admin)
    assert reserved.status_code == 422 and "reserved" in reserved.json()["error"]["message"]

    created = await async_client.post(USERS, json={"username": "bob", "roleId": VIEWER_ROLE}, headers=admin)
    token = created.json()["invite"]["token"]
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as other:
        renamed = await other.post(
            "/api/dashboard-auth/invite/accept",
            json={"token": token, "username": "admin", "password": ACCEPT_PASSWORD},
        )
    assert renamed.status_code == 422 and "reserved" in renamed.json()["error"]["message"]

    # A row that merely carries the name but is not the break-glass account is never re-armed.
    async with SessionLocal() as session:
        session.add(DashboardUser(id=str(uuid.uuid4()), username="admin", role_id=ADMIN_ROLE, is_break_glass=False))
        await session.commit()
    from app.core.auth.dashboard_users_cache import get_dashboard_users_cache

    await get_dashboard_users_cache().invalidate()
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup", json={"password": "password123"}, headers=admin
    )
    assert setup.status_code == 409 and _error(setup) == "password_already_configured"
    stray = await _user_by_username("admin")
    assert stray is not None and stray.password_hash is None


@pytest.mark.asyncio
async def test_setup_re_arms_the_bootstrap_account_after_it_was_disabled(
    async_client: AsyncClient, app_instance, monkeypatch
) -> None:
    """Disabling the emergency account must not be a one-way door.

    The row keeps its password hash while it is disabled, so a re-arm keyed on
    ``password_hash IS NULL`` never matches it again: the local form refuses it
    (``disabled_user``) and setup answers ``409 password_already_configured``
    for as long as the row exists. On a proxy install that is the whole
    break-glass path gone, with the proxy the only way back in. The condition
    is "not an *active* password holder" -- the same fact the gate above the
    compare-and-set tests -- and the write restores the state setup promises.

    The account is **renamed before it is disabled**, because the two recovery
    rules meet here and only this order tells them apart: an implementation
    that still found the row by ``admin`` would pass every assertion below with
    the original name, and would then either miss a renamed row entirely or
    insert a second one beside it and collide on the frozen id. Setup must
    re-arm *this row*, under its current name, and that name must be the one
    that signs in afterwards.
    """

    _trusted_header_mode(monkeypatch)
    admin = _as("alice")
    assert (await async_client.get(SESSION, headers=admin)).status_code == 200
    await _stepped_up(async_client, admin)

    created = await async_client.post(
        "/api/dashboard-auth/password/setup", json={"password": "password123"}, headers=admin
    )
    assert created.status_code == 200, created.text
    compat = await _user_by_username("admin")
    assert compat is not None and compat.password_hash is not None
    first_hash = compat.password_hash

    renamed = await async_client.patch(f"{USERS}/{compat.id}", json={"username": "rescue"}, headers=admin)
    assert renamed.status_code == 200, renamed.text

    disabled = await async_client.patch(f"{USERS}/{compat.id}", json={"status": "disabled"}, headers=admin)
    assert disabled.status_code == 200, disabled.text

    again = await async_client.post(
        "/api/dashboard-auth/password/setup", json={"password": "password456"}, headers=admin
    )
    assert again.status_code == 200, again.text
    rearmed = await _user_by_username("rescue")
    assert rearmed is not None and rearmed.id == compat.id
    assert rearmed.status == "active" and rearmed.role_id == ADMIN_ROLE and rearmed.is_break_glass is True
    assert rearmed.password_hash is not None and rearmed.password_hash != first_hash
    # One account, re-armed in place -- not a second row alongside a dead one,
    # and setup did not quietly recreate the name it was bootstrapped under.
    assert len(await _users_named("rescue")) == 1
    assert await _users_named("admin") == []

    # And the door it exists for is open: the new password signs in through the
    # local form under the name the account carries now, and the one it
    # replaced does not.
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as local:
        stale = await local.post(
            "/api/dashboard-auth/password/login", json={"username": "rescue", "password": "password123"}
        )
        assert stale.status_code == 401 and _error(stale) == "invalid_credentials"
        gone = await local.post(
            "/api/dashboard-auth/password/login", json={"username": "admin", "password": "password456"}
        )
        assert gone.status_code == 401 and _error(gone) == "invalid_credentials"
        opened = await local.post(
            "/api/dashboard-auth/password/login", json={"username": "rescue", "password": "password456"}
        )
        assert opened.status_code == 200, opened.text
        assert opened.json()["user"]["username"] == "rescue"


@pytest.mark.asyncio
async def test_sso_only_invites_have_no_link_anywhere(async_client: AsyncClient, app_instance, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    admin = _as("alice")
    assert (await async_client.get(SESSION, headers=admin)).status_code == 200
    await _stepped_up(async_client, admin)
    created = await async_client.post(
        USERS,
        json={
            "username": "bob",
            "roleId": VIEWER_ROLE,
            "ssoOnly": True,
            "expectedIdentity": {"provider": "trusted_header", "subject": "bob"},
        },
        headers=admin,
    )
    assert created.status_code == 201 and created.json()["invite"] is None
    assert created.json()["user"]["pendingInvite"] == {"expiresAt": None, "ssoOnly": True}
    user_id = created.json()["user"]["id"]

    resend = await async_client.post(f"{USERS}/{user_id}/invite", headers=admin)
    assert resend.status_code == 409 and _error(resend) == "sso_only_invite"

    # Even a token that matches the stored hash opens nothing: SSO-only accounts have no link.
    from app.modules.dashboard_users.service import invite_token_hash

    async with SessionLocal() as session:
        invite = (
            await session.execute(select(DashboardUserInvite).where(DashboardUserInvite.user_id == user_id))
        ).scalar_one()
        invite.token_hash = invite_token_hash("leaked-token")
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as other:
        described = await other.get("/api/dashboard-auth/invite/leaked-token")
        accepted = await other.post(
            "/api/dashboard-auth/invite/accept", json={"token": "leaked-token", "password": ACCEPT_PASSWORD}
        )
    assert described.status_code == 404 and accepted.status_code == 404
    listed = await async_client.get(USERS, headers=admin)
    bob = next(row for row in listed.json() if row["username"] == "bob")
    assert bob["status"] == "invited" and bob["pendingInvite"]["ssoOnly"] is True


@pytest.mark.asyncio
async def test_sso_only_accounts_wait_without_expiring(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    admin = _as("alice")
    assert (await async_client.get(SESSION, headers=admin)).status_code == 200
    await _stepped_up(async_client, admin)
    created = await async_client.post(
        USERS,
        json={
            "username": "bob",
            "roleId": VIEWER_ROLE,
            "ssoOnly": True,
            "expectedIdentity": {"provider": "trusted_header", "subject": "bob"},
        },
        headers=admin,
    )
    assert created.status_code == 201
    async with SessionLocal() as session:
        invite = (
            await session.execute(
                select(DashboardUserInvite).where(DashboardUserInvite.user_id == created.json()["user"]["id"])
            )
        ).scalar_one()
        invite.expires_at = datetime.now(UTC) - timedelta(hours=25)
        await session.commit()

    invites = await async_client.get(f"{USERS}/invites", headers=admin)
    assert [(i["username"], i["expiresAt"], i["ssoOnly"]) for i in invites.json()] == [("bob", None, True)]
    listed = await async_client.get(USERS, headers=admin)
    assert any(
        row["username"] == "bob" and row["pendingInvite"] == {"expiresAt": None, "ssoOnly": True}
        for row in listed.json()
    )
    assert (await async_client.get(SESSION, headers=admin)).json()["accessSummary"]["pendingInvites"] == 1

    first = await async_client.get(SESSION, headers=_as("bob"))
    assert first.json()["user"]["username"] == "bob" and first.json()["user"]["role"]["slug"] == "viewer"
    audited = await _rows("user_invited")
    assert len(audited) == 1 and "expires_at" not in (audited[0].details or "")


@pytest.mark.asyncio
async def test_break_glass_cookie_stays_valid_while_the_proxy_asserts_a_refused_identity(
    async_client: AsyncClient, monkeypatch
) -> None:
    _trusted_header_mode(monkeypatch)
    assert (await async_client.get(SESSION, headers=_as("alice"))).status_code == 200
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup", json={"password": "password123"}, headers=_as("alice")
    )
    assert setup.status_code == 200
    async_client.cookies.clear()
    login = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert login.status_code == 200 and login.json()["user"]["username"] == "admin"
    await _set_provider(unknown_identity_role_id=None)

    # Refused header + admin cookie: the cookie account is served.
    with_cookie = await async_client.get("/api/settings", headers=_as("stranger"))
    assert with_cookie.status_code == 200
    session = await async_client.get(SESSION, headers=_as("stranger"))
    assert session.json()["authenticated"] is True
    assert session.json()["user"]["username"] == "admin"
    assert session.json()["login"]["pendingIdentity"] is False
    assert session.json()["passwordSessionActive"] is True
    assert session.json()["localPasswordConfigured"] is True

    # Header user + cookie: the header account wins.
    header_user = await async_client.get(SESSION, headers=_as("alice"))
    assert header_user.json()["user"]["username"] == "alice"
    assert header_user.json()["passwordSessionActive"] is True

    # Refused header, no cookie: 401 and the pending session.
    async_client.cookies.clear()
    blocked = await async_client.get("/api/settings", headers=_as("stranger"))
    assert blocked.status_code == 401 and _error(blocked) == "identity_not_provisioned"
    pending = await async_client.get(SESSION, headers=_as("stranger"))
    assert pending.json()["authenticated"] is False and pending.json()["login"]["pendingIdentity"] is True
    assert pending.json()["localPasswordConfigured"] is True


@pytest.mark.asyncio
async def test_local_password_configured_ignores_proxy_accounts(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    session = await async_client.get(SESSION, headers=_as("alice"))
    assert session.json()["passwordRequired"] is True
    assert session.json()["localPasswordConfigured"] is False
    headerless = await async_client.get(SESSION)
    assert headerless.json()["authenticated"] is False and headerless.json()["localPasswordConfigured"] is False


@pytest.mark.asyncio
async def test_inactive_provider_row_is_refused_consistently(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    assert (await async_client.get(SESSION, headers=_as("alice"))).status_code == 200
    await _set_provider(enabled=False)

    blocked = await async_client.get("/api/settings", headers=_as("alice"))
    assert blocked.status_code == 401 and _error(blocked) == "proxy_auth_required"
    session = await async_client.get(SESSION, headers=_as("alice"))
    body = session.json()
    assert body["authenticated"] is False and body["user"] is None and body["accessSummary"] is None
    assert body["authMode"] == "trusted_header" and body["login"]["pendingIdentity"] is False


@pytest.mark.asyncio
async def test_over_long_or_verbose_subjects_are_bounded(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    too_long = await async_client.get("/api/settings", headers=_as("x" * 513))
    assert too_long.status_code == 401 and _error(too_long) == "proxy_auth_required"

    verbose = "v" * 200
    session = await async_client.get(SESSION, headers=_as(verbose))
    assert session.json()["user"]["displayName"] == "v" * 128
    identity = await _identity(verbose)
    assert identity is not None and identity.display_name == "v" * 128


@pytest.mark.asyncio
async def test_username_collision_retries_without_touching_the_expired_role(
    async_client: AsyncClient, monkeypatch
) -> None:
    _trusted_header_mode(monkeypatch)
    await _seed_legacy_account("bob", "someone-else", VIEWER_ROLE)
    original = identity_resolver.IdentityResolver._free_username
    handed_out: list[str] = []

    async def collide_once(self, subject):
        # First candidate collides with the existing ``bob`` row; the retry must survive the rollback.
        if not handed_out:
            handed_out.append("bob")
            return "bob"
        name = await original(self, subject)
        handed_out.append(name)
        return name

    monkeypatch.setattr(identity_resolver.IdentityResolver, "_free_username", collide_once)
    session = await async_client.get(SESSION, headers=_as("bob"))
    assert session.status_code == 200, session.text
    assert session.json()["user"]["username"] == "bob-2"
    assert session.json()["user"]["role"]["slug"] == "admin"
    assert handed_out == ["bob", "bob-2"]


@pytest.mark.asyncio
async def test_remote_provider_edit_clears_this_replicas_registry(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    from app.core.auth.providers.registry import get_auth_provider_registry
    from app.core.cache.invalidation import get_cache_invalidation_poller
    from app.db.models import CacheInvalidation

    registry = get_auth_provider_registry()
    assert (await async_client.get(SESSION, headers=_as("alice"))).status_code == 200
    assert registry._rows is not None
    poller = get_cache_invalidation_poller()
    assert poller is not None
    await poller._poll_once()  # flush and acknowledge this replica's own bumps
    await registry.rows()
    await async_client.get(SESSION, headers=_as("alice"))
    assert registry._rows is not None
    assert identity_resolver.get_identity_resolution_cache()._entries != {}

    # A peer replica's PATCH: the row version moves without this replica being told directly.
    async with SessionLocal() as session:
        row = await session.get(CacheInvalidation, "dashboard_users")
        if row is None:
            session.add(CacheInvalidation(namespace="dashboard_users", version=1))
        else:
            row.version += 1
        await session.commit()
    await poller._poll_once()
    assert registry._rows is None
    assert identity_resolver.get_identity_resolution_cache()._entries == {}
