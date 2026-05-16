from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Protocol

from sqlalchemy.exc import OperationalError

from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.usage.pricing import (
    UsageTokens,
    calculate_cost_from_usage,
    get_pricing_for_model,
)
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, ApiKey, ApiKeyLimit, LimitType, LimitWindow
from app.modules.api_keys.limit_windows import advance_limit_reset, next_limit_reset
from app.modules.api_keys.repository import (
    _UNSET,
    ApiKeyTrendBucket,
    ApiKeyUsageSummary,
    ApiKeyUsageTotals,
    ReservationResult,
    UsageReservationData,
    UsageReservationItemData,
    _Unset,
)

_SPARKLINE_DAYS = 7
_DETAIL_BUCKET_SECONDS = 3600
_SQLITE_BUSY_RETRY_ATTEMPTS = 4
_SQLITE_BUSY_RETRY_BASE_SECONDS = 0.1


class ApiKeysRepositoryProtocol(Protocol):
    async def create(self, row: ApiKey) -> ApiKey: ...

    async def get_by_id(self, key_id: str) -> ApiKey | None: ...

    async def get_by_hash(self, key_hash: str) -> ApiKey | None: ...

    async def list_all(self) -> list[ApiKey]: ...
    async def list_usage_summary_by_key(self) -> dict[str, ApiKeyUsageSummary]: ...
    async def get_usage_summary_by_key_id(self, key_id: str) -> ApiKeyUsageSummary: ...
    async def list_accounts_by_ids(self, account_ids: list[str]) -> list[Account]: ...

    async def update(
        self,
        key_id: str,
        *,
        name: str | _Unset = ...,
        allowed_models: str | None | _Unset = ...,
        enforced_model: str | None | _Unset = ...,
        enforced_reasoning_effort: str | None | _Unset = ...,
        enforced_service_tier: str | None | _Unset = ...,
        account_assignment_scope_enabled: bool | _Unset = ...,
        expires_at: datetime | None | _Unset = ...,
        is_active: bool | _Unset = ...,
        key_hash: str | _Unset = ...,
        key_prefix: str | _Unset = ...,
        commit: bool = True,
    ) -> ApiKey | None: ...

    async def delete(self, key_id: str) -> bool: ...

    async def update_last_used(self, key_id: str, *, commit: bool = True) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def get_limits_by_key(self, key_id: str) -> list[ApiKeyLimit]: ...

    async def replace_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]: ...

    async def upsert_limits(
        self, key_id: str, limits: list[ApiKeyLimit], *, commit: bool = True
    ) -> list[ApiKeyLimit]: ...
    async def replace_account_assignments(
        self, key_id: str, account_ids: list[str], *, commit: bool = True
    ) -> None: ...

    async def increment_limit_usage(
        self,
        key_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_microdollars: int,
    ) -> None: ...

    async def reset_limit(self, limit_id: int, *, expected_reset_at: datetime, new_reset_at: datetime) -> bool: ...

    async def try_reserve_usage(
        self,
        limit_id: int,
        *,
        delta: int,
        expected_reset_at: datetime,
    ) -> ReservationResult: ...

    async def adjust_reserved_usage(
        self,
        limit_id: int,
        *,
        delta: int,
        expected_reset_at: datetime,
    ) -> bool: ...

    async def create_usage_reservation(
        self,
        reservation_id: str,
        *,
        key_id: str,
        model: str,
        items: list[UsageReservationItemData],
    ) -> None: ...

    async def get_usage_reservation(self, reservation_id: str) -> UsageReservationData | None: ...

    async def transition_usage_reservation_status(
        self,
        reservation_id: str,
        *,
        expected_status: str,
        new_status: str,
    ) -> bool: ...

    async def upsert_reservation_item_actual(
        self,
        reservation_id: str,
        *,
        item: UsageReservationItemData,
        actual_delta: int,
    ) -> None: ...

    async def settle_usage_reservation(
        self,
        reservation_id: str,
        *,
        status: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_microdollars: int | None,
    ) -> None: ...

    async def trends_by_key(
        self,
        key_id: str,
        since: datetime,
        until: datetime,
        bucket_seconds: int = 3600,
    ) -> list[ApiKeyTrendBucket]: ...

    async def usage_7d(
        self,
        key_id: str,
        since: datetime,
        until: datetime,
    ) -> ApiKeyUsageTotals: ...


class ApiKeyNotFoundError(ValueError):
    pass


class ApiKeyInvalidError(ValueError):
    pass


class ApiKeyRateLimitExceededError(ValueError):
    def __init__(self, *, message: str, reset_at: datetime) -> None:
        super().__init__(message)
        self.reset_at = reset_at


