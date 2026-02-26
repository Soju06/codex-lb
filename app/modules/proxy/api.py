from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator

from aiohttp import ClientError, ClientTimeout

from fastapi import APIRouter, Body, Depends, Request, Response, Security
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.core.auth.dependencies import (
    set_openai_error_format,
    validate_codex_usage_identity,
    validate_proxy_api_key,
)
from app.core.clients.http import get_http_client
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.exceptions import ProxyAuthError, ProxyModelNotAllowed, ProxyRateLimitError
from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.chat_responses import ChatCompletionResult, collect_chat_completion, stream_chat_chunks
from app.core.openai.embeddings import EmbeddingsRequest, EmbeddingsResponse
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.model_registry import UpstreamModel, get_model_registry, is_public_model
from app.core.openai.models import (
    OpenAIError,
    OpenAIResponsePayload,
    OpenAIResponseResult,
)
from app.core.openai.models import (
    OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel,
)
from app.core.openai.parsing import parse_response_payload
from app.core.openai.requests import ResponsesCompactRequest, ResponsesReasoning, ResponsesRequest
from app.core.openai.v1_requests import V1ResponsesCompactRequest, V1ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.sse import parse_sse_data_json
from app.db.session import get_background_session
from app.dependencies import ProxyContext, get_proxy_context
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeysService,
    ApiKeyUsageReservationData,
)
from app.modules.model_overrides.service import RequestActorContext
from app.modules.proxy.schemas import (
    ModelListItem,
    ModelListResponse,
    ModelMetadata,
    RateLimitStatusPayload,
    ReasoningLevelSchema,
)
from app.modules.proxy.service import RequestActorLogData

