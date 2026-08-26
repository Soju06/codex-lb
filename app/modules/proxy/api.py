from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from json import JSONDecodeError
from typing import Any, Final, Literal, Protocol, TypeVar, cast
from uuid import uuid4

import anyio
from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Path,
    Request,
    Response,
    Security,
    WebSocket,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.convertors import Convertor, register_url_convertor
from starlette.datastructures import Headers
from starlette.websockets import WebSocketState

from app.core import usage as usage_core
from app.core.auth.dependencies import (
    set_openai_error_format,
    validate_codex_provider_usage_identity,
    validate_proxy_api_key,
    validate_proxy_api_key_authorization,
    validate_required_proxy_api_key,
    validate_required_proxy_api_key_authorization,
    validate_usage_api_key,
)
from app.core.auth.refresh import RefreshError
from app.core.cache.invalidation import NAMESPACE_RESET_CREDITS, bump_cache_invalidation_local
from app.core.clients.files import FileProxyError
from app.core.clients.proxy import (
    CODEX_LB_REQUIRED_CAPABILITY_HEADER,
    CodexControlRequestPrivacyPolicy,
    CodexControlResponse,
    ProxyResponseError,
    _is_native_codex_request,
)
from app.core.clients.proxy_websocket import (
    REALTIME_LIVE_CALL_ID_ROUTE_REGEX,
    RealtimeWebSocketProtocol,
)
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    ResetCreditFetchError,
    ResetCreditItem,
    build_snapshot,
    consume_reset_credit,
    fetch_reset_credits,
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
from app.core.exceptions import (
    ProxyAuthError,
    ProxyModelNotAllowed,
    ProxyRateLimitError,
    ProxyUpstreamError,
)
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_public_contract_error_total,
    stream_keepalive_sent_total,
)
from app.core.middleware.multipart_content_encoding import raise_for_unsupported_multipart_content_encoding
from app.core.multipart import (
    IMAGE_EDITS_MULTIPART_POLICY,
    TRANSCRIPTION_MULTIPART_POLICY,
    bounded_multipart_form,
    read_bounded_upload,
)
from app.core.multipart_fields import (
    optional_text,
    optional_upload,
    ordered_text_items,
    ordered_uploads,
    required_text,
    required_upload,
    uploaded_file_items,
)
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
    normalize_compaction_item_id,
)
from app.core.openai.models import (
    OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel,
)
from app.core.openai.parsing import parse_response_payload
from app.core.openai.requests import (
    ResponsesCompactRequest,
    ResponsesRequest,
    normalize_tool_type,
    responses_request_has_explicit_prompt_cache_controls,
    strip_replayed_tool_call_namespaces_from_payload,
)
from app.core.openai.v1_requests import V1ResponsesCompactRequest, V1ResponsesRequest
from app.core.request_locality import (
    FORWARDED_CHAIN_HEADER_NAMES,
    parse_trusted_proxy_networks,
    resolve_connection_client_ip,
    resolve_request_client_host,
)
from app.core.resilience.overload import is_local_overload_error_code, merge_retry_after_headers
from app.core.runtime_logging import log_error_response
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.json_guards import is_json_list, is_json_mapping
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.sse import (
    CODEX_KEEPALIVE_FRAME,
    SSE_KEEPALIVE_FRAME,
    format_sse_event,
    inject_sse_keepalives,
    parse_sse_data_json,
)
from app.db.models import Account, AccountStatus, ModelSource
from app.db.session import detach_session_objects, get_background_session
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
from app.modules.model_sources.catalog import (
    source_model_audio_cost_usd,
    source_model_cost_usd,
    source_model_request_overrides,
    source_model_supported_tool_types,
    source_model_supports_reasoning,
    source_models_to_upstream_models,
)
from app.modules.model_sources.forwarding import (
    ModelSourceForwardingError,
    SourceTimings,
    SourceUsage,
    SourceUsageHolder,
    forward_chat_completion,
)
from app.modules.model_sources.forwarding import (
    forward_audio_transcription as forward_source_audio_transcription,
)
from app.modules.model_sources.forwarding import (
    forward_embeddings as forward_source_embeddings,
)
from app.modules.model_sources.forwarding import (
    forward_responses as forward_source_responses,
)
from app.modules.model_sources.forwarding import (
    stream_chat_completion as stream_source_chat_completion,
)
from app.modules.model_sources.forwarding import (
    stream_responses as stream_source_responses,
)
from app.modules.model_sources.repository import ModelSourcesRepository
from app.modules.model_sources.selection import (
    allowed_source_ids_for_api_key,
    effective_model_for_api_key,
    select_responses_model_source,
)
from app.modules.proxy import affinity as proxy_affinity_module
from app.modules.proxy import images_service as images_service_module
from app.modules.proxy import service as proxy_service_module
from app.modules.proxy._service.support import (
    _bind_propagated_capacity_startup_ready,
    _bind_propagated_capacity_startup_wait,
    _bind_propagated_responses_owner_forward_dispatched,
    _bind_propagated_responses_owner_forward_rejected,
    _bind_propagated_responses_service_cleanup_ready,
    _could_be_blank_html_comment_line,
    _is_reasoning_summary_interleavable_event,
    _reasoning_summary_delta_key,
    _request_log_client_fields,
    _reset_propagated_capacity_startup_ready,
    _reset_propagated_capacity_startup_wait,
    _reset_propagated_responses_owner_forward_dispatched,
    _reset_propagated_responses_owner_forward_rejected,
    _reset_propagated_responses_service_cleanup_ready,
    _strip_blank_html_comment_lines,
)
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.helpers import _rate_limit_details
from app.modules.proxy.http_bridge_forwarding import parse_forwarded_request
from app.modules.proxy.images_observability import (
    IMAGE_ROUTE_MODEL_STATE,
    IMAGE_ROUTE_STARTED_AT_STATE,
    IMAGE_ROUTE_STREAM_STATE,
    record_images_route_observability,
)
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    apply_api_key_enforcement_to_chat_payload,
    apply_enforced_service_tier_model_fallback,
    enforce_strict_function_tools_format,
    enforce_strict_text_format,
    model_alias_requests_fast_mode,
    normalize_responses_request_payload,
    normalize_source_reasoning_aliases,
    openai_client_payload_error,
    openai_validation_error,
    resolve_model_alias,
    resolve_wire_reasoning_effort,
    responses_source_route_excluded,
    restore_source_reasoning_effort,
    sanitize_source_chat_payload,
    strip_terminal_compaction_trigger_input,
    validate_model_access,
    validate_top_level_compaction_trigger_input_shape,
)
from app.modules.proxy.schemas import (
    AccountPoolUsageResponse,
    CodexModelEntry,
    CodexModelsResponse,
    CodexTruncationPolicy,
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
from app.modules.proxy.selection_errors import USAGE_LIMIT_REACHED
from app.modules.proxy.types import (
    CreditStatusDetailsData,
    RateLimitResetCreditsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)
from app.modules.rate_limit_reset_credits.api import serialize_reset_credit_redeem
from app.modules.rate_limit_reset_credits.redeem_coordination import RedeemClaimTimeoutError
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.mappers import usage_history_to_window_row
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

_REASONING_SUMMARY_DELTA_TYPES = frozenset({"response.reasoning_summary_text.delta"})
_REASONING_SUMMARY_DONE_TYPES = frozenset(
    {
        "response.reasoning_summary_text.done",
        "response.reasoning_summary_part.done",
    }
)

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
_SOURCE_LIMITED_STREAM_BUFFER_BYTES = 16 * 1024 * 1024
_PROMPT_CACHE_MODE_HEADER = "X-Codex-LB-Prompt-Cache-Mode"
_SUBSCRIPTION_IMPLICIT_PROMPT_CACHE_MODE = "subscription-implicit"


def _mark_subscription_prompt_cache_fallback(response: Response, payload: ResponsesRequest) -> Response:
    if response.status_code < 400 and responses_request_has_explicit_prompt_cache_controls(payload):
        response.headers[_PROMPT_CACHE_MODE_HEADER] = _SUBSCRIPTION_IMPLICIT_PROMPT_CACHE_MODE
    return response


class _V1ResetCreditFreshCredentials:
    __slots__ = ("access_token_encrypted", "chatgpt_account_id")

    def __init__(self, *, access_token_encrypted: bytes, chatgpt_account_id: str | None) -> None:
        self.access_token_encrypted = access_token_encrypted
        self.chatgpt_account_id = chatgpt_account_id


class _RealtimeLiveCallIdConvertor(Convertor[str]):
    """Case-preserving path segment convertor for installed-app live call ids."""

    regex = REALTIME_LIVE_CALL_ID_ROUTE_REGEX

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor("realtime_live_call_id", _RealtimeLiveCallIdConvertor())

router = APIRouter(
    prefix="/backend-api/codex",
    tags=["proxy"],
    dependencies=[Security(validate_proxy_api_key), Depends(set_openai_error_format)],
)
realtime_call_router = APIRouter(
    prefix="/backend-api/codex",
    tags=["proxy"],
    dependencies=[Depends(set_openai_error_format)],
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
_OPENAPI_VALIDATION_ERROR_RESPONSE: Final[dict[str, Any]] = {
    "description": "Validation Error",
    "content": {
        "application/json": {
            "schema": {"$ref": "#/components/schemas/HTTPValidationError"},
        }
    },
}
_BACKEND_TRANSCRIBE_OPENAPI_EXTRA: Final[dict[str, Any]] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "title": "Body_backend_transcribe_backend_api_transcribe_post",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "contentMediaType": "application/octet-stream",
                            "title": "File",
                        },
                        "prompt": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Prompt",
                        },
                    },
                }
            }
        },
    },
    "responses": {"422": _OPENAPI_VALIDATION_ERROR_RESPONSE},
}
_V1_AUDIO_TRANSCRIPTIONS_OPENAPI_EXTRA: Final[dict[str, Any]] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "title": "Body_v1_audio_transcriptions_v1_audio_transcriptions_post",
                    "required": ["model", "file"],
                    "properties": {
                        "model": {"type": "string", "title": "Model"},
                        "file": {
                            "type": "string",
                            "contentMediaType": "application/octet-stream",
                            "title": "File",
                        },
                        "prompt": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Prompt",
                        },
                    },
                }
            }
        },
    },
    "responses": {"422": _OPENAPI_VALIDATION_ERROR_RESPONSE},
}
_V1_IMAGES_EDITS_OPENAPI_EXTRA: Final[dict[str, Any]] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "title": "Body_v1_images_edits_v1_images_edits_post",
                    "required": ["prompt"],
                    "properties": {
                        "model": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Model",
                        },
                        "prompt": {"type": "string", "title": "Prompt"},
                        "image": {
                            "anyOf": [
                                {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "contentMediaType": "application/octet-stream",
                                    },
                                },
                                {"type": "null"},
                            ],
                            "title": "Image",
                        },
                        "image[]": {
                            "anyOf": [
                                {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "contentMediaType": "application/octet-stream",
                                    },
                                },
                                {"type": "null"},
                            ],
                            "title": "Image[]",
                        },
                        "mask": {
                            "anyOf": [
                                {
                                    "type": "string",
                                    "contentMediaType": "application/octet-stream",
                                },
                                {"type": "null"},
                            ],
                            "title": "Mask",
                        },
                        "n": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "N",
                        },
                        "size": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Size",
                        },
                        "quality": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Quality",
                        },
                        "background": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Background",
                        },
                        "output_format": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Output Format",
                        },
                        "output_compression": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Output Compression",
                        },
                        "moderation": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Moderation",
                        },
                        "partial_images": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Partial Images",
                        },
                        "stream": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Stream",
                        },
                        "input_fidelity": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "Input Fidelity",
                        },
                        "user": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "title": "User",
                        },
                    },
                }
            }
        },
    },
    "responses": {"422": _OPENAPI_VALIDATION_ERROR_RESPONSE},
}


@dataclass(frozen=True, slots=True)
class _ParsedTranscriptionMultipart:
    audio_bytes: bytes
    filename: str
    content_type: str | None
    model: str | None
    prompt: str | None
    ordered_text_fields: tuple[tuple[str, str], ...]


_UNAVAILABLE_SELECTION_ERROR_CODES = {
    "no_accounts",
    "no_plan_support_for_model",
    "additional_quota_data_unavailable",
    "quota_exhausted",
    "no_additional_quota_eligible_accounts",
}
_STREAM_STARTUP_ERROR_PROBE_SECONDS = 0.05
_CAPACITY_WAIT_MARKER_GRACE_SECONDS = 0.05
# Keep bridge startup probing above tiny event-loop scheduling jitter:
# PostgreSQL-backed failures may need a DB round trip before the first item.
_HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS = 2.0
_CAPACITY_STARTUP_SIGNAL_DISCOVERY_SECONDS = _HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS
_CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS = 2.0
_CURSOR_CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS = 15.0
_CURSOR_CONTEXT_LIMIT_SYNTHETIC_USAGE_TOKENS: Final[int] = 1_000_000
_V1_MAX_OUTPUT_TOKEN_OVERRIDES: Final[dict[str, int]] = {
    "gpt-5.4": 128_000,
    "gpt-5.5": 128_000,
    "gpt-5.4-mini": 128_000,
    "gpt-5.3-codex": 128_000,
}


class _CapacityStartupReadyEvent(asyncio.Event):
    """Track when admission became ready so its startup probe cannot reset."""

    def __init__(self) -> None:
        super().__init__()
        self.set_at: float | None = None

    def set(self) -> None:
        if not self.is_set():
            self.set_at = time.monotonic()
        super().set()

    def clear(self) -> None:
        self.set_at = None
        super().clear()


_OPPORTUNISTIC_RETRY_AFTER_SECONDS = 60

# Internal Responses host model used to invoke the built-in
# ``image_generation`` tool on the /v1/images/* routes. It is never echoed
# to clients (only the requested ``gpt-image-*`` value appears in public
# responses) and is fixed (issue #1340 / PRINCIPLES.md P2): it tracks the
# registry bootstrap catalog's stable ``gpt-5.5`` slug and changes only in
# lockstep with catalog maintenance.
_IMAGES_HOST_MODEL = "gpt-5.5"

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


def _has_explicit_openai_sdk_marker(request: Request) -> bool:
    for header_name in request.headers:
        normalized_header = header_name.lower()
        if normalized_header.startswith("x-stainless-"):
            return True
    user_agent = request.headers.get("user-agent", "").lower()
    return "openai" in user_agent


def _is_openai_sdk_request(
    request: Request,
    payload: V1ResponsesRequest | Mapping[str, JsonValue] | None = None,
) -> bool:
    if _has_explicit_openai_sdk_marker(request):
        return True
    if payload is None or not _has_openai_responses_shape(payload):
        return False
    if isinstance(payload, Mapping):
        payload_dict = cast("Mapping[str, JsonValue]", payload)
        return _accepts_event_stream(request) or payload_dict.get("messages") is not None
    return _accepts_event_stream(request) or payload.messages is not None


async def _capture_raw_compaction_trigger_error(request: Request) -> None:
    """Validate top-level compaction triggers before Pydantic normalization.

    The typed request models intentionally hoist trailing system/developer
    messages into ``instructions``. Keep that behavior for runtime parsing and
    OpenAPI, but remember a raw trigger-placement error for the endpoint to
    render after FastAPI has supplied the typed body.
    """
    try:
        raw_payload = await request.json()
    except (JSONDecodeError, UnicodeDecodeError, ValueError):
        return
    if not is_json_mapping(raw_payload):
        return
    try:
        validate_top_level_compaction_trigger_input_shape(raw_payload)
    except ClientPayloadError as exc:
        request.state.compaction_trigger_error = exc


def _raw_compaction_trigger_error(request: Request) -> ClientPayloadError | None:
    error = getattr(request.state, "compaction_trigger_error", None)
    return error if isinstance(error, ClientPayloadError) else None


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
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
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

# A hard HTTP-bridge circuit is opened only after an ambiguous upstream turn
# failure. The caller must not immediately replay that turn, but it should
# also not have to guess when a new attempt is safe. Advertise a short,
# bounded retry interval on the one-shot 503 response.


def _codex_control_downstream_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() in _CODEX_CONTROL_RESPONSE_HEADERS}


def _codex_control_response(response: CodexControlResponse) -> Response:
    return Response(
        content=response.body,
        status_code=response.status_code,
        headers=_codex_control_downstream_headers(response.headers),
    )


def _realtime_call_error_response(request: Request, *, status_code: int) -> JSONResponse:
    return _logged_error_json_response(
        request,
        status_code,
        openai_error(
            "realtime_call_unavailable",
            "Realtime call could not be created",
            error_type="server_error",
        ),
    )


class _CodexControlAdapter(Protocol):
    @property
    def privacy_policy(self) -> CodexControlRequestPrivacyPolicy: ...

    @property
    def success_gate(self) -> Callable[[str, CodexControlResponse], Awaitable[bool]] | None: ...

    async def finalize(
        self,
        request: Request,
        context: ProxyContext,
        response: CodexControlResponse,
    ) -> Response: ...


class _PassthroughCodexControlAdapter:
    privacy_policy: Final[CodexControlRequestPrivacyPolicy] = CodexControlRequestPrivacyPolicy.STANDARD
    success_gate: Final[None] = None

    async def finalize(
        self,
        request: Request,
        context: ProxyContext,
        response: CodexControlResponse,
    ) -> Response:
        del request, context
        return _codex_control_response(response)


@dataclass(slots=True)
class _RealtimeCallCodexControlAdapter:
    context: ProxyContext
    api_key: ApiKeyData
    _binding_failure_message: str | None = "Realtime call owner could not be determined"

    @property
    def privacy_policy(self) -> CodexControlRequestPrivacyPolicy:
        return CodexControlRequestPrivacyPolicy.PRIVATE_REALTIME

    @property
    def success_gate(self) -> Callable[[str, CodexControlResponse], Awaitable[bool]]:
        return self._bind_successful_call_owner

    async def _bind_successful_call_owner(
        self,
        account_id: str,
        response: CodexControlResponse,
    ) -> bool:
        if not 200 <= response.status_code < 300:
            self._binding_failure_message = None
            return True
        try:
            bound_call_id = await self.context.service.bind_realtime_call_owner(
                response_headers=response.headers,
                account_id=account_id,
                api_key=self.api_key,
            )
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is None or current_task.cancelling():
                raise
            logger.error("Failed to persist realtime call owner binding")
            self._binding_failure_message = "Realtime call owner binding could not be persisted"
            return False

        except Exception:
            logger.error("Failed to persist realtime call owner binding")
            self._binding_failure_message = "Realtime call owner binding could not be persisted"
            return False
        if bound_call_id is None:
            self._binding_failure_message = "Realtime call response did not include a bindable Location"
            return False
        self._binding_failure_message = None
        return True

    async def finalize(
        self,
        request: Request,
        context: ProxyContext,
        response: CodexControlResponse,
    ) -> Response:
        del context
        if not 200 <= response.status_code < 300:
            return _codex_control_response(response)
        if self._binding_failure_message is not None:
            return _logged_error_json_response(
                request,
                503,
                openai_error(
                    "realtime_call_binding_failed",
                    self._binding_failure_message,
                    error_type="server_error",
                ),
            )
        return _codex_control_response(response)


_PASSTHROUGH_CODEX_CONTROL_ADAPTER = _PassthroughCodexControlAdapter()


async def _codex_control_proxy(
    request: Request,
    path: str,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    adapter: _CodexControlAdapter = _PASSTHROUGH_CODEX_CONTROL_ADAPTER,
    enforce_required_capability_transport: bool = True,
) -> Response:
    if enforce_required_capability_transport:
        capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
        if capability_transport_denial is not None:
            return capability_transport_denial
    try:
        response = await context.service.codex_control_request(
            path,
            method=request.method,
            payload=await request.body() if request.method.upper() not in {"GET", "HEAD"} else None,
            query_params=list(request.query_params.multi_items()),
            headers=request.headers,
            codex_session_affinity=True,
            api_key=api_key,
            privacy_policy=adapter.privacy_policy,
            success_gate=adapter.success_gate,
        )
    except ProxyResponseError as exc:
        if adapter.privacy_policy is CodexControlRequestPrivacyPolicy.PRIVATE_REALTIME:
            return _realtime_call_error_response(request, status_code=exc.status_code)
        return _logged_error_json_response(request, exc.status_code, exc.payload)
    except Exception:
        if adapter.privacy_policy is not CodexControlRequestPrivacyPolicy.PRIVATE_REALTIME:
            raise
        logger.warning(
            "Realtime call creation failed before upstream response request_id=%s",
            get_request_id(),
        )
        return _realtime_call_error_response(request, status_code=503)
    return await adapter.finalize(request, context, response)


@router.get("/thread/goal/get")
@router.post("/thread/goal/get")
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


@realtime_call_router.post("/realtime/calls")
async def codex_realtime_calls(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData = Security(validate_required_proxy_api_key),
) -> Response:
    return await _codex_control_proxy(
        request,
        "realtime/calls",
        context,
        api_key,
        adapter=_RealtimeCallCodexControlAdapter(context, api_key),
    )


