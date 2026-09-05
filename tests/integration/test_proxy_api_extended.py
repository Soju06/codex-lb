from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import Counter
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.requests import Request

import app.core.auth.dependencies as auth_dependencies
import app.core.resilience.network_recovery as network_recovery_module
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.auth.refresh import RefreshError
from app.core.clients import proxy as core_proxy
from app.core.clients.proxy import ProxyResponseError
from app.core.upstream_proxy import (
    ResolvedProxyEndpoint,
    ResolvedUpstreamRoute,
    UpstreamProxyRouteError,
)
from app.core.utils.sse import CODEX_KEEPALIVE_FRAME, SSE_KEEPALIVE_FRAME
from app.db.models import Account, AccountStatus, RequestLog, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.dependencies import ProxyContext, get_proxy_service_for_app
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._service.realtime_live import realtime_call_affinity_key
from app.modules.proxy._service.support import _signal_propagated_capacity_startup_ready

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _force_usage_weighted_routing(async_client) -> None:
    current = await async_client.get("/api/settings")
    assert current.status_code == 200
    payload = current.json()
    payload["routingStrategy"] = "usage_weighted"
    response = await async_client.put("/api/settings", json=payload)
    assert response.status_code == 200


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
    """Return the first non-synthesized SSE event payload. Skips the
    synthesized ``response.created`` envelope that the public-stream
    normalizer prepends when the upstream stream's first standard event is
    not ``response.created`` (see change
    ``normalize-v1-responses-openai-sdk-stream``)."""
    for line in lines:
        if not line.startswith("data: ") or line.startswith("data: [DONE]"):
            continue
        event = json.loads(line[6:])
        if event.get("type") == "codex.keepalive":
            continue
        if event.get("type") == "response.created":
            response = event.get("response")
            if isinstance(response, dict) and response.get("status") == "in_progress" and response.get("output") == []:
                continue
        return event
    raise AssertionError("No SSE data event found")


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


async def _create_realtime_api_key(async_client, name: str) -> tuple[dict[str, str], ApiKeyData]:
    response = await async_client.post("/api/api-keys/", json={"name": name})
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    return (
        {"authorization": f"Bearer {payload['key']}"},
        cast(ApiKeyData, SimpleNamespace(id=payload["id"])),
    )


async def _assert_realtime_call_request_log_error(
    async_client,
    *,
    request_id: str,
    api_key: ApiKeyData,
) -> None:
    service = get_proxy_service_for_app(async_client._transport.app)
    assert await service.drain_persistence_tasks(timeout_seconds=1)

    async with SessionLocal() as session:
        rows = (await session.execute(select(RequestLog).where(RequestLog.request_id == request_id))).scalars().all()
    assert len(rows) == 1
    persisted = rows[0]
    assert persisted.status == "error"
    assert persisted.transport == "http"
    assert persisted.api_key_id == api_key.id
    assert persisted.account_id is None
    assert persisted.error_code is None
    assert persisted.error_message is None

    response = await async_client.get("/api/request-logs?limit=100")
    assert response.status_code == 200, response.text
    public_rows = [entry for entry in response.json()["requests"] if entry["requestId"] == request_id]
    assert len(public_rows) == 1
    public_row = public_rows[0]
    assert public_row["status"] == "error"
    assert public_row["transport"] == "http"
    assert public_row["apiKeyId"] == api_key.id
    assert public_row["accountId"] is None
    assert public_row["errorCode"] is None
    assert public_row["errorMessage"] is None


def _sse_data_events(lines: list[str]) -> list[dict]:
    return [json.loads(line[6:]) for line in lines if line.startswith("data: ") and not line.startswith("data: [DONE]")]


async def _request_idle_heartbeat_stream(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    *,
    route: str,
    headers: dict[str, str],
    account_suffix: str,
) -> list[str]:
    await _import_account(
        async_client,
        f"acc_idle_heartbeat_{account_suffix}",
        f"idle-heartbeat-{account_suffix}@example.com",
    )

    settings = proxy_api_module.get_settings().model_copy(update={"sse_keepalive_interval_seconds": 0.005})
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module, "_HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS", 0.005)

    async def fake_stream(*args, **kwargs):
        del args, kwargs
        await asyncio.sleep(0.04)
        yield _sse_event(
            {
                "type": "codex.rate_limits",
                "plan_type": "pro",
                "rate_limits": {"allowed": True},
            }
        )
        yield _sse_event(
            {
                "type": "response.completed",
                "sequence_number": 1,
                "response": {
                    "id": f"resp_idle_heartbeat_{account_suffix}",
                    "object": "response",
                    "status": "completed",
                    "output": [],
                },
            }
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "heartbeat", "stream": True}
    async with async_client.stream(
        "POST",
        route,
        json=payload,
        headers={"accept": "text/event-stream", **headers},
    ) as response:
        assert response.status_code == 200
        return [line async for line in response.aiter_lines() if line]


