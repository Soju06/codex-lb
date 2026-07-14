from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from hashlib import sha256
from typing import Any, Protocol, TypeAlias

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import DEFAULT_PLAN, OpenAIAuthClaims, extract_id_token_claims
from app.core.auth.refresh import (
    RefreshError,
    TokenRefreshResult,
    get_token_refresh_timeout_override,
    refresh_access_token,
    should_refresh,
)
from app.core.balancer import PERMANENT_FAILURE_CODES, account_status_for_permanent_failure
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.upstream_proxy import UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.time import utcnow
from app.db.models import Account, AccountProxyBinding, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.refresh_claims import RefreshClaimCoordinatorPort, get_refresh_claim_coordinator
from app.modules.proxy.account_cache import get_account_selection_cache, mark_account_routing_unavailable


class AccountsRepositoryPort(Protocol):
    async def get_by_id(self, account_id: str) -> Account | None: ...

    async def get_by_id_fresh(self, account_id: str) -> Account | None: ...

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
    ) -> bool: ...

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool: ...

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool: ...

    async def workspace_slot_taken(
        self,
        *,
        account_id: str,
        email: str,
        chatgpt_account_id: str | None,
        workspace_id: str,
    ) -> bool: ...


class RefreshAdmissionLeasePort(Protocol):
    def release(self) -> None: ...


logger = logging.getLogger(__name__)

# Bound on how many times a successful refresh retries its token compare-and-set
# against freshly observed ciphertext when a concurrent re-auth/import merely
# re-encrypts the same refresh-token plaintext (Fernet ciphertext is
# non-deterministic, so the same plaintext yields different bytes). Genuine
# newer rotations are adopted instead of retried, so this only guards the rare
# same-plaintext re-encryption race and must stay small to avoid live-locking.
_TOKEN_CAS_MAX_ATTEMPTS = 5


_RefreshSingleflightKey: TypeAlias = tuple[str, str]


class _RefreshSingleflight:
    def __init__(self) -> None:
        self._inflight: dict[_RefreshSingleflightKey, asyncio.Task[Account]] = {}
        self._recent_failures: dict[_RefreshSingleflightKey, tuple[float, tuple[str, str, bool]]] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        key: _RefreshSingleflightKey,
        factory: Callable[[], Coroutine[object, object, Account]],
    ) -> Account:
        account_id = key[0]
        async with self._lock:
            self._purge_stale_versions(account_id, keep_key=key)
            cached_failure = self._recent_failures.get(key)
            if cached_failure is not None:
                expires_at, failure = cached_failure
                if expires_at > time.monotonic():
                    code, message, is_permanent = failure
                    raise RefreshError(code, message, is_permanent)
                self._recent_failures.pop(key, None)
            task = self._inflight.get(key)
            if task is not None and task.done() and not task.cancelled() and task.exception() is None:
                pass
            elif task is None or task.done():
                task = asyncio.create_task(factory())
                self._inflight[key] = task
                task.add_done_callback(lambda done, *, cache_key=key: self._schedule_complete(cache_key, done))
        assert task is not None
        return await asyncio.shield(task)

    def _schedule_complete(self, key: _RefreshSingleflightKey, task: asyncio.Task[Account]) -> None:
        asyncio.create_task(self._complete(key, task))

    async def _complete(self, key: _RefreshSingleflightKey, task: asyncio.Task[Account]) -> None:
        try:
            async with self._lock:
                current = self._inflight.get(key)
                if current is task:
                    self._inflight.pop(key, None)
                if task.cancelled():
                    self._recent_failures.pop(key, None)
                    return
                try:
                    task.result()
                except RefreshError as exc:
                    ttl = max(0.0, float(get_settings().proxy_refresh_failure_cooldown_seconds))
                    if ttl > 0 and not exc.transport_error:
                        self._recent_failures[key] = (
                            time.monotonic() + ttl,
                            (exc.code, exc.message, exc.is_permanent),
                        )
                    else:
                        self._recent_failures.pop(key, None)
                except BaseException:
                    self._recent_failures.pop(key, None)
                else:
                    self._recent_failures.pop(key, None)
        except BaseException:
            logger.exception("Refresh singleflight completion cleanup failed key=%s", key)

    def _purge_stale_versions(self, account_id: str, *, keep_key: _RefreshSingleflightKey) -> None:
        stale_failures = [key for key in self._recent_failures if key[0] == account_id and key != keep_key]
        for key in stale_failures:
            self._recent_failures.pop(key, None)
        stale_inflight = [
            key for key, task in self._inflight.items() if key[0] == account_id and key != keep_key and task.done()
        ]
        for key in stale_inflight:
            self._inflight.pop(key, None)

    def clear(self) -> None:
        self._inflight.clear()
        self._recent_failures.clear()


