from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.core.clients.proxy as proxy_client_module
import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.config.settings import Settings
from app.db.models import Account, DashboardSettings, RequestLog
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository

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


def _extract_first_event(lines: list[str]) -> dict:
    for line in lines:
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("No SSE data event found")


class _FakeUpstreamWebSocket:
    def __init__(self, messages: list[object]) -> None:
        self._messages = list(messages)
        self.sent_json: list[dict[str, object]] = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.closed = True
        return False

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent_json.append(payload)

    async def receive(self):
        if self._messages:
            return self._messages.pop(0)
        return SimpleNamespace(type=proxy_client_module.aiohttp.WSMsgType.CLOSE, data=None, extra=None)

    async def close(self) -> None:
        self.closed = True

    def exception(self):
        return None


@pytest.fixture(autouse=True)
def _disable_http_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    app_settings = Settings(
        http_responses_session_bridge_enabled=False,
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        upstream_stream_transport="auto",
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
        stream_idle_timeout_seconds=300.0,
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
    )
    dashboard_settings = DashboardSettings(
        id=1,
        sticky_threads_enabled=False,
        upstream_stream_transport="auto",
        prefer_earlier_reset_accounts=False,
        routing_strategy="usage_weighted",
        openai_cache_affinity_max_age_seconds=300,
        import_without_overwrite=False,
        totp_required_on_login=False,
        api_key_auth_enabled=False,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
        sticky_reallocation_budget_threshold_pct=95.0,
    )

    class _SettingsCache:
        async def get(self) -> DashboardSettings:
            return dashboard_settings

    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_module, "get_settings", lambda: app_settings)


