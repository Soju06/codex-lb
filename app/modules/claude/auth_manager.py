"""Claude-specific auth manager.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md``
*Manual Claude account add*.

Provides the Claude side of the OAuth account lifecycle. The Codex-flavored
counterpart lives in ``app/modules/accounts/auth_manager.py``. The two
managers deliberately do NOT share a base class: Claude accounts live on a
separate ``provider`` discriminator, with no Codex-specific columns and a
different refresh contract.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.claude.repository import ClaudeAccountRepository

logger = logging.getLogger(__name__)


# --- Public exceptions -----------------------------------------------------


class ClaudeAccountAlreadyExists(Exception):
    """Raised when an admin tries to add a Claude account whose UUID is
    already taken (HTTP 409 per the spec).
    """

    def __init__(self, claude_uuid: str) -> None:
        self.claude_uuid = claude_uuid
        super().__init__(f"Claude account already exists for uuid '{claude_uuid}'")


class ClaudeAccountNotFound(Exception):
    """Raised when a referenced Claude account id is missing in the repo."""


# --- Refresh client port (placeholder; rotation added in Phase 6.3) ---------


class ClaudeOAuthClientLike(Protocol):
    """Subset of ``ClaudeOAuthClient`` that the auth manager depends on.

    Defined as a protocol so tests can substitute a stub without
    instantiating the full aiohttp-backed client.
    """

    async def refresh(self, refresh_token: str) -> Any: ...


# --- Auth manager ----------------------------------------------------------


class ClaudeAuthManager:
    """Business logic for the Claude OAuth account lifecycle.

    Mirrors the constructor shape of
    ``app.modules.accounts.auth_manager.AuthManager`` (port + encryptor) so
    the two stay consistent. Operational collaborators (``oauth_client``,
    skew window) are passed via the constructor so tests can substitute
    them without touching the project singleton.
    """

    # Default refresh skew (seconds). Phase 0 §3 confirms 600s as a safe
    # default for OAuth tokens issued by Anthropic's public client; this
    # also gates the ``add_claude_account`` expiry cut-off so the guardian
    # refreshes BEFORE the upstream-supplied deadline.
    DEFAULT_SKEW_SECONDS: int = 600

    def __init__(
        self,
        *,
        repo: ClaudeAccountRepository,
        encryptor: TokenEncryptor | None = None,
        skew_seconds: int | None = None,
    ) -> None:
        self._repo = repo
        self._encryptor = encryptor or TokenEncryptor()
        self._skew_seconds = (
            skew_seconds if skew_seconds is not None else self._resolve_skew_seconds()
        )

    @staticmethod
    def _resolve_skew_seconds() -> int:
        try:
            value = getattr(
                get_settings(),
                "claude_oauth_refresh_skew_seconds",
                ClaudeAuthManager.DEFAULT_SKEW_SECONDS,
            )
            return int(value)
        except Exception:
            return ClaudeAuthManager.DEFAULT_SKEW_SECONDS

    # ------------------------------------------------------------------ add

    async def add_claude_account(
        self,
        *,
        claude_account_uuid: str,
        access_token: str,
        refresh_token: str,
        expires_in_seconds: int,
        scopes: list[str] | None,
        user_email: str | None,
        user_organization_uuid: str | None,
    ) -> str:
        """Persist a new Claude account row, returning its primary-key id.

        Raises :class:`ClaudeAccountAlreadyExists` if the UUID already
        exists for a ``provider='claude'`` row. Tokens are encrypted via
        the existing crypto envelope; the stored
        ``claude_access_token_expires_at`` is shifted earlier by
        ``skew_seconds`` so the auth guardian refreshes before the
        upstream-supplied deadline.
        """
        if await self._repo.exists_by_claude_uuid(claude_account_uuid):
            raise ClaudeAccountAlreadyExists(claude_account_uuid)

        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expires_in_seconds) - timedelta(
            seconds=self._skew_seconds
        )

        row: dict[str, object] = {
            "id": f"claude-{claude_account_uuid}",
            "provider": "claude",
            # Codex columns are NOT NULL in the table. The
            # ``ck_accounts_claude_rt_required`` CHECK constraint only
            # requires ``claude_refresh_token_encrypted``; the unused
            # Codex-flavored columns are filled with placeholder encrypted
            # blobs so the same table can host both providers.
            "plan_type": "claude_subscription",
            "routing_policy": "normal",
            "access_token_encrypted": self._encryptor.encrypt("claude"),
            "refresh_token_encrypted": self._encryptor.encrypt("claude"),
            "id_token_encrypted": self._encryptor.encrypt("claude"),
            "last_refresh": now,
            "claude_account_uuid": claude_account_uuid,
            "claude_access_token_encrypted": self._encryptor.encrypt(access_token),
            "claude_refresh_token_encrypted": self._encryptor.encrypt(refresh_token),
            "claude_access_token_expires_at": expires_at,
            "claude_scopes": _serialize_scopes(scopes),
            "claude_user_email": user_email,
            "claude_user_organization_uuid": user_organization_uuid,
            "status": AccountStatus.ACTIVE.value,
        }
        created = await self._repo.insert(row)
        return created.id


# --- Internal helpers ------------------------------------------------------


def _serialize_scopes(scopes: list[str] | None) -> str | None:
    """JSON-encode a scopes list for storage in the ``claude_scopes`` TEXT column."""
    if scopes is None:
        return None
    return json.dumps(scopes)


__all__ = [
    "ClaudeAccountAlreadyExists",
    "ClaudeAccountNotFound",
    "ClaudeAuthManager",
    "ClaudeOAuthClientLike",
]
