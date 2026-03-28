from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from importlib import import_module
from typing import cast

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.types import Message

from app.main import add_in_flight_middleware

shutdown_state = import_module("app.core.shutdown")

pytestmark = pytest.mark.unit

_Dispatch = Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]


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
    app = FastAPI()
    add_in_flight_middleware(app)
    dispatch = cast(_Dispatch, app.user_middleware[0].kwargs["dispatch"])

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
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
        },
        receive=receive,
    )

    async def call_next(_: Request) -> JSONResponse:
        assert shutdown_state.get_in_flight() == 1
        return JSONResponse({"ok": True})

    await dispatch(request, call_next)

    assert shutdown_state.get_in_flight() == 0
