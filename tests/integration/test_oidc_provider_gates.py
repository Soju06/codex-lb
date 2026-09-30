"""The two gates an OIDC install passes through, and the secret that never comes back.

Three things are proven here and nowhere else:

* the connection settings are written typed, stored sealed, and read back with
  the client secret masked — and that secret reaches no response, no audit row
  and no log line;
* turning the provider on needs the acting admin's own test sign-in from the
  last ten minutes, beside the qualifying-break-glass gate that was already
  there;
* an account whose only credential is its identity provider can step up by
  re-authenticating there, and an account that holds a real factor cannot use
  that shortcut.

The identity provider is the same offline fake the flow suite uses.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import app.modules.dashboard_users.identity_resolver as identity_resolver
from app.core.audit.service import drain_audit_log_tasks
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.auth.providers.registry import get_auth_provider_registry
from app.core.auth.step_up import STEP_UP_COOKIE, STEP_UP_MAX_AGE_SECONDS
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.utils.time import utcnow
from app.db.models import (
    AuditLog,
    AuthProviderKind,
    DashboardAuthProvider,
    DashboardIdentity,
    DashboardRoleGrant,
    DashboardRoleRecord,
    DashboardUser,
    RateLimitAttempt,
)
from app.db.session import SessionLocal
from app.modules.auth_providers.config import build_oidc_config, seal_oidc_config
from app.modules.auth_providers.repository import AuthProvidersRepository
from app.modules.auth_providers.schemas import AuthProviderUpdateRequest
from app.modules.auth_providers.seed import auth_provider_id
from app.modules.auth_providers.service import _ROLE_FIELDS, ADMIN_BOUND_FIELDS
from app.modules.dashboard_auth.oidc_api import OIDC_FAILURE_PATH, OIDC_SETTINGS_PATH
from app.modules.dashboard_auth.oidc_flows import OIDC_FLOW_COOKIE
from tests.fixtures.fake_idp import (
    ABSENT,
    CLIENT_ID,
    CLIENT_SECRET,
    DISCOVERY_URL,
    ISSUER,
    REDIRECT_URI,
    FakeIdp,
    json_response,
    provider_config_document,
)

pytestmark = pytest.mark.integration

PROVIDERS = "/api/auth-providers"
SESSION = "/api/dashboard-auth/session"
CALLBACK = "/api/dashboard-auth/oidc/callback"
LOGIN_START = "/api/dashboard-auth/oidc/login/start"
TEST_LOGIN_START = "/api/dashboard-auth/oidc/test-login/start"
STEP_UP_START = "/api/dashboard-auth/oidc/step-up/start"
STEP_UP = "/api/dashboard-auth/step-up"

OIDC_PROVIDER_ID = auth_provider_id(AuthProviderKind.OIDC)
PASSWORD_PROVIDER_ID = auth_provider_id(AuthProviderKind.PASSWORD)
TRUSTED_HEADER_PROVIDER_ID = auth_provider_id(AuthProviderKind.TRUSTED_HEADER)
ADMIN_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
VIEWER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]

ADMIN_PASSWORD = "password" + "123"
INVITED_PASSWORD = "invited-" + "password-1"
AUTHORIZATION_CODE = "authorization-" + "code-value"
SUBJECT = "alice-subject"


# --- helpers ---


@pytest.fixture(autouse=True)
def _isolate_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> FakeIdp:
    return FakeIdp().install(monkeypatch)


@asynccontextmanager
async def _client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


def _error(response) -> str:
    return response.json()["error"]["code"]


def _code(secret: str, *, steps: int = 0) -> str:
    """A TOTP code, optionally from a later step: a consumed code cannot be replayed."""

    return pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=30 * steps))


async def _rows(action: str) -> list[AuditLog]:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _all_audit_details() -> str:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    return json.dumps([row.details for row in rows])


async def _converge_caches() -> None:
    get_auth_provider_registry().clear()
    identity_resolver.get_identity_resolution_cache().clear()
    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


async def _clear_rate_limits() -> None:
    async with SessionLocal() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()


async def _provider_row() -> DashboardAuthProvider:
    async with SessionLocal() as session:
        row = await session.get(DashboardAuthProvider, OIDC_PROVIDER_ID)
    assert row is not None, "the migration and the test schema both seed an oidc/default row"
    return row


async def _setup_admin(client: AsyncClient) -> str:
    response = await client.post("/api/dashboard-auth/password/setup", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["user"]["id"]


async def _emergency_admin(admin: AsyncClient, person: AsyncClient, username: str = "rescue") -> str:
    """A second admin with a second factor, designated as the emergency account.

    Every ``enabled: true`` on a password-less provider needs one; without it
    the break-glass gate answers first and the test-login gate is never
    reached.
    """

    created = await admin.post("/api/dashboard-users", json={"username": username, "roleId": ADMIN_ROLE})
    assert created.status_code == 201, created.text
    accepted = await person.post(
        "/api/dashboard-auth/invite/accept",
        json={"token": created.json()["invite"]["token"], "password": INVITED_PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    started = await person.post("/api/dashboard-auth/totp/setup/start", json={})
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = await person.post(
        "/api/dashboard-auth/totp/setup/confirm", json={"secret": secret, "code": _code(secret)}
    )
    assert confirmed.status_code == 200, confirmed.text
    # Enrolling makes the account's own session incomplete until it presents a
    # code; a later step, because the one just confirmed is spent.
    verified = await person.post("/api/dashboard-auth/totp/verify", json={"code": _code(secret, steps=1)})
    assert verified.status_code == 200, verified.text
    designated = await admin.patch(f"/api/dashboard-users/{created.json()['user']['id']}", json={"isBreakGlass": True})
    assert designated.status_code == 200, designated.text
    return created.json()["user"]["id"]


def _config_body(**overrides: object) -> dict[str, object]:
    """The connect wizard's body: the whole document, in the wire's casing."""

    body: dict[str, object] = {
        "issuer": ISSUER,
        "discoveryUrl": DISCOVERY_URL,
        "clientId": CLIENT_ID,
        "clientSecret": CLIENT_SECRET,
        "redirectUri": REDIRECT_URI,
    }
    body.update(overrides)
    return body


async def _write_config(client: AsyncClient, **overrides: object):
    return await client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"config": _config_body(**overrides)})


async def _store_config_directly(*, enabled: bool, unknown_identity_role_id: str | None = VIEWER_ROLE) -> None:
    """Configure the row without the settings API; the API's own gates are tested separately."""

    sealed = seal_oidc_config(build_oidc_config(provider_config_document()))
    async with SessionLocal() as session:
        row = await session.get(DashboardAuthProvider, OIDC_PROVIDER_ID)
        assert row is not None
        row.enabled = enabled
        row.config_encrypted = sealed
        row.unknown_identity_role_id = unknown_identity_role_id
        row.updated_at = datetime.now(UTC)
        await session.commit()
    await _converge_caches()


