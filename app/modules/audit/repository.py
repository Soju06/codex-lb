from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

_LIKE_ESCAPE = "\\"


@dataclass(frozen=True, slots=True)
class AuditLogFilters:
    """Predicates for listing audit rows. Every field is optional; ``None`` means "no filter"."""

    action: str | None = None
    actor_user_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    severity: str | None = None
    reason: str | None = None
    since: datetime | None = None
    until: datetime | None = None


def _reason_like_pattern(reason: str) -> str:
    """Match ``"reason": "<value>"`` inside the JSON text of ``details``.

    ``details`` is written by ``json.dumps`` with default separators, so the
    key/value pair has exactly this spelling. The caller validates ``reason``
    against ``[a-z_]+``; the underscore is escaped because it is a LIKE
    single-character wildcard.
    """

    escaped = reason.replace("_", f"{_LIKE_ESCAPE}_")
    return f'%"reason": "{escaped}"%'


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_logs(
        self,
        filters: AuditLogFilters,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        if filters.action:
            stmt = stmt.where(AuditLog.action == filters.action)
        if filters.actor_user_id:
            stmt = stmt.where(AuditLog.actor_user_id == filters.actor_user_id)
        if filters.target_type:
            stmt = stmt.where(AuditLog.target_type == filters.target_type)
        if filters.target_id:
            stmt = stmt.where(AuditLog.target_id == filters.target_id)
        if filters.severity:
            stmt = stmt.where(AuditLog.severity == filters.severity)
        if filters.reason:
            stmt = stmt.where(AuditLog.details.like(_reason_like_pattern(filters.reason), escape=_LIKE_ESCAPE))
        if filters.since is not None:
            stmt = stmt.where(AuditLog.timestamp >= filters.since)
        if filters.until is not None:
            stmt = stmt.where(AuditLog.timestamp < filters.until)
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
