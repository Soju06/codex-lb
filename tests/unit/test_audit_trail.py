from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from time import perf_counter

import pytest
from sqlalchemy import select

import app.core.audit.service as audit_service_module
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

    async def slow_write(action: str, actor_ip: str | None, details: dict | None, request_id: str | None) -> None:
        _ = (action, actor_ip, details, request_id)
        started.set()
        await asyncio.sleep(0.2)

    def capture_task(coro):
        task = original_create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(audit_service_module, "_write_audit_log", slow_write)
    monkeypatch.setattr(audit_service_module.asyncio, "create_task", capture_task)

    started_at = perf_counter()
    audit_service_module.AuditService.log_async("settings_changed", details={"changed_fields": ["routing_strategy"]})
    elapsed = perf_counter() - started_at

    assert elapsed < 0.05
    await asyncio.wait_for(started.wait(), timeout=0.1)
    await asyncio.gather(*tasks)


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