@pytest.mark.asyncio
async def test_proxy_responses_no_accounts(async_client):
    payload = {"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_stream_123"
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["object"] == "response"
    assert event["response"]["status"] == "failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_proxy_responses_stream_surfaces_additional_quota_data_unavailable(async_client):
    email = "gated-unavailable@example.com"
    raw_account_id = "acc_gated_unavailable"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    payload = {"model": "gpt-5.3-codex-spark", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["error"]["code"] == "additional_quota_data_unavailable"


@pytest.mark.asyncio
async def test_proxy_responses_accepts_openai_style_payload_without_instructions(async_client):
    payload = {"model": "gpt-5.1", "input": "hi", "stream": True}
    request_id = "req_backend_openai_style_123"
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_v1_responses_routes(async_client):
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_v1_stream_123"
    async with async_client.stream(
        "POST",
        "/v1/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["object"] == "response"
    assert event["response"]["status"] == "failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_v1_responses_routes_under_root_path(app_instance):
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_v1_root_path_123"
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, root_path="/api")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with client.stream(
                "POST",
                "/v1/responses",
                json=payload,
                headers={"x-request-id": request_id},
            ) as resp:
                assert resp.status_code == 200
                lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["object"] == "response"
    assert event["response"]["status"] == "failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_v1_responses_previous_response_not_found_without_http_bridge_returns_stream_incomplete(
    async_client,
    monkeypatch,
):
    email = "prev-http-fallback@example.com"
    raw_account_id = "acc_prev_http_fallback"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        error_payload = proxy_module.openai_error(
            "previous_response_not_found",
            "Previous response with id 'resp_prev_http_fallback' not found.",
            error_type="invalid_request_error",
        )
        error_payload["error"]["param"] = "previous_response_id"
        raise proxy_module.ProxyResponseError(400, error_payload)
        if False:
            yield ""

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "input": "continue",
            "previous_response_id": "resp_prev_http_fallback",
        },
        headers={"session_id": "sid_prev_http_fallback"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "stream_incomplete"
    assert response.json()["error"]["message"] == "Upstream websocket closed before response.completed"


@pytest.mark.asyncio
async def test_v1_responses_previous_response_not_found_without_http_bridge_and_missing_owner_returns_stream_incomplete(
    async_client,
    monkeypatch,
):
    email = "prev-http-missing-owner@example.com"
    raw_account_id = "acc_prev_http_missing_owner"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        error_payload = proxy_module.openai_error(
            "previous_response_not_found",
            "Previous response with id 'resp_prev_http_missing_owner' not found.",
            error_type="invalid_request_error",
        )
        error_payload["error"]["param"] = "previous_response_id"
        raise proxy_module.ProxyResponseError(400, error_payload)
        if False:
            yield ""

    async def fake_resolve_owner(self, *, previous_response_id, api_key, session_id, surface):
        del self, previous_response_id, api_key, session_id, surface
        return None

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", fake_resolve_owner)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "input": "continue",
            "previous_response_id": "resp_prev_http_missing_owner",
        },
        headers={"session_id": "sid_prev_http_missing_owner"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "stream_incomplete"
    assert response.json()["error"]["message"] == "Upstream websocket closed before response.completed"


@pytest.mark.asyncio
async def test_v1_responses_previous_response_owner_lookup_failure_without_http_bridge_returns_upstream_unavailable(
    async_client,
    monkeypatch,
):
    email = "prev-http-owner-lookup-failure@example.com"
    raw_account_id = "acc_prev_http_owner_lookup_failure"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fail_owner_lookup(self, *, response_id, api_key_id, session_id=None):
        del self, response_id, api_key_id, session_id
        raise RuntimeError("lookup unavailable")

    async def fail_stream(*args, **kwargs):
        del args, kwargs
        raise AssertionError("owner lookup failure must fail before upstream stream attempt")
        if False:
            yield ""

    monkeypatch.setattr(RequestLogsRepository, "find_latest_account_id_for_response_id", fail_owner_lookup)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_stream)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "input": "continue",
            "previous_response_id": "resp_prev_owner_lookup_failure",
        },
        headers={"session_id": "sid_prev_owner_lookup_failure"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert response.json()["error"]["message"] == "Previous response owner lookup failed; retry later."


@pytest.mark.asyncio
async def test_v1_responses_previous_response_followup_without_http_bridge_recovers_owner_from_request_logs(
    async_client,
    monkeypatch,
):
    owner_email = "prev-http-owner-anchor@example.com"
    owner_raw_account_id = "acc_prev_http_owner_anchor"
    owner_auth_json = _make_auth_json(owner_raw_account_id, owner_email)
    owner_files = {"auth_json": ("auth.json", json.dumps(owner_auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=owner_files)
    assert response.status_code == 200

    other_email = "prev-http-other-anchor@example.com"
    other_raw_account_id = "acc_prev_http_other_anchor"
    other_auth_json = _make_auth_json(other_raw_account_id, other_email)
    other_files = {"auth_json": ("auth.json", json.dumps(other_auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=other_files)
    assert response.status_code == 200

    async with SessionLocal() as session:
        accounts = {
            account.chatgpt_account_id: account
            for account in (await session.execute(select(Account))).scalars().all()
            if account.chatgpt_account_id in {owner_raw_account_id, other_raw_account_id}
        }

    owner_account = accounts[owner_raw_account_id]
    other_account = accounts[other_raw_account_id]
    selection_preferred_ids: list[str | None] = []

    async def fake_select_account(self, deadline: float, **kwargs):
        del self, deadline
        preferred_account_id = cast(str | None, kwargs.get("preferred_account_id"))
        selection_preferred_ids.append(preferred_account_id)
        if not selection_preferred_ids[:-1]:
            return proxy_module.AccountSelection(account=owner_account, error_message=None, error_code=None)
        if preferred_account_id == owner_account.id:
            return proxy_module.AccountSelection(account=owner_account, error_message=None, error_code=None)
        return proxy_module.AccountSelection(account=other_account, error_message=None, error_code=None)

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, base_url, raise_for_status, kwargs
        if payload.previous_response_id is None:
            assert account_id == owner_raw_account_id
            yield (
                'data: {"type":"response.completed","response":{"id":"resp_prev_http_anchor",'
                '"object":"response","status":"completed","usage":{"input_tokens":3,"output_tokens":1,"total_tokens":4}}}\n\n'
            )
            return
        if payload.previous_response_id == "resp_prev_http_anchor" and account_id == owner_raw_account_id:
            yield (
                'data: {"type":"response.completed","response":{"id":"resp_prev_http_followup",'
                '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1,"total_tokens":3}}}\n\n'
            )
            return
        error_payload = proxy_module.openai_error(
            "previous_response_not_found",
            "Previous response with id 'resp_prev_http_anchor' not found.",
            error_type="invalid_request_error",
        )
        error_payload["error"]["param"] = "previous_response_id"
        raise proxy_module.ProxyResponseError(400, error_payload)
        if False:
            yield ""

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget_compatible", fake_select_account)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    first_response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.1", "input": "start"},
        headers={"session_id": "sid_prev_http_anchor"},
    )

    assert first_response.status_code == 200
    assert first_response.json()["id"] == "resp_prev_http_anchor"
    async with SessionLocal() as session:
        persisted_log = (
            await session.execute(select(RequestLog).where(RequestLog.request_id == "resp_prev_http_anchor").limit(1))
        ).scalar_one_or_none()
    assert persisted_log is not None
    assert persisted_log.session_id == "sid_prev_http_anchor"

    second_response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "input": "continue",
            "previous_response_id": "resp_prev_http_anchor",
        },
        headers={"session_id": "sid_prev_http_anchor"},
    )

    assert second_response.status_code == 200
    assert second_response.json()["id"] == "resp_prev_http_followup"
    assert selection_preferred_ids == [None, owner_account.id]


@pytest.mark.asyncio
async def test_v1_responses_without_http_bridge_websocket_upstream_rejects_oversized_response_create_before_connect(
    async_client,
    monkeypatch,
):
    email = "stream-ws-oversized@example.com"
    raw_account_id = "acc_stream_ws_oversized"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    app_settings = Settings(
        http_responses_session_bridge_enabled=False,
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        upstream_stream_transport="auto",
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
        stream_idle_timeout_seconds=300.0,
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
    )
    dashboard_settings = DashboardSettings(
        id=1,
        sticky_threads_enabled=False,
        upstream_stream_transport="websocket",
        prefer_earlier_reset_accounts=False,
        routing_strategy="usage_weighted",
        openai_cache_affinity_max_age_seconds=300,
        import_without_overwrite=False,
        totp_required_on_login=False,
        api_key_auth_enabled=False,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
        sticky_reallocation_budget_threshold_pct=95.0,
    )

    class _SettingsCache:
        async def get(self) -> DashboardSettings:
            return dashboard_settings

    class _CoreProxySettings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "default"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    async def fail_open_upstream_websocket(**kwargs):
        del kwargs
        raise AssertionError("oversized response.create must fail before upstream websocket connect")

    monkeypatch.setattr(proxy_module, "get_settings", lambda: app_settings)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_client_module, "get_settings", lambda: _CoreProxySettings())
    monkeypatch.setattr(proxy_client_module, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64, raising=False)
    monkeypatch.setattr(proxy_client_module, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 128, raising=False)
    monkeypatch.setattr(proxy_client_module, "_open_upstream_websocket", fail_open_upstream_websocket)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "x" * 256}]}],
        },
    )

    assert response.status_code == 413
    payload = response.json()
    assert payload["error"]["code"] == "payload_too_large"
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["param"] == "input"
    assert "response.create is too large for upstream websocket" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_v1_responses_without_http_bridge_websocket_upstream_slims_historical_inline_artifacts_and_succeeds(
    async_client,
    monkeypatch,
):
    email = "stream-ws-slim@example.com"
    raw_account_id = "acc_stream_ws_slim"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    app_settings = Settings(
        http_responses_session_bridge_enabled=False,
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        upstream_stream_transport="auto",
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
        stream_idle_timeout_seconds=300.0,
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
    )
    dashboard_settings = DashboardSettings(
        id=1,
        sticky_threads_enabled=False,
        upstream_stream_transport="websocket",
        prefer_earlier_reset_accounts=False,
        routing_strategy="usage_weighted",
        openai_cache_affinity_max_age_seconds=300,
        import_without_overwrite=False,
        totp_required_on_login=False,
        api_key_auth_enabled=False,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
        sticky_reallocation_budget_threshold_pct=95.0,
    )

    class _SettingsCache:
        async def get(self) -> DashboardSettings:
            return dashboard_settings

    class _CoreProxySettings:
        upstream_base_url = "https://chatgpt.com/backend-api"
        upstream_stream_transport = "default"
        upstream_connect_timeout_seconds = 8.0
        stream_idle_timeout_seconds = 45.0
        max_sse_event_bytes = 1024
        image_inline_fetch_enabled = False
        log_upstream_request_payload = False
        proxy_request_budget_seconds = 75.0
        log_upstream_request_summary = False

    fake_upstream = _FakeUpstreamWebSocket(
        [
            SimpleNamespace(
                type=proxy_client_module.aiohttp.WSMsgType.TEXT,
                data=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_http_stream_slim", "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
                extra=None,
            ),
            SimpleNamespace(
                type=proxy_client_module.aiohttp.WSMsgType.TEXT,
                data=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_http_stream_slim",
                            "object": "response",
                            "status": "completed",
                            "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
                        },
                    },
                    separators=(",", ":"),
                ),
                extra=None,
            ),
        ]
    )

    async def fake_open_upstream_websocket(**kwargs):
        del kwargs
        return fake_upstream, fake_upstream

    monkeypatch.setattr(proxy_module, "get_settings", lambda: app_settings)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_client_module, "get_settings", lambda: _CoreProxySettings())
    monkeypatch.setattr(proxy_client_module, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64, raising=False)
    monkeypatch.setattr(proxy_client_module, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 640, raising=False)
    monkeypatch.setattr(proxy_client_module, "_open_upstream_websocket", fake_open_upstream_websocket)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "old turn"}]},
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "data:image/png;base64," + ("A" * 1200),
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64," + ("B" * 1200),
                        }
                    ],
                },
                {"role": "user", "content": [{"type": "input_text", "text": "latest turn"}]},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_http_stream_slim"
    assert fake_upstream.sent_json
    request_input = fake_upstream.sent_json[0]["input"]
    assert isinstance(request_input, list)
    tool_input = cast(dict[str, object], request_input[1])
    assistant_input = cast(dict[str, object], request_input[2])
    assert tool_input["output"] == proxy_module._RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE.format(
        bytes=len(("data:image/png;base64," + ("A" * 1200)).encode("utf-8"))
    )
    assert assistant_input["content"] == [
        {"type": "input_text", "text": proxy_module._RESPONSE_CREATE_IMAGE_OMISSION_NOTICE}
    ]


