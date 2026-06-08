from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import app.modules.agent_provider_runtime.service as runtime_service_module
from app.modules.agent_provider_runtime.antigravity import AntigravityProcessResult

pytestmark = pytest.mark.integration


class _Response:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        chunks: list[bytes] | None = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._payload = payload or {}
        self.content = _Content(chunks or [])

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self._payload

    async def text(self) -> str:
        return "upstream-error"


class _Content:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_any(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self._responses.pop(0)


async def _create_proxy_key(async_client, payload: dict[str, object] | None = None) -> str:
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings.status_code == 200
    body = {"name": "gemini-proxy", **(payload or {})}
    response = await async_client.post("/api/api-keys/", json=body)
    assert response.status_code == 200
    return response.json()["key"]


async def _create_gemini_account(
    async_client,
    *,
    display_name: str = "Gemini API",
    api_key: str = "AIza-test-secret",
) -> str:
    response = await async_client.post(
        "/api/agent-providers/gemini/accounts",
        json={"displayName": display_name, "apiKey": api_key},
    )
    assert response.status_code == 200
    return response.json()["accountId"]


async def _create_antigravity_account(async_client) -> str:
    response = await async_client.post(
        "/api/agent-providers/antigravity/accounts",
        json={"displayName": "Antigravity default", "externalAccountId": "default"},
    )
    assert response.status_code == 200
    return response.json()["accountId"]


async def _create_antigravity_api_key_account(async_client) -> str:
    response = await async_client.post(
        "/api/agent-providers/antigravity/accounts",
        json={"displayName": "Antigravity API", "authMode": "api_key", "apiKey": "AIza-antigravity-secret"},
    )
    assert response.status_code == 200
    return response.json()["accountId"]


@pytest.mark.asyncio
async def test_gemini_chat_completion_route_uses_provider_account_and_proxy_auth(async_client, monkeypatch) -> None:
    key = await _create_proxy_key(
        async_client,
        {"limits": [{"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 20000}]},
    )
    account_id = await _create_gemini_account(async_client)
    request_quota = await async_client.put(
        f"/api/agent-providers/gemini/accounts/{account_id}/quota-windows/requests_per_day",
        json={"dimension": "requests_per_day", "used": 0, "limit": 10},
    )
    prompt_quota = await async_client.put(
        f"/api/agent-providers/gemini/accounts/{account_id}/quota-windows/prompt_tokens",
        json={"dimension": "prompt_tokens", "used": 10, "limit": 100},
    )
    assert request_quota.status_code == 200
    assert prompt_quota.status_code == 200
    settings_response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={"strategy": "single_account", "singleAccountId": account_id},
    )
    assert settings_response.status_code == 200

    session = _Session(
        [
            _Response(
                {
                    "responseId": "resp_api",
                    "candidates": [{"content": {"parts": [{"text": "Pong"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3},
                }
            )
        ]
    )

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)

    response = await async_client.post(
        "/v1/gemini/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Ping"}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "Pong"
    assert session.calls[0]["headers"]["x-goog-api-key"] == "AIza-test-secret"
    assert session.calls[0]["json"] == {"contents": [{"role": "user", "parts": [{"text": "Ping"}]}]}
    preflight = await async_client.post("/api/agent-providers/gemini/routing/preflight")
    assert preflight.status_code == 200
    windows = {
        window["dimension"]: window["used"]
        for account in preflight.json()["accounts"]
        if account["accountId"] == account_id
        for window in account["quotaWindows"]
    }
    assert windows["requests_per_day"] == 1
    assert windows["prompt_tokens"] == 12
    api_keys = await async_client.get("/api/api-keys/")
    assert api_keys.status_code == 200
    api_key_row = api_keys.json()[0]
    assert api_key_row["usageSummary"]["requestCount"] == 1
    assert api_key_row["usageSummary"]["totalTokens"] == 3
    assert api_key_row["limits"][0]["currentValue"] == 3
    request_logs = await async_client.get("/api/request-logs")
    assert request_logs.status_code == 200
    log = request_logs.json()["requests"][0]
    assert log["source"] == "gemini"
    assert log["transport"] == "gemini_native"
    assert log["apiKeyId"] == api_key_row["id"]
    assert log["inputTokens"] == 2
    assert log["outputTokens"] == 1


@pytest.mark.asyncio
async def test_gemini_stream_route_reports_startup_errors_before_200(async_client) -> None:
    key = await _create_proxy_key(async_client)

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Ping"}], "stream": True},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_direct_gemini_stream_route_reports_startup_errors_before_200(async_client) -> None:
    key = await _create_proxy_key(async_client)

    response = await async_client.post(
        "/v1/gemini/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Ping"}], "stream": True},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_gemini_expired_quota_window_advances_reset_after_settlement(async_client, monkeypatch) -> None:
    key = await _create_proxy_key(async_client)
    account_id = await _create_gemini_account(async_client)
    expired_reset = datetime.now(timezone.utc) - timedelta(minutes=5)
    quota_response = await async_client.put(
        f"/api/agent-providers/gemini/accounts/{account_id}/quota-windows/requests_per_day",
        json={"dimension": "requests_per_day", "used": 99, "limit": 100, "resetAt": expired_reset.isoformat()},
    )
    assert quota_response.status_code == 200
    settings_response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={"strategy": "single_account", "singleAccountId": account_id},
    )
    assert settings_response.status_code == 200
    session = _Session(
        [
            _Response(
                {
                    "responseId": "resp_reset",
                    "candidates": [{"content": {"parts": [{"text": "Pong"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3},
                }
            )
        ]
    )

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Ping"}]},
    )

    assert response.status_code == 200
    preflight = await async_client.post("/api/agent-providers/gemini/routing/preflight")
    assert preflight.status_code == 200
    window = next(
        window
        for account in preflight.json()["accounts"]
        if account["accountId"] == account_id
        for window in account["quotaWindows"]
        if window["dimension"] == "requests_per_day"
    )
    assert window["used"] == 1
    assert datetime.fromisoformat(window["resetAt"]) > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_standard_chat_completion_route_dispatches_gemini_models(async_client, monkeypatch) -> None:
    key = await _create_proxy_key(async_client, {"allowedModels": []})
    account_id = await _create_gemini_account(async_client)
    request_quota = await async_client.put(
        f"/api/agent-providers/gemini/accounts/{account_id}/quota-windows/requests_per_day",
        json={"dimension": "requests_per_day", "used": 0, "limit": 10},
    )
    prompt_quota = await async_client.put(
        f"/api/agent-providers/gemini/accounts/{account_id}/quota-windows/prompt_tokens",
        json={"dimension": "prompt_tokens", "used": 0, "limit": 100},
    )
    assert request_quota.status_code == 200
    assert prompt_quota.status_code == 200
    settings_response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={"strategy": "single_account", "singleAccountId": account_id},
    )
    assert settings_response.status_code == 200
    session = _Session(
        [
            _Response(
                {
                    "responseId": "resp_standard",
                    "candidates": [{"content": {"parts": [{"text": "Standard route"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5},
                }
            )
        ]
    )

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Ping"}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["choices"][0]["message"]["content"] == "Standard route"
    assert session.calls[0]["url"].endswith("/models/gemini-2.5-flash:generateContent")
    preflight = await async_client.post("/api/agent-providers/gemini/routing/preflight")
    assert preflight.status_code == 200
    windows = {
        window["dimension"]: window["used"]
        for account in preflight.json()["accounts"]
        if account["accountId"] == account_id
        for window in account["quotaWindows"]
    }
    assert windows["requests_per_day"] == 1
    assert windows["prompt_tokens"] == 3


@pytest.mark.asyncio
async def test_gemini_runtime_round_robin_persists_selected_cursor(async_client, monkeypatch) -> None:
    key = await _create_proxy_key(async_client)
    first_id = await _create_gemini_account(
        async_client,
        display_name="Gemini round robin A",
        api_key="AIza-round-robin-a",
    )
    second_id = await _create_gemini_account(
        async_client,
        display_name="Gemini round robin B",
        api_key="AIza-round-robin-b",
    )
    settings_response = await async_client.patch(
        "/api/agent-providers/gemini/routing/settings",
        json={"strategy": "round_robin", "quotaThresholdPct": 100.0},
    )
    assert settings_response.status_code == 200

    preflight = await async_client.post("/api/agent-providers/gemini/routing/preflight")
    assert preflight.status_code == 200
    selected_id = preflight.json()["selectedAccountId"]
    assert selected_id in {first_id, second_id}

    session = _Session(
        [
            _Response(
                {
                    "responseId": "resp_round_robin",
                    "candidates": [{"content": {"parts": [{"text": "Cursor saved"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
                }
            )
        ]
    )

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Ping"}]},
    )

    assert response.status_code == 200
    settings_after = await async_client.get("/api/agent-providers/gemini/routing/settings")
    assert settings_after.status_code == 200
    assert settings_after.json()["roundRobinCursor"] == selected_id


@pytest.mark.asyncio
async def test_gemini_chat_completion_route_enforces_api_key_model_policy(async_client, monkeypatch) -> None:
    key = await _create_proxy_key(
        async_client,
        {
            "allowedModels": ["gemini-2.5-flash"],
            "enforcedModel": "gemini-2.5-flash",
        },
    )
    await _create_gemini_account(async_client)
    session = _Session(
        [
            _Response(
                {
                    "responseId": "resp_enforced",
                    "candidates": [{"content": {"parts": [{"text": "Policy ok"}]}, "finishReason": "STOP"}],
                }
            )
        ]
    )

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)

    response = await async_client.post(
        "/v1/gemini/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "ignored-request-model", "messages": [{"role": "user", "content": "Ping"}]},
    )

    assert response.status_code == 200
    assert session.calls[0]["url"].endswith("/models/gemini-2.5-flash:generateContent")


@pytest.mark.asyncio
async def test_gemini_chat_completion_route_rejects_api_key_disallowed_model(async_client) -> None:
    key = await _create_proxy_key(async_client, {"allowedModels": ["gemini-2.5-flash"]})
    await _create_gemini_account(async_client)

    response = await async_client.post(
        "/v1/gemini/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gemini-2.5-pro", "messages": [{"role": "user", "content": "Ping"}]},
    )

    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["type"] == "permission_error"
    assert payload["error"]["code"] == "model_not_allowed"


@pytest.mark.asyncio
async def test_gemini_chat_completion_route_streams_openai_sse(async_client, monkeypatch) -> None:
    key = await _create_proxy_key(async_client)
    await _create_gemini_account(async_client)
    session = _Session(
        [
            _Response(
                chunks=[
                    (
                        b'data: {"responseId":"resp_stream","candidates":'
                        b'[{"content":{"parts":[{"text":"Hi"}]},"finishReason":"STOP"}]}\n\n'
                    ),
                ]
            )
        ]
    )

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)

    async with async_client.stream(
        "POST",
        "/v1/gemini/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gemini-2.5-flash", "stream": True, "messages": [{"role": "user", "content": "Ping"}]},
    ) as response:
        text = await response.aread()

    assert response.status_code == 200
    body = text.decode("utf-8")
    assert '"object":"chat.completion.chunk"' in body
    assert '"content":"Hi"' in body
    assert "data: [DONE]" in body


@pytest.mark.asyncio
async def test_gemini_chat_completion_route_preserves_upstream_retry_after(async_client, monkeypatch) -> None:
    key = await _create_proxy_key(async_client)
    await _create_gemini_account(async_client)
    session = _Session(
        [
            _Response(
                {"error": {"message": "Gemini quota exhausted"}},
                status=429,
                headers={"Retry-After": "60"},
            )
        ]
    )

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)

    response = await async_client.post(
        "/v1/gemini/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Ping"}]},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    payload = response.json()
    assert payload["error"]["type"] == "rate_limit_error"
    assert payload["error"]["message"] == "Gemini quota exhausted"


@pytest.mark.asyncio
async def test_gemini_chat_completion_route_rejects_invalid_payload_with_openai_error(async_client) -> None:
    key = await _create_proxy_key(async_client)

    response = await async_client.post(
        "/v1/gemini/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"messages": [{"role": "user", "content": "Ping"}]},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["message"] == "model is required"


@pytest.mark.asyncio
async def test_antigravity_harness_print_route_uses_selected_cli_profile(async_client, monkeypatch, tmp_path) -> None:
    account_id = await _create_antigravity_account(async_client)
    settings_response = await async_client.patch(
        "/api/agent-providers/antigravity/routing/settings",
        json={"strategy": "single_account", "singleAccountId": account_id},
    )
    assert settings_response.status_code == 200
    quota_response = await async_client.put(
        f"/api/agent-providers/antigravity/accounts/{account_id}/quota-windows/requests",
        json={"dimension": "requests", "used": 0, "limit": 10},
    )
    assert quota_response.status_code == 200
    calls = []

    async def _run(self, command, *, env):
        calls.append((command, env))
        return AntigravityProcessResult(exit_code=0, stdout="hello from agy", stderr="", duration_ms=9)

    monkeypatch.setattr(runtime_service_module.AntigravitySubprocessRunner, "run", _run)

    response = await async_client.post(
        "/api/agent-providers/antigravity/harness/print",
        json={"prompt": "Say hello", "workspacePath": str(tmp_path), "timeoutSeconds": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["providerId"] == "antigravity"
    assert payload["accountId"] == account_id
    assert payload["externalAccountId"] == "default"
    assert payload["stdout"] == "hello from agy"
    assert payload["command"][:6] == ["agy", "--print", "--print-timeout", "5s", "--prompt", "<redacted>"]
    assert "--dangerously-skip-permissions" not in calls[0][0].args
    assert calls[0][1]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert calls[0][1]["AGY_CLI_PROFILE"] == "default"
    assert calls[0][1]["ANTIGRAVITY_CLI_PROFILE"] == "default"
    preflight = await async_client.post("/api/agent-providers/antigravity/routing/preflight")
    assert preflight.status_code == 200
    windows = {
        window["dimension"]: window["used"]
        for account in preflight.json()["accounts"]
        if account["accountId"] == account_id
        for window in account["quotaWindows"]
    }
    assert windows.get("requests") == 1


@pytest.mark.asyncio
async def test_antigravity_interactions_route_uses_api_key_account(async_client, monkeypatch) -> None:
    key = await _create_proxy_key(async_client)
    account_id = await _create_antigravity_api_key_account(async_client)
    settings_response = await async_client.patch(
        "/api/agent-providers/antigravity/routing/settings",
        json={"strategy": "single_account", "singleAccountId": account_id},
    )
    assert settings_response.status_code == 200
    session = _Session(
        [
            _Response(
                {
                    "id": "interaction_1",
                    "output_text": "done",
                    "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2, "totalTokenCount": 6},
                }
            )
        ]
    )

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)

    response = await async_client.post(
        "/v1/antigravity/interactions",
        headers={"Authorization": f"Bearer {key}"},
        json={"agent": "antigravity-preview-05-2026", "input": "Do work", "environment": "remote"},
    )

    assert response.status_code == 200
    assert response.json()["output_text"] == "done"
    call = session.calls[0]
    assert call["url"].endswith("/v1beta/interactions")
    assert call["headers"]["x-goog-api-key"] == "AIza-antigravity-secret"
    assert call["headers"]["Api-Revision"] == "2026-05-20"
    assert call["json"]["agent"] == "antigravity-preview-05-2026"
    preflight = await async_client.post("/api/agent-providers/antigravity/routing/preflight")
    assert preflight.status_code == 200
    windows = {
        window["dimension"]: window["used"]
        for account in preflight.json()["accounts"]
        if account["accountId"] == account_id
        for window in account["quotaWindows"]
    }
    assert windows == {}


@pytest.mark.asyncio
async def test_antigravity_dashboard_interaction_run_uses_api_key_account(async_client, monkeypatch) -> None:
    account_id = await _create_antigravity_api_key_account(async_client)
    settings_response = await async_client.patch(
        "/api/agent-providers/antigravity/routing/settings",
        json={"strategy": "single_account", "singleAccountId": account_id},
    )
    assert settings_response.status_code == 200
    session = _Session(
        [
            _Response(
                {
                    "id": "interaction_dashboard",
                    "steps": [{"type": "model_output", "content": [{"type": "text", "text": "dashboard done"}]}],
                }
            )
        ]
    )

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)

    response = await async_client.post(
        "/api/agent-providers/antigravity/interactions/run",
        json={
            "agent": "antigravity-preview-05-2026",
            "input": "Summarize this repo",
            "environment": "remote",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["providerId"] == "antigravity"
    assert payload["agent"] == "antigravity-preview-05-2026"
    assert payload["outputText"] == "dashboard done"
    assert payload["response"]["id"] == "interaction_dashboard"
    assert session.calls[0]["headers"]["x-goog-api-key"] == "AIza-antigravity-secret"
    assert session.calls[0]["headers"]["Api-Revision"] == "2026-05-20"


@pytest.mark.asyncio
async def test_antigravity_interactions_route_enforces_api_key_model_policy(async_client) -> None:
    key = await _create_proxy_key(async_client, {"allowedModels": ["gemini-2.5-flash"]})
    await _create_antigravity_api_key_account(async_client)

    response = await async_client.post(
        "/v1/antigravity/interactions",
        headers={"Authorization": f"Bearer {key}"},
        json={"agent": "antigravity-preview-05-2026", "input": "Do work", "environment": "remote"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_not_allowed"


@pytest.mark.asyncio
async def test_standard_chat_completion_route_dispatches_antigravity_models(async_client, monkeypatch) -> None:
    key = await _create_proxy_key(async_client, {"allowedModels": []})
    account_id = await _create_antigravity_api_key_account(async_client)
    settings_response = await async_client.patch(
        "/api/agent-providers/antigravity/routing/settings",
        json={"strategy": "single_account", "singleAccountId": account_id},
    )
    assert settings_response.status_code == 200
    session = _Session(
        [
            _Response(
                {
                    "id": "interaction_chat",
                    "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Agent result"}]}],
                }
            )
        ]
    )

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)

    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "antigravity-preview-05-2026",
            "messages": [{"role": "user", "content": "Plan and run"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == "antigravity-preview-05-2026"
    assert payload["choices"][0]["message"]["content"] == "Agent result"
    assert session.calls[0]["json"] == {
        "agent": "antigravity-preview-05-2026",
        "input": "user: Plan and run",
        "environment": "remote",
    }


@pytest.mark.asyncio
async def test_v1_models_includes_antigravity_managed_agent(async_client) -> None:
    key = await _create_proxy_key(async_client, {"allowedModels": ["antigravity-preview-05-2026"]})

    response = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})

    assert response.status_code == 200
    models = response.json()["data"]
    assert [model["id"] for model in models] == ["antigravity-preview-05-2026"]
    assert models[0]["provider"] == "antigravity"
    assert models[0]["protocol"] == "interactions_api"
