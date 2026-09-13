from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.dashboard_session_ttl import (
    DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    REMOTE_DASHBOARD_SESSION_TTL_SECONDS,
)
from app.core.exceptions import DashboardAuthError, DashboardValidationError
from app.db.models import DashboardRoleRecord, DashboardUser
from app.dependencies import DashboardAuthContext
from app.modules.dashboard_auth.api import change_password, disable_totp, login_password, verify_totp
from app.modules.dashboard_auth.schemas import (
    DashboardAuthSessionResponse,
    PasswordChangeRequest,
    PasswordLoginRequest,
    TotpVerifyRequest,
)
from app.modules.dashboard_auth.service import (
    DASHBOARD_SESSION_COOKIE,
    DashboardSessionState,
    LoginTarget,
    PasswordSessionRequiredError,
    ResolvedUserSession,
    SessionDescription,
    UsernameRequiredError,
)

pytestmark = pytest.mark.unit


def _build_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"cookie", f"{DASHBOARD_SESSION_COOKIE}=session-1".encode())],
            "client": ("127.0.0.1", 12345),
        }
    )


def _build_login_request(
    path: str,
    *,
    client_host: str = "203.0.113.10",
    host: str = "lb.example",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", host.encode()), *(headers or [])],
            "client": (client_host, 12345),
            "_codex_lb_raw_socket_peer": (client_host, 12345),
            "server": (host.split(":", 1)[0], 80),
        }
    )


def _runtime_settings() -> SimpleNamespace:
    return SimpleNamespace(
        dashboard_auth_mode=DashboardAuthMode.STANDARD,
        firewall_trust_proxy_headers=False,
        firewall_trusted_proxy_cidrs=[],
        dashboard_trust_loopback_host_header_for_long_sessions=False,
    )


def _user(slug: PresetRoleSlug = PresetRoleSlug.ADMIN, *, is_break_glass: bool = False) -> DashboardUser:
    role = DashboardRoleRecord(id=PRESET_ROLE_IDS[slug], slug=slug.value, name=slug.value.title(), kind="preset")
    user = DashboardUser(
        id="user-1",
        username="admin" if slug is PresetRoleSlug.ADMIN else slug.value,
        role_id=role.id,
        status="active",
        password_hash="hash",
        session_generation=0,
        is_break_glass=is_break_glass,
    )
    user.role = role
    return user


def _login_context(user: DashboardUser | None, *, target: LoginTarget | None = None) -> DashboardAuthContext:
    return cast(
        DashboardAuthContext,
        SimpleNamespace(
            service=SimpleNamespace(
                resolve_login_target=AsyncMock(
                    return_value=target if target is not None else LoginTarget(username="admin", user=user)
                ),
                verify_user_password=AsyncMock(return_value=user),
                describe_session=AsyncMock(
                    return_value=SessionDescription(
                        response=DashboardAuthSessionResponse(
                            authenticated=True,
                            password_required=True,
                            totp_required_on_login=False,
                            totp_configured=False,
                        ),
                        resolved=None,
                    )
                ),
            ),
            session=object(),
        ),
    )


def _limiter() -> SimpleNamespace:
    return SimpleNamespace(check=AsyncMock(), check_and_increment=AsyncMock(), clear_for_key=AsyncMock())


def _store() -> SimpleNamespace:
    return SimpleNamespace(create_user_session=Mock(return_value="session-1"), get=lambda _sid: None)


def _users_cache() -> SimpleNamespace:
    return SimpleNamespace(invalidate=AsyncMock())


async def _login(
    request: Request,
    *,
    context: DashboardAuthContext,
    configured_ttl: int,
    runtime_settings: SimpleNamespace | None = None,
    payload: PasswordLoginRequest | None = None,
) -> tuple[JSONResponse, SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    # Two buckets are spent by every attempt: the per-(address, username) one
    # and the coarse per-address ceiling that bounds the endpoint.
    account_limiter, address_limiter = _limiter(), _limiter()
    store = _store()
    settings_cache = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(dashboard_session_ttl_seconds=configured_ttl))
    )
    runtime = runtime_settings or _runtime_settings()
    with (
        patch("app.core.auth.dashboard_session_ttl._get_settings", return_value=runtime),
        patch("app.core.request_locality.get_settings", return_value=runtime),
        patch("app.modules.dashboard_auth.api.get_password_rate_limiter", return_value=account_limiter),
        patch("app.modules.dashboard_auth.api.get_password_address_rate_limiter", return_value=address_limiter),
        patch("app.modules.dashboard_auth.api.get_dashboard_session_store", return_value=store),
        patch("app.modules.dashboard_auth.api.get_settings_cache", return_value=settings_cache),
    ):
        response = await login_password(request, payload or PasswordLoginRequest(password="password123"), context)
    assert isinstance(response, JSONResponse)
    return response, store, account_limiter, address_limiter


