from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from app.db.models import AuditLog
from app.modules.audit.repository import AuditRepository

type AuditDetailScalar = str | int | float | bool | None
type AuditDetailValue = AuditDetailScalar | Sequence[AuditDetailScalar]
type AuditDetails = dict[str, AuditDetailValue]


@dataclass(frozen=True, slots=True)
class AuditLogData:
    id: int
    timestamp: datetime
    action: str
    actor_ip: str | None
    details: AuditDetails | None
    request_id: str | None


class AuditLogsService:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def list_logs(
        self,
        *,
        action: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLogData]:
        rows = await self._repository.list_logs(action=action, limit=limit, offset=offset)
        return [_to_audit_log_data(row) for row in rows]


def _to_audit_log_data(row: AuditLog) -> AuditLogData:
    details: AuditDetails | None = None
    if row.details:
        parsed = json.loads(row.details)
        if isinstance(parsed, dict):
            details = cast(AuditDetails, parsed)
    return AuditLogData(
        id=row.id,
        timestamp=row.timestamp,
        action=row.action,
        actor_ip=row.actor_ip,
        details=details,
        request_id=row.request_id,
    )
