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
from starlette.types import Message, Receive, Scope, Send

import app.core.auth.dependencies as auth_dependencies
import app.core.request_locality as request_locality
import app.modules.proxy.api as proxy_api_module
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.exceptions import ProxyAuthError
from app.core.middleware.trusted_proxy_headers import TrustedProxyHeadersMiddleware
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData

pytestmark = pytest.mark.unit


async def _validate_proxy_websocket_through_projection(
    *,
    raw_host: str,
    headers: list[tuple[bytes, bytes]],
    capture_raw_peer: bool = True,
) -> tuple[ApiKeyData | None, JSONResponse | None]:
    result: tuple[ApiKeyData | None, JSONResponse | None] | None = None

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal result
        result = await proxy_api_module._validate_proxy_websocket_request(WebSocket(scope, receive, send))

    async def fail_receive() -> Message:
        raise AssertionError("authorization must finish before WebSocket upgrade")

    async def fail_send(_message: Message) -> None:
        raise AssertionError("authorization must finish before WebSocket upgrade")

    scope = cast(
        Scope,
        {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "scheme": "ws",
            "server": ("lb.example", 80),
            "client": (raw_host, 50001),
            "root_path": "",
            "path": "/v1/responses",
            "raw_path": b"/v1/responses",
            "query_string": b"",
            "headers": headers,
            "subprotocols": [],
            "state": {},
            "extensions": {},
        },
    )
    if capture_raw_peer:
        await TrustedProxyHeadersMiddleware(app)(scope, fail_receive, fail_send)
    else:
        await app(scope, fail_receive, fail_send)

    assert result is not None
    return result


@pytest.mark.asyncio
async def test_validate_proxy_websocket_request_returns_firewall_denial(monkeypatch):
    denial = JSONResponse(
        status_code=403,
        content=openai_error("ip_forbidden", "Access denied for client IP", error_type="access_error"),
    )

    async def fake_denial(_websocket):
        return denial

    async def fail_auth(_authorization, *, request: object | None = None):
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

    async def fail_auth(_authorization, *, request: object | None = None):
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

    async def pass_auth(authorization: str | None, *, request: object | None = None):
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
async def test_validate_proxy_websocket_request_supports_legacy_auth_override(monkeypatch):
    async def fake_denial(_websocket):
        return None

    api_key = ApiKeyData(
        id="key_legacy",
        name="Legacy Key",
        key_prefix="sk-legacy",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 3, 10),
        last_used_at=None,
    )
    seen_authorizations: list[str | None] = []

    async def legacy_auth_override(authorization: str | None):
        seen_authorizations.append(authorization)
        return api_key

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", fake_denial)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", legacy_auth_override)

    resolved_api_key, response = await proxy_api_module._validate_proxy_websocket_request(
        cast(WebSocket, SimpleNamespace(headers={"authorization": "Bearer legacy-key"})),
    )

    assert response is None
    assert resolved_api_key == api_key
    assert seen_authorizations == ["Bearer legacy-key"]


@pytest.mark.asyncio
async def test_validate_proxy_websocket_request_reraises_unrelated_type_error(monkeypatch):
    async def fake_denial(_websocket):
        return None

    calls = 0

    async def broken_auth_override(authorization: str | None, *, request: object | None = None):
        nonlocal calls
        del authorization, request
        calls += 1
        raise TypeError("unexpected keyword argument 'request_id'")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", fake_denial)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", broken_auth_override)

    with pytest.raises(TypeError, match="unexpected keyword argument 'request_id'"):
        await proxy_api_module._validate_proxy_websocket_request(
            cast(WebSocket, SimpleNamespace(headers={"authorization": "Bearer broken-key"})),
        )

    assert calls == 1


def _configure_disabled_proxy_auth(
    monkeypatch: pytest.MonkeyPatch,
    *,
    trusted_proxy_cidr: str,
    socket_allowlist_cidr: str,
) -> None:
    async def disabled_auth_settings() -> SimpleNamespace:
        return SimpleNamespace(api_key_auth_enabled=False)

    async def no_firewall_denial(_websocket: WebSocket) -> None:
        return None

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", no_firewall_denial)
    monkeypatch.setattr(
        auth_dependencies,
        "get_settings_cache",
        lambda: SimpleNamespace(get=disabled_auth_settings),
    )
    monkeypatch.setattr(
        auth_dependencies,
        "get_settings",
        lambda: SimpleNamespace(
            proxy_unauthenticated_client_cidrs=[socket_allowlist_cidr] if socket_allowlist_cidr else []
        ),
    )
    monkeypatch.setattr(
        request_locality,
        "get_settings",
        lambda: SimpleNamespace(
            firewall_trust_proxy_headers=True,
            firewall_trusted_proxy_cidrs=[trusted_proxy_cidr],
        ),
    )


