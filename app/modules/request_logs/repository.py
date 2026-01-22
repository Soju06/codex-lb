from __future__ import annotations

from datetime import datetime

import anyio
from sqlalchemy import String, and_, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, RequestLog


class RequestLogsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_since(self, since: datetime) -> list[RequestLog]:
        result = await self._session.execute(select(RequestLog).where(RequestLog.requested_at >= since))
        return list(result.scalars().all())

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
    ) -> RequestLog:
        resolved_request_id = ensure_request_id(request_id)
        log = RequestLog(
            account_id=account_id,
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
    ) -> list[RequestLog]:
        conditions = []
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
                    pair_conditions.append(
                        and_(RequestLog.model == base, RequestLog.reasoning_effort.is_(None))
                    )
                else:
                    pair_conditions.append(
                        and_(RequestLog.model == base, RequestLog.reasoning_effort == effort)
                    )
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
            status_conditions.append(
                and_(RequestLog.status == "error", RequestLog.error_code.in_(error_codes_in))
            )
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

        stmt = (
            select(RequestLog)
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
        return list(result.scalars().all())

    async def list_filter_options(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
    ) -> tuple[list[str], list[tuple[str, str | None]]]:
        conditions = []
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        if until is not None:
            conditions.append(RequestLog.requested_at <= until)
        status_conditions = []
        if include_success:
            status_conditions.append(RequestLog.status == "success")
        if error_codes_in:
            status_conditions.append(
                and_(RequestLog.status == "error", RequestLog.error_code.in_(error_codes_in))
            )
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

        account_stmt = select(RequestLog.account_id).distinct().order_by(RequestLog.account_id.asc())
        model_stmt = (
            select(RequestLog.model, RequestLog.reasoning_effort)
            .distinct()
            .order_by(RequestLog.model.asc(), RequestLog.reasoning_effort.asc())
        )
        if conditions:
            clause = and_(*conditions)
            account_stmt = account_stmt.where(clause)
            model_stmt = model_stmt.where(clause)

        account_rows = await self._session.execute(account_stmt)
        model_rows = await self._session.execute(model_stmt)

        account_ids = [row[0] for row in account_rows.all() if row[0]]
        model_options = [(row[0], row[1]) for row in model_rows.all() if row[0]]
        return account_ids, model_options


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        with anyio.CancelScope(shield=True):
            await session.rollback()
    except BaseException:
        return