@router.post("/safety/arc")
async def codex_safety_arc(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _codex_control_proxy(request, "safety/arc", context, api_key)


@router.post("/alpha/search")
async def codex_alpha_search(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _codex_control_proxy(request, "alpha/search", context, api_key)


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
    return await _codex_control_proxy(
        request,
        "wham/agent-identities/jwks",
        context,
        api_key,
        enforce_required_capability_transport=False,
    )


@router.post(
    "/responses/",
    include_in_schema=False,
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
    payload: dict[str, JsonValue] = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
    explicit_openai_sdk_marker = _has_explicit_openai_sdk_marker(request)
    openai_sdk_request = _is_openai_sdk_request(request, payload)
    native_codex_heartbeat = _is_native_codex_request(request.headers) and not explicit_openai_sdk_marker
    openai_compat_payload = _has_openai_responses_shape(payload)
    try:
        validate_top_level_compaction_trigger_input_shape(payload)
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

    raw_source_model = _effective_optional_model_for_api_key(api_key, responses_payload.model)
    (
        prohibit_fast_mode,
        service_tier_was_enforced,
        pre_normalization_effort,
    ) = await _apply_api_key_enforcement_with_fast_mode_policy(responses_payload, api_key)
    if prohibit_fast_mode and _is_fast_mode_model_alias(raw_source_model):
        raw_source_model = responses_payload.model
    validate_model_access(api_key, responses_payload.model)
    try:
        # Terminal compaction and file pins are structural subscription-only
        # constraints. Previous-response ownership is resolved from continuity
        # evidence below, after a viable source candidate exists.
        source_route_excluded = responses_source_route_excluded(responses_payload)
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return _logged_error_json_response(request, 400, error)
    try:
        source_selection = (
            None
            if source_route_excluded
            else await _select_responses_model_source_with_continuity(
                request,
                responses_payload,
                context,
                api_key,
                raw_model=raw_source_model,
                require_streaming=True,
            )
        )
    except ProxyResponseError as exc:
        return _logged_error_json_response(request, exc.status_code, exc.payload)
    source = source_selection[0] if source_selection is not None else None
    if source_selection is not None:
        responses_payload.model = source_selection[1]
    if source is not None:
        # Opportunistic admission gates subscription *account* capacity;
        # source-routed requests use no account, so a closed/empty pool must
        # not reject them.
        responses_payload.stream = True
        rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
        return await _source_responses_response(
            request,
            responses_payload,
            source=source,
            api_key=api_key,
            rate_limit_headers=rate_limit_headers,
            pre_normalization_effort=pre_normalization_effort,
        )

    apply_enforced_service_tier_model_fallback(
        responses_payload,
        service_tier_was_enforced=service_tier_was_enforced,
    )

    response = await _stream_responses(
        request,
        responses_payload,
        context,
        api_key,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
        api_key_policy_already_applied=True,
        prohibit_fast_mode=prohibit_fast_mode,
        # The Codex CLI consumes codex.* vendor events and the upstream's
        # native event ordering, while OpenAI SDK clients pointed at this
        # compatibility route need the same SSE contract enforcement as /v1.
        enforce_openai_sdk_contract=openai_sdk_request,
        native_codex_heartbeat=native_codex_heartbeat,
    )
    return _mark_subscription_prompt_cache_fallback(response, responses_payload)


@router.get("/opportunistic/admission")
async def opportunistic_admission(
    request: Request,
    model: str | None = None,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
    denial = await _opportunistic_admission_denial(request, context, api_key, model=model)
    if denial is not None:
        return denial
    return JSONResponse({"admitted": True})


@ws_router.websocket("/responses")
async def responses_websocket(
    websocket: WebSocket,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    capability_header_values = _required_capability_values(websocket.headers)
    api_key, denial = await _validate_proxy_websocket_request(
        websocket,
        allow_required_capability=True,
        require_api_key=bool(capability_header_values),
    )
    if denial is not None:
        await websocket.send_denial_response(denial)
        return
    client_turn_state = proxy_affinity_module._sticky_key_from_turn_state_header(websocket.headers)
    turn_state = proxy_affinity_module.ensure_downstream_turn_state(websocket.headers)
    await websocket.accept(headers=proxy_affinity_module.build_downstream_turn_state_accept_headers(turn_state))
    forwarded_headers = dict(websocket.headers)
    if client_turn_state is None:
        forwarded_headers["x-codex-turn-state"] = turn_state
    await context.service.proxy_responses_websocket(
        websocket,
        forwarded_headers,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        api_key=api_key,
        client_ip=resolve_request_client_host(websocket),
        synthesized_turn_state=turn_state if client_turn_state is None else None,
        capability_header_values=capability_header_values,
    )


@v1_router.post(
    "/responses/",
    response_model=OpenAIResponseResult,
    include_in_schema=False,
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
    _raw_trigger_validation: None = Depends(_capture_raw_compaction_trigger_error),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
    raw_trigger_error = _raw_compaction_trigger_error(request)
    if raw_trigger_error is not None:
        return _logged_error_json_response(request, 400, openai_client_payload_error(raw_trigger_error))
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
    raw_source_model = _effective_optional_model_for_api_key(api_key, responses_payload.model)
    (
        prohibit_fast_mode,
        service_tier_was_enforced,
        pre_normalization_effort,
    ) = await _apply_api_key_enforcement_with_fast_mode_policy(responses_payload, api_key)
    if prohibit_fast_mode and _is_fast_mode_model_alias(raw_source_model):
        raw_source_model = responses_payload.model
    validate_model_access(api_key, responses_payload.model)
    try:
        # Share file-pin exclusions with Codex/WebSocket, but do not treat a
        # terminal compaction_trigger as a source-route exclusion: /v1 has no
        # Codex compact path. Previous-response ownership is resolved from
        # continuity evidence below, after a viable source candidate exists.
        source_route_excluded = responses_source_route_excluded(
            responses_payload,
            exclude_compaction=False,
        )
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return _logged_error_json_response(request, 400, error)
    try:
        source_selection = (
            None
            if source_route_excluded
            else await _select_responses_model_source_with_continuity(
                request,
                responses_payload,
                context,
                api_key,
                raw_model=raw_source_model,
                require_streaming=responses_payload.stream is True,
            )
        )
    except ProxyResponseError as exc:
        return _logged_error_json_response(request, exc.status_code, exc.payload)
    source = source_selection[0] if source_selection is not None else None
    if source_selection is not None:
        responses_payload.model = source_selection[1]
    if source is not None:
        # Opportunistic admission gates subscription *account* capacity;
        # source-routed requests use no account, so a closed/empty pool must
        # not reject them.
        rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
        return await _source_responses_response(
            request,
            responses_payload,
            source=source,
            api_key=api_key,
            rate_limit_headers=rate_limit_headers,
            pre_normalization_effort=pre_normalization_effort,
        )
    apply_enforced_service_tier_model_fallback(
        responses_payload,
        service_tier_was_enforced=service_tier_was_enforced,
    )
    if responses_payload.stream:
        response = await _stream_responses(
            request,
            responses_payload,
            context,
            api_key,
            codex_session_affinity=False,
            openai_cache_affinity=True,
            prefer_http_bridge=True,
            api_key_policy_already_applied=True,
            prohibit_fast_mode=prohibit_fast_mode,
        )
    else:
        response = await _collect_responses(
            request,
            responses_payload,
            context,
            api_key,
            codex_session_affinity=False,
            openai_cache_affinity=True,
            prefer_http_bridge=True,
            api_key_policy_already_applied=True,
            prohibit_fast_mode=prohibit_fast_mode,
        )
    return _mark_subscription_prompt_cache_fallback(response, responses_payload)


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
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
    if forwarded_request_context.context.signature_version is None:
        try:
            await context.service.validate_http_bridge_legacy_forward_anchor(
                original_affinity_kind=forwarded_request_context.context.original_affinity_kind,
                original_affinity_key=forwarded_request_context.context.original_affinity_key,
                downstream_turn_state=forwarded_request_context.context.downstream_turn_state,
                previous_response_id=payload.previous_response_id,
                api_key=api_key,
            )
        except ProxyResponseError as exc:
            return _logged_error_json_response(request, exc.status_code, exc.payload)
    skip_limit_enforcement = api_key is None or forwarded_request_context.context.reservation is not None
    forwarded_headers = _strip_internal_bridge_headers(request.headers)
    if forwarded_request_context.context.original_request_unanchored:
        forwarded_headers = {
            key: value for key, value in forwarded_headers.items() if key.lower() != "x-codex-turn-state"
        }
    return await _stream_responses(
        request,
        payload,
        context,
        api_key,
        codex_session_affinity=forwarded_request_context.context.codex_session_affinity,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
        api_key_policy_already_applied=True,
        skip_limit_enforcement=skip_limit_enforcement,
        api_key_reservation_override=forwarded_request_context.context.reservation,
        include_rate_limit_headers=False,
        forwarded_request=True,
        forwarded_original_request_unanchored=forwarded_request_context.context.original_request_unanchored,
        forwarded_legacy_signature=forwarded_request_context.context.signature_version is None,
        forwarded_headers=forwarded_headers,
        forwarded_downstream_turn_state=forwarded_request_context.context.downstream_turn_state,
        forwarded_affinity_kind=forwarded_request_context.context.original_affinity_kind,
        forwarded_affinity_key=forwarded_request_context.context.original_affinity_key,
        forwarded_file_owner_account_id=forwarded_request_context.context.file_owner_account_id,
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
        prohibit_fast_mode=await _prohibit_fast_mode_enabled(),
    )


async def _proxy_realtime_live_websocket_route(
    websocket: WebSocket,
    call_id: str,
    context: ProxyContext,
    *,
    protocol: RealtimeWebSocketProtocol,
    query_params: list[tuple[str, str]],
    redacted_path: str,
) -> None:
    _redact_realtime_live_websocket_scope(websocket, path=redacted_path)
    api_key, denial = await _validate_proxy_websocket_request(websocket, require_api_key=True)
    if denial is not None:
        await websocket.send_denial_response(denial)
        return
    assert api_key is not None
    try:
        if protocol is RealtimeWebSocketProtocol.LIVE_V3 and any(key == "call_id" for key, _value in query_params):
            raise ProxyResponseError(
                400,
                openai_error(
                    "invalid_realtime_call_id",
                    "Path-based realtime sidebands must not include a call_id query parameter",
                ),
            )
        await context.service.proxy_realtime_live_websocket(
            websocket,
            call_id,
            dict(websocket.headers),
            query_params,
            protocol=protocol,
            api_key=api_key,
            client_ip=resolve_request_client_host(websocket),
        )
    except ProxyResponseError as exc:
        if websocket.application_state == WebSocketState.CONNECTING:
            await websocket.send_denial_response(JSONResponse(status_code=exc.status_code, content=exc.payload))
        elif websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011)
    except Exception:
        logger.error("Realtime live websocket setup failed")
        if websocket.application_state == WebSocketState.CONNECTING:
            await websocket.send_denial_response(
                JSONResponse(
                    status_code=503,
                    content=openai_error(
                        "realtime_live_unavailable",
                        "Realtime live websocket is unavailable",
                        error_type="server_error",
                    ),
                )
            )
        elif websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011)


@v1_ws_router.websocket("/live/{call_id:realtime_live_call_id}")
async def v1_live_websocket(
    websocket: WebSocket,
    call_id: str,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    await _proxy_realtime_live_websocket_route(
        websocket,
        call_id,
        context,
        protocol=RealtimeWebSocketProtocol.LIVE_V3,
        query_params=list(websocket.query_params.multi_items()),
        redacted_path="/v1/live/<redacted>",
    )


@ws_router.websocket("/{call_id:realtime_live_call_id}")
async def backend_codex_realtime_live_websocket(
    websocket: WebSocket,
    call_id: str,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    await _proxy_realtime_live_websocket_route(
        websocket,
        call_id,
        context,
        protocol=RealtimeWebSocketProtocol.LIVE_V3,
        query_params=list(websocket.query_params.multi_items()),
        redacted_path="/backend-api/codex/<redacted>",
    )


@v1_ws_router.websocket("/realtime")
async def v1_realtime_websocket(
    websocket: WebSocket,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    query_params = list(websocket.query_params.multi_items())
    call_ids = [value for key, value in query_params if key == "call_id"]
    call_id = call_ids[0] if len(call_ids) == 1 else ""
    await _proxy_realtime_live_websocket_route(
        websocket,
        call_id,
        context,
        protocol=RealtimeWebSocketProtocol.REALTIME_V1_V2,
        query_params=[item for item in query_params if item[0] != "call_id"],
        redacted_path="/v1/realtime",
    )


@v1_ws_router.websocket("/responses")
async def v1_responses_websocket(
    websocket: WebSocket,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    capability_header_values = _required_capability_values(websocket.headers)
    api_key, denial = await _validate_proxy_websocket_request(
        websocket,
        allow_required_capability=True,
        require_api_key=bool(capability_header_values),
    )
    if denial is not None:
        await websocket.send_denial_response(denial)
        return
    client_turn_state = proxy_affinity_module._sticky_key_from_turn_state_header(websocket.headers)
    turn_state = proxy_affinity_module.ensure_downstream_turn_state(websocket.headers)
    await websocket.accept(headers=proxy_affinity_module.build_downstream_turn_state_accept_headers(turn_state))
    forwarded_headers = dict(websocket.headers)
    if client_turn_state is None:
        forwarded_headers["x-codex-turn-state"] = turn_state
    await context.service.proxy_responses_websocket(
        websocket,
        forwarded_headers,
        codex_session_affinity=False,
        openai_cache_affinity=True,
        api_key=api_key,
        client_ip=resolve_request_client_host(websocket),
        synthesized_turn_state=turn_state if client_turn_state is None else None,
        capability_header_values=capability_header_values,
    )


@router.get("/models", response_model=CodexModelsResponse)
async def models(
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _build_codex_models_response(api_key)


@v1_router.get("/models", response_model=None)
async def v1_models(
    request: Request,
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    # Codex clients pointed at this proxy via `openai_base_url` fetch their model
    # catalog from `<base_url>/models` and always append a `client_version` query
    # parameter. They require the Codex catalog shape (`{"models": [...]}`); the
    # OpenAI-compatible list shape fails to parse client-side, and the client
    # silently falls back to its bundled model metadata (stale tool_mode /
    # use_responses_lite flags and context windows). Serve the Codex catalog to
    # Codex clients and keep the OpenAI-compatible shape for everyone else.
    if request.query_params.get("client_version"):
        return await _build_codex_models_response(api_key)
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

    own_limits = [_to_v1_usage_limit_response(limit) for limit in usage.limits]
    upstream_limits = [] if hide_upstream_limits else _ordered_aggregate_limits(aggregate_limits)

    return V1UsageResponse(
        request_count=usage.request_count,
        total_tokens=usage.total_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        total_cost_usd=usage.total_cost_usd,
        limits=own_limits or upstream_limits,
        upstream_limits=upstream_limits,
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
    return [(account.id, account.email) for account in eligible_accounts]


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


def _translate_v1_reset_credit_consume_error(exc: ConsumeResetCreditError) -> HTTPException:
    status_code = exc.status_code if exc.status_code > 0 else 503
    return HTTPException(status_code=status_code, detail=exc.message)


def _translate_v1_reset_credit_fetch_error(exc: ResetCreditFetchError) -> HTTPException:
    status_code = exc.status_code if exc.status_code > 0 else 503
    return HTTPException(status_code=status_code, detail=exc.message)


async def _fetch_authoritative_reset_credit(
    *,
    account_id: str,
    redeem_id: str,
    access_token: str,
    chatgpt_account_id: str | None,
    route: ResolvedUpstreamRoute | None,
) -> ResetCreditItem | None:
    """Resolve a redeem_id against a live upstream fetch when the local snapshot misses.

    A replica-local snapshot can be empty (fresh replica) or stale (peer
    redeemed), so upstream is authoritative before returning a 409. The fresh
    snapshot replaces whatever this replica had cached either way.
    """
    try:
        credits_response = await fetch_reset_credits(
            access_token,
            chatgpt_account_id,
            route=route,
            allow_direct_egress=route is None,
        )
    except ResetCreditFetchError as exc:
        raise _translate_v1_reset_credit_fetch_error(exc) from exc
    await get_rate_limit_reset_credits_store().set(account_id, build_snapshot(credits_response))
    if credits_response.available_count <= 0:
        return None
    for credit in credits_response.credits:
        if credit.id == redeem_id and credit.status == "available":
            return credit
    return None


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
async def _serialize_v1_reset_credit_redeem(account_id: str, *, session: AsyncSession) -> AsyncIterator[None]:
    """Serialize the v1 redeem section, mapping claim contention to the OpenAI envelope.

    The shared serializer raises ``RedeemClaimTimeoutError`` on SQLite claim
    contention; on this surface that must surface as an ``HTTPException`` so
    the ``/v1/*`` handler renders the OpenAI error envelope instead of the
    dashboard one.
    """
    try:
        async with serialize_reset_credit_redeem(account_id, session=session):
            yield
    except RedeemClaimTimeoutError as exc:
        raise HTTPException(
            status_code=409,
            detail="Another reset credit redemption is already in progress for this account",
        ) from exc


@asynccontextmanager
async def _v1_reset_credit_accounts_refresh_scope() -> AsyncIterator[AccountsRepository]:
    async with get_background_session() as session:
        yield AccountsRepository(session)


async def _ensure_v1_reset_credit_account_fresh(account_id: str) -> _V1ResetCreditFreshCredentials:
    async with get_background_session() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        # An account marked for background deletion is already deleted from
        # every consumer's point of view (its credentials are wiped).
        if account is None or account.delete_requested_at is not None:
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


@usage_router.post(
    "/v1/reset-credit",
    response_model=V1ResetCreditRedeemResponse,
)
async def v1_redeem_reset_credit(
    request: Request,
    payload: V1ResetCreditRedeemRequest,
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> V1ResetCreditRedeemResponse | JSONResponse:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
    async with get_background_session() as session:
        account = await AccountsRepository(session).get_by_id(payload.account_id)
        # A pending-deletion account is gone (credentials wiped): treat it
        # exactly like an account outside the pool. ``getattr`` because pool
        # membership tests stub the account with plain namespaces.
        if account is not None and getattr(account, "delete_requested_at", None) is not None:
            account = None
        if not _is_reset_credit_account_in_api_key_pool(account, api_key):
            raise HTTPException(status_code=403, detail="Account is outside the API key pool")
        if account is None:
            raise HTTPException(status_code=403, detail="Account is outside the API key pool")
        account_id = account.id

        async with _serialize_v1_reset_credit_redeem(account_id, session=session):
            try:
                route = await _resolve_reset_credit_route(session, account_id)
            except UpstreamProxyRouteError as exc:
                raise HTTPException(status_code=503, detail="Unable to resolve upstream proxy route") from exc
            try:
                redeem_credentials = await _ensure_v1_reset_credit_account_fresh(account_id)
            except RefreshError as exc:
                raise _translate_v1_reset_credit_refresh_error(exc) from exc
            access_token = TokenEncryptor().decrypt(redeem_credentials.access_token_encrypted)
            # Re-validate against upstream AFTER winning the cross-replica claim
            # rather than trusting the replica-local snapshot. A peer replica may
            # have redeemed this redeem_id while we waited for the claim, and our
            # cached snapshot can still show it as available until the
            # invalidation poll clears it; consuming from the stale cache would
            # send a second upstream consume for an already-redeemed credit.
            credit = await _fetch_authoritative_reset_credit(
                account_id=account_id,
                redeem_id=payload.redeem_id,
                access_token=access_token,
                chatgpt_account_id=redeem_credentials.chatgpt_account_id,
                route=route,
            )
            if credit is None:
                raise HTTPException(status_code=409, detail="Requested reset credit is unavailable")
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
                    await bump_cache_invalidation_local(NAMESPACE_RESET_CREDITS)
                raise _translate_v1_reset_credit_consume_error(exc) from exc
            await get_rate_limit_reset_credits_store().invalidate(account_id)
            await bump_cache_invalidation_local(NAMESPACE_RESET_CREDITS)
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
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
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


async def _apply_api_key_enforcement_with_fast_mode_policy(
    payload: ResponsesRequest | ResponsesCompactRequest,
    api_key: ApiKeyData | None,
) -> tuple[bool, bool, str | None]:
    prohibit_fast_mode = await _prohibit_fast_mode_enabled()
    enforcement = apply_api_key_enforcement(
        payload,
        api_key,
        prohibit_fast_mode=prohibit_fast_mode,
    )
    return (
        prohibit_fast_mode,
        enforcement.service_tier_was_enforced,
        enforcement.pre_normalization_reasoning_effort,
    )


async def _prohibit_fast_mode_enabled() -> bool:
    settings = await get_settings_cache().get()
    return bool(getattr(settings, "prohibit_fast_mode", False))


def _is_fast_mode_model_alias(model: str | None) -> bool:
    return model_alias_requests_fast_mode(model)


async def _rate_limit_headers_for_request(
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> dict[str, str]:
    if await _hide_upstream_quota_for_api_key_clients(api_key):
        return {}
    return await context.service.rate_limit_headers()


async def _release_reservation_deferring_cancellation(
    reservation: ApiKeyUsageReservationData,
) -> None:
    await _await_cleanup_deferring_cancellation(_release_reservation(reservation))


async def _await_result_deferring_cancellation(awaitable: Awaitable[_T]) -> tuple[_T, bool]:
    """Finish an owned awaitable despite repeated cancellation and report whether cancellation arrived."""

    task = asyncio.ensure_future(awaitable)
    cancellation_deferred = False
    with anyio.CancelScope(shield=True):
        while True:
            try:
                return await asyncio.shield(task), cancellation_deferred
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancellation_deferred = True
    raise RuntimeError("unreachable shielded cancellation-deferral state")


async def _await_cleanup_deferring_cancellation(awaitable: Awaitable[object]) -> None:
    """Finish a required cleanup operation despite repeated cancellation delivery."""

    await _await_result_deferring_cancellation(awaitable)


async def _rate_limit_headers_with_reservation_cleanup(
    context: ProxyContext,
    api_key: ApiKeyData | None,
    owned_reservation: ApiKeyUsageReservationData | None,
    *,
    reservation_cleanup: _ResponsesReservationCleanup | None = None,
) -> dict[str, str]:
    try:
        return await _rate_limit_headers_for_request(context, api_key)
    except BaseException:
        if reservation_cleanup is not None:
            await reservation_cleanup.release(action="rate limit headers")
        elif owned_reservation is not None:
            try:
                await _release_reservation_deferring_cancellation(owned_reservation)
            except (Exception, asyncio.CancelledError):
                logger.warning(
                    "Failed to release API key reservation after rate-limit header failure",
                    exc_info=True,
                )
        raise


@dataclass(slots=True)
class _ResponsesReservationCleanup:
    owns_reservation: bool
    reservation: ApiKeyUsageReservationData | None
    scheduler: _ResponsesCleanupScheduler | None
    request_id: str
    released: bool = False

    async def release(self, *, action: str) -> None:
        if not self.owns_reservation or self.released:
            return
        self.released = True
        await _release_reservation_best_effort(
            self.reservation,
            action=action,
            scheduler=self.scheduler,
            request_id=self.request_id,
        )


class _ResponsesCleanupScheduler(Protocol):
    def _schedule_cancel_safe_cleanup(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        action: str,
        request_id: str,
    ) -> asyncio.Task[None]: ...


def _responses_origin_may_release_reservation(
    *,
    service_cleanup_ready_event: asyncio.Event,
    owner_forward_dispatched_event: asyncio.Event | None = None,
    owner_forward_rejected_event: asyncio.Event | None = None,
) -> bool:
    if service_cleanup_ready_event.is_set():
        return False
    if owner_forward_dispatched_event is None or not owner_forward_dispatched_event.is_set():
        return True
    return owner_forward_rejected_event is not None and owner_forward_rejected_event.is_set()


def _responses_cleanup_scheduler(service: object) -> _ResponsesCleanupScheduler | None:
    if callable(getattr(service, "_schedule_cancel_safe_cleanup", None)):
        return cast(_ResponsesCleanupScheduler, service)
    return None


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


@transcribe_router.post("/transcribe", openapi_extra=_BACKEND_TRANSCRIBE_OPENAPI_EXTRA)
async def backend_transcribe(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
    multipart = await _parse_transcription_multipart(request, require_model=False)
    return await _transcribe_request(
        request=request,
        multipart=multipart,
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
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
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
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
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


@v1_router.post(
    "/audio/transcriptions",
    openapi_extra=_V1_AUDIO_TRANSCRIPTIONS_OPENAPI_EXTRA,
)
async def v1_audio_transcriptions(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
    multipart = await _parse_transcription_multipart(request, require_model=True)
    assert multipart.model is not None
    model = multipart.model
    source = await _select_audio_transcriptions_model_source(model, api_key)
    if source is not None:
        validate_model_access(api_key, model)
        rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
        return await _source_audio_transcription_response(
            request=request,
            model=model,
            multipart=multipart,
            source=source,
            api_key=api_key,
            rate_limit_headers=rate_limit_headers,
        )
    if model != _TRANSCRIPTION_MODEL:
        return _logged_error_json_response(
            request,
            status_code=400,
            content=_openai_invalid_transcription_model_error(model),
        )
    return await _transcribe_request(
        request=request,
        multipart=multipart,
        context=context,
        api_key=api_key,
    )


class V1EmbeddingsRequest(BaseModel):
    """OpenAI-compatible embeddings request.

    Only ``model`` and ``input`` are validated; other OpenAI params
    (``encoding_format``, ``dimensions``, ``user``, …) pass through to the
    model source verbatim.
    """

    model_config = ConfigDict(extra="allow")

    model: str
    input: str | list[str] | list[int] | list[list[int]]


@v1_router.post("/embeddings")
async def v1_embeddings(
    request: Request,
    payload: V1EmbeddingsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
    model = payload.model
    rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
    source = await _select_embeddings_model_source(model, api_key)
    if source is None:
        # Embeddings have no subscription-backed fallback: only configured
        # model sources can serve them.
        return _logged_error_json_response(
            request,
            status_code=404,
            content=openai_error(
                "model_not_found",
                f"The model '{model}' does not exist or no enabled model source supports embeddings for it",
                error_type="invalid_request_error",
            ),
            headers=rate_limit_headers,
        )
    validate_model_access(api_key, model)
    return await _source_embeddings_response(
        request=request,
        model=model,
        payload=payload,
        source=source,
        api_key=api_key,
        rate_limit_headers=rate_limit_headers,
    )


@router.post(
    "/images/generations",
    response_model=None,
    include_in_schema=False,
)
@v1_router.post(
    "/images/generations",
    response_model=None,
)
async def v1_images_generations(
    request: Request,
    payload: V1ImagesGenerationsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        _record_required_capability_image_transport_denial(
            request,
            route="generations",
            model=payload.model,
            stream=bool(payload.stream),
        )
        return capability_transport_denial
    return await _proxy_images_generation_request(
        request=request,
        payload=payload,
        context=context,
        api_key=api_key,
    )


def _coerce_image_form_stream_for_observability(stream: str | None) -> bool:
    if stream is None:
        return False
    return stream.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _record_images_edit_early_rejection(
    *,
    model: str | None,
    stream: bool,
    started_at: float,
) -> None:
    record_images_route_observability(
        route="edits",
        model=model,
        stream=stream,
        status=400,
        outcome="invalid_request",
        started_at=started_at,
    )


def _record_required_capability_image_transport_denial(
    request: Request,
    *,
    route: Literal["generations", "edits"],
    model: str | None,
    stream: bool,
) -> None:
    started_at = getattr(request.state, IMAGE_ROUTE_STARTED_AT_STATE, None)
    if not isinstance(started_at, float):
        started_at = time.perf_counter()
    record_images_route_observability(
        route=route,
        model=model,
        stream=stream,
        status=400,
        outcome="invalid_request",
        started_at=started_at,
    )


def _images_edit_invalid_request_response(
    request: Request,
    *,
    message: str,
    param: str,
    model: str | None,
    stream: bool,
    started_at: float,
) -> JSONResponse:
    _record_images_edit_early_rejection(
        model=model,
        stream=stream,
        started_at=started_at,
    )
    return _logged_error_json_response(
        request,
        400,
        images_service_module.make_invalid_request_error(message, param=param),
    )


@v1_router.post(
    "/images/edits",
    response_model=None,
    openapi_extra=_V1_IMAGES_EDITS_OPENAPI_EXTRA,
)
async def v1_images_edits(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        _record_required_capability_image_transport_denial(
            request,
            route="edits",
            model=None,
            stream=False,
        )
        return capability_transport_denial
    started_at = time.perf_counter()
    raise_for_unsupported_multipart_content_encoding(request)

    async with bounded_multipart_form(request, IMAGE_EDITS_MULTIPART_POLICY) as form:
        model = optional_text(form, "model")
        stream = optional_text(form, "stream")
        observability_stream = _coerce_image_form_stream_for_observability(stream)
        setattr(request.state, IMAGE_ROUTE_MODEL_STATE, model)
        setattr(request.state, IMAGE_ROUTE_STREAM_STATE, observability_stream)

        prompt = required_text(form, "prompt")
        n = optional_text(form, "n")
        size = optional_text(form, "size")
        quality = optional_text(form, "quality")
        background = optional_text(form, "background")
        output_format = optional_text(form, "output_format")
        output_compression = optional_text(form, "output_compression")
        moderation = optional_text(form, "moderation")
        partial_images = optional_text(form, "partial_images")
        input_fidelity = optional_text(form, "input_fidelity")
        user = optional_text(form, "user")

        file_items = uploaded_file_items(form)
        unknown_file = next(
            (field for field, _upload in file_items if field not in {"image", "image[]", "mask"}),
            None,
        )
        if unknown_file is not None:
            return _images_edit_invalid_request_response(
                request,
                message=f"Unknown file field '{unknown_file}'.",
                param=unknown_file,
                model=model,
                stream=observability_stream,
                started_at=started_at,
            )

        merged_images = ordered_uploads(form, ("image", "image[]"))
        if len(merged_images) > 16:
            return _images_edit_invalid_request_response(
                request,
                message="At most 16 image parts are allowed.",
                param="image",
                model=model,
                stream=observability_stream,
                started_at=started_at,
            )
        if not merged_images:
            return _images_edit_invalid_request_response(
                request,
                message="At least one ``image`` (or ``image[]``) multipart part is required.",
                param="image",
                model=model,
                stream=observability_stream,
                started_at=started_at,
            )

        mask_values = form.getlist("mask")
        if len(mask_values) > 1:
            return _images_edit_invalid_request_response(
                request,
                message="At most one mask part is allowed.",
                param="mask",
                model=model,
                stream=observability_stream,
                started_at=started_at,
            )
        mask = optional_upload(form, "mask")

        images_payload: list[tuple[bytes, str | None]] = []
        for upload in merged_images:
            data = await read_bounded_upload(
                upload,
                max_bytes=IMAGE_EDITS_MULTIPART_POLICY.max_file_bytes,
                param="image",
            )
            if not data:
                return _images_edit_invalid_request_response(
                    request,
                    message="image part is empty",
                    param="image",
                    model=model,
                    stream=observability_stream,
                    started_at=started_at,
                )
            images_payload.append((data, upload.content_type))

        mask_payload: tuple[bytes, str | None] | None = None
        if mask is not None:
            mask_data = await read_bounded_upload(
                mask,
                max_bytes=IMAGE_EDITS_MULTIPART_POLICY.max_file_bytes,
                param="mask",
            )
            if not mask_data:
                return _images_edit_invalid_request_response(
                    request,
                    message="mask part is empty",
                    param="mask",
                    model=model,
                    stream=observability_stream,
                    started_at=started_at,
                )
            mask_payload = (mask_data, mask.content_type)

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
        _record_images_edit_early_rejection(
            model=model,
            stream=observability_stream,
            started_at=started_at,
        )
        return _logged_error_json_response(request, 400, openai_validation_error(exc))

    return await _proxy_images_edit_request(
        request=request,
        payload=form_payload,
        images=images_payload,
        mask=mask_payload,
        context=context,
        api_key=api_key,
        started_at=started_at,
    )


@router.post("/images/edits", response_model=None, include_in_schema=False)
async def codex_images_edits(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    """Accept Codex's JSON data-URL image-edit payload on its native base URL.

    The built-in Codex image tool sends ``images: [{"image_url":
    "data:<mime>;base64,..."}]`` rather than the multipart ``image`` parts
    used by the public OpenAI Images API. Decode that transport shape here,
    then delegate to the shared edit pipeline so validation and upstream
    behavior remain identical.
    """
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        _record_required_capability_image_transport_denial(
            request,
            route="edits",
            model=None,
            stream=False,
        )
        return capability_transport_denial
    started_at = time.perf_counter()
    try:
        raw_payload = await request.json()
    except (JSONDecodeError, UnicodeDecodeError):
        _record_images_edit_early_rejection(
            model=None,
            stream=False,
            started_at=started_at,
        )
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                "Expected a JSON request body.",
                param="prompt",
            ),
        )
    if not is_json_mapping(raw_payload):
        _record_images_edit_early_rejection(
            model=None,
            stream=False,
            started_at=started_at,
        )
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                "Expected a JSON object request body.",
                param="prompt",
            ),
        )

    raw_model = raw_payload.get("model")
    observability_model = raw_model if isinstance(raw_model, str) else None
    raw_stream = raw_payload.get("stream", False)
    observability_stream = raw_stream if isinstance(raw_stream, bool) else False
    raw_form: dict[str, object] = {
        "model": raw_model,
        "prompt": raw_payload.get("prompt"),
        "n": raw_payload.get("n", 1),
        "size": raw_payload.get("size", "auto"),
        "quality": raw_payload.get("quality", "auto"),
        "background": raw_payload.get("background", "auto"),
        "output_format": raw_payload.get("output_format", "png"),
        "output_compression": raw_payload.get("output_compression", 100),
        "moderation": raw_payload.get("moderation", "auto"),
        "partial_images": raw_payload.get("partial_images"),
        "stream": raw_payload.get("stream", False),
        "input_fidelity": raw_payload.get("input_fidelity"),
        "user": raw_payload.get("user"),
    }
    try:
        form_payload = V1ImagesEditsForm.model_validate(raw_form)
    except ValidationError as exc:
        _record_images_edit_early_rejection(
            model=observability_model,
            stream=observability_stream,
            started_at=started_at,
        )
        return _logged_error_json_response(request, 400, openai_validation_error(exc))

    raw_images = raw_payload.get("images")
    if not isinstance(raw_images, list) or not raw_images:
        _record_images_edit_early_rejection(
            model=form_payload.model,
            stream=bool(form_payload.stream),
            started_at=started_at,
        )
        return _logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                "At least one `images[].image_url` data URL is required.",
                param="images",
            ),
        )

    images: list[tuple[bytes, str | None]] = []
    for index, raw_image in enumerate(raw_images):
        if not is_json_mapping(raw_image) or not isinstance(image_url := raw_image.get("image_url"), str):
            _record_images_edit_early_rejection(
                model=form_payload.model,
                stream=bool(form_payload.stream),
                started_at=started_at,
            )
            return _logged_error_json_response(
                request,
                400,
                images_service_module.make_invalid_request_error(
                    f"images[{index}].image_url must be a base64 data URL.",
                    param="images",
                ),
            )
        try:
            images.append(images_service_module.decode_data_url(image_url))
        except ValueError as exc:
            _record_images_edit_early_rejection(
                model=form_payload.model,
                stream=bool(form_payload.stream),
                started_at=started_at,
            )
            return _logged_error_json_response(
                request,
                400,
                images_service_module.make_invalid_request_error(str(exc), param="images"),
            )

    return await _proxy_images_edit_request(
        request=request,
        payload=form_payload,
        images=images,
        mask=None,
        context=context,
        api_key=api_key,
        started_at=started_at,
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
    started_at = time.perf_counter()
    route: Literal["generations"] = "generations"
    stream_requested = bool(payload.stream)
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
        record_images_route_observability(
            route=route,
            model=effective_model,
            stream=stream_requested,
            status=400,
            outcome="invalid_request",
            started_at=started_at,
        )
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
        record_images_route_observability(
            route=route,
            model=effective_model,
            stream=stream_requested,
            status=400,
            outcome="invalid_request",
            started_at=started_at,
        )
        return _logged_error_json_response(request, 400, openai_client_payload_error(exc))

    public_model = payload.model
    assert public_model is not None
    host_model = _IMAGES_HOST_MODEL

    try:
        validate_model_access(api_key, effective_model)
    except ProxyModelNotAllowed:
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=stream_requested,
            status=403,
            outcome="model_not_allowed",
            started_at=started_at,
        )
        raise

    rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
    try:
        reservation = await _enforce_request_limits(
            api_key,
            request_model=effective_model,
            request_service_tier=None,
        )
    except ProxyRateLimitError:
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=stream_requested,
            status=429,
            outcome="rate_limited",
            started_at=started_at,
        )
        raise

    try:
        responses_payload = images_service_module.images_generation_to_responses_request(payload, host_model=host_model)
    except ValidationError as exc:
        await _release_reservation(reservation)
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=stream_requested,
            status=400,
            outcome="invalid_request",
            started_at=started_at,
        )
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
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=stream_requested,
            status=prime_error.status_code,
            outcome="upstream_error",
            started_at=started_at,
        )
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
            except ProxyResponseError:
                captured["image_stream_outcome"] = "upstream_error"
                raise
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
                    context.service,
                    api_key,
                    reservation,
                    model=public_model,
                    input_tokens=_input if isinstance(_input, int) else None,
                    output_tokens=_output if isinstance(_output, int) else None,
                    cached_input_tokens=_cached if isinstance(_cached, int) else None,
                )
                stream_outcome = captured.get("image_stream_outcome")
                if not isinstance(stream_outcome, str):
                    stream_outcome = "stream_closed"
                record_images_route_observability(
                    route=route,
                    model=public_model,
                    stream=True,
                    status=200,
                    outcome=stream_outcome,
                    started_at=started_at,
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
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=False,
            status=exc.status_code,
            outcome="upstream_error",
            started_at=started_at,
        )
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
        context.service,
        api_key,
        reservation,
        model=public_model,
        input_tokens=_input if isinstance(_input, int) else None,
        output_tokens=_output if isinstance(_output, int) else None,
        cached_input_tokens=_cached if isinstance(_cached, int) else None,
    )

    if error_envelope is not None:
        error_status = _status_for_image_error_envelope(error_envelope)
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=False,
            status=error_status,
            outcome="image_error",
            started_at=started_at,
        )
        return _logged_error_json_response(
            request,
            error_status,
            error_envelope,
            headers=rate_limit_headers,
        )
    assert response_payload is not None
    images_result = images_service_module.images_response_from_responses(response_payload)
    if not isinstance(images_result, V1ImageResponse):
        image_status = _status_for_image_error_envelope(images_result)
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=False,
            status=image_status,
            outcome="image_error",
            started_at=started_at,
        )
        return _logged_error_json_response(
            request,
            image_status,
            images_result,
            headers=rate_limit_headers,
        )
    record_images_route_observability(
        route=route,
        model=public_model,
        stream=False,
        status=200,
        outcome="success",
        started_at=started_at,
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
    started_at: float,
) -> Response:
    route: Literal["edits"] = "edits"
    stream_requested = bool(payload.stream)
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
        record_images_route_observability(
            route=route,
            model=effective_model,
            stream=stream_requested,
            status=400,
            outcome="invalid_request",
            started_at=started_at,
        )
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
        record_images_route_observability(
            route=route,
            model=effective_model,
            stream=stream_requested,
            status=400,
            outcome="invalid_request",
            started_at=started_at,
        )
        return _logged_error_json_response(request, 400, openai_client_payload_error(exc))

    public_model = payload.model
    assert public_model is not None
    host_model = _IMAGES_HOST_MODEL

    try:
        validate_model_access(api_key, effective_model)
    except ProxyModelNotAllowed:
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=stream_requested,
            status=403,
            outcome="model_not_allowed",
            started_at=started_at,
        )
        raise

    rate_limit_headers = await _rate_limit_headers_for_request(context, api_key)
    try:
        reservation = await _enforce_request_limits(
            api_key,
            request_model=effective_model,
            request_service_tier=None,
        )
    except ProxyRateLimitError:
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=stream_requested,
            status=429,
            outcome="rate_limited",
            started_at=started_at,
        )
        raise

    try:
        responses_payload = images_service_module.images_edit_to_responses_request(
            payload,
            host_model=host_model,
            images=images,
            mask=mask,
        )
    except (ValidationError, ValueError) as exc:
        await _release_reservation(reservation)
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=stream_requested,
            status=400,
            outcome="invalid_request",
            started_at=started_at,
        )
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
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=stream_requested,
            status=prime_error.status_code,
            outcome="upstream_error",
            started_at=started_at,
        )
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
            except ProxyResponseError:
                captured["image_stream_outcome"] = "upstream_error"
                raise
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
                    context.service,
                    api_key,
                    reservation,
                    model=public_model,
                    input_tokens=_input if isinstance(_input, int) else None,
                    output_tokens=_output if isinstance(_output, int) else None,
                    cached_input_tokens=_cached if isinstance(_cached, int) else None,
                )
                stream_outcome = captured.get("image_stream_outcome")
                if not isinstance(stream_outcome, str):
                    stream_outcome = "stream_closed"
                record_images_route_observability(
                    route=route,
                    model=public_model,
                    stream=True,
                    status=200,
                    outcome=stream_outcome,
                    started_at=started_at,
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
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=False,
            status=exc.status_code,
            outcome="upstream_error",
            started_at=started_at,
        )
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
        context.service,
        api_key,
        reservation,
        model=public_model,
        input_tokens=_input if isinstance(_input, int) else None,
        output_tokens=_output if isinstance(_output, int) else None,
        cached_input_tokens=_cached if isinstance(_cached, int) else None,
    )

    if error_envelope is not None:
        error_status = _status_for_image_error_envelope(error_envelope)
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=False,
            status=error_status,
            outcome="image_error",
            started_at=started_at,
        )
        return _logged_error_json_response(
            request,
            error_status,
            error_envelope,
            headers=rate_limit_headers,
        )
    assert response_payload is not None
    images_result = images_service_module.images_response_from_responses(response_payload)
    if not isinstance(images_result, V1ImageResponse):
        image_status = _status_for_image_error_envelope(images_result)
        record_images_route_observability(
            route=route,
            model=public_model,
            stream=False,
            status=image_status,
            outcome="image_error",
            started_at=started_at,
        )
        return _logged_error_json_response(
            request,
            image_status,
            images_result,
            headers=rate_limit_headers,
        )
    record_images_route_observability(
        route=route,
        model=public_model,
        stream=False,
        status=200,
        outcome="success",
        started_at=started_at,
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
    try:
        return await _build_codex_models_response_body(api_key)
    finally:
        if reservation is not None:
            await _release_reservation_deferring_cancellation(reservation)


async def _build_codex_models_response_body(
    api_key: ApiKeyData | None,
) -> Response:

    allowed_models = _allowed_models_for_api_key(api_key)
    exact_source_allowed_models = _exact_source_allowed_models_for_api_key(api_key)
    visibility_allowed_models = _codex_model_visibility_allowed_models(api_key)

    registry = get_model_registry()
    models = registry.get_models_with_fallback()
    metadata_models = registry.get_models_for_metadata()
    source_models = [
        model
        for model in await _list_enabled_source_catalog_models(api_key, require_responses=True)
        if model.raw.get("supports_streaming") is True
    ]
    visible_source_models = []
    for source_model in source_models:
        if visibility_allowed_models is None:
            if exact_source_allowed_models is not None:
                if source_model.slug not in exact_source_allowed_models:
                    continue
            elif not is_public_model(source_model, allowed_models):
                continue
        visible_source_models.append(source_model)
    visible_source_models.sort(
        key=lambda model: (
            _effective_source_codex_visibility(
                model,
                visibility_allowed_models=visibility_allowed_models,
                exact_source_allowed_models=exact_source_allowed_models,
            )
            != "list"
        )
    )
    source_model_slugs = {
        model.slug
        for model in visible_source_models
        if _effective_source_codex_visibility(
            model,
            visibility_allowed_models=visibility_allowed_models,
            exact_source_allowed_models=exact_source_allowed_models,
        )
        == "list"
    }

    if not models and not metadata_models and not source_models:
        return JSONResponse(content=CodexModelsResponse(models=[], data=[]).model_dump(mode="json"))

    entries: list[CodexModelEntry] = []
    data: list[ModelListItem] = []
    seen_slugs: set[str] = set()
    for slug, model in models.items():
        if not _is_codex_backend_catalog_model(model):
            continue
        if visibility_allowed_models is None:
            if allowed_models is not None and slug not in allowed_models:
                continue
            entry = _to_codex_model_entry(model)
            entries.append(entry)
            seen_slugs.add(slug)
            if model.supported_in_api and entry.visibility == "list":
                data.append(_to_model_list_item(slug, model, created=_model_list_created_at(model)))
            continue
        entry = _to_codex_model_entry(
            model,
            visibility="list" if slug in visibility_allowed_models else "hide",
        )
        entries.append(entry)
        seen_slugs.add(slug)
        if model.supported_in_api and entry.visibility == "list":
            data.append(_to_model_list_item(slug, model, created=_model_list_created_at(model)))
    for slug, model in metadata_models.items():
        if slug in models or slug in source_model_slugs or not _is_codex_backend_catalog_model(model):
            continue
        if visibility_allowed_models is None and allowed_models is not None and slug not in allowed_models:
            continue
        entries.append(_to_codex_model_entry(model, visibility="hide"))
        seen_slugs.add(slug)
    for model in visible_source_models:
        if model.slug in seen_slugs:
            continue
        if visibility_allowed_models is None:
            entry = _to_codex_model_entry(model)
            entries.append(entry)
            seen_slugs.add(model.slug)
            if model.supported_in_api and entry.visibility == "list":
                data.append(_to_model_list_item(model.slug, model, created=_model_list_created_at(model)))
            continue
        entry = _to_codex_model_entry(
            model,
            visibility=_effective_source_codex_visibility(
                model,
                visibility_allowed_models=visibility_allowed_models,
                exact_source_allowed_models=exact_source_allowed_models,
            ),
        )
        entries.append(entry)
        seen_slugs.add(model.slug)
        if model.supported_in_api and entry.visibility == "list":
            data.append(_to_model_list_item(model.slug, model, created=_model_list_created_at(model)))
    return JSONResponse(content=CodexModelsResponse(models=entries, data=data).model_dump(mode="json"))


async def _build_models_response(api_key: ApiKeyData | None) -> Response:
    reservation = await _enforce_request_limits(
        api_key,
        request_model=None,
        request_service_tier=None,
    )
    try:
        return await _build_models_response_body(api_key)
    finally:
        if reservation is not None:
            await _release_reservation_deferring_cancellation(reservation)


async def _build_models_response_body(
    api_key: ApiKeyData | None,
) -> Response:

    allowed_models = _allowed_models_for_api_key(api_key)
    exact_source_allowed_models = _exact_source_allowed_models_for_api_key(api_key)
    created = int(time.time())

    registry = get_model_registry()
    models = registry.get_models_with_fallback()
    source_models = await _list_enabled_source_catalog_models(api_key)

    if not models and not source_models:
        return JSONResponse(content=_dump_v1_models_response(ModelListResponse(data=[])))

    items: list[ModelListItem] = []
    seen_slugs: set[str] = set()
    for slug, model in models.items():
        if not is_public_model(model, allowed_models):
            continue
        items.append(_to_model_list_item(slug, model, created=created))
        seen_slugs.add(slug)
    for model in source_models:
        if model.slug in seen_slugs:
            continue
        if exact_source_allowed_models is not None:
            if model.slug not in exact_source_allowed_models:
                continue
        elif not is_public_model(model, allowed_models):
            continue
        items.append(_to_model_list_item(model.slug, model, created=created))
        seen_slugs.add(model.slug)
    return JSONResponse(content=_dump_v1_models_response(ModelListResponse(data=items)))


async def _list_enabled_source_catalog_models(
    api_key: ApiKeyData | None,
    *,
    require_responses: bool = False,
) -> list[UpstreamModel]:
    async with get_background_session() as session:
        sources = await ModelSourcesRepository(session).list_enabled_sources()
        # ``close_session`` rolls back the read transaction, which would
        # expire the loaded rows; detach them so their attributes stay
        # readable after this session boundary.
        detach_session_objects(session)
    if require_responses:
        sources = [source for source in sources if source.supports_responses]
    assigned_source_ids = _allowed_source_ids_for_api_key(api_key)
    if assigned_source_ids is not None:
        sources = [source for source in sources if source.id in assigned_source_ids]
    return source_models_to_upstream_models(sources)


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


def _exact_source_allowed_models_for_api_key(api_key: ApiKeyData | None) -> set[str] | None:
    if api_key is None:
        return None
    allowed_models = set(api_key.allowed_models) if api_key.allowed_models else None
    if api_key.enforced_model:
        forced = {api_key.enforced_model}
        return forced if allowed_models is None else (allowed_models & forced)
    return allowed_models


def _canonical_model_set(models: Iterable[str]) -> set[str]:
    return {_canonical_model_slug(model) for model in models}


def _canonical_model_slug(model: str) -> str:
    return resolve_model_alias(model) or model


def _to_model_list_item(slug: str, model: UpstreamModel, *, created: int) -> ModelListItem:
    context_window = _resolved_context_window(model)
    return ModelListItem.model_validate(
        {
            "id": slug,
            "created": created,
            "owned_by": "codex-lb",
            "metadata": _to_model_metadata(model, context_window=context_window),
            "api_types": ["chat_completions"],
            "capabilities": _v1_model_capabilities(model, context_window=context_window),
            "context_length": context_window,
            "contextLength": context_window,
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


def _is_codex_backend_catalog_model(model: UpstreamModel) -> bool:
    if model.supported_in_api:
        return True
    return model.raw.get("shell_type") == "shell_command"


_CODEX_WIRE_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})


def _codex_model_truncation_policy(model: UpstreamModel) -> CodexTruncationPolicy:
    if "truncation_policy" in model.raw:
        try:
            return CodexTruncationPolicy.model_validate(model.raw["truncation_policy"])
        except ValidationError:
            pass
    mode = "bytes" if model.slug == "gpt-5.2" else "tokens"
    return CodexTruncationPolicy(mode=mode, limit=10_000)


def _codex_model_experimental_supported_tools(model: UpstreamModel) -> list[str]:
    tools = model.raw.get("experimental_supported_tools")
    if not is_json_list(tools):
        return []
    return [tool for tool in tools if isinstance(tool, str)]


def _codex_wire_reasoning_levels(model: UpstreamModel) -> list[ReasoningLevelSchema]:
    return [
        ReasoningLevelSchema(effort=level.effort, description=level.description)
        for level in model.supported_reasoning_levels
        if level.effort in _CODEX_WIRE_REASONING_EFFORTS
    ]


def _codex_wire_default_reasoning_level(model: UpstreamModel) -> str | None:
    default = model.default_reasoning_level
    if default in _CODEX_WIRE_REASONING_EFFORTS:
        return default
    return None


def _to_codex_model_entry(model: UpstreamModel, *, visibility: str | None = None) -> CodexModelEntry:
    raw = model.raw
    reasoning_levels = _codex_wire_reasoning_levels(model)

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
        "truncation_policy",
        "experimental_supported_tools",
    }
    for key, value in raw.items():
        if key not in skip_keys and isinstance(value, (bool, int, float, str, type(None), list, Mapping)):
            extra[key] = value

    # If context_window is overridden, also override max_context_window to match
    effective_cw = _resolved_context_window(model)
    if effective_cw != model.context_window and "max_context_window" in extra:
        extra["max_context_window"] = effective_cw

    return CodexModelEntry(
        slug=model.slug,
        display_name=model.display_name,
        description=model.description,
        base_instructions=model.base_instructions,
        default_reasoning_level=_codex_wire_default_reasoning_level(model),
        supported_reasoning_levels=reasoning_levels,
        supported_in_api=model.supported_in_api,
        priority=model.priority,
        minimal_client_version=model.minimal_client_version,
        supports_reasoning_summaries=model.supports_reasoning_summaries,
        support_verbosity=model.support_verbosity,
        default_verbosity=model.default_verbosity,
        supports_parallel_tool_calls=model.supports_parallel_tool_calls,
        context_window=effective_cw,
        input_modalities=list(model.input_modalities),
        available_in_plans=sorted(model.available_in_plans),
        prefer_websockets=model.prefer_websockets,
        visibility=visibility or _model_visibility(model),
        # Codex deserializes the complete catalog atomically. Repair legacy
        # bootstrap/retained metadata at this final wire boundary so one hidden
        # entry cannot invalidate otherwise-current live model metadata.
        truncation_policy=_codex_model_truncation_policy(model),
        experimental_supported_tools=_codex_model_experimental_supported_tools(model),
        **extra,
    )


