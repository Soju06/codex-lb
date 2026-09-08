from __future__ import annotations

import json

import pytest

import app.modules.dashboard_auth.service as dashboard_auth_service_module
from app.core.auth.dashboard_access import DashboardRole
from app.core.crypto import TokenEncryptor
from app.modules.dashboard_auth.service import DashboardSessionStore

pytestmark = pytest.mark.unit


def test_session_store_round_trip_with_password_and_totp(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: 1_700_000_000)
    store = DashboardSessionStore()

    session_id = store.create(password_verified=True, totp_verified=False, ttl_seconds=12 * 60 * 60)
    state = store.get(session_id)

    assert state is not None
    assert state.password_verified is True
    assert state.totp_verified is False
    assert state.role == DashboardRole.ADMIN
    assert state.expires_at == 1_700_000_000 + 12 * 60 * 60


def test_session_store_round_trip_with_guest_role(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: 1_700_000_000)
    store = DashboardSessionStore()

    session_id = store.create(
        password_verified=False,
        totp_verified=False,
        ttl_seconds=12 * 60 * 60,
        role=DashboardRole.GUEST,
        guest_session_generation=3,
    )
    state = store.get(session_id)

    assert state is not None
    assert state.password_verified is False
    assert state.totp_verified is False
    assert state.role == DashboardRole.GUEST
    assert state.guest_session_generation == 3


def test_session_store_requires_generation_for_guest_sessions() -> None:
    store = DashboardSessionStore()
    with pytest.raises(ValueError, match="guest session generation"):
        store.create(password_verified=False, totp_verified=False, ttl_seconds=60, role=DashboardRole.GUEST)


def test_session_store_admin_sessions_carry_no_generation(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: 1_700_000_000)
    store = DashboardSessionStore()
    session_id = store.create(password_verified=True, totp_verified=True, ttl_seconds=60)
    state = store.get(session_id)
    assert state is not None
    assert state.guest_session_generation is None


def test_session_store_legacy_guest_cookie_without_generation_never_matches(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: 1_700_000_000)
    store = DashboardSessionStore()
    encryptor = TokenEncryptor()
    legacy_payload = json.dumps(
        {"exp": 1_700_000_100, "pw": False, "tv": False, "role": "guest", "gv": True},
        separators=(",", ":"),
    )
    state = store.get(encryptor.encrypt(legacy_payload).decode("ascii"))
    assert state is not None
    assert state.role == DashboardRole.GUEST
    assert state.guest_session_generation is None


def test_session_store_rejects_non_integer_generation(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: 1_700_000_000)
    store = DashboardSessionStore()
    encryptor = TokenEncryptor()
    for bad in ("1", True, 1.5):
        payload = json.dumps(
            {"exp": 1_700_000_100, "pw": False, "tv": False, "role": "guest", "gv": True, "gg": bad},
            separators=(",", ":"),
        )
        assert store.get(encryptor.encrypt(payload).decode("ascii")) is None


def test_session_store_rejects_legacy_cookie_without_password_flag(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: 1_700_000_000)
    store = DashboardSessionStore()
    encryptor = TokenEncryptor()
    legacy_payload = json.dumps({"exp": 1_700_000_100, "tv": True}, separators=(",", ":"))
    legacy_session_id = encryptor.encrypt(legacy_payload).decode("ascii")

    assert store.get(legacy_session_id) is None


def test_session_store_rejects_expired_session(monkeypatch) -> None:
    current = {"value": 1_700_000_000}
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: current["value"])
    store = DashboardSessionStore()

    session_id = store.create(password_verified=True, totp_verified=True, ttl_seconds=12 * 60 * 60)
    current["value"] += 12 * 60 * 60 + 1

    assert store.get(session_id) is None
