from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

import app.main as main
from app import __version__
from app.core.config.settings import Settings
from app.core.middleware.app_version import add_app_version_middleware

pytestmark = pytest.mark.unit


def _build_app(status_code: int, *, headers: dict[str, str] | None = None) -> FastAPI:
    app = FastAPI()
    add_app_version_middleware(app)

    @app.get("/probe")
    async def probe() -> Response:
        return Response(status_code=status_code, headers=headers)

    return app


@pytest.mark.asyncio
async def test_app_version_middleware_adds_header_to_2xx_response():
    transport = ASGITransport(app=_build_app(204))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/probe")

    assert response.status_code == 204
    assert response.headers["X-App-Version"] == __version__


@pytest.mark.asyncio
async def test_app_version_middleware_skips_header_on_5xx_response():
    transport = ASGITransport(app=_build_app(503))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/probe")

    assert response.status_code == 503
    assert "X-App-Version" not in response.headers


@pytest.mark.asyncio
async def test_app_version_middleware_preserves_existing_header_value():
    transport = ASGITransport(app=_build_app(200, headers={"X-App-Version": "route-owned-version"}))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/probe")

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
