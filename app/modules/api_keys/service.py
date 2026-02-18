from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Protocol

from app.core.usage.pricing import (
    UsageTokens,
    calculate_cost_from_usage,
    get_pricing_for_model,
)
from app.core.utils.time import utcnow
from app.db.models import ApiKey, ApiKeyLimit, LimitType, LimitWindow
from app.modules.api_keys.repository import (
    _UNSET,
    ReservationResult,
    UsageReservationData,
    UsageReservationItemData,
    _Unset,
)


class ApiKeysRepositoryProtocol(Protocol):
    async def create(self, row: ApiKey) -> ApiKey: ...

    async def get_by_id(self, key_id: str) -> ApiKey | None: ...

    async def get_by_hash(self, key_hash: str) -> ApiKey | None: ...

    async def list_all(self) -> list[ApiKey]: ...

    async def update(
        self,
        key_id: str,
        *,
        name: str | _Unset = ...,
        allowed_models: str | None | _Unset = ...,
        expires_at: datetime | None | _Unset = ...,
        is_active: bool | _Unset = ...,
        key_hash: str | _Unset = ...,
        key_prefix: str | _Unset = ...,
    ) -> ApiKey | None: ...

    async def delete(self, key_id: str) -> bool: ...

    async def update_last_used(self, key_id: str) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def get_limits_by_key(self, key_id: str) -> list[ApiKeyLimit]: ...

    async def replace_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]: ...

    async def upsert_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]: ...

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
    expires_at: datetime | None
    limits: list[LimitRuleInput] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ApiKeyUpdateData:
    name: str | None = None
    name_set: bool = False
    allowed_models: list[str] | None = None
    allowed_models_set: bool = False
    expires_at: datetime | None = None
    expires_at_set: bool = False
    is_active: bool | None = None
    is_active_set: bool = False
    limits: list[LimitRuleInput] | None = None
    limits_set: bool = False
    reset_usage: bool = False


@dataclass(frozen=True, slots=True)
class ApiKeyData:
    id: str
    name: str
    key_prefix: str
    allowed_models: list[str] | None
    expires_at: datetime | None
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None
    limits: list[LimitRuleData] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ApiKeyCreatedData(ApiKeyData):
    key: str = ""


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
        plain_key = _generate_plain_key()
        row = ApiKey(
            id=str(__import__("uuid").uuid4()),
            name=_normalize_name(payload.name),
            key_hash=_hash_key(plain_key),
            key_prefix=plain_key[:15],
            allowed_models=_serialize_allowed_models(payload.allowed_models),
            expires_at=payload.expires_at,
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
        return [_to_api_key_data(row) for row in rows]

    async def update_key(self, key_id: str, payload: ApiKeyUpdateData) -> ApiKeyData:
        row = await self._repository.update(
            key_id,
            name=_normalize_name(payload.name or "") if payload.name_set else _UNSET,
            allowed_models=_serialize_allowed_models(payload.allowed_models) if payload.allowed_models_set else _UNSET,
            expires_at=payload.expires_at if payload.expires_at_set else _UNSET,
            is_active=(payload.is_active if payload.is_active_set and payload.is_active is not None else _UNSET),
        )
        if row is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")

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
            await self._repository.upsert_limits(key_id, limit_rows)
        elif payload.reset_usage:
            now = utcnow()
            existing_limits = await self._repository.get_limits_by_key(key_id)
            limit_rows = _build_reset_limit_rows(key_id=key_id, now=now, existing_limits=existing_limits)
            await self._repository.upsert_limits(key_id, limit_rows)

        if payload.limits_set or payload.reset_usage:
            row = await self._repository.get_by_id(key_id)
            if row is None:
                raise ApiKeyNotFoundError(f"API key not found: {key_id}")

        return _to_api_key_data(row)

    async def delete_key(self, key_id: str) -> None:
        deleted = await self._repository.delete(key_id)
        if not deleted:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")

    async def regenerate_key(self, key_id: str) -> ApiKeyCreatedData:
        row = await self._repository.get_by_id(key_id)
        if row is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        plain_key = _generate_plain_key()
        updated = await self._repository.update(
            key_id,
            key_hash=_hash_key(plain_key),
            key_prefix=plain_key[:15],
        )
        if updated is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        return _to_created_data(_to_api_key_data(updated), plain_key)

    async def validate_key(self, plain_key: str) -> ApiKeyData:
        if not plain_key:
            raise ApiKeyInvalidError("Missing API key in Authorization header")

        key_hash = _hash_key(plain_key)
        now = utcnow()
        row = _ensure_valid_api_key_row(await self._repository.get_by_hash(key_hash))
        if row.expires_at is not None and row.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")
        await _lazy_reset_expired_limits(self._repository, row.limits, now=now)
        refreshed = _ensure_valid_api_key_row(await self._repository.get_by_hash(key_hash))
        if refreshed.expires_at is not None and refreshed.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")
        return _to_api_key_data(refreshed)

    async def enforce_limits_for_request(
        self,
        key_id: str,
        *,
        request_model: str | None,
    ) -> ApiKeyUsageReservationData:
        now = utcnow()
        row = _ensure_valid_api_key_row(await self._repository.get_by_id(key_id))
        if row.expires_at is not None and row.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")
        await _lazy_reset_expired_limits(self._repository, row.limits, now=now)
        refreshed = _ensure_valid_api_key_row(await self._repository.get_by_id(key_id))
        if refreshed.expires_at is not None and refreshed.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")

        reservation_items: list[UsageReservationItemData] = []
        try:
            for limit in refreshed.limits:
                if not _limit_applies_for_request(limit, request_model=request_model):
                    continue
                if limit.current_value >= limit.max_value:
                    raise _rate_limit_exceeded_error(limit)
                reserve_delta = _reserve_delta_for_limit(limit)
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

        cost_microdollars = _calculate_cost_microdollars(
            model,
            input_tokens,
            output_tokens,
            cached_input_tokens,
        )

        try:
            for item in reservation.items:
                actual_delta = _compute_increment_for_limit_type(
                    item.limit_type,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
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
                status="finalized",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                cost_microdollars=cost_microdollars,
            )
            await self._repository.commit()
        except Exception:
            await self._repository.rollback()
            raise

        await self._repository.update_last_used(reservation.api_key_id)

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
    ) -> None:
        cost_microdollars = _calculate_cost_microdollars(
            model,
            input_tokens,
            output_tokens,
            cached_input_tokens,
        )
        await self._repository.increment_limit_usage(
            key_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_microdollars=cost_microdollars,
        )


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
    normalized = [model.strip() for model in allowed_models if model and model.strip()]
    return json.dumps(normalized)


