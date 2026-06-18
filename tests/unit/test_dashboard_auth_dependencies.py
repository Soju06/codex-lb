from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

import app.core.auth.dependencies as auth_dependencies
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.exceptions import DashboardAuthError

pytestmark = pytest.mark.unit


def _build_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )


@pytest.mark.asyncio
async def test_validate_dashboard_session_blocks_passwordless_guest_fallback_in_trusted_header_mode(monkeypatch):
    request = _build_request("/api/settings")
    settings = SimpleNamespace(
        password_hash="admin-password-hash",
        totp_required_on_login=False,
        guest_access_enabled=True,
        guest_password_hash=None,
    )

    monkeypatch.setattr(auth_dependencies, "get_dashboard_request_auth", lambda _request: None)
    monkeypatch.setattr(auth_dependencies, "get_dashboard_request_auth_mode", lambda: DashboardAuthMode.TRUSTED_HEADER)
    monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _request: True)
    monkeypatch.setattr(
        auth_dependencies,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=settings)),
    )
    monkeypatch.setattr(
        auth_dependencies,
        "get_dashboard_session_store",
        lambda: SimpleNamespace(get=lambda _session_id: None),
    )

    with pytest.raises(DashboardAuthError, match="Reverse proxy authentication is required") as exc_info:
        await auth_dependencies.validate_dashboard_session(request)

    assert exc_info.value.code == "proxy_auth_required"
    assert getattr(request.state, "dashboard_principal", None) is None
