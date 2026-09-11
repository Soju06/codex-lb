"""The host recovery commands against the running product (PLAN §4.6, docs/sso.md).

The unit suite proves what each command writes. This one proves the thing an
operator actually cares about at three in the morning: after the command runs,
the sign-in route that was refusing them lets them back in — same database,
same routes, no restart.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pyotp
import pytest
from anyio import to_thread
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app import cli
from app.admin_cli import BREAK_GLASS_CLI_ACTION
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.config.settings_cache import get_settings_cache
from app.db.models import AuditLog, DashboardSettings, DashboardUser, LocalLoginPolicy
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

USERS = "/api/dashboard-users"
SESSION = "/api/dashboard-auth/session"
LOGIN = "/api/dashboard-auth/password/login"
ADMIN_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
OPERATOR_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]
ADMIN_PASSWORD = "password" + "123"
PASSWORD = "invited-" + "password-1"
RECOVERED_PASSWORD = "recovered-" + "password-1"


@asynccontextmanager
async def _client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


async def _invite_and_accept(admin: AsyncClient, person: AsyncClient, username: str, role_id: str) -> str:
    created = await admin.post(USERS, json={"username": username, "roleId": role_id})
    assert created.status_code == 201, created.text
    accepted = await person.post(
        "/api/dashboard-auth/invite/accept", json={"token": created.json()["invite"]["token"], "password": PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text
    return created.json()["user"]["id"]


async def _set_policy(policy: LocalLoginPolicy) -> None:
    async with SessionLocal() as session:
        (await session.execute(select(DashboardSettings))).scalar_one().local_login_policy = policy.value
        await session.commit()
    await _converge()


async def _converge() -> None:
    """Stand in for the five-second cache TTL that carries a host write into a running server."""

    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


async def _run_cli(*argv: str) -> None:
    """The command is synchronous host tooling; run it off the loop, as the operator's shell would."""

    await to_thread.run_sync(lambda: cli.main(list(argv)))
    await _converge()


async def _session_generation(user_id: str) -> int:
    async with SessionLocal() as session:
        user = await session.get(DashboardUser, user_id)
        assert user is not None
        return user.session_generation


async def _cli_audit_rows() -> list[AuditLog]:
    async with SessionLocal() as session:
        rows = await session.execute(select(AuditLog).where(AuditLog.action == BREAK_GLASS_CLI_ACTION))
        return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_local_login_enable_lets_a_refused_account_sign_in_again(async_client: AsyncClient, app_instance) -> None:
    setup = await async_client.post("/api/dashboard-auth/password/setup", json={"password": ADMIN_PASSWORD})
    assert setup.status_code == 200, setup.text
    async with _client(app_instance) as person:
        await _invite_and_accept(async_client, person, "olivia", OPERATOR_ROLE)
    await _set_policy(LocalLoginPolicy.BREAK_GLASS_ONLY)

    async with _client(app_instance) as refused_client:
        refused = await refused_client.post(LOGIN, json={"username": "olivia", "password": PASSWORD})
        assert refused.status_code == 401

    await _run_cli("admin", "local-login", "enable")

    async with _client(app_instance) as recovered_client:
        recovered = await recovered_client.post(LOGIN, json={"username": "olivia", "password": PASSWORD})
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["authenticated"] is True
    assert (await _cli_audit_rows())[0].severity == "critical"