def _parameters(authorization_url: str) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(urlsplit(authorization_url).query).items()}


async def _round_trip(
    client: AsyncClient,
    idp: FakeIdp,
    start: str,
    *,
    subject: str = SUBJECT,
    claims: dict[str, Any] | None = None,
):
    """Start a signed-in flow, answer it at the identity provider, and come back."""

    started = await client.post(start)
    assert started.status_code == 200, started.text
    parameters = _parameters(started.json()["authorizationUrl"])
    idp.token_response = json_response(
        {"id_token": idp.id_token(nonce=parameters["nonce"], subject=subject, claims=claims)}
    )
    return await client.get(CALLBACK, params={"state": parameters["state"], "code": AUTHORIZATION_CODE}), parameters


async def _test_login(client: AsyncClient, idp: FakeIdp, *, subject: str = SUBJECT):
    completed, _ = await _round_trip(client, idp, TEST_LOGIN_START, subject=subject)
    return completed


async def _oidc_sign_in(client: AsyncClient, idp: FakeIdp, *, subject: str = SUBJECT):
    started = await client.get(LOGIN_START)
    assert started.status_code == 303, started.text
    parameters = _parameters(started.headers["location"])
    idp.token_response = json_response({"id_token": idp.id_token(nonce=parameters["nonce"], subject=subject)})
    return await client.get(CALLBACK, params={"state": parameters["state"], "code": AUTHORIZATION_CODE})


async def _age_the_proof(minutes: int) -> None:
    async with SessionLocal() as session:
        row = await session.get(DashboardAuthProvider, OIDC_PROVIDER_ID)
        assert row is not None and row.test_login_verified_at is not None
        row.test_login_verified_at = datetime.now(UTC) - timedelta(minutes=minutes)
        await session.commit()


# --- 1. the connection settings ---


@pytest.mark.asyncio
async def test_the_connection_settings_are_written_typed_and_read_back_masked(async_client: AsyncClient) -> None:
    await _setup_admin(async_client)

    written = await _write_config(async_client, groupsClaim="roles")

    assert written.status_code == 200, written.text
    config = written.json()["config"]
    assert config["issuer"] == ISSUER
    assert config["clientId"] == CLIENT_ID
    assert config["redirectUri"] == REDIRECT_URI
    # The claim names are settings, not secrets: an operator has to see them.
    assert config["groupsClaim"] == "roles"
    assert config["subjectClaim"] == "sub" and config["emailClaim"] == "email"
    assert config["clientSecret"] == f"****{CLIENT_SECRET[-4:]}"
    assert CLIENT_SECRET not in json.dumps(written.json())

    listed = await async_client.get(PROVIDERS)
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["kind"] == "oidc")
    assert row["config"] == config
    assert row["enabled"] is False and row["active"] is False
    assert CLIENT_SECRET not in listed.text

    # Sealed at rest: the column holds neither the secret nor the issuer in clear.
    stored = await _provider_row()
    assert stored.config_encrypted is not None
    assert CLIENT_SECRET.encode() not in stored.config_encrypted
    assert ISSUER.encode() not in stored.config_encrypted


