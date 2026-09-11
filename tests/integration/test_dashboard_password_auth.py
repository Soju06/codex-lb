from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.db.models import RateLimitAttempt
from app.db.session import get_background_session

pytestmark = pytest.mark.integration


async def _clear_password_rate_limit_attempts() -> None:
    async with get_background_session() as session:
        await session.execute(delete(RateLimitAttempt).where(RateLimitAttempt.type == "password"))
        await session.commit()


@pytest.mark.asyncio
async def test_password_endpoints_setup_login_change_remove(async_client):
    weak = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "short"})
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "validation_error"

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200
    assert setup.json()["passwordRequired"] is True

    setup_again = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_again.status_code == 409

    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200

    invalid_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "wrong-password"},
    )
    assert invalid_login.status_code == 401
    assert invalid_login.json()["error"]["code"] == "invalid_credentials"

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 200
    assert login.json()["authenticated"] is True

    bad_change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": "wrong-password", "newPassword": "new-password-456"},
    )
    assert bad_change.status_code == 401
    assert bad_change.json()["error"]["code"] == "invalid_credentials"

    change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": "password123", "newPassword": "new-password-456"},
    )
    assert change.status_code == 200

    logout_again = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout_again.status_code == 200

    old_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert old_login.status_code == 401

    new_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "new-password-456"},
    )
    assert new_login.status_code == 200

    bad_remove = await async_client.request(
        "DELETE",
        "/api/dashboard-auth/password",
        json={"password": "wrong-password"},
    )
    assert bad_remove.status_code == 401
    assert bad_remove.json()["error"]["code"] == "invalid_credentials"

    remove = await async_client.request(
        "DELETE",
        "/api/dashboard-auth/password",
        json={"password": "new-password-456"},
    )
    assert remove.status_code == 200

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    session_payload = session.json()
    assert session_payload["passwordRequired"] is False
    assert session_payload["authenticated"] is True
    assert session_payload["totpRequiredOnLogin"] is False


@pytest.mark.asyncio
async def test_password_login_rate_limit(async_client):
    await _clear_password_rate_limit_attempts()

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200
    await async_client.post("/api/dashboard-auth/logout", json={})

    for _ in range(8):
        response = await async_client.post(
            "/api/dashboard-auth/password/login",
            json={"password": "wrong-password"},
        )
        assert response.status_code == 401

    limited = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "wrong-password"},
    )
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers

    await _clear_password_rate_limit_attempts()
    success = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert success.status_code == 200


@pytest.mark.asyncio
async def test_password_not_configured_requests_do_not_spend_login_budget(async_client):
    await _clear_password_rate_limit_attempts()

    for _ in range(8):
        response = await async_client.post(
            "/api/dashboard-auth/password/login",
            json={"password": "wrong-password"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "password_not_configured"

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_password_setup_rejects_overlong_password(async_client):
    # bcrypt enforces a hard 72-byte input limit and raises ValueError otherwise.
    # The API must surface this as a 422 validation error, not a 500.
    long_password = "a" * 73  # 73 ASCII bytes -> over the bcrypt limit
    response = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": long_password},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "password_too_long"
    assert "72 bytes" in body["error"]["message"]


@pytest.mark.asyncio
async def test_password_setup_accepts_72_byte_password(async_client):
    # Exactly 72 bytes must still be accepted.
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "a" * 72},
    )
    assert setup.status_code == 200


@pytest.mark.asyncio
async def test_password_setup_counts_utf8_bytes_not_codepoints(async_client):
    # 25 emoji code points = 100 UTF-8 bytes (each emoji is 4 bytes).
    # Even though len() reports 25 characters, the encoded length exceeds 72
    # bytes and must be rejected with the same clear error.
    long_emoji = "🦞" * 25  # 100 bytes when encoded
    response = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": long_emoji},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "password_too_long"


@pytest.mark.asyncio
async def test_password_change_rejects_overlong_new_password(async_client):
    # Establish a valid password so we can authenticate the change request.
    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 200

    change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"current_password": "password123", "new_password": "a" * 73},
    )
    assert change.status_code == 422
    body = change.json()
    assert body["error"]["code"] == "password_too_long"


