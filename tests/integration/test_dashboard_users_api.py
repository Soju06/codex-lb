"""Product-path coverage for ``manage-dashboard-users-and-invites``.

People are added through the management API, sign in by accepting an invite
link with a second client, and every invariant (self modification, last admin,
delegation, compat lock, key cascade) is exercised through the routes.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pyotp
import pytest
from alembic import command
from anyio import to_thread
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.modules.dashboard_users.repository as users_repository
import app.modules.dashboard_users.service as users_service
from app.core.audit.service import drain_audit_log_tasks
from app.core.auth.api_key_cache import get_api_key_cache
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, Permission, PresetRoleSlug
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade
from app.db.models import (
    ApiKey,
    AuditLog,
    DashboardIdentity,
    DashboardRoleGrant,
    DashboardRoleRecord,
    DashboardSettings,
    DashboardUser,
    DashboardUserInvite,
    RateLimitAttempt,
)
from app.db.session import SessionLocal
from app.modules.dashboard_users.repository import DashboardUsersRepository

pytestmark = pytest.mark.integration

USERS = "/api/dashboard-users"
ADMIN_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
OPERATOR_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]
VIEWER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]
PASSWORD = "invited-password-1"

_HEAD_REVISION = inspect_migration_state(get_settings().database_url).head_revision
_PARENT_REVISION = "20260909_030000_add_audit_actor_columns"
_TARGET_REVISION = "20260909_040000_add_dashboard_user_invites"


# --- helpers ---


async def _setup_admin(client: AsyncClient) -> str:
    response = await client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    assert response.status_code == 200, response.text
    return response.json()["user"]["id"]


async def _create(client: AsyncClient, username: str, *, role_id: str = VIEWER_ROLE, **extra: Any) -> dict[str, Any]:
    response = await client.post(USERS, json={"username": username, "roleId": role_id, **extra})
    assert response.status_code == 201, response.text
    return response.json()


@asynccontextmanager
async def _client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


async def _accept(client: AsyncClient, token: str, **fields: Any) -> dict[str, Any]:
    response = await client.post(
        "/api/dashboard-auth/invite/accept", json={"token": token, "password": PASSWORD, **fields}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _error(response) -> str:
    return response.json()["error"]["code"]


async def _rows(action: str) -> list[AuditLog]:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _user(user_id: str) -> DashboardUser | None:
    async with SessionLocal() as session:
        return await session.get(DashboardUser, user_id)


async def _custom_role(slug: str, grants: dict[Permission, str]) -> str:
    role_id = str(uuid.uuid4())
    async with SessionLocal() as session:
        session.add(DashboardRoleRecord(id=role_id, slug=slug, name=slug.title(), kind="custom"))
        session.add_all(
            DashboardRoleGrant(role_id=role_id, permission=permission.value, scope=scope)
            for permission, scope in grants.items()
        )
        await session.commit()
    return role_id


MANAGER_GRANTS = {Permission.DASHBOARD_READ: "all", Permission.ACCOUNTS_READ: "all", Permission.USERS_MANAGE: "all"}
ROOT_GRANTS = {permission: "all" for permission in Permission}


async def _clear_rate_limits() -> None:
    async with SessionLocal() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()


async def _owned_key(client: AsyncClient, owner_id: str, *, active: bool = True, reason: str | None = None) -> ApiKey:
    created = await client.post("/api/api-keys", json={"name": f"key-{uuid.uuid4().hex[:6]}"})
    assert created.status_code == 200, created.text
    async with SessionLocal() as session:
        key = await session.get(ApiKey, created.json()["id"])
        assert key is not None
        key.owner_user_id = owner_id
        key.is_active = active
        key.deactivated_reason = reason
        await session.commit()
        await session.refresh(key)
    return key


async def _key(key_id: str) -> ApiKey:
    async with SessionLocal() as session:
        key = await session.get(ApiKey, key_id)
        assert key is not None
        return key


# --- gates and listing ---


@pytest.mark.asyncio
async def test_implicit_local_admin_can_list_but_not_mutate(async_client: AsyncClient) -> None:
    listing = await async_client.get(USERS)
    assert listing.status_code == 200 and listing.json() == []
    created = await async_client.post(USERS, json={"username": "bob", "roleId": VIEWER_ROLE})
    assert created.status_code == 409 and _error(created) == "admin_account_required"
    roles = await async_client.get("/api/dashboard-roles")
    assert roles.status_code == 200


@pytest.mark.asyncio
async def test_list_shape_exposes_no_secrets_and_marks_pending_invites(async_client: AsyncClient) -> None:
    admin_id = await _setup_admin(async_client)
    created = await _create(async_client, "Bob", display_name="Bob B.", email="Bob@Example.com")
    assert created["user"]["username"] == "bob"
    assert created["user"]["email"] == "bob@example.com"
    assert created["invite"]["token"] and created["invite"]["expiresAt"]

    listing = await async_client.get(USERS)
    users = {row["username"]: row for row in listing.json()}
    assert set(users) == {"admin", "bob"}
    for row in users.values():
        assert not {"passwordHash", "totpSecretEncrypted", "totpSecret", "token", "tokenHash"} & set(row)
    assert users["admin"]["id"] == admin_id
    assert users["admin"]["status"] == "active" and users["admin"]["hasPassword"] is True
    assert users["admin"]["isBreakGlass"] is True and users["admin"]["pendingInvite"] is None
    assert users["admin"]["role"] == {"id": ADMIN_ROLE, "slug": "admin", "name": "Admin", "kind": "preset"}
    assert users["bob"]["status"] == "invited" and users["bob"]["hasPassword"] is False
    assert users["bob"]["roleSource"] == "manual"
    assert users["bob"]["pendingInvite"] == {"expiresAt": created["invite"]["expiresAt"], "ssoOnly": False}

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.json()["accessSummary"]["pendingInvites"] == 1
    assert session.json()["accessSummary"]["usersInvited"] == 1


@pytest.mark.asyncio
async def test_create_validation_and_uniqueness(async_client: AsyncClient) -> None:
    await _setup_admin(async_client)
    await _create(async_client, "bob", email="bob@example.com")

    duplicate = await async_client.post(USERS, json={"username": "BOB", "roleId": VIEWER_ROLE})
    assert duplicate.status_code == 409 and _error(duplicate) == "username_taken"
    same_email = await async_client.post(
        USERS, json={"username": "bobby", "roleId": VIEWER_ROLE, "email": "BOB@example.com"}
    )
    assert same_email.status_code == 409 and _error(same_email) == "email_taken"
    bad_name = await async_client.post(USERS, json={"username": "bad name!", "roleId": VIEWER_ROLE})
    assert bad_name.status_code == 422 and _error(bad_name) == "validation_error"
    bad_email = await async_client.post(USERS, json={"username": "carl", "roleId": VIEWER_ROLE, "email": "nope"})
    assert bad_email.status_code == 422 and _error(bad_email) == "validation_error"
    for slug in (PresetRoleSlug.GUEST, PresetRoleSlug.MEMBER):
        refused = await async_client.post(USERS, json={"username": "carl", "roleId": PRESET_ROLE_IDS[slug]})
        assert refused.status_code == 422 and _error(refused) == "role_not_assignable"
    unknown = await async_client.post(USERS, json={"username": "carl", "roleId": str(uuid.uuid4())})
    assert unknown.status_code == 422 and _error(unknown) == "role_not_assignable"
    sso = await async_client.post(USERS, json={"username": "carl", "roleId": VIEWER_ROLE, "ssoOnly": True})
    assert sso.status_code == 422 and _error(sso) == "validation_error"


@pytest.mark.asyncio
async def test_delegation_limits_which_roles_a_manager_may_grant(async_client: AsyncClient, app_instance) -> None:
    """A caller holding ``users:manage`` without the admin grants can hand out viewer, not admin or operator."""

    await _setup_admin(async_client)
    manager_role = await _custom_role("team-lead", MANAGER_GRANTS)
    invite = (await _create(async_client, "lead", role_id=manager_role))["invite"]["token"]
    async with _client(app_instance) as lead:
        await _accept(lead, invite)
        refused = await lead.post(USERS, json={"username": "boss", "roleId": ADMIN_ROLE})
        assert refused.status_code == 403 and _error(refused) == "insufficient_delegation"
        refused = await lead.post(USERS, json={"username": "ops", "roleId": OPERATOR_ROLE})
        assert refused.status_code == 403 and _error(refused) == "insufficient_delegation"
        allowed = await lead.post(USERS, json={"username": "viewer1", "roleId": VIEWER_ROLE})
        assert allowed.status_code == 201, allowed.text

        # Acting on an admin (revoke sessions, reset TOTP, delete) is refused the same way.
        admin_id = next(u["id"] for u in (await lead.get(USERS)).json() if u["username"] == "admin")
        for path in ("revoke-sessions", "reset-totp"):
            refused = await lead.post(f"{USERS}/{admin_id}/{path}")
            assert refused.status_code == 403 and _error(refused) == "insufficient_delegation"
        refused = await lead.patch(f"{USERS}/{admin_id}", json={"displayName": "x"})
        assert refused.status_code == 403 and _error(refused) == "insufficient_delegation"

    operator = await _create(async_client, "ops", role_id=OPERATOR_ROLE)
    async with _client(app_instance) as ops:
        await _accept(ops, operator["invite"]["token"])
        no_permission = await ops.get(USERS)
        assert no_permission.status_code == 403 and _error(no_permission) == "permission_required"


# --- invite lifecycle ---


@pytest.mark.asyncio
async def test_invite_lookup_and_acceptance(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    assert (await async_client.patch("/api/dashboard-auth/me", json={"displayName": "Root Admin"})).status_code == 200
    created = await _create(async_client, "bob", role_id=OPERATOR_ROLE)
    token = created["invite"]["token"]

    async with _client(app_instance) as guest:
        garbage = await guest.get("/api/dashboard-auth/invite/not-a-token")
        assert garbage.status_code == 404 and _error(garbage) == "invite_not_found"
        looked_up = await guest.get(f"/api/dashboard-auth/invite/{token}")
        assert looked_up.status_code == 200, looked_up.text
        assert looked_up.json() == {
            "roleName": "Operator",
            "inviterDisplayName": "Root Admin",
            "suggestedUsername": "bob",
            "usernameLocked": False,
            "expiresAt": created["invite"]["expiresAt"],
        }

        short = await guest.post("/api/dashboard-auth/invite/accept", json={"token": token, "password": "short"})
        assert short.status_code == 422 and _error(short) == "validation_error"
        accepted = await _accept(guest, token, username="Robert", displayName="Bob")
        assert accepted["authenticated"] is True and accepted["user"]["username"] == "robert"
        assert accepted["user"]["role"]["slug"] == "operator"
        me = await guest.get("/api/dashboard-auth/me")
        assert me.status_code == 200 and me.json()["displayName"] == "Bob"
        assert (await guest.get("/api/accounts")).status_code == 200

        second = await guest.post("/api/dashboard-auth/invite/accept", json={"token": token, "password": PASSWORD})
        assert second.status_code == 409 and _error(second) == "already_signed_in"
    async with _client(app_instance) as other:
        replay = await other.post("/api/dashboard-auth/invite/accept", json={"token": token, "password": PASSWORD})
        assert replay.status_code == 404 and _error(replay) == "invite_not_found"
        assert (await other.get(f"/api/dashboard-auth/invite/{token}")).status_code == 404

    (row,) = await _rows("invite_accepted")
    assert row.actor_username == "robert" and row.target_id == created["user"]["id"]
    assert (await async_client.get("/api/dashboard-auth/session")).json()["accessSummary"]["pendingInvites"] == 0
    users = {u["username"]: u for u in (await async_client.get(USERS)).json()}
    assert users["robert"]["status"] == "active" and users["robert"]["hasPassword"] is True
    assert users["robert"]["lastLoginAt"] is not None


@pytest.mark.asyncio
async def test_locked_username_and_username_collisions_on_accept(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    locked = await _create(async_client, "locked", username_locked=True)
    free = await _create(async_client, "free")
    async with _client(app_instance) as guest:
        assert (await guest.get(f"/api/dashboard-auth/invite/{locked['invite']['token']}")).json()["usernameLocked"]
        refused = await guest.post(
            "/api/dashboard-auth/invite/accept",
            json={"token": locked["invite"]["token"], "password": PASSWORD, "username": "other"},
        )
        assert refused.status_code == 422 and _error(refused) == "username_locked"
        reserved = await guest.post(
            "/api/dashboard-auth/invite/accept",
            json={"token": free["invite"]["token"], "password": PASSWORD, "username": "ADMIN"},
        )
        # `admin` belongs to the local break-glass account and is never handed out.
        assert reserved.status_code == 422 and _error(reserved) == "validation_error"
        taken = await guest.post(
            "/api/dashboard-auth/invite/accept",
            json={"token": free["invite"]["token"], "password": PASSWORD, "username": "LOCKED"},
        )
        assert taken.status_code == 409 and _error(taken) == "username_taken"
        # The refused attempts did not consume either invite.
        await _accept(guest, locked["invite"]["token"], username="LOCKED")


@pytest.mark.asyncio
async def test_invite_rate_limits(async_client: AsyncClient, app_instance) -> None:
    await _clear_rate_limits()
    await _setup_admin(async_client)
    token = (await _create(async_client, "bob"))["invite"]["token"]
    async with _client(app_instance) as guest:
        for _ in range(30):
            assert (await guest.get("/api/dashboard-auth/invite/x")).status_code == 404
        limited = await guest.get(f"/api/dashboard-auth/invite/{token}")
        assert limited.status_code == 429 and _error(limited) == "invite_rate_limited"
        assert "Retry-After" in limited.headers

        for _ in range(5):
            assert (
                await guest.post("/api/dashboard-auth/invite/accept", json={"token": "bad", "password": PASSWORD})
            ).status_code == 404
        per_token = await guest.post("/api/dashboard-auth/invite/accept", json={"token": "bad", "password": PASSWORD})
        assert per_token.status_code == 429 and _error(per_token) == "invite_rate_limited"
        for suffix in range(2):
            assert (
                await guest.post(
                    "/api/dashboard-auth/invite/accept", json={"token": f"b{suffix}", "password": PASSWORD}
                )
            ).status_code == 404
        per_ip = await guest.post("/api/dashboard-auth/invite/accept", json={"token": token, "password": PASSWORD})
        assert per_ip.status_code == 429
    await _clear_rate_limits()


@pytest.mark.asyncio
async def test_resend_rotates_and_revoke_deletes_the_invited_account(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    created = await _create(async_client, "bob")
    user_id, old_token = created["user"]["id"], created["invite"]["token"]

    resent = await async_client.post(f"{USERS}/{user_id}/invite")
    assert resent.status_code == 200, resent.text
    new_token = resent.json()["token"]
    assert new_token != old_token
    async with _client(app_instance) as guest:
        assert (await guest.get(f"/api/dashboard-auth/invite/{old_token}")).status_code == 404
        assert (await guest.get(f"/api/dashboard-auth/invite/{new_token}")).status_code == 200
    pending = (await async_client.get(f"{USERS}/invites")).json()
    assert [p["username"] for p in pending] == ["bob"]
    assert pending[0]["roleId"] == VIEWER_ROLE and pending[0]["createdByUserId"]

    revoked = await async_client.delete(f"{USERS}/{user_id}/invite")
    assert revoked.status_code == 204
    assert await _user(user_id) is None
    assert (await async_client.get(f"{USERS}/invites")).json() == []
    async with _client(app_instance) as guest:
        assert (await guest.get(f"/api/dashboard-auth/invite/{new_token}")).status_code == 404
    assert len(await _rows("invite_resent")) == 1 and len(await _rows("invite_revoked")) == 1

    # A resend for an account that already accepted is refused.
    active = await _create(async_client, "carl")
    async with _client(app_instance) as guest:
        await _accept(guest, active["invite"]["token"])
    not_pending = await async_client.post(f"{USERS}/{active['user']['id']}/invite")
    assert not_pending.status_code == 409 and _error(not_pending) == "invite_not_pending"
    not_pending = await async_client.delete(f"{USERS}/{active['user']['id']}/invite")
    assert not_pending.status_code == 409 and _error(not_pending) == "invite_not_pending"


@pytest.mark.asyncio
async def test_expired_invites_are_refused_and_purged_lazily(
    async_client: AsyncClient, app_instance, monkeypatch
) -> None:
    await _setup_admin(async_client)
    created = await _create(async_client, "bob")
    user_id, token = created["user"]["id"], created["invite"]["token"]

    later = datetime.now(UTC) + timedelta(hours=25)
    monkeypatch.setattr(users_repository, "utc_now", lambda: later)
    async with _client(app_instance) as guest:
        expired = await guest.get(f"/api/dashboard-auth/invite/{token}")
        assert expired.status_code == 404 and _error(expired) == "invite_not_found"
        accept = await guest.post("/api/dashboard-auth/invite/accept", json={"token": token, "password": PASSWORD})
        assert accept.status_code == 404
    # The row still exists (no background job) but it is a zombie: not counted anywhere...
    summary = (await async_client.get("/api/dashboard-auth/session")).json()["accessSummary"]
    assert (summary["usersTotal"], summary["usersInvited"], summary["pendingInvites"]) == (1, 0, 0)
    assert await _user(user_id) is not None
    # ...and every account route treats it as gone (the first call purges it).
    for method, path, body in (
        ("PATCH", f"{USERS}/{user_id}", {"displayName": "x"}),
        ("POST", f"{USERS}/{user_id}/invite", None),
        ("DELETE", f"{USERS}/{user_id}/invite", None),
        ("POST", f"{USERS}/{user_id}/reset-totp", None),
        ("POST", f"{USERS}/{user_id}/revoke-sessions", None),
        ("DELETE", f"{USERS}/{user_id}", None),
    ):
        response = await async_client.request(method, path, json=body)
        assert response.status_code == 404 and _error(response) == "user_not_found", (method, response.text)
    assert await _user(user_id) is None
    assert (await async_client.get(f"{USERS}/invites")).json() == []
    assert "bob" not in {u["username"] for u in (await async_client.get(USERS)).json()}

    # The list itself purges too.
    zombie = (await _create(async_client, "carl"))["user"]["id"]
    monkeypatch.setattr(users_repository, "utc_now", lambda: later + timedelta(hours=25))
    assert "carl" not in {u["username"] for u in (await async_client.get(USERS)).json()}
    assert await _user(zombie) is None


@pytest.mark.asyncio
async def test_accept_is_bound_to_the_presented_token(async_client: AsyncClient, app_instance, monkeypatch) -> None:
    """A resend between lookup and consume must fail the accept (the old link is dead)."""

    await _setup_admin(async_client)
    created = await _create(async_client, "bob")
    user_id, old_token = created["user"]["id"], created["invite"]["token"]
    original = users_service.DashboardUsersService._pending_invite

    async def _rotating_lookup(self, token):
        invite = await original(self, token)
        async with SessionLocal() as session:
            row = await session.get(DashboardUserInvite, invite.id)
            assert row is not None
            row.token_hash = users_service.invite_token_hash("rotated-by-a-resend")
            await session.commit()
        return invite

    monkeypatch.setattr(users_service.DashboardUsersService, "_pending_invite", _rotating_lookup)
    async with _client(app_instance) as guest:
        stale = await guest.post("/api/dashboard-auth/invite/accept", json={"token": old_token, "password": PASSWORD})
        assert stale.status_code == 404 and _error(stale) == "invite_not_found"
    monkeypatch.undo()
    refreshed = await _user(user_id)
    assert refreshed is not None and refreshed.status == "invited" and refreshed.password_hash is None
    async with SessionLocal() as session:
        invite = (
            await session.execute(select(DashboardUserInvite).where(DashboardUserInvite.user_id == user_id))
        ).scalar_one()
        assert invite.consumed_at is None


@pytest.mark.asyncio
async def test_uniqueness_races_answer_409(async_client: AsyncClient, app_instance, monkeypatch) -> None:
    """When the pre-check misses (a concurrent writer won), the unique violation is still a 409."""

    await _setup_admin(async_client)
    await _create(async_client, "bob", email="bob@example.com")
    pending = await _create(async_client, "carl")

    async def _nobody(self, value):
        return None

    monkeypatch.setattr(DashboardUsersRepository, "get_by_username", _nobody)
    monkeypatch.setattr(DashboardUsersRepository, "get_by_email", _nobody)
    taken = await async_client.post(USERS, json={"username": "bob", "roleId": VIEWER_ROLE})
    assert taken.status_code == 409 and _error(taken) == "username_taken"
    taken = await async_client.post(USERS, json={"username": "dave", "roleId": VIEWER_ROLE, "email": "bob@example.com"})
    assert taken.status_code == 409 and _error(taken) == "email_taken"
    async with _client(app_instance) as guest:
        taken = await guest.post(
            "/api/dashboard-auth/invite/accept",
            json={"token": pending["invite"]["token"], "password": PASSWORD, "username": "bob"},
        )
        assert taken.status_code == 409 and _error(taken) == "username_taken"
    taken = await async_client.patch(f"{USERS}/{pending['user']['id']}", json={"email": "bob@example.com"})
    assert taken.status_code == 409 and _error(taken) == "email_taken"
    monkeypatch.undo()
    # Nothing was consumed or half-written.
    async with _client(app_instance) as guest:
        await _accept(guest, pending["invite"]["token"])


@pytest.mark.asyncio
async def test_signed_in_browsers_and_stale_cookies(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    live = await _create(async_client, "bob")
    # A live token posted from a signed-in browser is refused and stays live.
    refused = await async_client.post(
        "/api/dashboard-auth/invite/accept", json={"token": live["invite"]["token"], "password": PASSWORD}
    )
    assert refused.status_code == 409 and _error(refused) == "already_signed_in"
    async with _client(app_instance) as fresh:
        await _accept(fresh, live["invite"]["token"])
        # After the admin revokes bob's sessions, bob's cookie is stale and no longer blocks an accept.
        assert (await async_client.post(f"{USERS}/{live['user']['id']}/revoke-sessions")).status_code == 200
        assert (await fresh.get("/api/dashboard-auth/me")).status_code == 401
        other = await _create(async_client, "carl")
        accepted = await _accept(fresh, other["invite"]["token"])
        assert accepted["user"]["username"] == "carl"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", f"{USERS}/x"),
        ("DELETE", f"{USERS}/x"),
        ("POST", f"{USERS}/x/invite"),
        ("DELETE", f"{USERS}/x/invite"),
        ("POST", f"{USERS}/x/reset-totp"),
        ("POST", f"{USERS}/x/revoke-sessions"),
        ("POST", f"{USERS}/x/reactivate-keys"),
    ],
)
async def test_every_mutation_requires_an_account(async_client: AsyncClient, method: str, path: str) -> None:
    response = await async_client.request(method, path, json={} if method == "PATCH" else None)
    assert response.status_code == 409 and _error(response) == "admin_account_required", response.text


# --- edits, roles, status ---


@pytest.mark.asyncio
async def test_role_change_is_audited_and_ends_the_targets_sessions(async_client: AsyncClient, app_instance) -> None:
    admin_id = await _setup_admin(async_client)
    created = await _create(async_client, "bob", role_id=OPERATOR_ROLE)
    user_id = created["user"]["id"]
    async with _client(app_instance) as bob:
        await _accept(bob, created["invite"]["token"])
        assert (await bob.get("/api/dashboard-auth/me")).status_code == 200

        changed = await async_client.patch(f"{USERS}/{user_id}", json={"roleId": VIEWER_ROLE, "displayName": "Bobby"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["role"]["slug"] == "viewer" and changed.json()["displayName"] == "Bobby"
        (row,) = await _rows("user_role_changed")
        assert row.actor_user_id == admin_id and row.target_id == user_id
        assert '"from": "operator"' in (row.details or "") and '"to": "viewer"' in (row.details or "")
        assert len(await _rows("user_updated")) == 1

        gone = await bob.get("/api/dashboard-auth/me")
        assert gone.status_code == 401

    # The migrated admin is an ordinary account: its own role and status are
    # refused because it is the *caller's*, not because of any compatibility
    # lock, and its profile stays editable.
    for body in ({"roleId": VIEWER_ROLE}, {"status": "disabled"}):
        refused_self = await async_client.patch(f"{USERS}/{admin_id}", json=body)
        assert refused_self.status_code == 409 and _error(refused_self) == "self_modification_forbidden"
    self_profile = await async_client.patch(f"{USERS}/{admin_id}", json={"displayName": "Me"})
    assert self_profile.status_code == 200 and self_profile.json()["displayName"] == "Me"

    second = await _create(async_client, "admin2", role_id=ADMIN_ROLE)
    async with _client(app_instance) as admin2:
        await _accept(admin2, second["invite"]["token"])
        for body in ({"roleId": VIEWER_ROLE}, {"status": "disabled"}):
            refused = await admin2.patch(f"{USERS}/{second['user']['id']}", json=body)
            assert refused.status_code == 409 and _error(refused) == "self_modification_forbidden"
        self_delete = await admin2.delete(f"{USERS}/{second['user']['id']}")
        assert self_delete.status_code == 409 and _error(self_delete) == "self_modification_forbidden"
    fresh_id = (await _create(async_client, "new"))["user"]["id"]
    invited_status = await async_client.patch(f"{USERS}/{fresh_id}", json={"status": "invited"})
    assert invited_status.status_code == 422
    pending_activate = await async_client.patch(f"{USERS}/{fresh_id}", json={"status": "active"})
    assert pending_activate.status_code == 409 and _error(pending_activate) == "invite_pending"
    # Editing an invited account keeps the listing shape (pendingInvite included).
    renamed = await async_client.patch(f"{USERS}/{fresh_id}", json={"displayName": "New"})
    assert renamed.status_code == 200 and renamed.json()["pendingInvite"] is not None
    missing = await async_client.patch(f"{USERS}/{uuid.uuid4()}", json={"displayName": "x"})
    assert missing.status_code == 404 and _error(missing) == "user_not_found"


async def _set_status(user_id: str, status: str) -> None:
    async with SessionLocal() as session:
        user = await session.get(DashboardUser, user_id)
        assert user is not None
        user.status = status
        await session.commit()
    await get_dashboard_users_cache().invalidate()


async def _count_active_admins() -> int:
    async with SessionLocal() as session:
        return await DashboardUsersRepository(session).count_active_admins()


async def _two_admins(
    admin_client: AsyncClient, app_instance, stack: AsyncExitStack
) -> tuple[AsyncClient, str, AsyncClient, str]:
    """Two further admins signed in; callers then park the migrated admin as disabled."""

    a = await _create(admin_client, "alice", role_id=ADMIN_ROLE)
    b = await _create(admin_client, "bruce", role_id=ADMIN_ROLE)
    alice = await stack.enter_async_context(_client(app_instance))
    bruce = await stack.enter_async_context(_client(app_instance))
    await _accept(alice, a["invite"]["token"])
    await _accept(bruce, b["invite"]["token"])
    return alice, a["user"]["id"], bruce, b["user"]["id"]


@pytest.mark.asyncio
async def test_last_active_admin_is_protected(async_client: AsyncClient, app_instance) -> None:
    """Custom roles never count as admins, however wide their grants."""

    admin_id = await _setup_admin(async_client)
    root_role = await _custom_role("root", ROOT_GRANTS)
    root = await _create(async_client, "root", role_id=root_role)
    async with AsyncExitStack() as stack:
        alice, alice_id, bruce, bruce_id = await _two_admins(async_client, app_instance, stack)
        root_client = await stack.enter_async_context(_client(app_instance))
        await _accept(root_client, root["invite"]["token"])
        # The migrated admin is disabled through the API like anyone else,
        # under the same rules (two other active admins exist).
        disabled_compat = await root_client.patch(f"{USERS}/{admin_id}", json={"status": "disabled"})
        assert disabled_compat.status_code == 200, disabled_compat.text
        assert (await async_client.get("/api/dashboard-auth/me")).status_code == 401

        # Two active admins: alice may be disabled by bruce...
        disabled = await bruce.patch(f"{USERS}/{alice_id}", json={"status": "disabled"})
        assert disabled.status_code == 200, disabled.text
        assert (await alice.get("/api/dashboard-auth/me")).status_code == 401
        # ...which makes bruce the last one: nobody may disable, demote or delete him.
        for body in ({"status": "disabled"}, {"roleId": VIEWER_ROLE}):
            refused = await root_client.patch(f"{USERS}/{bruce_id}", json=body)
            assert refused.status_code == 409 and _error(refused) == "last_admin_protected"
        refused = await root_client.delete(f"{USERS}/{bruce_id}")
        assert refused.status_code == 409 and _error(refused) == "last_admin_protected"
        assert await _count_active_admins() == 1

        reenabled = await bruce.patch(f"{USERS}/{alice_id}", json={"status": "active"})
        assert reenabled.status_code == 200
        deleted = await root_client.delete(f"{USERS}/{bruce_id}")
        assert deleted.status_code == 204
        assert (await bruce.get("/api/dashboard-auth/me")).status_code == 401
    # Two disables: the migrated admin (now an ordinary account, disabled
    # through the API) and alice, who was then re-enabled.
    assert {row.target_id for row in await _rows("user_disabled")} == {admin_id, alice_id}
    assert [row.target_id for row in await _rows("user_enabled")] == [alice_id]
    (deleted_row,) = await _rows("user_deleted")
    assert deleted_row.actor_username == "root" and deleted_row.target_id == bruce_id


@pytest.mark.asyncio
async def test_concurrent_admin_mutations_keep_exactly_one_admin(async_client: AsyncClient, app_instance) -> None:
    """The last-admin invariant is part of the write, not a check before it."""

    admin_id = await _setup_admin(async_client)
    async with AsyncExitStack() as stack:
        alice, alice_id, bruce, bruce_id = await _two_admins(async_client, app_instance, stack)
        assert (await alice.patch(f"{USERS}/{admin_id}", json={"status": "disabled"})).status_code == 200
        first, second = await asyncio.gather(
            alice.patch(f"{USERS}/{bruce_id}", json={"status": "disabled"}),
            bruce.patch(f"{USERS}/{alice_id}", json={"status": "disabled"}),
        )
        statuses = sorted(r.status_code for r in (first, second))
        assert statuses == [200, 409], [(r.status_code, r.text) for r in (first, second)]
        loser = first if first.status_code == 409 else second
        assert _error(loser) == "last_admin_protected"
        assert await _count_active_admins() == 1

        winner, winner_id, loser_id = (
            (alice, alice_id, bruce_id) if first.status_code == 200 else (bruce, bruce_id, alice_id)
        )
        assert (await winner.patch(f"{USERS}/{loser_id}", json={"status": "active"})).status_code == 200
        loser_client = bruce if winner is alice else alice
        login = await loser_client.post(
            "/api/dashboard-auth/password/login",
            json={"username": "bruce" if winner is alice else "alice", "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        assert await _count_active_admins() == 2

        first, second = await asyncio.gather(
            winner.patch(f"{USERS}/{loser_id}", json={"roleId": VIEWER_ROLE}),
            loser_client.delete(f"{USERS}/{winner_id}"),
        )
        assert sorted(r.status_code for r in (first, second)) in ([200, 409], [204, 409]), [
            (r.status_code, r.text) for r in (first, second)
        ]
        assert await _count_active_admins() == 1


@pytest.mark.asyncio
async def test_disable_cascades_owned_keys_and_reactivate_restores_only_those(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    created = await _create(async_client, "bob", role_id=OPERATOR_ROLE)
    user_id = created["user"]["id"]
    async with _client(app_instance) as bob:
        await _accept(bob, created["invite"]["token"])
    active_key = await _owned_key(async_client, user_id)
    manual_key = await _owned_key(async_client, user_id, active=False, reason="manual")
    unowned = await _owned_key(async_client, user_id)
    async with SessionLocal() as session:
        row = await session.get(ApiKey, unowned.id)
        assert row is not None
        row.owner_user_id = None
        await session.commit()
    cache = get_api_key_cache()
    await cache.set(active_key.key_hash, object())
    version = cache.version

    disabled = await async_client.patch(f"{USERS}/{user_id}", json={"status": "disabled"})
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "disabled"
    cascaded = await _key(active_key.id)
    assert cascaded.is_active is False and cascaded.deactivated_reason == "owner_disabled"
    assert (await _key(manual_key.id)).deactivated_reason == "manual"
    assert (await _key(unowned.id)).is_active is True
    assert await cache.get(active_key.key_hash) is None and cache.version > version
    (audit,) = await _rows("user_keys_deactivated")
    assert '"count": 1' in (audit.details or "")

    while_disabled = await async_client.post(f"{USERS}/{user_id}/reactivate-keys")
    assert while_disabled.status_code == 409 and _error(while_disabled) == "user_not_active"
    assert (await async_client.patch(f"{USERS}/{user_id}", json={"status": "active"})).status_code == 200
    assert (await _key(active_key.id)).is_active is False  # re-enabling never restores keys by itself

    version = cache.version
    restored = await async_client.post(f"{USERS}/{user_id}/reactivate-keys")
    assert restored.status_code == 200 and restored.json() == {"reactivated": 1}
    again = await _key(active_key.id)
    assert again.is_active is True and again.deactivated_reason is None
    manual = await _key(manual_key.id)
    assert manual.is_active is False and manual.deactivated_reason == "manual"
    assert cache.version > version
    assert (await async_client.post(f"{USERS}/{user_id}/reactivate-keys")).json() == {"reactivated": 0}


@pytest.mark.asyncio
async def test_delete_keeps_keys_inactive_without_an_owner(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    created = await _create(async_client, "bob", role_id=OPERATOR_ROLE)
    user_id = created["user"]["id"]
    async with _client(app_instance) as bob:
        await _accept(bob, created["invite"]["token"])
        key = await _owned_key(async_client, user_id)
        deleted = await async_client.delete(f"{USERS}/{user_id}")
        assert deleted.status_code == 204
        assert (await bob.get("/api/dashboard-auth/me")).status_code == 401
    assert await _user(user_id) is None
    orphan = await _key(key.id)
    assert orphan.owner_user_id is None and orphan.is_active is False
    assert orphan.deactivated_reason == "owner_disabled"
    (row,) = await _rows("user_deleted")
    assert row.actor_username == "admin" and '"username": "bob"' in (row.details or "")
    async with SessionLocal() as session:
        assert (
            await session.execute(select(DashboardUserInvite).where(DashboardUserInvite.user_id == user_id))
        ).first() is None


@pytest.mark.asyncio
async def test_reset_totp_and_revoke_sessions_bump_the_generation(async_client: AsyncClient, app_instance) -> None:
    admin_id = await _setup_admin(async_client)
    created = await _create(async_client, "bob", role_id=OPERATOR_ROLE)
    user_id = created["user"]["id"]
    async with _client(app_instance) as bob:
        await _accept(bob, created["invite"]["token"])
        revoked = await async_client.post(f"{USERS}/{user_id}/revoke-sessions")
        assert revoked.status_code == 200
        assert (await bob.get("/api/dashboard-auth/me")).status_code == 401
        login = await bob.post("/api/dashboard-auth/password/login", json={"username": "bob", "password": PASSWORD})
        assert login.status_code == 200, login.text

        async with SessionLocal() as session:
            user = await session.get(DashboardUser, user_id)
            assert user is not None
            user.totp_secret_encrypted = b"secret"
            user.totp_last_verified_step = 7
            await session.commit()
        await get_dashboard_users_cache().invalidate()
        assert next(u for u in (await async_client.get(USERS)).json() if u["id"] == user_id)["totpConfigured"] is True

        reset = await async_client.post(f"{USERS}/{user_id}/reset-totp")
        assert reset.status_code == 200
        assert (await bob.get("/api/dashboard-auth/me")).status_code == 401
    refreshed = await _user(user_id)
    assert refreshed is not None
    assert refreshed.totp_secret_encrypted is None and refreshed.totp_last_verified_step is None
    assert refreshed.session_generation == 3  # accept, revoke, reset
    (row,) = await _rows("user_sessions_revoked")
    assert '"scope": "admin"' in (row.details or "") and row.actor_user_id == admin_id
    assert len(await _rows("user_totp_reset")) == 1

    own = await async_client.post(f"{USERS}/{admin_id}/reset-totp")
    assert own.status_code == 409 and _error(own) == "self_modification_forbidden"


@pytest.mark.asyncio
async def test_profile_self_service(async_client: AsyncClient) -> None:
    await _setup_admin(async_client)
    await _create(async_client, "bob", email="bob@example.com")
    updated = await async_client.patch(
        "/api/dashboard-auth/me", json={"displayName": " Root ", "email": "Root@Example.com"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["displayName"] == "Root" and updated.json()["email"] == "root@example.com"
    taken = await async_client.patch("/api/dashboard-auth/me", json={"email": "bob@example.com"})
    assert taken.status_code == 409 and _error(taken) == "email_taken"
    cleared = await async_client.patch("/api/dashboard-auth/me", json={"email": None})
    assert cleared.status_code == 200 and cleared.json()["email"] is None
    rows = [r for r in await _rows("user_updated") if '"self": true' in (r.details or "")]
    assert len(rows) == 2 and {r.actor_username for r in rows} == {"admin"}


@pytest.mark.asyncio
async def test_api_key_patch_maintains_deactivated_reason(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    created = await _create(async_client, "bob", role_id=OPERATOR_ROLE)
    user_id = created["user"]["id"]
    async with _client(app_instance) as bob:
        await _accept(bob, created["invite"]["token"])
    first = await _owned_key(async_client, user_id)
    second = await _owned_key(async_client, user_id)

    assert (await async_client.patch(f"{USERS}/{user_id}", json={"status": "disabled"})).status_code == 200
    assert (await _key(first.id)).deactivated_reason == "owner_disabled"
    # An explicit revoke while the owner is disabled wins over the cascade.
    revoked = await async_client.patch(f"/api/api-keys/{first.id}", json={"isActive": False})
    assert revoked.status_code == 200, revoked.text
    assert (await _key(first.id)).deactivated_reason == "manual"
    # Re-enabling a key of a disabled owner is refused.
    refused = await async_client.patch(f"/api/api-keys/{second.id}", json={"isActive": True})
    assert refused.status_code == 409 and _error(refused) == "owner_disabled"

    assert (await async_client.patch(f"{USERS}/{user_id}", json={"status": "active"})).status_code == 200
    restored = await async_client.post(f"{USERS}/{user_id}/reactivate-keys")
    assert restored.json() == {"reactivated": 1}
    assert (await _key(first.id)).is_active is False and (await _key(second.id)).is_active is True
    reenabled = await async_client.patch(f"/api/api-keys/{first.id}", json={"isActive": True})
    assert reenabled.status_code == 200
    key = await _key(first.id)
    assert key.is_active is True and key.deactivated_reason is None
    revoked = await async_client.patch(f"/api/api-keys/{first.id}", json={"isActive": False})
    assert revoked.status_code == 200 and (await _key(first.id)).deactivated_reason == "manual"


async def _enrol_totp(client: AsyncClient) -> str:
    started = await client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = await client.post(
        "/api/dashboard-auth/totp/setup/confirm", json={"secret": secret, "code": pyotp.TOTP(secret).now()}
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret


async def _totp_policy(enabled: bool) -> None:
    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardSettings))).scalar_one()
        row.totp_required_on_login = enabled
        await session.commit()
    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


async def _both_totp_requirements(enabled: bool) -> None:
    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardSettings))).scalar_one()
        row.totp_required_on_login = enabled
        row.totp_required_for_admin_role = enabled
        await session.commit()
    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


async def _disable_own_totp(client: AsyncClient) -> None:
    """Enrol, turn the requirement on, verify, then drop the secret through ``/totp/disable``."""

    secret = await _enrol_totp(client)
    await _totp_policy(True)
    verified = await client.post("/api/dashboard-auth/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    assert verified.status_code == 200, verified.text
    # One step ahead of the code just spent on /totp/verify: still inside the
    # verification window, and not a replay of a consumed step.
    code = pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=30))
    disabled = await client.post("/api/dashboard-auth/totp/disable", json={"code": code})
    assert disabled.status_code == 200, disabled.text
    await get_settings_cache().invalidate()


async def _policy_flag() -> bool:
    async with SessionLocal() as session:
        return bool((await session.execute(select(DashboardSettings))).scalar_one().totp_required_on_login)


@pytest.mark.asyncio
async def test_reset_totp_keeps_the_install_policy_on_every_account(async_client: AsyncClient, app_instance) -> None:
    admin_id = await _setup_admin(async_client)
    second = await _create(async_client, "admin2", role_id=ADMIN_ROLE)
    async with _client(app_instance) as admin2:
        await _accept(admin2, second["invite"]["token"])
        admin_secret = await _enrol_totp(async_client)
        admin2_secret = await _enrol_totp(admin2)

        # Policy off: resetting the migrated admin is allowed and leaves the flag off.
        reset = await admin2.post(f"{USERS}/{admin_id}/reset-totp")
        assert reset.status_code == 200, reset.text
        assert await _policy_flag() is False
        assert len(await _rows("user_totp_reset")) == 1
        compat = await _user(admin_id)
        assert compat is not None and compat.totp_secret_encrypted is None
        # The reset ended the compat admin's sessions; sign back in and re-enrol so the policy can be turned on.
        login = await async_client.post(
            "/api/dashboard-auth/password/login", json={"username": "admin", "password": "password123"}
        )
        assert login.status_code == 200, login.text
        admin_secret = await _enrol_totp(async_client)

        await _totp_policy(True)
        for client, secret in ((async_client, admin_secret), (admin2, admin2_secret)):
            verified = await client.post("/api/dashboard-auth/totp/verify", json={"code": pyotp.TOTP(secret).now()})
            assert verified.status_code == 200, verified.text

        # Policy on: resetting another admin clears only that account and keeps
        # the install-wide policy on.
        reset = await async_client.post(f"{USERS}/{second['user']['id']}/reset-totp")
        assert reset.status_code == 200, reset.text
        assert await _policy_flag() is True
        assert (await admin2.get("/api/dashboard-auth/me")).status_code == 401
        login = await admin2.post(
            "/api/dashboard-auth/password/login", json={"username": "admin2", "password": PASSWORD}
        )
        assert login.status_code == 200, login.text
        assert login.json()["totpEnrollmentRequired"] is True
        held = await admin2.get(USERS)
        assert held.status_code == 403 and _error(held) == "totp_enrollment_required"

        # And the migrated admin is not exempt: with the policy still on it is
        # reset like anyone else and meets the same enrolment gate. Release N
        # refused this with 409 compat_user_locked to protect a previous-release
        # replica; there is no such replica and no such refusal.
        admin2_secret = await _enrol_totp(admin2)
        assert (
            await admin2.post("/api/dashboard-auth/totp/verify", json={"code": pyotp.TOTP(admin2_secret).now()})
        ).status_code == 200
        reset_compat = await admin2.post(f"{USERS}/{admin_id}/reset-totp")
        assert reset_compat.status_code == 200, reset_compat.text
        assert await _policy_flag() is True
        assert (await async_client.get("/api/dashboard-auth/me")).status_code == 401
        back = await async_client.post(
            "/api/dashboard-auth/password/login", json={"username": "admin", "password": "password123"}
        )
        assert back.status_code == 200 and back.json()["totpEnrollmentRequired"] is True
        held_compat = await async_client.get(USERS)
        assert held_compat.status_code == 403 and _error(held_compat) == "totp_enrollment_required"
    await _totp_policy(False)


@pytest.mark.asyncio
async def test_password_removal_is_refused_while_an_invite_is_pending(async_client: AsyncClient) -> None:
    await _setup_admin(async_client)
    pending = await _create(async_client, "bob")
    refused = await async_client.request("DELETE", "/api/dashboard-auth/password", json={"password": "password123"})
    assert refused.status_code == 409 and _error(refused) == "other_users_exist"
    assert (await async_client.delete(f"{USERS}/{pending['user']['id']}/invite")).status_code == 204
    removed = await async_client.request("DELETE", "/api/dashboard-auth/password", json={"password": "password123"})
    assert removed.status_code == 200, removed.text


@pytest.mark.asyncio
async def test_password_removal_is_refused_while_a_disabled_account_exists(
    async_client: AsyncClient, app_instance
) -> None:
    """A disabled account is still an account, and only an account can manage it.

    Removing the last password returns the install to the passwordless
    bootstrap state, where local requests are served as an implicit admin that
    holds no account and therefore cannot enable, delete or act as anybody.
    Allowing it while a disabled row survives strands that row for good: it
    cannot sign in (``disabled_user``), nobody can re-enable it, and -- because
    the removal also clears both install-wide TOTP requirements on the grounds
    that this account *is* the install -- it would come back exempt from a
    requirement the install still meant to have.
    """

    admin_id = await _setup_admin(async_client)
    second = await _create(async_client, "bob", role_id=ADMIN_ROLE)
    async with _client(app_instance) as bob:
        await _accept(bob, second["invite"]["token"])
        assert (await bob.patch(f"{USERS}/{admin_id}", json={"status": "disabled"})).status_code == 200

        refused = await bob.request("DELETE", "/api/dashboard-auth/password", json={"password": PASSWORD})
        assert refused.status_code == 409 and _error(refused) == "other_users_exist"

        # The install still demands a sign-in, so the disabled row is still
        # reachable by somebody who can manage it.
        async with _client(app_instance) as anonymous:
            state = await anonymous.get("/api/dashboard-auth/session")
            assert state.json()["passwordRequired"] is True and state.json()["authenticated"] is False
        assert (await bob.patch(f"{USERS}/{admin_id}", json={"status": "active"})).status_code == 200
        assert (await bob.patch(f"{USERS}/{admin_id}", json={"status": "disabled"})).status_code == 200

        # Deleting it is the way through, and the route says so.
        assert (await bob.delete(f"{USERS}/{admin_id}")).status_code == 204
        removed = await bob.request("DELETE", "/api/dashboard-auth/password", json={"password": PASSWORD})
        assert removed.status_code == 200, removed.text

    # ...and the install that is left is the passwordless one, re-bootstrappable.
    async with _client(app_instance) as fresh:
        again = await fresh.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
        assert again.status_code == 200, again.text


async def _owner_and_keys_consistent(owner_id: str, key_ids: list[str]) -> None:
    owner = await _user(owner_id)
    assert owner is not None
    keys = [await _key(key_id) for key_id in key_ids]
    if owner.status == "disabled":
        assert all(not key.is_active and key.deactivated_reason == "owner_disabled" for key in keys)
    else:
        assert owner.status == "active" and all(key.is_active for key in keys)


@pytest.mark.asyncio
async def test_key_reactivation_never_leaves_active_keys_on_a_disabled_owner(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    created = await _create(async_client, "bob", role_id=OPERATOR_ROLE)
    user_id = created["user"]["id"]
    async with _client(app_instance) as bob:
        await _accept(bob, created["invite"]["token"])
    key = await _owned_key(async_client, user_id)
    for _ in range(3):
        assert (await async_client.patch(f"{USERS}/{user_id}", json={"status": "disabled"})).status_code == 200
        assert (await async_client.patch(f"{USERS}/{user_id}", json={"status": "active"})).status_code == 200
        disable, reactivate = await asyncio.gather(
            async_client.patch(f"{USERS}/{user_id}", json={"status": "disabled"}),
            async_client.post(f"{USERS}/{user_id}/reactivate-keys"),
        )
        assert disable.status_code == 200, disable.text
        assert reactivate.status_code in (200, 409), reactivate.text
        if reactivate.status_code == 409:
            assert _error(reactivate) == "user_not_active"
        await _owner_and_keys_consistent(user_id, [key.id])
        # The same holds for the API-key page re-enabling a key of a racing owner.
        assert (await async_client.patch(f"{USERS}/{user_id}", json={"status": "active"})).status_code == 200
        disable, patched = await asyncio.gather(
            async_client.patch(f"{USERS}/{user_id}", json={"status": "disabled"}),
            async_client.patch(f"/api/api-keys/{key.id}", json={"isActive": True}),
        )
        assert disable.status_code == 200, disable.text
        assert patched.status_code in (200, 409), patched.text
        if patched.status_code == 409:
            assert _error(patched) == "owner_disabled"
        owner = await _user(user_id)
        assert owner is not None and owner.status == "disabled"
        assert (await _key(key.id)).is_active is False
        # Undo the manual reason left by a winning PATCH so the next round starts from the cascade state.
        async with SessionLocal() as session:
            row = await session.get(ApiKey, key.id)
            assert row is not None
            row.deactivated_reason = "owner_disabled"
            await session.commit()


@pytest.mark.asyncio
async def test_purge_spares_an_account_activated_meanwhile(async_client: AsyncClient, monkeypatch) -> None:
    """The purge decides in its own DELETE: an acceptance that committed after the candidate scan survives."""

    await _setup_admin(async_client)
    zombie = (await _create(async_client, "zombie"))["user"]["id"]
    racer = (await _create(async_client, "racer"))["user"]["id"]
    monkeypatch.setattr(users_repository, "utc_now", lambda: datetime.now(UTC) + timedelta(hours=25))
    original = DashboardUsersRepository.purge_expired_invited_users

    async def _accept_lands_first(self, now):
        # Simulates the concurrent acceptance committing between "select candidates" and "delete".
        await _set_status(racer, "active")
        return await original(self, now)

    monkeypatch.setattr(DashboardUsersRepository, "purge_expired_invited_users", _accept_lands_first)
    listing = {u["username"]: u["status"] for u in (await async_client.get(USERS)).json()}
    assert listing == {"admin": "active", "racer": "active"}
    assert await _user(zombie) is None and await _user(racer) is not None


@pytest.mark.asyncio
async def test_revoke_and_resend_refuse_an_account_accepted_meanwhile(
    async_client: AsyncClient, app_instance, monkeypatch
) -> None:
    await _setup_admin(async_client)
    created = await _create(async_client, "bob")
    user_id, token = created["user"]["id"], created["invite"]["token"]
    original = users_service.DashboardUsersService._get
    accepted_once = False

    async def _get_then_accept(self, requested_id):
        nonlocal accepted_once
        user = await original(self, requested_id)
        if requested_id == user_id and not accepted_once:
            accepted_once = True
            async with _client(app_instance) as guest:
                await _accept(guest, token)
        return user

    monkeypatch.setattr(users_service.DashboardUsersService, "_get", _get_then_accept)
    revoked = await async_client.delete(f"{USERS}/{user_id}/invite")
    assert revoked.status_code == 409 and _error(revoked) == "invite_not_pending"
    survivor = await _user(user_id)
    assert survivor is not None and survivor.status == "active" and survivor.password_hash is not None
    monkeypatch.undo()

    second = await _create(async_client, "carl")
    accepted_once = False
    user_id, token = second["user"]["id"], second["invite"]["token"]
    monkeypatch.setattr(users_service.DashboardUsersService, "_get", _get_then_accept)
    resent = await async_client.post(f"{USERS}/{user_id}/invite")
    assert resent.status_code == 409 and _error(resent) == "invite_not_pending"
    async with SessionLocal() as session:
        invite = (
            await session.execute(select(DashboardUserInvite).where(DashboardUserInvite.user_id == user_id))
        ).scalar_one()
    assert invite.consumed_at is not None  # the accepted invite was not re-armed


# --- roles ---


@pytest.mark.asyncio
async def test_roles_read_api(async_client: AsyncClient) -> None:
    await _setup_admin(async_client)
    await _create(async_client, "bob")
    roles = await async_client.get("/api/dashboard-roles")
    assert roles.status_code == 200, roles.text
    by_slug = {role["slug"]: role for role in roles.json()}
    assert set(by_slug) == {"admin", "operator", "member", "viewer", "guest"}
    admin = by_slug["admin"]
    assert admin["id"] == ADMIN_ROLE and admin["kind"] == "preset" and admin["locked"] is True
    assert admin["assignableToUsers"] is True and admin["usersCount"] == 1
    assert {"permission": "users:manage", "scope": "all"} in admin["grants"]
    assert len(admin["grants"]) == len(Permission)
    assert by_slug["guest"]["assignableToUsers"] is False and by_slug["member"]["assignableToUsers"] is False
    assert by_slug["viewer"]["usersCount"] == 1
    assert by_slug["viewer"]["grants"] == [
        {"permission": "accounts:read", "scope": "all"},
        {"permission": "dashboard:read", "scope": "all"},
    ]

    permissions = await async_client.get("/api/dashboard-roles/permissions")
    assert permissions.status_code == 200
    by_permission = {p["permission"]: p for p in permissions.json()}
    assert set(by_permission) == {p.value for p in Permission}
    assert by_permission["api_keys:assign"]["implies"] == ["api_keys:write"]
    assert by_permission["api_keys:write"]["ownSupported"] is True
    assert by_permission["users:manage"]["privileged"] is True
    assert by_permission["accounts:read"]["privileged"] is False and by_permission["accounts:read"]["implies"] == []
    assert all(p["description"] for p in permissions.json())


# --- schema ---


@pytest.mark.asyncio
async def test_invites_migration_upgrades_and_downgrades(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'invites.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, _PARENT_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        target = await to_thread.run_sync(lambda: run_upgrade(db_url, _TARGET_REVISION, bootstrap_legacy=False))
        assert target.current_revision == _TARGET_REVISION
        config = _build_alembic_config(db_url)
        # Re-running the upgrade body over the existing table is a no-op.
        await to_thread.run_sync(lambda: command.stamp(config, _PARENT_REVISION))
        await to_thread.run_sync(lambda: run_upgrade(db_url, _TARGET_REVISION, bootstrap_legacy=False))
        async with engine.connect() as conn:
            columns = {row[1] for row in await conn.execute(text("PRAGMA table_info('dashboard_user_invites')"))}
        assert columns == {
            "id",
            "user_id",
            "token_hash",
            "expires_at",
            "consumed_at",
            "revoked_at",
            "created_by_user_id",
            "sso_only",
            "username_locked",
            "created_at",
        }
        await to_thread.run_sync(lambda: command.downgrade(config, _PARENT_REVISION))
        async with engine.connect() as conn:
            tables = {row[0] for row in await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "dashboard_user_invites" not in tables
        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
    finally:
        await engine.dispose()


# --- roles a sign-in provider manages (PR-2c-2) ---


async def _managed_by_provider(user_id: str) -> None:
    """Make the account look like one a sign-in provider created and manages."""

    async with SessionLocal() as session:
        user = await session.get(DashboardUser, user_id)
        assert user is not None
        user.role_source = "mapping"
        session.add(
            DashboardIdentity(
                user_id=user_id, provider="trusted_header", provider_key="default", subject="bob@example.com"
            )
        )
        await session.commit()
    await get_dashboard_users_cache().invalidate()


@pytest.mark.asyncio
async def test_an_externally_managed_role_is_changed_only_with_force(async_client: AsyncClient) -> None:
    admin_id = await _setup_admin(async_client)
    created = await _create(async_client, "bob", role_id=OPERATOR_ROLE)
    user_id = created["user"]["id"]
    await _managed_by_provider(user_id)

    refused = await async_client.patch(f"{USERS}/{user_id}", json={"roleId": VIEWER_ROLE})
    assert refused.status_code == 409 and _error(refused) == "role_managed_externally"
    unchanged = await _user(user_id)
    assert unchanged is not None and unchanged.role_id == OPERATOR_ROLE and unchanged.role_source == "mapping"
    assert await _rows("user_role_changed") == []

    # ``force`` only means something together with a role change.
    pointless = await async_client.patch(f"{USERS}/{user_id}", json={"force": True, "displayName": "Bobby"})
    assert pointless.status_code == 422

    forced = await async_client.patch(f"{USERS}/{user_id}", json={"roleId": VIEWER_ROLE, "force": True})
    assert forced.status_code == 200, forced.text
    assert forced.json()["role"]["slug"] == "viewer"
    taken_over = await _user(user_id)
    # Taken over by hand: no later re-evaluation moves this role again.
    assert taken_over is not None and taken_over.role_id == VIEWER_ROLE and taken_over.role_source == "manual"

    (override,) = await _rows("role_source_overridden")
    assert override.actor_user_id == admin_id and override.target_id == user_id
    assert '"from_source": "mapping"' in (override.details or "")
    assert '"to_source": "manual"' in (override.details or "")
    assert '"provider": "trusted_header"' in (override.details or "")
    assert len(await _rows("user_role_changed")) == 1

    # An account that is already manual takes ``force`` without a second override row.
    again = await async_client.patch(f"{USERS}/{user_id}", json={"roleId": OPERATOR_ROLE, "force": True})
    assert again.status_code == 200, again.text
    assert len(await _rows("role_source_overridden")) == 1
    assert len(await _rows("user_role_changed")) == 2


# --- rename ---


@pytest.mark.asyncio
async def test_the_bootstrap_account_can_be_renamed(async_client: AsyncClient, app_instance) -> None:
    """Release N pinned the name because the legacy mirror was keyed on it; nothing is now."""

    admin_id = await _setup_admin(async_client)
    created = await _create(async_client, "bob", role_id=OPERATOR_ROLE)
    async with _client(app_instance) as bob:
        await _accept(bob, created["invite"]["token"])

        renamed = await async_client.patch(f"{USERS}/{admin_id}", json={"username": "Alice"})
        assert renamed.status_code == 200, renamed.text
        # Normalised exactly as a username chosen at creation is.
        assert renamed.json()["username"] == "alice" and renamed.json()["id"] == admin_id
        (row,) = await _rows("user_renamed")
        assert row.target_id == admin_id
        assert '"from": "admin"' in (row.details or "") and '"to": "alice"' in (row.details or "")

        # A rename is not a role or status change: the session survives it, and
        # the account signs in under the new name.
        assert (await async_client.get("/api/dashboard-auth/me")).json()["username"] == "alice"
        stored = await _user(admin_id)
        assert stored is not None and stored.session_generation == 0
        await async_client.post("/api/dashboard-auth/logout", json={})
        stale = await async_client.post(
            "/api/dashboard-auth/password/login", json={"username": "admin", "password": "password123"}
        )
        assert stale.status_code == 401
        login = await async_client.post(
            "/api/dashboard-auth/password/login", json={"username": "alice", "password": "password123"}
        )
        assert login.status_code == 200, login.text

        # Taken and reserved names are refused, and nothing changes.
        taken = await async_client.patch(f"{USERS}/{admin_id}", json={"username": "bob"})
        assert taken.status_code == 409 and _error(taken) == "username_taken"
        back = await async_client.patch(f"{USERS}/{admin_id}", json={"username": "admin"})
        assert back.status_code == 422 and _error(back) == "validation_error"
        cleared = await async_client.patch(f"{USERS}/{admin_id}", json={"username": None})
        assert cleared.status_code == 422
        unchanged = await _user(admin_id)
        assert unchanged is not None and unchanged.username == "alice"
        assert len(await _rows("user_renamed")) == 1

        # Renaming somebody else works the same way, and re-using the name the
        # bootstrap account left is still refused: the reservation is one-way.
        other = await async_client.patch(f"{USERS}/{created['user']['id']}", json={"username": "robert"})
        assert other.status_code == 200 and other.json()["username"] == "robert"
        assert (await bob.get("/api/dashboard-auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_a_renamed_install_can_still_be_re_bootstrapped(async_client: AsyncClient) -> None:
    """The rename trap: setup re-arms the row by its id, never by the name it happens to carry.

    A lookup by ``admin`` would miss the renamed row, collide on the
    deterministic id and answer ``409 password_already_configured`` forever.
    """

    admin_id = await _setup_admin(async_client)
    assert (await async_client.patch(f"{USERS}/{admin_id}", json={"username": "alice"})).status_code == 200

    removed = await async_client.request("DELETE", "/api/dashboard-auth/password", json={"password": "password123"})
    assert removed.status_code == 200, removed.text
    again = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password456"})
    assert again.status_code == 200, again.text
    assert again.json()["user"]["id"] == admin_id
    assert again.json()["user"]["username"] == "alice"  # re-armed in place, not re-created as `admin`
    async with SessionLocal() as session:
        assert (await session.execute(text("SELECT COUNT(*) FROM dashboard_users"))).scalar_one() == 1
    login = await async_client.post(
        "/api/dashboard-auth/password/login", json={"username": "alice", "password": "password456"}
    )
    assert login.status_code == 200, login.text


@pytest.mark.asyncio
async def test_password_removal_clears_both_requirements_on_a_renamed_account(async_client: AsyncClient) -> None:
    """The install-wide reset is decided by the operation, not by the account's name.

    Release N keyed it on ``admin``, so on a renamed install it would silently
    have stopped firing -- leaving sign-in mandatory with no account able to
    present the factor it demands.
    """

    admin_id = await _setup_admin(async_client)
    assert (await async_client.patch(f"{USERS}/{admin_id}", json={"username": "alice"})).status_code == 200
    secret = await _enrol_totp(async_client)
    await _both_totp_requirements(True)
    assert (
        await async_client.post("/api/dashboard-auth/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    ).status_code == 200

    removed = await async_client.request("DELETE", "/api/dashboard-auth/password", json={"password": "password123"})
    assert removed.status_code == 200, removed.text
    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardSettings))).scalar_one()
    assert row.totp_required_on_login is False and row.totp_required_for_admin_role is False
    await get_settings_cache().invalidate()
    # No sign-in is required, and the install is not holding a door nobody can open.
    assert (await async_client.get("/api/settings")).status_code == 200


@pytest.mark.asyncio
async def test_a_team_member_cannot_turn_off_the_install_requirement_from_totp_disable(
    async_client: AsyncClient, app_instance
) -> None:
    """``/totp/disable`` carries no ``security:write``; on a team it may not move an install-wide setting."""

    await _setup_admin(async_client)
    second = await _create(async_client, "admin2", role_id=ADMIN_ROLE)
    async with _client(app_instance) as admin2:
        await _accept(admin2, second["invite"]["token"])
        admin_secret = await _enrol_totp(async_client)
        admin2_secret = await _enrol_totp(admin2)
        await _totp_policy(True)
        for client, secret in ((async_client, admin_secret), (admin2, admin2_secret)):
            verified = await client.post("/api/dashboard-auth/totp/verify", json={"code": pyotp.TOTP(secret).now()})
            assert verified.status_code == 200, verified.text

        # One step ahead of the code just spent on /totp/verify: still inside
        # the verification window, and not a replay of a consumed step.
        next_step_code = pyotp.TOTP(admin2_secret).at(datetime.now(UTC) + timedelta(seconds=30))
        disabled = await admin2.post("/api/dashboard-auth/totp/disable", json={"code": next_step_code})
        assert disabled.status_code == 200, disabled.text
        async with SessionLocal() as session:
            row = (await session.execute(select(DashboardSettings))).scalar_one()
        assert row.totp_required_on_login is True
        await get_settings_cache().invalidate()
        # ...and that account meets the gate it left standing.
        assert (await admin2.get("/api/dashboard-auth/me")).status_code in (200, 403)
        await admin2.post("/api/dashboard-auth/logout", json={})
        login = await admin2.post(
            "/api/dashboard-auth/password/login", json={"username": "admin2", "password": PASSWORD}
        )
        assert login.status_code == 200 and login.json()["totpEnrollmentRequired"] is True
    await _totp_policy(False)


@pytest.mark.asyncio
async def test_a_disabled_colleague_still_counts_as_a_second_account_for_totp_disable(
    async_client: AsyncClient, app_instance
) -> None:
    """An install is every account it holds, not the ones that happen to be active today.

    A disabled account keeps its role and can be enabled again by anybody with
    ``users:manage``, so turning the install-wide requirement off because it was
    not counted is the self-service route deciding somebody else's sign-in.
    """

    admin_id = await _setup_admin(async_client)
    second = await _create(async_client, "admin2", role_id=ADMIN_ROLE)
    async with _client(app_instance) as admin2:
        await _accept(admin2, second["invite"]["token"])
        assert (await admin2.patch(f"{USERS}/{admin_id}", json={"status": "disabled"})).status_code == 200

        # One active account, one disabled one: the requirement stays on.
        await _disable_own_totp(admin2)
        assert await _policy_flag() is True
        await _totp_policy(False)

        # The disabled account is deleted; now the acting account really is the
        # install, and the same route turns the requirement off.
        assert (await admin2.delete(f"{USERS}/{admin_id}")).status_code == 204
        await _disable_own_totp(admin2)
        assert await _policy_flag() is False
    await _totp_policy(False)
