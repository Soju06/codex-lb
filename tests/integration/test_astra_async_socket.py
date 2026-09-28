from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import ServerConnection, serve

from app.core.clients.proxy_websocket import WebsocketsUpstreamWebSocket
from app.core.types import JsonValue
from app.db.models import Account
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_module
from tests.integration.test_http_responses_bridge import _import_account, _install_bridge_settings
from tests.unit.test_astra_async_tools import response

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses", "/backend-api/codex/responses/"]
)
async def test_async_continuity_over_real_upstream_socket(
    async_client: AsyncClient, app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    # Given a real WebSocket peer emitting two async calls and one sync call.
    _install_bridge_settings(monkeypatch, enabled=True)
    await _import_account(async_client, "acc_async_wire", "async-wire@example.com")
    calls = [
        {"type": "function_call", "call_id": "async_a", "name": "slow", "arguments": "{}", "async": True},
        {"type": "custom_tool_call", "call_id": "async_b", "name": "later", "input": "x", "async": True},
        {"type": "function_call", "call_id": "sync_c", "name": "now", "arguments": "{}"},
    ]
    sent: list[dict[str, JsonValue]] = []
    connections: list[ServerConnection] = []

    async def peer(connection: ServerConnection) -> None:
        connections.append(connection)
        async for frame in connection:
            sent.append(json.loads(frame))
            response_id = f"wire_{len(sent)}"
            parent = f"wire_{len(sent) - 1}" if len(sent) > 1 else None
            await connection.send(json.dumps(response("response.created", response_id, parent=parent)))
            output = calls if len(sent) == 1 else []
            for call in output:
                for event_type in ("response.output_item.added", "response.output_item.done"):
                    await connection.send(json.dumps({"type": event_type, "response_id": response_id, "item": call}))
            await connection.send(json.dumps(response("response.completed", response_id, parent=parent, output=output)))

    async def fresh(self: proxy_module.ProxyService, account: Account, **kwargs: JsonValue) -> Account:
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    service = get_proxy_service_for_app(app_instance)
    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def connect(*args: JsonValue, **kwargs: JsonValue) -> WebsocketsUpstreamWebSocket:
            connection = await websocket_connect(f"ws://127.0.0.1:{port}", proxy=None)
            return WebsocketsUpstreamWebSocket(connection)

        monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
        outputs = [
            {"type": "function_call_output", "call_id": "async_a", "output": "first result"},
            {"type": "custom_tool_call_output", "call_id": "async_b", "output": "second result"},
        ]
        base = {"model": "gpt-6-astra", "instructions": "", "prompt_cache_key": "async-wire"}
        try:
            # When an intervening turn precedes independently arriving real results.
            async with asyncio.timeout(10):
                for index, items in enumerate(("Begin", "Continue", [outputs[0]], [outputs[1]]), start=1):
                    payload = {**base, "input": items}
                    if index > 1:
                        payload["previous_response_id"] = f"wire_{index - 1}"
                    result = await async_client.post(path, json=payload)
                    assert result.status_code == 200, result.text
                    if path.startswith("/v1/"):
                        assert result.json()["id"] == f"wire_{index}"
                    else:
                        events = [
                            json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: {")
                        ]
                        assert events[-1]["response"]["id"] == f"wire_{index}"

            # Then only sync work is interrupted; both delayed outputs cross the wire intact.
            assert len(connections) == 1
            assert len(sent) == 4
            second_input = sent[1]["input"]
            assert isinstance(second_input, list)
            synthetic = [item for item in second_input if isinstance(item, dict) and "call_id" in item]
            assert [item["call_id"] for item in synthetic] == ["sync_c"]
            assert sent[2]["input"] == [outputs[0]]
            assert sent[3]["input"] == [outputs[1]]
            assert await service.drain_persistence_tasks(timeout_seconds=5)
        finally:
            assert await service.close_all_http_bridge_sessions()
    assert not server.sockets