# --- user accounts: login resolution, session revocation, /me ---------------------------------

import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from typing import Any  # noqa: E402

import bcrypt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

import app.modules.dashboard_auth.api as dashboard_auth_api_module  # noqa: E402
import app.modules.dashboard_auth.service as dashboard_auth_service_module  # noqa: E402
from app.core.audit.service import drain_audit_log_tasks  # noqa: E402
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug  # noqa: E402
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache  # noqa: E402
from app.core.config.settings_cache import get_settings_cache  # noqa: E402
from app.core.crypto import TokenEncryptor  # noqa: E402
from app.db.models import COMPAT_ADMIN_USERNAME, AuditLog, DashboardSettings, DashboardUser  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store  # noqa: E402
from app.modules.dashboard_users.repository import DashboardUsersRepository, LocalAuthState  # noqa: E402
from app.modules.settings.repository import SettingsRepository  # noqa: E402


async def _clear_rate_limit_attempts(*types: str) -> None:
    async with get_background_session() as session:
        await session.execute(delete(RateLimitAttempt).where(RateLimitAttempt.type.in_(types)))
        await session.commit()


async def _seed_legacy_password_hash(value: str | None) -> None:
    """Write the legacy column directly, the way a previous-release replica would."""

    async with SessionLocal() as session:
        row = await SettingsRepository(session).get_or_create()
        row.password_hash = value
        await session.commit()
    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


async def _legacy_password_hash() -> str | None:
    async with SessionLocal() as session:
        return (await session.execute(select(DashboardSettings))).scalar_one().password_hash


async def _insert_user(
    username: str,
    *,
    password: str | None = "second-password-1",
    slug: PresetRoleSlug = PresetRoleSlug.OPERATOR,
    status: str = "active",
) -> DashboardUser:
    async with SessionLocal() as session:
        user = DashboardUser(
            id=str(uuid.uuid4()),
            username=username,
            role_id=PRESET_ROLE_IDS[slug],
            status=status,
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode() if password else None,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    await get_dashboard_users_cache().invalidate()
    return user


async def _compat_user() -> DashboardUser:
    async with SessionLocal() as session:
        return (
            await session.execute(select(DashboardUser).where(DashboardUser.username == COMPAT_ADMIN_USERNAME))
        ).scalar_one()


async def _update_user(user_id: str, **values: object) -> None:
    async with SessionLocal() as session:
        user = await session.get(DashboardUser, user_id)
        assert user is not None
        for key, value in values.items():
            setattr(user, key, value)
        await session.commit()
    await get_dashboard_users_cache().invalidate()


def _second_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _setup(client: AsyncClient, password: str = "password123") -> dict[str, Any]:
    response = await client.post("/api/dashboard-auth/password/setup", json={"password": password})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_bootstrap_creates_admin_account_and_describes_the_session(async_client):
    payload = await _setup(async_client)

    assert payload["authenticated"] is True
    assert payload["passwordRequired"] is True
    assert payload["role"] == "admin"
    assert payload["user"]["username"] == "admin"
    assert payload["user"]["role"]["slug"] == "admin"
    assert payload["user"]["role"]["kind"] == "preset"
    assert payload["authMethod"] == "password"
    assert payload["mustChangePassword"] is False
    assert payload["totpEnrollmentRequired"] is False
    assert payload["login"] == {
        "usernameField": "hidden",
        "providers": [{"kind": "password", "providerKey": "default", "label": "Password", "loginUrl": None}],
        "localLogin": "enabled",
        "pendingIdentity": False,
    }
    assert payload["accessSummary"]["usersTotal"] == 1
    assert payload["accessSummary"]["usersActive"] == 1
    assert payload["accessSummary"]["nonAdminUsers"] == 0
    assert payload["accessSummary"]["providersEnabled"] == ["password"]
    # Assignable presets, ordered by slug: admin, operator, viewer (guest never; member not yet).
    assert payload["assignableRoleIds"] == [
        PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
        PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR],
        PRESET_ROLE_IDS[PresetRoleSlug.VIEWER],
    ]
    permissions = payload["permissions"]
    assert permissions[:2] == ["read", "write"]
    assert "users:manage:all" in permissions and "api_keys:write:all" in permissions

    user = await _compat_user()
    assert user.last_login_at is None  # setup issues the session without a login event
    me = await async_client.get("/api/dashboard-auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "id": user.id,
        "username": "admin",
        "displayName": None,
        "email": None,
        "role": {"id": PRESET_ROLE_IDS[PresetRoleSlug.ADMIN], "slug": "admin", "name": "Admin", "kind": "preset"},
        "authMethod": "password",
        "totpConfigured": False,
        "mustChangePassword": False,
    }


