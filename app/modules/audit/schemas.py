from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.modules.shared.schemas import DashboardModel

type AuditDetailScalar = str | int | float | bool | None
type AuditDetailValue = AuditDetailScalar | Sequence[AuditDetailScalar]


class AuditLogResponse(DashboardModel):
    id: int
    timestamp: datetime
    action: str
    actor_ip: str | None = None
    details: dict[str, AuditDetailValue] | None = None
    request_id: str | None = None
