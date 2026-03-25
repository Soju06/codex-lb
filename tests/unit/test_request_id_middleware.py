from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.middleware.request_id import add_request_id_middleware
from app.core.utils.request_id import get_request_id

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_request_id_middleware_resets_context_on_success():
    app = FastAPI()
    add_request_id_middleware(app)
    dispatch = app.user_middleware[0].kwargs["dispatch"]

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
        receive=lambda: None,
    )

    async def call_next(_: Request) -> JSONResponse:
        assert get_request_id() == "req-test-123"
        return JSONResponse({"ok": True})

    response = await dispatch(request, call_next)

    assert response.headers["x-request-id"] == "req-test-123"
    assert get_request_id() is None
