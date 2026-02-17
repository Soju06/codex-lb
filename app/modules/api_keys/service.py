from __future__ import annotations

import json
import secrets
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


class ApiKeysRepositoryProtocol(Protocol):
    async def create(self, row: ApiKey) -> ApiKey: ...

    async def get_by_id(self, key_id: str) -> ApiKey | None: ...

    async def get_by_hash(self, key_hash: str) -> ApiKey | None: ...

    async def list_all(self) -> list[ApiKey]: ...

    async def update(
        self,
        key_id: str,
        *,
        name: str | object = ...,
        allowed_models: str | None | object = ...,
        expires_at: datetime | None | object = ...,
        is_active: bool | object = ...,
        key_hash: str | object = ...,
        key_prefix: str | object = ...,
    ) -> ApiKey | None: ...

    async def delete(self, key_id: str) -> bool: ...

    async def update_last_used(self, key_id: str) -> None: ...

    async def get_limits_by_key(self, key_id: str) -> list[ApiKeyLimit]: ...

    async def replace_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]: ...

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
        updates: dict[str, object] = {}
        if payload.name_set:
            updates["name"] = _normalize_name(payload.name or "")
        if payload.allowed_models_set:
            updates["allowed_models"] = _serialize_allowed_models(payload.allowed_models)
        if payload.expires_at_set:
            updates["expires_at"] = payload.expires_at
        if payload.is_active_set and payload.is_active is not None:
            updates["is_active"] = payload.is_active
        row = await self._repository.update(key_id, **updates)
        if row is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")

        if payload.limits_set and payload.limits is not None:
            now = utcnow()
            limit_rows = [_limit_input_to_row(li, key_id, now) for li in payload.limits]
            await self._repository.replace_limits(key_id, limit_rows)
            # Refresh to get updated limits
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

        row = await self._repository.get_by_hash(_hash_key(plain_key))
        if row is None or not row.is_active:
            raise ApiKeyInvalidError("Invalid API key")

        now = utcnow()
        if row.expires_at is not None and row.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")

        # Lazy reset expired limits
        limits = row.limits
        for limit in limits:
            if limit.reset_at < now:
                new_reset_at = _advance_reset(limit.reset_at, now, limit.limit_window)
                await self._repository.reset_limit(
                    limit.id,
                    expected_reset_at=limit.reset_at,
                    new_reset_at=new_reset_at,
                )

        # Re-fetch to get updated limit values after resets
        row = await self._repository.get_by_hash(_hash_key(plain_key))
        if row is None:
            raise ApiKeyInvalidError("Invalid API key")

        # Check all limits
        for limit in row.limits:
            if limit.current_value >= limit.max_value:
                raise ApiKeyRateLimitExceededError(
                    message=f"API key {limit.limit_type.value} {limit.limit_window.value} limit exceeded"
                    + (f" for model {limit.model_filter}" if limit.model_filter else ""),
                    reset_at=limit.reset_at,
                )

        return _to_api_key_data(row)

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
            model, input_tokens, output_tokens, cached_input_tokens,
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


def _limit_input_to_row(li: LimitRuleInput, key_id: str, now: datetime) -> ApiKeyLimit:
    window = LimitWindow(li.limit_window)
    return ApiKeyLimit(
        api_key_id=key_id,
        limit_type=LimitType(li.limit_type),
        limit_window=window,
        max_value=li.max_value,
        current_value=0,
        model_filter=li.model_filter,
        reset_at=_next_reset(now, window),
    )


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