@pytest.mark.asyncio
async def test_v1_responses_accepts_messages(async_client):
    payload = {
        "model": "gpt-5.1",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    request_id = "req_v1_messages_123"
    async with async_client.stream(
        "POST",
        "/v1/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["object"] == "response"
    assert event["response"]["status"] == "failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_v1_responses_without_instructions(async_client):
    payload = {"model": "gpt-5.1", "input": [{"role": "user", "content": "hi"}], "stream": True}
    request_id = "req_v1_no_instructions_123"
    async with async_client.stream(
        "POST",
        "/v1/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["object"] == "response"
    assert event["response"]["status"] == "failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_failed_returns_error(async_client):
    payload = {"model": "gpt-5.1", "input": "hi"}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_proxy_responses_streams_upstream(async_client, monkeypatch):
    email = "streamer@example.com"
    raw_account_id = "acc_live"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen["access_token"] = access_token
        seen["account_id"] = account_id
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_1","usage":'
            '{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_stream_123"
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
    assert seen["access_token"] == "access-token"
    assert seen["account_id"] == raw_account_id

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog)
            .where(RequestLog.account_id == expected_account_id)
            .order_by(RequestLog.requested_at.desc())
        )
        log = result.scalars().first()
        assert log is not None
        assert log.request_id == "resp_1"
        assert log.transport == "http"


@pytest.mark.asyncio
async def test_proxy_responses_forwards_native_codex_headers(async_client, monkeypatch):
    email = "stream-headers@example.com"
    raw_account_id = "acc_stream_headers"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_headers: dict[str, str] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del payload, access_token, account_id, base_url, raise_for_status
        seen_headers.update(headers)
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True}
    native_headers = {
        "originator": "Codex Desktop",
        "session_id": "sid-native",
        "x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"none"}',
        "x-codex-beta-features": "js_repl,multi_agent",
        "x-request-id": "req_native_headers_123",
    }

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers=native_headers,
    ) as resp:
        assert resp.status_code == 200
        _ = [line async for line in resp.aiter_lines() if line]

    assert seen_headers["originator"] == native_headers["originator"]
    assert seen_headers["session_id"] == native_headers["session_id"]
    assert seen_headers["x-codex-turn-metadata"] == native_headers["x-codex-turn-metadata"]
    assert seen_headers["x-codex-beta-features"] == native_headers["x-codex-beta-features"]
    assert seen_headers["x-request-id"] == native_headers["x-request-id"]


