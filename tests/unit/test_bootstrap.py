from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

import app.core.bootstrap as bootstrap_module
from app.core.crypto import TokenEncryptor

pytestmark = pytest.mark.unit


def _patch_settings(monkeypatch: pytest.MonkeyPatch, *, token: str | None) -> None:
    monkeypatch.setattr(
        "app.core.bootstrap.get_settings",
        lambda: SimpleNamespace(dashboard_bootstrap_token=token),
    )


def _patch_shared_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    password_hash: str | None,
    bootstrap_token_encrypted: bytes | None,
    bootstrap_token_hash: bytes | None,
) -> None:
    monkeypatch.setattr(
        bootstrap_module,
        "_get_shared_bootstrap_state",
        AsyncMock(return_value=(password_hash, bootstrap_token_encrypted, bootstrap_token_hash)),
    )


def _patch_encryptor(monkeypatch: pytest.MonkeyPatch) -> TokenEncryptor:
    encryptor = TokenEncryptor(key=Fernet.generate_key())
    monkeypatch.setattr(bootstrap_module, "_get_encryptor", lambda: encryptor)
    return encryptor


@pytest.mark.asyncio
async def test_has_active_bootstrap_token_returns_true_when_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token="manual-token")
    _patch_shared_state(
        monkeypatch, password_hash=None, bootstrap_token_encrypted=b"ignored", bootstrap_token_hash=b"ignored"
    )

    assert await bootstrap_module.has_active_bootstrap_token() is True


@pytest.mark.asyncio
async def test_has_active_bootstrap_token_returns_true_when_hash_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_shared_state(
        monkeypatch, password_hash=None, bootstrap_token_encrypted=b"encrypted", bootstrap_token_hash=b"hash"
    )

    assert await bootstrap_module.has_active_bootstrap_token() is True


@pytest.mark.asyncio
async def test_has_active_bootstrap_token_returns_false_when_password_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_shared_state(
        monkeypatch, password_hash="configured", bootstrap_token_encrypted=b"encrypted", bootstrap_token_hash=b"hash"
    )

    assert await bootstrap_module.has_active_bootstrap_token() is False


@pytest.mark.asyncio
async def test_has_active_bootstrap_token_returns_false_when_nothing_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_shared_state(monkeypatch, password_hash=None, bootstrap_token_encrypted=None, bootstrap_token_hash=None)

    assert await bootstrap_module.has_active_bootstrap_token() is False


@pytest.mark.asyncio
async def test_validate_bootstrap_token_accepts_non_ascii_manual_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token="부트스트랩-토큰")
    _patch_shared_state(monkeypatch, password_hash=None, bootstrap_token_encrypted=None, bootstrap_token_hash=None)

    assert await bootstrap_module.validate_bootstrap_token("부트스트랩-토큰") is True


@pytest.mark.asyncio
async def test_validate_bootstrap_token_checks_stored_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_shared_state(
        monkeypatch,
        password_hash=None,
        bootstrap_token_encrypted=b"encrypted",
        bootstrap_token_hash=hashlib.sha256("shared-auto-token".encode("utf-8")).digest(),
    )

    assert await bootstrap_module.validate_bootstrap_token("shared-auto-token") is True
    assert await bootstrap_module.validate_bootstrap_token("wrong-token") is False


@pytest.mark.asyncio
async def test_has_active_bootstrap_token_reads_uncached_shared_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_shared_state(
        monkeypatch, password_hash=None, bootstrap_token_encrypted=b"encrypted", bootstrap_token_hash=b"hash"
    )

    assert await bootstrap_module.has_active_bootstrap_token() is True


@pytest.mark.asyncio
async def test_validate_bootstrap_token_reads_uncached_shared_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_shared_state(
        monkeypatch,
        password_hash=None,
        bootstrap_token_encrypted=b"encrypted",
        bootstrap_token_hash=hashlib.sha256("shared-auto-token".encode("utf-8")).digest(),
    )

    assert await bootstrap_module.validate_bootstrap_token("shared-auto-token") is True


@pytest.mark.asyncio
async def test_get_bootstrap_validation_status_reports_password_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    _patch_shared_state(
        monkeypatch, password_hash="configured", bootstrap_token_encrypted=None, bootstrap_token_hash=None
    )

    assert await bootstrap_module.get_bootstrap_validation_status("shared-auto-token") == "password_already_configured"


@pytest.mark.asyncio
async def test_ensure_auto_bootstrap_token_reuses_existing_encrypted_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    encryptor = _patch_encryptor(monkeypatch)
    encrypted = encryptor.encrypt("shared-auto-token")

    async def _get_settings() -> SimpleNamespace:
        return SimpleNamespace(
            password_hash=None,
            bootstrap_token_encrypted=encrypted,
            bootstrap_token_hash=hashlib.sha256("shared-auto-token".encode("utf-8")).digest(),
        )

    repository = SimpleNamespace(
        get_settings=AsyncMock(side_effect=_get_settings),
        clear_bootstrap_token=AsyncMock(),
        store_bootstrap_token_if_absent=AsyncMock(),
    )

    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(bootstrap_module, "SessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(bootstrap_module, "DashboardAuthRepository", lambda _session: repository)

    assert await bootstrap_module.ensure_auto_bootstrap_token() == "shared-auto-token"
    repository.store_bootstrap_token_if_absent.assert_not_called()
