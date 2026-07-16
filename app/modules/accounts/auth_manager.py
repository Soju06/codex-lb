from __future__ import annotations

import asyncio
import contextvars
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
    pop_token_refresh_timeout_override,
    push_token_refresh_timeout_override,
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

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool: ...

    async def update_account_metadata(
        self,
        account_id: str,
        *,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        last_refresh: datetime | None = None,
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

# Dedicated bounded retry budget for the FINAL safety persist of a freshly
# rotated token, DELIBERATELY SEPARATE from the claim/caller deadline. Persisting
# a valid single-use rotated token is worth a few extra milliseconds over budget:
# giving up here would strand the account holding the already-consumed token, and
# a later blind retry would re-exchange that consumed token into an
# ``invalid_grant``/reauth knockout of an otherwise healthy account. Each attempt
# is still a ciphertext-guarded compare-and-set against the freshly re-read
# ciphertext (a genuinely different peer plaintext is ADOPTED, never clobbered);
# because any ciphertext change means a writer committed and no realistic writer
# re-encrypts the SAME consumed token in a tight loop, this lands within a couple
# of attempts in practice. Kept small (with tiny backoff) so a truly pathological
# storm cannot live-lock.
_FINAL_PERSIST_MAX_ATTEMPTS = 3
_FINAL_PERSIST_RETRY_BASE_SECONDS = 0.02

# Bound on how many times releasing a refresh claim is retried when the release
# DELETE hits a transient DB error (SQLite lock past the busy timeout, a dropped
# Postgres connection). Release runs in ``finally`` after the token update has
# already committed, so a release failure must never mask a successful refresh;
# after a couple of brief retries we log and swallow and let the claim expire by
# its TTL.
_CLAIM_RELEASE_MAX_ATTEMPTS = 3
_CLAIM_RELEASE_RETRY_BASE_SECONDS = 0.05

# Cross-replica refresh-claim wait/poll tuning (fixed; issue #1340 /
# PRINCIPLES.md P2). The wait caps how long a non-claimant polls for the claim
# winner's rotated tokens before giving up; the poll interval bounds
# claim-table read pressure while waiting. Note the claim TTL floor
# (``token_refresh_claim_ttl_seconds``) is derived from the admission wait and
# refresh timeouts, not from these values.
_TOKEN_REFRESH_CLAIM_WAIT_SECONDS = 8.0
_TOKEN_REFRESH_CLAIM_POLL_SECONDS = 0.25

# Terminal account statuses a PRIOR claim holder may have committed while
# leaving ``refresh_token_encrypted`` UNCHANGED: a permanent refresh failure
# (e.g. ``invalid_grant``) downgraded through ``_handle_permanent_refresh_failure``
# or the safe-terminal persist-conflict path (``_flag_persist_conflict_reauth``),
# both of which flag REAUTH_REQUIRED/DEACTIVATED without rotating the token. A
# waiter that wins the RELEASED claim and re-reads one of these on the SAME
# (unchanged) refresh material must NOT re-exchange the stored consumed/dead
# token; it must fail closed and surface the terminal state instead.
_TERMINAL_REFRESH_STATUSES = frozenset({AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED})


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
        # The wait for a foreign claim is bounded by the fixed cap AND the
        # caller's remaining refresh budget: the singleflight body is shielded
        # and outlives a cancelled caller, so without the budget cap a small
        # request budget with a held foreign claim would leave this task
        # polling for the full fixed wait (holding its repo session and
        # the inflight singleflight entry that later callers join).
        wait_seconds = _TOKEN_REFRESH_CLAIM_WAIT_SECONDS
        caller_budget = get_token_refresh_timeout_override()
        start = time.monotonic()
        # Absolute deadline of the caller's ORIGINAL refresh budget (if any).
        # Used to cap the post-wait upstream exchange so a long claim wait
        # cannot be followed by a full-budget OAuth exchange that overruns the
        # request deadline.
        caller_deadline: float | None = None
        if caller_budget is not None:
            caller_budget = max(0.0, caller_budget)
            caller_deadline = start + caller_budget
            wait_seconds = min(wait_seconds, caller_budget)
        deadline = start + wait_seconds
        poll_seconds = _TOKEN_REFRESH_CLAIM_POLL_SECONDS
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
                # Monotonic deadline covering the ENTIRE claim hold from this
                # point: the exchange AND the post-exchange DB persist/status-CAS
                # loops all run while this claim is held. The exchange itself is
                # already budget-bounded, but the persist/status write loops were
                # bounded only by attempt count -- a contended DB write could keep
                # them looping past the claim TTL, after which a peer can win the
                # claim and re-exchange the (now consumed) single-use token. Bound
                # the persist section by the smaller of the claim TTL and the
                # caller's remaining budget so total claim-hold stays within
                # budget + a small fixed release, and a persist that runs past the
                # deadline stops (releasing the claim) instead of looping.
                claim_ttl = max(0.0, float(settings.token_refresh_claim_ttl_seconds))
                persist_deadline = time.monotonic() + claim_ttl
                if caller_deadline is not None:
                    persist_deadline = min(persist_deadline, caller_deadline)
                try:
                    # Post-claim fresh re-read: another replica may have rotated
                    # the material between the caller's read and our claim.
                    latest = await self._repo.get_by_id_fresh(account.id)
                    if latest is not None:
                        if (
                            _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
                            != requested_fingerprint
                        ):
                            # A peer genuinely rotated/repaired the material; adopt
                            # it and proceed without an upstream call.
                            return _adopt_account_row(account, latest)
                        if latest.status in _TERMINAL_REFRESH_STATUSES:
                            # A PRIOR claim holder finished by committing a terminal
                            # status (a permanent ``invalid_grant`` downgrade, or the
                            # safe-terminal persist-conflict path) WITHOUT rotating
                            # ``refresh_token_encrypted``. The fresh row therefore
                            # still holds the SAME consumed/dead token: re-exchanging
                            # it would just repeat a permanent refresh failure and
                            # defeat the fail-closed behavior the terminal flag exists
                            # to enforce. Adopt the terminal row (so the caller's
                            # object mirrors the committed state) and surface it as a
                            # PERMANENT failure instead of starting another upstream
                            # exchange. A GENUINE peer rotation is handled by the
                            # fingerprint branch above, so a repaired account never
                            # reaches here.
                            _adopt_account_row(account, latest)
                            raise _terminal_status_refresh_error(account)
                    fresh_material = (
                        latest.refresh_token_encrypted if latest is not None else account.refresh_token_encrypted
                    )
                    # The claim wait may have consumed most/all of the caller's
                    # refresh budget. This singleflight body is shielded from
                    # caller cancellation, so proceeding with the ORIGINAL
                    # ``token_refresh_timeout_override`` still active would let the
                    # OAuth exchange spend a whole budget AGAIN — overrunning the
                    # request deadline while pinning this repo session and the
                    # inflight singleflight entry that later callers join.
                    # Recompute the remaining budget: fail fast with the transient
                    # claim timeout when nothing is left, otherwise cap the
                    # exchange to what remains of the caller's deadline.
                    if caller_deadline is not None:
                        remaining_budget = caller_deadline - time.monotonic()
                        if remaining_budget <= 0:
                            raise RefreshError(
                                "refresh_claim_timeout",
                                f"Token refresh for account {account.id} exhausted its "
                                f"{caller_budget:.3f}s budget waiting for a peer replica's refresh "
                                f"claim before the upstream exchange could start",
                                False,
                                transport_error=True,
                            )
                        override_token = push_token_refresh_timeout_override(remaining_budget)
                        try:
                            return await self._perform_refresh(
                                account,
                                refresh_token_encrypted=fresh_material,
                                deadline=persist_deadline,
                            )
                        finally:
                            pop_token_refresh_timeout_override(override_token)
                    return await self._perform_refresh(
                        account,
                        refresh_token_encrypted=fresh_material,
                        deadline=persist_deadline,
                    )
                finally:
                    await self._release_claim_quietly(claims, account.id, owner=requested_fingerprint)
            # Claim held by another replica: adopt its rotation as soon as it
            # commits; never write account status from the losing side.
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is not None and (
                _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
                != requested_fingerprint
            ):
                return _adopt_account_row(account, latest)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RefreshError(
                    "refresh_claim_timeout",
                    f"Token refresh for account {account.id} is claimed by another replica; "
                    f"timed out waiting {wait_seconds:.3f}s for its rotation",
                    False,
                    transport_error=True,
                )
            # Cap the per-iteration sleep to the remaining claim-wait budget.
            # The configured poll interval may exceed what is left of the
            # caller's deadline; sleeping the full interval would let this
            # shielded task overrun the caller budget while still holding its
            # repo session and the inflight singleflight entry that later
            # callers join. Sleep the smaller of the poll interval and the time
            # remaining so the claim wait is truly bounded by the caller budget.
            await asyncio.sleep(min(poll_seconds, remaining))

    async def _release_claim_quietly(
        self,
        claims: RefreshClaimCoordinatorPort,
        account_id: str,
        *,
        owner: str,
    ) -> None:
        """Release the refresh claim without ever masking the refresh result.

        The release runs in ``finally`` after the token update has already
        committed, so a transient DB error here (a SQLite lock past the busy
        timeout, a dropped Postgres connection) MUST NOT replace a successful
        ``_perform_refresh``/adoption return value with a failure and leave the
        claim blocking peers until its TTL: the committed rotation is already
        durable and the claim harmlessly expires on its own. So retry a couple
        of times for a transient hiccup, then log and swallow.

        This suppresses only the release DELETE's own errors. An exception
        raised by the refresh body still propagates: when the body raised, this
        coroutine runs in the ``finally``, returns normally after swallowing any
        release error, and the original body exception continues to unwind.
        """
        last_exc: Exception | None = None
        for attempt in range(_CLAIM_RELEASE_MAX_ATTEMPTS):
            try:
                await claims.release(account_id, owner=owner)
                return
            except Exception as exc:  # noqa: BLE001 - release must never mask the refresh result
                last_exc = exc
                if attempt < _CLAIM_RELEASE_MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_CLAIM_RELEASE_RETRY_BASE_SECONDS * (2**attempt))
        logger.warning(
            "Failed to release refresh claim for account_id=%s after %d attempts; leaving it to "
            "expire by TTL (the committed refresh result is unaffected)",
            account_id,
            _CLAIM_RELEASE_MAX_ATTEMPTS,
            exc_info=last_exc,
        )

    async def _perform_refresh(
        self,
        account: Account,
        *,
        refresh_token_encrypted: bytes,
        deadline: float | None = None,
    ) -> Account:
        attempted_fingerprint = _refresh_token_material_fingerprint(self._encryptor, refresh_token_encrypted)
        refresh_token = self._encryptor.decrypt(refresh_token_encrypted)
        try:
            result = await self._refresh_tokens(refresh_token, account=account)
        except RefreshError as exc:
            if exc.is_permanent:
                adopted = await self._handle_permanent_refresh_failure(
                    account, exc, attempted_fingerprint, deadline=deadline
                )
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

        async def _write_tokens(expected_refresh_token_encrypted: bytes) -> bool:
            return await self._repo.rotate_tokens(
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
            deadline=deadline,
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
        write: Callable[[bytes], Awaitable[bool]],
        expected_refresh_token_encrypted: bytes,
        deadline: float | None = None,
    ) -> Account | None:
        """Persist freshly rotated tokens through a *guarded* compare-and-set only.

        Returns the latest account row to adopt when a peer committed a
        genuinely newer refresh-token rotation (different material) — that write
        must never be clobbered. Returns ``None`` when our own rotation was
        persisted (the caller then mirrors the new tokens onto its object), or
        when the row vanished.

        INVARIANT: there is NO unconditional token write anywhere in this
        helper. Every persist is a compare-and-set conditioned on the exact
        refresh-token ciphertext observed in the immediately-preceding read
        (``WHERE refresh_token_encrypted == :expected``). That comparison is
        atomic in the database, so there is no read->write gap: if ANYTHING
        changed the row after our read — a non-deterministic re-encryption of the
        same plaintext OR a genuine peer rotation — the guarded write MISSES and
        clobbers nothing.

        This structurally resolves the long-oscillating tension between "don't
        drop the freshly-rotated token" and "don't clobber a genuine peer
        rotation". The old exhaustion tail did an unconditional ``write(None)``
        after a final plaintext-confirming re-read, which left a TOCTOU gap: a
        peer rotation landing between that read and the unconditional write was
        clobbered with a token minted from the already-consumed material.
        Removing the unconditional write closes BOTH horns at once:

        * (A) don't drop the freshly rotated token — when the stored plaintext is
          confirmed to be the same token we consumed (only re-encrypted), we do
          NOT give up: we retry the ciphertext-guarded CAS against the freshly
          observed ciphertext so our new token still lands.
        * (B) don't clobber a peer rotation — because every write is guarded, a
          genuine rotation that lands in the former read->write gap now simply
          causes a MISS, and we re-read, see the different plaintext, and ADOPT
          it instead of overwriting.

        On a guarded-write miss we re-read and decrypt the stored plaintext to
        decide (never on the non-deterministic ciphertext):

        * genuinely DIFFERENT valid plaintext -> a peer rotated -> ADOPT it
          (return the peer row), never overwrite;
        * SAME plaintext as the token we exchanged FROM (re-encryption noise of
          the still-consumed token) -> retry the ciphertext-guarded CAS against
          the newly observed ciphertext so our rotation wins;
        * plaintext cannot be decrypted/compared -> raise the transient
          ``token_persist_conflict`` (we cannot prove a retry is safe).

        When the bounded guarded retries are exhausted OR the claim/caller
        deadline cuts the retry loop mid-storm, we do NOT drop the freshly rotated
        token: we run a DEDICATED small bounded retry loop
        (``_FINAL_PERSIST_MAX_ATTEMPTS`` attempts with tiny backoff) that is
        DELIBERATELY SEPARATE from the claim/caller deadline. Persisting a valid
        single-use rotated token is worth a few extra milliseconds over budget,
        because giving up here strands the account holding the already-consumed
        token. Each final attempt is still a ciphertext-guarded compare-and-set
        keyed on the freshly re-read ciphertext, so it is safe — it lands only if
        nothing changed since that read (persisting our new token and evicting the
        already-consumed one) and clobbers nothing otherwise. On each miss we
        re-read and decide on the DECRYPTED plaintext: a genuinely different peer
        plaintext is ADOPTED (never overwritten, done); the same plaintext merely
        re-encrypted is RETRIED against the newly observed ciphertext. Because any
        ciphertext change means a writer committed, and no realistic writer
        re-encrypts the same consumed token in a tight loop, this lands within a
        couple of attempts in practice.

        SAFE TERMINAL OUTCOME. Only if the dedicated final retries are ALL
        exhausted while the stored material stays the already-consumed token (a
        truly pathological same-plaintext storm, or undecryptable stored material
        — neither occurs in real production) do we give up — but we FAIL CLOSED
        rather than surfacing ordinary transient contention. A bare transient
        ``token_persist_conflict`` here would release the claim and let a later
        blind retry re-exchange the still-stored consumed token, turning this
        persistence race into an ``invalid_grant``/reauth PERMANENT knockout of a
        healthy account. Instead we flag the account ``REAUTH_REQUIRED`` through
        the SAME ciphertext-guarded status path (see
        ``_flag_persist_conflict_reauth``): the dead stored token is explicitly
        surfaced to operators, a peer rotation that lands in the guard window is
        ADOPTED (never clobbered), and the account is never left silently holding
        a consumed token that a blind retry would permanently knock out.

        This is what ends the long "drop the rotated token vs clobber a peer
        rotation" oscillation, and closes the irreducible trilemma corner
        (never-clobber vs never-drop vs bounded-time) that only appears in a
        same-plaintext storm where a writer re-encrypts the SAME consumed token
        faster than our CAS lands, repeatedly: the dedicated retries make the
        persist land in all realistic cases, and the safe terminal flag makes the
        pathological corner recoverable instead of a knockout. Every write remains
        a guarded compare-and-set, so nothing is ever clobbered.
        """
        consumed_plaintext = _decrypt_refresh_token_plaintext(self._encryptor, expected_refresh_token_encrypted)
        expected = expected_refresh_token_encrypted
        deadline_elapsed = False
        for _attempt in range(_TOKEN_CAS_MAX_ATTEMPTS):
            if await write(expected):
                # Guarded write landed: the row still held exactly the ciphertext
                # we observed, so our freshly rotated token replaced it atomically
                # and nothing was clobbered.
                return None
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is None:
                # Row is gone; nothing to persist or adopt.
                return None
            latest_plaintext = _decrypt_refresh_token_plaintext(self._encryptor, latest.refresh_token_encrypted)
            if consumed_plaintext is None or latest_plaintext is None:
                # Cannot prove plaintext identity, so we cannot prove another
                # guarded retry (or an adoption) is safe. A guarded persist was
                # already ATTEMPTED just above (it missed), so surfacing a
                # transient (non-permanent) error here is still a last resort, not
                # a raise-in-place-of-persist. The caller retries once the
                # contention clears; ``transport_error`` keeps it out of the
                # permanent-failure cooldown cache.
                logger.warning(
                    "Token-refresh compare-and-set for account_id=%s missed and the stored "
                    "refresh-token plaintext could not be decrypted for comparison; surfacing a "
                    "transient error rather than risking a clobber",
                    account.id,
                )
                raise RefreshError(
                    "token_persist_conflict",
                    (
                        f"Token-refresh compare-and-set for account_id={account.id} could not persist "
                        f"rotated tokens (stored refresh-token plaintext was undecryptable)"
                    ),
                    False,
                    transport_error=True,
                )
            if latest_plaintext != consumed_plaintext:
                # A peer stored genuinely newer refresh-token material after our
                # read; the guarded write MISSED and clobbered nothing. Adopt it
                # rather than overwriting with the token we already consumed.
                return _adopt_account_row(account, latest)
            # Same refresh-token plaintext, re-encrypted concurrently: retry the
            # guarded CAS against the freshly observed ciphertext so our rotation
            # lands only while the consumed material is still stored. No
            # unconditional write is ever issued.
            expected = latest.refresh_token_encrypted
            # The RETRY LOOP -- not the final safety persist below -- is bounded by
            # the claim/caller deadline: a contended DB write must not keep the
            # loop (and the held claim) spinning past the budget, after which a
            # peer could win the claim and re-exchange the already-consumed token.
            # On deadline expiry we STOP retrying but still fall through to the
            # single final guarded persist so the rotated token is never dropped
            # unpersisted.
            if deadline is not None and time.monotonic() >= deadline:
                deadline_elapsed = True
                break

        # FINAL, always-guarded persist of the freshly rotated token, keyed on the
        # LAST-OBSERVED ciphertext. Reached when the bounded retries were exhausted
        # OR the deadline cut the retry loop. This runs a DEDICATED small bounded
        # retry loop that is DELIBERATELY SEPARATE from the claim/caller deadline:
        # persisting a valid single-use rotated token is worth a few extra
        # milliseconds over budget, because giving up here strands the account
        # holding the already-consumed token. Each attempt is a single
        # compare-and-set, so it lands only if nothing changed since the last read
        # (persisting our new token and evicting the consumed one) and clobbers
        # nothing otherwise; on each miss we re-read and decide on the decrypted
        # plaintext (ADOPT a genuine peer rotation, retry a same-plaintext
        # re-encryption against the newly observed ciphertext). Because any
        # ciphertext change means a writer committed and no realistic writer
        # re-encrypts the same consumed token in a tight loop, this lands within a
        # couple of attempts in practice.
        for final_attempt in range(_FINAL_PERSIST_MAX_ATTEMPTS):
            if await write(expected):
                return None
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is None:
                # Row is gone; nothing to persist or adopt.
                return None
            latest_plaintext = _decrypt_refresh_token_plaintext(self._encryptor, latest.refresh_token_encrypted)
            if _is_genuine_peer_rotation(consumed_plaintext, latest_plaintext):
                # A genuine peer rotation landed on the final re-read: ADOPT it (our
                # freshly rotated token is legitimately superseded), never overwrite.
                return _adopt_account_row(account, latest)
            if consumed_plaintext is None or latest_plaintext is None:
                # Cannot prove plaintext identity, so we cannot prove another
                # guarded retry is safe. Stop the dedicated retries and fall
                # through to the SAFE terminal outcome rather than spinning.
                break
            # Same refresh-token plaintext, re-encrypted concurrently: retry the
            # guarded CAS against the freshly observed ciphertext so our rotation
            # lands only while the consumed material is still stored.
            expected = latest.refresh_token_encrypted
            if final_attempt < _FINAL_PERSIST_MAX_ATTEMPTS - 1:
                await asyncio.sleep(_FINAL_PERSIST_RETRY_BASE_SECONDS * (2**final_attempt))

        # SAFE TERMINAL OUTCOME. The dedicated final retries were ALL exhausted
        # while the stored material stayed the already-consumed token (a truly
        # pathological same-plaintext storm, or undecryptable stored material).
        # We must NOT surface an ordinary transient ``token_persist_conflict``
        # here: that would release the claim and let a later blind retry re-read
        # and re-exchange the still-stored consumed single-use token, turning this
        # persistence race into an ``invalid_grant``/reauth PERMANENT knockout of
        # an otherwise-healthy account. Fail CLOSED instead by flagging the account
        # REAUTH_REQUIRED through the SAME ciphertext-guarded status path, so the
        # dead stored token is explicitly surfaced to operators (never silently
        # retried) and a peer rotation that lands in the guard window is still
        # ADOPTED rather than clobbered.
        return await self._flag_persist_conflict_reauth(
            account,
            expected_refresh_token_encrypted=expected,
            consumed_plaintext=consumed_plaintext,
            deadline_elapsed=deadline_elapsed,
        )

    async def _flag_persist_conflict_reauth(
        self,
        account: Account,
        *,
        expected_refresh_token_encrypted: bytes,
        consumed_plaintext: str | None,
        deadline_elapsed: bool,
    ) -> Account | None:
        """SAFE terminal outcome for a truly pathological final-persist storm.

        Reached only when the DEDICATED bounded final-persist retries were ALL
        exhausted while the database kept holding the already-consumed refresh
        token (a sustained same-plaintext re-encryption storm, or undecryptable
        stored material — neither occurs in real production, since token
        re-encryption is a one-time re-auth/import event, not a tight loop).

        Rather than surfacing a bare transient ``token_persist_conflict`` — which
        releases the claim and lets a later blind retry re-exchange the stored
        consumed token into an ``invalid_grant``/reauth PERMANENT knockout of a
        healthy account — this fails CLOSED by flagging the account
        ``REAUTH_REQUIRED`` through the SAME ciphertext-guarded status CAS keyed on
        the last-observed (consumed-token) ciphertext:

        * guarded CAS LANDS -> the account is EXPLICITLY flagged for re-auth (its
          durable state truly holds a dead token), so operators repair it instead
          of a blind retry silently knocking it out. Return the flagged row.
        * guarded CAS MISSES on a genuinely DIFFERENT peer plaintext -> a peer
          re-authenticated/rotated; the account is repaired, so ADOPT the peer row
          (never clobbered), do not flag reauth.
        * guarded CAS keeps missing on same-plaintext re-encryption through its
          own bounded budget -> only THEN surface the transient
          ``token_persist_conflict`` as the last resort (no worse than before, and
          astronomically unlikely). ``transport_error`` keeps it out of the
          permanent-failure cooldown cache.

        Every write is a guarded compare-and-set, so a genuine peer rotation is
        never overwritten in any branch.
        """
        status = AccountStatus.REAUTH_REQUIRED
        reason = "Refresh token persistence conflict; stored token is stale - re-login required"
        expected = expected_refresh_token_encrypted
        for _attempt in range(_FINAL_PERSIST_MAX_ATTEMPTS):
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is None:
                # Row is gone; nothing to flag or adopt.
                return None
            latest_plaintext = _decrypt_refresh_token_plaintext(self._encryptor, latest.refresh_token_encrypted)
            if _is_genuine_peer_rotation(consumed_plaintext, latest_plaintext):
                # A peer rotated genuinely newer material: the account is repaired.
                # ADOPT it; do NOT flag reauth on a healthy rotated row.
                return _adopt_account_row(account, latest)
            expected = latest.refresh_token_encrypted
            applied = await self._repo.update_status_if_current(
                account.id,
                status,
                reason,
                expected_status=latest.status,
                expected_deactivation_reason=latest.deactivation_reason,
                expected_reset_at=latest.reset_at,
                expected_refresh_token_encrypted=expected,
            )
            if applied:
                account.status = status
                account.deactivation_reason = reason
                mark_account_routing_unavailable(account.id)
                get_account_selection_cache().invalidate()
                logger.warning(
                    "Token-refresh compare-and-set for account_id=%s could not persist the freshly "
                    "rotated token after the dedicated final-persist retries (%s); flagged the account "
                    "REAUTH_REQUIRED via the guarded status path so it is explicitly repaired rather "
                    "than left holding a consumed token that a blind retry would permanently knock out",
                    account.id,
                    "claim/caller deadline elapsed" if deadline_elapsed else "same-plaintext storm exhausted",
                )
                return account
            # Guarded status CAS missed on unchanged material; re-read and retry.
        # Even the guarded reauth flag could not land on unchanged material through
        # its own bounded budget: surface the transient (non-permanent) conflict as
        # the last resort. ``transport_error`` keeps it out of the permanent-failure
        # cooldown cache and never risks a clobber.
        logger.warning(
            "Token-refresh compare-and-set for account_id=%s could not persist the freshly rotated "
            "token nor flag REAUTH_REQUIRED after the dedicated final-persist retries; surfacing a "
            "transient conflict so the caller retries the whole refresh rather than risking a clobber",
            account.id,
        )
        raise RefreshError(
            "token_persist_conflict",
            (
                f"Token-refresh compare-and-set for account_id={account.id} could not persist "
                f"rotated tokens nor flag REAUTH_REQUIRED after a final guarded attempt missed on "
                f"unchanged material"
            ),
            False,
            transport_error=True,
        )

    async def _handle_permanent_refresh_failure(
        self,
        account: Account,
        exc: RefreshError,
        attempted_fingerprint: str,
        *,
        deadline: float | None = None,
    ) -> Account | None:
        """Persist a permanent refresh failure without clobbering a concurrent rotation.

        Returns the latest account row when its refresh-token material rotated
        after this attempt began (the caller adopts it instead of raising);
        returns ``None`` when the permanent failure stands (the status CAS
        landed, or the row vanished). Raises a TRANSIENT ``RefreshError``
        (``transport_error=True``, non-permanent) when the status-downgrade CAS
        is EXHAUSTED by contention while the account still holds the failed
        material — we could not authoritatively persist ``REAUTH_REQUIRED``, so
        the caller must retry rather than fall back to the unguarded
        ``LoadBalancer.mark_permanent_failure()`` write that could clobber a
        peer rotation landing in the same window. The comparison uses the
        fingerprint of the material this attempt exchanged, captured before the
        fresh re-read, because ``get_by_id_fresh`` may refresh the caller's own
        identity-map object in place.

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
        for attempt in range(_TOKEN_CAS_MAX_ATTEMPTS):
            # The FIRST status CAS always runs so a genuine permanent failure is
            # persisted best-effort. The RETRIES are additionally bounded by the
            # claim/caller deadline (not just the attempt count): the status
            # write loop runs while the claim is held, and a contended DB write
            # must not keep looping -- and holding the claim -- past the budget.
            # On deadline expiry surface the transient status-downgrade conflict
            # so the claim is released and the caller retries rather than running
            # the unguarded permanent mark that could clobber a peer rotation.
            if attempt > 0 and deadline is not None and time.monotonic() >= deadline:
                logger.warning(
                    "Permanent refresh-failure status CAS for account_id=%s code=%s exceeded the "
                    "claim/caller deadline after %d attempt(s); surfacing a transient conflict and "
                    "releasing the claim rather than looping past the budget",
                    account.id,
                    exc.code,
                    attempt,
                )
                raise RefreshError(
                    "status_downgrade_conflict",
                    (
                        f"Permanent refresh-failure status downgrade for account_id={account.id} "
                        f"(code={exc.code}) exceeded the refresh claim/caller deadline before it "
                        f"could persist REAUTH_REQUIRED"
                    ),
                    False,
                    transport_error=True,
                ) from exc
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
        # The bounded status-downgrade compare-and-set never landed: the account
        # still holds the material that failed permanently, but a sustained
        # re-encryption storm kept changing the observed ciphertext so we could
        # not authoritatively persist REAUTH_REQUIRED. Returning ``None`` here
        # would make ``_perform_refresh`` re-raise the ORIGINAL permanent
        # ``RefreshError``; proxy callers then commonly invoke
        # ``LoadBalancer.mark_permanent_failure()`` whose ``update_status`` path
        # is NOT guarded by the refresh-token ciphertext CAS — so a genuine peer
        # re-auth/import rotation that lands after our final re-read but before
        # that fallback write would be clobbered with ``REAUTH_REQUIRED``,
        # exactly the repaired-account clobber the CAS guards exist to prevent.
        # Since we could not win an atomic CAS window, surface a transient
        # (non-permanent) error so the caller RETRIES the whole refresh once the
        # contention clears rather than running the unguarded permanent mark.
        # ``transport_error`` keeps it out of the permanent-failure cooldown
        # cache. A genuinely different peer rotation is still adopted above (that
        # stays a repair); a status CAS that SUCCEEDS above still stands as a
        # real permanent failure — only contention-driven exhaustion becomes
        # transient here.
        logger.warning(
            "Permanent refresh-failure status CAS for account_id=%s code=%s kept missing on "
            "unchanged token material after %d attempts; surfacing a transient error so the caller "
            "retries rather than running the unguarded permanent-failure mark that could clobber a "
            "concurrent peer rotation",
            account.id,
            exc.code,
            _TOKEN_CAS_MAX_ATTEMPTS,
        )
        raise RefreshError(
            "status_downgrade_conflict",
            (
                f"Permanent refresh-failure status downgrade for account_id={account.id} "
                f"(code={exc.code}) could not persist REAUTH_REQUIRED after "
                f"{_TOKEN_CAS_MAX_ATTEMPTS} attempts due to concurrent writes"
            ),
            False,
            transport_error=True,
        ) from exc

    async def _refresh_tokens(self, refresh_token: str, *, account: Account) -> TokenRefreshResult:
        # Bound the ENTIRE claim-holding work of this body — the token-refresh
        # admission wait AND the upstream OAuth exchange — by the caller's
        # remaining refresh budget. This runs inside the shielded singleflight
        # task that outlives a cancelled caller and holds the cross-replica DB
        # refresh claim, so any unbounded wait here keeps the claim held past the
        # request deadline and blocks peer replicas on the same account's claim.
        #
        # ``token_refresh_timeout_override`` (set by the claim path to the
        # remaining budget) only caps the HTTP exchange; without help,
        # ``WorkAdmissionController`` waits up to
        # ``proxy_admission_wait_timeout_seconds`` for a slot on a saturated
        # token-refresh semaphore BEFORE that HTTP timeout is even armed. Derive
        # one monotonic deadline from the budget and enforce it on BOTH the
        # admission acquire and the exchange so their sum cannot overrun it.
        budget = get_token_refresh_timeout_override()
        deadline = time.monotonic() + max(0.0, budget) if budget is not None else None

        refresh_lease = await self._acquire_refresh_admission_bounded(account, deadline)
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
            # Cap the exchange to what is actually LEFT after the admission wait
            # (and route resolution). The pushed override still carries the full
            # pre-admission remaining budget, so re-derive it here; otherwise a
            # long admission wait followed by a full-remaining-budget exchange
            # could hold the claim for up to twice the budget.
            exchange_override: contextvars.Token[float | None] | None = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RefreshError(
                        "refresh_claim_timeout",
                        f"Token refresh for account {account.id} exhausted its refresh budget after "
                        f"acquiring token-refresh admission but before the upstream exchange could start",
                        False,
                        transport_error=True,
                    )
                exchange_override = push_token_refresh_timeout_override(remaining)
            try:
                return await _call_with_supported_optional_kwargs(
                    refresh_access_token,
                    refresh_token,
                    optional_kwargs={
                        "route": route,
                        "allow_direct_egress": route is None,
                    },
                )
            finally:
                if exchange_override is not None:
                    pop_token_refresh_timeout_override(exchange_override)
        finally:
            if refresh_lease is not None:
                refresh_lease.release()

    async def _acquire_refresh_admission_bounded(
        self,
        account: Account,
        deadline: float | None,
    ) -> RefreshAdmissionLeasePort | None:
        """Acquire token-refresh admission without overrunning the caller budget.

        ``WorkAdmissionController`` waits up to
        ``proxy_admission_wait_timeout_seconds`` for a slot on a saturated
        token-refresh semaphore. That wait happens while this shielded task
        already holds the cross-replica DB refresh claim, so it MUST be capped by
        the caller's remaining refresh budget (``deadline``): otherwise a
        small-budget request would hold the claim — and block peer replicas on
        the same account — for the full admission timeout. When the budget is
        already exhausted before/at admission, fail fast with the transient
        ``refresh_claim_timeout`` so the claim is released (by the caller's
        ``finally``) rather than held for the full wait.
        """
        if self._acquire_refresh_admission is None:
            return None
        if deadline is None:
            return await self._acquire_refresh_admission()
        admission_wait = deadline - time.monotonic()
        if admission_wait <= 0:
            raise RefreshError(
                "refresh_claim_timeout",
                f"Token refresh for account {account.id} exhausted its refresh budget before "
                f"token-refresh admission could be acquired",
                False,
                transport_error=True,
            )
        try:
            return await asyncio.wait_for(self._acquire_refresh_admission(), timeout=admission_wait)
        except asyncio.TimeoutError as exc:
            raise RefreshError(
                "refresh_claim_timeout",
                f"Token refresh for account {account.id} exhausted its refresh budget waiting for "
                f"token-refresh admission on a saturated concurrency gate",
                False,
                transport_error=True,
            ) from exc

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
            # This backfill runs on EVERY ensure_fresh for a legacy account (the
            # fast, no-refresh path included) against the caller's selection-time
            # snapshot, OUTSIDE any refresh claim. It derives and persists only
            # the missing chatgpt_account_id, so it routes through the
            # metadata-only writer, which STRUCTURALLY cannot write token
            # ciphertext. That makes it impossible for this stale in-memory
            # snapshot to rewrite the refresh-token material and clobber a peer
            # replica's concurrent single-use rotation -- the exact lost-update
            # the refresh persist path was rebuilt to forbid, which previously
            # re-entered via this sibling.
            await self._repo.update_account_metadata(
                account.id,
                chatgpt_account_id=raw_account_id,
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


def _terminal_status_refresh_error(account: Account) -> RefreshError:
    """Build the PERMANENT ``RefreshError`` that surfaces a prior holder's terminal status.

    Reached only when a claim waiter wins the released claim and re-reads a row
    whose refresh material is UNCHANGED but whose status is already terminal
    (a prior holder committed REAUTH_REQUIRED/DEACTIVATED without rotating the
    token). The chosen code maps back to the SAME committed status through
    :func:`account_status_for_permanent_failure`, so a downstream
    ``LoadBalancer.mark_permanent_failure`` re-affirms the terminal state rather
    than flipping REAUTH_REQUIRED to DEACTIVATED (or vice versa).
    """
    code = "account_deactivated" if account.status == AccountStatus.DEACTIVATED else "refresh_token_invalidated"
    reason = account.deactivation_reason or PERMANENT_FAILURE_CODES.get(code, "re-login required")
    return RefreshError(
        code,
        (
            f"Token refresh for account {account.id} not attempted: a prior refresh-claim holder "
            f"already flagged the account {account.status.value} without rotating the refresh token "
            f"({reason}); reusing the unchanged consumed token would repeat a permanent failure"
        ),
        True,
    )


def _refresh_token_material_fingerprint(encryptor: TokenEncryptor, refresh_token_encrypted: bytes) -> str:
    try:
        material = encryptor.decrypt(refresh_token_encrypted).encode("utf-8")
    except Exception:
        material = refresh_token_encrypted
    return sha256(material).hexdigest()


def _decrypt_refresh_token_plaintext(encryptor: TokenEncryptor, refresh_token_encrypted: bytes) -> str | None:
    """Decrypt refresh-token ciphertext to its plaintext, or ``None`` if undecryptable.

    Unlike :func:`_refresh_token_material_fingerprint` (which silently falls back
    to hashing the raw ciphertext when decryption fails), this returns ``None`` so
    callers can distinguish a genuine plaintext comparison from an impossible one.
    """
    try:
        return encryptor.decrypt(refresh_token_encrypted)
    except Exception:
        return None


def _is_genuine_peer_rotation(consumed_plaintext: str | None, latest_plaintext: str | None) -> bool:
    """True when the freshly re-read plaintext is a genuinely DIFFERENT refresh
    token than the one this attempt exchanged FROM (a peer rotation to ADOPT),
    rather than the same consumed token merely re-encrypted or an undecryptable
    blob. Both plaintexts must decrypt for the comparison to be trustworthy."""
    return consumed_plaintext is not None and latest_plaintext is not None and latest_plaintext != consumed_plaintext


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
