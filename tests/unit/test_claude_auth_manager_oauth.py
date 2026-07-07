"""Tests for ``ClaudeAuthManager.add_claude_account_from_oauth``.

Only the OAuth wrapper is exercised here; the underlying
``add_claude_account`` behavior is exhaustively covered in
``test_claude_account_service.py``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.modules.claude.oauth.tokens import ClaudeOauthClaims

from app.modules.claude.auth_manager import (
    ClaudeAccountAlreadyExists,
    ClaudeAuthManager,
)

pytestmark = pytest.mark.unit


class _CapturingRepo:
    """In-memory repo stand-in that records the inserted row."""

    def __init__(self) -> None:
        self.inserted: dict[str, Any] | None = None

    async def exists_by_claude_uuid(self, claude_uuid: str) -> bool:
        return False

    async def insert(self, row: dict[str, Any]) -> Any:
        self.inserted = row
        return type("Row", (), {"id": row["id"]})()


class _ExistingRepo:
    """Repo stand-in that reports the UUID as already-taken."""

    async def exists_by_claude_uuid(self, claude_uuid: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_add_claude_account_from_oauth_delegates_with_claim_derived_fields() -> None:
    repo = _CapturingRepo()
    mgr = ClaudeAuthManager(repo=repo)  # type: ignore[arg-type]
    claims = ClaudeOauthClaims(
        claude_account_uuid="acct-uuid",
        user_email="u@example.test",
        user_organization_uuid="org-uuid",
        scopes=["user:profile", "user:inference"],
    )

    new_id = await mgr.add_claude_account_from_oauth(
        access_token="AT",
        refresh_token="RT",
        expires_in=3600,
        id_token_claims=claims,
    )

    assert new_id == "claude-acct-uuid"
    assert repo.inserted is not None

    row = repo.inserted
    assert row["claude_account_uuid"] == "acct-uuid"
    # Column names match the existing ``add_claude_account`` row keys
    assert row["claude_user_email"] == "u@example.test"
    assert row["claude_user_organization_uuid"] == "org-uuid"
    # scopes stored as JSON-encoded string
    assert json.loads(row["claude_scopes"]) == ["user:profile", "user:inference"]


@pytest.mark.asyncio
async def test_add_claude_account_from_oauth_propagates_duplicate() -> None:
    mgr = ClaudeAuthManager(repo=_ExistingRepo())  # type: ignore[arg-type]
    claims = ClaudeOauthClaims(
        claude_account_uuid="duplicate",
        user_email=None,
        user_organization_uuid=None,
        scopes=None,
    )

    with pytest.raises(ClaudeAccountAlreadyExists):
        await mgr.add_claude_account_from_oauth(
            access_token="AT",
            refresh_token="RT",
            expires_in=3600,
            id_token_claims=claims,
        )


@pytest.mark.asyncio
async def test_tokens_are_encrypted_in_storage() -> None:
    repo = _CapturingRepo()
    mgr = ClaudeAuthManager(repo=repo)  # type: ignore[arg-type]
    claims = ClaudeOauthClaims(claude_account_uuid="acct-uuid")

    await mgr.add_claude_account_from_oauth(
        access_token="PLAINTEXT_AT",
        refresh_token="PLAINTEXT_RT",
        expires_in=3600,
        id_token_claims=claims,
    )

    # Encrypted tokens are stored as bytes (Fernet envelope) — must not
    # contain plaintext.
    assert b"PLAINTEXT_AT" not in repo.inserted["claude_access_token_encrypted"]
    assert b"PLAINTEXT_RT" not in repo.inserted["claude_refresh_token_encrypted"]
