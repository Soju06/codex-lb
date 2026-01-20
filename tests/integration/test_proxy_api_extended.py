from __future__ import annotations

import base64
import json

import pytest
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
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


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _extract_first_event(lines: list[str]) -> dict:
    for line in lines:
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("No SSE data event found")


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


@pytest.mark.asyncio
async def test_proxy_compact_not_implemented(async_client, monkeypatch):
    await _import_account(async_client, "acc_compact_ni", "ni@example.com")

    async def fake_compact(*_args, **_kwargs):
        raise NotImplementedError

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_implemented"


@pytest.mark.asyncio
async def test_proxy_compact_upstream_error_propagates(async_client, monkeypatch):
    await _import_account(async_client, "acc_compact_err", "err@example.com")

    async def fake_compact(*_args, **_kwargs):
        raise ProxyResponseError(502, {"error": {"code": "upstream_error", "message": "boom"}})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_proxy_stream_records_cached_and_reasoning_tokens(async_client, monkeypatch):
    expected_account_id = await _import_account(async_client, "acc_usage", "usage@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        usage = {
            "input_tokens": 10,
            "output_tokens": 5,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 2},
        }
        event = {"type": "response.completed", "response": {"id": "resp_1", "usage": usage}}
        yield _sse_event(event)

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_usage_123"
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog)
            .where(RequestLog.account_id == expected_account_id)
            .order_by(RequestLog.requested_at.desc())
        )
        log = result.scalars().first()
        assert log is not None
        assert log.request_id == request_id
        assert log.input_tokens == 10
        assert log.output_tokens == 5
        assert log.cached_input_tokens == 3
        assert log.reasoning_tokens == 2
        assert log.status == "success"


@pytest.mark.asyncio
async def test_proxy_stream_retries_rate_limit_then_success(async_client, monkeypatch):
    expected_account_id_1 = await _import_account(async_client, "acc_1", "one@example.com")
    expected_account_id_2 = await _import_account(async_client, "acc_2", "two@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        if account_id == "acc_1":
            event = {
                "type": "response.failed",
                "response": {"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
            }
            yield _sse_event(event)
            return
        event = {
            "type": "response.completed",
            "response": {"id": "resp_2", "usage": {"input_tokens": 1, "output_tokens": 1}},
        }
        yield _sse_event(event)

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        logs = list(result.scalars().all())
        assert len(logs) == 2
        by_account = {log.account_id: log for log in logs}
        assert by_account[expected_account_id_1].status == "error"
        assert by_account[expected_account_id_1].error_code == "rate_limit_exceeded"
        assert by_account[expected_account_id_1].error_message == "slow down"
        assert by_account[expected_account_id_2].status == "success"

    async with SessionLocal() as session:
        acc1 = await session.get(Account, expected_account_id_1)
        acc2 = await session.get(Account, expected_account_id_2)
        assert acc1 is not None
        assert acc2 is not None
        assert acc1.status == AccountStatus.RATE_LIMITED
        assert acc2.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_proxy_stream_usage_limit_returns_http_error(async_client, monkeypatch):
    expected_account_id = await _import_account(async_client, "acc_limit", "limit@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        raise ProxyResponseError(
            429,
            {
                "error": {
                    "type": "usage_limit_reached",
                    "message": "The usage limit has been reached",
                    "plan_type": "plus",
                    "resets_at": 1767612327,
                }
            },
        )
        if False:
            yield ""

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    response = await async_client.post("/backend-api/codex/responses", json=payload)
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["type"] == "usage_limit_reached"
    assert error["plan_type"] == "plus"
    assert error["resets_at"] == 1767612327

    async with SessionLocal() as session:
        acc = await session.get(Account, expected_account_id)
        assert acc is not None
        assert acc.status == AccountStatus.RATE_LIMITED
