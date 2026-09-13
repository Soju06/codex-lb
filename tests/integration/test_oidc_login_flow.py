"""The OIDC sign-in flow end to end, against an identity provider that never touches the network.

The happy path is one test here; the rest are the refusals an adversarial
reader of an authentication flow looks for: a replayed authorization code, a
reused state, a state without its browser, a mismatched nonce, an expired
flow, a caller-supplied destination, and a redirect URI taken from the request
instead of the row.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import app.modules.dashboard_users.identity_resolver as identity_resolver
from app.core.audit.service import drain_audit_log_tasks
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.dashboard_session_ttl import REMOTE_DASHBOARD_SESSION_TTL_SECONDS
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.auth.providers.registry import get_auth_provider_registry
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.db.models import (
    AuditLog,
    AuthProviderKind,
    DashboardAuthProvider,
    DashboardIdentity,
    DashboardOidcLoginFlow,
    DashboardSettings,
    DashboardUser,
    RateLimitAttempt,
)
from app.db.session import SessionLocal
from app.modules.auth_providers.config import build_oidc_config, seal_oidc_config
from app.modules.auth_providers.seed import auth_provider_id
from app.modules.dashboard_auth.oidc_api import OIDC_DASHBOARD_PATH, OIDC_FAILURE_PATH, OIDC_PENDING_PATH
from app.modules.dashboard_auth.oidc_flows import OIDC_FLOW_COOKIE, OidcFlowPurpose, OidcFlowRepository
from tests.fixtures.fake_idp import (
    CLIENT_SECRET,
    REDIRECT_URI,
    FakeIdp,
    json_response,
    provider_config_document,
)

pytestmark = pytest.mark.integration

START = "/api/dashboard-auth/oidc/login/start"
CALLBACK = "/api/dashboard-auth/oidc/callback"
SESSION = "/api/dashboard-auth/session"
OIDC_PROVIDER_ID = auth_provider_id(AuthProviderKind.OIDC)
VIEWER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]
ADMIN_PASSWORD = "password" + "123"
AUTHORIZATION_CODE = "authorization-" + "code-value"


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


def _trusted_header_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reverse-proxy topology, as the trusted-header suite sets it up."""

    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", DashboardAuthMode.TRUSTED_HEADER.value)
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", "Remote-User")
    get_settings.cache_clear()


async def _configure_provider(
    *, enabled: bool = True, unknown_identity_role_id: str | None = VIEWER_ROLE, **config_overrides: object
) -> None:
    """Store a connection document the way the settings API will, then converge the caches."""

    sealed = seal_oidc_config(build_oidc_config(provider_config_document(**config_overrides)))
    async with SessionLocal() as session:
        row = await session.get(DashboardAuthProvider, OIDC_PROVIDER_ID)
        assert row is not None, "the migration and the test schema both seed an oidc/default row"
        row.enabled = enabled
        row.config_encrypted = sealed
        row.unknown_identity_role_id = unknown_identity_role_id
        row.updated_at = datetime.now(UTC)
        await session.commit()
    await _converge_caches()


async def _converge_caches() -> None:
    get_auth_provider_registry().clear()
    identity_resolver.get_identity_resolution_cache().clear()
    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


async def _rows(action: str) -> list[AuditLog]:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _clear_rate_limits() -> None:
    async with SessionLocal() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()


def _authorization_parameters(response) -> dict[str, str]:
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return {name: values[0] for name, values in query.items()}


async def _start(client: AsyncClient, **kwargs: Any) -> dict[str, str]:
    response = await client.get(START, **kwargs)
    parameters = _authorization_parameters(response)
    assert OIDC_FLOW_COOKIE in response.cookies
    return parameters


async def _callback(
    client: AsyncClient,
    *,
    state: str | None,
    code: str | None = AUTHORIZATION_CODE,
    headers: dict[str, str] | None = None,
    extra: dict[str, str] | None = None,
):
    query: dict[str, str] = dict(extra or {})
    if state is not None:
        query["state"] = state
    if code is not None:
        query["code"] = code
    return await client.get(CALLBACK, params=query, headers=headers)


async def _sign_in(client: AsyncClient, idp: FakeIdp, *, subject: str = "alice-subject", **token_kwargs: Any):
    parameters = await _start(client)
    idp.token_response = json_response(
        {"id_token": idp.id_token(nonce=parameters["nonce"], subject=subject, **token_kwargs)}
    )
    return await _callback(client, state=parameters["state"])


