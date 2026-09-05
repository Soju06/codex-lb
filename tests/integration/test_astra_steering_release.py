from __future__ import annotations

import asyncio
import json
from collections import Counter
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

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
@pytest.mark.parametrize("cancel_socket", [False, True], ids=["disconnect", "cancel"])
@pytest.mark.parametrize("release_failures", [1, 2], ids=["transient", "persistent"])
async def test_websocket_teardown_retries_failed_placeholder_release(
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cancel_socket: bool,
    release_failures: int,
) -> None:
    # Given real reservation accounting and a placeholder awaiting tool input.
    account = _make_account("acc_steering_release")
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)
        keys = ApiKeysService(ApiKeysRepository(session))
        created = await keys.create_key(
            ApiKeyCreateData(
                name="steering-release",
                allowed_models=None,
                expires_at=None,
                limits=[LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000)],
            )
        )
        api_key = await keys.get_key_by_id(created.id)

    service = get_proxy_service_for_app(app_instance)
    call = {"type": "function_call", "call_id": "tool", "name": "slow", "arguments": "{}"}
    result = {"type": "function_call_output", "call_id": "tool", "output": "saved"}
    unrelated_completed = asyncio.Event()
    disconnect = asyncio.Event()
    retry_started = asyncio.Event()
    allow_retry = asyncio.Event()

    class Socket(ScriptedSocket):
        async def receive(self) -> dict:
            if self.scripts:
                return await super().receive()
            await disconnect.wait()
            if cancel_socket:
                raise asyncio.CancelledError
            return {"type": "websocket.disconnect"}

        async def send_text(self, text: str) -> None:
            await super().send_text(text)
            if saw("response.completed", "r-after")([json.loads(text)]):
                unrelated_completed.set()

    socket = Socket(
        [
            (create(), lambda _: True),
            (
                {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"},
                saw("response.created", "r1"),
            ),
            (create(parent="r1", input_items=[result]), saw("response.steer.pending")),
            (create(input_items="Unrelated"), saw("response.completed", "r2")),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                response("response.completed", "r1", output=[call]),
                {
                    "type": "response.steer.pending",
                    "steer": {"id": "s1", "previous_response_id": "r1"},
                    "reason": "waiting_for_required_input",
                    "required_input": [{"type": "function_call_output", "call_id": "tool"}],
                },
            ],
            [response("response.created", "r2", parent="r1"), response("response.completed", "r2", parent="r1")],
            [response("response.created", "r-after"), response("response.completed", "r-after")],
        ]
    )
    monkeypatch.setattr(service, "_connect_proxy_websocket", AsyncMock(return_value=(account, upstream)))
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_revalidate_open_websocket_account", AsyncMock(return_value=(account, None, None)))
    monkeypatch.setattr(proxy_api, "_validate_proxy_websocket_request", AsyncMock(return_value=(api_key, None)))
    monkeypatch.setattr(proxy_api, "_websocket_upstream_transport_denial", AsyncMock(return_value=None))

    states = []
    heartbeats = []
    reservations = []
    release_attempts: Counter[str] = Counter()
    finalized: list[str] = []
    original_start = service._start_request_state_api_key_reservation_heartbeat
    original_release = ApiKeysService.release_usage_reservation
    original_finalize = ApiKeysService.finalize_usage_reservation

    def start(state, **kwargs):
        original_start(state, **kwargs)
        if state not in states:
            states.append(state)
            reservations.append(state.api_key_reservation)
            heartbeats.append(state.api_key_reservation_heartbeat_task)

    async def release(self, reservation_id):
        release_attempts[reservation_id] += 1
        if reservation_id == reservations[1].reservation_id:
            if release_attempts[reservation_id] == 1:
                raise OperationalError("release placeholder", {}, RuntimeError("transient database failure"))
            retry_started.set()
            await allow_retry.wait()
            if release_failures == 2:
                raise OperationalError("release placeholder", {}, RuntimeError("persistent database failure"))
        await original_release(self, reservation_id)

    async def finalize(self, reservation_id, **kwargs):
        await original_finalize(self, reservation_id, **kwargs)
        finalized.append(reservation_id)

    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", start)
    monkeypatch.setattr(ApiKeysService, "release_usage_reservation", release)
    monkeypatch.setattr(ApiKeysService, "finalize_usage_reservation", finalize)
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

    operation = asyncio.create_task(
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
        )
    )
    retry_signal = asyncio.create_task(retry_started.wait())
    try:
        # When the refund fails, both the explicit continuation and unrelated work finish.
        await asyncio.wait_for(unrelated_completed.wait(), timeout=5)
        assert await service.drain_persistence_tasks(timeout_seconds=2)
        placeholder_id = reservations[1].reservation_id
        async with SessionLocal() as session:
            repo = ApiKeysRepository(session)
            placeholder = await repo.get_usage_reservation(placeholder_id)
            assert placeholder is not None and placeholder.status == "reserved"
            limits = await repo.get_limits_by_key(api_key.id)
            assert limits[0].current_value == 42 + sum(item.reserved_delta for item in placeholder.items)
        assert release_attempts == {placeholder_id: 1}
        assert states[1].api_key_reservation_heartbeat_task is None
        assert heartbeats[1].done()

        # Then teardown owns and awaits the retry rather than leaving a detached reservation.
        disconnect.set()
        await asyncio.wait({operation, retry_signal}, timeout=2, return_when=asyncio.FIRST_COMPLETED)
        assert retry_started.is_set(), "socket teardown dropped the failed placeholder release"
        assert not operation.done()
        cleanup_tasks = [task for task in service._background_cleanup_tasks if not task.done()]
        assert any(task.get_name() == "proxy-websocket-finalization-scope-cleanup" for task in cleanup_tasks)
        allow_retry.set()
        if cancel_socket:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, timeout=2)
        else:
            await asyncio.wait_for(operation, timeout=2)
        assert await service.drain_persistence_tasks(timeout_seconds=2)
        assert all(task.done() for task in cleanup_tasks + heartbeats)
        assert not service._background_cleanup_tasks
        assert release_attempts == {placeholder_id: 2}
        assert Counter(finalized) == {reservations[index].reservation_id: 1 for index in (0, 2, 3)}
        assert all(
            state.response_create_admission is None and not state.response_create_gate_acquired for state in states
        )
        async with SessionLocal() as session:
            repo = ApiKeysRepository(session)
            stored = []
            for reservation in reservations:
                row = await repo.get_usage_reservation(reservation.reservation_id)
                assert row is not None
                stored.append(row)
            assert [row.status for row in stored] == [
                "finalized",
                "released" if release_failures == 1 else "reserved",
                "finalized",
                "finalized",
            ]
            if release_failures == 1:
                assert [item.actual_delta for item in stored[1].items] == [0]
            limits = await repo.get_limits_by_key(api_key.id)
            assert limits[0].current_value == (
                42 if release_failures == 1 else 42 + sum(item.reserved_delta for item in stored[1].items)
            )
        assert (
            sum(
                record.exc_info is not None and isinstance(record.exc_info[1], OperationalError)
                for record in caplog.records
            )
            == release_failures
        )
    finally:
        disconnect.set()
        allow_retry.set()
        if not operation.done():
            operation.cancel()
        retry_signal.cancel()
        await asyncio.gather(operation, retry_signal, return_exceptions=True)
        assert await service.drain_persistence_tasks(timeout_seconds=2)