@pytest.mark.asyncio
async def test_a_connection_document_is_refused_on_a_row_that_has_no_configuration(
    async_client: AsyncClient,
) -> None:
    await _setup_admin(async_client)

    for provider_id in (PASSWORD_PROVIDER_ID, TRUSTED_HEADER_PROVIDER_ID):
        refused = await async_client.patch(f"{PROVIDERS}/{provider_id}", json={"config": _config_body()})
        assert refused.status_code == 422, refused.text
        assert _error(refused) == "config_not_supported"

    # The reverse-proxy row still *reports* the two header names it reads.
    listed = (await async_client.get(PROVIDERS)).json()
    header_row = next(item for item in listed if item["kind"] == "trusted_header")
    assert header_row["config"] == {"identityHeader": "Remote-User", "groupsHeader": "Remote-Groups"}
    assert next(item for item in listed if item["kind"] == "password")["config"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("issuer", "http://idp.example.test"),
        ("issuer", "https://idp.example.test/?tenant=1"),
        ("discoveryUrl", "https://127.0.0.1/.well-known/openid-configuration"),
        ("discoveryUrl", "https://169.254.169.254/.well-known/openid-configuration"),
        ("redirectUri", "https://dash.example.test/somewhere-else"),
    ],
)
async def test_an_unusable_url_is_refused_by_name_and_nothing_is_stored(
    async_client: AsyncClient, field: str, value: str
) -> None:
    await _setup_admin(async_client)

    refused = await _write_config(async_client, **{field: value})

    assert refused.status_code == 422, refused.text
    assert _error(refused) == "invalid_provider_config"
    assert refused.json()["error"]["param"] is not None
    assert (await _provider_row()).config_encrypted is None


@pytest.mark.asyncio
async def test_a_config_body_is_the_whole_document_or_nothing(async_client: AsyncClient) -> None:
    """Unknown fields, a missing secret and an explicit ``null`` are all refused."""

    await _setup_admin(async_client)

    unknown_field = await _write_config(async_client, identityHeader="Remote-User")
    assert unknown_field.status_code == 422, unknown_field.text

    partial = dict(_config_body())
    del partial["clientSecret"]
    # Inheriting the stored secret across a repoint would send it to a server
    # the operator had not yet typed it for.
    missing_secret = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"config": partial})
    assert missing_secret.status_code == 422, missing_secret.text

    cleared = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"config": None})
    assert cleared.status_code == 422, cleared.text
    assert _error(cleared) == "invalid_provider_config"

    assert (await _provider_row()).config_encrypted is None


@pytest.mark.asyncio
async def test_the_client_secret_reaches_no_response_no_audit_row_and_no_log_line(
    async_client: AsyncClient, app_instance, idp: FakeIdp, caplog: pytest.LogCaptureFixture
) -> None:
    """The one containment test: the whole admin path, then a search for the value."""

    with caplog.at_level(logging.DEBUG):
        await _setup_admin(async_client)
        async with _client(app_instance) as person:
            await _emergency_admin(async_client, person)
        bodies = [(await _write_config(async_client)).text]
        await _test_login(async_client, idp)
        enabled = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True})
        assert enabled.status_code == 200, enabled.text
        bodies += [enabled.text, (await async_client.get(PROVIDERS)).text, (await async_client.get(SESSION)).text]

    assert all(CLIENT_SECRET not in body for body in bodies)
    # The token request carried it; nothing wrote it down.
    assert any("client_secret" in request.data for request in idp.token_requests())
    assert CLIENT_SECRET not in await _all_audit_details()
    assert CLIENT_SECRET not in "\n".join(record.getMessage() for record in caplog.records)
    assert "provider_updated" in [row.action for row in await _rows("provider_updated")]
    # A changed connection is audited as the fact that it changed, never as a value.
    details = json.loads((await _rows("provider_updated"))[0].details or "{}")
    assert details["config"] is True and ISSUER not in json.dumps(details)


# --- 2. the enable gate ---


@pytest.mark.asyncio
async def test_enabling_without_a_test_login_is_refused(async_client: AsyncClient, app_instance, idp: FakeIdp) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
    await _write_config(async_client)

    refused = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True})
    assert refused.status_code == 409 and _error(refused) == "oidc_test_login_required"
    assert (await _provider_row()).enabled is False

    await _test_login(async_client, idp)
    await _age_the_proof(11)

    stale = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True})
    assert stale.status_code == 409 and _error(stale) == "oidc_test_login_required"
    assert (await _provider_row()).enabled is False


