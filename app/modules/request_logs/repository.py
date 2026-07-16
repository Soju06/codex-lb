from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import cast as typing_cast

import anyio
from sqlalchemy import Integer, String, and_, case, cast, func, literal_column, or_, select
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.core.usage.logs import RequestLogLike, calculated_cost_from_log
from app.core.usage.types import BucketModelAggregate, RequestActivityAggregate, UsageSummaryLogsAggregate
from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, ApiKey, RequestKind, RequestLog
from app.db.session import sqlite_writer_section


@dataclass(frozen=True, slots=True)
class _RequestLogFilters:
    conditions: list
    needs_related_search_joins: bool


# The exact COUNT(*) behind the request-log listing's "X-Y of N" scans the
# whole filtered set on PostgreSQL; the dashboard re-runs it on every 30s
# poll and every pagination click even though the displayed total is
# tolerant of short staleness. Cache it per filter signature for a small
# fixed TTL (issue #1340 / PRINCIPLES.md P2; the test suite patches the
# TTL to 0 so totals stay exact within a test).
_COUNT_CACHE_TTL_SECONDS = 30.0
_COUNT_CACHE_MAX_ENTRIES = 256
_recent_count_cache: dict[tuple, tuple[int, float]] = {}


def _clear_recent_count_cache() -> None:
    _recent_count_cache.clear()


def _cached_recent_count(key: tuple) -> int | None:
    entry = _recent_count_cache.get(key)
    if entry is None:
        return None
    total, expires_at = entry
    if time.monotonic() >= expires_at:
        _recent_count_cache.pop(key, None)
        return None
    return total


def _store_recent_count(key: tuple, total: int, ttl_seconds: float) -> None:
    if len(_recent_count_cache) >= _COUNT_CACHE_MAX_ENTRIES:
        oldest = min(_recent_count_cache, key=lambda existing: _recent_count_cache[existing][1])
        _recent_count_cache.pop(oldest, None)
    _recent_count_cache[key] = (total, time.monotonic() + ttl_seconds)


class RequestLogsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _exclude_warmup_clause() -> ColumnElement[bool]:
        return RequestLog.request_kind.not_in((RequestKind.WARMUP.value, "limit_warmup"))

    async def list_since(self, since: datetime) -> list[RequestLog]:
        result = await self._session.execute(
            select(RequestLog).where(
                RequestLog.requested_at >= since,
                self._exclude_warmup_clause(),
            )
        )
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
            .where(self._exclude_warmup_clause())
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
        stmt = self._aggregate_activity_stmt(since)
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

    async def aggregate_activity_between(self, since: datetime, until: datetime) -> RequestActivityAggregate:
        stmt = self._aggregate_activity_stmt(since, until)
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

    def _aggregate_activity_stmt(self, since: datetime, until: datetime | None = None):
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
        ).where(
            RequestLog.requested_at >= since,
            self._exclude_warmup_clause(),
        )
        if until is not None:
            stmt = stmt.where(RequestLog.requested_at < until)
        return stmt

    async def top_error_since(self, since: datetime) -> str | None:
        stmt = self._top_error_stmt(since)
        result = await self._session.execute(stmt)
        row = result.first()
        return str(row[0]) if row and row[0] else None

    async def aggregate_usage_metrics_since(self, since: datetime) -> UsageSummaryLogsAggregate:
        """Aggregate the usage-summary window in SQL instead of hydrating
        every RequestLog row (the secondary window is typically 7 days).

        Matches the Python log helpers exactly: output tokens fall back to
        reasoning tokens, cached tokens clamp per-row to [0, input_tokens],
        and models whose costs are all NULL are omitted from per-model cost.
        """
        dialect = self._session.get_bind().dialect.name
        # SQLite's two-argument min()/max() scalar functions are its
        # least()/greatest().
        least = func.least if dialect == "postgresql" else func.min
        greatest = func.greatest if dialect == "postgresql" else func.max

        window = [RequestLog.requested_at >= since, self._exclude_warmup_clause()]
        output_expr = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
        tokens_expr = func.coalesce(RequestLog.input_tokens, 0) + output_expr
        cached_expr = case(
            (RequestLog.cached_input_tokens.is_(None), 0),
            (RequestLog.input_tokens.is_(None), greatest(0, RequestLog.cached_input_tokens)),
            else_=greatest(0, least(RequestLog.cached_input_tokens, RequestLog.input_tokens)),
        )
        # ONE statement = one snapshot: totals, top error, and per-model cost
        # must describe the same committed row set (the legacy path read one
        # SELECT and reduced it in Python, which was internally consistent;
        # separate statements under READ COMMITTED are not). The grouped
        # result stays tiny (models x error codes) and everything derives
        # from it in Python.
        is_error_expr = (RequestLog.status != literal_column("'success'")).label("is_error")
        rows = (
            await self._session.execute(
                select(
                    RequestLog.model,
                    is_error_expr,
                    RequestLog.error_code,
                    func.count().label("request_count"),
                    func.coalesce(func.sum(tokens_expr), 0).label("total_tokens"),
                    func.coalesce(func.sum(cached_expr), 0).label("cached_input_tokens"),
                    func.sum(RequestLog.cost_usd).label("cost_usd"),
                    func.count(RequestLog.cost_usd).label("cost_count"),
                )
                .where(*window)
                .group_by(RequestLog.model, is_error_expr, RequestLog.error_code)
            )
        ).all()

        request_count = 0
        error_count = 0
        total_tokens = 0
        cached_input_tokens = 0
        error_code_counts: dict[str, int] = {}
        cost_sums: dict[str, float] = {}
        cost_counts: dict[str, int] = {}
        for model, is_error, error_code, group_count, group_tokens, group_cached, group_cost, cost_count in rows:
            group_count = int(group_count or 0)
            request_count += group_count
            total_tokens += int(group_tokens or 0)
            cached_input_tokens += int(group_cached or 0)
            if is_error:
                error_count += group_count
                if error_code:
                    error_code_counts[error_code] = error_code_counts.get(error_code, 0) + group_count
            cost_sums[model] = cost_sums.get(model, 0.0) + float(group_cost or 0.0)
            cost_counts[model] = cost_counts.get(model, 0) + int(cost_count or 0)

        top_error = None
        if error_code_counts:
            # Deterministic tie-break: highest count, then code ascending
            # (the same rule _top_error_stmt uses for the dashboard).
            top_error = min(error_code_counts, key=lambda code: (-error_code_counts[code], code))

        return UsageSummaryLogsAggregate(
            request_count=request_count,
            error_count=error_count,
            total_tokens=total_tokens,
            cached_input_tokens=cached_input_tokens,
            top_error=top_error,
            # Models whose costs are all NULL stay out, matching the legacy
            # per-row skip of None costs.
            cost_by_model=sorted((model, cost_sums[model]) for model, count in cost_counts.items() if count > 0),
        )

    async def top_error_between(self, since: datetime, until: datetime) -> str | None:
        stmt = self._top_error_stmt(since, until)
        result = await self._session.execute(stmt)
        row = result.first()
        return str(row[0]) if row and row[0] else None

    def _top_error_stmt(self, since: datetime, until: datetime | None = None):
        stmt = (
            select(RequestLog.error_code, func.count(RequestLog.id).label("error_count"))
            .where(
                RequestLog.requested_at >= since,
                self._exclude_warmup_clause(),
                RequestLog.status != "success",
                RequestLog.error_code.is_not(None),
            )
            .group_by(RequestLog.error_code)
            .order_by(func.count(RequestLog.id).desc(), RequestLog.error_code.asc())
            .limit(1)
        )
        if until is not None:
            stmt = stmt.where(RequestLog.requested_at < until)
        return stmt

    async def earliest_activity_at(self) -> datetime | None:
        stmt = select(func.min(RequestLog.requested_at)).where(self._exclude_warmup_clause())
        result = await self._session.execute(stmt)
        value = result.scalar_one_or_none()
        return value if isinstance(value, datetime) else None

    async def add_log(
        self,
        account_id: str | None,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        latency_first_token_ms: int | None = None,
        latency_queue_ms: int | None = None,
        latency_response_created_ms: int | None = None,
        latency_first_upstream_event_ms: int | None = None,
        latency_response_create_gate_wait_ms: int | None = None,
        latency_bridge_queue_wait_ms: int | None = None,
        prewarm_status: str | None = None,
        prewarm_latency_ms: int | None = None,
        prewarm_canary_bucket: str | None = None,
        prewarm_eligible_reason: str | None = None,
        session_previous_gap_ms: int | None = None,
        error_message: str | None = None,
        requested_at: datetime | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        transport: str | None = None,
        upstream_transport: str | None = None,
        api_key_id: str | None = None,
        session_id: str | None = None,
        plan_type: str | None = None,
        source: str | None = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
        client_ip: str | None = None,
        failure_phase: str | None = None,
        failure_detail: str | None = None,
        failure_exception_type: str | None = None,
        upstream_status_code: int | None = None,
        upstream_error_code: str | None = None,
        model_source_id: str | None = None,
        model_source_kind: str | None = None,
        cost_usd: float | None = None,
        bridge_stage: str | None = None,
        request_kind: str = RequestKind.NORMAL.value,
        upstream_proxy_route_mode: str | None = None,
        upstream_proxy_pool_id: str | None = None,
        upstream_proxy_endpoint_id: str | None = None,
        upstream_proxy_fallback_used: bool | None = None,
        upstream_proxy_fail_closed_reason: str | None = None,
        archive_request_id: str | None = None,
    ) -> RequestLog:
        async with sqlite_writer_section():
            resolved_request_id = ensure_request_id(request_id)
            resolved_archive_request_id = (archive_request_id or "").strip() or resolved_request_id
            resolved_plan_type = plan_type
            if resolved_plan_type is None and account_id:
                resolved_plan_type = await self._resolve_account_plan_type(account_id)
            resolved_useragent = useragent if not isinstance(useragent, str) or useragent.strip() else None
            resolved_useragent_group = (
                useragent_group if not isinstance(useragent_group, str) or useragent_group.strip() else None
            )
            resolved_client_ip = client_ip if not isinstance(client_ip, str) or client_ip.strip() else None
            log = RequestLog(
                account_id=account_id,
                model_source_id=model_source_id,
                model_source_kind=model_source_kind,
                api_key_id=api_key_id,
                session_id=session_id,
                request_id=resolved_request_id,
                archive_request_id=resolved_archive_request_id,
                model=model,
                plan_type=resolved_plan_type,
                source=source,
                transport=transport,
                upstream_transport=upstream_transport,
                request_kind=request_kind,
                useragent=resolved_useragent,
                useragent_group=resolved_useragent_group,
                client_ip=resolved_client_ip,
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
                latency_queue_ms=latency_queue_ms,
                latency_response_created_ms=latency_response_created_ms,
                latency_first_upstream_event_ms=latency_first_upstream_event_ms,
                latency_response_create_gate_wait_ms=latency_response_create_gate_wait_ms,
                latency_bridge_queue_wait_ms=latency_bridge_queue_wait_ms,
                prewarm_status=prewarm_status,
                prewarm_latency_ms=prewarm_latency_ms,
                prewarm_canary_bucket=prewarm_canary_bucket,
                prewarm_eligible_reason=prewarm_eligible_reason,
                session_previous_gap_ms=session_previous_gap_ms,
                status=status,
                error_code=error_code,
                error_message=error_message,
                failure_phase=failure_phase,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
                upstream_status_code=upstream_status_code,
                upstream_error_code=upstream_error_code,
                bridge_stage=bridge_stage,
                upstream_proxy_route_mode=upstream_proxy_route_mode,
                upstream_proxy_pool_id=upstream_proxy_pool_id,
                upstream_proxy_endpoint_id=upstream_proxy_endpoint_id,
                upstream_proxy_fallback_used=upstream_proxy_fallback_used,
                upstream_proxy_fail_closed_reason=upstream_proxy_fail_closed_reason,
                requested_at=requested_at or utcnow(),
            )
            log.cost_usd = (
                cost_usd
                if cost_usd is not None
                else 0.0
                if model_source_id is not None
                else calculated_cost_from_log(typing_cast(RequestLogLike, log))
            )
            self._session.add(log)
            try:
                await self._session.commit()
                # No refresh: every column is set explicitly before insert and
                # expire_on_commit=False, so the round trip was pure overhead
                # on every request's log write.
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
        async with sqlite_writer_section():
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
            exclude_soft_deleted=True,
        )

        stmt = select(RequestLog).order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
        stmt = self._apply_related_search_joins(stmt, filters.needs_related_search_joins)
        if filters.conditions:
            stmt = stmt.where(and_(*filters.conditions))
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        logs = list(result.scalars().all())

        ttl_seconds = _COUNT_CACHE_TTL_SECONDS
        if ttl_seconds <= 0:
            return logs, await self._count_recent(filters)
        cache_key = (
            search,
            since,
            until,
            tuple(account_ids or ()),
            tuple(api_key_ids or ()),
            tuple(model_options or ()),
            tuple(models or ()),
            tuple(reasoning_efforts or ()),
            include_success,
            include_error_other,
            tuple(sorted(error_codes_in)) if error_codes_in else None,
            tuple(sorted(error_codes_excluding)) if error_codes_excluding else None,
        )
        total = _cached_recent_count(cache_key)
        if total is None:
            total = await self._count_recent(filters)
            _store_recent_count(cache_key, total, ttl_seconds)
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
            exclude_soft_deleted=True,
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
            exclude_soft_deleted=True,
        )

        unfiltered = not any((since, until, account_ids, api_key_ids, model_options, models, reasoning_efforts))
        if unfiltered:
            # PostgreSQL has no loose index scan: with no user filters each
            # DISTINCT below is a full pass over request_logs, four times per
            # filter-panel load. Emulate the skip scan instead — one indexed
            # probe per distinct value.
            return (
                [value for value in await self._distinct_skip_scan(RequestLog.account_id, filters.conditions) if value],
                await self._pair_facet_skip_scan(RequestLog.model, RequestLog.reasoning_effort, filters.conditions),
                [
                    value
                    for value in await self._distinct_skip_scan(RequestLog.api_key_id, api_key_facet_filters.conditions)
                    if value
                ],
                await self._pair_facet_skip_scan(RequestLog.status, RequestLog.error_code, filters.conditions),
            )

        account_stmt = select(RequestLog.account_id).distinct().order_by(RequestLog.account_id.asc())
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

    async def _distinct_skip_scan(
        self,
        column: InstrumentedAttribute[str] | InstrumentedAttribute[str | None],
        conditions: list,
    ) -> list[str]:
        """Loose-index-scan emulation: seed min(column), then min(column) >
        previous, one btree probe per distinct value. NULLs never seed or
        chain (min() skips them); empty strings are preserved — the legacy
        DISTINCT path only drops falsy values per facet, in the callers."""
        seed = select(func.min(column).label("val")).where(*conditions)
        skip = seed.cte("facet_skip", recursive=True)
        successor = select(func.min(column)).where(*conditions, column > skip.c.val).scalar_subquery()
        skip = skip.union_all(select(successor).where(skip.c.val.is_not(None)))
        stmt = select(skip.c.val).where(skip.c.val.is_not(None)).order_by(skip.c.val.asc())
        rows = await self._session.execute(stmt)
        return [value for (value,) in rows.all() if value is not None]

    async def _pair_facet_skip_scan(
        self,
        leading: InstrumentedAttribute[str] | InstrumentedAttribute[str | None],
        second: InstrumentedAttribute[str] | InstrumentedAttribute[str | None],
        conditions: list,
    ) -> list[tuple[str, str | None]]:
        """(leading, second) facet: skip-scan the leading column, then per
        value probe a `(value, NULL)` pair and skip-scan the non-NULL second
        values. NULL pair placement follows the backend's ORDER BY ASC NULL
        ordering (SQLite: first, PostgreSQL: last) so results match the
        legacy DISTINCT path exactly."""
        nulls_first = self._session.get_bind().dialect.name == "sqlite"
        pairs: list[tuple[str, str | None]] = []
        for value in await self._distinct_skip_scan(leading, conditions):
            if not value:
                # Legacy DISTINCT drops falsy leading values in Python.
                continue
            value_conditions = [*conditions, leading == value]
            null_probe = select(RequestLog.id).where(*value_conditions, second.is_(None)).limit(1)
            has_null = (await self._session.execute(null_probe)).scalar_one_or_none() is not None
            second_values = await self._distinct_skip_scan(second, value_conditions)
            if has_null and nulls_first:
                pairs.append((value, None))
            pairs.extend((value, second_value) for second_value in second_values)
            if has_null and not nulls_first:
                pairs.append((value, None))
        return pairs

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
        exclude_soft_deleted: bool = False,
    ) -> _RequestLogFilters:
        conditions = []
        if exclude_soft_deleted:
            conditions.append(RequestLog.deleted_at.is_(None))
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        if until is not None:
            conditions.append(RequestLog.requested_at <= until)
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
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
                    Account.email.ilike(search_pattern),
                    RequestLog.request_id.ilike(search_pattern),
                    RequestLog.model.ilike(search_pattern),
                    RequestLog.reasoning_effort.ilike(search_pattern),
                    RequestLog.source.ilike(search_pattern),
                    RequestLog.client_ip.ilike(search_pattern),
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


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        with anyio.CancelScope(shield=True):
            await session.rollback()
    except BaseException:
        return
