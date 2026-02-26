from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelOverride


class ModelOverridesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[ModelOverride]:
        stmt = select(ModelOverride).order_by(ModelOverride.match_type.asc(), ModelOverride.match_value.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, override_id: int) -> ModelOverride | None:
        return await self._session.get(ModelOverride, override_id)

    async def find_first_enabled(self, match_type: str, match_value: str) -> ModelOverride | None:
        stmt: Select[tuple[ModelOverride]] = (
            select(ModelOverride)
            .where(
                ModelOverride.enabled.is_(True),
                ModelOverride.match_type == match_type,
                ModelOverride.match_value == match_value,
            )
            .order_by(ModelOverride.id.asc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def create(self, row: ModelOverride) -> ModelOverride:
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def update(self, row: ModelOverride) -> ModelOverride:
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, row: ModelOverride) -> None:
        await self._session.delete(row)
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    @staticmethod
    def is_unique_violation(error: Exception) -> bool:
        if not isinstance(error, IntegrityError):
            return False
        message = str(error.orig).lower()
        return "uq_model_overrides_match" in message or "unique constraint" in message