@pytest.mark.asyncio
async def test_one_admins_test_login_does_not_arm_anothers_enable(
    async_client: AsyncClient, app_instance, idp: FakeIdp
) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
        await _write_config(async_client)
        # The emergency admin is an admin too, and runs the test sign-in.
        await _test_login(person, idp)

        refused = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True})
        assert refused.status_code == 409 and _error(refused) == "oidc_test_login_required"
        assert (await _provider_row()).enabled is False

        accepted = await person.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True})
    assert accepted.status_code == 200, accepted.text
    assert (await _provider_row()).enabled is True


@pytest.mark.asyncio
async def test_repointing_the_identity_provider_invalidates_the_proof(
    async_client: AsyncClient, app_instance, idp: FakeIdp
) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
    await _write_config(async_client)
    await _test_login(async_client, idp)

    # A label is not a connection: the proof stands.
    labelled = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"label": "Company sign-in"})
    assert labelled.status_code == 200, labelled.text
    assert labelled.json()["testLoginVerifiedAt"] is not None

    # Re-saving the identical document is not a repoint either.
    unchanged = await _write_config(async_client)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["testLoginVerifiedAt"] is not None

    repointed = await _write_config(async_client, clientSecret=CLIENT_SECRET + "-rotated")
    assert repointed.status_code == 200, repointed.text
    assert repointed.json()["testLoginVerifiedAt"] is None

    refused = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True})
    assert refused.status_code == 409 and _error(refused) == "oidc_test_login_required"
    assert (await _provider_row()).enabled is False


@pytest.mark.asyncio
async def test_repointing_and_enabling_in_one_request_is_refused(
    async_client: AsyncClient, app_instance, idp: FakeIdp
) -> None:
    """The connection write lands before the gate reads the proof it just cleared."""

    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
    await _write_config(async_client)
    await _test_login(async_client, idp)

    refused = await async_client.patch(
        f"{PROVIDERS}/{OIDC_PROVIDER_ID}",
        json={"config": _config_body(clientId=CLIENT_ID + "-other"), "enabled": True},
    )

    assert refused.status_code == 409 and _error(refused) == "oidc_test_login_required"
    row = await _provider_row()
    assert row.enabled is False and row.config_encrypted is not None


@pytest.mark.asyncio
async def test_the_proof_is_spent_by_the_enable_it_armed(async_client: AsyncClient, app_instance, idp: FakeIdp) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
    await _write_config(async_client)
    await _test_login(async_client, idp)

    enabled = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True and enabled.json()["testLoginVerifiedAt"] is None

    disabled = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text

    again = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True})
    assert again.status_code == 409 and _error(again) == "oidc_test_login_required"
    assert (await _provider_row()).enabled is False


@pytest.mark.asyncio
async def test_the_break_glass_gate_still_answers_first(async_client: AsyncClient, idp: FakeIdp) -> None:
    """Neither gate holds: the request is refused and the row does not move."""

    await _setup_admin(async_client)
    await _write_config(async_client)

    refused = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True})

    assert refused.status_code == 409 and _error(refused) == "break_glass_requires_totp"
    assert (await _provider_row()).enabled is False
    # Disabling is never gated, in either direction.
    assert (await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": False})).status_code == 200


@pytest.mark.asyncio
async def test_a_completed_test_login_is_audited_and_provisions_nothing(
    async_client: AsyncClient, app_instance, idp: FakeIdp
) -> None:
    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
    await _write_config(async_client)

    completed = await _test_login(async_client, idp, subject="someone-nobody-invited")

    assert completed.status_code == 303
    assert completed.headers["location"] == OIDC_SETTINGS_PATH
    assert async_client.cookies.get(OIDC_FLOW_COOKIE) is None

    [proved] = await _rows("oidc_test_login_succeeded")
    details = json.loads(proved.details or "{}")
    assert details["kind"] == "oidc" and details["subject"] == "someone-nobody-invited"
    assert proved.actor_username == "admin"

    # A pre-flight is not a sign-in: no account, no identity, and the admin's
    # own session is untouched.
    async with SessionLocal() as session:
        assert (await session.execute(select(DashboardIdentity))).scalars().all() == []
        usernames = {row.username for row in (await session.execute(select(DashboardUser))).scalars().all()}
    assert "someone-nobody-invited" not in usernames
    assert (await async_client.get(SESSION)).json()["user"]["username"] == "admin"


@pytest.mark.asyncio
async def test_a_test_login_runs_against_the_row_as_stored_but_a_sign_in_does_not(
    async_client: AsyncClient, app_instance, idp: FakeIdp
) -> None:
    """The pre-flight is the one path that must work while the provider is still off."""

    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
    await _write_config(async_client)
    assert (await _provider_row()).enabled is False

    proved = await _test_login(async_client, idp)
    assert proved.headers["location"] == OIDC_SETTINGS_PATH

    async with _client(app_instance) as anonymous:
        refused = await anonymous.get(LOGIN_START)
    assert refused.status_code == 404 and _error(refused) == "provider_not_found"


@pytest.mark.asyncio
async def test_a_test_login_by_another_browser_records_nothing(
    async_client: AsyncClient, app_instance, idp: FakeIdp
) -> None:
    """The flow cookie travels; the session it belongs to does not."""

    await _setup_admin(async_client)
    await _write_config(async_client)
    started = await async_client.post(TEST_LOGIN_START)
    assert started.status_code == 200, started.text
    parameters = _parameters(started.json()["authorizationUrl"])
    idp.token_response = json_response({"id_token": idp.id_token(nonce=parameters["nonce"], subject=SUBJECT)})

    async with _client(app_instance) as stranger:
        stranger.cookies.set(OIDC_FLOW_COOKIE, async_client.cookies[OIDC_FLOW_COOKIE])
        hijacked = await stranger.get(CALLBACK, params={"state": parameters["state"], "code": AUTHORIZATION_CODE})

    assert hijacked.status_code == 303 and hijacked.headers["location"] == OIDC_FAILURE_PATH
    assert (await _provider_row()).test_login_verified_at is None
    assert await _rows("oidc_test_login_succeeded") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "active"),
    [(DashboardAuthMode.STANDARD, True), (DashboardAuthMode.TRUSTED_HEADER, True), (DashboardAuthMode.DISABLED, False)],
)
async def test_the_enabled_row_is_active_everywhere_except_where_authentication_is_off(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, mode: DashboardAuthMode, active: bool
) -> None:
    await _setup_admin(async_client)
    await _store_config_directly(enabled=True)
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", mode.value)
    if mode == DashboardAuthMode.TRUSTED_HEADER:
        monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
        monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
        monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", "Remote-User")
    get_settings.cache_clear()

    async with SessionLocal() as session:
        row = await session.get(DashboardAuthProvider, OIDC_PROVIDER_ID)
        assert row is not None
        from app.core.auth.providers.registry import provider_active

        assert provider_active(row, mode) is active


