from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast as typing_cast

import anyio
from sqlalchemy import Integer, String, and_, cast, func, literal_column, or_, select
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.usage.logs import RequestLogLike, calculated_cost_from_log
from app.core.usage.types import BucketModelAggregate, RequestActivityAggregate
from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, ApiKey, RequestLog
from app.modules.upstream_identities.types import CHATGPT_WEB_PROVIDER_KIND


@dataclass(frozen=True, slots=True)
class _RequestLogFilters:
    conditions: list
    needs_related_search_joins: bool


class RequestLogsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_since(self, since: datetime) -> list[RequestLog]:
        result = await self._session.execute(select(RequestLog).where(RequestLog.requested_at >= since))
        return list(result.scalars().all())

    async def find_latest_account_id_for_response_id(
        self,
        *,
        response_id: str,
        api_key_id: str | None,
        session_id: str | None = None,
    ) -> str | None:
        response_id_value = response_id.strip()
        if not response_id_value:
            return None

        base_conditions = [
            RequestLog.request_id == response_id_value,
            RequestLog.status == "success",
            RequestLog.account_id.is_not(None),
        ]
        if api_key_id is not None:
            base_conditions.append(RequestLog.api_key_id == api_key_id)

        async def _lookup_account_id(conditions: list[ColumnElement[bool]]) -> str | None:
            stmt = (
                select(RequestLog.account_id)
                .where(and_(*conditions))
                .order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
                .limit(1)
            )
            result = await self._session.execute(stmt)
            account_id = result.scalar_one_or_none()
            if not isinstance(account_id, str):
                return None
            stripped = account_id.strip()
            return stripped or None

        session_id_value = session_id.strip() if isinstance(session_id, str) else ""
        if session_id_value:
            scoped_owner = await _lookup_account_id([*base_conditions, RequestLog.session_id == session_id_value])
            if scoped_owner is not None:
                return scoped_owner

        return await _lookup_account_id(base_conditions)

    async def aggregate_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
    ) -> list[BucketModelAggregate]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            bucket_expr = func.floor(func.extract("epoch", RequestLog.requested_at) / bucket_seconds) * bucket_seconds
        else:
            # Use explicit integer division for SQLite: CAST(epoch / N AS INTEGER) * N
            epoch_col = cast(func.strftime("%s", RequestLog.requested_at), Integer)
            bucket_expr = cast(epoch_col / bucket_seconds, Integer) * bucket_seconds
        bucket_col = bucket_expr.label("bucket_epoch")

        stmt = (
            select(
                bucket_col,
                RequestLog.model,
                RequestLog.service_tier,
                func.count().label("request_count"),
                func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)).label("error_count"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.reasoning_tokens), 0).label("reasoning_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            )
            .where(RequestLog.requested_at >= since)
            .group_by(bucket_col, RequestLog.model, RequestLog.service_tier)
            .order_by(bucket_col)
        )
        result = await self._session.execute(stmt)
        return [
            BucketModelAggregate(
                bucket_epoch=int(row.bucket_epoch),
                model=row.model,
                service_tier=row.service_tier,
                request_count=int(row.request_count),
                error_count=int(row.error_count),
                input_tokens=int(row.input_tokens),
                output_tokens=int(row.output_tokens),
                cached_input_tokens=int(row.cached_input_tokens),
                reasoning_tokens=int(row.reasoning_tokens),
                cost_usd=float(row.cost_usd or 0.0),
            )
            for row in result.all()
        ]

    async def aggregate_activity_since(self, since: datetime) -> RequestActivityAggregate:
        stmt = select(
            func.count().label("request_count"),
            func.coalesce(
                func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)),
                0,
            ).label("error_count"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
        ).where(RequestLog.requested_at >= since)
        result = await self._session.execute(stmt)
        row = result.one()
        return RequestActivityAggregate(
            request_count=int(row.request_count),
            error_count=int(row.error_count),
            input_tokens=int(row.input_tokens),
            output_tokens=int(row.output_tokens),
            cached_input_tokens=int(row.cached_input_tokens),
            cost_usd=float(row.cost_usd or 0.0),
        )

    async def top_error_since(self, since: datetime) -> str | None:
        stmt = (
            select(RequestLog.error_code, func.count(RequestLog.id).label("error_count"))
            .where(
                RequestLog.requested_at >= since,
                RequestLog.status != "success",
                RequestLog.error_code.is_not(None),
            )
            .group_by(RequestLog.error_code)
            .order_by(func.count(RequestLog.id).desc(), RequestLog.error_code.asc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        return str(row[0]) if row and row[0] else None

    async def add_log(
        self,
        account_id: str | None,
        *,
        provider_kind: str | None = None,
        routing_subject_id: str | None = None,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        latency_first_token_ms: int | None = None,
        error_message: str | None = None,
        requested_at: datetime | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        transport: str | None = None,
        api_key_id: str | None = None,
        route_class: str | None = None,
        upstream_request_id: str | None = None,
        rejection_reason: str | None = None,
        session_id: str | None = None,
        plan_type: str | None = None,
    ) -> RequestLog:
        resolved_request_id = ensure_request_id(request_id)
        resolved_provider_kind = provider_kind
        if resolved_provider_kind is None and account_id is not None:
            resolved_provider_kind = CHATGPT_WEB_PROVIDER_KIND
        resolved_routing_subject_id = routing_subject_id or account_id
        resolved_plan_type = plan_type
        if resolved_plan_type is None and account_id:
            resolved_plan_type = await self._resolve_account_plan_type(account_id)
        log = RequestLog(
            account_id=account_id,
            provider_kind=resolved_provider_kind,
            routing_subject_id=resolved_routing_subject_id,
            api_key_id=api_key_id,
            session_id=session_id,
            request_id=resolved_request_id,
            model=model,
            plan_type=resolved_plan_type,
            transport=transport,
            route_class=route_class,
            upstream_request_id=upstream_request_id,
            rejection_reason=rejection_reason,
            service_tier=service_tier,
            requested_service_tier=requested_service_tier,
            actual_service_tier=actual_service_tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            cost_usd=None,
            reasoning_effort=reasoning_effort,
            latency_ms=latency_ms,
            latency_first_token_ms=latency_first_token_ms,
            status=status,
            error_code=error_code,
            error_message=error_message,
            requested_at=requested_at or utcnow(),
        )
        log.cost_usd = calculated_cost_from_log(typing_cast(RequestLogLike, log))
        self._session.add(log)
        try:
            await self._session.commit()
            await self._session.refresh(log)
            return log
        except sa_exc.ResourceClosedError:
            return log
        except BaseException:
            await _safe_rollback(self._session)
            raise

    async def update_model_for_request(self, request_id: str, model: str) -> int:
        """Override the ``model`` field of any logs matching ``request_id``.

        Used by route handlers that translate a public request shape (e.g.
        ``/v1/images/generations``) into an internal Responses request: the
        first-pass log row stores the internal host model used for routing,
        and we rewrite it here once the public effective model is known so
        the dashboard and usage views surface the user-visible model.

        Returns the number of rows that were updated.
        """
        resolved_request_id = ensure_request_id(request_id)
        try:
            # Fetch the affected rows so we can recompute ``cost_usd``
            # from the new model. ``add_log`` derives the cost at insert
            # time from the original (host) model; without recomputing
            # here, dashboards would mix the public ``gpt-image-*`` model
            # label with host-model pricing and report inaccurate cost.
            stmt = select(RequestLog).where(RequestLog.request_id == resolved_request_id)
            result_rows = await self._session.execute(stmt)
            logs = list(result_rows.scalars())
            if not logs:
                return 0
            for log in logs:
                log.model = model
                log.cost_usd = calculated_cost_from_log(typing_cast(RequestLogLike, log))
            await self._session.commit()
        except sa_exc.ResourceClosedError:
            return 0
        except BaseException:
            await _safe_rollback(self._session)
            raise
        return len(logs)

    async def list_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        api_key_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
    ) -> tuple[list[RequestLog], int]:
        filters = self._build_filters(
            search=search,
            since=since,
            until=until,
            account_ids=account_ids,
            api_key_ids=api_key_ids,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=include_success,
            include_error_other=include_error_other,
            error_codes_in=error_codes_in,
            error_codes_excluding=error_codes_excluding,
        )

        total_col = func.count().over().label("_total")
        stmt = select(RequestLog, total_col).order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
        stmt = self._apply_related_search_joins(stmt, filters.needs_related_search_joins)
        if filters.conditions:
            stmt = stmt.where(and_(*filters.conditions))
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        rows = result.all()
        if not rows:
            return [], await self._count_recent(filters)
        logs = [row[0] for row in rows]
        total = rows[0][1]
        return logs, total

    async def _count_recent(self, filters: _RequestLogFilters) -> int:
        count_stmt = select(func.count(RequestLog.id)).select_from(RequestLog)
        count_stmt = self._apply_related_search_joins(count_stmt, filters.needs_related_search_joins)
        if filters.conditions:
            count_stmt = count_stmt.where(and_(*filters.conditions))
        result = await self._session.execute(count_stmt)
        return int(result.scalar_one())

    async def _resolve_account_plan_type(self, account_id: str) -> str | None:
        result = await self._session.execute(select(Account.plan_type).where(Account.id == account_id).limit(1))
        return result.scalar_one_or_none()

    async def list_filter_options(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        api_key_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
    ) -> tuple[list[str], list[tuple[str, str | None]], list[str], list[tuple[str, str | None]]]:
        filters = self._build_filters(
            since=since,
            until=until,
            account_ids=account_ids,
            api_key_ids=api_key_ids,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=True,
            include_error_other=True,
            error_codes_in=None,
            error_codes_excluding=None,
        )
        api_key_facet_filters = self._build_filters(
            since=since,
            until=until,
            account_ids=account_ids,
            api_key_ids=None,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=True,
            include_error_other=True,
            error_codes_in=None,
            error_codes_excluding=None,
        )

        subject_id = _request_log_subject_id_expr()
        account_stmt = select(subject_id).distinct().order_by(subject_id.asc())
        model_stmt = (
            select(RequestLog.model, RequestLog.reasoning_effort)
            .distinct()
            .order_by(RequestLog.model.asc(), RequestLog.reasoning_effort.asc())
        )
        api_key_stmt = select(RequestLog.api_key_id).distinct().order_by(RequestLog.api_key_id.asc())
        status_stmt = (
            select(RequestLog.status, RequestLog.error_code)
            .distinct()
            .order_by(RequestLog.status.asc(), RequestLog.error_code.asc())
        )
        if filters.conditions:
            clause = and_(*filters.conditions)
            account_stmt = account_stmt.where(clause)
            model_stmt = model_stmt.where(clause)
            status_stmt = status_stmt.where(clause)
        if api_key_facet_filters.conditions:
            api_key_stmt = api_key_stmt.where(and_(*api_key_facet_filters.conditions))

        account_rows = await self._session.execute(account_stmt)
        model_rows = await self._session.execute(model_stmt)
        api_key_rows = await self._session.execute(api_key_stmt)
        status_rows = await self._session.execute(status_stmt)

        account_ids = [row[0] for row in account_rows.all() if row[0]]
        model_options = [(row[0], row[1]) for row in model_rows.all() if row[0]]
        api_key_ids = [row[0] for row in api_key_rows.all() if row[0]]
        status_values = [(row[0], row[1]) for row in status_rows.all() if row[0]]
        return account_ids, model_options, api_key_ids, status_values

    async def get_api_key_names_by_ids(self, api_key_ids: list[str]) -> dict[str, str]:
        unique_ids = sorted({key_id for key_id in api_key_ids if key_id})
        if not unique_ids:
            return {}
        result = await self._session.execute(select(ApiKey.id, ApiKey.name).where(ApiKey.id.in_(unique_ids)))
        return {key_id: name for key_id, name in result.all() if key_id and name}

    async def get_api_key_details_by_ids(self, api_key_ids: list[str]) -> dict[str, tuple[str, str | None]]:
        unique_ids = sorted({key_id for key_id in api_key_ids if key_id})
        if not unique_ids:
            return {}
        result = await self._session.execute(
            select(ApiKey.id, ApiKey.name, ApiKey.key_prefix).where(ApiKey.id.in_(unique_ids))
        )
        return {key_id: (name, key_prefix) for key_id, name, key_prefix in result.all() if key_id and name}

    def _build_filters(
        self,
        *,
        search: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        api_key_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
    ) -> _RequestLogFilters:
        conditions = []
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        if until is not None:
            conditions.append(RequestLog.requested_at <= until)
        if account_ids:
            conditions.append(_request_log_subject_id_expr().in_(account_ids))
        if api_key_ids:
            conditions.append(RequestLog.api_key_id.in_(api_key_ids))

        if model_options:
            pair_conditions = []
            for model, effort in model_options:
                base = (model or "").strip()
                if not base:
                    continue
                if effort is None:
                    pair_conditions.append(and_(RequestLog.model == base, RequestLog.reasoning_effort.is_(None)))
                else:
                    pair_conditions.append(and_(RequestLog.model == base, RequestLog.reasoning_effort == effort))
            if pair_conditions:
                conditions.append(or_(*pair_conditions))
        else:
            if models:
                conditions.append(RequestLog.model.in_(models))
            if reasoning_efforts:
                conditions.append(RequestLog.reasoning_effort.in_(reasoning_efforts))

        status_conditions = []
        if include_success:
            status_conditions.append(RequestLog.status == "success")
        if error_codes_in:
            status_conditions.append(and_(RequestLog.status == "error", RequestLog.error_code.in_(error_codes_in)))
        if include_error_other:
            error_clause = [RequestLog.status == "error"]
            if error_codes_excluding:
                error_clause.append(
                    or_(
                        RequestLog.error_code.is_(None),
                        ~RequestLog.error_code.in_(error_codes_excluding),
                    )
                )
            status_conditions.append(and_(*error_clause))
        if status_conditions:
            conditions.append(or_(*status_conditions))
        if search:
            search_pattern = f"%{search}%"
            conditions.append(
                or_(
                    RequestLog.account_id.ilike(search_pattern),
                    RequestLog.routing_subject_id.ilike(search_pattern),
                    Account.email.ilike(search_pattern),
                    RequestLog.request_id.ilike(search_pattern),
                    RequestLog.model.ilike(search_pattern),
                    RequestLog.reasoning_effort.ilike(search_pattern),
                    RequestLog.status.ilike(search_pattern),
                    RequestLog.error_code.ilike(search_pattern),
                    RequestLog.error_message.ilike(search_pattern),
                    RequestLog.api_key_id.ilike(search_pattern),
                    ApiKey.name.ilike(search_pattern),
                    cast(RequestLog.requested_at, String).ilike(search_pattern),
                    cast(RequestLog.input_tokens, String).ilike(search_pattern),
                    cast(RequestLog.output_tokens, String).ilike(search_pattern),
                    cast(RequestLog.cached_input_tokens, String).ilike(search_pattern),
                    cast(RequestLog.reasoning_tokens, String).ilike(search_pattern),
                    cast(RequestLog.latency_ms, String).ilike(search_pattern),
                )
            )
            return _RequestLogFilters(conditions=conditions, needs_related_search_joins=True)
        return _RequestLogFilters(conditions=conditions, needs_related_search_joins=False)

    def _apply_related_search_joins(self, stmt, include_related_search_joins: bool):
        if not include_related_search_joins:
            return stmt
        return stmt.outerjoin(Account, Account.id == RequestLog.account_id).outerjoin(
            ApiKey,
            ApiKey.id == RequestLog.api_key_id,
        )


def _request_log_subject_id_expr():
    return func.coalesce(RequestLog.account_id, RequestLog.routing_subject_id)


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        with anyio.CancelScope(shield=True):
            await session.rollback()
    except BaseException:
        return