@pytest.mark.asyncio
async def test_reset_password_lets_the_account_sign_in_afterwards(
    async_client: AsyncClient, app_instance, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await async_client.post("/api/dashboard-auth/password/setup", json={"password": ADMIN_PASSWORD})
    assert setup.status_code == 200, setup.text
    async with _client(app_instance) as person:
        user_id = await _invite_and_accept(async_client, person, "rescue", ADMIN_ROLE)
        assert (await person.get(SESSION)).json()["authenticated"] is True

        generation_before = await _session_generation(user_id)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        replies = iter((RECOVERED_PASSWORD, RECOVERED_PASSWORD))
        monkeypatch.setattr(getpass, "getpass", lambda _prompt: next(replies))
        await _run_cli("admin", "reset-password", "rescue")

        # The session that existed while the account was lost is gone with the credential.
        assert (await person.get(SESSION)).json()["authenticated"] is False

    async with _client(app_instance) as returning:
        stale = await returning.post(LOGIN, json={"username": "rescue", "password": PASSWORD})
        assert stale.status_code == 401
        signed_in = await returning.post(LOGIN, json={"username": "rescue", "password": RECOVERED_PASSWORD})
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["authenticated"] is True
        assert signed_in.json()["user"]["id"] == user_id

    (row,) = await _cli_audit_rows()
    assert row.severity == "critical" and row.auth_method == "cli"
    assert row.target_type == "user" and row.target_id == user_id
    assert row.actor_username is None
    assert await _session_generation(user_id) == generation_before + 1


async def _enrol_totp(client: AsyncClient) -> str:
    started = await client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = await client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": secret, "code": pyotp.TOTP(secret).now()},
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret


async def _emergency_admin(admin: AsyncClient, person: AsyncClient, username: str) -> str:
    """A qualifying emergency account: admin preset, a password, a second factor, designated."""

    user_id = await _invite_and_accept(admin, person, username, ADMIN_ROLE)
    await _enrol_totp(person)
    designated = await admin.patch(f"{USERS}/{user_id}", json={"isBreakGlass": True})
    assert designated.status_code == 200, designated.text
    return user_id


async def _stored_policy() -> str:
    async with SessionLocal() as session:
        return (await session.execute(select(DashboardSettings))).scalar_one().local_login_policy


def _answer_password_prompt(monkeypatch: pytest.MonkeyPatch, password: str) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    replies = iter((password, password))
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: next(replies))


@pytest.mark.asyncio
async def test_clearing_the_last_second_factor_re_opens_local_sign_in(
    async_client: AsyncClient, app_instance, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `--clear-two-factor` lockout: the command must not strand the operator it is rescuing.

    Under ``break_glass_only`` only a *qualifying* account is admitted, and
    clearing the secret is exactly what stops this one qualifying — so without
    the re-open the new password would meet a form that refuses it, on an
    install whose only other admin is not admitted either.
    """

    setup = await async_client.post("/api/dashboard-auth/password/setup", json={"password": ADMIN_PASSWORD})
    assert setup.status_code == 200, setup.text
    async with _client(app_instance) as person:
        user_id = await _emergency_admin(async_client, person, "rescue")
    await _set_policy(LocalLoginPolicy.BREAK_GLASS_ONLY)

    _answer_password_prompt(monkeypatch, RECOVERED_PASSWORD)
    await _run_cli("admin", "reset-password", "rescue", "--clear-two-factor")

    assert await _stored_policy() == LocalLoginPolicy.ENABLED.value
    async with _client(app_instance) as returning:
        signed_in = await returning.post(LOGIN, json={"username": "rescue", "password": RECOVERED_PASSWORD})
        assert signed_in.status_code == 200, signed_in.text
        # No secret left to present, so the always-two-factor rule does not bite.
        assert signed_in.json()["authenticated"] is True
        assert signed_in.json()["user"]["id"] == user_id

    (row,) = await _cli_audit_rows()
    assert row.details is not None
    assert LocalLoginPolicy.BREAK_GLASS_ONLY.value in row.details


@pytest.mark.asyncio
async def test_a_policy_that_still_has_a_way_in_is_left_alone(
    async_client: AsyncClient, app_instance, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lockout repair, not a way to relax a policy: a second qualifying admin keeps the door shut."""

    setup = await async_client.post("/api/dashboard-auth/password/setup", json={"password": ADMIN_PASSWORD})
    assert setup.status_code == 200, setup.text
    async with _client(app_instance) as first, _client(app_instance) as second:
        await _emergency_admin(async_client, first, "rescue")
        await _emergency_admin(async_client, second, "backup")
    await _set_policy(LocalLoginPolicy.BREAK_GLASS_ONLY)

    _answer_password_prompt(monkeypatch, RECOVERED_PASSWORD)
    await _run_cli("admin", "reset-password", "rescue", "--clear-two-factor")

    assert await _stored_policy() == LocalLoginPolicy.BREAK_GLASS_ONLY.value
    async with _client(app_instance) as returning:
        # The account it just reset is no longer qualifying, so the policy it
        # left alone still refuses it -- byte-identical to a wrong password.
        refused = await returning.post(LOGIN, json={"username": "rescue", "password": RECOVERED_PASSWORD})
        assert refused.status_code == 401
