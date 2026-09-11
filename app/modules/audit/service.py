from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from app.core.audit.types import AuditActor, AuditDetailValue, AuditTarget
from app.db.models import AuditLog
from app.modules.audit.repository import AuditLogFilters, AuditRepository

type AuditDetails = dict[str, AuditDetailValue]


@dataclass(frozen=True, slots=True)
class AuditLogData:
    id: int
    timestamp: datetime
    action: str
    actor_ip: str | None
    details: AuditDetails | None
    request_id: str | None
    actor: AuditActor | None
    target: AuditTarget | None
    severity: str


class AuditLogsService:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def list_logs(
        self,
        filters: AuditLogFilters,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLogData]:
        rows = await self._repository.list_logs(filters, limit=limit, offset=offset)
        return [_to_audit_log_data(row) for row in rows]


def _to_audit_log_data(row: AuditLog) -> AuditLogData:
    details: AuditDetails | None = None
    if row.details:
        parsed = json.loads(row.details)
        if isinstance(parsed, dict):
            details = cast(AuditDetails, parsed)
    actor: AuditActor | None = None
    actor_columns = (row.actor_user_id, row.actor_username, row.actor_role_slug, row.auth_method)
    if any(value is not None for value in actor_columns):
        actor = AuditActor(
            user_id=row.actor_user_id,
            username=row.actor_username,
            role_slug=row.actor_role_slug,
            auth_method=row.auth_method,
        )
    target: AuditTarget | None = None
    if row.target_type is not None and row.target_id is not None:
        target = AuditTarget(type=row.target_type, id=row.target_id)
    return AuditLogData(
        id=row.id,
        timestamp=row.timestamp,
        action=row.action,
        actor_ip=row.actor_ip,
        details=details,
        request_id=row.request_id,
        actor=actor,
        target=target,
        severity=row.severity,
    )
