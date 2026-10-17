from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from app.core.audit.types import AuditSeverity
from app.core.auth.dashboard_access import Permission
from app.core.auth.dependencies import require_dashboard_permission, set_dashboard_error_format
from app.dependencies import AuditContext, get_audit_context
from app.modules.audit.repository import AuditLogFilters
from app.modules.audit.schemas import AuditActorResponse, AuditLogResponse, AuditTargetResponse
from app.modules.audit.service import AuditLogData

router = APIRouter(
    prefix="/api/audit-logs",
    tags=["dashboard"],
    dependencies=[Depends(require_dashboard_permission(Permission.AUDIT_READ)), Depends(set_dashboard_error_format)],
)

#: ``details.reason`` values are lower-case identifiers; the filter is matched
#: textually inside the JSON column, so anything else is refused up front.
_REASON_PATTERN = r"^[a-z_]{1,32}$"


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a query datetime to UTC; a naive value is taken as UTC.

    Rows are stored with UTC wall-clock components, so comparing against the
    caller's own offset would silently shift the window on SQLite.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _to_response(row: AuditLogData) -> AuditLogResponse:
    return AuditLogResponse(
        id=row.id,
        timestamp=row.timestamp,
        action=row.action,
        actor_ip=row.actor_ip,
        details=row.details,
        request_id=row.request_id,
        actor=(
            AuditActorResponse(
                user_id=row.actor.user_id,
                username=row.actor.username,
                role_slug=row.actor.role_slug,
                auth_method=row.actor.auth_method,
            )
            if row.actor is not None
            else None
        ),
        target=AuditTargetResponse(type=row.target.type, id=row.target.id) if row.target is not None else None,
        severity=row.severity,
    )


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_logs(
    action: str | None = Query(default=None),
    actor_user_id: str | None = Query(default=None, max_length=36),
    target_type: str | None = Query(default=None, max_length=32),
    target_id: str | None = Query(default=None, max_length=128),
    severity: AuditSeverity | None = Query(default=None),
    reason: str | None = Query(default=None, pattern=_REASON_PATTERN),
    since: datetime | None = Query(default=None, description="Inclusive lower bound (ISO 8601)"),
    until: datetime | None = Query(default=None, description="Exclusive upper bound (ISO 8601)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: AuditContext = Depends(get_audit_context),
) -> list[AuditLogResponse]:
    filters = AuditLogFilters(
        action=action,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        severity=severity.value if severity is not None else None,
        reason=reason,
        since=_as_utc(since),
        until=_as_utc(until),
    )
    rows = await context.service.list_logs(filters, limit=limit, offset=offset)
    return [_to_response(row) for row in rows]
