"""Tests for ``app.modules.claude.auth_manager.ClaudeAuthManager.add_claude_account``.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md``
— requirement *Manual Claude account add*.

These tests cover the add-claude-account happy path and the duplicate-UUID
conflict branch. Refresh / lifecycle tests arrive in Tasks 6.3 + 6.4.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import TokenEncryptor
from app.db.models import AccountStatus
from app.modules.claude import auth_manager as auth_manager_module
from app.modules.claude.auth_manager import (
    ClaudeAccountAlreadyExists,
    ClaudeAuthManager,
)

pytestmark = pytest.mark.unit


class _FakeEncryptor:
    """Deterministic stand-in for ``TokenEncryptor`` so tests can verify the
    storage envelope is bytes-typed (NOT plaintext-as-string)."""

    def encrypt(self, plaintext: str) -> bytes:
        return f"enc::{plaintext}".encode("utf-8")

    def decrypt(self, ciphertext: bytes) -> str:
        return ciphertext.decode("utf-8").removeprefix("enc::")


def _serialize_for_storage(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class _FakeRepo:
    """In-memory implementation of the ``ClaudeAccountRepository`` protocol
    narrowed to the surface Task 6.2 uses (``exists_by_claude_uuid``,
    ``insert``)."""

    def __init__(self) -> None:
        self.exists_uuid = False
        self.persisted: dict[str, dict[str, Any]] = {}
        self.insert_calls: list[dict[str, Any]] = []

    async def exists_by_claude_uuid(self, claude_uuid: str) -> bool:
        return self.exists_uuid or any(
            row.get("claude_account_uuid") == claude_uuid
            and row.get("provider") == "claude"
            for row in self.persisted.values()
        )

    async def insert(self, row: dict[str, Any]):
        account_id = row["id"]
        self.persisted[account_id] = {
            k: _serialize_for_storage(v) for k, v in row.items()
        }
        self.insert_calls.append(self.persisted[account_id])
        return type("Inserted", (), {"id": account_id, "claude_account_uuid": row["claude_account_uuid"]})()


@pytest.fixture()
def fake_repo() -> _FakeRepo:
    return _FakeRepo()


@pytest.fixture()
def fake_encryptor() -> _FakeEncryptor:
    return _FakeEncryptor()


@pytest.mark.asyncio
async def test_add_claude_account_persists_encrypted_tokens(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor
) -> None:
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor)

    account_id = await manager.add_claude_account(
        claude_account_uuid="abc-123",
        access_token="AT",
        refresh_token="RT",
        expires_in_seconds=3600,
        scopes=["user:inference"],
        user_email="user@example.com",
        user_organization_uuid="org-1",
    )

    row = fake_repo.persisted[account_id]
    assert row["provider"] == "claude"
    assert row["claude_account_uuid"] == "abc-123"
    assert row["status"] == AccountStatus.ACTIVE.value
    assert row["claude_user_email"] == "user@example.com"
    assert row["claude_user_organization_uuid"] == "org-1"

    # Tokens are stored as bytes (not plaintext strings); the encrypted
    # envelope encodes the plaintext (round-trip works).
    at_blob = row["claude_access_token_encrypted"]
    rt_blob = row["claude_refresh_token_encrypted"]
    assert isinstance(at_blob, bytes)
    assert isinstance(rt_blob, bytes)
    assert at_blob.startswith(b"enc::")
    assert rt_blob.startswith(b"enc::")
    # Round-trip decryption recovers the original tokens.
    assert fake_encryptor.decrypt(at_blob) == "AT"
    assert fake_encryptor.decrypt(rt_blob) == "RT"
    # Scopes JSON-serialized.
    assert json.loads(row["claude_scopes"]) == ["user:inference"]


@pytest.mark.asyncio
async def test_add_claude_account_sets_expiry_with_skew(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor
) -> None:
    """Expiry equals ``now + expires_in - skew`` (default 600s)."""
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor, skew_seconds=600)

    before = datetime.now(timezone.utc)
    account_id = await manager.add_claude_account(
        claude_account_uuid="abc-123",
        access_token="AT",
        refresh_token="RT",
        expires_in_seconds=3600,
        scopes=None,
        user_email=None,
        user_organization_uuid=None,
    )
    after = datetime.now(timezone.utc)

    row = fake_repo.persisted[account_id]
    expires_at = datetime.fromisoformat(row["claude_access_token_expires_at"])
    expected_low = before + timedelta(seconds=3600 - 600) - timedelta(seconds=1)
    expected_high = after + timedelta(seconds=3600 - 600) + timedelta(seconds=1)
    assert expected_low <= expires_at <= expected_high


@pytest.mark.asyncio
async def test_add_claude_account_rejects_duplicate_uuid(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor
) -> None:
    fake_repo.exists_uuid = True
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor)

    with pytest.raises(ClaudeAccountAlreadyExists) as exc_info:
        await manager.add_claude_account(
            claude_account_uuid="abc-123",
            access_token="AT",
            refresh_token="RT",
            expires_in_seconds=3600,
            scopes=None,
            user_email=None,
            user_organization_uuid=None,
        )

    assert exc_info.value.claude_uuid == "abc-123"


@pytest.mark.asyncio
async def test_add_claude_account_uses_settings_skew_when_default(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``skew_seconds`` is omitted the manager reads
    ``settings.claude_oauth_refresh_skew_seconds`` (Phase 2 default: 600)."""

    class _Settings:
        claude_oauth_refresh_skew_seconds = 120

    monkeypatch.setattr(auth_manager_module, "get_settings", lambda: _Settings())

    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor)

    before = datetime.now(timezone.utc)
    account_id = await manager.add_claude_account(
        claude_account_uuid="abc-123",
        access_token="AT",
        refresh_token="RT",
        expires_in_seconds=300,
        scopes=None,
        user_email=None,
        user_organization_uuid=None,
    )
    after = datetime.now(timezone.utc)

    row = fake_repo.persisted[account_id]
    expires_at = datetime.fromisoformat(row["claude_access_token_expires_at"])
    expected_low = before + timedelta(seconds=300 - 120) - timedelta(seconds=1)
    expected_high = after + timedelta(seconds=300 - 120) + timedelta(seconds=1)
    assert expected_low <= expires_at <= expected_high


@pytest.mark.asyncio
async def test_add_with_real_token_encryptor_never_persists_plaintext() -> None:
    """End-to-end smoke: real ``TokenEncryptor`` produces Fernet ciphertext;
    ciphertext round-trips back to the plaintext tokens."""
    key = Fernet.generate_key()
    repo = _FakeRepo()
    manager = ClaudeAuthManager(repo=repo, encryptor=TokenEncryptor(key=key))

    account_id = await manager.add_claude_account(
        claude_account_uuid="real-enc-1",
        access_token="plaintext-access",
        refresh_token="plaintext-refresh",
        expires_in_seconds=3600,
        scopes=None,
        user_email=None,
        user_organization_uuid=None,
    )

    row = repo.persisted[account_id]
    # Ciphertext bytes are not equal to the plaintext strings.
    assert row["claude_access_token_encrypted"] != b"plaintext-access"
    assert row["claude_refresh_token_encrypted"] != b"plaintext-refresh"
    # And the encrypted blobs really do decrypt back to the original tokens.
    assert TokenEncryptor(key=key).decrypt(row["claude_access_token_encrypted"]) == "plaintext-access"
    assert TokenEncryptor(key=key).decrypt(row["claude_refresh_token_encrypted"]) == "plaintext-refresh"