_REFRESH_SINGLEFLIGHT = _RefreshSingleflight()


class AuthManager:
    def __init__(
        self,
        repo: AccountsRepositoryPort,
        *,
        acquire_refresh_admission: Callable[[], Awaitable[RefreshAdmissionLeasePort]] | None = None,
        refresh_repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepositoryPort]] | None = None,
        refresh_claims: RefreshClaimCoordinatorPort | None = None,
    ) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()
        self._acquire_refresh_admission = acquire_refresh_admission
        # Optional factory yielding a *fresh* accounts repo (own DB session) for
        # the detached, shielded refresh task. When set, the singleflight body
        # runs against this session instead of the request-scoped `repo`, so a
        # caller cancelled by a client disconnect cannot close the session out
        # from under the still-running refresh task and strand a pooled
        # connection. See _run_refresh.
        self._refresh_repo_factory = refresh_repo_factory
        # Cross-replica refresh claim coordinator. ``None`` defers to the
        # process default (see refresh_claims.get_refresh_claim_coordinator),
        # which the test harness may set to ``None`` to disable claims.
        self._refresh_claims = refresh_claims

    async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        if force or should_refresh(account.last_refresh):
            account = await _REFRESH_SINGLEFLIGHT.run(
                _refresh_singleflight_key(self._encryptor, account),
                lambda: self._run_refresh(account),
            )
        return await self._ensure_chatgpt_account_id(account)

    async def _run_refresh(self, account: Account) -> Account:
        """Singleflight body for token refresh.

        Runs inside a detached task that the singleflight keeps alive with
        ``asyncio.shield`` (so concurrent waiters share one refresh and a
        cancelled waiter does not abort it). Because the task outlives the
        caller, it MUST NOT use the caller's request-scoped session: when a
        client disconnects, the caller is cancelled and its
        ``async with get_background_session()`` closes that session, while this
        shielded task keeps running and would then touch a closed,
        concurrently-finalized ``AsyncSession`` (not safe for concurrent use) —
        stranding a pooled connection that never returns. When a
        ``refresh_repo_factory`` is provided, open a fresh session here so the
        refresh write is fully self-contained; otherwise fall back to the bound
        repo (callers whose session is not client-cancellable, e.g. the usage
        refresh scheduler).
        """
        if self._refresh_repo_factory is None:
            return await self.refresh_account(account)
        async with self._refresh_repo_factory() as repo:
            owned = AuthManager(
                repo,
                acquire_refresh_admission=self._acquire_refresh_admission,
                refresh_claims=self._refresh_claims,
            )
            return await owned.refresh_account(account)

    async def refresh_account(self, account: Account) -> Account:
        claims = self._refresh_claims if self._refresh_claims is not None else get_refresh_claim_coordinator()
        if claims is None:
            return await self._perform_refresh(account, refresh_token_encrypted=account.refresh_token_encrypted)
        return await self._refresh_account_with_claim(account, claims)

    async def _refresh_account_with_claim(
        self,
        account: Account,
        claims: RefreshClaimCoordinatorPort,
    ) -> Account:
        """Serialize the upstream token exchange across replicas.

        Exactly one claimant per account may run the OAuth exchange at a time;
        everyone else waits (bounded) for the winner's rotated tokens to land
        and adopts them without an upstream call. Refresh tokens are single-use
        upstream, so a second concurrent exchange would receive a permanent
        ``refresh_token_reused`` error and could revoke the token family.
        """
        settings = get_settings()
        requested_fingerprint = _refresh_token_material_fingerprint(
            self._encryptor,
            account.refresh_token_encrypted,
        )
        # The wait for a foreign claim is bounded by the configured cap AND the
        # caller's remaining refresh budget: the singleflight body is shielded
        # and outlives a cancelled caller, so without the budget cap a small
        # request budget with a held foreign claim would leave this task
        # polling for the full configured wait (holding its repo session and
        # the inflight singleflight entry that later callers join).
        wait_seconds = max(0.0, float(settings.token_refresh_claim_wait_seconds))
        caller_budget = get_token_refresh_timeout_override()
        if caller_budget is not None:
            wait_seconds = min(wait_seconds, max(0.0, caller_budget))
        deadline = time.monotonic() + wait_seconds
        poll_seconds = max(0.01, float(settings.token_refresh_claim_poll_seconds))
        # NOTE: comparisons below use the fingerprint captured at entry, not
        # ``account.refresh_token_encrypted``: when ``account`` is attached to
        # the repo's session, ``get_by_id_fresh`` refreshes that very
        # identity-map object in place, so comparing against the live attribute
        # would compare the row with itself.
        while True:
            if await claims.try_acquire(
                account.id,
                ttl_seconds=settings.token_refresh_claim_ttl_seconds,
                owner=requested_fingerprint,
            ):
                try:
                    # Post-claim fresh re-read: another replica may have rotated
                    # the material between the caller's read and our claim.
                    latest = await self._repo.get_by_id_fresh(account.id)
                    if latest is not None and (
                        _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
                        != requested_fingerprint
                    ):
                        return _adopt_account_row(account, latest)
                    fresh_material = (
                        latest.refresh_token_encrypted if latest is not None else account.refresh_token_encrypted
                    )
                    return await self._perform_refresh(account, refresh_token_encrypted=fresh_material)
                finally:
                    await claims.release(account.id, owner=requested_fingerprint)
            # Claim held by another replica: adopt its rotation as soon as it
            # commits; never write account status from the losing side.
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is not None and (
                _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
                != requested_fingerprint
            ):
                return _adopt_account_row(account, latest)
            if time.monotonic() >= deadline:
                raise RefreshError(
                    "refresh_claim_timeout",
                    f"Token refresh for account {account.id} is claimed by another replica; "
                    f"timed out waiting {wait_seconds:.3f}s for its rotation",
                    False,
                    transport_error=True,
                )
            await asyncio.sleep(poll_seconds)

    async def _perform_refresh(self, account: Account, *, refresh_token_encrypted: bytes) -> Account:
        attempted_fingerprint = _refresh_token_material_fingerprint(self._encryptor, refresh_token_encrypted)
        refresh_token = self._encryptor.decrypt(refresh_token_encrypted)
        try:
            result = await self._refresh_tokens(refresh_token, account=account)
        except RefreshError as exc:
            if exc.is_permanent:
                adopted = await self._handle_permanent_refresh_failure(account, exc, attempted_fingerprint)
                if adopted is not None:
                    return adopted
            raise

        new_access_token_encrypted = self._encryptor.encrypt(result.access_token)
        new_refresh_token_encrypted = self._encryptor.encrypt(result.refresh_token)
        new_id_token_encrypted = self._encryptor.encrypt(result.id_token)
        new_last_refresh = utcnow()
        new_chatgpt_account_id = result.account_id or account.chatgpt_account_id
        new_chatgpt_user_id = result.chatgpt_user_id or account.chatgpt_user_id
        if result.plan_type is not None:
            new_plan_type = coerce_account_plan_type(
                result.plan_type,
                account.plan_type or DEFAULT_PLAN,
            )
        elif not account.plan_type:
            new_plan_type = DEFAULT_PLAN
        else:
            new_plan_type = account.plan_type
        new_email = result.email or account.email
        incoming_workspace_id = _clean_optional(result.workspace_id)
        current_workspace_id = _clean_optional(account.workspace_id)
        next_workspace_id = current_workspace_id
        if incoming_workspace_id and current_workspace_id and current_workspace_id != incoming_workspace_id:
            logger.warning(
                "Refresh payload reported workspace_id=%s for account_id=%s while existing "
                "workspace_id=%s is already set; keeping slot identity",
                incoming_workspace_id,
                account.id,
                current_workspace_id,
            )
            next_workspace_id = current_workspace_id
        elif not current_workspace_id and incoming_workspace_id:
            slot_taken = await self._repo.workspace_slot_taken(
                account_id=account.id,
                email=new_email,
                chatgpt_account_id=new_chatgpt_account_id,
                workspace_id=incoming_workspace_id,
            )
            if slot_taken:
                logger.warning(
                    "Refresh payload reported workspace_id=%s for legacy account_id=%s, but that slot "
                    "is already owned by another account; keeping unknown workspace",
                    incoming_workspace_id,
                    account.id,
                )
            else:
                next_workspace_id = incoming_workspace_id
        workspace_matches_current_slot = incoming_workspace_id is None or incoming_workspace_id == next_workspace_id
        new_workspace_label = account.workspace_label
        new_seat_type = account.seat_type
        if workspace_matches_current_slot and result.workspace_label:
            new_workspace_label = result.workspace_label
        if workspace_matches_current_slot and result.seat_type:
            new_seat_type = result.seat_type

        async def _write_tokens(expected_refresh_token_encrypted: bytes | None) -> bool:
            return await self._repo.update_tokens(
                account.id,
                access_token_encrypted=new_access_token_encrypted,
                refresh_token_encrypted=new_refresh_token_encrypted,
                id_token_encrypted=new_id_token_encrypted,
                last_refresh=new_last_refresh,
                plan_type=new_plan_type,
                email=new_email,
                chatgpt_account_id=new_chatgpt_account_id,
                chatgpt_user_id=new_chatgpt_user_id or None,
                workspace_id=next_workspace_id,
                workspace_label=new_workspace_label,
                seat_type=new_seat_type,
                expected_refresh_token_encrypted=expected_refresh_token_encrypted,
            )

        adopted = await self._persist_refreshed_tokens(
            account,
            write=_write_tokens,
            expected_refresh_token_encrypted=refresh_token_encrypted,
            attempted_fingerprint=attempted_fingerprint,
        )
        if adopted is not None:
            return adopted

        account.access_token_encrypted = new_access_token_encrypted
        account.refresh_token_encrypted = new_refresh_token_encrypted
        account.id_token_encrypted = new_id_token_encrypted
        account.last_refresh = new_last_refresh
        account.chatgpt_account_id = new_chatgpt_account_id
        account.chatgpt_user_id = new_chatgpt_user_id
        account.plan_type = new_plan_type
        account.email = new_email
        account.workspace_id = next_workspace_id
        account.workspace_label = new_workspace_label
        account.seat_type = new_seat_type
        return account

    async def _persist_refreshed_tokens(
        self,
        account: Account,
        *,
        write: Callable[[bytes | None], Awaitable[bool]],
        expected_refresh_token_encrypted: bytes,
        attempted_fingerprint: str,
    ) -> Account | None:
        """Persist freshly rotated tokens with a compare-and-set on the exchanged material.

        Returns the latest account row to adopt when a peer committed a
        genuinely newer refresh-token rotation (different material) — that write
        must never be clobbered. Returns ``None`` when our own rotation was
        persisted (the caller then mirrors the new tokens onto its object), or
        when the row vanished.

        A compare-and-set miss does not by itself imply a newer rotation: a
        concurrent re-auth/import can re-encrypt the *same* refresh-token
        plaintext, and Fernet ciphertext is non-deterministic, so the stored
        bytes change without any new token being issued. Adopting that row would
        hand back the single-use token this attempt already consumed upstream,
        leaving the account active with invalid material. So on a miss we compare
        refresh-token fingerprints: a different fingerprint means a real peer
        rotation to adopt; the same fingerprint means our successful exchange
        holds the only valid tokens, and we retry the CAS against the freshly
        observed ciphertext so our rotation wins.

        If the CAS keeps missing on same-plaintext re-encryption until the
        bounded budget is exhausted, we MUST NOT drop the freshly issued token:
        the upstream exchange already consumed the single-use refresh token and
        issued a new one, so leaving the DB holding the consumed token would
        turn transient contention into a permanent ``refresh_token_reused`` /
        ``invalid_grant`` failure on the next refresh. After a final re-read that
        still shows no genuine peer rotation, we escalate to an unconditional
        write (keyed on account id only) so the DB ends with the usable token.
        Only when even that forced write cannot land (the row vanished) do we
        raise a transient error — and then the consumed token is not left as the
        authoritative stored value because the row no longer exists.
        """
        expected = expected_refresh_token_encrypted
        for _ in range(_TOKEN_CAS_MAX_ATTEMPTS):
            if await write(expected):
                return None
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is None:
                # Row is gone; nothing to persist or adopt.
                return None
            if (
                _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
                != attempted_fingerprint
            ):
                # A peer stored genuinely newer refresh-token material; adopt it
                # rather than overwriting with the token we already consumed.
                return _adopt_account_row(account, latest)
            # Same refresh-token plaintext, re-encrypted concurrently: retry the
            # CAS against the observed ciphertext so our rotation lands.
            expected = latest.refresh_token_encrypted
        # The compare-and-set never landed within the bounded budget, but the
        # upstream exchange already consumed the single-use refresh token and
        # issued a new one. The DB still holds the already-consumed token.
        # Dropping the freshly issued material here (returning ``None`` would
        # look like success; raising while leaving the consumed token stored
        # would guarantee a permanent ``refresh_token_reused`` on the next
        # refresh) would turn transient re-encryption contention into a
        # permanent account failure. So reconcile toward the NEW token: re-read
        # once more to adopt a genuine peer rotation if one just landed,
        # otherwise force an unconditional write of the freshly issued material
        # (keyed on account id only) so the DB ends with the usable token.
        latest = await self._repo.get_by_id_fresh(account.id)
        if latest is None:
            # Row is gone; nothing to persist or adopt.
            return None
        if (
            _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
            != attempted_fingerprint
        ):
            # A peer stored genuinely newer refresh-token material after our last
            # miss; adopt it rather than clobbering it with a forced write.
            return _adopt_account_row(account, latest)
        logger.warning(
            "Token-refresh compare-and-set for account_id=%s kept missing on re-encrypted "
            "same-plaintext material after %d attempts; forcing an unconditional write of the "
            "freshly issued tokens so the consumed token is not left stored",
            account.id,
            _TOKEN_CAS_MAX_ATTEMPTS,
        )
        if await write(None):
            # Forced write landed: the DB now holds the newly issued (usable)
            # refresh token, never the consumed one. Report success so the
            # caller mirrors the rotated material onto its in-memory account.
            return None
        # The unconditional write only fails when the row no longer exists, so
        # there is no consumed token left stored to reuse. Surface a transient
        # (non-permanent) failure so the caller retries the whole refresh rather
        # than proceeding with unpersisted material. ``transport_error`` keeps it
        # out of the permanent-failure cooldown cache.
        raise RefreshError(
            "token_persist_conflict",
            (
                f"Token-refresh compare-and-set for account_id={account.id} could not persist "
                f"rotated tokens after {_TOKEN_CAS_MAX_ATTEMPTS} attempts"
            ),
            False,
            transport_error=True,
        )

    async def _handle_permanent_refresh_failure(
        self,
        account: Account,
        exc: RefreshError,
        attempted_fingerprint: str,
    ) -> Account | None:
        """Persist a permanent refresh failure without clobbering a concurrent rotation.

        Returns the latest account row when its refresh-token material rotated
        after this attempt began (the caller adopts it instead of raising);
        returns ``None`` when the permanent failure stands. The comparison uses
        the fingerprint of the material this attempt exchanged, captured before
        the fresh re-read, because ``get_by_id_fresh`` may refresh the caller's
        own identity-map object in place.

        The status downgrade uses a compare-and-set conditioned on the freshly
        observed account state including the refresh-token ciphertext: a
        concurrent re-auth/import can change that ciphertext between the fresh
        re-read and the write. As with token persistence, a ciphertext change is
        not by itself a newer rotation — Fernet is non-deterministic, so a
        re-auth/import that re-encrypts the SAME plaintext changes the bytes
        without issuing a new token. When the fingerprint is still unchanged the
        account is holding the very material that just failed permanently, so we
        re-read and retry the CAS (bounded) against the freshly observed
        ciphertext rather than skipping the downgrade and leaving the account
        active with dead credentials. Only a genuinely different fingerprint is
        adopted as a peer rotation.
        """
        latest = await self._repo.get_by_id_fresh(account.id)
        if latest is None:
            # Account row is gone; nothing to downgrade.
            return None
        if (
            _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
            != attempted_fingerprint
        ):
            return _adopt_account_row(account, latest)
        reason = PERMANENT_FAILURE_CODES.get(exc.code, exc.message)
        status = account_status_for_permanent_failure(exc.code)
        for _ in range(_TOKEN_CAS_MAX_ATTEMPTS):
            applied = await self._repo.update_status_if_current(
                account.id,
                status,
                reason,
                expected_status=latest.status,
                expected_deactivation_reason=latest.deactivation_reason,
                expected_reset_at=latest.reset_at,
                expected_refresh_token_encrypted=latest.refresh_token_encrypted,
            )
            if applied:
                account.status = status
                account.deactivation_reason = reason
                mark_account_routing_unavailable(account.id)
                get_account_selection_cache().invalidate()
                return None
            # CAS missed: the freshly observed account state changed between the
            # re-read and the write. Re-read to decide why.
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is None:
                return None
            if (
                _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
                != attempted_fingerprint
            ):
                # A concurrent re-auth/import committed a genuinely different
                # refresh token in the CAS window. The account is repaired;
                # adopt the freshly rotated row (mirroring the pre-CAS check
                # above) instead of re-raising. Returning ``None`` here would
                # make ``_perform_refresh`` re-raise the original permanent
                # ``RefreshError``, and proxy callers then commonly invoke
                # ``LoadBalancer.mark_permanent_failure()`` whose ``update_status``
                # path is NOT guarded by this refresh-token CAS — so it would
                # clobber the peer's valid rotation with ``REAUTH_REQUIRED`` and
                # tear down sessions for an account that was just repaired.
                return _adopt_account_row(account, latest)
            # Same refresh-token plaintext, merely re-encrypted (non-deterministic
            # Fernet) — or an unrelated status/reason/reset nudge. The account is
            # still holding the material that just failed permanently, so retry
            # the CAS against the freshly observed ciphertext so the downgrade
            # lands rather than leaving a dead account active.
        logger.warning(
            "Permanent refresh-failure status CAS for account_id=%s code=%s kept missing on "
            "unchanged token material after %d attempts; leaving status unchanged",
            account.id,
            exc.code,
            _TOKEN_CAS_MAX_ATTEMPTS,
        )
        return None

    async def _refresh_tokens(self, refresh_token: str, *, account: Account) -> TokenRefreshResult:
        refresh_lease: RefreshAdmissionLeasePort | None = None
        if self._acquire_refresh_admission is not None:
            refresh_lease = await self._acquire_refresh_admission()
        try:
            async with get_background_session() as session:
                try:
                    route = await resolve_upstream_route(
                        session,
                        account_id=account.id,
                        operation="token_refresh",
                        scope="account",
                        encryptor=self._encryptor,
                    )
                except UpstreamProxyRouteError as exc:
                    raise RefreshError(
                        "upstream_proxy_unavailable",
                        f"Upstream proxy route unavailable: {exc.reason}",
                        False,
                        transport_error=True,
                        upstream_proxy_fail_closed_reason=exc.reason,
                    ) from exc
                if route is None and await _account_has_active_proxy_binding(session, account.id):
                    raise RefreshError(
                        "upstream_proxy_unavailable",
                        "Account has an active proxy binding but no route resolved",
                        False,
                        transport_error=True,
                        upstream_proxy_fail_closed_reason="binding_route_unavailable",
                    )
            return await _call_with_supported_optional_kwargs(
                refresh_access_token,
                refresh_token,
                optional_kwargs={
                    "route": route,
                    "allow_direct_egress": route is None,
                },
            )
        finally:
            if refresh_lease is not None:
                refresh_lease.release()

    async def _ensure_chatgpt_account_id(self, account: Account) -> Account:
        if account.chatgpt_account_id:
            return account
        try:
            id_token = self._encryptor.decrypt(account.id_token_encrypted)
        except Exception:
            return account
        raw_account_id = _chatgpt_account_id_from_id_token(id_token)
        if not raw_account_id:
            return account

        account.chatgpt_account_id = raw_account_id
        try:
            await self._repo.update_tokens(
                account.id,
                access_token_encrypted=account.access_token_encrypted,
                refresh_token_encrypted=account.refresh_token_encrypted,
                id_token_encrypted=account.id_token_encrypted,
                last_refresh=account.last_refresh,
                plan_type=account.plan_type,
                email=account.email,
                chatgpt_account_id=raw_account_id,
                workspace_id=account.workspace_id,
                workspace_label=account.workspace_label,
                seat_type=account.seat_type,
            )
        except Exception:
            logger.warning("Failed to persist chatgpt_account_id account_id=%s", account.id, exc_info=True)
        return account


