from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey
from app.core.utils.time import utcnow

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
        return list(result.scalars().all())

    async def update(
        self,
        key_id: str,
        *,
        name: str | object = _UNSET,
        allowed_models: str | None | object = _UNSET,
        weekly_token_limit: int | None | object = _UNSET,
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
        if weekly_token_limit is not _UNSET:
            assert weekly_token_limit is None or isinstance(weekly_token_limit, int)
            row.weekly_token_limit = weekly_token_limit
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

    async def increment_weekly_usage(self, key_id: str, token_count: int) -> None:
        await self._session.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .values(
                weekly_tokens_used=ApiKey.weekly_tokens_used + token_count,
                last_used_at=utcnow(),
            )
        )
        await self._session.commit()

    async def reset_weekly_usage(self, key_id: str, *, expected_reset_at: datetime, new_reset_at: datetime) -> bool:
        result = await self._session.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .where(ApiKey.weekly_reset_at == expected_reset_at)
            .values(weekly_tokens_used=0, weekly_reset_at=new_reset_at)
        )
        await self._session.commit()
        return bool(result.rowcount and result.rowcount > 0)
