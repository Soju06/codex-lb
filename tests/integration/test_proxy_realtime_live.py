from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.core.auth.dependencies import validate_required_proxy_api_key_authorization
from app.core.clients.proxy import CodexControlResponse
from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.exceptions import ProxyAuthError

pytestmark = pytest.mark.integration


def _auth_json(account_id: str, email: str) -> dict[str, object]:
    claims = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    body = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return {
        "tokens": {
            "idToken": f"header.{body}.sig",
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        }
    }


@pytest.fixture(autouse=True)
def _allow_proxy_websocket_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization, **_kwargs):
        return None

    async def require_proxy_api_key(authorization):
        if authorization != "Bearer live-key":
            raise ProxyAuthError("Missing API key in Authorization header")
        return SimpleNamespace(id="live-api-key")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(
        proxy_api_module,
        "validate_required_proxy_api_key_authorization",
        require_proxy_api_key,
    )


def test_v1_live_websocket_routes_call_id_and_headers(app_instance, monkeypatch):
    calls = []

    async def fake_proxy_live(self, websocket, call_id, headers, query_params, *, api_key, client_ip=None):
        del self
        assert api_key.id == "live-api-key"
        calls.append(
            {
                "call_id": call_id,
                "alpha": headers.get("openai-alpha"),
                "attestation": headers.get("x-oai-attestation"),
                "query_params": query_params,
                "logged_path": websocket.scope["path"],
                "logged_query": websocket.scope["query_string"],
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
                "Authorization": "Bearer live-key",
            },
        ) as websocket:
            assert websocket.receive_text() == "ready"

    assert calls == [
        {
            "call_id": "rtc_route",
            "alpha": "quicksilver=v2",
            "attestation": "attestation",
            "query_params": [("intent", "quicksilver"), ("architecture", "avas")],
            "logged_path": "/v1/live/<redacted>",
            "logged_query": b"",
            "client_ip": "testclient",
        }
    ]


@pytest.mark.asyncio
async def test_v1_live_full_account_bound_lifecycle_and_cross_key_denial(
    app_instance,
    async_client,
    monkeypatch,
):
    monkeypatch.setattr(
        proxy_api_module,
        "validate_required_proxy_api_key_authorization",
        validate_required_proxy_api_key_authorization,
    )
    connector_calls = []

    async def fake_codex_control_request(*_args, **_kwargs):
        return CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/live/rtc_full_lifecycle"},
        )

    class Upstream:
        uses_proxy = False

        def __init__(self) -> None:
            self.messages = [
                UpstreamWebSocketMessage(kind="text", text="ready"),
                UpstreamWebSocketMessage(kind="close", close_code=1000, close_reason="done"),
            ]

        async def send_text(self, _text: str) -> None:
            return None

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def receive(self) -> UpstreamWebSocketMessage:
            return self.messages.pop(0)

        async def close(self, code: int = 1000, reason: str = "") -> None:
            del code, reason

        def response_header(self, _name: str) -> str | None:
            return None

        def archive_received(self, _message: UpstreamWebSocketMessage) -> None:
            return None

    async def fake_connect_live_websocket(call_id, headers, access_token, account_id, **kwargs):
        connector_calls.append(
            {
                "call_id": call_id,
                "headers": headers,
                "access_token": access_token,
                "account_id": account_id,
                "kwargs": kwargs,
            }
        )
        return Upstream()

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_module, "connect_live_websocket", fake_connect_live_websocket)

    auth_json = _auth_json("acc_live_full", "live-full@example.com")
    imported = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")},
    )
    assert imported.status_code == 200
    key_a_response = await async_client.post("/api/api-keys/", json={"name": "live-full-a"})
    key_b_response = await async_client.post("/api/api-keys/", json={"name": "live-full-b"})
    assert key_a_response.status_code == 200
    assert key_b_response.status_code == 200
    key_a = key_a_response.json()["key"]
    key_b = key_b_response.json()["key"]

    created = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp", "Authorization": f"Bearer {key_a}"},
    )
    assert created.status_code == 201

    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as denied:
            with client.websocket_connect(
                "/v1/live/rtc_full_lifecycle",
                headers={"Authorization": f"Bearer {key_b}"},
            ):
                pass
        assert denied.value.status_code == 404

        with client.websocket_connect(
            "/v1/live/rtc_full_lifecycle?intent=quicksilver",
            headers={"Authorization": f"Bearer {key_a}"},
        ) as websocket:
            assert websocket.receive_text() == "ready"

    assert len(connector_calls) == 1
    assert connector_calls[0]["call_id"] == "rtc_full_lifecycle"
    assert connector_calls[0]["access_token"] == "access-token"
    assert connector_calls[0]["account_id"] == "acc_live_full"
    assert connector_calls[0]["kwargs"]["query_params"] == [("intent", "quicksilver")]


def test_v1_live_websocket_requires_api_key_before_binding_lookup(app_instance, monkeypatch):
    lookup_called = False

    async def fail_lookup(*_args, **_kwargs):
        nonlocal lookup_called
        lookup_called = True
        raise AssertionError("unauthenticated websocket must not resolve call ownership")

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_realtime_call_owner", fail_lookup)

    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as raised:
            with client.websocket_connect("/v1/live/rtc_missing"):
                pass

    assert raised.value.status_code == 401
    assert json.loads(raised.value.content)["error"]["code"] == "invalid_api_key"
    assert lookup_called is False


def test_v1_live_websocket_unknown_api_key_scoped_binding_is_denied(app_instance, monkeypatch):
    async def missing_owner(self, call_id, *, api_key):
        del self, api_key
        assert call_id == "rtc_missing"
        return None

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_realtime_call_owner", missing_owner)

    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as raised:
            with client.websocket_connect(
                "/v1/live/rtc_missing",
                headers={"Authorization": "Bearer live-key"},
            ):
                pass

    assert raised.value.status_code == 404
    assert json.loads(raised.value.content)["error"]["code"] == "realtime_call_not_found"


def test_v1_live_websocket_rejects_malformed_call_id_before_selection(app_instance):
    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as raised:
            with client.websocket_connect(
                "/v1/live/call_not_realtime",
                headers={"Authorization": "Bearer live-key"},
            ):
                pass

    assert raised.value.status_code == 400
    assert json.loads(raised.value.content)["error"]["code"] == "invalid_realtime_call_id"