def _resolved_context_window(model: UpstreamModel) -> int:
    # An explicit operator context-window override is an assertion about the usable
    # input budget, so it must also reach the generic OpenAI-compatible fields
    # (`context_length`, `contextLength`, `capabilities.context_length`, and
    # `metadata.input_context_window`). Generic clients read those rather than
    # `metadata.context_window` and would otherwise cap themselves at the
    # un-overridden upstream budget while Codex-native clients use the wider window.
    # The override is clamped to the upstream-declared `max_context_window` so it can
    # never advertise more input than the backend sanctions — the same clamp the Codex
    # client applies to `model_context_window` in config.toml. The clamp only applies
    # when upstream declares a ceiling strictly above `context_window`: bootstrap
    # subscription models (`_bootstrap_model`) and source-catalog models
    # (`source_models_to_upstream_models`) synthesize `max_context_window ==
    # context_window` purely so Codex clients can parse the entry, and treating that
    # parseability default as a real ceiling would silently disable every raise
    # override for those models.
    #
    # This is the single resolution point for the reported window: the Codex-native
    # `context_window`/`max_context_window` rewrite, `metadata.context_window`, and
    # every input-budget field all share this one value, so an override above the
    # backend ceiling can never split one model into two contradictory budgets.
    overrides = get_settings().model_context_window_overrides
    override = overrides.get(model.slug)
    if override is None:
        return model.context_window
    max_context_window = model.raw.get("max_context_window")
    if (
        isinstance(max_context_window, int)
        and not isinstance(max_context_window, bool)
        and max_context_window > model.context_window
    ):
        return min(override, max_context_window)
    return override


