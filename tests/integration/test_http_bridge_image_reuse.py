from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from aiohttp import WSMsgType, web
from sqlalchemy import select

import app.core.clients.proxy_websocket as websocket_client
from app.db.models import ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_module
from app.modules.proxy._service.http_bridge import helpers as bridge_helpers
from tests.integration.test_http_promotion_accounting import _key
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as cleanup_http_bridge_sessions,  # noqa: F401
)
from tests.integration.test_http_responses_bridge import _make_app_settings, _promotion_history
from tests.integration.test_http_responses_bridge import (
    promotion_transport as promotion_transport,
)

pytestmark = pytest.mark.integration

# A real 1x1 PNG; the protocol simulator does not decode or validate image bytes.
_INLINE_IMAGE = {
    "type": "input_image",
    "image_url": (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    ),
    "detail": "high",
}
_HEADERS = {"user-agent": "OpenAI/Python"}


def _images(value):
    if isinstance(value, dict):
        if value.get("type") == "input_image":
            return [value]
        return [image for child in value.values() for image in _images(child)]
    if isinstance(value, list):
        return [image for child in value for image in _images(child)]
    return []


def _image_turn(shape="message", *, malformed=False):
    image = dict(_INLINE_IMAGE)
    if malformed:
        image["image_url"] = "data:image/png;base64,not-valid-base64!"
    if shape == "top_level":
        return [image]
    if shape == "tool_output":
        return [
            {"type": "function_call", "call_id": "shot", "name": "screenshot", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "shot", "output": [image]},
        ]
    return [{"role": "user", "content": [{"type": "input_text", "text": "describe"}, image]}]


def _body(history):
    return {"model": "gpt-5.4", "input": history, "prompt_cache_key": "inline-image-session", "stream": False}


@dataclass
class _LoopbackUpstream:
    # Strong references make transport identity meaningful even after closure.
    transports: list[asyncio.Transport] = field(default_factory=list)
    sockets: list[web.WebSocketResponse] = field(default_factory=list)
    frames: list[tuple[int, dict]] = field(default_factory=list)
    handshake_accounts: list[str | None] = field(default_factory=list)
    image_behavior: str = "complete"
    image_received: asyncio.Event = field(default_factory=asyncio.Event)

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        websocket = web.WebSocketResponse()
        await websocket.prepare(request)
        connection = len(self.sockets)
        self.sockets.append(websocket)
        assert request.transport is not None
        self.transports.append(request.transport)
        self.handshake_accounts.append(request.headers.get("chatgpt-account-id"))
        async for message in websocket:
            if message.type != WSMsgType.TEXT:
                continue
            payload = json.loads(message.data)
            self.frames.append((connection, payload))
            response_id = f"resp_loopback_{connection}_{len(self.frames)}"
            is_image = bool(_images(payload.get("input")))
            # Synthetic protocol errors; not a reproduction of the #903 provider trace.
            if is_image and self.image_behavior in {"error", "response.failed"}:
                error = {
                    "code": "invalid_value",
                    "type": "invalid_request_error",
                    "message": "Invalid inline image payload",
                    "param": "input",
                }
                event = {"type": "error", "status": 400, "error": error}
                if self.image_behavior == "response.failed":
                    event = {
                        "type": "response.failed",
                        "response": {"id": response_id, "status": "failed", "error": error, "output": []},
                    }
                await websocket.send_json(event)
                self.image_received.set()
                continue
            if is_image and self.image_behavior == "stall_before_created":
                self.image_received.set()
                continue
            await websocket.send_json(
                {"type": "response.created", "response": {"id": response_id, "status": "in_progress"}}
            )
            if is_image and self.image_behavior == "stall_after_created":
                self.image_received.set()
                continue
            await websocket.send_json(
                {"type": "response.output_text.delta", "delta": "OK", "output_index": 0, "content_index": 0}
            )
            await websocket.send_json(
                {
                    "type": "response.completed",
                    "response": {
                        "id": response_id,
                        "object": "response",
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "OK"}],
                            }
                        ],
                        "usage": {"input_tokens": 24, "output_tokens": 2, "total_tokens": 26},
                    },
                }
            )
        return websocket


