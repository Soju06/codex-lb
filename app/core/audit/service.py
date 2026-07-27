from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence

from app.core.shutdown import is_control_plane_task_admission_open, wait_for_tasks_to_drain
from app.core.utils.request_id import get_request_id
from app.db.models import AuditLog
from app.db.session import get_session

logger = logging.getLogger(__name__)

_REDACTED_DETAIL_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "id_token",
        "key",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)

type AuditDetailScalar = str | int | float | bool | None
type AuditDetailValue = AuditDetailScalar | Sequence[AuditDetailScalar]
type AuditDetails = Mapping[str, AuditDetailValue]

_AUDIT_LOG_TASKS: set[asyncio.Task[None]] = set()


def _sanitize_details(details: AuditDetails | None) -> dict[str, AuditDetailValue] | None:
    if not details:
        return None

    sanitized = {key: value for key, value in details.items() if key.strip().lower() not in _REDACTED_DETAIL_KEYS}
    return sanitized or None


class AuditService:
    @staticmethod
    async def log(
        action: str,
        actor_ip: str | None = None,
        details: AuditDetails | None = None,
        request_id: str | None = None,
    ) -> None:
        await _write_audit_log(action, actor_ip, details, request_id)

    @staticmethod
    def log_async(
        action: str,
        actor_ip: str | None = None,
        details: AuditDetails | None = None,
        request_id: str | None = None,
    ) -> None:
        if not is_control_plane_task_admission_open():
            logger.warning("Audit log task rejected after shutdown admission closed: %s", action)
            return
        task = asyncio.create_task(
            _write_audit_log(action, actor_ip, details, request_id or get_request_id()),
            name=f"audit-log-{action}",
        )
        _AUDIT_LOG_TASKS.add(task)
        task.add_done_callback(_handle_audit_log_task_done)


async def drain_audit_log_tasks(timeout_seconds: float) -> bool:
    pending = await wait_for_tasks_to_drain(_AUDIT_LOG_TASKS, timeout_seconds)
    for task in sorted(pending, key=lambda pending_task: pending_task.get_name()):
        logger.warning("Audit log task did not drain before shutdown: %s", task.get_name())
    return not pending


def _handle_audit_log_task_done(task: asyncio.Task[None]) -> None:
    try:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(
                "Audit log task failed unexpectedly: %s",
                task.get_name(),
                exc_info=(type(exc), exc, exc.__traceback__),
            )
    finally:
        _AUDIT_LOG_TASKS.discard(task)


async def _write_audit_log(
    action: str,
    actor_ip: str | None,
    details: AuditDetails | None,
    request_id: str | None,
) -> None:
    try:
        sanitized_details = _sanitize_details(details)
        async for session in get_session():
            log_entry = AuditLog(
                action=action,
                actor_ip=actor_ip,
                details=json.dumps(sanitized_details) if sanitized_details else None,
                request_id=request_id,
            )
            session.add(log_entry)
            await session.commit()
    except Exception:
        logger.warning("Failed to write audit log", exc_info=True)