@pytest.mark.asyncio
async def test_me_requires_a_user_account(async_client, app_instance):
    # Passwordless local install: implicit admin, no account.
    anonymous = await async_client.get("/api/dashboard-auth/me")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "user_account_required"

    await _setup(async_client)
    enable = await async_client.put(
        "/api/settings",
        json={**(await async_client.get("/api/settings")).json(), "guestAccessEnabled": True},
    )
    assert enable.status_code == 200, enable.text
    guest = AsyncClient(
        transport=ASGITransport(app=app_instance, client=("203.0.113.9", 50001)), base_url="http://lb.example"
    )
    async with guest:
        login = await guest.post("/api/dashboard-auth/guest/login", json={})
        assert login.status_code == 200
        assert login.json()["role"] == "guest"
        assert login.json()["accessSummary"] is None
        assert login.json()["user"] is None
        assert login.json()["permissions"] == ["read", "accounts:read:all", "dashboard:read:all"]
        me = await guest.get("/api/dashboard-auth/me")
        assert me.status_code == 401
        assert me.json()["error"]["code"] == "user_account_required"


@pytest.mark.asyncio
async def test_login_resolves_the_sole_user_and_accepts_explicit_usernames(async_client):
    await _setup(async_client)
    await async_client.post("/api/dashboard-auth/logout", json={})

    implicit = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert implicit.status_code == 200
    assert implicit.json()["user"]["username"] == "admin"
    assert (await _compat_user()).last_login_at is not None

    explicit = await async_client.post(
        "/api/dashboard-auth/password/login", json={"username": "  ADMIN ", "password": "password123"}
    )
    assert explicit.status_code == 200

    wrong_user = await async_client.post(
        "/api/dashboard-auth/password/login", json={"username": "nobody", "password": "password123"}
    )
    wrong_password = await async_client.post(
        "/api/dashboard-auth/password/login", json={"username": "admin", "password": "wrong-password"}
    )
    assert wrong_user.status_code == wrong_password.status_code == 401
    assert wrong_user.json() == wrong_password.json()
    assert wrong_user.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_requires_username_once_a_second_account_holds_a_password(async_client):
    await _clear_rate_limit_attempts("password")
    await _setup(async_client)
    session = await async_client.get("/api/dashboard-auth/session")
    assert session.json()["login"]["usernameField"] == "hidden"

    await _insert_user("ops")
    session = await async_client.get("/api/dashboard-auth/session")
    assert session.json()["login"]["usernameField"] == "shown"
    assert session.json()["accessSummary"]["usersTotal"] == 2
    assert session.json()["accessSummary"]["nonAdminUsers"] == 1
    await async_client.post("/api/dashboard-auth/logout", json={})

    ambiguous = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert ambiguous.status_code == 422
    assert ambiguous.json()["error"]["code"] == "username_required"

    # The 422 spent no rate-limit budget: eight real failures are still allowed.
    for _ in range(8):
        response = await async_client.post(
            "/api/dashboard-auth/password/login", json={"username": "ops", "password": "wrong"}
        )
        assert response.status_code == 401
    limited = await async_client.post("/api/dashboard-auth/password/login", json={"username": "ops", "password": "x"})
    assert limited.status_code == 429

    await _clear_rate_limit_attempts("password")
    ops_login = await async_client.post(
        "/api/dashboard-auth/password/login", json={"username": "ops", "password": "second-password-1"}
    )
    assert ops_login.status_code == 200
    assert ops_login.json()["user"]["role"]["slug"] == "operator"
    assert ops_login.json()["accessSummary"] is None
    assert ops_login.json()["assignableRoleIds"] == []
    assert "users:manage:all" not in ops_login.json()["permissions"]
    assert "write" in ops_login.json()["permissions"]