@pytest.mark.asyncio
async def test_openapi_operation_ids_are_unique_and_thread_goal_methods_stable(async_client):
    response = await async_client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    operation_ids: list[str] = []
    http_methods = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method not in http_methods:
                continue
            operation_id = operation.get("operationId")
            assert isinstance(operation_id, str), f"{method.upper()} {path} has no operationId"
            operation_ids.append(operation_id)

    duplicate_ids = {operation_id: count for operation_id, count in Counter(operation_ids).items() if count > 1}
    assert duplicate_ids == {}

    thread_goal = schema["paths"]["/backend-api/codex/thread/goal/get"]
    assert thread_goal["get"]["operationId"] == "thread_goal_get_backend_api_codex_thread_goal_get_get"
    assert thread_goal["post"]["operationId"] == "thread_goal_get_backend_api_codex_thread_goal_get_post"

    assert schema["paths"]["/v1/responses"]["post"]["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/V1ResponsesRequest"
    }
    assert schema["paths"]["/backend-api/codex/responses/compact"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ResponsesCompactRequest"}


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
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_thread_goal_get_forwards_upstream_goal(async_client, monkeypatch, method):
    await _import_account(async_client, "acc_goal_get", "goal-get@example.com")
    calls = []

    async def fake_thread_goal(
        operation,
        payload,
        headers,
        access_token,
        account_id,
        *,
        method="POST",
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append(
            {
                "operation": operation,
                "payload": dict(payload),
                "access_token": access_token,
                "account_id": account_id,
                "method": method,
                "timeout_seconds": timeout_seconds,
                "session_id": headers.get("session_id"),
            }
        )
        return {
            "goal": {
                "threadId": payload["threadId"],
                "objective": "ship the proxy",
                "status": "active",
                "tokenBudget": None,
                "tokensUsed": 0,
                "timeBudgetSeconds": None,
                "timeUsedSeconds": 0,
                "createdAt": 1,
                "updatedAt": 1,
            }
        }

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)
    thread_id = "019debd9-2372-7f23-92b9-9f34002a6355"
    response = await async_client.request(
        method,
        "/backend-api/codex/thread/goal/get",
        params={"threadId": thread_id} if method == "GET" else None,
        json={"threadId": thread_id} if method == "POST" else None,
        headers={"session_id": "goal-session"},
    )

    assert response.status_code == 200
    assert response.json()["goal"]["objective"] == "ship the proxy"
    assert calls == [
        {
            "operation": "get",
            "payload": {"threadId": thread_id},
            "access_token": "access-token",
            "account_id": "acc_goal_get",
            "method": method,
            "timeout_seconds": calls[0]["timeout_seconds"],
            "session_id": "goal-session",
        }
    ]
    assert isinstance(calls[0]["timeout_seconds"], float)
    assert calls[0]["timeout_seconds"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "operation", "payload", "expected"),
    [
        (
            "/backend-api/codex/thread/goal/set",
            "set",
            {
                "threadId": "019debd9-2372-7f23-92b9-9f34002a6355",
                "objective": "ship the whole protocol",
                "status": "active",
            },
            {"goal": {"threadId": "019debd9-2372-7f23-92b9-9f34002a6355", "objective": "ship the whole protocol"}},
        ),
        (
            "/backend-api/codex/thread/goal/clear",
            "clear",
            {"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
            {"cleared": True},
        ),
    ],
)
async def test_thread_goal_mutations_forward_upstream(
    async_client,
    monkeypatch,
    endpoint,
    operation,
    payload,
    expected,
):
    await _import_account(async_client, f"acc_goal_{operation}", f"goal-{operation}@example.com")
    calls = []

    async def fake_thread_goal(
        current_operation,
        current_payload,
        headers,
        access_token,
        account_id,
        *,
        method="POST",
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append((current_operation, dict(current_payload), access_token, account_id, method, timeout_seconds))
        return expected

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(endpoint, json=payload)

    assert response.status_code == 200
    assert response.json() == expected
    assert calls[0][:5] == (operation, payload, "access-token", f"acc_goal_{operation}", "POST")
    assert isinstance(calls[0][5], float)
    assert calls[0][5] > 0


@pytest.mark.asyncio
async def test_thread_goal_get_returns_empty_goal_when_upstream_lacks_protocol(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_missing", "goal-missing@example.com")

    async def fake_thread_goal(*_args, **_kwargs):
        raise ProxyResponseError(404, {"error": {"code": "not_found", "message": "Not Found"}})

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 200
    assert response.json() == {"goal": None}


@pytest.mark.asyncio
async def test_thread_goal_get_propagates_non_protocol_404(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_gateway_404", "goal-gateway-404@example.com")

    async def fake_thread_goal(*_args, **_kwargs):
        raise ProxyResponseError(
            404,
            {"error": {"code": "upstream_error", "message": "Upstream error: HTTP 404 Not Found"}},
        )

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_thread_goal_get_propagates_thread_not_found(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_thread_not_found", "goal-thread-not-found@example.com")

    async def fake_thread_goal(*_args, **_kwargs):
        raise ProxyResponseError(
            404,
            {"error": {"code": "not_found", "message": "Thread not found"}},
        )

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_thread_goal_get_propagates_real_client_errors(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_rate_limited", "goal-rate@example.com")

    async def fake_thread_goal(*_args, **_kwargs):
        raise ProxyResponseError(
            429,
            {"error": {"code": "rate_limit_exceeded", "message": "slow down", "type": "rate_limit_error"}},
        )

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_thread_goal_get_rejects_malformed_json(async_client):
    await _import_account(async_client, "acc_goal_bad_json", "goal-bad-json@example.com")

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        content=b'{"threadId":',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "thread goal payload must be valid JSON"


@pytest.mark.asyncio
async def test_thread_goal_get_rejects_malformed_utf8_json(async_client):
    await _import_account(async_client, "acc_goal_bad_utf8", "goal-bad-utf8@example.com")

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        content=b'{"threadId":"\xff"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "thread goal payload must be valid JSON"


@pytest.mark.asyncio
async def test_thread_goal_get_propagates_selection_failures(async_client, monkeypatch):
    async def fake_select(*_args, **_kwargs):
        return proxy_module.AccountSelection(
            account=None,
            error_message="No scoped accounts are available",
            error_code="no_accounts",
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_thread_goal_get_maps_pool_usage_exhaustion_for_codex(async_client, monkeypatch):
    async def fake_select(*_args, **_kwargs):
        return proxy_module.AccountSelection(
            account=None,
            error_message="Usage limit reached",
            error_code="usage_limit_reached",
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "message": "Usage limit reached",
            "type": "usage_limit_reached",
            "code": "usage_limit_reached",
        }
    }


@pytest.mark.asyncio
async def test_thread_goal_set_propagates_upstream_errors(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_set_error", "goal-set-error@example.com")

    async def fake_thread_goal(*_args, **_kwargs):
        raise ProxyResponseError(404, {"error": {"code": "not_found", "message": "Not Found"}})

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/set",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355", "objective": "keep real errors"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_thread_goal_retry_failure_after_forced_refresh_updates_account_health(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_retry_error", "goal-retry-error@example.com")
    calls = 0
    handled: list[tuple[str, int]] = []

    async def fake_thread_goal(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProxyResponseError(401, {"error": {"code": "invalid_api_key", "message": "stale token"}})
        raise ProxyResponseError(
            429,
            {"error": {"code": "rate_limit_exceeded", "message": "still blocked", "type": "rate_limit_error"}},
        )

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    async def fake_handle_proxy_error(self, account, exc):
        handled.append((account.id, exc.status_code))

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_proxy_error", fake_handle_proxy_error)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/set",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355", "objective": "retry honestly"},
    )

    assert response.status_code == 429
    assert calls == 2
    assert len(handled) == 1
    assert handled[0][0].startswith("acc_goal_retry_error")
    assert handled[0][1] == 429


@pytest.mark.asyncio
async def test_thread_goal_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_invalidated_a", "goal-invalidated-a@example.com")
    await _import_account(async_client, "acc_goal_invalidated_b", "goal-invalidated-b@example.com")
    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

    async def fake_thread_goal(operation, payload, headers, access_token, account_id, **kwargs):
        del operation, payload, headers, access_token, kwargs
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise ProxyResponseError(401, {"error": {"code": "invalid_api_key", "message": "token invalidated"}})
        return {"goal": {"objective": "recovered"}}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/set",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355", "objective": "recover"},
    )

    assert response.status_code == 200
    assert response.json()["goal"]["objective"] == "recovered"
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
async def test_thread_goal_set_uses_active_account_when_budget_selection_is_empty(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_control", "goal-control@example.com")
    calls = []

    async def fake_select(*_args, **_kwargs):
        return proxy_module.AccountSelection(
            account=None,
            error_message="No active accounts available",
            error_code="no_accounts",
        )

    async def fake_thread_goal(
        operation,
        payload,
        headers,
        access_token,
        account_id,
        *,
        method="POST",
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append((operation, dict(payload), access_token, account_id, method, timeout_seconds))
        return {"cleared": True}

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select)
    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)
    payload = {"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"}

    response = await async_client.post("/backend-api/codex/thread/goal/clear", json=payload)

    assert response.status_code == 200
    assert response.json() == {"cleared": True}
    assert calls[0][:5] == ("clear", payload, "access-token", "acc_goal_control", "POST")
    assert isinstance(calls[0][5], float)
    assert calls[0][5] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "upstream_path", "payload"),
    [
        ("/backend-api/codex/analytics-events/events", "analytics-events/events", {"events": []}),
        (
            "/backend-api/codex/memories/trace_summarize",
            "memories/trace_summarize",
            {"model": "gpt-5.1", "raw_memories": []},
        ),
        (
            "/backend-api/codex/safety/arc",
            "safety/arc",
            {"decision": "allow"},
        ),
    ],
)
async def test_codex_control_json_endpoints_forward_upstream(
    async_client,
    monkeypatch,
    endpoint,
    upstream_path,
    payload,
):
    await _import_account(async_client, "acc_codex_control", "codex-control@example.com")
    calls = []

    async def fake_codex_control_request(
        path,
        *,
        method,
        payload: bytes | None,
        query_params,
        headers,
        access_token,
        account_id,
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append(
            {
                "path": path,
                "method": method,
                "payload": json.loads(payload or b"{}"),
                "query_params": dict(query_params),
                "session_id": headers.get("session_id"),
                "access_token": access_token,
                "account_id": account_id,
                "timeout_seconds": timeout_seconds,
            }
        )
        return core_proxy.CodexControlResponse(
            status_code=200,
            body=json.dumps({"ok": True}).encode("utf-8"),
            headers={"content-type": "application/json", "x-request-id": "upstream-request"},
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    response = await async_client.post(endpoint, json=payload, headers={"session_id": "control-session"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers["x-request-id"] == "upstream-request"
    assert calls == [
        {
            "path": upstream_path,
            "method": "POST",
            "payload": payload,
            "query_params": {},
            "session_id": "control-session",
            "access_token": "access-token",
            "account_id": "acc_codex_control",
            "timeout_seconds": calls[0]["timeout_seconds"],
        }
    ]
    assert isinstance(calls[0]["timeout_seconds"], float)
    assert calls[0]["timeout_seconds"] > 0


@pytest.mark.asyncio
async def test_codex_alpha_search_forwards_request_and_response(async_client, monkeypatch):
    await _import_account(async_client, "acc_codex_search", "codex-search@example.com")
    calls = []
    upstream_body = b'{"results":[{"title":"OpenAI","url":"https://openai.com/"}]}'

    async def fake_codex_control_request(
        path,
        *,
        method,
        payload: bytes | None,
        query_params,
        headers,
        access_token,
        account_id,
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append(
            {
                "path": path,
                "method": method,
                "payload": payload,
                "query_params": list(query_params),
                "session_id": headers.get("session_id"),
                "access_token": access_token,
                "account_id": account_id,
                "timeout_seconds": timeout_seconds,
            }
        )
        return core_proxy.CodexControlResponse(
            status_code=200,
            body=upstream_body,
            headers={
                "content-type": "application/json",
                "x-request-id": "search-request",
                "set-cookie": "must-not-leak=1",
            },
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    payload = b'{ "query": "OpenAI official website" }'

    response = await async_client.post(
        "/backend-api/codex/alpha/search?result_count=10",
        content=payload,
        headers={"content-type": "application/json", "session_id": "search-session"},
    )

    assert response.status_code == 200
    assert response.content == upstream_body
    assert response.headers["x-request-id"] == "search-request"
    assert "set-cookie" not in response.headers
    assert calls == [
        {
            "path": "alpha/search",
            "method": "POST",
            "payload": payload,
            "query_params": [("result_count", "10")],
            "session_id": "search-session",
            "access_token": "access-token",
            "account_id": "acc_codex_search",
            "timeout_seconds": calls[0]["timeout_seconds"],
        }
    ]
    assert isinstance(calls[0]["timeout_seconds"], float)
    assert calls[0]["timeout_seconds"] > 0


@pytest.mark.asyncio
async def test_codex_alpha_search_preserves_normalized_control_error_contract(async_client, monkeypatch):
    async def fake_codex_control_request(*_args, **_kwargs):
        raise ProxyResponseError(
            429,
            {
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "Search rate limit exceeded",
                    "type": "rate_limit_error",
                }
            },
        )

    monkeypatch.setattr(proxy_module.ProxyService, "codex_control_request", fake_codex_control_request)

    response = await async_client.post(
        "/backend-api/codex/alpha/search",
        json={"query": "OpenAI official website"},
    )

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "rate_limit_exceeded",
            "message": "Search rate limit exceeded",
            "type": "rate_limit_error",
        }
    }


@pytest.mark.asyncio
async def test_codex_realtime_call_normalizes_upstream_control_error(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    auth_headers, _api_key = await _create_realtime_api_key(async_client, "realtime-upstream-denial")
    secrets = (
        "private-upstream-account",
        "private-upstream-bearer",
        "private-upstream-call-id",
        "private-upstream-denial-code",
    )

    async def fail_codex_control_request(*_args, **_kwargs):
        raise ProxyResponseError(
            403,
            {
                "error": {
                    "code": secrets[3],
                    "message": f"denied {secrets[0]} with {secrets[1]} for {secrets[2]}",
                    "type": "permission_error",
                }
            },
        )

    monkeypatch.setattr(proxy_module.ProxyService, "codex_control_request", fail_codex_control_request)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        response = await async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\na=ice-pwd:private-sdp-credential\r\n",
            headers={"content-type": "application/sdp", **auth_headers},
        )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "realtime_call_unavailable",
            "message": "Realtime call could not be created",
            "type": "server_error",
        }
    }
    assert "proxy_error_response request_id=" in caplog.text
    assert 'code="realtime_call_unavailable"' in caplog.text
    combined_output = response.text + caplog.text
    for secret in (*secrets, "private-sdp-credential"):
        assert secret not in combined_output


@pytest.mark.asyncio
async def test_codex_realtime_call_requires_api_key_even_when_global_auth_is_disabled(
    async_client,
    monkeypatch,
):
    upstream_called = False

    async def fake_codex_control_request(*_args, **_kwargs):
        nonlocal upstream_called
        upstream_called = True
        raise AssertionError("unauthenticated realtime call must not reach upstream")

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    response = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert upstream_called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "call_id"),
    [
        ("/v1/realtime/calls/rtc_123", "rtc_123"),
        ("https://api.openai.com/v1/realtime/calls/rtc_absolute", "rtc_absolute"),
        ("/v1/realtime/calls/rtc_query?intent=quicksilver&token=private-query", "rtc_query"),
        (
            "https://api.openai.com/v1/realtime/calls/rtc_query_fragment?intent=quicksilver#opaque-fragment",
            "rtc_query_fragment",
        ),
    ],
    ids=["root-relative", "absolute-https", "root-relative-query", "absolute-query-fragment"],
)
async def test_codex_realtime_call_accepts_valid_key_for_remote_client_when_global_auth_is_disabled(
    async_client, monkeypatch, location: str, call_id: str
):
    account_id = await _import_account(async_client, "acc_codex_realtime", "codex-realtime@example.com")
    auth_headers, api_key = await _create_realtime_api_key(async_client, "realtime-forward")
    monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _request: False)
    monkeypatch.setattr(
        auth_dependencies,
        "_is_proxy_unauthenticated_socket_peer_allowed",
        lambda _request: False,
    )
    calls = []

    async def fake_codex_control_request(
        path,
        *,
        method,
        payload: bytes | None,
        query_params,
        headers,
        access_token,
        account_id,
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append((path, method, payload, headers.get("content-type"), access_token, account_id, timeout_seconds))
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": location},
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    response = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp", **auth_headers},
    )

    assert response.status_code == 201
    assert response.content == b"v=answer\r\n"
    assert response.headers["location"] == location
    assert calls == [
        (
            "realtime/calls",
            "POST",
            b"v=offer\r\n",
            "application/sdp",
            "access-token",
            "acc_codex_realtime",
            calls[0][6],
        )
    ]
    assert isinstance(calls[0][6], float)
    assert calls[0][6] > 0

    affinity_key = realtime_call_affinity_key(call_id, api_key)
    async with SessionLocal() as session:
        binding = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == affinity_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
    assert binding.account_id == account_id
    assert call_id not in binding.key
    for sensitive_location_detail in ("intent=quicksilver", "private-query", "opaque-fragment"):
        assert sensitive_location_detail not in binding.key


@pytest.mark.asyncio
async def test_codex_realtime_call_awaits_durable_binding_before_success_log(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_id = await _import_account(
        async_client,
        "acc_codex_realtime_awaited_binding",
        "codex-realtime-awaited-binding@example.com",
    )
    auth_headers, api_key = await _create_realtime_api_key(async_client, "realtime-awaited-binding")
    binding_started = asyncio.Event()
    allow_binding = asyncio.Event()
    upstream_calls = 0
    request_id = "req-realtime-awaited-binding"
    original_bind = proxy_module.ProxyService.bind_realtime_call_owner

    async def fake_codex_control_request(*_args, **_kwargs):
        nonlocal upstream_calls
        upstream_calls += 1
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/realtime/calls/rtc_awaited_binding"},
        )

    async def delayed_binding(self, **kwargs):
        binding_started.set()
        await allow_binding.wait()
        return await original_bind(self, **kwargs)

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_module.ProxyService, "bind_realtime_call_owner", delayed_binding)

    request_task = asyncio.create_task(
        async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\n",
            headers={"content-type": "application/sdp", "x-request-id": request_id, **auth_headers},
        )
    )
    try:
        await asyncio.wait_for(binding_started.wait(), timeout=1)
        assert request_task.done() is False
        async with SessionLocal() as session:
            rows_before_binding = (
                (await session.execute(select(RequestLog).where(RequestLog.request_id == request_id))).scalars().all()
            )
        assert rows_before_binding == []

        allow_binding.set()
        response = await asyncio.wait_for(request_task, timeout=1)
    finally:
        allow_binding.set()
        if not request_task.done():
            request_task.cancel()
        await asyncio.gather(request_task, return_exceptions=True)

    assert response.status_code == 201
    assert upstream_calls == 1
    service = get_proxy_service_for_app(async_client._transport.app)
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    async with SessionLocal() as session:
        [persisted] = (
            (await session.execute(select(RequestLog).where(RequestLog.request_id == request_id))).scalars().all()
        )
        binding = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == realtime_call_affinity_key("rtc_awaited_binding", api_key),
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
    assert persisted.status == "success"
    assert persisted.account_id is None
    assert persisted.api_key_id == api_key.id
    assert binding.account_id == account_id


@pytest.mark.asyncio
async def test_codex_realtime_call_selection_logs_redact_account_identifiers(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    account_id = await _import_account(
        async_client,
        "acc_codex_realtime_selection_private",
        "codex-realtime-selection-private@example.com",
    )
    auth_headers, _api_key = await _create_realtime_api_key(async_client, "realtime-selection-private")

    async def fake_codex_control_request(*_args, **_kwargs):
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/realtime/calls/rtc_selection_private"},
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    service = get_proxy_service_for_app(async_client._transport.app)
    stale_lease = await service._load_balancer.acquire_account_lease(
        account_id,
        kind="response_create",
    )
    assert stale_lease is not None
    object.__setattr__(stale_lease, "acquired_at", float("-inf"))

    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = await async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\n",
            headers={"content-type": "application/sdp", **auth_headers},
        )

    assert response.status_code == 201
    assert "<redacted>" in caplog.text
    assert account_id not in caplog.text


