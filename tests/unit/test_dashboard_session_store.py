from __future__ import annotations

import json

import pytest

import app.modules.dashboard_auth.service as dashboard_auth_service_module
from app.core.crypto import TokenEncryptor
from app.modules.dashboard_auth.service import DashboardSessionStore

pytestmark = pytest.mark.unit


def test_session_store_round_trip_with_password_and_totp(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_auth_service_module, "time", lambda: 1_700_000_000)
    store = DashboardSessionStore()

    session_id = store.create(password_verified=True, totp_verified=False)
    state = store.get(session_id)

    assert state is not None
    assert state.password_verified is True
    assert state.totp_verified is False
    assert state.expires_at == 1_700_000_000 + 12 * 60 * 60


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

    session_id = store.create(password_verified=True, totp_verified=True)
    current["value"] += 12 * 60 * 60 + 1

    assert store.get(session_id) is None
