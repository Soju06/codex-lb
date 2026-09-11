"""Step-up primitives: the freshness window, the account's methods, and the step-up cookie."""

from __future__ import annotations

import json
from typing import Any

import pytest

import app.modules.dashboard_auth.service as service_module
from app.core.auth.dashboard_access import PRIVILEGED_PERMISSIONS, STEP_UP_PERMISSIONS, Permission
from app.core.auth.step_up import STEP_UP_MAX_AGE_SECONDS, is_step_up_fresh, step_up_expires_at, step_up_methods
from app.core.crypto import TokenEncryptor
from app.db.models import DashboardUser
from app.modules.dashboard_auth.service import DashboardSessionStore, StepUpCookieStore

pytestmark = pytest.mark.unit

_NOW = 1_700_000_000


def _user(*, password: bool, totp: bool) -> DashboardUser:
    return DashboardUser(
        id="user-1",
        username="alice",
        role_id="role",
        status="active",
        password_hash="hash" if password else None,
        totp_secret_encrypted=b"secret" if totp else None,
        session_generation=0,
    )


@pytest.mark.parametrize(
    ("password", "totp", "expected"),
    [
        (True, False, ["password"]),
        (True, True, ["password", "totp"]),
        (False, True, ["totp"]),
        (False, False, []),
    ],
)
def test_step_up_methods_follow_what_the_account_holds(password: bool, totp: bool, expected: list[str]) -> None:
    assert step_up_methods(_user(password=password, totp=totp)) == expected


def test_freshness_window_is_five_minutes_inclusive() -> None:
    assert STEP_UP_MAX_AGE_SECONDS == 300
    assert is_step_up_fresh(None, now=_NOW) is False
    assert is_step_up_fresh(_NOW, now=_NOW) is True
    assert is_step_up_fresh(_NOW - 300, now=_NOW) is True
    assert is_step_up_fresh(_NOW - 301, now=_NOW) is False
    assert step_up_expires_at(_NOW) == _NOW + 300


def test_step_up_permissions_are_the_write_side_of_the_privileged_set() -> None:
    assert STEP_UP_PERMISSIONS == {
        Permission.SECURITY_WRITE,
        Permission.USERS_MANAGE,
        Permission.ROLES_MANAGE,
        Permission.ACCOUNTS_EXPORT,
    }
    assert STEP_UP_PERMISSIONS <= PRIVILEGED_PERMISSIONS


def test_session_cookie_carries_su_only_when_minted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "time", lambda: _NOW)
    store = DashboardSessionStore()

    without = store.get(
        store.create_user_session("user-1", 1, password_verified=True, totp_verified=False, ttl_seconds=60)
    )
    with_su = store.get(
        store.create_user_session(
            "user-1", 1, password_verified=True, totp_verified=True, ttl_seconds=60, step_up_verified_at=_NOW - 5
        )
    )

    assert without is not None and without.step_up_verified_at is None
    assert with_su is not None and with_su.step_up_verified_at == _NOW - 5


def _sealed(payload: dict[str, Any]) -> str:
    return TokenEncryptor().encrypt(json.dumps(payload)).decode("ascii")


def test_step_up_cookie_round_trip_and_rejections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "time", lambda: _NOW)
    store = StepUpCookieStore()
    token = store.create("user-1", session_generation=3, verified_at=_NOW)

    assert store.get(token, user_id="user-1", session_generation=3) == _NOW
    # Another account's cookie is worthless for this one.
    assert store.get(token, user_id="user-2", session_generation=3) is None
    # Revoking the account's sessions (or resetting its TOTP) voids the proof.
    assert store.get(token, user_id="user-1", session_generation=4) is None
    assert store.get(None, user_id="user-1", session_generation=3) is None
    assert store.get("not-a-token", user_id="user-1", session_generation=3) is None
    # Expired: the cookie's own exp has passed.
    monkeypatch.setattr(service_module, "time", lambda: _NOW + 301)
    assert store.get(token, user_id="user-1", session_generation=3) is None
    # Wrong version or missing fields.
    monkeypatch.setattr(service_module, "time", lambda: _NOW)
    assert (
        store.get(
            _sealed({"v": 2, "uid": "user-1", "sg": 3, "su": _NOW, "exp": _NOW + 300}),
            user_id="user-1",
            session_generation=3,
        )
        is None
    )
    assert (
        store.get(
            _sealed({"v": 1, "uid": "user-1", "sg": 3, "exp": _NOW + 300}), user_id="user-1", session_generation=3
        )
        is None
    )