@dataclass(frozen=True, slots=True)
class LimitRuleData:
    id: int
    limit_type: str
    limit_window: str
    max_value: int
    current_value: int
    model_filter: str | None
    reset_at: datetime


@dataclass(frozen=True, slots=True)
class LimitRuleInput:
    limit_type: str
    limit_window: str
    max_value: int
    model_filter: str | None = None


@dataclass(frozen=True, slots=True)
class ApiKeyCreateData:
    name: str
    allowed_models: list[str] | None
    enforced_model: str | None = None
    enforced_reasoning_effort: str | None = None
    enforced_service_tier: str | None = None
    expires_at: datetime | None = None
    limits: list[LimitRuleInput] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ApiKeyUpdateData:
    name: str | None = None
    name_set: bool = False
    allowed_models: list[str] | None = None
    allowed_models_set: bool = False
    enforced_model: str | None = None
    enforced_model_set: bool = False
    enforced_reasoning_effort: str | None = None
    enforced_reasoning_effort_set: bool = False
    enforced_service_tier: str | None = None
    enforced_service_tier_set: bool = False
    expires_at: datetime | None = None
    expires_at_set: bool = False
    is_active: bool | None = None
    is_active_set: bool = False
    assigned_account_ids: list[str] | None = None
    assigned_account_ids_set: bool = False
    limits: list[LimitRuleInput] | None = None
    limits_set: bool = False
    reset_usage: bool = False


@dataclass(frozen=True, slots=True)
class ApiKeyData:
    id: str
    name: str
    key_prefix: str
    allowed_models: list[str] | None
    enforced_model: str | None
    enforced_reasoning_effort: str | None
    enforced_service_tier: str | None
    expires_at: datetime | None
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None
    limits: list[LimitRuleData] = field(default_factory=list)
    usage_summary: "ApiKeyUsageSummaryData | None" = None
    account_assignment_scope_enabled: bool = False
    assigned_account_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ApiKeyCreatedData(ApiKeyData):
    key: str = ""


@dataclass(frozen=True, slots=True)
class ApiKeyUsageSummaryData:
    request_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float


@dataclass(frozen=True, slots=True)
class ApiKeyUsageReservationData:
    reservation_id: str
    key_id: str
    model: str


