"""Pydantic schemas for the Claude OAuth pool admin endpoints.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md``
— specifically the *Manual Claude account add*, *List Claude accounts*, and
*Disable and re-enable Claude accounts* requirements.

The schemas inherit ``DashboardModel`` so that camelCase JSON aliases are
preserved on serialization without per-field ``serialization_alias`` lines;
this matches the convention used by ``app/modules/accounts/schemas.py``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.modules.shared.schemas import DashboardModel


def _strip_or_none(value: str | None) -> str | None:
    """Trim a string-or-None and treat empty strings as None.

    Used by optional fields where the operator might paste whitespace and we
    want to normalize that to a missing value rather than re-persist " ".
    """
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class AddClaudeAccountRequest(DashboardModel):
    """Body of ``POST /api/claude/accounts``.

    All token fields are required and are encrypted server-side before
    persistence; they MUST NOT appear in any response payload.
    """

    claude_account_uuid: str = Field(
        min_length=1,
        max_length=128,
        description="Anthropic account UUID for the OAuth subscription.",
    )
    access_token: str = Field(
        min_length=1,
        description="OAuth-issued access token (sk-ant-oat01-…).",
    )
    refresh_token: str = Field(
        min_length=1,
        description="OAuth-issued refresh token.",
    )
    expires_in_seconds: int = Field(
        gt=0,
        le=86400 * 30,
        description="Lifetime of the access token in seconds (server rejects > 30 days).",
    )
    scopes: list[str] | None = Field(
        default=None,
        description="Optional list of OAuth scopes; stored normalized and unique.",
    )
    user_email: str | None = Field(
        default=None,
        max_length=320,
        description="Optional user email associated with the subscription.",
    )
    user_organization_uuid: str | None = Field(
        default=None,
        max_length=128,
        description="Optional organization UUID the subscription belongs to.",
    )

    @field_validator("claude_account_uuid", "access_token", "refresh_token")
    @classmethod
    def _no_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("must not be blank")
        return trimmed

    @field_validator("user_email")
    @classmethod
    def _trim_email(cls, value: str | None) -> str | None:
        return _strip_or_none(value)

    @field_validator("user_organization_uuid")
    @classmethod
    def _trim_uuid(cls, value: str | None) -> str | None:
        return _strip_or_none(value)

    @field_validator("scopes")
    @classmethod
    def _normalize_scopes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        seen: set[str] = set()
        for scope in value:
            if not isinstance(scope, str):
                raise ValueError("scopes entries must be strings")
            stripped = scope.strip()
            if not stripped:
                continue
            if stripped in seen:
                continue
            seen.add(stripped)
            cleaned.append(stripped)
        return sorted(cleaned) or None


class DisableClaudeAccountRequest(DashboardModel):
    """Body of ``PATCH /api/claude/accounts/{id}/disable``.

    ``reason`` is recorded in ``accounts.deactivation_reason`` so operators can
    tell manual disables from refresh-time ``invalid_grant`` disables later.
    """

    reason: str | None = Field(default=None, max_length=512)

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: str | None) -> str | None:
        return _strip_or_none(value)


class ClaudeAccountResponse(DashboardModel):
    """Public representation of a Claude account row.

    Plaintext tokens SHALL NOT be serialized — this is enforced both by the
    schema (no token fields here) and by repository-level redaction tests in
    ``tests/unit/test_claude_schemas.py``.
    """

    id: str
    claude_account_uuid: str
    user_email: str | None = None
    user_organization_uuid: str | None = None
    is_active: bool
    claude_access_token_expires_at: datetime | None = None
    last_used_at: datetime | None = None
    rate_limit_requests_remaining: int | None = None
    rate_limit_input_tokens_remaining: int | None = None
    rate_limit_output_tokens_remaining: int | None = None
    rate_limit_status: str | None = None
    created_at: datetime


class ListClaudeAccountsResponse(DashboardModel):
    """Body of ``GET /api/claude/accounts``."""

    accounts: list[ClaudeAccountResponse] = Field(default_factory=list)
