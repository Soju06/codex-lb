from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.dependencies import AuditContext, get_audit_context
from app.modules.audit.schemas import AuditLogResponse

router = APIRouter(
    prefix="/api/audit-logs",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_logs(
    action: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: AuditContext = Depends(get_audit_context),
) -> list[AuditLogResponse]:
    rows = await context.service.list_logs(action=action, limit=limit, offset=offset)
    return [
        AuditLogResponse(
            id=row.id,
            timestamp=row.timestamp,
            action=row.action,
            actor_ip=row.actor_ip,
            details=row.details,
            request_id=row.request_id,
        )
        for row in rows
    ]