class ApiKeysService:
    def __init__(self, repository: ApiKeysRepositoryProtocol) -> None:
        self._repository = repository

    async def create_key(self, payload: ApiKeyCreateData) -> ApiKeyCreatedData:
        now = utcnow()
        expires_at = _normalize_expires_at(payload.expires_at)
        plain_key = _generate_plain_key()
        normalized_allowed_models = _normalize_allowed_models(payload.allowed_models)
        enforced_model = _normalize_model_slug(payload.enforced_model)
        enforced_reasoning_effort = _normalize_reasoning_effort(payload.enforced_reasoning_effort)
        enforced_service_tier = _normalize_service_tier(payload.enforced_service_tier)
        _validate_model_enforcement(enforced_model=enforced_model, allowed_models=normalized_allowed_models)
        row = ApiKey(
            id=str(__import__("uuid").uuid4()),
            name=_normalize_name(payload.name),
            key_hash=_hash_key(plain_key),
            key_prefix=plain_key[:15],
            allowed_models=_serialize_allowed_models(normalized_allowed_models),
            enforced_model=enforced_model,
            enforced_reasoning_effort=enforced_reasoning_effort,
            enforced_service_tier=enforced_service_tier,
            expires_at=expires_at,
            is_active=True,
            created_at=now,
            last_used_at=None,
        )
        created = await self._repository.create(row)

        if payload.limits:
            limit_rows = [_limit_input_to_row(li, created.id, now) for li in payload.limits]
            await self._repository.replace_limits(created.id, limit_rows)
            # Refresh to get updated limits
            created = await self._repository.get_by_id(created.id)
            if created is None:
                raise ValueError("Failed to create API key")

        return _to_created_data(_to_api_key_data(created), plain_key)

    async def list_keys(self) -> list[ApiKeyData]:
        rows = await self._repository.list_all()
        usage_summary_by_key = await self._repository.list_usage_summary_by_key()
        return [
            _to_api_key_data(row, usage_summary=_to_usage_summary_data(usage_summary_by_key.get(row.id)))
            for row in rows
        ]

    async def update_key(self, key_id: str, payload: ApiKeyUpdateData) -> ApiKeyData:
        expires_at = _normalize_expires_at(payload.expires_at) if payload.expires_at_set else None
        existing = await self._repository.get_by_id(key_id)
        if existing is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")

        if payload.allowed_models_set:
            allowed_models = _normalize_allowed_models(payload.allowed_models)
        else:
            allowed_models = None
        if payload.assigned_account_ids_set:
            assigned_account_ids = _normalize_assigned_account_ids(payload.assigned_account_ids)
            existing_accounts = await self._repository.list_accounts_by_ids(assigned_account_ids)
            existing_account_ids = {account.id for account in existing_accounts}
            missing_account_ids = [
                account_id for account_id in assigned_account_ids if account_id not in existing_account_ids
            ]
            if missing_account_ids:
                missing = ", ".join(missing_account_ids)
                raise ValueError(f"Unknown account ids: {missing}")
            account_assignment_scope_enabled: bool | _Unset = bool(assigned_account_ids)
        else:
            assigned_account_ids = None
            account_assignment_scope_enabled = _UNSET

        if payload.enforced_model_set:
            enforced_model = _normalize_model_slug(payload.enforced_model)
        else:
            enforced_model = None

        if payload.enforced_reasoning_effort_set:
            enforced_reasoning_effort = _normalize_reasoning_effort(payload.enforced_reasoning_effort)
        else:
            enforced_reasoning_effort = None

        if payload.enforced_service_tier_set:
            enforced_service_tier = _normalize_service_tier(payload.enforced_service_tier)
        else:
            enforced_service_tier = None

        if payload.allowed_models_set or payload.enforced_model_set:
            effective_allowed_models = (
                allowed_models if payload.allowed_models_set else _deserialize_allowed_models(existing.allowed_models)
            )
            effective_enforced_model = (
                enforced_model if payload.enforced_model_set else _normalize_model_slug(existing.enforced_model)
            )
            _validate_model_enforcement(
                enforced_model=effective_enforced_model,
                allowed_models=effective_allowed_models,
            )

        limit_rows: list[ApiKeyLimit] | None = None
        if payload.limits_set:
            now = utcnow()
            existing_limits = await self._repository.get_limits_by_key(key_id)
            submitted_limits = payload.limits or []
            limit_rows = _build_limit_rows_for_update(
                key_id=key_id,
                now=now,
                submitted_limits=submitted_limits,
                existing_limits=existing_limits,
                reset_usage=payload.reset_usage,
            )
        elif payload.reset_usage:
            now = utcnow()
            existing_limits = await self._repository.get_limits_by_key(key_id)
            limit_rows = _build_reset_limit_rows(key_id=key_id, now=now, existing_limits=existing_limits)

        try:
            row = await self._repository.update(
                key_id,
                name=_normalize_name(payload.name or "") if payload.name_set else _UNSET,
                allowed_models=_serialize_allowed_models(allowed_models) if payload.allowed_models_set else _UNSET,
                enforced_model=enforced_model if payload.enforced_model_set else _UNSET,
                enforced_reasoning_effort=(
                    enforced_reasoning_effort if payload.enforced_reasoning_effort_set else _UNSET
                ),
                enforced_service_tier=(enforced_service_tier if payload.enforced_service_tier_set else _UNSET),
                account_assignment_scope_enabled=account_assignment_scope_enabled,
                expires_at=expires_at if payload.expires_at_set else _UNSET,
                is_active=(payload.is_active if payload.is_active_set and payload.is_active is not None else _UNSET),
                commit=False,
            )
            if row is None:
                raise ApiKeyNotFoundError(f"API key not found: {key_id}")

            if payload.assigned_account_ids_set:
                assert assigned_account_ids is not None
                await self._repository.replace_account_assignments(key_id, assigned_account_ids, commit=False)

            if limit_rows is not None:
                await self._repository.upsert_limits(key_id, limit_rows, commit=False)

            await self._repository.commit()
        except Exception:
            await self._repository.rollback()
            raise

        if (
            payload.assigned_account_ids_set
            or limit_rows is not None
            or payload.name_set
            or payload.allowed_models_set
            or payload.enforced_model_set
            or payload.enforced_reasoning_effort_set
            or payload.enforced_service_tier_set
            or payload.expires_at_set
            or payload.is_active_set
        ):
            row = await self._repository.get_by_id(key_id)
            if row is None:
                raise ApiKeyNotFoundError(f"API key not found: {key_id}")

        await get_api_key_cache().invalidate(row.key_hash)
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)
        return _to_api_key_data(row)

    async def delete_key(self, key_id: str) -> None:
        row = await self._repository.get_by_id(key_id)
        if row is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        deleted = await self._repository.delete(key_id)
        if not deleted:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        await get_api_key_cache().invalidate(row.key_hash)
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)

    async def regenerate_key(self, key_id: str) -> ApiKeyCreatedData:
        row = await self._repository.get_by_id(key_id)
        if row is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        old_key_hash = row.key_hash
        plain_key = _generate_plain_key()
        updated = await self._repository.update(
            key_id,
            key_hash=_hash_key(plain_key),
            key_prefix=plain_key[:15],
        )
        if updated is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        await get_api_key_cache().invalidate(old_key_hash)
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)
        return _to_created_data(_to_api_key_data(updated), plain_key)

    async def validate_key(self, plain_key: str) -> ApiKeyData:
        if not plain_key:
            raise ApiKeyInvalidError("Missing API key in Authorization header")

        key_hash = _hash_key(plain_key)
        now = utcnow()
        row = _ensure_valid_api_key_row(await self._repository.get_by_hash(key_hash))
        if row.expires_at is not None and row.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")
        limits_reset = await _lazy_reset_expired_limits(self._repository, row.limits, now=now)
        refreshed = _ensure_valid_api_key_row(await self._repository.get_by_hash(key_hash)) if limits_reset else row
        if refreshed.expires_at is not None and refreshed.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")
        return _to_api_key_data(refreshed)

    async def get_key_by_id(self, key_id: str) -> ApiKeyData:
        now = utcnow()
        row = _ensure_valid_api_key_row(await self._repository.get_by_id(key_id))
        if row.expires_at is not None and row.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")
        return _to_api_key_data(row)

    async def enforce_limits_for_request(
        self,
        key_id: str,
        *,
        request_model: str | None,
        request_service_tier: str | None = None,
    ) -> ApiKeyUsageReservationData:
        for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
            try:
                return await self._enforce_limits_for_request_once(
                    key_id,
                    request_model=request_model,
                    request_service_tier=request_service_tier,
                )
            except OperationalError as exc:
                if not _is_sqlite_database_locked(exc) or attempt == _SQLITE_BUSY_RETRY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_SQLITE_BUSY_RETRY_BASE_SECONDS * (2**attempt))

        raise RuntimeError("unreachable")

    async def _enforce_limits_for_request_once(
        self,
        key_id: str,
        *,
        request_model: str | None,
        request_service_tier: str | None,
    ) -> ApiKeyUsageReservationData:
        now = utcnow()
        row = _ensure_valid_api_key_row(await self._repository.get_by_id(key_id))
        if row.expires_at is not None and row.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")
        limits_reset = await _lazy_reset_expired_limits(self._repository, row.limits, now=now)
        refreshed = _ensure_valid_api_key_row(await self._repository.get_by_id(key_id)) if limits_reset else row
        if refreshed.expires_at is not None and refreshed.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")

        reservation_items: list[UsageReservationItemData] = []
        try:
            for limit in refreshed.limits:
                if not _limit_applies_for_request(limit, request_model=request_model):
                    continue
                if limit.current_value >= limit.max_value:
                    raise _rate_limit_exceeded_error(limit)
                reserve_delta = _reserve_delta_for_limit(
                    limit,
                    request_model=request_model,
                    request_service_tier=request_service_tier,
                )
                if reserve_delta <= 0:
                    continue
                result = await self._repository.try_reserve_usage(
                    limit.id,
                    delta=reserve_delta,
                    expected_reset_at=limit.reset_at,
                )
                if not result.success:
                    raise _rate_limit_exceeded_error(limit)
                reservation_items.append(
                    UsageReservationItemData(
                        limit_id=limit.id,
                        limit_type=limit.limit_type,
                        reserved_delta=reserve_delta,
                        expected_reset_at=limit.reset_at,
                    )
                )

            reservation_id = _next_usage_reservation_id()
            await self._repository.create_usage_reservation(
                reservation_id,
                key_id=key_id,
                model=request_model or "",
                items=reservation_items,
            )
            await self._repository.commit()
        except Exception:
            await self._repository.rollback()
            raise

        return ApiKeyUsageReservationData(
            reservation_id=reservation_id,
            key_id=key_id,
            model=request_model or "",
        )

    async def finalize_usage_reservation(
        self,
        reservation_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        service_tier: str | None = None,
    ) -> None:
        await self._settle_usage_reservation(
            reservation_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            service_tier=service_tier,
            status="finalized",
        )

    async def fail_usage_reservation(
        self,
        reservation_id: str,
        *,
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        service_tier: str | None = None,
    ) -> None:
        await self._settle_usage_reservation(
            reservation_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            service_tier=service_tier,
            status="failed",
        )

    async def _settle_usage_reservation(
        self,
        reservation_id: str,
        *,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        service_tier: str | None,
        status: str,
    ) -> None:
        reservation = await self._repository.get_usage_reservation(reservation_id)
        if reservation is None or reservation.status != "reserved":
            return

        claimed = await self._repository.transition_usage_reservation_status(
            reservation_id,
            expected_status="reserved",
            new_status="settling",
        )
        if not claimed:
            await self._repository.rollback()
            return

        effective_input_tokens = input_tokens or 0
        effective_output_tokens = output_tokens or 0
        effective_cached_input_tokens = cached_input_tokens or 0
        cost_microdollars = _calculate_cost_microdollars(
            model,
            effective_input_tokens,
            effective_output_tokens,
            effective_cached_input_tokens,
            service_tier,
        )

        try:
            for item in reservation.items:
                actual_delta = _compute_increment_for_limit_type(
                    item.limit_type,
                    input_tokens=effective_input_tokens,
                    output_tokens=effective_output_tokens,
                    cost_microdollars=cost_microdollars,
                )
                delta = actual_delta - item.reserved_delta
                if delta != 0:
                    await self._repository.adjust_reserved_usage(
                        item.limit_id,
                        delta=delta,
                        expected_reset_at=item.expected_reset_at,
                    )
                await self._repository.upsert_reservation_item_actual(
                    reservation_id,
                    item=item,
                    actual_delta=actual_delta,
                )

            await self._repository.settle_usage_reservation(
                reservation_id,
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                cost_microdollars=cost_microdollars,
            )
            await self._repository.update_last_used(reservation.api_key_id, commit=False)
            await self._repository.commit()
        except Exception:
            await self._repository.rollback()
            raise

    async def release_usage_reservation(self, reservation_id: str) -> None:
        reservation = await self._repository.get_usage_reservation(reservation_id)
        if reservation is None or reservation.status != "reserved":
            return

        claimed = await self._repository.transition_usage_reservation_status(
            reservation_id,
            expected_status="reserved",
            new_status="released",
        )
        if not claimed:
            await self._repository.rollback()
            return

        try:
            for item in reservation.items:
                await self._repository.adjust_reserved_usage(
                    item.limit_id,
                    delta=-item.reserved_delta,
                    expected_reset_at=item.expected_reset_at,
                )
                await self._repository.upsert_reservation_item_actual(
                    reservation_id,
                    item=item,
                    actual_delta=0,
                )
            await self._repository.settle_usage_reservation(
                reservation_id,
                status="released",
                input_tokens=None,
                output_tokens=None,
                cached_input_tokens=None,
                cost_microdollars=None,
            )
            await self._repository.commit()
        except Exception:
            await self._repository.rollback()
            raise

    async def record_usage(
        self,
        key_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        service_tier: str | None = None,
    ) -> None:
        cost_microdollars = _calculate_cost_microdollars(
            model,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            service_tier,
        )
        await self._repository.increment_limit_usage(
            key_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_microdollars=cost_microdollars,
        )

    async def get_key_trends(self, key_id: str) -> ApiKeyTrendsData | None:
        row = await self._repository.get_by_id(key_id)
        if row is None:
            return None
        now = utcnow()
        since = now - timedelta(days=_SPARKLINE_DAYS)
        buckets = await self._repository.trends_by_key(
            key_id,
            since,
            now,
            _DETAIL_BUCKET_SECONDS,
        )
        return _build_api_key_trends(key_id, buckets, since, now, _DETAIL_BUCKET_SECONDS)

    async def get_key_usage_summary_for_self(self, key_id: str) -> ApiKeySelfUsageData | None:
        """Return usage summary + current limits for a single key (self-service lookup)."""
        row = await self._repository.get_by_id(key_id)
        if row is None:
            return None

        now = utcnow()
        # Reset any expired limits before reading state
        limits_reset = await _lazy_reset_expired_limits(self._repository, row.limits, now=now)
        refreshed = await self._repository.get_by_id(key_id) if limits_reset else row
        if refreshed is None:
            return None

        usage = await self._repository.get_usage_summary_by_key_id(key_id)
        limits = [
            ApiKeySelfLimitData(
                limit_type=limit.limit_type.value,
                limit_window=limit.limit_window.value,
                max_value=limit.max_value,
                current_value=max(0, min(limit.current_value, limit.max_value)),
                remaining_value=max(0, limit.max_value - max(0, min(limit.current_value, limit.max_value))),
                model_filter=limit.model_filter,
                reset_at=limit.reset_at,
                source="api_key_limit",
            )
            for limit in refreshed.limits
        ]
        return ApiKeySelfUsageData(
            request_count=usage.request_count,
            total_tokens=usage.total_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            total_cost_usd=usage.total_cost_usd,
            limits=limits,
        )

    async def get_key_usage_7d(self, key_id: str) -> ApiKeyUsage7DayData | None:
        row = await self._repository.get_by_id(key_id)
        if row is None:
            return None
        now = utcnow()
        since = now - timedelta(days=7)
        data = await self._repository.usage_7d(key_id, since, now)
        return ApiKeyUsage7DayData(
            key_id=key_id,
            total_tokens=data.total_tokens,
            total_cost_usd=data.total_cost_usd,
            total_requests=data.total_requests,
            cached_input_tokens=data.cached_input_tokens,
        )


