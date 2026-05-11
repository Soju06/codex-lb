from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket
from fastapi.responses import JSONResponse
from starlette.requests import Request

import app.core.auth.dependencies as auth_dependencies
import app.modules.proxy.api as proxy_api_module
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.exceptions import ProxyAuthError
from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.requests import ResponsesRequest
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_validate_proxy_websocket_request_returns_firewall_denial(monkeypatch):
    denial = JSONResponse(
        status_code=403,
        content=openai_error("ip_forbidden", "Access denied for client IP", error_type="access_error"),
    )

    async def fake_denial(_websocket):
        return denial

    async def fail_auth(_authorization):
        raise AssertionError("authorization validation must not run when firewall already denied the websocket")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", fake_denial)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", fail_auth)

    api_key, response = await proxy_api_module._validate_proxy_websocket_request(
        cast(WebSocket, SimpleNamespace(headers={})),
    )

    assert api_key is None
    assert response is denial


@pytest.mark.asyncio
async def test_validate_proxy_websocket_request_maps_auth_error(monkeypatch):
    async def fake_denial(_websocket):
        return None

    async def fail_auth(_authorization):
        raise ProxyAuthError("Missing API key in Authorization header")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", fake_denial)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", fail_auth)

    api_key, response = await proxy_api_module._validate_proxy_websocket_request(
        cast(WebSocket, SimpleNamespace(headers={"authorization": "Bearer invalid"})),
    )

    assert api_key is None
    assert response is not None
    assert response.status_code == 401
    payload = json.loads(cast(bytes, response.body).decode("utf-8"))
    assert payload["error"]["code"] == "invalid_api_key"
    assert payload["error"]["message"] == "Missing API key in Authorization header"


@pytest.mark.asyncio
async def test_validate_proxy_websocket_request_returns_validated_api_key(monkeypatch):
    async def fake_denial(_websocket):
        return None

    api_key = ApiKeyData(
        id="key_1",
        name="Test Key",
        key_prefix="sk-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 3, 10),
        last_used_at=None,
    )

    async def pass_auth(authorization: str | None):
        assert authorization == "Bearer valid-key"
        return api_key

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", fake_denial)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", pass_auth)

    resolved_api_key, response = await proxy_api_module._validate_proxy_websocket_request(
        cast(WebSocket, SimpleNamespace(headers={"authorization": "Bearer valid-key"})),
    )

    assert response is None
    assert resolved_api_key == api_key


@pytest.mark.asyncio
async def test_validate_proxy_websocket_request_allows_explicit_socket_peer_when_auth_disabled(monkeypatch):
    async def fake_denial(_websocket):
        return None

    async def fake_dashboard_settings() -> SimpleNamespace:
        return SimpleNamespace(api_key_auth_enabled=False)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", fake_denial)
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: SimpleNamespace(get=fake_dashboard_settings))
    monkeypatch.setattr(
        auth_dependencies,
        "get_settings",
        lambda: SimpleNamespace(proxy_unauthenticated_client_cidrs=["192.168.65.1/32"]),
    )
    monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _request: False)

    resolved_api_key, response = await proxy_api_module._validate_proxy_websocket_request(
        cast(
            WebSocket,
            SimpleNamespace(headers={}, client=SimpleNamespace(host="192.168.65.1")),
        )
    )

    assert response is None
    assert resolved_api_key is None


@pytest.mark.asyncio
async def test_validate_proxy_websocket_request_rejects_remote_socket_peer_outside_allowlist(monkeypatch):
    async def fake_denial(_websocket):
        return None

    async def fake_dashboard_settings() -> SimpleNamespace:
        return SimpleNamespace(api_key_auth_enabled=False)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", fake_denial)
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: SimpleNamespace(get=fake_dashboard_settings))
    monkeypatch.setattr(
        auth_dependencies,
        "get_settings",
        lambda: SimpleNamespace(proxy_unauthenticated_client_cidrs=["192.168.65.1/32"]),
    )
    monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _request: False)

    resolved_api_key, response = await proxy_api_module._validate_proxy_websocket_request(
        cast(
            WebSocket,
            SimpleNamespace(headers={}, client=SimpleNamespace(host="192.168.65.2")),
        )
    )

    assert resolved_api_key is None
    assert response is not None
    assert response.status_code == 401
    payload = json.loads(cast(bytes, response.body).decode("utf-8"))
    assert payload["error"]["code"] == "invalid_api_key"
    assert payload["error"]["message"] == "Proxy authentication must be configured before remote access is allowed"


