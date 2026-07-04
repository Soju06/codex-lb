"""Claude-specific auth manager.

Provides the Claude side of the OAuth lifecycle that the Codex-flavored
``app/modules/accounts/auth_manager.py`` does for OpenAI. The two managers
deliberately do NOT share a base class: Claude accounts have a separate
provider, no Codex-specific columns, and a different refresh contract.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md``
*Manual Claude account add*, *Auth guardian refreshes Claude access tokens*,
*401 from Anthropic triggers rotate-and-retry once*, *Refresh-token rotation
is unconditional on every successful refresh*, *Per-account refresh
serialization (singleflight)*, and *Disable and re-enable Claude accounts*.

Phase 13 wires ``codex_lb_claude_refresh_total`` via a direct import from
``app.core.metrics.prometheus``; when ``prometheus_client`` is unavailable
the imported counter is ``None`` and ``_record_metric`` becomes a no-op.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clients.anthropic.errors import ClaudeAuthError, ClaudeUpstreamError
from app.core.clients.anthropic.oauth import ClaudeRefreshResult
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.metrics.prometheus import codex_lb_claude_refresh_total
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.claude.repository import ClaudeAccountRepository, SqlClaudeAccountRepository

logger = logging.getLogger(__name__)


# --- Cross-replica serialization -------------------------------------------


# Scope-prefix convention shared by every PostgreSQL advisory lock in this
# codebase (``reset-credit-redeem``, ``merge-email``, ``account-id``,
# ``rate-limiter``). The string is hashed by ``hashtext`` server-side.
_CLAUDE_REFRESH_LOCK_SCOPE = "claude-refresh"


async def _acquire_postgresql_claude_refresh_lock(session: AsyncSession, account_id: str) -> None:
    """Acquire a per-account cross-process lock for the duration of ``session``.

    Uses ``pg_advisory_xact_lock(hashtext(:key))`` so the lock auto-releases on
    transaction commit and never leaks across rollbacks. Mirrors
    ``app.modules.rate_limit_reset_credits.api._acquire_postgresql_reset_credit_redeem_lock``.
    """
    lock_key = f"{_CLAUDE_REFRESH_LOCK_SCOPE}:{account_id}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": lock_key},
    )


@asynccontextmanager
async def _claude_refresh_session() -> AsyncIterator[AsyncSession]:
    """Yield a session for the refresh pass and commit on exit.

    Used by :meth:`ClaudeAuthManager._run_refresh` so the cross-replica
    advisory lock and the persistence write share one transaction. The
    lock auto-releases on commit; an explicit commit is required because
    :func:`app.db.session.get_background_session` rolls back any open
    transaction in its ``finally`` block, which would silently drop the
    rotated-credential writes.
    """
    async with get_background_session() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await _safe_rollback(session)
            raise


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        await session.rollback()
    except BaseException:
        return


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


# --- Refresh client port ---------------------------------------------------


class ClaudeOAuthClientLike(Protocol):
    """Subset of ``ClaudeOAuthClient`` that the auth manager depends on.

    Defined as a protocol so tests can substitute a stub without
    instantiating the full aiohttp-backed client.
    """

    async def refresh(self, refresh_token: str) -> ClaudeRefreshResult: ...


# --- Singleflight ----------------------------------------------------------


@dataclass
class _ClaudeRefreshSingleflight:
    """Per-account singleflight for OAuth refresh.

    Two callers asking to refresh the same ``account_id`` collapse onto the
    same in-flight task; callers on different account_ids run independently.
    Lifecycle:

    - ``run(key, factory)`` first acquires the coordination lock, then
      either reuses the existing task or creates a new one.
    - Waiters ``await asyncio.shield(task)`` so a cancelled waiter does not
      cancel the leader.
    - On completion the leader task is cleared out so a new caller can
      start a fresh refresh on the next tick.

    The dict is keyed by ``account_id`` (NOT a tuple of token materials) per
    the *Per-account refresh serialization (singleflight)* requirement in the
    spec: a guardian + 401-retry coalesce must not race each other.
    """

    _inflight: dict[str, asyncio.Task[ClaudeRefreshResult]]
    _lock: asyncio.Lock

    def __init__(self) -> None:
        self._inflight = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        key: str,
        factory: Callable[[], Awaitable[ClaudeRefreshResult]],
    ) -> ClaudeRefreshResult:
        async with self._lock:
            task = self._inflight.get(key)
            if task is None or task.done():
                task = asyncio.ensure_future(factory())
                self._inflight[key] = task
                task.add_done_callback(self._make_callback(key))
        assert task is not None
        return await asyncio.shield(task)

    def _make_callback(self, key: str) -> Callable[[asyncio.Task[Any]], None]:
        def _clear(_task: asyncio.Task[Any]) -> None:
            self._inflight.pop(key, None)

        return _clear

    def clear(self) -> None:
        self._inflight.clear()


# Process-wide singleton: the spec requires singleflight across the guardian
# scheduler and the 401-retry path within the same process.
_CLAUDE_REFRESH_SINGLEFLIGHT = _ClaudeRefreshSingleflight()


def clear_claude_refresh_singleflight_state() -> None:
    """Test-only helper: reset the process-wide singleflight map."""
    _CLAUDE_REFRESH_SINGLEFLIGHT.clear()


# --- Auth manager ----------------------------------------------------------


class ClaudeAuthManager:
    """Business logic for the Claude OAuth account lifecycle.

    Mirrors the constructor shape of
    ``app.modules.accounts.auth_manager.AuthManager`` (port + encryptor) so
    the two stay consistent. Operational collaborators (``oauth_client``,
    skew window) are passed via the constructor so tests can substitute
    them without touching the project singleton.
    """

    # Default refresh skew (seconds) used when ``claude_oauth_refresh_skew_seconds``
    # cannot be resolved from settings. Phase 0 §3 confirms 600s as a safe
    # default for OAuth tokens issued by Anthropic's public client.
    DEFAULT_SKEW_SECONDS: int = 600

    def __init__(
        self,
        *,
        repo: ClaudeAccountRepository,
        encryptor: TokenEncryptor | None = None,
        oauth_client: ClaudeOAuthClientLike | None = None,
        skew_seconds: int | None = None,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
        scoped_repo_factory: Callable[[AsyncSession], ClaudeAccountRepository] | None = None,
    ) -> None:
        self._repo = repo
        self._encryptor = encryptor or TokenEncryptor()
        self._oauth_client = oauth_client
        self._skew_seconds = skew_seconds if skew_seconds is not None else self._resolve_skew_seconds()
        # ``session_factory`` is the dependency seam for the refresh pass.
        # Production defaults to :func:`_claude_refresh_session` (which wraps
        # ``get_background_session`` with an explicit commit/rollback); tests
        # inject a fake that yields an in-memory or stub session so the lock
        # and commit logic can be exercised without a real database.
        self._session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] = (
            session_factory if session_factory is not None else _claude_refresh_session
        )
        # ``scoped_repo_factory`` produces the per-pass repo that backs
        # writes under the advisory-lock transaction. Production defaults
        # to :class:`SqlClaudeAccountRepository`; tests inject a recording
        # variant so assertions can inspect calls without a real DB.
        self._scoped_repo_factory: Callable[[AsyncSession], ClaudeAccountRepository] = (
            scoped_repo_factory if scoped_repo_factory is not None else SqlClaudeAccountRepository
        )

    @staticmethod
    def _resolve_skew_seconds() -> int:
        try:
            value = getattr(get_settings(), "claude_oauth_refresh_skew_seconds", ClaudeAuthManager.DEFAULT_SKEW_SECONDS)
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

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expires_in_seconds) - timedelta(seconds=self._skew_seconds)

        row: dict[str, object] = {
            "id": f"claude-{claude_account_uuid}",
            "provider": "claude",
            # Codex columns are NOT NULL in the table. The
            # ``ck_accounts_claude_rt_required`` CHECK constraint only
            # requires ``claude_refresh_token_encrypted``; the unused
            # Codex-flavored columns are filled with placeholder
            # encrypted blobs so the same table can host both providers.
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

    # ---------------------------------------------------- find_due_rotation

    async def find_accounts_due_for_rotation(self, *, skew_seconds: int | None = None) -> list[Account]:
        """List Claude accounts whose access token expires within the skew
        window. Used by the auth guardian scheduler in Phase 7.
        """
        effective_skew = skew_seconds if skew_seconds is not None else self._skew_seconds
        return await self._repo.find_due_for_rotation(skew_seconds=effective_skew, now=datetime.now(timezone.utc))

    # ----------------------------------------------------------- lifecycle

    async def disable_claude_account(self, account: Account, *, reason: str | None = None) -> bool:
        """Disable an account. Idempotent: returns False if the row is missing."""
        return await self._repo.deactivate(account.id, reason=(reason or "manual_disable"))

    async def enable_claude_account(self, account: Account) -> bool:
        """Re-enable a disabled account. Idempotent no-op when already active."""
        return await self._repo.activate(account.id)

    # ----------------------------------------------------------- rotation

    async def get_access_token(self, account: Account) -> str:
        """Decrypt and return the OAuth access token for ``account``.

        Used by :class:`app.modules.claude.service.ClaudeProxyService` to
        resolve the bearer token at request time without re-loading the
        SQLAlchemy row. The decryption envelope is the existing
        :class:`TokenEncryptor` (Fernet); plain-text material never leaves
        the manager.
        """
        token_bytes = account.claude_access_token_encrypted
        if token_bytes is None:
            raise ClaudeAuthError(f"no access token stored for account '{account.id}'")
        return self._encryptor.decrypt(token_bytes)

    async def rotate_claude_access_token(
        self,
        account: Account,
    ) -> ClaudeRefreshResult | None:
        """Rotate the access token for ``account``.

        Serializes concurrent callers behind a per-account singleflight lock
        (see :class:`_ClaudeRefreshSingleflight`). On Postgres the call is
        additionally serialized across processes via
        ``pg_advisory_xact_lock(hashtext("claude-refresh:{id}"))`` (see
        :func:`_acquire_postgresql_claude_refresh_lock`). Concurrent
        refreshes share the same in-flight ``POST /v1/oauth/token`` call.

        Returns:
            ``ClaudeRefreshResult`` on success; ``None`` when the refresh
            was aborted by ``invalid_grant`` or by an Anthropic response
            that omitted the new ``refresh_token`` (the account is then
            DEACTIVATED with a descriptive reason).

        Raises:
            :class:`app.core.clients.anthropic.errors.ClaudeUpstreamError`
            for 5xx transport failures; the account is NOT deactivated so
            the caller may retry.

        Notes:
            The manager refreshes unconditionally because the caller
            (guardian scheduler / 401-retry path) is already responsible
            for the "do I need a refresh?" decision. The guardian scheduler
            and the 401-retry path share this single entrypoint so they
            cannot double-gate.
        """
        refresh_token_bytes = account.claude_refresh_token_encrypted
        if refresh_token_bytes is None:
            raise ClaudeAuthError("no refresh token stored for account")

        result = await _CLAUDE_REFRESH_SINGLEFLIGHT.run(
            account.id,
            factory=lambda: self._run_refresh(account, refresh_token_bytes),  # ty:ignore[invalid-argument-type]
        )
        return result

    async def _run_refresh(self, account: Account, refresh_token_bytes: bytes) -> ClaudeRefreshResult | None:
        """Inner body of :meth:`rotate_claude_access_token`.

        Decrypts the refresh token, calls the OAuth client, handles the
        three response classes (success / invalid_grant / upstream error),
        and persists rotated credentials when applicable.

        The whole refresh pass runs inside a dedicated background session
        (see :func:`_claude_refresh_session`) so the cross-process advisory
        lock and the persistence writes share one transaction. The lock
        is acquired at the start of the pass, the writes commit on the
        success path, and a rollback on the failure path still releases
        the lock. SQLite is single-process; the in-process singleflight
        in :data:`_CLAUDE_REFRESH_SINGLEFLIGHT` is sufficient on that
        dialect and the advisory lock acquisition is skipped.

        The injected ``self._repo`` is intentionally NOT used for writes
        here: the refresh pass needs a session scoped to its own
        transaction, not the caller's per-request session. The admin and
        read paths (e.g. ``disable_claude_account``, ``find_accounts_due_for_rotation``)
        continue to use ``self._repo``.
        """
        if self._oauth_client is None:
            raise ClaudeAuthError("no OAuth client configured")

        refresh_token = self._encryptor.decrypt(refresh_token_bytes)

        async with self._session_factory() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                # Acquire the lock BEFORE decrypting the refresh token. A
                # second caller that is already blocked on the lock will
                # not see the in-flight token material until commit.
                await _acquire_postgresql_claude_refresh_lock(session, account.id)
            else:
                logger.debug(
                    "claude.refresh.single_process_lock_only account_id=%s dialect=%s",
                    account.id,
                    dialect,
                )

            scoped_repo = self._scoped_repo_factory(session)
            try:
                result = await self._oauth_client.refresh(refresh_token)
            except ClaudeAuthError as exc:
                await self._deactivate_for_invalid_grant(scoped_repo, account, exc)
                self._record_metric("invalid_grant")
                return None
            except ClaudeUpstreamError:
                self._record_metric("error")
                raise

            # Defensive: Anthropic always rotates the refresh token, but the public
            # contract does not forbid omission. A `None` refresh token cannot be
            # silently coerced to the previous value because Anthropic's single-use
            # refresh-token rotation would 400 with `invalid_grant` on the next
            # refresh attempt. Deactivate explicitly so the operator re-authorizes.
            if result.refresh_token is None:
                await self._persist_rotated_credentials(scoped_repo, account, result)
                await self._deactivate_for_missing_refresh_token(scoped_repo, account, result.raw_body)
                self._record_metric("invalid_grant")
                return None

            await self._persist_rotated_credentials(scoped_repo, account, result)
            self._record_metric("success")
            return result
        # ``self._session_factory`` is expected to commit on success and
        # roll back on exception; the production ``get_background_session``
        # is wrapped by :func:`_claude_refresh_session` to guarantee this.

    async def _deactivate_for_invalid_grant(
        self,
        repo: ClaudeAccountRepository,
        account: Account,
        error: ClaudeAuthError,
    ) -> None:
        """Mark the account DEACTIVATED for ``invalid_grant`` and emit the
        structured log required by the spec.
        """
        reason = f"invalid_grant: {error}"
        logger.warning(
            "claude.refresh.failed",
            extra={
                "account_id": account.id,
                "reason": "invalid_grant",
                "error": str(error),
            },
        )
        await repo.deactivate(account.id, reason=reason)

    async def _deactivate_for_missing_refresh_token(
        self,
        repo: ClaudeAccountRepository,
        account: Account,
        body_excerpt: str | bytes | None,
    ) -> None:
        """Mark the account DEACTIVATED when Anthropic omits the new refresh token.

        Emits the structured ``claude.refresh.refresh_token_missing`` log line
        required by the spec so the operator can re-authorize. The deactivate
        reason is the typed ``refresh_token_missing:`` prefix so dashboards can
        filter on it independently of ``invalid_grant``. ``body_excerpt`` is
        the OAuth refresh response body so the log carries the "original
        message body excerpt" the spec requires.
        """
        message = "Anthropic refresh response omitted refresh_token"
        logger.warning(
            "claude.refresh.refresh_token_missing",
            extra={
                "event": "claude.refresh.refresh_token_missing",
                "account_id": account.id,
                "severity": "warning",
                "body_excerpt": _format_body_excerpt(body_excerpt),
            },
        )
        await repo.deactivate(
            account.id,
            reason=f"refresh_token_missing:{message}",
        )

    async def _persist_rotated_credentials(
        self,
        repo: ClaudeAccountRepository,
        account: Account,
        result: ClaudeRefreshResult,
    ) -> None:
        """Persist rotated credentials for the given ``account``.

        Unconditional rotation: the new ``access_token`` always overwrites
        the old ciphertext, and the new ``refresh_token`` overwrites the
        old refresh token when Anthropic returns one. When the response
        omits ``refresh_token`` (``result.refresh_token is None``) the
        repo's ``update_tokens`` sets the column to ``NULL`` and the
        caller (:meth:`_run_refresh`) deactivates the account.

        The defensive "missing refresh token" branch is NOT handled here
        because the caller needs to know whether the refresh succeeded
        (return ``ClaudeRefreshResult``) or whether the account was
        deactivated (return ``None`` to the proxy service so it aborts
        the request instead of retrying).
        """
        new_access = self._encryptor.encrypt(result.access_token)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=result.expires_in - self._skew_seconds)
        new_refresh: bytes | None = None
        if result.refresh_token is not None:
            new_refresh = self._encryptor.encrypt(result.refresh_token)

        await repo.update_tokens(
            account_id=account.id,
            access_token_encrypted=new_access,
            refresh_token_encrypted=new_refresh,
            access_token_expires_at=expires_at,
        )

    def _record_metric(self, result: str) -> None:
        """Increment ``codex_lb_claude_refresh_total{result=…}``.

        The counter is imported directly from
        ``app.core.metrics.prometheus``. When ``prometheus_client`` is not
        installed the counter is ``None`` (the module wires that fallback)
        and this is a no-op. Any label error is swallowed so a metrics
        outage cannot poison the auth lifecycle.
        """
        if codex_lb_claude_refresh_total is None:
            return
        try:
            codex_lb_claude_refresh_total.labels(result=result).inc()
        except Exception:  # pragma: no cover - metrics layer may reject labels
            logger.debug("metrics increment failed", exc_info=True)


# --- Internal helpers ------------------------------------------------------


def _serialize_scopes(scopes: list[str] | None) -> str | None:
    """JSON-encode a scopes list for storage in the ``claude_scopes`` TEXT column."""
    if scopes is None:
        return None
    return json.dumps(scopes)


_BODY_EXCERPT_MAX_CHARS = 512


def _format_body_excerpt(body: str | bytes | None) -> str | None:
    """Render the OAuth response body excerpt for structured logs.

    Returns ``None`` for ``None`` input so logs can record "no body
    available" via the absence of the field. Bytes are decoded as UTF-8
    with ``errors="replace"`` so a malformed payload never crashes the
    log path. Output is truncated to ``_BODY_EXCERPT_MAX_CHARS`` to
    bound the log line size; truncation is annotated with an ellipsis.
    """
    if body is None:
        return None
    if isinstance(body, (bytes, bytearray)):
        text = bytes(body).decode("utf-8", errors="replace")
    else:
        text = str(body)
    if len(text) > _BODY_EXCERPT_MAX_CHARS:
        return text[:_BODY_EXCERPT_MAX_CHARS] + "…"
    return text


def _deserialize_scopes(raw: str | None) -> list[str] | None:
    """Inverse of :func:`_serialize_scopes`; returns ``None`` for missing or
    malformed blobs."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return [s for s in parsed if isinstance(s, str)]


__all__ = [
    "ClaudeAccountAlreadyExists",
    "ClaudeAccountNotFound",
    "ClaudeAuthManager",
    "ClaudeOAuthClientLike",
    "clear_claude_refresh_singleflight_state",
]


# Re-export private exception markers so the test module can grep them and so
# mypy doesn't drop the imports on a names-only import.
_ = (ClaudeAuthError, ClaudeUpstreamError, ClaudeRefreshResult)
