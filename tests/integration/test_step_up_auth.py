"""Product-path coverage for ``step-up-auth`` (PLAN §5 H5).

Sensitive mutations need a re-verification from the last five minutes, defined
by what the account holds: a password account re-enters its password (plus its
authenticator code once it has one); a reverse-proxy account without a password
re-enters its authenticator code, or is told to enrol one. Reads and principals
without an account are never gated.
"""

from __future__ import annotations

from typing import Any

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.core.auth.totp as totp_module
import app.modules.dashboard_auth.service as service_module
from app.core.audit.service import drain_audit_log_tasks
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.step_up import STEP_UP_COOKIE
from app.core.config.settings import get_settings
from app.db.models import AuditLog, AuthProviderKind
from app.db.session import SessionLocal
from app.modules.auth_providers.seed import auth_provider_id
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store

pytestmark = pytest.mark.integration

SESSION = "/api/dashboard-auth/session"
STEP_UP = "/api/dashboard-auth/step-up"
PROVIDER = f"/api/auth-providers/{auth_provider_id(AuthProviderKind.TRUSTED_HEADER)}"
EPOCH = 1_700_000_000


class _Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = EPOCH
        monkeypatch.setattr(service_module, "time", lambda: self.now)
        monkeypatch.setattr(totp_module, "time", lambda: self.now)

    def advance(self, seconds: int) -> None:
        self.now += seconds


def _trusted_header_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", DashboardAuthMode.TRUSTED_HEADER.value)
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", "Remote-User")
    get_settings.cache_clear()


def _as(subject: str) -> dict[str, str]:
    return {"Remote-User": subject}


def _error(response) -> dict[str, Any]:
    return response.json()["error"]


async def _setup(client: AsyncClient) -> None:
    response = await client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    assert response.status_code == 200, response.text


async def _security_change(client: AsyncClient, *, headers: dict[str, str] | None = None, enabled: bool = True):
    return await client.put("/api/settings", json={"guestAccessEnabled": enabled}, headers=headers)


async def _enrol_totp(client: AsyncClient, clock: _Clock, *, headers: dict[str, str] | None = None) -> str:
    start = await client.post("/api/dashboard-auth/totp/setup/start", json={}, headers=headers)
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]
    confirm = await client.post(
        "/api/dashboard-auth/totp/setup/confirm",
        json={"secret": secret, "code": pyotp.TOTP(secret).at(clock.now)},
        headers=headers,
    )
    assert confirm.status_code == 200, confirm.text
    return secret


def _su(client: AsyncClient) -> int | None:
    state = get_dashboard_session_store().get(client.cookies.get(DASHBOARD_SESSION_COOKIE))
    assert state is not None
    return state.step_up_verified_at


async def _rows(action: str) -> list[AuditLog]:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


# --- password accounts ---


