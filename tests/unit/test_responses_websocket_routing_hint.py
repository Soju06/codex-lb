from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients import proxy_websocket as client
from app.core.clients.native_egress import NativeWebSocketRequest
from app.modules.proxy import service as proxy_service
from app.modules.proxy.request_policy import apply_api_key_enforcement
from tests.unit.test_proxy_utils import (
    _make_account,
    _repo_factory,
    _RequestLogsRecorder,
)
from tests.unit.test_proxy_websocket_client import _FakeNativeWebSocket
from tests.unit.test_proxy_websocket_model_source_guard import _api_key


@pytest.mark.asyncio
@pytest.mark.parametrize("frontend_key", [False, True])
@pytest.mark.parametrize("account_header", ["subscription-account", None])
@pytest.mark.parametrize("tier", ["priority", "ultrafast", None])
async def test_subscription_handshake_uses_normalized_request_not_inbound_hint(
    monkeypatch: pytest.MonkeyPatch,
    frontend_key: bool,
    account_header: str | None,
    tier: str | None,
) -> None:
    # Given a final normalized request and selected subscription Account.
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("subscription-account")
    account.chatgpt_account_id = account_header
    key = replace(_api_key(), enforced_model="gpt-5-high") if frontend_key else None
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5-high", "instructions": "", "input": "hello", "service_tier": tier}
    )
    apply_api_key_enforcement(payload, key)
    state, _ = service._prepare_response_bridge_request_state(
        payload,
        api_key=key,
        api_key_reservation=None,
        include_type_field=True,
        attach_event_queue=False,
        transport="websocket",
        client_metadata=None,
    )
    # Response-observed tier is deliberately different: it is not wire intent.
    state.service_tier = "flex"
    state.actual_service_tier = "flex"
    native = AsyncMock()
    native.websocket.return_value = _FakeNativeWebSocket()
    monkeypatch.setattr(client, "discover_native_egress_client", lambda: native)
    monkeypatch.setattr(service, "_resolve_upstream_route_for_account", AsyncMock(return_value=None))

    # When the real service opener reaches the real persistent client builder.
    with anyio.fail_after(5):
        upstream = await service._open_upstream_websocket(
            account,
            {"X-Codex-Routing-Hint": "model=spoof;tier=flex"},
            request_state=state,
        )
        await upstream.close()

    # Inspect the emitted handshake at the native transport boundary.
    request = native.websocket.await_args.args[0]
    assert isinstance(request, NativeWebSocketRequest)
    expected = "model=gpt-5" + (f";tier={tier}" if tier is not None else "")
    assert request.headers.get("x-codex-routing-hint") == expected
    assert "X-Codex-Routing-Hint" not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("with_state", [False, True])
async def test_model_less_preconnect_omits_hint(monkeypatch: pytest.MonkeyPatch, with_state: bool) -> None:
    # Given preconnect has no request model (or no request state at all).
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("subscription-preconnect")
    state = proxy_service._WebSocketRequestState(
        request_id="preconnect",
        model=None,
        service_tier="priority",
        requested_service_tier="priority",
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=anyio.current_time(),
    )
    native = AsyncMock()
    native.websocket.return_value = _FakeNativeWebSocket()
    monkeypatch.setattr(client, "discover_native_egress_client", lambda: native)
    monkeypatch.setattr(service, "_resolve_upstream_route_for_account", AsyncMock(return_value=None))

    # When opening through the production service and client.
    with anyio.fail_after(5):
        upstream = await service._open_upstream_websocket(
            account,
            {"x-codex-routing-hint": "model=spoof;tier=priority"},
            request_state=state if with_state else None,
        )
        await upstream.close()

    # Then no guessed model or tier is emitted.
    request = native.websocket.await_args.args[0]
    assert isinstance(request, NativeWebSocketRequest)
    assert "x-codex-routing-hint" not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
async def test_unopted_client_and_live_do_not_forward_hint(monkeypatch: pytest.MonkeyPatch, live: bool) -> None:
    # Given a low-level caller without trusted Responses routing context.
    native = AsyncMock()
    native.websocket.return_value = _FakeNativeWebSocket()
    monkeypatch.setattr(client, "discover_native_egress_client", lambda: native)
    headers = {"X-Codex-Routing-Hint": "model=spoof;tier=priority"}

    # When opening live or a standalone Responses connection without opt-in.
    with anyio.fail_after(5):
        if live:
            upstream = await client.connect_live_websocket(
                "rtc_hint_test",
                headers,
                "test-token",
                "subscription-account",
                protocol=client.RealtimeWebSocketProtocol.LIVE_V3,
                allow_direct_egress=True,
            )
        else:
            upstream = await client.connect_responses_websocket(headers, "test-api-key", None, allow_direct_egress=True)
        await upstream.close()

    # Then inbound hints remain stripped and account-id alone is not opt-in.
    request = native.websocket.await_args.args[0]
    assert isinstance(request, NativeWebSocketRequest)
    assert all(key.lower() != "x-codex-routing-hint" for key in request.headers)