@pytest.mark.asyncio
async def test_codex_realtime_call_binds_account_after_forced_refresh_success(async_client, monkeypatch):
    account_id = await _import_account(
        async_client,
        "acc_codex_realtime_refresh",
        "codex-realtime-refresh@example.com",
    )
    auth_headers, api_key = await _create_realtime_api_key(async_client, "realtime-refresh")
    calls = 0
    refresh_forces: list[bool] = []

    async def fake_codex_control_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "expired"}},
            )
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/realtime/calls/rtc_refreshed"},
        )

    async def fake_ensure_fresh(
        self,
        account,
        *,
        force=False,
        timeout_seconds=None,
        privacy_policy=core_proxy.CodexControlRequestPrivacyPolicy.STANDARD,
    ):
        del self
        assert timeout_seconds is not None
        assert privacy_policy is core_proxy.CodexControlRequestPrivacyPolicy.PRIVATE_REALTIME
        refresh_forces.append(force)
        return account

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp", **auth_headers},
    )

    assert response.status_code == 201
    assert calls == 2
    assert refresh_forces == [False, True]
    affinity_key = realtime_call_affinity_key("rtc_refreshed", api_key)
    async with SessionLocal() as session:
        binding = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == affinity_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
    assert binding.account_id == account_id


@pytest.mark.asyncio
@pytest.mark.parametrize("binding_failure", ["insert-failure", "owner-conflict"])
async def test_codex_realtime_call_binding_failure_fails_closed_without_replay(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    binding_failure: str,
) -> None:
    account_id = await _import_account(
        async_client,
        f"acc_codex_realtime_binding_{binding_failure}",
        f"codex-realtime-binding-{binding_failure}@example.com",
    )
    auth_headers, api_key = await _create_realtime_api_key(async_client, f"realtime-binding-{binding_failure}")
    upstream_calls = 0
    request_id = f"req-realtime-binding-{binding_failure}"

    async def fake_codex_control_request(*_args, **_kwargs):
        nonlocal upstream_calls
        upstream_calls += 1
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/realtime/calls/rtc_binding"},
        )

    async def fail_binding(*_args, **_kwargs):
        raise RuntimeError(f"{binding_failure} for {account_id}")

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_module.ProxyService, "bind_realtime_call_owner", fail_binding)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        response = await async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\n",
            headers={"content-type": "application/sdp", "x-request-id": request_id, **auth_headers},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "realtime_call_binding_failed"
    assert upstream_calls == 1
    binding_records = [
        record
        for record in caplog.records
        if record.name == proxy_api_module.__name__
        and record.getMessage() == "Failed to persist realtime call owner binding"
    ]
    assert len(binding_records) == 1
    assert account_id not in caplog.text
    await _assert_realtime_call_request_log_error(async_client, request_id=request_id, api_key=api_key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_branch", "expected_log"),
    [
        (
            "before-upstream",
            "Codex control request budget exhausted before upstream call",
        ),
        (
            "before-forced-refresh",
            "Codex control request budget exhausted before forced refresh retry",
        ),
        (
            "forced-refresh-connect",
            "Codex control forced refresh/connect failed",
        ),
    ],
    ids=["before-upstream", "before-forced-refresh", "forced-refresh-connect"],
)
async def test_codex_realtime_call_failure_logs_redact_account_identifiers(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_branch: str,
    expected_log: str,
) -> None:
    account_id = await _import_account(
        async_client,
        f"acc_codex_realtime_log_{failure_branch}",
        f"codex-realtime-log-{failure_branch}@example.com",
    )
    auth_headers, _api_key = await _create_realtime_api_key(
        async_client,
        f"realtime-log-{failure_branch}",
    )

    async def fake_codex_control_request(*_args, **_kwargs):
        if failure_branch == "before-upstream":
            raise AssertionError("budget exhaustion must prevent the upstream call")
        raise ProxyResponseError(
            401,
            {"error": {"code": "invalid_api_key", "message": "expired"}},
        )

    async def fake_fresh_with_failover(self, account, *, force=False, **_kwargs):
        del self
        if failure_branch == "forced-refresh-connect" and force:
            raise asyncio.TimeoutError(f"refresh failed for {account_id}")
        return account

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_ensure_previsible_unary_fresh_with_failover",
        fake_fresh_with_failover,
    )
    if failure_branch == "before-upstream":
        remaining = iter((1.0, 0.0))
        monkeypatch.setattr(proxy_module, "_remaining_budget_seconds", lambda _deadline: next(remaining))
    elif failure_branch == "before-forced-refresh":
        remaining = iter((1.0, 1.0, 0.0))
        monkeypatch.setattr(proxy_module, "_remaining_budget_seconds", lambda _deadline: next(remaining))
    else:
        monkeypatch.setattr(proxy_module, "_remaining_budget_seconds", lambda _deadline: 1.0)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        response = await async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\n",
            headers={
                "content-type": "application/sdp",
                "x-request-id": f"req-private-{failure_branch}",
                **auth_headers,
            },
        )

    assert response.status_code >= 400
    matching_records = [
        record
        for record in caplog.records
        if record.name == "app.modules.proxy.service" and expected_log in record.getMessage()
    ]
    assert len(matching_records) == 1
    assert "account_id=<redacted>" in matching_records[0].getMessage()
    assert account_id not in caplog.text

    service = get_proxy_service_for_app(async_client._transport.app)
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    async with SessionLocal() as session:
        persisted = (
            await session.execute(select(RequestLog).where(RequestLog.request_id == f"req-private-{failure_branch}"))
        ).scalar_one()
    assert persisted.status == "error"
    for private_failure_field in (
        "error_code",
        "error_message",
        "failure_phase",
        "failure_detail",
        "failure_exception_type",
        "upstream_status_code",
        "upstream_error_code",
        "bridge_stage",
    ):
        assert getattr(persisted, private_failure_field) is None


@pytest.mark.asyncio
async def test_codex_realtime_call_shared_freshness_budget_log_redacts_account_id(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    account_id = await _import_account(
        async_client,
        "acc_codex_realtime_shared_budget",
        "codex-realtime-shared-budget@example.com",
    )
    auth_headers, _api_key = await _create_realtime_api_key(
        async_client,
        "realtime-shared-budget",
    )

    async def unexpected_codex_control_request(*_args, **_kwargs):
        raise AssertionError("freshness budget exhaustion must prevent the upstream call")

    remaining_budget = iter((1.0, 0.0))
    monkeypatch.setattr(proxy_module, "core_codex_control_request", unexpected_codex_control_request)
    monkeypatch.setattr(proxy_module, "_remaining_budget_seconds", lambda _deadline: next(remaining_budget))

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        response = await async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\n",
            headers={
                "content-type": "application/sdp",
                "x-request-id": "req-private-shared-budget",
                **auth_headers,
            },
        )

    assert response.status_code >= 400
    matching_records = [
        record
        for record in caplog.records
        if record.name == "app.modules.proxy.service"
        and "request budget exhausted before freshness check" in record.getMessage()
    ]
    assert len(matching_records) == 1
    assert "account_id=<redacted>" in matching_records[0].getMessage()
    assert account_id not in matching_records[0].getMessage()
    assert matching_records[0].exc_info is None


@pytest.mark.asyncio
async def test_codex_realtime_call_shared_refresh_failover_logs_redact_account_and_traceback(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    imported_account_ids = {
        await _import_account(
            async_client,
            "acc_codex_realtime_shared_refresh_a",
            "codex-realtime-shared-refresh-a@example.com",
        ),
        await _import_account(
            async_client,
            "acc_codex_realtime_shared_refresh_b",
            "codex-realtime-shared-refresh-b@example.com",
        ),
    }
    auth_headers, _api_key = await _create_realtime_api_key(
        async_client,
        "realtime-shared-refresh",
    )
    first_account_id: str | None = None
    refresh_secret = "private-refresh-traceback-secret"

    async def fake_ensure_fresh(
        self,
        account,
        *,
        force=False,
        timeout_seconds=None,
        privacy_policy=core_proxy.CodexControlRequestPrivacyPolicy.STANDARD,
    ):
        nonlocal first_account_id
        del self, force
        assert timeout_seconds is not None
        assert privacy_policy is core_proxy.CodexControlRequestPrivacyPolicy.PRIVATE_REALTIME
        if first_account_id is None:
            first_account_id = account.id
        if account.id == first_account_id:
            raise RefreshError(
                "transport_error",
                f"oauth timed out for {account.id}: {refresh_secret}",
                False,
                transport_error=True,
            )
        return account

    async def fake_codex_control_request(*_args, **_kwargs):
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/realtime/calls/rtc_shared_refresh"},
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = await async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\n",
            headers={
                "content-type": "application/sdp",
                "x-request-id": "req-private-shared-refresh",
                **auth_headers,
            },
        )

    assert response.status_code == 201
    assert first_account_id in imported_account_ids
    refresh_records = [
        record
        for record in caplog.records
        if record.name == "app.modules.proxy._service.support"
        and "codex_control_realtime_calls refresh failed" in record.getMessage()
    ]
    assert len(refresh_records) == 1
    assert "account_id=<redacted>" in refresh_records[0].getMessage()
    assert refresh_records[0].exc_info is None
    assert refresh_secret not in caplog.handler.format(refresh_records[0])

    health_records = [
        record
        for record in caplog.records
        if record.name == "app.modules.proxy.service"
        and record.getMessage().startswith("Recorded transient account error ")
    ]
    assert len(health_records) == 1
    assert "account_id=<redacted>" in health_records[0].getMessage()


@pytest.mark.asyncio
async def test_codex_realtime_call_auth_manager_backfill_log_redacts_account_and_traceback(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    account_id = await _import_account(
        async_client,
        "acc_codex_realtime_auth_manager_backfill",
        "codex-realtime-auth-manager-backfill@example.com",
    )
    auth_headers, _api_key = await _create_realtime_api_key(
        async_client,
        "realtime-auth-manager-backfill",
    )
    async with SessionLocal() as session:
        account = (await session.execute(select(Account).where(Account.id == account_id))).scalar_one()
        account.chatgpt_account_id = None
        await session.commit()

    private_exception_detail = "private-auth-manager-backfill-traceback"

    async def fail_metadata_backfill(
        self,
        persisted_account_id: str,
        **_kwargs,
    ) -> bool:
        del self
        assert persisted_account_id == account_id
        raise RuntimeError(private_exception_detail)

    async def fake_codex_control_request(*_args, **_kwargs):
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={
                "content-type": "application/sdp",
                "location": "/v1/realtime/calls/rtc_auth_manager_backfill",
            },
        )

    monkeypatch.setattr(
        AccountsRepository,
        "update_account_metadata",
        fail_metadata_backfill,
    )
    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        response = await async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\n",
            headers={
                "content-type": "application/sdp",
                "x-request-id": "req-private-auth-manager-backfill",
                **auth_headers,
            },
        )

    assert response.status_code == 201
    matching_records = [
        record
        for record in caplog.records
        if record.name == "app.modules.accounts.auth_manager"
        and "Failed to persist chatgpt_account_id" in record.getMessage()
    ]
    assert len(matching_records) == 1
    assert "account_id=<redacted>" in matching_records[0].getMessage()
    assert matching_records[0].exc_info is None
    assert account_id not in caplog.text
    assert private_exception_detail not in caplog.handler.format(matching_records[0])


@pytest.mark.asyncio
async def test_codex_realtime_call_process_network_recovery_logs_redact_account_id(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    account_id = await _import_account(
        async_client,
        "acc_codex_realtime_process_network",
        "codex-realtime-process-network@example.com",
    )
    auth_headers, _api_key = await _create_realtime_api_key(
        async_client,
        "realtime-process-network",
    )
    refresh_calls = 0

    async def fake_ensure_fresh(
        self,
        account,
        *,
        force=False,
        timeout_seconds=None,
        redact_sensitive_details=False,
    ):
        nonlocal refresh_calls
        del self, force
        assert timeout_seconds is not None
        assert redact_sensitive_details is True
        refresh_calls += 1
        if refresh_calls == 1:
            raise RefreshError(
                "transport_error",
                f"network recovery failed for {account.id}",
                False,
                transport_error=True,
                transport_error_code=network_recovery_module.PROCESS_NETWORK_UNAVAILABLE_CODE,
                retryable_same_contract=True,
            )
        return account

    async def fake_codex_control_request(*_args, **_kwargs):
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/realtime/calls/rtc_process_network"},
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(network_recovery_module, "backoff_seconds", lambda _attempt: 0.0)
    monkeypatch.setattr(
        network_recovery_module,
        "rotate_shared_http_transport",
        AsyncMock(return_value="rotated"),
    )
    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = await async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\n",
            headers={
                "content-type": "application/sdp",
                "x-request-id": "req-private-process-network",
                **auth_headers,
            },
        )

    assert response.status_code == 201
    assert refresh_calls == 2
    recovery_records = [
        record
        for record in caplog.records
        if record.name == network_recovery_module.__name__ and "process_network_recovery stage=" in record.getMessage()
    ]
    assert len(recovery_records) == 2
    assert all("account_id=<redacted>" in record.getMessage() for record in recovery_records)
    assert all(account_id not in record.getMessage() for record in recovery_records)
    assert all(record.exc_info is None for record in recovery_records)


