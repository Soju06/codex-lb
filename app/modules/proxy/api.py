from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.errors import openai_error
from app.core.clients.proxy import ProxyResponseError
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.dependencies import ProxyContext, get_proxy_context
from app.modules.proxy.schemas import RateLimitStatusPayload

router = APIRouter(prefix="/backend-api/codex", tags=["proxy"])
usage_router = APIRouter(tags=["proxy"])


@router.post("/responses")
async def responses(
    request: Request,
    payload: ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> Response:
    rate_limit_headers = await context.service.rate_limit_headers()
    stream = context.service.stream_responses(
        payload,
        request.headers,
        propagate_http_errors=True,
    )
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        return StreamingResponse(
            _prepend_first(None, stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )
    except ProxyResponseError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.payload, headers=rate_limit_headers)
    return StreamingResponse(
        _prepend_first(first, stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", **rate_limit_headers},
    )


@router.post("/responses/compact")
async def responses_compact(
    request: Request,
    payload: ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> JSONResponse:
    rate_limit_headers = await context.service.rate_limit_headers()
    try:
        result = await context.service.compact_responses(payload, request.headers)
    except NotImplementedError:
        error = openai_error("not_implemented", "responses/compact is not implemented")
        return JSONResponse(status_code=501, content=error, headers=rate_limit_headers)
    except ProxyResponseError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.payload, headers=rate_limit_headers)
    return JSONResponse(content=result.model_dump(exclude_none=True), headers=rate_limit_headers)


@usage_router.get("/api/codex/usage", response_model=RateLimitStatusPayload)
async def codex_usage(
    context: ProxyContext = Depends(get_proxy_context),
) -> RateLimitStatusPayload:
    payload = await context.service.get_rate_limit_payload()
    return RateLimitStatusPayload.from_data(payload)


async def _prepend_first(first: str | None, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    if first is not None:
        yield first
    async for line in stream:
        yield line