# --- 3. the identity provider as a step-up factor ---


async def _oidc_only_admin(client: AsyncClient, idp: FakeIdp) -> DashboardUser:
    """An account whose only credential is its identity provider: no password, no secret."""

    await _store_config_directly(enabled=True, unknown_identity_role_id=ADMIN_ROLE)
    signed_in = await _oidc_sign_in(client, idp)
    assert signed_in.status_code == 303, signed_in.text
    async with SessionLocal() as session:
        user = (
            await session.execute(select(DashboardUser).where(DashboardUser.username == SUBJECT))
        ).scalar_one_or_none()
    assert user is not None and user.password_hash is None and user.totp_secret_encrypted is None
    return user


@pytest.mark.asyncio
async def test_an_oidc_only_admin_can_finally_step_up(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _oidc_only_admin(async_client, idp)

    # What the dashboard shows and what the gate accepts are the same list.
    assert (await async_client.get(SESSION)).json()["stepUp"]["methods"] == ["oidc"]
    refused = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"label": "Company sign-in"})
    assert refused.status_code == 403 and _error(refused) == "step_up_required"
    assert refused.json()["error"]["details"]["methods"] == ["oidc"]

    # The body endpoint cannot serve a factor that takes a redirect round trip.
    body = await async_client.post(STEP_UP, json={"code": "000000"})
    assert body.status_code == 403 and _error(body) == "step_up_unavailable"

    completed, parameters = await _round_trip(
        async_client, idp, STEP_UP_START, claims={"auth_time": int(datetime.now(UTC).timestamp())}
    )
    assert parameters["prompt"] == "login" and parameters["max_age"] == "0"
    assert completed.status_code == 303 and completed.headers["location"] == OIDC_SETTINGS_PATH

    accepted = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"label": "Company sign-in"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["label"] == "Company sign-in"

    [verified] = await _rows("step_up_verified")
    assert json.loads(verified.details or "{}")["methods"] == ["oidc"]