def _chatgpt_account_id_from_id_token(id_token: str) -> str | None:
    claims = extract_id_token_claims(id_token)
    auth_claims = claims.auth or OpenAIAuthClaims()
    return auth_claims.chatgpt_account_id or claims.chatgpt_account_id


def _refresh_singleflight_key(
    encryptor: TokenEncryptor,
    account: Account,
) -> _RefreshSingleflightKey:
    return (
        account.id,
        _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted),
    )


def _adopt_account_row(target: Account, source: Account) -> Account:
    """Copy a concurrently committed row's state onto the caller's account object.

    ``source`` is attached to the refresh task's short-lived session; returning
    it directly would hand callers an object that expires when that session
    closes. Copying onto the caller's object mirrors how a successful refresh
    reports its result.
    """
    if target is source:
        return target
    for column in Account.__table__.columns:
        if column.name in ("id", "created_at"):
            continue
        setattr(target, column.name, getattr(source, column.name))
    return target


def _refresh_token_material_fingerprint(encryptor: TokenEncryptor, refresh_token_encrypted: bytes) -> str:
    try:
        material = encryptor.decrypt(refresh_token_encrypted).encode("utf-8")
    except Exception:
        material = refresh_token_encrypted
    return sha256(material).hexdigest()


def _clean_optional(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


async def _call_with_supported_optional_kwargs(
    func: Callable[..., Awaitable[Any]],
    /,
    *args: Any,
    optional_kwargs: Mapping[str, Any],
    **required_kwargs: Any,
) -> Any:
    kwargs = dict(required_kwargs)
    kwargs.update(optional_kwargs)
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None
    accepts_var_keyword = signature is not None and any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )
    if signature is not None and not accepts_var_keyword:
        for name in optional_kwargs:
            if name not in signature.parameters:
                kwargs.pop(name, None)
    return await func(*args, **kwargs)


def _clear_refresh_singleflight_state() -> None:
    _REFRESH_SINGLEFLIGHT.clear()


async def _account_has_active_proxy_binding(session: AsyncSession, account_id: str) -> bool:
    try:
        result = await session.execute(
            select(AccountProxyBinding.id)
            .where(
                AccountProxyBinding.account_id == account_id,
                AccountProxyBinding.is_active.is_(True),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None
    except OperationalError:
        return False