def _v1_max_output_tokens(model: UpstreamModel) -> int | None:
    raw_value = model.raw.get("max_output_tokens")
    if isinstance(raw_value, int):
        return raw_value
    return _V1_MAX_OUTPUT_TOKEN_OVERRIDES.get(model.slug)


def _v1_model_capabilities(model: UpstreamModel, *, context_window: int) -> dict[str, JsonValue]:
    supports_streaming_raw = model.raw.get("supports_streaming")
    supports_streaming = supports_streaming_raw if isinstance(supports_streaming_raw, bool) else True
    return {
        "context_length": context_window,
        "max_output_tokens": _v1_max_output_tokens(model),
        "supports_reasoning": _v1_supports_reasoning(model),
        "supports_images": _v1_supports_vision(model),
        "supportsImages": _v1_supports_vision(model),
        "supports_vision": _v1_supports_vision(model),
        "supports_tool_use": model.supports_parallel_tool_calls,
        "supports_streaming": supports_streaming,
        "input_modalities": list(model.input_modalities),
        "output_modalities": ["text"],
    }


def _v1_supports_reasoning(model: UpstreamModel) -> bool:
    if bool(model.supported_reasoning_levels) or model.supports_reasoning_summaries:
        return True
    # Source models whose operator declared no levels and no summary support
    # opt in via raw metadata instead, so /v1/models reflects reality.
    return model.raw.get("supports_reasoning") is True


def _v1_supports_vision(model: UpstreamModel) -> bool:
    return "image" in model.input_modalities


def _model_visibility(model: UpstreamModel) -> str:
    visibility = model.raw.get("visibility")
    return visibility if isinstance(visibility, str) else "list"


def _effective_source_codex_visibility(
    model: UpstreamModel,
    *,
    visibility_allowed_models: set[str] | None,
    exact_source_allowed_models: set[str] | None,
) -> str:
    raw_visibility = _model_visibility(model)
    if raw_visibility != "list":
        return raw_visibility
    if (
        visibility_allowed_models is not None
        and exact_source_allowed_models is not None
        and model.slug not in exact_source_allowed_models
    ):
        return "hide"
    return "list"


def _to_model_metadata(model: UpstreamModel, *, context_window: int) -> ModelMetadata:
    return ModelMetadata(
        display_name=model.display_name,
        description=model.description,
        context_window=context_window,
        input_context_window=context_window,
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
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
    cursor_compat_client = _is_cursor_compat_client(request, api_key)
    effective_model = _effective_model_for_api_key(api_key, payload.model)

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
    # The replaced effort is discarded: the enforced Responses payload built
    # here is only ever forwarded to a subscription. This endpoint does
    # source-route, but that branch forwards the untouched original chat
    # payload, so there is nothing for a restore to undo.
    prohibit_fast_mode, service_tier_was_enforced, _ = await _apply_api_key_enforcement_with_fast_mode_policy(
        responses_payload, api_key
    )
    if prohibit_fast_mode and _is_fast_mode_model_alias(effective_model):
        effective_model = responses_payload.model
    validate_model_access(api_key, responses_payload.model)
    source_selection = (
        await _select_chat_model_source(
            responses_payload.model,
            api_key,
            raw_model=effective_model,
            require_streaming=payload.stream is True,
        )
        if not responses_shaped_payload and payload.messages is not None
        else None
    )
    source = source_selection[0] if source_selection is not None else None
    request_model = source_selection[1] if source_selection is not None else responses_payload.model
    if source is None:
        apply_enforced_service_tier_model_fallback(
            responses_payload,
            service_tier_was_enforced=service_tier_was_enforced,
        )
        # Opportunistic admission gates subscription *account* capacity;
        # source-routed requests use no account, so a closed/empty pool must
        # not reject them.
        admission_denial = await _opportunistic_admission_denial(
            request, context, api_key, model=responses_payload.model
        )
        if admission_denial is not None:
            return admission_denial
    reservation = await _enforce_request_limits(
        api_key,
        request_model=request_model,
        request_service_tier=responses_payload.service_tier,
        request_usage_budget=estimate_api_key_request_usage(responses_payload),
    )
    if source is not None:
        return await _source_chat_completion_response(
            request,
            payload,
            source=source,
            model=request_model,
            api_key=api_key,
            allowed_reasoning_effort=(
                responses_payload._codex_lb_client_reasoning_effort
                if api_key is not None and api_key.allowed_reasoning_efforts is not None
                else None
            ),
            reservation=reservation,
            rate_limit_headers=rate_limit_headers,
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
    capacity_wait_event = asyncio.Event()
    capacity_ready_event = _CapacityStartupReadyEvent()
    capacity_wait_token = _bind_propagated_capacity_startup_wait(capacity_wait_event)
    capacity_ready_token = _bind_propagated_capacity_startup_ready(capacity_ready_event)
    try:
        stream, startup_error = await _probe_chat_stream_startup_error(
            stream,
            timeout_seconds=startup_probe_timeout,
            capacity_wait_event=capacity_wait_event,
            capacity_ready_event=capacity_ready_event,
        )
    finally:
        _reset_propagated_capacity_startup_ready(capacity_ready_token)
        _reset_propagated_capacity_startup_wait(capacity_wait_token)
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
                on_keepalive=lambda: _record_stream_keepalive("chat_completions"),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        try:
            first = await stream.__anext__()
        except StopAsyncIteration:
            first = None
        except ProxyResponseError as exc:
            return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)

        result = await collect_chat_completion(
            _prepend_first(first, stream),
            model=responses_payload.model,
        )
    finally:
        await _aclose_stream(stream)
    if isinstance(result, OpenAIErrorEnvelopeModel):
        status_code, envelope = _mask_previous_response_not_found_error(result)
        return _logged_error_json_response(
            request,
            status_code,
            content=envelope.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    if cursor_compat_client and isinstance(result, ChatCompletion):
        _apply_cursor_usage_fallback(result, payload, source="non_stream")
    return JSONResponse(
        content=result.model_dump(mode="json", exclude_none=True),
        status_code=200,
        headers=rate_limit_headers,
    )


async def _select_chat_model_source(
    model: str,
    api_key: ApiKeyData | None,
    *,
    raw_model: str | None = None,
    require_streaming: bool = False,
) -> tuple[ModelSource, str] | None:
    assigned_source_ids = _allowed_source_ids_for_api_key(api_key)
    exact_allowed_models = set(api_key.allowed_models) if api_key and api_key.allowed_models else None
    candidates = [candidate for candidate in (raw_model, model) if candidate]
    if not candidates:
        return None
    deduped_candidates = list(dict.fromkeys(candidates))
    registry_models = get_model_registry().get_models_with_fallback()
    async with get_background_session() as session:
        repository = ModelSourcesRepository(session)
        for candidate in deduped_candidates:
            if exact_allowed_models is not None and candidate not in exact_allowed_models:
                continue
            subscription_model = registry_models.get(candidate)
            if assigned_source_ids is None and subscription_model is not None:
                continue
            source = await repository.find_chat_source_for_model(
                candidate,
                allowed_source_ids=assigned_source_ids,
                require_streaming=require_streaming,
            )
            if source is not None:
                break
        else:
            source = None
        # ``close_session`` rolls back the read transaction, which would
        # expire the loaded row; detach it so the forwarding path can read
        # its attributes after this session boundary.
        detach_session_objects(session)
        return (source, candidate) if source is not None else None


async def _select_responses_model_source(
    model: str,
    api_key: ApiKeyData | None,
    *,
    raw_model: str | None = None,
    require_streaming: bool = False,
) -> tuple[ModelSource, str] | None:
    # Shared with the WebSocket path so both transports agree on which models
    # belong to a model source.
    return await select_responses_model_source(
        model,
        api_key,
        raw_model=raw_model,
        require_streaming=require_streaming,
    )


async def _select_responses_model_source_with_continuity(
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    raw_model: str | None = None,
    require_streaming: bool = False,
) -> tuple[ModelSource, str] | None:
    """Select a source unless recorded subscription continuity owns the anchor."""
    source_selection = await _select_responses_model_source(
        payload.model,
        api_key,
        raw_model=raw_model,
        require_streaming=require_streaming,
    )
    if source_selection is None or payload.previous_response_id is None:
        return source_selection
    owner_account_id = await context.service._resolve_websocket_previous_response_owner(
        previous_response_id=payload.previous_response_id,
        api_key=api_key,
        session_id=proxy_affinity_module._owner_lookup_session_id_from_headers(request.headers),
        surface="http_source_route",
    )
    return None if owner_account_id is not None else source_selection


async def _select_embeddings_model_source(model: str, api_key: ApiKeyData | None) -> ModelSource | None:
    assigned_source_ids = _allowed_source_ids_for_api_key(api_key)
    exact_allowed_models = _exact_source_allowed_models_for_api_key(api_key)
    if exact_allowed_models is not None and model not in exact_allowed_models:
        return None
    async with get_background_session() as session:
        source = await ModelSourcesRepository(session).find_embeddings_source_for_model(
            model,
            allowed_source_ids=assigned_source_ids,
        )
        detach_session_objects(session)
        return source


async def _select_audio_transcriptions_model_source(model: str, api_key: ApiKeyData | None) -> ModelSource | None:
    assigned_source_ids = _allowed_source_ids_for_api_key(api_key)
    exact_allowed_models = _exact_source_allowed_models_for_api_key(api_key)
    if exact_allowed_models is not None and model not in exact_allowed_models:
        return None
    if assigned_source_ids is None and model == _TRANSCRIPTION_MODEL:
        return None
    async with get_background_session() as session:
        source = await ModelSourcesRepository(session).find_audio_transcriptions_source_for_model(
            model,
            allowed_source_ids=assigned_source_ids,
        )
        detach_session_objects(session)
        return source


def _allowed_source_ids_for_api_key(api_key: ApiKeyData | None) -> set[str] | None:
    return allowed_source_ids_for_api_key(api_key)


async def _parse_transcription_multipart(
    request: Request,
    *,
    require_model: bool,
) -> _ParsedTranscriptionMultipart:
    raise_for_unsupported_multipart_content_encoding(request)
    async with bounded_multipart_form(request, TRANSCRIPTION_MULTIPART_POLICY) as form:
        model = required_text(form, "model") if require_model else None
        file = required_upload(form, "file")
        prompt = optional_text(form, "prompt")
        audio_bytes = await read_bounded_upload(
            file,
            max_bytes=TRANSCRIPTION_MULTIPART_POLICY.max_file_bytes,
            param="file",
        )
        return _ParsedTranscriptionMultipart(
            audio_bytes=audio_bytes,
            filename=file.filename or "audio.wav",
            content_type=file.content_type,
            model=model,
            prompt=prompt,
            ordered_text_fields=tuple(ordered_text_items(form, excluded_fields=("file",))),
        )


async def _source_embeddings_response(
    *,
    request: Request,
    model: str,
    payload: "V1EmbeddingsRequest",
    source: ModelSource,
    api_key: ApiKeyData | None,
    rate_limit_headers: Mapping[str, str],
) -> Response:
    reservation = await _enforce_request_limits(
        api_key,
        request_model=model,
        request_service_tier=None,
    )
    outbound = payload.model_dump(exclude_unset=True)
    outbound["model"] = model
    try:
        result = await forward_source_embeddings(source, outbound)
    except asyncio.CancelledError:
        release_exc: BaseException | None = None
        if reservation is not None:
            try:
                await _release_reservation_deferring_cancellation(reservation)
            except BaseException as exc:
                release_exc = exc
        if release_exc is not None:
            logger.warning(
                "Failed to release source embeddings reservation after request cancellation source_id=%s model=%s",
                source.id,
                model,
                exc_info=release_exc,
            )
        raise
    except ModelSourceForwardingError as exc:
        await _release_reservation(reservation)
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="error",
            error_code=_source_error_code(exc.payload),
            error_message=_source_error_message(exc.payload),
            upstream_status_code=exc.upstream_status_code,
        )
        return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)
    if result.usage is None and _reservation_requires_usage(reservation):
        await _release_reservation(reservation)
        error = openai_error(
            "usage_unavailable",
            "OpenAI-compatible model source embeddings response did not include token usage for a limited API key",
            error_type="server_error",
        )
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="error",
            error_code="usage_unavailable",
            error_message="source embeddings response missing token usage",
            upstream_status_code=result.upstream_status_code,
        )
        return _logged_error_json_response(request, 502, error, headers=rate_limit_headers)
    settled = await _settle_source_reservation(
        reservation,
        source=source,
        model=model,
        usage=result.usage,
    )
    if not settled:
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="error",
            error_code="usage_settlement_failed",
            error_message="source usage settlement failed",
            upstream_status_code=result.upstream_status_code,
        )
        return _logged_error_json_response(
            request,
            502,
            _source_usage_settlement_failed_error(),
            headers=rate_limit_headers,
        )
    await _log_source_chat_completion(
        request,
        source=source,
        api_key=api_key,
        model=model,
        status="success",
        usage=result.usage,
        upstream_status_code=result.upstream_status_code,
    )
    return JSONResponse(content=result.payload, headers=dict(rate_limit_headers))


async def _source_audio_transcription_response(
    *,
    request: Request,
    model: str,
    multipart: _ParsedTranscriptionMultipart,
    source: ModelSource,
    api_key: ApiKeyData | None,
    rate_limit_headers: Mapping[str, str],
) -> Response:
    reservation = await _enforce_request_limits(
        api_key,
        request_model=model,
        request_service_tier=None,
    )
    try:
        result = await forward_source_audio_transcription(
            source,
            audio_bytes=multipart.audio_bytes,
            filename=multipart.filename,
            content_type=multipart.content_type,
            fields=list(multipart.ordered_text_fields),
        )
    except ModelSourceForwardingError as exc:
        await _release_reservation(reservation)
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="error",
            error_code=_source_error_code(exc.payload),
            error_message=_source_error_message(exc.payload),
            upstream_status_code=exc.upstream_status_code,
        )
        return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)

    # ASR billing prefers audio duration: when the source model has a
    # per-minute rate and the response carries a duration, settle cost from
    # the duration with zero tokens. Only when there is no usable duration
    # cost do we fall back to token usage (and fail closed for limited keys
    # if neither is available).
    audio_cost_usd = (
        source_model_audio_cost_usd(source, model, result.audio_seconds) if result.audio_seconds is not None else None
    )
    if audio_cost_usd is not None:
        settle_usage: SourceUsage | None = SourceUsage(input_tokens=0, output_tokens=0)
        cost_override: float | None = audio_cost_usd
    else:
        settle_usage = result.usage
        cost_override = None
        if result.usage is None and _reservation_requires_usage(reservation):
            await _release_reservation(reservation)
            error = openai_error(
                "usage_unavailable",
                "OpenAI-compatible model source transcription response did not include token usage "
                "or a usable duration for a limited API key",
                error_type="server_error",
            )
            await _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=model,
                status="error",
                error_code="usage_unavailable",
                error_message="source transcription response missing token usage and duration cost",
                upstream_status_code=result.upstream_status_code,
            )
            return _logged_error_json_response(request, 502, error, headers=rate_limit_headers)

    settled = await _settle_source_reservation(
        reservation,
        source=source,
        model=model,
        usage=settle_usage,
        cost_usd_override=cost_override,
    )
    if not settled:
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="error",
            error_code="usage_settlement_failed",
            error_message="source usage settlement failed",
            upstream_status_code=result.upstream_status_code,
        )
        return _logged_error_json_response(
            request,
            502,
            _source_usage_settlement_failed_error(),
            headers=rate_limit_headers,
        )
    await _log_source_chat_completion(
        request,
        source=source,
        api_key=api_key,
        model=model,
        status="success",
        usage=settle_usage,
        timings=result.timings,
        cost_usd_override=cost_override,
        upstream_status_code=result.upstream_status_code,
    )
    headers = dict(rate_limit_headers)
    if result.content_type is not None:
        headers["content-type"] = result.content_type
    return Response(content=result.body, status_code=200, headers=headers)


