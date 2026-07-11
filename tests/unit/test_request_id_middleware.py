from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.types import Message

from app.core.middleware.request_id import add_request_id_middleware
from app.core.utils.request_id import get_request_id, get_request_scope_id

pytestmark = pytest.mark.unit

_Dispatch = Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]


@pytest.mark.asyncio
async def test_request_id_middleware_resets_context_on_success():
    app = FastAPI()
    add_request_id_middleware(app)
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
            "headers": [(b"x-request-id", b"req-test-123")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive=_empty_receive,
    )

    async def call_next(_: Request) -> JSONResponse:
        assert get_request_id() == "req-test-123"
        assert get_request_scope_id() not in {None, "req-test-123"}
        return JSONResponse({"ok": True})

    response = await dispatch(request, call_next)

    assert response.headers["x-request-id"] == "req-test-123"
    assert get_request_id() is None
    assert get_request_scope_id() is None


@pytest.mark.asyncio
async def test_request_id_middleware_uses_distinct_server_scopes_for_duplicate_client_ids():
    app = FastAPI()
    add_request_id_middleware(app)
    dispatch = cast(_Dispatch, app.user_middleware[0].kwargs["dispatch"])
    scopes: list[str] = []

    def make_request() -> Request:
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/health",
                "raw_path": b"/health",
                "query_string": b"",
                "root_path": "",
                "headers": [(b"x-request-id", b"duplicate-client-id")],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            },
            receive=_empty_receive,
        )

    async def call_next(_: Request) -> JSONResponse:
        assert get_request_id() == "duplicate-client-id"
        scope = get_request_scope_id()
        assert scope is not None
        scopes.append(scope)
        return JSONResponse({"ok": True})

    first, second = await asyncio.gather(
        dispatch(make_request(), call_next),
        dispatch(make_request(), call_next),
    )

    assert first.headers["x-request-id"] == "duplicate-client-id"
    assert second.headers["x-request-id"] == "duplicate-client-id"
    assert len(set(scopes)) == 2


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}
