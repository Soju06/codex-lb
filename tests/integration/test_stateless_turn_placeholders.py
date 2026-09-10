from __future__ import annotations

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import app.modules.proxy.service as proxy_module
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [None, "turn_0123456789abcdef0123456789abcdef"])
async def test_stateless_first_request_with_unregistered_placeholder(async_client, monkeypatch, marker):
    _install_bridge_settings(monkeypatch, enabled=True)
    for index in range(2):
        await _import_account(async_client, f"unowned_marker_{index}", f"unowned-{index}@example.com")
    upstream = _FakeBridgeUpstreamWebSocket()

    async def fresh(self, account, *, force=False, timeout_seconds):
        return account

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    headers = {"originator": "codex_exec", "user-agent": "codex_exec/0.150.1"}
    if marker is not None:
        headers["x-codex-turn-state"] = marker
    response = await async_client.post(
        "/backend-api/codex/responses",
        headers=headers,
        json={"model": "gpt-5.1", "instructions": "Return OK.", "input": "Hello.", "stream": True},
    )
    assert response.status_code == 200, response.text
    assert '"response.completed"' in response.text
    assert json.loads(upstream.sent_text[0])["input"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize(
    "shape", ["complete_tools", "opaque", "unresolved", "unknown_item", "unknown_metadata", "previous_response"]
)
async def test_placeholder_recovery_preserves_original_body_and_owner_constraints(
    async_client, monkeypatch, path, shape
):
    _install_bridge_settings(monkeypatch, enabled=True)
    for index in range(2):
        await _import_account(async_client, f"placeholder_fence_{index}", f"placeholder-fence-{index}@example.com")
    upstream = _FakeBridgeUpstreamWebSocket()

    async def fresh(self, account, *, force=False, timeout_seconds):
        return account

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    body = _request_body(shape)
    original_input = deepcopy(body["input"])
    response = await async_client.post(
        path, json=body, headers={"x-codex-turn-state": "turn_0123456789abcdef0123456789abcdef"}
    )
    if shape == "complete_tools":
        assert response.status_code == 200, response.text
        assert '"response.completed"' in response.text
        assert json.loads(upstream.sent_text[0])["input"] == original_input
    else:
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == "previous_response_owner_unavailable"
        assert upstream.sent_text == []


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize(
    "shape", ["complete_tools", "opaque", "unresolved", "unknown_item", "unknown_metadata", "previous_response"]
)
def test_websocket_stateless_placeholder_uses_normal_account_selection(app_instance, monkeypatch, path, shape):
    from tests.integration.test_proxy_websocket_responses import _SequencedUpstreamWebSocket, _websocket_response_batch

    upstream = _SequencedUpstreamWebSocket(
        [], deferred_message_batches=[_websocket_response_batch("resp_placeholder_complete")]
    )

    async def fresh(self, account, *, force=False, timeout_seconds):
        return account

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)

    async def import_accounts():
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as importer:
            for index in range(2):
                await _import_account(importer, f"ws_placeholder_{index}", f"ws-placeholder-{index}@example.com")

    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        client.portal.call(import_accounts)
        with client.websocket_connect(
            f"ws://localhost{path}", headers={"x-codex-turn-state": "turn_0123456789abcdef0123456789abcdef"}
        ) as websocket:
            body = _request_body(shape)
            body.pop("stream")
            websocket.send_json({"type": "response.create", **body})
            first = websocket.receive_json()
            if shape == "complete_tools":
                assert first["type"] == "response.created", first
                assert websocket.receive_json()["type"] == "response.completed"
                assert json.loads(upstream.sent_text[0])["input"] == body["input"]
            else:
                assert first["type"] == "response.failed", first
                assert first["response"]["error"]["code"] == "previous_response_owner_unavailable"
                assert upstream.sent_text == []


def _request_body(shape):
    body = {
        "model": "gpt-5.1",
        "instructions": "Keep the transcript.",
        "stream": True,
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Run the tool."}]},
            {"type": "function_call", "call_id": "call_placeholder", "name": "example", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_placeholder", "output": "OK"},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Continue."}]},
        ],
    }
    if shape == "opaque":
        body["input"].append({"type": "reasoning", "encrypted_content": "opaque-test-state", "summary": []})
    elif shape == "unresolved":
        del body["input"][1]
    elif shape == "unknown_item":
        body["input"][0]["unknown_owner_reference"] = "opaque-test-reference"
    elif shape == "unknown_metadata":
        body["client_metadata"] = {"unknown_owner_reference": "opaque-test-reference"}
    elif shape == "previous_response":
        body["previous_response_id"] = "resp_unknown_owner"
    return body


@pytest.mark.asyncio
async def test_registered_placeholder_owner_survives_account_pool_growth(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    await _import_account(async_client, "registered_first", "registered-first@example.com")
    upstream = _FakeBridgeUpstreamWebSocket()
    connected_accounts = []

    async def fresh(self, account, *, force=False, timeout_seconds):
        return account

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        connected_accounts.append(account_id_header)
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    headers = {"x-codex-turn-state": "turn_0123456789abcdef0123456789abcdef"}
    body = {"model": "gpt-5.1", "instructions": "Return OK.", "input": "Hello.", "stream": True}
    first = await async_client.post("/backend-api/codex/responses", headers=headers, json=body)
    assert first.status_code == 200, first.text
    await _import_account(async_client, "registered_second", "registered-second@example.com")
    second = await async_client.post("/backend-api/codex/responses", headers=headers, json=body)
    assert second.status_code == 200, second.text
    assert '"response.completed"' in second.text
    assert connected_accounts and set(connected_accounts) == {"registered_first"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize(
    "shape", ["complete_tools", "opaque", "unresolved", "unknown_item", "unknown_metadata", "previous_response"]
)
async def test_raw_http_placeholder_uses_the_same_complete_body_proof(async_client, monkeypatch, path, shape):
    from tests.integration.test_http_responses_bridge import (
        _install_proxy_settings,
        _make_app_settings,
        _make_dashboard_settings,
    )

    dashboard = _make_dashboard_settings()
    dashboard.http_downstream_transport_policy = "always_http"
    _install_proxy_settings(monkeypatch, app_settings=_make_app_settings(enabled=False), dashboard_settings=dashboard)
    for index in range(2):
        await _import_account(async_client, f"raw_placeholder_{index}", f"raw-placeholder-{index}@example.com")
    received_inputs = []

    async def fresh(self, account, *, force=False, timeout_seconds):
        return account

    async def stream(payload, headers, access_token, account_id, **kwargs):
        received_inputs.append(payload.input)
        for kind, status in [("response.created", "in_progress"), ("response.completed", "completed")]:
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": kind,
                        "response": {
                            "id": "resp_raw_placeholder",
                            "object": "response",
                            "status": status,
                            "output": [],
                        },
                    }
                )
                + "\n\n"
            )

    async def reject_bridge(*args, **kwargs):
        raise AssertionError("raw HTTP test reached WebSocket transport")

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", reject_bridge)
    body = _request_body(shape)
    response = await async_client.post(
        path, json=body, headers={"x-codex-turn-state": "turn_0123456789abcdef0123456789abcdef"}
    )
    if shape == "complete_tools":
        assert response.status_code == 200, response.text
        assert '"response.completed"' in response.text
        assert received_inputs == [body["input"]]
    else:
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == "previous_response_owner_unavailable"
        assert received_inputs == []
