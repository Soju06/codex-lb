"""Repository port + SQLAlchemy implementation for Claude OAuth accounts.

The repository port is intentionally narrow: only the operations the auth
manager (and the future scheduler / admin endpoints) need are exposed. The
Codex-flavored ``AccountsRepositoryPort`` lives in
``app/modules/accounts/auth_manager.py`` and is intentionally NOT merged —
Claude auth does not need Codex-specific fields (workspace slots,
``chatgpt_account_id``, etc.) so a separate port keeps both surfaces small.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountStatus


@dataclass(frozen=True, slots=True)
class ClaudeAccountRow:
    """In-memory projection of a Claude account row.

    Returned by ``insert`` so the auth manager sees the canonical id + uuid
    it just wrote without coupling it to SQLAlchemy's ``Account``.
    """

    id: str
    claude_account_uuid: str


class ClaudeAccountRepository(Protocol):
    """Port for Claude-specific account storage."""

    async def exists_by_claude_uuid(self, claude_uuid: str) -> bool:
        """Return True if any ``provider='claude'`` row already claims this UUID."""

    async def insert(self, row: dict[str, object]) -> ClaudeAccountRow:
        """Persist a new Claude account. The ``row`` dict carries the same
        column -> value mapping the SQLAlchemy implementation expects and
        includes the encrypted token blobs plus the computed expiry."""

    async def get_by_id(self, account_id: str) -> Account | None:
        """Read the full ``Account`` row by primary key (used by the refresh
        path to re-encrypt the refresh token against the canonical
        SQLAlchemy instance)."""

    async def update_tokens(
        self,
        *,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes | None,
        access_token_expires_at: datetime,
    ) -> bool:
        """Persist rotated credentials. ``refresh_token_encrypted=None`` is a
        defensive branch: the spec mandates *unconditional rotation*, but the
        server has never omitted the new refresh token in verified captures;
        this slot stays for future-proofing rather than for correctness."""

    async def deactivate(self, account_id: str, *, reason: str) -> bool:
        """Mark the account DEACTIVATED with the given reason and return
        whether the row was actually updated (False = row not found)."""

    async def activate(self, account_id: str) -> bool:
        """Mark the account ACTIVE again, clearing the deactivation reason."""

    async def list_accounts(self) -> list[Account]:
        """Return all ``provider='claude'`` rows."""

    async def find_due_for_rotation(self, *, skew_seconds: int, now: datetime) -> list[Account]:
        """Return Claude accounts whose access token expires within the skew
        window (i.e. ``claude_access_token_expires_at <= now + skew_seconds``).
        Used by the auth guardian scheduler in Phase 7."""

    async def count_active(self) -> int:
        """Return the number of ``provider='claude'`` accounts with
        ``status = ACTIVE``. Drives the
        ``codex_lb_claude_accounts_active`` Prometheus gauge at scrape
        time (Phase 13, ``openspec/changes/add-claude-oauth-pool``)."""


class SqlClaudeAccountRepository:
    """SQLAlchemy-backed implementation of :class:`ClaudeAccountRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists_by_claude_uuid(self, claude_uuid: str) -> bool:
        result = await self._session.execute(
            select(Account.id)
            .where(Account.claude_account_uuid == claude_uuid)
            .where(Account.provider == "claude")
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def insert(self, row: dict[str, object]) -> ClaudeAccountRow:
        account = Account(**row)  # type: ignore[arg-type]
        self._session.add(account)
        await self._session.flush()
        return ClaudeAccountRow(id=account.id, claude_account_uuid=account.claude_account_uuid or "")

    async def get_by_id(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id)

    async def update_tokens(
        self,
        *,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes | None,
        access_token_expires_at: datetime,
    ) -> bool:
        # Always update both columns. When ``refresh_token_encrypted`` is
        # ``None`` the column is cleared (defensive branch in the spec —
        # Anthropic always rotates but a future server omission MUST NOT
        # preserve a possibly-stale token).
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(
                claude_access_token_encrypted=access_token_encrypted,
                claude_refresh_token_encrypted=refresh_token_encrypted,
                claude_access_token_expires_at=access_token_expires_at,
            )
            .returning(Account.id)
        )
        return result.scalar_one_or_none() is not None

    async def deactivate(self, account_id: str, *, reason: str) -> bool:
        # NOTE: ``Account`` has no ``is_active`` column. The ``status`` enum
        # alone conveys the active/deactivated state (``ACTIVE`` vs
        # ``DEACTIVATED``). The load-balancer candidate filter already
        # excludes non-ACTIVE accounts; setting ``deactivation_reason``
        # preserves the diagnostic for the dashboard.
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(
                status=AccountStatus.DEACTIVATED,
                deactivation_reason=reason,
            )
            .returning(Account.id)
        )
        return result.scalar_one_or_none() is not None

    async def activate(self, account_id: str) -> bool:
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
            .returning(Account.id)
        )
        return result.scalar_one_or_none() is not None

    async def list_accounts(self) -> list[Account]:
        result = await self._session.execute(select(Account).where(Account.provider == "claude").order_by(Account.id))
        return list(result.scalars().all())

    async def find_due_for_rotation(self, *, skew_seconds: int, now: datetime) -> list[Account]:
        from datetime import timedelta

        cutoff = now + timedelta(seconds=skew_seconds)
        result = await self._session.execute(
            select(Account)
            .where(Account.provider == "claude")
            .where(Account.status != AccountStatus.DEACTIVATED)
            .where(Account.claude_refresh_token_encrypted.is_not(None))
            .where(Account.claude_access_token_expires_at.is_not(None))
            .where(Account.claude_access_token_expires_at <= cutoff)
            .order_by(Account.claude_access_token_expires_at)
        )
        return list(result.scalars().all())

    async def count_active(self) -> int:
        """Count ``provider='claude'`` rows with ``status = ACTIVE``.

        Drives the ``codex_lb_claude_accounts_active`` Prometheus gauge.

        Note on semantics vs the load balancer's
        :func:`app.modules.proxy.load_balancer._selectable_accounts`:

        - ``count_active`` filters by ``status == ACTIVE`` only.
        - ``_selectable_accounts`` additionally excludes
          ``REAUTH_REQUIRED`` and ``PAUSED`` statuses.

        The two predicates can drift. ``REAUTH_REQUIRED`` and ``PAUSED``
        are Codex-side concepts; they are filtered for consistency with
        the Codex pool but are NOT counted here. If a future change adds
        a Claude-only "draining" state, this counter MUST be updated to
        match the load balancer's view so the gauge does not mislead
        operators.
        """
        from sqlalchemy import func

        result = await self._session.execute(
            select(func.count(Account.id))
            .where(Account.provider == "claude")
            .where(Account.status == AccountStatus.ACTIVE)
        )
        return int(result.scalar_one() or 0)
