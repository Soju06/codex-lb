from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core.auth.dashboard_access import DashboardRole
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.dashboard_session_ttl import (
    DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    REMOTE_DASHBOARD_SESSION_TTL_SECONDS,
)
from app.core.exceptions import DashboardAuthError
from app.dependencies import DashboardAuthContext
from app.modules.dashboard_auth.api import disable_totp, login_password, verify_totp
from app.modules.dashboard_auth.schemas import DashboardAuthSessionResponse, PasswordLoginRequest, TotpVerifyRequest
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, PasswordSessionRequiredError

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


def _build_login_request(path: str, *, client_host: str = "203.0.113.10", host: str = "lb.example") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", host.encode())],
            "client": (client_host, 12345),
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


@pytest.mark.asyncio
async def test_verify_totp_does_not_spend_rate_limit_budget_before_session_validation():
    limiter = SimpleNamespace(
        check_and_increment=AsyncMock(),
        clear_for_key=AsyncMock(),
    )
    context = cast(
        DashboardAuthContext,
        SimpleNamespace(
            service=SimpleNamespace(
                ensure_active_password_session=AsyncMock(side_effect=PasswordSessionRequiredError("session required")),
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
    limiter = SimpleNamespace(
        check_and_increment=AsyncMock(),
        clear_for_key=AsyncMock(),
    )
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
    limiter = SimpleNamespace(
        check_and_increment=AsyncMock(),
        clear_for_key=AsyncMock(),
    )
    session_store = SimpleNamespace(
        create=Mock(return_value="session-1"),
        get=lambda _sid: SimpleNamespace(
            password_verified=True,
            totp_verified=False,
            role=DashboardRole.ADMIN,
            guest_verified=False,
        ),
    )
    context = cast(
        DashboardAuthContext,
        SimpleNamespace(
            service=SimpleNamespace(
                verify_password=AsyncMock(),
                get_session_state=AsyncMock(
                    return_value=DashboardAuthSessionResponse(
                        authenticated=True,
                        password_required=True,
                        totp_required_on_login=False,
                        totp_configured=False,
                    )
                ),
            ),
            session=object(),
        ),
    )
    settings = SimpleNamespace(password_hash="hash", dashboard_session_ttl_seconds=7200)
    settings_cache = SimpleNamespace(get=AsyncMock(return_value=settings))

    with patch("app.modules.dashboard_auth.api.get_password_rate_limiter", return_value=limiter):
        with patch("app.modules.dashboard_auth.api.get_dashboard_session_store", return_value=session_store):
            with patch("app.modules.dashboard_auth.api.get_settings_cache", return_value=settings_cache):
                response = await login_password(
                    _build_login_request("/api/dashboard-auth/password/login"),
                    PasswordLoginRequest(password="password123"),
                    context,
                )

    assert isinstance(response, JSONResponse)
    assert response.headers["set-cookie"]
    assert "Max-Age=7200" in response.headers["set-cookie"]
    session_store.create.assert_called_once_with(
        password_verified=True,
        totp_verified=False,
        ttl_seconds=7200,
        role=DashboardRole.ADMIN,
        guest_verified=False,
    )
    limiter.check_and_increment.assert_awaited_once()
    limiter.clear_for_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_password_uses_one_year_ttl_for_direct_loopback_dashboard_request():
    limiter = SimpleNamespace(
        check_and_increment=AsyncMock(),
        clear_for_key=AsyncMock(),
    )
    session_store = SimpleNamespace(
        create=Mock(return_value="session-1"),
        get=lambda _sid: SimpleNamespace(
            password_verified=True,
            totp_verified=False,
            role=DashboardRole.ADMIN,
            guest_verified=False,
        ),
    )
    context = cast(
        DashboardAuthContext,
        SimpleNamespace(
            service=SimpleNamespace(
                verify_password=AsyncMock(),
                get_session_state=AsyncMock(
                    return_value=DashboardAuthSessionResponse(
                        authenticated=True,
                        password_required=True,
                        totp_required_on_login=False,
                        totp_configured=False,
                    )
                ),
            ),
            session=object(),
        ),
    )
    settings = SimpleNamespace(
        password_hash="hash",
        dashboard_session_ttl_seconds=DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    )
    settings_cache = SimpleNamespace(get=AsyncMock(return_value=settings))
    runtime_settings = _runtime_settings()

    with (
        patch("app.core.auth.dashboard_session_ttl._get_settings", return_value=runtime_settings),
        patch("app.core.request_locality.get_settings", return_value=runtime_settings),
        patch("app.modules.dashboard_auth.api.get_password_rate_limiter", return_value=limiter),
        patch("app.modules.dashboard_auth.api.get_dashboard_session_store", return_value=session_store),
        patch("app.modules.dashboard_auth.api.get_settings_cache", return_value=settings_cache),
    ):
        response = await login_password(
            _build_login_request(
                "/api/dashboard-auth/password/login",
                client_host="127.0.0.1",
                host="127.0.0.1:2455",
            ),
            PasswordLoginRequest(password="password123"),
            context,
        )

    assert isinstance(response, JSONResponse)
    assert f"Max-Age={DEFAULT_DASHBOARD_SESSION_TTL_SECONDS}" in response.headers["set-cookie"]
    session_store.create.assert_called_once_with(
        password_verified=True,
        totp_verified=False,
        ttl_seconds=DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
        role=DashboardRole.ADMIN,
        guest_verified=False,
    )
    limiter.check_and_increment.assert_awaited_once()
    limiter.clear_for_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_password_caps_non_loopback_dashboard_session_ttl():
    limiter = SimpleNamespace(
        check_and_increment=AsyncMock(),
        clear_for_key=AsyncMock(),
    )
    session_store = SimpleNamespace(
        create=Mock(return_value="session-1"),
        get=lambda _sid: SimpleNamespace(
            password_verified=True,
            totp_verified=False,
            role=DashboardRole.ADMIN,
            guest_verified=False,
        ),
    )
    context = cast(
        DashboardAuthContext,
        SimpleNamespace(
            service=SimpleNamespace(
                verify_password=AsyncMock(),
                get_session_state=AsyncMock(
                    return_value=DashboardAuthSessionResponse(
                        authenticated=True,
                        password_required=True,
                        totp_required_on_login=False,
                        totp_configured=False,
                    )
                ),
            ),
            session=object(),
        ),
    )
    settings = SimpleNamespace(password_hash="hash", dashboard_session_ttl_seconds=90 * 24 * 60 * 60)
    settings_cache = SimpleNamespace(get=AsyncMock(return_value=settings))
    runtime_settings = _runtime_settings()

    with (
        patch("app.core.auth.dashboard_session_ttl._get_settings", return_value=runtime_settings),
        patch("app.core.request_locality.get_settings", return_value=runtime_settings),
        patch("app.modules.dashboard_auth.api.get_password_rate_limiter", return_value=limiter),
        patch("app.modules.dashboard_auth.api.get_dashboard_session_store", return_value=session_store),
        patch("app.modules.dashboard_auth.api.get_settings_cache", return_value=settings_cache),
    ):
        response = await login_password(
            _build_login_request("/api/dashboard-auth/password/login"),
            PasswordLoginRequest(password="password123"),
            context,
        )

    assert isinstance(response, JSONResponse)
    assert f"Max-Age={REMOTE_DASHBOARD_SESSION_TTL_SECONDS}" in response.headers["set-cookie"]
    session_store.create.assert_called_once_with(
        password_verified=True,
        totp_verified=False,
        ttl_seconds=REMOTE_DASHBOARD_SESSION_TTL_SECONDS,
        role=DashboardRole.ADMIN,
        guest_verified=False,
    )
    limiter.check_and_increment.assert_awaited_once()
    limiter.clear_for_key.assert_awaited_once()
