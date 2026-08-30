from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import extract_id_token_claims, resolve_seat_identity
from app.core.crypto import TokenEncryptor
from app.core.upstream_proxy.cache import get_upstream_route_cache
from app.core.utils.time import utcnow
from app.db.account_identity_lock import advisory_lock_key, lock_postgresql_account_identities
from app.db.models import (
    Account,
    AccountLimitWarmup,
    AccountStatus,
    AccountUsageRollup,
    AdditionalUsageHistory,
    ApiKeyAccountAssignment,
    DashboardSettings,
    HttpBridgeSessionAlias,
    HttpBridgeSessionRecord,
    HttpBridgeSessionState,
    RequestLog,
    RuntimeSentinel,
    StickySession,
    StickySessionKind,
    UsageHistory,
)
from app.db.session import sqlite_writer_section
from app.modules.accounts.usage_rollup import (
    AccountUsageRollupRepository,
    deduped_usage_aggregate_stmt,
    lock_fold_state,
    merge_rollups_into,
)
from app.modules.accounts.usage_time_rollup import (
    merge_time_rollups_into,
    mirror_account_hard_delete_into_time_rollups,
    mirror_account_soft_delete_into_time_rollups,
)
from app.modules.usage.additional_quota_keys import normalize_additional_quota_routing_policy_overrides
from app.modules.usage.plan_downgrade_observations import discard_plan_downgrade_observations
from app.modules.usage.repository import _clear_bulk_history_since_sqlite_cache

_SETTINGS_ROW_ID = 1
_DUPLICATE_ACCOUNT_SUFFIX = "__copy"
# deactivation_reason stamped by the fast DELETE path while the background
# worker drains the account's rows. The authoritative pending marker is
# accounts.delete_requested_at; the reason string is operator-facing only.
ACCOUNT_PENDING_DELETION_REASON = "pending_deletion"


def credentials_replaced_since_wipe(
    access_token_encrypted: bytes,
    refresh_token_encrypted: bytes,
    id_token_encrypted: bytes,
) -> bool:
    """True when a marked account's token ciphertext is no longer the
    empty-credential wipe stamped by :meth:`AccountsRepository.begin_delete`.

    New-code credential replacements clear the pending-deletion marker in the
    same transaction, but a replacement handled by a PRE-UPGRADE replica
    during a rolling deploy writes fresh ciphertext without knowing the
    marker columns. Fresh (non-wiped) credentials on a still-marked row are
    therefore themselves the supersede signal; the caller must clear the
    marker and abandon the deletion. ALL THREE token fields are inspected: a
    legal replacement may carry an empty refresh token while providing fresh
    access/id material, and mistaking it for the wipe would finalize a
    freshly replaced account. Undecryptable material also counts as
    replaced — never finalize a row whose credentials we cannot attribute to
    our own wipe.
    """
    encryptor = TokenEncryptor()
    for ciphertext in (access_token_encrypted, refresh_token_encrypted, id_token_encrypted):
        try:
            if encryptor.decrypt(ciphertext) != "":
                return True
        except Exception:
            return True
    return False


_UNSET = object()
_HARD_STICKY_UNAVAILABLE_STATUSES = frozenset(
    (AccountStatus.PAUSED, AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED)
)
_HARD_STICKY_OUTAGE_GRACE_SEEDED_SENTINEL = "hard_sticky_outage_grace_seeded"


def _is_missing_hard_sticky_seed_table(exc: OperationalError) -> bool:
    """Return True when startup grace seeding hit a not-yet-migrated database.

    ``seed_hard_sticky_outage_grace_on_startup`` runs unguarded in the app
    lifespan. With ``database_migrate_on_startup=false`` (operator-managed
    migrations) the process can boot against a legacy database that does not
    have ``runtime_sentinels`` (or, on a brand-new file, ``accounts``) yet;
    before this guard that boot crashed with an unhandled sqlite
    ``OperationalError``. The seeding is a best-effort one-time backfill: on a
    missing table it rolls back and returns 0 without stamping the sentinel,
    so the first boot after migrations run still performs the backfill. Only
    the sqlite "no such table" shape is matched — this deployment mode boots
    sqlite before migration; every other OperationalError still propagates.
    """
    message = str(exc).lower()
    return "no such table: runtime_sentinels" in message or "no such table: accounts" in message


@dataclass(frozen=True, slots=True)
class AccountRequestUsageSummary:
    request_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float


# The account-listing request-usage summary dedupes and re-aggregates the
# un-folded raw tail on every dashboard accounts load, and the displayed
# lifetime totals tolerate short staleness. Cache the merged summaries per
# account-id signature for a small fixed TTL, mirroring the request-log
# COUNT cache (issue #1340 / PRINCIPLES.md P2); the test suite patches the
# TTL to 0 so summaries stay exact within a test. Account deletion and
# duplicate-identity consolidation clear the cache because they re-attribute
# usage rather than merely append to it.
_SUMMARY_CACHE_TTL_SECONDS = 30.0
_SUMMARY_CACHE_MAX_ENTRIES = 64
_request_usage_summary_cache: dict[tuple[str, ...] | None, tuple[dict[str, AccountRequestUsageSummary], float]] = {}
# Invalidation generation: a fill that was already computing when a clear
# happened must not re-populate the cache with its pre-clear result. Fills
# capture the generation before their first await and stores are discarded
# on mismatch. Deletion/consolidation clear synchronously right after their
# commit (no await in between), so every store either precedes the commit
# (its stale data is wiped by the clear) or observes the bumped generation.
_summary_cache_generation = 0


def _clear_request_usage_summary_cache() -> None:
    global _summary_cache_generation
    _summary_cache_generation += 1
    _request_usage_summary_cache.clear()


def _cached_request_usage_summaries(
    key: tuple[str, ...] | None,
) -> dict[str, AccountRequestUsageSummary] | None:
    entry = _request_usage_summary_cache.get(key)
    if entry is None:
        return None
    summaries, expires_at = entry
    if time.monotonic() >= expires_at:
        _request_usage_summary_cache.pop(key, None)
        return None
    return summaries


def _store_request_usage_summaries(
    key: tuple[str, ...] | None,
    summaries: dict[str, AccountRequestUsageSummary],
    ttl_seconds: float,
    generation: int,
) -> None:
    if generation != _summary_cache_generation:
        return
    if len(_request_usage_summary_cache) >= _SUMMARY_CACHE_MAX_ENTRIES:
        oldest = min(
            _request_usage_summary_cache,
            key=lambda existing: _request_usage_summary_cache[existing][1],
        )
        _request_usage_summary_cache.pop(oldest, None)
    _request_usage_summary_cache[key] = (summaries, time.monotonic() + ttl_seconds)


class AccountIdentityConflictError(Exception):
    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(
            f"Cannot overwrite account for email '{email}' because multiple matching accounts exist. "
            "Remove duplicates or enable import without overwrite."
        )


class AccountIdentityRelockError(RuntimeError):
    """Raised after identity membership changes across both bounded lock attempts."""


class AccountsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def get_by_id(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id)

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        """Re-read one account with a real SELECT, refreshing the identity map.

        Unlike ``get_by_id`` (``session.get``), this never returns a cached
        identity-map object without hitting the database, so callers checking
        for concurrently rotated token material observe the latest committed
        row.
        """
        result = await self._session.execute(
            select(Account).where(Account.id == account_id).execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
        # Accounts marked for background deletion are already deleted from the
        # operator's point of view: they never appear in listings (dashboard,
        # usage refresh, automations) even though their rows survive until the
        # deletion worker finishes draining them.
        stmt = select(Account).where(Account.delete_requested_at.is_(None)).order_by(Account.email)
        if refresh_existing:
            stmt = stmt.execution_options(populate_existing=True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_accounts_by_ids(self, account_ids: list[str], *, refresh_existing: bool = False) -> list[Account]:
        if not account_ids:
            return []
        stmt = (
            select(Account)
            .where(Account.id.in_(account_ids))
            .where(Account.delete_requested_at.is_(None))
            .order_by(Account.email)
        )
        if refresh_existing:
            stmt = stmt.execution_options(populate_existing=True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_request_usage_summary_by_account(
        self,
        account_ids: list[str] | None = None,
    ) -> dict[str, AccountRequestUsageSummary]:
        ttl_seconds = _SUMMARY_CACHE_TTL_SECONDS
        cache_key = tuple(sorted(account_ids)) if account_ids is not None else None
        generation = _summary_cache_generation
        if ttl_seconds > 0:
            cached = _cached_request_usage_summaries(cache_key)
            if cached is not None:
                return dict(cached)
        rollup_repo = AccountUsageRollupRepository(self._session)
        folded, watermark = await rollup_repo.read_state(account_ids)

        merged: dict[str, list[float]] = {
            account_id: [
                sums.request_count,
                sums.input_tokens,
                sums.output_tokens,
                sums.cached_input_tokens,
                sums.total_cost_usd,
            ]
            for account_id, sums in folded.items()
        }
        tail_stmt = deduped_usage_aggregate_stmt(account_ids=account_ids, after_exclusive=watermark)
        result = await self._session.execute(tail_stmt)
        for (
            account_id,
            request_count,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            total_cost_usd,
        ) in result.all():
            if not account_id:
                continue
            totals = merged.setdefault(account_id, [0, 0, 0, 0, 0.0])
            totals[0] += int(request_count or 0)
            totals[1] += int(input_tokens or 0)
            totals[2] += int(output_tokens or 0)
            totals[3] += int(cached_input_tokens or 0)
            totals[4] += float(total_cost_usd or 0.0)

        summaries: dict[str, AccountRequestUsageSummary] = {}
        for account_id, (request_count, input_sum, output_sum, cached_sum, total_cost_usd) in merged.items():
            input_total = int(input_sum)
            output_total = int(output_sum)
            cached_total = max(0, min(int(cached_sum), input_total))
            summaries[account_id] = AccountRequestUsageSummary(
                request_count=int(request_count),
                total_tokens=input_total + output_total,
                cached_input_tokens=cached_total,
                total_cost_usd=round(float(total_cost_usd), 6),
            )
        if ttl_seconds > 0:
            _store_request_usage_summaries(cache_key, summaries, ttl_seconds, generation)
            return dict(summaries)
        return summaries

    async def exists_active_chatgpt_account_id(self, chatgpt_account_id: str) -> bool:
        return await self.get_active_by_chatgpt_account_id(chatgpt_account_id) is not None

    async def get_active_by_chatgpt_account_id(self, chatgpt_account_id: str) -> Account | None:
        result = await self._session.execute(
            select(Account)
            .where(Account.chatgpt_account_id == chatgpt_account_id)
            .where(
                Account.status.notin_((AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED))
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        account: Account,
        *,
        merge_by_email: bool | None = None,
        merge_by_chatgpt_identity: bool = False,
    ) -> Account:
        async with sqlite_writer_section():
            return await self._upsert_unlocked(
                account,
                merge_by_email=merge_by_email,
                merge_by_chatgpt_identity=merge_by_chatgpt_identity,
            )

    async def _upsert_unlocked(
        self,
        account: Account,
        *,
        merge_by_email: bool | None = None,
        merge_by_chatgpt_identity: bool = False,
        _identity_lock_attempt: int = 0,
    ) -> Account:
        dialect_name = self._dialect_name()
        sqlite_lock_acquired = False
        if merge_by_email is None:
            if dialect_name == "sqlite":
                await self._acquire_sqlite_merge_lock()
                sqlite_lock_acquired = True
            merge_by_email = await self._merge_by_email_enabled()

        if dialect_name == "sqlite" and not sqlite_lock_acquired:
            # sqlite BEGIN IMMEDIATE serializes all writers globally, so
            # the identity-reconciliation branch is already mutually
            # exclusive on this dialect.
            await self._acquire_sqlite_merge_lock()
        elif dialect_name == "postgresql":
            # Upstream identity membership always serializes before email
            # locks and account row locks. This applies to ordinary imports as
            # well as explicit identity reconciliation because either can add,
            # replace, or remove a membership used by live-usage fallback.
            locked_identities = await self._lock_postgresql_upsert_identity_candidates(
                account,
                include_email=bool(merge_by_email),
            )
            if merge_by_email:
                await self._acquire_postgresql_merge_lock(account.email)
            elif not locked_identities:
                await self._acquire_postgresql_identity_lock(account.id)
            if not await self._postgresql_upsert_identity_candidates_are_locked(
                account,
                include_email=bool(merge_by_email),
                locked_identities=locked_identities,
            ):
                await self._session.rollback()
                if _identity_lock_attempt >= 1:
                    raise AccountIdentityRelockError(
                        "Account identity candidates changed during PostgreSQL upsert locking"
                    )
                return await self._upsert_unlocked(
                    account,
                    merge_by_email=merge_by_email,
                    merge_by_chatgpt_identity=merge_by_chatgpt_identity,
                    _identity_lock_attempt=_identity_lock_attempt + 1,
                )

        # Identity-aware reconciliation runs before the deterministic-id
        # check so that a deactivated row whose refresh token was revoked
        # is reused on reauth instead of being shadowed by an __copy row
        # under the same upstream ChatGPT identity (issue #788).
        #
        # This path is intentionally independent of merge_by_email: the
        # OAuth reauth caller passes merge_by_chatgpt_identity=True even
        # when the operator has opted into "import without overwrite",
        # because that setting governs the dashboard import path (side-
        # by-side rows for the same email) rather than the reauth path
        # (one local row per upstream identity).
        if merge_by_chatgpt_identity and account.chatgpt_account_id:
            canonical = await self._account_by_chatgpt_identity(
                account.chatgpt_account_id,
                workspace_id=account.workspace_id,
                email=account.email,
            )
            if canonical is not None:
                await self._apply_account_replacement(canonical, account)
                usage_cache_dirty = await self._reconcile_chatgpt_identity_duplicates(
                    canonical=canonical,
                    chatgpt_account_id=account.chatgpt_account_id,
                    workspace_id=account.workspace_id,
                    email=account.email,
                )
                await self._session.commit()
                if usage_cache_dirty:
                    _clear_bulk_history_since_sqlite_cache()
                    # Consolidation re-attributes request logs and rollup sums
                    # to the canonical account; cached listing summaries would
                    # keep reporting the deleted duplicates until TTL expiry.
                    _clear_request_usage_summary_cache()
                    # Duplicate reconciliation deletes Account rows, cascading
                    # any account_proxy_bindings they owned. The route cache is
                    # keyed by deterministic account id, so stale duplicate-id
                    # outcomes must not survive the commit.
                    await get_upstream_route_cache().invalidate()
                await self._session.refresh(canonical)
                return canonical

        existing = await self._session.get(Account, account.id)
        if existing:
            if merge_by_email or _is_workspace_less_reauth_for_known_slot(
                existing,
                account,
                merge_by_chatgpt_identity=merge_by_chatgpt_identity,
            ):
                await self._apply_account_replacement(existing, account)
                await self._session.commit()
                await self._session.refresh(existing)
                return existing
            account.id = await self._next_available_account_id(account.id)

        if merge_by_email:
            existing_by_email = await self._single_account_by_email(account.email)
            if existing_by_email:
                await self._apply_account_replacement(existing_by_email, account)
                await self._session.commit()
                await self._session.refresh(existing_by_email)
                return existing_by_email

        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def upsert_reauthorized(self, account: Account) -> Account:
        return await self.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)

    async def replace_reauthorized(self, account_id: str, account: Account) -> Account | None:
        """Replace credentials on the exact local row selected for reauthentication."""
        async with sqlite_writer_section():
            if self._dialect_name() == "postgresql":
                existing = await self._lock_postgresql_account_identity_membership(
                    account_id,
                    account.chatgpt_account_id,
                )
            else:
                existing = await self._session.get(Account, account_id)
            if existing is None:
                return None
            await self._apply_account_replacement(existing, account)
            await self._session.commit()
            await self._session.refresh(existing)
            return existing

    async def _apply_account_replacement(self, target: Account, source: Account) -> None:
        """Apply freshly imported or reauthorized material onto an existing row.

        Every in-place credential replacement goes through here rather than
        calling :func:`_apply_account_updates` directly, so replacing a
        credential always discards the account's pending plan-downgrade
        evidence in the same transaction: evidence gathered under the previous
        credential must not count toward a downgrade for the new one (#1456).
        Routine token rotation is a different event with its own path
        (:meth:`rotate_tokens`) and deliberately does not discard evidence.
        """
        _apply_account_updates(target, source)
        await discard_plan_downgrade_observations(self._session, target.id)

    async def upsert_account_slot(
        self,
        account: Account,
        *,
        preserve_unknown_workspace_duplicates: bool | None = None,
        preserve_identity_slots: bool = False,
    ) -> Account:
        async with sqlite_writer_section():
            return await self._upsert_account_slot_unlocked(
                account,
                preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates,
                preserve_identity_slots=preserve_identity_slots,
            )

    async def _upsert_account_slot_unlocked(
        self,
        account: Account,
        *,
        preserve_unknown_workspace_duplicates: bool | None = None,
        preserve_identity_slots: bool = False,
        _identity_lock_attempt: int = 0,
    ) -> Account:
        if preserve_unknown_workspace_duplicates is None:
            preserve_unknown_workspace_duplicates = not await self._merge_by_email_enabled()
        dialect_name = self._dialect_name()
        if dialect_name == "sqlite":
            await self._acquire_sqlite_merge_lock()
        elif dialect_name == "postgresql":
            locked_identities = await self._lock_postgresql_upsert_identity_candidates(
                account,
                include_email=True,
            )
            for lock_key in sorted(
                _slot_lock_keys(
                    account,
                    preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates,
                )
            ):
                await self._acquire_postgresql_identity_lock(lock_key)
            if not await self._postgresql_upsert_identity_candidates_are_locked(
                account,
                include_email=True,
                locked_identities=locked_identities,
            ):
                await self._session.rollback()
                if _identity_lock_attempt >= 1:
                    raise AccountIdentityRelockError(
                        "Account identity candidates changed during PostgreSQL slot locking"
                    )
                return await self._upsert_account_slot_unlocked(
                    account,
                    preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates,
                    preserve_identity_slots=preserve_identity_slots,
                    _identity_lock_attempt=_identity_lock_attempt + 1,
                )

        existing = await self._account_by_slot_identity(account)
        if existing:
            await self._apply_account_replacement(existing, account)
            await self._session.commit()
            await self._session.refresh(existing)
            return existing

        existing_by_id = await self._session.get(Account, account.id)
        if existing_by_id:
            if _same_unknown_workspace_identity(existing_by_id, account) and not preserve_unknown_workspace_duplicates:
                await self._apply_account_replacement(existing_by_id, account)
                await self._session.commit()
                await self._session.refresh(existing_by_id)
                return existing_by_id
            account.id = await self._next_available_account_id(account.id)
        elif not preserve_unknown_workspace_duplicates:
            if _workspace_slot_key(account):
                existing_by_email = await self._single_unknown_workspace_account_by_email(account.email)
            elif preserve_identity_slots and account.chatgpt_account_id:
                existing_by_email = None
            else:
                existing_by_email = await self._single_account_by_email(account.email)
            if existing_by_email and not _can_reuse_email_fallback(existing_by_email, account):
                existing_by_email = None
            if existing_by_email:
                await self._apply_account_replacement(existing_by_email, account)
                await self._session.commit()
                await self._session.refresh(existing_by_email)
                return existing_by_email

        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def _account_by_chatgpt_identity(
        self,
        chatgpt_account_id: str,
        *,
        workspace_id: str | None,
        email: str | None,
    ) -> Account | None:
        """Return the canonical local account row for a ChatGPT identity.

        Order of preference, so that reauth targets the matching real-email
        slot when one exists, while still allowing an upstream email change
        to reuse a single unambiguous identity row:

        1. The oldest row with the incoming email.
        2. The only identity row when no email match exists.
        """

        stmt = select(Account).where(Account.chatgpt_account_id == chatgpt_account_id)
        order_by: list[Any] = [Account.created_at.asc(), Account.id.asc()]
        if workspace_id:
            stmt = stmt.where(or_(Account.workspace_id == workspace_id, Account.workspace_id.is_(None)))
            order_by.insert(0, Account.workspace_id.is_(None).asc())
        else:
            stmt = stmt.where(Account.workspace_id.is_(None))

        result = await self._session.execute(stmt.order_by(*order_by))
        candidates = list(result.scalars().all())
        if not candidates:
            return None
        if not email:
            return candidates[0]

        for candidate in candidates:
            if candidate.email == email:
                return candidate

        if len(candidates) == 1:
            return candidates[0]
        return None

    async def _reconcile_chatgpt_identity_duplicates(
        self,
        canonical: Account,
        chatgpt_account_id: str,
        workspace_id: str | None,
        email: str | None,
    ) -> bool:
        duplicate_stmt = select(Account.id).where(
            Account.chatgpt_account_id == chatgpt_account_id,
            Account.id != canonical.id,
        )
        if email:
            duplicate_stmt = duplicate_stmt.where(Account.email == email)
        if workspace_id is None:
            duplicate_stmt = duplicate_stmt.where(Account.workspace_id.is_(None))
        else:
            duplicate_stmt = duplicate_stmt.where(Account.workspace_id == workspace_id)
        duplicate_accounts = (await self._session.execute(duplicate_stmt)).scalars().all()
        duplicate_ids = list(duplicate_accounts)
        if not duplicate_ids:
            return False

        # Serialize against fold passes before reassigning any request logs:
        # a fold overlapping this transaction could otherwise attribute the
        # duplicates' logs to rollup rows this transaction deletes, leaving
        # those logs behind the watermark but counted in no rollup.
        await lock_fold_state(self._session)

        duplicate_api_key_ids = (
            (
                await self._session.execute(
                    select(ApiKeyAccountAssignment.api_key_id).where(ApiKeyAccountAssignment.account_id == canonical.id)
                )
            )
            .scalars()
            .all()
        )
        existing_api_key_ids = set(duplicate_api_key_ids)

        duplicate_assignments = (
            (
                await self._session.execute(
                    select(ApiKeyAccountAssignment).where(ApiKeyAccountAssignment.account_id.in_(duplicate_ids))
                )
            )
            .scalars()
            .all()
        )
        for assignment in duplicate_assignments:
            if assignment.api_key_id in existing_api_key_ids:
                await self._session.delete(assignment)
            else:
                assignment.account_id = canonical.id
                existing_api_key_ids.add(assignment.api_key_id)

        await self._session.execute(
            update(UsageHistory).where(UsageHistory.account_id.in_(duplicate_ids)).values(account_id=canonical.id)
        )
        await self._session.execute(
            update(AdditionalUsageHistory)
            .where(AdditionalUsageHistory.account_id.in_(duplicate_ids))
            .values(account_id=canonical.id)
        )
        await self._session.execute(
            update(RequestLog).where(RequestLog.account_id.in_(duplicate_ids)).values(account_id=canonical.id)
        )
        await self._reconcile_limit_warmups(canonical.id, duplicate_ids)
        await self._session.execute(
            update(StickySession).where(StickySession.account_id.in_(duplicate_ids)).values(account_id=canonical.id)
        )
        await self._session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.account_id.in_(duplicate_ids))
            .values(account_id=canonical.id)
        )
        # Folded usage must follow the reassigned request logs, or the
        # canonical account silently loses the duplicates' pre-watermark
        # history from its lifetime totals.
        await merge_rollups_into(self._session, canonical.id, duplicate_ids)
        # Folded time-axis buckets follow the reassigned request logs too:
        # bucket-wise merge-add onto the canonical account, duplicates
        # removed, in this same fold-state-locked transaction.
        await merge_time_rollups_into(self._session, canonical.id, duplicate_ids)
        await self._session.execute(delete(Account).where(Account.id.in_(duplicate_ids)))
        return True

    async def _reconcile_limit_warmups(self, canonical_account_id: str, duplicate_ids: list[str]) -> None:
        existing_keys = {
            (window, reset_at)
            for window, reset_at in (
                await self._session.execute(
                    select(AccountLimitWarmup.window, AccountLimitWarmup.reset_at).where(
                        AccountLimitWarmup.account_id == canonical_account_id
                    )
                )
            ).all()
        }
        duplicate_warmups = (
            (
                await self._session.execute(
                    select(AccountLimitWarmup).where(AccountLimitWarmup.account_id.in_(duplicate_ids))
                )
            )
            .scalars()
            .all()
        )
        for warmup in duplicate_warmups:
            key = (warmup.window, warmup.reset_at)
            if key in existing_keys:
                await self._session.delete(warmup)
            else:
                warmup.account_id = canonical_account_id
                existing_keys.add(key)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = _UNSET,
    ) -> bool:
        async with sqlite_writer_section():
            previous_status = await self._session.scalar(
                select(Account.status).where(Account.id == account_id).with_for_update()
            )
            values: dict[str, object | None] = {
                "status": status,
                "deactivation_reason": deactivation_reason,
                "reset_at": reset_at,
            }
            if blocked_at is not _UNSET:
                values["blocked_at"] = blocked_at
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
                # An account marked for background deletion is terminal: a
                # stale in-flight settlement (e.g. a 429 from a request that
                # was selected before the DELETE) must not replace the
                # DEACTIVATED/pending_deletion state and make the account
                # selectable again mid-drain. Only a credential replacement
                # (which clears the marker) may resurrect the row.
                .where(Account.delete_requested_at.is_(None))
                .values(**values)
                .returning(Account.id)
            )
            updated_id = result.scalar_one_or_none()
            if updated_id is not None and self._hard_sticky_outage_started(previous_status, status):
                await self._refresh_hard_sticky_outage_grace(account_id)
            if updated_id is not None and status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
                await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
                await self._close_http_bridge_sessions_for_account(account_id)
            await self._session.commit()
            return updated_id is not None

    async def update_security_work_authorized(self, account_id: str, enabled: bool) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
                # Marked-for-deletion rows are gone from the operator's
                # perspective: ID-based mutations must report not-found, as the
                # synchronous delete did once the row was removed.
                .where(Account.delete_requested_at.is_(None))
                .values(security_work_authorized=enabled)
                .returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = _UNSET,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None | object = _UNSET,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool:
        async with sqlite_writer_section():
            values: dict[str, object | None] = {
                "status": status,
                "deactivation_reason": deactivation_reason,
                "reset_at": reset_at,
            }
            if blocked_at is not _UNSET:
                values["blocked_at"] = blocked_at
            stmt = (
                update(Account)
                .where(Account.id == account_id)
                .where(Account.status == expected_status)
                # Same pending-deletion fence as ``update_status``: marked
                # rows are terminal for ordinary status writers.
                .where(Account.delete_requested_at.is_(None))
                .values(**values)
                .returning(Account.id)
            )
            if expected_deactivation_reason is None:
                stmt = stmt.where(Account.deactivation_reason.is_(None))
            else:
                stmt = stmt.where(Account.deactivation_reason == expected_deactivation_reason)
            if expected_reset_at is None:
                stmt = stmt.where(Account.reset_at.is_(None))
            else:
                stmt = stmt.where(Account.reset_at == expected_reset_at)
            if expected_blocked_at is not _UNSET:
                if expected_blocked_at is None:
                    stmt = stmt.where(Account.blocked_at.is_(None))
                else:
                    stmt = stmt.where(Account.blocked_at == expected_blocked_at)
            if expected_refresh_token_encrypted is not None:
                # Guards permanent refresh-failure downgrades: a concurrent
                # re-auth/import rotates the token ciphertext without touching
                # status/reason/reset, and this write must lose that race.
                stmt = stmt.where(Account.refresh_token_encrypted == expected_refresh_token_encrypted)
            result = await self._session.execute(stmt)
            updated_id = result.scalar_one_or_none()
            if updated_id is not None and self._hard_sticky_outage_started(expected_status, status):
                await self._refresh_hard_sticky_outage_grace(account_id)
            if updated_id is not None and status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
                await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
                await self._close_http_bridge_sessions_for_account(account_id)
            await self._session.commit()
            return updated_id is not None

    @staticmethod
    def _hard_sticky_outage_started(
        previous_status: AccountStatus | None,
        status: AccountStatus,
    ) -> bool:
        return (
            previous_status is not None
            and previous_status not in _HARD_STICKY_UNAVAILABLE_STATUSES
            and status in _HARD_STICKY_UNAVAILABLE_STATUSES
        )

    async def _refresh_hard_sticky_outage_grace(self, account_id: str) -> None:
        """Start a fresh purge grace period when a hard owner goes unavailable."""

        await self._session.execute(
            update(StickySession)
            .where(
                StickySession.account_id == account_id,
                StickySession.kind == StickySessionKind.CODEX_SESSION,
            )
            .values(updated_at=utcnow())
        )

    async def seed_hard_sticky_outage_grace_on_startup(self) -> int:
        """Backfill, exactly once ever, a grace window for already-unavailable owners.

        ``_refresh_hard_sticky_outage_grace`` only fires on a live status
        transition into PAUSED/RATE_LIMITED/QUOTA_EXCEEDED, so it never runs
        for an account that was already sitting in one of those statuses
        before this process started (e.g. an outage that began minutes
        before a deploy). Without this, the purge scheduler's very first
        cleanup cycle after this feature ships could treat that mapping's
        stale ``updated_at`` as proof of a long-dead owner and purge it, even
        though the outage is brand new — violating the "merely transient
        outage is never purged" invariant for the upgrade window.

        That backfill only needs to happen once per database, not once per
        process start. All replicas share one database, and reseeding on
        every boot resets the grace clock for accounts that are still
        (correctly) unavailable; if deploys or autoscaling cycle faster than
        the purge cutoff, a durably-dead mapping's grace clock would never
        run out and it would never be purged. ``runtime_sentinels`` gives
        every replica a shared, durable "has this ever run" marker: the
        first replica to atomically stamp
        ``_HARD_STICKY_OUTAGE_GRACE_SEEDED_SENTINEL`` performs the backfill;
        every later boot, on this or any other replica, finds the sentinel
        already stamped and skips it, leaving the live per-transition hook
        as the sole grace-clock source from then on.
        """
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            insert_fn = pg_insert
        elif dialect == "sqlite":
            insert_fn = sqlite_insert
        else:
            raise RuntimeError(f"Hard-sticky outage grace seeding sentinel unsupported for dialect={dialect!r}")
        stamp_stmt = (
            insert_fn(RuntimeSentinel)
            .values(name=_HARD_STICKY_OUTAGE_GRACE_SEEDED_SENTINEL, value=utcnow().isoformat())
            .on_conflict_do_nothing(index_elements=[RuntimeSentinel.name])
            .returning(RuntimeSentinel.name)
        )
        try:
            async with sqlite_writer_section():
                stamp_result = await self._session.execute(stamp_stmt)
                stamped_by_this_boot = stamp_result.scalar_one_or_none() is not None
                if not stamped_by_this_boot:
                    await self._session.commit()
                    return 0
                account_ids = (
                    await self._session.scalars(
                        select(Account.id).where(Account.status.in_(_HARD_STICKY_UNAVAILABLE_STATUSES))
                    )
                ).all()
                for account_id in account_ids:
                    await self._refresh_hard_sticky_outage_grace(account_id)
                await self._session.commit()
        except OperationalError as exc:
            if not _is_missing_hard_sticky_seed_table(exc):
                raise
            await self._session.rollback()
            return 0
        return len(account_ids)

    async def _close_http_bridge_sessions_for_account(self, account_id: str) -> None:
        session_ids = select(HttpBridgeSessionRecord.id).where(HttpBridgeSessionRecord.account_id == account_id)
        await self._session.execute(
            delete(HttpBridgeSessionAlias).where(HttpBridgeSessionAlias.session_id.in_(session_ids))
        )
        await self._session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.account_id == account_id)
            .values(
                account_id=None,
                state=HttpBridgeSessionState.CLOSED,
                closed_at=utcnow(),
                owner_instance_id=None,
                lease_expires_at=None,
                latest_turn_state=None,
                latest_response_id=None,
                latest_input_item_count=None,
                latest_input_full_fingerprint=None,
                latest_pending_tool_calls_json=None,
            )
        )

    async def update_alias(self, account_id: str, alias: str | None) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
                # Marked-for-deletion rows are gone from the operator's
                # perspective: ID-based mutations must report not-found, as the
                # synchronous delete did once the row was removed.
                .where(Account.delete_requested_at.is_(None))
                .values(alias=alias)
                .returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_limit_warmup_enabled(self, account_id: str, enabled: bool) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
                # Marked-for-deletion rows are gone from the operator's
                # perspective: ID-based mutations must report not-found, as the
                # synchronous delete did once the row was removed.
                .where(Account.delete_requested_at.is_(None))
                .values(limit_warmup_enabled=enabled)
                .returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_routing_policy(self, account_id: str, routing_policy: str) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
                # Marked-for-deletion rows are gone from the operator's
                # perspective: ID-based mutations must report not-found, as the
                # synchronous delete did once the row was removed.
                .where(Account.delete_requested_at.is_(None))
                .values(routing_policy=routing_policy)
                .returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def begin_delete(self, account_id: str, *, delete_history: bool = False) -> bool:
        """Mark an account for background deletion; commits in milliseconds.

        Fast path of ``DELETE /api/accounts/{id}``: the account becomes
        terminal (``DEACTIVATED`` — every serving path already excludes it)
        and carries the pending-deletion marker that hides it from listings
        and enqueues it for the deletion worker, which drains its bulk rows
        in chunks and finalizes via :meth:`delete` with ``only_pending=True``.

        The stored token ciphertext is overwritten with empty-credential
        ciphertext in the same transaction: the row outlives the DELETE
        response by the drain duration, and no reader — including a
        pre-upgrade replica during a rolling deploy, whose export endpoints
        do not know the marker — may still be able to produce usable
        credentials from it. A credential replacement (the only supersede
        path) writes fresh ciphertext, and token rotation is CAS-guarded on
        the pre-wipe refresh ciphertext, so a stale in-flight rotation
        misses rather than resurrecting the old material. Before the wipe,
        the non-secret seat identity is preserved: legacy rows whose
        ``chatgpt_user_id`` was never backfilled carry it only inside the
        id-token claims, and targeted reauthentication — the promised
        supersede path — verifies the seat against exactly those two
        sources, so ``chatgpt_user_id`` is backfilled from the claims when
        absent.

        API-key account assignments are removed here as well (the FK cascade
        used to do this when the synchronous delete removed the row), so key
        listings and pooled-usage projections exclude the account
        immediately; the key's ``account_assignment_scope_enabled`` flag is
        persisted separately and keeps the key scoped.

        Idempotent: a repeat request on an already-marked account succeeds
        without changing the frozen ``delete_history`` choice (first request
        wins — matching the synchronous behavior, where a second DELETE after
        the first completed found nothing left to escalate).
        """
        # Repeat requests short-circuit BEFORE the writer section / row lock:
        # a drain chunk holds the account row (and, on SQLite, the writer
        # section) for up to a few seconds, and the fast-path contract is a
        # millisecond-scale response. The unlocked read is safe because the
        # repeat changes nothing — the first request froze the variant and
        # the wipe/cleanup already ran — and a replacement racing this read
        # supersedes the deletion exactly as if it landed after this
        # response. When credentials were replaced WITHOUT clearing the
        # marker (a pre-upgrade replica's replacement), fall through to the
        # full path so an explicit re-delete re-wipes and re-arms.
        marked_row = (
            await self._session.execute(
                select(
                    Account.delete_requested_at,
                    Account.access_token_encrypted,
                    Account.refresh_token_encrypted,
                    Account.id_token_encrypted,
                ).where(Account.id == account_id)
            )
        ).first()
        if (
            marked_row is not None
            and marked_row[0] is not None
            and not credentials_replaced_since_wipe(marked_row[1], marked_row[2], marked_row[3])
        ):
            return True
        encryptor = TokenEncryptor()
        wiped_token = encryptor.encrypt("")
        async with sqlite_writer_section():
            seat_stmt = select(Account.chatgpt_user_id, Account.id_token_encrypted).where(Account.id == account_id)
            if self._dialect_name() == "postgresql":
                # Hold the row through the mark so the derived seat identity
                # cannot go stale between this read and the update below.
                seat_stmt = seat_stmt.with_for_update(key_share=True)
            seat_row = (await self._session.execute(seat_stmt)).first()
            if seat_row is None:
                await self._session.rollback()
                return False
            seat_user_id: str | None = seat_row[0]
            if seat_user_id is None:
                try:
                    claims = extract_id_token_claims(encryptor.decrypt(seat_row[1]))
                    seat_user_id = resolve_seat_identity(claims, claims.auth)
                except Exception:
                    seat_user_id = None
            values: dict[str, Any] = {
                "status": AccountStatus.DEACTIVATED,
                "deactivation_reason": ACCOUNT_PENDING_DELETION_REASON,
                "reset_at": None,
                "blocked_at": None,
                "access_token_encrypted": wiped_token,
                "refresh_token_encrypted": wiped_token,
                "id_token_encrypted": wiped_token,
                "delete_requested_at": func.coalesce(Account.delete_requested_at, utcnow()),
                "delete_history_requested": case(
                    (Account.delete_requested_at.is_(None), delete_history),
                    else_=Account.delete_history_requested,
                ),
            }
            if seat_user_id is not None:
                values["chatgpt_user_id"] = seat_user_id
            result = await self._session.execute(
                update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
            )
            updated_id = result.scalar_one_or_none()
            if updated_id is not None:
                # Same immediate cleanup the DEACTIVATED transition performs:
                # sticky mappings and bridge sessions must not outlive the
                # account's routability.
                await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
                await self._close_http_bridge_sessions_for_account(account_id)
                await self._session.execute(
                    delete(ApiKeyAccountAssignment).where(ApiKeyAccountAssignment.account_id == account_id)
                )
            await self._session.commit()
            return updated_id is not None

    async def delete(
        self,
        account_id: str,
        *,
        delete_history: bool = False,
        only_pending: bool = False,
    ) -> bool:
        async with sqlite_writer_section():
            if self._dialect_name() == "postgresql":
                # Identity membership precedes the fold-state lock so live
                # settlement and deletion cannot form an identity/fold cycle.
                locked_account = await self._lock_postgresql_account_identity_membership(account_id, None)
                pending_state = (
                    None
                    if locked_account is None
                    else (
                        locked_account.delete_requested_at,
                        locked_account.delete_history_requested,
                        locked_account.access_token_encrypted,
                        locked_account.refresh_token_encrypted,
                        locked_account.id_token_encrypted,
                    )
                )
            else:
                pending_state = (
                    await self._session.execute(
                        select(
                            Account.delete_requested_at,
                            Account.delete_history_requested,
                            Account.access_token_encrypted,
                            Account.refresh_token_encrypted,
                            Account.id_token_encrypted,
                        ).where(Account.id == account_id)
                    )
                ).first()
            if only_pending:
                # Background finalization: a credential replacement
                # (re-import/reauth) that cleared the marker supersedes the
                # deletion, so touch nothing. The variant comes from the
                # persisted flag frozen at request time, never the caller.
                # On PostgreSQL the identity-membership row lock held above
                # keeps the marker stable through this transaction; on SQLite
                # the writer section serializes all writers.
                if pending_state is None or pending_state[0] is None:
                    await self._session.rollback()
                    return False
                if credentials_replaced_since_wipe(pending_state[2], pending_state[3], pending_state[4]):
                    # A pre-upgrade replica replaced the credentials without
                    # being able to clear marker columns its ORM does not
                    # know. That replacement supersedes the deletion: clear
                    # the marker (we hold the row lock) and abandon.
                    await self._session.execute(
                        update(Account)
                        .where(Account.id == account_id)
                        .values(delete_requested_at=None, delete_history_requested=False)
                    )
                    await self._session.commit()
                    return False
                delete_history = bool(pending_state[1])
            # Serialize against fold passes before touching the account's
            # request logs: without the fold-state lock an in-flight hourly
            # slice could aggregate the pre-delete attribution but commit
            # after this transaction, resurrecting the account's folded rows
            # the mirrors below just moved or removed.
            await lock_fold_state(self._session)
            if self._dialect_name() == "postgresql":
                # Upgrade the account row to a full FOR UPDATE lock BEFORE the
                # raw sweeps. FOR UPDATE conflicts with the KEY SHARE taken by
                # concurrent request-log FK inserts, so every in-flight
                # stream's log row either commits before this point (and the
                # sweeps below see it) or its insert blocks until this
                # transaction commits and then fails its FK against the
                # deleted row — the same outcome a post-delete insert always
                # had. Without the upgrade, an insert could commit between
                # the sweep and the account-row delete: the FK's ON DELETE
                # SET NULL would leave a live (deleted_at IS NULL) orphan on
                # the soft path, or surviving raw history under
                # delete_history. Lock order (identity -> fold -> row
                # exclusive) matches the historical transaction, where the
                # final DELETE acquired this same exclusive lock after the
                # fold lock.
                await self._session.execute(select(Account.id).where(Account.id == account_id).with_for_update())
            await self._session.execute(delete(UsageHistory).where(UsageHistory.account_id == account_id))
            if delete_history:
                await self._session.execute(delete(RequestLog).where(RequestLog.account_id == account_id))
                # Mirror the raw DELETE into the folded time-axis buckets;
                # raw below the watermark may already be pruned, so folded
                # rows can never be recomputed and must be removed directly.
                await mirror_account_hard_delete_into_time_rollups(self._session, account_id)
            else:
                await self._session.execute(
                    update(RequestLog)
                    .where(RequestLog.account_id == account_id)
                    .values(account_id=None, deleted_at=utcnow()),
                )
                # Mirror the retroactive detach (account_id=NULL, deleted_at
                # set) into the folded buckets: move them to the orphaned
                # deleted dimension so time-series totals are preserved.
                await mirror_account_soft_delete_into_time_rollups(self._session, account_id)
            await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
            await self._session.execute(delete(AccountUsageRollup).where(AccountUsageRollup.account_id == account_id))
            result = await self._session.execute(delete(Account).where(Account.id == account_id).returning(Account.id))
            deleted_id = result.scalar_one_or_none()
            await self._session.commit()
            if deleted_id is not None:
                _clear_bulk_history_since_sqlite_cache()
                # Deletion drops the account's rollup row and detaches or
                # deletes its request logs; cached listing summaries would
                # keep reporting the account until TTL expiry.
                _clear_request_usage_summary_cache()
            return deleted_id is not None

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
    ) -> bool:
        """Persist rotated access/refresh/id token ciphertext under a mandatory
        compare-and-set on the refresh-token ciphertext.

        This is the ONLY method that writes token ciphertext, and the CAS
        predicate (``expected_refresh_token_encrypted``) is REQUIRED — there is
        no code path that writes ``refresh_token_encrypted`` unconditionally.
        The comparison is atomic in the database, so a concurrent rotation
        committed after the caller read ``expected`` makes this write MISS (it
        returns ``False`` and touches no row) rather than clobbering the peer's
        fresh material. Metadata is co-written here only because a genuine
        rotation carries a fresh identity/plan/workspace snapshot; metadata-only
        writers must use ``update_account_metadata`` (which cannot touch token
        material at all).
        """
        async with sqlite_writer_section():
            if self._dialect_name() == "postgresql":
                await self._lock_postgresql_account_identity_membership(account_id, chatgpt_account_id)
            values: dict[str, bytes | datetime | str] = {
                "access_token_encrypted": access_token_encrypted,
                "refresh_token_encrypted": refresh_token_encrypted,
                "id_token_encrypted": id_token_encrypted,
                "last_refresh": last_refresh,
            }
            if plan_type is not None:
                values["plan_type"] = plan_type
            if email is not None:
                values["email"] = email
            if chatgpt_account_id is not None:
                values["chatgpt_account_id"] = chatgpt_account_id
            if chatgpt_user_id is not None:
                values["chatgpt_user_id"] = chatgpt_user_id
            if workspace_id is not None:
                values["workspace_id"] = workspace_id
            if workspace_label is not None:
                values["workspace_label"] = workspace_label
            if seat_type is not None:
                values["seat_type"] = seat_type
            stmt = (
                update(Account)
                .where(Account.id == account_id)
                # Compare-and-set on the exact ciphertext the caller read before
                # the upstream exchange, so a concurrent rotation is never
                # clobbered by a slower writer.
                .where(Account.refresh_token_encrypted == expected_refresh_token_encrypted)
                .values(**values)
                .returning(Account.id)
            )
            result = await self._session.execute(stmt)
            await self._session.commit()
            return result.scalar_one_or_none() is not None

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
    ) -> bool:
        """Update non-token account metadata (identity/plan/workspace fields).

        This method structurally CANNOT write access/refresh/id token
        ciphertext — there is no parameter for it — so a metadata-only writer
        holding a stale ``Account`` snapshot can never clobber a concurrent
        cross-replica token rotation. It is the correct path for backfills and
        identity syncs that must never touch token material. Only the provided
        (non-``None``) fields are written; a call with nothing to set is a
        no-op existence check.
        """
        async with sqlite_writer_section():
            if self._dialect_name() == "postgresql":
                await self._lock_postgresql_account_identity_membership(account_id, chatgpt_account_id)
            values: dict[str, str | datetime] = {}
            if plan_type is not None:
                values["plan_type"] = plan_type
            if email is not None:
                values["email"] = email
            if chatgpt_account_id is not None:
                values["chatgpt_account_id"] = chatgpt_account_id
            if chatgpt_user_id is not None:
                values["chatgpt_user_id"] = chatgpt_user_id
            if workspace_id is not None:
                values["workspace_id"] = workspace_id
            if workspace_label is not None:
                values["workspace_label"] = workspace_label
            if seat_type is not None:
                values["seat_type"] = seat_type
            if last_refresh is not None:
                values["last_refresh"] = last_refresh
            if not values:
                existing = await self._session.get(Account, account_id)
                return existing is not None
            result = await self._session.execute(
                update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def workspace_slot_taken(
        self,
        *,
        account_id: str,
        email: str,
        chatgpt_account_id: str | None,
        workspace_id: str,
    ) -> bool:
        if chatgpt_account_id:
            predicates = [
                (Account.chatgpt_account_id == chatgpt_account_id) & (Account.workspace_id == workspace_id),
                (
                    (Account.email == email)
                    & (Account.workspace_id == workspace_id)
                    & Account.chatgpt_account_id.is_(None)
                ),
            ]
        else:
            predicates = [(Account.email == email) & (Account.workspace_id == workspace_id)]
        result = await self._session.execute(
            select(Account.id).where(Account.id != account_id).where(or_(*predicates)).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _merge_by_email_enabled(self) -> bool:
        settings = await self._session.get(DashboardSettings, _SETTINGS_ROW_ID)
        if settings is None:
            return True
        return not settings.import_without_overwrite

    async def additional_quota_routing_policy_overrides(self) -> dict[str, str]:
        settings = await self._session.get(DashboardSettings, _SETTINGS_ROW_ID)
        if settings is None or not settings.additional_quota_routing_policies_json:
            return {}
        try:
            parsed = json.loads(settings.additional_quota_routing_policies_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        string_policies = {
            key: value for key, value in parsed.items() if isinstance(key, str) and isinstance(value, str)
        }
        return normalize_additional_quota_routing_policy_overrides(string_policies)

    async def _next_available_account_id(self, base_id: str) -> str:
        candidate = base_id
        sequence = 2
        while await self._session.get(Account, candidate) is not None:
            candidate = f"{base_id}{_DUPLICATE_ACCOUNT_SUFFIX}{sequence}"
            sequence += 1
        return candidate

    async def _single_account_by_email(self, email: str) -> Account | None:
        result = await self._session.execute(
            select(Account).where(Account.email == email).order_by(Account.created_at.asc(), Account.id.asc()).limit(2)
        )
        matches = list(result.scalars().all())
        if not matches:
            return None
        if len(matches) > 1:
            raise AccountIdentityConflictError(email)
        return matches[0]

    async def _single_unknown_workspace_account_by_email(self, email: str) -> Account | None:
        result = await self._session.execute(
            select(Account)
            .where(Account.email == email)
            .where(Account.workspace_id.is_(None))
            .where(Account.workspace_label.is_(None))
            .order_by(Account.created_at.asc(), Account.id.asc())
            .limit(2)
        )
        matches = list(result.scalars().all())
        if not matches:
            return None
        if len(matches) > 1:
            raise AccountIdentityConflictError(email)
        return matches[0]

    async def _account_by_slot_identity(self, account: Account) -> Account | None:
        workspace_slot = _workspace_slot_identity(account)
        if account.chatgpt_account_id and account.email and workspace_slot:
            column, value = workspace_slot
            result = await self._session.execute(
                select(Account)
                .where(Account.chatgpt_account_id == account.chatgpt_account_id)
                .where(Account.email == account.email)
                .where(column == value)
                .order_by(Account.created_at.asc(), Account.id.asc())
                .limit(1)
            )
            if matched := result.scalar_one_or_none():
                return matched
        if account.chatgpt_account_id and account.email and account.workspace_id and account.workspace_label:
            result = await self._session.execute(
                select(Account)
                .where(Account.chatgpt_account_id == account.chatgpt_account_id)
                .where(Account.email == account.email)
                .where(Account.workspace_id.is_(None))
                .where(Account.workspace_label == account.workspace_label)
                .order_by(Account.created_at.asc(), Account.id.asc())
                .limit(1)
            )
            if matched := result.scalar_one_or_none():
                return matched
        if workspace_slot and account.email:
            column, value = workspace_slot
            result = await self._session.execute(
                select(Account)
                .where(Account.email == account.email)
                .where(column == value)
                .order_by(Account.created_at.asc(), Account.id.asc())
                .limit(1)
            )
            matched = result.scalar_one_or_none()
            if matched is not None and _can_reuse_email_fallback(matched, account):
                return matched
        return None

    async def _lock_postgresql_account_identity_membership(
        self,
        account_id: str,
        incoming_chatgpt_account_id: str | None,
        *,
        second_attempt: bool = False,
    ) -> Account | None:
        """Lock one row's old/new upstream memberships before mutating it."""
        observed_identity = await self._session.scalar(
            select(Account.chatgpt_account_id).where(Account.id == account_id)
        )
        await lock_postgresql_account_identities(
            self._session,
            (observed_identity, incoming_chatgpt_account_id),
        )
        locked_account = await self._session.scalar(
            select(Account)
            .where(Account.id == account_id)
            # PostgreSQL FOR NO KEY UPDATE stabilizes identity membership but
            # remains compatible with the KEY SHARE lock taken by concurrent
            # rollup FK inserts. Deletion upgrades only after the fold lock.
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        locked_identity = locked_account.chatgpt_account_id if locked_account is not None else None
        if locked_identity == observed_identity:
            return locked_account
        await self._session.rollback()
        if second_attempt:
            raise AccountIdentityRelockError("Account identity changed during PostgreSQL membership lock acquisition")
        return await self._lock_postgresql_account_identity_membership(
            account_id,
            incoming_chatgpt_account_id,
            second_attempt=True,
        )

    async def _lock_postgresql_upsert_identity_candidates(
        self,
        account: Account,
        *,
        include_email: bool,
    ) -> frozenset[str]:
        predicates = _upsert_identity_candidate_predicates(account, include_email=include_email)
        observed = (
            (await self._session.execute(select(Account.chatgpt_account_id).where(or_(*predicates)))).scalars().all()
        )
        identities = frozenset(identity for identity in (*observed, account.chatgpt_account_id) if identity)
        await lock_postgresql_account_identities(self._session, identities)
        return identities

    async def _postgresql_upsert_identity_candidates_are_locked(
        self,
        account: Account,
        *,
        include_email: bool,
        locked_identities: frozenset[str],
    ) -> bool:
        predicates = _upsert_identity_candidate_predicates(account, include_email=include_email)
        current = (
            (
                await self._session.execute(
                    select(Account.chatgpt_account_id).where(or_(*predicates)).with_for_update(key_share=True)
                )
            )
            .scalars()
            .all()
        )
        return all(identity is None or identity in locked_identities for identity in current)

    def _dialect_name(self) -> str:
        return self._session.get_bind().dialect.name

    async def _acquire_sqlite_merge_lock(self) -> None:
        try:
            await self._session.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as exc:
            message = str(exc).lower()
            if "within a transaction" not in message:
                raise
            # A no-op write escalates the current deferred transaction to a write
            # transaction, serializing concurrent writers.
            await self._session.execute(text("UPDATE accounts SET id = id WHERE 1 = 0"))

    async def _acquire_postgresql_merge_lock(self, email: str) -> None:
        lock_key = advisory_lock_key("merge-email", email)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    async def _acquire_postgresql_identity_lock(self, account_id: str) -> None:
        lock_key = advisory_lock_key("account-id", account_id)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )


def _apply_account_updates(target: Account, source: Account) -> None:
    if source.chatgpt_account_id is not None:
        target.chatgpt_account_id = source.chatgpt_account_id
    if source.chatgpt_user_id is not None:
        target.chatgpt_user_id = source.chatgpt_user_id
    target.email = source.email
    if source.workspace_id is not None or target.workspace_id is None:
        target.workspace_id = source.workspace_id
        target.workspace_label = source.workspace_label
        target.seat_type = source.seat_type
    if not target.codex_installation_id:
        target.codex_installation_id = source.codex_installation_id or str(uuid.uuid4())
    target.plan_type = source.plan_type
    target.access_token_encrypted = source.access_token_encrypted
    target.refresh_token_encrypted = source.refresh_token_encrypted
    target.id_token_encrypted = source.id_token_encrypted
    target.last_refresh = source.last_refresh
    target.status = source.status
    target.deactivation_reason = source.deactivation_reason
    target.reset_at = source.reset_at
    target.blocked_at = source.blocked_at
    # A credential replacement (re-import/reauth) supersedes a pending
    # background deletion: clearing the marker makes the deletion worker
    # abandon the account before finalizing (rows already drained stay
    # detached — history loss was requested by the earlier delete).
    target.delete_requested_at = None
    target.delete_history_requested = False


def _slot_lock_key(account: Account, *, preserve_unknown_workspace_duplicates: bool = True) -> str:
    return _slot_lock_keys(
        account,
        preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates,
    )[0]


def _slot_lock_keys(account: Account, *, preserve_unknown_workspace_duplicates: bool = True) -> tuple[str, ...]:
    keys: list[str] = []
    workspace_key = _workspace_slot_key(account)
    if account.chatgpt_account_id:
        if workspace_key:
            keys.append(f"slot:{account.chatgpt_account_id}:{workspace_key}")
        elif account.email:
            keys.append(f"slot:{account.chatgpt_account_id}:{account.email}")
    if account.email and workspace_key:
        keys.append(f"slot-email:{account.email}:{workspace_key}")
        if not preserve_unknown_workspace_duplicates:
            keys.append(f"slot-email-unknown:{account.email}")
    if keys:
        return tuple(keys)
    if account.email and not preserve_unknown_workspace_duplicates:
        return (f"slot-email-unknown:{account.email}",)
    return (f"slot-local:{account.id}",)


def _upsert_identity_candidate_predicates(account: Account, *, include_email: bool) -> list[Any]:
    predicates = [Account.id == account.id]
    if account.chatgpt_account_id:
        predicates.append(Account.chatgpt_account_id == account.chatgpt_account_id)
    if include_email and account.email:
        predicates.append(Account.email == account.email)
    return predicates


def _same_unknown_workspace_identity(existing: Account, incoming: Account) -> bool:
    return (
        _workspace_slot_key(existing) is None
        and _workspace_slot_key(incoming) is None
        and existing.chatgpt_account_id == incoming.chatgpt_account_id
        and existing.email == incoming.email
    )


def _workspace_slot_identity(account: Account) -> tuple[Any, str] | None:
    if account.workspace_id:
        return Account.workspace_id, account.workspace_id
    if account.workspace_label:
        return Account.workspace_label, account.workspace_label
    return None


def _workspace_slot_key(account: Account) -> str | None:
    if account.workspace_id:
        return account.workspace_id
    if account.workspace_label:
        return account.workspace_label
    return None


def _is_workspace_less_reauth_for_known_slot(
    existing: Account,
    incoming: Account,
    *,
    merge_by_chatgpt_identity: bool,
) -> bool:
    return (
        merge_by_chatgpt_identity
        and existing.workspace_id is not None
        and incoming.workspace_id is None
        and incoming.chatgpt_account_id is not None
        and existing.chatgpt_account_id == incoming.chatgpt_account_id
        and existing.email == incoming.email
    )


def _can_reuse_email_fallback(existing: Account, incoming: Account) -> bool:
    existing_workspace_key = _workspace_slot_key(existing)
    incoming_workspace_key = _workspace_slot_key(incoming)
    if existing_workspace_key and incoming_workspace_key and existing_workspace_key != incoming_workspace_key:
        return False
    return (
        not incoming.chatgpt_account_id
        or not existing.chatgpt_account_id
        or existing.chatgpt_account_id == incoming.chatgpt_account_id
    )