@pytest.mark.asyncio
async def test_an_account_that_holds_a_factor_is_not_offered_the_shortcut(
    async_client: AsyncClient, idp: FakeIdp
) -> None:
    """A password account's methods are unchanged, and an OIDC round trip does not satisfy them."""

    await _setup_admin(async_client)
    await _store_config_directly(enabled=True, unknown_identity_role_id=ADMIN_ROLE)
    # Give the password admin an OIDC identity of its own, so only the rule
    # (not the missing identity) can be what refuses it.
    async with SessionLocal() as session:
        admin = (await session.execute(select(DashboardUser).where(DashboardUser.username == "admin"))).scalar_one()
        session.add(DashboardIdentity(user_id=admin.id, provider="oidc", provider_key="default", subject=SUBJECT))
        await session.commit()
    await _converge_caches()

    assert (await async_client.get(SESSION)).json()["stepUp"]["methods"] == ["password"]

    completed, _ = await _round_trip(
        async_client, idp, STEP_UP_START, claims={"auth_time": int(datetime.now(UTC).timestamp())}
    )

    assert completed.status_code == 303 and completed.headers["location"] == OIDC_FAILURE_PATH
    assert await _rows("step_up_verified") == []
    assert STEP_UP_COOKIE not in async_client.cookies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_time", "label"),
    [(ABSENT, "absent"), (0, "stale")],
    ids=["no-auth-time", "stale-auth-time"],
)
async def test_a_re_authentication_the_identity_provider_will_not_date_is_refused(
    async_client: AsyncClient, idp: FakeIdp, auth_time: Any, label: str
) -> None:
    await _oidc_only_admin(async_client, idp)
    claims: dict[str, Any] = {
        "auth_time": auth_time
        if auth_time is ABSENT
        else int(datetime.now(UTC).timestamp()) - STEP_UP_MAX_AGE_SECONDS - 120
    }

    completed, _ = await _round_trip(async_client, idp, STEP_UP_START, claims=claims)

    assert completed.status_code == 303 and completed.headers["location"] == OIDC_FAILURE_PATH
    still_refused = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"label": label})
    assert still_refused.status_code == 403 and _error(still_refused) == "step_up_required"
    assert await _rows("step_up_verified") == []


@pytest.mark.asyncio
async def test_another_persons_identity_cannot_step_up_for_this_account(
    async_client: AsyncClient, idp: FakeIdp
) -> None:
    await _oidc_only_admin(async_client, idp)
    await _clear_rate_limits()

    completed, _ = await _round_trip(
        async_client,
        idp,
        STEP_UP_START,
        subject="bob-subject",
        claims={"auth_time": int(datetime.now(UTC).timestamp())},
    )

    assert completed.status_code == 303 and completed.headers["location"] == OIDC_FAILURE_PATH
    assert await _rows("step_up_verified") == []
    # Neither account got one: the other subject was never even resolved.
    async with SessionLocal() as session:
        subjects = {row.subject for row in (await session.execute(select(DashboardIdentity))).scalars().all()}
    assert subjects == {SUBJECT}


@pytest.mark.asyncio
async def test_the_step_up_start_needs_an_account(async_client: AsyncClient, app_instance, idp: FakeIdp) -> None:
    await _store_config_directly(enabled=True)

    async with _client(app_instance) as anonymous:
        refused = await anonymous.post(STEP_UP_START)

    assert refused.status_code == 401 and _error(refused) == "user_account_required"


# --- 4. the proof names a configuration, not just a moment ---


@pytest.mark.asyncio
async def test_a_configuration_written_during_the_round_trip_is_not_what_gets_proved(
    async_client: AsyncClient, app_instance, idp: FakeIdp
) -> None:
    """A pre-flight proves the connection it reached, not the one the row holds on return.

    The settings page can commit a repoint while the admin is at the identity
    provider. The write clears the proof; a callback that stamped unconditionally
    would hand it straight back — against an issuer nobody has tested.
    """

    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        await _emergency_admin(async_client, person)
    await _write_config(async_client)

    started = await async_client.post(TEST_LOGIN_START)
    assert started.status_code == 200, started.text
    parameters = _parameters(started.json()["authorizationUrl"])
    idp.token_response = json_response({"id_token": idp.id_token(nonce=parameters["nonce"], subject=SUBJECT)})

    # ... and the connection is rewritten while the browser is away.
    repointed = await _write_config(async_client, clientSecret=CLIENT_SECRET + "-rotated")
    assert repointed.status_code == 200, repointed.text

    completed = await async_client.get(CALLBACK, params={"state": parameters["state"], "code": AUTHORIZATION_CODE})

    assert completed.status_code == 303 and completed.headers["location"] == OIDC_FAILURE_PATH
    # Refused before the exchange: the code was never even presented.
    assert idp.token_requests() == []
    assert (await _provider_row()).test_login_verified_at is None
    assert await _rows("oidc_test_login_succeeded") == []

    refused = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True})
    assert refused.status_code == 409 and _error(refused) == "oidc_test_login_required"
    assert (await _provider_row()).enabled is False


@pytest.mark.asyncio
async def test_the_stamp_is_conditional_on_the_configuration_it_verified(async_client: AsyncClient) -> None:
    """The window the callback's own check cannot cover: a write that commits after its read.

    The callback reads the row, spends ten seconds exchanging a code, and only
    then writes. A configuration ``PATCH`` that commits inside that window
    clears the proof, so the write has to name the document it verified or it
    re-arms a connection nobody tested.
    """

    admin_id = await _setup_admin(async_client)
    await _store_config_directly(enabled=False)
    verified_config = (await _provider_row()).config_encrypted
    assert verified_config is not None

    # An unchanged row still takes the stamp: this is the ordinary path.
    async with SessionLocal() as session:
        stamped = await AuthProvidersRepository(session).record_test_login(
            OIDC_PROVIDER_ID, user_id=admin_id, verified_at=utcnow(), verified_config=verified_config
        )
    assert stamped is True
    assert (await _provider_row()).test_login_verified_at is not None

    # Now the settings page repoints and clears the proof, as ``_write_config`` does.
    async with SessionLocal() as session:
        row = await session.get(DashboardAuthProvider, OIDC_PROVIDER_ID)
        assert row is not None
        row.config_encrypted = seal_oidc_config(
            build_oidc_config(provider_config_document(client_secret=CLIENT_SECRET + "-rotated"))
        )
        row.test_login_user_id = None
        row.test_login_verified_at = None
        await session.commit()

    async with SessionLocal() as session:
        restamped = await AuthProvidersRepository(session).record_test_login(
            OIDC_PROVIDER_ID, user_id=admin_id, verified_at=utcnow(), verified_config=verified_config
        )

    assert restamped is False
    row = await _provider_row()
    assert row.test_login_verified_at is None and row.test_login_user_id is None


