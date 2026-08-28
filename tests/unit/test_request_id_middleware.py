from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.middleware.request_id import add_request_id_middleware
from app.core.utils.request_id import get_request_id, get_request_scope_id

pytestmark = pytest.mark.unit


def _build_app(request_ids: list[str | None], scope_ids: list[str | None]) -> FastAPI:
    app = FastAPI()
    add_request_id_middleware(app)

    @app.get("/health")
    async def health() -> dict[str, bool]:
        request_ids.append(get_request_id())
        scope_ids.append(get_request_scope_id())
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_request_id_middleware_resets_context_on_success():
    request_ids: list[str | None] = []
    scope_ids: list[str | None] = []
    transport = ASGITransport(app=_build_app(request_ids, scope_ids))

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health", headers={"x-request-id": "req-test-123"})

    assert response.headers["x-request-id"] == "req-test-123"
    assert request_ids == ["req-test-123"]
    assert scope_ids[0] not in {None, "req-test-123"}
    assert get_request_id() is None
    assert get_request_scope_id() is None


@pytest.mark.asyncio
async def test_request_id_middleware_generates_id_when_missing():
    request_ids: list[str | None] = []
    scope_ids: list[str | None] = []
    transport = ASGITransport(app=_build_app(request_ids, scope_ids))

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    generated = response.headers["x-request-id"]
    assert generated
    assert request_ids == [generated]


@pytest.mark.asyncio
async def test_request_id_middleware_falls_back_to_request_id_header():
    request_ids: list[str | None] = []
    scope_ids: list[str | None] = []
    transport = ASGITransport(app=_build_app(request_ids, scope_ids))

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health", headers={"request-id": "legacy-456"})

    assert response.headers["x-request-id"] == "legacy-456"
    assert request_ids == ["legacy-456"]


@pytest.mark.asyncio
async def test_request_id_middleware_uses_distinct_server_scopes_for_duplicate_client_ids():
    request_ids: list[str | None] = []
    scope_ids: list[str | None] = []
    transport = ASGITransport(app=_build_app(request_ids, scope_ids))

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first, second = await asyncio.gather(
            client.get("/health", headers={"x-request-id": "duplicate-client-id"}),
            client.get("/health", headers={"x-request-id": "duplicate-client-id"}),
        )

    assert first.headers["x-request-id"] == "duplicate-client-id"
    assert second.headers["x-request-id"] == "duplicate-client-id"
    assert request_ids == ["duplicate-client-id", "duplicate-client-id"]
    assert len(set(scope_ids)) == 2
