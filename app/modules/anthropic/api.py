from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth.dependencies import (
    set_anthropic_error_format,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.clients.anthropic_api_proxy import get_recent_diagnostics
from app.core.clients.anthropic_proxy import AnthropicProxyError, anthropic_error_payload
from app.core.config.settings_cache import get_settings_cache
from app.core.types import JsonValue
from app.db.session import get_background_session
from app.dependencies import AnthropicContext, get_anthropic_context
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeysService,
    ApiKeyUsageReservationData,
)

router = APIRouter(prefix="/claude/v1", tags=["anthropic"], dependencies=[Depends(set_anthropic_error_format)])
api_router = APIRouter(prefix="/claude-sdk/v1", tags=["anthropic"], dependencies=[Depends(set_anthropic_error_format)])
diagnostics_router = APIRouter(
    prefix="/api/anthropic",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)

_bearer = HTTPBearer(description="API key (e.g. sk-clb-...)", auto_error=False)


async def validate_anthropic_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> ApiKeyData | None:
    settings = await get_settings_cache().get()
    if not settings.api_key_auth_enabled:
        return None

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail=anthropic_error_payload("authentication_error", "Missing API key in Authorization header"),
        )

    token = credentials.credentials
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        try:
            return await service.validate_key(token)
        except ApiKeyInvalidError as exc:
            raise HTTPException(
                status_code=401,
                detail=anthropic_error_payload("authentication_error", str(exc)),
            ) from exc


@router.post("/messages")
async def messages(
    request: Request,
    context: AnthropicContext = Depends(get_anthropic_context),
    api_key: ApiKeyData | None = Security(validate_anthropic_api_key),
):
    return await _messages_impl(request, context, api_key, transport="api")


@api_router.post("/messages")
async def messages_api(
    request: Request,
    context: AnthropicContext = Depends(get_anthropic_context),
    api_key: ApiKeyData | None = Security(validate_anthropic_api_key),
):
    return await _messages_impl(request, context, api_key, transport="sdk")


async def _messages_impl(
    request: Request,
    context: AnthropicContext,
    api_key: ApiKeyData | None,
    *,
    transport: Literal["sdk", "api"],
):
    payload = await _require_json_object(request)
    model = _extract_model(payload)
    _validate_model_access(api_key, model)
    reservation = await _enforce_request_limits(api_key, request_model=model)
    stream = bool(payload.get("stream"))

    if stream:
        upstream_stream = context.service.stream_messages(
            payload,
            request.headers,
            api_key=api_key,
            api_key_reservation=reservation,
            transport=transport,
        )
        try:
            first = await upstream_stream.__anext__()
        except StopAsyncIteration:
            return StreamingResponse(
                _prepend_first(None, upstream_stream),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )
        except AnthropicProxyError as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.payload)

        return StreamingResponse(
            _prepend_first(first, upstream_stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    try:
        response_payload = await context.service.create_message(
            payload,
            request.headers,
            api_key=api_key,
            api_key_reservation=reservation,
            transport=transport,
        )
        return JSONResponse(content=response_payload)
    except AnthropicProxyError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.payload)


async def _enforce_request_limits(
    api_key: ApiKeyData | None,
    *,
    request_model: str | None,
) -> ApiKeyUsageReservationData | None:
    if api_key is None:
        return None

    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        try:
            return await service.enforce_limits_for_request(api_key.id, request_model=request_model)
        except ApiKeyRateLimitExceededError as exc:
            message = f"{exc}. Usage resets at {exc.reset_at.isoformat()}Z."
            raise HTTPException(
                status_code=429,
                detail=anthropic_error_payload("rate_limit_error", message),
            ) from exc
        except ApiKeyInvalidError as exc:
            raise HTTPException(
                status_code=401,
                detail=anthropic_error_payload("authentication_error", str(exc)),
            ) from exc


def _validate_model_access(api_key: ApiKeyData | None, model: str | None) -> None:
    if api_key is None:
        return
    allowed_models = api_key.allowed_models
    if not allowed_models:
        return
    if model is None or model in allowed_models:
        return
    message = f"This API key does not have access to model '{model}'"
    raise HTTPException(
        status_code=403,
        detail=anthropic_error_payload("permission_error", message),
    )


async def _prepend_first(first: str | None, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    if first is not None:
        yield first
    async for line in stream:
        yield line


async def _require_json_object(request: Request) -> dict[str, JsonValue]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=anthropic_error_payload("invalid_request_error", "Invalid JSON body"),
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail=anthropic_error_payload("invalid_request_error", "Request body must be an object"),
        )
    return payload


def _extract_model(payload: dict[str, JsonValue]) -> str | None:
    model = payload.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


@diagnostics_router.get("/diagnostics")
async def list_anthropic_diagnostics(
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, list[dict[str, object]]]:
    return {"entries": get_recent_diagnostics(limit)}