@pytest.mark.asyncio
async def test_validate_internal_bridge_api_key_allows_auth_disabled_remote_request(monkeypatch):
    async def fake_settings():
        return SimpleNamespace(api_key_auth_enabled=False)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/bridge/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )

    async def pass_auth(authorization: str | None, *, request: Request | None = None):
        assert authorization is None
        assert request is not None
        return None

    monkeypatch.setattr(proxy_api_module, "get_settings_cache", lambda: SimpleNamespace(get=fake_settings))
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", pass_auth)

    api_key, response = await proxy_api_module._validate_internal_bridge_api_key(request)

    assert api_key is None
    assert response is None


@pytest.mark.asyncio
async def test_validate_internal_bridge_api_key_preserves_local_request_exemption(monkeypatch):
    async def fake_settings():
        return SimpleNamespace(api_key_auth_enabled=True)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/bridge/responses",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )

    async def pass_auth(authorization: str | None, *, request: Request | None = None):
        assert authorization is None
        assert request is not None
        return None

    monkeypatch.setattr(proxy_api_module, "get_settings_cache", lambda: SimpleNamespace(get=fake_settings))
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", pass_auth)

    api_key, response = await proxy_api_module._validate_internal_bridge_api_key(request)

    assert api_key is None
    assert response is None


@pytest.mark.asyncio
async def test_stream_responses_prefers_forwarded_downstream_turn_state(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/bridge/responses",
            "headers": [(b"x-codex-turn-state", b"http_turn_header_value")],
            "client": ("10.0.0.12", 12345),
        }
    )
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    captured: dict[str, object] = {}

    def fake_apply_api_key_enforcement(_payload, _api_key, **_kwargs):
        return None

    def fake_validate_model_access(_api_key, _model):
        return None

    async def fake_enforce_request_limits(_api_key, *, request_model=None, request_service_tier=None):
        return None

    async def fake_release_reservation(_reservation):
        return None

    async def fake_rate_limit_headers():
        return {}

    async def fake_stream_http_responses(
        _payload,
        _headers,
        *,
        downstream_turn_state=None,
        **kwargs,
    ):
        captured["downstream_turn_state"] = downstream_turn_state
        event_block = (
            'data: {"type":"response.completed","response":{"id":"resp_1","object":"response",'
            '"status":"completed","output":[]}}\n\n'
        )
        yield event_block

    monkeypatch.setattr(proxy_api_module, "apply_api_key_enforcement", fake_apply_api_key_enforcement)
    monkeypatch.setattr(proxy_api_module, "validate_model_access", fake_validate_model_access)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", fake_enforce_request_limits)
    monkeypatch.setattr(proxy_api_module, "_release_reservation", fake_release_reservation)
    monkeypatch.setattr(
        proxy_api_module.proxy_service_module,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_enabled=True),
    )

    context = cast(
        proxy_api_module.ProxyContext,
        SimpleNamespace(
            service=SimpleNamespace(
                rate_limit_headers=fake_rate_limit_headers,
                stream_http_responses=fake_stream_http_responses,
            )
        ),
    )

    response = await proxy_api_module._stream_responses(
        request,
        payload,
        context,
        None,
        prefer_http_bridge=True,
        forwarded_request=True,
        forwarded_headers={"x-codex-turn-state": "http_turn_header_value"},
        forwarded_downstream_turn_state="http_turn_forwarded_value",
    )

    assert captured["downstream_turn_state"] == "http_turn_forwarded_value"
    assert response.headers["x-codex-turn-state"] == "http_turn_forwarded_value"


