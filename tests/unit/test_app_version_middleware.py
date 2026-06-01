from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from httpx import ASGITransport, AsyncClient
from starlette.types import Message

import app.main as main
from app import __version__
from app.core.config.settings import Settings
from app.core.middleware.app_version import add_app_version_middleware

pytestmark = pytest.mark.unit

_Dispatch = Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]


@pytest.mark.asyncio
async def test_app_version_middleware_adds_header_to_2xx_response():
    app = FastAPI()
    add_app_version_middleware(app)
    dispatch = cast(_Dispatch, app.user_middleware[0].kwargs["dispatch"])

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
        receive=_empty_receive,
    )

    async def call_next(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True}, status_code=204)

    response = await dispatch(request, call_next)

    assert response.headers["X-App-Version"] == __version__


@pytest.mark.asyncio
async def test_app_version_middleware_skips_header_on_5xx_response():
    app = FastAPI()
    add_app_version_middleware(app)
    dispatch = cast(_Dispatch, app.user_middleware[0].kwargs["dispatch"])

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
        receive=_empty_receive,
    )

    async def call_next(_: Request) -> JSONResponse:
        return JSONResponse({"error": "boom"}, status_code=503)

    response = await dispatch(request, call_next)

    assert "X-App-Version" not in response.headers


@pytest.mark.asyncio
async def test_app_version_middleware_preserves_existing_header_value():
    app = FastAPI()
    add_app_version_middleware(app)
    dispatch = cast(_Dispatch, app.user_middleware[0].kwargs["dispatch"])

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
        receive=_empty_receive,
    )

    async def call_next(_: Request) -> Response:
        return Response(status_code=200, headers={"X-App-Version": "route-owned-version"})

    response = await dispatch(request, call_next)

    assert response.headers["X-App-Version"] == "route-owned-version"


@pytest.mark.asyncio
async def test_app_version_middleware_adds_header_to_short_circuited_4xx_response_from_create_app(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(main, "get_settings", lambda: Settings(backpressure_max_concurrent_requests=1))
    app = main.create_app()
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.get("/work")
    async def work():
        entered.set()
        await release.wait()
        return {"ok": True}

    work_route = app.router.routes.pop()
    fallback_index = next(
        index for index, route in enumerate(app.router.routes) if getattr(route, "path", None) == "/{path:path}"
    )
    app.router.routes.insert(fallback_index, work_route)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first_request = asyncio.create_task(client.get("/work"))
        await entered.wait()

        overloaded = await client.get("/work")
        release.set()
        await first_request

    assert overloaded.status_code == 429
    assert overloaded.headers["X-App-Version"] == __version__


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}