router = APIRouter(
    prefix="/backend-api/codex",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
v1_router = APIRouter(
    prefix="/v1",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
usage_router = APIRouter(
    tags=["proxy"],
    dependencies=[Depends(validate_codex_usage_identity), Depends(set_openai_error_format)],
)


@router.post(
    "/responses",
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
async def responses(
    request: Request,
    payload: ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _stream_responses(request, payload, context, api_key)


@v1_router.post(
    "/responses",
    response_model=OpenAIResponseResult,
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
async def v1_responses(
    request: Request,
    payload: V1ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    try:
        responses_payload = payload.to_responses_request()
    except ClientPayloadError as exc:
        error = _openai_invalid_payload_error(exc.param)
        return JSONResponse(status_code=400, content=error)
    except ValidationError as exc:
        error = _openai_validation_error(exc)
        return JSONResponse(status_code=400, content=error)
    if responses_payload.stream:
        return await _stream_responses(request, responses_payload, context, api_key)
    return await _collect_responses(request, responses_payload, context, api_key)


@router.get("/models", response_model=ModelListResponse)
async def models(
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _build_models_response(api_key)


@v1_router.get("/models", response_model=ModelListResponse)
async def v1_models(
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _build_models_response(api_key)


@v1_router.post("/embeddings", response_model=EmbeddingsResponse)
async def v1_embeddings(
    request: Request,
    payload: EmbeddingsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    override_actor = _build_actor_log(
        request=request,
        api_key=api_key,
        requested_model=payload.model,
        override_id=None,
    )
    await _apply_model_routing(
        context=context,
        actor_log=override_actor,
        payload=payload,
        apply_reasoning=False,
    )

    _validate_model_access(api_key, payload.model)

    rate_limit_headers = await context.service.rate_limit_headers()
    reservation = await _enforce_request_limits(api_key, request_model=payload.model)
    try:
        status_code, result = await _proxy_embeddings(payload)
    except Exception:
        await _release_reservation(reservation)
        return JSONResponse(
            status_code=502,
            content=openai_error("upstream_error", "Embeddings upstream request failed"),
            headers=rate_limit_headers,
        )

    if status_code >= 400:
        await _release_reservation(reservation)
        return JSONResponse(status_code=status_code, content=result, headers=rate_limit_headers)

    prompt_tokens = _extract_embeddings_prompt_tokens(result)
    await _finalize_embeddings_reservation(
        api_key=api_key,
        reservation=reservation,
        model=payload.model,
        prompt_tokens=prompt_tokens,
    )

    try:
        validated = EmbeddingsResponse.model_validate(result)
        content = validated.model_dump(mode="json", exclude_none=True)
    except ValidationError:
        content = result

    return JSONResponse(status_code=200, content=content, headers=rate_limit_headers)


async def _build_models_response(api_key: ApiKeyData | None) -> Response:
    reservation = await _enforce_request_limits(api_key, request_model=None)

    allowed_models = set(api_key.allowed_models) if api_key and api_key.allowed_models else None
    created = int(time.time())

    registry = get_model_registry()
    snapshot = registry.get_snapshot()

    if snapshot is None:
        await _release_reservation(reservation)
        return JSONResponse(content=ModelListResponse(data=[]).model_dump(mode="json"))

    items: list[ModelListItem] = []
    for slug, model in snapshot.models.items():
        if not is_public_model(model, allowed_models):
            continue
        items.append(
            ModelListItem(
                id=slug,
                created=created,
                owned_by="codex-lb",
                metadata=_to_model_metadata(model),
            )
        )
    await _release_reservation(reservation)
    return JSONResponse(content=ModelListResponse(data=items).model_dump(mode="json"))


def _to_model_metadata(model: UpstreamModel) -> ModelMetadata:
    return ModelMetadata(
        display_name=model.display_name,
        description=model.description,
        context_window=model.context_window,
        input_modalities=list(model.input_modalities),
        supported_reasoning_levels=[
            ReasoningLevelSchema(effort=rl.effort, description=rl.description)
            for rl in model.supported_reasoning_levels
        ],
        default_reasoning_level=model.default_reasoning_level,
        supports_reasoning_summaries=model.supports_reasoning_summaries,
        support_verbosity=model.support_verbosity,
        default_verbosity=model.default_verbosity,
        prefer_websockets=model.prefer_websockets,
        supports_parallel_tool_calls=model.supports_parallel_tool_calls,
        supported_in_api=model.supported_in_api,
        minimal_client_version=model.minimal_client_version,
        priority=model.priority,
    )


@v1_router.post(
    "/chat/completions",
    response_model=ChatCompletionResult,
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
async def v1_chat_completions(
    request: Request,
    payload: ChatCompletionsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    try:
        responses_payload = payload.to_responses_request()
    except ClientPayloadError as exc:
        error = _openai_invalid_payload_error(exc.param)
        return JSONResponse(status_code=400, content=error)
    except ValidationError as exc:
        error = _openai_validation_error(exc)
        return JSONResponse(status_code=400, content=error)

    actor_log = _build_actor_log(
        request=request,
        api_key=api_key,
        requested_model=responses_payload.model,
        override_id=None,
    )
    actor_log = await _apply_model_routing(
        context=context,
        actor_log=actor_log,
        payload=responses_payload,
        apply_reasoning=True,
    )

    _validate_model_access(api_key, responses_payload.model)

    rate_limit_headers = await context.service.rate_limit_headers()
    reservation = await _enforce_request_limits(api_key, request_model=responses_payload.model)
    responses_payload.stream = True
    stream = context.service.stream_responses(
        responses_payload,
        request.headers,
        propagate_http_errors=True,
        api_key=api_key,
        api_key_reservation=reservation,
        suppress_text_done_events=True,
        actor_log=actor_log,
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
            stream_chat_chunks(stream_with_first, model=responses_payload.model, include_usage=include_usage),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    result = await collect_chat_completion(stream_with_first, model=responses_payload.model)
    if isinstance(result, OpenAIErrorEnvelopeModel):
        error = result.error
        code = error.code if error else None
        status_code = 503 if code == "no_accounts" else 502
        return JSONResponse(
            content=result.model_dump(mode="json", exclude_none=True),
            status_code=status_code,
            headers=rate_limit_headers,
        )
    return JSONResponse(
        content=result.model_dump(mode="json", exclude_none=True),
        status_code=200,
        headers=rate_limit_headers,
    )


async def _stream_responses(
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    suppress_text_done_events: bool = False,
) -> Response:
    actor_log = _build_actor_log(
        request=request,
        api_key=api_key,
        requested_model=payload.model,
        override_id=None,
    )
    actor_log = await _apply_model_routing(
        context=context,
        actor_log=actor_log,
        payload=payload,
        apply_reasoning=True,
    )

    _validate_model_access(api_key, payload.model)
    reservation = await _enforce_request_limits(api_key, request_model=payload.model)

    rate_limit_headers = await context.service.rate_limit_headers()
    payload.stream = True
    stream = context.service.stream_responses(
        payload,
        request.headers,
        propagate_http_errors=True,
        api_key=api_key,
        api_key_reservation=reservation,
        suppress_text_done_events=suppress_text_done_events,
        actor_log=actor_log,
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
        await _release_reservation(reservation)
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
    api_key: ApiKeyData | None,
    *,
    suppress_text_done_events: bool = False,
) -> Response:
    actor_log = _build_actor_log(
        request=request,
        api_key=api_key,
        requested_model=payload.model,
        override_id=None,
    )
    actor_log = await _apply_model_routing(
        context=context,
        actor_log=actor_log,
        payload=payload,
        apply_reasoning=True,
    )

    _validate_model_access(api_key, payload.model)
    reservation = await _enforce_request_limits(api_key, request_model=payload.model)

    rate_limit_headers = await context.service.rate_limit_headers()
    payload.stream = True
    stream = context.service.stream_responses(
        payload,
        request.headers,
        propagate_http_errors=True,
        api_key=api_key,
        api_key_reservation=reservation,
        suppress_text_done_events=suppress_text_done_events,
        actor_log=actor_log,
    )
    try:
        response_payload = await _collect_responses_payload(stream)
    except ProxyResponseError as exc:
        await _release_reservation(reservation)
        error = _parse_error_envelope(exc.payload)
        return JSONResponse(
            status_code=exc.status_code,
            content=error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    if isinstance(response_payload, OpenAIResponsePayload):
        if response_payload.status == "failed":
            error_payload = _error_envelope_from_response(response_payload.error)
            status_code = _status_for_error(error_payload.error)
            return JSONResponse(
                status_code=status_code,
                content=error_payload.model_dump(mode="json", exclude_none=True),
                headers=rate_limit_headers,
            )
        return JSONResponse(
            content=response_payload.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    status_code = _status_for_error(response_payload.error)
    return JSONResponse(
        status_code=status_code,
        content=response_payload.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )


@router.post("/responses/compact", response_model=OpenAIResponseResult)
async def responses_compact(
    request: Request,
    payload: ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    return await _compact_responses(request, payload, context, api_key)


@v1_router.post("/responses/compact", response_model=OpenAIResponseResult)
async def v1_responses_compact(
    request: Request,
    payload: V1ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    try:
        compact_payload = payload.to_compact_request()
    except ClientPayloadError as exc:
        error = _openai_invalid_payload_error(exc.param)
        return JSONResponse(status_code=400, content=error)
    except ValidationError as exc:
        error = _openai_validation_error(exc)
        return JSONResponse(status_code=400, content=error)
    return await _compact_responses(request, compact_payload, context, api_key)


async def _compact_responses(
    request: Request,
    payload: ResponsesCompactRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> JSONResponse:
    override_actor = _build_actor_log(
        request=request,
        api_key=api_key,
        requested_model=payload.model,
        override_id=None,
    )
    await _apply_model_routing(
        context=context,
        actor_log=override_actor,
        payload=payload,
        apply_reasoning=False,
    )

    _validate_model_access(api_key, payload.model)
    reservation = await _enforce_request_limits(api_key, request_model=payload.model)

    rate_limit_headers = await context.service.rate_limit_headers()
    try:
        result = await context.service.compact_responses(
            payload,
            request.headers,
            api_key=api_key,
            api_key_reservation=reservation,
        )
    except NotImplementedError:
        error = OpenAIErrorEnvelopeModel(
            error=OpenAIError(
                message="responses/compact is not implemented",
                type="server_error",
                code="not_implemented",
            )
        )
        return JSONResponse(
            status_code=501,
            content=error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    except ProxyResponseError as exc:
        error = _parse_error_envelope(exc.payload)
        return JSONResponse(
            status_code=exc.status_code,
            content=error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    finally:
        await _release_reservation(reservation)
    return JSONResponse(
        content=result.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )


@usage_router.get("/api/codex/usage", response_model=RateLimitStatusPayload)
@usage_router.get("/api/codex/usage/", response_model=RateLimitStatusPayload, include_in_schema=False)
async def codex_usage(
    context: ProxyContext = Depends(get_proxy_context),
) -> RateLimitStatusPayload:
    payload = await context.service.get_rate_limit_payload()
    return RateLimitStatusPayload.from_data(payload)


async def _apply_model_routing(
    *,
    context: ProxyContext,
    actor_log: RequestActorLogData,
    payload: ResponsesRequest | ResponsesCompactRequest | EmbeddingsRequest,
    apply_reasoning: bool,
) -> RequestActorLogData:
    global_force = await _resolve_global_model_force()
    if global_force is not None:
        forced_model, forced_effort = global_force
        payload.model = forced_model
        if apply_reasoning and forced_effort is not None and isinstance(payload, ResponsesRequest):
            _apply_reasoning_effort(payload, forced_effort)
        return actor_log

    override = await context.service.resolve_model_override(_to_actor_context(actor_log))
    if override is None:
        return actor_log

    payload.model = override.forced_model
    if apply_reasoning and override.forced_reasoning_effort is not None and isinstance(payload, ResponsesRequest):
        _apply_reasoning_effort(payload, override.forced_reasoning_effort)
    return _with_override(actor_log, override.override_id)


async def _resolve_global_model_force() -> tuple[str, str | None] | None:
    settings = await get_settings_cache().get()
    if not bool(getattr(settings, "global_model_force_enabled", False)):
        return None

    forced_model = getattr(settings, "global_model_force_model", None)
    if not isinstance(forced_model, str) or not forced_model.strip():
        return None

    forced_effort = _normalize_forced_reasoning_effort(
        getattr(settings, "global_model_force_reasoning_effort", None)
    )
    return forced_model.strip(), forced_effort


def _normalize_forced_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized == "normal":
        return "medium"
    return normalized


def _apply_reasoning_effort(payload: ResponsesRequest, effort: str) -> None:
    if payload.reasoning is None:
        payload.reasoning = ResponsesReasoning(effort=effort)
        return
    payload.reasoning.effort = effort


def _with_override(actor_log: RequestActorLogData, override_id: int) -> RequestActorLogData:
    return RequestActorLogData(
        client_ip=actor_log.client_ip,
        client_app=actor_log.client_app,
        api_key=actor_log.api_key,
        requested_model=actor_log.requested_model,
        override_id=override_id,
    )


def _to_actor_context(actor_log: RequestActorLogData) -> RequestActorContext:
    return RequestActorContext(
        client_ip=actor_log.client_ip,
        client_app=actor_log.client_app,
        api_key_identifier=actor_log.api_key,
    )


def _build_actor_log(
    *,
    request: Request,
    api_key: ApiKeyData | None,
    requested_model: str,
    override_id: int | None,
) -> RequestActorLogData:
    return RequestActorLogData(
        client_ip=_resolve_client_ip(request),
        client_app=_resolve_client_app(request),
        api_key=_resolve_api_key_identifier(request, api_key),
        requested_model=requested_model,
        override_id=override_id,
    )


def _resolve_api_key_identifier(request: Request, api_key: ApiKeyData | None) -> str | None:
    if api_key is not None:
        return f"id:{api_key.id}".lower()
    token = _extract_bearer_token(request.headers.get("authorization"))
    if not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return f"hash:{digest}"


def _extract_bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if not raw.lower().startswith("bearer "):
        return None
    token = raw[7:].strip()
    if not token:
        return None
    return token


def _resolve_client_ip(request: Request) -> str | None:
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if not value:
            continue
        first = value.split(",", 1)[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return None


def _resolve_client_app(request: Request) -> str | None:
    for header in (
        "x-openclaw-app",
        "x-app-id",
        "x-client-app",
        "x-application-name",
        "x-openai-client-user-agent",
        "user-agent",
    ):
        value = request.headers.get(header)
        if not value:
            continue
        normalized = value.strip()
        if normalized:
            return normalized[:256]
    return None


async def _prepend_first(first: str | None, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    if first is not None:
        yield first
    async for line in stream:
        yield line


def _parse_sse_payload(line: str) -> dict[str, JsonValue] | None:
    return parse_sse_data_json(line)


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
            raise ProxyRateLimitError(message) from exc
        except ApiKeyInvalidError as exc:
            raise ProxyAuthError(str(exc)) from exc


async def _release_reservation(reservation: ApiKeyUsageReservationData | None) -> None:
    if reservation is None:
        return
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        await service.release_usage_reservation(reservation.reservation_id)


async def _proxy_embeddings(payload: EmbeddingsRequest) -> tuple[int, OpenAIErrorEnvelope | dict[str, JsonValue]]:
    settings = get_settings()
    if not settings.embeddings_enabled or not settings.embeddings_upstream_url:
        return (
            405,
            openai_error(
                "invalid_request_error",
                "Method Not Allowed",
                error_type="invalid_request_error",
            ),
        )

    request_payload = payload.to_upstream_payload(model_override=settings.embeddings_model_override)
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if settings.embeddings_upstream_api_key:
        headers["Authorization"] = f"Bearer {settings.embeddings_upstream_api_key}"

    try:
        async with get_http_client().session.post(
            settings.embeddings_upstream_url,
            headers=headers,
            json=request_payload,
            timeout=ClientTimeout(total=settings.embeddings_upstream_timeout_seconds),
        ) as resp:
            raw_text = await resp.text()
    except ClientError:
        return 502, openai_error("upstream_error", "Embeddings upstream request failed")

    try:
        parsed: JsonValue = json.loads(raw_text) if raw_text else {}
    except json.JSONDecodeError:
        parsed = openai_error(
            "upstream_error",
            "Embeddings upstream returned non-JSON response",
            error_type="server_error",
        )

    if not isinstance(parsed, dict):
        parsed = openai_error(
            "upstream_error",
            "Embeddings upstream returned invalid response",
            error_type="server_error",
        )

    if resp.status >= 400:
        if "error" not in parsed:
            parsed = openai_error(
                "upstream_error",
                f"Embeddings upstream error (HTTP {resp.status})",
                error_type="server_error",
            )
        return resp.status, parsed

    return 200, parsed


def _extract_embeddings_prompt_tokens(payload: dict[str, JsonValue] | OpenAIErrorEnvelope) -> int | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt_tokens = usage.get("prompt_tokens")
    if isinstance(prompt_tokens, int):
        return max(prompt_tokens, 0)
    if isinstance(prompt_tokens, float):
        return max(int(prompt_tokens), 0)
    return None


async def _finalize_embeddings_reservation(
    *,
    api_key: ApiKeyData | None,
    reservation: ApiKeyUsageReservationData | None,
    model: str,
    prompt_tokens: int | None,
) -> None:
    if reservation is None:
        return

    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        if api_key is None or prompt_tokens is None:
            await service.release_usage_reservation(reservation.reservation_id)
            return
        await service.finalize_usage_reservation(
            reservation.reservation_id,
            model=model,
            input_tokens=max(prompt_tokens, 0),
            output_tokens=0,
            cached_input_tokens=0,
        )


def _validate_model_access(api_key: ApiKeyData | None, model: str | None) -> None:
    if api_key is None:
        return
    allowed_models = api_key.allowed_models
    if not allowed_models:
        return
    if model is None or model in allowed_models:
        return
    raise ProxyModelNotAllowed(f"This API key does not have access to model '{model}'")


async def _collect_responses_payload(stream: AsyncIterator[str]) -> OpenAIResponseResult:
    async for line in stream:
        payload = _parse_sse_payload(line)
        if not payload:
            continue
        event_type = payload.get("type")
        if event_type == "error":
            return _parse_event_error_envelope(payload)
        if event_type == "response.failed":
            response = payload.get("response")
            if isinstance(response, dict):
                error_value = response.get("error")
                if isinstance(error_value, dict):
                    try:
                        return OpenAIErrorEnvelopeModel.model_validate({"error": error_value})
                    except ValidationError:
                        return _default_error_envelope()
                parsed = parse_response_payload(response)
                if parsed is not None and parsed.error is not None:
                    return _error_envelope_from_response(parsed.error)
            return _default_error_envelope()
        if event_type in ("response.completed", "response.incomplete"):
            response = payload.get("response")
            if isinstance(response, dict):
                parsed = parse_response_payload(response)
                if parsed is not None:
                    return parsed
            return _default_error_envelope()
    return _default_error_envelope()


def _parse_event_error_envelope(payload: dict[str, JsonValue]) -> OpenAIErrorEnvelopeModel:
    error_value = payload.get("error")
    if isinstance(error_value, dict):
        try:
            return OpenAIErrorEnvelopeModel.model_validate({"error": error_value})
        except ValidationError:
            return _default_error_envelope()
    return _default_error_envelope()


def _default_error_envelope() -> OpenAIErrorEnvelopeModel:
    return OpenAIErrorEnvelopeModel(
        error=OpenAIError(
            message="Upstream error",
            type="server_error",
            code="upstream_error",
        )
    )


def _parse_error_envelope(payload: JsonValue | OpenAIErrorEnvelope) -> OpenAIErrorEnvelopeModel:
    if not isinstance(payload, dict):
        return _default_error_envelope()
    try:
        return OpenAIErrorEnvelopeModel.model_validate(payload)
    except ValidationError:
        return _default_error_envelope()


def _openai_validation_error(exc: ValidationError) -> OpenAIErrorEnvelope:
    error = _openai_invalid_payload_error()
    if exc.errors():
        first = exc.errors()[0]
        loc = first.get("loc", [])
        if isinstance(loc, (list, tuple)):
            param = ".".join(str(part) for part in loc if part != "body")
            if param:
                error["error"]["param"] = param
    return error


def _openai_invalid_payload_error(param: str | None = None) -> OpenAIErrorEnvelope:
    error = openai_error("invalid_request_error", "Invalid request payload", error_type="invalid_request_error")
    if param:
        error["error"]["param"] = param
    return error


def _error_envelope_from_response(error_value: OpenAIError | None) -> OpenAIErrorEnvelopeModel:
    if error_value is None:
        return _default_error_envelope()
    return OpenAIErrorEnvelopeModel(error=error_value)


def _status_for_error(error_value: OpenAIError | None) -> int:
    if error_value and error_value.code == "no_accounts":
        return 503
    return 502