async def _source_responses_response(
    request: Request,
    payload: ResponsesRequest,
    *,
    source: ModelSource,
    api_key: ApiKeyData | None,
    rate_limit_headers: Mapping[str, str],
    pre_normalization_effort: str | None,
) -> Response:
    # This is the first point where the request is known to be served by a
    # model source rather than a subscription account, so it is the only place
    # the reasoning-effort workaround can be undone safely.
    restore_source_reasoning_effort(
        payload,
        source,
        pre_normalization_effort=pre_normalization_effort,
    )
    reservation = await _enforce_request_limits(
        api_key,
        request_model=payload.model,
        request_service_tier=payload.service_tier,
        request_usage_budget=estimate_api_key_request_usage(payload),
    )
    source_payload = payload.model_dump_for_forwarding()
    preserve_materialized_provider_alias = payload._codex_lb_provider_reasoning_effort_materialized and (
        api_key is None or (api_key.enforced_reasoning_effort is None and api_key.allowed_reasoning_efforts is None)
    )
    if preserve_materialized_provider_alias:
        reasoning = source_payload.get("reasoning")
        if isinstance(reasoning, dict):
            reasoning = {key: value for key, value in reasoning.items() if key != "effort"}
            if reasoning:
                source_payload["reasoning"] = reasoning
            else:
                source_payload.pop("reasoning")
    if api_key is not None and (
        api_key.enforced_reasoning_effort is not None
        or (api_key.allowed_reasoning_efforts is not None and payload._codex_lb_client_reasoning_effort is not None)
    ):
        normalize_source_reasoning_aliases(source_payload)
    source_reasoning_effort = (
        api_key.enforced_reasoning_effort
        if api_key is not None and api_key.enforced_reasoning_effort is not None
        else payload._codex_lb_client_reasoning_effort
    )
    if source_reasoning_effort is not None and not preserve_materialized_provider_alias:
        source_reasoning_effort = resolve_wire_reasoning_effort(source_reasoning_effort)
        reasoning = source_payload.get("reasoning")
        if isinstance(reasoning, dict):
            source_payload["reasoning"] = {
                **reasoning,
                "effort": source_reasoning_effort,
            }
        else:
            source_payload["reasoning"] = {"effort": source_reasoning_effort}
    strip_replayed_tool_call_namespaces_from_payload(source_payload)
    source_payload["stream"] = bool(payload.stream)
    _apply_source_response_request_overrides(source_payload, source_model_request_overrides(source, payload.model))
    _drop_unsupported_source_response_tools(
        source_payload,
        supported_tool_types=source_model_supported_tool_types(source, payload.model),
    )

    if payload.stream:
        try:
            stream = await stream_source_responses(source, source_payload)
        except ModelSourceForwardingError as exc:
            await _release_reservation(reservation)
            await _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=payload.model,
                status="error",
                error_code=_source_error_code(exc.payload),
                error_message=_source_error_message(exc.payload),
                upstream_status_code=exc.upstream_status_code,
            )
            return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)
        if _reservation_requires_usage(reservation):
            return await _buffered_limited_source_chat_stream_response(
                request,
                source=source,
                api_key=api_key,
                model=payload.model,
                reservation=reservation,
                stream=stream.body,
                usage_holder=stream.usage_holder,
                rate_limit_headers=rate_limit_headers,
            )
        body = _source_chat_stream_with_settlement(
            stream.body,
            usage_holder=stream.usage_holder,
            request=request,
            source=source,
            api_key=api_key,
            model=payload.model,
            reservation=reservation,
        )
        return StreamingResponse(
            body,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                **rate_limit_headers,
            },
        )

    try:
        result = await forward_source_responses(source, source_payload)
    except ModelSourceForwardingError as exc:
        await _release_reservation(reservation)
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=payload.model,
            status="error",
            error_code=_source_error_code(exc.payload),
            error_message=_source_error_message(exc.payload),
            upstream_status_code=exc.upstream_status_code,
        )
        return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)

    if result.usage is None and _reservation_requires_usage(reservation):
        await _release_reservation(reservation)
        error = openai_error(
            "usage_unavailable",
            "OpenAI-compatible model source response did not include usage for a limited API key",
            error_type="server_error",
        )
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=payload.model,
            status="error",
            error_code="usage_unavailable",
            error_message="source response missing usage",
            upstream_status_code=result.upstream_status_code,
        )
        return _logged_error_json_response(request, 502, error, headers=rate_limit_headers)

    settled = await _settle_source_reservation(reservation, source=source, model=payload.model, usage=result.usage)
    if not settled:
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=payload.model,
            status="error",
            error_code="usage_settlement_failed",
            error_message="source usage settlement failed",
            upstream_status_code=result.upstream_status_code,
        )
        return _logged_error_json_response(
            request,
            502,
            _source_usage_settlement_failed_error(),
            headers=rate_limit_headers,
        )
    await _log_source_chat_completion(
        request,
        source=source,
        api_key=api_key,
        model=payload.model,
        status="success",
        usage=result.usage,
        timings=result.timings,
        upstream_status_code=result.upstream_status_code,
    )
    return JSONResponse(content=result.payload, status_code=200, headers=rate_limit_headers)


# Keys that source_request_overrides must never clobber: the routed model slug
# is owned by source selection, and the stream flag drives SSE-vs-JSON response
# handling on the proxy side.
_SOURCE_RESPONSE_OVERRIDE_PROTECTED_KEYS = frozenset({"model", "stream"})


def _apply_source_response_request_overrides(
    payload: dict[str, JsonValue],
    overrides: Mapping[str, JsonValue],
) -> None:
    for key, value in overrides.items():
        if key in _SOURCE_RESPONSE_OVERRIDE_PROTECTED_KEYS:
            continue
        if key == "options" and isinstance(value, Mapping):
            existing_options = payload.get("options")
            merged_options = dict(existing_options) if isinstance(existing_options, Mapping) else {}
            merged_options.update(value)
            payload["options"] = merged_options
            continue
        payload[key] = value


def _source_tool_type_supported(tool_type: JsonValue | None, supported_tool_types: frozenset[str]) -> bool:
    if tool_type == "function":
        return True
    return isinstance(tool_type, str) and tool_type in supported_tool_types


def _normalize_source_allowed_tool_choice_aliases(payload: dict[str, JsonValue]) -> None:
    """Normalize legacy tool-type aliases nested under ``allowed_tools``.

    Request validation normalizes the ``tools`` list and the top-level
    ``tool_choice`` type (``web_search_preview`` -> ``web_search``) but leaves
    entries nested under an ``allowed_tools`` choice untouched. Sources that
    reject the legacy alias or validate the forced choice against the
    (normalized) tools list would fail such requests, so source-bound payloads
    always get the same alias normalization applied to the nested entries.
    """
    tool_choice = payload.get("tool_choice")
    if not is_json_mapping(tool_choice):
        return
    if tool_choice.get("type") != "allowed_tools":
        return
    allowed = tool_choice.get("tools")
    if not is_json_list(allowed):
        return
    normalized_allowed: list[JsonValue] = []
    changed = False
    for entry in allowed:
        if is_json_mapping(entry):
            entry_type = entry.get("type")
            if isinstance(entry_type, str):
                normalized_type = normalize_tool_type(entry_type)
                if normalized_type != entry_type:
                    entry = {**entry, "type": normalized_type}
                    changed = True
        normalized_allowed.append(entry)
    if not changed:
        return
    updated_choice: dict[str, JsonValue] = dict(tool_choice)
    updated_choice["tools"] = normalized_allowed
    payload["tool_choice"] = updated_choice


# Maps hosted tool types to the Responses ``include`` prefixes that only make
# sense while that tool is present (see _RESPONSES_INCLUDE_ALLOWLIST in
# app.core.openai.requests). When the source filter drops a hosted tool, the
# matching include entries must be pruned with it: sources that validate
# ``include`` would otherwise reject the request the filter just repaired.
# Non-tool-specific entries (``reasoning.*``, ``message.*``) are never pruned.
_SOURCE_TOOL_INCLUDE_PREFIXES: dict[str, tuple[str, ...]] = {
    "web_search": ("web_search_call.",),
    "file_search": ("file_search_call.",),
    "code_interpreter": ("code_interpreter_call.",),
    "computer_use": ("computer_call_output.",),
    "computer_use_preview": ("computer_call_output.",),
}


def _prune_source_tool_specific_includes(payload: dict[str, JsonValue], dropped_tool_types: frozenset[str]) -> None:
    prefixes = tuple(
        prefix for tool_type in dropped_tool_types for prefix in _SOURCE_TOOL_INCLUDE_PREFIXES.get(tool_type, ())
    )
    if not prefixes:
        return
    include = payload.get("include")
    if not is_json_list(include):
        return
    kept_include: list[JsonValue] = [
        entry for entry in include if not (isinstance(entry, str) and entry.startswith(prefixes))
    ]
    if len(kept_include) == len(include):
        return
    if not kept_include:
        payload.pop("include", None)
        return
    payload["include"] = kept_include


def _drop_unsupported_source_response_tools(
    payload: dict[str, JsonValue],
    *,
    supported_tool_types: frozenset[str],
) -> None:
    _normalize_source_allowed_tool_choice_aliases(payload)
    tools = payload.get("tools")
    if not is_json_list(tools):
        return
    kept_tools: list[JsonValue] = []
    kept_types: set[str] = set()
    dropped_types: set[str] = set()
    for tool in tools:
        if not is_json_mapping(tool):
            continue
        tool_type = tool.get("type")
        if not _source_tool_type_supported(tool_type, supported_tool_types):
            if isinstance(tool_type, str):
                dropped_types.add(normalize_tool_type(tool_type))
            continue
        kept_tools.append(tool)
        if isinstance(tool_type, str):
            kept_types.add(tool_type)
    if len(kept_tools) == len(tools):
        return
    _prune_source_tool_specific_includes(payload, frozenset(dropped_types))
    if not kept_tools:
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
        payload.pop("parallel_tool_calls", None)
        return
    payload["tools"] = kept_tools
    _drop_dangling_source_tool_choice(payload, frozenset(kept_types))


def _drop_dangling_source_tool_choice(payload: dict[str, JsonValue], kept_types: frozenset[str]) -> None:
    """Keep tool_choice consistent with the tools that survived filtering.

    A forced tool_choice that references a dropped hosted tool (for example
    ``{"type": "web_search"}``) would make the source reject the request, so it
    falls back to the provider default by removing the key. ``function``-typed
    choices always stay: function tools are never dropped by the filter.

    Entries under ``allowed_tools`` carry the same tool-type alias
    normalization as the ``tools`` list by the time this runs (see
    ``_normalize_source_allowed_tool_choice_aliases``), so a legacy-alias
    forced choice keeps matching the normalized tool it targets.
    """
    tool_choice = payload.get("tool_choice")
    if not is_json_mapping(tool_choice):
        return
    choice_type = tool_choice.get("type")
    if choice_type == "function":
        return
    if choice_type == "allowed_tools":
        allowed = tool_choice.get("tools")
        if not is_json_list(allowed):
            return
        kept_allowed: list[JsonValue] = [
            entry
            for entry in allowed
            if is_json_mapping(entry) and _source_tool_type_supported(entry.get("type"), kept_types)
        ]
        if len(kept_allowed) == len(allowed):
            return
        if not kept_allowed:
            payload.pop("tool_choice", None)
            return
        updated_choice: dict[str, JsonValue] = dict(tool_choice)
        updated_choice["tools"] = kept_allowed
        payload["tool_choice"] = updated_choice
        return
    if not _source_tool_type_supported(choice_type, kept_types):
        payload.pop("tool_choice", None)


async def _source_chat_completion_response(
    request: Request,
    payload: ChatCompletionsRequest,
    *,
    source: ModelSource,
    model: str,
    api_key: ApiKeyData | None,
    allowed_reasoning_effort: str | None = None,
    reservation: ApiKeyUsageReservationData | None,
    rate_limit_headers: Mapping[str, str],
) -> Response:
    source_payload = payload.model_dump(mode="json", exclude_none=True)
    source_payload["model"] = model
    source_payload["stream"] = bool(payload.stream)
    apply_api_key_enforcement_to_chat_payload(
        source_payload,
        api_key,
        allowed_reasoning_effort=allowed_reasoning_effort,
        materialize_allowed_reasoning_effort=allowed_reasoning_effort is not None,
    )
    sanitize_source_chat_payload(
        source_payload,
        allow_reasoning=source_model_supports_reasoning(source, model),
    )

    if payload.stream:
        stream_options = source_payload.get("stream_options")
        if isinstance(stream_options, dict):
            stream_options["include_usage"] = True
        else:
            source_payload["stream_options"] = {"include_usage": True}
        try:
            stream = await stream_source_chat_completion(source, source_payload)
        except ModelSourceForwardingError as exc:
            await _release_reservation(reservation)
            await _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=model,
                status="error",
                error_code=_source_error_code(exc.payload),
                error_message=_source_error_message(exc.payload),
                upstream_status_code=exc.upstream_status_code,
            )
            return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)
        except asyncio.CancelledError:
            release_exc: BaseException | None = None
            if reservation is not None:
                try:
                    await _release_reservation_deferring_cancellation(reservation)
                except BaseException as exc:
                    release_exc = exc
            await _await_cleanup_deferring_cancellation(
                _log_source_chat_completion(
                    request,
                    source=source,
                    api_key=api_key,
                    model=model,
                    status="cancelled",
                    error_code="client_disconnected",
                    error_message="client disconnected during source stream setup",
                )
            )
            if release_exc is not None:
                logger.warning(
                    "Failed to release source stream setup reservation after client disconnect source_id=%s model=%s",
                    source.id,
                    model,
                    exc_info=release_exc,
                )
            raise
        except BaseException:
            if reservation is not None:
                await _release_reservation_deferring_cancellation(reservation)
            raise
        if _reservation_requires_usage(reservation):
            return await _buffered_limited_source_chat_stream_response(
                request,
                source=source,
                api_key=api_key,
                model=model,
                reservation=reservation,
                stream=stream.body,
                usage_holder=stream.usage_holder,
                rate_limit_headers=rate_limit_headers,
            )
        body = _source_chat_stream_with_settlement(
            stream.body,
            usage_holder=stream.usage_holder,
            request=request,
            source=source,
            api_key=api_key,
            model=model,
            reservation=reservation,
        )
        return StreamingResponse(
            body,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        result = await forward_chat_completion(source, source_payload)
    except ModelSourceForwardingError as exc:
        await _release_reservation(reservation)
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="error",
            error_code=_source_error_code(exc.payload),
            error_message=_source_error_message(exc.payload),
            upstream_status_code=exc.upstream_status_code,
        )
        return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)
    except asyncio.CancelledError:
        release_exc: BaseException | None = None
        if reservation is not None:
            try:
                await _release_reservation_deferring_cancellation(reservation)
            except BaseException as exc:
                release_exc = exc
        await _await_cleanup_deferring_cancellation(
            _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=model,
                status="cancelled",
                error_code="client_disconnected",
                error_message="client disconnected during source request setup",
            )
        )
        if release_exc is not None:
            logger.warning(
                "Failed to release source request setup reservation after client disconnect source_id=%s model=%s",
                source.id,
                model,
                exc_info=release_exc,
            )
        raise
    except BaseException:
        if reservation is not None:
            await _release_reservation_deferring_cancellation(reservation)
        raise

    if result.usage is None and _reservation_requires_usage(reservation):
        await _release_reservation(reservation)
        error = openai_error(
            "usage_unavailable",
            "OpenAI-compatible model source response did not include usage for a limited API key",
            error_type="server_error",
        )
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="error",
            error_code="usage_unavailable",
            error_message="source response missing usage",
            upstream_status_code=result.upstream_status_code,
        )
        return _logged_error_json_response(request, 502, error, headers=rate_limit_headers)

    settled, settlement_deferred_cancellation = await _await_result_deferring_cancellation(
        _settle_source_reservation(reservation, source=source, model=model, usage=result.usage)
    )
    if settlement_deferred_cancellation:
        await _await_cleanup_deferring_cancellation(
            _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=model,
                status="cancelled",
                usage=result.usage,
                timings=result.timings,
                error_code="client_disconnected",
                error_message="client disconnected during source usage settlement",
                upstream_status_code=result.upstream_status_code,
            )
        )
        raise asyncio.CancelledError
    if not settled:
        _, log_deferred_cancellation = await _await_result_deferring_cancellation(
            _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=model,
                status="error",
                error_code="usage_settlement_failed",
                error_message="source usage settlement failed",
                upstream_status_code=result.upstream_status_code,
            )
        )
        if log_deferred_cancellation:
            raise asyncio.CancelledError
        return _logged_error_json_response(
            request,
            502,
            _source_usage_settlement_failed_error(),
            headers=rate_limit_headers,
        )
    _, log_deferred_cancellation = await _await_result_deferring_cancellation(
        _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="success",
            usage=result.usage,
            timings=result.timings,
            upstream_status_code=result.upstream_status_code,
        )
    )
    if log_deferred_cancellation:
        raise asyncio.CancelledError
    return JSONResponse(content=result.payload, status_code=200, headers=rate_limit_headers)


async def _buffered_limited_source_chat_stream_response(
    request: Request,
    *,
    source: ModelSource,
    api_key: ApiKeyData | None,
    model: str,
    reservation: ApiKeyUsageReservationData | None,
    stream: AsyncIterator[bytes],
    usage_holder: SourceUsageHolder,
    rate_limit_headers: Mapping[str, str],
) -> Response:
    chunks: list[bytes] = []
    total_bytes = 0
    buffer_limit_exceeded = False
    try:
        async for chunk in stream:
            total_bytes += len(chunk)
            if total_bytes > _SOURCE_LIMITED_STREAM_BUFFER_BYTES:
                buffer_limit_exceeded = True
                break
            chunks.append(chunk)
        if buffer_limit_exceeded:
            # Returning while the generator is suspended at a yield would keep
            # the leased upstream session/response open until GC finalizes the
            # abandoned generator; close it deterministically.
            await _aclose_stream(stream)
            await _release_reservation(reservation)
            error = openai_error(
                "source_stream_buffer_limit_exceeded",
                "OpenAI-compatible model source stream exceeded the limited-key accounting buffer",
                error_type="server_error",
            )
            await _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=model,
                status="error",
                error_code="source_stream_buffer_limit_exceeded",
                error_message="source stream buffer limit exceeded",
            )
            return _logged_error_json_response(request, 502, error, headers=rate_limit_headers)
    except asyncio.CancelledError as cancel_exc:
        # Starlette cancels this task when the downstream client disconnects;
        # CancelledError is a BaseException, so without this branch the
        # reservation would stay charged until stale-reservation cleanup.
        close_exc: BaseException | None = None
        release_exc: BaseException | None = None
        try:
            await _await_cleanup_deferring_cancellation(_aclose_stream(stream))
        except BaseException as exc:
            close_exc = exc
        if reservation is not None:
            try:
                await _release_reservation_deferring_cancellation(reservation)
            except BaseException as exc:
                release_exc = exc
        await _await_cleanup_deferring_cancellation(
            _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=model,
                status="cancelled",
                usage=usage_holder.usage,
                timings=usage_holder.timings,
                error_code="client_disconnected",
                error_message="client disconnected during source stream buffering",
            )
        )
        if release_exc is not None:
            logger.warning(
                "Failed to release buffered source stream reservation after client disconnect source_id=%s model=%s",
                source.id,
                model,
                exc_info=release_exc,
            )
        if close_exc is not None:
            raise close_exc
        raise cancel_exc
    except ModelSourceForwardingError as exc:
        await _release_reservation(reservation)
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="error",
            error_code=_source_error_code(exc.payload),
            error_message=_source_error_message(exc.payload),
            upstream_status_code=exc.upstream_status_code,
        )
        return _logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)
    except Exception as exc:
        await _release_reservation(reservation)
        error = openai_error(
            "model_source_stream_error",
            "OpenAI-compatible model source stream failed",
            error_type="server_error",
        )
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="error",
            error_code="model_source_stream_error",
            error_message=exc.__class__.__name__,
        )
        return _logged_error_json_response(request, 502, error, headers=rate_limit_headers)

    if usage_holder.usage is None:
        await _release_reservation(reservation)
        error = openai_error(
            "usage_unavailable",
            "OpenAI-compatible model source stream did not include usage for a limited API key",
            error_type="server_error",
        )
        await _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="error",
            error_code="usage_unavailable",
            error_message="source stream missing usage",
        )
        return _logged_error_json_response(request, 502, error, headers=rate_limit_headers)

    settled, settlement_deferred_cancellation = await _await_result_deferring_cancellation(
        _settle_source_reservation(reservation, source=source, model=model, usage=usage_holder.usage)
    )
    if settlement_deferred_cancellation:
        await _await_cleanup_deferring_cancellation(
            _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=model,
                status="cancelled",
                usage=usage_holder.usage,
                timings=usage_holder.timings,
                error_code="client_disconnected",
                error_message="client disconnected during source stream usage settlement",
            )
        )
        raise asyncio.CancelledError
    if not settled:
        _, log_deferred_cancellation = await _await_result_deferring_cancellation(
            _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=model,
                status="error",
                error_code="usage_settlement_failed",
                error_message="source usage settlement failed",
            )
        )
        if log_deferred_cancellation:
            raise asyncio.CancelledError
        return _logged_error_json_response(
            request,
            502,
            _source_usage_settlement_failed_error(),
            headers=rate_limit_headers,
        )
    _, log_deferred_cancellation = await _await_result_deferring_cancellation(
        _log_source_chat_completion(
            request,
            source=source,
            api_key=api_key,
            model=model,
            status="success",
            usage=usage_holder.usage,
            timings=usage_holder.timings,
        )
    )
    if log_deferred_cancellation:
        raise asyncio.CancelledError

    async def body() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", **rate_limit_headers},
    )


