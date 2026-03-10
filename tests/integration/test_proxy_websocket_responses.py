from __future__ import annotations

import asyncio
import base64
import json
from typing import cast

import pytest
from fastapi.testclient import TestClient

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module

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


class _FakeUpstreamMessage:
    def __init__(
        self,
        kind: str,
        *,
        text: str | None = None,
        data: bytes | None = None,
        close_code: int | None = None,
        error: str | None = None,
    ) -> None:
        self.kind = kind
        self.text = text
        self.data = data
        self.close_code = close_code
        self.error = error


class _FakeUpstreamWebSocket:
    def __init__(self, messages: list[_FakeUpstreamMessage]) -> None:
        self.sent_text: list[str] = []
        self.sent_bytes: list[bytes] = []
        self.closed = False
        self._messages: asyncio.Queue[_FakeUpstreamMessage] = asyncio.Queue()
        for message in messages:
            self._messages.put_nowait(message)

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def receive(self) -> _FakeUpstreamMessage:
        return await self._messages.get()

    async def close(self) -> None:
        self.closed = True


def test_backend_responses_websocket_proxies_upstream_and_persists_log(app_instance, monkeypatch):
    email = "ws-proxy@example.com"
    raw_account_id = "acc_ws_proxy"
    auth_json = _make_auth_json(raw_account_id, email)
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.created",
                    "response": {"id": "resp_ws_1", "object": "response", "status": "in_progress"},
                },
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_ws_1",
                        "object": "response",
                        "status": "completed",
                        "service_tier": "fast",
                        "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
                    },
                },
                separators=(",", ":"),
            ),
        ),
    ]
    fake_upstream = _FakeUpstreamWebSocket(upstream_messages)
    seen: dict[str, object] = {}

    async def fake_connect(headers, access_token, account_id, base_url=None, session=None):
        seen["headers"] = dict(headers)
        seen["access_token"] = access_token
        seen["account_id"] = account_id
        return fake_upstream

    async def allow_firewall(_websocket):
        return None

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect, raising=False)
    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "service_tier": "fast",
        "reasoning": {"effort": "high"},
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        response = client.post(
            "/api/accounts/import",
            files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")},
        )
        assert response.status_code == 200

        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer external-token",
                "chatgpt-account-id": "external-account",
                "session_id": "thread-ws-1",
                "openai-beta": "responses_websockets=2026-02-06",
            },
        ) as websocket:
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())
        logs_response = client.get("/api/request-logs")
        assert logs_response.status_code == 200
        logs_payload = logs_response.json()

    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    assert seen["access_token"] == "access-token"
    assert seen["account_id"] == raw_account_id
    seen_headers = cast(dict[str, str], seen["headers"])
    assert seen_headers["session_id"] == "thread-ws-1"
    assert seen_headers["openai-beta"] == "responses_websockets=2026-02-06"
    assert "authorization" not in {key.lower() for key in seen_headers}
    assert fake_upstream.sent_text == [json.dumps(request_payload, separators=(",", ":"))]
    assert logs_payload["requests"]
    log = logs_payload["requests"][0]
    assert log["requestId"] == "resp_ws_1"
    assert log["model"] == "gpt-5.4"
    assert log["serviceTier"] == "fast"
    assert log["transport"] == "websocket"
    assert log["status"] == "ok"
    assert log["tokens"] == 8


def test_backend_responses_websocket_emits_no_accounts_error(app_instance, monkeypatch):
    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    async def allow_firewall(_websocket):
        return None

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            event = json.loads(websocket.receive_text())

    assert event["type"] == "error"
    assert event["status"] == 503
    assert event["error"]["code"] == "no_accounts"
