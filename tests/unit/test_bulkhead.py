from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.core.resilience.bulkhead as bulkhead_module
from app.core.resilience.bulkhead import BulkheadMiddleware, BulkheadSemaphore

pytestmark = pytest.mark.unit


def _bulkhead(**kwargs: int) -> BulkheadSemaphore:
    return BulkheadSemaphore(
        proxy_http_limit=kwargs.get("proxy_http_limit", 1),
        proxy_websocket_limit=kwargs.get("proxy_websocket_limit", 1),
        proxy_compact_limit=kwargs.get("proxy_compact_limit", 1),
        dashboard_limit=kwargs.get("dashboard_limit", 1),
    )


@pytest.mark.asyncio
async def test_bulkhead_allows_requests_under_limit():
    app = FastAPI()
    app.add_middleware(cast(Any, BulkheadMiddleware), bulkhead=_bulkhead(proxy_http_limit=2, dashboard_limit=2))

    @app.get("/v1/work")
    async def work():
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/v1/work")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_bulkhead_returns_429_when_proxy_http_lane_full():
    app = FastAPI()
    app.add_middleware(cast(Any, BulkheadMiddleware), bulkhead=_bulkhead())
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

    assert overloaded.status_code == 429
    assert overloaded.json()["error"]["code"] == "proxy_overloaded"
    assert overloaded.headers["retry-after"] == "5"
    assert first_response.status_code == 200


@pytest.mark.asyncio
async def test_bulkhead_compact_lane_isolated_from_general_proxy_http():
    app = FastAPI()
    app.add_middleware(cast(Any, BulkheadMiddleware), bulkhead=_bulkhead())
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.get("/v1/work")
    async def proxy_work():
        entered.set()
        await release.wait()
        return {"ok": True}

    @app.post("/v1/responses/compact")
    async def compact_work():
        return {"ok": "compact"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first_request = asyncio.create_task(client.get("/v1/work"))
        await entered.wait()

        compact_response = await client.post("/v1/responses/compact")
        release.set()
        await first_request

    assert compact_response.status_code == 200
    assert compact_response.json() == {"ok": "compact"}


@pytest.mark.asyncio
async def test_bulkhead_isolates_dashboard_when_proxy_full():
    app = FastAPI()
    app.add_middleware(cast(Any, BulkheadMiddleware), bulkhead=_bulkhead())
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
    app.add_middleware(cast(Any, BulkheadMiddleware), bulkhead=_bulkhead())
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
async def test_bulkhead_websocket_denies_with_http_response_when_lane_full():
    bulkhead = _bulkhead()
    lane_name, sem = bulkhead.get_semaphore("websocket", "/v1/responses")
    assert lane_name == "proxy_websocket"
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
    assert sent_events[0]["type"] == "websocket.http.response.start"
    assert sent_events[0]["status"] == 429
    assert sent_events[1]["type"] == "websocket.http.response.body"
    payload = json.loads(cast(bytes, sent_events[1]["body"]).decode("utf-8"))
    assert payload["error"]["code"] == "proxy_overloaded"


@pytest.mark.asyncio
async def test_bulkhead_dashboard_websocket_uses_detail_payload_when_lane_full():
    bulkhead = _bulkhead(dashboard_limit=1)
    lane_name, sem = bulkhead.get_semaphore("websocket", "/api/status/socket")
    assert lane_name == "dashboard"
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
            {"type": "websocket", "path": "/api/status/socket"},
            cast(Any, receive),
            cast(Any, send),
        )
    finally:
        sem.release()

    assert app_called is False
    payload = json.loads(cast(bytes, sent_events[1]["body"]).decode("utf-8"))
    assert payload == {"detail": "codex-lb is temporarily overloaded in the dashboard lane"}


def test_get_bulkhead_derives_compact_limit_from_http_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bulkhead_module, "_bulkhead", None)
    bulkhead = bulkhead_module.get_bulkhead(proxy_http_limit=0, proxy_websocket_limit=1, dashboard_limit=1)
    lane_name, sem = bulkhead.get_semaphore("http", "/v1/responses/compact")
    assert lane_name == "proxy_compact"
    assert sem is None


@pytest.mark.asyncio
async def test_bulkhead_websocket_lane_recovers_after_active_session_exits():
    bulkhead = _bulkhead(proxy_websocket_limit=1)
    entered = asyncio.Event()
    release = asyncio.Event()
    app_calls = 0

    async def inner_app(scope, receive, send):
        nonlocal app_calls
        del scope, receive
        app_calls += 1
        await send({"type": "websocket.accept"})
        if app_calls == 1:
            entered.set()
            await release.wait()
        await send({"type": "websocket.close", "code": 1000})

    middleware = BulkheadMiddleware(cast(Any, inner_app), bulkhead=bulkhead)

    async def dormant_receive() -> dict[str, object]:
        await asyncio.sleep(3600)
        return {"type": "websocket.disconnect", "code": 1000}

    first_sent_events: list[dict[str, object]] = []

    async def first_send(message: dict[str, object]) -> None:
        first_sent_events.append(message)

    first_scope = {"type": "websocket", "path": "/backend-api/codex/responses"}
    first_call = asyncio.create_task(
        middleware(
            cast(Any, first_scope),
            cast(Any, dormant_receive),
            cast(Any, first_send),
        )
    )
    await entered.wait()

    denied_events: list[dict[str, object]] = []
    connect_delivered = False

    async def denial_receive() -> dict[str, object]:
        nonlocal connect_delivered
        if not connect_delivered:
            connect_delivered = True
            return {"type": "websocket.connect"}
        return {"type": "websocket.disconnect", "code": 1000}

    async def denial_send(message: dict[str, object]) -> None:
        denied_events.append(message)

    await middleware(
        cast(Any, first_scope),
        cast(Any, denial_receive),
        cast(Any, denial_send),
    )

    release.set()
    await first_call

    recovered_events: list[dict[str, object]] = []

    async def recovered_send(message: dict[str, object]) -> None:
        recovered_events.append(message)

    await middleware(
        cast(Any, first_scope),
        cast(Any, denial_receive),
        cast(Any, recovered_send),
    )

    assert first_sent_events[0]["type"] == "websocket.accept"
    assert denied_events[0]["type"] == "websocket.http.response.start"
    assert denied_events[0]["status"] == 429
    assert recovered_events[0]["type"] == "websocket.accept"
    assert recovered_events[1]["type"] == "websocket.close"
    assert app_calls == 2