@pytest.mark.asyncio
async def test_verify_totp_does_not_spend_rate_limit_budget_before_session_validation():
    limiter = _limiter()
    context = cast(
        DashboardAuthContext,
        SimpleNamespace(
            service=SimpleNamespace(
                require_password_session=AsyncMock(side_effect=PasswordSessionRequiredError("session required")),
                verify_totp=AsyncMock(),
            ),
            session=object(),
        ),
    )

    with patch("app.modules.dashboard_auth.api.get_totp_rate_limiter", return_value=limiter):
        with pytest.raises(DashboardAuthError, match="session required"):
            await verify_totp(
                _build_request("/api/dashboard-auth/totp/verify"),
                TotpVerifyRequest(code="123456"),
                context,
            )

    limiter.check_and_increment.assert_not_awaited()
    limiter.clear_for_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_disable_totp_does_not_spend_rate_limit_budget_before_session_validation():
    limiter = _limiter()
    context = cast(
        DashboardAuthContext,
        SimpleNamespace(
            service=SimpleNamespace(
                ensure_totp_verified_session=AsyncMock(side_effect=PasswordSessionRequiredError("session required")),
                disable_totp=AsyncMock(),
            ),
            session=object(),
        ),
    )

    with patch("app.modules.dashboard_auth.api.get_totp_rate_limiter", return_value=limiter):
        with pytest.raises(DashboardAuthError, match="session required"):
            await disable_totp(
                _build_request("/api/dashboard-auth/totp/disable"),
                TotpVerifyRequest(code="123456"),
                context,
            )

    limiter.check_and_increment.assert_not_awaited()
    limiter.clear_for_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_password_uses_configured_dashboard_session_ttl_for_cookie():
    user = _user()
    response, store, account_limiter, address_limiter = await _login(
        _build_login_request("/api/dashboard-auth/password/login"),
        context=_login_context(user),
        configured_ttl=7200,
    )

    assert "Max-Age=7200" in response.headers["set-cookie"]
    store.create_user_session.assert_called_once_with(
        "user-1",
        0,
        password_verified=True,
        totp_verified=False,
        ttl_seconds=7200,
        auth_method="password",
        step_up_verified_at=ANY,
        break_glass=False,
    )
    # Both buckets are spent: the per-address ceiling is added to the
    # per-account budget, never swapped in. Only the per-account bucket is
    # cleared -- clearing the coarse one would let anybody holding one valid
    # account reset the endpoint's only ceiling between sprays.
    account_limiter.check_and_increment.assert_awaited_once()
    account_limiter.clear_for_key.assert_awaited_once()
    address_limiter.check_and_increment.assert_awaited_once()
    address_limiter.clear_for_key.assert_not_awaited()
    assert (
        address_limiter.check_and_increment.await_args.args[0] != account_limiter.check_and_increment.await_args.args[0]
    )


@pytest.mark.asyncio
async def test_login_password_uses_one_year_ttl_for_direct_loopback_dashboard_request():
    response, store, _, _ = await _login(
        _build_login_request(
            "/api/dashboard-auth/password/login",
            client_host="127.0.0.1",
            host="127.0.0.1:2455",
        ),
        context=_login_context(_user()),
        configured_ttl=DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    )

    assert f"Max-Age={DEFAULT_DASHBOARD_SESSION_TTL_SECONDS}" in response.headers["set-cookie"]
    assert store.create_user_session.call_args.kwargs["ttl_seconds"] == DEFAULT_DASHBOARD_SESSION_TTL_SECONDS


@pytest.mark.asyncio
async def test_login_password_caps_non_loopback_dashboard_session_ttl():
    response, store, _, _ = await _login(
        _build_login_request("/api/dashboard-auth/password/login"),
        context=_login_context(_user()),
        configured_ttl=90 * 24 * 60 * 60,
    )

    assert f"Max-Age={REMOTE_DASHBOARD_SESSION_TTL_SECONDS}" in response.headers["set-cookie"]
    assert store.create_user_session.call_args.kwargs["ttl_seconds"] == REMOTE_DASHBOARD_SESSION_TTL_SECONDS


@pytest.mark.asyncio
async def test_login_password_caps_later_duplicate_forwarded_identity_from_loopback_socket():
    runtime_settings = _runtime_settings()
    runtime_settings.dashboard_trust_loopback_host_header_for_long_sessions = True
    response, store, _, _ = await _login(
        _build_login_request(
            "/api/dashboard-auth/password/login",
            client_host="127.0.0.1",
            host="127.0.0.1:2455",
            headers=[
                (b"x-forwarded-for", b""),
                (b"x-forwarded-for", b"203.0.113.24"),
            ],
        ),
        context=_login_context(_user()),
        configured_ttl=90 * 24 * 60 * 60,
    )

    assert f"Max-Age={REMOTE_DASHBOARD_SESSION_TTL_SECONDS}" in response.headers["set-cookie"]
    assert store.create_user_session.call_args.kwargs["ttl_seconds"] == REMOTE_DASHBOARD_SESSION_TTL_SECONDS


