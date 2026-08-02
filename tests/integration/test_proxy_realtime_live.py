from __future__ import annotations

import asyncio
import base64
import json
import logging
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.testclient import WebSocketDenialResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect
from uvicorn.protocols.utils import get_client_addr, get_path_with_query_string

import app.core.clients.proxy_websocket as proxy_websocket_module
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.auth.dependencies import validate_required_proxy_api_key_authorization
from app.core.clients.proxy import (
    CodexControlRequestPrivacyPolicy,
    CodexControlResponse,
    ProxyResponseError,
)
from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.exceptions import ProxyAuthError
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.account_cache import AccountSelectionCache

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


class _UvicornWebSocketLogProbe:
    """Emit Uvicorn-equivalent handshake records from the server-owned ASGI scope."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "websocket":
            await self._app(scope, receive, send)
            return

        handshake_started = False
        websocket_scope = cast(Any, scope)

        async def send_with_access_log(message: Message) -> None:
            nonlocal handshake_started
            if not handshake_started:
                if message["type"] == "websocket.accept":
                    logging.getLogger("uvicorn.error").info(
                        '%s - "WebSocket %s" [accepted]',
                        get_client_addr(websocket_scope),
                        get_path_with_query_string(websocket_scope),
                    )
                    handshake_started = True
                elif message["type"] == "websocket.close":
                    logging.getLogger("uvicorn.error").info(
                        '%s - "WebSocket %s" 403',
                        get_client_addr(websocket_scope),
                        get_path_with_query_string(websocket_scope),
                    )
                    handshake_started = True
                elif message["type"] == "websocket.http.response.start":
                    logging.getLogger("uvicorn.error").info(
                        '%s - "WebSocket %s" %d',
                        get_client_addr(websocket_scope),
                        get_path_with_query_string(websocket_scope),
                        message["status"],
                    )
                    handshake_started = True
            await send(message)

        await self._app(scope, receive, send_with_access_log)


@pytest.mark.parametrize(
    ("path", "logged_path", "expected_call_id", "expected_protocol"),
    [
        (
            "/v1/live/rtc_route?intent=quicksilver&architecture=avas",
            "/v1/live/%3Credacted%3E",
            "rtc_route",
            proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
        ),
        (
            "/backend-api/codex/rtc_route?intent=quicksilver&architecture=avas",
            "/backend-api/codex/%3Credacted%3E",
            "rtc_route",
            proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
        ),
        (
            "/backend-api/codex/123e4567-e89b-12d3-a456-426614174000?intent=quicksilver&architecture=avas",
            "/backend-api/codex/%3Credacted%3E",
            "123e4567-e89b-12d3-a456-426614174000",
            proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
        ),
        (
            "/v1/realtime?call_id=rtc_route&intent=quicksilver&architecture=avas",
            "/v1/realtime",
            "rtc_route",
            proxy_websocket_module.RealtimeWebSocketProtocol.REALTIME_V1_V2,
        ),
    ],
    ids=["frameless", "current-app-rtc", "current-app-uuid", "legacy-query"],
)
def test_realtime_sideband_websocket_aliases_route_to_shared_service(
    app_instance,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
    logged_path: str,
    expected_call_id: str,
    expected_protocol: proxy_websocket_module.RealtimeWebSocketProtocol,
):
    calls = []

    async def fake_proxy_live(
        self,
        websocket,
        call_id,
        headers,
        query_params,
        *,
        protocol,
        api_key,
        client_ip=None,
    ):
        del self
        assert api_key.id == "live-api-key"
        calls.append(
            {
                "call_id": call_id,
                "alpha": headers.get("openai-alpha"),
                "attestation": headers.get("x-oai-attestation"),
                "query_params": query_params,
                "protocol": protocol,
                "client_ip": client_ip,
            }
        )
        await websocket.accept()
        await websocket.send_text("ready")
        await websocket.close(code=1000)

    monkeypatch.setattr(proxy_module.ProxyService, "proxy_realtime_live_websocket", fake_proxy_live)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with TestClient(_UvicornWebSocketLogProbe(app_instance)) as client:
            with client.websocket_connect(
                path,
                headers={
                    "OpenAI-Alpha": "quicksilver=v2",
                    "x-oai-attestation": "attestation",
                    "Authorization": "Bearer live-key",
                },
            ) as websocket:
                assert websocket.receive_text() == "ready"

    assert calls == [
        {
            "call_id": expected_call_id,
            "alpha": "quicksilver=v2",
            "attestation": "attestation",
            "query_params": [("intent", "quicksilver"), ("architecture", "avas")],
            "protocol": expected_protocol,
            "client_ip": "testclient",
        }
    ]
    access_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "uvicorn.error" and ' - "WebSocket ' in record.getMessage()
    ]
    assert len(access_messages) == 1
    assert f'"WebSocket {logged_path}" [accepted]' in access_messages[0]
    assert expected_call_id not in access_messages[0]
    assert "quicksilver" not in access_messages[0]


@pytest.mark.parametrize(
    ("path", "logged_path", "call_id"),
    [
        (
            "/backend-api/codex/rtc_rejected_current?intent=rejected-query-marker",
            "/backend-api/codex/%3Credacted%3E",
            "rtc_rejected_current",
        ),
        (
            "/v1/live/rtc_rejected_v3?intent=rejected-query-marker",
            "/v1/live/%3Credacted%3E",
            "rtc_rejected_v3",
        ),
        (
            "/v1/realtime?call_id=rtc_rejected_legacy&intent=rejected-query-marker",
            "/v1/realtime",
            "rtc_rejected_legacy",
        ),
    ],
    ids=["current-app", "v3", "legacy-query"],
)
def test_realtime_sideband_websocket_rejections_log_redacted_server_scope(
    app_instance,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
    logged_path: str,
    call_id: str,
) -> None:
    service_called = False

    async def fail_if_called(*_args, **_kwargs):
        nonlocal service_called
        service_called = True
        raise AssertionError("authentication rejection must happen before the Live service")

    monkeypatch.setattr(proxy_module.ProxyService, "proxy_realtime_live_websocket", fail_if_called)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with TestClient(_UvicornWebSocketLogProbe(app_instance)) as client:
            with pytest.raises(WebSocketDenialResponse) as raised:
                with client.websocket_connect(path):
                    pass

    assert raised.value.status_code == 401
    assert service_called is False
    access_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "uvicorn.error" and ' - "WebSocket ' in record.getMessage()
    ]
    assert len(access_messages) == 1
    assert f'"WebSocket {logged_path}" 401' in access_messages[0]
    assert call_id not in access_messages[0]
    assert "rejected-query-marker" not in access_messages[0]


def test_duplicated_prefix_live_alias_logs_redacted_rejection(
    app_instance,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service_calls = 0

    async def accept_if_routed(_self, websocket, call_id, *_args, **_kwargs):
        nonlocal service_calls
        assert call_id == "rtc_alias_log_marker"
        service_calls += 1
        await websocket.accept()
        await websocket.close(code=1000)

    def observe_rejection(client: TestClient, path: str) -> tuple[str, int | None, bytes | str | None]:
        try:
            with client.websocket_connect(path, headers={"Authorization": "Bearer live-key"}):
                pass
        except WebSocketDenialResponse as exc:
            return "denial", exc.status_code, exc.content
        except WebSocketDisconnect as exc:
            return "close", exc.code, exc.reason
        return "accepted", None, None

    monkeypatch.setattr(proxy_module.ProxyService, "proxy_realtime_live_websocket", accept_if_routed)
    call_id = "rtc_alias_log_marker"
    query_marker = "alias-query-marker"
    non_live_query_marker = "ordinary-query-marker"
    non_live_path = f"/backend-api/codex/v1/not-a-live-route?trace={non_live_query_marker}"

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with TestClient(_UvicornWebSocketLogProbe(app_instance)) as client:
            expected_rejection = observe_rejection(client, non_live_path)
            alias_result = observe_rejection(
                client,
                f"/backend-api/codex/v1/{call_id}?intent=quicksilver&trace={query_marker}",
            )

    access_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "uvicorn.error" and ' - "WebSocket ' in record.getMessage()
    ]
    assert len(access_messages) == 2
    assert f'"WebSocket {non_live_path}" 403' in access_messages[0]
    assert non_live_query_marker in access_messages[0]
    assert '"WebSocket /backend-api/codex/v1/%3Credacted%3E" [accepted]' in access_messages[1]
    assert call_id not in access_messages[1]
    assert query_marker not in access_messages[1]
    assert "quicksilver" not in access_messages[1]
    assert expected_rejection[0] != "accepted"
    assert alias_result == ("accepted", None, None)
    assert service_calls == 1


@pytest.mark.parametrize(
    "path",
    [
        "/backend-api/codex/rtc_a?call_id=rtc_b",
        "/v1/live/rtc_a?call_id=rtc_b",
    ],
    ids=["current-app", "v3"],
)
def test_path_realtime_sideband_rejects_query_call_id_before_service(
    app_instance,
    monkeypatch,
    path: str,
):
    service_called = False

    async def fail_proxy_live(*_args, **_kwargs):
        nonlocal service_called
        service_called = True
        raise AssertionError("path call_id conflict must fail before the sideband service")

    monkeypatch.setattr(proxy_module.ProxyService, "proxy_realtime_live_websocket", fail_proxy_live)

    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as raised:
            with client.websocket_connect(path, headers={"Authorization": "Bearer live-key"}):
                pass

    assert raised.value.status_code == 400
    assert json.loads(raised.value.content)["error"]["code"] == "invalid_realtime_call_id"
    assert service_called is False


def test_generic_backend_codex_websocket_path_does_not_enter_live_service(
    app_instance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_called = False

    async def reject_if_called(_self, websocket, *_args, **_kwargs):
        nonlocal service_called
        service_called = True
        await websocket.close(code=1008)

    monkeypatch.setattr(proxy_module.ProxyService, "proxy_realtime_live_websocket", reject_if_called)

    with TestClient(app_instance) as client:
        with pytest.raises((WebSocketDenialResponse, WebSocketDisconnect)):
            with client.websocket_connect(
                "/backend-api/codex/ordinary-control",
                headers={"Authorization": "Bearer live-key"},
            ):
                pass

    assert service_called is False


@pytest.mark.parametrize(
    ("path", "expected_call_id"),
    [
        ("/backend-api/codex/rtc_route.ok", "rtc_route.ok"),
        ("/backend-api/codex/123e4567-e89b-12d3-a456-426614174000", "123e4567-e89b-12d3-a456-426614174000"),
        ("/backend-api/codex/123E4567-E89B-12D3-A456-426614174000", "123E4567-E89B-12D3-A456-426614174000"),
        ("/v1/live/rtc_route.ok", "rtc_route.ok"),
        ("/v1/live/123E4567-E89B-12D3-A456-426614174000", "123E4567-E89B-12D3-A456-426614174000"),
    ],
    ids=[
        "current-app-rtc-dot",
        "current-app-uuid",
        "current-app-uppercase-uuid",
        "v3-rtc-dot",
        "v3-uppercase-uuid",
    ],
)
def test_constrained_live_routes_enter_live_service_with_raw_call_id(
    app_instance,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    expected_call_id: str,
) -> None:
    calls: list[str] = []

    async def fake_proxy_live(self, websocket, call_id, headers, query_params, *, protocol, api_key, client_ip=None):
        del self, headers, query_params, protocol, api_key, client_ip
        calls.append(call_id)
        await websocket.accept()
        await websocket.close(code=1000)

    monkeypatch.setattr(proxy_module.ProxyService, "proxy_realtime_live_websocket", fake_proxy_live)

    with TestClient(app_instance) as client:
        with client.websocket_connect(path, headers={"Authorization": "Bearer live-key"}):
            pass

    assert calls == [expected_call_id]


@pytest.mark.parametrize(
    "path",
    [
        "/backend-api/codex/rtc_bad$value",
        "/backend-api/codex/rtc_" + ("a" * 253),
        "/backend-api/codex/123e4567e89b12d3a456426614174000",
        "/backend-api/codex/ordinary-control",
        "/v1/live/rtc_bad$value",
        "/v1/live/" + ("a" * 260),
    ],
    ids=[
        "current-app-invalid-rtc-char",
        "current-app-oversized-rtc",
        "current-app-compact-uuid",
        "current-app-ordinary",
        "v3-invalid-rtc-char",
        "v3-oversized",
    ],
)
def test_constrained_live_routes_reject_malformed_call_ids_before_live_service(
    app_instance,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    service_called = False

    async def reject_if_called(_self, websocket, *_args, **_kwargs):
        nonlocal service_called
        service_called = True
        await websocket.close(code=1008)

    monkeypatch.setattr(proxy_module.ProxyService, "proxy_realtime_live_websocket", reject_if_called)

    with TestClient(app_instance) as client:
        with pytest.raises((WebSocketDenialResponse, WebSocketDisconnect)):
            with client.websocket_connect(path, headers={"Authorization": "Bearer live-key"}):
                pass

    assert service_called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call_id", "location", "sideband_call_id", "sideband_template", "expected_upstream_url"),
    [
        (
            "rtc_full_lifecycle",
            "/v1/realtime/calls/rtc_full_lifecycle",
            "rtc_full_lifecycle",
            "/v1/live/{call_id}?intent=quicksilver",
            "wss://api.openai.com/v1/live/rtc_full_lifecycle?intent=quicksilver",
        ),
        (
            "123e4567-e89b-12d3-a456-426614174000",
            "/v1/realtime/calls/123e4567-e89b-12d3-a456-426614174000",
            "123e4567-e89b-12d3-a456-426614174000",
            "/v1/live/{call_id}?intent=quicksilver",
            "wss://api.openai.com/v1/live/123e4567-e89b-12d3-a456-426614174000?intent=quicksilver",
        ),
        (
            "rtc_current_app",
            "/v1/realtime/calls/rtc_current_app",
            "rtc_current_app",
            "/backend-api/codex/{call_id}",
            "wss://api.openai.com/v1/live/rtc_current_app",
        ),
        (
            "rtc_legacy",
            "/v1/realtime/calls/rtc_legacy",
            "rtc_legacy",
            "/v1/realtime?call_id={call_id}&intent=quicksilver",
            "wss://api.openai.com/v1/realtime?intent=quicksilver&call_id=rtc_legacy",
        ),
        (
            "123E4567-E89B-12D3-A456-426614174000",
            "/v1/realtime/calls/123E4567-E89B-12D3-A456-426614174000",
            "123E4567-E89B-12D3-A456-426614174000",
            "/backend-api/codex/{call_id}",
            "wss://api.openai.com/v1/live/123e4567-e89b-12d3-a456-426614174000",
        ),
        (
            "123E4567-E89B-12D3-A456-426614174000",
            "https://api.openai.com/v1/realtime/calls/123E4567-E89B-12D3-A456-426614174000",
            "123e4567-e89b-12d3-a456-426614174000",
            "/backend-api/codex/{call_id}",
            "wss://api.openai.com/v1/live/123e4567-e89b-12d3-a456-426614174000",
        ),
    ],
    ids=[
        "frameless",
        "frameless-uuid",
        "current-app",
        "legacy-query",
        "current-app-uppercase-uuid",
        "current-app-canonicalized-absolute-uppercase-uuid",
    ],
)
async def test_realtime_call_location_drives_supported_account_bound_sideband_routes(
    app_instance,
    async_client,
    monkeypatch,
    call_id: str,
    location: str,
    sideband_call_id: str,
    sideband_template: str,
    expected_upstream_url: str,
):
    monkeypatch.setattr(
        proxy_api_module,
        "validate_required_proxy_api_key_authorization",
        validate_required_proxy_api_key_authorization,
    )
    connector_calls = []
    control_calls: list[tuple[str, str | None]] = []
    offered_subprotocols = ("live.v0", "live.v1")
    selected_subprotocol = "live.v1"

    async def fake_codex_control_request(*_args, access_token, account_id=None, **_kwargs):
        control_calls.append((access_token, account_id))
        if len(control_calls) == 1:
            raise ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "expired"}},
            )
        return CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": location},
        )

    async def fake_ensure_fresh(
        self,
        account,
        *,
        force=False,
        timeout_seconds=None,
        privacy_policy=CodexControlRequestPrivacyPolicy.STANDARD,
    ):
        assert timeout_seconds is not None
        assert privacy_policy is CodexControlRequestPrivacyPolicy.PRIVATE_REALTIME
        if not force:
            return account
        async with self._repo_factory() as repos:
            refreshed = await repos.accounts.get_by_id_fresh(account.id)
            assert refreshed is not None
            refreshed.access_token_encrypted = self._encryptor.encrypt("rotated-access-token")
            refreshed.refresh_token_encrypted = self._encryptor.encrypt("rotated-refresh-token")
            refreshed.chatgpt_account_id = "acc_live_rotated"
            refreshed.codex_installation_id = "00000000-0000-4000-8000-000000000002"
            await repos.accounts.session.commit()
            refreshed = await repos.accounts.get_by_id_fresh(account.id)
            assert refreshed is not None
            repos.accounts.session.expunge(refreshed)
            return refreshed

    malicious_route = ResolvedUpstreamRoute(
        mode="account_bound_live_secret_mode",
        pool_id="pool_live_secret",
        endpoint=ResolvedProxyEndpoint(
            "ep_live_secret",
            "http",
            "proxy.live-secret.test",
            8080,
            "live-proxy-user",
            "live-proxy-pass",
        ),
    )

    class Upstream:
        uses_proxy = False
        upstream_proxy_route_mode = "upstream_attr_live_mode"
        upstream_proxy_pool_id = "upstream_attr_live_pool"
        upstream_proxy_endpoint_id = "upstream_attr_live_endpoint"
        upstream_proxy_fallback_used = True

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

        def response_header(self, name: str) -> str | None:
            if name.lower() == "sec-websocket-protocol":
                return selected_subprotocol
            return None

        def archive_received(self, _message: UpstreamWebSocketMessage) -> None:
            return None

    async def fake_connect_upstream_websocket(headers, access_token, account_id, *, url, **kwargs):
        connector_calls.append(
            {
                "url": url,
                "headers": headers,
                "access_token": access_token,
                "account_id": account_id,
                "kwargs": kwargs,
            }
        )
        return Upstream()

    async def fake_resolve_route(self, account, *, operation: str):
        del self, account
        if operation != "realtime_live_websocket":
            return None
        return malicious_route

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_websocket_module, "_connect_upstream_websocket", fake_connect_upstream_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_upstream_route_for_account", fake_resolve_route)

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

    selection_cache = AccountSelectionCache(ttl_seconds=5)
    get_proxy_service_for_app(app_instance)._load_balancer._selection_inputs_cache = selection_cache

    created = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp", "Authorization": f"Bearer {key_a}"},
    )
    assert created.status_code == 201
    assert created.headers["location"] == location
    returned_call_id = created.headers["location"].rsplit("/", maxsplit=1)[-1]
    assert returned_call_id == call_id
    sideband_path = sideband_template.format(call_id=sideband_call_id)
    assert control_calls == [
        ("access-token", "acc_live_full"),
        ("rotated-access-token", "acc_live_rotated"),
    ]

    with TestClient(app_instance) as client:
        app_instance.state.proxy_service._load_balancer._selection_inputs_cache = selection_cache
        with pytest.raises(WebSocketDenialResponse) as denied:
            with client.websocket_connect(
                sideband_path,
                headers={"Authorization": f"Bearer {key_b}"},
            ):
                pass
        assert denied.value.status_code == 404

        with client.websocket_connect(
            sideband_path,
            headers={"Authorization": f"Bearer {key_a}"},
            subprotocols=list(offered_subprotocols),
        ) as websocket:
            assert websocket.accepted_subprotocol == selected_subprotocol
            assert websocket.receive_text() == "ready"

            close_message = websocket.receive()
            assert close_message["type"] == "websocket.close"
            assert close_message["code"] == 1000

        assert client.portal is not None
        deadline = asyncio.get_running_loop().time() + 1
        while True:
            client.portal.call(lambda: app_instance.state.proxy_service.drain_persistence_tasks(timeout_seconds=1))
            request_logs = await async_client.get("/api/request-logs?limit=100")
            assert request_logs.status_code == 200, request_logs.text
            matching_logs = [
                entry for entry in request_logs.json()["requests"] if entry["requestKind"] == "realtime_live"
            ]
            if matching_logs:
                live_log = matching_logs[0]
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("realtime live request log was not exposed by the request-logs API")
            await asyncio.sleep(0.01)

        assert live_log["status"] == "ok"
        assert live_log["transport"] == "websocket"
        assert live_log["requestKind"] == "realtime_live"
        assert live_log["apiKeyId"] == key_a_response.json()["id"]
        for private_public_field in (
            "accountId",
            "conversationId",
            "errorCode",
            "errorMessage",
            "failurePhase",
            "failureDetail",
            "failureExceptionType",
            "upstreamStatusCode",
            "upstreamErrorCode",
            "bridgeStage",
            "planType",
            "model",
        ):
            assert live_log.get(private_public_field) in (None, "")
        serialized_live_log = json.dumps(live_log, sort_keys=True)
        for secret in (
            returned_call_id,
            "intent=quicksilver",
            expected_upstream_url,
            key_a,
            "rotated-access-token",
            "v=offer",
            malicious_route.mode,
            malicious_route.pool_id,
            malicious_route.endpoint_id,
            "upstream_attr_live_mode",
            "upstream_attr_live_pool",
            "upstream_attr_live_endpoint",
            "live-proxy-user",
            "live-proxy-pass",
            "proxy.live-secret.test",
        ):
            assert secret not in serialized_live_log

        async with SessionLocal() as session:
            persisted = (
                await session.execute(select(RequestLog).where(RequestLog.request_kind == "realtime_live"))
            ).scalar_one()
        assert persisted.status == "success"
        assert persisted.api_key_id == key_a_response.json()["id"]
        assert persisted.transport == "websocket"
        assert persisted.request_kind == "realtime_live"
        assert persisted.account_id is None
        assert not persisted.model
        assert persisted.conversation_id is None
        for private_route_field in (
            "error_code",
            "error_message",
            "failure_phase",
            "failure_detail",
            "failure_exception_type",
            "upstream_status_code",
            "upstream_error_code",
            "bridge_stage",
            "upstream_proxy_route_mode",
            "upstream_proxy_pool_id",
            "upstream_proxy_endpoint_id",
            "upstream_proxy_fallback_used",
            "upstream_proxy_fail_closed_reason",
        ):
            assert getattr(persisted, private_route_field) is None

    assert len(connector_calls) == 1
    assert connector_calls[0]["url"] == expected_upstream_url
    assert connector_calls[0]["access_token"] == "rotated-access-token"
    assert connector_calls[0]["account_id"] == "acc_live_rotated"
    assert connector_calls[0]["kwargs"]["subprotocols"] == offered_subprotocols
    assert connector_calls[0]["headers"]["x-codex-installation-id"] == "00000000-0000-4000-8000-000000000002"


@pytest.mark.asyncio
async def test_realtime_sideband_unexpected_setup_log_is_content_free(
    app_instance,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_exception_detail = "database setup failed for acc_private_sideband"

    async def fail_setup(*_args, **_kwargs):
        raise RuntimeError(private_exception_detail)

    monkeypatch.setattr(
        proxy_module.ProxyService,
        "proxy_realtime_live_websocket",
        fail_setup,
    )

    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=proxy_api_module.__name__):
        with TestClient(app_instance) as client:
            with pytest.raises(WebSocketDenialResponse) as denied:
                with client.websocket_connect(
                    "/v1/live/rtc_setup_failure",
                    headers={"Authorization": "Bearer live-key"},
                ):
                    pass

    assert denied.value.status_code == 503
    matching_records = [
        record
        for record in caplog.records
        if record.name == proxy_api_module.__name__ and record.getMessage() == "Realtime live websocket setup failed"
    ]
    assert len(matching_records) == 1
    assert matching_records[0].exc_info is None
    assert private_exception_detail not in caplog.text


@pytest.mark.asyncio
async def test_realtime_sideband_failure_log_is_content_free(
    app_instance,
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        proxy_api_module,
        "validate_required_proxy_api_key_authorization",
        validate_required_proxy_api_key_authorization,
    )
    call_id = "rtc_private_failure"
    account_id = "acc_live_private_failure"
    malicious_route = ResolvedUpstreamRoute(
        mode="account_bound_failure_secret_mode",
        pool_id="pool_failure_secret",
        endpoint=ResolvedProxyEndpoint(
            "ep_failure_secret",
            "http",
            "proxy.failure-secret.test",
            8080,
            "failure-proxy-user",
            "failure-proxy-pass",
        ),
    )

    async def fake_codex_control_request(*_args, **_kwargs):
        return CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": f"/v1/realtime/calls/{call_id}"},
        )

    async def fail_connect(*_args, **_kwargs):
        raise ProxyResponseError(
            403,
            {
                "error": {
                    "code": "private-upstream-code",
                    "message": "private-upstream-message with query-secret",
                }
            },
            failure_phase="private_live_connect",
            failure_detail="malicious-live-failure-detail",
            failure_exception_type="MaliciousLiveFailure",
            upstream_status_code=403,
            upstream_error_code="private-upstream-code",
        )

    async def fake_resolve_route(self, account, *, operation: str):
        del self, account
        if operation != "realtime_live_websocket":
            return None
        return malicious_route

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_websocket_module, "_connect_upstream_websocket", fail_connect)
    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_upstream_route_for_account", fake_resolve_route)

    imported = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_auth_json(account_id, "live-private-failure@example.com")),
                "application/json",
            )
        },
    )
    assert imported.status_code == 200
    key_response = await async_client.post("/api/api-keys/", json={"name": "live-private-failure"})
    assert key_response.status_code == 200
    api_key = key_response.json()["key"]
    api_key_id = key_response.json()["id"]

    created = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={
            "content-type": "application/sdp",
            "Authorization": f"Bearer {api_key}",
            "x-request-id": "req-live-private-failure",
        },
    )
    assert created.status_code == 201

    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as denied:
            with client.websocket_connect(
                f"/v1/live/{call_id}?intent=quicksilver&token=query-secret",
                headers={"Authorization": f"Bearer {api_key}"},
            ):
                pass
        assert denied.value.status_code >= 400
        assert client.portal is not None
        assert client.portal.call(
            lambda: get_proxy_service_for_app(app_instance).drain_persistence_tasks(timeout_seconds=1)
        )

        request_logs = await async_client.get("/api/request-logs?limit=100")
        assert request_logs.status_code == 200, request_logs.text
        matching_logs = [entry for entry in request_logs.json()["requests"] if entry["requestKind"] == "realtime_live"]
        assert len(matching_logs) == 1
        public_log = matching_logs[0]
        assert public_log["status"] == "error"
        assert public_log["transport"] == "websocket"
        assert public_log["requestKind"] == "realtime_live"
        assert public_log["apiKeyId"] == api_key_id
        for private_public_field in (
            "accountId",
            "conversationId",
            "errorCode",
            "errorMessage",
            "failurePhase",
            "failureDetail",
            "failureExceptionType",
            "upstreamStatusCode",
            "upstreamErrorCode",
            "bridgeStage",
            "planType",
            "model",
        ):
            assert public_log.get(private_public_field) in (None, "")
        serialized_public_log = json.dumps(public_log, sort_keys=True)
        for secret in (
            account_id,
            call_id,
            "private-upstream-code",
            "private-upstream-message with query-secret",
            "private_live_connect",
            "malicious-live-failure-detail",
            "MaliciousLiveFailure",
            malicious_route.mode,
            malicious_route.pool_id,
            malicious_route.endpoint_id,
            "failure-proxy-user",
            "failure-proxy-pass",
            "proxy.failure-secret.test",
            "intent=quicksilver",
            "query-secret",
        ):
            assert secret not in serialized_public_log

    async with SessionLocal() as session:
        persisted = (
            await session.execute(select(RequestLog).where(RequestLog.request_kind == "realtime_live"))
        ).scalar_one()
    assert persisted.status == "error"
    assert persisted.request_kind == "realtime_live"
    assert persisted.transport == "websocket"
    assert persisted.api_key_id == api_key_id
    assert persisted.account_id is None
    assert not persisted.model
    assert persisted.conversation_id is None
    for private_failure_field in (
        "error_code",
        "error_message",
        "failure_phase",
        "failure_detail",
        "failure_exception_type",
        "upstream_status_code",
        "upstream_error_code",
        "bridge_stage",
        "upstream_proxy_route_mode",
        "upstream_proxy_pool_id",
        "upstream_proxy_endpoint_id",
        "upstream_proxy_fallback_used",
        "upstream_proxy_fail_closed_reason",
    ):
        assert getattr(persisted, private_failure_field) is None


@pytest.mark.asyncio
async def test_realtime_sideband_rejects_reassigned_key_scope_before_owner_use(
    app_instance,
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        proxy_api_module,
        "validate_required_proxy_api_key_authorization",
        validate_required_proxy_api_key_authorization,
    )
    owner_a = generate_unique_account_id("acc_live_scope_a", "live-scope-a@example.com")
    owner_b = generate_unique_account_id("acc_live_scope_b", "live-scope-b@example.com")
    call_id = "rtc_scope_reassign"
    selection_calls: list[str | None] = []
    decrypt_calls: list[str] = []
    route_calls: list[str] = []
    connector_calls: list[str] = []

    async def fake_codex_control_request(*_args, **_kwargs):
        return CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": f"/v1/realtime/calls/{call_id}"},
        )

    async def tracking_select(self, deadline, **kwargs):
        del self, deadline
        selection_calls.append(kwargs.get("preferred_account_id"))
        raise AssertionError("reassigned key must not reach account selection")

    async def tracking_resolve_route(self, account, *, operation: str):
        del self
        if operation != "realtime_live_websocket":
            return None
        route_calls.append(f"{account.id}:{operation}")
        raise AssertionError("reassigned key must not resolve upstream route")

    async def tracking_connect(*_args, **_kwargs):
        connector_calls.append("called")
        raise AssertionError("reassigned key must not open upstream connector")

    original_decrypt = None

    def tracking_decrypt(value: bytes) -> str:
        assert original_decrypt is not None
        decrypt_calls.append(value.hex())
        return original_decrypt(value)

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    for account_id, email in (
        ("acc_live_scope_a", "live-scope-a@example.com"),
        ("acc_live_scope_b", "live-scope-b@example.com"),
    ):
        imported = await async_client.post(
            "/api/accounts/import",
            files={"auth_json": ("auth.json", json.dumps(_auth_json(account_id, email)), "application/json")},
        )
        assert imported.status_code == 200

    key_response = await async_client.post(
        "/api/api-keys/",
        json={"name": "live-scope-reassign", "assignedAccountIds": [owner_a]},
    )
    assert key_response.status_code == 200
    key_payload = key_response.json()
    api_key = key_payload["key"]
    key_id = key_payload["id"]

    created = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp", "Authorization": f"Bearer {api_key}"},
    )
    assert created.status_code == 201

    updated = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"assignedAccountIds": [owner_b]},
    )
    assert updated.status_code == 200
    assert updated.json()["accountAssignmentScopeEnabled"] is True
    assert updated.json()["assignedAccountIds"] == [owner_b]

    service = get_proxy_service_for_app(app_instance)
    original_decrypt = service._encryptor.decrypt
    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget_compatible", tracking_select)
    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_upstream_route_for_account", tracking_resolve_route)
    monkeypatch.setattr(proxy_websocket_module, "_connect_upstream_websocket", tracking_connect)
    monkeypatch.setattr(service._encryptor, "decrypt", tracking_decrypt)
    selection_calls.clear()
    decrypt_calls.clear()
    route_calls.clear()
    connector_calls.clear()

    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as denied:
            with client.websocket_connect(
                f"/v1/live/{call_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            ):
                pass

    assert denied.value.status_code == 404
    assert json.loads(denied.value.content)["error"]["code"] == "realtime_call_not_found"
    assert json.loads(denied.value.content)["error"]["message"] == "Realtime call binding not found or expired"
    assert selection_calls == []
    assert decrypt_calls == []
    assert route_calls == []
    assert connector_calls == []


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


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_code"),
    [
        ("/v1/realtime", 400, "invalid_realtime_call_id"),
        ("/v1/realtime?call_id=rtc_first&call_id=rtc_second", 400, "invalid_realtime_call_id"),
    ],
    ids=["legacy-missing", "legacy-duplicate"],
)
def test_realtime_sideband_websocket_rejects_malformed_call_id_before_selection(
    app_instance,
    path: str,
    expected_status: int,
    expected_code: str,
) -> None:
    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as raised:
            with client.websocket_connect(
                path,
                headers={"Authorization": "Bearer live-key"},
            ):
                pass

    assert raised.value.status_code == expected_status
    assert json.loads(raised.value.content)["error"]["code"] == expected_code


def test_v1_live_unconstrained_path_does_not_enter_live_service(
    app_instance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_called = False

    async def reject_if_called(_self, websocket, *_args, **_kwargs):
        nonlocal service_called
        service_called = True
        await websocket.close(code=1008)

    monkeypatch.setattr(proxy_module.ProxyService, "proxy_realtime_live_websocket", reject_if_called)

    with TestClient(app_instance) as client:
        with pytest.raises((WebSocketDenialResponse, WebSocketDisconnect)):
            with client.websocket_connect(
                "/v1/live/call_not_realtime",
                headers={"Authorization": "Bearer live-key"},
            ):
                pass

    assert service_called is False