@pytest.mark.asyncio
async def test_v1_responses_stream_preserves_done_text_events(async_client, monkeypatch):
    email = "done-filter@example.com"
    raw_account_id = "acc_done_filter"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield 'data: {"type":"response.output_text.delta","delta":"Hey there! "}\n\n'
        yield 'data: {"type":"response.output_text.delta","delta":"What are we tackling?"}\n\n'
        yield 'data: {"type":"response.output_text.done","text":"Hey there! What are we tackling?"}\n\n'
        yield (
            'data: {"type":"response.content_part.done","part":{"type":"output_text",'
            '"text":"Hey there! What are we tackling?"}}\n\n'
        )
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.2", "input": "hi", "stream": True}
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event_types: list[str] = []
    for line in lines:
        if not line.startswith("data: "):
            continue
        data = json.loads(line[6:])
        event_type = data.get("type")
        if isinstance(event_type, str):
            event_types.append(event_type)

    assert "response.output_text.delta" in event_types
    assert "response.completed" in event_types
    assert "response.output_text.done" in event_types
    assert "response.content_part.done" in event_types


@pytest.mark.asyncio
async def test_v1_responses_stream_keeps_non_text_content_part_done_events(async_client, monkeypatch):
    email = "done-filter-non-text@example.com"
    raw_account_id = "acc_done_filter_non_text"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield 'data: {"type":"response.output_text.delta","delta":"First line"}\n\n'
        yield (
            'data: {"type":"response.content_part.done","part":{"type":"output_image",'
            '"image_url":"https://example.com/a.png"}}\n\n'
        )
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.2", "input": "hi", "stream": True}
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    content_part_events: list[dict[str, object]] = []
    for line in lines:
        if not line.startswith("data: "):
            continue
        raw_payload = line[6:]
        if raw_payload == "[DONE]":
            continue
        data = json.loads(raw_payload)
        if data.get("type") == "response.content_part.done":
            content_part_events.append(data)

    assert content_part_events
    assert content_part_events[0]["part"] == {"type": "output_image", "image_url": "https://example.com/a.png"}


