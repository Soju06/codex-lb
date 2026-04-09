from __future__ import annotations

from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

import app.core.bootstrap as bootstrap_module
from app.core.crypto import TokenEncryptor

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_bootstrap_state() -> Iterator[None]:
    bootstrap_module._encryptor = None
    yield
    bootstrap_module._encryptor = None


def _patch_settings(monkeypatch: pytest.MonkeyPatch, *, token: str | None) -> None:
    monkeypatch.setattr(
        "app.core.bootstrap.get_settings",
        lambda: SimpleNamespace(dashboard_bootstrap_token=token),
    )


def _patch_settings_cache(
    monkeypatch: pytest.MonkeyPatch,
    *,
    password_hash: str | None,
    bootstrap_token_encrypted: bytes | None,
) -> None:
    cache = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                password_hash=password_hash,
                bootstrap_token_encrypted=bootstrap_token_encrypted,
            )
        )
    )
    monkeypatch.setattr("app.core.bootstrap.get_settings_cache", lambda: cache)


@pytest.mark.asyncio
async def test_get_active_bootstrap_token_returns_env_var_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token="manual-token")
    _patch_settings_cache(monkeypatch, password_hash=None, bootstrap_token_encrypted=b"ignored")

    assert await bootstrap_module.get_active_bootstrap_token() == "manual-token"


@pytest.mark.asyncio
async def test_get_active_bootstrap_token_returns_db_token_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    encryptor = TokenEncryptor(key=Fernet.generate_key())
    bootstrap_module._encryptor = encryptor
    encrypted = encryptor.encrypt("shared-auto-token")
    _patch_settings_cache(monkeypatch, password_hash=None, bootstrap_token_encrypted=encrypted)

    assert await bootstrap_module.get_active_bootstrap_token() == "shared-auto-token"


@pytest.mark.asyncio
async def test_get_active_bootstrap_token_returns_none_when_password_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    encryptor = TokenEncryptor(key=Fernet.generate_key())
    bootstrap_module._encryptor = encryptor
    encrypted = encryptor.encrypt("shared-auto-token")
    _patch_settings_cache(monkeypatch, password_hash="configured", bootstrap_token_encrypted=encrypted)

    assert await bootstrap_module.get_active_bootstrap_token() is None


@pytest.mark.asyncio
async def test_get_active_bootstrap_token_returns_none_when_nothing_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_settings_cache(monkeypatch, password_hash=None, bootstrap_token_encrypted=None)

    assert await bootstrap_module.get_active_bootstrap_token() is None