@pytest.mark.asyncio
async def test_password_change_reissues_this_cookie_and_revokes_the_others(async_client, app_instance):
    await _setup(async_client)
    old_cookie = async_client.cookies.get(DASHBOARD_SESSION_COOKIE)
    assert old_cookie

    change = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": "password123", "newPassword": "new-password-456"},
    )
    assert change.status_code == 200
    new_cookie = async_client.cookies.get(DASHBOARD_SESSION_COOKIE)
    assert new_cookie and new_cookie != old_cookie
    assert (await async_client.get("/api/settings")).status_code == 200

    async with _second_client(app_instance) as other:
        other.cookies.set(DASHBOARD_SESSION_COOKIE, old_cookie)
        stale = await other.get("/api/settings")
        assert stale.status_code == 401
        assert stale.json()["error"]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_logout_all_revokes_every_session_of_the_account(async_client, app_instance):
    await _setup(async_client)
    async with _second_client(app_instance) as other:
        login = await other.post("/api/dashboard-auth/password/login", json={"password": "password123"})
        assert login.status_code == 200
        assert (await other.get("/api/settings")).status_code == 200

        revoked = await async_client.post("/api/dashboard-auth/logout-all", json={})
        assert revoked.status_code == 200
        assert async_client.cookies.get(DASHBOARD_SESSION_COOKIE) is None

        assert (await other.get("/api/settings")).status_code == 401
        again = await other.post("/api/dashboard-auth/logout-all", json={})
        assert again.status_code == 401

    relogin = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert relogin.status_code == 200
    assert (await async_client.get("/api/settings")).status_code == 200


@pytest.mark.asyncio
async def test_disabled_account_and_generation_bump_invalidate_cookies(async_client):
    await _setup(async_client)
    user = await _compat_user()
    assert (await async_client.get("/api/settings")).status_code == 200

    await _update_user(user.id, session_generation=user.session_generation + 1)
    stale = await async_client.get("/api/settings")
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "authentication_required"
    session = await async_client.get("/api/dashboard-auth/session")
    assert session.json()["authenticated"] is False
    assert session.json()["user"] is None

    relogin = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert relogin.status_code == 200

    # A second account keeps the install password-protected while the admin is disabled
    # (disabling the last admin is refused by the user-management API of a later change).
    await _insert_user("ops")
    await _update_user(user.id, status="disabled")
    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "authentication_required"
    denied = await async_client.post(
        "/api/dashboard-auth/password/login", json={"username": "admin", "password": "password123"}
    )
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "invalid_credentials"
    # With the admin disabled, "ops" is the only account that can sign in, so the username is implied again.
    implicit = await async_client.post("/api/dashboard-auth/password/login", json={"password": "second-password-1"})
    assert implicit.status_code == 200
    assert implicit.json()["user"]["username"] == "ops"


