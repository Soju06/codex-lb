from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from app.core.utils.time import utcnow
from app.db.models import ApiKey


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
        weekly_token_limit: int | None | object = ...,
        expires_at: datetime | None | object = ...,
        is_active: bool | object = ...,
        key_hash: str | object = ...,
        key_prefix: str | object = ...,
    ) -> ApiKey | None: ...

    async def delete(self, key_id: str) -> bool: ...

    async def increment_weekly_usage(self, key_id: str, token_count: int) -> None: ...

    async def reset_weekly_usage(self, key_id: str, *, expected_reset_at: datetime, new_reset_at: datetime) -> bool: ...


class ApiKeyNotFoundError(ValueError):
    pass


class ApiKeyInvalidError(ValueError):
    pass


class ApiKeyRateLimitExceededError(ValueError):
    def __init__(self, *, weekly_reset_at: datetime) -> None:
        super().__init__("API key weekly token limit exceeded")
        self.weekly_reset_at = weekly_reset_at


@dataclass(frozen=True, slots=True)
class ApiKeyCreateData:
    name: str
    allowed_models: list[str] | None
    weekly_token_limit: int | None
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class ApiKeyUpdateData:
    name: str | None = None
    name_set: bool = False
    allowed_models: list[str] | None = None
    allowed_models_set: bool = False
    weekly_token_limit: int | None = None
    weekly_token_limit_set: bool = False
    expires_at: datetime | None = None
    expires_at_set: bool = False
    is_active: bool | None = None
    is_active_set: bool = False


@dataclass(frozen=True, slots=True)
class ApiKeyData:
    id: str
    name: str
    key_prefix: str
    allowed_models: list[str] | None
    weekly_token_limit: int | None
    weekly_tokens_used: int
    weekly_reset_at: datetime
    expires_at: datetime | None
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class ApiKeyCreatedData(ApiKeyData):
    key: str


class ApiKeysService:
    def __init__(self, repository: ApiKeysRepositoryProtocol) -> None:
        self._repository = repository

    async def create_key(self, payload: ApiKeyCreateData) -> ApiKeyCreatedData:
        now = utcnow()
        plain_key = _generate_plain_key()
        row = ApiKey(
            id=str(uuid4()),
            name=_normalize_name(payload.name),
            key_hash=_hash_key(plain_key),
            key_prefix=plain_key[:15],
            allowed_models=_serialize_allowed_models(payload.allowed_models),
            weekly_token_limit=payload.weekly_token_limit,
            weekly_tokens_used=0,
            weekly_reset_at=now + timedelta(days=7),
            expires_at=payload.expires_at,
            is_active=True,
            created_at=now,
            last_used_at=None,
        )
        created = await self._repository.create(row)
        return ApiKeyCreatedData(
            **asdict(_to_api_key_data(created)),
            key=plain_key,
        )

    async def list_keys(self) -> list[ApiKeyData]:
        rows = await self._repository.list_all()
        return [_to_api_key_data(row) for row in rows]

    async def update_key(self, key_id: str, payload: ApiKeyUpdateData) -> ApiKeyData:
        updates: dict[str, object] = {}
        if payload.name_set:
            updates["name"] = _normalize_name(payload.name or "")
        if payload.allowed_models_set:
            updates["allowed_models"] = _serialize_allowed_models(payload.allowed_models)
        if payload.weekly_token_limit_set:
            updates["weekly_token_limit"] = payload.weekly_token_limit
        if payload.expires_at_set:
            updates["expires_at"] = payload.expires_at
        if payload.is_active_set and payload.is_active is not None:
            updates["is_active"] = payload.is_active
        row = await self._repository.update(key_id, **updates)
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
        return ApiKeyCreatedData(
            **asdict(_to_api_key_data(updated)),
            key=plain_key,
        )

    async def validate_key(self, plain_key: str) -> ApiKeyData:
        if not plain_key:
            raise ApiKeyInvalidError("Missing API key in Authorization header")

        row = await self._repository.get_by_hash(_hash_key(plain_key))
        if row is None or not row.is_active:
            raise ApiKeyInvalidError("Invalid API key")

        now = utcnow()
        if row.expires_at is not None and row.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")

        if row.weekly_reset_at < now:
            new_reset_at = _advance_weekly_reset(row.weekly_reset_at, now)
            reset_ok = await self._repository.reset_weekly_usage(
                row.id,
                expected_reset_at=row.weekly_reset_at,
                new_reset_at=new_reset_at,
            )
            if reset_ok:
                row.weekly_tokens_used = 0
                row.weekly_reset_at = new_reset_at
            else:
                refreshed = await self._repository.get_by_id(row.id)
                if refreshed is None:
                    raise ApiKeyInvalidError("Invalid API key")
                row = refreshed

        if row.weekly_token_limit is not None and row.weekly_tokens_used >= row.weekly_token_limit:
            raise ApiKeyRateLimitExceededError(weekly_reset_at=row.weekly_reset_at)

        return _to_api_key_data(row)


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("API key name is required")
    return normalized


def _generate_plain_key() -> str:
    return f"sk-clb-{secrets.token_hex(24)}"


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


def _to_api_key_data(row: ApiKey) -> ApiKeyData:
    return ApiKeyData(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        allowed_models=_deserialize_allowed_models(row.allowed_models),
        weekly_token_limit=row.weekly_token_limit,
        weekly_tokens_used=row.weekly_tokens_used,
        weekly_reset_at=row.weekly_reset_at,
        expires_at=row.expires_at,
        is_active=row.is_active,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


def _advance_weekly_reset(weekly_reset_at: datetime, now: datetime) -> datetime:
    next_reset = weekly_reset_at
    while next_reset <= now:
        next_reset += timedelta(days=7)
    return next_reset
