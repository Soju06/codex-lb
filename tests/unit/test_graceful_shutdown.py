from __future__ import annotations

import asyncio
from importlib import import_module

import pytest

from app.main import InFlightMiddleware

shutdown_state = import_module("app.core.shutdown")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_shutdown_state() -> None:
    setattr(shutdown_state, "_draining", False)
    setattr(shutdown_state, "_in_flight", 0)


def test_set_draining_updates_shutdown_state() -> None:
    shutdown_state.set_draining(True)

    assert shutdown_state._draining is True


@pytest.mark.asyncio
async def test_wait_for_in_flight_drain_waits_until_zero() -> None:
    shutdown_state.increment_in_flight()

    async def release_request() -> None:
        await asyncio.sleep(0.05)
        shutdown_state.decrement_in_flight()

    release_task = asyncio.create_task(release_request())

    drained = await shutdown_state.wait_for_in_flight_drain(timeout_seconds=1.0, poll_interval_seconds=0.01)

    await release_task
    assert drained is True
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_wait_for_in_flight_drain_respects_timeout() -> None:
    shutdown_state.increment_in_flight()

    drained = await shutdown_state.wait_for_in_flight_drain(timeout_seconds=0.05, poll_interval_seconds=0.01)

    assert drained is False
    assert shutdown_state.get_in_flight() == 1


@pytest.mark.asyncio
async def test_in_flight_middleware_increments_and_decrements() -> None:
    in_flight_during_app: int | None = None

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal in_flight_during_app
        in_flight_during_app = shutdown_state.get_in_flight()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    middleware = InFlightMiddleware(inner_app)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": b"", "more_body": False}

    sent_messages: list[dict] = []

    async def send(msg):  # noqa: ANN001, ANN202
        sent_messages.append(msg)

    await middleware(scope, receive, send)

    assert in_flight_during_app == 1
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_in_flight_middleware_tracks_websocket() -> None:
    in_flight_during_ws: int | None = None

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal in_flight_during_ws
        in_flight_during_ws = shutdown_state.get_in_flight()

    middleware = InFlightMiddleware(inner_app)

    scope = {"type": "websocket", "path": "/v1/responses"}

    async def ws_receive():  # noqa: ANN202
        return {"type": "websocket.connect"}

    async def ws_send(msg):  # noqa: ANN001, ANN202
        pass

    await middleware(scope, ws_receive, ws_send)

    assert in_flight_during_ws == 1
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_in_flight_middleware_skips_lifespan() -> None:
    app_called = False

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal app_called
        app_called = True

    middleware = InFlightMiddleware(inner_app)

    async def ls_receive():  # noqa: ANN202
        return {}

    async def ls_send(msg):  # noqa: ANN001, ANN202
        pass

    await middleware({"type": "lifespan"}, ls_receive, ls_send)

    assert app_called is True
    assert shutdown_state.get_in_flight() == 0
