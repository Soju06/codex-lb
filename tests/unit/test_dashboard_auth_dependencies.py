from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

import app.core.auth.dependencies as auth_dependencies
from app.core.auth.dashboard_access import (
    PRESET_ROLE_IDS,
    DashboardRole,
    Permission,
    PresetRoleSlug,
    admin_principal,
    guest_principal,
)
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.exceptions import DashboardAuthError, DashboardPermissionError
from app.db.models import DashboardRoleRecord, DashboardUser
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, DashboardSessionState
from app.modules.dashboard_users.repository import LocalAuthState

pytestmark = pytest.mark.unit


def _build_request(path: str, *, cookie: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", f"{DASHBOARD_SESSION_COOKIE}={cookie}".encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
        }
    )


def _admin_user(**overrides: object) -> DashboardUser:
    role = DashboardRoleRecord(id=PRESET_ROLE_IDS[PresetRoleSlug.ADMIN], slug="admin", name="Admin", kind="preset")
    user = DashboardUser(
        id="user-1",
        username="admin",
        role_id=role.id,
        status="active",
        password_hash="hash",
        session_generation=2,
        is_break_glass=True,
    )
    user.role = role
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def _user_state(**overrides: Any) -> DashboardSessionState:
    fields: dict[str, Any] = {
        "expires_at": 2_000_000_000,
        "issued_at": 1_700_000_000,
        "kind": "user",
        "user_id": "user-1",
        "session_generation": 2,
        "password_verified": True,
        "totp_verified": False,
        "auth_method": "password",
    }
    fields.update(overrides)
    return DashboardSessionState(**fields)


def _auth_state(**overrides: Any) -> LocalAuthState:
    fields: dict[str, Any] = {
        "any_user": True,
        "active_users": 1,
        "active_local_password_users": 1,
        "requires_auth": True,
        "sole_local_password_user_id": "user-1",
    }
    fields.update(overrides)
    return LocalAuthState(**fields)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings: SimpleNamespace,
    auth_state: LocalAuthState,
    user: DashboardUser | None,
    state: DashboardSessionState | None,
    auth_mode: DashboardAuthMode = DashboardAuthMode.STANDARD,
    local: bool = True,
) -> None:
    monkeypatch.setattr(auth_dependencies, "get_dashboard_request_auth", lambda _request: None)
    monkeypatch.setattr(auth_dependencies, "get_dashboard_request_auth_mode", lambda: auth_mode)
    monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _request: local)
    monkeypatch.setattr(
        auth_dependencies, "get_settings_cache", lambda: SimpleNamespace(get=AsyncMock(return_value=settings))
    )
    monkeypatch.setattr(
        auth_dependencies,
        "get_dashboard_users_cache",
        lambda: SimpleNamespace(
            local_auth_state=AsyncMock(return_value=auth_state),
            get_user=AsyncMock(return_value=user),
        ),
    )
    monkeypatch.setattr(
        auth_dependencies, "get_dashboard_session_store", lambda: SimpleNamespace(get=lambda _session_id: state)
    )


def _settings(**overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "totp_required_on_login": False,
        "totp_required_for_admin_role": False,
        "guest_access_enabled": False,
        "guest_password_hash": None,
        "guest_session_generation": 0,
        "local_login_policy": "enabled",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.mark.asyncio
async def test_validate_dashboard_session_blocks_passwordless_guest_fallback_in_trusted_header_mode(monkeypatch):
    request = _build_request("/api/settings")
    _install(
        monkeypatch,
        settings=_settings(guest_access_enabled=True),
        auth_state=_auth_state(),
        user=None,
        state=None,
        auth_mode=DashboardAuthMode.TRUSTED_HEADER,
    )

    with pytest.raises(DashboardAuthError, match="Reverse proxy authentication is required") as exc_info:
        await auth_dependencies.validate_dashboard_session(request)

    assert exc_info.value.code == "proxy_auth_required"
    assert getattr(request.state, "dashboard_principal", None) is None


@pytest.mark.asyncio
async def test_user_session_yields_user_principal(monkeypatch):
    request = _build_request("/api/settings", cookie="sid")
    user = _admin_user()
    _install(monkeypatch, settings=_settings(), auth_state=_auth_state(), user=user, state=_user_state())

    principal = await auth_dependencies.validate_dashboard_session(request)

    assert principal.role == DashboardRole.ADMIN
    assert principal.user_id == "user-1"
    assert principal.username == "admin"
    assert principal.role_slug == "admin"
    assert principal.auth_method == "password"
    assert principal.totp_enrollment_required is False
    assert principal.has(Permission.USERS_MANAGE)
    assert request.state.dashboard_principal is principal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user", "state"),
    [
        (None, _user_state()),
        (_admin_user(status="disabled"), _user_state()),
        (_admin_user(status="invited"), _user_state()),
        (_admin_user(session_generation=3), _user_state(session_generation=2)),
        (_admin_user(), _user_state(password_verified=False)),
    ],
    ids=["missing-user", "disabled", "invited", "stale-generation", "password-not-verified"],
)
async def test_user_cookie_that_no_longer_binds_to_an_account_is_rejected(monkeypatch, user, state):
    request = _build_request("/api/settings", cookie="sid")
    _install(monkeypatch, settings=_settings(), auth_state=_auth_state(), user=user, state=state)

    with pytest.raises(DashboardAuthError) as exc_info:
        await auth_dependencies.validate_dashboard_session(request)

    assert exc_info.value.code == "authentication_required"


