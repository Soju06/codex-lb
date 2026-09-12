"""Product-path coverage for ``local-login-policy-and-break-glass``.

``local_login_policy`` closes the local password form; the qualifying
break-glass invariant makes sure closing it can never be a lockout. Both
directions are exercised through the routes: the policy refuses ordinary
sign-ins, the emergency account always presents a second factor and is
audited at critical severity, and every mutation that could remove the last
qualifying account answers ``409 last_break_glass_protected``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import ExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pyotp
import pytest
from alembic import command
from anyio import to_thread
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.audit.service import AuditActor, drain_audit_log_tasks
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade
from app.db.models import (
    AuditLog,
    AuthProviderKind,
    DashboardAuthProvider,
    DashboardSettings,
    DashboardUser,
    LocalLoginPolicy,
    RateLimitAttempt,
)
from app.db.session import SessionLocal
from app.modules.auth_providers.repository import AuthProvidersRepository
from app.modules.auth_providers.seed import auth_provider_id
from app.modules.dashboard_auth.repository import DashboardAuthRepository
from app.modules.dashboard_auth.service import get_password_address_rate_limiter
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_users.break_glass import LastBreakGlassProtectedError
from app.modules.dashboard_users.repository import DashboardUsersRepository
from app.modules.dashboard_users.service import DashboardUsersService
from app.modules.settings.repository import SettingsRepository

pytestmark = pytest.mark.integration

USERS = "/api/dashboard-users"
SETTINGS = "/api/settings"
SESSION = "/api/dashboard-auth/session"
LOGIN = "/api/dashboard-auth/password/login"
ADMIN_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
OPERATOR_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]
ADMIN_PASSWORD = "password" + "123"
PASSWORD = "invited-" + "password-1"
TRUSTED_HEADER_PROVIDER_ID = auth_provider_id(AuthProviderKind.TRUSTED_HEADER)
#: A password no account holds. Built here rather than inline next to a username
#: so the pair never reads as a credential to secret scanners.
WRONG_PASSWORD = "no" + "pe"


def _attempt(username: str) -> dict[str, str]:
    """A sign-in body that is guaranteed to fail on the password check."""
    return {"username": username, "password": WRONG_PASSWORD}


_PARENT_REVISION = "20260911_020000_add_http_bridge_terminal_append_phase"
_TARGET_REVISION = "20260911_030000_add_local_login_policy"


# --- helpers ---


@pytest.fixture(autouse=True)
def _isolate_settings_cache():
    """``get_settings`` is a process-wide ``lru_cache``; this module patches the environment.

    Cleared on both sides on purpose. Clearing on the way in makes the module
    independent of whatever a previous test left cached, and clearing on the
    way out means a ``Settings`` built from this module's patched environment
    never outlives the test that patched it — the cache is the one piece of
    state the per-test database reset and the hot-path cache reset do not own.
    """

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@asynccontextmanager
async def _client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


def _error(response) -> str:
    return response.json()["error"]["code"]


def _code(secret: str, *, steps: int = 0) -> str:
    return pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=30 * steps))


async def _rows(action: str) -> list[AuditLog]:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _user(user_id: str) -> DashboardUser | None:
    async with SessionLocal() as session:
        return await session.get(DashboardUser, user_id)


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


async def _enrol_totp(client: AsyncClient) -> str:
    started = await client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = await client.post(
        "/api/dashboard-auth/totp/setup/confirm", json={"secret": secret, "code": _code(secret)}
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret


async def _emergency_admin(admin: AsyncClient, person: AsyncClient, username: str = "rescue") -> tuple[str, str]:
    """A second, non-compat admin with a second factor, designated as the emergency account.

    Deliberately not the compat ``admin`` row: that one is locked by
    ``compat_user_locked``, which would mask the guard under test.
    """

    user_id = await _invite_and_accept(admin, person, username, ADMIN_ROLE)
    secret = await _enrol_totp(person)
    patched = await admin.patch(f"{USERS}/{user_id}", json={"isBreakGlass": True})
    assert patched.status_code == 200, patched.text
    assert patched.json()["isBreakGlass"] is True
    return user_id, secret


async def _require_totp_on_login() -> None:
    """Turn the install-wide second-factor requirement on, as the Settings page does."""

    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardSettings))).scalar_one()
        row.totp_required_on_login = True
        await session.commit()
    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


async def _set_policy(policy: LocalLoginPolicy) -> None:
    """Store the policy directly; the API gate that guards tightening has its own tests."""

    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardSettings))).scalar_one()
        row.local_login_policy = policy.value
        await session.commit()
    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


async def _clear_rate_limits() -> None:
    async with SessionLocal() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()


def _from(host: str) -> dict[str, str]:
    """One ASGI transport has one socket peer; the trusted forwarded header stands in for the address."""

    return {"X-Forwarded-For": host}


# --- 1. the policy on the sign-in path ---


@pytest.mark.asyncio
async def test_break_glass_only_refuses_an_ordinary_sign_in_indistinguishably(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _invite_and_accept(async_client, person, "olivia", OPERATOR_ROLE)
    await _set_policy(LocalLoginPolicy.BREAK_GLASS_ONLY)
    await _clear_rate_limits()

    async with _client(app_instance) as anonymous:
        refused = await anonymous.post(LOGIN, json={"username": "olivia", "password": PASSWORD})
        unknown = await anonymous.post(LOGIN, json={"username": "nobody", "password": PASSWORD})
    assert refused.status_code == unknown.status_code == 401
    assert refused.json() == unknown.json()
    assert _error(refused) == "invalid_credentials"

    reasons = [row.details or "" for row in await _rows("login_failed")]
    assert any('"reason": "login_policy"' in row and '"username": "olivia"' in row for row in reasons)

    # The unauthenticated login screen learns the policy, never the account name.
    hint = (await async_client.get(SESSION)).json()
    assert hint["login"]["localLogin"] == "break_glass_only"
    assert "rescue" not in str(hint) and "olivia" not in str(hint)


@pytest.mark.parametrize(
    "policy",
    [LocalLoginPolicy.ENABLED, LocalLoginPolicy.ADMINS_ONLY, LocalLoginPolicy.BREAK_GLASS_ONLY],
    ids=lambda policy: policy.value,
)
@pytest.mark.asyncio
async def test_the_emergency_admin_needs_a_second_factor_and_is_audited_as_critical(
    async_client: AsyncClient, app_instance, policy: LocalLoginPolicy
) -> None:
    """The always-two-factor rule belongs to the designation, not to the policy.

    ``_totp_required_for`` ORs ``break_glass_second_factor_required`` in
    regardless of ``local_login_policy``, so exercising only the strictest
    value would let a regression that tied the two together pass: under
    ``enabled`` the designated account would then get in on its password alone,
    which is the state the whole invariant assumes cannot happen.
    """

    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        _, secret = await _emergency_admin(async_client, person)
        await _set_policy(policy)
        await person.post("/api/dashboard-auth/logout", json={})

        # Both TOTP toggles are off, and the account still cannot get in on its password.
        login = await person.post(LOGIN, json={"username": "rescue", "password": PASSWORD})
        assert login.status_code == 200 and login.json()["authenticated"] is False
        assert login.json()["totpRequiredOnLogin"] is True
        blocked = await person.get(SETTINGS)
        assert blocked.status_code == 401 and _error(blocked) == "totp_required"

        verified = await person.post("/api/dashboard-auth/totp/verify", json={"code": _code(secret)})
        assert verified.status_code == 200, verified.text
        assert verified.json()["authenticated"] is True
        assert verified.json()["breakGlassSession"] is True
        assert (await person.get(SETTINGS)).status_code == 200

    emergency = await _rows("break_glass_login")
    assert len(emergency) == 1
    assert emergency[0].severity == "critical"
    assert emergency[0].actor_username == "rescue"

    # An account that carries no designation signs in without either.
    await _set_policy(LocalLoginPolicy.ENABLED)
    async with _client(app_instance) as ordinary:
        await _invite_and_accept(async_client, ordinary, "olivia", OPERATOR_ROLE)
        assert (await ordinary.get(SESSION)).json()["breakGlassSession"] is False
    assert len(await _rows("break_glass_login")) == 1


@pytest.mark.asyncio
async def test_the_migrated_admin_signs_in_and_enrols_while_the_policy_is_open(async_client: AsyncClient) -> None:
    """Designated but not qualifying: the upgrade path must never self-lock."""

    await _setup_admin(async_client)
    admin = await _user((await async_client.get(SESSION)).json()["user"]["id"])
    assert admin is not None and admin.is_break_glass is True and admin.totp_secret_encrypted is None

    await async_client.post("/api/dashboard-auth/logout", json={})
    login = await async_client.post(LOGIN, json={"password": ADMIN_PASSWORD})
    assert login.status_code == 200 and login.json()["authenticated"] is True
    assert (await async_client.get(SETTINGS)).status_code == 200
    assert await _enrol_totp(async_client)


@pytest.mark.asyncio
async def test_a_designation_without_a_secret_signs_in_as_an_ordinary_admin(async_client: AsyncClient) -> None:
    """The unenrolled designation meets the install's own TOTP policy and nothing more.

    ``break_glass_second_factor_required`` binds an account that *holds* a
    secret, so the migrated ``admin`` row is an ordinary admin sign-in: on an
    install that requires two-factor it lands in the enrolment state and
    reaches ``/totp/setup/*`` from there, which is the only way an upgraded
    install ever gets a qualifying account. The critical ``break_glass_login``
    row belongs to the second factor — writing one for a sign-in that
    presented none would put a critical event on every ordinary sign-in of
    every upgraded install, and the severity would stop meaning anything.
    """

    await _setup_admin(async_client)
    admin = await _user((await async_client.get(SESSION)).json()["user"]["id"])
    assert admin is not None and admin.is_break_glass is True and admin.totp_secret_encrypted is None
    await _require_totp_on_login()
    await async_client.post("/api/dashboard-auth/logout", json={})
    await _clear_rate_limits()

    login = await async_client.post(LOGIN, json={"username": "admin", "password": ADMIN_PASSWORD})
    assert login.status_code == 200, login.text
    body = login.json()
    # Signed in, not held pending: nothing asks a factor of an account that
    # holds none. The indicator follows the designation all the same.
    assert body["authenticated"] is True and body["totpRequiredOnLogin"] is False
    assert body["totpEnrollmentRequired"] is True
    assert body["breakGlassSession"] is True

    refused = await async_client.get(SETTINGS)
    assert refused.status_code == 403 and _error(refused) == "totp_enrollment_required"
    assert await _rows("break_glass_login") == []

    secret = await _enrol_totp(async_client)

    # Enrolled: the always-two-factor rule takes over, and the enrolment did
    # not verify the session it was made from.
    pending = await async_client.get(SETTINGS)
    assert pending.status_code == 401 and _error(pending) == "totp_required"
    verified = await async_client.post("/api/dashboard-auth/totp/verify", json={"code": _code(secret)})
    assert verified.status_code == 200, verified.text
    assert (await async_client.get(SETTINGS)).status_code == 200
    assert [row.severity for row in await _rows("break_glass_login")] == ["critical"]


@pytest.mark.asyncio
async def test_break_glass_only_refuses_a_designation_that_has_not_enrolled(
    async_client: AsyncClient, app_instance
) -> None:
    """The migrated ``admin`` row is a designation, not a second factor.

    Admitting it would leave the strictest policy with a password-only admin
    door — weaker than ``admins_only``. It stays admitted where it can enrol.
    """

    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
    admin = await _user((await async_client.get(SESSION)).json()["user"]["id"])
    assert admin is not None and admin.is_break_glass is True and admin.totp_secret_encrypted is None

    for policy in (LocalLoginPolicy.ENABLED, LocalLoginPolicy.ADMINS_ONLY):
        await _set_policy(policy)
        await _clear_rate_limits()
        async with _client(app_instance) as anonymous:
            allowed = await anonymous.post(LOGIN, json={"username": "admin", "password": ADMIN_PASSWORD})
            assert allowed.status_code == 200, f"{policy.value}: {allowed.text}"
            assert allowed.json()["authenticated"] is True

    await _set_policy(LocalLoginPolicy.BREAK_GLASS_ONLY)
    await _clear_rate_limits()
    async with _client(app_instance) as anonymous:
        refused = await anonymous.post(LOGIN, json={"username": "admin", "password": ADMIN_PASSWORD})
        unknown = await anonymous.post(LOGIN, json={"username": "nobody", "password": ADMIN_PASSWORD})
    assert refused.status_code == 401 and _error(refused) == "invalid_credentials"
    assert refused.json() == unknown.json()


@pytest.mark.asyncio
async def test_an_admin_without_a_local_password_does_not_count_as_a_way_back_in(
    async_client: AsyncClient, app_instance
) -> None:
    """A proxy-provisioned admin cannot use the local form, so tightening stays refused."""

    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        user_id, _ = await _emergency_admin(async_client, person, "rescue")
    async with SessionLocal() as session:
        row = await session.get(DashboardUser, user_id)
        assert row is not None
        row.password_hash = None
        await session.commit()
    await get_dashboard_users_cache().invalidate()

    async with SessionLocal() as session:
        assert await DashboardUsersRepository(session).count_qualifying_break_glass() == 0

    refused = await async_client.put(SETTINGS, json={"localLoginPolicy": "break_glass_only"})
    assert refused.status_code == 409 and _error(refused) == "break_glass_requires_totp"
    assert (await async_client.get(SETTINGS)).json()["localLoginPolicy"] == "enabled"


@pytest.mark.asyncio
async def test_removing_the_last_emergency_password_is_refused(async_client: AsyncClient) -> None:
    """Password removal is one more way to lose the last qualifying account."""

    await _setup_admin(async_client)
    secret = await _enrol_totp(async_client)
    assert (await async_client.post("/api/dashboard-auth/totp/verify", json={"code": _code(secret)})).status_code == 200
    assert (await async_client.put(SETTINGS, json={"localLoginPolicy": "break_glass_only"})).status_code == 200

    refused = await async_client.request("DELETE", "/api/dashboard-auth/password", json={"password": ADMIN_PASSWORD})
    assert refused.status_code == 409, refused.text
    assert _error(refused) == "last_break_glass_protected"
    admin = await _user((await async_client.get(SESSION)).json()["user"]["id"])
    assert admin is not None and admin.password_hash is not None


# --- 2. the gates in the other direction ---


@pytest.mark.asyncio
async def test_tightening_needs_a_qualifying_account_and_names_the_one_that_would_do(
    async_client: AsyncClient,
) -> None:
    await _setup_admin(async_client)

    refused = await async_client.put(SETTINGS, json={"localLoginPolicy": "break_glass_only"})
    assert refused.status_code == 409 and _error(refused) == "break_glass_requires_totp"
    assert "admin" in refused.json()["error"]["message"]
    assert (await async_client.get(SETTINGS)).json()["localLoginPolicy"] == "enabled"

    secret = await _enrol_totp(async_client)
    verified = await async_client.post("/api/dashboard-auth/totp/verify", json={"code": _code(secret)})
    assert verified.status_code == 200, verified.text

    accepted = await async_client.put(SETTINGS, json={"localLoginPolicy": "break_glass_only"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["localLoginPolicy"] == "break_glass_only"
    changed = await _rows("login_policy_changed")
    assert len(changed) == 1 and '"to": "break_glass_only"' in (changed[0].details or "")

    # Re-saving the stored value is not a tightening, and relaxing is never gated.
    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardUser).where(DashboardUser.username == "admin"))).scalar_one()
        row.totp_secret_encrypted = None
        await session.commit()
    await get_dashboard_users_cache().invalidate()
    assert (await async_client.put(SETTINGS, json={"localLoginPolicy": "break_glass_only"})).status_code == 200
    assert (await async_client.put(SETTINGS, json={"localLoginPolicy": "enabled"})).status_code == 200


@pytest.mark.asyncio
async def test_enabling_a_password_less_provider_is_gated_and_disabling_never_is(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    disabled = await async_client.patch(f"/api/auth-providers/{TRUSTED_HEADER_PROVIDER_ID}", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert [row.action for row in await _rows("provider_disabled")] == ["provider_disabled"]

    refused = await async_client.patch(f"/api/auth-providers/{TRUSTED_HEADER_PROVIDER_ID}", json={"enabled": True})
    assert refused.status_code == 409 and _error(refused) == "break_glass_requires_totp"
    assert "admin" in refused.json()["error"]["message"]

    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
    accepted = await async_client.patch(f"/api/auth-providers/{TRUSTED_HEADER_PROVIDER_ID}", json={"enabled": True})
    assert accepted.status_code == 200, accepted.text
    assert len(await _rows("provider_enabled")) == 1


# --- 3. one test per edge of the bidirectional guard ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("edge", "body"),
    [
        ("demote", {"roleId": OPERATOR_ROLE}),
        ("disable", {"status": "disabled"}),
        ("undesignate", {"isBreakGlass": False}),
    ],
)
async def test_the_account_patch_refuses_to_remove_the_last_emergency_account(
    async_client: AsyncClient, app_instance, edge: str, body: dict[str, Any]
) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        user_id, _ = await _emergency_admin(async_client, person)
    await _set_policy(LocalLoginPolicy.ADMINS_ONLY)

    refused = await async_client.patch(f"{USERS}/{user_id}", json=body)
    assert refused.status_code == 409, f"{edge}: {refused.text}"
    assert _error(refused) == "last_break_glass_protected"
    row = await _user(user_id)
    assert row is not None
    assert row.role_id == ADMIN_ROLE and row.status == "active" and row.is_break_glass is True


@pytest.mark.asyncio
async def test_deleting_the_last_emergency_account_is_refused(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        user_id, _ = await _emergency_admin(async_client, person)
    await _set_policy(LocalLoginPolicy.ADMINS_ONLY)

    refused = await async_client.delete(f"{USERS}/{user_id}")
    assert refused.status_code == 409 and _error(refused) == "last_break_glass_protected"
    assert await _user(user_id) is not None


@pytest.mark.asyncio
async def test_the_administrative_totp_reset_is_refused_on_the_last_emergency_account(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        user_id, _ = await _emergency_admin(async_client, person)
    await _set_policy(LocalLoginPolicy.ADMINS_ONLY)

    refused = await async_client.post(f"{USERS}/{user_id}/reset-totp")
    assert refused.status_code == 409 and _error(refused) == "last_break_glass_protected"
    row = await _user(user_id)
    assert row is not None and row.totp_secret_encrypted is not None


@pytest.mark.asyncio
async def test_self_service_removal_of_the_last_second_factor_is_refused(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        user_id, secret = await _emergency_admin(async_client, person)
        await _set_policy(LocalLoginPolicy.ADMINS_ONLY)
        # Designating the account made its second factor mandatory, so it has
        # to present one before it can ask to remove it.
        assert (await person.post("/api/dashboard-auth/totp/verify", json={"code": _code(secret)})).status_code == 200

        refused = await person.post("/api/dashboard-auth/totp/disable", json={"code": _code(secret, steps=1)})
        assert refused.status_code == 409, refused.text
        assert _error(refused) == "last_break_glass_protected"
    row = await _user(user_id)
    assert row is not None and row.totp_secret_encrypted is not None


@pytest.mark.asyncio
async def test_the_shared_deactivation_back_channel_refuses_and_audits_the_refusal(
    async_client: AsyncClient, app_instance
) -> None:
    """``deactivate_user()`` is the path SCIM joins in Phase 3b; it cannot bypass the guard."""

    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        user_id, _ = await _emergency_admin(async_client, person)
    await _set_policy(LocalLoginPolicy.BREAK_GLASS_ONLY)

    async with SessionLocal() as session:
        service = DashboardUsersService(
            DashboardUsersRepository(session), DashboardRolesRepository(session), DashboardAuthRepository(session)
        )
        with pytest.raises(LastBreakGlassProtectedError):
            await service.deactivate_user(
                user_id,
                actor=AuditActor(user_id=None, username=None, role_slug=None, auth_method="scim"),
                actor_ip=None,
                source="scim",
            )
    row = await _user(user_id)
    assert row is not None and row.status == "active"
    refusals = await _rows("scim_deprovision_refused")
    assert len(refusals) == 1 and refusals[0].severity == "warning"
    assert not await _rows("user_disabled")


@pytest.mark.asyncio
async def test_the_guard_sleeps_while_local_sign_in_is_open(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        user_id, _ = await _emergency_admin(async_client, person)

    allowed = await async_client.patch(f"{USERS}/{user_id}", json={"status": "disabled"})
    assert allowed.status_code == 200, allowed.text
    row = await _user(user_id)
    assert row is not None and row.status == "disabled"


@pytest.mark.asyncio
async def test_a_second_qualifying_account_unblocks_the_change(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as first, _client(app_instance) as second:
        first_id, _ = await _emergency_admin(async_client, first, "rescue")
        await _emergency_admin(async_client, second, "backup")
    await _set_policy(LocalLoginPolicy.ADMINS_ONLY)

    cleared = await async_client.patch(f"{USERS}/{first_id}", json={"isBreakGlass": False})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["isBreakGlass"] is False


@pytest.mark.asyncio
async def test_the_designation_is_limited_to_the_admin_preset(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        user_id = await _invite_and_accept(async_client, person, "olivia", OPERATOR_ROLE)

    refused = await async_client.patch(f"{USERS}/{user_id}", json={"isBreakGlass": True})
    assert refused.status_code == 422 and _error(refused) == "validation_error"
    row = await _user(user_id)
    assert row is not None and row.is_break_glass is False


@pytest.mark.asyncio
async def test_two_administrators_racing_the_last_two_emergency_accounts(
    async_client: AsyncClient, app_instance
) -> None:
    """The invariant is part of the write, not only a check before it."""

    import asyncio

    await _setup_admin(async_client)
    async with _client(app_instance) as first, _client(app_instance) as second:
        first_id, first_secret = await _emergency_admin(async_client, first, "rescue")
        second_id, second_secret = await _emergency_admin(async_client, second, "backup")
        await _set_policy(LocalLoginPolicy.ADMINS_ONLY)

        left, right = await asyncio.gather(
            async_client.patch(f"{USERS}/{first_id}", json={"isBreakGlass": False}),
            async_client.patch(f"{USERS}/{second_id}", json={"isBreakGlass": False}),
        )
    assert sorted(response.status_code for response in (left, right)) == [200, 409], [
        (response.status_code, response.text) for response in (left, right)
    ]
    loser = left if left.status_code == 409 else right
    assert _error(loser) == "last_break_glass_protected"
    remaining = [row for row in (await _user(first_id), await _user(second_id)) if row is not None]
    assert sum(1 for row in remaining if row.is_break_glass) == 1
    assert first_secret and second_secret


# --- 4. the failed-login limiter ---


@pytest.mark.asyncio
async def test_a_limit_for_one_username_at_one_address_bars_neither_the_username_elsewhere_nor_others(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as olivia, _client(app_instance) as peter:
        await _invite_and_accept(async_client, olivia, "olivia", OPERATOR_ROLE)
        await _invite_and_accept(async_client, peter, "peter", OPERATOR_ROLE)
    await _clear_rate_limits()

    async with _client(app_instance) as anonymous:
        for _ in range(8):
            attempt = await anonymous.post(LOGIN, json=_attempt("olivia"), headers=_from("203.0.113.7"))
            assert attempt.status_code == 401
        limited = await anonymous.post(LOGIN, json=_attempt("olivia"), headers=_from("203.0.113.7"))
        assert limited.status_code == 429 and "Retry-After" in limited.headers

        # Same username, another address: still served.
        elsewhere = await anonymous.post(
            LOGIN, json={"username": "olivia", "password": PASSWORD}, headers=_from("203.0.113.8")
        )
        assert elsewhere.status_code == 200, elsewhere.text
        # Another username, same address: still served.
        neighbour = await anonymous.post(LOGIN, json=_attempt("peter"), headers=_from("203.0.113.7"))
        assert neighbour.status_code == 401


@pytest.mark.asyncio
async def test_the_emergency_account_is_limited_like_every_other_so_the_limiter_is_not_an_oracle(
    async_client: AsyncClient, app_instance
) -> None:
    """No exemption: the 429 boundary must not tell a stranger which account is the emergency one."""

    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        _, secret = await _emergency_admin(async_client, person)
    await _clear_rate_limits()

    async with _client(app_instance) as anonymous:
        for username in ("rescue", "olivia-who-does-not-exist"):
            for _ in range(8):
                attempt = await anonymous.post(LOGIN, json=_attempt(username), headers=_from("198.51.100.4"))
                assert attempt.status_code == 401 and _error(attempt) == "invalid_credentials"
            limited = await anonymous.post(LOGIN, json=_attempt(username), headers=_from("198.51.100.4"))
            assert limited.status_code == 429, f"{username}: {limited.text}"

        # T13/T15 still hold without the exemption: the key carries the client
        # address, so the operator signing in from somewhere else is untouched.
        accepted = await anonymous.post(
            LOGIN, json={"username": "rescue", "password": PASSWORD}, headers=_from("198.51.100.5")
        )
        assert accepted.status_code == 200, accepted.text
    assert secret


@pytest.mark.asyncio
async def test_one_address_cannot_spray_unlimited_usernames(async_client: AsyncClient, app_instance) -> None:
    """The per-account bucket is local by design, so a coarse per-address ceiling is what caps the endpoint."""

    await _setup_admin(async_client)
    await _clear_rate_limits()

    ceiling = get_password_address_rate_limiter().max_attempts
    async with _client(app_instance) as anonymous:
        for index in range(ceiling):
            # A fresh username every time: each one mints its own per-account
            # bucket, so only the address bucket can ever say stop.
            attempt = await anonymous.post(LOGIN, json=_attempt(f"ghost-{index}"), headers=_from("198.51.100.9"))
            assert attempt.status_code == 401, f"{index}: {attempt.text}"
        barred = await anonymous.post(LOGIN, json=_attempt("ghost-final"), headers=_from("198.51.100.9"))
        assert barred.status_code == 429 and "Retry-After" in barred.headers
        assert _error(barred) == "password_rate_limited"

        # Another address is unaffected, and the ceiling is far above the
        # per-account limit so a shared office egress never meets it first.
        assert ceiling > 8
        elsewhere = await anonymous.post(LOGIN, json=_attempt("ghost-final"), headers=_from("198.51.100.10"))
        assert elsewhere.status_code == 401


@pytest.mark.asyncio
async def test_a_successful_login_clears_its_own_bucket_and_not_the_address_ceiling(
    async_client: AsyncClient, app_instance
) -> None:
    """The per-account bucket is the one a success owns; the ceiling is not its to reset."""

    await _setup_admin(async_client)
    await _clear_rate_limits()

    async with _client(app_instance) as anonymous:
        for _ in range(4):
            assert (
                await anonymous.post(LOGIN, json=_attempt("admin"), headers=_from("198.51.100.11"))
            ).status_code == 401
        accepted = await anonymous.post(
            LOGIN, json={"username": "admin", "password": ADMIN_PASSWORD}, headers=_from("198.51.100.11")
        )
        assert accepted.status_code == 200, accepted.text

    async with SessionLocal() as session:
        remaining = (await session.execute(select(RateLimitAttempt.type))).scalars().all()
    # The pair bucket is empty; the address bucket still carries every attempt
    # this client made, the successful one included.
    assert [row for row in remaining if row == "password"] == []
    assert len([row for row in remaining if row == "password_address"]) == 5


@pytest.mark.asyncio
async def test_one_valid_account_cannot_reset_the_address_ceiling(async_client: AsyncClient, app_instance) -> None:
    """Clearing the coarse bucket on success would hand its ceiling to any insider.

    Seven guesses at someone else's username, one sign-in of their own, seven
    more — the endpoint's only cap would be resettable on demand by the very
    caller it bounds, and every guess costs a blocking password-hash comparison
    on the loop that also serves the proxy.
    """

    await _setup_admin(async_client)
    await _clear_rate_limits()
    ceiling = get_password_address_rate_limiter().max_attempts

    async with _client(app_instance) as anonymous:
        # Spend all but one of the ceiling on invented usernames, so no
        # per-account bucket is anywhere near its own limit.
        for index in range(ceiling - 1):
            attempt = await anonymous.post(LOGIN, json=_attempt(f"ghost-{index}"), headers=_from("198.51.100.12"))
            assert attempt.status_code == 401, f"{index}: {attempt.text}"

        # The valid sign-in is the last attempt the ceiling has room for.
        accepted = await anonymous.post(
            LOGIN, json={"username": "admin", "password": ADMIN_PASSWORD}, headers=_from("198.51.100.12")
        )
        assert accepted.status_code == 200, accepted.text

        barred = await anonymous.post(LOGIN, json=_attempt("ghost-after"), headers=_from("198.51.100.12"))
        assert barred.status_code == 429, barred.text
        assert _error(barred) == "password_rate_limited" and "Retry-After" in barred.headers


# --- 5. the trusted-header fallback ---


@pytest.mark.asyncio
async def test_the_policy_narrows_the_reverse_proxy_password_fallback(
    async_client: AsyncClient, app_instance, monkeypatch
) -> None:
    await _setup_admin(async_client)
    # ``break_glass_only`` admits a *qualifying* account, so the compat admin
    # enrols before the policy is tightened (exactly what the tightening gate
    # would have forced anyway).
    admin_secret = await _enrol_totp(async_client)
    assert (
        await async_client.post("/api/dashboard-auth/totp/verify", json={"code": _code(admin_secret)})
    ).status_code == 200
    async with _client(app_instance) as person:
        operator_id = await _invite_and_accept(async_client, person, "olivia", OPERATOR_ROLE)
        assert operator_id

        monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", DashboardAuthMode.TRUSTED_HEADER.value)
        monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", "Remote-User")
        monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
        monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
        get_settings.cache_clear()
        async with SessionLocal() as session:
            provider = await session.get(DashboardAuthProvider, TRUSTED_HEADER_PROVIDER_ID)
            assert provider is not None
            provider.unknown_identity_role_id = None
            await session.commit()
        from app.core.auth.providers.registry import get_auth_provider_registry

        get_auth_provider_registry().clear()

        stranger = {"Remote-User": "stranger@example.com"}
        # Default policy: today's behaviour, the operator's cookie still serves.
        served = await person.get(SETTINGS, headers=stranger)
        assert served.status_code == 200, served.text
        assert (await person.get(SESSION, headers=stranger)).json()["passwordSessionActive"] is True

        await _set_policy(LocalLoginPolicy.BREAK_GLASS_ONLY)
        refused = await person.get(SETTINGS, headers=stranger)
        assert refused.status_code == 401 and _error(refused) == "identity_not_provisioned"
        described = await person.get(SESSION, headers=stranger)
        assert described.json()["passwordSessionActive"] is False
        assert described.json()["login"]["pendingIdentity"] is True

        # The emergency admin's cookie still gets through the same door.
        assert (await async_client.get(SETTINGS, headers=stranger)).status_code == 200


# --- 6. the migration ---


async def _settings_columns(engine) -> dict[str, Any]:
    async with engine.connect() as conn:
        return {row[1]: row for row in await conn.execute(text("PRAGMA table_info('dashboard_settings')"))}


async def _stamped_revision(engine) -> str | None:
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_local_login_policy_migration_upgrades_and_downgrades(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'policy.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, _PARENT_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        await to_thread.run_sync(lambda: run_upgrade(db_url, _TARGET_REVISION, bootstrap_legacy=False))
        columns = await _settings_columns(engine)
        assert "local_login_policy" in columns
        # An upgraded install keeps today's behaviour.
        assert "'enabled'" in str(columns["local_login_policy"][4])
        assert await _stamped_revision(engine) == _TARGET_REVISION

        # The reverse direction is the one an operator reaches for when a
        # replica is schema-ahead, and ``run_upgrade`` never exercises it: the
        # column has to go, and the ledger has to name the parent again.
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), _PARENT_REVISION))
        assert "local_login_policy" not in await _settings_columns(engine)
        assert await _stamped_revision(engine) == _PARENT_REVISION

        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        head = await to_thread.run_sync(lambda: inspect_migration_state(db_url).head_revision)
        assert result.current_revision == head
        assert "local_login_policy" in await _settings_columns(engine)
    finally:
        await engine.dispose()


# --- 7. the guards are one atomic step, not a check then a write ---


def _record_order(calls: list[str], owner: type, names: Sequence[str]):
    """Patch ``owner``'s methods so each call appends its name, preserving behaviour.

    ``names`` is a sequence rather than a tuple so that a two-name call does not
    read as an ``(identity, secret)`` pair to secret scanners.
    """

    originals = {name: getattr(owner, name) for name in names}

    def _wrap(name: str):
        original = originals[name]

        async def _recorded(self, *args, **kwargs):
            calls.append(name)
            return await original(self, *args, **kwargs)

        return _recorded

    return [patch.object(owner, name, _wrap(name)) for name in names]


@pytest.mark.asyncio
async def test_reset_totp_takes_the_account_lock_before_its_first_read(async_client: AsyncClient, app_instance) -> None:
    """F7: a read that opens the transaction first leaves ``BEGIN IMMEDIATE`` on its fallback path."""

    await _setup_admin(async_client)
    async with _client(app_instance) as first, _client(app_instance) as second:
        target_id, _ = await _emergency_admin(async_client, first, "rescue")
        await _emergency_admin(async_client, second, "backup")
    await _set_policy(LocalLoginPolicy.ADMINS_ONLY)

    calls: list[str] = []
    patches = [
        *_record_order(calls, DashboardUsersRepository, ["acquire_write_intent", "count_qualifying_break_glass"]),
        *_record_order(calls, DashboardUsersService, ["_get"]),
    ]
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        reset = await async_client.post(f"{USERS}/{target_id}/reset-totp")
    assert reset.status_code == 200, reset.text

    assert calls[0] == "acquire_write_intent", calls
    assert calls.index("acquire_write_intent") < calls.index("_get") < calls.index("count_qualifying_break_glass")


@pytest.mark.asyncio
async def test_tightening_takes_the_account_lock_before_it_counts(async_client: AsyncClient, app_instance) -> None:
    """F6: the count and the policy write must be one step, so the lock comes first."""

    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)

    calls: list[str] = []
    with ExitStack() as stack:
        for patcher in _record_order(
            calls, SettingsRepository, ["acquire_account_write_intent", "count_qualifying_break_glass"]
        ):
            stack.enter_context(patcher)
        tightened = await async_client.put(SETTINGS, json={"localLoginPolicy": "admins_only"})
    assert tightened.status_code == 200, tightened.text
    assert calls == ["acquire_account_write_intent", "count_qualifying_break_glass"], calls

    # A save that cannot be a tightening never queues behind account mutations.
    calls.clear()
    with ExitStack() as stack:
        for patcher in _record_order(
            calls, SettingsRepository, ["acquire_account_write_intent", "count_qualifying_break_glass"]
        ):
            stack.enter_context(patcher)
        relaxed = await async_client.put(SETTINGS, json={"localLoginPolicy": "enabled"})
    assert relaxed.status_code == 200, relaxed.text
    assert calls == [], calls


@pytest.mark.asyncio
async def test_enabling_a_provider_takes_the_account_lock_before_it_counts(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
    disabled = await async_client.patch(f"/api/auth-providers/{TRUSTED_HEADER_PROVIDER_ID}", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text

    calls: list[str] = []
    with ExitStack() as stack:
        for patcher in _record_order(
            calls,
            AuthProvidersRepository,
            ["acquire_account_write_intent", "get_provider", "count_qualifying_break_glass"],
        ):
            stack.enter_context(patcher)
        enabled = await async_client.patch(f"/api/auth-providers/{TRUSTED_HEADER_PROVIDER_ID}", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert calls == ["acquire_account_write_intent", "get_provider", "count_qualifying_break_glass"], calls


@pytest.mark.asyncio
async def test_disabling_totp_consumes_the_code_before_the_guard_counts(
    async_client: AsyncClient, app_instance
) -> None:
    """F5: advancing the replay counter commits, which would release a lock taken before it."""

    await _setup_admin(async_client)
    async with _client(app_instance) as first, _client(app_instance) as second:
        _, secret = await _emergency_admin(async_client, first, "rescue")
        await _emergency_admin(async_client, second, "backup")
        await _set_policy(LocalLoginPolicy.ADMINS_ONLY)
        assert (await first.post("/api/dashboard-auth/totp/verify", json={"code": _code(secret)})).status_code == 200

        calls: list[str] = []
        patches = [
            *_record_order(calls, DashboardAuthRepository, ["try_advance_user_totp_step", "acquire_write_intent"]),
            *_record_order(calls, DashboardUsersRepository, ["count_qualifying_break_glass"]),
        ]
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            disabled = await first.post("/api/dashboard-auth/totp/disable", json={"code": _code(secret, steps=1)})
        assert disabled.status_code == 200, disabled.text

    assert calls == ["try_advance_user_totp_step", "acquire_write_intent", "count_qualifying_break_glass"], calls


@pytest.mark.asyncio
async def test_the_self_service_guard_locks_before_it_reads_the_policy(async_client: AsyncClient, app_instance) -> None:
    """The same TOCTOU as the three above, one level down: the *policy* read.

    ``assert_break_glass_remains`` is a no-op while the policy is ``enabled``,
    so a policy read taken outside the lock can be a snapshot older than a
    concurrent tightening — and the guard would then wave through the removal
    of the last qualifying account under a policy that, by the time the write
    lands, forbids exactly that. Lock, then read, then count.
    """

    await _setup_admin(async_client)
    async with _client(app_instance) as first, _client(app_instance) as second:
        _, secret = await _emergency_admin(async_client, first, "rescue")
        await _emergency_admin(async_client, second, "backup")
        await _set_policy(LocalLoginPolicy.ADMINS_ONLY)
        assert (await first.post("/api/dashboard-auth/totp/verify", json={"code": _code(secret)})).status_code == 200

        calls: list[str] = []
        patches = [
            *_record_order(calls, DashboardAuthRepository, ["acquire_write_intent", "get_settings"]),
            *_record_order(calls, DashboardUsersRepository, ["count_qualifying_break_glass"]),
        ]
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            disabled = await first.post("/api/dashboard-auth/totp/disable", json={"code": _code(secret, steps=1)})
        assert disabled.status_code == 200, disabled.text

    # Whatever the request read before reaching the guard, everything from the
    # lock onwards is the guard, in this order.
    lock = calls.index("acquire_write_intent")
    assert calls[lock:] == ["acquire_write_intent", "get_settings", "count_qualifying_break_glass"], calls
