from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _allow_proxy_websocket_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization, **_kwargs):
        return None

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)


def test_v1_live_websocket_routes_call_id_and_headers(app_instance, monkeypatch):
    calls = []

    async def fake_proxy_live(self, websocket, call_id, headers, query_params, *, api_key, client_ip=None):
        del self, api_key
        calls.append(
            {
                "call_id": call_id,
                "alpha": headers.get("openai-alpha"),
                "attestation": headers.get("x-oai-attestation"),
                "query_params": query_params,
                "client_ip": client_ip,
            }
        )
        await websocket.accept()
        await websocket.send_text("ready")
        await websocket.close(code=1000)

    monkeypatch.setattr(proxy_module.ProxyService, "proxy_realtime_live_websocket", fake_proxy_live)

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/v1/live/rtc_route?intent=quicksilver&architecture=avas",
            headers={
                "OpenAI-Alpha": "quicksilver=v2",
                "x-oai-attestation": "attestation",
            },
        ) as websocket:
            assert websocket.receive_text() == "ready"

    assert calls == [
        {
            "call_id": "rtc_route",
            "alpha": "quicksilver=v2",
            "attestation": "attestation",
            "query_params": [("intent", "quicksilver"), ("architecture", "avas")],
            "client_ip": "testclient",
        }
    ]


def test_v1_live_websocket_missing_api_key_scoped_binding_is_denied(app_instance, monkeypatch):
    async def missing_owner(self, call_id, *, api_key):
        del self, api_key
        assert call_id == "rtc_missing"
        return None

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_realtime_call_owner", missing_owner)

    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as raised:
            with client.websocket_connect("/v1/live/rtc_missing"):
                pass

    assert raised.value.status_code == 404
    assert json.loads(raised.value.content)["error"]["code"] == "realtime_call_not_found"


def test_v1_live_websocket_rejects_malformed_call_id_before_selection(app_instance):
    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as raised:
            with client.websocket_connect("/v1/live/call_not_realtime"):
                pass

    assert raised.value.status_code == 400
    assert json.loads(raised.value.content)["error"]["code"] == "invalid_realtime_call_id"
