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
@pytest.mark.parametrize("initial_limit", ["none", "existing", "other-model"])
@pytest.mark.parametrize("exhausted", [True, False], ids=["reject", "admit"])
async def test_queued_steering_reconciles_refreshed_limits_on_the_wire(
    app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch, initial_limit: str, exhausted: bool
) -> None:
    # Given an owned parent and a steer admitted before a quota policy update.
    finish_successor = asyncio.Event()
    wire_frames: list[dict] = []
    before_terminal: list[tuple[int, int]] = []
    service, api_key, _ = await _configure_route(app_instance, monkeypatch, ScriptedUpstream([]))
    existing = LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000)
    async with SessionLocal() as session:
        limits = [existing] if initial_limit == "existing" else []
        if initial_limit == "other-model":
            limits = [
                LimitRuleInput(
                    limit_type="input_tokens", limit_window="daily", max_value=1_000_000, model_filter="other"
                )
            ]
        await ApiKeysService(ApiKeysRepository(session)).update_key(
            api_key.id, ApiKeyUpdateData(limits=limits, limits_set=True)
        )

    class Socket(ScriptedSocket):
        async def send_text(self, text: str) -> None:
            event = json.loads(text)
            if event["type"] == "response.steer.accepted":
                # When the administrator changes policy before the next frame.
                async with SessionLocal() as session:
                    refreshed = [existing] if initial_limit == "existing" else []
                    refreshed.append(
                        LimitRuleInput(limit_type="input_tokens", limit_window="daily", max_value=1_000_000)
                    )
                    await ApiKeysService(ApiKeysRepository(session)).update_key(
                        api_key.id, ApiKeyUpdateData(limits=refreshed, limits_set=True)
                    )
                    limit = (
                        await session.execute(
                            select(ApiKeyLimit).where(
                                ApiKeyLimit.api_key_id == api_key.id, ApiKeyLimit.limit_type == "input_tokens"
                            )
                        )
                    ).scalar_one()
                    limit.current_value = limit.max_value if exhausted else 0
                    await session.commit()
            elif event["type"] == "response.steer.failed":
                finish_successor.set()
            await super().send_text(text)

    socket = Socket(
        [
            (create(), lambda _: True),
            (
                {"type": "response.steer", "previous_response_id": "parent", "input": "First correction"},
                saw("response.completed", "parent"),
            ),
            (
                {"type": "response.steer", "previous_response_id": "parent", "input": "Second correction"},
                saw("response.steer.accepted"),
            ),
        ]
    )
    socket.finish_when = lambda event: saw("response.completed", "successor")([event])

    async def upstream_server(connection):
        wire_frames.append(json.loads(await connection.recv()))
        for kind in ["response.created", "response.completed"]:
            await connection.send(json.dumps(response(kind, "parent")))
        wire_frames.append(json.loads(await connection.recv()))
        await connection.send(
            json.dumps({"type": "response.steer.accepted", "steer": {"id": "first", "previous_response_id": "parent"}})
        )
        receive_second = asyncio.create_task(connection.recv())
        rejected = asyncio.create_task(finish_successor.wait())
        try:
            done, _ = await asyncio.wait([receive_second, rejected], timeout=3, return_when=asyncio.FIRST_COMPLETED)
            assert done, "second submission neither reached the wire nor returned a rejection"
            if receive_second in done:
                wire_frames.append(json.loads(receive_second.result()))
            async with SessionLocal() as session:
                rows = (
                    (
                        await session.execute(
                            select(ApiKeyUsageReservation).where(
                                ApiKeyUsageReservation.api_key_id == api_key.id,
                                ApiKeyUsageReservation.status == "reserved",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                limits = await ApiKeysRepository(session).get_limits_by_key(api_key.id)
                before_terminal.append(
                    (len(rows), next(x.current_value for x in limits if x.limit_type == "input_tokens"))
                )
                if not exhausted:
                    assert len(rows) == 1
                    reservation = await ApiKeysRepository(session).get_usage_reservation(rows[0].id)
                    assert reservation is not None
                    input_items = [item for item in reservation.items if item.limit_type == "input_tokens"]
                    assert [item.reserved_delta for item in input_items] == [8192]
            for kind in ["response.created", "response.completed"]:
                await connection.send(json.dumps(response(kind, "successor", parent="parent")))
            await connection.wait_closed()
        finally:
            for task in [receive_second, rejected]:
                if not task.done():
                    task.cancel()
            await asyncio.gather(receive_second, rejected, return_exceptions=True)

    async with asyncio.timeout(8), serve(upstream_server, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as connection:
            upstream = WebsocketsUpstreamWebSocket(connection)
            monkeypatch.setattr(
                service,
                "_connect_proxy_websocket",
                AsyncMock(return_value=(_make_account("acc_steering_parent_id"), upstream)),
            )
            await _run_route(app_instance, socket)
    assert await service.drain_persistence_tasks(timeout_seconds=2)
    # Then policy is enforced before wire handoff, preserving the original owner.
    failures = [event for event in socket.sent if event["type"] == "response.steer.failed"]
    assert len(wire_frames) == (2 if exhausted else 3)
    assert [event["error"]["code"] for event in failures] == (["rate_limit_exceeded"] if exhausted else [])
    assert saw("response.completed", "successor")(socket.sent)
    if not exhausted:
        assert before_terminal[0][0] == 1
        assert before_terminal[0][1] == 8192
    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(api_key.id)
        # The new policy starts at the controlled counter, then settles one successor.
        expected_input_usage = 1_000_000 if exhausted else 10
        assert next(x.current_value for x in limits if x.limit_type == "input_tokens") == expected_input_usage
        rows = (
            (
                await session.execute(
                    select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == api_key.id)
                )
            )
            .scalars()
            .all()
        )
        assert all(row.status == "finalized" for row in rows)
