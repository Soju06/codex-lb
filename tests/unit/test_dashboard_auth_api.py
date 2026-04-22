from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from starlette.requests import Request

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


def _build_login_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
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
        get=lambda _sid: SimpleNamespace(password_verified=True, totp_verified=False),
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

    assert response.headers["set-cookie"]
    assert "Max-Age=7200" in response.headers["set-cookie"]
    limiter.check_and_increment.assert_awaited_once()
    limiter.clear_for_key.assert_awaited_once()
