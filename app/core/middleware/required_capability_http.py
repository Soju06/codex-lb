from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette._utils import get_route_path

from app.core.auth.dependencies import validate_required_proxy_api_key_authorization
from app.core.clients.proxy import CODEX_LB_REQUIRED_CAPABILITY_HEADER
from app.core.errors import openai_error
from app.core.exceptions import ProxyAuthError, ProxyRequiredCapabilityTransportError
from app.core.runtime_logging import log_error_response
from app.modules.proxy.images_observability import (
    IMAGE_ROUTE_STARTED_AT_STATE,
    record_images_route_observability,
)

logger = logging.getLogger(__name__)

_JSON_BODY_DENY_PATHS = frozenset(
    {
        "/backend-api/codex/responses",
        "/backend-api/codex/responses/compact",
        "/backend-api/codex/images/generations",
        "/v1/responses",
        "/v1/responses/compact",
        "/v1/chat/completions",
        "/v1/images/generations",
        "/v1/reset-credit",
        "/v1/warmup",
        "/api/codex/rate-limit-reset-credits/consume",
    }
)


def _is_pre_body_deny_path(path: str) -> bool:
    normalized = path.rstrip("/")
    return normalized in _JSON_BODY_DENY_PATHS or normalized.startswith("/v1/warmup/")


def add_required_capability_http_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def required_capability_http_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method != "POST":
            return await call_next(request)
        if not request.headers.getlist(CODEX_LB_REQUIRED_CAPABILITY_HEADER):
            return await call_next(request)
        if not _is_pre_body_deny_path(get_route_path(request.scope)):
            return await call_next(request)
        try:
            await validate_required_proxy_api_key_authorization(request.headers.get("authorization"))
        except ProxyAuthError as exc:
            return _capability_error_response(request, exc)
        return _capability_error_response(request, ProxyRequiredCapabilityTransportError())


def _capability_error_response(
    request: Request,
    exc: ProxyAuthError | ProxyRequiredCapabilityTransportError,
) -> JSONResponse:
    log_error_response(
        logger,
        request,
        exc.status_code,
        exc.code,
        exc.message,
        category="openai_error_response",
    )
    path = get_route_path(request.scope)
    if path.rstrip("/").endswith("/images/generations"):
        started_at = getattr(request.state, IMAGE_ROUTE_STARTED_AT_STATE, None)
        if not isinstance(started_at, float):
            started_at = time.perf_counter()
        record_images_route_observability(
            route="generations",
            model=None,
            stream=False,
            status=exc.status_code,
            outcome="auth_error" if isinstance(exc, ProxyAuthError) else "invalid_request",
            started_at=started_at,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=openai_error(exc.code, exc.message, error_type=exc.error_type),
    )
