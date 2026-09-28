from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from app.core.clients.proxy_websocket import WebsocketsUpstreamWebSocket
from app.db.models import ApiKeyLimit, ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeysService, ApiKeyUpdateData, LimitRuleInput
from tests.integration.test_astra_steering_parent_id import _configure_route, _run_route
from tests.unit.test_astra_steering_protocol import ScriptedSocket, ScriptedUpstream, create, response, saw
from tests.unit.test_proxy_utils import _make_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_reservation", [False, True], ids=["new-reservation", "existing-reservation"])
@pytest.mark.parametrize("cancel_sender", [False, True], ids=["rejection", "cancellation"])
async def test_refreshed_steering_reservation_retains_cleanup_owner_during_reader_races(
    app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch, existing_reservation: bool, cancel_sender: bool
) -> None:
    # Given the real route/transport and a reservation result held after commit.
    committed, allow_return, server_finished, rejection_delivered = (asyncio.Event() for _ in range(4))
    sender: asyncio.Task | None = None
    service, api_key, _ = await _configure_route(app_instance, monkeypatch, ScriptedUpstream([]))
    states, heartbeats = [], []
    original_start = service._start_request_state_api_key_reservation_heartbeat

    def observe_start(state, **kwargs):
        original_start(state, **kwargs)
        states.append(state)
        if state.api_key_reservation_heartbeat_task is not None:
            heartbeats.append(state.api_key_reservation_heartbeat_task)

    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", observe_start)
    async with SessionLocal() as session:
        if not existing_reservation:
            await ApiKeysService(ApiKeysRepository(session)).update_key(
                api_key.id, ApiKeyUpdateData(limits=[], limits_set=True)
            )

    class Socket(ScriptedSocket):
        async def receive(self):
            nonlocal sender
            result = await super().receive()
            sender = asyncio.current_task()
            return result

        async def send_text(self, text: str) -> None:
            event = json.loads(text)
            if event["type"] == "response.steer.accepted":
                async with SessionLocal() as session:
                    limits = (
                        [LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000)]
                        if existing_reservation
                        else []
                    )
                    limits.append(LimitRuleInput(limit_type="input_tokens", limit_window="daily", max_value=1_000_000))
                    await ApiKeysService(ApiKeysRepository(session)).update_key(
                        api_key.id, ApiKeyUpdateData(limits=limits, limits_set=True)
                    )
                    limit = (
                        await session.execute(
                            select(ApiKeyLimit).where(
                                ApiKeyLimit.api_key_id == api_key.id, ApiKeyLimit.limit_type == "input_tokens"
                            )
                        )
                    ).scalar_one()
                    limit.current_value = 0
                    await session.commit()
            elif event["type"] == "response.steer.failed":
                rejection_delivered.set()
            await super().send_text(text)

    socket = Socket(
        [
            (create(), lambda _: True),
            (
                {"type": "response.steer", "previous_response_id": "parent", "input": "First"},
                saw("response.completed", "parent"),
            ),
            (
                {"type": "response.steer", "previous_response_id": "parent", "input": "Second"},
                saw("response.steer.accepted"),
            ),
        ]
    )
    socket.finish_when = lambda event: saw("response.completed", "successor")([event])
    method = "_extend_websocket_api_key_usage" if existing_reservation else "_reserve_websocket_api_key_usage"
    original_adjustment = getattr(service, method)
    calls = 0

    async def hold_committed_result(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = await original_adjustment(*args, **kwargs)
        if existing_reservation or calls == 3:
            committed.set()
            await allow_return.wait()
        return result

    monkeypatch.setattr(service, method, hold_committed_result)

    class Upstream(WebsocketsUpstreamWebSocket):
        async def send_text(self, text: str) -> None:
            if json.loads(text).get("input") == "Second":
                # The queued second steer is owned before its transport handoff;
                # process the first rejection before allowing that handoff.
                await rejection_delivered.wait()
            await super().send_text(text)

    async def upstream_server(connection):
        try:
            await connection.recv()
            for kind in ["response.created", "response.completed"]:
                await connection.send(json.dumps(response(kind, "parent")))
            first = json.loads(await connection.recv())
            assert first["type"] == "response.steer" and first["input"] == "First"
            await connection.send(
                json.dumps(
                    {"type": "response.steer.accepted", "steer": {"id": "first", "previous_response_id": "parent"}}
                )
            )
            await committed.wait()
            # When cancellation or first-steer rejection races attachment.
            if cancel_sender:
                assert sender is not None
                sender.cancel()
                allow_return.set()
            else:
                await connection.send(
                    json.dumps(
                        {
                            "type": "response.steer.failed",
                            "steer": {"id": "first", "previous_response_id": "parent"},
                            "error": {"code": "invalid_input", "message": "Rejected"},
                        }
                    )
                )
                allow_return.set()
                second = json.loads(await connection.recv())
                assert second["input"] == "Second"
                for kind in ["response.created", "response.completed"]:
                    await connection.send(json.dumps(response(kind, "successor", parent="parent")))
            await connection.wait_closed()
        finally:
            server_finished.set()

    async with asyncio.timeout(8), serve(upstream_server, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as connection:
            monkeypatch.setattr(
                service,
                "_connect_proxy_websocket",
                AsyncMock(return_value=(_make_account("acc_steering_parent_id"), Upstream(connection))),
            )
            try:
                if cancel_sender:
                    with pytest.raises(asyncio.CancelledError):
                        await _run_route(app_instance, socket)
                else:
                    await _run_route(app_instance, socket)
            finally:
                allow_return.set()
        await server_finished.wait()
    assert committed.is_set()
    assert await service.drain_persistence_tasks(timeout_seconds=2)
    # Then no committed reservation loses the socket's settlement/cleanup owner.
    async with SessionLocal() as session:
        rows = list(
            await session.scalars(select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == api_key.id))
        )
        assert len(rows) == (2 if existing_reservation else 1)
        assert all(row.status in {"released", "finalized", "failed"} for row in rows)
        limit = await session.scalar(
            select(ApiKeyLimit.current_value).where(
                ApiKeyLimit.api_key_id == api_key.id, ApiKeyLimit.limit_type == "input_tokens"
            )
        )
        assert limit == (0 if cancel_sender else 10)
    assert not service._background_cleanup_tasks
    assert all(task.done() for task in heartbeats)
    assert all(state.response_create_admission is None and not state.response_create_gate_acquired for state in states)
