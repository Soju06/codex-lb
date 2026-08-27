from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx import AsyncClient, Response
from starlette.routing import WebSocketRoute
from starlette.testclient import WebSocketDenialResponse

import app.modules.proxy.api as proxy_api_module
from app.core.auth import dependencies as auth_dependencies
from app.core.clients.proxy import CODEX_LB_REQUIRED_CAPABILITY_HEADER, CodexControlResponse
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService
from app.modules.proxy.service import ProxyService

pytestmark = pytest.mark.integration

_CAPABILITY_HEADERS = {CODEX_LB_REQUIRED_CAPABILITY_HEADER: "trusted_cyber"}
_TRANSPORT_DENIAL = {
    "error": {
        "code": "required_capability_transport_unsupported",
        "message": "Required capability routing is only supported over the Responses WebSocket transport.",
        "type": "invalid_request_error",
    }
}
_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_IMAGE_DATA_URL = f"data:image/png;base64,{base64.b64encode(_IMAGE_BYTES).decode('ascii')}"

_RouteKey = tuple[str, str, str]
_FAIL_CLOSED_HTTP_ROUTES: frozenset[_RouteKey] = frozenset(
    {
        ("HTTP", "POST", "/backend-api/codex/realtime/calls"),
        ("HTTP", "POST", "/backend-api/codex/thread/goal/get"),
        ("HTTP", "GET", "/backend-api/codex/thread/goal/get"),
        ("HTTP", "POST", "/backend-api/codex/thread/goal/set"),
        ("HTTP", "POST", "/backend-api/codex/thread/goal/clear"),
        ("HTTP", "POST", "/backend-api/codex/analytics-events/events"),
        ("HTTP", "POST", "/backend-api/codex/memories/trace_summarize"),
        ("HTTP", "POST", "/backend-api/codex/safety/arc"),
        ("HTTP", "POST", "/backend-api/codex/alpha/search"),
        ("HTTP", "GET", "/backend-api/codex/agent-identities/jwks"),
        ("HTTP", "POST", "/backend-api/codex/responses"),
        ("HTTP", "POST", "/backend-api/codex/responses/"),
        ("HTTP", "GET", "/backend-api/codex/opportunistic/admission"),
        ("HTTP", "POST", "/backend-api/codex/images/generations"),
        ("HTTP", "POST", "/backend-api/codex/images/edits"),
        ("HTTP", "POST", "/backend-api/codex/responses/compact"),
        ("HTTP", "POST", "/internal/bridge/responses"),
        ("HTTP", "POST", "/v1/responses"),
        ("HTTP", "POST", "/v1/responses/"),
        ("HTTP", "POST", "/v1/warmup"),
        ("HTTP", "POST", "/v1/warmup/{mode}"),
        ("HTTP", "POST", "/v1/audio/transcriptions"),
        ("HTTP", "POST", "/v1/images/generations"),
        ("HTTP", "POST", "/v1/images/edits"),
        ("HTTP", "POST", "/v1/chat/completions"),
        ("HTTP", "POST", "/v1/embeddings"),
        ("HTTP", "POST", "/v1/responses/compact"),
        ("HTTP", "POST", "/backend-api/transcribe"),
        ("HTTP", "POST", "/backend-api/files"),
        ("HTTP", "POST", "/backend-api/files/{file_id}/uploaded"),
        ("HTTP", "POST", "/v1/reset-credit"),
        ("HTTP", "POST", "/api/codex/rate-limit-reset-credits/consume/"),
        ("HTTP", "POST", "/api/codex/rate-limit-reset-credits/consume"),
    }
)
_LOCAL_AUTHENTICATED_ROUTES: frozenset[_RouteKey] = frozenset(
    {
        ("HTTP", "GET", "/backend-api/codex/models"),
        ("HTTP", "GET", "/v1/models"),
        ("HTTP", "GET", "/v1/usage"),
        ("HTTP", "POST", "/v1/images/variations"),
        ("HTTP", "GET", "/v1/reset-credit"),
        ("HTTP", "GET", "/api/codex/usage/"),
        ("HTTP", "GET", "/api/codex/usage"),
    }
)
_RESPONSES_WEBSOCKET_ROUTES: frozenset[_RouteKey] = frozenset(
    {
        ("WS", "WEBSOCKET", "/backend-api/codex/responses"),
        ("WS", "WEBSOCKET", "/v1/responses"),
    }
)
_FAIL_CLOSED_WEBSOCKET_ROUTES: frozenset[_RouteKey] = frozenset(
    {
        ("WS", "WEBSOCKET", "/backend-api/codex/{call_id:realtime_live_call_id}"),
        ("WS", "WEBSOCKET", "/v1/live/{call_id:realtime_live_call_id}"),
        ("WS", "WEBSOCKET", "/v1/realtime"),
    }
)
_SEPARATE_NAMESPACE_ROUTES: frozenset[_RouteKey] = frozenset(
    {
        ("HTTP", "GET", "/backend-api/wham/agent-identities/jwks"),
    }
)


