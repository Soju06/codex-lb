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

The metrics layer is conditionally resolved at call time — when Phase 13
adds ``codex_lb_claude_refresh_total`` in
``app/core/metrics/prometheus.py`` this module will see it without changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from app.core.clients.anthropic.errors import ClaudeAuthError, ClaudeUpstreamError
from app.core.clients.anthropic.oauth import ClaudeRefreshResult
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


# --- Refresh client port ---------------------------------------------------


class ClaudeOAuthClientLike(Protocol):
    """Subset of ``ClaudeOAuthClient`` that the auth manager depends on.

    Defined as a protocol so tests can substitute a stub without
    instantiating the full aiohttp-backed client.
    """

    async def refresh(self, refresh_token: str) -> ClaudeRefreshResult: ...


# --- Metric shim -----------------------------------------------------------
#
# Phase 13 introduces ``codex_lb_claude_refresh_total{result="…"}`` in
# ``app/core/metrics/prometheus.py``. Until that lands we resolve counters
# defensively: missing attributes or missing modules return ``None`` and
# every call site uses ``if metric is not None: metric.inc()``. Phase 13
# MUST replace this shim with a direct import — the public surface
# (``codex_lb_claude_refresh_total``) is what tests assert against so the
# swap is observation-equivalent.


def _resolve_metric(name: str) -> Any | None:
    """Return the named Prometheus counter from ``app.core.metrics.prometheus``
    when the symbol is registered, otherwise ``None``.
    """
    try:
        from app.core.metrics import prometheus as prom  # noqa: PLC0415
    except Exception:  # pragma: no cover - metrics module not yet wired
        return None
    return getattr(prom, name, None)


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
    ) -> None:
        self._repo = repo
        self._encryptor = encryptor or TokenEncryptor()
        self._oauth_client = oauth_client
        self._skew_seconds = (
            skew_seconds
            if skew_seconds is not None
            else self._resolve_skew_seconds()
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
        expires_at = now + timedelta(seconds=expires_in_seconds) - timedelta(
            seconds=self._skew_seconds
        )

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

    async def find_accounts_due_for_rotation(
        self, *, skew_seconds: int | None = None
    ) -> list[Account]:
        """List Claude accounts whose access token expires within the skew
        window. Used by the auth guardian scheduler in Phase 7.
        """
        effective_skew = skew_seconds if skew_seconds is not None else self._skew_seconds
        return await self._repo.find_due_for_rotation(
            skew_seconds=effective_skew, now=datetime.now(timezone.utc)
        )

    # ----------------------------------------------------------- lifecycle

    async def disable_claude_account(
        self, account: Account, *, reason: str | None = None
    ) -> bool:
        """Disable an account. Idempotent: returns False if the row is missing."""
        return await self._repo.deactivate(
            account.id, reason=(reason or "manual_disable")
        )

    async def enable_claude_account(self, account: Account) -> bool:
        """Re-enable a disabled account. Idempotent no-op when already active."""
        return await self._repo.activate(account.id)

    # ----------------------------------------------------------- rotation

    async def rotate_claude_access_token(
        self,
        account: Account,
        *,
        force: bool = False,
    ) -> ClaudeRefreshResult | None:
        """Rotate the access token for ``account``.

        Serializes concurrent callers behind a per-account singleflight lock
        (see :class:`_ClaudeRefreshSingleflight`). Concurrent refreshes
        share the same in-flight ``POST /v1/oauth/token`` call.

        Returns:
            ``ClaudeRefreshResult`` on success; ``None`` when the refresh
            was aborted by ``invalid_grant`` (the account is then
            DEACTIVATED with reason ``invalid_grant: <body>``).

        Raises:
            :class:`app.core.clients.anthropic.errors.ClaudeUpstreamError`
            for 5xx transport failures; the account is NOT deactivated so
            the caller may retry.

        Notes:
            ``force`` is reserved for the 401-retry path. Today the manager
            refreshes unconditionally because the caller (guardian /
            401-retry) is already responsible for the "do I need a
            refresh?" decision. The parameter is in the signature so the
            guardian scheduler and the 401-retry path share a single
            entrypoint that does NOT double-gate.
        """
        refresh_token_bytes = account.claude_refresh_token_encrypted
        if refresh_token_bytes is None:
            raise ClaudeAuthError("no refresh token stored for account")

        del force  # see docstring: gating is the caller's responsibility

        result = await _CLAUDE_REFRESH_SINGLEFLIGHT.run(
            account.id,
            factory=lambda: self._run_refresh(account, refresh_token_bytes),
        )
        return result

    async def _run_refresh(
        self, account: Account, refresh_token_bytes: bytes
    ) -> ClaudeRefreshResult:
        """Inner body of :meth:`rotate_claude_access_token`.

        Decrypts the refresh token, calls the OAuth client, handles the
        three response classes (success / invalid_grant / upstream error),
        and persists rotated credentials when applicable.
        """
        if self._oauth_client is None:
            raise ClaudeAuthError("no OAuth client configured")

        refresh_token = self._encryptor.decrypt(refresh_token_bytes)
        try:
            result = await self._oauth_client.refresh(refresh_token)
        except ClaudeAuthError as exc:
            await self._deactivate_for_invalid_grant(account, exc)
            self._record_metric("invalid_grant")
            return None
        except ClaudeUpstreamError:
            self._record_metric("error")
            raise

        await self._persist_rotated_credentials(account, result)
        self._record_metric("success")
        return result

    async def _deactivate_for_invalid_grant(
        self, account: Account, error: ClaudeAuthError
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
        await self._repo.deactivate(account.id, reason=reason)

    async def _persist_rotated_credentials(
        self, account: Account, result: ClaudeRefreshResult
    ) -> None:
        """Persist rotated credentials for the given ``account``.

        Unconditional rotation: when ``result.refresh_token`` is ``None``
        (defensive — never observed in verified captures) the existing
        refresh token bytes are discarded (column set to NULL) and the
        account is left flagged so a future re-authorize flow can surface
        it.
        """
        new_access = self._encryptor.encrypt(result.access_token)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=result.expires_in - self._skew_seconds
        )
        new_refresh: bytes | None = None
        if result.refresh_token is not None:
            new_refresh = self._encryptor.encrypt(result.refresh_token)

        await self._repo.update_tokens(
            account_id=account.id,
            access_token_encrypted=new_access,
            refresh_token_encrypted=new_refresh,
            access_token_expires_at=expires_at,
        )

    def _record_metric(self, result: str) -> None:
        """Increment ``codex_lb_claude_refresh_total{result=…}``.

        Resolves the counter lazily so the metrics module (Phase 13) is the
        single source of truth. When prometheus_client is unavailable or
        the counter is not yet registered, this is a no-op.
        """
        counter = _resolve_metric("codex_lb_claude_refresh_total")
        if counter is None:
            return
        try:
            counter.labels(result=result).inc()
        except Exception:  # pragma: no cover - metrics layer may reject labels
            logger.debug("metrics increment failed", exc_info=True)


# --- Internal helpers ------------------------------------------------------


def _serialize_scopes(scopes: list[str] | None) -> str | None:
    """JSON-encode a scopes list for storage in the ``claude_scopes`` TEXT column."""
    if scopes is None:
        return None
    return json.dumps(scopes)


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
