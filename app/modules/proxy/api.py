from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from json import JSONDecodeError
from typing import Any, Final, Literal, cast
from uuid import uuid4

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Request,
    Response,
    Security,
    UploadFile,
    WebSocket,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import usage as usage_core
from app.core.auth.dependencies import (
    set_openai_error_format,
    validate_codex_usage_identity,
    validate_proxy_api_key,
    validate_proxy_api_key_authorization,
    validate_usage_api_key,
)
from app.core.auth.refresh import RefreshError
from app.core.clients.files import FileProxyError
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    ResetCreditItem,
    consume_reset_credit,
)
from app.core.clients.usage import (
    ConsumeRateLimitResetCreditResponse as UpstreamConsumeRateLimitResetCreditResponse,
)
from app.core.clients.usage import UsageFetchError, consume_rate_limit_reset_credit
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.errors import (
    PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
    OpenAIErrorEnvelope,
    is_previous_response_not_found_error,
    openai_error,
    response_failed_event,
)
from app.core.exceptions import ProxyAuthError, ProxyRateLimitError, ProxyUpstreamError
from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, bridge_public_contract_error_total
from app.core.middleware.api_firewall import _parse_trusted_proxy_networks, resolve_connection_client_ip
from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.chat_responses import (
    ChatCompletion,
    ChatCompletionResult,
    ChatCompletionUsage,
    collect_chat_completion,
    stream_chat_chunks,
)
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.images import V1ImageResponse, V1ImagesEditsForm, V1ImagesGenerationsRequest
from app.core.openai.model_registry import UpstreamModel, get_model_registry, is_public_model
from app.core.openai.models import (
    CompactResponsePayload,
    CompactResponseResult,
    OpenAIError,
    OpenAIResponsePayload,
    OpenAIResponseResult,
)
from app.core.openai.models import (
    OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel,
)
from app.core.openai.parsing import parse_response_payload
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.openai.v1_requests import V1ResponsesCompactRequest, V1ResponsesRequest
from app.core.request_locality import resolve_request_client_host
from app.core.resilience.overload import is_local_overload_error_code, merge_retry_after_headers
from app.core.runtime_logging import log_error_response
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import (
    CODEX_KEEPALIVE_FRAME,
    SSE_KEEPALIVE_FRAME,
    format_sse_event,
    inject_sse_keepalives,
    parse_sse_data_json,
)
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.dependencies import ProxyContext, get_proxy_context, get_proxy_websocket_context
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    TRAFFIC_CLASS_OPPORTUNISTIC,
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeyRequestUsageBudget,
    ApiKeySelfLimitData,
    ApiKeysService,
    ApiKeyUsageReservationData,
    _compute_pooled_credits,
)
from app.modules.firewall.repository import FirewallRepository
from app.modules.firewall.service import FirewallRepositoryPort, FirewallService
from app.modules.proxy import affinity as proxy_affinity_module
from app.modules.proxy import images_service as images_service_module
from app.modules.proxy import service as proxy_service_module
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.helpers import _rate_limit_details
from app.modules.proxy.http_bridge_forwarding import parse_forwarded_request
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    enforce_strict_function_tools_format,
    enforce_strict_text_format,
    normalize_responses_request_payload,
    openai_client_payload_error,
    openai_validation_error,
    resolve_model_alias,
    strip_terminal_compaction_trigger_input,
    validate_model_access,
)
from app.modules.proxy.schemas import (
    AccountPoolUsageResponse,
    CodexModelEntry,
    CodexModelsResponse,
    ConsumeRateLimitResetCreditRequest,
    ConsumeRateLimitResetCreditResponse,
    FileCreateRequest,
    ModelListItem,
    ModelListResponse,
    ModelMetadata,
    RateLimitStatusPayload,
    ReasoningLevelSchema,
    V1ResetCreditEntry,
    V1ResetCreditRedeemRequest,
    V1ResetCreditRedeemResponse,
    V1UsageLimitResponse,
    V1UsageResponse,
    WarmupFailedAccount,
    WarmupRequest,
    WarmupResponse,
    WarmupSkippedAccount,
    WarmupSubmittedAccount,
)
from app.modules.proxy.types import (
    CreditStatusDetailsData,
    RateLimitResetCreditsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)
from app.modules.rate_limit_reset_credits.api import serialize_reset_credit_redeem
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from app.modules.usage.mappers import usage_history_to_window_row
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)

_PUBLIC_RESPONSE_OUTPUT_ITEM_TYPES = frozenset(
    {
        "message",
        "compaction",
        "function_call",
        "function_call_output",
        "reasoning",
        "web_search_call",
        "file_search_call",
        "computer_call",
        "code_interpreter_call",
        "mcp_approval_request",
        "mcp_list_tools",
        "output_image",
    }
)
_PUBLIC_RESPONSE_TEXT_PART_TYPES = frozenset({"output_text", "input_text", "text", "refusal"})
_PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES = frozenset(
    {"response.completed", "response.incomplete", "response.failed", "error"}
)
_PUBLIC_RESPONSES_PRE_CREATED_BUFFER_LIMIT = 64


class _V1ResetCreditFreshCredentials:
    __slots__ = ("access_token_encrypted", "chatgpt_account_id")

    def __init__(self, *, access_token_encrypted: bytes, chatgpt_account_id: str | None) -> None:
        self.access_token_encrypted = access_token_encrypted
        self.chatgpt_account_id = chatgpt_account_id