# --- 5. delegation: the levers that decide who an identity is ---


async def _sec_officer(async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A custom role holding ``security:write`` and nothing that hands out admin.

    The shape the role-mappings surface is already written against: an operator
    who may administer sign-in without being an administrator.
    """

    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", DashboardAuthMode.TRUSTED_HEADER.value)
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", "Remote-User")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_GROUPS_HEADER", "Remote-Groups")
    get_settings.cache_clear()

    role_id = str(uuid.uuid4())
    async with SessionLocal() as session:
        session.add(DashboardRoleRecord(id=role_id, slug="sec", name="Sec", kind="custom"))
        session.add_all(
            DashboardRoleGrant(role_id=role_id, permission=permission, scope="all")
            for permission in ("dashboard:read", "accounts:read", "ops:write", "security:write")
        )
        user = DashboardUser(id=str(uuid.uuid4()), username="sec-officer", role_id=role_id, role_source="manual")
        session.add(user)
        await session.flush()
        session.add(
            DashboardIdentity(
                user_id=user.id, provider="trusted_header", provider_key="default", subject="sec@example.com"
            )
        )
        await session.commit()
    await _converge_caches()

    headers = {"Remote-User": "sec@example.com"}
    return await _stepped_up(async_client, headers)


async def _stepped_up(client: AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    """Every write here is ``security:write``, so each needs a credential re-verified just now."""

    start = await client.post("/api/dashboard-auth/totp/setup/start", json={}, headers=headers)
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]
    confirm = await client.post(
        "/api/dashboard-auth/totp/setup/confirm", json={"secret": secret, "code": _code(secret)}, headers=headers
    )
    assert confirm.status_code == 200, confirm.text
    stepped = await client.post("/api/dashboard-auth/step-up", json={"code": _code(secret, steps=1)}, headers=headers)
    assert stepped.status_code == 200, stepped.text
    return headers


@pytest.mark.asyncio
async def test_repointing_the_identity_provider_needs_more_than_security_write(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An identity provider asserts *who someone is*, so repointing it is delegating everything.

    The role fields on this very endpoint already refuse this caller the admin
    preset. Rewriting the connection reaches further than any role does: point
    it at an identity provider you run, mint a token whose subject is an
    existing admin's, and the shared resolver signs you into that account.
    """

    headers = await _sec_officer(async_client, monkeypatch)
    await _store_config_directly(enabled=False)
    stored = (await _provider_row()).config_encrypted

    refused = await async_client.patch(
        f"{PROVIDERS}/{OIDC_PROVIDER_ID}",
        json={"config": _config_body(issuer="https://idp.attacker.example")},
        headers=headers,
    )

    assert refused.status_code == 403, refused.text
    assert _error(refused) == "insufficient_delegation"
    assert (await _provider_row()).config_encrypted == stored

    # The benign levers on the same endpoint still work for this role.
    labelled = await async_client.patch(
        f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"label": "Company sign-in"}, headers=headers
    )
    assert labelled.status_code == 200, labelled.text


@pytest.mark.asyncio
async def test_widening_email_linking_needs_more_than_security_write(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turning linking on widens the same move to any account whose address you know.

    Narrowing it again is never gated: the recovery direction stays open, as it
    does for every other gate on this endpoint.
    """

    headers = await _sec_officer(async_client, monkeypatch)
    await _store_config_directly(enabled=False)

    refused = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"linkByEmail": True}, headers=headers)
    assert refused.status_code == 403 and _error(refused) == "insufficient_delegation"
    assert (await _provider_row()).link_by_email is False

    async with SessionLocal() as session:
        row = await session.get(DashboardAuthProvider, OIDC_PROVIDER_ID)
        assert row is not None
        row.link_by_email = True
        await session.commit()
    await _converge_caches()

    narrowed = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"linkByEmail": False}, headers=headers)
    assert narrowed.status_code == 200, narrowed.text
    assert (await _provider_row()).link_by_email is False


@pytest.mark.asyncio
async def test_arming_a_row_that_hands_out_admin_needs_more_than_security_write(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabling a provider arms the roles it ALREADY hands out, which are delegated too.

    The role fields are bounded when they are *written*; that leaves the row
    this caller never wrote. Turning it on is the same act as writing its role,
    performed without ever naming it.
    """

    headers = await _sec_officer(async_client, monkeypatch)
    await _store_config_directly(enabled=False, unknown_identity_role_id=ADMIN_ROLE)

    refused = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True}, headers=headers)

    assert refused.status_code == 403, refused.text
    assert _error(refused) == "insufficient_delegation"
    assert (await _provider_row()).enabled is False

    # A row that hands out no more than the caller holds is not blocked here:
    # it falls through to the two lockout gates, as before.
    await _store_config_directly(enabled=False, unknown_identity_role_id=VIEWER_ROLE)
    await _clear_rate_limits()
    lockout = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"enabled": True}, headers=headers)
    assert lockout.status_code == 409 and _error(lockout) != "insufficient_delegation"
    assert (await _provider_row()).enabled is False


