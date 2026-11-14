"""Offline route and adapter regressions for received peer 1009 during send."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from app.core.clients.proxy_websocket import UpstreamWebSocketTransportError, WebsocketsUpstreamWebSocket
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_module
from app.modules.proxy._service import support as proxy_support
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,  # noqa: F401
    _get_account,
    _import_account,
    _install_proxy_settings,
    _make_app_settings,
    _make_dashboard_settings,
    _PromotionUpstreamWebSocket,
)


@pytest_asyncio.fixture
async def inline_bridge_transport(async_client, app_instance, monkeypatch):
    """Real routes + bridge; NO inline-image flag set (production default).

    Only upstream I/O is faked. ``core_stream_responses`` starts as a hard
    guard: the default contract must serve image requests on the bridge,
    never the raw path (tests that deliberately exercise a fallback re-patch
    it with a recorder).
    """
    dashboard = _make_dashboard_settings()
    dashboard.http_downstream_transport_policy = "smart"
    _install_proxy_settings(
        monkeypatch,
        app_settings=_make_app_settings(enabled=True),
        dashboard_settings=dashboard,
    )
    account_id = await _import_account(async_client, "acc_inline_bounded", "inline-bounded@example.com")
    account = await _get_account(account_id)
    upstreams: list[Any] = [_PromotionUpstreamWebSocket(response_id_prefix="resp_inline_bounded")]
    state = SimpleNamespace(connects=0, upstreams=upstreams, account=account)

    async def fake_select_account_with_budget(self, *args, **kwargs):
        del self, args, kwargs
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, **kwargs):
        del self, kwargs
        return target

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        del headers, access_token, account_id_header, base_url, session
        upstream = upstreams[min(state.connects, len(upstreams) - 1)]
        state.connects += 1
        return upstream

    async def fail_legacy_stream(*args, **kwargs):
        raise AssertionError(
            "raw core_stream_responses path must not be used while the default contract serves the request"
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_legacy_stream)
    proxy_support.clear_upstream_websocket_transport_failure()
    yield state
    proxy_support.clear_upstream_websocket_transport_failure()
    await get_proxy_service_for_app(app_instance).drain_persistence_tasks(timeout_seconds=5.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/v1/responses/"])
async def test_peer_1009_send_is_terminal_and_next_image_succeeds(
    async_client, inline_bridge_transport, monkeypatch, path
):
    state = inline_bridge_transport
    penalty = AsyncMock()
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_stream_error", penalty)
    exc = ConnectionClosedError(Close(1009, "too big"), Close(1009, "too big"), True)
    adapter = WebsocketsUpstreamWebSocket(SimpleNamespace(send=AsyncMock(side_effect=exc)))

    class Rejected(_PromotionUpstreamWebSocket):
        async def send_text(self, text):
            self.sent_text.append(text)
            await adapter.send_text(text)

    rejected = Rejected()
    state.upstreams[:] = [rejected, _PromotionUpstreamWebSocket()]
    body = {
        "model": "gpt-5.4",
        "instructions": "Return OK",
        "stream": True,
        "prompt_cache_key": "peer-1009-regression",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "describe"},
                    {
                        "type": "input_image",
                        "image_url": (
                            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
                        ),
                    },
                ],
            }
        ],
    }
    response = await async_client.post(path, json=body, follow_redirects=True)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert len(rejected.sent_text) == 1
    assert state.connects == 1
    penalty.assert_not_awaited()
    response = await async_client.post(path, json=body, follow_redirects=True)
    assert response.status_code == 200
    assert "response.completed" in response.text
    assert state.connects == 2
    penalty.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,payload", [("send_text", "{}"), ("send_bytes", b"{}")])
@pytest.mark.parametrize("peer", [1009, 1000, 1006, None])
async def test_send_classification_requires_received_peer_1009(method, payload, peer):
    exc = ConnectionClosedError(Close(peer, "") if peer else None, Close(1009, ""), True if peer else None)
    connection = SimpleNamespace(send=AsyncMock(side_effect=exc))
    adapter = WebsocketsUpstreamWebSocket(connection)
    with pytest.raises(UpstreamWebSocketTransportError) as error:
        await getattr(adapter, method)(payload)
    assert error.value.error_code == ("payload_too_large" if peer == 1009 else "upstream_unavailable")
    connection.send.assert_awaited_once()