router = APIRouter(
    prefix="/backend-api/codex",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
ws_router = APIRouter(
    prefix="/backend-api/codex",
    tags=["proxy"],
)
wham_router = APIRouter(
    prefix="/backend-api/wham",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
v1_router = APIRouter(
    prefix="/v1",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
v1_ws_router = APIRouter(
    prefix="/v1",
    tags=["proxy"],
)
usage_router = APIRouter(
    tags=["proxy"],
    dependencies=[Depends(set_openai_error_format)],
)
transcribe_router = APIRouter(
    prefix="/backend-api",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
files_router = APIRouter(
    prefix="/backend-api",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
internal_router = APIRouter(
    prefix="/internal/bridge",
    tags=["proxy"],
    dependencies=[Depends(set_openai_error_format)],
)

_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
_UNAVAILABLE_SELECTION_ERROR_CODES = {
    "no_accounts",
    "no_plan_support_for_model",
    "additional_quota_data_unavailable",
    "quota_exhausted",
    "no_additional_quota_eligible_accounts",
}
_STREAM_STARTUP_ERROR_PROBE_SECONDS = 0.05
# Keep bridge startup probing above tiny event-loop scheduling jitter:
# PostgreSQL-backed failures may need a DB round trip before the first item.
_HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS = 2.0
_CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS = 2.0
_CURSOR_CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS = 15.0
_CURSOR_CONTEXT_LIMIT_SYNTHETIC_USAGE_TOKENS: Final[int] = 1_000_000
_V1_MAX_OUTPUT_TOKEN_OVERRIDES: Final[dict[str, int]] = {
    "gpt-5.4": 128_000,
    "gpt-5.5": 128_000,
    "gpt-5.4-mini": 128_000,
    "gpt-5.3-codex": 128_000,
}
_OPPORTUNISTIC_RETRY_AFTER_SECONDS = 60

# OpenAI error ``type`` -> HTTP status for the /v1/images/* non-streaming
# error path. The /v1/responses path has its own ``_status_for_error``
# helper that operates on a parsed ``OpenAIError`` model; the image
# adapter works with raw envelope dicts so we map directly here.
_IMAGE_ERROR_TYPE_STATUS: Final[dict[str, int]] = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "rate_limit_error": 429,
    "insufficient_quota": 429,
}

# OpenAI error ``code`` -> HTTP status, applied as a higher-precedence
# override before the type-based mapping above.
_IMAGE_ERROR_CODE_STATUS: Final[dict[str, int]] = {
    "content_policy_violation": 400,
    "rate_limit_exceeded": 429,
    "insufficient_quota": 429,
}
_WARMUP_MODES: Final[frozenset[str]] = frozenset({"normal", "strict", "force"})


def _accepts_event_stream(request: Request) -> bool:
    for value in request.headers.getlist("accept"):
        media_ranges = (part.split(";", 1)[0].strip().lower() for part in value.split(","))
        if "text/event-stream" in media_ranges:
            return True
    return False


def _has_openai_responses_shape(payload: V1ResponsesRequest | Mapping[str, JsonValue]) -> bool:
    if isinstance(payload, Mapping):
        payload_dict = cast("Mapping[str, JsonValue]", payload)
        return (
            ("input" in payload_dict and payload_dict.get("instructions") is None)
            or payload_dict.get("messages") is not None
            or "truncation" in payload_dict
        )

    explicit_fields = payload.model_fields_set
    return (
        ("input" in explicit_fields and payload.instructions is None)
        or payload.messages is not None
        or "truncation" in explicit_fields
    )


def _is_openai_sdk_request(
    request: Request,
    payload: V1ResponsesRequest | Mapping[str, JsonValue] | None = None,
) -> bool:
    for header_name in request.headers:
        normalized_header = header_name.lower()
        if normalized_header.startswith("x-stainless-"):
            return True
    user_agent = request.headers.get("user-agent", "").lower()
    if "openai" in user_agent:
        return True
    if payload is None or not _has_openai_responses_shape(payload):
        return False
    if isinstance(payload, Mapping):
        payload_dict = cast("Mapping[str, JsonValue]", payload)
        return _accepts_event_stream(request) or payload_dict.get("messages") is not None
    return _accepts_event_stream(request) or payload.messages is not None


async def _thread_goal_payload_from_request(request: Request) -> dict[str, JsonValue]:
    if request.method.upper() == "GET":
        return {key: value for key, value in request.query_params.multi_items()}
    try:
        raw = await request.json()
    except (JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="thread goal payload must be valid JSON") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="thread goal payload must be a JSON object")
    return cast(dict[str, JsonValue], raw)


async def _thread_goal_proxy(
    request: Request,
    operation: str,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:
    payload = await _thread_goal_payload_from_request(request)
    try:
        response = await context.service.thread_goal_request(
            operation,
            payload,
            request.headers,
            method=request.method,
            codex_session_affinity=True,
            api_key=api_key,
        )
    except ProxyResponseError as exc:
        return _logged_error_json_response(request, exc.status_code, exc.payload)
    return JSONResponse(response)


_CODEX_CONTROL_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-type",
        "etag",
        "last-modified",
        "location",
        "openai-processing-ms",
        "request-id",
        "x-request-id",
    }
)


def _codex_control_downstream_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() in _CODEX_CONTROL_RESPONSE_HEADERS}


async def _codex_control_proxy(
    request: Request,
    path: str,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:
    try:
        response = await context.service.codex_control_request(
            path,
            method=request.method,
            payload=await request.body() if request.method.upper() not in {"GET", "HEAD"} else None,
            query_params=list(request.query_params.multi_items()),
            headers=request.headers,
            codex_session_affinity=True,
            api_key=api_key,
        )
    except ProxyResponseError as exc:
        return _logged_error_json_response(request, exc.status_code, exc.payload)
    return Response(
        content=response.body,
        status_code=response.status_code,
        headers=_codex_control_downstream_headers(response.headers),
    )


@router.api_route("/thread/goal/get", methods=["GET", "POST"])
async def thread_goal_get(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _thread_goal_proxy(request, "get", context, api_key)


@router.post("/thread/goal/set")
async def thread_goal_set(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _thread_goal_proxy(request, "set", context, api_key)


@router.post("/thread/goal/clear")
async def thread_goal_clear(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _thread_goal_proxy(request, "clear", context, api_key)


@router.post("/analytics-events/events")
async def codex_analytics_events(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _codex_control_proxy(request, "analytics-events/events", context, api_key)


@router.post("/memories/trace_summarize")
async def codex_memories_trace_summarize(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _codex_control_proxy(request, "memories/trace_summarize", context, api_key)


@router.post("/realtime/calls")
async def codex_realtime_calls(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _codex_control_proxy(request, "realtime/calls", context, api_key)


@router.post("/safety/arc")
async def codex_safety_arc(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _codex_control_proxy(request, "safety/arc", context, api_key)


@router.get("/agent-identities/jwks")
async def codex_agent_identities_jwks(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _codex_control_proxy(request, "agent-identities/jwks", context, api_key)


@wham_router.get("/agent-identities/jwks")
async def wham_agent_identities_jwks(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _codex_control_proxy(request, "wham/agent-identities/jwks", context, api_key)


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
    payload: dict[str, JsonValue] = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    openai_sdk_request = _is_openai_sdk_request(request, payload)
    openai_compat_payload = _has_openai_responses_shape(payload)
    try:
        responses_payload = normalize_responses_request_payload(
            payload,
            openai_compat=openai_compat_payload,
        )
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return _logged_error_json_response(request, 400, error)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return _logged_error_json_response(request, 400, error)

    return await _stream_responses(
        request,
        responses_payload,
        context,
        api_key,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
        # The Codex CLI consumes codex.* vendor events and the upstream's
        # native event ordering, while OpenAI SDK clients pointed at this
        # compatibility route need the same SSE contract enforcement as /v1.
        enforce_openai_sdk_contract=openai_sdk_request,
    )


@router.get("/opportunistic/admission")
async def opportunistic_admission(
    request: Request,
    model: str | None = None,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    denial = await _opportunistic_admission_denial(request, context, api_key, model=model)
    if denial is not None:
        return denial
    return JSONResponse({"admitted": True})


@ws_router.websocket("/responses")
async def responses_websocket(
    websocket: WebSocket,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    api_key, denial = await _validate_proxy_websocket_request(websocket)
    if denial is not None:
        await websocket.send_denial_response(denial)
        return
    turn_state = proxy_affinity_module.ensure_downstream_turn_state(websocket.headers)
    await websocket.accept(headers=proxy_affinity_module.build_downstream_turn_state_accept_headers(turn_state))
    forwarded_headers = dict(websocket.headers)
    forwarded_headers.setdefault("x-codex-turn-state", turn_state)
    await context.service.proxy_responses_websocket(
        websocket,
        forwarded_headers,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        api_key=api_key,
        client_ip=resolve_request_client_host(websocket),
    )


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
        enforce_strict_text_format(responses_payload)
        enforce_strict_function_tools_format(responses_payload.tools)
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return _logged_error_json_response(request, 400, error)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return _logged_error_json_response(request, 400, error)
    if responses_payload.stream:
        return await _stream_responses(
            request,
            responses_payload,
            context,
            api_key,
            codex_session_affinity=False,
            openai_cache_affinity=True,
            prefer_http_bridge=True,
        )
    return await _collect_responses(
        request,
        responses_payload,
        context,
        api_key,
        codex_session_affinity=False,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
    )


@internal_router.post(
    "/responses",
    include_in_schema=False,
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
async def internal_bridge_responses(
    request: Request,
    payload: ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> Response:
    forwarded_request_context, internal_error = parse_forwarded_request(
        request.headers,
        payload=payload,
        current_instance=get_settings().http_responses_session_bridge_instance_id,
    )
    if internal_error is not None or forwarded_request_context is None:
        assert internal_error is not None
        return _logged_error_json_response(request, internal_error.status_code, internal_error.payload)
    api_key, auth_error = await _validate_internal_bridge_api_key(request)
    if auth_error is not None:
        return auth_error
    skip_limit_enforcement = api_key is None or forwarded_request_context.context.reservation is not None
    forwarded_headers = _strip_internal_bridge_headers(request.headers)
    return await _stream_responses(
        request,
        payload,
        context,
        api_key,
        codex_session_affinity=forwarded_request_context.context.codex_session_affinity,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
        skip_limit_enforcement=skip_limit_enforcement,
        api_key_reservation_override=forwarded_request_context.context.reservation,
        include_rate_limit_headers=False,
        forwarded_request=True,
        forwarded_headers=forwarded_headers,
        forwarded_downstream_turn_state=forwarded_request_context.context.downstream_turn_state,
        forwarded_affinity_kind=forwarded_request_context.context.original_affinity_kind,
        forwarded_affinity_key=forwarded_request_context.context.original_affinity_key,
        forwarded_client_ip=forwarded_request_context.context.client_ip,
        # The OpenAI-SDK contract rewrites (drop ``codex.*``, backfill terminal
        # output, synthesize ``response.created``) MUST be applied by the
        # origin instance — the one that actually responds to the client — so
        # they can honour the original route's ``enforce_openai_sdk_contract``
        # decision. This handler runs on the owner instance after the origin
        # forwarded the request via the internal bridge; if we re-applied them
        # here, a forwarded ``/backend-api/codex/responses`` request would
        # lose ``codex.*`` events (and gain a synthetic ``response.created``)
        # before the origin ever sees the stream. Forward verbatim and let
        # the origin run its own normalization.
        enforce_openai_sdk_contract=False,
    )


@v1_ws_router.websocket("/responses")
async def v1_responses_websocket(
    websocket: WebSocket,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    api_key, denial = await _validate_proxy_websocket_request(websocket)
    if denial is not None:
        await websocket.send_denial_response(denial)
        return
    turn_state = proxy_affinity_module.ensure_downstream_turn_state(websocket.headers)
    await websocket.accept(headers=proxy_affinity_module.build_downstream_turn_state_accept_headers(turn_state))
    forwarded_headers = dict(websocket.headers)
    forwarded_headers.setdefault("x-codex-turn-state", turn_state)
    await context.service.proxy_responses_websocket(
        websocket,
        forwarded_headers,
        codex_session_affinity=False,
        openai_cache_affinity=True,
        api_key=api_key,
        client_ip=resolve_request_client_host(websocket),
    )


@router.get("/models", response_model=CodexModelsResponse)
async def models(
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _build_codex_models_response(api_key)


@v1_router.get("/models", response_model=ModelListResponse)
async def v1_models(
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _build_models_response(api_key)


@v1_router.get("/usage", response_model=V1UsageResponse)
async def v1_usage(
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> V1UsageResponse | JSONResponse:
    usage_sections = _parse_usage_sections(api_key.usage_sections)
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session), usage_repository=UsageRepository(session))
        usage = await service.get_key_usage_summary_for_self(api_key.id)
        aggregate_limits = await _build_aggregate_credit_limits(session) if "upstream_limits" in usage_sections else {}
        hide_upstream_limits = await _hide_upstream_quota_for_api_key_clients(api_key)
        account_pool_usage = (
            await _build_account_pool_usage(
                session,
                assigned_account_ids=api_key.assigned_account_ids,
                account_assignment_scope_enabled=api_key.account_assignment_scope_enabled,
            )
            if "account_pool_usage" in usage_sections and not hide_upstream_limits
            else None
        )

    if usage is None:
        raise ProxyAuthError("Invalid API key")

    return V1UsageResponse(
        request_count=usage.request_count,
        total_tokens=usage.total_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        total_cost_usd=usage.total_cost_usd,
        limits=[_to_v1_usage_limit_response(limit) for limit in usage.limits],
        upstream_limits=[] if hide_upstream_limits else _ordered_aggregate_limits(aggregate_limits),
        account_pool_usage=account_pool_usage,
    )


def _is_reset_credit_selectable_account(account: Account) -> bool:
    return bool(account.chatgpt_account_id) and account.status not in (
        AccountStatus.REAUTH_REQUIRED,
        AccountStatus.DEACTIVATED,
        AccountStatus.PAUSED,
    )


def _eligible_reset_credit_accounts(accounts: list[Account], api_key: ApiKeyData) -> list[Account]:
    if api_key.account_assignment_scope_enabled:
        assigned_ids = {account_id for account_id in api_key.assigned_account_ids if account_id}
        requested_accounts = [account for account in accounts if account.id in assigned_ids]
    else:
        requested_accounts = accounts
    return [account for account in requested_accounts if _is_reset_credit_selectable_account(account)]


def _project_reset_credit_accounts(accounts: list[Account], api_key: ApiKeyData) -> list[tuple[str, str]]:
    eligible_accounts = sorted(
        _eligible_reset_credit_accounts(accounts, api_key),
        key=lambda account: (account.email, account.id),
    )
    return [(account.id, account.email) for account in eligible_accounts]  # ty:ignore[invalid-return-type]


def _list_available_reset_credits(account_id: str, email: str) -> list[V1ResetCreditEntry]:
    snapshot = get_rate_limit_reset_credits_store().get(account_id)
    if snapshot is None or snapshot.available_count <= 0:
        return []

    available_credits = [credit for credit in snapshot.credits if credit.status == "available"]
    if not available_credits:
        return []

    far_future = datetime.max.replace(tzinfo=timezone.utc)
    ordered_credits = sorted(
        available_credits,
        key=lambda credit: (credit.expires_at or far_future, credit.id),
    )
    return [
        V1ResetCreditEntry(
            account_id=account_id,
            email=email,
            redeem_id=credit.id,
            expired_at=credit.expires_at,
        )
        for credit in ordered_credits
    ]


def _is_reset_credit_account_in_api_key_pool(account: Account | None, api_key: ApiKeyData) -> bool:
    if account is None or not _is_reset_credit_selectable_account(account):
        return False
    if not api_key.account_assignment_scope_enabled:
        return True
    assigned_ids = {account_id for account_id in api_key.assigned_account_ids if account_id}
    return account.id in assigned_ids


def _select_available_reset_credit_by_id(account_id: str, redeem_id: str) -> ResetCreditItem | None:
    snapshot = get_rate_limit_reset_credits_store().get(account_id)
    if snapshot is None or snapshot.available_count <= 0:
        return None
    for credit in snapshot.credits:
        if credit.id == redeem_id and credit.status == "available":
            return credit
    return None


def _translate_v1_reset_credit_consume_error(exc: ConsumeResetCreditError) -> HTTPException:
    status_code = exc.status_code if exc.status_code > 0 else 503
    return HTTPException(status_code=status_code, detail=exc.message)


def _should_invalidate_v1_reset_credit_snapshot_on_consume_error(exc: ConsumeResetCreditError) -> bool:
    return exc.status_code == 409


def _translate_v1_reset_credit_refresh_error(exc: RefreshError) -> HTTPException:
    if exc.is_permanent:
        get_account_selection_cache().invalidate()
    return HTTPException(
        status_code=409,
        detail=f"Reset credit redeem could not refresh account credentials: {exc.message}",
    )


@asynccontextmanager
async def _v1_reset_credit_accounts_refresh_scope() -> AsyncIterator[AccountsRepository]:
    async with get_background_session() as session:
        yield AccountsRepository(session)


async def _ensure_v1_reset_credit_account_fresh(account_id: str) -> _V1ResetCreditFreshCredentials:
    async with get_background_session() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        auth_manager = AuthManager(
            repo,
            refresh_repo_factory=_v1_reset_credit_accounts_refresh_scope,
        )
        refreshed = await auth_manager.ensure_fresh(account, force=False)
        return _V1ResetCreditFreshCredentials(
            access_token_encrypted=refreshed.access_token_encrypted,
            chatgpt_account_id=refreshed.chatgpt_account_id,
        )


@usage_router.get("/v1/reset-credit", response_model=list[V1ResetCreditEntry])
async def v1_reset_credit(
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> list[V1ResetCreditEntry]:
    async with get_background_session() as session:
        accounts = await AccountsRepository(session).list_accounts(refresh_existing=True)
        eligible_accounts = _project_reset_credit_accounts(accounts, api_key)

    response: list[V1ResetCreditEntry] = []
    for account_id, account_email in eligible_accounts:
        response.extend(_list_available_reset_credits(account_id, account_email))
    return response


@usage_router.post("/v1/reset-credit", response_model=V1ResetCreditRedeemResponse)
async def v1_redeem_reset_credit(
    payload: V1ResetCreditRedeemRequest,
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> V1ResetCreditRedeemResponse:
    async with get_background_session() as session:
        account = await AccountsRepository(session).get_by_id(payload.account_id)
        if not _is_reset_credit_account_in_api_key_pool(account, api_key):
            raise HTTPException(status_code=403, detail="Account is outside the API key pool")
        if account is None:
            raise HTTPException(status_code=403, detail="Account is outside the API key pool")
        account_id = account.id

        async with serialize_reset_credit_redeem(account_id, session=session):
            credit = _select_available_reset_credit_by_id(account_id, payload.redeem_id)
            if credit is None:
                raise HTTPException(status_code=409, detail="Requested reset credit is unavailable")
            try:
                route = await _resolve_reset_credit_route(session, account_id)
            except UpstreamProxyRouteError as exc:
                raise HTTPException(status_code=503, detail="Unable to resolve upstream proxy route") from exc
            try:
                redeem_credentials = await _ensure_v1_reset_credit_account_fresh(account_id)
            except RefreshError as exc:
                raise _translate_v1_reset_credit_refresh_error(exc) from exc
            access_token = TokenEncryptor().decrypt(redeem_credentials.access_token_encrypted)
            try:
                result = await consume_reset_credit(
                    access_token,
                    redeem_credentials.chatgpt_account_id,
                    credit.id,
                    route=route,
                    allow_direct_egress=route is None,
                )
            except ConsumeResetCreditError as exc:
                if _should_invalidate_v1_reset_credit_snapshot_on_consume_error(exc):
                    await get_rate_limit_reset_credits_store().invalidate(account_id)
                raise _translate_v1_reset_credit_consume_error(exc) from exc
            await get_rate_limit_reset_credits_store().invalidate(account_id)
            try:
                await _refresh_usage_after_v1_reset_credit_redeem(account_id)
            except Exception:
                logger.warning(
                    "V1 reset credit consume succeeded but usage refresh failed account_id=%s",
                    account_id,
                    exc_info=True,
                )
            redeemed_at = result.credit.redeemed_at if result.credit else None
            return V1ResetCreditRedeemResponse(
                code=result.code,
                windows_reset=result.windows_reset,
                redeemed_at=redeemed_at,
            )


async def _resolve_reset_credit_route(session: AsyncSession, account_id: str) -> ResolvedUpstreamRoute | None:
    return await resolve_upstream_route(
        session,
        account_id=account_id,
        operation="reset_credits_consume",
        scope="account",
    )


async def _refresh_usage_after_v1_reset_credit_redeem(account_id: str) -> None:
    async with get_background_session() as session:
        account = await AccountsRepository(session).get_by_id(account_id)
        if account is None:
            logger.warning(
                "V1 reset credit consume succeeded but account disappeared before usage refresh account_id=%s",
                account_id,
            )
            return
        usage_updater = UsageUpdater(
            UsageRepository(session),
            AccountsRepository(session),
            AdditionalUsageRepository(session),
        )
        refreshed = await usage_updater.force_refresh(account)
    if refreshed:
        get_account_selection_cache().invalidate()
        return
    logger.warning(
        "V1 reset credit consume succeeded but usage refresh returned no update account_id=%s",
        account_id,
    )


async def _run_v1_warmup(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = None,
    *,
    mode: str,
) -> Response:
    if mode not in _WARMUP_MODES:
        return _logged_error_json_response(
            request,
            400,
            openai_error(
                "invalid_request_error",
                "Invalid warmup mode. Supported values: normal, strict, force.",
                error_type="invalid_request_error",
            ),
        )

    try:
        result = await context.service.warmup(mode=mode, headers=request.headers, api_key=api_key)
    except ValueError as exc:
        return _logged_error_json_response(
            request,
            400,
            openai_error(
                "invalid_request_error",
                str(exc),
                error_type="invalid_request_error",
            ),
        )

    response = WarmupResponse(
        mode=result.mode,
        total_accounts=result.total_accounts,
        submitted=[
            WarmupSubmittedAccount(
                account_id=entry.account_id,
                request_id=entry.request_id,
                model=entry.model,
            )
            for entry in result.submitted
        ],
        skipped=[
            WarmupSkippedAccount(
                account_id=entry.account_id,
                reason=entry.reason,
            )
            for entry in result.skipped
        ],
        failed=[
            WarmupFailedAccount(
                account_id=entry.account_id,
                error_code=entry.error_code,
                error_message=entry.error_message,
            )
            for entry in result.failed
        ],
    )
    return JSONResponse(content=response.model_dump(mode="json"))


@v1_router.post("/warmup", response_model=WarmupResponse)
async def v1_warmup(
    request: Request,
    payload: WarmupRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _run_v1_warmup(
        request,
        context,
        api_key,
        mode=payload.mode.strip().lower(),
    )


@v1_router.post("/warmup/{mode}", response_model=WarmupResponse)
async def v1_warmup_by_mode(
    request: Request,
    mode: str,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _run_v1_warmup(
        request,
        context,
        api_key,
        mode=mode.strip().lower(),
    )


def _ordered_aggregate_limits(aggregate_limits: dict[str, V1UsageLimitResponse]) -> list[V1UsageLimitResponse]:
    return [limit for window in ("5h", "7d", "monthly") if (limit := aggregate_limits.get(window)) is not None]


def _parse_usage_sections(raw: str) -> set[str]:
    if not raw or not raw.strip():
        return set()
    return {s.strip() for s in raw.split(",") if s.strip()}


async def _build_account_pool_usage(
    session: AsyncSession,
    *,
    assigned_account_ids: list[str],
    account_assignment_scope_enabled: bool,
) -> AccountPoolUsageResponse | None:
    from app.modules.api_keys.repository import ApiKeysRepository

    repo = ApiKeysRepository(session)
    usage_repo = UsageRepository(session)
    if account_assignment_scope_enabled:
        all_accounts = await repo.list_accounts_by_ids(assigned_account_ids)
        usage_account_ids: list[str] | None = assigned_account_ids
    else:
        all_accounts = await repo.list_all_accounts()
        usage_account_ids = None

    primary_usage = await usage_repo.latest_by_account("primary", account_ids=usage_account_ids)
    secondary_usage = await usage_repo.latest_by_account("secondary", account_ids=usage_account_ids)

    data = _compute_pooled_credits(
        assigned_account_ids=assigned_account_ids,
        all_accounts=all_accounts,
        primary_usage=primary_usage,
        secondary_usage=secondary_usage,
        account_assignment_scope_enabled=account_assignment_scope_enabled,
    )
    return AccountPoolUsageResponse(
        primary=data.remaining_percent_primary,
        secondary=data.remaining_percent_secondary,
    )


def _to_v1_usage_limit_response(limit: ApiKeySelfLimitData) -> V1UsageLimitResponse:
    current_value = max(0, min(limit.current_value, limit.max_value))
    return V1UsageLimitResponse(
        limit_type=limit.limit_type,
        limit_window=limit.limit_window,
        max_value=limit.max_value,
        current_value=current_value,
        remaining_value=max(0, limit.max_value - current_value),
        model_filter=limit.model_filter,
        reset_at=limit.reset_at.isoformat() + "Z",
        source=limit.source,
    )


async def _build_codex_usage_payload_for_api_key(api_key: ApiKeyData) -> RateLimitStatusPayloadData:
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        usage = await service.get_key_usage_summary_for_self(api_key.id)

    if usage is None:
        raise ProxyAuthError("Invalid API key")

    key_limits = [_to_v1_usage_limit_response(limit) for limit in usage.limits]
    primary_credit_limit = _select_codex_usage_limit(key_limits, "5h") or _select_codex_usage_limit(key_limits, "daily")
    secondary_credit_limit = _select_codex_usage_limit(key_limits, "7d") or _select_codex_usage_limit(
        key_limits, "weekly"
    )
    monthly_credit_limit = _select_codex_usage_limit(key_limits, "monthly")

    return RateLimitStatusPayloadData(
        plan_type="api_key",
        rate_limit=_rate_limit_details(
            _codex_usage_window_snapshot(primary_credit_limit),
            _codex_usage_window_snapshot(secondary_credit_limit),
            _codex_usage_window_snapshot(monthly_credit_limit),
        ),
        credits=_codex_usage_credit_snapshot(primary_credit_limit, secondary_credit_limit, monthly_credit_limit),
    )


async def _hide_upstream_quota_for_api_key_clients(api_key: ApiKeyData | None) -> bool:
    if api_key is None:
        return False
    settings = await get_settings_cache().get()
    return bool(getattr(settings, "hide_upstream_quota_from_api_keys", False))


async def _rate_limit_headers_for_request(
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> dict[str, str]:
    if await _hide_upstream_quota_for_api_key_clients(api_key):
        return {}
    return await context.service.rate_limit_headers()


def _select_codex_usage_limit(
    limits: list[V1UsageLimitResponse],
    window: str,
) -> V1UsageLimitResponse | None:
    candidates = [
        limit
        for limit in limits
        if limit.limit_window == window and limit.model_filter is None and limit.limit_type == "credits"
    ]
    return candidates[0] if candidates else None


def _codex_usage_window_snapshot(limit: V1UsageLimitResponse | None) -> RateLimitWindowSnapshotData | None:
    if limit is None or limit.max_value <= 0:
        return None
    reset_at = datetime.fromisoformat(limit.reset_at.replace("Z", "+00:00"))
    reset_epoch = int(reset_at.timestamp())
    now_epoch = int(time.time())
    used_percent = max(0, min(100, int((limit.current_value / limit.max_value) * 100)))
    window_seconds = {"5h": 18000, "daily": 86400, "7d": 604800, "weekly": 604800, "monthly": 2592000}.get(
        limit.limit_window
    )
    return RateLimitWindowSnapshotData(
        used_percent=used_percent,
        limit_window_seconds=window_seconds,
        reset_after_seconds=max(0, reset_epoch - now_epoch),
        reset_at=reset_epoch,
    )


def _codex_usage_credit_snapshot(
    primary_limit: V1UsageLimitResponse | None,
    secondary_limit: V1UsageLimitResponse | None,
    monthly_limit: V1UsageLimitResponse | None = None,
) -> CreditStatusDetailsData | None:
    preferred = monthly_limit or secondary_limit or primary_limit
    if preferred is None or preferred.limit_type != "credits":
        return None
    return CreditStatusDetailsData(
        has_credits=preferred.remaining_value > 0,
        unlimited=False,
        balance=str(preferred.remaining_value),
        approx_local_messages=None,
        approx_cloud_messages=None,
    )


def _codex_usage_reset_credits_from_request(request: Request) -> RateLimitResetCreditsData | None:
    usage_payload = getattr(request.state, "codex_usage_identity_payload", None)
    summary = getattr(usage_payload, "rate_limit_reset_credits", None)
    if summary is None:
        return None
    return RateLimitResetCreditsData(available_count=max(0, int(summary.available_count or 0)))


def _attach_codex_usage_reset_credits(
    payload: RateLimitStatusPayloadData,
    request: Request,
) -> RateLimitStatusPayloadData:
    reset_credits = _codex_usage_reset_credits_from_request(request)
    if reset_credits is None:
        return payload
    return replace(payload, rate_limit_reset_credits=reset_credits)


async def _build_aggregate_credit_limits(session: AsyncSession) -> dict[str, V1UsageLimitResponse]:
    usage_repository = UsageRepository(session)
    primary_latest = await usage_repository.latest_by_account(window="primary")
    secondary_latest = await usage_repository.latest_by_account(window="secondary")
    monthly_latest = await usage_repository.latest_by_account(window="monthly")

    primary_rows = [usage_history_to_window_row(entry) for entry in primary_latest.values()]
    secondary_rows = [usage_history_to_window_row(entry) for entry in secondary_latest.values()]
    monthly_rows = [usage_history_to_window_row(entry) for entry in monthly_latest.values()]
    primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(primary_rows, secondary_rows)

    account_ids = (
        {row.account_id for row in primary_rows}
        | {row.account_id for row in secondary_rows}
        | {row.account_id for row in monthly_rows}
    )
    if not account_ids:
        return {}

    account_map = {account.id: account for account in await _load_accounts_by_id(session, account_ids)}
    if not account_map:
        return {}

    active_account_ids = set(account_map)
    primary_rows = [row for row in primary_rows if row.account_id in active_account_ids]
    secondary_rows = [row for row in secondary_rows if row.account_id in active_account_ids]
    monthly_rows = [row for row in monthly_rows if row.account_id in active_account_ids]
    limits: dict[str, V1UsageLimitResponse] = {}

    for window_key, rows, label in (
        ("primary", primary_rows, "5h"),
        ("secondary", secondary_rows, "7d"),
        ("monthly", monthly_rows, "monthly"),
    ):
        if not rows:
            continue
        summary = usage_core.summarize_usage_window(rows, account_map, window_key)
        max_value = max(0, int(round(summary.capacity_credits or 0.0)))
        if max_value <= 0:
            continue
        if summary.reset_at is None:
            continue
        current_value = max(0, min(int(round(summary.used_credits or 0.0)), max_value))
        limits[label] = V1UsageLimitResponse(
            limit_type="credits",
            limit_window=label,
            max_value=max_value,
            current_value=current_value,
            remaining_value=max(0, max_value - current_value),
            model_filter=None,
            reset_at=datetime.fromtimestamp(summary.reset_at, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            source="aggregate",
        )

    return limits


async def _load_accounts_by_id(session: AsyncSession, account_ids: set[str]) -> list[Account]:
    if not account_ids:
        return []
    result = await session.execute(
        select(Account).where(
            Account.id.in_(account_ids),
            Account.status.notin_((AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED)),
        )
    )
    return list(result.scalars().all())


@transcribe_router.post("/transcribe")
async def backend_transcribe(
    request: Request,
    file: UploadFile = File(...),
    prompt: str | None = Form(None),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    return await _transcribe_request(
        request=request,
        file=file,
        prompt=prompt,
        context=context,
        api_key=api_key,
    )


# Synthetic ``model`` strings used for API-key limit accounting +
# request-log filtering on the file upload protocol. They never reach
# upstream -- this is a proxy-internal name only.
_FILES_CREATE_LIMIT_MODEL: Final = "files-create"
_FILES_FINALIZE_LIMIT_MODEL: Final = "files-finalize"


@files_router.post("/files")
async def backend_files_create(
    request: Request,
    payload: FileCreateRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    """Forward a `POST /backend-api/files` upload registration to upstream.

    Accepts ``{file_name, file_size, use_case}`` and returns the upstream
    JSON verbatim (typically ``{file_id, upload_url}``) so callers can
    PUT the bytes directly to the SAS upload URL without going through
    the proxy. The 16 MiB websocket ceiling on ``/responses`` does not
    apply here -- upstream caps file size at 512 MiB which we enforce in
    ``FileCreateRequest``.
    """
    reservation = await _enforce_request_limits(
        api_key,
        request_model=_FILES_CREATE_LIMIT_MODEL,
        request_service_tier=None,
    )
    try:
        result = await context.service.create_file(
            payload.model_dump(mode="json", exclude_none=True),
            request.headers,
            api_key=api_key,
        )
    except FileProxyError as exc:
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    except ProxyResponseError as exc:
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    finally:
        await _release_reservation(reservation)
    return JSONResponse(content=result)


@files_router.post("/files/{file_id}/uploaded")
async def backend_files_finalize(
    request: Request,
    file_id: str = Path(..., min_length=1),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    """Forward a `POST /backend-api/files/{file_id}/uploaded` finalize call.

    The upstream contract returns ``{status: success|retry|failed,
    download_url, file_name, mime_type, ...}``. ``service.finalize_file``
    polls upstream for up to 30 s while ``status == "retry"``; we return
    the final payload verbatim so the caller sees what upstream saw.
    """
    reservation = await _enforce_request_limits(
        api_key,
        request_model=_FILES_FINALIZE_LIMIT_MODEL,
        request_service_tier=None,
    )
    try:
        result = await context.service.finalize_file(
            file_id,
            request.headers,
            api_key=api_key,
        )
    except FileProxyError as exc:
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    except ProxyResponseError as exc:
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
        )
    finally:
        await _release_reservation(reservation)
    return JSONResponse(content=result)


@v1_router.post("/audio/transcriptions")
async def v1_audio_transcriptions(
    request: Request,
    model: str = Form(...),
    file: UploadFile = File(...),
    prompt: str | None = Form(None),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    if model != _TRANSCRIPTION_MODEL:
        return _logged_error_json_response(
            request,
            status_code=400,
            content=_openai_invalid_transcription_model_error(model),
        )
    return await _transcribe_request(
        request=request,
        file=file,
        prompt=prompt,
        context=context,
        api_key=api_key,
    )


@v1_router.post("/images/generations", response_model=None)
async def v1_images_generations(
    request: Request,
    payload: V1ImagesGenerationsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _proxy_images_generation_request(
        request=request,
        payload=payload,
        context=context,
        api_key=api_key,
    )


@v1_router.post("/images/edits", response_model=None)
async def v1_images_edits(
    request: Request,
    # All typed form fields below are bound as raw strings so FastAPI
    # never 422s on malformed input (e.g. ``n=abc``). Pydantic on
    # ``V1ImagesEditsForm`` coerces and validates them and surfaces any
    # failure as an OpenAI-shape ``invalid_request_error`` envelope.
    model: str | None = Form(None),
    prompt: str = Form(...),
    # Accept either the OpenAI canonical ``image`` form key (single or
    # repeated) or the ``image[]`` array-style key that some OpenAI SDKs
    # / HTTP clients emit when sending multiple files. Both are bound as
    # ``list[UploadFile] = File(None)`` and merged below; at least one
    # entry must be present after the merge.
    image: list[UploadFile] | None = File(None),
    image_brackets: list[UploadFile] | None = File(None, alias="image[]"),
    mask: UploadFile | None = File(None),
    n: str | None = Form(None),
    size: str | None = Form(None),
    quality: str | None = Form(None),
    background: str | None = Form(None),
    output_format: str | None = Form(None),
    output_compression: str | None = Form(None),
    moderation: str | None = Form(None),
    partial_images: str | None = Form(None),
    stream: str | None = Form(None),
    input_fidelity: str | None = Form(None),
    user: str | None = Form(None),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    raw_form: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "size": size if size is not None else "auto",
        "quality": quality if quality is not None else "auto",
        "background": background if background is not None else "auto",
        "output_format": output_format if output_format is not None else "png",
        "moderation": moderation if moderation is not None else "auto",
        "input_fidelity": input_fidelity,
        "user": user,
    }
    # Pydantic coerces these scalar fields from strings on its own as
    # long as the value is a valid representation (e.g. "1", "true");
    # invalid values land in ValidationError below and we map to
    # ``invalid_request_error`` rather than letting FastAPI 422.
    if n is not None:
        raw_form["n"] = n
    else:
        raw_form["n"] = 1
    if output_compression is not None:
        raw_form["output_compression"] = output_compression
    else:
        raw_form["output_compression"] = 100
    if partial_images is not None:
        raw_form["partial_images"] = partial_images
    if stream is not None:
        raw_form["stream"] = stream
    else:
        raw_form["stream"] = False
    try:
        form_payload = V1ImagesEditsForm.model_validate(raw_form)
    except ValidationError as exc:
        return _logged_error_json_response(request, 400, openai_validation_error(exc))

    # Merge ``image`` and ``image[]`` into a single ordered list. Both
    # form keys are accepted so OpenAI SDKs and HTTP clients that pick
    # either convention work without modification.
    merged_images: list[UploadFile] = []
    if image:
        merged_images.extend(image)
    if image_brackets:
        merged_images.extend(image_brackets)
    if not merged_images:
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                "At least one ``image`` (or ``image[]``) multipart part is required.",
                param="image",
            ),
        )

    images_payload: list[tuple[bytes, str | None]] = []
    for upload in merged_images:
        try:
            data = await upload.read()
        finally:
            await upload.close()
        if not data:
            return _logged_error_json_response(
                request,
                400,
                images_service_module.make_invalid_request_error(
                    "image part is empty",
                    param="image",
                ),
            )
        images_payload.append((data, upload.content_type))

    mask_payload: tuple[bytes, str | None] | None = None
    if mask is not None:
        try:
            data = await mask.read()
        finally:
            await mask.close()
        if not data:
            return _logged_error_json_response(
                request,
                400,
                images_service_module.make_invalid_request_error(
                    "mask part is empty",
                    param="mask",
                ),
            )
        mask_payload = (data, mask.content_type)

    return await _proxy_images_edit_request(
        request=request,
        payload=form_payload,
        images=images_payload,
        mask=mask_payload,
        context=context,
        api_key=api_key,
    )


@v1_router.post("/images/variations", include_in_schema=False)
async def v1_images_variations(
    request: Request,
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    # ``api_key`` is captured purely so the standard
    # ``Security(validate_proxy_api_key)`` dependency runs and rejects
    # unauthenticated callers with the same policy as every other
    # /v1/images/* route (and the rest of /v1). Without it, this
    # endpoint would return a public 404 even when proxy API-key auth
    # is enabled, which is an inconsistent auth surface.
    del api_key
    return _logged_error_json_response(
        request,
        status_code=404,
        content=images_service_module.make_not_found_error(
            "/v1/images/variations is not supported by codex-lb. Use /v1/images/edits with an explicit prompt instead."
        ),
    )


async def _prime_upstream_stream(
    request: Request,
    upstream: AsyncIterator[str],
    rate_limit_headers: Mapping[str, str],
    *,
    on_error: Callable[[], Awaitable[None]] | None = None,
) -> tuple[AsyncIterator[str] | None, Response | None]:
    """Pull the first chunk from ``upstream`` so any error raised before the
    first SSE event is surfaced as a structured OpenAI error envelope
    instead of a broken/truncated stream.

    Returns ``(primed_iterator, None)`` on success, where the returned
    iterator yields the captured first chunk followed by the rest of
    ``upstream``. Returns ``(None, error_response)`` when the upstream
    raised before yielding anything; in that case ``on_error`` is called
    so the caller can release reservations.
    """
    iterator = upstream.__aiter__()
    try:
        first_chunk = await iterator.__anext__()
    except StopAsyncIteration:
        first_chunk = None
    except ProxyResponseError as exc:
        if on_error is not None:
            await on_error()
        return None, _logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=dict(rate_limit_headers),
        )

    async def _replay() -> AsyncIterator[str]:
        if first_chunk is not None:
            yield first_chunk
        async for chunk in iterator:
            yield chunk

    return _replay(), None


async def _proxy_images_generation_request(
    *,
    request: Request,
    payload: V1ImagesGenerationsRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:
    # Apply the API key's enforced model BEFORE running the cross-field
    # validation matrix. Otherwise a request that passes validation
    # under the client-supplied ``model`` (e.g. gpt-image-2 with a 16-
    # multiple custom size) could silently be swapped to a different
    # ``gpt-image-*`` variant whose validation matrix it does not
    # satisfy, leading to a non-canonical upstream failure instead of
    # a deterministic 400 at the API boundary.
    settings = proxy_service_module.get_settings()
    requested_model = payload.model  # may be None; resolved below.
    effective_model = _effective_model_for_api_key(
        api_key,
        requested_model or settings.images_default_model,
    )
    if not images_service_module.is_supported_image_model(effective_model):
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                f"Effective model '{effective_model}' is not a 'gpt-image-*' model. "
                f"This API key is pinned to '{effective_model}' which cannot be used on "
                f"/v1/images/* routes; use a key that allows gpt-image models.",
                param="model",
            ),
        )
    if effective_model != requested_model:
        # Rebind ``payload.model`` so the validation matrix below, the
        # downstream translation, request logging, and tool config all
        # see the enforced (or default-resolved) value.
        payload = payload.model_copy(update={"model": effective_model})

    try:
        payload = images_service_module.validate_generations_payload(payload)
    except ClientPayloadError as exc:
        return _logged_error_json_response(request, 400, openai_client_payload_error(exc))

    public_model = payload.model
    assert public_model is not None
    host_model = settings.images_host_model

    try:
        validate_model_access(api_key, effective_model)
    except Exception:
        # Re-raise so the global handler maps to 403.
        raise

    rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
    reservation = await _enforce_request_limits(
        api_key,
        request_model=effective_model,
        request_service_tier=None,
    )

    try:
        responses_payload = images_service_module.images_generation_to_responses_request(payload, host_model=host_model)
    except ValidationError as exc:
        await _release_reservation(reservation)
        return _logged_error_json_response(
            request,
            400,
            openai_validation_error(exc),
            headers=rate_limit_headers,
        )

    # We always need an upstream stream because tool_usage.image_gen only
    # appears on response.completed. For non-streaming clients we drain the
    # stream and translate to a JSON envelope.
    # Pass ``api_key_reservation=None`` so the standard stream settlement
    # in ``_settle_stream_api_key_usage`` does NOT release/finalize the
    # reservation from ``response.usage`` (which is typically empty for
    # the image_generation tool path). The image route owns the
    # reservation lifecycle and finalizes it from the captured
    # ``tool_usage.image_gen`` tokens via ``_finalize_image_reservation``,
    # which avoids the double-billing scenario where standard settlement
    # would charge ``response.usage`` and we would also charge the image
    # tokens.
    upstream = context.service.stream_responses(
        responses_payload,
        request.headers,
        codex_session_affinity=False,
        propagate_http_errors=True,
        openai_cache_affinity=True,
        api_key=api_key,
        api_key_reservation=None,
        client_ip=resolve_request_client_host(request),
    )

    # ``images_service`` populates ``response_id`` once the upstream stream
    # surfaces the Responses id, so we can rewrite the request log's model
    # column from the internal host model to the public ``gpt-image-*``
    # value the client actually requested.
    captured: dict[str, object] = {}

    # Prime the upstream stream so that errors raised before the first
    # chunk (e.g. exhausted retries propagating a ProxyResponseError) are
    # surfaced as structured OpenAI error envelopes instead of broken /
    # truncated SSE streams. ``_prime_upstream_stream`` returns either
    # ``(primed_iterator, None)`` on success or ``(None, error_response)``
    # when the upstream raised before yielding anything.
    primed_upstream, prime_error = await _prime_upstream_stream(
        request,
        upstream,
        rate_limit_headers,
        on_error=lambda: _release_reservation(reservation),
    )
    if prime_error is not None:
        return prime_error
    assert primed_upstream is not None

    if payload.stream:
        translated = images_service_module.translate_responses_stream_to_images_stream(
            primed_upstream, captured=captured
        )

        async def _stream_with_log_rewrite() -> AsyncIterator[bytes]:
            try:
                async for chunk in translated:
                    yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            finally:
                # Run the request-log model rewrite even when the stream
                # is cancelled mid-flight (e.g. client disconnect). Without
                # this, an interrupted SSE response would leave the
                # request_logs row pinned to the internal host model.
                response_id = captured.get("response_id")
                if response_id and isinstance(response_id, str):
                    await context.service.rewrite_request_log_model(response_id, public_model)
                # Finalize the reservation from the captured
                # ``tool_usage.image_gen`` tokens (or release if
                # upstream never produced a usable image). This is the
                # single point where the image API charges API-key
                # limits; standard stream settlement is bypassed via
                # ``api_key_reservation=None`` above.
                _input = captured.get("image_input_tokens")
                _output = captured.get("image_output_tokens")
                _cached = captured.get("image_cached_input_tokens")
                await _finalize_image_reservation(
                    reservation,
                    model=public_model,
                    input_tokens=_input if isinstance(_input, int) else None,
                    output_tokens=_output if isinstance(_output, int) else None,
                    cached_input_tokens=_cached if isinstance(_cached, int) else None,
                )

        return StreamingResponse(
            _stream_with_log_rewrite(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        response_payload, error_envelope = await images_service_module.collect_responses_stream_for_images(
            primed_upstream,
            captured=captured,
        )
    except ProxyResponseError as exc:
        await _release_reservation(reservation)
        return _logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=rate_limit_headers,
        )

    response_id = captured.get("response_id")
    if response_id and isinstance(response_id, str):
        await context.service.rewrite_request_log_model(response_id, public_model)
    _input = captured.get("image_input_tokens")
    _output = captured.get("image_output_tokens")
    _cached = captured.get("image_cached_input_tokens")
    await _finalize_image_reservation(
        reservation,
        model=public_model,
        input_tokens=_input if isinstance(_input, int) else None,
        output_tokens=_output if isinstance(_output, int) else None,
        cached_input_tokens=_cached if isinstance(_cached, int) else None,
    )

    if error_envelope is not None:
        return _logged_error_json_response(
            request,
            _status_for_image_error_envelope(error_envelope),
            error_envelope,
            headers=rate_limit_headers,
        )
    assert response_payload is not None
    images_result = images_service_module.images_response_from_responses(response_payload)
    if not isinstance(images_result, V1ImageResponse):
        return _logged_error_json_response(
            request,
            _status_for_image_error_envelope(images_result),
            images_result,
            headers=rate_limit_headers,
        )
    return JSONResponse(
        content=images_result.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )


async def _proxy_images_edit_request(
    *,
    request: Request,
    payload: V1ImagesEditsForm,
    images: list[tuple[bytes, str | None]],
    mask: tuple[bytes, str | None] | None,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:
    # Apply the API key's enforced model BEFORE validating the
    # cross-field matrix, so the matrix is checked against the model we
    # will actually send upstream. See the matching comment in
    # ``_proxy_images_generation_request``.
    settings = proxy_service_module.get_settings()
    requested_model = payload.model
    effective_model = _effective_model_for_api_key(
        api_key,
        requested_model or settings.images_default_model,
    )
    if not images_service_module.is_supported_image_model(effective_model):
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                f"Effective model '{effective_model}' is not a 'gpt-image-*' model. "
                f"This API key is pinned to '{effective_model}' which cannot be used on "
                f"/v1/images/* routes; use a key that allows gpt-image models.",
                param="model",
            ),
        )
    if effective_model != requested_model:
        payload = payload.model_copy(update={"model": effective_model})

    try:
        payload = images_service_module.validate_edits_payload(payload)
    except ClientPayloadError as exc:
        return _logged_error_json_response(request, 400, openai_client_payload_error(exc))

    public_model = payload.model
    assert public_model is not None
    host_model = settings.images_host_model

    validate_model_access(api_key, effective_model)

    rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
    reservation = await _enforce_request_limits(
        api_key,
        request_model=effective_model,
        request_service_tier=None,
    )

    try:
        responses_payload = images_service_module.images_edit_to_responses_request(
            payload,
            host_model=host_model,
            images=images,
            mask=mask,
        )
    except (ValidationError, ValueError) as exc:
        await _release_reservation(reservation)
        if isinstance(exc, ValidationError):
            return _logged_error_json_response(
                request,
                400,
                openai_validation_error(exc),
                headers=rate_limit_headers,
            )
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(str(exc)),
            headers=rate_limit_headers,
        )

    # See ``_proxy_images_generation_request`` for why we pass
    # ``api_key_reservation=None`` and finalize via
    # ``_finalize_image_reservation`` instead.
    upstream = context.service.stream_responses(
        responses_payload,
        request.headers,
        codex_session_affinity=False,
        propagate_http_errors=True,
        openai_cache_affinity=True,
        api_key=api_key,
        api_key_reservation=None,
        client_ip=resolve_request_client_host(request),
    )

    captured: dict[str, object] = {}

    primed_upstream, prime_error = await _prime_upstream_stream(
        request,
        upstream,
        rate_limit_headers,
        on_error=lambda: _release_reservation(reservation),
    )
    if prime_error is not None:
        return prime_error
    assert primed_upstream is not None

    if payload.stream:
        translated = images_service_module.translate_responses_stream_to_images_stream(
            primed_upstream, captured=captured, is_edit=True
        )

        async def _stream_with_log_rewrite() -> AsyncIterator[bytes]:
            try:
                async for chunk in translated:
                    yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            finally:
                # Run the request-log model rewrite even when the stream
                # is cancelled mid-flight (e.g. client disconnect). Without
                # this, an interrupted SSE response would leave the
                # request_logs row pinned to the internal host model.
                response_id = captured.get("response_id")
                if response_id and isinstance(response_id, str):
                    await context.service.rewrite_request_log_model(response_id, public_model)
                # Finalize the reservation from the captured
                # ``tool_usage.image_gen`` tokens (or release if
                # upstream never produced a usable image). This is the
                # single point where the image API charges API-key
                # limits; standard stream settlement is bypassed via
                # ``api_key_reservation=None`` above.
                _input = captured.get("image_input_tokens")
                _output = captured.get("image_output_tokens")
                _cached = captured.get("image_cached_input_tokens")
                await _finalize_image_reservation(
                    reservation,
                    model=public_model,
                    input_tokens=_input if isinstance(_input, int) else None,
                    output_tokens=_output if isinstance(_output, int) else None,
                    cached_input_tokens=_cached if isinstance(_cached, int) else None,
                )

        return StreamingResponse(
            _stream_with_log_rewrite(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        response_payload, error_envelope = await images_service_module.collect_responses_stream_for_images(
            primed_upstream,
            captured=captured,
        )
    except ProxyResponseError as exc:
        await _release_reservation(reservation)
        return _logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=rate_limit_headers,
        )

    response_id = captured.get("response_id")
    if response_id and isinstance(response_id, str):
        await context.service.rewrite_request_log_model(response_id, public_model)
    _input = captured.get("image_input_tokens")
    _output = captured.get("image_output_tokens")
    _cached = captured.get("image_cached_input_tokens")
    await _finalize_image_reservation(
        reservation,
        model=public_model,
        input_tokens=_input if isinstance(_input, int) else None,
        output_tokens=_output if isinstance(_output, int) else None,
        cached_input_tokens=_cached if isinstance(_cached, int) else None,
    )

    if error_envelope is not None:
        return _logged_error_json_response(
            request,
            _status_for_image_error_envelope(error_envelope),
            error_envelope,
            headers=rate_limit_headers,
        )
    assert response_payload is not None
    images_result = images_service_module.images_response_from_responses(response_payload)
    if not isinstance(images_result, V1ImageResponse):
        return _logged_error_json_response(
            request,
            _status_for_image_error_envelope(images_result),
            images_result,
            headers=rate_limit_headers,
        )
    return JSONResponse(
        content=images_result.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )


async def _build_codex_models_response(api_key: ApiKeyData | None) -> Response:
    reservation = await _enforce_request_limits(
        api_key,
        request_model=None,
        request_service_tier=None,
    )

    allowed_models = _allowed_models_for_api_key(api_key)
    visibility_allowed_models = _codex_model_visibility_allowed_models(api_key)

    registry = get_model_registry()
    models = registry.get_models_with_fallback()

    if not models:
        await _release_reservation(reservation)
        return JSONResponse(content=CodexModelsResponse(models=[], data=[]).model_dump(mode="json"))

    entries: list[CodexModelEntry] = []
    data: list[ModelListItem] = []
    for slug, model in models.items():
        if not model.supported_in_api:
            continue
        if visibility_allowed_models is None:
            if not is_public_model(model, allowed_models):
                continue
            entry = _to_codex_model_entry(model)
            entries.append(entry)
            if entry.visibility == "list":
                data.append(_to_model_list_item(slug, model, created=_model_list_created_at(model)))
            continue
        entry = _to_codex_model_entry(
            model,
            visibility="list" if slug in visibility_allowed_models else "hide",
        )
        entries.append(entry)
        if entry.visibility == "list":
            data.append(_to_model_list_item(slug, model, created=_model_list_created_at(model)))
    await _release_reservation(reservation)
    return JSONResponse(content=CodexModelsResponse(models=entries, data=data).model_dump(mode="json"))


async def _build_models_response(api_key: ApiKeyData | None) -> Response:
    reservation = await _enforce_request_limits(
        api_key,
        request_model=None,
        request_service_tier=None,
    )

    allowed_models = _allowed_models_for_api_key(api_key)
    created = int(time.time())

    registry = get_model_registry()
    models = registry.get_models_with_fallback()

    if not models:
        await _release_reservation(reservation)
        return JSONResponse(content=_dump_v1_models_response(ModelListResponse(data=[])))

    items: list[ModelListItem] = []
    for slug, model in models.items():
        if not is_public_model(model, allowed_models):
            continue
        items.append(_to_model_list_item(slug, model, created=created))
    await _release_reservation(reservation)
    return JSONResponse(content=_dump_v1_models_response(ModelListResponse(data=items)))


def _dump_v1_models_response(response: ModelListResponse) -> dict[str, JsonValue]:
    payload = response.model_dump(mode="json")
    for item in payload["data"]:
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        for key in ("additional_speed_tiers", "service_tiers", "default_service_tier"):
            if metadata.get(key) is None:
                metadata.pop(key, None)
    return payload


def _allowed_models_for_api_key(api_key: ApiKeyData | None) -> set[str] | None:
    allowed_models = _canonical_model_set(api_key.allowed_models) if api_key and api_key.allowed_models else None
    if api_key and api_key.enforced_model:
        forced = {_canonical_model_slug(api_key.enforced_model)}
        return forced if allowed_models is None else (allowed_models & forced)
    return allowed_models


def _canonical_model_set(models: Iterable[str]) -> set[str]:
    return {_canonical_model_slug(model) for model in models}


def _canonical_model_slug(model: str) -> str:
    return resolve_model_alias(model) or model


def _to_model_list_item(slug: str, model: UpstreamModel, *, created: int) -> ModelListItem:
    return ModelListItem.model_validate(
        {
            "id": slug,
            "created": created,
            "owned_by": "codex-lb",
            "metadata": _to_model_metadata(model),
            "api_types": ["chat_completions"],
            "capabilities": _v1_model_capabilities(model),
            "context_length": _v1_input_context_window(model),
            "contextLength": _v1_input_context_window(model),
            "max_output_tokens": _v1_max_output_tokens(model),
            "maxOutputTokens": _v1_max_output_tokens(model),
            "supports_reasoning": _v1_supports_reasoning(model),
            "supportsReasoning": _v1_supports_reasoning(model),
            "supports_images": _v1_supports_vision(model),
            "supportsImages": _v1_supports_vision(model),
            "supports_vision": _v1_supports_vision(model),
            "supportsVision": _v1_supports_vision(model),
        }
    )


def _model_list_created_at(model: UpstreamModel) -> int:
    for key in ("created", "created_at", "createdAt"):
        raw_value = model.raw.get(key)
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, float):
            return int(raw_value)
    return 0


def _codex_model_visibility_allowed_models(api_key: ApiKeyData | None) -> set[str] | None:
    if api_key is None or not api_key.apply_to_codex_model or not api_key.allowed_models:
        return None
    return _allowed_models_for_api_key(api_key)


def _to_codex_model_entry(model: UpstreamModel, *, visibility: str | None = None) -> CodexModelEntry:
    raw = model.raw

    extra: dict[str, JsonValue] = {}
    skip_keys = {
        "slug",
        "display_name",
        "description",
        "base_instructions",
        "default_reasoning_level",
        "supported_reasoning_levels",
        "supported_in_api",
        "priority",
        "minimal_client_version",
        "supports_reasoning_summaries",
        "support_verbosity",
        "default_verbosity",
        "supports_parallel_tool_calls",
        "context_window",
        "input_modalities",
        "available_in_plans",
        "prefer_websockets",
        "visibility",
    }
    for key, value in raw.items():
        if key not in skip_keys and isinstance(value, (bool, int, float, str, type(None), list, Mapping)):
            extra[key] = value

    # If context_window is overridden, also override max_context_window to match
    effective_cw = _effective_context_window(model)
    if effective_cw != model.context_window and "max_context_window" in extra:
        extra["max_context_window"] = effective_cw

    return CodexModelEntry(
        slug=model.slug,
        display_name=model.display_name,
        description=model.description,
        base_instructions=model.base_instructions,
        default_reasoning_level=model.default_reasoning_level,
        supported_reasoning_levels=[
            ReasoningLevelSchema(effort=rl.effort, description=rl.description)
            for rl in model.supported_reasoning_levels
        ],
        supported_in_api=model.supported_in_api,
        priority=model.priority,
        minimal_client_version=model.minimal_client_version,
        supports_reasoning_summaries=model.supports_reasoning_summaries,
        support_verbosity=model.support_verbosity,
        default_verbosity=model.default_verbosity,
        supports_parallel_tool_calls=model.supports_parallel_tool_calls,
        context_window=_effective_context_window(model),
        input_modalities=list(model.input_modalities),
        available_in_plans=sorted(model.available_in_plans),
        prefer_websockets=model.prefer_websockets,
        visibility=visibility or _model_visibility(model),
        **extra,
    )


def _effective_context_window(model: UpstreamModel) -> int:
    overrides = get_settings().model_context_window_overrides
    return overrides.get(model.slug, model.context_window)


def _v1_full_context_window(model: UpstreamModel) -> int:
    overrides = get_settings().model_context_window_overrides
    return overrides.get(model.slug, model.context_window)


def _v1_input_context_window(model: UpstreamModel) -> int:
    return model.context_window


def _v1_max_output_tokens(model: UpstreamModel) -> int | None:
    return _V1_MAX_OUTPUT_TOKEN_OVERRIDES.get(model.slug)


def _v1_model_capabilities(model: UpstreamModel) -> dict[str, JsonValue]:
    return {
        "context_length": _v1_input_context_window(model),
        "max_output_tokens": _v1_max_output_tokens(model),
        "supports_reasoning": _v1_supports_reasoning(model),
        "supports_images": _v1_supports_vision(model),
        "supportsImages": _v1_supports_vision(model),
        "supports_vision": _v1_supports_vision(model),
        "supports_tool_use": True,
        "supports_streaming": True,
        "input_modalities": list(model.input_modalities),
        "output_modalities": ["text"],
    }


def _v1_supports_reasoning(model: UpstreamModel) -> bool:
    return bool(model.supported_reasoning_levels) or model.supports_reasoning_summaries


def _v1_supports_vision(model: UpstreamModel) -> bool:
    return "image" in model.input_modalities


def _model_visibility(model: UpstreamModel) -> str:
    visibility = model.raw.get("visibility")
    return visibility if isinstance(visibility, str) else "list"


def _to_model_metadata(model: UpstreamModel) -> ModelMetadata:
    return ModelMetadata(
        display_name=model.display_name,
        description=model.description,
        context_window=_v1_full_context_window(model),
        input_context_window=_v1_input_context_window(model),
        max_output_tokens=_v1_max_output_tokens(model),
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
        additional_speed_tiers=_raw_string_list(model.raw, "additional_speed_tiers"),
        service_tiers=_raw_object_list(model.raw, "service_tiers"),
        default_service_tier=_raw_optional_string(model.raw, "default_service_tier"),
    )


def _raw_string_list(raw: Mapping[str, JsonValue], key: str) -> list[str] | None:
    value = raw.get(key)
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str)]


def _raw_object_list(raw: Mapping[str, JsonValue], key: str) -> list[dict[str, JsonValue]] | None:
    value = raw.get(key)
    if not isinstance(value, list):
        return None
    return [dict(cast(Mapping[str, JsonValue], item)) for item in value if isinstance(item, Mapping)]


def _raw_optional_string(raw: Mapping[str, JsonValue], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) else None


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
    cursor_compat_client = _is_cursor_compat_client(request, api_key)
    effective_model = _effective_model_for_api_key(api_key, payload.model)
    validate_model_access(api_key, effective_model)

    rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
    try:
        responses_shaped_payload = not payload.messages and payload.input is not None
        if not responses_shaped_payload:
            # Validate strict function tool schemas against the *original* request
            # ``tools`` list before ``to_responses_request()`` runs. The chat
            # normalizer (``_normalize_chat_tools``) silently drops invalid
            # entries (non-dict tools, function tools with missing/empty
            # ``name``), so validating the normalized output would surface
            # ``tools[i].function.parameters`` with an ``i`` that no longer maps
            # to the client's inbound payload. Using ``payload.tools`` keeps the
            # error envelope's ``param`` aligned with what the client sent.
            enforce_strict_function_tools_format(
                payload.tools,
                param_template="tools[{index}].function.parameters",
                nested=True,
            )
        responses_payload = payload.to_responses_request()
        enforce_strict_text_format(responses_payload)
        if responses_shaped_payload:
            enforce_strict_function_tools_format(responses_payload.tools)
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return _logged_error_json_response(request, 400, error, headers=rate_limit_headers)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return _logged_error_json_response(request, 400, error, headers=rate_limit_headers)
    apply_api_key_enforcement(responses_payload, api_key)
    admission_denial = await _opportunistic_admission_denial(request, context, api_key, model=responses_payload.model)
    if admission_denial is not None:
        return admission_denial
    reservation = await _enforce_request_limits(
        api_key,
        request_model=responses_payload.model,
        request_service_tier=responses_payload.service_tier,
        request_usage_budget=estimate_api_key_request_usage(responses_payload),
    )
    responses_payload.stream = True
    stream = context.service.stream_responses(
        responses_payload,
        request.headers,
        codex_session_affinity=False,
        propagate_http_errors=True,
        openai_cache_affinity=True,
        api_key=api_key,
        api_key_reservation=reservation,
        suppress_text_done_events=True,
        client_ip=resolve_request_client_host(request),
    )
    startup_probe_timeout = (
        _CURSOR_CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS
        if cursor_compat_client
        else _CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS
    )
    stream, startup_error = await _probe_chat_stream_startup_error(stream, timeout_seconds=startup_probe_timeout)
    if startup_error is not None:
        if cursor_compat_client and _is_context_length_startup_error(startup_error):
            await _release_reservation(reservation)
            if payload.stream:
                return _cursor_context_limit_usage_stream(
                    payload,
                    headers=rate_limit_headers,
                )
            return _cursor_context_limit_usage_completion(
                payload,
                headers=rate_limit_headers,
            )
        return _stream_startup_error_response(request, startup_error, headers=rate_limit_headers)
    if payload.stream:
        stream_options = payload.stream_options
        include_usage = cursor_compat_client or bool(stream_options and stream_options.include_usage)
        chat_stream = stream_chat_chunks(
            _stream_proxy_errors_as_response_failed(stream),
            model=responses_payload.model,
            include_usage=include_usage,
        )
        if cursor_compat_client:
            chat_stream = _stream_with_cursor_usage_fallback(chat_stream, payload)
        return StreamingResponse(
            inject_sse_keepalives(
                chat_stream,
                get_settings().sse_keepalive_interval_seconds,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        first = None
    except ProxyResponseError as exc:
        return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)

    stream_with_first = _prepend_first(first, stream)
    result = await collect_chat_completion(stream_with_first, model=responses_payload.model)
    if isinstance(result, OpenAIErrorEnvelopeModel):
        error = result.error
        code = error.code if error else None
        status_code = 503 if code in _UNAVAILABLE_SELECTION_ERROR_CODES else 502
        return _logged_error_json_response(
            request,
            status_code,
            content=result.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    if cursor_compat_client and isinstance(result, ChatCompletion):
        _apply_cursor_usage_fallback(result, payload, source="non_stream")
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
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
    suppress_text_done_events: bool = False,
    prefer_http_bridge: bool = False,
    skip_limit_enforcement: bool = False,
    api_key_reservation_override: ApiKeyUsageReservationData | None = None,
    include_rate_limit_headers: bool = True,
    forwarded_request: bool = False,
    forwarded_headers: Mapping[str, str] | None = None,
    forwarded_downstream_turn_state: str | None = None,
    forwarded_affinity_kind: str | None = None,
    forwarded_affinity_key: str | None = None,
    forwarded_client_ip: str | None = None,
    enforce_openai_sdk_contract: bool = True,
) -> Response:
    apply_api_key_enforcement(payload, api_key)
    validate_model_access(api_key, payload.model)
    admission_denial = await _opportunistic_admission_denial(request, context, api_key, model=payload.model)
    if admission_denial is not None:
        return admission_denial
    compact_trigger_input: list[JsonValue] | None = None
    if codex_session_affinity:
        try:
            compact_trigger_input = strip_terminal_compaction_trigger_input(payload)
        except ClientPayloadError as exc:
            error = openai_client_payload_error(exc)
            return _logged_error_json_response(request, 400, error)
    owns_reservation = api_key_reservation_override is None
    reservation = (
        api_key_reservation_override
        if skip_limit_enforcement
        else await _enforce_request_limits(
            api_key,
            request_model=payload.model,
            request_service_tier=payload.service_tier,
            request_usage_budget=estimate_api_key_request_usage(payload),
        )
    )

    rate_limit_headers = await _rate_limit_headers_for_request(context, api_key) if include_rate_limit_headers else {}
    bridge_active = prefer_http_bridge and proxy_service_module.get_settings().http_responses_session_bridge_enabled
    effective_headers = forwarded_headers or request.headers
    client_ip = forwarded_client_ip if forwarded_request else resolve_request_client_host(request)
    downstream_turn_state = (
        forwarded_downstream_turn_state
        if bridge_active and forwarded_downstream_turn_state is not None
        else proxy_affinity_module.ensure_http_downstream_turn_state(effective_headers)
        if bridge_active
        else None
    )
    turn_state_headers = (
        proxy_affinity_module.build_downstream_turn_state_response_headers(downstream_turn_state)
        if downstream_turn_state is not None
        else {}
    )
    if compact_trigger_input is not None:
        compact_payload_data = payload.model_dump(
            mode="json",
            include={
                "model",
                "instructions",
                "reasoning",
                "store",
                "service_tier",
                "prompt_cache_key",
            },
            exclude_none=True,
        )
        if isinstance(payload.model_extra, dict):
            prompt_cache_key_alias = payload.model_extra.get("promptCacheKey")
            if isinstance(prompt_cache_key_alias, str) and "prompt_cache_key" not in compact_payload_data:
                compact_payload_data["prompt_cache_key"] = prompt_cache_key_alias
        compact_payload_data["input"] = compact_trigger_input
        if payload.previous_response_id is not None:
            compact_payload_data["previous_response_id"] = payload.previous_response_id
        if payload.conversation is not None:
            compact_payload_data["conversation"] = payload.conversation
        compact_payload = ResponsesCompactRequest.model_validate(compact_payload_data)
        try:
            try:
                compact_result = await context.service.compact_responses(
                    compact_payload,
                    effective_headers,
                    codex_session_affinity=codex_session_affinity,
                    openai_cache_affinity=openai_cache_affinity,
                    api_key=api_key,
                    api_key_reservation=reservation,
                    client_ip=client_ip,
                )
            except NotImplementedError:
                error = OpenAIErrorEnvelopeModel(
                    error=OpenAIError(
                        message="responses/compact is not implemented",
                        type="server_error",
                        code="not_implemented",
                    )
                )
                return _logged_error_json_response(
                    request,
                    501,
                    error.model_dump(mode="json", exclude_none=True),
                    headers=rate_limit_headers,
                )
            except ProxyResponseError as exc:
                return _stream_startup_error_response(
                    request,
                    exc,
                    headers=rate_limit_headers,
                )
            compact_item = _compact_response_output_item(compact_result)
            if compact_item is None:
                error = openai_error(
                    "upstream_error",
                    "Compact response did not include a compaction output item",
                    error_type="server_error",
                )
                return _logged_error_json_response(request, 502, error, headers=rate_limit_headers)
            response_id = _compact_response_id(compact_result)
            stream = _synthetic_compaction_response_stream(
                compact_item,
                response_id=response_id,
                usage=compact_result.usage,
            )
            return StreamingResponse(
                stream,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                    **turn_state_headers,
                    **rate_limit_headers,
                },
            )
        finally:
            if owns_reservation:
                await _release_reservation(reservation)
    payload.stream = True
    if prefer_http_bridge:
        stream = context.service.stream_http_responses(
            payload,
            effective_headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
            downstream_turn_state=downstream_turn_state,
            forwarded_request=forwarded_request,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
            client_ip=client_ip,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
        )
    else:
        stream = context.service.stream_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
            client_ip=client_ip,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
        )
    stream, startup_error = await _probe_stream_startup_error(
        stream,
        convert_event_errors=bridge_active and enforce_openai_sdk_contract,
        timeout_seconds=(
            _HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS if prefer_http_bridge else _STREAM_STARTUP_ERROR_PROBE_SECONDS
        ),
    )
    if startup_error is not None:
        if owns_reservation:
            await _release_reservation(reservation)
        return _stream_startup_error_response(
            request,
            startup_error,
            headers=rate_limit_headers,
        )
    stream = _normalize_public_responses_stream(
        _stream_response_error_events(
            stream,
            owns_reservation=owns_reservation,
            reservation=reservation,
        ),
        enforce_openai_sdk_contract=enforce_openai_sdk_contract,
    )
    keepalive_frame = CODEX_KEEPALIVE_FRAME if not enforce_openai_sdk_contract else SSE_KEEPALIVE_FRAME
    if not enforce_openai_sdk_contract:
        stream = _prepend_initial_sse_heartbeat(
            stream,
            keepalive_frame,
            request_id=get_request_id(),
            route_family="responses",
        )
    return StreamingResponse(
        inject_sse_keepalives(
            stream,
            get_settings().sse_keepalive_interval_seconds,
            keepalive_frame=keepalive_frame,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            **turn_state_headers,
            **rate_limit_headers,
        },
    )


def _strip_internal_bridge_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if not key.lower().startswith("x-codex-bridge-")}


async def _collect_responses(
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
    suppress_text_done_events: bool = False,
    prefer_http_bridge: bool = False,
) -> Response:
    apply_api_key_enforcement(payload, api_key)
    validate_model_access(api_key, payload.model)
    admission_denial = await _opportunistic_admission_denial(request, context, api_key, model=payload.model)
    if admission_denial is not None:
        return admission_denial
    reservation = await _enforce_request_limits(
        api_key,
        request_model=payload.model,
        request_service_tier=payload.service_tier,
        request_usage_budget=estimate_api_key_request_usage(payload),
    )

    rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
    bridge_active = prefer_http_bridge and proxy_service_module.get_settings().http_responses_session_bridge_enabled
    downstream_turn_state = (
        proxy_affinity_module.ensure_http_downstream_turn_state(request.headers) if bridge_active else None
    )
    client_ip = resolve_request_client_host(request)
    turn_state_headers = (
        proxy_affinity_module.build_downstream_turn_state_response_headers(downstream_turn_state)
        if downstream_turn_state is not None
        else {}
    )
    payload.stream = True
    if prefer_http_bridge:
        stream = context.service.stream_http_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
            downstream_turn_state=downstream_turn_state,
            client_ip=client_ip,
        )
    else:
        stream = context.service.stream_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
            client_ip=client_ip,
        )
    try:
        response_payload = await _collect_responses_payload(stream)
    except ProxyResponseError as exc:
        await _release_reservation(reservation)
        error = _parse_error_envelope(exc.payload)
        status_code, error = _mask_previous_response_not_found_error(error, default_status=exc.status_code)
        return _logged_error_json_response(
            request,
            status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    if isinstance(response_payload, OpenAIResponsePayload):
        if response_payload.status == "failed":
            error_payload = _error_envelope_from_response(response_payload.error)
            status_code, error_payload = _mask_previous_response_not_found_error(error_payload)
            return _logged_error_json_response(
                request,
                status_code,
                error_payload.model_dump(mode="json", exclude_none=True),
                headers={**turn_state_headers, **rate_limit_headers},
            )
        return JSONResponse(
            content=response_payload.model_dump(mode="json", exclude_none=True),
            headers={**turn_state_headers, **rate_limit_headers},
        )
    status_code, response_payload = _mask_previous_response_not_found_error(response_payload)
    return _logged_error_json_response(
        request,
        status_code,
        response_payload.model_dump(mode="json", exclude_none=True),
        headers={**turn_state_headers, **rate_limit_headers},
    )


@router.post("/responses/compact", response_model=CompactResponseResult)
async def responses_compact(
    request: Request,
    payload: ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    return await _compact_responses(
        request, payload, context, api_key, codex_session_affinity=True, openai_cache_affinity=True
    )


@v1_router.post("/responses/compact", response_model=CompactResponseResult)
async def v1_responses_compact(
    request: Request,
    payload: V1ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    try:
        compact_payload = payload.to_compact_request()
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return _logged_error_json_response(request, 400, error)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return _logged_error_json_response(request, 400, error)
    return await _compact_responses(
        request,
        compact_payload,
        context,
        api_key,
        codex_session_affinity=False,
        openai_cache_affinity=True,
    )


async def _compact_responses(
    request: Request,
    payload: ResponsesCompactRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
) -> JSONResponse:
    apply_api_key_enforcement(payload, api_key)
    validate_model_access(api_key, payload.model)
    admission_denial = await _opportunistic_admission_denial(
        request,
        context,
        api_key,
        model=payload.model,
        lease_kind="response_create",
    )
    if admission_denial is not None:
        return admission_denial
    reservation = await _enforce_request_limits(
        api_key,
        request_model=payload.model,
        request_service_tier=_compact_request_service_tier(payload),
        request_usage_budget=estimate_api_key_request_usage(payload),
    )

    rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
    try:
        result = await context.service.compact_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            client_ip=resolve_request_client_host(request),
        )
    except NotImplementedError:
        error = OpenAIErrorEnvelopeModel(
            error=OpenAIError(
                message="responses/compact is not implemented",
                type="server_error",
                code="not_implemented",
            )
        )
        return _logged_error_json_response(
            request,
            501,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    except ProxyResponseError as exc:
        error = _parse_error_envelope(exc.payload)
        status_code, error = _mask_previous_response_not_found_error(error, default_status=exc.status_code)
        return _logged_error_json_response(
            request,
            status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    finally:
        await _release_reservation(reservation)
    result_payload = result.model_dump(mode="json", exclude_none=True)
    if codex_session_affinity:
        result_payload = _normalize_codex_remote_compaction_v2_result(result, result_payload)
    return JSONResponse(
        content=result_payload,
        headers=rate_limit_headers,
    )


def _normalize_codex_remote_compaction_v2_result(
    payload: CompactResponsePayload,
    result_payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    compaction_item = _compact_response_output_item(payload)
    if compaction_item is None:
        return result_payload
    normalized = dict(result_payload)
    normalized["output"] = [compaction_item]
    return normalized


def _compact_response_output_item(payload: CompactResponsePayload) -> dict[str, JsonValue] | None:
    extra = payload.model_extra or {}
    output = getattr(payload, "output", None)
    if output is None:
        output = extra.get("output")
    if isinstance(output, list):
        for raw_item in output:
            item = _json_mapping_from_model_or_mapping(raw_item)
            if item is None:
                continue
            item_type = item.get("type")
            encrypted_content = item.get("encrypted_content")
            if isinstance(item_type, str) and item_type in {"compaction", "compaction_summary"}:
                if isinstance(encrypted_content, str):
                    return {
                        "type": "compaction",
                        "encrypted_content": encrypted_content,
                    }
    summary = getattr(payload, "compaction_summary", None)
    if summary is None:
        summary = extra.get("compaction_summary")
    summary_mapping = _json_mapping_from_model_or_mapping(summary)
    if summary_mapping is not None:
        encrypted_content = summary_mapping.get("encrypted_content")
        if isinstance(encrypted_content, str):
            return {
                "type": "compaction",
                "encrypted_content": encrypted_content,
            }
    return None


def _json_mapping_from_model_or_mapping(value: object) -> Mapping[str, JsonValue] | None:
    if is_json_mapping(value):
        return value
    if hasattr(value, "model_dump"):
        dumped = cast(Any, value).model_dump(mode="json", exclude_none=True)
        if is_json_mapping(dumped):
            return dumped
    return None


def _compact_response_id(payload: CompactResponsePayload) -> str:
    if payload.id:
        return payload.id
    request_id = get_request_id()
    if request_id:
        return f"resp_{request_id}"
    return f"resp_{uuid4().hex}"


async def _synthetic_compaction_response_stream(
    compact_item: Mapping[str, JsonValue],
    *,
    response_id: str,
    usage: object | None,
) -> AsyncIterator[str]:
    completed_response: dict[str, JsonValue] = {
        "id": response_id,
        "object": "response",
        "status": "completed",
        "output": [dict(compact_item)],
    }
    usage_mapping = _json_mapping_from_model_or_mapping(usage)
    if usage_mapping is not None:
        completed_response["usage"] = dict(usage_mapping)
    yield format_sse_event(
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": dict(compact_item),
        }
    )
    yield format_sse_event(
        {
            "type": "response.completed",
            "response": completed_response,
        }
    )
    yield "data: [DONE]\n\n"


async def _transcribe_request(
    *,
    request: Request,
    file: UploadFile,
    prompt: str | None,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> JSONResponse:
    validate_model_access(api_key, _TRANSCRIPTION_MODEL)
    reservation = await _enforce_request_limits(
        api_key,
        request_model=_TRANSCRIPTION_MODEL,
        request_service_tier=None,
    )
    rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
    try:
        audio_bytes = await file.read()
        result = await context.service.transcribe(
            audio_bytes=audio_bytes,
            filename=file.filename or "audio.wav",
            content_type=file.content_type,
            prompt=prompt,
            headers=request.headers,
            api_key=api_key,
        )
    except ProxyResponseError as exc:
        error = _parse_error_envelope(exc.payload)
        return _logged_error_json_response(
            request,
            exc.status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    finally:
        await _release_reservation(reservation)
    return JSONResponse(content=result, headers=rate_limit_headers)


@usage_router.get("/api/codex/usage", response_model=RateLimitStatusPayload)
@usage_router.get("/api/codex/usage/", response_model=RateLimitStatusPayload, include_in_schema=False)
async def codex_usage(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Depends(validate_codex_usage_identity),
) -> RateLimitStatusPayload:
    payload = (
        await _build_codex_usage_payload_for_api_key(api_key)
        if api_key is not None
        else _attach_codex_usage_reset_credits(await context.service.get_rate_limit_payload(), request)
    )
    return RateLimitStatusPayload.from_data(payload)


@usage_router.post(
    "/api/codex/rate-limit-reset-credits/consume",
    response_model=ConsumeRateLimitResetCreditResponse,
)
@usage_router.post(
    "/api/codex/rate-limit-reset-credits/consume/",
    response_model=ConsumeRateLimitResetCreditResponse,
    include_in_schema=False,
)
async def codex_consume_rate_limit_reset_credit(
    request: Request,
    payload: ConsumeRateLimitResetCreditRequest = Body(...),
    api_key: ApiKeyData | None = Depends(validate_codex_usage_identity),
) -> ConsumeRateLimitResetCreditResponse | JSONResponse:
    if api_key is not None:
        raise ProxyAuthError("ChatGPT authentication required for usage limit reset credits")
    redeem_request_id = payload.redeem_request_id.strip()
    if not redeem_request_id:
        return _logged_error_json_response(
            request,
            400,
            openai_error(
                "invalid_request_error",
                "redeem_request_id must not be empty",
                error_type="invalid_request_error",
            ),
        )

    upstream_response = await _consume_rate_limit_reset_credit_for_request(
        request,
        redeem_request_id=redeem_request_id,
    )
    account_id = _request_state_str(request, "codex_usage_identity_account_id")
    if account_id is not None:
        await get_rate_limit_reset_credits_store().invalidate(account_id)
    if upstream_response.code in {"reset", "already_redeemed"}:
        await _force_refresh_codex_usage_identity_account(request)
    return ConsumeRateLimitResetCreditResponse.model_validate(upstream_response.model_dump())


async def _consume_rate_limit_reset_credit_for_request(
    request: Request,
    *,
    redeem_request_id: str,
) -> UpstreamConsumeRateLimitResetCreditResponse:
    access_token = _request_state_str(request, "codex_usage_identity_access_token")
    chatgpt_account_id = _request_state_str(request, "codex_usage_identity_chatgpt_account_id")
    if access_token is None or chatgpt_account_id is None:
        raise ProxyAuthError("ChatGPT authentication required for usage limit reset credits")
    route = getattr(request.state, "codex_usage_identity_route", None)
    try:
        return await consume_rate_limit_reset_credit(
            access_token=access_token,
            account_id=chatgpt_account_id,
            redeem_request_id=redeem_request_id,
            route=route,
            allow_direct_egress=route is None,
        )
    except UsageFetchError as exc:
        if exc.status_code == 429:
            raise ProxyRateLimitError(exc.message) from exc
        if exc.status_code in (401, 403):
            raise ProxyAuthError("Invalid ChatGPT token or chatgpt-account-id") from exc
        raise ProxyUpstreamError("Unable to consume ChatGPT usage reset at this time") from exc


async def _force_refresh_codex_usage_identity_account(request: Request) -> None:
    account_id = _request_state_str(request, "codex_usage_identity_account_id")
    if account_id is None:
        return
    access_token = _request_state_str(request, "codex_usage_identity_access_token")
    async with get_background_session() as session:
        accounts_repo = AccountsRepository(session)
        account = await accounts_repo.get_by_id(account_id)
        if account is None:
            return
        updater = UsageUpdater(
            UsageRepository(session),
            accounts_repo,
            AdditionalUsageRepository(session),
        )
        usage_written = await updater.force_refresh(
            account,
            ignore_refresh_disabled=True,
            access_token_override=access_token,
        )
        if usage_written:
            get_account_selection_cache().invalidate()


def _request_state_str(request: Request, name: str) -> str | None:
    value = getattr(request.state, name, None)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


async def _prepend_first(first: str | None, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    if first is not None:
        yield first
    async for line in stream:
        yield line


async def _read_first_stream_item(stream: AsyncIterator[str]) -> str:
    return await anext(stream)


def _retrieve_first_stream_task_exception(task: asyncio.Task[str]) -> None:
    # Retrieve a finished probe task's exception so an abandoned task does not
    # surface asyncio's "exception was never retrieved" warning. Consumers that
    # await the task still re-raise it.
    if not task.cancelled():
        task.exception()


def _create_first_stream_probe_task(stream: AsyncIterator[str]) -> asyncio.Task[str]:
    """Create the first-stream-item probe task.

    ``_probe_stream_startup_error`` / ``_probe_chat_stream_startup_error`` race
    this task against a timeout. On timeout the task keeps running and is handed
    to the streamed response for consumption. If the wrapping stream is dropped
    before the task is awaited -- for example the request is torn down while the
    upstream is still blocked on the response-create admission gate -- the task
    would otherwise finish with an unretrieved ``ProxyResponseError`` and asyncio
    would log it. The done-callback retrieves the result in that abandoned case
    without hiding the error from consumers that do await the task.
    """
    task = asyncio.create_task(_read_first_stream_item(stream))
    task.add_done_callback(_retrieve_first_stream_task_exception)
    return task


async def _probe_stream_startup_error(
    stream: AsyncIterator[str],
    *,
    convert_event_errors: bool = False,
    timeout_seconds: float | None = None,
) -> tuple[AsyncIterator[str], ProxyResponseError | OpenAIErrorEnvelopeModel | None]:
    if timeout_seconds is None:
        timeout_seconds = _STREAM_STARTUP_ERROR_PROBE_SECONDS
    first_task = _create_first_stream_probe_task(stream)
    done, _pending = await asyncio.wait({first_task}, timeout=timeout_seconds)
    if not done:
        # Probe window elapsed before the first item arrived. Hand the still-
        # running task off to be consumed by the streamed response. asyncio.wait
        # (rather than wait_for + shield) never cancels the task on timeout,
        # avoiding the Python 3.14 "exception in shielded future" log when the
        # upstream later returns an error such as a 429 from the admission gate.
        return _prepend_first_task(first_task, stream), None
    try:
        first = first_task.result()
    except StopAsyncIteration:
        return _prepend_first(None, stream), None
    except ProxyResponseError as exc:
        return _prepend_first(None, stream), exc
    if convert_event_errors:
        first_error = _stream_event_error_envelope(first)
        if first_error is not None:
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                await aclose()
            return _prepend_first(None, stream), first_error
    return _prepend_first(first, stream), None


_CHAT_COMPLETIONS_STARTUP_EVENT_TYPES: Final[set[str]] = {
    "response.created",
    "response.in_progress",
}


def _is_cursor_compat_client(request: Request, api_key: ApiKeyData | None) -> bool:
    if api_key is not None and api_key.name.strip().lower() == "cursor":
        return True
    user_agent = request.headers.get("user-agent", "")
    return "cursor" in user_agent.lower()


def _is_context_length_startup_error(error: ProxyResponseError | OpenAIErrorEnvelopeModel) -> bool:
    code, message = _startup_error_details(error)
    if code == "context_length_exceeded":
        return True
    if message is None:
        return False
    normalized = message.lower()
    return (
        "context window" in normalized
        or "input token limit exceeded" in normalized
        or "token limit exceeded" in normalized
    )


def _startup_error_details(error: ProxyResponseError | OpenAIErrorEnvelopeModel) -> tuple[str | None, str | None]:
    if isinstance(error, ProxyResponseError):
        return _error_details_from_content(error.payload)
    return _error_details_from_content(error)


def _cursor_context_limit_usage_stream(
    payload: ChatCompletionsRequest,
    *,
    headers: Mapping[str, str] | None = None,
) -> StreamingResponse:
    """Return a successful empty stream with over-limit usage so Cursor can compact.

    Cursor's custom-provider path wraps provider errors before the agent loop can
    classify them as its internal InputTokenLimitError. For Cursor only, preserve
    the original request history and report token usage beyond the advertised
    model window instead of returning an OpenAI error.
    """
    response_id = f"chatcmpl_{time.time_ns()}"
    created = int(time.time())
    model = payload.model
    usage_tokens = _CURSOR_CONTEXT_LIMIT_SYNTHETIC_USAGE_TOKENS

    def sse_data(data: dict[str, JsonValue] | str) -> str:
        if data == "[DONE]":
            return "data: [DONE]\n\n"
        return f"data: {json.dumps(data, separators=(',', ':'))}\n\n"

    async def body() -> AsyncIterator[str]:
        yield sse_data(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None,
                    }
                ],
            }
        )
        yield sse_data(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
        yield sse_data(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": {
                    "prompt_tokens": usage_tokens,
                    "completion_tokens": 0,
                    "total_tokens": usage_tokens,
                },
            }
        )
        yield sse_data("[DONE]")

    return StreamingResponse(body(), media_type="text/event-stream", headers=headers)


def _cursor_context_limit_usage_completion(
    payload: ChatCompletionsRequest,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    response_id = f"chatcmpl_{time.time_ns()}"
    created = int(time.time())
    model = payload.model
    usage_tokens = _CURSOR_CONTEXT_LIMIT_SYNTHETIC_USAGE_TOKENS
    return JSONResponse(
        content={
            "id": response_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": usage_tokens,
                "completion_tokens": 0,
                "total_tokens": usage_tokens,
            },
        },
        status_code=200,
        headers=headers,
    )


async def _stream_with_cursor_usage_fallback(
    stream: AsyncIterator[str],
    payload: ChatCompletionsRequest,
) -> AsyncIterator[str]:
    prompt_tokens = _estimate_cursor_prompt_tokens(payload)
    completion_chars = 0
    async for line in stream:
        parsed = _parse_chat_completion_sse(line)
        if parsed is None:
            yield line
            continue
        completion_chars += _chat_completion_delta_chars(parsed)
        if _is_chat_completion_usage_chunk(parsed) and _needs_cursor_usage_fallback(parsed.get("usage")):
            completion_tokens = max(1, _estimate_tokens_from_chars(completion_chars))
            parsed["usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
            logger.info(
                "cursor_usage_fallback source=stream model=%s prompt_tokens=%s completion_tokens=%s",
                payload.model,
                prompt_tokens,
                completion_tokens,
            )
            yield f"data: {json.dumps(parsed, separators=(',', ':'))}\n\n"
            continue
        yield line


def _is_chat_completion_usage_chunk(payload: dict[str, JsonValue]) -> bool:
    return payload.get("choices") == []


def _parse_chat_completion_sse(line: str) -> dict[str, JsonValue] | None:
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None
    data = stripped.removeprefix("data:").strip()
    if data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _needs_cursor_usage_fallback(usage: JsonValue) -> bool:
    if not isinstance(usage, dict):
        return True
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    return not isinstance(prompt_tokens, int) or prompt_tokens <= 0 or not isinstance(completion_tokens, int)


def _apply_cursor_usage_fallback(
    result: ChatCompletion,
    payload: ChatCompletionsRequest,
    *,
    source: str,
) -> None:
    usage = result.usage.model_dump(mode="json", exclude_none=True) if result.usage is not None else None
    if not _needs_cursor_usage_fallback(usage):
        return
    prompt_tokens = _estimate_cursor_prompt_tokens(payload)
    completion_tokens = max(1, _estimate_tokens_from_chars(_chat_completion_result_chars(result)))
    result.usage = ChatCompletionUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    logger.info(
        "cursor_usage_fallback source=%s model=%s prompt_tokens=%s completion_tokens=%s",
        source,
        payload.model,
        prompt_tokens,
        completion_tokens,
    )


def _estimate_cursor_prompt_tokens(payload: ChatCompletionsRequest) -> int:
    data = payload.model_dump(mode="json", exclude_none=True)
    counted: dict[str, JsonValue] = {}
    for key in ("messages", "input", "instructions", "tools", "tool_choice", "response_format"):
        value = data.get(key)
        if value is not None:
            counted[key] = value
    message_count = len(data.get("messages", [])) if isinstance(data.get("messages"), list) else 0
    return max(1, _estimate_tokens_from_chars(_json_text_chars(counted)) + message_count * 4)


def _estimate_tokens_from_chars(chars: int) -> int:
    return (max(0, chars) + 3) // 4


def _json_text_chars(value: JsonValue) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_json_text_chars(item) for item in value)
    if isinstance(value, dict):
        return sum(_json_text_chars(item) for item in value.values())
    return 0


def _chat_completion_delta_chars(payload: dict[str, JsonValue]) -> int:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return 0
    total = 0
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        for key in ("content", "refusal"):
            value = delta.get(key)
            if isinstance(value, str):
                total += len(value)
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            total += _json_text_chars(tool_calls)
    return total


def _chat_completion_result_chars(result: ChatCompletion) -> int:
    total = 0
    for choice in result.choices:
        message = choice.message
        if isinstance(message.content, str):
            total += len(message.content)
        if isinstance(message.refusal, str):
            total += len(message.refusal)
        if message.tool_calls:
            total += _json_text_chars(
                [tool_call.model_dump(mode="json", exclude_none=True) for tool_call in message.tool_calls]
            )
    return total


async def _probe_chat_stream_startup_error(
    stream: AsyncIterator[str],
    *,
    timeout_seconds: float = _CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS,
    max_startup_events: int = 8,
) -> tuple[AsyncIterator[str], ProxyResponseError | OpenAIErrorEnvelopeModel | None]:
    buffered: list[str] = []
    for _ in range(max_startup_events):
        first_task = _create_first_stream_probe_task(stream)
        done, _pending = await asyncio.wait({first_task}, timeout=timeout_seconds)
        if not done:
            return _prepend_items(buffered, _prepend_first_task(first_task, stream)), None
        try:
            first = first_task.result()
        except StopAsyncIteration:
            return _prepend_items(buffered, _prepend_first(None, stream)), None
        except ProxyResponseError as exc:
            return _prepend_items(buffered, _prepend_first(None, stream)), exc

        first_error = _stream_event_error_envelope(first)
        if first_error is not None:
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                await aclose()
            return _prepend_first(None, stream), first_error

        payload = _parse_sse_payload(first)
        event_type = payload.get("type") if payload else None
        buffered.append(first)
        if event_type in _CHAT_COMPLETIONS_STARTUP_EVENT_TYPES:
            continue
        return _prepend_items(buffered, stream), None
    return _prepend_items(buffered, stream), None


async def _prepend_items(items: list[str], stream: AsyncIterator[str]) -> AsyncIterator[str]:
    for item in items:
        yield item
    async for line in stream:
        yield line


async def _prepend_first_task(first_task: asyncio.Task[str], stream: AsyncIterator[str]) -> AsyncIterator[str]:
    try:
        first = await first_task
    except StopAsyncIteration:
        return
    finally:
        # If the wrapping stream is closed before the first item is consumed
        # (client disconnect, request teardown), cancel the still-running probe
        # task so it does not hold the upstream connection open.
        if not first_task.done():
            first_task.cancel()
    yield first
    async for line in stream:
        yield line


async def _prepend_initial_sse_heartbeat(
    stream: AsyncIterator[str],
    keepalive_frame: str,
    *,
    request_id: str | None = None,
    route_family: str = "responses",
) -> AsyncIterator[str]:
    logger.info(
        "responses_stream_heartbeat request_id=%s route_family=%s stage=initial elapsed_seconds=0.000",
        request_id,
        route_family,
    )
    yield keepalive_frame
    async for line in stream:
        yield line


async def _stream_proxy_errors_as_response_failed(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    async for line in _stream_response_error_events(stream, owns_reservation=False, reservation=None):
        yield line


async def _stream_response_error_events(
    stream: AsyncIterator[str],
    *,
    owns_reservation: bool,
    reservation: ApiKeyUsageReservationData | None,
) -> AsyncIterator[str]:
    try:
        async for line in stream:
            yield line
    except ProxyResponseError as exc:
        if owns_reservation:
            try:
                await _release_reservation(reservation)
            except Exception:
                logger.warning("Failed to release stream reservation after upstream proxy error", exc_info=True)
        envelope = _parse_error_envelope(exc.payload)
        _, envelope = _mask_previous_response_not_found_error(envelope, default_status=exc.status_code)
        error = envelope.error
        yield format_sse_event(
            response_failed_event(
                error.code if error and error.code else "upstream_error",
                error.message if error and error.message else "Upstream error",
                error.type if error and error.type else "server_error",
                error_param=error.param if error else None,
            )
        )


def _stream_startup_error_response(
    request: Request,
    error: ProxyResponseError | OpenAIErrorEnvelopeModel,
    *,
    headers: Mapping[str, str],
) -> JSONResponse:
    if isinstance(error, ProxyResponseError):
        envelope = _parse_error_envelope(error.payload)
        status_code, envelope = _mask_previous_response_not_found_error(envelope, default_status=error.status_code)
        return _logged_error_json_response(
            request,
            status_code,
            envelope.model_dump(mode="json", exclude_none=True),
            headers=headers,
        )
    status_code, envelope = _mask_previous_response_not_found_error(error)
    return _logged_error_json_response(
        request,
        status_code,
        envelope.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


def _stream_event_error_envelope(event_block: str) -> OpenAIErrorEnvelopeModel | None:
    payload = _parse_sse_payload(event_block)
    if payload is None:
        return None
    event_type = payload.get("type")
    if event_type == "error":
        return _parse_event_error_envelope(payload)
    if event_type != "response.failed":
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return _default_error_envelope()
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


def _parse_sse_payload(line: str) -> dict[str, JsonValue] | None:
    return parse_sse_data_json(line)


def _logged_error_json_response(
    request: Request,
    status_code: int,
    content: Mapping[str, JsonValue] | OpenAIErrorEnvelopeModel | OpenAIErrorEnvelope,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    if isinstance(content, OpenAIErrorEnvelopeModel):
        public_content: Mapping[str, JsonValue] | OpenAIErrorEnvelope = content.model_dump(
            mode="json", exclude_none=True
        )
    else:
        public_content = content
    code, message = _error_details_from_content(public_content)
    effective_headers = dict(headers or {})
    if status_code == 429 and is_local_overload_error_code(code):
        effective_headers = merge_retry_after_headers(effective_headers)
    log_error_response(
        logger,
        request,
        status_code,
        code,
        message,
        category="proxy_error_response",
    )
    # codeql[py/stack-trace-exposure] This is an OpenAI-compatible proxy boundary:
    # upstream/provider error envelopes intentionally preserve diagnostics for
    # clients, while internal exception handlers construct generic error
    # envelopes before reaching this response helper.
    return JSONResponse(status_code=status_code, content=public_content, headers=effective_headers or None)


def _error_details_from_content(
    content: Mapping[str, JsonValue] | OpenAIErrorEnvelopeModel | OpenAIErrorEnvelope,
) -> tuple[str | None, str | None]:
    if isinstance(content, OpenAIErrorEnvelopeModel):
        error = content.error
        if error is None:
            return None, None
        return error.code, error.message
    if not isinstance(content, Mapping):
        return None, None
    error = content.get("error")
    if isinstance(error, str):
        details = content.get("details")
        message = details.get("detail") if is_json_mapping(details) else None
        return error, message if isinstance(message, str) else None
    if not is_json_mapping(error):
        return None, None
    error_mapping = error
    code = error_mapping.get("code")
    message = error_mapping.get("message")
    return code if isinstance(code, str) else None, message if isinstance(message, str) else None


async def _validate_proxy_api_key_authorization_for_connection(
    authorization: str | None,
    connection: Request | WebSocket,
) -> ApiKeyData | None:
    try:
        return await validate_proxy_api_key_authorization(authorization, request=connection)
    except TypeError as exc:
        if not _is_legacy_proxy_auth_override_type_error(exc):
            raise
    return await validate_proxy_api_key_authorization(authorization)


def _is_legacy_proxy_auth_override_type_error(exc: TypeError) -> bool:
    message = str(exc)
    return "unexpected keyword argument 'request'" in message


async def _validate_proxy_websocket_request(
    websocket: WebSocket,
) -> tuple[ApiKeyData | None, JSONResponse | None]:
    denial = await _websocket_firewall_denial_response(websocket)
    if denial is not None:
        return None, denial
    try:
        api_key = await _validate_proxy_api_key_authorization_for_connection(
            websocket.headers.get("authorization"),
            websocket,
        )
    except ProxyAuthError as exc:
        return None, JSONResponse(
            status_code=exc.status_code,
            content=openai_error(exc.code, exc.message, error_type=exc.error_type),
        )
    return api_key, None


async def _validate_internal_bridge_api_key(
    request: Request,
) -> tuple[ApiKeyData | None, JSONResponse | None]:
    dashboard_settings = await get_settings_cache().get()
    if not dashboard_settings.api_key_auth_enabled:
        return None, None
    try:
        api_key = await _validate_proxy_api_key_authorization_for_connection(
            request.headers.get("authorization"),
            request,
        )
    except ProxyAuthError as exc:
        return None, JSONResponse(
            status_code=exc.status_code,
            content=openai_error(exc.code, exc.message, error_type=exc.error_type),
        )
    return api_key, None


async def _websocket_firewall_denial_response(websocket: WebSocket) -> JSONResponse | None:
    settings = get_settings()
    client_ip = resolve_connection_client_ip(
        websocket.headers,
        websocket.client.host if websocket.client else None,
        trust_proxy_headers=settings.firewall_trust_proxy_headers,
        trusted_proxy_networks=_parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs),
    )
    async with get_background_session() as session:
        repository = cast(FirewallRepositoryPort, FirewallRepository(session))
        service = FirewallService(repository)
        if await service.is_ip_allowed(client_ip):
            return None
    return JSONResponse(
        status_code=403,
        content=openai_error("ip_forbidden", "Access denied for client IP", error_type="access_error"),
    )


async def _enforce_request_limits(
    api_key: ApiKeyData | None,
    *,
    request_model: str | None,
    request_service_tier: str | None,
    request_usage_budget: ApiKeyRequestUsageBudget | None = None,
) -> ApiKeyUsageReservationData | None:
    if api_key is None:
        return None

    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        try:
            return await service.enforce_limits_for_request(
                api_key.id,
                request_model=request_model,
                request_service_tier=request_service_tier,
                request_usage_budget=request_usage_budget,
            )
        except ApiKeyRateLimitExceededError as exc:
            message = f"{exc}. Usage resets at {exc.reset_at.isoformat()}Z."
            raise ProxyRateLimitError(message) from exc
        except ApiKeyInvalidError as exc:
            raise ProxyAuthError(str(exc)) from exc


async def _opportunistic_admission_denial(
    request: Request,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    model: str | None,
    lease_kind: Literal["response_create", "stream"] | None = "stream",
) -> JSONResponse | None:
    if api_key is None or api_key.traffic_class != TRAFFIC_CLASS_OPPORTUNISTIC:
        return None
    selection = await context.service.check_opportunistic_admission(
        api_key=api_key,
        model=_effective_optional_model_for_api_key(api_key, model),
        lease_kind=lease_kind,
    )
    if selection.account is not None:
        return None
    message = selection.error_message or "opportunistic burn window closed"
    if not message.startswith("opportunistic burn window closed"):
        message = f"opportunistic burn window closed: {message}"
    return _logged_error_json_response(
        request,
        429,
        openai_error("rate_limit_exceeded", message, error_type="rate_limit_error"),
        headers={"Retry-After": str(_OPPORTUNISTIC_RETRY_AFTER_SECONDS)},
    )


async def _release_reservation(reservation: ApiKeyUsageReservationData | None) -> None:
    if reservation is None:
        return
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        await service.release_usage_reservation(reservation.reservation_id)


async def _finalize_image_reservation(
    reservation: ApiKeyUsageReservationData | None,
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None = None,
) -> None:
    """Finalize the API-key usage reservation for a ``/v1/images/*`` call.

    The image adapter bypasses the standard stream settlement (``stream_responses``
    is invoked with ``api_key_reservation=None``) because the ``image_generation``
    tool path typically leaves ``response.usage`` empty; charging from
    ``tool_usage.image_gen`` is the only source of truth. This helper
    finalizes the reservation with the captured image tokens when present,
    otherwise releases it. Calling this exactly once per request prevents
    the double-billing scenario where both the standard settlement and
    the post-hoc image record_usage path increment limits.

    Persistence errors are caught and logged so a transient DB/session
    failure during the tail accounting cannot turn a successfully
    generated image into a user-facing 500 (non-streaming) or an
    abrupt stream termination (streaming). This mirrors the
    best-effort accounting policy used by
    ``ProxyService._settle_stream_api_key_usage``.
    """
    if reservation is None:
        return
    try:
        if not input_tokens and not output_tokens:
            await _release_reservation(reservation)
            return
        async with get_background_session() as session:
            service = ApiKeysService(ApiKeysRepository(session))
            await service.finalize_usage_reservation(
                reservation.reservation_id,
                model=model,
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cached_input_tokens=int(cached_input_tokens or 0),
                service_tier=None,
            )
    except Exception:
        logger.warning(
            "failed to finalize image reservation reservation_id=%s model=%s",
            reservation.reservation_id,
            model,
            exc_info=True,
        )


def _effective_model_for_api_key(api_key: ApiKeyData | None, requested_model: str) -> str:
    if api_key is None or api_key.enforced_model is None:
        return requested_model
    return api_key.enforced_model


def _effective_optional_model_for_api_key(api_key: ApiKeyData | None, requested_model: str | None) -> str | None:
    if api_key is None or api_key.enforced_model is None:
        return requested_model
    return api_key.enforced_model


def _compact_request_service_tier(payload: ResponsesCompactRequest) -> str | None:
    value = payload.service_tier
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


async def _collect_responses_payload(stream: AsyncIterator[str]) -> OpenAIResponseResult:
    output_items: dict[int, dict[str, JsonValue]] = {}
    terminal_result: OpenAIResponseResult | None = None
    contract_violation_kind: str | None = None
    async for line in stream:
        payload = _parse_sse_payload(line)
        if not payload:
            if _looks_like_sse_data_block(line):
                contract_violation_kind = contract_violation_kind or "invalid_json"
            continue
        event_type = payload.get("type")
        _collect_output_item_event(payload, output_items)
        if terminal_result is not None:
            continue
        if event_type == "error":
            terminal_result = _parse_event_error_envelope(payload)
            continue
        if event_type == "response.failed":
            response = payload.get("response")
            if isinstance(response, dict):
                error_value = response.get("error")
                if isinstance(error_value, dict):
                    try:
                        terminal_result = OpenAIErrorEnvelopeModel.model_validate({"error": error_value})
                        continue
                    except ValidationError:
                        terminal_result = _default_error_envelope()
                        continue
                parsed = parse_response_payload(response)
                if parsed is not None and parsed.error is not None:
                    terminal_result = _error_envelope_from_response(parsed.error)
                    continue
            terminal_result = _default_error_envelope()
            continue
        if event_type in ("response.completed", "response.incomplete"):
            response = payload.get("response")
            if is_json_mapping(response):
                normalized_response, violation_kind = _normalize_public_response_mapping(response, output_items)
                if violation_kind is not None:
                    contract_violation_kind = contract_violation_kind or violation_kind
                if normalized_response is not None:
                    parsed = parse_response_payload(normalized_response)
                else:
                    parsed = None
                if parsed is not None:
                    terminal_result = parsed
                    continue
            error_kind = contract_violation_kind or "invalid_json"
            terminal_result = _public_contract_error_envelope(
                error_kind,
                _public_contract_error_message(error_kind),
            )

    if terminal_result is not None:
        return terminal_result
    error_kind = contract_violation_kind or "upstream_stream_truncated"
    return _public_contract_error_envelope(
        error_kind,
        _public_contract_error_message(error_kind),
    )


def _collect_output_item_event(
    payload: dict[str, JsonValue],
    output_items: dict[int, dict[str, JsonValue]],
) -> None:
    event_type = payload.get("type")
    if event_type not in ("response.output_item.added", "response.output_item.done"):
        return
    output_index = payload.get("output_index")
    item = payload.get("item")
    if not isinstance(output_index, int) or not isinstance(item, dict):
        return
    output_items[output_index] = dict(item)


def _merge_collected_output_items(
    response: Mapping[str, JsonValue],
    output_items: dict[int, dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    merged = dict(response)
    if not output_items:
        return merged

    existing_output = response.get("output")
    if isinstance(existing_output, list) and existing_output:
        return merged

    merged["output"] = [item for _, item in sorted(output_items.items())]
    return merged


async def _normalize_public_responses_stream(
    stream: AsyncIterator[str],
    *,
    enforce_openai_sdk_contract: bool = True,
) -> AsyncIterator[str]:
    """Normalize the upstream SSE event stream for the public /v1 surface.

    Args:
        stream: the upstream SSE event blocks (post-error-conversion).
        enforce_openai_sdk_contract: when True (the default, used for /v1),
            apply OpenAI Responses SSE contract enforcement: drop Codex
            vendor events (codex.*), backfill terminal output from streamed
            item events, and synthesize a leading response.created event
            when the upstream stream's first standard event is not
            response.created. When False (used for /backend-api/codex/*,
            which feeds the Codex CLI), all events including codex.* are
            forwarded verbatim and no synthesis happens — the Codex CLI
            relies on the upstream's native event shape.
    """
    terminal_seen = False
    done_seen = False
    contract_violation_kind: str | None = None
    seen_text_delta_keys: set[tuple[str | None, int | None]] = set()
    # Collect output items from streamed ``response.output_item.added`` /
    # ``response.output_item.done`` events so the terminal
    # ``response.completed`` / ``response.incomplete`` payload can be
    # backfilled when the upstream Codex backend leaves ``response.output``
    # empty. This mirrors the existing non-streaming behavior in
    # ``_collect_responses_payload`` so OpenAI SDK consumers calling
    # ``stream.get_final_response().output`` see the same items the
    # non-streaming endpoint returns.
    output_items: dict[int, dict[str, JsonValue]] = {}
    # Track whether the first standard ``response.*`` event the public stream
    # emits is ``response.created``. The OpenAI Responses SSE contract requires
    # ``response.created`` to be the first event. The upstream Codex backend
    # sometimes drops straight to a terminal event (e.g. ``response.failed``
    # when upstream rejects the request mid-stream) without emitting
    # ``response.created`` first, which makes the OpenAI SDK's
    # ``_create_initial_response`` raise ``RuntimeError``. When that happens
    # we synthesize a ``response.created`` snapshot from the terminal event's
    # ``response`` envelope so the SDK parser can complete the stream.
    created_emitted = False
    # Anonymous pre-created events cannot be made SDK-safe until a response
    # envelope arrives: the public OpenAI SDK requires response.created first.
    # Buffer them temporarily. Once an envelope arrives, replay only lightweight
    # visible content events (text/content-part deltas) after the created event;
    # drop unowned output_item lifecycle events so cancelled-request orphans are
    # not attached to the later response.
    pre_created_buffer: list[dict[str, JsonValue]] = []

    def formatted_payloads_with_synthetic_deltas(payload: dict[str, JsonValue]) -> list[str]:
        return [
            *[
                format_sse_event(synthetic_payload)
                for synthetic_payload in _synthetic_text_delta_events(payload, seen_text_delta_keys)
            ],
            format_sse_event(payload),
        ]

    def buffered_pre_created_payloads_to_replay(response_id: str | None) -> list[str]:
        try:
            if _pre_created_buffer_has_indexed_lifecycle(pre_created_buffer):
                return []
            return _format_legacy_pre_created_payloads(
                pre_created_buffer,
                response_id=response_id,
                seen_text_delta_keys=seen_text_delta_keys,
            )
        finally:
            pre_created_buffer.clear()

    async for event_block in stream:
        if event_block.strip() == "data: [DONE]":
            done_seen = True
            if terminal_seen:
                yield event_block
            continue
        if _looks_like_sse_comment_block(event_block):
            yield event_block
            continue
        payload = _parse_sse_payload(event_block)
        if payload is None:
            if _looks_like_sse_data_block(event_block):
                contract_violation_kind = contract_violation_kind or "invalid_json"
            continue
        raw_event_type = payload.get("type")
        if (
            enforce_openai_sdk_contract
            and isinstance(raw_event_type, str)
            and raw_event_type
            in (
                "response.completed",
                "response.incomplete",
            )
        ):
            response_obj = payload.get("response")
            if is_json_mapping(response_obj):
                existing_output = response_obj.get("output")
                needs_backfill = not (isinstance(existing_output, list) and existing_output)
                if needs_backfill and output_items:
                    merged_response = _merge_collected_output_items(response_obj, output_items)
                    payload = dict(payload)
                    payload["response"] = merged_response
        normalized_payload, violation_kind = _normalize_public_stream_payload(
            payload,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
        )
        if violation_kind is not None:
            contract_violation_kind = contract_violation_kind or violation_kind
        if normalized_payload is None:
            continue
        event_type = normalized_payload.get("type")
        if not enforce_openai_sdk_contract and (
            event_type == "error" or is_json_mapping(normalized_payload.get("error"))
        ):
            terminal_seen = True
            yield event_block
            continue

        if enforce_openai_sdk_contract and not created_emitted and isinstance(event_type, str):
            if event_type == "response.created":
                created_emitted = True
                yield format_sse_event(normalized_payload)
                response_id = _response_id_from_event_payload(normalized_payload)
                for formatted_payload in buffered_pre_created_payloads_to_replay(response_id):
                    yield formatted_payload
                continue

            synthetic_created = _synthetic_response_created_envelope(normalized_payload)
            if synthetic_created is not None:
                yield format_sse_event(synthetic_created)
                created_emitted = True
                response_id = _response_id_from_event_payload(synthetic_created)
                for formatted_payload in buffered_pre_created_payloads_to_replay(response_id):
                    yield formatted_payload
            elif _should_buffer_public_pre_created_event(event_type):
                if len(pre_created_buffer) >= _PUBLIC_RESPONSES_PRE_CREATED_BUFFER_LIMIT:
                    error_kind = contract_violation_kind or "upstream_stream_truncated"
                    for formatted_payload in _public_response_failed_event_blocks(error_kind, include_created=True):
                        yield formatted_payload
                    return
                pre_created_buffer.append(normalized_payload)
                continue
            elif event_type in _PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES:
                if event_type == "error":
                    for formatted_payload in _public_response_failed_event_blocks_from_error(
                        normalized_payload,
                        include_created=True,
                    ):
                        yield formatted_payload
                    return
                error_kind = contract_violation_kind or "upstream_stream_truncated"
                for formatted_payload in _public_response_failed_event_blocks(error_kind, include_created=True):
                    yield formatted_payload
                return

        if enforce_openai_sdk_contract and event_type == "error":
            for formatted_payload in _public_response_failed_event_blocks_from_error(
                normalized_payload,
                include_created=not created_emitted,
            ):
                yield formatted_payload
            return

        _collect_output_item_event(normalized_payload, output_items)
        if event_type == "response.output_text.delta":
            seen_text_delta_keys.add(_text_delta_stream_key(normalized_payload))
        for formatted_payload in formatted_payloads_with_synthetic_deltas(normalized_payload):
            yield formatted_payload
        if isinstance(event_type, str) and event_type in _PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES:
            terminal_seen = True
    if terminal_seen:
        if not done_seen and not enforce_openai_sdk_contract:
            yield "data: [DONE]\n\n"
        return
    error_kind = contract_violation_kind or (
        "upstream_stream_truncated" if enforce_openai_sdk_contract else "stream_incomplete"
    )
    include_created = enforce_openai_sdk_contract and not created_emitted
    for formatted_payload in _public_response_failed_event_blocks(error_kind, include_created=include_created):
        yield formatted_payload


def _should_buffer_public_pre_created_event(event_type: str) -> bool:
    return (
        event_type.startswith("response.")
        and event_type != "response.created"
        and event_type not in _PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES
    )


def _public_response_failed_event_blocks(error_kind: str, *, include_created: bool) -> list[str]:
    failed_payload = cast(
        dict[str, JsonValue],
        response_failed_event(
            error_kind,
            _public_contract_error_message(error_kind),
            response_id=f"resp_{error_kind}",
        ),
    )
    blocks: list[str] = []
    if include_created:
        synthetic_created = _synthetic_response_created_envelope(failed_payload)
        if synthetic_created is not None:
            blocks.append(format_sse_event(synthetic_created))
    blocks.append(format_sse_event(failed_payload))
    return blocks


def _public_response_failed_event_blocks_from_error(
    payload: dict[str, JsonValue],
    *,
    include_created: bool,
) -> list[str]:
    envelope = _parse_event_error_envelope(payload)
    error = envelope.error
    if error is None:
        error = _default_error_envelope().error
    assert error is not None
    message = error.message
    raw_message = payload.get("message")
    if isinstance(raw_message, str) and raw_message.strip():
        if not message or message == "Upstream error":
            message = raw_message.strip()
    error_type = error.type
    if not error_type:
        raw_error_type = payload.get("error_type")
        if isinstance(raw_error_type, str) and raw_error_type.strip():
            error_type = raw_error_type.strip()
    failed_payload = cast(
        dict[str, JsonValue],
        response_failed_event(
            error.code or "upstream_error",
            message or "Upstream error",
            error_type or "server_error",
            response_id=f"resp_{error.code or 'upstream_error'}",
            error_param=error.param,
        ),
    )
    blocks: list[str] = []
    if include_created:
        synthetic_created = _synthetic_response_created_envelope(failed_payload)
        if synthetic_created is not None:
            blocks.append(format_sse_event(synthetic_created))
    blocks.append(format_sse_event(failed_payload))
    return blocks


_REPLAYABLE_PUBLIC_PRE_CREATED_EVENT_TYPES = frozenset(
    {
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.refusal.delta",
        "response.refusal.done",
    }
)


_INDEXED_PRE_CREATED_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "response.output_item.added",
        "response.output_item.done",
        "response.content_part.added",
    }
)


def _pre_created_buffer_has_indexed_lifecycle(payloads: list[dict[str, JsonValue]]) -> bool:
    for payload in payloads:
        event_type = payload.get("type")
        if isinstance(event_type, str) and event_type in _INDEXED_PRE_CREATED_LIFECYCLE_EVENT_TYPES:
            return True
        if isinstance(payload.get("output_index"), int) or isinstance(payload.get("item_id"), str):
            return True
    return False


def _format_legacy_pre_created_payloads(
    payloads: list[dict[str, JsonValue]],
    *,
    response_id: str | None,
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> list[str]:
    formatted: list[str] = []
    if not payloads:
        return formatted

    item_id = _synthetic_pre_created_item_id(response_id)
    output_index = 0
    content_index = 0
    text_item_opened = False
    text_parts: list[str] = []
    final_text: str | None = None

    def open_text_item(sequence_number: int) -> None:
        nonlocal text_item_opened
        if text_item_opened:
            return
        output_item_added = cast(
            dict[str, JsonValue],
            {
                "type": "response.output_item.added",
                "sequence_number": sequence_number,
                "output_index": output_index,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                },
            },
        )
        formatted.append(format_sse_event(output_item_added))
        content_part_added = cast(
            dict[str, JsonValue],
            {
                "type": "response.content_part.added",
                "sequence_number": sequence_number,
                "output_index": output_index,
                "content_index": content_index,
                "item_id": item_id,
                "part": {"type": "output_text", "text": ""},
            },
        )
        formatted.append(format_sse_event(content_part_added))
        text_item_opened = True

    sequence_number = 0
    for payload in payloads:
        event_type = payload.get("type")
        if not isinstance(event_type, str) or event_type not in _REPLAYABLE_PUBLIC_PRE_CREATED_EVENT_TYPES:
            continue
        sequence_number = _payload_sequence_number(payload, sequence_number + 1)
        if event_type in {"response.output_text.delta", "response.refusal.delta"}:
            delta = payload.get("delta")
            if not isinstance(delta, str):
                continue
            open_text_item(sequence_number)
            text_parts.append(delta)
            normalized = dict(payload)
            normalized["sequence_number"] = sequence_number
            normalized["output_index"] = output_index
            normalized["content_index"] = content_index
            normalized["item_id"] = item_id
            normalized.setdefault("logprobs", [])
            formatted.append(format_sse_event(normalized))
            seen_text_delta_keys.add((item_id, output_index))
            continue
        if event_type in {"response.output_text.done", "response.refusal.done"}:
            text = payload.get("text")
            if not isinstance(text, str):
                text = "".join(text_parts)
            open_text_item(sequence_number)
            final_text = text
            normalized = dict(payload)
            normalized["sequence_number"] = sequence_number
            normalized["output_index"] = output_index
            normalized["content_index"] = content_index
            normalized["item_id"] = item_id
            normalized.setdefault("logprobs", [])
            formatted.append(format_sse_event(normalized))
            continue
        part = payload.get("part")
        if is_json_mapping(part) and part.get("type") in _PUBLIC_RESPONSE_TEXT_PART_TYPES:
            text = part.get("text")
            if isinstance(text, str):
                final_text = text
            open_text_item(sequence_number)
            normalized = dict(payload)
            normalized["sequence_number"] = sequence_number
            normalized["output_index"] = output_index
            normalized["content_index"] = content_index
            normalized["item_id"] = item_id
            formatted.append(format_sse_event(normalized))
        else:
            # Preserve legacy unindexed non-text content_part.done events for
            # raw SSE clients. They are not used to assemble SDK output.
            formatted.append(format_sse_event(payload))

    if text_item_opened:
        seen_text_delta_keys.add((None, output_index))
        text = final_text if final_text is not None else "".join(text_parts)
        output_item_done = cast(
            dict[str, JsonValue],
            {
                "type": "response.output_item.done",
                "sequence_number": sequence_number + 1,
                "output_index": output_index,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text}],
                },
            },
        )
        formatted.append(format_sse_event(output_item_done))
    return formatted


def _payload_sequence_number(payload: Mapping[str, JsonValue], fallback: int) -> int:
    sequence_number = payload.get("sequence_number")
    return sequence_number if isinstance(sequence_number, int) else fallback


def _response_id_from_event_payload(payload: Mapping[str, JsonValue]) -> str | None:
    response = payload.get("response")
    if not is_json_mapping(response):
        return None
    response_id = response.get("id")
    return response_id if isinstance(response_id, str) and response_id else None


def _synthetic_pre_created_item_id(response_id: str | None) -> str:
    if response_id:
        return f"msg_{response_id}_precreated"
    return "msg_precreated"


def _normalize_public_stream_payload(
    payload: dict[str, JsonValue],
    *,
    enforce_openai_sdk_contract: bool = True,
) -> tuple[dict[str, JsonValue] | None, str | None]:
    event_type = payload.get("type")
    # Drop Codex-internal vendor events on the public /v1 surface only. The
    # upstream Codex backend emits non-standard events (notably
    # ``codex.rate_limits``, which is throttled per rate-limit window and so
    # leaks intermittently before ``response.created``). The OpenAI Responses
    # SSE contract does not define any ``codex.*`` event type, and the OpenAI
    # SDK's stream parser raises ``RuntimeError`` if any other event arrives
    # first. The Codex CLI routes under ``/backend-api/codex/*`` legitimately
    # consume these events and pass ``enforce_openai_sdk_contract=False`` so
    # they continue to forward unchanged.
    if enforce_openai_sdk_contract and isinstance(event_type, str) and event_type.startswith("codex."):
        return None, None
    if event_type == "error":
        parsed_error = _parse_event_error_envelope(payload)
        if _is_previous_response_not_found_public_error(parsed_error.error):
            return (
                cast(
                    dict[str, JsonValue],
                    response_failed_event(
                        "stream_incomplete",
                        PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
                    ),
                ),
                None,
            )
        return payload, None
    if event_type in ("response.completed", "response.incomplete"):
        response = payload.get("response")
        if not is_json_mapping(response):
            return (
                cast(
                    dict[str, JsonValue],
                    response_failed_event(
                        "invalid_json",
                        _public_contract_error_message("invalid_json"),
                    ),
                ),
                "invalid_json",
            )
        normalized_response, violation_kind = _normalize_public_response_mapping(response)
        if normalized_response is None:
            error_kind = violation_kind or "invalid_output_item"
            return (
                cast(
                    dict[str, JsonValue],
                    response_failed_event(
                        error_kind,
                        _public_contract_error_message(error_kind),
                    ),
                ),
                error_kind,
            )
        normalized_payload = dict(payload)
        normalized_payload["response"] = normalized_response
        return normalized_payload, violation_kind
    if event_type in ("response.output_item.added", "response.output_item.done"):
        item = payload.get("item")
        if not is_json_mapping(item):
            return None, "invalid_output_item"
        normalized_item = _normalize_public_output_item(item)
        if normalized_item is None:
            return None, "invalid_output_item"
        normalized_payload = dict(payload)
        normalized_payload["item"] = normalized_item
        violation_kind = None
        item_type = item.get("type")
        if isinstance(item_type, str) and not _is_public_passthrough_output_item_type(item_type):
            violation_kind = "invalid_output_item"
        return normalized_payload, violation_kind
    return payload, None


def _synthetic_response_created_envelope(
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue] | None:
    """Synthesize a ``response.created`` SSE payload from a non-created event.

    Used by ``_normalize_public_responses_stream`` when the upstream's first
    standard event is not ``response.created`` (for example, the Codex backend
    sometimes jumps straight to ``response.failed`` when upstream rejects the
    request mid-stream). The OpenAI Responses SSE contract requires
    ``response.created`` to be the first event the stream emits — the OpenAI
    Python SDK's ``ResponseStreamState._create_initial_response`` raises
    ``RuntimeError`` otherwise.

    Returns ``None`` when no ``response`` envelope is available on the source
    event (in that case the caller forwards the event verbatim; the SDK
    consumer will still see a parser error, but the stream contract is at
    least not silently violated by our synthesis logic).
    """
    response = payload.get("response")
    if not is_json_mapping(response):
        return None
    created_envelope: dict[str, JsonValue] = dict(response)
    created_envelope["status"] = "in_progress"
    created_envelope["output"] = []
    synthetic: dict[str, JsonValue] = {
        "type": "response.created",
        "response": created_envelope,
    }
    sequence_number = payload.get("sequence_number")
    if isinstance(sequence_number, int):
        synthetic["sequence_number"] = sequence_number
    return synthetic


def _synthetic_text_delta_events(
    payload: Mapping[str, JsonValue],
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> list[dict[str, JsonValue]]:
    event_type = payload.get("type")
    if event_type == "response.output_item.done":
        output_index = payload.get("output_index")
        item = payload.get("item")
        if isinstance(output_index, int) and is_json_mapping(item):
            synthetic = _synthetic_text_delta_for_output_item(output_index, item, seen_text_delta_keys)
            return [synthetic] if synthetic is not None else []
    if event_type not in {"response.completed", "response.incomplete"}:
        return []
    response = payload.get("response")
    if not is_json_mapping(response):
        return []
    output = response.get("output")
    if not isinstance(output, list):
        return []

    synthetic_events: list[dict[str, JsonValue]] = []
    for output_index, item in enumerate(output):
        if not is_json_mapping(item):
            continue
        synthetic = _synthetic_text_delta_for_output_item(output_index, item, seen_text_delta_keys)
        if synthetic is not None:
            synthetic_events.append(synthetic)
    return synthetic_events


def _synthetic_text_delta_for_output_item(
    output_index: int,
    item: Mapping[str, JsonValue],
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> dict[str, JsonValue] | None:
    normalized_item = _normalize_public_output_item(item)
    if normalized_item is None:
        return None
    text = _extract_public_output_item_text(normalized_item)
    if text is None:
        return None
    key = _output_item_stream_key(output_index, normalized_item)
    if _seen_text_delta_for_output_item(key, seen_text_delta_keys):
        return None
    seen_text_delta_keys.add(key)

    event: dict[str, JsonValue] = {
        "type": "response.output_text.delta",
        "output_index": output_index,
        "content_index": 0,
        "delta": text,
    }
    item_id = normalized_item.get("id")
    if isinstance(item_id, str) and item_id:
        event["item_id"] = item_id
    return event


def _text_delta_stream_key(payload: Mapping[str, JsonValue]) -> tuple[str | None, int | None]:
    item_id = payload.get("item_id")
    output_index = payload.get("output_index")
    return (
        item_id if isinstance(item_id, str) and item_id else None,
        output_index if isinstance(output_index, int) else None,
    )


def _output_item_stream_key(
    output_index: int,
    item: Mapping[str, JsonValue],
) -> tuple[str | None, int | None]:
    item_id = item.get("id")
    return (item_id if isinstance(item_id, str) and item_id else None, output_index)


def _seen_text_delta_for_output_item(
    key: tuple[str | None, int | None],
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> bool:
    item_id, output_index = key
    return any(
        candidate in seen_text_delta_keys
        for candidate in (
            key,
            (item_id, None) if item_id is not None else None,
            (None, output_index) if output_index is not None else None,
            (None, None),
        )
        if candidate is not None
    )


def _normalize_public_response_mapping(
    response: Mapping[str, JsonValue],
    output_items: dict[int, dict[str, JsonValue]] | None = None,
) -> tuple[dict[str, JsonValue] | None, str | None]:
    merged = _merge_collected_output_items(response, output_items or {})
    output = merged.get("output")
    if not isinstance(output, list):
        return merged, None
    normalized_output: list[JsonValue] = []
    dropped_items = 0
    for item in output:
        if not is_json_mapping(item):
            dropped_items += 1
            continue
        normalized_item = _normalize_public_output_item(item)
        if normalized_item is None:
            dropped_items += 1
            continue
        normalized_output.append(normalized_item)
    if output and not normalized_output:
        _record_public_contract_violation("invalid_output_item")
        return None, "invalid_output_item"
    normalized = dict(merged)
    normalized["output"] = normalized_output
    if dropped_items:
        _record_public_contract_violation("invalid_output_item")
        return normalized, "invalid_output_item"
    return normalized, None


def _normalize_public_output_item(item: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    item_type = item.get("type")
    if isinstance(item_type, str) and _is_public_passthrough_output_item_type(item_type):
        return dict(item)
    text_value = _extract_public_output_item_text(item)
    if text_value is None:
        return None
    normalized: dict[str, JsonValue] = {
        "type": "message",
        "role": "assistant",
        "status": item.get("status") if isinstance(item.get("status"), str) else "completed",
        "content": [{"type": "output_text", "text": text_value}],
    }
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        normalized["id"] = item_id
    return normalized


def _is_public_passthrough_output_item_type(item_type: str) -> bool:
    if item_type in _PUBLIC_RESPONSE_OUTPUT_ITEM_TYPES:
        return True
    return item_type.endswith("_call") or item_type.endswith("_call_output")


def _extract_public_output_item_text(item: Mapping[str, JsonValue]) -> str | None:
    direct_text = item.get("text")
    if isinstance(direct_text, str) and direct_text:
        return direct_text
    content = item.get("content")
    if is_json_mapping(content):
        content_parts: list[Mapping[str, JsonValue]] = [content]
    elif isinstance(content, list):
        content_parts = [part for part in content if is_json_mapping(part)]
    else:
        content_parts = []
    parts: list[str] = []
    for part in content_parts:
        part_type = part.get("type")
        if isinstance(part_type, str) and part_type in _PUBLIC_RESPONSE_TEXT_PART_TYPES:
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
                continue
        text = part.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    if parts:
        return "".join(parts)
    summary = item.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    return None


def _looks_like_sse_data_block(event_block: str) -> bool:
    return "data:" in event_block


def _looks_like_sse_comment_block(event_block: str) -> bool:
    return bool(event_block.strip()) and all(
        not line.strip() or line.lstrip().startswith(":") for line in event_block.splitlines()
    )


def _public_contract_error_message(kind: str) -> str:
    if kind == "invalid_json":
        return "Responses stream produced an invalid JSON payload"
    if kind == "invalid_output_item":
        return "Responses stream produced unsupported output items"
    if kind == "upstream_stream_truncated":
        return "Responses stream ended before a terminal event"
    if kind == "stream_incomplete":
        return "Upstream stream ended before response.completed"
    return "Responses stream violated the public contract"


def _public_contract_error_envelope(kind: str, message: str) -> OpenAIErrorEnvelopeModel:
    _record_public_contract_violation(kind)
    return OpenAIErrorEnvelopeModel(
        error=OpenAIError(
            message=message,
            type="server_error",
            code=kind,
        )
    )


def _record_public_contract_violation(kind: str) -> None:
    logger.warning("bridge_public_contract_violation kind=%s", kind)
    if PROMETHEUS_AVAILABLE and bridge_public_contract_error_total is not None:
        bridge_public_contract_error_total.labels(kind=kind).inc()


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
    if payload.get("type") == "error":
        return _parse_event_error_envelope(cast(dict[str, JsonValue], payload))
    try:
        return OpenAIErrorEnvelopeModel.model_validate(payload)
    except ValidationError:
        return _default_error_envelope()


def _openai_invalid_transcription_model_error(model: str) -> OpenAIErrorEnvelope:
    error = openai_error(
        "invalid_request_error",
        f"Unsupported transcription model '{model}'. Only '{_TRANSCRIPTION_MODEL}' is supported.",
        error_type="invalid_request_error",
    )
    error["error"]["param"] = "model"
    return error


def _error_envelope_from_response(error_value: OpenAIError | None) -> OpenAIErrorEnvelopeModel:
    if error_value is None:
        return _default_error_envelope()
    return OpenAIErrorEnvelopeModel(error=error_value)


def _is_previous_response_not_found_public_error(error_value: OpenAIError | None) -> bool:
    if error_value is None:
        return False
    return is_previous_response_not_found_error(
        code=error_value.code,
        param=error_value.param,
        message=error_value.message,
    )


def _mask_previous_response_not_found_error(
    envelope: OpenAIErrorEnvelopeModel,
    *,
    default_status: int | None = None,
) -> tuple[int, OpenAIErrorEnvelopeModel]:
    if not _is_previous_response_not_found_public_error(envelope.error):
        return default_status if default_status is not None else _status_for_error(envelope.error), envelope
    return (
        502,
        OpenAIErrorEnvelopeModel(
            error=OpenAIError(
                message=PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
                type="server_error",
                code="stream_incomplete",
            )
        ),
    )


def _status_for_error(error_value: OpenAIError | None) -> int:
    if error_value and error_value.code == "previous_response_not_found":
        return 502
    if error_value and error_value.code in _UNAVAILABLE_SELECTION_ERROR_CODES:
        return 503
    if error_value and error_value.code in {"rate_limit_exceeded", "usage_limit_reached", "insufficient_quota"}:
        return 429
    if error_value and error_value.code in {"invalid_api_key", "invalid_authentication", "token_invalidated"}:
        return 401
    if error_value and error_value.code == "invalid_request_error":
        return 400
    if error_value and error_value.type == "authentication_error":
        return 401
    if error_value and error_value.type == "invalid_request_error":
        return 400
    if error_value and error_value.type in {"rate_limit_error", "usage_limit_reached", "insufficient_quota"}:
        return 429
    return 502


def _status_for_image_error_envelope(envelope: object) -> int:
    """Map an OpenAI-shape error envelope dict to its canonical HTTP status
    for the ``/v1/images/*`` non-streaming response path.

    Returns 502 when no specific mapping matches (e.g. server_error or an
    unrecognised type), so transport-level failures still surface as
    upstream errors. Code matches take precedence over type matches.
    """
    if not isinstance(envelope, Mapping):
        return 502
    error = cast(Mapping[str, object], envelope).get("error")
    if not isinstance(error, Mapping):
        return 502
    error_map = cast(Mapping[str, object], error)
    code = error_map.get("code")
    if isinstance(code, str):
        if code in _IMAGE_ERROR_CODE_STATUS:
            return _IMAGE_ERROR_CODE_STATUS[code]
        if code in _UNAVAILABLE_SELECTION_ERROR_CODES:
            return 503
    error_type = error_map.get("type")
    if isinstance(error_type, str) and error_type in _IMAGE_ERROR_TYPE_STATUS:
        return _IMAGE_ERROR_TYPE_STATUS[error_type]
    return 502
