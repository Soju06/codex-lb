from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import utcnow
from app.db.models import RequestLog


class RequestLogsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_since(self, since: datetime) -> list[RequestLog]:
        result = await self._session.execute(
            select(RequestLog).where(RequestLog.requested_at >= since)
        )
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
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
            error_message=error_message,
            requested_at=requested_at or utcnow(),
        )
        self._session.add(log)
        await self._session.commit()
        await self._session.refresh(log)
        return log

    async def list_recent(
        self,
        limit: int = 50,
        since: datetime | None = None,
        until: datetime | None = None,
        account_id: str | None = None,
        model: str | None = None,
        status: str | None = None,
        error_codes: list[str] | None = None,
    ) -> list[RequestLog]:
        conditions = []
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        if until is not None:
            conditions.append(RequestLog.requested_at <= until)
        if account_id is not None:
            conditions.append(RequestLog.account_id == account_id)
        if model is not None:
            conditions.append(RequestLog.model == model)
        if status is not None:
            conditions.append(RequestLog.status == status)
        if error_codes:
            conditions.append(RequestLog.error_code.in_(error_codes))

        stmt = select(RequestLog).order_by(RequestLog.requested_at.desc())
        if conditions:
            stmt = stmt.where(and_(*conditions))
        if limit:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