# --- 6. a factor presented is not a factor waived ---


@pytest.mark.asyncio
async def test_a_totp_code_on_an_oidc_session_does_not_stand_in_for_the_password(
    async_client: AsyncClient, idp: FakeIdp
) -> None:
    """Automatic step-up belongs to a fresh *password* sign-in, not to any fresh sign-in.

    An OIDC session is fresh and carries ``password_verified`` — that is the
    flag the gate reads to admit a cookie at all — but nobody presented a
    password to obtain it. Stamping a step-up when the second factor completes
    would let an account that holds a password change every security setting
    having proven only one of the two factors it is required to present.
    """

    admin_id = await _setup_admin(async_client)
    await _store_config_directly(enabled=True)
    async with SessionLocal() as session:
        session.add(DashboardIdentity(user_id=admin_id, provider="oidc", provider_key="default", subject=SUBJECT))
        await session.commit()
    await _converge_caches()

    signed_in = await _oidc_sign_in(async_client, idp)
    assert signed_in.status_code == 303, signed_in.text
    described = (await async_client.get(SESSION)).json()
    assert described["authMethod"] == "oidc" and described["user"]["id"] == admin_id
    assert described["stepUp"] == {"verifiedAt": None, "expiresAt": None, "methods": ["password"]}

    started = await async_client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = await async_client.post(
        "/api/dashboard-auth/totp/setup/confirm", json={"secret": secret, "code": _code(secret)}
    )
    assert confirmed.status_code == 200, confirmed.text
    verified = await async_client.post("/api/dashboard-auth/totp/verify", json={"code": _code(secret, steps=1)})
    assert verified.status_code == 200, verified.text

    refused = await async_client.patch(f"{PROVIDERS}/{OIDC_PROVIDER_ID}", json={"label": "Company sign-in"})

    assert refused.status_code == 403, refused.text
    assert _error(refused) == "step_up_required"
    assert refused.json()["error"]["details"]["methods"] == ["password", "totp"]
    assert (await async_client.get(SESSION)).json()["stepUp"]["verifiedAt"] is None
    assert (await _provider_row()).label != "Company sign-in"


@pytest.mark.asyncio
async def test_a_password_sign_in_still_steps_up_when_its_second_factor_lands(
    async_client: AsyncClient, app_instance
) -> None:
    """The behaviour the rule above narrows, unchanged: both credentials, one window."""

    await _setup_admin(async_client)
    async with _client(app_instance) as person:
        user_id = await _emergency_admin(async_client, person)
        # ``_emergency_admin`` signs in with a password and then verifies a code.
        stepped = (await person.get(SESSION)).json()["stepUp"]
        assert stepped["verifiedAt"] is not None and stepped["methods"] == ["password", "totp"]
        allowed = await person.patch(f"/api/dashboard-users/{user_id}", json={"isBreakGlass": True})
    assert allowed.status_code == 200, allowed.text


def test_every_field_of_the_provider_patch_is_classified() -> None:
    """A new lever on this endpoint fails here until someone decides what it reaches.

    This is the third time a delegation gate on this surface has covered one
    field while another on the same endpoint had equal power. A list of the
    fields that reach every account, checked against the request model, is
    cheaper than finding the fourth one in a review.
    """

    reaches_every_account = ADMIN_BOUND_FIELDS  # bounded by every grant
    hands_out_one_role = set(_ROLE_FIELDS)  # bounded by that role's grants
    arms_what_the_row_already_holds = {"enabled"}  # bounded by the row's roles
    # Display text and a flag with no reader anywhere in the product today; the
    # latter waives nothing, and the step-up rules say so explicitly.
    reaches_nothing = {"label", "skip_role_sync", "idp_mfa_enforced"}

    classified = reaches_every_account | hands_out_one_role | arms_what_the_row_already_holds | reaches_nothing
    assert set(AuthProviderUpdateRequest.model_fields) == classified
    assert reaches_every_account == {"config", "link_by_email"}
