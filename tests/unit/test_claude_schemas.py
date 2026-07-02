"""Tests for the Claude admin-account Pydantic schemas.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md``
— *Manual Claude account add* (UUID, accessToken, refreshToken, expiresInSeconds,
scopes, userEmail, userOrganizationUuid), *Disable and re-enable Claude
accounts* (reason), and *List Claude accounts* (no plaintext tokens in
response).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.claude.schemas import (
    AddClaudeAccountRequest,
    ClaudeAccountResponse,
    DisableClaudeAccountRequest,
    ListClaudeAccountsResponse,
)

pytestmark = pytest.mark.unit


def _minimal_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "claudeAccountUuid": "abc-123",
        "accessToken": "AT",
        "refreshToken": "RT",
        "expiresInSeconds": 3600,
    }
    payload.update(overrides)
    return payload


def test_add_request_accepts_happy_path() -> None:
    req = AddClaudeAccountRequest(**_minimal_payload())  # type: ignore[arg-type]

    assert req.claude_account_uuid == "abc-123"
    assert req.access_token == "AT"
    assert req.refresh_token == "RT"
    assert req.expires_in_seconds == 3600
    assert req.scopes is None
    assert req.user_email is None
    assert req.user_organization_uuid is None


def test_add_request_accepts_all_optional_fields() -> None:
    req = AddClaudeAccountRequest(
        **_minimal_payload(
            scopes=["user:profile", "user:inference"],
            userEmail="user@example.com",
            userOrganizationUuid="org-uuid-1",
        )  # type: ignore[arg-type]
    )

    assert req.scopes == ["user:inference", "user:profile"]  # sorted, deduplicated
    assert req.user_email == "user@example.com"
    assert req.user_organization_uuid == "org-uuid-1"


def test_add_request_rejects_missing_refresh_token() -> None:
    payload = _minimal_payload()
    del payload["refreshToken"]

    with pytest.raises(ValidationError) as exc_info:
        AddClaudeAccountRequest(**payload)  # type: ignore[arg-type]

    errors = exc_info.value.errors()
    assert any(err["loc"] == ("refreshToken",) for err in errors), errors


def test_add_request_rejects_blank_uuid() -> None:
    with pytest.raises(ValidationError) as exc_info:
        AddClaudeAccountRequest(**_minimal_payload(claudeAccountUuid="   "))  # type: ignore[arg-type]

    errors = exc_info.value.errors()
    assert any("claudeAccountUuid" in err["loc"] for err in errors), errors


def test_add_request_rejects_zero_or_negative_expires() -> None:
    with pytest.raises(ValidationError):
        AddClaudeAccountRequest(**_minimal_payload(expiresInSeconds=0))  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        AddClaudeAccountRequest(**_minimal_payload(expiresInSeconds=-3600))  # type: ignore[arg-type]


def test_add_request_rejects_absurd_expires() -> None:
    # 31 days in seconds — guard against absurd values per the task brief.
    thirty_one_days = 86400 * 31

    with pytest.raises(ValidationError):
        AddClaudeAccountRequest(**_minimal_payload(expiresInSeconds=thirty_one_days))  # type: ignore[arg-type]


def test_add_request_deduplicates_and_sorts_scopes() -> None:
    req = AddClaudeAccountRequest(
        **_minimal_payload(
            scopes=["user:inference", "user:profile", "user:inference", "  user:profile  "],
        )  # type: ignore[arg-type]
    )

    assert req.scopes == ["user:inference", "user:profile"]


def test_response_does_not_leak_plaintext_tokens() -> None:
    """Plaintext ``accessToken`` / ``refreshToken`` MUST NOT appear in the
    serialized response — verified by both name field inspection and the
    absence of token literals in the JSON payload.
    """
    payload = {
        "id": "claude-1",
        "claudeAccountUuid": "abc-123",
        "userEmail": "user@example.com",
        "userOrganizationUuid": "org-uuid-1",
        "isActive": True,
        "claudeAccessTokenExpiresAt": None,
        "lastUsedAt": None,
        "rateLimitRequestsRemaining": 42,
        "rateLimitInputTokensRemaining": 100000,
        "rateLimitOutputTokensRemaining": 50000,
        "rateLimitStatus": "allowed",
        "createdAt": "2026-07-01T12:00:00Z",
    }

    response = ClaudeAccountResponse(**payload)

    # Field-level: the schema MUST NOT advertise token fields at all.
    fields = set(ClaudeAccountResponse.model_fields.keys())
    assert "accessToken" not in fields
    assert "refreshToken" not in fields
    assert "accessTokenEncrypted" not in fields
    assert "refreshTokenEncrypted" not in fields
    assert "claudeAccessTokenEncrypted" not in fields
    assert "claudeRefreshTokenEncrypted" not in fields

    # Serialized: no plaintext token literal leaks through.
    serialized = response.model_dump_json()
    assert "AT" not in serialized
    assert "RT" not in serialized
    assert "accessToken" not in serialized
    assert "refreshToken" not in serialized


def test_list_response_contains_only_sanitized_accounts() -> None:
    payload = {
        "accounts": [
            {
                "id": "claude-1",
                "claudeAccountUuid": "abc-123",
                "userEmail": None,
                "userOrganizationUuid": None,
                "isActive": True,
                "claudeAccessTokenExpiresAt": None,
                "lastUsedAt": None,
                "rateLimitRequestsRemaining": None,
                "rateLimitInputTokensRemaining": None,
                "rateLimitOutputTokensRemaining": None,
                "rateLimitStatus": None,
                "createdAt": "2026-07-01T12:00:00Z",
            }
        ]
    }

    response = ListClaudeAccountsResponse(**payload)
    serialized = response.model_dump_json()

    assert "AT" not in serialized
    assert "RT" not in serialized
    assert "abc-123" in serialized


def test_disable_reason_is_trimmed_and_none_for_empty_string() -> None:
    req_with_reason = DisableClaudeAccountRequest(reason="  manual-disable  ")
    assert req_with_reason.reason == "manual-disable"

    req_blank = DisableClaudeAccountRequest(reason="   ")
    assert req_blank.reason is None

    req_none = DisableClaudeAccountRequest()
    assert req_none.reason is None
