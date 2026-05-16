from __future__ import annotations

import base64
import json

import pytest
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import CodexControlResponse, ProxyResponseError
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.proxy.load_balancer import AccountSelection

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
        return AccountSelection(
            account=None,
            error_message="No scoped accounts are available",
            error_code="no_accounts",
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget_compatible", fake_select)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "no_accounts"


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
async def test_thread_goal_set_uses_active_account_when_budget_selection_is_empty(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_control", "goal-control@example.com")
    calls = []

    async def fake_select(*_args, **_kwargs):
        return AccountSelection(account=None, error_message="No active accounts available", error_code="no_accounts")

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

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget_compatible", fake_select)
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
        return CodexControlResponse(
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
async def test_codex_realtime_call_forwards_raw_sdp_and_location(async_client, monkeypatch):
    await _import_account(async_client, "acc_codex_realtime", "codex-realtime@example.com")
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
        return CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/realtime/calls/call_123"},
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    response = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp"},
    )

    assert response.status_code == 201
    assert response.content == b"v=answer\r\n"
    assert response.headers["location"] == "/v1/realtime/calls/call_123"
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
    assert calls == 2
    assert len(handled) == 1
    assert handled[0][0].startswith("acc_codex_retry_error")
    assert handled[0][1] == 503


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
        return CodexControlResponse(
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
async def test_proxy_stream_does_not_retry_stream_idle_timeout(async_client, monkeypatch):
    await _import_account(async_client, "acc_idle_1", "idle-one@example.com")
    await _import_account(async_client, "acc_idle_2", "idle-two@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        event = {
            "type": "response.failed",
            "response": {"error": {"code": "stream_idle_timeout", "message": "idle"}},
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
    assert event["type"] == "response.failed"
    assert event["response"]["error"]["code"] == "stream_idle_timeout"

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        logs = list(result.scalars().all())
        assert len(logs) == 1
        assert logs[0].error_code == "stream_idle_timeout"


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
