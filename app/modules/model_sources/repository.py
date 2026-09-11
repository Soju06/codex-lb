from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, and_, case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import ModelSource, ModelSourceModel, RequestLog
from app.modules.model_sources.governance import latest_health_checks, operational_status
from app.modules.model_sources.schemas import CompanySourceStatus, ObservedSourceUsage


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

    async def operational_status(self, source: ModelSource) -> CompanySourceStatus:
        return await operational_status(self._session, source)

    async def latest_health_checks(self, source_ids: list[str]) -> dict[tuple[str, str], RequestLog]:
        return await latest_health_checks(self._session, source_ids)

    async def list_sources(self) -> list[ModelSource]:
        result = await self._session.execute(
            select(ModelSource).options(selectinload(ModelSource.models)).order_by(ModelSource.name)
        )
        return list(result.scalars().unique().all())

    async def observed_usage(self, source_ids: list[str], *, since: datetime) -> dict[str, ObservedSourceUsage]:
        complete = and_(RequestLog.input_tokens.is_not(None), RequestLog.output_tokens.is_not(None))
        rows = await self._session.execute(
            select(
                RequestLog.model_source_id,
                func.count().label("requests"),
                func.sum(case((complete, 0), else_=1)).label("missing"),
                func.sum(RequestLog.input_tokens).label("input_tokens"),
                func.sum(RequestLog.output_tokens).label("output_tokens"),
            )
            .where(RequestLog.model_source_id.in_(source_ids), RequestLog.requested_at >= since)
            .group_by(RequestLog.model_source_id)
        )
        result = {
            sid: ObservedSourceUsage(
                since=since, requests=0, requests_without_usage=0, input_tokens=None, output_tokens=None
            )
            for sid in source_ids
        }
        for row in rows:
            result[row.model_source_id] = ObservedSourceUsage(
                since=since,
                requests=row.requests,
                requests_without_usage=row.missing,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
            )
        return result

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
            .where(ModelSource.kind.in_(("openai_compatible", "llmbox", "trae", "codebase_llm")))
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
            .where(ModelSource.kind.in_(("openai_compatible", "llmbox", "trae", "codebase_llm")))
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
