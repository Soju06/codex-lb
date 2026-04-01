from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from app.core.exceptions import DashboardAuthError
from app.dependencies import DashboardAuthContext
from app.modules.dashboard_auth.api import disable_totp, verify_totp
from app.modules.dashboard_auth.schemas import TotpVerifyRequest
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