def test_registered_proxy_route_inventory_has_one_explicit_capability_policy(app_instance) -> None:
    registered: set[_RouteKey] = set()
    for route in app_instance.routes:
        endpoint = getattr(route, "endpoint", None)
        if getattr(endpoint, "__module__", None) != proxy_api_module.__name__:
            continue
        if isinstance(route, APIRoute):
            registered.update(("HTTP", method, route.path) for method in route.methods or ())
        elif isinstance(route, WebSocketRoute):
            registered.add(("WS", "WEBSOCKET", route.path))

    policy_groups = (
        _FAIL_CLOSED_HTTP_ROUTES,
        _LOCAL_AUTHENTICATED_ROUTES,
        _RESPONSES_WEBSOCKET_ROUTES,
        _FAIL_CLOSED_WEBSOCKET_ROUTES,
        _SEPARATE_NAMESPACE_ROUTES,
    )
    classified = frozenset().union(*policy_groups)

    assert registered == classified
    assert sum(len(group) for group in policy_groups) == len(classified)


async def _create_api_key(name: str) -> str:
    async with SessionLocal() as session:
        created = await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(name=name, allowed_models=None)
        )
    return created.key


async def _request(
    async_client: AsyncClient,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str],
    request_kwargs: Mapping[str, Any],
) -> Response:
    return await async_client.request(method, path, headers=headers, **request_kwargs)


