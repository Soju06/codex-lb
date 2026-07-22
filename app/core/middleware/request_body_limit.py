from __future__ import annotations

import logging

from fastapi import FastAPI
from starlette._utils import get_route_path
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config.settings import get_settings
from app.core.errors import dashboard_error, openai_error
from app.core.runtime_logging import log_error_response

REQUEST_BODY_TOO_LARGE_MESSAGE = "Request body exceeds the maximum allowed size"

_RESPONSES_INGRESS_PATHS = frozenset(
    {
        "/backend-api/codex/responses",
        "/v1/responses",
    }
)
_OPENAI_INGRESS_PATH_PREFIXES = (
    "/api/codex",
    "/backend-api",
    "/internal/bridge",
    "/v1",
)
_REQUEST_BODY_TOO_LARGE_STATE = "_codex_lb_request_body_too_large"

logger = logging.getLogger(__name__)


class _RequestBodyTooLarge(Exception):
    pass


def request_body_limit_for_path(path: str) -> int:
    settings = get_settings()
    if path.rstrip("/") in _RESPONSES_INGRESS_PATHS:
        return max(settings.max_decompressed_body_bytes, settings.max_decompressed_responses_body_bytes)
    return settings.max_decompressed_body_bytes


def _path_belongs_to(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def _uses_openai_ingress_errors(path: str) -> bool:
    return any(_path_belongs_to(path, prefix) for prefix in _OPENAI_INGRESS_PATH_PREFIXES)


def request_ingress_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    uses_openai_errors = _uses_openai_ingress_errors(get_route_path(request.scope))
    response_code = "invalid_request_error" if uses_openai_errors and code == "invalid_request" else code
    log_error_response(
        logger,
        request,
        status_code,
        response_code,
        message,
        category="openai_error_response" if uses_openai_errors else "dashboard_error_response",
    )
    if uses_openai_errors:
        return JSONResponse(
            status_code=status_code,
            content=openai_error(response_code, message, error_type="invalid_request_error"),
        )
    return JSONResponse(
        status_code=status_code,
        content=dashboard_error(response_code, message),
    )


def request_body_limit_was_exceeded(request: Request) -> bool:
    return getattr(request.state, _REQUEST_BODY_TOO_LARGE_STATE, False) is True


def _mark_request_body_limit_exceeded(scope: Scope) -> None:
    state = scope.setdefault("state", {})
    state[_REQUEST_BODY_TOO_LARGE_STATE] = True


def _is_unencoded_multipart(headers: Headers) -> bool:
    content_type = headers.get("content-type", "")
    media_type = content_type.partition(";")[0].strip().lower()
    return media_type == "multipart/form-data" and headers.get("content-encoding") is None


def _declared_content_length(headers: Headers) -> int | None:
    value = headers.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if _is_unencoded_multipart(headers):
            await self.app(scope, receive, send)
            return

        path = get_route_path(scope)
        limit = request_body_limit_for_path(path)
        declared_length = _declared_content_length(headers)
        if declared_length is not None and declared_length > limit:
            response = request_ingress_error_response(
                Request(scope),
                status_code=413,
                code="payload_too_large",
                message=REQUEST_BODY_TOO_LARGE_MESSAGE,
            )
            await response(scope, receive, send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    _mark_request_body_limit_exceeded(scope)
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise
            response = request_ingress_error_response(
                Request(scope),
                status_code=413,
                code="payload_too_large",
                message=REQUEST_BODY_TOO_LARGE_MESSAGE,
            )
            await response(scope, receive, send)


def add_request_body_limit_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestBodyLimitMiddleware)
