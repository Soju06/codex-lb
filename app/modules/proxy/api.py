from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.chat_responses import collect_chat_completion, stream_chat_chunks
from app.core.openai.models_catalog import MODEL_CATALOG
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.openai.v1_requests import V1ResponsesCompactRequest, V1ResponsesRequest
from app.core.types import JsonValue
from app.dependencies import ProxyContext, get_proxy_context
from app.modules.proxy.schemas import RateLimitStatusPayload

router = APIRouter(prefix="/backend-api/codex", tags=["proxy"])
v1_router = APIRouter(prefix="/v1", tags=["proxy"])
usage_router = APIRouter(tags=["proxy"])


@router.post("/responses")
async def responses(
    request: Request,
    payload: ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> Response:
    return await _stream_responses(request, payload, context)


@v1_router.post("/responses")
async def v1_responses(
    request: Request,
    payload: V1ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> Response:
    responses_payload = payload.to_responses_request()
    if responses_payload.stream:
        return await _stream_responses(request, responses_payload, context)
    return await _collect_responses(request, responses_payload, context)


@v1_router.get("/models")
async def v1_models() -> JSONResponse:
    created = int(time.time())
    items = [
        {
            "id": model_id,
            "object": "model",
            "created": created,
            "owned_by": "codex-lb",
            "metadata": entry.model_dump(mode="json"),
        }
        for model_id, entry in MODEL_CATALOG.items()
    ]
    return JSONResponse({"object": "list", "data": items})


@v1_router.post("/chat/completions")
async def v1_chat_completions(
    request: Request,
    payload: ChatCompletionsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> Response:
    rate_limit_headers = await context.service.rate_limit_headers()
    responses_payload = payload.to_responses_request()
    responses_payload.stream = True
    stream = context.service.stream_responses(
        responses_payload,
        request.headers,
        propagate_http_errors=True,
    )
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        first = None
    except ProxyResponseError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.payload, headers=rate_limit_headers)

    stream_with_first = _prepend_first(first, stream)
    if payload.stream:
        stream_options = payload.stream_options
        include_usage = bool(stream_options and stream_options.include_usage)
        return StreamingResponse(
            stream_chat_chunks(stream_with_first, model=payload.model, include_usage=include_usage),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    result = await collect_chat_completion(stream_with_first, model=payload.model)
    status_code = 200
    if isinstance(result, dict) and "error" in result:
        error = result.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        status_code = 503 if code == "no_accounts" else 502
    return JSONResponse(content=result, status_code=status_code, headers=rate_limit_headers)


async def _stream_responses(
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
) -> Response:
    rate_limit_headers = await context.service.rate_limit_headers()
    payload.stream = True
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


async def _collect_responses(
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
) -> Response:
    rate_limit_headers = await context.service.rate_limit_headers()
    payload.stream = True
    stream = context.service.stream_responses(
        payload,
        request.headers,
        propagate_http_errors=True,
    )
    try:
        response_payload = await _collect_responses_payload(stream)
    except ProxyResponseError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.payload, headers=rate_limit_headers)
    if isinstance(response_payload, dict) and response_payload.get("object") == "response":
        status = response_payload.get("status")
        if status == "failed":
            error_payload = _error_envelope_from_response(response_payload.get("error"))
            status_code = _status_for_error(error_payload.get("error"))
            return JSONResponse(status_code=status_code, content=error_payload, headers=rate_limit_headers)
    if (
        isinstance(response_payload, dict)
        and "error" in response_payload
        and response_payload.get("object") != "response"
    ):
        return JSONResponse(status_code=502, content=response_payload, headers=rate_limit_headers)
    return JSONResponse(content=response_payload, headers=rate_limit_headers)


@router.post("/responses/compact")
async def responses_compact(
    request: Request,
    payload: ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> JSONResponse:
    return await _compact_responses(request, payload, context)


@v1_router.post("/responses/compact")
async def v1_responses_compact(
    request: Request,
    payload: V1ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> JSONResponse:
    return await _compact_responses(request, payload.to_compact_request(), context)


async def _compact_responses(
    request: Request,
    payload: ResponsesCompactRequest,
    context: ProxyContext,
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


def _parse_sse_payload(line: str) -> dict[str, JsonValue] | None:
    data = None
    if line.startswith("data:"):
        data = line[5:].strip()
    elif "\n" in line:
        for part in line.splitlines():
            if part.startswith("data:"):
                data = part[5:].strip()
                break
    if data is None:
        return None
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


async def _collect_responses_payload(stream: AsyncIterator[str]) -> dict[str, JsonValue]:
    response_payload: dict[str, JsonValue] | None = None
    async for line in stream:
        payload = _parse_sse_payload(line)
        if not payload:
            continue
        event_type = payload.get("type")
        if event_type in ("response.completed", "response.incomplete", "response.failed"):
            response = payload.get("response")
            if isinstance(response, dict):
                response_payload = cast(dict[str, JsonValue], response)
    if response_payload is not None:
        return response_payload
    return cast(dict[str, JsonValue], openai_error("upstream_error", "Upstream error"))


def _error_envelope_from_response(error_value: JsonValue | None) -> dict[str, JsonValue]:
    if isinstance(error_value, dict):
        return {"error": cast(dict[str, JsonValue], error_value)}
    return cast(dict[str, JsonValue], openai_error("upstream_error", "Upstream error"))


def _status_for_error(error_value: JsonValue | None) -> int:
    if isinstance(error_value, dict):
        code = error_value.get("code")
        if code == "no_accounts":
            return 503
    return 502