@pytest.mark.asyncio
async def test_totp_policy_with_secret_requires_verified_step(monkeypatch):
    request = _build_request("/api/settings", cookie="sid")
    user = _admin_user(totp_secret_encrypted=b"secret")
    _install(
        monkeypatch,
        settings=_settings(totp_required_on_login=True),
        auth_state=_auth_state(),
        user=user,
        state=_user_state(totp_verified=False),
    )

    with pytest.raises(DashboardAuthError) as exc_info:
        await auth_dependencies.validate_dashboard_session(request)
    assert exc_info.value.code == "totp_required"

    _install(
        monkeypatch,
        settings=_settings(totp_required_on_login=True),
        auth_state=_auth_state(),
        user=user,
        state=_user_state(totp_verified=True),
    )
    principal = await auth_dependencies.validate_dashboard_session(_build_request("/api/settings", cookie="sid"))
    assert principal.user_id == "user-1"


@pytest.mark.asyncio
async def test_totp_policy_without_secret_puts_user_in_enrollment_state(monkeypatch):
    user = _admin_user(totp_secret_encrypted=None)
    _install(
        monkeypatch,
        settings=_settings(totp_required_on_login=True),
        auth_state=_auth_state(),
        user=user,
        state=_user_state(),
    )

    with pytest.raises(DashboardPermissionError) as exc_info:
        await auth_dependencies.validate_dashboard_session(_build_request("/api/settings", cookie="sid"))
    assert exc_info.value.code == "totp_enrollment_required"

    self_service = _build_request("/api/dashboard-auth/session", cookie="sid")
    principal = await auth_dependencies.validate_dashboard_session(self_service)
    assert principal.totp_enrollment_required is True


@pytest.mark.asyncio
async def test_passwordless_local_install_is_implicit_admin(monkeypatch):
    request = _build_request("/api/settings")
    _install(
        monkeypatch,
        settings=_settings(),
        auth_state=_auth_state(
            any_user=False,
            active_users=0,
            active_local_password_users=0,
            requires_auth=False,
            sole_local_password_user_id=None,
        ),
        user=None,
        state=None,
    )

    principal = await auth_dependencies.validate_dashboard_session(request)

    assert principal.role == DashboardRole.ADMIN
    assert principal.user_id is None
    assert principal.auth_method == "local_bootstrap"


@pytest.mark.asyncio
async def test_passwordless_remote_install_requires_bootstrap(monkeypatch):
    request = _build_request("/api/settings")
    _install(
        monkeypatch,
        settings=_settings(),
        auth_state=_auth_state(
            any_user=False,
            active_users=0,
            active_local_password_users=0,
            requires_auth=False,
            sole_local_password_user_id=None,
        ),
        user=None,
        state=None,
        local=False,
    )

    with pytest.raises(DashboardAuthError) as exc_info:
        await auth_dependencies.validate_dashboard_session(request)
    assert exc_info.value.code == "bootstrap_required"


@pytest.mark.asyncio
async def test_guest_cookie_requires_current_generation(monkeypatch):
    guest_state = DashboardSessionState(
        expires_at=2_000_000_000, issued_at=1_700_000_000, kind="guest", guest_session_generation=1
    )
    _install(
        monkeypatch,
        settings=_settings(guest_access_enabled=True, guest_session_generation=1),
        auth_state=_auth_state(),
        user=None,
        state=guest_state,
    )
    principal = await auth_dependencies.validate_dashboard_session(_build_request("/api/settings", cookie="g"))
    assert principal.role == DashboardRole.GUEST

    _install(
        monkeypatch,
        settings=_settings(guest_access_enabled=True, guest_session_generation=2),
        auth_state=_auth_state(),
        user=None,
        state=guest_state,
        local=False,
    )
    # Stale generation: the cookie is ignored, but passwordless guest access still applies.
    principal = await auth_dependencies.validate_dashboard_session(_build_request("/api/settings", cookie="g"))
    assert principal.role == DashboardRole.GUEST


@pytest.mark.asyncio
async def test_require_dashboard_admin_access_rejects_guest(monkeypatch):
    request = _build_request("/api/conversation-archive/records")
    monkeypatch.setattr(
        auth_dependencies,
        "validate_dashboard_session",
        AsyncMock(return_value=guest_principal()),
    )

    with pytest.raises(DashboardPermissionError, match="conversations:read") as exc_info:
        await auth_dependencies.require_dashboard_admin_access(request)

    assert exc_info.value.code == "permission_required"


@pytest.mark.asyncio
async def test_require_dashboard_admin_access_allows_admin(monkeypatch):
    request = _build_request("/api/conversation-archive/records")
    principal = admin_principal(auth_mode=DashboardAuthMode.STANDARD)
    monkeypatch.setattr(
        auth_dependencies,
        "validate_dashboard_session",
        AsyncMock(return_value=principal),
    )

    assert await auth_dependencies.require_dashboard_admin_access(request) is principal