@pytest.mark.asyncio
async def test_previous_release_cookie_format_is_rejected(async_client):
    await _setup(async_client)
    v1_payload = {"exp": int(time.time()) + 3600, "pw": True, "tv": True, "role": "admin", "gv": False}
    v1_cookie = TokenEncryptor().encrypt(json.dumps(v1_payload, separators=(",", ":"))).decode("ascii")
    async_client.cookies.set(DASHBOARD_SESSION_COOKIE, v1_cookie)

    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "authentication_required"
    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_remove_password_is_refused_while_other_accounts_exist(async_client):
    await _setup(async_client)
    other = await _insert_user("ops")

    refused = await async_client.request("DELETE", "/api/dashboard-auth/password", json={"password": "password123"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "other_users_exist"
    assert (await async_client.get("/api/settings")).status_code == 200

    async with SessionLocal() as session:
        row = await session.get(DashboardUser, other.id)
        assert row is not None
        await session.delete(row)
        await session.commit()
    await get_dashboard_users_cache().invalidate()

    removed = await async_client.request("DELETE", "/api/dashboard-auth/password", json={"password": "password123"})
    assert removed.status_code == 200
    user = await _compat_user()
    assert user.password_hash is None
    session_payload = (await async_client.get("/api/dashboard-auth/session")).json()
    assert session_payload["passwordRequired"] is False
    assert session_payload["authenticated"] is True  # local implicit admin again
    assert session_payload["user"] is None
    assert session_payload["login"]["usernameField"] == "shown"

    # First-run setup re-arms the same account rather than failing on the unique username.
    again = await _setup(async_client, "password456")
    assert again["user"]["id"] == user.id


@pytest.mark.asyncio
async def test_legacy_credential_columns_are_not_consulted(async_client, monkeypatch):
    """Spec: with zero accounts the install is passwordless even if the legacy column says otherwise."""

    await _seed_legacy_password_hash("$2b$stale-legacy-only")

    assert (await async_client.get("/api/settings")).status_code == 200
    local = (await async_client.get("/api/dashboard-auth/session")).json()
    assert local["passwordRequired"] is False
    assert local["authenticated"] is True
    assert local["user"] is None

    monkeypatch.setattr(dashboard_auth_api_module, "is_local_request", lambda _request: False)
    remote = (await async_client.get("/api/dashboard-auth/session")).json()
    assert remote["bootstrapRequired"] is True
    assert remote["authenticated"] is False
    # Team facts never reach an unauthenticated caller.
    assert remote["accessSummary"] is None
    assert remote["assignableRoleIds"] == []
    assert remote["user"] is None
    assert remote["role"] == "admin" and remote["permissions"][:2] == ["read", "write"]


@pytest.mark.asyncio
async def test_stale_legacy_hash_does_not_block_first_run_setup(async_client):
    """A legacy-only hash left by a late previous-release write must not wedge setup forever."""

    await _seed_legacy_password_hash("$2b$stale-legacy-only")

    payload = await _setup(async_client)
    assert payload["user"]["username"] == "admin"
    user = await _compat_user()
    assert user.password_hash is not None
    assert await _legacy_password_hash() == user.password_hash  # mirrored unconditionally

    await async_client.post("/api/dashboard-auth/logout", json={})
    login = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_login_username_is_bounded_and_malformed_names_stay_out_of_the_audit_log(async_client):
    await _setup(async_client)

    too_long = await async_client.post(
        "/api/dashboard-auth/password/login", json={"username": "x" * 65, "password": "password123"}
    )
    assert too_long.status_code == 422

    malformed = await async_client.post(
        "/api/dashboard-auth/password/login", json={"username": "ad\x01min", "password": "password123"}
    )
    assert malformed.status_code == 401
    assert malformed.json()["error"]["code"] == "invalid_credentials"
    assert await drain_audit_log_tasks(5.0)

    async with SessionLocal() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == "login_failed"))).scalars().all()
    assert len(rows) == 1
    details = json.loads(rows[0].details or "{}")
    assert details["username"] is None
    assert details["reason"] == "invalid_username"
    assert "\x01" not in (rows[0].details or "")


_PASSWORDLESS_STATE = LocalAuthState(
    any_user=False,
    active_users=0,
    active_local_password_users=0,
    requires_auth=False,
    sole_local_password_user_id=None,
)