@pytest.mark.asyncio
async def test_codex_realtime_call_shared_upstream_failover_log_redacts_account_id(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _import_account(
        async_client,
        "acc_codex_realtime_shared_upstream_a",
        "codex-realtime-shared-upstream-a@example.com",
    )
    await _import_account(
        async_client,
        "acc_codex_realtime_shared_upstream_b",
        "codex-realtime-shared-upstream-b@example.com",
    )
    auth_headers, _api_key = await _create_realtime_api_key(
        async_client,
        "realtime-shared-upstream",
    )
    rejected_upstream_account_id: str | None = None

    async def fake_codex_control_request(*_args, account_id=None, **_kwargs):
        nonlocal rejected_upstream_account_id
        if rejected_upstream_account_id is None:
            rejected_upstream_account_id = account_id
        if account_id == rejected_upstream_account_id:
            raise ProxyResponseError(
                502,
                {
                    "error": {
                        "code": "upstream_unavailable",
                        "message": "upstream connection reset before dispatch",
                    }
                },
                failure_phase="connect",
            )
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/realtime/calls/rtc_shared_upstream"},
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = await async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\n",
            headers={
                "content-type": "application/sdp",
                "x-request-id": "req-private-shared-upstream",
                **auth_headers,
            },
        )

    assert response.status_code == 201
    health_records = [
        record
        for record in caplog.records
        if record.name == "app.modules.proxy.service"
        and record.getMessage().startswith("Recorded transient account error ")
    ]
    assert len(health_records) == 1
    assert "account_id=<redacted>" in health_records[0].getMessage()


@pytest.mark.asyncio
async def test_ordinary_codex_control_shared_refresh_log_keeps_account_diagnostics(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    imported_account_ids = {
        await _import_account(
            async_client,
            "acc_codex_control_shared_refresh_a",
            "codex-control-shared-refresh-a@example.com",
        ),
        await _import_account(
            async_client,
            "acc_codex_control_shared_refresh_b",
            "codex-control-shared-refresh-b@example.com",
        ),
    }
    first_account_id: str | None = None
    refresh_diagnostic = "ordinary-refresh-diagnostic"

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        nonlocal first_account_id
        del self, force
        assert timeout_seconds is not None
        if first_account_id is None:
            first_account_id = account.id
        if account.id == first_account_id:
            raise RefreshError(
                "transport_error",
                f"oauth timed out for {account.id}: {refresh_diagnostic}",
                False,
                transport_error=True,
            )
        return account

    async def fake_codex_control_request(*_args, **_kwargs):
        return core_proxy.CodexControlResponse(
            status_code=200,
            body=b'{"ok":true}',
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        response = await async_client.post(
            "/backend-api/codex/alpha/search",
            json={"query": "ordinary diagnostics"},
            headers={"x-request-id": "req-ordinary-shared-refresh"},
        )

    assert response.status_code == 200
    assert first_account_id in imported_account_ids
    refresh_records = [
        record
        for record in caplog.records
        if record.name == "app.modules.proxy._service.support"
        and "codex_control_alpha_search refresh failed" in record.getMessage()
    ]
    assert len(refresh_records) == 1
    assert f"account_id={first_account_id}" in refresh_records[0].getMessage()
    assert refresh_records[0].exc_info is not None
    assert refresh_diagnostic in caplog.handler.format(refresh_records[0])


@pytest.mark.asyncio
async def test_codex_realtime_call_failure_request_log_is_content_free_for_public_api(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_id = await _import_account(
        async_client,
        "acc_private_call_create_leak",
        "private-call-create-leak@example.com",
    )
    auth_headers, api_key = await _create_realtime_api_key(async_client, "private-call-create-leak")
    malicious_call_id = "rtc_private_call_create_secret"
    malicious_route_mode = "account_bound_secret_mode"
    malicious_pool_id = "pool_private_call_create_secret"
    malicious_endpoint_id = "ep_private_call_create_secret"
    malicious_fail_closed_reason = "malicious_no_healthy_endpoint_secret"
    malicious_error_code = "malicious-private-upstream-code"
    malicious_error_message = (
        f"denied {account_id} call={malicious_call_id} token=private-bearer-secret "
        f"query=intent=quicksilver path=/v1/realtime/calls/{malicious_call_id}"
    )
    malicious_failure_phase = "private_upstream_connect"
    malicious_failure_detail = f"bridge detail for {malicious_call_id} via {malicious_endpoint_id}"
    malicious_failure_exception_type = "MaliciousPrivateUpstreamError"
    malicious_bridge_stage = "private_call_create_bridge"
    malicious_sdp = b"v=0\r\na=ice-pwd:private-sdp-credential-secret\r\n"
    route = ResolvedUpstreamRoute(
        mode=malicious_route_mode,
        pool_id=malicious_pool_id,
        endpoint=ResolvedProxyEndpoint(
            malicious_endpoint_id,
            "http",
            "proxy.private-call-create.test",
            8080,
            "proxy-user-secret",
            "proxy-pass-secret",
        ),
    )
    forbidden_secrets = (
        account_id,
        "private-call-create-leak@example.com",
        malicious_call_id,
        malicious_route_mode,
        malicious_pool_id,
        malicious_endpoint_id,
        malicious_fail_closed_reason,
        malicious_error_code,
        malicious_error_message,
        malicious_failure_phase,
        malicious_failure_detail,
        malicious_failure_exception_type,
        malicious_bridge_stage,
        "private-sdp-credential-secret",
        "private-bearer-secret",
        "proxy-user-secret",
        "proxy-pass-secret",
        "proxy.private-call-create.test",
        "intent=quicksilver",
        f"/v1/realtime/calls/{malicious_call_id}",
        "upstream_proxy_unavailable",
        "Upstream proxy route unavailable",
    )

    async def fake_resolve_route(self, account, *, operation):
        del self
        assert account.id == account_id
        assert operation == "codex_control_realtime_calls"
        return route

    async def fail_closed_codex_control_request(*_args, **kwargs):
        route_trace = kwargs.get("route_trace")
        resolved_route = kwargs.get("route")
        if route_trace is not None and resolved_route is not None:
            route_trace.record(route=resolved_route, fallback_used=True)
        raise UpstreamProxyRouteError(malicious_fail_closed_reason, account_id=account_id)

    async def rich_failure_codex_control_request(*_args, **kwargs):
        route_trace = kwargs.get("route_trace")
        resolved_route = kwargs.get("route")
        if route_trace is not None and resolved_route is not None:
            route_trace.record(route=resolved_route, fallback_used=True)
        raise ProxyResponseError(
            403,
            {
                "error": {
                    "code": malicious_error_code,
                    "message": malicious_error_message,
                    "type": "permission_error",
                }
            },
            failure_phase=malicious_failure_phase,
            failure_detail=malicious_failure_detail,
            failure_exception_type=malicious_failure_exception_type,
            upstream_status_code=403,
            upstream_error_code=malicious_error_code,
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_upstream_route_for_account", fake_resolve_route)

    async def _assert_content_free_public_log(*, request_id: str, response_text: str) -> None:
        service = get_proxy_service_for_app(async_client._transport.app)
        assert await service.drain_persistence_tasks(timeout_seconds=1)

        request_logs = await async_client.get("/api/request-logs?limit=100")
        assert request_logs.status_code == 200, request_logs.text
        matching_logs = [entry for entry in request_logs.json()["requests"] if entry["requestId"] == request_id]
        assert len(matching_logs) == 1
        public_log = matching_logs[0]

        assert public_log["requestId"] == request_id
        assert public_log["requestKind"] == "normal"
        assert public_log["status"] == "error"
        assert public_log["transport"] == "http"
        assert public_log["apiKeyId"] == api_key.id

        for private_public_field in (
            "accountId",
            "conversationId",
            "errorCode",
            "errorMessage",
            "failurePhase",
            "failureDetail",
            "failureExceptionType",
            "upstreamStatusCode",
            "upstreamErrorCode",
            "bridgeStage",
            "planType",
            "model",
        ):
            assert public_log.get(private_public_field) in (None, "")

        serialized_public_log = json.dumps(public_log, sort_keys=True)
        for secret in forbidden_secrets:
            assert secret not in serialized_public_log
            assert secret not in response_text

        async with SessionLocal() as session:
            persisted = (
                await session.execute(select(RequestLog).where(RequestLog.request_id == request_id))
            ).scalar_one()

        assert persisted.status == "error"
        assert persisted.request_kind == "normal"
        assert persisted.transport == "http"
        assert persisted.api_key_id == api_key.id
        assert persisted.account_id is None
        assert persisted.upstream_proxy_fail_closed_reason is None
        for private_failure_field in (
            "error_code",
            "error_message",
            "failure_phase",
            "failure_detail",
            "failure_exception_type",
            "upstream_status_code",
            "upstream_error_code",
            "bridge_stage",
            "upstream_proxy_route_mode",
            "upstream_proxy_pool_id",
            "upstream_proxy_endpoint_id",
            "upstream_proxy_fallback_used",
            "upstream_proxy_fail_closed_reason",
            "conversation_id",
            "plan_type",
        ):
            assert getattr(persisted, private_failure_field) is None
        assert not persisted.model

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fail_closed_codex_control_request)
    fail_closed_request_id = "req-private-call-create-fail-closed"
    fail_closed_response = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=malicious_sdp,
        headers={
            "content-type": "application/sdp",
            "authorization": auth_headers["authorization"],
            "x-request-id": fail_closed_request_id,
            "user-agent": "private-call-create-agent/1.0",
        },
    )
    assert fail_closed_response.status_code == 502
    assert fail_closed_response.json() == {
        "error": {
            "code": "realtime_call_unavailable",
            "message": "Realtime call could not be created",
            "type": "server_error",
        }
    }
    await _assert_content_free_public_log(
        request_id=fail_closed_request_id,
        response_text=fail_closed_response.text,
    )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", rich_failure_codex_control_request)
    rich_request_id = "req-private-call-create-rich-failure"
    rich_response = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=malicious_sdp,
        headers={
            "content-type": "application/sdp",
            "authorization": auth_headers["authorization"],
            "x-request-id": rich_request_id,
            "user-agent": "private-call-create-agent/1.0",
        },
    )
    assert rich_response.status_code == 403
    assert rich_response.json() == {
        "error": {
            "code": "realtime_call_unavailable",
            "message": "Realtime call could not be created",
            "type": "server_error",
        }
    }
    await _assert_content_free_public_log(
        request_id=rich_request_id,
        response_text=rich_response.text,
    )


@pytest.mark.asyncio
async def test_codex_realtime_call_pretransport_failure_is_fixed_and_credential_safe(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    account_id = await _import_account(
        async_client,
        "acc_codex_realtime_decrypt_failure",
        "codex-realtime-decrypt-failure@example.com",
    )
    auth_headers, _api_key = await _create_realtime_api_key(async_client, "realtime-decrypt-failure")
    ciphertexts: list[str] = []

    def fail_decrypt(_encryptor, encrypted: bytes) -> str:
        ciphertext = encrypted.hex()
        ciphertexts.append(ciphertext)
        raise ValueError(f"invalid encrypted token {ciphertext} for {account_id}")

    monkeypatch.setattr(proxy_module.TokenEncryptor, "decrypt", fail_decrypt)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        response = await async_client.post(
            "/backend-api/codex/realtime/calls",
            content=b"v=offer\r\na=ice-pwd:private-sdp-credential\r\n",
            headers={
                "content-type": "application/sdp",
                "x-request-id": "req-private-realtime-failure",
                **auth_headers,
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "realtime_call_unavailable",
            "message": "Realtime call could not be created",
            "type": "server_error",
        }
    }
    matching_records = [
        record
        for record in caplog.records
        if record.name == proxy_api_module.__name__
        and record.getMessage().startswith("Realtime call creation failed before upstream response request_id=")
    ]
    assert len(matching_records) == 1
    assert matching_records[0].getMessage().split("request_id=", maxsplit=1)[1]
    assert matching_records[0].exc_info is None
    assert len(ciphertexts) == 1
    for secret in (account_id, ciphertexts[0], "private-sdp-credential", "invalid encrypted token"):
        assert secret not in caplog.text

    service = get_proxy_service_for_app(async_client._transport.app)
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    async with SessionLocal() as session:
        persisted = (
            await session.execute(select(RequestLog).where(RequestLog.request_id == "req-private-realtime-failure"))
        ).scalar_one()
    assert persisted.status == "error"
    for private_failure_field in (
        "error_code",
        "error_message",
        "failure_phase",
        "failure_detail",
        "failure_exception_type",
        "upstream_status_code",
        "upstream_error_code",
        "bridge_stage",
    ):
        assert getattr(persisted, private_failure_field) is None


@pytest.mark.asyncio
async def test_codex_control_unexpected_failure_remains_visible_on_ordinary_route(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_codex_control_request(*_args, **_kwargs):
        raise RuntimeError("ordinary control programmer error")

    monkeypatch.setattr(
        proxy_module.ProxyService,
        "codex_control_request",
        fail_codex_control_request,
    )

    with pytest.raises(RuntimeError, match="ordinary control programmer error"):
        await async_client.post(
            "/backend-api/codex/alpha/search",
            json={"query": "OpenAI official website"},
        )


@pytest.mark.asyncio
async def test_codex_realtime_call_without_bindable_location_fails_closed(async_client, monkeypatch):
    await _import_account(async_client, "acc_codex_realtime_location", "codex-realtime-location@example.com")
    auth_headers, api_key = await _create_realtime_api_key(async_client, "realtime-location")
    upstream_calls = 0
    request_id = "req-realtime-location-failure"

    async def fake_codex_control_request(*_args, **_kwargs):
        nonlocal upstream_calls
        upstream_calls += 1
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp"},
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    response = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp", "x-request-id": request_id, **auth_headers},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "realtime_call_binding_failed"
    assert upstream_calls == 1
    await _assert_realtime_call_request_log_error(async_client, request_id=request_id, api_key=api_key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "live/rtc_unsupported",
        "/unrelated/live/rtc_unsupported",
        "realtime/calls/rtc_unsupported",
        "/unrelated/realtime/calls/rtc_unsupported",
        "//attacker.invalid/v1/realtime/calls/rtc_unsupported",
        "///v1/realtime/calls/rtc_unsupported",
        "v1/realtime/calls/rtc_unsupported",
        "/v1/realtime/calls/rtc_unsupported#fragment",
        "/v1/realtime/calls/rtc_unsupported/extra",
        "ftp://api.openai.com/v1/realtime/calls/rtc_unsupported",
        "https:///v1/realtime/calls/rtc_unsupported",
        "https://api.openai.com/v1/realtime/calls/rtc_unsupported;param",
    ],
    ids=[
        "relative-live",
        "unrelated-live",
        "relative-realtime-calls",
        "unrelated-realtime-calls",
        "network-path-reference",
        "ambiguous-leading-slashes",
        "relative-exact-path",
        "fragment",
        "extra-segment",
        "unsupported-scheme",
        "absolute-without-authority",
        "path-parameters",
    ],
)
async def test_codex_realtime_call_rejects_unsupported_location_without_binding_or_replay(
    async_client,
    monkeypatch,
    location: str,
):
    await _import_account(async_client, "acc_codex_realtime_unsupported", "codex-realtime-unsupported@example.com")
    auth_headers, api_key = await _create_realtime_api_key(async_client, "realtime-unsupported")
    upstream_calls = 0
    request_id = "req-realtime-unsupported-location"

    async def fake_codex_control_request(*_args, **_kwargs):
        nonlocal upstream_calls
        upstream_calls += 1
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": location},
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    response = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp", "x-request-id": request_id, **auth_headers},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "realtime_call_binding_failed"
    assert upstream_calls == 1
    affinity_key = realtime_call_affinity_key("rtc_unsupported", api_key)
    async with SessionLocal() as session:
        binding = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == affinity_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one_or_none()
    assert binding is None
    await _assert_realtime_call_request_log_error(async_client, request_id=request_id, api_key=api_key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_status", "initial_attempts"),
    [(401, 2), (502, 1)],
    ids=["post-refresh-auth-failover", "previsible-connect-failover"],
)
async def test_codex_realtime_call_binds_final_failover_account(
    async_client, monkeypatch, failure_status: int, initial_attempts: int
):
    await _import_account(async_client, "acc_codex_realtime_a", "codex-realtime-a@example.com")
    await _import_account(async_client, "acc_codex_realtime_b", "codex-realtime-b@example.com")
    auth_headers, api_key = await _create_realtime_api_key(async_client, "realtime-failover")
    captured_account_ids: list[str | None] = []
    selection_redaction_values: list[object] = []
    rejected_account_id: str | None = None
    select_account_with_budget = proxy_module.ProxyService._select_account_with_budget

    async def recording_select_account_with_budget(self, *args, **kwargs):
        selection_redaction_values.append(kwargs.get("redact_sensitive_details", False))
        return await select_account_with_budget(self, *args, **kwargs)

    async def fake_codex_control_request(*_args, account_id=None, **_kwargs):
        nonlocal rejected_account_id
        if rejected_account_id is None:
            rejected_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == rejected_account_id:
            if failure_status == 401:
                raise ProxyResponseError(401, {"error": {"code": "invalid_api_key", "message": "retry elsewhere"}})
            raise ProxyResponseError(
                502,
                {"error": {"code": "upstream_unavailable", "message": "[Errno 104] Connection reset by peer"}},
                failure_phase="connect",
            )
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/realtime/calls/rtc_failover"},
        )

    async def fake_ensure_fresh(
        self,
        account,
        *,
        force=False,
        timeout_seconds=None,
        privacy_policy=core_proxy.CodexControlRequestPrivacyPolicy.STANDARD,
    ):
        del self, force
        assert timeout_seconds is not None
        assert privacy_policy is core_proxy.CodexControlRequestPrivacyPolicy.PRIVATE_REALTIME
        return account

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_select_account_with_budget",
        recording_select_account_with_budget,
    )

    response = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp", **auth_headers},
    )

    assert response.status_code == 201
    assert len(captured_account_ids) == initial_attempts + 1
    assert captured_account_ids[:initial_attempts] == [captured_account_ids[0]] * initial_attempts
    assert captured_account_ids[-1] != captured_account_ids[0]
    assert selection_redaction_values
    assert all(value is True for value in selection_redaction_values)

    affinity_key = realtime_call_affinity_key("rtc_failover", api_key)
    async with SessionLocal() as session:
        binding = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == affinity_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
        final_account = (
            await session.execute(select(Account).where(Account.chatgpt_account_id == captured_account_ids[-1]))
        ).scalar_one()
    assert binding.account_id == final_account.id