@dataclass(frozen=True, slots=True)
class ApiKeyTrendsPoint:
    t: datetime
    v: float


@dataclass(frozen=True, slots=True)
class ApiKeyTrendsData:
    key_id: str
    cost: list[ApiKeyTrendsPoint] = field(default_factory=list)
    tokens: list[ApiKeyTrendsPoint] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ApiKeyUsage7DayData:
    key_id: str
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_requests: int = 0
    cached_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ApiKeySelfLimitData:
    limit_type: str
    limit_window: str
    max_value: int
    current_value: int
    remaining_value: int
    model_filter: str | None
    reset_at: datetime
    source: str = "api_key_limit"


@dataclass(frozen=True, slots=True)
class ApiKeySelfUsageData:
    request_count: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    total_cost_usd: float = 0.0
    limits: list[ApiKeySelfLimitData] = field(default_factory=list)


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("API key name is required")
    return normalized


def _generate_plain_key() -> str:
    return f"sk-clb-{secrets.token_urlsafe(32)}"


def _hash_key(plain_key: str) -> str:
    return sha256(plain_key.encode("utf-8")).hexdigest()


def _serialize_allowed_models(allowed_models: list[str] | None) -> str | None:
    if allowed_models is None:
        return None
    return json.dumps(allowed_models)


