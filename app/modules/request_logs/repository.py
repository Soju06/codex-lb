from __future__ import annotations

from datetime import datetime

import anyio
from sqlalchemy import Integer, String, and_, cast, func, literal_column, or_, select
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.types import BucketModelAggregate
from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, RequestLog


class RequestLogsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_since(self, since: datetime, *, owner_user_id: str | None = None) -> list[RequestLog]:
        stmt = select(RequestLog).where(RequestLog.requested_at >= since)
        if owner_user_id is not None:
            stmt = stmt.join(Account, Account.id == RequestLog.account_id).where(Account.owner_user_id == owner_user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def aggregate_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
        owner_user_id: str | None = None,
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
                func.count().label("request_count"),
                func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)).label("error_count"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.reasoning_tokens), 0).label("reasoning_tokens"),
            )
            .where(RequestLog.requested_at >= since)
            .group_by(bucket_col, RequestLog.model)
            .order_by(bucket_col)
        )
        if owner_user_id is not None:
            stmt = stmt.join(Account, Account.id == RequestLog.account_id).where(Account.owner_user_id == owner_user_id)
        result = await self._session.execute(stmt)
        return [
            BucketModelAggregate(
                bucket_epoch=int(row.bucket_epoch),
                model=row.model,
                request_count=int(row.request_count),
                error_count=int(row.error_count),
                input_tokens=int(row.input_tokens),
                output_tokens=int(row.output_tokens),
                cached_input_tokens=int(row.cached_input_tokens),
                reasoning_tokens=int(row.reasoning_tokens),
            )
            for row in result.all()
        ]

    async def add_log(
        self,
        account_id: str,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        error_message: str | None = None,
        requested_at: datetime | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        api_key_id: str | None = None,
    ) -> RequestLog:
        resolved_request_id = ensure_request_id(request_id)
        log = RequestLog(
            account_id=account_id,
            api_key_id=api_key_id,
            request_id=resolved_request_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            reasoning_effort=reasoning_effort,
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
            error_message=error_message,
            requested_at=requested_at or utcnow(),
        )
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

    async def list_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
        owner_user_id: str | None = None,
    ) -> tuple[list[RequestLog], int]:
        conditions = self._build_filters(
            search=search,
            since=since,
            until=until,
            account_ids=account_ids,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=include_success,
            include_error_other=include_error_other,
            error_codes_in=error_codes_in,
            error_codes_excluding=error_codes_excluding,
            owner_user_id=owner_user_id,
        )

        total_col = func.count().over().label("_total")
        stmt = (
            select(RequestLog, total_col)
            .outerjoin(Account, Account.id == RequestLog.account_id)
            .order_by(RequestLog.requested_at.desc())
        )
        if conditions:
            stmt = stmt.where(and_(*conditions))
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        rows = result.all()
        if not rows:
            return [], await self._count_recent(conditions)
        logs = [row[0] for row in rows]
        total = rows[0][1]
        return logs, total

    async def _count_recent(self, conditions: list) -> int:
        count_stmt = (
            select(func.count(RequestLog.id))
            .select_from(RequestLog)
            .outerjoin(Account, Account.id == RequestLog.account_id)
        )
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        result = await self._session.execute(count_stmt)
        return int(result.scalar_one())

    async def list_filter_options(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        owner_user_id: str | None = None,
    ) -> tuple[list[str], list[tuple[str, str | None]], list[tuple[str, str | None]]]:
        conditions = self._build_filters(
            since=since,
            until=until,
            account_ids=account_ids,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=True,
            include_error_other=True,
            error_codes_in=None,
            error_codes_excluding=None,
            owner_user_id=owner_user_id,
        )

        account_stmt = select(RequestLog.account_id).distinct().order_by(RequestLog.account_id.asc())
        model_stmt = (
            select(RequestLog.model, RequestLog.reasoning_effort)
            .distinct()
            .order_by(RequestLog.model.asc(), RequestLog.reasoning_effort.asc())
        )
        status_stmt = (
            select(RequestLog.status, RequestLog.error_code)
            .distinct()
            .order_by(RequestLog.status.asc(), RequestLog.error_code.asc())
        )
        account_stmt = account_stmt.outerjoin(Account, Account.id == RequestLog.account_id)
        model_stmt = model_stmt.outerjoin(Account, Account.id == RequestLog.account_id)
        status_stmt = status_stmt.outerjoin(Account, Account.id == RequestLog.account_id)
        if conditions:
            clause = and_(*conditions)
            account_stmt = account_stmt.where(clause)
            model_stmt = model_stmt.where(clause)
            status_stmt = status_stmt.where(clause)

        account_rows = await self._session.execute(account_stmt)
        model_rows = await self._session.execute(model_stmt)
        status_rows = await self._session.execute(status_stmt)

        account_ids = [row[0] for row in account_rows.all() if row[0]]
        model_options = [(row[0], row[1]) for row in model_rows.all() if row[0]]
        status_values = [(row[0], row[1]) for row in status_rows.all() if row[0]]
        return account_ids, model_options, status_values

    def _build_filters(
        self,
        *,
        search: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
        owner_user_id: str | None = None,
    ) -> list:
        conditions = []
        if owner_user_id is not None:
            conditions.append(Account.owner_user_id == owner_user_id)
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        if until is not None:
            conditions.append(RequestLog.requested_at <= until)
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))

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
                    RequestLog.status.ilike(search_pattern),
                    RequestLog.error_code.ilike(search_pattern),
                    RequestLog.error_message.ilike(search_pattern),
                    cast(RequestLog.requested_at, String).ilike(search_pattern),
                    cast(RequestLog.input_tokens, String).ilike(search_pattern),
                    cast(RequestLog.output_tokens, String).ilike(search_pattern),
                    cast(RequestLog.cached_input_tokens, String).ilike(search_pattern),
                    cast(RequestLog.reasoning_tokens, String).ilike(search_pattern),
                    cast(RequestLog.latency_ms, String).ilike(search_pattern),
                )
            )
        return conditions


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        with anyio.CancelScope(shield=True):
            await session.rollback()
    except BaseException:
        return