async def _source_chat_stream_with_settlement(
    stream: AsyncIterator[bytes],
    *,
    usage_holder: SourceUsageHolder,
    request: Request,
    source: ModelSource,
    api_key: ApiKeyData | None,
    model: str,
    reservation: ApiKeyUsageReservationData | None,
) -> AsyncIterator[bytes]:
    status = "success"
    error_code: str | None = None
    error_message: str | None = None
    try:
        async for chunk in stream:
            yield chunk
    except (asyncio.CancelledError, GeneratorExit):
        # Client disconnect surfaces as CancelledError (task cancellation) or
        # GeneratorExit (generator aclose); both bypass ``except Exception``
        # and would leave the reservation charged until stale cleanup.
        # Recorded as a cancelled terminal — the same normal client-side
        # disconnect classification the main proxy streaming path writes —
        # so it stays out of every error-rate numerator and top_error
        # (#1552).
        status = "cancelled"
        error_code = "client_disconnected"
        error_message = "client disconnected before stream completed"
        try:
            await _await_cleanup_deferring_cancellation(_aclose_stream(stream))
        finally:
            if reservation is not None:
                await _release_reservation_deferring_cancellation(reservation)
        raise
    except ModelSourceForwardingError as exc:
        status = "error"
        error_code = _source_error_code(exc.payload)
        error_message = _source_error_message(exc.payload)
        await _release_reservation(reservation)
        raise
    except Exception as exc:
        status = "error"
        error_code = "model_source_stream_error"
        error_message = exc.__class__.__name__
        await _release_reservation(reservation)
        raise
    else:
        settled, settlement_deferred_cancellation = await _await_result_deferring_cancellation(
            _settle_source_reservation(reservation, source=source, model=model, usage=usage_holder.usage)
        )
        if settlement_deferred_cancellation:
            status = "cancelled"
            error_code = "client_disconnected"
            error_message = "client disconnected during source usage settlement"
            raise asyncio.CancelledError
        if not settled:
            status = "error"
            error_code = "usage_settlement_failed"
            error_message = "source usage settlement failed"
        if usage_holder.usage is None and _reservation_requires_usage(reservation):
            status = "error"
            error_code = "usage_unavailable"
            error_message = "source stream missing usage"
            logger.warning(
                "source stream completed without usage for limited API key source_id=%s key_id=%s model=%s",
                source.id,
                api_key.id if api_key else None,
                model,
            )
    finally:
        await _await_cleanup_deferring_cancellation(
            _log_source_chat_completion(
                request,
                source=source,
                api_key=api_key,
                model=model,
                status=status,
                usage=usage_holder.usage,
                timings=usage_holder.timings,
                error_code=error_code,
                error_message=error_message,
                upstream_status_code=None,
            )
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
    forwarded_original_request_unanchored: bool = False,
    forwarded_legacy_signature: bool = False,
    forwarded_headers: Mapping[str, str] | None = None,
    forwarded_downstream_turn_state: str | None = None,
    forwarded_affinity_kind: str | None = None,
    forwarded_affinity_key: str | None = None,
    forwarded_file_owner_account_id: str | None = None,
    forwarded_client_ip: str | None = None,
    enforce_openai_sdk_contract: bool = True,
    native_codex_heartbeat: bool = False,
    api_key_policy_already_applied: bool = False,
    prohibit_fast_mode: bool = False,
) -> Response:
    # Owner-forwarded payloads have already passed API-key enforcement,
    # account-catalog fallback, reservation, and signing on the origin
    # instance. Re-validate the key's other policy here, but retain the
    # signed effective tier: an owner with an older/staler model snapshot must
    # not re-add a tier that the origin authoritatively removed.
    forwarded_effective_service_tier = payload.service_tier if forwarded_request else None
    service_tier_was_enforced = False
    if not api_key_policy_already_applied:
        service_tier_was_enforced = apply_api_key_enforcement(
            payload,
            api_key,
            prohibit_fast_mode=prohibit_fast_mode,
        ).service_tier_was_enforced
    if forwarded_request:
        payload.service_tier = forwarded_effective_service_tier
    else:
        apply_enforced_service_tier_model_fallback(
            payload,
            service_tier_was_enforced=service_tier_was_enforced,
        )
    validate_model_access(api_key, payload.model)
    compact_payload: ResponsesCompactRequest | None = None
    if codex_session_affinity:
        try:
            compact_trigger_input = strip_terminal_compaction_trigger_input(payload)
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
                # The main /responses route trims the terminal trigger before
                # compaction so the compact budget and image elision see only
                # the history to summarize. The upstream /compact contract
                # still requires exactly one terminal trigger on the wire.
                compact_payload_data["input"] = [
                    *compact_trigger_input,
                    {"type": "compaction_trigger"},
                ]
                if payload.previous_response_id is not None:
                    compact_payload_data["previous_response_id"] = payload.previous_response_id
                if payload.conversation is not None:
                    compact_payload_data["conversation"] = payload.conversation
                compact_payload = ResponsesCompactRequest.model_validate(compact_payload_data)
                # Validate the exact compact wire payload before admission or
                # reservation work so an untrimmable trigger is a client 400,
                # not a late service exception that reaches the global 500.
                compact_payload.to_payload()
        except ClientPayloadError as exc:
            error = openai_client_payload_error(exc)
            return _logged_error_json_response(request, 400, error)
    admission_denial = await _opportunistic_admission_denial(request, context, api_key, model=payload.model)
    if admission_denial is not None:
        return admission_denial
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
    reservation_cleanup = _ResponsesReservationCleanup(
        owns_reservation=owns_reservation,
        reservation=reservation,
        scheduler=_responses_cleanup_scheduler(context.service),
        request_id=ensure_request_id(),
    )
    responses_service_cleanup_ready_event = asyncio.Event()
    responses_owner_forward_dispatched_event = asyncio.Event()
    responses_owner_forward_rejected_event = asyncio.Event()

    rate_limit_headers = (
        await _rate_limit_headers_with_reservation_cleanup(
            context,
            api_key,
            reservation if owns_reservation else None,
            reservation_cleanup=reservation_cleanup if owns_reservation else None,
        )
        if include_rate_limit_headers
        else {}
    )
    bridge_active = prefer_http_bridge and proxy_service_module.get_settings().http_responses_session_bridge_enabled
    effective_headers = forwarded_headers or request.headers
    bridge_recovery_eligible = _http_bridge_recovery_request_eligible(
        payload,
        bridge_active=bridge_active,
        headers=effective_headers,
    )
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
    if compact_payload is not None:
        responses_cleanup_ready_token = _bind_propagated_responses_service_cleanup_ready(
            responses_service_cleanup_ready_event
        )
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
                    forwarded_request=forwarded_request,
                    forwarded_file_owner_account_id=forwarded_file_owner_account_id,
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
                if forwarded_request and responses_service_cleanup_ready_event.is_set():
                    # Fallback settlement already transferred cleanup. A 502
                    # would look like a definitive rejection and let origin
                    # replay a compact that already ran.
                    envelope = _parse_error_envelope(exc.payload)
                    error = envelope.error
                    stream = _synthetic_compaction_failure_stream(
                        response_id=get_request_id() or "unknown",
                        error_code=(error.code if error is not None and error.code else "upstream_error"),
                        error_message=(
                            error.message
                            if error is not None and error.message
                            else "Compact request failed after settlement"
                        ),
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
                return _stream_startup_error_response(
                    request,
                    exc,
                    headers=rate_limit_headers,
                )
            compact_item = _compact_response_output_item(compact_result)
            if compact_item is None:
                if forwarded_request and responses_service_cleanup_ready_event.is_set():
                    stream = _synthetic_compaction_failure_stream(response_id=_compact_response_id(compact_result))
                else:
                    error = openai_error(
                        "upstream_error",
                        "Compact response did not include a compaction output item",
                        error_type="server_error",
                    )
                    return _logged_error_json_response(request, 502, error, headers=rate_limit_headers)
            else:
                stream = _synthetic_compaction_response_stream(
                    compact_item,
                    response_id=_compact_response_id(compact_result),
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
            _reset_propagated_responses_service_cleanup_ready(responses_cleanup_ready_token)
            if _responses_origin_may_release_reservation(
                service_cleanup_ready_event=responses_service_cleanup_ready_event
            ):
                await reservation_cleanup.release(action="terminal compaction response")
    capacity_wait_event = asyncio.Event()
    capacity_ready_event = _CapacityStartupReadyEvent()
    payload.stream = True

    def build_response_stream() -> AsyncIterator[str]:
        if prefer_http_bridge:
            return context.service.stream_http_responses(
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
                forwarded_original_request_unanchored=forwarded_original_request_unanchored,
                forwarded_legacy_signature=forwarded_legacy_signature,
                forwarded_affinity_kind=forwarded_affinity_kind,
                forwarded_affinity_key=forwarded_affinity_key,
                forwarded_file_owner_account_id=forwarded_file_owner_account_id,
                client_ip=client_ip,
                enforce_openai_sdk_contract=enforce_openai_sdk_contract,
                capacity_startup_wait_event=capacity_wait_event,
                capacity_startup_ready_event=capacity_ready_event,
            )
        return context.service.stream_responses(
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

    def build_recovery_response_stream() -> AsyncIterator[str]:
        """Build a server-owned retry with a fresh API-key reservation.

        The first bridge generator owns and settles the admission reservation
        when it terminates.  Indefinite recovery must not reuse that object:
        each retry gets a new reservation and therefore remains accounted and
        bounded even when the client connection stays open for a long time.
        """

        async def _retry() -> AsyncIterator[str]:
            retry_reservation = reservation
            if prefer_http_bridge and api_key is not None and reservation is not None:
                retry_service_tier = dict(payload.to_payload()).get("service_tier")
                retry_reservation = await _enforce_request_limits(
                    api_key,
                    request_model=payload.model,
                    request_service_tier=(retry_service_tier if isinstance(retry_service_tier, str) else None),
                    request_usage_budget=estimate_api_key_request_usage(payload),
                )
            retry_stream = context.service.stream_http_responses(
                payload,
                effective_headers,
                codex_session_affinity=codex_session_affinity,
                propagate_http_errors=True,
                openai_cache_affinity=openai_cache_affinity,
                api_key=api_key,
                api_key_reservation=retry_reservation,
                suppress_text_done_events=suppress_text_done_events,
                downstream_turn_state=downstream_turn_state,
                forwarded_request=forwarded_request,
                forwarded_original_request_unanchored=forwarded_original_request_unanchored,
                forwarded_legacy_signature=forwarded_legacy_signature,
                forwarded_affinity_kind=forwarded_affinity_kind,
                forwarded_affinity_key=forwarded_affinity_key,
                forwarded_file_owner_account_id=forwarded_file_owner_account_id,
                client_ip=client_ip,
                enforce_openai_sdk_contract=enforce_openai_sdk_contract,
                capacity_startup_wait_event=capacity_wait_event,
                capacity_startup_ready_event=capacity_ready_event,
            )
            async for line in retry_stream:
                yield line

        return _retry()

    stream = build_response_stream()
    startup_handoff_tasks: list[asyncio.Task[str]] = []
    capacity_wait_token = _bind_propagated_capacity_startup_wait(capacity_wait_event)
    capacity_ready_token = _bind_propagated_capacity_startup_ready(capacity_ready_event)
    responses_owner_forward_dispatched_token = _bind_propagated_responses_owner_forward_dispatched(
        responses_owner_forward_dispatched_event
    )
    responses_owner_forward_rejected_token = _bind_propagated_responses_owner_forward_rejected(
        responses_owner_forward_rejected_event
    )
    responses_cleanup_ready_token = _bind_propagated_responses_service_cleanup_ready(
        responses_service_cleanup_ready_event
    )
    try:
        try:
            stream, startup_error = await _probe_stream_startup_error(
                stream,
                convert_event_errors=bridge_active and enforce_openai_sdk_contract,
                timeout_seconds=(
                    _HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS
                    if prefer_http_bridge
                    else _STREAM_STARTUP_ERROR_PROBE_SECONDS
                ),
                capacity_wait_event=capacity_wait_event,
                capacity_ready_event=capacity_ready_event,
                handoff_task_sink=startup_handoff_tasks,
                service_cleanup_ready_event=(
                    responses_service_cleanup_ready_event if forwarded_request and reservation is not None else None
                ),
            )
        finally:
            _reset_propagated_responses_service_cleanup_ready(responses_cleanup_ready_token)
            _reset_propagated_responses_owner_forward_rejected(responses_owner_forward_rejected_token)
            _reset_propagated_responses_owner_forward_dispatched(responses_owner_forward_dispatched_token)
            _reset_propagated_capacity_startup_ready(capacity_ready_token)
            _reset_propagated_capacity_startup_wait(capacity_wait_token)
    except BaseException:
        if _responses_origin_may_release_reservation(
            service_cleanup_ready_event=responses_service_cleanup_ready_event,
            owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
            owner_forward_rejected_event=responses_owner_forward_rejected_event,
        ):
            await reservation_cleanup.release(action="responses startup")
        raise
    if startup_error is not None:
        startup_error_code = (
            _startup_error_details(startup_error)[0] if isinstance(startup_error, ProxyResponseError) else None
        )
        startup_recovery_allowed = (
            isinstance(startup_error, ProxyResponseError)
            and bridge_recovery_eligible
            and get_settings().http_responses_session_bridge_ambiguous_continuation_recovery_mode
            == "server_indefinite_recovery"
            and getattr(startup_error, "http_bridge_durable_recovery_eligible", False)
            and startup_error_code
            in {"stream_incomplete", "stream_idle_timeout", "upstream_request_timeout", "upstream_unavailable"}
            and _responses_origin_may_release_reservation(
                service_cleanup_ready_event=responses_service_cleanup_ready_event,
                owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
                owner_forward_rejected_event=responses_owner_forward_rejected_event,
            )
        )
        if startup_recovery_allowed:
            assert isinstance(startup_error, ProxyResponseError)

            # A durable bridge can fail before the startup probe observes the
            # first response.created event. Feed that error through the same
            # server-owned recovery loop used for failures after the probe;
            # returning JSON here would hand a recoverable disconnect back to
            # the client before recovery is even installed.
            async def _raise_startup_error() -> AsyncIterator[str]:
                raise startup_error
                yield ""  # pragma: no cover

            stream = _raise_startup_error()
        else:
            if _responses_origin_may_release_reservation(
                service_cleanup_ready_event=responses_service_cleanup_ready_event,
                owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
                owner_forward_rejected_event=responses_owner_forward_rejected_event,
            ):
                await reservation_cleanup.release(action="responses startup error")
            return _stream_startup_error_response(
                request,
                startup_error,
                headers=rate_limit_headers,
                allow_client_full_history_once=bridge_recovery_eligible,
            )
    # Server-indefinite recovery is only safe for an explicitly anchored
    # continuation. Fresh first-turn requests have no durable parent
    # operation to fence, so do not install the recovery loop for them.
    recovery_stream_factory = (
        build_recovery_response_stream
        if bridge_recovery_eligible
        and _responses_origin_may_release_reservation(
            service_cleanup_ready_event=responses_service_cleanup_ready_event,
            owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
            owner_forward_rejected_event=responses_owner_forward_rejected_event,
        )
        else None
    )
    stream = _normalize_public_responses_stream(
        _stream_response_error_events(
            stream,
            owns_reservation=owns_reservation,
            reservation=reservation,
            reservation_cleanup=reservation_cleanup,
            responses_service_cleanup_ready_event=responses_service_cleanup_ready_event,
            responses_owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
            responses_owner_forward_rejected_event=responses_owner_forward_rejected_event,
            recovery_stream_factory=recovery_stream_factory,
            allow_client_full_history_once=bridge_recovery_eligible,
            require_durable_recovery_fence=bridge_recovery_eligible,
        ),
        enforce_openai_sdk_contract=enforce_openai_sdk_contract,
    )
    service_stream = stream
    use_codex_keepalive = native_codex_heartbeat or not enforce_openai_sdk_contract
    keepalive_frame = CODEX_KEEPALIVE_FRAME if use_codex_keepalive else SSE_KEEPALIVE_FRAME
    if use_codex_keepalive:
        stream = _prepend_initial_sse_heartbeat(
            stream,
            keepalive_frame,
            request_id=get_request_id(),
            route_family="responses",
        )
    stream = inject_sse_keepalives(
        stream,
        get_settings().sse_keepalive_interval_seconds,
        keepalive_frame=keepalive_frame,
        on_keepalive=lambda: _record_stream_keepalive("responses"),
    )
    # Outermost so a client close after the initial heartbeat still closes
    # the service stream, including when the startup probe already completed.
    stream = _guard_responses_startup_handoff(
        stream,
        startup_task=startup_handoff_tasks[0] if startup_handoff_tasks else None,
        streams_to_close=(service_stream,),
        reservation_cleanup=reservation_cleanup,
        responses_service_cleanup_ready_event=responses_service_cleanup_ready_event,
        responses_owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
        responses_owner_forward_rejected_event=responses_owner_forward_rejected_event,
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
    api_key_policy_already_applied: bool = False,
    prohibit_fast_mode: bool = False,
) -> Response:
    service_tier_was_enforced = False
    if not api_key_policy_already_applied:
        service_tier_was_enforced = apply_api_key_enforcement(
            payload,
            api_key,
            prohibit_fast_mode=prohibit_fast_mode,
        ).service_tier_was_enforced
    apply_enforced_service_tier_model_fallback(
        payload,
        service_tier_was_enforced=service_tier_was_enforced,
    )
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
    reservation_cleanup = _ResponsesReservationCleanup(
        owns_reservation=True,
        reservation=reservation,
        scheduler=_responses_cleanup_scheduler(context.service),
        request_id=ensure_request_id(),
    )
    responses_service_cleanup_ready_event = asyncio.Event()
    responses_owner_forward_dispatched_event = asyncio.Event()
    responses_owner_forward_rejected_event = asyncio.Event()

    rate_limit_headers = await _rate_limit_headers_with_reservation_cleanup(
        context,
        api_key,
        reservation,
        reservation_cleanup=reservation_cleanup,
    )
    bridge_active = prefer_http_bridge and proxy_service_module.get_settings().http_responses_session_bridge_enabled
    bridge_recovery_eligible = _http_bridge_recovery_request_eligible(
        payload,
        bridge_active=bridge_active,
        headers=request.headers,
    )
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
    captured_turn_state_headers: dict[str, str] = {}
    responses_owner_forward_dispatched_token = _bind_propagated_responses_owner_forward_dispatched(
        responses_owner_forward_dispatched_event
    )
    responses_owner_forward_rejected_token = _bind_propagated_responses_owner_forward_rejected(
        responses_owner_forward_rejected_event
    )
    responses_cleanup_ready_token = _bind_propagated_responses_service_cleanup_ready(
        responses_service_cleanup_ready_event
    )
    try:
        response_payload = await _collect_responses_payload(
            stream,
            captured_turn_state_headers=captured_turn_state_headers,
        )
    except asyncio.CancelledError:
        if _responses_origin_may_release_reservation(
            service_cleanup_ready_event=responses_service_cleanup_ready_event,
            owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
            owner_forward_rejected_event=responses_owner_forward_rejected_event,
        ):
            await reservation_cleanup.release(action="responses collection cancellation")
        raise
    except ProxyResponseError as exc:
        if _responses_origin_may_release_reservation(
            service_cleanup_ready_event=responses_service_cleanup_ready_event,
            owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
            owner_forward_rejected_event=responses_owner_forward_rejected_event,
        ):
            await reservation_cleanup.release(action="responses collection error")
        error = _parse_error_envelope(exc.payload)
        status_code, error = _mask_previous_response_not_found_error(
            error,
            default_status=exc.status_code,
            allow_client_full_history_once=(
                bridge_recovery_eligible and getattr(exc, "http_bridge_durable_recovery_eligible", False)
            ),
        )
        return _logged_error_json_response(
            request,
            status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers={**captured_turn_state_headers, **rate_limit_headers},
        )
    except BaseException:
        if _responses_origin_may_release_reservation(
            service_cleanup_ready_event=responses_service_cleanup_ready_event,
            owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
            owner_forward_rejected_event=responses_owner_forward_rejected_event,
        ):
            await reservation_cleanup.release(action="responses collection")
        raise
    finally:
        _reset_propagated_responses_service_cleanup_ready(responses_cleanup_ready_token)
        _reset_propagated_responses_owner_forward_rejected(responses_owner_forward_rejected_token)
        _reset_propagated_responses_owner_forward_dispatched(responses_owner_forward_dispatched_token)
    if isinstance(response_payload, OpenAIResponsePayload):
        if response_payload.status == "failed":
            error_payload = _error_envelope_from_response(response_payload.error)
            status_code, error_payload = _mask_previous_response_not_found_error(
                error_payload,
                allow_client_full_history_once=False,
            )
            return _logged_error_json_response(
                request,
                status_code,
                error_payload.model_dump(mode="json", exclude_none=True),
                headers={**turn_state_headers, **captured_turn_state_headers, **rate_limit_headers},
            )
        return JSONResponse(
            content=response_payload.model_dump(mode="json", exclude_none=True),
            headers={**turn_state_headers, **captured_turn_state_headers, **rate_limit_headers},
        )
    status_code, response_payload = _mask_previous_response_not_found_error(
        response_payload,
        allow_client_full_history_once=False,
    )
    return _logged_error_json_response(
        request,
        status_code,
        response_payload.model_dump(mode="json", exclude_none=True),
        headers={**turn_state_headers, **captured_turn_state_headers, **rate_limit_headers},
    )


@router.post(
    "/responses/compact",
    response_model=CompactResponseResult,
)
async def responses_compact(
    request: Request,
    payload: ResponsesCompactRequest = Body(...),
    _raw_trigger_validation: None = Depends(_capture_raw_compaction_trigger_error),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
    raw_trigger_error = _raw_compaction_trigger_error(request)
    if raw_trigger_error is not None:
        return _logged_error_json_response(request, 400, openai_client_payload_error(raw_trigger_error))
    return await _compact_responses(
        request,
        payload,
        context,
        api_key,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        prohibit_fast_mode=await _prohibit_fast_mode_enabled(),
    )


@v1_router.post(
    "/responses/compact",
    response_model=CompactResponseResult,
)
async def v1_responses_compact(
    request: Request,
    payload: V1ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
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
        prohibit_fast_mode=await _prohibit_fast_mode_enabled(),
    )


async def _compact_responses(
    request: Request,
    payload: ResponsesCompactRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
    prohibit_fast_mode: bool = False,
) -> JSONResponse:
    # The replaced effort is discarded: this path is subscription-only, so the
    # rewrite that works around the backend hang must stick.
    service_tier_was_enforced = apply_api_key_enforcement(
        payload,
        api_key,
        prohibit_fast_mode=prohibit_fast_mode,
    ).service_tier_was_enforced
    apply_enforced_service_tier_model_fallback(
        payload,
        service_tier_was_enforced=service_tier_was_enforced,
    )
    validate_model_access(api_key, payload.model)
    try:
        request_usage_budget = estimate_api_key_request_usage(payload)
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return _logged_error_json_response(request, 400, error)
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
        request_usage_budget=request_usage_budget,
    )

    reservation_cleanup = _ResponsesReservationCleanup(
        owns_reservation=True,
        reservation=reservation,
        scheduler=_responses_cleanup_scheduler(context.service),
        request_id=ensure_request_id(),
    )
    responses_service_cleanup_ready_event = asyncio.Event()
    rate_limit_headers = await _rate_limit_headers_with_reservation_cleanup(
        context,
        api_key,
        reservation,
        reservation_cleanup=reservation_cleanup,
    )
    responses_cleanup_ready_token = _bind_propagated_responses_service_cleanup_ready(
        responses_service_cleanup_ready_event
    )
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
        _reset_propagated_responses_service_cleanup_ready(responses_cleanup_ready_token)
        if _responses_origin_may_release_reservation(service_cleanup_ready_event=responses_service_cleanup_ready_event):
            await reservation_cleanup.release(action="compact response")
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
            if isinstance(item_type, str) and item_type in {"compaction", "compaction_summary"}:
                normalized_item = _normalize_compaction_output_item(item)
                if normalized_item is not None:
                    return normalized_item
    summary = getattr(payload, "compaction_summary", None)
    if summary is None:
        summary = extra.get("compaction_summary")
    summary_mapping = _json_mapping_from_model_or_mapping(summary)
    if summary_mapping is not None:
        return _normalize_compaction_output_item(summary_mapping)
    return None


def _normalize_compaction_output_item(item: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    encrypted_content = item.get("encrypted_content")
    if not isinstance(encrypted_content, str):
        return None

    normalized: dict[str, JsonValue] = {
        "type": "compaction",
        "encrypted_content": encrypted_content,
    }
    item_id = normalize_compaction_item_id(item.get("id"))
    if item_id is not None:
        normalized["id"] = item_id
    status = item.get("status")
    if isinstance(status, str) and status.strip():
        normalized["status"] = status
    return normalized


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
    item = dict(compact_item)
    item.setdefault("status", "completed")
    completed_response: dict[str, JsonValue] = {
        "id": response_id,
        "object": "response",
        "status": "completed",
        "output": [item],
    }
    usage_mapping = _json_mapping_from_model_or_mapping(usage)
    if usage_mapping is not None:
        completed_response["usage"] = dict(usage_mapping)
    yield format_sse_event(
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {
                "id": response_id,
                "object": "response",
                "status": "in_progress",
                "output": [],
            },
        }
    )
    yield format_sse_event(
        {
            "type": "response.output_item.added",
            "sequence_number": 1,
            "output_index": 0,
            "item": {
                **item,
                "status": "in_progress",
            },
        }
    )
    yield format_sse_event(
        {
            "type": "response.output_item.done",
            "sequence_number": 2,
            "output_index": 0,
            "item": item,
        }
    )
    yield format_sse_event(
        {
            "type": "response.completed",
            "sequence_number": 3,
            "response": completed_response,
        }
    )
    yield "data: [DONE]\n\n"


async def _synthetic_compaction_failure_stream(
    *,
    response_id: str,
    error_code: str = "upstream_error",
    error_message: str = "Compact response did not include a compaction output item",
) -> AsyncIterator[str]:
    yield format_sse_event(
        response_failed_event(
            error_code,
            error_message,
            response_id=response_id,
        )
    )
    yield "data: [DONE]\n\n"


async def _transcribe_request(
    *,
    request: Request,
    multipart: _ParsedTranscriptionMultipart,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> JSONResponse:
    validate_model_access(api_key, _TRANSCRIPTION_MODEL)
    reservation = await _enforce_request_limits(
        api_key,
        request_model=_TRANSCRIPTION_MODEL,
        request_service_tier=None,
    )
    rate_limit_headers = await _rate_limit_headers_with_reservation_cleanup(context, api_key, reservation)
    try:
        result = await context.service.transcribe(
            audio_bytes=multipart.audio_bytes,
            filename=multipart.filename,
            content_type=multipart.content_type,
            prompt=multipart.prompt,
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
    api_key: ApiKeyData | None = Depends(validate_codex_provider_usage_identity),
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
    api_key: ApiKeyData | None = Depends(validate_codex_provider_usage_identity),
) -> ConsumeRateLimitResetCreditResponse | JSONResponse:
    capability_transport_denial = await _required_capability_http_transport_denial(request, api_key)
    if capability_transport_denial is not None:
        return capability_transport_denial
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


async def _wait_for_first_stream_probe(
    first_task: asyncio.Task[str],
    *,
    timeout_seconds: float,
    capacity_wait_event: asyncio.Event | None,
    capacity_ready_event: asyncio.Event | None = None,
) -> bool:
    try:
        done, _pending = await asyncio.wait({first_task}, timeout=timeout_seconds)
        if done:
            if capacity_wait_event is not None and capacity_wait_event.is_set():
                capacity_wait_event.clear()
            return True
        if capacity_wait_event is None:
            return False

        # Account selection can include a PostgreSQL round trip before it can
        # report either successful admission or local capacity pressure. Give
        # those paired signals the established bounded startup-probe window,
        # but keep one absolute deadline so unrelated/no-marker streams cannot
        # turn the route into a request-budget-length startup wait.
        signal_discovery_seconds = (
            _CAPACITY_STARTUP_SIGNAL_DISCOVERY_SECONDS
            if capacity_ready_event is not None
            else _CAPACITY_WAIT_MARKER_GRACE_SECONDS
        )
        signal_discovery_deadline = asyncio.get_running_loop().time() + signal_discovery_seconds
        while True:
            if first_task.done():
                if capacity_wait_event.is_set():
                    capacity_wait_event.clear()
                return True
            if capacity_wait_event.is_set():
                recovery_ready_task = (
                    asyncio.create_task(capacity_ready_event.wait()) if capacity_ready_event is not None else None
                )
                try:
                    recovery_waiters = {first_task}
                    if recovery_ready_task is not None:
                        recovery_waiters.add(recovery_ready_task)
                    recovery_done, _pending = await asyncio.wait(
                        recovery_waiters,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if first_task not in recovery_done:
                        # Re-read the paired level state. The ready signal has
                        # cleared the wait that recovered, but a newer wait may
                        # already have superseded that ready before this task
                        # resumes.
                        continue
                finally:
                    if recovery_ready_task is not None and not recovery_ready_task.done():
                        recovery_ready_task.cancel()
                    if recovery_ready_task is not None:
                        await asyncio.gather(recovery_ready_task, return_exceptions=True)
                continue
            if capacity_ready_event is not None and capacity_ready_event.is_set():
                # Admission recovery only proves that local capacity is ready;
                # the resumed upstream can still fail before its first item.
                # Preserve the route's normal bounded startup-error window so
                # an immediate 4xx / response.failed remains an HTTP startup
                # error, while a slow healthy upstream is still handed off.
                post_ready_timeout = timeout_seconds
                if isinstance(capacity_ready_event, _CapacityStartupReadyEvent):
                    ready_set_at = capacity_ready_event.set_at
                    if ready_set_at is not None:
                        post_ready_timeout = max(0.0, timeout_seconds - (time.monotonic() - ready_set_at))
                if post_ready_timeout <= 0:
                    return False
                post_ready_done, _pending = await asyncio.wait(
                    {first_task},
                    timeout=post_ready_timeout,
                )
                return bool(post_ready_done)

            marker_task = asyncio.create_task(capacity_wait_event.wait())
            ready_task = asyncio.create_task(capacity_ready_event.wait()) if capacity_ready_event is not None else None
            try:
                signal_discovery_remaining = max(
                    0.0,
                    signal_discovery_deadline - asyncio.get_running_loop().time(),
                )
                if signal_discovery_remaining <= 0:
                    return False
                signal_waiters = {first_task, marker_task}
                if ready_task is not None:
                    signal_waiters.add(ready_task)
                signal_done, _pending = await asyncio.wait(
                    signal_waiters,
                    timeout=signal_discovery_remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not signal_done:
                    return False
            finally:
                pending_signal_tasks = [
                    task for task in (marker_task, ready_task) if task is not None and not task.done()
                ]
                for task in pending_signal_tasks:
                    task.cancel()
                await asyncio.gather(
                    marker_task,
                    *(task for task in (ready_task,) if task is not None),
                    return_exceptions=True,
                )
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            first_task.cancel()
            await asyncio.gather(first_task, return_exceptions=True)
        raise


async def _probe_stream_startup_error(
    stream: AsyncIterator[str],
    *,
    convert_event_errors: bool = False,
    timeout_seconds: float | None = None,
    capacity_wait_event: asyncio.Event | None = None,
    capacity_ready_event: asyncio.Event | None = None,
    handoff_task_sink: list[asyncio.Task[str]] | None = None,
    service_cleanup_ready_event: asyncio.Event | None = None,
) -> tuple[AsyncIterator[str], ProxyResponseError | OpenAIErrorEnvelopeModel | None]:
    if timeout_seconds is None:
        timeout_seconds = _STREAM_STARTUP_ERROR_PROBE_SECONDS
    first_task = _create_first_stream_probe_task(stream)
    probe_done = await _wait_for_first_stream_probe(
        first_task,
        timeout_seconds=timeout_seconds,
        capacity_wait_event=capacity_wait_event,
        capacity_ready_event=capacity_ready_event,
    )
    if service_cleanup_ready_event is not None:
        buffered_before_cleanup_ready: list[str] = []
        handoff_deadline = asyncio.get_running_loop().time() + timeout_seconds
        while not service_cleanup_ready_event.is_set():
            remaining = handoff_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                with anyio.CancelScope(shield=True):
                    if not first_task.done():
                        first_task.cancel()
                        await asyncio.gather(first_task, return_exceptions=True)
                return (
                    _prepend_first(None, stream),
                    ProxyResponseError(
                        503,
                        openai_error(
                            "upstream_unavailable",
                            "Reservation cleanup handoff timed out",
                            error_type="server_error",
                        ),
                        failure_phase="reservation_cleanup_handoff",
                        failure_detail="cleanup_handoff_timeout",
                    ),
                )
            if not first_task.done():
                cleanup_ready_task = asyncio.create_task(service_cleanup_ready_event.wait())
                try:
                    await asyncio.wait(
                        {first_task, cleanup_ready_task},
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    with anyio.CancelScope(shield=True):
                        first_task.cancel()
                        cleanup_ready_task.cancel()
                        await asyncio.gather(first_task, cleanup_ready_task, return_exceptions=True)
                    raise
                finally:
                    if not cleanup_ready_task.done():
                        cleanup_ready_task.cancel()
                    await asyncio.gather(cleanup_ready_task, return_exceptions=True)
            if service_cleanup_ready_event.is_set():
                break
            if not first_task.done():
                continue
            try:
                first = first_task.result()
            except StopAsyncIteration:
                return (
                    _prepend_first(None, stream),
                    ProxyResponseError(
                        502,
                        openai_error(
                            "stream_incomplete",
                            "Upstream stream ended before reservation cleanup handoff",
                            error_type="server_error",
                        ),
                    ),
                )
            except ProxyResponseError as exc:
                return _prepend_first(None, stream), exc
            if convert_event_errors:
                first_error = _stream_event_error_envelope(first)
                if first_error is not None:
                    aclose = getattr(stream, "aclose", None)
                    if callable(aclose):
                        await aclose()
                    return _prepend_first(None, stream), first_error
            buffered_before_cleanup_ready.append(first)
            first_task = _create_first_stream_probe_task(stream)

        if handoff_task_sink is not None:
            handoff_task_sink.append(first_task)
        return (
            _prepend_items(
                buffered_before_cleanup_ready,
                _prepend_first_task(first_task, stream),
            ),
            None,
        )
    if not probe_done:
        # Probe window elapsed before the first item arrived. Hand the still-
        # running task off to be consumed by the streamed response. asyncio.wait
        # (rather than wait_for + shield) never cancels the task on timeout,
        # avoiding the Python 3.14 "exception in shielded future" log when the
        # upstream later returns an error such as a 429 from the admission gate.
        if handoff_task_sink is not None:
            handoff_task_sink.append(first_task)
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
    capacity_wait_event: asyncio.Event | None = None,
    capacity_ready_event: asyncio.Event | None = None,
) -> tuple[AsyncIterator[str], ProxyResponseError | OpenAIErrorEnvelopeModel | None]:
    buffered: list[str] = []
    for _ in range(max_startup_events):
        first_task = _create_first_stream_probe_task(stream)
        probe_done = await _wait_for_first_stream_probe(
            first_task,
            timeout_seconds=timeout_seconds,
            capacity_wait_event=capacity_wait_event,
            capacity_ready_event=capacity_ready_event,
        )
        if not probe_done:
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
    try:
        yield keepalive_frame
        async for line in stream:
            yield line
    finally:
        await _close_responses_stream_best_effort(stream, action="initial heartbeat")


def _record_stream_keepalive(surface: str) -> None:
    if PROMETHEUS_AVAILABLE and stream_keepalive_sent_total is not None:
        stream_keepalive_sent_total.labels(surface=surface).inc()


async def _guard_responses_startup_handoff(
    stream: AsyncIterator[str],
    *,
    startup_task: asyncio.Task[str] | None,
    streams_to_close: tuple[AsyncIterator[str], ...],
    reservation_cleanup: _ResponsesReservationCleanup,
    responses_service_cleanup_ready_event: asyncio.Event,
    responses_owner_forward_dispatched_event: asyncio.Event,
    responses_owner_forward_rejected_event: asyncio.Event,
) -> AsyncIterator[str]:
    try:
        async for line in stream:
            yield line
    finally:
        with anyio.CancelScope(shield=True):
            release_candidate = startup_task is None
            if startup_task is not None:
                if startup_task.done():
                    release_candidate = startup_task.cancelled() or startup_task.exception() is not None
                else:
                    release_candidate = True
                    startup_task.cancel()
                    await asyncio.gather(startup_task, return_exceptions=True)
            closed_stream_ids: set[int] = set()
            for stream_index, stream_to_close in enumerate(reversed(streams_to_close)):
                stream_id = id(stream_to_close)
                if stream_id in closed_stream_ids:
                    continue
                closed_stream_ids.add(stream_id)
                await _close_responses_stream_best_effort(
                    stream_to_close,
                    action=f"startup wrapper {stream_index}",
                )
            if release_candidate and _responses_origin_may_release_reservation(
                service_cleanup_ready_event=responses_service_cleanup_ready_event,
                owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
                owner_forward_rejected_event=responses_owner_forward_rejected_event,
            ):
                await reservation_cleanup.release(action="responses startup handoff")


async def _close_responses_stream_best_effort(
    stream: AsyncIterator[str],
    *,
    action: str,
) -> None:
    aclose = getattr(stream, "aclose", None)
    if not callable(aclose):
        return
    try:
        await aclose()
    except asyncio.CancelledError:
        logger.debug("Responses %s stream close was cancelled", action)
    except Exception:
        logger.warning("Failed to close Responses %s stream", action, exc_info=True)


async def _stream_proxy_errors_as_response_failed(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    async for line in _stream_response_error_events(stream, owns_reservation=False, reservation=None):
        yield line


async def _stream_response_error_events(
    stream: AsyncIterator[str],
    *,
    owns_reservation: bool,
    reservation: ApiKeyUsageReservationData | None,
    reservation_cleanup: _ResponsesReservationCleanup | None = None,
    responses_service_cleanup_ready_event: asyncio.Event | None = None,
    responses_owner_forward_dispatched_event: asyncio.Event | None = None,
    responses_owner_forward_rejected_event: asyncio.Event | None = None,
    recovery_stream_factory: Callable[[], AsyncIterator[str]] | None = None,
    allow_client_full_history_once: bool = False,
    require_durable_recovery_fence: bool = False,
) -> AsyncIterator[str]:
    cleanup = reservation_cleanup or _ResponsesReservationCleanup(
        owns_reservation=owns_reservation,
        reservation=reservation,
        scheduler=None,
        request_id=ensure_request_id(),
    )

    async def release_owned_reservation() -> None:
        if responses_service_cleanup_ready_event is not None and not _responses_origin_may_release_reservation(
            service_cleanup_ready_event=responses_service_cleanup_ready_event,
            owner_forward_dispatched_event=responses_owner_forward_dispatched_event,
            owner_forward_rejected_event=responses_owner_forward_rejected_event,
        ):
            return
        await cleanup.release(action="responses stream cleanup")

    saw_downstream_event = False
    try:
        async for line in stream:
            if line.startswith("data:") or line.startswith("event:"):
                saw_downstream_event = True
            yield line
    except ProxyResponseError as exc:
        error_code = exc.payload.get("error", {}).get("code") if isinstance(exc.payload, dict) else None
        indefinite_recovery = (
            get_settings().http_responses_session_bridge_ambiguous_continuation_recovery_mode
            == "server_indefinite_recovery"
        )
        if (
            recovery_stream_factory is not None
            and indefinite_recovery
            and (not require_durable_recovery_fence or getattr(exc, "http_bridge_durable_recovery_eligible", False))
            and not saw_downstream_event
            and error_code
            in {"stream_incomplete", "stream_idle_timeout", "upstream_request_timeout", "upstream_unavailable"}
        ):
            # Keep the client stream alive while the server owns recovery.
            # The operation remains serialized by the durable operation
            # fingerprint; each new upstream attempt is still at-least-once.
            retry_delay = max(1.0, min(30.0, float(exc.retry_after_seconds or 5.0)))
            while True:
                yield ": codex-lb recovery in progress\n\n"
                await asyncio.sleep(retry_delay)
                try:
                    retry_stream = recovery_stream_factory()
                    retry_saw_downstream_event = False
                    async for line in retry_stream:
                        if line.startswith("data:") or line.startswith("event:"):
                            retry_saw_downstream_event = True
                            saw_downstream_event = True
                        yield line
                    return
                except ProxyResponseError as retry_exc:
                    retry_code = (
                        retry_exc.payload.get("error", {}).get("code") if isinstance(retry_exc.payload, dict) else None
                    )
                    if (
                        retry_code
                        not in {
                            "stream_incomplete",
                            "stream_idle_timeout",
                            "upstream_request_timeout",
                            "upstream_unavailable",
                        }
                        or retry_saw_downstream_event
                        or (
                            require_durable_recovery_fence
                            and not getattr(retry_exc, "http_bridge_durable_recovery_eligible", False)
                        )
                    ):
                        exc = retry_exc
                        break
                    retry_delay = max(1.0, min(30.0, float(retry_exc.retry_after_seconds or retry_delay)))
                except (ProxyRateLimitError, ProxyAuthError) as retry_limit_exc:
                    # A quota revocation or limit can happen between recovery
                    # attempts. Convert it into the same terminal SSE shape
                    # as other proxy failures instead of aborting an already
                    # started response stream without a response.failed event.
                    exc = ProxyResponseError(
                        retry_limit_exc.status_code,
                        openai_error(
                            retry_limit_exc.code,
                            retry_limit_exc.message,
                            error_type=getattr(retry_limit_exc, "error_type", "server_error"),
                        ),
                    )
                    break
                except Exception:
                    # Recovery admission can also fail before a replacement
                    # stream is created (for example, a transient database
                    # failure while reserving usage). Do not let that
                    # unexpected exception truncate an already-started SSE
                    # response; the outer cleanup still settles the original
                    # reservation and emits one terminal response.failed event.
                    logger.warning("HTTP bridge recovery admission failed", exc_info=True)
                    exc = ProxyResponseError(
                        503,
                        openai_error(
                            "bridge_recovery_admission_failed",
                            "Recovery admission failed; retry shortly.",
                            error_type="server_error",
                        ),
                        retry_after_seconds=5,
                    )
                    break
        await release_owned_reservation()
        envelope = _parse_error_envelope(exc.payload)
        _, envelope = _mask_previous_response_not_found_error(
            envelope,
            default_status=exc.status_code,
            allow_client_full_history_once=(
                allow_client_full_history_once and getattr(exc, "http_bridge_durable_recovery_eligible", False)
            ),
        )
        error = envelope.error
        retry_hint = ""
        if exc.retry_after_seconds is not None and exc.retry_after_seconds > 0:
            # Preserve the HTTP Retry-After signal when a streaming response
            # has already started and the exception must be represented as an
            # SSE event.  The SSE retry field is milliseconds, while the
            # exception stores seconds.  Clients that do not implement the
            # directive safely ignore the extra comment line.
            retry_hint = f"retry: {max(1, math.ceil(exc.retry_after_seconds * 1000))}\n"
        yield retry_hint + format_sse_event(
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
    allow_client_full_history_once: bool = False,
) -> JSONResponse:
    if isinstance(error, ProxyResponseError):
        envelope = _parse_error_envelope(error.payload)
        status_code, envelope = _mask_previous_response_not_found_error(
            envelope,
            default_status=error.status_code,
            allow_client_full_history_once=(
                allow_client_full_history_once and getattr(error, "http_bridge_durable_recovery_eligible", False)
            ),
        )
        startup_headers = dict(headers)
        if error.retry_after_seconds is not None and error.retry_after_seconds > 0:
            startup_headers.setdefault("Retry-After", str(error.retry_after_seconds))
        return _logged_error_json_response(
            request,
            status_code,
            envelope.model_dump(mode="json", exclude_none=True),
            headers=startup_headers,
        )
    status_code, envelope = _mask_previous_response_not_found_error(
        error,
        allow_client_full_history_once=False,
    )
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


def _required_capability_values(headers: Mapping[str, str]) -> tuple[str, ...]:
    if isinstance(headers, Headers):
        return tuple(headers.getlist(CODEX_LB_REQUIRED_CAPABILITY_HEADER))
    normalized_name = CODEX_LB_REQUIRED_CAPABILITY_HEADER.lower()
    return tuple(value for name, value in headers.items() if name.lower() == normalized_name)


async def _validate_proxy_websocket_request(
    websocket: WebSocket,
    *,
    allow_required_capability: bool = False,
    require_api_key: bool = False,
) -> tuple[ApiKeyData | None, JSONResponse | None]:
    denial = await _websocket_firewall_denial_response(websocket)
    if denial is not None:
        return None, denial
    capability_header_values = _required_capability_values(websocket.headers)
    try:
        if require_api_key or capability_header_values:
            api_key = await validate_required_proxy_api_key_authorization(websocket.headers.get("authorization"))
        else:
            api_key = await _validate_proxy_api_key_authorization_for_connection(
                websocket.headers.get("authorization"),
                websocket,
            )
    except ProxyAuthError as exc:
        return None, JSONResponse(
            status_code=exc.status_code,
            content=openai_error(exc.code, exc.message, error_type=exc.error_type),
        )
    if capability_header_values and not allow_required_capability:
        return api_key, JSONResponse(
            status_code=400,
            content=openai_error(
                "required_capability_transport_unsupported",
                "Required capability routing is only supported over the Responses WebSocket transport.",
                error_type="invalid_request_error",
            ),
        )
    return api_key, None


async def _required_capability_http_transport_denial(
    request: Request,
    api_key: ApiKeyData | None,
) -> JSONResponse | None:
    """Authenticate capability intent and reject unsupported HTTP routing."""

    if not _required_capability_values(request.headers):
        return None
    if api_key is None:
        await validate_required_proxy_api_key_authorization(request.headers.get("authorization"))
    return _logged_error_json_response(
        request,
        400,
        openai_error(
            "required_capability_transport_unsupported",
            "Required capability routing is only supported over the Responses WebSocket transport.",
            error_type="invalid_request_error",
        ),
    )


def _redact_realtime_live_websocket_scope(websocket: WebSocket, *, path: str) -> None:
    """Remove opaque live identifiers before Uvicorn emits handshake logs."""

    websocket.scope["path"] = path
    websocket.scope["raw_path"] = path.replace("<", "%3C").replace(">", "%3E").encode("ascii")
    websocket.scope["query_string"] = b""


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
        trusted_proxy_networks=parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs),
        allowed_proxy_header_names=FORWARDED_CHAIN_HEADER_NAMES,
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
    if selection.error_code == USAGE_LIMIT_REACHED:
        return _logged_error_json_response(
            request,
            429,
            openai_error(
                USAGE_LIMIT_REACHED,
                selection.error_message or "Usage limit reached",
                error_type=USAGE_LIMIT_REACHED,
                resets_at=selection.resets_at,
            ),
        )
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


async def _release_reservation_best_effort(
    reservation: ApiKeyUsageReservationData | None,
    *,
    action: str,
    scheduler: _ResponsesCleanupScheduler | None,
    request_id: str,
) -> None:
    if reservation is None:
        return
    try:
        await _release_reservation_deferring_cancellation(reservation)
    except Exception:
        logger.warning("Failed to release API key reservation during %s", action, exc_info=True)
        if scheduler is None:
            return
        scheduler._schedule_cancel_safe_cleanup(
            _release_reservation_deferring_cancellation(reservation),
            action=f"{action.replace(' ', '_')}_retry",
            request_id=request_id,
        )


async def _finalize_image_reservation(
    service: proxy_service_module.ProxyService,
    api_key: ApiKeyData | None,
    reservation: ApiKeyUsageReservationData | None,
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None = None,
) -> None:
    """Transfer image-token settlement to tracked persistence ownership."""
    if reservation is None:
        return
    await service.settle_image_api_key_usage(
        api_key,
        reservation,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        request_id=get_request_id() or reservation.reservation_id,
    )


async def _settle_source_reservation(
    reservation: ApiKeyUsageReservationData | None,
    *,
    source: ModelSource,
    model: str,
    usage: SourceUsage | None,
    cost_usd_override: float | None = None,
) -> bool:
    if reservation is None:
        return True
    try:
        if usage is None:
            await _release_reservation(reservation)
            return True
        cost_usd = cost_usd_override if cost_usd_override is not None else _source_usage_cost_usd(source, model, usage)
        async with get_background_session() as session:
            service = ApiKeysService(ApiKeysRepository(session))
            await service.finalize_usage_reservation(
                reservation.reservation_id,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                service_tier=None,
                cost_microdollars=int(cost_usd * 1_000_000) if cost_usd is not None else None,
            )
        return True
    except Exception:
        logger.warning(
            "failed to settle source reservation reservation_id=%s model=%s",
            reservation.reservation_id,
            model,
            exc_info=True,
        )
        try:
            await _release_reservation(reservation)
        except Exception:
            logger.warning(
                "failed to release source reservation after settlement failure reservation_id=%s",
                reservation.reservation_id,
                exc_info=True,
            )
        return False


def _source_usage_cost_usd(source: ModelSource, model: str, usage: SourceUsage | None) -> float | None:
    if usage is None:
        return None
    cost_usd = source_model_cost_usd(
        source,
        model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
    )
    return 0.0 if cost_usd is None else cost_usd


async def _log_source_chat_completion(
    request: Request,
    *,
    source: ModelSource,
    api_key: ApiKeyData | None,
    model: str,
    status: str,
    usage: SourceUsage | None = None,
    timings: SourceTimings | None = None,
    cost_usd_override: float | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    upstream_status_code: int | None = None,
) -> None:
    conversation_id = _request_log_client_fields(request.headers)[2]
    try:
        async with get_background_session() as session:
            await RequestLogsRepository(session).add_log(
                account_id=None,
                model_source_id=source.id,
                model_source_kind=source.kind,
                api_key_id=api_key.id if api_key is not None else None,
                request_id=ensure_request_id(),
                model=model,
                input_tokens=usage.input_tokens if usage is not None else None,
                output_tokens=usage.output_tokens if usage is not None else None,
                cached_input_tokens=usage.cached_input_tokens if usage is not None else None,
                cost_usd=(
                    cost_usd_override if cost_usd_override is not None else _source_usage_cost_usd(source, model, usage)
                ),
                latency_ms=timings.latency_ms if timings is not None else None,
                latency_first_token_ms=(timings.latency_first_token_ms if timings is not None else None),
                status=status,
                error_code=error_code,
                error_message=error_message,
                upstream_status_code=upstream_status_code,
                transport="http",
                upstream_transport="openai_compatible_http",
                source="model_source",
                useragent=request.headers.get("user-agent"),
                conversation_id=conversation_id,
                client_ip=resolve_request_client_host(request),
            )
    except Exception:
        logger.warning(
            "failed to write source request log source_id=%s model=%s status=%s",
            source.id,
            model,
            status,
            exc_info=True,
        )


async def _aclose_stream(stream: AsyncIterator[object]) -> None:
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        await aclose()


def _reservation_requires_usage(reservation: ApiKeyUsageReservationData | None) -> bool:
    return bool(reservation and reservation.has_applicable_limits)


def _source_usage_settlement_failed_error() -> OpenAIErrorEnvelope:
    return openai_error(
        "usage_settlement_failed",
        "OpenAI-compatible model source usage could not be settled",
        error_type="server_error",
    )


def _source_error_code(payload: Mapping[str, JsonValue]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def _source_error_message(payload: Mapping[str, JsonValue]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None
    message = error.get("message")
    return message if isinstance(message, str) else None


def _effective_model_for_api_key(api_key: ApiKeyData | None, requested_model: str) -> str:
    if api_key is None or api_key.enforced_model is None:
        return requested_model
    return api_key.enforced_model


def _effective_optional_model_for_api_key(api_key: ApiKeyData | None, requested_model: str | None) -> str | None:
    return effective_model_for_api_key(api_key, requested_model)


def _compact_request_service_tier(payload: ResponsesCompactRequest) -> str | None:
    value = payload.service_tier
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _capture_response_metadata_turn_state(
    payload: Mapping[str, JsonValue],
    captured_turn_state_headers: dict[str, str],
) -> None:
    """Keep the first real upstream turn-state metadata header for the HTTP response."""
    if captured_turn_state_headers or payload.get("type") != "response.metadata":
        return
    headers = payload.get("headers")
    if not is_json_mapping(headers):
        return
    for name, value in headers.items():
        if str(name).lower() != "x-codex-turn-state" or not isinstance(value, str):
            continue
        turn_state = value.strip()
        if turn_state:
            captured_turn_state_headers["x-codex-turn-state"] = turn_state
        return


async def _collect_responses_payload(
    stream: AsyncIterator[str],
    *,
    captured_turn_state_headers: dict[str, str] | None = None,
) -> OpenAIResponseResult:
    output_items: dict[int, dict[str, JsonValue]] = {}
    terminal_result: OpenAIResponseResult | None = None
    contract_violation_kind: str | None = None
    async for line in stream:
        payload = _parse_sse_payload(line)
        if not payload:
            if _looks_like_sse_data_block(line):
                contract_violation_kind = contract_violation_kind or "invalid_json"
            continue
        if captured_turn_state_headers is not None:
            _capture_response_metadata_turn_state(payload, captured_turn_state_headers)
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
    stream = _normalize_reasoning_summary_stream(stream)
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
    next_sequence_number = 0
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

    def formatted_payloads_with_synthetic_deltas(
        payload: dict[str, JsonValue], raw_block: str | None = None
    ) -> list[str]:
        synthetic_blocks = [
            format_sse_event(synthetic_payload)
            for synthetic_payload in _synthetic_text_delta_events(payload, seen_text_delta_keys)
        ]
        if raw_block is not None and not synthetic_blocks:
            # Nothing rewrote the payload and nothing synthetic precedes it:
            # pass the upstream block through instead of re-serializing.
            return [raw_block]
        return [*synthetic_blocks, format_sse_event(payload)]

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

    def normalize_public_failure_sequence(
        payload: dict[str, JsonValue],
        *,
        reserve_created_sequence: bool,
    ) -> tuple[dict[str, JsonValue], int | None]:
        nonlocal next_sequence_number
        sequence_number = payload.get("sequence_number")
        if isinstance(sequence_number, int) and not isinstance(sequence_number, bool):
            next_sequence_number = max(next_sequence_number, sequence_number + 1)
            if enforce_openai_sdk_contract and reserve_created_sequence and payload.get("type") == "response.failed":
                return payload, sequence_number - 1
            return payload, None
        if enforce_openai_sdk_contract and payload.get("type") == "response.failed":
            created_sequence_number: int | None = None
            if reserve_created_sequence:
                created_sequence_number = next_sequence_number
                next_sequence_number += 1
            normalized_payload = dict(payload)
            normalized_payload["sequence_number"] = next_sequence_number
            next_sequence_number += 1
            return normalized_payload, created_sequence_number
        return payload, None

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
        parsed_payload = payload
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
        synthetic_created = None
        if (
            enforce_openai_sdk_contract
            and not created_emitted
            and isinstance(event_type, str)
            and event_type != "response.created"
        ):
            synthetic_created = _synthetic_response_created_envelope(normalized_payload)
        normalized_payload, synthetic_created_sequence = normalize_public_failure_sequence(
            normalized_payload,
            reserve_created_sequence=synthetic_created is not None,
        )
        if synthetic_created is not None and synthetic_created_sequence is not None:
            synthetic_created["sequence_number"] = synthetic_created_sequence
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

            if synthetic_created is not None:
                yield format_sse_event(synthetic_created)
                created_emitted = True
                response_id = _response_id_from_event_payload(synthetic_created)
                for formatted_payload in buffered_pre_created_payloads_to_replay(response_id):
                    yield formatted_payload
            elif _should_buffer_public_pre_created_event(event_type):
                if len(pre_created_buffer) >= _PUBLIC_RESPONSES_PRE_CREATED_BUFFER_LIMIT:
                    error_kind = contract_violation_kind or "upstream_stream_truncated"
                    for formatted_payload in _public_response_failed_event_blocks(
                        error_kind,
                        include_created=True,
                        sequence_number=next_sequence_number,
                    ):
                        yield formatted_payload
                    return
                pre_created_buffer.append(normalized_payload)
                continue
            elif event_type in _PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES:
                if event_type == "error":
                    for formatted_payload in _public_response_failed_event_blocks_from_error(
                        normalized_payload,
                        include_created=True,
                        sequence_number=next_sequence_number,
                    ):
                        yield formatted_payload
                    return
                error_kind = contract_violation_kind or "upstream_stream_truncated"
                for formatted_payload in _public_response_failed_event_blocks(
                    error_kind,
                    include_created=True,
                    sequence_number=next_sequence_number,
                ):
                    yield formatted_payload
                return

        if enforce_openai_sdk_contract and event_type == "error":
            for formatted_payload in _public_response_failed_event_blocks_from_error(
                normalized_payload,
                include_created=not created_emitted,
                sequence_number=next_sequence_number,
            ):
                yield formatted_payload
            return

        _collect_output_item_event(normalized_payload, output_items)
        if event_type == "response.output_text.delta":
            seen_text_delta_keys.add(_text_delta_stream_key(normalized_payload))
        # Both the backfill branch and _normalize_public_stream_payload copy
        # the dict when they change anything, so identity with the parsed
        # payload proves the event is unmutated. Pass-through additionally
        # requires the block to already carry the canonical `event: <type>`
        # framing that format_sse_event would add: bridge rewrite paths can
        # enqueue data-only blocks, and named-event (EventSource) clients
        # would otherwise lose the event name re-serialization used to add.
        unmutated_block = (
            event_block
            if normalized_payload is parsed_payload and _has_canonical_event_framing(event_block, event_type)
            else None
        )
        for formatted_payload in formatted_payloads_with_synthetic_deltas(normalized_payload, unmutated_block):
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
    for formatted_payload in _public_response_failed_event_blocks(
        error_kind,
        include_created=include_created,
        sequence_number=next_sequence_number if enforce_openai_sdk_contract else None,
    ):
        yield formatted_payload


def _should_buffer_public_pre_created_event(event_type: str) -> bool:
    return (
        event_type.startswith("response.")
        and event_type != "response.created"
        and event_type not in _PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES
    )


def _public_response_failed_event_blocks(
    error_kind: str,
    *,
    include_created: bool,
    sequence_number: int | None,
) -> list[str]:
    failed_payload = cast(
        dict[str, JsonValue],
        response_failed_event(
            error_kind,
            _public_contract_error_message(error_kind),
            response_id=f"resp_{error_kind}",
        ),
    )
    if sequence_number is not None:
        failed_payload["sequence_number"] = sequence_number + int(include_created)
    blocks: list[str] = []
    if include_created:
        synthetic_created = _synthetic_response_created_envelope(failed_payload)
        if synthetic_created is not None:
            if sequence_number is not None:
                synthetic_created["sequence_number"] = sequence_number
            blocks.append(format_sse_event(synthetic_created))
    blocks.append(format_sse_event(failed_payload))
    return blocks


def _public_response_failed_event_blocks_from_error(
    payload: dict[str, JsonValue],
    *,
    include_created: bool,
    sequence_number: int,
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
    failed_payload["sequence_number"] = sequence_number + int(include_created)
    blocks: list[str] = []
    if include_created:
        synthetic_created = _synthetic_response_created_envelope(failed_payload)
        if synthetic_created is not None:
            synthetic_created["sequence_number"] = sequence_number
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


def _has_canonical_event_framing(event_block: str, event_type: JsonValue) -> bool:
    """True when the block already carries the `event: <type>` line that
    format_sse_event would emit (or the payload has no type, where canonical
    framing is data-only)."""
    if not isinstance(event_type, str) or not event_type:
        return event_block.startswith("data: ")
    return event_block.startswith(f"event: {event_type}\n")


def _normalize_public_stream_payload(
    payload: dict[str, JsonValue],
    *,
    enforce_openai_sdk_contract: bool = True,
) -> tuple[dict[str, JsonValue] | None, str | None]:
    event_type = payload.get("type")
    if event_type == "response.reasoning_summary_text.done" and isinstance(payload.get("text"), str):
        normalized_payload = dict(payload)
        normalized_payload["text"] = _strip_blank_html_comment_lines(cast(str, payload["text"]))
        return normalized_payload, None
    if event_type in {"response.reasoning_summary_part.added", "response.reasoning_summary_part.done"}:
        part = payload.get("part")
        if is_json_mapping(part) and part.get("type") == "summary_text" and isinstance(part.get("text"), str):
            normalized_part = dict(part)
            normalized_part["text"] = _strip_blank_html_comment_lines(cast(str, part["text"]))
            normalized_payload = dict(payload)
            normalized_payload["part"] = normalized_part
            return normalized_payload, None
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
    if item_type == "reasoning":
        return _normalize_reasoning_output_item(item)
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


def _normalize_reasoning_output_item(item: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Remove renderer-only blank HTML comments from reasoning summaries.

    Recent Codex reasoning summaries can include a standalone ``<!-- -->``
    markdown placeholder after the visible summary heading. The Codex TUI renders
    reasoning summary text directly, so proxying that inert marker verbatim makes
    it visible between tool calls. Limit the cleanup to reasoning summary text so
    assistant/user-visible content and non-empty HTML comments remain untouched.
    """

    normalized = dict(item)
    summary = item.get("summary")
    if not isinstance(summary, list):
        return normalized

    normalized_summary: list[JsonValue] = []
    changed = False
    for part in summary:
        if not is_json_mapping(part):
            normalized_summary.append(part)
            continue
        text = part.get("text")
        if part.get("type") != "summary_text" or not isinstance(text, str):
            normalized_summary.append(dict(part))
            continue
        cleaned = _strip_blank_html_comment_lines(text)
        normalized_part = dict(part)
        normalized_part["text"] = cleaned
        normalized_summary.append(normalized_part)
        changed = changed or cleaned != text

    if changed:
        normalized["summary"] = normalized_summary
    return normalized


async def _normalize_reasoning_summary_stream(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    pending: dict[tuple[str | None, int | None, int | None], list[tuple[dict[str, JsonValue], str]]] = {}

    def flush(key: tuple[str | None, int | None, int | None]) -> list[str]:
        entries = pending.pop(key, [])
        text = "".join(cast(str, payload.get("delta")) for payload, _ in entries)
        cleaned = _strip_blank_html_comment_lines(text)
        if cleaned == text:
            # Nothing changed: replay the original upstream blocks instead of
            # re-serializing every buffered payload.
            return [raw_block for _, raw_block in entries]
        if not cleaned or not entries:
            return []
        normalized = dict(entries[0][0])
        normalized["delta"] = cleaned
        return [format_sse_event(normalized)]

    async for event_block in stream:
        payload = _parse_sse_payload(event_block)
        if payload is None:
            yield event_block
            continue
        event_type = payload.get("type")
        event_key = _reasoning_summary_delta_key(payload)
        if (
            pending
            and not _is_reasoning_summary_interleavable_event(event_type)
            and not (
                event_type in _REASONING_SUMMARY_DELTA_TYPES | _REASONING_SUMMARY_DONE_TYPES and event_key in pending
            )
        ):
            for pending_key in tuple(pending):
                for buffered in flush(pending_key):
                    yield buffered
        if event_type in _REASONING_SUMMARY_DELTA_TYPES:
            delta = payload.get("delta")
            if not isinstance(delta, str):
                yield event_block
                continue
            key = event_key
            if key in pending:
                pending[key].append((payload, event_block))
                buffered_text = "".join(cast(str, item.get("delta")) for item, _ in pending[key])
                if _strip_blank_html_comment_lines(buffered_text) != buffered_text:
                    for buffered in flush(key):
                        yield buffered
                    continue
                if _could_be_blank_html_comment_line(buffered_text):
                    continue
                for buffered in flush(key):
                    yield buffered
                continue
            cleaned_delta = _strip_blank_html_comment_lines(delta)
            if cleaned_delta != delta:
                normalized_payload = dict(payload)
                normalized_payload["delta"] = cleaned_delta
                yield format_sse_event(normalized_payload)
                continue
            if _could_be_blank_html_comment_line(delta):
                pending[key] = [(payload, event_block)]
                continue
            yield event_block
            continue
        if event_type in _REASONING_SUMMARY_DONE_TYPES:
            key = event_key
            for buffered in flush(key):
                yield buffered
        elif event_type in _PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES:
            for key in tuple(pending):
                for buffered in flush(key):
                    yield buffered
        yield event_block

    for key in tuple(pending):
        for buffered in flush(key):
            yield buffered


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
        (
            f"Unsupported transcription model '{model}'. Use '{_TRANSCRIPTION_MODEL}' for the subscription-backed "
            "transcription route, or configure an enabled OpenAI-compatible model source with Audio Transcriptions "
            "support for this model."
        ),
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


def _http_bridge_recovery_request_eligible(
    payload: ResponsesRequest,
    *,
    bridge_active: bool,
    headers: Mapping[str, str] | None = None,
) -> bool:
    turn_state_anchor = proxy_affinity_module._sticky_key_from_turn_state_header(headers or {})
    if not bridge_active or (payload.previous_response_id is None and turn_state_anchor is None):
        return False
    settings = proxy_service_module.get_settings()
    if not getattr(settings, "http_responses_session_bridge_operation_ledger_enabled", True):
        return False
    # Turn-state-only requests are admitted to the recovery-capable stream so
    # the submit path can first prove a durable predecessor by advancing its
    # operation anchor. The streaming layer marks an exception recovery-safe
    # only after that proof; fresh first turns remain fail-closed there.
    if proxy_service_module._responses_request_contains_input_image(
        payload
    ) or proxy_service_module._responses_request_uses_image_generation(payload):
        return False
    payload_bytes = len(json.dumps(payload.to_payload(), ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    return payload_bytes <= proxy_service_module._ws_transport_payload_budget_bytes(settings)


def _mask_previous_response_not_found_error(
    envelope: OpenAIErrorEnvelopeModel,
    *,
    default_status: int | None = None,
    allow_client_full_history_once: bool = False,
) -> tuple[int, OpenAIErrorEnvelopeModel]:
    if not _is_previous_response_not_found_public_error(envelope.error):
        return default_status if default_status is not None else _status_for_error(envelope.error), envelope
    # In recovery-first mode, preserve the upstream-shaped 400 so Codex can
    # drop the ambiguous previous_response_id anchor and resend full local
    # history. This is intentionally opt-in because the resend is at-least-once
    # and may duplicate an upstream response that was accepted but not observed.
    if (
        allow_client_full_history_once
        and get_settings().http_responses_session_bridge_ambiguous_continuation_recovery_mode
        == "client_full_history_once"
    ):
        return default_status if default_status is not None else 400, envelope
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