_PROVIDER_ROUTING_CASES = [
    pytest.param("GET", "/backend-api/codex/thread/goal/get", {}, id="thread-goal-get"),
    pytest.param("POST", "/backend-api/codex/thread/goal/get", {"json": {}}, id="thread-goal-get-post"),
    pytest.param("POST", "/backend-api/codex/thread/goal/set", {"json": {}}, id="thread-goal-set"),
    pytest.param("POST", "/backend-api/codex/thread/goal/clear", {"json": {}}, id="thread-goal-clear"),
    pytest.param(
        "POST",
        "/backend-api/codex/analytics-events/events",
        {"json": {}},
        id="analytics-events",
    ),
    pytest.param(
        "POST",
        "/backend-api/codex/memories/trace_summarize",
        {"json": {}},
        id="memory-trace",
    ),
    pytest.param("POST", "/backend-api/codex/realtime/calls", {"content": b"v=offer\r\n"}, id="realtime-call"),
    pytest.param("POST", "/backend-api/codex/safety/arc", {"json": {}}, id="safety-arc"),
    pytest.param("POST", "/backend-api/codex/alpha/search", {"json": {}}, id="alpha-search"),
    pytest.param("GET", "/backend-api/codex/agent-identities/jwks", {}, id="agent-identities"),
    pytest.param("GET", "/backend-api/codex/opportunistic/admission", {}, id="opportunistic-admission"),
    pytest.param("POST", "/v1/warmup", {"json": {"mode": "normal"}}, id="warmup-body"),
    pytest.param("POST", "/v1/warmup/normal", {}, id="warmup-path"),
    pytest.param(
        "POST",
        "/v1/chat/completions",
        {"json": {"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "inert"}]}},
        id="chat-completions",
    ),
    pytest.param(
        "POST",
        "/v1/embeddings",
        {"json": {"model": "text-embedding-3-small", "input": "inert"}},
        id="embeddings",
    ),
]

_PROVIDER_BINARY_ROUTE_CASES = [
    pytest.param(
        "POST",
        "/backend-api/files",
        {"json": {"file_name": "inert.txt", "file_size": 1, "use_case": "codex"}},
        id="files-create",
    ),
    pytest.param(
        "POST",
        "/backend-api/files/file_inert/uploaded",
        {"json": {}},
        id="files-finalize",
    ),
    pytest.param(
        "POST",
        "/backend-api/transcribe",
        {"files": {"file": ("inert.wav", b"inert", "audio/wav")}},
        id="native-transcription",
    ),
    pytest.param(
        "POST",
        "/v1/audio/transcriptions",
        {
            "data": {"model": "gpt-4o-transcribe"},
            "files": {"file": ("inert.wav", b"inert", "audio/wav")},
        },
        id="v1-transcription",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "request_kwargs"), _PROVIDER_ROUTING_CASES)
async def test_daybreak_capability_fails_closed_on_unsupported_routing_http_surfaces(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    request_kwargs: Mapping[str, Any],
) -> None:
    async def fail_before_routing(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("capability-bearing provider request must fail before routing")

    monkeypatch.setattr(ProxyService, "thread_goal_request", fail_before_routing)
    monkeypatch.setattr(ProxyService, "codex_control_request", fail_before_routing)
    monkeypatch.setattr(proxy_api_module, "_opportunistic_admission_denial", fail_before_routing)
    monkeypatch.setattr(proxy_api_module, "_select_chat_model_source", fail_before_routing)
    monkeypatch.setattr(proxy_api_module, "_select_embeddings_model_source", fail_before_routing)
    key = await _create_api_key(f"Daybreak route guard {path}")

    response = await _request(
        async_client,
        method,
        path,
        headers={"Authorization": f"Bearer {key}", **_CAPABILITY_HEADERS},
        request_kwargs=request_kwargs,
    )

    assert response.status_code == 400
    assert response.json() == _TRANSPORT_DENIAL


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_state", ["missing", "invalid"])
async def test_daybreak_capability_unsupported_http_authenticates_before_denial(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    auth_state: str,
) -> None:
    async def fail_before_routing(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("unauthenticated capability intent must fail before routing")

    monkeypatch.setattr(ProxyService, "thread_goal_request", fail_before_routing)
    headers = dict(_CAPABILITY_HEADERS)
    if auth_state == "invalid":
        headers["Authorization"] = "Bearer invalid-daybreak-key"

    response = await async_client.get(
        "/backend-api/codex/thread/goal/get",
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/v1/responses",
        "/backend-api/codex/responses",
        "/v1/chat/completions",
        "/v1/embeddings",
        "/v1/images/generations",
        "/v1/warmup",
        "/v1/warmup/default",
    ],
)
@pytest.mark.parametrize("auth_state", ["valid", "missing", "invalid"])
async def test_daybreak_json_routes_reject_capability_before_body_validation(
    async_client: AsyncClient,
    path: str,
    auth_state: str,
) -> None:
    headers = {
        **_CAPABILITY_HEADERS,
        "Content-Type": "application/json",
    }
    if auth_state == "valid":
        key = await _create_api_key(f"Daybreak pre-body guard {path}")
        headers["Authorization"] = f"Bearer {key}"
    elif auth_state == "invalid":
        headers["Authorization"] = "Bearer invalid-daybreak-key"

    response = await async_client.post(path, headers=headers, content=b"{")

    if auth_state == "valid":
        assert response.status_code == 400
        assert response.json() == _TRANSPORT_DENIAL
        return
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_daybreak_capability_does_not_bypass_api_firewall(async_client: AsyncClient) -> None:
    add_response = await async_client.post("/api/firewall/ips", json={"ipAddress": "10.20.30.40"})
    assert add_response.status_code == 200
    key = await _create_api_key("Daybreak firewall")

    response = await async_client.post(
        "/v1/responses",
        headers={
            "Authorization": f"Bearer {key}",
            **_CAPABILITY_HEADERS,
            "Content-Type": "application/json",
        },
        content=b"{",
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ip_forbidden"


_RESET_CREDIT_CONSUME_CASES = [
    pytest.param(
        "/v1/reset-credit",
        {"account_id": "acct_inert", "redeem_id": "credit_inert"},
        id="self-service-reset-credit",
    ),
    pytest.param(
        "/api/codex/rate-limit-reset-credits/consume",
        {"redeem_request_id": "redeem_inert"},
        id="codex-usage-reset-credit",
    ),
    pytest.param(
        "/api/codex/rate-limit-reset-credits/consume/",
        {"redeem_request_id": "redeem_inert"},
        id="codex-usage-reset-credit-slash",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "payload"), _RESET_CREDIT_CONSUME_CASES)
@pytest.mark.parametrize("auth_state", ["valid", "missing", "invalid"])
async def test_daybreak_capability_reset_credit_consumes_authenticate_then_fail_closed(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: Mapping[str, str],
    auth_state: str,
) -> None:
    async def fail_before_identity_or_account_io(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("capability-bearing reset-credit request must fail before identity, account, or upstream I/O")

    monkeypatch.setattr(auth_dependencies, "fetch_usage", fail_before_identity_or_account_io)
    monkeypatch.setattr(proxy_api_module.AccountsRepository, "get_by_id", fail_before_identity_or_account_io)
    monkeypatch.setattr(proxy_api_module, "_fetch_authoritative_reset_credit", fail_before_identity_or_account_io)
    monkeypatch.setattr(proxy_api_module, "_ensure_v1_reset_credit_account_fresh", fail_before_identity_or_account_io)
    monkeypatch.setattr(
        proxy_api_module,
        "_consume_rate_limit_reset_credit_for_request",
        fail_before_identity_or_account_io,
    )
    monkeypatch.setattr(proxy_api_module, "consume_reset_credit", fail_before_identity_or_account_io)
    headers = dict(_CAPABILITY_HEADERS)
    if auth_state == "valid":
        key = await _create_api_key(f"Daybreak reset-credit guard {path}")
        headers["Authorization"] = f"Bearer {key}"
    elif auth_state == "invalid":
        headers["Authorization"] = "Bearer invalid-daybreak-key"

    response = await async_client.post(path, headers=headers, json=payload)

    if auth_state == "valid":
        assert response.status_code == 400
        assert response.json() == _TRANSPORT_DENIAL
    else:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_headerless_reset_credit_routes_keep_existing_auth_and_account_behavior(
    async_client: AsyncClient,
) -> None:
    key = await _create_api_key("Ordinary reset-credit behavior")
    headers = {"Authorization": f"Bearer {key}"}

    self_service = await async_client.post(
        "/v1/reset-credit",
        headers=headers,
        json={"account_id": "acct_missing", "redeem_id": "credit_missing"},
    )
    codex_usage = await async_client.post(
        "/api/codex/rate-limit-reset-credits/consume",
        headers=headers,
        json={"redeem_request_id": "redeem_inert"},
    )

    assert self_service.status_code == 403
    assert self_service.json()["error"]["code"] != "required_capability_transport_unsupported"
    assert codex_usage.status_code == 401
    assert codex_usage.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_state", ["valid", "missing", "invalid"])
async def test_daybreak_capability_cannot_be_appended_to_signed_internal_bridge(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    auth_state: str,
) -> None:
    from app.core.config.settings import get_settings
    from app.core.openai.requests import ResponsesRequest
    from app.modules.proxy.http_bridge_forwarding import HTTPBridgeForwardContext, build_owner_forward_headers

    async def fail_before_bridge_routing(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("capability-bearing internal bridge request must fail before account routing")

    monkeypatch.setattr(ProxyService, "validate_http_bridge_legacy_forward_anchor", fail_before_bridge_routing)
    monkeypatch.setattr(proxy_api_module, "_stream_responses", fail_before_bridge_routing)
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.6-sol", "instructions": "", "input": "inert", "stream": True}
    )
    context = HTTPBridgeForwardContext(
        origin_instance="origin-inert",
        target_instance=get_settings().http_responses_session_bridge_instance_id,
        codex_session_affinity=True,
        downstream_turn_state="turn_inert",
    )
    authorization: str | None = None
    if auth_state == "valid":
        key = await _create_api_key("Daybreak internal bridge guard")
        authorization = f"Bearer {key}"
    elif auth_state == "invalid":
        authorization = "Bearer invalid-daybreak-key"
    inbound_headers = {} if authorization is None else {"authorization": authorization}
    headers = build_owner_forward_headers(headers=inbound_headers, payload=payload, context=context)
    headers[CODEX_LB_REQUIRED_CAPABILITY_HEADER] = "trusted_cyber"

    response = await async_client.post(
        "/internal/bridge/responses",
        headers=headers,
        json=payload.model_dump_for_forwarding(),
    )

    if auth_state == "valid":
        assert response.status_code == 400
        assert response.json() == _TRANSPORT_DENIAL
    else:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "request_kwargs"), _PROVIDER_BINARY_ROUTE_CASES)
@pytest.mark.parametrize("auth_state", ["valid", "missing", "invalid"])
async def test_daybreak_capability_fails_closed_before_provider_body_or_account_routing(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    request_kwargs: Mapping[str, Any],
    auth_state: str,
) -> None:
    async def fail_before_routing(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("capability-bearing provider request must fail before body parsing or routing")

    monkeypatch.setattr(proxy_api_module, "_parse_transcription_multipart", fail_before_routing)
    monkeypatch.setattr(ProxyService, "create_file", fail_before_routing)
    monkeypatch.setattr(ProxyService, "finalize_file", fail_before_routing)
    monkeypatch.setattr(ProxyService, "transcribe", fail_before_routing)
    headers = dict(_CAPABILITY_HEADERS)
    if auth_state == "valid":
        key = await _create_api_key(f"Daybreak binary route guard {path}")
        headers["Authorization"] = f"Bearer {key}"
    elif auth_state == "invalid":
        headers["Authorization"] = "Bearer invalid-daybreak-key"

    response = await _request(
        async_client,
        method,
        path,
        headers=headers,
        request_kwargs=request_kwargs,
    )

    if auth_state == "valid":
        assert response.status_code == 400
        assert response.json() == _TRANSPORT_DENIAL
    else:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_headerless_provider_http_keeps_existing_routing_behavior(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def ordinary_thread_goal(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"route": "ordinary"}

    monkeypatch.setattr(ProxyService, "thread_goal_request", ordinary_thread_goal)

    response = await async_client.get("/backend-api/codex/thread/goal/get")

    assert response.status_code == 200
    assert response.json() == {"route": "ordinary"}
    assert calls == 1


_IMAGE_CASES = [
    pytest.param(
        "POST",
        "/backend-api/codex/images/generations",
        {"json": {"model": "gpt-image-2", "prompt": "inert"}},
        id="native-generation",
    ),
    pytest.param(
        "POST",
        "/v1/images/generations",
        {"json": {"model": "gpt-image-2", "prompt": "inert"}},
        id="v1-generation",
    ),
    pytest.param(
        "POST",
        "/backend-api/codex/v1/images/generations",
        {"json": {"model": "gpt-image-2", "prompt": "inert"}},
        id="rewritten-native-generation",
    ),
    pytest.param(
        "POST",
        "/backend-api/codex/images/edits",
        {
            "json": {
                "model": "gpt-image-2",
                "prompt": "inert",
                "images": [{"image_url": _IMAGE_DATA_URL}],
            }
        },
        id="native-edit",
    ),
    pytest.param(
        "POST",
        "/v1/images/edits",
        {
            "data": {"model": "gpt-image-2", "prompt": "inert"},
            "files": {"image": ("inert.png", _IMAGE_BYTES, "image/png")},
        },
        id="v1-edit",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "request_kwargs"), _IMAGE_CASES)
@pytest.mark.parametrize("auth_state", ["valid", "missing", "invalid"])
async def test_daybreak_capability_image_routes_authenticate_then_fail_closed(
    async_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    request_kwargs: Mapping[str, Any],
    auth_state: str,
) -> None:
    async def fail_before_routing(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("capability-bearing image request must fail before image routing")

    monkeypatch.setattr(proxy_api_module, "_proxy_images_generation_request", fail_before_routing)
    monkeypatch.setattr(proxy_api_module, "_proxy_images_edit_request", fail_before_routing)
    headers = dict(_CAPABILITY_HEADERS)
    if auth_state == "valid":
        key = await _create_api_key(f"Daybreak image guard {path}")
        headers["Authorization"] = f"Bearer {key}"
    elif auth_state == "invalid":
        headers["Authorization"] = "Bearer invalid-daybreak-key"

    with caplog.at_level("WARNING", logger="app.modules.proxy.api"):
        response = await _request(
            async_client,
            method,
            path,
            headers=headers,
            request_kwargs=request_kwargs,
        )

    if auth_state == "valid":
        assert response.status_code == 400
        assert response.json() == _TRANSPORT_DENIAL
        route = "edits" if path.endswith("/edits") else "generations"
        assert caplog.text.count(f"images_route_complete route={route} ") == 1
        assert "status=400 outcome=invalid_request" in caplog.text
    else:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_headerless_image_route_keeps_existing_validation_behavior(async_client: AsyncClient) -> None:
    response = await async_client.post(
        "/backend-api/codex/images/generations",
        json={"model": "not-an-image-model", "prompt": "inert"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] != "required_capability_transport_unsupported"


_LOCAL_ROUTE_CASES = [
    pytest.param("GET", "/backend-api/codex/models", 200, id="native-models"),
    pytest.param("GET", "/v1/models", 200, id="v1-models"),
    pytest.param("GET", "/v1/usage", 200, id="self-service-usage"),
    pytest.param("POST", "/v1/images/variations", 404, id="unsupported-image-variations"),
    pytest.param("GET", "/v1/reset-credit", 200, id="self-service-reset-credit-list"),
    pytest.param("GET", "/api/codex/usage", 200, id="codex-usage"),
    pytest.param("GET", "/api/codex/usage/", 200, id="codex-usage-slash"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "expected_status"), _LOCAL_ROUTE_CASES)
async def test_daybreak_capability_allows_authenticated_local_routes_without_account_routing(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    expected_status: int,
) -> None:
    async def fail_before_account_or_upstream_io(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("local capability-bearing route must not select an account or call upstream")

    monkeypatch.setattr(auth_dependencies, "fetch_usage", fail_before_account_or_upstream_io)
    monkeypatch.setattr(ProxyService, "_select_account_with_budget", fail_before_account_or_upstream_io)
    monkeypatch.setattr(ProxyService, "get_rate_limit_payload", fail_before_account_or_upstream_io)
    key = await _create_api_key(f"Daybreak local route {path}")

    response = await async_client.request(
        method,
        path,
        headers={"Authorization": f"Bearer {key}", **_CAPABILITY_HEADERS},
    )

    assert response.status_code == expected_status
    if expected_status == 404:
        assert response.json()["error"]["code"] != "required_capability_transport_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "_expected_status"), _LOCAL_ROUTE_CASES)
@pytest.mark.parametrize("auth_state", ["missing", "invalid"])
async def test_daybreak_local_routes_authenticate_capability_carrier(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    _expected_status: int,
    auth_state: str,
) -> None:
    async def fail_before_upstream_identity_io(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("unauthenticated capability carrier must fail before upstream identity I/O")

    monkeypatch.setattr(auth_dependencies, "fetch_usage", fail_before_upstream_identity_io)
    headers = dict(_CAPABILITY_HEADERS)
    if auth_state == "invalid":
        headers["Authorization"] = "Bearer invalid-daybreak-key"

    response = await async_client.request(method, path, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_state", ["missing", "invalid", "valid"])
async def test_capability_header_outside_codex_provider_namespace_keeps_existing_behavior(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    auth_state: str,
) -> None:
    calls = 0

    async def ordinary_wham_control(*_args: Any, **_kwargs: Any) -> CodexControlResponse:
        nonlocal calls
        calls += 1
        return CodexControlResponse(status_code=200, body=b"{}", headers={"content-type": "application/json"})

    monkeypatch.setattr(ProxyService, "codex_control_request", ordinary_wham_control)
    headers = dict(_CAPABILITY_HEADERS)
    if auth_state == "valid":
        key = await _create_api_key("WHAM namespace control")
        headers["Authorization"] = f"Bearer {key}"
    elif auth_state == "invalid":
        headers["Authorization"] = "Bearer invalid-wham-key"

    response = await async_client.get(
        "/backend-api/wham/agent-identities/jwks",
        headers=headers,
    )

    if auth_state == "valid":
        assert response.status_code == 200
        assert response.json() == {}
        assert calls == 1
        return
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert calls == 0


@pytest.mark.parametrize(
    "path",
    [
        "/v1/live/rtc_daybreak_guard",
        "/backend-api/codex/rtc_daybreak_guard",
        "/backend-api/codex/v1/rtc_daybreak_guard",
        "/v1/realtime?call_id=rtc_daybreak_guard",
    ],
    ids=["v1-live", "native-live", "native-v1-alias-live", "v1-realtime"],
)
@pytest.mark.parametrize("auth_state", ["valid", "missing", "invalid"])
def test_daybreak_capability_fails_closed_on_non_responses_websockets(
    app_instance,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    auth_state: str,
) -> None:
    async def fail_before_owner_lookup(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("capability-bearing Live WebSocket must fail before owner lookup")

    monkeypatch.setattr(ProxyService, "proxy_realtime_live_websocket", fail_before_owner_lookup)
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        headers = dict(_CAPABILITY_HEADERS)
        if auth_state == "valid":
            key = client.portal.call(_create_api_key, f"Daybreak live guard {path}")
            headers["Authorization"] = f"Bearer {key}"
        elif auth_state == "invalid":
            headers["Authorization"] = "Bearer invalid-daybreak-key"

        with pytest.raises(WebSocketDenialResponse) as denial:
            with client.websocket_connect(path, headers=headers):
                pytest.fail("unsupported capability-bearing WebSocket must not connect")

    if auth_state == "valid":
        assert denial.value.status_code == 400
        assert denial.value.json() == _TRANSPORT_DENIAL
    else:
        assert denial.value.status_code == 401
        assert denial.value.json()["error"]["code"] == "invalid_api_key"


def test_headerless_live_websocket_keeps_existing_owner_lookup_behavior(app_instance) -> None:
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        key = client.portal.call(_create_api_key, "Ordinary live control")

        with pytest.raises(WebSocketDenialResponse) as denial:
            with client.websocket_connect(
                "/backend-api/codex/rtc_ordinary_missing",
                headers={"Authorization": f"Bearer {key}"},
            ):
                pytest.fail("an unbound ordinary live call must not connect")

    assert denial.value.status_code == 404
    assert denial.value.json()["error"]["code"] != "required_capability_transport_unsupported"


def test_websocket_transport_downgrade_never_applies_to_capability_handshakes(app_instance) -> None:
    # The websocket-outage 426 is a session-scoped switch to HTTP in codex-rs,
    # but capability routing resolves only on this transport: downgrading a
    # capability handshake sends it to an HTTP path that rejects the same
    # capability with 400, and the client never comes back.
    from app.modules.proxy._service import support as transport_health

    connected = False
    transport_health.mark_upstream_websocket_transport_failure()
    try:
        with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
            assert client.portal is not None
            key = client.portal.call(_create_api_key, "Capability handshake during outage")
            # Guard against a vacuous pass: the denial state must still be
            # armed at the moment the handshake runs.
            assert transport_health.upstream_websocket_transport_recently_failed() is True

            with client.websocket_connect(
                "/backend-api/codex/responses",
                headers={"Authorization": f"Bearer {key}", **_CAPABILITY_HEADERS},
            ):
                connected = True
    finally:
        transport_health.clear_upstream_websocket_transport_failure()

    assert connected, "a capability handshake must not be downgraded by the websocket-outage denial"


@pytest.mark.asyncio
async def test_capability_signal_in_client_metadata_is_rejected_over_http(async_client: AsyncClient) -> None:
    # The websocket path resolves a required capability from client_metadata as
    # well as the header and fails closed to security-work-authorized accounts.
    # Nothing on the HTTP path applies that constraint, so a metadata-only
    # signal must be rejected rather than entering ordinary account selection.
    key = await _create_api_key("Metadata-only capability signal")

    response = await async_client.post(
        "/backend-api/codex/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "gpt-5.6-sol",
            "input": "hello",
            "client_metadata": {CODEX_LB_REQUIRED_CAPABILITY_HEADER: "trusted_cyber"},
        },
    )

    assert response.status_code == 400
    assert response.json() == _TRANSPORT_DENIAL


_METADATA_CAPABILITY_ROUTE_CASES = [
    pytest.param(
        "/v1/responses",
        {"model": "gpt-5.6-sol", "input": "hello"},
        id="v1-responses",
    ),
    pytest.param(
        "/backend-api/codex/responses/compact",
        {"model": "gpt-5.6-sol", "instructions": "", "input": "hello"},
        id="native-compact",
    ),
    pytest.param(
        "/v1/responses/compact",
        {"model": "gpt-5.6-sol", "input": "hello"},
        id="v1-compact",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "body"), _METADATA_CAPABILITY_ROUTE_CASES)
async def test_metadata_only_capability_signal_is_rejected_on_every_responses_route(
    async_client: AsyncClient, path: str, body: dict[str, Any]
) -> None:
    # The Codex-native responses route already rejects a metadata-only
    # capability signal. Every other HTTP route that parses a
    # Responses-shaped body must apply the same constraint: the payload
    # models allow extra fields, so the signal survives parsing and would
    # otherwise enter ordinary account selection without the
    # security-work-authorized requirement.
    key = await _create_api_key(f"Metadata-only capability signal {path}")

    response = await async_client.post(
        path,
        headers={"Authorization": f"Bearer {key}"},
        json={**body, "client_metadata": {CODEX_LB_REQUIRED_CAPABILITY_HEADER: "trusted_cyber"}},
    )

    assert response.status_code == 400
    assert response.json() == _TRANSPORT_DENIAL


@pytest.mark.asyncio
async def test_metadata_only_capability_signal_is_rejected_on_signed_internal_bridge(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A legitimate owner forward can never carry the capability key — the
    # websocket path strips it before payload propagation — so a signal
    # arriving here is either a spoofed forward or an origin-side guard
    # gap, and must fail before account routing either way.
    from app.core.config.settings import get_settings
    from app.core.openai.requests import ResponsesRequest
    from app.modules.proxy.http_bridge_forwarding import HTTPBridgeForwardContext, build_owner_forward_headers

    async def fail_before_bridge_routing(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("metadata-signal internal bridge request must fail before account routing")

    monkeypatch.setattr(ProxyService, "validate_http_bridge_legacy_forward_anchor", fail_before_bridge_routing)
    monkeypatch.setattr(proxy_api_module, "_stream_responses", fail_before_bridge_routing)
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": "inert",
            "stream": True,
            "client_metadata": {CODEX_LB_REQUIRED_CAPABILITY_HEADER: "trusted_cyber"},
        }
    )
    context = HTTPBridgeForwardContext(
        origin_instance="origin-inert",
        target_instance=get_settings().http_responses_session_bridge_instance_id,
        codex_session_affinity=True,
        downstream_turn_state="turn_inert",
    )
    key = await _create_api_key("Metadata-only internal bridge guard")
    headers = build_owner_forward_headers(headers={"authorization": f"Bearer {key}"}, payload=payload, context=context)

    response = await async_client.post(
        "/internal/bridge/responses",
        headers=headers,
        json=payload.model_dump_for_forwarding(),
    )

    assert response.status_code == 400
    assert response.json() == _TRANSPORT_DENIAL


@pytest.mark.asyncio
async def test_benign_client_metadata_keeps_ordinary_responses_routing(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the required-capability key inside client_metadata triggers the
    # transport denial; unrelated metadata must keep ordinary routing.
    from fastapi.responses import JSONResponse

    calls = 0

    async def ordinary_compact(*_args: Any, **_kwargs: Any) -> JSONResponse:
        nonlocal calls
        calls += 1
        return JSONResponse({"route": "ordinary"})

    monkeypatch.setattr(proxy_api_module, "_compact_responses", ordinary_compact)
    key = await _create_api_key("Benign client metadata")

    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": "hello",
            "client_metadata": {"editor": "vscode"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"route": "ordinary"}
    assert calls == 1
