from __future__ import annotations

import json
from typing import Any

import pytest

import app.modules.dashboard_auth.service as dashboard_auth_service_module
from app.core.auth.dashboard_access import DashboardRole
from app.core.crypto import TokenEncryptor
from app.modules.dashboard_auth.service import DashboardSessionStore

pytestmark = pytest.mark.unit

_NOW = 1_700_000_000


def _sealed(payload: dict[str, Any]) -> str:
    return TokenEncryptor().encrypt(json.dumps(payload, separators=(",", ":"))).decode("ascii")


def _v2_user_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "v": 2,
        "exp": _NOW + 100,
        "iat": _NOW,
        "uid": "user-1",
        "sg": 3,
        "pv": True,
        "tp": False,
        "am": "password",
    }
    payload.update(overrides)
    return payload


def test_user_session_round_trip(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: _NOW)
    store = DashboardSessionStore()

    session_id = store.create_user_session(
        "user-1", 7, password_verified=True, totp_verified=False, ttl_seconds=12 * 60 * 60
    )
    state = store.get(session_id)

    assert state is not None
    assert state.kind == "user"
    assert state.user_id == "user-1"
    assert state.session_generation == 7
    assert state.password_verified is True
    assert state.totp_verified is False
    assert state.auth_method == "password"
    assert state.issued_at == _NOW
    assert state.expires_at == _NOW + 12 * 60 * 60
    assert state.role == DashboardRole.ADMIN
    assert state.guest_session_generation is None


def test_user_session_payload_is_version_2_with_renamed_keys(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: _NOW)
    store = DashboardSessionStore()
    session_id = store.create_user_session("user-1", 0, password_verified=True, totp_verified=True, ttl_seconds=60)

    payload = json.loads(TokenEncryptor().decrypt(session_id.encode("ascii")))

    assert payload == {
        "v": 2,
        "exp": _NOW + 60,
        "iat": _NOW,
        "uid": "user-1",
        "sg": 0,
        "pv": True,
        "tp": True,
        "am": "password",
    }
    # The v1 keys are gone so a previous-release replica fails closed on this cookie.
    assert not {"pw", "tv", "role", "gv"} & payload.keys()


def test_guest_session_round_trip(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: _NOW)
    store = DashboardSessionStore()

    session_id = store.create_guest_session(ttl_seconds=12 * 60 * 60, guest_session_generation=3)
    state = store.get(session_id)

    assert state is not None
    assert state.kind == "guest"
    assert state.role == DashboardRole.GUEST
    assert state.guest_session_generation == 3
    assert state.user_id is None
    assert state.password_verified is False
    payload = json.loads(TokenEncryptor().decrypt(session_id.encode("ascii")))
    assert payload == {"v": 2, "exp": _NOW + 12 * 60 * 60, "iat": _NOW, "guest": True, "gg": 3}


def test_v1_cookies_are_rejected(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: _NOW)
    store = DashboardSessionStore()
    v1_admin = {"exp": _NOW + 100, "pw": True, "tv": True, "role": "admin", "gv": False}
    v1_guest = {"exp": _NOW + 100, "pw": False, "tv": False, "role": "guest", "gv": True, "gg": 1}
    v1_minimal = {"exp": _NOW + 100, "pw": True, "tv": False}

    for payload in (v1_admin, v1_guest, v1_minimal):
        assert store.get(_sealed(payload)) is None


def test_v2_payload_with_v1_keys_mixed_in_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: _NOW)
    store = DashboardSessionStore()
    # A version tag alone is not enough: the user fields must be present and typed.
    assert store.get(_sealed({"v": 2, "exp": _NOW + 100, "iat": _NOW, "pw": True, "tv": True})) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"uid": ""},
        {"uid": 12},
        {"sg": "3"},
        {"sg": True},
        {"pv": "yes"},
        {"tp": 1},
        {"am": None},
        {"iat": None},
        {"exp": "soon"},
    ],
)
def test_malformed_user_payloads_are_rejected(monkeypatch, overrides: dict[str, object]) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: _NOW)
    store = DashboardSessionStore()
    payload = _v2_user_payload(**overrides)
    for key, value in overrides.items():
        if value is None:
            payload.pop(key)
    assert store.get(_sealed(payload)) is None


def test_guest_payload_requires_integer_generation(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: _NOW)
    store = DashboardSessionStore()
    for bad in ("1", True, 1.5, None):
        payload: dict[str, Any] = {"v": 2, "exp": _NOW + 100, "iat": _NOW, "guest": True, "gg": bad}
        if bad is None:
            payload.pop("gg")
        assert store.get(_sealed(payload)) is None


def test_expired_session_is_rejected(monkeypatch) -> None:
    current = {"value": _NOW}
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: current["value"])
    store = DashboardSessionStore()

    session_id = store.create_user_session("user-1", 0, password_verified=True, totp_verified=True, ttl_seconds=3600)
    current["value"] += 3601

    assert store.get(session_id) is None


def test_garbage_and_empty_cookies_are_rejected() -> None:
    store = DashboardSessionStore()
    assert store.get(None) is None
    assert store.get("") is None
    assert store.get("   ") is None
    assert store.get("not-a-fernet-token") is None
