from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app.core.types import JsonValue
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService, LimitRuleInput
from app.modules.proxy import api as proxy_api
from tests.unit.test_astra_steering_protocol import ScriptedSocket, ScriptedUpstream, create, response, saw
from tests.unit.test_proxy_utils import _make_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_id", ["r1", " r1 ", "\tr1\n", "\u2003r1\u2003"])
@pytest.mark.parametrize("structured_input", [False, True], ids=["string", "structured"])
async def test_normalized_steering_parent_refunds_echoed_rejection_before_disconnect(
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    parent_id: str,
    structured_input: bool,
) -> None:
    account = _make_account("acc_steering_parent_id")
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)
        keys = ApiKeysService(ApiKeysRepository(session))
        created = await keys.create_key(
            ApiKeyCreateData(
                name="steering-parent-id",
                allowed_models=None,
                expires_at=None,
                limits=[LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000)],
            )
        )
        api_key = await keys.get_key_by_id(created.id)
    assert api_key is not None
    input_items: JsonValue = [{"role": "user", "content": "Correction"}] if structured_input else "Correction"
    reservations = []
    refunded_before_disconnect = False

    class Socket(ScriptedSocket):
        async def send_text(self, text: str) -> None:
            nonlocal refunded_before_disconnect
            if json.loads(text)["type"] == "response.steer.failed":
                async with SessionLocal() as session:
                    row = await ApiKeysRepository(session).get_usage_reservation(reservations[1].reservation_id)
                    refunded_before_disconnect = row is not None and row.status == "released"
            await super().send_text(text)

    socket = Socket(
        [
            (create(), lambda _: True),
            (
                {"type": "response.steer", "previous_response_id": parent_id, "input": input_items},
                saw("response.created", "r1"),
            ),
            (create(input_items="Unrelated"), saw("response.completed", "r1")),
        ]
    )
    socket.finish_when = lambda event: saw("response.completed", "r-after")([event])

    class EchoRejectingUpstream(ScriptedUpstream):
        async def send_text(self, text: str) -> None:
            frame = json.loads(text)
            if frame["type"] == "response.steer":
                self.events[0] = [
                    {
                        "type": "response.steer.failed",
                        "steer": {"previous_response_id": frame["previous_response_id"], "input": frame["input"]},
                        "error": {"code": "invalid_input", "message": "Rejected"},
                    },
                    response("response.completed", "r1"),
                ]
            await super().send_text(text)

    upstream = EchoRejectingUpstream(
        [
            [response("response.created", "r1")],
            [],
            [response("response.created", "r-after"), response("response.completed", "r-after")],
        ]
    )
    service = get_proxy_service_for_app(app_instance)
    monkeypatch.setattr(service, "_connect_proxy_websocket", AsyncMock(return_value=(account, upstream)))
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_revalidate_open_websocket_account", AsyncMock(return_value=(account, None, None)))
    monkeypatch.setattr(proxy_api, "_validate_proxy_websocket_request", AsyncMock(return_value=(api_key, None)))
    monkeypatch.setattr(proxy_api, "_websocket_upstream_transport_denial", AsyncMock(return_value=None))
    original_start = service._start_request_state_api_key_reservation_heartbeat

    def start(state, **kwargs):
        original_start(state, **kwargs)
        if state.api_key_reservation is not None and state.api_key_reservation not in reservations:
            reservations.append(state.api_key_reservation)

    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", start)
    connected = False

    async def receive():
        nonlocal connected
        if not connected:
            connected = True
            return {"type": "websocket.connect"}
        return await socket.receive()

    async def send(message):
        if message["type"] == "websocket.send":
            await socket.send_text(message["text"])
        elif message["type"] == "websocket.close":
            await socket.close()

    await asyncio.wait_for(
        app_instance(
            {
                "type": "websocket",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "scheme": "ws",
                "path": "/backend-api/codex/responses",
                "root_path": "",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
                "subprotocols": [],
            },
            receive,
            send,
        ),
        timeout=5,
    )
    assert await service.drain_persistence_tasks(timeout_seconds=2)
    assert refunded_before_disconnect
    assert upstream.sent[1] == {"type": "response.steer", "previous_response_id": "r1", "input": input_items}
    assert saw("response.completed", "r-after")(socket.sent)
    assert len(reservations) == 3
    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(api_key.id)
        assert limits[0].current_value == 28
