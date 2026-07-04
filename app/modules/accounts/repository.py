from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountLimitWarmup,
    AccountStatus,
    AdditionalUsageHistory,
    ApiKeyAccountAssignment,
    DashboardSettings,
    HttpBridgeSessionRecord,
    RequestLog,
    StickySession,
    UsageHistory,
)
from app.db.session import sqlite_writer_section
from app.modules.usage.additional_quota_keys import normalize_additional_quota_routing_policy_overrides
from app.modules.usage.repository import _clear_bulk_history_since_sqlite_cache

_SETTINGS_ROW_ID = 1
_DUPLICATE_ACCOUNT_SUFFIX = "__copy"
_UNSET = object()


@dataclass(frozen=True, slots=True)
class AccountRequestUsageSummary:
    request_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float


class AccountIdentityConflictError(Exception):
    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(
            f"Cannot overwrite account for email '{email}' because multiple matching accounts exist. "
            "Remove duplicates or enable import without overwrite."
        )


class AccountsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def get_by_id(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id)

    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
        stmt = select(Account).order_by(Account.email)
        if refresh_existing:
            stmt = stmt.execution_options(populate_existing=True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_request_usage_summary_by_account(
        self,
        account_ids: list[str] | None = None,
    ) -> dict[str, AccountRequestUsageSummary]:
        summaries: dict[str, AccountRequestUsageSummary] = {}
        output_tokens_expr = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
        conditions: list = [
            RequestLog.request_kind.not_in(("warmup", "limit_warmup")),
            RequestLog.deleted_at.is_(None),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))

        latest_request_log_ids = (
            select(
                func.max(RequestLog.id).label("request_log_id"),
            )
            .where(*conditions)
            .group_by(
                RequestLog.account_id,
                RequestLog.request_id,
                RequestLog.requested_at,
            )
            .subquery("latest_request_log_ids")
        )
        stmt = (
            select(
                RequestLog.account_id,
                func.count(RequestLog.id).label("request_count"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("total_cost_usd"),
            )
            .join(latest_request_log_ids, RequestLog.id == latest_request_log_ids.c.request_log_id)
            .group_by(RequestLog.account_id)
        )
        result = await self._session.execute(stmt)
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
            input_sum = int(input_tokens or 0)
            output_sum = int(output_tokens or 0)
            cached_sum = int(cached_input_tokens or 0)
            cached_sum = max(0, min(cached_sum, input_sum))
            return_row = AccountRequestUsageSummary(
                request_count=int(request_count or 0),
                total_tokens=input_sum + output_sum,
                cached_input_tokens=cached_sum,
                total_cost_usd=round(float(total_cost_usd or 0.0), 6),
            )
            summaries[account_id] = return_row

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
            # Identity-keyed advisory lock must always be acquired when
            # identity reconciliation is in play, regardless of
            # merge_by_email. Two concurrent reauths for the same
            # upstream chatgpt_account_id but different email claims
            # (e.g. user changed email upstream) would otherwise take
            # different email-scoped locks, both miss the canonical-row
            # lookup below, and both INSERT a duplicate row for the
            # same identity.
            #
            # Ordering identity-first, then email, gives a stable
            # acquisition order across all callers so two concurrent
            # reauths that overlap on either key serialize without
            # deadlock.
            identity_locked = False
            if merge_by_chatgpt_identity and account.chatgpt_account_id:
                await self._acquire_postgresql_identity_lock(f"chatgpt:{account.chatgpt_account_id}")
                identity_locked = True
            if merge_by_email:
                await self._acquire_postgresql_merge_lock(account.email)  # ty:ignore[invalid-argument-type]
            elif not identity_locked:
                await self._acquire_postgresql_identity_lock(account.id)

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
                _apply_account_updates(canonical, account)
                usage_cache_dirty = await self._reconcile_chatgpt_identity_duplicates(
                    canonical=canonical,
                    chatgpt_account_id=account.chatgpt_account_id,
                    workspace_id=account.workspace_id,
                    email=account.email,
                )
                await self._session.commit()
                if usage_cache_dirty:
                    _clear_bulk_history_since_sqlite_cache()
                await self._session.refresh(canonical)
                return canonical

        existing = await self._session.get(Account, account.id)
        if existing:
            if merge_by_email or _is_workspace_less_reauth_for_known_slot(
                existing,
                account,
                merge_by_chatgpt_identity=merge_by_chatgpt_identity,
            ):
                _apply_account_updates(existing, account)
                await self._session.commit()
                await self._session.refresh(existing)
                return existing
            account.id = await self._next_available_account_id(account.id)

        if merge_by_email:
            existing_by_email = await self._single_account_by_email(account.email)  # ty:ignore[invalid-argument-type]
            if existing_by_email:
                _apply_account_updates(existing_by_email, account)
                await self._session.commit()
                await self._session.refresh(existing_by_email)
                return existing_by_email

        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def upsert_reauthorized(self, account: Account) -> Account:
        return await self.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)

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
    ) -> Account:
        if preserve_unknown_workspace_duplicates is None:
            preserve_unknown_workspace_duplicates = not await self._merge_by_email_enabled()
        dialect_name = self._dialect_name()
        if dialect_name == "sqlite":
            await self._acquire_sqlite_merge_lock()
        elif dialect_name == "postgresql":
            for lock_key in sorted(
                _slot_lock_keys(
                    account,
                    preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates,
                )
            ):
                await self._acquire_postgresql_identity_lock(lock_key)

        existing = await self._account_by_slot_identity(account)
        if existing:
            _apply_account_updates(existing, account)
            await self._session.commit()
            await self._session.refresh(existing)
            return existing

        existing_by_id = await self._session.get(Account, account.id)
        if existing_by_id:
            if _same_unknown_workspace_identity(existing_by_id, account) and not preserve_unknown_workspace_duplicates:
                _apply_account_updates(existing_by_id, account)
                await self._session.commit()
                await self._session.refresh(existing_by_id)
                return existing_by_id
            account.id = await self._next_available_account_id(account.id)
        elif not preserve_unknown_workspace_duplicates:
            if _workspace_slot_key(account):
                existing_by_email = await self._single_unknown_workspace_account_by_email(account.email)  # ty:ignore[invalid-argument-type]
            elif preserve_identity_slots and account.chatgpt_account_id:
                existing_by_email = None
            else:
                existing_by_email = await self._single_account_by_email(account.email)  # ty:ignore[invalid-argument-type]
            if existing_by_email and not _can_reuse_email_fallback(existing_by_email, account):
                existing_by_email = None
            if existing_by_email:
                _apply_account_updates(existing_by_email, account)
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
            values: dict[str, object | None] = {
                "status": status,
                "deactivation_reason": deactivation_reason,
                "reset_at": reset_at,
            }
            if blocked_at is not _UNSET:
                values["blocked_at"] = blocked_at
            result = await self._session.execute(
                update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_rate_limit_cache(
        self,
        account_id: str,
        fields: dict[str, object] | None = None,
        **kwargs: object,
    ) -> bool:
        """Persist a partial update of the seven Anthropic rate-limit cache columns.

        Phase 9 (Task 9.0) of the Claude OAuth pool:
        ``openspec/changes/add-claude-oauth-pool/specs/account-routing/spec.md``
        — *Claude rate-limit cooldown mirrors Codex cooldown*.

        Only the columns whose value is present in ``fields`` are written; the
        other rate-limit columns are left untouched. Passing an empty mapping
        is a no-op (no SQL UPDATE is issued) so the cooldown helper in the
        load balancer can safely forward the parsed dict without a separate
        "is anything present" guard.

        Two call styles are supported:

        - ``update_rate_limit_cache(account_id, {"rate_limit_requests_remaining": 42})``
          — the production call site in
          ``app/modules/proxy/load_balancer.py::record_claude_rate_limit_response``
          forwards the parsed dict positionally.
        - ``update_rate_limit_cache(
              account_id,
              rate_limit_requests_remaining=42,
              rate_limit_status="allowed",
          )`` — keyword form, useful in tests and ad-hoc callers.

        The signature accepts both shapes so the contract documented in the
        OpenSpec plan ("only updates non-None values") holds regardless of
        how callers marshal their arguments.
        """
        merged: dict[str, object] = {}
        if fields:
            merged.update(fields)
        merged.update(kwargs)
        # Drop None values so the partial-update contract is preserved
        # regardless of which call style the caller used.
        values: dict[str, object] = {key: value for key, value in merged.items() if value is not None}
        # Only the seven Anthropic rate-limit cache columns are writable
        # through this helper. Other keys are silently ignored to keep the
        # repo narrow — anything else (status, reset_at, blocked_at, etc.)
        # must go through the dedicated ``update_status`` path.
        allowed_columns = {
            "rate_limit_requests_remaining",
            "rate_limit_requests_reset_at",
            "rate_limit_input_tokens_remaining",
            "rate_limit_input_tokens_reset_at",
            "rate_limit_output_tokens_remaining",
            "rate_limit_output_tokens_reset_at",
            "rate_limit_status",
        }
        values = {key: value for key, value in values.items() if key in allowed_columns}
        if not values:
            return False

        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_security_work_authorized(self, account_id: str, enabled: bool) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
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
            result = await self._session.execute(stmt)
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_alias(self, account_id: str, alias: str | None) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account).where(Account.id == account_id).values(alias=alias).returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_limit_warmup_enabled(self, account_id: str, enabled: bool) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
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
                .values(routing_policy=routing_policy)
                .returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def delete(self, account_id: str, *, delete_history: bool = False) -> bool:
        async with sqlite_writer_section():
            await self._session.execute(delete(UsageHistory).where(UsageHistory.account_id == account_id))
            if delete_history:
                await self._session.execute(delete(RequestLog).where(RequestLog.account_id == account_id))
            else:
                await self._session.execute(
                    update(RequestLog)
                    .where(RequestLog.account_id == account_id)
                    .values(account_id=None, deleted_at=utcnow()),
                )
            await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
            result = await self._session.execute(delete(Account).where(Account.id == account_id).returning(Account.id))
            deleted_id = result.scalar_one_or_none()
            await self._session.commit()
            if deleted_id is not None:
                _clear_bulk_history_since_sqlite_cache()
            return deleted_id is not None

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
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        async with sqlite_writer_section():
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
            if workspace_id is not None:
                values["workspace_id"] = workspace_id
            if workspace_label is not None:
                values["workspace_label"] = workspace_label
            if seat_type is not None:
                values["seat_type"] = seat_type
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
        lock_key = _advisory_lock_key("merge-email", email)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    async def _acquire_postgresql_identity_lock(self, account_id: str) -> None:
        lock_key = _advisory_lock_key("account-id", account_id)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )


def _apply_account_updates(target: Account, source: Account) -> None:
    if source.chatgpt_account_id is not None:
        target.chatgpt_account_id = source.chatgpt_account_id
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


def _advisory_lock_key(scope: str, value: str) -> int:
    digest = hashlib.sha256(f"{scope}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
