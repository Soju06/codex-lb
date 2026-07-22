from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Mapping
from time import perf_counter

import pytest
from sqlalchemy import select

import app.core.audit.service as audit_service_module
from app.core import shutdown as shutdown_state
from app.core.auth import generate_unique_account_id
from app.core.utils.time import utcnow
from app.db.models import AuditLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.unit


def _encode_jwt(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict[str, object]:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _wait_for_audit_log(action: str, *, attempts: int = 20) -> AuditLog:
    for _ in range(attempts):
        async with SessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc())
            )
            row = result.scalars().first()
            if row is not None:
                return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"audit log not written for action={action}")


@pytest.mark.asyncio
async def test_account_creation_writes_audit_log(async_client) -> None:
    email = "audit@example.com"
    raw_account_id = "acc_audit"
    expected_account_id = generate_unique_account_id(raw_account_id, email)

    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(_make_auth_json(raw_account_id, email)), "application/json")},
        headers={"x-request-id": "audit-account-create"},
    )

    assert response.status_code == 200

    audit_log = await _wait_for_audit_log("account_created")
    assert audit_log.request_id == "audit-account-create"
    assert audit_log.details == json.dumps({"account_id": expected_account_id})


@pytest.mark.asyncio
async def test_account_export_writes_audit_log(async_client) -> None:
    email = "audit-export@example.com"
    raw_account_id = "acc_audit_export"
    expected_account_id = generate_unique_account_id(raw_account_id, email)

    create_response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(_make_auth_json(raw_account_id, email)), "application/json")},
    )
    assert create_response.status_code == 200

    export_response = await async_client.post(
        f"/api/accounts/{expected_account_id}/export",
        headers={"x-request-id": "audit-account-export"},
    )

    assert export_response.status_code == 200

    audit_log = await _wait_for_audit_log("account_exported")
    assert audit_log.request_id == "audit-account-export"
    assert audit_log.details == json.dumps({"account_id": expected_account_id})


@pytest.mark.asyncio
async def test_audit_log_async_is_fire_and_forget(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks: list[asyncio.Task[None]] = []
    original_create_task = asyncio.create_task
    started = asyncio.Event()
    allow_write_finish = asyncio.Event()

    async def slow_write(action: str, actor_ip: str | None, details: dict | None, request_id: str | None) -> None:
        _ = (action, actor_ip, details, request_id)
        started.set()
        await allow_write_finish.wait()

    def capture_task(coro, *, name=None, context=None):
        task = original_create_task(coro, name=name, context=context)
        tasks.append(task)
        return task

    monkeypatch.setattr(audit_service_module, "_write_audit_log", slow_write)
    monkeypatch.setattr(audit_service_module.asyncio, "create_task", capture_task)

    started_at = perf_counter()
    audit_service_module.AuditService.log_async("settings_changed", details={"changed_fields": ["routing_strategy"]})
    elapsed = perf_counter() - started_at

    assert elapsed < 0.05
    await asyncio.wait_for(started.wait(), timeout=0.1)
    assert set(tasks) <= audit_service_module._AUDIT_LOG_TASKS

    drain = asyncio.create_task(audit_service_module.drain_audit_log_tasks(timeout_seconds=1))
    await asyncio.sleep(0)
    assert not drain.done()

    allow_write_finish.set()
    assert await drain is True
    assert audit_service_module._AUDIT_LOG_TASKS == set()


def test_audit_log_async_rejects_post_cutoff_work_without_creating_a_task(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def unexpected_write(*args, **kwargs) -> None:
        _ = (args, kwargs)
        raise AssertionError("post-cutoff audit write coroutine was created")

    def unexpected_task(*args, **kwargs) -> None:
        _ = (args, kwargs)
        raise AssertionError("post-cutoff audit task was created")

    monkeypatch.setattr(audit_service_module, "_write_audit_log", unexpected_write)
    monkeypatch.setattr(audit_service_module.asyncio, "create_task", unexpected_task)
    shutdown_state.close_control_plane_task_admission()

    started_at = perf_counter()
    with caplog.at_level(logging.WARNING, logger=audit_service_module.__name__):
        audit_service_module.AuditService.log_async("post_cutoff_settings_change")
    elapsed = perf_counter() - started_at

    assert elapsed < 0.05
    assert audit_service_module._AUDIT_LOG_TASKS == set()
    assert "Audit log task rejected after shutdown admission closed: post_cutoff_settings_change" in caplog.text


@pytest.mark.asyncio
async def test_audit_log_task_failure_is_consumed_and_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail_write(action: str, actor_ip: str | None, details: dict | None, request_id: str | None) -> None:
        _ = (action, actor_ip, details, request_id)
        raise RuntimeError("unexpected audit failure")

    monkeypatch.setattr(audit_service_module, "_write_audit_log", fail_write)

    with caplog.at_level(logging.WARNING, logger=audit_service_module.__name__):
        audit_service_module.AuditService.log_async("failure_test")
        assert await audit_service_module.drain_audit_log_tasks(timeout_seconds=1) is True

    assert audit_service_module._AUDIT_LOG_TASKS == set()
    assert "Audit log task failed unexpectedly: audit-log-failure_test" in caplog.text


@pytest.mark.asyncio
async def test_audit_log_drain_reports_overdue_task(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    allow_write_finish = asyncio.Event()

    async def blocked_write(action: str, actor_ip: str | None, details: dict | None, request_id: str | None) -> None:
        _ = (action, actor_ip, details, request_id)
        started.set()
        await allow_write_finish.wait()

    monkeypatch.setattr(audit_service_module, "_write_audit_log", blocked_write)
    audit_service_module.AuditService.log_async("timeout_test")
    await asyncio.wait_for(started.wait(), timeout=1)

    with caplog.at_level(logging.WARNING, logger=audit_service_module.__name__):
        assert await audit_service_module.drain_audit_log_tasks(timeout_seconds=0) is False

    assert "Audit log task did not drain before shutdown: audit-log-timeout_test" in caplog.text
    allow_write_finish.set()
    assert await audit_service_module.drain_audit_log_tasks(timeout_seconds=1) is True
    assert audit_service_module._AUDIT_LOG_TASKS == set()


@pytest.mark.asyncio
async def test_get_audit_logs_returns_entries(async_client) -> None:
    now = utcnow()
    async with SessionLocal() as session:
        session.add_all(
            [
                AuditLog(
                    action="settings_changed",
                    actor_ip="127.0.0.1",
                    details=json.dumps({"changed_fields": ["routing_strategy"]}),
                    request_id="audit-1",
                    timestamp=now,
                ),
                AuditLog(
                    action="login_failed",
                    actor_ip="127.0.0.2",
                    details=json.dumps({"method": "password"}),
                    request_id="audit-2",
                    timestamp=now,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/audit-logs", params={"action": "settings_changed", "limit": 10, "offset": 0}
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["action"] == "settings_changed"
    assert payload[0]["details"] == {"changed_fields": ["routing_strategy"]}
    assert payload[0]["requestId"] == "audit-1"
