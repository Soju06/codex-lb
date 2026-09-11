from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Protocol

from app.core.audit.types import (
    AuditActor,
    AuditAuthMethod,
    AuditDetails,
    AuditDetailScalar,
    AuditDetailValue,
    AuditEvent,
    AuditSeverity,
    AuditTarget,
)
from app.core.shutdown import is_control_plane_task_admission_open, wait_for_tasks_to_drain
from app.core.utils.request_id import get_request_id
from app.db.models import AuditLog
from app.db.session import get_session

__all__ = [
    "AuditActor",
    "AuditAuthMethod",
    "AuditDetailScalar",
    "AuditDetailValue",
    "AuditDetails",
    "AuditEvent",
    "AuditService",
    "AuditSeverity",
    "AuditSink",
    "AuditTarget",
    "DatabaseAuditSink",
    "drain_audit_log_tasks",
    "get_audit_sinks",
    "register_audit_sink",
    "reset_audit_sinks",
]

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

_AUDIT_LOG_TASKS: set[asyncio.Task[None]] = set()


def _sanitize_details(details: AuditDetails | None) -> dict[str, AuditDetailValue] | None:
    if not details:
        return None

    sanitized = {key: value for key, value in details.items() if key.strip().lower() not in _REDACTED_DETAIL_KEYS}
    return sanitized or None


class AuditSink(Protocol):
    """A destination for audit events. Sinks must not raise to callers; failures are logged."""

    async def emit(self, event: AuditEvent) -> None: ...


class DatabaseAuditSink:
    """The authoritative sink: one ``audit_logs`` row per event."""

    async def emit(self, event: AuditEvent) -> None:
        actor = event.actor
        target = event.target
        async for session in get_session():
            session.add(
                AuditLog(
                    timestamp=event.timestamp,
                    action=event.action,
                    actor_ip=event.actor_ip,
                    details=json.dumps(event.details) if event.details else None,
                    request_id=event.request_id,
                    actor_user_id=actor.user_id if actor else None,
                    actor_username=actor.username if actor else None,
                    actor_role_slug=actor.role_slug if actor else None,
                    auth_method=actor.auth_method if actor else None,
                    target_type=target.type if target else None,
                    target_id=target.id if target else None,
                    severity=event.severity.value,
                )
            )
            await session.commit()


_DEFAULT_SINKS: tuple[AuditSink, ...] = (DatabaseAuditSink(),)
_AUDIT_SINKS: tuple[AuditSink, ...] = _DEFAULT_SINKS


def get_audit_sinks() -> tuple[AuditSink, ...]:
    """Registered sinks in emit order; the database sink is always first."""

    return _AUDIT_SINKS


def register_audit_sink(sink: AuditSink) -> None:
    global _AUDIT_SINKS
    _AUDIT_SINKS = (*_AUDIT_SINKS, sink)


def reset_audit_sinks() -> None:
    global _AUDIT_SINKS
    _AUDIT_SINKS = _DEFAULT_SINKS


def _build_event(
    action: str,
    actor_ip: str | None,
    details: AuditDetails | None,
    request_id: str | None,
    *,
    actor: AuditActor | None,
    target: AuditTarget | None,
    severity: AuditSeverity,
) -> AuditEvent:
    return AuditEvent(
        action=action,
        timestamp=datetime.now(UTC),
        actor=actor,
        actor_ip=actor_ip,
        target=target,
        severity=severity,
        details=_sanitize_details(details),
        request_id=request_id,
    )


class AuditService:
    @staticmethod
    async def log(
        action: str,
        actor_ip: str | None = None,
        details: AuditDetails | None = None,
        request_id: str | None = None,
        *,
        actor: AuditActor | None = None,
        target: AuditTarget | None = None,
        severity: AuditSeverity = AuditSeverity.INFO,
    ) -> None:
        await _write_audit_log(
            _build_event(action, actor_ip, details, request_id, actor=actor, target=target, severity=severity)
        )

    @staticmethod
    def log_async(
        action: str,
        actor_ip: str | None = None,
        details: AuditDetails | None = None,
        request_id: str | None = None,
        *,
        actor: AuditActor | None = None,
        target: AuditTarget | None = None,
        severity: AuditSeverity = AuditSeverity.INFO,
    ) -> None:
        if not is_control_plane_task_admission_open():
            logger.warning("Audit log task rejected after shutdown admission closed: %s", action)
            return
        event = _build_event(
            action,
            actor_ip,
            details,
            request_id or get_request_id(),
            actor=actor,
            target=target,
            severity=severity,
        )
        task = asyncio.create_task(_write_audit_log(event), name=f"audit-log-{action}")
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


async def _write_audit_log(event: AuditEvent) -> None:
    """Fan the event out to every sink; one failing sink never starves the others."""

    for sink in get_audit_sinks():
        try:
            await sink.emit(event)
        except Exception:
            logger.warning("Audit sink %s failed for action %s", type(sink).__name__, event.action, exc_info=True)