@pytest_asyncio.fixture
async def loopback_upstream(promotion_transport, app_instance, monkeypatch):
    """Real FastAPI route, bridge and upstream connector; loopback TCP/WS only.

    The inherited fixture replaces account selection/refresh and leaves a raw
    transport recorder as a negative sink. No provider or cache claim is made.
    """
    upstream = _LoopbackUpstream()
    application = web.Application()
    application.router.add_get("/codex/responses", upstream.handle)
    runner = web.AppRunner(application, shutdown_timeout=1.0)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    settings = _make_app_settings(enabled=True)
    settings.upstream_websocket_trust_env = False
    monkeypatch.setattr(websocket_client, "get_settings", lambda: settings)
    # Exercise the installed Python connector, not a native sidecar or proxy.
    monkeypatch.setattr(websocket_client, "discover_native_egress_client", lambda: None)

    async def connect(headers, access_token, account_id, **kwargs):
        return await websocket_client.connect_responses_websocket(
            headers,
            access_token,
            account_id,
            base_url=f"http://127.0.0.1:{port}",
            allow_direct_egress=True,
        )

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    try:
        yield upstream
    finally:
        service = get_proxy_service_for_app(app_instance)
        for session in list(service._http_bridge_sessions.values()):
            await service._close_http_bridge_session(session)
        for websocket in upstream.sockets:
            await websocket.close()
        await runner.cleanup()


async def _post(async_client, history, *, path="/v1/responses", headers=None, stream=False):
    # Far below the fixture's 75s request budget: pre-created errors must not
    # merely be cleaned up by a bridge acknowledgement timeout.
    async with asyncio.timeout(5):
        return await async_client.post(path, json={**_body(history), "stream": stream}, headers=headers or _HEADERS)


async def _assert_settled(app_instance, key_id, *, count, released):
    service = get_proxy_service_for_app(app_instance)
    await service.drain_persistence_tasks(timeout_seconds=5)
    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == key_id)))
            .scalars()
            .all()
        )
    assert len(rows) == count
    assert all(row.status not in {"reserved", "settling"} for row in rows)
    assert sum(row.status == "released" for row in rows) == released


def _assert_slot_released(session):
    assert not session.pending_requests
    assert session.queued_request_count == 0
    assert not session.response_create_gate.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "stream", "shape"),
    [
        ("/v1/responses", False, "message"),
        ("/v1/responses", True, "top_level"),
        ("/v1/responses/", False, "tool_output"),
        ("/v1/responses/", True, "message"),
        ("/backend-api/codex/responses", True, "tool_output"),
        ("/backend-api/codex/responses", False, "message"),
    ],
)
async def test_inline_image_history_reuses_physical_upstream_session(
    async_client, app_instance, promotion_transport, loopback_upstream, path, shape, stream
):
    _, raw_calls, _ = promotion_transport
    history = _promotion_history()
    image_history = [*history, *_image_turn(shape)]
    followup = [*image_history, {"role": "assistant", "content": "OK"}, {"role": "user", "content": "next"}]
    for turn in [history, image_history, followup]:
        response = await _post(async_client, turn, path=path, stream=stream)
        assert response.status_code == 200, response.text
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
            assert events[-1]["type"] == "response.completed"
        else:
            assert response.json()["status"] == "completed"

    if path == "/backend-api/codex/responses" and not stream:
        # This route explicitly opts out of the bridge, even without images.
        assert len(raw_calls) == 3
        assert not loopback_upstream.sockets
        assert not get_proxy_service_for_app(app_instance)._http_bridge_sessions
        return

    # On original main only the warmup reaches the loopback socket; the image
    # and retained-image turn go to raw_calls. This is the intended RED signal.
    assert not raw_calls, "inline-image history bypassed the reusable bridge"
    assert len(loopback_upstream.sockets) == len(loopback_upstream.transports) == 1
    assert not loopback_upstream.sockets[0].closed
    assert [connection for connection, _ in loopback_upstream.frames] == [0, 0, 0]
    frames = [frame for _, frame in loopback_upstream.frames]
    assert all(frame["type"] == "response.create" for frame in frames)
    assert {frame["prompt_cache_key"] for frame in frames} == {"inline-image-session"}
    assert [_images(frame["input"]) for frame in frames] == [[], [_INLINE_IMAGE], [_INLINE_IMAGE]]
    assert loopback_upstream.handshake_accounts == ["acc_promotion"]
    service = get_proxy_service_for_app(app_instance)
    assert len(service._http_bridge_sessions) == 1
    _assert_slot_released(next(iter(service._http_bridge_sessions.values())))


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["error", "response.failed"])
async def test_inline_image_error_before_created_releases_slot_and_reservation(
    async_client, app_instance, promotion_transport, loopback_upstream, terminal
):
    _, raw_calls, _ = promotion_transport
    key = await _key(async_client, "image-error")
    headers = {**_HEADERS, "Authorization": f"Bearer {key['key']}"}
    history = _promotion_history()
    warmup = await _post(async_client, history, headers=headers)
    assert warmup.status_code == 200, warmup.text
    service = get_proxy_service_for_app(app_instance)
    session = next(iter(service._http_bridge_sessions.values()))
    loopback_upstream.image_behavior = terminal
    failed = await _post(async_client, [*history, *_image_turn(malformed=True)], headers=headers)
    assert failed.status_code == 400, failed.text
    assert failed.json()["error"] == {
        "code": "invalid_value",
        "type": "invalid_request_error",
        "message": "Invalid inline image payload",
        "param": "input",
    }
    assert loopback_upstream.image_received.is_set()
    assert not raw_calls
    _assert_slot_released(session)
    await _assert_settled(app_instance, key["id"], count=2, released=1)

    loopback_upstream.image_behavior = "complete"
    followup = await _post(async_client, _promotion_history("a clean next turn"), headers=headers)
    assert followup.status_code == 200, followup.text
    assert followup.json()["status"] == "completed"
    assert len(loopback_upstream.frames) == 3
    for current in service._http_bridge_sessions.values():
        _assert_slot_released(current)
    await _assert_settled(app_instance, key["id"], count=3, released=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["stall_before_created", "stall_after_created"])