@pytest.mark.asyncio
async def test_chat_completions_reserves_limits_with_submitted_service_tier(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )
    payload = ChatCompletionsRequest.model_validate(
        {
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "service_tier": "priority",
        }
    )
    api_key = ApiKeyData(
        id="key_omit_priority",
        name="omit-priority-key",
        key_prefix="sk-clb-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 3, 10),
        last_used_at=None,
        omit_priority_request=True,
    )
    captured: dict[str, object] = {}

    async def fake_enforce_request_limits(_api_key, *, request_model=None, request_service_tier=None):
        captured["request_model"] = request_model
        captured["request_service_tier"] = request_service_tier
        return None

    async def fake_rate_limit_headers():
        return {}

    async def fake_stream_responses(stream_payload, _headers, **_kwargs):
        captured["forwarded_service_tier"] = stream_payload.service_tier
        yield 'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed","output":[]}}\n\n'

    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", fake_enforce_request_limits)

    context = cast(
        proxy_api_module.ProxyContext,
        SimpleNamespace(
            service=SimpleNamespace(
                rate_limit_headers=fake_rate_limit_headers,
                stream_responses=fake_stream_responses,
            )
        ),
    )

    response = await proxy_api_module.v1_chat_completions(request, payload, context, api_key)

    assert response.status_code == 200
    assert captured["request_model"] == "gpt-5.4"
    assert captured["request_service_tier"] is None
    assert captured["forwarded_service_tier"] is None


@pytest.mark.asyncio
async def test_stream_responses_does_not_release_forwarded_reservation_on_internal_bridge_error(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/bridge/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    release_reservation = AsyncMock()
    forwarded_reservation = ApiKeyUsageReservationData(
        reservation_id="res_1",
        key_id="key_1",
        model="gpt-5.4",
    )

    def fake_apply_api_key_enforcement(_payload, _api_key, **_kwargs):
        return None

    def fake_validate_model_access(_api_key, _model):
        return None

    async def fake_rate_limit_headers():
        return {}

    async def fake_stream_http_responses(*args, **kwargs):
        del args, kwargs
        raise proxy_api_module.ProxyResponseError(
            503,
            openai_error("bridge_owner_unreachable", "owner unavailable", error_type="server_error"),
        )
        yield ""

    monkeypatch.setattr(proxy_api_module, "apply_api_key_enforcement", fake_apply_api_key_enforcement)
    monkeypatch.setattr(proxy_api_module, "validate_model_access", fake_validate_model_access)
    monkeypatch.setattr(proxy_api_module, "_release_reservation", release_reservation)
    monkeypatch.setattr(
        proxy_api_module.proxy_service_module,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_enabled=True),
    )

    context = cast(
        proxy_api_module.ProxyContext,
        SimpleNamespace(
            service=SimpleNamespace(
                rate_limit_headers=fake_rate_limit_headers,
                stream_http_responses=fake_stream_http_responses,
            )
        ),
    )

    response = await proxy_api_module._stream_responses(
        request,
        payload,
        context,
        None,
        prefer_http_bridge=True,
        skip_limit_enforcement=True,
        api_key_reservation_override=forwarded_reservation,
        forwarded_request=True,
    )

    assert response.status_code == 503
    release_reservation.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_responses_preserves_legacy_forwarded_reserved_service_tier(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/bridge/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": "hi",
            "service_tier": "priority",
        }
    )
    api_key = ApiKeyData(
        id="key_omit_priority",
        name="omit-priority-key",
        key_prefix="sk-clb-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 3, 10),
        last_used_at=None,
        omit_priority_request=True,
    )
    forwarded_reservation = ApiKeyUsageReservationData(
        reservation_id="res_1",
        key_id="key_omit_priority",
        model="gpt-5.4",
    )
    captured: dict[str, object] = {}

    async def fake_rate_limit_headers():
        return {}

    async def fake_stream_http_responses(stream_payload, _headers, **_kwargs):
        captured["service_tier"] = stream_payload.service_tier
        yield 'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed","output":[]}}\n\n'

    monkeypatch.setattr(
        proxy_api_module.proxy_service_module,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_enabled=True),
    )

    context = cast(
        proxy_api_module.ProxyContext,
        SimpleNamespace(
            service=SimpleNamespace(
                rate_limit_headers=fake_rate_limit_headers,
                stream_http_responses=fake_stream_http_responses,
            )
        ),
    )

    response = await proxy_api_module._stream_responses(
        request,
        payload,
        context,
        api_key,
        prefer_http_bridge=True,
        skip_limit_enforcement=True,
        api_key_reservation_override=forwarded_reservation,
        forwarded_request=True,
    )

    assert response.status_code == 200
    assert payload.service_tier == "priority"
    assert captured["service_tier"] == "priority"


