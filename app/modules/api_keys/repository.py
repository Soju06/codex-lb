from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import ApiKey, ApiKeyLimit

_UNSET = object()


class ApiKeysRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, row: ApiKey) -> ApiKey:
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        return await self._session.get(ApiKey, key_id)

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        result = await self._session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[ApiKey]:
        result = await self._session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
        return list(result.scalars().unique().all())

    async def update(
        self,
        key_id: str,
        *,
        name: str | object = _UNSET,
        allowed_models: str | None | object = _UNSET,
        expires_at: datetime | None | object = _UNSET,
        is_active: bool | object = _UNSET,
        key_hash: str | object = _UNSET,
        key_prefix: str | object = _UNSET,
    ) -> ApiKey | None:
        row = await self.get_by_id(key_id)
        if row is None:
            return None
        if name is not _UNSET:
            assert isinstance(name, str)
            row.name = name
        if allowed_models is not _UNSET:
            assert allowed_models is None or isinstance(allowed_models, str)
            row.allowed_models = allowed_models
        if expires_at is not _UNSET:
            assert expires_at is None or isinstance(expires_at, datetime)
            row.expires_at = expires_at
        if is_active is not _UNSET:
            assert isinstance(is_active, bool)
            row.is_active = is_active
        if key_hash is not _UNSET:
            assert isinstance(key_hash, str)
            row.key_hash = key_hash
        if key_prefix is not _UNSET:
            assert isinstance(key_prefix, str)
            row.key_prefix = key_prefix
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, key_id: str) -> bool:
        row = await self.get_by_id(key_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def update_last_used(self, key_id: str) -> None:
        await self._session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=utcnow()))
        await self._session.commit()

    # ── Limit operations ──

    async def get_limits_by_key(self, key_id: str) -> list[ApiKeyLimit]:
        result = await self._session.execute(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key_id))
        return list(result.scalars().all())

    async def replace_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]:
        existing = await self.get_limits_by_key(key_id)
        for limit in existing:
            await self._session.delete(limit)
        for limit in limits:
            limit.api_key_id = key_id
            self._session.add(limit)
        await self._session.commit()
        # Refresh parent so the selectin relationship is reloaded
        parent = await self._session.get(ApiKey, key_id)
        if parent is not None:
            await self._session.refresh(parent, attribute_names=["limits"])
        return await self.get_limits_by_key(key_id)

    async def increment_limit_usage(
        self,
        key_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_microdollars: int,
    ) -> None:
        limits = await self.get_limits_by_key(key_id)
        for limit in limits:
            if limit.model_filter is not None and limit.model_filter != model:
                continue
            increment = _compute_increment(limit, input_tokens, output_tokens, cost_microdollars)
            if increment > 0:
                await self._session.execute(
                    update(ApiKeyLimit)
                    .where(ApiKeyLimit.id == limit.id)
                    .values(current_value=ApiKeyLimit.current_value + increment)
                )
        await self._session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=utcnow()))
        await self._session.commit()

    async def reset_limit(self, limit_id: int, *, expected_reset_at: datetime, new_reset_at: datetime) -> bool:
        result = await self._session.execute(
            update(ApiKeyLimit)
            .where(ApiKeyLimit.id == limit_id)
            .where(ApiKeyLimit.reset_at == expected_reset_at)
            .values(current_value=0, reset_at=new_reset_at)
            .returning(ApiKeyLimit.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None


def _compute_increment(limit: ApiKeyLimit, input_tokens: int, output_tokens: int, cost_microdollars: int) -> int:
    from app.db.models import LimitType

    if limit.limit_type == LimitType.TOTAL_TOKENS:
        return input_tokens + output_tokens
    if limit.limit_type == LimitType.INPUT_TOKENS:
        return input_tokens
    if limit.limit_type == LimitType.OUTPUT_TOKENS:
        return output_tokens
    if limit.limit_type == LimitType.COST_USD:
        return cost_microdollars
    return 0