@pytest.mark.asyncio
async def test_password_account_steps_up_with_its_password(async_client: AsyncClient, monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    await _setup(async_client)
    assert _su(async_client) == EPOCH  # a password-only sign-in is itself the step-up

    # Fresh: the sign-in was moments ago.
    assert (await _security_change(async_client)).status_code == 200
    clock.advance(301)

    blocked = await _security_change(async_client, enabled=False)
    assert blocked.status_code == 403, blocked.text
    error = _error(blocked)
    assert error["code"] == "step_up_required"
    assert error["param"] == "security:write"
    assert error["details"] == {"methods": ["password"]}
    # Reads and non-security writes are untouched.
    assert (await async_client.get("/api/settings")).status_code == 200
    assert (await async_client.get("/api/dashboard-users")).status_code == 200
    assert (await async_client.put("/api/settings", json={"stickyThreadsEnabled": False})).status_code == 200
    session = (await async_client.get(SESSION)).json()
    assert session["stepUp"] == {"verifiedAt": None, "expiresAt": None, "methods": ["password"]}

    wrong = await async_client.post(STEP_UP, json={"password": "nope"})
    assert wrong.status_code == 401 and _error(wrong)["code"] == "invalid_credentials"
    missing = await async_client.post(STEP_UP, json={})
    assert missing.status_code == 401 and _error(missing) == _error(wrong)

    stepped = await async_client.post(STEP_UP, json={"password": "password123"})
    assert stepped.status_code == 200, stepped.text
    assert stepped.json() == {"verifiedAt": clock.now, "expiresAt": clock.now + 300}
    assert _su(async_client) == clock.now
    assert (await _security_change(async_client, enabled=False)).status_code == 200
    session = (await async_client.get(SESSION)).json()
    assert session["stepUp"] == {"verifiedAt": clock.now, "expiresAt": clock.now + 300, "methods": ["password"]}

    (row,) = await _rows("step_up_verified")
    assert row.actor_user_id == session["user"]["id"] and row.auth_method == "password"
    assert '"methods": ["password"]' in (row.details or "")
    failed = [r for r in await _rows("login_failed") if '"method": "step_up"' in (r.details or "")]
    assert len(failed) == 2

    # The verification ages out like any other.
    clock.advance(301)
    assert _error(await _security_change(async_client))["code"] == "step_up_required"


@pytest.mark.asyncio
async def test_password_account_with_totp_presents_both_factors(async_client: AsyncClient, monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    await _setup(async_client)
    secret = await _enrol_totp(async_client, clock)
    totp = pyotp.TOTP(secret)

    # A fresh password login is no longer a full step-up; the TOTP step completes it.
    await async_client.post("/api/dashboard-auth/logout", json={})
    login = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert login.status_code == 200 and _su(async_client) is None
    clock.advance(30)
    verify = await async_client.post("/api/dashboard-auth/totp/verify", json={"code": totp.at(clock.now)})
    assert verify.status_code == 200, verify.text
    assert _su(async_client) == clock.now

    clock.advance(301)
    blocked = await _security_change(async_client)
    assert _error(blocked)["details"] == {"methods": ["password", "totp"]}

    password_only = await async_client.post(STEP_UP, json={"password": "password123"})
    assert password_only.status_code == 401
    code_only = await async_client.post(STEP_UP, json={"code": totp.at(clock.now)})
    assert code_only.status_code == 401
    clock.advance(30)
    both = await async_client.post(STEP_UP, json={"password": "password123", "code": totp.at(clock.now)})
    assert both.status_code == 200, both.text
    assert (await _security_change(async_client)).status_code == 200
    # The step-up consumed the code: replaying it is refused.
    clock.advance(301)
    replay = await async_client.post(STEP_UP, json={"password": "password123", "code": totp.at(clock.now - 301)})
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_step_up_attempts_share_the_password_limiter_budget(async_client: AsyncClient, monkeypatch) -> None:
    _Clock(monkeypatch)
    await _setup(async_client)
    for _ in range(8):
        assert (await async_client.post(STEP_UP, json={"password": "wrong"})).status_code == 401
    limited = await async_client.post(STEP_UP, json={"password": "password123"})
    assert limited.status_code == 429
    assert _error(limited)["code"] == "step_up_rate_limited"


@pytest.mark.asyncio
async def test_su_is_minted_at_invite_acceptance(async_client: AsyncClient, app_instance, monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    await _setup(async_client)
    created = await async_client.post(
        "/api/dashboard-users", json={"username": "lee", "roleId": PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]}
    )
    assert created.status_code == 201, created.text
    token = created.json()["invite"]["token"]

    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as other:
        accepted = await other.post(
            "/api/dashboard-auth/invite/accept", json={"token": token, "password": "lee-password-1"}
        )
        assert accepted.status_code == 200, accepted.text
        assert _su(other) == clock.now


@pytest.mark.asyncio
async def test_implicit_local_admin_has_nothing_to_re_verify(async_client: AsyncClient) -> None:
    # No account exists: the local passwordless admin is exempt (documented).
    response = await async_client.post("/api/dashboard-auth/guest/password", json={"password": "guest-pass-123"})
    assert response.status_code == 200, response.text


# --- reverse-proxy accounts ---


@pytest.mark.asyncio
async def test_header_account_enrols_totp_and_steps_up_by_code(async_client: AsyncClient, monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    _trusted_header_mode(monkeypatch)
    alice = _as("alice@example.com")
    assert (await async_client.get(SESSION, headers=alice)).json()["authenticated"] is True

    # No password, no authenticator: the change is refused and says why. Nothing is exempted.
    unavailable = await async_client.patch(PROVIDER, json={"linkByEmail": True}, headers=alice)
    assert unavailable.status_code == 403, unavailable.text
    assert _error(unavailable)["code"] == "step_up_unavailable"
    assert (await async_client.post(STEP_UP, json={"code": "000000"}, headers=alice)).status_code == 403
    session = (await async_client.get(SESSION, headers=alice)).json()
    assert session["stepUp"] == {"verifiedAt": None, "expiresAt": None, "methods": []}
    # Reads still work.
    assert (await async_client.get("/api/auth-providers", headers=alice)).status_code == 200

    secret = await _enrol_totp(async_client, clock, headers=alice)
    required = await async_client.patch(PROVIDER, json={"linkByEmail": True}, headers=alice)
    assert _error(required) == {
        "code": "step_up_required",
        "message": "Confirm your identity to continue",
        "param": "security:write",
        "details": {"methods": ["totp"]},
    }

    clock.advance(30)
    stepped = await async_client.post(STEP_UP, json={"code": pyotp.TOTP(secret).at(clock.now)}, headers=alice)
    assert stepped.status_code == 200, stepped.text
    assert async_client.cookies.get(STEP_UP_COOKIE)
    assert async_client.cookies.get(DASHBOARD_SESSION_COOKIE) is None
    assert (await async_client.patch(PROVIDER, json={"linkByEmail": True}, headers=alice)).status_code == 200
    session = (await async_client.get(SESSION, headers=alice)).json()
    assert session["stepUp"] == {"verifiedAt": clock.now, "expiresAt": clock.now + 300, "methods": ["totp"]}
    (row,) = await _rows("step_up_verified")
    assert row.actor_user_id == session["user"]["id"] and row.auth_method == "trusted_header"

    # The cookie is bound to alice: bob presenting it gets nowhere.
    bob = _as("bob@example.com")
    assert _error(await async_client.patch(PROVIDER, json={"linkByEmail": False}, headers=bob))["code"] == (
        "step_up_unavailable"
    )

    # It ages out like a session step-up.
    clock.advance(301)
    assert _error(await async_client.patch(PROVIDER, json={"linkByEmail": False}, headers=alice))["code"] == (
        "step_up_required"
    )

    # Disabling the only method is allowed but announced.
    clock.advance(30)
    disabled = await async_client.post(
        "/api/dashboard-auth/totp/disable", json={"code": pyotp.TOTP(secret).at(clock.now)}, headers=alice
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json() == {"status": "ok", "stepUpAvailable": False}


@pytest.mark.asyncio
async def test_header_account_verify_route_mints_the_step_up_cookie(async_client: AsyncClient, monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    _trusted_header_mode(monkeypatch)
    alice = _as("alice")
    assert (await async_client.get(SESSION, headers=alice)).status_code == 200
    secret = await _enrol_totp(async_client, clock, headers=alice)
    clock.advance(30)

    bad = await async_client.post("/api/dashboard-auth/totp/verify", json={"code": "000000"}, headers=alice)
    assert bad.status_code == 400 and _error(bad)["code"] == "invalid_totp_code"
    verified = await async_client.post(
        "/api/dashboard-auth/totp/verify", json={"code": pyotp.TOTP(secret).at(clock.now)}, headers=alice
    )
    assert verified.status_code == 200, verified.text
    assert async_client.cookies.get(STEP_UP_COOKIE)
    assert (await async_client.patch(PROVIDER, json={"linkByEmail": True}, headers=alice)).status_code == 200


@pytest.mark.asyncio
async def test_totp_verify_on_an_old_session_does_not_mint_a_step_up(async_client: AsyncClient, monkeypatch) -> None:
    """Holding a stale cookie must not buy a step-up by enrolling a second factor.

    The password behind an old session was proven long ago, and whoever holds
    the cookie could have enrolled the secret themselves; only the second
    factor of a sign-in that just happened counts.
    """

    clock = _Clock(monkeypatch)
    await _setup(async_client)
    clock.advance(301)
    assert _error(await _security_change(async_client))["code"] == "step_up_required"

    secret = await _enrol_totp(async_client, clock)
    verified = await async_client.post(
        "/api/dashboard-auth/totp/verify", json={"code": pyotp.TOTP(secret).at(clock.now)}
    )
    assert verified.status_code == 200, verified.text
    # Whatever the session already carried is kept; nothing is refreshed.
    assert _su(async_client) == EPOCH
    blocked = await _security_change(async_client)
    assert blocked.status_code == 403 and _error(blocked)["code"] == "step_up_required"
    # The password is what unlocks it again (with a code the counter has not seen).
    clock.advance(31)
    stepped = await async_client.post(
        STEP_UP, json={"password": "password123", "code": pyotp.TOTP(secret).at(clock.now)}
    )
    assert stepped.status_code == 200, stepped.text
    assert (await _security_change(async_client)).status_code == 200


@pytest.mark.asyncio
async def test_resetting_totp_voids_a_header_account_step_up(async_client: AsyncClient, monkeypatch) -> None:
    """An administrator revoking the account's sessions must void its step-up at once."""

    clock = _Clock(monkeypatch)
    _trusted_header_mode(monkeypatch)
    alice = _as("alice")
    assert (await async_client.get(SESSION, headers=alice)).status_code == 200
    secret = await _enrol_totp(async_client, clock, headers=alice)
    clock.advance(30)
    stepped = await async_client.post(STEP_UP, json={"code": pyotp.TOTP(secret).at(clock.now)}, headers=alice)
    assert stepped.status_code == 200, stepped.text
    assert (await async_client.patch(PROVIDER, json={"linkByEmail": True}, headers=alice)).status_code == 200

    users = (await async_client.get("/api/dashboard-users", headers=alice)).json()
    alice_id = next(row["id"] for row in users if row["username"] == "alice")
    revoked = await async_client.post(f"/api/dashboard-users/{alice_id}/revoke-sessions", headers=alice)
    assert revoked.status_code == 200, revoked.text

    # The cookie still rides along, but the generation it was minted for is gone.
    assert async_client.cookies.get(STEP_UP_COOKIE)
    refused = await async_client.patch(PROVIDER, json={"linkByEmail": False}, headers=alice)
    assert refused.status_code == 403 and _error(refused)["code"] == "step_up_required"
    session = (await async_client.get(SESSION, headers=alice)).json()
    assert session["stepUp"]["verifiedAt"] is None
