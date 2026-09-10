from __future__ import annotations

import contextlib
import json
from typing import cast

import pytest

import app.core.clients.proxy as proxy_client_module
from tests.integration.compact_test_helpers import _make_auth_json

pytestmark = pytest.mark.integration


class _SseContent:
    async def iter_chunked(self, size: int):
        del size
        yield (
            b'data: {"type":"response.output_item.done","output_index":0,'
            b'"item":{"id":"msg_compact_summary_hop","type":"message","role":"assistant",'
            b'"status":"completed","content":[{"type":"output_text","text":"enc_compact_summary_hop"}]}}\n\n'
            b'data: {"type":"response.completed","response":'
            b'{"object":"response","id":"resp_compact_summary_hop","status":"completed","output":[]}}\n\n'
        )


class _SseResponse:
    status = 200
    reason = "OK"
    headers: dict[str, str] = {"content-type": "text/event-stream"}
    content = _SseContent()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _JsonSession:
    def __init__(self, response: object) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._response


@pytest.mark.asyncio
async def test_proxy_compact_route_strips_chunked_and_connection_headers(async_client, monkeypatch):
    """A decoded compact request must not leak its inbound HTTP framing upstream."""
    raw_account_id = "acc_compact_chunked_headers"
    response = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, "compact-chunked-headers@example.com")),
                "application/json",
            )
        },
    )
    assert response.status_code == 200

    session = _JsonSession(_SseResponse())

    @contextlib.asynccontextmanager
    async def lease_session(session_override=None):
        assert session_override is None
        yield session

    monkeypatch.setattr(proxy_client_module, "lease_http_session", lease_session)
    monkeypatch.setattr(proxy_client_module, "discover_native_egress_client", lambda: None)
    native_user_agent = "codex_exec/0.151.0 (Ubuntu 24.4.0; x86_64) dumb"
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json={"model": "gpt-5.1", "instructions": "compact", "input": []},
        headers={
            "User-Agent": native_user_agent,
            "originator": "codex_exec",
            "version": "0.151.0",
            "Authorization": "Bearer inbound-token",
            "chatgpt-account-id": "inbound-account",
            "x-codex-session-id": "continuity-session",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Transfer-Encoding": "chunked",
            "Connection": "keep-alive, x-client-hop, authorization, chatgpt-account-id, accept, content-type",
            "Keep-Alive": "timeout=5",
            "x-client-hop": "drop-me",
        },
    )

    assert response.status_code == 200, response.text
    assert session.calls
    upstream = {key.lower(): value for key, value in cast(dict[str, str], session.calls[0]["headers"]).items()}
    assert "transfer-encoding" not in upstream
    assert "connection" not in upstream
    assert "keep-alive" not in upstream
    assert "x-client-hop" not in upstream
    assert upstream["user-agent"] == native_user_agent
    assert upstream["originator"] == "codex_exec"
    assert upstream["version"] == "0.151.0"
    assert upstream["authorization"] == "Bearer access-token"
    assert upstream["chatgpt-account-id"] == raw_account_id
    assert upstream["x-codex-session-id"] == "continuity-session"
    assert upstream["accept"] == "text/event-stream"
    assert upstream["content-type"] == "application/json"