async def test_inline_image_cancellation_releases_slot_and_reservation(
    async_client, app_instance, promotion_transport, loopback_upstream, phase
):
    _, raw_calls, _ = promotion_transport
    key = await _key(async_client, "image-cancel")
    headers = {**_HEADERS, "Authorization": f"Bearer {key['key']}"}
    history = _promotion_history()
    warmup = await _post(async_client, history, headers=headers)
    assert warmup.status_code == 200, warmup.text
    service = get_proxy_service_for_app(app_instance)
    session = next(iter(service._http_bridge_sessions.values()))
    loopback_upstream.image_behavior = phase
    request = asyncio.create_task(_post(async_client, [*history, *_image_turn()], headers=headers))
    observed = asyncio.create_task(loopback_upstream.image_received.wait())
    try:
        done, _ = await asyncio.wait({request, observed}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        assert observed in done, "image request finished/bypassed without reaching the warm upstream WebSocket"
        assert not request.done()
        if phase == "stall_after_created":
            # Server send alone is not proof the bridge consumed the ack.
            async with asyncio.timeout(5):
                while True:
                    async with session.pending_lock:
                        acknowledged = any(state.response_id is not None for state in session.pending_requests)
                    if acknowledged:
                        break
                    await asyncio.sleep(0)
        else:
            assert session.response_create_gate.locked()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    finally:
        for task in (request, observed):
            if not task.done():
                task.cancel()
        await asyncio.gather(request, observed, return_exceptions=True)

    assert not raw_calls
    _assert_slot_released(session)
    assert session.closed
    assert session.upstream_control.retire_after_drain
    await _assert_settled(app_instance, key["id"], count=2, released=1)

    loopback_upstream.image_behavior = "complete"
    followup = await _post(async_client, _promotion_history("a clean next turn"), headers=headers)
    assert followup.status_code == 200, followup.text
    assert followup.json()["status"] == "completed"
    assert len(loopback_upstream.sockets) == 2
    assert loopback_upstream.transports[0] is not loopback_upstream.transports[1]
    assert [connection for connection, _ in loopback_upstream.frames] == [0, 0, 1]
    assert followup.json()["id"].startswith("resp_loopback_1_")
    for current in service._http_bridge_sessions.values():
        _assert_slot_released(current)
    await _assert_settled(app_instance, key["id"], count=3, released=1)


@pytest.mark.asyncio
async def test_silent_inline_image_times_out_without_replay(
    async_client, app_instance, promotion_transport, loopback_upstream, monkeypatch, record_property
):
    # Shorten the existing acknowledgement deadline, not the retry implementation.
    deadline = 0.1
    monkeypatch.setattr(bridge_helpers, "HTTP_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS", deadline)
    _, raw_calls, _ = promotion_transport
    key = await _key(async_client, "image-timeout")
    headers = {**_HEADERS, "Authorization": f"Bearer {key['key']}"}
    history = _promotion_history()
    assert (await _post(async_client, history, headers=headers)).status_code == 200
    service = get_proxy_service_for_app(app_instance)
    session = next(iter(service._http_bridge_sessions.values()))
    loopback_upstream.image_behavior = "stall_before_created"

    started = asyncio.get_running_loop().time()
    response = await _post(async_client, [*history, *_image_turn()], headers=headers)
    elapsed = asyncio.get_running_loop().time() - started
    record_property("client_error_seconds", elapsed)
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "upstream_request_timeout"
    assert deadline <= elapsed < 2, elapsed
    assert not raw_calls
    assert loopback_upstream.image_received.is_set()
    assert [connection for connection, _ in loopback_upstream.frames] == [0, 0], "silent image was replayed"
    _assert_slot_released(session)
    assert session.closed
    await _assert_settled(app_instance, key["id"], count=2, released=1)

    loopback_upstream.image_behavior = "complete"
    followup = await _post(async_client, _promotion_history("a clean next turn"), headers=headers)
    assert followup.status_code == 200, followup.text
    assert [connection for connection, _ in loopback_upstream.frames] == [0, 0, 1]
    await _assert_settled(app_instance, key["id"], count=3, released=1)
