from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.resilience.backpressure import BackpressureMiddleware

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_backpressure_allows_requests_under_limit():
    app = FastAPI()
    app.add_middleware(cast(Any, BackpressureMiddleware), max_concurrent=2)

    @app.get("/work")
    async def work():
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/work")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_backpressure_returns_429_when_at_capacity():
    app = FastAPI()
    app.add_middleware(cast(Any, BackpressureMiddleware), max_concurrent=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.get("/work")
    async def work():
        entered.set()
        await release.wait()
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first_request = asyncio.create_task(client.get("/work"))
        await entered.wait()

        overloaded = await client.get("/work")
        release.set()
        first_response = await first_request

    assert overloaded.status_code == 429
    assert overloaded.json() == {"detail": "Too Many Requests"}
    assert overloaded.headers["retry-after"] == "5"
    assert first_response.status_code == 200


@pytest.mark.asyncio
async def test_backpressure_exempts_health_live_even_at_capacity():
    app = FastAPI()
    app.add_middleware(cast(Any, BackpressureMiddleware), max_concurrent=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.get("/work")
    async def work():
        entered.set()
        await release.wait()
        return {"ok": True}

    @app.get("/health/live")
    async def health_live():
        return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first_request = asyncio.create_task(client.get("/work"))
        await entered.wait()

        health_response = await client.get("/health/live")
        release.set()
        await first_request

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_backpressure_websocket_rejects_with_close_when_at_capacity():
    app_called = False

    async def inner_app(scope, receive, send):
        nonlocal app_called
        app_called = True
        del scope, receive, send

    middleware = BackpressureMiddleware(cast(Any, inner_app), max_concurrent=1)
    await middleware._semaphore.acquire()
    sent_events: list[dict[str, object]] = []
    connect_delivered = False

    async def receive() -> dict[str, object]:
        nonlocal connect_delivered
        if not connect_delivered:
            connect_delivered = True
            return {"type": "websocket.connect"}
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(message: dict[str, object]) -> None:
        sent_events.append(message)

    try:
        await middleware(
            {"type": "websocket", "path": "/v1/responses"},
            cast(Any, receive),
            cast(Any, send),
        )
    finally:
        middleware._semaphore.release()

    assert app_called is False
    assert sent_events == [
        {
            "type": "websocket.close",
            "code": 1013,
            "reason": "Too Many Requests",
        }
    ]