def test_public_previous_response_not_found_error_is_masked_to_stream_incomplete():
    envelope = proxy_api_module.OpenAIErrorEnvelopeModel(
        error=proxy_api_module.OpenAIError(
            message="Previous response with id 'resp_missing' not found.",
            type="invalid_request_error",
            code="previous_response_not_found",
            param="previous_response_id",
        )
    )

    status_code, masked = proxy_api_module._mask_previous_response_not_found_error(
        envelope,
        default_status=400,
    )

    assert status_code == 502
    error = masked.model_dump(mode="json")["error"]
    assert error["code"] == "stream_incomplete"
    assert error["type"] == "server_error"
    assert error["message"] == "Upstream websocket closed before response.completed"
    assert "resp_missing" not in masked.model_dump_json()


def test_public_previous_response_invalid_request_param_is_masked_to_stream_incomplete():
    envelope = proxy_api_module.OpenAIErrorEnvelopeModel(
        error=proxy_api_module.OpenAIError(
            message="Previous response with id 'resp_missing' not found.",
            type="invalid_request_error",
            code="invalid_request_error",
            param="previous_response_id",
        )
    )

    status_code, masked = proxy_api_module._mask_previous_response_not_found_error(
        envelope,
        default_status=400,
    )

    assert status_code == 502
    error = masked.model_dump(mode="json")["error"]
    assert error["code"] == "stream_incomplete"


def test_public_previous_response_error_event_is_masked_to_response_failed():
    payload = {
        "type": "error",
        "status": 400,
        "error": {
            "message": "Previous response with id 'resp_missing' not found.",
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "param": "previous_response_id",
        },
    }

    normalized, violation_kind = proxy_api_module._normalize_public_stream_payload(payload)

    assert violation_kind is None
    assert normalized is not None
    assert normalized["type"] == "response.failed"
    response = cast(dict[str, object], normalized["response"])
    error = cast(dict[str, object], response["error"])
    assert error["code"] == "stream_incomplete"
    assert error["type"] == "server_error"
    assert "resp_missing" not in json.dumps(normalized)


@pytest.mark.asyncio
async def test_probe_stream_startup_error_closes_consumed_bridge_error_stream():
    closed = False

    async def stream():
        nonlocal closed
        try:
            yield (
                'data: {"type":"response.failed","response":{"error":{'
                '"message":"Previous response with id \'resp_missing\' not found.",'
                '"type":"invalid_request_error","code":"previous_response_not_found",'
                '"param":"previous_response_id"}}}\n\n'
            )
            yield 'data: {"type":"response.completed","response":{"id":"resp_after"}}\n\n'
        finally:
            closed = True

    _probed, startup_error = await proxy_api_module._probe_stream_startup_error(
        stream(),
        convert_event_errors=True,
    )

    assert startup_error is not None
    assert closed is True


def test_stream_startup_error_response_masks_proxy_previous_response_error():
    request = Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []})
    error = ProxyResponseError(
        400,
        {
            "error": {
                "message": "Previous response with id 'resp_missing' not found.",
                "type": "invalid_request_error",
                "code": "previous_response_not_found",
                "param": "previous_response_id",
            }
        },
    )

    response = proxy_api_module._stream_startup_error_response(request, error, headers={})

    assert response.status_code == 502
    response_body = bytes(response.body)
    body = json.loads(response_body)
    assert body["error"]["code"] == "stream_incomplete"
    assert "resp_missing" not in response_body.decode()


def test_public_stream_incomplete_error_event_is_not_rewritten_when_already_public():
    payload = {
        "type": "error",
        "status": 502,
        "error": {
            "message": "Custom upstream stream detail",
            "type": "server_error",
            "code": "stream_incomplete",
        },
    }

    normalized, violation_kind = proxy_api_module._normalize_public_stream_payload(payload)

    assert violation_kind is None
    assert normalized == payload


def test_public_previous_response_top_level_error_envelope_is_parsed_for_masking():
    payload = {
        "type": "error",
        "status": 400,
        "error": {
            "message": "Previous response with id 'resp_missing' not found.",
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "param": "previous_response_id",
        },
    }

    parsed = proxy_api_module._parse_error_envelope(payload)
    status_code, masked = proxy_api_module._mask_previous_response_not_found_error(parsed, default_status=400)

    assert status_code == 502
    error = masked.model_dump(mode="json")["error"]
    assert error["code"] == "stream_incomplete"
    assert "resp_missing" not in masked.model_dump_json()
