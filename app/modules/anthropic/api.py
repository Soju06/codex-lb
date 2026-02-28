from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth.dependencies import set_anthropic_error_format
from app.core.clients.anthropic_proxy import AnthropicProxyError, anthropic_error_payload
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings_cache import get_settings_cache
from app.core.openai.chat_responses import collect_chat_completion
from app.core.openai.models import OpenAIErrorEnvelope
from app.core.types import JsonValue
from app.db.session import get_background_session
from app.dependencies import AnthropicContext, ProxyContext, get_anthropic_context, get_proxy_context
from app.modules.anthropic.codex_compat import (
    AnthropicCodexCompatError,
    chat_completion_to_anthropic_message,
    openai_error_to_anthropic_error,
    payload_to_responses_request,
    proxy_payload_to_anthropic_error,
    resolve_target_model,
    stream_message_as_anthropic_events,
)
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
desktop_router = APIRouter(tags=["anthropic-desktop"])

_bearer = HTTPBearer(description="API key (e.g. sk-clb-...)", auto_error=False)


@desktop_router.get("/api/bootstrap")
async def claude_desktop_bootstrap() -> dict[str, JsonValue]:
    return {
        "account": {
            "uuid": "anthropic_default",
            "email": "anthropic@local",
        },
        "organization": {
            "uuid": "codex-lb",
            "name": "codex-lb",
        },
    }


@desktop_router.get("/api/desktop/features")
async def claude_desktop_features() -> dict[str, JsonValue]:
    # Claude Desktop treats missing features as disabled.
    return {"features": {}}


@desktop_router.post("/api/event_logging/batch")
async def claude_desktop_event_logging_batch() -> dict[str, JsonValue]:
    # Ack event batches to keep desktop clients from retry-spamming on startup.
    return {"ok": True}


async def validate_anthropic_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> ApiKeyData | None:
    settings = await get_settings_cache().get()
    if not settings.api_key_auth_enabled:
        return None

    token: str | None = None
    if credentials is not None and credentials.credentials:
        token = credentials.credentials
    else:
        x_api_key = request.headers.get("x-api-key")
        if isinstance(x_api_key, str) and x_api_key.strip():
            token = x_api_key.strip()

    if token is None:
        raise HTTPException(
            status_code=401,
            detail=anthropic_error_payload(
                "authentication_error",
                "Missing API key in Authorization or x-api-key header",
            ),
        )

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
    proxy_context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_anthropic_api_key),
):
    return await _messages_impl(request, context, api_key, proxy_context=proxy_context, transport="codex")


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
    proxy_context: ProxyContext | None = None,
    transport: str,
):
    payload = await _require_json_object(request)
    model = _extract_model(payload)

    if transport == "codex":
        if proxy_context is None:
            return JSONResponse(
                status_code=500,
                content=anthropic_error_payload("api_error", "Proxy context unavailable"),
            )
        return await _messages_codex_impl(
            request,
            payload,
            model,
            proxy_context,
            api_key,
        )

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


async def _messages_codex_impl(
    request: Request,
    payload: dict[str, JsonValue],
    requested_model: str | None,
    proxy_context: ProxyContext,
    api_key: ApiKeyData | None,
):
    target_model = resolve_target_model(requested_model)
    _validate_model_access(api_key, target_model)
    reservation = await _enforce_request_limits(api_key, request_model=target_model)

    try:
        responses_payload, stream_requested, normalized_requested_model = payload_to_responses_request(
            payload,
            target_model=target_model,
        )
    except AnthropicCodexCompatError as exc:
        return JSONResponse(
            status_code=400,
            content=anthropic_error_payload("invalid_request_error", str(exc)),
        )

    try:
        stream = proxy_context.service.stream_responses(
            responses_payload,
            request.headers,
            propagate_http_errors=True,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=True,
        )
        result = await collect_chat_completion(stream, model=target_model)
    except ProxyResponseError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=proxy_payload_to_anthropic_error(exc.payload, exc.status_code),
        )

    if isinstance(result, OpenAIErrorEnvelope):
        status_code, error_payload = openai_error_to_anthropic_error(result)
        return JSONResponse(status_code=status_code, content=error_payload)

    message_payload = chat_completion_to_anthropic_message(
        result,
        requested_model=normalized_requested_model,
        target_model=target_model,
    )
    if stream_requested:
        return StreamingResponse(
            stream_message_as_anthropic_events(message_payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    return JSONResponse(content=message_payload)


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
