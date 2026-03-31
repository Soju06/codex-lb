from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.resilience.bulkhead import BulkheadMiddleware, BulkheadSemaphore

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_bulkhead_allows_requests_under_limit():
    app = FastAPI()
    app.add_middleware(
        cast(Any, BulkheadMiddleware),
        bulkhead=BulkheadSemaphore(proxy_limit=2, dashboard_limit=2),
    )

    @app.get("/v1/work")
    async def work():
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/v1/work")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_bulkhead_returns_503_when_proxy_bucket_full():
    app = FastAPI()
    app.add_middleware(
        cast(Any, BulkheadMiddleware),
        bulkhead=BulkheadSemaphore(proxy_limit=1, dashboard_limit=1),
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.get("/v1/work")
    async def work():
        entered.set()
        await release.wait()
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first_request = asyncio.create_task(client.get("/v1/work"))
        await entered.wait()

        overloaded = await client.get("/v1/work")
        release.set()
        first_response = await first_request

    assert overloaded.status_code == 503
    assert overloaded.json() == {"detail": "Service temporarily unavailable (bulkhead full)"}
    assert overloaded.headers["retry-after"] == "5"
    assert first_response.status_code == 200


@pytest.mark.asyncio
async def test_bulkhead_isolates_dashboard_when_proxy_full():
    app = FastAPI()
    app.add_middleware(
        cast(Any, BulkheadMiddleware),
        bulkhead=BulkheadSemaphore(proxy_limit=1, dashboard_limit=1),
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.get("/v1/work")
    async def proxy_work():
        entered.set()
        await release.wait()
        return {"ok": True}

    @app.get("/api/status")
    async def dashboard_status():
        return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first_request = asyncio.create_task(client.get("/v1/work"))
        await entered.wait()

        dashboard_response = await client.get("/api/status")
        release.set()
        await first_request

    assert dashboard_response.status_code == 200
    assert dashboard_response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_bulkhead_health_probes_bypass_limits():
    app = FastAPI()
    app.add_middleware(
        cast(Any, BulkheadMiddleware),
        bulkhead=BulkheadSemaphore(proxy_limit=1, dashboard_limit=1),
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.get("/v1/work")
    async def proxy_work():
        entered.set()
        await release.wait()
        return {"ok": True}

    @app.get("/health/live")
    async def health_live():
        return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first_request = asyncio.create_task(client.get("/v1/work"))
        await entered.wait()

        health_response = await client.get("/health/live")
        release.set()
        await first_request

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_bulkhead_websocket_rejects_with_close_when_proxy_bucket_full():
    bulkhead = BulkheadSemaphore(proxy_limit=1, dashboard_limit=1)
    sem = bulkhead.get_semaphore("/v1/responses")
    assert sem is not None
    await sem.acquire()

    app_called = False

    async def inner_app(scope, receive, send):
        nonlocal app_called
        app_called = True
        del scope, receive, send

    middleware = BulkheadMiddleware(cast(Any, inner_app), bulkhead=bulkhead)
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
        sem.release()

    assert app_called is False
    assert sent_events == [
        {
            "type": "websocket.close",
            "code": 1013,
            "reason": "Service temporarily unavailable (bulkhead full)",
        }
    ]
