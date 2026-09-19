from __future__ import annotations

import json
import logging
import uuid

import pytest
from sqlalchemy import select

import app.core.audit.service as audit_service_module
from app.core.audit.service import (
    AuditActor,
    AuditEvent,
    AuditService,
    AuditSeverity,
    AuditTarget,
    DatabaseAuditSink,
    get_audit_sinks,
    register_audit_sink,
    reset_audit_sinks,
)
from app.core.auth.dashboard_access import (
    OPERATOR_GRANTS,
    PRESET_ROLE_IDS,
    DashboardAuthMode,
    PresetRoleSlug,
    admin_principal,
    guest_principal,
    user_principal,
)
from app.db.models import AuditLog, DashboardRoleRecord, DashboardUser
from app.db.session import SessionLocal

pytestmark = pytest.mark.unit


def _operator_user() -> DashboardUser:
    role = DashboardRoleRecord(
        id=PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR],
        slug=PresetRoleSlug.OPERATOR.value,
        name="Operator",
        kind="preset",
    )
    user = DashboardUser(id=str(uuid.uuid4()), username="alice", role_id=role.id, status="active")
    user.role = role
    return user


def test_actor_from_user_principal_snapshots_the_account() -> None:
    user = _operator_user()
    principal = user_principal(user, OPERATOR_GRANTS, auth_method="password")

    actor = AuditActor.from_principal(principal)

    assert actor == AuditActor(user_id=user.id, username="alice", role_slug="operator", auth_method="password")


@pytest.mark.parametrize(
    ("auth_mode", "auth_method", "asserted_actor"),
    [
        (DashboardAuthMode.STANDARD, "local_bootstrap", None),
        (DashboardAuthMode.TRUSTED_HEADER, "trusted_header", "ops"),
        (DashboardAuthMode.DISABLED, "disabled", None),
    ],
)
def test_actor_from_implicit_admin_has_no_account(
    auth_mode: DashboardAuthMode, auth_method: str, asserted_actor: str | None
) -> None:
    principal = admin_principal(auth_mode=auth_mode, actor=asserted_actor, auth_method=auth_method)

    actor = AuditActor.from_principal(principal)

    # The trusted-header admin has no account row but the proxy asserted a name; keep it.
    assert actor == AuditActor(user_id=None, username=asserted_actor, role_slug="admin", auth_method=auth_method)


def test_actor_from_guest_principal() -> None:
    assert AuditActor.from_principal(guest_principal()) == AuditActor(
        user_id=None, username=None, role_slug="guest", auth_method="guest"
    )


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


class _FailingSink:
    async def emit(self, event: AuditEvent) -> None:
        raise RuntimeError("webhook down")


@pytest.fixture
def _sink_registry():
    reset_audit_sinks()
    try:
        yield
    finally:
        reset_audit_sinks()


def test_default_registry_is_the_database_sink(_sink_registry) -> None:
    sinks = get_audit_sinks()
    assert len(sinks) == 1
    assert isinstance(sinks[0], DatabaseAuditSink)


@pytest.mark.asyncio
async def test_event_is_built_once_with_sanitised_details_and_attribution(db_setup, _sink_registry) -> None:
    recording = _RecordingSink()
    register_audit_sink(recording)
    actor = AuditActor(user_id="u-1", username="alice", role_slug="operator", auth_method="password")
    target = AuditTarget("api_key", "k-1")

    await AuditService.log(
        "api_key_created",
        actor_ip="203.0.113.9",
        details={"key_id": "k-1", "key": "sk-clb-secret", "password": "nope"},
        request_id="req-1",
        actor=actor,
        target=target,
        severity=AuditSeverity.WARNING,
    )

    assert len(recording.events) == 1
    event = recording.events[0]
    assert event.action == "api_key_created"
    assert event.details == {"key_id": "k-1"}
    assert event.actor == actor
    assert event.target == target
    assert event.severity is AuditSeverity.WARNING
    assert event.timestamp.tzinfo is not None

    async with SessionLocal() as session:
        row = (await session.execute(select(AuditLog).where(AuditLog.request_id == "req-1"))).scalar_one()
    assert json.loads(row.details or "{}") == {"key_id": "k-1"}
    assert (row.actor_user_id, row.actor_username, row.actor_role_slug, row.auth_method) == (
        "u-1",
        "alice",
        "operator",
        "password",
    )
    assert (row.target_type, row.target_id, row.severity) == ("api_key", "k-1", "warning")


@pytest.mark.asyncio
async def test_failing_sink_does_not_block_other_sinks(
    db_setup, _sink_registry, caplog: pytest.LogCaptureFixture
) -> None:
    register_audit_sink(_FailingSink())
    recording = _RecordingSink()
    register_audit_sink(recording)

    with caplog.at_level(logging.WARNING, logger=audit_service_module.__name__):
        AuditService.log_async("sink_fanout_test", actor_ip="127.0.0.1")
        assert await audit_service_module.drain_audit_log_tasks(timeout_seconds=5) is True

    async with SessionLocal() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == "sink_fanout_test"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].severity == "info"
    assert rows[0].actor_user_id is None
    assert [event.action for event in recording.events] == ["sink_fanout_test"]
    assert "Audit sink _FailingSink failed for action sink_fanout_test" in caplog.text
    assert audit_service_module._AUDIT_LOG_TASKS == set()
