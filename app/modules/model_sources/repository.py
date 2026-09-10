from __future__ import annotations

from sqlalchemy import ColumnElement, and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import ModelSource, ModelSourceModel


def _enablement_filter(only_disabled: bool) -> ColumnElement[bool]:
    """Enabled-state predicate for a per-capability source lookup.

    ``only_disabled`` selects the exact complement of the routable set: rows
    that match the model and the route shape but that an operator switched off,
    at the source or at the individual model. Routing needs that complement to
    tell "no source serves this model" apart from "the source that serves this
    model is switched off" -- the latter must not fall through to a
    subscription account, which rejects the model outright.
    """
    if only_disabled:
        return or_(ModelSource.is_enabled.is_(False), ModelSourceModel.is_enabled.is_(False))
    return and_(ModelSource.is_enabled.is_(True), ModelSourceModel.is_enabled.is_(True))


class ModelSourcesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_sources(self) -> list[ModelSource]:
        result = await self._session.execute(
            select(ModelSource).options(selectinload(ModelSource.models)).order_by(ModelSource.name)
        )
        return list(result.scalars().unique().all())

    async def list_enabled_sources(self) -> list[ModelSource]:
        result = await self._session.execute(
            select(ModelSource)
            .options(selectinload(ModelSource.models))
            .where(ModelSource.is_enabled.is_(True))
            .order_by(ModelSource.name)
        )
        return list(result.scalars().unique().all())

    async def get_by_id(self, source_id: str) -> ModelSource | None:
        result = await self._session.execute(
            select(ModelSource).options(selectinload(ModelSource.models)).where(ModelSource.id == source_id)
        )
        return result.scalar_one_or_none()

    async def find_chat_source_for_model(
        self,
        model: str,
        *,
        allowed_source_ids: set[str] | None = None,
        require_streaming: bool = False,
        only_disabled: bool = False,
    ) -> ModelSource | None:
        stmt = (
            select(ModelSource)
            .options(selectinload(ModelSource.models))
            .join(ModelSourceModel, ModelSourceModel.source_id == ModelSource.id)
            .where(ModelSource.kind == "openai_compatible")
            .where(ModelSource.supports_chat_completions.is_(True))
            .where(ModelSourceModel.model == model)
            .where(_enablement_filter(only_disabled))
            .order_by(ModelSource.name, ModelSource.id)
            .limit(1)
        )
        if require_streaming:
            stmt = stmt.where(ModelSourceModel.supports_streaming.is_(True))
        if allowed_source_ids is not None:
            if not allowed_source_ids:
                return None
            stmt = stmt.where(ModelSource.id.in_(allowed_source_ids))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_responses_source_for_model(
        self,
        model: str,
        *,
        allowed_source_ids: set[str] | None = None,
        require_streaming: bool = False,
        only_disabled: bool = False,
    ) -> ModelSource | None:
        stmt = (
            select(ModelSource)
            .options(selectinload(ModelSource.models))
            .join(ModelSourceModel, ModelSourceModel.source_id == ModelSource.id)
            .where(ModelSource.kind == "openai_compatible")
            .where(ModelSource.supports_responses.is_(True))
            .where(ModelSourceModel.model == model)
            .where(_enablement_filter(only_disabled))
            .order_by(ModelSource.name, ModelSource.id)
            .limit(1)
        )
        if require_streaming:
            stmt = stmt.where(ModelSourceModel.supports_streaming.is_(True))
        if allowed_source_ids is not None:
            if not allowed_source_ids:
                return None
            stmt = stmt.where(ModelSource.id.in_(allowed_source_ids))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_audio_transcriptions_source_for_model(
        self,
        model: str,
        *,
        allowed_source_ids: set[str] | None = None,
    ) -> ModelSource | None:
        stmt = (
            select(ModelSource)
            .options(selectinload(ModelSource.models))
            .join(ModelSourceModel, ModelSourceModel.source_id == ModelSource.id)
            .where(ModelSource.kind == "openai_compatible")
            .where(ModelSource.is_enabled.is_(True))
            .where(ModelSource.supports_audio_transcriptions.is_(True))
            .where(ModelSourceModel.model == model)
            .where(ModelSourceModel.is_enabled.is_(True))
            .order_by(ModelSource.name, ModelSource.id)
            .limit(1)
        )
        if allowed_source_ids is not None:
            if not allowed_source_ids:
                return None
            stmt = stmt.where(ModelSource.id.in_(allowed_source_ids))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_embeddings_source_for_model(
        self,
        model: str,
        *,
        allowed_source_ids: set[str] | None = None,
    ) -> ModelSource | None:
        stmt = (
            select(ModelSource)
            .options(selectinload(ModelSource.models))
            .join(ModelSourceModel, ModelSourceModel.source_id == ModelSource.id)
            .where(ModelSource.kind == "openai_compatible")
            .where(ModelSource.is_enabled.is_(True))
            .where(ModelSource.supports_embeddings.is_(True))
            .where(ModelSourceModel.model == model)
            .where(ModelSourceModel.is_enabled.is_(True))
            .order_by(ModelSource.name, ModelSource.id)
            .limit(1)
        )
        if allowed_source_ids is not None:
            if not allowed_source_ids:
                return None
            stmt = stmt.where(ModelSource.id.in_(allowed_source_ids))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, row: ModelSource, *, commit: bool = True) -> ModelSource:
        self._session.add(row)
        if commit:
            await self._session.commit()
            await self._session.refresh(row, attribute_names=["models"])
        return row

    async def delete(self, source_id: str) -> bool:
        result = await self._session.execute(
            select(ModelSource)
            .options(
                selectinload(ModelSource.models),
                selectinload(ModelSource.api_key_assignments),
            )
            .where(ModelSource.id == source_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def replace_models(
        self,
        source: ModelSource,
        models: list[ModelSourceModel],
        *,
        commit: bool = True,
    ) -> None:
        await self._session.execute(delete(ModelSourceModel).where(ModelSourceModel.source_id == source.id))
        for model in models:
            model.source_id = source.id
            self._session.add(model)
        if commit:
            await self._session.commit()
            await self._session.refresh(source, attribute_names=["models"])

    async def refresh_models(self, source: ModelSource) -> None:
        await self._session.refresh(source, attribute_names=["models"])

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