@pytest.mark.parametrize(
    (
        "raw_host",
        "forwarded_allow_ips",
        "trusted_proxy_cidr",
        "socket_allowlist_cidr",
        "host",
        "xff",
        "forwarded",
        "capture_raw_peer",
        "denied",
    ),
    [
        ("10.0.0.2", "10.0.0.2", "10.0.0.0/8", "", "localhost", "127.0.0.1", "for=127.0.0.1", True, False),
        ("10.0.0.2", "10.0.0.2", "10.0.0.0/8", "", "localhost", "127.0.0.1", "for=203.0.113.24", True, True),
        ("10.0.0.2", "10.0.0.2", "10.0.0.0/8", "", "localhost", "127.0.0.1", "for=not-an-ip", True, True),
        ("127.0.0.1", "127.0.0.1", "127.0.0.1/32", "192.168.65.1/32", "lb.example", "192.168.65.1", None, True, True),
        ("127.0.0.1", "127.0.0.1", "127.0.0.1/32", "127.0.0.1/32", "lb.example", "203.0.113.24", None, True, False),
        ("192.168.65.1", "", "192.168.0.0/16", "192.168.65.1/32", "lb.example", None, None, False, True),
        ("198.51.100.10", "*", "127.0.0.1/32", "", "localhost", "127.0.0.1", "for=127.0.0.1", True, True),
    ],
    ids=[
        "agreeing-families",
        "conflicting-families",
        "malformed-family",
        "projected-client-not-socket-allowlisted",
        "raw-peer-socket-allowlisted",
        "missing-capture",
        "untrusted-raw-peer",
    ],
)
@pytest.mark.asyncio
async def test_websocket_auth_uses_raw_peer_and_identity_consensus(
    monkeypatch: pytest.MonkeyPatch,
    raw_host: str,
    forwarded_allow_ips: str,
    trusted_proxy_cidr: str,
    socket_allowlist_cidr: str,
    host: str,
    xff: str | None,
    forwarded: str | None,
    capture_raw_peer: bool,
    denied: bool,
) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", forwarded_allow_ips)
    _configure_disabled_proxy_auth(
        monkeypatch,
        trusted_proxy_cidr=trusted_proxy_cidr,
        socket_allowlist_cidr=socket_allowlist_cidr,
    )
    headers = [(b"host", host.encode())]
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    if forwarded is not None:
        headers.append((b"forwarded", forwarded.encode()))

    api_key, response = await _validate_proxy_websocket_through_projection(
        raw_host=raw_host,
        headers=headers,
        capture_raw_peer=capture_raw_peer,
    )

    assert api_key is None
    if not denied:
        assert response is None
        return
    assert response is not None
    assert response.status_code == 401
    assert json.loads(cast(bytes, response.body))["error"]["code"] == "invalid_api_key"


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

    def fake_apply_api_key_enforcement(_payload, _api_key, *, prohibit_fast_mode=False):
        assert prohibit_fast_mode is False
        return None

    def fake_validate_model_access(_api_key, _model):
        return None

    async def fake_enforce_request_limits(
        _api_key, *, request_model=None, request_service_tier=None, request_usage_budget=None
    ):
        del request_model, request_service_tier, request_usage_budget
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
async def test_stream_responses_preserves_forwarded_effective_service_tier(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/bridge/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    # The origin removed an API-key-enforced priority tier because its
    # authoritative catalog says this model never advertises it.
    payload = ResponsesRequest(model="gpt-5.4-mini", instructions="hi", input="hi", service_tier=None)
    api_key = ApiKeyData(
        id="key_priority",
        name="Priority key",
        key_prefix="sk-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier="priority",
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 7, 22),
        last_used_at=None,
    )
    observed: dict[str, object] = {}

    async def fake_rate_limit_headers():
        return {}

    def fake_stream_http_responses(forwarded_payload, _headers, **_kwargs):
        observed["service_tier"] = forwarded_payload.service_tier

        async def body():
            yield (
                'data: {"type":"response.completed","response":'
                '{"id":"resp_1","object":"response","status":"completed","output":[]}}\n\n'
            )

        return body()

    fallback = AsyncMock(side_effect=AssertionError("forwarded effective tier must not be recomputed"))
    monkeypatch.setattr(proxy_api_module, "apply_enforced_service_tier_model_fallback", fallback)
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
        include_rate_limit_headers=False,
        forwarded_request=True,
    )

    assert response.status_code == 200
    assert observed["service_tier"] is None
    assert payload.service_tier is None
    fallback.assert_not_awaited()


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

    def fake_apply_api_key_enforcement(_payload, _api_key, *, prohibit_fast_mode=False):
        assert prohibit_fast_mode is False
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

    normalized, violation_kind = proxy_api_module._normalize_public_stream_payload(cast(dict[str, JsonValue], payload))

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

    normalized, violation_kind = proxy_api_module._normalize_public_stream_payload(cast(dict[str, JsonValue], payload))

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


def test_public_missing_tool_output_input_error_preserves_client_status():
    payload = {
        "type": "error",
        "status": 400,
        "error": {
            "message": "No tool output found for function call call_W3U0TC60cgB5OD7gVCyS0qIq.",
            "type": "invalid_request_error",
            "code": "invalid_request_error",
            "param": "input",
        },
    }

    parsed = proxy_api_module._parse_error_envelope(payload)
    status_code, masked = proxy_api_module._mask_previous_response_not_found_error(parsed, default_status=400)

    assert status_code == 400
    error = masked.model_dump(mode="json")["error"]
    assert error["code"] == "invalid_request_error"
    assert error["param"] == "input"
    assert "call_W3U0TC60cgB5OD7gVCyS0qIq" in masked.model_dump_json()