def _deserialize_allowed_models(payload: str | None) -> list[str] | None:
    if payload is None:
        return None
    parsed = json.loads(payload)
    if not isinstance(parsed, list):
        return None
    models = [str(value).strip() for value in parsed if str(value).strip()]
    return models


def _normalize_allowed_models(allowed_models: list[str] | None) -> list[str] | None:
    if allowed_models is None:
        return None
    return [model.strip() for model in allowed_models if model and model.strip()]


def _normalize_assigned_account_ids(account_ids: list[str] | None) -> list[str]:
    if not account_ids:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for account_id in account_ids:
        value = account_id.strip()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def _normalize_model_slug(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


_SUPPORTED_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})
_SUPPORTED_SERVICE_TIERS = frozenset({"auto", "default", "priority", "flex"})


def _normalize_expires_at(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return to_utc_naive(value)


def _normalize_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in _SUPPORTED_REASONING_EFFORTS:
        options = ", ".join(sorted(_SUPPORTED_REASONING_EFFORTS))
        raise ValueError(f"Unsupported enforced reasoning effort '{normalized}'. Expected one of: {options}")
    return normalized


def _normalize_reasoning_effort_lenient(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in _SUPPORTED_REASONING_EFFORTS:
        return normalized
    return None


def _normalize_service_tier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized == "fast":
        normalized = "priority"
    if normalized not in _SUPPORTED_SERVICE_TIERS:
        options = ", ".join(sorted(_SUPPORTED_SERVICE_TIERS | {"fast"}))
        raise ValueError(f"Unsupported enforced service tier '{normalized}'. Expected one of: {options}")
    return normalized


def _normalize_service_tier_lenient(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized == "fast":
        return "priority"
    if normalized in _SUPPORTED_SERVICE_TIERS:
        return normalized
    return None


def _validate_model_enforcement(*, enforced_model: str | None, allowed_models: list[str] | None) -> None:
    if enforced_model is None or not allowed_models:
        return
    if enforced_model not in allowed_models:
        raise ValueError("enforced_model must be present in allowed_models when allowed_models is configured")


def _to_limit_rule_data(limit: ApiKeyLimit) -> LimitRuleData:
    return LimitRuleData(
        id=limit.id,
        limit_type=limit.limit_type.value,
        limit_window=limit.limit_window.value,
        max_value=limit.max_value,
        current_value=limit.current_value,
        model_filter=limit.model_filter,
        reset_at=limit.reset_at,
    )


def _ensure_valid_api_key_row(row: ApiKey | None) -> ApiKey:
    if row is None or not row.is_active:
        raise ApiKeyInvalidError("Invalid API key")
    return row


async def _lazy_reset_expired_limits(
    repository: ApiKeysRepositoryProtocol,
    limits: list[ApiKeyLimit],
    *,
    now: datetime,
) -> bool:
    reset_performed = False
    for limit in limits:
        if limit.reset_at >= now:
            continue
        new_reset_at = advance_limit_reset(limit.reset_at, now, limit.limit_window)
        await repository.reset_limit(
            limit.id,
            expected_reset_at=limit.reset_at,
            new_reset_at=new_reset_at,
        )
        reset_performed = True
    return reset_performed


def _rate_limit_exceeded_error(limit: ApiKeyLimit) -> ApiKeyRateLimitExceededError:
    return ApiKeyRateLimitExceededError(
        message=f"API key {limit.limit_type.value} {limit.limit_window.value} limit exceeded"
        + (f" for model {limit.model_filter}" if limit.model_filter else ""),
        reset_at=limit.reset_at,
    )


def _limit_applies_for_request(limit: ApiKeyLimit, *, request_model: str | None) -> bool:
    if limit.model_filter is None:
        return True
    if request_model is None:
        return False
    return limit.model_filter == request_model


def _reserve_delta_for_limit(
    limit: ApiKeyLimit,
    *,
    request_model: str | None,
    request_service_tier: str | None,
) -> int:
    remaining = limit.max_value - limit.current_value
    if remaining <= 0:
        return 0
    budget = _reserve_budget_for_limit_type(
        limit.limit_type,
        request_model=request_model,
        request_service_tier=request_service_tier,
    )
    return min(remaining, budget)


def _reserve_budget_for_limit_type(
    limit_type: LimitType,
    *,
    request_model: str | None,
    request_service_tier: str | None,
) -> int:
    if limit_type == LimitType.TOTAL_TOKENS:
        return 8_192
    if limit_type == LimitType.INPUT_TOKENS:
        return 8_192
    if limit_type == LimitType.OUTPUT_TOKENS:
        return 8_192
    if limit_type == LimitType.COST_USD:
        return _reserve_cost_budget_microdollars(request_model, request_service_tier)
    if limit_type == LimitType.CREDITS:
        return 0
    return 1


def _reserve_cost_budget_microdollars(model: str | None, service_tier: str | None) -> int:
    if not model:
        return 2_000_000
    cost_microdollars = _calculate_cost_microdollars(
        model,
        8_192,
        8_192,
        0,
        service_tier,
    )
    return cost_microdollars if cost_microdollars > 0 else 2_000_000


def _compute_increment_for_limit_type(
    limit_type: LimitType,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_microdollars: int,
) -> int:
    if limit_type == LimitType.TOTAL_TOKENS:
        return input_tokens + output_tokens
    if limit_type == LimitType.INPUT_TOKENS:
        return input_tokens
    if limit_type == LimitType.OUTPUT_TOKENS:
        return output_tokens
    if limit_type == LimitType.COST_USD:
        return cost_microdollars
    if limit_type == LimitType.CREDITS:
        return 0
    return 0


def _next_usage_reservation_id() -> str:
    return f"ur_{uuid.uuid4().hex}"


def _to_created_data(data: ApiKeyData, key: str) -> ApiKeyCreatedData:
    return ApiKeyCreatedData(
        id=data.id,
        name=data.name,
        key_prefix=data.key_prefix,
        allowed_models=data.allowed_models,
        enforced_model=data.enforced_model,
        enforced_reasoning_effort=data.enforced_reasoning_effort,
        enforced_service_tier=data.enforced_service_tier,
        expires_at=data.expires_at,
        is_active=data.is_active,
        created_at=data.created_at,
        last_used_at=data.last_used_at,
        limits=data.limits,
        usage_summary=data.usage_summary,
        account_assignment_scope_enabled=data.account_assignment_scope_enabled,
        assigned_account_ids=data.assigned_account_ids,
        key=key,
    )


def _to_api_key_data(row: ApiKey, *, usage_summary: ApiKeyUsageSummaryData | None = None) -> ApiKeyData:
    limits = [_to_limit_rule_data(limit) for limit in row.limits] if row.limits else []
    account_assignments = getattr(row, "account_assignments", [])
    return ApiKeyData(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        allowed_models=_deserialize_allowed_models(row.allowed_models),
        enforced_model=_normalize_model_slug(row.enforced_model),
        enforced_reasoning_effort=_normalize_reasoning_effort_lenient(row.enforced_reasoning_effort),
        enforced_service_tier=_normalize_service_tier_lenient(row.enforced_service_tier),
        expires_at=row.expires_at,
        is_active=row.is_active,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        limits=limits,
        usage_summary=usage_summary,
        account_assignment_scope_enabled=getattr(row, "account_assignment_scope_enabled", False),
        assigned_account_ids=[assignment.account_id for assignment in account_assignments],
    )


def _to_usage_summary_data(summary: ApiKeyUsageSummary | None) -> ApiKeyUsageSummaryData | None:
    if summary is None:
        return None
    return ApiKeyUsageSummaryData(
        request_count=summary.request_count,
        total_tokens=summary.total_tokens,
        cached_input_tokens=summary.cached_input_tokens,
        total_cost_usd=summary.total_cost_usd,
    )


def _limit_input_to_row(
    li: LimitRuleInput,
    key_id: str,
    now: datetime,
    *,
    current_value: int = 0,
    reset_at: datetime | None = None,
) -> ApiKeyLimit:
    window = LimitWindow(li.limit_window)
    if li.limit_type == LimitType.CREDITS.value and li.model_filter is not None:
        raise ValueError("credits limits do not support model_filter")
    return ApiKeyLimit(
        api_key_id=key_id,
        limit_type=LimitType(li.limit_type),
        limit_window=window,
        max_value=li.max_value,
        current_value=current_value,
        model_filter=li.model_filter,
        reset_at=reset_at if reset_at is not None else next_limit_reset(now, window),
    )


def _build_limit_rows_for_update(
    *,
    key_id: str,
    now: datetime,
    submitted_limits: list[LimitRuleInput],
    existing_limits: list[ApiKeyLimit],
    reset_usage: bool,
) -> list[ApiKeyLimit]:
    existing_by_key = {_limit_identity_from_row(limit): limit for limit in existing_limits}
    submitted_by_key = {_limit_identity_from_input(limit): limit for limit in submitted_limits}
    if len(submitted_by_key) != len(submitted_limits):
        raise ValueError("Duplicate limit rules are not allowed")

    rows: list[ApiKeyLimit] = []
    for submitted in submitted_limits:
        identity = _limit_identity_from_input(submitted)
        matched = existing_by_key.get(identity)
        if matched is None or reset_usage:
            rows.append(_limit_input_to_row(submitted, key_id, now))
            continue
        rows.append(
            _limit_input_to_row(
                submitted,
                key_id,
                now,
                current_value=matched.current_value,
                reset_at=matched.reset_at,
            )
        )
    return rows


def _build_reset_limit_rows(
    *,
    key_id: str,
    now: datetime,
    existing_limits: list[ApiKeyLimit],
) -> list[ApiKeyLimit]:
    rows: list[ApiKeyLimit] = []
    for existing in existing_limits:
        rows.append(
            ApiKeyLimit(
                api_key_id=key_id,
                limit_type=existing.limit_type,
                limit_window=existing.limit_window,
                max_value=existing.max_value,
                current_value=0,
                model_filter=existing.model_filter,
                reset_at=next_limit_reset(now, existing.limit_window),
            )
        )
    return rows


def _limit_identity_from_input(limit: LimitRuleInput) -> tuple[str, str, str | None]:
    return (limit.limit_type, limit.limit_window, limit.model_filter)


def _limit_identity_from_row(limit: ApiKeyLimit) -> tuple[str, str, str | None]:
    return (limit.limit_type.value, limit.limit_window.value, limit.model_filter)


def _calculate_cost_microdollars(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    service_tier: str | None = None,
) -> int:
    resolved = get_pricing_for_model(model)
    if resolved is None:
        return 0
    _, price = resolved
    usage = UsageTokens(
        input_tokens=float(input_tokens),
        output_tokens=float(output_tokens),
        cached_input_tokens=float(cached_input_tokens),
    )
    cost_usd = calculate_cost_from_usage(usage, price, service_tier=service_tier)
    if cost_usd is None:
        return 0
    return int(cost_usd * 1_000_000)


def _is_sqlite_database_locked(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


def _build_api_key_trends(
    key_id: str,
    buckets: list[ApiKeyTrendBucket],
    since: datetime,
    until: datetime,
    bucket_seconds: int,
) -> ApiKeyTrendsData:
    since_utc = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since.astimezone(timezone.utc)
    until_utc = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until.astimezone(timezone.utc)
    if until_utc <= since_utc:
        return ApiKeyTrendsData(key_id=key_id)

    start_epoch = (int(since_utc.timestamp()) // bucket_seconds) * bucket_seconds
    # The SQL window is exclusive of `until`, so step back one microsecond to find the last visible bucket.
    end_epoch = (int((until_utc - timedelta(microseconds=1)).timestamp()) // bucket_seconds) * bucket_seconds
    bucket_count = ((end_epoch - start_epoch) // bucket_seconds) + 1
    time_grid = [start_epoch + i * bucket_seconds for i in range(bucket_count)]

    cost_by_bucket: dict[int, float] = {}
    tokens_by_bucket: dict[int, int] = {}
    for b in buckets:
        cost_by_bucket[b.bucket_epoch] = b.total_cost_usd
        tokens_by_bucket[b.bucket_epoch] = b.total_tokens

    cost_points: list[ApiKeyTrendsPoint] = []
    tokens_points: list[ApiKeyTrendsPoint] = []
    for epoch in time_grid:
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        cost_points.append(ApiKeyTrendsPoint(t=dt, v=round(cost_by_bucket.get(epoch, 0.0), 6)))
        tokens_points.append(ApiKeyTrendsPoint(t=dt, v=float(tokens_by_bucket.get(epoch, 0))))

    return ApiKeyTrendsData(key_id=key_id, cost=cost_points, tokens=tokens_points)