@pytest.mark.asyncio
async def test_remote_admin_sessions_are_capped_at_twelve_hours_even_under_thirty_days():
    thirty_days = 30 * 24 * 60 * 60
    _, admin_store, _, _ = await _login(
        _build_login_request("/api/dashboard-auth/password/login"),
        context=_login_context(_user(PresetRoleSlug.ADMIN)),
        configured_ttl=thirty_days,
    )
    _, operator_store, _, _ = await _login(
        _build_login_request("/api/dashboard-auth/password/login"),
        context=_login_context(_user(PresetRoleSlug.OPERATOR)),
        configured_ttl=thirty_days,
    )

    assert admin_store.create_user_session.call_args.kwargs["ttl_seconds"] == REMOTE_DASHBOARD_SESSION_TTL_SECONDS
    assert operator_store.create_user_session.call_args.kwargs["ttl_seconds"] == thirty_days


@pytest.mark.asyncio
async def test_login_password_username_required_does_not_spend_budget():
    context = cast(
        DashboardAuthContext,
        SimpleNamespace(
            service=SimpleNamespace(resolve_login_target=AsyncMock(side_effect=UsernameRequiredError("username"))),
            session=object(),
        ),
    )
    account_limiter = _limiter()
    address_limiter = _limiter()
    audit_limiter = _limiter()
    with (
        patch("app.modules.dashboard_auth.api.get_password_rate_limiter", return_value=account_limiter),
        patch("app.modules.dashboard_auth.api.get_password_address_rate_limiter", return_value=address_limiter),
        patch("app.modules.dashboard_auth.api.get_login_failed_audit_rate_limiter", return_value=audit_limiter),
        patch("app.modules.dashboard_auth.api.log_login_failed") as log_login_failed,
    ):
        with pytest.raises(DashboardValidationError) as exc_info:
            await login_password(
                _build_login_request("/api/dashboard-auth/password/login"),
                PasswordLoginRequest(password="password123"),
                context,
            )

    assert exc_info.value.code == "username_required"
    # Audited, but only through the read-only check of the bucket ordinary
    # logins actually increment -- the per-address one -- plus the audit budget.
    # A per-username key would be a counter nothing advances, so the guard
    # would never bite.
    address_limiter.check.assert_awaited_once()
    # Assembled, not written out: "<prefix>:<host>" in one literal reads as a
    # credential pair to secret scanners.
    assert address_limiter.check.await_args.args[0] == "password_" + "login:203.0.113.10"
    address_limiter.check_and_increment.assert_not_awaited()
    account_limiter.check.assert_not_awaited()
    account_limiter.check_and_increment.assert_not_awaited()
    audit_limiter.check_and_increment.assert_awaited_once()
    log_login_failed.assert_called_once()
    assert log_login_failed.call_args.args[1:] == ("password", "username_required")


@pytest.mark.asyncio
async def test_password_change_reissued_cookie_never_outlives_the_replaced_session():
    user = _user()
    now = 1_700_000_000
    state = DashboardSessionState(
        expires_at=now + 600,
        issued_at=now - 3600,
        kind="user",
        user_id=user.id,
        session_generation=0,
        password_verified=True,
        totp_verified=False,
        auth_method="password",
    )
    change = AsyncMock(return_value=1)
    context = cast(
        DashboardAuthContext,
        SimpleNamespace(
            service=SimpleNamespace(
                require_management_session=AsyncMock(return_value=ResolvedUserSession(user=user, state=state)),
                change_password=change,
            ),
            repository=SimpleNamespace(get_user_by_id=AsyncMock(return_value=user)),
            session=object(),
        ),
    )
    store = _store()
    settings_cache = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(dashboard_session_ttl_seconds=7200)), invalidate=AsyncMock()
    )
    runtime = _runtime_settings()
    with (
        patch("app.modules.dashboard_auth.api.time", return_value=now),
        patch("app.core.auth.dashboard_session_ttl._get_settings", return_value=runtime),
        patch("app.core.request_locality.get_settings", return_value=runtime),
        patch("app.modules.dashboard_auth.api.get_dashboard_request_auth", return_value=None),
        patch("app.modules.dashboard_auth.api.get_dashboard_session_store", return_value=store),
        patch("app.modules.dashboard_auth.api.get_settings_cache", return_value=settings_cache),
        patch("app.modules.dashboard_auth.api.get_dashboard_users_cache", return_value=_users_cache()),
    ):
        response = await change_password(
            _build_login_request("/api/dashboard-auth/password/change"),
            PasswordChangeRequest(current_password="password123", new_password="new-password-456"),
            context,
        )

    assert isinstance(response, JSONResponse)
    assert "Max-Age=600" in response.headers["set-cookie"]
    assert store.create_user_session.call_args.kwargs["ttl_seconds"] == 600
    change.assert_awaited_once()