@pytest.mark.asyncio
async def test_backend_responses_stream_preserves_done_text_events(async_client, monkeypatch):
    email = "done-preserve@example.com"
    raw_account_id = "acc_done_preserve"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield 'data: {"type":"response.output_text.delta","delta":"Hey there! "}\n\n'
        yield 'data: {"type":"response.output_text.delta","delta":"What are we tackling?"}\n\n'
        yield 'data: {"type":"response.output_text.done","text":"Hey there! What are we tackling?"}\n\n'
        yield (
            'data: {"type":"response.content_part.done","part":{"type":"output_text",'
            '"text":"Hey there! What are we tackling?"}}\n\n'
        )
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.2", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event_types: list[str] = []
    for line in lines:
        if not line.startswith("data: "):
            continue
        raw_payload = line[6:]
        if raw_payload == "[DONE]":
            continue
        data = json.loads(raw_payload)
        event_type = data.get("type")
        if isinstance(event_type, str):
            event_types.append(event_type)

    assert "response.output_text.delta" in event_types
    assert "response.output_text.done" in event_types
    assert "response.content_part.done" in event_types
    assert "response.completed" in event_types


@pytest.mark.asyncio
async def test_v1_responses_sanitizes_interleaved_reasoning_fields(async_client, monkeypatch):
    email = "reasoning-sanitize@example.com"
    raw_account_id = "acc_reasoning_sanitize"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_input: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen_input["input"] = payload.input
        yield 'data: {"type":"response.completed","response":{"id":"resp_reasoning_sanitize"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "input": [
            {
                "role": "user",
                "reasoning_content": "drop",
                "tool_calls": [{"id": "call_1", "type": "function"}],
                "function_call": {"name": "noop", "arguments": "{}"},
                "content": [
                    {"type": "input_text", "text": "hello"},
                    {"type": "reasoning", "reasoning_details": {"tokens": 4}},
                    {"type": "input_text", "text": "world", "reasoning_content": "drop"},
                ],
            }
        ],
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert seen_input["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "hello"},
                {"type": "input_text", "text": "world"},
            ],
        }
    ]