@pytest.mark.asyncio
async def test_first_password_setup_is_compare_and_set(async_client, app_instance, monkeypatch):
    """Two setups that both pass the auth-state check: exactly one wins, and only its password works."""

    first = await _setup(async_client, "first-password-1")

    # Deterministic race: the second request believes the install is still passwordless.
    with monkeypatch.context() as patched:

        async def passwordless(self: DashboardUsersRepository) -> LocalAuthState:
            return _PASSWORDLESS_STATE

        patched.setattr(DashboardUsersRepository, "local_auth_state", passwordless)
        async with _second_client(app_instance) as loser:
            second = await loser.post("/api/dashboard-auth/password/setup", json={"password": "second-password-2"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "password_already_configured"
    assert first["user"]["username"] == "admin"

    await async_client.post("/api/dashboard-auth/logout", json={})
    denied = await async_client.post("/api/dashboard-auth/password/login", json={"password": "second-password-2"})
    assert denied.status_code == 401
    allowed = await async_client.post("/api/dashboard-auth/password/login", json={"password": "first-password-1"})
    assert allowed.status_code == 200
    user = await _compat_user()
    assert await _legacy_password_hash() == user.password_hash


@pytest.mark.asyncio
async def test_concurrent_first_password_setups_admit_exactly_one(async_client, app_instance):
    async with _second_client(app_instance) as other:
        first, second = await asyncio.gather(
            async_client.post("/api/dashboard-auth/password/setup", json={"password": "first-password-1"}),
            other.post("/api/dashboard-auth/password/setup", json={"password": "second-password-2"}),
        )
    outcomes = sorted([first.status_code, second.status_code])
    assert outcomes == [200, 409], (first.text, second.text)
    winner_password = "first-password-1" if first.status_code == 200 else "second-password-2"
    loser_password = "second-password-2" if winner_password == "first-password-1" else "first-password-1"
    async with SessionLocal() as session:
        count = (await session.execute(select(DashboardUser))).scalars().all()
    assert len(count) == 1

    async with _second_client(app_instance) as fresh:
        assert (
            await fresh.post("/api/dashboard-auth/password/login", json={"password": loser_password})
        ).status_code == 401
        assert (
            await fresh.post("/api/dashboard-auth/password/login", json={"password": winner_password})
        ).status_code == 200


@pytest.mark.asyncio
async def test_guest_cookie_is_bound_to_the_generation_it_was_verified_against(async_client, app_instance, monkeypatch):
    """A guest password enabled between credential check and cookie minting must kill that cookie."""

    await _setup(async_client)
    current = (await async_client.get("/api/settings")).json()
    assert (await async_client.put("/api/settings", json={**current, "guestAccessEnabled": True})).status_code == 200
    original_verify = dashboard_auth_service_module.DashboardAuthService.verify_guest_password
    seen: dict[str, int] = {}

    async def verify_then_enable_password(self, password, *, actor_ip=None):
        verification = await original_verify(self, password, actor_ip=actor_ip)
        seen["generation"] = verification.guest_session_generation
        # An admin enables a guest password (and thereby bumps the generation) right after the check.
        async with SessionLocal() as session:
            row = await SettingsRepository(session).get_or_create()
            row.guest_password_hash = bcrypt.hashpw(b"guest-secret-1", bcrypt.gensalt(4)).decode()
            row.guest_session_generation += 1
            await session.commit()
        await get_settings_cache().invalidate()
        return verification

    monkeypatch.setattr(
        dashboard_auth_service_module.DashboardAuthService, "verify_guest_password", verify_then_enable_password
    )
    guest = AsyncClient(
        transport=ASGITransport(app=app_instance, client=("203.0.113.9", 50001)), base_url="http://lb.example"
    )
    async with guest:
        login = await guest.post("/api/dashboard-auth/guest/login", json={})
        assert login.status_code == 200
        state = get_dashboard_session_store().get(guest.cookies.get(DASHBOARD_SESSION_COOKIE))
        assert state is not None and state.guest_session_generation == seen["generation"]

        blocked = await guest.get("/api/settings")
        assert blocked.status_code == 401
        session = await guest.get("/api/dashboard-auth/session")
        assert session.json()["authenticated"] is False
        assert session.json()["guestPasswordRequired"] is True


@pytest.mark.asyncio
async def test_roles_without_all_scope_dashboard_read_are_refused_by_the_session_gate(async_client):
    await _setup(async_client)
    await _insert_user("mem", slug=PresetRoleSlug.MEMBER)
    await async_client.post("/api/dashboard-auth/logout", json={})

    login = await async_client.post(
        "/api/dashboard-auth/password/login", json={"username": "mem", "password": "second-password-1"}
    )
    assert login.status_code == 200
    assert login.json()["user"]["role"]["slug"] == "member"
    assert login.json()["permissions"] == ["read", "api_keys:read:own", "api_keys:write:own", "dashboard:read:own"]

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True

    for path in ("/api/accounts", "/api/request-logs"):
        refused = await async_client.get(path)
        assert refused.status_code == 403, (path, refused.text)
        assert refused.json()["error"]["code"] == "permission_required"
        assert refused.json()["error"]["param"] == "dashboard:read"
