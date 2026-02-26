from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from hashlib import sha256

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, Security
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.auth.dependencies import set_anthropic_error_format, validate_anthropic_api_key
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import get_settings
from app.core.openai.model_registry import get_model_registry
from app.core.openai.requests import ResponsesReasoning, ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id
from app.db.session import get_background_session
from app.dependencies import AnthropicCompatContext, get_anthropic_compat_context
from app.modules.anthropic_compat.schemas import (
    AnthropicCountTokensRequest,
    AnthropicCountTokensResponse,
    AnthropicErrorEnvelope,
    AnthropicEventLoggingResponse,
    AnthropicMessageResponse,
    AnthropicMessagesRequest,
)
from app.modules.anthropic_compat.translator import (
    AnthropicTranslationError,
    PromptCacheKeyResolution,
    anthropic_error_from_openai_payload,
    collect_anthropic_response_from_openai_stream,
    stream_anthropic_events_from_openai_stream,
    to_responses_request_with_cache_resolution,
)
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeysService,
    ApiKeyUsageReservationData,
)

logger = logging.getLogger(__name__)

_CLAUDE_CODE_FORCED_MODEL = "gpt-5.3-codex"
_CLAUDE_CODE_FORCED_REASONING_EFFORT = "xhigh"
_CLAUDE_CODE_STRIPPED_SAMPLING_FIELDS: tuple[str, ...] = ("temperature", "top_p", "top_k")
_CLAUDE_SHARED_PROMPT_CACHE_KEY_PREFIX = "claude-shared"
_CLAUDE_SHARED_PROMPT_CACHE_KEY_VERSION = 1
_CLAUDE_FALLBACK_MODEL_PREFERENCES: tuple[str, ...] = (
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.1",
    "gpt-5",
    "gpt-4.1",
)

router = APIRouter(tags=["anthropic_compat"], dependencies=[Depends(set_anthropic_error_format)])
anthropic_router = APIRouter(
    prefix="/anthropic",
    tags=["anthropic_compat"],
    dependencies=[Depends(set_anthropic_error_format)],
)


