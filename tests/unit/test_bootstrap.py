from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.core.bootstrap as bootstrap_module

pytestmark = pytest.mark.unit


def _patch_settings(monkeypatch: pytest.MonkeyPatch, *, token: str | None) -> None:
    monkeypatch.setattr(
        "app.core.bootstrap.get_settings",
        lambda: SimpleNamespace(dashboard_bootstrap_token=token),
    )


def _patch_settings_cache(
    monkeypatch: pytest.MonkeyPatch,
    *,
    password_hash: str | None,
    bootstrap_token_hash: bytes | None,
) -> None:
    cache = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                password_hash=password_hash,
                bootstrap_token_hash=bootstrap_token_hash,
            )
        )
    )
    monkeypatch.setattr("app.core.bootstrap.get_settings_cache", lambda: cache)


@pytest.mark.asyncio
async def test_has_active_bootstrap_token_returns_true_when_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token="manual-token")
    _patch_settings_cache(monkeypatch, password_hash=None, bootstrap_token_hash=b"ignored")

    assert await bootstrap_module.has_active_bootstrap_token() is True


@pytest.mark.asyncio
async def test_has_active_bootstrap_token_returns_true_when_hash_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_settings_cache(monkeypatch, password_hash=None, bootstrap_token_hash=b"hash")

    assert await bootstrap_module.has_active_bootstrap_token() is True


@pytest.mark.asyncio
async def test_has_active_bootstrap_token_returns_false_when_password_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_settings_cache(monkeypatch, password_hash="configured", bootstrap_token_hash=b"hash")

    assert await bootstrap_module.has_active_bootstrap_token() is False


@pytest.mark.asyncio
async def test_has_active_bootstrap_token_returns_false_when_nothing_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_settings_cache(monkeypatch, password_hash=None, bootstrap_token_hash=None)

    assert await bootstrap_module.has_active_bootstrap_token() is False


@pytest.mark.asyncio
async def test_validate_bootstrap_token_accepts_non_ascii_manual_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token="부트스트랩-토큰")
    _patch_settings_cache(monkeypatch, password_hash=None, bootstrap_token_hash=None)

    assert await bootstrap_module.validate_bootstrap_token("부트스트랩-토큰") is True


@pytest.mark.asyncio
async def test_validate_bootstrap_token_checks_stored_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_settings_cache(
        monkeypatch,
        password_hash=None,
        bootstrap_token_hash=hashlib.sha256("shared-auto-token".encode("utf-8")).digest(),
    )

    assert await bootstrap_module.validate_bootstrap_token("shared-auto-token") is True
    assert await bootstrap_module.validate_bootstrap_token("wrong-token") is False