@pytest.mark.asyncio
async def test_proxy_responses_forces_stream(async_client, monkeypatch):
    email = "stream-force@example.com"
    raw_account_id = "acc_stream_force"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    observed_stream: dict[str, bool | None] = {"value": None}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        observed_stream["value"] = payload.stream
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": False}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert observed_stream["value"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", ["web_search", "web_search_preview"])
async def test_proxy_responses_accepts_builtin_tools(async_client, monkeypatch, tool_type):
    email = "tools@example.com"
    raw_account_id = "acc_tools"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen["payload"] = payload
        yield 'data: {"type":"response.completed","response":{"id":"resp_tools"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "tools": [{"type": tool_type}],
        "stream": True,
    }
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert getattr(seen.get("payload"), "tools", None) == [{"type": "web_search"}]


@pytest.mark.asyncio
async def test_v1_responses_streams_event_sequence(async_client, monkeypatch):
    email = "stream-seq@example.com"
    raw_account_id = "acc_stream_seq"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield 'data: {"type":"response.created","response":{"id":"resp_1"}}\n\n'
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield 'data: {"type":"response.function_call_arguments.delta","delta":"{}"}\n\n'
        yield 'data: {"type":"response.refusal.delta","delta":"no"}\n\n'
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    assert any("response.output_text.delta" in line for line in lines)
    assert any("response.function_call_arguments.delta" in line for line in lines)
    assert any("response.refusal.delta" in line for line in lines)