@router.post(
    "/v1/messages",
    response_model=AnthropicMessageResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def messages(
    request: Request,
    payload: AnthropicMessagesRequest = Body(...),
    context: AnthropicCompatContext = Depends(get_anthropic_compat_context),
    api_key: ApiKeyData | None = Security(validate_anthropic_api_key),
) -> Response:
    return await _messages_impl(request, payload, context, api_key)


@anthropic_router.post(
    "/v1/messages",
    response_model=AnthropicMessageResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def anthropic_messages(
    request: Request,
    payload: AnthropicMessagesRequest = Body(...),
    context: AnthropicCompatContext = Depends(get_anthropic_compat_context),
    api_key: ApiKeyData | None = Security(validate_anthropic_api_key),
) -> Response:
    return await _messages_impl(request, payload, context, api_key)


@router.post("/v1/messages/count_tokens", response_model=AnthropicCountTokensResponse)
async def count_tokens(
    request: Request,
    payload: AnthropicCountTokensRequest = Body(...),
    context: AnthropicCompatContext = Depends(get_anthropic_compat_context),
    api_key: ApiKeyData | None = Security(validate_anthropic_api_key),
) -> Response:
    return await _count_tokens_impl(request, payload, context, api_key)


@anthropic_router.post("/v1/messages/count_tokens", response_model=AnthropicCountTokensResponse)
async def anthropic_count_tokens(
    request: Request,
    payload: AnthropicCountTokensRequest = Body(...),
    context: AnthropicCompatContext = Depends(get_anthropic_compat_context),
    api_key: ApiKeyData | None = Security(validate_anthropic_api_key),
) -> Response:
    return await _count_tokens_impl(request, payload, context, api_key)


@router.post("/api/event_logging/batch", response_model=AnthropicEventLoggingResponse)
async def event_logging_batch() -> AnthropicEventLoggingResponse:
    return AnthropicEventLoggingResponse(status="ok")


@anthropic_router.post("/api/event_logging/batch", response_model=AnthropicEventLoggingResponse)
async def anthropic_event_logging_batch() -> AnthropicEventLoggingResponse:
    return AnthropicEventLoggingResponse(status="ok")


async def _messages_impl(
    request: Request,
    payload: AnthropicMessagesRequest,
    context: AnthropicCompatContext,
    api_key: ApiKeyData | None,
) -> Response:
    try:
        responses_payload, cache_resolution = to_responses_request_with_cache_resolution(payload)
    except AnthropicTranslationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    responses_payload = _apply_claude_code_overrides(payload.model, responses_payload, api_key)
    responses_payload, cache_resolution = _apply_claude_prompt_cache_policy(
        payload.model,
        payload,
        responses_payload,
        cache_resolution,
        api_key,
    )
    _log_prompt_cache_resolution(
        request=request,
        operation="messages",
        requested_model=payload.model,
        translated_payload=responses_payload,
        cache_resolution=cache_resolution,
    )

    _validate_model_access(api_key, payload.model)
    reservation = await _enforce_request_limits(api_key, request_model=payload.model)
    rate_limit_headers = await context.service.rate_limit_headers()

    if payload.stream:
        stream = context.service.stream_responses(
            responses_payload,
            request.headers,
            api_key=api_key,
            api_key_reservation=reservation,
        )
        try:
            first = await stream.__anext__()
        except StopAsyncIteration:
            first = None
        except ProxyResponseError as exc:
            await _release_reservation(reservation)
            error = anthropic_error_from_openai_payload(
                exc.payload,
                fallback_message="Upstream error",
                status_code=exc.status_code,
            )
            return JSONResponse(
                status_code=exc.status_code,
                content=error.model_dump(mode="json", exclude_none=True),
                headers=rate_limit_headers,
            )

        stream_with_first = _prepend_first(first, stream)
        return StreamingResponse(
            stream_anthropic_events_from_openai_stream(stream_with_first, model=payload.model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    stream = context.service.stream_responses(
        responses_payload,
        request.headers,
        api_key=api_key,
        api_key_reservation=reservation,
    )
    try:
        response = await collect_anthropic_response_from_openai_stream(stream, model=payload.model)
    except ProxyResponseError as exc:
        await _release_reservation(reservation)
        error = anthropic_error_from_openai_payload(
            exc.payload,
            fallback_message="Upstream error",
            status_code=exc.status_code,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )

    if isinstance(response, AnthropicErrorEnvelope):
        status_code = _status_for_anthropic_error_type(response.error.type)
        return JSONResponse(
            status_code=status_code,
            content=response.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )

    return JSONResponse(
        content=response.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )


async def _count_tokens_impl(
    request: Request,
    payload: AnthropicCountTokensRequest,
    context: AnthropicCompatContext,
    api_key: ApiKeyData | None,
) -> Response:
    try:
        responses_payload, cache_resolution = to_responses_request_with_cache_resolution(payload)
    except AnthropicTranslationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    responses_payload = _apply_claude_code_overrides(payload.model, responses_payload, api_key)
    responses_payload, cache_resolution = _apply_claude_prompt_cache_policy(
        payload.model,
        payload,
        responses_payload,
        cache_resolution,
        api_key,
    )
    responses_payload, cache_resolution = _apply_claude_count_tokens_cache_lane(
        payload.model,
        responses_payload,
        cache_resolution,
    )
    _log_prompt_cache_resolution(
        request=request,
        operation="count_tokens",
        requested_model=payload.model,
        translated_payload=responses_payload,
        cache_resolution=cache_resolution,
    )

    _validate_model_access(api_key, payload.model)
    rate_limit_headers = await context.service.rate_limit_headers()
    input_tokens = _estimate_input_tokens(responses_payload)
    response = AnthropicCountTokensResponse(input_tokens=input_tokens)
    return JSONResponse(content=response.model_dump(mode="json", exclude_none=True), headers=rate_limit_headers)


async def _prepend_first(first: str | None, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    if first is not None:
        yield first
    async for line in stream:
        yield line


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
            return await service.enforce_limits_for_request(
                api_key.id,
                request_model=request_model,
            )
        except ApiKeyRateLimitExceededError as exc:
            message = f"{exc}. Usage resets at {exc.reset_at.isoformat()}Z."
            raise HTTPException(status_code=429, detail=message) from exc
        except ApiKeyInvalidError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc


async def _release_reservation(reservation: ApiKeyUsageReservationData | None) -> None:
    if reservation is None:
        return

    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        await service.release_usage_reservation(reservation.reservation_id)


def _validate_model_access(api_key: ApiKeyData | None, model: str | None) -> None:
    if api_key is None:
        return

    allowed_models = api_key.allowed_models
    if not allowed_models:
        return

    if model is None or model in allowed_models:
        return

    raise HTTPException(status_code=403, detail=f"This API key does not have access to model '{model}'")


def _resolve_upstream_model(requested_model: str, api_key: ApiKeyData | None) -> str:
    normalized_model = requested_model.strip()
    if not normalized_model:
        return requested_model
    if not normalized_model.lower().startswith("claude-"):
        return normalized_model

    candidates = _candidate_models_for_api_key(api_key)
    if not candidates:
        return normalized_model
    if normalized_model in candidates:
        return normalized_model

    for preferred in _CLAUDE_FALLBACK_MODEL_PREFERENCES:
        if preferred in candidates:
            logger.info("Anthropic model remapped from '%s' to '%s'", normalized_model, preferred)
            return preferred

    fallback = candidates[0]
    logger.info("Anthropic model remapped from '%s' to '%s'", normalized_model, fallback)
    return fallback


def _apply_claude_code_overrides(
    requested_model: str,
    responses_payload: ResponsesRequest,
    api_key: ApiKeyData | None,
) -> ResponsesRequest:
    normalized_model = requested_model.strip().lower()
    if not normalized_model.startswith("claude-"):
        resolved_model = _resolve_upstream_model(requested_model, api_key)
        if resolved_model == responses_payload.model:
            return responses_payload
        return responses_payload.model_copy(update={"model": resolved_model})

    forced = responses_payload.model_copy(
        update={
            "model": _CLAUDE_CODE_FORCED_MODEL,
            "reasoning": ResponsesReasoning(effort=_CLAUDE_CODE_FORCED_REASONING_EFFORT),
        }
    )
    forced = _strip_sampling_fields(forced, _CLAUDE_CODE_STRIPPED_SAMPLING_FIELDS)
    forced = _promote_instructions_to_input_prefix(forced)
    logger.info(
        "Anthropic Claude request forced to model '%s' with reasoning effort '%s'",
        _CLAUDE_CODE_FORCED_MODEL,
        _CLAUDE_CODE_FORCED_REASONING_EFFORT,
    )
    return forced


def _apply_claude_prompt_cache_policy(
    requested_model: str,
    payload: AnthropicMessagesRequest,
    responses_payload: ResponsesRequest,
    cache_resolution: PromptCacheKeyResolution,
    api_key: ApiKeyData | None,
) -> tuple[ResponsesRequest, PromptCacheKeyResolution]:
    normalized_model = requested_model.strip().lower()
    if not normalized_model.startswith("claude-"):
        return responses_payload, cache_resolution

    # Preserve only explicit caller-provided keys. All other sources are
    # normalized into the shared Claude lane to keep cache routing stable.
    if cache_resolution.source == "explicit" and responses_payload.prompt_cache_key:
        return responses_payload, cache_resolution

    shared_key = _build_claude_shared_prompt_cache_key(payload, api_key)
    if responses_payload.prompt_cache_key == shared_key and cache_resolution.source == "claude_shared":
        return responses_payload, cache_resolution

    updated_payload = responses_payload.model_copy(update={"prompt_cache_key": shared_key})
    return updated_payload, PromptCacheKeyResolution(key=shared_key, source="claude_shared")


def _build_claude_shared_prompt_cache_key(
    payload: AnthropicMessagesRequest,
    api_key: ApiKeyData | None,
) -> str:
    identity = {
        "scope": f"anthropic_claude_shared_v{_CLAUDE_SHARED_PROMPT_CACHE_KEY_VERSION}",
        "api_key_id": api_key.id if api_key else None,
        "requested_model": payload.model.strip().lower(),
        "system": _normalize_system_text(payload),
        "tools": _normalize_tool_signature(payload),
    }
    canonical = json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_CLAUDE_SHARED_PROMPT_CACHE_KEY_PREFIX}:{digest}"


def _apply_claude_count_tokens_cache_lane(
    requested_model: str,
    responses_payload: ResponsesRequest,
    cache_resolution: PromptCacheKeyResolution,
) -> tuple[ResponsesRequest, PromptCacheKeyResolution]:
    normalized_model = requested_model.strip().lower()
    if not normalized_model.startswith("claude-"):
        return responses_payload, cache_resolution
    prompt_cache_key = responses_payload.prompt_cache_key
    if not prompt_cache_key:
        return responses_payload, cache_resolution
    lane_key = f"{prompt_cache_key}:count_tokens"
    updated_payload = responses_payload.model_copy(update={"prompt_cache_key": lane_key})
    return updated_payload, PromptCacheKeyResolution(key=lane_key, source=cache_resolution.source)


def _estimate_input_tokens(payload: ResponsesRequest) -> int:
    request_shape = {
        "instructions": payload.instructions,
        "input": payload.input,
        "tools": payload.tools,
    }
    serialized = json.dumps(request_shape, ensure_ascii=False, separators=(",", ":"))
    # Fast local heuristic to avoid an extra upstream call.
    return max(1, (len(serialized) + 3) // 4)


def _normalize_system_text(payload: AnthropicMessagesRequest) -> str:
    system = payload.system
    if system is None:
        return ""
    if isinstance(system, str):
        return system.strip()

    parts: list[str] = []
    for block in system:
        text = block.text.strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _normalize_tool_signature(payload: AnthropicMessagesRequest) -> list[dict[str, JsonValue]]:
    tools: list[dict[str, JsonValue]] = []
    for tool in payload.tools:
        tools.append(
            {
                "name": tool.name.strip(),
                "description": tool.description,
                "input_schema": tool.input_schema or {},
            }
        )
    return sorted(tools, key=lambda tool: str(tool["name"]))


def _candidate_models_for_api_key(api_key: ApiKeyData | None) -> list[str]:
    if api_key and api_key.allowed_models:
        return _dedupe_preserve_order(api_key.allowed_models)

    snapshot = get_model_registry().get_snapshot()
    if snapshot is None:
        return []
    return sorted(snapshot.models.keys())


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _strip_sampling_fields(payload: ResponsesRequest, fields: tuple[str, ...]) -> ResponsesRequest:
    raw = payload.model_dump(mode="python", exclude_none=False)
    for field in fields:
        raw.pop(field, None)
    return ResponsesRequest.model_validate(raw)


def _promote_instructions_to_input_prefix(payload: ResponsesRequest) -> ResponsesRequest:
    instructions = payload.instructions.strip()
    if not instructions:
        return payload

    existing_input = payload.input
    input_items: list[JsonValue]
    if isinstance(existing_input, list):
        input_items = list(existing_input)
    else:
        input_items = [existing_input]

    prefix_message: dict[str, JsonValue] = {
        "role": "developer",
        "content": [{"type": "input_text", "text": instructions}],
    }
    updated_input: list[JsonValue] = [prefix_message, *input_items]
    return payload.model_copy(update={"instructions": "", "input": updated_input})


def _status_for_anthropic_error_type(error_type: str) -> int:
    if error_type == "authentication_error":
        return 401
    if error_type == "permission_error":
        return 403
    if error_type == "rate_limit_error":
        return 429
    if error_type == "invalid_request_error":
        return 400
    return 502


def _log_prompt_cache_resolution(
    *,
    request: Request,
    operation: str,
    requested_model: str,
    translated_payload: ResponsesRequest,
    cache_resolution: PromptCacheKeyResolution,
) -> None:
    prompt_cache_key = cache_resolution.key
    prompt_cache_key_hash = _hash_identifier(prompt_cache_key) if prompt_cache_key else None
    settings = get_settings()
    prompt_cache_key_raw = (
        _truncate_identifier(prompt_cache_key)
        if settings.log_proxy_request_shape_raw_cache_key and prompt_cache_key
        else None
    )
    logger.info(
        "anthropic_prompt_cache request_id=%s operation=%s method=%s path=%s requested_model=%s upstream_model=%s "
        "stream=%s source=%s prompt_cache_key=%s prompt_cache_key_raw=%s",
        get_request_id(),
        operation,
        request.method,
        request.url.path,
        requested_model,
        translated_payload.model,
        translated_payload.stream,
        cache_resolution.source,
        prompt_cache_key_hash,
        prompt_cache_key_raw,
    )


def _hash_identifier(value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _truncate_identifier(value: str, *, max_length: int = 96) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:48]}...{value[-16:]}"