def _deserialize_allowed_models(payload: str | None) -> list[str] | None:
    if payload is None:
        return None
    parsed = json.loads(payload)
    if not isinstance(parsed, list):
        return None
    models = [str(value).strip() for value in parsed if str(value).strip()]
    return models


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
) -> None:
    for limit in limits:
        if limit.reset_at >= now:
            continue
        new_reset_at = _advance_reset(limit.reset_at, now, limit.limit_window)
        await repository.reset_limit(
            limit.id,
            expected_reset_at=limit.reset_at,
            new_reset_at=new_reset_at,
        )


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


def _reserve_delta_for_limit(limit: ApiKeyLimit) -> int:
    remaining = limit.max_value - limit.current_value
    if remaining <= 0:
        return 0
    budget = _reserve_budget_for_limit_type(limit.limit_type)
    return min(remaining, budget)


def _reserve_budget_for_limit_type(limit_type: LimitType) -> int:
    if limit_type == LimitType.TOTAL_TOKENS:
        return 8_192
    if limit_type == LimitType.INPUT_TOKENS:
        return 8_192
    if limit_type == LimitType.OUTPUT_TOKENS:
        return 8_192
    if limit_type == LimitType.COST_USD:
        return 2_000_000
    return 1


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
    return 0


def _next_usage_reservation_id() -> str:
    return f"ur_{uuid.uuid4().hex}"


def _to_created_data(data: ApiKeyData, key: str) -> ApiKeyCreatedData:
    return ApiKeyCreatedData(
        id=data.id,
        name=data.name,
        key_prefix=data.key_prefix,
        allowed_models=data.allowed_models,
        expires_at=data.expires_at,
        is_active=data.is_active,
        created_at=data.created_at,
        last_used_at=data.last_used_at,
        limits=data.limits,
        key=key,
    )


def _to_api_key_data(row: ApiKey) -> ApiKeyData:
    limits = [_to_limit_rule_data(limit) for limit in row.limits] if row.limits else []
    return ApiKeyData(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        allowed_models=_deserialize_allowed_models(row.allowed_models),
        expires_at=row.expires_at,
        is_active=row.is_active,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        limits=limits,
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
    return ApiKeyLimit(
        api_key_id=key_id,
        limit_type=LimitType(li.limit_type),
        limit_window=window,
        max_value=li.max_value,
        current_value=current_value,
        model_filter=li.model_filter,
        reset_at=reset_at if reset_at is not None else _next_reset(now, window),
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
                reset_at=_next_reset(now, existing.limit_window),
            )
        )
    return rows


def _limit_identity_from_input(limit: LimitRuleInput) -> tuple[str, str, str | None]:
    return (limit.limit_type, limit.limit_window, limit.model_filter)


def _limit_identity_from_row(limit: ApiKeyLimit) -> tuple[str, str, str | None]:
    return (limit.limit_type.value, limit.limit_window.value, limit.model_filter)


def _next_reset(now: datetime, window: LimitWindow) -> datetime:
    if window == LimitWindow.DAILY:
        return now + timedelta(days=1)
    if window == LimitWindow.WEEKLY:
        return now + timedelta(days=7)
    if window == LimitWindow.MONTHLY:
        return now + timedelta(days=30)
    return now + timedelta(days=7)


def _advance_reset(reset_at: datetime, now: datetime, window: LimitWindow) -> datetime:
    delta = _window_delta(window)
    next_reset = reset_at
    while next_reset <= now:
        next_reset += delta
    return next_reset


def _window_delta(window: LimitWindow) -> timedelta:
    if window == LimitWindow.DAILY:
        return timedelta(days=1)
    if window == LimitWindow.WEEKLY:
        return timedelta(days=7)
    if window == LimitWindow.MONTHLY:
        return timedelta(days=30)
    return timedelta(days=7)


def _calculate_cost_microdollars(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
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
    cost_usd = calculate_cost_from_usage(usage, price)
    if cost_usd is None:
        return 0
    return int(cost_usd * 1_000_000)