@pytest.mark.asyncio
async def test_codex_control_retry_failure_after_forced_refresh_updates_account_health(async_client, monkeypatch):
    await _import_account(async_client, "acc_codex_retry_error", "codex-retry-error@example.com")
    calls = 0
    handled: list[tuple[str, int]] = []

    async def fake_codex_control_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProxyResponseError(401, {"error": {"code": "invalid_api_key", "message": "stale token"}})
        raise ProxyResponseError(
            503,
            {"error": {"code": "upstream_unavailable", "message": "still down", "type": "server_error"}},
        )

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    async def fake_handle_proxy_error(self, account, exc):
        handled.append((account.id, exc.status_code))

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_proxy_error", fake_handle_proxy_error)

    response = await async_client.post(
        "/backend-api/codex/safety/arc",
        json={"decision": "allow"},
    )

    assert response.status_code == 503
    assert "X-App-Version" not in response.headers
    assert calls == 2
    assert len(handled) == 1
    assert handled[0][0].startswith("acc_codex_retry_error")
    assert handled[0][1] == 503


@pytest.mark.asyncio
async def test_codex_control_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    await _import_account(async_client, "acc_codex_invalidated_a", "codex-invalidated-a@example.com")
    await _import_account(async_client, "acc_codex_invalidated_b", "codex-invalidated-b@example.com")
    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

    async def fake_codex_control_request(*_args, account_id=None, **_kwargs):
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise ProxyResponseError(401, {"error": {"code": "invalid_api_key", "message": "token invalidated"}})
        return proxy_module.CodexControlResponse(status_code=200, headers={}, body=b'{"ok":true}')

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post("/backend-api/codex/safety/arc", json={"decision": "allow"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
async def test_codex_control_post_401_forced_refresh_claim_timeout_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Regression (P2 forced-refresh surfaces): when the codex-control post-401
    forced refresh on the failover account hits a transient cross-replica
    refresh-CLAIM-CONTENTION timeout, the surface routes through
    ``_ensure_fresh_with_budget_or_auth_error``, which MUST surface a retryable
    ``upstream_unavailable`` (502) rather than a bogus 401 ``invalid_api_key``."""
    await _import_account(async_client, "acc_codex_claim_a", "codex-claim-a@example.com")
    await _import_account(async_client, "acc_codex_claim_b", "codex-claim-b@example.com")

    async def fake_codex_control_request(*_args, account_id=None, **_kwargs):
        del _args, account_id, _kwargs
        # Always 401 so the surface fails over and forces a refresh on the peer.
        raise ProxyResponseError(401, {"error": {"code": "invalid_api_key", "message": "token invalidated"}})

    first_fresh_account: dict[str, str | None] = {"id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_fresh_account["id"] is None:
            first_fresh_account["id"] = account.id
        if account.id != first_fresh_account["id"]:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post("/backend-api/codex/safety/arc", json={"decision": "allow"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "upstream_path"),
    [
        ("/backend-api/codex/agent-identities/jwks", "agent-identities/jwks"),
        ("/backend-api/wham/agent-identities/jwks", "wham/agent-identities/jwks"),
    ],
)
async def test_codex_agent_identity_jwks_routes_forward_upstream(async_client, monkeypatch, endpoint, upstream_path):
    await _import_account(async_client, "acc_codex_jwks", "codex-jwks@example.com")
    calls = []

    async def fake_codex_control_request(
        path,
        *,
        method,
        payload,
        query_params,
        headers,
        access_token,
        account_id,
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append((path, method, payload, list(query_params), access_token, account_id, timeout_seconds))
        return core_proxy.CodexControlResponse(
            status_code=200,
            body=b'{"keys":[]}',
            headers={
                "cache-control": "public, max-age=3600",
                "content-type": "application/json",
                "etag": '"jwks-v1"',
                "last-modified": "Sat, 16 May 2026 19:00:00 GMT",
            },
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    response = await async_client.get(endpoint, params=[("kid", "test"), ("kid", "next")])

    assert response.status_code == 200
    assert response.json() == {"keys": []}
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.headers["etag"] == '"jwks-v1"'
    assert response.headers["last-modified"] == "Sat, 16 May 2026 19:00:00 GMT"
    assert calls[0][:6] == (
        upstream_path,
        "GET",
        None,
        [("kid", "test"), ("kid", "next")],
        "access-token",
        "acc_codex_jwks",
    )
    assert isinstance(calls[0][6], float)
    assert calls[0][6] > 0


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
        assert log.request_id == "resp_1"
        assert log.input_tokens == 10
        assert log.output_tokens == 5
        assert log.cached_input_tokens == 3
        assert log.reasoning_tokens == 2
        assert log.status == "success"


@pytest.mark.asyncio
async def test_proxy_stream_surfaces_and_logs_upstream_eof_without_terminal(async_client, monkeypatch):
    expected_account_id = await _import_account(async_client, "acc_stream_eof", "stream-eof@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        event = {"type": "response.output_text.delta", "delta": "partial"}
        yield _sse_event(event)

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": "req_stream_eof"},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [
        json.loads(line[6:]) for line in lines if line.startswith("data: ") and not line.startswith("data: [DONE]")
    ]
    failed = [event for event in events if event.get("type") == "response.failed"]
    assert failed
    assert failed[-1]["response"]["error"]["code"] == "stream_incomplete"

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog)
            .where(RequestLog.account_id == expected_account_id)
            .order_by(RequestLog.requested_at.desc())
        )
        log = result.scalars().first()
        assert log is not None
        assert log.status == "error"
        assert log.error_code == "stream_incomplete"
        assert log.error_message == "Upstream stream ended before response.completed"
        assert log.failure_phase == "upstream"
        assert log.failure_detail == "upstream_eof_before_terminal_event"


@pytest.mark.asyncio
async def test_proxy_stream_classifies_core_generated_eof_failure(async_client, monkeypatch):
    expected_account_id = await _import_account(async_client, "acc_stream_core_eof", "stream-core-eof@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield _sse_event({"type": "response.output_text.delta", "delta": "partial"})
        yield _sse_event(
            {
                "type": "response.failed",
                "response": {
                    "id": "resp_core_eof",
                    "error": {
                        "code": "stream_incomplete",
                        "message": "Upstream closed stream without completion",
                    },
                },
            }
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": "req_stream_core_eof"},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = [
        json.loads(line[6:]) for line in lines if line.startswith("data: ") and not line.startswith("data: [DONE]")
    ][-1]
    assert event["type"] == "response.failed"
    assert event["response"]["error"]["code"] == "stream_incomplete"

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog)
            .where(RequestLog.account_id == expected_account_id)
            .order_by(RequestLog.requested_at.desc())
        )
        log = result.scalars().first()
        assert log is not None
        assert log.status == "error"
        assert log.error_code == "stream_incomplete"
        assert log.error_message == "Upstream closed stream without completion"
        assert log.failure_phase == "upstream"
        assert log.failure_detail == "upstream_eof_before_terminal_event"


async def test_proxy_stream_surfaces_first_core_generated_eof_before_no_accounts(async_client, monkeypatch):
    expected_account_id = await _import_account(
        async_client,
        "acc_stream_first_core_eof",
        "stream-first-core-eof@example.com",
    )

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield _sse_event(
            {
                "type": "response.failed",
                "response": {
                    "id": "resp_first_core_eof",
                    "error": {
                        "code": "stream_incomplete",
                        "message": "Upstream closed stream without completion",
                    },
                },
            }
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": "req_stream_first_core_eof"},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = [
        json.loads(line[6:]) for line in lines if line.startswith("data: ") and not line.startswith("data: [DONE]")
    ][-1]
    assert event["type"] == "response.failed"
    assert event["response"]["error"]["code"] == "stream_incomplete"

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog)
            .where(RequestLog.account_id == expected_account_id)
            .order_by(RequestLog.requested_at.desc())
        )
        logs = list(result.scalars().all())
        log = next((item for item in logs if item.error_code == "stream_incomplete"), None)
        assert log is not None
        assert log.status == "error"
        assert log.error_code == "stream_incomplete"
        assert log.error_message == "Upstream closed stream without completion"
        assert log.failure_phase == "upstream"
        assert log.failure_detail == "upstream_eof_before_terminal_event"


@pytest.mark.asyncio
async def test_proxy_stream_exception_without_terminal_event_logs_as_stream_incomplete(async_client, monkeypatch):
    expected_account_id = await _import_account(async_client, "acc_stream_exc", "stream-exception@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        del payload, headers, access_token, account_id, base_url, raise_for_status
        yield _sse_event({"type": "response.output_text.delta", "delta": "partial"})
        raise RuntimeError("upstream stream processing failed")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": "req_stream_exception"},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    stream_lines = [line for line in lines if line.startswith("data: ") and not line.startswith("data: [DONE]")]
    events = [json.loads(line[6:]) for line in stream_lines]
    event = _extract_first_event(stream_lines)
    assert event["type"] == "response.output_text.delta"
    assert all(
        event.get("type") != "response.failed" or event["response"]["error"]["code"] != "client_disconnected"
        for event in events
    )

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog)
            .where(RequestLog.account_id == expected_account_id)
            .order_by(RequestLog.requested_at.desc())
        )
        log = result.scalars().first()
        assert log is not None
        assert log.status == "error"
        assert log.error_code == "stream_incomplete"
        assert log.error_message == "Upstream stream ended before response.completed"
        assert log.failure_phase == "upstream"
        assert log.failure_detail == "upstream_eof_before_terminal_event"


@pytest.mark.asyncio
async def test_stream_responses_starts_sse_keepalive_before_first_upstream_event(monkeypatch):
    upstream_started = asyncio.Event()
    release_upstream = asyncio.Event()
    seen_client_ip: list[str | None] = []

    class _FakeService:
        async def rate_limit_headers(self):
            return {}

        async def stream_responses(self, *args, **kwargs):
            del args
            seen_client_ip.append(kwargs.get("client_ip"))
            upstream_started.set()
            # The real service signals this after local admission. Preserve
            # that contract while this fake deliberately stalls upstream I/O.
            _signal_propagated_capacity_startup_ready()
            await release_upstream.wait()
            event = {"type": "response.completed", "response": {"id": "resp_delayed"}}
            yield _sse_event(event)

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=False,
        sse_keepalive_interval_seconds=0.01,
        proxy_account_stream_recovery_reserve=1,
        proxy_api_key_fair_share_congestion_threshold_pct=0,
    )
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module.proxy_service_module, "get_settings", lambda: settings)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [],
            "client": ("203.0.113.7", 54321),
        }
    )
    payload = proxy_api_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    )

    response = await proxy_api_module._stream_responses(
        request,
        payload,
        ProxyContext(service=cast(proxy_module.ProxyService, _FakeService())),
        api_key=None,
    )

    assert isinstance(response, StreamingResponse)
    assert upstream_started.is_set() is True
    iterator = response.body_iterator.__aiter__()
    first_chunk = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert first_chunk == SSE_KEEPALIVE_FRAME
    release_upstream.set()
    chunks = [cast(str, await asyncio.wait_for(iterator.__anext__(), timeout=0.2)) for _ in range(2)]
    assert any("response.completed" in chunk for chunk in chunks)
    assert seen_client_ip == ["203.0.113.7"]


@pytest.mark.asyncio
async def test_source_responses_stream_starts_sse_keepalive_before_first_upstream_event(monkeypatch):
    """Source-routed /v1/responses must keep SSE alive like account streams."""
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceResponsesStream, SourceUsageHolder

    release_upstream = asyncio.Event()
    keepalives: list[str] = []

    async def delayed_body():
        await release_upstream.wait()
        # Split across chunk boundaries so reassembly is required.
        event = _sse_event({"type": "response.completed", "response": {"id": "resp_source_delayed"}})
        mid = max(1, len(event) // 2)
        yield event[:mid].encode("utf-8")
        yield event[mid:].encode("utf-8")

    async def fake_stream_source_responses(_source, _payload):
        return SourceResponsesStream(
            body=delayed_body(),
            usage_holder=SourceUsageHolder(),
            upstream_status_code=200,
        )

    async def allow_request_limits(*args, **kwargs):
        del args, kwargs
        return None

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0.01)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module, "stream_source_responses", fake_stream_source_responses)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", allow_request_limits)
    monkeypatch.setattr(
        proxy_api_module,
        "_record_stream_keepalive",
        lambda surface: keepalives.append(surface),
    )

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "headers": [],
            "client": ("203.0.113.9", 54321),
        }
    )
    payload = proxy_api_module.ResponsesRequest.model_validate(
        {"model": "src-model", "instructions": "hi", "input": [], "stream": True}
    )
    source = ModelSource(
        id="src_keepalive",
        name="keepalive-source",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=False,
        supports_responses=True,
    )

    response = await proxy_api_module._source_responses_response(
        request,
        payload,
        source=source,
        api_key=None,
        rate_limit_headers={},
        pre_normalization_effort=None,
    )

    assert isinstance(response, StreamingResponse)
    iterator = response.body_iterator.__aiter__()
    first_chunk = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert first_chunk == SSE_KEEPALIVE_FRAME
    assert keepalives == ["responses_source"]
    release_upstream.set()
    remaining: list[str] = []
    while True:
        try:
            remaining.append(cast(str, await asyncio.wait_for(iterator.__anext__(), timeout=0.2)))
        except StopAsyncIteration:
            break
    joined = "".join(remaining)
    assert "resp_source_delayed" in joined
    created_at = joined.find("response.created")
    completed_at = joined.find("response.completed")
    assert created_at != -1
    assert completed_at != -1
    assert created_at < completed_at


@pytest.mark.asyncio
async def test_source_responses_stream_reassembles_crlf_event_blocks(monkeypatch):
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceResponsesStream, SourceUsageHolder

    async def crlf_split_body():
        event = 'data: {"type":"response.completed","response":{"id":"resp_crlf"}}\r\n\r\n'
        mid = max(1, len(event) // 2)
        yield event[:mid].encode("utf-8")
        yield event[mid:].encode("utf-8")

    async def fake_stream_source_responses(_source, _payload):
        return SourceResponsesStream(
            body=crlf_split_body(),
            usage_holder=SourceUsageHolder(),
            upstream_status_code=200,
        )

    async def allow_request_limits(*args, **kwargs):
        del args, kwargs
        return None

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module, "stream_source_responses", fake_stream_source_responses)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", allow_request_limits)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "headers": [],
            "client": ("203.0.113.11", 54321),
        }
    )
    payload = proxy_api_module.ResponsesRequest.model_validate(
        {"model": "src-model", "instructions": "hi", "input": [], "stream": True}
    )
    source = ModelSource(
        id="src_crlf",
        name="crlf-source",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=False,
        supports_responses=True,
    )

    response = await proxy_api_module._source_responses_response(
        request,
        payload,
        source=source,
        api_key=None,
        rate_limit_headers={},
        pre_normalization_effort=None,
    )
    assert isinstance(response, StreamingResponse)
    joined = "".join([cast(str, chunk) async for chunk in response.body_iterator])
    assert "resp_crlf" in joined
    created_at = joined.find("response.created")
    completed_at = joined.find("response.completed")
    assert created_at != -1
    assert completed_at != -1
    assert created_at < completed_at


@pytest.mark.asyncio
async def test_source_responses_forwards_unparseable_blocks_without_synthetic_terminal(monkeypatch):
    """Unparseable source data passes through verbatim instead of becoming response.failed."""
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceResponsesStream, SourceUsageHolder

    malformed_block = 'data: {"type":"response.completed","response":{"id":"resp_unparseable"}\n\n'

    async def malformed_body():
        yield malformed_block.encode("utf-8")

    async def fake_stream_source_responses(_source, _payload):
        return SourceResponsesStream(
            body=malformed_body(),
            usage_holder=SourceUsageHolder(),
            upstream_status_code=200,
        )

    async def allow_request_limits(*args, **kwargs):
        del args, kwargs
        return None

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module, "stream_source_responses", fake_stream_source_responses)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", allow_request_limits)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "headers": [],
            "client": ("203.0.113.9", 54321),
        }
    )
    payload = proxy_api_module.ResponsesRequest.model_validate(
        {"model": "src-model", "instructions": "hi", "input": [], "stream": True}
    )
    source = ModelSource(
        id="src_unparseable",
        name="unparseable-source",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=False,
        supports_responses=True,
    )

    response = await proxy_api_module._source_responses_response(
        request,
        payload,
        source=source,
        api_key=None,
        rate_limit_headers={},
        pre_normalization_effort=None,
    )

    assert isinstance(response, StreamingResponse)
    chunks = [cast(str, chunk) async for chunk in response.body_iterator]
    joined = "".join(chunks)
    assert malformed_block in joined
    assert "response.failed" not in joined


@pytest.mark.asyncio
async def test_iter_source_sse_event_blocks_closes_owner_when_aiter_fails():
    closed: list[str] = []

    class _Owner:
        def __aiter__(self):
            closed.append("aiter")
            raise RuntimeError("aiter failed")

        async def aclose(self) -> None:
            closed.append("owner")

    with pytest.raises(RuntimeError, match="aiter failed"):
        async for _ in proxy_api_module._iter_source_sse_event_blocks(_Owner()):
            pass

    assert closed == ["aiter", "owner"]


@pytest.mark.asyncio
async def test_iter_source_sse_event_blocks_closes_owner_when_iterator_close_fails():
    closed: list[str] = []

    class _Iterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self) -> None:
            closed.append("iterator")
            raise RuntimeError("iterator close failed")

    class _Owner:
        def __aiter__(self):
            return _Iterator()

        async def aclose(self) -> None:
            closed.append("owner")

    with pytest.raises(RuntimeError, match="iterator close failed"):
        async for _ in proxy_api_module._iter_source_sse_event_blocks(_Owner()):
            pass

    assert closed == ["iterator", "owner"]


@pytest.mark.asyncio
async def test_iter_source_sse_event_blocks_swallows_lf_residue_of_split_crlf():
    """...\r\n\r | \n chunking dispatches at the bare CR and drops the LF residue."""

    async def split_after_cr_body():
        yield b'data: {"type":"response.created"}\r\n\r'
        yield b'\ndata: {"type":"response.completed"}\n\n'

    blocks = [
        block
        async for block in proxy_api_module._iter_source_sse_event_blocks(
            split_after_cr_body(),
            max_event_bytes=4096,
        )
    ]

    assert blocks == [
        'data: {"type":"response.created"}\r\n\r',
        'data: {"type":"response.completed"}\n\n',
    ]


@pytest.mark.asyncio
async def test_iter_source_sse_event_blocks_ignores_leading_utf8_bom():
    """One optional leading UTF-8 BOM is ignored, split across chunks or not."""

    async def bom_body():
        yield b"\xef\xbb"
        yield b'\xbfdata: {"type":"response.completed","response":{"id":"resp_bom"}}\n\n'

    blocks = [
        block
        async for block in proxy_api_module._iter_source_sse_event_blocks(
            bom_body(),
            max_event_bytes=4096,
        )
    ]

    assert blocks == ['data: {"type":"response.completed","response":{"id":"resp_bom"}}\n\n']


@pytest.mark.asyncio
async def test_iter_source_sse_event_blocks_preserves_crlf_and_cr_terminators():
    async def body():
        yield b'event: response.completed\r\ndata: {"type":"response.completed"}\r\n\r\n'
        yield b'event: response.failed\rdata: {"type":"response.failed"}\r\r'

    blocks = [block async for block in proxy_api_module._iter_source_sse_event_blocks(body())]
    assert blocks == [
        'event: response.completed\r\ndata: {"type":"response.completed"}\r\n\r\n',
        'event: response.failed\rdata: {"type":"response.failed"}\r\r',
    ]


@pytest.mark.asyncio
async def test_iter_source_sse_event_blocks_preserves_mixed_lf_cr_terminators():
    async def body():
        yield b'data: {"type":"response.completed","response":{"id":"resp_lf_cr"}}\n\r'
        yield b'data: {"type":"response.failed"}\n\r\n'

    blocks = [block async for block in proxy_api_module._iter_source_sse_event_blocks(body())]
    assert blocks == [
        'data: {"type":"response.completed","response":{"id":"resp_lf_cr"}}\n\r',
        'data: {"type":"response.failed"}\n\r\n',
    ]


@pytest.mark.asyncio
async def test_iter_source_sse_event_blocks_dispatches_cr_only_without_waiting():
    release_next = asyncio.Event()
    closed = asyncio.Event()

    async def body():
        try:
            yield b'data: {"type":"response.completed","response":{"id":"resp_cr"}}\r\r'
            await release_next.wait()
            yield b'data: {"type":"response.failed"}\r\r'
        finally:
            closed.set()

    iterator = proxy_api_module._iter_source_sse_event_blocks(body()).__aiter__()
    first = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert first == 'data: {"type":"response.completed","response":{"id":"resp_cr"}}\r\r'
    release_next.set()
    second = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert second.endswith("\r\r")
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_iter_source_sse_event_blocks_rejects_oversize_event():
    from app.core.clients.proxy import StreamEventTooLargeError

    async def body():
        yield b"data: " + (b"x" * 64) + b"\n\n"

    with pytest.raises(StreamEventTooLargeError):
        async for _ in proxy_api_module._iter_source_sse_event_blocks(body(), max_event_bytes=32):
            pass


@pytest.mark.asyncio
async def test_wrap_source_responses_native_codex_preserves_codex_events_and_keepalive(monkeypatch):
    keepalives: list[str] = []
    release_upstream = asyncio.Event()

    async def delayed_body():
        await release_upstream.wait()
        yield (
            b'data: {"type":"codex.rate_limits","plan_type":"pro","rate_limits":{"allowed":true}}\n\n'
            b'data: {"type":"response.completed","response":{"id":"resp_codex_src","output":[]}}\n\n'
        )

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0.01, max_sse_event_bytes=16 * 1024 * 1024)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        proxy_api_module,
        "_record_stream_keepalive",
        lambda surface: keepalives.append(surface),
    )

    stream = proxy_api_module._wrap_source_responses_public_stream(
        delayed_body(),
        enforce_openai_sdk_contract=False,
        native_codex_heartbeat=True,
    )
    iterator = stream.__aiter__()
    first = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert first == CODEX_KEEPALIVE_FRAME
    # Prefixed heartbeat does not record metrics; wait for an injected idle frame.
    second = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert second == CODEX_KEEPALIVE_FRAME
    assert keepalives == ["responses_source"]
    release_upstream.set()
    remaining = [chunk async for chunk in iterator]
    joined = "".join(remaining)
    assert "codex.rate_limits" in joined
    assert "response.created" not in joined
    assert "resp_codex_src" in joined


@pytest.mark.asyncio
async def test_wrap_source_responses_closes_source_on_early_error_and_client_close(monkeypatch):
    closed: list[str] = []

    async def error_body():
        try:
            yield b'data: {"type":"error","error":{"message":"boom","code":"server_error"}}\n\n'
            yield b'data: {"type":"response.completed","response":{"id":"resp_skip"}}\n\n'
        finally:
            closed.append("source")

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0, max_sse_event_bytes=16 * 1024 * 1024)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)

    chunks = [
        chunk
        async for chunk in proxy_api_module._wrap_source_responses_public_stream(
            error_body(),
            enforce_openai_sdk_contract=True,
        )
    ]
    joined = "".join(chunks)
    assert "response.failed" in joined
    assert "resp_skip" not in joined
    assert closed == ["source"]

    closed.clear()
    hold = asyncio.Event()

    async def held_body():
        try:
            yield b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_hold"}}\n\n'
            await hold.wait()
            yield b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_hold"}}\n\n'
        finally:
            closed.append("source")

    stream = proxy_api_module._wrap_source_responses_public_stream(
        held_body(),
        enforce_openai_sdk_contract=True,
    )
    iterator = stream.__aiter__()
    first = await iterator.__anext__()
    assert "response.created" in first
    await cast(Any, iterator).aclose()
    assert closed == ["source"]
    hold.set()


@pytest.mark.asyncio
async def test_wrap_source_responses_closes_raw_source_after_initial_heartbeat_disconnect(monkeypatch):
    """A native client dropping right after the prefixed heartbeat must close the raw source."""

    class _RawSource:
        def __init__(self) -> None:
            self.closed = False

        def __aiter__(self) -> "_RawSource":
            return self

        async def __anext__(self) -> bytes:
            await asyncio.Event().wait()
            raise StopAsyncIteration

        async def aclose(self) -> None:
            self.closed = True

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0, max_sse_event_bytes=16 * 1024 * 1024)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)

    raw_source = _RawSource()
    stream = proxy_api_module._wrap_source_responses_public_stream(
        raw_source,
        enforce_openai_sdk_contract=False,
        native_codex_heartbeat=True,
    )
    iterator = stream.__aiter__()
    first = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert first == CODEX_KEEPALIVE_FRAME
    # None of the inner layers has started iterating yet; closing the wrapper
    # must still release the eagerly opened source body.
    await cast(Any, iterator).aclose()
    assert raw_source.closed is True


@pytest.mark.asyncio
async def test_source_stream_retry_control_block_keeps_truncation_failure(monkeypatch):
    """Valid non-data control blocks pass through without suppressing truncation synthesis."""

    async def control_then_truncated_body():
        yield b"retry: 1000\n\n"
        yield b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_ctrl"}}\n\n'

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0, max_sse_event_bytes=16 * 1024 * 1024)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)

    chunks = [
        chunk
        async for chunk in proxy_api_module._wrap_source_responses_public_stream(
            control_then_truncated_body(),
            enforce_openai_sdk_contract=True,
        )
    ]
    joined = "".join(chunks)
    assert "retry: 1000" in joined
    assert "resp_ctrl" in joined
    assert "response.failed" in joined


@pytest.mark.asyncio
async def test_source_stream_data_substring_in_field_value_keeps_truncation_failure(monkeypatch):
    """A field value containing the literal 'data:' is not a data block and must not suppress failures."""

    async def id_field_then_truncated_body():
        yield b"id: data:1\n\n"
        yield b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_idfield"}}\n\n'

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0, max_sse_event_bytes=16 * 1024 * 1024)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)

    chunks = [
        chunk
        async for chunk in proxy_api_module._wrap_source_responses_public_stream(
            id_field_then_truncated_body(),
            enforce_openai_sdk_contract=True,
        )
    ]
    joined = "".join(chunks)
    assert "id: data:1" in joined
    assert "resp_idfield" in joined
    assert "response.failed" in joined


@pytest.mark.asyncio
async def test_source_stream_unicode_separator_in_field_value_keeps_truncation_failure(monkeypatch):
    """U+2028 inside a field value must not expose a false data line: SSE splits only on CR/LF/CRLF."""

    async def unicode_field_then_truncated_body():
        yield "id: metadata\u2028data:oops\n\n".encode("utf-8")
        yield b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_u2028"}}\n\n'

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0, max_sse_event_bytes=16 * 1024 * 1024)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)

    chunks = [
        chunk
        async for chunk in proxy_api_module._wrap_source_responses_public_stream(
            unicode_field_then_truncated_body(),
            enforce_openai_sdk_contract=True,
        )
    ]
    joined = "".join(chunks)
    assert "metadata\u2028data:oops" in joined
    assert "resp_u2028" in joined
    assert "response.failed" in joined


@pytest.mark.asyncio
async def test_source_stream_bare_data_field_suppresses_synthetic_terminal(monkeypatch):
    """The valid empty-field form (a bare 'data' line) counts as unparseable source data."""

    async def bare_data_body():
        yield b"data\n\n"

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0, max_sse_event_bytes=16 * 1024 * 1024)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)

    chunks = [
        chunk
        async for chunk in proxy_api_module._wrap_source_responses_public_stream(
            bare_data_body(),
            enforce_openai_sdk_contract=True,
        )
    ]
    joined = "".join(chunks)
    assert "data\n\n" in joined
    assert "response.failed" not in joined


@pytest.mark.asyncio
async def test_wrap_source_responses_preserves_crlf_framing_of_unchanged_events(monkeypatch):
    """An unchanged CRLF-framed standard event must pass through byte-identically."""
    crlf_delta_block = (
        "event: response.output_text.delta\r\n"
        'data: {"type":"response.output_text.delta","item_id":"item_crlf","output_index":0,'
        '"content_index":0,"delta":"hi"}\r\n\r\n'
    )

    async def crlf_framed_body():
        yield b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_crlf_frame"}}\n\n'
        yield crlf_delta_block.encode("utf-8")
        yield (
            b"event: response.completed\n"
            b'data: {"type":"response.completed","response":{"id":"resp_crlf_frame","output":[]}}\n\n'
        )

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0, max_sse_event_bytes=16 * 1024 * 1024)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)

    chunks = [
        chunk
        async for chunk in proxy_api_module._wrap_source_responses_public_stream(
            crlf_framed_body(),
            enforce_openai_sdk_contract=True,
        )
    ]
    joined = "".join(chunks)
    assert crlf_delta_block in joined
    assert "resp_crlf_frame" in joined


@pytest.mark.asyncio
async def test_source_responses_stream_preserves_split_utf8_and_crlf(monkeypatch):
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceResponsesStream, SourceUsageHolder

    async def split_boundary_body():
        # "é" is UTF-8 C3 A9; split after C3. Also split CRLF after CR.
        prefix = b'data: {"type":"response.completed","response":{"id":"resp_'
        mid = "caf\u00e9".encode("utf-8")
        yield prefix + mid[:4]  # ends mid UTF-8 sequence for é
        yield mid[4:] + b'"}}\r'
        yield b"\n\r\n"

    async def fake_stream_source_responses(_source, _payload):
        return SourceResponsesStream(
            body=split_boundary_body(),
            usage_holder=SourceUsageHolder(),
            upstream_status_code=200,
        )

    async def allow_request_limits(*args, **kwargs):
        del args, kwargs
        return None

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module, "stream_source_responses", fake_stream_source_responses)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", allow_request_limits)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "headers": [],
            "client": ("203.0.113.12", 54321),
        }
    )
    payload = proxy_api_module.ResponsesRequest.model_validate(
        {"model": "src-model", "instructions": "hi", "input": [], "stream": True}
    )
    source = ModelSource(
        id="src_utf8_crlf",
        name="utf8-crlf-source",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=False,
        supports_responses=True,
    )

    response = await proxy_api_module._source_responses_response(
        request,
        payload,
        source=source,
        api_key=None,
        rate_limit_headers={},
        pre_normalization_effort=None,
    )
    assert isinstance(response, StreamingResponse)
    joined = "".join([cast(str, chunk) async for chunk in response.body_iterator])
    assert "resp_caf" in joined
    assert "\\u00e9" in joined or "caf\u00e9" in joined
    created_at = joined.find("response.created")
    completed_at = joined.find("response.completed")
    assert created_at != -1
    assert completed_at != -1
    assert created_at < completed_at


@pytest.mark.asyncio
async def test_source_responses_normalize_error_still_settles_reservation(monkeypatch):
    """Normalize early-return must not aclose settlement as client_disconnected."""
    from app.db.models import ModelSource
    from app.modules.model_sources.forwarding import SourceResponsesStream, SourceUsage, SourceUsageHolder

    settle_calls: list[object] = []
    log_statuses: list[str] = []

    async def error_then_completed_body():
        yield b'data: {"type":"error","error":{"message":"boom","code":"server_error"}}\n\n'
        yield b'data: {"type":"response.completed","response":{"id":"resp_should_not_matter"}}\n\n'

    async def fake_stream_source_responses(_source, _payload):
        return SourceResponsesStream(
            body=error_then_completed_body(),
            usage_holder=SourceUsageHolder(usage=SourceUsage(input_tokens=1, output_tokens=1)),
            upstream_status_code=200,
        )

    async def allow_request_limits(*args, **kwargs):
        del args, kwargs
        return object()

    async def record_settle(reservation, **kwargs):
        del kwargs
        settle_calls.append(reservation)
        return True

    async def record_log(*args, **kwargs):
        del args
        log_statuses.append(str(kwargs.get("status")))

    settings = SimpleNamespace(sse_keepalive_interval_seconds=0)
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module, "stream_source_responses", fake_stream_source_responses)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", allow_request_limits)
    monkeypatch.setattr(proxy_api_module, "_settle_source_reservation", record_settle)
    monkeypatch.setattr(proxy_api_module, "_log_source_chat_completion", record_log)
    monkeypatch.setattr(proxy_api_module, "_reservation_requires_usage", lambda _reservation: False)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "headers": [],
            "client": ("203.0.113.10", 54321),
        }
    )
    payload = proxy_api_module.ResponsesRequest.model_validate(
        {"model": "src-model", "instructions": "hi", "input": [], "stream": True}
    )
    source = ModelSource(
        id="src_normalize_error",
        name="normalize-error-source",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=False,
        supports_responses=True,
    )

    response = await proxy_api_module._source_responses_response(
        request,
        payload,
        source=source,
        api_key=None,
        rate_limit_headers={},
        pre_normalization_effort=None,
    )
    assert isinstance(response, StreamingResponse)
    chunks = [cast(str, chunk) async for chunk in response.body_iterator]
    joined = "".join(chunks)
    assert "response.created" in joined
    assert "response.failed" in joined
    assert "resp_should_not_matter" not in joined
    assert settle_calls, "normalize early-return must still settle the outer reservation"
    assert "cancelled" not in log_statuses


@pytest.mark.asyncio
async def test_backend_desktop_openai_shape_preserves_native_event_order(
    async_client,
    monkeypatch,
):
    lines = await _request_idle_heartbeat_stream(
        async_client,
        monkeypatch,
        route="/backend-api/codex/responses",
        headers={
            "user-agent": "Codex Desktop/0.1.0 (Mac OS 26.5.0; arm64)",
            "originator": "Codex Desktop",
        },
        account_suffix="desktop_openai_shape",
    )

    event_types = [event.get("type") for event in _sse_data_events(lines)]
    assert event_types[0] == "codex.rate_limits"
    assert "codex.keepalive" not in event_types
    assert "response.created" not in event_types
    assert "response.completed" in event_types


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_headers", "account_suffix"),
    [
        pytest.param(
            {
                "user-agent": "Codex Desktop/0.1.0 (Mac OS 26.5.0; arm64)",
                "originator": "Codex Desktop",
                "x-stainless-lang": "python",
            },
            "desktop_stainless",
            id="x-stainless-overrides-desktop",
        ),
        pytest.param(
            {
                "user-agent": "OpenAI/Python 2.24.0",
                "originator": "Codex Desktop",
            },
            "native_originator_openai_ua",
            id="openai-user-agent-overrides-native-originator",
        ),
    ],
)
async def test_backend_explicit_sdk_marker_uses_comment_heartbeat(
    async_client,
    monkeypatch,
    identity_headers,
    account_suffix,
):
    lines = await _request_idle_heartbeat_stream(
        async_client,
        monkeypatch,
        route="/backend-api/codex/responses",
        headers=identity_headers,
        account_suffix=account_suffix,
    )

    assert lines[0] == SSE_KEEPALIVE_FRAME.strip()
    assert CODEX_KEEPALIVE_FRAME.strip().splitlines()[0] not in lines
    event_types = [event.get("type") for event in _sse_data_events(lines)]
    assert event_types[0] == "response.created"
    assert "codex.rate_limits" not in event_types
    assert "response.completed" in event_types


@pytest.mark.asyncio
async def test_v1_desktop_identity_uses_comment_heartbeat_and_sdk_event_order(
    async_client,
    monkeypatch,
):
    lines = await _request_idle_heartbeat_stream(
        async_client,
        monkeypatch,
        route="/v1/responses",
        headers={
            "user-agent": "Codex Desktop/0.1.0 (Mac OS 26.5.0; arm64)",
            "originator": "Codex Desktop",
        },
        account_suffix="v1_desktop",
    )

    assert lines[0] == SSE_KEEPALIVE_FRAME.strip()
    assert CODEX_KEEPALIVE_FRAME.strip().splitlines()[0] not in lines
    event_types = [event.get("type") for event in _sse_data_events(lines)]
    assert event_types[0] == "response.created"
    assert "codex.rate_limits" not in event_types
    assert "response.completed" in event_types


@pytest.mark.asyncio
async def test_compact_responses_passes_client_ip_to_service(monkeypatch):
    seen_client_ip: list[str | None] = []

    class _FakeService:
        async def rate_limit_headers(self):
            return {}

        async def compact_responses(self, *args, **kwargs):
            del args
            seen_client_ip.append(kwargs.get("client_ip"))
            return proxy_api_module.CompactResponsePayload.model_validate({"object": "response.compact"})

    async def allow_request_limits(*args, **kwargs):
        del args, kwargs
        return None

    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", allow_request_limits)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses/compact",
            "headers": [],
            "client": ("203.0.113.8", 54321),
        }
    )
    payload = proxy_api_module.ResponsesCompactRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": []}
    )

    response = await proxy_api_module._compact_responses(
        request,
        payload,
        ProxyContext(service=cast(proxy_module.ProxyService, _FakeService())),
        api_key=None,
    )

    assert response.status_code == 200
    assert seen_client_ip == ["203.0.113.8"]


@pytest.mark.asyncio
async def test_codex_route_stream_responses_starts_event_keepalive_before_first_upstream_event(monkeypatch):
    upstream_started = asyncio.Event()
    release_upstream = asyncio.Event()

    class _FakeService:
        async def rate_limit_headers(self):
            return {}

        async def stream_responses(self, *args, **kwargs):
            del args, kwargs
            upstream_started.set()
            _signal_propagated_capacity_startup_ready()
            await release_upstream.wait()
            event = {"type": "response.completed", "response": {"id": "resp_delayed"}}
            yield _sse_event(event)

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=False,
        sse_keepalive_interval_seconds=0.01,
        proxy_account_stream_recovery_reserve=1,
        proxy_api_key_fair_share_congestion_threshold_pct=0,
    )
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module.proxy_service_module, "get_settings", lambda: settings)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [],
        }
    )
    payload = proxy_api_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    )

    response = await proxy_api_module._stream_responses(
        request,
        payload,
        ProxyContext(service=cast(proxy_module.ProxyService, _FakeService())),
        api_key=None,
        enforce_openai_sdk_contract=False,
    )

    assert isinstance(response, StreamingResponse)
    assert upstream_started.is_set() is True
    iterator = response.body_iterator.__aiter__()
    first_chunk = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert first_chunk == CODEX_KEEPALIVE_FRAME
    release_upstream.set()
    chunks = [cast(str, await asyncio.wait_for(iterator.__anext__(), timeout=0.2)) for _ in range(2)]
    assert any("response.completed" in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_codex_route_stream_responses_keeps_client_alive_while_bridge_cooldown_delays_first_event(
    monkeypatch,
):
    upstream_started = asyncio.Event()
    release_upstream = asyncio.Event()

    class _FakeService:
        async def rate_limit_headers(self):
            return {}

        async def stream_responses(self, *args, **kwargs):
            del args, kwargs
            upstream_started.set()
            _signal_propagated_capacity_startup_ready()
            await release_upstream.wait()
            yield _sse_event({"type": "response.in_progress", "response": {"id": "resp_cooldown_wait"}})
            yield _sse_event({"type": "response.completed", "response": {"id": "resp_cooldown_wait"}})

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=False,
        sse_keepalive_interval_seconds=0.01,
        proxy_account_stream_recovery_reserve=1,
        proxy_api_key_fair_share_congestion_threshold_pct=0,
    )
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module.proxy_service_module, "get_settings", lambda: settings)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [],
        }
    )
    payload = proxy_api_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    )

    response = await proxy_api_module._stream_responses(
        request,
        payload,
        ProxyContext(service=cast(proxy_module.ProxyService, _FakeService())),
        api_key=None,
        enforce_openai_sdk_contract=False,
    )

    assert isinstance(response, StreamingResponse)
    assert upstream_started.is_set() is True
    iterator = response.body_iterator.__aiter__()
    first_chunk = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert first_chunk == CODEX_KEEPALIVE_FRAME
    release_upstream.set()
    second_chunk = cast(str, await asyncio.wait_for(iterator.__anext__(), timeout=0.2))
    third_chunk = cast(str, await asyncio.wait_for(iterator.__anext__(), timeout=0.2))
    assert "response.in_progress" in second_chunk
    assert "response.completed" in third_chunk


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
async def test_proxy_stream_fails_over_after_first_event_stream_idle_timeout(async_client, monkeypatch):
    expected_account_id_1 = await _import_account(async_client, "acc_idle_1", "idle-one@example.com")
    expected_account_id_2 = await _import_account(async_client, "acc_idle_2", "idle-two@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        if account_id == "acc_idle_1":
            event = {
                "type": "response.failed",
                "response": {"error": {"code": "stream_idle_timeout", "message": "idle"}},
            }
            yield _sse_event(event)
            return
        event = {"type": "response.completed", "response": {"id": "resp_idle_ok", "usage": {}}}
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
    assert event["response"]["id"] == "resp_idle_ok"

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        logs = list(result.scalars().all())
        assert len(logs) == 2
        by_account = {log.account_id: log for log in logs}
        assert by_account[expected_account_id_1].error_code == "stream_idle_timeout"
        assert by_account[expected_account_id_2].status == "success"

    service = get_proxy_service_for_app(async_client._transport.app)
    idle_runtime = service._load_balancer._runtime.get(expected_account_id_1)
    assert idle_runtime is None or idle_runtime.error_count == 0


@pytest.mark.asyncio
async def test_proxy_stream_drops_forwarded_headers(async_client, monkeypatch):
    await _import_account(async_client, "acc_headers", "headers@example.com")
    captured_headers: dict[str, str] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        captured_headers.update(headers)
        event = {
            "type": "response.completed",
            "response": {"id": "resp_headers", "usage": {"input_tokens": 1, "output_tokens": 1}},
        }
        yield _sse_event(event)

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_headers = {
        "x-forwarded-for": "1.2.3.4",
        "x-forwarded-proto": "https",
        "x-real-ip": "1.2.3.4",
        "forwarded": "for=1.2.3.4;proto=https",
        "cf-connecting-ip": "1.2.3.4",
        "cf-ray": "ray123",
        "true-client-ip": "1.2.3.4",
        "user-agent": "codex-test",
    }
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers=request_headers,
    ) as resp:
        assert resp.status_code == 200
        _ = [line async for line in resp.aiter_lines() if line]

    normalized = {key.lower() for key in captured_headers}
    assert "x-forwarded-for" not in normalized
    assert "x-forwarded-proto" not in normalized
    assert "x-real-ip" not in normalized
    assert "forwarded" not in normalized
    assert "cf-connecting-ip" not in normalized
    assert "cf-ray" not in normalized
    assert "true-client-ip" not in normalized
    assert "user-agent" in normalized


@pytest.mark.asyncio
async def test_proxy_stream_usage_limit_returns_http_error(async_client, monkeypatch):
    raw_account_id = "acc_stream_usage_limit"
    expected_account_id = await _import_account(async_client, raw_account_id, "stream-usage-limit@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        assert account_id == raw_account_id
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
    # This regression checks that the startup probe turns a pre-first-event
    # upstream usage-limit failure into an HTTP error and still marks the account
    # unhealthy. Keep the test on the single-candidate branch so PostgreSQL CI
    # does not spend the probe budget on an intentionally absent failover target.
    # Full-suite PostgreSQL runs can spend several seconds persisting the
    # RATE_LIMITED state before the stream raises the startup error.
    monkeypatch.setattr(proxy_module, "_STREAM_MAX_ACCOUNT_ATTEMPTS", 1)
    monkeypatch.setattr(proxy_api_module, "_STREAM_STARTUP_ERROR_PROBE_SECONDS", 30.0)

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