# --- 1. starting a flow ---


@pytest.mark.asyncio
async def test_the_start_sends_the_browser_to_the_identity_provider(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider()

    parameters = await _start(async_client)

    assert parameters["code_challenge_method"] == "S256"
    assert parameters["response_type"] == "code"
    assert parameters["redirect_uri"] == REDIRECT_URI
    assert parameters["state"] and parameters["nonce"] and parameters["code_challenge"]
    # The clear state and nonce are never stored; only their hashes are.
    async with SessionLocal() as session:
        [flow] = (await session.execute(select(DashboardOidcLoginFlow))).scalars().all()
    assert flow.purpose == OidcFlowPurpose.LOGIN.value
    assert parameters["state"] not in (flow.state_hash, flow.nonce_hash)
    assert parameters["nonce"] not in (flow.state_hash, flow.nonce_hash)
    assert flow.code_verifier_encrypted != parameters["code_challenge"].encode()


@pytest.mark.asyncio
async def test_a_disabled_or_unconfigured_provider_is_the_same_404(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider(enabled=False)
    disabled = await async_client.get(START)
    assert disabled.status_code == 404 and _error(disabled) == "provider_not_found"
    assert OIDC_FLOW_COOKIE not in disabled.cookies

    async with SessionLocal() as session:
        row = await session.get(DashboardAuthProvider, OIDC_PROVIDER_ID)
        assert row is not None
        row.enabled, row.config_encrypted = True, None
        await session.commit()
    await _converge_caches()

    unconfigured = await async_client.get(START)
    assert unconfigured.status_code == 404 and _error(unconfigured) == "provider_not_found"


@pytest.mark.asyncio
async def test_the_provider_is_never_active_while_dashboard_auth_is_bypassed(
    async_client: AsyncClient, idp: FakeIdp, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _configure_provider()
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", DashboardAuthMode.DISABLED.value)
    get_settings.cache_clear()

    refused = await async_client.get(START)

    assert refused.status_code == 404 and _error(refused) == "provider_not_found"


@pytest.mark.asyncio
async def test_the_login_screen_advertises_the_provider_and_its_start_path(
    async_client: AsyncClient, idp: FakeIdp
) -> None:
    await _configure_provider()

    providers = (await async_client.get(SESSION)).json()["login"]["providers"]

    entry = next(item for item in providers if item["kind"] == "oidc")
    assert entry["loginUrl"] == START
    assert next(item for item in providers if item["kind"] == "password")["loginUrl"] is None


# --- 2. no destination is ever accepted from the caller ---


@pytest.mark.asyncio
async def test_a_caller_supplied_destination_is_ignored_everywhere(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider()
    evil = {"next": "https://evil.example.test", "return_to": "//evil.example.test", "redirect": "https://evil.test"}

    started = await async_client.get(START, params=evil)
    assert urlsplit(started.headers["location"]).netloc == "idp.example.test"
    assert "evil" not in started.headers["location"]

    parameters = _authorization_parameters(started)
    idp.token_response = json_response({"id_token": idp.id_token(nonce=parameters["nonce"])})
    completed = await _callback(async_client, state=parameters["state"], extra=evil)

    assert completed.status_code == 303
    assert completed.headers["location"] == OIDC_DASHBOARD_PATH


# --- 3. the happy path and the pending screen ---


@pytest.mark.asyncio
async def test_a_provisioned_identity_signs_in_as_an_account(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider()

    completed = await _sign_in(async_client, idp, claims={"email": "alice@example.com", "name": "Alice Smith"})

    assert completed.status_code == 303
    assert completed.headers["location"] == OIDC_DASHBOARD_PATH
    # The flow cookie is cleared however the flow ends.
    assert async_client.cookies.get(OIDC_FLOW_COOKIE) is None

    session = (await async_client.get(SESSION)).json()
    assert session["authenticated"] is True
    assert session["authMethod"] == "oidc"
    assert session["user"]["role"]["slug"] == "viewer"

    async with SessionLocal() as session_db:
        identity = (
            await session_db.execute(select(DashboardIdentity).where(DashboardIdentity.provider == "oidc"))
        ).scalar_one()
    assert identity.subject == "alice-subject"

    [success] = [row for row in await _rows("login_success") if '"oidc"' in (row.details or "")]
    assert json.loads(success.details or "{}") == {"method": "oidc", "username": "alice-subject"}


@pytest.mark.asyncio
async def test_two_spellings_of_a_subject_become_two_accounts(
    async_client: AsyncClient, app_instance, idp: FakeIdp
) -> None:
    """Unlike the trusted-header path, an OIDC ``sub`` is case-sensitive by specification."""

    await _configure_provider()
    await _sign_in(async_client, idp, subject="Alice")
    async with _client(app_instance) as other:
        await _sign_in(other, idp, subject="alice")

    async with SessionLocal() as session:
        subjects = (
            (await session.execute(select(DashboardIdentity.subject).where(DashboardIdentity.provider == "oidc")))
            .scalars()
            .all()
        )
    assert sorted(subjects) == ["Alice", "alice"]


@pytest.mark.asyncio
async def test_an_unknown_identity_lands_on_the_pending_screen(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider(unknown_identity_role_id=None)

    refused = await _sign_in(
        async_client,
        idp,
        subject="stranger",
        claims={"email": "stranger@example.com", "groups": ["Contractors"]},
    )

    assert refused.status_code == 303
    assert refused.headers["location"] == OIDC_PENDING_PATH
    # No session was minted: the response carries no account cookie at all.
    assert not [header for header in refused.headers.get_list("set-cookie") if "dashboard_session" in header]

    async with SessionLocal() as session:
        assert (await session.execute(select(DashboardUser))).scalars().all() == []

    # Written by the shared resolver, not by the provider, and it names the facts
    # an operator needs to add the person.
    unknown = [row for row in await _rows("login_failed") if '"unknown_identity"' in (row.details or "")]
    assert len(unknown) == 1
    details = json.loads(unknown[0].details or "{}")
    assert details["provider"] == "oidc" and details["subject"] == "stranger"
    assert details["email"] == "stranger@example.com" and details["groups"] == ["contractors"]


@pytest.mark.asyncio
async def test_an_unverified_email_never_reaches_the_account(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider()

    await _sign_in(async_client, idp, claims={"email": "alice@example.com", "email_verified": False})

    async with SessionLocal() as session:
        [user] = (await session.execute(select(DashboardUser))).scalars().all()
        [identity] = (await session.execute(select(DashboardIdentity))).scalars().all()
    assert user.email is None and identity.email is None


# --- 4. single use: state, code and nonce ---


@pytest.mark.asyncio
async def test_a_reused_state_is_refused_and_no_code_is_exchanged_twice(
    async_client: AsyncClient, idp: FakeIdp
) -> None:
    await _configure_provider()
    parameters = await _start(async_client)
    idp.token_response = json_response({"id_token": idp.id_token(nonce=parameters["nonce"])})

    held_cookie = async_client.cookies[OIDC_FLOW_COOKIE]

    first = await _callback(async_client, state=parameters["state"])
    assert first.headers["location"] == OIDC_DASHBOARD_PATH

    # The completion cleared the cookie, so the replay re-sends it by hand --
    # exactly what a captured URL plus a captured cookie would look like.
    replayed = await async_client.get(
        CALLBACK,
        params={"state": parameters["state"], "code": AUTHORIZATION_CODE},
        headers={"Cookie": f"{OIDC_FLOW_COOKIE}={held_cookie}"},
    )

    assert replayed.status_code == 303
    assert replayed.headers["location"] == OIDC_FAILURE_PATH
    assert len(idp.token_requests()) == 1


@pytest.mark.asyncio
async def test_two_simultaneous_consumes_have_exactly_one_winner(async_client: AsyncClient, idp: FakeIdp) -> None:
    """The conditional delete is what makes a state single-use across replicas."""

    await _configure_provider()
    parameters = await _start(async_client)
    state_hash = _state_hash(parameters["state"])

    async def _consume() -> object:
        async with SessionLocal() as session:
            return await OidcFlowRepository(session, TokenEncryptor()).consume(state_hash)

    winners = [result for result in await asyncio.gather(_consume(), _consume()) if result is not None]

    assert len(winners) == 1


def _state_hash(state: str) -> str:
    from app.core.auth.providers.oidc import hash_flow_value

    return hash_flow_value(state)


@pytest.mark.asyncio
async def test_a_state_without_its_browser_is_refused_and_the_flow_survives(
    async_client: AsyncClient, app_instance, idp: FakeIdp
) -> None:
    await _configure_provider()
    parameters = await _start(async_client)
    idp.token_response = json_response({"id_token": idp.id_token(nonce=parameters["nonce"])})

    async with _client(app_instance) as other_browser:
        stripped = await _callback(other_browser, state=parameters["state"])
        assert stripped.headers["location"] == OIDC_FAILURE_PATH

        # A cookie from a *different* flow does not unlock this state either.
        await _start(other_browser)
        mismatched = await _callback(other_browser, state=parameters["state"])
        assert mismatched.headers["location"] == OIDC_FAILURE_PATH

        without_state = await _callback(other_browser, state=None)
        assert without_state.headers["location"] == OIDC_FAILURE_PATH

    assert idp.token_requests() == []

    # The owner's flow was never consumed by any of that.
    completed = await _callback(async_client, state=parameters["state"])
    assert completed.headers["location"] == OIDC_DASHBOARD_PATH


@pytest.mark.asyncio
async def test_a_state_that_is_not_even_text_we_could_compare_is_refused(
    async_client: AsyncClient, idp: FakeIdp
) -> None:
    """A query parameter can carry anything; the comparison must not raise on it."""

    await _configure_provider()
    await _start(async_client)

    for hostile in ["stäte", "x" * 4096, ""]:
        refused = await _callback(async_client, state=hostile)
        assert refused.status_code == 303
        assert refused.headers["location"] == OIDC_FAILURE_PATH


@pytest.mark.asyncio
async def test_a_mismatched_or_absent_nonce_is_refused(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider()

    mismatched = await _sign_in(async_client, idp, claims={"nonce": "a-nonce-this-flow-never-sent"})
    assert mismatched.headers["location"] == OIDC_FAILURE_PATH

    from tests.fixtures.fake_idp import ABSENT

    absent = await _sign_in(async_client, idp, claims={"nonce": ABSENT})
    assert absent.headers["location"] == OIDC_FAILURE_PATH

    async with SessionLocal() as session:
        assert (await session.execute(select(DashboardUser))).scalars().all() == []


@pytest.mark.asyncio
async def test_an_abandoned_flow_expires(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider()
    parameters = await _start(async_client)
    async with SessionLocal() as session:
        [flow] = (await session.execute(select(DashboardOidcLoginFlow))).scalars().all()
        flow.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    idp.token_response = json_response({"id_token": idp.id_token(nonce=parameters["nonce"])})

    stale = await _callback(async_client, state=parameters["state"])

    assert stale.headers["location"] == OIDC_FAILURE_PATH
    assert idp.token_requests() == []
    async with SessionLocal() as session:
        assert (await session.execute(select(DashboardOidcLoginFlow))).scalars().all() == []


@pytest.mark.asyncio
async def test_an_identity_provider_error_is_not_reflected(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider()
    parameters = await _start(async_client)

    refused = await _callback(
        async_client,
        state=parameters["state"],
        code=None,
        extra={"error": "access_denied", "error_description": "the person said no"},
    )

    assert refused.headers["location"] == OIDC_FAILURE_PATH
    assert "access_denied" not in refused.text and "said no" not in refused.text
    assert all("said no" not in (row.details or "") for row in await _rows("login_failed"))


# --- 5. every failure looks the same, and carries nothing ---


@pytest.mark.asyncio
async def test_every_failure_is_the_same_answer_and_leaks_nothing(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider()

    unknown_state = await _callback(async_client, state="a-state-nobody-issued")
    bad_token = await _sign_in(async_client, idp, claims={"iss": "https://evil.example.test"})

    parameters = await _start(async_client)
    idp.token_response = json_response({"error": "invalid_grant"}, status=400)
    refused_exchange = await _callback(async_client, state=parameters["state"])

    answers = {
        (response.status_code, response.headers["location"])
        for response in (unknown_state, bad_token, refused_exchange)
    }
    assert answers == {(303, OIDC_FAILURE_PATH)}
    assert all(response.text == "" or "state" not in response.text for response in (unknown_state, bad_token))

    details = [row.details or "" for row in await _rows("login_failed")]
    assert details and all('"reason": "oidc_login_failed"' in row for row in details)
    for row in details:
        # Only fixed labels of our own: no code, no secret, no token, and the
        # audit sanitizer redacts none of those key names, so keeping them out
        # is the only thing that keeps them out.
        assert set(json.loads(row)) == {"method", "username", "reason", "stage"}
        assert AUTHORIZATION_CODE not in row
        assert CLIENT_SECRET not in row
        assert "eyJ" not in row  # the first characters of every JWT


@pytest.mark.asyncio
async def test_a_replayed_callback_url_is_rate_limited_as_json(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider()
    await _clear_rate_limits()
    parameters = await _start(async_client)
    captured = {"Cookie": f"{OIDC_FLOW_COOKIE}={async_client.cookies[OIDC_FLOW_COOKIE]}"}
    query = {"state": parameters["state"], "code": AUTHORIZATION_CODE}

    answers = [await async_client.get(CALLBACK, params=query, headers=captured) for _ in range(6)]

    assert [response.status_code for response in answers[:5]] == [303] * 5
    assert answers[-1].status_code == 429
    assert _error(answers[-1]) == "oidc_rate_limited"
    assert answers[-1].headers["retry-after"]
    # The key is a hash; the live state value is not in the rate-limit table.
    async with SessionLocal() as session:
        keys = (await session.execute(select(RateLimitAttempt.key))).scalars().all()
    assert all(parameters["state"] not in key for key in keys)


# --- 6. the redirect URI, and the Host header ---


@pytest.mark.asyncio
async def test_the_redirect_uri_comes_from_the_row_not_the_request(async_client: AsyncClient, idp: FakeIdp) -> None:
    await _configure_provider()

    parameters = await _start(async_client, headers={"Host": "evil.example.test"})
    idp.token_response = json_response({"id_token": idp.id_token(nonce=parameters["nonce"])})
    await _callback(async_client, state=parameters["state"], headers={"Host": "evil.example.test"})

    assert parameters["redirect_uri"] == REDIRECT_URI
    [exchange] = idp.token_requests()
    assert exchange.data["redirect_uri"] == REDIRECT_URI
    assert "evil.example.test" not in json.dumps(exchange.data)


# --- 7. the session it mints ---


async def _set_session_lifetime(seconds: int) -> None:
    async with SessionLocal() as session:
        (await session.execute(select(DashboardSettings))).scalar_one().dashboard_session_ttl_seconds = seconds
        await session.commit()
    await _converge_caches()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured", "expected"),
    [(365 * 24 * 3600, REMOTE_DASHBOARD_SESSION_TTL_SECONDS), (3600, 3600)],
    ids=["one-year-is-capped", "one-hour-still-wins"],
)
async def test_the_single_sign_on_session_is_capped_at_twelve_hours(
    async_client: AsyncClient, idp: FakeIdp, configured: int, expected: int
) -> None:
    await _configure_provider()
    await _set_session_lifetime(configured)

    completed = await _sign_in(async_client, idp)

    cookie = next(
        header for header in completed.headers.get_list("set-cookie") if header.startswith("codex_lb_dashboard_session")
    )
    assert f"Max-Age={expected}" in cookie


@pytest.mark.asyncio
async def test_an_oidc_session_is_not_a_local_password_fallback(
    async_client: AsyncClient, app_instance, idp: FakeIdp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag that admits a cookie is not evidence of a *local password*."""

    await _configure_provider(unknown_identity_role_id=PRESET_ROLE_IDS[PresetRoleSlug.ADMIN])
    password_admin = await async_client.post("/api/dashboard-auth/password/setup", json={"password": ADMIN_PASSWORD})
    assert password_admin.status_code == 200, password_admin.text

    async with _client(app_instance) as sso_browser:
        signed_in = await _sign_in(sso_browser, idp, subject="sso-admin")
        assert signed_in.headers["location"] == OIDC_DASHBOARD_PATH

        _trusted_header_mode(monkeypatch)
        await _converge_caches()

        # No identity header, and the OIDC cookie is not the local fallback.
        refused = await sso_browser.get("/api/settings")
        assert refused.status_code == 401 and _error(refused) == "proxy_auth_required"
        assert (await sso_browser.get(SESSION)).json()["passwordSessionActive"] is False

    # The real password session still is.
    admitted = await async_client.get("/api/settings")
    assert admitted.status_code == 200, admitted.text


# --- 8. the shape of the routes themselves ---


def test_both_public_routes_are_safe_methods(app_instance) -> None:
    """A ``form_post`` callback would be refused by the origin middleware before routing."""

    methods = {
        route.path: route.methods
        for route in app_instance.routes
        if getattr(route, "path", "").startswith("/api/dashboard-auth/oidc")
    }

    assert methods[START] == {"GET"}
    assert methods[CALLBACK] == {"GET"}