@pytest.mark.asyncio
async def test_proxy_responses_stream_large_event_line(async_client, monkeypatch):
    email = "stream-large@example.com"
    raw_account_id = "acc_stream_large"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        delta = "A" * (200 * 1024)
        yield f'data: {{"type":"response.output_text.delta","delta":"{delta}"}}\n\n'
        yield 'data: {"type":"response.completed","response":{"id":"resp_large"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_stream_large_123"
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    assert any("response.output_text.delta" in line for line in lines)
    assert any("response.completed" in line for line in lines)
    assert not any("stream_event_too_large" in line for line in lines)


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_returns_response(async_client, monkeypatch):
    email = "responses-nonstream@example.com"
    raw_account_id = "acc_responses_nonstream"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    observed_stream: dict[str, bool | None] = {"value": None}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        observed_stream["value"] = payload.stream
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_1","object":"response",'
            '"status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": [{"role": "user", "content": "hi"}], "stream": False}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "resp_1"
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert observed_stream["value"] is True


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_reconstructs_reasoning_output(async_client, monkeypatch):
    email = "responses-reasoning-output@example.com"
    raw_account_id = "acc_responses_reasoning_output"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield (
            'data: {"type":"response.output_item.done","output_index":0,"item":{"id":"rs_1",'
            '"type":"reasoning","summary":[{"type":"summary_text","text":"Need more steps"}],'
            '"reasoning_details":{"tokens":4}}}\n\n'
        )
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_reasoning_1","object":"response",'
            '"status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": [{"role": "user", "content": "hi"}], "stream": False}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "resp_reasoning_1"
    assert body["output"] == [
        {
            "id": "rs_1",
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Need more steps"}],
            "reasoning_details": {"tokens": 4},
        }
    ]


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_preserves_sse_error_payload(async_client, monkeypatch):
    email = "responses-error-event@example.com"
    raw_account_id = "acc_responses_error_event"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield (
            'data: {"type":"error","error":{"message":"No active accounts available",'
            '"type":"server_error","code":"no_accounts"}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "hi", "stream": False}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "no_accounts"
    assert body["error"]["type"] == "server_error"
    assert body["error"]["message"] == "No active accounts available"


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_failed_without_status_returns_error(async_client, monkeypatch):
    email = "responses-error-no-status@example.com"
    raw_account_id = "acc_responses_error_no_status"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield (
            'data: {"type":"response.failed","response":{"error":{"message":"No active accounts available",'
            '"type":"server_error","code":"no_accounts"}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "hi", "stream": False}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "no_accounts"
    assert body["error"]["type"] == "server_error"


@pytest.mark.asyncio
async def test_v1_responses_invalid_messages_returns_openai_400(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "system",
                "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}],
            },
            {"role": "user", "content": "hi"},
        ],
    }
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "invalid_request_error"
    assert body["error"]["param"] == "messages"


@pytest.mark.asyncio
async def test_v1_responses_compact_invalid_messages_returns_openai_400(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "developer",
                "content": [{"type": "file", "file": {"file_url": "https://example.com/a.pdf"}}],
            },
            {"role": "user", "content": "hi"},
        ],
    }
    resp = await async_client.post("/v1/responses/compact", json=payload)

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "invalid_request_error"
    assert body["error"]["param"] == "messages"


@pytest.mark.asyncio
async def test_v1_chat_completions_invalid_tool_calls_returns_openai_400(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"arguments": "{}"}}],
            },
            {"role": "user", "content": "continue"},
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_v1_responses_normalizes_assistant_input_text(async_client, monkeypatch):
    email = "assistant-normalize@example.com"
    raw_account_id = "acc_assistant_normalize"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_input: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen_input["input"] = payload.input
        yield 'data: {"type":"response.completed","response":{"id":"resp_assistant_normalize"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "input": [
            {"role": "assistant", "content": [{"type": "input_text", "text": "Prior answer"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
        ],
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert seen_input["input"] == [
        {"role": "assistant", "content": [{"type": "output_text", "text": "Prior answer"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
    ]


@pytest.mark.asyncio
async def test_v1_responses_normalizes_tool_messages(async_client, monkeypatch):
    email = "tool-normalize@example.com"
    raw_account_id = "acc_tool_normalize"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_input: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen_input["input"] = payload.input
        yield 'data: {"type":"response.completed","response":{"id":"resp_tool_normalize"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "messages": [
            {"role": "assistant", "content": "Running tool."},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
            {"role": "user", "content": "continue"},
        ],
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert seen_input["input"] == [
        {"role": "assistant", "content": [{"type": "output_text", "text": "Running tool."}]},
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'},
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]
